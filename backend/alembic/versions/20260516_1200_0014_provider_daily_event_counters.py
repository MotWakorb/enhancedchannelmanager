"""session_telemetry_provider_daily: add reconnect/error/switch event counters

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-16 12:00:00.000000

The nightly rollup task (``tasks.stats_v2_rollup._rollup_provider_daily``)
already aggregates ``buffer_event_count`` from ``session_telemetry`` into
``session_telemetry_provider_daily``. Migration 0013 (bd-ov5vb) added three
companion per-type counters to the raw ``session_telemetry`` table:

  * ``reconnect_event_count``
  * ``error_event_count``
  * ``switch_event_count``

…but the rollup INSERT projection was not updated, so those counters are
silently dropped at rollup time. Raw rows are pruned at 30 days (ADR-007 D1),
which means beyond the 30-day window the three new counters become permanently
unrecoverable from the rolled-up data.

This migration fixes the gap by adding the three missing columns to the rollup
table, after which the companion update in ``_rollup_provider_daily`` (bd-d0ha9)
will SUM them alongside ``buffer_event_count``. (bd-d0ha9)

SQLite specifics:

* Three independent ``ALTER TABLE ADD COLUMN`` statements. Each is a simple
  NOT NULL DEFAULT 0 column add; SQLite populates every existing row inline
  without a full table rebuild.
* Existing rollup rows gain DEFAULT 0 for the three new columns — technically
  under-counted (the real counts were never rolled up for those dates), but
  a truthful posture: those counters were not tracked in the rollup at that
  time. Operators who need historical accuracy for those counters can re-run
  the rollup task for any day still within the 30-day raw-row retention
  window (the task is idempotent; re-running a day updates in place via
  INSERT OR REPLACE).

bd-5w6jz idempotency: long-running installs may already have one or more of
the three columns via ``create_all()`` from the post-0014 ORM model. Each
column-add is independently guarded — present columns are skipped, absent
columns get added. If all three are already present the migration returns
immediately.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0014``
covers fresh up, fresh down, full idempotency (all three columns already
present), and partial idempotency (subset of columns present).

Bead: ``enhancedchannelmanager-d0ha9``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# The three new per-event-type counters added in this migration.
_NEW_COLUMNS = (
    "reconnect_event_count",
    "error_event_count",
    "switch_event_count",
)


def _provider_daily_columns(connection) -> set[str]:
    """Return the set of column names currently on
    ``session_telemetry_provider_daily``.

    Returns the empty set if the table is missing — the bd-5w6jz guard
    treats a missing table as "no columns to add" so this migration
    becomes a no-op rather than raising.
    """
    insp = inspect(connection)
    if not insp.has_table("session_telemetry_provider_daily"):
        return set()
    return {c["name"] for c in insp.get_columns("session_telemetry_provider_daily")}


def upgrade() -> None:
    """Add three per-type event counters to ``session_telemetry_provider_daily``.

    Each ADD COLUMN is independently guarded so a partial-drift state
    (one column already present from a previous interrupted run, or columns
    already added via ``create_all()`` against a newer ORM snapshot) cleans
    up rather than raising ``OperationalError: duplicate column name``.

    Idempotent (bd-5w6jz): if all three columns are already present,
    return without paying even the inspect-then-skip cost.
    """
    conn = op.get_bind()
    existing = _provider_daily_columns(conn)
    missing = [c for c in _NEW_COLUMNS if c not in existing]

    if not missing:
        # Fully drifted forward — every target column already exists.
        return

    for column_name in missing:
        # NOT NULL DEFAULT 0 — SQLite populates every existing row inline.
        # ``server_default`` mirrors the ORM declaration in models.py so
        # the schema matches the model exactly.
        op.add_column(
            "session_telemetry_provider_daily",
            sa.Column(
                column_name,
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    """Drop the three per-type event counters from
    ``session_telemetry_provider_daily``.

    Defensive (bd-5w6jz): inspect first; skip whichever column(s) are
    already absent so a partial-rerun cleans up rather than raising.
    """
    conn = op.get_bind()
    existing = _provider_daily_columns(conn)
    present = [c for c in _NEW_COLUMNS if c in existing]

    if not present:
        return

    for column_name in present:
        op.drop_column("session_telemetry_provider_daily", column_name)
