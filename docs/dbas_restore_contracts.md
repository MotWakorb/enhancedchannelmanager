# DBAS Phase-2 Restore Contracts

Status: **Accepted** (contract definition, no behavior yet)
Bead: `enhancedchannelmanager-kxuj2`
Implements: shared contracts for ADR-012 (DBAS absorption), Phase 2 restore
Module: `backend/dbas/restore_contracts.py`

## Why this document exists

Three data shapes are referenced across every Phase-2 restore bead but, before
this work, were defined nowhere. If each importer invented its own, they would
diverge. The single restore UX component (bead `…-0i2vt.20`) could not then
render dry-run, apply, and summary from one type. This grooming finding
(code-reviewer / PM / DBA) pins the contracts **before** the importers start so
the importer beads build against a fixed surface.

This is a contract definition. There is no behavior to test yet; the importer
beads that consume these contracts carry the functional tests. The module is
validated by `python -m py_compile` only.

## Entity taxonomy

`EntityType` enumerates the FK-bearing, ID-remappable entity types. The values
are canonical keys used as dict keys in the remap table and as discriminators in
the ledger, so they are part of the on-disk format. Keep them stable.

| `EntityType` | Producer bead | Role |
|---|---|---|
| `channel_group` | `…-0i2vt.12` | remap producer |
| `channel_profile` | `…-0i2vt.12` | remap producer |
| `stream_profile` | `…-0i2vt.12` | remap producer |
| `channel` | `…-4vouz` | remap consumer |
| `user_agent` | `…-0i2vt.13` | n/a |
| `dvr_rule` | `…-0i2vt.13` | n/a |
| `user` | `…-l1p4p` | crown-jewel, opt-in |

`plugins` is intentionally **absent**: removed from v0.18.0 per ADR-012 D10
(RCE risk). An importer that encounters a plugin in the archive reports it
skipped with `SkipReason.UNSUPPORTED_IN_THIS_VERSION`.

## Contract 1: Restore response schema (`RestoreReport`)

One schema for three surfaces:

| Surface | Bead | Reads |
|---|---|---|
| Dry-run engine | `…-0i2vt.16` | produces a `RestoreReport` with `is_dry_run=True` |
| Apply + rollback | `…-0i2vt.18` | produces a `RestoreReport` with `is_dry_run=False` |
| Restore-complete UX | `…-0i2vt.20` | renders both |

Key fields:

- `is_dry_run`: distinguishes the counts-only plan from a realized result. One
  UI component branches on this.
- `categories: list[EntityCategoryReport]`: per-entity-category counts.
  - Apply populates `created` / `updated` / `skipped` / `failed`.
  - Dry-run populates `would_create` / `would_update` / `would_skip` (counts-only
    per ADR-012 D7; full diff tree deferred to v0.19.x).
  - `skip_details` / `failure_details` carry the **reasons**. They are the
    source of truth; the integer counts are conveniences derived from them.
    Importers keep them consistent (`len(skip_details) == skipped` on apply).
- `outcome: RestoreOutcome | None`: the **tri-state** result. `None` on a
  dry-run (a plan has no realized outcome).
- `logo_misses: int`: aggregate count of unresolved logo references. The logo
  beads `.15` / `.19` consume this (the D9 red banner).
- `logo_miss_details: list[LogoMissDetail]`: the per-logo drill-down behind
  the aggregate (bead `…-qhui4`): each row carries the missed logo's
  `source_export_id` + operator-facing `label`, plus (bead `…-cm9bi`) its
  AFFECTED CHANNELS as `channels: list[LogoMissChannel]` (`channel_id` +
  `name`). One miss stays one detail row. `len(logo_miss_details)` tracks
  `logo_misses`, which counts logos, not channels; a logo shared by several
  channels lists them all in `channels`. `channel_id` is the **destination**
  Dispatcharr channel id, resolved through the `EntityType.CHANNEL` remap
  namespace (the channels importer runs before the logos importer); it is
  `None` when unknown: the channel's create failed/was skipped, or the run is
  a dry-run (whose CHANNEL remap holds provisional ids that must never render
  as real Dispatcharr links). Both fields are additive optional: no
  `CONTRACT_VERSION` bump.

  **Two producers feed `logo_misses`**, and `RestoreReport.record_logo_miss` is
  the one place both the aggregate and the drill-down are written, so
  `len(logo_miss_details) == logo_misses` holds by construction:

  1. `dbas.importers.logos` — an archived logo the destination did not already
     have (the D9 red-banner signal).
  2. `dbas.channel_reattach.reattach_channel_logos` — an archived logo
     REFERENCE that could not be put back onto the channels that had it. The
     channels importer still drops `logo_id` from the create payload (it is a
     SOURCE id and would dangle); this pass re-derives it through the `LOGO`
     remap afterwards. Added by bead `…-dfkbn` after a round-trip drill measured
     `logo_misses: 0` while 12 channels lost a logo they had.

  **Both producers fire on the same failure**, and that is one lost logo, not
  two (bead `…-k2r7m`): an upload the destination rejected leaves no `LOGO`
  remap entry, so the reattach pass that runs next cannot resolve the reference
  either. `record_logo_miss` therefore MERGES a second report of an archived
  logo it already holds, keyed on `source_export_id`:

  - the `channels` lists are **unioned** — the importer sees every channel that
    referenced the logo, the reattach pass sees the ones whose PATCH it could
    not perform, and dropping either slice under-names the damage;
  - the operator-facing `label` **wins over a synthesized one**. The reattach
    pass holds only the archive id, so it labels its rows `logo #13 (archived)`
    and passes `label_is_synthetic=True`; a producer that knows the archived
    NAME does not, and its label survives regardless of which ran first.

  A miss recorded with `source_export_id: None` carries no identity to merge on
  and always gets its own row — under-counting a real loss is the failure this
  surface exists to prevent, so ambiguity resolves toward reporting. Drill run
  `2026-08-06-run9` measured the pre-merge behaviour: one logo failed and the
  report read `failed 1` beside `2 logo(s) could not be reinstated`.

### Post-restore action items (beads `…-6pilh` / `…-2o0cz` / `…-dfkbn`)

Each is an **aggregate count + a named drill-down**, written through one
recorder so the two cannot drift. None of them is a failure — the entity was
created and `outcome` is unaffected — but every one is state the operator had
before the backup and does not have after the restore. They exist because a
round-trip drill produced `Restore success: created 32, failed 0` for an
instance where not one channel could play, every logo and EPG link was gone, a
channel profile had silently widened from 9 members to 12, and two non-default
ECM settings had reverted. All are additive optional: no `CONTRACT_VERSION` bump.

| Aggregate | Drill-down | Recorder | Means |
|---|---|---|---|
| `credentials_needing_reentry` | `credential_reentry_details` | `record_credential_reentry` | the entity is on the destination and authenticates nowhere, because the artifact carried only the redaction sentinel for the named FIELDS and the destination still has no usable value at them. Asked of the DESTINATION ROW on every cycle, not only where the entity was created (bead `…-ukjx5`) — an account that already exists is not the same fact as one whose password has been re-entered, and recording only on the create path made a scheduled sync say it once and then go silent forever. Paths inside the destination's own cached copy of the provider's reply (`…custom_properties.user_info.*`) are NOT recorded (bead `…-posm1`): there is no field to re-enter them into and the destination rewrites the blob itself on its next successful refresh, so counting them left the line reading "1 account(s) need credentials re-entered" after the operator had already re-entered them. The importer still LOGS every path it stripped — that is a developer surface. **Not** a delivery shortfall: the redaction is deliberate (bead `…-msqf7`), and its consequence is already counted by `stream_urls_redacted` / `channels_with_no_playable_stream` |
| `channels_needing_stream_reattach` | `stream_reattach_details` | `record_stream_reattach_needed` | still holding at least one URL-less placeholder SLOT after the post-refresh rebind. Most of these channels still play — the `…-ixdaw` fix deliberately leaves one contested slot on its placeholder |
| `channels_with_no_playable_stream` | `stream_reattach_details` (rows with `has_playable_stream: false`) | `record_stream_reattach_needed` | the SUBSET above left with NO stream that can serve — **the channel cannot play**. "Can serve" is `credential_sentinel.url_can_serve`, NOT truthiness on `url`: a stream whose address carries the redaction sentinel is a non-empty string that fetches HTTP 404, and reading it as playable reported a replica whose channels were 90% dead as entirely healthy (bead `…-1td94`). Non-zero on an apply forces `outcome: completed_with_failures` (bead `…-daziw`) |
| `epg_links_unrestored` | `epg_link_miss_details` | `record_epg_link_unrestored` | no destination EPG row carried the channel's archived `tvg_id`. Computed only over ARCHIVE channels that carry an `epg_data_id`, so a channel the source never linked cannot contribute (bead `…-15g1j`) — which is what makes it safe to key an outcome on. Non-zero on an apply forces `outcome: completed_with_failures` (bead `…-posm1`) |
| `profile_membership_drift` | `profile_membership_drift_details` | `record_profile_membership_drift` | channels whose membership on the destination DIFFERED from the archived selection and were flipped back (Dispatcharr enables every new channel in every profile). The pass still asserts EVERY membership every cycle — that is the fail-closed write and it is unchanged — but it counts only what had actually drifted (bead `…-ukjx5`). It used to count every flip it asserted, so a converged replica reported the same non-zero number on every scheduled cycle, forever |
| `stream_urls_redacted` | `stream_url_redaction_details` | `record_redacted_stream_urls` | streams **the destination is currently holding** whose URL had the provider credentials cut out of it (bead `…-msqf7`). An Xtream Codes stream address IS the credential (`/live/<user>/<pass>/<id>.ts`), so the credential segments become the sentinel and the rest of the address crosses — the stream lands naming where it pointed and cannot play until the destination has its own provider account. Every channel left on one of these is ALSO counted in `channels_with_no_playable_stream`, so the run is downgraded rather than reporting success (bead `…-1td94`). Taken from ONE reading of the destination's stream rows by the post-refresh rebind pass, so the recorder REPLACES the population rather than appending to it; it was recorded at create time until bead `…-ukjx5`, which made cycle two report `0` over a destination whose streams were all still redacted |

`streams_rebound` is the informational counterpart to the first: how many
placeholder bindings the rebind pass DID resolve onto a real provider stream.

`profile_membership_drift` counts CHANNELS flipped, not profiles — the
operator-meaningful unit ("3 channels a profile was built to exclude were
exposed") — so unlike the others it does **not** track the length of its detail
list. An already-correct profile records nothing.

### The delivery-shortfall set — what forbids `success`

`RestoreReport.DELIVERY_SHORTFALL_FIELDS` is the single declaration of which
aggregates mean **the source had this and the replica does not**. It is read by
exactly two consumers, which is why it is declared once rather than repeated:
`restore_orchestrator.compute_outcome` decides the OUTCOME from it, and
`tasks.dbas_restore._credential_reentry_suffix` renders each member as a clause
in the one-line summary a scheduled run produces.

The invariant, of which the members are examples rather than the specification:

> A run never presents as an unqualified `success` when the replica it produced
> is missing something the source had and the run was asked to carry.

| Member | Why it is in the set |
|---|---|
| `channels_with_no_playable_stream` | the channel cannot play (bead `…-daziw`) |
| `stream_urls_redacted` | the replica holds an address ECM cut the credentials out of (bead `…-msqf7`) |
| `epg_links_unrestored` | a channel the source gave a guide link arrived without one (bead `…-v7d37`) |
| `logo_misses` | a logo the operator HAD on the source and does not have after the run (bead `…-dfkbn`) |
| `entities_blocked_by_dependency` | an archived entity the run was asked to deliver was never created, because something it references is not on the destination (bead `…-4mkoe`). Only the GENUINE half: a dependency the operator DESELECTED is recorded `SkipReason.DEPENDENCY_DESELECTED` and never counted, because that absence is what the operator asked for |

Everything else on the report is deliberately **out**, and each exclusion has a
reason worth keeping:

- `channels_needing_stream_reattach` — a channel that kept its real streams and
  merely holds one leftover placeholder **plays**. The `…-ixdaw` fix produces
  exactly that on healthy channels; downgrading on it would false-fail an
  instance where everything works (bead `…-daziw`).
- `credentials_needing_reentry` — the redaction is deliberate (bead `…-msqf7`),
  so the run was asked **not** to carry it. Its consequence — a replica whose
  streams have no usable address — is already in the set twice over, so the
  OUTCOME keys on what the replica is missing and the SUMMARY keys on what the
  operator must do.
- `channel_group_drift` under the default preserve mode, and both
  `ReattachPopulation.preserved_channels` — the operator asked the run to leave
  the destination's own choices alone.
- `profile_membership_drift` and `streams_rebound` — work the run **performed**.
  A membership that had drifted and was corrected leaves the replica matching,
  which is the opposite of a shortfall.
- A **faithful absence** is never a member (bead `…-15g1j`). Implementing the
  literal "anything absent" reading turned all ten keystone round-trip scenarios
  red for replications that had lost nothing, so every member above is a counter
  whose PRODUCER already restricts it to things the source actually had.

**Key on the outcome, never on which member fired.** Bead `…-cwmid` had to undo
a narrower keying after drill run 2026-08-06-run9 measured the severity ordering
inverted — 12-of-12 channels unplayable alerting `warning` while one cosmetic
logo failure alerted `error` / "Task Failed". Every member resolves to the same
`completed_with_failures`, which `RestoreOutcome.is_degraded_not_failed` maps to
`warning` with a per-task `alert_on_warning` opt-out. Adding a member therefore
cannot reorder severities, because no member is ever consulted for one.

A **dry run** never downgrades: a preview that predicts a shortfall predicted it,
and nothing was applied to be missing.

#### The rebind is no longer restore-only (bead `…-2o0cz` residual)

The rebind pass has **two** entry points. The contract above describes the
archive-driven one, which runs as a restore-completion step and is the only one
that writes the report.

On a STANDARD (redacted) artifact that pass has nothing to resolve: the restored
M3U account carries no credential when the deferred refresh fires, so no real
stream exists yet. Drill runs 4, 5 and 7 all measured the same consequence — the
operator re-entered the credential, refreshed, watched 96 real streams appear
BESIDE the placeholders, and not one channel was rebound. The only recovery was
re-running the whole restore.

`dbas.placeholder_rebind.rebind_placeholders_after_refresh` closes that. It
re-runs the same matcher against whatever the refresh materialized, using the
placeholder's OWN name as the match key (`custom_stream_fallback` copies the
archived stream's `name` verbatim), so it needs no archive, ledger or id remap.

| | archive-driven | refresh-driven |
|---|---|---|
| Runs | restore completion, clean non-dry-run apply | M3U refresh completion |
| Triggered by | `restore_orchestrator.run_restore` | `POST /api/m3u/refresh/{account_id}` completing (UI button, MCP `refresh_m3u`); the scheduled `m3u_refresh` task, once per run |
| NOT triggered by | — | `POST /api/m3u/refresh` (refresh-all / MCP `refresh_all_m3u`) — it returns before the refresh completes and exposes no completion signal; direct `DispatcharrClient.refresh_all_m3u_accounts` callers; a refresh performed in Dispatcharr's own UI. Those heal on the next scheduled `m3u_refresh` run |
| Match key | the ARCHIVE record, via the STREAM remap | the PLACEHOLDER's own record |
| Scope | streams this run's `RollbackLedger` owns | any URL-less stream on the synthetic `ECM Custom Streams (DBAS restore)` account. An operator's own URL-less stream on any OTHER account is never touched |
| Writes the report | yes | **no** — there is no `RestoreReport` at that point. The counters above stay apply-only and never move outside a restore |

Both share one per-channel implementation, so archived slot ORDER, the `…-ixdaw`
de-dup backstop and the all-or-nothing PATCH failure handling are identical on
both paths. They are serialized by one module-level lock: the archive-driven
pass waits (it is authoritative), the refresh-driven pass stands down when a
rebind is already in flight, so neither can double-run. A restore never reaches
either hook — its deferred phase calls `DispatcharrClient.refresh_m3u_account`
directly rather than ECM's own route or task.

The refresh-driven pass is a silent no-op on any instance with no synthetic
custom-stream account (one `get_m3u_accounts` call), and on any account with no
URL-less stream under it (one extra account-scoped page). M3U refresh is a hot
scheduled path and the pass must not cost it anything in the common case.

#### What a DRY RUN reports for each (bead `…-dgnms`)

A preview must never report a confident `0` for a condition the apply will
report as N. Drill run 4 previewed and applied the same artifact against a fresh
target back to back and measured exactly that, four times over. The counters
above split into two groups, and they answer differently:

| Counter | On a dry run |
|---|---|
| `credentials_needing_reentry` | predicted. It is a fact about the artifact AND about the destination row, and both are readable without writing anything |
| `epg_links_unrestored` | never recorded — a preview does not claim a loss |
| `entities_blocked_by_dependency` | **predicted.** The FK resolution a preview performs is the same one the apply performs (the anti-drift provisional remap exists so a preview and an apply reach the same verdict), so the preview's `would_skip` rows are the apply's skips. `compute_outcome` still refuses to downgrade a preview |
| `profile_membership_drift` | **predicted.** The drift set is the restored channels whose membership on the destination differs from the archived selection, computed from the archive, the remap the same run already populated, and one read-only look at the destination's current profile memberships. Both branches run the identical expression — a channel counts as currently enabled when the destination's profile enables it, when this run created it (Dispatcharr's enable-everything create default), or when the profile is not on the destination yet — so preview N == apply N (bead `…-ukjx5`) |
| `channels_needing_stream_reattach` | **`null` — not predicted** |
| `channels_with_no_playable_stream` | **`null` — not predicted** |
| `stream_urls_redacted` | **`null` — not predicted** (bead `…-ukjx5`). It now counts what the DESTINATION holds, and the pass that reads the destination's streams is the same post-refresh rebind that cannot run before the apply. A preview `0` would be a claim about a destination nothing looked at |
| `streams_rebound` | `0`, and that is literally true: a preview rebinds nothing |

The three `null`s are the honest answer, not a gap. The pass that writes them
re-runs the stream matcher against the provider streams the **deferred M3U
refresh** materializes, and a dry run performs no refresh — the number is not
knowable before the apply. `null` is additive-compatible: the fields were
already optional, every backend consumer coerces with `or 0`, and a client that
renders them must show "not predicted" rather than `0`.

A PREDICTED counter must also be worded as a prediction (bead `…-juu3c`). The
one-line summary `DbasRestoreTask` puts on the task-history row and the MCP
result renders each action item in the **future tense** on a dry run — "N
profile membership(s) **would be** corrected", not "corrected". The counts,
clauses and clause order are identical to the apply's; only the verb moves.
`credentials_needing_reentry` is exempt: "N account(s) need credentials
re-entered" is already true of a preview and of an apply.

The population SPLITS (`epg_link_reattach` / `logo_reattach`) are predicted in
both modes and must agree with the apply. `logo_reattach` counts both the logos
that MATCH on the destination and the ones this run has decided to CREATE; the
second half is what a fresh target consists of entirely.

### Outcome: never "success" on mixed state

`RestoreOutcome` has exactly four realized states:

| Value | Meaning |
|---|---|
| `success` | every selected entity created/updated/skipped cleanly; nothing failed; no compensation needed |
| `completed_with_failures` | the restore ran to completion and **nothing was rolled back**, but the result is not clean. Two independent triggers: at least one entity in a non-fatal category failed (the failed rows are counted in their category), **or** an apply produced a replica **missing something the source had** — any member of `RestoreReport.DELIVERY_SHORTFALL_FIELDS` above zero (beads `…-daziw`, `…-posm1`). Either way the applied state is real and kept |
| `partial_failed_rolled_back` | at least one failure in a fatal category; compensating rollback ran and removed **every** created entity (404-on-delete counts as removed); instance back to pre-restore state |
| `failed_rollback_incomplete` | the **indeterminate** state. Two independent triggers: a fatal failure occurred **and** the rollback could not fully undo it (a non-404 delete error, ledger residue surfaced for manual cleanup), **or** the run could not read the destination it describes (`destination_unreadable` is set — bead `…-bj442`) and therefore knows neither what that destination carries nor what it applied. The second trigger **dominates** every other reading of the counts, because a run whose destination reads failed gets its counts from the importers' `existing = []` fallback and they describe the SOURCE. It is a **sibling** of the delivery-shortfall set, never a member: nothing was lost, the cycle never read what it describes, and bead `…-jqfxm` treats it as an error rather than a degraded warning |

The contract: the UX labels the three non-success states explicitly — "some
items could not be restored" / "restore failed, state rolled back" / "rollback
incomplete" — and **never** "success". Mixed state must never read as success.

**Non-fatal categories.** `dbas.restore_orchestrator.NON_FATAL_FAILURE_CATEGORIES`
is the single source of truth, and its sole member is `user`
(`dispatcharr_users`). Nothing in a restore holds an FK into users, so a user row
upstream refuses degrades nothing downstream — whereas aborting on it costs the
operator every other category *plus* an ECM settings mutation the rollback cannot
compensate (bead `enhancedchannelmanager-y65si`). Every other category is
load-bearing and still aborts the whole restore on its first failure. An importer
that *raises* is fatal regardless of category.

`SkipReason` vs `FailureReason`: a **skip** is an intentional no-op that leaves
state consistent; a **failure** is an apply attempt that errored and may have
left partial state. Failures are what can drive a rollback.

## Contract 2: ID-remap table (`IdRemapTable`)

A Dispatcharr export records each entity's id as it was on the **source**
instance. On restore those ids are meaningless. The destination assigns its
own. FK references in the archive point at source ids and must be rewritten to
destination ids before being sent upstream.

| Aspect | Contract |
|---|---|
| Structure | `dict[EntityType, dict[source_export_id, destination_id]]` |
| Written by | groups/profiles importer (`…-0i2vt.12`) via `add(type, src, dest)` after each successful create |
| Read by | channels importer (`…-4vouz`) and settings/users importers (`…-0i2vt.13` / `…-l1p4p`) via `resolve(type, src)` before sending FKs upstream |
| Lifetime | one restore run, threaded through importers in dependency order (groups/profiles BEFORE channels). **Not durable**: a crashed restore is rolled back and restarted, which rebuilds the remap from zero |
| Unresolved | `resolve(...) -> None` means the FK cannot be rewritten → report `FailureReason.DEPENDENCY_UNRESOLVED` (or `SkipReason.DEPENDENCY_UNRESOLVED` in dry-run), never send a dangling source id upstream |

**Critical constraint:** importers MUST NOT reuse `backup.py`'s
delete-all-then-recreate strategy. That destroys the very relationships this
table preserves. It would invalidate every destination id mid-run. Restore is
additive/idempotent per entity, never wholesale wipe-and-replace.

## Contract 3: Rollback ledger (`RollbackLedger`)

Dispatcharr has no database transactions (ADR-012; bead `…-0i2vt.18`).
Best-effort consistency is a compensating-delete rollback. An in-memory list of
"what I created so far" dies with the process on a mid-restore ECM crash,
orphaning every created entity with no record to undo them. The ledger is
therefore **durable**.

### Durability / persistence contract

- Stored as an atomic JSON file under `CONFIG_DIR`: the same durable, mounted
  volume as `/config/journal.db` and `settings.json`
  (`backend/config.py:CONFIG_DIR`). Suggested path:
  `/config/dbas/restore_ledger_<restore_id>.json`.
- A destination id is only known **after** the upstream create returns, so the
  cadence is: issue create → on return, append the `LedgerEntry` (with its now-
  known `destination_id`) and flush to disk → only then issue the next create.
  Worst-case crash window is a single entity (the in-flight create whose response
  never landed and so was never ledgered), recoverable by the pre-flight orphan
  check.
- Writes are atomic (temp file + `os.replace`) so a crash never leaves a
  half-written ledger.
- An intent-log that records a create *before* it is issued (shrinking the crash
  window to zero) is deferred: not needed for v0.18.0.

The contract intentionally leaves the *act* of persisting to the importer/
rollback layer (`record_created()` only mutates the in-memory model) so the
durable-write strategy lives in exactly one place rather than being
re-implemented per importer.

### Compensation contract

- **Order:** descending `LedgerEntry.sequence` (reverse creation = reverse
  dependency order, since importers run parents-before-children). Never delete a
  parent while a child still references it. `compensation_order()` returns
  uncompensated entries newest-first.
- **Idempotent:** a compensating DELETE that returns **404 is success** (the
  entity is already gone, desired end state). Only a non-404 upstream error is
  a failed compensation. A resumed rollback skips entries already marked
  `compensated`.
- **Outcome mapping into Contract 1:**
  - no rollback was triggered because every failure was in a non-fatal category
    → `completed_with_failures` (the ledger stays as-is; nothing is compensated)
  - every entry compensated (deleted or 404) → `partial_failed_rolled_back`
  - any entry's DELETE failed with a non-404 error → `failed_rollback_incomplete`;
    residual uncompensated entries stay in the ledger and are surfaced in the
    `RestoreReport` for manual cleanup.
- On a clean, fully-successful restore the ledger is deleted (no compensation
  needed). On any rollback it is retained until every entry is compensated.

## Versioning

`CONTRACT_VERSION` (currently `1`) stamps both the wire `RestoreReport` and the
on-disk ledger. Bump it when a field's **meaning** changes (not for additive
optional fields). The ledger reader (`…-0i2vt.18`) refuses to compensate a
ledger whose `contract_version` it does not understand rather than guess at a
stale shape.

## Downstream consumption summary

| Contract | Produced by | Consumed by |
|---|---|---|
| `RestoreReport` | dry-run `…-0i2vt.16`, apply/rollback `…-0i2vt.18` | UX `…-0i2vt.20`; `logo_misses` by `.15` / `.19` |
| `IdRemapTable` | groups/profiles `…-0i2vt.12` | channels `…-4vouz`, settings/users `…-0i2vt.13` / `…-l1p4p` |
| `RollbackLedger` | every importer (records creates) | rollback `…-0i2vt.18` |

## Deferred / out of scope for this bead

- Functional tests: owned by the importer beads that exercise these contracts.
- The durable-write *implementation* (atomic file write, `os.replace`, orphan
  pre-flight): owned by `…-0i2vt.18`; this module defines the in-memory shape
  and the contract it must satisfy.
- Full entity-level diff tree for dry-run: deferred to v0.19.x per ADR-012 D7.
- Plugins restore: removed from v0.18.0 per ADR-012 D10.
