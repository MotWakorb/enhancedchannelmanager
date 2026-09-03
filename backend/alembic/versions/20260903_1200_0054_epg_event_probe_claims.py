"""add durable EPG event probe claims

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-03 12:00:00.000000

Bead: enhancedchannelmanager-8gmk8.1.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0054"
down_revision: Union[str, Sequence[str], None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "epg_event_probe_claims" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "epg_event_probe_claims",
            sa.Column("trigger_key", sa.String(length=1000), nullable=False),
            sa.Column("claimed_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("trigger_key"),
        )


def downgrade() -> None:
    if "epg_event_probe_claims" in inspect(op.get_bind()).get_table_names():
        op.drop_table("epg_event_probe_claims")
