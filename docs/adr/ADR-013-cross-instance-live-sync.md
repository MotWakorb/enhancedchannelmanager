# ADR-013: Cross-Instance Live Sync (one-way A→B configuration replication)

- **Status**: Accepted
- **Date**: 2026-06-19 (PO decisions ratified in epic `enhancedchannelmanager-i39wu` team-plan; architecture proven by spike `enhancedchannelmanager-xp6mp`)
- **Author**: IT Architect persona (on behalf of PO), encoding the i39wu team-plan decisions and the xp6mp spike findings.
- **Bead**: `enhancedchannelmanager-i39wu` (epic) · spike `enhancedchannelmanager-xp6mp` (closed) · this ADR file tracked under `enhancedchannelmanager-anop4`.
- **Extends**: [ADR-012](ADR-012-dbas-absorption-approach.md) D6 (Sync = Phase 3, deferred to v0.18.1 as a separate epic; the `SyncTarget` schema landed in v0.18.0 under `0i2vt.4`).
- **Security companion**: `docs/security/threat_model_dbas_import.md` **Addendum D** (bead `gwjss`) — the STRIDE rows + ratings for the sync egress surface. Build is gated on Addendum D the same way `u81kh` was gated on Addendum C.

## Context

**What sync is.** A recurring, **one-way** replication of configuration from a source ECM/Dispatcharr instance **A** to a managed-replica instance **B**. It is *continuous* (scheduled), where DBAS backup/restore (v0.18.0, shipped) is a *one-shot* artifact. The `SyncTarget` model + `sync_targets` table landed schema-present / code-absent in v0.18.0 (`backend/export_models.py`, Alembic `0023`) precisely so this epic is not migration-gated.

**The operator problem.** An operator running two Dispatcharr instances — a primary plus a DR/standby, or a LAN box plus a remote box — must today hand-copy configuration or repeatedly run one-shot backup→restore to keep B aligned with A. There is no way to keep B continuously tracking A. Success = the operator points ECM-A at B once, and B's lineup/config thereafter converges to A automatically, with a loud signal if it ever stops.

**Distinct from two already-shipped things.** Sync is NOT DBAS backup/restore (one-shot artifact; restore = migration) and NOT cloud-upload (backup egress to S3/etc.). It is continuous config replication between two *live* instances.

**Trust posture.** Self-hosted, single-operator, LAN-first — the same posture as the DBAS threat model: operator-trusted input, always-on safety guards, **no** archive signing / PKI / provenance.

**The decisive architectural finding (spike `xp6mp`, demonstrated).** The DBAS restore orchestrator (`backend/dbas/restore_orchestrator.py` `run_restore`/`run_dry_run`) and all eight importers (`backend/dbas/importers/`) take the Dispatcharr `client` as an injected parameter; the **only** coupling to "local" is a single `get_client()` call in `backend/tasks/dbas_restore.py`. A throwaway PoC ran the real `run_restore()` + the real importer against a swapped (mock-B) client, round-tripped one category A→B, and proved the re-run is a clean no-op — **with zero edits to `backend/dbas/`.** Therefore **sync = "restore over HTTP"**: point the existing, tested restore engine at a `DispatcharrClient` built from a `SyncTarget` row instead of the local one.

## Decision

Build one-way A→B live sync as a thin shell over the **reused** DBAS restore engine, per the decision table below. New code is the client-seam only: a remote-client factory, `SyncTarget` CRUD, a `SyncTask` on `task_scheduler`, observability, and a settings-card UI. The orchestrator and importers are **not** modified for the sync mechanism itself (one small importer *correctness* fix is called out in S3/decision table notes).

| # | Decision | Choice | Alternatives Considered | Exit Path |
|---|----------|--------|-------------------------|-----------|
| **S1** | Mechanism | **REUSE** the DBAS `run_restore` orchestrator + 8 importers unchanged; sync = "restore over HTTP" against a remote `DispatcharrClient`. New code = remote-client factory + `SyncTarget` CRUD + `SyncTask` + alert + UI. *(Proven by spike `xp6mp`: round-trip + re-run no-op, zero `dbas/` edits.)* | (a) Greenfield sync engine; (b) bidirectional CRDT/merge engine. Both re-derive FK-remap, the 4-tier stream matcher, the rollback ledger, and the dry-run engine from scratch. | Revert = drop the `SyncTask`, the remote-client factory, and the `SyncTarget` router from registration. Orchestrator/importers are untouched, so nothing there to unwind. |
| **S2** | Direction | **One-way A→B.** B is a managed replica; A is system-of-record. **Bidirectional is explicitly out of scope → a separate future ADR** (it opens a new inbound-write trust boundary on A, makes conflict resolution a security control, and risks A→B→A loop amplification). | Bidirectional A↔B; last-writer-wins two-way. | Bidirectional is additive in a later epic; one-way imposes no schema/contract that blocks it. |
| **S3** | Category set + permanent **never-sync** list | **Sync:** M3U accounts, EPG sources, channel groups, channel profiles, stream profiles, user agents, core settings, **channels (+ embedded streams)**, logos *(logos phased — see S9)*. **NEVER-SYNC (permanent, code-enforced):** **users** (privilege-flag escalation / operator lockout under continuous push) and the credential-freshness columns (`credentials`, `credential_version`, `token_revoked_at`, `insecure`). Plugins excluded (inherits ADR-012 D10). | Sync all 13 categories incl. users; sync credentials on the wire. | A future ADR could add `users` behind an explicit, separately-ratified opt-in with a lockout guard; the never-sync set is one shared constant in the redact/category-filter layer, removable per-category if justified. |
| **S4** | Change detection | **Full-read + idempotent upsert every cycle. NO delta state in v1.** Each cycle reads B's full category; the importers match→skip-or-create. | Delta/CDC with persisted per-entity sync cursors; change-driven (webhook) trigger. | Delta is a deferred optimization **gated on measured slowness**; it bolts onto the existing match logic without changing the contract. |
| **S5** | Conflict policy | **Source-wins (A overwrites B).** Consistent with one-way "A is system-of-record." The importer collision taxonomy already encodes this (existing-identical → skip; ambiguous match on a load-bearing natural key → `CONFLICT`, surfaced, not silent). | Last-write-wins by timestamp; manual / field-level merge. | Merge/manual conflict UI is additive later; the per-entity `CONFLICT` result already exists to surface it. |
| **S6** | Trigger | **Scheduled-interval** via `task_scheduler` (+ manual force-sync). Overlap guard + credential-freshness gate at fire time. **One `task_id` per SyncTarget** (distinct targets run concurrently; the `ALREADY_RUNNING` guard excludes a second run of the *same* target). | Change-driven / webhook; continuous streaming. | Change-driven is the same `SyncTask` invoked from an event source later; no engine change. |
| **S7** | Security controls | **SSRF `validate_outbound_url` on `base_url` on EVERY request** (execute-time, resolve-by-IP, redirect re-validate) — and the CI grep that forbids raw outbound calls **must extend to the sync module**. **Credential-freshness at fire time** (capture `credential_version` at enqueue; re-check + `token_revoked_at` at execute; abort+audit on change/revoke — mirror `dbas_backup`). **TLS `verify=True` default; per-target `insecure` escape hatch ONLY with a per-cycle audit row** (and forbidden-by-construction if the payload is ever non-redacted). **Redact-by-default** via the shared `_REDACT_KEYS` denylist before serialize. *(Risk ratings → Addendum D / `gwjss`.)* | Config-time-only SSRF validation; one-time insecure audit; secrets on the wire. | Controls are existing chokepoints; tightening (mandatory TLS, drop the insecure flag) is a settings change, not a re-architecture. |
| **S8** | Failure / idempotency | **Reuse `RollbackLedger` + compensating-delete + the tri-state `RestoreOutcome`** (never SUCCESS on mixed state); default-ON dry-run guardrail carries over. **Idempotency is the load-bearing operational property:** a run MUST be safe to re-run to convergence (upsert-by-stable-identity), so retry IS the recovery mechanism — **no rollback/saga machinery** beyond what the ledger already provides, and none may be added without revisiting this ADR. | Best-effort no-rollback; a custom sync-specific failure model. | The tri-state contract is already the orchestrator's; a richer per-category report is additive. |
| **S9** | Which importers run per cycle | **Config categories every cycle** (M3U, EPG, groups, channel/stream profiles, user agents, core settings — cheap reads). **Channels + streams every cycle** (in scope; pulls the 4-tier stream matcher). **Logos: PHASED — not in the first sync-cycle slice** (the logos importer carries a destructive `clear_existing` bulk-delete + a streaming-upload cost that is wrong to run every interval). **The deferred auto-sync / EPG-download phase MUST be suppressed per-cycle** (it would re-trigger provider auto-sync / EPG-download on B on every run). **Not** "run all importers blindly." | Run all importers every cycle incl. logos and the deferred phase; run the channels matcher off-cycle. | Logos join the per-cycle set once cost is measured acceptable (or on a slower sub-interval); the importer already registers into the same ordered step list. |

### Persisted sync state (DBA ruling, spike `xp6mp`)

Per-target **current state** is three nullable columns on `sync_targets` — `last_full_sync_at`, `last_outcome`, `last_source_fingerprint` — **not** a `sync_runs` history table in v1. The v1 access pattern (the staleness alert + the UI status card) reads exactly one current row per target; that is a 1:1 attribute of the target, not an event stream. Run *history* is emitted to the metrics/observability pipeline (`ecm_sync_runs_total`); a queryable `sync_runs` table is additive later if the UI needs historical drill-down. The columns are nullable additive DDL (no `server_default`, no data-only migration — the smart-bootstrap stamp-skip trap, see `docs/database_migrations.md`).

### Channel / stream collision-safe floor (DBA ruling, spike `xp6mp`)

The DBAS importers' one-shot natural keys become **silent recurring divergence** under continuous sync, so the sync path floors them:
- **Channels:** a `(name, channel_number)` match where `channel_number` is null/absent on **both** sides is ambiguous → emit `CONFLICT` (skipped-with-reason, surfaced), **not** a silent `ALREADY_EXISTS_IDENTICAL`. (A non-null number match stays identical.) This is a **correctness fix inside one importer** (`channels.py`) — the orchestrator/importer *reuse* claim of S1 still holds; recommended to fix uniformly (it was a latent one-shot bug).
- **Streams:** the matcher floors at **Tier-3 exact-normalized**; Tier-4 fuzzy (`token_set_ratio ≥ 0.60`) requires an explicit per-`SyncTarget` opt-in (`fuzzy_stream_matching`, default off) and, when on, reports low-confidence rather than a silent `updated`.

### Phasing

Both "config categories" and "channels/streams/logos" are *in scope*, but they ship in two slices:
1. **Phase-1 (ships first): config-category sync** — the one-way engine via `run_restore` against the remote client (M3U/EPG/groups/profiles/agents/settings), redact-by-default, dry-run default. Cheap full reads; exercises the seam, the SSRF chokepoint, the freshness gate, and the tri-state outcome end-to-end with the lowest blast radius.
2. **Phase-2 (ships behind it): channels/streams/logos** — where the 4-tier stream matcher, the collision-safe floor, and the logo cost/risk land.

## Consequences

### Positive
- **The hard, security-sensitive work is already built and tested** — FK-remap, the stream matcher, the rollback ledger, the dry-run guardrail, the SSRF chokepoint, the Fernet credential handling, the `task_scheduler` substrate, the per-task staleness gauge. Sync inherits every one of them by injection.
- **One mechanism to operate and reason about** — a sync failure looks like a restore failure (same orchestrator, same tri-state, same ledger). No second engine to maintain. This is the continuation of ADR-012's single-tool thesis.
- **Redact-by-default + never-sync-users keeps the egress surface narrow** — B receives topology, not secrets; the operator re-enters credentials on B once (or B keeps its own). No live secrets stream over the network on a schedule.

### Negative / costs
- **B is not immediately stream-ready for credentialed sources** — redact-by-default means M3U/EPG passwords are not replicated; the operator re-enters them on B. Secret *migration* remains the `u81kh` encrypted-artifact path, not sync. This is the deliberate trade for not streaming live secrets continuously.
- **One shared importer (`channels.py`) gets a small correctness change** for the collision floor — a `(name, null)` channel that one-shot restore previously skipped silently now surfaces `CONFLICT`. Recommended fix is uniform (it improves restore too).
- **Full integration validation needs a second live Dispatcharr-B** — the build + unit/contract tests run against mocks/fakes; the live A→B round-trip and the live half of the test harness (`46pkq`) are gated on a reachable B.

### Exit path
Sync is additive at exactly two seams (the remote-client factory and the `SyncTask`) plus the `SyncTarget` CRUD/UI. Reverting = unregister the task + router + factory; the orchestrator and importers are untouched. The three new `sync_targets` columns are nullable and reversible.

## Alternatives Considered

- **Greenfield sync engine** — rejected. Re-derives the FK-remap, the 4-tier stream matcher, the rollback ledger, and the dry-run engine that the restore path already ships and tests; months of duplicated, security-sensitive work for zero architectural gain, and it reintroduces the divergence DBAS absorption killed.
- **Bidirectional A↔B in v1** — rejected for v1 (separate future ADR). It opens a new *inbound-write* trust boundary on A, turns conflict resolution into a security control (last-write-wins = whoever an attacker controls wins), and risks loop amplification. One-way keeps A as the single source of truth and lets redaction + never-sync-users be enforced at one egress point.
- **Change-driven trigger / delta-sync in v1** — rejected for v1. Change-driven adds a change-feed failure domain with no existing observability; delta needs persisted cursors. Full-read + idempotent upsert on a scheduled interval converges to the same state with no new operational surface, and idempotency makes "just retry next interval" a complete recovery story. Both are deferred optimizations gated on measured need.

## Related

- [ADR-012](ADR-012-dbas-absorption-approach.md) — DBAS absorption (this ADR extends D6; reuses the Phase-2 restore substrate it produced).
- `docs/security/threat_model_dbas_import.md` — Addendum B §9 (the SSRF + credential-freshness contract, written anticipating SyncTarget) and the forthcoming **Addendum D** (`gwjss`, the sync STRIDE rows / ratings — the build gate).
- Epic `enhancedchannelmanager-i39wu` + spike `enhancedchannelmanager-xp6mp` (the decision record + the demonstrated reuse proof).
- `backend/export_models.py` (`SyncTarget` model), `backend/security/ssrf.py` (the reused chokepoint), `backend/dbas/` (the reused restore engine).
