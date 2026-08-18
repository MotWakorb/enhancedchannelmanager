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
    "database_commit", "managed_channel_ledger", "stream_probe",
    "provider_refresh", "dummy_epg_refresh", "xmltv_cache",
    "notification", "live_data_refresh",
})


@dataclass
class PlannedWrite:
    method: str
    args: list[Any]
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class PlanningContext:
    """Explicit capability boundary for a mutation-free planning traversal.

    This is intentionally distinct from ``dry_run``: dry-run produces a user
    simulation and therefore skips writes, while planning executes normal
    decision logic against a shadow client so it can record the exact writes.
    Internal sinks must key off this context rather than inferring safety from
    the Dispatcharr client type.
    """

    enabled: bool = False

    @property
    def allow_internal_side_effects(self) -> bool:
        return not self.enabled


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

    def accounting(self) -> dict[str, int]:
        """Authoritative counts derived from exact operations, never previews."""
        targets: set[tuple[str, Any]] = set()
        for index, write in enumerate(self.writes):
            if write.method == "assign_channel_numbers":
                targets.update(("channel", value) for value in write.args[0])
            elif write.method == "update_profile_channel":
                targets.add(("channel", write.args[1]))
            elif write.method in {"update_channel", "delete_channel"}:
                targets.add(("channel", write.args[0]))
            elif write.method == "delete_channel_group":
                targets.add(("group", write.args[0]))
            else:
                # Each create is a distinct future entity even when payloads match.
                targets.add((write.method, index))
        return {"write_count": len(self.writes), "unique_target_count": len(targets)}


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
        completed_targets = [
            f"{item[0].method}:{item[1][0] if item[1] else '<no-arg>'}"
            for item in completed
        ]
        raise PartialReplayError(
            len(completed), completed_targets, compensation_errors
        ) from exc
    return results, remap


def journal_entries_for_plan(
    plan: PipelineWritePlan, remap: dict[int, int], execution_id: int
) -> list[dict[str, Any]]:
    """Build target-specific audit rows for every replayed mutation semantic."""
    entries: list[dict[str, Any]] = []
    def append(action: str, entity_id: Any, name: Any, before: Any, after: Any, description: str):
        entries.append({
            "category": "auto_creation", "action_type": action,
            "entity_id": entity_id, "entity_name": name,
            "description": description, "before_value": before,
            "after_value": after, "user_initiated": False,
            "mutation_source": "auto_creation", "batch_id": str(execution_id),
        })

    next_temp = -1
    for write in plan.writes:
        method = write.method
        if method.startswith("create_"):
            entity_id = remap.get(next_temp, next_temp)
            next_temp -= 1
            payload = write.args[0] if write.args else {}
            append(method, entity_id, payload.get("name") if isinstance(payload, dict) else str(payload),
                   None, payload, f"Planned pipeline executed {method} for {entity_id}")
            continue
        if method == "assign_channel_numbers":
            for channel_id in write.args[0]:
                append("assign_channel_number", remap.get(channel_id, channel_id), None, None,
                       {"starting_number": write.args[1]}, "Planned pipeline assigned channel number")
            continue
        if method == "update_profile_channel":
            profile_id, raw_id, payload = write.args
            channel_id = remap.get(raw_id, raw_id)
            append("assign_channel_profile", channel_id, None,
                   {"profile_id": profile_id}, payload,
                   f"Planned pipeline updated profile {profile_id} membership")
            continue
        raw_id = write.args[0] if write.args else None
        entity_id = remap.get(raw_id, raw_id)
        before = plan.channel_preconditions.get(str(raw_id), {})
        payload = write.args[1] if len(write.args) > 1 else None
        if method == "update_channel" and isinstance(payload, dict) and "streams" in payload:
            old_streams = set(before.get("streams", []) or [])
            new_streams = set(payload.get("streams", []) or [])
            for stream_id in sorted(new_streams - old_streams):
                append("merge_stream", entity_id, before.get("name"),
                       {"stream_ids": sorted(old_streams)}, {"stream_id": stream_id},
                       f"Planned pipeline attached stream {stream_id} to channel {entity_id}")
            for stream_id in sorted(old_streams - new_streams):
                append("remove_stream", entity_id, before.get("name"),
                       {"stream_id": stream_id}, {"stream_ids": sorted(new_streams)},
                       f"Planned pipeline removed stream {stream_id} from channel {entity_id}")
            remaining = {key: value for key, value in payload.items() if key != "streams"}
            if remaining:
                append(method, entity_id, before.get("name"), before, remaining,
                       f"Planned pipeline updated channel {entity_id}")
        else:
            append(method, entity_id, before.get("name"), before, payload,
                   f"Planned pipeline executed {method} for {entity_id}")
    return entries
