"""Widen task_executions.status to hold 'completed_with_warnings'.

Revision ID: 0042
Revises: 0041
Bead: enhancedchannelmanager-fexq1

A finished run's severity is now derived once
(``task_scheduler.task_outcome``) and every terminal surface maps from it,
including the persisted history row. That adds the terminal status
``completed_with_warnings`` (23 chars) to the set the column carries:
running, completed, completed_with_warnings, failed, cancelled.

``TaskExecution.status`` was declared ``String(20)`` — three chars too
short. SQLite does not enforce VARCHAR width, so rows written by the new
code are stored intact on every deployed install today, but the declared
contract would truncate or reject the value on a width-enforcing backend
(the stack is Postgres-fluent). Widen to ``String(32)``, matching what
revision 0039 did for ``auto_creation_executions.status`` when the
pipeline gained ``completed_with_errors``.

Schema-only: no row is rewritten, and rows already holding ``failed`` for
a degraded run are deliberately left alone — this migration does not
reinterpret history written by an earlier build.

SQLite cannot ALTER COLUMN in place, so the width change goes through
``op.batch_alter_table`` (table recreate) per docs/database_migrations.md.
Both directions are guarded on the reflected column width so re-running
either is a no-op.
"""
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0042"
down_revision: Union[str, Sequence[str], None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "task_executions"
COLUMN = "status"
WIDE = 32
NARROW = 20


def _status_width() -> Optional[int]:
    """Return the reflected VARCHAR length of the status column, or None.

    SQLite reflects the column as ``VARCHAR(N)``; reading N lets the
    migration stay idempotent (re-running upgrade/downgrade at the target
    width is a no-op).
    """
    for col in inspect(op.get_bind()).get_columns(TABLE):
        if col["name"] == COLUMN:
            return getattr(col["type"], "length", None)
    return None


def upgrade() -> None:
    width = _status_width()
    if width is not None and width >= WIDE:
        return
    # SQLite has no in-place ALTER COLUMN — batch mode recreates the table
    # (all three indexes on this table, incl. idx_task_exec_status, are
    # auto-reflected and recreated by batch mode).
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.alter_column(
            COLUMN,
            existing_type=sa.String(NARROW),
            type_=sa.String(WIDE),
            existing_nullable=False,
        )


def downgrade() -> None:
    width = _status_width()
    if width is not None and width <= NARROW:
        return
    # NOTE: narrowing back to String(20) is LOSSY on a width-enforcing
    # backend if any row already holds 'completed_with_warnings' (23 chars) —
    # the standard, accepted risk of reversing a widening migration. SQLite
    # ignores VARCHAR width, so existing rows are preserved as-is here; this
    # down migration only restores the declared column contract.
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.alter_column(
            COLUMN,
            existing_type=sa.String(WIDE),
            type_=sa.String(NARROW),
            existing_nullable=False,
        )
