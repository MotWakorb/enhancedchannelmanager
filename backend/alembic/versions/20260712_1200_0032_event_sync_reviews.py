"""event_sync_reviews (ambiguous-band review queue, bead ti939.3.2)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-12 12:00:00.000000

Creates the table backing the Event Sync ambiguous-match review queue
(bead enhancedchannelmanager-ti939.3.2, epic ti939.3):

* ``event_sync_reviews`` — one row per reviewable (secondary stream
  identity, master event identity) PAIRING under one rule, carrying both
  the open question (``status='pending'``) and, after the operator acts,
  the durable decision (``accepted`` / ``rejected`` / ``superseded``).

**Keying (HARD security constraint, epic ti939.3, locked at planning):**
identity columns are the content fingerprint — ``(rule_id, provider_id,
stream_name_hash, event_key)`` — NEVER channel or stream IDs. Dispatcharr
stream IDs churn on every provider refresh and channel IDs live only while
an event's channel exists; an ID-keyed queue would refill with duplicates
of already-answered questions after every refresh. Fingerprint semantics
(LOCALS normalization, SHA-256, unknown-provider sentinel 0, UTC event
key) are defined once in ``backend/services/event_sync_review.py``.

Phase-1 scoping note: the root epic's "NO new tables" decision (PO
decision 5, stateless recompute) was scoped to Phase 1 — this Phase 2 bead
explicitly reuses the ADR-008 pending-merges review PATTERN, which means a
queue table. The constraint that CARRIES FORWARD is about keying (content
fingerprints, never IDs), which this schema enforces via the NOT NULL
fingerprint columns + unique index. Snapshot channel/stream ids appear
ONLY inside the display-only ``evidence`` JSON and are re-verified against
live Dispatcharr before any use.

Deliberate deviations from the ADR-008 §D8 twin-table precedent:

* NO companion journal table. ADR-008 §D6 predates the mutation_source-
  aware shared ``journal_entries`` substrate; review accepts/rejects and
  queue-driven attaches journal there under category ``event_sync``
  (queryable via the existing journal API — no log scraping).
* ``status`` DOES carry a CHECK (load-bearing state machine, mirroring
  ``ck_pending_merges_status``); ``resolution_source`` is app-validated
  with no CHECK (mirroring the BD-C trigger_context deviation — new
  sources must not require a schema migration).
* FK to ``auto_creation_rules`` with ON DELETE CASCADE: unlike
  ``candidate_channel_id`` (an external Dispatcharr entity, no FK), the
  rule IS a local ECM row, and a decision is meaningless without the
  rule's parse patterns — deleting the rule deletes its queue.

Index set:

  uq_event_sync_reviews_fingerprint   UNIQUE (rule_id, provider_id,
                                      stream_name_hash, event_key)
  idx_event_sync_reviews_status_created  (status, created_at)
  idx_event_sync_reviews_rule_status     (rule_id, status)

The unique index is FULL (not partial like ``uq_pending_merges_active``):
answered rows persist as the decision record, so "the queue must not
refill with already-answered questions" is DB-enforced — a re-encountered
fingerprint can only refresh the existing row, never insert a duplicate.

bd-5w6jz idempotency (0014 pattern): the table and all three indexes are
guarded per-statement via ``has_table`` / ``get_indexes`` inspection, so a
drifted DB where ``create_all()`` already materialised the post-0032 ORM
shape passes through as a no-op instead of raising
``OperationalError: ... already exists``.

Pre-merge gate: ``backend/tests/integration/test_event_sync_reviews_migration.py``
covers fresh up, fresh down, full drift, index drift, CHECK enforcement,
unique-fingerprint enforcement and FK CASCADE.

Bead: ``enhancedchannelmanager-ti939.3.2``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# Upgrade/downgrade share one source of truth for the index set.
_EVENT_SYNC_REVIEWS_INDEXES: tuple[tuple[str, list[str], bool], ...] = (
    (
        "uq_event_sync_reviews_fingerprint",
        ["rule_id", "provider_id", "stream_name_hash", "event_key"],
        True,
    ),
    ("idx_event_sync_reviews_status_created", ["status", "created_at"], False),
    ("idx_event_sync_reviews_rule_status", ["rule_id", "status"], False),
)


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    """Index names currently on *table_name* (empty set if table absent)."""
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


def upgrade() -> None:
    """Create ``event_sync_reviews`` + its three indexes (guarded)."""
    conn = op.get_bind()

    if not _table_exists(conn, "event_sync_reviews"):
        op.create_table(
            "event_sync_reviews",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            # Local ECM rule row — FK CASCADE (see module docstring).
            sa.Column("rule_id", sa.Integer(), nullable=False),
            # M3U account id; 0 = documented unknown-provider sentinel.
            # NOT NULL because SQLite unique indexes treat NULLs as
            # distinct, which would break the dedup invariant.
            sa.Column("provider_id", sa.Integer(), nullable=False),
            # SHA-256 hex of the LOCALS-normalized secondary stream name.
            sa.Column("stream_name_hash", sa.Text(), nullable=False),
            # "<cleaned parsed title>|<UTC ISO start>" of the MASTER side.
            sa.Column("event_key", sa.Text(), nullable=False),
            sa.Column(
                "status",
                sa.Text(),
                server_default=sa.text("'pending'"),
                nullable=False,
            ),
            # Epoch-ms (ADR-007 / pending_merges convention).
            sa.Column("created_at", sa.Integer(), nullable=False),
            # Epoch-ms of the last run that re-encountered the pairing.
            sa.Column("last_seen_at", sa.Integer(), nullable=False),
            # Epoch-ms when the row left 'pending'; NULL while pending.
            sa.Column("resolved_at", sa.Integer(), nullable=True),
            # 'operator' / 'superseded_by_accept' — app-validated, no CHECK.
            sa.Column("resolution_source", sa.Text(), nullable=True),
            # Opaque acting-user DB id; NULL while pending.
            sa.Column("actor_token_id", sa.Text(), nullable=True),
            # Display-only JSON snapshot — never identity-authoritative.
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending','accepted','rejected','superseded')",
                name="ck_event_sync_reviews_status",
            ),
            sa.ForeignKeyConstraint(
                ["rule_id"],
                ["auto_creation_rules.id"],
                name="fk_event_sync_reviews_rule",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_event_sync_reviews"),
        )

    existing = _index_names(conn, "event_sync_reviews")
    for idx_name, columns, unique in _EVENT_SYNC_REVIEWS_INDEXES:
        if idx_name not in existing:
            op.create_index(
                idx_name, "event_sync_reviews", columns, unique=unique
            )


def downgrade() -> None:
    """Drop the table + indexes (defensive against partial state)."""
    conn = op.get_bind()

    existing = _index_names(conn, "event_sync_reviews")
    for idx_name, _columns, _unique in reversed(_EVENT_SYNC_REVIEWS_INDEXES):
        if idx_name in existing:
            op.drop_index(idx_name, table_name="event_sync_reviews")
    if _table_exists(conn, "event_sync_reviews"):
        op.drop_table("event_sync_reviews")
