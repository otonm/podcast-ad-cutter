# 06 — Docker: Config Directory, Output Bind Mount, Serve-Mode Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In server mode the container can save settings, exposes `output/` at a stable host path, and still runs the pipeline on a schedule.

**Architecture:**
- Config is mounted as a **directory**, read-write. Atomic `os.replace()` cannot swap a single-file bind mount, and `:ro` blocks writes entirely.
- Output becomes a bind mount, so host Tailscale can funnel it.
- In serve mode, `entrypoint.sh` starts `supercronic` in the background with a crontab that `curl`s `POST /api/v1/run`, plus one startup run once `/health` answers. Then it `exec`s the server, so the server is PID 1 and a crash restarts the container.

**Tech Stack:** POSIX sh, supercronic 0.2.33, curl (already in the image), Docker Compose.

**Spec:** [plans/README.md](README.md) — Architecture (exposure) and G2/G3.

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan). Additionally:
- No Python changes in this plan. Shell has no pytest coverage; verification is the scripted container checks below.
- The crontab targets `http://127.0.0.1:${APP_PORT:-8080}`. This requires the server to listen on loopback, which the default `APP_HOST` (`0.0.0.0`) does.

## Review Focus

1. **A cron tick while a run is still active:** the API returns 409, `curl -f` exits non-zero, and supercronic logs an error. Harmless; the next tick retries. Verified in Task 2, Step 4.
2. **`APP_HOST` set to a non-loopback address** (e.g. a specific IP): scheduled runs silently fail. Document this in the compose comments.
3. **The API never comes up:** the startup-run loop keeps waiting in the background, and the container restarts via the server process anyway. Accepted.
4. **Settings saved from the GUI:** must land in `./config/config.yaml` on the host and survive a container restart. Verified in Task 1, Step 3.
5. **Ownership of `./output` and `./config` on the host:** `entrypoint.sh` already `chown`s `/output` and `/config` to `app` (uid 1000). Make sure host Tailscale (root) can read `./output`. Verified in plan 08.

---

### Task 1: Compose — config directory and output bind mount

**Files:**
- Modify: `docker-compose.example.yml`
- Modify: `README.md` (Docker section: config now lives in `./config/config.yaml`; output in `./output`)

**Interfaces:**
- Produces:
  - Container paths are unchanged (`/config/config.yaml`, `/output`), so `run.sh` and `config.yaml` `paths.*` need no change.
  - Host layout: `./config/config.yaml` and `./output/`.

- [ ] **Step 1: Edit `docker-compose.example.yml`**

  - Replace `./config.yaml:/config/config.yaml:ro` with `./config:/config`.
  - Replace the named `output` volume with `./output:/output`, and remove `output:` from the top-level `volumes:`.
  - Rewrite the header comments:
    - step 1 becomes "copy config.example.yaml → config/config.yaml";
    - in MODES, server mode now also runs `CRON_SCHEDULE`;
    - drop "CRON_SCHEDULE is ignored in server mode".
  - Leave the backend `ports:` commented. The GUI container (plan 07) is the only published port.

- [ ] **Step 2: Update the `README.md` Docker section with the same layout**

- [ ] **Step 3: Verify settings writes in a container**

  Run:

```bash
docker build -t pac:test .
mkdir -p /tmp/pac/config /tmp/pac/output && cp config.example.yaml /tmp/pac/config/config.yaml
# set paths.* in /tmp/pac/config/config.yaml to /output /cache /data /logs
docker run -d --name pac -e APP_SERVE=true -e GROQ_API_KEY=x \
  -v /tmp/pac/config:/config -v /tmp/pac/output:/output pac:test
docker exec pac curl -fsS -X PATCH -H 'Content-Type: application/json' \
  -d '{"ad_detection":{"min_confidence":0.55}}' http://127.0.0.1:8080/api/v1/settings
grep min_confidence /tmp/pac/config/config.yaml
```

  Expected: the PATCH returns 200 JSON, and `grep` shows `min_confidence: 0.55`. (With the old single-file `:ro` mount, the PATCH fails.)

- [ ] **Step 4: Commit**

```bash
git add docker-compose.example.yml README.md
git commit -m "fix(docker): mount config as a writable directory and output as bind mount"
```

### Task 2: Serve-mode scheduler and startup run

**Files:**
- Modify: `entrypoint.sh` (the `if [ -n "${APP_SERVE:-}" ]` branch)

**Interfaces:**
- Consumes: `POST /api/v1/run` (202, or 409 if already running) and `GET /api/v1/health`.
- Produces: in serve mode, the same schedule semantics as cron mode (one run at startup, then every `CRON_SCHEDULE`).

- [ ] **Step 0: Check docs**

  Confirm the supercronic `-passthrough-logs` flag and crontab format against its README, the same as the current cron branch.

- [ ] **Step 1: Implement**

  Replace the serve branch with:

```sh
if [ -n "${APP_SERVE:-}" ]; then
    echo "entrypoint: server mode, schedule=${CRON_SCHEDULE}"
    API="http://127.0.0.1:${APP_PORT:-8080}/api/v1"
    printf '%s curl -fsS -X POST %s/run\n' "${CRON_SCHEDULE}" "${API}" > /tmp/crontab
    gosu app /usr/local/bin/supercronic -passthrough-logs /tmp/crontab &
    # Startup run, mirroring cron mode; waits until the API answers.
    ( until curl -fsS "${API}/health" >/dev/null 2>&1; do sleep 1; done
      curl -fsS -X POST "${API}/run" || true ) &
    exec gosu app /bin/sh /app/run.sh
fi
```

- [ ] **Step 2: Syntax check**

  Run: `sh -n entrypoint.sh`

  Expected: no output, exit 0.

- [ ] **Step 3: Verify the startup run**

  Run:

```bash
docker build -t pac:test . && docker rm -f pac 2>/dev/null
docker run -d --name pac -e APP_SERVE=true -e APP_DEBUG=true -e GROQ_API_KEY=x -e CRON_SCHEDULE='* * * * *' \
  -v /tmp/pac/config:/config -v /tmp/pac/output:/output pac:test
sleep 5 && docker logs pac 2>&1 | grep -E 'server mode|POST /api/v1/run'
```

  Expected: "entrypoint: server mode, schedule=* * * * *", and an `aiohttp.access` log line (visible because `APP_DEBUG=true`) for `POST /api/v1/run` with status 202.

- [ ] **Step 4: Verify the cron tick**

  Run: `sleep 70 && docker logs pac 2>&1 | grep -c 'POST /api/v1/run'`

  Expected: ≥ 2. The tick may show 409 if the startup run is still active; that's acceptable.

- [ ] **Step 5: Update the compose comments and commit**

  Update the `docker-compose.example.yml` comment for `CRON_SCHEDULE`: "applies in both modes". Note that `APP_HOST` must include loopback for scheduled runs in server mode.

```bash
git add entrypoint.sh docker-compose.example.yml
git commit -m "feat(docker): schedule pipeline runs in server mode via supercronic + API"
```
