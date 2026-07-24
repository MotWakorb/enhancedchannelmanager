"""
Auto-creation router — auto-creation pipeline CRUD, execution, import/export, schema.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import asyncio
import io
import json
import logging
import tarfile
import time
import uuid
from datetime import date, datetime
from typing import List, Optional

import httpx

import journal
from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, model_validator
from starlette.responses import StreamingResponse

from auth import RequireAdminIfEnabled
from concurrency import run_cpu_bound
from database import get_session
from dispatcharr_client import get_client
from services.dedup_matcher import (
    NameCleanMode,
    is_admissible,
    score_all,
)
from regex_lint import (
    lint_actions_json,
    lint_conditions_json,
    lint_pattern,
    violations_to_http_detail,
)

# Strong references to in-flight background pipeline tasks (bd-enfsy). Without
# this set, asyncio only weakly references created tasks and the GC could
# cancel them mid-run — fire-and-forget without supervision is a known
# footgun. Tasks remove themselves on completion via the done callback below.
_BACKGROUND_TASKS: set[asyncio.Task] = set()

logger = logging.getLogger(__name__)

# NOTE (enhancedchannelmanager-dl0kk): route handler function names in this
# module (get_auto_creation_rules, create_auto_creation_rule,
# run_auto_creation_pipeline, _lint_auto_creation_rule_request, etc.)
# intentionally keep their old "auto_creation" names for now. FastAPI derives
# each endpoint's OpenAPI operationId from the handler function name when one
# isn't set explicitly, so renaming these here would change the external API
# contract ahead of schedule. The handler rename is coordinated with the
# API route path + MCP tool alias rename in a later phase — do not rename
# these in isolation.
router = APIRouter(tags=["Channel Pipeline"])


# =============================================================================
# Pydantic models
# =============================================================================


class CreateChannelPipelineRuleRequest(BaseModel):
    """Request to create an auto-creation rule."""
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    active_from: Optional[date] = None
    active_until: Optional[date] = None
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
    quality_tie_break_order: str = "desc"
    quality_m3u_tie_break_enabled: bool = True
    normalization_group_ids: list[int] = []
    skip_struck_streams: bool = False
    orphan_action: str = "delete"
    # Default True for new rules (bd-p6ko9, GH #226) — see models.ChannelPipelineRule.
    match_scope_target_group: bool = True
    # Explicit rule-level scope group for merge lookups (GH #298, bd-kncun).
    # None = "Auto" (create_channel falls back to the action's target group;
    # merge_streams stays group-agnostic) — see models.ChannelPipelineRule.
    match_scope_group_id: Optional[int] = None
    # Manual-channel isolation (enhancedchannelmanager-orzck / W1). Default False
    # protects hand-built manual channels from being adopted as merge targets.
    allow_manual_channel_merge: bool = False
    # Fold match key (GH #645 / bead 0vao3). Opt-in: when True, the
    # create_channel if_exists merge lookup also compares by casefolded,
    # whitespace-stripped keys (match_fold.fold_match_key). Default False
    # keeps existing installs' matching behavior unchanged.
    fold_match_key: bool = False
    # Event Sync (ti939.1.3). Non-null makes the rule the event_sync KIND
    # (preview-only this phase — excluded from pipeline execution). Validated
    # by channel_pipeline_schema.validate_event_sync_config().
    event_sync_config: Optional[dict] = None

    @model_validator(mode="after")
    def validate_active_window(self):
        if (
            self.active_from is not None
            and self.active_until is not None
            and self.active_until < self.active_from
        ):
            raise ValueError("active_until must be on or after active_from")
        return self


class UpdateChannelPipelineRuleRequest(BaseModel):
    """Request to update an auto-creation rule."""
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    active_from: Optional[date] = None
    active_until: Optional[date] = None
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
    quality_tie_break_order: Optional[str] = None
    quality_m3u_tie_break_enabled: Optional[bool] = None
    normalization_group_ids: Optional[list[int]] = None
    skip_struck_streams: Optional[bool] = None
    orphan_action: Optional[str] = None
    match_scope_target_group: Optional[bool] = None
    # GH #298 (bd-kncun): None is a MEANINGFUL value here (the "Auto" choice),
    # so the update handler distinguishes "field present in request" from "field
    # absent" via ``model_fields_set`` rather than the ``is not None`` convention
    # used by the other optionals — otherwise a rule could never be reset to Auto.
    match_scope_group_id: Optional[int] = None
    # enhancedchannelmanager-orzck (W1): None = leave unchanged.
    allow_manual_channel_merge: Optional[bool] = None
    # GH #645 / bead 0vao3: None = leave unchanged.
    fold_match_key: Optional[bool] = None
    # Event Sync (ti939.1.3). None is MEANINGFUL (clears the config, reverting
    # the rule to the standard kind), so the update handler distinguishes
    # "field present" from "field absent" via ``model_fields_set`` — the same
    # convention as match_scope_group_id above.
    event_sync_config: Optional[dict] = None

    @model_validator(mode="after")
    def validate_submitted_active_window(self):
        if (
            self.active_from is not None
            and self.active_until is not None
            and self.active_until < self.active_from
        ):
            raise ValueError("active_until must be on or after active_from")
        return self


class BulkUpdateChannelPipelineRulesRequest(UpdateChannelPipelineRuleRequest):
    """Bulk-update multiple rules. Only include fields to change (omit others)."""

    rule_ids: List[int] = Field(..., min_length=1, max_length=500)
    merge_streams_remove_non_matching: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_conditions_and_actions(cls, data):
        """bd-gjoe5: Bulk-update inherits conditions/actions from the single-rule
        update request, but the handler does not apply them — silently dropping
        them is the wrong default for an API contract. Reject the request so
        callers route conditions/actions edits through PUT /rules/{id} instead.
        event_sync_config (ti939.1.3) is rejected for the same reason: it is
        rule logic, not a scalar, and the bulk handler does not apply it.
        """
        if isinstance(data, dict) and (
            "conditions" in data
            or "actions" in data
            or "event_sync_config" in data
        ):
            raise ValueError(
                "conditions, actions and event_sync_config are not supported "
                "in bulk-update; use PUT /rules/{id}"
            )
        return data


class AnalyzeRuleBodyRequest(CreateChannelPipelineRuleRequest):
    """Analyze an UNSAVED rule body — advisory findings, no persistence
    (enhancedchannelmanager-m1s38.2).

    Reuses the create-rule field set so the rule builder can POST the same
    shape it would save, but relaxes and bounds it for live authoring:

    * ``name`` is optional — the analyzer runs while the rule is still being
      drafted, before the operator has typed a name.
    * ``conditions``/``actions`` are optional (an empty draft yields empty
      findings) and CAPPED at 200 each. No saved-rule cap exists today; 200
      is ~10x the largest rule seen in production and exists only to stop a
      giant pasted body from self-inflicting a slow AST-walk regex lint. The
      analyzer never executes user regex (regex_lint does a safe AST walk),
      so this is a work bound, not a ReDoS control. Advisory endpoint: the
      cap bounds the analyze request only — it never blocks a save.
    """

    name: str = ""
    conditions: list = Field(default_factory=list, max_length=200)
    actions: list = Field(default_factory=list, max_length=200)


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


def _apply_rule_scalar_updates(
    rule, request: UpdateChannelPipelineRuleRequest
) -> dict:
    """Apply scalar columns from an update request (excluding conditions/actions body).

    Returns a diff dict of the form ``{field: {"before": old, "after": new}}``
    for every scalar column whose value actually changed. Fields set to the
    same value they already had are omitted so callers can avoid emitting
    no-op journal entries (bd-91mcq).
    """
    diff: dict = {}

    def _set(field: str, new_value) -> None:
        """Assign attribute and record a diff entry if the value changed."""
        old_value = getattr(rule, field)
        if old_value != new_value:
            diff[field] = {"before": old_value, "after": new_value}
        setattr(rule, field, new_value)

    if request.name is not None:
        _set("name", request.name)
    if request.description is not None:
        _set("description", request.description)
    if request.enabled is not None:
        _set("enabled", request.enabled)
    if request.priority is not None:
        _set("priority", request.priority)
    if "active_from" in request.model_fields_set:
        _set("active_from", request.active_from)
    if "active_until" in request.model_fields_set:
        _set("active_until", request.active_until)
    if request.m3u_account_id is not None:
        _set("m3u_account_id", request.m3u_account_id)
    if request.target_group_id is not None:
        _set("target_group_id", request.target_group_id)
    if request.run_on_refresh is not None:
        _set("run_on_refresh", request.run_on_refresh)
    if request.stop_on_first_match is not None:
        _set("stop_on_first_match", request.stop_on_first_match)
    if request.sort_field is not None:
        _set("sort_field", request.sort_field or None)
    if request.sort_order is not None:
        _set("sort_order", request.sort_order)
    if request.probe_on_sort is not None:
        _set("probe_on_sort", request.probe_on_sort)
    if request.sort_regex is not None:
        _set("sort_regex", request.sort_regex or None)
    if request.stream_sort_field is not None:
        _set("stream_sort_field", request.stream_sort_field or None)
    if request.stream_sort_order is not None:
        _set("stream_sort_order", request.stream_sort_order)
    if request.quality_tie_break_order is not None:
        _set("quality_tie_break_order", request.quality_tie_break_order)
    if request.quality_m3u_tie_break_enabled is not None:
        _set("quality_m3u_tie_break_enabled", request.quality_m3u_tie_break_enabled)
    if request.normalization_group_ids is not None:
        # NormalizationRuleGroup IDs go through a setter; diff by before/after
        # of the serialized value to keep comparison simple.
        before_norm = rule.normalization_group_ids
        rule.set_normalization_group_ids(request.normalization_group_ids)
        after_norm = rule.normalization_group_ids
        if before_norm != after_norm:
            diff["normalization_group_ids"] = {
                "before": before_norm,
                "after": after_norm,
            }
    if request.skip_struck_streams is not None:
        _set("skip_struck_streams", request.skip_struck_streams)
    if request.orphan_action is not None:
        _set("orphan_action", request.orphan_action)
    if getattr(request, "match_scope_target_group", None) is not None:
        _set("match_scope_target_group", request.match_scope_target_group)
    # GH #298 (bd-kncun): only touch the scope group when the field was actually
    # supplied. ``model_fields_set`` lets an explicit ``None`` (reset to "Auto")
    # through, which the ``is not None`` convention above could not express.
    if "match_scope_group_id" in request.model_fields_set:
        _set("match_scope_group_id", request.match_scope_group_id)
    if getattr(request, "allow_manual_channel_merge", None) is not None:
        _set("allow_manual_channel_merge", request.allow_manual_channel_merge)
    if getattr(request, "fold_match_key", None) is not None:
        _set("fold_match_key", request.fold_match_key)

    return diff


def _validate_persisted_active_window(rule) -> None:
    """Validate the effective window after partial or bulk updates."""
    active_from = rule.active_from
    active_until = rule.active_until
    if (
        isinstance(active_from, date)
        and isinstance(active_until, date)
        and active_until < active_from
    ):
        raise HTTPException(status_code=400, detail={
            "message": "Invalid rule configuration",
            "errors": ["active_until must be on or after active_from"],
        })


def _parse_yaml_active_date(value, field_name: str) -> Optional[date]:
    """Parse a YAML calendar date without depending on scalar quoting."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a YYYY-MM-DD calendar date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date")


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


def _validate_normalization_group_ids(
    submitted_ids: Optional[list[int]], session
) -> None:
    """bd-i75ax: Reject write requests that reference non-existent
    NormalizationRuleGroup IDs.

    Delta-on-write semantics: only the IDs the caller is *submitting* on this
    request are validated. Already-stored values are left alone — a previously
    valid stored ID whose group has since been deleted should not block an
    unrelated PUT (e.g. renaming the rule). The startup audit (bd-i75ax)
    confirmed zero stale IDs in production data; this guard prevents future
    typos and copy-paste from the wrong environment.

    Empty list and ``None`` are both valid (no normalization groups is a
    legitimate state — it means normalization is disabled for the rule).

    Raises:
        HTTPException(422) with detail.invalid_normalization_group_ids listing
        every offending ID, when any submitted ID is missing from
        normalization_rule_groups.
    """
    if not submitted_ids:
        # None or empty list — nothing to validate.
        return

    from models import NormalizationRuleGroup

    # De-dup before query to keep the IN-list small; preserves order on report.
    seen: set[int] = set()
    unique_ids: list[int] = []
    for gid in submitted_ids:
        if gid not in seen:
            seen.add(gid)
            unique_ids.append(gid)

    existing_rows = session.query(NormalizationRuleGroup.id).filter(
        NormalizationRuleGroup.id.in_(unique_ids)
    ).all()
    existing_ids = {row[0] for row in existing_rows}

    invalid_ids = [gid for gid in unique_ids if gid not in existing_ids]
    if invalid_ids:
        logger.warning(
            "[AUTO-CREATE] Rejecting write — invalid normalization_group_ids: %s",
            invalid_ids,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "One or more normalization_group_ids do not exist in "
                    "normalization_rule_groups"
                ),
                "invalid_normalization_group_ids": invalid_ids,
            },
        )


# =============================================================================
# Rule CRUD Endpoints
# =============================================================================


@router.get("/rules")
async def get_auto_creation_rules():
    """Get all auto-creation rules sorted by priority."""
    logger.debug("[AUTO-CREATE] GET /rules")
    try:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rules = session.query(ChannelPipelineRule).order_by(
                ChannelPipelineRule.priority
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


# =============================================================================
# Rule analyzer (bd-0gntx Phase 1).
#
# Two endpoints:
#   POST /rules/analyze            — analyze rules currently in the DB.
#   POST /rules/analyze/from-bundle — analyze rules.yaml from an uploaded
#                                     debug bundle tar.gz, plus any
#                                     channel_groups_diagnostic.json.
# Both are advisory: findings are warnings/info, never errors. Saves are
# never blocked.
#
# Routes are declared BEFORE /rules/{rule_id} so the static "analyze"
# segment isn't captured by the rule_id path parameter.
# =============================================================================


@router.post("/rules/analyze")
async def analyze_auto_creation_rules():
    """Analyze all rules currently in the DB; return advisory findings.

    Response shape (see channel_pipeline_rule_analyzer.analyze_rules)::

        {
          "rules": [{"rule_id", "rule_name", "findings": [...]}],
          "summary": {"error": int, "warning": int, "info": int}
        }
    """
    logger.debug("[AUTO-CREATE] POST /rules/analyze")
    try:
        from channel_pipeline_rule_analyzer import analyze_rules
        from models import ChannelPipelineRule, NormalizationRuleGroup
        session = get_session()
        try:
            rules = session.query(ChannelPipelineRule).order_by(
                ChannelPipelineRule.priority
            ).all()
            rule_dicts = [r.to_dict() for r in rules]
            # Group enabled-state so the analyzer can flag rules that reference
            # DISABLED/missing normalization groups (enhancedchannelmanager-e8p1h).
            norm_groups = [
                g.to_dict()
                for g in session.query(NormalizationRuleGroup).all()
            ]
            result = analyze_rules(
                rule_dicts, normalization_groups=norm_groups,
            )
            logger.info(
                "[AUTO-CREATE] Analyzed %s rules: %s findings",
                len(rule_dicts),
                sum(result["summary"].values()),
            )
            return result
        finally:
            session.close()
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to analyze rules: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/analyze/from-bundle")
async def analyze_auto_creation_rules_from_bundle(
    file: UploadFile = File(...),
):
    """Analyze rules.yaml inside an uploaded debug-bundle tar.gz.

    The bundle is read entirely in-memory and never persisted. The
    endpoint never touches the DB — you can paste in any user's bundle
    without exposing this ECM's data.

    Returns the same response shape as POST /rules/analyze. If the
    bundle includes ``channel_groups_diagnostic.json`` the
    ``MERGE_STREAMS_NO_TARGET_CHANNELS`` finding becomes available.
    """
    import yaml
    from channel_pipeline_rule_analyzer import analyze_rules

    logger.debug(
        "[AUTO-CREATE] POST /rules/analyze/from-bundle filename=%s",
        file.filename,
    )

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to read uploaded file: {e}"
        )

    try:
        buf = io.BytesIO(content)
        tf = tarfile.open(fileobj=buf, mode="r:gz")
    except (tarfile.TarError, OSError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid tar.gz archive: {e}",
        )

    rules_yaml_text: str | None = None
    diagnostic: dict | None = None
    try:
        with tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                # Match by basename so we tolerate bundles created with a
                # leading directory (the production generator ships flat).
                base = member.name.rsplit("/", 1)[-1]
                if base == "rules.yaml":
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        rules_yaml_text = extracted.read().decode("utf-8")
                elif base == "channel_groups_diagnostic.json":
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        try:
                            diagnostic = json.loads(extracted.read())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            # Diagnostic is optional — corrupt one shouldn't
                            # 400 the whole analysis. Just drop it.
                            diagnostic = None
    except tarfile.TarError as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to read archive: {e}"
        )

    if rules_yaml_text is None:
        raise HTTPException(
            status_code=400,
            detail="Bundle does not contain a rules.yaml at the root.",
        )

    try:
        data = yaml.safe_load(rules_yaml_text)
    except yaml.YAMLError as e:
        raise HTTPException(
            status_code=400, detail=f"rules.yaml is not valid YAML: {e}"
        )

    if isinstance(data, list):
        rules_in = data
    elif isinstance(data, dict):
        rules_in = data.get("rules", [])
    else:
        rules_in = []

    if not isinstance(rules_in, list):
        raise HTTPException(
            status_code=400,
            detail="rules.yaml 'rules' must be a list.",
        )

    result = analyze_rules(rules_in, channel_groups_diagnostic=diagnostic)
    logger.info(
        "[AUTO-CREATE] Analyzed %s rules from bundle: %s findings",
        len(rules_in),
        sum(result["summary"].values()),
    )
    return result


@router.post("/rules/analyze-body")
async def analyze_channel_pipeline_rule_body(request: AnalyzeRuleBodyRequest):
    """Analyze an UNSAVED rule body; return advisory findings WITHOUT saving.

    Enables live authoring feedback in the rule builder (the rail is
    enhancedchannelmanager-m1s38.3). Same advisory-only contract and same
    response shape as ``POST /rules/analyze``::

        {
          "rules": [{"rule_id", "rule_name", "findings": [...]}],
          "summary": {"error": int, "warning": int, "info": int}
        }

    Advisory-only (bd-0gntx contract): findings are warnings/info, never a
    gate — this endpoint never refuses a save. It reuses the same
    ``analyze_rules`` engine as the saved-rule and from-bundle routes; the
    only new behavior vs those routes is that the body is caller-supplied
    (validated by ``AnalyzeRuleBodyRequest`` and length-capped) rather than
    read from the DB or a debug bundle.

    Auth: inherits the global ``/api/*`` gate (any authenticated user),
    matching the sibling ``/rules/analyze`` surface — no per-route admin
    dependency.
    """
    logger.debug("[AUTO-CREATE] POST /rules/analyze-body")
    try:
        from channel_pipeline_rule_analyzer import analyze_rules
        from models import NormalizationRuleGroup

        # The analyzer reads a dict shaped like a rule's to_dict(). Only the
        # fields it consumes are forwarded; the rest of the create-shaped body
        # (sort fields, orphan_action, etc.) is accepted for forward-compat but
        # not needed by any current check.
        rule_dict = {
            "id": None,
            "name": request.name,
            "conditions": request.conditions,
            "actions": request.actions,
            "target_group_id": request.target_group_id,
            "normalization_group_ids": request.normalization_group_ids,
            "match_scope_target_group": request.match_scope_target_group,
        }

        # Richer findings (bd-m1s38.2): normalization-group enabled-state lets
        # the disabled-normalization-group advisory fire; live channel-group
        # counts let the merge-empty-target advisory fire. Both are best-effort
        # — the analyzer no-ops when they're absent — so a lookup failure must
        # NOT 500 an advisory endpoint; we log and degrade to fewer findings.
        norm_groups = None
        try:
            session = get_session()
            try:
                norm_groups = [
                    g.to_dict()
                    for g in session.query(NormalizationRuleGroup).all()
                ]
            finally:
                session.close()
        except Exception as e:
            logger.warning(
                "[AUTO-CREATE] analyze-body: normalization groups unavailable, "
                "skipping disabled-group advisory: %s", e,
            )

        # Only pay the Dispatcharr round-trip when the merge-empty-target check
        # could actually fire (a merge_streams action AND an explicit target
        # group). Keeps the common debounced authoring call from fetching all
        # channel groups on every keystroke.
        diagnostic = None
        has_merge = any(
            isinstance(a, dict) and a.get("type") == "merge_streams"
            for a in request.actions
        )
        if has_merge and request.target_group_id is not None:
            try:
                client = get_client()
                groups = await client.get_channel_groups() or []
                diagnostic = {
                    "groups": [
                        {
                            "id": g.get("id"),
                            "name": g.get("name"),
                            "channel_count": g.get("channel_count"),
                        }
                        for g in groups
                        if isinstance(g, dict)
                    ]
                }
            except Exception as e:
                logger.warning(
                    "[AUTO-CREATE] analyze-body: channel groups unavailable, "
                    "skipping merge-empty-target advisory: %s", e,
                )

        result = analyze_rules(
            [rule_dict],
            channel_groups_diagnostic=diagnostic,
            normalization_groups=norm_groups,
        )
        logger.info(
            "[AUTO-CREATE] Analyzed unsaved rule body (%s conditions, "
            "%s actions): %s findings",
            len(request.conditions),
            len(request.actions),
            sum(result["summary"].values()),
        )
        return result
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to analyze rule body: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/rules/{rule_id}")
async def get_auto_creation_rule(rule_id: int):
    """Get a specific auto-creation rule by ID."""
    logger.debug("[AUTO-CREATE] GET /rules/%s", rule_id)
    try:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
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
async def create_auto_creation_rule(request: CreateChannelPipelineRuleRequest, _admin=RequireAdminIfEnabled):
    """Create a new auto-creation rule. Admin only."""
    try:
        from models import ChannelPipelineRule
        from channel_pipeline_schema import validate_rule

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

        # ti939.1.3: write-time validation of the event_sync config. Also
        # fills defaults (time_window_minutes/attach_threshold/enabled) in
        # place so the stored JSON is explicit.
        if request.event_sync_config is not None:
            from channel_pipeline_schema import validate_event_sync_config
            es_errors = validate_event_sync_config(request.event_sync_config)
            if es_errors:
                raise HTTPException(status_code=400, detail={
                    "message": "Invalid rule configuration",
                    "errors": es_errors
                })

        session = get_session()
        try:
            # bd-j5p4k: write-time FK validation for normalization_group_ids.
            # Run BEFORE the DB insert so a bad ID can't create a partially
            # populated row. Mirrors the PUT/bulk-update guard added in
            # bd-i75ax — same delta-on-write semantics, same 422 shape.
            _validate_normalization_group_ids(
                request.normalization_group_ids, session
            )

            # Auto-assign priority: if requested priority already taken, append at end
            existing_priorities = [r.priority for r in session.query(ChannelPipelineRule).all()]
            if existing_priorities and request.priority in existing_priorities:
                priority = max(existing_priorities) + 1
            else:
                priority = request.priority

            rule = ChannelPipelineRule(
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=priority,
                active_from=request.active_from,
                active_until=request.active_until,
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
                quality_tie_break_order=request.quality_tie_break_order,
                quality_m3u_tie_break_enabled=request.quality_m3u_tie_break_enabled,
                normalization_group_ids=json.dumps(request.normalization_group_ids) if request.normalization_group_ids else None,
                skip_struck_streams=request.skip_struck_streams,
                orphan_action=request.orphan_action,
                match_scope_target_group=request.match_scope_target_group,
                match_scope_group_id=request.match_scope_group_id,
                allow_manual_channel_merge=request.allow_manual_channel_merge,
                fold_match_key=request.fold_match_key,
                event_sync_config=(
                    json.dumps(request.event_sync_config)
                    if request.event_sync_config else None
                ),
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
async def update_auto_creation_rule(rule_id: int, request: UpdateChannelPipelineRuleRequest, _admin=RequireAdminIfEnabled):
    """Update an auto-creation rule. Admin only."""
    try:
        from models import ChannelPipelineRule
        from channel_pipeline_schema import validate_rule

        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            # bd-i75ax: write-time FK validation for normalization_group_ids.
            # Run BEFORE mutation so a bad ID can't leave partial scalar
            # changes on the row. Only validates IDs the caller submitted
            # (delta-on-write — does not re-check stored values).
            _validate_normalization_group_ids(
                request.normalization_group_ids, session
            )

            _apply_rule_scalar_updates(rule, request)
            _validate_persisted_active_window(rule)

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

            # ti939.1.3: event_sync_config. Delta-on-write (bd-i75ax
            # convention): only a SUBMITTED config is validated — stored
            # configs on unrelated PUTs (e.g. a rename) are left alone.
            # ``model_fields_set`` lets an explicit null through (clears the
            # config, reverting the rule to the standard kind), same
            # convention as match_scope_group_id.
            if "event_sync_config" in request.model_fields_set:
                if request.event_sync_config is not None:
                    from channel_pipeline_schema import validate_event_sync_config
                    es_errors = validate_event_sync_config(request.event_sync_config)
                    if es_errors:
                        raise HTTPException(status_code=400, detail={
                            "message": "Invalid rule configuration",
                            "errors": es_errors
                        })
                rule.set_event_sync_config(request.event_sync_config)

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
async def bulk_update_auto_creation_rules(request: BulkUpdateChannelPipelineRulesRequest, _admin=RequireAdminIfEnabled):
    """Apply the same field changes to many rules. Omitted fields are left unchanged. Admin only."""
    from models import ChannelPipelineRule
    from channel_pipeline_schema import validate_rule

    payload = request.model_dump(exclude_unset=True)
    rule_ids = payload.pop("rule_ids", None) or []
    merge_streams_remove_non_matching = payload.pop("merge_streams_remove_non_matching", None)

    if not rule_ids:
        raise HTTPException(status_code=400, detail="rule_ids is required")
    if len(rule_ids) != len(set(rule_ids)):
        raise HTTPException(status_code=400, detail="duplicate rule_ids")
    if not payload and merge_streams_remove_non_matching is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    scalar_update = UpdateChannelPipelineRuleRequest(**payload) if payload else UpdateChannelPipelineRuleRequest()

    # Lint sort_regex (bd-eio04.7) before any DB work. Bulk-update does not
    # accept conditions/actions, so only sort_regex can carry a pattern.
    _lint_auto_creation_rule_request(None, None, scalar_update.sort_regex)

    # Only mutations that touch rule logic (conditions/actions) need post-update
    # schema validation. Scalars-only bulk edits (enabled, priority, sort fields,
    # …) must not be blocked by pre-existing schema drift in untouched rows
    # (bd-z7xqy). Bulk-update does not accept raw conditions/actions, so today
    # the only logic mutation is merge_streams_remove_non_matching.
    mutated_rule_logic = merge_streams_remove_non_matching is not None

    session = get_session()
    try:
        # Single SELECT ... WHERE id IN (...) instead of N per-id queries
        # (bd-bh1hh: avoid N+1 — at max_length=500 this collapses 500 round
        # trips into 1).
        rules_by_id = {
            r.id: r for r in session.query(ChannelPipelineRule)
            .filter(ChannelPipelineRule.id.in_(rule_ids)).all()
        }
        missing = [rid for rid in rule_ids if rid not in rules_by_id]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Rules not found: {missing}",
            )

        # bd-i75ax: write-time FK validation for normalization_group_ids.
        # Validate the SUBMITTED ids once (they're applied identically to
        # every rule in scope), before any mutation. A bad ID must roll back
        # the entire bulk-update — not leave half the rules updated and the
        # other half rejected. Delta-on-write: stored values on rules in
        # scope are not re-checked.
        _validate_normalization_group_ids(
            scalar_update.normalization_group_ids, session
        )

        # Track per-rule diffs so we can emit per-entity journal entries with a
        # shared batch_id after a successful commit (bd-91mcq). Matches the
        # pattern at backend/routers/channels.py:800 (bulk channel renumber).
        updated: list = []
        rule_diffs: list[tuple] = []  # (rule, scalar_diff, merge_streams_before, merge_streams_after)
        for rid in rule_ids:
            rule = rules_by_id[rid]

            scalar_diff: dict = {}
            if payload:
                scalar_diff = _apply_rule_scalar_updates(rule, scalar_update)
                _validate_persisted_active_window(rule)

            merge_before = None
            merge_after = None
            if merge_streams_remove_non_matching is not None:
                actions = rule.get_actions()
                new_actions = _apply_merge_streams_remove_non_matching(
                    actions, merge_streams_remove_non_matching
                )
                if new_actions != actions:
                    merge_before = actions
                    merge_after = new_actions
                rule.actions = json.dumps(new_actions)

            if mutated_rule_logic:
                conditions = rule.get_conditions()
                actions = rule.get_actions()
                validation = validate_rule(conditions, actions)
                if not validation["valid"]:
                    raise HTTPException(status_code=400, detail={
                        "message": f"Invalid rule configuration for rule id={rid}",
                        "errors": validation["errors"],
                    })
            updated.append(rule)
            rule_diffs.append((rule, scalar_diff, merge_before, merge_after))

        session.commit()
        # No per-rule session.refresh() — attached instances already reflect
        # the committed values. ChannelPipelineRule has no server_default or DB
        # triggers on the columns written here; updated_at uses a Python-side
        # onupdate callable which SQLAlchemy applies during flush, so it is
        # populated on the in-memory instance before to_dict() reads it.
        # (bd-bh1hh: drops another N SELECTs per bulk request.)

        # Emit one journal entry per rule that actually changed, all sharing
        # the same batch_id so forensics can group them. Skip rules where no
        # scalar column and no merge-streams action changed (bulk-toggle to
        # the existing value is a no-op).
        batch_id = str(uuid.uuid4())[:8]
        for rule, scalar_diff, merge_before, merge_after in rule_diffs:
            if not scalar_diff and merge_before is None:
                continue

            description_parts = [
                f"{field}: {entry['before']} -> {entry['after']}"
                for field, entry in scalar_diff.items()
            ]
            if merge_before is not None:
                description_parts.append(
                    "merge_streams.remove_non_matching -> "
                    f"{merge_streams_remove_non_matching}"
                )
            description = (
                f"Bulk-updated rule '{rule.name}': " + ", ".join(description_parts)
            )

            before_value = {
                field: entry["before"] for field, entry in scalar_diff.items()
            }
            after_value = {
                field: entry["after"] for field, entry in scalar_diff.items()
            }
            if merge_before is not None:
                before_value["actions"] = merge_before
                after_value["actions"] = merge_after

            journal.log_entry(
                category="auto_creation",
                action_type="bulk_update",
                entity_id=rule.id,
                entity_name=rule.name,
                description=description,
                before_value=before_value,
                after_value=after_value,
                batch_id=batch_id,
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
async def delete_auto_creation_rule(rule_id: int, _admin=RequireAdminIfEnabled):
    """Delete an auto-creation rule. Admin only."""
    logger.debug("[AUTO-CREATE] DELETE /rules/%s", rule_id)
    try:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
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
async def reorder_auto_creation_rules(rule_ids: List[int] = Body(...), _admin=RequireAdminIfEnabled):
    """Reorder auto-creation rules by setting priorities based on array order. Admin only."""
    logger.debug("[AUTO-CREATE] POST /rules/reorder - %d rules", len(rule_ids))
    try:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            for priority, rule_id in enumerate(rule_ids):
                rule = session.query(ChannelPipelineRule).filter(
                    ChannelPipelineRule.id == rule_id
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
async def toggle_auto_creation_rule(rule_id: int, _admin=RequireAdminIfEnabled):
    """Toggle the enabled state of an auto-creation rule. Admin only."""
    logger.debug("[AUTO-CREATE] POST /rules/%s/toggle", rule_id)
    try:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
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
async def duplicate_auto_creation_rule(rule_id: int, _admin=RequireAdminIfEnabled):
    """Duplicate an auto-creation rule. Admin only."""
    logger.debug("[AUTO-CREATE] POST /rules/%s/duplicate", rule_id)
    try:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")

            # Create a copy with a new name
            new_rule = ChannelPipelineRule(
                name=f"{rule.name} (Copy)",
                description=rule.description,
                enabled=False,  # Disabled by default
                priority=rule.priority + 1,
                active_from=rule.active_from,
                active_until=rule.active_until,
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
                quality_tie_break_order=rule.quality_tie_break_order,
                quality_m3u_tie_break_enabled=rule.quality_m3u_tie_break_enabled,
                normalization_group_ids=rule.normalization_group_ids,
                skip_struck_streams=rule.skip_struck_streams,
                probe_on_sort=rule.probe_on_sort,
                sort_regex=rule.sort_regex,
                orphan_action=rule.orphan_action,
                match_scope_target_group=rule.match_scope_target_group,
                match_scope_group_id=rule.match_scope_group_id,
                allow_manual_channel_merge=rule.allow_manual_channel_merge,
                fold_match_key=rule.fold_match_key,
                # ti939.1.3: keep the KIND on duplication — dropping the
                # config would silently turn the copy into a standard rule
                # that executes in the pipeline.
                event_sync_config=rule.event_sync_config,
            )
            session.add(new_rule)
            session.commit()
            session.refresh(new_rule)

            # Log to journal (gjb01 audit-trail fix): duplication creates a
            # rule, so it journals a create entry like POST /rules does —
            # previously a duplicated-then-deleted rule left a delete-only
            # pair in the journal.
            journal.log_entry(
                category="auto_creation",
                action_type="create",
                entity_id=new_rule.id,
                entity_name=new_rule.name,
                description=(
                    f"Created auto-creation rule '{new_rule.name}' "
                    f"(duplicated from '{rule.name}')"
                ),
            )

            logger.info(
                "[AUTO-CREATE] Duplicated rule id=%s into id=%s name='%s'",
                rule_id, new_rule.id, new_rule.name,
            )
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


async def _ensure_engine():
    """Get the auto-creation engine, initializing it if necessary."""
    from channel_pipeline_engine import get_channel_pipeline_engine, init_channel_pipeline_engine

    engine = get_channel_pipeline_engine()
    if not engine:
        client = get_client()
        engine = await init_channel_pipeline_engine(client)
    return engine


def _create_pending_execution(
    *,
    mode: str,
    triggered_by: str,
    rule_id: int | None = None,
    rule_name: str | None = None,
) -> int:
    """Create a status='running' execution row up front (bd-enfsy 202+poll).

    Returns the new execution id so the caller can return it in the 202
    response and the background task can finalize the same row when work
    completes.
    """
    from models import ChannelPipelineExecution

    session = get_session()
    try:
        execution = ChannelPipelineExecution(
            mode=mode,
            triggered_by=triggered_by,
            started_at=datetime.utcnow(),
            status="running",
            rule_id=rule_id,
            rule_name=rule_name,
        )
        session.add(execution)
        session.commit()
        session.refresh(execution)
        return execution.id
    finally:
        session.close()


def _mark_execution_failed(execution_id: int, error: BaseException) -> None:
    """Mark a pre-created execution as failed and capture the error message."""
    from models import ChannelPipelineExecution

    session = get_session()
    try:
        execution = session.query(ChannelPipelineExecution).filter(
            ChannelPipelineExecution.id == execution_id
        ).first()
        if execution is None:
            logger.warning(
                "[AUTO-CREATE] Cannot mark execution %s failed — row was deleted",
                execution_id,
            )
            return
        # Only finalize if still running (don't clobber a completed/rolled_back row)
        if execution.status == "running":
            now = datetime.utcnow()
            execution.completed_at = now
            execution.duration_seconds = (
                (now - execution.started_at).total_seconds()
                if execution.started_at else 0.0
            )
            execution.status = "failed"
            execution.error_message = f"{type(error).__name__}: {error}"
            session.commit()
    finally:
        session.close()


def _supervise_background_pipeline(coro, *, execution_id: int, label: str) -> asyncio.Task:
    """Schedule a coroutine as a supervised background task.

    Wraps the coroutine in a try/except so any failure is captured to the
    pre-created execution row (status='failed' + error_message) — no silent
    fire-and-forget. Holds a strong reference to the task so the GC cannot
    cancel it mid-run.
    """
    async def _runner():
        try:
            await coro
            logger.info("[AUTO-CREATE] Background %s execution=%s completed", label, execution_id)
        except asyncio.CancelledError:
            logger.warning("[AUTO-CREATE] Background %s execution=%s cancelled", label, execution_id)
            _mark_execution_failed(execution_id, RuntimeError("Background task cancelled"))
            raise
        except Exception as e:  # noqa: BLE001 — supervisor must catch broadly
            logger.exception(
                "[AUTO-CREATE] Background %s execution=%s failed: %s",
                label, execution_id, e,
            )
            _mark_execution_failed(execution_id, e)

    task = asyncio.create_task(_runner(), name=f"auto-creation-{label}-exec-{execution_id}")
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


@router.post("/run", status_code=202)
async def run_auto_creation_pipeline(request: RunPipelineRequest, _admin=RequireAdminIfEnabled):
    """Enqueue an auto-creation pipeline run and return immediately (bd-enfsy).

    Pipeline runs on large catalogs can take minutes — running them inside the
    request coroutine pinned an HTTP worker for the duration and forced us to
    exempt this prefix from the 30s request-timeout middleware (bd-zv6pi). We
    now pre-create the execution row, return its id, and run the pipeline in
    a supervised background task. Clients poll
    ``GET /api/auto-creation/executions/{id}`` until ``status`` is terminal
    (``completed`` / ``failed`` / ``rolled_back``).
    """
    logger.debug("[AUTO-CREATE] POST /run - dry_run=%s", request.dry_run)
    try:
        engine = await _ensure_engine()
        execution_id = _create_pending_execution(
            mode="dry_run" if request.dry_run else "execute",
            triggered_by="api",
        )
        _supervise_background_pipeline(
            engine.run_pipeline(
                dry_run=request.dry_run,
                triggered_by="api",
                m3u_account_ids=request.m3u_account_ids,
                rule_ids=request.rule_ids,
                execution_id=execution_id,
            ),
            execution_id=execution_id,
            label="run_pipeline",
        )
        return JSONResponse(
            status_code=202,
            content={
                "execution_id": execution_id,
                "status": "running",
                "message": "Pipeline started; poll /api/channel-pipeline/executions/{id} for status",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to enqueue auto-creation pipeline: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/rules/{rule_id}/run", status_code=202)
async def run_auto_creation_rule(rule_id: int, dry_run: bool = False, _admin=RequireAdminIfEnabled):
    """Enqueue a single-rule auto-creation run and return immediately (bd-enfsy).

    See ``run_auto_creation_pipeline`` for the 202 + poll contract.
    """
    logger.debug("[AUTO-CREATE] POST /rules/%s/run - dry_run=%s", rule_id, dry_run)
    try:
        engine = await _ensure_engine()
        # Verify the rule exists before enqueuing — otherwise the pre-created
        # execution row violates the rule_id FK constraint and the caller would
        # see a 500 instead of a clean 404. Capture the name so the execution
        # row keeps it for display even if the rule is later deleted (matches
        # the engine's pre-existing behavior).
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
            ).first()
            if rule is None:
                raise HTTPException(status_code=404, detail="Rule not found")
            rule_name = rule.name
        finally:
            session.close()

        execution_id = _create_pending_execution(
            mode="dry_run" if dry_run else "execute",
            triggered_by="api",
            rule_id=rule_id,
            rule_name=rule_name,
        )
        _supervise_background_pipeline(
            engine.run_rule(
                rule_id=rule_id,
                dry_run=dry_run,
                triggered_by="api",
                execution_id=execution_id,
            ),
            execution_id=execution_id,
            label="run_rule",
        )
        return JSONResponse(
            status_code=202,
            content={
                "execution_id": execution_id,
                "status": "running",
                "rule_id": rule_id,
                "message": "Rule run started; poll /api/channel-pipeline/executions/{id} for status",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to enqueue auto-creation rule %s: %s", rule_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/circuit-breaker")
async def get_run_on_refresh_circuit_breaker():
    """Return the run-on-refresh circuit-breaker state (bd-exo4j / GH #473).

    ``disabled`` True means a previous auto-creation run was abandoned (likely
    an OOM crash) and the startup crash-sentinel tripped the breaker, so
    auto-creation will NOT auto-fire after M3U refresh until an operator clears
    it via ``POST /reset-circuit-breaker``. Manual "Run Now" is never gated.
    Lets the frontend distinguish auto-disabled from user-disabled.
    """
    from config import get_settings
    settings = get_settings()
    return {
        "disabled": bool(getattr(settings, "auto_creation_run_on_refresh_disabled", False)),
        "reason": "abandoned_run" if getattr(settings, "auto_creation_run_on_refresh_disabled", False) else None,
    }


@router.post("/reset-circuit-breaker")
async def reset_run_on_refresh_circuit_breaker(_admin=RequireAdminIfEnabled):
    """Clear the run-on-refresh circuit breaker (bd-exo4j / GH #473).

    A DELIBERATE operator act — re-enables the post-refresh auto-fire chain
    that the startup crash-sentinel disabled after an abandoned (OOM-killed)
    run. The breaker NEVER auto-resets on its own, so this endpoint is the only
    in-band recovery path. Idempotent: clearing an already-clear breaker is a
    no-op success.
    """
    from config import get_settings, save_settings
    settings = get_settings()
    was_disabled = bool(getattr(settings, "auto_creation_run_on_refresh_disabled", False))
    if was_disabled:
        settings.auto_creation_run_on_refresh_disabled = False
        save_settings(settings)
        logger.warning(
            "[AUTO-CREATE] Run-on-refresh circuit breaker CLEARED by operator — "
            "auto-creation will auto-fire after M3U refresh again."
        )
        try:
            journal.log_entry(
                category="auto_creation",
                action_type="circuit_breaker_reset",
                entity_name="Auto-Creation",
                description="Operator cleared the run-on-refresh circuit breaker.",
                user_initiated=True,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[AUTO-CREATE] Failed to journal breaker reset: %s", e)
    return {"success": True, "was_disabled": was_disabled, "disabled": False}


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
        from models import ChannelPipelineExecution, ChannelPipelineSnapshot
        session = get_session()
        try:
            query = session.query(ChannelPipelineExecution)

            if rule_id is not None:
                query = query.filter(ChannelPipelineExecution.rule_id == rule_id)
            if status is not None:
                query = query.filter(ChannelPipelineExecution.status == status)

            total = query.count()
            executions = query.order_by(
                ChannelPipelineExecution.started_at.desc()
            ).offset(offset).limit(limit).all()

            # Derive has_snapshot (ADR-010 §D6) — a boolean from the existence
            # of an ChannelPipelineSnapshot row, NOT a denormalized column (which
            # could drift from the FK truth). uc51o.6 (MCP) and uc51o.7 (UI)
            # gate the snapshot-restore affordance on this flag. Resolve it with
            # ONE query over the page's execution ids (an IN over the snapshot
            # FK index) — never one query per execution (no N+1).
            page_ids = [e.id for e in executions]
            snapshotted_ids: set[int] = set()
            if page_ids:
                snapshotted_ids = {
                    row[0]
                    for row in session.query(
                        ChannelPipelineSnapshot.execution_id
                    ).filter(
                        ChannelPipelineSnapshot.execution_id.in_(page_ids)
                    ).all()
                }

            execution_dicts = []
            for e in executions:
                d = e.to_dict()
                d["has_snapshot"] = e.id in snapshotted_ids
                execution_dicts.append(d)

            return {
                "executions": execution_dicts,
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
        from models import ChannelPipelineExecution, ChannelPipelineConflict
        session = get_session()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == execution_id
            ).first()
            if not execution:
                raise HTTPException(status_code=404, detail="Execution not found")

            result = execution.to_dict(include_entities=include_entities, include_log=include_log)

            # Include conflicts
            conflicts = session.query(ChannelPipelineConflict).filter(
                ChannelPipelineConflict.execution_id == execution_id
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


@router.get("/executions/{execution_id}/snapshot")
async def get_auto_creation_execution_snapshot(execution_id: int):
    """Get the pre-run channel<->stream snapshot for an execution (ADR-010).

    Returns the snapshot payload — the manual (non-Dispatcharr-auto-created)
    channels captured BEFORE this execution mutated anything, with
    ``stream_ids`` (IDs only — never URLs, §D1), plus ``snapshot_time`` and
    ``channel_count``. Read-only — no admin guard (consistent with the
    router's other GETs; the global auth middleware already covers it). The
    WRITE restore endpoint (Phase 3, uc51o.4) WILL be admin-gated.

    Returns 404 when the execution has no snapshot (e.g. a dry-run, a legacy
    run, or a run whose capture failed and logged-and-proceeded — §D2).
    """
    logger.debug("[AUTO-CREATE] GET /executions/%s/snapshot", execution_id)
    try:
        from models import ChannelPipelineSnapshot
        session = get_session()
        try:
            snapshot = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == execution_id
            ).first()
            if not snapshot:
                raise HTTPException(
                    status_code=404,
                    detail="No snapshot for this execution",
                )
            return snapshot.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[AUTO-CREATE] Failed to get snapshot for execution %s: %s",
            execution_id, e,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/executions/{execution_id}/rollback")
async def rollback_auto_creation_execution(
    execution_id: int,
    confirm: bool = Query(
        False,
        description=(
            "Acknowledgement of the optimistic-overwrite warning (ADR-010 §D5), "
            "REQUIRED only when the execution has a pre-run snapshot. When a "
            "snapshot exists, rollback performs a FULL restore that overwrites "
            "the current stream assignments of every snapshot channel with the "
            "pre-run state — any changes made after the run will be LOST. "
            "Executions with NO snapshot ignore this flag and keep the legacy "
            "delete-created-only behaviour (no confirm needed)."
        ),
    ),
    _admin=RequireAdminIfEnabled,
):
    """Rollback an auto-creation execution. Admin only.

    UNIFIED REVERT (ADR-010 §D8, uc51o.5). The behaviour is chosen from whether
    the execution has a pre-run snapshot:

    * **Snapshot present** — performs the FULL whole-run restore (delegates to
      the same path as ``/restore-snapshot``): re-adds streams the run removed,
      removes streams it added, restores drifted metadata. Because this is an
      OPTIMISTIC OVERWRITE that can clobber edits made after the run, it
      REQUIRES ``confirm=true``; without it the call is refused with HTTP 409
      and a message explaining the overwrite. The response carries
      ``removed_channels`` / ``restored_channels`` / ``failed_channels``.
    * **No snapshot** — the legacy delete-created-only rollback, BYTE-COMPATIBLE
      with the pre-uc51o.5 behaviour (no ``confirm`` required). The response
      carries ``entities_removed`` / ``entities_restored``.

    Status codes:
      * 409 — the execution has a snapshot and ``confirm`` was not set.
      * 400 — other guard failures (already rolled back, dry-run, nothing to
        undo).
      * 200 — rollback performed (legacy or restore shape; on the restore path
        ``success`` is False with ``failed_channels`` when any channel failed).
    """
    logger.debug(
        "[AUTO-CREATE] POST /executions/%s/rollback (confirm=%s)",
        execution_id, confirm,
    )
    try:
        from channel_pipeline_engine import get_channel_pipeline_engine, init_channel_pipeline_engine

        # Get or initialize engine
        engine = get_channel_pipeline_engine()
        if not engine:
            client = get_client()
            engine = await init_channel_pipeline_engine(client)

        result = await engine.rollback_execution(
            execution_id, rolled_back_by="api", confirm=confirm,
        )

        if not result["success"]:
            # Snapshot present + no confirm → 409 (acknowledge the overwrite,
            # uc51o.5). The restore path still returns success=False WITH
            # restore counts on a partial failure; that is handled below as a
            # 200 success-with-warnings, so only guard failures land here.
            if result.get("requires_confirm"):
                raise HTTPException(status_code=409, detail=result.get("error"))
            # A restore that was ATTEMPTED returns counts even when success is
            # False (partial failure) — let that fall through to 200. Only
            # pre-attempt guard failures (no counts) become 400.
            if "restored_channels" not in result:
                raise HTTPException(
                    status_code=400, detail=result.get("error", "Rollback failed"),
                )

        # Journal — the description adapts to whichever revert path ran. The
        # snapshot-restore path returns removed_channels/restored_channels; the
        # legacy path returns entities_removed/entities_restored.
        rule_name = result.get("rule_name", f"Execution {execution_id}")
        session = get_session()
        try:
            if "restored_channels" in result:
                # Full snapshot-restore path.
                removed = result.get("removed_channels", 0)
                restored = result.get("restored_channels", 0)
                failed = len(result.get("failed_channels", []))
                description = (
                    f"Rolled back '{rule_name}' via snapshot restore: "
                    f"removed {removed} created channel(s), "
                    f"restored {restored} channel(s)"
                    + (f", {failed} failed" if failed else "")
                )
            else:
                # Legacy delete-created-only path.
                removed = result.get("entities_removed", 0)
                restored = result.get("entities_restored", 0)
                description = (
                    f"Rolled back '{rule_name}': removed {removed} channel(s), "
                    f"restored {restored} entit{'y' if restored == 1 else 'ies'}"
                )
            journal.log_entry(
                category="auto_creation",
                action_type="rollback",
                entity_id=execution_id,
                entity_name=rule_name,
                description=description,
            )
        finally:
            session.close()

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to rollback auto-creation execution %s: %s", execution_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/executions/{execution_id}/restore-snapshot")
async def restore_auto_creation_snapshot(
    execution_id: int,
    confirm: bool = Query(
        False,
        description=(
            "Required acknowledgement of the optimistic-overwrite warning "
            "(ADR-010 §D5). Must be true. Reverting OVERWRITES the current "
            "stream assignments of every snapshot channel with the state "
            "captured before this run — ANY changes made after the run "
            "(manual edits, Dispatcharr drift) WILL BE LOST. This cannot be "
            "undone."
        ),
    ),
    _admin=RequireAdminIfEnabled,
):
    """Whole-run revert from the pre-run snapshot (ADR-010 §D8). Admin only.

    SAFETY-CRITICAL DESTRUCTIVE WRITE. This overwrites live Dispatcharr channels
    with the snapshot's pre-run stream-set + metadata (OPTIMISTIC OVERWRITE,
    §D5) — it intentionally clobbers any changes made since the snapshot was
    captured. The caller MUST have surfaced the §D5 pre-revert warning; the
    ``confirm=true`` parameter is the API-level acknowledgement so a raw call
    cannot skip it.

    Returns a structured result — ``removed_channels`` / ``restored_channels``
    counts plus a per-item ``failed_channels`` list. Partial failures are
    SURFACED (a snapshot channel deleted since the run, a vanished stream id):
    the operation does NOT abort on the first failure and does NOT report
    blanket success if any item failed.

    Status codes:
      * 400 — ``confirm`` not set (warning unacknowledged), or the execution is
        a dry-run / already reverted.
      * 404 — no snapshot for this execution (use ``/rollback`` instead).
      * 200 — restore attempted. ``success`` is False (with the failures
        listed) when any channel failed; True when all succeeded.
    """
    logger.debug(
        "[AUTO-CREATE] POST /executions/%s/restore-snapshot (confirm=%s)",
        execution_id, confirm,
    )

    if not confirm:
        # The §D5 warning is mandatory and the ONLY mitigation for the
        # overwrite risk in v1 — refuse to act without explicit acknowledgement.
        raise HTTPException(
            status_code=400,
            detail=(
                "Restore requires confirm=true. Reverting overwrites the "
                "current stream assignments of every snapshot channel with the "
                "pre-run state; any changes made after the run will be lost. "
                "This cannot be undone."
            ),
        )

    try:
        from channel_pipeline_engine import get_channel_pipeline_engine, init_channel_pipeline_engine

        engine = get_channel_pipeline_engine()
        if not engine:
            client = get_client()
            engine = await init_channel_pipeline_engine(client)

        result = await engine.restore_snapshot(execution_id, restored_by="api")

        # No snapshot → 404 with guidance to use /rollback (ADR-010 §D8 step 1).
        if result.get("no_snapshot"):
            raise HTTPException(status_code=404, detail=result.get("error"))

        # Guard failures (dry-run, already reverted, not found) → 400.
        # A restore that was ATTEMPTED returns success/failure with counts;
        # only the pre-attempt guards carry no count keys.
        if not result.get("success") and "restored_channels" not in result:
            error = result.get("error", "Restore failed")
            status = 404 if "not found" in error.lower() else 400
            raise HTTPException(status_code=status, detail=error)

        # Journal the restore (mirrors the rollback endpoint's journaling).
        rule_name = result.get("rule_name", f"Execution {execution_id}")
        removed = result.get("removed_channels", 0)
        restored = result.get("restored_channels", 0)
        failed = len(result.get("failed_channels", []))
        session = get_session()
        try:
            journal.log_entry(
                category="auto_creation",
                action_type="restore_snapshot",
                entity_id=execution_id,
                entity_name=rule_name,
                description=(
                    f"Restored snapshot for '{rule_name}': "
                    f"removed {removed} created channel(s), "
                    f"restored {restored} channel(s)"
                    + (f", {failed} failed" if failed else "")
                ),
            )
        finally:
            session.close()

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[AUTO-CREATE] Failed to restore snapshot for execution %s: %s",
            execution_id, e,
        )
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
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rules = session.query(ChannelPipelineRule).order_by(
                ChannelPipelineRule.priority
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
                _qto = getattr(rule, "quality_tie_break_order", None)
                if isinstance(_qto, str) and _qto.strip():
                    yaml_quality_tie = _qto.strip().lower()
                    if yaml_quality_tie not in ("asc", "desc"):
                        yaml_quality_tie = "desc"
                else:
                    yaml_quality_tie = "desc"

                _qmte = getattr(rule, "quality_m3u_tie_break_enabled", True)
                yaml_quality_m3u_enabled = (
                    _qmte if isinstance(_qmte, bool) else True
                )

                rule_dict = {
                    "name": rule.name,
                    "description": rule.description,
                    "enabled": rule.enabled,
                    "priority": rule.priority,
                    "active_from": (
                        rule.active_from.isoformat()
                        if isinstance(rule.active_from, date) else None
                    ),
                    "active_until": (
                        rule.active_until.isoformat()
                        if isinstance(rule.active_until, date) else None
                    ),
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
                    "quality_tie_break_order": yaml_quality_tie,
                    "quality_m3u_tie_break_enabled": yaml_quality_m3u_enabled,
                    "normalization_group_ids": rule.get_normalization_group_ids(),
                    "skip_struck_streams": rule.skip_struck_streams or False,
                    "probe_on_sort": rule.probe_on_sort or False,
                    "orphan_action": rule.orphan_action or "delete",
                    "match_scope_target_group": rule.match_scope_target_group or False,
                    "match_scope_group_id": rule.match_scope_group_id,
                    "allow_manual_channel_merge": rule.allow_manual_channel_merge or False,
                    "fold_match_key": rule.fold_match_key or False,
                    # ti939.1.3 (PR #612 review): export the event_sync KIND —
                    # omitting it would make an export→import round-trip
                    # resurrect the rule as a standard rule whose dormant
                    # conditions/actions execute.
                    "event_sync_config": rule.get_event_sync_config(),
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
async def import_auto_creation_rules_yaml(request: ImportYAMLRequest, _admin=RequireAdminIfEnabled):
    """Import auto-creation rules from YAML.

    Supports portable name fields: if group_name/target_group_name/m3u_account_name
    are present and corresponding IDs are missing, names are resolved to local IDs.
    Explicit IDs always take priority over names.
    """
    logger.debug("[AUTO-CREATE-YAML] POST /import/yaml - overwrite=%s", request.overwrite)
    try:
        import yaml
        from models import ChannelPipelineRule
        from channel_pipeline_schema import validate_rule

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

                # ti939.1.3 (PR #612 review): an imported event_sync_config
                # MUST route through the same write-time validator as
                # POST/PUT — an unvalidated import path would bypass the
                # schema-enforced scoping rail. Invalid configs reject the
                # rule with the standard import error shape. Also fills
                # defaults in place so the stored JSON is explicit.
                event_sync_config = rule_data.get("event_sync_config")
                if event_sync_config is not None:
                    from channel_pipeline_schema import validate_event_sync_config
                    es_errors = validate_event_sync_config(event_sync_config)
                    if es_errors:
                        errors.append({
                            "rule_index": i,
                            "rule_name": rule_data.get("name", f"Rule {i}"),
                            "errors": es_errors
                        })
                        continue

                try:
                    active_from = _parse_yaml_active_date(
                        rule_data.get("active_from"), "active_from"
                    )
                    active_until = _parse_yaml_active_date(
                        rule_data.get("active_until"), "active_until"
                    )
                    if (
                        active_from is not None
                        and active_until is not None
                        and active_until < active_from
                    ):
                        raise ValueError(
                            "active_until must be on or after active_from"
                        )
                except ValueError as exc:
                    errors.append({
                        "rule_index": i,
                        "rule_name": rule_data.get("name", f"Rule {i}"),
                        "errors": [str(exc)],
                    })
                    continue

                # Check if rule with same name exists
                existing = session.query(ChannelPipelineRule).filter(
                    ChannelPipelineRule.name == rule_data.get("name")
                ).first()

                if existing:
                    if request.overwrite:
                        # Update existing rule
                        existing.description = rule_data.get("description")
                        existing.enabled = rule_data.get("enabled", True)
                        existing.priority = rule_data.get("priority", 0)
                        existing.active_from = active_from
                        existing.active_until = active_until
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
                        existing.quality_tie_break_order = rule_data.get("quality_tie_break_order", "desc")
                        if "quality_m3u_tie_break_enabled" in rule_data:
                            existing.quality_m3u_tie_break_enabled = bool(
                                rule_data.get("quality_m3u_tie_break_enabled")
                            )
                        existing.normalization_group_ids = _resolve_normalization_group_ids(rule_data, session)
                        existing.skip_struck_streams = rule_data.get("skip_struck_streams", False)
                        existing.probe_on_sort = rule_data.get("probe_on_sort", False)
                        existing.orphan_action = rule_data.get("orphan_action", "delete")
                        existing.match_scope_target_group = rule_data.get("match_scope_target_group", True)
                        existing.match_scope_group_id = rule_data.get("match_scope_group_id")
                        existing.allow_manual_channel_merge = rule_data.get("allow_manual_channel_merge", False)
                        existing.fold_match_key = rule_data.get("fold_match_key", False)
                        # ti939.1.3: preserve (or clear) the event_sync KIND
                        # on overwrite-import. Import-update overwrites every
                        # field unconditionally, so an exported standard rule
                        # correctly imports as standard (None clears).
                        existing.set_event_sync_config(event_sync_config)
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
                    rule = ChannelPipelineRule(
                        name=rule_data.get("name", f"Imported Rule {i}"),
                        description=rule_data.get("description"),
                        enabled=rule_data.get("enabled", True),
                        priority=rule_data.get("priority", 0),
                        active_from=active_from,
                        active_until=active_until,
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
                        quality_tie_break_order=rule_data.get("quality_tie_break_order", "desc"),
                        quality_m3u_tie_break_enabled=bool(rule_data.get("quality_m3u_tie_break_enabled", True)),
                        normalization_group_ids=_resolve_normalization_group_ids(rule_data, session),
                        skip_struck_streams=rule_data.get("skip_struck_streams", False),
                        probe_on_sort=rule_data.get("probe_on_sort", False),
                        orphan_action=rule_data.get("orphan_action", "delete"),
                        match_scope_target_group=rule_data.get("match_scope_target_group", True),
                        match_scope_group_id=rule_data.get("match_scope_group_id"),
                        allow_manual_channel_merge=rule_data.get("allow_manual_channel_merge", False),
                        fold_match_key=rule_data.get("fold_match_key", False),
                        # ti939.1.3: keep the event_sync KIND on import-create
                        # (validated above; None = standard kind).
                        event_sync_config=(
                            json.dumps(event_sync_config)
                            if event_sync_config else None
                        ),
                    )
                    session.add(rule)
                    logger.debug("[AUTO-CREATE-YAML] Rule '%s': created new, stored actions=%s", rule_name, rule.actions)
                    imported.append({"name": rule.name, "action": "created"})

            session.commit()

            # De-duplicate priorities: if any rules share the same priority,
            # re-assign sequential priorities preserving relative order (by id)
            all_rules = session.query(ChannelPipelineRule).order_by(
                ChannelPipelineRule.priority, ChannelPipelineRule.id
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


# =============================================================================
# Component B — no-write scored fuzzy preview (enhancedchannelmanager-jnzst)
# =============================================================================

# DoS guard ceilings. The preview is an N×M scoring pass (every stream against
# every channel in the scoped groups); without caps a broad group set could be
# turned into a CPU-amplification vector. These bound the worst case.
_PREVIEW_MAX_GROUPS = 25
_PREVIEW_MAX_STREAMS = 2000
_PREVIEW_MAX_CHANNELS = 2000
_PREVIEW_FETCH_PAGE_SIZE = 500


class ScoredTriple(BaseModel):
    """One scored (stream, channel) pair surfaced by the preview."""

    stream_id: int
    stream_name: str
    channel_id: str
    channel_name: str
    score: float
    callsign_verdict: str  # "match" | "conflict" | "absent"
    signal: str


class FuzzyPreviewResponse(BaseModel):
    """Paginated, write-free preview of scored fuzzy matches."""

    triples: List[ScoredTriple]
    total: int
    page: int
    page_size: int
    total_pages: int
    min_score: float
    truncated: bool  # True when the candidate pool hit a DoS ceiling


@router.get("/fuzzy-preview", response_model=FuzzyPreviewResponse)
async def preview_fuzzy_matches(
    group_ids: List[int] = Query(
        ..., description="Channel-group IDs to scope the preview to (non-empty)"
    ),
    min_score: float = Query(
        ..., ge=0.0, le=1.0,
        description="Minimum score to include a triple. May be below the 0.60 "
                    "confidence floor here — the preview deliberately exposes "
                    "sub-floor scores for inspection (it never writes).",
    ),
    allow_no_callsign: bool = Query(
        False,
        description="Q1 opt-in. When True, a no-callsign ('absent') pair is "
                    "admissible at score >= 0.90 (NO_CALLSIGN_FLOOR). Default "
                    "False = require a parseable callsign on both sides. An M1 "
                    "callsign 'conflict' is NEVER admissible regardless.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin=RequireAdminIfEnabled,
) -> FuzzyPreviewResponse:
    """Score streams against channels in the given groups — ZERO writes.

    This is the read-only sibling of the scored-fuzzy rule path: it runs the
    SAME shared core (``services.dedup_matcher.score_all``) AND the SAME
    admission policy (``services.dedup_matcher.is_admissible``) so operators can
    inspect exactly what a rule WOULD do before committing. It never calls any
    mutating Dispatcharr method.

    Admission (FIX 2 — single source of truth). Returned triples are
    ADMISSIBLE-only: an M1 callsign ``conflict`` is NEVER returned (even at
    ``min_score == 0``), and a no-callsign ``absent`` pair is returned only when
    ``allow_no_callsign`` is True and its score reaches ``NO_CALLSIGN_FLOOR``
    (0.90). This is what makes the two MCP write tools (which assign purely on
    ``score`` from these triples) inherit M1/M2 with no consumer-side policy —
    they can only ever see admissible pairs.

    The ``min_score`` floor clamp is deliberately NOT applied here (unlike the
    dedup candidate lookup): a sub-0.60 ``min_score`` lets an operator see
    borderline ``match``-verdict pairs. ``conflict``/``absent`` admission is
    still governed by the shared policy, so sub-floor ``min_score`` cannot
    re-admit an incident-class false positive.

    Validation (DoS hardening): ``group_ids`` must be non-empty (an empty list
    would mean "all groups" → an unbounded N×M scoring pass), free of
    duplicates and negatives, and capped at ``_PREVIEW_MAX_GROUPS``. The fetched
    stream and channel pools are each capped; hitting a cap sets ``truncated``.
    """
    # --- Validate group_ids (reject empty=all, dup, negative; cap count) ---
    if not group_ids:
        raise HTTPException(status_code=400, detail="group_ids must be non-empty")
    if any(g < 0 for g in group_ids):
        raise HTTPException(status_code=400, detail="group_ids must be non-negative")
    if len(set(group_ids)) != len(group_ids):
        raise HTTPException(status_code=400, detail="group_ids must not contain duplicates")
    if len(group_ids) > _PREVIEW_MAX_GROUPS:
        raise HTTPException(
            status_code=400,
            detail=f"group_ids exceeds the cap of {_PREVIEW_MAX_GROUPS}",
        )

    client = get_client()
    allowed = set(group_ids)
    truncated = False

    # --- Fetch channels in the scoped groups (capped) ---
    channels: list[dict] = []
    try:
        for gid in group_ids:
            cpage = 1
            while True:
                resp = await client.get_channels(
                    page=cpage, page_size=_PREVIEW_FETCH_PAGE_SIZE, channel_group=gid
                )
                batch = resp.get("results", []) if isinstance(resp, dict) else (resp or [])
                channels.extend(batch)
                if len(channels) >= _PREVIEW_MAX_CHANNELS:
                    channels = channels[:_PREVIEW_MAX_CHANNELS]
                    truncated = True
                    break
                if not isinstance(resp, dict) or not resp.get("next"):
                    break
                cpage += 1
            if truncated:
                break
    except Exception as e:
        logger.warning("[AUTO-CREATE] fuzzy-preview channel fetch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # --- Fetch streams in the scoped groups (capped) ---
    streams: list[dict] = []
    try:
        group_names = [
            await client._channel_group_name_for_id(gid) for gid in group_ids
        ]
        for gname in group_names:
            if not gname or truncated:
                continue
            spage = 1
            while True:
                resp = await client.get_streams(
                    page=spage, page_size=_PREVIEW_FETCH_PAGE_SIZE,
                    channel_group_name=gname,
                )
                batch = resp.get("results", []) if isinstance(resp, dict) else (resp or [])
                streams.extend(batch)
                if len(streams) >= _PREVIEW_MAX_STREAMS:
                    streams = streams[:_PREVIEW_MAX_STREAMS]
                    truncated = True
                    break
                if not isinstance(resp, dict) or not resp.get("next"):
                    break
                spage += 1
    except Exception as e:
        logger.warning("[AUTO-CREATE] fuzzy-preview stream fetch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # Build the candidate (channel) list scoped to the allowed groups and the
    # per-candidate tvg_id map for the override rung.
    candidates: list[tuple[str, str]] = []
    cand_tvg: dict[str, str] = {}
    for ch in channels:
        if ch.get("channel_group_id") not in allowed:
            continue
        cid, cname = ch.get("id"), ch.get("name")
        if cid is None or not cname:
            continue
        candidates.append((str(cid), cname))
        if ch.get("tvg_id"):
            cand_tvg[str(cid)] = ch["tvg_id"]

    # Score every (stream, candidate) pair via the shared core. Offload the
    # CPU-bound scoring off the event loop.
    def _score_all_pairs() -> list[ScoredTriple]:
        out: list[ScoredTriple] = []
        for s in streams:
            sid, sname = s.get("id"), s.get("name")
            if sid is None or not sname:
                continue
            stvg = s.get("tvg_id")
            for cid, cname, sm in score_all(
                sname, candidates,
                stream_tvg_id=stvg, candidate_tvg_ids=cand_tvg,
                mode=NameCleanMode.LOCALS,
            ):
                # Shared admission policy — conflict never returned, absent only
                # at >= NO_CALLSIGN_FLOOR when allow_no_callsign, match at
                # >= min_score (FIX 2). The MCP write tools inherit M1/M2 here.
                if not is_admissible(
                    sm, min_score=min_score, allow_no_callsign=allow_no_callsign
                ):
                    continue
                out.append(ScoredTriple(
                    stream_id=int(sid), stream_name=sname,
                    channel_id=cid, channel_name=cname,
                    score=round(sm.score, 4),
                    callsign_verdict=sm.callsign_verdict,
                    signal=sm.signal,
                ))
        # Highest score first, deterministic tie-break on ids.
        out.sort(key=lambda t: (-t.score, t.stream_id, t.channel_id))
        return out

    all_triples = await run_cpu_bound(_score_all_pairs)

    # --- REAL pagination ---
    total = len(all_triples)
    total_pages = (total + page_size - 1) // page_size if total else 0
    start = (page - 1) * page_size
    page_slice = all_triples[start:start + page_size]

    logger.debug(
        "[AUTO-CREATE] fuzzy-preview groups=%s streams=%d channels=%d "
        "min_score=%.2f total=%d page=%d truncated=%s",
        group_ids, len(streams), len(candidates), min_score, total, page, truncated,
    )

    return FuzzyPreviewResponse(
        triples=page_slice,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        min_score=min_score,
        truncated=truncated,
    )


# =============================================================================
# Event Sync preview — dry-run matching, ZERO writes (bead ti939.1.4)
# =============================================================================

# Per-stream candidate cap: candidates arrive best-first from the matcher, so
# truncation only trims the diagnostic tail. Stream-level counts (which is
# what reconciles against the summary) are unaffected.
_EVENT_PREVIEW_MAX_CANDIDATES_PER_STREAM = 10
# Sample cap inside one parse-failure group; ``count`` always carries the
# full total so a silently broken pattern stays loud even when sampled.
_EVENT_PREVIEW_MAX_FAILURE_SAMPLES = 25


class EventSyncPreviewRequest(BaseModel):
    """Preview an event_sync rule: a saved rule id OR an inline config.

    Exactly one source must be provided — ``event_sync_config`` exists so the
    rule editor can preview BEFORE saving (bead ti939.1.4).
    """

    rule_id: Optional[int] = None
    event_sync_config: Optional[dict] = None

    @model_validator(mode="after")
    def _exactly_one_source(self):
        if (self.rule_id is None) == (self.event_sync_config is None):
            raise ValueError(
                "provide exactly one of rule_id (preview a saved rule) or "
                "event_sync_config (preview before saving)"
            )
        return self


async def _load_event_sync_preview_config(request: EventSyncPreviewRequest) -> dict:
    """Resolve + validate the config to preview (saved rule or inline)."""
    from channel_pipeline_schema import validate_event_sync_config

    if request.rule_id is not None:
        from models import ChannelPipelineRule
        session = get_session()
        try:
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == request.rule_id
            ).first()
            if not rule:
                raise HTTPException(status_code=404, detail="Rule not found")
            config = rule.get_event_sync_config()
            if config is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Rule {request.rule_id} is not an event_sync rule "
                        f"(no event_sync_config)"
                    ),
                )
        finally:
            session.close()
    else:
        # Shallow copy: the validator fills defaults in place and the
        # request object must not be mutated.
        config = dict(request.event_sync_config)

    # Inline configs are unvalidated by definition; stored configs are
    # re-validated so a config predating a schema rail (or hand-edited via
    # import) fails loudly here instead of previewing under stale semantics.
    errors = validate_event_sync_config(config)
    if errors:
        raise HTTPException(status_code=400, detail={
            "message": "Invalid event_sync_config",
            "errors": errors,
        })
    return config


async def _fetch_and_resolve_event_sync(
    config: dict,
    client,
    effective_master_group_id: int,
    *,
    decisions=None,
    exclusions=None,
) -> dict:
    """Fetch master channels + secondary streams and resolve them — the ONE
    fetch/resolve path shared by the preview endpoint and the debug-bundle
    matching-diagnostics section (bead 03nji).

    ZERO-WRITE: only READ Dispatcharr methods are called, and scoring runs
    through ``services.event_sync_resolver.resolve_event_sync`` — the same
    pure matcher the attach path uses. No scoring/band/attach policy lives
    here; this helper only gathers the resolver's inputs and returns its
    output, so preview and the debug bundle cannot fork a second matcher path.

    ``effective_master_group_id`` is the Channel-Group-Override-resolved id
    the caller already computed from the pre-flight group settings.

    Returns a dict: ``resolution`` (EventSyncResolution), ``name_to_id``,
    ``master_names``, ``master_channels``, ``group_names``, ``truncated``.
    """
    from functools import partial

    from fastapi.concurrency import run_in_threadpool

    from services import event_sync_staleness
    from services.event_sync_resolver import (
        SecondaryStream,
        build_master_name_to_id,
        resolve_event_sync,
    )
    from stream_prober import extract_m3u_account_id

    master_group_id = config["master_group_id"]
    secondary_group_ids = config["secondary_group_ids"]
    truncated = False

    # --- Fetch the master group's channels (capped, paginated) -----------
    master_channels: list[dict] = []
    try:
        cpage = 1
        while True:
            resp = await client.get_channels(
                page=cpage, page_size=_PREVIEW_FETCH_PAGE_SIZE,
                channel_group=effective_master_group_id,
            )
            batch = resp.get("results", []) if isinstance(resp, dict) else (resp or [])
            master_channels.extend(
                ch for ch in batch
                if ch.get("channel_group_id") == effective_master_group_id
            )
            if len(master_channels) >= _PREVIEW_MAX_CHANNELS:
                master_channels = master_channels[:_PREVIEW_MAX_CHANNELS]
                truncated = True
                break
            if not isinstance(resp, dict) or not resp.get("next"):
                break
            cpage += 1
    except Exception as e:
        logger.warning("[EVENT-SYNC] master-channel fetch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # Identity name -> current channel id, re-resolved on THIS call (stateless
    # recompute; the matcher never sees ids). bead parse-from-stream: identity
    # is the attached stream's name when parse_master_from_stream is on.
    try:
        name_to_id = await build_master_name_to_id(
            master_channels, client,
            bool(config.get("parse_master_from_stream")),
        )
    except Exception as e:
        logger.warning("[EVENT-SYNC] master identity build failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    master_names = sorted(name_to_id)

    # Master-group reality echo (bead at41p) — when master_group_id and the
    # effective (Channel-Group-Override-resolved) id diverge, "I set the master
    # to X" and "the matcher read group Y" are different facts; log the remap
    # plus how many channels/identities that group actually yielded, so an empty
    # or generic master group is visible at the fetch boundary rather than only
    # inferable from a downstream usable=0. Observability only.
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "[EVENT-SYNC] fetch master_group=%s effective_master_group=%s "
            "master_channels=%d master_identities=%d "
            "parse_master_from_stream=%s truncated=%s",
            master_group_id, effective_master_group_id,
            len(master_channels), len(master_names),
            bool(config.get("parse_master_from_stream")), truncated,
        )

    # --- Fetch the secondary groups' streams (capped, paginated) ---------
    group_names: dict[int, str | None] = {}
    secondary_streams: list[SecondaryStream] = []
    try:
        accounts = await client.get_m3u_accounts() or []
        account_names = {a.get("id"): a.get("name") for a in accounts}
        # bead jqwfq Stage 1: previous-day stream-name membership per account
        # (latest pre-midnight M3USnapshot), feeding each SecondaryStream's
        # name_seen_before_today staleness signal. Sync DB read → threadpool.
        # Best-effort: a failed lookup means freshness unknown (None flags),
        # never a failed preview — the rail fails open by construction.
        stale_lookup: dict = {}
        try:
            stale_lookup = await run_in_threadpool(
                event_sync_staleness.previous_day_names,
                [a.get("id") for a in accounts if a.get("id") is not None],
                event_sync_staleness.local_midnight_utc(),
            )
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] staleness lookup failed (%s) — stream-name "
                "freshness unknown for this preview", e,
            )
        # bead jiscc: provider-scoped secondary scopes. m3u_account_id filters
        # the fetch to one provider; None = the whole group.
        secondary_scopes = config.get("secondary") or [
            {"group_id": g, "m3u_account_id": None}
            for g in secondary_group_ids
        ]
        for scope in secondary_scopes:
            gid = scope["group_id"]
            provider_filter = scope.get("m3u_account_id")
            gname = await client._channel_group_name_for_id(gid)
            group_names[gid] = gname
            if not gname:
                logger.warning(
                    "[EVENT-SYNC] secondary group %s has no resolvable "
                    "channel-group name; skipping fetch", gid,
                )
                continue
            if truncated:
                break
            spage = 1
            while True:
                resp = await client.get_streams(
                    page=spage, page_size=_PREVIEW_FETCH_PAGE_SIZE,
                    channel_group_name=gname,
                    m3u_account=provider_filter,
                )
                batch = resp.get("results", []) if isinstance(resp, dict) else (resp or [])
                for s in batch:
                    # Mirror the engine fetch's name+id guard: a no-id stream
                    # is unattachable and must not diverge dry-run parity.
                    if not s.get("name") or s.get("id") is None:
                        continue
                    account_id = extract_m3u_account_id(s.get("m3u_account"))
                    secondary_streams.append(SecondaryStream(
                        name=s["name"],
                        group_id=gid,
                        stream_id=s.get("id"),
                        provider=account_names.get(account_id),
                        provider_id=account_id,
                        # bead jqwfq: True/False/None staleness signal —
                        # snapshot groups are keyed by group NAME.
                        name_seen_before_today=(
                            event_sync_staleness.name_seen_before_today(
                                stale_lookup, account_id, gname, s["name"],
                            )
                        ),
                    ))
                if len(secondary_streams) >= _PREVIEW_MAX_STREAMS:
                    secondary_streams = secondary_streams[:_PREVIEW_MAX_STREAMS]
                    truncated = True
                    break
                if not isinstance(resp, dict) or not resp.get("next"):
                    break
                spage += 1
        # bead 6xxmp: optionally fetch the MASTER group's own streams as a
        # self-attach source (group_id = master_group_id).
        if config.get("include_master_group_streams") and not truncated:
            mgname = await client._channel_group_name_for_id(master_group_id)
            group_names[master_group_id] = mgname
            if not mgname:
                logger.warning(
                    "[EVENT-SYNC] master group %s has no resolvable "
                    "channel-group name; skipping self-attach fetch",
                    master_group_id,
                )
            else:
                spage = 1
                while True:
                    resp = await client.get_streams(
                        page=spage, page_size=_PREVIEW_FETCH_PAGE_SIZE,
                        channel_group_name=mgname,
                    )
                    batch = resp.get("results", []) if isinstance(resp, dict) else (resp or [])
                    for s in batch:
                        if not s.get("name") or s.get("id") is None:
                            continue
                        account_id = extract_m3u_account_id(s.get("m3u_account"))
                        secondary_streams.append(SecondaryStream(
                            name=s["name"],
                            group_id=master_group_id,
                            stream_id=s.get("id"),
                            provider=account_names.get(account_id),
                            provider_id=account_id,
                            # bead jqwfq: same staleness signal for the
                            # master-group self-attach source.
                            name_seen_before_today=(
                                event_sync_staleness.name_seen_before_today(
                                    stale_lookup, account_id, mgname,
                                    s["name"],
                                )
                            ),
                        ))
                    if len(secondary_streams) >= _PREVIEW_MAX_STREAMS:
                        secondary_streams = secondary_streams[:_PREVIEW_MAX_STREAMS]
                        truncated = True
                        break
                    if not isinstance(resp, dict) or not resp.get("next"):
                        break
                    spage += 1
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[EVENT-SYNC] stream fetch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # bead 6xxmp: stream ids already on a master channel — the resolver drops
    # the master group's own already-attached streams so preview's would-attach
    # count matches the run's attach count.
    attached_stream_ids: set[int] = set()
    for ch in master_channels:
        for s in ch.get("streams", []):
            sid = s["id"] if isinstance(s, dict) else s
            if sid is not None:
                attached_stream_ids.add(sid)

    # --- Resolve (CPU-bound scoring off the event loop) ------------------
    resolution = await run_cpu_bound(
        partial(
            resolve_event_sync, config, master_names, secondary_streams,
            decisions=decisions, exclusions=exclusions,
            attached_stream_ids=attached_stream_ids,
        )
    )
    return {
        "resolution": resolution,
        "name_to_id": name_to_id,
        "master_names": master_names,
        "master_channels": master_channels,
        "group_names": group_names,
        "truncated": truncated,
        # bead 2ey2y: the previous-day snapshot lookup, exposed so callers can
        # compute snapshot COVERAGE over the resolved rows (inert-rail
        # warning) — empty dict when no account had a qualifying snapshot or
        # the lookup itself failed (fail-open).
        "stale_lookup": stale_lookup,
    }


@router.post("/event-sync-preview")
async def preview_event_sync(
    request: EventSyncPreviewRequest, _admin=RequireAdminIfEnabled
):
    """Dry-run event matching against live master channels — ZERO writes.

    Phase 1A of Event Sync (epic ti939): runs the read-only pre-flight, then
    fetches the master group's channels and the secondary groups' streams
    from Dispatcharr and resolves matches through
    ``services.event_sync_resolver.resolve_event_sync`` — the EXACT function
    the Phase 1B attach path will call, so preview scoring and future attach
    scoring cannot diverge (dry-run parity by construction).

    A pre-flight failure does NOT block the preview — the operator must see
    the misconfiguration alongside what the matcher would still do. This
    endpoint never calls any mutating Dispatcharr method: no merges, no
    channel mutations, and it never toggles Dispatcharr group settings.

    Summary counts (would_attach / ambiguous_skipped / unmatched /
    parse_failed / excluded_by_operator) reconcile exactly with the
    ``streams`` detail rows: each stream carries exactly one disposition
    and the five counts sum to ``secondary_streams``. Master channels are re-resolved by name on every
    call (stateless recompute — duplicate-named masters map to the lowest
    channel id, deterministically).
    """
    from services.event_sync_preflight import (
        build_event_sync_master_rule_lookup,
        build_staleness_rail_warning,
        check_event_sync_group_settings,
        count_snapshot_covered_streams,
        resolve_effective_master_group_id,
    )
    from services.event_sync_exclusion_store import load_exclusion_keys
    from services.event_sync_resolver import (
        DISPOSITION_AMBIGUOUS,
        DISPOSITION_EXCLUDED,
        DISPOSITION_PARSE_FAILED,
        DISPOSITION_UNMATCHED,
        DISPOSITION_WOULD_ATTACH,
    )
    from services.event_sync_review import (
        ATTACH_SOURCE_REVIEW_QUEUE,
        PROVIDER_ID_UNKNOWN,
        REVIEW_STATUS_ACCEPTED,
        REVIEW_STATUS_PENDING,
        REVIEW_STATUS_REJECTED,
        master_event_key,
        stream_name_hash,
    )
    from services.event_sync_review_store import (
        load_pending_fingerprints,
        load_review_decisions,
    )

    config = await _load_event_sync_preview_config(request)
    master_group_id = config["master_group_id"]
    secondary_group_ids = config["secondary_group_ids"]
    client = get_client()

    # ti939.3.2: review-queue state for a SAVED rule. Decisions feed the
    # shared resolver (so the preview predicts exactly what a run would do
    # — accepted pairings show as would_attach via review_queue, rejected
    # ones are suppressed); pending fingerprints only decorate candidate
    # rows. Inline (unsaved) configs have no rule id and therefore no
    # queue state — matching the run they could ever produce.
    decisions = None
    pending_fps: frozenset = frozenset()
    exclusion_keys: frozenset = frozenset()
    if request.rule_id is not None:
        session = get_session()
        try:
            decisions = load_review_decisions(session, request.rule_id)
            pending_fps = load_pending_fingerprints(session, request.rule_id)
            # ti939.3.5: operator never-attach exclusions feed the SAME
            # shared resolver, so the preview predicts exactly what a run
            # would suppress (excluded pairings report as
            # excluded_by_operator, never as would_attach).
            exclusion_keys = load_exclusion_keys(session, request.rule_id)
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] preview: failed to load review-queue state "
                "for rule %s (%s) — previewing without it",
                request.rule_id, e,
            )
            decisions = None
            pending_fps = frozenset()
            exclusion_keys = frozenset()
        finally:
            session.close()

    # --- Pre-flight (READ-ONLY; failures surface, never block) -----------
    # bead yjchp: cross-rule context — when a failing secondary group is
    # ANOTHER enabled event_sync rule's MASTER, the failure message must
    # not advise disabling auto_channel_sync (masters require it ON).
    # Best-effort: a lookup failure falls back to the generic messages.
    other_master_rules: dict = {}
    try:
        from models import ChannelPipelineRule

        session = get_session()
        try:
            enabled_rules = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.enabled == True  # noqa: E712
            ).all()
            other_master_rules = build_event_sync_master_rule_lookup(
                enabled_rules, exclude_rule_id=request.rule_id
            )
        finally:
            session.close()
    except Exception as e:
        logger.warning(
            "[EVENT-SYNC] preview: failed to build cross-rule pre-flight "
            "context (%s) — pre-flight messages fall back to the generic "
            "advice", e,
        )

    # Fetch group settings ONCE and reuse for both the pre-flight and the
    # Channel Group Override resolution below (bead override).
    try:
        all_settings = await client.get_all_m3u_group_settings()
        preflight = await check_event_sync_group_settings(
            client, config, all_settings=all_settings,
            other_master_rules=other_master_rules,
        )
    except Exception as e:
        logger.warning("[EVENT-SYNC] preview pre-flight failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # Follow a Channel Group Override: when the master group is an auto-synced
    # SOURCE, Dispatcharr places its channels in the override TARGET group, so
    # the master channels must be fetched from there — not the raw
    # master_group_id, which would come back empty (bead override).
    effective_master_group_id = resolve_effective_master_group_id(
        all_settings, master_group_id
    )

    # Shared fetch + resolve (bead 03nji) — the ONE fetch/resolve path the
    # preview endpoint and the debug-bundle matching diagnostics both call, so
    # a second resolver invocation can never drift from this one. Master
    # channels, secondary streams, name→id map, and the CPU-bound resolve all
    # live in the helper; the endpoint keeps only the request-shaped
    # serialization below.
    fetched = await _fetch_and_resolve_event_sync(
        config, client, effective_master_group_id, decisions=decisions,
        exclusions=exclusion_keys,
    )
    resolution = fetched["resolution"]
    name_to_id = fetched["name_to_id"]
    group_names = fetched["group_names"]
    master_channels = fetched["master_channels"]
    truncated = fetched["truncated"]

    # bead 2ey2y: inert-rail warning — when assume_current_date +
    # demote_stale_dateless are both on but NO previous-day snapshot covers
    # any resolved secondary stream, the stale-dateless guard fails open
    # silently. Surface that as an explicit pre-flight WARNING (never a
    # failure — nothing is misconfigured; the rail just has no data yet).
    snapshot_covered = count_snapshot_covered_streams(
        resolution.resolved, group_names, fetched["stale_lookup"]
    )
    staleness_warning = build_staleness_rail_warning(
        config,
        secondary_stream_count=len(resolution.resolved),
        snapshot_covered_count=snapshot_covered,
    )
    if staleness_warning is not None:
        preflight["warnings"].append(staleness_warning)

    # --- Build detail rows; counts derive from the SAME iteration so they
    # reconcile with the rows by construction. --------------------------
    counts = {
        DISPOSITION_WOULD_ATTACH: 0,
        DISPOSITION_AMBIGUOUS: 0,
        DISPOSITION_UNMATCHED: 0,
        DISPOSITION_PARSE_FAILED: 0,
        # ti939.3.5: operator never-attach exclusions — fifth disposition;
        # the five counts sum to secondary_streams.
        DISPOSITION_EXCLUDED: 0,
    }
    streams_out: list[dict] = []
    unmatched_out: list[dict] = []
    failure_groups: dict[tuple[int, str], dict] = {}
    would_attach_via_review = 0
    candidates_pending_review = 0
    # bead jqwfq Stage 1: staleness-signal summary — how many stream names
    # positively predate today (stale-suspect) and how many have unknown
    # freshness (no snapshot / capped group / unknown provider).
    stale_suspect_streams = 0
    freshness_unknown_streams = 0

    # ti939.3.2: per-candidate queue markers ('pending review' /
    # 'previously accepted/rejected'), computed from the SAME fingerprints
    # the resolver keys decisions on.
    has_queue_state = bool(pending_fps) or (
        decisions is not None and (decisions.accepted or decisions.rejected)
    )

    def _candidate_review_status(resolved, candidate) -> str | None:
        if not has_queue_state:
            return None
        event_key = master_event_key(candidate.parsed)
        if event_key is None:
            return None
        provider = (
            resolved.stream.provider_id
            if resolved.stream.provider_id is not None
            else PROVIDER_ID_UNKNOWN
        )
        fp = (provider, stream_name_hash(resolved.stream.name), event_key)
        if decisions is not None and fp in decisions.accepted:
            return REVIEW_STATUS_ACCEPTED
        if decisions is not None and fp in decisions.rejected:
            return REVIEW_STATUS_REJECTED
        if fp in pending_fps:
            return REVIEW_STATUS_PENDING
        return None

    # ti939.3.5: per-candidate operator-exclusion marker, computed from the
    # SAME fingerprints the resolver filters on (candidates render from the
    # UNFILTERED matcher result, so an excluded pairing stays visible in
    # the table with this marker — transparency, mirroring rejected rows).
    def _candidate_excluded(resolved, candidate) -> bool:
        if not exclusion_keys:
            return False
        event_key = master_event_key(candidate.parsed)
        if event_key is None:
            return False
        provider = (
            resolved.stream.provider_id
            if resolved.stream.provider_id is not None
            else PROVIDER_ID_UNKNOWN
        )
        fp = (provider, stream_name_hash(resolved.stream.name), event_key)
        return fp in exclusion_keys

    for r in resolution.resolved:
        counts[r.disposition] += 1
        if r.disposition == DISPOSITION_WOULD_ATTACH \
                and r.attach_source == ATTACH_SOURCE_REVIEW_QUEUE:
            would_attach_via_review += 1
        if r.stream.name_seen_before_today is True:
            stale_suspect_streams += 1
        elif r.stream.name_seen_before_today is None:
            freshness_unknown_streams += 1
        parsed_start = (
            r.result.parsed.start.isoformat() if r.result.parsed.start else None
        )
        streams_out.append({
            "stream_id": r.stream.stream_id,
            "stream_name": r.stream.name,
            "group_id": r.stream.group_id,
            "provider": r.stream.provider,
            "parsed_title": r.result.parsed.title,
            "parsed_start": parsed_start,
            "matched_pattern": r.result.parsed.matched_pattern,
            "disposition": r.disposition,
            "unmatchable_reason": r.result.unmatchable_reason,
            # ti939.2.1: machine-readable reason when disposition is
            # "ambiguous" — "contested_top_candidates" (the PR #613 contested
            # rail) or "top_candidate_ambiguous_band". Preview inherits the
            # rail automatically (same resolver as the attach path).
            "ambiguous_reason": r.ambiguous_reason,
            # bead jqwfq Stage 1: tri-state staleness signal — True (name
            # present in the account's previous-day M3U snapshot: stale
            # suspect), False (captured uncapped and absent: seen first
            # today), None (unknown — fail-open).
            "name_seen_before_today": r.stream.name_seen_before_today,
            # ti939.3.2: "threshold" | "review_queue" when would_attach —
            # a queue-driven attach prediction is visibly not a score-driven
            # one. None for every other disposition.
            "attach_source": r.attach_source,
            # S5 (bead sf8dj): diagnostic provenance — the optional relaxations
            # (assume_current_date / time_window_ignored / lowered_threshold /
            # master_from_stream) that admitted this would-attach row. Empty
            # for a plain in-window default-threshold match. Additive field.
            "matched_via": [
                {"key": key, "label": label} for key, label in r.matched_via
            ],
            # ti939.3.5: masters this stream will NEVER attach to (operator
            # exclusion). Non-empty whenever any pairing was suppressed —
            # including rows that still attach/queue against OTHER masters.
            "excluded_masters": list(r.excluded_masters),
            "would_attach_master": (
                {
                    "channel_id": name_to_id.get(r.best.master_name),
                    "name": r.best.master_name,
                }
                if r.best is not None else None
            ),
            "candidates": [
                {
                    "master_channel_name": c.master_name,
                    "master_channel_id": name_to_id.get(c.master_name),
                    "master_parsed_title": c.parsed.title,
                    "master_parsed_start": (
                        c.parsed.start.isoformat() if c.parsed.start else None
                    ),
                    "score": round(c.score, 4),
                    "band": c.band,
                    "team_verdict": c.team_verdict,
                    "time_delta_minutes": round(c.time_delta_minutes, 1),
                    "reject_reason": (
                        c.reject_reasons[0] if c.reject_reasons else None
                    ),
                    # ti939.3.2 queue marker: 'pending' | 'accepted' |
                    # 'rejected' | None for this exact pairing fingerprint.
                    "review_status": _candidate_review_status(r, c),
                    # ti939.3.5: operator never-attach marker for this
                    # exact pairing fingerprint.
                    "excluded": _candidate_excluded(r, c),
                }
                for c in r.result.candidates[:_EVENT_PREVIEW_MAX_CANDIDATES_PER_STREAM]
            ],
        })
        if streams_out[-1]["candidates"]:
            candidates_pending_review += sum(
                1 for c in streams_out[-1]["candidates"]
                if c["review_status"] == REVIEW_STATUS_PENDING
            )

        if r.disposition == DISPOSITION_UNMATCHED:
            top = r.result.candidates[0] if r.result.candidates else None
            unmatched_out.append({
                "stream_id": r.stream.stream_id,
                "stream_name": r.stream.name,
                "group_id": r.stream.group_id,
                "provider": r.stream.provider,
                "parsed_title": r.result.parsed.title,
                "parsed_start": parsed_start,
                "best_candidate": (
                    {
                        "master_channel_name": top.master_name,
                        "score": round(top.score, 4),
                        "band": top.band,
                        "reject_reason": (
                            top.reject_reasons[0] if top.reject_reasons else None
                        ),
                    }
                    if top is not None else None
                ),
            })
        elif r.disposition == DISPOSITION_PARSE_FAILED:
            key = (r.stream.group_id, r.result.unmatchable_reason or "unknown")
            bucket = failure_groups.setdefault(key, {
                "group_id": r.stream.group_id,
                "group_name": group_names.get(r.stream.group_id),
                "reason": r.result.unmatchable_reason,
                "count": 0,
                "stream_names": [],
            })
            bucket["count"] += 1
            if len(bucket["stream_names"]) < _EVENT_PREVIEW_MAX_FAILURE_SAMPLES:
                bucket["stream_names"].append(r.stream.name)

    # --- Unmatched-stream promotion plan (bead ti939.4.1) -----------------
    # Present ONLY when the config opted in — a promotion-less preview
    # payload stays byte-identical (AC-1). The plan comes from the SAME
    # pure helper the live run's executor calls over the same resolver
    # output (services.event_sync_promote.build_promotion_plan), fed with
    # the target group's CURRENT channels, so "would promote" here equals
    # what a run would create/adopt on unchanged data (dry-run parity by
    # construction).
    promotion_out: dict | None = None
    promote_annotations: dict[tuple, dict] = {}
    if config.get("promote_unmatched"):
        from services.event_sync_promote import build_promotion_plan

        promote_target_group_id = config["promote_target_group_id"]
        target_channels: list[dict] = []
        try:
            tpage = 1
            while True:
                resp = await client.get_channels(
                    page=tpage, page_size=_PREVIEW_FETCH_PAGE_SIZE,
                    channel_group=promote_target_group_id,
                )
                batch = (
                    resp.get("results", []) if isinstance(resp, dict)
                    else (resp or [])
                )
                target_channels.extend(
                    ch for ch in batch
                    if ch.get("channel_group_id") == promote_target_group_id
                )
                if len(target_channels) >= _PREVIEW_MAX_CHANNELS:
                    target_channels = target_channels[:_PREVIEW_MAX_CHANNELS]
                    truncated = True
                    break
                if not isinstance(resp, dict) or not resp.get("next"):
                    break
                tpage += 1
        except Exception as e:
            logger.warning(
                "[EVENT-SYNC] preview: promotion target-group fetch failed "
                "(%s) — planning against an empty group (every unit shows "
                "as create)", e,
            )
            target_channels = []

        existing_name_to_id: dict[str, int] = {}
        for ch in target_channels:
            cname, cid = ch.get("name"), ch.get("id")
            if not cname or cid is None:
                continue
            lowered = cname.lower()
            if lowered not in existing_name_to_id \
                    or cid < existing_name_to_id[lowered]:
                existing_name_to_id[lowered] = cid

        plan = build_promotion_plan(
            config, resolution.resolved, existing_name_to_id
        )
        units_out = []
        for unit in plan.units:
            for row in unit.rows:
                promote_annotations[(row.stream.group_id,
                                     row.stream.stream_id)] = {
                    "would_promote": True,
                    "promote_action": unit.action,
                    "promote_channel_name": unit.channel_name,
                }
            units_out.append({
                "channel_name": unit.channel_name,
                "action": unit.action,
                "event_key": unit.event_key,
                "dateless": unit.dateless,
                "existing_channel_id": unit.existing_channel_id,
                "streams": [
                    {
                        "stream_id": row.stream.stream_id,
                        "stream_name": row.stream.name,
                        "provider": row.stream.provider,
                        "group_id": row.stream.group_id,
                        "disposition": row.disposition,
                    }
                    for row in unit.rows
                ],
            })
        for unit in plan.capped_units:
            for row in unit.rows:
                promote_annotations[(row.stream.group_id,
                                     row.stream.stream_id)] = {
                    "would_promote": False,
                    "promote_action": None,
                    "promote_channel_name": unit.channel_name,
                    "promote_capped": True,
                }
        promotion_out = {
            "enabled": True,
            "target_group_id": promote_target_group_id,
            "would_promote": len(plan.units),
            "would_promote_streams": plan.stream_count,
            "would_create": plan.would_create,
            "would_attach_existing": plan.would_attach_existing,
            "cap": plan.cap,
            "capped": plan.capped,
            "cap_overage": plan.cap_overage,
            "units": units_out,
        }
        # Annotate the unmatched rows in place — the operator reads the
        # unmatched table first, so the promotion verdict belongs on it.
        for row in unmatched_out:
            note = promote_annotations.get(
                (row["group_id"], row["stream_id"])
            )
            if note is not None:
                row.update(note)
            else:
                # Complete-identity gate: an unmatched row with no
                # annotation has no complete parsed identity (or lost the
                # cap) — say so explicitly rather than leaving the column
                # blank-and-ambiguous.
                row.setdefault("would_promote", False)

    logger.info(
        "[EVENT-SYNC] preview master_group=%s secondaries=%s masters=%d "
        "streams=%d would_attach=%d ambiguous=%d unmatched=%d parse_failed=%d "
        "excluded_by_operator=%d preflight_ok=%s truncated=%s "
        "stale_suspect=%d freshness_unknown=%d snapshot_covered=%d "
        "would_promote=%s",
        master_group_id, secondary_group_ids, len(master_channels),
        len(resolution.resolved), counts[DISPOSITION_WOULD_ATTACH],
        counts[DISPOSITION_AMBIGUOUS], counts[DISPOSITION_UNMATCHED],
        counts[DISPOSITION_PARSE_FAILED], counts[DISPOSITION_EXCLUDED],
        preflight["ok"], truncated,
        stale_suspect_streams, freshness_unknown_streams, snapshot_covered,
        promotion_out["would_promote"] if promotion_out else "off",
    )

    return {
        # bead ti939.4.1: promotion keys appear ONLY when the config opted
        # in (promotion_out is None otherwise → the two ** expansions are
        # empty and the payload is byte-identical to the pre-feature shape).
        **({"promotion": promotion_out} if promotion_out is not None else {}),
        "preflight": preflight,
        "summary": {
            "secondary_streams": len(resolution.resolved),
            "would_attach": counts[DISPOSITION_WOULD_ATTACH],
            "ambiguous_skipped": counts[DISPOSITION_AMBIGUOUS],
            "unmatched": counts[DISPOSITION_UNMATCHED],
            "parse_failed": counts[DISPOSITION_PARSE_FAILED],
            # ti939.3.5: streams whose only viable pairing carries an
            # operator never-attach exclusion (fifth disposition).
            "excluded_by_operator": counts[DISPOSITION_EXCLUDED],
            "master_channels": len(master_channels),
            "master_channels_unparsed": len(resolution.unparsed_master_names),
            # ti939.3.2: how many would_attach rows come from prior review
            # accepts (subset of would_attach) and how many rendered
            # candidate pairings are sitting in the pending review queue.
            "would_attach_via_review": would_attach_via_review,
            "candidates_pending_review": candidates_pending_review,
            # bead jqwfq Stage 1: staleness-signal counts. stale_suspect =
            # names positively present in the previous-day snapshot;
            # freshness_unknown = no verdict possible (no qualifying
            # snapshot, unknown provider, uncaptured or capped group).
            "stale_suspect_streams": stale_suspect_streams,
            "freshness_unknown_streams": freshness_unknown_streams,
            # bead ti939.4.1: promotion counts — units (= channels) and the
            # justifying streams across them. Keys present only on
            # promotion-enabled previews (AC-1 payload parity).
            **({
                "would_promote": promotion_out["would_promote"],
                "would_promote_streams": promotion_out[
                    "would_promote_streams"],
            } if promotion_out is not None else {}),
        },
        "streams": streams_out,
        "unmatched_streams": unmatched_out,
        "parse_failures": [
            failure_groups[key] for key in sorted(failure_groups)
        ],
        "unparsed_master_channels": list(resolution.unparsed_master_names),
        "truncated": truncated,
    }


@router.post("/validate")
async def validate_auto_creation_rule(
    conditions: list = Body(...),
    actions: list = Body(...)
):
    """Validate conditions and actions without creating a rule."""
    logger.debug("[AUTO-CREATE] POST /validate")
    try:
        from channel_pipeline_schema import validate_rule
        # Offload regex compile/validation off event loop (bd-w3z4h)
        result = await run_cpu_bound(validate_rule, conditions, actions)
        return result
    except Exception as e:
        logger.exception("[AUTO-CREATE] Failed to validate auto-creation rule: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/schema/conditions")
async def get_auto_creation_condition_schema():
    """Get the schema for available condition types."""
    from channel_pipeline_schema import ConditionType

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
    from channel_pipeline_schema import ActionType

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
                "match_by": {"type": "string", "enum": ["tvg_id", "normalized_name", "stream_group"], "default": "tvg_id", "description": "DEPRECATED no-op (bd-0emgo.1): validated but never consumed at runtime. Use loose_name_match to control fuzzy vs exact matching."},
                "loose_name_match": {"type": "boolean", "default": False, "description": "When false (default), target=auto merges only on EXACT normalized-name equality. When true, restores the legacy fuzzy cascade (core-name/deparen/word-prefix/call-sign)."},
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
    from channel_pipeline_schema import TemplateVariables

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
#
# Bundle generation walks every channel + every stream in the catalog. On a
# 15K-channel install that is hundreds of Dispatcharr round-trips and easily
# exceeds the 30s ECM_REQUEST_TIMEOUT_SECONDS middleware budget (bd-cns7j).
#
# Architecture (matches the bd-enfsy 202+poll pattern used by /run):
#   POST /debug-bundle              → 202 + {job_id, status: "running"};
#                                     dispatches a supervised background task.
#   GET  /debug-bundle/{job_id}     → JSON status while running/failed;
#                                     StreamingResponse(application/gzip) when
#                                     ready (job evicted on read).
#
# Job state lives in-memory because the artifact itself is RAM-only and
# operator-triggered. A 30-min TTL prunes abandoned jobs on every new POST.
#
# Inside the worker, page fetches and stream-detail batches run via
# asyncio.gather with a bounded semaphore so a 15K-channel catalog finishes
# in seconds instead of minutes.

_DEBUG_BUNDLE_PAGE_SIZE = 100
_DEBUG_BUNDLE_FETCH_CONCURRENCY = 8
_DEBUG_BUNDLE_JOB_TTL_SECONDS = 1800  # 30 minutes

# Resilience for the upstream fan-out (bd-59x51). A debug bundle is generated
# precisely when something is already wrong — often when Dispatcharr itself is
# slow or overloaded. So the build must NEVER hard-fail on a transient upstream
# fault: retry each page/batch a few times with exponential backoff, and if it
# still fails, drop that slice and record it in the manifest rather than aborting
# the whole bundle. A partial-but-stamped bundle beats no diagnostics at all.
_DEBUG_BUNDLE_FETCH_RETRIES = 3  # total attempts per page/batch
_DEBUG_BUNDLE_RETRY_BASE_DELAY = 0.5  # seconds; doubles each retry (0.5s, 1s, ...)


def _is_retryable_upstream_error(exc: BaseException) -> bool:
    """True for transient upstream faults worth retrying: a 5xx response, or a
    transport/timeout error. A 4xx is the caller's fault and is NOT retried."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


async def _with_retry(coro_factory, *, what: str):
    """Await ``coro_factory()`` with bounded retry + exponential backoff on
    transient upstream faults (5xx / transport / timeout).

    ``coro_factory`` is a zero-arg callable returning a fresh awaitable per
    attempt (a coroutine can only be awaited once). Non-retryable errors raise
    immediately; the last error is re-raised after retries are exhausted.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(_DEBUG_BUNDLE_FETCH_RETRIES):
        try:
            return await coro_factory()
        except Exception as e:
            if not _is_retryable_upstream_error(e):
                raise
            last_exc = e
            if attempt < _DEBUG_BUNDLE_FETCH_RETRIES - 1:
                delay = _DEBUG_BUNDLE_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "[AUTO-CREATE] Debug bundle: %s failed (attempt %d/%d), "
                    "retrying in %.1fs: %s",
                    what, attempt + 1, _DEBUG_BUNDLE_FETCH_RETRIES, delay, e,
                )
                await asyncio.sleep(delay)
    assert last_exc is not None  # loop ran >=1 time, so this is set on failure
    raise last_exc


class _DebugBundleJob:
    """In-memory state for one debug-bundle build (bd-cns7j)."""

    __slots__ = ("status", "created_at", "completed_at", "error", "filename", "data")

    def __init__(self) -> None:
        self.status: str = "running"  # running | completed | failed
        self.created_at: float = time.time()
        self.completed_at: float | None = None
        self.error: str | None = None
        self.filename: str | None = None
        self.data: bytes | None = None


_DEBUG_BUNDLE_JOBS: dict[str, _DebugBundleJob] = {}


def _prune_old_debug_bundle_jobs() -> None:
    """Drop jobs older than the TTL so the dict can't grow unbounded."""
    cutoff = time.time() - _DEBUG_BUNDLE_JOB_TTL_SECONDS
    stale = [jid for jid, job in _DEBUG_BUNDLE_JOBS.items() if job.created_at < cutoff]
    for jid in stale:
        _DEBUG_BUNDLE_JOBS.pop(jid, None)
    if stale:
        logger.debug("[AUTO-CREATE] Pruned %s expired debug-bundle jobs", len(stale))


def _add_tar_entry(tf: tarfile.TarFile, name: str, data: str):
    """Add a text file to a tar archive."""
    encoded = data.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(encoded)
    info.mtime = time.time()
    tf.addfile(info, io.BytesIO(encoded))


async def _fetch_all_channels(client) -> tuple[list[dict], dict]:
    """Fetch the full channel catalog with parallel pagination (bd-cns7j).

    Returns ``(channels, report)``. ``report`` describes fetch completeness so
    the manifest can flag a partial bundle (bd-59x51): a transient upstream 504
    on a single page is retried, and if it still fails that page is skipped —
    the whole build is never aborted just because Dispatcharr was slow on one
    page (the build runs precisely when upstream is likely struggling).
    """
    report: dict = {"complete": True, "expected_pages": 1, "failed_pages": []}
    # Page 1 is load-bearing (it yields the total count); retry it but let a
    # hard failure here propagate — with no first page there is no catalog.
    first = await _with_retry(
        lambda: client.get_channels(page=1, page_size=_DEBUG_BUNDLE_PAGE_SIZE),
        what="channel page 1",
    )
    channels: list[dict] = list(first.get("results", []))
    total_count = first.get("count")
    if total_count is not None and isinstance(total_count, int) and total_count > 0:
        total_pages = (total_count + _DEBUG_BUNDLE_PAGE_SIZE - 1) // _DEBUG_BUNDLE_PAGE_SIZE
        report["expected_pages"] = total_pages
        if total_pages > 1:
            sem = asyncio.Semaphore(_DEBUG_BUNDLE_FETCH_CONCURRENCY)

            async def fetch_page(p: int) -> list[dict]:
                async with sem:
                    res = await _with_retry(
                        lambda: client.get_channels(page=p, page_size=_DEBUG_BUNDLE_PAGE_SIZE),
                        what=f"channel page {p}",
                    )
                    return res.get("results", []) or []

            pages = list(range(2, total_pages + 1))
            tail = await asyncio.gather(
                *(fetch_page(p) for p in pages),
                return_exceptions=True,
            )
            for p, page_results in zip(pages, tail):
                if isinstance(page_results, BaseException):
                    report["failed_pages"].append(p)
                    logger.warning(
                        "[AUTO-CREATE] Debug bundle: channel page %d gave up after "
                        "%d attempts; skipping (bundle will be partial): %s",
                        p, _DEBUG_BUNDLE_FETCH_RETRIES, page_results,
                    )
                    continue
                channels.extend(page_results)
        if report["failed_pages"]:
            report["complete"] = False
            logger.warning(
                "[AUTO-CREATE] Debug bundle: %d/%d channel pages failed; "
                "collected %d channels (partial)",
                len(report["failed_pages"]), total_pages, len(channels),
            )
        return channels, report
    # Fallback for backends that don't return ``count`` — sequential walk.
    page = 2
    cursor = first
    while cursor.get("next"):
        try:
            cursor = await _with_retry(
                lambda: client.get_channels(page=page, page_size=_DEBUG_BUNDLE_PAGE_SIZE),
                what=f"channel page {page}",
            )
        except Exception as e:
            report["complete"] = False
            report["failed_pages"].append(page)
            logger.warning(
                "[AUTO-CREATE] Debug bundle: sequential channel page %d gave up "
                "after %d attempts; stopping walk (bundle will be partial): %s",
                page, _DEBUG_BUNDLE_FETCH_RETRIES, e,
            )
            break
        channels.extend(cursor.get("results", []) or [])
        page += 1
    report["expected_pages"] = page - 1 + len(report["failed_pages"])
    return channels, report


async def _fetch_stream_details(client, stream_ids: list[int], obfuscate_url) -> tuple[dict, dict]:
    """Fetch stream metadata in parallel batches (bd-cns7j).

    Returns ``(lookup, report)``. Each batch is retried on a transient upstream
    fault (bd-59x51); a batch that still fails is skipped (its streams simply
    fall back to empty detail) and counted in ``report`` so the manifest can
    flag the bundle as partial.
    """
    lookup: dict = {}
    report: dict = {"complete": True, "expected_batches": 0, "failed_batches": 0}
    if not stream_ids:
        return lookup, report
    batches = [
        stream_ids[i:i + _DEBUG_BUNDLE_PAGE_SIZE]
        for i in range(0, len(stream_ids), _DEBUG_BUNDLE_PAGE_SIZE)
    ]
    report["expected_batches"] = len(batches)
    sem = asyncio.Semaphore(_DEBUG_BUNDLE_FETCH_CONCURRENCY)

    async def fetch_batch(batch: list[int]) -> list:
        async with sem:
            try:
                return await _with_retry(
                    lambda: client.get_streams_by_ids(batch),
                    what="stream batch",
                )
            except Exception as e:
                report["failed_batches"] += 1
                logger.warning(
                    "[AUTO-CREATE] Debug bundle: stream batch gave up after %d "
                    "attempts; skipping (bundle will be partial): %s",
                    _DEBUG_BUNDLE_FETCH_RETRIES, e,
                )
                return []

    results = await asyncio.gather(*(fetch_batch(b) for b in batches))
    if report["failed_batches"]:
        report["complete"] = False
    for streams in results:
        for s in streams:
            m3u_acct = s.get("m3u_account")
            if isinstance(m3u_acct, dict):
                m3u_id = m3u_acct.get("id")
            else:
                m3u_id = m3u_acct
            lookup[s.get("id")] = {
                "name": s.get("name", ""),
                "m3u_account_id": m3u_id,
                "url": obfuscate_url(s.get("url", "")) if s.get("url") else "",
            }
    return lookup, report


def _event_sync_matching_stream_row(r, name_to_id: dict) -> dict:
    """Serialize one ``ResolvedStream`` into a debug-bundle matching row
    (bead 03nji).

    Pure READ of the resolver's output — no scoring, no band/attach policy.
    ``best_candidate`` is the attach winner for a would_attach row, else the
    top-ranked candidate so a rejected/ambiguous/unmatched row still shows
    WHY it did not attach (score, band, reject reason). It is ``None`` only
    when nothing was in the time window at all.
    """
    parsed = r.result.parsed
    parsed_start = parsed.start.isoformat() if parsed.start else None
    best = r.best if r.best is not None else (
        r.result.candidates[0] if r.result.candidates else None
    )
    best_out = None
    if best is not None:
        best_out = {
            "master_channel_name": best.master_name,
            "master_channel_id": name_to_id.get(best.master_name),
            "master_parsed_title": best.parsed.title,
            "master_parsed_start": (
                best.parsed.start.isoformat() if best.parsed.start else None
            ),
            "score": round(best.score, 4),
            "band": best.band,
            "team_verdict": best.team_verdict,
            "time_delta_minutes": round(best.time_delta_minutes, 1),
            "reject_reason": (
                best.reject_reasons[0] if best.reject_reasons else None
            ),
        }
    return {
        "stream_id": r.stream.stream_id,
        "stream_name": r.stream.name,
        "group_id": r.stream.group_id,
        "provider": r.stream.provider,
        "parsed_title": parsed.title,
        "parsed_start": parsed_start,
        "matched_pattern": parsed.matched_pattern,
        "disposition": r.disposition,
        "unmatchable_reason": r.result.unmatchable_reason,
        "ambiguous_reason": r.ambiguous_reason,
        "attach_source": r.attach_source,
        # bead jqwfq Stage 1: tri-state staleness signal (True = name present
        # in the previous-day snapshot, False = captured-uncapped absent,
        # None = unknown / fail-open).
        "name_seen_before_today": r.stream.name_seen_before_today,
        "matched_via": [
            {"key": key, "label": label} for key, label in r.matched_via
        ],
        # ti939.3.5: masters suppressed by an operator never-attach exclusion.
        "excluded_masters": list(r.excluded_masters),
        "best_candidate": best_out,
    }


async def _build_event_sync_matching_section(client) -> dict:
    """Debug-bundle matching diagnostics for Event Sync (bead 03nji).

    For each ENABLED event_sync rule, run the ZERO-WRITE resolver via the
    shared :func:`_fetch_and_resolve_event_sync` path — the EXACT fetch/resolve
    the preview endpoint uses, so this section can never fork the matcher — and
    serialize the full per-stream evidence a user needs to PROVE OUT matching:
    parsed title/time, best-candidate master, score, band, disposition, time
    delta, team verdict, reject/parse-fail reason, ``matched_via`` provenance,
    plus per-rule unparsed master names and summary counts.

    Observability only: nothing here scores, bands, or attaches. Each rule is
    isolated — one rule's fetch/resolve failure records an ``error`` on that
    rule's entry and never aborts the bundle.
    """
    from channel_pipeline_schema import validate_event_sync_config
    from models import ChannelPipelineRule
    from services.event_sync_preflight import (
        build_event_sync_master_rule_lookup,
        build_staleness_rail_warning,
        check_event_sync_group_settings,
        count_snapshot_covered_streams,
        resolve_effective_master_group_id,
    )
    from services.event_sync_exclusion_store import load_exclusion_keys
    from services.event_sync_resolver import (
        DISPOSITION_AMBIGUOUS,
        DISPOSITION_EXCLUDED,
        DISPOSITION_PARSE_FAILED,
        DISPOSITION_UNMATCHED,
        DISPOSITION_WOULD_ATTACH,
    )
    from services.event_sync_review_store import load_review_decisions

    section: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "rule_count": 0,
        "rules": [],
    }

    session = get_session()
    try:
        rules = session.query(ChannelPipelineRule).order_by(
            ChannelPipelineRule.priority, ChannelPipelineRule.id
        ).all()
        event_sync_rules = [r for r in rules if r.enabled and r.is_event_sync()]
    finally:
        session.close()

    if not event_sync_rules:
        section["note"] = (
            "No enabled event_sync rules — nothing to prove out. Enable an "
            "Event Sync rule and regenerate the bundle to capture matching "
            "diagnostics."
        )
        return section

    # Fetch group settings ONCE for Channel-Group-Override resolution shared
    # across all rules (the same read the preview pre-flight performs).
    try:
        all_settings = await client.get_all_m3u_group_settings()
    except Exception as e:
        logger.warning(
            "[EVENT-SYNC] debug bundle: group-settings fetch failed: %s", e)
        all_settings = {}

    for rule in event_sync_rules:
        rule_entry: dict = {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "enabled": rule.enabled,
        }
        try:
            config = rule.get_event_sync_config()
            if config is None:
                rule_entry["error"] = "event_sync_config is missing or corrupt"
                section["rules"].append(rule_entry)
                continue
            errors = validate_event_sync_config(config)
            if errors:
                rule_entry["error"] = f"invalid event_sync_config: {errors}"
                section["rules"].append(rule_entry)
                continue

            master_group_id = config["master_group_id"]
            effective_master_group_id = resolve_effective_master_group_id(
                all_settings, master_group_id
            )

            # Pre-flight status (bead yjchp): "why didn't this rule fire on
            # refresh" is not diagnosable from a bundle without it — the
            # unattended run gates on exactly this check. Reuses the
            # already-fetched all_settings and the cross-rule master lookup
            # (fix for the 'disable auto-sync would break the other rule'
            # advice). Recorded BEFORE the fetch/resolve so it survives a
            # later per-rule failure; its own failure must not sink the
            # bundle (error string, existing pattern).
            try:
                rule_entry["preflight"] = await check_event_sync_group_settings(
                    client, config, all_settings=all_settings,
                    other_master_rules=build_event_sync_master_rule_lookup(
                        event_sync_rules, exclude_rule_id=rule.id
                    ),
                )
            except Exception as e:  # noqa: BLE001 — never sink the bundle
                logger.warning(
                    "[EVENT-SYNC] debug bundle: pre-flight failed for rule "
                    "%s (%s): %s", rule.id, rule.name, e)
                rule_entry["preflight"] = {
                    "error": f"{type(e).__name__}: {e}",
                }

            # Review-queue decisions feed the resolver so the bundle predicts
            # exactly what a live run would do (mirrors the preview endpoint).
            decisions = None
            exclusion_keys: frozenset = frozenset()
            dsession = get_session()
            try:
                decisions = load_review_decisions(dsession, rule.id)
                # ti939.3.5: exclusions ride along so the bundle predicts
                # exactly what a live run would suppress.
                exclusion_keys = load_exclusion_keys(dsession, rule.id)
            except Exception as e:
                logger.warning(
                    "[EVENT-SYNC] debug bundle: review-queue load failed for "
                    "rule %s (%s) — resolving without it", rule.id, e)
                decisions = None
                exclusion_keys = frozenset()
            finally:
                dsession.close()

            fetched = await _fetch_and_resolve_event_sync(
                config, client, effective_master_group_id, decisions=decisions,
                exclusions=exclusion_keys,
            )
            resolution = fetched["resolution"]
            name_to_id = fetched["name_to_id"]

            counts = {
                DISPOSITION_WOULD_ATTACH: 0,
                DISPOSITION_AMBIGUOUS: 0,
                DISPOSITION_UNMATCHED: 0,
                DISPOSITION_PARSE_FAILED: 0,
                DISPOSITION_EXCLUDED: 0,
            }
            streams_out = []
            stale_suspect = 0
            freshness_unknown = 0
            for r in resolution.resolved:
                counts[r.disposition] = counts.get(r.disposition, 0) + 1
                if r.stream.name_seen_before_today is True:
                    stale_suspect += 1
                elif r.stream.name_seen_before_today is None:
                    freshness_unknown += 1
                streams_out.append(
                    _event_sync_matching_stream_row(r, name_to_id))

            # bead 2ey2y: inert-rail warning (same computation as the preview
            # endpoint) — appended to the pre-flight's warnings list so the
            # bundle shows WHY the stale-dateless guard did nothing.
            # setdefault: the pre-flight above may have failed into the
            # {"error": ...} shape and still deserves the warning.
            staleness_warning = build_staleness_rail_warning(
                config,
                secondary_stream_count=len(resolution.resolved),
                snapshot_covered_count=count_snapshot_covered_streams(
                    resolution.resolved, fetched["group_names"],
                    fetched["stale_lookup"],
                ),
            )
            if staleness_warning is not None:
                rule_entry["preflight"].setdefault(
                    "warnings", []).append(staleness_warning)

            rule_entry.update({
                "master_group_id": master_group_id,
                "effective_master_group_id": effective_master_group_id,
                "secondary_group_ids": list(
                    config.get("secondary_group_ids", [])),
                "matching_controls": {
                    "attach_threshold": config.get("attach_threshold"),
                    "enforce_time_window": config.get(
                        "enforce_time_window", True),
                    "time_window_minutes": config.get("time_window_minutes"),
                    "assume_current_date": config.get(
                        "assume_current_date", False),
                    # bead jqwfq: the stale-dateless demote rail knob —
                    # inert unless assume_current_date is also on.
                    "demote_stale_dateless": config.get(
                        "demote_stale_dateless", True),
                    "parse_master_from_stream": config.get(
                        "parse_master_from_stream", False),
                    "include_master_group_streams": config.get(
                        "include_master_group_streams", False),
                    # bead yjchp: the refresh-trigger opt-in — without it an
                    # unattended M3U refresh never runs this rule, the #1
                    # "why didn't it fire" root cause.
                    "auto_run": config.get("auto_run", False),
                },
                "summary": {
                    "secondary_streams": len(resolution.resolved),
                    "would_attach": counts[DISPOSITION_WOULD_ATTACH],
                    "ambiguous_skipped": counts[DISPOSITION_AMBIGUOUS],
                    "unmatched": counts[DISPOSITION_UNMATCHED],
                    "parse_failed": counts[DISPOSITION_PARSE_FAILED],
                    # ti939.3.5: operator never-attach exclusions.
                    "excluded_by_operator": counts[DISPOSITION_EXCLUDED],
                    "master_channels": len(fetched["master_channels"]),
                    "master_channels_unparsed": len(
                        resolution.unparsed_master_names),
                    # bead jqwfq Stage 1: staleness-signal counts (same
                    # semantics as the preview summary fields).
                    "stale_suspect_streams": stale_suspect,
                    "freshness_unknown_streams": freshness_unknown,
                },
                "streams": streams_out,
                "unparsed_master_channels": list(
                    resolution.unparsed_master_names),
                "truncated": fetched["truncated"],
            })
        except Exception as e:  # noqa: BLE001 — one rule must not sink the bundle
            logger.warning(
                "[EVENT-SYNC] debug bundle: rule %s (%s) diagnostics failed: "
                "%s", rule.id, rule.name, e)
            rule_entry["error"] = f"{type(e).__name__}: {e}"
        section["rules"].append(rule_entry)

    section["rule_count"] = len(section["rules"])
    return section


async def _build_debug_bundle() -> tuple[str, bytes]:
    """Build the debug bundle and return (filename, bytes).

    Pure work function — no HTTP / endpoint awareness. Used by the background
    worker dispatched from POST /debug-bundle.
    """
    logger.info("[AUTO-CREATE] Generating debug bundle")
    start = time.time()
    client = get_client()

    from csv_handler import generate_csv
    from log_utils import get_recent_logs
    from models import ChannelPipelineRule
    from obfuscate import obfuscate_text, obfuscate_url
    from routers.backup import APP_VERSION

    # -- 1. Fetch channels and groups from Dispatcharr ----------------
    all_channels, channels_report = await _fetch_all_channels(client)
    groups = await client.get_channel_groups() or []
    group_lookup = {g.get("id"): g.get("name", "") for g in groups}

    # -- 2. channels.json — channels with streams and stats -----------
    all_stream_ids: set = set()
    for ch in all_channels:
        all_stream_ids.update(ch.get("streams", []))

    stream_ids_list = list(all_stream_ids)
    stream_detail_lookup, streams_report = await _fetch_stream_details(
        client, stream_ids_list, obfuscate_url
    )

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
        rules = session.query(ChannelPipelineRule).order_by(
            ChannelPipelineRule.priority
        ).all()

        m3u_id_to_name = {}
        try:
            m3u_accounts = await client.get_m3u_accounts()
            m3u_id_to_name = {a["id"]: a["name"] for a in m3u_accounts}
        except Exception as m3u_lookup_err:
            # M3U-name lookup is decorative for the YAML export — when it
            # fails we still export rules with raw m3u_account_ids and the
            # operator can re-import on a host with M3U access.
            logger.warning(
                "[AUTO-CREATE-EXPORT] Could not fetch M3U accounts for name resolution: %s",
                m3u_lookup_err,
            )

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
    # Import the canonical credential-field set from the backup router so the
    # two redactors can never drift (bd-jmi1c P0-1 / bd-46g4t). Prior to this
    # the local tuple omitted both the Dispatcharr ``api_key`` (legacy, leak
    # since v0.16.0-0004) and the new ``dispatcharr_api_key`` field.
    from routers.backup import _SETTINGS_CREDENTIAL_FIELDS as _BACKUP_CREDS
    settings_obj = get_config_settings()
    settings_dict = settings_obj.model_dump()
    # Redact sensitive fields. The set is the union of the backup router's
    # ``_SETTINGS_CREDENTIAL_FIELDS`` (password, dispatcharr_api_key, api_key,
    # smtp_password, telegram_bot_token, mcp_api_key) plus debug-bundle-only
    # additions (discord_webhook_url, telegram_chat_id) that aren't credential-
    # class in the backup contract but are still PII operators wouldn't want
    # shared in a debug bundle.
    _REDACTED = "***REDACTED***"
    _DEBUG_BUNDLE_EXTRA = ("discord_webhook_url", "telegram_chat_id")
    for key in (*_BACKUP_CREDS, *_DEBUG_BUNDLE_EXTRA):
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
    # vkktd.2: the child ``task_schedules`` rows carry an ``enabled`` that is
    # ALWAYS seeded True; FIRING is gated by BOTH that child flag AND the PARENT
    # ``scheduled_tasks.enabled`` (task_engine requires both). Exporting only the
    # child made a gated-OFF task read "enabled: true" in the bundle and cost a
    # multi-hour diagnosis. Annotate every schedule with the parent gate and an
    # explicit ``effective_enabled = parent.enabled AND child.enabled`` so the
    # real firing gate is unambiguous, and keep the frozen next_run_at / null
    # last_run_at (already in to_dict) as the corroborating "never fired" signal.
    from models import TaskSchedule, ScheduledTask
    sched_session = get_session()
    try:
        parent_enabled_by_task = {
            row.task_id: row.enabled
            for row in sched_session.query(
                ScheduledTask.task_id, ScheduledTask.enabled
            ).all()
        }
        schedules = sched_session.query(TaskSchedule).order_by(
            TaskSchedule.task_id, TaskSchedule.id
        ).all()
        schedules_data = []
        for s in schedules:
            d = s.to_dict()
            parent_enabled = parent_enabled_by_task.get(s.task_id)
            # ``enabled`` from to_dict() is the CHILD schedule flag. Expose it
            # under an unambiguous alias plus the parent gate and the effective
            # (firing) gate. ``effective_enabled`` is None only when the parent
            # scheduled_tasks row is missing (state can't be determined).
            d["child_schedule_enabled"] = s.enabled
            d["parent_task_enabled"] = parent_enabled
            d["effective_enabled"] = (
                bool(parent_enabled) and bool(s.enabled)
                if parent_enabled is not None else None
            )
            schedules_data.append(d)
    finally:
        sched_session.close()
    task_schedules_str = json.dumps(schedules_data, indent=2)

    # -- 6b. normalization_rules.yaml — group + rule definitions ------
    # The auto-creation rules above reference normalization_group_ids (e.g.
    # [1,2,5,6,7,8]); without the group definitions we can't reason about
    # what normalize() actually does to a stream name. Capture all groups
    # with their rules nested in priority order. Strip ids/timestamps from
    # the rule body — they aren't useful for diagnosis and add noise — but
    # keep the group id so the cross-reference from rules.yaml resolves.
    from models import NormalizationRule, NormalizationRuleGroup
    norm_session = get_session()
    try:
        groups_q = norm_session.query(NormalizationRuleGroup).order_by(
            NormalizationRuleGroup.priority, NormalizationRuleGroup.id
        ).all()
        rules_by_group: dict[int, list] = {}
        for r in norm_session.query(NormalizationRule).order_by(
            NormalizationRule.group_id, NormalizationRule.priority, NormalizationRule.id
        ).all():
            rules_by_group.setdefault(r.group_id, []).append(r)

        norm_export = {
            "version": 1,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "groups": [],
        }
        for g in groups_q:
            group_rules = []
            for r in rules_by_group.get(g.id, []):
                rule_dict = {
                    "name": r.name,
                    "description": r.description,
                    "enabled": r.enabled,
                    "priority": r.priority,
                    "condition_type": r.condition_type,
                    "condition_value": r.condition_value,
                    "case_sensitive": r.case_sensitive,
                    "tag_group_id": r.tag_group_id,
                    "tag_match_position": r.tag_match_position,
                    "require_delimiter": r.require_delimiter,
                    "tag_group_name": r.tag_group.name if r.tag_group else None,
                    "conditions": r.get_conditions(),
                    "condition_logic": r.condition_logic,
                    "action_type": r.action_type,
                    "action_value": r.action_value,
                    "else_action_type": r.else_action_type,
                    "else_action_value": r.else_action_value,
                    "stop_processing": r.stop_processing,
                    "is_builtin": r.is_builtin,
                }
                group_rules.append(rule_dict)
            norm_export["groups"].append({
                "id": g.id,  # Kept so auto-creation rules' normalization_group_ids resolve.
                "name": g.name,
                "description": g.description,
                "enabled": g.enabled,
                "priority": g.priority,
                "is_builtin": g.is_builtin,
                "rule_count": len(group_rules),
                "rules": group_rules,
            })
        norm_group_count = len(groups_q)
        norm_rule_count = sum(len(v) for v in rules_by_group.values())
    finally:
        norm_session.close()
    norm_yaml_content = yaml.dump(norm_export, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # -- 7. channel_groups_diagnostic.json — Channel Manager mismatch diagnosis
    # Run BEFORE logs.txt is captured so [GROUPS-DIAG] lines land in the log dump too.
    from routers.channel_groups import build_channel_groups_diagnostic
    try:
        cg_diagnostic = build_channel_groups_diagnostic(groups, all_channels)
        cg_diagnostic_str = json.dumps(cg_diagnostic, indent=2, default=str)
    except Exception as e:
        logger.warning("[AUTO-CREATE] Debug bundle: channel groups diagnostic failed: %s", e)
        cg_diagnostic_str = json.dumps({"error": str(e)})

    # -- 7b. event_sync_matching.json — per-rule matching diagnostics (bead
    # 03nji). Runs the ZERO-WRITE resolver for every enabled event_sync rule so
    # a user hitting a matching problem can export a bundle that PROVES OUT what
    # the matcher decided (parse/score/band/disposition/reject reason per
    # stream). Runs BEFORE logs.txt so its [EVENT-SYNC] lines land in the dump.
    try:
        event_sync_matching = await _build_event_sync_matching_section(client)
    except Exception as e:  # noqa: BLE001 — never sink the whole bundle
        logger.warning(
            "[AUTO-CREATE] Debug bundle: event_sync matching section failed: %s",
            e)
        event_sync_matching = {"error": str(e), "rules": []}
    event_sync_matching_str = json.dumps(event_sync_matching, indent=2, default=str)
    event_sync_rule_count = len(event_sync_matching.get("rules", []))

    # -- 8. logs.txt — recent logs, obfuscated -----------------------
    log_lines = get_recent_logs()
    obfuscated_lines = [obfuscate_text(line) for line in log_lines]
    logs_text = "\n".join(obfuscated_lines)

    # -- 9. manifest.json --------------------------------------------
    total_streams = len(all_stream_ids)
    probed_success = sum(1 for s in stream_stats_lookup.values() if s.probe_status == "success")
    probed_failed = sum(1 for s in stream_stats_lookup.values() if s.probe_status in ("failed", "timeout"))
    black_screen_count = sum(1 for s in stream_stats_lookup.values() if s.is_black_screen)
    low_fps_count = sum(1 for s in stream_stats_lookup.values() if s.is_low_fps)

    # data_completeness (bd-59x51): whether every upstream page/batch came back.
    # When False, the channel/stream data above is PARTIAL — a reader must not
    # treat counts as authoritative. This is surfaced so a slow Dispatcharr that
    # timed out some pages yields a usable, honestly-labelled bundle instead of
    # a hard failure right when diagnostics are most needed.
    data_complete = bool(channels_report.get("complete")) and bool(streams_report.get("complete"))

    manifest = {
        "ecm_version": APP_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "channel_count": len(all_channels),
        "rule_count": rule_count,
        "group_count": len(groups),
        "stream_count": total_streams,
        "normalization_group_count": norm_group_count,
        "normalization_rule_count": norm_rule_count,
        "event_sync_rule_count": event_sync_rule_count,
        "data_completeness": {
            "complete": data_complete,
            "channels": channels_report,
            "streams": streams_report,
        },
        "stream_stats": {
            "probed_success": probed_success,
            "probed_failed": probed_failed,
            "unprobed": total_streams - probed_success - probed_failed,
            "black_screen": black_screen_count,
            "low_fps": low_fps_count,
        },
    }
    manifest_str = json.dumps(manifest, indent=2)
    if not data_complete:
        logger.warning(
            "[AUTO-CREATE] Debug bundle is PARTIAL — channels: %s, streams: %s",
            channels_report, streams_report,
        )

    # -- 10. Pack into tar.gz ----------------------------------------
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        _add_tar_entry(tf, "channels.json", channels_json_str)
        _add_tar_entry(tf, "channels.csv", csv_content)
        _add_tar_entry(tf, "rules.yaml", yaml_content)
        _add_tar_entry(tf, "normalization_rules.yaml", norm_yaml_content)
        _add_tar_entry(tf, "settings.json", settings_json_str)
        _add_tar_entry(tf, "task_schedules.json", task_schedules_str)
        _add_tar_entry(tf, "channel_groups_diagnostic.json", cg_diagnostic_str)
        _add_tar_entry(tf, "event_sync_matching.json", event_sync_matching_str)
        _add_tar_entry(tf, "logs.txt", logs_text)
        _add_tar_entry(tf, "manifest.json", manifest_str)
    payload = buf.getvalue()

    elapsed_ms = (time.time() - start) * 1000
    filename = f"ecm-debug-bundle-{datetime.utcnow():%Y%m%d-%H%M%S}.tar.gz"
    logger.info(
        "[AUTO-CREATE] Debug bundle generated in %.1fms (%s channels, %s rules, %s bytes)",
        elapsed_ms, len(all_channels), rule_count, len(payload),
    )
    return filename, payload


async def _run_debug_bundle_job(job_id: str) -> None:
    """Worker: build the bundle and store the result on the job row.

    Wrapped in try/except so failures land on the job (status='failed' +
    error) instead of vanishing into the asyncio task void. Mirrors the
    bd-enfsy supervision shape but specialized for the in-memory job dict.
    """
    job = _DEBUG_BUNDLE_JOBS.get(job_id)
    if job is None:
        logger.warning("[AUTO-CREATE] Debug bundle job %s missing before start", job_id)
        return
    try:
        filename, payload = await _build_debug_bundle()
        job.filename = filename
        job.data = payload
        job.status = "completed"
        job.completed_at = time.time()
        logger.info(
            "[AUTO-CREATE] Debug bundle job %s completed (%s bytes)",
            job_id, len(payload),
        )
    except asyncio.CancelledError:
        job.status = "failed"
        job.error = "Background task cancelled"
        job.completed_at = time.time()
        logger.warning("[AUTO-CREATE] Debug bundle job %s cancelled", job_id)
        raise
    except Exception as e:  # noqa: BLE001 — supervisor must catch broadly
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.completed_at = time.time()
        logger.exception("[AUTO-CREATE] Debug bundle job %s failed: %s", job_id, e)


@router.post("/debug-bundle", status_code=202)
async def start_debug_bundle():
    """Enqueue debug-bundle generation; return job id for polling (bd-cns7j).

    Generation walks the full catalog from Dispatcharr and was previously
    inline in the request, which timed out on large installs (>~10K channels).
    The work now runs in a supervised background task; the client polls
    ``GET /api/auto-creation/debug-bundle/{job_id}`` until the artifact is
    available.
    """
    _prune_old_debug_bundle_jobs()
    job_id = uuid.uuid4().hex
    _DEBUG_BUNDLE_JOBS[job_id] = _DebugBundleJob()

    task = asyncio.create_task(
        _run_debug_bundle_job(job_id),
        name=f"debug-bundle-{job_id}",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    logger.info("[AUTO-CREATE] Debug bundle job %s enqueued", job_id)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "running",
            "message": "Debug bundle generation started; poll /api/channel-pipeline/debug-bundle/{job_id} for status",
        },
    )


@router.get("/debug-bundle/{job_id}")
async def get_debug_bundle(job_id: str):
    """Poll/download a debug-bundle job (bd-cns7j).

    - ``running``  → 200 with ``{job_id, status: "running"}``
    - ``failed``   → 200 with ``{job_id, status: "failed", error}``
    - ``completed`` → ``application/gzip`` attachment; the job is evicted on read
    - missing job  → 404
    """
    job = _DEBUG_BUNDLE_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Debug bundle job not found")
    if job.status == "running":
        return {"job_id": job_id, "status": "running"}
    if job.status == "failed":
        return {"job_id": job_id, "status": "failed", "error": job.error or "unknown error"}
    # completed
    payload = job.data or b""
    filename = job.filename or "ecm-debug-bundle.tar.gz"
    # Single-shot download — drop the job so RAM is freed on read.
    _DEBUG_BUNDLE_JOBS.pop(job_id, None)
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
