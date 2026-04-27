"""auto_creation_quality_m3u_tie_break

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-27 21:00:00.000000

Adds per-rule stream sort fields for quality (resolution) ordering when two
streams share the same probed height:

* ``quality_tie_break_order`` — ``asc`` / ``desc`` tie-break using ECM M3U
  account priorities (defaults in the ORM to ``desc``).
* ``quality_m3u_tie_break_enabled`` — toggle to disable that M3U tie-break.

The legacy ``database._run_migrations`` path may have already added these
columns on long-running installs; Alembic is the authoritative timeline for
fresh upgrades and drift tests (``test_baseline_matches_metadata_no_drift``).

Bead: enhancedchannelmanager-3j9su
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add quality tie-break columns to auto_creation_rules."""
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("quality_tie_break_order", sa.String(length=4), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "quality_m3u_tie_break_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.alter_column(
            "quality_m3u_tie_break_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    """Remove quality tie-break columns."""
    with op.batch_alter_table("auto_creation_rules", schema=None) as batch_op:
        batch_op.drop_column("quality_m3u_tie_break_enabled")
        batch_op.drop_column("quality_tie_break_order")
