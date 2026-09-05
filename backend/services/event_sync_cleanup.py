"""Rule-owned, cross-account Event Sync detaches and their surgical inverse.

HTTP is outside SQLite transactions. List PATCH has no upstream CAS: a fresh
read narrows, but cannot eliminate, the race with external writers.
"""
from contextlib import contextmanager
from datetime import datetime
import json
import logging
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from database import get_database_url
from config import get_settings
from models import ChannelPipelineRule, JournalEntry
from services import event_sync_staleness
from services.event_sync_exclusion_store import load_exclusion_keys
from services.event_sync_preflight import (
    CHECK_GROUP_SETTINGS_FOUND, check_event_sync_group_settings, resolve_effective_master_group_id,
)
from services.event_sync_resolver import (
    SecondaryStream, build_master_name_to_id, resolve_event_sync,
)
from services.event_sync_review_store import load_review_decisions

logger = logging.getLogger(__name__)


class UncertainMutationError(RuntimeError):
    """PATCH started, but its outcome could not be confirmed."""


@contextmanager
def _session():
    # StaticPool readers may roll back a shared DBAPI connection. Keep these
    # durable intent transactions private (the mapping writer uses this pattern).
    engine = create_engine(get_database_url(), poolclass=NullPool)
    try:
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()


def valid_id(value):
    return type(value) is int and 0 < value <= 9223372036854775807


def _uuid(value):
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def native_guard(channel: dict, stream: dict) -> str | None:
    if channel.get("auto_created") is not True:
        return "native_auto_created_required"
    if not valid_id(channel.get("auto_created_by")):
        return "native_owner_unknown"
    if not valid_id(stream.get("m3u_account")):
        return "stream_account_unknown"
    if channel["auto_created_by"] == stream["m3u_account"]:
        return "same_account_protected"
    return None


def _ids(channel):
    raw = channel.get("streams")
    if not isinstance(raw, list):
        raise ValueError("channel memberships unavailable")
    ids = [s.get("id") if isinstance(s, dict) else s for s in raw]
    if any(not valid_id(s) for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("channel membership identity uncertain")
    return ids


def rule_identity(rule_id: int) -> dict:
    if not valid_id(rule_id):
        raise ValueError("rule identity unavailable")
    with _session() as session:
        rule = session.get(ChannelPipelineRule, rule_id)
        if rule is None or not rule.is_event_sync() or not isinstance(rule.created_at, datetime):
            raise ValueError("rule creation identity unavailable")
        return {"rule_id": rule_id, "rule_created_at": rule.created_at.isoformat()}


async def _pages(fetch, **kwargs):
    rows = []
    seen = set()
    for page in range(1, 101):
        response = await fetch(page=page, page_size=500, **kwargs)
        if isinstance(response, list):
            batch, more, count = response, False, len(response)
        elif isinstance(response, dict) and isinstance(response.get("results"), list):
            batch, more, count = response["results"], response.get("next"), response.get("count")
            if "next" not in response or type(count) is not int or count < 0:
                raise ValueError("incomplete page metadata")
        else:
            raise ValueError("malformed page")
        for row in batch:
            if not isinstance(row, dict) or not valid_id(row.get("id")) or row["id"] in seen:
                raise ValueError("malformed or duplicate page identity")
            seen.add(row["id"])
        rows.extend(batch)
        if len(rows) > 50000 or more and not batch:
            raise ValueError("truncated or stalled pagination")
        if not more:
            if count != len(rows):
                raise ValueError("incomplete page count")
            return rows
    raise ValueError("truncated pagination")


def _history(channel_id):
    # Existing idx_journal_entity narrows this read; no arbitrary 1000-row cap.
    with _session() as session:
        rows = session.query(JournalEntry).filter(
            JournalEntry.category == "event_sync", JournalEntry.entity_id == channel_id,
        ).order_by(JournalEntry.id).all()
        return [(r.id, r.action_type, json.loads(r.before_value or "{}"),
                 json.loads(r.after_value or "{}")) for r in rows]


async def plan_cleanup(client, rule_id: int, config: dict) -> dict:
    plan = {"decisions": [], "operations": [], "error": None}
    if config.get("detach_stale_streams") is not True:
        return plan
    try:
        identity = rule_identity(rule_id)
        # The attach scorer deliberately falls back on settings failure. A
        # destructive decision must instead load explicitly and fail closed.
        aliases = get_settings().event_sync_team_aliases
        if not isinstance(aliases, list) or any(
            not isinstance(group, dict) or not isinstance(group.get("terms"), list)
            or any(not isinstance(term, str) for term in group["terms"])
            for group in aliases
        ):
            raise ValueError("team alias settings unavailable")
        aliases = [group["terms"] for group in aliases]
        with _session() as session:
            decisions = load_review_decisions(session, rule_id)
            exclusions = load_exclusion_keys(session, rule_id)
        settings = await client.get_all_m3u_group_settings()
        if not isinstance(settings, dict):
            raise ValueError("group settings unavailable")
        preflight = await check_event_sync_group_settings(client, config, all_settings=settings)
        if any(failure["check"] == CHECK_GROUP_SETTINGS_FOUND for failure in preflight["failures"]):
            plan["error"] = "configured_group_settings_unavailable"
            return plan
        group = resolve_effective_master_group_id(settings, config["master_group_id"])
        channels = await _pages(client.get_channels, channel_group=group)
        if any(ch.get("channel_group_id") != group for ch in channels):
            raise ValueError("master group identity uncertain")
        scopes = config.get("secondary") or [
            {"group_id": gid, "m3u_account_id": None} for gid in config["secondary_group_ids"]
        ]
        if config.get("include_master_group_streams"):
            scopes = [*scopes, {"group_id": config["master_group_id"], "m3u_account_id": None}]
        accounts = await client.get_m3u_accounts()
        if not isinstance(accounts, list) or any(not valid_id(a.get("id")) for a in accounts):
            raise ValueError("accounts unavailable")
        account_ids = {account["id"] for account in accounts}
        stale = event_sync_staleness.previous_day_names(
            [a["id"] for a in accounts], event_sync_staleness.local_midnight_utc(),
        )
        streams = {}
        names = {}
        for scope in scopes:
            gid = scope["group_id"]
            names[gid] = await client._channel_group_name_for_id(gid)
            if not names[gid]:
                raise ValueError("configured group unavailable")
            rows = await _pages(client.get_streams, channel_group_name=names[gid],
                                m3u_account=scope.get("m3u_account_id"))
            for row in rows:
                if not isinstance(row.get("name"), str) or not row["name"]:
                    raise ValueError("stream name unavailable")
                if row.get("channel_group") != gid:
                    raise ValueError("stream group identity uncertain")
                if scope.get("m3u_account_id") is not None and row.get("m3u_account") != scope["m3u_account_id"]:
                    raise ValueError("stream scope identity uncertain")
                if row["id"] in streams and streams[row["id"]] != row:
                    raise ValueError("stream identity changed during pagination")
                streams[row["id"]] = row
        master_names = await build_master_name_to_id(
            channels, client, bool(config.get("parse_master_from_stream")),
        )
        for channel in channels:
            cid = channel["id"]
            history = _history(cid)
            pending = any(after.get("state") in {"intent", "uncertain"} for _, _, _, after in history)
            for sid in _ids(channel):
                row = {"channel_id": cid, "channel_name": channel.get("name"),
                       "stream_id": sid, "decision": "preserve", "reason": "ownership_unknown_or_expired"}
                plan["decisions"].append(row)
                fetched = await client.get_streams_by_ids([sid])
                if not isinstance(fetched, list) or len(fetched) != 1 or fetched[0].get("id") != sid:
                    row["reason"] = "stream_identity_unavailable"
                    continue
                stream = fetched[0]
                reason = native_guard(channel, stream)
                if reason:
                    row["reason"] = reason
                    continue
                if channel["auto_created_by"] not in account_ids or stream["m3u_account"] not in account_ids:
                    row["reason"] = "account_identity_unavailable"
                    continue
                uuid = _uuid(channel.get("uuid"))
                if not uuid:
                    row["reason"] = "channel_identity_unknown"
                    continue
                if pending:
                    row["reason"] = "uncertain_prior_mutation"
                    continue
                proof = None
                for _, action, before, after in history:
                    operation = after.get("operation", {})
                    if operation.get("stream_id") == sid:
                        if after.get("state") == "cancelled":
                            continue
                        proof = None
                        preimage = before.get("stream_ids") if isinstance(before, dict) else None
                        if (not isinstance(preimage, list) or any(not valid_id(s) for s in preimage)
                                or len(set(preimage)) != len(preimage)):
                            row["reason"] = "ownership_preimage_missing_or_invalid"
                            continue
                        if (action == "merge_stream" and after.get("state") == "confirmed"
                                and after.get("version") == 1 and sid not in preimage
                                and sid in after.get("stream_ids", [])):
                            proof = operation
                if (not proof or any(proof.get(k) != v for k, v in identity.items())
                        or proof.get("channel_uuid") != uuid
                        or proof.get("stream_account") != stream.get("m3u_account")):
                    continue
                gid = stream.get("channel_group")
                if not valid_id(gid) or not isinstance(stream.get("name"), str):
                    row["reason"] = "stream_identity_unknown"
                    continue
                secondary = SecondaryStream(
                    name=stream["name"], group_id=gid, stream_id=sid,
                    provider_id=stream["m3u_account"],
                    name_seen_before_today=event_sync_staleness.name_seen_before_today(
                        stale, stream["m3u_account"], names.get(gid), stream["name"],
                    ),
                )
                resolved = resolve_event_sync(config, sorted(master_names), [secondary],
                                              decisions=decisions, exclusions=exclusions, team_aliases=aliases)
                own_names = [name for name, mid in master_names.items() if mid == cid]
                if (not own_names or any(name in resolved.unparsed_master_names for name in own_names)
                        or len(resolved.resolved) != 1):
                    row["reason"] = "master_identity_or_matching_unavailable"
                    continue
                result = resolved.resolved[0]
                if result.disposition in {"ambiguous", "parse_failed"}:
                    row["reason"] = "matching_" + result.disposition
                    continue
                if result.disposition not in {"would_attach", "unmatched", "excluded_by_operator"}:
                    row["reason"] = "matching_unknown"
                    continue
                in_scope = any(gid == s["group_id"] and s.get("m3u_account_id") in
                               (None, stream["m3u_account"]) for s in scopes)
                if in_scope and (sid not in streams or any(
                    streams[sid].get(key) != stream.get(key) for key in ("name", "channel_group", "m3u_account")
                )):
                    row["reason"] = "stream_changed_or_missing_from_scope_read"
                    continue
                if (in_scope and result.best is not None
                        and master_names.get(result.best.master_name) == cid):
                    row["reason"] = "still_matches"
                    continue
                row.update(decision="would_detach", reason="no_longer_matches_current_rule")
                plan["operations"].append({**identity, "channel_id": cid, "channel_uuid": uuid,
                                           "stream_id": sid, "stream_account": stream["m3u_account"],
                                           "stream_group": gid,
                                           "action": "detach", "config": config,
                                           "channel_name": channel.get("name"), "stream_name": stream["name"],
                                           "owner_account": channel["auto_created_by"], "master_group": group,
                                           "master_identity_name": next(name for name, mid in master_names.items() if mid == cid)})
    except Exception as error:
        logger.warning("[EVENT-SYNC] Cleanup evaluation incomplete: %s", error)
        plan["operations"] = []
        plan["error"] = "incomplete_cleanup_evaluation"
        for row in plan["decisions"]:
            row.update(decision="preserve", reason="incomplete_cleanup_evaluation")
    return plan


def write_intent(operation: dict, batch_id: str, channel: dict, before: list, after: list) -> int:
    with _session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        if not str(batch_id).startswith("undo:"):
            from channel_pipeline_schema import validate_event_sync_config
            rule = session.get(ChannelPipelineRule, operation["rule_id"])
            current = rule.get_event_sync_config() if rule is not None else None
            expected = json.loads(json.dumps(operation["config"]))
            if (rule is None or not rule.enabled or current is None
                    or rule.created_at.isoformat() != operation["rule_created_at"]
                    or validate_event_sync_config(current) or validate_event_sync_config(expected)
                    or current != expected):
                raise ValueError("rule configuration or identity changed before intent")
        pending = session.query(JournalEntry).filter(
            JournalEntry.category == "event_sync", JournalEntry.entity_id == channel["id"],
        ).all()
        if any(json.loads(row.after_value or "{}").get("state") in {"intent", "uncertain"} for row in pending):
            raise RuntimeError("uncertain prior Event Sync mutation blocks further writes")
        entry = JournalEntry(
            category="event_sync", action_type="merge_stream" if operation["action"] == "attach" else "detach_stream",
            entity_id=channel["id"], entity_name=channel.get("name") or str(channel["id"]),
            description=f"Event Sync {operation['action']} stream {operation['stream_id']}: intent",
            before_value=json.dumps({"stream_ids": before}),
            after_value=json.dumps({"version": 1, "state": "intent", "operation": operation, "stream_ids": after}),
            batch_id=str(batch_id), user_initiated=False, mutation_source="auto_creation",
        )
        session.add(entry)
        session.commit()
        return entry.id


def _outcome(entry_id: int, state: str):
    with _session() as session:
        entry = session.get(JournalEntry, entry_id)
        if entry is None:
            raise RuntimeError("cleanup intent disappeared")
        data = json.loads(entry.after_value)
        data["state"] = state
        entry.after_value = json.dumps(data)
        entry.description = f"Event Sync {data['operation']['action']} stream {data['operation']['stream_id']}: {state}"
        session.commit()


async def apply_change(client, operation: dict, batch_id: str) -> dict:
    if not batch_id or any(not valid_id(operation.get(k)) for k in ("rule_id", "channel_id", "stream_id", "stream_account")):
        raise ValueError("mutation identity unavailable")
    if any(operation.get(k) != v for k, v in rule_identity(operation["rule_id"]).items()):
        raise ValueError("rule identity changed")
    if operation["action"] == "detach":
        plan = await plan_cleanup(client, operation["rule_id"], operation["config"])
        if operation not in plan["operations"]:
            raise ValueError("cleanup decision changed or incomplete")
    elif operation["action"] != "attach":
        raise ValueError("unknown Event Sync operation")
    channel = await client.get_channel(operation["channel_id"])
    if (not valid_id(channel.get("id")) or channel["id"] != operation["channel_id"]
            or _uuid(channel.get("uuid")) != operation.get("channel_uuid") or not _uuid(channel.get("uuid"))):
        raise ValueError("channel identity changed")
    streams = await client.get_streams_by_ids([operation["stream_id"]])
    if (not isinstance(streams, list) or len(streams) != 1 or not valid_id(streams[0].get("id"))
            or not valid_id(streams[0].get("m3u_account")) or streams[0].get("id") != operation["stream_id"]
            or streams[0].get("m3u_account") != operation["stream_account"]):
        raise ValueError("stream identity changed")
    if operation["action"] == "detach" and native_guard(channel, streams[0]):
        raise ValueError("native attachment protected")
    if operation["action"] == "detach" and (
        channel.get("name") != operation["channel_name"]
        or channel.get("auto_created_by") != operation["owner_account"]
        or channel.get("channel_group_id") != operation["master_group"]
        or streams[0].get("name") != operation["stream_name"]
        or streams[0].get("channel_group") != operation["stream_group"]
    ):
        raise ValueError("matching inputs changed immediately before mutation")
    if operation["action"] == "detach" and operation["config"].get("parse_master_from_stream"):
        current_identity = await build_master_name_to_id([channel], client, True)
        if current_identity.get(operation["master_identity_name"]) != channel["id"]:
            raise ValueError("master matching identity changed")
    before = _ids(channel)
    sid = operation["stream_id"]
    after = ([s for s in before if s != sid] if operation["action"] == "detach"
             else before if sid in before else [*before, sid])
    if before == after:
        return {"skipped": True, "channel": channel}
    entry_id = write_intent(operation, batch_id, channel, before, after)
    patch_started = False
    try:
        fresh = await client.get_channel(channel["id"])
        if any(fresh.get(key) != channel.get(key) for key in (
            "id", "uuid", "name", "channel_group_id", "auto_created", "auto_created_by",
        )):
            _outcome(entry_id, "cancelled")
            raise ValueError("channel identity changed after intent")
        fresh_streams = await client.get_streams_by_ids([sid])
        if (not isinstance(fresh_streams, list) or len(fresh_streams) != 1
                or not valid_id(fresh_streams[0].get("id")) or fresh_streams[0]["id"] != sid
                or not valid_id(fresh_streams[0].get("m3u_account"))
                or fresh_streams[0]["m3u_account"] != operation["stream_account"]):
            raise ValueError("stream identity changed after intent")
        if operation["action"] == "detach" and (
            native_guard(fresh, fresh_streams[0]) or fresh_streams[0].get("name") != operation["stream_name"]
            or fresh_streams[0].get("channel_group") != operation["stream_group"]
        ):
            raise ValueError("stream authority or matching identity changed after intent")
        if operation["action"] == "detach" and operation["config"].get("parse_master_from_stream"):
            fresh_identity = await build_master_name_to_id([fresh], client, True)
            if fresh_identity.get(operation["master_identity_name"]) != channel["id"]:
                raise ValueError("master matching identity changed after intent")
        before = _ids(fresh)
        after = ([s for s in before if s != sid] if operation["action"] == "detach"
                 else before if sid in before else [*before, sid])
        if before == after:
            _outcome(entry_id, "cancelled")
            return {"skipped": True, "channel": fresh, "before": before}
        with _session() as session:
            intent = session.get(JournalEntry, entry_id)
            if intent is None:
                raise RuntimeError("intent unavailable")
            data = json.loads(intent.after_value)
            data["stream_ids"] = after
            intent.before_value = json.dumps({"stream_ids": before})
            intent.after_value = json.dumps(data)
            session.commit()
        patch_started = True
        await client.update_channel(channel["id"], {"streams": after})
        actual = await client.get_channel(channel["id"])
        if _uuid(actual.get("uuid")) != operation["channel_uuid"] or _ids(actual) != after:
            raise RuntimeError("upstream outcome not confirmed")
        _outcome(entry_id, "confirmed")
    except Exception as error:
        # The already-committed intent remains blocking even if this update fails.
        try:
            _outcome(entry_id, "uncertain" if patch_started else "cancelled")
        except Exception:
            logger.exception("[EVENT-SYNC] Could not record uncertain intent outcome")
        if patch_started:
            raise UncertainMutationError("Event Sync mutation outcome uncertain; further cleanup blocked") from error
        raise RuntimeError("Event Sync mutation cancelled before PATCH; reevaluate cleanup") from error
    return {"skipped": False, "channel": actual, "journal_id": entry_id, "before": before}


async def rollback_batch(client, batch_id: str, expected_count: int | None = None) -> dict:
    try:
        with _session() as session:
            rows = session.query(JournalEntry).filter(
                JournalEntry.category == "event_sync", JournalEntry.batch_id == str(batch_id),
            ).order_by(JournalEntry.id.desc()).all()
            entries = [(r.id, json.loads(r.after_value or "{}")) for r in rows]
        entries = [(rid, data) for rid, data in entries if data.get("version") == 1]
    except Exception:
        logger.exception("[EVENT-SYNC] Recovery history unavailable")
        return {"success": False, "error": "cleanup_history_unavailable"}
    if expected_count is not None and len(entries) != expected_count:
        return {"success": False, "error": "cleanup_history_incomplete_or_expired"}
    if not entries:
        return {"success": False, "error": "cleanup_history_missing_or_expired"}
    touched = 0
    for rid, data in entries:
        if data.get("state") == "reverted":
            continue
        if data.get("state") != "confirmed":
            return {"success": False, "error": "uncertain_cleanup_outcome", "entities_restored": touched}
        try:
            op = data["operation"]
            if any(not valid_id(op.get(k)) for k in ("channel_id", "stream_id", "stream_account", "rule_id")):
                raise ValueError("malformed recovery identity")
            channel = await client.get_channel(op["channel_id"])
            if (not valid_id(channel.get("id")) or channel["id"] != op["channel_id"]
                    or not _uuid(channel.get("uuid")) or _uuid(channel.get("uuid")) != op["channel_uuid"]):
                raise ValueError("channel identity changed")
            streams = await client.get_streams_by_ids([op["stream_id"]])
            if (len(streams) != 1 or not valid_id(streams[0].get("id")) or not valid_id(streams[0].get("m3u_account"))
                    or streams[0].get("id") != op["stream_id"] or streams[0].get("m3u_account") != op["stream_account"]):
                raise ValueError("stream identity unavailable")
            before = _ids(channel)
            sid = op["stream_id"]
            if op["action"] == "attach":
                if native_guard(channel, streams[0]):
                    raise ValueError("native attachment protected")
                after = [s for s in before if s != sid]
            elif op["action"] == "detach":
                after = before if sid in before else [*before, sid]
            else:
                raise ValueError("unknown recovery action")
            # Persist inverse intent in a separate batch so retry never inverts
            # an inverse. Uncertain inverse blocks retry, rather than guessing.
            undo = {**op, "action": "detach" if op["action"] == "attach" else "attach"}
            undo_id = write_intent(undo, f"undo:{batch_id}:{rid}", channel, before, after)
            _outcome(rid, "uncertain")
            if after != before:
                await client.update_channel(channel["id"], {"streams": after})
                actual = await client.get_channel(channel["id"])
                if _uuid(actual.get("uuid")) != op["channel_uuid"] or _ids(actual) != after:
                    raise ValueError("inverse outcome uncertain")
                touched += 1
            _outcome(undo_id, "reverted")
            _outcome(rid, "reverted")
        except Exception:
            logger.exception("[EVENT-SYNC] Surgical cleanup rollback failed")
            return {"success": False, "error": "cleanup_rollback_failed", "entities_restored": touched}
    return {"success": True, "surgical_unmerge": True, "entities_removed": 0, "entities_restored": touched}
