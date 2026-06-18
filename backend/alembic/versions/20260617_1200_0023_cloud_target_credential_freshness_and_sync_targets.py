"""cloud_target credential-freshness columns + sync_targets table (ADR-008 §D3, Sec Mandatory #5)

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-17 12:00:00.000000

Two additive, reversible changes for the v0.18.0 DBAS absorption epic
(bead enhancedchannelmanager-0i2vt.4):

1. EXTEND the existing ``cloud_storage_targets`` table with the
   credential-freshness contract (ADR-008 Security Mandatory #5):

   * ``credential_version`` INTEGER NOT NULL server_default='1' — a monotonic
     counter bumped on every credentials write (enforced same-txn by the ORM
     before_update listener in ``export_models.py``). The server_default is
     REQUIRED: this table may already hold live rows in deployed installs, and
     SQLite cannot add a NOT NULL column without a default to a populated
     table. Existing rows backfill to version 1.
   * ``token_revoked_at`` DATETIME NULL — a non-NULL value hard-stops any
     scheduled op that resolves this target (consumed by .6/.8). NULL == live.
   * ``insecure`` BOOLEAN NOT NULL server_default='0' — per-target TLS-skip
     flag (consumed by .8). Existing rows backfill to False (verify TLS).

2. CREATE the ``sync_targets`` table — the Dispatcharr-B cross-instance sync
   destination. SCHEMA-PRESENT / CODE-ABSENT by design: the table lands in
   v0.18.0 so the v0.18.1 sync feature is not gated on a migration when it
   ships, but there are NO v0.18.0 endpoints, Pydantic schemas, or service
   code that touch it — it is excluded from the public OpenAPI surface simply
   by having no router. See the ``SyncTarget`` model docstring in
   ``export_models.py``.

   ``sync_targets.credential_version`` is NOT NULL with the Python-side
   default=1 and NO server_default: this is a brand-new table created in one
   statement, so there are no pre-existing rows a NOT NULL add would orphan
   (matches the ``auto_creation_snapshots.channel_count`` / ``M3USnapshot``
   precedent in 0022). A server_default would buy nothing and would show as
   modify_default drift against the ORM.

Idempotency (house pattern, see 0022): every ADD COLUMN is guarded by a
column-presence check and the CREATE TABLE by a ``has_table`` check, so a
DB that drifted forward via ``create_all()`` (ORM shape materialised while
``alembic_version`` was still at 0022) passes through as a no-op rather than
raising ``OperationalError: duplicate column`` / ``table already exists``.

Reversibility: ``downgrade()`` drops the three added columns (via
``batch_alter_table`` — SQLite cannot DROP COLUMN without table recreation)
and drops the ``sync_targets`` table. Dropping ``credential_version`` /
``token_revoked_at`` / ``insecure`` loses the freshness metadata, and dropping
``sync_targets`` loses any configured sync destinations — both are acceptable
on a downgrade of a feature that has not shipped its consumers yet (the cloud
targets themselves and their encrypted credentials are preserved; only the
freshness bookkeeping is shed).

Pre-merge gate: ``tests/integration/test_cloud_sync_target_migration.py``
covers fresh up, fresh down (round-trip), the populated-table server_default
backfill, and idempotent re-run. ``tests/unit/test_alembic_baseline.py`` locks
ORM/migration parity (the new column set + table are picked up automatically
by the metadata-drift comparison).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_CLOUD_TABLE = "cloud_storage_targets"
_SYNC_TABLE = "sync_targets"

_NEW_CLOUD_COLUMNS = ("credential_version", "token_revoked_at", "insecure")


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _column_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def _add_cloud_columns_if_absent(conn) -> None:
    """Add the three credential-freshness columns to cloud_storage_targets.

    Each column is guarded independently so a half-applied prior run (some
    columns present, some not) finishes cleanly. ``batch_alter_table`` recreates
    the table under the hood, which is how SQLite handles ADD COLUMN with a
    server_default cleanly and keeps the op reversible.
    """
    existing = _column_names(conn, _CLOUD_TABLE)
    to_add = [c for c in _NEW_CLOUD_COLUMNS if c not in existing]
    if not to_add:
        return

    with op.batch_alter_table(_CLOUD_TABLE) as batch:
        if "credential_version" in to_add:
            # server_default='1' backfills existing rows; required because the
            # table may already hold live targets (NOT NULL add without default
            # fails on a populated SQLite table).
            batch.add_column(
                sa.Column(
                    "credential_version",
                    sa.Integer(),
                    nullable=False,
                    server_default="1",
                )
            )
        if "token_revoked_at" in to_add:
            batch.add_column(
                sa.Column("token_revoked_at", sa.DateTime(), nullable=True)
            )
        if "insecure" in to_add:
            batch.add_column(
                sa.Column(
                    "insecure",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )


def _create_sync_targets_if_absent(conn) -> None:
    """Create ``sync_targets`` only if it does not already exist (drift-safe)."""
    if _table_exists(conn, _SYNC_TABLE):
        return

    op.create_table(
        _SYNC_TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # New table, single statement -> no pre-existing rows to orphan, so no
        # server_default (matches the auto_creation_snapshots precedent). The
        # ORM supplies default=1.
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("token_revoked_at", sa.DateTime(), nullable=True),
        sa.Column("insecure", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sync_targets"),
        sa.UniqueConstraint("name", name="uq_sync_targets_name"),
    )


def upgrade() -> None:
    conn = op.get_bind()
    _add_cloud_columns_if_absent(conn)
    _create_sync_targets_if_absent(conn)


def downgrade() -> None:
    """Reverse 0023: drop sync_targets, then the cloud_storage_targets columns.

    Defensive (house pattern): skip drops on artifacts that are not present so a
    half-applied state still cleans up rather than raising.
    """
    conn = op.get_bind()

    if _table_exists(conn, _SYNC_TABLE):
        op.drop_table(_SYNC_TABLE)

    existing = _column_names(conn, _CLOUD_TABLE)
    to_drop = [c for c in _NEW_CLOUD_COLUMNS if c in existing]
    if to_drop:
        with op.batch_alter_table(_CLOUD_TABLE) as batch:
            for col in to_drop:
                batch.drop_column(col)
