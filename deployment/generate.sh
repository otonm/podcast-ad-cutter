#!/usr/bin/env bash
# generate.sh — generate ignition.json from templates and the 'env' file.
#
# Usage:
#   cp deployment/env.example deployment/env
#   # edit deployment/env
#   ./deployment/generate.sh
#
# Requires: butane (https://github.com/coreos/butane/releases)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STAGING="${SCRIPT_DIR}/.staging"
ENV_FILE="${SCRIPT_DIR}/env"
OUTPUT="${SCRIPT_DIR}/ignition.json"

# ── Preflight checks ──────────────────────────────────────────────────────────
if ! command -v butane &>/dev/null; then
    echo "ERROR: 'butane' not found. Install from https://github.com/coreos/butane/releases"
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: deployment/env not found."
    echo "  cp deployment/env.example deployment/env"
    echo "  # fill in your values"
    exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/config.yaml" ]]; then
    echo "ERROR: config.yaml not found at project root."
    echo "  cp config.example.yaml config.yaml"
    echo "  # fill in your feed URLs and model settings"
    exit 1
fi

# ── Load env ──────────────────────────────────────────────────────────────────
# set -a: auto-export all variables defined after this point
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# ── Validate required vars ────────────────────────────────────────────────────
required_vars=(
    GITHUB_USERNAME
    SSH_PUBLIC_KEY
    FEED_CHECK_INTERVAL_HOURS
)
for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: Required variable '$var' is not set in deployment/env"
        exit 1
    fi
done

if ! [[ "${FEED_CHECK_INTERVAL_HOURS}" =~ ^[0-9]+$ ]] || \
   (( FEED_CHECK_INTERVAL_HOURS < 1 || FEED_CHECK_INTERVAL_HOURS > 24 )); then
    echo "ERROR: FEED_CHECK_INTERVAL_HOURS must be an integer between 1 and 24."
    exit 1
fi

echo "==> Generating ignition.json"
echo "    GITHUB_USERNAME=${GITHUB_USERNAME}"
echo "    FEED_CHECK_INTERVAL_HOURS=${FEED_CHECK_INTERVAL_HOURS}"

# ── Create staging directory ──────────────────────────────────────────────────
rm -rf "$STAGING"
mkdir -p "$STAGING"

# Cleanup staging on exit (including on error)
trap 'rm -rf "$STAGING"' EXIT

# ── Render templates into staging ─────────────────────────────────────────────

# Quadlet container unit: replace {{GITHUB_USERNAME}}
sed "s|{{GITHUB_USERNAME}}|${GITHUB_USERNAME}|g" \
    "${SCRIPT_DIR}/podcast-ad-cutter.container.template" \
    > "${STAGING}/podcast-ad-cutter.container"

# Quadlet timer unit: replace {{INTERVAL_HOURS}}
# FEED_CHECK_INTERVAL_HOURS=24 maps to systemd 'daily' shorthand because
# 0/24 is not a valid systemd hour value (valid range is 0-23).
if (( FEED_CHECK_INTERVAL_HOURS == 24 )); then
    sed "s|OnCalendar=.*|OnCalendar=daily|g" \
        "${SCRIPT_DIR}/podcast-ad-cutter.timer.template" \
        > "${STAGING}/podcast-ad-cutter.timer"
else
    sed "s|{{INTERVAL_HOURS}}|${FEED_CHECK_INTERVAL_HOURS}|g" \
        "${SCRIPT_DIR}/podcast-ad-cutter.timer.template" \
        > "${STAGING}/podcast-ad-cutter.timer"
fi

# Copy first-boot script (no substitution — it reads secrets at runtime)
cp "${SCRIPT_DIR}/first-boot.sh" "${STAGING}/first-boot.sh"

# Copy operator's config.yaml from project root
cp "${PROJECT_ROOT}/config.yaml" "${STAGING}/config.yaml"

# Render butane template: substitute ${VAR} placeholders.
# The explicit variable list prevents envsubst from corrupting other $ chars.
BUTANE_VARS='${SSH_PUBLIC_KEY}${GITHUB_USERNAME}${GHCR_TOKEN}'\
'${ANTHROPIC_API_KEY}${OPENAI_API_KEY}${GROQ_API_KEY}'\
'${OPENROUTER_API_KEY}${GEMINI_API_KEY}'\
'${AWS_ACCESS_KEY_ID}${AWS_SECRET_ACCESS_KEY}${AWS_REGION_NAME}'

envsubst "$BUTANE_VARS" \
    < "${SCRIPT_DIR}/butane.yaml.template" \
    > "${STAGING}/butane.yaml"

# ── Run butane ────────────────────────────────────────────────────────────────
butane \
    --pretty \
    --strict \
    --files-dir "$STAGING" \
    "${STAGING}/butane.yaml" \
    > "$OUTPUT"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> ignition.json generated at deployment/ignition.json"
echo ""
echo "WARNING: This file contains plaintext secrets (API keys, GHCR token)."
echo "         Keep it private. It is gitignored."
echo ""
echo "Next steps — provision the CoreOS server:"
echo ""
echo "  Cloud (user-data):  pass deployment/ignition.json as instance user-data"
echo "  Bare metal:         coreos-installer install /dev/sda \\"
echo "                        --ignition-file deployment/ignition.json"
echo "  VM (libvirt):       add ignition config path to VM definition"
echo ""
echo "After first boot, verify:"
echo "  ssh core@<server-ip>"
echo "  systemctl status podcast-ad-cutter-first-boot.service"
echo "  journalctl -u podcast-ad-cutter-first-boot.service"
