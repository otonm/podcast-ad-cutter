#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# Fix ownership of bind-mounted volumes — runs as root before privilege drop
chown -R app:app /output /data /logs /cache /config 2>/dev/null || true

if [ ! -f /app/run.sh ]; then
    echo "ERROR: /app/run.sh not found in container image" >&2
    exit 1
fi

# Use explicit interpreter — avoids shebang exec issues in some container runtimes
printf '%s /bin/sh /app/run.sh\n' "${CRON_SCHEDULE}" > /tmp/crontab

echo "entrypoint: schedule=${CRON_SCHEDULE}"
exec gosu app /usr/local/bin/supercronic -passthrough-logs /tmp/crontab
