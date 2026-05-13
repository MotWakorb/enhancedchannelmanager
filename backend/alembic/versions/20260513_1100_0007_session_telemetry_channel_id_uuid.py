"""session_telemetry_channel_id_uuid

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-13 11:00:00.000000

Corrects ``session_telemetry.channel_id`` from ``INTEGER NULL`` to
``VARCHAR(64) NOT NULL``.

Why this correction exists (skqln.3 step (a) follow-up — bd-skqln.3):

Migration 0006 (skqln.2) declared ``channel_id INTEGER`` based on a
schema-modeling oversight. Every other channel-keyed table in ECM uses
``String(64)`` Dispatcharr UUIDs — ``channel_watch_stats`` (line 125 of
``backend/models.py``), ``channel_bandwidth``, ``channel_popularity_scores``,
and ``unique_client_connections`` all key off the same UUID shape. The
integer column was inconsistent with the rest of the schema and with the
writer (``BandwidthTracker._write_session_telemetry``), which keys
channels by Dispatcharr UUID string.

The mistake was caught when step (b) (the ``channel_watch_stats_v`` view)
was being designed and it became clear that a ``GROUP BY channel_id``
returning equivalent-shape rows to ``channel_watch_stats`` (which stores
String UUIDs) was impossible against an ``INTEGER`` column.

This migration is **safe** because ``session_telemetry`` has zero rows
at this point: the writer is gated behind ``ECM_SESSION_TELEMETRY_WRITE_ENABLED``
(default OFF) and the feature flag has not yet been enabled in any
environment. We use SQLite batch mode (``batch_alter_table`` →
``alter_column``) so the table is recreated with the new column type;
no row data is lost (because there is none).

NOT NULL is correct here: every writer of a ``session_telemetry`` row
(``BandwidthTracker`` today; future buffer-event ingest in skqln.15) is
tied to an active channel by definition — a row without a channel has
no meaning in the rollup query that step (b) is about to introduce.

Reversible: ``downgrade()`` flips the column back to ``INTEGER NULL`` for
parity with migration 0006's original shape. The downgrade is informational
only — production never runs it (forward-only deploy policy) — but the
alembic round-trip smoke test exercises it.

Bead: ``enhancedchannelmanager-skqln.3`` (step (a) corrective).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    """Alter session_telemetry.channel_id: INTEGER NULL → VARCHAR(64) NOT NULL.

    SQLite has no native ``ALTER COLUMN`` for type or nullability changes,
    so we use Alembic's batch mode which rebuilds the table with the new
    column shape. The table is empty (writer gated behind a default-OFF
    feature flag), so no row data is at risk.

    The composite covering index
    ``idx_session_telemetry_provider_channel_observed_bytes`` references
    ``channel_id`` and is automatically rebuilt by SQLite's table-copy
    semantics during batch mode — we do not touch it explicitly.
    """
    with op.batch_alter_table("session_telemetry", schema=None) as batch_op:
        batch_op.alter_column(
            "channel_id",
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            existing_nullable=True,
            nullable=False,
        )


def downgrade() -> None:
    """Revert session_telemetry.channel_id back to INTEGER NULL.

    Restores the migration 0006 shape so the round-trip smoke test in
    ``backend/tests/integration/test_alembic_smoke.py`` can downgrade
    through this revision cleanly. Forward-only deploy in production
    means this path never runs there; it exists for round-trip parity.
    """
    with op.batch_alter_table("session_telemetry", schema=None) as batch_op:
        batch_op.alter_column(
            "channel_id",
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
