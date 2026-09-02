# Database Migrations

Enhanced Channel Manager uses [Alembic](https://alembic.sqlalchemy.org/) on top of SQLAlchemy to version and evolve the SQLite schema at `/config/journal.db`. The baseline revision (`0001`) was introduced in bead `bd-c5wf5` (PR #81, commit `f996ec9b`, 2026-04-20) to unblock DBAS restore/sync (`bd-gb5r5.3`, `bd-gb5r5.4`), which must be able to gate on a known schema version before importing a backup.

This document is the authoring guide for every schema change that lands in ECM. It is a lean first pass. Expect to fill in gotchas as the next few revisions land.

> **Baseline not yet exercised.** Revision `0001` passes the drift test (`test_baseline_matches_metadata_no_drift`) and runs on fresh installs via `_bootstrap_alembic`, but as of 2026-04-20 no one has round-tripped `upgrade head` → `downgrade base` → `upgrade head` against a non-empty DB. Treat that smoke test as a precondition before trusting a real production rollback. A follow-up bead tracks the actual exercise.

## Layout

```
backend/
  alembic.ini                   # Alembic config; script_location = %(here)s/alembic
  alembic/
    env.py                      # Loads ECM metadata + runtime DB URL
    script.py.mako              # Template for new revision files
    versions/                   # One .py per migration, committed to git
      20260420_2034_0001_baseline_initial_schema.py
  database.py                   # init_db() → _bootstrap_alembic() → upgrade/stamp
  tests/unit/test_alembic_baseline.py   # Drift + FK + schema-version tests
```

Filename convention (from `alembic.ini` `file_template`): `YYYYMMDD_HHMM_<revid>_<slug>.py`. Keep revision IDs sequential (`0001`, `0002`, ...) so `alembic history` reads chronologically.

### Container layout (important)

The backend deploys **flat** to `/app/` (not `/app/backend/`), so in the running container:

| Repo path | Container path |
|-|-|
| `backend/alembic.ini` | `/app/alembic.ini` |
| `backend/alembic/env.py` | `/app/alembic/env.py` |
| `backend/alembic/versions/*.py` | `/app/alembic/versions/*.py` |

Run `alembic` commands from `/app` inside `ecm-ecm-1`. Deploying a new revision is `docker cp backend/alembic/versions/<file>.py ecm-ecm-1:/app/alembic/versions/`.

## Runtime behaviour

`init_db()` (called on app startup from `main.py`) hands control to `_bootstrap_alembic()` in `database.py`:

| Situation | Action |
|-|-|
| Fresh install, empty DB | `alembic upgrade head` creates every table |
| Pre-Alembic install (existing DB, no `alembic_version` row) | `alembic stamp head` records the revision without re-running DDL |
| Existing Alembic install at head | `upgrade head` is a no-op |
| Existing Alembic install behind head | `upgrade head` applies pending revisions in order |

Both the app and the test suite depend on the `PRAGMA foreign_keys=ON` / `PRAGMA journal_mode=WAL` connect-listener registered in `database.py`. `alembic/env.py` inherits those PRAGMAs automatically because the listener attaches to the SQLAlchemy `Engine` class.

### The smart-bootstrap fast path, and destructive migrations

There is a fifth case the table above glosses over. On a long-running install
`Base.metadata.create_all()` can materialise a table or column *before* the
migration that was supposed to create it. `_bootstrap_alembic` detects that
("`alembic_version` lags head but the live schema already covers the model
shape") and **stamps forward instead of upgrading**. The migrations have
nothing left to do. This is the bd-5w6jz fix; without it every release needs
per-migration `inspect`-then-skip guards or startup crashes on
`already exists`.

The predicate behind it, `_schema_matches_head`, is a **subset check**: every
`Base.metadata` table and column must exist in the live DB. It deliberately
ignores extra tables and columns the model no longer declares.

**A drop-only migration is therefore invisible to it.** Every model artifact is
present *precisely because* the migration's job is to remove something the
model no longer declares. Revision `0041` hit this in production on
2026-07-30: the log read `Running stamp_revision 0040 -> 0041`,
`alembic_version` said `0041`, `lookup_tables` was still there, and 0041's
pre-drop row dump never ran (bead `enhancedchannelmanager-nywpw`).

`_bootstrap_alembic` now walks the pending revisions and **refuses to stamp
across any that remove a schema object or delete rows**. Those are replayed
individually: stamped to the predecessor, then `upgrade`d to that revision by
id. Everything else is still stamped over, so the fast path keeps its
benefit for the non-destructive majority.

Detection is automatic: `_revision_is_destructive` AST-parses the revision
module (minus `downgrade()` and docstrings) looking for `op.drop_table` /
`drop_column` / `drop_index` / `drop_constraint` (including `batch_op`
equivalents) and for `DROP …` / `DELETE FROM` / `TRUNCATE` in SQL string
literals. A module that will not parse is treated as destructive.

**As an author you normally need to do nothing.** Two escape hatches exist for
the cases static analysis cannot see:

```python
destructive = True   # opt IN: this revision removes something the scanner
                     # cannot see (e.g. a batch_alter_table(copy_from=...)
                     # rebuild that drops a constraint, or dynamically-built
                     # SQL). Revision 0011 uses this.

destructive = False  # opt OUT of a scanner false positive. Justify it in the
                     # revision docstring — this is the only way to lose data
                     # by forgetting nothing. Should be rare.
```

Bias toward over-detection: a false positive costs one extra (idempotent)
migration actually running; a false negative is silent data loss.

### Selected-rule audit data (revision 0051)

Revision `0051` adds nullable `TEXT` column
`auto_creation_executions.selected_rule_outcomes`. Upgrade is idempotent only
when an existing column has that exact compatible shape; startup and migration
both fail closed if the column is non-text or non-nullable. Deploy the backend
revision before a frontend that expects selected-rule integrity and per-rule
outcomes.

The JSON array remains in canonical audit/display order (ascending rule priority,
then rule ID), even though temporal processing is phase-ordered: all selected
Standard rules run before selected Event Sync rules. During a selected run the
same column receives bounded phase checkpoints: one Standard start write, one
Standard boundary write, and start/terminal writes around each Event Sync rule.
Run-all and single-rule executions keep the column `NULL` and do not use these
checkpoints.

Before any downgrade to `0050`, **stop every ECM instance and create a
full-fidelity recovery copy of `journal.db`**. A standard backup and a legacy
Full Backup are not sufficient: both deliberately remove Channel Pipeline
execution history. After stopping every instance, use SQLite's backup facility
to write a separate database file, or copy `journal.db` only after shutdown has
completed. An encrypted DBAS artifact may be retained as an additional recovery
artifact, but restore preview alone does not expose or validate its embedded
SQLite database and is not sufficient evidence for this downgrade.

Verify the raw recovery copy before altering history: open that copy with SQLite,
require `PRAGMA integrity_check` to return `ok`, and query
`auto_creation_executions` to confirm the expected rows and non-null
`selected_rule_outcomes` values are present. Keep the verified file outside the
active configuration directory. Do not delete selected-rule audit rows until
this verification is complete.

To recover, stop every ECM instance again, preserve the failed database and its
`-wal`/`-shm` sidecars for diagnosis, restore the verified database file to the
configured `journal.db` path without mixing in sidecars from another copy, and
then restart ECM. Re-run `PRAGMA integrity_check` against the restored file
before startup if the restore operation copied or transformed it.

Downgrade is safe only under operator-provided quiescence and while every row has
`selected_rule_outcomes IS NULL`. Revision `0051` refuses when it observes an
active/running execution or selected-rule audit data, but SQLite's
nontransactional DDL leaves a race between those checks and the native column
drop: the migration cannot prove that an external ECM process stayed stopped.
Once any selected-rule run has persisted audit data, `alembic downgrade 0050`
intentionally refuses rather than silently erase execution history. Roll back
application code without downgrading the database, or explicitly archive and
remove that audit data only after establishing the verified recovery point
above. The guard is a data-safety check under quiescence, not atomic
cross-process locking and not a migration failure to bypass during routine
deployment rollback.

Two consequences for authoring:

- **Keep destructive migrations idempotent** (`inspect`-guarded), like every
  other revision. The replay path runs them by id, and the self-heal below may
  run them a second time.
- **Give a destructive migration its own safety net.** Revision 0041 writes
  every row it is about to delete to `<db_dir>/lookup_tables_dropped_0041.json`
  before dropping. That dump is what makes an unattended replay acceptable.

#### Self-heal for installs already stamped past a drop

Installs the *pre-fix* fast path already stamped past sit at
`alembic_version == head` with the dropped table still present, so nothing
above fires for them. `_heal_stamped_past_drops` handles that population: for
each revision in `_STAMPED_PAST_DROP_HEAL` that `alembic_version` claims is
applied but whose tables are still physically there, it stamps back to the
predecessor, upgrades to that revision, and restores the version row.

That registry is hand-curated, which is acceptable *because it is closed by
construction*: no future migration can be stamped past, so no future
migration can need an entry. It covers **table drops only**: a table's absence
is cheap, unambiguous evidence that the revision ran. Dropped columns,
constraints and indexes, and `DELETE`d rows, have no equally cheap generic
probe and are not healed.

Operators can read the applied revision at any time:

```bash
curl -s http://localhost:6100/api/health/schema
# {"current_revision":"0001","head_revision":"0001","up_to_date":true,
#  "foreign_keys_enabled":true,"journal_mode":"wal"}
```

## Authoring a migration

### 1. Make the model change

Edit `backend/models.py` (or `export_models.py`, or `ffmpeg_builder/persistence.py`) the same way you would today. Do **not** write the migration first. Autogenerate needs your intent encoded in the ORM.

### 2. Generate a revision (autogenerate)

From inside the container, with `ecm-ecm-1` running and pointing at your working DB:

```bash
docker exec ecm-ecm-1 sh -c "cd /app && alembic revision --autogenerate -m 'short_imperative_message'"
```

Copy the generated file back into the repo so it lands in git:

```bash
docker cp ecm-ecm-1:/app/alembic/versions/<new-filename>.py backend/alembic/versions/
```

### 2b. Handwritten migration (when autogenerate won't help)

Pure data migrations, or DDL that Alembic's comparer can't infer (e.g., renaming a column while preserving data, splitting one table into two), should be written by hand:

```bash
docker exec ecm-ecm-1 sh -c "cd /app && alembic revision -m 'backfill_user_identities'"
```

Then fill in `upgrade()` / `downgrade()` directly: no autogenerate, no metadata comparison.

### 3. Hand-review the output

Autogenerate is a starting point, not a deliverable. It routinely misses:

- **Indexes** declared outside `Column(index=True)` (e.g., composite indexes via `Index(...)` in `__table_args__`).
- **Check constraints** that rely on database functions.
- **Server defaults** when the Python default doesn't match the generated SQL default.
- **SQLite batch semantics**: see [SQLite gotchas](#sqlite-specific-gotchas).
- **Data migrations**: autogenerate never writes DML. If the schema change requires backfill, add an explicit `op.execute(...)` or a loop against `op.get_bind()`.

Open the generated file and make sure each `upgrade()` step has a mirroring `downgrade()` step. If a change is genuinely irreversible (dropping a table with data, for example), leave an explicit `raise NotImplementedError("cannot downgrade: ...")` and document why in the docstring. Never fake a reversible migration with `pass`.

If the revision **removes** anything (a table, column, index or constraint, or rows), read [The smart-bootstrap fast path, and destructive migrations](#the-smart-bootstrap-fast-path-and-destructive-migrations) before shipping it.

### 4. Test locally

Test against a throwaway SQLite file so a broken migration can't corrupt your working DB. `CONFIG_DIR` is a directory, and the DB lands at `$CONFIG_DIR/journal.db`:

```bash
# Fresh DB, upgrade from scratch (CONFIG_DIR=/tmp/mig_test → /tmp/mig_test/journal.db)
docker exec ecm-ecm-1 sh -c '
  rm -rf /tmp/mig_test && mkdir -p /tmp/mig_test &&
  cd /app && CONFIG_DIR=/tmp/mig_test alembic upgrade head
'

# Apply to a copy of the current prod DB (use ALEMBIC_DATABASE_URL to target a file directly)
docker exec ecm-ecm-1 sh -c '
  cp /config/journal.db /tmp/prod_copy.db &&
  cd /app && ALEMBIC_DATABASE_URL=sqlite:////tmp/prod_copy.db alembic upgrade head
'

# Round-trip the new revision (prove it is reversible)
docker exec ecm-ecm-1 sh -c '
  cp /config/journal.db /tmp/prod_copy.db &&
  cd /app &&
  ALEMBIC_DATABASE_URL=sqlite:////tmp/prod_copy.db alembic upgrade head &&
  ALEMBIC_DATABASE_URL=sqlite:////tmp/prod_copy.db alembic downgrade -1 &&
  ALEMBIC_DATABASE_URL=sqlite:////tmp/prod_copy.db alembic upgrade head
'

# Backend test suite — the baseline-drift test will flag unmatched metadata
cd backend && python -m pytest tests/unit/test_alembic_baseline.py -v
```

If `test_baseline_matches_metadata_no_drift` fails, autogenerate missed something or your migration under-specifies the target schema. Re-run autogenerate (or tune the migration) until the test passes.

### 5. Deploy

Standard container-first workflow (see root `CLAUDE.md`):

```bash
docker cp backend/alembic/versions/<new-filename>.py ecm-ecm-1:/app/alembic/versions/
docker cp backend/models.py ecm-ecm-1:/app/models.py
docker restart ecm-ecm-1
```

Startup will apply the new revision automatically. Check the logs for `[DATABASE] Running alembic upgrade head` followed by Alembic's own `Running upgrade <prev> -> <new>, <message>` line.

## Rollback

Alembic supports `alembic downgrade -1` or `alembic downgrade <rev>`. Two ground rules:

- **Only roll back if `downgrade()` is genuinely non-destructive**. If the up migration dropped data or a column, the down migration cannot restore it. That information is gone.
- **Test the rollback the same way you test the upgrade** (see the round-trip command above). A down migration that has never been exercised is as dangerous as a backup that has never been restored.

For production incidents where Alembic rollback is unsafe, the DBAS restore flow (`bd-gb5r5.3`) is the escape hatch: restore a ZIP backup from before the bad migration, then stamp to that revision:

```bash
docker exec ecm-ecm-1 sh -c "cd /app && alembic stamp <known-good-rev>"
```

`stamp` rewrites only the `alembic_version` row. No DDL is run. Use it when you've restored a DB whose schema is already at the target revision but whose `alembic_version` is missing or wrong.

## SQLite-specific gotchas

- **Choose native drops before batch reconstruction.** The supported SQLite 3.35+ versions can remove a simple, unconstrained column with `op.drop_column(...)`; use that path when possible. Reserve `op.batch_alter_table(...)` for changes SQLite still cannot perform natively, such as type or constraint alterations. Batch mode recreates the table, and replacing a referenced parent while `PRAGMA foreign_keys=ON` can fire inbound `ON DELETE CASCADE` constraints and erase child rows. Before any unavoidable batch reconstruction, inventory inbound foreign keys and add a populated round-trip test that proves parent and child rows survive.
- **Name your constraints explicitly.** `CHECK` and `UNIQUE` constraint names are unstable across SQLite versions. Pass `name=...` to `sa.CheckConstraint` / `sa.UniqueConstraint` rather than relying on auto-generated names.
- **Foreign keys are per-connection.** They are only enforced when `PRAGMA foreign_keys=ON`. The app-wide connect listener in `database.py` handles this everywhere SQLAlchemy opens a connection (including Alembic via `env.py`), but if you run raw `sqlite3` in a shell for debugging, you must set the PRAGMA yourself. Raw `sqlite3.connect()` calls bypass the listener.
- **WAL journal mode is per-connection too.** The listener sets it on every connect, but direct `sqlite3` CLI sessions will use `delete` mode. This is expected and harmless: both modes see the same committed state.

## Data-lifecycle / retention policy

Some tables are append-mostly fact tables that grow with time and need an explicit retention + rollup policy, not just a schema. The first of these is `session_telemetry` (Stats v2, v0.17.0): its retention window, the daily rollup tables (`watch_time_by_user_daily`, `provider_performance_daily`), the `telemetry_rollup_state` marker, the nightly rollup/prune job, and the Postgres-migration trigger are all specified in **[`docs/adr/ADR-007-session-telemetry-retention.md`](adr/ADR-007-session-telemetry-retention.md)**. When you author the migration that creates one of those tables (`bd-skqln.2`), follow the ADR for the table shapes, indexes, and PKs, and add the new tables to the drift test like any other schema object.

General rule: if a new table accumulates rows per unit time (telemetry, events, audit logs), it needs a retention/rollup ADR *before* its first write, not after.

## Backfill policy for `session_telemetry`

`session_telemetry` (migration `0006`; `0007` corrected the `channel_id`
column type; `0008` added the `channel_watch_stats_v` read-compat view)
is the per-poll observation stream that backs Stats v2. It starts
recording the day v0.17.0 deploys. **There is no historical backfill
for periods before that.** This is a deliberate DBA-standup decision
(2026-05-13, bead `bd-skqln.3` step (c)):

- **Option (a) (synthesize `observed_at` / `poll_interval_ms` from the
  legacy `channel_watch_stats` lifetime aggregates):** rejected. The
  legacy table does not carry the per-poll grain `session_telemetry`
  is defined on, so any synthesized rows would be fabricated telemetry,
  worse than no data, because downstream readers (the popularity
  formula, GH-62 watch-time-by-user, GH-59 buffer / provider stats)
  cannot distinguish synthesized rows from real observations.
- **Option (b) (UNION transition window in the read path):** rejected.
  Reading both the legacy aggregate shape and the new per-poll shape on
  every query doubles read cost for as long as the window stays open,
  and the window has no natural close.
- **Option (c) (accept the gap):** chosen. v0.17.0 is when this metric
  began. No pre-v0.17.0 history is recoverable from
  `channel_watch_stats`, because its grain (lifetime aggregate, one
  row per channel) is incompatible with `session_telemetry`'s grain
  (one row per poll per client per channel). The legacy
  `channel_watch_stats` rows are not deleted by the v0.17.0 cutover.
  They remain alongside `session_telemetry` until a separate cleanup
  bead retires the table.

Operator-facing note: [`docs/user_guide/stats/stats-v2-history-cutover.md`](user_guide/stats/stats-v2-history-cutover.md).

## What bead `bd-c5wf5` did NOT do

- No retroactive per-column migrations for historical schema changes. The 30+ ad-hoc `_add_*` functions in `database.py` predate Alembic; they continue to run after `_bootstrap_alembic()` for installs that already crossed those versions. **New columns should land as Alembic revisions**, not new helpers in `database.py`.
- No cleanup of legacy orphan tables in some live DBs (`services`, `health_checks`, `incidents`, `incident_updates`, `maintenance_windows`, `service_alert_rules`, `service_alert_history` from the pre-v0.13 health-monitor subsystem, plus `popularity_rules` removed in v0.11.0-0005). Those tables exist in deployed `journal.db` files but are not in any current model. A future bead should write a revision to drop them.

    **They no longer leave the instance.** Bead `enhancedchannelmanager-gi4zn` made the backup's `journal.db` scrub an allowlist (`_STANDARD_ARTIFACT_TABLES` in `backend/routers/backup.py`): a standard artifact carries only the fourteen named configuration tables and drops everything else, so these eight ship in no backup. That matters because they were shipping until then, and they hold operator data: `services.health_endpoint` is an operator URL and `incidents.created_by` is an account name. No denylist maintained by reading `models.py` could have caught them, because they are not in `models.py`. This is containment, not cleanup: the tables are still in deployed databases and the drop revision is still owed. A **legacy full-ZIP** restore does clear them from the destination as a side effect, because it replaces `journal.db` wholesale and `init_db()` then recreates only model-declared tables. The DBAS artifact restore never writes `journal.db`, so it leaves them where they are.
- No production rollback tooling beyond `alembic downgrade`. DBAS handles that tier.

## References

- Bead `bd-c5wf5` / PR #81: introduction of Alembic + baseline revision.
- `backend/alembic/env.py`: target metadata wiring and DB URL resolution.
- `backend/database.py`: `_bootstrap_alembic`, PRAGMA listener, `get_current_schema_revision`, `get_alembic_head_revision`.
- `backend/tests/unit/test_alembic_baseline.py`: drift + FK + schema-version tests.
- `docs/backend_architecture.md`: overall backend layout.
- Alembic docs: <https://alembic.sqlalchemy.org/en/latest/>.
