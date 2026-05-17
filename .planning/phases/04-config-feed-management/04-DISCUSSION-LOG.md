# Phase 4: Config & Feed Management - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-17
**Phase:** 4-Config-Feed-Management
**Areas discussed:** Settings response scope, PATCH merge strategy, Feed slug contract, GET /settings consistency

---

## Settings Response Scope

| Option | Description | Selected |
|--------|-------------|----------|
| AppConfig only | Return only what's in config.yaml — no credential info | |
| AppConfig + credential presence indicators | AppConfig plus credentials section showing "set"/"not set" per provider | ✓ |
| AppConfig + redacted credential values | AppConfig plus credentials with values replaced by "****" | |

**User's choice:** AppConfig + credential presence indicators

---

| Option | Description | Selected |
|--------|-------------|----------|
| Only the separate credentials section | AppConfig has no actual secrets; no AppConfig fields need redaction | ✓ |
| Treat base_url and URL fields as sensitive | Also redact infrastructure-revealing fields | |
| You decide | Implementation detail | |

**User's choice:** Only the separate credentials section needs redaction treatment

---

| Option | Description | Selected |
|--------|-------------|----------|
| No — allow PATCH during run | Changes apply on next run anyway; blocking would be surprising | ✓ |
| Yes — block with 409 during run | Consistent with Phase 3 episode control pattern | |

**User's choice:** PATCH /settings not blocked during active runs

---

## PATCH Merge Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Deep merge — partial nested updates | Client sends only changed fields; server deep-merges | ✓ |
| Shallow merge — top-level key replace | Client sends full sub-object per top-level key | |
| Full replace — PUT semantics | Client sends entire config | |

**User's choice:** Deep merge

---

| Option | Description | Selected |
|--------|-------------|----------|
| Feeds excluded from PATCH /settings | Feed CRUD reserved for /api/v1/feeds | ✓ |
| Full config patchable including feeds | PATCH /settings can overwrite feeds list | |

**User's choice:** Feeds excluded from PATCH /settings

---

| Option | Description | Selected |
|--------|-------------|----------|
| 422 Unprocessable Entity | Pydantic extra='forbid' rejects unknown keys | ✓ |
| Silently ignore unknown keys | Discard unknown keys, only merge known ones | |

**User's choice:** 422 for unknown keys

---

## Feed Slug Contract

| Option | Description | Selected |
|--------|-------------|----------|
| Derive slug from title on the fly | slugify(feed.title) — same as FeedPublisher | ✓ |
| Add explicit slug field to FeedConfig | Stable slug even if title changes | |

**User's choice:** Derive on the fly from title

---

| Option | Description | Selected |
|--------|-------------|----------|
| DB count — episodes stored for this feed | COUNT(*) WHERE podcast = feed.title | ✓ |
| config episodes_to_keep value | Return configured limit, not actual stored count | |

**User's choice:** DB count

---

| Option | Description | Selected |
|--------|-------------|----------|
| title + url required, rest optional | enabled defaults true, episodes_to_keep uses FeedConfig default | ✓ |
| Full FeedConfig object required | Client must send all fields | |

**User's choice:** title + url required, rest optional with defaults

---

| Option | Description | Selected |
|--------|-------------|----------|
| url, enabled, episodes_to_keep patchable | Title excluded — renaming breaks slug + DB linkage | ✓ |
| All FeedConfig fields including title | Allow renaming feeds | |

**User's choice:** url, enabled, episodes_to_keep only

---

## GET /settings Consistency

| Option | Description | Selected |
|--------|-------------|----------|
| Re-read from disk | GET always re-reads config.yaml; reflects PATCH immediately | ✓ |
| Return in-memory Config | Returns Config loaded at server startup | |

**User's choice:** Re-read from disk on every request

---

| Option | Description | Selected |
|--------|-------------|----------|
| No — keep it simple | No pending_restart field | ✓ |
| Yes — include pending_restart: true | Flag when disk config differs from in-memory | |

**User's choice:** No pending_restart field

---

## Claude's Discretion

- Route file organization: `api/routes/settings.py` and `api/routes/feeds.py`
- Temp file naming for atomic config write
- Per-request DB connection strategy for episode count
- PATCH success response shape (200 with resource vs 204 No Content)

## Deferred Ideas

None — discussion stayed within phase scope.
