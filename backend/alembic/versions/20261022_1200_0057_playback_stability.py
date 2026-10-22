"""Persist the Playback Probe sample window on stream_stats.

Revision ID: 0057
Revises: 0056
"""
from alembic import op
import sqlalchemy as sa

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def _columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("playback_samples", sa.Text(), nullable=True),
        sa.Column("playback_seconds", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("playback_discontinuities", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("playback_reconnects", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("playback_start_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_playback_probed", sa.DateTime(), nullable=True),
    )


def _existing_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("stream_stats")}


def upgrade() -> None:
    columns = _existing_columns()
    for column in _columns():
        if column.name not in columns:
            op.add_column("stream_stats", column)


def downgrade() -> None:
    columns = _existing_columns()
    for column in reversed(_columns()):
        if column.name in columns:
            op.drop_column("stream_stats", column.name)
