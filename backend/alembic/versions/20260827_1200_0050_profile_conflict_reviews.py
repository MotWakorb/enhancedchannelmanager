"""profile conflict review queue

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-27 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0050"
down_revision: Union[str, Sequence[str], None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

TABLE = "profile_conflict_reviews"
INDEXES = (
    ("uq_profile_conflict_reviews_fingerprint", ["fingerprint"], True),
    ("idx_profile_conflict_reviews_status_seen", ["status", "last_seen_at"], False),
    (
        "idx_profile_conflict_reviews_effective_status",
        ["effective_group_id", "status"],
        False,
    ),
)


def _index_names(connection) -> set[str]:
    if not inspect(connection).has_table(TABLE):
        return set()
    return {row["name"] for row in inspect(connection).get_indexes(TABLE)}


def upgrade() -> None:
    conn = op.get_bind()
    if not inspect(conn).has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("fingerprint", sa.Text(), nullable=False),
            sa.Column("fingerprint_version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("effective_group_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.Text(), server_default="pending", nullable=False),
            sa.Column("accepted_choice_key", sa.Text(), nullable=True),
            sa.Column("accepted_profile_ids", sa.Text(), nullable=True),
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.Column("last_seen_at", sa.Integer(), nullable=False),
            sa.Column("resolved_at", sa.Integer(), nullable=True),
            sa.Column("applied_at", sa.Integer(), nullable=True),
            sa.Column("actor_token_id", sa.Text(), nullable=True),
            sa.Column("retry_error", sa.Text(), nullable=True),
            sa.Column("notified_at", sa.Integer(), nullable=True),
            sa.Column("accept_journaled_at", sa.Integer(), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending','accepted','superseded')",
                name="ck_profile_conflict_reviews_status",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_profile_conflict_reviews"),
        )
    existing = _index_names(conn)
    for name, columns, unique in INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, columns, unique=unique)


def downgrade() -> None:
    conn = op.get_bind()
    existing = _index_names(conn)
    for name, _columns, _unique in reversed(INDEXES):
        if name in existing:
            op.drop_index(name, table_name=TABLE)
    if inspect(conn).has_table(TABLE):
        op.drop_table(TABLE)
