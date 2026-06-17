# ADR-011: Decouple M3U Refresh from Auto-Creation (Event-Driven)

- **Status**: Accepted
- **Date**: 2026-06-16 (PO decisions locked) / 2026-06-16 (ADR written + accepted)
- **Author**: IT Architect persona, on behalf of the PO, encoding the PO-locked
  decisions of 2026-06-16. This ADR **documents and makes implementable**
  three PO-locked decisions; it is not the place to relitigate them.
- **Bead**: `enhancedchannelmanager-ka7j9` — Phase 2: decouple m3u_refresh → auto-creation (event-driven)
- **Related**:
  - `enhancedchannelmanager-exo4j` — **Phase 1 guard** (merged, PR #505): the
    run-on-refresh circuit breaker + crash-sentinel this ADR builds on. The
    breaker, the `ECM_DISABLE_RUN_ON_REFRESH` break-glass, the crash-sentinel
    (`_abandon_orphaned_auto_creation_executions`), and the reset endpoints are
    all **preserved unchanged** — see §D5.
  - `enhancedchannelmanager-sjdsq` — bounded execution log (the OOM memory fix).
  - `enhancedchannelmanager-h2xnl` — per-run created-channel cap (preserved in
    the collapsed path — see §D4).
  - GH #473 — the OOM crash-loop outage whose root coupling this ADR removes.
  - `docs/architecture.md` — system overview; the auto-creation trigger model
    section is updated on acceptance.

## Context

Before this ADR, scheduled M3U refresh **hard-chained** auto-creation as a
side-effect: `tasks/m3u_refresh.py::M3URefreshTask.execute()` called
`run_auto_creation_after_refresh(...)` at the end of a successful run, and the
manual single-account refresh poll (`routers/m3u.py::_poll_m3u_refresh_completion`)
did the same per account. Auto-creation had no entry point of its own on the
unattended path — it only ran *because* a refresh ran.

This coupling is the structural cause of the GH #473 crash loop: an
OOM-killed auto-creation run left its `AutoCreationExecution` row stuck
`status="running"`, and the next scheduled refresh unconditionally re-fired
auto-creation → re-OOM, on every restart. Phase 1 (`exo4j`, PR #505) added a
**circuit breaker** to stop the re-fire, but left the coupling itself in place:
auto-creation was still a side-effect of refresh, with no back-pressure and no
independent lifecycle. Two further problems followed from the coupling:

- A single failed M3U account in a batch could short-circuit the
  success-return path and **suppress auto-creation for the whole batch**.
- There were **two** auto-creation entry points with divergent notification and
  cap handling (`AutoCreationTask.execute()` for the "scheduled" path, and the
  standalone `run_auto_creation_after_refresh` for the refresh side-effect).

### Why this is an architecture decision (warrants an ADR)

It changes the **trigger model** of a core pipeline (from synchronous
side-effect to self-scheduled, watermark-driven), introduces a new persisted
**coordination signal** (the refresh watermark), changes the **failure
isolation** boundary between two subsystems, and collapses two entry points
into one — each a contract other code and operators depend on.

## Decision

Adopt **Option B — a self-scheduled AutoCreationTask + a refresh watermark.**
M3U refresh publishes a "refresh completed" signal (a timestamp watermark in
settings); the interval-scheduled `AutoCreationTask` reads that signal fresh on
each tick and **decides for itself** whether to run. The hard chain is removed
and the duplicate path is collapsed (Strangler-Fig end state).

The three PO-locked decisions (2026-06-16) map to the design sections below:

| PO decision (2026-06-16) | Section |
|---|---|
| Q1 — the refresh watermark advances on EVERY successful refresh (preserve today's "runs after every refresh" behavior; NOT change-gated) | §D1 |
| Q2 — the post-refresh run executes only `enabled AND run_on_refresh=True` rules (today's semantics) | §D3 |
| Q3 — ~60s scheduler-tick trigger latency is accepted | §D2 |

### §D1 — Refresh watermark (Q1)

Two persisted timestamps are added to `DispatcharrSettings` (settings.json — no
DB migration; the same `get_settings`/`save_settings` mechanism the exo4j
breaker flag uses):

- `last_m3u_refresh_completed_at` — advanced to `datetime.utcnow().isoformat()`
  on **every successful** M3U refresh (Q1: not change-gated), by both the
  scheduled `M3URefreshTask` and the manual single-account refresh poll. A
  partial-success batch (`success_count > 0` with some failures) still advances
  it, so a single failed account no longer suppresses auto-creation for the
  batch.
- `last_auto_creation_consumed_refresh_at` — advanced by `AutoCreationTask` to
  the refresh value it consumed, when (and only when) it auto-fires.

Both are ISO-8601 UTC strings; empty string == "never" (sorts before any real
timestamp). We deliberately do **not** reuse `M3USnapshot.snapshot_time` — it is
written only on *detected changes*, so it is unsuitable as a "a refresh
happened" marker.

### §D2 — Self-scheduled AutoCreationTask (Q3)

`AutoCreationTask`'s default schedule flips from `MANUAL` to `INTERVAL` at the
engine's check cadence (`DEFAULT_CHECK_INTERVAL == 60s`). The task therefore
ticks roughly every 60s; the engine's existing "already running"
guard (`TaskEngine._active_tasks` + `_lock`) prevents a double-fire across
overlapping ticks. A ~60s trigger latency after a refresh is accepted (Q3).

Existing installs carry a persisted `scheduled_tasks` row at
`schedule_type='manual'`; a one-time, idempotent, WHERE-gated startup migration
(`database._migrate_auto_creation_task_manual_to_interval`, modeled on the
bd-ifmr5 cleanup migration) flips it to `interval` / `interval_seconds=60` and
NULLs `next_run_at` so the registry recomputes it. (Not an Alembic migration —
the bd-5w6jz smart-bootstrap fast-path would silently skip a data-only Alembic
migration on existing installs.)

### §D3 — The AUTO-FIRE GUARD (Q2)

At the top of `AutoCreationTask.execute()`, the post-refresh pipeline runs only
when **all** of these hold (settings read **fresh** every tick):

1. **(a) enabled** — the task is enabled.
2. **(b) not suppressed** — `_run_on_refresh_suppressed()` is False (the exo4j
   breaker is clear AND `ECM_DISABLE_RUN_ON_REFRESH` is unset).
3. **(c) work exists** — at least one `enabled AND run_on_refresh=True` rule
   exists; only that rule set is ever run on this path (Q2).
4. **(d) a new refresh** — `last_m3u_refresh_completed_at > last_auto_creation_consumed_refresh_at`.

When all hold, the task **consumes the watermark first** (advances
`last_auto_creation_consumed_refresh_at` to the refresh value and persists it)
**before** running the pipeline, so a crash mid-run or an overlapping tick
cannot re-fire against the same refresh — the watermark is the back-pressure
that the old hard chain lacked.

### §D4 — One entry point, one cap, one notification style

The standalone `run_auto_creation_after_refresh` is removed; its notification
(start / completion / "no changes"), its `bd-h2xnl` capped-run warning, and its
breaker suppression notification + journal entry are **migrated into**
`AutoCreationTask`. There is now exactly one auto-creation auto-fire path, with
one breaker gate, one created-channel-cap path, and one notification style. The
pipeline `triggered_by` stays `"m3u_refresh"` so the dedup hook (ADR-008 §D) and
journal semantics are unchanged.

### §D5 — Preserved exo4j contract (unchanged)

- The crash-sentinel `_abandon_orphaned_auto_creation_executions` (task_engine)
  is unchanged and still runs before the scheduler arms.
- The persisted breaker flag `auto_creation_run_on_refresh_disabled`, the reset
  endpoints (`GET/POST /api/auto-creation/(reset-)circuit-breaker`), and the
  `ECM_DISABLE_RUN_ON_REFRESH` break-glass are unchanged.
- Settings are read fresh at decision time (never cached) — the breaker
  scenario is a restart.
- **Manual "Run Now"** (`POST /api/auto-creation/run` and
  `/rules/{id}/run`) goes straight to `engine.run_pipeline` and is **never
  gated** by the breaker or the watermark — unchanged.

## Alternatives Considered

- **Option A — keep the hard chain, add back-pressure inside it.** Rejected:
  leaves auto-creation without an independent lifecycle and keeps the two
  divergent entry points; the failure-isolation problem (one failed account
  suppressing the batch) persists.
- **Event bus / pub-sub.** Rejected as over-engineered for a single-process
  app: a settings watermark + the existing 60s scheduler tick achieves the
  decoupling with no new infrastructure and accepted latency (Q3).
- **Reuse `M3USnapshot.snapshot_time` as the signal.** Rejected: it is written
  only on detected changes, so it cannot represent "a refresh ran" (Q1 requires
  firing after every successful refresh).

## Consequences

- **Positive**: auto-creation has its own lifecycle (own task-history row, own
  notification); a single failed M3U account no longer suppresses the batch; one
  entry point / one breaker gate / one cap path; the GH #473 coupling is gone at
  the structural level (the breaker is now defense-in-depth, not the only stop).
- **Negative / accepted**: up to ~60s latency between a refresh completing and
  auto-creation firing (Q3); auto-creation now appears as its own task run, not
  a refresh sub-step (a behavior change operators will see in task history and
  notifications — documented in the CHANGELOG).
- **Risk — lost watermark on shutdown**: the watermark is a best-effort settings
  write; if it fails, the worst case is one missed/extra auto-fire, never a
  crash loop (consume-before-run bounds re-fire to once per refresh).
