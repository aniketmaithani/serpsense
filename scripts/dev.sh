#!/usr/bin/env bash
# Run SerpSense on this machine without Docker: a private Postgres and Redis in .dev/ (their own
# ports, so a Postgres or Redis you already run is left alone), migrations, then the web app,
# both Celery workers and beat in the foreground. Ctrl-C stops the app.
#
#   scripts/dev.sh                 start everything (mail goes to Mailpit, or prints in the log)
#   scripts/dev.sh --real-mail     send mail with the SMTP settings in .env instead
#   scripts/dev.sh cli <command>   run a serpsense command against it, e.g. cli seed-demo --owner you@example.com
#   scripts/dev.sh stop            stop any leftover app processes and the private services
#
# Needs uv, and Postgres and Redis on PATH (brew install postgresql@17 redis); Mailpit is optional
# (brew install mailpit). Settings come from .env; DATABASE_URL and REDIS_URL are set here.
set -euo pipefail
cd "$(dirname "$0")/.."
DEV="$PWD/.dev"
PG_PORT=${DEV_PG_PORT:-5434}
REDIS_PORT=${DEV_REDIS_PORT:-6381}
WEB_PORT=${DEV_WEB_PORT:-8000}
SMTP_DEV_PORT=1026
MAILPIT_UI=8026

need() { command -v "$1" >/dev/null || { echo "Missing $1: $2" >&2; exit 1; }; }

stop_all() {
  pkill -f "$PWD/.venv/bin/uvicorn serpsense" 2>/dev/null || true
  pkill -f "celery -A serpsense.entrypoints.jobs.celery_app" 2>/dev/null || true
  if pg_ctl -D "$DEV/pg" status >/dev/null 2>&1; then pg_ctl -D "$DEV/pg" -m fast stop >/dev/null; fi
  redis-cli -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 || true
  if [ -f "$DEV/mailpit.pid" ]; then kill "$(cat "$DEV/mailpit.pid")" 2>/dev/null || true; rm -f "$DEV/mailpit.pid"; fi
  echo "Stopped the app and the private Postgres, Redis and Mailpit (data stays in .dev/)."
}

start_services() {
  need uv "https://docs.astral.sh/uv/"
  need pg_ctl "brew install postgresql@17"
  need redis-server "brew install redis"
  mkdir -p "$DEV"
  if [ ! -f "$DEV/pg/PG_VERSION" ]; then initdb -D "$DEV/pg" -U serpsense --auth=trust >/dev/null; fi
  if ! pg_ctl -D "$DEV/pg" status >/dev/null 2>&1; then
    pg_ctl -D "$DEV/pg" -l "$DEV/pg.log" -w start \
      -o "-p $PG_PORT -k $DEV -c listen_addresses=127.0.0.1" >/dev/null
  fi
  if ! psql -h 127.0.0.1 -p "$PG_PORT" -U serpsense -d postgres -tAc \
      "select 1 from pg_database where datname = 'serpsense'" | grep -q 1; then
    createdb -h 127.0.0.1 -p "$PG_PORT" -U serpsense serpsense
  fi
  if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --daemonize yes --dir "$DEV" \
      --save "" --logfile "$DEV/redis.log"
  fi
}

configure() {
  if [ ! -f .env ]; then
    echo "No .env: cp .env.example .env && uv run serpsense gen-secrets >> .env" >&2
    exit 1
  fi
  set -a; . ./.env; set +a
  export DATABASE_URL="postgresql+psycopg://serpsense@127.0.0.1:$PG_PORT/serpsense"
  export REDIS_URL="redis://127.0.0.1:$REDIS_PORT/0"
  if [ "${1:-}" = --real-mail ]; then return; fi
  if command -v mailpit >/dev/null; then
    if ! curl -s -o /dev/null "http://127.0.0.1:$MAILPIT_UI/"; then
      mailpit --smtp "127.0.0.1:$SMTP_DEV_PORT" --listen "127.0.0.1:$MAILPIT_UI" \
        >"$DEV/mailpit.log" 2>&1 &
      echo $! >"$DEV/mailpit.pid"
    fi
    export EMAIL_BACKEND=smtp SMTP_HOST=127.0.0.1 SMTP_PORT=$SMTP_DEV_PORT SMTP_STARTTLS=false
    export SMTP_USER="" SMTP_PASSWORD=""
    MAIL="Mailpit: http://127.0.0.1:$MAILPIT_UI"
  else
    export EMAIL_BACKEND=console
    MAIL="no Mailpit (brew install mailpit): sign-in codes print in the [outbox] lines"
  fi
}

run() {  # run <name> <command...>: in the background, its lines prefixed with its name
  local name=$1; shift
  "$@" 2>&1 | awk -v p="[$name] " '{ print p $0; fflush() }' &
}

case "${1:-}" in
  stop) stop_all; exit 0 ;;
  cli) shift; start_services; configure; exec uv run serpsense "$@" ;;
esac

start_services
configure "${1:-}"
uv sync -q
uv run alembic upgrade head
trap 'kill 0' INT TERM EXIT
APP=serpsense.entrypoints.jobs.celery_app
run web uv run uvicorn serpsense.entrypoints.web.app:create_app --factory --reload \
  --host 127.0.0.1 --port "$WEB_PORT"
run worker uv run celery -A "$APP" worker -Q scans,maintenance -n scans@%h --concurrency 2 --loglevel INFO
run outbox uv run celery -A "$APP" worker -Q outbox -n outbox@%h --concurrency 2 --loglevel INFO
run beat uv run celery -A "$APP" beat --loglevel INFO --schedule "$DEV/celerybeat-schedule"
echo "SerpSense: http://127.0.0.1:$WEB_PORT   ${MAIL:-mail from .env}   (Ctrl-C stops it)"
wait
