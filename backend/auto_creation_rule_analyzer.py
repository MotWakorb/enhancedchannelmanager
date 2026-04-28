"""
auto_creation_rule_analyzer — advisory analyzer for auto-creation rules.

Surfaces structural and regex-style configuration bugs in auto-creation
rules WITHOUT running them. Used by the /api/auto-creation/rules/analyze
endpoint (live-mode) and /from-bundle endpoint (debug-bundle upload).

Design rules:

* All findings are advisory — severity ``warning`` or ``info``. Saves
  never block on analyzer findings; that is what :mod:`regex_lint`'s
  strict path is for.
* The OR-grouping algorithm is duplicated from
  :func:`auto_creation_evaluator.evaluate_conditions` (lines 828–834).
  Duplication is intentional — the evaluator is performance-critical
  and we don't want a runtime import dependency just to read the
  algorithm. :func:`split_or_groups` and the test
  ``test_users_sports_rule_grouping`` lock the contract.
* External data (channel-group counts, execution history) is optional.
  When absent, the relevant findings simply aren't produced — never
  invent findings from missing data.

Bead: bd-0gntx (Phase 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Iterable, Literal

from regex_lint import (
    LintViolation,
    lint_conditions_json_advisory,
    lint_pattern_advisory,
)


# Guard condition types — these constrain *which streams* a rule applies
# to (not *what value* a stream has). When ANDed with a regex/contains
# filter and then OR'd with bare regex/contains alternatives, the OR
# arms drop the guard and the rule fires for streams the user didn't
# intend.
_GUARD_TYPES = frozenset({
    "normalized_name_in_group",
    "normalized_name_not_in_group",
    "normalized_name_exists",
    "provider_is",
})


Severity = Literal["error", "warning", "info"]


@dataclass
class RuleFinding:
    """One advisory finding emitted by :func:`analyze_rule`."""

    rule_id: int | None
    rule_name: str
    code: str
    message: str
    severity: Severity = "warning"
    field: str = ""
    suggestion: str = ""
    detail: dict = _dc_field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
            "suggestion": self.suggestion,
            "detail": dict(self.detail),
        }


# -------------------------------------------------------------------------
# Condition helpers.
# -------------------------------------------------------------------------


def split_or_groups(conditions: list) -> list[list]:
    """Split a flat condition list into OR-groups.

    Mirrors :func:`auto_creation_evaluator.evaluate_conditions` (the
    ``or_groups`` construction). Each ``connector="or"`` after a
    non-empty current group starts a new group; conditions within a
    group are AND'd. The first connector is effectively ignored
    because the leading group is empty when we encounter it.
    """
    if not conditions:
        return []

    groups: list[list] = [[]]
    for cond in conditions:
        connector = (
            cond.get("connector", "and") if isinstance(cond, dict)
            else getattr(cond, "connector", "and")
        )
        if connector == "or" and groups[-1]:
            groups.append([])
        groups[-1].append(cond)
    return [g for g in groups if g]


def _group_has_guard(group: list, guard_type: str) -> bool:
    for cond in group:
        ctype = (
            cond.get("type") if isinstance(cond, dict) else getattr(cond, "type", None)
        )
        if ctype == guard_type:
            return True
    return False


def _conditions_contain_only_never(group: list) -> bool:
    """A group is unsatisfiable if it includes a ``never`` condition.

    AND semantics within a group: any ``never`` makes the whole group
    unsatisfiable, regardless of the other conditions.
    """
    for cond in group:
        ctype = (
            cond.get("type") if isinstance(cond, dict) else getattr(cond, "type", None)
        )
        if ctype == "never":
            return True
    return False


# -------------------------------------------------------------------------
# Per-finding detectors.
# -------------------------------------------------------------------------


def _check_andor_drops_guard(
    rule_id: int | None, rule_name: str, conditions: list,
) -> list[RuleFinding]:
    """Find guards that appear in some OR-groups but not others.

    Real-world bug shape (the 2026-04-28 user's Sports rule)::

        name_in_group=X AND group_matches=A
        OR group_matches=B
        OR group_contains=C

    Group 1 has the guard ``name_in_group=X`` AND-ed with ``A``;
    groups 2 and 3 don't. Streams from B or C qualify regardless of
    whether they're in group X — almost certainly not what the user
    meant.
    """
    groups = split_or_groups(conditions)
    if len(groups) < 2:
        return []

    findings: list[RuleFinding] = []
    for guard_type in _GUARD_TYPES:
        guarded = [i for i, g in enumerate(groups) if _group_has_guard(g, guard_type)]
        if not guarded:
            continue
        unguarded = [i for i in range(len(groups)) if i not in guarded]
        if not unguarded:
            continue
        findings.append(RuleFinding(
            rule_id=rule_id,
            rule_name=rule_name,
            code="ANDOR_DROPS_GUARD",
            severity="warning",
            field="conditions",
            message=(
                f"This rule has a ``{guard_type}`` constraint in "
                f"OR-group(s) {guarded} but not in OR-group(s) "
                f"{unguarded}. Conditions chained as ``A AND B OR C`` "
                f"read as ``(A AND B) OR C`` — the guard does NOT "
                f"propagate into ``C``. Streams matched by the "
                f"unguarded OR-arms will fire this rule regardless "
                f"of whether they pass the ``{guard_type}`` check."
            ),
            suggestion=(
                "Either repeat the guard in every OR-group, or split "
                "this rule into one rule per OR-arm so each rule has "
                "its own guard."
            ),
            detail={
                "guard_type": guard_type,
                "or_groups_with_guard": guarded,
                "or_groups_missing_guard": unguarded,
            },
        ))
    return findings


def _check_merge_streams_no_target_channels(
    rule_id: int | None,
    rule_name: str,
    actions: list,
    target_group_id: int | None,
    channel_groups_diagnostic: dict | None,
) -> list[RuleFinding]:
    """Flag merge_streams when target_group_id has no channels.

    Without :paramref:`channel_groups_diagnostic`, this check is a
    no-op — we never invent findings from missing data.
    """
    if not channel_groups_diagnostic or target_group_id is None:
        return []

    has_merge = any(
        (a.get("type") if isinstance(a, dict) else getattr(a, "type", None))
        == "merge_streams"
        for a in (actions or [])
    )
    if not has_merge:
        return []

    groups = channel_groups_diagnostic.get("groups", []) or []
    target = next(
        (g for g in groups if g.get("id") == target_group_id),
        None,
    )
    if target is None:
        return []
    channel_count = target.get("channel_count")
    if channel_count is None or channel_count > 0:
        return []

    return [RuleFinding(
        rule_id=rule_id,
        rule_name=rule_name,
        code="MERGE_STREAMS_NO_TARGET_CHANNELS",
        severity="warning",
        field=f"target_group_id={target_group_id}",
        message=(
            f"This rule uses ``merge_streams`` to attach streams to "
            f"channels in group id={target_group_id} "
            f"({target.get('name', '?')}), but that group currently "
            f"has 0 channels. ``merge_streams`` only ATTACHES to "
            f"existing channels — it does not create them. Every "
            f"matched stream will be skipped."
        ),
        suggestion=(
            "If you want new channels created, switch the action to "
            "``create_channel``. Otherwise add channels to the target "
            "group first, then re-run."
        ),
        detail={
            "target_group_id": target_group_id,
            "channel_count": channel_count,
        },
    )]


def _check_rule_has_no_hope_of_matching(
    rule_id: int | None, rule_name: str, conditions: list,
) -> list[RuleFinding]:
    """Flag rules where every OR-group is unsatisfiable.

    Conservative: only flags when EVERY group has a ``never`` (so the
    rule provably matches nothing). Empty conditions = always-true,
    handled elsewhere.
    """
    groups = split_or_groups(conditions)
    if not groups:
        return []
    if not all(_conditions_contain_only_never(g) for g in groups):
        return []
    return [RuleFinding(
        rule_id=rule_id,
        rule_name=rule_name,
        code="RULE_HAS_NO_HOPE_OF_MATCHING",
        severity="warning",
        field="conditions",
        message=(
            "Every OR-group on this rule contains a ``never`` "
            "condition. The rule will not match any stream, ever. "
            "Either remove the ``never`` conditions or delete/disable "
            "the rule."
        ),
        suggestion=(
            "Disable the rule, or remove the ``never`` conditions "
            "you no longer need."
        ),
        detail={"or_group_count": len(groups)},
    )]


def _bubble_up_regex_advisories(
    rule_id: int | None, rule_name: str, conditions: list,
) -> list[RuleFinding]:
    """Re-emit regex_lint advisory violations as RuleFindings.

    The regex linter doesn't know which rule a violation belongs to;
    the analyzer wraps each violation with the rule's ``id`` and
    ``name`` so the API consumer can render them as per-rule findings.
    """
    viols = lint_conditions_json_advisory(conditions)
    return [_lint_violation_to_finding(rule_id, rule_name, v) for v in viols]


def _lint_violation_to_finding(
    rule_id: int | None, rule_name: str, v: LintViolation,
) -> RuleFinding:
    return RuleFinding(
        rule_id=rule_id,
        rule_name=rule_name,
        code=v.code,
        severity=v.severity if v.severity in ("error", "warning", "info") else "warning",
        field=v.field,
        message=v.message,
        suggestion="",
        detail=dict(v.detail),
    )


# -------------------------------------------------------------------------
# Public API.
# -------------------------------------------------------------------------


def analyze_rule(
    rule: dict,
    *,
    channel_groups_diagnostic: dict | None = None,
) -> list[RuleFinding]:
    """Run all advisory checks on one rule; return RuleFindings.

    :param rule: a dict shaped like the auto-creation rule JSON
        returned by ``GET /api/auto-creation/rules`` (id, name,
        conditions, actions, target_group_id, …) or parsed from
        ``rules.yaml`` in a debug bundle.
    :param channel_groups_diagnostic: optional dict shaped like
        ``channel_groups_diagnostic.json`` from a debug bundle, with
        a top-level ``groups`` list of ``{id, name, channel_count}``.
        When present, enables the
        :data:`MERGE_STREAMS_NO_TARGET_CHANNELS` check.
    """
    rule_id = rule.get("id") if isinstance(rule, dict) else None
    rule_name = rule.get("name") or "" if isinstance(rule, dict) else ""
    conditions = rule.get("conditions") if isinstance(rule, dict) else None
    actions = rule.get("actions") if isinstance(rule, dict) else None
    target_group_id = rule.get("target_group_id") if isinstance(rule, dict) else None

    out: list[RuleFinding] = []
    out.extend(_bubble_up_regex_advisories(rule_id, rule_name, conditions or []))
    out.extend(_check_andor_drops_guard(rule_id, rule_name, conditions or []))
    out.extend(_check_rule_has_no_hope_of_matching(rule_id, rule_name, conditions or []))
    out.extend(_check_merge_streams_no_target_channels(
        rule_id, rule_name, actions or [], target_group_id, channel_groups_diagnostic,
    ))
    return out


def analyze_rules(
    rules: Iterable[dict],
    *,
    channel_groups_diagnostic: dict | None = None,
) -> dict:
    """Bulk-analyze rules; return the API response shape.

    Response shape::

        {
          "rules": [
            {"rule_id": int|None, "rule_name": str,
             "findings": [<RuleFinding.to_dict()>, ...]},
            ...
          ],
          "summary": {"error": int, "warning": int, "info": int}
        }

    Per-rule order matches input order so the UI can pair findings
    with the user's rule list.
    """
    summary = {"error": 0, "warning": 0, "info": 0}
    out_rules: list[dict] = []
    for rule in rules or []:
        findings = analyze_rule(
            rule, channel_groups_diagnostic=channel_groups_diagnostic,
        )
        for f in findings:
            summary[f.severity] = summary.get(f.severity, 0) + 1
        out_rules.append({
            "rule_id": rule.get("id") if isinstance(rule, dict) else None,
            "rule_name": rule.get("name") or "" if isinstance(rule, dict) else "",
            "findings": [f.to_dict() for f in findings],
        })
    return {"rules": out_rules, "summary": summary}
