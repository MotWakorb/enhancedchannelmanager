"""Widen auto_creation_executions.status to hold 'completed_with_errors'.

Revision ID: 0039
Revises: 0038
Bead: enhancedchannelmanager-y3m6o.1 (GitHub #720)

Build 0.17.6-0152 introduced the terminal pipeline-run status
``completed_with_errors`` (21 chars). The persisted column
``ChannelPipelineExecution.status`` (table ``auto_creation_executions``)
was declared ``String(20)`` — one char too short. SQLite does not
enforce VARCHAR width so deployed rows are fine today, but the contract
is wrong: a width-enforcing backend (the stack is Postgres-fluent) would
truncate or reject the value. Widen to ``String(32)``, which comfortably
covers the current allowed set (pending, running, completed, failed,
rolled_back, capped, completed_with_errors, abandoned) plus near-future
statuses. ``abandoned`` is a PRE-EXISTING value (GH #473 / bd-exo4j —
task_engine crash-reconciliation transitions 'running' -> 'abandoned'), not
introduced by this migration; it fits String(20) but is listed here for a
complete terminal-set record.

SQLite cannot ALTER COLUMN in place, so the width change goes through
``op.batch_alter_table`` (table recreate) per docs/database_migrations.md.
Both directions are guarded on the reflected column width so re-running
either is a no-op.
"""
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0039"
down_revision: Union[str, Sequence[str], None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "auto_creation_executions"
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
    # (indexes on this table, incl. idx_auto_exec_status, are auto-reflected
    # and recreated by batch mode).
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
    # backend if any row already holds 'completed_with_errors' (21 chars) —
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
