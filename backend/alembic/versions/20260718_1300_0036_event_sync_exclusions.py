"""event_sync_exclusions (operator never-attach table, bead ti939.3.5)

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-18 13:00:00.000000

Creates the table backing Event Sync operator exclusions
(bead enhancedchannelmanager-ti939.3.5, epic ti939.3):

* ``event_sync_exclusions`` — one row per "never attach this provider
  stream to that event" decision under one rule. The row's existence IS
  the decision (no state machine); DELETE is the undo.

Why it exists: stateless recompute means a false-positive attach the
operator manually detaches is re-attached on the next run, forever. The
resolver consults this table BEFORE the attach band is honored, so an
excluded pairing can neither attach (threshold OR review-queue accept —
exclusion outranks accept) nor re-enqueue; it reports as
``excluded_by_operator`` in preview and run summaries.

**Keying (HARD security constraint, epic ti939.3, locked at planning):**
identity columns are the content fingerprint — ``(rule_id, provider_id,
stream_name_hash, event_key)`` — NEVER channel or stream IDs. Dispatcharr
stream IDs churn on every provider refresh and channel IDs live only while
an event's channel exists. Fingerprint semantics (LOCALS normalization,
SHA-256, unknown-provider sentinel 0, UTC/dateless event key) are defined
once in ``backend/services/event_sync_review.py`` — churn survival is by
construction. Snapshot ids appear ONLY inside the display-only
``evidence`` JSON (same role as ``event_sync_reviews.evidence``).

Index set:

  uq_event_sync_exclusions_fingerprint  UNIQUE (rule_id, provider_id,
                                        stream_name_hash, event_key)
  idx_event_sync_exclusions_rule        (rule_id)

bd-5w6jz idempotency (0032 pattern): table and indexes are guarded
per-statement via ``has_table`` / ``get_indexes`` inspection so a drifted
DB where ``create_all()`` already materialised the post-0036 ORM shape
passes through as a no-op.

Bead: ``enhancedchannelmanager-ti939.3.5``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# Upgrade/downgrade share one source of truth for the index set.
_EVENT_SYNC_EXCLUSIONS_INDEXES: tuple[tuple[str, list[str], bool], ...] = (
    (
        "uq_event_sync_exclusions_fingerprint",
        ["rule_id", "provider_id", "stream_name_hash", "event_key"],
        True,
    ),
    ("idx_event_sync_exclusions_rule", ["rule_id"], False),
)


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    """Index names currently on *table_name* (empty set if table absent)."""
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


def upgrade() -> None:
    """Create ``event_sync_exclusions`` + its two indexes (guarded)."""
    conn = op.get_bind()

    if not _table_exists(conn, "event_sync_exclusions"):
        op.create_table(
            "event_sync_exclusions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            # Local ECM rule row — FK CASCADE (mirrors event_sync_reviews).
            sa.Column("rule_id", sa.Integer(), nullable=False),
            # M3U account id; 0 = documented unknown-provider sentinel.
            sa.Column("provider_id", sa.Integer(), nullable=False),
            # SHA-256 hex of the LOCALS-normalized secondary stream name.
            sa.Column("stream_name_hash", sa.Text(), nullable=False),
            # Normalized master event identity.
            sa.Column("event_key", sa.Text(), nullable=False),
            # Epoch-ms (ADR-007 / pending_merges convention).
            sa.Column("created_at", sa.Integer(), nullable=False),
            # Optional operator free-text.
            sa.Column("note", sa.Text(), nullable=True),
            # Opaque acting-user DB id; "anonymous" when auth is disabled.
            sa.Column("actor_token_id", sa.Text(), nullable=True),
            # Display-only JSON snapshot — never identity-authoritative.
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["rule_id"],
                ["auto_creation_rules.id"],
                name="fk_event_sync_exclusions_rule",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_event_sync_exclusions"),
        )

    existing = _index_names(conn, "event_sync_exclusions")
    for idx_name, columns, unique in _EVENT_SYNC_EXCLUSIONS_INDEXES:
        if idx_name not in existing:
            op.create_index(
                idx_name, "event_sync_exclusions", columns, unique=unique
            )


def downgrade() -> None:
    """Drop the table + indexes (defensive against partial state)."""
    conn = op.get_bind()

    existing = _index_names(conn, "event_sync_exclusions")
    for idx_name, _columns, _unique in reversed(_EVENT_SYNC_EXCLUSIONS_INDEXES):
        if idx_name in existing:
            op.drop_index(idx_name, table_name="event_sync_exclusions")
    if _table_exists(conn, "event_sync_exclusions"):
        op.drop_table("event_sync_exclusions")
