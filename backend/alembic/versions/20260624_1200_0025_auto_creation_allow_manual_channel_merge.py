"""auto_creation_allow_manual_channel_merge

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-24 12:00:00.000000

Adds ``allow_manual_channel_merge`` (Boolean, default False) to
``auto_creation_rules`` (enhancedchannelmanager-orzck / W1).

When False (the default, and the safe behavior), auto-creation will NOT
adopt a hand-built MANUAL channel (a channel whose ``auto_created`` flag
is missing/falsy) as a merge/update/rename target on a name collision —
the manual channel is treated as "not found" and a new auto channel is
created instead. This fixes the reported bleed where merging auto-created
channels overwrote regular (manual) channels' names / metadata / filters.

Backwards-compatible: every existing rule is backfilled to False, which
is exactly the protective behavior we want — no rule silently keeps the
old manual-adoption behavior unless an operator explicitly opts in.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add allow_manual_channel_merge column.

    Mirrors the 0002 convention: server_default='0' during ADD COLUMN to
    backfill existing rows (SQLite requires a default for a NOT NULL column
    add), then drop the server-side default so the drift test sees no default
    clause — the ORM supplies False for new rows via the Python-side default.

    bd-5w6jz idempotency (guard added alongside 0031): if the column already
    exists — fast-path stamp raced by create_all(), or a forced re-run
    against an already-complete schema — skip rather than rebuild. The
    unguarded batch_alter_table re-run only ever "worked" while this was the
    LAST column on auto_creation_rules; once 0031 appended
    event_sync_config, the batch rebuild's column reordering raised
    CircularDependencyError on re-runs.
    """
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table("auto_creation_rules"):
        existing = {c["name"] for c in insp.get_columns("auto_creation_rules")}
        if "allow_manual_channel_merge" in existing:
            return

    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allow_manual_channel_merge",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.alter_column(
            "allow_manual_channel_merge",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    """Downgrade schema — drop allow_manual_channel_merge column."""
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.drop_column("allow_manual_channel_merge")
