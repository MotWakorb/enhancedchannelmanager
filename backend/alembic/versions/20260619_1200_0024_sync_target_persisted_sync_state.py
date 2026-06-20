"""sync_targets persisted-sync-state columns (v0.18.1 cross-instance live sync)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-19 12:00:00.000000

Additive, reversible extension of the existing ``sync_targets`` table (created
in 0023) for the v0.18.1 cross-instance SyncTarget live-sync feature
(bead enhancedchannelmanager-vigbu, epic i39wu; SPIKE xp6mp DBA Ruling 2).

Four columns are added:

* ``last_full_sync_at`` DATETIME NULL — timestamp of the last completed full
  sync run. NULL == never synced.
* ``last_outcome`` VARCHAR(40) NULL — outcome of the last sync run
  ("success"/"failed"/...). NULL == never synced.
* ``last_source_fingerprint`` TEXT NULL — fingerprint of the source config the
  last run pushed, for idempotent skip-if-unchanged. NULL == never synced.
* ``fuzzy_stream_matching`` BOOLEAN NOT NULL server_default='0' — opt-in flag
  to include the stream floor and fuzzy-match streams on the remote.

The three bookkeeping columns are NULLABLE, so a NULL backfill is the correct
"never synced" sentinel and NO server_default is needed. ``fuzzy_stream_matching``
is the one NOT-NULL add and therefore REQUIRES a server_default: this table may
already hold live rows in installs that configured a sync target via the model,
and SQLite cannot add a NOT NULL column without a default to a populated table
(docs/database_migrations.md — this is DDL with a server_default, NOT a
data-only migration that the smart-bootstrap stamp-skip would skip). Existing
rows backfill to False (exact stream matching).

Idempotency (house pattern, see 0023): every ADD COLUMN is guarded by a
column-presence check, so a DB that drifted forward via ``create_all()`` (ORM
shape materialised while ``alembic_version`` was still at 0023) passes through
as a no-op rather than raising ``OperationalError: duplicate column``.

Reversibility: ``downgrade()`` drops the four added columns (via
``batch_alter_table`` — SQLite cannot DROP COLUMN without table recreation).
Dropping them loses only the per-target sync bookkeeping; the target rows and
their encrypted credentials are preserved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_SYNC_TABLE = "sync_targets"

_NEW_COLUMNS = (
    "last_full_sync_at",
    "last_outcome",
    "last_source_fingerprint",
    "fuzzy_stream_matching",
)


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _column_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def _add_sync_state_columns_if_absent(conn) -> None:
    """Add the four persisted-sync-state columns to sync_targets.

    Each column is guarded independently so a half-applied prior run (some
    columns present, some not) finishes cleanly. ``batch_alter_table`` recreates
    the table under the hood, which is how SQLite handles ADD COLUMN with a
    server_default cleanly and keeps the op reversible. Skips entirely if the
    table itself is absent (a fresh DB materialises the full ORM shape via
    ``create_all()`` and stamps head; nothing to add).
    """
    existing = _column_names(conn, _SYNC_TABLE)
    if not existing:
        # Table absent (not yet created). Nothing to extend.
        return
    to_add = [c for c in _NEW_COLUMNS if c not in existing]
    if not to_add:
        return

    with op.batch_alter_table(_SYNC_TABLE) as batch:
        if "last_full_sync_at" in to_add:
            batch.add_column(
                sa.Column("last_full_sync_at", sa.DateTime(), nullable=True)
            )
        if "last_outcome" in to_add:
            batch.add_column(
                sa.Column("last_outcome", sa.String(length=40), nullable=True)
            )
        if "last_source_fingerprint" in to_add:
            batch.add_column(
                sa.Column("last_source_fingerprint", sa.Text(), nullable=True)
            )
        if "fuzzy_stream_matching" in to_add:
            # server_default='0' backfills existing rows; required because the
            # table may already hold live targets (NOT NULL add without default
            # fails on a populated SQLite table). Nullable columns above need none.
            batch.add_column(
                sa.Column(
                    "fuzzy_stream_matching",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )


def upgrade() -> None:
    conn = op.get_bind()
    _add_sync_state_columns_if_absent(conn)


def downgrade() -> None:
    """Reverse 0024: drop the four persisted-sync-state columns.

    Defensive (house pattern): skip drops on columns that are not present so a
    half-applied state still cleans up rather than raising.
    """
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    to_drop = [c for c in _NEW_COLUMNS if c in existing]
    if to_drop:
        with op.batch_alter_table(_SYNC_TABLE) as batch:
            for col in to_drop:
                batch.drop_column(col)
