"""
Unit tests for :mod:`channel_pipeline_rule_analyzer` — bd-0gntx Phase 1.

The analyzer emits advisory findings on a rule's structure (not its
runtime behavior) so users can spot common configuration bugs before
running the rule. Findings are warnings; saves never block.

The fixture rule shapes in this file are lifted from the 2026-04-28
debug bundle that motivated bd-0gntx — every finding code must
reproduce against the rules in that bundle.
"""
from __future__ import annotations

import pytest

import channel_pipeline_rule_analyzer as analyzer
from channel_pipeline_rule_analyzer import (
    RuleFinding,
    analyze_rule,
    analyze_rules,
    split_or_groups,
)


# =========================================================================
# OR-grouping algorithm parity with the evaluator.
#
# split_or_groups mirrors evaluate_conditions's OR-group construction
# (channel_pipeline_evaluator.py:828-834). If that algorithm changes the
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
    "stream_group_is",
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
# MERGE_SCOPE_NOT_TARGET_GROUP — create_channel with if_exists=merge /
# merge_only on a rule whose match_scope_target_group is off. Advisory
# (severity=info), not a misconfiguration (GH #226, bd-p6ko9).
# =========================================================================


def _codes(rule, **kwargs):
    return {f.code for f in analyze_rule(rule, **kwargs)}


class TestMergeScopeNotTargetGroup:
    def test_merge_with_scope_off_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}", "if_exists": "merge"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" in _codes(rule)

    def test_merge_only_with_scope_off_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge_only"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" in _codes(rule)

    def test_severity_is_info(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        findings = [f for f in analyze_rule(rule) if f.code == "MERGE_SCOPE_NOT_TARGET_GROUP"]
        assert findings
        for f in findings:
            assert f.severity == "info"
        # The finding records which if_exists value triggered it.
        assert findings[0].detail.get("if_exists") == "merge"

    def test_scope_on_not_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": True,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)

    def test_no_create_channel_action_not_flagged(self):
        # merge_streams is a different action; this check is create_channel-only.
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)

    def test_create_channel_if_exists_skip_not_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "skip"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)

    def test_create_channel_if_exists_update_not_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "update"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)

    def test_create_channel_if_exists_default_not_flagged(self):
        # No if_exists key at all → defaults to "skip" → no finding.
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)

    def test_empty_actions_not_flagged(self):
        rule = {
            "id": 1, "name": "x",
            "match_scope_target_group": False,
            "conditions": [],
            "actions": [],
        }
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)

    def test_missing_actions_key_not_flagged(self):
        rule = {"id": 1, "name": "x", "match_scope_target_group": False, "conditions": []}
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" not in _codes(rule)


# =========================================================================
# MERGE_SCOPE_PINNED_TO_OTHER_GROUP (GH #801, bead rtst2.1) - the rule's
# merge lookup is pinned to a group its create_channel action never lands
# in, so every same-name lookup searches a group the rule's channels were
# never in. This is the INVERSE of MERGE_SCOPE_NOT_TARGET_GROUP above,
# which covers scope-off/search-all-groups (GH #226, bd-p6ko9).
# =========================================================================

_PIN_CODE = "MERGE_SCOPE_PINNED_TO_OTHER_GROUP"


class TestMergeScopePinnedToOtherGroup:
    def test_pin_differs_from_rule_target_group_flagged(self):
        """The reporter's shape: scope pinned to 7, channels land in 12."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "orphan_action": "delete",
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        assert _PIN_CODE in _codes(rule)

    def test_pin_differs_from_action_group_id_flagged(self):
        """An action-level group_id wins over the rule target group, exactly
        as in _execute_create_channel's fallback chain."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 12,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge", "group_id": 30}],
        }
        findings = [f for f in analyze_rule(rule) if f.code == _PIN_CODE]
        assert findings
        assert findings[0].detail["create_group_id"] == 30

    def test_pin_matching_action_group_id_not_flagged(self):
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 30,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge", "group_id": 30}],
        }
        assert _PIN_CODE not in _codes(rule)

    def test_pin_matching_target_group_not_flagged(self):
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 12,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        assert _PIN_CODE not in _codes(rule)

    def test_no_pin_is_auto_and_not_flagged(self):
        """match_scope_group_id=None is the Auto choice: the scope follows the
        create action's group, so it can never disagree with it."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": None,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        assert _PIN_CODE not in _codes(rule)

    def test_scope_off_not_flagged(self):
        """With match_scope_target_group off the lookup searches every group,
        so the pin is inert. MERGE_SCOPE_NOT_TARGET_GROUP covers that case."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": False,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        assert _PIN_CODE not in _codes(rule)

    def test_create_group_before_create_channel_not_flagged(self):
        """A prior create_group action sets exec_ctx.current_group_id at run
        time, so the landing group is not statically knowable. Never invent a
        finding from data we do not have."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "conditions": [],
            "actions": [
                {"type": "create_group", "name_template": "{stream_group}"},
                {"type": "create_channel", "if_exists": "merge"},
            ],
        }
        assert _PIN_CODE not in _codes(rule)

    def test_create_group_after_create_channel_still_flagged(self):
        """Actions run in list order, so a create_group AFTER the create_channel
        cannot have set current_group_id for it."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "conditions": [],
            "actions": [
                {"type": "create_channel", "if_exists": "merge"},
                {"type": "create_group", "name_template": "{stream_group}"},
            ],
        }
        assert _PIN_CODE in _codes(rule)

    def test_unresolvable_landing_group_not_flagged(self):
        """No action group_id and no rule target group: nothing to compare."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": None,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        assert _PIN_CODE not in _codes(rule)

    def test_no_create_channel_action_not_flagged(self):
        """merge_streams is group-agnostic; this check is create_channel only."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "merge_streams", "target": "auto"}],
        }
        assert _PIN_CODE not in _codes(rule)

    @pytest.mark.parametrize("if_exists", ["merge", "merge_only", "skip", "update"])
    def test_every_if_exists_mode_flagged(self, if_exists):
        """The name lookup runs before if_exists is consulted, so a wrong scope
        misses for every mode: merge/merge_only lose the merge, skip and update
        create a duplicate instead of skipping/updating."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": if_exists}],
        }
        assert _PIN_CODE in _codes(rule)

    def test_severity_and_detail(self):
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "orphan_action": "delete",
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        findings = [f for f in analyze_rule(rule) if f.code == _PIN_CODE]
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "warning"
        assert f.detail["match_scope_group_id"] == 7
        assert f.detail["create_group_id"] == 12
        assert f.detail["orphan_action"] == "delete"
        assert f.detail["deletes_orphans"] is True

    def test_orphan_action_keep_still_flagged_without_delete_language(self):
        """Without orphan_action=delete the rule still churns duplicates, it
        just does not delete the previous run's channels."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "orphan_action": "keep",
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
        }
        findings = [f for f in analyze_rule(rule) if f.code == _PIN_CODE]
        assert len(findings) == 1
        assert findings[0].detail["deletes_orphans"] is False
        assert "deleted" not in findings[0].message

    def test_second_action_mismatch_flagged_once(self):
        """First create_channel agrees with the pin, second does not."""
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 12,
            "target_group_id": 12,
            "conditions": [],
            "actions": [
                {"type": "create_channel", "if_exists": "merge"},
                {"type": "create_channel", "if_exists": "merge", "group_id": 30},
            ],
        }
        findings = [f for f in analyze_rule(rule) if f.code == _PIN_CODE]
        assert len(findings) == 1
        assert findings[0].detail["create_group_id"] == 30
        assert findings[0].detail["action_index"] == 1

    def test_missing_actions_key_not_flagged(self):
        rule = {
            "id": 1, "name": "PPV",
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "target_group_id": 12,
            "conditions": [],
        }
        assert _PIN_CODE not in _codes(rule)


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


class TestSortGroupActionAnalyzerAwareness:
    """enhancedchannelmanager-vy4fl: the analyzer must not flag a rule using
    the sort_group action as having an unrecognized/"Unknown" action.

    sort_group is a pure ACTION type — it never appears in
    channel_pipeline_rule_analyzer._GUARD_TYPES (which classifies
    CONDITION types only, for the ANDOR_DROPS_GUARD check) and none of
    the analyzer's per-action checks (_check_merge_streams_no_target_
    channels, _check_merge_scope_not_target_group) match on
    "sort_group", so a rule using it produces zero findings from those
    checks. The analyzer has no generic "unknown action type" finding at
    all — that validation lives in channel_pipeline_schema.Action.validate
    (write-time, HTTP 422) — so this test locks the absence of any
    spurious finding for a rule that legitimately uses sort_group.
    """

    def test_sort_group_action_produces_no_findings(self):
        rule = {
            "id": 10,
            "name": "Auto-sort Sports group",
            "conditions": [
                {"type": "stream_group_contains", "value": "Sports", "connector": "and"},
            ],
            "actions": [
                {"type": "merge_streams", "target": "auto"},
                {"type": "sort_group", "order": "desc", "starting_number": 100},
            ],
            "target_group_id": 5,
        }
        findings = analyze_rule(rule)
        assert findings == []

    def test_sort_group_not_in_guard_types(self):
        assert "sort_group" not in analyzer._GUARD_TYPES


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


# =========================================================================
# RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP — a rule selects normalization
# groups that are globally DISABLED (or missing), so normalization silently
# applies nothing (enhancedchannelmanager-e8p1h).
# =========================================================================
class TestDisabledNormalizationGroupFinding:
    def test_disabled_group_flagged(self):
        rule = {
            "id": 1,
            "name": "Movie Channels",
            "conditions": [],
            "actions": [],
            "normalization_group_ids": [1, 2],
        }
        norm_groups = [
            {"id": 1, "name": "Quality Tags", "enabled": True},
            {"id": 2, "name": "Country Prefixes", "enabled": False},
        ]
        findings = analyze_rule(rule, normalization_groups=norm_groups)
        codes = [f.code for f in findings]
        assert "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP" in codes
        f = next(f for f in findings if f.code == "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP")
        assert f.severity == "warning"
        # Names only the disabled group, not the enabled one.
        assert [g["id"] for g in f.detail["disabled_groups"]] == [2]

    def test_all_groups_enabled_not_flagged(self):
        rule = {
            "id": 1, "name": "Movie Channels",
            "conditions": [], "actions": [],
            "normalization_group_ids": [1, 2],
        }
        norm_groups = [
            {"id": 1, "name": "Quality Tags", "enabled": True},
            {"id": 2, "name": "Country Prefixes", "enabled": True},
        ]
        codes = [f.code for f in analyze_rule(rule, normalization_groups=norm_groups)]
        assert "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP" not in codes

    def test_no_normalization_groups_data_no_finding(self):
        """Without the optional group-state data, the check is a no-op —
        we never invent findings from missing data (bundle path)."""
        rule = {
            "id": 1, "name": "Movie Channels",
            "conditions": [], "actions": [],
            "normalization_group_ids": [2],
        }
        codes = [f.code for f in analyze_rule(rule)]
        assert "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP" not in codes

    def test_missing_group_flagged(self):
        rule = {
            "id": 1, "name": "Dangling",
            "conditions": [], "actions": [],
            "normalization_group_ids": [99],
        }
        norm_groups = [{"id": 1, "name": "Quality Tags", "enabled": True}]
        findings = [
            f for f in analyze_rule(rule, normalization_groups=norm_groups)
            if f.code == "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP"
        ]
        assert len(findings) == 1
        assert findings[0].detail["disabled_groups"][0]["id"] == 99

    def test_analyze_rules_threads_normalization_groups(self):
        rules = [{
            "id": 1, "name": "Movie Channels",
            "conditions": [], "actions": [],
            "normalization_group_ids": [2],
        }]
        norm_groups = [{"id": 2, "name": "Country Prefixes", "enabled": False}]
        result = analyze_rules(rules, normalization_groups=norm_groups)
        codes = [f["code"] for f in result["rules"][0]["findings"]]
        assert "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP" in codes
        assert result["summary"]["warning"] >= 1
