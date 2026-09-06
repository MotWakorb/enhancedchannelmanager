"""Persist fresh low-bitrate classification (GH980 / 8gmk8.4).

Revision ID: 0056
Revises: 0055
"""
from alembic import op
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("stream_stats")}
    if "is_low_bitrate" not in columns:
        op.add_column("stream_stats", sa.Column("is_low_bitrate", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("stream_stats") as batch_op:
        batch_op.drop_column("is_low_bitrate")
