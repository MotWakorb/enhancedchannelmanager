"""Add optional inclusive active-date bounds to channel pipeline rules.

Revision ID: 0038
Revises: 0037
Bead: enhancedchannelmanager-6lhmb (GitHub #646)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0038"
down_revision: Union[str, Sequence[str], None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("auto_creation_rules")
    }


def upgrade() -> None:
    existing = _columns()
    if "active_from" not in existing:
        op.add_column(
            "auto_creation_rules", sa.Column("active_from", sa.Date(), nullable=True)
        )
    if "active_until" not in existing:
        op.add_column(
            "auto_creation_rules", sa.Column("active_until", sa.Date(), nullable=True)
        )


def downgrade() -> None:
    existing = _columns()
    if "active_until" in existing:
        op.drop_column("auto_creation_rules", "active_until")
    if "active_from" in existing:
        op.drop_column("auto_creation_rules", "active_from")
