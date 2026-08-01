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
  as real Dispatcharr links). Only the logos importer records misses. The
  channels importer drops `logo_id` from create payloads and has no
  logo-attach path. Both fields are additive optional: no `CONTRACT_VERSION`
  bump.

### Tri-state outcome: never "success" on mixed state

`RestoreOutcome` has exactly three realized states:

| Value | Meaning |
|---|---|
| `success` | every selected entity created/updated/skipped cleanly; nothing failed; no compensation needed |
| `partial_failed_rolled_back` | at least one failure; compensating rollback ran and removed **every** created entity (404-on-delete counts as removed); instance back to pre-restore state |
| `failed_rollback_incomplete` | a failure occurred **and** the rollback could not fully undo it (a non-404 delete error); instance indeterminate; ledger residue surfaced for manual cleanup |

The contract: the UX labels the two non-success states "restore failed, state
rolled back" / "rollback incomplete", **never** "success". This is the whole
point of the tri-state: mixed state must never read as success.

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
