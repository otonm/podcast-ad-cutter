#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# Fix ownership of bind-mounted volumes — runs as root before privilege drop
chown -R app:app /output /data /logs /cache /config 2>/dev/null || true

if [ ! -f /app/run.sh ]; then
    echo "ERROR: /app/run.sh not found in container image" >&2
    exit 1
fi

if [ -n "${APP_SERVE:-}" ]; then
    echo "entrypoint: server mode, schedule=${CRON_SCHEDULE}"
    # Scheduled runs go through the API so they share the server's run state (409 while running).
    API="http://127.0.0.1:${APP_PORT:-8080}/api/v1"
    printf '%s curl -fsS -X POST %s/run\n' "${CRON_SCHEDULE}" "${API}" > /tmp/crontab
    gosu app /usr/local/bin/supercronic -passthrough-logs /tmp/crontab &
    # Startup run, mirroring cron mode; waits until the API answers.
    ( until curl -fsS "${API}/health" >/dev/null 2>&1; do sleep 1; done
      curl -fsS -X POST "${API}/run" || true ) &
    exec gosu app /bin/sh /app/run.sh
fi

# Run once at startup instead of waiting for the first cron tick. Foreground so a
# scheduled run can't overlap it; a failure must not stop the schedule from starting.
echo "entrypoint: startup run"
gosu app /bin/sh /app/run.sh || echo "entrypoint: startup run failed (exit $?), continuing" >&2

# Use explicit interpreter — avoids shebang exec issues in some container runtimes
printf '%s /bin/sh /app/run.sh\n' "${CRON_SCHEDULE}" > /tmp/crontab

echo "entrypoint: schedule=${CRON_SCHEDULE}"
exec gosu app /usr/local/bin/supercronic -passthrough-logs /tmp/crontab
