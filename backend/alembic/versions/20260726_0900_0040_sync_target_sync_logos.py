"""sync_targets.sync_logos opt-in column (v0.18.1 cross-instance sync logo slice)

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-26 09:00:00.000000

Additive, reversible extension of ``sync_targets`` for the v0.18.1 entity-slice
sync (bead enhancedchannelmanager-7ipq2.1, epic 7ipq2; ADR-013 S9 exit path).

One column is added:

* ``sync_logos`` BOOLEAN NOT NULL server_default='0' — per-target OPT-IN flag
  to include the LOGO replication slice in this target's sync cycles. Default
  False keeps logos out of the unconditional per-cycle category set exactly as
  ADR-013 S9 ratified; an operator opts a target in explicitly. The sync logos
  step is never destructive (``clear_existing`` hard-disabled) and streams
  missed logos one at a time (D8).

The NOT-NULL add REQUIRES a server_default: ``sync_targets`` may already hold
live rows, and SQLite cannot add a NOT NULL column without a default to a
populated table (docs/database_migrations.md — DDL with a server_default, NOT a
data-only migration the smart-bootstrap stamp-skip would skip). Existing rows
backfill to False. Mirrors the ``fuzzy_stream_matching`` add in 0024.

Idempotency (house pattern, see 0024): the ADD COLUMN is guarded by a
column-presence check, so a DB that drifted forward via ``create_all()`` passes
through as a no-op rather than raising ``OperationalError: duplicate column``.

Reversibility: ``downgrade()`` drops the column via ``batch_alter_table``
(SQLite cannot DROP COLUMN without table recreation). Dropping it loses only
the per-target logo opt-in; target rows and credentials are preserved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0040"
down_revision: Union[str, Sequence[str], None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_SYNC_TABLE = "sync_targets"
_NEW_COLUMN = "sync_logos"


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _column_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    if not existing:
        # Table absent (fresh DB materialises the full ORM shape via
        # create_all() and stamps head). Nothing to extend.
        return
    if _NEW_COLUMN in existing:
        # Drift-forward no-op (column already materialised by create_all()).
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        # server_default='0' backfills existing rows; required because the
        # table may already hold live targets (NOT NULL add without a default
        # fails on a populated SQLite table).
        batch.add_column(
            sa.Column(
                _NEW_COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    """Reverse 0040: drop sync_logos (defensive — skip when absent)."""
    conn = op.get_bind()
    if _NEW_COLUMN not in _column_names(conn, _SYNC_TABLE):
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        batch.drop_column(_NEW_COLUMN)
