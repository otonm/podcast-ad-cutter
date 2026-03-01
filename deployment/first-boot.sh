#!/usr/bin/env bash
# first-boot.sh — runs once on first boot of the CoreOS host.
# Managed by podcast-ad-cutter-first-boot.service (enabled by Ignition).
set -euo pipefail

SENTINEL="/var/lib/podcast-ad-cutter/.setup-done"

if [[ -f "$SENTINEL" ]]; then
    echo "First-boot setup already complete. Skipping."
    exit 0
fi

echo "==> podcast-ad-cutter: starting first-boot setup"

# Load secrets written by Ignition (mode 0600, only readable by root/service)
# shellcheck source=/dev/null
source /etc/podcast-ad-cutter/secrets.env

# ── 1. Authenticate to ghcr.io ────────────────────────────────────────────────
# GHCR_TOKEN may be empty if the ghcr.io package is public — login still works
# but is not strictly required for pulling public images.
if [[ -n "${GHCR_TOKEN:-}" ]]; then
    echo "==> Logging in to ghcr.io as ${GITHUB_USERNAME}"
    printf '%s' "$GHCR_TOKEN" | \
        podman login ghcr.io --username "$GITHUB_USERNAME" --password-stdin
fi

# ── 2. Create Podman secrets from env vars ────────────────────────────────────
# All secrets are always created so the .container unit file stays static.
# Unused providers get an empty-string secret value — litellm ignores them.
for secret_name in \
    tailscale_auth_key \
    anthropic_api_key \
    openai_api_key \
    groq_api_key \
    openrouter_api_key \
    gemini_api_key \
    aws_access_key_id \
    aws_secret_access_key \
    aws_region_name
do
    # Convert snake_case secret name to UPPER_CASE env var name
    var_name="${secret_name^^}"

    if podman secret exists "$secret_name" 2>/dev/null; then
        echo "==> Secret '$secret_name' already exists — skipping"
    else
        printf '%s' "${!var_name:-}" | podman secret create "$secret_name" -
        echo "==> Created secret: $secret_name"
    fi
done

# ── 3. Pull the container image ───────────────────────────────────────────────
echo "==> Pulling ghcr.io/${GITHUB_USERNAME}/podcast-ad-cutter:latest"
podman pull "ghcr.io/${GITHUB_USERNAME}/podcast-ad-cutter:latest"

# ── 4. Reload systemd so Quadlet generates podcast-ad-cutter.service ──────────
echo "==> Running systemctl daemon-reload (Quadlet generator)"
systemctl daemon-reload

# ── 4b. Enable and start Tailscale ────────────────────────────────────────────
echo "==> Enabling tailscale.service"
systemctl enable --now tailscale.service

# ── 5. Start the web UI service ───────────────────────────────────────────────
# daemon-reload causes Quadlet to generate podcast-ad-cutter.service, but systemd
# does not auto-start units that become wanted by an already-active target.
# We start it explicitly here; WantedBy=default.target handles subsequent reboots.
echo "==> Starting podcast-ad-cutter.service"
systemctl start podcast-ad-cutter.service

# ── 6. Destroy the plaintext secrets file ────────────────────────────────────
echo "==> Shredding /etc/podcast-ad-cutter/secrets.env"
shred -u /etc/podcast-ad-cutter/secrets.env

# ── 7. Mark setup as complete ─────────────────────────────────────────────────
mkdir -p "$(dirname "$SENTINEL")"
touch "$SENTINEL"

echo "==> First-boot setup complete."
echo "    Web UI:          http://<tailscale-hostname>.<tailnet>.ts.net:8000"
echo "    Live logs:       journalctl -u podcast-ad-cutter.service -f"
