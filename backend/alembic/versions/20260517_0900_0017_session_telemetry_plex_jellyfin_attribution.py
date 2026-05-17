"""session_telemetry: add Plex + Jellyfin user attribution columns

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-17 09:00:00.000000

Plex + Jellyfin user attribution epic (bd-r5f0c) substrate, at parity
with the shipped Emby attribution from migration 0016 (bd-k026g, parent
epic bd-2cenq). ECM only sees the Dispatcharr stream session's IP. When
users watch via a Plex or Jellyfin server, ALL stream pulls come from
that media server's IP, so Stats can't distinguish individual viewers —
they all collapse to a single "Plex server" / "Jellyfin server" identity
(or get attributed to the admin/API user, same namespace-collision the
Emby work fixed). The Plex + Jellyfin integrations cross-reference each
live upstream session against ECM's active streams and surface the real
upstream username in Watch Time and per-channel attribution, identical
in shape to the Emby cross-ref resolver.

This migration adds four NULLABLE TEXT columns to ``session_telemetry``
so ``BandwidthTracker`` (W4 in this epic) can persist the resolved
upstream attribution on each per-poll row:

* ``plex_user_id TEXT NULL`` — Plex user identifier (Plex account IDs
  are typically integers but Plex serves them as strings in the
  ``/sessions`` payload; carrying them as TEXT avoids the implicit
  parse/cast every read path would otherwise pay and stays aligned with
  the Emby ``emby_user_id`` TEXT choice from migration 0016). Plain
  nullable column, NOT a FK — Plex's user table is upstream and not an
  ECM table, mirroring the established pattern for every other upstream
  identifier on this row (``user_id``, ``provider_id``, ``channel_id``,
  ``stream_id``, ``emby_user_id``).
* ``plex_user_name TEXT NULL`` — denormalized Plex username captured at
  write time from the per-poll Plex ``/status/sessions`` lookup the
  resolver (W2 in this epic) maintains. No read-side joins against any
  Plex endpoint or local table. Mirrors the bd-gsn3r
  ``dispatcharr_username`` and bd-k026g ``emby_user_name``
  denormalization patterns: read paths surface this column verbatim.
* ``jellyfin_user_id TEXT NULL`` — Jellyfin user identifier (GUID, same
  shape as Emby user IDs since Jellyfin forked from Emby and preserves
  the GUID-string identity contract). Stored as TEXT for the same
  reasons as ``emby_user_id`` and ``plex_user_id``. Plain nullable
  column, NOT a FK — Jellyfin's user table is upstream and not an ECM
  table.
* ``jellyfin_user_name TEXT NULL`` — denormalized Jellyfin username
  captured at write time from the per-poll Jellyfin ``/Sessions``
  lookup the resolver (W3 in this epic) maintains. Same
  denormalization rationale as the other three.

Schema posture: denormalized on ``session_telemetry`` rather than split
into separate ``plex_users`` / ``jellyfin_users`` tables. The columns
are NULL for non-Plex / non-Jellyfin rows (the vast majority on most
installs — only the subset whose client IP matches the configured Plex
or Jellyfin server IP AND has a concurrent matching upstream session is
enriched). The Stats v2 query pattern (per-poll projection of user
attribution alongside ``dispatcharr_username`` and ``emby_user_name``)
stays flat — a per-row JOIN to a normalized table would be more
expensive than the per-poll resolver lookup, which already runs once
per poll window and caches in the per-integration session-cache layer.
This mirrors the explicit posture established for migration 0016: keep
upstream attribution denormalized here, let the resolver own the
identity lookup. If a future normalization need surfaces (e.g.
upstream user metadata beyond id+name becomes a hot query), file a
follow-up bead rather than refactor upfront.

Backfill / pre-migration rows: NOT auto-applied. Pre-migration rows
keep all four new columns NULL — exactly matching the truthful posture
for any non-Plex / non-Jellyfin row, since "upstream attribution was
not resolved at write time" and "this row predates the integration"
are operationally equivalent from the read side. The Stats v2
history-cutover policy from bd-skqln.3 ("accept the gap") covers this —
the panel was already attributing all Plex / Jellyfin traffic to the
wrong user before this fix; the pre-migration rows now honestly admit
that. W4 will wire bandwidth_tracker and stats.py against these
columns once W1/W2/W3 all land on dev.

SQLite specifics:

* Four independent ``ALTER TABLE ADD COLUMN`` statements. Each is a
  plain NULLABLE TEXT column add; SQLite supports this directly without
  batch-rebuild because NULL is the implicit default for nullable
  columns — no row-rewrite cost.
* No view drop/recreate dance needed — the existing
  ``channel_watch_stats_v`` view only SELECTs ``channel_id``,
  ``observed_at``, and ``poll_interval_ms`` from ``session_telemetry``,
  so adding columns to the underlying table cannot disturb it. Same
  reasoning as migrations 0013, 0011, and 0016's column-add path.

bd-5w6jz idempotency: long-running installs may already have some or
all columns via ``create_all()`` from the post-0017 ORM model in
``models.py``, or via a smart-bootstrap fast-path stamp through this
revision. The migration guards each column-add independently — present
columns are skipped, absent columns get added. If all four columns are
already present, the migration returns immediately without paying the
no-op ALTER cost. This mirrors the bd-ov5vb (migration 0013), bd-d0ha9
(migration 0015), and bd-k026g (migration 0016) idempotency pattern
verbatim.

Index decisions: deferred. Read-side query shape for the new columns
(equality on a single user_id, or projection only) does not benefit
from a dedicated index at the row-volume scale ADR-007 plans for.
If a future profile shows a hot scan filtered on
``plex_user_id`` / ``jellyfin_user_id`` directly, file a follow-up
bead for the index — do not preempt it here.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0017``
covers fresh up, fresh down, full idempotency (all four columns
already present), and partial idempotency (each individual column
already present — the guard must add only the missing ones without
raising).

Bead: ``enhancedchannelmanager-r5f0c.1`` (parent epic
``enhancedchannelmanager-r5f0c``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# The four new Plex + Jellyfin attribution columns added in this
# migration. Listed once so the upgrade/downgrade/guard helpers stay in
# lockstep — if a fifth attribution column is added in a future
# migration, it lives in its own revision, not by mutating this list.
_NEW_COLUMNS = (
    "plex_user_id",
    "plex_user_name",
    "jellyfin_user_id",
    "jellyfin_user_name",
)


def _session_telemetry_columns(connection) -> set[str]:
    """Return the set of column names currently on ``session_telemetry``.

    Returns the empty set if the table is missing — every prior 0006-0016
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
    """Add the four Plex + Jellyfin user attribution columns to ``session_telemetry``.

    Each ADD COLUMN is independently guarded so a partial-drift state
    (some columns already present from a previous interrupted run, or
    some columns already added via ``create_all()`` against a newer ORM
    snapshot) cleans up rather than raising
    ``OperationalError: duplicate column name``.

    Idempotent (bd-5w6jz): if all four columns are already present, return
    without paying even the inspect-then-skip cost.
    """
    conn = op.get_bind()
    existing = _session_telemetry_columns(conn)
    missing = [c for c in _NEW_COLUMNS if c not in existing]

    if not missing:
        # Fully drifted forward — every target column already exists.
        # Common on long-running installs where ``create_all()`` has
        # materialised the post-0017 ORM shape between two alembic runs.
        return

    for column_name in missing:
        # NULLABLE TEXT, no default; pre-migration rows surface as NULL
        # ("non-Plex / non-Jellyfin viewer" on the frontend) per the
        # accept-the-gap policy described in the module docstring.
        op.add_column(
            "session_telemetry",
            sa.Column(column_name, sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Drop the four Plex + Jellyfin user attribution columns.

    Defensive (bd-5w6jz): inspect first; skip whichever column(s) are
    already absent so a partial-rerun cleans up rather than raising.
    The view-drop/recreate dance from migration 0011 is NOT needed —
    ``channel_watch_stats_v`` does not reference any of the dropped
    columns, so SQLite's per-column drop succeeds without it.

    Operators who downgrade past this point lose the Plex + Jellyfin
    attribution columns; the W4 ``BandwidthTracker`` writer continues to
    function but the resolved upstream user_id/user_name are no longer
    persisted. Read paths fall back to ``dispatcharr_username`` (which
    will surface the Plex / Jellyfin server's API token user — exactly
    the namespace-collision the epic exists to fix, same as the
    pre-0016 state for Emby).
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
    # still safe. Same approach as migrations 0013 and 0016's downgrade
    # path.
    for column_name in present:
        op.drop_column("session_telemetry", column_name)
