"""selected pipeline rule outcomes

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0051"
down_revision: Union[str, Sequence[str], None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

TABLE = "auto_creation_executions"
COLUMN = "selected_rule_outcomes"


def _column_names(connection) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(TABLE)}


def upgrade() -> None:
    connection = op.get_bind()
    if COLUMN not in _column_names(connection):
        op.add_column(TABLE, sa.Column(COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    if COLUMN in _column_names(connection):
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_column(COLUMN)
