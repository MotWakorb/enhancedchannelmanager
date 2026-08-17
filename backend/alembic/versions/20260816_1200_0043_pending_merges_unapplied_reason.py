"""pending_merges.unapplied_reason — an accepted merge ECM could not apply

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-16 12:00:00.000000

Additive, reversible extension of ``pending_merges`` for bead
``enhancedchannelmanager-i5ic0`` (PO decision 2026-08-16; ADR-008 §D1/§D6
amendment pending).

One column is added:

* ``unapplied_reason`` TEXT NULL — operator-actionable prose recorded when an
  accepted merge could NOT be applied to Dispatcharr, because the stream-name
  lookup matched nothing, matched several, matched one with no usable id, was
  truncated at its page ceiling so uniqueness is unknown, or failed outright.

WHY A FLAG ON ``pending`` AND NOT A FOURTH ``status``. The decision requires
the row to stay in front of the operator and stay retryable. Every consumer of
the queue keys off ``status='pending'`` — the list endpoint, the admin snapshot
the bulk accept targets, the ``ecm_pending_merges_queue_depth`` gauge behind
the subnav count badge, and the §D5 partial unique index
``uq_pending_merges_active``. A fourth status value would have to be added to
all of them (and to the ``ck_pending_merges_status`` CHECK, which on SQLite
means a table rebuild) purely to re-derive the behaviour they already have.
The flag is orthogonal to the state machine instead: ``status`` still answers
"has this left the queue", and ``unapplied_reason`` answers "did the last
accept land upstream". NULL means no accept has failed to apply — which covers
both a never-accepted row and a row whose retry succeeded, since the accept
path clears the column when it resolves.

NULLABLE, so no ``server_default`` and no backfill: every existing row predates
the feature and correctly reads "no accept has failed to apply". SQLite can add
a nullable column to a populated table directly.

Idempotency (house pattern, see 0040/0024): the ADD COLUMN is guarded by a
column-presence check, so a DB that drifted forward via ``create_all()`` passes
through as a no-op rather than raising ``OperationalError: duplicate column``.

Reversibility: ``downgrade()`` drops the column via ``batch_alter_table``
(SQLite cannot DROP COLUMN without table recreation). Dropping it loses only
the reason text; the queue rows themselves are untouched and a re-accept
recomputes the reason.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0043"
down_revision: Union[str, Sequence[str], None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_TABLE = "pending_merges"
_NEW_COLUMN = "unapplied_reason"


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _column_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _column_names(conn, _TABLE)
    if not existing:
        # Table absent (fresh DB materialises the full ORM shape via
        # create_all() and stamps head). Nothing to extend.
        return
    if _NEW_COLUMN in existing:
        # Drift-forward no-op (column already materialised by create_all()).
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_NEW_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    """Reverse 0043: drop unapplied_reason (defensive — skip when absent)."""
    conn = op.get_bind()
    if _NEW_COLUMN not in _column_names(conn, _TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_NEW_COLUMN)
