"""
Unit tests for :mod:`auto_creation_rule_analyzer` — bd-0gntx Phase 1.

The analyzer emits advisory findings on a rule's structure (not its
runtime behavior) so users can spot common configuration bugs before
running the rule. Findings are warnings; saves never block.

The fixture rule shapes in this file are lifted from the 2026-04-28
debug bundle that motivated bd-0gntx — every finding code must
reproduce against the rules in that bundle.
"""
from __future__ import annotations

import pytest

import auto_creation_rule_analyzer as analyzer
from auto_creation_rule_analyzer import (
    RuleFinding,
    analyze_rule,
    analyze_rules,
    split_or_groups,
)


# =========================================================================
# OR-grouping algorithm parity with the evaluator.
#
# split_or_groups mirrors evaluate_conditions's OR-group construction
# (auto_creation_evaluator.py:828-834). If that algorithm changes the
# analyzer must change with it — these tests pin the contract.
# =========================================================================


class TestSplitOrGroups:
    def test_all_and_one_group(self):
        conds = [
            {"type": "stream_name_contains", "value": "x", "connector": "and"},
            {"type": "quality_min", "value": 720, "connector": "and"},
        ]
        groups = split_or_groups(conds)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_or_starts_new_group(self):
        conds = [
            {"type": "stream_name_contains", "value": "x", "connector": "and"},
            {"type": "stream_name_contains", "value": "y", "connector": "or"},
        ]
        groups = split_or_groups(conds)
        assert len(groups) == 2

    def test_first_or_does_not_create_empty_leading_group(self):
        # Mirrors the evaluator: the first connector is effectively
        # ignored because or_groups[-1] is empty when we encounter it.
        conds = [
            {"type": "stream_name_contains", "value": "x", "connector": "or"},
            {"type": "stream_name_contains", "value": "y", "connector": "and"},
        ]
        groups = split_or_groups(conds)
        assert len(groups) == 1

    def test_users_sports_rule_grouping(self):
        # Exact shape from the 2026-04-28 bundle's "Sports Networks" rule.
        conds = [
            {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
            {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
            {"type": "stream_group_matches", "value": "US|", "connector": "or"},
            {"type": "stream_group_contains", "value": "^4K", "connector": "or"},
        ]
        groups = split_or_groups(conds)
        # 3 OR-groups: [name+UK], [US], [4K] — confirms the bug shape.
        assert len(groups) == 3
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1
        assert len(groups[2]) == 1


# =========================================================================
# ANDOR_DROPS_GUARD — a guard condition (name_in_group / provider_is)
# appears in some OR-groups but not others.
# =========================================================================


GUARD_TYPES = (
    "normalized_name_in_group",
    "normalized_name_not_in_group",
    "normalized_name_exists",
    "provider_is",
)


class TestAndorDropsGuard:
    def test_users_sports_rule_flagged(self):
        rule = {
            "id": 2,
            "name": "Sports Networks - excl Fr and Es",
            "conditions": [
                {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
                {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
                {"type": "stream_group_matches", "value": "US|", "connector": "or"},
                {"type": "stream_group_contains", "value": "^4K", "connector": "or"},
            ],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        findings = analyze_rule(rule)
        codes = {f.code for f in findings}
        assert "ANDOR_DROPS_GUARD" in codes

    def test_severity_is_warning(self):
        rule = {
            "id": 2, "name": "x",
            "conditions": [
                {"type": "normalized_name_in_group", "value": 1, "connector": "and"},
                {"type": "stream_group_matches", "value": "a", "connector": "or"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "ANDOR_DROPS_GUARD"]
        assert findings
        for f in findings:
            assert f.severity == "warning"

    def test_finding_names_the_dropped_group(self):
        rule = {
            "id": 2, "name": "x",
            "conditions": [
                {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
                {"type": "stream_group_matches", "value": "a", "connector": "or"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "ANDOR_DROPS_GUARD"]
        # Detail tells the user *which* OR-groups dropped the guard.
        assert findings[0].detail.get("guard_type") == "normalized_name_in_group"
        assert "or_groups_missing_guard" in findings[0].detail

    @pytest.mark.parametrize("guard_type", GUARD_TYPES)
    def test_each_guard_type_detected(self, guard_type):
        rule = {
            "id": 1, "name": "x",
            "conditions": [
                {"type": guard_type, "value": 1, "connector": "and"},
                {"type": "stream_group_matches", "value": "a", "connector": "or"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "ANDOR_DROPS_GUARD"]
        assert len(findings) == 1, f"guard_type={guard_type} not detected"

    def test_guard_in_every_or_group_not_flagged(self):
        # Rewritten Sports rule — guard is in every OR group. Clean.
        rule = {
            "id": 1, "name": "x",
            "conditions": [
                {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
                {"type": "stream_group_matches", "value": "^UK\\|", "connector": "and"},
                {"type": "normalized_name_in_group", "value": 1464, "connector": "or"},
                {"type": "stream_group_matches", "value": "^US\\|", "connector": "and"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "ANDOR_DROPS_GUARD"]
        assert findings == []

    def test_no_or_groups_means_no_drop(self):
        # All AND — no possibility of dropping the guard.
        rule = {
            "id": 1, "name": "x",
            "conditions": [
                {"type": "normalized_name_in_group", "value": 1, "connector": "and"},
                {"type": "stream_group_matches", "value": "a", "connector": "and"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "ANDOR_DROPS_GUARD"]
        assert findings == []

    def test_no_guard_anywhere_means_no_drop(self):
        # Rule has no guards at all — nothing to drop.
        rule = {
            "id": 1, "name": "x",
            "conditions": [
                {"type": "stream_group_matches", "value": "a", "connector": "and"},
                {"type": "stream_group_matches", "value": "b", "connector": "or"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "ANDOR_DROPS_GUARD"]
        assert findings == []


# =========================================================================
# MERGE_STREAMS_NO_TARGET_CHANNELS — merge_streams target=auto with a
# target_group_id that has zero channels (or with execution history
# that shows 100% no-channel-found skips).
# =========================================================================


class TestMergeStreamsNoTargetChannels:
    def test_target_group_with_zero_channels_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "target_group_id": 99,
            "conditions": [],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        # Diagnostic says group 99 has zero channels.
        diagnostic = {"groups": [{"id": 99, "name": "Empty", "channel_count": 0}]}
        findings = analyze_rule(rule, channel_groups_diagnostic=diagnostic)
        codes = {f.code for f in findings}
        assert "MERGE_STREAMS_NO_TARGET_CHANNELS" in codes

    def test_severity_is_warning(self):
        rule = {
            "id": 1, "name": "x",
            "target_group_id": 99,
            "conditions": [],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        diagnostic = {"groups": [{"id": 99, "channel_count": 0}]}
        findings = [
            f for f in analyze_rule(rule, channel_groups_diagnostic=diagnostic)
            if f.code == "MERGE_STREAMS_NO_TARGET_CHANNELS"
        ]
        assert findings
        for f in findings:
            assert f.severity == "warning"

    def test_target_group_with_channels_not_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "target_group_id": 99,
            "conditions": [],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        diagnostic = {"groups": [{"id": 99, "channel_count": 42}]}
        findings = [
            f for f in analyze_rule(rule, channel_groups_diagnostic=diagnostic)
            if f.code == "MERGE_STREAMS_NO_TARGET_CHANNELS"
        ]
        assert findings == []

    def test_no_diagnostic_no_finding(self):
        # Without the diagnostic, we can't know channel counts. Don't
        # invent findings — the analyzer must be quiet when it can't be
        # sure.
        rule = {
            "id": 1, "name": "x",
            "target_group_id": 99,
            "conditions": [],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        findings = [
            f for f in analyze_rule(rule)
            if f.code == "MERGE_STREAMS_NO_TARGET_CHANNELS"
        ]
        assert findings == []

    def test_create_channel_action_not_flagged(self):
        # Only merge_streams produces this finding.
        rule = {
            "id": 1, "name": "x",
            "target_group_id": 99,
            "conditions": [],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
        }
        diagnostic = {"groups": [{"id": 99, "channel_count": 0}]}
        findings = [
            f for f in analyze_rule(rule, channel_groups_diagnostic=diagnostic)
            if f.code == "MERGE_STREAMS_NO_TARGET_CHANNELS"
        ]
        assert findings == []


# =========================================================================
# RULE_HAS_NO_HOPE_OF_MATCHING — every OR-group contains ``never``.
# =========================================================================


class TestRuleHasNoHopeOfMatching:
    def test_all_groups_have_never_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "conditions": [
                {"type": "stream_name_contains", "value": "x", "connector": "and"},
                {"type": "never", "connector": "and"},
                {"type": "never", "connector": "or"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "RULE_HAS_NO_HOPE_OF_MATCHING"]
        assert findings

    def test_one_group_can_match_not_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "conditions": [
                {"type": "never", "connector": "and"},
                {"type": "stream_name_contains", "value": "x", "connector": "or"},
            ],
            "actions": [],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "RULE_HAS_NO_HOPE_OF_MATCHING"]
        assert findings == []

    def test_empty_conditions_not_flagged(self):
        # Empty conditions = always-true rule. Different problem; not
        # this code's business.
        rule = {"id": 1, "name": "x", "conditions": [], "actions": []}
        findings = [f for f in analyze_rule(rule) if f.code == "RULE_HAS_NO_HOPE_OF_MATCHING"]
        assert findings == []


# =========================================================================
# Regex advisory bubble-up — analyze_rule should surface the bd-0gntx
# regex_lint warnings as findings on the rule.
# =========================================================================


class TestAdvisoryRegexBubbleUp:
    def test_trivially_matches_all_surfaces_as_finding(self):
        rule = {
            "id": 2, "name": "x",
            "conditions": [
                {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
            ],
            "actions": [],
        }
        codes = {f.code for f in analyze_rule(rule)}
        assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes

    def test_redundant_caret_surfaces(self):
        rule = {
            "id": 5, "name": "x",
            "conditions": [
                {"type": "stream_group_matches", "value": "^\\^4k", "connector": "and"},
            ],
            "actions": [],
        }
        codes = {f.code for f in analyze_rule(rule)}
        assert "REGEX_REDUNDANT_ESCAPE_CARET" in codes

    def test_operator_value_mismatch_surfaces(self):
        rule = {
            "id": 2, "name": "x",
            "conditions": [
                {"type": "stream_group_contains", "value": "^4K", "connector": "and"},
            ],
            "actions": [],
        }
        codes = {f.code for f in analyze_rule(rule)}
        assert "OPERATOR_VALUE_LOOKS_LIKE_REGEX" in codes

    def test_clean_rule_produces_no_advisories(self):
        # The user's "Movie Networks - UK add" rule — clean.
        rule = {
            "id": 3, "name": "Movie Networks - UK add",
            "conditions": [
                {"type": "normalized_name_in_group", "value": 1473, "connector": "and"},
                {"type": "stream_group_matches", "value": "^UK\\|", "connector": "and"},
            ],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        findings = analyze_rule(rule)
        assert findings == []


# =========================================================================
# Bulk analyze_rules — wraps analyze_rule per rule + summary counts.
# =========================================================================


class TestAnalyzeRules:
    def test_summary_counts(self):
        rules = [
            # One bad, one clean.
            {
                "id": 2, "name": "bad",
                "conditions": [
                    {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
                ],
                "actions": [],
            },
            {
                "id": 3, "name": "clean",
                "conditions": [
                    {"type": "stream_group_matches", "value": "^UK\\|", "connector": "and"},
                ],
                "actions": [],
            },
        ]
        result = analyze_rules(rules)
        assert result["summary"]["warning"] >= 1
        assert result["summary"]["error"] == 0
        # Per-rule entries preserve order.
        assert [r["rule_name"] for r in result["rules"]] == ["bad", "clean"]
        assert len(result["rules"][0]["findings"]) >= 1
        assert len(result["rules"][1]["findings"]) == 0

    def test_empty_rules_list(self):
        result = analyze_rules([])
        assert result == {
            "rules": [],
            "summary": {"error": 0, "warning": 0, "info": 0},
        }


# =========================================================================
# RuleFinding dataclass plumbing.
# =========================================================================


class TestRuleFinding:
    def test_default_severity_is_warning(self):
        # Most analyzer findings are warnings — make that the default.
        f = RuleFinding(rule_id=1, rule_name="x", code="ANDOR_DROPS_GUARD", message="m")
        assert f.severity == "warning"

    def test_to_dict_round_trip(self):
        f = RuleFinding(
            rule_id=1, rule_name="x",
            code="ANDOR_DROPS_GUARD",
            severity="warning",
            field="conditions[2]",
            message="m",
            suggestion="s",
            detail={"foo": "bar"},
        )
        d = f.to_dict()
        assert d["rule_id"] == 1
        assert d["code"] == "ANDOR_DROPS_GUARD"
        assert d["severity"] == "warning"
        assert d["field"] == "conditions[2]"
        assert d["suggestion"] == "s"
        assert d["detail"] == {"foo": "bar"}
