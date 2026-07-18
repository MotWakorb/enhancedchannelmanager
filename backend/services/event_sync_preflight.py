"""Event Sync pre-flight checks (bead enhancedchannelmanager-ti939.1.3).

Verifies the Dispatcharr-side preconditions of an event_sync rule BEFORE a
preview (Phase 1A) or run (Phase 1B) executes:

* the MASTER group has ``auto_channel_sync`` **ON** — Dispatcharr owns the
  master channels' lifecycle; with auto-sync off no master channels exist
  and the whole feature silently matches nothing, so this must surface as
  an explicit failure in results rather than an empty preview;
* every SECONDARY group has ``auto_channel_sync`` **OFF** — a secondary
  with auto-sync on means Dispatcharr is creating duplicate channels from
  a group that event_sync treats as a pure stream source.

**READ-ONLY by contract.** This module inspects group settings via the
Dispatcharr client's ``get_all_m3u_group_settings()`` and must NEVER write
or toggle any Dispatcharr setting — guided toggles are a Phase 2 operator
convenience (epic ti939.3), and even then they are explicit operator
actions, never a side effect of a check. Unit tests pin this with a
client mock exposing only the read method.

Known edge (documented, not coded around): Dispatcharr channel groups are
GLOBAL BY NAME (bd-dgs64, config.py ~L104-117). ``get_all_m3u_group_settings``
collapses per-account settings to one entry per group ID, preferring an
entry with ``auto_channel_sync`` enabled — so a secondary group whose NAME
is also auto-synced by another account reads as auto_channel_sync ON here
and fails the secondary check. That is the correct surface: ANY account
auto-syncing that group ID means Dispatcharr is creating channels from it.
Real event groups are provider-distinct-named.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Machine-readable check identifiers (preview/run results contract).
CHECK_MASTER_AUTO_SYNC_ON = "master_auto_sync_on"
CHECK_SECONDARY_AUTO_SYNC_OFF = "secondary_auto_sync_off"
CHECK_GROUP_SETTINGS_FOUND = "group_settings_found"
# bead 2ey2y: rule-level WARNING id (surfaced in ``warnings``, never flips
# ``ok``) — the stale-dateless demote rail is enabled but has no snapshot
# data to act on, so it silently fails open.
CHECK_STALENESS_RAIL_SNAPSHOTS = "staleness_rail_snapshots"


def _build_override_maps(all_settings: dict) -> tuple[dict, dict]:
    """Resolve Dispatcharr Channel Group Override relationships (bead 6xxmp/override).

    A Channel Group Override never rewrites a STREAM's group, but auto-sync
    creates the CHANNELS into the override TARGET group, while the
    ``auto_channel_sync`` setting lives on the ORIGINAL (source) group
    (``custom_properties.group_override`` holds the target group ID — same
    shape ``routers/channel_groups.py`` reads for orphan detection). So an
    operator who points the master at their auto-synced provider group gets
    an EMPTY master set (its channels are in the target), and an operator who
    points it at the target hits a group with no direct M3U setting.

    Returns ``(target_to_source_setting, source_to_target_id)``:
    * ``target_to_source_setting``: override TARGET group id -> the source
      group's setting dict (preferring an ``auto_channel_sync``-ON source —
      that is the one actually creating channels into the target).
    * ``source_to_target_id``: source group id -> its override TARGET id.
    """
    target_to_source: dict[int, dict] = {}
    source_to_target: dict[int, int] = {}
    for gid, setting in all_settings.items():
        cp = setting.get("custom_properties")
        target = cp.get("group_override") if isinstance(cp, dict) else None
        if not isinstance(target, int):
            continue
        source_to_target[gid] = target
        existing = target_to_source.get(target)
        if existing is None or (
            setting.get("auto_channel_sync")
            and not existing.get("auto_channel_sync")
        ):
            target_to_source[target] = setting
    return target_to_source, source_to_target


def resolve_effective_master_group_id(
    all_settings: dict, master_group_id: int
) -> int:
    """Channel-group id where the master group's channels ACTUALLY live.

    Follows a Channel Group Override: if the configured master group is an
    override SOURCE (an auto-synced group whose channels Dispatcharr places
    in a different target group), the auto-created master channels live in
    that TARGET group — so callers must fetch/filter master channels by the
    returned id, not the raw ``master_group_id``. A group with no override
    resolves to itself; picking the target directly also resolves to itself.
    """
    _target_to_source, source_to_target = _build_override_maps(all_settings)
    return source_to_target.get(master_group_id, master_group_id)


def _failure(
    *, group_id: int, role: str, check: str, expected: str, got: str,
    message: str, conflicting_rule: str | None = None,
) -> dict:
    """One pre-flight failure entry (teaching shape: expected/got/message).

    ``conflicting_rule`` (bead yjchp): the name of ANOTHER enabled
    event_sync rule whose MASTER group is the failing group — present only
    on cross-rule ``secondary_auto_sync_off`` failures, so the UI can link
    the two rules. Omitted (not null-valued) otherwise to keep the
    long-standing failure shape byte-identical for every other failure.
    """
    failure = {
        "group_id": group_id,
        "role": role,
        "check": check,
        "expected": expected,
        "got": got,
        "message": message,
    }
    if conflicting_rule is not None:
        failure["conflicting_rule"] = conflicting_rule
    return failure


def build_event_sync_master_rule_lookup(
    rules, *, exclude_rule_id: int | None = None
) -> dict[int, str]:
    """Map master_group_id -> rule name for ENABLED event_sync rules.

    Cross-rule context for :func:`check_event_sync_group_settings` (bead
    yjchp): when rule A lists a group as a SECONDARY while that same group
    is rule B's MASTER, the ``secondary_auto_sync_off`` failure's stock
    advice ("disable auto_channel_sync") would break rule B — masters
    REQUIRE auto-sync ON. Callers build this lookup from their rule source
    (engine: DB-loaded rules; preview: a session query) and pass it in;
    this module stays free of DB imports.

    ``rules`` is any iterable of ChannelPipelineRule-shaped objects
    (``.id`` / ``.name`` / ``.is_event_sync()`` / ``.get_event_sync_config()``).
    ``exclude_rule_id`` drops the rule under check itself — a rule is never
    its own conflict. Rules whose config is missing or explicitly disabled
    (``enabled: false``) are skipped: a disabled rule's master imposes no
    live constraint.
    """
    lookup: dict[int, str] = {}
    for rule in rules:
        if exclude_rule_id is not None and rule.id == exclude_rule_id:
            continue
        if not rule.is_event_sync():
            continue
        config = rule.get_event_sync_config()
        if not config or not config.get("enabled", True):
            continue
        master_group_id = config.get("master_group_id")
        if master_group_id is None:
            master_group_id = (config.get("master") or {}).get("group_id")
        if master_group_id is not None:
            lookup[master_group_id] = rule.name
    return lookup


def count_snapshot_covered_streams(
    resolved_streams, group_names: dict, stale_lookup: dict
) -> int:
    """How many resolved secondary streams have snapshot COVERAGE (bead 2ey2y).

    A stream is *covered* when its (provider, group-name) pair was captured
    in the previous-day staleness lookup
    (:func:`services.event_sync_staleness.previous_day_names`) — i.e. the
    stale-dateless demote rail has data it can act on for that stream.
    Coverage is NOT a staleness verdict: a group captured AT the snapshot
    cap still counts as covered, because positive membership (the only
    demote trigger) remains detectable for capped groups.

    ``resolved_streams`` is any iterable of resolver ``ResolvedStream``-shaped
    objects (``.stream.provider_id`` / ``.stream.group_id``); ``group_names``
    maps group id -> channel-group NAME (snapshot groups are keyed by name).
    """
    covered = 0
    for r in resolved_streams:
        stream = r.stream
        gname = group_names.get(stream.group_id)
        if (
            stream.provider_id is not None
            and gname
            and gname in stale_lookup.get(stream.provider_id, {})
        ):
            covered += 1
    return covered


def build_staleness_rail_warning(
    config: dict,
    *,
    secondary_stream_count: int,
    snapshot_covered_count: int,
) -> dict | None:
    """Rule-level pre-flight WARNING: the stale-dateless rail is inert (2ey2y).

    The jqwfq demote rail (``assume_current_date`` + ``demote_stale_dateless``)
    FAILS OPEN when no qualifying M3USnapshot covers the rule's secondary
    streams — correct behavior, but silently inert: the operator believes the
    rail protects them when it cannot. This surfaces that state as an explicit
    warning in the pre-flight result's ``warnings`` list (never a failure —
    ``ok`` is untouched, nothing is misconfigured on the Dispatcharr side).

    Fires only when ALL of:

    * ``assume_current_date`` is on (the rail is otherwise irrelevant);
    * ``demote_stale_dateless`` is on (absent key reads TRUE — the backend
      default, matching the engine/schema default-fill);
    * at least one secondary stream was fetched (an empty fetch has louder
      problems than rail coverage);
    * ZERO fetched streams are snapshot-covered
      (:func:`count_snapshot_covered_streams`).

    Returns the warning entry dict, or ``None`` when the rail is off or has
    usable data. Entry shape mirrors the failure teaching shape minus the
    group fields (``check`` / ``expected`` / ``got`` / ``message``) — this is
    a rule-level condition, not a per-group one.
    """
    if not config.get("assume_current_date"):
        return None
    if not config.get("demote_stale_dateless", True):
        return None
    if secondary_stream_count <= 0 or snapshot_covered_count > 0:
        return None
    logger.warning(
        "[EVENT-SYNC] staleness rail inert: assume_current_date + "
        "demote_stale_dateless are ON but no previous-day M3U snapshot "
        "covers any of the %d secondary stream(s) — the stale-dateless "
        "guard fails open for this rule",
        secondary_stream_count,
    )
    return {
        "check": CHECK_STALENESS_RAIL_SNAPSHOTS,
        "expected": (
            "a previous-day M3U snapshot covering at least one secondary "
            "stream's provider + group"
        ),
        "got": (
            f"no snapshot coverage for any of {secondary_stream_count} "
            f"secondary stream(s)"
        ),
        "message": (
            "The stale-dateless guard is enabled (assume current date + "
            "demote stale dateless) but currently INERT: no previous-day "
            "M3U snapshot covers this rule's secondary streams, so no "
            "dateless name can be recognized as yesterday's leftover and "
            "every match is treated as fresh (the guard fails open). "
            "Snapshots are captured by the M3U change monitor (~2x daily) "
            "for enabled groups only — a young deployment, a disabled "
            "change monitor, or groups not enabled for capture all leave "
            "the guard without data. It starts protecting automatically "
            "once a qualifying snapshot exists."
        ),
    }


async def check_event_sync_group_settings(
    client, config: dict, all_settings: dict | None = None,
    other_master_rules: dict[int, str] | None = None,
) -> dict:
    """Pre-flight an event_sync config against live Dispatcharr group settings.

    Args:
        client: Dispatcharr API client. Only
            ``get_all_m3u_group_settings()`` is called — this helper is
            read-only by contract (see module docstring).
        config: A validated event_sync_config dict (``master_group_id``,
            ``secondary_group_ids``).
        all_settings: Optional pre-fetched
            ``get_all_m3u_group_settings()`` result — passed by callers that
            also resolve the override relationship (preview/run) to avoid a
            second fetch. Fetched here when omitted.
        other_master_rules: Optional master_group_id -> rule-name map for
            OTHER enabled event_sync rules (see
            :func:`build_event_sync_master_rule_lookup`, bead yjchp). When a
            failing secondary group is another rule's MASTER, the
            ``secondary_auto_sync_off`` message is tailored — advising the
            stock "disable auto_channel_sync" there would break that other
            rule (masters require auto-sync ON). The machine-readable
            ``check`` id is unchanged; the failure gains a
            ``conflicting_rule`` field. ``None`` (the default) keeps every
            message byte-identical to prior behavior.

    Returns:
        ``{"ok": bool, "failures": [ ... ], "warnings": []}`` where each
        failure carries ``group_id`` / ``role`` / ``check`` / ``expected`` /
        ``got`` / ``message``. ``ok`` is True only when every check passed. A
        group absent from every account's settings fails
        ``group_settings_found`` — scoping to a group no provider carries
        is a stale config worth surfacing, not a silent no-op.
        ``warnings`` (bead 2ey2y) is always an EMPTY list here — advisory
        rule-level entries (e.g. :func:`build_staleness_rail_warning`) are
        appended by the preview/bundle callers AFTER the stream fetch, since
        they need data this settings-only check does not have. Warnings
        never flip ``ok``.
    """
    # bead jiscc: read the provider-scoped nested shape (P1 keeps it in sync
    # with the flat keys; fall back to whole-group scopes for a bare config).
    master_scope = config.get("master") or {
        "group_id": config.get("master_group_id"), "m3u_account_id": None,
    }
    secondary_scopes = config.get("secondary") or [
        {"group_id": g, "m3u_account_id": None}
        for g in (config.get("secondary_group_ids") or [])
    ]

    if all_settings is None:
        all_settings = await client.get_all_m3u_group_settings()
    target_to_source, _source_to_target = _build_override_maps(all_settings)

    # The non-collapsed per-(provider, group) view is only needed when a scope
    # is provider-specific — on a shared group the collapsed view (which
    # prefers auto_channel_sync ON) would misreport the other provider's row.
    needs_provider = (
        master_scope.get("m3u_account_id") is not None
        or any(s.get("m3u_account_id") is not None for s in secondary_scopes)
    )
    by_provider = (
        await client.get_m3u_group_settings_by_provider()
        if needs_provider else {}
    )

    def _scope_setting(scope: dict) -> dict | None:
        """Effective junction setting for a scope, or None if not found.

        Provider-scoped -> the specific (provider, group) row. Whole-group
        (provider None) -> the collapsed setting, resolving a Channel Group
        Override target through its source (bead override).
        """
        gid = scope.get("group_id")
        pid = scope.get("m3u_account_id")
        if pid is not None:
            return by_provider.get((pid, gid))
        setting = all_settings.get(gid)
        if setting is None:
            setting = target_to_source.get(gid)
        return setting

    def _scope_label(scope: dict) -> str:
        pid = scope.get("m3u_account_id")
        base = f"group {scope.get('group_id')}"
        return f"{base} (provider {pid})" if pid is not None else base

    failures: list[dict] = []

    # --- Master ---------------------------------------------------------
    master_settings = _scope_setting(master_scope)
    if master_settings is None:
        failures.append(_failure(
            group_id=master_scope.get("group_id"),
            role="master",
            check=CHECK_GROUP_SETTINGS_FOUND,
            expected="group present in an M3U account's group settings",
            got="no settings for this (group, provider) on any account",
            message=(
                f"Master {_scope_label(master_scope)} was not found in the "
                f"M3U account's group settings — the group may have been "
                f"removed or renamed, or that provider does not carry it."
            ),
        ))
    elif not master_settings.get("auto_channel_sync"):
        failures.append(_failure(
            group_id=master_scope.get("group_id"),
            role="master",
            check=CHECK_MASTER_AUTO_SYNC_ON,
            expected="auto_channel_sync ON",
            got="auto_channel_sync OFF",
            message=(
                f"Master {_scope_label(master_scope)} has auto_channel_sync "
                f"OFF in Dispatcharr — Dispatcharr creates no master channels, "
                f"so event_sync has nothing to attach to. Enable "
                f"auto_channel_sync for it in Dispatcharr (ECM never toggles "
                f"it for you)."
            ),
        ))

    # --- Secondaries ----------------------------------------------------
    for scope in secondary_scopes:
        settings = _scope_setting(scope)
        if settings is None:
            failures.append(_failure(
                group_id=scope.get("group_id"),
                role="secondary",
                check=CHECK_GROUP_SETTINGS_FOUND,
                expected="group present in an M3U account's group settings",
                got="no settings for this (group, provider) on any account",
                message=(
                    f"Secondary {_scope_label(scope)} was not found in the "
                    f"M3U account's group settings — the group may have been "
                    f"removed or renamed, or that provider does not carry it."
                ),
            ))
        elif settings.get("auto_channel_sync"):
            conflicting_rule = (other_master_rules or {}).get(
                scope.get("group_id")
            )
            if conflicting_rule is not None:
                # Cross-rule conflict (bead yjchp): this group is another
                # enabled event_sync rule's MASTER — masters REQUIRE
                # auto_channel_sync ON, so the stock "disable it" advice
                # would break that rule. Advise restructuring instead.
                message = (
                    f"Secondary {_scope_label(scope)} has auto_channel_sync "
                    f"ON in Dispatcharr because it is the MASTER group of "
                    f"event_sync rule '{conflicting_rule}' (masters require "
                    f"auto_channel_sync ON). Do NOT disable auto_channel_sync "
                    f"— that would break '{conflicting_rule}'. Instead, "
                    f"remove this group from this rule's secondary groups, "
                    f"or point rule '{conflicting_rule}' at a different "
                    f"master group."
                )
            else:
                message = (
                    f"Secondary {_scope_label(scope)} has auto_channel_sync "
                    f"ON in Dispatcharr — Dispatcharr is creating its own "
                    f"channels from it, which duplicates the master channels "
                    f"event_sync attaches to. Disable auto_channel_sync for "
                    f"it in Dispatcharr (ECM never toggles it for you)."
                )
            failures.append(_failure(
                group_id=scope.get("group_id"),
                role="secondary",
                check=CHECK_SECONDARY_AUTO_SYNC_OFF,
                expected="auto_channel_sync OFF",
                got="auto_channel_sync ON",
                message=message,
                conflicting_rule=conflicting_rule,
            ))

    if failures:
        logger.warning(
            "[EVENT-SYNC] Pre-flight failed %s check(s) for master=%s "
            "secondaries=%s: %s",
            len(failures), _scope_label(master_scope),
            [_scope_label(s) for s in secondary_scopes],
            [(f["group_id"], f["check"]) for f in failures],
        )
    return {"ok": not failures, "failures": failures, "warnings": []}
