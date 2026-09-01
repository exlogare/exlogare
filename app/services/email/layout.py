"""Transactional email HTML wrapper (table layout, inline styles, hosted logo)."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from app.core.config import Settings, get_settings

_COLOR_BG = "#f4f6f9"
_COLOR_CARD = "#ffffff"
_COLOR_BORDER = "#e8ecf2"
_COLOR_TEXT = "#1a1f2c"
_COLOR_MUTED = "#64748b"
_COLOR_ACCENT = "#4f46e5"
_COLOR_DANGER = "#dc2626"


def brand_logo_url(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    base = s.email_brand_base_url.rstrip("/")
    return f"{base}/logo.png"


def wrap_email_html(
    *,
    inner_html: str,
    title: str,
    preheader: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Wrap inner HTML (trusted markup from our app) in a branded shell."""
    s = settings or get_settings()
    logo = html.escape(brand_logo_url(s), quote=True)
    raw_site = s.email_brand_base_url.rstrip("/")
    site_href = html.escape(raw_site, quote=True)
    site_label = html.escape(
        raw_site.replace("https://", "").replace("http://", ""), quote=True
    )
    company = html.escape(s.company_name, quote=True)
    safe_title = html.escape(title, quote=True)
    year = datetime.now(timezone.utc).year

    preview = (
        html.escape(preheader, quote=True)
        if preheader
        else html.escape(title.replace("\n", " "), quote=True)
    )

    preview_pad = "&nbsp;" * 120

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="x-ua-compatible" content="ie=edge">
<title>{safe_title}</title>
</head>
<body style="margin:0;padding:0;background-color:{_COLOR_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">
{preview}{preview_pad}
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{_COLOR_BG};">
<tr>
<td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;margin:0 auto;">
<tr>
<td align="center" style="padding-bottom:24px;">
<a href="{site_href}" style="text-decoration:none;display:inline-block;">
<img src="{logo}" width="120" height="120" alt="{company}" style="display:block;border:0;width:120px;height:auto;max-width:120px;">
</a>
</td>
</tr>
<tr>
<td style="background-color:{_COLOR_CARD};border-radius:16px;border:1px solid {_COLOR_BORDER};overflow:hidden;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td style="padding:28px 28px 8px 28px;font-size:16px;line-height:24px;color:{_COLOR_TEXT};">
{inner_html}
</td>
</tr>
<tr>
<td style="padding:8px 28px 28px 28px;font-size:13px;line-height:20px;color:{_COLOR_MUTED};border-top:1px solid {_COLOR_BORDER};">
© {year} {company} · <a href="{site_href}" style="color:{_COLOR_MUTED};text-decoration:underline;">{site_label}</a>
</td>
</tr>
</table>
</td>
</tr>
<tr>
<td align="center" style="padding-top:20px;font-size:12px;line-height:18px;color:{_COLOR_MUTED};">
Automated message — please do not reply unless an address is shown.
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>
"""


def email_button(*, href: str, label: str) -> str:
    """Primary CTA button (bulletproof-ish: padding on <a> for clients without border-radius)."""
    safe_href = html.escape(href, quote=True)
    safe_label = html.escape(label, quote=True)
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
<tr>
<td align="center" bgcolor="{_COLOR_ACCENT}" style="border-radius:12px;">
<a href="{safe_href}" target="_blank" rel="noopener noreferrer"
style="display:inline-block;padding:14px 28px;font-size:16px;font-weight:600;line-height:20px;color:#ffffff;text-decoration:none;border-radius:12px;background-color:{_COLOR_ACCENT};">
{safe_label}
</a>
</td>
</tr>
</table>
<p style="margin:16px 0 0;font-size:13px;line-height:20px;color:{_COLOR_MUTED};word-break:break-all;">
If the button does not work, copy and paste this link:<br>
<a href="{safe_href}" style="color:{_COLOR_ACCENT};">{safe_href}</a>
</p>"""


def email_paragraph(text: str, *, muted: bool = False) -> str:
    color = _COLOR_MUTED if muted else _COLOR_TEXT
    return (
        f'<p style="margin:0 0 16px;font-size:16px;line-height:24px;color:{color};">'
        f"{html.escape(text)}"
        f"</p>"
    )


def email_notice(*, title: str, body: str, variant: str = "neutral") -> str:
    """Small callout: variant neutral | danger | success."""
    if variant == "danger":
        bg, border, title_c = "#fef2f2", "#fecaca", _COLOR_DANGER
    elif variant == "success":
        bg, border, title_c = "#f0fdf4", "#bbf7d0", "#15803d"
    else:
        bg, border, title_c = "#f8fafc", _COLOR_BORDER, _COLOR_TEXT
    body_html = html.escape(body).replace("\n", "<br>\n")
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 16px;background-color:{bg};border:1px solid {border};border-radius:12px;">
<tr>
<td style="padding:14px 16px;font-size:14px;line-height:21px;color:{_COLOR_TEXT};">
<strong style="display:block;margin-bottom:6px;color:{title_c};">{html.escape(title)}</strong>
{body_html}
</td>
</tr>
</table>"""


__all__ = [
    "brand_logo_url",
    "wrap_email_html",
    "email_button",
    "email_paragraph",
    "email_notice",
]
