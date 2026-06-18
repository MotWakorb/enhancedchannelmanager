# STRIDE Threat Model: DBAS Import / Restore

**Bead:** bd-qmuij (informs bd-gb5r5.3 — DBAS import engine); §8–§9 addenda + checklist 18–26: `enhancedchannelmanager-0i2vt.3` (Phase 0, v0.18.0 DBAS absorption)
**Author:** Security Engineer persona (Claude)
**Date:** 2026-04-20 · **Addenda A & B added:** 2026-05-12 · **Re-pointed at ADR-012, lifted to Accepted, Addendum C added:** 2026-06-17
**Status:** Accepted — assumptions (§6) and the Addendum A residual (§8.4) resolved by PO; cross-instance scope corrected to ADR-012 D11; passphrase encryption covered by Addendum C (ADR-012 D12)
**Related:** bd-ppe28 (closed, OWASP hardening), ADR-002 (restore transaction model, pending), ADR-004 (DBAS instance trust — referenced), [ADR-012](../adr/ADR-012-dbas-absorption-approach.md) (DBAS absorption — source of truth), epic `enhancedchannelmanager-0i2vt` (DBAS absorption), beads `0i2vt.4` (Fernet credential models) / `0i2vt.5` (SSRF wizard) / `0i2vt.7` (ZIP builder) / `0i2vt.8` (cloud upload) / `u81kh` + `0zrse` (whole-artifact passphrase encryption — Addendum C) / `l1p4p` + `tsfv0` (users importer + Dispatcharr user-API spike — §3.6 P2 / §6 A3)

---

## 1. Scope & System Overview

The DBAS (Database Archive / Backup & Sync) import endpoint accepts an uploaded `.zip` archive and restores a prior ECM + Dispatcharr configuration into the running instance. bd-gb5r5.3 ports the legacy `importService.ts` from DBAS to Python. The archive contains heterogeneous payloads — ECM `journal.db` + settings, uploaded logos/TLS material, M3U credentials, API tokens, and user accounts. The restore path is ordered: M3U → EPG → profiles → groups → stream profiles → logos → channels → user agents → settings → DVR → comskip → users → refresh triggers, with name-based conflict resolution and ID remapping.

> **Plugins EXCLUDED from v0.18.0 (ADR-012 D10).** The original DBAS restore path included a
> **plugins** payload whose execution semantics were never determined in ECM (`grep -ri plugin
> backend/` → 0 hits). ADR-012 D10 (PO, 2026-06-16) **excludes the plugins category from v0.18.0
> backup/restore entirely** — it sidesteps the unresolved RCE-on-restore question and unblocks the
> rest of the bulk importer (`0i2vt.13` drops the plugins category). Consequently, every
> plugin-conditional threat in this model (S4, T4, D4, P3) is **moot / deferred for v0.18.0** and
> retained only as a forward-looking record for the release that revisits plugin semantics. The
> former plugin step is removed from the restore order above.

This threat model covers the **Python import engine** ECM will build. The current `backend/routers/backup.py` ZIP restore (`/api/backup/restore`) is a smaller-scope precursor and is referenced as the inherited baseline — its protections (admin-only, manifest, basic path-traversal guard) are **table stakes**; DBAS extends them to cover categories that baseline does not (users, M3U creds). ECM has no current `plugin*` code in `backend/` (verified by `grep -ri plugin backend/` → 0 hits); rather than specify the plugin threat against an undetermined spec, ADR-012 D10 **excludes plugins from v0.18.0** — so the plugin-related rows below (S4, T4, D4, P3) are retained as deferred records, not v0.18.0 acceptance criteria.

Attack surfaces modeled:

1. **ZIP upload** — HTTP multipart path: authz, size, origin claim.
2. **ZIP extraction** — archive parsing: Zip Slip, symlinks, bombs, entry count.
3. **User-table restore** — risk of attacker-supplied admin account.
4. **Plugin restore** — RCE iff plugins are executable. **EXCLUDED from v0.18.0 per ADR-012 D10** — surface retained for traceability only; the conditional rows below are moot/deferred.
5. **M3U / API-token restore** — credential handling + log redaction.
6. **Endpoint authz** — admin-only gating, per-category opt-in, current-user preservation.
7. **Audit logging** — who restored what, when, with what counts.

---

## 2. Data Flow (Trust Boundaries)

```
[Admin browser] --TLS--> [FastAPI /api/dbas/import] --> tempdir extract
                                                   \--> [optional] passphrase decrypt (Addendum C)
                                                   \--> manifest verify (SHA-256)
                                                   \--> per-category restore:
                                                        - ECM DB (SQLAlchemy txn)
                                                        - settings.json (atomic write)
                                                        - Dispatcharr API (HTTP, separate trust boundary)
                                                   \--> journal.log_entry per category
                                                   \--> tempdir cleanup (finally)
```

(`plugins/` is **not** restored in v0.18.0 — ADR-012 D10; the former plugin step is removed.)

Trust boundaries crossed:
- **Browser → ECM** (authenticated admin)
- **ECM → filesystem** (tempdir, then `/config/`)
- **ECM → SQLite** (`journal.db`)
- **ECM → Dispatcharr** (separate service; per ADR-004 treated as admin-configured & trusted)

**Archive provenance — trusted operator input, always-on safety guards (ADR-012 D11).** The
restored archive is treated as **trusted operator input** — the same trust ECM extends to an
operator typing configuration directly into the UI. This is the correct posture for a self-hosted,
single-operator LAN tool: full untrusted-archive provenance/signature checking (archive signing,
a trust store, supply-chain attestation) is **deliberately out of scope** — it is overkill for this
deployment model, and that is the **decided posture**, not an unresolved gap. **Trusted does not
mean unvalidated, however:** a set of always-on safety validations applies to *every* archive
**regardless of source** — including the cross-instance migration case (back up instance A, restore
onto instance B), which ADR-012 D11 puts squarely **IN scope** for v0.18.0:
- **SSRF denylist on every restored URL** (M3U/EPG/XC hosts) — see §3.6 P4 + Addendum B; the
  validator does not trust a URL just because it arrived in an operator's archive.
- **Schema / `schema_version` validation** before any file is materialised (ADR-012 D1; checklist 7).
- **Never restore a foreign admin that locks out the current operator** — current-operator
  preservation keyed off the **auth subject** (not username/id, which a cross-instance archive
  remaps), and conservative privilege-flag restore — see §3.6 P2.

The earlier framing of this model (cross-instance restore *out of scope*; see the superseded §6 A2)
predates ADR-012 D11 and is corrected here and in §6 A2.

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
| S4 | Plugin restore | Archive ships plugin claiming provenance from a trusted author | SHA-256 per-plugin entry in manifest; if plugins are code, plugin payload must match signed/allowlisted set | **moot / deferred (ADR-012 D10 — plugins excluded from v0.18.0)** | Crit (conditional) |
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
| T4 | Plugin restore | Plugin file content mutated vs. manifest | SHA-256 verification per manifest entry rejects any file whose content hash does not match | **moot / deferred (ADR-012 D10 — plugins excluded from v0.18.0)** | Crit (conditional) |
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
| D4 | Plugin restore | Infinite-loop plugin executed during restore | Plugins NOT executed during restore — only written to disk, activation gated. If plugins execute at import, bound with wall-clock + memory limits | **moot / deferred (ADR-012 D10 — plugins excluded from v0.18.0)** | Crit (conditional) |
| D5 | M3U/API-token restore | Restore triggers N synchronous Dispatcharr API calls | Reuse existing async `dispatcharr_client`; per-item timeout (already in client). Batch size cap (propose 500) | to-build | Med |
| D6 | Endpoint authz | Admin endpoint DoS via cred-stuffing at login | Out of scope for this endpoint — auth router rate-limiting owns this | existing (verify) | Low |
| D7 | Audit logging | High-volume category restore produces one journal row per item → journal.db bloat | Aggregate to **one journal row per category** with count, not per-item; batched log entry pattern | to-build | Med |

### 3.6 Elevation of Privilege

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| P1 | ZIP upload | Non-admin triggers restore via CSRF against an authenticated admin | `RequireAdminIfEnabled` + existing auth middleware (GET-safe; restore is POST). CSRF mitigation relies on token-bearer auth (not cookies) — verify in DBAS router | existing (verify) | High |
| P2 | User-table restore | **Crown-jewel threat:** archive grants attacker admin / privilege-escalation via crafted user rows | (a) category `users` is **opt-in** with a distinct checkbox in the UI + request body flag `include_users: true`; (b) **current authenticated admin row is never overwritten, deleted, disabled, or demoted** — identified by **auth subject** of the requesting user (NOT username/`id`, which a cross-instance archive remaps); (c) **no password is transported** — Dispatcharr's user API exposes `password` only as a write-only plaintext field (no pre-computed-hash API; source hash never retrievable — spike `tsfv0` vs 0.26.0), so each restored user is **created with no usable password + force-reset**; ECM never fabricates, derives, or rehashes a password; (d) **the real escalation surface is the WRITABLE privilege flags** `is_superuser` / `is_staff` / `user_level` — restore them **conservatively** (default non-privileged; never trust the archive's superuser bit for an account the operator did not already control); (e) audit row with list of usernames only — never passwords/hashes | to-build | **Crit** |
| P3 | Plugin restore | Plugin runs at import as root/app user, escaping to shell | (a) category `plugins` is **opt-in** with explicit warning UI; (b) if plugins are code: sandboxing required (subinterpreter / subprocess / container) OR reject plugin category until ADR lands; (c) if plugins are config only: validate against schema and skip execution semantics | **moot / deferred (ADR-012 D10 — plugins excluded from v0.18.0; the RCE surface is removed by exclusion)** | **Crit** (conditional) |
| P4 | M3U/API-token restore | Restored M3U source URL triggers SSRF at first refresh | ppe28.3 URL-scheme validation applied at **restore time**, not just at input time | to-build (reuse ppe28.3) | Med |
| P5 | Endpoint authz | DBAS endpoint inadvertently exempted via `AUTH_EXEMPT_PATHS` | Automated test asserts DBAS paths are NOT in `AUTH_EXEMPT_PATHS` | to-build | High |
| P6 | ZIP extraction | Symlink → `/app/main.py` overwrites running code | Symlink refusal (T2b) + extraction targets tempdir only; files move to `/config/` only after validation, never to `/app/` | to-build | Crit |
| P7 | Audit logging | Restore succeeds silently, attacker hides traces by later restore | Journal entries for DBAS import are marked `user_initiated=True`; frontend exposes a filter for `category='dbas_import'`; retention policy tracked in a separate bead (note for PO) | to-build | Med |

**Cell count:** 6 dimensions × 7 surfaces nominal = 42; table has 50 rows (some dimensions list sub-threats T2b, D2b, P2-subpoints). All 42 canonical cells covered, with extra rows where a single surface warranted split threats. **Note:** the four plugin-restore rows (S4, T4, D4, P3) are **moot / deferred for v0.18.0** (ADR-012 D10 — plugins excluded); they remain in the table for traceability and to seed the release that revisits plugin semantics.

---

## 4. Hardening Checklist (Acceptance Criteria for bd-gb5r5.3)

The DBAS import engine implementation (bd-gb5r5.3) must satisfy **all** of the following, each mapped to a STRIDE cell:

1. **Admin-only endpoint gating** — DBAS import routes use `RequireAdminIfEnabled` DI; DBAS paths absent from `AUTH_EXEMPT_PATHS`; test asserts both. *(S1, P1, P5)*
2. **Per-category opt-in flag** — the `users` category requires a distinct boolean flag in the request body; default false; frontend checkbox ships with warning copy. *(S3, P2)* *(The `plugins` category is excluded from v0.18.0 per ADR-012 D10, so no plugin opt-in flag ships in v0.18.0.)*
3. **Current admin preservation** — the requesting admin's `users` row is **never** overwritten, deleted, disabled, or demoted; identified by **auth subject** (not username/`id`, which a cross-instance archive remaps); test covers the case where the archive contains a colliding username. *(P2)*
   - **No password transported, conservative privilege flags** — every restored user is created **with no usable password + force-reset** (Dispatcharr exposes `password` write-only plaintext; no hash crosses the boundary — spike `tsfv0`); the WRITABLE `is_superuser`/`is_staff`/`user_level` flags are restored **conservatively** (default non-privileged; never trust the archive's superuser bit for an account the operator did not already control). Tests: colliding-username-does-not-touch-operator, archive-superuser-bit-not-trusted, no-password-set, force-reset-flagged. *(P2)*
4. **Zip Slip hardening** — reject any entry whose normalised path is absolute, contains `..`, or whose `resolve()` escapes the tempdir; reject symlink entries (`S_IFLNK`); reject paths >32 segments or >255 bytes. *(T2, T2b, D2b, P6)*
5. **Zip bomb / DoS caps** — enforce pre-extraction: max upload 256 MB, max entries 10,000, max cumulative uncompressed 1 GB, max per-entry ratio 100×. Values are PO-tunable via settings. *(D1, D2)*
6. **Streaming upload** — do not call `await file.read()`; stream to a `NamedTemporaryFile` via `shutil.copyfileobj`; enforce upload cap during stream. *(D1)*
7. **SHA-256 manifest** — `ecm_backup.json` includes `{files: [{path, sha256, size}]}`; verify all three before any file is materialised outside tempdir; reject mismatch with 400. *(S2, T4)*
8. **Tempdir isolation & cleanup** — all extraction lands in a per-request `tempfile.TemporaryDirectory`; move to `/config/` only after full validation; cleanup guaranteed by context manager (`try/finally` double-safety). Dry-run guaranteed side-effect free. *(T2, P6, plus bead AC)*
9. **Secrets-in-logs denylist** — `_redact()` helper applied to all log lines and dry-run previews; denylist covers `password`, `password_hash`, `token`, `api_key`, `smtp_password`, M3U `username`/`password`, plus any field ending `_secret` / `_token`. `password_hash` is in the denylist on the **export side** too (`_REDACT_KEYS` — see Addendum A / checklist 18): a password hash sitting in an unencrypted backup artifact is an **offline-cracking target**, so it is redacted (or carried only under whole-artifact passphrase encryption, Addendum C), never shipped in cleartext. Unit test enforces. *(I2, I5, I7)*
10. **URL scheme re-validation on restore** — reuse bd-ppe28.3 validator for any restored URL field (M3U source, EPG source, XC host). *(S5, P4)*
11. **Per-category audit logging** — one `journal.log_entry` per category with `category='dbas_import'`, `action_type=category_name`, counts, and (for `users`) list of usernames added — **never** passwords / hashes / secrets. Log includes request ID. *(R1-R5, R7, D7, P7)*
12. **Error sanitisation** — HTTPException `detail` strings never echo file paths, stack traces, or unique-constraint values; full detail goes to server log via `logger.exception`. *(I1, I3)*
13. **Plugin execution gate — N/A for v0.18.0.** Plugins are **excluded from v0.18.0** backup/restore (ADR-012 D10): the category is not imported at all, so there is no plugin payload to write, gate, or execute. This item is retained as the forward-looking acceptance criterion for the release that revisits plugin semantics: if/when plugins are restored, they must be written to disk but NOT executed during restore, with activation behind a separate explicit admin action. *(D4, P3 — both moot/deferred for v0.18.0)*
14. **Transaction model** — all DB restore per category runs inside a SQLAlchemy transaction with rollback on exception; see ADR-002 for cross-category atomicity. *(T3)*
15. **Dispatcharr-call bounding** — Dispatcharr restore batches capped at 500 items, each call uses existing per-request timeout. *(D5)*
16. **CSRF posture** — DBAS endpoint must not rely on cookie-only auth; require `Authorization: Bearer` token. Test asserts. *(P1)*
17. **Authz denial logging** — 401/403 on DBAS endpoint emits structured WARN log including reason. *(R6)*

### 4.1 Addendum checklist items (v0.18.0 DBAS absorption — Addenda A & B)

The v0.18.0 epic (`enhancedchannelmanager-0i2vt`, ADR-012) adds an **export/backup** path
and **outbound cloud destinations** that did not exist when items 1–17 were written. The
following items extend the checklist; they are acceptance criteria for the Phase-0 work
(`0i2vt.1`, `0i2vt.2`, `0i2vt.3`) and the Phase-1 work (`0i2vt.4`, `0i2vt.5`, `0i2vt.7`,
`0i2vt.8`). See Addendum A (§8) and Addendum B (§9) for the threat tables these map to.

18. **Export-artifact redaction parity (Addendum A)** — the v0.18.0 backup ZIP builder
    (`0i2vt.7`) MUST apply the same redaction the existing YAML/`settings.json` export path
    applies (`backend/routers/backup.py` → `REDACTED` marker + `_scrub_journal_db_to_temp` +
    `_gather_settings`): every credential-class key across all **13 Dispatcharr categories**
    (M3U account passwords/usernames, EPG source creds, XC host creds, core-settings SMTP
    password, plugin config secrets, user `password_hash`, DVR/comskip tokens, cloud-target
    tokens) is replaced with the `REDACTED` sentinel **or** stored encrypted (item 19) before
    the bytes enter the ZIP. The denylist is the single shared `_REDACT_KEYS`-style set used by
    both YAML and ZIP paths — no second, divergent list. Unit test: build a backup whose source
    state contains a known M3U password, an SMTP password, and a cloud token; assert none of the
    three plaintext values appear anywhere in the ZIP bytes (manifest, `settings.json`,
    `journal.db`, per-category YAML, binary subtree). *(A1, A2, A4 — Addendum A; closes Security
    Mandatory #4 + #6)*
19. **Encrypted-rather-than-redacted carve-out (Addendum A)** — where a backup is intended to
    be **restorable with credentials intact** (cross-instance migration), credential fields MAY
    be carried in ciphertext instead of redacted, but ONLY via the existing Fernet primitive
    (`backend/cloud_storage/crypto.py`, per ADR-012 D3) and ONLY for the `SyncTarget`/`CloudTarget`
    credential columns defined in `0i2vt.4`. The Fernet key is **never** placed in the ZIP. A
    backup taken on instance A and restored on instance B without the key MUST surface the
    credential fields as unreadable (decryption-failure → field treated as absent, restore
    continues with a WARN), never as plaintext and never as a hard crash. Test: restore a
    backup whose `CloudTarget.token_ciphertext` was encrypted under a different key → token field
    absent, restore proceeds. *(A3 — Addendum A; ties into ADR-012 D3)*
20. **Manifest covers redacted state (Addendum A)** — the ZIP `manifest` / `schema_version`
    block records SHA-256 over the **post-redaction** bytes (the bytes actually written), so
    integrity verification on restore validates what is present, not a pre-redaction phantom.
    The manifest itself is enumerated as metadata-only on dry-run (path/size/sha256), per item 8.
    *(A5 — Addendum A)*
21. **SSRF validator on ALL outbound URLs (Addendum B)** — every outbound HTTP(S) request the
    backup/sync subsystem makes — cloud-destination uploads (S3 endpoint URL, WebDAV base URL,
    OneDrive/Dropbox/GDrive API hosts and any user-overridable endpoint), `SyncTarget` Dispatcharr-B
    URL, and any user-supplied callback/webhook — passes through a shared SSRF validator BEFORE the
    connection is opened. The validator is the single chokepoint; no adapter (`s3_adapter.py`,
    `onedrive_adapter.py`, `dropbox_adapter.py`, `gdrive_adapter.py`, WebDAV) may issue a raw
    `httpx`/`requests` call that bypasses it. This is the Phase-1 deliverable in `0i2vt.5`/`0i2vt.8`;
    this checklist item is the contract. *(B1, B2, B4, B6 — Addendum B; ADR-012 D4)*
22. **Always-on denylist regardless of LAN-friendly choice (Addendum B)** — even when the
    first-run wizard (`0i2vt.5`) chose LAN-friendly mode, the validator ALWAYS rejects, with
    no opt-out: link-local `169.254.0.0/16` (incl. IMDS `169.254.169.254/32`), CGNAT
    `100.64.0.0/10`, `0.0.0.0/8`, IPv6 loopback `::1`, IPv6 ULA `fc00::/7`, IPv6 link-local
    `fe80::/10`, IPv6 site-local `fec0::/10`, IPv4-mapped-IPv6 `::ffff:0:0/96`, and any
    non-`http`/`https` scheme. `127.0.0.0/8` and RFC1918 ranges are rejected in public-only mode
    and allowed in LAN-friendly mode; everything in the always-on list is rejected in **both**.
    Test corpus: each denied range + an IPv4-mapped-IPv6 representation of the IMDS address + a
    `gopher://`/`file://`/`ftp://` scheme → all rejected in both modes. *(B2, B6 — Addendum B;
    ADR-012 D4)*
23. **DNS-rebinding mitigation: resolve-then-connect-by-IP (Addendum B)** — the validator
    resolves the destination hostname **once**, validates the returned address(es) against the
    denylist (and, if any A/AAAA record is denied, rejects the whole request — no "use the allowed
    one"), then the HTTP client connects **by that validated IP**, sending the original hostname
    only as SNI and `Host:` header. The window between validation and connect must not contain a
    second, unvalidated DNS lookup. Test: a hostname that returns two A records (one public, one
    `169.254.169.254`) → rejected; a hostname whose resolution is mocked to change between
    validation and connect → connection still goes to the validated IP. *(B3 — Addendum B; ADR-012 D4)*
24. **Redirect re-validation (Addendum B)** — 3xx responses are NOT auto-followed to a new host
    without re-running the full denylist + resolve-by-IP check on the redirect target; a redirect
    to a previously-unvalidated host is either blocked outright or only followed after a fresh
    validation pass. Cross-scheme downgrades (`https://` → `http://`) on redirect are rejected.
    Test: server replies `302` to `http://169.254.169.254/latest/meta-data/` → request fails, no
    connection to the IMDS host. *(B3, B6 — Addendum B; ADR-012 D4)*
25. **TLS-verify default + audited insecure flag (Addendum B)** — outbound requests use
    `verify=True` by default. A per-`CloudTarget`/`SyncTarget` `insecure=true` escape hatch MAY
    exist (self-signed WebDAV/MinIO are real deployments) but every outbound request made with
    `insecure=true` writes a `journal.log_entry` audit row (`category='backup_outbound'`,
    target id, host, `tls_verified=false`) — not just once at config time, on **every** request.
    Test: configure an `insecure=true` target, trigger a backup upload, assert an audit row with
    `tls_verified=false` exists for that request. *(B1, B5 — Addendum B; ADR-012 D4)*
26. **Outbound-credential freshness binding (Addendum B / cross-ref `0i2vt.4`)** — a scheduled
    backup/sync op that fires after the target's credentials were rotated or revoked MUST NOT use
    the stale token: the `CloudTarget`/`SyncTarget` model carries `credential_version` and
    `token_revoked_at`; the scheduler captures `credential_version` at enqueue time and the worker
    re-checks it at execution time, aborting (WARN + audit row) if it changed or if
    `token_revoked_at` is set. (This is Security Mandatory #5; the schema lands in `0i2vt.4`, the
    enforcement in `0i2vt.6`/`0i2vt.8`.) *(B5 — Addendum B)*

### 4.2 Addendum checklist items (whole-artifact passphrase encryption — Addendum C)

These extend the checklist for the opt-in whole-artifact passphrase-encryption path (ADR-012 D12,
bead `u81kh`, crypto design from spike `0zrse`). They are acceptance criteria for the `.7` ZIP-builder
encrypt stage and the Phase-2 decrypt-at-ingest gate. See Addendum C (§10) for the threat table.

27. **Opt-in, redact-by-default preserved (Addendum C)** — passphrase encryption is **opt-in**; the
    default backup remains redact-by-default (ADR-012 D1). Credentials are carried in the artifact
    **only** via an explicit operator "include credentials for migration" choice that **requires** a
    passphrase. There is no switch that ships unredacted credentials without a passphrase. *(C1, C2)*
28. **REDACT-THEN-ENCRYPT is structural (Addendum C)** — redaction runs **inside** the build path and
    cannot be skipped; `include_credentials` only re-injects the approved credential set before
    encryption. There is no "encrypt instead of redact, skipping redaction" code path. Test: a backup
    with `include_credentials=false` + a passphrase still contains no plaintext credentials after
    decryption. *(C2, C3)*
29. **KDF + AEAD construction (Addendum C, per spike `0zrse`)** — scrypt KDF with **N ≥ 2¹⁵** (floor),
    r=8, p=1; per-artifact random salt; KDF params + salt live in a **cleartext authenticated header**.
    Chunked streaming AEAD (ChaCha20-Poly1305 **or** AES-256-GCM); per-chunk nonce (random base XOR
    counter); each chunk's **AAD binds the header + chunk-index + is_final flag** so no chunk can be
    swapped, reordered, or the stream truncated. Min **12-char** passphrase, API-enforced. *(C3, C4)*
30. **Cleartext header with `format_version` separate from `schema_version` (Addendum C)** — the
    header carries `magic`, `format_version` (the *encryption-envelope* version, **distinct from** the
    backup `schema_version`), KDF params, salt, AEAD id, and chunk size, all authenticated. This lets a
    version check (`0i2vt.17`) read the envelope/schema metadata **before decrypting** and refuse an
    unsupported version without needing the passphrase. *(C4)*
31. **New primitive, parallel to Fernet — D3/D12 reconciliation (Addendum C)** — passphrase encryption
    is a **new crypto primitive** (`backend/cloud_storage/crypto.py`'s Fernet is static-key,
    whole-in-RAM, non-streaming and is **not** reused for this path). **ADR-012 D3 governs at-rest
    credential columns** (Fernet); **D12 governs the opt-in whole-artifact path** (this primitive).
    The two coexist; D12 *partially* supersedes D3 only for the whole-artifact path. *(C3)*
32. **Off-event-loop streaming (Addendum C)** — KDF and encrypt/decrypt run **off the event loop** and
    **stream to temp files** (not whole-artifact-in-RAM). The `.7` builder's in-memory `BytesIO`
    assembly becomes tempfile-streaming (needed for D8 regardless). *(C3)*
33. **No wrong-passphrase oracle — STRUCTURAL, not wall-clock (Addendum C)** — a wrong passphrase and a
    corrupted artifact MUST fail with an **identical exception** and release **zero plaintext** on any
    failure; never emit a verified prefix before the whole-artifact authentication completes. This is a
    **structural** property (identical exception + zero-plaintext-on-failure), **not** a timing
    guarantee: spike `0zrse` **demonstrated** a ~15 ms size-dependent timing residual (wrong passphrase
    fails at chunk 0; corrupt-last-chunk fails at chunk N), which is **ACCEPTED** for an offline
    artifact (see Addendum C residual). Do **not** write a wall-clock/stopwatch equivalence test (flaky
    and misleading); test the structural property instead. *(C5)*
34. **Lost passphrase = unrecoverable, hard-gate UX (Addendum C)** — a lost passphrase makes the
    artifact **permanently unrecoverable** (no recovery, no backdoor). The UI must surface this as a
    **hard gate** — an `acknowledge_unrecoverable` checkbox the operator must tick, not a tooltip —
    before an encrypted backup is produced. *(C6)*

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
  - `test_current_admin_preserved` — archive contains same username as requester (preservation keyed off **auth subject**) → requester row intact.
  - `test_current_admin_not_demoted` — archive marks requester as non-admin → rejected or ignored.
  - `test_no_password_transported` — restored user is created with **no usable password** + force-reset flag; archive password/hash fields are never applied.
  - `test_archive_superuser_bit_not_trusted` — archive marks a non-operator account `is_superuser=True` → restored conservatively as non-privileged.
  - `test_users_category_fails_closed_if_hash_field_appears` — startup capability check fails the `users` category closed if a `password_hash` write field appears on the Dispatcharr schema.
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
- `test_dbas_import_plugins.py` — **DEFERRED for v0.18.0** (plugins excluded, ADR-012 D10). Retained for the release that revisits plugins:
  - `test_plugins_not_executed_on_import` — stub plugin with side-effect (write marker file); restore; marker file absent.
- `test_dbas_passphrase_encryption.py` (Addendum C — opt-in passphrase path)
  - `test_redact_then_encrypt_no_plaintext` — `include_credentials=false` + passphrase; decrypt; no plaintext credential present (redaction not skipped inside encrypt).
  - `test_wrong_passphrase_and_corrupt_artifact_identical_exception` — wrong passphrase and a corrupted artifact raise the **same** exception type/message; **no** plaintext is released on either failure. (Structural, not wall-clock — no stopwatch assertion.)
  - `test_header_version_check_before_decrypt` — an unsupported `format_version` is rejected reading the **cleartext header**, without a passphrase.
  - `test_chunk_reorder_or_truncate_rejected` — swapping/reordering chunks or truncating the stream fails AEAD/AAD verification (no partial plaintext).
  - `test_min_passphrase_length_enforced` — an 11-char passphrase is rejected at the API boundary.
  - `test_lost_passphrase_unrecoverable_ack_required` — producing an encrypted backup requires the `acknowledge_unrecoverable` flag.

---

## 6. Assumptions (resolved)

The items below originally gated design-completeness. All are now **resolved** (the resolutions are
why this model is lifted from Draft to Accepted). Each is kept with its resolution recorded inline.

**A1 — Plugins: code or config? → RESOLVED: excluded from v0.18.0 (ADR-012 D10).**
`grep -ri plugin backend/` returns zero matches in the ECM backend, and whether a Dispatcharr
"plugin" is **executable Python** (RCE risk = critical) or **declarative config** remains
undetermined. Rather than gate the rest of the restore on that unknown, **ADR-012 D10 (PO,
2026-06-16) excludes the plugins category from v0.18.0 backup/restore entirely.** The RCE-on-restore
question is sidestepped, not answered; it is revisited in the release that understands plugin
semantics. Threats S4, T4, D4, P3 are therefore **moot / deferred for v0.18.0**.

**A2 — Cross-instance restore → RESOLVED: IN scope, trusted-input + always-on guards (ADR-012 D11).**
This previously asserted cross-instance restore was **out of scope** (same-instance only). **ADR-012
D11 (PO, 2026-06-16) puts cross-instance restore squarely IN scope for v0.18.0** — it is the epic's
headline value (back up instance A, restore onto instance B for migration / DR). The corrected
posture (see §2): the archive is **trusted operator input** — *no archive signing/provenance/trust
store* (deliberately overkill for a self-hosted single-operator LAN tool; this is the **decided
posture**, not a gap) — but **always-on safety validations apply regardless of source**: SSRF
denylist on every restored URL (§3.6 P4, Addendum B), `schema_version` validation (D1, checklist 7),
and current-operator preservation by **auth subject** so a foreign admin row can never lock out the
operator running the restore (§3.6 P2). ADR-004 is no longer the gating dependency it was framed as.

**A3 — Password-hash algorithm parity → RESOLVED: NON-ISSUE; no hash is ever transported (spike `tsfv0`).**
The earlier framing assumed the archive carried a `users.password_hash` whose algorithm had to match
the target, with "reject the users category on mismatch" as the control. **Spike `tsfv0` (live vs
Dispatcharr 0.26.0) makes this moot:** Dispatcharr's user API exposes `password` only as a
**write-only plaintext field** — there is **no pre-computed-hash API**, and the source hash is
**never retrievable** (GET never returns it). ECM's own auth uses **bcrypt**; Dispatcharr/Django uses
**pbkdf2_sha256** — the two are not interchangeable in either direction, but that no longer matters
because **no hash ever crosses the restore boundary.** The users importer therefore **never transports
a hash**: every restored Dispatcharr user is **created with no usable password + force-reset**, and ECM
**never fabricates, derives, or rehashes** a password. Hash-algorithm parity is a **non-issue** for
restore; the importer does not even parse an incoming hash field. (See §3.6 P2 clause (c). The real
crown-jewel surface is the writable privilege flags — clause (d) — not hash integrity.) A startup
capability check should fail the users category **closed** if a `password_hash` write field ever
appears on the Dispatcharr schema.

**A4 — Upload size / entry-count caps → RESOLVED (ratified defaults).**
256 MB upload, 10,000 entries, 1 GB cumulative, 100× per-entry ratio, tunable via `settings.json`.
Accepted as defensible defaults for typical ECM deployments. (Checklist 5.)

**A5 — Journal retention / tamper-evidence → RESOLVED (accepted residual).**
`journal.db` is not tamper-evident (T7, P7). Hash-chained / external-sink tamper-evidence is a
**separate epic**, out of scope here; this is **accepted risk** for v0.18.0.

**A6 — CSRF posture → RESOLVED.**
Auth is bearer-token only (not cookie-based); DBAS import requires `Authorization: Bearer` (checklist
16). If cookie-based sessions are ever added, DBAS import will need double-submit CSRF or
`SameSite=Strict` — tracked as a follow-on at that time.

---

## 7. Related Work & References

- `backend/routers/backup.py` — baseline ZIP restore (`/api/backup/restore`). DBAS extends it; this model is a **superset** of that endpoint's protections.
- `docs/auth_middleware.md` — global secure-by-default auth; DBAS inherits.
- bd-ppe28, bd-ppe28.1, bd-ppe28.3 (closed) — OWASP URL-scheme hardening; reused for M3U/EPG URLs at restore.
- ADR-002 (pending) — DBAS restore transaction model & downtime contract.
- ADR-003 (pending) — WebSocket long-running job pattern; DBAS import will run as a background job with progress events.
- ADR-004 (`docs/adr/ADR-004-release-cut-promotion-discipline.md`) — release-cut discipline; DBAS instance-trust posture is now resolved by **ADR-012 D11** (cross-instance IN scope; trusted-input + always-on guards), not deferred to ADR-004.
- [ADR-012](../adr/ADR-012-dbas-absorption-approach.md) — **source-of-truth ADR for DBAS absorption** (the D1–D12 decision table). The §3 STRIDE controls and Addenda A/B/C below are the security contract for the `0i2vt` child beads (`0i2vt.5`, `0i2vt.7`, `0i2vt.8`, `l1p4p`, `u81kh`, etc.); ADR-012 does not restate them — this document is authoritative for the controls.
- bd-gb5r5.3 — DBAS import engine; hardening checklist in §4 will be appended to that bead's acceptance criteria.

> **Note on bead lineage.** The 42-bead plan `bd-gb5r5` referenced in the §1–§7 body was retired
> 2026-04-21 and superseded by epic `enhancedchannelmanager-0i2vt` ("v0.18.0 DBAS absorption:
> Backup + Restore"). The source of truth for that epic is **ADR-012**
> (`docs/adr/ADR-012-dbas-absorption-approach.md`, Accepted). ADR-012's own preamble records the
> ADR number-history (the earlier phantom DBAS filename and the later number collision); that history
> is **not** duplicated here. All decision references in this document — D1–D12 — point at ADR-012's
> decision table. The
> §1–§7 body still uses `bd-gb5r5.3` for the import-engine bead id (not re-baselined), but its
> *decisions* are governed by ADR-012; the Addenda (A export-redaction, B outbound/SSRF, C
> passphrase encryption) and checklist items 18–26 are the v0.18.0-current layer and take precedence
> where they overlap.

---

## 8. Addendum A — Export Artifact Redaction (v0.18.0 backup ZIP)

**Added:** 2026-05-12 · **Bead:** `enhancedchannelmanager-0i2vt.3` (Phase 0) · **Feeds:** `0i2vt.4` (Fernet credential models), `0i2vt.7` (ZIP artifact builder) · **Closes:** "Security Mandatory #4 + #6"

### 8.1 Scope

The v0.18.0 backup feature produces an **export artifact** — a ZIP wrapping per-category YAML
plus a binary subtree (uploaded logos, TLS material), with a `manifest` block carrying
`schema_version`, per-file SHA-256, and sizes, across the **13 Dispatcharr config categories**
(M3U accounts, EPG sources, channel groups, channel profiles, stream profiles, user agents,
core settings, plugins, DVR rules, comskip config, users, channels-with-streams, logos).

This is a **new outbound data egress path** that the original threat model (§1–§7, written
against the *import* engine) does not cover. It is, however, structurally the mirror image of a
control ECM **already implements** on the legacy backup path: `backend/routers/backup.py` already
redacts credential-class keys before they enter the backup ZIP — `REDACTED = "***REDACTED***"`
sentinel, `_scrub_journal_db_to_temp()` rewrites credential keys inside `journal.db`,
`_gather_settings()` returns a redacted `settings.json`. **Addendum A requires the v0.18.0
13-category ZIP builder to extend that same redaction to the categories the legacy path does not
yet touch** (M3U/EPG/XC creds per-category, cloud-target tokens, user `password_hash`, etc.),
using the *same shared denylist* — not a second, divergent one. (Plugin config secrets are kept in
the denylist superset for defence-in-depth / forward-compatibility even though the **plugins category
itself is excluded from v0.18.0 export/restore per ADR-012 D10** — redacting a key that is not
exported is harmless and avoids a gap if plugins return.)

**Trust boundary:** the export artifact crosses **ECM → operator's hands → (optionally) cloud
storage**. Once it leaves the container it is outside every ECM control. Treat the **default
(redacted)** artifact as if it will be stored unencrypted on a third party's disk, because it often
will be (Dropbox, an S3 bucket, a USB stick). The **opt-in passphrase-encrypted** artifact
(Addendum C / ADR-012 D12) is the only form that carries *unredacted* credentials off-host, and it
is protected solely by the operator's passphrase — see §10 for that path's controls and residuals.

### 8.2 STRIDE rows — Export Artifact

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation | Status | Sev |
|---|---------|--------|--------|-----------------|------------|--------|-----|
| A1 | ZIP build | Information Disclosure | Backup ZIP ships plaintext credentials from any of the 13 categories | Operator downloads a routine backup; the ZIP contains M3U `username`/`password`, EPG/XC creds, core-settings SMTP password, user `password_hash`, cloud-target tokens. Operator emails it to support, drops it in a shared Dropbox, or it's swept up by an automated backup-of-the-backup. All those secrets are now exfiltrated. | Shared `_REDACT_KEYS`-style denylist (the *same* set `backend/routers/backup.py` uses for the legacy path) applied per-category before bytes enter the ZIP: every matched key → `REDACTED` sentinel **or** Fernet ciphertext (A3). `journal.db` scrubbed via the existing `_scrub_journal_db_to_temp()` pattern. Unit test asserts no known plaintext secret appears anywhere in the ZIP. | to-build (`0i2vt.7`) | **High** |
| A2 | ZIP build / dry-run | Information Disclosure | Backup *preview* or progress log echoes secret values | The Phase-1 backup runs as an HTTP-polled task (ADR-012 D5); a verbose progress line or a "what will be included" preview lists raw category rows including secret fields. | Reuse the §3.4/I2 rule: preview/log lines enumerate **metadata only** (category, count, sizes), and any per-row preview runs through the shared `_redact()` helper. No secret value ever reaches a log line or a progress event. | to-build (`0i2vt.7`) | High |
| A3 | ZIP build | Information Disclosure (mitigated form) | A *restorable* backup needs creds intact, so redaction would break cross-instance migration → temptation to ship plaintext | Operator wants to migrate Dispatcharr config A→B *including* M3U passwords so they don't have to re-enter 40 sources. Redaction defeats that, so someone "just for migration" turns redaction off. | Carry credential fields as **Fernet ciphertext** (ADR-012 D3 primitive, `backend/cloud_storage/crypto.py`), restricted to the `SyncTarget`/`CloudTarget` credential columns from `0i2vt.4`. The Fernet **key is never in the ZIP**. Restore on an instance lacking the key → field unreadable → treated as absent, restore continues with WARN (never plaintext, never crash). No global "disable redaction" switch exists. | to-build (`0i2vt.4` + `0i2vt.7`) | Med |
| A4 | ZIP build | Tampering / Spoofing | Manifest SHA-256 covers pre-redaction bytes, so a tampered redacted file passes verification | Attacker who can write into the ZIP after redaction but before manifest finalisation swaps a redacted `settings.json` for one with a malicious SMTP relay; if the manifest was computed over the *original* bytes, the swap goes undetected on restore. | Manifest SHA-256 is computed over the **exact bytes written into the ZIP** (post-redaction, post-encryption), as the last step before sealing the archive. Restore verifies what is present. (Reuses the §3.3/T4 manifest-hash mechanism, just pinned to the redacted content.) | to-build (`0i2vt.7`) | Med |
| A5 | ZIP build | Repudiation | No record that a backup was taken / who took it / whether it was redacted | An operator (or a compromised admin session) silently exfiltrates config via a backup; no trail. | `journal.log_entry` per backup: `category='backup'`, `user_id`, request ID, timestamp, category counts, artifact SHA-256, and a `redaction_mode` field (`redacted` vs `encrypted`). Mirrors §3.3/R1. | to-build (`0i2vt.7`) | Med |

### 8.3 Mitigations summary (Addendum A)

1. **Single shared redaction denylist.** One `_REDACT_KEYS`-style constant, imported by both the legacy `backup.py` path and the new 13-category ZIP builder. Adding a category never means forgetting to add it to a second list. (Checklist 18.)
2. **Redact-by-default, encrypt-as-carve-out.** Default behaviour redacts to the `REDACTED` sentinel. The only path that carries readable-with-key ciphertext is `SyncTarget`/`CloudTarget` credentials via the existing Fernet primitive; the key never travels with the artifact. No global "ship plaintext" switch. (Checklist 19.)
3. **Metadata-only previews & progress.** Dry-run / preview / progress events enumerate path, size, sha256, counts — never row contents. Per-row preview, where it exists, runs through `_redact()`. (Checklist 18, reuse I2.)
4. **Manifest over post-redaction bytes.** SHA-256 is the last step before sealing; it covers exactly what's in the ZIP. (Checklist 20.)
5. **Backup audit row.** Every backup is journalled with subject, request ID, counts, artifact hash, and redaction mode. (Checklist 18/Addendum A row A5; reuse R1.)

### 8.4 Residual risk (Addendum A)

- **Residual: artifact handling after egress — Medium, accepted (PO-resolved).** Once the ZIP leaves the container ECM has zero control. Even fully redacted, the artifact still reveals the *shape* of a deployment (channel names, source URLs minus creds, user list). Mitigations reduce a credential breach to a topology disclosure; they cannot make the artifact safe to publish. **PO decision (2026-06-16, ADR-012 D12):** "redacted backup may be stored anywhere; encrypted backup needs the passphrase kept separate" **is** the accepted posture — and the PO went further than the original "v0.18.x candidate" recommendation, deciding to **ship an optional whole-artifact passphrase encryption path in v0.18.0** (opt-in; redact-by-default stays the default). That path is specified in **Addendum C (§10)**. The topology-disclosure residual of a *redacted* artifact remains accepted (it is inherent to producing a portable backup at all).
- **Residual: redaction-denylist completeness — Low.** A credential-class key not in the denylist ships in plaintext. Mitigated by the shared-list discipline (one place to audit) and the unit test that fails if a known secret leaks; but a *novel* category added without a denylist review is the failure mode. Action: the "add a Dispatcharr category" checklist must include "add its secret keys to `_REDACT_KEYS`".
- **Residual: Fernet key compromise — Low (for v0.18.0 scope).** If both the encrypted artifact and the Fernet key leak, the carve-out creds are exposed. Out of scope to fix here (no KMS for MVP, ADR-012 D3); the key-bootstrap integrity check (`0i2vt.2`, mode 0600 + ownership) is the compensating control.

---

## 9. Addendum B — Outbound Destinations & SSRF (v0.18.0 cloud upload + v0.18.1 sync)

**Added:** 2026-05-12 · **Bead:** `enhancedchannelmanager-0i2vt.3` (Phase 0) · **Feeds:** `0i2vt.4` (SyncTarget/CloudTarget models), `0i2vt.5` (first-run SSRF wizard + always-on denylist + DNS-rebinding mitigations), `0i2vt.8` (cloud upload wiring) · **Source:** ADR-012 D4 + "Security Mandatory #2, #3, #5"

### 9.1 Scope

v0.18.0 adds **operator-configurable outbound destinations**:

- **CloudTarget** — S3 (incl. S3-compatible: MinIO, Wasabi, B2 — *operator supplies the endpoint URL*), WebDAV (*operator supplies the base URL*), OneDrive, Dropbox, Google Drive. Adapters already scaffolded in `backend/cloud_storage/` (`s3_adapter.py`, `onedrive_adapter.py`, `dropbox_adapter.py`, `gdrive_adapter.py`, `factory.py`).
- **SyncTarget** — a second Dispatcharr instance's URL (reserved for v0.18.1 sync; schema lands in v0.18.0 per ADR-012).

**The threat:** an authenticated admin (or an attacker who has compromised an admin session)
can point ECM at an arbitrary URL — and ECM, running *inside the operator's network*, will make
the request. That is a classic **server-side request forgery (SSRF)** primitive: hit the cloud
metadata endpoint (`169.254.169.254`) for instance credentials, scan/poke internal infrastructure
(routers, databases, other containers), or use ECM as an unwitting proxy. The §1–§7 import model
only ever talked about *inbound* archives and the *one* admin-configured local Dispatcharr (ADR-004
treated as trusted, sync-to-third-party explicitly out of scope). v0.18.0 changes that: ECM now
deliberately makes outbound requests to **destinations the operator typed in**, including
*endpoint URLs* (not just API tokens) for S3-compatible and WebDAV. Every one of those URLs is
attacker-influenceable and must be validated.

ADR-012 D4 resolves the policy: a **first-run wizard** lets the operator pick *LAN-friendly*
(RFC1918 + loopback allowed — the default, because plenty of operators back up to a NAS on
`192.168.x.x`) vs *public-only* (private ranges blocked). **Regardless of that choice**, an
always-on denylist blocks metadata/link-local/CGNAT/etc., and DNS-rebinding mitigations are
mandatory. This addendum is the threat-model backing for `0i2vt.5`; §9.4 hands the concrete
validator requirements to that bead.

**Trust boundary added:** **ECM → arbitrary operator-supplied URL** (cloud APIs, S3-compatible
endpoints, WebDAV servers, Dispatcharr-B). This is a new boundary; treat the destination as
untrusted *and* treat the act of connecting as a capability that must be gated.

### 9.2 STRIDE rows — Outbound Destinations

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation | Status | Sev |
|---|---------|--------|--------|-----------------|------------|--------|-----|
| B1 | CloudTarget config | Tampering / Spoofing | Operator-supplied S3/WebDAV **endpoint URL** is malicious | Admin (or hijacked admin session) sets the "S3 endpoint" to `http://169.254.169.254/` or `http://10.0.0.5:6379/` ("MinIO on the LAN"). ECM dutifully connects on the next backup upload. | Shared SSRF validator (§9.4) on **every** outbound URL before connect — endpoint URLs included, not just tokens. No adapter issues a raw `httpx`/`requests` call that bypasses the validator (single chokepoint). | to-build (`0i2vt.5` + `0i2vt.8`) | **High** |
| B2 | Any outbound URL | Information Disclosure / EoP | SSRF to cloud metadata / link-local / internal ranges | Destination resolves to `169.254.169.254` → ECM fetches the instance's IAM credentials and (because it's a "backup destination") may even *upload to it* or surface the response in an error. Or destination is `127.0.0.1:<admin-port>` / `100.64.x.x` / `[::1]` and ECM is now an internal-network scanner/proxy. | **Always-on denylist** (regardless of wizard choice): `169.254.0.0/16` (incl. IMDS), `100.64.0.0/10`, `0.0.0.0/8`, `::1`, `fc00::/7`, `fe80::/10`, `fec0::/10`, `::ffff:0:0/96`, non-`http(s)` schemes — *all rejected in both modes*. `127.0.0.0/8` + RFC1918 rejected in public-only mode, allowed in LAN-friendly. (§9.4 item 2.) | to-build (`0i2vt.5`) | **High** |
| B3 | Any outbound URL | Tampering | DNS rebinding / TOCTOU — hostname validated, then re-resolves to a denied IP at connect time (or a redirect lands on one) | Attacker controls `evil.example.com`; first DNS lookup (validation) returns a public IP, second lookup (the actual connect) returns `169.254.169.254`. Or the destination replies `302 → http://169.254.169.254/latest/meta-data/`. The naïve "validate the hostname then `requests.get(hostname)`" pattern is bypassed. | **Resolve-then-connect-by-IP:** resolve once, validate *every* returned A/AAAA against the denylist (any denied record → reject the whole request), connect by the validated IP with the hostname as SNI/`Host:`. **Redirect re-validation:** 3xx to a new host is not auto-followed; re-run the full denylist + resolve-by-IP on the redirect target, and reject `https→http` downgrades. (§9.4 items 3–4.) | to-build (`0i2vt.5`) | **High** |
| B4 | Cloud adapters | EoP / bypass | An adapter (`s3_adapter.py` etc.) makes a raw HTTP call that skips the validator | The S3 SDK or a WebDAV client library opens its own connection straight from the endpoint URL string, never touching ECM's validator → SSRF protection is theatre. | The validator is the **single chokepoint**: either (a) all adapters route through one ECM-owned HTTP client that validates on every request and pins to the resolved IP, or (b) where an SDK insists on doing its own DNS, ECM pre-resolves + validates and hands the SDK an IP + `Host:` override. CI test: grep adapters for direct `httpx`/`requests`/`urllib` calls; any hit fails the build unless it's the validated client. (§9.4 item 1.) | to-build (`0i2vt.8`) | High |
| B5 | CloudTarget/SyncTarget creds | Tampering / Repudiation | Scheduled backup uses a *stale* (rotated/revoked) cloud token; or `insecure=true` is set with no audit trail | (a) Admin rotates the Dropbox token; a backup schedule created earlier still fires with the old token — silently failing or, worse, hitting a now-attacker-controlled account that reused the old token. (b) Admin sets `insecure=true` for a self-signed WebDAV box; later that box is MITM'd and nobody knows ECM was talking to it without TLS verification. | (a) `credential_version` + `token_revoked_at` columns on the model (`0i2vt.4`); scheduler captures version at enqueue, worker re-checks at execute, aborts with WARN + audit row on mismatch (Security Mandatory #5). (b) `verify=True` default; `insecure=true` per-target escape hatch writes a `journal.log_entry` (`category='backup_outbound'`, host, `tls_verified=false`) on **every** request, not once at config time. (§9.4 items 5–6, checklist 25–26.) | to-build (`0i2vt.4` + `0i2vt.8`) | Med |
| B6 | First-run wizard / settings | EoP / misconfig | Wizard default or a later settings change weakens the denylist | Operator clicks through the wizard picking "LAN-friendly" without reading; or a future settings page lets someone add `169.254.169.254` to an allowlist "to scrape metadata for monitoring". | The always-on denylist is **not** subject to the wizard choice or any allowlist — it is unconditional in code, with no settings key that can disable it. The wizard choice only toggles the RFC1918/loopback band. A test asserts the always-on entries are rejected in *both* modes and that no settings key removes them. (§9.4 item 2.) | to-build (`0i2vt.5`) | Med |

### 9.3 Mitigations summary (Addendum B)

1. **One SSRF chokepoint.** A single ECM-owned validated HTTP client (or pre-resolve+IP-pin shim for SDKs that won't cooperate). CI grep forbids raw outbound calls in the adapters. (Checklist 21, 24; §9.4 item 1.)
2. **Always-on denylist, unconditional.** Metadata/link-local/CGNAT/IPv6-special/non-http(s) rejected in *both* wizard modes; no settings key, no allowlist can re-enable them. (Checklist 22; §9.4 item 2.)
3. **LAN-friendly is the only knob.** The wizard toggles RFC1918 + `127.0.0.0/8` only; default LAN-friendly per ADR-012 D4. (Checklist 22; §9.4 item 2.)
4. **Resolve-then-connect-by-IP.** Resolve once, validate all records, connect by validated IP with hostname as SNI/`Host:`. Closes DNS-rebinding TOCTOU. (Checklist 23; §9.4 item 3.)
5. **Redirect re-validation + no scheme downgrade.** 3xx to a new host re-runs the full check; `https→http` rejected. (Checklist 24; §9.4 item 4.)
6. **TLS verify on; insecure flag is audited per request.** `verify=True` default; `insecure=true` → audit row every time. (Checklist 25; §9.4 item 6.)
7. **Credential-freshness binding.** `credential_version` + `token_revoked_at`; enqueue-time capture, execute-time re-check. (Checklist 26; §9.4 item 5.)

### 9.4 Phase-1 handoff — SSRF validator requirements (for `0i2vt.5`)

`0i2vt.5` MUST deliver a validator meeting **all** of the following. (`0i2vt.5`'s own description
already lists most of this — restating here so the threat model is the single source the bead's
acceptance criteria check against. Where `0i2vt.5` says "extends bead `zbt74` validator pattern":
that pattern covers scheme + IPv4 RFC1918; the items below add IPv6, CGNAT, IMDS, and
DNS-rebinding coverage that `zbt74` does not.)

1. **Single validated outbound client / chokepoint.** All outbound HTTP(S) from the backup/sync
   subsystem goes through one validated client. Cloud SDKs that do their own DNS get pre-resolved
   IPs + `Host:` overrides from the validator. CI test forbids raw `httpx`/`requests`/`urllib`
   calls in `backend/cloud_storage/` adapters and the sync code.
2. **Scheme allowlist + always-on IP denylist + wizard-toggled band.**
   - Scheme: only `http` and `https`. Reject `file`, `ftp`, `gopher`, `data`, `dict`, etc.
   - Always-on deny (both wizard modes, no opt-out, no settings override, no allowlist):
     `0.0.0.0/8`, `169.254.0.0/16` (incl. `169.254.169.254/32` IMDS), `100.64.0.0/10` (CGNAT),
     `::1/128`, `fc00::/7` (ULA), `fe80::/10` (link-local), `fec0::/10` (site-local),
     `::ffff:0:0/96` (IPv4-mapped — must be unwrapped and re-checked against the IPv4 rules so
     `::ffff:169.254.169.254` is caught), `::/128`, multicast (`224.0.0.0/4`, `ff00::/8`).
   - Wizard-toggled: `127.0.0.0/8` and RFC1918 (`10/8`, `172.16/12`, `192.168/16`) + IPv6
     equivalents — *allowed* in LAN-friendly (default), *rejected* in public-only.
3. **Resolve-then-connect-by-IP (DNS-rebinding mitigation).** Resolve the hostname once; validate
   **every** returned A and AAAA record against the rules; if **any** record is denied, reject the
   whole request (do not "pick the allowed one"). Connect by the validated IP, with the original
   hostname as TLS SNI and `Host:` header. No second DNS lookup between validation and connect.
4. **Redirect handling.** Do not transparently follow 3xx to a different host. Either block all
   cross-host redirects, or re-run steps 2–3 on each redirect target before following. Reject any
   redirect that downgrades `https` → `http`. Cap redirect chain length (≤ 5).
5. **Credential-freshness binding (with `0i2vt.4`).** Honour `credential_version` /
   `token_revoked_at`: scheduler captures `credential_version` at enqueue; worker aborts (WARN +
   `journal.log_entry`) if it changed or `token_revoked_at` is set at execute time.
6. **TLS posture.** `verify=True` default. Optional per-target `insecure=true`; when set, every
   outbound request with it logs a `journal.log_entry` (`category='backup_outbound'`, target id,
   host, `tls_verified=false`).
7. **First-run wizard.** Appears on first run; records the LAN-friendly vs public-only choice;
   default = LAN-friendly (ADR-012 D4). The choice is re-editable in settings, but editing it can
   only move the *RFC1918/loopback band* — it can never touch the always-on denylist (item 2).
8. **Regression corpus (mandatory, ships with `0i2vt.5`).** Covers, at minimum: each always-on
   denied range (v4 and v6); `::ffff:169.254.169.254` and other IPv4-mapped representations of
   denied addresses; unicode/punycode hostnames that decode to a denied target; a two-A-record
   response (one allowed, one denied) → rejected; a resolution that changes between validate and
   connect → connection still goes to the validated IP; a `302 → http://169.254.169.254/...`
   redirect → blocked; an `https → http` redirect → blocked; each non-http(s) scheme → rejected;
   RFC1918 allowed in LAN-friendly / rejected in public-only.

### 9.5 Residual risk (Addendum B)

- **Residual: authenticated-admin abuse — Low, accepted.** An admin can still configure a backup
  destination that is *attacker-controlled but a perfectly valid public host* and exfiltrate the
  (redacted, per Addendum A) backup there. The SSRF validator stops ECM from hitting *internal*
  and *metadata* targets; it cannot stop a legitimate admin from sending a backup to a public S3
  bucket they shouldn't. This is inherent to "operator configures their own backup destination"
  and is bounded by the admin-only gating + the audit row on every backup (Addendum A row A5).
  No further mitigation proposed for v0.18.0.
- **Residual: SDK DNS behaviour — Low/Medium until verified.** Item 1's "pre-resolve + `Host:`
  override for SDKs" assumes the boto3 / Dropbox / Graph / WebDAV clients can be made to connect
  by IP. If one cannot (e.g., SNI/cert validation that insists on the hostname *and* does its own
  resolution), that adapter has a residual rebinding window. **Action for `0i2vt.8`:** verify each
  adapter's HTTP layer can be IP-pinned; if not, document the gap and consider an egress-proxy
  shim. Re-rate to Medium if any adapter can't be pinned.
- **Residual: IPv6 / new special-purpose ranges — Low.** IANA adds special-purpose ranges over
  time; a future reserved range not in item 2's list would not be denied. Mitigated by using the
  Python `ipaddress` module's `is_private` / `is_link_local` / `is_reserved` / `is_loopback` /
  `is_multicast` properties as a *backstop* in addition to the explicit CIDR list, so the validator
  fails closed on categories even if a specific new prefix isn't enumerated.
- **Residual: time-of-day DNS for long-running uploads — Low.** A multi-GB upload holds a
  connection open for a long time; the validated IP is fixed for that connection (good), but if
  the connection drops and the client retries, the retry must re-run validation, not reuse a
  cached hostname. **Action for `0i2vt.8`:** retries go back through the validator.

---

## 10. Addendum C — Whole-Artifact Passphrase Encryption (v0.18.0 opt-in cred-carrying backup)

**Added:** 2026-06-17 · **Bead:** `enhancedchannelmanager-u81kh` (Phase 1, build-last/deferrable) ·
**Crypto design:** spike `enhancedchannelmanager-0zrse` (closed — engineer + security + code-reviewer,
live-demo'd vs `cryptography` 49.0.0) · **Source:** ADR-012 D12 · **Feeds:** `0i2vt.7` (ZIP-builder
encrypt stage) + the Phase-2 decrypt-at-ingest gate.

### 10.1 Scope and posture

ADR-012 D12 (PO, 2026-06-16) ships an **optional, opt-in whole-artifact passphrase encryption** path
so that **credentials can travel with a cross-instance migration** (pairs with D11) instead of being
re-entered on the target. **Redact-by-default (D1) remains the default**; passphrase encryption is for
operators who explicitly want secrets included.

**The load-bearing security fact.** Passphrase mode enables cred-carrying migration only by wrapping an
**unredacted** artifact. That moves **every credential** from *"never present in the artifact"*
(redact-by-default) to *"present, protected solely by the operator's passphrase."* This is a
deliberate, PO-accepted trade — but it is exactly why the construction below (KDF strength, AEAD, no
plaintext on failure) and the UX gates (min passphrase, unrecoverable acknowledgement) are mandatory,
not nice-to-haves. The default path does **not** make this trade; only the explicit
"include credentials for migration" opt-in (which **requires** a passphrase) does.

**New primitive — D3/D12 reconciliation.** This is a **new crypto primitive**, intentionally
**parallel to** the existing Fernet primitive in `backend/cloud_storage/crypto.py` (which is
static-key, whole-artifact-in-RAM, and cannot stream). Per ADR-012's own D3 note, **D12 partially
supersedes D3**: **D3 still governs at-rest credential columns** (the `SyncTarget`/`CloudTarget`
Fernet-encrypted fields, Addendum A row A3); **D12 governs this opt-in whole-artifact path**. The two
coexist. `crypto.py` is **not** reused here.

**Trust boundary added:** **ECM → encrypted artifact → operator's hands / cloud → ECM (decrypt on
restore).** The ciphertext crosses the same untrusted egress boundary as a redacted artifact
(Addendum A), but unlike the redacted artifact it **contains live credentials** — so the encryption
must be the only thing standing between the artifact and full credential disclosure.

### 10.2 Construction (from spike `0zrse` — build-ready)

- **KDF:** **scrypt**, **N ≥ 2¹⁵** (floor), r=8, p=1; **per-artifact random salt**. KDF parameters and
  salt are stored in a **cleartext, authenticated header** (so a future ECM — or the `0i2vt.17`
  version check — can read them before attempting decryption).
- **Cleartext authenticated header:** `magic`, **`format_version`** (the *encryption-envelope* version),
  KDF params, salt, AEAD id, chunk size. **`format_version` is SEPARATE from the backup
  `schema_version`** so that ECM `.17`-style version checks can validate the envelope **pre-decrypt**
  and refuse an unsupported version without a passphrase. The header is covered by the AEAD's AAD
  (below), so it cannot be tampered with undetected.
- **AEAD:** **chunked streaming** AEAD — **ChaCha20-Poly1305 or AES-256-GCM**. **Per-chunk nonce**
  (random base XOR a counter). Each chunk's **AAD binds the header + the chunk index + the `is_final`
  flag**, which makes chunk **swap, reorder, and stream truncation** detectable (any such manipulation
  fails authentication).
- **REDACT-THEN-ENCRYPT, enforced structurally:** redaction runs **inside** the build path and is not
  skippable; the `include_credentials` opt-in only **re-injects the approved credential set** before
  encryption. There is no "skip redaction and encrypt instead" branch.
- **Off-event-loop, streaming:** KDF and encrypt/decrypt run **off the event loop** and **stream to
  temp files** (the `.7` builder's in-memory `BytesIO` assembly becomes tempfile-streaming — required
  for the D8 logo-streaming memory model regardless).
- **Phase-2 decrypt is a single ingest gate, not an 11-bead fan-out:** decrypt happens **once** at
  restore ingest, before the per-category importers run; `.10`–`.15` need **zero** crypto changes.
- **Passphrase policy:** **minimum 12 characters**, enforced at the API boundary.

### 10.3 STRIDE rows — Passphrase Encryption

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation | Status | Sev |
|---|---------|--------|--------|-----------------|------------|--------|-----|
| C1 | Encrypted artifact | Information Disclosure | The opt-in path ships an **unredacted** artifact, so a weak/brute-forceable passphrase exposes every credential | Operator picks "include credentials" with a 4-char passphrase; artifact lands in Dropbox; offline brute-force of a low-entropy passphrase recovers all M3U/EPG/SMTP/cloud creds. | Strong KDF (**scrypt N ≥ 2¹⁵**) raises per-guess cost; **min 12-char passphrase** (API-enforced, checklist 29); redact-by-default stays default so this surface only exists when the operator opted in. | to-build (`u81kh`/`0i2vt.7`) | **High** |
| C2 | Build path | Information Disclosure / Tampering | "Encrypt instead of redact" path skips redaction, or a non-passphrase switch ships unredacted creds | A code path lets `include_credentials=true` write unredacted creds without a passphrase, or disables redaction "to make migration work." | **REDACT-THEN-ENCRYPT is structural** (checklist 28): redaction is inside the build path, not skippable; `include_credentials` only re-injects approved creds, and **only** with a passphrase set. No "disable redaction" switch (consistent with Addendum A). Test: `include_credentials=false` + passphrase → decrypt → no plaintext cred. | to-build (`0i2vt.7`) | High |
| C3 | KDF / AEAD construction | Tampering / Spoofing | Weak KDF, missing AEAD, or chunk swap/reorder/truncate yields forged or partial plaintext | Attacker truncates the ciphertext to drop a "force-reset" record, or reorders chunks, or the construction uses an unauthenticated cipher so a flipped bit silently alters a restored value. | **scrypt N ≥ 2¹⁵** KDF; **AEAD** (ChaCha20-Poly1305 / AES-256-GCM) per chunk; **per-chunk nonce**; **AAD binds header + chunk-index + is_final** → swap/reorder/truncation all fail authentication (checklist 29). New primitive parallel to Fernet, off-event-loop, streaming (checklist 31–32). | to-build (`u81kh`) | **High** |
| C4 | Cleartext header | Tampering / DoS | Header tampered, or version confusion forces a wrong/failed decode | Attacker edits the cleartext KDF params (e.g., lowers N) to weaken the derived key, or sets a `format_version` the target mishandles. | Header is **authenticated** (covered by AEAD AAD) → param tampering fails decryption. **`format_version` is separate from `schema_version`** and is checked **pre-decrypt** (checklist 30), so an unsupported envelope is refused cleanly (user-facing "unsupported version"; full detail server-side) rather than crashing or mis-decoding. | to-build (`u81kh`/`0i2vt.17`) | Med |
| C5 | Decrypt path | Information Disclosure (oracle) | A wrong-passphrase vs corrupted-artifact distinction, or an early plaintext release, leaks an oracle | Attacker probes whether a guessed passphrase is "closer" by observing different errors, partial output, or a verified-prefix before full authentication. | **No wrong-passphrase oracle — STRUCTURAL** (checklist 33): wrong passphrase and corrupt artifact raise an **identical exception** and release **zero plaintext** on any failure; never emit a verified prefix before whole-artifact auth completes. **Accepted residual:** spike `0zrse` demonstrated a ~15 ms size-dependent **timing** residual (wrong pass fails at chunk 0; corrupt-last-chunk fails at chunk N) — **accepted** for an offline artifact (see §10.4). Do **not** assert wall-clock equivalence in tests. | to-build (`u81kh`) | Med |
| C6 | Operator UX | Repudiation / availability | Operator forgets the passphrase → artifact is **permanently unrecoverable** | Operator encrypts a migration backup, loses the passphrase, and later cannot restore — total data-availability loss for that artifact, with no ECM-side recovery. | **Accepted risk** — there is intentionally no recovery/backdoor (a backdoor would defeat C1/C5). Compensating control: a **hard-gate UX warning** (checklist 34) — an explicit `acknowledge_unrecoverable` checkbox the operator must tick (not a tooltip) before an encrypted backup is produced. | to-build (`u81kh`) | Med |

### 10.4 Mitigations summary (Addendum C)

1. **Opt-in; redact-by-default preserved.** Encryption is opt-in; the default backup stays redacted
   (D1). Credentials are carried only via the explicit "include credentials for migration" choice,
   which **requires** a passphrase. (Checklist 27.)
2. **REDACT-THEN-ENCRYPT, structural.** Redaction is inside the build path and cannot be skipped;
   `include_credentials` only re-injects approved creds. (Checklist 28.)
3. **Strong, parameterised KDF + authenticated streaming AEAD.** scrypt N ≥ 2¹⁵ + per-chunk AEAD with
   AAD binding header/index/final → no swap/reorder/truncation; min 12-char passphrase. (Checklist 29.)
4. **Cleartext authenticated header, `format_version` separate from `schema_version`.** Version checks
   run pre-decrypt; the header is tamper-evident. (Checklist 30.)
5. **New primitive parallel to Fernet; D3/D12 reconciled.** D3 → at-rest cred columns (Fernet);
   D12 → opt-in whole-artifact path (this primitive). (Checklist 31.)
6. **Off-event-loop streaming.** KDF + encrypt/decrypt stream to temp files off the loop. (Checklist 32.)
7. **No wrong-passphrase oracle — structural, not wall-clock.** Identical exception + zero plaintext on
   failure; the demonstrated ~15 ms timing residual is an accepted offline residual. (Checklist 33.)
8. **Lost-passphrase hard-gate UX.** `acknowledge_unrecoverable` checkbox; no recovery path.
   (Checklist 34.)

### 10.5 Residual risk (Addendum C)

- **Residual: size-dependent timing oracle (~15 ms) — Low, accepted.** Spike `0zrse` **demonstrated**
  that a wrong passphrase fails at the *first* chunk while a corrupted *last* chunk fails after
  streaming the whole artifact, producing a measurable, size-dependent timing difference. The no-oracle
  property is therefore specified as **structural** (identical exception + zero-plaintext-on-failure +
  never release a verified prefix), **not** as wall-clock equivalence. For an **offline** artifact the
  attacker already possesses (no online query channel, no rate-limit to defeat), this timing channel
  yields negligible advantage over offline brute force, which the KDF already gates. **Accepted** for
  v0.18.0; the alternative ("fail at end" to flatten timing) is available if a future use makes the
  artifact's decryption an online oracle. Do not paper over it with a flaky stopwatch test.
- **Residual: unredacted creds protected solely by the passphrase — Medium, accepted (opt-in only).**
  By design, the opt-in cred-carrying artifact stakes every credential on the operator's passphrase
  (§10.1). Mitigated by the strong KDF, the 12-char minimum, and the fact that the default path never
  makes this trade. A weak (but ≥12-char) passphrase remains the operator's risk. No KMS / escrow for
  the MVP (consistent with D3's no-KMS posture).
- **Residual: lost passphrase = unrecoverable — Low/accepted (availability, not confidentiality).**
  Intentional: no recovery/backdoor. Bounded by the hard-gate acknowledgement (C6). This is an
  availability trade the operator explicitly accepts per artifact.
- **Residual: build-time dependency choice deferred.** Spike `0zrse` left one open build-kickoff
  decision — implement framing within `cryptography` (hand-rolled chunk framing, zero new deps, more
  maintenance) **vs.** add a vetted streaming AEAD library (PyNaCl `secretstream` / `age` — security's
  preference, removes framing risk, adds a dependency + supply-chain review). **Not a threat-model
  decision; settle at `u81kh` build start.** Either choice must still satisfy the construction in §10.2.
