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
from dbas.importers.epg_sources import import_epg_sources
from dbas.importers.groups_profiles import (
    import_channel_groups,
    import_channel_profiles,
    import_groups_profiles,
    import_stream_profiles,
)
from dbas.importers.m3u_accounts import (
    apply_deferred_auto_sync,
    import_m3u_accounts,
    resolve_group,
)
from dbas.importers.users import import_users
from dbas.importers.logos import (
    LogoImportResult,
    import_logos,
    resolve_logo_match,
)

__all__ = [
    "apply_deferred_auto_sync",
    "import_channel_groups",
    "import_channel_profiles",
    "import_channels",
    "import_epg_sources",
    "import_groups_profiles",
    "import_m3u_accounts",
    "import_stream_profiles",
    "import_users",
    "resolve_group",
    "LogoImportResult",
    "import_logos",
    "resolve_logo_match",
]

# --- enhancedchannelmanager-0i2vt.13: settings/agents bulk importer ---------
# user agents + core settings + DVR rules + upcoming recordings (…-ciabe) +
# comskip (plugins EXCLUDED per ADR-012 D10; users restored by
# importers/users.py — l1p4p). Appended at the END to minimize merge conflict
# with sibling importer appends.
from dbas.importers.settings_agents import (
    import_comskip,
    import_core_settings,
    import_dvr_rules,
    import_settings_agents,
    import_upcoming_recordings,
    import_user_agents,
)

__all__ += [
    "import_comskip",
    "import_core_settings",
    "import_dvr_rules",
    "import_settings_agents",
    "import_upcoming_recordings",
    "import_user_agents",
]
