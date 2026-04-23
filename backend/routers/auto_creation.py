"""
Auto-creation router — auto-creation pipeline CRUD, execution, import/export, schema.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import io
import json
import logging
import tarfile
import time
from datetime import datetime
from typing import List, Optional

import journal
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from concurrency import run_cpu_bound
from database import get_session
from dispatcharr_client import get_client
from regex_lint import (
    lint_actions_json,
    lint_conditions_json,
    lint_pattern,
    violations_to_http_detail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auto-creation", tags=["Auto-Creation"])


# =============================================================================
# Pydantic models
# =============================================================================


class CreateAutoCreationRuleRequest(BaseModel):
    """Request to create an auto-creation rule."""
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    m3u_account_id: Optional[int] = None
    target_group_id: Optional[int] = None
    conditions: list
    actions: list
    run_on_refresh: bool = False
    stop_on_first_match: bool = True
    sort_field: Optional[str] = None
    sort_order: str = "asc"
    probe_on_sort: bool = False
    sort_regex: Optional[str] = None
    stream_sort_field: Optional[str] = None
    stream_sort_order: str = "asc"
    normalization_group_ids: list[int] = []
    skip_struck_streams: bool = False
    orphan_action: str = "delete"
    match_scope_target_group: bool = False


class UpdateAutoCreationRuleRequest(BaseModel):
    """Request to update an auto-creation rule."""
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    m3u_account_id: Optional[int] = None
    target_group_id: Optional[int] = None
    conditions: Optional[list] = None
    actions: Optional[list] = None
    run_on_refresh: Optional[bool] = None
    stop_on_first_match: Optional[bool] = None
    sort_field: Optional[str] = None
    sort_order: Optional[str] = None
    probe_on_sort: Optional[bool] = None
    sort_regex: Optional[str] = None
    stream_sort_field: Optional[str] = None
    stream_sort_order: Optional[str] = None
    normalization_group_ids: Optional[list[int]] = None
    skip_struck_streams: Optional[bool] = None
    orphan_action: Optional[str] = None
    match_scope_target_group: Optional[bool] = None


class BulkUpdateAutoCreationRulesRequest(UpdateAutoCreationRuleRequest):
    """Bulk-update multiple rules. Only include fields to change (omit others)."""

    rule_ids: List[int] = Field(..., min_length=1, max_length=500)
    merge_streams_remove_non_matching: Optional[bool] = None


class RunPipelineRequest(BaseModel):
    """Request to run the auto-creation pipeline."""
    dry_run: bool = False
    m3u_account_ids: Optional[List[int]] = None
    rule_ids: Optional[List[int]] = None


class ImportYAMLRequest(BaseModel):
    """Request to import rules from YAML."""
    yaml_content: str
    overwrite: bool = False


def _apply_merge_streams_remove_non_matching(actions: list, value: bool) -> list:
    """Set remove_non_matching on every merge_streams action (stored as flat keys on the action dict)."""
    out = []
    for a in actions:
        if not isinstance(a, dict):
            out.append(a)
            continue
        if a.get("type") != "merge_streams":
            out.append(a)
            continue
        out.append({**a, "remove_non_matching": bool(value)})
    return out


def _apply_rule_scalar_updates(rule, request: UpdateAutoCreationRuleRequest) -> None:
    """Apply scalar columns from an update request (excluding conditions/actions body)."""
    if request.name is not None:
        rule.name = request.name
    if request.description is not None:
        rule.description = request.description
    if request.enabled is not None:
        rule.enabled = request.enabled
    if request.priority is not None:
        rule.priority = request.priority
    if request.m3u_account_id is not None:
        rule.m3u_account_id = request.m3u_account_id
    if request.target_group_id is not None:
        rule.target_group_id = request.target_group_id
    if request.run_on_refresh is not None:
        rule.run_on_refresh = request.run_on_refresh
    if request.stop_on_first_match is not None:
        rule.stop_on_first_match = request.stop_on_first_match
    if request.sort_field is not None:
        rule.sort_field = request.sort_field or None
    if request.sort_order is not None:
        rule.sort_order = request.sort_order
    if request.probe_on_sort is not None:
        rule.probe_on_sort = request.probe_on_sort
    if request.sort_regex is not None:
        rule.sort_regex = request.sort_regex or None
    if request.stream_sort_field is not None:
        rule.stream_sort_field = request.stream_sort_field or None
    if request.stream_sort_order is not None:
        rule.stream_sort_order = request.stream_sort_order
    if request.normalization_group_ids is not None:
        rule.set_normalization_group_ids(request.normalization_group_ids)
    if request.skip_struck_streams is not None:
        rule.skip_struck_streams = request.skip_struck_streams
    if request.orphan_action is not None:
        rule.orphan_action = request.orphan_action
    if getattr(request, "match_scope_target_group", None) is not None:
        rule.match_scope_target_group = request.match_scope_target_group


def _resolve_normalization_group_ids(rule_data: dict, session) -> str | None:
    """Resolve normalization_group_ids from rule data, with backward compat for normalize_names."""
    norm_ids = rule_data.get("normalization_group_ids")
    if norm_ids is not None:
        return json.dumps(norm_ids) if norm_ids else None
    # Legacy: normalize_names=true -> all enabled groups
    if rule_data.get("normalize_names"):
        from models import NormalizationRuleGroup
        groups = session.query(NormalizationRuleGroup.id).filter(
            NormalizationRuleGroup.enabled == True
        ).order_by(NormalizationRuleGroup.priority).all()
        return json.dumps([g.id for g in groups]) if groups else None
    return None


# =============================================================================
# Rule CRUD Endpoints
# =============================================================================


@router.get("/rules")
async def get_auto_creation_rules():
    """Get all auto-creation rules sorted by priority."""
    logger.debug("[AUTO-CREATE] GET /rules")
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rules = session.query(AutoCreationRule).order_by(
                AutoCreationRule.priority
            ).all()
            logger.debug("[AUTO-CREATE] Returning %s rules to UI", len(rules))
            for r in rules:
                actions = r.get_actions()
                action_summary = ", ".join(f"{a.get('type', '?')}" for a in actions)
                logger.debug("[AUTO-CREATE]   Rule id=%s '%s': actions=[%s], raw_actions=%s", r.id, r.name, action_summary, r.actions)
            return {"rules": [r.to_dict() for r in rules]}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to get auto-creation rules: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/rules/{rule_id}")
async def get_auto_creation_rule(rule_id: int):
    """Get a specific auto-creation rule by ID."""
    logger.debug("[AUTO-CREATE] GET /rules/%s", rule_id)
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to get auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


def _lint_auto_creation_rule_request(
    conditions: Optional[list], actions: Optional[list], sort_regex: Optional[str]
) -> None:
    """Raise HTTP 422 if any pattern field fails the regex linter (bd-eio04.7).

    Auto-creation rules have regex-bearing fields scattered across:
      - ``sort_regex`` (top-level rule column)
      - Condition ``value`` for regex-flavored condition types
        (``stream_name_matches``, ``stream_group_matches``,
        ``tvg_id_matches``, ``channel_exists_matching``)
      - Action ``pattern`` for ``set_variable`` in ``regex_extract`` /
        ``regex_replace`` modes
      - Action ``name_transform_pattern`` on ``create_channel`` /
        ``create_group``
    """
    violations = []
    violations.extend(lint_pattern(sort_regex, field="sort_regex"))
    if conditions:
        violations.extend(lint_conditions_json(conditions, prefix="conditions"))
    if actions:
        violations.extend(lint_actions_json(actions, prefix="actions"))
    if violations:
        logger.warning(
            "[AUTO-CREATE] Rejected rule — %d lint violation(s): %s",
            len(violations),
            [(v.field, v.code) for v in violations],
        )
        raise HTTPException(
            status_code=422, detail=violations_to_http_detail(violations)
        )


@router.post("/rules")
async def create_auto_creation_rule(request: CreateAutoCreationRuleRequest):
    """Create a new auto-creation rule."""
    try:
        from models import AutoCreationRule
        from auto_creation_schema import validate_rule

        # Validate conditions and actions
        logger.debug("[AUTO-CREATE] Creating rule '%s' with %s actions", request.name, len(request.actions))
        for j, action in enumerate(request.actions):
            logger.debug("[AUTO-CREATE]   Action %s: %s", j, action)
        # Lint regex patterns (bd-eio04.7) BEFORE schema validation so users
        # see the specific pattern error rather than a generic schema message.
        _lint_auto_creation_rule_request(
            request.conditions, request.actions, request.sort_regex
        )
        validation = validate_rule(request.conditions, request.actions)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail={
                "message": "Invalid rule configuration",
                "errors": validation["errors"]
            })

        session = get_session()
        try:
            # Auto-assign priority: if requested priority already taken, append at end
            existing_priorities = [r.priority for r in session.query(AutoCreationRule).all()]
            if existing_priorities and request.priority in existing_priorities:
                priority = max(existing_priorities) + 1
            else:
                priority = request.priority

            rule = AutoCreationRule(
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=priority,
                m3u_account_id=request.m3u_account_id,
                target_group_id=request.target_group_id,
                conditions=json.dumps(request.conditions),
                actions=json.dumps(request.actions),
                run_on_refresh=request.run_on_refresh,
                stop_on_first_match=request.stop_on_first_match,
                sort_field=request.sort_field,
                sort_order=request.sort_order,
                probe_on_sort=request.probe_on_sort,
                sort_regex=request.sort_regex,
                stream_sort_field=request.stream_sort_field,
                stream_sort_order=request.stream_sort_order,
                normalization_group_ids=json.dumps(request.normalization_group_ids) if request.normalization_group_ids else None,
                skip_struck_streams=request.skip_struck_streams,
                orphan_action=request.orphan_action,
                match_scope_target_group=request.match_scope_target_group
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)

            # Log to journal
            journal.log_entry(
                category="auto_creation",
                action_type="create",
                entity_id=rule.id,
                entity_name=rule.name,
                description=f"Created auto-creation rule '{rule.name}'"
            )

            logger.info("[AUTO-CREATE] Created rule id=%s name='%s'", rule.id, rule.name)
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to create auto-creation rule: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/rules/{rule_id}")
async def update_auto_creation_rule(rule_id: int, request: UpdateAutoCreationRuleRequest):
    """Update an auto-creation rule."""
    try:
        from models import AutoCreationRule
        from auto_creation_schema import validate_rule

        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            _apply_rule_scalar_updates(rule, request)

            # Validate and update conditions/actions if provided
            conditions = request.conditions if request.conditions is not None else rule.get_conditions()
            actions = request.actions if request.actions is not None else rule.get_actions()

            logger.debug("[AUTO-CREATE] Updating rule id=%s '%s' with %s actions", rule_id, rule.name, len(actions))
            for j, action in enumerate(actions):
                logger.debug("[AUTO-CREATE]   Action %s: %s", j, action)

            # Lint regex patterns (bd-eio04.7). Only fields actually
            # supplied on the PUT are linted — an operator renaming a rule
            # shouldn't hit a 422 for a pattern they didn't edit. Pre-lint
            # rows are surfaced separately by the startup scan.
            _lint_auto_creation_rule_request(
                request.conditions if request.conditions is not None else None,
                request.actions if request.actions is not None else None,
                request.sort_regex if request.sort_regex is not None else None,
            )

            validation = validate_rule(conditions, actions)
            if not validation["valid"]:
                raise HTTPException(status_code=400, detail={
                    "message": "Invalid rule configuration",
                    "errors": validation["errors"]
                })

            if request.conditions is not None:
                rule.conditions = json.dumps(request.conditions)
            if request.actions is not None:
                rule.actions = json.dumps(request.actions)

            session.commit()
            session.refresh(rule)

            # Log to journal
            journal.log_entry(
                category="auto_creation",
                action_type="update",
                entity_id=rule.id,
                entity_name=rule.name,
                description=f"Updated auto-creation rule '{rule.name}'"
            )

            logger.info("[AUTO-CREATE] Updated rule id=%s name='%s'", rule.id, rule.name)
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to update auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/bulk-update")
async def bulk_update_auto_creation_rules(request: BulkUpdateAutoCreationRulesRequest):
    """Apply the same field changes to many rules. Omitted fields are left unchanged."""
    from models import AutoCreationRule
    from auto_creation_schema import validate_rule

    payload = request.model_dump(exclude_unset=True)
    rule_ids = payload.pop("rule_ids", None) or []
    merge_streams_remove_non_matching = payload.pop("merge_streams_remove_non_matching", None)

    if not rule_ids:
        raise HTTPException(status_code=400, detail="rule_ids is required")
    if len(rule_ids) != len(set(rule_ids)):
        raise HTTPException(status_code=400, detail="duplicate rule_ids")
    if not payload and merge_streams_remove_non_matching is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    scalar_update = UpdateAutoCreationRuleRequest(**payload) if payload else UpdateAutoCreationRuleRequest()

    # Lint sort_regex (bd-eio04.7) before any DB work. Bulk-update does not
    # accept conditions/actions, so only sort_regex can carry a pattern.
    _lint_auto_creation_rule_request(None, None, scalar_update.sort_regex)

    session = get_session()
    try:
        updated: list = []
        for rid in rule_ids:
            rule = session.query(AutoCreationRule).filter(AutoCreationRule.id == rid).first()
            if not rule:
                raise HTTPException(status_code=404, detail=f"Rule not found: {rid}")

            if payload:
                _apply_rule_scalar_updates(rule, scalar_update)

            if merge_streams_remove_non_matching is not None:
                actions = rule.get_actions()
                new_actions = _apply_merge_streams_remove_non_matching(
                    actions, merge_streams_remove_non_matching
                )
                rule.actions = json.dumps(new_actions)

            conditions = rule.get_conditions()
            actions = rule.get_actions()
            validation = validate_rule(conditions, actions)
            if not validation["valid"]:
                raise HTTPException(status_code=400, detail={
                    "message": f"Invalid rule configuration for rule id={rid}",
                    "errors": validation["errors"],
                })
            updated.append(rule)

        session.commit()
        for rule in updated:
            session.refresh(rule)

        names = ", ".join(r.name for r in updated[:5])
        if len(updated) > 5:
            names += ", …"
        journal.log_entry(
            category="auto_creation",
            action_type="update",
            entity_id=0,
            entity_name="bulk",
            description=f"Bulk-updated {len(updated)} auto-creation rule(s): {names}",
        )

        return {"rules": [r.to_dict() for r in updated], "updated_count": len(updated)}
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.exception("[AUTO-CREATE] Bulk update failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.delete("/rules/{rule_id}")
async def delete_auto_creation_rule(rule_id: int):
    """Delete an auto-creation rule."""
    logger.debug("[AUTO-CREATE] DELETE /rules/%s", rule_id)
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            rule_name = rule.name
            session.delete(rule)
            session.commit()

            # Log to journal
            journal.log_entry(
                category="auto_creation",
                action_type="delete",
                entity_id=rule_id,
                entity_name=rule_name,
                description=f"Deleted auto-creation rule '{rule_name}'"
            )

            logger.info("[AUTO-CREATE] Deleted rule id=%s name='%s'", rule_id, rule_name)
            return {"status": "deleted", "id": rule_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to delete auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/reorder")
async def reorder_auto_creation_rules(rule_ids: List[int] = Body(...)):
    """Reorder auto-creation rules by setting priorities based on array order."""
    logger.debug("[AUTO-CREATE] POST /rules/reorder - %d rules", len(rule_ids))
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            for priority, rule_id in enumerate(rule_ids):
                rule = session.query(AutoCreationRule).filter(
                    AutoCreationRule.id == rule_id
                ).first()
                if rule:
                    rule.priority = priority
            session.commit()
            return {"status": "reordered", "rule_ids": rule_ids}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to reorder auto-creation rules: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/{rule_id}/toggle")
async def toggle_auto_creation_rule(rule_id: int):
    """Toggle the enabled state of an auto-creation rule."""
    logger.debug("[AUTO-CREATE] POST /rules/%s/toggle", rule_id)
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            rule.enabled = not rule.enabled
            session.commit()
            session.refresh(rule)

            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to toggle auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/{rule_id}/duplicate")
async def duplicate_auto_creation_rule(rule_id: int):
    """Duplicate an auto-creation rule."""
    logger.debug("[AUTO-CREATE] POST /rules/%s/duplicate", rule_id)
    try:
        from models import AutoCreationRule
        session = get_session()
        try:
            rule = session.query(AutoCreationRule).filter(
                AutoCreationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            # Create a copy with a new name
            new_rule = AutoCreationRule(
                name=f"{rule.name} (Copy)",
                description=rule.description,
                enabled=False,  # Disabled by default
                priority=rule.priority + 1,
                m3u_account_id=rule.m3u_account_id,
                target_group_id=rule.target_group_id,
                conditions=rule.conditions,
                actions=rule.actions,
                run_on_refresh=rule.run_on_refresh,
                stop_on_first_match=rule.stop_on_first_match,
                sort_field=rule.sort_field,
                sort_order=rule.sort_order,
                stream_sort_field=rule.stream_sort_field,
                stream_sort_order=rule.stream_sort_order,
                normalization_group_ids=rule.normalization_group_ids,
                skip_struck_streams=rule.skip_struck_streams,
                probe_on_sort=rule.probe_on_sort,
                sort_regex=rule.sort_regex,
                orphan_action=rule.orphan_action,
                match_scope_target_group=rule.match_scope_target_group
            )
            session.add(new_rule)
            session.commit()
            session.refresh(new_rule)

            return new_rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to duplicate auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Pipeline Execution Endpoints
# =============================================================================


@router.post("/run")
async def run_auto_creation_pipeline(request: RunPipelineRequest):
    """Run the auto-creation pipeline."""
    logger.debug("[AUTO-CREATE] POST /run - dry_run=%s", request.dry_run)
    try:
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        # Get or initialize engine
        engine = get_auto_creation_engine()
        if not engine:
            client = get_client()
            engine = await init_auto_creation_engine(client)

        result = await engine.run_pipeline(
            dry_run=request.dry_run,
            triggered_by="api",
            m3u_account_ids=request.m3u_account_ids,
            rule_ids=request.rule_ids
        )

        return result
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to run auto-creation pipeline: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/{rule_id}/run")
async def run_auto_creation_rule(rule_id: int, dry_run: bool = False):
    """Run a specific auto-creation rule."""
    logger.debug("[AUTO-CREATE] POST /rules/%s/run - dry_run=%s", rule_id, dry_run)
    try:
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        # Get or initialize engine
        engine = get_auto_creation_engine()
        if not engine:
            client = get_client()
            engine = await init_auto_creation_engine(client)

        result = await engine.run_rule(
            rule_id=rule_id,
            dry_run=dry_run,
            triggered_by="api"
        )

        return result
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to run auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/executions")
async def get_auto_creation_executions(
    limit: int = 50,
    offset: int = 0,
    rule_id: Optional[int] = None,
    status: Optional[str] = None
):
    """Get auto-creation execution history."""
    logger.debug("[AUTO-CREATE] GET /executions - limit=%s offset=%s rule_id=%s status=%s", limit, offset, rule_id, status)
    try:
        from models import AutoCreationExecution
        session = get_session()
        try:
            query = session.query(AutoCreationExecution)

            if rule_id is not None:
                query = query.filter(AutoCreationExecution.rule_id == rule_id)
            if status is not None:
                query = query.filter(AutoCreationExecution.status == status)

            total = query.count()
            executions = query.order_by(
                AutoCreationExecution.started_at.desc()
            ).offset(offset).limit(limit).all()

            return {
                "executions": [e.to_dict() for e in executions],
                "total": total,
                "limit": limit,
                "offset": offset
            }
        finally:
            session.close()
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to get auto-creation executions: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/executions/{execution_id}")
async def get_auto_creation_execution(execution_id: int, include_entities: bool = False, include_log: bool = False):
    """Get details of a specific execution."""
    logger.debug("[AUTO-CREATE] GET /executions/%s", execution_id)
    try:
        from models import AutoCreationExecution, AutoCreationConflict
        session = get_session()
        try:
            execution = session.query(AutoCreationExecution).filter(
                AutoCreationExecution.id == execution_id
            ).first()
            if not execution:
                raise HTTPException(status_code=404, detail="Execution not found")

            result = execution.to_dict(include_entities=include_entities, include_log=include_log)

            # Include conflicts
            conflicts = session.query(AutoCreationConflict).filter(
                AutoCreationConflict.execution_id == execution_id
            ).all()
            result["conflicts"] = [c.to_dict() for c in conflicts]

            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to get auto-creation execution %s: %s", execution_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/executions/{execution_id}/rollback")
async def rollback_auto_creation_execution(execution_id: int):
    """Rollback an auto-creation execution."""
    logger.debug("[AUTO-CREATE] POST /executions/%s/rollback", execution_id)
    try:
        from auto_creation_engine import get_auto_creation_engine, init_auto_creation_engine

        # Get or initialize engine
        engine = get_auto_creation_engine()
        if not engine:
            client = get_client()
            engine = await init_auto_creation_engine(client)

        result = await engine.rollback_execution(execution_id, rolled_back_by="api")

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "Rollback failed"))

        # Log to journal
        rule_name = result.get("rule_name", f"Execution {execution_id}")
        removed = result.get("entities_removed", 0)
        restored = result.get("entities_restored", 0)
        session = get_session()
        try:
            journal.log_entry(
                category="auto_creation",
                action_type="rollback",
                entity_id=execution_id,
                entity_name=rule_name,
                description=f"Rolled back '{rule_name}': removed {removed} channel(s), restored {restored} entit{'y' if restored == 1 else 'ies'}"
            )
        finally:
            session.close()

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to rollback auto-creation execution %s: %s", execution_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# YAML Import/Export Endpoints
# =============================================================================


@router.get("/export/yaml")
async def export_auto_creation_rules_yaml():
    """Export all auto-creation rules as YAML.

    Includes portable name fields (group_name, target_group_name, m3u_account_name)
    alongside numeric IDs so rules can be shared between ECM instances.
    """
    logger.debug("[AUTO-CREATE-YAML] GET /export/yaml")
    try:
        import yaml
        from models import AutoCreationRule
        session = get_session()
        try:
            rules = session.query(AutoCreationRule).order_by(
                AutoCreationRule.priority
            ).all()

            # Build id→name lookup maps for portable export
            client = get_client()
            group_id_to_name = {}
            m3u_id_to_name = {}
            try:
                start = time.time()
                groups = await client.get_channel_groups()
                elapsed_ms = (time.time() - start) * 1000
                logger.debug("[AUTO-CREATE-YAML] Fetched channel groups in %.1fms", elapsed_ms)
                group_id_to_name = {g["id"]: g["name"] for g in groups}
            except Exception as e:
                logger.warning("[AUTO-CREATE-YAML] Could not fetch channel groups for YAML export: %s", e)
            try:
                start = time.time()
                m3u_accounts = await client.get_m3u_accounts()
                elapsed_ms = (time.time() - start) * 1000
                logger.debug("[AUTO-CREATE-YAML] Fetched M3U accounts in %.1fms", elapsed_ms)
                m3u_id_to_name = {a["id"]: a["name"] for a in m3u_accounts}
            except Exception as e:
                logger.warning("[AUTO-CREATE-YAML] Could not fetch M3U accounts for YAML export: %s", e)

            export_data = {
                "version": 1,
                "exported_at": datetime.utcnow().isoformat() + "Z",
                "rules": []
            }

            for rule in rules:
                rule_dict = {
                    "name": rule.name,
                    "description": rule.description,
                    "enabled": rule.enabled,
                    "priority": rule.priority,
                    "m3u_account_id": rule.m3u_account_id,
                    "m3u_account_name": m3u_id_to_name.get(rule.m3u_account_id),
                    "target_group_id": rule.target_group_id,
                    "target_group_name": group_id_to_name.get(rule.target_group_id),
                    "conditions": rule.get_conditions(),
                    "actions": rule.get_actions(),
                    "run_on_refresh": rule.run_on_refresh,
                    "stop_on_first_match": rule.stop_on_first_match,
                    "sort_field": rule.sort_field,
                    "sort_order": rule.sort_order or "asc",
                    "sort_regex": rule.sort_regex,
                    "stream_sort_field": rule.stream_sort_field,
                    "stream_sort_order": rule.stream_sort_order or "asc",
                    "normalization_group_ids": rule.get_normalization_group_ids(),
                    "skip_struck_streams": rule.skip_struck_streams or False,
                    "probe_on_sort": rule.probe_on_sort or False,
                    "orphan_action": rule.orphan_action or "delete",
                    "match_scope_target_group": rule.match_scope_target_group or False
                }

                # Add group_name to actions that have group_id
                for action in rule_dict["actions"]:
                    gid = action.get("group_id")
                    if gid is not None and gid in group_id_to_name:
                        action["group_name"] = group_id_to_name[gid]

                export_data["rules"].append(rule_dict)

            yaml_content = yaml.dump(export_data, default_flow_style=False, sort_keys=False)

            return PlainTextResponse(
                content=yaml_content,
                media_type="text/yaml",
                headers={
                    "Content-Disposition": "attachment; filename=auto-creation-rules.yaml"
                }
            )
        finally:
            session.close()
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to export auto-creation rules: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/import/yaml")
async def import_auto_creation_rules_yaml(request: ImportYAMLRequest):
    """Import auto-creation rules from YAML.

    Supports portable name fields: if group_name/target_group_name/m3u_account_name
    are present and corresponding IDs are missing, names are resolved to local IDs.
    Explicit IDs always take priority over names.
    """
    logger.debug("[AUTO-CREATE-YAML] POST /import/yaml - overwrite=%s", request.overwrite)
    try:
        import yaml
        from models import AutoCreationRule
        from auto_creation_schema import validate_rule

        # Parse YAML
        try:
            data = yaml.safe_load(request.yaml_content)
            logger.debug("[AUTO-CREATE-YAML] Parsed YAML with %s rules", len(data.get('rules', data) if isinstance(data, dict) else data))
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

        # Accept both {"rules": [...]} and a bare list of rules
        if isinstance(data, list):
            data = {"rules": data}

        if not data or "rules" not in data:
            raise HTTPException(status_code=400, detail="YAML must contain a 'rules' array or be a list of rules")

        # Build name→id lookup maps for portable import
        client = get_client()
        group_name_to_id = {}
        m3u_name_to_id = {}
        try:
            start = time.time()
            groups = await client.get_channel_groups()
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[AUTO-CREATE-YAML] Fetched channel groups in %.1fms", elapsed_ms)
            group_name_to_id = {g["name"].lower(): g["id"] for g in groups}
        except Exception as e:
            logger.warning("[AUTO-CREATE-YAML] Could not fetch channel groups for YAML import: %s", e)
        try:
            start = time.time()
            m3u_accounts = await client.get_m3u_accounts()
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[AUTO-CREATE-YAML] Fetched M3U accounts in %.1fms", elapsed_ms)
            m3u_name_to_id = {a["name"].lower(): a["id"] for a in m3u_accounts}
        except Exception as e:
            logger.warning("[AUTO-CREATE-YAML] Could not fetch M3U accounts for YAML import: %s", e)

        session = get_session()
        try:
            imported = []
            errors = []
            warnings = []

            for i, rule_data in enumerate(data["rules"]):
                rule_name = rule_data.get('name', f'Rule {i}')
                logger.debug("[AUTO-CREATE-YAML] Processing rule %s: '%s'", i, rule_name)
                for j, action in enumerate(rule_data.get("actions", [])):
                    logger.debug("[AUTO-CREATE-YAML]   Action %s from YAML: type=%s, params={%s}", j, action.get('type'), ', '.join('%s=%s' % (k, v) for k, v in action.items() if k != 'type'))
                for j, cond in enumerate(rule_data.get("conditions", [])):
                    logger.debug("[AUTO-CREATE-YAML]   Condition %s from YAML: type=%s, value=%s, connector=%s", j, cond.get('type'), cond.get('value'), cond.get('connector'))
                # Resolve portable name fields to local IDs
                # target_group_name → target_group_id
                if not rule_data.get("target_group_id") and rule_data.get("target_group_name"):
                    name = rule_data["target_group_name"]
                    resolved_id = group_name_to_id.get(name.lower())
                    if resolved_id:
                        rule_data["target_group_id"] = resolved_id
                    else:
                        warnings.append(f"Rule '{rule_data.get('name', f'Rule {i}')}': target_group_name '{name}' not found locally")

                # m3u_account_name → m3u_account_id
                if not rule_data.get("m3u_account_id") and rule_data.get("m3u_account_name"):
                    name = rule_data["m3u_account_name"]
                    resolved_id = m3u_name_to_id.get(name.lower())
                    if resolved_id:
                        rule_data["m3u_account_id"] = resolved_id
                    else:
                        warnings.append(f"Rule '{rule_data.get('name', f'Rule {i}')}': m3u_account_name '{name}' not found locally")

                # Resolve group_name → group_id in actions
                for action in rule_data.get("actions", []):
                    if not action.get("group_id") and action.get("group_name"):
                        name = action["group_name"]
                        resolved_id = group_name_to_id.get(name.lower())
                        if resolved_id:
                            action["group_id"] = resolved_id
                        else:
                            warnings.append(f"Rule '{rule_data.get('name', f'Rule {i}')}': action group_name '{name}' not found locally")
                    # Strip transient name fields from stored data
                    action.pop("group_name", None)

                # Strip transient name fields from rule-level data
                rule_data.pop("target_group_name", None)
                rule_data.pop("m3u_account_name", None)
                # Validate rule
                conditions = rule_data.get("conditions", [])
                actions = rule_data.get("actions", [])
                logger.debug("[AUTO-CREATE-YAML] Rule '%s': validating %s conditions, %s actions", rule_name, len(conditions), len(actions))
                for j, action in enumerate(actions):
                    logger.debug("[AUTO-CREATE-YAML]   Action %s pre-validate: type=%s, all_keys=%s", j, action.get('type'), list(action.keys()))
                validation = validate_rule(conditions, actions)

                if not validation["valid"]:
                    errors.append({
                        "rule_index": i,
                        "rule_name": rule_data.get("name", f"Rule {i}"),
                        "errors": validation["errors"]
                    })
                    continue

                # Check if rule with same name exists
                existing = session.query(AutoCreationRule).filter(
                    AutoCreationRule.name == rule_data.get("name")
                ).first()

                if existing:
                    if request.overwrite:
                        # Update existing rule
                        existing.description = rule_data.get("description")
                        existing.enabled = rule_data.get("enabled", True)
                        existing.priority = rule_data.get("priority", 0)
                        existing.m3u_account_id = rule_data.get("m3u_account_id")
                        existing.target_group_id = rule_data.get("target_group_id")
                        existing.conditions = json.dumps(conditions)
                        existing.actions = json.dumps(actions)
                        existing.run_on_refresh = rule_data.get("run_on_refresh", False)
                        existing.stop_on_first_match = rule_data.get("stop_on_first_match", True)
                        existing.sort_field = rule_data.get("sort_field")
                        existing.sort_order = rule_data.get("sort_order", "asc")
                        existing.sort_regex = rule_data.get("sort_regex")
                        existing.stream_sort_field = rule_data.get("stream_sort_field")
                        existing.stream_sort_order = rule_data.get("stream_sort_order", "asc")
                        existing.normalization_group_ids = _resolve_normalization_group_ids(rule_data, session)
                        existing.skip_struck_streams = rule_data.get("skip_struck_streams", False)
                        existing.probe_on_sort = rule_data.get("probe_on_sort", False)
                        existing.orphan_action = rule_data.get("orphan_action", "delete")
                        existing.match_scope_target_group = rule_data.get("match_scope_target_group", False)
                        logger.debug("[AUTO-CREATE-YAML] Rule '%s': updated existing (id=%s), stored actions=%s", rule_name, existing.id, existing.actions)
                        imported.append({"name": existing.name, "action": "updated"})
                    else:
                        errors.append({
                            "rule_index": i,
                            "rule_name": rule_data.get("name"),
                            "errors": ["Rule with this name already exists"]
                        })
                        continue
                else:
                    # Create new rule
                    rule = AutoCreationRule(
                        name=rule_data.get("name", f"Imported Rule {i}"),
                        description=rule_data.get("description"),
                        enabled=rule_data.get("enabled", True),
                        priority=rule_data.get("priority", 0),
                        m3u_account_id=rule_data.get("m3u_account_id"),
                        target_group_id=rule_data.get("target_group_id"),
                        conditions=json.dumps(conditions),
                        actions=json.dumps(actions),
                        run_on_refresh=rule_data.get("run_on_refresh", False),
                        stop_on_first_match=rule_data.get("stop_on_first_match", True),
                        sort_field=rule_data.get("sort_field"),
                        sort_order=rule_data.get("sort_order", "asc"),
                        sort_regex=rule_data.get("sort_regex"),
                        stream_sort_field=rule_data.get("stream_sort_field"),
                        stream_sort_order=rule_data.get("stream_sort_order", "asc"),
                        normalization_group_ids=_resolve_normalization_group_ids(rule_data, session),
                        skip_struck_streams=rule_data.get("skip_struck_streams", False),
                        probe_on_sort=rule_data.get("probe_on_sort", False),
                        orphan_action=rule_data.get("orphan_action", "delete"),
                        match_scope_target_group=rule_data.get("match_scope_target_group", False)
                    )
                    session.add(rule)
                    logger.debug("[AUTO-CREATE-YAML] Rule '%s': created new, stored actions=%s", rule_name, rule.actions)
                    imported.append({"name": rule.name, "action": "created"})

            session.commit()

            # De-duplicate priorities: if any rules share the same priority,
            # re-assign sequential priorities preserving relative order (by id)
            all_rules = session.query(AutoCreationRule).order_by(
                AutoCreationRule.priority, AutoCreationRule.id
            ).all()
            priorities = [r.priority for r in all_rules]
            if len(priorities) != len(set(priorities)):
                logger.info("[AUTO-CREATE-YAML] Duplicate priorities detected after import, re-assigning sequentially")
                for idx, rule in enumerate(all_rules):
                    rule.priority = idx
                session.commit()

            # Log to journal
            if imported:
                journal.log_entry(
                    category="auto_creation",
                    action_type="import",
                    entity_id=None,
                    entity_name="YAML Import",
                    description=f"Imported {len(imported)} auto-creation rules from YAML"
                )

            result = {
                "success": True,
                "imported": imported,
                "errors": errors
            }
            if warnings:
                result["warnings"] = warnings
            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to import auto-creation rules: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Validation & Schema Endpoints
# =============================================================================


@router.post("/validate")
async def validate_auto_creation_rule(
    conditions: list = Body(...),
    actions: list = Body(...)
):
    """Validate conditions and actions without creating a rule."""
    logger.debug("[AUTO-CREATE] POST /validate")
    try:
        from auto_creation_schema import validate_rule
        # Offload regex compile/validation off event loop (bd-w3z4h)
        result = await run_cpu_bound(validate_rule, conditions, actions)
        return result
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to validate auto-creation rule: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/schema/conditions")
async def get_auto_creation_condition_schema():
    """Get the schema for available condition types."""
    from auto_creation_schema import ConditionType

    conditions = []
    for ct in list(ConditionType):
        condition_info = {
            "type": ct.value,
            "category": "logical" if ct.value in ("and", "or", "not") else
                        "special" if ct.value in ("always", "never") else
                        "channel" if ct.value.startswith("channel_") or ct.value in ("has_channel", "normalized_name_in_group") else
                        "stream"
        }

        # Add value type hints
        if ct.value in ("stream_name_matches", "stream_name_contains", "stream_group_matches",
                        "tvg_id_matches", "channel_exists_with_name", "channel_exists_matching"):
            condition_info["value_type"] = "string"
            condition_info["description"] = f"Pattern to match"
        elif ct.value in ("quality_min", "quality_max"):
            condition_info["value_type"] = "integer"
            condition_info["description"] = "Resolution height (e.g., 720, 1080)"
        elif ct.value in ("tvg_id_exists", "logo_exists", "has_channel"):
            condition_info["value_type"] = "boolean"
            condition_info["description"] = "Whether the property exists"
        elif ct.value == "provider_is":
            condition_info["value_type"] = "integer|array"
            condition_info["description"] = "M3U account ID(s)"
        elif ct.value == "codec_is":
            condition_info["value_type"] = "string|array"
            condition_info["description"] = "Video codec (e.g., h264, hevc)"
        elif ct.value == "channel_in_group":
            condition_info["value_type"] = "integer"
            condition_info["description"] = "Channel group ID"
        elif ct.value == "normalized_name_in_group":
            condition_info["value_type"] = "integer"
            condition_info["description"] = "Group ID — matches if normalized stream name equals a channel name in this group"
        elif ct.value == "normalized_name_not_in_group":
            condition_info["value_type"] = "integer"
            condition_info["description"] = "Group ID — matches if normalized stream name does NOT equal any channel name in this group"
        elif ct.value == "normalized_name_exists":
            condition_info["value_type"] = "none"
            condition_info["description"] = "Matches if normalized stream name equals a channel name in ANY group"
        elif ct.value == "normalized_name_not_exists":
            condition_info["value_type"] = "none"
            condition_info["description"] = "Matches if normalized stream name does NOT equal any channel name in any group"
        elif ct.value in ("and", "or"):
            condition_info["value_type"] = "array"
            condition_info["description"] = "Array of sub-conditions"
        elif ct.value == "not":
            condition_info["value_type"] = "array"
            condition_info["description"] = "Single condition to negate"

        conditions.append(condition_info)

    return {"conditions": conditions}


@router.get("/schema/actions")
async def get_auto_creation_action_schema():
    """Get the schema for available action types."""
    from auto_creation_schema import ActionType

    actions = [
        {
            "type": ActionType.CREATE_CHANNEL.value,
            "description": "Create a new channel",
            "params": {
                "name_template": {"type": "string", "default": "{stream_name}", "description": "Template for channel name"},
                "channel_number": {"type": "string|integer", "default": "auto", "description": "'auto', specific number, or 'min-max' range"},
                "group_id": {"type": "integer", "optional": True, "description": "Target channel group ID"},
                "if_exists": {"type": "string", "enum": ["skip", "merge", "update"], "default": "skip", "description": "Behavior if channel exists"}
            }
        },
        {
            "type": ActionType.CREATE_GROUP.value,
            "description": "Create a new channel group",
            "params": {
                "name_template": {"type": "string", "default": "{stream_group}", "description": "Template for group name"},
                "if_exists": {"type": "string", "enum": ["skip", "use_existing"], "default": "use_existing", "description": "Behavior if group exists"}
            }
        },
        {
            "type": ActionType.MERGE_STREAMS.value,
            "description": "Merge multiple streams into one channel",
            "params": {
                "target": {"type": "string", "enum": ["new_channel", "existing_channel", "auto"], "default": "auto"},
                "match_by": {"type": "string", "enum": ["tvg_id", "normalized_name", "stream_group"], "default": "tvg_id"},
                "find_channel_by": {"type": "string", "enum": ["name_exact", "name_regex", "tvg_id"], "optional": True},
                "find_channel_value": {"type": "string", "optional": True},
                "quality_preference": {"type": "array", "default": [1080, 720, 480], "description": "Quality order preference"},
                "max_streams": {"type": "integer", "default": 5}
            }
        },
        {
            "type": ActionType.ASSIGN_LOGO.value,
            "description": "Assign a logo to the channel",
            "params": {
                "value": {"type": "string", "description": "'from_stream' or URL"}
            }
        },
        {
            "type": ActionType.ASSIGN_TVG_ID.value,
            "description": "Assign a TVG ID (EPG ID) to the channel",
            "params": {
                "value": {"type": "string", "description": "'from_stream' or specific value"}
            }
        },
        {
            "type": ActionType.ASSIGN_EPG.value,
            "description": "Assign an EPG source to the channel",
            "params": {
                "epg_id": {"type": "integer", "description": "EPG source ID"}
            }
        },
        {
            "type": ActionType.ASSIGN_PROFILE.value,
            "description": "Assign a stream profile to the channel",
            "params": {
                "profile_id": {"type": "integer", "description": "Stream profile ID"}
            }
        },
        {
            "type": ActionType.SET_CHANNEL_NUMBER.value,
            "description": "Set the channel number",
            "params": {
                "value": {"type": "string|integer", "description": "'auto', specific number, or 'min-max' range"}
            }
        },
        {
            "type": ActionType.SKIP.value,
            "description": "Skip this stream (don't create channel)"
        },
        {
            "type": ActionType.STOP_PROCESSING.value,
            "description": "Stop processing further rules for this stream"
        },
        {
            "type": ActionType.LOG_MATCH.value,
            "description": "Log a debug message",
            "params": {
                "message": {"type": "string", "description": "Message to log (supports templates)"}
            }
        }
    ]

    return {"actions": actions}


@router.get("/schema/template-variables")
async def get_auto_creation_template_variables():
    """Get available template variables for name templates."""
    from auto_creation_schema import TemplateVariables

    return {
        "variables": [
            {"name": "{stream_name}", "description": "Original stream name"},
            {"name": "{stream_group}", "description": "Stream's group name from M3U"},
            {"name": "{tvg_id}", "description": "Stream's EPG ID"},
            {"name": "{tvg_name}", "description": "Stream's EPG name"},
            {"name": "{quality}", "description": "Resolution as string (e.g., '1080p')"},
            {"name": "{quality_raw}", "description": "Resolution as number (e.g., 1080)"},
            {"name": "{provider}", "description": "M3U account name"},
            {"name": "{provider_id}", "description": "M3U account ID"},
            {"name": "{normalized_name}", "description": "Name after normalization rules"}
        ]
    }


# =============================================================================
# Debug Bundle
# =============================================================================


def _add_tar_entry(tf: tarfile.TarFile, name: str, data: str):
    """Add a text file to a tar archive."""
    encoded = data.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(encoded)
    info.mtime = time.time()
    tf.addfile(info, io.BytesIO(encoded))


@router.get("/debug-bundle")
async def generate_debug_bundle():
    """Generate a diagnostic tar.gz bundle for troubleshooting auto-creation.

    Contains obfuscated channel data, rules, and logs safe for sharing.
    """
    logger.info("[AUTO-CREATE] Generating debug bundle")
    start = time.time()
    client = get_client()

    from csv_handler import generate_csv
    from log_utils import get_recent_logs
    from models import AutoCreationRule
    from obfuscate import obfuscate_text, obfuscate_url
    from routers.backup import APP_VERSION

    try:
        # -- 1. Fetch channels and groups from Dispatcharr ----------------
        all_channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=100)
            channels = result.get("results", [])
            all_channels.extend(channels)
            if not result.get("next"):
                break
            page += 1

        groups = await client.get_channel_groups() or []
        group_lookup = {g.get("id"): g.get("name", "") for g in groups}

        # -- 2. channels.json — channels with streams and stats -----------
        # Collect all stream IDs across channels
        all_stream_ids = set()
        for ch in all_channels:
            all_stream_ids.update(ch.get("streams", []))

        # Fetch stream details in batches (for names and M3U info)
        stream_detail_lookup = {}  # stream_id -> {name, m3u_account_id, url}
        stream_ids_list = list(all_stream_ids)
        for i in range(0, len(stream_ids_list), 100):
            batch = stream_ids_list[i:i + 100]
            if batch:
                try:
                    streams = await client.get_streams_by_ids(batch)
                    for s in streams:
                        m3u_acct = s.get("m3u_account")
                        if isinstance(m3u_acct, dict):
                            m3u_id = m3u_acct.get("id")
                        else:
                            m3u_id = m3u_acct
                        stream_detail_lookup[s.get("id")] = {
                            "name": s.get("name", ""),
                            "m3u_account_id": m3u_id,
                            "url": obfuscate_url(s.get("url", "")) if s.get("url") else "",
                        }
                except Exception as e:
                    logger.warning("[AUTO-CREATE] Debug bundle: failed to fetch stream batch: %s", e)

        # Load stream stats from DB
        from models import StreamStats
        stats_session = get_session()
        try:
            stats_records = stats_session.query(StreamStats).filter(
                StreamStats.stream_id.in_(stream_ids_list)
            ).all() if stream_ids_list else []
            stream_stats_lookup = {s.stream_id: s for s in stats_records}
        finally:
            stats_session.close()

        # Build channels with embedded stream info, sorted by channel_number
        channels_json_data = []
        for ch in sorted(all_channels, key=lambda c: c.get("channel_number", 0) or 0):
            stream_ids = ch.get("streams", [])
            streams_data = []
            for position, sid in enumerate(stream_ids, start=1):
                detail = stream_detail_lookup.get(sid, {})
                stat = stream_stats_lookup.get(sid)
                stream_entry = {
                    "id": sid,
                    "position": position,
                    "name": detail.get("name", ""),
                    "m3u_account_id": detail.get("m3u_account_id"),
                    "url": detail.get("url", ""),
                }
                if stat:
                    stream_entry["stats"] = {
                        "probe_status": stat.probe_status,
                        "resolution": stat.resolution,
                        "fps": stat.fps,
                        "video_codec": stat.video_codec,
                        "audio_codec": stat.audio_codec,
                        "audio_channels": stat.audio_channels,
                        "bitrate": stat.bitrate,
                        "video_bitrate": stat.video_bitrate,
                        "is_black_screen": stat.is_black_screen or False,
                        "is_low_fps": stat.is_low_fps or False,
                        "consecutive_failures": stat.consecutive_failures or 0,
                        "last_probed": stat.last_probed.isoformat() + "Z" if stat.last_probed else None,
                    }
                streams_data.append(stream_entry)

            channels_json_data.append({
                "id": ch.get("id"),
                "name": ch.get("name", ""),
                "channel_number": ch.get("channel_number"),
                "channel_group_name": group_lookup.get(ch.get("channel_group_id"), ""),
                "stream_count": len(stream_ids),
                "streams": streams_data,
            })
        channels_json_str = json.dumps(channels_json_data, indent=2)

        # -- 3. channels.csv — full export with obfuscated URLs -----------
        csv_channels = []
        for ch in sorted(all_channels, key=lambda c: c.get("channel_number", 0) or 0):
            stream_ids = ch.get("streams", [])
            stream_urls = [
                stream_detail_lookup.get(sid, {}).get("url", "")
                for sid in stream_ids
                if stream_detail_lookup.get(sid, {}).get("url")
            ]
            csv_channels.append({
                "channel_number": ch.get("channel_number"),
                "name": ch.get("name", ""),
                "group_name": group_lookup.get(ch.get("channel_group_id"), ""),
                "tvg_id": ch.get("tvg_id", ""),
                "gracenote_id": ch.get("tvc_guide_stationid", ""),
                "logo_url": "",
                "stream_urls": ";".join(stream_urls),
            })
        csv_content = generate_csv(csv_channels)

        # -- 4. rules.yaml — reuse export logic --------------------------
        import yaml
        session = get_session()
        try:
            rules = session.query(AutoCreationRule).order_by(
                AutoCreationRule.priority
            ).all()

            m3u_id_to_name = {}
            try:
                m3u_accounts = await client.get_m3u_accounts()
                m3u_id_to_name = {a["id"]: a["name"] for a in m3u_accounts}
            except Exception:
                pass

            export_rules = {
                "version": 1,
                "exported_at": datetime.utcnow().isoformat() + "Z",
                "rules": [],
            }
            for rule in rules:
                rule_dict = {
                    "name": rule.name,
                    "description": rule.description,
                    "enabled": rule.enabled,
                    "priority": rule.priority,
                    "m3u_account_id": rule.m3u_account_id,
                    "m3u_account_name": m3u_id_to_name.get(rule.m3u_account_id),
                    "target_group_id": rule.target_group_id,
                    "target_group_name": group_lookup.get(rule.target_group_id),
                    "conditions": rule.get_conditions(),
                    "actions": rule.get_actions(),
                    "run_on_refresh": rule.run_on_refresh,
                    "stop_on_first_match": rule.stop_on_first_match,
                    "sort_field": rule.sort_field,
                    "sort_order": rule.sort_order or "asc",
                    "sort_regex": rule.sort_regex,
                    "stream_sort_field": rule.stream_sort_field,
                    "stream_sort_order": rule.stream_sort_order or "asc",
                    "normalization_group_ids": rule.get_normalization_group_ids(),
                    "skip_struck_streams": rule.skip_struck_streams or False,
                    "probe_on_sort": rule.probe_on_sort or False,
                    "orphan_action": rule.orphan_action or "delete",
                }
                for action in rule_dict["actions"]:
                    gid = action.get("group_id")
                    if gid is not None and gid in group_lookup:
                        action["group_name"] = group_lookup[gid]
                export_rules["rules"].append(rule_dict)

            rule_count = len(rules)
            yaml_content = yaml.dump(export_rules, default_flow_style=False, sort_keys=False)
        finally:
            session.close()

        # -- 5. settings.json — user settings with secrets redacted -------
        from config import get_settings as get_config_settings
        settings_obj = get_config_settings()
        settings_dict = settings_obj.model_dump()
        # Redact sensitive fields
        _REDACTED = "***REDACTED***"
        for key in ("password", "smtp_password", "discord_webhook_url",
                     "telegram_bot_token", "telegram_chat_id", "mcp_api_key"):
            if settings_dict.get(key):
                settings_dict[key] = _REDACTED
        # Redact Dispatcharr URL credentials (keep host/port for debugging)
        if settings_dict.get("url"):
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(settings_dict["url"])
            if parsed.username or parsed.password:
                clean = parsed._replace(netloc=f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname)
                settings_dict["url"] = urlunparse(clean)
        if settings_dict.get("username"):
            settings_dict["username"] = _REDACTED
        settings_json_str = json.dumps(settings_dict, indent=2)

        # -- 6. task_schedules.json — scheduled task configuration --------
        from models import TaskSchedule
        sched_session = get_session()
        try:
            schedules = sched_session.query(TaskSchedule).order_by(
                TaskSchedule.task_id, TaskSchedule.id
            ).all()
            schedules_data = [s.to_dict() for s in schedules]
        finally:
            sched_session.close()
        task_schedules_str = json.dumps(schedules_data, indent=2)

        # -- 7. channel_groups_diagnostic.json — Channel Manager mismatch diagnosis
        # Run BEFORE logs.txt is captured so [GROUPS-DIAG] lines land in the log dump too.
        from routers.channel_groups import build_channel_groups_diagnostic
        try:
            cg_diagnostic = build_channel_groups_diagnostic(groups, all_channels)
            cg_diagnostic_str = json.dumps(cg_diagnostic, indent=2, default=str)
        except Exception as e:
            logger.warning("[AUTO-CREATE] Debug bundle: channel groups diagnostic failed: %s", e)
            cg_diagnostic_str = json.dumps({"error": str(e)})

        # -- 8. logs.txt — recent logs, obfuscated -----------------------
        log_lines = get_recent_logs()
        obfuscated_lines = [obfuscate_text(line) for line in log_lines]
        logs_text = "\n".join(obfuscated_lines)

        # -- 8. manifest.json --------------------------------------------
        # Compute stream stats summary
        total_streams = len(all_stream_ids)
        probed_success = sum(1 for s in stream_stats_lookup.values() if s.probe_status == "success")
        probed_failed = sum(1 for s in stream_stats_lookup.values() if s.probe_status in ("failed", "timeout"))
        black_screen_count = sum(1 for s in stream_stats_lookup.values() if s.is_black_screen)
        low_fps_count = sum(1 for s in stream_stats_lookup.values() if s.is_low_fps)

        manifest = {
            "ecm_version": APP_VERSION,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "channel_count": len(all_channels),
            "rule_count": rule_count,
            "group_count": len(groups),
            "stream_count": total_streams,
            "stream_stats": {
                "probed_success": probed_success,
                "probed_failed": probed_failed,
                "unprobed": total_streams - probed_success - probed_failed,
                "black_screen": black_screen_count,
                "low_fps": low_fps_count,
            },
        }
        manifest_str = json.dumps(manifest, indent=2)

        # -- 9. Pack into tar.gz -----------------------------------------
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            _add_tar_entry(tf, "channels.json", channels_json_str)
            _add_tar_entry(tf, "channels.csv", csv_content)
            _add_tar_entry(tf, "rules.yaml", yaml_content)
            _add_tar_entry(tf, "settings.json", settings_json_str)
            _add_tar_entry(tf, "task_schedules.json", task_schedules_str)
            _add_tar_entry(tf, "channel_groups_diagnostic.json", cg_diagnostic_str)
            _add_tar_entry(tf, "logs.txt", logs_text)
            _add_tar_entry(tf, "manifest.json", manifest_str)
        buf.seek(0)

        elapsed_ms = (time.time() - start) * 1000
        filename = f"ecm-debug-bundle-{datetime.utcnow():%Y%m%d-%H%M%S}.tar.gz"
        logger.info("[AUTO-CREATE] Debug bundle generated in %.1fms (%s channels, %s rules)", elapsed_ms, len(all_channels), rule_count)

        return StreamingResponse(
            buf,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to generate debug bundle: %s", e)
        raise HTTPException(status_code=500, detail="Failed to generate debug bundle")


# =============================================================================
# Lint findings (bd-eio04.7) — read-only view of the startup migration scan.
# =============================================================================


@router.get("/lint-findings")
async def get_auto_creation_lint_findings():
    """Return the cached lint findings for auto-creation rules.

    See ``routers/normalization.py::get_normalization_lint_findings`` for
    semantics. Findings are scoped to ``rule_type='auto_creation'``.
    """
    logger.debug("[AUTO-CREATE] GET /lint-findings")
    try:
        from models import RuleLintFinding
        from tasks.rule_lint_scan import RULE_TYPE_AUTO_CREATION

        session = get_session()
        try:
            findings = session.query(RuleLintFinding).filter(
                RuleLintFinding.rule_type == RULE_TYPE_AUTO_CREATION
            ).order_by(RuleLintFinding.rule_id, RuleLintFinding.id).all()
            return {"findings": [f.to_dict() for f in findings]}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to get lint findings: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
