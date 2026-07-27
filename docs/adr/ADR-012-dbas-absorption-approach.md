# ADR-012: DBAS Absorption Approach (Backup + Restore, full 13-category round-trip)

- **Status**: Accepted
- **Date**: 2026-04-21 (original PO decision, recorded in epic `enhancedchannelmanager-0i2vt`) / 2026-06-16 (formalized as an ADR file by the IT Architect persona; bead `enhancedchannelmanager-07lfx`)
- **Author**: IT Architect persona (on behalf of PO), encoding the nine PO-level decisions (D1–D9) and five resolved open questions captured in epic `enhancedchannelmanager-0i2vt` on 2026-04-21.
- **Bead**: `enhancedchannelmanager-0i2vt` (epic) · ADR-formalization tracked under `enhancedchannelmanager-07lfx`
- **Supersedes**: the retired 42-bead DBAS migration plan `enhancedchannelmanager-gb5r5` (closed 2026-04-21).

> **Why ADR-012 and not ADR-008.** The epic bead `0i2vt` and the DBAS threat model
> (`docs/security/threat_model_dbas_import.md`) both cite *"ADR-008-dbas-absorption-approach.md
> (Accepted 2026-04-21)"* as the source of truth. That filename was never created. The ADR-008
> number was instead taken by `ADR-008-interactive-stream-dedup.md` (Accepted 2026-05-16) — a
> later, unrelated decision that collided on the number. To avoid rewriting an already-shipped,
> widely-referenced ADR (ADR-008 dedup is cited throughout `docs/architecture.md`, `docs/api.md`,
> and the dedup code), this decision record takes the **next free number, ADR-012** (ADR-011 was
> the prior highest). All prior references to "ADR-008" *in the DBAS context* (the epic bead, the
> threat model addenda, the D1–D9 decision list) should be read as pointing here. The decision
> content and its 2026-04-21 acceptance date are unchanged; only the filename/number is corrected.

## Context

**What DBAS is.** DBAS (Dispatcharr Backup And Sync) is a standalone Node.js companion product
that backs up a full Dispatcharr + ECM configuration to a ZIP artifact, optionally uploads it to
cloud storage, restores it end-to-end onto a (possibly different) Dispatcharr instance, and can
sync configuration between two Dispatcharr instances. It covers **13 configuration categories**:
M3U accounts, EPG sources, channel groups, channel profiles, stream profiles, user agents, core
settings, plugins, DVR rules, comskip config, users, channels (with embedded streams), and logos.

**Why absorb it.** Maintaining DBAS as a separate Node.js app duplicates the Dispatcharr API
client, the task scheduler, the notification infrastructure, and the credential-handling surface
that ECM already implements in Python. ECM already ships a smaller-scope backup/restore precursor
(`backend/routers/backup.py` — ZIP + YAML export/restore of ECM settings + `journal.db`). Absorbing
DBAS into ECM as a first-class backup/restore subsystem retires the standalone product, removes the
duplicate maintenance surface, and gives Dispatcharr operators a single tool for full-configuration
backup, disaster recovery, and cross-instance migration.

**User value.** Operators can take a full 13-category backup, push it to durable off-host storage,
and restore the entire configuration end-to-end onto a clean Dispatcharr — for disaster recovery or
to migrate from one instance to another — without manual per-entity fix-up afterward.

**Scope of this ADR.** It governs the absorption approach for **v0.18.0**: Phase 0 (security
prerequisites), Phase 1 (backup + cloud upload), and Phase 2 (restore, all 13 categories). **Sync**
(bidirectional configuration sync between two live Dispatcharr instances) is **Phase 3, deferred to
v0.18.1** as a separate epic — but the `SyncTarget` schema lands in v0.18.0 (see D-table / Consequences).

**Estimate.** 27–46 engineer-days, single engineer, accepted by the PO.

**Prior art / superseded plan.** The original DBAS migration was the 42-bead epic `gb5r5` (filed
2026-02-18). A 2026-04-21 team-plan archaeology found it significantly overlapping already-shipped
ECM work (the `backup.py` YAML restore, `task_scheduler.py`, `dispatcharr_client.py`, the
notification infra), with 26 of 37 tree beads stale (67 days untouched) and lacking acceptance
criteria. The PO retired `gb5r5` entirely rather than salvage it, choosing to re-derive the scope
from user need. Epic `0i2vt` (this ADR) is that re-derivation.

## Decision

Absorb DBAS into ECM per the nine PO-level decisions below. Build it in three phases (Phase 0
security prerequisites → Phase 1 backup + cloud upload → Phase 2 full restore), all shipping in
**v0.18.0**; defer Sync to **v0.18.1**.

| # | Decision | What was decided |
|---|----------|------------------|
| **D1** | Artifact format | ZIP-wrapping per-category YAML + a binary subtree (per-image logo files + `metadata.json` + `url-mappings.json`) + a SHA-256 checksum file alongside the ZIP + a manifest carrying `schema_version`. **`schema_version` is mandatory from the first release.** Restore on an unknown version refuses with a user-facing `"Unsupported backup version"` (full detail server-side only). |
| **D2** | Phase-1 scope | v0.18.0 **MUST ship backup AND restore.** Backup without restore is pointless (you cannot recover from a backup you cannot apply). The two are one user-facing capability, not two releases. |
| **D3** | Credential storage | Fernet symmetric encryption via the **existing** `backend/cloud_storage/crypto.py` primitive. **No KMS for the MVP.** No new crypto surface is introduced. *(Note 2026-06-16: **D12 partially supersedes this** — optional whole-artifact passphrase encryption introduces a new KDF+AEAD surface for the opt-in cred-carrying path. D3 still governs at-rest credential storage.)* |
| **D4** | Outbound URL policy | A **first-run SSRF wizard** (LAN-friendly default: RFC1918 + loopback allowed; public-only mode blocks them) + an **always-on denylist** (metadata/link-local/CGNAT/IPv6-special/non-`http(s)` schemes, rejected in *both* modes, no opt-out) + **DNS-rebinding mitigations** (resolve-then-connect-by-IP, redirect re-validation). |
| **D5** | Progress transport | **HTTP polling**, reusing `task_scheduler` + the NotificationCenter. **WebSocket is NOT ported** from DBAS. |
| **D6** | Release sequencing | **v0.18.0 (DBAS) then v0.18.1 (Sync), sequential**, per the ADR-004 "one major thing per cut" discipline. DBAS is the sole major item of v0.18.0; Sync is the sole major item of v0.18.1. |
| **D7** | Dry-run engine | **Counts-only** (would-create / would-update / would-skip per entity per action). The full entity-level diff tree is **deferred to v0.19.x.** Dry-run is **default-ON** for Dispatcharr restores (opt in to apply; cannot opt out of the dry-run guardrail). |
| **D8** | Logo memory model | **Streaming upload** pattern (read one logo → decode → upload → release before the next), **not** a port of DBAS's `CumulativeMemoryTracker`. Re-evaluable at implementation kickoff. |
| **D9** | Logo-miss severity | A logo that cannot be matched on restore produces a **WARN log + an aggregate count + a prominent red banner on the restore-complete screen** — not a silent DEBUG line. |
| **D10** | Plugins scope | **Plugins are EXCLUDED from v0.18.0** backup/restore (RCE-surface unknown; sidesteps the question and unblocks the rest of `0i2vt.13`). Revisit once plugin semantics are understood. *(Added 2026-06-16; full rationale in the Amendment below.)* |
| **D11** | Cross-instance restore | **IN SCOPE for v0.18.0.** The archive is **trusted operator input**, but always-on safety validations apply regardless of source (SSRF denylist on every URL, schema validation, never restore a foreign admin that locks out the current operator). No archive signing/provenance for a self-hosted LAN tool. *(Added 2026-06-16; rationale in the Amendment.)* |
| **D12** | Passphrase encryption | **Optional whole-artifact passphrase encryption SHIPS in v0.18.0** (overrides the architect's defer recommendation) so credentials travel with a cross-instance migration (pairs with D11). Opt-in; redact-by-default (D1) stays the default. Introduces a new KDF+AEAD surface (**partially supersedes D3**) and a decrypt path across all Phase-2 restore beads. *(Added 2026-06-16. Grooming 2026-06-17: design-gated — crypto spike `0zrse` precedes build; v0.18.0-vs-v0.18.1 placement decided on spike output. Rationale in the Amendment.)* |

### Five open questions — all resolved inline at decision time (2026-04-21)

| Q | Question | Resolution |
|---|----------|------------|
| Q1 | Scope of the v0.18.0 deliverable | **Full round-trip** (backup + restore, all 13 categories). |
| Q2 | Engineer-day estimate | **27–46 engineer-days accepted** by the PO (2026-04-21). *(Stale as of 2026-06-17: this predates D10/D11/D12. Grooming put the D12-in range at ~37–60 days. UNDER REVISION — re-estimate pending the `0zrse` crypto-spike outcome, since D12's v0.18.0-vs-.1 placement is the largest swing. v0.18.0 scope also narrowed: Dropbox/OneDrive cloud targets deferred, leaving S3/WebDAV/GDrive.)* |
| Q3 | SSRF wizard default | **LAN-friendly** (many operators back up to a NAS on `192.168.x.x`). |
| Q4 | Dry-run depth | **Counts-only** for v0.18.0; full diff tree deferred to v0.19.x (= D7). |
| Q5 | Logo-miss visibility | **Prominent red banner** on restore-complete (= D9). |

### Phase breakdown (as decomposed in epic `0i2vt`)

- **Phase 0 — security prerequisites** (`0i2vt.1`, `.2`, `.3`): redact secrets in the existing ZIP
  export path (`.1`, already shipped, rescoped to `l0nhi`); Fernet-key bootstrap integrity check
  (mode 0600 + ownership) (`.2`); STRIDE addenda for export-artifact redaction + outbound
  destinations (`.3`, drafted into `docs/security/threat_model_dbas_import.md` Addenda A & B).
- **Phase 1 — backup + cloud upload** (`0i2vt.4`–`.9`): `SyncTarget` + `CloudTarget` models with
  Fernet-encrypted credentials (`.4`); first-run SSRF wizard + denylist + DNS-rebinding (`.5`);
  scheduled + manual backup via `task_scheduler` (`.6`); ZIP artifact builder with
  `schema_version` + SHA-256 + manifest (`.7`); cloud upload wiring (S3 / OneDrive / Dropbox /
  GDrive / WebDAV) (`.8`); retention policy (last-N + time-based) (`.9`).
- **Phase 2 — restore, 13 categories** (`0i2vt.10`–`.20`): M3U accounts importer (`.10`); EPG
  sources (`.11`); bulk importer for groups + channel profiles + stream profiles (`.12`); bulk
  importer for user agents + core settings + plugins + DVR + comskip + users (`.13`); channels
  importer with 4-tier stream matching + custom-stream fallback (`.14`); logos importer (`.15`);
  dry-run engine (`.16`); schema-version validation (`.17`); pre-flight validation +
  compensating-delete rollback (`.18`); restore-complete UX — logo-miss red banner (`.19`) +
  per-entity summary counts (`.20`).

  **Hard ordering inside Phase 2:** M3U → EPG → Channels → Logos (an entity cannot restore before
  the entities it references).

## Consequences

### Positive

- **Single tool, single maintenance surface.** Retires the standalone DBAS Node.js app; the
  Dispatcharr client, scheduler, notifications, and credential handling are reused, not duplicated.
- **No new crypto, no new transport.** D3 reuses the shipped Fernet primitive; D5 reuses the
  shipped task-scheduler + notification-polling path. Both decisions minimize new attack surface
  and new operational surface — consistent with the single-container model.
- **Forward-compatible artifacts.** D1's mandatory `schema_version` means a future ECM can detect
  an older artifact and route to a migrator rather than silently misapplying it; the SHA-256
  protects against transport corruption on cloud round-trips.
- **Restore is guard-railed.** D7's default-ON counts-only dry-run answers "am I about to wreck my
  setup?" at near-zero engineering cost; D9's red banner makes logo misses impossible to overlook.
- **Schema lands once.** `SyncTarget` is defined in v0.18.0 (D-table / `0i2vt.4`) even though Sync
  ships in v0.18.1, so the v0.18.1 cut is not gated on a migration.

### Negative / costs

- **Large epic, 27–46 days.** Even sub-phased, this is the biggest single capability ECM has taken
  on. Phase 2 alone has 11 children, two of which (`.14` Channels, `.15` Logos) are 4–8 days each.
- **No local transaction across the Dispatcharr REST boundary.** Dispatcharr has no database
  transactions ECM can join, so restore consistency is best-effort: pre-flight validation +
  compensating-delete rollback (D-implied, `0i2vt.18`). This is weaker than an ACID restore and the
  failure modes must be surfaced explicitly to the operator (partial-state reporting), not hidden.
- **Outbound egress is a new trust boundary.** v0.18.0 deliberately makes ECM connect to
  operator-typed URLs (cloud endpoints, WebDAV base URLs, eventually Sync targets). D4's SSRF
  controls are mandatory, not optional, and the always-on denylist must be un-disableable.
- **`CumulativeMemoryTracker` deliberately not ported (D8).** The streaming pattern must survive a
  100 GB policy ceiling (50 MB × 2000 logos) without OOM in a single-process container; this is an
  implementation risk to validate at `0i2vt.15` kickoff.

### Exit path

- **Whole feature**: the subsystem is additive — new routers, new models, new tasks. Backing it out
  is dropping the routers from `all_routers`, the tasks from the registry, and the
  `SyncTarget`/`CloudTarget` tables via a down-migration. No infrastructure to unwind, no vendor
  relationship, no data other operators depend on.
- **D8 (streaming)**: if streaming proves insufficient at scale, the `CumulativeMemoryTracker` port
  is the documented fallback (D8 says "re-evaluable at kickoff").
- **D5 (HTTP polling)**: if polling proves inadequate for progress UX, a WebSocket/SSE transport can
  be added later; the task/notification model does not preclude it.

## Alternatives Considered

| # | Option | Pros | Cons | Decision |
|---|--------|------|------|----------|
| 1 | **Keep DBAS as a standalone Node.js companion** | No absorption work; DBAS already exists | Duplicates ECM's Dispatcharr client / scheduler / notifications / credential handling; second deploy artifact; two codebases drift | Rejected — duplicate maintenance surface is the whole reason to absorb |
| 2 | **The retired 42-bead `gb5r5` plan** (port DBAS 1:1, Sync included up front) | Comprehensive; mirrors DBAS feature-for-feature | 26/37 beads stale, no acceptance criteria, significant overlap with already-shipped ECM work, Sync coupled to the first cut (violates ADR-004 one-major-thing) | **Retired 2026-04-21** — re-derived from user need as epic `0i2vt` |
| 3 | **Backup-only in v0.18.0, restore in v0.18.1** | Smaller first cut | A backup you cannot restore has no user value; splits one capability across two releases | Rejected by **D2** (backup AND restore ship together) |
| 4 | **Full entity-level diff tree for dry-run in v0.18.0** | Richest "what will change" preview | Expensive to build; counts-only answers the safety question at a fraction of the cost | Rejected by **D7** (counts-only; diff tree deferred to v0.19.x) |
| 5 | **KMS-backed credential storage** | Stronger key custody | No KMS in the single-container deployment model; over-engineered for the MVP | Rejected by **D3** (Fernet via existing `crypto.py`); KMS revisitable post-MVP |
| 6 | **Port DBAS's WebSocket progress transport** | Live push, no poll latency | ECM has no WebSocket backend today; adds a transport + scaling surface for a single-process app; the notification-poll path already exists | Rejected by **D5** (HTTP polling, reuse task_scheduler + NotificationCenter) |
| 7 | **Port `CumulativeMemoryTracker` for logos** | Bounded cumulative memory accounting | Heavier than needed; streaming (read-decode-upload-release per file) bounds memory to one logo at a time | Rejected by **D8** (streaming), re-evaluable at kickoff |
| 8 | **Optional `schema_version` (add it later when needed)** | Slightly less work day one | A v0.18.0 artifact with no version is un-migratable by a future ECM — the cost lands forever later | Rejected by **D1** (mandatory from first release) |

## Related

- **Epic / decision source of truth**: `enhancedchannelmanager-0i2vt` (the D1–D9 list + five
  resolved questions; this ADR formalizes that bead's content).
- **Retired predecessor**: `enhancedchannelmanager-gb5r5` (42-bead plan, closed 2026-04-21).
- **Threat model**: `docs/security/threat_model_dbas_import.md` — STRIDE analysis of the import/
  restore engine (§1–§7) plus Addendum A (export-artifact redaction, feeds `0i2vt.7`) and Addendum
  B (outbound destinations / SSRF, feeds `0i2vt.5`/`0i2vt.8`) and checklist items 18–26. This ADR
  does **not** restate the threat controls; the threat model is authoritative for them. (Note: that
  document still cites "ADR-008" for the DBAS context — read as this ADR-012 per the number-collision
  note above.)
- **ADR-004** (`docs/adr/ADR-004-release-cut-promotion-discipline.md`) — the one-major-thing release
  discipline that D6's v0.18.0→v0.18.1 sequencing leans on.
- **Crypto module**: `backend/cloud_storage/crypto.py` — the Fernet primitive D3 reuses
  (`KEY_FILE = /config/.export_key`, mode 0600; the `0i2vt.2` bootstrap check hardens it).
- **Existing backup precursor**: `backend/routers/backup.py` — the ZIP/YAML export+restore the
  v0.18.0 builder extends (`REDACTED` sentinel, `_scrub_journal_db_to_temp`, `_gather_settings`,
  `RESTORABLE_SECTIONS`).
- **Task substrate**: `backend/task_scheduler.py` / `backend/task_engine.py` — the scheduled/manual
  invocation (`0i2vt.6`) and HTTP-polled progress (D5) path.
- **Cloud adapters**: `backend/cloud_storage/factory.py` + `s3_adapter.py` / `onedrive_adapter.py`
  / `dropbox_adapter.py` / `gdrive_adapter.py` — the upload substrate `0i2vt.8` extends (WebDAV
  adapter to be added).

## Amendment — 2026-06-16 PO decisions (ADR-formalization review, bead `07lfx`)

The IT-architect review of epic `0i2vt` (bead `07lfx`) endorsed the approach and surfaced three
decisions that the PO resolved on 2026-06-16. They extend D1–D9:

| # | Decision | Rationale / impact |
|---|----------|--------------------|
| **D10** | **Plugins are EXCLUDED from v0.18.0 backup/restore.** | We do not yet know whether a Dispatcharr "plugin" is executable code (RCE surface on restore) or declarative config (`grep plugin backend/` = 0 hits). Excluding it sidesteps the RCE question entirely and unblocks the rest of `0i2vt.13` (settings/DVR/comskip/users/agents). Revisit in a later release once plugin semantics are understood. `0i2vt.13` drops the plugins category. |
| **D11** | **Cross-instance restore is IN SCOPE for v0.18.0.** The archive is treated as **trusted operator input** (same trust as the operator typing config), but the always-on safety validations apply **regardless of source**: SSRF denylist on every URL, schema validation, and never restoring a foreign admin that locks out the current operator. | The epic's headline value is migration (back up instance A, restore onto instance B). Full untrusted-archive provenance/signature checking is overkill for a self-hosted single-operator LAN tool; operator-trusted-input + unconditional safety guards is the right posture. The threat model (currently same-instance-scoped, Draft) must be re-pointed at this ADR and lifted out of Draft to cover the cross-instance case. |
| **D12** | **Optional whole-artifact passphrase encryption SHIPS in v0.18.0** (overrides the architect's recommendation to defer to v0.18.x). | So credentials travel with a cross-instance migration instead of being re-entered on the target (pairs with D11). This ADDS scope to v0.18.0: passphrase → key-derivation (e.g. scrypt/PBKDF2), lost-passphrase handling (unrecoverable — must be made explicit to the operator), and the encrypt/decrypt UX on backup + restore. The redact-by-default path (D1) remains the default; passphrase encryption is opt-in for operators who want secrets included. Needs dedicated design at grooming. A new bead covers it. |

These are recorded as PO-accepted. The threat model and the affected child beads (`0i2vt.13`,
the new passphrase-encryption bead, `0i2vt.7`/restore) are updated to match.
