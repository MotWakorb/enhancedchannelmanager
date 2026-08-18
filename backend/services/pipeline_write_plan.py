"""Record and replay exact Dispatcharr writes for channel-pipeline plans."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from services.mutation_plan_store import canonical_hash


PIPELINE_WRITE_METHODS = frozenset({
    "assign_channel_numbers", "create_channel", "create_channel_group", "create_logo",
    "delete_channel", "delete_channel_group", "update_channel", "update_profile_channel",
})

# Every normal successful API run side effect suppressed during plan_only must
# be recreated after replay. This inventory is asserted by tests and reviewed
# alongside the external write chokepoint inventory above.
PIPELINE_INTERNAL_SIDE_EFFECTS = frozenset({
    "execution_record", "rollback_snapshot", "journal_entries",
    "event_review_candidates", "rule_statistics", "conflict_records",
})


@dataclass
class PlannedWrite:
    method: str
    args: list[Any]
    kwargs: dict[str, Any]


@dataclass
class PipelineWritePlan:
    writes: list[PlannedWrite] = field(default_factory=list)
    channel_preconditions: dict[str, dict[str, Any]] = field(default_factory=dict)
    group_preconditions: dict[str, dict[str, Any]] = field(default_factory=dict)
    profile_preconditions: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "writes": [vars(write) for write in self.writes],
            "channel_preconditions": self.channel_preconditions,
            "group_preconditions": self.group_preconditions,
            "profile_preconditions": self.profile_preconditions,
        }


class PartialReplayError(RuntimeError):
    """Upstream has no transaction; exposes exactly how far replay reached."""

    def __init__(self, failed_index: int, completed: list[str], compensation_errors: list[str]):
        super().__init__(f"pipeline replay failed at write {failed_index}")
        self.failed_index = failed_index
        self.completed = completed
        self.compensation_errors = compensation_errors


class PlanningDispatcharrClient:
    """Delegate reads while replacing every supported write with a recording."""

    def __init__(self, client) -> None:
        self._client = client
        self.plan = PipelineWritePlan()
        self._next_temp_id = -1
        self._shadow_channels: dict[int, dict[str, Any]] = {}

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    async def get_channel(self, channel_id: int) -> dict[str, Any]:
        if channel_id in self._shadow_channels:
            return copy.deepcopy(self._shadow_channels[channel_id])
        return await self._client.get_channel(channel_id)

    async def _channel_before(self, channel_id: int) -> dict[str, Any]:
        if channel_id in self._shadow_channels:
            return copy.deepcopy(self._shadow_channels[channel_id])
        channel = await self._client.get_channel(channel_id)
        relevant = {
            "id": channel_id,
            "name": channel.get("name"),
            "streams": list(channel.get("streams", []) or []),
            "channel_group_id": channel.get("channel_group_id"),
            "logo_id": channel.get("logo_id"),
            "tvg_id": channel.get("tvg_id"),
            "channel_number": channel.get("channel_number"),
            "stream_profile_id": channel.get("stream_profile_id"),
            "epg_data_id": channel.get("epg_data_id"),
        }
        self.plan.channel_preconditions.setdefault(str(channel_id), copy.deepcopy(relevant))
        self._shadow_channels[channel_id] = relevant
        return copy.deepcopy(relevant)

    def _record(self, method: str, *args, **kwargs) -> None:
        self.plan.writes.append(PlannedWrite(method, copy.deepcopy(list(args)), copy.deepcopy(kwargs)))

    async def create_channel(self, data: dict) -> dict:
        temp_id = self._next_temp_id
        self._next_temp_id -= 1
        created = {"id": temp_id, **copy.deepcopy(data)}
        self._shadow_channels[temp_id] = created
        self._record("create_channel", data)
        return copy.deepcopy(created)

    async def create_channel_group(self, name: str) -> dict:
        temp_id = self._next_temp_id
        self._next_temp_id -= 1
        self._record("create_channel_group", name)
        return {"id": temp_id, "name": name}

    async def create_logo(self, data: dict) -> dict:
        temp_id = self._next_temp_id
        self._next_temp_id -= 1
        self._record("create_logo", data)
        return {"id": temp_id, **copy.deepcopy(data)}

    async def update_channel(self, channel_id: int, data: dict) -> dict:
        current = await self._channel_before(channel_id)
        current.update(copy.deepcopy(data))
        self._shadow_channels[channel_id] = current
        self._record("update_channel", channel_id, data)
        return copy.deepcopy(current)

    async def delete_channel(self, channel_id: int) -> None:
        await self._channel_before(channel_id)
        self._record("delete_channel", channel_id)
        self._shadow_channels.pop(channel_id, None)

    async def delete_channel_group(self, group_id: int) -> None:
        groups = await self._client.get_channel_groups()
        group = next((item for item in groups if item.get("id") == group_id), None)
        if group is None:
            raise ValueError(f"channel group {group_id} not found during planning")
        self.plan.group_preconditions[str(group_id)] = {
            "id": group_id, "name": group.get("name")
        }
        self._record("delete_channel_group", group_id)

    async def assign_channel_numbers(self, channel_ids: list[int], starting_number=None) -> dict:
        for channel_id in channel_ids:
            await self._channel_before(channel_id)
        self._record("assign_channel_numbers", channel_ids, starting_number)
        return {"status": "planned"}

    async def update_profile_channel(self, profile_id: int, channel_id: int, data: dict) -> dict:
        await self._channel_before(channel_id)
        profiles = await self._client.get_channel_profiles()
        profile = next((item for item in profiles if item.get("id") == profile_id), None)
        if profile is None:
            raise ValueError(f"channel profile {profile_id} not found during planning")
        members = {
            item.get("id") if isinstance(item, dict) else item
            for item in (profile.get("channels") or [])
        }
        self.plan.profile_preconditions[f"{profile_id}:{channel_id}"] = channel_id in members
        self._record("update_profile_channel", profile_id, channel_id, data)
        return copy.deepcopy(data)


async def validate_read_set(client, plan: PipelineWritePlan) -> None:
    """Validate every existing channel before replay performs its first write."""
    for raw_id, expected in plan.channel_preconditions.items():
        current = await client.get_channel(int(raw_id))
        actual = {key: current.get(key) for key in expected if key != "id"}
        actual["id"] = int(raw_id)
        if canonical_hash(actual) != canonical_hash(expected):
            raise ValueError(f"channel {raw_id} drifted")
    if plan.group_preconditions:
        groups = {str(item.get("id")): item for item in await client.get_channel_groups()}
        for raw_id, expected in plan.group_preconditions.items():
            current = groups.get(raw_id)
            if current is None or current.get("name") != expected.get("name"):
                raise ValueError(f"channel group {raw_id} drifted")
    if plan.profile_preconditions:
        profiles = {item.get("id"): item for item in await client.get_channel_profiles()}
        for key, expected in plan.profile_preconditions.items():
            profile_id, channel_id = map(int, key.split(":"))
            profile = profiles.get(profile_id)
            members = {
                item.get("id") if isinstance(item, dict) else item
                for item in ((profile or {}).get("channels") or [])
            }
            if (channel_id in members) is not expected:
                raise ValueError(f"channel profile membership {key} drifted")


async def replay_write_plan(
    client, plan: PipelineWritePlan, *, read_set_validated: bool = False
) -> tuple[list[Any], dict[int, int]]:
    """Validate first, then replay only recorded writes with temp-ID remapping."""
    if not read_set_validated:
        await validate_read_set(client, plan)
    remap: dict[int, int] = {}
    results: list[Any] = []

    def mapped(value: Any) -> Any:
        if isinstance(value, int) and value < 0:
            if value not in remap:
                raise ValueError(f"unresolved temporary id {value}")
            return remap[value]
        if isinstance(value, list):
            return [mapped(item) for item in value]
        if isinstance(value, dict):
            return {key: mapped(item) for key, item in value.items()}
        return value

    next_temp = -1
    completed: list[tuple[PlannedWrite, list[Any], Any]] = []
    try:
        for write in plan.writes:
            args = mapped(write.args)
            kwargs = mapped(write.kwargs)
            result = await getattr(client, write.method)(*args, **kwargs)
            if write.method.startswith("create_"):
                real_id = result.get("id") if isinstance(result, dict) else None
                if real_id is None:
                    raise RuntimeError(f"{write.method} did not return an id")
                remap[next_temp] = int(real_id)
                next_temp -= 1
            results.append(result)
            completed.append((write, args, result))
    except Exception as exc:
        compensation_errors: list[str] = []
        # Best effort for reversible writes. Deletes and profile membership are
        # explicitly not recreated because upstream cannot preserve their IDs.
        for done, args, result in reversed(completed):
            try:
                if done.method == "create_channel":
                    await client.delete_channel(result["id"])
                elif done.method == "create_channel_group":
                    await client.delete_channel_group(result["id"])
                elif done.method == "update_channel" and args[0] > 0:
                    before = plan.channel_preconditions.get(str(args[0]))
                    if before:
                        await client.update_channel(args[0], {
                            key: value for key, value in before.items() if key != "id"
                        })
            except Exception as compensation_exc:  # noqa: BLE001
                compensation_errors.append(f"{done.method}: {compensation_exc}")
        raise PartialReplayError(
            len(completed), [item[0].method for item in completed], compensation_errors
        ) from exc
    return results, remap


def journal_entries_for_plan(
    plan: PipelineWritePlan, remap: dict[int, int], execution_id: int
) -> list[dict[str, Any]]:
    """Build non-secret audit entries, including surgical stream-merge rows."""
    entries: list[dict[str, Any]] = []
    for write in plan.writes:
        if write.method != "update_channel" or len(write.args) < 2:
            continue
        raw_id, payload = write.args[0], write.args[1]
        channel_id = remap.get(raw_id, raw_id)
        if raw_id < 0 or "streams" not in payload:
            continue
        before = plan.channel_preconditions.get(str(raw_id), {})
        old_streams = set(before.get("streams", []) or [])
        new_streams = set(payload.get("streams", []) or [])
        for stream_id in sorted(new_streams - old_streams):
            entries.append({
                "category": "auto_creation", "action_type": "merge_stream",
                "entity_id": channel_id, "entity_name": before.get("name"),
                "description": f"Planned pipeline attached stream {stream_id} to channel {channel_id}",
                "before_value": {"stream_ids": sorted(old_streams)},
                "after_value": {"stream_id": stream_id},
                "user_initiated": False, "mutation_source": "auto_creation",
                "batch_id": str(execution_id),
            })
    return entries
