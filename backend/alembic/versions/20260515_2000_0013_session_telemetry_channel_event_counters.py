"""session_telemetry: add per-type channel-event counters (reconnect/error/switch)

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-15 20:00:00.000000

Stats v2 channel-event ingest broadening (bd-ov5vb + bd-1x5v0). The pre-0013
``BandwidthTracker._collect_buffer_events`` helper passed ``event_type=buffering``
as a hard filter against Dispatcharr's ``/api/core/system-events/`` endpoint
and bucketed the deduped result into a single ``buffer_event_count`` per
session_telemetry row.

Verified on the PO's live instance (2026-05-15): zero ``channel_buffering``
events in the most recent 100-event Dispatcharr window. The events that
actually represent channel-health problems — ``channel_reconnect`` (8 in
window), ``channel_error`` (4), ``stream_switch`` (3) — were being filtered
out at the API call, so ECM's "Buffering events" metric was a no-op on real
installs.

This migration adds three companion counters next to the existing
``buffer_event_count`` so the broadened ingest can attribute each event
type to its own column without collapsing semantics:

* ``reconnect_event_count`` INTEGER NOT NULL DEFAULT 0
* ``error_event_count`` INTEGER NOT NULL DEFAULT 0
* ``switch_event_count`` INTEGER NOT NULL DEFAULT 0

``buffer_event_count`` is deliberately KEPT — it rolls forward with the
existing semantics (deduped ``channel_buffering`` count) so:

* Pre-0013 read paths (``GET /api/stats/providers/buffering``,
  ``SessionTelemetryProviderDaily.buffer_event_count``,
  ``test_perf_provider_stats``) keep returning the same shape; the values
  collapse to 0 on installs whose Dispatcharr never emitted
  ``channel_buffering`` (which is most installs), and that's the truthful
  posture — those events genuinely did not occur.
* The Providers panel UI (bd-1x5v0) gets a richer "Channel events"
  breakdown via the three new counters without breaking back-compat for
  any downstream consumer.

SQLite specifics:

* Three independent ``ALTER TABLE ADD COLUMN`` statements. SQLite supports
  this directly without batch-rebuild because each is a simple
  NOT NULL DEFAULT 0 column add (the default value populates every
  existing row inline).
* No view drop/recreate dance needed — the existing
  ``channel_watch_stats_v`` view only SELECTs ``channel_id``,
  ``observed_at``, and ``poll_interval_ms`` from ``session_telemetry``,
  so adding columns to the underlying table cannot disturb it.

bd-5w6jz idempotency: long-running installs may already have one or more
of the three columns via ``create_all()`` from the post-0013 ORM model in
``models.py``. The migration guards each column-add independently —
present columns are skipped, absent columns get added. If all three are
already present (e.g. after a smart-bootstrap fast-path stamp through
this revision), the migration returns immediately without paying the
no-op ALTER cost.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0013``
covers fresh up, fresh down, full idempotency (all three columns already
present), and partial idempotency (subset of columns present — the
guard must add only the missing ones without raising).

Bead: ``enhancedchannelmanager-ov5vb`` (paired with ``enhancedchannelmanager-1x5v0``
for the UI relabel that consumes the new counters).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# The three new per-event-type counters added in this migration. Listed
# once so the upgrade/downgrade/guard helpers stay in lockstep — if a
# fourth event type is added in a future migration, it lives in its own
# revision, not by mutating this list.
_NEW_COLUMNS = (
    "reconnect_event_count",
    "error_event_count",
    "switch_event_count",
)


def _session_telemetry_columns(connection) -> set[str]:
    """Return the set of column names currently on ``session_telemetry``.

    Returns the empty set if the table is missing — every prior 0006-0011
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
    """Add the three per-type channel-event counters to ``session_telemetry``.

    Each ADD COLUMN is independently guarded so a partial-drift state
    (one column already present from a previous interrupted run, or two
    columns already added via ``create_all()`` against a newer ORM
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
        # materialised the post-0013 ORM shape between two alembic runs.
        return

    for column_name in missing:
        # NOT NULL DEFAULT 0 — SQLite populates every existing row inline.
        # ``server_default`` mirrors the ORM declaration in models.py so
        # the schema matches the model exactly (the parity check in
        # ``database._assert_schema_matches_models`` will lock this on
        # the next boot after the upgrade lands).
        op.add_column(
            "session_telemetry",
            sa.Column(
                column_name,
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    """Drop the three per-type channel-event counters.

    Defensive (bd-5w6jz): inspect first; skip whichever column(s) are
    already absent so a partial-rerun cleans up rather than raising.
    The view-drop/recreate dance from migration 0011 is NOT needed —
    ``channel_watch_stats_v`` does not reference any of the dropped
    columns, so SQLite's per-column drop succeeds without it.

    Operators who downgrade past this point lose the per-type
    counter columns; the broader ingest helper in
    ``bandwidth_tracker._collect_channel_events`` continues to write
    only to ``buffer_event_count`` (which is preserved by this
    migration's upgrade). Pre-0013 read paths keep working unchanged
    because they only reference ``buffer_event_count``.
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
    # still safe.
    for column_name in present:
        op.drop_column("session_telemetry", column_name)
