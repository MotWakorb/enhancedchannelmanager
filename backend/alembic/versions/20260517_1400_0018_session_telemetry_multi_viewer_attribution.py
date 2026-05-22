"""session_telemetry: add multi-viewer attribution columns (Emby/Plex/Jellyfin)

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-17 14:00:00.000000

Multi-viewer attribution backend (bd-r5f0c.9, parent epic bd-r5f0c). Fixes
the structurally-wrong single-viewer model surfaced by the PO: when N users
watch the same channel via the same media server (Emby / Plex / Jellyfin),
ECM only displayed ONE user. Media servers are transcoding proxies — N
upstream viewers share one ECM-side client (the server itself), so the
resolver was correctly matching all N sessions across the tiered match,
then ``_tiebreak_most_recent`` was discarding all but the most-recent winner
before persistence.

This migration adds three NULLABLE TEXT columns to ``session_telemetry``
so the W4 ``BandwidthTracker`` writer (post-bd-r5f0c.9) can persist the
FULL viewer list per source on each per-poll row:

* ``emby_viewers TEXT NULL`` — JSON-encoded
  ``[{"user_id": "abc", "user_name": "Alice"}, ...]`` (or NULL when the
  Emby resolver matched zero viewers for this (channel_id, client_ip)
  pair). The list is sorted ``last_activity_date`` descending so position
  0 is the most-recent viewer — matches the content of the legacy
  ``emby_user_name`` column (preserved post-0018 for back-compat).
* ``plex_viewers TEXT NULL`` — JSON-encoded list of viewer dicts, same
  shape as ``emby_viewers``. Mirrors the Plex resolver's output. Note
  that the Plex resolver pre-bd-r5f0c.9 surfaced only the user_name
  string (no user_id slot exposed by ``/status/sessions``); the
  bd-r5f0c.9 plural variant carries each viewer as
  ``{"user_id": None, "user_name": "..."}`` until the resolver is
  extended (the column tolerates either shape — the JSON-encoded
  ``user_id`` can be null).
* ``jellyfin_viewers TEXT NULL`` — JSON-encoded list, same shape as
  ``emby_viewers``. Mirrors the Jellyfin resolver's output.

Schema posture: denormalized JSON-in-TEXT (same as the existing
``dispatcharr_username`` / ``emby_user_id`` / ``emby_user_name`` and
0017's Plex + Jellyfin singular columns). Application-layer
serialization (``json.dumps`` at write, ``json.loads`` on read) — NOT
SQLAlchemy's JSON column type — so the column type matches every other
attribution column in shape (TEXT) and so the W5 frontend reader can
``json.parse`` the raw string without a driver-specific adapter
contract. If a future query pattern needs SQL-level access to the
viewer list (e.g. ``json_extract(emby_viewers, '$[0].user_id')``),
SQLite's JSON1 extension is already loaded in ``database.py``'s
connect listener and the TEXT column would still satisfy it — no
migration churn needed.

The existing pre-0018 columns (``emby_user_id`` / ``emby_user_name`` /
``plex_user_id`` / ``plex_user_name`` / ``jellyfin_user_id`` /
``jellyfin_user_name``, all carried through 0016 + 0017) are preserved
verbatim — this migration is ADDITIVE ONLY. The writer post-bd-r5f0c.9
populates BOTH the new JSON column AND the legacy singular column on
the same row: the legacy column gets the most-recent viewer's name (or
user_id) for back-compat with Stats v2 aggregations and frontend code
that hasn't migrated to the list yet (W5 lands the list-rendering
frontend separately).

Backfill / pre-migration rows: NOT auto-applied. Pre-migration rows
keep all three new columns NULL — exactly matching the truthful posture
for any non-source-mediated row. The Stats v2 history-cutover policy
("accept the gap") covers this — multi-viewer attribution simply
didn't exist before this fix, and pre-0018 rows honestly admit that.

SQLite specifics:

* Three independent ``ALTER TABLE ADD COLUMN`` statements. Each is a
  plain NULLABLE TEXT column add; SQLite supports this directly without
  batch-rebuild because NULL is the implicit default for nullable
  columns — no row-rewrite cost. Same approach as 0016 + 0017.
* No view drop/recreate dance needed — the existing
  ``channel_watch_stats_v`` view only SELECTs ``channel_id``,
  ``observed_at``, and ``poll_interval_ms``, so adding columns to the
  underlying table cannot disturb it.

bd-5w6jz idempotency: long-running installs may already have some or
all columns via ``create_all()`` from the post-0018 ORM model in
``models.py``, or via a smart-bootstrap fast-path stamp through this
revision. The migration guards each column-add independently — present
columns are skipped, absent columns get added. If all three columns
are already present, the migration returns immediately. This mirrors
the bd-ov5vb (0013), bd-d0ha9 (0015), bd-k026g (0016), and bd-r5f0c.1
(0017) idempotency pattern verbatim.

Index decisions: deferred. The new columns are read-only verbatim
on the read side (the frontend list-renders the JSON-decoded list);
SQL-level filters/aggregations against the JSON content are not on
any current query path. If a future profile shows a hot ``json_extract``
scan, that's a follow-up bead — do not preempt it here.

Pre-merge gate: the new TestMigration0018 class in
``backend/tests/integration/test_alembic_smoke.py`` covers fresh up,
fresh down, full idempotency (all three columns already present), and
partial idempotency (each individual column already present — the
per-column guard must add only the missing ones without raising).

Bead: ``enhancedchannelmanager-r5f0c.9`` (parent epic
``enhancedchannelmanager-r5f0c``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# The three new multi-viewer attribution columns added in this
# migration. Listed once so the upgrade/downgrade/guard helpers stay in
# lockstep — if a future migration adds a fourth source, it lives in
# its own revision, not by mutating this list.
_NEW_COLUMNS = (
    "emby_viewers",
    "plex_viewers",
    "jellyfin_viewers",
)


def _session_telemetry_columns(connection) -> set[str]:
    """Return the set of column names currently on ``session_telemetry``.

    Returns the empty set if the table is missing — every prior
    0006-0017 migration is idempotent and may have skipped its create
    on a fully-drifted DB. The bd-5w6jz guard treats a missing table as
    "no columns to add" so this migration becomes a no-op rather than
    raising; the next ``create_all()`` + ``_assert_schema_matches_models``
    pass surfaces the missing table.
    """
    insp = inspect(connection)
    if not insp.has_table("session_telemetry"):
        return set()
    return {c["name"] for c in insp.get_columns("session_telemetry")}


def upgrade() -> None:
    """Add the three multi-viewer attribution columns to ``session_telemetry``.

    Each ADD COLUMN is independently guarded so a partial-drift state
    (some columns already present from a previous interrupted run, or
    some columns already added via ``create_all()`` against a newer ORM
    snapshot) cleans up rather than raising
    ``OperationalError: duplicate column name``.

    Idempotent (bd-5w6jz): if all three columns are already present,
    return without paying even the inspect-then-skip cost.
    """
    conn = op.get_bind()
    existing = _session_telemetry_columns(conn)
    missing = [c for c in _NEW_COLUMNS if c not in existing]

    if not missing:
        # Fully drifted forward — every target column already exists.
        # Common on long-running installs where ``create_all()`` has
        # materialised the post-0018 ORM shape between two alembic runs.
        return

    for column_name in missing:
        # NULLABLE TEXT, no default; pre-migration rows surface as NULL
        # ("no multi-viewer list" on the frontend, equivalent to "this
        # row predates the multi-viewer integration").
        op.add_column(
            "session_telemetry",
            sa.Column(column_name, sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Drop the three multi-viewer attribution columns.

    Defensive (bd-5w6jz): inspect first; skip whichever column(s) are
    already absent so a partial-rerun cleans up rather than raising.
    The view-drop/recreate dance from migration 0011 is NOT needed —
    ``channel_watch_stats_v`` does not reference any of the dropped
    columns, so SQLite's per-column drop succeeds without it.

    Operators who downgrade past this point lose the multi-viewer
    columns; the W4 ``BandwidthTracker`` writer continues to function
    against the legacy singular ``*_user_name`` columns (the most-recent
    viewer continues to populate those). Read paths fall back to the
    pre-bd-r5f0c.9 single-viewer surface — same as the v0.17.1-0042
    state.
    """
    conn = op.get_bind()
    existing = _session_telemetry_columns(conn)
    present = [c for c in _NEW_COLUMNS if c in existing]

    if not present:
        return

    # SQLite's native ``ALTER TABLE DROP COLUMN`` (3.35+) is supported by
    # SQLAlchemy's plain ``op.drop_column``. Same approach as migrations
    # 0013, 0016, and 0017's downgrade path. Each drop is independently
    # guarded above so a partial-rerun (e.g. one column already dropped
    # manually) is still safe.
    for column_name in present:
        op.drop_column("session_telemetry", column_name)
