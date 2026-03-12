"""
Normalization router — normalization rule management and testing endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import json
import logging
import time
from typing import List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config import get_settings
from database import get_session
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/normalization", tags=["Normalization"])


# Request models
class CreateRuleGroupRequest(BaseModel):
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0


class UpdateRuleGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class CreateRuleRequest(BaseModel):
    group_id: int
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    # Legacy single condition (optional if using compound conditions)
    condition_type: Optional[str] = None  # always, contains, starts_with, ends_with, regex, tag_group
    condition_value: Optional[str] = None
    case_sensitive: bool = False
    # Tag group condition (for condition_type='tag_group')
    tag_group_id: Optional[int] = None
    tag_match_position: Optional[str] = None  # 'prefix', 'suffix', or 'contains'
    # Compound conditions (takes precedence over legacy fields if set)
    conditions: Optional[List[dict]] = None  # [{type, value, negate, case_sensitive}]
    condition_logic: str = "AND"  # "AND" or "OR"
    # Action configuration
    action_type: str  # remove, replace, regex_replace, strip_prefix, strip_suffix, normalize_prefix
    action_value: Optional[str] = None
    # Else action (executed when condition doesn't match)
    else_action_type: Optional[str] = None
    else_action_value: Optional[str] = None
    stop_processing: bool = False


class UpdateRuleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    # Legacy single condition
    condition_type: Optional[str] = None
    condition_value: Optional[str] = None
    case_sensitive: Optional[bool] = None
    # Tag group condition
    tag_group_id: Optional[int] = None
    tag_match_position: Optional[str] = None
    # Compound conditions
    conditions: Optional[List[dict]] = None
    condition_logic: Optional[str] = None
    # Action configuration
    action_type: Optional[str] = None
    action_value: Optional[str] = None
    # Else action
    else_action_type: Optional[str] = None
    else_action_value: Optional[str] = None
    stop_processing: Optional[bool] = None


class TestRuleRequest(BaseModel):
    text: str
    condition_type: str
    condition_value: Optional[str] = None
    case_sensitive: bool = False
    # Tag group condition
    tag_group_id: Optional[int] = None
    tag_match_position: Optional[str] = None  # 'prefix', 'suffix', or 'contains'
    # Compound conditions (takes precedence if set)
    conditions: Optional[List[dict]] = None  # [{type, value, negate, case_sensitive}]
    condition_logic: str = "AND"  # "AND" or "OR"
    action_type: str
    action_value: Optional[str] = None
    # Else action
    else_action_type: Optional[str] = None
    else_action_value: Optional[str] = None


class TestRulesBatchRequest(BaseModel):
    texts: list[str]


class ReorderRulesRequest(BaseModel):
    rule_ids: list[int]  # Rules in new priority order


class ReorderGroupsRequest(BaseModel):
    group_ids: list[int]  # Groups in new priority order


@router.get("/rules")
async def get_all_normalization_rules():
    """Get all normalization rules organized by group."""
    logger.debug("[NORMALIZE] GET /rules")
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            rules = engine.get_all_rules()
            return {"groups": rules}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to get normalization rules")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/groups")
async def get_normalization_groups():
    """Get all normalization rule groups."""
    logger.debug("[NORMALIZE] GET /groups")
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            groups = session.query(NormalizationRuleGroup).order_by(
                NormalizationRuleGroup.priority
            ).all()
            return {"groups": [g.to_dict() for g in groups]}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to get normalization groups")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/groups")
async def create_normalization_group(request: CreateRuleGroupRequest):
    """Create a new normalization rule group."""
    logger.debug("[NORMALIZE] POST /groups - name=%s", request.name)
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            group = NormalizationRuleGroup(
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                is_builtin=False
            )
            session.add(group)
            session.commit()
            session.refresh(group)
            logger.info("[NORMALIZE] Created group id=%s name=%s", group.id, group.name)
            return group.to_dict()
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to create normalization group")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/groups/{group_id}")
async def get_normalization_group(group_id: int):
    """Get a normalization rule group by ID."""
    logger.debug("[NORMALIZE] GET /groups/%s", group_id)
    try:
        from models import NormalizationRuleGroup, NormalizationRule
        session = get_session()
        try:
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            # Include rules in response
            rules = session.query(NormalizationRule).filter(
                NormalizationRule.group_id == group_id
            ).order_by(NormalizationRule.priority).all()

            result = group.to_dict()
            result["rules"] = [r.to_dict() for r in rules]
            return result
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to get normalization group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/groups/{group_id}")
async def update_normalization_group(group_id: int, request: UpdateRuleGroupRequest):
    """Update a normalization rule group."""
    logger.debug("[NORMALIZE] PATCH /groups/%s", group_id)
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            if request.name is not None:
                group.name = request.name
            if request.description is not None:
                group.description = request.description
            if request.enabled is not None:
                group.enabled = request.enabled
            if request.priority is not None:
                group.priority = request.priority

            session.commit()
            session.refresh(group)
            logger.info("[NORMALIZE] Updated group id=%s name=%s", group.id, group.name)
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to update normalization group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/groups/{group_id}")
async def delete_normalization_group(group_id: int):
    """Delete a normalization rule group and all its rules."""
    logger.debug("[NORMALIZE] DELETE /groups/%s", group_id)
    try:
        from models import NormalizationRuleGroup, NormalizationRule
        session = get_session()
        try:
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            # Delete all rules in this group first
            session.query(NormalizationRule).filter(
                NormalizationRule.group_id == group_id
            ).delete()

            # Delete the group
            session.delete(group)
            session.commit()
            logger.info("[NORMALIZE] Deleted group id=%s", group_id)
            return {"status": "deleted", "id": group_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to delete normalization group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/groups/reorder")
async def reorder_normalization_groups(request: ReorderGroupsRequest):
    """Reorder normalization rule groups."""
    logger.debug("[NORMALIZE] POST /groups/reorder - count=%s", len(request.group_ids))
    try:
        from models import NormalizationRuleGroup
        session = get_session()
        try:
            for priority, group_id in enumerate(request.group_ids):
                session.query(NormalizationRuleGroup).filter(
                    NormalizationRuleGroup.id == group_id
                ).update({"priority": priority})
            session.commit()
            return {"status": "reordered", "group_ids": request.group_ids}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to reorder normalization groups")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/rules/{rule_id}")
async def get_normalization_rule(rule_id: int):
    """Get a normalization rule by ID."""
    logger.debug("[NORMALIZE] GET /rules/%s", rule_id)
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            rule = session.query(NormalizationRule).filter(
                NormalizationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to get normalization rule %s", rule_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules")
async def create_normalization_rule(request: CreateRuleRequest):
    """Create a new normalization rule."""
    logger.debug("[NORMALIZE] POST /rules - name=%s group_id=%s", request.name, request.group_id)
    try:
        from models import NormalizationRule, NormalizationRuleGroup
        session = get_session()
        try:
            # Verify group exists
            group = session.query(NormalizationRuleGroup).filter(
                NormalizationRuleGroup.id == request.group_id
            ).first()
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")

            # Serialize conditions to JSON if provided
            conditions_json = json.dumps(request.conditions) if request.conditions else None

            rule = NormalizationRule(
                group_id=request.group_id,
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                condition_type=request.condition_type,
                condition_value=request.condition_value,
                case_sensitive=request.case_sensitive,
                tag_group_id=request.tag_group_id,
                tag_match_position=request.tag_match_position,
                conditions=conditions_json,
                condition_logic=request.condition_logic,
                action_type=request.action_type,
                action_value=request.action_value,
                else_action_type=request.else_action_type,
                else_action_value=request.else_action_value,
                stop_processing=request.stop_processing,
                is_builtin=False
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            logger.info("[NORMALIZE] Created rule id=%s name=%s", rule.id, rule.name)
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to create normalization rule")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/rules/{rule_id}")
async def update_normalization_rule(rule_id: int, request: UpdateRuleRequest):
    """Update a normalization rule."""
    logger.debug("[NORMALIZE] PATCH /rules/%s", rule_id)
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            rule = session.query(NormalizationRule).filter(
                NormalizationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            if request.name is not None:
                rule.name = request.name
            if request.description is not None:
                rule.description = request.description
            if request.enabled is not None:
                rule.enabled = request.enabled
            if request.priority is not None:
                rule.priority = request.priority
            if request.condition_type is not None:
                rule.condition_type = request.condition_type
            if request.condition_value is not None:
                rule.condition_value = request.condition_value
            if request.case_sensitive is not None:
                rule.case_sensitive = request.case_sensitive
            if request.tag_group_id is not None:
                rule.tag_group_id = request.tag_group_id
            if request.tag_match_position is not None:
                rule.tag_match_position = request.tag_match_position
            if request.conditions is not None:
                rule.conditions = json.dumps(request.conditions) if request.conditions else None
            if request.condition_logic is not None:
                rule.condition_logic = request.condition_logic
            if request.action_type is not None:
                rule.action_type = request.action_type
            if request.action_value is not None:
                rule.action_value = request.action_value
            if request.else_action_type is not None:
                rule.else_action_type = request.else_action_type
            if request.else_action_value is not None:
                rule.else_action_value = request.else_action_value
            if request.stop_processing is not None:
                rule.stop_processing = request.stop_processing

            session.commit()
            session.refresh(rule)
            logger.info("[NORMALIZE] Updated rule id=%s name=%s", rule.id, rule.name)
            return rule.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to update normalization rule %s", rule_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/rules/{rule_id}")
async def delete_normalization_rule(rule_id: int):
    """Delete a normalization rule."""
    logger.debug("[NORMALIZE] DELETE /rules/%s", rule_id)
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            rule = session.query(NormalizationRule).filter(
                NormalizationRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            session.delete(rule)
            session.commit()
            logger.info("[NORMALIZE] Deleted rule id=%s", rule_id)
            return {"status": "deleted", "id": rule_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to delete normalization rule %s", rule_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/groups/{group_id}/rules/reorder")
async def reorder_normalization_rules(group_id: int, request: ReorderRulesRequest):
    """Reorder normalization rules within a group."""
    logger.debug("[NORMALIZE] POST /groups/%s/rules/reorder - count=%s", group_id, len(request.rule_ids))
    try:
        from models import NormalizationRule
        session = get_session()
        try:
            for priority, rule_id in enumerate(request.rule_ids):
                session.query(NormalizationRule).filter(
                    NormalizationRule.id == rule_id,
                    NormalizationRule.group_id == group_id
                ).update({"priority": priority})
            session.commit()
            return {"status": "reordered", "group_id": group_id, "rule_ids": request.rule_ids}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to reorder rules in group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test")
async def test_normalization_rule(request: TestRuleRequest):
    """Test a rule configuration against sample text without saving."""
    logger.debug("[NORMALIZE] POST /test - action_type=%s condition_type=%s", request.action_type, request.condition_type)
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            result = engine.test_rule(
                text=request.text,
                condition_type=request.condition_type,
                condition_value=request.condition_value or "",
                case_sensitive=request.case_sensitive,
                action_type=request.action_type,
                action_value=request.action_value or "",
                conditions=request.conditions,
                condition_logic=request.condition_logic,
                tag_group_id=request.tag_group_id,
                tag_match_position=request.tag_match_position or "contains",
                else_action_type=request.else_action_type,
                else_action_value=request.else_action_value
            )
            return result
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to test normalization rule")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test-batch")
async def test_normalization_batch(request: TestRulesBatchRequest):
    """Test all enabled rules against multiple sample texts."""
    logger.debug("[NORMALIZE] POST /test-batch - count=%s", len(request.texts))
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            results = engine.test_rules_batch(request.texts)
            return {
                "results": [
                    {
                        "original": r.original,
                        "normalized": r.normalized,
                        "rules_applied": r.rules_applied,
                        "transformations": [
                            {"rule_id": t[0], "before": t[1], "after": t[2]}
                            for t in r.transformations
                        ]
                    }
                    for r in results
                ]
            }
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to test normalization batch")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/normalize")
async def normalize_text(request: TestRulesBatchRequest):
    """Normalize one or more texts using all enabled rules."""
    logger.debug("[NORMALIZE] POST /normalize - count=%s", len(request.texts))
    try:
        from normalization_engine import get_normalization_engine
        session = get_session()
        try:
            engine = get_normalization_engine(session)
            results = engine.test_rules_batch(request.texts)
            return {
                "results": [
                    {"original": r.original, "normalized": r.normalized}
                    for r in results
                ]
            }
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to normalize texts")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/rule-stats")
async def get_normalization_rule_stats(limit: int = 500):
    """Get statistics on how many streams each rule matches.

    Fetches streams from Dispatcharr and tests each enabled rule individually
    to count how many streams it would match.

    Args:
        limit: Maximum number of streams to test (default 500, max 2000)

    Returns:
        Dict with rule_stats (list of {rule_id, rule_name, group_name, match_count})
        and metadata (total_streams_tested, total_rules)
    """
    logger.debug("[NORMALIZE] GET /rule-stats - limit=%s", limit)
    try:
        from models import NormalizationRule, NormalizationRuleGroup
        from normalization_engine import get_normalization_engine

        # Cap the limit to avoid performance issues
        limit = min(limit, 2000)

        # Fetch streams from Dispatcharr
        client = get_client()
        start = time.time()
        streams_result = await client.get_streams(page=1, page_size=limit)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[NORMALIZE] get_streams completed in %.1fms", elapsed_ms)
        streams = streams_result.get("results", [])
        stream_names = [s.get("name", "") for s in streams if s.get("name")]

        if not stream_names:
            return {
                "rule_stats": [],
                "total_streams_tested": 0,
                "total_rules": 0
            }

        session = get_session()
        try:
            engine = get_normalization_engine(session)

            # Get all rules with their groups
            groups = session.query(NormalizationRuleGroup).order_by(
                NormalizationRuleGroup.priority
            ).all()
            group_map = {g.id: g.name for g in groups}

            rules = session.query(NormalizationRule).order_by(
                NormalizationRule.group_id,
                NormalizationRule.priority
            ).all()

            rule_stats = []
            for rule in rules:
                match_count = 0
                for name in stream_names:
                    match = engine._match_condition(name, rule)
                    if match.matched:
                        match_count += 1

                rule_stats.append({
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "group_id": rule.group_id,
                    "group_name": group_map.get(rule.group_id, "Unknown"),
                    "enabled": rule.enabled,
                    "match_count": match_count,
                    "match_percentage": round(match_count / len(stream_names) * 100, 1) if stream_names else 0
                })

            return {
                "rule_stats": rule_stats,
                "total_streams_tested": len(stream_names),
                "total_rules": len(rules)
            }
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to get rule stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/migration/status")
async def get_normalization_migration_status():
    """Get the status of the normalization rules migration."""
    logger.debug("[NORMALIZE] GET /migration/status")
    try:
        from normalization_migration import get_migration_status
        session = get_session()
        try:
            status = get_migration_status(session)
            return status
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to get migration status")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/migration/run")
async def run_normalization_migration(force: bool = False, migrate_settings: bool = True):
    """Create demo normalization rules.

    Creates editable demo rules that are disabled by default. Users can enable
    the rule groups they want to use.

    Args:
        force: If True, recreate rules even if they already exist
        migrate_settings: If True, also migrate user's custom_normalization_tags
    """
    logger.debug("[NORMALIZE] POST /migration/run - force=%s migrate_settings=%s", force, migrate_settings)
    try:
        from normalization_migration import create_demo_rules

        # Get user settings to migrate
        custom_normalization_tags = []

        if migrate_settings:
            settings = get_settings()
            custom_normalization_tags = settings.custom_normalization_tags or []

        session = get_session()
        try:
            result = create_demo_rules(
                session,
                force=force,
                custom_normalization_tags=custom_normalization_tags
            )
            return result
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to create demo rules")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/export")
async def export_normalization_rules():
    """Export all normalization rules and groups as YAML."""
    logger.debug("[NORMALIZE] GET /export")
    try:
        from models import NormalizationRuleGroup, NormalizationRule
        session = get_session()
        try:
            groups = session.query(NormalizationRuleGroup).order_by(
                NormalizationRuleGroup.priority
            ).all()

            export_data = {
                "normalization_rules": {
                    "version": 1,
                    "groups": []
                }
            }

            for group in groups:
                rules = session.query(NormalizationRule).filter(
                    NormalizationRule.group_id == group.id
                ).order_by(NormalizationRule.priority).all()

                group_data = {
                    "name": group.name,
                    "description": group.description,
                    "enabled": group.enabled,
                    "is_builtin": group.is_builtin,
                    "rules": []
                }

                for rule in rules:
                    rule_data = {
                        "name": rule.name,
                        "description": rule.description,
                        "enabled": rule.enabled,
                        "condition_type": rule.condition_type,
                        "condition_value": rule.condition_value,
                        "case_sensitive": rule.case_sensitive,
                        "action_type": rule.action_type,
                        "action_value": rule.action_value,
                        "stop_processing": rule.stop_processing,
                        "is_builtin": rule.is_builtin,
                    }

                    # Include compound conditions if present
                    if rule.conditions:
                        rule_data["conditions"] = json.loads(rule.conditions) if isinstance(rule.conditions, str) else rule.conditions
                        rule_data["condition_logic"] = rule.condition_logic or "AND"

                    # Include tag group reference by name for portability
                    if rule.tag_group_id:
                        from models import TagGroup
                        tag_group = session.query(TagGroup).filter(TagGroup.id == rule.tag_group_id).first()
                        if tag_group:
                            rule_data["tag_group_name"] = tag_group.name
                        rule_data["tag_match_position"] = rule.tag_match_position

                    # Include else action if present
                    if rule.else_action_type:
                        rule_data["else_action_type"] = rule.else_action_type
                        rule_data["else_action_value"] = rule.else_action_value

                    group_data["rules"].append(rule_data)

                export_data["normalization_rules"]["groups"].append(group_data)

            yaml_content = yaml.dump(export_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
            return Response(
                content=yaml_content,
                media_type="application/x-yaml",
                headers={"Content-Disposition": "attachment; filename=normalization-rules.yaml"}
            )
        finally:
            session.close()
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to export normalization rules")
        raise HTTPException(status_code=500, detail="Internal server error")


class ImportRulesRequest(BaseModel):
    yaml_content: str
    overwrite: bool = False  # If true, delete existing non-builtin groups first


@router.post("/import")
async def import_normalization_rules(request: ImportRulesRequest):
    """Import normalization rules and groups from YAML."""
    logger.debug("[NORMALIZE] POST /import - overwrite=%s", request.overwrite)
    try:
        data = yaml.safe_load(request.yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not data or "normalization_rules" not in data:
        raise HTTPException(status_code=400, detail="Missing 'normalization_rules' key in YAML")

    rules_data = data["normalization_rules"]
    if "groups" not in rules_data:
        raise HTTPException(status_code=400, detail="Missing 'groups' key in normalization_rules")

    try:
        from models import NormalizationRuleGroup, NormalizationRule, TagGroup
        session = get_session()
        try:
            # If overwrite, delete existing non-builtin groups
            if request.overwrite:
                existing_groups = session.query(NormalizationRuleGroup).filter(
                    NormalizationRuleGroup.is_builtin == False
                ).all()
                for g in existing_groups:
                    session.query(NormalizationRule).filter(
                        NormalizationRule.group_id == g.id
                    ).delete()
                    session.delete(g)
                session.flush()

            # Build tag group name -> id map for resolving references
            tag_groups = session.query(TagGroup).all()
            tag_group_map = {tg.name: tg.id for tg in tag_groups}

            created_groups = 0
            created_rules = 0
            skipped_groups = 0

            max_priority = session.query(NormalizationRuleGroup).count()

            for group_data in rules_data["groups"]:
                group_name = group_data.get("name")
                if not group_name:
                    continue

                # Skip if group with same name already exists (unless overwrite)
                existing = session.query(NormalizationRuleGroup).filter(
                    NormalizationRuleGroup.name == group_name
                ).first()
                if existing:
                    skipped_groups += 1
                    continue

                group = NormalizationRuleGroup(
                    name=group_name,
                    description=group_data.get("description"),
                    enabled=group_data.get("enabled", True),
                    priority=max_priority,
                    is_builtin=False,  # Always create as non-builtin on import
                )
                session.add(group)
                session.flush()  # Get the group ID
                max_priority += 1
                created_groups += 1

                for rule_priority, rule_data in enumerate(group_data.get("rules", [])):
                    # Resolve tag group reference by name
                    tag_group_id = None
                    if "tag_group_name" in rule_data:
                        tag_group_id = tag_group_map.get(rule_data["tag_group_name"])

                    conditions_json = None
                    if "conditions" in rule_data and rule_data["conditions"]:
                        conditions_json = json.dumps(rule_data["conditions"])

                    rule = NormalizationRule(
                        group_id=group.id,
                        name=rule_data.get("name", "Imported Rule"),
                        description=rule_data.get("description"),
                        enabled=rule_data.get("enabled", True),
                        priority=rule_priority,
                        condition_type=rule_data.get("condition_type"),
                        condition_value=rule_data.get("condition_value"),
                        case_sensitive=rule_data.get("case_sensitive", False),
                        tag_group_id=tag_group_id,
                        tag_match_position=rule_data.get("tag_match_position"),
                        conditions=conditions_json,
                        condition_logic=rule_data.get("condition_logic", "AND"),
                        action_type=rule_data.get("action_type", "remove"),
                        action_value=rule_data.get("action_value"),
                        else_action_type=rule_data.get("else_action_type"),
                        else_action_value=rule_data.get("else_action_value"),
                        stop_processing=rule_data.get("stop_processing", False),
                        is_builtin=False,
                    )
                    session.add(rule)
                    created_rules += 1

            session.commit()
            logger.info("[NORMALIZE] Imported %s groups, %s rules, skipped %s groups",
                        created_groups, created_rules, skipped_groups)
            return {
                "status": "imported",
                "created_groups": created_groups,
                "created_rules": created_rules,
                "skipped_groups": skipped_groups,
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[NORMALIZE] Failed to import normalization rules")
        raise HTTPException(status_code=500, detail=str(e))
