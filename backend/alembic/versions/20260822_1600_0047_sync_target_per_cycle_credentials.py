"""sync_targets: drop the provisioning markers, add the Schedules Direct password

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-22 16:00:00.000000

PO ruling 2026-08-22, verbatim: "We should be sending credentials every time so
that we don't need the user to deal with needing to re-type anything. Any update
happens as soon as the next scheduled sync occurs."

That reverses the one-time provisioning design migration 0046 supported, one
revision after it landed. Provider credentials now cross on EVERY cycle as part
of the ordinary importer write, so:

* ``credentials_provisioned_at`` and ``destination_credential_observed_at`` are
  DROPPED. Both existed solely to feed the S11 ``insecure`` refusal — "has ECM
  written a credential to this replica?" and "has a cycle seen one on it?" — and
  that refusal is gone (the PO: "I know the security risks. That's on the user
  to mitigate, not us."). Nothing reads them. A column kept alive with no reader
  is bookkeeping the next maintainer mistakes for a control, which is why this
  drops them rather than leaving them nullable and unused.
* ``schedules_direct_password`` TEXT NULL is ADDED. It is the ONE credential
  that cannot be harvested off this instance — Dispatcharr marks the Schedules
  Direct password write-only, never returns it, and SHA1-hashes it at fetch — so
  the operator supplies it once, on the target, and it cascades every cycle with
  everything else. Stored Fernet-encrypted through the same
  ``cloud_storage.crypto`` path as this row's own ``credentials`` column and
  never returned decrypted by any route.

WHAT IS LOST BY DROPPING THE MARKERS, stated rather than implied: nothing that
is still read. ``credentials_provisioned_at`` recorded that a provisioning
action had run; there is no provisioning action. Dropping them does NOT retract
credentials already written to a replica — see ADR-013's amended S11.

Idempotency (house pattern, see 0024 / 0040 / 0046): every DDL step is guarded
by a column-presence check, so a DB that drifted forward via ``create_all()``
passes through as a no-op.

Reversibility: ``downgrade()`` restores both nullable DATETIME markers (as NULL
for every row — the observation they recorded is not recoverable) and drops the
Schedules Direct password. Reverting the code does NOT retract credentials
already sent to a replica.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "0047"
down_revision: Union[str, Sequence[str], None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


_SYNC_TABLE = "sync_targets"
_DROPPED_MARKERS = ("credentials_provisioned_at", "destination_credential_observed_at")
_ADDED_COLUMN = "schedules_direct_password"


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
        # create_all() and stamps head). Nothing to change.
        return
    stale = [name for name in _DROPPED_MARKERS if name in existing]
    add = _ADDED_COLUMN not in existing
    if not stale and not add:
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        if add:
            # NULLABLE add: NULL is the correct "no Schedules Direct password
            # supplied" backfill for every existing row, so no server_default.
            batch.add_column(sa.Column(_ADDED_COLUMN, sa.Text(), nullable=True))
        for name in stale:
            batch.drop_column(name)


def downgrade() -> None:
    """Reverse 0047: restore the two markers (as NULL) and drop the SD password."""
    conn = op.get_bind()
    existing = _column_names(conn, _SYNC_TABLE)
    restore = [name for name in _DROPPED_MARKERS if name not in existing]
    drop = _ADDED_COLUMN in existing
    if not restore and not drop:
        return
    with op.batch_alter_table(_SYNC_TABLE) as batch:
        for name in restore:
            # Restored as NULL for every row: "ECM provisioned this target" and
            # "a cycle observed a credential on it" are facts this schema no
            # longer records, so there is nothing honest to backfill them with.
            batch.add_column(sa.Column(name, sa.DateTime(), nullable=True))
        if drop:
            batch.drop_column(_ADDED_COLUMN)
