"""sync_targets: logos default ON, throttled by a sub-interval

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-23 12:00:00.000000

Bead ``enhancedchannelmanager-2yq19``, under ADR-013's 2026-08-22 governing
principle: a replica is a faithful copy, everything replicates by default, and
every exclusion must be named, individually justified, and visible.

``sync_logos`` shipped default OFF (bead ``7ipq2.1``). The reason was COST, not
correctness — the logos importer carries a destructive ``clear_existing``
bulk-delete plus a per-logo streaming upload that is wrong to pay every
interval. That mechanism decision stands. What does not stand is the DEFAULT: an
operator who never finds the toggle receives a replica with no artwork at all,
which is the second half of the failure epic ``f5a5j`` is named for. A silent
omission is exactly what the principle forbids, and "it is cheaper not to" is
one of the three non-reasons the ADR names.

So the cost is answered with a THROTTLE rather than with omission:

* ``sync_logos`` server_default flips ``'0'`` -> ``'1'``.
* ``logo_sync_interval_hours`` INTEGER NOT NULL server_default ``'24'`` is
  ADDED — how often the LOGO slice may run. ``0`` means every cycle (the
  pre-throttle behaviour, still available). The config and channel slices are
  untouched; only the expensive slice is throttled.
* ``last_logo_sync_at`` DATETIME NULL is ADDED — when the slice last ran. NULL
  is the correct "never" backfill and is also what makes a freshly-created
  target carry logos on its FIRST cycle instead of waiting out a sub-interval
  with no artwork on the replica at all.

WHAT THIS DELIBERATELY DOES NOT DO, and it is the load-bearing half. **No
existing row's ``sync_logos`` value is rewritten.** A ``server_default`` governs
what a row inserted WITHOUT the column gets; it does not touch rows that already
have one. A stored ``False`` is indistinguishable from an operator who
deliberately turned logos off, and silently flipping a saved operator choice in
a migration is a one-way door this bead was not asked to open. Operators with an
existing target still turn logos on with the toggle bead ``…-8gnik`` shipped;
what changes is that nobody NEW has to find it.

``logo_sync_interval_hours`` and ``last_logo_sync_at`` DO apply to existing
rows, because neither had a prior operator-set value to preserve.

Idempotency (house pattern, see 0024 / 0040 / 0046 / 0047): every DDL step is
guarded by a column-presence check, so a DB that drifted forward via
``create_all()`` passes through as a no-op. The ``sync_logos`` default change is
applied by rebuilding the column under ``batch_alter_table``, which is how
SQLite alters a default at all.

Reversibility: ``downgrade()`` restores the ``'0'`` default and drops the two
new columns. It likewise does not rewrite any row's stored ``sync_logos``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


# revision identifiers, used by Alembic.
revision: str = "0048"
down_revision: Union[str, Sequence[str], None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_SYNC_TABLE = "sync_targets"
_LOGO_FLAG = "sync_logos"
_INTERVAL_COLUMN = "logo_sync_interval_hours"
_STAMP_COLUMN = "last_logo_sync_at"


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _column_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def _set_logo_flag_default(server_default: str) -> None:
    """Rebuild ``sync_logos`` with a new server_default (SQLite-safe).

    SQLite cannot ALTER a column default in place; ``batch_alter_table``
    recreates the table around the change, preserving every row's stored value —
    which is the point: this migration changes what a NEW row defaults to and
    rewrites nobody's saved choice.
    """
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        batch.alter_column(
            _LOGO_FLAG,
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=text(server_default),
        )


def upgrade() -> None:
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    if not existing:
        # Table absent (fresh DB materialises the full ORM shape via
        # create_all() and stamps head). Nothing to change.
        return
    if _INTERVAL_COLUMN not in existing or _STAMP_COLUMN not in existing:
        with op.batch_alter_table(_SYNC_TABLE) as batch:
            if _INTERVAL_COLUMN not in existing:
                # NOT NULL add to a possibly-populated table => server_default
                # is mandatory on SQLite (docs/database_migrations.md — the
                # smart-bootstrap stamp-skip trap).
                batch.add_column(
                    sa.Column(
                        _INTERVAL_COLUMN,
                        sa.Integer(),
                        nullable=False,
                        server_default=text("24"),
                    )
                )
            if _STAMP_COLUMN not in existing:
                # NULLABLE add: NULL is the correct "the logo slice has never
                # run for this target" backfill, and it lets the next cycle
                # carry logos immediately rather than waiting out an interval.
                batch.add_column(sa.Column(_STAMP_COLUMN, sa.DateTime(), nullable=True))
    if _LOGO_FLAG in existing:
        _set_logo_flag_default("1")


def downgrade() -> None:
    """Reverse 0048: restore the OFF default and drop the sub-interval columns.

    Row values are left alone in this direction too — a target an operator
    switched logos ON for stays on.
    """
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    if not existing:
        return
    if _LOGO_FLAG in existing:
        _set_logo_flag_default("0")
    drop = [name for name in (_INTERVAL_COLUMN, _STAMP_COLUMN) if name in existing]
    if drop:
        with op.batch_alter_table(_SYNC_TABLE) as batch:
            for name in drop:
                batch.drop_column(name)
