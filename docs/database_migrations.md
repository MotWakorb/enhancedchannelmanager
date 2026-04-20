# Database Migrations

Enhanced Channel Manager uses [Alembic](https://alembic.sqlalchemy.org/) on top of SQLAlchemy to version and evolve the SQLite schema at `/config/journal.db`. The baseline revision (`0001`) was introduced in bead `bd-c5wf5` to unblock DBAS restore/sync (`bd-gb5r5.3`, `bd-gb5r5.4`), which must be able to gate on a known schema version before importing a backup.

This document is the authoring guide for every schema change that lands in ECM.

## Layout

```
backend/
  alembic.ini                   # Alembic config; points script_location at alembic/
  alembic/
    env.py                      # Loads ECM metadata + runtime DB URL
    script.py.mako              # Template for new revision files
    versions/                   # One .py per migration, committed to git
      20260420_2034_0001_baseline_initial_schema.py
  database.py                   # init_db() → _bootstrap_alembic() → upgrade/stamp
```

Filename convention: `YYYYMMDD_HHMM_<revid>_<slug>.py`, sourced from the `file_template` setting in `alembic.ini`. Keep revision IDs sequential (`0001`, `0002`, ...) so `alembic history` reads chronologically.

## Runtime behaviour

`init_db()` (called on app startup from `main.py`) hands control to `_bootstrap_alembic()`:

| Situation | Action |
|-|-|
| Fresh install, empty DB | `alembic upgrade head` creates every table |
| Pre-Alembic install (existing DB, no `alembic_version` row) | `alembic stamp head` records the revision without re-running DDL |
| Existing Alembic install at head | `upgrade head` is a no-op |
| Existing Alembic install behind head | `upgrade head` applies pending revisions in order |

Both the app and the test suite depend on the `PRAGMA foreign_keys=ON` / `PRAGMA journal_mode=WAL` connect-listener registered in `database.py`. Alembic env.py inherits those PRAGMAs automatically because the listener attaches to the SQLAlchemy `Engine` class (see `docs/backend_architecture.md` for the operational rationale).

Operators can read the applied revision at any time:

```bash
curl -s http://localhost:6100/api/health/schema
# {"current_revision":"0001","head_revision":"0001","up_to_date":true,
#  "foreign_keys_enabled":true,"journal_mode":"wal"}
```

## Authoring a migration

### 1. Make the model change

Edit `backend/models.py` (or `export_models.py`, or `ffmpeg_builder/persistence.py`) the same way you would today. Do **not** try to write the migration first — autogenerate needs your intent encoded in the ORM.

### 2. Generate a revision

From inside the container (Alembic is installed via `backend/requirements.in`):

```bash
docker exec ecm-ecm-1 sh -c "cd /app && alembic revision --autogenerate -m 'short_imperative_message'"
```

Copy the new file back to the repo:

```bash
docker cp ecm-ecm-1:/app/alembic/versions/<new-filename>.py backend/alembic/versions/
```

### 3. Hand-review the autogenerate output

Autogenerate is a starting point, not a deliverable. It routinely misses:

- **Indexes** declared outside `Column(index=True)` (e.g., composite indexes via `Index(...)` in `__table_args__`).
- **Check constraints** that rely on database functions.
- **Server defaults** when the Python default doesn't match the generated SQL default.
- **SQLite batch semantics**: SQLite cannot drop/alter columns directly. Any column removal or type change must use `op.batch_alter_table(...)`. Alembic emits batch mode when `render_as_batch=True`, but when hand-editing a migration you must wrap the ops yourself.
- **Data migrations**: autogenerate never writes DML. If the schema change requires backfill, add an explicit `op.execute(...)` or a loop against `op.get_bind()`.

Open the generated file and make sure each `upgrade()` step has a mirroring `downgrade()` step. If a change is genuinely irreversible (dropping a table with data, for example), leave an explicit `raise NotImplementedError("cannot downgrade…")` and document why in the docstring.

### 4. Test locally

```bash
# Fresh DB, upgrade from scratch
docker exec ecm-ecm-1 sh -c "rm -f /tmp/mig_test.db && cd /app && CONFIG_DIR=/tmp/mig_test alembic upgrade head"

# Apply to a copy of the current prod DB
docker exec ecm-ecm-1 sh -c "cp /config/journal.db /tmp/prod_copy.db && cd /app && ALEMBIC_DATABASE_URL=sqlite:////tmp/prod_copy.db alembic upgrade head"

# Backend test suite — the baseline-drift test will flag unmatched metadata
cd backend && python -m pytest tests/unit/test_alembic_baseline.py -v
```

If `test_baseline_matches_metadata_no_drift` fails, autogenerate missed something or your migration under-specifies the target schema. Re-run autogenerate or tune the migration until the test passes.

### 5. Deploy

Standard container-first workflow (see root `CLAUDE.md`):

```bash
docker cp backend/alembic/versions/<new-filename>.py ecm-ecm-1:/app/alembic/versions/
docker cp backend/models.py ecm-ecm-1:/app/models.py
docker restart ecm-ecm-1
```

Startup will apply the new revision automatically. Check the logs for `Running upgrade <prev> -> <new>, <message>`.

## Rollback

Alembic supports `alembic downgrade -1` or `alembic downgrade <rev>`. Two ground rules:

- **Only roll back if `downgrade()` is non-destructive**. If the up migration dropped data or a column, the down migration cannot restore it — never fake a reversible migration with `pass`.
- **Test the rollback in the same way you test the upgrade**. A down migration that's never been exercised is as dangerous as a backup that's never been restored.

For production incidents where Alembic rollback is unsafe, the DBAS restore flow (bead `bd-gb5r5.3`) is the escape hatch: restore a ZIP backup from before the bad migration, then stamp to that revision.

## SQLite-specific gotchas

- SQLite cannot `ALTER TABLE ... DROP COLUMN` or `ALTER COLUMN` in older versions. Use `op.batch_alter_table(...)` which recreates the table under the hood.
- `CHECK` constraint names are unstable across SQLite versions — let Alembic name them explicitly via `sa.CheckConstraint(..., name="ck_foo")` rather than relying on auto-generation.
- Foreign keys are only enforced when `PRAGMA foreign_keys=ON`. The app-wide connect listener in `database.py` handles this, but if you run raw `sqlite3` in a shell for debugging, you must set the PRAGMA yourself — raw `sqlite3` does **not** inherit ECM's engine listener.
- `PRAGMA journal_mode=WAL` is set per connection. The listener sets it on every connect, but direct `sqlite3.connect()` calls bypass the listener and will use `delete` mode. This is expected and harmless — operations under WAL or delete mode see the same committed state.

## What this bead did NOT do

- Retroactive per-column migrations for historical schema changes. The 30+ ad-hoc `_add_*` functions in `database.py` predate Alembic; they continue to run after `_bootstrap_alembic()` for installs that already crossed those versions. New columns should land as Alembic revisions, not new helpers in `database.py`.
- Cleanup of legacy orphan tables in some live DBs (`services`, `health_checks`, `incidents`, etc. from a pre-v0.13 health-monitor subsystem). Those tables exist in deployed `journal.db` files but are not in any current model. A future bead should write a revision to drop them — stamping them into the baseline would have forced the revision to carry tables no code references.
- Production rollback tooling beyond `alembic downgrade`. DBAS handles that.

## References

- Bead `bd-c5wf5`: introduction of Alembic + baseline revision.
- `docs/backend_architecture.md`: overall backend layout.
- Alembic docs: <https://alembic.sqlalchemy.org/en/latest/>.
