"""Save provider-scoped M3U Changes view exclusions.

Revision ID: 0057
Revises: 0056
"""
from alembic import op
import sqlalchemy as sa

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "m3u_change_exclusions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "m3u_change_exclusions",
        sa.Column("m3u_account_id", sa.Integer(), nullable=False),
        sa.Column("group_name", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("m3u_account_id", "group_name"),
        sa.CheckConstraint("m3u_account_id > 0", name="ck_m3u_change_exclusion_account"),
        sa.CheckConstraint("length(group_name) BETWEEN 1 AND 255", name="ck_m3u_change_exclusion_name"),
    )


def downgrade() -> None:
    op.drop_table("m3u_change_exclusions")
