<p align="center">
  <a href="https://exlogare.net">
    <img src="https://github.com/exlogare/exlogare-cli/raw/main/assets/logo.png" alt="Exlogare logo" width="160">
  </a>
</p>

<p align="center">
  <a href="https://exlogare.net">exlogare.net</a>
  ·
  <a href="https://exlogare.net/docs">Documentation</a>
  ·
  <a href="./README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://github.com/exlogare/exlogare/actions/workflows/images.yml">
    <img src="https://github.com/exlogare/exlogare/actions/workflows/images.yml/badge.svg" alt="Docker images">
  </a>
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/docker--compose-ready-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/github/v/tag/exlogare/exlogare?label=version" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License">
  <img src="https://img.shields.io/badge/edition-Community-blue" alt="Community Edition">
</p>

# Exlogare Community Edition

Open-source Community Edition of Exlogare — self-hosted CI/CD failure analysis with AI root-cause reports. Deploy with Docker Compose.

## Quick start

```bash
cp .env.example .env
# Fill in secrets — see "Environment variables" below

./scripts/generate_secrets.sh .env.example   # optional: print secrets to stdout

docker compose up -d --build
```

Open `http://localhost:8080` (or your `WEB_PORT`), sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`, then connect GitLab, GitHub, Jenkins, or generic ingest.

Pull pre-built images instead of building locally:

```bash
export IMAGE_TAG=latest          # or latest-dev / 1.0.0 / …
docker compose pull && docker compose up -d
```

## Features

- **CI integrations** — GitLab, GitHub, Bitbucket, GitFlic, Jenkins, generic ingest
- **AI root-cause analysis** — any OpenAI-compatible LLM (OpenAI, Ollama, vLLM, LiteLLM, Azure, DashScope, …)
- **Dashboard** — analyses, recurring failures, project stats
- **Docker Compose** — postgres, redis, api, worker, beat, web

## Docker images

Published to GHCR:

| Image | Role |
|-------|------|
| `ghcr.io/exlogare/exlogare-api` | FastAPI + Celery worker/beat |
| `ghcr.io/exlogare/exlogare-web` | nginx + SPA |

Set `IMAGE_TAG` in `.env`: `latest`, `latest-dev`, `latest-test`, or a pinned release (e.g. `1.0.0`).

## Architecture

```
web (nginx + SPA)  →  api (FastAPI + init_db)
                         ↓
                    postgres, redis
                         ↓
                    worker, beat (Celery)
```

On every API start, `scripts/entrypoint-api.sh` runs `python -m app.core.init_db` (idempotent: `create_all` + bootstrap admin when `users` is empty).

## External Postgres and Redis

By default Compose starts bundled `postgres` and `redis` containers. To use **your own** managed Postgres (RDS, Cloud SQL, …) or Redis (ElastiCache, Memorystore, …):

**1.** Create the database (PostgreSQL **16+** recommended) and empty Redis instance. Exlogare uses **three Redis logical databases** on the same server:

| Variable | Redis DB | Purpose |
|----------|----------|---------|
| `REDIS_URL` | `0` | App cache, rate limits, heartbeats |
| `CELERY_BROKER_URL` | `1` | Celery message broker |
| `CELERY_RESULT_BACKEND` | `2` | Celery task results |

**2.** Add connection URLs to `.env` (URL-encode special characters in passwords):

```env
DATABASE_URL=postgresql+asyncpg://exlogare:secret@db.internal:5432/exlogare
SYNC_DATABASE_URL=postgresql+psycopg2://exlogare:secret@db.internal:5432/exlogare
REDIS_URL=redis://:redis-secret@redis.internal:6379/0
CELERY_BROKER_URL=redis://:redis-secret@redis.internal:6379/1
CELERY_RESULT_BACKEND=redis://:redis-secret@redis.internal:6379/2
```

- `DATABASE_URL` — async driver for FastAPI (`asyncpg`)
- `SYNC_DATABASE_URL` — sync driver for Celery workers (`psycopg2`)

**3.** Start without bundled datastores:

```bash
docker compose -f docker-compose.yml -f docker-compose.external.yml up -d
```

This skips the `postgres` and `redis` services from the default stack. Only `api`, `worker`, `beat`, and `web` start.

**4.** Network access — containers must reach your DB/Redis host. Options:

- Put Exlogare on the same VPC / Docker network as the databases
- Use `host.docker.internal` for services on the Docker host (Linux: add `extra_hosts: ["host.docker.internal:host-gateway"]` to `api`, `worker`, and `beat` in a local override)
- Expose managed services on private IPs reachable from the host running Compose

**TLS** — append query params to Postgres URLs if your provider requires SSL, e.g. `?ssl=require` (check your provider’s docs).

For the default bundled stack, leave the five URL variables unset — Compose builds them from `POSTGRES_*` and the internal `postgres` / `redis` service names.

## HTTPS and PUBLIC_BASE_URL

`PUBLIC_BASE_URL` is the address **other systems** use to reach your Exlogare API (webhooks, OAuth redirects, messenger bots). It must match what you put in `.env` — scheme, hostname, and port if not 443/80. `WEB_BASE_URL` should usually be the same in the default single-host Docker Compose layout.

Exlogare does **not** hard-require HTTPS in code — defaults are `http://localhost:8080`. Whether HTTP is enough depends on **how** you connect CI and **where** GitLab/GitHub runs. Not everyone runs Exlogare on a public VPS; local and home-lab setups are supported with the right mode.

### Deployment scenarios

Pick the row that matches your setup:

| Scenario | Example `PUBLIC_BASE_URL` | Works for | Notes |
|----------|---------------------------|-----------|-------|
| **Local UI only** | `http://localhost:8080` | Dashboard, settings, manual tests | Default. No inbound calls from Git hosts needed. |
| **OAuth + polling** | `http://localhost:8080` | Self-hosted or cloud GitLab/GitHub when Exlogare **pulls** pipeline status | Exlogare calls **out** to your Git host; webhooks are optional. Good for a laptop or home PC. |
| **Same machine / LAN** | `http://192.168.1.10:8080` or `http://exlogare.local:8080` | Webhooks from GitLab on the **same host or LAN** | Avoid `localhost` in webhook URLs — GitLab in Docker often cannot reach the host’s `127.0.0.1`. Use the host LAN IP or an internal DNS name GitLab can resolve. |
| **DDNS + HTTP (home lab)** | `http://exlogare.myddns.net:8080` | Self-hosted GitLab webhooks over the internet | Forward the port on your router. GitLab must reach this URL; Exlogare registers hooks with SSL verification **off** for `http://` URLs. Not suitable for **GitHub.com** (HTTPS required) or **Telegram** bots. |
| **HTTPS tunnel (no VPS)** | `https://xyz.trycloudflare.com` or ngrok URL | Webhooks from cloud GitLab/GitHub without owning a server | Cloudflare Tunnel, ngrok, etc. give a public HTTPS URL to a process on your PC. Often the simplest way to test webhooks locally. |
| **Production server** | `https://exlogare.example.com` | All integrations, messengers, cloud OAuth | Recommended for always-on installs. Valid TLS certificate; port 443. |

### When HTTPS is actually required

- **GitHub.com webhooks** — GitHub sends hooks only to **HTTPS** URLs.
- **Telegram bots** — Telegram accepts webhook URLs over **HTTPS** only.
- **Cloud OAuth apps** — GitLab.com / GitHub.com app settings often expect HTTPS redirect URIs outside pure localhost dev.
- **Self-hosted GitLab** — HTTP webhooks are allowed; Exlogare sets `enable_ssl_verification: false` automatically for `http://` hook URLs. You can also add the webhook manually in GitLab and disable SSL verification there.

### When localhost is not enough

If your Git host is on **another machine** (e.g. `git.company.com` or GitLab.com), it cannot POST to `http://localhost:8080` on your laptop — that address points to the Git server itself, not to you. Options:

1. Use **polling** mode (no inbound URL needed).
2. Expose Exlogare via **LAN IP**, **DDNS + port forward**, or an **HTTPS tunnel**.
3. Push logs from CI with **generic ingest** or `POST /api/analyze` (Exlogare must be reachable from the runner, not necessarily from the public internet).

The dashboard warns when a webhook URL uses `localhost` and your GitLab is remote — follow the manual webhook steps or change `PUBLIC_BASE_URL`.

### Quick `.env` examples

```env
# Laptop: UI + GitLab polling only
PUBLIC_BASE_URL=http://localhost:8080
WEB_BASE_URL=http://localhost:8080

# Home PC: self-hosted GitLab webhooks via DDNS (HTTP)
PUBLIC_BASE_URL=http://exlogare.myddns.net:8080
WEB_BASE_URL=http://exlogare.myddns.net:8080

# VPS / production
PUBLIC_BASE_URL=https://exlogare.example.com
WEB_BASE_URL=https://exlogare.example.com
```

## Development

```bash
pip install -e ".[dev]"
LLM_ENABLED=false JWT_SECRET=dev ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  python -m app.core.init_db
uvicorn app.main:app --reload --port 8000

cd web && npm install && npm run dev
```

---

## Environment variables

Copy `.env.example` to `.env`. Variables below match `app/core/config.py`.  
By default, `docker-compose.yml` builds `DATABASE_URL`, `REDIS_URL`, and Celery URLs from `POSTGRES_*` and internal service names. To use external Postgres/Redis, set the five URL variables in `.env` and start with `docker-compose.external.yml` — see [External Postgres and Redis](#external-postgres-and-redis).

Legend: **required** = must be set before first production start; **bootstrap** = only when the database is empty; **optional** = safe to leave default.

### Images & web

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAGE_TAG` | `latest` | Docker tag for `ghcr.io/exlogare/exlogare-api` and `exlogare-web`. Use `latest-dev`, `1.0.0`, etc. |
| `WEB_PORT` | `8080` | Host port mapped to the web container (nginx). |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | URL of the API as seen by webhooks and OAuth. Required for webhook mode; use LAN/DDNS/tunnel if Git host is remote. See [HTTPS and PUBLIC_BASE_URL](#https-and-public_base_url). |
| `WEB_BASE_URL` | `http://localhost:8080` | SPA origin for CORS and links in emails. Usually the same as `PUBLIC_BASE_URL` in single-host compose. |
| `APP_ENV` | `prod` | `dev`, `test`, `staging`, or `prod`. Controls docs exposure, CORS extras, trusted hosts. |
| `UPDATE_CHECK_ENABLED` | `true` | When `false`, the dashboard skips GitHub release checks (no outbound calls; version badge shows the installed version only). |

### Database (Postgres)

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | `exlogare` | Postgres role. Used by the `postgres` service and injected into API URLs in compose. |
| `POSTGRES_PASSWORD` | — | **required** Postgres password. Generate with `scripts/generate_secrets.sh`. |
| `POSTGRES_DB` | `exlogare` | Database name. |
| `DATABASE_URL` | *(compose)* | Async SQLAlchemy URL (`postgresql+asyncpg://…`). Required when using external Postgres — see [External Postgres and Redis](#external-postgres-and-redis). |
| `SYNC_DATABASE_URL` | *(compose)* | Sync URL for Celery workers (`postgresql+psycopg2://…`). Required with external Postgres. |

### Redis & Celery

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | *(compose)* | General Redis (cache, rate limits, heartbeats). DB index `0`. Set for external Redis. |
| `CELERY_BROKER_URL` | *(compose)* | Celery message broker. DB index `1`. Set for external Redis. |
| `CELERY_RESULT_BACKEND` | *(compose)* | Celery result backend. DB index `2`. Set for external Redis. |

### Secrets & auth

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | — | **required** HMAC secret for session JWT cookies. Long random string. |
| `ENCRYPTION_KEY` | — | **required** Fernet key (base64) for encrypting OAuth tokens and secrets at rest. Generate: `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. |
| `JWT_EXPIRES_MINUTES` | `10080` | JWT/session lifetime (7 days). |
| `LOGIN_RATE_LIMIT_PER_HOUR` | `30` | Max login attempts per email per hour (brute-force protection). |

### Bootstrap admin

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_EMAIL` | — | **bootstrap** Admin email. Required on first start when `users` table is empty; ignored afterwards. |
| `ADMIN_PASSWORD` | — | **bootstrap** Admin password (min 8 chars). Not rotated on restart. |
| `ADMIN_TENANT_NAME` | `Default` | **bootstrap** Name of the initial organization/tenant. |

### Data retention

| Variable | Default | Description |
|----------|---------|-------------|
| `RETENTION_DAYS` | `365` | How long analyses and related history are kept. Celery beat runs daily cleanup. |

### LLM (OpenAI-compatible)

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_ENABLED` | `true` | `false` → `StubAnalyzer` (no real AI; dev/test only). |
| `LLM_BASE_URL` | *(empty)* | OpenAI-compatible base URL. Empty = `https://api.openai.com/v1`. |
| `LLM_API_KEY` | — | API key for the chosen endpoint. For Ollama/local, any non-empty value (e.g. `ollama`). |
| `LLM_MODEL` | `gpt-4o-mini` | Model name on the selected endpoint. |
| `LLM_TEMPERATURE` | `0.1` | Sampling temperature. |
| `LLM_MAX_TOKENS` | `600` | Max tokens in the completion response. |
| `LLM_JSON_MODE` | `true` | Request `response_format=json_object`. Set `false` if the endpoint does not support JSON mode (fallback parsing is used). |
| `LLM_TAIL_LINES` | `500` | Last N log lines sent to the model. |
| `LLM_TOKEN_BUDGET` | `2500` | Approximate token budget for the log excerpt. |
| `LLM_SYSTEM_PROMPT` | — | **required if `LLM_ENABLED=true`** Inline system prompt. Use `\n` for newlines. |
| `LLM_SYSTEM_PROMPT_FILE` | — | Alternative to inline prompt: path to a text file inside the container (e.g. mount `/config/system_prompt.txt`). |
| `LLM_USER_PROMPT_TEMPLATE` | *(built-in)* | Template with `{header}`, `{log_excerpt}`, `{project_path}`. |

**LLM backend examples**

| Backend | `LLM_BASE_URL` | `LLM_MODEL` | `LLM_API_KEY` |
|---------|----------------|-------------|---------------|
| OpenAI | *(empty)* | `gpt-4o-mini` | `sk-…` |
| Ollama | `http://host.docker.internal:11434/v1` | `llama3.1` | `ollama` |
| vLLM | `http://vllm:8000/v1` | `meta-llama/…` | `local` |
| LiteLLM | `http://litellm:4000/v1` | `gpt-4o` | proxy key |
| DashScope | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | dashscope key |

### GitLab integration (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `GITLAB_BASE_URL` | `https://gitlab.com` | GitLab instance URL (self-managed: `https://gitlab.company.com`). |
| `GITLAB_OAUTH_CLIENT_ID` | — | OAuth application ID. |
| `GITLAB_OAUTH_CLIENT_SECRET` | — | OAuth application secret. |
| `GITLAB_OAUTH_REDIRECT_URI` | — | Must match OAuth app settings; default targets port 8080 via nginx. |
| `GITLAB_WEBHOOK_SECRET` | — | Shared secret for verifying GitLab webhook payloads. |

### GitHub integration (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_BASE_URL` | `https://github.com` | GitHub.com or GHES base URL. |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | REST API base (GHES: `https://github.company.com/api/v3`). |
| `GITHUB_OAUTH_CLIENT_ID` | — | OAuth App client ID. |
| `GITHUB_OAUTH_CLIENT_SECRET` | — | OAuth App client secret. |
| `GITHUB_OAUTH_REDIRECT_URI` | — | OAuth callback URL registered in GitHub. |

### Bitbucket integration (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `BITBUCKET_BASE_URL` | `https://bitbucket.org` | Cloud or Data Center base URL. |
| `BITBUCKET_API_BASE_URL` | `https://api.bitbucket.org/2.0` | REST API base. |
| `BITBUCKET_OAUTH_CLIENT_ID` | — | OAuth consumer key. |
| `BITBUCKET_OAUTH_CLIENT_SECRET` | — | OAuth consumer secret. |
| `BITBUCKET_OAUTH_REDIRECT_URI` | — | OAuth callback URL. |
| `BITBUCKET_WEBHOOK_SECRET` | — | Webhook verification secret. |

### GitFlic integration (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `GITFLIC_BASE_URL` | `https://gitflic.ru` | GitFlic or self-hosted instance URL. |
| `GITFLIC_API_BASE_URL` | `https://api.gitflic.ru` | REST API base (self-hosted: `{base}/rest-api`). |
| `GITFLIC_OAUTH_BASE_URL` | `https://oauth.gitflic.ru` | OAuth server base. |
| `GITFLIC_OAUTH_CLIENT_ID` | — | OAuth client ID. |
| `GITFLIC_OAUTH_CLIENT_SECRET` | — | OAuth client secret. |
| `GITFLIC_OAUTH_REDIRECT_URI` | — | OAuth callback URL. |
| `GITFLIC_WEBHOOK_SECRET` | — | Webhook shared secret. |

### Email (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_PROVIDER` | `auto` | `console` (log only), `smtp`, or `auto`. |
| `SMTP_URL` | — | Full SMTP URL (overrides host/port/user if set). |
| `SMTP_HOST` | — | SMTP server hostname. |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_USERNAME` | — | SMTP auth username. |
| `SMTP_PASSWORD` | — | SMTP auth password. |
| `SMTP_FROM` | — | From address for outbound mail. |
| `SMTP_STARTTLS` | `true` | Use STARTTLS. |
| `FROM_EMAIL` | `no-reply@localhost` | Fallback sender address. |
| `CONTACT_EMAIL` | `admin@localhost` | Contact address shown in public forms. |
| `SUPPORT_EMAIL` | `admin@localhost` | Support contact (dashboard mailto link). |

### Notifications — Telegram & Slack (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_PLATFORM_BOT_TOKEN` | — | Bot token for linking Telegram channels. |
| `TELEGRAM_PLATFORM_BOT_USERNAME` | — | Bot `@username` for deep links. |
| `SLACK_PLATFORM_CLIENT_ID` | — | Slack OAuth app client ID. |
| `SLACK_PLATFORM_CLIENT_SECRET` | — | Slack OAuth app secret. |
| `TELEGRAM_WEBHOOK_BASE_URL` | — | Public URL for Telegram webhook registration. |
| `TELEGRAM_WEBHOOK_IP` | — | Restrict webhook source IP if needed. |

### Worker polling & limits

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | `60` | OAuth polling interval for CI providers. |
| `POLL_BATCH_SIZE` | `20` | Max connections polled per beat tick. |
| `RATE_LIMIT_PER_MINUTE` | `120` | Global API rate limit per IP. |
| `COST_SAVER_TTL_HOURS` | `6` | Dedup window for repeated analyses of the same failure. |

### Outbound HTTP proxy (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `OUTBOUND_HTTP_PROXY_URL` | — | Default HTTP proxy for outbound calls. |
| `TELEGRAM_PROXY_URL` | — | Proxy override for Telegram API. |
| `SLACK_PROXY_URL` | — | Proxy override for Slack API. |
| `OUTBOUND_HTTP_PROXY_TIMEOUT` | `10.0` | Proxy connect/read timeout (seconds). |

### Ops (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `ALLOWED_HOSTS` | — | Comma-separated hostnames for `TrustedHostMiddleware` in prod. |
| `FLOWER_BASIC_AUTH` | — | `user:password` for Flower UI if deployed separately. |

---

## License

[Apache License 2.0](./LICENSE) — Exlogare Community Edition.
