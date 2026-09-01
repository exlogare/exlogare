

[exlogare.net](https://exlogare.net) · [Документация](https://exlogare.net/docs) · [English](./README.md)



# Exlogare Community Edition

Open-source Community Edition Exlogare — self-hosted анализ падений CI/CD с AI root-cause отчётами. Развёртывание через Docker Compose.

## Быстрый старт

```bash
cp .env.example .env
# Заполните секреты — см. раздел «Переменные окружения» ниже

./scripts/generate_secrets.sh .env.example   # опционально: вывести секреты в stdout

docker compose up -d --build
```

Откройте `http://localhost:8080` (или ваш `WEB_PORT`), войдите с `ADMIN_EMAIL` / `ADMIN_PASSWORD`, подключите GitLab, GitHub, Jenkins или generic ingest.

Готовые образы без локальной сборки:

```bash
export IMAGE_TAG=latest          # или latest-dev / 1.0.0 / …
docker compose pull && docker compose up -d
```

## Возможности

- **Интеграции CI** — GitLab, GitHub, Bitbucket, GitFlic, Jenkins, generic ingest
- **AI root-cause analysis** — любой OpenAI-compatible LLM, включая **свою локальную модель** (Ollama, vLLM, LM Studio)
- **SSO** — опциональный OpenID Connect (Keycloak и другие OIDC IdP)
- **Dashboard** — анализы, повторяющиеся падения, статистика по проектам
- **Docker Compose** — postgres, redis, api, worker, beat, web

## Своя локальная модель

Exlogare ходит в любой OpenAI-compatible `/v1/chat/completions`. Для полностью автономного стека укажите Ollama (или vLLM / LM Studio) на хосте или в другом контейнере.

```env
LLM_ENABLED=true
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1
LLM_JSON_MODE=false
```

Замечания:

- Compose пробрасывает `host.docker.internal` → host-gateway для `api` и `worker`.
- На части Linux-хостов используйте `http://172.17.0.1:11434/v1`.
- Для локальных серверов без auth достаточно любого непустого `LLM_API_KEY`.
- Если модель не умеет JSON mode — `LLM_JSON_MODE=false`.
- После смены `.env` перезапустите API/worker. В **Настройки → AI** виден текущий endpoint и есть проверка связи.

| Backend | `LLM_BASE_URL` | `LLM_MODEL` | `LLM_API_KEY` |
|---------|----------------|-------------|---------------|
| Ollama | `http://host.docker.internal:11434/v1` | `llama3.1` | `ollama` |
| vLLM | `http://vllm:8000/v1` | `meta-llama/…` | `local` |
| LM Studio | `http://host.docker.internal:1234/v1` | *(из UI)* | `lm-studio` |
| OpenAI | *(пусто)* | `gpt-4o-mini` | `sk-…` |

## OpenID Connect (Keycloak)

Опциональный SSO рядом с email/password. Создайте **confidential** client со Standard flow и Valid redirect URI:

`{PUBLIC_BASE_URL}/api/auth/oidc/callback`

```env
OIDC_ENABLED=true
OIDC_ISSUER=https://keycloak.example.com/realms/exlogare
OIDC_CLIENT_ID=exlogare
OIDC_CLIENT_SECRET=…
OIDC_SCOPES=openid email profile
OIDC_AUTO_PROVISION=true
OIDC_DISPLAY_NAME=Keycloak
```

IdP должен отдавать claim `email`. При `OIDC_AUTO_PROVISION=true` первый вход создаёт участника в bootstrap-тенанте (сначала поднимите админа через `ADMIN_*`).

## Docker-образы

Публикуются в GHCR:


| Образ                           | Назначение                   |
| ------------------------------- | ---------------------------- |
| `ghcr.io/exlogare/exlogare-api` | FastAPI + Celery worker/beat |
| `ghcr.io/exlogare/exlogare-web` | nginx + SPA                  |


Задайте `IMAGE_TAG` в `.env`: `latest`, `latest-dev`, `latest-test` или зафиксированный релиз (например `1.0.0`).

## Архитектура

```
web (nginx + SPA)  →  api (FastAPI + init_db)
                         ↓
                    postgres, redis
                         ↓
                    worker, beat (Celery)
```

При каждом старте API `scripts/entrypoint-api.sh` выполняет `python -m app.core.init_db` (идемпотентно: `create_all` + bootstrap admin, если таблица `users` пуста).

## Свой Postgres и Redis

По умолчанию Compose поднимает контейнеры `postgres` и `redis`. Чтобы использовать **свои** managed-сервисы (RDS, Cloud SQL, ElastiCache, Memorystore, …):

**1.** Создайте базу PostgreSQL (**16+** рекомендуется) и Redis. Exlogare использует **три logical DB** на одном Redis:

| Переменная | Redis DB | Назначение |
|------------|----------|------------|
| `REDIS_URL` | `0` | Кэш приложения, rate limit |
| `CELERY_BROKER_URL` | `1` | Брокер Celery |
| `CELERY_RESULT_BACKEND` | `2` | Результаты задач Celery |

**2.** Пропишите URL в `.env` (спецсимволы в пароле — URL-encode):

```env
DATABASE_URL=postgresql+asyncpg://exlogare:secret@db.internal:5432/exlogare
SYNC_DATABASE_URL=postgresql+psycopg2://exlogare:secret@db.internal:5432/exlogare
REDIS_URL=redis://:redis-secret@redis.internal:6379/0
CELERY_BROKER_URL=redis://:redis-secret@redis.internal:6379/1
CELERY_RESULT_BACKEND=redis://:redis-secret@redis.internal:6379/2
```

- `DATABASE_URL` — async-драйвер для FastAPI (`asyncpg`)
- `SYNC_DATABASE_URL` — sync-драйвер для Celery (`psycopg2`)

**3.** Запуск без bundled Postgres/Redis:

```bash
docker compose -f docker-compose.yml -f docker-compose.external.yml up -d
```

Сервисы `postgres` и `redis` из стека не стартуют — только `api`, `worker`, `beat`, `web`.

**4.** Сеть — контейнеры должны достучаться до ваших хостов БД/Redis:

- общая VPC / Docker-сеть с базами
- `host.docker.internal` для сервисов на хосте Docker (Linux: добавьте `extra_hosts: ["host.docker.internal:host-gateway"]` в `api`, `worker`, `beat` через local override)
- private IP managed-сервисов, доступный с машины, где крутится Compose

**TLS** — для Postgres добавьте query-параметры, если провайдер требует SSL, например `?ssl=require` (смотрите документацию провайдера).

Для bundled-стека по умолчанию **не задавайте** эти пять URL — Compose соберёт их из `POSTGRES_*` и имён сервисов `postgres` / `redis`.

## HTTPS и PUBLIC_BASE_URL

`PUBLIC_BASE_URL` — адрес, по которому **внешние системы** достучатся до API Exlogare (вебхуки, OAuth redirect, боты). Должен совпадать с тем, что в `.env`: схема, хост и порт (если не 443/80). `WEB_BASE_URL` в типичном single-host Docker Compose обычно такой же.

OAuth callback (`GITLAB_OAUTH_REDIRECT_URI` и аналоги для GitHub/Bitbucket/GitFlic/OIDC) по умолчанию строятся из `PUBLIC_BASE_URL`, если переменные пустые. Именно этот URL указывайте в Application GitLab — **не** `localhost`, если GitLab с него не достучится; задайте `PUBLIC_BASE_URL` на LAN/DDNS/туннель.

Exlogare **не требует HTTPS жёстко в коде** — по умолчанию `http://localhost:8080`. Достаточно ли HTTP, зависит от **режима интеграции** и от того, **где** крутится GitLab/GitHub. Не все ставят Exlogare на публичный VPS; локальные и домашние установки тоже поддерживаются.

### Сценарии развёртывания

Выберите строку под ваш случай:

| Сценарий | Пример `PUBLIC_BASE_URL` | Подходит для | Заметки |
|----------|--------------------------|--------------|---------|
| **Только локальный UI** | `http://localhost:8080` | Dashboard, настройки, ручные тесты | Значение по умолчанию. Входящие вызовы от Git-хоста не нужны. |
| **OAuth + polling** | `http://localhost:8080` | Self-hosted или cloud GitLab/GitHub, когда Exlogare **сам опрашивает** CI | Exlogare ходит **наружу** к Git-хосту; вебхуки не обязательны. Удобно для ноутбука или домашнего ПК. |
| **Одна машина / LAN** | `http://192.168.1.10:8080` или `http://exlogare.local:8080` | Вебхуки от GitLab на **том же хосте или в LAN** | Не используйте `localhost` в URL вебхука — GitLab в Docker часто не видит `127.0.0.1` хоста. Укажите LAN IP или внутреннее DNS-имя. |
| **DDNS + HTTP (домашняя лаба)** | `http://exlogare.myddns.net:8080` | Вебхуки self-hosted GitLab через интернет | Проброс порта на роутере. GitLab должен достучаться до URL; для `http://` Exlogare отключает проверку SSL при регистрации хука. Не подходит для **GitHub.com** (нужен HTTPS) и **Telegram**. |
| **HTTPS-туннель (без VPS)** | `https://xyz.trycloudflare.com` или ngrok | Вебхуки от cloud GitLab/GitHub с ПК дома | Cloudflare Tunnel, ngrok и т.п. дают публичный HTTPS на процесс на вашей машине. Часто проще всего для локальной отладки вебхуков. |
| **Production-сервер** | `https://exlogare.example.com` | Все интеграции, мессенджеры, cloud OAuth | Рекомендуется для постоянной установки. Валидный TLS, порт 443. |

### Когда HTTPS действительно обязателен

- **Вебхуки GitHub.com** — GitHub шлёт хуки только на **HTTPS**.
- **Telegram-боты** — webhook URL только **HTTPS**.
- **Cloud OAuth** — приложения GitLab.com / GitHub.com часто ждут HTTPS redirect URI (кроме чистого localhost в dev).
- **Self-hosted GitLab** — HTTP-вебхуки разрешены; Exlogare сам ставит `enable_ssl_verification: false` для `http://`. Можно добавить хук вручную в GitLab и отключить проверку SSL там.

### Когда localhost недостаточно

Если Git-хост на **другой машине** (например `git.company.com` или GitLab.com), он не может POST на `http://localhost:8080` вашего ноутбука — для сервера `localhost` это он сам. Варианты:

1. Режим **polling** (входящий URL не нужен).
2. Проброс через **LAN IP**, **DDNS + port forward** или **HTTPS-туннель**.
3. Отправка логов из CI через **generic ingest** или `POST /api/analyze` (Exlogare должен быть доступен **раннеру**, не обязательно из интернета).

Dashboard предупреждает, если URL вебхука на `localhost`, а GitLab удалённый — следуйте шагам ручной настройки или смените `PUBLIC_BASE_URL`.

### Примеры `.env`

```env
# Ноутбук: UI + polling GitLab
PUBLIC_BASE_URL=http://localhost:8080
WEB_BASE_URL=http://localhost:8080

# Домашний ПК: вебхуки self-hosted GitLab через DDNS (HTTP)
PUBLIC_BASE_URL=http://exlogare.myddns.net:8080
WEB_BASE_URL=http://exlogare.myddns.net:8080

# VPS / production
PUBLIC_BASE_URL=https://exlogare.example.com
WEB_BASE_URL=https://exlogare.example.com
```

## Разработка

```bash
pip install -e ".[dev]"
LLM_ENABLED=false JWT_SECRET=dev ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  python -m app.core.init_db
uvicorn app.main:app --reload --port 8000

cd web && npm install && npm run dev
```

---

## Переменные окружения

Скопируйте `.env.example` в `.env`. Переменные соответствуют `app/core/config.py`.  
По умолчанию `docker-compose.yml` собирает `DATABASE_URL`, `REDIS_URL` и URL Celery из `POSTGRES_*` и имён сервисов. Для внешних Postgres/Redis задайте пять URL в `.env` и запускайте с `docker-compose.external.yml` — см. [Свой Postgres и Redis](#свой-postgres-и-redis).

Обозначения: **обязательно** = нужно перед первым prod-запуском; **bootstrap** = только при пустой БД; **опционально** = можно оставить по умолчанию.

### Образы и веб


| Переменная        | По умолчанию            | Описание                                                                                               |
| ----------------- | ----------------------- | ------------------------------------------------------------------------------------------------------ |
| `IMAGE_TAG`       | `latest`                | Docker-тег для `ghcr.io/exlogare/exlogare-api` и `exlogare-web`. Примеры: `latest-dev`, `1.0.0`.       |
| `WEB_PORT`        | `8080`                  | Порт на хосте для web-контейнера (nginx).                                                              |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | URL API для вебхуков и OAuth. Нужен для режима webhook; при удалённом Git-хосте — LAN/DDNS/туннель. См. [HTTPS и PUBLIC_BASE_URL](#https-и-public_base_url). |
| `WEB_BASE_URL`    | `http://localhost:8080` | Origin SPA для CORS и ссылок в письмах. В single-host compose обычно совпадает с `PUBLIC_BASE_URL`.    |
| `APP_ENV`         | `prod`                  | `dev`, `test`, `staging` или `prod`. Влияет на `/docs`, CORS, trusted hosts.                           |
| `UPDATE_CHECK_ENABLED` | `true`             | При `false` dashboard не обращается к GitHub за новыми релизами (бейдж показывает только установленную версию). |


### База данных (Postgres)


| Переменная          | По умолчанию | Описание                                                                             |
| ------------------- | ------------ | ------------------------------------------------------------------------------------ |
| `POSTGRES_USER`     | `exlogare`   | Роль Postgres. Используется сервисом `postgres` и подставляется в URL API в compose. |
| `POSTGRES_PASSWORD` | —            | **обязательно** Пароль Postgres. Сгенерировать: `scripts/generate_secrets.sh`.       |
| `POSTGRES_DB`       | `exlogare`   | Имя базы данных.                                                                     |
| `DATABASE_URL`      | *(compose)*  | Async URL SQLAlchemy (`postgresql+asyncpg://…`). Обязателен для внешнего Postgres — см. [Свой Postgres и Redis](#свой-postgres-и-redis). |
| `SYNC_DATABASE_URL` | *(compose)*  | Sync URL для Celery (`postgresql+psycopg2://…`). Обязателен с внешним Postgres. |


### Redis и Celery


| Переменная              | По умолчанию | Описание                                   |
| ----------------------- | ------------ | ------------------------------------------ |
| `REDIS_URL`             | *(compose)*  | Redis: кэш, rate limit. DB index `0`. Для внешнего Redis.     |
| `CELERY_BROKER_URL`     | *(compose)*  | Брокер Celery. DB index `1`. Для внешнего Redis. |
| `CELERY_RESULT_BACKEND` | *(compose)*  | Backend результатов Celery. DB index `2`. Для внешнего Redis. |


### Секреты и аутентификация


| Переменная                  | По умолчанию | Описание                                                                                                                                                                        |
| --------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `JWT_SECRET`                | —            | **обязательно** HMAC-секрет для JWT-сессий в cookie. Длинная случайная строка.                                                                                                  |
| `ENCRYPTION_KEY`            | —            | **обязательно** Fernet-ключ (base64) для шифрования OAuth-токенов в БД. Генерация: `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. |
| `JWT_EXPIRES_MINUTES`       | `10080`      | Время жизни JWT/сессии (7 дней).                                                                                                                                                |
| `LOGIN_RATE_LIMIT_PER_HOUR` | `30`         | Лимит попыток входа на email в час (защита от brute-force).                                                                                                                     |


### Bootstrap admin


| Переменная          | По умолчанию | Описание                                                                                                        |
| ------------------- | ------------ | --------------------------------------------------------------------------------------------------------------- |
| `ADMIN_EMAIL`       | —            | **bootstrap** Email администратора. Обязателен при первом запуске (пустая таблица `users`); далее игнорируется. |
| `ADMIN_PASSWORD`    | —            | **bootstrap** Пароль admin (мин. 8 символов). При перезапуске не меняется.                                      |
| `ADMIN_TENANT_NAME` | `Default`    | **bootstrap** Название начальной организации/tenant.                                                            |


### OpenID Connect (опционально)


| Переменная | По умолчанию | Описание |
| ---------- | ------------ | -------- |
| `OIDC_ENABLED` | `false` | Включить SSO-кнопку и OIDC-роуты. |
| `OIDC_ISSUER` | — | Issuer IdP (Keycloak: `…/realms/<realm>`). |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | — | Credentials confidential client. |
| `OIDC_REDIRECT_URI` | `{PUBLIC_BASE_URL}/api/auth/oidc/callback` | Должен совпадать с redirect URI клиента. |
| `OIDC_SCOPES` | `openid email profile` | Scopes (`email` обязателен). |
| `OIDC_AUTO_PROVISION` | `true` | Создавать участника при первом SSO-входе. |
| `OIDC_DISPLAY_NAME` | `SSO` | Текст на кнопке входа. |


### Хранение данных


| Переменная       | По умолчанию | Описание                                                                |
| ---------------- | ------------ | ----------------------------------------------------------------------- |
| `RETENTION_DAYS` | `365`        | Срок хранения анализов и истории. Ежедневная очистка через Celery beat. |


### LLM (OpenAI-compatible)


| Переменная                 | По умолчанию   | Описание                                                                                                             |
| -------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------- |
| `LLM_ENABLED`              | `true`         | `false` → `StubAnalyzer` (без реального AI; только dev/test).                                                        |
| `LLM_BASE_URL`             | *(пусто)*      | Base URL OpenAI-compatible API. Пусто = `https://api.openai.com/v1`.                                                 |
| `LLM_API_KEY`              | —              | API-ключ endpoint'а. Для Ollama/local — любое непустое значение (напр. `ollama`).                                    |
| `LLM_MODEL`                | `gpt-4o-mini`  | Имя модели на выбранном endpoint.                                                                                    |
| `LLM_TEMPERATURE`          | `0.1`          | Temperature сэмплирования.                                                                                           |
| `LLM_MAX_TOKENS`           | `900`          | Максимум токенов в ответе.                                                                                           |
| `LLM_JSON_MODE`            | `true`         | Запрашивать `response_format=json_object`. `false`, если endpoint не поддерживает JSON mode (есть fallback-парсинг). |
| `LLM_TAIL_LINES`           | `500`          | Сколько последних строк лога отправлять в модель.                                                                    |
| `LLM_TOKEN_BUDGET`         | `2500`         | Приблизительный бюджет токенов на excerpt лога.                                                                      |
| `LLM_SYSTEM_PROMPT`        | *(пусто)*      | Опционально: inline system prompt (перекрывает файл). `\n` для переносов строк.                                      |
| `LLM_SYSTEM_PROMPT_FILE`   | `config/llm_system_prompt.txt` | Встроенный RCA-промпт CE. Задаёт JSON-схему (`root_cause`, `explanation`, **`fix_suggestion`**, …) и требует конкретных шагов исправления. Можно редактировать или смонтировать свой файл. |
| `LLM_USER_PROMPT_TEMPLATE` | *(встроенный)* | Шаблон с `{header}`, `{log_excerpt}`, `{project_path}`.                                                              |


**Встроенный LLM-промпт**

Community Edition поставляется с [`config/llm_system_prompt.txt`](config/llm_system_prompt.txt) — в образе API путь `/app/config/llm_system_prompt.txt`. Достаточно указать `LLM_API_KEY` (и при необходимости `LLM_BASE_URL` / `LLM_MODEL`); для обычного RCA промпт настраивать не нужно.

Промпт требует **конкретный** `fix_suggestion` (пути к файлам, команды, изменения конфигура из лога), а не общие фразы вроде «проверьте лог». Если переопределяете промпт, сохраняйте те же ключи JSON: `root_cause`, `explanation`, `fix_suggestion`, `severity`, `confidence`, `needs_more_context`, `missing_context_hint`.


**Примеры LLM backend**


| Backend   | `LLM_BASE_URL`                                           | `LLM_MODEL`    | `LLM_API_KEY` |
| --------- | -------------------------------------------------------- | -------------- | ------------- |
| OpenAI    | *(пусто)*                                                | `gpt-4o-mini`  | `sk-…`        |
| Ollama    | `http://host.docker.internal:11434/v1`                   | `llama3.1`     | `ollama`      |
| vLLM      | `http://vllm:8000/v1`                                    | `meta-llama/…` | `local`       |
| LiteLLM   | `http://litellm:4000/v1`                                 | `gpt-4o`       | ключ прокси   |
| DashScope | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen-plus`    | dashscope key |


### GitLab (опционально)


| Переменная                   | По умолчанию         | Описание                                                                 |
| ---------------------------- | -------------------- | ------------------------------------------------------------------------ |
| `GITLAB_BASE_URL`            | `https://gitlab.com` | URL GitLab (self-managed: `https://gitlab.company.com`).                 |
| `GITLAB_OAUTH_CLIENT_ID`     | —                    | ID OAuth-приложения.                                                     |
| `GITLAB_OAUTH_CLIENT_SECRET` | —                    | Secret OAuth-приложения.                                                 |
| `GITLAB_OAUTH_REDIRECT_URI`  | `{PUBLIC_BASE_URL}/auth/gitlab/callback` | Оставьте пустым — возьмётся из `PUBLIC_BASE_URL`. |
| `GITLAB_WEBHOOK_SECRET`      | —                    | Shared secret для проверки webhook GitLab.                               |


### GitHub (опционально)


| Переменная                    | По умолчанию             | Описание                                              |
| ----------------------------- | ------------------------ | ----------------------------------------------------- |
| `GITHUB_BASE_URL`             | `https://github.com`     | GitHub.com или GHES.                                  |
| `GITHUB_API_BASE_URL`         | `https://api.github.com` | REST API (GHES: `https://github.company.com/api/v3`). |
| `GITHUB_OAUTH_CLIENT_ID`      | —                        | Client ID OAuth App.                                  |
| `GITHUB_OAUTH_CLIENT_SECRET`  | —                        | Client secret OAuth App.                              |
| `GITHUB_OAUTH_REDIRECT_URI`   | `{PUBLIC_BASE_URL}/api/integrations/github/oauth/callback` | Оставьте пустым — из `PUBLIC_BASE_URL`. |


### Bitbucket (опционально)


| Переменная                      | По умолчанию                    | Описание               |
| ------------------------------- | ------------------------------- | ---------------------- |
| `BITBUCKET_BASE_URL`            | `https://bitbucket.org`         | Cloud или Data Center. |
| `BITBUCKET_API_BASE_URL`        | `https://api.bitbucket.org/2.0` | REST API base.         |
| `BITBUCKET_OAUTH_CLIENT_ID`     | —                               | OAuth consumer key.    |
| `BITBUCKET_OAUTH_CLIENT_SECRET` | —                               | OAuth consumer secret. |
| `BITBUCKET_OAUTH_REDIRECT_URI`  | `{PUBLIC_BASE_URL}/api/integrations/bitbucket/oauth/callback` | Оставьте пустым — из `PUBLIC_BASE_URL`. |
| `BITBUCKET_WEBHOOK_SECRET`      | —                               | Secret для webhook.    |


### GitFlic (опционально)


| Переменная                    | По умолчанию               | Описание                                   |
| ----------------------------- | -------------------------- | ------------------------------------------ |
| `GITFLIC_BASE_URL`            | `https://gitflic.ru`       | GitFlic или self-hosted.                   |
| `GITFLIC_API_BASE_URL`        | `https://api.gitflic.ru`   | REST API (self-hosted: `{base}/rest-api`). |
| `GITFLIC_OAUTH_BASE_URL`      | `https://oauth.gitflic.ru` | OAuth server.                              |
| `GITFLIC_OAUTH_CLIENT_ID`     | —                          | OAuth client ID.                           |
| `GITFLIC_OAUTH_CLIENT_SECRET` | —                          | OAuth client secret.                       |
| `GITFLIC_OAUTH_REDIRECT_URI`  | `{PUBLIC_BASE_URL}/api/integrations/gitflic/oauth/callback` | Оставьте пустым — из `PUBLIC_BASE_URL`. |
| `GITFLIC_WEBHOOK_SECRET`      | —                          | Shared secret webhook.                     |


### Email (опционально)


| Переменная       | По умолчанию         | Описание                                      |
| ---------------- | -------------------- | --------------------------------------------- |
| `EMAIL_PROVIDER` | `auto`               | `console` (только лог), `smtp` или `auto`.    |
| `SMTP_URL`       | —                    | Полный SMTP URL (перекрывает host/port/user). |
| `SMTP_HOST`      | —                    | Хост SMTP-сервера.                            |
| `SMTP_PORT`      | `587`                | Порт SMTP.                                    |
| `SMTP_USERNAME`  | —                    | Логин SMTP.                                   |
| `SMTP_PASSWORD`  | —                    | Пароль SMTP.                                  |
| `SMTP_FROM`      | —                    | Адрес отправителя.                            |
| `SMTP_STARTTLS`  | `true`               | Использовать STARTTLS.                        |
| `FROM_EMAIL`     | `no-reply@localhost` | Fallback sender.                              |
| `CONTACT_EMAIL`  | `admin@localhost`    | Получатель заявок `/api/public/contact`.        |
| `SUPPORT_EMAIL`  | `admin@localhost`    | Дополнительный inbox для contact form (опц.).   |
| `COMPANY_NAME`   | `Exlogare`           | Название бренда в transactional email.          |


### Уведомления — Telegram и Slack (опционально)

Каналы мессенджеров настраиваются в dashboard (**Интеграции → Мессенджеры**): свой Telegram bot token или Slack incoming webhook. Platform-wide env vars не нужны.

| Переменная                       | По умолчанию | Описание                                        |
| -------------------------------- | ------------ | ----------------------------------------------- |
| `TELEGRAM_WEBHOOK_BASE_URL`      | —            | Публичный URL для регистрации Telegram webhook. |
| `TELEGRAM_WEBHOOK_IP`            | —            | Ограничение IP источника webhook.               |


### Polling worker и лимиты


| Переменная              | По умолчанию | Описание                                             |
| ----------------------- | ------------ | ---------------------------------------------------- |
| `POLL_INTERVAL_SECONDS` | `60`         | Интервал OAuth-polling CI-провайдеров.               |
| `POLL_BATCH_SIZE`       | `20`         | Макс. подключений за один tick beat.                 |
| `RATE_LIMIT_PER_MINUTE` | `120`        | Глобальный rate limit API на IP.                     |
| `COST_SAVER_TTL_HOURS`  | `6`          | Окно дедупликации повторных анализов одного падения. |


### Исходящий HTTP proxy (опционально)


| Переменная                    | По умолчанию | Описание                                        |
| ----------------------------- | ------------ | ----------------------------------------------- |
| `OUTBOUND_HTTP_PROXY_URL`     | —            | HTTP proxy по умолчанию для исходящих запросов. |
| `TELEGRAM_PROXY_URL`          | —            | Proxy только для Telegram API.                  |
| `SLACK_PROXY_URL`             | —            | Proxy только для Slack API.                     |
| `OUTBOUND_HTTP_PROXY_TIMEOUT` | `10.0`       | Таймаут proxy (секунды).                        |


### Эксплуатация (опционально)


| Переменная          | По умолчанию | Описание                                                      |
| ------------------- | ------------ | ------------------------------------------------------------- |
| `LOG_LEVEL`         | `INFO`       | Уровень логов: `DEBUG`, `INFO`, `WARNING`, `ERROR`.           |
| `ALLOWED_HOSTS`     | —            | Список host через запятую для `TrustedHostMiddleware` в prod. |
| `FLOWER_BASIC_AUTH` | —            | `user:password` для Flower UI при отдельном деплое.           |


---

## Лицензия

[Apache License 2.0](./LICENSE) — Exlogare Community Edition.