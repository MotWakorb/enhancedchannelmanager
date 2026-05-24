"""auto_creation: add channels_touched to executions

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-24 04:30:00.000000

bd-0emgo.4 (production auto-creation reporting bug): a dry-run reported
``streams_merged=26`` but ``channels_touched=0``. Root cause was a missing
persistence column, not a missing computation. The engine computes
``results["channels_touched"]`` correctly (distinct channels that received at
least one stream merge), but the finalize block had no column to write it to,
so the value was dropped on save. The MCP/API surfaces read the *persisted*
execution record (``AutoCreationExecution.to_dict()``) — which had every other
counter column but not this one — so they reported the default 0 while
``streams_merged`` (which does have a column) showed the real 26.

This migration adds the NOT NULL ``channels_touched`` integer column to
``auto_creation_executions`` (default 0). The engine now writes
``execution.channels_touched = results["channels_touched"]`` and ``to_dict()``
exposes it, so polled dry-run and live runs report it honestly.

Backwards-compatible: defaults to 0 for every existing row. The server_default
is supplied so the NOT NULL add succeeds on tables that already have rows; the
ORM model declares ``default=0`` to match. The schema-drift test in
``tests/unit/test_alembic_baseline.py`` filters ``modify_default`` /
``modify_nullable`` noise, so the server_default vs. app-default difference is
not flagged as structural drift.

SQLite specifics:

* A single ``ALTER TABLE ADD COLUMN`` with a server_default. SQLite supports a
  NOT NULL add when a default is provided — every existing row gets 0 inline,
  no batch rebuild needed.

bd-5w6jz idempotency: long-running installs may already have the column via
``create_all()`` from the post-0021 ORM model. The column-add is guarded — if
the column is already present the migration returns immediately without raising
``OperationalError: duplicate column name``.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py`` covers the
baseline round-trip; ``tests/unit/test_alembic_baseline.py`` locks ORM/migration
parity on the next boot.

Bead: ``enhancedchannelmanager-0emgo.4``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_NEW_COLUMN = "channels_touched"
_TABLE = "auto_creation_executions"


def _execution_columns(connection) -> set[str]:
    """Return the set of column names currently on ``auto_creation_executions``.

    Returns the empty set if the table is missing — the bd-5w6jz guard treats
    a missing table as "no column to add" so this migration becomes a no-op
    rather than raising; the next ``create_all()`` + schema-parity pass
    surfaces the missing table.
    """
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    """Add the NOT NULL ``channels_touched`` column (default 0).

    Idempotent (bd-5w6jz): if the column is already present (e.g. materialised
    by ``create_all()`` against a newer ORM snapshot on a long-running install),
    return without raising ``OperationalError: duplicate column name``.
    """
    conn = op.get_bind()
    if _NEW_COLUMN in _execution_columns(conn):
        return

    # server_default="0" so the NOT NULL add succeeds on tables with existing
    # rows (every row gets 0 inline). The ORM declares default=0; the drift
    # test filters modify_default noise.
    op.add_column(
        _TABLE,
        sa.Column(
            _NEW_COLUMN,
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    """Drop the ``channels_touched`` column.

    Defensive (bd-5w6jz): inspect first; skip if the column is already absent
    so a partial-rerun cleans up rather than raising. SQLite 3.35+ supports
    native ``ALTER TABLE DROP COLUMN`` via ``op.drop_column`` without a full
    table rebuild.

    Operators who downgrade past this point lose the persisted distinct-channel
    count; runs still complete, but the polled record reports 0 again.
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _execution_columns(conn):
        return

    op.drop_column(_TABLE, _NEW_COLUMN)
