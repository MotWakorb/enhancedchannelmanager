"""Probe channel streams when a matching EPG event becomes active."""

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import safe_regex
from database import get_session
from models import EPGEventProbeClaim
from sqlalchemy.exc import IntegrityError
from task_registry import register_task
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler


logger = logging.getLogger(__name__)


_MAX_EPG_DATA_ROWS = 50_000


@register_task
class EPGEventProbeTask(TaskScheduler):
    """Evaluate active guide entries and probe each matching channel once."""

    task_id = "epg_event_probe"
    task_name = "EPG Event Probe"
    task_description = "Probe every stream on a channel when a matching EPG event starts"
    default_enabled = False
    persist_config = False
    schedule_parameter_schema = {
        "description": "Probe channels while matching EPG events are active",
        "parameters": [
            {
                "name": "title_pattern",
                "type": "string",
                "label": "Title match expression",
                "description": "Regular expression matched against active EPG titles",
                "required": True,
            },
            {
                "name": "allow_reorder_after_probe",
                "type": "boolean",
                "label": "Allow stream reordering",
                "description": "Allow the global auto-reorder setting after this event probe",
                "default": True,
            },
        ],
    }

    def __init__(
        self,
        schedule_config: Optional[ScheduleConfig] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        claim_trigger: Optional[Callable[[str], bool]] = None,
        release_claims: Optional[Callable[[list[str]], None]] = None,
    ):
        super().__init__(schedule_config or ScheduleConfig(
            schedule_type=ScheduleType.MANUAL
        ))
        self._prober = None
        self._title_pattern: Optional[str] = None
        self._compiled_title_pattern = None
        self._allow_reorder_after_probe = True
        self._invocation_schedule_id: Optional[int] = None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._claim_trigger = claim_trigger or self._claim_trigger_key
        self._release_claims = release_claims or self._release_trigger_claims

    def set_prober(self, prober) -> None:
        self._prober = prober

    def get_config(self) -> dict:
        return {
            "title_pattern": self._title_pattern,
            "allow_reorder_after_probe": self._allow_reorder_after_probe,
        }

    @classmethod
    def validate_schedule_parameters(cls, parameters: Optional[dict]) -> None:
        if not isinstance(parameters, dict):
            raise ValueError("EPG event probe parameters must be an object")
        title_pattern = parameters.get("title_pattern")
        if not isinstance(title_pattern, str) or not title_pattern.strip():
            raise ValueError("title_pattern must be a non-empty string")
        try:
            safe_regex.compile(title_pattern)
        except safe_regex.SafeRegexError as error:
            raise ValueError("title_pattern must be a valid regex") from error
        if (
            "allow_reorder_after_probe" in parameters
            and type(parameters["allow_reorder_after_probe"]) is not bool
        ):
            raise ValueError("allow_reorder_after_probe must be a boolean")

    validate_run_parameters = validate_schedule_parameters

    def prepare_invocation_parameters(
        self,
        triggered_by: str,
        schedule_id: Optional[int],
        parameters: Optional[dict],
    ) -> Optional[dict]:
        del triggered_by
        self._invocation_schedule_id = schedule_id
        return parameters

    def update_config(self, config: dict) -> None:
        self.validate_schedule_parameters(config)
        self._title_pattern = config["title_pattern"]
        self._compiled_title_pattern = safe_regex.compile(self._title_pattern)
        self._allow_reorder_after_probe = config.get(
            "allow_reorder_after_probe", True
        )

    def restore_invocation_config(self, config: dict) -> None:
        self._title_pattern = config.get("title_pattern")
        self._compiled_title_pattern = (
            safe_regex.compile(self._title_pattern)
            if self._title_pattern
            else None
        )
        self._allow_reorder_after_probe = config.get(
            "allow_reorder_after_probe", True
        )
        self._invocation_schedule_id = None

    async def validate_config(self) -> tuple[bool, str]:
        if self._prober is None:
            return False, "StreamProber not initialized"
        if self._compiled_title_pattern is None:
            return False, "Title match expression is not configured"
        return True, ""

    @staticmethod
    def _parse_grid_time(value) -> Optional[datetime]:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _claim_trigger_key(self, trigger_key: str) -> bool:
        session = get_session()
        try:
            session.add(EPGEventProbeClaim(trigger_key=trigger_key))
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False
        finally:
            session.close()

    def _release_trigger_claims(self, trigger_keys: list[str]) -> None:
        session = get_session()
        try:
            session.query(EPGEventProbeClaim).filter(
                EPGEventProbeClaim.trigger_key.in_(trigger_keys)
            ).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    async def _all_channels(self) -> list[dict]:
        channels: list[dict] = []
        page = 1
        while True:
            response = await self._prober.client.get_channels(page=page, page_size=500)
            channels.extend(response.get("results", []))
            if not response.get("next"):
                return channels
            page += 1
            if page > 50:
                raise RuntimeError("Channel pagination exceeded the 50-page safety limit")

    async def execute(self) -> TaskResult:
        started_at = datetime.utcnow()
        if not self._enabled:
            return TaskResult(
                success=True,
                message="EPG event probe is disabled",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        if self._prober is None or self._compiled_title_pattern is None:
            return TaskResult(
                success=False,
                message="EPG event probe is not configured",
                error="CONFIG_INVALID",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        programs = await self._prober.client.get_epg_grid()
        active_matches = []
        for program in programs:
            start = self._parse_grid_time(program.get("start_time"))
            end = self._parse_grid_time(program.get("end_time"))
            title = program.get("title")
            tvg_id = program.get("tvg_id")
            if (
                start is not None
                and end is not None
                and start <= now < end
                and isinstance(title, str)
                and isinstance(tvg_id, str)
                and tvg_id
                and safe_regex.search(self._compiled_title_pattern, title) is not None
            ):
                active_matches.append((program, start))

        if not active_matches:
            return TaskResult(
                success=True,
                message="No active EPG titles matched",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        channels = await self._all_channels()
        epg_rows = await self._prober.client.get_epg_data(
            max_results=_MAX_EPG_DATA_ROWS + 1
        )
        if len(epg_rows) > _MAX_EPG_DATA_ROWS:
            raise RuntimeError(
                f"EPG data pagination exceeded the {_MAX_EPG_DATA_ROWS}-row safety limit"
            )
        epg_tvg_by_id = {
            row["id"]: row["tvg_id"]
            for row in epg_rows
            if isinstance(row.get("id"), int)
            and isinstance(row.get("tvg_id"), str)
        }
        channels_by_tvg: dict[str, list[dict]] = {}
        for channel in channels:
            epg_data_id = channel.get("epg_data_id")
            if epg_data_id is not None:
                tvg_id = epg_tvg_by_id.get(epg_data_id)
                if tvg_id is not None:
                    channels_by_tvg.setdefault(tvg_id, []).append(channel)
                continue
            tvg_id = channel.get("tvg_id")
            channel_uuid = channel.get("uuid")
            if isinstance(tvg_id, str):
                channels_by_tvg.setdefault(tvg_id, []).append(channel)
            if isinstance(channel_uuid, str) and channel_uuid != tvg_id:
                channels_by_tvg.setdefault(channel_uuid, []).append(channel)

        candidates: list[tuple[str, int, list[int]]] = []
        for program, start in active_matches:
            tvg_id = program["tvg_id"]
            for channel in channels_by_tvg.get(tvg_id, []):
                channel_id = channel.get("id")
                if not isinstance(channel_id, int):
                    continue
                event_identity = program.get("id", start.isoformat())
                trigger_key = (
                    f"{self._invocation_schedule_id or 'manual'}:"
                    f"{event_identity}:{start.isoformat()}:{tvg_id}:{channel_id}"
                )
                full_channel = await self._prober.client.get_channel(channel_id)
                candidates.append(
                    (trigger_key, channel_id, full_channel.get("streams", []))
                )

        trigger_keys: list[str] = []
        matched_channel_ids: list[int] = []
        stream_ids: list[int] = []
        stream_id_set: set[int] = set()
        for trigger_key, channel_id, channel_stream_ids in candidates:
            if not self._claim_trigger(trigger_key):
                continue
            matched_channel_ids.append(channel_id)
            trigger_keys.append(trigger_key)
            for stream_id in channel_stream_ids:
                if isinstance(stream_id, int) and stream_id not in stream_id_set:
                    stream_id_set.add(stream_id)
                    stream_ids.append(stream_id)

        if not trigger_keys:
            return TaskResult(
                success=True,
                message="Matching EPG events were already evaluated",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        if not stream_ids:
            return TaskResult(
                success=True,
                message="Matching EPG channels have no streams",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                details={
                    "trigger_keys": trigger_keys,
                    "matched_channels": matched_channel_ids,
                },
            )

        probe_result = await self._prober.probe_streams_by_ids(
            stream_ids,
            start_send_alerts=self._send_alerts,
            allow_reorder_after_probe=self._allow_reorder_after_probe,
        )
        if probe_result.get("status") == "already_running":
            self._release_claims(trigger_keys)
            return TaskResult(
                success=False,
                message="A stream probe is already in progress",
                error="ALREADY_RUNNING",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        failed_count = int(probe_result.get("failed", 0))
        success_count = int(probe_result.get("success", 0))
        total = int(probe_result.get("total", len(stream_ids)))
        succeeded = probe_result.get("status") == "completed"
        return TaskResult(
            success=succeeded,
            message=(
                f"EPG event probe processed {len(matched_channel_ids)} channel(s) "
                f"and {total} stream(s)"
            ),
            error=None if succeeded else probe_result.get("error", "PROBE_FAILED"),
            started_at=started_at,
            completed_at=datetime.utcnow(),
            total_items=total,
            success_count=success_count,
            failed_count=failed_count,
            details={
                "trigger_keys": trigger_keys,
                "matched_channels": matched_channel_ids,
            },
        )
