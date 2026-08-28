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
from services.m3u_group_state import (
    acquire_effective_group_locks,
    coerce_profile_id,
    effective_group_lock,
)

logger = logging.getLogger(__name__)

# --- Per-effective-group serialization (SAME-PROCESS only) -----------------
# The reconcile entrypoints (save hook, post-refresh poll, monitor every-pass
# sweep) and the Channel Pipeline's assign_channel_profile write run as separate
# async tasks; without serialization they could interleave for the SAME
# effective group (enable{A} -> enable{B} -> disable{A} -> disable{B}) and leave
# channels in ZERO profiles. A lazily-created asyncio.Lock per effective group id
# serializes them WITHIN THIS PROCESS.
#
# HONEST GUARANTEE (right-sized for a single-operator home-lab tool): this is an
# IN-PROCESS lock. It does NOT serialize concurrent mutations across processes.
# The background monitor/scheduler runs only in the MAIN process (the HTTPS
# subprocess, ECM_HTTPS_SUBPROCESS=1, skips background services — main.py), but
# that subprocess STILL serves HTTP requests, so a group-settings save handled
# there can run concurrently with a main-process sweep unserialized. Cross-
# process serialization (a distributed lock / single-writer TLS proxy) is OUT OF
# SCOPE at this tier and DEFERRED to bead nq3ed. The design assumption here is a
# single operator making one change at a time.
#
# Bound on lock re-acquisition when a concurrent override retarget keeps moving
# the effective group under us (Should-Fix 2) — avoids a pathological flip-flop.
_LOCK_REACQUIRE_MAX = 3

# Coalescing guard: a full selected-group sweep already running short-circuits a
# redundant one (the monitor fires every pass; an overlapping post-refresh poll
# makes another a no-op). A coalesced follower gets a distinct ``queued`` outcome
# (see _queued_result) — NEVER mapped to success — and the idempotent scheduled
# sweep converges the deferred work. (Round-9: the earlier _sweep_pending
# trailing-pass loop was unbounded + false-success and has been removed.)
_sweep_in_progress = False


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
    the caller treats this as a NO-OP). Values are coerced to int (numeric
    strings accepted for legacy back-compat); genuinely non-numeric entries are
    dropped with a warning so a corrupt id can never silently strand the group.
    """
    if not isinstance(setting, dict):
        return None
    cp = setting.get("custom_properties")
    if not isinstance(cp, dict):
        return None
    raw = cp.get("channel_profile_ids")
    if not isinstance(raw, list) or not raw:
        return None
    selection = []
    for pid in raw:
        coerced = coerce_profile_id(pid)
        if coerced is None:
            logger.warning(
                "[PROFILE-RECONCILE] dropping non-integer channel_profile_id %r",
                pid,
            )
        else:
            selection.append(coerced)
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
    is retained.

    Blocker 2 (clobber): Dispatcharr's channel PATCH replaces custom_properties
    WHOLESALE, so we fetch the channel's CURRENT custom_properties immediately
    before the merge (not the reconcile snapshot, which may be seconds stale) to
    minimise the window in which a concurrent EPG/logo/metadata write is erased.
    A failure is logged and swallowed — never fail the reconcile over a marker
    cleanup.
    """
    cid = channel.get("id")
    if cid is None:
        return
    # Fresh-fetch the current custom_properties right before the PATCH. Blocker
    # 5: if the fresh read FAILS, do NOT write from the stale snapshot (a
    # wholesale PATCH from stale data would re-introduce lost-update). SKIP the
    # clear entirely — the release retries on the next sweep.
    try:
        fresh = await client.get_channel(cid)
    except Exception as e:  # noqa: BLE001 - fail closed (no write from stale)
        logger.warning(
            "[PROFILE-RECONCILE] channel %s: fresh custom_properties read FAILED "
            "(%s) — SKIPPING the marker clear rather than writing from stale "
            "snapshot; will retry on the next sweep", cid, e,
        )
        return
    cp = fresh.get("custom_properties") if isinstance(fresh, dict) else None
    if not isinstance(cp, dict):
        return
    if (PIPELINE_OWNERSHIP_MARKER_KEY not in cp
            and PIPELINE_OWNERSHIP_RULE_ID_KEY not in cp):
        return  # Already clear (another run beat us) — no write needed.
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


class _ReconcileCancelled(Exception):
    """Raised when ``cancel_check`` fires during a long in-group operation
    (pagination / profile writes) so the group aborts promptly and cleanly
    (reported ``degraded``, not errored) — Finding: cancel during long phases."""


def _check_cancel(cancel_check):
    if cancel_check is not None and cancel_check():
        raise _ReconcileCancelled()


async def _fetch_group_channels(client, group_id: int, cancel_check=None) -> list[dict]:
    """Enumerate every channel in ``group_id`` via paginated ``get_channels``.

    ``get_channels`` filters by group NAME under the hood (it translates the
    id), so this returns every channel whose group name matches — consistent
    with the global-by-name selection semantics documented at module level.
    ``cancel_check`` is polled between pages so a long enumeration aborts
    promptly on cancellation.
    """
    channels: list[dict] = []
    page = 1
    while page <= _MAX_CHANNEL_PAGES:
        _check_cancel(cancel_check)
        response = await client.get_channels(
            page=page, page_size=_CHANNEL_PAGE_SIZE, channel_group=group_id
        )
        results = response.get("results", []) if isinstance(response, dict) else []
        channels.extend(results)
        if not results or not response.get("next"):
            break
        page += 1
    return channels


async def _recheck_newly_owned(client, effective_gid, live_rule_ids, cancel_check=None) -> set:
    """Re-fetch the group's channels and return the set of channel ids that are
    NOW pipeline-owned (Blocker 2a pre-write ownership re-check). Raises on fetch
    failure so the caller can fail closed rather than risk a membership clobber."""
    recheck = await _fetch_group_channels(client, effective_gid, cancel_check)
    return {
        c["id"] for c in recheck
        if c.get("id") is not None and _ownership_state(c, live_rule_ids) == "owned"
    }


def _result(status: str, group_id: int, *, effective_gid=None, scoped=0,
            excluded=0, released=0, enabled=0, disabled=0,
            failed_profile_ids=None, conflict=False, error=None) -> dict:
    """Build a uniform reconcile result dict so every caller can rely on the
    same keys (status, counts, failed_profile_ids, conflict, error).

    ``status`` vocabulary: ``no_selection`` | ``no_channels`` | ``conflict`` |
    ``stale_selection`` | ``reconciled`` | ``partial_failure`` | ``degraded``
    (enables applied but exclusivity could NOT be enforced — universe fetch
    failed) | ``error`` (setup/exception before any per-group apply)."""
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
        "error": error,
    }


async def reconcile_group_profiles(
    client, all_settings: dict, group_id: int, live_rule_ids=_UNSET,
    settings_provider=None, cancel_check=None,
) -> dict:
    """Apply a group's stored profile selection to its channels (idempotent).

    Serialized per EFFECTIVE group (Blocker 1): all three entrypoints call this,
    so acquiring the effective-group lock here makes a group's enable+disable
    phases atomic against every concurrent reconcile of that group — no
    interleave can strand channels in zero profiles.

    ``settings_provider`` — optional ``() -> awaitable[dict]`` returning the
    freshest ``all_settings``. When supplied, after acquiring the lock we
    RE-READ the settings (TOCTOU revalidation): the selection/universe may have
    changed while we blocked, so we always apply the CURRENT selection, never a
    stale snapshot. Real callers (save hook, sweep) pass
    ``client.get_all_m3u_group_settings``; unit tests omit it and use the static
    settings passed in.

    ``live_rule_ids`` — the set of currently-enabled rule ids that still assign
    channel profiles. Pass it explicitly (the sweep computes it ONCE); left
    unset it is resolved from the DB. Explicit ``None`` means resolution failed
    and every marker is treated conservatively as still-owned.

    Returns a uniform result dict (see :func:`_result`).
    """
    if live_rule_ids is _UNSET:
        live_rule_ids = await _resolve_live_rule_ids()

    # Acquire the lock on the group we will ACTUALLY mutate (Should-Fix 2). The
    # effective group is derived from ``all_settings``, but revalidation may
    # re-fetch settings under the lock and — if a concurrent Channel-Group-
    # Override retarget happened — resolve to a DIFFERENT effective group. If we
    # held the pre-revalidation lock we could mutate the new group while another
    # task holds the new group's lock, interleaving enable/disable and stranding
    # channels. So: revalidate, and if the effective group changed, RELEASE and
    # RE-ACQUIRE under the new key. Bounded to avoid a pathological flip-flop.
    #
    # Blocker 4 (FAIL CLOSED): if the lock key can't be stabilised within the
    # bound, OR the post-lock revalidation fetch fails, do NOT issue any
    # membership writes — return ``degraded`` and let the scheduled sweep retry.
    # Proceeding under a stale/unstable key could destructively interleave or
    # overwrite newer desired state.
    for _attempt in range(_LOCK_REACQUIRE_MAX):
        lock_key = resolve_effective_master_group_id(all_settings, group_id)
        async with effective_group_lock(lock_key):
            if settings_provider is not None:
                try:
                    all_settings = await settings_provider()
                except Exception as e:  # noqa: BLE001 - fail closed
                    logger.warning(
                        "[PROFILE-RECONCILE] group=%s: post-lock revalidation "
                        "fetch FAILED (%s) — failing closed (no writes), degraded",
                        group_id, e,
                    )
                    return _result(
                        "degraded", group_id, effective_gid=lock_key,
                        error="revalidation fetch failed; no writes issued",
                    )
            effective_gid = resolve_effective_master_group_id(all_settings, group_id)
            if effective_gid == lock_key:
                try:
                    setting = all_settings.get(group_id)
                    if isinstance(setting, dict) and setting.get(
                        "_ecm_channel_profile_conflict"
                    ):
                        try:
                            from services.profile_conflict_review import (
                                ensure_profile_conflict_review_under_lock,
                            )
                            await ensure_profile_conflict_review_under_lock(
                                client, all_settings, effective_gid
                            )
                        except Exception as e:  # noqa: BLE001 - freeze still wins
                            logger.warning(
                                "[PROFILE-RECONCILE] effective=%s: could not "
                                "reconcile conflict review queue: %s",
                                effective_gid, e,
                            )
                    return await _reconcile_group_locked(
                        client, all_settings, group_id, effective_gid,
                        live_rule_ids, cancel_check,
                    )
                except _ReconcileCancelled:
                    logger.info(
                        "[PROFILE-RECONCILE] group=%s: cancelled mid-reconcile — "
                        "degraded (no further writes)", group_id,
                    )
                    return _result(
                        "degraded", group_id, effective_gid=effective_gid,
                        error="cancelled mid-reconcile",
                    )
        # effective group changed under us — loop to re-acquire the correct lock.

    # Exhausted the bound without stabilising the lock key — FAIL CLOSED.
    final_key = resolve_effective_master_group_id(all_settings, group_id)
    logger.warning(
        "[PROFILE-RECONCILE] group=%s: effective group kept changing under the "
        "lock after %d attempts — failing closed (no writes), degraded",
        group_id, _LOCK_REACQUIRE_MAX,
    )
    return _result(
        "degraded", group_id, effective_gid=final_key,
        error="effective group unstable under lock; no writes issued",
    )


async def _reconcile_group_locked(
    client, all_settings: dict, group_id: int, effective_gid: int, live_rule_ids,
    cancel_check=None,
) -> dict:
    """The reconcile body, run while holding the effective-group lock.

    May raise :class:`_ReconcileCancelled` if ``cancel_check`` fires during a
    long phase — the caller maps that to a clean ``degraded`` result."""
    setting = all_settings.get(group_id)
    selection = _selection_from_setting(setting)
    conflict = bool(isinstance(setting, dict) and setting.get("_ecm_channel_profile_conflict"))
    if conflict:
        logger.warning(
            "[PROFILE-RECONCILE] group=%s effective=%s: profile selections "
            "conflict; membership is frozen pending operator review",
            group_id, effective_gid,
        )
        return _result(
            "conflict", group_id, effective_gid=effective_gid, conflict=True,
            error="channel-profile membership is frozen pending review",
        )
    if selection is None:
        # Decision 1a: absent/unset selection is a read-only no-op.
        return _result("no_selection", group_id, conflict=conflict)

    selected = set(selection)

    channels = await _fetch_group_channels(client, effective_gid, cancel_check)

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
        # Sub-note (Finding 4 family): the executor now holds the SAME group lock
        # during a pipeline assign, so in the common case a pipeline stamp cannot
        # race this enable-only write. But if the executor SKIPPED its lock
        # (settings fetch failed), only this re-check protects a just-owned
        # channel from being additively enabled here — so guard the enable-only
        # path with the ownership re-check too (drop newly-owned before enabling).
        try:
            newly_owned = await _recheck_newly_owned(
                client, effective_gid, live_rule_ids, cancel_check
            )
        except Exception as e:  # noqa: BLE001 - fail closed rather than clobber
            logger.warning(
                "[PROFILE-RECONCILE] group=%s: enable-only ownership re-check "
                "fetch FAILED (%s) — failing closed (no writes), degraded",
                group_id, e,
            )
            return _result(
                "degraded", group_id, effective_gid=effective_gid,
                excluded=owned_count, released=released, conflict=conflict,
                error="enable-only ownership re-check fetch failed; no writes issued",
            )
        if newly_owned:
            n_drop = sum(1 for cid in channel_ids if cid in newly_owned)
            channel_ids = [cid for cid in channel_ids if cid not in newly_owned]
            owned_count += n_drop
        if not channel_ids:
            return _result(
                "no_channels", group_id, effective_gid=effective_gid,
                excluded=owned_count, released=released, conflict=conflict,
            )
        profiles_enabled = 0
        failed_enable: list[int] = []
        for pid in selection:
            _check_cancel(cancel_check)
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
        # Blocker 3a: enables were applied but EXCLUSIVITY could NOT be enforced
        # (we never learned the universe, so no disables ran). That is NOT a
        # clean reconcile — report ``degraded`` so the run/summary reflects the
        # incompleteness instead of a false success.
        return _result(
            "degraded", group_id,
            effective_gid=effective_gid, scoped=len(channel_ids),
            excluded=owned_count, released=released, enabled=profiles_enabled,
            failed_profile_ids=failed_enable, conflict=conflict,
            error="profile universe unavailable; exclusivity not enforced",
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

    # Blocker 2(a): RE-CHECK ownership immediately before the destructive writes.
    # A Channel Pipeline assign_channel_profile may have stamped a channel as
    # pipeline-owned AFTER our initial snapshot (the pipeline stamps the marker
    # BEFORE its exclusive-membership write — Blocker 2(b) — so a marker seen
    # here means the pipeline already owns it). Overwriting such a channel's
    # membership would clobber the pipeline decision, and the marker would keep
    # later sweeps preserving the wrong membership. Re-fetch the group's channels
    # under the lock and DROP any channel that became owned since the snapshot.
    try:
        newly_owned = await _recheck_newly_owned(
            client, effective_gid, live_rule_ids, cancel_check
        )
    except Exception as e:  # noqa: BLE001 - fail closed rather than risk a clobber
        logger.warning(
            "[PROFILE-RECONCILE] group=%s: ownership re-check fetch FAILED (%s) — "
            "failing closed (no writes), degraded", group_id, e,
        )
        return _result(
            "degraded", group_id, effective_gid=effective_gid,
            excluded=owned_count, released=released, conflict=conflict,
            error="ownership re-check fetch failed; no writes issued",
        )
    dropped = [cid for cid in channel_ids if cid in newly_owned]
    if dropped:
        logger.info(
            "[PROFILE-RECONCILE] group=%s: dropping %d channel(s) that became "
            "pipeline-owned since the snapshot (pre-write ownership re-check)",
            group_id, len(dropped),
        )
        channel_ids = [cid for cid in channel_ids if cid not in newly_owned]
        owned_count += len(dropped)
    if not channel_ids:
        return _result(
            "no_channels", group_id, effective_gid=effective_gid,
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
        _check_cancel(cancel_check)
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
        _check_cancel(cancel_check)
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
    it). When no target row carries a selection, the lowest source group id is
    the deterministic representative. Representatives only remove duplicate
    work; conflicted effective groups are refused before any membership write.
    """
    effective_to_gid: dict[int, int] = {}
    for gid in sorted(set(gids)):
        eff = resolve_effective_master_group_id(all_settings, gid)
        if eff not in effective_to_gid or gid == eff:
            effective_to_gid[eff] = gid
    return [effective_to_gid[eff] for eff in sorted(effective_to_gid)]


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


async def normalize_group_selections(client, all_settings: dict, cancel_check=None) -> dict:
    """Enforced-global DURABLE convergence (Blocker 3b): propagate each group's
    WINNING selection (``all_settings[gid]`` — the deterministic collapse
    winner) to EVERY M3U account row for that group that DIVERGES.

    Runs every sweep, UNCONDITIONALLY (not gated on "changed this request"), so a
    partially-failed NON-EMPTY cascade, a divergent sibling, or a stale row
    self-heals without an operator action. Writes are serialized under the
    per-effective-group locks (shared with reconcile). Best-effort — never
    raises; returns ``{normalized_accounts, failed_accounts, fetch_failed?}``.

    RIGHT-SIZING BOUNDARY (Finding 3 / bead nq3ed): normalize converges to the
    COLLAPSE WINNER, which prefers a row that HAS a selection. So a partially-
    failed CLEAR (operator cleared the selection but one sibling clear PATCH
    failed) leaves a stale has-selection sibling that the collapse re-elects, and
    normalize will then RESURRECT that stale selection into the cleared rows.
    Durably completing a clear needs an explicit "cleared" tombstone to
    distinguish it from "never managed" — that versioned/tombstone desired-state
    is DEFERRED to bead nq3ed. Here the save surfaces an incomplete clear
    honestly (degraded + named accounts) so the operator can retry; normalize
    does NOT durably auto-heal a partially-failed clear.
    """
    winning: dict[int, list[int]] = {}
    for gid, setting in all_settings.items():
        if isinstance(setting, dict) and setting.get("_ecm_channel_profile_conflict"):
            continue
        sel = _selection_from_setting(setting)
        if sel:
            winning[gid] = sorted(set(sel))
    if not winning:
        return {"normalized_accounts": 0, "failed_accounts": 0}

    try:
        accounts = await client.get_m3u_accounts()
    except Exception as e:  # noqa: BLE001
        # Honesty finding (B1): a failed account-list fetch is NOT zero failures —
        # normalize could not converge ANY divergent sibling this pass. Count it
        # so the sweep/monitor reflect a warning instead of a false green.
        logger.warning("[PROFILE-RECONCILE] normalize: could not list accounts: %s", e)
        return {"normalized_accounts": 0, "failed_accounts": 1, "fetch_failed": True}
    if not isinstance(accounts, list):
        logger.warning("[PROFILE-RECONCILE] normalize: account list unavailable (got %r)", type(accounts))
        return {"normalized_accounts": 0, "failed_accounts": 1, "fetch_failed": True}

    normalized = 0
    failed = 0
    for acct in accounts:
        if cancel_check is not None and cancel_check():
            break
        aid = acct.get("id")
        if aid is None:
            continue
        rows_to_write = []
        for row in acct.get("channel_groups", []):
            gid = row.get("channel_group")
            desired = winning.get(gid)
            if desired is None:
                continue
            current = sorted(set(_selection_from_setting(row) or []))
            raw = (row.get("custom_properties") or {}).get("channel_profile_ids")
            # Finding 2: skip only when the row's SELECTION matches AND it is
            # already stored in the canonical INTEGER list form. A legacy
            # ["12"] row coerces to the same selection but is not canonical, so
            # it is rewritten to int storage (divergence otherwise persists).
            if current == desired and raw == list(desired):
                continue
            new_cp = dict(row.get("custom_properties") or {})
            new_cp["channel_profile_ids"] = list(desired)
            new_cp.pop("_ecm_channel_profile_conflict", None)  # ECM-synthetic
            rows_to_write.append({**row, "custom_properties": new_cp})
        if not rows_to_write:
            continue
        eff_gids = {
            resolve_effective_master_group_id(all_settings, r["channel_group"])
            for r in rows_to_write
        }
        try:
            async with acquire_effective_group_locks(eff_gids):
                await client.update_m3u_group_settings(aid, {"group_settings": rows_to_write})
            normalized += 1
            logger.info(
                "[PROFILE-RECONCILE] normalize: converged account %s (%d divergent "
                "group row(s))", aid, len(rows_to_write),
            )
        except Exception as e:  # noqa: BLE001 - best-effort per account
            failed += 1
            logger.warning(
                "[PROFILE-RECONCILE] normalize: account %s update failed: %s", aid, e
            )
    return {"normalized_accounts": normalized, "failed_accounts": failed}


def _queued_result() -> dict:
    """A coalesced follower's outcome. Round-9 (B2&B3): this is a DISTINCT
    NON-TERMINAL status — ``queued`` — that NO caller may map to success. It is
    NOT a completed sweep: the reconcile did not run this call. The idempotent
    scheduled sweep (every ~5 min) is the convergence guarantee, so a follower
    can safely be dropped WITHOUT a trailing pass (the earlier over-built
    ``_sweep_pending`` trailing loop was unbounded and produced a false-success
    all-zero result — removed)."""
    return {"status": "queued", "coalesced": True}


async def reconcile_all_selected_groups(
    client, all_settings: dict | None = None, cancel_check=None,
) -> dict:
    """Reconcile every group that carries a profile selection (COALESCED).

    Coalesce redundant sweeps — if a full sweep is already in flight (the monitor
    fires every pass; a post-refresh poll may overlap), this call short-circuits
    and returns a ``{status: "queued"}`` NON-TERMINAL outcome that no caller maps
    to success. The in-flight sweep + the idempotent every-~5-min scheduled sweep
    are the convergence guarantee; the deferred follower needs no trailing pass.
    """
    global _sweep_in_progress
    if _sweep_in_progress:
        logger.info("[PROFILE-RECONCILE] sweep already in progress — coalescing (queued)")
        return _queued_result()
    _sweep_in_progress = True
    try:
        return await _run_selected_group_sweep(client, all_settings, cancel_check)
    finally:
        _sweep_in_progress = False


async def _run_selected_group_sweep(
    client, all_settings: dict | None = None, cancel_check=None,
) -> dict:
    """Reconcile every group that carries a profile selection.

    Fetches ``all_settings`` once if not supplied, NORMALIZES divergent sibling
    rows (Blocker 3b), resolves the live profile-assigning rule set ONCE, dedupes
    by effective group, and reconciles each. Returns aggregate counts; one
    group's failure is logged and does not abort the rest.

    ``cancel_check`` — an optional ``() -> bool`` predicate checked between groups
    AND threaded into each group's long phases (pagination / profile writes) so a
    long sweep aborts promptly on cancellation.
    """
    if all_settings is None:
        try:
            all_settings = await client.get_all_m3u_group_settings()
        except Exception as e:  # noqa: BLE001
            # Finding: a failed group-settings fetch is NOT a clean sweep — count
            # it as an errored sweep so task history isn't falsely green.
            logger.warning("[PROFILE-RECONCILE] failed to fetch group settings: %s", e)
            return {
                "groups_reconciled": 0, "groups_partial_failure": 0,
                "groups_degraded": 0, "groups_errored": 1,
                "groups_conflicted": 0,
                "accounts_normalized": 0, "accounts_normalize_failed": 0,
                "groups_with_selection": 0, "channels_scoped": 0,
            }

    try:
        from services.profile_conflict_review import reconcile_profile_conflict_reviews
        await reconcile_profile_conflict_reviews(client, all_settings)
    except Exception as e:  # noqa: BLE001 - review queue must not abort membership sweep
        logger.warning("[PROFILE-RECONCILE] conflict review queue pass failed: %s", e)

    # Blocker 3b: normalize divergent sibling rows FIRST (durable convergence),
    # so the membership reconcile below sees converged per-account selections.
    normalize_result = {}
    try:
        normalize_result = await normalize_group_selections(
            client, all_settings, cancel_check=cancel_check
        )
    except Exception as e:  # noqa: BLE001 - best-effort, never abort the sweep
        logger.warning("[PROFILE-RECONCILE] normalize pass failed: %s", e)

    live_rule_ids = await _resolve_live_rule_ids()

    target_gids = groups_with_selection(all_settings)
    reconcile_gids = dedupe_gids_by_effective_group(all_settings, target_gids)

    # Revalidate each group's selection under its lock (Blocker 1 TOCTOU) when
    # the client can re-fetch; the FakeClient in unit tests has no such method
    # so revalidation is simply skipped there.
    settings_provider = getattr(client, "get_all_m3u_group_settings", None)

    groups_reconciled = 0
    groups_partial_failure = 0
    groups_degraded = 0
    groups_errored = 0
    groups_conflicted = 0
    channels_scoped = 0
    for gid in reconcile_gids:
        if cancel_check is not None and cancel_check():
            logger.info(
                "[PROFILE-RECONCILE] sweep cancelled mid-run after %d group(s)",
                groups_reconciled + groups_partial_failure + groups_degraded,
            )
            break
        try:
            result = await reconcile_group_profiles(
                client, all_settings, gid, live_rule_ids=live_rule_ids,
                settings_provider=settings_provider, cancel_check=cancel_check,
            )
            status = result.get("status")
            if status == "reconciled":
                groups_reconciled += 1
                channels_scoped += result.get("channels_scoped", 0)
            elif status == "partial_failure":
                groups_partial_failure += 1
                channels_scoped += result.get("channels_scoped", 0)
            elif status == "degraded":
                groups_degraded += 1
                channels_scoped += result.get("channels_scoped", 0)
            elif status == "error":
                groups_errored += 1
            elif status == "conflict":
                groups_conflicted += 1
        except Exception as e:  # noqa: BLE001 - isolate per-group failures
            # Should-Fix 3: a per-group EXCEPTION (e.g. get_channels raising)
            # must count as an error so the monitor's warning aggregation sees
            # it — otherwise a hard per-group failure reads as clean success.
            groups_errored += 1
            logger.warning(
                "[PROFILE-RECONCILE] group=%s reconcile failed: %s", gid, e
            )

    if target_gids:
        logger.info(
            "[PROFILE-RECONCILE] swept %d group(s) with a selection (%d after "
            "effective-group dedupe), reconciled %d, partial_failure %d, "
            "degraded %d, conflicted %d, errored %d, scoped %d channel(s)",
            len(target_gids), len(reconcile_gids), groups_reconciled,
            groups_partial_failure, groups_degraded, groups_conflicted, groups_errored,
            channels_scoped,
        )
    return {
        "groups_reconciled": groups_reconciled,
        "groups_partial_failure": groups_partial_failure,
        "groups_degraded": groups_degraded,
        "groups_errored": groups_errored,
        "groups_conflicted": groups_conflicted,
        # Account-domain normalize counters kept SEPARATE from the group-domain
        # counters above so the caller never conflates the two (Finding: counter
        # semantics).
        "accounts_normalized": normalize_result.get("normalized_accounts", 0),
        "accounts_normalize_failed": normalize_result.get("failed_accounts", 0),
        "groups_with_selection": len(target_gids),
        "channels_scoped": channels_scoped,
    }
