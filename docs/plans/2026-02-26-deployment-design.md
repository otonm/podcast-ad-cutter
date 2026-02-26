# Deployment Design: Podman on Fedora CoreOS with Ignition

**Date:** 2026-02-26
**Status:** Approved

---

## Overview

Deploy the podcast-ad-cutter as an OCI container on a Fedora CoreOS server. The server is
provisioned on first boot via an Ignition file (generated from Butane). The container image
is built by GitHub Actions on every push to `main` and pushed to the GitHub Container Registry
(`ghcr.io`). The CoreOS host uses `podman auto-update` to pull new image digests automatically.
Feed checks run on a configurable interval via a Quadlet-managed systemd timer.

---

## Delivery Flow

```
dev pushes to main
      ↓
GitHub Actions builds OCI image → ghcr.io/<owner>/podcast-ad-cutter:latest
      ↓
(first time) operator runs generate.sh → ignition.json
      ↓
CoreOS boots with ignition.json
      ↓
Ignition writes files + first-boot.service fires:
  - podman login ghcr.io
  - podman secret create (API keys)
  - podman pull latest image
  - systemctl daemon-reload  (Quadlet generates podcast-ad-cutter.service)
  - systemctl enable --now podcast-ad-cutter.timer
  - shred secrets.env
      ↓
podcast-ad-cutter.timer fires every FEED_CHECK_INTERVAL_HOURS
podcast-ad-cutter.service runs one-shot container → exits
      ↓
podman-auto-update.timer (daily, ships with CoreOS) pulls new image digest → restarts service
```

---

## File Structure

```
project root
├── Containerfile                                  # Multi-stage OCI image build
├── .github/
│   └── workflows/
│       └── build-push.yml                         # CI: build + push to ghcr.io on push to main
└── deployment/
    ├── .gitignore                                 # Ignores: env, ignition.json, generated files
    ├── env.example                                # All required vars to fill in
    ├── generate.sh                                # Run locally to produce ignition.json
    ├── butane.yaml.template                       # Human-readable CoreOS first-boot config
    ├── podcast-ad-cutter.container.template       # Quadlet container unit template
    ├── podcast-ad-cutter.timer.template           # Timer template ({{INTERVAL_HOURS}})
    └── first-boot.sh                              # First-boot script embedded in Ignition
```

---

## Section 1: Container Image (`Containerfile`)

Multi-stage build:

- **Stage 1 — builder:** `python:3.12-slim` + uv. Copies `pyproject.toml` and `uv.lock`,
  runs `uv sync --frozen --no-dev` to populate `.venv`, then copies all source.
- **Stage 2 — runtime:** Fresh `python:3.12-slim`. Installs `ffmpeg` via apt. Copies `.venv`
  and source from builder. Sets `PATH` to include `.venv/bin`. Sets `ENTRYPOINT` to
  `["python", "main.py"]` with no default `CMD` so Quadlet's `Exec=` appends the runtime flags.

Final image contains no build tools (no uv, no gcc, no pip).

---

## Section 2: GitHub Actions (`build-push.yml`)

- **Trigger:** push to `main`
- **Permissions:** `contents: read`, `packages: write` (uses `GITHUB_TOKEN` — no PAT required)
- **Steps:**
  1. `actions/checkout@v4`
  2. `docker/login-action@v3` → `ghcr.io` with `github.actor` / `GITHUB_TOKEN`
  3. `docker/setup-buildx-action@v3`
  4. `docker/build-push-action@v6` — context `.`, push `true`,
     tag `ghcr.io/${{ github.repository_owner }}/podcast-ad-cutter:latest`,
     cache `type=gha` (fast rebuilds on unchanged layers)

---

## Section 3: Quadlet Units

### `podcast-ad-cutter.container.template`

| Field | Value |
|---|---|
| `Image` | `ghcr.io/{{GITHUB_USERNAME}}/podcast-ad-cutter:latest` |
| `AutoUpdate` | `registry` — enables `podman auto-update` |
| `Volume` (x2) | `podcast-ad-cutter-data:/app/data:Z`, `podcast-ad-cutter-output:/app/output:Z` |
| `Volume` (config) | `/etc/podcast-ad-cutter/config.yaml:/etc/podcast-ad-cutter/config.yaml:ro,Z` |
| `Secret` (x5) | `anthropic_api_key`, `openai_api_key`, `groq_api_key`, `openrouter_api_key`, `gemini_api_key` — all `type=env`. Unused keys are created empty so the unit file stays static. |
| `Exec` | `--use-cache --config /etc/podcast-ad-cutter/config.yaml` |
| `[Service] Type` | `oneshot` — runs, finishes, exits; timer re-triggers next interval |
| `[Service] Restart` | `no` — failed runs are not retried immediately; next timer firing handles it |

### `podcast-ad-cutter.timer.template`

| Field | Value |
|---|---|
| `OnBootSec` | `5min` — fires shortly after boot to catch any missed run |
| `OnCalendar` | `*-*-* 0/{{INTERVAL_HOURS}}:00:00` — every N hours |
| `Persistent` | `true` — fires immediately on boot if a run was missed while server was off |

---

## Section 4: Ignition / Butane

### What `butane.yaml.template` writes on first boot

| File | Host path | Notes |
|---|---|---|
| SSH public key | `~core/.ssh/authorized_keys` | From `SSH_PUBLIC_KEY` var |
| `config.yaml` | `/etc/podcast-ad-cutter/config.yaml` | Operator's feed/model config |
| Rendered `.container` | `/etc/containers/systemd/podcast-ad-cutter.container` | `{{GITHUB_USERNAME}}` substituted |
| Rendered `.timer` | `/etc/containers/systemd/podcast-ad-cutter.timer` | `{{INTERVAL_HOURS}}` substituted |
| `secrets.env` | `/etc/podcast-ad-cutter/secrets.env` | Mode `0600`; shredded after first boot |
| `first-boot.sh` | `/usr/local/bin/podcast-ad-cutter-first-boot` | Mode `0755` |
| `first-boot.service` | `/etc/systemd/system/podcast-ad-cutter-first-boot.service` | Oneshot, enabled |

Ignition also enables `podman-auto-update.timer` (ships with CoreOS) directly via the
`systemd.units` section.

### `first-boot.sh` — runs once via `podcast-ad-cutter-first-boot.service`

Guard: `ConditionPathExists=!/var/lib/podcast-ad-cutter/.setup-done` — never runs again if
sentinel file exists.

Steps:
1. `source /etc/podcast-ad-cutter/secrets.env`
2. `podman login ghcr.io -u $GITHUB_USERNAME --password-stdin <<< $GHCR_TOKEN`
3. `podman secret create` for each API key (all five; empty string if unused)
4. `podman pull ghcr.io/$GITHUB_USERNAME/podcast-ad-cutter:latest`
5. `systemctl daemon-reload` — causes Quadlet generator to produce `podcast-ad-cutter.service`
6. `systemctl enable --now podcast-ad-cutter.timer`
7. `shred -u /etc/podcast-ad-cutter/secrets.env`
8. `mkdir -p /var/lib/podcast-ad-cutter && touch /var/lib/podcast-ad-cutter/.setup-done`

---

## Section 5: `generate.sh` — Operator Workflow

Run once on the operator's workstation. Requires `butane` CLI installed
(`brew install butane` / `dnf install butane`).

```
env.example  →  fill in  →  env  (gitignored)
                                  ↓
                           generate.sh:
                           1. source env
                           2. validate required vars
                           3. envsubst .container.template → podcast-ad-cutter.container
                           4. envsubst .timer.template     → podcast-ad-cutter.timer
                           5. envsubst butane.yaml.template → butane.yaml
                              (butane.yaml uses `local:` refs to inline generated files)
                           6. butane --pretty --strict \
                                     --files-dir deployment/ \
                                     butane.yaml > ignition.json
                           7. print warning: keep ignition.json private
```

### Required variables in `env`

| Variable | Description |
|---|---|
| `GITHUB_USERNAME` | GitHub account that owns the package |
| `GHCR_TOKEN` | GitHub PAT with `read:packages` scope (for pulling private images) |
| `SSH_PUBLIC_KEY` | Full public key string for the `core` user |
| `FEED_CHECK_INTERVAL_HOURS` | Integer — how often to check feeds (e.g. `6`) |
| `ANTHROPIC_API_KEY` | Optional — leave empty if unused |
| `OPENAI_API_KEY` | Optional — leave empty if unused |
| `GROQ_API_KEY` | Optional — leave empty if unused |
| `OPENROUTER_API_KEY` | Optional — leave empty if unused |
| `GEMINI_API_KEY` | Optional — leave empty if unused |

### `deployment/.gitignore`

```
env
ignition.json
butane.yaml
podcast-ad-cutter.container
podcast-ad-cutter.timer
```

---

## Persistent Storage

| Named volume | Container path | Contents |
|---|---|---|
| `podcast-ad-cutter-data` | `/app/data` | SQLite database (`podcasts.db`) |
| `podcast-ad-cutter-output` | `/app/output` | Clean audio files |

Volumes are created automatically by Podman on first container run. They survive image updates.

---

## Auto-Update Behaviour

`podman-auto-update.timer` (enabled by Ignition, runs daily by default on CoreOS) queries
`ghcr.io` for a new digest on `podcast-ad-cutter:latest`. If a newer digest is found, Podman
pulls it and restarts `podcast-ad-cutter.service`. The next timer firing of
`podcast-ad-cutter.timer` then uses the updated image.

To trigger an update immediately on the server:
```bash
sudo podman auto-update
```

---

## Useful Commands on the CoreOS Host

```bash
# Check feed timer schedule and last/next run
systemctl status podcast-ad-cutter.timer

# Follow live logs from the pipeline
journalctl -u podcast-ad-cutter.service -f

# Check auto-update status
systemctl status podman-auto-update.timer
sudo podman auto-update --dry-run

# Inspect output files
sudo podman volume inspect podcast-ad-cutter-output
```
