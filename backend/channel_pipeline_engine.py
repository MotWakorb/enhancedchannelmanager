"""
Auto-Creation Rules Engine

The main orchestrator for the auto-creation pipeline. Coordinates:
- Loading and prioritizing rules
- Fetching streams from M3U accounts
- Evaluating conditions against streams
- Executing actions when conditions match
- Tracking changes for audit and rollback
- Conflict detection and resolution
"""
import asyncio
import json
import logging
import re
import resource
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

import safe_regex
import journal
from sqlalchemy import or_
from config import (
    DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN,
    DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES,
    get_settings,
)
from database import get_session
from models import (
    ChannelPipelineRule,
    ChannelPipelineExecution,
    ChannelPipelineConflict,
    ChannelPipelineSnapshot,
    StreamStats
)
from channel_pipeline_schema import (
    Action,
    ActionType,
)
from channel_pipeline_evaluator import (
    ConditionEvaluator,
    StreamContext,
)
from channel_pipeline_executor import (
    ActionExecutor,
    ExecutionContext,
)
from channel_pipeline_sort import sort_channels_by_name


logger = logging.getLogger(__name__)


# enhancedchannelmanager-ti939.2.1 (Event Sync Phase 1B): the ONLY
# triggered_by values allowed to execute event_sync rules. Manual-run-only is
# a Phase 1 hard constraint — event_sync rules must NEVER fire from the
# unattended watermark task ("m3u_refresh") or any scheduled path until Phase
# 2's explicit opt-in auto-run flag. Deny-by-default: anything not in this
# set is treated as unattended.
EVENT_SYNC_ALLOWED_TRIGGERS = frozenset({"manual", "api"})

# Deny-by-default sentinel for run_pipeline/run_rule's triggered_by
# (PR #616 review, bead ti939.2.2): a caller that does not IDENTIFY its
# trigger runs as "unspecified", which is deliberately NOT in
# EVENT_SYNC_ALLOWED_TRIGGERS — a future call site that forgets to thread
# triggered_by can never execute event_sync rules by accident. Every
# production caller passes its trigger explicitly (the routers pass "api",
# the watermark task passes "m3u_refresh").
TRIGGERED_BY_UNSPECIFIED = "unspecified"

# Phase 2 (bead ti939.3.1): the ONE unattended trigger that can execute
# event_sync rules — and only for rules whose config carries the explicit
# auto_run=true opt-in. Everything else keeps the deny-by-default posture
# above: "scheduled" and the unspecified sentinel stay denied even for
# opted-in rules.
EVENT_SYNC_AUTO_RUN_TRIGGER = "m3u_refresh"


def event_sync_trigger_allowed(triggered_by: str, config: dict | None) -> bool:
    """Per-rule event_sync trigger gate (ti939.3.1).

    Manual triggers (EVENT_SYNC_ALLOWED_TRIGGERS) are always allowed. The
    refresh-watermark trigger is allowed ONLY when the rule's validated
    ``event_sync_config`` carries the literal ``auto_run: true`` — absent,
    false, or any non-bool value all read as "not opted in", so stored
    configs that predate the flag keep manual-run-only behavior exactly.
    Every other trigger is denied.
    """
    if triggered_by in EVENT_SYNC_ALLOWED_TRIGGERS:
        return True
    if triggered_by == EVENT_SYNC_AUTO_RUN_TRIGGER:
        return bool(config) and config.get("auto_run") is True
    return False

# Upper bound on secondary streams fetched per event_sync rule — a fetch
# guard, not a decision knob (per-stream decisions are independent, so
# truncation only defers later streams to the next idempotent run; it is
# logged loudly).
EVENT_SYNC_MAX_SECONDARY_STREAMS = 5000
_EVENT_SYNC_FETCH_PAGE_SIZE = 500


# Keys carrying the verbose per-stream evaluation trace. Dropped from each
# retained entry on non-dry-run so peak RSS does not scale with
# streams×rules×conditions (bd-sjdsq). The Rule Analyzer re-evaluates rules
# itself and does NOT read the persisted execution_log; rollback uses
# ChannelPipelineSnapshot/created_entities, not the log — so dropping these is
# safe. The retained entry still carries stream_id / stream_name and the
# actions_executed list (which holds created channel entity_ids), so rollback
# and the per-channel audit trail are intact.
_VERBOSE_LOG_KEYS = ("rules_evaluated",)


class BoundedExecutionLog(list):
    """A list that bounds the auto-creation execution_log in memory (bd-sjdsq).

    THE SINGLE CHOKEPOINT for execution_log growth. Every
    ``results["execution_log"].append(...)`` site in the engine and the
    executor funnels through this ``append`` — a list subclass rather than a
    helper function so a missed call site is impossible by construction (any
    append on the object is governed, no matter who holds the reference).

    Two bounds, applied incrementally as entries arrive (NOT a final trim — the
    reporter's docker-stats showed steady growth, so a final trim is too late):

    1. Verbose-trace stripping (non-dry-run only): the per-stream
       ``rules_evaluated`` trace — the dominant memory term — is replaced with
       ``[]`` before the entry is retained. Dry-run keeps the full trace
       (operator wants it for debugging and a dry-run mutates nothing).
    2. Entry cap: at most ``cap`` entries are retained. Once the cap is hit,
       further entries are counted but NOT stored. ``finalize()`` appends a
       single aggregate summary entry ("…and N more") and sets a
       ``log_truncated`` marker so the truncation is never silent.

    ``cap <= 0`` disables the cap (entries still get verbose-stripped on
    non-dry-run). Pass ``verbose=True`` for dry-run.
    """

    def __init__(self, cap: int = 0, verbose: bool = False):
        super().__init__()
        self._cap = cap if cap and cap > 0 else 0
        self._verbose = verbose
        self.retained_count = 0
        self.truncated_count = 0
        self.truncated = False
        self._finalized = False

    def append(self, entry):  # noqa: D102 — see class docstring
        # Strip the verbose per-stream trace on non-dry-run runs. Mutating the
        # dict in place is fine: it is freshly built at each call site and not
        # referenced elsewhere after the append.
        if not self._verbose and isinstance(entry, dict):
            for key in _VERBOSE_LOG_KEYS:
                if entry.get(key):
                    entry[key] = []

        if self._cap and self.retained_count >= self._cap:
            # At cap — count it, drop it. The summary entry is emitted by
            # finalize() so we keep peak memory flat regardless of overflow.
            self.truncated_count += 1
            if not self.truncated:
                self.truncated = True
                logger.warning(
                    "[AUTO-CREATE] execution_log reached the retention cap (%s "
                    "entries); further entries are summarized, not stored "
                    "(bd-sjdsq / GH #473)",
                    self._cap,
                )
            return

        super().append(entry)
        self.retained_count += 1

    def finalize(self):
        """Append the aggregate truncation summary once, if truncated."""
        if self._finalized:
            return
        self._finalized = True
        if self.truncated and self.truncated_count > 0:
            super().append({
                "stream_id": None,
                "stream_name": "[AUTO-CREATE] execution log truncated",
                "m3u_account_id": None,
                "log_truncated": True,
                "rules_evaluated": [],
                "actions_executed": [{
                    "type": "log_truncated",
                    "description": (
                        f"Execution log capped at {self._cap} entries; "
                        f"{self.truncated_count} more matched-stream entries were "
                        f"omitted to bound memory. Raise "
                        f"max_auto_creation_log_entries in Settings to retain more."
                    ),
                    "success": True,
                    "entity_id": None,
                    "error": None,
                }],
            })


class ChannelPipelineEngine:
    """
    Main orchestrator for the auto-creation pipeline.

    Usage:
        engine = ChannelPipelineEngine(dispatcharr_client)

        # Dry run to preview changes
        result = await engine.run_pipeline(dry_run=True)

        # Execute for real
        result = await engine.run_pipeline()

        # Run specific rule
        result = await engine.run_rule(rule_id, dry_run=True)

        # Rollback an execution
        await engine.rollback_execution(execution_id)
    """

    def __init__(self, client):
        """
        Initialize the engine.

        Args:
            client: Dispatcharr API client instance
        """
        self.client = client
        self._existing_channels = None
        self._existing_groups = None
        self._stream_stats_cache = {}
        self._struck_stream_ids = set()

    async def run_pipeline(
        self,
        dry_run: bool = False,
        triggered_by: str = TRIGGERED_BY_UNSPECIFIED,
        m3u_account_ids: list[int] = None,
        rule_ids: list[int] = None,
        execution_id: int | None = None,
    ) -> dict:
        """
        Run the full auto-creation pipeline.

        Args:
            dry_run: If True, only simulate changes without applying
            triggered_by: How the pipeline was triggered (manual, api,
                scheduled, m3u_refresh). Defaults to the DENIED sentinel
                ``TRIGGERED_BY_UNSPECIFIED`` (PR #616 review): an
                unidentified trigger runs standard rules normally but can
                never execute event_sync rules — callers must identify
                themselves to reach the manual-run-only attach phase.
            m3u_account_ids: Optional list of M3U account IDs to process (None = all)
            rule_ids: Optional list of rule IDs to run (None = all enabled)
            execution_id: Optional pre-created execution record id. When the
                router enqueues a background task (bd-enfsy 202+poll pattern)
                it creates an ChannelPipelineExecution(status="running") up front
                so it can return the id to the caller immediately. Passing the
                id here lets the engine reuse that record instead of creating a
                second one — the row stays in "running" while work proceeds and
                is finalized to "completed" / "failed" by this method.

        Returns:
            Dict with execution summary and results
        """
        started_at = datetime.utcnow()
        logger.info("[AUTO-CREATE-ENGINE] Starting auto-creation pipeline (dry_run=%s, triggered_by=%s, execution_id=%s)", dry_run, triggered_by, execution_id)

        # Load existing channels and groups
        await self._load_existing_data()

        # Load enabled rules
        rules = await self._load_rules(rule_ids)

        # ---------------------------------------------------------------------
        # event_sync routing (bead ti939.1.3 exclusion + ti939.2.1 attach path).
        #
        # event_sync rules are a different KIND: they match secondary-provider
        # streams onto Dispatcharr-owned master channels via the isolated
        # matcher service (services/event_sync_matcher.py), not via per-stream
        # condition/action evaluation. They are ALWAYS excluded from Pass
        # 1/Pass 2 evaluation; Pass 4 orphan reconciliation is ALSO
        # hard-bypassed for them (see _reconcile_orphans) — belt and braces,
        # since the filter here already keeps them out of the rules list Pass
        # 4 receives.
        #
        # Phase 1B (ti939.2.1): event_sync rules EXECUTE via the dedicated
        # attach phase (_run_event_sync_rules → executor.execute_event_sync_
        # rule) on manually-triggered runs (EVENT_SYNC_ALLOWED_TRIGGERS).
        # Phase 2 (ti939.3.1): the refresh-watermark trigger is ALSO allowed,
        # per rule, when the rule's config carries the explicit auto_run=true
        # opt-in (event_sync_trigger_allowed). Every other unattended path —
        # "scheduled", anything unrecognized — stays denied even for opted-in
        # rules. The task layer's watermark query selects only opted-in
        # event_sync rules (belt); this per-rule gate is the braces; the
        # phase itself re-checks per rule AND gates the unattended path on
        # the circuit breaker + a pre-flight (suspenders).
        #
        # VERIFIED NON-WORK (do not "fix" these — they were checked):
        # * No bypass of the auto_creation_exclude_auto_sync_groups global
        #   filter is needed for event_sync. Secondary event groups have
        #   auto_channel_sync OFF, so they already pass the filter built in
        #   _apply_global_filters (the auto_sync_group_ids set below, ~L1017-
        #   1063 collects only auto-sync-ON groups). The MASTER group's
        #   streams never need to enter the pipeline at all — Dispatcharr has
        #   already attached them to the master channels it owns.
        # * Known edge, documented rather than coded around: Dispatcharr
        #   groups are GLOBAL BY NAME (bd-dgs64, config.py ~L104-117). A
        #   secondary provider publishing the SAME group name as the master
        #   would share the group ID and be excluded by that filter. Real
        #   event groups are provider-distinct-named; the pre-flight check
        #   (services/event_sync_preflight.py) surfaces the misconfiguration.
        # ---------------------------------------------------------------------
        event_sync_rules = [r for r in rules if r.is_event_sync()]
        event_sync_to_run: list = []
        if event_sync_rules:
            rules = [r for r in rules if not r.is_event_sync()]
            event_sync_to_run = [
                r for r in event_sync_rules
                if event_sync_trigger_allowed(
                    triggered_by, r.get_event_sync_config())
            ]
            excluded = [
                r for r in event_sync_rules if r not in event_sync_to_run
            ]
            if event_sync_to_run:
                logger.info(
                    "[AUTO-CREATE-ENGINE] %s event_sync rule(s) excluded from "
                    "Pass 1/2 evaluation; will run via the event_sync attach "
                    "phase (triggered_by=%s): %s",
                    len(event_sync_to_run), triggered_by,
                    [r.id for r in event_sync_to_run],
                )
            if excluded:
                logger.info(
                    "[AUTO-CREATE-ENGINE] Excluded %s event_sync rule(s) from "
                    "this run entirely — event_sync is manual-run-only unless "
                    "the rule's config opts into auto_run, and even then only "
                    "for the refresh-watermark trigger (triggered_by=%s): %s",
                    len(excluded), triggered_by,
                    [r.id for r in excluded],
                )

        if not rules and not event_sync_to_run:
            if event_sync_rules:
                message = (
                    "Only event_sync rules are in scope and none is allowed "
                    "for this trigger — event_sync rules are manual-run-only "
                    "unless event_sync_config.auto_run is true, and auto_run "
                    "admits only the refresh-watermark trigger"
                )
            else:
                message = "No active enabled rules to process"
            logger.info("[AUTO-CREATE-ENGINE] %s", message)
            # If a pre-created execution exists, mark it completed so it does
            # not stay in "running" forever (otherwise the frontend poll would
            # spin indefinitely on a no-op run).
            if execution_id is not None:
                await self._finalize_no_op_execution(execution_id)
            return {
                "success": True,
                "message": message,
                "streams_evaluated": 0,
                "streams_matched": 0
            }

        # Detect rules referencing DISABLED/missing normalization groups
        # (enhancedchannelmanager-e8p1h). Additive: logs a WARNING and carries
        # the warnings into the run summary so the operator sees that
        # normalization silently applied nothing. Does NOT change behavior.
        normalization_warnings = (
            await self._detect_disabled_normalization_group_warnings(rules)
        )

        # Fetch streams from M3U accounts. Skipped when ONLY event_sync rules
        # run (ti939.2.1): the event_sync attach phase fetches its OWN
        # secondary-group streams, and _fetch_streams with an empty rules
        # list would pull every account's full catalog for nothing.
        if rules:
            streams = await self._fetch_streams(m3u_account_ids, rules)
        else:
            streams = []
        logger.info("[AUTO-CREATE-ENGINE] Fetched %s streams to evaluate against %s rules", len(streams), len(rules))

        # Enrich streams with channel_id from existing channels
        # (Dispatcharr stream API doesn't return channel association)
        stream_to_channel = {}
        for ch in (self._existing_channels or []):
            ch_id = ch.get("id")
            for s in ch.get("streams", []):
                sid = s["id"] if isinstance(s, dict) else s
                stream_to_channel[sid] = (ch_id, ch.get("name"))
        enriched = 0
        for ctx in streams:
            if not ctx.channel_id and ctx.stream_id in stream_to_channel:
                ctx.channel_id, ctx.channel_name = stream_to_channel[ctx.stream_id]
                enriched += 1
        if enriched:
            logger.info("[AUTO-CREATE-ENGINE] Enriched %s streams with channel associations", enriched)

        # Apply global exclusion filters
        streams, exclusion_log = await self._apply_global_filters(streams)

        # Reuse pre-created execution record (bd-enfsy 202+poll path) when
        # the router has supplied an id; otherwise create one ourselves
        # (synchronous /tasks/scheduled path remains unchanged).
        if execution_id is not None:
            execution = await self._load_execution(execution_id)
            if execution is None:
                # The pre-created row was deleted between enqueue and run —
                # fall back to creating a fresh one so the run still records
                # something sensible.
                logger.warning(
                    "[AUTO-CREATE-ENGINE] execution_id=%s not found at run time; "
                    "creating a new execution record",
                    execution_id,
                )
                execution = await self._create_execution(
                    mode="dry_run" if dry_run else "execute",
                    triggered_by=triggered_by,
                )
        else:
            execution = await self._create_execution(
                mode="dry_run" if dry_run else "execute",
                triggered_by=triggered_by
            )

        # ADR-010 §D2: capture a pre-run snapshot of the manual
        # (non-Dispatcharr-auto-created) channel<->stream state BEFORE any
        # mutation, so a full whole-run revert is possible. Gated on
        # ``not dry_run`` (mode=="execute") — a dry run mutates nothing, so
        # there is nothing to revert and a snapshot would only consume
        # storage. This runs AFTER _load_existing_data (the in-memory channel
        # list is already populated — no N+1) and BEFORE _process_streams
        # (the single call that performs all mutation).
        #
        # ti939.2.1: when event_sync rules will run, their MASTER groups'
        # channels are INCLUDED in the snapshot even though they are
        # Dispatcharr-auto-created (normally §D3-excluded) — the attach phase
        # mutates their stream lists, so rollback/restore needs their pre-run
        # state to undo attaches.
        if not dry_run:
            event_sync_master_group_ids: set[int] = set()
            for r in event_sync_to_run:
                cfg = r.get_event_sync_config()
                if cfg and cfg.get("master_group_id") is not None:
                    event_sync_master_group_ids.add(cfg["master_group_id"])
            await self._capture_snapshot(
                execution.id,
                include_auto_created_group_ids=event_sync_master_group_ids,
            )

        # Process streams through rules (+ the event_sync attach phase,
        # ti939.2.1 — runs inside _process_streams so its merges share the
        # same executor, journal buffer/flush and results plumbing).
        results = await self._process_streams(
            streams, rules, execution, dry_run, triggered_by=triggered_by,
            event_sync_rules=event_sync_to_run,
        )

        # Prepend exclusion log entries and set streams_excluded count
        results["execution_log"] = exclusion_log + results["execution_log"]
        results["streams_excluded"] = len(exclusion_log)

        # Finalize execution record
        completed_at = datetime.utcnow()
        execution.completed_at = completed_at
        execution.duration_seconds = (completed_at - started_at).total_seconds()
        # bd-h2xnl: a capped run is a distinct terminal status so the execution
        # record (and the UI/alerts that read it) surface "capped at N of M"
        # rather than masquerading as a clean completion. error_message carries
        # the would-have-been M so the operator sees the full picture.
        if results.get("capped"):
            execution.status = "capped"
            would = results.get("channels_created", 0) + results.get("cap_would_create", 0)
            execution.error_message = (
                f"Created-channel cap reached: created "
                f"{results.get('channels_created', 0)} of ~{would} matched; "
                f"{results.get('cap_would_create', 0)} stream(s) not processed. "
                f"Auto-creation is idempotent — run it again to continue from "
                f"where it stopped (the already-created channels persist), or "
                f"raise the cap in Settings > Auto Creation."
            )
        else:
            execution.status = "completed"
        execution.streams_evaluated = results["streams_evaluated"]
        execution.streams_matched = results["streams_matched"]
        execution.channels_created = results["channels_created"]
        execution.channels_updated = results["channels_updated"]
        execution.groups_created = results["groups_created"]
        execution.streams_merged = results["streams_merged"]
        # bd-0emgo.4: persist distinct-channels-merged so the polled execution
        # record (what the MCP/API surface read) reports it. Without a column it
        # was computed in-memory but dropped on save, so a dry-run reported
        # streams_merged=26 but channels_touched=0.
        execution.channels_touched = results.get("channels_touched", 0)
        execution.streams_skipped = results["streams_skipped"]
        execution.streams_excluded = results.get("streams_excluded", 0)
        execution.set_created_entities(results["created_entities"])
        execution.set_modified_entities(results["modified_entities"])
        execution.set_execution_log(results["execution_log"])
        # Persist disabled-normalization-group warnings so the polled record and
        # the executions UI can surface them (enhancedchannelmanager-e8p1h).
        # ti939.2.1: event_sync run warnings (attach-cap overage, skipped
        # invalid configs) ride the same surface — popped from results so the
        # transient list is not duplicated on the API response (the persisted
        # record and the structured results["event_sync"] summaries carry it).
        run_warnings = list(normalization_warnings) + results.pop(
            "event_sync_warnings", []
        )
        execution.set_warnings(run_warnings)

        # enhancedchannelmanager-7wuhd: persist the structured event_sync
        # per-rule summaries + the pure-event_sync kind flag so the executions
        # UI renders an event_sync-aware summary (secondary streams evaluated /
        # attached / already-attached / ambiguous / unmatched / parse failures)
        # instead of the standard evaluated/matched/created counters, which are
        # structurally 0 for event_sync runs. The heavy ``review_candidates``
        # payloads are stripped — they are already enqueued to the review table
        # on live runs and the UI needs only the counters. A run is PURE
        # event_sync when event_sync rule(s) ran and NO standard rules were in
        # scope; a mixed run keeps ``is_event_sync`` False so the UI stacks both
        # summary blocks.
        event_sync_summaries = [
            {k: v for k, v in summary.items() if k != "review_candidates"}
            for summary in results.get("event_sync", [])
        ]
        execution.set_event_sync_summary(event_sync_summaries)
        execution.is_event_sync = bool(event_sync_to_run) and not bool(rules)

        if dry_run:
            execution.set_dry_run_results(results["dry_run_results"])

        await self._save_execution(execution)

        # Update rule stats. event_sync rules that ran get last_run_at /
        # match_count too (ti939.2.1) — _run_event_sync_rules recorded their
        # attach counts in results["rule_match_counts"].
        if not dry_run:
            await self._update_rule_stats(rules + event_sync_to_run, results)

        removed = results.get('channels_removed', 0)
        moved = results.get('channels_moved', 0)
        orphan_info = ""
        if removed:
            orphan_info = f", {removed} orphans removed"
        if moved:
            orphan_info += f", {moved} orphans moved"
        logger.info(
            "[AUTO-CREATE-ENGINE] Pipeline completed: %s/%s streams matched, "
            "%s channels created, %s updated%s",
            results['streams_matched'], results['streams_evaluated'],
            results['channels_created'], results['channels_updated'], orphan_info
        )

        return {
            "success": True,
            "execution_id": execution.id,
            "mode": execution.mode,
            "duration_seconds": execution.duration_seconds,
            "normalization_warnings": normalization_warnings,
            **results
        }

    async def run_rule(
        self,
        rule_id: int,
        dry_run: bool = False,
        triggered_by: str = TRIGGERED_BY_UNSPECIFIED,
        execution_id: int | None = None,
    ) -> dict:
        """
        Run a specific rule.

        Args:
            rule_id: ID of the rule to run
            dry_run: If True, only simulate changes
            triggered_by: How the rule was triggered. Defaults to the DENIED
                sentinel ``TRIGGERED_BY_UNSPECIFIED`` — see
                :meth:`run_pipeline`.
            execution_id: Optional pre-created execution record id, threaded
                through to ``run_pipeline`` (see its docstring for the
                bd-enfsy 202+poll background-task pattern).

        Returns:
            Dict with execution summary
        """
        return await self.run_pipeline(
            dry_run=dry_run,
            triggered_by=triggered_by,
            rule_ids=[rule_id],
            execution_id=execution_id,
        )

    async def rollback_execution(
        self,
        execution_id: int,
        rolled_back_by: str = "manual",
        confirm: bool = False,
    ) -> dict:
        """
        Rollback changes from a specific execution (ADR-010 §D8, uc51o.5).

        UNIFIED REVERT. This is the single revert entry point and chooses its
        behaviour from whether the execution has a pre-run snapshot:

        * **Snapshot present** — requires ``confirm=True`` (the same
          acknowledgement the ``/restore-snapshot`` endpoint demands); without
          it, the call is refused with ``requires_confirm=True`` and
          ``has_snapshot=True`` so the router can surface the overwrite
          warning (HTTP 409). Once confirmed, the revert mechanism is chosen
          by journal coverage (bead sfysz):

          - When the journal PROVES every mutation the run made was an
            event_sync stream attach (category=event_sync merge_stream
            entries with batch_id=execution id fully covering the run's
            modified entities — see :meth:`_event_sync_journal_covers_run`),
            the SURGICAL :meth:`_journal_driven_unmerge` runs instead:
            it removes ONLY the stream ids the run added from each master's
            CURRENT stream list, preserving Dispatcharr's own master-stream
            churn between run and rollback. Dispatcharr never resets stream
            lists, so a stale id written by a full-replace would NOT
            self-heal — the full restore is strictly worse whenever the
            surgical revert suffices. Returns the legacy
            ``entities_removed``/``entities_restored`` shape plus
            ``surgical_unmerge: True``.
          - Otherwise (standard-rule runs, mixed runs, legacy runs,
            missing/partial journal), delegates to :meth:`restore_snapshot`
            for the FULL whole-run revert (re-adds streams the run removed,
            removes streams it added, restores drifted metadata) — an
            OPTIMISTIC OVERWRITE (ADR-010 §D5) that can clobber edits made
            AFTER the run, which is exactly why the confirm gate above is
            required either way.

        * **No snapshot** — the legacy delete-created-only path, BYTE-COMPATIBLE
          with the pre-uc51o.5 behaviour: deletes run-created entities, prefers
          the surgical journal-driven un-merge, else restores ``modified``
          entities. It does NOT require ``confirm`` (true backward compat for
          legacy / dry-run-then-claimed / capture-failure runs that have no
          snapshot to overwrite from).

        Args:
            execution_id: ID of the execution to rollback
            rolled_back_by: Who/what initiated the rollback
            confirm: Acknowledgement of the optimistic-overwrite warning. Only
                consulted when a snapshot is present; ignored on the legacy
                no-snapshot path.

        Returns:
            Dict with rollback results. The snapshot path returns the
            :meth:`restore_snapshot` shape (``removed_channels`` /
            ``restored_channels`` / ``failed_channels``); the legacy path
            returns the unchanged ``entities_removed`` / ``entities_restored``
            shape.
        """
        session = get_session()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == execution_id
            ).first()

            if not execution:
                return {"success": False, "error": "Execution not found"}

            if execution.status == "rolled_back":
                return {"success": False, "error": "Execution already rolled back"}

            if execution.mode == "dry_run":
                return {"success": False, "error": "Cannot rollback a dry-run execution"}

            # --- uc51o.5: unify on the snapshot when one exists ---------------
            # If this execution has a pre-run snapshot, the FULL restore is the
            # right revert (the legacy path cannot re-add streams the run
            # removed — ADR-010 Context). Because restore is an optimistic
            # overwrite that can clobber post-run edits (§D5), it requires the
            # SAME confirm acknowledgement as /restore-snapshot. Refuse without
            # it rather than silently widening the blast radius; the no-snapshot
            # path below is untouched and keeps its no-confirm semantics.
            has_snapshot = session.query(ChannelPipelineSnapshot.id).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first() is not None

            if has_snapshot:
                if not confirm:
                    logger.info(
                        "[AUTO-CREATE-ENGINE] Rollback of execution %s has a "
                        "snapshot; refusing without confirm (would overwrite "
                        "post-run edits)",
                        execution_id,
                    )
                    return {
                        "success": False,
                        "has_snapshot": True,
                        "requires_confirm": True,
                        "error": (
                            f"Execution {execution_id} has a pre-run snapshot, "
                            f"so rollback performs a FULL restore that "
                            f"overwrites the current stream assignments of "
                            f"every snapshot channel with the pre-run state — "
                            f"any changes made after the run will be lost. "
                            f"Re-send with confirm=true (or use "
                            f"/restore-snapshot) to acknowledge."
                        ),
                    }
                # Confirmed. Prefer the SURGICAL journal-driven unmerge when
                # the journal fully covers the run's attaches (bead sfysz):
                # it removes ONLY the run-added stream ids and preserves
                # Dispatcharr's own master-stream churn between run and
                # rollback, which the snapshot full-replace would clobber
                # with stale ids Dispatcharr never self-heals.
                if self._event_sync_journal_covers_run(execution):
                    handled, touched = await self._journal_driven_unmerge(
                        execution_id
                    )
                    if handled:
                        execution.status = "rolled_back"
                        execution.rolled_back_at = datetime.utcnow()
                        execution.rolled_back_by = rolled_back_by
                        session.commit()
                        logger.info(
                            "[AUTO-CREATE-ENGINE] Rollback of execution %s "
                            "completed via surgical journal-driven unmerge "
                            "(%s channel(s) touched; snapshot restore "
                            "skipped)",
                            execution_id, touched,
                        )
                        return {
                            "success": True,
                            "execution_id": execution_id,
                            "rule_name": (execution.rule_name
                                          or f"Execution {execution_id}"),
                            "entities_removed": 0,
                            "entities_restored": touched,
                            "surgical_unmerge": True,
                        }
                    # Coverage said yes but the entries vanished between the
                    # check and the unmerge read — fall through to the full
                    # restore rather than declaring a rollback that did
                    # nothing.
                    logger.warning(
                        "[AUTO-CREATE-ENGINE] Surgical unmerge of execution "
                        "%s found no journal entries after a positive "
                        "coverage check — falling back to snapshot restore",
                        execution_id,
                    )
                # Delegate to the full snapshot-restore. Close this session
                # first — restore_snapshot opens its own.
                logger.info(
                    "[AUTO-CREATE-ENGINE] Rollback of execution %s delegating "
                    "to snapshot-restore (snapshot present, confirmed)",
                    execution_id,
                )
                session.close()
                return await self.restore_snapshot(
                    execution_id, restored_by=rolled_back_by
                )

            logger.info("[AUTO-CREATE-ENGINE] Rolling back execution %s", execution_id)

            created = execution.get_created_entities()
            modified = execution.get_modified_entities()

            # Refuse (rather than silently no-op) when there is nothing to undo.
            # An execution with ZERO created AND ZERO modified entities has no
            # recorded restore data — either it predates entity tracking (a
            # legacy run) or it genuinely changed nothing. Marking such a run
            # "rolled_back" with entities_removed=0/entities_restored=0 LOOKS
            # like a clean rollback but guarantees nothing. Leave status
            # untouched and tell the caller why. Runs that created OR modified
            # anything fall through and roll back normally (only the both-empty
            # case refuses); the already-rolled-back and dry-run guards above
            # still take precedence.
            if not created and not modified:
                logger.warning(
                    "[AUTO-CREATE-ENGINE] Refusing rollback of execution %s: "
                    "no recorded created or modified entities",
                    execution_id,
                )
                return {
                    "success": False,
                    "error": (
                        f"No recorded created or modified entities for execution "
                        f"{execution_id}; cannot guarantee a rollback (the run "
                        f"predates entity tracking or made no changes). Refusing "
                        f"to mark it rolled_back."
                    ),
                }

            # Rollback created entities (in reverse order)
            for entity in reversed(created):
                await self._rollback_created_entity(entity)

            # jnzst Q4: prefer the SURGICAL journal-driven un-merge for stream
            # merges — it removes ONLY the stream IDs this run added and
            # preserves streams a concurrent edit added afterward. It is
            # attempted first; if the run has merge_stream journal entries it
            # handles all the stream-list changes and the snapshot restore below
            # is SKIPPED (running it too would clobber the concurrent edits the
            # surgical pass just preserved). When the journal has no merge
            # entries for the batch (legacy runs), handled is False and we fall
            # back to the snapshot restore.
            handled, _surgical_touched = await self._journal_driven_unmerge(execution_id)

            if not handled:
                # Restore modified entities in REVERSE order (bd-a7okb). Restore is
                # last-write-wins: _rollback_modified_entity overwrites the channel
                # from each entry's pre-change `previous` snapshot. When a run merged
                # several streams into the SAME pre-existing channel, the snapshots
                # are cumulative ([A] before B, [A,B] before C, ...). Forward order
                # would apply the earliest (true original) first and let a later
                # snapshot win, leaving all-but-the-last merged stream behind.
                # Reversing makes the earliest snapshot win — restoring the original
                # — exactly as created-entity teardown is reversed above.
                for entity in reversed(modified):
                    await self._rollback_modified_entity(entity)

            # Mark execution as rolled back
            execution.status = "rolled_back"
            execution.rolled_back_at = datetime.utcnow()
            execution.rolled_back_by = rolled_back_by
            session.commit()

            logger.info("[AUTO-CREATE-ENGINE] Rollback complete: %s created entities removed, %s entities restored", len(created), len(modified))

            return {
                "success": True,
                "execution_id": execution_id,
                "rule_name": execution.rule_name or f"Execution {execution_id}",
                "entities_removed": len(created),
                "entities_restored": len(modified)
            }

        except Exception as e:
            session.rollback()
            logger.error("[AUTO-CREATE-ENGINE] Rollback failed: %s", e)
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    async def restore_snapshot(self, execution_id: int, restored_by: str = "manual") -> dict:
        """Whole-run revert via the pre-run ChannelPipelineSnapshot (ADR-010 §D8).

        SAFETY-CRITICAL: this mutates live Dispatcharr channels. It performs an
        OPTIMISTIC OVERWRITE (ADR-010 §D5) — it unconditionally writes the
        snapshot's stream-set + key metadata back to each channel, clobbering
        ANY changes made (manual edits, Dispatcharr drift) AFTER the run but
        BEFORE this revert. The caller MUST surface the §D5 pre-revert warning;
        the endpoint requires an explicit ``confirm=true`` so a raw API call
        cannot skip the acknowledgement.

        Algorithm (ADR-010 §D8):
          1. Load the snapshot for ``execution_id`` (404-equivalent — returns
             ``{"success": False, "error": ...}`` with a ``no_snapshot`` flag so
             the router can map to 404 and tell the caller to use /rollback).
             Refuse a dry-run execution or one already in a terminal revert
             state, mirroring the rollback guards.
          2. Delete run-CREATED channels/groups first (via the existing
             ``_rollback_created_entity`` path) so channels that did not exist
             at snapshot time do not survive as orphans.
          3. For each snapshot channel, full-REPLACE its stream set
             (``update_channel(streams=[ids])`` — the §D1 IDs-only primitive)
             and restore key metadata (name, channel_group_id, epg_data_id,
             tvg_id) in one PATCH. Channels flagged ``event_sync_master`` at
             capture time get a STREAMS-ONLY payload instead (bead
             ti939.2.3): Dispatcharr owns their metadata, and the attach run
             only ever changed their stream list.
          4. Idempotent: re-running re-issues the same PATCHes / deletes →
             same end state. A second call after a clean first call finds the
             created channels already gone (delete 404 swallowed by
             ``_rollback_created_entity``) and re-writes the same stream-sets.
          5. Partial failures are COLLECTED and SURFACED, never silent and
             never fatal mid-run: a snapshot channel that 404s in Dispatcharr
             (deleted since the run) or a metadata/stream write that fails is
             recorded in ``failed_channels`` and the loop CONTINUES. The result
             reports ``restored_channels`` / ``removed_channels`` counts plus
             the per-item ``failed_channels`` list. ``success`` is True only
             when nothing failed; a run that failed on some items returns
             ``success=False`` (success-with-warnings) carrying the failures —
             NEVER a blanket success that hides them.

        Args:
            execution_id: ID of the execution whose pre-run state to restore.
            restored_by: Who/what initiated the restore (recorded on the row).

        Returns:
            On no-snapshot / guard failure: ``{"success": False, "error": ...,
            "no_snapshot": bool}``. On a restore attempt: ``{"success": bool,
            "execution_id", "rule_name", "removed_channels", "restored_channels",
            "failed_channels": [{"id", "name", "error"}]}``.
        """
        session = get_session()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == execution_id
            ).first()

            if not execution:
                return {"success": False, "error": "Execution not found"}

            if execution.mode == "dry_run":
                # A dry run mutates nothing and has no snapshot anyway.
                return {
                    "success": False,
                    "error": "Cannot restore a dry-run execution",
                }

            if execution.status == "rolled_back":
                return {
                    "success": False,
                    "error": "Execution already reverted",
                }

            snapshot = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()

            if not snapshot:
                # No snapshot to restore from — the caller should use the legacy
                # /rollback path (delete-created-only) instead. Flagged so the
                # router can map to 404 with that guidance.
                return {
                    "success": False,
                    "no_snapshot": True,
                    "error": (
                        f"No snapshot for execution {execution_id}; use "
                        f"/rollback instead (this run predates snapshotting, "
                        f"was a dry-run, or its capture failed)."
                    ),
                }

            logger.info(
                "[AUTO-CREATE-ENGINE] Restoring snapshot for execution %s "
                "(OPTIMISTIC OVERWRITE — post-run edits will be lost)",
                execution_id,
            )

            # --- Step 2: delete run-created channels/groups first -------------
            # Reuse the rollback's created-entity teardown verbatim so a
            # channel that did NOT exist at snapshot time (and therefore is not
            # in the snapshot) is removed rather than left as an orphan. Deletes
            # in reverse order, mirroring rollback. _rollback_created_entity
            # swallows a 404 (already-deleted) so a SECOND restore is safe.
            created = execution.get_created_entities()
            removed_channels = 0
            for entity in reversed(created):
                await self._rollback_created_entity(entity)
                if entity.get("type") == "channel":
                    removed_channels += 1

            # --- Step 3-5: full-replace each snapshot channel -----------------
            channels = snapshot.get_channels_data().get("channels", [])
            restored_channels = 0
            failed_channels: list[dict] = []

            for ch in channels:
                channel_id = ch.get("id")
                channel_name = ch.get("name")
                try:
                    # Full-REPLACE primitive (§D1 IDs-only) + key metadata in
                    # one PATCH. Including ``streams`` makes update_channel
                    # overwrite the entire stream set to the snapshot order;
                    # the metadata keys restore drift on name / group / epg /
                    # tvg.
                    #
                    # EXCEPT event_sync master channels (flagged at capture
                    # time — PR #616 review / bead ti939.2.3): Dispatcharr
                    # owns their metadata and updates it in place across
                    # refreshes (slot renames, EPG re-links). The attach run
                    # only ever changed their STREAM list, so the restore
                    # writes back ONLY the stream list — restoring the
                    # captured metadata would revert Dispatcharr-owned state
                    # to stale pre-run values, and Dispatcharr is not
                    # guaranteed to self-heal until the source stream next
                    # changes. Unflagged channels (every standard-rule
                    # snapshot, all pre-flag legacy snapshots) keep the
                    # full-payload behavior byte-identical.
                    if ch.get("event_sync_master"):
                        payload = {
                            "streams": list(ch.get("stream_ids") or []),
                        }
                    else:
                        payload = {
                            "streams": list(ch.get("stream_ids") or []),
                            "name": channel_name,
                            "channel_group_id": ch.get("channel_group_id"),
                            "epg_data_id": ch.get("epg_data_id"),
                            "tvg_id": ch.get("tvg_id"),
                        }
                    await self.client.update_channel(channel_id, payload)
                    restored_channels += 1
                except Exception as e:
                    # Collect-and-continue: a deleted channel (404), a
                    # vanished referenced stream id (IDs-only can't recreate a
                    # deleted stream — ADR-010 negative consequence #2), or any
                    # write error is recorded and the loop proceeds. NEVER
                    # abort on the first failure; NEVER report blanket success.
                    logger.warning(
                        "[AUTO-CREATE-ENGINE] Restore failed for channel %s (%s): %s",
                        channel_id, channel_name, e,
                    )
                    failed_channels.append({
                        "id": channel_id,
                        "name": channel_name,
                        "error": str(e),
                    })

            # --- Step 7: mark terminal state + return -------------------------
            # Share the ``rolled_back`` terminal state with the legacy rollback
            # (ADR-010 §D8 step 7 leaves the exact name to this bead; reusing
            # the existing state keeps the idempotency guard — already-reverted
            # → refuse — consistent across both revert surfaces). Only mark
            # terminal when NOTHING failed, so a partial restore stays
            # re-runnable (idempotent retry after a partial failure, §D5).
            if not failed_channels:
                execution.status = "rolled_back"
                execution.rolled_back_at = datetime.utcnow()
                execution.rolled_back_by = restored_by
            session.commit()

            logger.info(
                "[AUTO-CREATE-ENGINE] Restore complete for execution %s: "
                "%s created channel(s) removed, %s channel(s) restored, "
                "%s failed",
                execution_id, removed_channels, restored_channels,
                len(failed_channels),
            )

            return {
                # success-with-warnings: False when any item failed so the
                # caller never mistakes a partial restore for a clean one.
                "success": not failed_channels,
                "execution_id": execution_id,
                "rule_name": execution.rule_name or f"Execution {execution_id}",
                "removed_channels": removed_channels,
                "restored_channels": restored_channels,
                "failed_channels": failed_channels,
            }

        except Exception as e:
            session.rollback()
            logger.error("[AUTO-CREATE-ENGINE] Restore failed: %s", e)
            return {"success": False, "error": str(e)}
        finally:
            session.close()

    # =========================================================================
    # Data Loading
    # =========================================================================

    async def _load_existing_data(self):
        """Load existing channels and groups from Dispatcharr."""
        try:
            # get_channels() returns paginated dict {"count": N, "results": [...]}
            # Fetch all pages
            all_channels = []
            page = 1
            while True:
                result = await self.client.get_channels(page=page, page_size=100)
                channels = result.get("results", [])
                all_channels.extend(channels)
                if len(all_channels) >= result.get("count", 0) or not channels:
                    break
                page += 1
            self._existing_channels = all_channels

            # get_channel_groups() returns a flat list
            self._existing_groups = await self.client.get_channel_groups() or []
            logger.debug("[AUTO-CREATE-ENGINE] Loaded %s channels, %s groups", len(self._existing_channels), len(self._existing_groups))
            if self._existing_channels:
                channel_names = [c.get("name", "<no name>") for c in self._existing_channels]
                logger.debug("[AUTO-CREATE-ENGINE] Existing channel names: %s", channel_names)
        except Exception as e:
            logger.exception("[AUTO-CREATE-ENGINE] Failed to load existing data: %s", e)
            self._existing_channels = []
            self._existing_groups = []

    async def _load_rules(self, rule_ids: list[int] = None) -> list[ChannelPipelineRule]:
        """Load enabled rules active on today's UTC calendar date."""
        session = get_session()
        try:
            today = datetime.utcnow().date()
            query = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.enabled == True,
                or_(
                    ChannelPipelineRule.active_from.is_(None),
                    ChannelPipelineRule.active_from <= today,
                ),
                or_(
                    ChannelPipelineRule.active_until.is_(None),
                    ChannelPipelineRule.active_until >= today,
                ),
            )

            if rule_ids:
                query = query.filter(ChannelPipelineRule.id.in_(rule_ids))

            rules = query.order_by(ChannelPipelineRule.priority).all()
            for r in rules:
                logger.debug(
                    "[AUTO-CREATE-ENGINE] Rule id=%s name=%r priority=%s "
                    "m3u_account_id=%s sort_field=%s "
                    "stop_on_first_match=%s",
                    r.id, r.name, r.priority,
                    r.m3u_account_id, r.sort_field,
                    r.stop_on_first_match
                )
            return rules

        finally:
            session.close()

    async def _detect_disabled_normalization_group_warnings(
        self, rules: list[ChannelPipelineRule]
    ) -> list[dict]:
        """Detect rules whose ``normalization_group_ids`` reference DISABLED or
        missing normalization groups (enhancedchannelmanager-e8p1h).

        When a rule selects normalization groups that are globally disabled (or
        no longer exist), every ``[NORMALIZE]`` decision applies nothing — the
        rule's prefixes/suffixes are never stripped and ``merge_streams
        target:auto`` matches almost nothing — yet the run otherwise looks
        clean. This surfaces the problem so the operator knows to enable the
        groups. It is ADDITIVE detection only: it does NOT change what gets
        normalized or merged.

        Returns a list of warning dicts, one per affected rule::

            {"rule_id": int, "rule_name": str,
             "disabled_groups": [{"id": int, "name": str|None,
                                  "missing": bool}]}
        """
        # Only rules that actually reference a normalization group can be
        # affected — short-circuit otherwise so healthy configs do no DB work.
        referenced_ids = set()
        for r in rules:
            referenced_ids.update(r.get_normalization_group_ids() or [])
        if not referenced_ids:
            return []

        from models import NormalizationRuleGroup
        session = get_session()
        try:
            groups = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id.in_(referenced_ids)
            ).all()
            # id -> (name, enabled); ids absent from this map no longer exist.
            group_state = {g.id: (g.name, bool(g.enabled)) for g in groups}
        finally:
            session.close()

        warnings: list[dict] = []
        for r in rules:
            ids = r.get_normalization_group_ids() or []
            problem_groups = []
            for gid in ids:
                if gid not in group_state:
                    problem_groups.append({"id": gid, "name": None, "missing": True})
                elif not group_state[gid][1]:  # exists but disabled
                    problem_groups.append({
                        "id": gid,
                        "name": group_state[gid][0],
                        "missing": False,
                    })
            if problem_groups:
                names = ", ".join(
                    g["name"] or f"#{g['id']}" for g in problem_groups
                )
                logger.warning(
                    "[AUTO-CREATE-ENGINE] Rule id=%s name=%r references "
                    "disabled/missing normalization group(s): %s — "
                    "normalization will apply NO changes for this rule. "
                    "Enable the group(s) in Settings > Normalization.",
                    r.id, r.name, names,
                )
                warnings.append({
                    "rule_id": r.id,
                    "rule_name": r.name,
                    "disabled_groups": problem_groups,
                })
        return warnings

    async def _fetch_streams(
        self,
        m3u_account_ids: list[int] = None,
        rules: list[ChannelPipelineRule] = None
    ) -> list[StreamContext]:
        """
        Fetch streams from M3U accounts.

        Args:
            m3u_account_ids: Specific accounts to fetch from (None = derive from rules)
            rules: Rules to check for account filtering

        Returns:
            List of StreamContext objects
        """
        # Determine which M3U accounts to fetch
        accounts_to_fetch = set()

        if m3u_account_ids:
            accounts_to_fetch = set(m3u_account_ids)
        elif rules:
            # Check if any rule targets specific accounts
            for rule in rules:
                if rule.m3u_account_id:
                    accounts_to_fetch.add(rule.m3u_account_id)

            # If no specific accounts, fetch all
            if not accounts_to_fetch:
                m3u_accounts = await self.client.get_m3u_accounts() or []
                accounts_to_fetch = {a["id"] for a in m3u_accounts}
        else:
            m3u_accounts = await self.client.get_m3u_accounts() or []
            accounts_to_fetch = {a["id"] for a in m3u_accounts}

        # Fetch streams from each account
        all_streams = []
        m3u_accounts = await self.client.get_m3u_accounts() or []
        account_map = {a["id"]: a for a in m3u_accounts}
        logger.debug("[AUTO-CREATE-ENGINE] Accounts to fetch: %s", accounts_to_fetch)

        # Load stream stats for quality info
        await self._load_stream_stats()

        # Build group name map for enriching stream data
        # (Dispatcharr API returns channel_group as ID, not name)
        group_name_map = {}
        if self._existing_groups:
            group_name_map = {g["id"]: g["name"] for g in self._existing_groups}

        for account_id in accounts_to_fetch:
            account = account_map.get(account_id)
            if not account:
                continue

            try:
                # get_streams() returns paginated dict {"count": N, "results": [...]}
                page = 1
                fetched_for_account = 0
                while True:
                    # page_size=1000 (was 100): auto-creation runs a full
                    # per-account stream sweep right after every M3U refresh, so
                    # the smaller page meant ~10x the requests against Dispatcharr
                    # for no benefit. Matches STREAM_PULL_PAGE_SIZE in the refresh
                    # task (bd-iwfr7).
                    result = await self.client.get_streams(
                        page=page, page_size=1000, m3u_account=account_id
                    )
                    streams = result.get("results", [])
                    for stream in streams:
                        # Enrich with group name (API only returns numeric channel_group ID)
                        group_id = stream.get("channel_group")
                        if group_id and "channel_group_name" not in stream:
                            stream["channel_group_name"] = group_name_map.get(group_id)
                        stats = self._stream_stats_cache.get(stream.get("id"))
                        ctx = StreamContext.from_dispatcharr_stream(
                            stream,
                            m3u_account_id=account_id,
                            m3u_account_name=account.get("name"),
                            stream_stats=stats
                        )
                        all_streams.append(ctx)
                    fetched_for_account += len(streams)
                    total = result.get("count", 0)
                    if fetched_for_account >= total or not streams:
                        break
                    page += 1
            except Exception as e:
                logger.error("[AUTO-CREATE-ENGINE] Failed to fetch streams from M3U account %s: %s", str(account_id).replace('\n', ''), str(e).replace('\n', ''))

        return all_streams

    async def _apply_global_filters(self, streams: list) -> tuple:
        """
        Apply global exclusion filters to streams before rule evaluation.

        Returns:
            (filtered_streams, exclusion_log_entries)
        """
        settings = get_settings()
        excluded_terms = settings.auto_creation_excluded_terms or []
        excluded_groups = settings.auto_creation_excluded_groups or []
        exclude_auto_sync = settings.auto_creation_exclude_auto_sync_groups

        if not excluded_terms and not excluded_groups and not exclude_auto_sync:
            return streams, []

        # Build auto-sync group ID set if needed
        auto_sync_group_ids = set()
        if exclude_auto_sync:
            try:
                all_group_settings = await self.client.get_all_m3u_group_settings()
                for group_id, gs in all_group_settings.items():
                    if gs.get("auto_channel_sync"):
                        auto_sync_group_ids.add(group_id)
                logger.debug("[AUTO-CREATE-ENGINE] Found %s auto-sync group IDs", len(auto_sync_group_ids))
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch auto-sync groups: %s", e)

        # Build word-boundary patterns for case-insensitive matching
        terms_lower = [t.lower() for t in excluded_terms if t]
        terms_with_patterns = [
            (t, re.compile(r'\b' + re.escape(t) + r'\b'))
            for t in terms_lower
        ]
        groups_lower = [g.lower() for g in excluded_groups if g]

        filtered = []
        exclusion_log = []

        for stream in streams:
            reason = None

            # Check excluded terms (case-insensitive word-boundary match
            # against both stream name and group name)
            if terms_lower:
                name_lower = (stream.stream_name or "").lower()
                group_lower = (stream.group_name or "").lower()
                for term, pattern in terms_with_patterns:
                    if pattern.search(name_lower) or pattern.search(group_lower):
                        reason = f"Excluded: matched term '{term}'"
                        break

            # Check excluded groups (case-insensitive exact match)
            if reason is None and groups_lower:
                group_lower = (stream.group_name or "").lower()
                for grp in groups_lower:
                    if group_lower == grp:
                        reason = f"Excluded: group '{stream.group_name}'"
                        break

            # Check auto-sync groups
            if reason is None and auto_sync_group_ids and stream.channel_group_id:
                if stream.channel_group_id in auto_sync_group_ids:
                    reason = "Excluded: auto-sync group"

            if reason:
                logger.debug("[AUTO-CREATE-ENGINE] %s - stream=%r id=%s", reason, stream.stream_name, stream.stream_id)
                exclusion_log.append({
                    "stream_id": stream.stream_id,
                    "stream_name": stream.stream_name,
                    "m3u_account_id": stream.m3u_account_id,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "action": "excluded",
                        "success": True,
                        "description": reason
                    }]
                })
            else:
                filtered.append(stream)

        excluded_count = len(streams) - len(filtered)
        if excluded_count > 0:
            logger.info(
                "[AUTO-CREATE-ENGINE] Excluded %s streams "
                "(%s total -> %s remaining)",
                excluded_count, len(streams), len(filtered)
            )
            if terms_lower:
                logger.info("[AUTO-CREATE-ENGINE]   Terms: %s", excluded_terms)
            if groups_lower:
                logger.info("[AUTO-CREATE-ENGINE]   Groups: %s", excluded_groups)
            if auto_sync_group_ids:
                logger.info("[AUTO-CREATE-ENGINE]   Auto-sync groups: %s groups", len(auto_sync_group_ids))

        return filtered, exclusion_log

    async def _load_stream_stats(self):
        """Load stream stats from database for quality info."""
        session = get_session()
        try:
            stats = session.query(StreamStats).filter(
                StreamStats.probe_status == "success"
            ).all()

            self._stream_stats_cache = {
                s.stream_id: s.to_dict() for s in stats
            }

            # Load struck stream IDs (consecutive_failures >= strike_threshold)
            threshold = get_settings().strike_threshold
            if threshold > 0:
                struck = session.query(StreamStats.stream_id).filter(
                    StreamStats.consecutive_failures >= threshold
                ).all()
                self._struck_stream_ids = {s[0] for s in struck}
                if self._struck_stream_ids:
                    logger.info("[AUTO-CREATE-ENGINE] Loaded %s struck stream IDs (threshold=%s)",
                                len(self._struck_stream_ids), threshold)
            else:
                self._struck_stream_ids = set()
        finally:
            session.close()

    async def _probe_unprobed_streams(
        self,
        matched_entries: list,
        rules: list[ChannelPipelineRule],
        results: dict,
        dry_run: bool
    ):
        """
        Probe streams that haven't been probed yet, for rules that have
        probe_on_sort=True and sort_field='quality'.

        This runs after Pass 1 (match collection) and before sorting,
        so that quality data is available for the sort.
        """
        from stream_prober import get_prober

        # Collect streams that need probing
        rule_map = {r.id: r for r in rules}
        streams_to_probe = {}  # stream_id -> (url, name, stream_ctx)

        for stream, winning_rule, _losing, _log in matched_entries:
            rule = rule_map.get(winning_rule.id)
            if not rule:
                continue
            needs_quality = rule.sort_field == "quality" or getattr(rule, 'stream_sort_field', None) == "quality"
            if not needs_quality or not getattr(rule, 'probe_on_sort', False):
                continue
            # Only probe streams without existing stats
            if stream.stream_id in self._stream_stats_cache:
                continue
            if not stream.stream_url:
                continue
            streams_to_probe[stream.stream_id] = (
                stream.stream_url, stream.stream_name, stream
            )

        if not streams_to_probe:
            return

        prober = get_prober()
        if not prober:
            logger.warning("[AUTO-CREATE-ENGINE] Prober not available, skipping probe step")
            return

        count = len(streams_to_probe)
        logger.info("[AUTO-CREATE-ENGINE] Probing %s unprobed stream(s) for quality sorting", count)

        if dry_run:
            results["dry_run_results"].append({
                "stream_id": None,
                "stream_name": "[AUTO-CREATE-ENGINE]",
                "rule_id": None,
                "rule_name": None,
                "action": f"Would probe {count} unprobed stream(s) for quality data",
                "would_create": False,
                "would_modify": False
            })
            return

        # Probe with concurrency limit
        semaphore = asyncio.Semaphore(3)

        async def probe_one(stream_id, url, name):
            async with semaphore:
                try:
                    await prober.probe_stream(stream_id, url, name)
                except Exception as e:
                    logger.warning("[AUTO-CREATE-ENGINE] Failed to probe stream %s (%s): %s", stream_id, name, e)

        tasks = [
            probe_one(sid, url, name)
            for sid, (url, name, _ctx) in streams_to_probe.items()
        ]
        await asyncio.gather(*tasks)

        # Reload stats cache
        await self._load_stream_stats()

        # Update resolution_height on matched stream contexts
        for stream, _rule, _losing, _log in matched_entries:
            stats = self._stream_stats_cache.get(stream.stream_id)
            if stats and stats.get("resolution"):
                try:
                    parts = stats["resolution"].split("x")
                    if len(parts) == 2:
                        stream.resolution_height = int(parts[1])
                except (ValueError, IndexError) as e:
                    logger.debug("[AUTO-CREATE-ENGINE] Suppressed resolution parse error: %s", e)

        results["execution_log"].append({
            "stream_id": None,
            "stream_name": f"[AUTO-CREATE-ENGINE]",
            "m3u_account_id": None,
            "rules_evaluated": [],
            "actions_executed": [{
                "type": "probe_streams",
                "description": f"Probed {count} unprobed stream(s) for quality sorting",
                "success": True,
                "entity_id": None,
                "error": None
            }]
        })

    async def _reorder_channel_streams(
        self,
        rules: list[ChannelPipelineRule],
        rule_channel_order: dict,
        results: dict,
        dry_run: bool,
        settings=None,
        stream_m3u_map: dict = None,
        custom_stream_ids: set[int] | None = None,
        catchup_stream_ids: set[int] | None = None,
    ):
        """
        Pass 3.5: Reorder streams within channels using smart sort.

        Uses the user's stream_sort_priority, stream_sort_enabled, and
        m3u_account_priorities settings (same logic as stream_prober smart sort).
        Falls back to resolution-only if settings not available.
        """
        if stream_m3u_map is None:
            stream_m3u_map = {}
        if custom_stream_ids is None:
            custom_stream_ids = set()
        if catchup_stream_ids is None:
            catchup_stream_ids = set()

        for rule in rules:
            if not rule.stream_sort_field:
                continue

            # Deduplicate — rule_channel_order may list the same channel multiple times
            channel_ids = list(dict.fromkeys(rule_channel_order.get(rule.id, [])))
            if not channel_ids:
                continue

            for channel_id in channel_ids:
                # Find channel in existing channels cache
                channel = None
                for ch in (self._existing_channels or []):
                    if ch.get("id") == channel_id:
                        channel = ch
                        break
                if not channel:
                    # Channel may have been created during this run — fetch fresh
                    try:
                        channel = await self.client.get_channel(channel_id)
                        if channel and "streams" not in channel:
                            channel["streams"] = await self.client.get_channel_streams(channel_id)
                    except Exception as e:
                        logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch channel %s for reorder: %s", channel_id, e)
                if not channel:
                    continue

                # Get current stream IDs in the channel
                stream_items = channel.get("streams", []) or []
                current_streams = [
                    s["id"] if isinstance(s, dict) else s
                    for s in stream_items
                ]
                if len(current_streams) < 2:
                    continue

                channel_name = channel.get("name", f"Channel #{channel_id}")

                # Some sort modes (e.g. stream_name) only need names, not probe stats.
                # When a stream has no stats row yet, _stream_name_for_sort falls back
                # to "Stream <id>", which can make sorting appear to do nothing.
                # If the channel payload includes stream dicts with names, seed those
                # into the per-call stats cache so name-based sorts can still reorder.
                stats_cache = self._stream_stats_cache
                if any(isinstance(s, dict) and s.get("name") for s in stream_items):
                    stats_cache = dict(self._stream_stats_cache)
                    for s in stream_items:
                        if not isinstance(s, dict):
                            continue
                        sid = s.get("id")
                        sname = s.get("name")
                        if not sid or not sname:
                            continue
                        existing = stats_cache.get(sid)
                        if isinstance(existing, dict):
                            if not existing.get("stream_name"):
                                stats_cache[sid] = {**existing, "stream_name": sname}
                        else:
                            stats_cache[sid] = {"stream_name": sname}

                # Respect rule.stream_sort_field (Provider Order, Quality, etc.).
                # Previously this always used global smart-sort settings, so "Provider Order (M3U)"
                # only changed the log label and did not reorder by M3U account priority.
                sorted_streams = _reorder_streams_for_rule(
                    current_streams,
                    rule,
                    stats_cache,
                    stream_m3u_map,
                    channel_name,
                    settings,
                    custom_stream_ids=custom_stream_ids,
                    catchup_stream_ids=catchup_stream_ids,
                )

                # Skip if order didn't change
                if sorted_streams == current_streams:
                    mode_label = _stream_sort_rule_label(rule.stream_sort_field)
                    logger.info(
                        "[AUTO-CREATE-ENGINE] Channel '%s': already sorted by %s, skipping",
                        channel_name,
                        mode_label,
                    )
                    # Still record that we evaluated sorting for UI visibility.
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": f"[AUTO-CREATE-ENGINE] {channel_name}",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "reorder_streams",
                            "description": f"Stream order already sorted in '{channel_name}' by {mode_label} (no changes)",
                            "success": True,
                            "entity_id": channel_id,
                            "error": None
                        }]
                    })
                    continue

                if dry_run:
                    results["dry_run_results"].append({
                        "stream_id": None,
                        "stream_name": f"[AUTO-CREATE-ENGINE] {channel_name}",
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "action": f"Would reorder {len(sorted_streams)} streams in '{channel_name}' "
                                  f"by {_stream_sort_rule_label(rule.stream_sort_field)}",
                        "would_create": False,
                        "would_modify": True
                    })
                else:
                    try:
                        await self.client.update_channel(channel_id, {"streams": sorted_streams})
                        # Update cache
                        channel["streams"] = sorted_streams

                        # Collect deprioritization reasons
                        deprioritized = []
                        for sid in sorted_streams:
                            stats = self._stream_stats_cache.get(sid)
                            if stats:
                                if stats.get("is_black_screen"):
                                    deprioritized.append({"id": sid, "name": stats.get("stream_name", f"Stream {sid}"), "reason": "black_screen"})
                                elif stats.get("is_low_fps"):
                                    deprioritized.append({"id": sid, "name": stats.get("stream_name", f"Stream {sid}"), "reason": "low_fps"})
                                elif stats.get("probe_status") in ("failed", "timeout"):
                                    deprioritized.append({"id": sid, "name": stats.get("stream_name", f"Stream {sid}"), "reason": stats.get("probe_status")})

                        mode_label = _stream_sort_rule_label(rule.stream_sort_field)
                        desc_parts = [f"Reordered {len(sorted_streams)} streams in '{channel_name}' by {mode_label}"]
                        if deprioritized:
                            reasons = {}
                            for d in deprioritized:
                                reasons.setdefault(d["reason"], []).append(d["name"])
                            reason_strs = []
                            for reason, names in reasons.items():
                                label = {"black_screen": "black screen", "low_fps": "low FPS", "failed": "failed", "timeout": "timed out"}.get(reason, reason)
                                reason_strs.append(f"{len(names)} {label}")
                            desc_parts.append(f"({', '.join(reason_strs)} deprioritized)")

                        reorder_desc = " ".join(desc_parts)

                        results["execution_log"].append({
                            "stream_id": None,
                            "stream_name": f"[AUTO-CREATE-ENGINE] {channel_name}",
                            "m3u_account_id": None,
                            "rules_evaluated": [],
                            "actions_executed": [{
                                "type": "reorder_streams",
                                "description": reorder_desc,
                                "success": True,
                                "entity_id": channel_id,
                                "error": None
                            }]
                        })
                        logger.info(
                            "[AUTO-CREATE-ENGINE] %s", reorder_desc
                        )
                    except Exception as e:
                        logger.error(
                            "[AUTO-CREATE-ENGINE] Failed to reorder streams in '%s': %s",
                            channel_name, e
                        )

    async def _sort_channel_groups(
        self,
        sort_group_requests: dict,
        executor: ActionExecutor,
        results: dict,
        dry_run: bool,
        settings=None,
    ):
        """
        Pass 3.6: Alphabetically sort + renumber channels within a group.

        Queued by the ``sort_group`` action
        (``channel_pipeline_executor.ActionExecutor._execute_sort_group``)
        — one entry per distinct group touched this run, already
        deduplicated by the engine's per-stream aggregation
        (``results["sort_group_requests"]``, a dict keyed by group_id) so a
        group with N matched streams is sorted exactly ONCE regardless of N.

        Sort semantics are a backend port of the manual Sort & Renumber
        modal's comparator (``frontend/src/utils/channelSort.ts``,
        ``frontend/src/components/ChannelsPane.tsx``) — see
        ``channel_pipeline_sort.py``. Keep both sides in lock-step; a
        behavior change on one side without the other means automated and
        manual sorting silently diverge.

        Reads channels from ``executor._channel_by_id`` (not
        ``self._existing_channels``) because that cache is kept live for
        every channel touched THIS run, including ones created by an
        earlier action in the same stream's pipeline pass — the engine's
        own ``self._existing_channels`` is a pre-run snapshot that never
        gets appended to (see engine/executor cache split, bd-vy4fl
        investigation). Reading the executor's cache directly mirrors the
        existing precedent elsewhere in this module (e.g.
        ``executor._channel_by_id`` in the EPG-retry and reconcile passes).

        Runs AFTER Pass 3 (rule-level ``sort_field`` renumber) so an
        explicit ``sort_group`` action — a more specific, per-action
        request — wins if a rule configures both mechanisms for the same
        channels.
        """
        if not sort_group_requests:
            return

        for group_id, params in sort_group_requests.items():
            channels = [
                ch for ch in executor._channel_by_id.values()
                if ch.get("channel_group_id") == group_id
            ]
            if len(channels) < 2:
                continue

            group_name = executor._group_by_id.get(group_id, {}).get("name", f"Group #{group_id}")
            order = params.get("order", "asc")
            strip_numbers = params.get("strip_numbers", True)
            ignore_country = params.get("ignore_country", False)
            requested_starting_number = params.get("starting_number")

            sorted_channels = sort_channels_by_name(
                channels,
                name_key=lambda ch: ch.get("name", ""),
                strip_numbers=strip_numbers,
                ignore_country=ignore_country,
                order=order,
            )
            channel_ids = [ch["id"] for ch in sorted_channels]

            # Default starting number: the group's current lowest channel
            # number, or 1 if no channel in the group has one assigned yet
            # — mirrors the manual modal's default
            # (ChannelsPane.tsx handleOpenSortRenumber).
            if requested_starting_number is not None:
                starting_number = requested_starting_number
            else:
                existing_numbers = [
                    ch.get("channel_number") for ch in channels
                    if ch.get("channel_number") is not None
                ]
                starting_number = min(existing_numbers) if existing_numbers else 1

            mode_label = f"alphabetical ({order})"

            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": None,
                    "stream_name": f"[AUTO-CREATE-ENGINE] {group_name}",
                    "rule_id": None,
                    "rule_name": None,
                    "action": f"Would sort {len(channel_ids)} channels in '{group_name}' "
                              f"{mode_label} starting at #{starting_number}",
                    "would_create": False,
                    "would_modify": True
                })
                continue

            try:
                await self.client.assign_channel_numbers(channel_ids, starting_number)
                rename_count = await _auto_rename_after_renumber(
                    self.client, channel_ids, starting_number, settings
                )
                rename_note = f", renamed {rename_count} channel names" if rename_count else ""
                desc = (
                    f"Sorted {len(channel_ids)} channels in '{group_name}' "
                    f"{mode_label}, renumbered starting at #{starting_number}{rename_note}"
                )
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": f"[AUTO-CREATE-ENGINE] {group_name}",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "sort_group",
                        "description": desc,
                        "success": True,
                        "entity_id": group_id,
                        "error": None
                    }]
                })
                logger.info("[AUTO-CREATE-ENGINE] %s", desc)
            except Exception as e:
                logger.error(
                    "[AUTO-CREATE-ENGINE] Failed to sort group '%s': %s", group_name, e
                )
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": f"[AUTO-CREATE-ENGINE] {group_name}",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "sort_group",
                        "description": f"Failed to sort group: {e}",
                        "success": False,
                        "entity_id": group_id,
                        "error": str(e)
                    }]
                })

    # =========================================================================
    # Stream Processing
    # =========================================================================

    async def _process_streams(
        self,
        streams: list[StreamContext],
        rules: list[ChannelPipelineRule],
        execution: ChannelPipelineExecution,
        dry_run: bool,
        triggered_by: str = TRIGGERED_BY_UNSPECIFIED,
        event_sync_rules: list[ChannelPipelineRule] = None,
    ) -> dict:
        """
        Process streams through the rules pipeline.

        Args:
            streams: List of stream contexts to process
            rules: List of rules sorted by priority
            execution: Execution record for tracking
            dry_run: Whether to simulate only
            triggered_by: Engine-side triggered_by string (e.g.
                "m3u_refresh", "scheduled", "manual"). Threaded
                through to ``ActionExecutor`` so the BD-F bulk-M3U
                dedup hook in ``_execute_create_channel`` only fires
                for the M3U-refresh path per ADR-008 §D1.
            event_sync_rules: event_sync-kind rules cleared to run by
                ``run_pipeline``'s manual-run-only gate (ti939.2.1).
                Executed by the dedicated attach phase after Pass 2 —
                NEVER through Pass 1/2 condition/action evaluation.

        Returns:
            Dict with processing results
        """
        # Load user settings once for the entire pipeline run
        settings = get_settings()
        logger.debug(
            "[AUTO-CREATE-ENGINE] include_channel_number_in_name=%s, "
            "separator=%r, default_profiles=%s, "
            "timezone=%s, auto_rename=%s, "
            "sort_priority=%s, sort_enabled=%s, "
            "deprioritize_failed=%s",
            getattr(settings, 'include_channel_number_in_name', False),
            getattr(settings, 'channel_number_separator', '-'),
            getattr(settings, 'default_channel_profile_ids', []),
            getattr(settings, 'timezone_preference', 'both'),
            getattr(settings, 'auto_rename_channel_number', False),
            getattr(settings, 'stream_sort_priority', []),
            getattr(settings, 'stream_sort_enabled', {}),
            getattr(settings, 'deprioritize_failed_streams', True)
        )

        # Create normalization engine if any rule uses normalization_group_ids
        # or if any condition needs it (normalized_name_in_group).
        # Also create it if any NormalizationRuleGroup is enabled in the DB so
        # the executor's normalized-name/core-name indices are available for
        # _find_channel_by_name lookups — this prevents auto-creation from
        # creating duplicate channels when an existing channel's name would
        # collapse to the same normalized form (GH-104 / bd-u9odj).
        norm_engine = None
        needs_norm = any(r.get_normalization_group_ids() for r in rules)
        if not needs_norm:
            # Check if any condition uses normalized_name_in_group
            for r in rules:
                for c in r.get_conditions():
                    ctype = c.get("type") if isinstance(c, dict) else getattr(c, "type", "")
                    if ctype in ("normalized_name_in_group", "normalized_name_not_in_group",
                                  "normalized_name_exists", "normalized_name_not_exists"):
                        needs_norm = True
                        break
                if needs_norm:
                    break
        if not needs_norm:
            # Fall back to DB: any enabled group means lookups should consult
            # the normalized indices even if no rule explicitly opts in.
            try:
                from models import NormalizationRuleGroup
                session = get_session()
                try:
                    has_enabled_group = session.query(
                        NormalizationRuleGroup
                    ).filter(
                        NormalizationRuleGroup.enabled == True  # noqa: E712 — SQLA needs ==
                    ).first() is not None
                finally:
                    session.close()
                if has_enabled_group:
                    needs_norm = True
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to probe enabled normalization groups: %s", e)
        if needs_norm:
            try:
                from normalization_engine import get_normalization_engine
                session = get_session()
                norm_engine = get_normalization_engine(session)
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to initialize normalization engine: %s", e)

        # Initialize evaluator (with normalization engine for normalized_name_in_group conditions)
        evaluator = ConditionEvaluator(self._existing_channels, self._existing_groups,
                                       normalization_engine=norm_engine)

        # Fetch all profile IDs if default profiles are configured OR any rule
        # uses an assign_channel_profile action. Honoring a per-rule profile
        # selection is subtractive (enable selected, disable the rest — GH #720
        # / y3m6o), so the disable loop needs the full profile universe even
        # when no GLOBAL default is set. Event-sync rules are in scope and are
        # ChannelPipelineRule instances too, so their actions are checked the
        # same way. Mirrors the needs_epg detection idiom below (dict vs object
        # action shapes).
        needs_profiles = bool(settings.default_channel_profile_ids) or any(
            (a.get("type") if isinstance(a, dict) else getattr(a, "type", "")) == "assign_channel_profile"
            for r in list(rules) + list(event_sync_rules or [])
            for a in r.get_actions()
        )
        # all_profile_ids carries a THREE-way availability signal consumed by
        # the executor (see ActionExecutor.__init__ / _execute_assign_channel_
        # profile):
        #   * a list (incl. []) => the KNOWN profile universe; [] = genuinely
        #     zero profiles configured, or profiles simply not needed.
        #   * None               => the universe is UNAVAILABLE (fetch raised).
        # assign_channel_profile enforces exclusive membership subtractively and
        # MUST NOT report success when the universe is unavailable (it cannot
        # know what to DISABLE), so a fetch failure yields None — NOT [] — to
        # keep "unavailable" distinct from "genuinely empty" and avoid silently
        # recreating GH #720 during a transient read failure (y3m6o.1 Bug 2).
        all_profile_ids: list[int] | None = []
        if needs_profiles:
            try:
                profiles = await self.client.get_channel_profiles()
                all_profile_ids = [p["id"] for p in profiles]
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch channel profiles: %s", e)
                all_profile_ids = None

        # Pre-fetch EPG data and sources if any rule uses assign_epg —
        # including event_sync rules whose config opts into dummy EPG
        # auto-assignment (ti939.3.3: their assignment path resolves the
        # profile's Dispatcharr source from epg_sources and its entries from
        # epg_data, exactly like a standard assign_epg action).
        epg_data = []
        epg_sources = []
        needs_epg = any(
            a.get("type") == "assign_epg" if isinstance(a, dict) else getattr(a, "type", "") == "assign_epg"
            for r in rules for a in r.get_actions()
        ) or any(
            (r.get_event_sync_config() or {}).get("dummy_epg_profile_id")
            for r in (event_sync_rules or [])
        )
        if needs_epg:
            try:
                epg_data = await self.client.get_epg_data()
                logger.debug("[AUTO-CREATE-ENGINE] Fetched %s EPG data entries for assign_epg resolution", len(epg_data))
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch EPG data for assign_epg: %s", e)
            try:
                epg_sources = await self.client.get_epg_sources()
                logger.debug("[AUTO-CREATE-ENGINE] Fetched %s EPG sources", len(epg_sources))
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch EPG sources: %s", e)

        # Build stream_id -> m3u_account_id map for smart sort M3U priority lookups,
        # and a set of operator-added custom stream IDs (Dispatcharr is_custom) for
        # the "custom_streams" Smart Sort criterion (bead ap1ud / GH #244).
        stream_m3u_map = {}
        custom_stream_ids: set[int] = set()
        catchup_stream_ids: set[int] = set()
        for s in streams:
            stream_m3u_map[s.stream_id] = s.m3u_account_id
            if getattr(s, "is_custom", False):
                custom_stream_ids.add(s.stream_id)
            if getattr(s, "is_catchup", False):
                catchup_stream_ids.add(s.stream_id)

        executor = ActionExecutor(
            self.client, self._existing_channels, self._existing_groups,
            normalization_engine=norm_engine,
            settings=settings,
            all_profile_ids=all_profile_ids,
            epg_data=epg_data,
            epg_sources=epg_sources,
            # BD-F (bd-a5lb2): thread triggered_by into the executor so
            # the bulk-M3U dedup hook in _execute_create_channel only
            # fires for the M3U-refresh path per ADR-008 §D1.
            triggered_by=triggered_by,
            # bd-0emgo.5: thread the execution_id so each LIVE merge writes
            # a journal entry tagged batch_id=str(execution_id), giving an
            # operator a queryable (channel_id, stream_id) audit trail to
            # recover from a bad run via get_journal(batch_id=...).
            execution_id=execution.id,
        )

        # Results tracking
        results = {
            "streams_evaluated": 0,
            "streams_matched": 0,
            "channels_created": 0,
            "channels_updated": 0,
            "groups_created": 0,
            "streams_merged": 0,
            # Count of distinct channels that received at least one merge this
            # run. Set after Pass 2 from len(channels_touched_ids), unioned from
            # the add_result chokepoint (exec_ctx.merged_channel_ids).
            "channels_touched": 0,
            "streams_skipped": 0,
            "streams_removed": 0,
            "channels_removed": 0,
            "channels_moved": 0,
            # BD-F (bd-a5lb2): aggregate count of rows enqueued to the
            # pending_merges queue by the bulk-M3U dedup hook (ADR-008
            # §D1). Surfaces on the pipeline result so the M3U-refresh
            # task can hand the count to BD-J's toast handler.
            "pending_merges_added": 0,
            "created_entities": [],
            "modified_entities": [],
            "dry_run_results": [],
            "conflicts": [],
            # bd-sjdsq: bounded in-memory execution_log. Verbose per-stream
            # trace is stripped on non-dry-run; dry-run keeps the full trace.
            # cap <= 0 (operator override) disables the entry cap. Every
            # append site in the engine + executor funnels through this
            # object's overridden .append — the single chokepoint.
            "execution_log": BoundedExecutionLog(
                cap=(0 if dry_run else max(0, getattr(
                    settings, "max_auto_creation_log_entries",
                    DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES,
                ))),
                verbose=dry_run,
            ),
            "rule_match_counts": {},
            "probe_stream_ids": set(),
            "streams_probed": 0,
            # enhancedchannelmanager-vy4fl: group_id -> sort_group params,
            # aggregated from every matched stream's exec_ctx this run (see
            # the aggregation site below). A dict keyed by group_id so N
            # streams landing in the same group still produce ONE entry —
            # consumed once by Pass 3.6 (_sort_channel_groups) then deleted
            # before the results dict is returned (transient control-flow
            # data, same treatment as probe_stream_ids).
            "sort_group_requests": {},
            # bd-h2xnl / bd-exo4j created-channel cap state. Resolved here so
            # the Pass 2 soft-abort and the run summary share one value.
            "channel_cap": max(0, getattr(
                settings, "max_auto_created_channels_per_run",
                DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN,
            )),
            "capped": False,
            "cap_would_create": 0,
        }

        # Track which streams have been processed by which rules
        stream_rule_matches = {}  # stream_id -> list of (rule_id, priority)

        # =====================================================================
        # Pass 1: Evaluate all streams against all rules, collect matches
        # =====================================================================
        logger.info("[AUTO-CREATE-ENGINE] Evaluating %s streams against %s rules", len(streams), len(rules))
        matched_entries = []  # list of (stream, winning_rule, losing_rules, stream_rules_log)

        for stream in streams:
            results["streams_evaluated"] += 1
            logger.debug(
                "[AUTO-CREATE-ENGINE] Evaluating stream id=%s name=%r "
                "m3u=%s group=%r",
                stream.stream_id, stream.stream_name,
                stream.m3u_account_id, stream.group_name
            )

            # Track rules that match this stream
            matching_rules = []

            # Build per-stream log of rule evaluations
            stream_rules_log = []

            for rule in rules:
                # Check if rule applies to this M3U account
                if rule.m3u_account_id and rule.m3u_account_id != stream.m3u_account_id:
                    logger.debug(
                        "[AUTO-CREATE-ENGINE]   Rule '%s' skipped: m3u filter "
                        "(rule=%s != stream=%s)",
                        rule.name, rule.m3u_account_id, stream.m3u_account_id
                    )
                    continue

                # Evaluate conditions with connector logic (AND/OR)
                # Evaluate ALL conditions (no short-circuit) so the log is complete
                conditions = rule.get_conditions()
                conditions_log = []

                # Group conditions by OR breaks (AND binds tighter)
                or_groups = [[]]
                for cond in conditions:
                    connector = cond.get("connector", "and") if isinstance(cond, dict) else getattr(cond, 'connector', 'and')
                    if connector == "or" and or_groups[-1]:
                        or_groups.append([])
                    or_groups[-1].append(cond)

                # Evaluate ALL conditions for logging, track match per group
                matched = False
                for group in or_groups:
                    group_matched = True
                    for condition in group:
                        result = evaluator.evaluate(condition, stream)
                        conditions_log.append({
                            "type": result.condition_type,
                            "value": condition.get("value") if isinstance(condition, dict) else str(getattr(condition, 'value', '')),
                            "matched": result.matched,
                            "details": result.details,
                            "connector": condition.get("connector", "and") if isinstance(condition, dict) else getattr(condition, 'connector', 'and')
                        })
                        if not result.matched:
                            group_matched = False
                    if group_matched:
                        matched = True

                rule_log = {
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "conditions": conditions_log,
                    "matched": matched,
                    "was_winner": False
                }
                stream_rules_log.append(rule_log)

                logger.debug(
                    "[AUTO-CREATE-ENGINE]   Rule '%s' (id=%s): matched=%s "
                    "(%s conditions in %s OR-group(s))",
                    rule.name, rule.id, matched,
                    len(conditions), len(or_groups)
                )

                if matched:
                    matching_rules.append(rule)

                    # Check for conflicts (multiple rules matching same stream)
                    if stream.stream_id not in stream_rule_matches:
                        stream_rule_matches[stream.stream_id] = []
                    stream_rule_matches[stream.stream_id].append((rule.id, rule.priority))

                    if rule.stop_on_first_match:
                        logger.debug("[AUTO-CREATE-ENGINE]   Rule '%s' has stop_on_first_match, skipping remaining rules", rule.name)
                        break

            if not matching_rules:
                logger.debug("[AUTO-CREATE-ENGINE] Stream %r: no rules matched", stream.stream_name)
                continue

            # Determine winning and losing rules
            winning_rule = matching_rules[0]
            losing_rules = matching_rules[1:] if len(matching_rules) > 1 else []

            logger.debug(
                "[AUTO-CREATE-ENGINE] Stream %r: winner='%s' (id=%s)%s",
                stream.stream_name, winning_rule.name, winning_rule.id,
                (", losers=%s" % [r.name for r in losing_rules]) if losing_rules else ""
            )

            matched_entries.append((stream, winning_rule, losing_rules, stream_rules_log))

        logger.info("[AUTO-CREATE-ENGINE] Complete: %s streams matched out of %s evaluated", len(matched_entries), len(streams))

        # =====================================================================
        # Pass 1.1: Timezone filter on matched entries
        # =====================================================================
        if settings.timezone_preference != "both":
            before_count = len(matched_entries)
            matched_entries = [
                entry for entry in matched_entries
                if _filter_by_timezone(entry[0].stream_name, settings.timezone_preference)
            ]
            filtered_count = before_count - len(matched_entries)
            if filtered_count > 0:
                logger.info(
                    "[AUTO-CREATE-ENGINE] Filtered %s streams "
                    "(preference=%s), %s remaining",
                    filtered_count, settings.timezone_preference,
                    len(matched_entries)
                )

        # =====================================================================
        # Pass 1.5: Probe unprobed streams (for rules with probe_on_sort)
        # =====================================================================
        await self._probe_unprobed_streams(matched_entries, rules, results, dry_run)

        # =====================================================================
        # Between passes: Sort matched entries by rule's sort configuration
        # =====================================================================
        rule_map = {r.id: r for r in rules}
        rule_groups = defaultdict(list)
        for entry in matched_entries:
            rule_groups[entry[1].id].append(entry)

        sorted_entries = []
        for rule_id, entries in rule_groups.items():
            rule = rule_map.get(rule_id)
            if rule and rule.sort_field:
                logger.debug(
                    "[AUTO-CREATE-ENGINE] Sorting %s entries for rule '%s' "
                    "by %s %s",
                    len(entries), rule.name,
                    rule.sort_field, rule.sort_order or 'asc'
                )
                # bd-eio04.15: precompile sort_regex ONCE per rule, outside
                # the sort comparator. Python's Timsort invokes the key
                # function O(n) times (not N log N — keys are memoized), so
                # the per-call cost is O(n) safe_regex.search invocations.
                # Pre-compiling avoids paying the compile overhead on every
                # call and keeps the hot path close to stdlib re speed.
                # Oversize / invalid patterns raise here; we fall back to a
                # None sort_regex so _sort_key returns the unmatched
                # sentinel for every stream (stable arbitrary order).
                precompiled_sort_regex = None
                raw_sort_regex = getattr(rule, 'sort_regex', None)
                if raw_sort_regex and rule.sort_field == "stream_name_regex":
                    try:
                        precompiled_sort_regex = safe_regex.compile(raw_sort_regex)
                    except safe_regex.SafeRegexError as e:
                        logger.warning(
                            "[AUTO-CREATE-ENGINE] Rule '%s' sort_regex "
                            "failed to compile (%s); falling back to "
                            "unsorted order for stream_name_regex",
                            rule.name, e,
                        )
                entries.sort(
                    key=lambda e: _sort_key(e[0], rule.sort_field, precompiled_sort_regex),
                    reverse=(rule.sort_order == "desc")
                )
            sorted_entries.extend(entries)

        logger.debug("[AUTO-CREATE-ENGINE] Total sorted entries: %s", len(sorted_entries))

        # Track channel IDs per rule in sorted order for:
        # - Pass 3 renumber: ONLY channels the rule owns (created this run OR pre-run managed)
        # - Pass 3.5 stream reorder: channels the rule owns OR channels it actually modified this run
        rule_channel_order = defaultdict(list)  # rule_id -> [channel_id, ...] in sorted order (renumber gating)
        rule_channel_order_streams = defaultdict(list)  # rule_id -> [channel_id, ...] in sorted order (reorder gating)

        # Snapshot each rule's pre-run managed channel set. Used to gate the
        # rule_channel_order append so Pass 3's renumber only touches channels
        # this rule actually owns: either created this run OR already managed
        # by this rule prior to the run. A channel matched into via the
        # normalized-name fallback (PR #107) that belongs to a DIFFERENT rule
        # must NOT be added, or Pass 3 will renumber foreign groups.
        # See bd-yj5yi / GH-104 regression.
        pre_run_managed_ids: dict[int, set[int]] = {
            rule.id: set(rule.get_managed_channel_ids() or [])
            for rule in rules
        }

        # =====================================================================
        # Pass 2: Execute actions on sorted matches
        # =====================================================================
        logger.debug("[AUTO-CREATE-ENGINE] Executing actions for %s matched streams", len(sorted_entries))
        # Distinct channels merged into across the whole run. Unioned from each
        # stream's exec_ctx.merged_channel_ids (populated at the add_result
        # chokepoint), so it stays consistent with streams_merged no matter which
        # path produced the merge (bd-0emgo.4).
        channels_touched_ids: set = set()
        # bd-h2xnl / bd-exo4j created-channel cap (shared safety valve). Checked
        # at the TOP of each iteration — i.e. between streams, after the prior
        # stream's actions fully completed and aggregated — so a cap trip can
        # never leave a half-applied batch. The cap counts channels CREATED this
        # run (results["channels_created"]); merges/updates do not count toward
        # it (they don't drive the PPV/event blast radius). Disabled when
        # channel_cap <= 0 or in dry-run (a dry run mutates nothing).
        _channel_cap = results.get("channel_cap", 0)
        _cap_active = bool(_channel_cap) and not dry_run
        for _idx, (stream, winning_rule, losing_rules, stream_rules_log) in enumerate(sorted_entries):
            if _cap_active and results["channels_created"] >= _channel_cap:
                # Soft-abort: stop creating further channels, leave what we have
                # consistent, record N-of-M for a non-silent surface.
                remaining = len(sorted_entries) - _idx
                results["capped"] = True
                results["cap_would_create"] = remaining
                logger.warning(
                    "[AUTO-CREATE] Created-channel cap reached: %s created, "
                    "stopping with %s matched stream(s) unprocessed (cap=%s). "
                    "Review the rule or raise max_auto_created_channels_per_run "
                    "(bd-h2xnl / GH #473).",
                    results["channels_created"], remaining, _channel_cap,
                )
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": "[AUTO-CREATE] created-channel cap reached",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "capped",
                        "description": (
                            f"Auto-creation capped at {results['channels_created']} "
                            f"channel(s); {remaining} more matched stream(s) were not "
                            f"processed. Auto-creation is idempotent — run it again "
                            f"to continue (created channels persist), or raise the "
                            f"cap in Settings > Auto Creation (currently {_channel_cap})."
                        ),
                        "success": True,
                        "entity_id": None,
                        "error": None,
                    }],
                })
                break

            # Skip struck-out streams if the winning rule has skip_struck_streams enabled
            if getattr(winning_rule, 'skip_struck_streams', False) and stream.stream_id in self._struck_stream_ids:
                logger.info("[AUTO-CREATE-ENGINE] Skipping struck stream %r (id=%s) for rule '%s'",
                            stream.stream_name, stream.stream_id, winning_rule.name)
                results["streams_skipped_struck"] = results.get("streams_skipped_struck", 0) + 1
                continue

            results["streams_matched"] += 1
            logger.debug(
                "[AUTO-CREATE-ENGINE] Stream %r (id=%s): "
                "executing rule '%s' actions",
                stream.stream_name, stream.stream_id, winning_rule.name
            )

            # Track per-rule match counts
            results["rule_match_counts"][winning_rule.id] = results["rule_match_counts"].get(winning_rule.id, 0) + 1

            # Mark winner in log
            for rl in stream_rules_log:
                if rl["rule_id"] == winning_rule.id and rl["matched"]:
                    rl["was_winner"] = True
                    break

            # Record conflict if multiple rules matched
            if losing_rules:
                await self._record_conflict(
                    execution=execution,
                    stream=stream,
                    winning_rule=winning_rule,
                    losing_rules=losing_rules,
                    conflict_type="duplicate_match"
                )
                results["conflicts"].append({
                    "stream_id": stream.stream_id,
                    "stream_name": stream.stream_name,
                    "winning_rule_id": winning_rule.id,
                    "losing_rule_ids": [r.id for r in losing_rules]
                })

            # Execute actions and capture results
            exec_ctx = ExecutionContext(dry_run=dry_run)
            actions = winning_rule.get_actions()
            actions_log = []
            stop_processing = False

            for action_data in actions:
                action = Action.from_dict(action_data)

                action_result = await executor.execute(
                    action, stream, exec_ctx, winning_rule.target_group_id,
                    normalization_group_ids=winning_rule.get_normalization_group_ids(),
                    match_scope_target_group=bool(getattr(winning_rule, 'match_scope_target_group', False)),
                    rule_scope_group_id=getattr(winning_rule, 'match_scope_group_id', None),
                    allow_manual_channel_merge=bool(getattr(winning_rule, 'allow_manual_channel_merge', False)),
                    # GH #645 / bead 0vao3: opt-in whitespace/case fold on the
                    # create_channel if_exists merge lookup's comparison key.
                    fold_match_key=bool(getattr(winning_rule, 'fold_match_key', False)),
                    rule_id=winning_rule.id,
                )

                action_entry = {
                    "type": action_result.action_type,
                    "description": action_result.description,
                    "success": action_result.success,
                    "entity_id": action_result.entity_id,
                    "error": action_result.error
                }
                if action_result.details:
                    action_entry["details"] = action_result.details
                actions_log.append(action_entry)

                # Check for stop_processing action.
                # NOTE: by the time we reach Pass 2, Pass 1 has already
                # resolved exactly one winning rule per stream — there are no
                # "further rules" left to stop here (the rule-level
                # short-circuit is `rule.stop_on_first_match`, handled in
                # Pass 1). So at the Pass 2 / per-stream level STOP_PROCESSING
                # is effectively a no-op: it must NOT abort the remaining
                # streams (bd-iqm50 / GH #225 — `break` here used to kill the
                # entire sorted_entries loop after the first such stream), and
                # it does NOT abort the current rule's remaining actions
                # either (the action's own description is "stop processing
                # further *rules*", not further actions of this rule).
                if action.type == ActionType.STOP_PROCESSING.value:
                    stop_processing = True

                # Record dry-run result
                if dry_run:
                    results["dry_run_results"].append({
                        "stream_id": stream.stream_id,
                        "stream_name": stream.stream_name,
                        "rule_id": winning_rule.id,
                        "rule_name": winning_rule.name,
                        "action": action_result.description,
                        "would_create": action_result.created,
                        "would_modify": action_result.modified
                    })

            # Add stream log entry (only for matched streams)
            results["execution_log"].append({
                "stream_id": stream.stream_id,
                "stream_name": stream.stream_name,
                "m3u_account_id": stream.m3u_account_id,
                "rules_evaluated": stream_rules_log,
                "actions_executed": actions_log
            })

            # Aggregate results from execution context
            results["channels_created"] += exec_ctx.channels_created
            results["channels_updated"] += exec_ctx.channels_updated
            results["groups_created"] += exec_ctx.groups_created
            results["streams_merged"] += exec_ctx.streams_merged
            results["streams_skipped"] += exec_ctx.streams_skipped
            results["streams_removed"] += exec_ctx.streams_removed
            # Union this stream's merged-into channels into the run-wide set
            # (bd-0emgo.4); len() becomes channels_touched after the loop.
            channels_touched_ids.update(exec_ctx.merged_channel_ids)
            # BD-F (bd-a5lb2): aggregate per-stream pending-merge enqueues
            # so the pipeline result surfaces a total the M3U-refresh
            # response can pass to BD-J's toast handler. Includes both
            # fresh inserts and ADR-008 §D5 idempotent collisions —
            # operationally both are "would have created a channel, now
            # waiting on operator review".
            results["pending_merges_added"] += exec_ctx.pending_merges_added
            results["created_entities"].extend(exec_ctx.created_entities)
            results["modified_entities"].extend(exec_ctx.modified_entities)
            results["probe_stream_ids"].update(exec_ctx.probe_stream_ids)
            # enhancedchannelmanager-vy4fl: last-write-wins per group_id if
            # two DIFFERENT sort_group actions (different rules, or the
            # same rule fired with different params across streams) target
            # the same group — an edge case, since a rule's sort_group
            # action params are static for the whole run. Whichever stream
            # is processed LAST this run decides the group's sort params.
            results["sort_group_requests"].update(exec_ctx.sort_group_requests)

            # Track channel ID for Pass 3 renumber.
            # Only include channels this rule actually OWNS — either just
            # created this run, or already in the rule's pre-run managed set.
            # This prevents Pass 3 from renumbering foreign-group channels
            # that were matched via the normalized-name fallback introduced
            # in PR #107 (bd-yj5yi / GH-104).
            if exec_ctx.current_channel_id:
                cid = exec_ctx.current_channel_id
                owned_by_this_rule = (
                    cid in exec_ctx.created_channel_ids
                    or cid in pre_run_managed_ids.get(winning_rule.id, set())
                )
                if owned_by_this_rule:
                    rule_channel_order[winning_rule.id].append(cid)
                else:
                    logger.debug(
                        "[AUTO-CREATE-ENGINE] Rule '%s': skipping Pass 3 append for "
                        "channel_id=%s — matched via fallback into foreign/unmanaged "
                        "channel (not created this run, not pre-run managed)",
                        winning_rule.name, cid
                    )

                # Track channel ID for Pass 3.5 stream reorder.
                # For stream sorting we also want to include channels that the rule actually
                # modified (e.g. merge added/removed streams) during this run; otherwise
                # stream_sort can silently never run when Channels Created=0.
                modified_this_run = any(
                    (a.get("entity_id") == cid and a.get("type") in ("merge_stream", "merge_streams_prune"))
                    for a in actions_log
                )
                if owned_by_this_rule or modified_this_run:
                    # Avoid duplicates when multiple matched streams touch the same channel.
                    # Preserve first-seen order (mirrors later dict.fromkeys de-dupe, but keeps
                    # the intermediate structure stable for tests and debug logging).
                    if cid not in rule_channel_order_streams[winning_rule.id]:
                        rule_channel_order_streams[winning_rule.id].append(cid)

            if stop_processing:
                # bd-iqm50 / GH #225: continue (NOT break) — STOP_PROCESSING
                # has no remaining rules to stop in Pass 2, so it must not
                # terminate the loop over the other matched streams.
                logger.debug(
                    "[AUTO-CREATE-ENGINE] Stream %r: STOP_PROCESSING action "
                    "(no-op at Pass 2 level — one winning rule per stream); "
                    "continuing to next stream",
                    stream.stream_name,
                )
                continue

        # =====================================================================
        # Event Sync attach phase (bead ti939.2.1 — Phase 1B).
        # Runs AFTER Pass 2 so its merges share the same executor (journal
        # buffer + flush-in-finally) and results plumbing, and BEFORE the
        # channels_touched derivation at Pass 2.75 so attach merges count.
        # Rides the existing per-stream merge internals — no new engine pass
        # semantics: each secondary stream independently resolves to at most
        # one master channel.
        # =====================================================================
        if event_sync_rules:
            await self._run_event_sync_rules(
                event_sync_rules, executor, results, dry_run,
                triggered_by=triggered_by,
                channels_touched_ids=channels_touched_ids,
                # bead io0tv: the attach phase registers each rule's touched
                # master channels for Pass 3.5 stream reorder, and extends
                # stream_m3u_map with provider ids for the master + secondary
                # streams so provider_order / quality-tie-break sorts score
                # them correctly.
                rule_channel_order_streams=rule_channel_order_streams,
                stream_m3u_map=stream_m3u_map,
                # bead ti939.4.1: promotion-enabled rules register their
                # PROMOTED channel ids here — the managed set Pass 4
                # reconciles. Attach-only rules never write it.
                rule_channel_order=rule_channel_order,
            )

        # =====================================================================
        # Pass 2.5: Verify EPG assignments on newly created channels
        # =====================================================================
        if not dry_run:
            verified_ok, re_patched, failed = await executor.verify_epg_assignments()
            if re_patched or failed:
                logger.info(
                    "[AUTO-CREATE-ENGINE] EPG verification: %s ok, %s re-patched, %s failed",
                    verified_ok, re_patched, failed
                )
                results["channels_updated"] += re_patched
                if re_patched:
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": "[AUTO-CREATE-ENGINE] EPG verification",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "verify_epg",
                            "description": f"Re-patched EPG on {re_patched} newly created channel(s)",
                            "success": True,
                            "entity_id": None,
                            "error": None
                        }]
                    })

        # =====================================================================
        # Pass 2.75: Merge reconciliation — prune non-matching streams (optional)
        # =====================================================================
        await executor.prune_merge_streams(results, dry_run)

        # Distinct-channel count: how many channels had at least one stream
        # merged into them this run. Unioned across streams from the add_result
        # chokepoint (exec_ctx.merged_channel_ids), which fires for EVERY
        # merge_stream result — merge_streams action AND create_channel
        # if_exists=merge AND any future merge path — so it can never drift from
        # streams_merged (bd-0emgo.4: a live dry-run reported streams_merged=26
        # but channels_touched=0 when the count was derived from a scattered
        # call-site dict that the create_channel merge path missed). This is the
        # honest label for "Channels touched by merges" as opposed to
        # channels_updated, which counts only genuine property updates
        # (logo/tvg/epg/number/etc.).
        results["channels_touched"] = len(channels_touched_ids)

        # =====================================================================
        # Pass 3: Re-sort existing channels for rules with sort_field
        # =====================================================================
        logger.debug("[AUTO-CREATE-ENGINE] Starting channel renumbering pass")
        for rule in rules:
            if not rule.sort_field:
                continue
            channel_ids = list(dict.fromkeys(rule_channel_order.get(rule.id, [])))
            if not channel_ids or len(channel_ids) < 2:
                continue

            starting_number = _get_rule_starting_number(rule)
            if starting_number is None:
                continue

            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": None,
                    "stream_name": "[AUTO-CREATE-ENGINE]",
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "action": f"Would renumber {len(channel_ids)} channels starting at #{starting_number} "
                              f"(sorted by {rule.sort_field} {rule.sort_order or 'asc'})",
                    "would_create": False,
                    "would_modify": True
                })
            else:
                try:
                    await self.client.assign_channel_numbers(channel_ids, starting_number)
                    # Auto-rename channel names after renumber
                    rename_count = await _auto_rename_after_renumber(
                        self.client, channel_ids, starting_number, settings
                    )
                    rename_note = f", renamed {rename_count} channel names" if rename_count else ""
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": f"[AUTO-CREATE-ENGINE] Rule '{rule.name}'",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "renumber_channels",
                            "description": f"Renumbered {len(channel_ids)} channels starting at #{starting_number} "
                                           f"(sorted by {rule.sort_field} {rule.sort_order or 'asc'}){rename_note}",
                            "success": True,
                            "entity_id": None,
                            "error": None
                        }]
                    })
                    logger.info(
                        "[AUTO-CREATE-ENGINE] Rule '%s': renumbered %s channels "
                        "starting at #%s%s",
                        rule.name, len(channel_ids), starting_number, rename_note
                    )
                except Exception as e:
                    logger.error("[AUTO-CREATE-ENGINE] Rule '%s': failed to renumber channels: %s", rule.name, e)
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": f"[AUTO-CREATE-ENGINE] Rule '{rule.name}'",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "renumber_channels",
                            "description": f"Failed to renumber channels: {e}",
                            "success": False,
                            "entity_id": None,
                            "error": str(e)
                        }]
                    })

        try:
            # =================================================================
            # Pass 3.5: Reorder streams within channels by smart sort
            # =================================================================
            logger.debug("[AUTO-CREATE-ENGINE] Starting stream reorder within channels")
            # bead io0tv: event_sync rules participate in Pass 3.5 too — their
            # stream_sort_field/stream_sort_order columns order streams within
            # the master channels the attach phase touched this run (registered
            # in rule_channel_order_streams by _run_event_sync_rules). Rules
            # without stream_sort_field are no-ops inside, so pre-feature
            # event_sync rules keep their append-only behavior unchanged.
            # Pass 3 (renumber) and Pass 4 (orphan reconcile) still receive
            # ONLY the standard rules.
            await self._reorder_channel_streams(
                rules + list(event_sync_rules or []),
                rule_channel_order_streams, results, dry_run,
                settings=settings, stream_m3u_map=stream_m3u_map,
                custom_stream_ids=custom_stream_ids,
                catchup_stream_ids=catchup_stream_ids,
            )

            # =================================================================
            # Pass 3.6: Sort + renumber channels within a group (sort_group
            # action, enhancedchannelmanager-vy4fl)
            # =================================================================
            logger.debug("[AUTO-CREATE-ENGINE] Starting sort_group post-run pass")
            await self._sort_channel_groups(
                results["sort_group_requests"], executor, results, dry_run,
                settings=settings,
            )
            del results["sort_group_requests"]

            # =================================================================
            # Pass 4: Reconcile — clean up orphaned channels
            # =================================================================
            logger.debug("[AUTO-CREATE-ENGINE] Starting orphan reconciliation")
            # bead ti939.4.1: promotion-enabled event_sync rules join Pass 4
            # — their managed set (promoted channels ONLY) reconciles like
            # any standard rule's. ONLY rules whose promotion pass ran to
            # completion THIS run are eligible (the attach phase records
            # them): a rule skipped for a fetch failure / invalid config
            # made no observation, and reconciling it would mass-delete
            # promoted channels on a transient error. Attach-only event_sync
            # rules never appear here — their hard bypass is unchanged.
            promotion_reconcile_ids = results.pop(
                "event_sync_promotion_reconcile_rule_ids", set()
            )
            pass4_rules = list(rules)
            if promotion_reconcile_ids:
                pass4_rules.extend(
                    r for r in (event_sync_rules or [])
                    if r.id in promotion_reconcile_ids
                )
            await self._reconcile_orphans(
                pass4_rules, rule_channel_order, executor, execution, results,
                dry_run, settings=settings
            )

            # =================================================================
            # Pass 5: Dummy EPG refresh + retry deferred assign_epg
            # =================================================================
            if executor._deferred_epg_assignments:
                logger.info(
                    "[AUTO-CREATE-ENGINE] Pass 5: %s deferred EPG assignments to retry",
                    len(executor._deferred_epg_assignments)
                )
                await self._refresh_dummy_epg_and_retry(executor, results, epg_sources, dry_run)

            # =================================================================
            # Pass 6: Batch probe streams queued by probe_streams actions
            # =================================================================
            if results["probe_stream_ids"]:
                await self._batch_probe_streams(
                    results["probe_stream_ids"], streams, results, dry_run
                )

            # Clean up non-serializable set before returning
            del results["probe_stream_ids"]

            # bd-sjdsq: finalize the bounded execution_log — appends the single
            # aggregate "…and N more" summary entry if it truncated.
            exec_log = results.get("execution_log")
            if isinstance(exec_log, BoundedExecutionLog):
                exec_log.finalize()
                log_retained = exec_log.retained_count
                log_truncated = exec_log.truncated_count
                results["execution_log_truncated"] = exec_log.truncated
            else:  # pragma: no cover — defensive; pipeline always sets the bounded list
                log_retained = len(exec_log or [])
                log_truncated = 0

            # bd-sjdsq OBSERVABILITY (SRE hard requirement): a peak-RSS line and
            # a run-size summary at completion so operators can see whether a run
            # blew up before it OOM-kills again. INFO level, [AUTO-CREATE] prefix.
            try:
                # ru_maxrss is in KiB on Linux.
                peak_rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
                logger.info(
                    "[AUTO-CREATE] Run peak RSS: %.1f MiB", peak_rss_mib
                )
            except Exception as e:  # pragma: no cover — getrusage is best-effort
                logger.debug("[AUTO-CREATE] Could not read peak RSS: %s", e)
            logger.info(
                "[AUTO-CREATE] Run size summary: streams_evaluated=%s "
                "streams_matched=%s channels_created=%s "
                "execution_log_retained=%s execution_log_truncated=%s "
                "capped=%s cap_would_create=%s",
                results.get("streams_evaluated", 0),
                results.get("streams_matched", 0),
                results.get("channels_created", 0),
                log_retained, log_truncated,
                results.get("capped", False),
                results.get("cap_would_create", 0),
            )

            # Drop the transient cap budget (not part of the API surface).
            results.pop("channel_cap", None)

            return results
        finally:
            # Flush buffered live-merge journal rows even when a later pipeline
            # pass raises. The flush helper no-ops on empty buffers and logs its
            # own failures so it does not mask the original exception.
            executor._flush_journal_buffer()

    # =========================================================================
    # Event Sync attach phase (bead ti939.2.1 — Phase 1B)
    # =========================================================================

    async def _fetch_event_sync_secondary_streams(
        self, config: dict, account_names: dict
    ) -> list:
        """Fetch one event_sync rule's secondary-group streams (paginated).

        Returns ``services.event_sync_resolver.SecondaryStream`` objects —
        the exact input shape the shared resolver (and therefore the preview
        endpoint) consumes. Groups whose channel-group name cannot be
        resolved are skipped loudly (mirrors the preview endpoint). The fetch
        is bounded by ``EVENT_SYNC_MAX_SECONDARY_STREAMS`` as a guard; the
        run is idempotent, so a truncated run picks up the rest next run.
        """
        from fastapi.concurrency import run_in_threadpool

        from services import event_sync_staleness
        from services.event_sync_resolver import SecondaryStream
        from stream_prober import extract_m3u_account_id

        # bead jqwfq Stage 1: previous-day stream-name membership per account
        # (latest pre-midnight M3USnapshot) — the same staleness signal the
        # preview computes, so run/preview parity holds on the flag. Sync DB
        # read → threadpool. Best-effort: a failed lookup means freshness
        # unknown (None flags), never a failed run — fail-open by design.
        stale_lookup: dict = {}
        try:
            stale_lookup = await run_in_threadpool(
                event_sync_staleness.previous_day_names,
                [aid for aid in account_names if aid is not None],
                event_sync_staleness.local_midnight_utc(),
            )
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] staleness lookup failed (%s) — stream-name "
                "freshness unknown for this run", e,
            )

        secondary_streams: list = []
        truncated = False
        # bead jiscc: iterate the provider-scoped secondary scopes. When a
        # scope carries an m3u_account_id, the stream fetch is filtered to that
        # provider (get_streams m3u_account=) — the same channel group can then
        # supply DIFFERENT providers' streams to different rules. m3u_account_id
        # None = the whole group (pre-provider-scope behavior).
        secondary_scopes = config.get("secondary") or [
            {"group_id": g, "m3u_account_id": None}
            for g in config["secondary_group_ids"]
        ]
        for scope in secondary_scopes:
            gid = scope["group_id"]
            provider_filter = scope.get("m3u_account_id")
            gname = await self.client._channel_group_name_for_id(gid)
            if not gname:
                logger.warning(
                    "[EVENT-SYNC] Secondary group %s has no resolvable "
                    "channel-group name; skipping fetch", gid,
                )
                continue
            if truncated:
                break
            page = 1
            while True:
                resp = await self.client.get_streams(
                    page=page, page_size=_EVENT_SYNC_FETCH_PAGE_SIZE,
                    channel_group_name=gname,
                    m3u_account=provider_filter,
                )
                batch = (
                    resp.get("results", []) if isinstance(resp, dict)
                    else (resp or [])
                )
                for s in batch:
                    # id guard beside the name guard (PR #616 review): a
                    # stream with a name but NO id would resolve normally and
                    # then append None to a master channel's stream list at
                    # attach time — skip it here instead.
                    if not s.get("name") or s.get("id") is None:
                        continue
                    account_id = extract_m3u_account_id(s.get("m3u_account"))
                    secondary_streams.append(SecondaryStream(
                        name=s["name"],
                        group_id=gid,
                        stream_id=s.get("id"),
                        provider=account_names.get(account_id),
                        # ti939.3.2: the review-queue fingerprint component
                        # (account ids are refresh-stable; stream ids are not).
                        provider_id=account_id,
                        # bead jqwfq: tri-state staleness signal — snapshot
                        # groups are keyed by group NAME.
                        name_seen_before_today=(
                            event_sync_staleness.name_seen_before_today(
                                stale_lookup, account_id, gname, s["name"],
                            )
                        ),
                    ))
                if len(secondary_streams) >= EVENT_SYNC_MAX_SECONDARY_STREAMS:
                    secondary_streams = secondary_streams[
                        :EVENT_SYNC_MAX_SECONDARY_STREAMS]
                    truncated = True
                    logger.warning(
                        "[EVENT-SYNC] Secondary stream fetch truncated at "
                        "%s streams (guard) — remaining streams will be "
                        "picked up by the next idempotent run",
                        EVENT_SYNC_MAX_SECONDARY_STREAMS,
                    )
                    break
                if not isinstance(resp, dict) or not resp.get("next"):
                    break
                page += 1

        # bead 6xxmp: optionally also fetch the MASTER group's own streams as
        # a secondary source (group_id = master_group_id). The resolver drops
        # the ones already attached to a master channel, so only a second
        # provider's unsynced streams in the same-named group survive. Mirrors
        # the secondary fetch above so preview and run see the same universe.
        if config.get("include_master_group_streams") and not truncated:
            master_group_id = config["master_group_id"]
            mgname = await self.client._channel_group_name_for_id(
                master_group_id
            )
            if not mgname:
                logger.warning(
                    "[EVENT-SYNC] Master group %s has no resolvable "
                    "channel-group name; skipping self-attach fetch",
                    master_group_id,
                )
            else:
                page = 1
                while True:
                    resp = await self.client.get_streams(
                        page=page, page_size=_EVENT_SYNC_FETCH_PAGE_SIZE,
                        channel_group_name=mgname,
                    )
                    batch = (
                        resp.get("results", []) if isinstance(resp, dict)
                        else (resp or [])
                    )
                    for s in batch:
                        if not s.get("name") or s.get("id") is None:
                            continue
                        account_id = extract_m3u_account_id(s.get("m3u_account"))
                        secondary_streams.append(SecondaryStream(
                            name=s["name"],
                            group_id=master_group_id,
                            stream_id=s.get("id"),
                            provider=account_names.get(account_id),
                            provider_id=account_id,
                            # bead jqwfq: same staleness signal for the
                            # master-group self-attach source.
                            name_seen_before_today=(
                                event_sync_staleness.name_seen_before_today(
                                    stale_lookup, account_id, mgname,
                                    s["name"],
                                )
                            ),
                        ))
                    if len(secondary_streams) >= EVENT_SYNC_MAX_SECONDARY_STREAMS:
                        secondary_streams = secondary_streams[
                            :EVENT_SYNC_MAX_SECONDARY_STREAMS]
                        logger.warning(
                            "[EVENT-SYNC] Secondary stream fetch truncated at "
                            "%s streams (guard, incl. master-group self-attach)"
                            " — remaining streams picked up next run",
                            EVENT_SYNC_MAX_SECONDARY_STREAMS,
                        )
                        break
                    if not isinstance(resp, dict) or not resp.get("next"):
                        break
                    page += 1
        return secondary_streams

    async def _resolve_event_sync_refresh_accounts(
        self, config: dict, provider_cache: dict
    ) -> set[int]:
        """Resolve a rule's scopes to the M3U account ids to pre-refresh (y8yby).

        The rule's master + secondary scopes each identify a channel group and,
        optionally, the ONE provider account that scope draws from:

        * a provider-scoped scope (``m3u_account_id`` set) → that account id
          directly;
        * a whole-group scope (``m3u_account_id`` None) → EVERY provider
          account that carries that channel group, resolved via
          ``get_m3u_group_settings_by_provider()`` (keyed by
          ``(m3u_account_id, channel_group_id)``).

        The master group is always included (its provider(s) back the master
        channels, and with ``include_master_group_streams`` it is also a stream
        source). Account ids are deduped. The by-provider settings map is
        fetched at most once per phase via ``provider_cache``.
        """
        master = config.get("master") or {
            "group_id": config.get("master_group_id"), "m3u_account_id": None,
        }
        secondary = config.get("secondary") or [
            {"group_id": g, "m3u_account_id": None}
            for g in config.get("secondary_group_ids", [])
        ]

        account_ids: set[int] = set()
        whole_group_ids: set[int] = set()
        for scope in [master, *secondary]:
            pid = scope.get("m3u_account_id")
            gid = scope.get("group_id")
            if pid is not None:
                account_ids.add(pid)
            elif gid is not None:
                whole_group_ids.add(gid)

        if whole_group_ids:
            by_provider = provider_cache.get("by_provider")
            if by_provider is None:
                try:
                    by_provider = (
                        await self.client.get_m3u_group_settings_by_provider()
                    )
                except Exception as e:
                    logger.warning(
                        "[EVENT-SYNC] Pre-refresh: failed to resolve provider "
                        "accounts for whole-group scopes (%s) — those groups' "
                        "providers will not be pre-refreshed", e,
                    )
                    by_provider = {}
                provider_cache["by_provider"] = by_provider
            for key in by_provider:
                # key is (m3u_account_id, channel_group_id).
                acc_id, group_id = key
                if group_id in whole_group_ids and acc_id is not None:
                    account_ids.add(acc_id)

        return account_ids

    async def _prerefresh_event_sync_providers(
        self, rule, config: dict, results: dict, provider_cache: dict
    ) -> None:
        """Pre-refresh a rule's M3U providers before a manual run (bead y8yby).

        Resolves the rule's master + secondary scopes to their backing M3U
        account ids and refreshes exactly those via the existing
        ``M3URefreshTask`` (``account_ids=[...]``) — synchronous refresh-then-
        run. **Best-effort**: a single failed account (or a wholesale failure)
        WARNs and records a run warning but never aborts the run — the run
        proceeds against current data, mirroring the M3U refresh watermark's
        partial-success behavior. Progress + outcome ride the execution log.

        Manual-run-only: the caller gates this out of the unattended auto-run
        path (that path already runs after a refresh).
        """
        account_ids = await self._resolve_event_sync_refresh_accounts(
            config, provider_cache
        )
        if not account_ids:
            logger.warning(
                "[EVENT-SYNC] Rule '%s' (id=%s): refresh_providers_before_run "
                "is on but no M3U provider accounts could be resolved from its "
                "scopes — skipping pre-refresh, running against current data",
                rule.name, rule.id,
            )
            results["event_sync_warnings"].append({
                "type": "event_sync_prerefresh_no_accounts",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "message": (
                    f"event_sync rule '{rule.name}': 'refresh providers "
                    f"before run' is on but no M3U provider accounts could be "
                    f"resolved from the rule's groups — the run proceeded "
                    f"without a pre-refresh, against current data."
                ),
            })
            return

        ids = sorted(account_ids)
        logger.info(
            "[EVENT-SYNC] Rule '%s' (id=%s): pre-refreshing %d M3U provider "
            "account(s) before run: %s",
            rule.name, rule.id, len(ids), ids,
        )
        results["execution_log"].append({
            "stream_id": None,
            "stream_name": (
                f"[EVENT-SYNC] {rule.name}: refreshing "
                f"{len(ids)} M3U provider(s) before run"
            ),
            "m3u_account_id": None,
            "rules_evaluated": [],
            "actions_executed": [{
                "type": "event_sync_prerefresh",
                "description": (
                    f"Refreshing {len(ids)} M3U provider account(s) "
                    f"{ids} before running rule '{rule.name}'"
                ),
            }],
        })

        from tasks.m3u_refresh import M3URefreshTask
        task = M3URefreshTask()
        task.update_config({"account_ids": ids, "skip_inactive": True})
        try:
            task_result = await task.execute()
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] Rule '%s' (id=%s): pre-refresh FAILED wholesale "
                "(%s) — best-effort, running against current data",
                rule.name, rule.id, e,
            )
            results["event_sync_warnings"].append({
                "type": "event_sync_prerefresh_failed",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "message": (
                    f"event_sync rule '{rule.name}': pre-run provider refresh "
                    f"failed ({e}) — the run proceeded against current data "
                    f"(best-effort)."
                ),
            })
            return

        ok = getattr(task_result, "success_count", 0) or 0
        failed = getattr(task_result, "failed_count", 0) or 0
        details = getattr(task_result, "details", None) or {}
        errors = details.get("errors", []) if isinstance(details, dict) else []
        if failed:
            logger.warning(
                "[EVENT-SYNC] Rule '%s' (id=%s): pre-refresh partial success "
                "(%d ok, %d failed) — best-effort, running against current "
                "data. Errors: %s",
                rule.name, rule.id, ok, failed, errors,
            )
            results["event_sync_warnings"].append({
                "type": "event_sync_prerefresh_partial",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "ok": ok,
                "failed": failed,
                "message": (
                    f"event_sync rule '{rule.name}': pre-run provider refresh "
                    f"partially failed ({ok} refreshed, {failed} failed) — "
                    f"the run proceeded against current data (best-effort). "
                    f"{'; '.join(str(x) for x in errors)}"
                ),
            })
        results["execution_log"].append({
            "stream_id": None,
            "stream_name": (
                f"[EVENT-SYNC] {rule.name}: provider pre-refresh complete"
            ),
            "m3u_account_id": None,
            "rules_evaluated": [],
            "actions_executed": [{
                "type": "event_sync_prerefresh_result",
                "description": (
                    f"Pre-run provider refresh complete: {ok} refreshed"
                    + (f", {failed} failed (best-effort)" if failed else "")
                ),
            }],
        })

    async def _run_event_sync_rules(
        self,
        event_sync_rules: list,
        executor: "ActionExecutor",
        results: dict,
        dry_run: bool,
        triggered_by: str,
        channels_touched_ids: set,
        rule_channel_order_streams: dict = None,
        stream_m3u_map: dict = None,
        rule_channel_order: dict = None,
    ) -> None:
        """Execute the event_sync attach phase (bead ti939.2.1 — Phase 1B).

        For each event_sync rule cleared by ``run_pipeline``'s manual-run
        gate: re-validate the stored config (a config predating a schema rail
        must not run under stale semantics), fetch the secondary groups'
        streams, and hand off to ``executor.execute_event_sync_rule`` — which
        resolves through ``services.event_sync_resolver.resolve_event_sync``,
        the SAME function the preview endpoint calls (dry-run parity by
        construction), and attaches via the existing merge internals.

        Per rule this phase writes: per-attach execution-log entries, the
        operator's one-line drift detector
        ("event_sync: X attached, Y ambiguous skipped, Z unmatched, W parse
        failures") into the execution log, a structured summary into
        ``results["event_sync"]``, and cap-overage / invalid-config warnings
        into ``results["event_sync_warnings"]`` (persisted onto the execution
        record by ``run_pipeline``).

        NEVER: creates/deletes channels, touches managed_channel_ids, or
        toggles Dispatcharr group settings — with ONE sanctioned, strictly
        opt-in exception (bead ti939.4.1): a rule whose config carries
        ``promote_unmatched: true`` CREATES ECM-managed channels for
        unmatched secondary-only events in its dedicated
        ``promote_target_group_id`` group, registers exactly those promoted
        channels in ``rule_channel_order`` (the managed set), and thereby
        opts into Pass 4 orphan reconciliation for them. Master-group
        channels stay Dispatcharr-owned and can never enter the managed set
        (the promotion lookup is scoped to the target group; this method
        re-asserts the invariant when registering).

        Pass 3.5 participation (bead io0tv): when the caller supplies
        ``rule_channel_order_streams``, each rule registers the master
        channels its attaches touched — INCLUDING idempotent already-attached
        no-ops — so a rule with ``stream_sort_field`` set (e.g.
        ``provider_order``) reorders its masters' streams on EVERY run, not
        only runs that performed a new attach (ordering heals after the
        operator changes M3U account priorities). ``stream_m3u_map`` is
        extended with provider ids from the secondary fetch (which still
        contains streams attached on PRIOR runs — they stay resident in
        their secondary groups) plus one bulk ``get_streams_by_ids`` read
        for the masters' own uncovered streams, so provider_order scoring
        sees every stream on the channel. Rules without a
        ``stream_sort_field`` register too but Pass 3.5 no-ops them —
        pre-feature rules keep append-only attach semantics unchanged.

        Unattended (watermark-triggered, ti939.3.1) calls add three gates on
        top of the manual path, each pinned by its own test:

        * **Per-rule trigger gate** — only rules whose config carries the
          explicit ``auto_run: true`` opt-in run; the rest are refused here
          even if a caller bypassed ``run_pipeline``'s routing.
        * **Circuit breaker (bead ixujz)** — a tripped run-on-refresh
          breaker (or the break-glass env var) refuses the WHOLE unattended
          phase before any fetch or write; manual runs are never gated (they
          are the recovery surface).
        * **Pre-flight (fail-closed)** — each opted-in rule's Dispatcharr
          group settings are checked (master auto-sync ON, secondaries OFF);
          on failure the rule is skipped with a persisted
          ``event_sync_preflight_failed`` warning that the watermark task
          turns into a notification. Manual runs never pre-flight here — the
          operator's preview carries the pre-flight surface.
        """
        from channel_pipeline_schema import validate_event_sync_config

        # Defense in depth (the braces to run_pipeline's belt): re-apply the
        # per-rule trigger gate. A future caller that reaches this phase
        # directly can still only run manually-triggered rules or explicitly
        # opted-in rules on the watermark trigger — never anything else.
        allowed_rules = []
        for rule in event_sync_rules:
            if event_sync_trigger_allowed(
                    triggered_by, rule.get_event_sync_config()):
                allowed_rules.append(rule)
            else:
                logger.warning(
                    "[EVENT-SYNC] Attach phase invoked with triggered_by=%r "
                    "— refusing event_sync rule '%s' (id=%s): not an allowed "
                    "trigger for this rule (manual-run-only unless "
                    "event_sync_config.auto_run is true)",
                    triggered_by, rule.name, rule.id,
                )
        if not allowed_rules:
            return
        event_sync_rules = allowed_rules

        unattended = triggered_by not in EVENT_SYNC_ALLOWED_TRIGGERS
        if unattended:
            # bead ixujz: a tripped circuit breaker must gate the event_sync
            # auto-run chain too. The watermark task already checks this at
            # the top of its tick (belt); this check covers any direct
            # unattended caller (braces). Lazy import — ONE definition of
            # the breaker semantics lives in tasks/channel_pipeline.py
            # (persisted flag + ECM_DISABLE_RUN_ON_REFRESH break-glass),
            # read FRESH here for the same reason it is there: the breaker
            # scenario is a restart, so a cached value is wrong.
            from tasks.channel_pipeline import _run_on_refresh_suppressed
            suppressed, reason = _run_on_refresh_suppressed()
            if suppressed:
                logger.warning(
                    "[EVENT-SYNC] Unattended attach phase SUPPRESSED (%s) — "
                    "refusing %s opted-in event_sync rule(s). Manual runs "
                    "remain available; clear the circuit breaker via POST "
                    "/api/channel-pipeline/reset-circuit-breaker to restore "
                    "auto-runs.",
                    reason, len(event_sync_rules),
                )
                results.setdefault("event_sync_warnings", []).append({
                    "type": "event_sync_auto_run_suppressed",
                    "reason": reason,
                    "message": (
                        f"event_sync auto-run suppressed ({reason}): "
                        f"{len(event_sync_rules)} opted-in rule(s) did not "
                        f"run. Manual runs are unaffected; clear the "
                        f"circuit breaker to restore unattended runs."
                    ),
                })
                return

        results.setdefault("event_sync", [])
        results.setdefault("event_sync_warnings", [])

        # bead y8yby: per-phase cache for the (m3u_account, group) settings map
        # used to resolve whole-group scopes to their backing provider accounts
        # for the optional pre-refresh-on-run step. Filled lazily on first use.
        prerefresh_provider_cache: dict = {}

        # bead yjchp: cross-rule context for the unattended pre-flight —
        # when a failing secondary group is ANOTHER enabled event_sync
        # rule's MASTER, the failure message must not advise disabling
        # auto_channel_sync (masters require it ON). Loaded once per phase
        # from ALL enabled rules, not just this run's rules — the
        # conflicting rule may not be opted into auto_run and so may be
        # absent from ``event_sync_rules``. Best-effort: a load failure
        # falls back to the untailored (still-correct check id) messages.
        all_enabled_rules: list = []
        if unattended:
            try:
                all_enabled_rules = await self._load_rules()
            except Exception as e:
                logger.warning(
                    "[EVENT-SYNC] Failed to load rules for cross-rule "
                    "pre-flight context (%s) — pre-flight messages fall "
                    "back to the generic advice", e,
                )

        # Provider display names, fetched once for the whole phase.
        try:
            accounts = await self.client.get_m3u_accounts() or []
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] Failed to fetch M3U accounts for provider "
                "names (journal provenance will carry provider=None): %s", e,
            )
            accounts = []
        account_names = {a.get("id"): a.get("name") for a in accounts}

        # Channel Group Override resolution (bead override): master channels
        # for an auto-synced SOURCE group live in its override TARGET group.
        # Fetch settings ONCE for the whole phase so each rule's master fetch
        # follows the override instead of coming back empty.
        from services.event_sync_preflight import (
            resolve_effective_master_group_id,
        )
        try:
            all_group_settings = await self.client.get_all_m3u_group_settings()
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] Failed to fetch group settings for override "
                "resolution (%s) — master fetch uses the configured group id "
                "as-is", e,
            )
            all_group_settings = {}

        for rule in event_sync_rules:
            config = rule.get_event_sync_config()
            errors = (
                ["event_sync_config is missing or corrupt"]
                if config is None else validate_event_sync_config(config)
            )
            if errors:
                # Loud skip: a stored config that no longer validates must
                # surface as a warning, never run under stale semantics and
                # never silently disappear from the run.
                logger.warning(
                    "[EVENT-SYNC] Rule '%s' (id=%s): config failed "
                    "re-validation; skipped: %s",
                    rule.name, rule.id, errors,
                )
                results["event_sync_warnings"].append({
                    "type": "event_sync_invalid_config",
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "message": (
                        f"event_sync rule '{rule.name}' skipped: stored "
                        f"config failed re-validation ({'; '.join(errors)})"
                    ),
                })
                continue
            if not config.get("enabled", True):
                logger.info(
                    "[EVENT-SYNC] Rule '%s' (id=%s): event_sync_config."
                    "enabled is false — skipped", rule.name, rule.id,
                )
                continue

            if unattended:
                # ti939.3.1: unattended runs pre-flight the Dispatcharr
                # group settings and FAIL CLOSED — no operator is watching,
                # so a rule whose preconditions cannot be verified (master
                # auto-sync OFF, missing group, or the check itself failing)
                # is skipped with a persisted warning the task layer turns
                # into a notification. Manual runs never pre-flight here;
                # the preview is the operator's pre-flight surface.
                from services.event_sync_preflight import (
                    build_event_sync_master_rule_lookup,
                    check_event_sync_group_settings,
                )
                try:
                    preflight = await check_event_sync_group_settings(
                        self.client, config,
                        other_master_rules=build_event_sync_master_rule_lookup(
                            all_enabled_rules, exclude_rule_id=rule.id
                        ),
                    )
                except Exception as e:
                    preflight = {
                        "ok": False,
                        "failures": [],
                        "error": str(e),
                    }
                if not preflight["ok"]:
                    failure_text = "; ".join(
                        f["message"] for f in preflight["failures"]
                    ) or f"pre-flight check failed ({preflight.get('error')})"
                    logger.warning(
                        "[EVENT-SYNC] Rule '%s' (id=%s): unattended "
                        "pre-flight failed; rule skipped this run: %s",
                        rule.name, rule.id, failure_text,
                    )
                    results["event_sync_warnings"].append({
                        "type": "event_sync_preflight_failed",
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "failures": preflight["failures"],
                        "message": (
                            f"event_sync rule '{rule.name}' skipped on the "
                            f"unattended run: pre-flight failed — "
                            f"{failure_text} Fix the Dispatcharr group "
                            f"settings in M3U Manager or via the rule "
                            f"editor's confirmed Fix button (ECM never "
                            f"toggles auto_channel_sync unattended); the "
                            f"rule runs again on the next refresh."
                        ),
                    })
                    continue

            # bead y8yby: optional pre-refresh of the rule's M3U providers
            # before a MANUAL run (live Run AND dry-run Test), so the match
            # runs against fresh provider data — closing the refresh-ordering
            # staleness window. NEVER on the unattended auto-run path: that
            # path already fires right after an M3U refresh, so pre-refreshing
            # there would be circular. Best-effort: a failed provider refresh
            # warns but the run PROCEEDS against current data (mirrors the M3U
            # refresh watermark's partial-success posture). Runs before the
            # secondary fetch below so the fetch sees post-refresh streams.
            if not unattended and config.get("refresh_providers_before_run"):
                await self._prerefresh_event_sync_providers(
                    rule, config, results, prerefresh_provider_cache
                )

            try:
                secondary_streams = (
                    await self._fetch_event_sync_secondary_streams(
                        config, account_names
                    )
                )
            except Exception as e:
                logger.warning(
                    "[EVENT-SYNC] Rule '%s' (id=%s): secondary stream fetch "
                    "failed; rule skipped this run: %s",
                    rule.name, rule.id, e,
                )
                results["event_sync_warnings"].append({
                    "type": "event_sync_fetch_failed",
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "message": (
                        f"event_sync rule '{rule.name}' skipped: secondary "
                        f"stream fetch failed ({e})"
                    ),
                })
                continue

            # ti939.3.2: load the rule's prior review decisions
            # (fingerprint-keyed accepts/rejects) — an INPUT to the shared
            # resolver for BOTH live and dry runs, so dry-run classification
            # stays byte-identical to the live run it predicts.
            from services.event_sync_exclusion_store import (
                load_exclusion_keys,
            )
            from services.event_sync_review_store import (
                load_review_decisions,
            )
            decisions = None
            exclusions = None
            try:
                db = get_session()
                try:
                    decisions = load_review_decisions(db, rule.id)
                    # ti939.3.5: operator never-attach exclusions ride the
                    # same load so run and preview consult the same set.
                    exclusions = load_exclusion_keys(db, rule.id)
                finally:
                    db.close()
            except Exception as e:
                logger.warning(
                    "[EVENT-SYNC] Rule '%s' (id=%s): failed to load review "
                    "decisions/exclusions (%s) — running without them; "
                    "accepted pairings will not auto-attach and operator "
                    "exclusions will not suppress this run",
                    rule.name, rule.id, e,
                )

            effective_master_group_id = resolve_effective_master_group_id(
                all_group_settings, config["master_group_id"]
            )
            exec_ctx = ExecutionContext(dry_run=dry_run)
            summary = await executor.execute_event_sync_rule(
                rule.id, rule.name, config, secondary_streams, exec_ctx,
                decisions=decisions,
                effective_master_group_id=effective_master_group_id,
                exclusions=exclusions,
            )

            # ti939.3.2: persist the run's ambiguous pairings as pending
            # review rows — live runs only (a dry run mutates nothing; it
            # reports the would-enqueue count instead). Dedup against
            # pending AND answered fingerprints is DB-enforced by the
            # unique fingerprint index + the store's status check.
            review_payloads = summary.pop("review_candidates", [])
            summary["review_enqueued"] = 0
            summary["review_refreshed"] = 0
            summary["review_would_enqueue"] = (
                len(review_payloads) if dry_run else 0
            )
            if review_payloads and not dry_run:
                from services.event_sync_review import (
                    MAX_REVIEW_ENQUEUE_PER_RUN,
                )
                from services.event_sync_review_store import (
                    enqueue_review_candidates,
                )
                if len(review_payloads) > MAX_REVIEW_ENQUEUE_PER_RUN:
                    overflow = (
                        len(review_payloads) - MAX_REVIEW_ENQUEUE_PER_RUN
                    )
                    review_payloads = review_payloads[
                        :MAX_REVIEW_ENQUEUE_PER_RUN]
                    logger.warning(
                        "[EVENT-SYNC] Rule '%s' (id=%s): review enqueue "
                        "capped at %s this run (%s deferred) — runs are "
                        "idempotent, the remainder re-surfaces next run",
                        rule.name, rule.id, MAX_REVIEW_ENQUEUE_PER_RUN,
                        overflow,
                    )
                    results["event_sync_warnings"].append({
                        "type": "event_sync_review_enqueue_capped",
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "cap": MAX_REVIEW_ENQUEUE_PER_RUN,
                        "overage": overflow,
                        "message": (
                            f"event_sync rule '{rule.name}': review-queue "
                            f"enqueue capped at "
                            f"{MAX_REVIEW_ENQUEUE_PER_RUN} pairings this "
                            f"run ({overflow} deferred to the next run). "
                            f"A cap this size usually means a broken parse "
                            f"pattern — check the preview."
                        ),
                    })
                try:
                    db = get_session()
                    try:
                        counts = enqueue_review_candidates(
                            db, rule.id, review_payloads
                        )
                    finally:
                        db.close()
                    summary["review_enqueued"] = counts["enqueued"]
                    summary["review_refreshed"] = counts["refreshed"]
                except Exception as e:
                    logger.warning(
                        "[EVENT-SYNC] Rule '%s' (id=%s): review enqueue "
                        "failed (%s) — ambiguous matches were skipped as "
                        "before but NOT queued this run",
                        rule.name, rule.id, e,
                    )
                    results["event_sync_warnings"].append({
                        "type": "event_sync_review_enqueue_failed",
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "message": (
                            f"event_sync rule '{rule.name}': failed to "
                            f"enqueue ambiguous matches for review ({e})"
                        ),
                    })

            # Aggregate the executor context exactly like Pass 2 does so
            # streams_merged / channels_touched / modified_entities (legacy
            # rollback state) stay consistent across every merge path.
            results["streams_merged"] += exec_ctx.streams_merged
            results["streams_skipped"] += exec_ctx.streams_skipped
            results["modified_entities"].extend(exec_ctx.modified_entities)
            # bead ti939.4.1: promotion is the only event_sync path that
            # creates channels; for every other rule these are 0/empty, so
            # the aggregation is a no-op (AC-1 byte-identical). setdefault
            # tolerates direct callers (tests) that pass a minimal results
            # dict without the standard-run keys.
            results.setdefault("created_entities", []).extend(
                exec_ctx.created_entities
            )
            results["channels_created"] = (
                results.get("channels_created", 0) + exec_ctx.channels_created
            )
            channels_touched_ids.update(exec_ctx.merged_channel_ids)
            results["rule_match_counts"][rule.id] = summary["attached"]

            # Per-attach execution-log entries (bounded by the shared
            # BoundedExecutionLog chokepoint).
            attach_entries = summary.pop("attach_entries")
            for entry in attach_entries:
                match = entry.get("match") or {}
                results["execution_log"].append({
                    "stream_id": match.get("secondary_stream_id"),
                    "stream_name": match.get("secondary_stream_name"),
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [entry],
                })
                if dry_run:
                    results["dry_run_results"].append({
                        "stream_id": match.get("secondary_stream_id"),
                        "stream_name": match.get("secondary_stream_name"),
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "action": entry["description"],
                        "would_create": False,
                        "would_modify": not entry.get("skipped", False),
                    })

            # =============================================================
            # Unmatched-stream promotion (bead ti939.4.1) — present on the
            # summary ONLY when the rule's config opted in, so everything
            # in this block is unreachable for promotion-less rules.
            # =============================================================
            promotion = summary.get("promotion")
            if promotion is not None:
                # Execution-log + dry-run rows per promotion entry (the
                # bounded-log chokepoint applies, mirroring attach entries).
                for entry in promotion.pop("promote_entries"):
                    match = entry.get("match") or {}
                    results["execution_log"].append({
                        "stream_id": match.get("secondary_stream_id"),
                        "stream_name": match.get("secondary_stream_name"),
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [entry],
                    })
                    if dry_run:
                        results["dry_run_results"].append({
                            "stream_id": match.get("secondary_stream_id"),
                            "stream_name": match.get("secondary_stream_name"),
                            "rule_id": rule.id,
                            "rule_name": rule.name,
                            "action": entry["description"],
                            "would_create": entry["type"] == "event_sync_promote"
                                             and not entry.get("skipped", False),
                            "would_modify": not entry.get("skipped", False),
                        })

                # Register the promoted channels as THIS rule's managed set
                # (the "current" side of Pass 4 reconciliation). INVARIANT
                # (dedicated test): the managed set contains ONLY channels
                # in the promotion target group — a Dispatcharr-owned
                # master can never leak in. The executor's scoped lookup
                # already guarantees this; re-assert here so a future bug
                # upstream turns into a loud WARN + drop, never a Pass 4
                # delete of a channel ECM does not own.
                if rule_channel_order is not None:
                    target_gid = promotion.get("target_group_id")
                    order_list = rule_channel_order[rule.id]
                    for cid in promotion["channel_ids"]:
                        ch = executor._channel_by_id.get(cid) or {}
                        if ch.get("channel_group_id") != target_gid:
                            logger.warning(
                                "[EVENT-SYNC] Rule '%s' (id=%s): promoted "
                                "channel id=%s is NOT in the promotion "
                                "target group %s — REFUSING to register it "
                                "in the managed set (ownership invariant)",
                                rule.name, rule.id, cid, target_gid,
                            )
                            continue
                        if cid not in order_list:
                            order_list.append(cid)
                    # Mark this rule as reconcile-eligible THIS run: the
                    # promotion pass ran to completion, so an empty managed
                    # set means "no justifying stream observed" (delete is
                    # correct), not "the rule never got to look" (fetch
                    # failure / invalid config / disabled — those `continue`
                    # above and never reach here, so Pass 4 must not
                    # reconcile them; a transient fetch failure must never
                    # mass-delete promoted channels).
                    results.setdefault(
                        "event_sync_promotion_reconcile_rule_ids", set()
                    ).add(rule.id)

                # Cap-overage warning surface (mirrors event_sync_attach_
                # capped).
                if promotion["capped"]:
                    promote_cap_message = (
                        f"event_sync rule '{rule.name}': per-run promotion "
                        f"cap ({promotion['cap']}) reached — "
                        f"{promotion['cap_overage']} unmatched event(s) "
                        f"were NOT promoted this run. Promotion is "
                        f"idempotent: run again to continue, or raise "
                        f"max_promote_per_run on the rule."
                    )
                    results["event_sync_warnings"].append({
                        "type": "event_sync_promote_capped",
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "cap": promotion["cap"],
                        "overage": promotion["cap_overage"],
                        "message": promote_cap_message,
                    })
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": (
                            f"[EVENT-SYNC] {rule.name}: promotion cap reached"
                        ),
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "event_sync_promote_capped",
                            "description": promote_cap_message,
                            "success": True,
                            "entity_id": None,
                            "error": None,
                        }],
                    })

            # bead io0tv: register this rule's touched master channels for
            # Pass 3.5 stream reorder. Two sources, unioned:
            # * exec_ctx.merged_channel_ids — masters that received a REAL
            #   attach this run (the add_result chokepoint; live and dry-run);
            # * skipped attach_entries — masters whose stream was ALREADY
            #   attached (idempotent no-op). Registering these too means a
            #   stream_sort_field rule re-heals ordering on every run, not
            #   only on runs that performed a new attach (attach is
            #   idempotent, so steady-state runs are mostly no-ops).
            # Dedupe preserves first-seen order (same pattern as Pass 2's
            # rule_channel_order_streams append).
            if rule_channel_order_streams is not None:
                order_list = rule_channel_order_streams.setdefault(rule.id, [])
                reorder_cids = list(exec_ctx.merged_channel_ids)
                reorder_cids.extend(
                    entry["entity_id"] for entry in attach_entries
                    if entry.get("success") and entry.get("entity_id") is not None
                )
                for cid in reorder_cids:
                    if cid not in order_list:
                        order_list.append(cid)

                # Provider coverage for provider_order / quality-tie-break
                # scoring: without a stream_id -> m3u_account_id entry a
                # stream scores priority 0 and sinks. The secondary fetch
                # already carries provider_id for every secondary-group
                # stream — including ones attached on PRIOR runs, which stay
                # resident in their secondary groups.
                if stream_m3u_map is not None:
                    for s in secondary_streams:
                        if s.stream_id is not None \
                                and s.stream_id not in stream_m3u_map:
                            stream_m3u_map[s.stream_id] = s.provider_id

                    # The masters' own streams (Dispatcharr-attached from the
                    # master group's provider) are NOT in the secondary fetch
                    # unless include_master_group_streams is on — resolve the
                    # remainder with one bulk read. Only when this rule will
                    # actually sort (stream_sort_field set): otherwise the
                    # call would be a wasted round-trip.
                    if getattr(rule, "stream_sort_field", None):
                        uncovered: list[int] = []
                        for cid in order_list:
                            ch = executor._channel_by_id.get(cid) or {}
                            for s in ch.get("streams", []) or []:
                                sid = s["id"] if isinstance(s, dict) else s
                                if sid is not None \
                                        and sid not in stream_m3u_map \
                                        and sid not in uncovered:
                                    uncovered.append(sid)
                        if uncovered:
                            try:
                                from stream_prober import extract_m3u_account_id
                                fetched = await self.client.get_streams_by_ids(
                                    uncovered
                                )
                                for s in fetched or []:
                                    sid = s.get("id")
                                    if sid is not None \
                                            and sid not in stream_m3u_map:
                                        stream_m3u_map[sid] = (
                                            extract_m3u_account_id(
                                                s.get("m3u_account")
                                            )
                                        )
                            except Exception as e:
                                logger.warning(
                                    "[EVENT-SYNC] Rule '%s' (id=%s): failed to "
                                    "resolve M3U accounts for %s master "
                                    "stream(s) (%s) — they score priority 0 "
                                    "in this run's stream reorder",
                                    rule.name, rule.id, len(uncovered), e,
                                )

            # Attach-cap overage: WARN surface on the execution record
            # (the executor already logger.warning'd when the cap tripped).
            if summary["capped"]:
                cap_message = (
                    f"event_sync rule '{rule.name}': per-run attach cap "
                    f"({summary['cap']}) reached — {summary['cap_overage']} "
                    f"would-attach stream(s) were NOT attached this run. "
                    f"event_sync is idempotent: run it again to continue, "
                    f"or raise max_attach_per_run on the rule."
                )
                results["event_sync_warnings"].append({
                    "type": "event_sync_attach_capped",
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "cap": summary["cap"],
                    "overage": summary["cap_overage"],
                    "message": cap_message,
                })
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": f"[EVENT-SYNC] {rule.name}: attach cap reached",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "event_sync_capped",
                        "description": cap_message,
                        "success": True,
                        "entity_id": None,
                        "error": None,
                    }],
                })

            # The operator's one-line drift detector — a silently broken
            # parse pattern must be visible in one line within a day.
            summary_line = (
                f"event_sync: {summary['attached']} attached, "
                f"{summary['ambiguous_skipped']} ambiguous skipped, "
                f"{summary['unmatched']} unmatched, "
                f"{summary['parse_failed']} parse failures"
            )
            extras = []
            if summary["already_attached"]:
                extras.append(f"{summary['already_attached']} already attached")
            # ti939.3.2 review-queue extras: queue-driven attaches and
            # newly queued questions must be visible in the one-line drift
            # detector too.
            if summary.get("queue_attached"):
                extras.append(
                    f"{summary['queue_attached']} via review queue"
                )
            if summary.get("review_enqueued"):
                extras.append(
                    f"{summary['review_enqueued']} queued for review"
                )
            if summary.get("review_would_enqueue"):
                extras.append(
                    f"{summary['review_would_enqueue']} would queue for review"
                )
            if summary.get("rejected_suppressed"):
                extras.append(
                    f"{summary['rejected_suppressed']} suppressed by "
                    f"rejected reviews"
                )
            # ti939.3.5: operator never-attach exclusions must be visible
            # in the one-line drift detector too.
            if summary.get("excluded_by_operator"):
                extras.append(
                    f"{summary['excluded_by_operator']} excluded by operator"
                )
            elif summary.get("excluded_suppressed"):
                extras.append(
                    f"{summary['excluded_suppressed']} pairing(s) "
                    f"suppressed by operator exclusions"
                )
            # bead ti939.4.1: promotion visibility in the one-line drift
            # detector — a run that created/deleted-by-reconcile channels
            # must be readable in one line. Key absent for promotion-less
            # rules, so their line is byte-identical.
            if summary.get("promotion") is not None:
                promo = summary["promotion"]
                extras.append(
                    f"{promo['promoted_created']} promoted, "
                    f"{promo['promoted_adopted']} promoted-adopted"
                )
                if promo["capped"]:
                    extras.append(
                        f"promotion capped at {promo['cap']}, "
                        f"{promo['cap_overage']} deferred"
                    )
            if summary["capped"]:
                extras.append(
                    f"capped at {summary['cap']}, "
                    f"{summary['cap_overage']} deferred"
                )
            if summary["attach_errors"]:
                extras.append(f"{summary['attach_errors']} attach errors")
            if extras:
                summary_line += f" ({'; '.join(extras)})"
            summary["summary_line"] = summary_line

            logger.info(
                "[EVENT-SYNC] Rule '%s' (id=%s): %s",
                rule.name, rule.id, summary_line,
            )
            results["execution_log"].append({
                "stream_id": None,
                "stream_name": f"[EVENT-SYNC] {rule.name}",
                "m3u_account_id": None,
                "rules_evaluated": [],
                "actions_executed": [{
                    "type": "event_sync_summary",
                    "description": summary_line,
                    "success": True,
                    "entity_id": None,
                    "error": None,
                }],
            })

            # ti939.3.3: dummy EPG auto-assignment for master channels —
            # every run (manual AND unattended), after the attach phase.
            # Deferred assignments ride the EXISTING Pass 5 refresh-and-
            # retry via executor._deferred_epg_assignments.
            if config.get("dummy_epg_profile_id"):
                epg_summary = await self._assign_event_sync_dummy_epg(
                    rule, config, executor, exec_ctx, results, dry_run
                )
                if epg_summary is not None:
                    summary["dummy_epg"] = epg_summary

            results["event_sync"].append(summary)

    async def _assign_event_sync_dummy_epg(
        self,
        rule,
        config: dict,
        executor: "ActionExecutor",
        exec_ctx: "ExecutionContext",
        results: dict,
        dry_run: bool,
    ) -> Optional[dict]:
        """One event_sync rule's dummy EPG assignment step (ti939.3.3).

        Thin engine wrapper around
        ``executor.assign_event_sync_dummy_epg``: resolves the profile row
        (disabled profile → warning + skip, so a convenience toggle never
        kills the rule's attach path; the VALIDATOR already guaranteed
        existence), surfaces a missing Dispatcharr source as a run warning,
        writes per-channel and summary execution-log entries, and folds the
        step's channel updates into the run counters. Returns the step
        summary (stored on the rule's event_sync summary), or None when the
        step was skipped.

        NEVER: creates/deletes channels or writes Dispatcharr group
        settings — the step is epg_data_id metadata on existing master
        channels.
        """
        from models import DummyEPGProfile

        profile_id = config["dummy_epg_profile_id"]
        db = get_session()
        try:
            profile = db.get(DummyEPGProfile, profile_id)
        finally:
            db.close()
        if profile is None:
            # The validator refuses dangling ids, so this is a race
            # (profile deleted mid-run) — warn, never crash the run.
            results["event_sync_warnings"].append({
                "type": "event_sync_dummy_epg_profile_missing",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "message": (
                    f"event_sync rule '{rule.name}': dummy EPG profile "
                    f"{profile_id} no longer exists — dummy EPG assignment "
                    f"skipped this run. Remove dummy_epg_profile_id from "
                    f"the rule or recreate the profile."
                ),
            })
            return None
        if not profile.enabled:
            results["event_sync_warnings"].append({
                "type": "event_sync_dummy_epg_profile_disabled",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "message": (
                    f"event_sync rule '{rule.name}': dummy EPG profile "
                    f"'{profile.name}' (id={profile_id}) is disabled — "
                    f"dummy EPG assignment skipped this run (attaches are "
                    f"unaffected). Enable the profile to resume guide-data "
                    f"assignment."
                ),
            })
            return None

        # Delta-fold the step's counters: the attach phase already extended
        # results with this exec_ctx's totals, so only what THIS step adds
        # may be folded again.
        modified_before = len(exec_ctx.modified_entities)
        updated_before = exec_ctx.channels_updated

        epg_summary = await executor.assign_event_sync_dummy_epg(
            rule.id, rule.name, config, exec_ctx
        )
        epg_summary["profile_name"] = profile.name

        results["modified_entities"].extend(
            exec_ctx.modified_entities[modified_before:]
        )
        results["channels_updated"] += (
            exec_ctx.channels_updated - updated_before
        )

        if epg_summary["source_id"] is None:
            results["event_sync_warnings"].append({
                "type": "event_sync_dummy_epg_no_source",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "profile_id": profile_id,
                "message": (
                    f"event_sync rule '{rule.name}': no Dispatcharr EPG "
                    f"source serves dummy EPG profile '{profile.name}' "
                    f"(id={profile_id}) — add an XMLTV source pointing at "
                    f"ECM's /api/dummy-epg/xmltv/{profile_id} endpoint, "
                    f"then run again. No guide data was assigned."
                ),
            })

        # Per-channel entries (bounded by the shared BoundedExecutionLog
        # chokepoint), mirroring the attach entries' shape.
        for entry in epg_summary.pop("assign_entries"):
            results["execution_log"].append({
                "stream_id": None,
                "stream_name": f"[EVENT-SYNC EPG] {entry['entity_name']}",
                "m3u_account_id": None,
                "rules_evaluated": [],
                "actions_executed": [entry],
            })
            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": None,
                    "stream_name": f"[EVENT-SYNC EPG] {entry['entity_name']}",
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "action": entry["description"],
                    "would_create": False,
                    "would_modify": entry["success"],
                })

        epg_line = (
            f"event_sync dummy EPG ('{profile.name}'): "
            f"{epg_summary['assigned']} assigned, "
            f"{epg_summary['deferred']} deferred to Pass 5, "
            f"{epg_summary['already_assigned']} already assigned"
        )
        extras = []
        if epg_summary["skipped_foreign_epg"]:
            extras.append(
                f"{epg_summary['skipped_foreign_epg']} kept foreign EPG"
            )
        if epg_summary["failed"]:
            extras.append(f"{epg_summary['failed']} failed")
        if extras:
            epg_line += f" ({'; '.join(extras)})"
        epg_summary["summary_line"] = epg_line

        logger.info(
            "[EVENT-SYNC] Rule '%s' (id=%s): %s",
            rule.name, rule.id, epg_line,
        )
        results["execution_log"].append({
            "stream_id": None,
            "stream_name": f"[EVENT-SYNC EPG] {rule.name}",
            "m3u_account_id": None,
            "rules_evaluated": [],
            "actions_executed": [{
                "type": "event_sync_dummy_epg_summary",
                "description": epg_line,
                "success": epg_summary["failed"] == 0,
                "entity_id": None,
                "error": None,
            }],
        })
        return epg_summary

    # =========================================================================
    # Pass 6: Batch probe streams queued by probe_streams actions
    # =========================================================================

    async def _batch_probe_streams(
        self,
        probe_stream_ids: set[int],
        streams: list,
        results: dict,
        dry_run: bool
    ):
        """Probe streams that were queued by probe_streams actions."""
        from stream_prober import get_prober

        # Build lookup from stream contexts
        stream_lookup = {}
        for stream in streams:
            if stream.stream_id in probe_stream_ids and stream.stream_url:
                stream_lookup[stream.stream_id] = (stream.stream_url, stream.stream_name)

        if not stream_lookup:
            logger.info("[AUTO-CREATE-ENGINE] Pass 6: no probeable streams found in queue of %s", len(probe_stream_ids))
            return

        count = len(stream_lookup)
        logger.info("[AUTO-CREATE-ENGINE] Pass 6: probing %s stream(s) queued by probe_streams actions", count)

        if dry_run:
            results["dry_run_results"].append({
                "stream_id": None,
                "stream_name": "[Pass 6] Probe Streams",
                "rule_id": None,
                "rule_name": None,
                "action": f"Would probe {count} stream(s)",
                "would_create": False,
                "would_modify": True
            })
            return

        prober = get_prober()
        if not prober:
            logger.warning("[AUTO-CREATE-ENGINE] Pass 6: prober not available, skipping")
            results["execution_log"].append({
                "stream_name": "[Pass 6] Probe Streams",
                "actions_executed": [{"type": "probe_streams", "description": "Prober not available, skipped"}]
            })
            return

        semaphore = asyncio.Semaphore(3)
        probed = 0

        async def probe_one(stream_id, url, name):
            nonlocal probed
            async with semaphore:
                try:
                    await prober.probe_stream(stream_id, url, name)
                    probed += 1
                except Exception as e:
                    logger.warning("[AUTO-CREATE-ENGINE] Pass 6: failed to probe stream %s (%s): %s", stream_id, name, e)

        tasks = [
            probe_one(sid, url, name)
            for sid, (url, name) in stream_lookup.items()
        ]
        await asyncio.gather(*tasks)

        results["streams_probed"] = probed
        logger.info("[AUTO-CREATE-ENGINE] Pass 6: probed %s/%s stream(s)", probed, count)

        results["execution_log"].append({
            "stream_name": "[Pass 6] Probe Streams",
            "actions_executed": [{
                "type": "probe_streams",
                "description": f"Probed {probed}/{count} stream(s)"
            }]
        })

    # =========================================================================
    # Pass 5: Dummy EPG Refresh + Retry Deferred Assignments
    # =========================================================================

    async def _refresh_dummy_epg_and_retry(
        self, executor, results: dict, epg_sources: list, dry_run: bool
    ):
        """
        Refresh dummy EPG sources and retry deferred assign_epg actions.

        Steps reported (both dry-run and live):
        1. Auto-add target group IDs to dummy EPG profiles if missing
        2. Regenerate XMLTV cache
        3. Refresh each Dispatcharr EPG source + poll for completion
        4. Re-fetch EPG data
        5. Retry each deferred assign_epg action
        """
        from database import get_session
        from models import DummyEPGProfile

        # Collect unique dummy source IDs and target group IDs from deferred list
        dummy_source_ids = set()
        target_group_ids = set()
        for channel_id, action, stream_ctx, exec_ctx in executor._deferred_epg_assignments:
            epg_source_id = action.params.get("epg_id")
            if epg_source_id is not None:
                dummy_source_ids.add(epg_source_id)
            channel = executor._channel_by_id.get(channel_id, {})
            # Dispatcharr API returns "channel_group", executor payload uses "channel_group_id"
            gid = channel.get("channel_group_id") or channel.get("channel_group")
            if gid:
                target_group_ids.add(gid)

        logger.info(
            "[AUTO-CREATE-ENGINE] Pass 5: dummy sources=%s, target groups=%s",
            dummy_source_ids, target_group_ids
        )

        # Build source lookup
        source_by_id = {s["id"]: s for s in epg_sources}

        # Match dummy source IDs to profile IDs via URL pattern
        import re as _re
        profile_ids_to_update = set()
        for src_id in dummy_source_ids:
            src = source_by_id.get(src_id)
            if not src:
                continue
            url = src.get("url", "")
            m = _re.search(r'/api/dummy-epg/xmltv/(\d+)', url)
            if m:
                profile_ids_to_update.add(int(m.group(1)))
            else:
                profile_ids_to_update = None
                break

        # Resolve profile names for reporting
        profile_names = {}
        db = get_session()
        try:
            if profile_ids_to_update is None:
                profiles = db.query(DummyEPGProfile).filter(
                    DummyEPGProfile.enabled == True  # noqa: E712
                ).all()
            else:
                profiles = db.query(DummyEPGProfile).filter(
                    DummyEPGProfile.id.in_(profile_ids_to_update),
                    DummyEPGProfile.enabled == True  # noqa: E712
                ).all()

            for profile in profiles:
                profile_names[profile.id] = profile.name
                existing_groups = set(profile.get_channel_group_ids())
                missing = target_group_ids - existing_groups

                # Step 1: Auto-add target groups to profiles
                if missing and target_group_ids:
                    group_names = [
                        executor._group_by_id.get(gid, {}).get("name", f"ID:{gid}")
                        for gid in missing
                    ]
                    step1_desc = (
                        f"Add groups {group_names} to dummy EPG profile "
                        f"'{profile.name}' (id={profile.id})"
                    )
                    if dry_run:
                        results["dry_run_results"].append({
                            "stream_id": None,
                            "stream_name": "[Pass 5] Update Profile Groups",
                            "rule_id": None,
                            "rule_name": None,
                            "action": f"Would {step1_desc.lower()}",
                            "would_create": False,
                            "would_modify": True
                        })
                    else:
                        updated = list(existing_groups | target_group_ids)
                        profile.set_channel_group_ids(updated)
                        db.merge(profile)
                        logger.info("[AUTO-CREATE-ENGINE] Pass 5: %s", step1_desc)

                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": "[Pass 5] Update Profile Groups",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "update_epg_profile",
                            "description": ("Would " if dry_run else "") + step1_desc,
                            "success": True,
                            "entity_id": profile.id,
                            "error": None
                        }]
                    })

            if not dry_run:
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error("[AUTO-CREATE-ENGINE] Pass 5: failed to update profile groups: %s", e)
        finally:
            db.close()

        # Step 2: Regenerate XMLTV cache
        profile_label = ", ".join(
            f"'{profile_names.get(pid, pid)}'" for pid in (profile_ids_to_update or profile_names.keys())
        ) or "all enabled profiles"
        step2_desc = f"Regenerate XMLTV cache for {profile_label}"

        if dry_run:
            results["dry_run_results"].append({
                "stream_id": None,
                "stream_name": "[Pass 5] Regenerate XMLTV",
                "rule_id": None,
                "rule_name": None,
                "action": f"Would regenerate XMLTV cache for {profile_label}",
                "would_create": False,
                "would_modify": True
            })
        else:
            try:
                from tasks.dummy_epg_refresh import DummyEPGRefreshTask
                task = DummyEPGRefreshTask()
                profile_count = await task._regenerate_xmltv()
                step2_desc = f"Regenerated XMLTV cache for {profile_count} profiles"
                logger.info("[AUTO-CREATE-ENGINE] Pass 5: %s", step2_desc)
            except Exception as e:
                logger.exception("[AUTO-CREATE-ENGINE] Pass 5: failed to regenerate XMLTV: %s", e)
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": "[Pass 5] Regenerate XMLTV",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "regenerate_xmltv",
                        "description": f"Failed to regenerate XMLTV: {e}",
                        "success": False,
                        "entity_id": None,
                        "error": str(e)
                    }]
                })
                return

        results["execution_log"].append({
            "stream_id": None,
            "stream_name": "[Pass 5] Regenerate XMLTV",
            "m3u_account_id": None,
            "rules_evaluated": [],
            "actions_executed": [{
                "type": "regenerate_xmltv",
                "description": ("Would regenerate" if dry_run else "Regenerated")
                               + f" XMLTV cache for {profile_label}",
                "success": True,
                "entity_id": None,
                "error": None
            }]
        })

        # Step 3: Refresh each Dispatcharr EPG source
        for src_id in dummy_source_ids:
            src = source_by_id.get(src_id)
            source_name = src.get("name", f"Source {src_id}") if src else f"Source {src_id}"

            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": None,
                    "stream_name": f"[Pass 5] Refresh EPG Source",
                    "rule_id": None,
                    "rule_name": None,
                    "action": f"Would refresh Dispatcharr EPG source '{source_name}' (id={src_id})",
                    "would_create": False,
                    "would_modify": True
                })
            else:
                try:
                    from tasks.dummy_epg_refresh import wait_for_epg_source_refresh
                    await wait_for_epg_source_refresh(
                        self.client, src_id, source_name,
                        poll_interval=3, max_wait=120
                    )
                except Exception as e:
                    logger.error(
                        "[AUTO-CREATE-ENGINE] Pass 5: failed to refresh source %s: %s",
                        source_name, e
                    )

            results["execution_log"].append({
                "stream_id": None,
                "stream_name": f"[Pass 5] Refresh EPG Source",
                "m3u_account_id": None,
                "rules_evaluated": [],
                "actions_executed": [{
                    "type": "refresh_epg_source",
                    "description": ("Would refresh" if dry_run else "Refreshed")
                                   + f" EPG source '{source_name}' (id={src_id})",
                    "success": True,
                    "entity_id": src_id,
                    "error": None
                }]
            })

        # Step 4: Re-fetch EPG data
        if dry_run:
            results["dry_run_results"].append({
                "stream_id": None,
                "stream_name": "[Pass 5] Reload EPG Data",
                "rule_id": None,
                "rule_name": None,
                "action": "Would re-fetch EPG data from Dispatcharr",
                "would_create": False,
                "would_modify": False
            })
            results["execution_log"].append({
                "stream_id": None,
                "stream_name": "[Pass 5] Reload EPG Data",
                "m3u_account_id": None,
                "rules_evaluated": [],
                "actions_executed": [{
                    "type": "reload_epg_data",
                    "description": "Would re-fetch EPG data from Dispatcharr",
                    "success": True,
                    "entity_id": None,
                    "error": None
                }]
            })
        else:
            try:
                new_epg_data = await self.client.get_epg_data()
                executor.reload_epg_data(new_epg_data)
                logger.info("[AUTO-CREATE-ENGINE] Pass 5: reloaded %s EPG data entries", len(new_epg_data))
                results["execution_log"].append({
                    "stream_id": None,
                    "stream_name": "[Pass 5] Reload EPG Data",
                    "m3u_account_id": None,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "reload_epg_data",
                        "description": f"Reloaded {len(new_epg_data)} EPG data entries",
                        "success": True,
                        "entity_id": None,
                        "error": None
                    }]
                })
            except Exception as e:
                logger.error("[AUTO-CREATE-ENGINE] Pass 5: failed to re-fetch EPG data: %s", e)
                return

        # Step 5: Retry each deferred assign_epg
        # Snapshot and clear to prevent re-deferring into the same list during retry
        deferred_snapshot = list(executor._deferred_epg_assignments)
        executor._deferred_epg_assignments.clear()

        retry_success = 0
        retry_failed = 0
        from channel_pipeline_schema import Action
        for channel_id, action, stream_ctx, exec_ctx in deferred_snapshot:
            epg_source_id = action.params.get("epg_id")
            src = source_by_id.get(epg_source_id)
            source_name = src.get("name", f"Source {epg_source_id}") if src else f"Source {epg_source_id}"
            channel = executor._channel_by_id.get(channel_id, {})
            channel_name = channel.get("name", f"Channel {channel_id}")

            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": stream_ctx.stream_id,
                    "stream_name": f"[Pass 5 Retry] {stream_ctx.stream_name}",
                    "rule_id": None,
                    "rule_name": None,
                    "action": f"Would retry assign_epg from '{source_name}' "
                              f"(id={epg_source_id}) to channel '{channel_name}'",
                    "would_create": False,
                    "would_modify": True
                })
                results["execution_log"].append({
                    "stream_id": stream_ctx.stream_id,
                    "stream_name": f"[Pass 5 Retry] {stream_ctx.stream_name}",
                    "m3u_account_id": stream_ctx.m3u_account_id,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": "assign_epg",
                        "description": f"Would retry assign_epg from '{source_name}' "
                                       f"(id={epg_source_id}) to channel '{channel_name}'",
                        "success": True,
                        "entity_id": channel_id,
                        "error": None
                    }]
                })
                retry_success += 1
            else:
                action_obj = Action.from_dict(
                    action.to_dict() if hasattr(action, 'to_dict')
                    else (action if isinstance(action, dict)
                          else {"type": action.type, **action.params})
                )
                retry_result = await executor._execute_assign_epg(action_obj, stream_ctx, exec_ctx)
                if retry_result.success and not retry_result.deferred:
                    retry_success += 1
                else:
                    retry_failed += 1
                    logger.warning(
                        "[AUTO-CREATE-ENGINE] Pass 5: retry failed for channel %s: %s",
                        channel_id, retry_result.error or retry_result.description
                    )

                results["execution_log"].append({
                    "stream_id": stream_ctx.stream_id,
                    "stream_name": f"[Pass 5 Retry] {stream_ctx.stream_name}",
                    "m3u_account_id": stream_ctx.m3u_account_id,
                    "rules_evaluated": [],
                    "actions_executed": [{
                        "type": retry_result.action_type,
                        "description": retry_result.description,
                        "success": retry_result.success,
                        "entity_id": retry_result.entity_id,
                        "error": retry_result.error
                    }]
                })

        # Summary
        total = retry_success + retry_failed
        if dry_run:
            summary_desc = f"Would refresh dummy EPG and retry {total} deferred EPG assignments"
        else:
            summary_desc = (
                f"Refreshed dummy EPG and retried {total} deferred assignments "
                f"({retry_success} succeeded, {retry_failed} failed)"
            )

        results["execution_log"].append({
            "stream_id": None,
            "stream_name": "[Pass 5] Summary",
            "m3u_account_id": None,
            "rules_evaluated": [],
            "actions_executed": [{
                "type": "dummy_epg_refresh",
                "description": summary_desc,
                "success": retry_failed == 0,
                "entity_id": None,
                "error": None
            }]
        })

        if dry_run:
            results["dry_run_results"].append({
                "stream_id": None,
                "stream_name": "[Pass 5] Summary",
                "rule_id": None,
                "rule_name": None,
                "action": summary_desc,
                "would_create": False,
                "would_modify": False
            })

        logger.info(
            "[AUTO-CREATE-ENGINE] Pass 5 complete: %s succeeded, %s failed",
            retry_success, retry_failed
        )

    # =========================================================================
    # Pass 4: Reconciliation
    # =========================================================================

    async def _reconcile_orphans(
        self,
        rules: list[ChannelPipelineRule],
        rule_channel_order: dict,
        executor,
        execution: ChannelPipelineExecution,
        results: dict,
        dry_run: bool,
        settings=None
    ):
        """
        Reconcile orphaned channels after pipeline execution.

        For each rule that was executed, compare its previous managed_channel_ids
        with the current set of channel IDs. Orphans (previous - current) are
        cleaned up according to the rule's orphan_action setting.
        """
        session = get_session()
        try:
            for rule in rules:
                # HARD BYPASS for event_sync rules (bead ti939.1.3; epic
                # ti939 PO decision 6). Dispatcharr owns the master channels'
                # lifecycle (creates/updates/deletes them from the master
                # auto-sync group, preserving UUIDs); ECM never owns any
                # channel under an event_sync rule. managed_channel_ids must
                # therefore NEVER be populated for this rule kind — a
                # half-populated set against Dispatcharr-owned channels would
                # make this pass "reconcile" (delete/move) channels ECM does
                # not own. Note this is stricter than orphan_action="none",
                # which still records managed_channel_ids below; event_sync
                # skips even that, forcing orphan-action semantics to none.
                # run_pipeline already filters event_sync rules out of the
                # list this method receives — this guard is belt-and-braces
                # for any direct caller.
                event_sync_promote_group_id = None
                if rule.is_event_sync():
                    cfg = rule.get_event_sync_config() or {}
                    if not cfg.get("promote_unmatched"):
                        logger.debug(
                            "[AUTO-CREATE-ENGINE] Rule '%s': event_sync kind — "
                            "orphan reconciliation hard-bypassed "
                            "(Dispatcharr owns master-channel lifecycle)",
                            rule.name,
                        )
                        continue
                    # bead ti939.4.1: promotion-enabled event_sync rules DO
                    # reconcile — their managed set contains ONLY the
                    # ECM-promoted channels in promote_target_group_id (the
                    # attach phase registers nothing else; the register site
                    # re-asserts it). Lifecycle is reconciliation-driven by
                    # PO decision: a promoted channel is current iff its
                    # justifying unmatched stream was observed in the
                    # provider playlist THIS run — no wall-clock arithmetic,
                    # no timestamps, no run counters anywhere below. The
                    # target group id is kept as a LAST-RESORT ownership
                    # rail on the delete loop.
                    event_sync_promote_group_id = cfg.get(
                        "promote_target_group_id"
                    )
                    if event_sync_promote_group_id is None:
                        # Config drifted (promote on, target gone) — refuse
                        # to reconcile rather than guess ownership.
                        logger.warning(
                            "[AUTO-CREATE-ENGINE] Rule '%s': promote_unmatched "
                            "set but no promote_target_group_id — orphan "
                            "reconciliation skipped for safety", rule.name,
                        )
                        continue

                orphan_action = getattr(rule, 'orphan_action', 'delete') or 'delete'
                logger.debug(
                    "[AUTO-CREATE-ENGINE] Rule '%s': orphan_action=%s, "
                    "managed_channel_ids=%s",
                    rule.name, orphan_action, rule.managed_channel_ids is not None
                )

                # orphan_action "none" means skip reconciliation entirely for this rule
                if orphan_action == 'none':
                    current_ids = set(rule_channel_order.get(rule.id, []))
                    if current_ids and not dry_run:
                        rule.set_managed_channel_ids(list(current_ids))
                        session.merge(rule)
                    continue

                current_ids = set(rule_channel_order.get(rule.id, []))
                previous_ids = set(rule.get_managed_channel_ids())

                # First run after upgrade: managed_channel_ids is null
                # Just populate, don't delete anything
                if rule.managed_channel_ids is None:
                    if current_ids and not dry_run:
                        rule.set_managed_channel_ids(list(current_ids))
                        session.merge(rule)
                    logger.info(
                        "[AUTO-CREATE-ENGINE] Rule '%s': first run, populated "
                        "%s managed channel IDs",
                        rule.name, len(current_ids)
                    )
                    continue

                orphan_ids = previous_ids - current_ids

                # Filter out stale orphans: IDs that no longer exist in
                # Dispatcharr (already deleted externally or via re-import).
                # Only keep orphans that actually still exist as channels —
                # there's no point trying to delete something that's already gone,
                # and it prevents delete/re-import cycles from triggering
                # mass orphan cleanup and renumbering.
                if orphan_ids:
                    existing_channel_ids = set(executor._channel_by_id.keys())
                    stale_ids = orphan_ids - existing_channel_ids
                    if stale_ids:
                        logger.info(
                            "[AUTO-CREATE-ENGINE] Rule '%s': %s orphan ID(s) no "
                            "longer exist in Dispatcharr (already deleted/re-imported), skipping",
                            rule.name, len(stale_ids)
                        )
                        orphan_ids -= stale_ids

                # bead ti939.4.1 ownership rail (last resort, belt over the
                # register-time invariant): a promotion-enabled event_sync
                # rule may only ever delete/move channels INSIDE its
                # promotion target group. Any orphan candidate outside it
                # means the managed set was polluted — WARN and refuse that
                # id rather than touch a channel ECM does not own.
                if event_sync_promote_group_id is not None and orphan_ids:
                    foreign_ids = {
                        cid for cid in orphan_ids
                        if (executor._channel_by_id.get(cid) or {}).get(
                            "channel_group_id"
                        ) != event_sync_promote_group_id
                    }
                    if foreign_ids:
                        logger.warning(
                            "[AUTO-CREATE-ENGINE] Rule '%s': %s managed "
                            "channel id(s) %s are OUTSIDE the promotion "
                            "target group %s — refusing to reconcile them "
                            "(ownership invariant; managed set was "
                            "polluted)",
                            rule.name, len(foreign_ids),
                            sorted(foreign_ids)[:10],
                            event_sync_promote_group_id,
                        )
                        orphan_ids -= foreign_ids

                logger.debug(
                    "[AUTO-CREATE-ENGINE] Rule '%s': previous=%s "
                    "current=%s orphans=%s orphan_ids=%s",
                    rule.name, len(previous_ids),
                    len(current_ids), len(orphan_ids),
                    list(orphan_ids)[:20]
                )

                if not orphan_ids:
                    # No orphans — just update managed set
                    if not dry_run and current_ids != previous_ids:
                        rule.set_managed_channel_ids(list(current_ids))
                        session.merge(rule)
                    continue

                logger.info(
                    "[AUTO-CREATE-ENGINE] Rule '%s': %s orphaned channels "
                    "(previous=%s, current=%s)",
                    rule.name, len(orphan_ids),
                    len(previous_ids), len(current_ids)
                )

                # Track groups that may become empty (for delete_and_cleanup_groups)
                affected_group_ids = set()

                for channel_id in orphan_ids:
                    channel = executor._channel_by_id.get(channel_id, {})
                    channel_name = channel.get("name", f"ID:{channel_id}")

                    if dry_run:
                        action_desc = {
                            "delete": f"Would delete orphaned channel '{channel_name}'",
                            "move_uncategorized": f"Would move orphaned channel '{channel_name}' to Uncategorized",
                            "delete_and_cleanup_groups": f"Would delete orphaned channel '{channel_name}' + cleanup empty groups",
                        }.get(orphan_action, f"Would delete orphaned channel '{channel_name}'")

                        results["dry_run_results"].append({
                            "stream_id": None,
                            "stream_name": f"[Orphan] {channel_name}",
                            "rule_id": rule.id,
                            "rule_name": rule.name,
                            "action": action_desc,
                            "would_create": False,
                            "would_modify": orphan_action == "move_uncategorized"
                        })
                        results["channels_removed"] += 1
                        continue

                    # Execute cleanup based on setting
                    if orphan_action == "move_uncategorized":
                        action_result = await executor.move_channel_to_uncategorized(channel_id)
                        if action_result.success:
                            results["channels_moved"] += 1
                    else:
                        # "delete" or "delete_and_cleanup_groups"
                        if channel.get("channel_group"):
                            affected_group_ids.add(channel["channel_group"])
                        action_result = await executor.remove_channel(channel_id)
                        if action_result.success:
                            results["channels_removed"] += 1

                    # Log the cleanup action
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": f"[Orphan] {channel_name}",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": action_result.action_type,
                            "description": action_result.description,
                            "success": action_result.success,
                            "entity_id": channel_id,
                            "error": action_result.error
                        }]
                    })

                # For delete_and_cleanup_groups: check if any groups are now empty
                if not dry_run and orphan_action == "delete_and_cleanup_groups" and affected_group_ids:
                    for group_id in affected_group_ids:
                        group_result = await executor.delete_group_if_empty(group_id)
                        if group_result.success and not group_result.skipped:
                            results["execution_log"].append({
                                "stream_id": None,
                                "stream_name": f"[Cleanup] Empty group {group_result.entity_name}",
                                "m3u_account_id": None,
                                "rules_evaluated": [],
                                "actions_executed": [{
                                    "type": group_result.action_type,
                                    "description": group_result.description,
                                    "success": group_result.success,
                                    "entity_id": group_id,
                                    "error": group_result.error
                                }]
                            })

                # Renumber remaining channels to close gaps
                remaining_channel_ids = list(dict.fromkeys(rule_channel_order.get(rule.id, [])))
                # Filter out orphans to keep only current channels in their sorted order
                remaining_channel_ids = [cid for cid in remaining_channel_ids if cid not in orphan_ids]
                starting_number = _get_rule_starting_number(rule)

                if remaining_channel_ids and starting_number is not None:
                    if dry_run:
                        results["dry_run_results"].append({
                            "stream_id": None,
                            "stream_name": "[Renumber after cleanup]",
                            "rule_id": rule.id,
                            "rule_name": rule.name,
                            "action": f"Would renumber {len(remaining_channel_ids)} channels starting at #{starting_number}",
                            "would_create": False,
                            "would_modify": True
                        })
                    else:
                        try:
                            await self.client.assign_channel_numbers(remaining_channel_ids, starting_number)
                            # Auto-rename channel names after orphan renumber
                            rename_count = await _auto_rename_after_renumber(
                                self.client, remaining_channel_ids, starting_number, settings
                            )
                            rename_note = f", renamed {rename_count} channel names" if rename_count else ""
                            results["execution_log"].append({
                                "stream_id": None,
                                "stream_name": f"[Renumber] Rule '{rule.name}' after orphan cleanup",
                                "m3u_account_id": None,
                                "rules_evaluated": [],
                                "actions_executed": [{
                                    "type": "renumber_channels",
                                    "description": f"Renumbered {len(remaining_channel_ids)} channels starting at #{starting_number} after removing {len(orphan_ids)} orphans{rename_note}",
                                    "success": True,
                                    "entity_id": None,
                                    "error": None
                                }]
                            })
                            logger.info(
                                "[AUTO-CREATE-ENGINE] Rule '%s': renumbered %s channels "
                                "starting at #%s after orphan cleanup%s",
                                rule.name, len(remaining_channel_ids),
                                starting_number, rename_note
                            )
                        except Exception as e:
                            logger.error("[AUTO-CREATE-ENGINE] Rule '%s': failed to renumber after cleanup: %s", rule.name, e)

                # Update managed_channel_ids (not during dry run)
                if not dry_run:
                    rule.set_managed_channel_ids(list(current_ids))
                    session.merge(rule)

            session.commit()
        except Exception as e:
            session.rollback()
            logger.exception("[AUTO-CREATE-ENGINE] Failed to sync managed channel IDs: %s", e)
        finally:
            session.close()

    # =========================================================================
    # Execution Tracking
    # =========================================================================

    async def _create_execution(self, mode: str, triggered_by: str) -> ChannelPipelineExecution:
        """Create a new execution record."""
        session = get_session()
        try:
            execution = ChannelPipelineExecution(
                mode=mode,
                triggered_by=triggered_by,
                started_at=datetime.utcnow(),
                status="running"
            )
            session.add(execution)
            session.commit()
            session.refresh(execution)
            return execution
        finally:
            session.close()

    async def _load_execution(self, execution_id: int) -> ChannelPipelineExecution | None:
        """Load an existing execution record by id (bd-enfsy: reuses the row
        the router pre-created when enqueuing background work)."""
        session = get_session()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == execution_id
            ).first()
            if execution is not None:
                # Detach so it can be mutated outside this session and saved
                # via _save_execution(merge) like a freshly-created one.
                session.expunge(execution)
            return execution
        finally:
            session.close()

    async def _finalize_no_op_execution(self, execution_id: int) -> None:
        """Mark a pre-created execution as completed when there's no work to do
        (e.g. no enabled rules). Without this the row would remain in
        ``status="running"`` forever and the frontend poller would spin."""
        session = get_session()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == execution_id
            ).first()
            if execution is None:
                return
            now = datetime.utcnow()
            execution.completed_at = now
            execution.duration_seconds = (now - execution.started_at).total_seconds() if execution.started_at else 0.0
            execution.status = "completed"
            session.commit()
        finally:
            session.close()

    async def _save_execution(self, execution: ChannelPipelineExecution):
        """Save execution record."""
        session = get_session()
        try:
            session.merge(execution)
            session.commit()
        finally:
            session.close()

    async def _capture_snapshot(
        self,
        execution_id: int,
        include_auto_created_group_ids: set | None = None,
    ) -> None:
        """Persist a pre-run ChannelPipelineSnapshot for ``execution_id`` (ADR-010).

        Serializes the manual (non-Dispatcharr-auto-created) channel<->stream
        state from the already-loaded in-memory ``self._existing_channels`` —
        no per-channel API call, no N+1 (ADR-010 §D2). One row per execution,
        linked 1:1 via FK.

        Per-channel payload (STREAM IDS ONLY — never URLs, §D1):
        ``{id, name, channel_group_id, epg_data_id, tvg_id, stream_ids:[int]}``

        Channels where ``auto_created`` is truthy are EXCLUDED (§D3) — they are
        Dispatcharr-owned, regenerable state that Dispatcharr re-derives on
        every refresh; restoring them would fight Dispatcharr's own sync and
        bloat the snapshot. The filter mirrors ``channels.py:614``'s
        ``not ch.get("auto_created", False)``.

        ``include_auto_created_group_ids`` (ti939.2.1) is the narrow event_sync
        exception to §D3: auto-created channels in these groups — the event
        MASTER groups of the event_sync rules that will run — ARE captured,
        because the attach phase mutates their stream lists and a rollback /
        restore must be able to write back the pre-run stream-set. Dispatcharr
        preserves foreign streams across refreshes (verified in the ti939
        feasibility read), so restoring these stream-sets does not fight
        Dispatcharr's sync the way restoring its regenerable metadata would.

        Capture-failure policy (ADR-010 §D2, uc51o.2 v1 default):
        LOG-AND-PROCEED. A capture failure must NOT abort the mutating run —
        it logs a WARNING and the run continues with NO snapshot (the run is
        still revertible via the legacy entity-rollback). It does NOT raise.
        """
        include_group_ids = include_auto_created_group_ids or set()
        try:
            channels = []
            for ch in (self._existing_channels or []):
                # §D3: exclude Dispatcharr-auto-created-from-groups channels —
                # EXCEPT event_sync master-group channels (see docstring).
                if ch.get("auto_created", False) \
                        and ch.get("channel_group_id") not in include_group_ids:
                    continue
                # §D1: stream IDs only. Match the executor's own coercion
                # (executor.py:918) — Dispatcharr embeds streams as a list of
                # IDs (or, defensively, dicts carrying an "id").
                stream_ids = [
                    s["id"] if isinstance(s, dict) else s
                    for s in ch.get("streams", [])
                ]
                entry = {
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "channel_group_id": ch.get("channel_group_id"),
                    "epg_data_id": ch.get("epg_data_id"),
                    "tvg_id": ch.get("tvg_id"),
                    "stream_ids": stream_ids,
                }
                if ch.get("auto_created", False):
                    # Only reachable via the include set: an event_sync
                    # MASTER-group channel. Flag it so restore_snapshot
                    # writes back ONLY the stream list (PR #616 review /
                    # bead ti939.2.3): ECM's attach phase mutates nothing
                    # but streams on these Dispatcharr-owned channels, and
                    # restoring their captured name/group/epg/tvg would
                    # revert Dispatcharr's own in-place updates (e.g. a
                    # "TBD vs TBD" -> real-matchup slot rename) to stale
                    # pre-run values ECM never touched.
                    entry["event_sync_master"] = True
                channels.append(entry)

            session = get_session()
            try:
                snapshot = ChannelPipelineSnapshot(
                    execution_id=execution_id,
                    snapshot_time=datetime.utcnow(),
                    channel_count=len(channels),
                )
                snapshot.set_channels_data({"channels": channels})
                session.add(snapshot)
                session.commit()
                logger.info(
                    "[AUTO-CREATE-ENGINE] Captured pre-run snapshot for "
                    "execution_id=%s (%s manual channels)",
                    execution_id, len(channels),
                )
            finally:
                session.close()
        except Exception as e:
            # Log-and-proceed: never abort the mutating run on a capture
            # failure. The run remains revertible via the legacy
            # entity-rollback; the execution simply has no snapshot.
            logger.warning(
                "[AUTO-CREATE-ENGINE] Failed to capture pre-run snapshot for "
                "execution_id=%s; run proceeds WITHOUT a snapshot (legacy "
                "rollback still available): %s",
                execution_id, e,
            )

    async def _record_conflict(
        self,
        execution: ChannelPipelineExecution,
        stream: StreamContext,
        winning_rule: ChannelPipelineRule,
        losing_rules: list[ChannelPipelineRule],
        conflict_type: str
    ):
        """Record a conflict in the database."""
        session = get_session()
        try:
            conflict = ChannelPipelineConflict(
                execution_id=execution.id,
                stream_id=stream.stream_id,
                stream_name=stream.stream_name,
                winning_rule_id=winning_rule.id,
                conflict_type=conflict_type,
                resolution="first_rule_wins",
                description=f"Multiple rules matched stream '{stream.stream_name}': "
                           f"rule '{winning_rule.name}' (priority {winning_rule.priority}) won"
            )
            conflict.set_losing_rule_ids([r.id for r in losing_rules])
            session.add(conflict)
            session.commit()
        finally:
            session.close()

    async def _update_rule_stats(self, rules: list[ChannelPipelineRule], results: dict):
        """Update rule statistics after execution."""
        rule_match_counts = results.get("rule_match_counts", {})
        session = get_session()
        try:
            for rule in rules:
                rule.last_run_at = datetime.utcnow()
                matches = rule_match_counts.get(rule.id, 0)
                rule.match_count = matches
                session.merge(rule)
            session.commit()
        finally:
            session.close()

    # =========================================================================
    # Rollback
    # =========================================================================

    async def _rollback_created_entity(self, entity: dict):
        """Rollback a created entity by deleting it."""
        entity_type = entity.get("type")
        entity_id = entity.get("id")

        try:
            if entity_type == "channel":
                await self.client.delete_channel(entity_id)
                logger.info("[AUTO-CREATE-ENGINE] Deleted channel %s (%s)", entity_id, entity.get('name'))
            elif entity_type == "group":
                await self.client.delete_channel_group(entity_id)
                logger.info("[AUTO-CREATE-ENGINE] Deleted group %s (%s)", entity_id, entity.get('name'))
        except Exception as e:
            logger.error("[AUTO-CREATE-ENGINE] Failed to rollback %s %s: %s", entity_type, entity_id, e)

    async def _rollback_modified_entity(self, entity: dict):
        """Rollback a modified entity by restoring its previous state.

        WARNING: this is the SNAPSHOT-restore path — it overwrites the channel's
        FULL stream list from the pre-change snapshot, clobbering any streams a
        concurrent edit added after the merge. ``_journal_driven_unmerge``
        (jnzst Q4) is the surgical alternative and is preferred when journal
        provenance is available; this remains the fallback when it is not.
        """
        entity_type = entity.get("type")
        entity_id = entity.get("id")
        previous = entity.get("previous", {})

        try:
            if entity_type == "channel" and previous:
                await self.client.update_channel(entity_id, previous)
                logger.info("[AUTO-CREATE-ENGINE] Restored channel %s to previous state", entity_id)
        except Exception as e:
            logger.error("[AUTO-CREATE-ENGINE] Failed to restore %s %s: %s", entity_type, entity_id, e)

    @staticmethod
    def _added_stream_ids(before_value: dict | None, after_value: dict | None) -> list[int]:
        """The stream IDs a single journaled merge ADDED (after - before).

        Reads the ``{"stream_ids": [...]}`` lists ``_journal_merge`` wrote.
        Order-preserving set difference so a re-add of an already-present id is
        not double-counted. Returns [] when either side is missing/malformed.
        """
        try:
            before = set(before_value.get("stream_ids", []) if before_value else [])
            after = list(after_value.get("stream_ids", []) if after_value else [])
        except (AttributeError, TypeError):
            return []
        return [sid for sid in after if sid not in before]

    def _event_sync_journal_covers_run(self, execution) -> bool:
        """True when the journal PROVES the run's only mutations were
        event_sync stream attaches (bead sfysz).

        The surgical :meth:`_journal_driven_unmerge` is a SAFE full revert
        only when nothing the run did falls outside what the journal's
        merge entries describe. Coverage therefore requires ALL of:

        * no created entities — event_sync never creates channels/groups;
        * every modified entity is a channel whose recorded ``previous``
          state is EXACTLY a stream list (the only mutation the attach path
          performs — a metadata change would need the snapshot restore);
        * the batch's ``merge_stream`` journal entries (batch_id =
          execution id) ALL carry the ``event_sync`` category — a mixed run
          with standard-rule merges (category ``auto_creation``) is not
          covered, keeping the standard-rule rollback byte-identical;
        * entry count and per-channel multiset match the modified entities
          1:1 — a journal write that failed to flush leaves the run only
          partially described, so the full restore must run instead.

        Any journal read failure (or an over-page-size batch that cannot be
        verified in one read) counts as NOT covered: the fallback is the
        snapshot restore, never a partial surgical revert.
        """
        if execution.get_created_entities():
            return False
        modified = execution.get_modified_entities()
        if not modified:
            return False
        expected_channel_ids: list = []
        for entity in modified:
            if entity.get("type") != "channel":
                return False
            previous = entity.get("previous") or {}
            if set(previous.keys()) != {"streams"}:
                return False
            expected_channel_ids.append(entity.get("id"))

        try:
            page = journal.get_entries(
                page=1, page_size=1000,
                action_type="merge_stream",
                batch_id=str(execution.id),
            )
        except Exception as e:
            logger.warning(
                "[AUTO-CREATE-ENGINE] Journal read failed for surgical-"
                "coverage check of execution %s: %s", execution.id, e,
            )
            return False

        if not isinstance(page, dict):
            return False
        entries = page.get("results", [])
        if page.get("count", len(entries)) != len(entries):
            # More entries than one page — cannot verify coverage in full.
            return False
        if len(entries) != len(expected_channel_ids):
            return False
        if any(e.get("category") != "event_sync" for e in entries):
            return False
        return (Counter(e.get("entity_id") for e in entries)
                == Counter(expected_channel_ids))

    async def _journal_driven_unmerge(self, execution_id: int) -> tuple[bool, int]:
        """Surgically un-merge a run's fuzzy stream merges (jnzst Q4).

        Reads every ``merge_stream`` journal entry tagged
        ``batch_id=str(execution_id)``, computes the stream IDs each merge
        ADDED (after - before), and removes ONLY those from each channel's
        CURRENT live stream list — preserving streams a concurrent edit added
        after the merge. This is the un-clobbering alternative to the snapshot
        restore in ``_rollback_modified_entity``.

        Returns ``(handled, channels_touched)``. ``handled`` is False when no
        merge journal entries exist for the batch (caller falls back to the
        snapshot restore); the per-channel before/after lists are then missing,
        so snapshot restore is the only option.
        """
        try:
            page = journal.get_entries(
                page=1, page_size=1000,
                action_type="merge_stream",
                batch_id=str(execution_id),
            )
        except Exception as e:
            logger.warning(
                "[AUTO-CREATE-ENGINE] Journal read failed for surgical unmerge "
                "of execution %s: %s", execution_id, e,
            )
            return False, 0

        entries = page.get("results", []) if isinstance(page, dict) else []
        if not entries:
            return False, 0

        # Aggregate the stream IDs added per channel across all merges in the run.
        added_by_channel: dict[int, set[int]] = defaultdict(set)
        for entry in entries:
            cid = entry.get("entity_id")
            if cid is None:
                continue
            before = entry.get("before_value")
            after = entry.get("after_value")
            # journal.get_entries returns to_dict() form where before/after are
            # already-parsed dicts; tolerate raw JSON strings defensively too.
            if isinstance(before, str):
                before = json.loads(before) if before else None
            if isinstance(after, str):
                after = json.loads(after) if after else None
            for sid in self._added_stream_ids(before, after):
                added_by_channel[cid].add(sid)

        channels_touched = 0
        for channel_id, added in added_by_channel.items():
            if not added:
                continue
            try:
                # Fetch the CURRENT live stream list (not the snapshot) so a
                # concurrently-added stream survives.
                channel = await self.client.get_channel(channel_id)
                current = [
                    s["id"] if isinstance(s, dict) else s
                    for s in (channel.get("streams") or [])
                ]
                remaining = [sid for sid in current if sid not in added]
                if remaining != current:
                    await self.client.update_channel(channel_id, {"streams": remaining})
                    channels_touched += 1
                    logger.info(
                        "[AUTO-CREATE-ENGINE] Surgical unmerge: removed %d stream(s) "
                        "from channel %s, kept %d (concurrent edits preserved)",
                        len(current) - len(remaining), channel_id, len(remaining),
                    )
            except Exception as e:
                logger.error(
                    "[AUTO-CREATE-ENGINE] Surgical unmerge failed for channel %s: %s",
                    channel_id, e,
                )

        return True, channels_touched


# =============================================================================
# Sort Helpers
# =============================================================================

def _smart_sort_streams(
    stream_ids: list[int],
    stats_cache: dict,
    stream_m3u_map: dict,
    channel_name: str = "unknown",
    settings=None,
    custom_stream_ids: set[int] | None = None,
    catchup_stream_ids: set[int] | None = None,
) -> list[int]:
    """
    Sort stream IDs using smart sort logic (mirrors stream_prober._smart_sort_streams).

    Uses configurable sort priority and enabled criteria from settings.
    Falls back to resolution-only if settings are unavailable.

    Args:
        stream_ids: Stream IDs to sort
        stats_cache: stream_id -> stats dict (from StreamStats.to_dict())
        stream_m3u_map: stream_id -> m3u_account_id
        channel_name: For logging
        settings: DispatcharrSettings instance
        custom_stream_ids: Set of operator-added custom stream IDs (Dispatcharr
            is_custom). Drives the ``custom_streams`` criterion. When None/omitted
            the criterion is inert (scores 0 everywhere) so callers degrade gracefully.
    """
    if custom_stream_ids is None:
        custom_stream_ids = set()
    if catchup_stream_ids is None:
        catchup_stream_ids = set()
    if settings is None:
        # Fallback: resolution-only sort (descending)
        def fallback_key(sid):
            stats = stats_cache.get(sid)
            if stats and stats.get("resolution"):
                try:
                    parts = stats["resolution"].split("x")
                    if len(parts) == 2:
                        return -int(parts[1])
                except (ValueError, IndexError):
                    logger.debug("[AUTO-CREATE] Non-numeric resolution %r, using default 0", stats.get("resolution"))
            return 0
        return sorted(stream_ids, key=fallback_key)

    # Get active sort criteria (enabled and in priority order)
    sort_priority = getattr(settings, 'stream_sort_priority',
                            ["resolution", "bitrate", "framerate", "video_codec", "m3u_priority", "audio_channels"])
    sort_enabled = getattr(settings, 'stream_sort_enabled',
                           {"resolution": True, "bitrate": True, "framerate": True})
    deprioritize_failed = getattr(settings, 'deprioritize_failed_streams', True)
    m3u_priorities = getattr(settings, 'm3u_account_priorities', {})
    fail_order = getattr(settings, 'failed_stream_sort_order', ["failed", "black_screen", "low_fps"])
    failed_rank = {cat: idx for idx, cat in enumerate(fail_order)}

    active_criteria = [c for c in sort_priority if sort_enabled.get(c, False)]

    logger.info(
        "[AUTO-CREATE-ENGINE] Channel '%s': smart sort with "
        "active_criteria=%s, deprioritize_failed=%s, failed_order=%s",
        channel_name, active_criteria, deprioritize_failed, fail_order
    )

    def compute_criteria_values(stats: dict | None, sid: int) -> list:
        """Compute sort-key values for active_criteria in priority order.

        Used for both successful streams (primary ordering) and deprioritized
        streams (within-bucket tiebreaker — bd-bqpq0, mirrors bd-sw883 in
        stream_prober.py). For a deprioritized stream where ``stats`` is None
        (no probe row at all), only m3u_priority can be computed; all other
        criteria are 0.
        """
        values = []
        for criterion in active_criteria:
            if criterion == "resolution":
                resolution_value = 0
                if stats and stats.get("resolution"):
                    try:
                        parts = stats["resolution"].split("x")
                        if len(parts) == 2:
                            resolution_value = int(parts[1])
                    except (ValueError, IndexError) as e:
                        logger.debug("[AUTO-CREATE-ENGINE] Suppressed resolution parse error: %s", e)
                values.append(-resolution_value)

            elif criterion == "bitrate":
                bitrate_value = 0
                if stats:
                    bitrate_value = stats.get("video_bitrate") or stats.get("bitrate") or 0
                values.append(-bitrate_value)

            elif criterion == "framerate":
                framerate_value = 0
                fps = stats.get("fps") if stats else None
                if fps:
                    try:
                        framerate_value = float(fps)
                    except (ValueError, TypeError) as e:
                        logger.debug("[AUTO-CREATE-ENGINE] Suppressed fps parse error: %s", e)
                values.append(-framerate_value)

            elif criterion == "m3u_priority":
                # m3u_priority does NOT require a successful probe — it comes
                # from the m3u account map, so it's always meaningful.
                # Streams with no M3U account (m3u_account_id is None) use the
                # "custom" key in m3u_priorities as a defensive fallback. Operator-added
                # custom streams carry the real "custom" M3U account id and are ranked
                # by the dedicated "custom_streams" criterion instead (bead ap1ud / GH #244).
                m3u_priority_value = 0
                m3u_account_id = stream_m3u_map.get(sid)
                if m3u_account_id is not None:
                    m3u_priority_value = m3u_priorities.get(str(m3u_account_id), 0)
                else:
                    # Account-less stream — defensive "custom" fallback.
                    m3u_priority_value = m3u_priorities.get("custom", 0)
                values.append(-m3u_priority_value)

            elif criterion == "audio_channels":
                audio_ch = (stats.get("audio_channels") if stats else 0) or 0
                values.append(-audio_ch)

            elif criterion == "video_codec":
                from stream_prober import get_codec_rank
                codec_value = get_codec_rank(stats.get("video_codec")) if stats else 0
                values.append(-codec_value)

            elif criterion == "custom_streams":
                # Binary criterion: 1 if the stream is an operator-added custom
                # stream (Dispatcharr is_custom), else 0. Negate so custom streams
                # sort first when ranked highest. Inert if custom_stream_ids not supplied.
                custom_value = 1 if sid in custom_stream_ids else 0
                values.append(-custom_value)

            elif criterion == "catchup":
                values.append(-(1 if sid in catchup_stream_ids else 0))

        return values

    def get_sort_value(sid: int) -> tuple:
        stats = stats_cache.get(sid)

        # Deprioritize failed/missing streams
        if deprioritize_failed:
            if not stats or stats.get("probe_status") in ("failed", "timeout", "pending"):
                rank = failed_rank.get('failed', 0)
                # bd-bqpq0: apply primary criteria within the failed bucket too.
                return (1, rank) + tuple(compute_criteria_values(stats, sid))

        # Deprioritize black screen streams (probe succeeded but content is black)
        if deprioritize_failed and stats and stats.get("is_black_screen"):
            rank = failed_rank.get('black_screen', 1)
            # bd-bqpq0: apply primary criteria within the black_screen bucket too.
            return (1, rank) + tuple(compute_criteria_values(stats, sid))

        # Deprioritize low FPS streams (probe succeeded but FPS below threshold)
        if deprioritize_failed and stats and stats.get("is_low_fps"):
            rank = failed_rank.get('low_fps', 2)
            # bd-bqpq0: apply primary criteria within the low_fps bucket too.
            return (1, rank) + tuple(compute_criteria_values(stats, sid))

        if not stats or stats.get("probe_status") != "success":
            # custom_streams is a binary criterion that does not require a probe,
            # so compute it even for unprobed streams (mirrors the prober's
            # unprobed-stream path). m3u_priority behaviour here is intentionally
            # left as-is (zeroed when unprobed and deprioritize_failed is off).
            unprobed_values = [
                -(1 if sid in custom_stream_ids else 0) if criterion == "custom_streams"
                else -(1 if sid in catchup_stream_ids else 0) if criterion == "catchup"
                else 0
                for criterion in active_criteria
            ]
            return (0, 0) + tuple(unprobed_values)

        sort_values = [0, 0]  # 0 = successful stream, 0 = sub-rank (unused)
        sort_values.extend(compute_criteria_values(stats, sid))
        return tuple(sort_values)

    # Log each stream's sort values
    for sid in stream_ids:
        stats = stats_cache.get(sid)
        sname = stats.get("stream_name", f"Stream {sid}") if stats else f"Stream {sid}"
        sv = get_sort_value(sid)
        logger.debug("[AUTO-CREATE-ENGINE]   %s (id=%s): sort_tuple=%s", sname, sid, sv)

    sorted_ids = sorted(stream_ids, key=get_sort_value)

    logger.info("[AUTO-CREATE-ENGINE] Channel '%s' sorted order:", channel_name)
    for idx, sid in enumerate(sorted_ids):
        stats = stats_cache.get(sid)
        sname = stats.get("stream_name", f"Stream {sid}") if stats else f"Stream {sid}"
        res = stats.get("resolution", "?") if stats else "?"
        logger.info("[AUTO-CREATE-ENGINE]   #%s: %s (id=%s, res=%s)", idx+1, sname, sid, res)

    return sorted_ids


def _stream_sort_rule_label(stream_sort_field: str | None) -> str:
    """Human-readable label for execution logs."""
    f = (stream_sort_field or "").strip()
    return {
        "smart_sort": "smart sort (from Settings)",
        "provider_order": "provider order (M3U account priority)",
        "quality": "quality (resolution)",
        "stream_name": "stream name",
        "stream_name_natural": "stream name (natural)",
    }.get(f, f"stream sort ({f})" if f else "stream sort")


def _m3u_account_priority_value(
    sid: int,
    stream_m3u_map: dict | None,
    settings,
) -> int:
    """Numeric ECM M3U priority for *sid* (0 when unknown).

    Streams with no M3U account (m3u_account_id is None) fall back to the
    ``m3u_account_priorities["custom"]`` key — a vestigial defensive fallback
    for account-less streams. Operator-added custom streams belong to the real
    Dispatcharr "custom" M3U account and are ranked by the dedicated
    "custom_streams" Smart Sort criterion (bead ap1ud / GH #244), not by this
    helper. The same key is consumed by Smart Sort's ``compute_criteria_values``
    and the provider-order / quality-tie-break paths for consistency.
    """
    pri_map = getattr(settings, "m3u_account_priorities", None) or {} if settings is not None else {}
    aid = (stream_m3u_map or {}).get(sid)
    if aid is None:
        return pri_map.get("custom", 0)
    return pri_map.get(str(aid), 0)


def _sort_streams_by_m3u_account_priority(
    stream_ids: list[int],
    stream_m3u_map: dict,
    settings,
    order: str,
    channel_name: str,
) -> list[int]:
    """Order streams by ECM Settings → M3U account priority values (higher = preferred).

    Does not require probe stats. *order*: "desc" = highest priority first (recommended),
    "asc" = lowest priority first.
    """
    def sort_key(sid: int):
        pri = _m3u_account_priority_value(sid, stream_m3u_map, settings)
        if order == "desc":
            return (-pri, sid)
        return (pri, sid)

    sorted_ids = sorted(stream_ids, key=sort_key)
    logger.info(
        "[AUTO-CREATE-ENGINE] Channel '%s': provider order (%s) by M3U priority -> %s",
        channel_name, order, sorted_ids,
    )
    return sorted_ids


def _resolution_height_from_stats(stats: dict | None) -> int:
    if not stats or not stats.get("resolution"):
        return 0
    try:
        parts = stats["resolution"].split("x")
        if len(parts) == 2:
            return int(parts[1])
    except (ValueError, IndexError) as e:
        logger.debug("[AUTO-CREATE-ENGINE] Suppressed resolution parse error: %s", e)
    return 0


def _sort_streams_by_resolution_height(
    stream_ids: list[int],
    stats_cache: dict,
    settings,
    order: str,
    channel_name: str,
    stream_m3u_map: dict | None = None,
    quality_tie_break_order: str = "desc",
    quality_m3u_tie_break_enabled: bool = True,
) -> list[int]:
    """Sort by probed resolution height; missing stats count as 0.

    When Settings enable deprioritization, push failed/black-screen/low-FPS
    streams to the bottom (same categories as smart sort).

    When *quality_m3u_tie_break_enabled* is True, equal resolutions are ordered by ECM M3U
    account priority (*quality_tie_break_order*: same semantics as Provider Order —
    ``desc`` = higher priority value first). When False, ties use stream id only (stable).
    """

    tb = (quality_tie_break_order or "desc").lower()
    if tb not in ("asc", "desc"):
        tb = "desc"

    deprioritize_failed = getattr(settings, "deprioritize_failed_streams", True) if settings is not None else True
    fail_order = getattr(settings, "failed_stream_sort_order", None) if settings is not None else None
    if not fail_order:
        fail_order = ["failed", "black_screen", "low_fps"]
    failed_rank = {name: idx for idx, name in enumerate(fail_order)}

    def sort_key(sid: int):
        stats = stats_cache.get(sid)
        h = _resolution_height_from_stats(stats)

        # rank: 0 = good stream, 1 = deprioritized bucket (ordered by fail_order)
        if deprioritize_failed:
            status = (stats or {}).get("probe_status") if isinstance(stats, dict) else None
            if status in ("failed", "timeout"):
                bucket = "failed"
                rank = failed_rank.get(bucket, len(failed_rank))
                hk = -h if order == "desc" else h
                if quality_m3u_tie_break_enabled:
                    pri = _m3u_account_priority_value(sid, stream_m3u_map, settings)
                    tb_key = -pri if tb == "desc" else pri
                    return (1, rank, hk, tb_key, sid)
                return (1, rank, hk, sid)
            if isinstance(stats, dict) and stats.get("is_black_screen"):
                bucket = "black_screen"
                rank = failed_rank.get(bucket, len(failed_rank))
                hk = -h if order == "desc" else h
                if quality_m3u_tie_break_enabled:
                    pri = _m3u_account_priority_value(sid, stream_m3u_map, settings)
                    tb_key = -pri if tb == "desc" else pri
                    return (1, rank, hk, tb_key, sid)
                return (1, rank, hk, sid)
            if isinstance(stats, dict) and stats.get("is_low_fps"):
                bucket = "low_fps"
                rank = failed_rank.get(bucket, len(failed_rank))
                hk = -h if order == "desc" else h
                if quality_m3u_tie_break_enabled:
                    pri = _m3u_account_priority_value(sid, stream_m3u_map, settings)
                    tb_key = -pri if tb == "desc" else pri
                    return (1, rank, hk, tb_key, sid)
                return (1, rank, hk, sid)

        hk = -h if order == "desc" else h
        if quality_m3u_tie_break_enabled:
            pri = _m3u_account_priority_value(sid, stream_m3u_map, settings)
            tb_key = -pri if tb == "desc" else pri
            return (0, 0, hk, tb_key, sid)
        return (0, 0, hk, sid)

    sorted_ids = sorted(stream_ids, key=sort_key)
    if quality_m3u_tie_break_enabled:
        logger.info(
            "[AUTO-CREATE-ENGINE] Channel '%s': quality sort (%s), equal-quality M3U tie-break (%s) -> %s",
            channel_name, order, tb, sorted_ids,
        )
    else:
        logger.info(
            "[AUTO-CREATE-ENGINE] Channel '%s': quality sort (%s), M3U tie-break off -> %s",
            channel_name, order, sorted_ids,
        )
    return sorted_ids


def _stream_name_for_sort(sid: int, stats_cache: dict) -> str:
    st = stats_cache.get(sid)
    if st and st.get("stream_name"):
        return st["stream_name"]
    return f"Stream {sid}"


def _sort_streams_by_stream_name(
    stream_ids: list[int],
    stats_cache: dict,
    order: str,
    channel_name: str,
    natural: bool,
) -> list[int]:
    if natural:
        temp = sorted(
            stream_ids,
            key=lambda sid: (_natural_sort_key(_stream_name_for_sort(sid, stats_cache)), sid),
        )
    else:
        temp = sorted(
            stream_ids,
            key=lambda sid: (_stream_name_for_sort(sid, stats_cache).lower(), sid),
        )
    if order == "desc":
        temp = list(reversed(temp))
    logger.info(
        "[AUTO-CREATE-ENGINE] Channel '%s': stream name sort (%s, natural=%s) -> %s",
        channel_name, order, natural, temp,
    )
    return temp


def _reorder_streams_for_rule(
    stream_ids: list[int],
    rule,
    stats_cache: dict,
    stream_m3u_map: dict,
    channel_name: str,
    settings,
    custom_stream_ids: set[int] | None = None,
    catchup_stream_ids: set[int] | None = None,
) -> list[int]:
    """Dispatch stream reordering based on rule.stream_sort_field."""
    field = (getattr(rule, "stream_sort_field", None) or "").strip()
    order = (getattr(rule, "stream_sort_order", None) or "asc").lower()
    if order not in ("asc", "desc"):
        order = "asc"

    _tb_raw = getattr(rule, "quality_tie_break_order", None)
    if isinstance(_tb_raw, str):
        quality_tie_break_order = _tb_raw.lower().strip()
    else:
        quality_tie_break_order = "desc"
    if quality_tie_break_order not in ("asc", "desc"):
        quality_tie_break_order = "desc"

    _tie_en_raw = getattr(rule, "quality_m3u_tie_break_enabled", None)
    if isinstance(_tie_en_raw, bool):
        quality_m3u_tie_break_enabled = _tie_en_raw
    else:
        quality_m3u_tie_break_enabled = True

    if not field or field == "smart_sort":
        return _smart_sort_streams(
            stream_ids, stats_cache, stream_m3u_map, channel_name, settings,
            custom_stream_ids=custom_stream_ids,
            catchup_stream_ids=catchup_stream_ids,
        )

    if field == "provider_order":
        return _sort_streams_by_m3u_account_priority(
            stream_ids, stream_m3u_map, settings, order, channel_name
        )

    if field == "quality":
        return _sort_streams_by_resolution_height(
            stream_ids,
            stats_cache,
            settings,
            order,
            channel_name,
            stream_m3u_map=stream_m3u_map,
            quality_tie_break_order=quality_tie_break_order,
            quality_m3u_tie_break_enabled=quality_m3u_tie_break_enabled,
        )

    if field == "stream_name":
        return _sort_streams_by_stream_name(
            stream_ids, stats_cache, order, channel_name, natural=False
        )

    if field == "stream_name_natural":
        return _sort_streams_by_stream_name(
            stream_ids, stats_cache, order, channel_name, natural=True
        )

    logger.warning(
        "[AUTO-CREATE-ENGINE] Channel '%s': unknown stream_sort_field=%r, "
        "falling back to smart sort",
        channel_name, field,
    )
    return _smart_sort_streams(
        stream_ids, stats_cache, stream_m3u_map, channel_name, settings,
        custom_stream_ids=custom_stream_ids,
        catchup_stream_ids=catchup_stream_ids,
    )


def _natural_sort_key(s: str) -> list:
    """Split string into text/number parts for natural sorting.

    "Olympics 2" < "Olympics 10" (unlike pure alphabetical).
    """
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def _sort_key(stream: StreamContext, sort_field: str, sort_regex=None):
    """Get sort key for a stream based on the sort field.

    ``sort_regex`` may be a raw string (legacy path — compiled on every
    call, do not use in hot loops) or a pre-compiled pattern object from
    :func:`safe_regex.compile`. Callers that sort N streams should
    precompile once and pass the compiled object to amortize compilation
    cost across the N log N comparisons (see bd-eio04.15 — hot-path
    mitigation in the sort closure at ``_run_rules``).
    """
    if sort_field == "stream_name":
        return stream.stream_name.lower()
    elif sort_field == "stream_name_natural":
        return _natural_sort_key(stream.stream_name)
    elif sort_field == "group_name":
        return (stream.group_name or "").lower()
    elif sort_field == "quality":
        return stream.resolution_height or 0
    elif sort_field == "provider_order":
        return stream.m3u_position
    elif sort_field == "channel_number":
        return stream.stream_chno if stream.stream_chno is not None else float('inf')
    elif sort_field == "stream_name_regex":
        if sort_regex:
            # bd-eio04.15: route through safe_regex. A pre-compiled pattern
            # (preferred) bypasses repeated compilation; a raw string falls
            # back to safe_regex.search's internal one-shot compile. On
            # timeout or no-match the result is None, which collapses to
            # the (-1, 0, "") sentinel — unmatched streams sort to the
            # front in ascending order, which matches the pre-migration
            # behavior when the stdlib re.search returned None.
            m = safe_regex.search(sort_regex, stream.stream_name)
            if m is not None and m.groups():
                captured = m.group(1)
                try:
                    return (0, float(captured), captured)
                except (ValueError, TypeError):
                    return (0, 0, captured)
        return (-1, 0, "")
    elif sort_field == "smart_sort":
        # Sort by resolution (descending), then bitrate, then audio tracks
        return (-(stream.resolution_height or 0), -(stream.bitrate or 0), -(stream.audio_tracks or 0))
    return stream.stream_name.lower()


def _get_rule_starting_number(rule) -> Optional[int]:
    """Extract the starting channel number from a rule's create_channel action.

    Returns the integer starting number, or None if the rule uses "auto" numbering
    or has no create_channel action.
    """
    for action_data in rule.get_actions():
        if action_data.get("type") != "create_channel":
            continue
        spec = action_data.get("channel_number", "auto")
        if isinstance(spec, int):
            return spec
        if isinstance(spec, str):
            if spec == "auto":
                return None
            # Handle range strings like "500-999" — use the start
            if "-" in spec:
                try:
                    return int(spec.split("-")[0])
                except ValueError:
                    return None
            try:
                return int(spec)
            except ValueError:
                return None
    return None


# =============================================================================
# Timezone Filter
# =============================================================================

# Pattern: stream name contains EAST or WEST near the end, possibly followed by
# quality indicators (HD, FHD, UHD, SD, 4K, HEVC, H.264/5) or parenthesized/bracketed
# tags like (CX), [HD], etc.
# Module-level constant assembled from raw-string literal fragments (multi-line
# string concatenation); no runtime interpolation — safe by construction.
_TZ_SUFFIX_RE = re.compile(  # nosemgrep: no-bare-re-on-dynamic-pattern
    r'[\s\-_.\(|\[](EAST|WEST)[\s\)\]]*'
    r'(?:\s*(?:F?HD|UHD|SD|4K|HEVC|H\.?26[45]|\([^)]*\)|\[[^\]]*\]))*'
    r'\s*$',
    re.IGNORECASE
)


def _filter_by_timezone(stream_name: str, preference: str) -> bool:
    """Check whether a stream should be kept based on timezone preference.

    Returns True if the stream should be KEPT, False if it should be filtered out.

    Behaviour:
      - "both"  -> keep everything
      - "east"  -> keep east-suffixed + base (no suffix), filter out WEST
      - "west"  -> keep west-suffixed + base (no suffix), filter out EAST
    """
    if preference == "both":
        return True

    m = _TZ_SUFFIX_RE.search(stream_name)
    if not m:
        # No timezone suffix -> base stream, always keep
        return True

    suffix = m.group(1).upper()
    if preference == "east":
        keep = suffix != "WEST"
        if not keep:
            logger.debug("[AUTO-CREATE-ENGINE] Filtering out WEST stream: %r", stream_name)
        return keep
    if preference == "west":
        keep = suffix != "EAST"
        if not keep:
            logger.debug("[AUTO-CREATE-ENGINE] Filtering out EAST stream: %r", stream_name)
        return keep

    return True


# =============================================================================
# Auto-Rename After Renumber
# =============================================================================

async def _auto_rename_after_renumber(
    client,
    channel_ids: list[int],
    starting_number: int,
    settings
) -> int:
    """
    After renumbering channels, update channel names to reflect new numbers.

    Mirrors the logic in main.py:2147-2174 for the manual renumber endpoint.
    Returns the number of channels renamed.
    """
    if not settings or not getattr(settings, 'auto_rename_channel_number', False):
        logger.debug("[AUTO-CREATE-ENGINE] Skipped: auto_rename_channel_number is disabled")
        return 0
    if starting_number is None:
        logger.debug("[AUTO-CREATE-ENGINE] Skipped: starting_number is None")
        return 0

    logger.debug("[AUTO-CREATE-ENGINE] Processing %s channels starting at #%s", len(channel_ids), starting_number)
    renamed = 0
    for idx, channel_id in enumerate(channel_ids):
        try:
            channel = await client.get_channel(channel_id)
        except Exception as e:
            logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch channel %s for renumbering: %s", channel_id, e)
            continue

        old_number = channel.get("channel_number")
        new_number = starting_number + idx
        channel_name = channel.get("name", "")

        if old_number is None or old_number == new_number or not channel_name:
            continue

        old_number_str = str(int(old_number) if old_number == int(old_number) else old_number)
        new_number_str = str(int(new_number) if new_number == int(new_number) else new_number)

        # Match the number as a standalone value (not part of a larger number)
        pattern = re.compile(r'(^|[^0-9])' + re.escape(old_number_str) + r'([^0-9]|$)')
        if pattern.search(channel_name):
            new_name = pattern.sub(r'\g<1>' + new_number_str + r'\g<2>', channel_name)
            if new_name != channel_name:
                try:
                    await client.update_channel(channel_id, {"name": new_name})
                    logger.info(
                        "[AUTO-CREATE-ENGINE] Channel %s: '%s' -> '%s'",
                        channel_id, channel_name, new_name
                    )
                    renamed += 1
                except Exception as e:
                    logger.warning("[AUTO-CREATE-ENGINE] Failed to rename channel %s: %s", channel_id, e)

    return renamed


# =============================================================================
# Singleton Instance
# =============================================================================

_engine_instance: Optional[ChannelPipelineEngine] = None


def get_channel_pipeline_engine() -> Optional[ChannelPipelineEngine]:
    """Get the auto-creation engine instance."""
    return _engine_instance


def set_channel_pipeline_engine(engine: ChannelPipelineEngine):
    """Set the auto-creation engine instance."""
    global _engine_instance
    _engine_instance = engine


def reset_channel_pipeline_engine() -> None:
    """Clear the auto-creation engine singleton (bd-snryv).

    ``ChannelPipelineEngine.__init__`` captures ``self.client`` once and uses it
    for every live Dispatcharr call the pipeline makes thereafter (get_channels,
    update_channel, delete_channel, assign_channel_numbers, etc.) — it never
    re-fetches ``get_client()``. ``routers/channel_pipeline.py``'s
    ``_ensure_engine()`` helper only builds a fresh engine via
    ``init_channel_pipeline_engine(get_client())`` when ``get_channel_pipeline_engine()``
    returns ``None``; it never rebuilds an existing engine. Without this,
    a Dispatcharr credential rotation leaves auto-creation rule runs hitting
    Dispatcharr with stale credentials indefinitely — same bug class as the
    BandwidthTracker/StreamProber singletons (see
    ``routers.settings._restart_background_services``), just a different
    singleton. Mirrors ``dispatcharr_client.reset_client()``: clearing the
    singleton here is sufficient because ``_ensure_engine()`` already does
    "build if None" — the next call naturally rebuilds with the fresh client.
    """
    global _engine_instance
    _engine_instance = None


async def init_channel_pipeline_engine(client) -> ChannelPipelineEngine:
    """Initialize the auto-creation engine with a Dispatcharr client."""
    engine = ChannelPipelineEngine(client)
    set_channel_pipeline_engine(engine)
    return engine
