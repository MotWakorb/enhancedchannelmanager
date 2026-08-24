"""sync_targets: per-target core-settings opt-out

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-23 14:00:00.000000

Bead ``enhancedchannelmanager-10wnq``. ADR-013 S9 and S3 have listed "core
settings" in the per-cycle set since the ADR was written, and the engine carried
none of it — ``core_settings`` appeared nowhere at any layer. That bead builds
the category; this migration adds the one column its per-blob register needs.

``core_settings_excluded`` TEXT NULL — a JSON list of core-settings blob keys
this target's operator declines. Empty / NULL means "replicate every blob the
engine's register allows", which is the faithful-copy default.

WHY A COLUMN AT ALL, given the principle says replicate. Two of the seven blobs
have a REAL functional risk behind them rather than a preference, and ADR-013's
own reading of both is "per-target opt-out, not omission":

* ``proxy_settings`` — buffering/timeout tuning copied onto slower hardware can
  degrade playback on the replica.
* ``backup_settings`` — both instances then run their own backup job on the same
  schedule, and the replica's retention count is bounded by the replica's
  storage rather than the primary's.

A list rather than two booleans, so a future blob needs a decision in the
register and not another migration.

IT CAN ONLY NARROW. ``NEVER_SYNC_CORE_SETTINGS_BLOBS`` (``network_access``) is
subtracted in the engine BEFORE this list is consulted, so naming that blob here
opts into nothing. The one exclusion that survives the principle is
code-enforced, not configuration.

Idempotency (house pattern, see 0024 / 0040 / 0046 / 0047 / 0048): the DDL step
is guarded by a column-presence check, so a DB that drifted forward via
``create_all()`` passes through as a no-op.

Reversibility: ``downgrade()`` drops the column. Losing it means every target
replicates every allowed blob again, which is the pre-feature default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0049"
down_revision: Union[str, Sequence[str], None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_SYNC_TABLE = "sync_targets"
_ADDED_COLUMN = "core_settings_excluded"


def _column_names(connection, table_name: str) -> set[str]:
    if not inspect(connection).has_table(table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    if not existing or _ADDED_COLUMN in existing:
        # Table absent (fresh DB materialises the full ORM shape via
        # create_all() and stamps head), or already drifted forward.
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        # NULLABLE add: NULL is the correct "this target excludes nothing"
        # backfill for every existing row, so no server_default.
        batch.add_column(sa.Column(_ADDED_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    """Reverse 0049: drop the opt-out list."""
    conn = op.get_bind()
    if _ADDED_COLUMN not in _column_names(conn, _SYNC_TABLE):
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        batch.drop_column(_ADDED_COLUMN)
