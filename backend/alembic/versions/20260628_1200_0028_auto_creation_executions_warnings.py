"""auto_creation: add warnings column to executions

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-28 12:00:00.000000

enhancedchannelmanager-e8p1h (product footgun): an auto-creation rule can carry
``normalization_group_ids`` that point at NormalizationRuleGroup rows that are
globally DISABLED (or no longer exist). When that happens every ``[NORMALIZE]``
decision applies nothing, provider prefixes/suffixes are never stripped, and
``merge_streams target:auto`` matches almost nothing — yet the run looks clean
with no warning anywhere.

This migration adds a nullable ``warnings`` TEXT column to
``auto_creation_executions``. The engine now persists a JSON array of advisory,
non-fatal run warnings (currently: rules referencing disabled/missing
normalization groups) so the polled execution record and the executions UI can
surface them. Distinct from ``error_message`` — the run still completes; these
are config problems the operator should fix.

Backwards-compatible: the column is nullable with no default, so every existing
row reads as "no warnings" (``get_warnings()`` returns ``[]`` for NULL).

SQLite specifics: a single ``ALTER TABLE ADD COLUMN`` of a nullable TEXT column
— no batch rebuild needed.

Idempotency (mirrors 0021/bd-5w6jz): long-running installs may already have the
column via ``create_all()`` from the post-0028 ORM model. The column-add is
guarded — if it is already present the migration returns without raising
``OperationalError: duplicate column name``.

Bead: ``enhancedchannelmanager-e8p1h``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_NEW_COLUMN = "warnings"
_TABLE = "auto_creation_executions"


def _execution_columns(connection) -> set[str]:
    """Return the set of column names currently on ``auto_creation_executions``.

    Returns the empty set if the table is missing — the guard treats a missing
    table as "no column to add" so this migration becomes a no-op rather than
    raising; the next ``create_all()`` + schema-parity pass surfaces the missing
    table.
    """
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    """Add the nullable ``warnings`` TEXT column.

    Idempotent: if the column is already present (e.g. materialised by
    ``create_all()`` against a newer ORM snapshot on a long-running install),
    return without raising ``OperationalError: duplicate column name``.
    """
    conn = op.get_bind()
    if _NEW_COLUMN in _execution_columns(conn):
        return

    op.add_column(
        _TABLE,
        sa.Column(_NEW_COLUMN, sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the ``warnings`` column.

    Defensive: inspect first; skip if the column is already absent so a
    partial-rerun cleans up rather than raising. SQLite 3.35+ supports native
    ``ALTER TABLE DROP COLUMN`` via ``op.drop_column`` without a full rebuild.
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _execution_columns(conn):
        return

    op.drop_column(_TABLE, _NEW_COLUMN)
