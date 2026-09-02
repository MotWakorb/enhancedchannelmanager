"""selected pipeline rule outcomes.

Downgrade requires every ECM instance to be stopped and a full-fidelity
``journal.db`` backup to exist before Alembic starts. SQLite DDL is not
transactionally lockable across this guard and the subsequent native column
drop, so the checks below are safe only under operator-provided quiescence.
They refuse known active executions or selected-rule audit data; they cannot
prove an external process will not write after the checks.

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
        active_count = connection.execute(sa.text(
            f"SELECT COUNT(*) FROM {TABLE} WHERE status = 'running'"
        )).scalar_one()
        if active_count:
            raise RuntimeError(
                f"refusing downgrade: {active_count} active/running execution(s) "
                "exist. Stop all ECM instances and create a full-fidelity "
                "journal.db backup before retrying; this guard is safe only "
                "under quiescence"
            )
        selected_count = connection.execute(sa.text(
            f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} IS NOT NULL"
        )).scalar_one()
        if selected_count:
            raise RuntimeError(
                f"refusing downgrade: all ECM instances must be stopped and a "
                f"full-fidelity journal.db backup must exist first; "
                f"{selected_count} row(s) contain selected rule audit data, so "
                "this downgrade would erase history even under quiescence"
            )
        # SQLite 3.35+ supports native DROP COLUMN. Do not use batch mode here:
        # rebuilding the parent table under foreign_keys=ON fires inbound
        # ON DELETE CASCADE constraints and erases snapshots/conflicts.
        op.drop_column(TABLE, COLUMN)
