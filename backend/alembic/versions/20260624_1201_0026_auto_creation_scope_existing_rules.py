"""auto_creation_scope_existing_rules

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-24 12:01:00.000000

Data migration (enhancedchannelmanager-orzck / W1, Part 3). Sets
``match_scope_target_group = True`` for every existing auto-creation rule.

Migration 0002 backfilled this flag to False (0) on all pre-existing rows
to preserve the legacy global-lookup behavior. The code default for new
rules is now True (bd-p6ko9, GH #226) — group-scoped lookups are the safe
behavior. This migration brings the existing rows onto that same safe
behavior so they no longer merge same-name channels across groups.

Release-note item (accepted): operators who deliberately disabled scoping
post-0002 will be flipped back to scoped here and must re-disable the
"scope merge lookups to this rule's target group" option if they truly
want the all-groups lookup.

Idempotent: a simple UPDATE that sets the flag to 1 everywhere. Running it
twice is harmless.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Flip existing rules onto the safe group-scoped behavior."""
    op.execute(
        "UPDATE auto_creation_rules SET match_scope_target_group = 1 "
        "WHERE match_scope_target_group = 0"
    )


def downgrade() -> None:
    """Data-only migration — schema is unchanged, so downgrade is a no-op.

    We intentionally do NOT restore the previous per-rule False values: this
    revision does not record which rows it flipped, so an exact inverse is not
    recoverable. The schema is identical before and after, so leaving the data
    as-is on downgrade is safe (no DDL to reverse). See docstring for the
    documented release-note tradeoff.
    """
    pass
