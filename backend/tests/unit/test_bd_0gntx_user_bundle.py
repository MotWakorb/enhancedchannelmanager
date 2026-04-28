"""
Acceptance test for bd-0gntx — the 2026-04-28 user's rules.yaml.

This bundle is the real-world motivator for bd-0gntx. Each rule in
``user_2026_04_28_rules.yaml`` reproduces a specific configuration
bug I called out in the diagnosis. This file pins the analyzer's
output against that exact fixture so a future regression that drops
a finding is caught immediately.

If a finding code here ever fails, do NOT relax the assertion —
investigate whether the analyzer regressed.

Note: ``MERGE_STREAMS_NO_TARGET_CHANNELS`` is not exercised here
because the user's rules all have ``target_group_id: null`` (the
bundle's merge rules pick a target dynamically). The dedicated unit
tests in ``test_auto_creation_rule_analyzer.py`` cover that finding.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from auto_creation_rule_analyzer import analyze_rules


_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "bd_0gntx"
    / "user_2026_04_28_rules.yaml"
)


@pytest.fixture(scope="module")
def user_rules() -> list[dict]:
    """Load the user's rules.yaml fixture as a list of rule dicts."""
    data = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "rules" in data, (
        f"Fixture {_FIXTURE} must contain a top-level 'rules' list."
    )
    return data["rules"]


@pytest.fixture(scope="module")
def analysis(user_rules) -> dict:
    return analyze_rules(user_rules)


@pytest.fixture(scope="module")
def findings_by_rule(analysis) -> dict[str, list[dict]]:
    """Map rule_name → list of finding dicts."""
    return {r["rule_name"]: r["findings"] for r in analysis["rules"]}


# =========================================================================
# Per-rule findings.
# =========================================================================


def _codes(findings: list[dict]) -> set[str]:
    return {f["code"] for f in findings}


def test_sports_networks_rule_surfaces_all_three_finding_codes(findings_by_rule):
    """The Sports rule has all three of the user's bugs at once.

    - ``stream_group_matches: UK|`` → REGEX_TRIVIALLY_MATCHES_ALL
    - ``stream_group_matches: US|`` → REGEX_TRIVIALLY_MATCHES_ALL (again)
    - ``stream_group_contains: ^4K`` → OPERATOR_VALUE_LOOKS_LIKE_REGEX
    - OR'd group_matches/group_contains drop the name_in_group guard
      → ANDOR_DROPS_GUARD
    """
    findings = findings_by_rule["Sports Networks - excl Fr and Es"]
    codes = _codes(findings)
    assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes
    assert "OPERATOR_VALUE_LOOKS_LIKE_REGEX" in codes
    assert "ANDOR_DROPS_GUARD" in codes


def test_movie_networks_uk_add_is_clean(findings_by_rule):
    """The user's working rule — must produce zero findings.

    Uses the correct shape: ``^UK\\|`` (anchored, escaped pipe), and
    ANDs the group filter with the name_in_group guard. This is the
    template the user should follow for the others.
    """
    findings = findings_by_rule["Movie Networks - UK add"]
    assert findings == [], (
        f"Reference 'clean' rule produced findings: "
        f"{[(f['code'], f['message']) for f in findings]}"
    )


def test_movie_networks_us_add_flagged_as_trivially_matches_all(
    findings_by_rule,
):
    """Same bug as Sports' US|: regex ``US|`` matches everything."""
    findings = findings_by_rule["Movie Networks - US Add"]
    codes = _codes(findings)
    assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes


def test_movie_networks_4k_add_redundant_caret(findings_by_rule):
    """The user's ``^\\^4k`` typo — anchor + literal caret."""
    findings = findings_by_rule["Movie Networks - 4K Add"]
    codes = _codes(findings)
    assert "REGEX_REDUNDANT_ESCAPE_CARET" in codes


def test_nba_league_pass_has_same_three_bugs(findings_by_rule):
    """A copy of the Sports rule — same three bug codes apply."""
    findings = findings_by_rule["NBA League Pass - excl Fr and Es (Copy)"]
    codes = _codes(findings)
    assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes
    # ``^4k`` (lowercase) under stream_group_contains → operator mismatch.
    assert "OPERATOR_VALUE_LOOKS_LIKE_REGEX" in codes
    assert "ANDOR_DROPS_GUARD" in codes


def test_kids_channels_us_pipe_flagged(findings_by_rule):
    """``stream_group_matches: US|`` — same bug as the others."""
    findings = findings_by_rule["Kids Channels - excl Fr and Es (Copy) (Copy)"]
    codes = _codes(findings)
    assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes


def test_priority_auto_sort_rule_unaffected(findings_by_rule):
    """The probe-only rule has no patterns or guards — must be quiet."""
    findings = findings_by_rule["Priority auto sort"]
    assert findings == []


# =========================================================================
# Aggregate counts — sanity check on the summary.
# =========================================================================


def test_summary_has_only_warnings(analysis):
    """Phase 1 emits no errors and no info — pure warnings."""
    s = analysis["summary"]
    assert s["error"] == 0
    assert s["info"] == 0
    assert s["warning"] >= 6  # at least one per buggy rule


def test_six_buggy_rules_have_at_least_one_finding(analysis):
    """Every rule except 'Priority auto sort' is buggy in some way."""
    buggy = [
        r for r in analysis["rules"]
        if r["rule_name"] != "Priority auto sort"
        and r["rule_name"] != "Movie Networks - UK add"
    ]
    for r in buggy:
        assert r["findings"], (
            f"Rule {r['rule_name']!r} expected to be flagged but produced "
            f"no findings."
        )


# =========================================================================
# Bundle-mode smoke: the saved fixture must round-trip through
# analyze_rules without errors. If the fixture file ever changes shape
# (yaml schema bump, key rename), this catches it before the
# per-rule tests.
# =========================================================================


def test_fixture_loads_cleanly(user_rules):
    assert len(user_rules) == 7
    assert all("name" in r for r in user_rules)
    assert all("conditions" in r for r in user_rules)
    assert all("actions" in r for r in user_rules)
