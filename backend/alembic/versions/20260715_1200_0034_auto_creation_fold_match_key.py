"""auto_creation_fold_match_key

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-15 12:00:00.000000

Adds ``fold_match_key`` (Boolean, default False) to ``auto_creation_rules``
(GH #645 / bead enhancedchannelmanager-0vao3).

When True (opt-in), the create_channel ``if_exists`` merge lookup
additionally compares names by a canonical fold key — casefold + strip ALL
whitespace (``match_fold.fold_match_key``) — so spelling variants like
"eurosport 2" / "Eurosport 2" / "Eurosport2" / "eurosport2" merge into ONE
channel instead of creating duplicates. Comparison key only; visible
channel names are never altered.

Backwards-compatible: every existing rule is backfilled to False, so no
install changes matching behavior until an operator explicitly opts a rule
in (the fold can over-merge genuinely distinct names that differ only in
spacing/case, hence opt-in).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add fold_match_key column.

    Mirrors the 0025 convention: server_default='0' during ADD COLUMN to
    backfill existing rows (SQLite requires a default for a NOT NULL column
    add), then drop the server-side default so the drift test sees no default
    clause — the ORM supplies False for new rows via the Python-side default.

    Idempotency guard (bd-5w6jz convention): if the column already exists —
    fast-path stamp raced by create_all(), or a forced re-run against an
    already-complete schema — skip rather than rebuild.
    """
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table("auto_creation_rules"):
        existing = {c["name"] for c in insp.get_columns("auto_creation_rules")}
        if "fold_match_key" in existing:
            return

    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "fold_match_key",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.alter_column(
            "fold_match_key",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    """Downgrade schema — drop fold_match_key column."""
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.drop_column("fold_match_key")
