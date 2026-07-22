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
* **2(b) — pipeline-owned channels are EXCLUDED, WITH HANDOFF.** A channel
  whose profile membership was set by an enabled Channel Pipeline
  ``assign_channel_profile`` rule outranks the group auto-sync selection
  (precedence: pipeline action > group selection > global default). Such
  channels carry a durable provenance marker plus the OWNING RULE ID in their
  Dispatcharr ``custom_properties`` (``PIPELINE_OWNERSHIP_MARKER_KEY`` +
  ``PIPELINE_OWNERSHIP_RULE_ID_KEY``), written by
  ``ActionExecutor._execute_assign_channel_profile``. At reconcile time we
  query the set of pipeline rule ids that are CURRENTLY enabled AND still
  carry an ``assign_channel_profile`` action; a channel is excluded ONLY while
  its owning rule is in that live set. If the owning rule is disabled, deleted,
  or no longer assigns profiles, the channel is RELEASED back to Auto-Sync
  control — it rejoins the reconcile and its stale marker keys are cleared.
  RESIDUAL LIMITATION (documented, NOT fixed): ownership is released on rule
  disable/delete/action-removal, but NOT on a rule CONDITION change that stops
  matching the channel — that would require per-channel rule re-evaluation,
  out of scope for a home-lab deployment.
* **3(a) — instant apply on save.** The group-settings save router reconciles
  the edited group so the selection takes effect (best-effort) on save; the
  converging backbone (change monitor + post-refresh poll) is the durable
  guarantee.
* **Global per channel-group.** A selection is resolved ONCE per global
  channel-group id, deterministically: precedence is ``auto_channel_sync`` ON
  first, then the LOWEST ``m3u_account_id`` (see
  ``dispatcharr_client.get_all_m3u_group_settings``). Channel enumeration is
  global-by-name. When two account rows for the same group carry DIFFERENT
  non-empty selections a conflict is flagged (``conflict`` in the result) so
  the save hook can warn the operator.

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

import json
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
# The id of the assign_channel_profile rule that established ownership. Read at
# reconcile time against the live rule set to implement automatic HANDOFF: when
# the owning rule is gone/disabled/no-longer-assigns, the channel is released.
PIPELINE_OWNERSHIP_RULE_ID_KEY = "ecm_profile_owner_rule_id"

# Page size for channel enumeration — matches the client default.
_CHANNEL_PAGE_SIZE = 100
# Hard cap on pages to avoid an unbounded loop if the API never returns a
# terminal page (defensive; a group with >100k channels is not real here).
_MAX_CHANNEL_PAGES = 1000

# Sentinel distinct from None: default for the live-rule-ids argument meaning
# "resolve it yourself from the DB". Explicit None means "resolution failed /
# unknown — treat every marker conservatively as still-owned".
_UNSET = object()


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


def _query_live_profile_assigning_rule_ids() -> set[int]:
    """SYNC: ids of enabled pipeline rules that still assign channel profiles.

    A channel is only excluded from group reconcile while its owning rule is in
    this set (decision 2b handoff). Queries the ChannelPipelineRule model
    (table ``auto_creation_rules``) for enabled rules whose JSON ``actions``
    array contains an ``assign_channel_profile`` action. Called INLINE on the
    event-loop thread (see :func:`_resolve_live_rule_ids`) — it is a handful of
    rows and, crucially, ECM's DB runs on a single shared StaticPool connection
    that every other access uses one-thread-at-a-time, so it must NOT be
    offloaded to a worker thread.
    """
    from database import get_session
    from models import ChannelPipelineRule

    ids: set[int] = set()
    db = get_session()
    try:
        rules = (
            db.query(ChannelPipelineRule)
            .filter(ChannelPipelineRule.enabled == True)  # noqa: E712 - SQLAlchemy
            .all()
        )
        for rule in rules:
            try:
                actions = json.loads(rule.actions) if rule.actions else []
            except (ValueError, TypeError):
                continue
            if any(
                isinstance(a, dict) and a.get("type") == "assign_channel_profile"
                for a in actions
            ):
                ids.add(rule.id)
    finally:
        db.close()
    return ids


async def _resolve_live_rule_ids() -> set[int] | None:
    """Resolve the live profile-assigning rule-id set.

    Runs the query INLINE on the event-loop thread (async only so callers'
    ``await`` is unchanged). ECM's DB is a single shared StaticPool sqlite
    connection used one-thread-at-a-time by all inline event-loop DB access;
    offloading this query to a worker thread would let it issue statements on
    that same connection concurrently with unrelated inline DB work and corrupt
    the victim's transaction state (Blocker 1). Returns the set on success, or
    ``None`` if the query FAILS — a failure must be conservative (treat every
    marker as still-owned) so a transient DB error can never release,
    reconcile, and stomp a genuinely pipeline-owned channel.
    """
    try:
        return _query_live_profile_assigning_rule_ids()
    except Exception as e:  # noqa: BLE001 - conservative fallback below
        logger.warning(
            "[PROFILE-RECONCILE] could not resolve live profile-assigning rule "
            "ids (%s) — treating all pipeline markers as still-owned this pass", e,
        )
        return None


def _ownership_state(channel: dict, live_rule_ids: set[int] | None) -> str:
    """Classify a channel's pipeline-ownership for reconcile (decision 2b).

    Returns one of:

    * ``"unowned"`` — no pipeline marker; a normal reconcile target.
    * ``"owned"`` — marked AND its owning rule is still live (or liveness is
      unknown / the marker is legacy without a usable rule id); EXCLUDED.
    * ``"released"`` — marked but its owning rule is gone/disabled/no-longer-
      assigns; the channel rejoins the reconcile and its stale marker is
      cleared (automatic handoff back to Auto-Sync control).
    """
    cp = channel.get("custom_properties")
    if not isinstance(cp, dict):
        return "unowned"
    if cp.get(PIPELINE_OWNERSHIP_MARKER_KEY) != PIPELINE_OWNERSHIP_MARKER_VALUE:
        return "unowned"
    if live_rule_ids is None:
        # Liveness unknown (resolution failed) — stay conservative: keep owned
        # so a transient DB error never releases a pipeline-owned channel.
        return "owned"
    rule_id = cp.get(PIPELINE_OWNERSHIP_RULE_ID_KEY)
    if not isinstance(rule_id, int) or isinstance(rule_id, bool):
        # Legacy/malformed marker with no usable rule id (should not exist in
        # prod — the rule-id stamp shipped with the handoff). Conservatively
        # owned, but warn: handoff cannot release it until it is re-stamped.
        logger.warning(
            "[PROFILE-RECONCILE] channel %s is pipeline-owned but carries no "
            "valid rule id (%r) — treating as owned; handoff cannot release it",
            channel.get("id"), rule_id,
        )
        return "owned"
    return "owned" if rule_id in live_rule_ids else "released"


async def _clear_ownership_marker(client, channel: dict) -> None:
    """Best-effort: strip the stale pipeline-ownership marker keys.

    Called when a channel is RELEASED (its owning rule is gone/disabled) so a
    future reconcile sees it as a plain Auto-Sync channel. Merge-preserving:
    only the two marker keys are dropped, every other ``custom_properties`` key
    is retained. A failure is logged and swallowed — never fail the reconcile
    over a marker cleanup.
    """
    cid = channel.get("id")
    cp = channel.get("custom_properties")
    if cid is None or not isinstance(cp, dict):
        return
    merged = {
        k: v
        for k, v in cp.items()
        if k not in (PIPELINE_OWNERSHIP_MARKER_KEY, PIPELINE_OWNERSHIP_RULE_ID_KEY)
    }
    try:
        await client.update_channel(cid, {"custom_properties": merged})
    except Exception as e:  # noqa: BLE001 - cleanup is best-effort
        logger.warning(
            "[PROFILE-RECONCILE] failed to clear stale ownership marker on "
            "released channel %s: %s", cid, e,
        )


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


def _result(status: str, group_id: int, *, effective_gid=None, scoped=0,
            excluded=0, released=0, enabled=0, disabled=0,
            failed_profile_ids=None, conflict=False) -> dict:
    """Build a uniform reconcile result dict so every caller can rely on the
    same keys (status, counts, failed_profile_ids, conflict)."""
    return {
        "status": status,
        "group_id": group_id,
        "effective_group_id": effective_gid,
        "channels_scoped": scoped,
        "channels_excluded": excluded,
        "channels_released": released,
        "profiles_enabled": enabled,
        "profiles_disabled": disabled,
        "failed_profile_ids": sorted(failed_profile_ids or []),
        "conflict": bool(conflict),
    }


async def reconcile_group_profiles(
    client, all_settings: dict, group_id: int, live_rule_ids=_UNSET,
) -> dict:
    """Apply a group's stored profile selection to its channels (idempotent).

    Reads ``custom_properties.channel_profile_ids`` from the group's settings
    row, resolves the EFFECTIVE group id (following any Channel Group
    Override), enumerates that group's channels, excludes pipeline-owned ones
    whose owning rule is still live (decision 2b handoff), and issues one bulk
    profile update per profile so the channels end up members of exactly the
    selected profiles.

    ``live_rule_ids`` — the set of currently-enabled rule ids that still assign
    channel profiles. Pass it explicitly (the sweep computes it ONCE); left
    unset it is resolved from the DB. Explicit ``None`` means resolution failed
    and every marker is treated conservatively as still-owned.

    Returns a uniform result dict (see :func:`_result`) with a ``status``:

    * ``no_selection`` — absent/empty selection, nothing done (decision 1a).
    * ``no_channels`` — selection present but the effective group has no
      reconcilable channels.
    * ``stale_selection`` — every selected profile has been DELETED (the
      authoritative universe contains none of them); a SAFETY NO-OP that
      leaves the channels untouched rather than disabling them everywhere.
    * ``partial_failure`` — a bulk profile write failed. If a SELECTED-profile
      enable failed the reconcile ABORTS before any destructive disable (so the
      channels cannot be stranded); ``failed_profile_ids`` is populated.
    * ``reconciled`` — all writes succeeded; counts populated. Also covers the
      degraded enable-selected-only path when the universe fetch fails.

    Never raises for a single bad profile — each bulk call is guarded.
    """
    if live_rule_ids is _UNSET:
        live_rule_ids = await _resolve_live_rule_ids()

    setting = all_settings.get(group_id)
    selection = _selection_from_setting(setting)
    conflict = bool(isinstance(setting, dict) and setting.get("_ecm_channel_profile_conflict"))
    if selection is None:
        # Decision 1a: absent/unset selection is a read-only no-op.
        return _result("no_selection", group_id, conflict=conflict)

    selected = set(selection)
    effective_gid = resolve_effective_master_group_id(all_settings, group_id)

    channels = await _fetch_group_channels(client, effective_gid)

    # Classify each channel (decision 2b handoff): owned -> excluded; released
    # -> rejoins the reconcile and its stale marker is cleared; unowned ->
    # normal target.
    owned_count = 0
    released_channels: list[dict] = []
    channel_ids: list[int] = []
    for c in channels:
        cid = c.get("id")
        if cid is None:
            continue
        state = _ownership_state(c, live_rule_ids)
        if state == "owned":
            owned_count += 1
        else:
            channel_ids.append(cid)
            if state == "released":
                released_channels.append(c)

    # Clear stale markers on released channels (best-effort, never fails the
    # reconcile). Done up front so a subsequent no_channels/stale path still
    # completes the handoff cleanup.
    for c in released_channels:
        await _clear_ownership_marker(client, c)
    if released_channels:
        logger.info(
            "[PROFILE-RECONCILE] group=%s: released %d channel(s) whose owning "
            "pipeline rule is no longer live — returned to Auto-Sync control",
            group_id, len(released_channels),
        )

    released = len(released_channels)
    if not channel_ids:
        logger.info(
            "[PROFILE-RECONCILE] group=%s effective=%s: no reconcilable channels "
            "(%d total, %d pipeline-owned) — nothing to do",
            group_id, effective_gid, len(channels), owned_count,
        )
        return _result(
            "no_channels", group_id, effective_gid=effective_gid,
            excluded=owned_count, released=released, conflict=conflict,
        )

    universe_fetch_failed = False
    try:
        profiles = await client.get_channel_profiles()
    except Exception as e:  # noqa: BLE001 - one bad fetch must not crash the poll
        logger.warning(
            "[PROFILE-RECONCILE] group=%s: failed to fetch profile universe: %s",
            group_id, e,
        )
        profiles = []
        universe_fetch_failed = True
    universe_ids = [p["id"] for p in profiles if isinstance(p, dict) and "id" in p]

    if universe_fetch_failed:
        # Universe UNKNOWN (the fetch raised). Without the authoritative
        # universe we cannot know which profiles to DISABLE, and disabling
        # blindly could strand channels — so degrade to ENABLE-SELECTED-ONLY:
        # enable every selected id and issue NO disables. This is never worse
        # than the pre-fix state (channels stayed in every profile); the
        # transient gap — a just-deselected profile stays enabled — closes on
        # the next successful reconcile. Logged so the skipped-disables window
        # is observable rather than silent.
        logger.warning(
            "[PROFILE-RECONCILE] group=%s: profile universe fetch failed — "
            "degrading to enable-selected-only; disables SKIPPED until the next "
            "successful reconcile (selection=%s)",
            group_id, sorted(selected),
        )
        profiles_enabled = 0
        failed_enable: list[int] = []
        for pid in selection:
            try:
                await client.bulk_update_profile_channels(
                    pid, {"channel_ids": channel_ids, "enabled": True}
                )
                profiles_enabled += 1
            except Exception as e:  # noqa: BLE001 - a stale/deleted id skips, not aborts
                failed_enable.append(pid)
                logger.warning(
                    "[PROFILE-RECONCILE] group=%s: profile %s enable failed, "
                    "skipping: %s", group_id, pid, e,
                )
        return _result(
            "partial_failure" if failed_enable else "reconciled", group_id,
            effective_gid=effective_gid, scoped=len(channel_ids),
            excluded=owned_count, released=released, enabled=profiles_enabled,
            failed_profile_ids=failed_enable, conflict=conflict,
        )

    # Authoritative universe in hand. Intersect the stored selection with it:
    # any selected id NOT present has been DELETED in Dispatcharr (nothing
    # prunes the stored selection), so it can neither be a member nor be enabled
    # (a bulk enable on a dead profile 404s). We iterate the universe as the
    # authoritative set and deliberately do NOT union the stale ids back in.
    universe_set = set(universe_ids)
    valid_selected = selected & universe_set

    if not valid_selected:
        # Every selected profile is gone (or the universe is authoritatively
        # empty). Disabling the channels in every real profile now would strand
        # them in zero profiles — the exact harm decision 1a forbids. Treat it
        # as a SAFETY NO-OP: touch nothing, surface a distinct status.
        logger.warning(
            "[PROFILE-RECONCILE] group=%s effective=%s: entire profile selection "
            "%s is stale (no selected profile exists in the universe %s) — "
            "leaving %d channel(s) UNTOUCHED rather than disabling everywhere",
            group_id, effective_gid, sorted(selected), sorted(universe_set),
            len(channel_ids),
        )
        return _result(
            "stale_selection", group_id, effective_gid=effective_gid,
            excluded=owned_count, released=released, conflict=conflict,
        )

    # Phase 1 — ENABLE the selected profiles FIRST (enable-first, Blocker 1).
    # If ANY selected-profile enable FAILS (transient 5xx/network) we must NOT
    # proceed to the disables: disabling every non-selected profile while a
    # needed enable did not land would remove the channels from every profile
    # and strand them. Abort with a degraded status and the failed ids.
    enabled_ok: list[int] = []
    failed_enable = []
    for pid in universe_ids:
        if pid not in valid_selected:
            continue
        try:
            await client.bulk_update_profile_channels(
                pid, {"channel_ids": channel_ids, "enabled": True}
            )
            enabled_ok.append(pid)
        except Exception as e:  # noqa: BLE001 - captured, aborts before disables
            failed_enable.append(pid)
            logger.warning(
                "[PROFILE-RECONCILE] group=%s: SELECTED profile %s enable failed: %s",
                group_id, pid, e,
            )

    if failed_enable:
        logger.warning(
            "[PROFILE-RECONCILE] group=%s effective=%s: ABORTING before any "
            "disable — selected-profile enable failed for %s; channels left "
            "as-is to avoid stranding (retry on next reconcile)",
            group_id, effective_gid, sorted(failed_enable),
        )
        return _result(
            "partial_failure", group_id, effective_gid=effective_gid,
            scoped=len(channel_ids), excluded=owned_count, released=released,
            enabled=len(enabled_ok), disabled=0,
            failed_profile_ids=failed_enable, conflict=conflict,
        )

    # Phase 2 — every selected enable landed; now DISABLE the non-selected
    # universe profiles. A disable failure here is NON-destructive (the channel
    # merely stays in a profile it shouldn't be), so best-effort continue but
    # record it for truthful status (Should-Fix 5).
    profiles_disabled = 0
    failed_disable: list[int] = []
    for pid in universe_ids:
        if pid in valid_selected:
            continue
        try:
            await client.bulk_update_profile_channels(
                pid, {"channel_ids": channel_ids, "enabled": False}
            )
            profiles_disabled += 1
        except Exception as e:  # noqa: BLE001 - non-destructive, best-effort continue
            failed_disable.append(pid)
            logger.warning(
                "[PROFILE-RECONCILE] group=%s: profile %s disable failed, "
                "skipping: %s", group_id, pid, e,
            )

    status = "partial_failure" if failed_disable else "reconciled"
    logger.info(
        "[PROFILE-RECONCILE] group=%s effective=%s: %s %d channel(s) "
        "(%d pipeline-owned excluded, %d released) into %d profile(s), "
        "disabled in %d, failed %s (selection=%s)",
        group_id, effective_gid, status, len(channel_ids), owned_count,
        released, len(enabled_ok), profiles_disabled, sorted(failed_disable),
        sorted(valid_selected),
    )
    return _result(
        status, group_id, effective_gid=effective_gid, scoped=len(channel_ids),
        excluded=owned_count, released=released, enabled=len(enabled_ok),
        disabled=profiles_disabled, failed_profile_ids=failed_disable,
        conflict=conflict,
    )


def groups_with_selection(all_settings: dict) -> list[int]:
    """Return the group ids that carry a non-empty ``channel_profile_ids``."""
    return [
        gid
        for gid, setting in all_settings.items()
        if _selection_from_setting(setting) is not None
    ]


def dedupe_gids_by_effective_group(all_settings: dict, gids) -> list[int]:
    """Collapse ``gids`` to one per EFFECTIVE channel-group id (Blocker 3 / 6).

    A Channel Group Override makes a SOURCE group's channels live in its TARGET
    group, so if both a source and its target carry a selection they would each
    reconcile the SAME channels — order-dependent last-writer-wins. Keep one gid
    per effective id, preferring the TARGET group's own selection (the group
    whose channels physically live there outranks a source redirecting into
    it). Order-independent: the target wins regardless of iteration order.
    """
    effective_to_gid: dict[int, int] = {}
    for gid in gids:
        eff = resolve_effective_master_group_id(all_settings, gid)
        if eff not in effective_to_gid or gid == eff:
            effective_to_gid[eff] = gid
    return list(effective_to_gid.values())


def resolve_save_reconcile_targets(all_settings: dict, edited_gids) -> list[int]:
    """The effective-group WINNER gids the SAVE hook must reconcile.

    Should-Fix 2 (no flap): the save hook must pick EXACTLY the same winner the
    sweep would, or an override source/target that carry different selections
    would flap every monitor pass. The sweep resolves winners by deduping ALL
    ``groups_with_selection`` by effective group (target-preferred); the save
    hook must therefore resolve over the SAME full selection set — not just over
    its edited gids — and reconcile whichever winners share an effective group
    with something this save touched. Deterministic and order-independent.
    """
    sweep_winners = dedupe_gids_by_effective_group(
        all_settings, groups_with_selection(all_settings)
    )
    touched_effective = {
        resolve_effective_master_group_id(all_settings, gid) for gid in edited_gids
    }
    return [
        w for w in sweep_winners
        if resolve_effective_master_group_id(all_settings, w) in touched_effective
    ]


async def reconcile_all_selected_groups(
    client, all_settings: dict | None = None, cancel_check=None,
) -> dict:
    """Reconcile every group that carries a profile selection.

    Convenience entrypoint for the converging hooks (change monitor,
    post-refresh poll). Fetches ``all_settings`` once if not supplied, resolves
    the live profile-assigning rule set ONCE (shared across every group so the
    handoff query runs a single time per sweep), dedupes by effective group,
    and reconciles each. Returns aggregate counts; one group's failure is
    logged and does not abort the rest. ``partial_failure`` groups are counted
    distinctly from fully ``reconciled`` ones.

    ``cancel_check`` (NIT 7): an optional ``() -> bool`` predicate checked
    between groups so a long sweep aborts promptly when the caller (the monitor)
    requests cancellation. Best-effort — omit for callers with no cancel signal.
    """
    if all_settings is None:
        try:
            all_settings = await client.get_all_m3u_group_settings()
        except Exception as e:  # noqa: BLE001
            logger.warning("[PROFILE-RECONCILE] failed to fetch group settings: %s", e)
            return {
                "groups_reconciled": 0, "groups_partial_failure": 0,
                "groups_with_selection": 0, "channels_scoped": 0,
            }

    live_rule_ids = await _resolve_live_rule_ids()

    target_gids = groups_with_selection(all_settings)
    reconcile_gids = dedupe_gids_by_effective_group(all_settings, target_gids)

    groups_reconciled = 0
    groups_partial_failure = 0
    channels_scoped = 0
    for gid in reconcile_gids:
        if cancel_check is not None and cancel_check():
            logger.info(
                "[PROFILE-RECONCILE] sweep cancelled mid-run after %d group(s)",
                groups_reconciled + groups_partial_failure,
            )
            break
        try:
            result = await reconcile_group_profiles(
                client, all_settings, gid, live_rule_ids=live_rule_ids
            )
            status = result.get("status")
            if status == "reconciled":
                groups_reconciled += 1
                channels_scoped += result.get("channels_scoped", 0)
            elif status == "partial_failure":
                groups_partial_failure += 1
                channels_scoped += result.get("channels_scoped", 0)
        except Exception as e:  # noqa: BLE001 - isolate per-group failures
            logger.warning(
                "[PROFILE-RECONCILE] group=%s reconcile failed: %s", gid, e
            )

    if target_gids:
        logger.info(
            "[PROFILE-RECONCILE] swept %d group(s) with a selection (%d after "
            "effective-group dedupe), reconciled %d, partial_failure %d, "
            "scoped %d channel(s)",
            len(target_gids), len(reconcile_gids), groups_reconciled,
            groups_partial_failure, channels_scoped,
        )
    return {
        "groups_reconciled": groups_reconciled,
        "groups_partial_failure": groups_partial_failure,
        "groups_with_selection": len(target_gids),
        "channels_scoped": channels_scoped,
    }
