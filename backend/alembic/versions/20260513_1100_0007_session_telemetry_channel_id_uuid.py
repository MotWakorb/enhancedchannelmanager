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

bd-5w6jz idempotency: long-running installs where ``init_db``'s
``Base.metadata.create_all()`` materialised ``session_telemetry`` from the
ORM model (which has ``channel_id String(64) NOT NULL`` post-migration-0007)
have the column ALREADY at the target shape while ``alembic_version`` lags
at ``0006``. Re-running batch_alter_table.alter_column would unnecessarily
rebuild the table; worse, in the create_all-then-stamp-at-old-rev case the
"alter to a shape it already is" can be a no-op-but-expensive table copy.
We inspect the live column type + nullability and skip the alter when the
column already matches the target ``String(64) NOT NULL``.

Bead: ``enhancedchannelmanager-skqln.3`` (step (a) corrective) +
``enhancedchannelmanager-5w6jz`` (idempotency).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def _channel_id_column(connection) -> dict | None:
    """Return SQLAlchemy inspector dict for ``session_telemetry.channel_id``,
    or ``None`` if the table or the column is missing.

    Used by the bd-5w6jz idempotency guard to decide whether the alter is
    still needed: if ``channel_id`` is already ``String(64) NOT NULL``,
    ``create_all()`` (or a prior partial migration) already shaped it the
    way 0007 wants and the batch alter is a no-op.
    """
    insp = inspect(connection)
    if not insp.has_table("session_telemetry"):
        return None
    for col in insp.get_columns("session_telemetry"):
        if col["name"] == "channel_id":
            return col
    return None


def _channel_id_already_string64_notnull(col: dict) -> bool:
    """True if ``col`` is ``String(64) NOT NULL`` (the upgrade target shape).

    SQLAlchemy returns ``col['type']`` as a ``sqlalchemy.types.*`` instance.
    SQLite's reflection produces ``sa.String`` (or its dialect alias
    ``sa.VARCHAR``) for ``VARCHAR(N)`` — both subclass ``sa.String``, so
    ``isinstance(type_, sa.String)`` covers both. ``length`` mirrors the
    declared ``VARCHAR(64)``. ``nullable`` comes straight from the column dict.
    """
    type_ = col["type"]
    return (
        isinstance(type_, sa.String)
        and getattr(type_, "length", None) == 64
        and col.get("nullable") is False
    )


def _channel_id_already_integer_nullable(col: dict) -> bool:
    """True if ``col`` is ``INTEGER NULL`` (the downgrade target shape).

    Mirror of ``_channel_id_already_string64_notnull`` for the downgrade
    path's idempotency guard.
    """
    type_ = col["type"]
    return isinstance(type_, sa.Integer) and col.get("nullable") is True


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

    Idempotent (bd-5w6jz): skip the alter if the column is already at the
    target ``String(64) NOT NULL`` shape (e.g. because ``create_all()``
    materialised the table from the post-0007 ORM model on a long-running
    install before Alembic caught up).
    """
    conn = op.get_bind()
    col = _channel_id_column(conn)
    if col is None:
        # Table missing entirely — 0006 is also idempotent and may have
        # skipped its create on a fully-drifted DB. Nothing to alter; the
        # next step's create_all() / canary check will surface it.
        return
    if _channel_id_already_string64_notnull(col):
        return

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

    Idempotent (bd-5w6jz): skip the alter if the column is already
    ``INTEGER NULL`` (a partially-applied prior downgrade, or the table
    not yet touched by 0007).
    """
    conn = op.get_bind()
    col = _channel_id_column(conn)
    if col is None:
        return
    if _channel_id_already_integer_nullable(col):
        return

    with op.batch_alter_table("session_telemetry", schema=None) as batch_op:
        batch_op.alter_column(
            "channel_id",
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
