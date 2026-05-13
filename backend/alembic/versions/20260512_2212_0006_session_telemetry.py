"""session_telemetry

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-12 22:12:00.000000

Creates the Stats v2 ``session_telemetry`` fact table — one row per
``BandwidthTracker`` poll cycle per active viewing session. Append-mostly;
raw rows are pruned by ``observed_at`` per the retention policy in
``docs/adr/ADR-007-session-telemetry-retention.md``. The daily rollup tables,
the ``telemetry_rollup_state`` marker, and the nightly prune job are OUT of
scope here — they land in bead ``enhancedchannelmanager-7i2vv``. The operator
opt-out toggle lands in ``enhancedchannelmanager-tp1pd``.

Schema notes:

* ``user_id`` is the only real FK (``users.id`` ``ON DELETE SET NULL`` —
  account deletion scrubs the behavioral trail; see
  ``docs/security/threat_model_stats_v2.md`` §7.8).
* ``channel_id`` / ``provider_id`` are plain nullable indexed integers, NOT
  foreign keys — ``channels``/``providers`` are upstream Dispatcharr entities,
  not ECM tables (same house pattern as ``channel_watch_stats`` etc.).
* Named CHECK ``ck_session_telemetry_bytes_delta_non_negative`` — unnamed
  SQLite CHECK names are unstable across versions (``docs/database_migrations.md``).
* The ``(provider_id, channel_id, observed_at, bytes_delta)`` composite is the
  GH-59 channels-by-provider heatmap covering index (skqln.16); GH-59 is in
  v0.17.0 scope per the skqln epic. The Postgres ``INCLUDE`` form is invalid in
  SQLite, so this is a plain composite with a trailing covering column.
* ``bitrate_bps`` is intentionally not stored (derivable from
  ``bytes_delta / poll_interval_ms``).

Fresh ``CREATE TABLE`` — no ``batch_alter_table`` needed (batch mode only
applies to later ``ALTER``s, not the initial create).

Bead: enhancedchannelmanager-skqln.2.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    """Create the session_telemetry fact table and its indexes."""
    op.create_table(
        "session_telemetry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("bytes_delta", sa.BigInteger(), nullable=False),
        sa.Column(
            "buffer_event_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("poll_interval_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "bytes_delta >= 0",
            name="ck_session_telemetry_bytes_delta_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_session_telemetry_observed_at",
        "session_telemetry",
        ["observed_at"],
        unique=False,
    )
    op.create_index(
        "idx_session_telemetry_user_observed",
        "session_telemetry",
        ["user_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "idx_session_telemetry_provider_observed",
        "session_telemetry",
        ["provider_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "idx_session_telemetry_session_id",
        "session_telemetry",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "idx_session_telemetry_provider_channel_observed_bytes",
        "session_telemetry",
        ["provider_id", "channel_id", "observed_at", "bytes_delta"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the session_telemetry indexes and table."""
    op.drop_index(
        "idx_session_telemetry_provider_channel_observed_bytes",
        table_name="session_telemetry",
    )
    op.drop_index(
        "idx_session_telemetry_session_id",
        table_name="session_telemetry",
    )
    op.drop_index(
        "idx_session_telemetry_provider_observed",
        table_name="session_telemetry",
    )
    op.drop_index(
        "idx_session_telemetry_user_observed",
        table_name="session_telemetry",
    )
    op.drop_index(
        "idx_session_telemetry_observed_at",
        table_name="session_telemetry",
    )
    op.drop_table("session_telemetry")
