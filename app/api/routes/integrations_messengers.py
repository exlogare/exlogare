from __future__ import annotations

import asyncio
import json
import secrets
import socket
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_admin, require_non_viewer
from app.core.logging import get_logger
from app.core.outbound_http import slack_client, telegram_client
from app.core.redis import get_redis
from app.models.notification_connection import NotificationChannel, NotificationConnection
from app.services.audit import record_audit


def _assert_notifications_allowed(principal: CurrentPrincipal) -> None:
    return

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
log = get_logger(__name__)


class TelegramInitRequest(BaseModel):
    bot_token: str = Field(min_length=20)


class TelegramInitResponse(BaseModel):
    connection_id: str
    bot_username: str
    link_code: str
    webhook_url: str
    webhook_registered: bool
    instructions: list[str]


class SlackWebhookInitRequest(BaseModel):
    webhook_url: str
    channel: str | None = None


class MatrixInitRequest(BaseModel):
    homeserver_url: str
    access_token: str
    room_id: str


class NotificationConnectionOut(BaseModel):
    id: str
    channel: str
    enabled: bool
    target: str | None
    endpoint: str | None
    status: str
    webhook_registered: bool | None = None
    link_code: str | None = None
    bot_username: str | None = None


class RetryWebhookResponse(BaseModel):
    ok: bool
    webhook_registered: bool
    detail: str | None = None


class TelegramWebhookInfoResponse(BaseModel):
    """Mirror of Telegram's getWebhookInfo payload, plus the raw result for debug."""

    ok: bool
    url: str | None = None
    has_custom_certificate: bool | None = None
    pending_update_count: int | None = None
    ip_address: str | None = None
    last_error_date: int | None = None
    last_error_message: str | None = None
    last_synchronization_error_date: int | None = None
    max_connections: int | None = None
    allowed_updates: list[str] | None = None
    raw: dict | None = None
    detail: str | None = None


class TestDeliveryResponse(BaseModel):
    ok: bool
    detail: str | None = None


@router.get("/notifications", response_model=list[NotificationConnectionOut])
async def list_notification_connections(
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[NotificationConnectionOut]:
    rows = (
        await session.execute(
            select(NotificationConnection).where(
                NotificationConnection.tenant_id == principal.tenant.id
            )
        )
    ).scalars().all()
    out: list[NotificationConnectionOut] = []
    for c in rows:
        cfg = c.config or {}
        out.append(
            NotificationConnectionOut(
                id=str(c.id),
                channel=c.channel.value,
                enabled=c.enabled,
                target=c.target,
                endpoint=c.endpoint,
                status=cfg.get("status", "pending" if not c.target else "active"),
                webhook_registered=cfg.get("webhook_registered")
                if c.channel == NotificationChannel.TELEGRAM
                else None,
                link_code=cfg.get("link_code")
                if c.channel == NotificationChannel.TELEGRAM and not c.target
                else None,
                bot_username=cfg.get("bot_username")
                if c.channel == NotificationChannel.TELEGRAM
                else None,
            )
        )
    return out


@router.delete("/notifications/{connection_id}")
async def delete_notification_connection(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await session.execute(
            select(NotificationConnection).where(
                NotificationConnection.id == connection_id,
                NotificationConnection.tenant_id == principal.tenant.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    await session.delete(row)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="notification_connection_deleted",
        actor=principal.user.email,
        target=str(connection_id),
    )
    await session.commit()
    return {"status": "deleted"}


@router.post("/telegram/init", response_model=TelegramInitResponse)
async def telegram_init(
    body: TelegramInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TelegramInitResponse:
    _assert_notifications_allowed(principal)
    bot_info = await _telegram_get_me(body.bot_token)
    if not bot_info or not bot_info.get("username"):
        raise HTTPException(status_code=400, detail="Invalid Telegram bot token")

    webhook_url = _telegram_webhook_url()
    webhook_registered = await _telegram_set_webhook(body.bot_token, webhook_url)

    link_code = f"{secrets.randbelow(1000000):06d}"

    conn = NotificationConnection(
        tenant_id=principal.tenant.id,
        channel=NotificationChannel.TELEGRAM,
        enabled=True,
        token_enc=encrypt_str(body.bot_token),
        target=None,
        endpoint=webhook_url,
        config={
            "bot_username": bot_info.get("username"),
            "link_code": link_code,
            "status": "pending_link",
            "webhook_registered": webhook_registered,
        },
    )
    session.add(conn)
    await session.flush()

    redis_client = get_redis()
    await redis_client.setex(
        f"tg:link_code:{link_code}",
        60 * 30,
        json.dumps({
            "connection_id": str(conn.id),
            "tenant_id": str(principal.tenant.id),
        }),
    )
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="telegram_bot_connected",
        actor=principal.user.email,
        target=f"bot:{bot_info.get('username')}",
        meta={"webhook_registered": webhook_registered},
    )
    await session.commit()
    return TelegramInitResponse(
        connection_id=str(conn.id),
        bot_username=bot_info.get("username"),
        link_code=link_code,
        webhook_url=webhook_url,
        webhook_registered=webhook_registered,
        instructions=[
            f"Start a chat with @{bot_info.get('username')} or add the bot to a group",
            f"Send this command in the chat: /link {link_code}",
            "Once received, your chat will be linked automatically.",
        ],
    )


@router.post(
    "/telegram/{connection_id}/retry-webhook",
    response_model=RetryWebhookResponse,
)
async def telegram_retry_webhook(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> RetryWebhookResponse:
    """Re-attempt setWebhook for an existing Telegram connection."""
    row = (
        await session.execute(
            select(NotificationConnection).where(
                NotificationConnection.id == connection_id,
                NotificationConnection.tenant_id == principal.tenant.id,
                NotificationConnection.channel == NotificationChannel.TELEGRAM,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found")
    if not row.token_enc:
        raise HTTPException(status_code=400, detail="Stored bot token missing")

    token = decrypt_str(row.token_enc)
    if not token:
        raise HTTPException(status_code=400, detail="Unable to decrypt bot token")

    webhook_url = _telegram_webhook_url()
    ok = await _telegram_set_webhook(token, webhook_url)

    cfg = dict(row.config or {})
    cfg["webhook_registered"] = ok
    row.config = cfg
    row.endpoint = webhook_url
    await session.commit()

    return RetryWebhookResponse(
        ok=ok,
        webhook_registered=ok,
        detail=None
        if ok
        else "Telegram rejected setWebhook (see server logs). Retry in a minute.",
    )


@router.get(
    "/telegram/{connection_id}/webhook-info",
    response_model=TelegramWebhookInfoResponse,
)
async def telegram_webhook_info(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TelegramWebhookInfoResponse:
    """Call Telegram ``getWebhookInfo`` for this bot and return the result."""
    row = (
        await session.execute(
            select(NotificationConnection).where(
                NotificationConnection.id == connection_id,
                NotificationConnection.tenant_id == principal.tenant.id,
                NotificationConnection.channel == NotificationChannel.TELEGRAM,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found")
    if not row.token_enc:
        raise HTTPException(status_code=400, detail="Stored bot token missing")
    token = decrypt_str(row.token_enc)
    if not token:
        raise HTTPException(status_code=400, detail="Unable to decrypt bot token")

    try:
        async with telegram_client(timeout=8) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getWebhookInfo"
            )
    except Exception as exc:
        log.warning("telegram.get_webhook_info_error", error=str(exc))
        return TelegramWebhookInfoResponse(ok=False, detail=str(exc)[:200])
    if resp.status_code >= 400:
        log.warning(
            "telegram.get_webhook_info_failed",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return TelegramWebhookInfoResponse(
            ok=False, detail=f"HTTP {resp.status_code}: {resp.text[:160]}"
        )
    data = resp.json()
    if not data.get("ok"):
        return TelegramWebhookInfoResponse(ok=False, detail=str(data)[:200], raw=data)
    result = data.get("result") or {}
    log.info("telegram.get_webhook_info", result=result)
    return TelegramWebhookInfoResponse(
        ok=True,
        url=result.get("url"),
        has_custom_certificate=result.get("has_custom_certificate"),
        pending_update_count=result.get("pending_update_count"),
        ip_address=result.get("ip_address"),
        last_error_date=result.get("last_error_date"),
        last_error_message=result.get("last_error_message"),
        last_synchronization_error_date=result.get("last_synchronization_error_date"),
        max_connections=result.get("max_connections"),
        allowed_updates=result.get("allowed_updates"),
        raw=result,
    )


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Telegram bot webhook endpoint. Handles /link <code> messages."""
    try:
        payload = await request.json()
    except Exception as exc:
        log.warning(
            "telegram.webhook_bad_body",
            error=str(exc),
            client=request.client.host if request.client else None,
            ua=request.headers.get("user-agent"),
        )
        return {"status": "bad_body"}
    message = payload.get("message") or payload.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    log.info(
        "telegram.webhook_incoming",
        client=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
        update_id=payload.get("update_id"),
        has_message=bool(message),
        chat_id=chat_id,
        text_preview=text[:50],
    )

    if not text.startswith("/link") or chat_id is None:
        return {"status": "ignored"}
    parts = text.split()
    if len(parts) < 2:
        return {"status": "ignored"}
    code = parts[1].strip()

    redis_client = get_redis()
    key = f"tg:link_code:{code}"
    raw = await redis_client.get(key)
    if raw is None:
        return {"status": "invalid_code"}
    data = json.loads(raw)
    await redis_client.delete(key)
    connection_id = uuid.UUID(data["connection_id"])

    row = (
        await session.execute(
            select(NotificationConnection).where(NotificationConnection.id == connection_id)
        )
    ).scalars().first()
    if row is None:
        return {"status": "missing_connection"}

    row.target = str(chat_id)
    row.config = {**(row.config or {}), "status": "active", "chat_title": chat.get("title")}

    await record_audit(
        session,
        tenant_id=row.tenant_id,
        action="telegram_chat_linked",
        target=str(chat_id),
        meta={"chat_title": chat.get("title")},
    )
    await session.commit()

    # Send a confirmation message
    try:
        token = decrypt_str(row.token_enc)
        if token:
            async with telegram_client(timeout=5) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": "Exlogare bot linked. You'll receive CI failure RCAs here.",
                    },
                )
    except Exception:
        pass
    return {"status": "linked"}


def _telegram_webhook_url() -> str:
    """Build the webhook URL we register with Telegram's setWebhook."""
    s = get_settings()
    base = (s.telegram_webhook_base_url or s.public_base_url or "").rstrip("/")
    return f"{base}/api/integrations/telegram/webhook"


async def _telegram_get_me(token: str) -> dict | None:
    try:
        async with telegram_client(timeout=8) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        if resp.status_code >= 400:
            return None
        data = resp.json()
        if not data.get("ok"):
            return None
        return data.get("result")
    except Exception:
        return None


_TELEGRAM_TRANSIENT_WEBHOOK_ERRORS = (
    "failed to resolve host",
    "temporary failure in name resolution",
    "getaddrinfo",
)


async def _resolve_webhook_ip(host: str) -> str | None:
    """Resolve ``host`` to an IPv4 address. Returns None on failure."""
    if not host:
        return None
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, None, socket.AF_INET, socket.SOCK_STREAM
        )
    except Exception as exc:
        log.warning("telegram.webhook_ip_resolve_failed", host=host, error=str(exc))
        return None
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str):
            return sockaddr[0]
    return None


async def _telegram_set_webhook(
    token: str, url: str, *, max_attempts: int = 5
) -> bool:
    """Register our webhook URL with Telegram."""
    parsed = urlparse(url)
    webhook_host = parsed.hostname or ""
    settings = get_settings()
    override_ip = (settings.telegram_webhook_ip or "").strip()
    resolved_ip = override_ip or await _resolve_webhook_ip(webhook_host)

    form: dict[str, str] = {
        "url": url,
        "allowed_updates": json.dumps(["message", "edited_message"]),
        "drop_pending_updates": "true",
    }
    if resolved_ip:
        form["ip_address"] = resolved_ip

    log.info(
        "telegram.set_webhook_request",
        url=url,
        webhook_host=webhook_host,
        resolved_ip=resolved_ip,
        ip_source=(
            "env_override"
            if override_ip
            else ("auto_dns" if resolved_ip else "none")
        ),
    )

    delay = 1.0
    last_body = ""
    last_status = 0
    for attempt in range(1, max_attempts + 1):
        try:
            async with telegram_client(timeout=8) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/setWebhook",
                    data=form,
                )
            last_status = resp.status_code
            last_body = resp.text[:200]
            if resp.status_code < 400:
                data = resp.json()
                ok = bool(data.get("ok"))
                if not ok:
                    log.warning("telegram.set_webhook_rejected", body=str(data)[:200])
                return ok
            body_lower = resp.text.lower()
            transient = any(
                marker in body_lower for marker in _TELEGRAM_TRANSIENT_WEBHOOK_ERRORS
            )
            if not transient:
                log.warning(
                    "telegram.set_webhook_failed",
                    status=resp.status_code,
                    body=last_body,
                )
                return False
            log.info(
                "telegram.set_webhook_transient_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                status=resp.status_code,
                body=last_body,
            )
        except Exception as exc:
            last_body = str(exc)[:200]
            log.info(
                "telegram.set_webhook_exception_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                error=last_body,
            )
        if attempt < max_attempts:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
    log.warning(
        "telegram.set_webhook_failed",
        status=last_status,
        body=last_body,
        attempts=max_attempts,
    )
    return False


@router.post("/slack/webhook-init", response_model=NotificationConnectionOut)
async def slack_webhook_init(
    body: SlackWebhookInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> NotificationConnectionOut:
    _assert_notifications_allowed(principal)
    parsed = urlparse(body.webhook_url)
    if parsed.scheme != "https" or "hooks.slack.com" not in (parsed.netloc or ""):
        raise HTTPException(status_code=400, detail="URL does not look like a Slack webhook")

    try:
        async with slack_client(timeout=5) as client:
            resp = await client.post(
                body.webhook_url,
                json={"text": "Exlogare webhook connected (test message)"},
            )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=400, detail=f"Slack webhook rejected test message ({resp.status_code})"
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Slack webhook unreachable: {exc}") from exc

    conn = NotificationConnection(
        tenant_id=principal.tenant.id,
        channel=NotificationChannel.SLACK,
        enabled=True,
        endpoint=body.webhook_url,
        target=body.channel,
        config={"status": "active", "mode": "incoming_webhook"},
    )
    session.add(conn)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="slack_webhook_connected",
        actor=principal.user.email,
    )
    await session.commit()
    return NotificationConnectionOut(
        id=str(conn.id),
        channel=conn.channel.value,
        enabled=conn.enabled,
        target=conn.target,
        endpoint=conn.endpoint,
        status="active",
    )


@router.post("/matrix/init", response_model=NotificationConnectionOut)
async def matrix_init(
    body: MatrixInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> NotificationConnectionOut:
    _assert_notifications_allowed(principal)
    base = body.homeserver_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{base}/_matrix/client/v3/rooms/{body.room_id}/state",
                headers={"Authorization": f"Bearer {body.access_token}"},
            )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=400,
                detail=f"Matrix verification failed ({resp.status_code})",
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Matrix homeserver unreachable: {exc}") from exc

    conn = NotificationConnection(
        tenant_id=principal.tenant.id,
        channel=NotificationChannel.MATRIX,
        enabled=True,
        token_enc=encrypt_str(body.access_token),
        target=body.room_id,
        endpoint=base,
        config={"status": "active"},
    )
    session.add(conn)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="matrix_connected",
        actor=principal.user.email,
        target=body.room_id,
    )
    await session.commit()
    return NotificationConnectionOut(
        id=str(conn.id),
        channel=conn.channel.value,
        enabled=conn.enabled,
        target=conn.target,
        endpoint=conn.endpoint,
        status="active",
    )


@router.post("/notifications/{connection_id}/test", response_model=TestDeliveryResponse)
async def test_notification(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TestDeliveryResponse:
    row = (
        await session.execute(
            select(NotificationConnection).where(
                NotificationConnection.id == connection_id,
                NotificationConnection.tenant_id == principal.tenant.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    test_text = "Exlogare test notification - your channel is connected."
    try:
        if row.channel == NotificationChannel.TELEGRAM:
            if not row.token_enc:
                return TestDeliveryResponse(ok=False, detail="Bot token missing")
            if not row.target:
                token = decrypt_str(row.token_enc)
                webhook_url = _telegram_webhook_url()
                ok = await _telegram_set_webhook(token, webhook_url)
                cfg = dict(row.config or {})
                cfg["webhook_registered"] = ok
                row.config = cfg
                row.endpoint = webhook_url
                await session.commit()
                code = (row.config or {}).get("link_code")
                detail = (
                    f"Webhook re-registered. Send '/link {code}' to the bot."
                    if ok and code
                    else "Awaiting /link code from Telegram (webhook not registered — retry in a minute)."
                )
                return TestDeliveryResponse(ok=False, detail=detail)
            token = decrypt_str(row.token_enc)
            async with telegram_client(timeout=5) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": row.target, "text": test_text},
                )
            return TestDeliveryResponse(ok=resp.status_code < 400, detail=resp.text[:120])
        if row.channel == NotificationChannel.SLACK and row.endpoint:
            async with slack_client(timeout=5) as client:
                resp = await client.post(row.endpoint, json={"text": test_text})
            return TestDeliveryResponse(ok=resp.status_code < 400, detail=resp.text[:120])
        if row.channel == NotificationChannel.MATRIX and row.endpoint and row.target and row.token_enc:
            token = decrypt_str(row.token_enc)
            txn = str(uuid.uuid4())
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(
                    f"{row.endpoint}/_matrix/client/v3/rooms/{row.target}/send/m.room.message/{txn}",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"msgtype": "m.text", "body": test_text},
                )
            return TestDeliveryResponse(ok=resp.status_code < 400, detail=resp.text[:120])
    except Exception as exc:
        return TestDeliveryResponse(ok=False, detail=str(exc))
    return TestDeliveryResponse(ok=False, detail="Channel not configured")
