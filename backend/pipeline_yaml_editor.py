"""Local-identity YAML editing, separate from portable rule import/export."""

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date

import yaml
from fastapi import HTTPException
from pydantic import ConfigDict, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import database
from channel_pipeline_schema import validate_event_sync_config, validate_rule
from models import ChannelPipelineRule

logger = logging.getLogger(__name__)
_MAX_BYTES = 2_000_000
_MAX_NODES = 100_000
_MAX_DEPTH = 40
_JSON_FIELDS = {
    "conditions", "actions", "normalization_group_ids", "required_provider_ids",
    "event_sync_config",
}


class _EditorLoader(yaml.SafeLoader):
    """Bound composition before construction; aliases cannot amplify the tree."""

    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent, index):
        self.nodes += 1
        self.depth += 1
        try:
            if self.check_event(yaml.AliasEvent):
                raise yaml.MarkedYAMLError(
                    problem="Aliases are not supported; copy the block instead",
                    problem_mark=self.peek_event().start_mark,
                )
            if self.depth > _MAX_DEPTH or self.nodes > _MAX_NODES:
                raise yaml.MarkedYAMLError(
                    problem="YAML structure exceeds the editor safety limit",
                    problem_mark=self.peek_event().start_mark,
                )
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                raise yaml.MarkedYAMLError(
                    problem="Mapping keys must be strings or integer group IDs",
                    problem_mark=key_node.start_mark,
                )
            if key in keys:
                raise yaml.MarkedYAMLError(
                    problem=f"Duplicate key: {key}", problem_mark=key_node.start_mark,
                )
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


@contextmanager
def _session():
    # Private DBAPI connection: generic StaticPool readers cannot roll back Save.
    engine = create_engine(database.get_database_url(), poolclass=NullPool)
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()


def _editable(rule):
    from routers.channel_pipeline import CreateChannelPipelineRuleRequest

    result = {"id": rule.id, "kind": "event_sync" if rule.is_event_sync() else "standard"}
    for field in CreateChannelPipelineRuleRequest.model_fields:
        value = getattr(rule, field)
        if field in _JSON_FIELDS:
            value = json.loads(value) if value is not None else (
                None if field == "event_sync_config" else []
            )
        elif isinstance(value, date):
            value = value.isoformat()
        result[field] = value
    return result


def _snapshot(rules):
    document = {"version": 1, "rules": [_editable(rule) for rule in rules]}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "yaml_content": yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        "revision": hashlib.sha256(encoded.encode()).hexdigest(),
    }


def _rules(session):
    return session.query(ChannelPipelineRule).order_by(ChannelPipelineRule.id).all()


def read_collection() -> dict[str, str]:
    with _session() as session:
        return _snapshot(_rules(session))


def _error(index, message, field=None):
    raise HTTPException(422, detail={
        "message": "Invalid rule configuration", "rule_index": index,
        "field": field, "errors": [message],
    })


def _parse(content):
    if len(content.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(422, detail={
            "message": "YAML exceeds the 2 MB editor limit", "line": 1, "column": 1,
        })
    try:
        document = yaml.load(content, Loader=_EditorLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise HTTPException(422, detail={
            "message": getattr(exc, "problem", None) or "Invalid YAML",
            "line": mark.line + 1 if mark else 1,
            "column": mark.column + 1 if mark else 1,
        }) from exc
    if (not isinstance(document, dict) or set(document) != {"version", "rules"}
            or type(document["version"]) is not int or document["version"] != 1
            or not isinstance(document["rules"], list)):
        raise HTTPException(422, detail="Expected version: 1 and a rules array, with no other document fields")
    return document["rules"]


def _validate_entries(entries):
    from routers.channel_pipeline import (
        CreateChannelPipelineRuleRequest, _lint_auto_creation_rule_request,
        _parse_yaml_active_date,
    )

    class EditorRule(CreateChannelPipelineRuleRequest):
        model_config = ConfigDict(strict=True, extra="forbid")

    validated = []
    ids = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _error(index, "Rule must be a mapping")
        data = dict(entry)
        rule_id = data.pop("id", None)
        if "id" in entry:
            if type(rule_id) is not int or not 0 < rule_id <= 9223372036854775807:
                _error(index, "id must be a positive local integer; omit it to create a rule", "id")
            if rule_id in ids:
                _error(index, "Duplicate supplied id", "id")
            ids.add(rule_id)
        kind = data.pop("kind", "event_sync" if data.get("event_sync_config") is not None else "standard")
        if kind not in ("standard", "event_sync"):
            _error(index, "Unknown rule kind", "kind")
        if (kind == "event_sync") != (data.get("event_sync_config") is not None):
            _error(index, "kind must agree with event_sync_config", "kind")
        try:
            for field in ("active_from", "active_until"):
                if field in data:
                    data[field] = _parse_yaml_active_date(data[field], field)
            request = EditorRule.model_validate(data)
            if not request.name.strip():
                _error(index, "name must not be blank", "name")
            for field in ("sort_order", "stream_sort_order", "quality_tie_break_order"):
                if getattr(request, field) not in ("asc", "desc"):
                    _error(index, "Expected asc or desc", field)
            if request.sort_field not in (None, "", "stream_name", "stream_name_natural", "group_name", "quality", "stream_name_regex", "provider_order", "channel_number"):
                _error(index, "Unknown channel sort field", "sort_field")
            if request.stream_sort_field not in (None, "", "smart_sort", "quality", "stream_name", "stream_name_natural", "provider_order"):
                _error(index, "Unknown stream sort field", "stream_sort_field")
            if request.orphan_action not in ("delete", "move_uncategorized", "delete_and_cleanup_groups", "none"):
                _error(index, "Unknown orphan action", "orphan_action")
            for field in ("conditions", "actions"):
                if any(not isinstance(item, dict) for item in getattr(request, field)):
                    _error(index, "Every entry must be a mapping", field)
            _lint_auto_creation_rule_request(request.conditions, request.actions, request.sort_regex)
            errors = validate_rule(request.conditions, request.actions)["errors"]
            if request.event_sync_config is not None:
                # Validator may canonicalize defaults; retain the submitted config
                # verbatim so an unrelated rename does not rewrite legacy scopes.
                config_copy = json.loads(json.dumps(request.event_sync_config, allow_nan=False))
                errors += validate_event_sync_config(config_copy)
            if errors:
                _error(index, errors)
            for field in _JSON_FIELDS:
                json.dumps(getattr(request, field), allow_nan=False)
            validated.append((rule_id, request.model_dump()))
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and "rule_index" in exc.detail:
                raise
            _error(index, exc.detail)
        except ValidationError as exc:
            _error(index, [
                {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
                for error in exc.errors(include_url=False, include_context=False)
            ])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            _error(index, str(exc))
    return validated


async def save_collection(content: str, revision: str, confirm_deletions: bool = False) -> dict[str, str]:
    from routers.channel_pipeline import get_client, _validate_normalization_group_ids

    entries = _validate_entries(_parse(content))
    # All HTTP is completed before reserving the SQLite writer.
    try:
        client = get_client()
        groups = {item["id"] for item in await client.get_channel_groups()}
        providers = {item["id"] for item in await client.get_m3u_accounts()}
        action_catalogs = {"group_id": groups}
        for field, method in (
            ("profile_id", "get_stream_profiles"),
            ("channel_profile_ids", "get_channel_profiles"),
            ("epg_id", "get_epg_data"),
        ):
            if any(field in action for _, data in entries for action in data["actions"]):
                action_catalogs[field] = {item["id"] for item in await getattr(client, method)()}
    except Exception as exc:
        raise HTTPException(503, detail="Reference catalogs unavailable; draft was not saved") from exc
    try:
        with _session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            current = _rules(session)
            if _snapshot(current)["revision"] != revision:
                raise HTTPException(409, detail={
                    "code": "stale_revision",
                    "message": "Rules changed since loading. Preserve your draft and reload; no changes were saved.",
                })
            by_id = {rule.id: rule for rule in current}
            submitted_ids = {rule_id for rule_id, _ in entries if rule_id is not None}
            for index, (rule_id, data) in enumerate(entries):
                if rule_id is not None and rule_id not in by_id:
                    _error(index, "Unknown supplied local id", "id")
                before = _editable(by_id[rule_id]) if rule_id is not None else {}
                for field, catalog in (("m3u_account_id", providers), ("target_group_id", groups), ("match_scope_group_id", groups)):
                    value = data[field]
                    if value != before.get(field) and value is not None:
                        if type(value) is not int or value not in catalog:
                            _error(index, "Reference does not exist", field)
                for field in ("required_provider_ids", "normalization_group_ids"):
                    if data[field] == before.get(field):
                        continue
                    if any(type(value) is not int or value <= 0 for value in data[field]):
                        _error(index, "References must be positive integers", field)
                    if field == "required_provider_ids":
                        if set(data[field]) - providers:
                            _error(index, "Provider reference does not exist", field)
                    else:
                        try:
                            _validate_normalization_group_ids(data[field], session)
                        except HTTPException as exc:
                            _error(index, exc.detail, field)
                if data["actions"] != before.get("actions"):
                    for action_index, action in enumerate(data["actions"]):
                        for field, catalog in action_catalogs.items():
                            value = action.get(field)
                            if value is None:
                                continue
                            values = value if isinstance(value, list) else [value]
                            if any(type(item) is not int or item not in catalog for item in values):
                                _error(index, "Reference does not exist", f"actions[{action_index}].{field}")
                config = data["event_sync_config"]
                if config is not None and config != before.get("event_sync_config"):
                    # The existing validator canonicalizes both legacy and
                    # provider-scoped group references on a private copy.
                    canonical = json.loads(json.dumps(config))
                    errors = validate_event_sync_config(canonical)
                    if errors:
                        _error(index, errors, "event_sync_config")
                    scopes = [canonical["master"], *canonical["secondary"]]
                    for scope in scopes:
                        if scope["group_id"] not in groups:
                            _error(index, "Event Sync group reference does not exist", "event_sync_config")
                        provider = scope.get("m3u_account_id")
                        if provider is not None and provider not in providers:
                            _error(index, "Event Sync provider reference does not exist", "event_sync_config")
                    promoted_group = canonical.get("promote_target_group_id")
                    if promoted_group is not None and promoted_group not in groups:
                        _error(index, "Promotion group reference does not exist", "event_sync_config.promote_target_group_id")
            deletions = [rule for rule in current if rule.id not in submitted_ids]
            if deletions and not confirm_deletions:
                raise HTTPException(409, detail={
                    "code": "deletion_confirmation_required",
                    "message": "Confirm deletion of omitted rules before saving",
                    "deletions": [{"id": rule.id, "name": rule.name} for rule in deletions],
                    "warnings": [
                        "Selected schedules may become stale and will not be repaired.",
                        "Local Event Sync reviews/exclusions are cleaned up by existing foreign keys.",
                        "Dispatcharr channels and execution history are not deleted.",
                    ],
                })
            # Allocate while the old maximum ID still exists, before removals.
            for rule_id, data in entries:
                rule = by_id[rule_id] if rule_id is not None else ChannelPipelineRule()
                before = _editable(rule) if rule_id is not None else {}
                for field, value in data.items():
                    if field in _JSON_FIELDS:
                        if rule_id is not None and before[field] == value:
                            continue
                        value = json.dumps(value, allow_nan=False) if value is not None else None
                    setattr(rule, field, value)
                if rule_id is None:
                    session.add(rule)
            session.flush()
            for rule in deletions:
                session.delete(rule)
            session.flush()
            result = _snapshot(_rules(session))
            session.commit()
            return result
    except HTTPException:
        raise
    except Exception as exc:
        if isinstance(exc, OperationalError) and getattr(exc.orig, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise HTTPException(409, detail={
                "code": "write_conflict",
                "message": "Another database writer is busy. Retry after it completes; no changes were saved.",
            }) from exc
        logger.exception("[PIPELINE-YAML] Collection save rolled back")
        raise HTTPException(500, detail="Unable to save rules; no changes were committed") from exc
