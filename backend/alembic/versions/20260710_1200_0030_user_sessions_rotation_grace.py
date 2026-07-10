"""user_sessions_rotation_grace

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-10 12:00:00.000000

Adds two nullable columns to ``user_sessions`` for the refresh-token
rotation grace window (bd-x67qe — cross-tab refresh race hard-logout):

* ``prior_refresh_token_hash`` (String(255), nullable) — SHA256 hash of the
  refresh token that was just rotated away. For a short grace window after
  rotation, ``POST /api/auth/refresh`` still accepts this predecessor and
  answers idempotently (a fresh access token bound to the SAME session, no
  second rotation) so two browser tabs racing the same rotation don't
  hard-logout the loser.
* ``rotated_at`` (DateTime, nullable) — when the rotation happened; bounds
  the grace window. Never extends ``expires_at``.

Only one generation is kept: every normal rotation overwrites both fields,
so a graced token can never chain to an older one. Both columns are
intentionally nullable — legacy sessions (never rotated since this deploy)
read back NULL and simply have no grace predecessor.

A single-column index ``idx_session_prior_token_hash`` backs the grace-path
lookup (``WHERE prior_refresh_token_hash = :hash AND is_revoked = 0``).

bd-5w6jz idempotency: a long-running install may already have the columns
and index via ``Base.metadata.create_all()`` from the post-0030 ORM model
(the create_all path can win the race against ``alembic upgrade``). Every
add is guarded — if the artifact already exists the migration skips it
instead of raising ``OperationalError``. The smoke test
``tests/integration/test_alembic_smoke.py`` (create_all + stamp/upgrade) and
the drift test ``tests/unit/test_alembic_baseline.py`` cover this.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "user_sessions"
_PRIOR_HASH_COLUMN = "prior_refresh_token_hash"
_ROTATED_AT_COLUMN = "rotated_at"
_INDEX = "idx_session_prior_token_hash"


def _table_columns(connection) -> set[str]:
    """Column names on ``user_sessions`` (empty set if the table is absent)."""
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def _table_indexes(connection) -> set[str]:
    """Index names on ``user_sessions`` (empty set if the table is absent)."""
    insp = inspect(connection)
    if not insp.has_table(_TABLE):
        return set()
    return {ix["name"] for ix in insp.get_indexes(_TABLE)}


def upgrade() -> None:
    """Add the nullable grace-window columns + the prior-hash index.

    Idempotent (bd-5w6jz): if the columns/index were already materialised by
    ``create_all()`` against the newer ORM snapshot, skip rather than raise.
    """
    conn = op.get_bind()
    columns = _table_columns(conn)
    if _PRIOR_HASH_COLUMN not in columns:
        op.add_column(
            _TABLE,
            sa.Column(_PRIOR_HASH_COLUMN, sa.String(length=255), nullable=True),
        )
    if _ROTATED_AT_COLUMN not in columns:
        op.add_column(
            _TABLE,
            sa.Column(_ROTATED_AT_COLUMN, sa.DateTime(), nullable=True),
        )
    if _INDEX not in _table_indexes(conn):
        op.create_index(_INDEX, _TABLE, [_PRIOR_HASH_COLUMN], unique=False)


def downgrade() -> None:
    """Drop the index and both grace-window columns.

    Defensive (bd-5w6jz): skip each drop if the artifact is already absent so
    a partial re-run cleans up rather than raising. Non-destructive for auth:
    losing the grace metadata only disables the grace window — every current
    refresh token keeps working.
    """
    conn = op.get_bind()
    if _INDEX in _table_indexes(conn):
        op.drop_index(_INDEX, table_name=_TABLE)
    columns = _table_columns(conn)
    if _ROTATED_AT_COLUMN in columns:
        op.drop_column(_TABLE, _ROTATED_AT_COLUMN)
    if _PRIOR_HASH_COLUMN in columns:
        op.drop_column(_TABLE, _PRIOR_HASH_COLUMN)
