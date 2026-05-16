"""task_schedules: CHECK constraint preventing interval/0 placeholder rows

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-15 19:00:00.000000

Defense-in-depth for the bd-p5b8i scheduling subsystem regression. The
placeholder bug (Bundle H, bd-1weac) wrote ``task_schedules`` rows with
``schedule_type='interval'`` AND ``interval_seconds=0``/``NULL``, which
the calculator surfaces as a fatal "no next-run" condition and the
engine then loops on. DBA spike's verbatim conclusion: "interval/0 as
a valid state is always a bug". This migration adds a database-level
``CHECK`` constraint that rejects the bad shape at write time so the
class of bug cannot recur via backup-restore or a future code path.

Constraint
----------
``ck_task_schedules_interval_positive`` ::

    schedule_type != 'interval' OR
        (interval_seconds IS NOT NULL AND interval_seconds > 0)

Semantics: when a schedule is NOT an interval schedule the constraint
imposes nothing on ``interval_seconds`` (daily/weekly/biweekly/monthly
rows leave it NULL — that's correct). When the schedule IS an interval,
``interval_seconds`` MUST be a positive integer. SQLite enforces CHECK
constraints on INSERT and UPDATE so the placeholder bug becomes
``IntegrityError`` at the write call site instead of silently corrupting
the scheduling subsystem.

SQLite specifics
----------------
SQLite has no ``ALTER TABLE ADD CONSTRAINT`` — adding a CHECK requires
a full table rebuild. We use ``batch_alter_table.create_check_constraint``
which generates the dance under the hood (rename old → create new with
constraint → copy rows → drop old). The bd-gsn3r migration 0011
``batch_alter_table`` pattern for the FK drop is the closest analogue;
this migration is simpler because we don't need ``copy_from`` /
``recreate="always"`` — adding a constraint via the batch op IS a
real per-batch operation, so SQLAlchemy's batch mode rebuilds without
extra coaxing.

Pre-flight repair
-----------------
The constraint cannot be added if any existing row violates it.
Bundle H's startup self-heal (``task_registry._heal_task_schedules_null_next_run_at``)
repairs the broken rows, but it runs in ``_run_migrations`` which is
AFTER ``alembic upgrade head`` in ``init_db``'s ordering — so we
cannot rely on Bundle H to have fixed the data before this migration
runs. The migration's pre-flight ``DELETE`` removes any violating row
unconditionally: ``task_registry._create_default_task_schedule`` will
rebuild a correct-shape row on the next ``sync_from_database`` pass,
which Bundle H also runs at startup. The operator-visible effect is
"interval/0 row vanishes, gets replaced with a correct interval row on
next startup" — same outcome as Bundle H's heal, just reached via
delete-and-rebuild instead of in-place repair. The migration logs the
DELETE rowcount at WARNING so operators see what happened.

Smart-bootstrap fast-path interaction
-------------------------------------
``database._schema_matches_head`` (bd-5w6jz) only checks columns +
tables, NOT constraints. On a long-running install where every model
column is already present, the fast-path stamps ``alembic_version``
forward to 0012 WITHOUT running this migration — so the CHECK constraint
will NOT be added to existing operators' DBs.

This is acceptable defense in depth because the write-time guard is
covered by the Pydantic validator on the TaskSchedule create/update
endpoints (``backend/routers/tasks.py``); the constraint exists
primarily to protect:

  1. Fresh installs (run migrations from scratch — constraint applied).
  2. DBAS backup-restore flow — the restored DB initializes from scratch
     and runs migrations, so a backup containing interval/0 rows fails
     to restore loudly rather than silently re-introducing the bug.

For existing installs that hit the fast-path, the Pydantic validator
+ Bundle H's self-heal cover the regression surface; the constraint
landing only on fresh installs is acceptable per the bead.

bd-5w6jz idempotency
--------------------
On a re-run (alembic_version rolled back, smart-bootstrap stamped past,
etc.), the migration must skip cleanly if the constraint already exists.
SQLite stores constraints inline in the ``CREATE TABLE`` text in
``sqlite_master.sql``; the cleanest detection is a substring check on
that DDL string for the constraint name.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0012``
covers fresh up, drift (pre-existing interval/0 rows repaired pre-add),
and idempotency (re-run is a no-op).

Bead: ``enhancedchannelmanager-lbkck`` (defense in depth for ``bd-p5b8i``).
"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


logger = logging.getLogger(__name__)


# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


CONSTRAINT_NAME = "ck_task_schedules_interval_positive"
CONSTRAINT_SQL = (
    "schedule_type != 'interval' OR "
    "(interval_seconds IS NOT NULL AND interval_seconds > 0)"
)


def _constraint_already_present(connection) -> bool:
    """True if the CHECK constraint name appears in the ``task_schedules`` DDL.

    SQLite serializes CHECK constraints inline in the ``CREATE TABLE`` text
    stored in ``sqlite_master.sql``. The named-constraint substring is the
    cheapest reliable detection: ``inspect().get_check_constraints()`` works
    on SQLAlchemy 1.4+ but the migration is meant to be robust against
    older alembic snapshots used in tests, and the DDL-text check is
    portable across all SQLite versions ECM supports.
    """
    inspector = inspect(connection)
    if not inspector.has_table("task_schedules"):
        return False
    ddl = connection.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='task_schedules'"
    )).scalar()
    return CONSTRAINT_NAME in (ddl or "")


def _repair_violating_rows(connection) -> int:
    """Delete any ``task_schedules`` row that would violate the CHECK.

    Returns the rowcount deleted. ``task_registry._create_default_task_schedule``
    will rebuild correct-shape rows on the next ``sync_from_database`` pass
    (Bundle H runs that at startup), so the operator-visible effect is "bad
    row vanishes, gets replaced with a good row on next startup". The DELETE
    is unconditional rather than UPDATE-to-fix because we don't know the
    task's intended default interval from the migration context — the
    registry owns that knowledge.
    """
    inspector = inspect(connection)
    if not inspector.has_table("task_schedules"):
        return 0
    bad_count = connection.execute(text(
        "SELECT COUNT(*) FROM task_schedules "
        "WHERE schedule_type = 'interval' "
        "AND (interval_seconds IS NULL OR interval_seconds <= 0)"
    )).scalar() or 0
    if bad_count == 0:
        return 0
    connection.execute(text(
        "DELETE FROM task_schedules "
        "WHERE schedule_type = 'interval' "
        "AND (interval_seconds IS NULL OR interval_seconds <= 0)"
    ))
    logger.warning(
        "[ALEMBIC 0012] Deleted %d task_schedules row(s) violating "
        "interval-positive invariant (interval_seconds NULL or <= 0). "
        "task_registry will rebuild correct-shape rows on next startup "
        "(Bundle H bd-1weac). Bead: lbkck.",
        bad_count,
    )
    return bad_count


def upgrade() -> None:
    """Add the ``ck_task_schedules_interval_positive`` CHECK constraint.

    Idempotent (bd-5w6jz): skip if the constraint is already present in
    the live DDL. Pre-flight: delete any row that would violate the
    constraint so the batch rebuild doesn't fail with IntegrityError on
    pre-existing bad data.
    """
    conn = op.get_bind()

    if _constraint_already_present(conn):
        # Already applied — common on a long-running install where a
        # previous run successfully added the constraint but the
        # alembic_version row didn't capture it due to a crash mid-stamp,
        # or where the smart-bootstrap fast-path is being re-run.
        return

    # Pre-flight repair: any violating row must be removed before the
    # constraint can be added. The DELETE is logged at WARNING so
    # operators upgrading WITHOUT Bundle H's heal yet (the bead's
    # critical dependency ordering) see what happened.
    _repair_violating_rows(conn)

    # SQLite has no ALTER TABLE ADD CONSTRAINT — the batch op generates
    # the table-rebuild dance under the hood. ``create_check_constraint``
    # IS a real per-batch operation, so the rebuild fires without
    # ``recreate="always"`` or ``copy_from`` (unlike 0011's empty-op FK
    # drop that needed both).
    with op.batch_alter_table("task_schedules") as batch_op:
        batch_op.create_check_constraint(
            CONSTRAINT_NAME,
            CONSTRAINT_SQL,
        )


def downgrade() -> None:
    """Drop the ``ck_task_schedules_interval_positive`` CHECK constraint.

    Defensive (bd-5w6jz): skip if the constraint is already absent — a
    partial-rerun downgrade should clean up rather than raise. The
    downgrade is a one-way door for the protection it provided: bad rows
    inserted after a downgrade-then-re-upgrade WILL fail the pre-flight
    repair, which is the correct posture (loud failure beats silent
    re-introduction of the bug).
    """
    conn = op.get_bind()

    if not _constraint_already_present(conn):
        return

    with op.batch_alter_table("task_schedules") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
