# Deployment: Podman on Fedora CoreOS with Ignition Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a `deployment/` folder containing everything needed to build a versioned OCI
image, push it to ghcr.io via GitHub Actions, and provision a Fedora CoreOS server on first
boot via an Ignition file — with the feed check frequency controlled by an env var.

**Architecture:** GitHub Actions builds a multi-stage OCI image on every push to `main` and
pushes it to `ghcr.io`. The CoreOS host is configured entirely on first boot by an Ignition
file (generated locally from a Butane template). A systemd one-shot container runs the pipeline
on a configurable timer; `podman auto-update` pulls new image digests daily.

**Tech Stack:** Podman, Fedora CoreOS, Butane/Ignition, Quadlet (systemd container units),
GitHub Actions (`docker/build-push-action`), `envsubst`, `shellcheck`, `butane` CLI.

**Prerequisites (operator workstation):**
- `butane` CLI: download from https://github.com/coreos/butane/releases or `yay -S butane`
- `shellcheck`: `sudo pacman -S shellcheck`
- `podman`: already installed on the dev machine

---

## Task 1: Create `deployment/` scaffold

**Files:**
- Create: `deployment/.gitignore`
- Create: `deployment/env.example`

**Step 1: Create the deployment directory and .gitignore**

```bash
mkdir -p deployment
```

Create `deployment/.gitignore` with this exact content:

```
# Generated files — never commit
env
ignition.json
butane.yaml
podcast-ad-cutter.container
podcast-ad-cutter.timer
.staging/
```

**Step 2: Create `deployment/env.example`**

```bash
# Copy this file to 'env' (same directory) and fill in all values.
# Then run: ./deployment/generate.sh
#
# The 'env' file and ignition.json are gitignored — they contain secrets.

# ── GitHub ────────────────────────────────────────────────────────────────────
# Your GitHub username (owner of the ghcr.io package)
GITHUB_USERNAME=your-github-username

# GitHub Personal Access Token with 'read:packages' scope.
# Create at: https://github.com/settings/tokens
# (Only needed if the ghcr.io package is private. Public repos → leave blank.)
GHCR_TOKEN=ghp_...

# ── CoreOS host ───────────────────────────────────────────────────────────────
# Full SSH public key string for the 'core' user on the server.
# Example: "ssh-ed25519 AAAA... user@host"
SSH_PUBLIC_KEY=ssh-ed25519 AAAA...

# ── Scheduling ────────────────────────────────────────────────────────────────
# How often to check configured podcast feeds, in hours (integer).
# Examples: 1, 6, 12, 24
FEED_CHECK_INTERVAL_HOURS=6

# ── API keys (Podman secrets) ─────────────────────────────────────────────────
# Fill in only the providers you use. Others can be left blank.
# These are stored as Podman secrets on the CoreOS host — not in config.yaml.
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
GEMINI_API_KEY=
```

**Step 3: Verify directory**

```bash
ls deployment/
```

Expected output: `.gitignore  env.example`

**Step 4: Commit**

```bash
git add deployment/.gitignore deployment/env.example
git commit -m "feat(deployment): add scaffold — .gitignore and env.example"
```

---

## Task 2: Write `Containerfile`

**Files:**
- Create: `Containerfile` (project root)

**Step 1: Write `Containerfile`**

```dockerfile
# Stage 1: builder — install Python deps via uv
FROM python:3.12-slim AS builder

# Copy uv binary from the official image (no apt install needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy lockfile and metadata first — layer is cached if these don't change
COPY pyproject.toml uv.lock .python-version ./

# Install dependencies into .venv (excludes dev deps, excludes the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY . .

# Stage 2: runtime — lean image with ffmpeg and the pre-built venv
FROM python:3.12-slim AS runtime

# ffmpeg is required by pydub for audio processing
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the populated venv and all source from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

# Prepend .venv/bin to PATH so 'python' resolves to the venv interpreter
ENV PATH="/app/.venv/bin:$PATH"

# No default CMD — Quadlet's Exec= appends runtime flags (--use-cache --config ...)
ENTRYPOINT ["python", "main.py"]
```

**Step 2: Verify the image builds**

This takes a few minutes on first run (downloads base images and deps).

```bash
podman build -t podcast-ad-cutter:test .
```

Expected: build completes with `Successfully tagged localhost/podcast-ad-cutter:test`

**Step 3: Verify the entrypoint is correct**

```bash
podman inspect podcast-ad-cutter:test --format '{{.Config.Entrypoint}}'
```

Expected: `[python main.py]`

**Step 4: Remove test image**

```bash
podman rmi podcast-ad-cutter:test
```

**Step 5: Commit**

```bash
git add Containerfile
git commit -m "feat(deployment): add multi-stage Containerfile"
```

---

## Task 3: Write GitHub Actions workflow

**Files:**
- Create: `.github/workflows/build-push.yml`

**Step 1: Create the workflow directory**

```bash
mkdir -p .github/workflows
```

**Step 2: Write `.github/workflows/build-push.yml`**

```yaml
name: Build and Push OCI Image

on:
  push:
    branches: [main]

jobs:
  build-push:
    runs-on: ubuntu-latest

    permissions:
      contents: read
      packages: write   # Required to push to ghcr.io using GITHUB_TOKEN

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Log in to ghcr.io
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/podcast-ad-cutter:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

**Step 3: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build-push.yml'))" \
    && echo "YAML valid"
```

Expected: `YAML valid`

**Step 4: Commit**

```bash
git add .github/workflows/build-push.yml
git commit -m "feat(deployment): add GitHub Actions workflow — build and push to ghcr.io"
```

---

## Task 4: Write Quadlet container unit template

**Files:**
- Create: `deployment/podcast-ad-cutter.container.template`

**Context:** Quadlet is the Podman-native way to run containers as systemd services. A
`.container` file in `/etc/containers/systemd/` is automatically converted to a systemd
`.service` unit by the Quadlet generator at boot. The `{{GITHUB_USERNAME}}` placeholder is
replaced by `generate.sh` using `sed`.

**Step 1: Write `deployment/podcast-ad-cutter.container.template`**

```ini
[Unit]
Description=Podcast Ad Cutter
After=network-online.target
Wants=network-online.target

[Container]
Image=ghcr.io/{{GITHUB_USERNAME}}/podcast-ad-cutter:latest

# Tells 'podman auto-update' to check ghcr.io for a newer image digest daily
AutoUpdate=registry

# Named volumes — created automatically by Podman on first run
Volume=podcast-ad-cutter-data:/app/data:Z
Volume=podcast-ad-cutter-output:/app/output:Z

# config.yaml is managed on the host; mounted read-only into the container
Volume=/etc/podcast-ad-cutter/config.yaml:/etc/podcast-ad-cutter/config.yaml:ro,Z

# API keys exposed as env vars from Podman secrets (created by first-boot.sh)
# All five secrets are always created (empty string for unused providers) so
# this unit file stays static regardless of which providers are configured.
Secret=anthropic_api_key,type=env,target=ANTHROPIC_API_KEY
Secret=openai_api_key,type=env,target=OPENAI_API_KEY
Secret=groq_api_key,type=env,target=GROQ_API_KEY
Secret=openrouter_api_key,type=env,target=OPENROUTER_API_KEY
Secret=gemini_api_key,type=env,target=GEMINI_API_KEY

# Arguments appended to the ENTRYPOINT defined in the Containerfile.
# --use-cache: skip transcription if a transcript already exists in the DB
# --config: read feed URLs and model settings from the host-managed config.yaml
Exec=--use-cache --config /etc/podcast-ad-cutter/config.yaml

[Service]
# oneshot: container runs, does its work, exits — timer re-triggers next run
Type=oneshot
# no retry on failure — the next timer firing handles it (avoids API spam)
Restart=no
```

**Step 2: Verify the file has no stray shell-interpolation characters**

```bash
grep -n '\$' deployment/podcast-ad-cutter.container.template
```

Expected: no output (no `$` characters in the file).

**Step 3: Commit**

```bash
git add deployment/podcast-ad-cutter.container.template
git commit -m "feat(deployment): add Quadlet container unit template"
```

---

## Task 5: Write Quadlet timer unit template

**Files:**
- Create: `deployment/podcast-ad-cutter.timer.template`

**Context:** The timer activates `podcast-ad-cutter.service` (auto-named from the `.container`
file) every `FEED_CHECK_INTERVAL_HOURS` hours. `{{INTERVAL_HOURS}}` is replaced by `generate.sh`
using `sed`.

**Step 1: Write `deployment/podcast-ad-cutter.timer.template`**

```ini
[Unit]
Description=Podcast Ad Cutter - Feed Check Timer

[Timer]
# Fire 5 minutes after boot to catch any run that was missed while the server was off
OnBootSec=5min

# Fire every INTERVAL_HOURS hours, aligned to midnight.
# Example with INTERVAL_HOURS=6: fires at 00:00, 06:00, 12:00, 18:00 daily.
OnCalendar=*-*-* 0/{{INTERVAL_HOURS}}:00:00

# If the server was off during a scheduled firing, run immediately on next boot
Persistent=true

[Install]
WantedBy=timers.target
```

**Step 2: Verify the placeholder is present**

```bash
grep '{{INTERVAL_HOURS}}' deployment/podcast-ad-cutter.timer.template
```

Expected: one matching line.

**Step 3: Commit**

```bash
git add deployment/podcast-ad-cutter.timer.template
git commit -m "feat(deployment): add Quadlet timer unit template"
```

---

## Task 6: Write `first-boot.sh`

**Files:**
- Create: `deployment/first-boot.sh`

**Context:** This script is embedded into the Ignition file by `generate.sh` (via a `local:`
reference in `butane.yaml.template`). On the CoreOS host, Ignition installs it at
`/usr/local/bin/podcast-ad-cutter-first-boot` and a systemd oneshot service runs it once,
after `network-online.target`. A sentinel file prevents it from running again on subsequent boots.

**Step 1: Write `deployment/first-boot.sh`**

```bash
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
# All five secrets are always created so the .container unit file stays static.
# Unused providers get an empty-string secret value — litellm ignores them.
for secret_name in \
    anthropic_api_key \
    openai_api_key \
    groq_api_key \
    openrouter_api_key \
    gemini_api_key
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

# ── 5. Enable and start the feed check timer ──────────────────────────────────
echo "==> Enabling podcast-ad-cutter.timer"
systemctl enable --now podcast-ad-cutter.timer

# ── 6. Destroy the plaintext secrets file ────────────────────────────────────
echo "==> Shredding /etc/podcast-ad-cutter/secrets.env"
shred -u /etc/podcast-ad-cutter/secrets.env

# ── 7. Mark setup as complete ─────────────────────────────────────────────────
mkdir -p "$(dirname "$SENTINEL")"
touch "$SENTINEL"

echo "==> First-boot setup complete."
echo "    Next feed check: systemctl status podcast-ad-cutter.timer"
echo "    Live logs:       journalctl -u podcast-ad-cutter.service -f"
```

**Step 2: Check with shellcheck**

```bash
shellcheck deployment/first-boot.sh
```

Expected: no output (no warnings or errors).

**Step 3: Commit**

```bash
git add deployment/first-boot.sh
git commit -m "feat(deployment): add first-boot setup script"
```

---

## Task 7: Write `butane.yaml.template`

**Files:**
- Create: `deployment/butane.yaml.template`

**Context:** Butane is the human-readable format for CoreOS Ignition configs. The operator runs
`butane` to convert `butane.yaml` → `ignition.json`. The `local:` keys reference files in the
staging directory (created by `generate.sh`). Placeholders use `${VAR}` syntax — `generate.sh`
runs `envsubst` with an explicit variable list to avoid corrupting non-placeholder `$` chars.

**Step 1: Write `deployment/butane.yaml.template`**

```yaml
variant: fcos
version: 1.5.0

# ── User setup ────────────────────────────────────────────────────────────────
passwd:
  users:
    - name: core
      ssh_authorized_keys:
        - ${SSH_PUBLIC_KEY}

# ── Files written on first boot ───────────────────────────────────────────────
storage:
  directories:
    - path: /etc/podcast-ad-cutter
      mode: 0755
    - path: /var/lib/podcast-ad-cutter
      mode: 0755

  files:
    # Operator's feed/model config — mounted read-only into the container
    - path: /etc/podcast-ad-cutter/config.yaml
      mode: 0644
      contents:
        local: config.yaml    # copied from project root by generate.sh

    # Plaintext secrets — mode 0600, destroyed by first-boot.sh after use
    - path: /etc/podcast-ad-cutter/secrets.env
      mode: 0600
      contents:
        inline: |
          GITHUB_USERNAME=${GITHUB_USERNAME}
          GHCR_TOKEN=${GHCR_TOKEN}
          ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
          OPENAI_API_KEY=${OPENAI_API_KEY}
          GROQ_API_KEY=${GROQ_API_KEY}
          OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
          GEMINI_API_KEY=${GEMINI_API_KEY}

    # Quadlet container unit ({{GITHUB_USERNAME}} already substituted by generate.sh)
    - path: /etc/containers/systemd/podcast-ad-cutter.container
      mode: 0644
      contents:
        local: podcast-ad-cutter.container

    # Quadlet timer unit ({{INTERVAL_HOURS}} already substituted by generate.sh)
    - path: /etc/containers/systemd/podcast-ad-cutter.timer
      mode: 0644
      contents:
        local: podcast-ad-cutter.timer

    # First-boot script
    - path: /usr/local/bin/podcast-ad-cutter-first-boot
      mode: 0755
      contents:
        local: first-boot.sh

    # Systemd unit that runs first-boot.sh once, after network is online
    - path: /etc/systemd/system/podcast-ad-cutter-first-boot.service
      mode: 0644
      contents:
        inline: |
          [Unit]
          Description=Podcast Ad Cutter - First Boot Setup
          After=network-online.target
          Wants=network-online.target
          ConditionPathExists=!/var/lib/podcast-ad-cutter/.setup-done

          [Service]
          Type=oneshot
          ExecStart=/usr/local/bin/podcast-ad-cutter-first-boot
          RemainAfterExit=yes

          [Install]
          WantedBy=multi-user.target

# ── Systemd units enabled on first boot ──────────────────────────────────────
systemd:
  units:
    # Runs first-boot.sh once after network is online
    - name: podcast-ad-cutter-first-boot.service
      enabled: true

    # Ships with Fedora CoreOS — checks ghcr.io daily for new image digests
    # and restarts containers whose image has changed (requires AutoUpdate=registry
    # in the .container file)
    - name: podman-auto-update.timer
      enabled: true
```

**Step 2: Verify the Butane version spec**

Fedora CoreOS 40+ uses Butane schema `fcos` version `1.5.0`. Confirm on the Butane releases
page (https://github.com/coreos/butane/releases) that your installed `butane` supports it:

```bash
butane --version
```

Expected: `butane 0.21.0` or later.

**Step 3: Commit**

```bash
git add deployment/butane.yaml.template
git commit -m "feat(deployment): add Butane template for CoreOS first-boot config"
```

---

## Task 8: Write `generate.sh`

**Files:**
- Create: `deployment/generate.sh`

**Context:** The operator runs this script once on their workstation before provisioning the
CoreOS server. It:
1. Sources `deployment/env`
2. Substitutes template placeholders
3. Copies all referenced files into a temp staging directory
4. Runs `butane` to produce `deployment/ignition.json`

**Step 1: Write `deployment/generate.sh`**

```bash
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
   (( FEED_CHECK_INTERVAL_HOURS < 1 || FEED_CHECK_INTERVAL_HOURS > 23 )); then
    echo "ERROR: FEED_CHECK_INTERVAL_HOURS must be an integer between 1 and 23."
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
sed "s|{{INTERVAL_HOURS}}|${FEED_CHECK_INTERVAL_HOURS}|g" \
    "${SCRIPT_DIR}/podcast-ad-cutter.timer.template" \
    > "${STAGING}/podcast-ad-cutter.timer"

# Copy first-boot script (no substitution — it reads secrets at runtime)
cp "${SCRIPT_DIR}/first-boot.sh" "${STAGING}/first-boot.sh"

# Copy operator's config.yaml from project root
cp "${PROJECT_ROOT}/config.yaml" "${STAGING}/config.yaml"

# Render butane template: substitute ${VAR} placeholders.
# The explicit variable list prevents envsubst from corrupting other $ chars.
BUTANE_VARS='${SSH_PUBLIC_KEY}${GITHUB_USERNAME}${GHCR_TOKEN}'\
'${ANTHROPIC_API_KEY}${OPENAI_API_KEY}${GROQ_API_KEY}'\
'${OPENROUTER_API_KEY}${GEMINI_API_KEY}'

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
```

**Step 2: Make it executable**

```bash
chmod +x deployment/generate.sh
```

**Step 3: Check with shellcheck**

```bash
shellcheck deployment/generate.sh
```

Expected: no output.

**Step 4: Commit**

```bash
git add deployment/generate.sh
git commit -m "feat(deployment): add generate.sh — produces ignition.json from templates"
```

---

## Task 9: Integration test — generate and validate `ignition.json`

**Goal:** Verify that `generate.sh` runs end-to-end and produces a valid Ignition JSON file
that `butane --strict` accepts.

**Step 1: Create a test env file with non-sensitive placeholder values**

```bash
cat > /tmp/podcast-ad-cutter-test-env <<'EOF'
GITHUB_USERNAME=testuser
GHCR_TOKEN=ghp_testtoken
SSH_PUBLIC_KEY=ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyForValidationOnly test@example.com
FEED_CHECK_INTERVAL_HOURS=6
ANTHROPIC_API_KEY=sk-ant-test
OPENAI_API_KEY=sk-test
GROQ_API_KEY=gsk_test
OPENROUTER_API_KEY=sk-or-test
GEMINI_API_KEY=
EOF

cp /tmp/podcast-ad-cutter-test-env deployment/env
```

**Step 2: Run generate.sh**

```bash
./deployment/generate.sh
```

Expected output ends with:
```
==> ignition.json generated at deployment/ignition.json
```

**Step 3: Verify ignition.json is valid JSON**

```bash
python3 -m json.tool deployment/ignition.json > /dev/null && echo "Valid JSON"
```

Expected: `Valid JSON`

**Step 4: Verify key fields are present in ignition.json**

```bash
python3 -c "
import json
ig = json.load(open('deployment/ignition.json'))
files = {f['path'] for f in ig['storage']['files']}
expected = {
    '/etc/podcast-ad-cutter/config.yaml',
    '/etc/podcast-ad-cutter/secrets.env',
    '/etc/containers/systemd/podcast-ad-cutter.container',
    '/etc/containers/systemd/podcast-ad-cutter.timer',
    '/usr/local/bin/podcast-ad-cutter-first-boot',
    '/etc/systemd/system/podcast-ad-cutter-first-boot.service',
}
missing = expected - files
assert not missing, f'Missing files: {missing}'

units = {u['name'] for u in ig['systemd']['units']}
assert 'podcast-ad-cutter-first-boot.service' in units
assert 'podman-auto-update.timer' in units

users = ig['passwd']['users']
assert any(u['name'] == 'core' for u in users)

print('All assertions passed.')
"
```

Expected: `All assertions passed.`

**Step 5: Verify placeholder substitution worked in secrets.env**

```bash
python3 -c "
import json, base64
ig = json.load(open('deployment/ignition.json'))
secrets_file = next(f for f in ig['storage']['files']
                    if f['path'] == '/etc/podcast-ad-cutter/secrets.env')
content = base64.b64decode(secrets_file['contents']['source'].split(',')[1]).decode()
assert 'testuser' in content, 'GITHUB_USERNAME not substituted'
assert '{{' not in content, 'Unsubstituted placeholder found'
print('Substitution OK.')
print(content)
"
```

Expected: `Substitution OK.` followed by the secrets.env content with real values (no `{{...}}` placeholders).

**Step 6: Verify the timer interval was substituted**

```bash
python3 -c "
import json, base64
ig = json.load(open('deployment/ignition.json'))
timer_file = next(f for f in ig['storage']['files']
                  if f['path'] == '/etc/containers/systemd/podcast-ad-cutter.timer')
content = base64.b64decode(timer_file['contents']['source'].split(',')[1]).decode()
assert '0/6:00:00' in content, 'INTERVAL_HOURS not substituted in timer'
assert '{{' not in content, 'Unsubstituted placeholder found'
print('Timer substitution OK.')
"
```

Expected: `Timer substitution OK.`

**Step 7: Clean up and remove test env file**

```bash
rm deployment/env deployment/ignition.json
```

**Step 8: Final commit**

```bash
git add deployment/generate.sh  # executable bit
git commit -m "test(deployment): validate generate.sh produces correct ignition.json"
```

---

## Post-Deployment Reference

### First provisioning

```bash
# 1. Fill in your values
cp deployment/env.example deployment/env
$EDITOR deployment/env

# 2. Generate ignition.json
./deployment/generate.sh

# 3. Boot CoreOS with the ignition file (example: bare metal)
sudo coreos-installer install /dev/sda \
    --ignition-file deployment/ignition.json

# 4. After boot, verify setup
ssh core@<server-ip>
systemctl status podcast-ad-cutter-first-boot.service
journalctl -u podcast-ad-cutter.service -f
```

### Ongoing operations

```bash
# Check feed timer schedule and last/next firing
systemctl status podcast-ad-cutter.timer

# Follow live pipeline logs
journalctl -u podcast-ad-cutter.service -f

# Check auto-update status
systemctl status podman-auto-update.timer

# Trigger an image update immediately (without waiting for daily timer)
sudo podman auto-update

# Access output audio files
sudo podman volume inspect podcast-ad-cutter-output
```

### Updating config.yaml or feed list

The config.yaml on the server (`/etc/podcast-ad-cutter/config.yaml`) is managed directly on
the host — not rebuilt into the image. To update it:

```bash
scp config.yaml core@<server-ip>:/etc/podcast-ad-cutter/config.yaml
# Changes take effect on the next feed check (no restart needed)
```

### Re-provisioning / changing frequency

Edit `deployment/env`, run `./deployment/generate.sh`, re-provision with the new `ignition.json`.
Or SSH in and manually edit `/etc/containers/systemd/podcast-ad-cutter.timer` then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart podcast-ad-cutter.timer
```
