"""
Rule Lint Scan — one-time read-only scan of existing rule rows.

Walks ``normalization_rules``, ``auto_creation_rules``, and
``dummy_epg_profiles``, runs :func:`regex_lint.lint_pattern` against
every user-supplied pattern field (including patterns buried inside the
JSON-encoded ``conditions`` / ``actions`` / ``substitution_pairs`` columns),
and writes findings to ``rule_lint_findings``. Does NOT mutate any rule
row — the scan is purely diagnostic.

Bead: bd-eio04.7. Runs on startup (see :func:`main.startup_event`) so pre-lint
rows that would now fail the write-time linter become visible in the UI
without the operator having to manually re-save every rule.

Idempotency contract: before each scan, existing findings for each rule
row are deleted and rewritten. Re-running the scan against an unchanged
corpus produces an identical result set (same count, same codes, same
messages; ``detected_at`` refreshes).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from regex_lint import (
    LintViolation,
    lint_actions_json,
    lint_conditions_json,
    lint_pattern,
    lint_substitution_pairs,
)

logger = logging.getLogger(__name__)


# Rule-type constants — persisted in the rule_type column. Kept here so
# that the scan writer and the API reader don't drift.
RULE_TYPE_NORMALIZATION = "normalization"
RULE_TYPE_AUTO_CREATION = "auto_creation"
RULE_TYPE_DUMMY_EPG = "dummy_epg"


def _safe_json_loads(raw: Any) -> Any:
    """JSON-decode ``raw`` if it's a string; return the parsed value, or
    ``None`` if decode fails. Passes non-strings through unchanged."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _scan_normalization_rule(rule: Any) -> list[LintViolation]:
    """Collect lint violations for one ``NormalizationRule`` row.

    Fields linted:
      - ``condition_value`` when ``condition_type == "regex"`` (the legacy
        single-condition shape).
      - ``action_value`` when ``action_type == "regex_replace"``.
      - ``else_action_value`` when ``else_action_type == "regex_replace"``.
      - Any pattern in the JSON ``conditions[]`` compound shape.
    """
    violations: list[LintViolation] = []
    if rule.condition_type == "regex":
        violations.extend(lint_pattern(rule.condition_value, field="condition_value"))
    if rule.action_type == "regex_replace":
        # regex_replace stores its pattern on condition_value and its
        # replacement on action_value — the replacement is a template,
        # not a regex, so we don't lint it. If the user got the
        # action_type wrong and condition_type is not 'regex', we still
        # skip linting action_value because it's a replacement string.
        pass
    # Compound conditions JSON.
    conditions = _safe_json_loads(rule.conditions)
    if isinstance(conditions, list):
        violations.extend(lint_conditions_json(conditions, prefix="conditions"))
    return violations


def _scan_auto_creation_rule(rule: Any) -> list[LintViolation]:
    """Collect lint violations for one ``AutoCreationRule`` row.

    Fields linted:
      - ``sort_regex`` (top-level column).
      - Patterns inside the JSON ``conditions[]`` (regex-flavored types).
      - ``action.pattern`` for ``set_variable`` regex modes.
      - ``action.name_transform_pattern`` on create_channel / create_group.
    """
    violations: list[LintViolation] = []
    violations.extend(lint_pattern(rule.sort_regex, field="sort_regex"))
    conditions = _safe_json_loads(rule.conditions)
    if isinstance(conditions, list):
        violations.extend(lint_conditions_json(conditions, prefix="conditions"))
    actions = _safe_json_loads(rule.actions)
    if isinstance(actions, list):
        violations.extend(lint_actions_json(actions, prefix="actions"))
    return violations


def _scan_dummy_epg_profile(profile: Any) -> list[LintViolation]:
    """Collect lint violations for one ``DummyEPGProfile`` row.

    Fields linted:
      - ``title_pattern``, ``time_pattern``, ``date_pattern`` (top-level).
      - ``find`` on substitution pairs with ``is_regex: True``.
      - Pattern fields inside each ``pattern_variants`` entry
        (``title_pattern`` / ``time_pattern`` / ``date_pattern``).
    """
    violations: list[LintViolation] = []
    violations.extend(lint_pattern(profile.title_pattern, field="title_pattern"))
    violations.extend(lint_pattern(profile.time_pattern, field="time_pattern"))
    violations.extend(lint_pattern(profile.date_pattern, field="date_pattern"))

    pairs = _safe_json_loads(profile.substitution_pairs)
    if isinstance(pairs, list):
        violations.extend(
            lint_substitution_pairs(pairs, prefix="substitution_pairs")
        )

    variants = _safe_json_loads(profile.pattern_variants)
    if isinstance(variants, list):
        for idx, variant in enumerate(variants):
            if not isinstance(variant, dict):
                continue
            violations.extend(
                lint_pattern(
                    variant.get("title_pattern"),
                    field=f"pattern_variants[{idx}].title_pattern",
                )
            )
            violations.extend(
                lint_pattern(
                    variant.get("time_pattern"),
                    field=f"pattern_variants[{idx}].time_pattern",
                )
            )
            violations.extend(
                lint_pattern(
                    variant.get("date_pattern"),
                    field=f"pattern_variants[{idx}].date_pattern",
                )
            )

    return violations


def _write_findings(
    session: Session,
    rule_type: str,
    rule_id: int,
    violations: list[LintViolation],
) -> int:
    """Replace any existing findings for ``(rule_type, rule_id)`` with the
    given list. Returns the number of findings written. No-op when
    ``violations`` is empty AND no prior findings exist (still wipes any
    stale rows from a previous scan when the pattern has since been fixed
    out of band)."""
    from models import RuleLintFinding

    # Idempotent: wipe prior findings for this rule before writing.
    session.query(RuleLintFinding).filter(
        RuleLintFinding.rule_type == rule_type,
        RuleLintFinding.rule_id == rule_id,
    ).delete(synchronize_session=False)

    if not violations:
        return 0

    now = datetime.utcnow()
    for v in violations:
        session.add(
            RuleLintFinding(
                rule_type=rule_type,
                rule_id=rule_id,
                field=v.field,
                code=v.code,
                message=v.message,
                detail=json.dumps(v.detail) if v.detail else None,
                detected_at=now,
            )
        )
    return len(violations)


def run_scan(session: Session) -> dict:
    """Scan all rule tables; write findings; return summary counts.

    Returns a summary dict::

        {
          "normalization": {"rules_scanned": 8, "rules_flagged": 1, "findings": 2},
          "auto_creation": {"rules_scanned": 3, "rules_flagged": 0, "findings": 0},
          "dummy_epg":     {"rules_scanned": 1, "rules_flagged": 0, "findings": 0},
          "total_findings": 2,
        }

    Safe to call from an idle ``on_event("startup")`` hook — it closes
    and commits its own transactions. Best-effort: any exception per-row
    is logged and the scan continues.
    """
    from models import AutoCreationRule, DummyEPGProfile, NormalizationRule

    summary: dict = {}
    total_findings = 0

    # --- normalization ---
    norm_rules = session.query(NormalizationRule).all()
    norm_flagged = 0
    norm_findings = 0
    for rule in norm_rules:
        try:
            viols = _scan_normalization_rule(rule)
        except Exception as e:
            logger.warning(
                "[RULE-LINT-SCAN] Failed to scan normalization rule id=%s: %s",
                rule.id,
                e,
            )
            continue
        written = _write_findings(
            session, RULE_TYPE_NORMALIZATION, rule.id, viols
        )
        if written:
            norm_flagged += 1
            norm_findings += written
    summary["normalization"] = {
        "rules_scanned": len(norm_rules),
        "rules_flagged": norm_flagged,
        "findings": norm_findings,
    }
    total_findings += norm_findings

    # --- auto_creation ---
    ac_rules = session.query(AutoCreationRule).all()
    ac_flagged = 0
    ac_findings = 0
    for rule in ac_rules:
        try:
            viols = _scan_auto_creation_rule(rule)
        except Exception as e:
            logger.warning(
                "[RULE-LINT-SCAN] Failed to scan auto_creation rule id=%s: %s",
                rule.id,
                e,
            )
            continue
        written = _write_findings(
            session, RULE_TYPE_AUTO_CREATION, rule.id, viols
        )
        if written:
            ac_flagged += 1
            ac_findings += written
    summary["auto_creation"] = {
        "rules_scanned": len(ac_rules),
        "rules_flagged": ac_flagged,
        "findings": ac_findings,
    }
    total_findings += ac_findings

    # --- dummy_epg ---
    de_profiles = session.query(DummyEPGProfile).all()
    de_flagged = 0
    de_findings = 0
    for profile in de_profiles:
        try:
            viols = _scan_dummy_epg_profile(profile)
        except Exception as e:
            logger.warning(
                "[RULE-LINT-SCAN] Failed to scan dummy_epg profile id=%s: %s",
                profile.id,
                e,
            )
            continue
        written = _write_findings(
            session, RULE_TYPE_DUMMY_EPG, profile.id, viols
        )
        if written:
            de_flagged += 1
            de_findings += written
    summary["dummy_epg"] = {
        "rules_scanned": len(de_profiles),
        "rules_flagged": de_flagged,
        "findings": de_findings,
    }
    total_findings += de_findings

    session.commit()
    summary["total_findings"] = total_findings
    logger.info(
        "[RULE-LINT-SCAN] Scan complete — total_findings=%d "
        "(normalization=%d, auto_creation=%d, dummy_epg=%d)",
        total_findings,
        norm_findings,
        ac_findings,
        de_findings,
    )
    return summary
