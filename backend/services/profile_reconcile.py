"""Group-level channel-profile reconciliation (GH #720 Part B / bead y3m6o).

ECM's "Auto Sync" M3U groups are backed by Dispatcharr's NATIVE
``auto_channel_sync``: Dispatcharr itself creates the channels for such a
group. ECM's Auto-Sync settings modal lets an operator pick which channel
PROFILES those channels should belong to and stores the selection as
``custom_properties.channel_profile_ids`` on the group's M3U settings row.
Nothing read that selection back, so Dispatcharr's default (auto-join EVERY
new channel to EVERY profile) stood — the operator's choice was silently
ignored.

This module closes that gap with a periodic, idempotent, CONVERGING
reconcile that applies the stored selection SUBTRACTIVELY to the group's
channels: for each profile in the universe, the group's channels are enabled
if the profile is selected and disabled otherwise. It is called from the M3U
change monitor (the converging backbone that catches BOTH ECM- and
Dispatcharr-triggered syncs), from the post-refresh completion poll, and from
the group-settings save router (instant apply on modal save).

Design decisions locked with the PO (see bead y3m6o / GH #720):

* **1(a) — absent/unset selection is a NO-OP.** A group with no
  ``channel_profile_ids`` (absent, ``None``, or empty list) is left entirely
  alone. "Empty" is NEVER interpreted as "disable everywhere" — that would
  strand the group's channels in zero profiles.
* **2(b) — pipeline-owned channels are EXCLUDED.** A channel whose profile
  membership was set by an enabled Channel Pipeline ``assign_channel_profile``
  rule outranks the group auto-sync selection (precedence: pipeline action >
  group selection > global default). Such channels carry a durable provenance
  marker in their Dispatcharr ``custom_properties`` (see
  ``PIPELINE_OWNERSHIP_MARKER_KEY``) written by
  ``ActionExecutor._execute_assign_channel_profile``; the reconcile skips
  every marked channel so it never stomps a pipeline decision.
* **3(a) — instant apply on save.** The group-settings save router reconciles
  the edited group so the selection takes effect without waiting for a sync.

Cost: **O(P) Dispatcharr PATCH calls per group**, independent of channel
count — one bulk ``bulk_update_profile_channels`` per profile, carrying the
whole channel-id list. This deliberately does NOT reuse the pipeline
executor's per-channel ``_apply_exclusive_profile_membership`` helper, which
is O(N*P) and instance-bound.

Known, documented edge: Dispatcharr channel groups are GLOBAL BY NAME and
``get_channels(channel_group=...)`` filters by group name (see
``dispatcharr_client.get_channels``). A selection therefore scopes every
channel sharing the effective group's name across accounts — which is
consistent with the per-global-group nature of the selection itself. We do
not fight this.
"""
from __future__ import annotations

import logging

from services.event_sync_preflight import resolve_effective_master_group_id

logger = logging.getLogger(__name__)

# Provenance marker (decision 2b). Written into a channel's Dispatcharr
# ``custom_properties`` by the pipeline ``assign_channel_profile`` action; read
# here to EXCLUDE pipeline-owned channels from group reconciliation. A string
# value (not a bare bool) so a future non-pipeline owner could be distinguished
# without a schema change, and so the key reads self-describingly in the
# Dispatcharr UI.
PIPELINE_OWNERSHIP_MARKER_KEY = "ecm_profile_owner"
PIPELINE_OWNERSHIP_MARKER_VALUE = "pipeline"

# Page size for channel enumeration — matches the client default.
_CHANNEL_PAGE_SIZE = 100
# Hard cap on pages to avoid an unbounded loop if the API never returns a
# terminal page (defensive; a group with >100k channels is not real here).
_MAX_CHANNEL_PAGES = 1000


def _selection_from_setting(setting: dict | None) -> list[int] | None:
    """Return the ``channel_profile_ids`` selection for a group setting row.

    Returns ``None`` when the selection is absent/unset/empty (decision 1a —
    the caller treats this as a NO-OP). Non-int entries are dropped defensively.
    """
    if not isinstance(setting, dict):
        return None
    cp = setting.get("custom_properties")
    if not isinstance(cp, dict):
        return None
    raw = cp.get("channel_profile_ids")
    if not isinstance(raw, list) or not raw:
        return None
    # Keep genuine ints only (bool is an int subclass but is not a profile id).
    selection = [pid for pid in raw if isinstance(pid, int) and not isinstance(pid, bool)]
    return selection or None


def _is_pipeline_owned(channel: dict) -> bool:
    """True if the channel's profile membership is owned by a pipeline rule.

    Reads the provenance marker written into the channel's Dispatcharr
    ``custom_properties`` by the ``assign_channel_profile`` action (decision
    2b). Marked channels are excluded from group reconciliation so the
    pipeline's decision is never overwritten.
    """
    cp = channel.get("custom_properties")
    if not isinstance(cp, dict):
        return False
    return cp.get(PIPELINE_OWNERSHIP_MARKER_KEY) == PIPELINE_OWNERSHIP_MARKER_VALUE


async def _fetch_group_channels(client, group_id: int) -> list[dict]:
    """Enumerate every channel in ``group_id`` via paginated ``get_channels``.

    ``get_channels`` filters by group NAME under the hood (it translates the
    id), so this returns every channel whose group name matches — consistent
    with the global-by-name selection semantics documented at module level.
    """
    channels: list[dict] = []
    page = 1
    while page <= _MAX_CHANNEL_PAGES:
        response = await client.get_channels(
            page=page, page_size=_CHANNEL_PAGE_SIZE, channel_group=group_id
        )
        results = response.get("results", []) if isinstance(response, dict) else []
        channels.extend(results)
        if not results or not response.get("next"):
            break
        page += 1
    return channels


async def reconcile_group_profiles(client, all_settings: dict, group_id: int) -> dict:
    """Apply a group's stored profile selection to its channels (idempotent).

    Reads ``custom_properties.channel_profile_ids`` from the group's settings
    row, resolves the EFFECTIVE group id (following any Channel Group
    Override), enumerates that group's channels, excludes pipeline-owned ones
    (decision 2b), and issues one bulk profile update per profile so the
    channels end up members of exactly the selected profiles.

    Returns a result dict with a ``status`` and counts, always safe to log:

    * ``no_selection`` — absent/empty selection, nothing done (decision 1a).
    * ``no_channels`` — selection present but the effective group is empty.
    * ``reconciled`` — bulk updates issued; counts populated.

    Never raises for a single bad profile — each per-profile bulk call is
    guarded so one stale/deleted selected id cannot abort the group.
    """
    setting = all_settings.get(group_id)
    selection = _selection_from_setting(setting)
    if selection is None:
        # Decision 1a: absent/unset selection is a read-only no-op.
        return {
            "status": "no_selection",
            "group_id": group_id,
            "channels_scoped": 0,
            "channels_excluded": 0,
            "profiles_enabled": 0,
            "profiles_disabled": 0,
        }

    selected = set(selection)
    effective_gid = resolve_effective_master_group_id(all_settings, group_id)

    channels = await _fetch_group_channels(client, effective_gid)
    owned = [c for c in channels if _is_pipeline_owned(c)]
    channel_ids = [c["id"] for c in channels if not _is_pipeline_owned(c) and "id" in c]
    excluded = len(owned)

    if not channel_ids:
        logger.info(
            "[PROFILE-RECONCILE] group=%s effective=%s: no reconcilable channels "
            "(%d total, %d pipeline-owned) — nothing to do",
            group_id, effective_gid, len(channels), excluded,
        )
        return {
            "status": "no_channels",
            "group_id": group_id,
            "effective_group_id": effective_gid,
            "channels_scoped": 0,
            "channels_excluded": excluded,
            "profiles_enabled": 0,
            "profiles_disabled": 0,
        }

    try:
        profiles = await client.get_channel_profiles()
    except Exception as e:  # noqa: BLE001 - one bad fetch must not crash the poll
        logger.warning(
            "[PROFILE-RECONCILE] group=%s: failed to fetch profile universe: %s",
            group_id, e,
        )
        profiles = []
    universe_ids = [p["id"] for p in profiles if isinstance(p, dict) and "id" in p]

    # Union so a selected id missing from a stale universe fetch is still
    # enabled, while every OTHER known profile is disabled — mirrors the
    # executor's per-channel helper semantics. When the universe fetch fails
    # entirely this degrades to enable-selected-only (never worse than the
    # pre-fix state where channels stayed in every profile).
    ordered_ids = list(dict.fromkeys(universe_ids + list(selection)))

    profiles_enabled = 0
    profiles_disabled = 0
    for pid in ordered_ids:
        enable = pid in selected
        try:
            await client.bulk_update_profile_channels(
                pid, {"channel_ids": channel_ids, "enabled": enable}
            )
            if enable:
                profiles_enabled += 1
            else:
                profiles_disabled += 1
        except Exception as e:  # noqa: BLE001 - a stale/deleted profile id skips, not aborts
            logger.warning(
                "[PROFILE-RECONCILE] group=%s: profile %s bulk update (enable=%s) "
                "failed, skipping: %s",
                group_id, pid, enable, e,
            )

    logger.info(
        "[PROFILE-RECONCILE] group=%s effective=%s: reconciled %d channel(s) "
        "(%d pipeline-owned excluded) into %d profile(s), disabled in %d "
        "(selection=%s)",
        group_id, effective_gid, len(channel_ids), excluded,
        profiles_enabled, profiles_disabled, sorted(selected),
    )
    return {
        "status": "reconciled",
        "group_id": group_id,
        "effective_group_id": effective_gid,
        "channels_scoped": len(channel_ids),
        "channels_excluded": excluded,
        "profiles_enabled": profiles_enabled,
        "profiles_disabled": profiles_disabled,
    }


def groups_with_selection(all_settings: dict) -> list[int]:
    """Return the group ids that carry a non-empty ``channel_profile_ids``."""
    return [
        gid
        for gid, setting in all_settings.items()
        if _selection_from_setting(setting) is not None
    ]


async def reconcile_all_selected_groups(client, all_settings: dict | None = None) -> dict:
    """Reconcile every group that carries a profile selection.

    Convenience entrypoint for the converging hooks (change monitor,
    post-refresh poll). Fetches ``all_settings`` once if not supplied, filters
    to groups with a selection, and reconciles each. Returns aggregate counts;
    one group's failure is logged and does not abort the rest.
    """
    if all_settings is None:
        try:
            all_settings = await client.get_all_m3u_group_settings()
        except Exception as e:  # noqa: BLE001
            logger.warning("[PROFILE-RECONCILE] failed to fetch group settings: %s", e)
            return {"groups_reconciled": 0, "groups_with_selection": 0, "channels_scoped": 0}

    target_gids = groups_with_selection(all_settings)
    groups_reconciled = 0
    channels_scoped = 0
    for gid in target_gids:
        try:
            result = await reconcile_group_profiles(client, all_settings, gid)
            if result.get("status") == "reconciled":
                groups_reconciled += 1
                channels_scoped += result.get("channels_scoped", 0)
        except Exception as e:  # noqa: BLE001 - isolate per-group failures
            logger.warning(
                "[PROFILE-RECONCILE] group=%s reconcile failed: %s", gid, e
            )

    if target_gids:
        logger.info(
            "[PROFILE-RECONCILE] swept %d group(s) with a selection, reconciled "
            "%d, scoped %d channel(s)",
            len(target_gids), groups_reconciled, channels_scoped,
        )
    return {
        "groups_reconciled": groups_reconciled,
        "groups_with_selection": len(target_gids),
        "channels_scoped": channels_scoped,
    }
