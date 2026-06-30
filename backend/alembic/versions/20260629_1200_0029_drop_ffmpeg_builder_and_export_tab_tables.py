"""drop FFMPEG Builder + Export-tab tables

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-29 12:00:00.000000

The FFMPEG Builder tab and the Export tab were removed (beads
enhancedchannelmanager-vrrxv / enhancedchannelmanager-1w428). This migration
drops the four tables whose only producer/consumer was those tabs:

  - ``ffmpeg_saved_configs`` — FFMPEG Builder saved configs (ffmpeg_builder.persistence)
  - ``playlist_profiles``    — Export-tab playlist export profiles
  - ``publish_configurations`` — Export-tab scheduled publish configs
  - ``publish_history``      — Export-tab publish audit log

Deliberately NOT dropped (still load-bearing):
  - ``cloud_storage_targets`` — DBAS backup upload destinations + list_cloud_targets MCP tool
  - ``sync_targets``          — cross-instance sync (routers/sync_targets.py, dbas_sync*)
  - ``ffmpeg_profiles``       — still a DBAS config-backup/restore section (routers/backup.py)

Drop order respects FKs: publish_history -> publish_configurations ->
playlist_profiles. ``publish_configurations`` also FK-references
``cloud_storage_targets`` (ON DELETE SET NULL) but that parent table is kept,
so dropping the child first is safe.

Idempotent: each drop is inspect-guarded so a long-running install whose ORM
already lacks these tables (create_all never re-materialises a deleted model)
is a no-op rather than raising ``no such table``.

Bead: enhancedchannelmanager-vrrxv / enhancedchannelmanager-1w428.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# Drop in FK-safe order (children before parents).
_DROP_ORDER = (
    "publish_history",
    "publish_configurations",
    "playlist_profiles",
    "ffmpeg_saved_configs",
)


def _has_table(connection, name: str) -> bool:
    return inspect(connection).has_table(name)


def upgrade() -> None:
    """Drop the FFMPEG-Builder + Export-tab tables (inspect-guarded)."""
    conn = op.get_bind()
    for table in _DROP_ORDER:
        if _has_table(conn, table):
            op.drop_table(table)


def downgrade() -> None:
    """Recreate the dropped tables with their baseline (revision 0001) schema.

    Data is not restored — only the schema, so a rollback leaves the tables
    empty. Recreate in parent-before-child order. Each create is inspect-guarded
    for partial-rerun safety.
    """
    conn = op.get_bind()

    if not _has_table(conn, "ffmpeg_saved_configs"):
        op.create_table(
            "ffmpeg_saved_configs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table(conn, "playlist_profiles"):
        op.create_table(
            "playlist_profiles",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("selection_mode", sa.String(length=20), nullable=False),
            sa.Column("selected_groups", sa.Text(), nullable=False),
            sa.Column("selected_channels", sa.Text(), nullable=False),
            sa.Column("stream_url_mode", sa.String(length=20), nullable=False),
            sa.Column("include_logos", sa.Boolean(), nullable=False),
            sa.Column("include_epg_ids", sa.Boolean(), nullable=False),
            sa.Column("include_channel_numbers", sa.Boolean(), nullable=False),
            sa.Column("sort_order", sa.String(length=20), nullable=False),
            sa.Column("filename_prefix", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )

    if not _has_table(conn, "publish_configurations"):
        op.create_table(
            "publish_configurations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("profile_id", sa.Integer(), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=True),
            sa.Column("schedule_type", sa.String(length=20), nullable=False),
            sa.Column("cron_expression", sa.String(length=100), nullable=True),
            sa.Column("event_triggers", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("webhook_url", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["profile_id"], ["playlist_profiles.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_id"], ["cloud_storage_targets.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("publish_configurations", schema=None) as batch_op:
            batch_op.create_index("idx_publish_config_profile", ["profile_id"], unique=False)

    if not _has_table(conn, "publish_history"):
        op.create_table(
            "publish_history",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("config_id", sa.Integer(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("channels_count", sa.Integer(), nullable=True),
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["config_id"], ["publish_configurations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("publish_history", schema=None) as batch_op:
            batch_op.create_index("idx_publish_history_config", ["config_id"], unique=False)
            batch_op.create_index(
                "idx_publish_history_started",
                [sa.literal_column("started_at DESC")],
                unique=False,
            )
