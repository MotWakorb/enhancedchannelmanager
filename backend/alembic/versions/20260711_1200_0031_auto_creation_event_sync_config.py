"""auto_creation_event_sync_config

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-11 12:00:00.000000

Adds ``event_sync_config`` (Text, nullable) to ``auto_creation_rules``
(enhancedchannelmanager-ti939.1.3 — Event Sync Phase 1A schema).

``event_sync_config`` holds the JSON configuration of an **event_sync**
rule kind: master_group_id, secondary_group_ids[], optional parse patterns
(shared or per-group title/time/date regexes), time_window_minutes,
attach_threshold, enabled. A rule with a non-NULL value IS an event_sync
rule; NULL means a standard (pre-feature) rule — every legacy row reads
back NULL and behaves exactly as before. NO new tables (PO decision:
stateless recompute — durable state is this one config column plus journal
provenance rows; channel IDs are never persisted across runs).

The column is nullable, so (like 0027's ``mutation_source``) there is no
``server_default`` dance — SQLite adds a nullable column with no default
and leaves existing rows NULL. No index: the column is only read when a
rule row is already loaded, never used as a filter predicate.

bd-5w6jz idempotency (0027 pattern): a long-running install may already
have the column via ``Base.metadata.create_all()`` from the post-0031 ORM
model (the create_all path can win the race against ``alembic upgrade``).
The column-add is guarded — if the column already exists the migration
skips it instead of raising ``OperationalError: duplicate column name``.
The smoke test ``tests/integration/test_alembic_smoke.py`` (create_all +
stamp/upgrade) and the drift test ``tests/unit/test_alembic_baseline.py``
cover this.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "auto_creation_rules"
_NEW_COLUMN = "event_sync_config"


def _table_columns(connection) -> set[str]:
    """Column names on ``auto_creation_rules`` (empty set if the table is absent)."""
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    """Add the nullable ``event_sync_config`` column.

    Idempotent (bd-5w6jz): if the column was already materialised by
    ``create_all()`` against the newer ORM snapshot, skip rather than raise.
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _table_columns(conn):
        op.add_column(
            _TABLE,
            sa.Column(_NEW_COLUMN, sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Drop the ``event_sync_config`` column.

    Defensive (bd-5w6jz): skip the drop if the column is already absent so a
    partial re-run cleans up rather than raising.
    """
    conn = op.get_bind()
    if _NEW_COLUMN in _table_columns(conn):
        op.drop_column(_TABLE, _NEW_COLUMN)
