"""add required provider coverage to channel pipeline rules

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-02 18:00:00.000000

Bead: enhancedchannelmanager-rtst2.3 / GitHub #876.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0053"
down_revision: Union[str, Sequence[str], None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("auto_creation_rules")}
    if "required_provider_ids" not in columns:
        op.add_column(
            "auto_creation_rules",
            sa.Column("required_provider_ids", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("auto_creation_rules")}
    if "required_provider_ids" in columns:
        op.drop_column("auto_creation_rules", "required_provider_ids")
