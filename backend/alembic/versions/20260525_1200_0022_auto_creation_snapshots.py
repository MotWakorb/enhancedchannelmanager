"""auto_creation_snapshots: pre-run channel<->stream snapshot table (ADR-010 §D6)

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-25 12:00:00.000000

Creates ``auto_creation_snapshots`` — the point-in-time, pre-run snapshot of
the manual (non-Dispatcharr-auto-created) channel<->stream state captured
BEFORE an auto-creation execution mutates anything, to enable a full
whole-run revert (ADR-010, Phase 2 / bead enhancedchannelmanager-uc51o.2).

Schema (ADR-010 §D6):

  auto_creation_snapshots:
    id            INTEGER PK AUTOINCREMENT
    execution_id  INTEGER NOT NULL FK -> auto_creation_executions.id
                  ON DELETE CASCADE, UNIQUE (1:1 to the execution)
    snapshot_time DATETIME NOT NULL (Python default datetime.utcnow)
    channel_count INTEGER  NOT NULL (Python default=0; denormalized for cheap
                  size reporting without parsing the BLOB; feeds the §D7 metric)
    channels_data TEXT NULL (JSON: {"channels": [{id, name, channel_group_id,
                  epg_data_id, tvg_id, stream_ids:[int]}, ...]} — STREAM IDS
                  ONLY per §D1; never URLs, which embed XC credentials)
    created_at    DATETIME NOT NULL

Indexes (ADR-010 §D6):
    uq_auto_snapshot_execution  UNIQUE (execution_id)
        — the 1:1 invariant AND the O(1) has_snapshot existence lookup.
    idx_auto_snapshot_time      (snapshot_time DESC)
        — the §D7 age-window prune scan.

CASCADE rationale (§D6): a pruned/deleted execution row takes its snapshot
with it. The snapshot is a recoverable convenience artifact (Dispatcharr is
the source of truth — §D8), so the FK delete-cascade and the downgrade DROP
are both acceptable losses.

Why a new table, not a reuse of ``m3u_snapshots`` (PO decision (f), §D6):
``M3USnapshot`` is a per-M3U-account playlist-change-detection artifact with
entirely different semantics, lifecycle, and FK. Reusing it would be a
naming/semantic collision. ``M3USnapshot`` is the structural precedent for
the retention-index pattern (§D7), NOT the table to overload.

Named UNIQUE constraint (``uq_auto_snapshot_execution``) per the SQLite
gotchas in ``docs/database_migrations.md`` — auto-generated UNIQUE/CHECK
constraint names are unstable across SQLite versions.

bd-5w6jz idempotency:

The CREATE TABLE and both CREATE INDEX statements are guarded against a
pre-existing artifact via per-statement ``has_table`` / ``get_indexes``
inspection. The migration is safe to re-run in any of these states:

* Fully drifted forward: the table already exists (e.g. ``create_all()``
  materialised the post-0022 ORM shape on a long-running install while
  ``alembic_version`` was still at 0021). Skip the CREATE TABLE and check
  each index independently.
* Index drift: table present, an index missing (an aborted prior run that
  created the table but crashed before an index landed). Add only the
  absent index.

This matches the smart-bootstrap fast-path (``_schema_matches_head`` in
``database.py``) which stamps ``alembic_version`` forward when the ORM shape
is already fully materialised — a forced re-run after that stamp must be a
no-op rather than raising ``OperationalError: table already exists``.

downgrade() drops the table (and its indexes); the data is a recoverable
convenience artifact, not source-of-truth (Dispatcharr is, §D8), so the drop
is acceptable. Defensive: skip drops on artifacts that are not present so a
half-applied prior state still cleans up rather than raising.

Pre-merge gate: ``backend/tests/integration/test_auto_creation_snapshot_migration.py``
covers fresh up, fresh down, full drift (table pre-created), and index drift.
``tests/unit/test_alembic_baseline.py`` locks ORM/migration parity (the new
table is picked up automatically by the metadata-drift comparison).

Bead: ``enhancedchannelmanager-uc51o.2`` (Phase 2 of epic
``enhancedchannelmanager-uc51o``; ADR-010 §D6).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_TABLE = "auto_creation_snapshots"
_UQ_EXECUTION = "uq_auto_snapshot_execution"
_IDX_TIME = "idx_auto_snapshot_time"


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    """Return the set of index names currently on *table_name*.

    Returns the empty set if the table is missing — the upgrade path creates
    the table first then layers indexes on top, but a drifted DB may have one
    without the other. Treating "table missing" as "no indexes" keeps the
    per-index guard branch-free.
    """
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


def _create_table_if_absent(conn) -> None:
    """Create ``auto_creation_snapshots`` only if it does not already exist.

    bd-5w6jz: ``create_all()`` may have materialised the table from the
    post-0022 ORM model in ``models.py`` while ``alembic_version`` was still
    at 0021. The named UNIQUE constraint keeps its identifier stable across
    SQLite versions (``docs/database_migrations.md`` SQLite gotchas).
    """
    if _table_exists(conn, _TABLE):
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # 1:1 to the execution whose pre-run state this captures. CASCADE so
        # a pruned/deleted execution row takes its snapshot with it (§D6).
        sa.Column("execution_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_time", sa.DateTime(), nullable=False),
        # Denormalized channel count for cheap size reporting without parsing
        # the BLOB. NOT NULL with the Python-side default=0 declared on the
        # ORM column. No server_default: this is a brand-new table created in
        # one statement, so there are no pre-existing rows that a NOT NULL add
        # would orphan — a server_default would buy nothing and would show as
        # modify_default drift against the ORM (which mirrors the
        # M3USnapshot.total_streams precedent: default=0, no server_default).
        sa.Column("channel_count", sa.Integer(), nullable=False),
        # JSON TEXT payload — STREAM IDS ONLY (§D1).
        sa.Column("channels_data", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["auto_creation_executions.id"],
            name="fk_auto_snapshot_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auto_creation_snapshots"),
        sa.UniqueConstraint("execution_id", name=_UQ_EXECUTION),
    )


def upgrade() -> None:
    """Create ``auto_creation_snapshots`` and its time index.

    The UNIQUE(execution_id) constraint is declared inline in the CREATE
    TABLE (it doubles as the FK-lookup index). The DESC time index is created
    separately. Both the table CREATE and the index CREATE are guarded
    (bd-5w6jz) so a drifted DB passes through as a no-op rather than raising
    ``OperationalError: ... already exists``.
    """
    conn = op.get_bind()

    _create_table_if_absent(conn)

    existing = _index_names(conn, _TABLE)
    if _IDX_TIME not in existing:
        # snapshot_time DESC — the age-window prune scan (§D7).
        op.create_index(
            _IDX_TIME,
            _TABLE,
            [sa.text("snapshot_time DESC")],
            unique=False,
        )


def downgrade() -> None:
    """Drop ``auto_creation_snapshots`` (and its indexes).

    The snapshot data is a recoverable convenience artifact, not
    source-of-truth (Dispatcharr is — §D8), so the drop is acceptable.
    Defensive (bd-5w6jz): skip drops on artifacts that are not present so a
    half-applied prior state still cleans up rather than raising. SQLite
    cascades index drops on DROP TABLE, but the explicit index drop keeps the
    migration symmetric with upgrade() and a partial-state rollback clean.
    """
    conn = op.get_bind()

    existing = _index_names(conn, _TABLE)
    if _IDX_TIME in existing:
        op.drop_index(_IDX_TIME, table_name=_TABLE)

    if _table_exists(conn, _TABLE):
        op.drop_table(_TABLE)
