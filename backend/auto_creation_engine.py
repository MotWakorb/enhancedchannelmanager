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
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional
import json

from database import get_session
from models import (
    AutoCreationRule,
    AutoCreationExecution,
    AutoCreationConflict,
    StreamStats
)
from auto_creation_schema import (
    Condition,
    Action,
    ActionType,
    validate_rule
)
from auto_creation_evaluator import (
    ConditionEvaluator,
    StreamContext,
    EvaluationResult
)
from auto_creation_executor import (
    ActionExecutor,
    ExecutionContext,
    ActionResult
)


logger = logging.getLogger(__name__)


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

    async def run_pipeline(
        self,
        dry_run: bool = False,
        triggered_by: str = "manual",
        m3u_account_ids: list[int] = None,
        rule_ids: list[int] = None
    ) -> dict:
        """
        Run the full auto-creation pipeline.

        Args:
            dry_run: If True, only simulate changes without applying
            triggered_by: How the pipeline was triggered (manual, scheduled, m3u_refresh)
            m3u_account_ids: Optional list of M3U account IDs to process (None = all)
            rule_ids: Optional list of rule IDs to run (None = all enabled)

        Returns:
            Dict with execution summary and results
        """
        started_at = datetime.utcnow()
        logger.info(f"Starting auto-creation pipeline (dry_run={dry_run}, triggered_by={triggered_by})")

        # Load existing channels and groups
        await self._load_existing_data()

        # Load enabled rules
        rules = await self._load_rules(rule_ids)
        if not rules:
            logger.info("No enabled rules found")
            return {
                "success": True,
                "message": "No enabled rules to process",
                "streams_evaluated": 0,
                "streams_matched": 0
            }

        # Fetch streams from M3U accounts
        streams = await self._fetch_streams(m3u_account_ids, rules)
        logger.info(f"Fetched {len(streams)} streams to evaluate against {len(rules)} rules")

        # Create execution record
        execution = await self._create_execution(
            mode="dry_run" if dry_run else "execute",
            triggered_by=triggered_by
        )

        # Process streams through rules
        results = await self._process_streams(streams, rules, execution, dry_run)

        # Finalize execution record
        completed_at = datetime.utcnow()
        execution.completed_at = completed_at
        execution.duration_seconds = (completed_at - started_at).total_seconds()
        execution.status = "completed"
        execution.streams_evaluated = results["streams_evaluated"]
        execution.streams_matched = results["streams_matched"]
        execution.channels_created = results["channels_created"]
        execution.channels_updated = results["channels_updated"]
        execution.groups_created = results["groups_created"]
        execution.streams_merged = results["streams_merged"]
        execution.streams_skipped = results["streams_skipped"]
        execution.set_created_entities(results["created_entities"])
        execution.set_modified_entities(results["modified_entities"])
        execution.set_execution_log(results["execution_log"])

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
            f"Pipeline completed: {results['streams_matched']}/{results['streams_evaluated']} streams matched, "
            f"{results['channels_created']} channels created, {results['channels_updated']} updated{orphan_info}"
        )

        return {
            "success": True,
            "execution_id": execution.id,
            "mode": execution.mode,
            "duration_seconds": execution.duration_seconds,
            **results
        }

    async def run_rule(
        self,
        rule_id: int,
        dry_run: bool = False,
        triggered_by: str = "manual"
    ) -> dict:
        """
        Run a specific rule.

        Args:
            rule_id: ID of the rule to run
            dry_run: If True, only simulate changes
            triggered_by: How the rule was triggered

        Returns:
            Dict with execution summary
        """
        return await self.run_pipeline(
            dry_run=dry_run,
            triggered_by=triggered_by,
            rule_ids=[rule_id]
        )

    async def rollback_execution(self, execution_id: int, rolled_back_by: str = "manual") -> dict:
        """
        Rollback changes from a specific execution.

        Args:
            execution_id: ID of the execution to rollback
            rolled_back_by: Who/what initiated the rollback

        Returns:
            Dict with rollback results
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

            logger.info(f"Rolling back execution {execution_id}")

            # Rollback created entities (in reverse order)
            created = execution.get_created_entities()
            for entity in reversed(created):
                await self._rollback_created_entity(entity)

            # Restore modified entities
            modified = execution.get_modified_entities()
            for entity in modified:
                await self._rollback_modified_entity(entity)

            # Mark execution as rolled back
            execution.status = "rolled_back"
            execution.rolled_back_at = datetime.utcnow()
            execution.rolled_back_by = rolled_back_by
            session.commit()

            logger.info(f"Rollback complete: {len(created)} created entities removed, {len(modified)} entities restored")

            return {
                "success": True,
                "execution_id": execution_id,
                "rule_name": execution.rule_name or f"Execution {execution_id}",
                "entities_removed": len(created),
                "entities_restored": len(modified)
            }

        except Exception as e:
            session.rollback()
            logger.error(f"Rollback failed: {e}")
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
            logger.debug(f"Loaded {len(self._existing_channels)} channels, {len(self._existing_groups)} groups")
        except Exception as e:
            logger.error(f"Failed to load existing data: {e}")
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
            return rules

        finally:
            session.close()

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
                    result = await self.client.get_streams(
                        page=page, page_size=100, m3u_account=account_id
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
                logger.error(f"Failed to fetch streams from M3U account {account_id}: {e}")

        return all_streams

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
            if rule.sort_field != "quality" or not getattr(rule, 'probe_on_sort', False):
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
            logger.warning("[Probe-on-sort] Prober not available, skipping probe step")
            return

        count = len(streams_to_probe)
        logger.info(f"[Probe-on-sort] Probing {count} unprobed stream(s) for quality sorting")

        if dry_run:
            results["dry_run_results"].append({
                "stream_id": None,
                "stream_name": "[Probe-on-sort]",
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
                    logger.warning(f"[Probe-on-sort] Failed to probe stream {stream_id} ({name}): {e}")

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
                except (ValueError, IndexError):
                    pass

        results["execution_log"].append({
            "stream_id": None,
            "stream_name": f"[Probe-on-sort]",
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
        rule_merged_channels: dict,
        results: dict,
        dry_run: bool
    ):
        """
        Pass 3.5: Reorder streams within channels by quality (resolution).

        For each rule with sort_field set, collect channels that had streams
        merged during this execution, and reorder the streams within each
        channel by resolution_height.
        """
        for rule in rules:
            if not rule.sort_field:
                continue

            channel_ids = rule_merged_channels.get(rule.id)
            if not channel_ids:
                continue

            sort_reverse = (rule.sort_order == "desc")

            for channel_id in channel_ids:
                # Find channel in existing channels cache
                channel = None
                for ch in (self._existing_channels or []):
                    if ch.get("id") == channel_id:
                        channel = ch
                        break
                if not channel:
                    continue

                # Get current stream IDs in the channel
                current_streams = [
                    s["id"] if isinstance(s, dict) else s
                    for s in channel.get("streams", [])
                ]
                if len(current_streams) < 2:
                    continue

                # Look up resolution for each stream
                def stream_sort_key(sid):
                    stats = self._stream_stats_cache.get(sid)
                    if stats and stats.get("resolution"):
                        try:
                            parts = stats["resolution"].split("x")
                            if len(parts) == 2:
                                return int(parts[1])
                        except (ValueError, IndexError):
                            pass
                    return 0

                sorted_streams = sorted(
                    current_streams,
                    key=stream_sort_key,
                    reverse=sort_reverse
                )

                # Skip if order didn't change
                if sorted_streams == current_streams:
                    continue

                channel_name = channel.get("name", f"Channel #{channel_id}")

                if dry_run:
                    results["dry_run_results"].append({
                        "stream_id": None,
                        "stream_name": f"[Stream-reorder] {channel_name}",
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "action": f"Would reorder {len(sorted_streams)} streams in '{channel_name}' "
                                  f"by {rule.sort_field} {rule.sort_order or 'asc'}",
                        "would_create": False,
                        "would_modify": True
                    })
                else:
                    try:
                        await self.client.update_channel(channel_id, {"streams": sorted_streams})
                        # Update cache
                        channel["streams"] = sorted_streams
                        results["execution_log"].append({
                            "stream_id": None,
                            "stream_name": f"[Stream-reorder] {channel_name}",
                            "m3u_account_id": None,
                            "rules_evaluated": [],
                            "actions_executed": [{
                                "type": "reorder_streams",
                                "description": f"Reordered {len(sorted_streams)} streams in '{channel_name}' "
                                              f"by {rule.sort_field} {rule.sort_order or 'asc'}",
                                "success": True,
                                "entity_id": channel_id,
                                "error": None
                            }]
                        })
                        logger.info(
                            f"[Stream-reorder] Reordered {len(sorted_streams)} streams in "
                            f"'{channel_name}' by {rule.sort_field} {rule.sort_order or 'asc'}"
                        )
                    except Exception as e:
                        logger.error(
                            f"[Stream-reorder] Failed to reorder streams in '{channel_name}': {e}"
                        )

    # =========================================================================
    # Stream Processing
    # =========================================================================

    async def _process_streams(
        self,
        streams: list[StreamContext],
        rules: list[AutoCreationRule],
        execution: AutoCreationExecution,
        dry_run: bool
    ) -> dict:
        """
        Process streams through the rules pipeline.

        Args:
            streams: List of stream contexts to process
            rules: List of rules sorted by priority
            execution: Execution record for tracking
            dry_run: Whether to simulate only

        Returns:
            Dict with processing results
        """
        # Initialize evaluator and executor
        evaluator = ConditionEvaluator(self._existing_channels, self._existing_groups)

        # Create normalization engine if any rule uses normalize_names
        norm_engine = None
        if any(getattr(r, 'normalize_names', False) for r in rules):
            try:
                from normalization_engine import get_normalization_engine
                session = get_session()
                norm_engine = get_normalization_engine(session)
            except Exception as e:
                logger.warning(f"Failed to initialize normalization engine: {e}")

        executor = ActionExecutor(
            self.client, self._existing_channels, self._existing_groups,
            normalization_engine=norm_engine
        )

        # Results tracking
        results = {
            "streams_evaluated": 0,
            "streams_matched": 0,
            "channels_created": 0,
            "channels_updated": 0,
            "groups_created": 0,
            "streams_merged": 0,
            "streams_skipped": 0,
            "channels_removed": 0,
            "channels_moved": 0,
            "created_entities": [],
            "modified_entities": [],
            "dry_run_results": [],
            "conflicts": [],
            "execution_log": [],
            "rule_match_counts": {}
        }

        # Track which streams have been processed by which rules
        stream_rule_matches = {}  # stream_id -> list of (rule_id, priority)

        # =====================================================================
        # Pass 1: Evaluate all streams against all rules, collect matches
        # =====================================================================
        matched_entries = []  # list of (stream, winning_rule, losing_rules, stream_rules_log)

        for stream in streams:
            results["streams_evaluated"] += 1

            # Track rules that match this stream
            matching_rules = []

            # Build per-stream log of rule evaluations
            stream_rules_log = []

            for rule in rules:
                # Check if rule applies to this M3U account
                if rule.m3u_account_id and rule.m3u_account_id != stream.m3u_account_id:
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

                if matched:
                    matching_rules.append(rule)

                    # Check for conflicts (multiple rules matching same stream)
                    if stream.stream_id not in stream_rule_matches:
                        stream_rule_matches[stream.stream_id] = []
                    stream_rule_matches[stream.stream_id].append((rule.id, rule.priority))

                    if rule.stop_on_first_match:
                        break

            if not matching_rules:
                continue

            # Determine winning and losing rules
            winning_rule = matching_rules[0]
            losing_rules = matching_rules[1:] if len(matching_rules) > 1 else []

            matched_entries.append((stream, winning_rule, losing_rules, stream_rules_log))

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
                entries.sort(
                    key=lambda e: _sort_key(e[0], rule.sort_field),
                    reverse=(rule.sort_order == "desc")
                )
            sorted_entries.extend(entries)

        # Track channel IDs per rule in sorted order (for Pass 3 renumber)
        rule_channel_order = defaultdict(list)  # rule_id -> [channel_id, ...] in sorted order
        # Track channels that had streams merged (for Pass 3.5 stream reorder)
        rule_merged_channels = defaultdict(set)  # rule_id -> set of channel_ids with merges

        # =====================================================================
        # Pass 2: Execute actions on sorted matches
        # =====================================================================
        for stream, winning_rule, losing_rules, stream_rules_log in sorted_entries:
            results["streams_matched"] += 1

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
                    normalize_names=getattr(winning_rule, 'normalize_names', False)
                )

                actions_log.append({
                    "type": action_result.action_type,
                    "description": action_result.description,
                    "success": action_result.success,
                    "entity_id": action_result.entity_id,
                    "error": action_result.error
                })

                # Check for stop_processing action
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
            results["created_entities"].extend(exec_ctx.created_entities)
            results["modified_entities"].extend(exec_ctx.modified_entities)

            # Track channel ID for Pass 3 renumber
            if exec_ctx.current_channel_id:
                rule_channel_order[winning_rule.id].append(exec_ctx.current_channel_id)
                # Track merged channels for Pass 3.5 stream reorder
                if exec_ctx.streams_merged > 0:
                    rule_merged_channels[winning_rule.id].add(exec_ctx.current_channel_id)

            if stop_processing:
                break

        # =====================================================================
        # Pass 3: Re-sort existing channels for rules with sort_field
        # =====================================================================
        for rule in rules:
            if not rule.sort_field:
                continue
            channel_ids = rule_channel_order.get(rule.id)
            if not channel_ids or len(channel_ids) < 2:
                continue

            starting_number = _get_rule_starting_number(rule)
            if starting_number is None:
                continue

            if dry_run:
                results["dry_run_results"].append({
                    "stream_id": None,
                    "stream_name": "[Re-sort]",
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
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": f"[Re-sort] Rule '{rule.name}'",
                        "m3u_account_id": None,
                        "rules_evaluated": [],
                        "actions_executed": [{
                            "type": "renumber_channels",
                            "description": f"Renumbered {len(channel_ids)} channels starting at #{starting_number} "
                                           f"(sorted by {rule.sort_field} {rule.sort_order or 'asc'})",
                            "success": True,
                            "entity_id": None,
                            "error": None
                        }]
                    })
                    logger.info(
                        f"[Re-sort] Rule '{rule.name}': renumbered {len(channel_ids)} channels "
                        f"starting at #{starting_number}"
                    )
                except Exception as e:
                    logger.error(f"[Re-sort] Rule '{rule.name}': failed to renumber channels: {e}")
                    results["execution_log"].append({
                        "stream_id": None,
                        "stream_name": f"[Re-sort] Rule '{rule.name}'",
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

        # =====================================================================
        # Pass 3.5: Reorder streams within channels by quality
        # =====================================================================
        await self._reorder_channel_streams(
            rules, rule_merged_channels, results, dry_run
        )

        # =====================================================================
        # Pass 4: Reconcile — clean up orphaned channels
        # =====================================================================
        await self._reconcile_orphans(
            rules, rule_channel_order, executor, execution, results, dry_run
        )

        return results

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
        dry_run: bool
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
                        f"[Reconcile] Rule '{rule.name}': first run, populated "
                        f"{len(current_ids)} managed channel IDs"
                    )
                    continue

                orphan_ids = previous_ids - current_ids

                if not orphan_ids:
                    # No orphans — just update managed set
                    if not dry_run and current_ids != previous_ids:
                        rule.set_managed_channel_ids(list(current_ids))
                        session.merge(rule)
                    continue

                logger.info(
                    f"[Reconcile] Rule '{rule.name}': {len(orphan_ids)} orphaned channels "
                    f"(previous={len(previous_ids)}, current={len(current_ids)})"
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
                remaining_channel_ids = rule_channel_order.get(rule.id, [])
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
                            results["execution_log"].append({
                                "stream_id": None,
                                "stream_name": f"[Renumber] Rule '{rule.name}' after orphan cleanup",
                                "m3u_account_id": None,
                                "rules_evaluated": [],
                                "actions_executed": [{
                                    "type": "renumber_channels",
                                    "description": f"Renumbered {len(remaining_channel_ids)} channels starting at #{starting_number} after removing {len(orphan_ids)} orphans",
                                    "success": True,
                                    "entity_id": None,
                                    "error": None
                                }]
                            })
                            logger.info(
                                f"[Reconcile] Rule '{rule.name}': renumbered {len(remaining_channel_ids)} channels "
                                f"starting at #{starting_number} after orphan cleanup"
                            )
                        except Exception as e:
                            logger.error(f"[Reconcile] Rule '{rule.name}': failed to renumber after cleanup: {e}")

                # Update managed_channel_ids (not during dry run)
                if not dry_run:
                    rule.set_managed_channel_ids(list(current_ids))
                    session.merge(rule)

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"[Reconcile] Failed: {e}")
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

    async def _save_execution(self, execution: AutoCreationExecution):
        """Save execution record."""
        session = get_session()
        try:
            session.merge(execution)
            session.commit()
        finally:
            session.close()

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
                logger.info(f"Deleted channel {entity_id} ({entity.get('name')})")
            elif entity_type == "group":
                await self.client.delete_channel_group(entity_id)
                logger.info(f"Deleted group {entity_id} ({entity.get('name')})")
        except Exception as e:
            logger.error(f"Failed to rollback {entity_type} {entity_id}: {e}")

    async def _rollback_modified_entity(self, entity: dict):
        """Rollback a modified entity by restoring its previous state."""
        entity_type = entity.get("type")
        entity_id = entity.get("id")
        previous = entity.get("previous", {})

        try:
            if entity_type == "channel" and previous:
                await self.client.update_channel(entity_id, previous)
                logger.info(f"Restored channel {entity_id} to previous state")
        except Exception as e:
            logger.error(f"Failed to restore {entity_type} {entity_id}: {e}")


# =============================================================================
# Sort Helpers
# =============================================================================

def _natural_sort_key(s: str) -> list:
    """Split string into text/number parts for natural sorting.

    "Olympics 2" < "Olympics 10" (unlike pure alphabetical).
    """
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def _sort_key(stream: StreamContext, sort_field: str):
    """Get sort key for a stream based on the sort field."""
    if sort_field == "stream_name":
        return stream.stream_name.lower()
    elif sort_field == "stream_name_natural":
        return _natural_sort_key(stream.stream_name)
    elif sort_field == "group_name":
        return (stream.group_name or "").lower()
    elif sort_field == "quality":
        return stream.resolution_height or 0
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
