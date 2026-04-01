#!/bin/sh
# Uses positional parameters to safely handle values with spaces (e.g. APP_FEED="The Daily")
set -- --config /config/config.yaml

[ -n "${APP_FEED:-}" ]               && set -- "$@" --feed "${APP_FEED}"
[ -n "${APP_MIN_CONFIDENCE:-}" ]     && set -- "$@" --min-confidence "${APP_MIN_CONFIDENCE}"
[ -n "${APP_FORCE_AI_DETECTION:-}" ] && set -- "$@" --force-ai-detection
[ -n "${APP_LOG_TO_FILE:-}" ]        && set -- "$@" --log-to-file
[ -n "${APP_DEBUG:-}" ]              && set -- "$@" --debug

echo "run.sh: python main.py $*"
exec /app/.venv/bin/python /app/main.py "$@"
