#!/bin/sh
# Community Edition API entrypoint: init DB schema + bootstrap admin, then exec CMD.
set -eu

if [ "${1:-}" = "gunicorn" ]; then
	echo "[entrypoint] running init_db (create_all + bootstrap)"
	python -m app.core.init_db
fi

exec "$@"
