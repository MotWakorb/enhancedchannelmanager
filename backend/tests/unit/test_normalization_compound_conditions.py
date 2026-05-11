"""
Regression tests for compound (AND/OR) condition matching in NormalizationEngine.

GH #217 / bd-hssif: a rule with **OR** logic whose *first* condition mismatched but
a later condition matched never captured a `primary_match` (the old `i == 0` guard in
`_match_compound_conditions`). `_match_compound_conditions` then fell through to
`RuleMatch(matched=True, match_start=0, match_end=len(text))`, and a Strip Prefix /
Remove / Replace action operated on the whole string — blanking the channel name.
The fix captures the first *matching*, non-negated condition's span regardless of
index, which also covers the analogous AND case where condition 1 is a negated guard.
"""
import pytest

from normalization_engine import NormalizationEngine


@pytest.fixture
def engine(test_session):
    return NormalizationEngine(test_session)


def test_or_rule_first_condition_misses_strip_prefix_keeps_channel_name(engine):
    """OR rule: cond1 mismatches, cond2 matches → Strip Prefix removes only the prefix, not the whole name."""
    result = engine.test_rule(
        text="VariantB | Some Channel HD",
        condition_type="",
        condition_value="",
        case_sensitive=False,
        action_type="strip_prefix",
        conditions=[
            {"type": "starts_with", "value": "VariantA"},  # mismatch
            {"type": "starts_with", "value": "VariantB"},  # match
        ],
        condition_logic="OR",
    )
    assert result["matched"] is True
    # Pre-fix: result["after"] == "" (entire name stripped). Post-fix: only "VariantB | " removed.
    assert result["after"] == "Some Channel HD"


def test_or_rule_first_condition_matches_uses_its_span(engine):
    """OR rule: when cond1 matches, behavior is unchanged — cond1's span drives the action."""
    result = engine.test_rule(
        text="VariantA | Another Channel",
        condition_type="",
        condition_value="",
        case_sensitive=False,
        action_type="strip_prefix",
        conditions=[
            {"type": "starts_with", "value": "VariantA"},  # match
            {"type": "starts_with", "value": "VariantB"},  # mismatch
        ],
        condition_logic="OR",
    )
    assert result["matched"] is True
    assert result["after"] == "Another Channel"


def test_and_rule_negated_first_condition_uses_first_positive_match(engine):
    """AND rule: cond1 is a negated guard (no span), cond2 is the positive matcher → cond2's span drives the strip."""
    result = engine.test_rule(
        text="UK | Sports Channel",
        condition_type="",
        condition_value="",
        case_sensitive=False,
        action_type="strip_prefix",
        conditions=[
            {"type": "contains", "value": "XXX", "negate": True},  # passes (text does not contain "XXX")
            {"type": "starts_with", "value": "UK"},                # match → primary span
        ],
        condition_logic="AND",
    )
    assert result["matched"] is True
    # Pre-fix: primary_match stayed None (the i == 0 condition was negated), match_end=len(text) → "".
    assert result["after"] == "Sports Channel"
