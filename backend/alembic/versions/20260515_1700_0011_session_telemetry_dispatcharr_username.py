"""session_telemetry: denormalize Dispatcharr username + drop ECM users FK

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-15 17:00:00.000000

Stats v2 architectural fix for the namespace-collision bug bd-uqbob worked
around with an env-var exclude filter. Two changes in one migration because
they touch the same column space and SQLite batch-mode rebuilds the entire
table either way:

1. Add column ``dispatcharr_username TEXT NULL``. The writer
   (``BandwidthTracker._write_session_telemetry``) populates this directly
   from the per-poll Dispatcharr ``user_id → username`` map it already
   maintains for the bd-uqbob exclude filter — zero extra round-trips.
   Read APIs surface this verbatim instead of joining
   ``session_telemetry.user_id`` against ECM's local ``users`` table.

2. Drop the FK constraint on ``session_telemetry.user_id`` →
   ``users.id``. ECM's local ``users`` table (auth identities for ECM
   logins) and Dispatcharr's ``users`` table (stream viewers) are two
   separate namespaces with coincidentally-overlapping integer IDs. The
   FK was a structural lie — it joined behavioral telemetry from one
   namespace to identity rows in another, and the join returned wrong
   answers (Dispatcharr viewer id=3 surfaced as ECM admin "claude" in
   the Watch Time panel when their integer ids collided). The column
   stays as an opaque Dispatcharr-side integer; ``_coerce_session_user_id``
   still scrubs the anonymous ``0`` sentinel so analytics queries see
   ``NULL`` rather than the noise value.

PO directive (verbatim, 2026-05-13):
    "1 is the correct choice, because we should be able to get the
    active user account from the Dispatcharr API; we never need to
    touch a table."

Backfill / pre-migration rows: NOT auto-applied. Pre-migration rows
keep ``dispatcharr_username = NULL`` and the read APIs surface them
gracefully ("Unknown viewer" on the frontend). The Stats v2 history-
cutover policy from bd-skqln.3 ("accept the gap") covers this — the
panel was already wrong about user attribution before this fix; the
pre-migration rows now honestly admit that. Operators who want a
clean slate can purge with the existing one-liner from the bd-uqbob
CHANGELOG entry. Backfilling via a startup-time Dispatcharr lookup
was rejected: it would couple migration completion to network
availability and would still be guessing at attribution for sessions
where the client_user_map mapping is no longer recoverable.

SQLite specifics:

* Adding ``dispatcharr_username`` is a normal ``ALTER TABLE ADD COLUMN``
  in SQLite — works without batch mode.

* Dropping the ``user_id`` FK requires a full table rebuild (SQLite
  has no ``ALTER TABLE DROP CONSTRAINT``). We use
  ``batch_alter_table`` with an explicit ``table_args`` listing the
  remaining constraints (just the named CHECK on ``bytes_delta``);
  the FK is omitted from that list, which is how Alembic's batch
  mode "drops" a constraint — the rebuilt table copies forward
  every column AND row but only the constraints we list.

* The ``channel_watch_stats_v`` view (created in 0008) references
  ``session_telemetry``. SQLite refuses to rename through a view, so
  the batch dance must drop and recreate the view around the alter
  — same pattern migration 0010 uses. The view DDL is held verbatim
  here so the migration is self-contained; the
  ``TestMigration0008::test_view_round_trip_ddl_is_stable`` canary
  in ``test_alembic_smoke.py`` keeps the two copies honest.

bd-5w6jz idempotency: long-running installs where ``init_db``'s
``Base.metadata.create_all()`` had already added ``dispatcharr_username``
to ``session_telemetry`` from the post-0011 ORM model in ``models.py``,
or where the smart-bootstrap fast-path stamped through this revision
without dropping the FK, would re-run the migration on the next start.
The guards:

* Skip the column add if ``dispatcharr_username`` is already present
  (the bd-m2k7p / bd-kh23e column-add idempotency pattern).
* Skip the FK-drop batch if ``user_id`` already has no FK in the
  live schema (``inspect().get_foreign_keys()`` returns no entry
  whose constrained columns include ``user_id``).
* If both are no-ops, return early — the table-copy cost of an
  unnecessary batch rebuild is non-trivial on a 26M-row deployment.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0011``
covers fresh up, fresh down, idempotency on the column path, and
idempotency on the FK-drop path.

Bead: ``enhancedchannelmanager-gsn3r``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# Verbatim copy of migration 0008's CHANNEL_WATCH_STATS_V_SQL (also held
# locally in 0010 for the same reason: each migration is self-contained
# so a future rename/relocation of 0008 can never silently break this
# one). The ``IF NOT EXISTS`` clause is required — SQLite stores the
# literal CREATE statement in ``sqlite_master.sql`` including the token,
# and the round-trip-stability canary in ``TestMigration0008`` asserts
# byte-identical DDL across down→up cycles.
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

    Returns the empty set if the table is missing — every prior 0006-0010
    migration is idempotent and may have skipped its create on a fully-
    drifted DB. The bd-5w6jz guard treats a missing table as "no columns
    to add" so this migration becomes a no-op rather than raising; the
    next ``create_all()`` + ``_assert_schema_matches_models`` pass surfaces
    the missing table.
    """
    insp = inspect(connection)
    if not insp.has_table("session_telemetry"):
        return set()
    return {c["name"] for c in insp.get_columns("session_telemetry")}


def _user_id_has_fk(connection) -> bool:
    """True if ``session_telemetry.user_id`` still has a FK constraint."""
    insp = inspect(connection)
    if not insp.has_table("session_telemetry"):
        return False
    for fk in insp.get_foreign_keys("session_telemetry"):
        if "user_id" in (fk.get("constrained_columns") or []):
            return True
    return False


def upgrade() -> None:
    """Add ``dispatcharr_username`` and drop the ``user_id`` FK.

    Two-phase: the column add runs as a plain ``ALTER TABLE`` (SQLite
    supports this directly). The FK drop runs as a batch rebuild because
    SQLite has no ``ALTER TABLE DROP CONSTRAINT``.

    Idempotent (bd-5w6jz): inspect existing columns + FKs; skip whichever
    half is already done. If both are no-ops, return without paying the
    table-copy cost of an unnecessary batch rebuild.
    """
    conn = op.get_bind()
    cols = _session_telemetry_columns(conn)
    has_fk = _user_id_has_fk(conn)

    needs_column = "dispatcharr_username" not in cols
    needs_fk_drop = has_fk

    if not (needs_column or needs_fk_drop):
        # Both already done — nothing to do. (Common on a long-running
        # install where ``create_all()`` materialised the post-0011 ORM
        # shape AND the prior fast-path-stamp left the FK already
        # absent.)
        return

    if needs_column:
        # Plain ADD COLUMN — no batch rebuild needed. NULLABLE TEXT, no
        # default; pre-migration rows surface as NULL ("Unknown viewer"
        # on the frontend) per the accept-the-gap policy.
        op.add_column(
            "session_telemetry",
            sa.Column("dispatcharr_username", sa.Text(), nullable=True),
        )

    if needs_fk_drop:
        # SQLite has no ``ALTER TABLE DROP CONSTRAINT``. ``batch_alter_table``
        # normally only rebuilds when the batch context contains an
        # operation that requires it (add column, alter column, etc.).
        # We need a rebuild for its own sake — the ENTIRE purpose is to
        # reshape the table without the FK constraint. Two pieces are
        # required:
        #
        # 1. ``copy_from=target_table`` — hand batch mode an explicit
        #    Table with the post-0011 shape (no FK on user_id). The
        #    rebuild copies every row's data forward but only the
        #    constraints declared on this explicit Table.
        #
        # 2. ``recreate="always"`` — without this, an empty batch context
        #    skips the rebuild entirely and the FK survives. This is
        #    the kludge SQLAlchemy's batch mode demands when the only
        #    "change" is a constraint shape that has no per-column op.
        #
        # The view-drop/recreate dance mirrors migration 0010's pattern;
        # SQLite refuses to rename through a view that references the
        # old table.
        op.execute("DROP VIEW IF EXISTS channel_watch_stats_v")
        target_table = sa.Table(
            "session_telemetry",
            sa.MetaData(),
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("observed_at", sa.Integer(), nullable=False),
            # NO FK — that's the entire point of this batch. ``user_id``
            # stays as an opaque Dispatcharr-side integer.
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("dispatcharr_username", sa.Text(), nullable=True),
            sa.Column("provider_id", sa.Integer(), nullable=True),
            sa.Column("channel_id", sa.String(64), nullable=False),
            sa.Column("bytes_delta", sa.BigInteger(), nullable=False),
            sa.Column(
                "buffer_event_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("poll_interval_ms", sa.Integer(), nullable=False),
            sa.Column("stream_id", sa.Integer(), nullable=True),
            sa.Column("stream_name", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "bytes_delta >= 0",
                name="ck_session_telemetry_bytes_delta_non_negative",
            ),
            # Indexes from migration 0006 — must be redeclared on the
            # rebuild Table so SQLAlchemy's batch mode recreates them.
            # ``copy_from`` IS the source of truth for the rebuilt
            # table's shape; without these the rebuild silently drops
            # all 5 indexes (caught by the
            # ``TestAlembicBaseline::test_baseline_matches_metadata_no_drift``
            # canary, which is exactly its job).
            sa.Index("idx_session_telemetry_observed_at", "observed_at"),
            sa.Index(
                "idx_session_telemetry_user_observed",
                "user_id",
                "observed_at",
            ),
            sa.Index(
                "idx_session_telemetry_provider_observed",
                "provider_id",
                "observed_at",
            ),
            sa.Index("idx_session_telemetry_session_id", "session_id"),
            sa.Index(
                "idx_session_telemetry_provider_channel_observed_bytes",
                "provider_id",
                "channel_id",
                "observed_at",
                "bytes_delta",
            ),
        )
        with op.batch_alter_table(
            "session_telemetry",
            copy_from=target_table,
            recreate="always",
        ) as _batch_op:
            # No per-column ops — the rebuild against ``copy_from`` +
            # ``recreate="always"`` IS the mechanism. The new table
            # inherits the rows but only the constraints + indexes
            # listed in ``target_table``.
            pass
        op.execute(_CHANNEL_WATCH_STATS_V_SQL)


def downgrade() -> None:
    """Restore the FK and drop ``dispatcharr_username``.

    Defensive (bd-5w6jz): inspect first; skip whichever half is already
    in the post-downgrade state so a partial-rerun cleans up rather than
    raising. The downgrade is not strictly the inverse — it adds the FK
    back and drops the column. Operators who downgrade past this point
    accept the namespace-collision bug returns; documented in the bead
    for forward-only-cutover guidance.
    """
    conn = op.get_bind()
    cols = _session_telemetry_columns(conn)
    has_fk = _user_id_has_fk(conn)

    has_column = "dispatcharr_username" in cols
    needs_fk_restore = not has_fk

    if not (has_column or needs_fk_restore):
        return

    if needs_fk_restore:
        # Restore the FK via batch rebuild against an explicit
        # ``copy_from`` Table that DOES declare the FK. Same
        # view-drop/recreate dance as upgrade(). Note this still
        # carries ``dispatcharr_username`` if it's currently present;
        # the column-drop runs AFTER the FK restore so the rebuild
        # doesn't have to lose two things at once.
        op.execute("DROP VIEW IF EXISTS channel_watch_stats_v")
        target_columns = [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("observed_at", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
        ]
        if has_column:
            target_columns.append(
                sa.Column("dispatcharr_username", sa.Text(), nullable=True)
            )
        target_columns.extend([
            sa.Column("provider_id", sa.Integer(), nullable=True),
            sa.Column("channel_id", sa.String(64), nullable=False),
            sa.Column("bytes_delta", sa.BigInteger(), nullable=False),
            sa.Column(
                "buffer_event_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("poll_interval_ms", sa.Integer(), nullable=False),
            sa.Column("stream_id", sa.Integer(), nullable=True),
            sa.Column("stream_name", sa.Text(), nullable=True),
        ])
        target_table = sa.Table(
            "session_telemetry",
            sa.MetaData(),
            *target_columns,
            sa.CheckConstraint(
                "bytes_delta >= 0",
                name="ck_session_telemetry_bytes_delta_non_negative",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                ondelete="SET NULL",
            ),
            # Indexes from migration 0006 — must be redeclared on the
            # downgrade rebuild Table for the same reason as upgrade()
            # above (``copy_from`` IS the source of truth for the
            # rebuilt shape; missing indexes are silently dropped).
            sa.Index("idx_session_telemetry_observed_at", "observed_at"),
            sa.Index(
                "idx_session_telemetry_user_observed",
                "user_id",
                "observed_at",
            ),
            sa.Index(
                "idx_session_telemetry_provider_observed",
                "provider_id",
                "observed_at",
            ),
            sa.Index("idx_session_telemetry_session_id", "session_id"),
            sa.Index(
                "idx_session_telemetry_provider_channel_observed_bytes",
                "provider_id",
                "channel_id",
                "observed_at",
                "bytes_delta",
            ),
        )
        with op.batch_alter_table(
            "session_telemetry",
            copy_from=target_table,
            recreate="always",
        ) as _batch_op:
            pass
        op.execute(_CHANNEL_WATCH_STATS_V_SQL)

    if has_column:
        # Drop the denormalized column. Wraps in a batch op because
        # SQLite has no native DROP COLUMN before recent versions; the
        # batch-mode rebuild handles it. Same view-drop/recreate
        # guard — dropping a column on the table the view references
        # would otherwise fail.
        op.execute("DROP VIEW IF EXISTS channel_watch_stats_v")
        with op.batch_alter_table("session_telemetry") as batch_op:
            batch_op.drop_column("dispatcharr_username")
        op.execute(_CHANNEL_WATCH_STATS_V_SQL)
