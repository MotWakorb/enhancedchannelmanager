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

import safe_regex
from config import get_settings
from database import get_session
from models import (
    AutoCreationRule,
    AutoCreationExecution,
    AutoCreationConflict,
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
        logger.info("[AUTO-CREATE-ENGINE] Starting auto-creation pipeline (dry_run=%s, triggered_by=%s)", dry_run, triggered_by)

        # Load existing channels and groups
        await self._load_existing_data()

        # Load enabled rules
        rules = await self._load_rules(rule_ids)
        if not rules:
            logger.info("[AUTO-CREATE-ENGINE] No enabled rules found")
            return {
                "success": True,
                "message": "No enabled rules to process",
                "streams_evaluated": 0,
                "streams_matched": 0
            }

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

        # Create execution record
        execution = await self._create_execution(
            mode="dry_run" if dry_run else "execute",
            triggered_by=triggered_by
        )

        # Process streams through rules
        results = await self._process_streams(streams, rules, execution, dry_run)

        # Prepend exclusion log entries and set streams_excluded count
        results["execution_log"] = exclusion_log + results["execution_log"]
        results["streams_excluded"] = len(exclusion_log)

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
        execution.streams_excluded = results.get("streams_excluded", 0)
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

            logger.info("[AUTO-CREATE-ENGINE] Rolling back execution %s", execution_id)

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
        stream_m3u_map: dict = None
    ):
        """
        Pass 3.5: Reorder streams within channels using smart sort.

        Uses the user's stream_sort_priority, stream_sort_enabled, and
        m3u_account_priorities settings (same logic as stream_prober smart sort).
        Falls back to resolution-only if settings not available.
        """
        if stream_m3u_map is None:
            stream_m3u_map = {}

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

        # Build stream_id -> m3u_account_id map for smart sort M3U priority lookups
        stream_m3u_map = {}
        for s in streams:
            stream_m3u_map[s.stream_id] = s.m3u_account_id

        executor = ActionExecutor(
            self.client, self._existing_channels, self._existing_groups,
            normalization_engine=norm_engine,
            settings=settings,
            all_profile_ids=all_profile_ids,
            epg_data=epg_data,
            epg_sources=epg_sources
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
            "streams_removed": 0,
            "channels_removed": 0,
            "channels_moved": 0,
            "created_entities": [],
            "modified_entities": [],
            "dry_run_results": [],
            "conflicts": [],
            "execution_log": [],
            "rule_match_counts": {},
            "probe_stream_ids": set(),
            "streams_probed": 0
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
        for stream, winning_rule, losing_rules, stream_rules_log in sorted_entries:
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
                    match_scope_target_group=bool(getattr(winning_rule, 'match_scope_target_group', False))
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
            results["streams_removed"] += exec_ctx.streams_removed
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
                break

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

        # =====================================================================
        # Pass 3.5: Reorder streams within channels by smart sort
        # =====================================================================
        logger.debug("[AUTO-CREATE-ENGINE] Starting stream reorder within channels")
        await self._reorder_channel_streams(
            rules, rule_channel_order_streams, results, dry_run,
            settings=settings, stream_m3u_map=stream_m3u_map
        )

        # =====================================================================
        # Pass 4: Reconcile — clean up orphaned channels
        # =====================================================================
        logger.debug("[AUTO-CREATE-ENGINE] Starting orphan reconciliation")
        await self._reconcile_orphans(
            rules, rule_channel_order, executor, execution, results, dry_run,
            settings=settings
        )

        # =====================================================================
        # Pass 5: Dummy EPG refresh + retry deferred assign_epg
        # =====================================================================
        if executor._deferred_epg_assignments:
            logger.info(
                "[AUTO-CREATE-ENGINE] Pass 5: %s deferred EPG assignments to retry",
                len(executor._deferred_epg_assignments)
            )
            await self._refresh_dummy_epg_and_retry(executor, results, epg_sources, dry_run)

        # =====================================================================
        # Pass 6: Batch probe streams queued by probe_streams actions
        # =====================================================================
        if results["probe_stream_ids"]:
            await self._batch_probe_streams(
                results["probe_stream_ids"], streams, results, dry_run
            )

        # Clean up non-serializable set before returning
        del results["probe_stream_ids"]

        return results

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
            source_url = src.get("url", "") if src else ""

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
                logger.info("[AUTO-CREATE-ENGINE] Deleted channel %s (%s)", entity_id, entity.get('name'))
            elif entity_type == "group":
                await self.client.delete_channel_group(entity_id)
                logger.info("[AUTO-CREATE-ENGINE] Deleted group %s (%s)", entity_id, entity.get('name'))
        except Exception as e:
            logger.error("[AUTO-CREATE-ENGINE] Failed to rollback %s %s: %s", entity_type, entity_id, e)

    async def _rollback_modified_entity(self, entity: dict):
        """Rollback a modified entity by restoring its previous state."""
        entity_type = entity.get("type")
        entity_id = entity.get("id")
        previous = entity.get("previous", {})

        try:
            if entity_type == "channel" and previous:
                await self.client.update_channel(entity_id, previous)
                logger.info("[AUTO-CREATE-ENGINE] Restored channel %s to previous state", entity_id)
        except Exception as e:
            logger.error("[AUTO-CREATE-ENGINE] Failed to restore %s %s: %s", entity_type, entity_id, e)


# =============================================================================
# Sort Helpers
# =============================================================================

def _smart_sort_streams(
    stream_ids: list[int],
    stats_cache: dict,
    stream_m3u_map: dict,
    channel_name: str = "unknown",
    settings=None
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
    """
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
                m3u_priority_value = 0
                m3u_account_id = stream_m3u_map.get(sid)
                if m3u_account_id is not None:
                    m3u_priority_value = m3u_priorities.get(str(m3u_account_id), 0)
                values.append(-m3u_priority_value)

            elif criterion == "audio_channels":
                audio_ch = (stats.get("audio_channels") if stats else 0) or 0
                values.append(-audio_ch)

            elif criterion == "video_codec":
                from stream_prober import get_codec_rank
                codec_value = get_codec_rank(stats.get("video_codec")) if stats else 0
                values.append(-codec_value)

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
            return (0, 0) + tuple(0 for _ in active_criteria)

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
    pri_map = getattr(settings, "m3u_account_priorities", None) or {}

    def sort_key(sid: int):
        aid = stream_m3u_map.get(sid)
        pri = pri_map.get(str(aid), 0) if aid is not None else 0
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
) -> list[int]:
    """Sort by probed resolution height; missing stats count as 0.

    When Settings enable deprioritization, push failed/black-screen/low-FPS
    streams to the bottom (same categories as smart sort).
    """

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
                # Within deprioritized bucket: still order by resolution (desc/asc)
                return (1, rank, -h if order == "desc" else h, sid)
            if isinstance(stats, dict) and stats.get("is_black_screen"):
                bucket = "black_screen"
                rank = failed_rank.get(bucket, len(failed_rank))
                return (1, rank, -h if order == "desc" else h, sid)
            if isinstance(stats, dict) and stats.get("is_low_fps"):
                bucket = "low_fps"
                rank = failed_rank.get(bucket, len(failed_rank))
                return (1, rank, -h if order == "desc" else h, sid)

        # Good stream bucket.
        return (0, 0, -h if order == "desc" else h, sid)

    sorted_ids = sorted(stream_ids, key=sort_key)
    logger.info(
        "[AUTO-CREATE-ENGINE] Channel '%s': quality sort (%s) -> %s",
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
) -> list[int]:
    """Dispatch stream reordering based on rule.stream_sort_field."""
    field = (getattr(rule, "stream_sort_field", None) or "").strip()
    order = (getattr(rule, "stream_sort_order", None) or "asc").lower()
    if order not in ("asc", "desc"):
        order = "asc"

    if not field or field == "smart_sort":
        return _smart_sort_streams(
            stream_ids, stats_cache, stream_m3u_map, channel_name, settings
        )

    if field == "provider_order":
        return _sort_streams_by_m3u_account_priority(
            stream_ids, stream_m3u_map, settings, order, channel_name
        )

    if field == "quality":
        return _sort_streams_by_resolution_height(
            stream_ids, stats_cache, settings, order, channel_name
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
        stream_ids, stats_cache, stream_m3u_map, channel_name, settings
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
_TZ_SUFFIX_RE = re.compile(
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
