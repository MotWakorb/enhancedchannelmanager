"""journal_automated_client

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-19 15:00:00.000000

Adds ``automated_client`` (Boolean, nullable) to ``journal_entries``
(enhancedchannelmanager-uliyr follow-up — automation marker for the Journal
Noise Purge).

``automated_client`` records whether the write came from a self-declared
automated client:

* ``True``  — the request carried the ``X-ECM-Automated-Client`` header
              (the backend E2E harness). Purgeable noise.
* ``False`` — an /api/* request WITHOUT the header (a real UI/MCP operator).
              The noise purge keeps these rows.
* ``NULL``  — legacy rows written before this marker, and non-HTTP internal
              writers (scheduler, pipelines, bandwidth tracker).

It is intentionally nullable with NO backfill: the NULL is load-bearing.
The noise purge treats unmarked ``auto_creation`` create/delete rows as the
PO's original measured legacy population (purge predicate
``automated_client IS NOT FALSE``), so the unmarked set naturally shrinks to
zero while newly written operator rows (False) are kept forever.

No index: the only consumer is the noise purge's already category/action-
narrowed scan, which ``idx_journal_category`` covers, over a table this same
purge keeps small.

Idempotency (bd-5w6jz pattern): a long-running install may already have the
column via ``Base.metadata.create_all()`` from the post-0037 ORM model (the
create_all path can win the race against ``alembic upgrade``). The column-add
is guarded — if it already exists the migration skips it instead of raising
``OperationalError: duplicate column name``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "journal_entries"
_NEW_COLUMN = "automated_client"


def _table_columns(connection) -> set[str]:
    """Column names on ``journal_entries`` (empty set if the table is absent)."""
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    """Add the nullable ``automated_client`` column.

    Idempotent (bd-5w6jz): if the column was already materialised by
    ``create_all()`` against the newer ORM snapshot, skip rather than raise.
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _table_columns(conn):
        op.add_column(
            _TABLE,
            sa.Column(_NEW_COLUMN, sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    """Drop the ``automated_client`` column.

    Defensive (bd-5w6jz): skip the drop if the column is already absent so a
    partial re-run cleans up rather than raising.
    """
    conn = op.get_bind()
    if _NEW_COLUMN in _table_columns(conn):
        op.drop_column(_TABLE, _NEW_COLUMN)
