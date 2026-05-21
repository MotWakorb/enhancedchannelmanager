"""auto_creation: add explicit match_scope_group_id to rules

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-20 21:00:00.000000

GH #298 (bd-kncun): "When merge lookup scope is ticked, I can't set the target
group in the GUI so it just matches against any group."

Migration 0002 added ``match_scope_target_group`` (the rule-level checkbox that
scopes existing-channel name lookups to "this rule's target group"). But the
only Target Group control lives on the Create Channel action — a Merge-Streams-
only rule had no group to scope to, so the lookup silently fell back to ALL
groups, exactly as the reporter described.

This migration adds a nullable ``match_scope_group_id`` column to
``auto_creation_rules`` so the operator can pin the scope group explicitly in
the rule builder, independent of any action's Target Group. The executor
enforces it across BOTH create_channel and merge_streams name lookups when
``match_scope_target_group`` is on.

Backwards-compatible: the column defaults to NULL for every existing row, which
preserves the prior behavior exactly — create_channel derives the scope from
the action's effective group_id, and merge_streams stays group-agnostic. No
backfill is needed (NULL is the correct value for all existing rules).

SQLite specifics:

* A single ``ALTER TABLE ADD COLUMN`` for a NULLABLE column with no default.
  SQLite supports this directly without a batch-rebuild — a nullable add does
  not require a default value, and every existing row gets NULL inline.

bd-5w6jz idempotency: long-running installs may already have the column via
``create_all()`` from the post-0019 ORM model in ``models.py``. The column-add
is guarded — if the column is already present the migration returns
immediately without raising ``OperationalError: duplicate column name``.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py::TestMigration0019``
covers fresh up, fresh down, and idempotency (column already present).

Bead: ``enhancedchannelmanager-kncun``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_NEW_COLUMN = "match_scope_group_id"


def _auto_creation_rules_columns(connection) -> set[str]:
    """Return the set of column names currently on ``auto_creation_rules``.

    Returns the empty set if the table is missing — the bd-5w6jz guard treats
    a missing table as "no column to add" so this migration becomes a no-op
    rather than raising; the next ``create_all()`` + schema-parity pass
    surfaces the missing table.
    """
    insp = inspect(connection)
    if not insp.has_table("auto_creation_rules"):
        return set()
    return {c["name"] for c in insp.get_columns("auto_creation_rules")}


def upgrade() -> None:
    """Add the nullable ``match_scope_group_id`` column to ``auto_creation_rules``.

    Idempotent (bd-5w6jz): if the column is already present (e.g. materialised
    by ``create_all()`` against a newer ORM snapshot on a long-running install),
    return without raising ``OperationalError: duplicate column name``.
    """
    conn = op.get_bind()
    if _NEW_COLUMN in _auto_creation_rules_columns(conn):
        return

    # Nullable column, no server_default — NULL is the correct value for every
    # existing rule (preserves pre-GH-298 behavior). The ORM declaration in
    # models.py matches (``Column(Integer, nullable=True)``); the drift test in
    # ``test_alembic_baseline.py`` locks this on the next boot.
    op.add_column(
        "auto_creation_rules",
        sa.Column(_NEW_COLUMN, sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the ``match_scope_group_id`` column.

    Defensive (bd-5w6jz): inspect first; skip if the column is already absent
    so a partial-rerun cleans up rather than raising. SQLite 3.35+ supports
    native ``ALTER TABLE DROP COLUMN`` via ``op.drop_column`` without a full
    table rebuild.

    Operators who downgrade past this point lose any explicitly-pinned scope
    group; rules fall back to the pre-GH-298 behavior (create_channel derives
    the scope from the action group; merge_streams is group-agnostic).
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _auto_creation_rules_columns(conn):
        return

    op.drop_column("auto_creation_rules", _NEW_COLUMN)
