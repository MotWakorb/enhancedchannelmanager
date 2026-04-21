# Database Migrations

Enhanced Channel Manager uses [Alembic](https://alembic.sqlalchemy.org/) on top of SQLAlchemy to version and evolve the SQLite schema at `/config/journal.db`. The baseline revision (`0001`) was introduced in bead `bd-c5wf5` (PR #81, commit `f996ec9b`, 2026-04-20) to unblock DBAS restore/sync (`bd-gb5r5.3`, `bd-gb5r5.4`), which must be able to gate on a known schema version before importing a backup.

This document is the authoring guide for every schema change that lands in ECM. It is a lean first-pass — expect to fill in gotchas as the next few revisions land.

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

Operators can read the applied revision at any time:

```bash
curl -s http://localhost:6100/api/health/schema
# {"current_revision":"0001","head_revision":"0001","up_to_date":true,
#  "foreign_keys_enabled":true,"journal_mode":"wal"}
```

## Authoring a migration

### 1. Make the model change

Edit `backend/models.py` (or `export_models.py`, or `ffmpeg_builder/persistence.py`) the same way you would today. Do **not** write the migration first — autogenerate needs your intent encoded in the ORM.

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

Then fill in `upgrade()` / `downgrade()` directly — no autogenerate, no metadata comparison.

### 3. Hand-review the output

Autogenerate is a starting point, not a deliverable. It routinely misses:

- **Indexes** declared outside `Column(index=True)` (e.g., composite indexes via `Index(...)` in `__table_args__`).
- **Check constraints** that rely on database functions.
- **Server defaults** when the Python default doesn't match the generated SQL default.
- **SQLite batch semantics** — see [SQLite gotchas](#sqlite-specific-gotchas).
- **Data migrations** — autogenerate never writes DML. If the schema change requires backfill, add an explicit `op.execute(...)` or a loop against `op.get_bind()`.

Open the generated file and make sure each `upgrade()` step has a mirroring `downgrade()` step. If a change is genuinely irreversible (dropping a table with data, for example), leave an explicit `raise NotImplementedError("cannot downgrade: ...")` and document why in the docstring — never fake a reversible migration with `pass`.

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

- **Only roll back if `downgrade()` is genuinely non-destructive**. If the up migration dropped data or a column, the down migration cannot restore it — that information is gone.
- **Test the rollback the same way you test the upgrade** (see the round-trip command above). A down migration that has never been exercised is as dangerous as a backup that has never been restored.

For production incidents where Alembic rollback is unsafe, the DBAS restore flow (`bd-gb5r5.3`) is the escape hatch: restore a ZIP backup from before the bad migration, then stamp to that revision:

```bash
docker exec ecm-ecm-1 sh -c "cd /app && alembic stamp <known-good-rev>"
```

`stamp` rewrites only the `alembic_version` row — no DDL is run. Use it when you've restored a DB whose schema is already at the target revision but whose `alembic_version` is missing or wrong.

## SQLite-specific gotchas

- **`ALTER TABLE` is limited.** SQLite cannot `DROP COLUMN` or `ALTER COLUMN` in older versions. Any column removal or type change must use `op.batch_alter_table(...)`, which recreates the table under the hood. Alembic emits batch mode when `render_as_batch=True`; when hand-editing a migration you must wrap the ops yourself.
- **Name your constraints explicitly.** `CHECK` and `UNIQUE` constraint names are unstable across SQLite versions — pass `name=...` to `sa.CheckConstraint` / `sa.UniqueConstraint` rather than relying on auto-generated names.
- **Foreign keys are per-connection.** They are only enforced when `PRAGMA foreign_keys=ON`. The app-wide connect listener in `database.py` handles this everywhere SQLAlchemy opens a connection (including Alembic via `env.py`), but if you run raw `sqlite3` in a shell for debugging, you must set the PRAGMA yourself. Raw `sqlite3.connect()` calls bypass the listener.
- **WAL journal mode is per-connection too.** The listener sets it on every connect, but direct `sqlite3` CLI sessions will use `delete` mode. This is expected and harmless — both modes see the same committed state.

## What bead `bd-c5wf5` did NOT do

- No retroactive per-column migrations for historical schema changes. The 30+ ad-hoc `_add_*` functions in `database.py` predate Alembic; they continue to run after `_bootstrap_alembic()` for installs that already crossed those versions. **New columns should land as Alembic revisions**, not new helpers in `database.py`.
- No cleanup of legacy orphan tables in some live DBs (`services`, `health_checks`, `incidents`, etc. from the pre-v0.13 health-monitor subsystem). Those tables exist in deployed `journal.db` files but are not in any current model. A future bead should write a revision to drop them.
- No production rollback tooling beyond `alembic downgrade`. DBAS handles that tier.

## References

- Bead `bd-c5wf5` / PR #81: introduction of Alembic + baseline revision.
- `backend/alembic/env.py`: target metadata wiring and DB URL resolution.
- `backend/database.py`: `_bootstrap_alembic`, PRAGMA listener, `get_current_schema_revision`, `get_alembic_head_revision`.
- `backend/tests/unit/test_alembic_baseline.py`: drift + FK + schema-version tests.
- `docs/backend_architecture.md`: overall backend layout.
- Alembic docs: <https://alembic.sqlalchemy.org/en/latest/>.
