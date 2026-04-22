"""
Unit tests for :mod:`tasks.rule_lint_scan` (bd-eio04.7 migration scan).

Seeds the three rule tables with a mix of benign and known-evil
patterns, runs the scan, and asserts:

  - ``rule_lint_findings`` is populated with the expected codes for the
    known-evil rows.
  - No benign row gets flagged.
  - Rule rows themselves are NOT mutated (scan is read-only).
  - Re-running the scan is idempotent — same finding set, no duplicates.
"""
from __future__ import annotations

import json

import pytest

from models import (
    AutoCreationRule,
    DummyEPGProfile,
    NormalizationRule,
    NormalizationRuleGroup,
    RuleLintFinding,
)
from tasks.rule_lint_scan import (
    RULE_TYPE_AUTO_CREATION,
    RULE_TYPE_DUMMY_EPG,
    RULE_TYPE_NORMALIZATION,
    run_scan,
)


# =========================================================================
# Helpers to seed rule rows.
# =========================================================================


def _seed_group(session) -> NormalizationRuleGroup:
    group = NormalizationRuleGroup(
        name="Test Group", enabled=True, priority=0, is_builtin=False
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def _seed_norm_rule_benign(session, group_id: int) -> NormalizationRule:
    rule = NormalizationRule(
        group_id=group_id,
        name="Strip HD suffix",
        enabled=True,
        priority=0,
        condition_type="regex",
        condition_value=r"\s*HD$",
        action_type="remove",
        is_builtin=False,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _seed_norm_rule_evil(session, group_id: int) -> NormalizationRule:
    rule = NormalizationRule(
        group_id=group_id,
        name="Evil nested quantifier",
        enabled=True,
        priority=1,
        condition_type="regex",
        condition_value=r"(a+)+b",
        action_type="remove",
        is_builtin=False,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _seed_norm_rule_too_long(session, group_id: int) -> NormalizationRule:
    """Build a row whose ``condition_value`` is length-capped by the
    column (500 chars) but whose compound-conditions JSON holds an
    oversize pattern. Exercises the scan's JSON-walking path."""
    conditions = [
        {"type": "regex", "value": "a" * 600},
    ]
    rule = NormalizationRule(
        group_id=group_id,
        name="Oversize pattern in JSON",
        enabled=True,
        priority=2,
        condition_type=None,
        condition_value=None,
        conditions=json.dumps(conditions),
        condition_logic="AND",
        action_type="remove",
        is_builtin=False,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _seed_auto_creation_rule_evil(session) -> AutoCreationRule:
    """Evil regex in both sort_regex and a set_variable action pattern."""
    conditions = [{"type": "stream_name_matches", "value": "(.*)*X"}]
    actions = [
        {
            "type": "set_variable",
            "variable_name": "foo",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": "(a+)+b",
        }
    ]
    rule = AutoCreationRule(
        name="Evil AC rule",
        enabled=True,
        priority=0,
        conditions=json.dumps(conditions),
        actions=json.dumps(actions),
        run_on_refresh=False,
        stop_on_first_match=True,
        sort_field="stream_name_regex",
        sort_order="asc",
        probe_on_sort=False,
        sort_regex=r"(\w+)+$",  # evil
        stream_sort_order="asc",
        skip_struck_streams=False,
        orphan_action="delete",
        match_scope_target_group=False,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _seed_auto_creation_rule_benign(session) -> AutoCreationRule:
    conditions = [{"type": "stream_name_contains", "value": "Sports"}]
    actions = [
        {"type": "create_channel", "name_template": "{stream_name}"}
    ]
    rule = AutoCreationRule(
        name="Benign AC rule",
        enabled=True,
        priority=1,
        conditions=json.dumps(conditions),
        actions=json.dumps(actions),
        run_on_refresh=False,
        stop_on_first_match=True,
        sort_order="asc",
        probe_on_sort=False,
        stream_sort_order="asc",
        skip_struck_streams=False,
        orphan_action="delete",
        match_scope_target_group=False,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _seed_dummy_epg_profile_evil(session) -> DummyEPGProfile:
    """Evil pattern in title_pattern, plus an evil is_regex=True
    substitution pair."""
    subs = [
        {"find": "(a+)+b", "replace": "", "is_regex": True, "enabled": True},
        {"find": "literal-string", "replace": "x", "is_regex": False, "enabled": True},
    ]
    profile = DummyEPGProfile(
        name="Evil Profile",
        enabled=True,
        name_source="channel",
        stream_index=1,
        title_pattern=r"(.*)*X",  # evil
        time_pattern=r"\d{1,2}:\d{2}",  # benign
        date_pattern=None,
        substitution_pairs=json.dumps(subs),
        event_timezone="US/Eastern",
        program_duration=180,
        tvg_id_template="ecm-{channel_number}",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def _seed_dummy_epg_profile_benign_b1g(session) -> DummyEPGProfile:
    """Regression case — B1G production title_pattern (spike FP)."""
    b1g_pattern = (
        r"(?<channel>.+?):\s+(?<event>"
        r"(?:(?<sport>.+?)\s+\|\s+(?<team1>.+?)\s+vs\s+(?<team2>.+?))"
        r"|(?:.+?))\s+@\s+"
    )
    profile = DummyEPGProfile(
        name="B1G Advanced EPG",
        enabled=True,
        name_source="channel",
        stream_index=1,
        title_pattern=b1g_pattern,
        event_timezone="US/Eastern",
        program_duration=180,
        tvg_id_template="ecm-{channel_number}",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


# =========================================================================
# Tests.
# =========================================================================


class TestRunScanWritesFindings:
    def test_flags_evil_patterns(self, test_session):
        group = _seed_group(test_session)
        _seed_norm_rule_benign(test_session, group.id)
        evil = _seed_norm_rule_evil(test_session, group.id)

        summary = run_scan(test_session)

        findings = test_session.query(RuleLintFinding).filter(
            RuleLintFinding.rule_type == RULE_TYPE_NORMALIZATION,
            RuleLintFinding.rule_id == evil.id,
        ).all()
        assert len(findings) == 1
        assert findings[0].code == "REGEX_NESTED_QUANTIFIER"
        assert findings[0].field == "condition_value"
        assert summary["normalization"]["rules_flagged"] == 1
        assert summary["normalization"]["findings"] == 1

    def test_flags_all_three_rule_types(self, test_session):
        group = _seed_group(test_session)
        _seed_norm_rule_evil(test_session, group.id)
        _seed_auto_creation_rule_evil(test_session)
        _seed_auto_creation_rule_benign(test_session)
        _seed_dummy_epg_profile_evil(test_session)
        _seed_dummy_epg_profile_benign_b1g(test_session)

        summary = run_scan(test_session)

        # Normalization: 1 flagged, 1 finding
        assert summary["normalization"]["rules_flagged"] == 1
        # Auto-creation: evil rule has sort_regex + condition + action = 3 findings
        ac_findings = test_session.query(RuleLintFinding).filter(
            RuleLintFinding.rule_type == RULE_TYPE_AUTO_CREATION
        ).all()
        assert len(ac_findings) == 3
        ac_fields = {f.field for f in ac_findings}
        assert "sort_regex" in ac_fields
        assert "conditions[0].value" in ac_fields
        assert "actions[0].pattern" in ac_fields
        # Dummy-EPG: evil profile has title_pattern + substitution[0].find = 2 findings
        de_findings = test_session.query(RuleLintFinding).filter(
            RuleLintFinding.rule_type == RULE_TYPE_DUMMY_EPG
        ).all()
        # B1G profile must NOT contribute (spike regression case).
        assert len(de_findings) == 2
        de_fields = {f.field for f in de_findings}
        assert "title_pattern" in de_fields
        assert "substitution_pairs[0].find" in de_fields

    def test_b1g_regression_not_flagged(self, test_session):
        """The spike's critical regression: B1G title_pattern must NOT be
        flagged by the scan, because regexploit would have (0 FP target)."""
        _seed_dummy_epg_profile_benign_b1g(test_session)
        run_scan(test_session)
        findings = test_session.query(RuleLintFinding).filter(
            RuleLintFinding.rule_type == RULE_TYPE_DUMMY_EPG
        ).all()
        assert findings == []

    def test_oversize_pattern_in_compound_conditions_flagged(self, test_session):
        group = _seed_group(test_session)
        rule = _seed_norm_rule_too_long(test_session, group.id)
        run_scan(test_session)
        findings = test_session.query(RuleLintFinding).filter(
            RuleLintFinding.rule_type == RULE_TYPE_NORMALIZATION,
            RuleLintFinding.rule_id == rule.id,
        ).all()
        assert len(findings) == 1
        assert findings[0].code == "REGEX_TOO_LONG"
        assert findings[0].field == "conditions[0].value"


class TestRunScanIsIdempotent:
    def test_second_run_produces_same_findings(self, test_session):
        group = _seed_group(test_session)
        _seed_norm_rule_evil(test_session, group.id)
        _seed_auto_creation_rule_evil(test_session)
        _seed_dummy_epg_profile_evil(test_session)

        first = run_scan(test_session)
        first_set = {
            (f.rule_type, f.rule_id, f.field, f.code)
            for f in test_session.query(RuleLintFinding).all()
        }

        second = run_scan(test_session)
        second_set = {
            (f.rule_type, f.rule_id, f.field, f.code)
            for f in test_session.query(RuleLintFinding).all()
        }

        # Idempotency contract — per-rule findings are wiped and
        # rewritten, so the (rule_type, rule_id, field, code) set is
        # stable and the total counts are equal. IDs may or may not be
        # reused depending on the SQLite autoincrement state; the
        # content-level assertion is the load-bearing one.
        assert first["total_findings"] == second["total_findings"]
        assert first_set == second_set, (
            f"Scan not idempotent — first={first_set}, second={second_set}"
        )
        # And the total row count in the table matches the per-scan total
        # (no duplicate rows accumulated).
        assert (
            test_session.query(RuleLintFinding).count()
            == second["total_findings"]
        )

    def test_clears_stale_findings_when_rule_fixed(self, test_session):
        """If a pattern is fixed out-of-band between scans, the stale
        finding must be cleared rather than sticking around."""
        group = _seed_group(test_session)
        rule = _seed_norm_rule_evil(test_session, group.id)
        run_scan(test_session)
        assert test_session.query(RuleLintFinding).count() == 1

        # Simulate an out-of-band fix.
        rule.condition_value = r"\s*HD$"
        test_session.commit()
        run_scan(test_session)
        assert test_session.query(RuleLintFinding).count() == 0


class TestRunScanDoesNotMutateRules:
    def test_rules_unchanged_after_scan(self, test_session):
        """Read-only contract: no rule row may have ``updated_at`` or
        any data field changed by the scan."""
        group = _seed_group(test_session)
        evil_norm = _seed_norm_rule_evil(test_session, group.id)
        evil_ac = _seed_auto_creation_rule_evil(test_session)
        evil_de = _seed_dummy_epg_profile_evil(test_session)

        before_norm = {
            "name": evil_norm.name,
            "condition_value": evil_norm.condition_value,
            "enabled": evil_norm.enabled,
            "updated_at": evil_norm.updated_at,
        }
        before_ac = {
            "name": evil_ac.name,
            "sort_regex": evil_ac.sort_regex,
            "enabled": evil_ac.enabled,
            "updated_at": evil_ac.updated_at,
        }
        before_de = {
            "name": evil_de.name,
            "title_pattern": evil_de.title_pattern,
            "enabled": evil_de.enabled,
            "updated_at": evil_de.updated_at,
        }

        run_scan(test_session)
        test_session.refresh(evil_norm)
        test_session.refresh(evil_ac)
        test_session.refresh(evil_de)

        assert evil_norm.condition_value == before_norm["condition_value"]
        assert evil_norm.enabled == before_norm["enabled"]
        assert evil_norm.updated_at == before_norm["updated_at"]

        assert evil_ac.sort_regex == before_ac["sort_regex"]
        assert evil_ac.enabled == before_ac["enabled"]
        assert evil_ac.updated_at == before_ac["updated_at"]

        assert evil_de.title_pattern == before_de["title_pattern"]
        assert evil_de.enabled == before_de["enabled"]
        assert evil_de.updated_at == before_de["updated_at"]


class TestRunScanSummary:
    def test_summary_shape(self, test_session):
        summary = run_scan(test_session)
        assert set(summary.keys()) == {
            "normalization",
            "auto_creation",
            "dummy_epg",
            "total_findings",
        }
        for key in ("normalization", "auto_creation", "dummy_epg"):
            assert set(summary[key].keys()) == {
                "rules_scanned",
                "rules_flagged",
                "findings",
            }

    def test_empty_db_produces_zero_findings(self, test_session):
        summary = run_scan(test_session)
        assert summary["total_findings"] == 0
        assert summary["normalization"]["rules_scanned"] == 0
        assert summary["auto_creation"]["rules_scanned"] == 0
        assert summary["dummy_epg"]["rules_scanned"] == 0


class TestRunScanHandlesBadJson:
    def test_malformed_conditions_json_is_skipped(self, test_session):
        """A row with corrupt ``conditions`` JSON should not crash the
        scan — just skip the JSON walk for that row."""
        group = _seed_group(test_session)
        rule = NormalizationRule(
            group_id=group.id,
            name="Corrupt JSON",
            enabled=True,
            priority=0,
            condition_type="contains",
            condition_value="foo",
            conditions="{not valid json}",
            condition_logic="AND",
            action_type="remove",
            is_builtin=False,
        )
        test_session.add(rule)
        test_session.commit()

        # Should not raise.
        summary = run_scan(test_session)
        # And the benign condition_value (contains, not regex) produces
        # no findings.
        assert summary["normalization"]["findings"] == 0
