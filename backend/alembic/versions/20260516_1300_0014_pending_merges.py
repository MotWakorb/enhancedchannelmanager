"""pending_merges + pending_merge_journal (interactive stream dedup, ADR-008 §D8)

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-16 13:00:00.000000

Creates the two tables that back the v0.17.1 interactive stream-to-channel
deduplication epic (bd-1v4ht / BD-C, ADR-008):

* ``pending_merges`` — the queue rows. One row per ``(stream_name,
  candidate_channel_id)`` pair the matcher has surfaced at or above the §D2
  confidence floor. ``status`` is the state machine column
  (``pending`` / ``merged`` / ``dismissed`` — §D3).
* ``pending_merge_journal`` — the audit trail. Every accept / dismiss /
  queue / auto-aged-out action writes a row here (§D6). Each audit field is
  a queryable column, NOT a JSON blob, so the MCP-vs-operator distinction
  the epic asks for is answerable without log scraping.

Schema source-of-truth: ``docs/adr/ADR-008-interactive-stream-dedup.md`` §D8.

Deliberate deviations from ADR-008 §D8 (per PO instruction on the BD-C
implementation brief, 2026-05-16):

* ``pending_merges.trigger_context`` and ``pending_merge_journal.trigger_context``
  do NOT carry a database-level CHECK constraint. ADR-008 §D8 lists both as
  ``CHECK in ('drag_drop','add_stream','m3u_refresh','mcp_tool')``; the PO's
  BD-C brief downgrades them to **write-time enums validated at the
  application layer** so future surface additions (e.g. a hypothetical
  ``bulk_import`` channel) do not require a schema migration. The four
  surface tags remain the canonical set in the ADR; the application layer
  is the gate. ``status`` (pending_merges) and ``action_type``
  (pending_merge_journal) DO carry CHECKs because their value sets are
  load-bearing for the §D3 state machine and §D6 audit semantics
  respectively — those cannot drift without an ADR amendment.

* ``pending_merge_journal.pending_merge_id`` FK uses ``ON DELETE RESTRICT``.
  ADR-008 §D8 declares ``FK → pending_merges.id`` without an ON DELETE
  clause; the PO brief specifies RESTRICT to guard against deletion of a
  queue row whose audit history still has live references. The audit
  substrate is the system of record for "who decided what when" — a
  pending_merges row that has been actioned should not be deletable
  without first dealing with its journal entries (typically: never delete;
  retention is bounded by construction since the queue drains to terminal
  states and rows are not garbage-collected in v0.17.1 per §D10).

Schema field set (per §D8, PO brief verbatim):

  pending_merges:
    id INTEGER PK AUTOINCREMENT
    stream_name TEXT NOT NULL
    group_id INTEGER NULL
    candidate_channel_id TEXT NOT NULL (Dispatcharr UUID; no FK — external)
    confidence REAL NOT NULL (0.0–1.0)
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK in ('pending','merged','dismissed')
    created_at INTEGER NOT NULL (epoch-ms)
    resolved_at INTEGER NULL (epoch-ms; NULL while pending)
    resolution_source TEXT NULL ('operator'|'auto'|'bulk_m3u_hook'|'mcp_tool')
    trigger_context TEXT NOT NULL (app-validated enum — see deviation note)

  pending_merge_journal:
    id INTEGER PK AUTOINCREMENT
    pending_merge_id INTEGER NOT NULL FK → pending_merges.id ON DELETE RESTRICT
    actor_token_id TEXT NOT NULL (opaque token id, NOT a username string)
    action_type TEXT NOT NULL
        CHECK in ('merge_confirmed','merge_dismissed','auto_queued','auto_aged_out')
    source_channel_id TEXT NOT NULL (Dispatcharr UUID)
    target_channel_id TEXT NOT NULL (Dispatcharr UUID)
    confidence_score REAL NOT NULL (captured at action time)
    timestamp_utc INTEGER NOT NULL (epoch-ms)
    trigger_context TEXT NOT NULL (app-validated enum — see deviation note)

Indexes (§D8):

  pending_merges (4 total — 3 plain + 1 partial unique):
    idx_pending_merges_group_created   (group_id, created_at)
    idx_pending_merges_status_created  (status, created_at)
    idx_pending_merges_candidate       (candidate_channel_id)
    uq_pending_merges_active           UNIQUE (stream_name, candidate_channel_id)
                                       WHERE status='pending'

  pending_merge_journal (3 total):
    idx_pending_merge_journal_pending  (pending_merge_id)
    idx_pending_merge_journal_time     (timestamp_utc)
    idx_pending_merge_journal_actor    (actor_token_id)

The partial-unique index ``uq_pending_merges_active`` is the §D5 dedup
invariant: at most one ``pending`` row per ``(stream_name,
candidate_channel_id)`` pair. ``merged`` and ``dismissed`` rows are
historical — the same pair can re-appear after dismissal because the §D10
"not a match" learning store is out of scope for v0.17.1. SQLite supports
partial indexes natively (since 3.8.0, well below the project minimum).
The ``WHERE`` clause is passed via ``sqlite_where`` on the Alembic
``op.create_index`` call.

FK ordering: ``pending_merges`` is created first in the same migration, so
``pending_merge_journal``'s NOT NULL FK to ``pending_merges.id`` is
satisfiable within the single migration transaction. No circular dependency
(ADR-008 §D8).

PRAGMA caveat: SQLite enforces foreign keys only when
``PRAGMA foreign_keys=ON``. ECM's connect listener in ``database.py`` sets
this on every connect (test suite included). Raw ``sqlite3`` CLI sessions
opened for debugging do NOT honour the FK unless the operator sets the
PRAGMA themselves; this is a debugging-only caveat, not a production
correctness issue.

bd-5w6jz idempotency:

Both tables and all seven indexes are guarded against pre-existing
artifacts via per-statement ``has_table`` / ``get_indexes`` inspection. The
migration is safe to re-run in any of these states:

* Fully drifted forward: both tables already exist (e.g. ``create_all()``
  materialised the post-0014 ORM shape on a long-running install while
  ``alembic_version`` was still at 0013). Skip every CREATE.
* Partial drift: one table present, one absent. Create only the missing
  table; for the present table, skip the CREATE TABLE but check each
  index independently.
* Index drift: tables present, some indexes missing (e.g. an aborted prior
  run created the table but crashed before the indexes landed). Add only
  the absent indexes.

This matches the smart-bootstrap fast-path (``_schema_matches_head`` in
``database.py``) which stamps ``alembic_version`` forward when the model
shape is fully materialised — a forced re-run of this migration after
that stamp must be a no-op rather than raising
``OperationalError: table already exists``.

Pre-merge gate: ``backend/tests/integration/test_pending_merges_migration.py``
covers fresh up, fresh down, full drift (both tables pre-created), partial
drift (one table only), index drift, CHECK enforcement (status + action_type),
partial-unique-index enforcement (pending-pending collision raises, but
pending-after-merged is fine), and FK enforcement.

Bead: ``enhancedchannelmanager-6by2n`` (BD-C of the v0.17.1 dedup epic
``enhancedchannelmanager-1v4ht``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# Index list factored out so upgrade/downgrade share one source of truth.
# Plain (non-unique, non-partial) indexes only — the partial unique index is
# handled separately because Alembic's create_index does not represent the
# WHERE clause uniformly across dialects.
_PENDING_MERGES_PLAIN_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("idx_pending_merges_group_created", ["group_id", "created_at"]),
    ("idx_pending_merges_status_created", ["status", "created_at"]),
    ("idx_pending_merges_candidate", ["candidate_channel_id"]),
)

_PENDING_MERGE_JOURNAL_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("idx_pending_merge_journal_pending", ["pending_merge_id"]),
    ("idx_pending_merge_journal_time", ["timestamp_utc"]),
    ("idx_pending_merge_journal_actor", ["actor_token_id"]),
)

# Partial unique index name held as a module constant so the upgrade,
# downgrade, and drift-detection paths all reference the same string.
_UQ_PENDING_MERGES_ACTIVE = "uq_pending_merges_active"


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    """Return the set of index names currently on *table_name*.

    Returns the empty set if the table is missing — the upgrade path
    creates the table first, then layers indexes on top, but a drifted DB
    may have one without the other. Treating "table missing" as "no
    indexes" keeps the per-index guard branch-free.
    """
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


def _create_pending_merges_if_absent(conn) -> None:
    """Create ``pending_merges`` only if it does not already exist.

    Pulled into a helper because the upgrade path mirrors the bd-5w6jz
    pattern: ``create_all()`` may have materialised the table from the
    post-0014 ORM model in ``models.py`` while ``alembic_version`` was
    still at 0013. The CHECK on ``status`` and the named PK make every
    constraint identifier stable across SQLite versions
    (``docs/database_migrations.md`` SQLite-specific gotchas).
    """
    if _table_exists(conn, "pending_merges"):
        return

    op.create_table(
        "pending_merges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Raw stream name as delivered by the M3U / add-stream surface —
        # NOT normalised. Normalisation is applied by the matcher at
        # compare time so the operator sees what the M3U actually
        # delivered (ADR-008 §D8).
        sa.Column("stream_name", sa.Text(), nullable=False),
        # Nullable: the "ungrouped import" case (NULL is treated as an
        # open candidate scope by the matcher per ADR-008 §D8).
        sa.Column("group_id", sa.Integer(), nullable=True),
        # Dispatcharr channel UUID. TEXT, no local FK — channels is not
        # an ECM table (ADR-008 §D4).
        sa.Column("candidate_channel_id", sa.Text(), nullable=False),
        # RapidFuzz token_set_ratio score, 0.0–1.0. Always >= the §D2
        # confidence floor; the application enforces the floor because
        # the floor is configurable per-install via the dedup_threshold
        # setting and a hard DB CHECK would calcify it.
        sa.Column("confidence", sa.Float(), nullable=False),
        # State machine column. CHECK is load-bearing for §D3 transitions.
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        # Epoch-ms (matches session_telemetry / ADR-007 convention).
        sa.Column("created_at", sa.Integer(), nullable=False),
        # Epoch-ms when the row left 'pending'; NULL while pending.
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        # Free-form-by-convention enum: 'operator' / 'auto' /
        # 'bulk_m3u_hook' / 'mcp_tool'. Nullable while pending. No CHECK
        # because the BD-C brief downgrades this to app-validated (see
        # module docstring).
        sa.Column("resolution_source", sa.Text(), nullable=True),
        # Surface that enqueued the row. App-validated enum per the
        # BD-C deviation note in the module docstring.
        sa.Column("trigger_context", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','merged','dismissed')",
            name="ck_pending_merges_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_merges"),
    )


def _create_pending_merge_journal_if_absent(conn) -> None:
    """Create ``pending_merge_journal`` only if absent.

    FK to ``pending_merges.id`` is NOT NULL with ``ON DELETE RESTRICT`` —
    the audit substrate is the system of record for "who decided what
    when"; deletion of a queue row whose journal still references it is
    rejected. See the module docstring's deviation note on the
    ADR-vs-brief direction.
    """
    if _table_exists(conn, "pending_merge_journal"):
        return

    op.create_table(
        "pending_merge_journal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Back-reference to the queue row. ADR-008 §D6: every journal row
        # is created in the context of a pending_merges row.
        sa.Column("pending_merge_id", sa.Integer(), nullable=False),
        # Opaque token identifier — the token's DB id, NOT a username
        # string (§D6 / §D7 audit-actor contract).
        sa.Column("actor_token_id", sa.Text(), nullable=False),
        # What was decided. CHECK is load-bearing for §D6 audit semantics.
        sa.Column("action_type", sa.Text(), nullable=False),
        # Dispatcharr stream UUID that triggered the prompt.
        sa.Column("source_channel_id", sa.Text(), nullable=False),
        # Dispatcharr channel UUID that was the merge candidate.
        sa.Column("target_channel_id", sa.Text(), nullable=False),
        # RapidFuzz score captured at action time, 0.0–1.0. Stored so
        # auditors can answer "what was the confidence when the decision
        # was made?" without needing to reconstruct from a deleted
        # pending_merges row.
        sa.Column("confidence_score", sa.Float(), nullable=False),
        # Epoch-ms, UTC. Matches pending_merges.created_at convention.
        sa.Column("timestamp_utc", sa.Integer(), nullable=False),
        # Surface the decision came in through. App-validated enum per
        # BD-C deviation note (see module docstring).
        sa.Column("trigger_context", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('merge_confirmed','merge_dismissed',"
            "'auto_queued','auto_aged_out')",
            name="ck_pending_merge_journal_action_type",
        ),
        sa.ForeignKeyConstraint(
            ["pending_merge_id"],
            ["pending_merges.id"],
            name="fk_pending_merge_journal_pending_merge",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_merge_journal"),
    )


def upgrade() -> None:
    """Create both tables and their seven indexes.

    Idempotent (bd-5w6jz): every CREATE TABLE / CREATE INDEX is guarded
    against a pre-existing artifact. A drifted DB where create_all() has
    materialised the post-0014 ORM shape will pass through this migration
    as a no-op rather than raising ``OperationalError: ... already exists``.
    """
    conn = op.get_bind()

    # Table 1: pending_merges (must land first — pending_merge_journal's
    # FK references it).
    _create_pending_merges_if_absent(conn)

    # pending_merges plain indexes.
    existing = _index_names(conn, "pending_merges")
    for idx_name, columns in _PENDING_MERGES_PLAIN_INDEXES:
        if idx_name not in existing:
            op.create_index(idx_name, "pending_merges", columns, unique=False)

    # pending_merges partial unique index. Alembic's create_index passes
    # ``sqlite_where`` through to the CREATE INDEX DDL; SQLite has
    # supported partial indexes since 3.8.0 (project minimum is well
    # above this). The text() wrapper avoids parameter-binding
    # ambiguity — partial-index predicates are part of the DDL, not
    # runtime SQL, so they cannot be parametrised.
    if _UQ_PENDING_MERGES_ACTIVE not in existing:
        op.create_index(
            _UQ_PENDING_MERGES_ACTIVE,
            "pending_merges",
            ["stream_name", "candidate_channel_id"],
            unique=True,
            sqlite_where=text("status = 'pending'"),
        )

    # Table 2: pending_merge_journal (FKs pending_merges.id — created
    # above in the same transaction so the FK target exists).
    _create_pending_merge_journal_if_absent(conn)

    existing = _index_names(conn, "pending_merge_journal")
    for idx_name, columns in _PENDING_MERGE_JOURNAL_INDEXES:
        if idx_name not in existing:
            op.create_index(
                idx_name, "pending_merge_journal", columns, unique=False
            )


def downgrade() -> None:
    """Drop both tables and all seven indexes.

    Reverse order of upgrade(): journal first (it depends on
    pending_merges via FK), then pending_merges. Within each table,
    indexes are dropped before the table itself — though SQLite
    cascades index drops on a DROP TABLE automatically, dropping
    explicitly keeps the migration symmetric with upgrade() and makes
    a partial-state rollback (only the journal dropped, pending_merges
    still standing) cleanly recoverable on a subsequent re-run.

    Defensive (bd-5w6jz): skip drops on artifacts that are not present
    so a half-applied prior state still cleans up rather than raising.
    """
    conn = op.get_bind()

    # Journal first — it FK-references pending_merges.
    existing = _index_names(conn, "pending_merge_journal")
    for idx_name, _columns in reversed(_PENDING_MERGE_JOURNAL_INDEXES):
        if idx_name in existing:
            op.drop_index(idx_name, table_name="pending_merge_journal")
    if _table_exists(conn, "pending_merge_journal"):
        op.drop_table("pending_merge_journal")

    # Then pending_merges (now safe because the FK referrer is gone).
    existing = _index_names(conn, "pending_merges")
    if _UQ_PENDING_MERGES_ACTIVE in existing:
        op.drop_index(_UQ_PENDING_MERGES_ACTIVE, table_name="pending_merges")
    for idx_name, _columns in reversed(_PENDING_MERGES_PLAIN_INDEXES):
        if idx_name in existing:
            op.drop_index(idx_name, table_name="pending_merges")
    if _table_exists(conn, "pending_merges"):
        op.drop_table("pending_merges")
