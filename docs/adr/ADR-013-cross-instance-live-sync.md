# ADR-013: Cross-Instance Live Sync (one-way A→B configuration replication)

- **Status**: Accepted — **amended twice on 2026-08-22, and the second reverses the first.**
  [Amendment (a)](#amendment-2026-08-22-a-one-time-credential-provisioning-at-sync-target-setup-distinct-from-per-cycle-sync-bead-enhancedchannelmanager-wd20y)
  added one-time credential provisioning (S10–S13). **[Amendment (b)](#amendment-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-amendment-a)
  supersedes it in full**: provider credentials cross on EVERY cycle, S10–S13 and INV-2/3/4/8/9 are
  void, and S3 and S7 are amended rather than merely re-read. **Read (b) first; (a) is history.**
- **Date**: 2026-06-19 (PO decisions ratified in epic `enhancedchannelmanager-i39wu` team-plan; architecture proven by spike `enhancedchannelmanager-xp6mp`)
- **Author**: IT Architect persona (on behalf of PO), encoding the i39wu team-plan decisions and the xp6mp spike findings.
- **Amendment beads**: `enhancedchannelmanager-wd20y` (2026-08-22, one-time credential provisioning
  — superseded the same day); the per-cycle ruling in amendment (b) also dissolves `ngwxx`, `3dmgr`,
  `2jvvb`/`5bib5` and `nc6t7`, and re-scopes `gad2p`.
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

### Governing principle (PO direction, 2026-08-22): a replica is a faithful copy

**Everything replicates by default. Any exclusion is an exception that must be explicitly named,
individually justified, and visible — never a silent omission, and never a default.**

The PO's bar, verbatim: *"this product doesn't work if it doesn't replicate everything to a second
instance."* A replica that is missing settings is a **broken replica, not a conservative one**.

This **inverts the default this ADR was originally written under.** Until 2026-08-22 an item was out
of scope unless someone argued it in. From now on an item is **in scope unless there is a written,
specific reason it cannot be**, and the following are explicitly *not* reasons: "we did not get to
it", "it is cheaper not to", "it seemed risky". A reason must name a **specific harm** or a
**specific impossibility**.

Two things this principle does **not** do, stated because the distinction decides how it is applied:

- **It is about scope, not about controls.** "Everything replicates" does not mean "everything
  replicates by any mechanism"; it means *the destination ends up faithful*, and the mechanism still
  has to be safe. Nothing here licenses putting a secret on a recurring cycle. `msqf7`'s redaction on
  the per-cycle path, `msqf7`'s redaction of ECM's own secrets and `avrix`'s group selection are all
  unchanged by it. *(INV-2's reachability guard and the S11 `insecure` gate were named here too and
  are since VOID — removed by amendment (b), not by this principle.)*
- **It does not turn "unreadable" into "out of scope."** A value that cannot be *harvested* is still
  in scope; it reaches the replica another way. The Schedules Direct password is the worked example —
  the operator supplies it once on the sync target and it cascades every cycle
  ([amendment (b)](#amendment-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-amendment-a)).

Every exclusion in this ADR is therefore reclassified into exactly three kinds, and the register is
[below the decision table](#exclusion-register-reclassified-against-the-2026-08-22-principle):
**(1) technically impossible** — say why, and say what closes the gap by hand; **(2) deliberately
excluded on a specific harm** that survives the principle; **(3) not yet built** — which is a gap
with a bead, not an exclusion.

| # | Decision | Choice | Alternatives Considered | Exit Path |
|---|----------|--------|-------------------------|-----------|
| **S1** | Mechanism | **REUSE** the DBAS `run_restore` orchestrator + 8 importers unchanged; sync = "restore over HTTP" against a remote `DispatcharrClient`. New code = remote-client factory + `SyncTarget` CRUD + `SyncTask` + alert + UI. *(Proven by spike `xp6mp`: round-trip + re-run no-op, zero `dbas/` edits.)* | (a) Greenfield sync engine; (b) bidirectional CRDT/merge engine. Both re-derive FK-remap, the 4-tier stream matcher, the rollback ledger, and the dry-run engine from scratch. | Revert = drop the `SyncTask`, the remote-client factory, and the `SyncTarget` router from registration. Orchestrator/importers are untouched, so nothing there to unwind. |
| **S2** | Direction | **One-way A→B.** B is a managed replica; A is system-of-record. **Bidirectional is explicitly out of scope → a separate future ADR** (it opens a new inbound-write trust boundary on A, makes conflict resolution a security control, and risks A→B→A loop amplification). | Bidirectional A↔B; last-writer-wins two-way. | Bidirectional is additive in a later epic; one-way imposes no schema/contract that blocks it. |
| **S3** | Category set + permanent **never-sync** list | *(**AMENDED 2026-08-22 by [amendment (b)](#amendment-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-amendment-a) → S3′: provider credentials are OUT of the never-sync set and cross on every cycle. The never-sync set is now `users` plus ECM's own secrets.**)* *(Reframed 2026-08-22 by the governing principle above: read this row as an **exclusion register**, not a scope ceiling. Its never-sync list is not the set of things that happen not to sync — it is the set for which a specific harm has been written down. Everything else is in scope, and anything absent is a gap, not a decision. Per-item reclassification: see the register below the table.)* **Sync:** M3U accounts, EPG sources, channel groups, channel profiles, stream profiles, user agents, core settings, **channels (+ embedded streams)**, logos *(logos phased — see S9)*. **NEVER-SYNC (permanent, code-enforced):** **users** (privilege-flag escalation / operator lockout under continuous push) and the credential-freshness columns (`credentials`, `credential_version`, `token_revoked_at`, `insecure`). Plugins excluded (inherits ADR-012 D10). | Sync all 13 categories incl. users; sync credentials on the wire. | A future ADR could add `users` behind an explicit, separately-ratified opt-in with a lockout guard; the never-sync set is one shared constant in the redact/category-filter layer, removable per-category if justified. |
| **S4** | Change detection | **Full-read + idempotent upsert every cycle. NO delta state in v1.** Each cycle reads B's full category; the importers match→skip-or-create. | Delta/CDC with persisted per-entity sync cursors; change-driven (webhook) trigger. | Delta is a deferred optimization **gated on measured slowness**; it bolts onto the existing match logic without changing the contract. |
| **S5** | Conflict policy | **Source-wins (A overwrites B).** Consistent with one-way "A is system-of-record." The importer collision taxonomy already encodes this (existing-identical → skip; ambiguous match on a load-bearing natural key → `CONFLICT`, surfaced, not silent). | Last-write-wins by timestamp; manual / field-level merge. | Merge/manual conflict UI is additive later; the per-entity `CONFLICT` result already exists to surface it. |
| **S6** | Trigger | **Scheduled-interval** via `task_scheduler` (+ manual force-sync). Overlap guard + credential-freshness gate at fire time. **One `task_id` per SyncTarget** (distinct targets run concurrently; the `ALREADY_RUNNING` guard excludes a second run of the *same* target). | Change-driven / webhook; continuous streaming. | Change-driven is the same `SyncTask` invoked from an event source later; no engine change. |
| **S7** | Security controls | *(**AMENDED 2026-08-22 by [amendment (b)](#amendment-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-amendment-a) → S7′: the payload IS non-redacted for provider credentials, and `insecure` is WARNED rather than forbidden. Every other control below stands.**)* **SSRF `validate_outbound_url` on `base_url` on EVERY request** (execute-time, resolve-by-IP, redirect re-validate) — and the CI grep that forbids raw outbound calls **must extend to the sync module**. **Credential-freshness at fire time** (capture `credential_version` at enqueue; re-check + `token_revoked_at` at execute; abort+audit on change/revoke — mirror `dbas_backup`). **TLS `verify=True` default; per-target `insecure` escape hatch ONLY with a per-cycle audit row** (and forbidden-by-construction if the payload is ever non-redacted). **Redact-by-default** via the shared `_REDACT_KEYS` denylist before serialize. *(Risk ratings → Addendum D / `gwjss`.)* | Config-time-only SSRF validation; one-time insecure audit; secrets on the wire. | Controls are existing chokepoints; tightening (mandatory TLS, drop the insecure flag) is a settings change, not a re-architecture. |
| **S8** | Failure / idempotency | **Reuse `RollbackLedger` + compensating-delete + the tri-state `RestoreOutcome`** (never SUCCESS on mixed state); default-ON dry-run guardrail carries over. **Idempotency is the load-bearing operational property:** a run MUST be safe to re-run to convergence (upsert-by-stable-identity), so retry IS the recovery mechanism — **no rollback/saga machinery** beyond what the ledger already provides, and none may be added without revisiting this ADR. | Best-effort no-rollback; a custom sync-specific failure model. | The tri-state contract is already the orchestrator's; a richer per-category report is additive. |
| **S9** | Which importers run per cycle | **Config categories every cycle** (M3U, EPG, groups, channel/stream profiles, user agents, core settings — cheap reads). **Channels + streams every cycle** (in scope; pulls the 4-tier stream matcher). **Logos: PHASED — not in the first sync-cycle slice** (the logos importer carries a destructive `clear_existing` bulk-delete + a streaming-upload cost that is wrong to run every interval). **The deferred auto-sync / EPG-download phase MUST be suppressed per-cycle** (it would re-trigger provider auto-sync / EPG-download on B on every run). **Not** "run all importers blindly." | Run all importers every cycle incl. logos and the deferred phase; run the channels matcher off-cycle. | Logos join the per-cycle set once cost is measured acceptable (or on a slower sub-interval); the importer already registers into the same ordered step list. |

### Exclusion register: reclassified against the 2026-08-22 principle

Every exclusion this ADR carries, walked and reclassified. Where a **prior PO ruling** excluded
something and the new principle points the other way, both are recorded, the tension is named, and
the entry **defaults toward replication** pending the PO's reconciliation — it is not resolved
silently in either direction.

#### Kind 1 — technically impossible, closed by hand

| Item | Why it cannot be read or written | What closes the gap |
|---|---|---|
| Schedules Direct EPG `password` | Write-only on Dispatcharr: never returned, SHA1-hashed at fetch (`docs/dispatcharr_api.md` §EPG Sources). The value exists nowhere on A to read, and B does not return it either, so ECM can report that it *wrote* one but never that B *holds* a working one. | **In scope, operator-supplied for that one field** at provisioning (2026-08-22 amendment, S10). Not excluded — impossible-to-harvest is not out-of-scope. A `source_type`-driven prompt appears only when a `schedules_direct` source is present. |

#### Kind 2 — deliberately excluded on a specific harm

| Item | The specific harm (not a general unease) |
|---|---|
| `dispatcharr_users` | Continuous one-way push of the `users` category repeatedly overwrites B's `is_superuser` / `is_staff` / `user_level` from A: a **privilege-escalation and operator-lockout primitive under automation**. `password_hash` is non-transportable anyway (Dispatcharr exposes `password` write-only). Survives unchanged. |
| The sync target's **own** credential columns (`credentials`, `credential_version`, `token_revoked_at`) | These are **A's record of how to reach B**, not instance configuration. There is nothing on B for them to correspond to, and pushing them would overwrite B's own token-freshness state with A's. Not a scope exclusion so much as a category error. Survives unchanged. |
| `network_access` (core-settings blob) | Access control **tied to where the instance sits**. Replicating it can lock the operator out of B, or open B up, depending on which direction the two instances differ. This is the same harm class as `users` and it survives the principle intact. |
| The deferred auto-sync / EPG-download phase, suppressed per cycle (S9) | Excludes an **action**, not a setting: running it every cycle re-triggers provider auto-sync and EPG downloads **on B**, on every interval. The replicated *configuration* still crosses; only the repeated side effect is suppressed. Survives. |

#### Kind 3 — not yet built, or unresolved: a gap with a bead

| Item | Status |
|---|---|
| `stream_settings`, `dvr_settings`, `system_settings` | **Ruled SYNC by the PO on 2026-08-21** (bead `10wnq`) and **not implemented** — `core_settings` appears nowhere in the sync engine at any layer. Under the new principle this is unambiguously a gap, not an exclusion. Note the implementation seam: settings are key/value **blobs**, absent from `_SECTION_TO_ENTITY` by design, so adding a category key alone would be inert. |
| Plugins (inherited from [ADR-012](ADR-012-dbas-absorption-approach.md) D10) | Held excluded on an **unconfirmed but severe** potential harm: nobody has yet established whether a Dispatcharr "plugin" is executable code (an RCE surface on write) or declarative config. That is a determination nobody has made, not a harm anybody has demonstrated, so under the principle it is a **gap with a safety hold**: the deliverable is the determination, and the hold stands only until it is made. |
| Provider **attribution** on replicated streams (`hne7k`, Option A) | Every replicated stream hangs off a synthesized "ECM Custom Streams" account while B's replicated provider accounts arrive holding zero streams, so **B's M3U Manager misrepresents what each account provides**. The PO chose Option A on 2026-08-20 ("let's try option A and see what happens"). **Tension with the new principle, recorded rather than resolved:** a replica whose provider attribution is wrong is not a faithful copy. `hne7k` established attribution is reconstructible (the source `m3u_account` reaches `_build_stream_payload`, and the remap is fully populated by the channels step); one thing is unmeasured and gates any move — whether Dispatcharr's own refresh reconciles or **deletes** rows ECM created under an account it now owns. Default under the principle is to replicate attribution; the measurement comes first. |

#### Tensions with the 2026-08-21 per-blob ruling

The PO ruled these four core-settings blobs **never-sync** on 2026-08-21 (bead `10wnq`), one day
before stating the faithful-copy principle. `network_access` survives as Kind 2 above. The other
three are recorded here with **both** positions, defaulting toward replication:

| Blob | 2026-08-21 ruling and its reason | Read against the 2026-08-22 principle |
|---|---|---|
| `proxy_settings` | Never-sync: buffering speed/timeout, client wait, init grace, shutdown delay, Redis chunk TTL — "tuning that may legitimately differ if the replica has different hardware or network." | "May legitimately differ" is a **preference**, not a named harm; the principle rejects that class of reason. There *is* a real functional risk (tuning copied onto different hardware can degrade playback on B), but that argues for a **per-target opt-out**, not for silent omission. Default: replicate, opt-out available. |
| `user_limit_settings` | Never-sync: recorded as adjacent to the permanently never-sync `users` category, for lockout reasons. Empty on the live instance. | **Adjacency is not a specific harm.** A specific harm may exist — limits keyed to user accounts that never cross could apply to nonexistent or mismatched accounts on B — but it is **unverified**, because the blob is empty on the live instance and nobody has read its populated shape. That makes this a measurement gap. Default: replicate once the shape is known. |
| `backup_settings` | Never-sync: schedule cron/frequency/retention — "two instances on an identical backup schedule is worse than useless, and B's retention is a local storage decision." | This one **does** name a specific harm (simultaneous backup runs; retention is bounded by B's own storage). It is the strongest of the three. Even so, a standby with no backup schedule at all is a replica missing settings, so the principle points at **replicate-with-offset or a per-target opt-out** rather than omission. Default: replicate, opt-out available. |

Also reconciled here, per the same 2026-08-21 ruling: **S3's never-sync column list is over-broad.**
It names `insecure` alongside the three credential columns; the shipped constant
`SYNC_NEVER_CREDENTIAL_COLUMNS` names only the three, and **the code is right**. `insecure` is a
local per-target *transport flag*, not a secret and not instance configuration — it describes A's
connection to B and has no counterpart on B. Read S3's fourth column name as withdrawn. This matters
now in a way it did not on 2026-08-21, because S11 makes `insecure` load-bearing locally.

#### Logos: replicated, but off by default — a tension worth naming

Logos are **in scope** and do replicate; S9 keeps them off the *unconditional* per-cycle set for two
mechanism reasons (a destructive `clear_existing` bulk-delete and a streaming-upload cost that is
wrong to pay every interval), and they run per target behind `sync_logos`. That is a mechanism
decision the principle does not disturb. What the principle **does** disturb is the **default-OFF**
toggle: a replica that silently arrives without branding is exactly the failure epic `f5a5j` is named
for ("...has lost its guide data and its branding"). Under a faithful-copy default, `sync_logos`
should default **ON**, with the per-cycle cost addressed by a slower sub-interval rather than by
omission. Recorded as a tension, defaulting toward replication.

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
- Beads `enhancedchannelmanager-msqf7`, `1td94`, `hne7k`, `kdz6p`, `avrix`, `a3lby` — the measured history the 2026-08-22 amendment below rests on.

---

## Amendment, 2026-08-22 (a): one-time credential provisioning at sync-target setup, distinct from per-cycle sync (bead `enhancedchannelmanager-wd20y`)

> **SUPERSEDED, same day, by [Amendment 2026-08-22 (b)](#amendment-2026-08-22-b-provider-credentials-cross-on-every-cycle-supersedes-amendment-a).**
> Everything below describes the one-time provisioning design that shipped in PR #908 and was
> removed two days later. It is kept because the reasoning is the record of what was weighed —
> including the objections the PO overruled — and a reader who finds a stale reference to
> "Provision Credentials" needs to be able to find out what it was. **Nothing in this section
> is current.** In particular **S10, S11, S12(c), S13, INV-2, INV-3, INV-4, INV-8 and INV-9 are
> void**; read amendment (b) for what replaced each.

- **Status of this amendment**: **Superseded 2026-08-22 by amendment (b). Was: Accepted — PO-ratified 2026-08-22.** Drafted 2026-08-21 by the
  architect in response to the PO direction recorded on `wd20y` ("my users would like it to be a hot
  standby"); four decisions were reserved for the PO and all four were ruled on. **Two rulings went
  against the architect's recommendation** and are recorded as such, with what the PO accepted in
  choosing them, in [PO rulings](#po-rulings-2026-08-22).
- **Amends**: **S3** (never-sync set) and **S7** (security controls), by adding decisions
  **S10–S13** that extend them, in the idiom of [ADR-012](ADR-012-dbas-absorption-approach.md)'s
  2026-06-16 amendment. The original S3/S7 rows are left as written; where their reading changes,
  the change is stated here explicitly.
- **S9 is unchanged and must stay unchanged.** The per-cycle importer set is the thing this
  amendment is careful not to touch, and under the ratified harvest design (S10) it is the
  load-bearing guarantee rather than a formality. See INV-2.
- **Depends on**: `enhancedchannelmanager-avrix` and `enhancedchannelmanager-a3lby`, both shipped in
  PR #907 (v0.18.1-0134). `avrix` is a **safety** dependency, not a sequencing preference — see
  [The mitigation this feature removes](#the-mitigation-this-feature-removes-and-what-replaces-it).
- **Does not stand alone**: the security companion (`docs/security/threat_model_dbas_import.md`
  **Addendum D**) states that it **gates build**. Several of its rows become false the day this
  ships. See [Addendum D must be amended too](#addendum-d-must-be-amended-too-and-it-is-the-build-gate).
- **Does not revert**: `enhancedchannelmanager-msqf7`. See
  [Why msqf7 stays](#why-msqf7-stays-and-why-this-amendment-says-so-out-loud).

### The gap this closes

A replica arrives structurally complete. The end-to-end UI acceptance run (`kdz6p`) measured 316
channels, 779 groups, 3 profiles, 948 profile memberships including the enabled flag, 316 logo
bindings and 183 EPG links all crossing A→B — and the provider password present in A's database 316
times and in B's **zero**, exactly as S3 intends. What B does not have is a working provider
credential, so every stream URL on B reads `.../live/*REDACTED*/*REDACTED*/.ts` and 404s, and
`channels_with_no_playable_stream` correctly reports 316 on every cycle (`1td94`).

The distance from that replica to a hot standby is **one credential entry, once** — `1td94` proved
the recovery end to end (0/59 serving → 53/59). Today the product frames that entry as a *recovery
procedure*. This amendment ratifies framing it as a *setup step*, and ratifies **only** that.

### What this ratifies, and what it does not

**Ratified:** a provider credential may reach B by **one explicit, operator-initiated, audited,
TLS-verified provisioning action** performed at (or after) sync-target setup, taking its values from
A's own provider accounts (S10).

**Not ratified, and unchanged by this amendment:**

- **The per-cycle payload.** Not one byte changes. The per-cycle path stays topology-only, keeps
  `msqf7`'s redaction untouched, and gains no exemption, no flag and no "provisioning mode".
- **The never-sync SET itself.** `users` stays permanently never-sync. Credential *values* stay out
  of every recurring cycle. S3's reversibility column — "the never-sync set is one shared constant
  in the redact/category-filter layer, **removable per-category if justified**", behind "an
  explicit, separately-ratified opt-in" — is the door this amendment walks through. It walks
  through it for **one new non-recurring path**, not for the recurring one.
- **S2 (one-way A→B)** and **S9 (per-cycle importer set)**.

### The distinction this turns on: recurrence, not secrecy

S3's rationale is about **recurrence**, and the shipped operator guide states it in exactly those
words: credentials are "Redacted before transmission to avoid streaming live secrets **on a
recurring schedule**" (`docs/user_guide/backup-restore/cross-instance-sync.md`, the Credentials row
of the what-crosses table). A one-time, operator-initiated action is not what that objects to. It
differs on every axis the rationale names:

| Property | Per-cycle sync (S3/S9, unchanged) | One-time provisioning (S10) |
|---|---|---|
| What starts it | `task_scheduler`, unattended, or force-sync | an authenticated admin, one explicit request |
| How often | every interval, forever | once per operator decision |
| Provider credentials on the wire | **none, in any field, in any URL position** | the named fields, for the named destination accounts |
| Where the value comes from | A's live config, read then **redacted** | A's live config, read then **written to B** — plus one operator-supplied field, the Schedules Direct password, which exists nowhere on A to read |
| Persisted on A for the purpose | n/a | **nothing** (INV-3) |
| TLS | `verify=True` default; `insecure` escape hatch permitted | verification **mandatory** while provisioned (S11) |
| Journal | one run row, `redaction_mode: topology_only` | one `sync_provision_credentials` row per attempt (S13) |

Read that fourth row carefully, because the ratified design (S10) makes the two paths differ there
by **one verb and nothing else**. Both read the same values, off the same records, by the same key
sets. One removes them; the other sends them. That is the whole safety margin, and it is why the
guarantees below are placed on the *code path* rather than on the payload.

**Two operator-facing sentences must change with it, and they are the same distinction.** The
guide's "Cross-instance sync never puts a provider credential on the wire" is true of the cycle and
would be false of a provisioned target read as a whole; and the same page presents credential
re-entry on B as a *recovery* procedure. Both must be rewritten to say that the cycle never carries
a credential and that setup may, once, deliberately — the wording is where an operator forms their
model of what this product does with their password, and `msqf7` is the record of what it costs when
that wording is ahead of the behaviour.

**"One-time" must be a property of the code path, not of anybody's intention.** An amendment that
merely *says* "we only do this once" is one refactor away from being false, and under S10 there is
no operator keystroke standing between A's secrets and the wire to make the refactor obvious. INV-2
is therefore not a formality: it is the only structural guarantee left, and it must be enforced as a
**reachability** property (the sync task cannot reach the provisioning writer), not merely as an
absent registry entry.

### Why `msqf7` stays, and why this amendment says so out loud

`msqf7` did not stop credentials *reaching* B. It stopped them being smuggled **implicitly** inside
every stream URL's path segments, on every scheduled cycle, in a field nothing inspected, while the
product told the operator credentials were stripped. Its live proof: A's database held the
credential 316 times, B's held it zero.

Under a hot-standby design the operator still wants a credential to reach B — **deliberately, by a
designed and audited path, never accidentally by a route no one is watching.** Those are opposite
things that look similar from a distance, and the distance is where the mistake gets made. So,
recorded here so that a later reader cannot mistake this amendment for permission:

> **Relaxing `_redact_credentials_deep`, `_scrub_credential_urls`,
> `_rewrite_known_credential_segments` or `_collect_credential_values` is NOT an implementation
> option for this feature, and "we provision credentials now anyway, so the redactor is redundant"
> is the specific wrong conclusion this paragraph exists to prevent.** The redactor governs the
> *recurring* path. Provisioning is a different path. If provisioning is ever implemented by
> weakening the redactor, it has been implemented wrongly, whatever it does on the day.

Note also `1td94`: the redaction is load-bearing for *correctness*, not only for secrecy. The
sentinel is what makes a dead address recognisable as dead, to the playability counter and to the
stream matcher. A relaxed redactor re-arms `1td94`'s failure — a placeholder that wins Tier-1
matching forever — as well as `msqf7`'s leak.

### S10 — one-time credential provisioning (extends S3)

| # | Decision | Choice | Alternatives considered | Exit path |
|---|---|---|---|---|
| **S10** | Destination provider credentials | A sync target gains an **explicit provisioning action**: an authenticated-admin request that **harvests the provider credential values from A's own provider records** and writes them onto B's replicated provider accounts, once, and records the fact. The harvested values are **transient** — read at action time, written, discarded; **never persisted on A for this purpose** (no column, no cache, no queued payload — INV-3). The writable field set is **exactly the field set the per-cycle redactor names as redacted for that entity**, and the harvest reads those same dotted paths off the RAW record (INV-6), so the two cannot drift. | (a) **Operator-typed, request-scoped values** — the architect's recommendation, **not chosen**; see [PO rulings](#po-rulings-2026-08-22) for what was accepted in preferring the harvest. (b) **Persist the credential on the SyncTarget row** — rejected: a new at-rest provider secret on A, a new decryption path, and a value a future scheduled job could read. (c) **Extend the encrypted-artifact path (`u81kh`/D12) to sync** — rejected: that is a one-shot migration artifact, not a live push, and it would put the whole credential set in flight to solve a one-field problem. | Revert = remove the route, its UI affordance and the marker column. Nothing in the per-cycle path changes, so there is nothing there to unwind. Credentials already written to B are **not** unwound by reverting — see [What a de-provision cannot guarantee](#what-a-de-provision-cannot-guarantee). |

**The harvest is a new WRITE, not a new READ, and the distinction is the whole risk assessment.**
`routers.backup._collect_credential_values` already walks the RAW gather on **every cycle** and
harvests exactly these values, by exactly these key sets (`_REDACT_KEYS` /
`_PROVIDER_IDENTITY_KEYS`) — that is how `msqf7`'s literal-match path-segment rule is possible at
all. So A's process already holds every value S10 would send, on every scheduled run, today. S10
adds no read capability, no new decryption, and no new place the secret lives. What it adds is a
code path that **sends** them.

That cuts both ways and both halves belong in the record:

- **It bounds the new exposure.** No new surface on A learns a secret it did not already handle.
- **It removes the barrier that made a mistake self-evident.** Under the operator-typed alternative,
  moving provisioning onto a cycle would have been *impossible* without first inventing somewhere to
  store the value, and INV-3 would have blocked that. Under the harvest, the values are already in
  the cycle's own memory: making the cycle push them is a call edge, not a redesign. **INV-3
  therefore no longer prevents a recurring push under S10.** INV-2 does, and INV-2 alone. This is
  stated plainly so nobody reads INV-3 as covering more than it does.

**Implementation shape, named so it is not re-derived.** For each entity, compute `redacted_fields`
from the redacted payload exactly as `_build_create_payload` does today, then read those same dotted
paths off the **raw** record with `credential_sentinel.value_at_path` — the round trip that module
already documents. This makes INV-6 true by construction rather than by a maintained list, which
matters more under the harvest than it would have under typed input, because **no human now reads
the values before they cross**.

**Scope — the credential-bearing account types that exist in this codebase**, enumerated rather
than generalised from the XC case, and each checked for whether a harvest can actually read it:

| Category | Type | Credential shape | Harvestable from A | In scope for S10 |
|---|---|---|---|---|
| M3U account | `XC` | `username` + `password` fields | **Yes.** Dispatcharr's `M3UAccountSerializer` marks `password` `write_only` but its `to_representation` re-adds it (`data["password"] = instance.password or ""`) for any caller at `user_level >= 10`, which ECM always is; measured again on 0.29.0, `/api/m3u/accounts/` returns both `username` and `password`, and `kdz6p` read the value out of A's database 316 times | **Yes** |
| M3U account | `STD` (plain M3U) | The credential is **inside `server_url`** (`get.php?username=…&password=…`); there are no username/password fields on this type | **Yes.** The raw gather must carry it, or `_scrub_credential_urls`'s whole-value rule would have nothing to replace with the sentinel — the sentinel it produces is what makes `server_url` a named re-entry field today | **Yes** — and note the provisioned value is a **URL**, not a password. A design that writes "username and password" onto B covers XC and silently misses this type |
| M3U account | `STD` — HDHomeRun tuner | none (a LAN tuner URL) | n/a | Nothing to provision |
| EPG source | `xmltv` | credential embedded in `url` | **Yes.** Measured on 0.29.0: `/api/epg/sources/` returns `url` | **Yes** — same URL shape as `STD` |
| EPG source | `schedules_direct` | `username` + `password` | **`username` yes; `password` NO — and not "difficult", impossible.** The serializer marks it write-only with no admin re-add; Dispatcharr never returns it and SHA1-hashes it at fetch (`docs/dispatcharr_api.md` §EPG Sources). `dbas/importers/epg_sources.py` records the same measurement and its consequence: "a live gather does not normally carry one, so there is usually nothing at that path to strip OR to re-check" | **Yes** — `username` harvested, `password` **operator-supplied** for this one field. See below |
| EPG source | `dummy` | none | n/a | Nothing to provision |

#### Schedules Direct: the one field the operator supplies

**PO direction, 2026-08-22, verbatim: "this product doesn't work if it doesn't replicate everything
to a second instance."** That settles it. `schedules_direct` is **IN scope**, and because its
password exists nowhere on A to read, that one field is **operator-supplied**.

This is not a reversal of the harvest ruling and should not be read as one. The harvest ruling
governs **where a value comes from when it can be read**; it was never a ruling that a field which
cannot be read gets dropped. Impossible-to-harvest and out-of-scope are different things, and the
earlier draft conflated them. A standby that silently loses guide data does not meet the bar the PO
just stated.

So the input model is: **harvest `XC`, plain-M3U `STD` and `xmltv` automatically; prompt for the SD
password only, and only when a `schedules_direct` source is actually present on the target being
provisioned.** Every constraint already ratified applies to the supplied value exactly as it applies
to a harvested one — request-scoped, **never persisted on A** (INV-3), never reachable from a cycle
(INV-2), audited by field name only (S13), and inside the closed two-category set (INV-6). One field
of typed input does not reopen the input-model decision for anything else.

**The `source_type`-driven statement survives, with its meaning inverted.** It was specified because
the reporting that names what B is missing
(`dbas/importers/epg_sources.py::_report_credentials_still_missing`) derives its list from
`redacted_fields` — the fields the redactor *sentinelled* — and an SD `password` was never in the
gathered payload, so it is never a redacted field and is **never reported**. The rule that makes
that reporter correct everywhere else ("a source with no credential produces no redacted field, so
it is never an action item") is wrong for exactly this type, where absence means *unreadable*, not
*unset*. Presence is therefore unknowable and no presence check can drive this. Driven by
`source_type` instead, the statement becomes **"this field needs your input"** rather than "this
cannot cross" — surfaced when a `schedules_direct` source is present, so the operator is prompted
rather than left to discover a gap.

**What ECM still cannot do for this field, stated so it is not mistaken for a defect later.** The
write-only property runs in both directions: B does not return the SD password either, so after
provisioning ECM can report that it **wrote** the value, never that B **holds** a working one. A
mistyped SD password therefore surfaces as B's EPG source failing to fetch, not as a provisioning
error. The remedy is the ordinary one — re-provision, which `a3lby`'s edit affordance already makes
cheap. The same property has one benign consequence worth recording: because B never returns it, an
SD password **cannot ride the per-cycle destination read back to A**, so this field is outside the
inbound exposure that drives S11 and threat-model row D16.

**Explicitly outside S10, and they stay outside**: ECM's own settings secrets
(`_SETTINGS_CREDENTIAL_FIELDS` — `dispatcharr_api_key`, `emby_api_key`, `plex_token`,
`smtp_password`, `telegram_bot_token`, `mcp_api_key`, …), alert-method secrets, cloud-target and
sync-target credentials (`SYNC_NEVER_CREDENTIAL_COLUMNS`), and `dispatcharr_users`
(`SYNC_NEVER_CATEGORIES`). Under a harvest design this boundary does more work than it did under
typed input — a harvest is a loop over records, and a loop widens by accident — so the provisioning
surface is a **closed set of two named entity categories**, M3U accounts and EPG sources, enforced
in code rather than by the shape of whatever the gather happens to return.

### S11 — mandatory TLS verification on a provisioned target (amends S7)

S7 already says the per-target `insecure` escape hatch is "forbidden-by-construction if the payload
is ever non-redacted". **There is no such construction to inherit, and the reason is worth stating
precisely rather than blaming the most recent change.** `insecure` has been editable on
`PUT /api/sync-targets/{id}` **since the router was first written** — `git log -S` on both
`insecure: Optional[bool]` (the update model) and `target.insecure = req.insecure` (the handler)
returns exactly one commit, `ed98f32f` (2026-06-19, bead `vigbu`, the original SyncTarget CRUD
router). It was never write-once at the API. PR #907 does not touch
`backend/routers/sync_targets.py` at all — only its test file — and the MCP `update_sync_target`
tool has always forwarded the same field to the same route (`mcp-server/tools/sync_targets.py`).
What `a3lby` changed in #907 is the **UI**: the write-once property was only ever a property of the
*form*, and now not even that. So this decision **builds**
the construction S7 assumed, rather than restoring one — and a UI-only guard would satisfy neither
the REST nor the MCP surface.

| # | Decision | Choice |
|---|---|---|
| **S11** | `insecure` vs a credential on B | **A sync target may not be in both states "TLS verification disabled" and "the destination holds a provider credential".** The gate triggers on **recorded OR observed** state (PO-ratified 2026-08-22 — see [Credentials ECM did not write](#credentials-ecm-did-not-write-the-observed-state-gate)): *recorded* = ECM's own provisioning marker; *observed* = a credential seen present on B by the cycle that already reads B's account rows. The two writes are refused symmetrically, whichever order they arrive in: provisioning is refused on a target with `insecure=true`, and setting `insecure=true` is refused on a target in either state — in both cases with the reason **and the remedy** stated, never silently. Clearing `insecure` (true→false) is always allowed. The recorded marker is a **per-row column** on the sync-target row, set on a successful provisioning; it is monotonic **except through one audited transition**, an explicit de-provision (below). Both refusals are enforced at the **service layer**, so REST and MCP are covered by one predicate. |

**Why the marker records "has been provisioned", and why it is a column.** The exposure `insecure`
bounds is not only the outbound push. **The per-cycle destination read pulls B's provider credential
back to A.** `dbas/importers/m3u_accounts.py::_report_credentials_still_missing` inspects the
destination's own account rows to decide what B is still missing, and its own measured note records
that on Dispatcharr 0.29.0 `/api/m3u/accounts/` returns **both `username` and `password`**. So once
B holds a provider credential, every subsequent cycle over an unverified-TLS connection carries that
credential across the network — inbound, unattended, on a schedule. Nothing about A's *intentions*
ends that; only B not holding the credential does. And the marker must be a **column on the
sync-target row**, not an inference from journal or execution history: `5dp92` records that
execution history is keyed on a **reusable** target id, so a freshly created target can inherit a
deleted target's rows — an "is there a provisioning row for this id?" gate would inherit a
*provisioned* verdict it never earned, or lose one it did.

#### Credentials ECM did not write: the observed-state gate

**PO ruling, 2026-08-22 (the architect's recommendation, accepted). Closes bead
`enhancedchannelmanager-3dmgr`.** The marker above records **what ECM wrote**. It does not record
**what B holds**, and those differ in a case that is live today, independently of this feature: the
credential re-entry that ECM's own operator guide currently documents as the *recovery* procedure is
typed into B by hand, so no marker exists for it. An operator who followed the guide and has
`insecure=true` is shipping B's live provider credential to A — **inbound**, over an unverified
channel, on every cycle, unattended — because the per-cycle destination read fetches B's provider
account rows and `/api/m3u/accounts/` returns both `username` and `password` to an admin caller on
0.29.0.

So the gate triggers on **recorded OR observed** state. This is a **widening of INV-4, not a
replacement**: everything already ratified stands — both write orders, service-layer enforcement,
the marker column, the de-provision escape and its five-point contract.

**Presence only, and no new read.** The observation is
`credential_sentinel.credential_is_present()` — non-empty and not the sentinel — evaluated against
account rows **the cycle already fetches**, inside the re-entry reporter that already calls it. No
new request, **no value comparison** (the same prohibition as S12's staleness signal, for the same
reason), and no secret stored. One new **non-secret** column is required and is stated rather than
absorbed: the write path (`PUT insecure=true`) has no live view of B, so the observation must be
recorded as a fact — a `destination_credential_observed_at` timestamp, holding a boolean's worth of
information and never a value. It clears when a cycle observes absence, by the same presence check.

**The two markers are distinct and must not be merged.** *Recorded* governs the de-provision
transition; *observed* governs only the refusal. That distinction decides the remedy, because **an
observed credential ECM did not write has no marker to clear, so de-provision is not the remedy for
it** — there is nothing on A to de-provision, and ECM will not delete a credential an operator
placed on B by hand. S11 requires every refusal to state a remedy, so this one states both, with
their consequences:

1. **Install a valid certificate on B and clear `insecure`.** This is the primary remedy: it keeps
   the standby working and ends the exposure. Recommend it first.
2. **Remove the credential from B, on B.** The next cycle observes absence, clears the observed
   marker, and `insecure` becomes settable again — at the cost of B no longer serving, which is the
   thing the operator presumably wanted.

Neither is "ECM does it for you", and the refusal must not imply otherwise.

**The window this leaves, bounded.** The observation happens on a cycle, so a credential typed into
B at time *T* is not seen until the next cycle — and that cycle's own destination read is what sees
it, so the credential crosses once more before the gate closes. The exposure is therefore bounded at
**exactly one further cycle after the credential appears on B**, i.e. one sync interval, and zero
cycles thereafter. It cannot be driven to zero from A's side without a new fetch, which constraint
(1) above forbids; one interval is the honest floor of a presence-only gate and is recorded as such
in threat-model row D16.

#### The de-provision escape

**PO ruling, 2026-08-22 (against the architect's recommendation of a permanent, symmetric refusal).**
An operator may leave the provisioned state by an **explicit de-provision action** — never as a side
effect of ticking `insecure`, which stays refused while the marker is set. De-provisioning is a
first-class operation with a contract, because a cosmetic one would be worse than no escape at all:

1. **The clear is ATTEMPTED on B.** De-provision writes the credential fields on B's provider
   accounts back to unset — the same field set S10 wrote, resolved the same way (INV-6). It is a
   real write to the destination, not a local flag flip.
2. **The marker flips only if that write succeeds.** A partial or failed clear leaves the marker
   **set**, leaves `insecure` refused, and reports which accounts were not cleared, by name. There
   is no "close enough": the marker means "B may still hold a credential", and a failed clear is
   exactly that state.
3. **Failure says so.** The operator is told which destination accounts still hold a credential and
   that the target remains provisioned. Silence on a failed de-provision would recreate, in the
   safety control itself, the reporting failure `1td94` and `ukjx5` were filed for.
4. **It is audited like a provisioning event** (S13): same journal category, its own action type,
   the same fields — actor, surface, target, entity ids and names, field names, TLS state, outcome —
   and, uniquely, the per-account success/failure breakdown that decides the marker.
5. **The operator is told what it cannot guarantee, at the moment they do it** — not in a doc.

#### What a de-provision cannot guarantee

This is the residual the PO accepted in choosing the escape, and it is the whole of what a
*successful* de-provision leaves behind. **A successful de-provision guarantees exactly one thing:
B's provider account rows no longer hold the credential, so B will not re-authenticate with it.**
Everything below survives it:

- **B's own stream rows.** Once B refreshed with a working credential, B's stream table holds URLs
  with the credential in their **path segments** — this is the normal shape, not an edge case:
  `msqf7` surveyed 1,409,363 provider URLs and found 100% path-credentialed. Clearing an account
  field does not rewrite those rows, and they remain valid addresses.
- **B's backups and exports.** Any DBAS artifact, cloud-target upload or support bundle B produced
  while provisioned carries whatever B held at the time. ADR-012's amendment keeps a *standard*
  artifact clean, but B's encrypted artifacts and B's own database copies are outside that.
- **B's logs and status fields**, including any upstream error body echoed into `last_message`.
- **Anything downstream of B that consumed B's output while provisioned** — B's own M3U/HDHR output,
  its clients, and any proxy or cache in front of them.
- **The provider side.** De-provisioning is **not revocation**. The credential stays valid at the
  provider until the operator rotates it there, which is outside ECM entirely.
- **Time already spent.** Every cycle between provisioning and de-provisioning is unrecoverable.

Two operational consequences follow and must be surfaced with the action. First, **the standby does
not immediately go dark**: B keeps serving from its existing credentialed stream rows until its next
refresh fails, so "it still works" is not evidence the clear did not happen. Second, **the
security-complete action is rotating the credential at the provider** — de-provision is the step
that stops B *re-acquiring* it, not the step that makes it safe.

**Alternatives rejected** (retained because the reasoning still governs the *shape* of the escape,
even though the escape itself was ratified):

- **Gate only at provisioning time**, leaving `insecure` freely editable afterwards. Rejected: that
  is the hole described above, and it fails silently — nothing refuses the later edit, the tick box
  appears to be accepted, and the next cycle simply runs unverified.
- **A clearable marker with no write to B** ("currently holds", flipped locally). Rejected, and this
  is the specific weakness the de-provision contract exists to close: a local flip is A's *belief*
  about B, falsifiable by an edit that changes nothing on B. Requiring a succeeded write to B is
  what makes the escape mean something.
- **Auto-clear `insecure` when provisioning** (silently tighten instead of refusing). Rejected:
  silently overriding a security-relevant setting the operator deliberately set is unauditable at
  the moment of the click, and denies them the information they need *before* handing over a secret.
- **De-provision as a side effect of enabling `insecure`.** Rejected: it buries a destination write
  inside a settings toggle, and step 2's failure path has nowhere to report to. De-provision is its
  own action, taken deliberately, with its own outcome.

*Reconciliation note:* S3's never-sync column list names `insecure` alongside `credentials`,
`credential_version` and `token_revoked_at`; the shipped constant `SYNC_NEVER_CREDENTIAL_COLUMNS`
names only the latter three. This is harmless today — `sync_targets` is ECM's own table and is not a
gathered Dispatcharr category at all (`SYNC_CONFIG_CATEGORIES`), so no path assembles it — but with
`insecure` now load-bearing for S11, the doc and the constant should be reconciled rather than left
to be discovered.

### S12 — credential rotation and staleness

A one-time-provisioned standby goes stale the moment the provider password changes on A. Doing
nothing here produces the failure this epic exists to end: a standby that reports healthy and cannot
serve, discovered at the moment it is needed.

| # | Decision | Choice |
|---|---|---|
| **S12** | Rotation | **(a) Operator-initiated re-provision is IN SCOPE** — it is the same action as the first provisioning, re-run, and because provisioning is part of sync-target **setup**, `a3lby`'s shipped invariant applies to it directly: "any sync-target field an operator can set at creation can be corrected afterwards without destroying the target". Under S10 it needs no input at all: A re-reads its own current values and writes them again. **(b) A staleness signal is IN SCOPE, stated as an invariant (INV-8), not as a mechanism.** **(c) Scheduled or automatic re-push is FORBIDDEN** — that is exactly the recurring transmission S3 exists to prevent. |

**(c) is the one the harvest design puts under real pressure, so it is stated as a rule rather than
left implicit.** Under operator-typed input, an "auto-heal stale credentials" feature would have had
nothing to send. Under S10 it has everything it needs: the cycle already holds the current values,
and the staleness signal of (b) would hand it a trigger. **A detected-stale credential must never
cause a push.** It causes a report; the operator decides. Enforcement is INV-2 — the sync task
cannot reach the provisioning writer — which is why INV-2's enforcement is a reachability test and
not a registry check.

**How staleness must NOT be detected:** by comparing credential *values* — that would pull B's
secret back to A on a schedule to answer a question, which is the mirror image of `msqf7`. The
signal must be built from state the cycle **already** reads: the destination provider account's
`status` / `last_message` / `stream_count`, which the destination read already returns and which
`avrix` already measured going to `status=error` ("No streams returned from Xtream Codes provider")
when the replica cannot ingest. If a truthful signal cannot be built from already-fetched state, the
correct outcome is to **say so and file it** — never to invent one, and never to fall back on a
value comparison.

**Consequence for the operator, stated for each option so the trade is visible:** with (a)+(b) — the
ratified pair — an operator whose provider password changed sees the standby say so on the next
cycle and fixes it with the same control they used at setup. With (a) alone, the standby stops
serving silently and is discovered during the failover it was built for. With neither, correcting a
rotated credential means delete-and-recreate, which `a3lby` was filed to end.

### S13 — what a provisioning event records

This is `msqf7`'s risk inverted. `msqf7` was dangerous because a secret moved on a path **nobody was
watching**. A deliberate path that is equally unwatched is no better — and under the harvest design
there is no operator keystroke to serve as an informal record, so the journal row is the only trace
that the value moved at all.

| # | Decision | Choice |
|---|---|---|
| **S13** | Audit | Every **provisioning** and every **de-provisioning** attempt — success *and* failure — writes exactly one operator-journal row, alongside the existing `sync_outbound` / `sync_insecure_tls` rows written by `tasks.dbas_sync_client.audit_insecure_cycle`. The row records: the actor (which admin principal, and **which surface** — REST or MCP), the sync target id and name, the destination entity type and id and the operator-facing **name** of each account written, the **field names** written or cleared, the count, the TLS verification state at the time, and the outcome. A de-provision row additionally carries the **per-account success/failure breakdown**, because that is what decides whether the marker flips (S11). It records **no value, no fragment of a value, and no masked tail of a value.** The action types are distinct (e.g. `sync_provision_credentials` / `sync_deprovision_credentials`) so each is greppable and countable on its own. |

Three properties make the rows useful rather than decorative:

- **They are the only place a provisioning can appear.** A cycle never writes one. So a
  `sync_provision_credentials` row whose actor is the scheduler is not a log line, it is **the
  alarm** — the signal that the one-time path has become recurring.
- **They are written at the service layer, not the UI layer.** There are at least two surfaces that
  reach sync-target mutation (the REST router and the MCP service principal, both called out in
  `routers/sync_targets.py`'s own gate docstring). A gate or an audit row that lives in the frontend
  covers neither.
- **A de-provision row is the only evidence the escape was real.** The marker flipping is a
  consequence of the write; the row is where the write's per-account outcome is recorded, and it is
  what an operator or an auditor reads afterwards to know which accounts on B were actually cleared.

### The mitigation this feature removes, and what replaces it

`avrix` established by live measurement that a replica which inherits no group-enable state either
ingests **nothing** (0 streams where the source ingests 316, aborting with an error that blames the
provider) or — with `auto_enable_new_groups_live` at Dispatcharr's default of `true` — enables **all
777 categories** and begins pulling the provider's entire 53,661-stream catalogue.

Today that second outcome needs four things to hold, and **the third is the mitigation**: (1) the
source account left at the true default, (2) groups narrowed by hand, (3) **someone manually
entering provider credentials into the replica's account**, and (4) that account refreshing. Under
`hne7k`'s Option A ruling the replica's provider accounts are decorative, so nothing performs step 3
for the operator — an engineer had to do it by hand to reproduce the case at all.

**This feature removes step 3 by design, and the harvest design removes it more completely than the
typed alternative would have**: not only does something now perform step 3, it performs it without
anyone reading the value. From the moment it ships, steps 1 and 2 alone suffice, step 4 happens on
any refresh, and nothing else tells the operator. What keeps it safe is `avrix`'s shipped behaviour:
the primary's per-group enabled selection is written to the replica's provider account, remapped
onto the replica's own group ids, **on every cycle**, and what cannot be delivered is counted and
named (`provider_group_selection_unapplied` / `_details`) in language an unattended run surfaces.

Recorded so that nobody reverts or weakens `avrix` without seeing the consequence:

> **`avrix` is a safety control for this feature, not a fidelity nicety.** Weakening it — deferring
> the selection to account-creation only, dropping the per-cycle re-application, or removing the
> unapplied counters — converts a two-category replica into one that may pull a 53,661-stream
> catalogue against the operator's own provider account, using credentials this feature put there.
> Any change to `dbas.importers.m3u_accounts`'s group-selection application is a change to this
> amendment's safety case and must be read against it.

### Acceptance criteria, as invariants

Stated as properties, not as reproductions. Each names the enforcement the build must land; until
that enforcement exists the line is a **convention, not a guarantee**, and must not be cited as one.

| # | Invariant | Enforcement the build must land |
|---|---|---|
| **INV-1** | **No provider credential value leaves A on any recurring cycle** — scheduled or force-sync — in any field, in any URL position (userinfo, query string, path segment), for any account type. The XC stream URL is one example of the property, not the specification. | Unchanged: the existing `msqf7` suite plus `_redact_credentials_deep` over every gathered section. This amendment adds **no** exemption to either. |
| **INV-2** | **The provisioning writer is unreachable from a cycle.** Not an `ImporterStep`, not in `sync_config_importer_steps()`, and **not reachable by any call path** from `tasks.dbas_sync` / `tasks.dbas_sync_engine`. | **Two tests, and under S10 this is the load-bearing one.** (i) the step registry contains no provisioning step, in the idiom of the existing `SYNC_NEVER_CATEGORIES` test; (ii) a **reachability/import guard** asserting the sync task and engine modules do not import the provisioning writer, transitively — the same idiom as the SSRF chokepoint CI grep this ADR already relies on. A registry check alone is insufficient: the cycle already holds the values, so a direct call would bypass the registry entirely. |
| **INV-3** | **A persists no provider credential for provisioning purposes.** No column, no cache, no settings key, no queued job payload; harvested values, and the one operator-supplied Schedules Direct password, are read, written and discarded within the request. | Schema review + a test that no harvested value is written to any persistent store. **Scope note:** under S10 this no longer *prevents* a recurring push (the cycle holds the same values in memory already) — it bounds at-rest exposure and keeps `sync_targets` free of provider secrets. INV-2 is what prevents recurrence. |
| **INV-4** | **A target is never both `insecure` and holding a provider credential on its destination**, on any branch, in any order of operations, on any surface (REST or MCP) — where "holding" is **recorded OR observed** (ECM's provisioning marker, or a credential seen present on B by a cycle). Provisioning on an `insecure` target is refused; enabling `insecure` on a target in either state is refused, with the reason **and** an applicable remedy; clearing `insecure` is always allowed. | One predicate, called by both the create and update paths at the service layer, with tests for **both orderings**, for **both state sources**, and for the MCP surface. A UI-only guard does not satisfy this. The observed half must be **presence-only** (`credential_is_present` against rows the cycle already fetches) — a test must assert no new destination request and no value comparison is introduced. |
| **INV-5** | **Every provisioning and de-provisioning attempt is recorded, and only such an attempt records one.** Success and failure both write exactly one row; no cycle ever writes one. | Journal-row assertions on all four paths (provision/de-provision × success/failure), plus a test that a full sync cycle produces zero rows of either action type. |
| **INV-6** | **The provisionable field set equals the redacted field set**, per entity, derived from the same function — and the de-provision clears exactly the set the provision wrote. | A test that derives both sides from `strip_redaction_sentinels` / `_build_create_payload` / `value_at_path` rather than from a literal, covering `STD` (`server_url`), `xmltv` (`url`) and `schedules_direct` (harvested `username` beside an operator-supplied `password`) as well as `XC`. Under the harvest this is the only thing standing between "the fields we meant" and "whatever the gather returned". |
| **INV-7** | **After setup completes, a replica serves the streams its source serves, with no further operator action** — or the run says plainly that it will not, naming what is missing. Silence is only permitted when it is true. | The `credential_reentry` / `provider_group_selection_unapplied` reporting already shipped, extended to the provisioned case, **plus** the `schedules_direct` prompt-and-statement driven by `source_type` rather than by a presence check (S10) — "this field needs your input", surfaced when such a source is present. Live verification against a real provider, read from B, not from the run's own report. |
| **INV-8** | **A standby whose provisioned credentials have stopped working says so**, on the cycle that can observe it, without any credential value crossing the wire to determine it, and **without triggering a push**. | Built from destination state the cycle already reads (account `status` / `last_message` / `stream_count`). If that cannot carry the signal truthfully, say so and file it — never a value comparison, and never an auto-heal. |
| **INV-9** | **A de-provision that did not clear B does not clear the marker.** The provisioned marker flips only on a destination write that succeeded for every account it targeted; any failure leaves the marker set, leaves `insecure` refused, and names the accounts still holding a credential. | Tests for the partial-failure and total-failure paths asserting the marker is unchanged and the refusal still holds — and specifically that a destination error cannot be swallowed into a success. This is the invariant that makes the ratified escape honest rather than cosmetic. |

### Consequences of this amendment

**Positive.** The replica becomes a standby that can actually take over, which is what the operator
asked the feature for. The security posture *improves* in one respect that is easy to miss: today's
"recovery procedure" already has the operator typing the provider password into B by hand, on
whatever channel they happen to use, with no record that it happened. S10 replaces an untracked
manual step with an audited one over a verification-mandatory connection — and under the harvest the
operator never handles the value at all, so it is never in a clipboard, a password manager export or
a support chat.

**Negative / costs.**

- **The ADR's own "Negative" bullet — "B is not immediately stream-ready for credentialed sources" —
  is now conditional** rather than absolute: it holds for a target that has not been provisioned,
  and that remains the default. Nothing about the *default* posture changes.
- **B becomes a location where a provider credential lives**, deliberately, and de-provisioning does
  not undo that (see [What a de-provision cannot guarantee](#what-a-de-provision-cannot-guarantee)).
  B's database, B's backups, B's logs and B's own stream rows inherit it. The operator is choosing
  this; the product must say so at the moment of the choice, not in a doc.
- **The barrier that made "push credentials every cycle" hard to build accidentally is gone.** Under
  the ratified harvest, the cycle already holds every value a push would need. INV-2's reachability
  guard is now the only structural thing preventing it, and it must be treated as a security control,
  not a tidiness test.
- **`insecure` and a credential on B are mutually exclusive** (S11), whether ECM put it there or an
  operator did. An operator running B behind a self-signed certificate must fix the certificate, or
  give up the credential on B — by de-provisioning (and accepting the residual above) if ECM wrote
  it, or by removing it on B if they did. Existing installations that combine `insecure` with a
  hand-entered credential will start being refused, which is the point: that combination is
  bead `enhancedchannelmanager-3dmgr`, and it is exposing a live secret today.
- **Schedules Direct costs one keystroke.** Its password cannot be harvested, so the operator supplies that one field at provisioning; and because B never returns it either, ECM can report that it wrote the value but never that B holds a working one. A mistyped SD password surfaces as B's EPG source failing to fetch, and the remedy is a re-provision.
- **The blast radius of a group-selection regression grows** (see `avrix`, above).

**Exit path.** Remove the provisioning and de-provisioning routes, their UI affordances and the
marker column; the per-cycle path is untouched by construction, so there is nothing there to unwind.
The marker column is nullable additive DDL, matching the S-series precedent for `sync_targets`.
Reverting the *code* does not retract credentials already written to B.

### Addendum D must be amended too, and it is the build gate

Addendum D says of itself that it **gates build** — "no sync build bead opens until §11 is reviewed
and the D2/D3/D7 hard lines are PO-ratified". D3 (`users` never sync) and D7 (one-way only) are
untouched by this amendment. **D2 is not**, and neither are D6, D9 and two of the addendum's own
residuals. The following go from true to false on the day S10 ships, and each must be re-stated in
the threat model before the first build bead opens — this ADR amendment does not, and cannot, do
that on its own:

| Addendum D row | What goes stale |
|---|---|
| **D2** (sync payload / Information Disclosure) | "**no plaintext-cred path in v0.18.1**" and "cred-carrying continuous sync is **not shipped**" remain true of the *recurring* path and become misleading as written. D2 needs the distinction this amendment draws, plus a STRIDE row for the provisioning path itself — and under the ratified harvest that row's threat is specifically **"the one-time path becomes a recurring one"**, whose mitigation is INV-2's reachability guard rather than anything in the payload. |
| **D6** (TLS) | Reads "forbidden-by-construction if the payload is ever non-redacted (**moot while redact-by-default holds**)". It stops being moot, and the construction has to be built rather than inherited. D6 must carry S11's symmetric refusal, the de-provision transition and its residual, and the reason the exposure it bounds runs on the **return** path, not only the outbound one. |
| **D9** (Audit) | The per-run journal contract gains the provisioning and de-provisioning rows (S13), including the property that a provisioning row attributed to the scheduler is an alarm, and the per-account breakdown that decides the marker. |
| **Residual: credential re-entry friction on B** | Described as "accepted, availability, **not confidentiality**". Provisioning converts it into a confidentiality decision the operator makes deliberately, per target. |
| **Residual: `insecure=true` on a recurring channel** | Rated Low and accepted because what crosses is "topology over unverified TLS", bounded by the per-cycle audit row. On a provisioned target that premise is gone (the destination read carries B's credential back), which is why S11 **refuses** the combination rather than auditing it. |

**Done, 2026-08-22, as bead `enhancedchannelmanager-t77qd`** — a separate bead deliberately, so the
gate stayed visible in the backlog rather than being assumed satisfied by this file. The amendment
is `docs/security/threat_model_dbas_import.md` **§11.5**, which carries the rows above plus three
findings this table did not anticipate: the SSRF chokepoint guard's scanned set is the literal glob
`tasks/dbas_sync*.py`, so a provisioning writer placed on the router is **outside** it (row D1); the
guard extension itself already shipped, leaving D1's status line stale in the other direction; and
`insecure` combined with a credential an operator entered on B **by hand** — the recovery ECM's own
guide documents — is exposed today, independently of this feature, because S11's marker records what
ECM wrote rather than what B holds (row D16). That last one carried a recommendation to gate the refusal on
observed as well as recorded state; the PO **ratified it on 2026-08-22** (rulings table, row 5), so
S11 and INV-4 now carry it and `enhancedchannelmanager-3dmgr` is closed by it.

### PO rulings, 2026-08-22

All four decisions the 2026-08-21 draft reserved were ruled on. **Two went against the architect's
recommendation.** They are recorded here with what was accepted in choosing them, because a reader
six months from now needs to see that the trade was made deliberately and with the objection in
front of the decider — not overlooked.

| # | Ruling | Against recommendation? | What was accepted in choosing it |
|---|---|---|---|
| **1. Proceed** | Authorized. The feature ships, removing the mitigation that today makes the 53,661-stream case unreachable. | No — the architect recommended proceeding. | The safety case now rests on `avrix`'s shipped per-cycle group-selection behaviour. |
| **2. `insecure` escape** | **An explicit de-provision escape**, rather than the architect's permanent symmetric refusal. | **Yes.** | Ruled with the architect's objection in front of the decider: **A cannot prove B's copy is gone, and the de-provision write can fail while the flag flips anyway.** The first is answered only partially — by naming the residual honestly (S11) rather than by removing it, since no design can retract a secret from a remote instance. The second is answered in full by INV-9: the marker flips only on a write to B that succeeded for every targeted account. What remains accepted is that a *successful* de-provision still leaves B's stream rows, backups, logs and downstream consumers carrying the credential, and that the provider-side credential stays valid until rotated. |
| **3. Input model** | **Harvest from A's own provider accounts**, rather than the architect's operator-typed, request-scoped values. | **Yes.** The architect had listed harvesting under "should not proceed". | Ruled having read that it re-creates `msqf7`'s shape and cannot serve Schedules Direct. Both consequences are carried rather than argued: SD keeps its harvest-impossible field (see ruling 6), and the objection is converted into constraints — closed category set, operator-triggered, one-time, audited, never persisted, and INV-2 raised from a registry check to a reachability guard, because the harvest removes the barrier that would otherwise have made a recurring push hard to build by accident. The architect's "no at-rest provider secret" ruling (INV-3) stands unchanged. |
| **4. Rotation** | **Re-provision and staleness signal, both now.** | No — as recommended. | Staleness from state the cycle already reads; never a credential-value comparison, and never an auto-heal push (S12(c)). |

| **5. Observed-state gate** | **The `insecure` refusal triggers on recorded OR observed state**, not on ECM's provisioning marker alone. | No — the architect's recommendation, accepted. **Closes bead `enhancedchannelmanager-3dmgr`.** | Accepted on the evidence that the exposure is live today and independent of this feature: the documented recovery has the operator typing a credential into B by hand, which no marker sees, while the per-cycle destination read carries it back over whatever channel the target is configured with. Cheap because `credential_is_present()` already runs against those rows every cycle — presence only, no new read, no value comparison, no stored secret; one non-secret timestamp column. Residual accepted: a bounded one-interval window (see [the observed-state gate](#credentials-ecm-did-not-write-the-observed-state-gate)). |
| **6. Schedules Direct** | **IN scope, with an operator-supplied value for the one field that cannot be harvested.** | **Yes** — the architect had ruled it excluded, with a reporting obligation. | Direction, verbatim: *"this product doesn't work if it doesn't replicate everything to a second instance."* The architect's reasoning (the password is write-only, so harvest is impossible) was sound on its own terms and is retained; what it got wrong was treating **impossible-to-harvest as out-of-scope**. The harvest ruling governs where a value comes from *when it can be read*, and was never a ruling that unreadable fields get dropped. A standby that silently loses guide data does not meet the stated bar. |
| **7. Faithful-copy principle** | **Everything replicates by default; every exclusion must be named, individually justified and visible.** Recorded as a [governing principle](#governing-principle-po-direction-2026-08-22-a-replica-is-a-faithful-copy) on the ADR body, not in this amendment, because it governs the whole document. | **Yes, and explicitly so** — direction, verbatim: *"You need to update the ADR, Architect persona be damned, with the fact that all settings must replicate."* | It inverts the default this ADR was written under. Every existing exclusion is reclassified in the [exclusion register](#exclusion-register-reclassified-against-the-2026-08-22-principle) into technically-impossible, specific-harm, or not-yet-built; three entries from the 2026-08-21 per-blob ruling and two prior decisions (`hne7k` attribution, `sync_logos` default-OFF) are recorded as tensions defaulting toward replication rather than resolved silently. Scope only — no control is weakened by it. |

Scheduled or automatic re-push is forbidden under every ruling.


---

## Amendment, 2026-08-22 (b): provider credentials cross on every cycle (supersedes amendment (a))

- **Status**: **Accepted — PO-ratified 2026-08-22.** Third and final ruling of that day; the PO
  ruled three times as the design was walked from setup-only, to cascade-on-change, to this.
- **Supersedes**: [Amendment 2026-08-22 (a)](#amendment-2026-08-22-a-one-time-credential-provisioning-at-sync-target-setup-distinct-from-per-cycle-sync-bead-enhancedchannelmanager-wd20y)
  in its entirety, and with it **S10, S11, S12, S13** and invariants **INV-2, INV-3, INV-4, INV-8,
  INV-9**. It also **reverses S12(c)** ("scheduled or automatic re-push is FORBIDDEN"), which was
  ratified that morning and is now wrong.
- **Amends**: **S3** (never-sync set) and **S7** (security controls) in the ADR body.
- **Does not revert**: `enhancedchannelmanager-msqf7`. See
  [What msqf7 actually forbids](#what-msqf7-actually-forbids-and-it-is-not-this).
- **Companion**: `docs/security/threat_model_dbas_import.md` §11.5, re-rated in the same branch.

### The ruling

Verbatim, and it is the whole argument:

> *"In the end, this should be easy for the user, not complicated. You keep fighting me on this and
> I don't know why. I know the security risks. That's on the user to mitigate, not us. If a user is
> dumb enough to insecurely send credentials cleartext, that's on them. We should be sending
> credentials every time so that we don't need the user to deal with needing to re-type anything.
> Any update happens as soon as the next scheduled sync occurs."*

Two prior rulings the same day are superseded by it and are recorded so the sequence is legible
rather than looking like a single decision: (i) *setup-only provisioning, automatic at sync-target
setup* — rejected because a replica whose credential goes stale on rotation is not a standby;
(ii) *cascade on detected change, gated on a recorded fingerprint* — rejected as complexity that
buys the operator nothing they asked for. **The PO made this call with the security objection in
front of them and accepted the risk in their own terms.** That is what makes it a decision rather
than an oversight, and it is why this section records the residuals as **accepted** rather than
as **mitigated**.

### S3 as amended — provider credentials leave the never-sync set

| # | Area | Decision | Reversibility |
|---|---|---|---|
| **S3′** | Never-sync set | **Provider credentials are IN the per-cycle sync.** M3U account `username`/`password`, the credential inside a plain-M3U `server_url`, the credential inside an XMLTV EPG `url`, the credential in a stream URL's path segments, and the credential in a provider logo address all cross on every cycle, as part of the ordinary importer write. **The never-sync set is now exactly two things**: `users` (privilege escalation / operator lockout under continuous push — unchanged, code-enforced, D3) and **ECM's own secrets** — its settings secrets, alert-method secrets, and the credentials of its cloud-storage and sync targets. Those are not provider credentials and have no purpose on a replica. | Revert = restore `known_secrets` threading and `scrub_credential_urls=True` in `build_live_source_plan`. Credentials already sent to a replica are **not** retracted by reverting. |

The mechanism is one constant, `tasks.dbas_sync_engine.PROVIDER_CREDENTIAL_SECTIONS`, naming
`m3u_accounts` and `epg_sources`, plus the same file's decision to stop threading harvested secrets
into the channels and logos redaction. A gathered category does **not** inherit the exception; it
has to be added there deliberately.

### S7 / S11 as amended — TLS verification is recommended, not enforced

| # | Area | Decision | Reversibility |
|---|---|---|---|
| **S7′** | `insecure` and a credential on the destination | **The refusal is REMOVED.** A sync target may be both "TLS verification disabled" and carrying provider credentials on every cycle. ECM does not block it. What it does instead: a **warning on every credential-carrying cycle** (`insecure_transmission_warning`, in the run's notes and the log), an **audit row on every such cycle** recording `tls_verified=false` and how many records carried a credential, and a **badge on the target row** in the UI saying credentials cross in clear. **The risk is PO-accepted, explicitly**: the operator owns both instances and mitigates this themselves. | Revert = restore the service-layer predicate on both write orders. |

This voids **S11** and **INV-4** entirely, and it dissolves rather than fixes two open beads:

- **`enhancedchannelmanager-ngwxx`** (P1) — the observed-credential predicate false-positived on
  every XC target, permanently and unclearably, because a populated but credential-free XC
  `server_url` counted as an observed credential. The predicate existed only to feed this refusal.
  With the refusal gone the observation has no consumer and was deleted, along with the
  `destination_credential_observed_at` column. **This bead is closed by removal, not by repair** —
  the wrong predicate is not made right, it is made absent.
- **`enhancedchannelmanager-3dmgr`** — the same refusal widened to a hand-entered credential on the
  replica. Same disposition: the exposure it described is real and is now **accepted** rather than
  refused, and stated to the operator at the point of the choice.

### S12 as amended — rotation needs no mechanism

| # | Area | Decision |
|---|---|---|
| **S12′** | Rotation and staleness | **Rotation falls out of per-cycle transmission and needs no feature.** A provider password changed on A reaches B on the next scheduled cycle because B is given the current value every cycle. There is no re-provision action, no marker to clear, no version gate, no change detector and no staleness that can arise between cycles. **S12(c) is REVERSED**: scheduled automatic re-push is not merely permitted, it is the design. |

The staleness signal (`destination_account_looks_stale` / `provisioned_credentials_stale`) is
retained because a replica whose provider account is failing is still worth reporting, but its
remedy is no longer "re-run the provisioning action" — it is the ordinary one: fix it on A.

### S13 as amended — every cycle states what it carried

| # | Area | Decision |
|---|---|---|
| **S13′** | Audit | **Every terminal route of a sync run writes exactly one `sync_outbound` journal row**, and that row states the redaction mode (`topology_plus_provider_credentials`), whether TLS was verified, **how many provider records carried a credential**, and **which ones, by operator-facing label and FIELD NAME**. Never a value, never a fragment of one, never a masked tail. Under per-cycle transmission this row is the only record of how often a secret moved, so the routes that carry nothing — the freshness abort, the destination-unreadable abort — write it too, recording that they carried nothing. |

This is the surviving form of **`enhancedchannelmanager-gad2p`**. That bead named two failure routes
that wrote no audit row: a provisioning gate refusal returning 409, and a de-provision against an
unreachable destination returning a bare 500. **Neither route exists any more** — the gate and the
de-provision action are both deleted — so the bead is fixed by the invariant rather than by patching
its two reproductions. The invariant as restated: *no credential-carrying attempt, and no attempt
that could have carried one, terminates without an audit row.*

### The rebind gap is closed by the same change

**`enhancedchannelmanager-2jvvb` / `enhancedchannelmanager-5bib5`.** Under `msqf7`'s redaction the
replica's channels were bound to stream URLs whose credential segments were the sentinel; the
replica's own refresh then fetched the real streams as **orphans**, and only a *further* ECM cycle
rebound the channels onto them. A target with no armed schedule sat with a full stream set bound to
nothing after an action that reported success.

Carrying the stream URL whole removes both halves at the source: the channel is bound to a working
address the moment the cycle writes it, so there is nothing to rebind and no orphan window. The
acceptance invariant — *the replica serves the streams its source serves, with no further operator
action* — is met on the first cycle rather than the third.

### What `msqf7` actually forbids, and it is not this

`msqf7` was **not** a bug about transmitting credentials. It was a bug about **ECM telling the
operator credentials were stripped while transmitting them anyway**, implicitly, inside stream-URL
path segments that nothing inspected, on every scheduled cycle. The product's own words were false.

Sending them deliberately is permitted by this amendment. Sending them while the UI, the docs, the
audit row or a docstring claims otherwise is the defect `msqf7` exists to prevent recurring, and it
is a build gate on this amendment. Changed in the same branch as the behaviour:

- `docs/user_guide/backup-restore/cross-instance-sync.md` — the "never puts a provider credential on
  the wire" claim, the credential-re-entry recovery procedure (which no longer exists), the
  never-synced table, and the redacted-stream troubleshooting entry;
- the SyncTargets card's "Credentials are not synced" banner, the insecure-TLS copy and the
  target-row badge;
- the `sync_outbound` audit row's `redaction_mode`, which asserted `topology_only` **while `msqf7`
  was live** — the audit trail stating the exact thing that was false;
- `tasks.dbas_sync_engine`'s own module docstring.

Bead `enhancedchannelmanager-nc6t7` is closed by this work.

### Invariants, restated

| # | Invariant | Status | Enforcement |
|---|---|---|---|
| **INV-1′** | **A provider credential leaves A only inside the provider sections and the addresses of the entities they own** (`m3u_accounts`, `epg_sources`, stream URLs, provider logo URLs). ECM's own settings secrets, alert-method secrets and target credentials never leave A on any path. | Amended (was: no credential leaves A on any cycle) | The two-pass split in `_redact_sync_sections`, plus tests asserting an ECM settings secret and an alert-method secret are still sentinelled in a sync plan. |
| **INV-2** | *The provisioning writer is unreachable from a cycle.* | **VOID.** | The writer module and its import-reachability guard are **deleted**. The guard asserted the exact opposite of the ratified design; it was removed rather than weakened, because a guard kept alive against a design that no longer exists reads as a control and enforces nothing. |
| **INV-3** | *A persists no provider credential.* | **VOID as written.** | A now persists exactly one, deliberately: the Schedules Direct password, Fernet-encrypted on the sync-target row, because it cannot be harvested and the alternative is the operator typing it every time. No other provider credential is persisted on A for sync purposes. |
| **INV-4** | *A target is never both `insecure` and holding a credential on B.* | **VOID.** | Replaced by a warning and an audit row (S7′). |
| **INV-5′** | **Every terminal route of a cycle writes exactly one audit row stating what it carried.** | Amended | Journal assertions on the completed-apply, dry-run, freshness-abort and destination-unreadable routes. |
| **INV-6′** | **The credential-bearing field set is the redactor's own, inverted** — what crosses is precisely what `preserve_keys` names plus what `scrub_credential_urls=False` leaves alone, within `PROVIDER_CREDENTIAL_SECTIONS`. | Amended | Derived from `_redact_credentials_deep`'s own parameters rather than from a literal field list. |
| **INV-7′** | **After one successful apply, a replica serves the streams its source serves, with no further operator action** — or the run says plainly what is missing. | Retained, and now literally true at the cycle layer | Stream URLs cross whole, so `channels_with_no_playable_stream` is 0 on the first cycle for a credentialed provider. **Not live-verified** — see the residual below. |
| **INV-8** | *A standby whose provisioned credentials stopped working says so, without triggering a push.* | **VOID as written** (the "without triggering a push" half is meaningless now) | The staleness reporting is retained as information; the cycle re-sends the current credential regardless. |
| **INV-9** | *A de-provision that did not clear B does not clear the marker.* | **VOID.** | There is no de-provision and no marker. |

### Residuals, accepted rather than mitigated

Each of these was previously prevented by a control this amendment removes. They are listed as
**accepted** — the PO's ruling is the acceptance — so that a later reader does not mistake an
absent control for an oversight.

1. **The replica is a place the provider credential lives, permanently and by design.** Its
   database, backups, exports, logs, and its own stream rows all hold it. Nothing retracts it; only
   rotating the credential at the provider ends the exposure.
2. **A target with `insecure=true` transmits the credential in clear on every cycle.** Recurring,
   not one-shot. Warned and audited, not blocked. *"That's on the user to mitigate, not us."*
3. **The blast radius of a compromised replica now includes the provider subscription**, where
   before it included only the topology.
4. **There is no ECM-side record of which replicas hold a credential**, because the marker columns
   were dropped. The journal is the record: every cycle that carried one says so, with the target
   named.
5. **A logo address carrying a credential is now copied verbatim** rather than dropped. This trades
   a named logo miss for the replica loading the artwork — and for the address carrying the
   credential, which under this ruling it already does everywhere else.
