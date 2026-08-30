"""Bound per-token password-reset validation attempts.

Revision ID: 0044
Revises: 0043

Adds a non-null attempt counter used by the account-level reset limiter and a
partial unique index allowing at most one unused credential per user. Existing
duplicate unused rows are removed before the index is created, retaining the
newest row. The server default gives every existing, hour-lived recovery token
its full budget. The migration is idempotent for databases whose ORM schema was
materialised before Alembic caught up.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0044"
down_revision: Union[str, Sequence[str], None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_TABLE = "password_reset_tokens"
_COLUMN = "attempt_count"
_INDEX = "uq_reset_token_unused_user"


def _column_names(connection) -> set[str]:
    inspector = inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    connection = op.get_bind()
    if _COLUMN not in _column_names(connection):
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    indexes = {index["name"] for index in inspect(connection).get_indexes(_TABLE)}
    if _INDEX not in indexes:
        connection.execute(
            sa.text(
                "DELETE FROM password_reset_tokens "
                "WHERE used_at IS NULL AND id NOT IN ("
                "SELECT MAX(id) FROM password_reset_tokens "
                "WHERE used_at IS NULL GROUP BY user_id)"
            )
        )
        op.create_index(
            _INDEX,
            _TABLE,
            ["user_id"],
            unique=True,
            sqlite_where=sa.text("used_at IS NULL"),
        )


def downgrade() -> None:
    connection = op.get_bind()
    indexes = {index["name"] for index in inspect(connection).get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)
    if _COLUMN not in _column_names(connection):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
