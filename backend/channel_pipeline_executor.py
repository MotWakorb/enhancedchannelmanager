"""
Auto-Creation Action Executor Service

Executes actions defined in auto-creation rules, such as creating channels,
groups, merging streams, and assigning properties. Tracks all changes for
potential rollback.
"""
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
import re

import safe_regex
import journal
from epg_matching import detect_region
from match_fold import fold_match_key
from channel_pipeline_schema import Action, ActionType, TemplateVariables

# Alias for use inside methods whose ``fold_match_key`` BOOL parameter (the
# per-rule flag, kept name-identical to the schema field) shadows the helper.
_fold_key = fold_match_key
from channel_pipeline_evaluator import StreamContext
from services.dedup_matcher import (
    NameCleanMode,
    is_admissible,
    score_one,
)


logger = logging.getLogger(__name__)


@dataclass
class ProfileMembershipResult:
    """Outcome of an exclusive channel-profile reconciliation.

    ``enabled_count`` / ``disabled_count`` count the SUCCEEDED FLIPS — profiles
    whose enabled-state actually CHANGED (enabled a profile that was off, or
    disabled one that was on) and whose ``update_profile_channel`` PATCH returned
    without raising. Profiles already in the desired state are NEITHER PATCHed
    NOR counted, so an idempotent reconcile reports ``enabled_count ==
    disabled_count == 0`` and the caller treats it as a no-op (``modified=False``,
    no ``channels_updated`` inflation — y3m6o.1 review follow-up).

    ``failed_profile_ids`` carries every profile id whose NEEDED flip raised. A
    profile that did not need changing is never PATCHed and therefore can never
    appear here. A NON-EMPTY ``failed_profile_ids`` means exclusive membership
    was NOT fully achieved, so the caller must not report plain success
    (GH #720 / y3m6o.1) — the channel may be left in a profile it should not be
    in, which is the exact invariant #720 protects.
    """
    enabled_count: int
    disabled_count: int
    failed_profile_ids: list[int] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when at least one profile's enabled-state actually flipped
        (succeeded). Drives the caller's ``modified`` flag."""
        return bool(self.enabled_count or self.disabled_count)


@dataclass
class ActionResult:
    """Result of executing a single action."""
    success: bool
    action_type: str
    description: str
    entity_type: Optional[str] = None  # channel, group, stream
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    created: bool = False  # True if new entity was created
    modified: bool = False  # True if existing entity was modified
    skipped: bool = False  # True if action was skipped (e.g., channel exists)
    previous_state: Optional[dict] = None  # For rollback
    error: Optional[str] = None
    details: list[str] = field(default_factory=list)  # Additional context (normalization, group, etc.)
    deferred: bool = False  # True if action will be retried after EPG refresh
    # y3m6o.1 Finding 6 (0152): whether this modification can be reversed from
    # its recorded ``previous_state``. assign_channel_profile changes channel
    # *profile membership* — a mutation the ActionResult carries NO
    # previous_state for and that neither the legacy per-run rollback (restores
    # channel fields) nor the pre-run snapshot (captures channel<->stream state)
    # can reverse. Such results set this False so add_result does NOT record a
    # misleading modified/rollback entity claiming the change is reversible.
    rollbackable: bool = True


@dataclass
class ExecutionContext:
    """
    Context for action execution, accumulates results and tracks state.
    """
    # Execution mode
    dry_run: bool = False

    # Results tracking
    results: list[ActionResult] = field(default_factory=list)

    # Created/modified entities for rollback
    created_entities: list[dict] = field(default_factory=list)
    modified_entities: list[dict] = field(default_factory=list)

    # Statistics
    channels_created: int = 0
    channels_updated: int = 0
    groups_created: int = 0
    streams_merged: int = 0
    streams_skipped: int = 0
    streams_removed: int = 0

    # Distinct channel IDs that received at least one *merge* (a stream added,
    # or would-be-added in dry-run) during THIS stream's action execution.
    # Populated in add_result() — the SAME chokepoint that counts streams_merged
    # — so no merge return point can be missed (merge_streams action AND
    # create_channel if_exists=merge AND any future merge path all funnel through
    # add_result). The engine unions these per-stream sets across the run to
    # derive channels_touched (bd-0emgo.4: a live dry-run reported
    # streams_merged=26 but channels_touched=0 when the count was derived from a
    # scattered call-site dict instead of this chokepoint).
    merged_channel_ids: set = field(default_factory=set)

    # Current state (updated during execution)
    current_channel_id: Optional[int] = None  # Channel created/selected for this stream
    current_group_id: Optional[int] = None  # Group created/selected

    # Channel IDs actually CREATED during this stream's action execution
    # (not merged-into or matched via fallback). Used by Pass 3 renumber
    # gating to avoid renumbering foreign channels the rule doesn't own.
    # See bd-yj5yi (GH-104) — PR #107 regression fix.
    created_channel_ids: set = field(default_factory=set)

    # Custom variables set by set_variable actions
    custom_variables: dict = field(default_factory=dict)

    # Stream IDs queued for post-pipeline probing
    probe_stream_ids: list[int] = field(default_factory=list)

    # enhancedchannelmanager-vy4fl: group_id -> sort_group params, queued by
    # the sort_group action for the post-run Pass 3.6 (see
    # channel_pipeline_engine.py _sort_channel_groups). A dict (not a list)
    # so the engine's per-stream aggregation (results["sort_group_requests"]
    # .update(...)) naturally dedupes to ONE entry per group regardless of
    # how many matched streams in this run resolved to that group.
    sort_group_requests: dict[int, dict] = field(default_factory=dict)

    # y3m6o.1 review (Finding 3): channel ids whose profile membership was
    # mutated NON-reversibly this run — a modified result flagged
    # rollbackable=False (assign_channel_profile carries no reversible
    # previous_state). Populated at the add_result chokepoint so no
    # profile-write path can be missed; the engine folds these across the run
    # and persists a disclosure warning so rollback/undo never claims to restore
    # channel-profile membership it cannot.
    non_reversible_channel_ids: set[int] = field(default_factory=set)

    # GH #720 Part B review (Judgment 4b): channel ids where the profile
    # membership WAS applied but the pipeline-ownership marker write FAILED, so
    # precedence was not established (a group reconcile may move them until the
    # next pipeline run re-stamps). Folded by the engine into a run-level
    # WARNING (NOT a failed action — the assignment itself succeeded, so the run
    # stays completed, not completed_with_errors).
    profile_ownership_unestablished_channel_ids: set[int] = field(default_factory=set)

    # GH #720 Part B (Finding 4): group settings fetched ONCE per pipeline run
    # (cached here) so assign_channel_profile can resolve each channel's
    # EFFECTIVE group id and acquire the SAME reconcile lock without a per-channel
    # get_all_m3u_group_settings fetch. ``_group_settings_fetched`` guards the
    # one-shot fetch (a None cache after fetch means the fetch failed — skip the
    # lock, fall back to the re-check + marker-ordering defense-in-depth).
    group_settings_cache: Optional[dict] = None
    group_settings_fetched: bool = False

    # y3m6o.1 review (Finding 1 reversal): default-profile assignment failures.
    # Each entry {channel_id, failed_profile_ids} for a newly-created channel
    # whose configured default-profile membership could not be fully enforced.
    # Default assignment stays best-effort for the CREATE (never aborts it), but
    # the PO decision escalates the failure into the run-level aggregation via
    # the engine, so such a run finalizes completed_with_errors, not green.
    default_profile_failures: list[dict] = field(default_factory=list)

    # BD-F (bd-a5lb2): per-stream count of pending_merges rows
    # enqueued by the bulk-M3U dedup hook (ADR-008 §D1). One per
    # would-be channel creation deferred to operator review. The
    # engine aggregates this across all streams in the run and
    # surfaces it on the pipeline result as ``pending_merges_added``
    # so the M3U-refresh response can drive BD-J's toast.
    pending_merges_added: int = 0

    def add_result(self, result: ActionResult):
        """Add an action result and update statistics."""
        self.results.append(result)

        if result.created:
            if result.entity_type == "channel":
                self.channels_created += 1
                self.created_entities.append({
                    "type": "channel",
                    "id": result.entity_id,
                    "name": result.entity_name
                })
            elif result.entity_type == "group":
                self.groups_created += 1
                self.created_entities.append({
                    "type": "group",
                    "id": result.entity_id,
                    "name": result.entity_name
                })

        if result.modified:
            if result.entity_type == "channel":
                # merge_stream results (action_type "merge_stream") count as
                # stream merges, not channel property updates.  All other
                # channel modifications (assign_logo, assign_tvg_id,
                # assign_epg, update_channel, set_channel_number, etc.) are
                # genuine property updates and increment channels_updated.
                if result.action_type == "merge_stream":
                    self.streams_merged += 1
                    # Record the touched channel at the SAME chokepoint that
                    # counts the merge, so channels_touched can never drift from
                    # streams_merged regardless of which path produced the merge
                    # (bd-0emgo.4). The set de-dupes channels merged into more
                    # than once; the engine unions these across streams.
                    if result.entity_id is not None:
                        self.merged_channel_ids.add(result.entity_id)
                else:
                    self.channels_updated += 1
            elif result.entity_type == "stream" and result.action_type == "remove_from_channel":
                self.streams_removed += 1
            # y3m6o.1 Finding 6 (0152): a non-rollbackable modification (e.g.
            # assign_channel_profile — profile membership with no reversible
            # previous_state) is still COUNTED as an update but is NOT recorded
            # as a rollback entity, so rollback/restore never claims to reverse
            # a change it cannot. The channel's OTHER actions (merges, field
            # updates) still record their own rollback entities independently.
            if result.rollbackable:
                self.modified_entities.append({
                    "type": result.entity_type,
                    "id": result.entity_id,
                    "name": result.entity_name,
                    "previous": result.previous_state
                })
            elif result.entity_type == "channel" and result.entity_id is not None:
                # y3m6o.1 review (Finding 3): a modified-but-non-rollbackable
                # channel change (assign_channel_profile — profile membership
                # with no reversible previous_state) is recorded HERE so the run
                # can DISCLOSE that rollback/undo will not restore it. Same
                # chokepoint that counts the update, so no profile-write path
                # (standard Pass 2 OR event_sync) can be missed.
                self.non_reversible_channel_ids.add(result.entity_id)

        if result.skipped:
            self.streams_skipped += 1


class ActionExecutor:
    """
    Executes actions against the Dispatcharr API.

    Usage:
        executor = ActionExecutor(dispatcharr_client)
        ctx = ExecutionContext()
        result = await executor.execute(action, stream_context, ctx)
    """

    def __init__(self, client, existing_channels: list = None, existing_groups: list = None,
                 normalization_engine=None, settings=None, all_profile_ids: list[int] | None = None,
                 epg_data: list = None, epg_sources: list = None,
                 triggered_by: str = "manual", execution_id: int | None = None,
                 channel_profile_membership: dict[int, set[int]] | None = None):
        """
        Initialize the executor.

        Args:
            client: Dispatcharr API client
            existing_channels: List of existing channels (for lookup/merge)
            existing_groups: List of existing groups (for lookup)
            normalization_engine: Optional NormalizationEngine for name normalization
            settings: DispatcharrSettings instance for channel naming/profile defaults
            all_profile_ids: The known channel-profile universe. A list (incl.
                ``[]`` = genuinely zero profiles configured) is treated as known;
                ``None`` (the default) is an explicit "universe unavailable /
                unknown" sentinel — assign_channel_profile fails on it rather
                than degrading to enable-only (GH #720 / y3m6o.1).
            epg_data: EPG data entries from Dispatcharr (for assign_epg resolution)
            epg_sources: EPG source dicts from Dispatcharr (for dummy EPG detection)
            triggered_by: The engine-side ``triggered_by`` string (e.g.
                "m3u_refresh", "scheduled", "manual"). Passed through to
                the BD-F bulk-M3U dedup hook (``services.m3u_dedup_hook``)
                so only the ``m3u_refresh`` path enqueues pending merges
                per ADR-008 §D1. Defaults to "manual" so non-engine
                callers (and existing tests that construct executors
                directly) keep their pre-BD-F behaviour.
            execution_id: The ``ChannelPipelineExecution.id`` for this run,
                threaded from the engine. Used as the journal ``batch_id``
                so an operator can list every ``(channel_id, stream_id)``
                pair a run touched via ``get_journal(batch_id=...)`` and
                recover from a bad merge (bd-0emgo.5). ``None`` (the default
                for direct-construct callers/tests) disables the per-merge
                journal write — entries without an execution_id would be
                unrecoverable noise.
            channel_profile_membership: Optional ``channel_id -> set(profile_ids
                enabled)`` map for channels at run start, built by the engine from
                the same ``get_channel_profiles()`` fetch (zero extra reads). Lets
                ``assign_channel_profile`` PATCH only the profiles whose state
                actually flips, so an idempotent reconcile does not inflate
                ``channels_updated``. ``None``/absent falls back to no-diff for
                unknown channels (write-all), preserving prior behavior for
                direct-construct callers/tests.
        """
        self.client = client
        self.existing_channels = existing_channels or []
        self.existing_groups = existing_groups or []
        self._normalization_engine = normalization_engine
        self._settings = settings
        # ``all_profile_ids`` carries a THREE-way availability signal, kept
        # DISTINCT (do NOT coerce ``None`` -> ``[]`` here):
        #   * a list (incl. ``[]``) -> the KNOWN profile universe; ``[]`` means
        #     genuinely zero channel profiles are configured (a real fact).
        #   * ``None`` -> the universe is UNAVAILABLE / unknown: the engine's
        #     ``get_channel_profiles()`` fetch raised, or a direct-construct
        #     caller never supplied it. Exclusive membership CANNOT be proven.
        # ``assign_channel_profile`` fails when the universe is unavailable
        # rather than silently degrading to enable-only-and-report-success
        # (GH #720 / y3m6o.1). Default-profile assignment treats both ``None``
        # and ``[]`` as a benign no-op via its own falsy guard.
        self._all_profile_ids = all_profile_ids
        self._triggered_by = triggered_by
        self._execution_id = execution_id
        # y3m6o.1 review follow-up: current channel-profile membership at RUN
        # START — ``channel_id -> set(profile_ids the channel is ENABLED in)``,
        # built from the SAME ``get_channel_profiles()`` fetch the engine already
        # makes for the universe (the profile payload's ``channels`` list is the
        # enabled member set), so it costs ZERO extra API reads. Used by
        # ``_apply_exclusive_profile_membership`` to PATCH only the profiles whose
        # enabled-state actually flips, so an idempotent no-op reconcile performs
        # zero writes and is not counted as a channel update.
        #
        # ``None`` (the default) is a distinct "no membership info" sentinel:
        # direct-construct callers/tests that never supply it keep the proven
        # write-all behavior (every profile PATCHed to its desired state). The
        # engine ALWAYS supplies a map (even ``{}``), so the diff optimization is
        # always active in production. When present: absent channels that EXISTED
        # at run start are enabled in zero profiles; channels CREATED this run are
        # auto-joined by Dispatcharr to ALL universe profiles (verified live) —
        # see ``_current_profile_membership``. The map is mutated in-run after
        # each reconcile so a second reconcile of the same channel sees fresh
        # state.
        self._channel_profile_membership: dict[int, set[int]] | None = (
            {cid: set(pids) for cid, pids in channel_profile_membership.items()}
            if channel_profile_membership is not None
            else None
        )
        # Channel ids that EXISTED at run start — the discriminator between "an
        # existing channel enabled in zero profiles" (current = ∅) and "a channel
        # created this run" (current = the full auto-joined universe).
        self._run_start_channel_ids: set[int] = {
            c["id"] for c in self.existing_channels if isinstance(c, dict) and "id" in c
        }

        # Build EPG data lookup: epg_source_id -> list of data entries
        self._epg_data_by_source: dict[int, list[dict]] = {}
        for entry in (epg_data or []):
            src_id = entry.get("epg_source")
            if src_id is not None:
                self._epg_data_by_source.setdefault(src_id, []).append(entry)

        # Identify dummy EPG sources (URL contains /api/dummy-epg/xmltv).
        # ti939.3.3 additionally needs the REVERSE of Pass 5's source→profile
        # URL mapping: profile id → per-profile source id (URL path
        # /api/dummy-epg/xmltv/<profile_id>), plus the combined all-profiles
        # sources (bare /api/dummy-epg/xmltv) as a fallback. Deterministic on
        # duplicates: the lowest source id wins.
        self._dummy_epg_source_ids: set[int] = set()
        self._dummy_source_by_profile: dict[int, int] = {}
        self._combined_dummy_source_ids: list[int] = []
        for src in sorted((epg_sources or []), key=lambda s: s.get("id", 0)):
            url = src.get("url") or ""
            if "/api/dummy-epg/xmltv" not in url:
                continue
            self._dummy_epg_source_ids.add(src["id"])
            m = re.search(r"/api/dummy-epg/xmltv/(\d+)", url)
            if m:
                self._dummy_source_by_profile.setdefault(
                    int(m.group(1)), src["id"]
                )
            else:
                self._combined_dummy_source_ids.append(src["id"])

        # Deferred EPG assignments (populated when dummy source has no data yet)
        self._deferred_epg_assignments: list[tuple] = []  # (channel_id, action, stream_ctx, exec_ctx)

        # Pending EPG verifications for newly created channels (channel_id, payload)
        self._pending_epg_verifications: list[tuple[int, dict]] = []

        # Build lookup indices
        self._channel_by_id = {c["id"]: c for c in self.existing_channels}
        self._channel_by_name = {c["name"].lower(): c for c in self.existing_channels}
        self._group_by_id = {g["id"]: g for g in self.existing_groups}
        self._group_by_name = {g["name"].lower(): g for g in self.existing_groups}

        # bead g0uuf: multi-candidate companions to the single-slot lookup
        # maps. Same channel name in N groups is a SUPPORTED layout (GH-92
        # group-scoped rules), but each legacy map keeps exactly one channel
        # per key (last-wins dict comprehension above, first-seen setdefault
        # below) — so a scoped _find_channel_by_name could only ever test one
        # arbitrary survivor and reported same-named in-scope channels as
        # "not found" (merge_only skipped; merge created a duplicate). The
        # candidate lists preserve EVERY channel per key in load order;
        # _find_channel_by_name scans them only after the legacy pick fails
        # its scope/manual gates, so behavior is unchanged whenever the
        # legacy pick was valid.
        self._by_name_candidates: dict[str, list[dict]] = {}
        self._base_name_candidates: dict[str, list[dict]] = {}
        self._normalized_name_candidates: dict[str, list[dict]] = {}
        self._core_name_candidates: dict[str, list[dict]] = {}
        self._fold_key_candidates: dict[str, list[dict]] = {}
        for c in self.existing_channels:
            self._add_candidate(self._by_name_candidates, c["name"].lower(), c)

        # Track newly created entities during this execution
        self._created_channels = {}  # name.lower() -> channel dict
        self._base_name_to_channel = {}  # base_name.lower() -> channel dict (for number-prefixed lookups)
        self._created_groups = {}  # name.lower() -> group dict
        self._next_dry_run_id = -1  # Unique negative IDs for dry-run simulated entities

        # Track streams per (channel_id, m3u_account_id) for max_streams_per_channel limit.
        # Lazily seeded per-channel via _ensure_channel_m3u_counts() because the
        # paginated channels API only returns stream IDs (ints), not full dicts.
        self._channel_m3u_counts: dict[tuple[int, int], int] = {}
        self._seeded_channels: set[int] = set()

        # merge_streams reconciliation: for actions with remove_non_matching=True,
        # we accumulate the desired stream IDs per channel and prune later.
        # NOTE: this dict is for PRUNE accounting only — do NOT derive
        # channels_touched from it. It is populated at scattered call sites
        # (_execute_merge_streams, _execute_create_channel if_exists=merge) and
        # historically missed merge paths, producing channels_touched=0 while
        # streams_merged>0 (bd-0emgo.4). channels_touched is now derived in the
        # engine by unioning exec_ctx.merged_channel_ids, populated at the
        # add_result chokepoint — see ExecutionContext.merged_channel_ids.
        self._merge_streams_added_by_channel: dict[int, set[int]] = {}
        self._merge_prune_enabled_channels: set[int] = set()

        # Pre-populate base-name mapping for existing channels with "NUMBER | " prefixes
        _num_prefix = re.compile(r'^\d+\s*\|\s*')
        for c in self.existing_channels:
            stripped = _num_prefix.sub('', c["name"])
            if stripped != c["name"]:
                self._base_name_to_channel.setdefault(stripped.lower(), c)
                self._add_candidate(self._base_name_candidates, stripped.lower(), c)

        # Folded-key mapping (GH #645 / bead 0vao3): canonical comparison key
        # (casefold + strip ALL whitespace, via the shared match_fold helper)
        # -> channel, for both the raw stored name and its number-prefix-
        # stripped base name. Consulted by _find_channel_by_name ONLY when the
        # firing rule opted in via its fold_match_key flag; built
        # unconditionally because one executor serves every rule in a run.
        # First-seen wins on key collisions, matching the other lookup maps.
        self._fold_key_to_channel: dict[str, dict] = {}
        for c in self.existing_channels:
            stripped = _num_prefix.sub('', c["name"])
            self._fold_key_to_channel.setdefault(fold_match_key(c["name"]), c)
            self._add_candidate(self._fold_key_candidates, fold_match_key(c["name"]), c)
            if stripped != c["name"]:
                self._fold_key_to_channel.setdefault(fold_match_key(stripped), c)
                self._add_candidate(self._fold_key_candidates, fold_match_key(stripped), c)

        # Pre-populate normalized-name mapping so merge_streams auto-lookup
        # can find channels the same way normalized_name_in_group does
        self._normalized_name_to_channel: dict[str, dict] = {}
        if self._normalization_engine:
            for c in self.existing_channels:
                stripped = _num_prefix.sub('', c["name"])
                try:
                    result = self._normalization_engine.normalize(stripped)
                    if result.normalized and result.normalized.lower() != stripped.lower():
                        self._normalized_name_to_channel.setdefault(
                            result.normalized.lower(), c
                        )
                        self._add_candidate(
                            self._normalized_name_candidates,
                            result.normalized.lower(), c)
                except Exception as e:
                    logger.warning("[AUTO-CREATE-EXEC] Normalization failed for channel '%s': %s", c.get("name", ""), e)

        # Pre-populate core-name mapping so merge_streams can fall back
        # to tag-group-based stripping (country prefix + quality suffix)
        # even when normalization rules are disabled.
        self._core_name_to_channel: dict[str, dict] = {}
        if self._normalization_engine:
            for c in self.existing_channels:
                stripped = _num_prefix.sub('', c["name"])
                try:
                    core = self._normalization_engine.extract_core_name(stripped)
                    if core:
                        self._core_name_to_channel.setdefault(core.lower(), c)
                        self._add_candidate(self._core_name_candidates, core.lower(), c)
                except Exception as e:
                    logger.warning("[AUTO-CREATE-EXEC] Core name extraction failed for channel '%s': %s", c.get("name", ""), e)

        # Index deparenthesized variants of core names so that
        # "Bravo (East)" also matches channel "Bravo East" and vice versa.
        for core_key, ch_val in list(self._core_name_to_channel.items()):
            deparen = re.sub(r'\(([^)]+)\)', r'\1', core_key)
            deparen = re.sub(r'\s+', ' ', deparen).strip()
            if deparen != core_key:
                self._core_name_to_channel.setdefault(deparen, ch_val)
        for core_key, cands in list(self._core_name_candidates.items()):
            deparen = re.sub(r'\(([^)]+)\)', r'\1', core_key)
            deparen = re.sub(r'\s+', ' ', deparen).strip()
            if deparen != core_key:
                for ch_val in cands:
                    self._add_candidate(self._core_name_candidates, deparen, ch_val)

        # Pre-populate call-sign mapping so merge_streams can match
        # local affiliates by FCC call sign (W/K + 2-3 letters).
        self._callsign_to_channel: dict[str, dict] = {}
        if self._normalization_engine:
            for c in self.existing_channels:
                try:
                    cs = self._normalization_engine.extract_call_sign(c["name"])
                    if cs:
                        self._callsign_to_channel.setdefault(cs, c)
                except Exception as e:
                    logger.warning("[AUTO-CREATE-EXEC] Call sign extraction failed for channel '%s': %s", c.get("name", ""), e)

        self._logo_cache = {}  # logo_url -> logo_id

        # Buffer per-merge journal entries so long runs do not commit one row
        # at a time. This keeps the audit trail but reduces WAL churn.
        self._journal_buffer: list[dict] = []
        self._journal_flush_threshold = 100

        # enhancedchannelmanager-wy6l5: manual-channel merge-block visibility.
        # _last_manual_block is set by _find_channel_by_name whenever the
        # block_manual gate rejected an otherwise-matching candidate (reset at
        # each lookup); callers read it to surface a user-visible "merge
        # blocked" reason instead of the old INFO-log-only outcome.
        # _journaled_manual_block_ids dedupes the journal to ONE
        # manual_channel_merge_blocked entry per blocked channel per run —
        # per-stream reasons live in the execution log's action descriptions.
        self._last_manual_block: Optional[dict] = None
        self._journaled_manual_block_ids: set[int] = set()

        # enhancedchannelmanager-3gigl: name-transform failure visibility.
        # _last_name_transform_error is set by _apply_name_transform whenever
        # safe_regex.sub reported a failure (invalid group reference in the
        # replacement, timeout, oversize pattern) — reset at each call; the
        # create_channel / create_group callers read it to append a
        # user-visible reason to the execution log's action details.
        # _journaled_transform_failure_keys dedupes the journal to ONE
        # name_transform_failed entry per (pattern, replacement, kind) per
        # run — effectively per rule per run, since the transform fields
        # live on the rule's action.
        self._last_name_transform_error: Optional[str] = None
        self._journaled_transform_failure_keys: set[tuple] = set()

        # Channel number tracking
        self._used_channel_numbers = set()
        for c in self.existing_channels:
            if c.get("channel_number"):
                self._used_channel_numbers.add(c["channel_number"])
        self._channel_assigned_numbers = {}  # channel_id -> number (set_channel_number dedup)

    def _flush_journal_buffer(self) -> None:
        """Flush buffered journal entries in one transaction."""
        if not self._journal_buffer:
            return

        pending = list(self._journal_buffer)
        if journal.log_entries(entries=pending):
            logger.debug(
                "[AUTO-CREATE-EXEC] Flushed %s buffered journal entries",
                len(pending),
            )
            self._journal_buffer.clear()
        else:
            logger.warning(
                "[AUTO-CREATE-EXEC] Failed to flush %s buffered journal entries",
                len(pending),
            )

    def _journal_manual_channel_adoption(
        self, channel: dict, stream_ctx: StreamContext, action_type: str
    ) -> None:
        """Record that an opt-in rule adopted a hand-built MANUAL channel.

        enhancedchannelmanager-orzck (W1): when ``allow_manual_channel_merge``
        is True and the resolved merge/update/rename target is a manual channel
        (``auto_created`` missing/falsy), write an audit entry so an operator can
        see exactly which manual channels an auto-creation run touched. Reuses
        the buffered journal the executor already flushes in batches. A None
        ``_execution_id`` (direct-construct callers/tests) disables journaling,
        same as the per-merge writer.
        """
        if self._execution_id is None:
            return
        self._journal_buffer.append({
            "category": "auto_creation",
            "action_type": "manual_channel_adopted",
            "entity_id": channel.get("id"),
            "entity_name": channel.get("name"),
            "description": (
                "Auto-creation adopted MANUAL channel '%s' (id=%s) as merge "
                "target for stream '%s' (allow_manual_channel_merge=True)"
                % (channel.get("name"), channel.get("id"), stream_ctx.stream_name)
            ),
            "before_value": {
                "auto_created": False,
                "action_type": action_type,
            },
            "after_value": {"stream_id": stream_ctx.stream_id},
            "user_initiated": False,
            "mutation_source": journal.MUTATION_SOURCE_AUTO_CREATION,
            "batch_id": str(self._execution_id),
        })
        if len(self._journal_buffer) >= self._journal_flush_threshold:
            self._flush_journal_buffer()

    def _journal_manual_channel_block(
        self, channel: dict, stream_ctx: StreamContext, action_type: str
    ) -> None:
        """Record that the manual-channel gate BLOCKED a resolved merge target.

        enhancedchannelmanager-wy6l5: when ``allow_manual_channel_merge`` is
        off (the default) and the resolved merge/update target is a hand-built
        MANUAL channel, the executor treats it as "not found" — and on the
        create_channel path then creates a NEW auto channel instead. That
        outcome used to be INFO-log-only, so rules looked like they were
        "skipping everything" for no reason. Mirror the opt-in path's
        ``manual_channel_adopted`` entry with a ``manual_channel_merge_blocked``
        one so the journal shows WHY. Deduped to one entry per blocked channel
        per run (``_journaled_manual_block_ids``) — the per-stream reasons ride
        in the execution log's action descriptions. A None ``_execution_id``
        (direct-construct callers/tests) disables journaling, same as the
        adoption writer.
        """
        if self._execution_id is None:
            return
        channel_id = channel.get("id")
        if channel_id in self._journaled_manual_block_ids:
            return
        self._journaled_manual_block_ids.add(channel_id)
        self._journal_buffer.append({
            "category": "auto_creation",
            "action_type": "manual_channel_merge_blocked",
            "entity_id": channel_id,
            "entity_name": channel.get("name"),
            "description": (
                "Auto-creation matched MANUAL channel '%s' (id=%s) as merge "
                "target for stream '%s' but allow_manual_channel_merge is off "
                "— treated as not found (the rule may create a new auto "
                "channel instead; enable allow_manual_channel_merge on the "
                "rule to merge into hand-built channels)"
                % (channel.get("name"), channel_id, stream_ctx.stream_name)
            ),
            "before_value": {
                "auto_created": False,
                "action_type": action_type,
            },
            "after_value": {"stream_id": stream_ctx.stream_id, "blocked": True},
            "user_initiated": False,
            "mutation_source": journal.MUTATION_SOURCE_AUTO_CREATION,
            "batch_id": str(self._execution_id),
        })
        if len(self._journal_buffer) >= self._journal_flush_threshold:
            self._flush_journal_buffer()

    async def execute(self, action: Action | dict, stream_ctx: StreamContext,
                      exec_ctx: ExecutionContext, rule_target_group_id: int = None,
                      normalization_group_ids: list[int] = None,
                      match_scope_target_group: bool = True,
                      rule_scope_group_id: int = None,
                      allow_manual_channel_merge: bool = False,
                      fold_match_key: bool = False,
                      rule_id: int = None) -> ActionResult:
        """
        Execute a single action.

        Args:
            action: Action to execute
            stream_ctx: Stream context with stream data
            exec_ctx: Execution context for tracking results
            rule_target_group_id: Default target group from rule
            rule_id: ID of the firing rule, threaded into scored-fuzzy merge
                provenance for the journal (M7, jnzst FIX 4). None outside a
                rule run.
            normalization_group_ids: List of normalization rule group IDs to apply (empty/None = disabled)
            match_scope_target_group: When True, restrict existing-channel name
                lookups in create_channel to channels in the action's effective
                target group. This allows two rules targeting different groups
                to create separate channels with the same name instead of
                merging into the first group's channel (GH-92, bd-r9mtd).
                Default False preserves pre-GH-92 global-lookup behavior.
            rule_scope_group_id: Explicit rule-level scope group (GH #298,
                bd-kncun). When set AND match_scope_target_group is True, it
                pins the group that name lookups are restricted to — in
                create_channel it takes precedence over the action's derived
                group_id, and in merge_streams it is the ONLY way to scope the
                match (merge_streams has no action group_id). None (default)
                preserves prior behavior: create_channel falls back to the
                derived group, merge_streams stays group-agnostic.
            allow_manual_channel_merge: When False (default —
                enhancedchannelmanager-orzck / W1), auto-creation will NOT adopt
                a hand-built MANUAL channel (``auto_created`` missing/falsy) as a
                merge/update/rename target on a name collision — the manual
                channel is treated as "not found" and a new auto channel is
                created instead. When True, the rule opts back into the legacy
                behavior and may adopt manual channels (the adoption is
                journaled). Threaded into the resolution chokepoint as
                ``block_manual = not allow_manual_channel_merge``.
            fold_match_key: When True (GH #645 / bead 0vao3 — opt-in per
                rule, default False), the create_channel ``if_exists`` merge
                lookup additionally compares names by the shared canonical
                fold key (casefold + strip ALL whitespace) so whitespace/case
                spelling variants merge into one channel instead of creating
                duplicates. Comparison key only — stored channel names are
                never altered. Threaded into ``_find_channel_by_name`` as
                ``fold_key``.

        Returns:
            ActionResult with execution details
        """
        if isinstance(action, dict):
            action = Action.from_dict(action)

        logger.debug(
            "[AUTO-CREATE-EXEC] Executing action type=%s for stream=%r "
            "(id=%s) dry_run=%s params=%s",
            action.type, stream_ctx.stream_name,
            stream_ctx.stream_id, exec_ctx.dry_run, action.params
        )

        try:
            action_type = ActionType(action.type)
        except ValueError:
            logger.debug("[AUTO-CREATE-EXEC] Unknown action type: %s", action.type)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Unknown action type: {action.type}",
                error=f"Unknown action type: {action.type}"
            )

        # Build template context for variable expansion. Pass the rule's
        # normalization groups so {normalized_name} is consistent across every
        # action, not just create_channel (GH #466 / bd-6gvt8).
        template_ctx = self._build_template_context(
            stream_ctx, exec_ctx, normalization_group_ids=normalization_group_ids)

        # Execute based on action type
        if action_type == ActionType.CREATE_CHANNEL:
            result = await self._execute_create_channel(
                action, stream_ctx, exec_ctx, template_ctx, rule_target_group_id,
                normalization_group_ids=normalization_group_ids,
                match_scope_target_group=match_scope_target_group,
                rule_scope_group_id=rule_scope_group_id,
                allow_manual_channel_merge=allow_manual_channel_merge,
                fold_match_key=fold_match_key,
            )
        elif action_type == ActionType.CREATE_GROUP:
            result = await self._execute_create_group(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.MERGE_STREAMS:
            result = await self._execute_merge_streams(action, stream_ctx, exec_ctx, template_ctx,
                                                         normalization_group_ids=normalization_group_ids,
                                                         match_scope_target_group=match_scope_target_group,
                                                         rule_scope_group_id=rule_scope_group_id,
                                                         allow_manual_channel_merge=allow_manual_channel_merge,
                                                         rule_id=rule_id)
        elif action_type == ActionType.ASSIGN_LOGO:
            result = await self._execute_assign_logo(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.ASSIGN_TVG_ID:
            result = await self._execute_assign_tvg_id(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.ASSIGN_EPG:
            result = await self._execute_assign_epg(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.ASSIGN_PROFILE:
            result = await self._execute_assign_profile(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.ASSIGN_CHANNEL_PROFILE:
            result = await self._execute_assign_channel_profile(
                action, stream_ctx, exec_ctx, rule_id=rule_id
            )
        elif action_type == ActionType.SET_CHANNEL_NUMBER:
            result = await self._execute_set_channel_number(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_VARIABLE:
            result = await self._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.REMOVE_FROM_CHANNEL:
            result = await self._execute_remove_from_channel(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_STREAM_PRIORITY:
            result = await self._execute_set_stream_priority(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.PROBE_STREAMS:
            exec_ctx.probe_stream_ids.append(stream_ctx.stream_id)
            result = ActionResult(
                success=True,
                action_type=action.type,
                description="Stream queued for probing after pipeline"
            )
        elif action_type == ActionType.SORT_GROUP:
            result = self._execute_sort_group(action, exec_ctx, rule_target_group_id)
        elif action_type == ActionType.SKIP:
            result = ActionResult(
                success=True,
                action_type=action.type,
                description="Stream skipped by rule",
                skipped=True
            )
        elif action_type == ActionType.STOP_PROCESSING:
            result = ActionResult(
                success=True,
                action_type=action.type,
                description="Stop processing further rules"
            )
        elif action_type == ActionType.LOG_MATCH:
            message = action.params.get("message", "Stream matched rule")
            expanded = TemplateVariables.expand_template(message, template_ctx, exec_ctx.custom_variables)
            logger.info("[AUTO-CREATE-EXEC] %s", expanded)
            result = ActionResult(
                success=True,
                action_type=action.type,
                description=expanded
            )
        else:
            result = ActionResult(
                success=False,
                action_type=action.type,
                description=f"Unhandled action type: {action.type}",
                error=f"Unhandled action type"
            )

        logger.debug(
            "[AUTO-CREATE-EXEC] Result: type=%s success=%s "
            "created=%s modified=%s skipped=%s "
            "desc=%r",
            result.action_type, result.success,
            result.created, result.modified, result.skipped,
            result.description
        )
        exec_ctx.add_result(result)
        return result

    def _build_template_context(self, stream_ctx: StreamContext, exec_ctx: ExecutionContext = None,
                                normalization_group_ids: list[int] = None) -> dict:
        """Build template variable context from stream context.

        ``{normalized_name}`` is the stream name after the firing rule's
        normalization groups are applied — the documented "Name after
        normalization rules" contract. It is computed here, at the single
        per-action chokepoint, so EVERY action that expands ``{normalized_name}``
        (Assign TVG-ID, Set Variable, Assign Logo, …) gets the same value the
        create_channel action does (GH #466 / bd-6gvt8). Previously the variable
        resolved to ``stream_ctx.normalized_name`` — never populated during a run
        — so it fell back to the raw stream name everywhere except create_channel,
        which alone re-normalized its expanded name afterward.

        When the rule has no normalization groups (or no engine), it falls back
        to the raw stream name — unchanged from prior behavior, so the only rules
        affected are those that actually opted into normalization.
        """
        quality_str = None
        if stream_ctx.resolution_height:
            if stream_ctx.resolution_height >= 2160:
                quality_str = "4K"
            elif stream_ctx.resolution_height >= 1080:
                quality_str = "1080p"
            elif stream_ctx.resolution_height >= 720:
                quality_str = "720p"
            elif stream_ctx.resolution_height >= 480:
                quality_str = "480p"
            else:
                quality_str = f"{stream_ctx.resolution_height}p"

        # Resolve {normalized_name} = stream name after the rule's normalization
        # groups. Prefer a normalized_name already stamped on the context; else
        # compute it from the rule's groups; else fall back to the raw name.
        normalized_name = stream_ctx.normalized_name
        if not normalized_name and normalization_group_ids and self._normalization_engine:
            try:
                norm_result = self._normalization_engine.normalize(
                    stream_ctx.stream_name, group_ids=normalization_group_ids)
                normalized_name = norm_result.normalized
            except Exception as e:
                logger.warning(
                    "[AUTO-CREATE-EXEC] Failed to normalize {normalized_name} for '%s': %s",
                    stream_ctx.stream_name, e)
        normalized_name = normalized_name or stream_ctx.stream_name

        ctx = {
            TemplateVariables.STREAM_NAME: stream_ctx.stream_name,
            TemplateVariables.STREAM_GROUP: stream_ctx.group_name or "",
            TemplateVariables.TVG_ID: stream_ctx.tvg_id or "",
            TemplateVariables.TVG_NAME: stream_ctx.tvg_name or "",
            TemplateVariables.QUALITY: quality_str or "",
            TemplateVariables.QUALITY_RAW: stream_ctx.resolution_height or "",
            TemplateVariables.PROVIDER: stream_ctx.m3u_account_name or "",
            TemplateVariables.PROVIDER_ID: stream_ctx.m3u_account_id or "",
            TemplateVariables.NORMALIZED_NAME: normalized_name,
        }

        # Add custom variables with var: prefix
        if exec_ctx and exec_ctx.custom_variables:
            for var_name, value in exec_ctx.custom_variables.items():
                ctx[f"var:{var_name}"] = value

        logger.debug("[AUTO-CREATE-EXEC] Built context: %s", ctx)
        return ctx

    # =========================================================================
    # Channel Creation
    # =========================================================================

    def _apply_name_transform(self, name: str, params: dict) -> str:
        """Apply optional regex name transform to a name string.

        On failure (invalid group reference in the replacement, timeout,
        oversize pattern) the name passes through unchanged — the
        pre-migration no-op contract — and the failure reason is stamped on
        ``self._last_name_transform_error`` for the calling action to
        surface in the execution log (enhancedchannelmanager-3gigl).
        safe_regex.sub never raises: it swallows internally and reports
        through the ``on_error`` callback, so there is no exception arm
        here (enhancedchannelmanager-ock7c removed the unreachable
        ``except re.error``).
        """
        self._last_name_transform_error = None
        pattern = params.get("name_transform_pattern")
        if pattern:
            replacement = params.get("name_transform_replacement", "")
            # Convert JS-style backreferences ($1, $2) to Python (\1, \2).
            # Pattern here is a hardcoded literal, so we leave this on stdlib re.
            py_replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
            original = name
            failures: list[safe_regex.SubFailure] = []
            # bd-eio04.15: user-supplied pattern routed through safe_regex.
            # On failure safe_regex.sub returns 'name' unchanged — that
            # matches the pre-migration fallback contract (no-op on
            # regex error) and keeps the channel-name pipeline moving.
            name = safe_regex.sub(pattern, py_replacement, name,
                                  on_error=failures.append)
            if failures:
                failure = failures[0]
                hint = ""
                if failure.kind == "template":
                    hint = (" — check that every $N in the replacement "
                            "matches a capture group in the pattern")
                self._last_name_transform_error = (
                    f"Name transform failed ({failure.kind}): "
                    f"{failure.message}{hint}. Pattern /{pattern}/ with "
                    f"replacement '{replacement}' left '{original}' unchanged."
                )
                logger.warning("[AUTO-CREATE-EXEC] %s",
                               self._last_name_transform_error)
                self._journal_name_transform_failure(
                    pattern, replacement, failure)
            elif name != original:
                logger.debug("[AUTO-CREATE-EXEC] '%s' -> '%s' (pattern=/%s/ replacement='%s')", original, name, pattern, replacement)
        return name.strip()

    def _journal_name_transform_failure(
        self, pattern: str, replacement: str, failure: "safe_regex.SubFailure"
    ) -> None:
        """Record a name-transform regex failure in the journal.

        enhancedchannelmanager-3gigl: mirrors the wy6l5 merge-block writer —
        deduped to ONE ``name_transform_failed`` entry per (pattern,
        replacement, kind) per run (per-stream reasons ride in the execution
        log's action details); a None ``_execution_id`` (direct-construct
        callers/tests) disables journaling.
        """
        if self._execution_id is None:
            return
        key = (pattern, replacement, failure.kind)
        if key in self._journaled_transform_failure_keys:
            return
        self._journaled_transform_failure_keys.add(key)
        self._journal_buffer.append({
            "category": "auto_creation",
            "action_type": "name_transform_failed",
            "entity_id": None,
            "entity_name": pattern,
            "description": (
                "Name transform failed during auto-creation run (%s): %s — "
                "pattern /%s/ with replacement '%s'; affected stream names "
                "were left untransformed" % (
                    failure.kind, failure.message, pattern, replacement)
            ),
            "before_value": {
                "pattern": pattern,
                "replacement": replacement,
                "failure_kind": failure.kind,
            },
            "after_value": None,
            "user_initiated": False,
            "mutation_source": journal.MUTATION_SOURCE_AUTO_CREATION,
            "batch_id": str(self._execution_id),
        })
        if len(self._journal_buffer) >= self._journal_flush_threshold:
            self._flush_journal_buffer()

    async def _resolve_logo_id(self, logo_url: str, name_hint: str = "") -> Optional[int]:
        """Resolve a logo URL to a Dispatcharr logo_id, creating if needed.

        Uses a cache to avoid duplicate lookups/creations within the same run.
        """
        if not logo_url:
            return None

        # Check cache first
        if logo_url in self._logo_cache:
            logger.debug("[AUTO-CREATE-EXEC] Cache hit for '%s' -> id=%s", logo_url[:60], self._logo_cache[logo_url])
            return self._logo_cache[logo_url]

        try:
            # Try to create the logo (Dispatcharr will reject duplicates)
            logo_name = name_hint or logo_url.split("/")[-1]
            result = await self.client.create_logo({"name": logo_name, "url": logo_url})
            logo_id = result.get("id")
            if logo_id:
                self._logo_cache[logo_url] = logo_id
                return logo_id
        except Exception as e:
            error_str = str(e).lower()
            # If logo already exists, find it by URL
            if "already exists" in error_str or "400" in error_str:
                try:
                    existing = await self.client.find_logo_by_url(logo_url)
                    if existing:
                        logo_id = existing.get("id")
                        self._logo_cache[logo_url] = logo_id
                        return logo_id
                except Exception as search_err:
                    logger.warning("[AUTO-CREATE-EXEC] Failed to find existing logo by URL: %s", search_err)
            else:
                logger.warning("[AUTO-CREATE-EXEC] Failed to create logo from '%s': %s", logo_url, e)
        return None

    def _get_group_name(self, group_id) -> Optional[str]:
        """Resolve a group ID to its name."""
        if not group_id:
            return None
        group = self._group_by_id.get(group_id) or self._created_groups.get(
            next((k for k, v in self._created_groups.items() if v.get("id") == group_id), None)
        )
        return group.get("name") if group else None

    async def _execute_create_channel(self, action: Action, stream_ctx: StreamContext,
                                       exec_ctx: ExecutionContext, template_ctx: dict,
                                       rule_target_group_id: int = None,
                                       normalization_group_ids: list[int] = None,
                                       match_scope_target_group: bool = True,
                                       rule_scope_group_id: int = None,
                                       allow_manual_channel_merge: bool = False,
                                       fold_match_key: bool = False) -> ActionResult:
        """Execute create_channel action."""
        params = action.params
        name_template = params.get("name_template", "{stream_name}")
        channel_name = TemplateVariables.expand_template(name_template, template_ctx, exec_ctx.custom_variables)
        logger.debug("[AUTO-CREATE-EXEC] Template '%s' expanded to '%s'", name_template, channel_name)
        channel_name = self._apply_name_transform(channel_name, params)

        # Track details for the execution log
        action_details = []

        # enhancedchannelmanager-3gigl: surface a name-transform failure as a
        # user-visible execution-log detail (previously a hash-labeled
        # safe_regex WARNING only — a silent per-stream no-op).
        if self._last_name_transform_error:
            action_details.append(self._last_name_transform_error)

        # Apply normalization engine if enabled (non-empty group IDs list)
        pre_norm_name = channel_name
        if normalization_group_ids and self._normalization_engine:
            logger.debug("[AUTO-CREATE-EXEC] Applying normalization groups %s to '%s'", normalization_group_ids, channel_name)
            try:
                # bd-eio04.1: preserve_superscripts kwarg removed. The
                # auto-creation path now shares NormalizationPolicy with
                # Test Rules — both strip letter-superscripts AND convert
                # numeric superscripts (ESPN² -> ESPN2). Closes GH #104.
                norm_result = self._normalization_engine.normalize(
                    channel_name, group_ids=normalization_group_ids)
                if norm_result.normalized != channel_name:
                    logger.debug("[AUTO-CREATE-EXEC] Normalized channel name: '%s' -> '%s'", channel_name, norm_result.normalized)
                    for rule_id, before, after in norm_result.transformations:
                        logger.debug("[AUTO-CREATE-EXEC]   Rule %s: '%s' -> '%s'", rule_id, before, after)
                    action_details.append(f"Name normalized: '{channel_name}' \u2192 '{norm_result.normalized}'")
                    channel_name = norm_result.normalized
                else:
                    logger.debug("[AUTO-CREATE-EXEC] Normalization applied %d groups but no changes for '%s'",
                                len(normalization_group_ids), channel_name)
            except Exception as e:
                logger.warning("[AUTO-CREATE-EXEC] Failed to normalize channel name '%s': %s", channel_name, e)
        elif self._normalization_engine:
            logger.debug("[AUTO-CREATE-EXEC] Normalization skipped for '%s' (no groups selected)", channel_name)

        if_exists = params.get("if_exists", "skip")
        group_id = params.get("group_id") or exec_ctx.current_group_id or rule_target_group_id
        logger.debug(
            "[AUTO-CREATE-EXEC] name='%s' if_exists=%s "
            "group_id=%s (param=%s, "
            "exec_ctx=%s, rule=%s)",
            channel_name, if_exists,
            group_id, params.get('group_id'),
            exec_ctx.current_group_id, rule_target_group_id
        )

        # Check if channel already exists (check with original name before number prefix).
        # When match_scope_target_group is True, restrict the lookup to channels in the
        # effective target group (GH-92, bd-r9mtd) so that two rules targeting different
        # groups can create separate channels with the same name.
        #
        # GH #298 (bd-kncun): prefer the explicit rule-level scope group when set,
        # falling back to the action's derived group_id. NULL rule_scope_group_id
        # preserves the prior behavior (scope = derived group_id).
        scope_group_id = (rule_scope_group_id or group_id) if match_scope_target_group else None
        # enhancedchannelmanager-orzck (W1): block adoption of hand-built MANUAL
        # channels unless the rule explicitly opts in.
        existing = self._find_channel_by_name(
            channel_name, scope_group_id=scope_group_id,
            block_manual=not allow_manual_channel_merge,
            # GH #645 / bead 0vao3: opt-in whitespace/case fold on the merge
            # lookup's comparison key (never on the stored name).
            fold_key=fold_match_key,
        )
        if existing is not None and allow_manual_channel_merge \
                and self._is_manual_channel(existing):
            self._journal_manual_channel_adoption(existing, stream_ctx, action.type)
        elif existing is None and self._last_manual_block is not None:
            # enhancedchannelmanager-wy6l5: the block_manual gate rejected a
            # matching hand-built manual channel. Surface WHY the merge did not
            # happen AND its consequence (a NEW auto channel gets created
            # below) in the execution log + journal — previously this was
            # INFO-log-only and looked like an inexplicable duplicate.
            blocked = self._last_manual_block
            self._journal_manual_channel_block(blocked, stream_ctx, action.type)
            action_details.append(
                f"Matched manual channel '{blocked.get('name')}' "
                f"(id={blocked.get('id')}) but allow_manual_channel_merge is "
                f"off — treated as not found, so a new auto channel is created "
                f"instead of merging into the hand-built channel (enable "
                f"allow_manual_channel_merge on the rule to merge)"
            )
        logger.debug(
            "[AUTO-CREATE-EXEC] Lookup '%s' (scope_group_id=%s): %s",
            channel_name, scope_group_id,
            'found id=' + str(existing['id']) if existing else 'not found'
        )

        if existing:
            existing_group_name = self._get_group_name(existing.get("channel_group_id"))
            if existing_group_name:
                action_details.append(f"Existing channel found in group '{existing_group_name}'")

            # When normalization collapsed a distinct name into an existing one,
            # explain how to keep them separate
            normalized_into_existing = (pre_norm_name != channel_name)
            if normalized_into_existing:
                action_details.append(
                    f"'{pre_norm_name}' became '{channel_name}' after normalization and matched an existing channel. "
                    f"To create separate channels instead: use separate rules with different target groups "
                    f"(e.g., one per country), or adjust the name template to keep the distinguishing text."
                )

            # Rename channel if normalization produces a different name than what's stored
            if normalization_group_ids and self._normalization_engine:
                existing_name = existing["name"]
                _num_pfx = re.match(r'^(\d+\s*\|\s*)', existing_name)
                existing_base = _num_pfx.group(0) if _num_pfx else ""
                existing_core = existing_name[len(existing_base):]
                if existing_core.lower() != channel_name.lower():
                    new_name = existing_base + channel_name
                    if exec_ctx.dry_run:
                        action_details.append(f"Would rename channel: '{existing_name}' \u2192 '{new_name}'")
                        existing["name"] = new_name
                    else:
                        try:
                            await self.client.update_channel(existing["id"], {"name": new_name})
                            action_details.append(f"Renamed channel: '{existing_name}' \u2192 '{new_name}'")
                            logger.info("[AUTO-CREATE-EXEC] Renamed channel %s: '%s' -> '%s'", existing["id"], existing_name, new_name)
                            # Update caches
                            old_lower = existing_name.lower()
                            self._channel_by_name.pop(old_lower, None)
                            # bead g0uuf: drop the renamed dict from its old
                            # candidate list (other same-named channels keep
                            # theirs) and index it under the new spelling.
                            old_cands = self._by_name_candidates.get(old_lower)
                            if old_cands:
                                self._by_name_candidates[old_lower] = [
                                    c for c in old_cands if c is not existing]
                            existing["name"] = new_name
                            self._channel_by_name[new_name.lower()] = existing
                            self._add_candidate(
                                self._by_name_candidates, new_name.lower(), existing)
                            # GH #645 / bead 0vao3: the renamed spelling must
                            # also be fold-findable (old fold keys still point
                            # at the same mutated dict, so they stay correct).
                            self._fold_key_to_channel.setdefault(
                                _fold_key(new_name), existing)
                            self._add_candidate(
                                self._fold_key_candidates, _fold_key(new_name), existing)
                        except Exception as e:
                            logger.warning("[AUTO-CREATE-EXEC] Failed to rename channel '%s' to '%s': %s", existing_name, new_name, e)
                            action_details.append(f"Failed to rename channel: {e}")

            if if_exists == "skip":
                exec_ctx.current_channel_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Channel '{channel_name}' already exists, skipped",
                    entity_type="channel",
                    entity_id=existing["id"],
                    entity_name=channel_name,
                    skipped=True,
                    details=action_details
                )
            elif if_exists in ("merge", "merge_only"):
                # channels_touched accounting now happens at the chokepoint
                # _add_stream_to_channel (bd-0emgo.4), so this path does NOT
                # write _merge_streams_added_by_channel — that dict is reserved
                # for merge_streams prune accounting, which create_channel does
                # not enable. Add stream to existing channel:
                result = await self._add_stream_to_channel(existing, stream_ctx, exec_ctx)
                result.details = action_details + result.details
                return result
            elif if_exists == "update":
                # Update existing channel properties
                return await self._update_channel(existing, stream_ctx, exec_ctx, params)

        # merge_only: don't create new channels, only merge into existing ones
        if if_exists == "merge_only":
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Channel '{channel_name}' not found, skipped (merge only)",
                entity_type="channel",
                entity_name=channel_name,
                skipped=True,
                details=action_details
            )

        # Determine channel number first (needed for name prefix)
        channel_number = self._get_next_channel_number(params.get("channel_number", "auto"))
        logger.debug("[AUTO-CREATE-EXEC] Channel number: spec=%s -> %s", params.get('channel_number', 'auto'), channel_number)

        # Apply channel number in name if setting is enabled
        base_name = channel_name  # Save before prefix for base-name mapping
        channel_name = self._apply_channel_number_in_name(channel_name, channel_number)
        if channel_name != base_name:
            logger.debug("[AUTO-CREATE-EXEC] Name with number prefix: '%s' -> '%s'", base_name, channel_name)

        # Resolve group name for descriptions
        group_name = self._get_group_name(group_id)
        group_label = f"'{group_name}'" if group_name else str(group_id)

        # Observability (bd-eio04.9): record whether normalization was
        # applied to this creation. Three buckets:
        #   - "true"    — normalization ran AND changed the name
        #   - "false"   — normalization ran but produced no change
        #   - "skipped" — no normalization groups configured for the rule
        # Only the non-dry-run path emits the metric (dry-run previews
        # do not create channels in the real sense).
        if normalization_group_ids and self._normalization_engine:
            normalized_label = "true" if pre_norm_name != channel_name else "false"
        else:
            normalized_label = "skipped"

        # BD-F (bd-a5lb2) — bulk-M3U dedup hook per ADR-008 §D1.
        #
        # The existing exact / normalized lookups above have already
        # returned None (we are below the `if existing:` branch). If
        # the engine was invoked from the M3U-refresh post-sync path
        # (``triggered_by == 'm3u_refresh'``), score the incoming
        # ``stream_ctx.stream_name`` against the same-group existing
        # channels via BD-A's matcher. A confidence above the
        # operator-configured threshold (clamped to the §D2 floor by
        # the matcher itself) enqueues a ``pending_merges`` row and
        # signals "skip the create_channel API call" — the pending
        # row encodes the deferred operator decision for BD-J's UI
        # (and BD-E's accept/dismiss endpoints) to resolve.
        #
        # The hook short-circuits in dry-run (a preview must not
        # mutate the queue) and for triggered_by values other than
        # ``m3u_refresh`` (scheduled / manual auto-creation runs
        # predate the dedup epic and stay on the legacy "always
        # create" semantics — they are not one of the four ADR-008
        # §D1 interactive trigger surfaces).
        # Per BD-F reviewer Warn-1 (bd-a5lb2 fix-forward): degrade
        # gracefully on hook failure. Without this guard, a hook bug
        # (matcher exception, ORM error other than IntegrityError, etc.)
        # would propagate out of ``_execute_create_channel`` and abort
        # the entire engine batch via the outer ``run_pipeline``
        # handler. Falling through to ``create_channel`` preserves
        # pre-BD-F behavior per stream and keeps batch progress intact.
        try:
            dedup_skip = await self._maybe_enqueue_pending_merge(
                stream_ctx=stream_ctx,
                channel_name=channel_name,
                group_id=group_id,
                exec_ctx=exec_ctx,
            )
        except Exception:
            logger.warning(
                "[DEDUP] Hook failed for stream=%s group_id=%s; falling through "
                "to channel creation (pre-BD-F behavior)",
                channel_name, group_id, exc_info=True,
            )
            dedup_skip = None
        if dedup_skip is not None:
            # Carry the accumulated context (normalization notes, wy6l5
            # manual-block reason, …) onto the dedup-skip result so the
            # execution log keeps the full story for this stream.
            dedup_skip.details = action_details + dedup_skip.details
            return dedup_skip

        # Create new channel
        if exec_ctx.dry_run:
            # Track simulated channel so subsequent streams in this run
            # see it as existing (matches execute-mode behavior)
            dry_id = self._next_dry_run_id
            self._next_dry_run_id -= 1
            simulated = {"id": dry_id, "name": channel_name, "channel_number": channel_number,
                         "channel_group_id": group_id, "streams": [stream_ctx.stream_id],
                         # enhancedchannelmanager-orzck (W1): a channel the engine
                         # creates in THIS run is an auto channel — mark it so the
                         # manual-channel gate lets later streams in the same run
                         # dedup-merge into it (otherwise the missing key would be
                         # read as "manual/protected" and block the merge).
                         "auto_created": True}
            if stream_ctx.tvg_id:
                simulated["tvg_id"] = stream_ctx.tvg_id
            self._created_channels[channel_name.lower()] = simulated
            self._channel_by_id[dry_id] = simulated
            # bead g0uuf: register in the multi-candidate index so a scoped
            # lookup finds this channel even when a same-named channel in
            # another group holds the single-slot map entries.
            self._add_candidate(self._by_name_candidates, channel_name.lower(), simulated)
            # Map base name to prefixed channel so subsequent lookups by base name merge correctly
            if base_name.lower() != channel_name.lower():
                self._base_name_to_channel[base_name.lower()] = simulated
                self._add_candidate(self._base_name_candidates, base_name.lower(), simulated)
            # GH #645 / bead 0vao3: register the fold keys so later streams in
            # this run can fold-match the simulated channel (first-seen wins).
            self._fold_key_to_channel.setdefault(_fold_key(channel_name), simulated)
            self._fold_key_to_channel.setdefault(_fold_key(base_name), simulated)
            self._add_candidate(self._fold_key_candidates, _fold_key(channel_name), simulated)
            self._add_candidate(self._fold_key_candidates, _fold_key(base_name), simulated)
            self._used_channel_numbers.add(channel_number)
            exec_ctx.current_channel_id = dry_id
            exec_ctx.created_channel_ids.add(dry_id)
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would create channel '{channel_name}' (#{channel_number}) in group {group_label}",
                entity_type="channel",
                entity_name=channel_name,
                created=True,
                details=action_details
            )

        # Create channel via API
        try:
            channel_data = {
                "name": channel_name,
                "channel_number": channel_number,
                "channel_group_id": group_id,
                "streams": [stream_ctx.stream_id]
            }

            # Resolve logo URL to a Dispatcharr logo_id
            if stream_ctx.logo_url:
                logo_id = await self._resolve_logo_id(stream_ctx.logo_url, channel_name)
                if logo_id:
                    channel_data["logo_id"] = logo_id
            if stream_ctx.tvg_id:
                channel_data["tvg_id"] = stream_ctx.tvg_id

            new_channel = await self.client.create_channel(channel_data)

            # Observability (bd-eio04.9) — one counter increment per real
            # channel creation. Wrapped in try/except so a missing
            # metric registry (e.g. CI-without-prometheus) never blocks
            # a creation that otherwise succeeded.
            try:
                from observability import get_metric
                get_metric("auto_creation_channels_created_total").labels(
                    normalized=normalized_label
                ).inc()
            except Exception:  # pragma: no cover
                logger.debug("[AUTO-CREATE-EXEC] metric increment failed", exc_info=True)

            # Track the new channel. enhancedchannelmanager-orzck (W1): the
            # engine just auto-created it, so mark auto_created=True regardless of
            # whether the API response echoed the flag — otherwise the
            # manual-channel gate would read the missing/falsy key as
            # "manual/protected" and block later same-run streams from
            # dedup-merging into this freshly-created channel.
            new_channel["auto_created"] = True
            self._created_channels[channel_name.lower()] = new_channel
            self._channel_by_id[new_channel["id"]] = new_channel
            # bead g0uuf: register in the multi-candidate index so a scoped
            # lookup finds this channel even when a same-named channel in
            # another group holds the single-slot map entries.
            self._add_candidate(self._by_name_candidates, channel_name.lower(), new_channel)
            # Map base name to prefixed channel so subsequent lookups by base name merge correctly
            if base_name.lower() != channel_name.lower():
                self._base_name_to_channel[base_name.lower()] = new_channel
                self._add_candidate(self._base_name_candidates, base_name.lower(), new_channel)
            # GH #645 / bead 0vao3: register the fold keys so later streams in
            # this run can fold-match the new channel (first-seen wins).
            self._fold_key_to_channel.setdefault(_fold_key(channel_name), new_channel)
            self._fold_key_to_channel.setdefault(_fold_key(base_name), new_channel)
            self._add_candidate(self._fold_key_candidates, _fold_key(channel_name), new_channel)
            self._add_candidate(self._fold_key_candidates, _fold_key(base_name), new_channel)
            self._used_channel_numbers.add(channel_number)
            self._channel_assigned_numbers[new_channel["id"]] = channel_number
            exec_ctx.current_channel_id = new_channel["id"]
            exec_ctx.created_channel_ids.add(new_channel["id"])

            # Assign default channel profiles if configured
            profile_desc = await self._assign_default_profiles(
                new_channel["id"], exec_ctx
            )

            desc = f"Created channel '{channel_name}' (#{channel_number}) in group {group_label}"
            if profile_desc:
                desc += f", {profile_desc}"

            return ActionResult(
                success=True,
                action_type=action.type,
                description=desc,
                entity_type="channel",
                entity_id=new_channel["id"],
                entity_name=channel_name,
                created=True,
                details=action_details
            )

        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to create channel '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to create channel '{channel_name}'",
                error=str(e)
            )

    async def _ensure_channel_m3u_counts(self, channel_id: int) -> None:
        """Lazily fetch and seed per-provider stream counts for a channel."""
        if channel_id in self._seeded_channels:
            return
        self._seeded_channels.add(channel_id)
        try:
            streams = await self.client.get_channel_streams(channel_id)
            for s in streams:
                if isinstance(s, dict) and s.get("m3u_account") is not None:
                    key = (channel_id, s["m3u_account"])
                    self._channel_m3u_counts[key] = self._channel_m3u_counts.get(key, 0) + 1
            logger.debug(
                "[AUTO-CREATE-EXEC] Seeded provider counts for channel %s: "
                "%s providers, %s streams",
                channel_id,
                sum(1 for k in self._channel_m3u_counts if k[0] == channel_id),
                sum(v for k, v in self._channel_m3u_counts.items() if k[0] == channel_id)
            )
        except Exception as e:
            logger.debug("[AUTO-CREATE-EXEC] Failed to fetch streams for channel %s: %s", channel_id, e)

    async def _add_stream_to_channel(self, channel: dict, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext,
                                      merge_provenance: dict | None = None,
                                      journal_category: str = "auto_creation") -> ActionResult:
        """Add a stream to an existing channel (merge behavior).

        ``merge_provenance`` (enhancedchannelmanager-jnzst) carries the scored-
        fuzzy provenance (score, effective threshold, signal, both parsed
        callsigns, rule_id, allowlist) for the journal. None on the exact /
        legacy paths so their journal entries are unchanged.

        ``journal_category`` (enhancedchannelmanager-ti939.2.1) is the journal
        category for the per-merge entry. The event_sync attach path passes
        "event_sync" so its attaches are auditable as their own category; the
        default keeps every pre-existing path on "auto_creation". The journal
        ``action_type`` stays "merge_stream" for BOTH so the journal-driven
        surgical unmerge (``_journal_driven_unmerge``) covers event_sync
        attaches on the legacy rollback path too.
        """
        channel_id = channel["id"]
        channel_name = channel["name"]

        # Get current streams
        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]
        logger.debug(
            "[AUTO-CREATE-EXEC] Adding stream %s (%r) "
            "to channel '%s' (id=%s), current streams=%s",
            stream_ctx.stream_id, stream_ctx.stream_name,
            channel_name, channel_id, current_streams
        )

        stream_count = len(current_streams)

        if stream_ctx.stream_id in current_streams:
            exec_ctx.current_channel_id = channel_id
            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Stream already in channel '{channel_name}' ({stream_count} streams)",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                skipped=True
            )

        new_count = stream_count + 1

        def _track_m3u_count():
            """Increment per-provider stream count for max_streams_per_channel tracking."""
            if stream_ctx.m3u_account_id is not None:
                key = (channel_id, stream_ctx.m3u_account_id)
                prev = self._channel_m3u_counts.get(key, 0)
                self._channel_m3u_counts[key] = prev + 1
                logger.debug(
                    "[AUTO-CREATE-EXEC] Provider stream count for channel '%s' "
                    "(id=%s), provider %s "
                    "(id=%s): %s -> %s",
                    channel_name, channel_id, stream_ctx.m3u_account_name,
                    stream_ctx.m3u_account_id, prev, prev + 1
                )

        if exec_ctx.dry_run:
            # Update cached channel so subsequent dry-run merges see this stream
            channel["streams"] = current_streams + [stream_ctx.stream_id]
            _track_m3u_count()
            exec_ctx.current_channel_id = channel_id
            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Would add stream to channel '{channel_name}' (stream {new_count})",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True
            )

        try:
            # Save previous state for rollback
            previous_state = {
                "streams": current_streams.copy()
            }

            # Add stream
            new_streams = current_streams + [stream_ctx.stream_id]
            await self.client.update_channel(channel_id, {"streams": new_streams})

            # Update cached channel so subsequent merges see the full list
            channel["streams"] = new_streams
            _track_m3u_count()
            exec_ctx.current_channel_id = channel_id

            # bd-0emgo.5: per-merge journal entry for lightweight
            # recoverability. Tagging each LIVE merge with
            # batch_id=str(execution_id) lets an operator list every
            # (channel_id, stream_id) pair a run touched via
            # get_journal(batch_id=...) and revert a bad run. STREAM IDs
            # ONLY in before/after — never URLs/objects (they embed
            # provider credentials). Skipped when no execution_id was
            # threaded in (direct-construct callers): such entries would
            # be uncorrelatable noise. Dry-run never reaches here.
            self._journal_merge(
                channel_id, channel_name, stream_ctx.stream_id,
                current_streams, new_streams,
                provenance=merge_provenance,
                category=journal_category,
                stream_name=stream_ctx.stream_name,
            )

            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Added stream to channel '{channel_name}' (stream {new_count})",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
                previous_state=previous_state
            )

        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to add stream to channel '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type="merge_stream",
                description=f"Failed to add stream to channel",
                error=str(e)
            )

    def _journal_merge(self, channel_id: int, channel_name: str, stream_id: int,
                       before_ids: list, after_ids: list,
                       provenance: dict | None = None,
                       category: str = "auto_creation",
                       stream_name: str | None = None) -> None:
        """Write a per-merge journal entry for an executed LIVE merge (bd-0emgo.5).

        Tags the entry with ``batch_id=str(execution_id)`` so an operator can
        list every ``(channel_id, stream_id)`` pair a run touched and recover
        from a bad merge. ``before_value``/``after_value`` carry STREAM IDs
        ONLY — never URLs/objects, which embed provider credentials. Entries are
        buffered and flushed via ``journal.log_entries`` to reduce commit churn
        during large auto-creation runs; ``JournalEntry.timestamp`` is stamped
        when the buffer flushes, not when each merge is queued.

        enhancedchannelmanager-jnzst: when a scored-fuzzy ``provenance`` dict is
        supplied, it is folded into ``after_value`` alongside the stream IDs
        (the free-form JSON escape hatch — no migration). It records the score,
        effective threshold, signal fired, both parsed callsigns, rule_id and
        allowlist so a bad scored merge is auditable.

        enhancedchannelmanager-ti939.2.1: ``category`` lets the event_sync
        attach path write its entries under the "event_sync" category (its own
        auditable stream) while keeping ``action_type="merge_stream"`` so
        batch-recovery readers and the journal-driven surgical unmerge treat
        them identically. ``stream_name`` (when given) puts the secondary
        stream's NAME in the description alongside its id — names survive
        stale IDs (journal is historical audit with lazy resolution, ADR-008
        §D4 precedent).
        """
        if self._execution_id is None:
            # No execution_id threaded in (direct-construct callers): an
            # uncorrelatable entry would be recovery noise, so skip.
            return
        after_value = {"stream_ids": list(after_ids)}
        if provenance:
            # Fold provenance into the after_value JSON. Keep stream_ids the
            # leading key so existing batch-recovery readers are unaffected.
            after_value["match"] = provenance
        if stream_name:
            description = "Merged stream '%s' (id %s) into channel '%s'" % (
                stream_name, stream_id, channel_name)
        else:
            description = "Merged stream %s into channel '%s'" % (
                stream_id, channel_name)
        self._journal_buffer.append({
            "category": category,
            "action_type": "merge_stream",
            "entity_id": channel_id,
            "entity_name": channel_name,
            "description": description,
            "before_value": {"stream_ids": list(before_ids)},
            "after_value": after_value,
            "user_initiated": False,
            "mutation_source": journal.MUTATION_SOURCE_AUTO_CREATION,
            "batch_id": str(self._execution_id),
        })
        if len(self._journal_buffer) >= self._journal_flush_threshold:
            self._flush_journal_buffer()

    async def _update_channel(self, channel: dict, stream_ctx: StreamContext,
                               exec_ctx: ExecutionContext, params: dict) -> ActionResult:
        """Update an existing channel's properties."""
        channel_id = channel["id"]
        channel_name = channel["name"]

        exec_ctx.current_channel_id = channel_id

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type="update_channel",
                description=f"Would update channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True
            )

        try:
            # Save previous state
            previous_state = {
                "logo_url": channel.get("logo_url"),
                "tvg_id": channel.get("tvg_id")
            }

            updates = {}
            if stream_ctx.logo_url and not channel.get("logo_url"):
                updates["logo_url"] = stream_ctx.logo_url
            if stream_ctx.tvg_id and not channel.get("tvg_id"):
                updates["tvg_id"] = stream_ctx.tvg_id

            if updates:
                await self.client.update_channel(channel_id, updates)
                # Update simulated state so subsequent actions see the changes
                channel.update(updates)

            return ActionResult(
                success=True,
                action_type="update_channel",
                description=f"Updated channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=bool(updates),
                previous_state=previous_state
            )

        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to update channel '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type="update_channel",
                description=f"Failed to update channel",
                error=str(e)
            )

    # =========================================================================
    # Group Creation
    # =========================================================================

    async def _execute_create_group(self, action: Action, stream_ctx: StreamContext,
                                     exec_ctx: ExecutionContext, template_ctx: dict) -> ActionResult:
        """Execute create_group action."""
        params = action.params
        name_template = params.get("name_template", "{stream_group}")
        group_name = TemplateVariables.expand_template(name_template, template_ctx, exec_ctx.custom_variables)
        logger.debug("[AUTO-CREATE-EXEC] Template '%s' expanded to '%s'", name_template, group_name)
        group_name = self._apply_name_transform(group_name, params)
        if_exists = params.get("if_exists", "use_existing")
        logger.debug("[AUTO-CREATE-EXEC] name='%s' if_exists=%s", group_name, if_exists)

        # Track details for the execution log.
        # enhancedchannelmanager-3gigl: surface a name-transform failure as a
        # user-visible execution-log detail (previously a hash-labeled
        # safe_regex WARNING only — a silent per-stream no-op).
        action_details = []
        if self._last_name_transform_error:
            action_details.append(self._last_name_transform_error)

        if not group_name:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Group name is empty after template expansion",
                error="Empty group name",
                details=action_details
            )

        # Check if group already exists
        existing = self._find_group_by_name(group_name)

        if existing:
            if if_exists == "use_existing":
                exec_ctx.current_group_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Using existing group '{group_name}'",
                    entity_type="group",
                    entity_id=existing["id"],
                    entity_name=group_name,
                    skipped=True,
                    details=action_details
                )
            else:  # skip
                exec_ctx.current_group_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Group '{group_name}' already exists, skipped",
                    entity_type="group",
                    entity_id=existing["id"],
                    entity_name=group_name,
                    skipped=True,
                    details=action_details
                )

        # Create new group
        if exec_ctx.dry_run:
            # Track simulated group so subsequent streams see it as existing
            simulated = {"id": -1, "name": group_name}
            self._created_groups[group_name.lower()] = simulated
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would create group '{group_name}'",
                entity_type="group",
                entity_name=group_name,
                created=True,
                details=action_details
            )

        try:
            new_group = await self.client.create_channel_group(group_name)

            # Track the new group
            self._created_groups[group_name.lower()] = new_group
            exec_ctx.current_group_id = new_group["id"]

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Created group '{group_name}'",
                entity_type="group",
                entity_id=new_group["id"],
                entity_name=group_name,
                created=True,
                details=action_details
            )

        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to create group '%s': %s", group_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to create group '{group_name}'",
                error=str(e),
                details=action_details
            )

    # =========================================================================
    # Stream Merging
    # =========================================================================

    async def _execute_merge_streams(self, action: Action, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext, template_ctx: dict,
                                      normalization_group_ids: list[int] = None,
                                      match_scope_target_group: bool = True,
                                      rule_scope_group_id: int = None,
                                      allow_manual_channel_merge: bool = False,
                                      rule_id: int = None) -> ActionResult:
        """Execute merge_streams action.

        GH #298 (bd-kncun): when ``match_scope_target_group`` is on and the rule
        carries an explicit ``rule_scope_group_id``, the channel match is scoped
        to that group across EVERY resolution path (name_exact, regex, tvg_id,
        normalized auto, core-name map, deparen, word-prefix, call-sign). merge_
        streams has no action group_id, so ``rule_scope_group_id`` is the only
        source of an effective scope group — if scope is on but the rule has no
        explicit scope group (NULL), behavior is unchanged (search all groups),
        and the rule builder warns the operator about that case.
        """
        params = action.params
        target = params.get("target", "auto")
        find_channel_by = params.get("find_channel_by")
        max_streams = params.get("max_streams_per_channel", 0)  # 0 = unlimited
        find_channel_value = params.get("find_channel_value")
        remove_non_matching = params.get("remove_non_matching", False) is True
        # bd-0emgo.1: merge_streams target=auto defaults to EXACT normalized-name
        # equality. The legacy fuzzy cascade (core-name / deparen / word-prefix
        # containment / call-sign) caused production over-matching (a "SKY Sport
        # 4K" channel — core "sky sport" — absorbed 75 unrelated "Sky Sport *"
        # streams via the word-prefix step). Set loose_name_match=True on the
        # action to restore the legacy cascade.
        loose_name_match = params.get("loose_name_match", False) is True
        # enhancedchannelmanager-jnzst: SCORED-FUZZY path. When loose_name_match
        # is on AND a min_score is configured, channel resolution runs through
        # the unified scoring core (services.dedup_matcher.score_all) instead of
        # the legacy core-name/deparen/word-prefix/callsign cascade. The schema
        # has already enforced (Component A): min_score in [floor, 1.0],
        # loose_name_match=True, and a non-empty target_channel_in_group
        # allowlist. allow_no_callsign is the Q1 opt-in.
        min_score = params.get("min_score")
        scored_fuzzy = loose_name_match and min_score is not None
        allow_no_callsign = params.get("allow_no_callsign", False) is True
        tie_break = params.get("tie_break", "highest_score")
        # bd-0emgo.3: TARGET-CHANNEL group filter (post-resolution reject).
        # Distinct from the stream-side normalized_name_[not_]in_group
        # conditions (which only gate whether the rule FIRES). These constrain
        # which existing channel the merge may land in, by the RESOLVED
        # channel's channel_group_id. Absent/empty list = no filter (back-
        # compat). target_channel_not_in_group = "merge anywhere EXCEPT these
        # groups" (primary); target_channel_in_group = "only merge into these
        # groups" (complement). Applied AFTER resolution, mirroring the GH #298
        # scope reject below.
        target_channel_not_in_group = params.get("target_channel_not_in_group") or []
        target_channel_in_group = params.get("target_channel_in_group") or []
        # Effective scope group for merge lookups (GH #298). merge_streams has no
        # action group_id, so the explicit rule scope group is the only source.
        # None when scope is off or no explicit group is pinned — preserves the
        # prior group-agnostic match.
        effective_scope_group_id = rule_scope_group_id if match_scope_target_group else None
        logger.debug(
            "[AUTO-CREATE-EXEC] target=%s find_by=%s "
            "find_value=%s stream=%r scope_group_id=%s",
            target, find_channel_by,
            find_channel_value, stream_ctx.stream_name, effective_scope_group_id
        )

        # enhancedchannelmanager-wy6l5: the manual channel the block_manual
        # gate rejected during resolution (if any). Read at the terminal
        # return points so the execution log shows WHY the merge was withheld
        # instead of the generic "no existing channel found".
        blocked_manual = None

        # For existing_channel target, find the channel
        if target == "existing_channel" or target == "auto":
            channel = None

            # Explicit find fields belong only to existing_channel. The UI
            # hides but historically retained them when switching to auto;
            # consuming that stale state (often name_exact with no value)
            # suppressed auto's normalized-identity lookup entirely (GH #845).
            if target == "existing_channel" and find_channel_by == "name_exact":
                expanded_name = TemplateVariables.expand_template(find_channel_value or "", template_ctx, exec_ctx.custom_variables)
                channel = self._find_channel_by_name(
                    expanded_name, scope_group_id=effective_scope_group_id,
                    block_manual=not allow_manual_channel_merge,
                )
                if channel is None:
                    blocked_manual = self._last_manual_block
            elif target == "existing_channel" and find_channel_by == "name_regex":
                channel = self._find_channel_by_regex(find_channel_value)
            elif target == "existing_channel" and find_channel_by == "tvg_id":
                channel = self._find_channel_by_tvg_id(find_channel_value or stream_ctx.tvg_id)

            # enhancedchannelmanager-jnzst: SCORED-FUZZY resolution. When a
            # min_score is configured, resolve through the unified scoring core
            # against the allowlisted target groups — NOT the legacy cascade.
            # The core enforces the M1 callsign hard-reject, the tvg_id
            # override, and the Q1 no-callsign policy; provenance is captured
            # for the journal. The schema guarantees target_channel_in_group is
            # non-empty here.
            scored_provenance = None
            if scored_fuzzy and not channel and target in ("auto", "existing_channel"):
                channel, scored_provenance = self._resolve_scored_fuzzy(
                    stream_ctx,
                    min_score=float(min_score),
                    allowed_groups=target_channel_in_group,
                    allow_no_callsign=allow_no_callsign,
                    tie_break=tie_break,
                    rule_id=rule_id,
                )

            # Auto-fallback: if no find_channel_by was specified and target is "auto",
            # try to find by normalized stream name (strips prefixes, applies normalization)
            # Skipped on the scored-fuzzy path — the scored resolver above owns it.
            if not scored_fuzzy and not channel and target == "auto":
                # Use the exact rule-scoped identity already computed at the
                # action chokepoint. Re-normalizing here without group_ids made
                # a normalized_name_in_group condition and its merge action
                # disagree about the same stream (GH #845).
                lookup_name = template_ctx[TemplateVariables.NORMALIZED_NAME]
                logger.debug("[AUTO-CREATE-EXEC] Auto-lookup by normalized name: '%s'", lookup_name)
                # bd-0emgo.1: default to exact normalized-name equality. With
                # exact_only=True the GH-104 re-normalize/core-name fuzzy
                # fallbacks inside _find_channel_by_name are skipped; only the
                # exact-key indices are consulted. loose_name_match=True restores
                # the legacy fuzzy lookup.
                channel = self._find_unique_channel_by_exact_identity(
                    lookup_name, scope_group_id=effective_scope_group_id,
                    block_manual=not allow_manual_channel_merge,
                )
                if channel is None:
                    blocked_manual = blocked_manual or self._last_manual_block

            # Core-name fallback (LEGACY FUZZY — bd-0emgo.1): strip country prefix
            # + quality suffix, deparenthesize, and do word-prefix containment.
            # This is the over-matching cascade; gated behind loose_name_match so
            # the default (exact) merge does not run it.
            if loose_name_match and not scored_fuzzy and not channel and normalization_group_ids and self._normalization_engine:
                try:
                    core_name = self._normalization_engine.extract_core_name(stream_ctx.stream_name)
                    if core_name:
                        logger.debug("[AUTO-CREATE-EXEC] Core name fallback: '%s' -> '%s'", stream_ctx.stream_name, core_name)
                        channel = self._core_name_to_channel.get(core_name.lower()) or self._find_channel_by_name(core_name)

                        # Sub-step A: Deparenthesize stream core name and retry
                        if not channel:
                            deparen = re.sub(r'\(([^)]+)\)', r'\1', core_name)
                            deparen = re.sub(r'\s+', ' ', deparen).strip()
                            if deparen.lower() != core_name.lower():
                                logger.debug("[AUTO-CREATE-EXEC] Deparen fallback: '%s' -> '%s'", core_name, deparen)
                                channel = self._core_name_to_channel.get(deparen.lower()) \
                                          or self._find_channel_by_name(deparen)

                        # Sub-step B: Word-prefix containment (single-candidate only)
                        if not channel:
                            lookup = re.sub(r'\(([^)]+)\)', r'\1', core_name).lower()
                            lookup = re.sub(r'\s+', ' ', lookup).strip()
                            lookup_words = lookup.split()
                            if len(lookup_words) >= 2:
                                candidates = []
                                for ch_core, ch_val in self._core_name_to_channel.items():
                                    ch_words = ch_core.split()
                                    if len(ch_words) >= 2:
                                        shorter, longer = (lookup_words, ch_words) \
                                            if len(lookup_words) <= len(ch_words) \
                                            else (ch_words, lookup_words)
                                        if longer[:len(shorter)] == shorter:
                                            candidates.append(ch_val)
                                if len(candidates) == 1:
                                    channel = candidates[0]
                                    logger.debug("[AUTO-CREATE-EXEC] Word-prefix matched '%s' (id=%s)", channel.get('name'), channel.get('id'))
                                elif len(candidates) > 1:
                                    logger.debug("[AUTO-CREATE-EXEC] Word-prefix skipped: %s ambiguous candidates for '%s'", len(candidates), core_name)

                        if channel:
                            logger.debug("[AUTO-CREATE-EXEC] Core name matched '%s' (id=%s)", channel.get('name'), channel.get('id'))
                except Exception as e:
                    logger.debug("[AUTO-CREATE-EXEC] Core name fallback failed: %s", e)

            # Call-sign fallback (LEGACY FUZZY — bd-0emgo.1): match local
            # affiliates by FCC call sign (W/K + 2-3 letters) extracted from both
            # stream and channel names. Gated behind loose_name_match so the
            # default (exact) merge does not run it.
            if loose_name_match and not scored_fuzzy and not channel and normalization_group_ids and self._normalization_engine:
                try:
                    cs = self._normalization_engine.extract_call_sign(stream_ctx.stream_name)
                    if cs:
                        logger.debug("[AUTO-CREATE-EXEC] Call sign fallback: '%s' -> '%s'", stream_ctx.stream_name, cs)
                        channel = self._callsign_to_channel.get(cs)
                        if channel:
                            logger.debug("[AUTO-CREATE-EXEC] Call sign matched '%s' (id=%s)", channel.get('name'), channel.get('id'))
                except Exception as e:
                    logger.debug("[AUTO-CREATE-EXEC] Call sign fallback failed: %s", e)

            # enhancedchannelmanager-orzck (W1): post-resolution MANUAL-channel
            # reject. The name-keyed and fuzzy paths above — name_regex, tvg_id,
            # the global fallback maps (_core_name_to_channel,
            # _callsign_to_channel), the deparen / word-prefix lookups, and the
            # scored-fuzzy resolver — do NOT route through the
            # _find_channel_by_name chokepoint, so its block_manual gate cannot
            # protect them. Mirror the GH #298 scope reject below: whatever path
            # produced the candidate, if it is a hand-built manual channel
            # (auto_created missing/falsy) and the rule did not opt in, treat it
            # as "not found" so the merge does not adopt the manual channel.
            if channel and not allow_manual_channel_merge \
                    and self._is_manual_channel(channel):
                logger.info(
                    "[AUTO-CREATE-EXEC] Manual-channel reject: candidate '%s' "
                    "(id=%s) is a hand-built manual channel (auto_created falsy) "
                    "and allow_manual_channel_merge is off — treating as not found",
                    channel.get('name'), channel.get('id'),
                )
                blocked_manual = channel
                channel = None
            elif channel is None and blocked_manual is None:
                # enhancedchannelmanager-wy6l5: a legacy-fuzzy
                # _find_channel_by_name lookup (core-name / deparen) may have
                # rejected a manual candidate without the call sites above
                # capturing it — pick up the marker from the LAST lookup of
                # this action so the skip reason still surfaces.
                blocked_manual = self._last_manual_block
            elif channel and allow_manual_channel_merge \
                    and self._is_manual_channel(channel):
                # Opt-in path adopted a manual channel — record it for audit.
                self._journal_manual_channel_adoption(channel, stream_ctx, action.type)

            # GH #298 (bd-kncun): post-resolution scope enforcement. The name-
            # keyed paths above — name_regex, tvg_id, and especially the global
            # fallback maps (_core_name_to_channel, _callsign_to_channel) and the
            # deparen / word-prefix lookups — do NOT honor the group scope on
            # their own. Whatever path produced the candidate, if a scope group
            # is in effect and the resolved channel lives in a different group,
            # treat it as "not found" so the rule does not merge across groups.
            # name_exact and the normalized auto-fallback already filtered via
            # _find_channel_by_name(scope_group_id=...); re-checking them here is
            # a cheap, harmless no-op (same group passes again).
            if channel and effective_scope_group_id is not None \
                    and channel.get("channel_group_id") != effective_scope_group_id:
                logger.debug(
                    "[AUTO-CREATE-EXEC] Scope reject: candidate '%s' (id=%s) in "
                    "group %s != scope group %s — treating as not found",
                    channel.get('name'), channel.get('id'),
                    channel.get('channel_group_id'), effective_scope_group_id
                )
                channel = None

            # bd-0emgo.3: TARGET-CHANNEL group filter (post-resolution reject).
            # Mirrors the GH #298 scope reject above, but instead of folding the
            # rejected candidate into the generic "not found" path it returns an
            # explicit skip with a clear reason so the operator sees WHY the
            # merge was withheld. Whatever resolution path produced the
            # candidate, if the resolved channel's group is excluded (or not in
            # the allow-list), the merge is skipped — this is the guard the
            # stream-side conditions could never provide.
            if channel and (target_channel_not_in_group or target_channel_in_group):
                resolved_group_id = channel.get("channel_group_id")
                if target_channel_not_in_group and resolved_group_id in target_channel_not_in_group:
                    logger.info(
                        "[AUTO-CREATE-EXEC] Skipped stream '%s': resolved target "
                        "channel '%s' (id=%s) is in excluded group %s "
                        "(target_channel_not_in_group=%s)",
                        stream_ctx.stream_name, channel.get('name'),
                        channel.get('id'), resolved_group_id,
                        target_channel_not_in_group
                    )
                    return ActionResult(
                        success=True, action_type=action.type,
                        description=(
                            f"Skipped: target channel '{channel['name']}' is in "
                            f"excluded group {resolved_group_id} "
                            f"(target_channel_not_in_group)"
                        ),
                        entity_type="channel", entity_id=channel["id"],
                        entity_name=channel["name"], skipped=True
                    )
                if target_channel_in_group and resolved_group_id not in target_channel_in_group:
                    logger.info(
                        "[AUTO-CREATE-EXEC] Skipped stream '%s': resolved target "
                        "channel '%s' (id=%s) group %s is not in allowed groups "
                        "%s (target_channel_in_group)",
                        stream_ctx.stream_name, channel.get('name'),
                        channel.get('id'), resolved_group_id,
                        target_channel_in_group
                    )
                    return ActionResult(
                        success=True, action_type=action.type,
                        description=(
                            f"Skipped: target channel '{channel['name']}' group "
                            f"{resolved_group_id} is not in allowed groups "
                            f"{target_channel_in_group} (target_channel_in_group)"
                        ),
                        entity_type="channel", entity_id=channel["id"],
                        entity_name=channel["name"], skipped=True
                    )

            if channel:
                # Track merged stream IDs per channel for optional prune step.
                # If prune is enabled for a channel by ANY merge action during this run,
                # the prune step will keep only streams merged into that channel this run.
                self._merge_streams_added_by_channel.setdefault(channel["id"], set()).add(stream_ctx.stream_id)
                if remove_non_matching:
                    self._merge_prune_enabled_channels.add(channel["id"])
                # Enforce per-provider stream limit if configured
                if max_streams > 0 and stream_ctx.m3u_account_id is not None:
                    await self._ensure_channel_m3u_counts(channel["id"])
                    provider_name = stream_ctx.m3u_account_name or f"provider #{stream_ctx.m3u_account_id}"
                    key = (channel["id"], stream_ctx.m3u_account_id)
                    current_count = self._channel_m3u_counts.get(key, 0)
                    logger.debug(
                        "[AUTO-CREATE-EXEC] Max streams check: channel '%s' has "
                        "%s/%s stream(s) from %s",
                        channel['name'], current_count, max_streams, provider_name
                    )
                    if current_count >= max_streams:
                        logger.info(
                            "[AUTO-CREATE-EXEC] Skipped stream '%s': "
                            "channel '%s' already has %s stream(s) "
                            "from %s (limit: %s)",
                            stream_ctx.stream_name, channel['name'],
                            current_count, provider_name, max_streams
                        )
                        return ActionResult(
                            success=True, action_type=action.type,
                            description=f"Skipped: '{channel['name']}' already has "
                                        f"{current_count} stream(s) from {provider_name} "
                                        f"(limit: {max_streams}/provider)",
                            entity_type="channel", entity_id=channel["id"],
                            entity_name=channel["name"], skipped=True
                        )
                return await self._add_stream_to_channel(
                    channel, stream_ctx, exec_ctx,
                    merge_provenance=scored_provenance,
                )
            elif target == "existing_channel":
                if blocked_manual is not None:
                    # enhancedchannelmanager-wy6l5: same failure semantics as
                    # "not found", but tell the operator WHY — the match was a
                    # protected manual channel, not a missing one.
                    self._journal_manual_channel_block(
                        blocked_manual, stream_ctx, action.type)
                    return ActionResult(
                        success=False,
                        action_type=action.type,
                        description=(
                            f"Merge blocked: matched manual channel "
                            f"'{blocked_manual.get('name')}' "
                            f"(id={blocked_manual.get('id')}) but "
                            f"allow_manual_channel_merge is off — treated as "
                            f"not found (enable it on the rule to merge into "
                            f"hand-built channels)"
                        ),
                        entity_type="channel",
                        entity_id=blocked_manual.get("id"),
                        entity_name=blocked_manual.get("name"),
                        error="Channel not found for merge (manual channel blocked)"
                    )
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"No channel found matching {find_channel_by}='{find_channel_value}'",
                    error="Channel not found for merge"
                )
            # For auto target, no matching channel found — skip
            # merge_streams only adds streams to existing channels;
            # use a create_channel action if new channels are needed.

        if blocked_manual is not None:
            # enhancedchannelmanager-wy6l5: user-visible skip reason for the
            # manual-channel block (previously INFO-log-only, leaving the rule
            # looking like it "skipped everything" with no reason).
            self._journal_manual_channel_block(blocked_manual, stream_ctx, action.type)
            return ActionResult(
                success=True,
                action_type=action.type,
                description=(
                    f"Skipped: matched manual channel "
                    f"'{blocked_manual.get('name')}' "
                    f"(id={blocked_manual.get('id')}) but "
                    f"allow_manual_channel_merge is off — treated as not "
                    f"found; stream not merged (merge_streams only adds to "
                    f"existing channels; enable allow_manual_channel_merge on "
                    f"the rule to merge into hand-built channels)"
                ),
                entity_type="channel",
                entity_id=blocked_manual.get("id"),
                entity_name=blocked_manual.get("name"),
                skipped=True
            )
        return ActionResult(
            success=True,
            action_type=action.type,
            description="No existing channel found — stream skipped (merge_streams only adds to existing channels)",
            skipped=True
        )

    # =========================================================================
    # Property Assignment Actions
    # =========================================================================

    async def _execute_assign_logo(self, action: Action, stream_ctx: StreamContext,
                                    exec_ctx: ExecutionContext,
                                    template_ctx: dict) -> ActionResult:
        """Execute assign_logo action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_logo",
                error="No channel to update"
            )

        value = action.params.get("value", "from_stream")
        if value == "from_stream":
            logo_url = stream_ctx.logo_url
        elif value == "from_epg":
            # Resolve logo from EPG data entry's icon_url
            epg_source_id = action.params.get("epg_id")
            if not epg_source_id:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description="No EPG source specified for from_epg logo",
                    error="Missing epg_id for from_epg"
                )
            source_entries = self._epg_data_by_source.get(epg_source_id, [])
            if not source_entries:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"No EPG data entries found for source {epg_source_id}",
                    error=f"EPG source {epg_source_id} has no data entries"
                )
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            epg_data_entry = self._match_epg_data(channel, source_entries)
            if not epg_data_entry:
                channel_name = channel.get("name", "unknown")
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"No matching EPG data for '{channel_name}' in source {epg_source_id}",
                    skipped=True
                )
            logo_url = epg_data_entry.get("icon_url") or epg_data_entry.get("icon")
        else:
            logo_url = TemplateVariables.expand_template(value, template_ctx, exec_ctx.custom_variables)

        if not logo_url:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="No logo URL to assign",
                skipped=True
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign logo: {logo_url[:50]}...",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            channel_name = channel.get("name", "")
            previous_state = {"logo_id": channel.get("logo_id"), "logo_url": channel.get("logo_url")}

            # Resolve logo URL to a Dispatcharr logo_id (same as channel creation)
            logo_id = await self._resolve_logo_id(logo_url, channel_name)
            if not logo_id:
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Could not resolve logo URL to logo_id: {logo_url[:60]}",
                    skipped=True
                )

            await self.client.update_channel(exec_ctx.current_channel_id, {"logo_id": logo_id})
            channel["logo_id"] = logo_id

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned logo to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign logo",
                error=str(e)
            )

    async def _execute_assign_tvg_id(self, action: Action, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext,
                                      template_ctx: dict) -> ActionResult:
        """Execute assign_tvg_id action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_tvg_id",
                error="No channel to update"
            )

        value = action.params.get("value", "from_stream")
        if value == "from_stream":
            tvg_id = stream_ctx.tvg_id
        else:
            tvg_id = TemplateVariables.expand_template(value, template_ctx, exec_ctx.custom_variables)

        if not tvg_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="No tvg_id to assign",
                skipped=True
            )

        if exec_ctx.dry_run:
            # Update simulated channel so subsequent actions (e.g. assign_epg) can use the tvg_id
            simulated = self._channel_by_id.get(exec_ctx.current_channel_id)
            if simulated is not None:
                simulated["tvg_id"] = tvg_id
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign tvg_id: {tvg_id}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"tvg_id": channel.get("tvg_id")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"tvg_id": tvg_id})
            channel["tvg_id"] = tvg_id

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned tvg_id '{tvg_id}' to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign tvg_id",
                error=str(e)
            )

    async def _execute_assign_epg(self, action: Action, stream_ctx: StreamContext,
                                   exec_ctx: ExecutionContext,
                                   defer_on_no_match: bool = False) -> ActionResult:
        """Execute assign_epg action.

        The user selects an EPG source ID (epg_id), but Dispatcharr channels use
        epg_data_id (an EPG data entry). This method resolves the source to the
        best-matching data entry:
        1. For dummy EPGs (1 entry per source): uses that single entry
        2. For standard EPGs: matches by the channel's tvg_id
        3. Fallback: first entry from the source

        ``defer_on_no_match`` (ti939.3.3, event_sync dummy EPG assignment
        only): when the dummy source HAS entries but none matches this
        channel — the steady state right after Dispatcharr creates a new
        event channel, before the profile's XMLTV covers it — defer to the
        existing Pass 5 refresh-and-retry instead of failing. Default False
        keeps the standard-rule path byte-identical (no-match = failure).
        """
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_epg",
                error="No channel to update"
            )

        epg_source_id = action.params.get("epg_id")
        if epg_source_id is None:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No epg_id specified",
                error="Missing epg_id"
            )

        # Resolve EPG source ID -> epg_data_id
        source_entries = self._epg_data_by_source.get(epg_source_id, [])
        if not source_entries:
            # For dummy EPG sources, defer instead of failing — Pass 5 will refresh and retry
            if epg_source_id in self._dummy_epg_source_ids:
                logger.info(
                    "[AUTO-CREATE-EXEC] Deferring assign_epg for dummy source %s "
                    "(channel %s) — will retry after EPG refresh",
                    epg_source_id, exec_ctx.current_channel_id
                )
                self._deferred_epg_assignments.append(
                    (exec_ctx.current_channel_id, action, stream_ctx, exec_ctx)
                )
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Deferred: will assign EPG from dummy source {epg_source_id} after refresh",
                    entity_type="channel",
                    entity_id=exec_ctx.current_channel_id,
                    deferred=True
                )
            logger.warning(
                "[AUTO-CREATE-EXEC] No EPG data entries found for source %s",
                epg_source_id
            )
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"No EPG data entries found for source {epg_source_id}",
                error=f"EPG source {epg_source_id} has no data entries"
            )

        channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
        epg_data_entry = self._match_epg_data(channel, source_entries)

        if not epg_data_entry:
            channel_name = channel.get("name", "unknown")
            if defer_on_no_match and epg_source_id in self._dummy_epg_source_ids:
                # ti939.3.3: same deferral as the empty-source branch above —
                # Pass 5 regenerates the profile's XMLTV (which then covers
                # this channel), refreshes the source, and retries.
                logger.info(
                    "[AUTO-CREATE-EXEC] Deferring assign_epg for dummy source "
                    "%s (channel %s '%s') — no matching entry yet; will retry "
                    "after EPG refresh",
                    epg_source_id, exec_ctx.current_channel_id, channel_name
                )
                self._deferred_epg_assignments.append(
                    (exec_ctx.current_channel_id, action, stream_ctx, exec_ctx)
                )
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Deferred: will assign EPG from dummy source {epg_source_id} after refresh",
                    entity_type="channel",
                    entity_id=exec_ctx.current_channel_id,
                    deferred=True
                )
            logger.warning("[AUTO-CREATE-EXEC] No EPG match for channel '%s' in source %s", channel_name, epg_source_id)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"No matching EPG data for '{channel_name}' in source {epg_source_id}",
                error="No EPG data match found"
            )

        epg_data_id = epg_data_entry["id"]
        set_tvg_id = action.params.get("set_tvg_id", False)
        epg_tvg_id = epg_data_entry.get("tvg_id")

        if exec_ctx.dry_run:
            desc = f"Would assign EPG data {epg_data_id} (source {epg_source_id}) to channel"
            if set_tvg_id and epg_tvg_id:
                channel["tvg_id"] = epg_tvg_id
                desc += f" and set tvg_id to '{epg_tvg_id}'"
            return ActionResult(
                success=True,
                action_type=action.type,
                description=desc,
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            previous_state = {"epg_data_id": channel.get("epg_data_id")}
            payload = {"epg_data_id": epg_data_id}
            if set_tvg_id and epg_tvg_id:
                previous_state["tvg_id"] = channel.get("tvg_id")
                payload["tvg_id"] = epg_tvg_id

            await self.client.update_channel(exec_ctx.current_channel_id, payload)
            channel.update(payload)

            # Track for post-execution verification if channel was just created
            newly_created_ids = {c["id"] for c in self._created_channels.values()}
            if exec_ctx.current_channel_id in newly_created_ids:
                self._pending_epg_verifications.append(
                    (exec_ctx.current_channel_id, payload)
                )

            logger.debug(
                "[AUTO-CREATE-EXEC] Assigned epg_data_id=%s (source=%s, "
                "tvg_id=%s) to channel %s",
                epg_data_id, epg_source_id,
                epg_data_entry.get('tvg_id'), exec_ctx.current_channel_id
            )

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned EPG data {epg_data_id} (source {epg_source_id}) to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to assign EPG: %s", e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign EPG",
                error=str(e)
            )

    async def verify_epg_assignments(self) -> tuple[int, int, int]:
        """Verify EPG assignments persisted on newly created channels.

        Dispatcharr's async stream processing (triggered by channel creation)
        can overwrite EPG assignments made immediately after creation. This
        method batches a single verification pass after all actions complete.

        Returns:
            (verified_ok, re_patched, failed) counts
        """
        if not self._pending_epg_verifications:
            return (0, 0, 0)

        import asyncio
        await asyncio.sleep(1)  # Single delay for the entire batch

        verified_ok = 0
        re_patched = 0
        failed = 0

        for channel_id, payload in self._pending_epg_verifications:
            try:
                channel = await self.client.get_channel(channel_id)
                expected_epg = payload.get("epg_data_id")
                actual_epg = channel.get("epg_data_id")

                if actual_epg == expected_epg:
                    verified_ok += 1
                    continue

                # Mismatch — re-PATCH
                logger.info(
                    "[AUTO-CREATE-EXEC] EPG verify: channel %s has epg_data_id=%s, "
                    "expected %s — re-patching",
                    channel_id, actual_epg, expected_epg
                )
                await self.client.update_channel(channel_id, payload)
                re_patched += 1
            except Exception as e:
                logger.warning(
                    "[AUTO-CREATE-EXEC] EPG verify failed for channel %s: %s",
                    channel_id, e
                )
                failed += 1

        self._pending_epg_verifications.clear()
        return (verified_ok, re_patched, failed)

    def reload_epg_data(self, epg_data: list):
        """Rebuild EPG data lookup from fresh data (called by engine in Pass 5)."""
        self._epg_data_by_source.clear()
        for entry in (epg_data or []):
            src_id = entry.get("epg_source")
            if src_id is not None:
                self._epg_data_by_source.setdefault(src_id, []).append(entry)
        logger.info(
            "[AUTO-CREATE-EXEC] Reloaded EPG data: %s sources, %s total entries",
            len(self._epg_data_by_source), len(epg_data or [])
        )

    # =========================================================================
    # EPG Matching (mirrors frontend epgMatching.ts logic)
    # =========================================================================

    # Quality/timezone suffixes stripped during normalization
    _QUALITY_SUFFIXES = ['fhd', 'uhd', '4k', 'hd', 'sd', '1080p', '1080i', '720p', '480p', '2160p', 'hevc', 'h264', 'h265']
    _TIMEZONE_SUFFIXES = ['east', 'west', 'et', 'pt', 'ct', 'mt']
    _LEAGUE_SUFFIXES = ['nfl', 'nba', 'mlb', 'nhl', 'mls', 'wnba', 'ncaa', 'cfb', 'cbb',
                        'epl', 'premierleague', 'laliga', 'bundesliga', 'seriea', 'ligue1',
                        'uefa', 'fifa', 'f1', 'nascar', 'pga', 'atp', 'wta', 'wwe', 'ufc', 'aew', 'boxing']
    _LEAGUE_PREFIXES_RE = re.compile(
        r'^(?:NFL|NBA|MLB|NHL|MLS|WNBA|NCAA|CFB|CBB|EPL|UEFA|FIFA|F1|NASCAR|PGA|ATP|WTA|WWE|UFC|AEW|BOXING)\s*[:|]\s*',
        re.IGNORECASE
    )

    @staticmethod
    def _normalize_for_epg(name: str) -> str:
        """Normalize a channel/EPG name for matching (mirrors frontend normalizeForEPGMatch)."""
        n = name.strip()
        # Strip channel number prefix: "107 | Name", "107 - Name", "107: Name"
        n = re.sub(r'^\d+(?:\.\d+)?\s*[|\-:.]\s*', '', n)
        # Strip "107 Name" (number + space + letter)
        n = re.sub(r'^\d+(?:\.\d+)?\s+(?=[A-Za-z])', '', n)
        # Strip country prefix: "US: Name", "UK | Name"
        n = re.sub(r'^[A-Z]{2}\s*[:|]\s*', '', n)
        # Strip league prefix: "NFL: Arizona Cardinals"
        n = ActionExecutor._LEAGUE_PREFIXES_RE.sub('', n)
        # Strip quality suffixes — {suffix} iterates the hardcoded
        # ActionExecutor._QUALITY_SUFFIXES tuple, not user input.
        for suffix in ActionExecutor._QUALITY_SUFFIXES:
            n = re.sub(rf'[\s\-_|:]*{suffix}\s*$', '', n, flags=re.IGNORECASE)  # nosemgrep: no-bare-re-on-dynamic-pattern
        # Strip timezone suffixes — {suffix} iterates the hardcoded
        # ActionExecutor._TIMEZONE_SUFFIXES tuple.
        for suffix in ActionExecutor._TIMEZONE_SUFFIXES:
            n = re.sub(rf'[\s\-_|:]*{suffix}\s*$', '', n, flags=re.IGNORECASE)  # nosemgrep: no-bare-re-on-dynamic-pattern
        # Convert semantic characters
        n = n.replace('+', 'plus').replace('&', 'and')
        # Lowercase alphanumeric only
        n = re.sub(r'[^a-z0-9]', '', n.lower())
        # Strip leading digits
        n = re.sub(r'^\d+', '', n)
        return n

    @staticmethod
    def _parse_tvg_id(tvg_id: str) -> str:
        """Parse tvg_id to extract the normalized base name (mirrors frontend parseTvgId)."""
        lower = tvg_id.lower()
        last_dot = lower.rfind('.')
        name_part = tvg_id

        if last_dot != -1:
            suffix = lower[last_dot + 1:]
            # Known league suffix
            if suffix in ActionExecutor._LEAGUE_SUFFIXES:
                name_part = tvg_id[:last_dot]
            # Looks like a country code (2-3 lowercase letters)
            elif 2 <= len(suffix) <= 3 and suffix.isalpha():
                name_part = tvg_id[:last_dot]

        # Strip call signs in parentheses: "AdultSwim(ADSM)" -> "AdultSwim"
        name_part = re.sub(r'\([^)]+\)', '', name_part)
        return ActionExecutor._normalize_for_epg(name_part)

    @staticmethod
    def _region_rank(channel_region: Optional[str], entry: dict) -> int:
        """Region-consistency rank for a candidate EPG entry (bead vznut.4).

        Mirrors the shipped vznut.2 semantics from epg_matching._sort_matches
        (region detection is REUSED via epg_matching.detect_region — one
        source of regional truth: West == Pacific, Mountain/Central their own
        regions, tvg_id-paren-then-last-word with quality/digit skipping):

          0 = entry's region matches the channel's (or channel has no region)
          1 = entry has no region (neutral)
          2 = conflict (channel and entry in different regions)

        Inert when ``channel_region`` is None — every candidate ranks 0, so
        non-regional channels sort exactly as before vznut.4.
        """
        if channel_region is None:
            return 0
        entry_region = detect_region(entry.get("tvg_id"), entry.get("name"))
        if entry_region == channel_region:
            return 0
        if entry_region is None:
            return 1
        return 2

    def _match_epg_data(self, channel: dict, source_entries: list[dict]) -> Optional[dict]:
        """
        Find the best EPG data entry for a channel from a list of source entries.
        Mirrors the frontend's "Accept Best Guesses" matching logic.

        Match priority:
        1. Exact tvg_id match (channel.tvg_id == entry.tvg_id)
        2. Exact normalized name match (channel name == entry tvg_id or name)
        3. Prefix match (channel name starts with entry name or vice versa)
        4. Fallback: first entry (for single-entry sources like dummy EPGs)

        Within tiers 2 and 3, candidates sort by region consistency first
        (bead vznut.4, mirroring vznut.2: a "...West" channel prefers the
        "(Pacific)" guide row over the East/base default), then by name-length
        similarity. Region goes AHEAD of the length tie-break because length
        similarity is a coincidental signal (the main matcher dropped it
        entirely in m4hp1) while the region tag is deliberate — a wrong-region
        row winning on character count is exactly the KNM failure mode. The
        preference is tier-preserving: a region-matched prefix candidate never
        outranks an exact candidate, and it is inert for channels with no
        detected region.
        """
        channel_tvg_id = channel.get("tvg_id")
        channel_name = channel.get("name", "")

        # 1. Exact tvg_id match
        if channel_tvg_id:
            for entry in source_entries:
                if entry.get("tvg_id") == channel_tvg_id:
                    logger.debug("[AUTO-CREATE-EXEC] Exact tvg_id match: %s", channel_tvg_id)
                    return entry

        # Normalize channel name
        norm_channel = self._normalize_for_epg(channel_name)
        if not norm_channel:
            # Can't match by name, use fallback
            if len(source_entries) == 1:
                logger.debug("[AUTO-CREATE-EXEC] Single entry fallback for '%s'", channel_name)
                return source_entries[0]
            return None

        # Region of the channel itself (vznut.4). Detected once, then each
        # candidate gets a consistency rank via _region_rank.
        channel_region = detect_region(channel_tvg_id, channel_name)

        # Build lookup from source entries.
        # Candidate tuples: (entry, normalized_key, region_rank, len_diff).
        exact_matches = []
        prefix_matches = []

        for entry in source_entries:
            entry_tvg_id = entry.get("tvg_id") or ""
            entry_name = entry.get("name") or ""
            region_rank = self._region_rank(channel_region, entry)

            # Normalize tvg_id and name
            norm_tvg = self._parse_tvg_id(entry_tvg_id) if entry_tvg_id else ""
            norm_name = self._normalize_for_epg(entry_name) if entry_name else ""

            # 2. Exact normalized match
            if norm_channel == norm_tvg or norm_channel == norm_name:
                exact_matches.append((entry, norm_tvg, region_rank,
                                      abs(len(norm_tvg) - len(norm_channel))))
                continue

            # Also check call sign in parentheses: "CartoonNetwork(STOONHD).us"
            call_sign_match = re.search(r'\(([^)]+)\)', entry_tvg_id)
            if call_sign_match:
                call_sign = re.sub(r'[^a-z0-9]', '', call_sign_match.group(1).lower())
                # Strip HD/SD suffix from call sign
                call_sign_base = re.sub(r'(hd|sd|fhd|uhd)$', '', call_sign)
                if norm_channel == call_sign or norm_channel == call_sign_base:
                    exact_matches.append((entry, norm_tvg, region_rank, 0))
                    continue

            # 3. Prefix match (at least 4 chars to avoid false positives)
            if len(norm_channel) >= 4 and norm_tvg:
                if norm_tvg.startswith(norm_channel) or norm_channel.startswith(norm_tvg):
                    len_diff = abs(len(norm_tvg) - len(norm_channel))
                    prefix_matches.append((entry, norm_tvg, region_rank, len_diff))
            if len(norm_channel) >= 4 and norm_name:
                if norm_name.startswith(norm_channel) or norm_channel.startswith(norm_name):
                    len_diff = abs(len(norm_name) - len(norm_channel))
                    # Avoid duplicates
                    if not any(e[0]["id"] == entry["id"] for e in prefix_matches):
                        prefix_matches.append((entry, norm_name, region_rank, len_diff))

        # Pick best match: exact > prefix; within a tier sort by region
        # consistency (vznut.4), then name length similarity.
        if exact_matches:
            exact_matches.sort(key=lambda x: (x[2], x[3]))
            best = exact_matches[0][0]
            logger.debug(
                "[AUTO-CREATE-EXEC] Exact name match: '%s' -> "
                "'%s' (tvg_id=%s)",
                channel_name, best.get('name'), best.get('tvg_id')
            )
            return best

        if prefix_matches:
            prefix_matches.sort(key=lambda x: (x[2], x[3]))
            best = prefix_matches[0][0]
            logger.debug(
                "[AUTO-CREATE-EXEC] Prefix match: '%s' -> "
                "'%s' (tvg_id=%s)",
                channel_name, best.get('name'), best.get('tvg_id')
            )
            return best

        # 4. Fallback for single-entry sources (dummy EPGs)
        if len(source_entries) == 1:
            logger.debug("[AUTO-CREATE-EXEC] Single entry fallback for '%s'", channel_name)
            return source_entries[0]

        return None

    async def _execute_assign_profile(self, action: Action, stream_ctx: StreamContext,
                                       exec_ctx: ExecutionContext) -> ActionResult:
        """Execute assign_profile action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_profile",
                error="No channel to update"
            )

        profile_id = action.params.get("profile_id")
        if not profile_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No profile_id specified",
                error="Missing profile_id"
            )

        if exec_ctx.dry_run:
            # Update simulated state so subsequent actions in this dry run
            # preview against the new profile (mirrors the real-run path).
            simulated = self._channel_by_id.get(exec_ctx.current_channel_id)
            if simulated is not None:
                simulated["stream_profile_id"] = profile_id
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign stream profile {profile_id}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"stream_profile_id": channel.get("stream_profile_id")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"stream_profile_id": profile_id})
            channel["stream_profile_id"] = profile_id

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned stream profile {profile_id} to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign profile",
                error=str(e)
            )

    async def _resolve_channel_effective_group(self, channel_id, exec_ctx):
        """Resolve a channel's EFFECTIVE group id for the shared reconcile lock
        (Finding 4). Fetches ``get_all_m3u_group_settings`` ONCE per pipeline run
        (cached on ``exec_ctx``) — NOT per channel — then follows any Channel
        Group Override.

        Returns ``(eff_gid, has_group)``:
        * ``(gid, True)``  — resolved; acquire the shared lock.
        * ``(None, True)`` — the channel HAS a group a reconcile could contend
          on, but the lock key could NOT be established (settings fetch failed /
          resolution error). The caller must FAIL CLOSED (Finding 2) — do NOT
          write unlocked.
        * ``(None, False)`` — the channel has NO group; nothing can contend, so
          the caller may proceed unlocked.
        """
        if not exec_ctx.group_settings_fetched:
            exec_ctx.group_settings_fetched = True
            try:
                exec_ctx.group_settings_cache = await self.client.get_all_m3u_group_settings()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[CHANNEL-PIPELINE-EXEC] could not fetch group settings for the "
                    "assign_channel_profile lock: %s", e,
                )
                exec_ctx.group_settings_cache = None
        channel = self._channel_by_id.get(channel_id) or {}
        gid = channel.get("channel_group")
        if gid is None:
            gid = channel.get("channel_group_id")
        has_group = gid is not None
        all_settings = exec_ctx.group_settings_cache
        if not isinstance(all_settings, dict) or not all_settings:
            # Settings unavailable: if the channel has a group, the lock key is
            # unresolvable -> fail closed; if group-less, nothing to serialize.
            return None, has_group
        if not has_group:
            return None, False
        from services.event_sync_preflight import resolve_effective_master_group_id
        try:
            return resolve_effective_master_group_id(all_settings, gid), True
        except Exception:  # noqa: BLE001 - resolution error -> fail closed
            return None, True

    async def _execute_assign_channel_profile(self, action: Action, stream_ctx: StreamContext,
                                               exec_ctx: ExecutionContext,
                                               rule_id: Optional[int] = None) -> ActionResult:
        """Execute assign_channel_profile action.

        ``rule_id`` (GH #720 Part B handoff): the id of the firing
        assign_channel_profile rule, stamped into the channel's ownership
        marker so the group reconcile can RELEASE the channel once that rule is
        disabled/deleted/no-longer-assigns. None outside a rule run.
        """
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_channel_profile",
                error="No channel to update"
            )

        channel_profile_ids = action.params.get("channel_profile_ids", [])
        if not channel_profile_ids:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel_profile_ids specified",
                error="Missing channel_profile_ids"
            )

        # Enforcing exclusive membership requires a KNOWN profile universe — we
        # must know which profiles to DISABLE. ``None`` means the universe is
        # UNAVAILABLE (the engine's get_channel_profiles() fetch raised), so
        # exclusivity is unprovable: FAIL rather than silently enable-only-and-
        # report-success, which would recreate GH #720 during a transient read
        # failure (y3m6o.1 Bug 2). A genuinely-empty universe (``[]``) is a
        # real, known fact and degrades to enable-selected-only as before.
        #
        # y3m6o.1 Bug 2 (0152): this check runs BEFORE the dry-run preview so a
        # DRY RUN whose universe is unavailable returns an explicit blocking
        # outcome instead of a rosy "Would assign N…" that the live run cannot
        # honor. The engine attempts the profile fetch during dry-run too, so
        # the executor knows the universe availability at preview time.
        if self._all_profile_ids is None:
            logger.warning(
                "[AUTO-CREATE-EXEC] Cannot assign channel profiles for channel %s: "
                "profile universe unavailable",
                exec_ctx.current_channel_id,
            )
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Cannot assign channel profiles: profile universe unavailable",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                # No writes happen (dry-run or live), so nothing was modified —
                # do NOT let add_result fabricate an update count / rollback
                # entry for a no-op.
                modified=False,
                error=(
                    "Channel profile universe unavailable (fetch failed); exclusive "
                    "membership cannot be enforced. Retry once profiles are reachable."
                ),
            )

        if exec_ctx.dry_run:
            # y3m6o.1 review follow-up: preview the ACTUAL flips (read-only) so a
            # dry run of an already-correct channel reports modified=False rather
            # than a phantom "Would assign N" — matching the live no-op path.
            #
            # y3m6o.2: assign_channel_profile is EXCLUSIVE (subtractive) — it
            # enables the selected profiles AND removes the channel from every
            # OTHER profile. The prior "enable X, disable Y" counts hid that
            # destructive complement (Y was only the profiles that happened to
            # need a flip THIS run — 0 when the channel was already exclusive),
            # so an operator previewing a channel already out of the other
            # profiles saw "disable 0" and never learned the semantic. State the
            # subtractive contract explicitly, naming the selected set and the
            # count of other profiles the channel is (or will be) removed from.
            # Read-only: _profile_flip_plan and the universe read never mutate.
            enable_pids, disable_pids = self._profile_flip_plan(
                exec_ctx.current_channel_id, channel_profile_ids
            )
            n_flips = len(enable_pids) + len(disable_pids)
            selected_set = set(channel_profile_ids)
            universe = set(self._all_profile_ids or [])
            other_count = len(universe - selected_set)
            selected_sorted = sorted(selected_set)
            exclusive_clause = (
                f"enable in {len(selected_set)} selected profile(s) "
                f"{selected_sorted} and REMOVE from all {other_count} other "
                f"channel profile(s)"
            )
            return ActionResult(
                success=True,
                action_type=action.type,
                description=(
                    f"Would enforce EXCLUSIVE channel-profile membership: "
                    f"{exclusive_clause} (this run flips {n_flips}: enable "
                    f"{len(enable_pids)}, disable {len(disable_pids)})"
                    if n_flips
                    else (
                        f"Channel already has EXCLUSIVE membership in exactly "
                        f"the {len(selected_set)} selected profile(s) "
                        f"{selected_sorted} and no others — no change "
                        f"(would still enforce removal from all {other_count} "
                        f"other channel profile(s))"
                    )
                ),
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=bool(n_flips),
            )

        # Finding 4 (close B2, SAME-PROCESS): acquire the SAME per-effective-group
        # lock the reconcile uses around the (marker-stamp + membership-write)
        # unit, so a pipeline assign_channel_profile and a group reconcile do not
        # touch this effective group's channels concurrently WITHIN THIS PROCESS.
        # (Cross-process contention — the HTTPS subprocess serves requests too —
        # is out of scope at this tier; see bead nq3ed.) The effective group is
        # resolved from group settings fetched ONCE per run (cached on exec_ctx —
        # see _resolve_channel_effective_group), NOT per channel. Acquire/release
        # is per-channel with no nested holds: the executor is never invoked from
        # within a reconcile (reconcile only calls the Dispatcharr client), so
        # there is no reconcile->executor->reconcile re-entrancy and no deadlock.
        # When the group can't be resolved (settings fetch failed / channel has
        # no group), skip the lock and rely on the pre-write re-check +
        # marker-before-membership ordering (defense-in-depth, still airtight in
        # combination for the resolvable case).
        from services.profile_reconcile import effective_group_lock
        eff_gid, has_group = await self._resolve_channel_effective_group(
            exec_ctx.current_channel_id, exec_ctx
        )
        if has_group and eff_gid is None:
            # Finding 2 (FAIL CLOSED): the channel has a group a reconcile could
            # contend on, but we could NOT establish the shared lock key (group-
            # settings fetch failed / resolution error). Writing unlocked risks a
            # clobber, so SKIP the write and report a retryable failure — nothing
            # was applied. (Cross-process contention is out of scope at this tier
            # — see bead nq3ed.)
            logger.warning(
                "[AUTO-CREATE-EXEC] assign_channel_profile for channel %s: lock "
                "key unresolvable (group settings unavailable) — failing closed, "
                "no profile write issued", exec_ctx.current_channel_id,
            )
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Channel profile assignment deferred: group lock unresolvable",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                error="group lock key unresolvable; failed closed (no write)",
            )
        # nullcontext ONLY for a genuinely group-less channel (nothing to
        # serialize against).
        lock_cm = (
            effective_group_lock(eff_gid) if eff_gid is not None
            else contextlib.nullcontext()
        )
        async with lock_cm:
            return await self._assign_channel_profile_write(
                action, exec_ctx, rule_id, channel_profile_ids
            )

    async def _assign_channel_profile_write(self, action, exec_ctx, rule_id,
                                            channel_profile_ids):
        """The marker-stamp + exclusive-membership write for one channel, run
        while holding the shared per-effective-group reconcile lock (Finding 4).
        Split out of :meth:`_execute_assign_channel_profile` so the lock wraps
        EXACTLY the write unit."""
        try:
            # GH #720 Part B (Blocker 2b — ORDERING): stamp the ownership marker
            # BEFORE the exclusive-membership write. A group reconcile that reads
            # this channel AFTER the pipeline started but BEFORE membership lands
            # must already see the marker and EXCLUDE it — otherwise the reconcile
            # could overwrite the pipeline's membership while the marker (written
            # later) then keeps subsequent sweeps preserving the wrong state.
            # Stamping first claims ownership as soon as the pipeline commits to
            # assigning. The marker write is fail-closed (Blocker 5): a failed
            # fresh-read skips the stamp and returns False — we record
            # ownership-unestablished but STILL apply membership (the profiles
            # must land), surfacing the incompleteness as a run-level warning.
            marked = await self._mark_channel_profile_ownership(
                exec_ctx.current_channel_id, rule_id
            )
            if not marked:
                # Judgment 4b: record the channel so the engine surfaces a
                # run-level WARNING (not a failed action) — the assignment below
                # still proceeds and success stays True.
                exec_ctx.profile_ownership_unestablished_channel_ids.add(
                    exec_ctx.current_channel_id
                )

            # Dispatcharr auto-joins every newly-created channel to ALL channel
            # profiles by default, so honoring a selection is SUBTRACTIVE: enable
            # the selected profiles AND disable the channel in every OTHER known
            # profile. An enable-only loop is a no-op — the channel stays in
            # every profile (GH #720 / y3m6o).
            membership = await self._apply_exclusive_profile_membership(
                exec_ctx.current_channel_id, channel_profile_ids
            )

            if membership.failed_profile_ids:
                # Best-effort continuation already attempted every profile, but
                # some PATCHes failed, so exclusive membership is UNPROVEN — the
                # channel may still be in a profile it shouldn't be. Report a
                # NON-success (still ``modified``: the successful enable/disable
                # calls did land) with the failed ids surfaced so the run is
                # observable and retryable (y3m6o.1 Bug 1). The ownership marker
                # was already stamped above (Blocker 2b) — that is correct: the
                # channel is pipeline-owned even with incomplete membership, so a
                # concurrent reconcile excludes it; the retry completes membership.
                failed_str = ", ".join(str(p) for p in membership.failed_profile_ids)
                logger.warning(
                    "[AUTO-CREATE-EXEC] Incomplete channel-profile assignment for "
                    "channel %s: failed to update profile(s) %s",
                    exec_ctx.current_channel_id, failed_str,
                )
                # y3m6o.1 Bug 3 (0152) + review follow-up: ``modified`` reflects
                # whether any real FLIP actually landed. enabled_count /
                # disabled_count now count only profiles whose state changed, so
                # a failure where no needed flip landed changed nothing and must
                # NOT be ``modified`` (no phantom update count / rollback entity);
                # a partial success (some flips landed) stays ``modified``.
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=(
                        f"Incomplete channel-profile assignment: enabled in "
                        f"{membership.enabled_count}, disabled in "
                        f"{membership.disabled_count}; failed to update "
                        f"profile(s): {failed_str}"
                    ),
                    entity_type="channel",
                    entity_id=exec_ctx.current_channel_id,
                    modified=membership.changed,
                    # Profile membership carries no reversible previous_state
                    # (Finding 6) — never recorded as a rollback entity.
                    rollbackable=False,
                    error=f"Failed to update channel profile(s): {failed_str}",
                )

            # Ownership marker was stamped BEFORE the membership write above
            # (Blocker 2b); ``marked`` reflects whether it landed.
            #
            # y3m6o.1 review follow-up: ``modified`` is True only when at least
            # one profile's enabled-state actually flipped. An idempotent
            # reconcile (channel already in exactly the selected profiles) makes
            # zero writes, reports changed=False, and is NOT counted as a channel
            # update — so re-running a rule no longer inflates channels_updated.
            base_desc = (
                f"Assigned channel profiles: enabled in "
                f"{membership.enabled_count}, disabled in "
                f"{membership.disabled_count}"
                if membership.changed
                else "Channel profiles already correct (no change)"
            )
            # Blocker 2: the profiles ARE applied (success stays True, the change
            # is counted), but if the ownership marker write failed, precedence
            # was NOT established — surface it non-fatally in the description +
            # error field so the run reflects the incompleteness.
            marker_warning = (
                None if marked
                else "ownership marker write failed; profile precedence not established"
            )
            return ActionResult(
                success=True,
                action_type=action.type,
                description=base_desc if marked else f"{base_desc} ({marker_warning})",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=membership.changed,
                # Profile membership carries no reversible previous_state
                # (Finding 6) — never recorded as a rollback entity.
                rollbackable=False,
                error=marker_warning,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign channel profile(s)",
                error=str(e)
            )

    async def apply_channel_profile_to_channels(
        self, action: Action | dict, channel_ids: list[int],
        exec_ctx: ExecutionContext, rule_id: Optional[int] = None,
    ) -> list[ActionResult]:
        """Apply an ``assign_channel_profile`` action to an EXPLICIT set of
        channel ids (the event_sync execution path — GH #720 / y3m6o.1 Finding
        4).

        ``rule_id`` (GH #720 Part B handoff) is threaded into the per-channel
        ownership marker exactly as the standard path does, so event_sync-owned
        channels are released when the owning rule is disabled/deleted.

        The standard Pass 1/2 path runs ``assign_channel_profile`` per matched
        stream against ``exec_ctx.current_channel_id``. event_sync rules do NOT
        flow through per-stream action evaluation — the dedicated attach phase
        (:meth:`execute_event_sync_rule`) resolves and attaches streams
        directly — so a configured ``assign_channel_profile`` on an event_sync
        rule never fired before. This helper closes that gap WITHOUT routing
        event_sync through the standard passes: the engine hands it the channels
        the rule touched this run (newly-attached masters + promoted channels)
        and it applies exclusive membership to each.

        It reuses :meth:`_execute_assign_channel_profile` unchanged by rebinding
        ``exec_ctx.current_channel_id`` per channel, so the truthful-status,
        exclusive-membership, universe-availability, and modified-flag semantics
        are BYTE-IDENTICAL to the standard path. Each result funnels through the
        shared ``add_result`` chokepoint exactly as the standard path's
        ``execute`` does, so channels_updated / modified_entities stay
        consistent. A dummy StreamContext is passed because
        ``_execute_assign_channel_profile`` reads only ``current_channel_id``.
        """
        if isinstance(action, dict):
            action = Action.from_dict(action)

        results: list[ActionResult] = []
        # De-dupe while preserving order — a channel touched by several attaches
        # this run gets its membership reconciled ONCE.
        ordered_ids = list(dict.fromkeys(channel_ids))
        dummy_ctx = StreamContext(
            stream_id=None,
            stream_name="[event_sync assign_channel_profile]",
            m3u_account_id=None,
        )
        saved_channel_id = exec_ctx.current_channel_id
        try:
            for channel_id in ordered_ids:
                exec_ctx.current_channel_id = channel_id
                result = await self._execute_assign_channel_profile(
                    action, dummy_ctx, exec_ctx, rule_id=rule_id
                )
                exec_ctx.add_result(result)
                results.append(result)
        finally:
            exec_ctx.current_channel_id = saved_channel_id
        return results

    async def _execute_set_channel_number(self, action: Action, stream_ctx: StreamContext,
                                           exec_ctx: ExecutionContext) -> ActionResult:
        """Execute set_channel_number action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for set_channel_number",
                error="No channel to update"
            )

        value = action.params.get("value", "auto")

        # Reuse the number if this channel was already assigned one this run
        # (avoids consuming extra numbers when multiple streams merge into same channel)
        existing_number = self._channel_assigned_numbers.get(exec_ctx.current_channel_id)
        if existing_number is not None:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Channel already numbered #{existing_number} this run",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                skipped=True
            )

        channel_number = self._get_next_channel_number(value)

        if exec_ctx.dry_run:
            self._channel_assigned_numbers[exec_ctx.current_channel_id] = channel_number
            # Update simulated state so subsequent actions in this dry run
            # preview against the new number (mirrors the real-run path).
            simulated = self._channel_by_id.get(exec_ctx.current_channel_id)
            if simulated is not None:
                simulated["channel_number"] = channel_number
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would set channel number to {channel_number}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"channel_number": channel.get("channel_number")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"channel_number": channel_number})
            channel["channel_number"] = channel_number
            self._used_channel_numbers.add(channel_number)
            self._channel_assigned_numbers[exec_ctx.current_channel_id] = channel_number

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Set channel number to {channel_number}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to set channel number",
                error=str(e)
            )

    # =========================================================================
    # Sort Group (enhancedchannelmanager-vy4fl)
    # =========================================================================

    def _execute_sort_group(
        self, action: Action, exec_ctx: ExecutionContext,
        rule_target_group_id: Optional[int],
    ) -> ActionResult:
        """Queue the resolved group for the post-run alphabetical sort pass.

        Does NOT touch any channel directly — it only records the group
        (and this action's params) into ``exec_ctx.sort_group_requests``.
        The engine aggregates these across every matched stream this run
        (``results["sort_group_requests"]``, a dict keyed by group_id, so
        N streams landing in the same group still produce ONE entry) and
        performs the actual sort+renumber once per group in Pass 3.6
        (``channel_pipeline_engine.py`` — ``_sort_channel_groups``).

        Group resolution precedence (mirrors create_channel's group_id
        fallback chain at ``_execute_create_channel``):
          1. explicit ``group_id`` action param
          2. ``exec_ctx.current_group_id`` (set by a prior create_group
             action this stream ran through)
          3. the channel_group_id of ``exec_ctx.current_channel_id``'s
             channel (set by a prior create_channel/merge_streams action)
          4. the rule's ``target_group_id``

        If a stream reaches this action with none of the above resolved
        (e.g. a rule whose ONLY action is sort_group, with no target
        group configured anywhere), the action fails loudly rather than
        silently no-op'ing.
        """
        params = action.params

        group_id = params.get("group_id")
        if group_id is None:
            group_id = exec_ctx.current_group_id
        if group_id is None and exec_ctx.current_channel_id:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            group_id = channel.get("channel_group_id")
        if group_id is None:
            group_id = rule_target_group_id

        if group_id is None:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="sort_group could not resolve a target group",
                error=(
                    "No group_id could be resolved — provide an explicit "
                    "group_id param, run this after create_channel/"
                    "create_group/merge_streams, or set the rule's target "
                    "group"
                ),
            )

        exec_ctx.sort_group_requests[group_id] = {
            "order": params.get("order", "asc"),
            "starting_number": params.get("starting_number"),
            "strip_numbers": params.get("strip_numbers", True),
            "ignore_country": params.get("ignore_country", False),
        }

        return ActionResult(
            success=True,
            action_type=action.type,
            description=f"Queued group {group_id} for alphabetical sort ({params.get('order', 'asc')})",
            entity_type="group",
            entity_id=group_id,
        )

    # =========================================================================
    # Set Variable
    # =========================================================================

    async def _execute_set_variable(self, action: Action, stream_ctx: StreamContext,
                                     exec_ctx: ExecutionContext, template_ctx: dict) -> ActionResult:
        """Execute set_variable action."""
        params = action.params
        var_name = params.get("variable_name", "")
        mode = params.get("variable_mode", "literal")
        logger.debug("[AUTO-CREATE-EXEC] var_name='%s' mode=%s params=%s", var_name, mode, params)

        # Get source value for regex modes
        source_value = ""
        if mode in ("regex_extract", "regex_replace"):
            source_field = params.get("source_field", "stream_name")
            source_value = template_ctx.get(source_field, "")
            logger.debug("[AUTO-CREATE-EXEC] source_field=%s source_value=%r", source_field, source_value)

        try:
            if mode == "regex_extract":
                pattern = params.get("pattern", "")
                # bd-eio04.15: user pattern routed through safe_regex. On
                # timeout safe_regex.search returns None; the existing None
                # arm sets result_value="" — that preserves the historical
                # "pattern did not match" contract for this action.
                match = safe_regex.search(pattern, str(source_value))
                if match is None:
                    result_value = ""
                elif match.groups():
                    result_value = match.group(1)
                else:
                    result_value = match.group(0)

            elif mode == "regex_replace":
                pattern = params.get("pattern", "")
                replacement = params.get("replacement", "")
                # Convert JS-style backreferences ($1, $2) to Python (\1, \2).
                # Hardcoded literal pattern — stays on stdlib re.
                py_replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
                # bd-eio04.15: user pattern through safe_regex.sub. Timeout
                # => source_value returned unchanged (the variable ends up
                # set to the original value), which is the least-surprising
                # fallback for a failed replace.
                result_value = safe_regex.sub(pattern, py_replacement, str(source_value))

            elif mode == "literal":
                template = params.get("template", "")
                result_value = TemplateVariables.expand_template(template, template_ctx, exec_ctx.custom_variables)

            else:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"Unknown variable mode: {mode}",
                    error=f"Unknown variable mode: {mode}"
                )

            # Store variable in execution context
            exec_ctx.custom_variables[var_name] = result_value

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Set variable '{var_name}' = '{result_value}'"
            )

        except re.error as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Regex error in set_variable: {e}",
                error=str(e)
            )

    # =========================================================================
    # Stream Management Actions
    # =========================================================================

    async def _execute_remove_from_channel(self, action: Action, stream_ctx: StreamContext,
                                            exec_ctx: ExecutionContext) -> ActionResult:
        """Execute remove_from_channel action — unassign a stream from its current channel."""
        if not stream_ctx.channel_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="Stream not assigned to any channel, skipped",
                skipped=True
            )

        channel_id = stream_ctx.channel_id
        channel = self._channel_by_id.get(channel_id)
        if not channel:
            try:
                channel = await self.client.get_channel(channel_id)
            except Exception as e:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"Failed to fetch channel {channel_id}",
                    error=str(e)
                )

        channel_name = channel.get("name", f"ID:{channel_id}")

        # Get current stream list
        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]
        if stream_ctx.stream_id not in current_streams:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Stream not in channel '{channel_name}', skipped",
                skipped=True
            )

        filtered_streams = [s for s in current_streams if s != stream_ctx.stream_id]

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would remove stream from channel '{channel_name}'",
                entity_type="stream",
                entity_id=stream_ctx.stream_id,
                entity_name=stream_ctx.stream_name,
                modified=True
            )

        try:
            previous_state = {"streams": current_streams.copy()}
            await self.client.update_channel(channel_id, {"streams": filtered_streams})
            channel["streams"] = filtered_streams

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Removed stream from channel '{channel_name}'",
                entity_type="stream",
                entity_id=stream_ctx.stream_id,
                entity_name=stream_ctx.stream_name,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to remove stream from channel '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to remove stream from channel '{channel_name}'",
                error=str(e)
            )

    async def _execute_set_stream_priority(self, action: Action, stream_ctx: StreamContext,
                                            exec_ctx: ExecutionContext) -> ActionResult:
        """Execute set_stream_priority action — move stream to lowest or highest position."""
        if not stream_ctx.channel_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="Stream not assigned to any channel, skipped",
                skipped=True
            )

        priority = action.params.get("priority", "lowest")
        channel_id = stream_ctx.channel_id
        channel = self._channel_by_id.get(channel_id)
        if not channel:
            try:
                channel = await self.client.get_channel(channel_id)
            except Exception as e:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"Failed to fetch channel {channel_id}",
                    error=str(e)
                )

        channel_name = channel.get("name", f"ID:{channel_id}")

        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]
        if stream_ctx.stream_id not in current_streams:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Stream not in channel '{channel_name}', skipped",
                skipped=True
            )

        # Build reordered list
        without = [s for s in current_streams if s != stream_ctx.stream_id]
        if priority == "highest":
            reordered = [stream_ctx.stream_id] + without
        else:
            reordered = without + [stream_ctx.stream_id]

        if reordered == current_streams:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Stream already at {priority} priority in '{channel_name}', skipped",
                skipped=True
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would move stream to {priority} priority in channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True
            )

        try:
            previous_state = {"streams": current_streams.copy()}
            await self.client.update_channel(channel_id, {"streams": reordered})
            channel["streams"] = reordered

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Moved stream to {priority} priority in channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to set stream priority in '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to set stream priority in channel '{channel_name}'",
                error=str(e)
            )

    async def prune_merge_streams(self, results: dict, dry_run: bool) -> None:
        """Apply merge_streams pruning for actions that enabled remove_non_matching.

        For each channel where any merge_streams action enabled remove_non_matching=True,
        remove any streams from that channel that were not merged into the channel
        during this rule run.
        """
        if not self._merge_prune_enabled_channels:
            return

        for channel_id in self._merge_prune_enabled_channels:
            desired = self._merge_streams_added_by_channel.get(channel_id, set())
            channel = self._channel_by_id.get(channel_id)
            if not channel:
                try:
                    channel = await self.client.get_channel(channel_id)
                except Exception as e:
                    logger.warning(
                        "[AUTO-CREATE-EXEC] Failed to fetch channel %s for merge prune: %s",
                        channel_id, e,
                    )
                    continue

            channel_name = channel.get("name", f"ID:{channel_id}")
            current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]

            # Preserve current order, prune out non-desired.
            pruned = [sid for sid in current_streams if sid in desired]
            missing = desired - set(current_streams)
            if missing:
                logger.warning(
                    "[AUTO-CREATE-EXEC] Channel '%s' merge prune: %s desired stream id(s) "
                    "not present in channel streams (will not be appended): %s",
                    channel_name, len(missing), sorted(list(missing))[:20],
                )

            if pruned == current_streams:
                continue

            removed_count = max(0, len(current_streams) - len(pruned))
            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": None,
                    "stream_name": f"[AUTO-CREATE-EXEC] {channel_name}",
                    "rule_id": None,
                    "rule_name": None,
                    "action": f"Would remove {removed_count} non-matching stream(s) from '{channel_name}'",
                    "would_create": False,
                    "would_modify": True
                })
                continue

            try:
                await self.client.update_channel(channel_id, {"streams": pruned})
                channel["streams"] = pruned
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": f"[AUTO-CREATE-EXEC] {channel_name}",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "merge_streams_prune",
                        "description": f"Removed {removed_count} non-matching stream(s) from '{channel_name}'",
                        "success": True,
                        "entity_id": channel_id,
                        "error": None
                    }]
                })
            except Exception as e:
                logger.error(
                    "[AUTO-CREATE-EXEC] Failed to prune merged streams in '%s': %s",
                    channel_name, e,
                )
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": f"[AUTO-CREATE-EXEC] {channel_name}",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "merge_streams_prune",
                        "description": f"Failed to remove non-matching streams from '{channel_name}': {e}",
                        "success": False,
                        "entity_id": channel_id,
                        "error": str(e)
                    }]
                })

    # =========================================================================
    # Reconciliation / Cleanup Methods
    # =========================================================================

    async def remove_channel(self, channel_id: int) -> ActionResult:
        """Delete an orphaned channel via the Dispatcharr API."""
        try:
            # Look up channel name for logging
            channel = self._channel_by_id.get(channel_id, {})
            channel_name = channel.get("name", f"ID:{channel_id}")

            await self.client.delete_channel(channel_id)
            logger.info("[AUTO-CREATE-EXEC] Deleted orphaned channel %s (%s)", channel_id, channel_name)

            return ActionResult(
                success=True,
                action_type="remove_channel",
                description=f"Deleted orphaned channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
            )
        except Exception as e:
            error_str = str(e)
            # Channel already gone (404) - treat as success
            if "404" in error_str or "not found" in error_str.lower():
                logger.info("[AUTO-CREATE-EXEC] Channel %s already deleted (404)", channel_id)
                return ActionResult(
                    success=True,
                    action_type="remove_channel",
                    description=f"Channel {channel_id} already deleted",
                    entity_type="channel",
                    entity_id=channel_id,
                )
            logger.error("[AUTO-CREATE-EXEC] Failed to delete channel %s: %s", channel_id, e)
            return ActionResult(
                success=False,
                action_type="remove_channel",
                description=f"Failed to delete channel {channel_id}",
                error=error_str,
            )

    async def move_channel_to_uncategorized(self, channel_id: int) -> ActionResult:
        """Move an orphaned channel to the Uncategorized group (group_id=None)."""
        try:
            channel = self._channel_by_id.get(channel_id, {})
            channel_name = channel.get("name", f"ID:{channel_id}")

            await self.client.update_channel(channel_id, {"channel_group_id": None})
            channel["channel_group_id"] = None
            logger.info("[AUTO-CREATE-EXEC] Moved orphaned channel %s (%s) to Uncategorized", channel_id, channel_name)

            return ActionResult(
                success=True,
                action_type="move_channel",
                description=f"Moved orphaned channel '{channel_name}' to Uncategorized",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
            )
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                logger.info("[AUTO-CREATE-EXEC] Channel %s already deleted (404)", channel_id)
                return ActionResult(
                    success=True,
                    action_type="move_channel",
                    description=f"Channel {channel_id} already deleted",
                    entity_type="channel",
                    entity_id=channel_id,
                )
            logger.error("[AUTO-CREATE-EXEC] Failed to move channel %s: %s", channel_id, e)
            return ActionResult(
                success=False,
                action_type="move_channel",
                description=f"Failed to move channel {channel_id}",
                error=error_str,
            )

    async def delete_group_if_empty(self, group_id: int) -> ActionResult:
        """Delete a channel group if it has no channels."""
        try:
            group = self._group_by_id.get(group_id, {})
            group_name = group.get("name", f"ID:{group_id}")

            # Fetch current channels in the group
            all_channels = []
            page = 1
            while True:
                result = await self.client.get_channels(page=page, page_size=100)
                channels = result.get("results", [])
                all_channels.extend(channels)
                if len(all_channels) >= result.get("count", 0) or not channels:
                    break
                page += 1

            channels_in_group = [c for c in all_channels if c.get("channel_group") == group_id]

            if channels_in_group:
                return ActionResult(
                    success=True,
                    action_type="delete_empty_group",
                    description=f"Group '{group_name}' still has {len(channels_in_group)} channels, kept",
                    entity_type="group",
                    entity_id=group_id,
                    entity_name=group_name,
                    skipped=True,
                )

            await self.client.delete_channel_group(group_id)
            logger.info("[AUTO-CREATE-EXEC] Deleted empty group %s (%s)", group_id, group_name)

            return ActionResult(
                success=True,
                action_type="delete_empty_group",
                description=f"Deleted empty group '{group_name}'",
                entity_type="group",
                entity_id=group_id,
                entity_name=group_name,
            )
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                return ActionResult(
                    success=True,
                    action_type="delete_empty_group",
                    description=f"Group {group_id} already deleted",
                    entity_type="group",
                    entity_id=group_id,
                )
            logger.error("[AUTO-CREATE-EXEC] Failed to delete group %s: %s", group_id, e)
            return ActionResult(
                success=False,
                action_type="delete_empty_group",
                description=f"Failed to delete group {group_id}",
                error=error_str,
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _apply_channel_number_in_name(self, channel_name: str, channel_number: int) -> str:
        """Prepend channel number to name if settings.include_channel_number_in_name is enabled."""
        if not self._settings or not getattr(self._settings, 'include_channel_number_in_name', False):
            return channel_name

        separator = getattr(self._settings, 'channel_number_separator', '-') or '-'
        number_str = str(int(channel_number) if channel_number == int(channel_number) else channel_number)

        # Strip any existing number prefix (e.g., "4000 | USA Network" -> "USA Network")
        stripped = re.sub(r'^\d+(?:\.\d+)?\s*[|\-:]\s*', '', channel_name).strip()
        if not stripped:
            stripped = channel_name

        result = f"{number_str} {separator} {stripped}"
        if result != channel_name:
            logger.debug("[AUTO-CREATE-EXEC] '%s' -> '%s'", channel_name, result)
        return result

    async def _apply_exclusive_profile_membership(
        self, channel_id: int, selected_ids: list[int]
    ) -> ProfileMembershipResult:
        """Make ``channel_id`` a member of exactly ``selected_ids``.

        Dispatcharr auto-joins every newly-created channel to ALL channel
        profiles, so honoring a profile selection is SUBTRACTIVE: for each
        profile id in the FULL known universe, enable it if selected and
        disable it otherwise.

        Returns a :class:`ProfileMembershipResult` carrying the SUCCEEDED
        enable/disable counts AND ``failed_profile_ids`` — the ids of any
        profile whose update raised. Callers inspect ``failed_profile_ids`` to
        detect incomplete exclusivity: a per-profile failure is logged and
        SKIPPED (best-effort continuation — one bad update must not abort the
        rest) but is NOT swallowed, so the caller can report a non-success and
        the incomplete reconciliation stays observable/retryable (GH #720 /
        y3m6o.1).

        The iteration order is the de-duplicated union of the known profile
        universe and the selection, so selected profiles are always enabled —
        even if the fetched profile list is stale and missing one — while every
        OTHER known profile is disabled.

        **Diff-only writes (y3m6o.1 review follow-up).** Only the profiles whose
        enabled-state actually FLIPS are PATCHed: current membership (from the
        run-start ``get_channel_profiles()`` snapshot, plus the verified
        Dispatcharr rule that a channel created THIS run is auto-joined to ALL
        universe profiles) is diffed against the desired exclusive set, and a
        profile already in the desired state is skipped. A fully-idempotent
        reconcile therefore performs ZERO writes and returns ``changed=False``,
        so re-running a rule no longer inflates ``channels_updated``. The
        in-memory membership map is updated after each successful flip so a
        second reconcile of the same channel this run sees fresh state.

        A ``None`` ``self._all_profile_ids`` (universe unavailable) is coerced
        to an empty universe HERE only defensively: callers that require a
        known universe to prove exclusivity (``_execute_assign_channel_profile``)
        gate on availability BEFORE calling this helper, and
        ``_assign_default_profiles`` returns early on a falsy universe — so this
        helper is never reached with ``None`` in practice.
        """
        enable_pids, disable_pids = self._profile_flip_plan(channel_id, selected_ids)

        # enabled_count / disabled_count count SUCCEEDED FLIPS only — a profile
        # already in the desired state is neither PATCHed nor counted (so a no-op
        # reconcile reports changed=False). failed_profile_ids captures the ids
        # of NEEDED flips whose PATCH raised.
        enabled_count = 0
        disabled_count = 0
        failed_profile_ids: list[int] = []
        current = self._current_profile_membership(channel_id)
        for pid, enable in [(p, True) for p in enable_pids] + [(p, False) for p in disable_pids]:
            try:
                await self.client.update_profile_channel(pid, channel_id, {"enabled": enable})
                if enable:
                    enabled_count += 1
                    current.add(pid)
                else:
                    disabled_count += 1
                    current.discard(pid)
            except Exception as e:
                logger.warning(
                    "[AUTO-CREATE-EXEC] Failed to update profile %s for channel %s: %s",
                    pid, channel_id, e,
                )
                failed_profile_ids.append(pid)

        # Persist the post-reconcile membership so a later same-run reconcile of
        # this channel diffs against fresh state (idempotent within the run).
        # Only the flips that SUCCEEDED are reflected (a failed flip left the
        # profile in its prior state); ``current`` was mutated accordingly above.
        # Skipped when membership info is absent (write-all mode).
        if self._channel_profile_membership is not None:
            self._channel_profile_membership[channel_id] = current
            self._run_start_channel_ids.add(channel_id)

        return ProfileMembershipResult(enabled_count, disabled_count, failed_profile_ids)

    async def _mark_channel_profile_ownership(self, channel_id: int,
                                              rule_id: Optional[int] = None) -> bool:
        """Stamp the pipeline-ownership provenance marker on a channel.

        GH #720 Part B (decision 2b + handoff): records — in the channel's
        Dispatcharr ``custom_properties`` — that this channel's profile
        membership was set by a pipeline ``assign_channel_profile`` rule
        (standard OR event_sync path — both funnel through
        ``_execute_assign_channel_profile``), so ``services.profile_reconcile``
        excludes it from group auto-sync reconciliation (pipeline action >
        group selection). Two keys are written: the owner marker
        (``ecm_profile_owner="pipeline"``) AND the OWNING RULE ID
        (``ecm_profile_owner_rule_id``) — the reconcile revalidates the rule id
        against the live rule set so ownership is RELEASED once the rule is
        disabled/deleted (automatic handoff).

        Blocker 2 (clobber): Dispatcharr's channel PATCH replaces
        custom_properties WHOLESALE. We FRESH-FETCH the channel's current
        custom_properties immediately before the merge (rather than trusting the
        run-start cache, which can be seconds stale) so a concurrent
        EPG/logo/metadata write is not erased. Idempotent: skipped without a
        write when the SAME owner AND rule id are already present.

        Returns ``True`` if the marker is present after this call (written or
        already-present), ``False`` if the write FAILED — the caller surfaces
        that as a non-fatal partial so ownership precedence being unestablished
        is visible (the profile assignment itself already succeeded).
        """
        from services.profile_reconcile import (
            PIPELINE_OWNERSHIP_MARKER_KEY,
            PIPELINE_OWNERSHIP_MARKER_VALUE,
            PIPELINE_OWNERSHIP_RULE_ID_KEY,
        )

        cached = self._channel_by_id.get(channel_id)
        cached_cp = cached.get("custom_properties") if isinstance(cached, dict) else None
        cached_cp = cached_cp if isinstance(cached_cp, dict) else {}

        # FAST idempotent-skip on the run cache FIRST (Should-Fix 4): a rule
        # re-run over already-marked channels must NOT issue a GET per channel.
        # The fresh-fetch (below) only matters when a WRITE is actually needed,
        # so gating it behind this check preserves clobber-safety at zero extra
        # GETs on the common no-op path.
        # F2 (accepted staleness): the cache is the run-start snapshot, so if the
        # marker was removed EXTERNALLY mid-run this fast path would skip re-
        # stamping it this run. Accepted for a single-operator tool — the next
        # pipeline run reads a fresh snapshot and re-stamps; not worth a per-
        # channel GET on the hot no-op path.
        if (cached_cp.get(PIPELINE_OWNERSHIP_MARKER_KEY) == PIPELINE_OWNERSHIP_MARKER_VALUE
                and cached_cp.get(PIPELINE_OWNERSHIP_RULE_ID_KEY) == rule_id):
            return True  # Already marked by this rule per the cache — skip.

        # A write is needed — fresh-fetch current custom_properties right before
        # the merge (clobber-safety). Blocker 5: if the fresh read FAILS, do NOT
        # write from the stale cache (a wholesale PATCH from stale data would
        # re-introduce lost-update during dependency degradation). SKIP the
        # marker mutation entirely and report ownership-unestablished — the next
        # run retries.
        try:
            fresh = await self.client.get_channel(channel_id)
        except Exception as e:  # noqa: BLE001 - fail closed (no write from stale)
            logger.warning(
                "[CHANNEL-PIPELINE-EXEC] channel %s: fresh custom_properties read "
                "FAILED (%s) — SKIPPING the ownership marker write rather than "
                "writing from stale cache; precedence not established this run",
                channel_id, e,
            )
            return False
        current_cp = fresh.get("custom_properties") if isinstance(fresh, dict) else None
        if not isinstance(current_cp, dict):
            current_cp = {}

        merged_cp = dict(current_cp)
        already_owner = (
            merged_cp.get(PIPELINE_OWNERSHIP_MARKER_KEY) == PIPELINE_OWNERSHIP_MARKER_VALUE
        )
        same_rule = merged_cp.get(PIPELINE_OWNERSHIP_RULE_ID_KEY) == rule_id
        if already_owner and same_rule:
            # Fresh state shows another run already marked it — no write; sync
            # the cache so later actions see the marker.
            if isinstance(cached, dict):
                cached["custom_properties"] = merged_cp
            return True
        merged_cp[PIPELINE_OWNERSHIP_MARKER_KEY] = PIPELINE_OWNERSHIP_MARKER_VALUE
        # Stamp the owning rule id so the reconcile can hand ownership back when
        # the rule goes away. None only outside a rule run (shouldn't happen for
        # a real assign) — leave the key absent so the reconcile logs the
        # legacy/conservative case rather than storing a null id.
        if rule_id is not None:
            merged_cp[PIPELINE_OWNERSHIP_RULE_ID_KEY] = rule_id
        try:
            await self.client.update_channel(
                channel_id, {"custom_properties": merged_cp}
            )
        except Exception as e:
            # The profile membership DID land, but the ownership marker did NOT —
            # precedence is NOT established. Surface it loudly AND to the caller
            # (returns False) so the run reflects the incompleteness rather than
            # a silent clean success.
            logger.warning(
                "[CHANNEL-PIPELINE-EXEC] Profile assignment for channel %s "
                "SUCCEEDED but the pipeline-ownership marker write FAILED (%s) — "
                "precedence is NOT established; a group Auto-Sync selection may "
                "move this channel until the next pipeline run re-stamps it",
                channel_id, e,
            )
            return False
        # Keep the in-run cache consistent so a later action in the same run
        # sees the marker.
        if isinstance(cached, dict):
            cached["custom_properties"] = merged_cp
        return True

    def _current_profile_membership(self, channel_id: int) -> set[int]:
        """The set of profile ids ``channel_id`` is CURRENTLY enabled in.

        * Known from the run-start snapshot -> that set.
        * Existed at run start but absent from the snapshot -> enabled nowhere
          (``∅``).
        * Created THIS run (not a run-start channel) -> Dispatcharr auto-joins
          new channels to ALL universe profiles (verified live), so the current
          membership is the full universe.

        Only meaningful when membership info is present; returns ``∅`` otherwise
        (write-all mode does not consult current membership).
        """
        if self._channel_profile_membership is None:
            return set()
        if channel_id in self._channel_profile_membership:
            return set(self._channel_profile_membership[channel_id])
        if channel_id in self._run_start_channel_ids:
            return set()
        # Created this run — auto-joined to every universe profile.
        return set(self._all_profile_ids or [])

    def _profile_flip_plan(
        self, channel_id: int, selected_ids: list[int]
    ) -> tuple[list[int], list[int]]:
        """Compute (enable_pids, disable_pids) — the profiles whose enabled-state
        must FLIP to make ``channel_id`` a member of exactly ``selected_ids``,
        given its current membership. Read-only (no writes), so it is shared by
        the live PATCH path and the dry-run preview. No-op profiles (already in
        the desired state) are excluded from both lists.

        Without membership info (``_channel_profile_membership is None``) it
        cannot diff, so it falls back to write-all: enable every selected profile
        and disable every other known profile (the proven pre-optimization
        behavior — preserved for direct-construct callers/tests).
        """
        universe = self._all_profile_ids if self._all_profile_ids is not None else []
        selected = set(selected_ids)
        ordered_ids = list(dict.fromkeys(list(universe) + list(selected_ids)))
        if self._channel_profile_membership is None:
            enable_pids = [p for p in ordered_ids if p in selected]
            disable_pids = [p for p in ordered_ids if p not in selected]
            return enable_pids, disable_pids
        current = self._current_profile_membership(channel_id)
        enable_pids = []
        disable_pids = []
        for pid in ordered_ids:
            desired = pid in selected
            now = pid in current
            if desired == now:
                continue  # already correct — skip (no write, no inflation)
            (enable_pids if desired else disable_pids).append(pid)
        return enable_pids, disable_pids

    async def _assign_default_profiles(
        self, channel_id: int, exec_ctx: "ExecutionContext | None" = None,
    ) -> str:
        """Assign default channel profiles to a newly created channel.

        Enables channel in default profiles, disables in non-default profiles.
        Returns a description string for logging, or empty string if no profiles configured.

        ``exec_ctx`` (y3m6o.1 review — Finding 1 reversal): when supplied, a
        partial/total default-profile write failure is recorded on the context
        (``default_profile_failures``) so the engine can escalate it into the
        run-level failure aggregation. Optional so direct-construct callers/tests
        that never pass a context keep the log-only behavior.
        """
        if not self._settings or not self._settings.default_channel_profile_ids:
            return ""
        # Default-profile assignment is a best-effort ENHANCEMENT, not the
        # user's explicit rule action — so a falsy universe stays a benign
        # no-op here, never a hard failure. This deliberately treats BOTH the
        # unavailable sentinel (``None``) and a genuinely-empty universe
        # (``[]``) the same way: absent a known universe there is nothing to
        # reconcile against, and silently skipping a best-effort default is
        # correct (unlike the explicit assign_channel_profile action, which
        # FAILS on an unavailable universe — GH #720 / y3m6o.1).
        if not self._all_profile_ids:
            return ""

        # Intended behavior via the shared helper: a configured default id that
        # is absent from the fetched profile universe is now (re)enabled rather
        # than silently skipped (the pre-refactor loop only iterated the fetched
        # universe). This is benign — arguably more correct, since an operator's
        # explicit default should be honored even if the universe fetch is
        # stale — and matches the union semantics used for per-rule selections.
        membership = await self._apply_exclusive_profile_membership(
            channel_id, list(self._settings.default_channel_profile_ids)
        )

        # y3m6o.1 review (Finding 1 reversal, was Finding 5 in 0152):
        # default-profile assignment stays best-effort for the CREATE (a failed
        # PATCH never aborts channel creation), but the prior log-only behavior
        # is REVERSED — a partial/total failure now ESCALATES through the run's
        # failure aggregation (via exec_ctx.default_profile_failures) so a run
        # that could not enforce a new channel's configured default membership
        # finalizes completed_with_errors, not green. Still logged for the ops
        # trail; the return description stays unchanged.
        if membership.failed_profile_ids:
            failed_str = ", ".join(
                str(p) for p in membership.failed_profile_ids
            )
            logger.warning(
                "[AUTO-CREATE-EXEC] Channel %s: default-profile assignment "
                "incomplete — enabled in %s, disabled in %s; failed to update "
                "profile(s): %s (channel creation unaffected; escalated to the "
                "run's failed-action aggregation)",
                channel_id, membership.enabled_count,
                membership.disabled_count, failed_str,
            )
            if exec_ctx is not None:
                exec_ctx.default_profile_failures.append({
                    "channel_id": channel_id,
                    "failed_profile_ids": list(membership.failed_profile_ids),
                })

        if membership.enabled_count or membership.disabled_count:
            desc = (
                f"profiles: enabled in {membership.enabled_count}, "
                f"disabled in {membership.disabled_count}"
            )
            logger.info("[AUTO-CREATE-EXEC] Channel %s: %s", channel_id, desc)
            return desc
        return ""

    def _resolve_scored_fuzzy(self, stream_ctx, min_score: float,
                              allowed_groups: list[int], allow_no_callsign: bool,
                              tie_break: str,
                              rule_id: Optional[int] = None) -> tuple[Optional[dict], Optional[dict]]:
        """Resolve a merge target via the unified scoring core (jnzst, Component A).

        Scores the stream against every channel in the allowlisted target
        groups using ``score_one`` (M1 callsign hard-reject → tvg_id override →
        LOCALS fuzzy). Returns ``(channel, provenance)`` for the best admitted
        candidate, or ``(None, None)`` when none clears the policy.

        Admission is delegated to ``services.dedup_matcher.is_admissible`` —
        the ONE shared admission policy (FIX 2). The preview endpoint and (via
        it) the MCP write tools call the SAME helper, so the rule path and
        every other consumer cannot drift:

        * ``conflict`` verdict (M1 fired)          → never admitted.
        * ``absent`` verdict (missing callsign)    → admitted ONLY if
          ``allow_no_callsign`` and score ≥ NO_CALLSIGN_FLOOR (0.90). Default
          policy REQUIRES a callsign on both sides (Q1).
        * ``match`` verdict                        → admitted at score ≥
          ``min_score`` (which the schema floored at CONFIDENCE_FLOOR).

        ``allowed_groups`` is the same allowlist the post-resolution scope
        reject re-checks; scoping the candidate pool here makes the resolution
        itself group-bounded (Q2) rather than relying on a downstream reject.

        ``rule_id`` is the firing rule's id (threaded from the engine via
        ``execute``) so the journal provenance is complete (M7, FIX 4). ``None``
        when called outside a rule run (e.g. a direct test harness).
        """
        allowed = set(allowed_groups or [])

        stream_name = stream_ctx.stream_name
        stream_tvg = getattr(stream_ctx, "tvg_id", None)

        best_channel = None
        best_match = None
        for ch in self.existing_channels:
            if ch.get("channel_group_id") not in allowed:
                continue
            cname = ch.get("name")
            if not cname:
                continue
            sm = score_one(
                stream_name,
                cname,
                stream_tvg_id=stream_tvg,
                candidate_tvg_id=ch.get("tvg_id"),
                mode=NameCleanMode.LOCALS,
            )
            # Shared admission policy (M1 conflict reject + Q1 no-callsign gate
            # + min_score) — the single chokepoint every consumer shares.
            if not is_admissible(
                sm, min_score=min_score, allow_no_callsign=allow_no_callsign
            ):
                continue
            # Admitted candidate — keep the best per tie_break.
            if best_match is None:
                best_channel, best_match = ch, sm
            elif sm.score > best_match.score:
                best_channel, best_match = ch, sm
            elif sm.score == best_match.score and tie_break == "lowest_id" \
                    and str(ch.get("id")) < str(best_channel.get("id")):
                best_channel, best_match = ch, sm

        if best_channel is None:
            return None, None

        provenance = {
            "score": round(best_match.score, 4),
            "effective_threshold": min_score,
            "signal": best_match.signal,
            "callsign_verdict": best_match.callsign_verdict,
            "stream_callsign": best_match.stream_callsign,
            "candidate_callsign": best_match.candidate_callsign,
            "tvg_id_override": best_match.tvg_id_override,
            "rule_id": rule_id,
            "allowlist_groups": list(allowed_groups or []),
        }
        logger.info(
            "[AUTO-CREATE-EXEC] Scored-fuzzy matched stream '%s' -> channel "
            "'%s' (id=%s) score=%.3f signal=%s",
            stream_name, best_channel.get("name"), best_channel.get("id"),
            best_match.score, best_match.signal,
        )
        return best_channel, provenance

    def _resolve_event_sync(self, config: dict, secondary_streams: list,
                            now=None, decisions=None,
                            effective_master_group_id=None,
                            master_name_to_id=None,
                            exclusions=None) -> tuple:
        """Event-mode candidate resolution (ti939.2.1) — SIBLING of
        :meth:`_resolve_scored_fuzzy`.

        Resolves every secondary stream against the MASTER group's channels
        through ``services.event_sync_resolver.resolve_event_sync`` — the
        EXACT function the preview endpoint calls, so preview decisions and
        run decisions cannot diverge on identical inputs (dry-run parity by
        construction). This method adds NO policy of its own: it only builds
        the master name universe and the name → channel-id map.

        Candidates are exclusively the master group's channels (tighter
        scoping than the scored-fuzzy allowlist — the mandatory-scoping
        rail). Master channels come from ``self.existing_channels`` — loaded
        ONCE per run by the engine and indexed in ``self._channel_by_id`` —
        so there is no per-stream (or even per-rule) refetch. Duplicate
        master names map to the LOWEST channel id, mirroring the preview
        endpoint exactly (stateless recompute: ids are re-resolved from the
        in-run channel list every run, never persisted).

        Args:
            config: A VALIDATED event_sync_config.
            secondary_streams: list[services.event_sync_resolver.SecondaryStream].
            now: Optional tz-aware anchor threaded to the resolver (tests).
            decisions: Optional ``services.event_sync_review.ReviewDecisions``
                (bead ti939.3.2) — prior operator accepts/rejects for THIS
                rule, applied INSIDE the shared resolver so run and preview
                classification cannot diverge.
            exclusions: Optional frozenset of operator never-attach
                fingerprints for THIS rule (bead ti939.3.5), applied INSIDE
                the shared resolver BEFORE the attach band is honored —
                same parity argument as decisions.

        Returns:
            ``(resolution, name_to_id, master_channel_count)`` where
            ``resolution`` is the EventSyncResolution and ``name_to_id`` maps
            master channel name -> current channel id.
        """
        # Lazy import: keeps the heavy matcher stack (rapidfuzz, pytz,
        # dummy-EPG engine) off this module's import path — same pattern as
        # channel_pipeline_schema's lazy matcher imports.
        from services.event_sync_resolver import resolve_event_sync

        master_group_id = config["master_group_id"]
        # Follow a Channel Group Override: master channels live in the
        # override TARGET group, not the configured source group (bead
        # override). Falls back to the configured id when unset.
        channel_group_id = (
            effective_master_group_id
            if effective_master_group_id is not None
            else master_group_id
        )
        channel_name_to_id: dict[str, int] = {}
        master_channel_count = 0
        # bead 6xxmp: stream ids already on a master channel — the resolver
        # drops these when the master group is a self-attach source so the
        # auto-synced provider's own streams are never re-offered.
        attached_stream_ids: set[int] = set()
        for ch in self.existing_channels:
            if ch.get("channel_group_id") != channel_group_id:
                continue
            master_channel_count += 1
            for s in ch.get("streams", []):
                sid = s["id"] if isinstance(s, dict) else s
                if sid is not None:
                    attached_stream_ids.add(sid)
            name, cid = ch.get("name"), ch.get("id")
            if not name or cid is None:
                continue
            if name not in channel_name_to_id or cid < channel_name_to_id[name]:
                channel_name_to_id[name] = cid
        # bead parse-from-stream: master identity may come from the attached
        # stream names (pre-built async in execute_event_sync_rule) instead of
        # the channel names. The attach path maps the winning identity back to
        # its channel id via this same map.
        name_to_id = (
            master_name_to_id if master_name_to_id is not None
            else channel_name_to_id
        )
        master_names = sorted(name_to_id)

        resolution = resolve_event_sync(
            config, master_names, secondary_streams, now=now,
            decisions=decisions, exclusions=exclusions,
            attached_stream_ids=attached_stream_ids,
        )
        return resolution, name_to_id, master_channel_count

    async def execute_event_sync_rule(self, rule_id: Optional[int],
                                      rule_name: str, config: dict,
                                      secondary_streams: list,
                                      exec_ctx: ExecutionContext,
                                      decisions=None,
                                      effective_master_group_id=None,
                                      exclusions=None) -> dict:
        """Execute one event_sync rule's attach path (bead ti939.2.1).

        Phase 1B — the FIRST write path for event_sync. Resolves every
        fetched secondary stream via :meth:`_resolve_event_sync` (the shared
        decision path), then executes band semantics:

        * ``would_attach`` → attach via the EXISTING add-stream-to-channel
          merge internals (:meth:`_add_stream_to_channel`). Idempotent by
          construction: a stream already on the master channel is a no-op
          skip, so stateless re-runs after every refresh are safe. The
          journal provenance carries ``attach_source`` ("threshold" vs
          "review_queue") so queue-driven attaches — dispositions upgraded
          by a prior operator accept — stay distinguishable (ti939.3.2).
        * ``ambiguous`` (band or contested rail) → skip + count, AND
          surface the enqueue-eligible pairings in
          ``summary["review_candidates"]`` (fingerprint + evidence
          payloads). The ENGINE persists them on live runs — this method
          stays DB-free, and a dry run therefore enqueues nothing by
          construction.
        * ``unmatched`` / ``parse_failed`` → skip with reason (counted).
          Pairings the operator previously REJECTED are already filtered
          inside the resolver (counted via ``rejected_suppressed``).
        * ``excluded_by_operator`` (bead ti939.3.5) → skip + count. The
          resolver already removed the excluded pairing(s) BEFORE the
          attach band was honored; this disposition marks a stream whose
          only viable pairing was an operator "never attach". Suppression
          on streams that still attach/queue elsewhere is counted via
          ``excluded_suppressed``.

        Blast-radius controls:

        * Per-run attach cap (``config["max_attach_per_run"]``): on overage
          this stops attaching (WARNs; the engine also writes an execution
          log entry + run warning from the returned summary) and records the
          overage count. Live runs only — a dry run mutates nothing, and
          capping it would hide the run's true would-attach size. Idempotent
          already-attached no-ops never consume cap budget.
        * Journal entry per attach: category "event_sync",
          batch_id = execution id, with NAMES alongside IDs (secondary
          stream name+id, provider, master channel name+id) plus score,
          band, time delta and team-token verdict — via the provenance dict
          threaded into ``_add_stream_to_channel``.
        * NEVER creates or deletes channels; never touches
          managed_channel_ids; never toggles Dispatcharr group settings.

        Returns a summary dict the engine folds into the execution record:
        counts per disposition, cap state, and per-attach ``attach_entries``
        for the execution log.
        """
        from services.event_sync_resolver import (
            AMBIGUOUS_REASON_CONTESTED,
            DEFAULT_MAX_ATTACH_PER_RUN,
            DISPOSITION_AMBIGUOUS,
            DISPOSITION_EXCLUDED,
            DISPOSITION_PARSE_FAILED,
            DISPOSITION_UNMATCHED,
            DISPOSITION_WOULD_ATTACH,
        )
        from services.event_sync_review import (
            ATTACH_SOURCE_REVIEW_QUEUE,
            PROVIDER_ID_UNKNOWN,
            master_event_key,
            stream_name_hash,
        )
        from concurrency import run_cpu_bound

        # bead parse-from-stream: build the master identity map from attached
        # stream names (async fetch) BEFORE the CPU-bound resolve. Default
        # (flag off) keeps channel-name identity built inside _resolve.
        master_name_to_id = None
        if config.get("parse_master_from_stream"):
            from services.event_sync_resolver import build_master_name_to_id
            cg = (
                effective_master_group_id
                if effective_master_group_id is not None
                else config["master_group_id"]
            )
            master_chs = [
                c for c in self.existing_channels
                if c.get("channel_group_id") == cg
            ]
            master_name_to_id = await build_master_name_to_id(
                master_chs, self.client, True
            )

        # CPU-bound scoring off the event loop (same treatment as the
        # preview endpoint).
        resolution, name_to_id, master_channel_count = await run_cpu_bound(
            self._resolve_event_sync, config, secondary_streams, None,
            decisions, effective_master_group_id, master_name_to_id,
            exclusions,
        )

        cap = config.get("max_attach_per_run", DEFAULT_MAX_ATTACH_PER_RUN)
        cap_active = bool(cap) and not exec_ctx.dry_run

        summary = {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "master_group_id": config["master_group_id"],
            "secondary_group_ids": list(config["secondary_group_ids"]),
            "secondary_streams": len(resolution.resolved),
            "master_channels": master_channel_count,
            "master_channels_unparsed": len(resolution.unparsed_master_names),
            "attached": 0,
            "already_attached": 0,
            "ambiguous_skipped": 0,
            "contested_skipped": 0,
            "unmatched": 0,
            "parse_failed": 0,
            "attach_errors": 0,
            "cap": cap,
            "capped": False,
            "cap_overage": 0,
            "attach_entries": [],
            # ti939.3.2 review-queue surface: queue-driven attach count,
            # rejection-suppressed pairing count, and the pending-question
            # payloads the engine persists on live runs.
            "queue_attached": 0,
            "rejected_suppressed": 0,
            "review_candidates": [],
            # ti939.3.5 operator exclusions: streams whose only viable
            # pairing was operator-excluded, plus the per-pairing
            # suppression count (also increments on streams that still
            # attach/queue elsewhere).
            "excluded_by_operator": 0,
            "excluded_suppressed": 0,
        }

        for r in resolution.resolved:
            summary["rejected_suppressed"] += r.rejected_suppressed
            summary["excluded_suppressed"] += r.excluded_suppressed
            if r.disposition == DISPOSITION_PARSE_FAILED:
                summary["parse_failed"] += 1
                continue
            if r.disposition == DISPOSITION_UNMATCHED:
                summary["unmatched"] += 1
                continue
            if r.disposition == DISPOSITION_EXCLUDED:
                summary["excluded_by_operator"] += 1
                logger.info(
                    "[EVENT-SYNC] Rule '%s': stream '%s' EXCLUDED by "
                    "operator (never-attach, %d pairing(s)) — skipped",
                    rule_name, r.stream.name, r.excluded_suppressed,
                )
                continue
            if r.disposition == DISPOSITION_AMBIGUOUS:
                summary["ambiguous_skipped"] += 1
                if r.ambiguous_reason == AMBIGUOUS_REASON_CONTESTED:
                    summary["contested_skipped"] += 1
                logger.info(
                    "[EVENT-SYNC] Rule '%s': stream '%s' AMBIGUOUS (%s) — "
                    "skipped, never auto-attached; %d pairing(s) surfaced "
                    "for operator review",
                    rule_name, r.stream.name, r.ambiguous_reason,
                    len(r.review_candidates),
                )
                # ti939.3.2: enqueue instead of silently skipping. One
                # payload per enqueue-eligible pairing, keyed on content
                # fingerprints (NEVER stream/channel ids — those appear
                # only inside the display-only evidence snapshot).
                shash = stream_name_hash(r.stream.name)
                provider_id = (
                    r.stream.provider_id
                    if r.stream.provider_id is not None
                    else PROVIDER_ID_UNKNOWN
                )
                for c in r.review_candidates:
                    event_key = master_event_key(c.parsed)
                    if event_key is None:  # defensive: candidates always parse
                        continue
                    summary["review_candidates"].append({
                        "provider_id": provider_id,
                        "stream_name_hash": shash,
                        "event_key": event_key,
                        "evidence": {
                            "rule_name": rule_name,
                            "stream_name": r.stream.name,
                            "provider": r.stream.provider,
                            "secondary_group_id": r.stream.group_id,
                            "stream_id": r.stream.stream_id,
                            "stream_parsed_title": r.result.parsed.title,
                            "stream_parsed_start": (
                                r.result.parsed.start.isoformat()
                                if r.result.parsed.start else None
                            ),
                            "master_channel_name": c.master_name,
                            "master_channel_id": name_to_id.get(c.master_name),
                            "master_parsed_title": c.parsed.title,
                            "master_parsed_start": (
                                c.parsed.start.isoformat()
                                if c.parsed.start else None
                            ),
                            "score": round(c.score, 4),
                            "band": c.band,
                            "team_verdict": c.team_verdict,
                            "time_delta_minutes": round(
                                c.time_delta_minutes, 1),
                            "ambiguous_reason": r.ambiguous_reason,
                        },
                    })
                continue
            if r.disposition != DISPOSITION_WOULD_ATTACH:
                # Defensive: an unknown disposition must be loud, not a
                # silent attach or a silent drop.
                logger.warning(
                    "[EVENT-SYNC] Rule '%s': stream '%s' has unknown "
                    "disposition %r — skipped",
                    rule_name, r.stream.name, r.disposition,
                )
                summary["attach_errors"] += 1
                continue

            # would_attach — resolve the master channel dict by CURRENT id.
            master_channel_id = name_to_id.get(r.best.master_name)
            channel = self._channel_by_id.get(master_channel_id)
            if channel is None:
                logger.warning(
                    "[EVENT-SYNC] Rule '%s': master channel '%s' (id=%s) "
                    "vanished from the run's channel cache — stream '%s' "
                    "not attached",
                    rule_name, r.best.master_name, master_channel_id,
                    r.stream.name,
                )
                summary["attach_errors"] += 1
                continue

            # Per-run attach cap (live only). Checked only for streams that
            # actually NEED attaching: an already-attached stream is an
            # idempotent no-op that neither consumes budget nor counts as
            # overage (otherwise a capped re-run would misreport its own
            # prior work as deferred).
            already_attached = r.stream.stream_id in {
                s["id"] if isinstance(s, dict) else s
                for s in channel.get("streams", [])
            }
            if not already_attached \
                    and cap_active and summary["attached"] >= cap:
                if not summary["capped"]:
                    summary["capped"] = True
                    logger.warning(
                        "[EVENT-SYNC] Rule '%s': per-run attach cap reached "
                        "(%s) — no further streams will be attached this "
                        "run. event_sync is idempotent: run it again to "
                        "continue, or raise max_attach_per_run on the rule.",
                        rule_name, cap,
                    )
                summary["cap_overage"] += 1
                continue

            provenance = {
                "kind": "event_sync",
                "rule_id": rule_id,
                "secondary_stream_id": r.stream.stream_id,
                "secondary_stream_name": r.stream.name,
                "provider": r.stream.provider,
                "secondary_group_id": r.stream.group_id,
                "master_channel_id": master_channel_id,
                "master_channel_name": r.best.master_name,
                "score": round(r.best.score, 4),
                "band": r.best.band,
                "time_delta_minutes": round(r.best.time_delta_minutes, 1),
                "team_verdict": r.best.team_verdict,
                # ti939.3.2: how the attach decision was reached —
                # "threshold" (matcher admission) vs "review_queue" (prior
                # operator accept). The journal-distinction contract: a
                # queue-driven attach is auditable as such.
                "attach_source": r.attach_source,
            }
            stream_ctx = StreamContext(
                stream_id=r.stream.stream_id,
                stream_name=r.stream.name,
                group_name=None,
                m3u_account_name=r.stream.provider,
            )
            result = await self._add_stream_to_channel(
                channel, stream_ctx, exec_ctx,
                merge_provenance=provenance,
                journal_category="event_sync",
            )
            # Route through the shared chokepoint so streams_merged /
            # merged_channel_ids / modified_entities (legacy rollback state)
            # aggregate exactly like every other merge path.
            exec_ctx.add_result(result)

            if not result.success:
                summary["attach_errors"] += 1
            elif result.skipped:
                # Already attached — the idempotence that makes stateless
                # re-runs after every refresh safe.
                summary["already_attached"] += 1
            else:
                summary["attached"] += 1
                if r.attach_source == ATTACH_SOURCE_REVIEW_QUEUE:
                    summary["queue_attached"] += 1
                    logger.info(
                        "[EVENT-SYNC] Rule '%s': stream '%s' attached to "
                        "'%s' via REVIEW-QUEUE accept (fingerprint-keyed "
                        "decision re-applied)",
                        rule_name, r.stream.name, r.best.master_name,
                    )

            summary["attach_entries"].append({
                "type": "event_sync_attach",
                "description": result.description,
                "success": result.success,
                "skipped": result.skipped,
                "entity_id": master_channel_id,
                "entity_name": r.best.master_name,
                "error": result.error,
                "match": provenance,
            })

        # Unmatched-stream promotion (bead ti939.4.1) — strictly opt-in.
        # The "promotion" key exists on the summary ONLY when the config
        # carries promote_unmatched=true, so a promotion-less rule's summary
        # (and everything the engine derives from it) stays byte-identical
        # to the pre-feature shape (AC-1).
        if config.get("promote_unmatched"):
            summary["promotion"] = await self._execute_event_sync_promotion(
                rule_id, rule_name, config, resolution, exec_ctx,
            )

        return summary

    async def _execute_event_sync_promotion(
        self, rule_id: Optional[int], rule_name: str, config: dict,
        resolution, exec_ctx: ExecutionContext,
    ) -> dict:
        """Promote unmatched secondary-only events to ECM-managed channels
        (bead ti939.4.1 — the ONE sanctioned channel-creation exception).

        The PLAN comes from ``services.event_sync_promote
        .build_promotion_plan`` — the same pure helper the preview endpoint
        calls over the same resolver output, so preview and live promotion
        cannot diverge on identical inputs. This method only REALIZES the
        plan:

        * **Create-or-adopt** per unit via a constructed
          ``Action(type="create_channel", if_exists="skip")`` through
          :meth:`_execute_create_channel`'s existing group-scoped duplicate
          lookup (``match_scope_target_group``). ``allow_manual_channel_merge``
          is True: a previously-promoted channel reads as "manual" on later
          runs (Dispatcharr does not persist ECM's in-run auto_created
          marker), and adopting it by name IS the idempotence mechanism —
          the promotion target group is documented ECM-owned space.
        * **Attach** every unit stream via :meth:`_add_stream_to_channel`
          with provenance ``kind="event_sync_promote"`` (content fingerprint
          + names; IDs display-only) under journal category ``event_sync``.
          The stream embedded in a CREATE call is journaled explicitly with
          the same provenance so every attached stream has its journal row.
        * **Cap**: only units the plan marked realizable run; capped units
          are counted (the engine surfaces the warning). Adoption never
          consumes cap budget.

        Returns the promotion summary the engine folds into the rule's
        event_sync summary and uses to register the managed set
        (``channel_ids`` — promoted channels ONLY, never masters: the
        create/adopt lookup is scoped to the promotion target group, so a
        master-group channel is unreachable by construction; the engine
        re-asserts this invariant when registering).
        """
        from services.event_sync_promote import build_promotion_plan
        from services.event_sync_review import (
            PROVIDER_ID_UNKNOWN,
            stream_name_hash,
        )

        target_group_id = config["promote_target_group_id"]

        # Existing-name map for the plan's create-vs-adopt decision — the
        # SAME channel universe the create action's scoped lookup resolves
        # against (self.existing_channels feeds both).
        existing_name_to_id: dict[str, int] = {}
        for ch in self.existing_channels:
            if ch.get("channel_group_id") != target_group_id:
                continue
            name, cid = ch.get("name"), ch.get("id")
            if not name or cid is None:
                continue
            lowered = name.lower()
            if lowered not in existing_name_to_id \
                    or cid < existing_name_to_id[lowered]:
                existing_name_to_id[lowered] = cid

        plan = build_promotion_plan(
            config, resolution.resolved, existing_name_to_id
        )

        promo = {
            "target_group_id": target_group_id,
            "units": len(plan.units),
            "promoted_created": 0,
            "promoted_adopted": 0,
            "streams_attached": 0,
            "already_attached": 0,
            "attach_errors": 0,
            "cap": plan.cap,
            "capped": plan.capped,
            "cap_overage": plan.cap_overage,
            "channel_ids": [],
            "promote_entries": [],
        }

        def _provenance(row, unit, channel_id, channel_name) -> dict:
            # Content fingerprint (provider_id + normalized-name hash +
            # event key) is the durable identity; every id is display-only
            # (Dispatcharr stream/channel ids churn — epic ti939.3 keying
            # constraint, inherited verbatim).
            return {
                "kind": "event_sync_promote",
                "rule_id": rule_id,
                "provider_id": (
                    row.stream.provider_id
                    if row.stream.provider_id is not None
                    else PROVIDER_ID_UNKNOWN
                ),
                "stream_name_hash": stream_name_hash(row.stream.name),
                "event_key": unit.event_key,
                "secondary_stream_id": row.stream.stream_id,
                "secondary_stream_name": row.stream.name,
                "provider": row.stream.provider,
                "secondary_group_id": row.stream.group_id,
                "promoted_channel_id": channel_id,
                "promoted_channel_name": channel_name,
                "disposition": row.disposition,
            }

        for unit in plan.units:
            first = unit.rows[0]
            create_action = Action(type="create_channel", params={
                "name_template": unit.channel_name,
                "if_exists": "skip",
                "group_id": target_group_id,
                "channel_number": "auto",
            })
            first_ctx = StreamContext(
                stream_id=first.stream.stream_id,
                stream_name=first.stream.name,
                group_name=None,
                m3u_account_name=first.stream.provider,
            )
            result = await self._execute_create_channel(
                create_action, first_ctx, exec_ctx, template_ctx={},
                rule_target_group_id=target_group_id,
                match_scope_target_group=True,
                allow_manual_channel_merge=True,
            )
            exec_ctx.add_result(result)
            if not result.success:
                logger.warning(
                    "[EVENT-SYNC] Rule '%s': promotion of event %r FAILED "
                    "to create/adopt channel '%s': %s",
                    rule_name, unit.event_key, unit.channel_name,
                    result.error,
                )
                promo["attach_errors"] += 1
                promo["promote_entries"].append({
                    "type": "event_sync_promote",
                    "description": (
                        f"Promotion failed for '{unit.channel_name}': "
                        f"{result.error}"
                    ),
                    "success": False,
                    "skipped": False,
                    "entity_id": None,
                    "entity_name": unit.channel_name,
                    "error": result.error,
                    "match": _provenance(first, unit, None, unit.channel_name),
                })
                continue

            channel_id = (
                result.entity_id if result.entity_id is not None
                else exec_ctx.current_channel_id
            )
            channel = self._channel_by_id.get(channel_id)
            attach_rows = list(unit.rows)
            if result.created:
                promo["promoted_created"] += 1
                # The FIRST stream rode the create payload — journal its
                # attach explicitly (live only; dry-run journals nothing)
                # so every promoted attach has a fingerprinted journal row.
                promo["streams_attached"] += 1
                if not exec_ctx.dry_run and first.stream.stream_id is not None:
                    self._journal_merge(
                        channel_id, unit.channel_name,
                        first.stream.stream_id,
                        [], [first.stream.stream_id],
                        provenance=_provenance(
                            first, unit, channel_id, unit.channel_name),
                        category="event_sync",
                        stream_name=first.stream.name,
                    )
                attach_rows = attach_rows[1:]
                logger.info(
                    "[EVENT-SYNC] Rule '%s': PROMOTED event %r -> created "
                    "channel '%s' (id=%s) in group %s with stream '%s'",
                    rule_name, unit.event_key, unit.channel_name,
                    channel_id, target_group_id, first.stream.name,
                )
            else:
                promo["promoted_adopted"] += 1
                logger.info(
                    "[EVENT-SYNC] Rule '%s': promotion adopted existing "
                    "channel '%s' (id=%s) for event %r",
                    rule_name, unit.channel_name, channel_id, unit.event_key,
                )

            promo["promote_entries"].append({
                "type": "event_sync_promote",
                "description": result.description,
                "success": True,
                "skipped": result.skipped,
                "entity_id": channel_id,
                "entity_name": unit.channel_name,
                "error": None,
                "match": _provenance(first, unit, channel_id, unit.channel_name),
            })

            # Attach the (remaining) unit streams — idempotent by
            # construction, exactly like the master attach path.
            for row in attach_rows:
                if channel is None:
                    promo["attach_errors"] += 1
                    continue
                row_ctx = StreamContext(
                    stream_id=row.stream.stream_id,
                    stream_name=row.stream.name,
                    group_name=None,
                    m3u_account_name=row.stream.provider,
                )
                attach_result = await self._add_stream_to_channel(
                    channel, row_ctx, exec_ctx,
                    merge_provenance=_provenance(
                        row, unit, channel_id, unit.channel_name),
                    journal_category="event_sync",
                )
                exec_ctx.add_result(attach_result)
                if not attach_result.success:
                    promo["attach_errors"] += 1
                elif attach_result.skipped:
                    promo["already_attached"] += 1
                else:
                    promo["streams_attached"] += 1
                promo["promote_entries"].append({
                    "type": "event_sync_promote_attach",
                    "description": attach_result.description,
                    "success": attach_result.success,
                    "skipped": attach_result.skipped,
                    "entity_id": channel_id,
                    "entity_name": unit.channel_name,
                    "error": attach_result.error,
                    "match": _provenance(
                        row, unit, channel_id, unit.channel_name),
                })

            if channel_id is not None:
                promo["channel_ids"].append(channel_id)

        if plan.capped:
            logger.warning(
                "[EVENT-SYNC] Rule '%s': per-run promotion cap reached "
                "(%s) — %s promotion unit(s) NOT created this run. "
                "Promotion is idempotent: run again to continue, or raise "
                "max_promote_per_run on the rule.",
                rule_name, plan.cap, plan.cap_overage,
            )

        return promo

    async def assign_event_sync_dummy_epg(self, rule_id: Optional[int],
                                          rule_name: str, config: dict,
                                          exec_ctx: ExecutionContext) -> dict:
        """Assign the rule's dummy EPG profile to master-group channels
        (bead ti939.3.3 — event_sync dummy EPG auto-assignment).

        The master group's channels are ordinary Dispatcharr channels, so
        this rides the EXISTING machinery end to end: each channel without
        guide data gets a standard ``assign_epg`` execution against the
        Dispatcharr EPG source that serves the profile's XMLTV
        (``/api/dummy-epg/xmltv/<profile_id>``); a channel the source does
        not cover yet (brand-new event, or first run) defers into
        ``_deferred_epg_assignments`` and the EXISTING Pass 5 refresh-and-
        retry — which also auto-adds the master group to the profile's
        ``channel_group_ids``, regenerates the XMLTV, refreshes the source
        and retries. No parallel mechanism.

        Idempotent and non-clobbering:

        * a channel whose ``epg_data_id`` already belongs to the profile's
          source is a no-op (``already_assigned``);
        * a channel with FOREIGN guide data (any other source, e.g. a
          hand-assigned real EPG) is never overwritten
          (``skipped_foreign_epg``);
        * only channels with NO guide data are assigned.

        NEVER: creates/deletes channels or touches Dispatcharr group
        settings — assignment is ``epg_data_id`` metadata on existing
        master channels, exactly what standard assign_epg rules write.

        Returns a summary dict the engine folds into the run results:
        counts per disposition plus per-channel ``assign_entries`` for the
        execution log. ``source_id`` is None when no Dispatcharr EPG
        source serves the profile (the engine surfaces that as a run
        warning — nothing to assign from).
        """
        profile_id = config["dummy_epg_profile_id"]
        master_group_id = config["master_group_id"]
        # bead ti939.4.1: promotion-enabled rules assign the SAME dummy EPG
        # profile to their ECM-promoted channels in the target group — a
        # promoted event needs guide data exactly as much as a master event
        # does. The set stays {master} for every promotion-less config, so
        # pre-feature behavior is byte-identical.
        epg_group_ids = {master_group_id}
        if config.get("promote_unmatched") \
                and config.get("promote_target_group_id") is not None:
            epg_group_ids.add(config["promote_target_group_id"])

        source_id = self._dummy_source_by_profile.get(profile_id)
        if source_id is None and self._combined_dummy_source_ids:
            # Fallback: a combined all-profiles source serves this profile's
            # entries too (lowest id — the sort in __init__ — for determinism).
            source_id = self._combined_dummy_source_ids[0]

        summary = {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "profile_id": profile_id,
            "source_id": source_id,
            "assigned": 0,
            "already_assigned": 0,
            "deferred": 0,
            "skipped_foreign_epg": 0,
            "failed": 0,
            "assign_entries": [],
        }
        if source_id is None:
            logger.warning(
                "[EVENT-SYNC] Rule '%s': dummy_epg_profile_id=%s has no "
                "Dispatcharr EPG source (no source URL matches "
                "/api/dummy-epg/xmltv/%s) — nothing to assign from",
                rule_name, profile_id, profile_id,
            )
            return summary

        source_entry_ids = {
            e["id"] for e in self._epg_data_by_source.get(source_id, [])
        }

        # bead ti939.4.1: channels PROMOTED THIS RUN are not in
        # self.existing_channels (that list is the run-start fetch) — they
        # live in _created_channels. Include them so a promoted channel gets
        # its guide data on the same run that created it, not one run late.
        # Promotion-less configs never take this branch (create_channel is
        # unreachable for them), so the iterated set is unchanged for them.
        channels_to_assign = list(self.existing_channels)
        if config.get("promote_unmatched"):
            existing_ids = {
                ch.get("id") for ch in self.existing_channels
            }
            channels_to_assign.extend(
                ch for ch in self._created_channels.values()
                if ch.get("channel_group_id") in epg_group_ids
                and ch.get("id") not in existing_ids
            )

        for channel in channels_to_assign:
            if channel.get("channel_group_id") not in epg_group_ids:
                continue
            channel_id = channel.get("id")
            channel_name = channel.get("name", f"Channel {channel_id}")
            epg_data_id = channel.get("epg_data_id")

            if epg_data_id is not None and epg_data_id in source_entry_ids:
                # Idempotence: already carrying this profile's guide data.
                summary["already_assigned"] += 1
                continue
            if epg_data_id is not None:
                # Foreign guide data (another source / hand-assigned real
                # EPG) — never clobber an existing assignment.
                logger.debug(
                    "[EVENT-SYNC] Rule '%s': master channel %s '%s' already "
                    "has foreign epg_data_id=%s — not overwritten",
                    rule_name, channel_id, channel_name, epg_data_id,
                )
                summary["skipped_foreign_epg"] += 1
                continue

            # A FRESH per-channel context: the deferred tuple carries the
            # ExecutionContext, and Pass 5's retry resolves the target from
            # its current_channel_id — a shared context would retry every
            # deferral against whichever channel was processed last.
            channel_ctx = ExecutionContext(dry_run=exec_ctx.dry_run)
            channel_ctx.current_channel_id = channel_id
            action = Action(type="assign_epg", params={"epg_id": source_id})
            stream_ctx = StreamContext(
                stream_id=0,
                stream_name=channel_name,
                group_name=None,
            )
            result = await self._execute_assign_epg(
                action, stream_ctx, channel_ctx, defer_on_no_match=True
            )
            # Fold into the rule-level context at the shared chokepoint so
            # channels_updated / modified_entities (legacy rollback state)
            # aggregate exactly like standard assign_epg executions.
            exec_ctx.add_result(result)

            if result.deferred:
                summary["deferred"] += 1
            elif result.success:
                summary["assigned"] += 1
            else:
                summary["failed"] += 1

            summary["assign_entries"].append({
                "type": "event_sync_dummy_epg",
                "description": result.description,
                "success": result.success,
                "deferred": result.deferred,
                "entity_id": channel_id,
                "entity_name": channel_name,
                "error": result.error,
            })

        return summary

    @staticmethod
    def _is_manual_channel(channel: Optional[dict]) -> bool:
        """True when ``channel`` is a hand-built MANUAL channel (protected).

        enhancedchannelmanager-orzck: a channel is MANUAL when its
        ``auto_created`` key is missing or falsy. This mirrors the ADR-010
        snapshot precedent (``channel_pipeline_engine._capture_snapshot`` →
        ``not ch.get("auto_created", False)``): a missing key means manual /
        protected, NOT auto. Only an explicit truthy ``auto_created`` makes a
        channel an unprotected auto-created merge candidate.
        """
        if channel is None:
            return False
        return not channel.get("auto_created", False)

    @staticmethod
    def _add_candidate(candidates: dict, key: str, channel: dict) -> None:
        """Append ``channel`` to the multi-candidate list for ``key``.

        bead g0uuf: identity-deduped (same dict object never listed twice
        under one key) so mutation sites — rename, create, dry-run create —
        can call this unconditionally.
        """
        if not key:
            return
        lst = candidates.setdefault(key, [])
        if not any(existing is channel for existing in lst):
            lst.append(channel)

    def _find_unique_channel_by_exact_identity(
        self,
        name: str,
        *,
        scope_group_id: Optional[int] = None,
        block_manual: bool = True,
    ) -> Optional[dict]:
        """Resolve one exact post-normalization identity, never a collision."""
        key = name.lower()
        candidates: list[dict] = []
        created = self._created_channels.get(key)
        if created is not None:
            candidates.append(created)
        for mapping in (
            self._base_name_candidates,
            self._by_name_candidates,
            self._normalized_name_candidates,
        ):
            candidates.extend(mapping.get(key, ()))

        unique: list[dict] = []
        seen: set[int] = set()
        for candidate in candidates:
            identity = candidate.get("id")
            marker = id(candidate) if identity is None else int(identity)
            if marker in seen:
                continue
            seen.add(marker)
            if scope_group_id is not None and candidate.get("channel_group_id") != scope_group_id:
                continue
            if block_manual and self._is_manual_channel(candidate):
                self._last_manual_block = candidate
                continue
            unique.append(candidate)

        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            logger.warning(
                "[AUTO-CREATE-EXEC] Exact normalized channel identity is "
                "ambiguous across %d eligible channels; stream skipped.",
                len(unique),
            )
        return None

    def _find_channel_by_name(self, name: str, scope_group_id: Optional[int] = None,
                              exact_only: bool = False,
                              block_manual: bool = True,
                              fold_key: bool = False) -> Optional[dict]:
        """Find channel by exact name (case-insensitive).

        Also checks the base-name mapping so that a lookup for "USA Network"
        finds a channel created as "4000 | USA Network", and the normalized-name
        mapping so that merge_streams can find channels the same way
        normalized_name_in_group does.

        When a normalization engine is available and no exact match is found,
        the lookup also normalizes the search name itself and re-queries the
        channel/core-name indices. This prevents auto-creation from creating
        duplicate channels when an existing channel was created before
        normalization rules existed (GH-104 / bd-u9odj).

        When ``scope_group_id`` is not None (enabled by the rule's
        ``match_scope_target_group`` flag — GH-92 / bd-r9mtd), any candidate
        channel is filtered to require ``channel_group_id == scope_group_id``.
        A match in a different group is treated as "not found" so the caller
        will create a new channel in the desired group instead of merging
        across groups. Passing ``None`` (default) preserves the prior
        group-agnostic behavior.

        When ``exact_only`` is True (bd-0emgo.1), only the exact-key indices are
        consulted (created/base-name/by-name/normalized-name maps); the
        re-normalize and core-name *fuzzy* fallbacks below are SKIPPED. This is
        used by merge_streams target=auto to default to exact normalized-name
        equality. ``exact_only=False`` (default) preserves the GH-104
        duplicate-prevention fallbacks for channel CREATION.

        When ``block_manual`` is True (default — enhancedchannelmanager-orzck /
        W1), any candidate that is a MANUAL channel (``auto_created`` missing or
        falsy) is treated as "not found" so auto-creation never adopts a
        hand-built channel as a merge/update/rename target. The manual channel
        is deliberately LEFT in the lookup maps (so GH-104 dedup still sees it
        and does not create a second copy) — it is rejected only at this
        resolution chokepoint, yielding None so the caller creates a new auto
        channel instead. Callers pass ``block_manual = not
        allow_manual_channel_merge``; the rule-level ``allow_manual_channel_merge``
        flag (default False) is the only way to opt a rule back into adopting
        manual channels.

        When ``fold_key`` is True (GH #645 / bead 0vao3 — the rule's opt-in
        ``fold_match_key`` flag), a LAST-RESORT fallback compares by the
        shared canonical fold key (casefold + strip ALL whitespace, see
        ``match_fold.fold_match_key``) so "Eurosport2" finds "eurosport 2".
        The fold is consulted only after every exact/normalized lookup above
        it misses — an exact match always wins — and the folded candidate
        still passes the same scope and manual-channel gates. Comparison key
        only: stored channel names are never altered.

        Multi-candidate resolution (bead g0uuf): the same channel name in
        several groups is a supported layout, but each lookup map holds ONE
        channel per key. Each stage therefore first tests its legacy
        single-slot pick (preserving historical winner semantics), and only
        when that pick fails the scope/manual gates scans every channel
        indexed under the same key, returning the first that passes. A
        scoped lookup thus finds the same-named channel IN the scoped group
        even when a sibling from another group occupies the single-slot map.
        """
        def _in_scope(c: Optional[dict]) -> bool:
            if c is None:
                return False
            if scope_group_id is not None \
                    and c.get("channel_group_id") != scope_group_id:
                return False
            if block_manual and self._is_manual_channel(c):
                # enhancedchannelmanager-wy6l5: remember the rejected manual
                # candidate so callers can surface a user-visible "merge
                # blocked" reason when the whole lookup ends in None. Only
                # recorded when the candidate already passed the scope filter —
                # i.e. the manual gate is the ONLY reason it was rejected.
                self._last_manual_block = c
                return False
            return True

        # bead g0uuf: per-stage resolver. The legacy single-slot pick keeps
        # priority (zero behavior change while it passes the scope/manual
        # gates); when it fails them, scan EVERY channel indexed under the
        # same key so same-named channels in other single-slot positions —
        # e.g. one "ESPN" per group across five groups — stay findable by a
        # scoped lookup instead of reading as "not found" (which made
        # merge_only skip and merge create a duplicate).
        def _pick(primary: Optional[dict], candidates: Optional[list]) -> Optional[dict]:
            if _in_scope(primary):
                return primary
            for cand in candidates or ():
                if cand is not primary and _in_scope(cand):
                    return cand
            return None

        # Reset the block marker for THIS lookup (stale candidates from a
        # previous stream/action must not leak into this one).
        self._last_manual_block = None
        name_lower = name.lower()
        # Check newly created channels first (by exact name)
        if name_lower in self._created_channels:
            cand = self._created_channels[name_lower]
            if _in_scope(cand):
                logger.debug("[AUTO-CREATE-EXEC] '%s' found in created channels", name)
                return cand
        # Check base-name mapping (base name -> number-prefixed channel)
        cand = _pick(self._base_name_to_channel.get(name_lower),
                     self._base_name_candidates.get(name_lower))
        if cand is not None:
            logger.debug("[AUTO-CREATE-EXEC] '%s' found via base-name mapping", name)
            return cand
        result = _pick(self._channel_by_name.get(name_lower),
                       self._by_name_candidates.get(name_lower))
        if result is not None:
            logger.debug("[AUTO-CREATE-EXEC] '%s' found in existing channels (id=%s)", name, result.get('id'))
            return result
        # Check normalized-name mapping (normalization-engine-processed channel names)
        cand = _pick(self._normalized_name_to_channel.get(name_lower),
                     self._normalized_name_candidates.get(name_lower))
        if cand is not None:
            logger.debug("[AUTO-CREATE-EXEC] '%s' found via normalized-name mapping (id=%s, name='%s')", name, cand.get('id'), cand.get('name'))
            return cand
        # Normalized-name fallback: when the search name isn't an exact match
        # to any stored channel, normalize it through the engine and retry. This
        # catches the case where an existing channel's stored name already
        # equals the normalized form (e.g., stored "RTL", searching for
        # "RTL ᴿᴬᵂ") — symmetric to the normalized-name map above which
        # handles the opposite direction.
        #
        # bd-0emgo.1: skipped when exact_only=True (merge_streams target=auto
        # default). These fuzzy fallbacks remain active for channel CREATION
        # (exact_only=False) where they prevent duplicate channels (GH-104).
        if not exact_only and self._normalization_engine is not None:
            try:
                norm = self._normalization_engine.normalize(name)
                norm_lower = (norm.normalized or "").strip().lower()
            except Exception as e:
                logger.warning("[AUTO-CREATE-EXEC] Normalization of lookup name '%s' failed: %s", name, e)
                norm_lower = ""
            if norm_lower and norm_lower != name_lower:
                legacy = (
                    self._channel_by_name.get(norm_lower)
                    or self._base_name_to_channel.get(norm_lower)
                    or self._normalized_name_to_channel.get(norm_lower)
                    or self._core_name_to_channel.get(norm_lower)
                )
                cand = _pick(legacy, [
                    c
                    for cands in (self._by_name_candidates.get(norm_lower),
                                  self._base_name_candidates.get(norm_lower),
                                  self._normalized_name_candidates.get(norm_lower),
                                  self._core_name_candidates.get(norm_lower))
                    for c in (cands or ())
                ])
                if cand is not None:
                    logger.debug("[AUTO-CREATE-EXEC] '%s' found via normalized-search fallback ('%s' -> '%s', id=%s)",
                                 name, name, norm_lower, cand.get('id'))
                    return cand
            # Core-name fallback: strip tag/country prefixes/suffixes and look
            # up by core key. Only used when normalized fallback above didn't
            # produce a hit, so we don't double-count.
            try:
                core = self._normalization_engine.extract_core_name(name)
                core_lower = (core or "").strip().lower()
            except Exception as e:
                logger.warning("[AUTO-CREATE-EXEC] Core-name extraction for lookup '%s' failed: %s", name, e)
                core_lower = ""
            if core_lower and core_lower != name_lower:
                cand = _pick(self._core_name_to_channel.get(core_lower),
                             self._core_name_candidates.get(core_lower))
                if cand is not None:
                    logger.debug("[AUTO-CREATE-EXEC] '%s' found via core-name fallback (core='%s', id=%s)",
                                 name, core_lower, cand.get('id'))
                    return cand
        # Folded-key fallback (GH #645 / bead 0vao3): opt-in per rule. Runs
        # LAST so any exact/normalized match above always wins; the candidate
        # goes through the same _in_scope gates (group scope + manual block).
        if fold_key:
            folded = fold_match_key(name)
            if folded:
                cand = _pick(self._fold_key_to_channel.get(folded),
                             self._fold_key_candidates.get(folded))
                if cand is not None:
                    logger.debug(
                        "[AUTO-CREATE-EXEC] '%s' found via fold-match-key fallback "
                        "(key='%s', id=%s, name='%s')",
                        name, folded, cand.get('id'), cand.get('name'))
                    return cand
        return None

    async def _maybe_enqueue_pending_merge(
        self,
        *,
        stream_ctx: StreamContext,
        channel_name: str,
        group_id: Optional[int],
        exec_ctx: ExecutionContext,
    ) -> Optional[ActionResult]:
        """BD-F bulk-M3U dedup hook integration (ADR-008 §D1, §D5, §D9).

        Wraps ``services.m3u_dedup_hook.check_and_enqueue_pending_merge``
        with the executor-side concerns: build the same-group candidate
        list from ``self.existing_channels``, source the operator
        threshold from settings, manage a short-lived DB session, and
        translate the hook's ``DedupHookResult`` into either an
        ``ActionResult`` (when the caller should skip channel creation)
        or ``None`` (when the caller should proceed with normal
        channel creation).

        Returns
        -------
        ActionResult | None
            ``ActionResult`` with ``skipped=True`` when a pending merge
            was enqueued (or was already present from an earlier M3U
            refresh — §D5 partial-unique-index collision). The caller
            MUST return this verbatim instead of creating the channel.
            ``None`` when no candidate was found or the hook
            short-circuited (dry-run, non-m3u_refresh triggered_by) —
            the caller proceeds with normal channel creation.
        """
        from services.m3u_dedup_hook import (
            M3U_REFRESH_TRIGGERED_BY,
            check_and_enqueue_pending_merge,
        )

        # Cheap short-circuit before any imports / DB work: only the
        # M3U-refresh path is in scope for BD-F. Other triggered_by
        # values (scheduled, manual) stay on legacy "always create"
        # semantics. The hook itself enforces this too — the early
        # check here just avoids unnecessary settings / candidate
        # work for the common non-M3U paths.
        if self._triggered_by != M3U_REFRESH_TRIGGERED_BY:
            return None

        if exec_ctx.dry_run:
            # Dry-run preview must not mutate the queue. The
            # downstream branch handles the simulated channel.
            return None

        # Build same-group candidate list from the executor's already-
        # loaded channel cache. The auto-creation engine fetches the
        # full Dispatcharr channel list once per pipeline run; here we
        # just filter by ``channel_group_id`` so the matcher scores
        # against the in-scope set (ADR-008 contract: candidates are
        # the existing channels in the target group). When
        # ``group_id`` is None (ungrouped import), we pass the full
        # list — the matcher's threshold + floor still bounds quality.
        if group_id is not None:
            candidates = [
                (c["id"], c.get("name", ""))
                for c in self.existing_channels
                if c.get("channel_group_id") == group_id and c.get("name")
            ]
        else:
            candidates = [
                (c["id"], c.get("name", ""))
                for c in self.existing_channels
                if c.get("name")
            ]

        if not candidates:
            return None

        # Source the operator-configured threshold from settings. The
        # matcher clamps to ADR-008 §D2 CONFIDENCE_FLOOR regardless,
        # so a missing settings field falls back to the floor (silent
        # refusal for anything below) rather than crashing.
        from config import get_settings
        try:
            settings = get_settings()
            threshold = float(getattr(settings, "dedup_threshold", 0.80))
        except Exception:
            logger.warning(
                "[DEDUP] Failed to read dedup_threshold from settings; "
                "falling back to 0.80",
                exc_info=True,
            )
            threshold = 0.80

        from database import get_session
        db_session = get_session()
        try:
            result = check_and_enqueue_pending_merge(
                stream_name=stream_ctx.stream_name,
                group_id=group_id,
                candidates=candidates,
                threshold=threshold,
                triggered_by=self._triggered_by,
                dry_run=exec_ctx.dry_run,
                db_session=db_session,
            )
        finally:
            db_session.close()

        if not result.enqueued:
            return None

        # Track the enqueue (fresh insert OR idempotent collision) on
        # the execution context so the engine can aggregate
        # ``pending_merges_added`` across all streams in the run and
        # surface it on the pipeline result for BD-J's toast.
        exec_ctx.pending_merges_added += 1

        # A pending merge row exists for this (stream_name, candidate)
        # pair after the hook returned — either freshly inserted by
        # this call or already present from an earlier refresh. In
        # both cases the caller skips the create_channel API call.
        # Description distinguishes the two paths for trace clarity.
        if result.candidate is not None:
            description = (
                f"Stream '{stream_ctx.stream_name}' enqueued as pending "
                f"merge candidate for existing channel id="
                f"{result.candidate.candidate_channel_id} "
                f"(confidence={result.candidate.confidence:.2f}); "
                f"channel creation deferred to operator review"
            )
        else:
            description = (
                f"Stream '{stream_ctx.stream_name}' already in pending "
                f"merges queue; channel creation deferred"
            )

        return ActionResult(
            success=True,
            action_type="create_channel",
            description=description,
            entity_type="channel",
            entity_name=channel_name,
            skipped=True,
            details=[description],
        )

    def _find_channel_by_regex(self, pattern: str) -> Optional[dict]:
        """Find first channel matching regex pattern."""
        try:
            # bd-eio04.15: safe_regex.compile rejects oversize patterns up
            # front and wraps compile errors in SafeRegexError. The compiled
            # pattern is reused for each channel lookup — safe_regex.search
            # enforces a per-call ReDoS budget, so a pathological pattern
            # fails a single channel comparison (returns None) rather than
            # hanging the whole search. End state: "no channel matched",
            # which is the existing fallback contract of this method.
            compiled = safe_regex.compile(pattern, flags=re.IGNORECASE)
            for channel in self.existing_channels:
                if safe_regex.search(compiled, channel.get("name", "")) is not None:
                    return channel
            for channel in self._created_channels.values():
                if safe_regex.search(compiled, channel.get("name", "")) is not None:
                    return channel
        except safe_regex.SafeRegexError:
            logger.debug("[AUTO-CREATE-EXEC] Invalid regex in channel name pattern")
        except re.error:
            logger.debug("[AUTO-CREATE-EXEC] Invalid regex in channel name pattern")
        return None

    def _find_channel_by_tvg_id(self, tvg_id: str) -> Optional[dict]:
        """Find channel by TVG ID."""
        if not tvg_id:
            return None
        for channel in self.existing_channels:
            if channel.get("tvg_id") == tvg_id:
                return channel
        for channel in self._created_channels.values():
            if channel.get("tvg_id") == tvg_id:
                return channel
        return None

    def _find_group_by_name(self, name: str) -> Optional[dict]:
        """Find group by exact name (case-insensitive)."""
        name_lower = name.lower()
        # Check newly created groups first
        if name_lower in self._created_groups:
            return self._created_groups[name_lower]
        return self._group_by_name.get(name_lower)

    def _get_next_channel_number(self, spec: Any) -> int:
        """
        Get next available channel number based on spec.

        Args:
            spec: "auto", specific int, or "min-max" range string

        Returns:
            Next available channel number
        """
        if isinstance(spec, int):
            num = spec
            while num in self._used_channel_numbers:
                num += 1
            logger.debug("[AUTO-CREATE-EXEC] spec=%s (int) -> %s", spec, num)
            return num

        if isinstance(spec, str):
            if spec == "auto":
                # Find next available number starting from 1
                num = 1
                while num in self._used_channel_numbers:
                    num += 1
                logger.debug("[AUTO-CREATE-EXEC] spec='auto' -> %s (skipped %s used numbers)", num, num - 1)
                return num

            # Check for range format "min-max"
            match = re.match(r"^(\d+)-(\d+)$", spec)
            if match:
                min_num = int(match.group(1))
                max_num = int(match.group(2))
                for num in range(min_num, max_num + 1):
                    if num not in self._used_channel_numbers:
                        logger.debug("[AUTO-CREATE-EXEC] spec='%s' (range) -> %s", spec, num)
                        return num
                # Range exhausted, use next after max
                logger.debug("[AUTO-CREATE-EXEC] spec='%s' range exhausted -> %s", spec, max_num + 1)
                return max_num + 1

            # Try parsing as int — auto-increment from this starting number
            try:
                num = int(spec)
                while num in self._used_channel_numbers:
                    num += 1
                logger.debug("[AUTO-CREATE-EXEC] spec='%s' (parsed int) -> %s", spec, num)
                return num
            except ValueError:
                logger.debug("[AUTO-CREATE-EXEC] Non-numeric channel number spec %r, falling back to auto", spec)

        # Fallback to auto
        num = 1
        while num in self._used_channel_numbers:
            num += 1
        logger.debug("[AUTO-CREATE-EXEC] spec=%r (fallback auto) -> %s", spec, num)
        return num
