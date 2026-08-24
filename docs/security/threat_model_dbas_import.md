# STRIDE Threat Model: DBAS Import / Restore

**Bead:** bd-qmuij (informs bd-gb5r5.3, the DBAS import engine); §8–§9 addenda + checklist 18–26: `enhancedchannelmanager-0i2vt.3` (Phase 0, v0.18.0 DBAS absorption)
**Author:** Security Engineer persona (Claude)
**Date:** 2026-04-20 · **Addenda A & B added:** 2026-05-12 · **Re-pointed at ADR-012, lifted to Accepted, Addendum C added:** 2026-06-17 · **Addendum D (cross-instance live sync) added:** 2026-06-19
**Status:** Accepted. Assumptions (§6) and the Addendum A residual (§8.4) resolved by PO; cross-instance scope corrected to ADR-012 D11; passphrase encryption covered by Addendum C (ADR-012 D12); v0.18.1 cross-instance live sync covered by Addendum D ([ADR-013](../adr/ADR-013-cross-instance-live-sync.md), epic `i39wu`); one-time credential provisioning (ADR-013 S10–S13, bead `wd20y`) covered by **§11.5**
**Related:** bd-ppe28 (closed, OWASP hardening), ADR-002 (restore transaction model, pending), ADR-004 (DBAS instance trust, referenced), [ADR-012](../adr/ADR-012-dbas-absorption-approach.md) (DBAS absorption, source of truth), epic `enhancedchannelmanager-0i2vt` (DBAS absorption), beads `0i2vt.4` (Fernet credential models) / `0i2vt.5` (SSRF wizard) / `0i2vt.7` (ZIP builder) / `0i2vt.8` (cloud upload) / `u81kh` + `0zrse` (whole-artifact passphrase encryption, Addendum C) / `l1p4p` + `tsfv0` (users importer + Dispatcharr user-API spike, §3.6 P2 / §6 A3)

---

## 1. Scope & System Overview

The DBAS (Database Archive / Backup & Sync) import endpoint accepts an uploaded `.zip` archive and restores a prior ECM + Dispatcharr configuration into the running instance. bd-gb5r5.3 ports the legacy `importService.ts` from DBAS to Python. The archive contains heterogeneous payloads: ECM `journal.db` + settings, uploaded logos/TLS material, M3U credentials, API tokens, and user accounts. The restore path is ordered: M3U → EPG → profiles → groups → stream profiles → logos → channels → user agents → settings → DVR → comskip → users → refresh triggers, with name-based conflict resolution and ID remapping.

> **Plugins EXCLUDED from v0.18.0 (ADR-012 D10).** The original DBAS restore path included a
> **plugins** payload whose execution semantics were never determined in ECM (`grep -ri plugin
> backend/` → 0 hits). ADR-012 D10 (PO, 2026-06-16) **excludes the plugins category from v0.18.0
> backup/restore entirely.** It sidesteps the unresolved RCE-on-restore question and unblocks the
> rest of the bulk importer (`0i2vt.13` drops the plugins category). Consequently, every
> plugin-conditional threat in this model (S4, T4, D4, P3) is **moot / deferred for v0.18.0** and
> retained only as a forward-looking record for the release that revisits plugin semantics. The
> former plugin step is removed from the restore order above.

This threat model covers the **Python import engine** ECM will build. The current `backend/routers/backup.py` ZIP restore (`/api/backup/restore`) is a smaller-scope precursor and is referenced as the inherited baseline: its protections (admin-only, manifest, basic path-traversal guard) are **table stakes**; DBAS extends them to cover categories that baseline does not (users, M3U creds). ECM has no current `plugin*` code in `backend/` (verified by `grep -ri plugin backend/` → 0 hits); rather than specify the plugin threat against an undetermined spec, ADR-012 D10 **excludes plugins from v0.18.0**, so the plugin-related rows below (S4, T4, D4, P3) are retained as deferred records, not v0.18.0 acceptance criteria.

Attack surfaces modeled:

1. **ZIP upload** (HTTP multipart path): authz, size, origin claim.
2. **ZIP extraction** (archive parsing): Zip Slip, symlinks, bombs, entry count.
3. **User-table restore**: risk of attacker-supplied admin account.
4. **Plugin restore**: RCE iff plugins are executable. **EXCLUDED from v0.18.0 per ADR-012 D10.** Surface retained for traceability only; the conditional rows below are moot/deferred.
5. **M3U / API-token restore**: credential handling + log redaction.
6. **Endpoint authz**: admin-only gating, per-category opt-in, current-user preservation.
7. **Audit logging**: who restored what, when, with what counts.

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

(`plugins/` is **not** restored in v0.18.0, per ADR-012 D10; the former plugin step is removed.)

Trust boundaries crossed:
- **Browser → ECM** (authenticated admin)
- **ECM → filesystem** (tempdir, then `/config/`)
- **ECM → SQLite** (`journal.db`)
- **ECM → Dispatcharr** (separate service; per ADR-004 treated as admin-configured & trusted)

**Archive provenance: trusted operator input, always-on safety guards (ADR-012 D11).** The
restored archive is treated as **trusted operator input**: the same trust ECM extends to an
operator typing configuration directly into the UI. This is the correct posture for a self-hosted,
single-operator LAN tool: full untrusted-archive provenance/signature checking (archive signing,
a trust store, supply-chain attestation) is **deliberately out of scope**. It is overkill for this
deployment model, and that is the **decided posture**, not an unresolved gap. **Trusted does not
mean unvalidated, however:** a set of always-on safety validations applies to *every* archive
**regardless of source**, including the cross-instance migration case (back up instance A, restore
onto instance B), which ADR-012 D11 puts squarely **IN scope** for v0.18.0:
- **SSRF denylist on every restored URL** (M3U/EPG/XC hosts). See §3.6 P4 + Addendum B; the
  validator does not trust a URL just because it arrived in an operator's archive.
- **Schema / `schema_version` validation** before any file is materialised (ADR-012 D1; checklist 7).
- **Never restore a foreign admin that locks out the current operator**: current-operator
  preservation keyed off the **auth subject** (not username/id, which a cross-instance archive
  remaps), and conservative privilege-flag restore. See §3.6 P2.

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
| S4 | Plugin restore | Archive ships plugin claiming provenance from a trusted author | SHA-256 per-plugin entry in manifest; if plugins are code, plugin payload must match signed/allowlisted set | **moot / deferred (ADR-012 D10: plugins excluded from v0.18.0)** | Crit (conditional) |
| S5 | M3U/API-token restore | Archive plants M3U source pointing to attacker host | Admin is the one importing: they already control sources; URL scheme validation (from bd-ppe28.3) re-applied at restore time rather than trusted from archive | to-build (reuse ppe28.3) | Med |
| S6 | Endpoint authz | Session fixation / cookie theft before invoke | Out of scope: covered by auth subsystem; noted for traceability | existing | Low |
| S7 | Audit logging | Journal entry spoofed by crafted payload | Journal rows written server-side post-decision with auth-subject + request ID; archive content cannot dictate log fields | to-build | Med |

### 3.2 Tampering

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| T1 | ZIP upload | MITM modifies archive in flight | TLS termination (existing); endpoint hash compared to manifest | existing + to-build | Med |
| T2 | ZIP extraction | Zip Slip: entry names `../../../app/main.py` | Reject any entry whose `pathlib.PurePosixPath` normalised form is absolute, contains `..`, or whose `resolve()` leaves the destination tempdir. **All extraction targets tempdir, not `/config/`** | to-build (baseline has a weaker check in `backup.py` §162-167) | High |
| T2b | ZIP extraction | Symlink entry escapes tempdir | Reject any zip entry whose `external_attr >> 16` indicates `stat.S_IFLNK`; `ZipFile.extract()` in CPython does not follow symlinks but we must refuse to **create** them | to-build | High |
| T3 | User-table restore | Tampered hash in `users.password_hash` overwrites admin row | DB restore runs inside a SQLAlchemy transaction; on failure, rollback; current-admin-row preservation rule blocks overwrite even on success (§3.6 P2) | to-build | High |
| T4 | Plugin restore | Plugin file content mutated vs. manifest | SHA-256 verification per manifest entry rejects any file whose content hash does not match | **moot / deferred (ADR-012 D10: plugins excluded from v0.18.0)** | Crit (conditional) |
| T5 | M3U/API-token restore | Secret field altered to attacker-controlled value | Admin trust: they chose the archive. Mitigation via manifest hash (T4 mechanism) | to-build | Med |
| T6 | Endpoint authz | Path parameter tampering bypasses category gate | Accept only a whitelist of category keys (reuse `RESTORABLE_SECTIONS`-style registry); reject unknown keys with 400 | to-build | Med |
| T7 | Audit logging | Post-hoc tampering of `journal.db` entries | Out of scope at this layer; journal tamper-evidence is a separate bead. Note for PO | accepted-risk | Low |

### 3.3 Repudiation

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| R1 | ZIP upload | Admin denies having uploaded | journal entry records `user_id`, IP (via `X-Forwarded-For` where trusted), archive SHA-256, timestamp, request ID | to-build | Med |
| R2 | ZIP extraction | Silent partial extraction leaves unattributable artifacts | Extraction into per-request tempdir; successful files + failed entries both logged with request ID | to-build | Med |
| R3 | User-table restore | No record of which admin account was added/replaced | Per-category audit entry with `category=users`, `added_count`, `updated_count`, `usernames_added[]` (usernames only; no PII beyond that) | to-build | High |
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
| D1 | ZIP upload | Arbitrarily large upload exhausts RAM / disk | **Max upload size cap** (propose: 256 MB; PO-tunable) enforced before `await file.read()`. Stream to tempfile via `shutil.copyfileobj` rather than `await file.read()` in one shot | to-build (baseline reads into memory, see §253) | High |
| D2 | ZIP extraction | Zip bomb: small archive, gigabytes uncompressed | **Compression-ratio cap** (propose: max 100× per entry, max 1 GB cumulative uncompressed); **entry-count cap** (propose: 10,000 entries); enforce by iterating `zf.infolist()` pre-extraction | to-build | High |
| D2b | ZIP extraction | Deep nested paths / pathological names cause path-resolver stalls | Cap path depth (e.g., 32 segments) and name length (255 bytes) | to-build | Med |
| D3 | User-table restore | Restore of massive user table blocks the request worker | Background task with WebSocket progress (per ADR-003 pending); synchronous fallback protected by a hard row-count cap | to-build | Med |
| D4 | Plugin restore | Infinite-loop plugin executed during restore | Plugins NOT executed during restore; only written to disk, activation gated. If plugins execute at import, bound with wall-clock + memory limits | **moot / deferred (ADR-012 D10: plugins excluded from v0.18.0)** | Crit (conditional) |
| D5 | M3U/API-token restore | Restore triggers N synchronous Dispatcharr API calls | Reuse existing async `dispatcharr_client`; per-item timeout (already in client). Batch size cap (propose 500) | to-build | Med |
| D6 | Endpoint authz | Admin endpoint DoS via cred-stuffing at login | Out of scope for this endpoint: auth router rate-limiting owns this | existing (verify) | Low |
| D7 | Audit logging | High-volume category restore produces one journal row per item → journal.db bloat | Aggregate to **one journal row per category** with count, not per-item; batched log entry pattern | to-build | Med |

### 3.6 Elevation of Privilege

| # | Surface | Threat | Mitigation | Status | Sev |
|---|---------|--------|------------|--------|-----|
| P1 | ZIP upload | Non-admin triggers restore via CSRF against an authenticated admin | `RequireAdminIfEnabled` + existing auth middleware (GET-safe; restore is POST). CSRF mitigation relies on token-bearer auth (not cookies); verify in DBAS router | existing (verify) | High |
| P2 | User-table restore | **Crown-jewel threat:** archive grants attacker admin / privilege-escalation via crafted user rows | (a) category `users` is **opt-in** with a distinct checkbox in the UI + request body flag `include_users: true`; (b) **current authenticated admin row is never overwritten, deleted, disabled, or demoted**, identified by **auth subject** of the requesting user (NOT username/`id`, which a cross-instance archive remaps); (c) **no password is transported**: Dispatcharr's user API exposes `password` only as a write-only plaintext field (no pre-computed-hash API; source hash never retrievable, per spike `tsfv0` vs 0.26.0), so each restored user is **created with no usable password + force-reset**; ECM never fabricates, derives, or rehashes a password; (d) **the real escalation surface is the WRITABLE privilege flags** `is_superuser` / `is_staff` / `user_level`; restore them **conservatively** (default non-privileged; never trust the archive's superuser bit for an account the operator did not already control); (e) audit row with list of usernames only, never passwords/hashes | to-build | **Crit** |
| P3 | Plugin restore | Plugin runs at import as root/app user, escaping to shell | (a) category `plugins` is **opt-in** with explicit warning UI; (b) if plugins are code: sandboxing required (subinterpreter / subprocess / container) OR reject plugin category until ADR lands; (c) if plugins are config only: validate against schema and skip execution semantics | **moot / deferred (ADR-012 D10: plugins excluded from v0.18.0; the RCE surface is removed by exclusion)** | **Crit** (conditional) |
| P4 | M3U/API-token restore | Restored M3U source URL triggers SSRF at first refresh | ppe28.3 URL-scheme validation applied at **restore time**, not just at input time | to-build (reuse ppe28.3) | Med |
| P5 | Endpoint authz | DBAS endpoint inadvertently exempted via `AUTH_EXEMPT_PATHS` | Automated test asserts DBAS paths are NOT in `AUTH_EXEMPT_PATHS` | to-build | High |
| P6 | ZIP extraction | Symlink → `/app/main.py` overwrites running code | Symlink refusal (T2b) + extraction targets tempdir only; files move to `/config/` only after validation, never to `/app/` | to-build | Crit |
| P7 | Audit logging | Restore succeeds silently, attacker hides traces by later restore | Journal entries for DBAS import are marked `user_initiated=True`; frontend exposes a filter for `category='dbas_import'`; retention policy tracked in a separate bead (note for PO) | to-build | Med |

**Cell count:** 6 dimensions × 7 surfaces nominal = 42; table has 50 rows (some dimensions list sub-threats T2b, D2b, P2-subpoints). All 42 canonical cells covered, with extra rows where a single surface warranted split threats. **Note:** the four plugin-restore rows (S4, T4, D4, P3) are **moot / deferred for v0.18.0** (ADR-012 D10: plugins excluded); they remain in the table for traceability and to seed the release that revisits plugin semantics.

---

## 4. Hardening Checklist (Acceptance Criteria for bd-gb5r5.3)

The DBAS import engine implementation (bd-gb5r5.3) must satisfy **all** of the following, each mapped to a STRIDE cell:

1. **Admin-only endpoint gating**: DBAS import routes use `RequireAdminIfEnabled` DI; DBAS paths absent from `AUTH_EXEMPT_PATHS`; test asserts both. *(S1, P1, P5)*
2. **Per-category opt-in flag**: the `users` category requires a distinct boolean flag in the request body; default false; frontend checkbox ships with warning copy. *(S3, P2)* *(The `plugins` category is excluded from v0.18.0 per ADR-012 D10, so no plugin opt-in flag ships in v0.18.0.)*
3. **Current admin preservation**: the requesting admin's `users` row is **never** overwritten, deleted, disabled, or demoted; identified by **auth subject** (not username/`id`, which a cross-instance archive remaps); test covers the case where the archive contains a colliding username. *(P2)*
   - **No password transported, conservative privilege flags**: every restored user is created **with no usable password + force-reset** (Dispatcharr exposes `password` write-only plaintext; no hash crosses the boundary, per spike `tsfv0`); the WRITABLE `is_superuser`/`is_staff`/`user_level` flags are restored **conservatively** (default non-privileged; never trust the archive's superuser bit for an account the operator did not already control). Tests: colliding-username-does-not-touch-operator, archive-superuser-bit-not-trusted, no-password-set, force-reset-flagged. *(P2)*
4. **Zip Slip hardening**: reject any entry whose normalised path is absolute, contains `..`, or whose `resolve()` escapes the tempdir; reject symlink entries (`S_IFLNK`); reject paths >32 segments or >255 bytes. *(T2, T2b, D2b, P6)*
5. **Zip bomb / DoS caps**: enforce pre-extraction: max upload 256 MB, max entries 10,000, max cumulative uncompressed 1 GB, max per-entry ratio 100×. Values are PO-tunable via settings. *(D1, D2)*
6. **Streaming upload**: do not call `await file.read()`; stream to a `NamedTemporaryFile` via `shutil.copyfileobj`; enforce upload cap during stream. *(D1)*
7. **SHA-256 manifest**: `ecm_backup.json` includes `{files: [{path, sha256, size}]}`; verify all three before any file is materialised outside tempdir; reject mismatch with 400. *(S2, T4)*
8. **Tempdir isolation & cleanup**: all extraction lands in a per-request `tempfile.TemporaryDirectory`; move to `/config/` only after full validation; cleanup guaranteed by context manager (`try/finally` double-safety). Dry-run guaranteed side-effect free. *(T2, P6, plus bead AC)*
9. **Secrets-in-logs denylist**: `_redact()` helper applied to all log lines and dry-run previews; denylist covers `password`, `password_hash`, `token`, `api_key`, `smtp_password`, M3U `username`/`password`, plus any field ending `_secret` / `_token`. `password_hash` is in the denylist on the **export side** too (`_REDACT_KEYS`; see Addendum A / checklist 18): a password hash sitting in an unencrypted backup artifact is an **offline-cracking target**, so it is redacted (or carried only under whole-artifact passphrase encryption, Addendum C), never shipped in cleartext. Unit test enforces. *(I2, I5, I7)*
10. **URL scheme re-validation on restore**: reuse bd-ppe28.3 validator for any restored URL field (M3U source, EPG source, XC host). *(S5, P4)*
11. **Per-category audit logging**: one `journal.log_entry` per category with `category='dbas_import'`, `action_type=category_name`, counts, and (for `users`) list of usernames added, **never** passwords / hashes / secrets. Log includes request ID. *(R1-R5, R7, D7, P7)*
12. **Error sanitisation**: HTTPException `detail` strings never echo file paths, stack traces, or unique-constraint values; full detail goes to server log via `logger.exception`. *(I1, I3)*
13. **Plugin execution gate: N/A for v0.18.0.** Plugins are **excluded from v0.18.0** backup/restore (ADR-012 D10): the category is not imported at all, so there is no plugin payload to write, gate, or execute. This item is retained as the forward-looking acceptance criterion for the release that revisits plugin semantics: if/when plugins are restored, they must be written to disk but NOT executed during restore, with activation behind a separate explicit admin action. *(D4, P3; both moot/deferred for v0.18.0)*
14. **Transaction model**: all DB restore per category runs inside a SQLAlchemy transaction with rollback on exception; see ADR-002 for cross-category atomicity. *(T3)*
15. **Dispatcharr-call bounding**: Dispatcharr restore batches capped at 500 items, each call uses existing per-request timeout. *(D5)*
16. **CSRF posture**: DBAS endpoint must not rely on cookie-only auth; require `Authorization: Bearer` token. Test asserts. *(P1)*
17. **Authz denial logging**: 401/403 on DBAS endpoint emits structured WARN log including reason. *(R6)*

### 4.1 Addendum checklist items (v0.18.0 DBAS absorption: Addenda A & B)

The v0.18.0 epic (`enhancedchannelmanager-0i2vt`, ADR-012) adds an **export/backup** path
and **outbound cloud destinations** that did not exist when items 1–17 were written. The
following items extend the checklist; they are acceptance criteria for the Phase-0 work
(`0i2vt.1`, `0i2vt.2`, `0i2vt.3`) and the Phase-1 work (`0i2vt.4`, `0i2vt.5`, `0i2vt.7`,
`0i2vt.8`). See Addendum A (§8) and Addendum B (§9) for the threat tables these map to.

18. **Export-artifact redaction parity (Addendum A)**: the v0.18.0 backup ZIP builder
    (`0i2vt.7`) MUST apply the same redaction the existing YAML/`settings.json` export path
    applies (`backend/routers/backup.py` → `REDACTED` marker + `_scrub_journal_db_to_temp` +
    `_gather_settings`): every credential-class key across all **13 Dispatcharr categories**
    (M3U account passwords/usernames, EPG source creds, XC host creds, core-settings SMTP
    password, plugin config secrets, user `password_hash`, DVR/comskip tokens, cloud-target
    tokens) is replaced with the `REDACTED` sentinel **or** stored encrypted (item 19) before
    the bytes enter the ZIP. The denylist is the single shared `_REDACT_KEYS`-style set used by
    both YAML and ZIP paths. There is no second, divergent list. Unit test: build a backup whose source
    state contains a known M3U password, an SMTP password, and a cloud token; assert none of the
    three plaintext values appear anywhere in the ZIP bytes (manifest, `settings.json`,
    `journal.db`, per-category YAML, binary subtree). *(A1, A2, A4, Addendum A; closes Security
    Mandatory #4 + #6)*
19. **Encrypted-rather-than-redacted carve-out (Addendum A)**: where a backup is intended to
    be **restorable with credentials intact** (cross-instance migration), credential fields MAY
    be carried in ciphertext instead of redacted, but ONLY via the existing Fernet primitive
    (`backend/cloud_storage/crypto.py`, per ADR-012 D3) and ONLY for the `SyncTarget`/`CloudTarget`
    credential columns defined in `0i2vt.4`. The Fernet key is **never** placed in the ZIP. A
    backup taken on instance A and restored on instance B without the key MUST surface the
    credential fields as unreadable (decryption-failure → field treated as absent, restore
    continues with a WARN), never as plaintext and never as a hard crash. Test: restore a
    backup whose `CloudTarget.token_ciphertext` was encrypted under a different key → token field
    absent, restore proceeds. *(A3, Addendum A; ties into ADR-012 D3)*
20. **Manifest covers redacted state (Addendum A)**: the ZIP `manifest` / `schema_version`
    block records SHA-256 over the **post-redaction** bytes (the bytes actually written), so
    integrity verification on restore validates what is present, not a pre-redaction phantom.
    The manifest itself is enumerated as metadata-only on dry-run (path/size/sha256), per item 8.
    *(A5, Addendum A)*
21. **SSRF validator on ALL outbound URLs (Addendum B)**: every outbound HTTP(S) request the
    backup/sync subsystem makes (cloud-destination uploads such as the S3 endpoint URL, WebDAV base URL,
    OneDrive/Dropbox/GDrive API hosts and any user-overridable endpoint, the `SyncTarget` Dispatcharr-B
    URL, and any user-supplied callback/webhook) passes through a shared SSRF validator BEFORE the
    connection is opened. The validator is the single chokepoint; no adapter (`s3_adapter.py`,
    `onedrive_adapter.py`, `dropbox_adapter.py`, `gdrive_adapter.py`, WebDAV) may issue a raw
    `httpx`/`requests` call that bypasses it. This is the Phase-1 deliverable in `0i2vt.5`/`0i2vt.8`;
    this checklist item is the contract. *(B1, B2, B4, B6, Addendum B; ADR-012 D4)*
22. **Always-on denylist regardless of LAN-friendly choice (Addendum B)**: even when the
    first-run wizard (`0i2vt.5`) chose LAN-friendly mode, the validator ALWAYS rejects, with
    no opt-out: link-local `169.254.0.0/16` (incl. IMDS `169.254.169.254/32`),
    `0.0.0.0/8`, IPv6 ULA `fc00::/7`, IPv6 link-local
    `fe80::/10`, IPv6 site-local `fec0::/10`, IPv4-mapped-IPv6 `::ffff:0:0/96`, and any
    non-`http`/`https` scheme. Loopback (`127.0.0.0/8` **and `::1`**), RFC1918 ranges, and RFC 6598
    Shared Address Space (`100.64.0.0/10`) are
    rejected in public-only mode and allowed in LAN-friendly mode; everything in the always-on
    list is rejected in **both**. *(`::1` moved from always-on to the toggled band by GH #754 /
    bead `0yh70`; see §9.4 item 2.)*
    Test corpus: each denied range + an IPv4-mapped-IPv6 representation of the IMDS address + a
    `gopher://`/`file://`/`ftp://` scheme → all rejected in both modes. *(B2, B6, Addendum B;
    ADR-012 D4)*
23. **DNS-rebinding mitigation: resolve-then-connect-by-IP (Addendum B)**: the validator
    resolves the destination hostname **once**, validates the returned address(es) against the
    denylist (and, if any A/AAAA record is denied, rejects the whole request; no "use the allowed
    one"), then the HTTP client connects **by that validated IP**, sending the original hostname
    only as SNI and `Host:` header. The window between validation and connect must not contain a
    second, unvalidated DNS lookup. Test: a hostname that returns two A records (one public, one
    `169.254.169.254`) → rejected; a hostname whose resolution is mocked to change between
    validation and connect → connection still goes to the validated IP. *(B3, Addendum B; ADR-012 D4)*
24. **Redirect re-validation (Addendum B)**: 3xx responses are NOT auto-followed to a new host
    without re-running the full denylist + resolve-by-IP check on the redirect target; a redirect
    to a previously-unvalidated host is either blocked outright or only followed after a fresh
    validation pass. Cross-scheme downgrades (`https://` → `http://`) on redirect are rejected.
    Test: server replies `302` to `http://169.254.169.254/latest/meta-data/` → request fails, no
    connection to the IMDS host. *(B3, B6, Addendum B; ADR-012 D4)*
25. **TLS-verify default + audited insecure flag (Addendum B)**: outbound requests use
    `verify=True` by default. A per-`CloudTarget`/`SyncTarget` `insecure=true` escape hatch MAY
    exist (self-signed WebDAV/MinIO are real deployments) but every outbound request made with
    `insecure=true` writes a `journal.log_entry` audit row (`category='backup_outbound'`,
    target id, host, `tls_verified=false`), not just once at config time, but on **every** request.
    Test: configure an `insecure=true` target, trigger a backup upload, assert an audit row with
    `tls_verified=false` exists for that request. *(B1, B5, Addendum B; ADR-012 D4)*
26. **Outbound-credential freshness binding (Addendum B / cross-ref `0i2vt.4`)**: a scheduled
    backup/sync op that fires after the target's credentials were rotated or revoked MUST NOT use
    the stale token: the `CloudTarget`/`SyncTarget` model carries `credential_version` and
    `token_revoked_at`; the scheduler captures `credential_version` at enqueue time and the worker
    re-checks it at execution time, aborting (WARN + audit row) if it changed or if
    `token_revoked_at` is set. (This is Security Mandatory #5; the schema lands in `0i2vt.4`, the
    enforcement in `0i2vt.6`/`0i2vt.8`.) *(B5, Addendum B)*

### 4.2 Addendum checklist items (whole-artifact passphrase encryption: Addendum C)

These extend the checklist for the opt-in whole-artifact passphrase-encryption path (ADR-012 D12,
bead `u81kh`, crypto design from spike `0zrse`). They are acceptance criteria for the `.7` ZIP-builder
encrypt stage and the Phase-2 decrypt-at-ingest gate. See Addendum C (§10) for the threat table.

27. **Opt-in, redact-by-default preserved (Addendum C)**: passphrase encryption is **opt-in**; the
    default backup remains redact-by-default (ADR-012 D1). The approved structured credential set
    is preserved in the artifact **only** via an explicit operator "include credentials for
    migration" choice that **requires** a passphrase. There is no switch that preserves those
    recognized fields or credential-bearing URL values without a passphrase. Operator-authored
    free text remains outside this guarantee. *(C1, C2)*
28. **REDACT-THEN-ENCRYPT is structural (Addendum C)**: redaction runs **inside** the build path and
    cannot be skipped; `include_credentials` only re-injects the approved credential set before
    encryption. There is no "encrypt instead of redact, skipping redaction" code path. Test: a backup
    with `include_credentials=false` + a passphrase still replaces the approved structured
    credential set and credential-bearing URL values after decryption. Operator-authored free text
    remains outside that guarantee. *(C2, C3)*
29. **KDF + AEAD construction (Addendum C, per spike `0zrse`)**: scrypt KDF with **N ≥ 2¹⁵** (floor),
    r=8, p=1; per-artifact random salt; KDF params + salt live in a **cleartext authenticated header**.
    Chunked streaming AEAD (ChaCha20-Poly1305 **or** AES-256-GCM); per-chunk nonce (random base XOR
    counter); each chunk's **AAD binds the header + chunk-index + is_final flag** so no chunk can be
    swapped, reordered, or the stream truncated. Min **12-char** passphrase, API-enforced. *(C3, C4)*
30. **Cleartext header with `format_version` separate from `schema_version` (Addendum C)**: the
    header carries `magic`, `format_version` (the *encryption-envelope* version, **distinct from** the
    backup `schema_version`), KDF params, salt, AEAD id, and chunk size, all authenticated. This lets a
    version check (`0i2vt.17`) read the envelope/schema metadata **before decrypting** and refuse an
    unsupported version without needing the passphrase. *(C4)*
31. **New primitive, parallel to Fernet: D3/D12 reconciliation (Addendum C)**: passphrase encryption
    is a **new crypto primitive** (`backend/cloud_storage/crypto.py`'s Fernet is static-key,
    whole-in-RAM, non-streaming and is **not** reused for this path). **ADR-012 D3 governs at-rest
    credential columns** (Fernet); **D12 governs the opt-in whole-artifact path** (this primitive).
    The two coexist; D12 *partially* supersedes D3 only for the whole-artifact path. *(C3)*
32. **Off-event-loop streaming (Addendum C)**: KDF and encrypt/decrypt run **off the event loop** and
    **stream to temp files** (not whole-artifact-in-RAM). The `.7` builder's in-memory `BytesIO`
    assembly becomes tempfile-streaming (needed for D8 regardless). *(C3)*
33. **No wrong-passphrase oracle: STRUCTURAL, not wall-clock (Addendum C)**: a wrong passphrase and a
    corrupted artifact MUST fail with an **identical exception** and release **zero plaintext** on any
    failure; never emit a verified prefix before the whole-artifact authentication completes. This is a
    **structural** property (identical exception + zero-plaintext-on-failure), **not** a timing
    guarantee: spike `0zrse` **demonstrated** a ~15 ms size-dependent timing residual (wrong passphrase
    fails at chunk 0; corrupt-last-chunk fails at chunk N), which is **ACCEPTED** for an offline
    artifact (see Addendum C residual). Do **not** write a wall-clock/stopwatch equivalence test (flaky
    and misleading); test the structural property instead. *(C5)*
34. **Lost passphrase = unrecoverable, hard-gate UX (Addendum C)**: a lost passphrase makes the
    artifact **permanently unrecoverable** (no recovery, no backdoor). The UI must surface this as a
    **hard gate** (an `acknowledge_unrecoverable` checkbox the operator must tick, not a tooltip)
    before an encrypted backup is produced. *(C6)*

---

## 5. Test Cases (for `backend/tests/security/`)

Proposed test module layout once the engine lands:

- `test_dbas_import_authz.py`
  - `test_requires_admin`: non-admin gets 403.
  - `test_endpoint_not_in_auth_exempt_paths`: static assertion.
  - `test_csrf_rejects_cookie_only_request`: reject if no bearer token.
- `test_dbas_import_zipbomb.py`
  - `test_rejects_oversized_upload`: 257 MB body → 413.
  - `test_rejects_too_many_entries`: 10,001-entry archive → 400.
  - `test_rejects_oversized_uncompressed`: 1.1 GB virtual expansion → 400.
  - `test_rejects_compression_ratio_bomb`: 1 KB → 200 MB entry → 400.
- `test_dbas_import_zipslip.py`
  - `test_rejects_path_traversal`: entry `../../etc/passwd` → 400.
  - `test_rejects_absolute_path`: entry `/app/main.py` → 400.
  - `test_rejects_symlink_entry`: `S_IFLNK` bit set → 400.
  - `test_rejects_deep_nesting`: 33-segment path → 400.
- `test_dbas_import_manifest.py`
  - `test_rejects_missing_manifest`: no `ecm_backup.json` → 400.
  - `test_rejects_sha256_mismatch`: tampered content byte → 400.
  - `test_rejects_unknown_version`: manifest claims v999 → 400.
- `test_dbas_import_users.py`
  - `test_users_category_requires_opt_in`: import with `users` content but `include_users=False` → users untouched.
  - `test_current_admin_preserved`: archive contains same username as requester (preservation keyed off **auth subject**) → requester row intact.
  - `test_current_admin_not_demoted`: archive marks requester as non-admin → rejected or ignored.
  - `test_no_password_transported`: restored user is created with **no usable password** + force-reset flag; archive password/hash fields are never applied.
  - `test_archive_superuser_bit_not_trusted`: archive marks a non-operator account `is_superuser=True` → restored conservatively as non-privileged.
  - `test_users_category_fails_closed_if_hash_field_appears`: startup capability check fails the `users` category closed if a `password_hash` write field appears on the Dispatcharr schema.
- `test_dbas_import_secrets.py`
  - `test_no_secret_in_logs`: restore an archive containing an M3U password; grep `caplog` for plaintext → must be absent.
  - `test_dryrun_redacts_settings`: dry-run preview of settings.json masks `password`, `smtp_password`.
  - `test_error_message_sanitised`: IntegrityError → response `detail` does not contain username or SQL fragment.
- `test_dbas_import_audit.py`
  - `test_one_journal_entry_per_category`: 3 categories → 3 rows.
  - `test_journal_entry_omits_secrets`: `after_value` field never contains secret keys.
  - `test_journal_entry_includes_request_id`: request ID correlates logs and journal row.
- `test_dbas_import_cleanup.py`
  - `test_tempdir_cleanup_on_success`.
  - `test_tempdir_cleanup_on_exception`: force failure mid-extraction, assert tempdir removed.
  - `test_dryrun_is_side_effect_free`: DB unchanged, `/config/` unchanged after dry-run.
- `test_dbas_import_url_validation.py`
  - `test_rejects_file_scheme_m3u_url`: reuse ppe28.3 suite; archive with `file://` URL → rejected.
- `test_dbas_import_plugins.py`: **DEFERRED for v0.18.0** (plugins excluded, ADR-012 D10). Retained for the release that revisits plugins:
  - `test_plugins_not_executed_on_import`: stub plugin with side-effect (write marker file); restore; marker file absent.
- `test_dbas_passphrase_encryption.py` (Addendum C, opt-in passphrase path)
  - `test_redact_then_encrypt_no_plaintext`: `include_credentials=false` + passphrase; decrypt; recognized credential fields and credential-bearing URL values are replaced (structured redaction not skipped inside encrypt; operator-authored free text is outside this assertion).
  - `test_wrong_passphrase_and_corrupt_artifact_identical_exception`: wrong passphrase and a corrupted artifact raise the **same** exception type/message; **no** plaintext is released on either failure. (Structural, not wall-clock; no stopwatch assertion.)
  - `test_header_version_check_before_decrypt`: an unsupported `format_version` is rejected reading the **cleartext header**, without a passphrase.
  - `test_chunk_reorder_or_truncate_rejected`: swapping/reordering chunks or truncating the stream fails AEAD/AAD verification (no partial plaintext).
  - `test_min_passphrase_length_enforced`: an 11-char passphrase is rejected at the API boundary.
  - `test_lost_passphrase_unrecoverable_ack_required`: producing an encrypted backup requires the `acknowledge_unrecoverable` flag.

---

## 6. Assumptions (resolved)

The items below originally gated design-completeness. All are now **resolved** (the resolutions are
why this model is lifted from Draft to Accepted). Each is kept with its resolution recorded inline.

**A1. Plugins: code or config? → RESOLVED: excluded from v0.18.0 (ADR-012 D10).**
`grep -ri plugin backend/` returns zero matches in the ECM backend, and whether a Dispatcharr
"plugin" is **executable Python** (RCE risk = critical) or **declarative config** remains
undetermined. Rather than gate the rest of the restore on that unknown, **ADR-012 D10 (PO,
2026-06-16) excludes the plugins category from v0.18.0 backup/restore entirely.** The RCE-on-restore
question is sidestepped, not answered; it is revisited in the release that understands plugin
semantics. Threats S4, T4, D4, P3 are therefore **moot / deferred for v0.18.0**.

**A2. Cross-instance restore → RESOLVED: IN scope, trusted-input + always-on guards (ADR-012 D11).**
This previously asserted cross-instance restore was **out of scope** (same-instance only). **ADR-012
D11 (PO, 2026-06-16) puts cross-instance restore squarely IN scope for v0.18.0.** It is the epic's
headline value (back up instance A, restore onto instance B for migration / DR). The corrected
posture (see §2): the archive is **trusted operator input**, with *no archive signing/provenance/trust
store* (deliberately overkill for a self-hosted single-operator LAN tool; this is the **decided
posture**, not a gap), but **always-on safety validations apply regardless of source**: SSRF
denylist on every restored URL (§3.6 P4, Addendum B), `schema_version` validation (D1, checklist 7),
and current-operator preservation by **auth subject** so a foreign admin row can never lock out the
operator running the restore (§3.6 P2). ADR-004 is no longer the gating dependency it was framed as.

**A3. Password-hash algorithm parity → RESOLVED: NON-ISSUE; no hash is ever transported (spike `tsfv0`).**
The earlier framing assumed the archive carried a `users.password_hash` whose algorithm had to match
the target, with "reject the users category on mismatch" as the control. **Spike `tsfv0` (live vs
Dispatcharr 0.26.0) makes this moot:** Dispatcharr's user API exposes `password` only as a
**write-only plaintext field**: there is **no pre-computed-hash API**, and the source hash is
**never retrievable** (GET never returns it). ECM's own auth uses **bcrypt**; Dispatcharr/Django uses
**pbkdf2_sha256**; the two are not interchangeable in either direction, but that no longer matters
because **no hash ever crosses the restore boundary.** The users importer therefore **never transports
a hash**: every restored Dispatcharr user is **created with no usable password + force-reset**, and ECM
**never fabricates, derives, or rehashes** a password. Hash-algorithm parity is a **non-issue** for
restore; the importer does not even parse an incoming hash field. (See §3.6 P2 clause (c). The real
crown-jewel surface is the writable privilege flags (clause (d)), not hash integrity.) A startup
capability check should fail the users category **closed** if a `password_hash` write field ever
appears on the Dispatcharr schema.

**A4. Upload size / entry-count caps → RESOLVED (ratified defaults).**
256 MB upload, 10,000 entries, 1 GB cumulative, 100× per-entry ratio, tunable via `settings.json`.
Accepted as defensible defaults for typical ECM deployments. (Checklist 5.)

**A5. Journal retention / tamper-evidence → RESOLVED (accepted residual).**
`journal.db` is not tamper-evident (T7, P7). Hash-chained / external-sink tamper-evidence is a
**separate epic**, out of scope here; this is **accepted risk** for v0.18.0.

**A6. CSRF posture → RESOLVED.**
Auth is bearer-token only (not cookie-based); DBAS import requires `Authorization: Bearer` (checklist
16). If cookie-based sessions are ever added, DBAS import will need double-submit CSRF or
`SameSite=Strict`; tracked as a follow-on at that time.

---

## 7. Related Work & References

- `backend/routers/backup.py`: baseline ZIP restore (`/api/backup/restore`). DBAS extends it; this model is a **superset** of that endpoint's protections.
- `docs/auth_middleware.md`: global secure-by-default auth; DBAS inherits.
- bd-ppe28, bd-ppe28.1, bd-ppe28.3 (closed): OWASP URL-scheme hardening; reused for M3U/EPG URLs at restore.
- ADR-002 (pending): DBAS restore transaction model & downtime contract.
- ADR-003 (pending): WebSocket long-running job pattern; DBAS import will run as a background job with progress events.
- ADR-004 (`docs/adr/ADR-004-release-cut-promotion-discipline.md`): release-cut discipline; DBAS instance-trust posture is now resolved by **ADR-012 D11** (cross-instance IN scope; trusted-input + always-on guards), not deferred to ADR-004.
- [ADR-012](../adr/ADR-012-dbas-absorption-approach.md): **source-of-truth ADR for DBAS absorption** (the D1–D12 decision table). The §3 STRIDE controls and Addenda A/B/C below are the security contract for the `0i2vt` child beads (`0i2vt.5`, `0i2vt.7`, `0i2vt.8`, `l1p4p`, `u81kh`, etc.); ADR-012 does not restate them; this document is authoritative for the controls.
- bd-gb5r5.3: DBAS import engine; hardening checklist in §4 will be appended to that bead's acceptance criteria.

> **Note on bead lineage.** The 42-bead plan `bd-gb5r5` referenced in the §1–§7 body was retired
> 2026-04-21 and superseded by epic `enhancedchannelmanager-0i2vt` ("v0.18.0 DBAS absorption:
> Backup + Restore"). The source of truth for that epic is **ADR-012**
> (`docs/adr/ADR-012-dbas-absorption-approach.md`, Accepted). ADR-012's own preamble records the
> ADR number-history (the earlier phantom DBAS filename and the later number collision); that history
> is **not** duplicated here. All decision references in this document, D1–D12, point at ADR-012's
> decision table. The
> §1–§7 body still uses `bd-gb5r5.3` for the import-engine bead id (not re-baselined), but its
> *decisions* are governed by ADR-012; the Addenda (A export-redaction, B outbound/SSRF, C
> passphrase encryption) and checklist items 18–26 are the v0.18.0-current layer and take precedence
> where they overlap.

---

## 8. Addendum A: Export Artifact Redaction (v0.18.0 backup ZIP)

**Added:** 2026-05-12 · **Bead:** `enhancedchannelmanager-0i2vt.3` (Phase 0) · **Feeds:** `0i2vt.4` (Fernet credential models), `0i2vt.7` (ZIP artifact builder) · **Closes:** "Security Mandatory #4 + #6"

### 8.1 Scope

The v0.18.0 backup feature produces an **export artifact**: a ZIP wrapping per-category YAML
plus a binary subtree (uploaded logos, TLS material), with a `manifest` block carrying
`schema_version`, per-file SHA-256, and sizes, across the **13 Dispatcharr config categories**
(M3U accounts, EPG sources, channel groups, channel profiles, stream profiles, user agents,
core settings, plugins, DVR rules, comskip config, users, channels-with-streams, logos).

This is a **new outbound data egress path** that the original threat model (§1–§7, written
against the *import* engine) does not cover. It is, however, structurally the mirror image of a
control ECM **already implements** on the legacy backup path: `backend/routers/backup.py` already
redacts credential-class keys before they enter the backup ZIP: `REDACTED = "***REDACTED***"`
sentinel, `_scrub_journal_db_to_temp()` rewrites credential keys inside `journal.db`,
`_gather_settings()` returns a redacted `settings.json`. **Addendum A requires the v0.18.0
13-category ZIP builder to extend that same redaction to the categories the legacy path does not
yet touch** (M3U/EPG/XC creds per-category, cloud-target tokens, user `password_hash`, etc.),
using the *same shared denylist*, not a second, divergent one. (Plugin config secrets are kept in
the denylist superset for defence-in-depth / forward-compatibility even though the **plugins category
itself is excluded from v0.18.0 export/restore per ADR-012 D10**: redacting a key that is not
exported is harmless and avoids a gap if plugins return.)

**Trust boundary:** the export artifact crosses **ECM → operator's hands → (optionally) cloud
storage**. Once it leaves the container it is outside every ECM control. Treat the **default
(redacted)** artifact as if it will be stored unencrypted on a third party's disk, because it often
will be (Dropbox, an S3 bucket, a USB stick). The **opt-in passphrase-encrypted** artifact
(Addendum C / ADR-012 D12) is the only form that carries *unredacted* credentials off-host, and it
is protected solely by the operator's passphrase. See §10 for that path's controls and residuals.

### 8.2 STRIDE rows: Export Artifact

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
3. **Metadata-only previews & progress.** Dry-run / preview / progress events enumerate path, size, sha256, counts, never row contents. Per-row preview, where it exists, runs through `_redact()`. (Checklist 18, reuse I2.)
4. **Manifest over post-redaction bytes.** SHA-256 is the last step before sealing; it covers exactly what's in the ZIP. (Checklist 20.)
5. **Backup audit row.** Every backup is journalled with subject, request ID, counts, artifact hash, and redaction mode. (Checklist 18/Addendum A row A5; reuse R1.)

### 8.4 Residual risk (Addendum A)

- **Residual: artifact handling after egress (Medium, accepted, PO-resolved).** Once the ZIP leaves the container ECM has zero control. After structured credential fields and credential-bearing URL values are replaced, the artifact still reveals the *shape* of a deployment and retains operator-authored free text, which could itself contain an unrecognized secret. Mitigations reduce the known structured-credential exposure; they cannot make the artifact safe to publish. **PO decision (2026-06-16, ADR-012 D12):** "redacted backup may be stored anywhere; encrypted backup needs the passphrase kept separate" **is** the accepted posture, and the PO went further than the original "v0.18.x candidate" recommendation, deciding to **ship an optional whole-artifact passphrase encryption path in v0.18.0** (opt-in; redact-by-default stays the default). That path is specified in **Addendum C (§10)**. The topology and authored-content residual of a *redacted* artifact remains accepted (it is inherent to producing a portable backup at all).
- **Residual: redaction-denylist completeness (Low).** A credential-class key not in the denylist ships in plaintext. Mitigated by the shared-list discipline (one place to audit) and the unit test that fails if a known secret leaks; but a *novel* category added without a denylist review is the failure mode. Action: the "add a Dispatcharr category" checklist must include "add its secret keys to `_REDACT_KEYS`".
- **Residual: Fernet key compromise (Low, for v0.18.0 scope).** If both the encrypted artifact and the Fernet key leak, the carve-out creds are exposed. Out of scope to fix here (no KMS for MVP, ADR-012 D3); the key-bootstrap integrity check (`0i2vt.2`, mode 0600 + ownership) is the compensating control.

### 8.5 Update 2026-08-17: the redaction control as built (bead `enhancedchannelmanager-gi4zn`)

Addendum A above is the design record. This section is the **current** description of the shipped control, and where the two differ this one is authoritative.

The trigger was a live drill (2026-08-05, run 3, finding F4): a standard artifact carried an Xtream Codes account's `username` in clear beside a correctly redacted `password`, in both places it appears. Enumerating a real artifact against the same rule found four more instances of the same protected-beside-unprotected asymmetry and three bearer credentials (`emby_api_key`, `jellyfin_api_key`, `plex_token`) that the exact-name denylist had never matched at all. An external security review of the first fix then found three more findings (A-1 to A-3 below), and a third round replaced the journal.db denylist with an allowlist.

**The property the control now establishes:** a standard artifact carries no value that identifies **or** authenticates against a third-party service, and no ECM authentication state.

#### Three rules, one place

`_redact_credentials_deep` in `backend/routers/backup.py` is the single redaction authority for every category and for the legacy `GET /api/backup/export` YAML. All four rules are needed; none is complete alone.

| # | Rule | Matches on | Why the others do not cover it |
|---|------|-----------|--------------------------------|
| 1 | Credential-class key denylist (`_REDACT_KEYS`) | Dict key name, case-insensitive, exact | Cheap and precise, but blind to a key it has not been told about. `emby_api_key` shipped in cleartext for exactly this reason. |
| 2 | Provider-identity keys (`_PROVIDER_IDENTITY_KEYS`, currently `username`) | Dict key name | Rule 1 is a *secret* list by construction. A username is not a secret, and it is still half a credential pair and the half that names the subscription. |
| 3 | URL credential value scrub (`_scrub_credential_urls`) | The **value**, wherever a URL appears | No key denylist can see a credential inside `get.php?username=...&password=...` or a `https://<username>:<password>@host/` userinfo, because the key holding it is called `server_url`. |
| 4 | Known-credential path segments (`_rewrite_known_credential_segments`) | The **literal credential value**, as a whole URL path segment | Rules 1–3 all fail on `/live/<user>/<pass>/<id>.ts`: the key is called `url`, and the credential is neither in the userinfo nor in the query string. |

Rule 2 is **on by default**, so a new caller of the deep redactor fails closed. There is exactly **one** exemption, held as a closed set (`_IDENTITY_EXEMPT_CATEGORIES`) rather than a per-call flag so it is auditable in one place: the `dispatcharr_users` category. Its username names the operator's own Dispatcharr instance rather than a third party, and `dbas.importers.users` creates each account by username and runs its destination-collision check on it, so a sentinel there would delete the restore path rather than protect anything. A test pins the set at exactly that one member so it cannot grow into a general escape hatch.

Rule 4 exists because rule 3's stated gap turned out to be reachable (bead `enhancedchannelmanager-msqf7`). A real Xtream Codes provider, sampled 2026-08-20, served **every one of its 1,409,363 stream URLs** at `/{live,movie,series}/<user>/<pass>/<id>` while authenticating its guide endpoint by query string — one provider, both carriers. The archive producer does not emit a stream URL (`_STREAM_CREDENTIAL_FIELDS` / `_safe_embedded_stream`, bead `enhancedchannelmanager-7i8rf`), but **cross-instance sync does**, deliberately: the stream `url` is the matcher's Tier-1 identity. So the pair travelled to the destination on every scheduled cycle while the run reported credentials stripped.

It is still true that no **general** rule separates `/live/u/p/1.ts` from an ordinary path. Rule 4 is not general. The source instance knows its own provider credentials, so `_collect_credential_values` harvests them off the raw payload — by the same key sets rules 1 and 2 use, so the three cannot drift on what counts as a credential — and each path segment is compared to those literal values, raw and percent-decoded. A path is rewritten because it contains **this operator's** password, not because it looks like it might contain someone's.

The **password is the gate**, and that is the whole of the false-positive defence: a URL is rewritten only once one of its segments equals a known secret, and only then is the identity half redacted alongside it. Without that gate, an operator whose XC username happened to be a structural path word (`live`, `movie`, `news`) would have every URL on the instance mangled, including credential-free ones from other providers. With it, a username collision can only occur inside a URL already proven to carry the password.

Unlike rules 1–3 the value is **rewritten, not replaced whole**: scheme, host, port, kind marker and stream id survive. A stream URL that cannot be carried at all costs the replica the stream, which is bead `enhancedchannelmanager-v7d37`'s failure one layer down. What lands is a stream that names where it pointed and cannot play until the destination has its own provider account, and the run says so (`RestoreReport.stream_urls_redacted`). A URL carrying credentials in **both** its query and its path is still handled by rule 3 and loses the whole value, so the path half can never survive on a partial rewrite's coat-tails.

**Residual, stated rather than left implicit:** a URL carrying only the *username* in a path segment, with no password anywhere in it and no credential-named query parameter, is not rewritten — the gate does not open. No observed provider emits that shape, and the only rule that would catch it is the ungated username match this design rejects.

**A second URL carrier reaches the destination, and it is handled the opposite way.** Cross-instance sync replicates a REMOTE-URL logo by copying its address rather than its bytes (bead `enhancedchannelmanager-sgrez`) — the same shape rules 3 and 4 exist for, since a logo address comes from the same provider on the same instances. Each candidate goes through `_scrub_credential_urls` with the same harvested values before it can enter the plan, so no new redaction rule is introduced. The difference is what happens when the scrub fires: a logo URL the scrub touches at all is **dropped**, not carried rewritten. Rule 4's survive-the-rewrite trade-off is specific to a stream, whose address is the matcher's identity and whose loss costs the replica the stream; a logo whose credential segments are the sentinel simply does not load, and `dbas/importers/logos.py` already rules that a destination row pointing at a silent 404 is worse than an honest miss. The record still travels without its address, so the run names the logo and the channels it affected rather than the logo vanishing.

#### ECM's own authentication state no longer ships (findings A-1, A-2)

Until this bead, the `journal.db` scrub visited `alert_methods` and nothing else, so the **default** artifact carried `users.password_hash` (the operator's own bcrypt admin hash, offline-crackable at leisure), `users.username` and `email` to crack it against, `user_sessions.refresh_token_hash` and `prior_refresh_token_hash`, the `ip_address` and `user_agent` the operator administers from, `password_reset_tokens.token_hash`, and `user_identities.provider` / `external_id` / `identifier` correlating the ECM admin with their OIDC, SAML or LDAP identity at a third-party IdP. The reviewer read these values out of a built artifact with `sqlite3`.

Those tables are now gone from the standard artifact. The rows are **removed rather than masked**, and that is an availability decision: ECM's first-run setup keys on `users` being empty, so a `users` table left populated but stripped of usable hashes is the one state that is both unauthenticatable and ineligible for the setup wizard. `VACUUM` with `secure_delete` is load-bearing rather than hygiene, and was measured: without it a plain delete leaves the purged hash verbatim in the page file.

The restore side compensates so that removal cannot cost availability: the destination's own account rows are snapshotted before `journal.db` is written and reinstated afterwards, recreating the table from the model when the artifact did not ship it. An instance that genuinely has no accounts reinstates nothing, which preserves the disaster-recovery path. A side effect is a tightening: an artifact's `users` table can no longer silently replace a live one.

#### The journal.db scrub is an allowlist, not a denylist

Three review rounds each found more tables that should not ship. That is the same failure shape as rule 1 above, so the direction was inverted. `_STANDARD_ARTIFACT_TABLES` enumerates the **fourteen** tables a standard artifact is allowed to carry, each with its reason recorded beside the entry; every other table is dropped and the file is `VACUUM`ed. A table added to the schema later ships nothing until someone deliberately permits it. A companion registry, `_STANDARD_ARTIFACT_EXCLUDED`, records a reason for each of the **thirty** model-declared tables that are dropped, and a test fails when a model declares a table classified in neither, so a new table cannot reach production unclassified.

The measured cost of the old direction, from a live database: it carried **eight tables that no current model declares at all** (`services`, `health_checks`, `incidents`, `incident_updates`, `maintenance_windows`, `service_alert_rules`, `service_alert_history` from the removed pre-v0.13 health-monitor subsystem, and `popularity_rules`). `services.health_endpoint` is an operator URL and `incidents.created_by` is an account name. No denylist maintained by reading `models.py` could have seen them, because they are not in `models.py`. See `docs/database_migrations.md`, which has described them as orphans since before this bead existed.

Permitted tables are not merely trusted: every string cell of every permitted table goes through the same JSON deep-redaction and URL rules, applied per cell rather than per named column, because a column list is the denylist shape this round removed.

#### The scrub fails closed (finding A-3)

Redaction previously failed **open** on all three of its error paths. An unopenable database and an unreadable table each returned the raw byte-for-byte copy of the live database behind a `200`, and a row whose `alert_methods.config` would not parse was shipped verbatim while valid rows in the same database were correctly redacted. Every path now raises `BackupScrubError` and the whole backup fails; the unscrubbed temporary copy is destroyed on the way out rather than left in the system temp directory. A `config` blob that does not parse as a JSON object loses its whole value to the sentinel: the row survives, because its name and type are not credentials and the restore wants them, but no byte that was never parsed can ship. The restore side merges a whole-value sentinel the same way it merges a per-key one, so the fail-closed producer cannot destroy a working alert method.

#### Effect on the residuals above

- **Redaction-denylist completeness** is now **closed for `journal.db` tables** and **still open for category fields.** Rules 1 and 2 remain key-name matching over the YAML categories, so a novel credential-class field in a new category still ships until someone adds it. Two tests narrow it: one reads the live settings model and fails on a credential-shaped settings field that is not covered, and one requires every model-declared table to carry an explicit keep or drop decision. Neither covers a new Dispatcharr-sourced category field, so the "add a Dispatcharr category" checklist item stands.
- **Artifact handling after egress** is unchanged in kind but materially smaller in degree. A standard artifact replaces recognized structured credential fields and credential-bearing URL values, and it carries no ECM accounts. It is not categorically safe for public disclosure: channel and group names, rule definitions and notes, and credential-free provider addresses remain, and free text could contain a secret ECM cannot recognize. The operator must assess that authored content before attaching the artifact to a support ticket.
- The **encrypted cred-carrying** artifact is unaffected by everything above and remains the migration path. The identity keys join its preserve set, the URL scrub is off on that path, and its `journal.db` member is a byte-for-byte copy of the live database, verified by hash. Note that encryption alone does not carry credentials: `include_credentials` does, and it requires a passphrase. That artifact is protected solely by the operator's passphrase and must never be attached to a ticket.

#### Compatibility note for incident response

A standard artifact produced by this control should only be restored by an ECM build that has it, or a newer one. `schema_version` did not move, because the artifact's structure is unchanged, so nothing refuses the combination. Restoring such an artifact with an older build has three known gaps, none retroactively fixable: the destination's own accounts are not preserved (the restoring admin can be signed out of their own instance and dropped at the setup wizard), alert-method `username` and `chat_id` are written in as the literal sentinel, and a whole-value sentinel `config` is left literal so that alert method stops sending until reconfigured. Roll back to a pre-upgrade artifact rather than restoring a newer one onto an older build.

---

## 9. Addendum B: Outbound Destinations & SSRF (v0.18.0 cloud upload + v0.18.1 sync)

**Added:** 2026-05-12 · **Bead:** `enhancedchannelmanager-0i2vt.3` (Phase 0) · **Feeds:** `0i2vt.4` (SyncTarget/CloudTarget models), `0i2vt.5` (first-run SSRF wizard + always-on denylist + DNS-rebinding mitigations), `0i2vt.8` (cloud upload wiring) · **Source:** ADR-012 D4 + "Security Mandatory #2, #3, #5"

### 9.1 Scope

v0.18.0 adds **operator-configurable outbound destinations**:

- **CloudTarget**: S3 (incl. S3-compatible: MinIO, Wasabi, B2, where the operator supplies the endpoint URL), WebDAV (*operator supplies the base URL*), OneDrive, Dropbox, Google Drive. Adapters already scaffolded in `backend/cloud_storage/` (`s3_adapter.py`, `onedrive_adapter.py`, `dropbox_adapter.py`, `gdrive_adapter.py`, `factory.py`).
- **SyncTarget**: a second Dispatcharr instance's URL (reserved for v0.18.1 sync; schema lands in v0.18.0 per ADR-012).

**The threat:** an authenticated admin (or an attacker who has compromised an admin session)
can point ECM at an arbitrary URL, and ECM, running *inside the operator's network*, will make
the request. That is a classic **server-side request forgery (SSRF)** primitive: hit the cloud
metadata endpoint (`169.254.169.254`) for instance credentials, scan/poke internal infrastructure
(routers, databases, other containers), or use ECM as an unwitting proxy. The §1–§7 import model
only ever talked about *inbound* archives and the *one* admin-configured local Dispatcharr (ADR-004
treated as trusted, sync-to-third-party explicitly out of scope). v0.18.0 changes that: ECM now
deliberately makes outbound requests to **destinations the operator typed in**, including
*endpoint URLs* (not just API tokens) for S3-compatible and WebDAV. Every one of those URLs is
attacker-influenceable and must be validated.

ADR-012 D4 resolves the policy: a **first-run wizard** lets the operator pick *LAN-friendly*
(RFC1918 + RFC 6598 shared space + loopback allowed: this is the default, because operators back up to LAN, VPN, and carrier-shared peers such as a NAS or second ECM instance on
`192.168.x.x`) vs *public-only* (private ranges blocked). **Regardless of that choice**, an
always-on denylist blocks metadata/link-local/special-use ranges outside that explicit peer band, and DNS-rebinding mitigations are
mandatory. This addendum is the threat-model backing for `0i2vt.5`; §9.4 hands the concrete
validator requirements to that bead.

**Trust boundary added:** **ECM → arbitrary operator-supplied URL** (cloud APIs, S3-compatible
endpoints, WebDAV servers, Dispatcharr-B). This is a new boundary; treat the destination as
untrusted *and* treat the act of connecting as a capability that must be gated.

### 9.2 STRIDE rows: Outbound Destinations

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation | Status | Sev |
|---|---------|--------|--------|-----------------|------------|--------|-----|
| B1 | CloudTarget config | Tampering / Spoofing | Operator-supplied S3/WebDAV **endpoint URL** is malicious | Admin (or hijacked admin session) sets the "S3 endpoint" to `http://169.254.169.254/` or `http://10.0.0.5:6379/` ("MinIO on the LAN"). ECM dutifully connects on the next backup upload. | Shared SSRF validator (§9.4) on **every** outbound URL before connect, endpoint URLs included, not just tokens. No adapter issues a raw `httpx`/`requests` call that bypasses the validator (single chokepoint). | to-build (`0i2vt.5` + `0i2vt.8`) | **High** |
| B2 | Any outbound URL | Information Disclosure / EoP | SSRF to cloud metadata / link-local / internal ranges | Destination resolves to `169.254.169.254` → ECM fetches the instance's IAM credentials and (because it's a "backup destination") may even *upload to it* or surface the response in an error. Or a public-only destination resolves to `127.0.0.1:<admin-port>` / `100.64.x.x` / `[::1]` and ECM is now an internal-network scanner/proxy. | **Always-on denylist** (regardless of wizard choice): `169.254.0.0/16` (incl. IMDS), `0.0.0.0/8`, `fc00::/7`, `fe80::/10`, `fec0::/10`, `::ffff:0:0/96`, non-`http(s)` schemes: *all rejected in both modes*. Loopback (`127.0.0.0/8` + `::1`), RFC1918, and RFC 6598 Shared Address Space (`100.64.0.0/10`) are rejected in public-only mode and allowed in LAN-friendly. (§9.4 item 2.) | to-build (`0i2vt.5`) | **High** |
| B3 | Any outbound URL | Tampering | DNS rebinding / TOCTOU: hostname validated, then re-resolves to a denied IP at connect time (or a redirect lands on one) | Attacker controls `evil.example.com`; first DNS lookup (validation) returns a public IP, second lookup (the actual connect) returns `169.254.169.254`. Or the destination replies `302 → http://169.254.169.254/latest/meta-data/`. The naïve "validate the hostname then `requests.get(hostname)`" pattern is bypassed. | **Resolve-then-connect-by-IP:** resolve once, validate *every* returned A/AAAA against the denylist (any denied record → reject the whole request), connect by the validated IP with the hostname as SNI/`Host:`. **Redirect re-validation:** 3xx to a new host is not auto-followed; re-run the full denylist + resolve-by-IP on the redirect target, and reject `https→http` downgrades. (§9.4 items 3–4.) | to-build (`0i2vt.5`) | **High** |
| B4 | Cloud adapters | EoP / bypass | An adapter (`s3_adapter.py` etc.) makes a raw HTTP call that skips the validator | The S3 SDK or a WebDAV client library opens its own connection straight from the endpoint URL string, never touching ECM's validator → SSRF protection is theatre. | The validator is the **single chokepoint**: either (a) all adapters route through one ECM-owned HTTP client that validates on every request and pins to the resolved IP, or (b) where an SDK insists on doing its own DNS, ECM pre-resolves + validates and hands the SDK an IP + `Host:` override. CI test: grep adapters for direct `httpx`/`requests`/`urllib` calls; any hit fails the build unless it's the validated client. (§9.4 item 1.) | to-build (`0i2vt.8`) | High |
| B5 | CloudTarget/SyncTarget creds | Tampering / Repudiation | Scheduled backup uses a *stale* (rotated/revoked) cloud token; or `insecure=true` is set with no audit trail | (a) Admin rotates the Dropbox token; a backup schedule created earlier still fires with the old token, silently failing or, worse, hitting a now-attacker-controlled account that reused the old token. (b) Admin sets `insecure=true` for a self-signed WebDAV box; later that box is MITM'd and nobody knows ECM was talking to it without TLS verification. | (a) `credential_version` + `token_revoked_at` columns on the model (`0i2vt.4`); scheduler captures version at enqueue, worker re-checks at execute, aborts with WARN + audit row on mismatch (Security Mandatory #5). (b) `verify=True` default; `insecure=true` per-target escape hatch writes a `journal.log_entry` (`category='backup_outbound'`, host, `tls_verified=false`) on **every** request, not once at config time. (§9.4 items 5–6, checklist 25–26.) | to-build (`0i2vt.4` + `0i2vt.8`) | Med |
| B6 | First-run wizard / settings | EoP / misconfig | Wizard default or a later settings change weakens the denylist | Operator clicks through the wizard picking "LAN-friendly" without reading; or a future settings page lets someone add `169.254.169.254` to an allowlist "to scrape metadata for monitoring". | The always-on denylist is **not** subject to the wizard choice or any allowlist: it is unconditional in code, with no settings key that can disable it. The wizard choice only toggles the RFC1918/loopback band. A test asserts the always-on entries are rejected in *both* modes and that no settings key removes them. (§9.4 item 2.) | to-build (`0i2vt.5`) | Med |

### 9.3 Mitigations summary (Addendum B)

1. **One SSRF chokepoint.** A single ECM-owned validated HTTP client (or pre-resolve+IP-pin shim for SDKs that won't cooperate). CI grep forbids raw outbound calls in the adapters. (Checklist 21, 24; §9.4 item 1.)
2. **Always-on denylist, unconditional.** Metadata/link-local/IPv6-special/non-http(s) destinations outside the explicit peer band are rejected in *both* wizard modes; no settings key or allowlist can re-enable them. IPv6 loopback `::1` is **not** in this set: it sits in the toggled band described in item 3. (Checklist 22; §9.4 item 2.)
3. **LAN-friendly is the only knob.** The wizard toggles RFC1918, RFC 6598 Shared Address Space (`100.64.0.0/10`), and loopback (`127.0.0.0/8` and `::1/128`); default LAN-friendly per ADR-012 D4. RFC 6598 is special-use and not globally routable, but is used for carrier and overlay-network peer addressing; this permission does not expand to other special-use ranges. (Checklist 22; §9.4 item 2.)
4. **Resolve-then-connect-by-IP.** Resolve once, validate all records, connect by validated IP with hostname as SNI/`Host:`. Closes DNS-rebinding TOCTOU. (Checklist 23; §9.4 item 3.)
5. **Redirect re-validation + no scheme downgrade.** 3xx to a new host re-runs the full check; `https→http` rejected. (Checklist 24; §9.4 item 4.)
6. **TLS verify on; insecure flag is audited per request.** `verify=True` default; `insecure=true` → audit row every time. (Checklist 25; §9.4 item 6.)
7. **Credential-freshness binding.** `credential_version` + `token_revoked_at`; enqueue-time capture, execute-time re-check. (Checklist 26; §9.4 item 5.)

### 9.4 Phase-1 handoff: SSRF validator requirements (for `0i2vt.5`)

`0i2vt.5` MUST deliver a validator meeting **all** of the following. (`0i2vt.5`'s own description
already lists most of this; restating here so the threat model is the single source the bead's
acceptance criteria check against. Where `0i2vt.5` says "extends bead `zbt74` validator pattern":
that pattern covers scheme + IPv4 RFC1918; the items below add IPv6, RFC 6598, IMDS, and
DNS-rebinding coverage that `zbt74` does not.)

1. **Single validated outbound client / chokepoint.** All outbound HTTP(S) from the backup/sync
   subsystem goes through one validated client. Cloud SDKs that do their own DNS get pre-resolved
   IPs + `Host:` overrides from the validator. CI test forbids raw `httpx`/`requests`/`urllib`
   calls in `backend/cloud_storage/` adapters and the sync code.
2. **Scheme allowlist + always-on IP denylist + wizard-toggled band.**
   - Scheme: only `http` and `https`. Reject `file`, `ftp`, `gopher`, `data`, `dict`, etc.
   - Always-on deny (both wizard modes, no opt-out, no settings override, no allowlist):
     `0.0.0.0/8`, `169.254.0.0/16` (incl. `169.254.169.254/32` IMDS),
     `fc00::/7` (ULA), `fe80::/10` (link-local), `fec0::/10` (site-local),
     `::ffff:0:0/96` (IPv4-mapped: must be unwrapped and re-checked against the IPv4 rules so
     `::ffff:169.254.169.254` is caught), `::/128`, multicast (`224.0.0.0/4`, `ff00::/8`).
   - Wizard-toggled: loopback (`127.0.0.0/8` and `::1/128`), RFC1918 (`10/8`, `172.16/12`,
     `192.168/16`), and RFC 6598 Shared Address Space (`100.64/10`): *allowed* in LAN-friendly
     (default), *rejected* in public-only. There is
     still no LAN carve-out for an IPv6 *network*: ULA `fc00::/7` and link-local `fe80::/10`
     stay always-on denied.

     **Amendment (2026-07-31, GH #754 / bead `enhancedchannelmanager-0yh70`).** As originally
     written this item contradicted itself: the always-on bullet listed `::1/128` while the
     wizard-toggled bullet read "`127.0.0.0/8` and RFC1918 ... + IPv6 equivalents", and `::1`
     *is* the IPv6 equivalent of `127.0.0.0/8`. Resolved in favour of the toggled bullet, for
     loopback ONLY, because (a) `::1` and `127.0.0.1` are the same trust domain (this host's
     own loopback interface), so denying one while permitting the other blocks no attacker
     capability; and (b) Docker's generated `/etc/hosts` maps `localhost` to BOTH `::1` and
     `127.0.0.1`, so with item 3's reject-if-any-record-denied rule an always-on `::1` makes
     `http://localhost:<port>` unusable in LAN-friendly mode on every container that is not
     `network_mode: host`. That was the reported GH #754 failure: a first-run operator whose
     Dispatcharr shares a gluetun network namespace could not save the only address that
     reaches it. Link-local (incl. IMDS) was re-affirmed as always-on in the same change; it
     has no legitimate ECM use and is the highest-value target in the set.
3. **Resolve-then-connect-by-IP (DNS-rebinding mitigation).** Resolve the hostname once; validate
   **every** returned A and AAAA record against the rules; if **any** record is denied, reject the
   whole request (do not "pick the allowed one"). Connect by the validated IP, with the original
   hostname as TLS SNI and `Host:` header. No second DNS lookup between validation and connect.
4. **Redirect handling.** Do not transparently follow 3xx to a different host. Either block all
   cross-host redirects, or re-run steps 2–3 on each redirect target before following. Reject any
   redirect that downgrades `https` → `http`. Cap redirect chain length (≤ 5).

   *Scope note (bead `enhancedchannelmanager-iyvl9`, 2026-08-24).* The downgrade refusal is
   unchanged for every destination this document covers — cloud backup targets, sync targets,
   EPG sources. `security/ssrf.py::validate_redirect` is shared with the **stream-probe** path,
   which is outside this threat model's scope, and that one caller may waive the downgrade clause
   by passing `SchemeDowngrade.ALLOW_STREAM_PROBE`. It waives nothing else: steps 2–3 and the
   chain cap still apply. The waiver is a keyword argument defaulting to `REFUSE`, so no
   destination in scope here can acquire it without an explicit code change, and
   `tests/security/test_probe_scheme_downgrade.py` fails if any module other than the prober
   names it. Rationale: XC providers 302 onto a plain-HTTP edge and serve the media over HTTP
   there regardless, so the media is already unencrypted and the redirect target carries no
   credentials — unlike the credentialed APIs in scope here, where a downgrade is a real loss.
5. **Credential-freshness binding (with `0i2vt.4`).** Honour `credential_version` /
   `token_revoked_at`: scheduler captures `credential_version` at enqueue; worker aborts (WARN +
   `journal.log_entry`) if it changed or `token_revoked_at` is set at execute time.
6. **TLS posture.** `verify=True` default. Optional per-target `insecure=true`; when set, every
   outbound request with it logs a `journal.log_entry` (`category='backup_outbound'`, target id,
   host, `tls_verified=false`).
7. **First-run wizard.** Appears on first run; records the LAN-friendly vs public-only choice;
   default = LAN-friendly (ADR-012 D4). The choice is re-editable in settings, but editing it can
   only move the *RFC1918/loopback band*; it can never touch the always-on denylist (item 2).
8. **Regression corpus (mandatory, ships with `0i2vt.5`).** Covers, at minimum: each always-on
   denied range (v4 and v6); `::ffff:169.254.169.254` and other IPv4-mapped representations of
   denied addresses; unicode/punycode hostnames that decode to a denied target; a two-A-record
   response (one allowed, one denied) → rejected; a resolution that changes between validate and
   connect → connection still goes to the validated IP; a `302 → http://169.254.169.254/...`
   redirect → blocked; an `https → http` redirect → blocked; each non-http(s) scheme → rejected;
   RFC1918 allowed in LAN-friendly / rejected in public-only.

### 9.5 Residual risk (Addendum B)

- **Residual: authenticated-admin abuse (Low, accepted).** An admin can still configure a backup
  destination that is *attacker-controlled but a perfectly valid public host* and exfiltrate the
  (redacted, per Addendum A) backup there. The SSRF validator stops ECM from hitting *internal*
  and *metadata* targets; it cannot stop a legitimate admin from sending a backup to a public S3
  bucket they shouldn't. This is inherent to "operator configures their own backup destination"
  and is bounded by the admin-only gating + the audit row on every backup (Addendum A row A5).
  No further mitigation proposed for v0.18.0.
- **Residual: SDK DNS behaviour (Low/Medium until verified).** Item 1's "pre-resolve + `Host:`
  override for SDKs" assumes the boto3 / Dropbox / Graph / WebDAV clients can be made to connect
  by IP. If one cannot (e.g., SNI/cert validation that insists on the hostname *and* does its own
  resolution), that adapter has a residual rebinding window. **Action for `0i2vt.8`:** verify each
  adapter's HTTP layer can be IP-pinned; if not, document the gap and consider an egress-proxy
  shim. Re-rate to Medium if any adapter can't be pinned.
- **Residual: IPv6 / new special-purpose ranges (Low).** IANA adds special-purpose ranges over
  time; a future reserved range not in item 2's list would not be denied. Mitigated by using the
  Python `ipaddress` module's `is_private` / `is_link_local` / `is_reserved` / `is_loopback` /
  `is_multicast` properties as a *backstop* in addition to the explicit CIDR list, so the validator
  fails closed on categories even if a specific new prefix isn't enumerated.
- **Residual: time-of-day DNS for long-running uploads (Low).** A multi-GB upload holds a
  connection open for a long time; the validated IP is fixed for that connection (good), but if
  the connection drops and the client retries, the retry must re-run validation, not reuse a
  cached hostname. **Action for `0i2vt.8`:** retries go back through the validator.

---

## 10. Addendum C: Whole-Artifact Passphrase Encryption (v0.18.0 opt-in cred-carrying backup)

**Added:** 2026-06-17 · **Bead:** `enhancedchannelmanager-u81kh` (Phase 1, build-last/deferrable) ·
**Crypto design:** spike `enhancedchannelmanager-0zrse` (closed: engineer + security + code-reviewer,
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
deliberate, PO-accepted trade, but it is exactly why the construction below (KDF strength, AEAD, no
plaintext on failure) and the UX gates (min passphrase, unrecoverable acknowledgement) are mandatory,
not nice-to-haves. The default path does **not** make this trade; only the explicit
"include credentials for migration" opt-in (which **requires** a passphrase) does.

**New primitive: D3/D12 reconciliation.** This is a **new crypto primitive**, intentionally
**parallel to** the existing Fernet primitive in `backend/cloud_storage/crypto.py` (which is
static-key, whole-artifact-in-RAM, and cannot stream). Per ADR-012's own D3 note, **D12 partially
supersedes D3**: **D3 still governs at-rest credential columns** (the `SyncTarget`/`CloudTarget`
Fernet-encrypted fields, Addendum A row A3); **D12 governs this opt-in whole-artifact path**. The two
coexist. `crypto.py` is **not** reused here.

**Trust boundary added:** **ECM → encrypted artifact → operator's hands / cloud → ECM (decrypt on
restore).** The ciphertext crosses the same untrusted egress boundary as a redacted artifact
(Addendum A), but unlike the redacted artifact it **contains live credentials**, so the encryption
must be the only thing standing between the artifact and full credential disclosure.

### 10.2 Construction (from spike `0zrse`, build-ready)

> **Implementation status (2026-06-19): BUILT (`u81kh`).** The construction below is
> implemented in `backend/dbas/artifact_crypto.py` (the new primitive: scrypt + chunked
> ChaCha20-Poly1305, authenticated cleartext header, structural no-oracle), wired at exactly the
> two seams: the encrypt stage in `routers.backup.build_backup_artifact` (opt-in `passphrase` +
> `include_credentials` + `acknowledge_unrecoverable`) and the decrypt-at-ingest gate in
> `tasks.dbas_restore.DbasRestoreTask._decrypt_gate`. Dep choice settled at build start:
> **`cryptography`-only** (0 new deps), per PO. The passphrase never touches a log line or the
> journal audit row (task-parameter redactor in `task_engine`). Encrypted backups are **manual-run
> only** (a passphrase is never persisted to the schedule store). The C1–C6 rows below read
> "to-build" historically; all are now built under `u81kh` except the **operator-facing
> `acknowledge_unrecoverable` checkbox UX (C6)**, whose *gate* is API-mandatory today (the build
> refuses without the ack) and whose *checkbox* lands with the DBAS backup/restore UI wiring.
> Tests: `backend/tests/dbas/test_artifact_crypto.py` (primitive),
> `backend/tests/routers/test_dbas_passphrase_encryption.py` (both seams),
> `backend/tests/unit/test_task_engine_param_redaction.py` (passphrase log/journal scrub).

- **KDF:** **scrypt**, **N ≥ 2¹⁵** (floor), r=8, p=1; **per-artifact random salt**. KDF parameters and
  salt are stored in a **cleartext, authenticated header** (so a future ECM, or the `0i2vt.17`
  version check, can read them before attempting decryption).
- **Cleartext authenticated header:** `magic`, **`format_version`** (the *encryption-envelope* version),
  KDF params, salt, AEAD id, chunk size. **`format_version` is SEPARATE from the backup
  `schema_version`** so that ECM `.17`-style version checks can validate the envelope **pre-decrypt**
  and refuse an unsupported version without a passphrase. The header is covered by the AEAD's AAD
  (below), so it cannot be tampered with undetected.
- **AEAD:** **chunked streaming** AEAD: **ChaCha20-Poly1305 or AES-256-GCM**. **Per-chunk nonce**
  (random base XOR a counter). Each chunk's **AAD binds the header + the chunk index + the `is_final`
  flag**, which makes chunk **swap, reorder, and stream truncation** detectable (any such manipulation
  fails authentication).
- **REDACT-THEN-ENCRYPT, enforced structurally:** redaction runs **inside** the build path and is not
  skippable; the `include_credentials` opt-in only **re-injects the approved credential set** before
  encryption. There is no "skip redaction and encrypt instead" branch.
- **Off-event-loop, streaming:** KDF and encrypt/decrypt run **off the event loop** and **stream to
  temp files** (the `.7` builder's in-memory `BytesIO` assembly becomes tempfile-streaming, required
  for the D8 logo-streaming memory model regardless).
- **Phase-2 decrypt is a single ingest gate, not an 11-bead fan-out:** decrypt happens **once** at
  restore ingest, before the per-category importers run; `.10`–`.15` need **zero** crypto changes.
- **Passphrase policy:** **minimum 12 characters**, enforced at the API boundary.

### 10.3 STRIDE rows: Passphrase Encryption

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation | Status | Sev |
|---|---------|--------|--------|-----------------|------------|--------|-----|
| C1 | Encrypted artifact | Information Disclosure | The opt-in path ships an **unredacted** artifact, so a weak/brute-forceable passphrase exposes every credential | Operator picks "include credentials" with a 4-char passphrase; artifact lands in Dropbox; offline brute-force of a low-entropy passphrase recovers all M3U/EPG/SMTP/cloud creds. | Strong KDF (**scrypt N ≥ 2¹⁵**) raises per-guess cost; **min 12-char passphrase** (API-enforced, checklist 29); redact-by-default stays default so this surface only exists when the operator opted in. | to-build (`u81kh`/`0i2vt.7`) | **High** |
| C2 | Build path | Information Disclosure / Tampering | "Encrypt instead of redact" path skips structured redaction, or a non-passphrase switch preserves the approved credential set | A code path lets `include_credentials=true` preserve recognized credential fields or credential-bearing URL values without a passphrase, or disables structured redaction "to make migration work." | **REDACT-THEN-ENCRYPT is structural** (checklist 28): structured redaction is inside the build path, not skippable; `include_credentials` only re-injects the approved credential set, and **only** with a passphrase set. No "disable redaction" switch (consistent with Addendum A). Test: `include_credentials=false` + passphrase → decrypt → recognized credential fields and credential-bearing URL values are replaced. Operator-authored free text remains outside this guarantee. | to-build (`0i2vt.7`) | High |
| C3 | KDF / AEAD construction | Tampering / Spoofing | Weak KDF, missing AEAD, or chunk swap/reorder/truncate yields forged or partial plaintext | Attacker truncates the ciphertext to drop a "force-reset" record, or reorders chunks, or the construction uses an unauthenticated cipher so a flipped bit silently alters a restored value. | **scrypt N ≥ 2¹⁵** KDF; **AEAD** (ChaCha20-Poly1305 / AES-256-GCM) per chunk; **per-chunk nonce**; **AAD binds header + chunk-index + is_final** → swap/reorder/truncation all fail authentication (checklist 29). New primitive parallel to Fernet, off-event-loop, streaming (checklist 31–32). | to-build (`u81kh`) | **High** |
| C4 | Cleartext header | Tampering / DoS | Header tampered, or version confusion forces a wrong/failed decode | Attacker edits the cleartext KDF params (e.g., lowers N) to weaken the derived key, or sets a `format_version` the target mishandles. | Header is **authenticated** (covered by AEAD AAD) → param tampering fails decryption. **`format_version` is separate from `schema_version`** and is checked **pre-decrypt** (checklist 30), so an unsupported envelope is refused cleanly (user-facing "unsupported version"; full detail server-side) rather than crashing or mis-decoding. | to-build (`u81kh`/`0i2vt.17`) | Med |
| C5 | Decrypt path | Information Disclosure (oracle) | A wrong-passphrase vs corrupted-artifact distinction, or an early plaintext release, leaks an oracle | Attacker probes whether a guessed passphrase is "closer" by observing different errors, partial output, or a verified-prefix before full authentication. | **No wrong-passphrase oracle: STRUCTURAL** (checklist 33): wrong passphrase and corrupt artifact raise an **identical exception** and release **zero plaintext** on any failure; never emit a verified prefix before whole-artifact auth completes. **Accepted residual:** spike `0zrse` demonstrated a ~15 ms size-dependent **timing** residual (wrong pass fails at chunk 0; corrupt-last-chunk fails at chunk N), which is **accepted** for an offline artifact (see §10.4). Do **not** assert wall-clock equivalence in tests. | to-build (`u81kh`) | Med |
| C6 | Operator UX | Repudiation / availability | Operator forgets the passphrase → artifact is **permanently unrecoverable** | Operator encrypts a migration backup, loses the passphrase, and later cannot restore: total data-availability loss for that artifact, with no ECM-side recovery. | **Accepted risk**: there is intentionally no recovery/backdoor (a backdoor would defeat C1/C5). Compensating control: a **hard-gate UX warning** (checklist 34): an explicit `acknowledge_unrecoverable` checkbox the operator must tick (not a tooltip) before an encrypted backup is produced. | to-build (`u81kh`) | Med |

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
7. **No wrong-passphrase oracle: structural, not wall-clock.** Identical exception + zero plaintext on
   failure; the demonstrated ~15 ms timing residual is an accepted offline residual. (Checklist 33.)
8. **Lost-passphrase hard-gate UX.** `acknowledge_unrecoverable` checkbox; no recovery path.
   (Checklist 34.)

### 10.5 Residual risk (Addendum C)

- **Residual: size-dependent timing oracle (~15 ms, Low, accepted).** Spike `0zrse` **demonstrated**
  that a wrong passphrase fails at the *first* chunk while a corrupted *last* chunk fails after
  streaming the whole artifact, producing a measurable, size-dependent timing difference. The no-oracle
  property is therefore specified as **structural** (identical exception + zero-plaintext-on-failure +
  never release a verified prefix), **not** as wall-clock equivalence. For an **offline** artifact the
  attacker already possesses (no online query channel, no rate-limit to defeat), this timing channel
  yields negligible advantage over offline brute force, which the KDF already gates. **Accepted** for
  v0.18.0; the alternative ("fail at end" to flatten timing) is available if a future use makes the
  artifact's decryption an online oracle. Do not paper over it with a flaky stopwatch test.
- **Residual: unredacted creds protected solely by the passphrase (Medium, accepted, opt-in only).**
  By design, the opt-in cred-carrying artifact stakes every credential on the operator's passphrase
  (§10.1). Mitigated by the strong KDF, the 12-char minimum, and the fact that the default path never
  makes this trade. A weak (but ≥12-char) passphrase remains the operator's risk. No KMS / escrow for
  the MVP (consistent with D3's no-KMS posture).
- **Residual: lost passphrase = unrecoverable (Low/accepted, availability, not confidentiality).**
  Intentional: no recovery/backdoor. Bounded by the hard-gate acknowledgement (C6). This is an
  availability trade the operator explicitly accepts per artifact.
- **Residual: build-time dependency choice deferred.** Spike `0zrse` left one open build-kickoff
  decision: implement framing within `cryptography` (hand-rolled chunk framing, zero new deps, more
  maintenance) **vs.** add a vetted streaming AEAD library (PyNaCl `secretstream` / `age`; security's
  preference, removes framing risk, adds a dependency + supply-chain review). **Not a threat-model
  decision; settle at `u81kh` build start.** Either choice must still satisfy the construction in §10.2.

---

## 11. Addendum D: Cross-Instance Live Sync (v0.18.1 one-way A→B config replication)

**Added:** 2026-06-19 · **Epic:** `enhancedchannelmanager-i39wu` · **Architecture:** [ADR-013](../adr/ADR-013-cross-instance-live-sync.md) · **Crypto/feasibility design:** spike `enhancedchannelmanager-xp6mp` (closed: architect + engineer + security + DBA + SRE; reuse-seam demonstrated). **This addendum GATES build** the same way Addendum C gated `u81kh`: no sync build bead opens until §11 is reviewed and the D2/D3/D7 hard lines are PO-ratified (they are; see ADR-013 S2/S3).

### 11.1 Scope and posture

ADR-013 ships **one-way** A→B continuous config replication: ECM-A reads its configuration and **writes** it into a remote Dispatcharr-B on a schedule, so B converges to A. This is the first ECM surface that makes **continuous, unattended, outbound WRITES into a second live instance**. It is *not* symmetric to DBAS restore: DBAS restore is the operator pulling an archive *onto themselves*; sync is **ECM-A reaching out and writing into B**, so the blast radius is a *remote* instance and the actor for B's safety is A's outbound code, not B's admin. That asymmetry drives every row below.

**The load-bearing posture facts (PO-ratified, ADR-013):**
- **One-way only** (S2). Bidirectional is a separate epic with its own inbound-write trust boundary on A.
- **Redact-by-default / topology only** (S3/S7). The sync payload carries **no** secrets: same shared `_REDACT_KEYS` denylist as the backup ZIP. Secret *migration* stays the `u81kh` encrypted-artifact path, not a continuous live channel.
- **Users never sync** (S3). Continuous one-way push of the `users` category would repeatedly overwrite B's privilege flags / lock out B's operator.

The hard primitives already exist and were designed for this: `backend/security/ssrf.py` names "a second Dispatcharr instance" in its own docstring; the `SyncTarget` model's credential-freshness columns + same-txn version-bump listeners are byte-for-byte the `CloudStorageTarget` contract; Addendum B §9 already wrote the SSRF + credential-freshness contract anticipating v0.18.1. **The risk is concentrated in what enters the payload (D2/D3) and the direction (D7), both design decisions, not missing tech.**

### 11.2 STRIDE rows: Cross-Instance Live Sync

| # | Surface | STRIDE | Threat | Mitigation | Status | Sev |
|---|---------|--------|--------|------------|--------|-----|
| **D1** | `SyncTarget.base_url` | Spoofing / EoP | An operator-typed `base_url` points the recurring, unattended sync at IMDS / an internal host / loopback: a scheduled internal scanner / credential thief / proxy. | `ssrf.py` `validate_outbound_url()` on **every** request (initial connect, each pagination page, each write, each retry, each redirect), at **execute time** not config-save time (DNS-rebinding); resolve-then-connect-by-IP; reject-whole-request if any A/AAAA is denied; `https→http` downgrade refusal. **The CI grep that forbids raw `httpx`/`requests` in `cloud_storage/` adapters MUST extend to the sync module** (`test_ssrf_chokepoint_guard.py` currently scans only `cloud_storage/`; without the extension, SSRF is unenforced for the new path). | to-build (reuse `0i2vt.5`; guard-extension = AC on `1t3al`) | **High** |
| **D2** | Sync payload | Information Disclosure | Replicating "config" naively streams live M3U/EPG/XC/SMTP/cloud secrets to B on a schedule, worse than the one-shot artifact (no operator/passphrase in the loop per fire; a wrong/typo'd `base_url` leaks every credential, silently, every cycle). | **Redact-by-default** via the shared `_REDACT_KEYS` deep redactor before serialize: topology only, **no plaintext-cred path in v0.18.1** (PO-ratified, ADR-013 S3/S7). Cred-carrying continuous sync is **not shipped**; secret migration stays the `u81kh` passphrase-artifact path. Test: assemble a sync payload from source state with a known M3U/SMTP/cloud secret; assert none appears in the serialized bytes (reuse Addendum A `A1`). | to-build | **High** |
| **D3** | `users` category | Elevation of Privilege | Continuous one-way push of `users` repeatedly overwrites B's `is_superuser`/`is_staff`/`user_level` from A: an operator-lockout / privilege-escalation primitive under automation. `password_hash` is non-transportable anyway (spike `tsfv0`: Dispatcharr exposes `password` write-only). | **`users` is a permanent, code-enforced never-sync exclusion** (S3): a single shared never-sync constant imported by the payload assembler AND its test, treated like the always-on SSRF denylist (unconditional, no settings key). Test asserts the `users` category is never assembled into a sync payload. | to-build (enforced exclusion) | **High** |
| **D4** | Category registry | Tampering | An unknown or secret-bearing category is synced. | Category **allowlist** (topology only; the S3 set); reject unknown keys; secret fields stripped pre-serialize via the shared denylist (D2). | to-build | Med |
| **D5** | Stale credential | Tampering / Repudiation | A scheduled sync fires after B's token was rotated/revoked and writes with the stale token. | `credential_version` + `token_revoked_at` (schema present, listeners present) → capture `credential_version` at enqueue (task-config JSON, no new column), re-read FRESH + `token_revoked_at` hard-stop at execute, **abort + WARN + `journal.log_entry`** (`category='sync_outbound'`, `aborted=stale_credential`) on change/revoke. The version bump is gated on `credentials` being dirty, so a rename/`enabled` toggle does not invalidate a live schedule (verified `export_models.py`). Mirrors `dbas_backup` `_check_credential_freshness` verbatim. | to-build (schema exists) | Med |
| **D6** | TLS | Tampering | `insecure=true` MITM on the continuous channel. | `verify=True` default; `insecure=true` writes a `journal.log_entry` audit row (`tls_verified=false`) **on every cycle** (not once at config time); **forbidden-by-construction if the payload is ever non-redacted** (moot while redact-by-default holds: topology has no creds to expose; the coupling is stated so a future cred-carrying toggle cannot silently combine with `insecure`). | to-build | Med |
| **D7** | Direction | Integrity / EoP | Bidirectional opens an **inbound-write boundary on A** (a remote instance becomes an authenticated writer into A) + last-write-wins/loop amplification. | **One-way A→B only** for v0.18.1 (ADR-013 S2, PO-ratified). Bidirectional is gated on a separate ADR + an inbound-authn design for A. | design-decision (one-way set) | **High** (if bidir) |
| **D8** | Partial failure | Integrity / Availability | Sync dies mid-replication → B in a half-written state surfaced as success. | **Idempotency is the recovery mechanism** (ADR-013 S8): upsert-by-stable-identity, so a partial/failed run is re-run to convergence next cycle. Reuse the `RollbackLedger` + the tri-state `RestoreOutcome` (never SUCCESS on mixed state); a `partial` run does not advance the freshness gauge (so the staleness alert fires on sustained partial drift). No partial state is reported as success. | to-build (reuse) | Med |
| **D9** | Audit | Repudiation | No trail of what A pushed to B, when, with what result. | `journal.log_entry` per sync run: target id, categories, counts, `redaction_mode`, result, request id (mirror Addendum A `A5`). Per-cycle `insecure` audit row (D6). | to-build | Med |
| **D10** | Trigger | DoS | A change-driven trigger storms B (every edit → a sync). | **Scheduled-interval default** over change-driven for v1 (ADR-013 S6); the `ALREADY_RUNNING` overlap guard prevents a run overlapping itself; change-driven (if ever added) requires debounce/coalesce + rate-cap. | to-build | Low |

### 11.3 Mitigations summary (Addendum D)

1. **One-way only.** A is system-of-record; bidirectional is a separate epic with a separate threat model. (D7 / ADR-013 S2.)
2. **Redact-by-default, PROVIDER CREDENTIALS EXCEPTED (amended 2026-08-22 — see §11.6).** The shared `_REDACT_KEYS` denylist still strips every secret field before serialize for every category *except* `m3u_accounts` and `epg_sources`, whose credential fields and credential-bearing addresses are transmitted deliberately on every cycle under the PO's ruling. ECM's own settings secrets, alert-method secrets and target credentials are unchanged. (D2 / S3′ / S7′.)
3. **Users never sync: enforced, not scoped.** A shared never-sync constant + a test that the payload assembler refuses the `users` category and the credential columns. (D3 / S3.)
4. **SSRF on every request, execute-time, and the CI guard extends to the sync module.** Without the guard extension the chokepoint is theatre for the new path. (D1.)
5. **Credential-freshness at fire time.** Capture-at-enqueue / re-check-at-execute / abort+audit; mirrors the shipped CloudStorageTarget contract. (D5.)
6. **TLS verify default; per-cycle insecure audit; insecure WARNED, not forbidden (amended 2026-08-22 — §11.6).** The payload does carry provider credentials now, and the PO removed the refusal in their own terms. What remains is a warning on every credential-carrying cycle, an audit row recording `tls_verified=false`, and a badge on the target. (D6.)
7. **Idempotency is recovery.** Upsert-by-stable-identity + reuse the rollback ledger + tri-state outcome; retry converges, no new saga machinery. (D8 / S8.)
8. **No new crypto.** TLS remains the complete in-transit control and ADR-012 D3's "no new crypto surface" still holds — but the payload is no longer topology-only, so TLS is now protecting a live provider credential rather than a deployment shape, on every cycle. That raises what a failure of it costs; it does not add a control. (D2/D6, amended — §11.6.)

### 11.4 Residual risk (Addendum D)

- **Residual: topology disclosure to B (Low, accepted).** Even redacted, the sync payload reveals the deployment shape (channel names, source URLs minus creds). Inherent to replicating config at all; B is an operator-trusted destination. Same accepted residual as the redacted backup artifact (Addendum A §8.4).
- **~~Residual: credential re-entry friction on B~~ — CLOSED 2026-08-22 (§11.6).** The operator no longer re-enters anything: provider credentials cross on every cycle. What replaced it is a confidentiality residual the PO has explicitly accepted — the replica is a place the provider credential lives — recorded in §11.6.
- **Residual: no live-B integration coverage at build time (tracked).** The build + unit/contract tests run against mocks/fakes; the live A→B round-trip + the live half of the test harness are gated on a reachable second Dispatcharr (bead `46pkq`). Flagged, not silently skipped.
- **Residual: `insecure=true` on a recurring channel — RE-RATED High, PO-ACCEPTED 2026-08-22 (§11.6).** The Low rating rested on the payload being topology. It is not: an insecure target receives a live provider credential in clear on every cycle. The refusal that §11.5 built was removed by PO ruling; the exposure is now warned, badged and audited, and the operator owns the mitigation.

### 11.5 Update 2026-08-22: one-time credential provisioning (bead `enhancedchannelmanager-t77qd`)

> **SUPERSEDED the same day by [§11.6](#116-update-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-115).**
> Everything in §11.5 analyses the one-time provisioning design that shipped in PR #908 and was
> removed two days later. It is kept as the record of what was weighed. **Rows D11-D16 as written
> below are void**; §11.6 re-rates each against per-cycle transmission and records which residuals
> the PO has ACCEPTED rather than mitigated. §11.5.4's five build gates are void with the build they
> gated.

§11.1–§11.4 above are the design record for a sync that **carries no credential at all**. This
section is the **current** description of the surface after [ADR-013](../adr/ADR-013-cross-instance-live-sync.md)'s
2026-08-22 amendment (decisions S10–S13, bead `enhancedchannelmanager-wd20y`, PO-ratified), and
where the two differ this one is authoritative.

**The §11 build gate applies to this section on the same terms.** §11 states that no sync build bead
opens until it is reviewed; that sentence now covers §11.5, and the `wd20y` build does not open until
the items in [§11.5.4](#1154-what-still-gates-the-build) are satisfied. An addendum left reading as
satisfied while the design underneath it has moved is the false-green class this document exists to
prevent.

#### What changed

ADR-013 S10 ratifies an **explicit, operator-initiated, audited, TLS-verified provisioning action**
that writes a provider credential onto the replica's provider accounts once, at sync-target setup.
The **per-cycle path is unchanged**: it still redacts, still carries topology only, and gains no
exemption and no "provisioning mode". Redact-by-default (D2) therefore remains a true and complete
description of *the cycle*, and stops being a true description of *the system*.

Two PO rulings of 2026-08-22 shape the risk and neither can be read off the decision text alone:

1. **Credentials are HARVESTED from A's own provider records, not typed by the operator.** The
   typed design had a human as an implicit review step — someone read the value before it crossed.
   That step is gone. Nothing below may carry a rating computed under the typed assumption.
2. **An explicit de-provision escape is permitted**, so a target *can* return to `insecure` after
   having held a credential. The architect recommended a permanent symmetric refusal; the PO ruled
   otherwise with that objection in front of them. What a de-provision cannot undo is a residual of
   this system now, recorded in §11.5.3.

Two later rulings of the same day complete the picture and are folded in below:

3. **Schedules Direct is IN scope with an operator-supplied value for its one unharvestable field.**
   The PO's product bar — *"this product doesn't work if it doesn't replicate everything to a second
   instance"* — governs, and the earlier draft's exclusion is withdrawn: impossible-to-harvest is not
   the same as out-of-scope. This is one field of typed input inside the closed set, request-scoped
   and never persisted, and it does not reopen the input model for anything else (row D15).
4. **The `insecure` refusal gates on recorded OR OBSERVED state** (row D16, closing bead
   `enhancedchannelmanager-3dmgr`).

ADR-013 also gained a **governing principle** on 2026-08-22 — a replica is a faithful copy;
everything replicates by default and every exclusion must be named and justified. It is a **scope**
principle and weakens no control in this addendum: the per-cycle redaction, the reachability guard
(D12), the TLS gate (D6/D16) and the never-sync `users` exclusion (D3) are untouched by it.
"Everything replicates" means the destination ends up faithful, not that any mechanism is licensed.

One further property of the harvest decides several ratings below and is stated once here. The
harvest is a new **write**, not a new **read**: `routers.backup._collect_credential_values` already
walks the raw gather on every cycle, by the same key sets the redactor uses, because that is what
makes the `msqf7` literal-match rule possible. A's process already holds every value a provisioning
would send, on every scheduled run, today. So the provisioning adds no new read surface — and it
removes the barrier that would have made a recurring push hard to build by accident, because the
values are already in the cycle's own memory. Under the typed design, making the cycle push
credentials was impossible without first inventing somewhere to persist them. Under the harvest it
is a call edge. **The feature's safety now rests on one structural guarantee where the typed design
had two**, and that is why D12 below is rated as it is.

#### 11.5.1 New STRIDE rows: one-time credential provisioning

| # | Surface | STRIDE | Threat | Mitigation | Status | Sev |
|---|---------|--------|--------|------------|--------|-----|
| **D11** | Provisioning action (A→B write) | Information Disclosure | A live provider credential is deliberately written to a second instance. B's database, backups, logs and its own generated stream URLs then hold it, and B may sit at a different site or trust level. The exposure is intended; the risk is that it is unbounded in *scope* (which fields, which categories) or *frequency*. | Closed set of exactly two entity categories (M3U accounts, EPG sources), enforced in code rather than by whatever the gather returns. Writable field set is **derived** from the redactor's own per-entity output (`strip_redaction_sentinels` → `value_at_path` against the raw record, ADR-013 INV-6), not from a maintained literal. Authenticated-admin actor only; one-time; TLS verification mandatory (D6/S11); harvested values — and the one operator-supplied Schedules Direct password — never persisted on A (INV-3); one journal row per attempt (D9/S13). | to-build (`wd20y`) | **High** |
| **D12** | Provisioning **code path** | Information Disclosure | **The one-time path becomes a recurring one.** The cycle already holds the values; making it push them is a single call edge, with no missing input and no operator keystroke absent to make the change conspicuous. A later "auto-heal stale credentials" convenience, or a well-meaning refactor that registers provisioning as an importer step, silently converts the whole design into the thing S3 forbids — and the run report would look normal. | **Not a payload control — a reachability control.** ADR-013 INV-2: the provisioning writer is not an `ImporterStep`, is absent from `sync_config_importer_steps()`, and is **not reachable by any call path** from `tasks.dbas_sync` / `tasks.dbas_sync_engine`, pinned by (i) a registry test in the idiom of the existing `SYNC_NEVER_CATEGORIES` test and (ii) a transitive import/reachability guard, the same idiom as row D1's chokepoint grep. A registry check alone is insufficient: a direct call bypasses the registry entirely. Detection backstop: a `sync_provision_credentials` journal row whose actor is the scheduler (D9). | to-build (`wd20y`) | **High** |
| **D13** | De-provision | Information Disclosure / Repudiation | A de-provision that did not actually clear B flips the marker anyway, and `insecure` becomes settable while B still holds a live credential — re-opening the unverified channel over which the per-cycle destination read carries that credential back (see D6, D16). A local flag flip is A's *belief* about B, and B is where the secret is. | ADR-013 INV-9: the clear is **attempted on B** over the same field set the provision wrote; the marker flips **only** if that write succeeded for every targeted account; any partial or total failure leaves the marker set, leaves `insecure` refused, and names the accounts still holding a credential. Audited as its own action type with a per-account success/failure breakdown (D9/S13). This is row D8's "no partial state is reported as success" applied to the safety control itself. | to-build (`wd20y`) | **High** |
| **D14** | Harvest scope | Information Disclosure | A harvest is a loop over records, and a loop widens by accident. A future category added to the sync, or a gather that starts returning more, silently enlarges what is provisioned — ECM's own settings secrets (`dispatcharr_api_key`, `emby_api_key`, `plex_token`, `smtp_password`, `telegram_bot_token`, `mcp_api_key`), alert-method secrets, cloud/sync-target credentials, or `dispatcharr_users`. "Provision the credentials" becomes "provision every secret A holds". | The provisioning category set is a **closed named set**, separate from and narrower than the sync category allowlist (row D4), enforced in code. Everything else stays outside by construction: `SYNC_NEVER_CREDENTIAL_COLUMNS`, `SYNC_NEVER_CATEGORIES`, `_SETTINGS_CREDENTIAL_FIELDS` and the alert-method keys are never provisioning inputs. INV-6's derived field set means a widened *gather* cannot widen the *write* on its own. | to-build (`wd20y`) | Med |
| **D15** | Schedules Direct reporting | Repudiation | **Absence means unreadable, not unset — and nothing says so.** An SD EPG password is write-only on Dispatcharr and is never returned, so it never enters the gather, so it is never a `redacted_field`, so `dbas/importers/epg_sources.py::_report_credentials_still_missing` — which derives its list from `redacted_fields` — can never name it. The operator's record of what crossed and what did not is silently incomplete for this type, and the rule that makes the reporter correct everywhere else ("a source with no credential produces no redacted field, so it is never an action item") is precisely wrong here. The security consequence is not cosmetic: it trains the operator to read "no report" as "fully provisioned", which is the assumption they will carry into a de-provision or an incident. | **Re-rated Med → Low, 2026-08-22, because the silence is what made it a finding and the silence is closed.** Under the PO's faithful-copy ruling `schedules_direct` is IN scope: `username` is harvested, and the password is **operator-supplied at provisioning**, prompted by the same **`source_type`-driven** rule (presence being unknowable, no presence check can drive it) — now reading "this field needs your input" rather than "this cannot cross". What remains: the write-only property runs both ways, so ECM can report that it **wrote** the value and never that B **holds** a working one; a mistyped password surfaces as B's EPG source failing to fetch, remedied by re-provisioning. That residual is availability-flavoured, not confidentiality — and the same property means an SD password **never rides the per-cycle destination read back to A**, so this field is outside D16's inbound exposure entirely. | to-build (`wd20y`) | ~~Med~~ → **Low** |
| **D16** | `insecure` + credentials B holds that ECM did not write | Information Disclosure | **Reachable today, before any `wd20y` code ships.** The per-cycle destination read fetches B's provider account rows on every cycle — `dbas/importers/m3u_accounts.py::_report_credentials_still_missing` inspects them, and its own measurement records that on Dispatcharr 0.29.0 `/api/m3u/accounts/` returns **both `username` and `password`** to an admin caller. The currently *documented recovery* is for the operator to enter the provider credential on B by hand. An operator who has done that and has `insecure=true` is shipping B's live provider credential to A over an unverified-TLS channel, every cycle, unattended. S11's marker records what **ECM wrote**, not what **B holds**, so it does not see this case at all. | **PO-ratified 2026-08-22 — closes bead `enhancedchannelmanager-3dmgr`.** The `insecure` refusal gates on **recorded OR OBSERVED** state (ADR-013 S11 / INV-4): *observed* is `credential_sentinel.credential_is_present()` — non-empty and not the sentinel — evaluated against the account rows **the cycle already fetches**, inside the re-entry reporter that already calls it. **Presence only:** no new request, no value comparison (same prohibition as S12's staleness signal), no stored secret. One new **non-secret** column (`destination_credential_observed_at`) is required and is stated rather than absorbed, because the write path `PUT insecure=true` has no live view of B; it clears when a cycle observes absence. The refusal must name a remedy that applies: an observed credential ECM did not write has **no marker to clear**, so de-provision is not the remedy — install a valid certificate and clear `insecure` (primary; keeps the standby working), or remove the credential on B (secondary; B stops serving). Attacker model is an **active** MITM able to present a forged certificate, not a passive eavesdropper. | to-build (`wd20y`); **pre-existing, not introduced by it** | **High** |

#### 11.5.2 Re-rated and re-scoped rows

| Row | Before | After | Reasoning |
|---|---|---|---|
| **D2** — sync payload / Information Disclosure | **High**, mitigated by "redact-by-default … **no plaintext-cred path in v0.18.1** … cred-carrying continuous sync is **not shipped**" | **High** (unchanged), mitigation **re-scoped to the per-cycle path** | The threat D2 names — naively streaming live secrets to B on a schedule — is still High and still fully mitigated, and the redactor is untouched. What changes is that D2's mitigation text was doing double duty as the system-wide claim "ECM never sends a credential to B". That half is now false and has been **moved to rows D11 and D12**, which carry their own mitigations. Read D2 as: *the cycle* carries no credential. Do not read it as: *ECM* never does. The severity does not move because nothing about the recurring path got worse; the honest change is scope, and pretending a number moved would obscure that. |
| **D6** — TLS / Tampering | Med — "`verify=True` default; `insecure=true` writes a per-cycle audit row; **forbidden-by-construction if the payload is ever non-redacted** (moot while redact-by-default holds: topology has no creds to expose)" | **High** (provisioned) / Med (never provisioned) | The premise for Med was that a MITM on the channel sees topology. On a provisioned target it sees a live provider credential, and — the part the original row did not consider — it sees it on the **inbound** leg too: the per-cycle destination read pulls B's account rows back to A, `password` included (D16). So the recurring flow carries a live secret rather than topology, every cycle, in both directions. Two further corrections: the "construction" was never built (`insecure` has been editable on `PUT /api/sync-targets/{id}` since the router's first commit, `ed98f32f`, 2026-06-19 — `git log -S` on both the update-model field and the handler write returns exactly that one commit, and the MCP `update_sync_target` tool reaches the same route), so S11 **builds** it rather than inheriting it; and it is no longer a one-way door, because the ratified de-provision escape (D13) lets a target return to `insecure`. The conditional rating follows the existing idiom of row D7's "**High** (if bidir)". |
| **D9** — Audit / Repudiation | Med — "`journal.log_entry` per sync run … no trail of what A pushed to B" | **High** | Two independent reasons, either sufficient. Under the harvest **no human reads the value before it crosses**, so the journal row is the *only* record that a secret moved at all — there is no operator memory, no clipboard, no ticket. And the row is now a **detection control, not only a forensic one**: a `sync_provision_credentials` row whose actor is the scheduler is the alarm for D12, the failure mode with no other detector. An audit row that is useful after an incident is Med; an audit row that is the sole detector of a control failure is High. |
| **D1** — SSRF / Spoofing-EoP | **High**, status "to-build (reuse `0i2vt.5`; guard-extension = AC on `1t3al`)" | **High** (unchanged), **requirement extended**, and the status line corrected | *Status correction:* the guard extension **shipped**. `backend/tests/test_ssrf_chokepoint_guard.py` scans `cloud_storage/*.py` **and** `tasks/dbas_sync*.py`, carries a `test_sync_module_is_in_scope` regression guard, and proves itself red by stripping the `# ssrf-ok:` tag; `tasks/dbas_sync_client.py` routes every request through `security.ssrf.validate_outbound_url` at execute time. D1 as written still reads as outstanding. *Requirement extension, and it is build-gating:* that guard's sync scope is the literal glob `_SYNC_GLOB = "dbas_sync*.py"` under `backend/tasks/`. **A provisioning writer placed anywhere else is not scanned** — and the most natural home for it, a route handler on `backend/routers/sync_targets.py`, is outside the glob. A raw outbound call on the one path that carries a credential would slip the chokepoint entirely. Either place the writer at `backend/tasks/dbas_sync_*.py` so the existing glob covers it, or extend the guard's scanned set in the same commit; do not leave the choice to be discovered. |
| **D4** — category registry / Tampering | Med | Med (unchanged), **scope note** | D4's allowlist governs what the *cycle* assembles, and "secret fields stripped pre-serialize" remains true there. The provisioning path has a **second, narrower** category set that D4 does not govern; that one is row D14. Two allowlists, two owners — neither inherits the other's coverage. |
| **D8** — partial failure / Integrity | Med | Med (unchanged), **newly instantiated** | "No partial state is reported as success" now has a second instance with a security consequence rather than an availability one: a partially-succeeded de-provision must not report success, because the report is what flips the marker that re-permits `insecure`. Recorded as D13 / INV-9. |
| **D3**, **D5**, **D7**, **D10** | — | **Unchanged** | Verified rather than assumed. D3 (`users` never sync) and D7 (one-way only) are the hard lines the §11 gate names and neither is touched — provisioning is A→B, adds no inbound-write boundary on A, and does not add a category to the never-sync set's complement. D5 concerns the **SyncTarget's own** API credential freshness, not provider credentials. D10 (trigger/DoS) is unaffected: provisioning is not a trigger. |

#### 11.5.3 Residual risk after S10–S13

The two residuals in §11.4 that assumed a credential-free channel are re-rated first; the rest are new.

- **Residual: credential re-entry friction on B — RE-RATED.** §11.4 records this as "accepted,
  availability, **not confidentiality**". That framing survives only for an **unprovisioned** target,
  which remains the default. For a provisioned one it inverts: the operator is making a
  **confidentiality decision, per target** — placing a live provider credential on a second instance
  in exchange for a standby that can actually serve. **New rating: Medium, accepted, PO-ratified
  2026-08-22.** The mitigation is that the decision is explicit, per-target, audited, and refused
  over an unverified channel; it is not that the exposure is small.
- **Residual: `insecure=true` on a recurring channel — RE-RATED and largely CONVERTED INTO A
  REFUSAL.** §11.4 rates this Low and accepts it "with per-cycle audit" because what crosses is
  "topology over unverified TLS". That premise does not survive B holding a provider credential:
  the per-cycle **destination read** pulls B's account rows back to A and `/api/m3u/accounts/`
  returns both `username` and `password` to an admin caller on 0.29.0, so the recurring **inbound**
  flow carries a live secret. For a provisioned target the combination is therefore **refused**
  (S11), not audited — an audit row that records a recurring credential exposure is a receipt, not a
  control. What remains as residual, after the refusal: **(a)** a never-provisioned target on an
  unverified channel, unchanged at **Low**; **(b)** the de-provision path, below; **(c)** the case
  the recorded marker cannot see — credentials B holds that ECM did not write — which is **row D16,
  rated High and reachable today**, and which the 2026-08-22 observed-state ruling closes down to the
  bounded window below.
- **Residual: what a successful de-provision cannot guarantee (High, accepted, PO-ratified
  2026-08-22).** This is the residual the PO accepted in choosing the escape over a permanent
  refusal, and it belongs here rather than only in the ADR because it is the operator-facing half. A
  **successful** de-provision guarantees exactly one thing: B's provider account rows no longer hold
  the credential, so B will not re-authenticate with it. Surviving it:
  - **B's own stream rows.** Once B refreshed with a working credential, B's stream table holds URLs
    with the credential in their **path segments**. That is the normal shape, not an edge case — the
    2026-08-20 survey found **all 1,409,363** of one real provider's stream URLs path-credentialed
    (§8.5, rule 4). Clearing an account field does not rewrite those rows, and they remain valid
    addresses.
  - **B's backups and exports** — any artifact, cloud upload or support bundle B produced while
    provisioned. §8.5 keeps a *standard* artifact clean; B's encrypted artifacts and raw database
    copies are outside that.
  - **B's logs and status fields**, including any upstream error body echoed into `last_message`.
  - **Anything downstream that consumed B's output while provisioned** — B's own M3U/HDHR output,
    its clients, and any proxy or cache in front of them.
  - **The provider side. De-provision is not revocation.** The credential stays valid at the
    provider until the operator rotates it there, which is outside ECM entirely.
  - **Time already spent.** Every cycle between provisioning and de-provisioning is unrecoverable.

  Two operator-facing consequences must be surfaced **with the action**, not in a doc. First,
  **B does not immediately go dark**: it keeps serving from its existing credentialed stream rows
  until its next refresh fails, so "it still works" is **not** evidence the clear failed — and
  conversely an operator watching for an outage as confirmation will get the wrong answer. Second,
  **the security-complete action is rotating the credential at the provider**; de-provision stops B
  *re-acquiring* it, it does not make the exposure end.
- **Residual: no human reads the value before it crosses (Medium, accepted, PO-ratified
  2026-08-22).** Under the harvest there is no operator keystroke between A's stored secrets and the
  write to B. The compensating controls are entirely mechanical — INV-6's derived field set bounds
  *which* values, D14's closed category set bounds *whose*, D12's reachability guard bounds *when*,
  and D9's journal row is the only trace afterwards. None of them is a person noticing that a value
  looks wrong. This is the reason D9 moved to High and the reason D12 cannot be satisfied by a
  registry check.
- **Residual: the observed-state gate leaves a bounded one-interval window (Low, accepted,
  PO-ratified 2026-08-22).** The observation happens on a cycle, so a credential typed into B at time
  *T* is not seen until the next cycle — and that cycle's own destination read is what sees it, so
  the credential crosses **once more** before the gate closes. The exposure is therefore bounded at
  **exactly one further cycle after the credential appears on B**, one sync interval, and zero
  cycles thereafter. It cannot be driven to zero from A's side without a new fetch at write time,
  which the presence-only constraint forbids; one interval is the honest floor of a presence-only
  gate. Before this ruling the same exposure was **unbounded** — every cycle, forever.
- **Residual: ECM cannot confirm the Schedules Direct password landed (Low, accepted).** The
  write-only property runs both ways: B does not return an SD password either, so after provisioning
  the run can report that it **wrote** a value, never that B **holds** a working one. A mistyped
  value surfaces as B's EPG source failing to fetch rather than as a provisioning error; the remedy
  is a re-provision, which `a3lby`'s edit affordance makes cheap. Availability, not confidentiality —
  and the same property keeps this field out of D16's inbound exposure, because it never crosses
  back.

#### 11.5.4 What still gates the build

§11's own rule — no sync build bead opens until the addendum is reviewed — applies to this section.
These are the conditions on the `wd20y` build specifically. All five are now requirements; the
decision item 5 previously carried was ruled on 2026-08-22.

1. **D12's reachability guard exists and is proven red.** Both halves: the registry test *and* the
   transitive import guard, with a demonstration that the guard fires when the sync engine is made
   to import the provisioning writer. This is the single structural control standing between a
   one-time path and a recurring one, so an unproven guard here is worth less than no guard, because
   it reads as coverage. (Enforcement-code-tests-itself applies: the guard ships with its own
   red-proof in the same commit, exactly as `test_ssrf_chokepoint_guard.py` does for D1.)
2. **The provisioning writer is inside the SSRF chokepoint guard's scanned set.** This is the item
   most easily lost, because nothing fails when it is wrong. `backend/tests/test_ssrf_chokepoint_guard.py`
   scans `cloud_storage/*.py` and, under `backend/tasks/`, the literal glob `_SYNC_GLOB = "dbas_sync*.py"`
   — `routers/` appears nowhere in the file. **The natural home for the provisioning route,
   `backend/routers/sync_targets.py`, is outside that set**, so a raw outbound call on the one path
   that carries a credential would bypass the chokepoint silently. Either place the writer at
   `backend/tasks/dbas_sync_*.py` so the existing glob covers it, or extend the guard's scanned set
   **in the same commit. Decide it before a line of the route is written**, not after. See row D1 —
   which also records that the guard extension itself already **shipped**, so D1's status line
   ("guard-extension = AC on `1t3al`") reads as a pending control that is in fact enforced; `bd show`
   returns no such issue.
3. **D13/INV-9 is proven on the failure paths, not only the happy one.** Partial-failure and
   total-failure tests asserting the marker is unchanged, `insecure` stays refused, and the affected
   accounts are named — and specifically that a destination error cannot be swallowed into a
   success. The escape is only honest if its failure path is.
4. **D15's `source_type`-driven Schedules Direct prompt is built with the feature**, not after it.
   Under the 2026-08-22 faithful-copy ruling SD is in scope, and the same `source_type` rule that was
   to have announced the exclusion now raises the prompt for the one operator-supplied field.
   Shipping the harvest without it leaves an SD standby silently without guide data, which is the
   finding either way — presence cannot drive it, because the value is unknowable on both instances.
5. **Row D16's observed-state gate ships with the feature — RULED 2026-08-22, closing bead
   `enhancedchannelmanager-3dmgr`.** The `insecure` refusal gates on recorded **or observed** state.
   Three properties must hold in the implementation and each is testable: **presence only**
   (`credential_is_present` against rows the cycle already fetches — a test must assert no new
   destination request and no value comparison appears); **the remedy is stated and applicable** (an
   observed credential ECM did not write has no marker to clear, so the refusal must offer the
   certificate fix or removal on B, never "de-provision"); and **the observation is recorded as a
   fact, not a value** (one non-secret timestamp column, cleared when a cycle observes absence).
   Note the exposure predates `wd20y` and is live today, so this item is the one whose absence has a
   cost even if the rest of the feature never ships.

Items 1, 3 and 5 are the ones where getting it wrong is invisible from the outside: the feature
works, the tests are green, and the control is absent.

---

### 11.6 Update 2026-08-22 (b): provider credentials cross on every cycle (supersedes §11.5)

**Companion to [ADR-013 amendment (b)](../adr/ADR-013-cross-instance-live-sync.md#amendment-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-amendment-a).**
PO-ratified 2026-08-22, third ruling of that day, in the PO's own words:

> *"I know the security risks. That's on the user to mitigate, not us. … We should be sending
> credentials every time so that we don't need the user to deal with needing to re-type anything."*

This section exists because §11.5 is now a description of removed code, and because several of the
threats it rated as *mitigated* are, under this ruling, **accepted**. Those are different words and
the difference is the whole point of writing it down: a mitigated threat has a control someone can
check; an accepted threat has a decision someone made. Reading a row as mitigated when it is
accepted is how a later reader concludes a control was lost by mistake.

#### 11.6.1 What actually changed

Provider credentials are part of what the ordinary sync cycle writes. There is no provisioning
action, no marker column, no version gate and no change detector. The mechanism is one constant —
`tasks.dbas_sync_engine.PROVIDER_CREDENTIAL_SECTIONS`, naming `m3u_accounts` and `epg_sources` — plus
the decision to stop threading harvested secrets into the channels and logos redaction so stream and
logo addresses cross whole.

What did **not** change: `users` never syncs (D3), ECM's own settings secrets, alert-method secrets
and cloud/sync-target credentials are still redacted, the SSRF chokepoint still gates every outbound
request (D1), and the standard backup **artifact** still redacts everything it always did — that is a
file people attach to support tickets, and it is a different surface from a live push to a host the
operator chose.

#### 11.6.2 Re-rated rows

| Row | Was | Now | Why, and mitigated vs accepted |
|---|---|---|---|
| **D2** — sync payload / Information Disclosure | "no plaintext-cred path in v0.18.1"; cred-carrying continuous sync not shipped | **High, ACCEPTED** | Cred-carrying continuous sync is exactly what shipped. The payload carries the operator's provider username, password and credential-bearing addresses to B on every cycle. There is no control that reduces this — it is the feature. What bounds it is the destination: B is a host the operator chose and owns. **Accepted, not mitigated.** |
| **D6** — TLS / Tampering | Med; "forbidden-by-construction if the payload is ever non-redacted" | **High, ACCEPTED on an `insecure` target; Med otherwise** | The payload is non-redacted now, so the construction's own condition is met — and §11.5 built the refusal it called for. The PO removed it. An active MITM against an `insecure` target reads a live provider credential on every cycle, in both directions (the destination read pulls B's account rows back to A). Remaining controls are **detective, not preventive**: a warning in the run's notes and log on every credential-carrying cycle, `tls_verified=false` on the audit row, and a badge on the target row. |
| **D9** — Audit / Repudiation | High (sole detector for D12) | **High, MITIGATED** | Still High, for a changed reason. D12's detection role is gone with D12; what makes the row High now is that under per-cycle transmission **no human reads the value and no one is present when it moves**, so the journal row is the only record that it moved at all. Strengthened accordingly: every terminal route of a cycle writes exactly one `sync_outbound` row carrying `redaction_mode=topology_plus_provider_credentials`, `tls_verified`, the count of provider records that carried a credential, and those records by label and FIELD NAME. The abort routes write it too, recording that they carried nothing. This is the surviving form of bead `gad2p`. |
| **D11** — provisioning action | High, to-build | **VOID** | There is no provisioning action. Its exposure is subsumed by D2. |
| **D12** — the one-time path becomes recurring | High, mitigated by INV-2's reachability guard | **REALISED AND ACCEPTED.** | This is the honest entry. D12's threat was that the one-time path would silently become a recurring one; the PO has made it recurring **deliberately and in the open**, so the threat did not fail to be mitigated — its premise was withdrawn. The reachability guard and the writer module were deleted rather than weakened, because a guard asserting the opposite of the ratified design reads as a control and enforces nothing. **What now bounds the transmission is nothing structural.** It is bounded only by scope (two categories, one constant) and by disclosure (the audit row, the docs and the UI all say it happens). A reader looking for the control that used to be here should know there is not one. |
| **D13** — de-provision | High, to-build | **VOID** | There is no de-provision. See §11.6.3 for what an operator does instead, and what it cannot achieve. |
| **D14** — harvest scope | Med | **Med, MITIGATED** | Survives in changed form and is the one structural control left. The credential-carrying section set is a closed named constant (`PROVIDER_CREDENTIAL_SECTIONS`), and the key set within it is **derived** from the redactor's own `_REDACT_KEYS` / `_PROVIDER_IDENTITY_KEYS` rather than maintained as a literal, so the two rules cannot drift about what a credential is. A newly gathered category does not inherit the exception. |
| **D15** — Schedules Direct reporting | Low | **Low, MITIGATED** | The silence that made it a finding stays closed, and the operator's cost fell from "supply it at every provisioning" to "supply it once". The password is stored Fernet-encrypted on the sync-target row and re-sent every cycle. Its write-only property still runs both ways: ECM can confirm it **wrote** the value and never that B holds a **working** one. New sub-residual: A now persists one provider credential at rest, which §11.5's INV-3 forbade — deliberate, scoped to the one value that cannot be harvested, and using the same crypto path as the target's own credentials. |
| **D16** — `insecure` + a credential B holds that ECM did not write | High, mitigated by the observed-state gate | **High, ACCEPTED** | The gate is gone with the refusal it fed. Its predicate was also measurably wrong (bead `ngwxx`: a populated but credential-free XC `server_url` counted as an observed credential, so the gate false-positived on every XC target, permanently and unclearably) — but that is not why it was removed; it was removed because nothing reads it. Whether ECM wrote the credential or the operator did is now immaterial: B holds one either way, every cycle. **Bead `3dmgr` is dissolved, not fixed.** |

#### 11.6.3 Residual risk after the ruling

Every item here is **PO-accepted**. None has a control that reduces it.

1. **The replica is a place the provider credential lives, permanently.** Its database, backups,
   exports, logs and its own stream rows hold it. Nothing retracts it.
2. **An `insecure` target leaks it in clear on every cycle**, recurring rather than one-shot,
   warned and audited but not blocked.
3. **The blast radius of a compromised replica now includes the provider subscription**, where
   before it was the topology.
4. **There is no ECM-side record of which replicas hold a credential** — the marker columns were
   dropped. The journal is the record: every cycle that carried one says so, naming the target.
5. **De-provisioning is not available and would not have been sufficient.** An operator who wants a
   replica to stop holding the credential must (i) delete or disable the sync target so no further
   cycle re-sends it, (ii) clear the credentials on B themselves, and (iii) **rotate the credential
   at the provider** — which is the only step that ends the exposure, and is outside ECM. Steps (i)
   and (ii) without (iii) stop B re-authenticating; they do not make the secret secret again.
6. **A logo address carrying a credential is copied verbatim** rather than dropped, trading a named
   logo miss for the replica loading the artwork.

#### 11.6.4 What still gates the build

§11.5.4's five items are void with the design they gated. Two survive in changed form, and one is new.

1. **The credential-carrying outbound path stays inside the SSRF chokepoint guard's scanned set.**
   Unchanged in force, easier to satisfy: the path is now `tasks.dbas_sync_engine` →
   `tasks.dbas_sync_client.make_remote_client`, both already inside `_SYNC_GLOB`. §11.5.4 item 2's
   warning — that `backend/routers/` is scanned nowhere — still applies to any future outbound path.
2. **The operator-facing text matches the behaviour, and it is a hard gate.** This is bead
   `msqf7`'s actual subject and the only thing about this feature that can still be a *defect*
   rather than an accepted risk. `msqf7` was not "credentials crossed"; it was "ECM said they were
   stripped while they crossed". Any surface that claims the sync path redacts provider
   credentials — the user guide, a tooltip, a report field, a docstring, or the audit row's own
   `redaction_mode` — is a recurrence of that bead. Corrected in the same branch as the behaviour.
3. **NEW: the two-section boundary is asserted, not assumed.** The easy and invisible way to widen
   this is to apply `preserve_keys` past `PROVIDER_CREDENTIAL_SECTIONS`. A test must assert that an
   ECM settings secret and an alert-method secret are still sentinelled in a sync plan **while** a
   provider password is not, so the pair fails asymmetrically when the boundary moves.
