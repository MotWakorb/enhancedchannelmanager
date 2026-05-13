# ADR-007: Retention & Rollup Policy for `session_telemetry`

- **Status**: Proposed
- **Date**: 2026-05-12 (proposed)
- **Author**: IT Architect persona (on behalf of PO), synthesizing input from SRE (retention-window justification), DBA (enforcement mechanism), and Technical Writer (write-up consistency + cross-linking). Schema-lifecycle placement is the Architect's call.
- **Bead**: `enhancedchannelmanager-skqln.1` (child of epic `enhancedchannelmanager-skqln` — Stats v2 / v0.17.0)
- **Related**:
  - `enhancedchannelmanager-skqln.2` — Schema + Alembic migration for `session_telemetry` (this ADR is its hard prerequisite; blocked until accepted)
  - `enhancedchannelmanager-skqln.3` — BandwidthTracker single-write refactor + `channel_watch_stats_v` compat view (also blocked on this ADR)
  - `enhancedchannelmanager-skqln.7` — Privacy 11a: pre-implementation threat model + data classification for Stats v2 (`docs/security/threat_model_stats_v2.md`; reviewed alongside this ADR — imposes no retention minimum and accepts the 30d-raw / 400d-rollup numbers below)
  - `enhancedchannelmanager-skqln.10` — Perf baseline + CI benchmark gate (5 hot queries — validates the rollup-table read path this ADR mandates)
  - `enhancedchannelmanager-skqln.11` — SLOs, storage-growth alerts, deployment-safety runbook (operationalizes the pruning-job failure modes this ADR specifies)
  - `enhancedchannelmanager-skqln.12` — Observability instrumentation (the pruning-job metrics this ADR requires)
  - `enhancedchannelmanager-skqln.14` — Active-stream → provider resolver (produces the `provider_id` dimension this ADR's per-provider rollup depends on)
  - `docs/database_migrations.md` — Alembic authoring guide; the `session_telemetry` table and its rollup tables land as migrations governed by that doc
  - `docs/architecture.md` — system overview (Stats v2 data foundation; update on acceptance to reference this ADR's lifecycle policy)
  - `docs/adr/ADR-006-frontend-error-telemetry.md` — sibling telemetry ADR (Phase 1 local sink, OTel migration target) — precedent for "local SQLite sink now, externalize later" framing

## Context

The Stats v2 initiative (v0.17.0, epic `skqln`) introduces a new `session_telemetry` table — a unified, append-mostly fact table written once per `BandwidthTracker` poll cycle (currently every `stats_poll_interval = 10` seconds, per `backend/config.py:81` / `backend/bandwidth_tracker.py:36`). It replaces the current pattern of incrementally mutating `channel_watch_stats` rows in place; that legacy table is exposed forward via the read-compat `VIEW channel_watch_stats_v` (epic decision, `skqln.3`). `session_telemetry` becomes the system of record for:

- **Per-user watch time** (GH-62 / `skqln.5` `skqln.6`): the Users panel needs watch-time-by-user with a 7/30/90-day selector and a daily trend chart.
- **Per-provider performance** (GH-59 / `skqln.14`–`skqln.18`, pulled into v0.17.0 by PO override 2026-04-23): the Providers panel needs buffering-events-by-provider, time-spent-per-provider, channels-by-provider heatmap, and bitrate-by-provider — and the operator uses this to make **provider keep/drop decisions at renewal**, which the epic body and the 2026-04-23 comment on this bead frame as a **quarterly-window** analysis ("useful in 30d, fully useful in 90d", keep/drop call at renewal).

### Why this ADR must land before the first write

Architect and DBA both asserted this during the 2026-04-21 team-plan as a hard prerequisite. SQLite is the primary datastore (`/config/journal.db`, single-container, WAL mode). At a 10-second poll cadence, every active channel emits a row per poll:

- DBA's projection: **~13–26M rows over 3–6 months at current load** (~1.5–3 GB with indexes). For SQLite that is workable **only** with disciplined retention and pre-aggregated rollups. Without a retention policy in place at first write, the table grows unbounded, the WAL grows with it, `VACUUM` becomes a long-lock event, and the hot aggregation queries (`skqln.10`'s 5-query benchmark gate) degrade super-linearly because SQLite has no parallel scan and no partitioning.
- The DBA's strong position — endorsed here — is that the read path **must** hit pre-aggregated rollup *tables*, never a view over raw rows. A view re-scans the full 26M-row table on every panel load; a daily rollup table is a few thousand rows.

So this ADR is not "should we have retention?" — the answer is unconditionally yes. The architectural questions are: **how long is the raw window, what shape are the rollups, who prunes, and when does this outgrow SQLite?**

### Privacy interaction (concurrent bead `skqln.7`)

`skqln.7` (Privacy threat model + data classification — `docs/security/threat_model_stats_v2.md`) was reviewed alongside this ADR. Security sets the retention **minimum**, DBA/SRE the operational **maximum**; the threat model imposes **no minimum** (shorter raw retention is strictly safer) and recommends the per-user rollup be **bounded** (~13 months) rather than kept forever. The values below honor that. Field-level handling adopted:

- `user_id` is **PII-adjacent behavioral data** (threat model class C2). Raw per-user rows are not retained longer than the stated use cases require (per-user 7/30/90-day watch time); the per-user rollup is bounded, not "forever".
- **Provider attribution** reveals which upstream M3U providers an operator uses and how heavily. Treat as **operator-visible-only** — not exposed to non-admin users — but it is *not* end-user PII (the operator owns their own provider relationships). Provider rollups can be retained longer than user rollups.

## Decision

### D1 — Raw retention window: **30 days**

`session_telemetry` raw rows are pruned at **30 days** of age (by `observed_at`). This is the DBA's proposal and the SRE's "full-fidelity" tier; it is *not* the Architect's earlier 180-day lean, and it is *not* the SRE's "30d full + 90d 5-min-bucket downsampled raw" two-tier proposal. Rationale for picking 30 days over the alternatives:

| Driver | Verdict at 30d raw |
|---|---|
| **Per-user use case (GH-62)** | Fully satisfied. The Users panel's longest selector is 90 days, but it reads the **per-user daily rollup** (D4), not raw rows. Raw rows beyond ~2 days are never read by the user panel — they exist only to *build* the daily rollup, which the nightly job (D3) does within hours of ingest. 30 days of raw is generous headroom for a rollup job that runs nightly. |
| **Per-provider quarterly use case (GH-59, the 2026-04-23 amendment)** | Satisfied **via rollups, not raw**. The keep/drop-at-renewal decision needs a *quarterly trend*, which is a sequence of **daily provider rollup rows** (D5) covering ~90+ days — not 90 days of raw 10-second samples. Retaining raw for a quarter to serve this would be ~13M rows for zero additional analytic value over the daily rollup. The correct fix for the quarterly need is **long-lived provider daily rollups (D5: 400 days)**, not a longer raw window. |
| **Operational cost** | 30 days ≈ 2–4M rows ≈ 250–500 MB with indexes — comfortably inside SQLite's happy range; `VACUUM` stays short; WAL stays bounded. The SRE's 90-day downsampled-raw tier would add a second write path (the downsampler) and a second schema, for data that the daily rollups already capture at the granularity the panels use. Rejected as complexity without a consumer. |
| **Privacy minimization** | 30 days is the shortest window that still leaves comfortable slack for a *failed* nightly rollup to be caught and re-run before the source rows age out (see D3 failure modes). Going shorter (DBA floated nothing shorter; 30 is the floor of the proposals) would tighten that recovery window. Going longer retains PII-adjacent per-user rows past their use. |

**The tradeoff being accepted:** ad-hoc forensic queries against *sub-daily* granularity (e.g., "what did the 14:32–14:38 buffering spike on provider X look like minute-by-minute three months ago?") are only answerable for the last 30 days. Beyond that, only daily-granularity rollups exist. This is judged acceptable: the panels are daily-granularity by design, and sub-daily forensics on month-old data is not a stated requirement. If it becomes one, the cheap fix is the SRE's downsampled-raw tier as an additive change — see Exit Path.

> **`skqln.7` outcome (2026-05-12):** the Stats v2 privacy threat model imposes **no minimum** on raw retention and does not object to 30 days. The 30-day window stands as final for both user- and provider-dimension data.

### D2 — Rollups are **tables**, not views

Daily rollups are materialized **tables**, populated by a scheduled job (D3), not SQL `VIEW`s computed at query time. This is the DBA's strong preference, adopted without reservation: a view over `session_telemetry` re-scans up to 26M rows on every Stats-panel load; a daily rollup table is on the order of `days_retained × distinct_keys` rows (thousands), and the `skqln.10` benchmark gate is written against the table read path. The legacy `channel_watch_stats_v` (`skqln.3`) is a *compat* view over a small legacy table — that is a different animal and stays a view; the new aggregates do not.

### D3 — Rollup schedule: **nightly scheduled job**, idempotent, re-runnable

A single nightly job (not on-demand, not per-request) recomputes the previous day's rollup rows and prunes aged raw rows. Design:

- **Trigger**: runs once per day during a low-traffic window (default ~03:30 local; configurable). Implemented as an `asyncio` task scheduled from `main.py`'s startup (the same place `BandwidthTracker` is wired, `backend/main.py:793`), guarded so it runs at most once per calendar day even across restarts (persist a `last_rollup_date` marker — a row in a small `telemetry_rollup_state` table or a settings key). **Not** a cron-in-container (the app is the only long-lived process; adding `cron` is unjustified infra) and **not** APScheduler (a new dep for one job — overkill; the existing `asyncio` loop pattern is the precedent).
- **Operation order, each run**:
  1. **Roll up** all *complete* days not yet rolled up (UTC day boundaries; "complete" = `observed_at < start_of_today_UTC`). Compute per-user and per-provider daily aggregates (D4, D5) with an **upsert** keyed on the rollup PK, so a re-run is idempotent and a partial prior run self-heals.
  2. **Verify** the rollup wrote rows for every day it claimed to cover (sanity assertion; if a day's source rows existed but produced zero rollup rows, that is a failure, not a no-op).
  3. **Prune** raw `session_telemetry` rows older than the D1 window (`observed_at < now - 30d`) — *only after* step 2 confirms those days are durably rolled up. Pruning is `DELETE` in bounded batches (e.g., 50k rows/statement) to avoid a single long write lock; followed by an incremental `PRAGMA wal_checkpoint(TRUNCATE)` and a periodic (weekly) `VACUUM` in the same window.
  4. **Emit metrics** (D6).
- **Idempotency / catch-up**: because step 1 covers *all* unrolled complete days (not just yesterday), a job that was skipped for N days because the container was down catches up on the next run. The 30-day raw window is the catch-up budget: as long as the rollup job runs successfully at least once every ~28 days, no raw data is pruned before it is rolled up. Below that, raw rows age out unrolled — see failure modes.

### D4 — Per-user rollup: `watch_time_by_user_daily`

```
watch_time_by_user_daily
  user_id        TEXT     NOT NULL    -- Dispatcharr user identifier; NULL → 'unknown' sentinel row, not dropped
  channel_id     TEXT     NOT NULL    -- so the panel's per-channel table (total minutes, last watched) is servable from the rollup
  day            DATE     NOT NULL    -- UTC calendar day
  watch_seconds  INTEGER  NOT NULL    -- sum of poll_interval × client_count contributions for (user, channel, day)
  session_count  INTEGER  NOT NULL    -- distinct viewing sessions that day (for "times watched")
  last_watched   DATETIME NOT NULL    -- max(observed_at) for (user, channel, day) — feeds the panel's "last watched" column
  PRIMARY KEY (user_id, channel_id, day)
```

- **PK = `(user_id, channel_id, day)`.** This is the grain the Users panel queries at: the 7/30/90-day selector is `SUM(watch_seconds) WHERE user_id = ? AND day >= ?`; the channel table is `GROUP BY channel_id WHERE user_id = ?`; the daily trend chart is `GROUP BY day WHERE user_id = ?`. All three are index-range scans over a table with `≈ active_users × active_channels × days_retained` rows.
- **Retention of *this* table: 400 days** (≈ 13 months — covers a full year plus slack for year-over-year glances and for a late audit). After 400 days these rows are pruned by the same nightly job. *This is the privacy-relevant horizon for user data* — it is bounded, not "forever". Flagged for `skqln.7` confirmation: if the privacy minimum for user-attributed aggregates is shorter (e.g., 90 days), this drops to that value and the 90-day panel selector is served from a table that goes exactly as far back as the longest selector plus a small buffer.
- Secondary index: `(day)` for the cross-user "all users" admin view and for the prune scan.

### D5 — Per-provider rollup: `provider_performance_daily`

```
provider_performance_daily
  provider_id          TEXT     NOT NULL  -- upstream M3U provider id from the skqln.14 resolver; NULL → 'unknown' bucket row, surfaced not dropped
  channel_id           TEXT     NOT NULL  -- enables the channels-by-provider heatmap without a raw rescan
  day                  DATE     NOT NULL  -- UTC calendar day
  watch_seconds        INTEGER  NOT NULL  -- time spent on this (provider, channel) that day → stacked-area "time per provider"
  bytes_delta_sum      INTEGER  NOT NULL  -- summed bytes for the day → derives daily mean bitrate (bytes_delta_sum*8 / watch_seconds)
  buffer_event_count   INTEGER  NOT NULL  -- buffering events ingested for this provider that day (skqln.15) → "buffering by provider" time-series
  sample_count         INTEGER  NOT NULL  -- number of poll samples contributing — denominator for averages, and a data-quality signal
  PRIMARY KEY (provider_id, channel_id, day)
```

- **PK = `(provider_id, channel_id, day)`.** Covers all four Providers-panel visualizations: buffering-by-provider (`SUM(buffer_event_count) GROUP BY provider_id, day`), time-per-provider stacked area (`SUM(watch_seconds) GROUP BY provider_id, day`), channels-by-provider heatmap (`GROUP BY provider_id, channel_id` over a window), bitrate-by-provider (`SUM(bytes_delta_sum)·8 / SUM(watch_seconds) GROUP BY provider_id, day`). All are range scans on a table with `≈ providers (<20) × active_channels × days_retained` rows — small.
- **Retention of *this* table: 400 days.** This is what actually serves the **quarterly keep/drop-at-renewal use case** that the 2026-04-23 amendment raised. 400 days lets the operator compare this renewal window against the last one (annual contracts) and see a full year of provider trend. Provider attribution is operator-visible-only data, not end-user PII, so a longer horizon than the user rollup is acceptable — but 400 days is still a *bounded* horizon, not "forever", because unbounded growth (even at thousands of rows/day) is exactly the failure mode this ADR exists to prevent, and there is no use case beyond ~13 months.
- Secondary index: `(provider_id, day)` (the dominant query prefix) and `(day)` for the prune scan.
- **NULL `provider_id`**: surfaces as an `'unknown'` bucket row, never silently excluded (epic decision; `skqln.11` runbook covers the cascade). The resolver (`skqln.14`) is best-effort; the rollup must not hide its misses.

### D6 — Pruning-job ownership, failure modes, alerting

- **Ownership**: the **SRE persona owns the operational behavior** (SLOs, alert thresholds, the deployment-safety runbook entry — `skqln.11`); the **engineer owns the implementation** (the `asyncio` task, the upsert, the batched delete); the **DBA owns the enforcement correctness** (that pruning is gated on durable rollup, that batch sizes don't lock-storm, that `VACUUM` cadence is right). There is no separate "pruning service" — it is a function of the ECM container, like `BandwidthTracker`.
- **Metrics emitted each run** (Prometheus, via `skqln.12`): `ecm_telemetry_rollup_last_success_timestamp` (gauge — for staleness alerting), `ecm_telemetry_rollup_duration_seconds`, `ecm_telemetry_rollup_days_processed`, `ecm_telemetry_raw_rows_pruned`, `ecm_telemetry_raw_row_count` (current size — feeds the storage-growth alert), `ecm_telemetry_rollup_errors_total`. **Cardinality discipline (SRE veto, epic decision): `user_id` and `channel_id` are NEVER metric labels; `provider_id` IS allowed (bounded <20).** The rollup metrics above are label-free or provider-labeled only.
- **Failure modes & responses** (full version in the `skqln.11` runbook):
  1. **Rollup job hasn't succeeded in >36 h** (one missed nightly + margin) → **warning alert**. Likely cause: container restart loop, an exception in the job, or a schema mismatch. Action: inspect logs, re-run manually; raw data is safe (30-day window).
  2. **Rollup job hasn't succeeded in >25 days** → **page**. Now within striking distance of raw rows aging out *before* being rolled up — permanent data loss risk for the un-rolled days. Action: emergency manual rollup before the prune step would fire.
  3. **`ecm_telemetry_raw_row_count` exceeds the SQLite-comfort ceiling** (DBA-set, e.g., >5M rows) when the rollup *is* succeeding → indicates the prune step is failing silently or the poll cadence/active-channel count has grown beyond projection. Action: investigate prune; this is also the **Postgres-trigger early-warning signal** (see D7).
  4. **Prune `DELETE` batch causes a lock-contention spike** (write latency SLI breach during the rollup window) → tune batch size down; if persistent, move the window or split the prune across multiple short runs. The `skqln.11` SLOs include a per-provider read-latency SLI and a write-latency SLI that this would trip.
  5. **Rollup wrote zero rows for a day that had source rows** (the D3 step-2 sanity check fails) → the job aborts *before* pruning that day's raw rows, logs an error, increments `ecm_telemetry_rollup_errors_total`, and the staleness clock keeps running (so failure mode 1 fires if it persists). Fail-safe: never prune what you couldn't roll up.
- **Deployment safety**: the `session_telemetry` schema, both rollup tables, and the `telemetry_rollup_state` marker land via Alembic per `docs/database_migrations.md` — including the `(provider_id, channel_id, observed_at) INCLUDE (bytes_delta)` covering index the epic mandates for the heatmap query (`skqln.2`). The first deploy ships an *empty* table; the "useful in 30d, fully useful in 90d" operator messaging (epic risk-acceptance item 2) is the consequence — there is no backfill path into the provider-free legacy history, and this ADR does not invent one.

### D7 — Postgres migration trigger: **>50M `session_telemetry` rows OR a second writer**, not v0.17.0

ECM stays on SQLite for Stats v2. The DBA's threshold, adopted: a migration to PostgreSQL is triggered when **either**:

- `session_telemetry` (raw) is projected to exceed **~50M rows** at steady state — which, with the 30-day retention in D1, only happens if poll cadence drops well below 10 s *or* active-channel counts grow ~20× over current. At that scale SQLite's single-writer + no-parallel-scan limits start to bite even with rollups. The `ecm_telemetry_raw_row_count` metric (D6 failure mode 3) is the leading indicator; SRE/DBA review it at the standing monthly cadence.
- **OR** ECM gains a second concurrent writer to the telemetry path (e.g., a separate ingest worker, a sidecar, or a multi-instance deployment) — SQLite's single-writer lock makes that a correctness hazard, not just a performance one.

Neither condition holds for v0.17.0 (`skqln`'s explicit "single-write to `session_telemetry`" decision keeps it one writer; the 30-day window keeps row count in the low millions). When the trigger fires, that is its own ADR (the rollup *tables* and the nightly-job design port cleanly to Postgres; the `asyncio` scheduler and the batched-delete-then-vacuum dance get simpler, not harder, on Postgres with partitioning).

## Alternatives Considered

| # | Option | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| 1 | **30d raw + nightly rollup tables + 400d rollup retention (chosen)** | Satisfies both use cases (user 7/30/90 via daily rollup; provider quarterly via 400d provider rollup); SQLite stays comfortable; one write path; one scheduled job reuses the existing `asyncio` pattern; privacy-minimizing on the raw window; bounded rollup horizon | Sub-daily forensics on >30-day-old data is unavailable; the empty-panel-for-30d operator UX cost is real; the nightly job is a new operational surface (mitigated by D6 alerting) | High — pure SQLite + `asyncio`; ports to Postgres cleanly | One Alembic migration set + one `asyncio` task; ~0 new infra |
| 2 | **180d raw, no/late rollups (Architect's earlier lean)** | Ad-hoc raw forensics for 6 months; defers the rollup-job work | ~13–26M raw rows in SQLite — `VACUUM` lock events, WAL bloat, super-linear panel-query degradation (no partitioning); retains PII-adjacent per-user rows 6× longer than any use case needs; panels still need rollups eventually, so this just delays the real work while paying the storage cost | High but operationally fragile | Low now, high later (forced rollup retrofit + a painful first prune of 25M rows) |
| 3 | **30d full raw + 90d 5-min-bucket downsampled raw (SRE's two-tier proposal)** | Preserves sub-daily (5-min) granularity for a quarter; richer forensics | A *second* write path (the downsampler) and a *second* schema, for data the daily rollups already cover at the granularity every panel actually uses; downsampler is itself a job that can fail/fall behind; more surface for the same analytic outcomes | High | Higher — two jobs, two schemas, two sets of failure modes |
| 4 | **Rollups as SQL `VIEW`s, no materialized tables** | Zero rollup-job complexity; always "fresh"; no rollup-table retention to manage | Every Stats-panel load full-scans up to 26M raw rows; `skqln.10`'s 5-query benchmark gate would fail by design; degrades with raw growth; the DBA's explicit veto | High | Zero build cost, unacceptable run cost |
| 5 | **On-demand rollup (compute-and-cache on first panel hit each day)** | No scheduled job; rollup happens lazily | First panel load each day eats the full aggregation latency (multi-second on a 26M-row table); cache invalidation is a new concern; pruning still needs *some* scheduled trigger so you don't actually remove the job — you just make it user-latency-visible | High | Similar to chosen, worse UX |
| 6 | **Move to Postgres now for v0.17.0** | Partitioning, parallel scan, `pg_partman`-style retention, no single-writer lock | ECM is a single-container self-hosted app; adding Postgres is a deployment-model change for users, a new ops burden, and unjustified at low-millions row scale; violates the project's "SQLite primary, single container" posture; the DBA's threshold (D7) isn't met | Medium — adds an external datastore dependency | High — operator-facing deployment change, far ahead of need |

## Consequences

### Positive

- **`session_telemetry` is bounded by construction.** Raw rows can't accumulate past 30 days; rollup tables can't accumulate past 400 days. SQLite stays in its comfort zone for the foreseeable life of v0.17.0; `VACUUM` stays a short weekly event; the WAL stays small.
- **Both Stats v2 use cases are served from cheap reads.** The Users panel and the Providers panel hit small daily-rollup tables (thousands of rows), not the fact table. `skqln.10`'s benchmark gate is written against that read path and is achievable.
- **The quarterly provider keep/drop decision is actually answerable** — and it's answerable from the *right* artifact (a 400-day sequence of daily provider rollups), not from an unaffordable 90-day raw window.
- **Privacy posture is defensible.** Raw PII-adjacent rows live 30 days; user-attributed aggregates live a bounded 400 days (subject to `skqln.7` possibly shortening either). Nothing is "retained forever". Provider attribution is operator-visible-only by data classification.
- **One write path, one scheduled job, no new infra.** Reuses the `asyncio` startup-task pattern already used for `BandwidthTracker`. No cron, no APScheduler, no external scheduler, no Postgres.
- **Clean Postgres exit when (if) the threshold trips** — rollup tables and the nightly-job logic port directly; the operational dance gets simpler on Postgres, not harder.
- **Fail-safe pruning.** The job never deletes a raw row for a day it hasn't durably rolled up; the 30-day window is a ~28-day catch-up budget for a job that's been down.

### Negative

- **30-day raw window means sub-daily forensics on older data is gone.** "Show me the minute-by-minute buffering on provider X during last quarter's incident" is answerable only for the last 30 days. Accepted (not a stated requirement); mitigated by the additive-downsampler exit path if it ever becomes one.
- **The Providers panel is sparse for the first 30 days post-deploy and not "fully useful" until ~90 days** (epic risk-acceptance item 2). No backfill into the provider-free legacy history exists, and this ADR doesn't invent one. Operator messaging ("useful in 30d, fully useful in 90d") is the mitigation, owned by `skqln.9` (user guide) and `skqln.11` (runbook).
- **A new operational surface: the nightly rollup/prune job.** It can fail, fall behind, or lock-storm on the prune `DELETE`. D6's metrics + the `skqln.11` SLOs/alerts are the backstop; the >25-day "page" threshold is the hard floor against data loss.
- **Rollup-table retention is a second knob to get right.** 400 days is a judgment call, PO-confirmed 2026-05-12 and accepted by the `skqln.7` privacy review (which recommends bounding the per-user rollup at ~13 months — 400 days satisfies it). It's a one-line config change if a future need shifts it.
- **`telemetry_rollup_state` is one more table** (the once-per-day marker). Trivial, but it's schema surface that lands via Alembic and must be in the drift test.

### Neutral / Out of Scope

- **The `session_telemetry` schema itself** — column list, types, the covering index — is `skqln.2`'s deliverable, governed by `docs/database_migrations.md`. This ADR specifies the *rollup* table shapes (D4, D5) and the *retention* knobs; it does not redefine the fact table.
- **The provider resolver's correctness** (`skqln.14`) and **buffer-event ingestion** (`skqln.15`) are upstream of this ADR. This ADR only specifies that NULL `provider_id` becomes an `'unknown'` bucket, never a silent drop.
- **The Stats tab IA / sub-nav refactor** is explicitly deferred to v0.18.0 (epic decision) and is unrelated to retention.
- **Externalizing telemetry to an OTel/Prometheus-remote-write pipeline** (cf. ADR-006's migration target for *frontend error* telemetry) is not in scope. If ECM ever ships a "send my stats to my own observability stack" feature, that's a new ADR; this one is about the local SQLite sink's lifecycle.

## Exit Path

If the chosen policy proves wrong:

1. **Soft exit — extend the raw window.** If 30 days turns out too tight for the rollup-catch-up budget (frequent container downtime in the field), bump the D1 window to 45 or 60 days. One config value + one Alembic-free change (the prune predicate reads the config). Costs more SQLite storage; no schema change.
2. **Additive exit — add the SRE downsampled-raw tier.** If sub-daily forensics on month-old data becomes a real requirement, add a `session_telemetry_5min` rollup table (5-minute buckets, ~90-day retention) populated by an extra step in the same nightly job. Additive: doesn't touch the existing tables or the daily rollups; one migration + one job step.
3. **Adjust rollup-table retention.** 400 days → whatever `skqln.7` mandates (down) or the operator community asks for (up, within reason). Config value; the prune predicate reads it. No schema change.
4. **Hard exit — Postgres migration** when D7's threshold trips. Own ADR. Rollup tables and the nightly-job logic port directly; SQLite-specific bits (`PRAGMA wal_checkpoint`, batched `DELETE` to dodge the write lock, weekly `VACUUM`) get replaced by Postgres-native partitioning + `DROP PARTITION`-style retention, which is simpler. Operator-facing: a deployment-model change (adds a Postgres container) — the reason D7 sets the bar high.

No vendor relationship to unwind; no external dependency introduced by this ADR.

## Open Questions

### Resolved inline (no PO action needed)

- **Raw retention window?** → **30 days** (D1). DBA's proposal; serves both use cases via rollups; privacy-minimizing on raw.
- **Rollup schedule — nightly vs. on-demand?** → **Nightly scheduled `asyncio` task** (D3), idempotent and catch-up-capable. On-demand rejected (user-latency cost; still needs a scheduled prune trigger anyway).
- **Rollup tables vs. views?** → **Tables** (D2). DBA's strong preference; views re-scan 26M rows; the benchmark gate assumes tables.
- **Per-user rollup PK/shape?** → `watch_time_by_user_daily`, **PK `(user_id, channel_id, day)`** (D4). Matches all three Users-panel queries.
- **Per-provider rollup PK/shape?** → `provider_performance_daily`, **PK `(provider_id, channel_id, day)`** (D5). Covers all four Providers-panel visualizations.
- **Pruning job — who owns it, alerting, failure modes?** → SRE owns operational behavior, engineer owns implementation, DBA owns enforcement correctness; failure modes 1–5 with warn/page thresholds at >36 h / >25 days; fail-safe (never prune an un-rolled day) (D6).
- **When does the Postgres trigger fire?** → **>50M raw rows OR a second concurrent telemetry writer** — neither holds for v0.17.0 (D7).
- **Does the quarterly provider need force a longer raw window?** → **No.** It forces a long-lived *provider rollup* (400 days, D5), which is the correct artifact for a quarterly/annual trend. "30d raw + rollups (bounded) forever" stands — with the clarification that "forever" is operationalized as a *bounded 400-day* rollup horizon, not literally unbounded.

### PO decisions — resolved 2026-05-12

1. **Rollup-table retention horizon → 400 days for both `watch_time_by_user_daily` and `provider_performance_daily`.** PO-confirmed. Implemented as a config value; revisit only if a concrete need appears.

2. **Privacy sign-off (`skqln.7`) — 30-day raw window and 400-day user-attributed-aggregate horizon accepted.** The concurrent Stats v2 threat model (`docs/security/threat_model_stats_v2.md`) imposes no minimum on raw retention (shorter is strictly safer) and explicitly does not object to 30 days; it recommends the per-user rollup be bounded (~13 months), which the 400-day horizon satisfies. D1 and D4 are therefore final, not provisional. (The threat model's remaining conditions — `user_id` FK `ON DELETE SET NULL`, redaction requirements folded into `skqln.2`/`.3` acceptance, synthetic-identity test fixtures — are tracked on those beads, not here.)

3. **Operator messaging for the 30/90-day "warm-up" → empty-state banner in the Providers panel + a line in the `skqln.9` user-guide entry.** PO-confirmed.

4. **Telemetry opt-out (related, decided alongside this ADR) → operator-level global toggle only**, no per-user opt-out, for v0.17.0. Disables `session_telemetry` collection entirely when off. (Tracked under `skqln.7`/`skqln.8`; noted here because it bounds what the pruning/rollup pipeline must handle — when the toggle is off there is simply no data to retain.)

## References

- Bead `enhancedchannelmanager-skqln.1` — this ADR's tracker
- Bead `enhancedchannelmanager-skqln` — Stats v2 epic (v0.17.0); carries the full scope + PO-override trail
- Bead `enhancedchannelmanager-skqln.2` — `session_telemetry` schema + Alembic migration (blocked on this ADR)
- Bead `enhancedchannelmanager-skqln.3` — BandwidthTracker single-write refactor + `channel_watch_stats_v` compat view (blocked on this ADR)
- Bead `enhancedchannelmanager-skqln.7` — Privacy 11a threat model + data classification (concurrent; sets the retention minimum)
- Bead `enhancedchannelmanager-skqln.10` — Perf baseline + CI benchmark gate (5 hot queries — validates the rollup-table read path)
- Bead `enhancedchannelmanager-skqln.11` — SLOs, storage-growth alerts, deployment-safety runbook (operationalizes D6)
- Bead `enhancedchannelmanager-skqln.12` — Observability instrumentation (the D6 metrics)
- Bead `enhancedchannelmanager-skqln.14` — Active-stream → provider resolver (produces the `provider_id` dimension)
- Bead `enhancedchannelmanager-skqln.15` — Buffer-event ingestion (feeds `provider_performance_daily.buffer_event_count`)
- `docs/database_migrations.md` — Alembic authoring guide; `session_telemetry` + rollup tables + `telemetry_rollup_state` land per that doc
- `docs/architecture.md` — system overview (update on acceptance to reference this lifecycle policy)
- `docs/adr/ADR-006-frontend-error-telemetry.md` — sibling telemetry ADR (local-sink-now, externalize-later precedent)
- `backend/bandwidth_tracker.py` — the writer (`_collect_stats`, 10 s poll); `main.py:793` — where the scheduled rollup task wires in
- `backend/config.py` — `stats_poll_interval` (the cadence driving row volume)
- `backend/models.py` — `ChannelWatchStats` (the legacy table `channel_watch_stats_v` views over)

## Revision History

| Date | Bead | Change | Rationale |
|---|---|---|---|
| 2026-05-12 | `enhancedchannelmanager-skqln.1` | Proposed | Initial retention/rollup/lifecycle contract for `session_telemetry`; hard prerequisite for `skqln.2`/`skqln.3` |
