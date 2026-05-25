"""normalization: add require_delimiter to rules

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-23 12:00:00.000000

bd-0emgo.2 (production normalization data-corruption bug, follow-up): the
generic-word guard added on this branch keeps "NFL Network" from collapsing to
the bare word "Network", but "NFL RedZone" -> "RedZone" is still wrong because
NFL RedZone is a BRAND, not "NFL" + category column. The distinguishing signal
is the separator: a strong delimiter after the league tag ("NFL: X", "NFL | X",
"NFL - X") means "NFL" is a category prefix (strip it); a bare space ("NFL
RedZone", "NFL Network", "NFL Sunday Ticket") means "NFL" is part of the brand
(keep it).

This migration adds a per-rule ``require_delimiter`` boolean to
``normalization_rules``. When True, the rule's tag-group prefix/suffix match
requires a strong delimiter rather than a bare space. The boot migration
``normalization_migration.set_league_strip_require_delimiter`` flips it True on
the existing "Strip League Prefixes" rule so upgrading operators get the fix;
all other rules (country/quality/timezone/etc.) keep the default False and so
continue stripping on a bare space ("US ESPN" -> "ESPN" still works).

Backwards-compatible: defaults to False (0) for every existing row, preserving
the prior bare-space-accepting behavior. The server_default is supplied so the
NOT NULL add succeeds on tables that already have rows; the ORM model declares
``default=False`` to match. The schema-drift test in
``tests/unit/test_alembic_baseline.py`` filters ``modify_default`` /
``modify_nullable`` noise, so the server_default vs. app-default difference is
not flagged as structural drift.

SQLite specifics:

* A single ``ALTER TABLE ADD COLUMN`` with a server_default. SQLite supports a
  NOT NULL add when a default is provided — every existing row gets 0 inline,
  no batch rebuild needed.

bd-5w6jz idempotency: long-running installs may already have the column via
``create_all()`` from the post-0020 ORM model. The column-add is guarded — if
the column is already present the migration returns immediately without raising
``OperationalError: duplicate column name``.

Pre-merge gate: ``backend/tests/integration/test_alembic_smoke.py`` covers the
baseline round-trip; ``tests/unit/test_alembic_baseline.py`` locks ORM/migration
parity on the next boot.

Bead: ``enhancedchannelmanager-0emgo.2``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_NEW_COLUMN = "require_delimiter"


def _normalization_rules_columns(connection) -> set[str]:
    """Return the set of column names currently on ``normalization_rules``.

    Returns the empty set if the table is missing — the bd-5w6jz guard treats
    a missing table as "no column to add" so this migration becomes a no-op
    rather than raising; the next ``create_all()`` + schema-parity pass
    surfaces the missing table.
    """
    insp = inspect(connection)
    if not insp.has_table("normalization_rules"):
        return set()
    return {c["name"] for c in insp.get_columns("normalization_rules")}


def upgrade() -> None:
    """Add the NOT NULL ``require_delimiter`` column (default False).

    Idempotent (bd-5w6jz): if the column is already present (e.g. materialised
    by ``create_all()`` against a newer ORM snapshot on a long-running install),
    return without raising ``OperationalError: duplicate column name``.
    """
    conn = op.get_bind()
    if _NEW_COLUMN in _normalization_rules_columns(conn):
        return

    # server_default="0" so the NOT NULL add succeeds on tables with existing
    # rows (every row gets False inline). The ORM declares default=False; the
    # drift test filters modify_default noise.
    op.add_column(
        "normalization_rules",
        sa.Column(
            _NEW_COLUMN,
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    """Drop the ``require_delimiter`` column.

    Defensive (bd-5w6jz): inspect first; skip if the column is already absent
    so a partial-rerun cleans up rather than raising. SQLite 3.35+ supports
    native ``ALTER TABLE DROP COLUMN`` via ``op.drop_column`` without a full
    table rebuild.

    Operators who downgrade past this point lose the per-rule delimiter
    requirement; the league strip falls back to bare-space matching (the
    pre-fix "NFL RedZone" -> "RedZone" behavior).
    """
    conn = op.get_bind()
    if _NEW_COLUMN not in _normalization_rules_columns(conn):
        return

    op.drop_column("normalization_rules", _NEW_COLUMN)
