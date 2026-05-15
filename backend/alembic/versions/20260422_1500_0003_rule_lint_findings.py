"""rule_lint_findings

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22 15:00:00.000000

Adds the ``rule_lint_findings`` table for bd-eio04.7 write-time pattern
linting. The table stores diagnostic findings from the migration-scan
step that flags pre-lint rule rows whose patterns would now fail the
write-time checks. Findings do NOT disable or modify the underlying
rules — UI surfaces them so an operator can decide whether to edit.

DB-engineer grooming note: separate table (not columns on the three
rule tables) so the hot-path rule rows aren't widened with optional
audit metadata.

bd-ax3uj idempotency: long-running installs where ``init_db``'s
``Base.metadata.create_all()`` had already created ``rule_lint_findings``
(and its two indexes) from the ORM model — while ``alembic_version`` was
still at ``0002`` — would explode here with
``sqlite3.OperationalError: table rule_lint_findings already exists``.
Inspect first, then skip the create on artifacts already present.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _index_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {idx["name"] for idx in inspect(connection).get_indexes(table_name)}


def upgrade() -> None:
    """Upgrade schema — create the rule_lint_findings table.

    Idempotent: if a prior ``create_all()`` already produced the table
    and/or its indexes, skip the matching ``op.create_*`` so SQLite does
    not raise ``OperationalError: ... already exists``. The ORM and the
    migration agree on shape, so a pre-existing artifact is the right
    one — no rebuild needed.
    """
    conn = op.get_bind()

    if not _table_exists(conn, 'rule_lint_findings'):
        op.create_table(
            'rule_lint_findings',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('rule_type', sa.String(length=30), nullable=False),
            sa.Column('rule_id', sa.Integer(), nullable=False),
            sa.Column('field', sa.String(length=120), nullable=False),
            sa.Column('code', sa.String(length=40), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('detail', sa.Text(), nullable=True),
            sa.Column('detected_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )

    existing_indexes = _index_names(conn, 'rule_lint_findings')
    if 'idx_rule_lint_finding_rule' not in existing_indexes:
        with op.batch_alter_table('rule_lint_findings', schema=None) as batch_op:
            batch_op.create_index(
                'idx_rule_lint_finding_rule', ['rule_type', 'rule_id'], unique=False
            )
    if 'idx_rule_lint_finding_code' not in existing_indexes:
        with op.batch_alter_table('rule_lint_findings', schema=None) as batch_op:
            batch_op.create_index(
                'idx_rule_lint_finding_code', ['code'], unique=False
            )


def downgrade() -> None:
    """Downgrade schema — drop the rule_lint_findings table."""
    conn = op.get_bind()
    if not _table_exists(conn, 'rule_lint_findings'):
        return
    existing_indexes = _index_names(conn, 'rule_lint_findings')
    with op.batch_alter_table('rule_lint_findings', schema=None) as batch_op:
        if 'idx_rule_lint_finding_code' in existing_indexes:
            batch_op.drop_index('idx_rule_lint_finding_code')
        if 'idx_rule_lint_finding_rule' in existing_indexes:
            batch_op.drop_index('idx_rule_lint_finding_rule')
    op.drop_table('rule_lint_findings')
