"""Phase-2 DBAS restore importers.

Each module here restores one entity category from a Dispatcharr export archive,
emitting per-entity results into the shared restore contracts
(``dbas.restore_contracts``): an :class:`~dbas.restore_contracts.EntityCategoryReport`
inside a :class:`~dbas.restore_contracts.RestoreReport`, destination-id
registrations in an :class:`~dbas.restore_contracts.IdRemapTable`, and
created-entity records in a :class:`~dbas.restore_contracts.RollbackLedger` so a
later failure can compensate-delete.

The first importer to land here is ``users`` — the crown-jewel
``dispatcharr_users`` category (bead ``enhancedchannelmanager-l1p4p``), the most
security-sensitive of the Phase-2 importers (privilege-escalation surface).

``channels`` (bead ``enhancedchannelmanager-4vouz``) restores the CHANNEL
category: it creates each archived channel row (remapping its FK references
through the :class:`~dbas.restore_contracts.IdRemapTable`) and reattaches each
channel to its archived channel-profile memberships. It leaves a clean seam where
bead ``0i2vt.14`` integrates the stream matcher + custom-stream fallback to
attach streams — this importer never matches or attaches a stream.
"""

from dbas.importers.channels import import_channels
from dbas.importers.m3u_accounts import (
    apply_deferred_auto_sync,
    import_m3u_accounts,
    resolve_group,
)
from dbas.importers.users import import_users

__all__ = [
    "apply_deferred_auto_sync",
    "import_channels",
    "import_m3u_accounts",
    "import_users",
    "resolve_group",
]
