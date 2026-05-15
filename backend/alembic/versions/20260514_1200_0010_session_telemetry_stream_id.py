"""session_telemetry.stream_id + stream_name (per-poll stream identity)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-14 12:00:00.000000

Adds two NULLABLE columns to ``session_telemetry`` capturing the active
stream identity per poll row:

* ``stream_id INTEGER NULL`` — the Dispatcharr stream row id
  (``streams.id`` upstream). No FK — ``streams`` is not an ECM table,
  consistent with ``provider_id`` and ``channel_id`` design per the
  ``SessionTelemetry`` model docstring (skqln.2).
* ``stream_name TEXT NULL`` — the ``name`` field from the corresponding
  stream record on Dispatcharr's ``/streams/by-ids/`` response. Side-
  loaded by ``BandwidthTracker._resolve_provider_ids`` from the same
  batch lookup that already powers provider attribution — zero extra
  Dispatcharr round-trips.

Both are NULLABLE because resolution can fail for the same reasons
``provider_id`` resolution fails (missing stream_id on payload, 404,
network error, deleted stream). The read APIs (skqln.5 watch-time
breakdown, skqln.16 channel-heatmap) surface NULL gracefully — the
frontend renders ``—`` rather than blocking the row.

The display format the PO ratified on 2026-05-14:
    [<provider_name>] - <stream_name>     e.g.  [Infinity] - US: TNT

Provider name is NOT stored here — it side-loads on the frontend via
the M3U accounts map. The backend persists raw identity (id + name);
display formatting is presentation-layer work.

SQLite view dependency: migration 0008 created
``channel_watch_stats_v`` as a saved query over ``session_telemetry``.
SQLite's batch-mode ``add_column`` rebuilds the table (``CREATE TABLE
_alembic_tmp_X`` → copy rows → rename) and refuses to rename through a
view that references the old table. We sidestep that by dropping the
view before the batch operation and recreating it after — the view is
a saved query with no row data, so this is free. The CREATE VIEW text
matches migration 0008 exactly so the round-trip DDL stability check
in ``TestMigration0008::test_view_round_trip_ddl_is_stable`` keeps
passing across this revision.

Reversible: ``downgrade()`` drops both columns in a single
``batch_alter_table`` block — SQLite has no native ``DROP COLUMN`` so
``op.drop_column`` rebuilds the table via the batch operation. Same
view-drop/recreate guard applies on the downgrade path.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0010``
covers fresh up, fresh down, and round-trip schema identity.

bd-5w6jz idempotency: long-running installs where ``init_db``'s
``Base.metadata.create_all()`` had already added ``stream_id`` /
``stream_name`` to ``session_telemetry`` from the ORM model in
``models.py`` (both columns are declared on ``SessionTelemetry``) — while
``alembic_version`` was still at ``0009`` — would explode here with
``sqlite3.OperationalError: duplicate column name: stream_id``, aborting
startup post-bd-zaaey loud-fail. Mirrors the bd-m2k7p fix on 0005:
inspect existing columns and skip the add for any already present. If
both columns are already there, skip the entire view-drop/recreate
batch dance — there is nothing to alter and no reason to pay for a
table-copy.

Bead: ``enhancedchannelmanager-kh23e`` (original) +
``enhancedchannelmanager-5w6jz`` (idempotency).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# Verbatim copy of migration 0008's CHANNEL_WATCH_STATS_V_SQL. Held here
# locally rather than imported so each migration is self-contained and a
# future renaming/relocation of 0008 cannot silently break this one. If
# 0008's DDL changes, this constant must change in lock-step — the round-
# trip DDL-stability test in ``TestMigration0008`` is the canary.
#
# bd-5w6jz: must include the ``IF NOT EXISTS`` clause that 0008 was
# updated to use, so the round-trip stability test (which asserts byte-
# identical DDL across down→up cycles) sees the same stored CREATE text
# every time. SQLite stores the literal CREATE statement in
# ``sqlite_master.sql``, including the ``IF NOT EXISTS`` token.
_CHANNEL_WATCH_STATS_V_SQL = """
CREATE VIEW IF NOT EXISTS channel_watch_stats_v AS
SELECT
    per_poll.channel_id AS channel_id,
    CAST(SUM(per_poll.poll_interval_ms) / 1000 AS INTEGER) AS total_watch_seconds,
    datetime(MAX(per_poll.observed_at) / 1000, 'unixepoch') AS last_watched
FROM (
    -- DISTINCT-fy by (channel_id, observed_at) so a channel with N
    -- concurrent clients in one poll contributes only one poll interval
    -- to total_watch_seconds — matches legacy _update_watch_time which
    -- adds self.poll_interval once per channel per still-active poll
    -- regardless of client count.
    SELECT
        channel_id,
        observed_at,
        MAX(poll_interval_ms) AS poll_interval_ms
    FROM session_telemetry
    GROUP BY channel_id, observed_at
) AS per_poll
GROUP BY per_poll.channel_id
"""


def _session_telemetry_columns(connection) -> set[str]:
    """Return the set of column names currently on ``session_telemetry``.

    Returns the empty set if the table is missing — 0006 is also
    idempotent and may have skipped its create on a fully-drifted DB.
    The bd-5w6jz guard treats a missing table as "no columns to add"
    so this migration becomes a no-op rather than raising; the next
    ``create_all()`` + ``_assert_schema_matches_models`` pass surfaces
    the missing table.
    """
    insp = inspect(connection)
    if not insp.has_table("session_telemetry"):
        return set()
    return {c["name"] for c in insp.get_columns("session_telemetry")}


def upgrade() -> None:
    """Add ``stream_id`` and ``stream_name`` nullable columns.

    Drops the dependent ``channel_watch_stats_v`` view first because
    SQLite's batch-mode table rebuild cannot rename through a view
    reference. Recreates the view with the identical DDL after the
    columns land. Single ``batch_alter_table`` so SQLite rebuilds the
    table once for both adds rather than twice.

    Idempotent (bd-5w6jz): inspect existing columns; skip the add for
    any already present. If both target columns are already on the
    table, skip the entire view-drop/recreate batch dance — there is
    nothing to alter and the table-copy cost is unnecessary.
    """
    conn = op.get_bind()
    cols = _session_telemetry_columns(conn)

    needs_stream_id = "stream_id" not in cols
    needs_stream_name = "stream_name" not in cols
    if not (needs_stream_id or needs_stream_name):
        # Both columns already exist (e.g. ``create_all()`` materialised
        # the post-0010 ORM shape on a long-running install before
        # Alembic caught up). Nothing to do.
        return

    op.execute("DROP VIEW IF EXISTS channel_watch_stats_v")
    with op.batch_alter_table("session_telemetry") as batch_op:
        if needs_stream_id:
            batch_op.add_column(
                sa.Column("stream_id", sa.Integer(), nullable=True),
            )
        if needs_stream_name:
            batch_op.add_column(
                sa.Column("stream_name", sa.Text(), nullable=True),
            )
    op.execute(_CHANNEL_WATCH_STATS_V_SQL)


def downgrade() -> None:
    """Drop both columns. Single batch rebuild for symmetry with upgrade().

    Same view-drop/recreate dance as ``upgrade()`` — SQLite rejects the
    table rename otherwise.

    Defensive (bd-5w6jz): skip drops on columns that are not present so
    a half-applied prior state still cleans up rather than raising.
    """
    conn = op.get_bind()
    cols = _session_telemetry_columns(conn)

    has_stream_id = "stream_id" in cols
    has_stream_name = "stream_name" in cols
    if not (has_stream_id or has_stream_name):
        return

    op.execute("DROP VIEW IF EXISTS channel_watch_stats_v")
    with op.batch_alter_table("session_telemetry") as batch_op:
        if has_stream_name:
            batch_op.drop_column("stream_name")
        if has_stream_id:
            batch_op.drop_column("stream_id")
    op.execute(_CHANNEL_WATCH_STATS_V_SQL)
