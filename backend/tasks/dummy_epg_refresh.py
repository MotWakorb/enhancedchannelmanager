"""
Dummy EPG Refresh Task.

Scheduled task to regenerate ECM dummy EPG XMLTV data and refresh
matching sources in Dispatcharr.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from dispatcharr_client import get_client
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task

logger = logging.getLogger(__name__)

# Polling configuration for waiting for refresh completion
POLL_INTERVAL_SECONDS = 5
MAX_WAIT_SECONDS = 300


@register_task
class DummyEPGRefreshTask(TaskScheduler):
    """
    Regenerate ECM dummy EPG XMLTV cache and refresh matching
    Dispatcharr EPG sources.

    Pipeline:
    1. Regenerate all ECM XMLTV cache (same as POST /api/dummy-epg/generate)
    2. Find Dispatcharr EPG sources whose URL contains /api/dummy-epg/xmltv
    3. Trigger refresh for each matching source
    4. Poll until refresh completes
    """

    task_id = "dummy_epg_refresh"
    task_name = "Dummy EPG Refresh"
    task_description = "Regenerate ECM dummy EPG data and refresh in Dispatcharr"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
            )
        super().__init__(schedule_config)

    async def _regenerate_xmltv(self) -> int:
        """Regenerate XMLTV cache for all enabled profiles. Returns profile count."""
        from database import get_session
        from models import DummyEPGProfile
        from dummy_epg_engine import generate_xmltv
        from cache import get_cache

        cache = get_cache()
        cache.invalidate_prefix("dummy_epg_xmltv")

        db = get_session()
        try:
            profiles = db.query(DummyEPGProfile).filter(
                DummyEPGProfile.enabled == True  # noqa: E712
            ).all()

            if not profiles:
                return 0

            client = get_client()
            # Fetch channels from Dispatcharr
            all_channels = []
            page = 1
            while True:
                resp = await client.get_channels(page=page, page_size=500)
                results = resp.get("results", [])
                all_channels.extend(results)
                if not resp.get("next"):
                    break
                page += 1
            channel_map = {ch["id"]: ch for ch in all_channels}

            # Resolve group IDs to channel assignments
            def _resolve(group_ids):
                group_set = set(group_ids)
                return [
                    {"channel_id": ch_id, "channel_name": ch.get("name", "")}
                    for ch_id, ch in channel_map.items()
                    if ch.get("channel_group_id") in group_set
                ]

            # Generate combined XMLTV
            profile_data = []
            for profile in profiles:
                p_dict = profile.to_dict()
                p_dict["channel_assignments"] = _resolve(p_dict.get("channel_group_ids", []))
                p_dict["channel_map"] = channel_map
                profile_data.append(p_dict)

            xml_string = generate_xmltv(profile_data, channel_map)
            cache.set("dummy_epg_xmltv_all", xml_string)

            # Also cache per-profile
            for profile in profiles:
                p_dict = profile.to_dict()
                p_dict["channel_assignments"] = _resolve(p_dict.get("channel_group_ids", []))
                p_dict["channel_map"] = channel_map
                per_xml = generate_xmltv([p_dict], channel_map)
                cache.set(f"dummy_epg_xmltv_{profile.id}", per_xml)

            logger.info("[%s] Regenerated XMLTV for %s profiles", self.task_id, len(profiles))
            return len(profiles)
        finally:
            db.close()

    async def execute(self) -> TaskResult:
        """Execute the dummy EPG refresh pipeline."""
        client = get_client()
        started_at = datetime.utcnow()

        # Step 1: Regenerate XMLTV cache
        self._set_progress(status="regenerating", current_item="Regenerating XMLTV...")

        try:
            profile_count = await self._regenerate_xmltv()
            logger.info("[%s] Regenerated %s profiles", self.task_id, profile_count)
        except Exception as e:
            logger.exception("[%s] Failed to regenerate XMLTV: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"Failed to regenerate XMLTV: {e}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        if self._cancel_requested:
            return TaskResult(
                success=False, message="Cancelled", error="CANCELLED",
                started_at=started_at, completed_at=datetime.utcnow(),
            )

        # Step 2: Find matching Dispatcharr sources
        self._set_progress(status="finding_sources", current_item="Finding Dispatcharr sources...")

        try:
            all_sources = await client.get_epg_sources()
        except Exception as e:
            logger.exception("[%s] Failed to fetch EPG sources: %s", self.task_id, e)
            return TaskResult(
                success=True,
                message=f"Regenerated {profile_count} profiles, but failed to fetch Dispatcharr sources: {e}",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=profile_count,
                success_count=profile_count,
            )

        matching = [
            s for s in all_sources
            if s.get("is_active") and s.get("url") and "/api/dummy-epg/xmltv" in s["url"]
        ]

        if not matching:
            logger.info("[%s] No matching Dispatcharr sources to refresh", self.task_id)
            return TaskResult(
                success=True,
                message=f"Regenerated {profile_count} profiles, no Dispatcharr sources to refresh",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                total_items=profile_count,
                success_count=profile_count,
            )

        # Step 3: Refresh each matching source
        self._set_progress(
            total=len(matching), current=0, status="refreshing",
            current_item=f"Refreshing {len(matching)} sources in Dispatcharr...",
        )

        success_count = 0
        failed_count = 0
        refreshed = []
        errors = []

        for i, source in enumerate(matching):
            if self._cancel_requested:
                break

            source_id = source["id"]
            source_name = source.get("name", f"Source {source_id}")
            self._set_progress(
                current=i + 1,
                current_item=f"Refreshing {source_name}...",
            )

            try:
                initial_source = await client.get_epg_source(source_id)
                initial_updated = initial_source.get("updated_at") or initial_source.get("last_updated")

                logger.info("[%s] Triggering refresh for: %s (id=%s)", self.task_id, source_name, source_id)
                await client.refresh_epg_source(source_id)

                # Poll until refresh completes
                self._set_progress(current_item=f"Waiting for {source_name}...")
                wait_start = datetime.utcnow()

                while not self._cancel_requested:
                    elapsed = (datetime.utcnow() - wait_start).total_seconds()
                    if elapsed >= MAX_WAIT_SECONDS:
                        logger.warning("[%s] Timeout waiting for %s", self.task_id, source_name)
                        break

                    await asyncio.sleep(POLL_INTERVAL_SECONDS)

                    current_source = await client.get_epg_source(source_id)
                    current_updated = current_source.get("updated_at") or current_source.get("last_updated")

                    if current_updated and current_updated != initial_updated:
                        logger.info("[%s] %s refresh complete", self.task_id, source_name)
                        break
                    elif elapsed > 30:
                        logger.info("[%s] %s - assuming complete after %.0fs", self.task_id, source_name, elapsed)
                        break

                success_count += 1
                refreshed.append(source_name)
                self._increment_progress(success_count=1)
            except Exception as e:
                logger.error("[%s] Failed to refresh %s: %s", self.task_id, source_name, e)
                failed_count += 1
                errors.append(f"{source_name}: {e}")
                self._increment_progress(failed_count=1)

        self._set_progress(
            success_count=success_count,
            failed_count=failed_count,
            status="completed" if not self._cancel_requested else "cancelled",
        )

        duration = (datetime.utcnow() - started_at).total_seconds()
        logger.info(
            "[%s] Finished in %.1fs: regenerated %s profiles, refreshed %s/%s sources",
            self.task_id, duration, profile_count, success_count, len(matching),
        )

        if self._cancel_requested:
            return TaskResult(
                success=False, message="Cancelled", error="CANCELLED",
                started_at=started_at, completed_at=datetime.utcnow(),
                total_items=len(matching), success_count=success_count,
                failed_count=failed_count,
                details={"profiles_regenerated": profile_count, "refreshed": refreshed, "errors": errors},
            )

        msg = f"Regenerated {profile_count} profiles, refreshed {success_count} Dispatcharr sources"
        if failed_count:
            msg += f", {failed_count} failed"

        return TaskResult(
            success=True,
            message=msg,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=len(matching),
            success_count=success_count,
            failed_count=failed_count,
            details={"profiles_regenerated": profile_count, "refreshed": refreshed, "errors": errors},
        )
