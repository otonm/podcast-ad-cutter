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

if ! command -v systemd-escape &>/dev/null; then
    echo "ERROR: 'systemd-escape' not found (required for NFS unit name computation)"
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
    TAILSCALE_AUTH_KEY
    TAILSCALE_HOSTNAME
)
for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: Required variable '$var' is not set in deployment/env"
        exit 1
    fi
done

echo "==> Generating ignition.json"
echo "    GITHUB_USERNAME=${GITHUB_USERNAME}"
echo "    TAILSCALE_HOSTNAME=${TAILSCALE_HOSTNAME}"
echo "    NFS_SHARE=${NFS_SHARE:-<none, using named volume>}"

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

# Tailscale Quadlet unit: replace {{TAILSCALE_HOSTNAME}}
sed "s|{{TAILSCALE_HOSTNAME}}|${TAILSCALE_HOSTNAME}|g" \
    "${SCRIPT_DIR}/tailscale.container.template" \
    > "${STAGING}/tailscale.container"

# Copy first-boot script (no substitution — it reads secrets at runtime)
cp "${SCRIPT_DIR}/first-boot.sh" "${STAGING}/first-boot.sh"

# Copy operator's config.yaml from project root
cp "${PROJECT_ROOT}/config.yaml" "${STAGING}/config.yaml"

# Render butane template: substitute ${VAR} placeholders.
# The explicit variable list prevents envsubst from corrupting other $ chars.
BUTANE_VARS='${SSH_PUBLIC_KEY}${GITHUB_USERNAME}${GHCR_TOKEN}'\
'${TAILSCALE_AUTH_KEY}'\
'${ANTHROPIC_API_KEY}${OPENAI_API_KEY}${GROQ_API_KEY}'\
'${OPENROUTER_API_KEY}${GEMINI_API_KEY}'\
'${AWS_ACCESS_KEY_ID}${AWS_SECRET_ACCESS_KEY}${AWS_REGION_NAME}'

envsubst "$BUTANE_VARS" \
    < "${SCRIPT_DIR}/butane.yaml.template" \
    > "${STAGING}/butane.yaml"

# ── Render podcast-ad-cutter.container NFS placeholders ───────────────────────
if [[ -n "${NFS_SHARE:-}" ]]; then
    NFS_MOUNT_PATH="${NFS_MOUNT_PATH:-/mnt/podcast-output}"
    NFS_UNIT_NAME=$(systemd-escape --path --suffix=mount "${NFS_MOUNT_PATH}")

    echo "    NFS_SHARE=${NFS_SHARE} → ${NFS_UNIT_NAME}"

    # Render NFS mount unit into staging
    sed -e "s|{{NFS_SHARE}}|${NFS_SHARE}|g" \
        -e "s|{{NFS_MOUNT_PATH}}|${NFS_MOUNT_PATH}|g" \
        "${SCRIPT_DIR}/nfs-output.mount.template" \
        > "${STAGING}/${NFS_UNIT_NAME}"

    # Inject NFS directory into butane.yaml (replace marker line)
    NFS_DIR_YAML="    - path: ${NFS_MOUNT_PATH}\n      mode: 0755"
    sed -i "s|    # <<<NFS_DIRECTORIES>>>|${NFS_DIR_YAML}|" "${STAGING}/butane.yaml"

    # Inject NFS unit into butane.yaml (replace marker line)
    NFS_UNIT_YAML="    - name: ${NFS_UNIT_NAME}\n      enabled: true\n      contents:\n        local: ${NFS_UNIT_NAME}"
    sed -i "s|    # <<<NFS_UNITS>>>|${NFS_UNIT_YAML}|" "${STAGING}/butane.yaml"

    # Podcast-ad-cutter container: NFS dependency and host bind mount
    OUTPUT_VOLUME_LINE="Volume=${NFS_MOUNT_PATH}:/app/output:z"
    NFS_AFTER_LINE="After=${NFS_UNIT_NAME}"
    NFS_REQUIRES_LINE="Requires=${NFS_UNIT_NAME}"
else
    # Remove NFS marker lines from butane.yaml
    sed -i "/# <<<NFS_DIRECTORIES>>>/d" "${STAGING}/butane.yaml"
    sed -i "/# <<<NFS_UNITS>>>/d" "${STAGING}/butane.yaml"

    OUTPUT_VOLUME_LINE="Volume=podcast-ad-cutter-output:/app/output:Z"
    NFS_AFTER_LINE=""
    NFS_REQUIRES_LINE=""
fi

# Apply NFS/output placeholders to podcast-ad-cutter.container
sed -i "s|{{OUTPUT_VOLUME_LINE}}|${OUTPUT_VOLUME_LINE}|g" \
    "${STAGING}/podcast-ad-cutter.container"

if [[ -n "$NFS_AFTER_LINE" ]]; then
    sed -i \
        -e "s|{{NFS_AFTER_LINE}}|${NFS_AFTER_LINE}|" \
        -e "s|{{NFS_REQUIRES_LINE}}|${NFS_REQUIRES_LINE}|" \
        "${STAGING}/podcast-ad-cutter.container"
else
    sed -i \
        -e "/{{NFS_AFTER_LINE}}/d" \
        -e "/{{NFS_REQUIRES_LINE}}/d" \
        "${STAGING}/podcast-ad-cutter.container"
fi

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
echo "  VM (Proxmox/libvirt):  scp deployment/ignition.json root@proxmox:/root/"
echo "                         qm set <vmid> --args \"-fw_cfg name=opt/com.coreos/config,file=/root/ignition.json\""
echo "                         qm start <vmid>"
echo ""
echo "After first boot, verify:"
echo "  ssh core@<server-ip>"
echo "  systemctl status podcast-ad-cutter-first-boot.service"
echo "  journalctl -u podcast-ad-cutter-first-boot.service"
echo ""
echo "  Tailscale:  After first boot, enable SSH in your tailnet ACLs"
echo "              Then: ssh core@${TAILSCALE_HOSTNAME}.<tailnet-name>.ts.net"
