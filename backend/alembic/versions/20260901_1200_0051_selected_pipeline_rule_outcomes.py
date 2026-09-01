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


def _columns(connection) -> dict[str, dict]:
    return {
        column["name"]: column
        for column in inspect(connection).get_columns(TABLE)
    }


def _assert_compatible_shape(column: dict) -> None:
    type_name = type(column["type"]).__name__.upper()
    if type_name != "TEXT" or column.get("nullable") is not True:
        raise RuntimeError(
            f"{TABLE}.{COLUMN} already exists with incompatible shape "
            f"(type={column['type']}, nullable={column.get('nullable')}); "
            "expected nullable TEXT"
        )


def upgrade() -> None:
    connection = op.get_bind()
    columns = _columns(connection)
    if COLUMN not in columns:
        op.add_column(TABLE, sa.Column(COLUMN, sa.Text(), nullable=True))
    else:
        _assert_compatible_shape(columns[COLUMN])


def downgrade() -> None:
    connection = op.get_bind()
    if COLUMN in _columns(connection):
        selected_count = connection.execute(sa.text(
            f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} IS NOT NULL"
        )).scalar_one()
        if selected_count:
            raise RuntimeError(
                f"refusing downgrade: {selected_count} row(s) contain selected "
                "rule audit data; an intentional destructive downgrade would "
                "erase that history"
            )
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_column(COLUMN)
