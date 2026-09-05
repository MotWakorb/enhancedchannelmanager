"""Add operator-defined literal channel aliases (u0ko6).

Revision ID: 0055
Revises: 0054
"""
from alembic import op
import sqlalchemy as sa

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "channel_name_mappings" not in tables:
        op.create_table(
            "channel_name_mappings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("preferred_name", sa.String(255), nullable=False),
        )
    if "channel_name_aliases" not in tables:
        op.create_table(
            "channel_name_aliases",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("mapping_id", sa.Integer(), sa.ForeignKey("channel_name_mappings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("match_key", sa.String(765), nullable=False, unique=True),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("channel_name_aliases")}
    if "ix_channel_name_aliases_mapping_id" not in indexes:
        op.create_index("ix_channel_name_aliases_mapping_id", "channel_name_aliases", ["mapping_id"])


def downgrade() -> None:
    op.drop_table("channel_name_aliases")
    op.drop_table("channel_name_mappings")
