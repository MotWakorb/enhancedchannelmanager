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
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — create the rule_lint_findings table."""
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
    with op.batch_alter_table('rule_lint_findings', schema=None) as batch_op:
        batch_op.create_index(
            'idx_rule_lint_finding_rule', ['rule_type', 'rule_id'], unique=False
        )
        batch_op.create_index(
            'idx_rule_lint_finding_code', ['code'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema — drop the rule_lint_findings table."""
    with op.batch_alter_table('rule_lint_findings', schema=None) as batch_op:
        batch_op.drop_index('idx_rule_lint_finding_code')
        batch_op.drop_index('idx_rule_lint_finding_rule')
    op.drop_table('rule_lint_findings')
