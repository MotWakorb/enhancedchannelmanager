"""Persist the playback-stability classification derived from Playback Probe samples.

Revision ID: 0058
Revises: 0057
"""
from alembic import op
import sqlalchemy as sa

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("stream_stats")}
    if "is_unstable" not in columns:
        op.add_column("stream_stats", sa.Column("is_unstable", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stream_stats") as batch_op:
        batch_op.drop_column("is_unstable")
