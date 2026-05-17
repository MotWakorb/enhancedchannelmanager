"""session_telemetry: add Emby user attribution columns

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-16 17:00:00.000000

Emby user attribution epic (bd-2cenq) substrate. ECM only sees the
Dispatcharr stream session's IP. When users watch via an Emby server, ALL
stream pulls come from the Emby server's IP, so Stats can't distinguish
individual Emby viewers — they all collapse to a single "Emby server"
identity (or get attributed to the admin/API user, see bd-uqbob). The
Emby integration cross-references each live Emby session against ECM's
active streams and surfaces the real Emby username in Watch Time and
per-channel attribution.

This migration adds two NULLABLE TEXT columns to ``session_telemetry`` so
``BandwidthTracker`` (bd-gih6d) can persist the resolved Emby attribution
on each per-poll row:

* ``emby_user_id TEXT NULL`` — Emby user identifier (GUID, e.g.
  ``"a1b2c3d4-1234-5678-90ab-cdef01234567"``). Stored as TEXT because
  Emby user IDs are GUIDs, NOT integers like the Dispatcharr
  ``user_id`` column. Plain nullable column, NOT a FK — Emby's user
  table is upstream and not an ECM table, mirroring the established
  pattern for every other upstream identifier on this row
  (``user_id``, ``provider_id``, ``channel_id``, ``stream_id``).
* ``emby_user_name TEXT NULL`` — denormalized Emby username captured at
  write time from the per-poll Emby ``/Sessions`` lookup the resolver
  (bd-cross-ref-resolver child) maintains. No read-side joins against any
  Emby endpoint or local table. Mirrors the bd-gsn3r ``dispatcharr_username``
  denormalization pattern: read paths surface this column verbatim.

Schema posture: denormalized on ``session_telemetry`` rather than split
into a separate ``emby_users`` table. The columns are NULL for non-Emby
rows (most rows on most installs — only the subset whose client IP
matches the configured Emby server IP AND has a concurrent matching
Emby session is enriched). The Stats v2 query pattern (per-poll
projection of user attribution alongside ``dispatcharr_username``) stays
flat — a per-row JOIN to a normalized table would be more expensive
than the per-poll resolver lookup, which already runs once per poll
window and caches in the bd-emby-session-cache child layer. The bead
text is explicit on this point: "Resist the urge to put emby user info
in a separate table — denormalizing here keeps Stats query patterns
simple, and the columns are NULL for non-Emby rows." If a future
normalization need surfaces (e.g. Emby user metadata beyond id+name
becomes a hot query), file a follow-up bead rather than refactor
upfront.

Backfill / pre-migration rows: NOT auto-applied. Pre-migration rows
keep ``emby_user_id = NULL`` and ``emby_user_name = NULL`` — exactly
matching the truthful posture for any non-Emby row, since "Emby
attribution was not resolved at write time" and "this row predates
the Emby integration" are operationally equivalent from the read
side. The Stats v2 history-cutover policy from bd-skqln.3 ("accept
the gap") covers this — the panel was already attributing all Emby
traffic to the wrong user before this fix; the pre-migration rows
now honestly admit that.

SQLite specifics:

* Two independent ``ALTER TABLE ADD COLUMN`` statements. Each is a
  plain NULLABLE TEXT column add; SQLite supports this directly without
  batch-rebuild because NULL is the implicit default for nullable
  columns — no row-rewrite cost.
* No view drop/recreate dance needed — the existing
  ``channel_watch_stats_v`` view only SELECTs ``channel_id``,
  ``observed_at``, and ``poll_interval_ms`` from ``session_telemetry``,
  so adding columns to the underlying table cannot disturb it. Same
  reasoning as migrations 0013 and 0011's column-add path.

bd-5w6jz idempotency: long-running installs may already have one or
both columns via ``create_all()`` from the post-0016 ORM model in
``models.py``, or via a smart-bootstrap fast-path stamp through this
revision. The migration guards each column-add independently — present
columns are skipped, absent columns get added. If both columns are
already present, the migration returns immediately without paying the
no-op ALTER cost. This mirrors the bd-ov5vb (migration 0013) and
bd-d0ha9 (migration 0015) idempotency pattern verbatim.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0016``
covers fresh up, fresh down, full idempotency (both columns already
present), and partial idempotency (one column present — the guard must
add only the missing one without raising).

Bead: ``enhancedchannelmanager-k026g`` (parent epic
``enhancedchannelmanager-2cenq``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# The two new Emby attribution columns added in this migration. Listed
# once so the upgrade/downgrade/guard helpers stay in lockstep — if a
# third Emby attribution column is added in a future migration, it lives
# in its own revision, not by mutating this list.
_NEW_COLUMNS = (
    "emby_user_id",
    "emby_user_name",
)


def _session_telemetry_columns(connection) -> set[str]:
    """Return the set of column names currently on ``session_telemetry``.

    Returns the empty set if the table is missing — every prior 0006-0015
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


def upgrade() -> None:
    """Add the two Emby user attribution columns to ``session_telemetry``.

    Each ADD COLUMN is independently guarded so a partial-drift state
    (one column already present from a previous interrupted run, or one
    column already added via ``create_all()`` against a newer ORM
    snapshot) cleans up rather than raising
    ``OperationalError: duplicate column name``.

    Idempotent (bd-5w6jz): if both columns are already present, return
    without paying even the inspect-then-skip cost.
    """
    conn = op.get_bind()
    existing = _session_telemetry_columns(conn)
    missing = [c for c in _NEW_COLUMNS if c not in existing]

    if not missing:
        # Fully drifted forward — every target column already exists.
        # Common on long-running installs where ``create_all()`` has
        # materialised the post-0016 ORM shape between two alembic runs.
        return

    for column_name in missing:
        # NULLABLE TEXT, no default; pre-migration rows surface as NULL
        # ("non-Emby viewer" on the frontend) per the accept-the-gap
        # policy described in the module docstring.
        op.add_column(
            "session_telemetry",
            sa.Column(column_name, sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Drop the two Emby user attribution columns.

    Defensive (bd-5w6jz): inspect first; skip whichever column(s) are
    already absent so a partial-rerun cleans up rather than raising.
    The view-drop/recreate dance from migration 0011 is NOT needed —
    ``channel_watch_stats_v`` does not reference either of the dropped
    columns, so SQLite's per-column drop succeeds without it.

    Operators who downgrade past this point lose the Emby attribution
    columns; the bd-gih6d ``BandwidthTracker`` writer continues to
    function but the resolved Emby user_id/user_name are no longer
    persisted. Read paths fall back to ``dispatcharr_username`` (which
    will surface the Emby server's API token user — exactly the
    namespace-collision the epic exists to fix).
    """
    conn = op.get_bind()
    existing = _session_telemetry_columns(conn)
    present = [c for c in _NEW_COLUMNS if c in existing]

    if not present:
        return

    # SQLite's native ``ALTER TABLE DROP COLUMN`` (3.35+) is supported by
    # SQLAlchemy's plain ``op.drop_column``. Wrapping in batch mode would
    # force a full table rebuild for each drop — unnecessary cost when
    # the native path works. Each drop is independently guarded above
    # so a partial-rerun (e.g. one column already dropped manually) is
    # still safe. Same approach as migration 0013's downgrade path.
    for column_name in present:
        op.drop_column("session_telemetry", column_name)
