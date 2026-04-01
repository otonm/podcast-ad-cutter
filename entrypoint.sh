#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

if [ ! -f /app/run.sh ]; then
    echo "ERROR: /app/run.sh not found in container image" >&2
    exit 1
fi

# Use explicit interpreter — avoids shebang exec issues in some container runtimes
printf '%s /bin/sh /app/run.sh\n' "${CRON_SCHEDULE}" > /tmp/crontab

echo "entrypoint: schedule=${CRON_SCHEDULE}"
exec supercronic /tmp/crontab
