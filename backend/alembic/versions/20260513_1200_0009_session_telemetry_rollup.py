"""session_telemetry rollups + telemetry_rollup_state marker

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-13 12:00:00.000000

Creates the Stats v2 daily-rollup machinery — three new tables — per
``docs/adr/ADR-007-session-telemetry-retention.md`` D4/D5 and the
``telemetry_rollup_state`` once-per-day marker described in D3:

* ``session_telemetry_user_daily`` — per-user, per-channel, per-UTC-day watch
  rollup. PK ``(user_id, channel_id, day)``. Sourced from
  ``session_telemetry`` by the nightly rollup job (bead
  ``enhancedchannelmanager-7i2vv``). Reads: the Users panel watch-time
  selector + per-channel breakdown.

* ``session_telemetry_provider_daily`` — per-provider, per-channel,
  per-UTC-day performance rollup. PK ``(provider_id, channel_id, day)``.
  Sourced from ``session_telemetry`` by the same nightly job.
  ``provider_id`` is **TEXT** (not the raw column's INTEGER) so the
  ``'unknown'`` sentinel from ADR-007 §line 109 can survive as a literal
  string in the PK — the rollup job coalesces a NULL raw ``provider_id``
  to the literal ``'unknown'`` at rollup time. Reads: the Providers panel
  buffering, watch-time, heatmap, and bitrate visualisations.

* ``telemetry_rollup_state`` — small marker table, one row per named
  rollup (``user_daily``, ``provider_daily``). Persists ``last_completed_day``
  + ``last_run_at_ms`` + ``last_run_status`` + ``last_run_error`` so the
  nightly job can guard against duplicate runs and SRE can alert on
  staleness (>36h warn, >25d page per ADR-007 D6 failure modes 1+2).

Why these tables are TABLES, not views (ADR-007 D2): the read path must
hit pre-aggregated rows, not re-scan up to 26M raw rows on every panel
load. The skqln.10 benchmark gate is written against the table read path.

Reversible: ``downgrade()`` drops the three tables in reverse dependency
order (rollup tables first, then the marker — the marker has no FKs but
we drop it last so a partial failure mid-downgrade leaves the rollup
tables intact for forensics; SQLite has no native DDL transactions
anyway, so this is best-effort ordering).

Pre-merge gate: ``backend/tests/integration/test_session_telemetry_rollup_migration.py``
covers fresh up, fresh down, and round-trip schema identity.

bd-5w6jz idempotency: long-running installs where ``init_db``'s
``Base.metadata.create_all()`` had already created the three rollup tables
+ their indexes from the ORM models in ``models.py``
(``SessionTelemetryUserDaily``, ``SessionTelemetryProviderDaily``,
``TelemetryRollupState``) — while ``alembic_version`` was still at
``0008`` — would explode here with
``sqlite3.OperationalError: table session_telemetry_user_daily already exists``,
aborting startup post-bd-zaaey loud-fail. Mirrors the bd-ax3uj fix on
0003/0004: inspect first, then skip the create on artifacts already
present.

Bead: ``enhancedchannelmanager-7i2vv`` (original) +
``enhancedchannelmanager-5w6jz`` (idempotency).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


def upgrade() -> None:
    """Create the three rollup tables + their indexes.

    Schema notes:

    * ``session_telemetry_user_daily.user_id`` is INTEGER NOT NULL.
      Raw ``session_telemetry.user_id`` IS nullable (anonymous/system
      traffic), but the rollup intentionally excludes NULL user_id rows
      — there is no behavioral subject to attribute. The exclusion is
      enforced at rollup time in the WHERE clause; the column constraint
      simply enforces the same invariant at the storage layer.

    * ``session_telemetry_provider_daily.provider_id`` is TEXT NOT NULL.
      The rollup job coalesces raw NULLs (resolver miss) to the literal
      string ``'unknown'`` at rollup time per ADR-007 §line 109, so this
      column never sees a NULL value.

    * ``session_telemetry_provider_daily.channel_id`` is VARCHAR(64) to
      match ``session_telemetry.channel_id`` after migration 0007.

    * ``telemetry_rollup_state.last_completed_day`` is nullable so the
      first-run case (no day has yet been rolled up) is representable.

    Idempotent (bd-5w6jz): for each ``op.create_table`` / ``op.create_index``,
    inspect first and skip if the artifact already exists (e.g. because
    ``create_all()`` materialised it from the ORM model on a long-running
    install before Alembic caught up).
    """
    conn = op.get_bind()

    if not _table_exists(conn, "session_telemetry_user_daily"):
        op.create_table(
            "session_telemetry_user_daily",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("channel_id", sa.String(length=64), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("watch_seconds", sa.Integer(), nullable=False),
            sa.Column("session_count", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint(
                "user_id",
                "channel_id",
                "day",
                name="pk_session_telemetry_user_daily",
            ),
        )
    if "idx_session_telemetry_user_daily_day" not in _index_names(
        conn, "session_telemetry_user_daily"
    ):
        op.create_index(
            "idx_session_telemetry_user_daily_day",
            "session_telemetry_user_daily",
            ["day"],
            unique=False,
        )

    if not _table_exists(conn, "session_telemetry_provider_daily"):
        op.create_table(
            "session_telemetry_provider_daily",
            sa.Column("provider_id", sa.Text(), nullable=False),
            sa.Column("channel_id", sa.String(length=64), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("watch_seconds", sa.Integer(), nullable=False),
            sa.Column("bytes_delta_sum", sa.BigInteger(), nullable=False),
            sa.Column("buffer_event_count", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint(
                "provider_id",
                "channel_id",
                "day",
                name="pk_session_telemetry_provider_daily",
            ),
        )
    provider_daily_indexes = _index_names(conn, "session_telemetry_provider_daily")
    if "idx_session_telemetry_provider_daily_provider_day" not in provider_daily_indexes:
        op.create_index(
            "idx_session_telemetry_provider_daily_provider_day",
            "session_telemetry_provider_daily",
            ["provider_id", "day"],
            unique=False,
        )
    if "idx_session_telemetry_provider_daily_day" not in provider_daily_indexes:
        op.create_index(
            "idx_session_telemetry_provider_daily_day",
            "session_telemetry_provider_daily",
            ["day"],
            unique=False,
        )

    if not _table_exists(conn, "telemetry_rollup_state"):
        op.create_table(
            "telemetry_rollup_state",
            sa.Column("rollup_name", sa.Text(), nullable=False),
            sa.Column("last_completed_day", sa.Date(), nullable=True),
            sa.Column("last_run_at_ms", sa.BigInteger(), nullable=True),
            sa.Column("last_run_status", sa.Text(), nullable=True),
            sa.Column("last_run_error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint(
                "rollup_name",
                name="pk_telemetry_rollup_state",
            ),
        )


def downgrade() -> None:
    """Drop the three rollup tables and their indexes.

    Order: rollup tables first (their indexes are dropped implicitly by
    SQLite when the table is dropped), then the marker table last so a
    partial failure preserves the most data.

    Defensive (bd-5w6jz): skip drops on artifacts that are not present so
    a half-applied prior state still cleans up rather than raising.
    """
    conn = op.get_bind()

    provider_daily_indexes = _index_names(conn, "session_telemetry_provider_daily")
    if "idx_session_telemetry_provider_daily_day" in provider_daily_indexes:
        op.drop_index(
            "idx_session_telemetry_provider_daily_day",
            table_name="session_telemetry_provider_daily",
        )
    if "idx_session_telemetry_provider_daily_provider_day" in provider_daily_indexes:
        op.drop_index(
            "idx_session_telemetry_provider_daily_provider_day",
            table_name="session_telemetry_provider_daily",
        )
    if _table_exists(conn, "session_telemetry_provider_daily"):
        op.drop_table("session_telemetry_provider_daily")

    if "idx_session_telemetry_user_daily_day" in _index_names(
        conn, "session_telemetry_user_daily"
    ):
        op.drop_index(
            "idx_session_telemetry_user_daily_day",
            table_name="session_telemetry_user_daily",
        )
    if _table_exists(conn, "session_telemetry_user_daily"):
        op.drop_table("session_telemetry_user_daily")

    if _table_exists(conn, "telemetry_rollup_state"):
        op.drop_table("telemetry_rollup_state")
