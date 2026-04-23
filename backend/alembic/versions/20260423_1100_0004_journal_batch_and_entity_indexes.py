"""journal_batch_and_entity_indexes

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-23 11:00:00.000000

Adds two indexes to ``journal_entries`` to back forensic queries that
became hot paths after bd-91mcq landed per-entity journal rows for
bulk auto-creation operations:

* ``idx_journal_batch_id`` — single-column on ``batch_id``. Powers
  "show me batch X" lookups. Without this index, the query full-scans
  the table. Bulk auto-creation amplifies row growth N-fold per run,
  so scan cost was growing with table size.
* ``idx_journal_entity`` — composite on ``(category, entity_id,
  timestamp DESC)``. Powers "history for this entity" lookups
  (``WHERE category=? AND entity_id=? ORDER BY timestamp DESC``).
  Leading columns are the equality filters; trailing ``timestamp DESC``
  lets SQLite serve the order-by from the index without a separate
  sort step.

Both indexes are non-unique. Reversal drops both.

DB-engineer note: SQLite ``CREATE INDEX`` is not concurrent (no
``CONCURRENTLY`` clause exists in the SQLite dialect), and the
journal_entries table is a write-light, append-mostly workload, so
build cost is acceptable. Postgres would need ``CONCURRENTLY`` if
this project ever ports off SQLite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add idx_journal_batch_id and idx_journal_entity to journal_entries."""
    with op.batch_alter_table('journal_entries', schema=None) as batch_op:
        batch_op.create_index(
            'idx_journal_batch_id',
            ['batch_id'],
            unique=False,
        )
        # The descending sort on ``timestamp`` is encoded via literal_column
        # (matching the baseline's idx_journal_timestamp pattern) so SQLAlchemy
        # emits ``CREATE INDEX ... (category, entity_id, timestamp DESC)``.
        batch_op.create_index(
            'idx_journal_entity',
            ['category', 'entity_id', sa.literal_column('timestamp DESC')],
            unique=False,
        )


def downgrade() -> None:
    """Drop both indexes added in ``upgrade()``."""
    with op.batch_alter_table('journal_entries', schema=None) as batch_op:
        batch_op.drop_index('idx_journal_entity')
        batch_op.drop_index('idx_journal_batch_id')
