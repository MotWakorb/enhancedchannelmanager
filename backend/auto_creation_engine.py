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
from collections import defaultdict
from datetime import datetime
from typing import Optional

import safe_regex
import journal
from config import (
    DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN,
    DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES,
    get_settings,
)
from database import get_session
from models import (
    AutoCreationRule,
    AutoCreationExecution,
    AutoCreationConflict,
    AutoCreationSnapshot,
    StreamStats
)
from auto_creation_schema import (
    Action,
    ActionType,
)
from auto_creation_evaluator import (
    ConditionEvaluator,
    StreamContext,
)
from auto_creation_executor import (
    ActionExecutor,
    ExecutionContext,
)


logger = logging.getLogger(__name__)


# Keys carrying the verbose per-stream evaluation trace. Dropped from each
# retained entry on non-dry-run so peak RSS does not scale with
# streams×rules×conditions (bd-sjdsq). The Rule Analyzer re-evaluates rules
# itself and does NOT read the persisted execution_log; rollback uses
# AutoCreationSnapshot/created_entities, not the log — so dropping these is
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


class AutoCreationEngine:
    """
    Main orchestrator for the auto-creation pipeline.

    Usage:
        engine = AutoCreationEngine(dispatcharr_client)

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
        triggered_by: str = "manual",
        m3u_account_ids: list[int] = None,
        rule_ids: list[int] = None,
        execution_id: int | None = None,
    ) -> dict:
        """
        Run the full auto-creation pipeline.

        Args:
            dry_run: If True, only simulate changes without applying
            triggered_by: How the pipeline was triggered (manual, scheduled, m3u_refresh)
            m3u_account_ids: Optional list of M3U account IDs to process (None = all)
            rule_ids: Optional list of rule IDs to run (None = all enabled)
            execution_id: Optional pre-created execution record id. When the
                router enqueues a background task (bd-enfsy 202+poll pattern)
                it creates an AutoCreationExecution(status="running") up front
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
        if not rules:
            logger.info("[AUTO-CREATE-ENGINE] No enabled rules found")
            # If a pre-created execution exists, mark it completed so it does
            # not stay in "running" forever (otherwise the frontend poll would
            # spin indefinitely on a no-op run).
            if execution_id is not None:
                await self._finalize_no_op_execution(execution_id)
            return {
                "success": True,
                "message": "No enabled rules to process",
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

        # Fetch streams from M3U accounts
        streams = await self._fetch_streams(m3u_account_ids, rules)
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
        if not dry_run:
            await self._capture_snapshot(execution.id)

        # Process streams through rules
        results = await self._process_streams(
            streams, rules, execution, dry_run, triggered_by=triggered_by
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
        execution.set_warnings(normalization_warnings)

        if dry_run:
            execution.set_dry_run_results(results["dry_run_results"])

        await self._save_execution(execution)

        # Update rule stats
        if not dry_run:
            await self._update_rule_stats(rules, results)

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
        triggered_by: str = "manual",
        execution_id: int | None = None,
    ) -> dict:
        """
        Run a specific rule.

        Args:
            rule_id: ID of the rule to run
            dry_run: If True, only simulate changes
            triggered_by: How the rule was triggered
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

        * **Snapshot present** — delegates to :meth:`restore_snapshot` for the
          FULL whole-run revert (re-adds streams the run removed, removes
          streams it added, restores drifted metadata). This is an OPTIMISTIC
          OVERWRITE (ADR-010 §D5) that can clobber edits made AFTER the run, so
          it requires ``confirm=True`` — the same acknowledgement the
          ``/restore-snapshot`` endpoint demands. Without it, the call is
          refused with ``requires_confirm=True`` and ``has_snapshot=True`` so
          the router can surface the overwrite warning (HTTP 409). This is the
          ONLY behaviour change for existing callers, and it ONLY affects runs
          that have a snapshot (i.e. runs created after the snapshot feature
          shipped).

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
            execution = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
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
            has_snapshot = session.query(AutoCreationSnapshot.id).filter(
                AutoCreationSnapshot.execution_id == execution_id
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
                # Confirmed: delegate to the full snapshot-restore. Close this
                # session first — restore_snapshot opens its own.
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
        """Whole-run revert via the pre-run AutoCreationSnapshot (ADR-010 §D8).

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
             tvg_id) in one PATCH.
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
            execution = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
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

            snapshot = session.query(AutoCreationSnapshot).filter(
                AutoCreationSnapshot.execution_id == execution_id
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

    async def _load_rules(self, rule_ids: list[int] = None) -> list[AutoCreationRule]:
        """Load enabled rules sorted by priority."""
        session = get_session()
        try:
            query = session.query(AutoCreationRule).filter(
                AutoCreationRule.enabled == True
            )

            if rule_ids:
                query = query.filter(AutoCreationRule.id.in_(rule_ids))

            rules = query.order_by(AutoCreationRule.priority).all()
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
        self, rules: list[AutoCreationRule]
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
        rules: list[AutoCreationRule] = None
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
        rules: list[AutoCreationRule],
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
        rules: list[AutoCreationRule],
        rule_channel_order: dict,
        results: dict,
        dry_run: bool,
        settings=None,
        stream_m3u_map: dict = None,
        custom_stream_ids: set[int] | None = None,
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

    # =========================================================================
    # Stream Processing
    # =========================================================================

    async def _process_streams(
        self,
        streams: list[StreamContext],
        rules: list[AutoCreationRule],
        execution: AutoCreationExecution,
        dry_run: bool,
        triggered_by: str = "manual",
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

        # Fetch all profile IDs if default profiles are configured
        all_profile_ids = []
        if settings.default_channel_profile_ids:
            try:
                profiles = await self.client.get_channel_profiles()
                all_profile_ids = [p["id"] for p in profiles]
            except Exception as e:
                logger.warning("[AUTO-CREATE-ENGINE] Failed to fetch channel profiles: %s", e)

        # Pre-fetch EPG data and sources if any rule uses assign_epg
        epg_data = []
        epg_sources = []
        needs_epg = any(
            a.get("type") == "assign_epg" if isinstance(a, dict) else getattr(a, "type", "") == "assign_epg"
            for r in rules for a in r.get_actions()
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
        for s in streams:
            stream_m3u_map[s.stream_id] = s.m3u_account_id
            if getattr(s, "is_custom", False):
                custom_stream_ids.add(s.stream_id)

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
            await self._reorder_channel_streams(
                rules, rule_channel_order_streams, results, dry_run,
                settings=settings, stream_m3u_map=stream_m3u_map,
                custom_stream_ids=custom_stream_ids,
            )

            # =================================================================
            # Pass 4: Reconcile — clean up orphaned channels
            # =================================================================
            logger.debug("[AUTO-CREATE-ENGINE] Starting orphan reconciliation")
            await self._reconcile_orphans(
                rules, rule_channel_order, executor, execution, results, dry_run,
                settings=settings
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
        from auto_creation_schema import Action
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
        rules: list[AutoCreationRule],
        rule_channel_order: dict,
        executor,
        execution: AutoCreationExecution,
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

    async def _create_execution(self, mode: str, triggered_by: str) -> AutoCreationExecution:
        """Create a new execution record."""
        session = get_session()
        try:
            execution = AutoCreationExecution(
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

    async def _load_execution(self, execution_id: int) -> AutoCreationExecution | None:
        """Load an existing execution record by id (bd-enfsy: reuses the row
        the router pre-created when enqueuing background work)."""
        session = get_session()
        try:
            execution = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
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
            execution = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
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

    async def _save_execution(self, execution: AutoCreationExecution):
        """Save execution record."""
        session = get_session()
        try:
            session.merge(execution)
            session.commit()
        finally:
            session.close()

    async def _capture_snapshot(self, execution_id: int) -> None:
        """Persist a pre-run AutoCreationSnapshot for ``execution_id`` (ADR-010).

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

        Capture-failure policy (ADR-010 §D2, uc51o.2 v1 default):
        LOG-AND-PROCEED. A capture failure must NOT abort the mutating run —
        it logs a WARNING and the run continues with NO snapshot (the run is
        still revertible via the legacy entity-rollback). It does NOT raise.
        """
        try:
            channels = []
            for ch in (self._existing_channels or []):
                # §D3: exclude Dispatcharr-auto-created-from-groups channels.
                if ch.get("auto_created", False):
                    continue
                # §D1: stream IDs only. Match the executor's own coercion
                # (executor.py:918) — Dispatcharr embeds streams as a list of
                # IDs (or, defensively, dicts carrying an "id").
                stream_ids = [
                    s["id"] if isinstance(s, dict) else s
                    for s in ch.get("streams", [])
                ]
                channels.append({
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "channel_group_id": ch.get("channel_group_id"),
                    "epg_data_id": ch.get("epg_data_id"),
                    "tvg_id": ch.get("tvg_id"),
                    "stream_ids": stream_ids,
                })

            session = get_session()
            try:
                snapshot = AutoCreationSnapshot(
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
        execution: AutoCreationExecution,
        stream: StreamContext,
        winning_rule: AutoCreationRule,
        losing_rules: list[AutoCreationRule],
        conflict_type: str
    ):
        """Record a conflict in the database."""
        session = get_session()
        try:
            conflict = AutoCreationConflict(
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

    async def _update_rule_stats(self, rules: list[AutoCreationRule], results: dict):
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
                -(1 if sid in custom_stream_ids else 0) if criterion == "custom_streams" else 0
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

_engine_instance: Optional[AutoCreationEngine] = None


def get_auto_creation_engine() -> Optional[AutoCreationEngine]:
    """Get the auto-creation engine instance."""
    return _engine_instance


def set_auto_creation_engine(engine: AutoCreationEngine):
    """Set the auto-creation engine instance."""
    global _engine_instance
    _engine_instance = engine


async def init_auto_creation_engine(client) -> AutoCreationEngine:
    """Initialize the auto-creation engine with a Dispatcharr client."""
    engine = AutoCreationEngine(client)
    set_auto_creation_engine(engine)
    return engine
