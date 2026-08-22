"""sync_targets provisioning markers (ADR-013 S10-S13 one-time credential provisioning)

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-22 12:00:00.000000

Additive, reversible extension of ``sync_targets`` for the one-time credential
provisioning feature (bead enhancedchannelmanager-wd20y; ADR-013 amendment
2026-08-22, decisions S10-S13; threat model §11.5 rows D11-D16).

Two NULLABLE columns are added, and neither ever holds a credential (INV-3 —
harvested values are read, written to B and discarded inside the request):

* ``credentials_provisioned_at`` DATETIME NULL — the S11 gate marker. Set on a
  provisioning that succeeded, cleared only by a de-provision whose write to B
  succeeded for EVERY targeted account (INV-9). A target may not be both
  "TLS verification disabled" and "provisioned"; this column is the recorded
  half of that predicate.
* ``destination_credentials_observed_at`` DATETIME NULL — the OBSERVED half
  (threat model row D16; PO ruling 2026-08-22 on §11.5.4 item 5). Stamped by
  the sync cycle from state it already reads: the credential re-entry reporter
  already runs ``credential_sentinel.credential_is_present`` against B's own
  account rows. Presence only — never a value, never a comparison.

Why COLUMNS and not an inference from journal / execution history: bead
enhancedchannelmanager-5dp92 records that execution history is keyed on a
REUSABLE target id, so a freshly created target can inherit a deleted target's
rows. An "is there a provisioning row for this id?" gate would inherit a
provisioned verdict it never earned, or lose one it did.

Both adds are NULLABLE, so no ``server_default`` is needed (NULL is the correct
"never provisioned" / "never observed" sentinel for existing rows) — the same
shape as the nullable bookkeeping adds in 0024.

Idempotency (house pattern, see 0024 / 0040): each ADD COLUMN is guarded by a
column-presence check, so a DB that drifted forward via ``create_all()`` passes
through as a no-op rather than raising ``OperationalError: duplicate column``.

Reversibility: ``downgrade()`` drops both columns via ``batch_alter_table``
(SQLite cannot DROP COLUMN without table recreation). Dropping them loses the
provisioning gate state only — target rows and credentials are preserved.
Reverting the code does NOT retract credentials already written to B (ADR-013,
"What a de-provision cannot guarantee").
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0046"
down_revision: Union[str, Sequence[str], None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_SYNC_TABLE = "sync_targets"
_NEW_COLUMNS = ("credentials_provisioned_at", "destination_credentials_observed_at")


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _column_names(connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    if not existing:
        # Table absent (fresh DB materialises the full ORM shape via
        # create_all() and stamps head). Nothing to extend.
        return
    missing = [name for name in _NEW_COLUMNS if name not in existing]
    if not missing:
        # Drift-forward no-op (columns already materialised by create_all()).
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        for name in missing:
            # NULLABLE add: NULL is the correct "never provisioned" / "never
            # observed" backfill for every existing row, so no server_default.
            batch.add_column(sa.Column(name, sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Reverse 0046: drop both provisioning markers (defensive — skip when absent)."""
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    present = [name for name in _NEW_COLUMNS if name in existing]
    if not present:
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        for name in present:
            batch.drop_column(name)
