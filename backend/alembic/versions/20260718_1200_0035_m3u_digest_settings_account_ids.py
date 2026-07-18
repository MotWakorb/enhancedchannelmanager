"""m3u_digest_settings_account_ids

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-18 12:00:00.000000

Adds ``account_ids`` (nullable TEXT, JSON array of M3U account IDs) to
``m3u_digest_settings`` (GH #496 / bead enhancedchannelmanager-wwovg).

An operator running a high-churn "FAST" IPTV provider (10k+ stream URL
changes/hour) alongside slow-changing standard providers wants the noisy
provider's changes excluded from M3U Change Digest NOTIFICATIONS, while
``M3UChangeLog`` keeps logging every account's changes unabridged. This
column scopes which accounts' changes are included when the digest task
builds its email/Discord content (``tasks/m3u_digest.py:_build_digest_payload``).
NULL/empty means "all accounts" — the pre-existing behavior, so every
current install is unaffected until an operator opts into a subset via the
Settings > M3U Digest page.

Column is nullable with no default (mirrors ``exclude_group_patterns`` /
``exclude_stream_patterns`` on the same table), so no backfill/server-default
dance is needed for the ADD COLUMN.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "m3u_digest_settings"
_COLUMN = "account_ids"


def upgrade() -> None:
    """Add the nullable account_ids TEXT column.

    Idempotency guard (bd-5w6jz convention): if the column already exists —
    fast-path stamp raced by create_all(), or a forced re-run against an
    already-complete schema — skip rather than rebuild.
    """
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table(_TABLE):
        existing = {c["name"] for c in insp.get_columns(_TABLE)}
        if _COLUMN in existing:
            return

    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the account_ids column."""
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_column(_COLUMN)
