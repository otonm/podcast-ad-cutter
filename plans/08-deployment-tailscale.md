# 08 — Deployment with Host Tailscale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Podcast clients reach the feeds and episodes publicly through Tailscale Funnel.
- The GUI is reachable only from the tailnet, through Tailscale Serve, on the same hostname.

**Architecture:** Host-installed Tailscale. No sidecar, no extra proxy.
- `tailscale funnel` serves `./output` on 443.
- `tailscale serve --https=8443` proxies to the GUI container on `127.0.0.1:8090`.

**Tech Stack:** Tailscale CLI on the host, Docker Compose.

**Spec:** [plans/README.md](README.md) — Architecture (exposure).

**Depends on:**
- Plan 06 (`./output` bind mount, backend with no published ports).
- Plan 07 (GUI container on `127.0.0.1:8090`).

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan). Additionally:
- Tailscale docs say Funnel only listens on 443, 8443 or 10000, while Serve may use any port.
- The same port can't be used for both Serve and Funnel.
- The docs don't say whether Funnel on 443 and Serve on 8443 can run on one node at the same time. Task 1 verifies this.

## Review Focus

1. **GUI reachable from the public internet:** must fail from an off-tailnet device. Verified in Task 1, Step 4. This is the most important check.
2. **Funnel's directory listing:** the root of the Funnel URL lists every feed and episode. Accepted (the files are public via RSS anyway). If unwanted, funnel a localhost file server instead of the folder.
3. **Existing subscribers:** the feed URL changes, so podcast apps must resubscribe to `https://<host>.<tailnet>.ts.net/<feed-slug>.rss`. The episode URLs inside the feed correct themselves on the next run (`components/pipeline.py:538-543`).
4. **Funnel config after a reboot:** with `--bg` it persists. Verify after a reboot.
5. **Host permissions:** `tailscaled` (root) must be able to read `./output`. The container writes the files as uid 1000; root reads them regardless.

---

### Task 1: Expose output publicly and the GUI to the tailnet

**Files:**
- Modify: `config/config.yaml` on the server (not in git): `base_url: https://<host>.<tailnet>.ts.net`
- Modify: `README.md` (new "Deployment with Tailscale" section: the commands below, the `base_url` rule, the resubscribe note)

**Interfaces:**
- Produces:
  - Public: `https://<host>.<tailnet>.ts.net/<feed-slug>.rss` and `https://<host>.<tailnet>.ts.net/<feed-slug>/<DD.MM.YYYY>-<title-slug>.<ext>`
  - Tailnet-only: `https://<host>.<tailnet>.ts.net:8443/` (the GUI)

- [ ] **Step 0: Re-check the current CLI syntax**

  Check the Tailscale docs: `tailscale.com/docs/reference/tailscale-cli/funnel` and `tailscale.com/kb/1242/tailscale-serve`.

- [ ] **Step 1: Set `base_url` and start the stack**

  Run: `docker compose up -d`. Then trigger a run from the GUI, or wait for the startup run.

  Expected: the `.rss` enclosures in `./output/<slug>.rss` start with the new `base_url`.

- [ ] **Step 2: Funnel the output folder**

  Run: `sudo tailscale funnel --bg "$(pwd)/output" && tailscale funnel status`

  Expected: the status shows `https://<host>.<tailnet>.ts.net` (Funnel on), serving the absolute output path.

- [ ] **Step 3: Serve the GUI to the tailnet**

  Run: `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090 && tailscale serve status`

  Expected: `:8443` proxies to `http://127.0.0.1:8090` (tailnet only). If the command errors because Funnel and Serve conflict on one node, stop and report back. The fallback is a second hostname (a sidecar for the GUI only), which needs a decision.

- [ ] **Step 4: Verify exposure from outside the tailnet** (phone on mobile data, Tailscale off)

  - [ ] `https://<host>.<tailnet>.ts.net/<slug>.rss` loads and its enclosure URLs play.
  - [ ] `https://<host>.<tailnet>.ts.net:8443/` **fails** (connection refused / timeout).
  - [ ] `https://<host>.<tailnet>.ts.net/api/v1/health` returns 404. The Funnel serves files only, so the API is unreachable.

- [ ] **Step 5: Verify from inside the tailnet**

  - [ ] `https://<host>.<tailnet>.ts.net:8443/` shows the GUI dashboard, and live events stream during a run (this checks Serve doesn't buffer SSE).

- [ ] **Step 6: Resubscribe**

  Add the new feed URL in your podcast app(s), then remove the old subscription.

- [ ] **Step 7: Commit the docs**

```bash
git add README.md
git commit -m "docs: deployment with Tailscale funnel (feeds) and serve (GUI)"
```
