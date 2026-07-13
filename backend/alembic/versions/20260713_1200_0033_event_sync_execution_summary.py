"""auto_creation: persist event_sync summary + kind flag on executions

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-13 12:00:00.000000

enhancedchannelmanager-7wuhd (product footgun): an Event Sync run's execution
details showed ``Streams Evaluated: 0 / Streams Matched: 0 / Channels Created:
0`` while the log showed real activity (already-attached streams). The standard
Pass 1/2 counters don't map to the event_sync model, and the real event_sync
counters (secondary_streams, attached, already_attached, ambiguous_skipped,
unmatched, parse_failed, ...) were only emitted as a TEXT summary line, never
persisted as structured fields on the ``auto_creation_executions`` row.

This migration adds two backward-compatible columns to
``auto_creation_executions``:

* ``event_sync_summary`` — nullable TEXT. JSON array of the per-rule event_sync
  summary dicts the attach phase computes, so the executions UI can render an
  event_sync-aware summary. NULL reads as "no event_sync activity"
  (``get_event_sync_summary()`` returns ``[]``).
* ``is_event_sync`` — NOT NULL BOOLEAN, server_default 0. True only for a PURE
  event_sync run (event_sync rule(s) ran, no standard rules in scope) so the UI
  can swap the standard counter block for the event_sync block reliably even
  after the source rule is deleted (rule_id is ON DELETE SET NULL). Every
  existing row reads as 0 (standard run).

SQLite specifics: both are single ``ALTER TABLE ADD COLUMN`` operations — no
batch rebuild needed. ``server_default="0"`` lets the NOT NULL add succeed on
tables with existing rows (every row gets 0 inline).

Idempotency (mirrors 0021/0028 / bd-5w6jz): long-running installs may already
have a column via ``create_all()`` against a newer ORM snapshot; each add is
guarded so it returns rather than raising ``duplicate column name``.

Bead: ``enhancedchannelmanager-7wuhd``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_TABLE = "auto_creation_executions"
_SUMMARY_COLUMN = "event_sync_summary"
_KIND_COLUMN = "is_event_sync"


def _execution_columns(connection) -> set[str]:
    """Return the set of column names currently on ``auto_creation_executions``.

    Returns the empty set if the table is missing — the guard treats a missing
    table as "no column to add" so this migration becomes a no-op rather than
    raising; the next ``create_all()`` + schema-parity pass surfaces the missing
    table.
    """
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    """Add the nullable ``event_sync_summary`` TEXT + NOT NULL ``is_event_sync``
    BOOLEAN columns.

    Idempotent: each add is skipped if the column already exists (e.g.
    materialised by ``create_all()`` against a newer ORM snapshot on a
    long-running install).
    """
    conn = op.get_bind()
    existing = _execution_columns(conn)

    if _SUMMARY_COLUMN not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_SUMMARY_COLUMN, sa.Text(), nullable=True),
        )

    if _KIND_COLUMN not in existing:
        # server_default="0" so the NOT NULL add succeeds on tables with
        # existing rows (every row gets 0 inline). The ORM declares
        # default=False; the drift test filters modify_default noise.
        op.add_column(
            _TABLE,
            sa.Column(
                _KIND_COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    """Drop the ``event_sync_summary`` and ``is_event_sync`` columns.

    Defensive: inspect first; skip a column that is already absent so a
    partial-rerun cleans up rather than raising. SQLite 3.35+ supports native
    ``ALTER TABLE DROP COLUMN`` via ``op.drop_column`` without a full rebuild.
    """
    conn = op.get_bind()
    existing = _execution_columns(conn)

    if _KIND_COLUMN in existing:
        op.drop_column(_TABLE, _KIND_COLUMN)

    if _SUMMARY_COLUMN in existing:
        op.drop_column(_TABLE, _SUMMARY_COLUMN)
