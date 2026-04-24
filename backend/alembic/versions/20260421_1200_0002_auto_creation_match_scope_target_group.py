"""auto_creation_match_scope_target_group

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-21 12:00:00.000000

Adds ``match_scope_target_group`` (Boolean, default False) to
``auto_creation_rules`` so that a rule can restrict its duplicate
channel-name check to its target group. Without this flag, running
a rule targeting group B for a stream named "ESPN" would merge into
an existing "ESPN" in group A — the GH-92 (bd-r9mtd) bug.

Backwards-compatible: the default False preserves the pre-GH-92
global-lookup behavior for all existing rules.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add match_scope_target_group column.

    Uses server_default='0' during ADD COLUMN to backfill existing rows
    (SQLite requires a default for a NOT NULL column add), then drops the
    server-side default so the drift test sees no default clause — matching
    the baseline convention where similar Boolean rule flags (e.g.
    ``skip_struck_streams``) have only a Python-side default in the ORM.
    """
    with op.batch_alter_table('auto_creation_rules', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'match_scope_target_group',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('0'),
            )
        )
    # Drop the server default so metadata drift test sees "no default" — the
    # ORM supplies False for new rows via Python-side default.
    with op.batch_alter_table('auto_creation_rules', schema=None) as batch_op:
        batch_op.alter_column(
            'match_scope_target_group',
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    """Downgrade schema — drop match_scope_target_group column."""
    with op.batch_alter_table('auto_creation_rules', schema=None) as batch_op:
        batch_op.drop_column('match_scope_target_group')
