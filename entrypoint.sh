#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

printf '%s /app/run.sh\n' "${CRON_SCHEDULE}" > /tmp/crontab

echo "entrypoint: schedule=${CRON_SCHEDULE}"
exec supercronic /tmp/crontab
