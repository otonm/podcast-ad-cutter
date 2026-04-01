#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# Write the run script dynamically so it always exists in /tmp.
# Single-quoted heredoc: $ and $@ are literal (not expanded here).
cat > /tmp/run.sh << 'SCRIPT'
#!/bin/sh
set -- --config /config/config.yaml
[ -n "${APP_FEED:-}" ]               && set -- "$@" --feed "${APP_FEED}"
[ -n "${APP_MIN_CONFIDENCE:-}" ]     && set -- "$@" --min-confidence "${APP_MIN_CONFIDENCE}"
[ -n "${APP_FORCE_AI_DETECTION:-}" ] && set -- "$@" --force-ai-detection
[ -n "${APP_LOG_TO_FILE:-}" ]        && set -- "$@" --log-to-file
[ -n "${APP_DEBUG:-}" ]              && set -- "$@" --debug
echo "run: python main.py $*"
exec /app/.venv/bin/python /app/main.py "$@"
SCRIPT
chmod +x /tmp/run.sh

printf '%s /bin/sh /tmp/run.sh\n' "${CRON_SCHEDULE}" > /tmp/crontab

echo "entrypoint: schedule=${CRON_SCHEDULE}"
exec supercronic /tmp/crontab
