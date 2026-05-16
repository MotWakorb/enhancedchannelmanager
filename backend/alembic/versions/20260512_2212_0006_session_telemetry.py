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

bd-5w6jz idempotency: long-running installs where ``init_db``'s
``Base.metadata.create_all()`` had already created ``session_telemetry``
(and its 5 indexes) from the ``SessionTelemetry`` ORM model in ``models.py``
— while ``alembic_version`` was still at ``0005`` — exploded here with
``sqlite3.OperationalError: table session_telemetry already exists``,
aborting startup post-bd-zaaey loud-fail. Mirrors the bd-ax3uj fix on
0003/0004: inspect first, then skip the create on artifacts already present.

Bead: enhancedchannelmanager-skqln.2 (original) + enhancedchannelmanager-5w6jz
(idempotency).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


# Index list factored out so upgrade/downgrade share one source of truth and
# any future addition to the migration's index set lands in one place.
_SESSION_TELEMETRY_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("idx_session_telemetry_observed_at", ["observed_at"]),
    ("idx_session_telemetry_user_observed", ["user_id", "observed_at"]),
    ("idx_session_telemetry_provider_observed", ["provider_id", "observed_at"]),
    ("idx_session_telemetry_session_id", ["session_id"]),
    (
        "idx_session_telemetry_provider_channel_observed_bytes",
        ["provider_id", "channel_id", "observed_at", "bytes_delta"],
    ),
)


def upgrade() -> None:
    """Create the session_telemetry fact table and its indexes.

    Idempotent (bd-5w6jz): if a prior ``create_all()`` already produced the
    table and/or any of its 5 indexes, skip the matching ``op.create_*`` so
    SQLite does not raise ``OperationalError: ... already exists``. The ORM
    and the migration agree on shape, so a pre-existing artifact is the
    right one — no rebuild needed.
    """
    conn = op.get_bind()

    if not _table_exists(conn, "session_telemetry"):
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

    existing_indexes = _index_names(conn, "session_telemetry")
    for idx_name, columns in _SESSION_TELEMETRY_INDEXES:
        if idx_name not in existing_indexes:
            op.create_index(
                idx_name,
                "session_telemetry",
                columns,
                unique=False,
            )


def downgrade() -> None:
    """Drop the session_telemetry indexes and table.

    Defensive (bd-5w6jz): skip drops on artifacts that are not present so a
    half-applied prior state still cleans up rather than raising.
    """
    conn = op.get_bind()
    existing_indexes = _index_names(conn, "session_telemetry")
    for idx_name, _columns in reversed(_SESSION_TELEMETRY_INDEXES):
        if idx_name in existing_indexes:
            op.drop_index(idx_name, table_name="session_telemetry")
    if _table_exists(conn, "session_telemetry"):
        op.drop_table("session_telemetry")
