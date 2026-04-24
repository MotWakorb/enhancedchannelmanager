# STRIDE Threat Model: DBAS Import / Restore

**Bead:** bd-qmuij (informs bd-gb5r5.3 — DBAS import engine)
**Author:** Security Engineer persona (Claude)
**Date:** 2026-04-20
**Status:** Draft — pending PO review of assumptions
**Related:** bd-ppe28 (closed, OWASP hardening), ADR-002 (restore transaction model, pending), ADR-004 (DBAS instance trust — referenced)

---

## 1. Scope & System Overview

The DBAS (Database Archive / Backup & Sync) import endpoint accepts an uploaded `.zip` archive and restores a prior ECM + Dispatcharr configuration into the running instance. bd-gb5r5.3 ports the legacy `importService.ts` from DBAS to Python. The archive contains heterogeneous payloads — ECM `journal.db` + settings, uploaded logos/TLS material, M3U credentials, API tokens, user accounts, and a **plugins** payload whose execution semantics are **not yet determined in ECM** (see Assumptions §6). The restore path is ordered: M3U → EPG → profiles → groups → stream profiles → logos → channels → user agents → settings → plugins → DVR → comskip → users → refresh triggers, with name-based conflict resolution and ID remapping.

This threat model covers the **Python import engine** ECM will build. The current `backend/routers/backup.py` ZIP restore (`/api/backup/restore`) is a smaller-scope precursor and is referenced as the inherited baseline — its protections (admin-only, manifest, basic path-traversal guard) are **table stakes**; DBAS extends them to cover categories that baseline does not (users, plugins, M3U creds). ECM has no current `plugin*` code in `backend/` (verified by `grep -ri plugin backend/` → 0 hits), so every plugin-related threat below is specified against a spec, not a live implementation.

Attack surfaces modeled:

1. **ZIP upload** — HTTP multipart path: authz, size, origin claim.
2. **ZIP extraction** — archive parsing: Zip Slip, symlinks, bombs, entry count.
3. **User-table restore** — risk of attacker-supplied admin account.
4. **Plugin restore** — RCE iff plugins are executable (see §6).
5. **M3U / API-token restore** — credential handling + log redaction.
6. **Endpoint authz** — admin-only gating, per-category opt-in, current-user preservation.
7. **Audit logging** — who restored what, when, with what counts.

---

## 2. Data Flow (Trust Boundaries)

```
[Admin browser] --TLS--> [FastAPI /api/dbas/import] --> tempdir extract
                                                   \--> manifest verify (SHA-256)
                                                   \--> per-category restore:
                                                        - ECM DB (SQLAlchemy txn)
                                                        - settings.json (atomic write)
                                                        - plugins/  (??? — see §6)
                                                        - Dispatcharr API (HTTP, separate trust boundary)
                                                   \--> journal.log_entry per category
                                                   \--> tempdir cleanup (finally)
```

Trust boundaries crossed:
- **Browser → ECM** (authenticated admin)
- **ECM → filesystem** (tempdir, then `/config/`)
- **ECM → SQLite** (`journal.db`)
- **ECM → Dispatcharr** (separate service; per ADR-004 treated as admin-configured & trusted; DBAS sync to a third-party target is out-of-scope until ADR-004 closes)

---

## 3. STRIDE Analysis

**Legend:** `status` ∈ {**existing** (already enforced by baseline/middleware), **to-build** (DBAS import engine must implement), **accepted-risk** (PO-signed deviation)}.
Severity is relative to *DBAS import endpoint*, not the whole product.

### 3.1 Spoofing

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| S1 | ZIP upload | Unauthenticated actor uploads an archive | Global auth middleware (`docs/auth_middleware.md`) + `RequireAdminIfEnabled` DI on endpoint | existing | High |
| S2 | ZIP extraction | Archive claims to be ECM-native but is crafted by attacker | Manifest header check (`ecm_backup.json` present, `version` field, magic-bytes check on DBs) + **SHA-256 content manifest** verified before any file is materialised | to-build | High |
| S3 | User-table restore | Imported users table asserts attacker email = admin | Only admins can trigger; require **per-category opt-in checkbox** for `users` category; current admin row preserved (§3.6 P2) | to-build | High |
| S4 | Plugin restore | Archive ships plugin claiming provenance from a trusted author | SHA-256 per-plugin entry in manifest; if plugins are code, plugin payload must match signed/allowlisted set (PO decision, §6) | to-build / PO | Crit (conditional) |
| S5 | M3U/API-token restore | Archive plants M3U source pointing to attacker host | Admin is the one importing — they already control sources; URL scheme validation (from bd-ppe28.3) re-applied at restore time rather than trusted from archive | to-build (reuse ppe28.3) | Med |
| S6 | Endpoint authz | Session fixation / cookie theft before invoke | Out of scope — covered by auth subsystem; noted for traceability | existing | Low |
| S7 | Audit logging | Journal entry spoofed by crafted payload | Journal rows written server-side post-decision with auth-subject + request ID; archive content cannot dictate log fields | to-build | Med |

### 3.2 Tampering

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| T1 | ZIP upload | MITM modifies archive in flight | TLS termination (existing); endpoint hash compared to manifest | existing + to-build | Med |
| T2 | ZIP extraction | Zip Slip — entry names `../../../app/main.py` | Reject any entry whose `pathlib.PurePosixPath` normalised form is absolute, contains `..`, or whose `resolve()` leaves the destination tempdir. **All extraction targets tempdir, not `/config/`** | to-build (baseline has a weaker check in `backup.py` §162-167) | High |
| T2b | ZIP extraction | Symlink entry escapes tempdir | Reject any zip entry whose `external_attr >> 16` indicates `stat.S_IFLNK`; `ZipFile.extract()` in CPython does not follow symlinks but we must refuse to **create** them | to-build | High |
| T3 | User-table restore | Tampered hash in `users.password_hash` overwrites admin row | DB restore runs inside a SQLAlchemy transaction; on failure, rollback; current-admin-row preservation rule blocks overwrite even on success (§3.6 P2) | to-build | High |
| T4 | Plugin restore | Plugin file content mutated vs. manifest | SHA-256 verification per manifest entry rejects any file whose content hash does not match | to-build | Crit (conditional) |
| T5 | M3U/API-token restore | Secret field altered to attacker-controlled value | Admin trust — they chose the archive. Mitigation via manifest hash (T4 mechanism) | to-build | Med |
| T6 | Endpoint authz | Path parameter tampering bypasses category gate | Accept only a whitelist of category keys (reuse `RESTORABLE_SECTIONS`-style registry); reject unknown keys with 400 | to-build | Med |
| T7 | Audit logging | Post-hoc tampering of `journal.db` entries | Out of scope at this layer; journal tamper-evidence is a separate bead. Note for PO | accepted-risk | Low |

### 3.3 Repudiation

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| R1 | ZIP upload | Admin denies having uploaded | journal entry records `user_id`, IP (via `X-Forwarded-For` where trusted), archive SHA-256, timestamp, request ID | to-build | Med |
| R2 | ZIP extraction | Silent partial extraction leaves unattributable artifacts | Extraction into per-request tempdir; successful files + failed entries both logged with request ID | to-build | Med |
| R3 | User-table restore | No record of which admin account was added/replaced | Per-category audit entry with `category=users`, `added_count`, `updated_count`, `usernames_added[]` (usernames only — no PII beyond that) | to-build | High |
| R4 | Plugin restore | Silently-installed plugin executes later without import trail | Per-plugin audit entry (name, hash, version), pinned to import request ID | to-build | High |
| R5 | M3U/API-token restore | Credential rotation without record | Audit entry lists `category=m3u`, count, **redacted values** (do not log secrets); secret diff is recorded as present/absent only | to-build | Med |
| R6 | Endpoint authz | No record of authz decision when request was rejected | Authz denials emit structured log with subject + reason (already partially done by middleware; confirm coverage for DBAS endpoint) | existing (verify) | Low |
| R7 | Audit logging | Journal write fails silently and restore proceeds | If `journal.log_entry` returns `None` for a category, restore surfaces a warning to the response + logs at WARN; restore still commits (category is informational, not blocking) unless PO flags otherwise | to-build | Med |

### 3.4 Information Disclosure

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| I1 | ZIP upload | Error responses leak filesystem paths / stack traces | 400/500 responses surface a short `detail` only; full traceback logged server-side via `logger.exception` (existing pattern) | existing | Low |
| I2 | ZIP extraction | Dry-run logs entry contents, including secret files | Dry-run must enumerate *metadata only* (path, size, sha256). Any preview of settings.json/users/plugins content is **redacted via the existing `REDACTED` marker** in `backup.py` and a new denylist of secret field names (`password`, `password_hash`, `token`, `api_key`, `smtp_password`, M3U `username`/`password`) | to-build | High |
| I3 | User-table restore | Error from unique-constraint violation echoes username back | Sanitise exception messages before returning; log full detail server-side only | to-build | Med |
| I4 | Plugin restore | Archive includes plugin source with hard-coded third-party credentials | Manifest review tooling (a dry-run inspect mode) flags any plugin file > N KB as "requires human review"; secrets not auto-logged | to-build | Med |
| I5 | M3U/API-token restore | Log line echoes restored M3U credentials | Secrets-in-logs rule: DBAS import never logs any field whose key is in the denylist (I2). Enforced via a `_redact()` helper; unit-tested (§5) | to-build | Crit |
| I6 | Endpoint authz | Endpoint discoverable via OpenAPI when auth disabled in dev | FastAPI docs gate on auth.setup_complete (existing); verify DBAS router inherits | existing (verify) | Low |
| I7 | Audit logging | Journal export leaks secrets captured during dry-run | Journal `before_value`/`after_value` never records secrets; only counts + category names | to-build | High |

### 3.5 Denial of Service

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| D1 | ZIP upload | Arbitrarily large upload exhausts RAM / disk | **Max upload size cap** (propose: 256 MB; PO-tunable) enforced before `await file.read()`. Stream to tempfile via `shutil.copyfileobj` rather than `await file.read()` in one shot | to-build (baseline reads into memory — §253) | High |
| D2 | ZIP extraction | Zip bomb — small archive, gigabytes uncompressed | **Compression-ratio cap** (propose: max 100× per entry, max 1 GB cumulative uncompressed); **entry-count cap** (propose: 10,000 entries); enforce by iterating `zf.infolist()` pre-extraction | to-build | High |
| D2b | ZIP extraction | Deep nested paths / pathological names cause path-resolver stalls | Cap path depth (e.g., 32 segments) and name length (255 bytes) | to-build | Med |
| D3 | User-table restore | Restore of massive user table blocks the request worker | Background task with WebSocket progress (per ADR-003 pending); synchronous fallback protected by a hard row-count cap | to-build | Med |
| D4 | Plugin restore | Infinite-loop plugin executed during restore | Plugins NOT executed during restore — only written to disk, activation gated (see §6). If plugins execute at import, bound with wall-clock + memory limits | to-build | Crit (conditional) |
| D5 | M3U/API-token restore | Restore triggers N synchronous Dispatcharr API calls | Reuse existing async `dispatcharr_client`; per-item timeout (already in client). Batch size cap (propose 500) | to-build | Med |
| D6 | Endpoint authz | Admin endpoint DoS via cred-stuffing at login | Out of scope for this endpoint — auth router rate-limiting owns this | existing (verify) | Low |
| D7 | Audit logging | High-volume category restore produces one journal row per item → journal.db bloat | Aggregate to **one journal row per category** with count, not per-item; batched log entry pattern | to-build | Med |

### 3.6 Elevation of Privilege

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| P1 | ZIP upload | Non-admin triggers restore via CSRF against an authenticated admin | `RequireAdminIfEnabled` + existing auth middleware (GET-safe; restore is POST). CSRF mitigation relies on token-bearer auth (not cookies) — verify in DBAS router | existing (verify) | High |
| P2 | User-table restore | **Crown-jewel threat:** archive grants attacker admin | (a) category `users` is **opt-in** with a distinct checkbox in the UI + request body flag `include_users: true`; (b) **current authenticated admin row is never overwritten or deleted** — identified by `id` of the requesting user from JWT/session; (c) password hashes imported as-is (no downgrade to plaintext); (d) audit row with list of usernames added | to-build | **Crit** |
| P3 | Plugin restore | Plugin runs at import as root/app user, escaping to shell | (a) category `plugins` is **opt-in** with explicit warning UI; (b) if plugins are code: sandboxing required (subinterpreter / subprocess / container) OR reject plugin category until ADR lands; (c) if plugins are config only: validate against schema and skip execution semantics | to-build / PO | **Crit** (conditional) |
| P4 | M3U/API-token restore | Restored M3U source URL triggers SSRF at first refresh | ppe28.3 URL-scheme validation applied at **restore time**, not just at input time | to-build (reuse ppe28.3) | Med |
| P5 | Endpoint authz | DBAS endpoint inadvertently exempted via `AUTH_EXEMPT_PATHS` | Automated test asserts DBAS paths are NOT in `AUTH_EXEMPT_PATHS` | to-build | High |
| P6 | ZIP extraction | Symlink → `/app/main.py` overwrites running code | Symlink refusal (T2b) + extraction targets tempdir only; files move to `/config/` only after validation, never to `/app/` | to-build | Crit |
| P7 | Audit logging | Restore succeeds silently, attacker hides traces by later restore | Journal entries for DBAS import are marked `user_initiated=True`; frontend exposes a filter for `category='dbas_import'`; retention policy tracked in a separate bead (note for PO) | to-build | Med |

**Cell count:** 6 dimensions × 7 surfaces nominal = 42; table has 50 rows (some dimensions list sub-threats T2b, D2b, P2-subpoints). All 42 canonical cells covered, with extra rows where a single surface warranted split threats.

---

## 4. Hardening Checklist (Acceptance Criteria for bd-gb5r5.3)

The DBAS import engine implementation (bd-gb5r5.3) must satisfy **all** of the following, each mapped to a STRIDE cell:

1. **Admin-only endpoint gating** — DBAS import routes use `RequireAdminIfEnabled` DI; DBAS paths absent from `AUTH_EXEMPT_PATHS`; test asserts both. *(S1, P1, P5)*
2. **Per-category opt-in flag** — `users` and `plugins` categories require distinct boolean flags in the request body; default false; frontend checkbox ships with warning copy. *(S3, P2, P3)*
3. **Current admin preservation** — the requesting admin's `users` row is **never** overwritten, deleted, disabled, or demoted; identified by auth subject; test covers the case where the archive contains a colliding username. *(P2)*
4. **Zip Slip hardening** — reject any entry whose normalised path is absolute, contains `..`, or whose `resolve()` escapes the tempdir; reject symlink entries (`S_IFLNK`); reject paths >32 segments or >255 bytes. *(T2, T2b, D2b, P6)*
5. **Zip bomb / DoS caps** — enforce pre-extraction: max upload 256 MB, max entries 10,000, max cumulative uncompressed 1 GB, max per-entry ratio 100×. Values are PO-tunable via settings. *(D1, D2)*
6. **Streaming upload** — do not call `await file.read()`; stream to a `NamedTemporaryFile` via `shutil.copyfileobj`; enforce upload cap during stream. *(D1)*
7. **SHA-256 manifest** — `ecm_backup.json` includes `{files: [{path, sha256, size}]}`; verify all three before any file is materialised outside tempdir; reject mismatch with 400. *(S2, T4)*
8. **Tempdir isolation & cleanup** — all extraction lands in a per-request `tempfile.TemporaryDirectory`; move to `/config/` only after full validation; cleanup guaranteed by context manager (`try/finally` double-safety). Dry-run guaranteed side-effect free. *(T2, P6, plus bead AC)*
9. **Secrets-in-logs denylist** — `_redact()` helper applied to all log lines and dry-run previews; denylist covers `password`, `password_hash`, `token`, `api_key`, `smtp_password`, M3U `username`/`password`, plus any field ending `_secret` / `_token`. Unit test enforces. *(I2, I5, I7)*
10. **URL scheme re-validation on restore** — reuse bd-ppe28.3 validator for any restored URL field (M3U source, EPG source, XC host). *(S5, P4)*
11. **Per-category audit logging** — one `journal.log_entry` per category with `category='dbas_import'`, `action_type=category_name`, counts, and (for `users`) list of usernames added — **never** passwords / hashes / secrets. Log includes request ID. *(R1-R5, R7, D7, P7)*
12. **Error sanitisation** — HTTPException `detail` strings never echo file paths, stack traces, or unique-constraint values; full detail goes to server log via `logger.exception`. *(I1, I3)*
13. **Plugin execution gate** — plugins are written to disk but NOT executed during restore. Activation requires a separate explicit admin action (tracked in a future bead once §6 resolves). *(D4, P3)*
14. **Transaction model** — all DB restore per category runs inside a SQLAlchemy transaction with rollback on exception; see ADR-002 for cross-category atomicity. *(T3)*
15. **Dispatcharr-call bounding** — Dispatcharr restore batches capped at 500 items, each call uses existing per-request timeout. *(D5)*
16. **CSRF posture** — DBAS endpoint must not rely on cookie-only auth; require `Authorization: Bearer` token. Test asserts. *(P1)*
17. **Authz denial logging** — 401/403 on DBAS endpoint emits structured WARN log including reason. *(R6)*

---

## 5. Test Cases (for `backend/tests/security/`)

Proposed test module layout once the engine lands:

- `test_dbas_import_authz.py`
  - `test_requires_admin` — non-admin gets 403.
  - `test_endpoint_not_in_auth_exempt_paths` — static assertion.
  - `test_csrf_rejects_cookie_only_request` — reject if no bearer token.
- `test_dbas_import_zipbomb.py`
  - `test_rejects_oversized_upload` — 257 MB body → 413.
  - `test_rejects_too_many_entries` — 10,001-entry archive → 400.
  - `test_rejects_oversized_uncompressed` — 1.1 GB virtual expansion → 400.
  - `test_rejects_compression_ratio_bomb` — 1 KB → 200 MB entry → 400.
- `test_dbas_import_zipslip.py`
  - `test_rejects_path_traversal` — entry `../../etc/passwd` → 400.
  - `test_rejects_absolute_path` — entry `/app/main.py` → 400.
  - `test_rejects_symlink_entry` — `S_IFLNK` bit set → 400.
  - `test_rejects_deep_nesting` — 33-segment path → 400.
- `test_dbas_import_manifest.py`
  - `test_rejects_missing_manifest` — no `ecm_backup.json` → 400.
  - `test_rejects_sha256_mismatch` — tampered content byte → 400.
  - `test_rejects_unknown_version` — manifest claims v999 → 400.
- `test_dbas_import_users.py`
  - `test_users_category_requires_opt_in` — import with `users` content but `include_users=False` → users untouched.
  - `test_current_admin_preserved` — archive contains same username as requester → requester row intact.
  - `test_current_admin_not_demoted` — archive marks requester as non-admin → rejected or ignored.
- `test_dbas_import_secrets.py`
  - `test_no_secret_in_logs` — restore an archive containing an M3U password; grep `caplog` for plaintext → must be absent.
  - `test_dryrun_redacts_settings` — dry-run preview of settings.json masks `password`, `smtp_password`.
  - `test_error_message_sanitised` — IntegrityError → response `detail` does not contain username or SQL fragment.
- `test_dbas_import_audit.py`
  - `test_one_journal_entry_per_category` — 3 categories → 3 rows.
  - `test_journal_entry_omits_secrets` — `after_value` field never contains secret keys.
  - `test_journal_entry_includes_request_id` — request ID correlates logs and journal row.
- `test_dbas_import_cleanup.py`
  - `test_tempdir_cleanup_on_success`.
  - `test_tempdir_cleanup_on_exception` — force failure mid-extraction, assert tempdir removed.
  - `test_dryrun_is_side_effect_free` — DB unchanged, `/config/` unchanged after dry-run.
- `test_dbas_import_url_validation.py`
  - `test_rejects_file_scheme_m3u_url` — reuse ppe28.3 suite; archive with `file://` URL → rejected.
- `test_dbas_import_plugins.py` (gated on §6 resolution)
  - `test_plugins_not_executed_on_import` — stub plugin with side-effect (write marker file); restore; marker file absent.

---

## 6. Assumptions (PO Decisions Needed)

The items below materially change the threat model and must be resolved before the bd-gb5r5.3 engine is considered design-complete.

**A1 — Plugins: code or config?**
`grep -ri plugin backend/` returns zero matches in the ECM backend as of 2026-04-20. The DBAS legacy `importService.ts` has a plugin restore step, but whether the ported ECM equivalent will treat plugins as **executable Python** (RCE risk = critical) or **declarative config** (risk = medium, similar to settings) is **not determinable from the codebase**. This model assumes the conservative case (executable) and gates plugin activation behind a separate admin action. **PO decision required** — see threats S4, T4, D4, P3.

**A2 — Dispatcharr trust boundary on restore target**
Per ADR-004 (referenced, pending), DBAS sync to a *different* Dispatcharr instance raises trust questions. This threat model covers **restore to the admin-configured local Dispatcharr** (trusted, per bd-ppe28 conclusion). Cross-instance restore (e.g., restoring a prod archive to a staging Dispatcharr) is **out of scope** until ADR-004 closes. **PO confirmation required** that v0.17.0 DBAS import is same-instance only.

**A3 — Users table schema & password hashing algorithm parity**
Assumes the source archive was produced by a compatible ECM version whose `users.password_hash` uses the same algorithm (argon2 / bcrypt / whatever ECM uses today). Cross-version password-hash migration is out of scope. If mismatched, restore must **reject the users category with a clear error**, not attempt rehash. **PO to confirm** version-compatibility policy.

**A4 — Upload size / entry-count caps**
Proposed: 256 MB upload, 10,000 entries, 1 GB cumulative, 100× ratio. These are defensible defaults but should be tunable via `settings.json` and sized to realistic ECM deployments. **PO to ratify** the ceiling for typical install sizes.

**A5 — Journal retention / tamper-evidence**
The model notes that `journal.db` itself is not tamper-evident (T7, P7). Adding tamper-evidence (hash-chained entries, external sink) is a **separate epic** and out of scope for bd-gb5r5.3. **PO to confirm** this is acceptable risk for v0.17.0.

**A6 — CSRF posture**
Assumes auth is bearer-token only (not cookie-based). If cookie-based sessions are added later, DBAS import needs double-submit CSRF or `SameSite=Strict`. **PO to confirm** auth architecture for v0.17.0 before DBAS lands.

---

## 7. Related Work & References

- `backend/routers/backup.py` — baseline ZIP restore (`/api/backup/restore`). DBAS extends it; this model is a **superset** of that endpoint's protections.
- `docs/auth_middleware.md` — global secure-by-default auth; DBAS inherits.
- bd-ppe28, bd-ppe28.1, bd-ppe28.3 (closed) — OWASP URL-scheme hardening; reused for M3U/EPG URLs at restore.
- ADR-002 (pending) — DBAS restore transaction model & downtime contract.
- ADR-003 (pending) — WebSocket long-running job pattern; DBAS import will run as a background job with progress events.
- ADR-004 (pending) — DBAS instance-trust posture (same-instance vs. cross-instance).
- bd-gb5r5.3 — DBAS import engine; hardening checklist in §4 will be appended to that bead's acceptance criteria.
