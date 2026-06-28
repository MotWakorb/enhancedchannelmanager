"""journal_mutation_source

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-24 12:02:00.000000

Adds ``mutation_source`` (String(20), nullable) to ``journal_entries``
(enhancedchannelmanager-vp1rx / W3 — AI-mutation audit log).

``mutation_source`` records WHO/WHAT initiated a journaled change:

* ``"ui"``            — an operator acting through the web UI (JWT principal)
* ``"mcp_ai"``        — an AI agent acting through the static MCP API key
* ``"scheduler"``     — a scheduled task
* ``"auto_creation"`` — the auto-creation pipeline

It is intentionally nullable: every legacy row, and any path that does not
stamp an actor, reads back ``NULL`` (unknown). This is additive and fully
back-compatible — no existing column is touched, and ``user_initiated``
(manual-vs-automatic) keeps its independent meaning.

A single-column index ``idx_journal_mutation_source`` backs the forensic
"show me everything the AI did" filter exposed by ``GET /api/journal``.

The column is nullable, so (unlike the 0025 NOT-NULL add) there is no
``server_default`` dance — SQLite happily adds a nullable column with no
default and leaves existing rows NULL.

bd-5w6jz idempotency: a long-running install may already have the column and
index via ``Base.metadata.create_all()`` from the post-0027 ORM model (the
create_all path can win the race against ``alembic upgrade``). Both the
column-add and the index-create are guarded — if the artifact already exists
the migration skips it instead of raising ``OperationalError: duplicate column
name`` / ``index already exists``. The smoke test
``tests/integration/test_alembic_smoke.py`` (create_all + stamp/upgrade) and the
drift test ``tests/unit/test_alembic_baseline.py`` cover this.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "journal_entries"
_NEW_COLUMN = "mutation_source"
_INDEX = "idx_journal_mutation_source"


def _table_columns(connection) -> set[str]:
    """Column names on ``journal_entries`` (empty set if the table is absent)."""
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def _table_indexes(connection) -> set[str]:
    """Index names on ``journal_entries`` (empty set if the table is absent)."""
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {ix["name"] for ix in insp.get_indexes(_TABLE)}


def upgrade() -> None:
    """Add the nullable ``mutation_source`` column + its index.

    Idempotent (bd-5w6jz): if the column/index were already materialised by
    ``create_all()`` against the newer ORM snapshot, skip rather than raise.
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _table_columns(conn):
        op.add_column(
            _TABLE,
            sa.Column(_NEW_COLUMN, sa.String(length=20), nullable=True),
        )
    if _INDEX not in _table_indexes(conn):
        op.create_index(_INDEX, _TABLE, [_NEW_COLUMN], unique=False)


def downgrade() -> None:
    """Drop the index and the ``mutation_source`` column.

    Defensive (bd-5w6jz): skip each drop if the artifact is already absent so a
    partial re-run cleans up rather than raising.
    """
    conn = op.get_bind()
    if _INDEX in _table_indexes(conn):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _NEW_COLUMN in _table_columns(conn):
        op.drop_column(_TABLE, _NEW_COLUMN)
