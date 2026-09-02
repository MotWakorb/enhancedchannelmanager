"""event sync review retention index

The smart-bootstrap schema check covers tables and columns, not indexes. Its
explicit replay marker is therefore required so existing databases at 0051
run this index-only revision instead of being stamped directly to 0052.

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-02 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "0052"
down_revision: Union[str, Sequence[str], None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

# Force smart-bootstrap to execute this otherwise-invisible index-only change.
destructive = True

TABLE = "event_sync_reviews"
INDEX = "idx_event_sync_reviews_status_seen_id"


def _index_names(connection) -> set[str]:
    inspector = inspect(connection)
    if not inspector.has_table(TABLE):
        return set()
    return {row["name"] for row in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    connection = op.get_bind()
    if (
        inspect(connection).has_table(TABLE)
        and INDEX not in _index_names(connection)
    ):
        op.create_index(
            INDEX,
            TABLE,
            ["status", "last_seen_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    if INDEX in _index_names(op.get_bind()):
        op.drop_index(INDEX, table_name=TABLE)
