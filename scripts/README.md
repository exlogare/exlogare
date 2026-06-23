# scripts/

Operational helpers for Exlogare Community Edition.

| File                  | Purpose                                                       |
| --------------------- | ------------------------------------------------------------- |
| `entrypoint-api.sh`   | Runs `python -m app.core.init_db` before Gunicorn on API start |
| `generate_secrets.sh` | Generate JWT / encryption / DB secrets for `.env`             |

## Database bootstrap

Schema is created automatically on API startup — no Alembic migrations.

```sh
docker compose exec api python -m app.core.init_db
```

On first run (empty `users` table), set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env` before starting the API.

## Generate secrets

```sh
./scripts/generate_secrets.sh .env.example
```

Paste the output into your `.env` and set `ADMIN_*` and `LLM_*` manually.
