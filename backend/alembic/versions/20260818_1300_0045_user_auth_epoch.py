"""Add a per-user authentication epoch for access-token invalidation.

Revision ID: 0045
Revises: 0044

Existing users and access tokens remain at epoch zero during rollout. A
successful password reset increments the user's epoch, invalidating access
tokens issued before the reset without relying on timestamp precision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0045"
down_revision: Union[str, Sequence[str], None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

_TABLE = "users"
_COLUMN = "auth_epoch"


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


def downgrade() -> None:
    connection = op.get_bind()
    if _COLUMN not in _column_names(connection):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
