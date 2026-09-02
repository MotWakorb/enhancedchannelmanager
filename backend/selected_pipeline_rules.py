"""Shared selected-rule eligibility and worker snapshot loading."""

from datetime import datetime

from channel_pipeline_schema import validate_event_sync_config, validate_rule
from database import get_session
from models import ChannelPipelineRule


class SelectedRuleValidationError(ValueError):
    def __init__(self, code: str, message: str, *, rule_ids=None, issues=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.rule_ids = rule_ids or []
        self.issues = issues or []


def load_selected_rule_snapshots(rule_ids: list[int]) -> list[ChannelPipelineRule]:
    """Atomically validate exact IDs and return detached priority/ID snapshots."""
    session = get_session()
    try:
        rules = session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.id.in_(rule_ids)
        ).order_by(ChannelPipelineRule.priority, ChannelPipelineRule.id).all()
        found_ids = {rule.id for rule in rules}
        missing = sorted(set(rule_ids) - found_ids)
        if missing:
            raise SelectedRuleValidationError(
                "unknown_rule_ids", "Selected rules were not found",
                rule_ids=missing,
            )

        issues = []
        for rule in rules:
            issues.extend(selected_rule_issues(rule))

        if issues:
            raise SelectedRuleValidationError(
                "selected_rules_not_runnable",
                "Every selected rule must be runnable",
                issues=issues,
            )
        for rule in rules:
            session.expunge(rule)
        return rules
    finally:
        session.close()


def selected_rule_issues(
    rule: ChannelPipelineRule, *, today=None
) -> list[dict]:
    """Return the shared selected-run eligibility issues for one rule."""
    today = today or datetime.utcnow().date()
    if not rule.enabled:
        return [{"rule_id": rule.id, "rule_name": rule.name,
                 "reason": "disabled"}]
    if ((rule.active_from is not None and rule.active_from > today)
            or (rule.active_until is not None and rule.active_until < today)):
        return [{"rule_id": rule.id, "rule_name": rule.name,
                 "reason": "inactive"}]

    errors = list(validate_rule(
        rule.get_conditions(), rule.get_actions()
    ).get("errors", []))
    required_provider_error = rule.get_required_provider_ids_error()
    if required_provider_error:
        errors.append(required_provider_error)
    if rule.is_event_sync():
        config = rule.get_event_sync_config()
        if config is None:
            errors.append("event_sync_config is invalid")
        elif not config.get("enabled", True):
            return [{"rule_id": rule.id, "rule_name": rule.name,
                     "reason": "disabled"}]
        else:
            errors.extend(validate_event_sync_config(config))
    return ([{"rule_id": rule.id, "rule_name": rule.name,
              "reason": "invalid", "errors": errors}] if errors else [])
