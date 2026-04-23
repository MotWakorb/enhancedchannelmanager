"""
Unit, property-based, and ReDoS-adversarial tests for the bd-eio04.14
safe_regex migration in ``backend/normalization_engine.py``.

Covers the five migrated user-regex call sites:

- ``_match_single_condition`` regex-condition branch (``safe_regex.search``)
- ``_match_tag_group`` contains-with-separators branch (``safe_regex.search``)
- ``_match_tag_group`` contains-at-start branch (``safe_regex.match``)
- ``_apply_action`` regex_replace action (``safe_regex.sub``)
- ``_apply_else_action`` regex_replace else-action (``safe_regex.sub``)

Each site has:

1. Happy-path equivalence against the pre-migration ``re.search`` /
   ``re.sub`` result on benign patterns (including fixtures from
   ``backend/tests/fixtures/unicode_fixtures.py`` where applicable).
2. ``None``-sentinel handling: an adversarial pattern
   (``(a|aa)+b`` against a 50-char ``a``-string input) must return the
   documented fallback within 500 ms wall-clock (5x the 100 ms
   safe_regex default timeout).
3. A Hypothesis property-based test asserting stdlib ``re`` and the
   migrated call behave equivalently on benign literal patterns.

Non-goals — deliberately out of scope for this test file:

- Re-testing normalization policy preprocessing (NFC, Cf-whitelist
  strip, superscript conversion). Those contracts live in
  ``test_normalization_parity.py`` / ``test_stream_normalization.py``
  and the ``normalization_canary.py`` harness.
- Re-testing the observability decision-log hook (bd-eio04.9). Those
  contracts live in ``test_normalization_observability.py``.

See bd-eio04.14 grooming for the per-site fallback rationale.
"""

from __future__ import annotations

import logging
import re
import time

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st

import safe_regex
from normalization_engine import NormalizationEngine, RuleMatch
from tests.fixtures.factories import (
    create_normalization_rule,
    create_normalization_rule_group,
    create_tag,
    create_tag_group,
)
from tests.fixtures.unicode_fixtures import ALL_FIXTURES


# =========================================================================
# Shared test constants.
# =========================================================================


# Alternative pattern that reliably exercises the backtracker in the
# ``regex`` library. Paired with a mismatching 50-char input so the
# backtracker has to exhaust every alternation branch.
_EVIL_PATTERN_ALT = r"(a|aa)+b"
_EVIL_INPUT_ALT = "a" * 50 + "!"

# Wall-clock ceiling for adversarial assertions. 500 ms is generous for
# safe_regex's 100 ms default (plus Python overhead + test fixture
# setup); anything above suggests the timeout plumbing didn't engage.
# Matches bd-eio04.15's ``_MAX_ADVERSARIAL_SECONDS`` for parity.
_MAX_ADVERSARIAL_SECONDS = 0.5


# Benign pattern/text strategy for property-based equivalence tests.
# Mirrors bd-eio04.15's strategy: literal-only patterns (via re.escape)
# so stdlib ``re`` and the ``regex`` library agree — the migration is
# about safety, not engine selection, and Unicode-class shorthand
# divergences between engines are not regressions we introduced.
_BENIGN_ALPHABET = "abcXYZ0123 -_:|"
_BENIGN_PATTERN_STRATEGY = st.text(
    alphabet=_BENIGN_ALPHABET, min_size=1, max_size=20,
).map(re.escape)
_BENIGN_TEXT_STRATEGY = st.text(
    alphabet=_BENIGN_ALPHABET, min_size=0, max_size=80,
)


# =========================================================================
# Fixtures.
# =========================================================================


@pytest.fixture
def engine(test_session):
    """NormalizationEngine bound to an in-memory test session."""
    return NormalizationEngine(test_session)


def _mk_regex_rule(session, *, pattern: str, action_type: str = "replace",
                   action_value: str = "", case_sensitive: bool = False,
                   else_action_type: str | None = None,
                   else_action_value: str | None = None):
    """Create a NormalizationRule with a regex condition for direct use
    with _apply_action / _apply_else_action. Returns the rule."""
    group = create_normalization_rule_group(session)
    return create_normalization_rule(
        session,
        group_id=group.id,
        condition_type="regex",
        condition_value=pattern,
        case_sensitive=case_sensitive,
        action_type=action_type,
        action_value=action_value,
        else_action_type=else_action_type,
        else_action_value=else_action_value,
    )


# =========================================================================
# Site 1 — _match_single_condition, regex condition branch.
# =========================================================================


class TestMatchSingleConditionRegex:
    """_match_single_condition regex branch — sentinel: RuleMatch(matched=False)."""

    def test_happy_path_match(self, engine):
        result = engine._match_single_condition("ESPN HD", "regex", r"HD$")
        assert result.matched is True
        assert result.match_start is not None
        assert result.match_end is not None

    def test_happy_path_no_match(self, engine):
        result = engine._match_single_condition("ESPN HD", "regex", r"SD$")
        assert result.matched is False

    def test_case_sensitive(self, engine):
        cs_result = engine._match_single_condition(
            "ESPN hd", "regex", r"HD$", case_sensitive=True,
        )
        ci_result = engine._match_single_condition(
            "ESPN hd", "regex", r"HD$", case_sensitive=False,
        )
        assert cs_result.matched is False
        assert ci_result.matched is True

    def test_invalid_pattern_returns_no_match(self, engine):
        """Malformed pattern must fall through to matched=False, not raise.
        safe_regex.search logs [SAFE_REGEX] WARN and returns None."""
        result = engine._match_single_condition("ESPN HD", "regex", r"(unclosed")
        assert result.matched is False

    @pytest.mark.parametrize(
        "fixture",
        [f for f in ALL_FIXTURES if f.input],
        ids=[f.name for f in ALL_FIXTURES if f.input],
    )
    def test_benign_fixture_matches_letters(self, engine, fixture):
        """For each benign Unicode fixture, matching against its own
        expected_normalized value as a literal pattern must match the
        policy-preprocessed text. Locks: the migration preserves the
        bd-eio04.1 policy's text preprocessing (NFC, Cf-strip, superscript
        conversion) — if this regresses, either the migration broke
        preprocessing or safe_regex dropped the text."""
        # Pull the first ASCII letter run from the expected normalized
        # output as the probe pattern. We need a pattern that (a) is
        # literal ASCII, (b) is stable under re.escape, (c) is present
        # after the shared NormalizationPolicy runs.
        token = re.search(r"[A-Za-z]+", fixture.expected_normalized)
        if not token:
            pytest.skip(f"no ASCII letter run in fixture {fixture.name}")
        pattern = re.escape(token.group(0))
        result = engine._match_single_condition(fixture.input, "regex", pattern)
        assert result.matched is True, (
            f"fixture {fixture.name}: expected {token.group(0)!r} to match "
            f"post-policy text derived from input {fixture.input!r}"
        )

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        derandomize=True,
        max_examples=50,
    )
    @given(pattern=_BENIGN_PATTERN_STRATEGY, text=_BENIGN_TEXT_STRATEGY)
    def test_property_matches_stdlib_on_benign_input(self, engine, pattern, text):
        """For benign literal patterns, safe_regex.search must agree
        with stdlib re.search on match/no-match verdict. Policy
        preprocessing (NFC etc.) is a no-op for ASCII text — baseline
        uses the policy-processed text to match the migrated code's
        own preprocessing step."""
        from normalization_engine import get_default_policy
        policy = get_default_policy()
        proc_text = policy.apply_to_text(text)
        proc_pat = policy.apply_to_text(pattern)
        stdlib_matched = bool(re.search(proc_pat, proc_text, flags=re.IGNORECASE))
        result = engine._match_single_condition(text, "regex", pattern)
        assert result.matched is stdlib_matched

    def test_adversarial_pattern_falls_back_to_no_match(self, engine, caplog):
        """ReDoS-style pattern must return RuleMatch(matched=False)
        within the wall-clock budget and emit a [SAFE_REGEX] WARN."""
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = engine._match_single_condition(
                _EVIL_INPUT_ALT, "regex", _EVIL_PATTERN_ALT,
            )
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS, (
            f"adversarial regex took {elapsed:.3f}s — safe_regex timeout "
            f"did not engage"
        )
        assert result.matched is False
        # Proxy for timeout-counter emission: [SAFE_REGEX] WARN present.
        assert any("[SAFE_REGEX]" in m for m in caplog.messages), (
            f"no [SAFE_REGEX] WARN logged; caplog messages: {caplog.messages!r}"
        )


# =========================================================================
# Sites 2 & 3 — _match_tag_group contains branches (search + match).
# =========================================================================


class TestMatchTagGroupContains:
    """_match_tag_group contains branches — sentinel: non-match RuleMatch."""

    _tg_counter: int = 0

    def _mk_tag_group(self, session, values: list[str], case_sensitive: bool = False) -> int:
        # Unique per-call name so Hypothesis can invoke the property
        # test many times against a function-scoped session without
        # colliding on tag_groups.name UNIQUE.
        TestMatchTagGroupContains._tg_counter += 1
        name = f"TG_test_{TestMatchTagGroupContains._tg_counter}"
        tg = create_tag_group(session, name=name)
        for v in values:
            create_tag(session, group_id=tg.id, value=v, case_sensitive=case_sensitive)
        # bd-eio04.14: invalidate the module-level tag-group cache so
        # successive property-test invocations don't reuse a stale
        # in-memory tag list from a prior tag_group_id.
        from normalization_engine import invalidate_tag_cache
        invalidate_tag_cache()
        return tg.id

    def test_happy_path_separator_on_both_sides(self, engine, test_session):
        """Site 2 (search): tag surrounded by separators matches."""
        tg_id = self._mk_tag_group(test_session, ["WY"])
        result = engine._match_tag_group("Laramie | WY | 5", tg_id, "contains")
        assert result.matched is True
        assert result.matched_tag == "WY"

    def test_happy_path_tag_at_start(self, engine, test_session):
        """Site 3 (match): tag at start followed by separator matches."""
        tg_id = self._mk_tag_group(test_session, ["US"])
        result = engine._match_tag_group("US | ESPN HD", tg_id, "contains")
        assert result.matched is True
        assert result.matched_tag == "US"
        assert result.match_start == 0

    def test_word_boundary_no_inside_match(self, engine, test_session):
        """Short tag must NOT match inside a longer word."""
        tg_id = self._mk_tag_group(test_session, ["LA"])
        result = engine._match_tag_group("Laramie", tg_id, "contains")
        assert result.matched is False

    def test_no_match_empty_text(self, engine, test_session):
        tg_id = self._mk_tag_group(test_session, ["HD"])
        result = engine._match_tag_group("", tg_id, "contains")
        assert result.matched is False

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        derandomize=True,
        max_examples=30,
    )
    @given(
        tag=st.text(alphabet="ABCXYZ", min_size=2, max_size=4),
        text=_BENIGN_TEXT_STRATEGY,
    )
    def test_property_matches_stdlib_contains(self, engine, test_session, tag, text):
        """For benign ASCII tags, the safe_regex-migrated site must
        agree with stdlib re on whether a separator-bounded match
        exists in the text."""
        tg_id = self._mk_tag_group(test_session, [tag])
        # Reconstruct the pre-migration verdict using stdlib re.
        sep = r"[\s:\-|/]+"
        tag_pat = re.escape(tag.lower())
        flags = re.IGNORECASE
        pat_search = r"(" + sep + r")" + tag_pat + r"(?=" + sep + r"|$)"
        pat_match = tag_pat + r"(" + sep + r")"
        stdlib_found = bool(re.search(pat_search, text, flags)) or bool(
            re.match(pat_match, text, flags)
        )
        result = engine._match_tag_group(text, tg_id, "contains")
        # contains loop falls through to matched=False if no tag hits;
        # stdlib_found reflects the same disjunction the migrated code
        # evaluates for the single tag.
        assert result.matched is stdlib_found

    def test_adversarial_tag_value_does_not_hang(self, engine, test_session, caplog):
        """An adversarial tag value that expands into a pathological
        pattern must fall through to the non-match return without
        hanging."""
        # Tag values route through re.escape, so a literal pathological
        # string is neutralized (escape turns metachars into literals).
        # We simulate the worst realistic case by stuffing a long
        # separator-heavy tag value — the 50-char 'a' run plus a
        # separator sentinel stress the backtracker without needing
        # actual meta characters, and the alt class in the search
        # pattern does the work.
        tg_id = self._mk_tag_group(test_session, ["a" * 40])
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = engine._match_tag_group("a" * 200, tg_id, "contains")
        elapsed = time.perf_counter() - start
        # Even if no [SAFE_REGEX] WARN fires (the pattern is benign after
        # re.escape), the wall-clock guard still holds.
        assert elapsed < _MAX_ADVERSARIAL_SECONDS, (
            f"tag-group contains call took {elapsed:.3f}s"
        )
        # Either matched or unmatched is acceptable here — the wall
        # clock is the invariant.
        assert isinstance(result, RuleMatch)


# =========================================================================
# Site 4 — _apply_action regex_replace.
# =========================================================================


class TestApplyActionRegexReplace:
    """_apply_action regex_replace — sentinel: original text unchanged."""

    def test_happy_path_replace(self, engine, test_session):
        rule = _mk_regex_rule(
            test_session, pattern=r"HD$", action_type="regex_replace",
            action_value="",
        )
        match = RuleMatch(matched=True, match_start=5, match_end=7)
        # pattern HD$ on "ESPN HD" -> ""
        result = engine._apply_action("ESPN HD", rule, match)
        assert result == "ESPN "

    def test_backreference_js_to_py(self, engine, test_session):
        """JS-style $1 must be rewritten to Python \\1 and substituted."""
        rule = _mk_regex_rule(
            test_session, pattern=r"(\w+) HD", action_type="regex_replace",
            action_value="$1",
        )
        match = RuleMatch(matched=True, match_start=0, match_end=7)
        result = engine._apply_action("ESPN HD", rule, match)
        assert result == "ESPN"

    def test_non_regex_condition_returns_text(self, engine, test_session):
        """regex_replace without a regex condition must log and return input."""
        group = create_normalization_rule_group(test_session)
        rule = create_normalization_rule(
            test_session, group_id=group.id,
            condition_type="contains", condition_value="HD",
            action_type="regex_replace", action_value="",
        )
        match = RuleMatch(matched=True, match_start=0, match_end=7)
        result = engine._apply_action("ESPN HD", rule, match)
        assert result == "ESPN HD"

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        derandomize=True,
        max_examples=50,
    )
    @given(pattern=_BENIGN_PATTERN_STRATEGY, text=_BENIGN_TEXT_STRATEGY)
    def test_property_matches_stdlib_on_benign_input(
        self, engine, test_session, pattern, text,
    ):
        rule = _mk_regex_rule(
            test_session, pattern=pattern, action_type="regex_replace",
            action_value="",
        )
        match = RuleMatch(matched=True, match_start=0, match_end=0)
        expected = re.sub(pattern, "", text, flags=re.IGNORECASE)
        got = engine._apply_action(text, rule, match)
        assert got == expected

    def test_adversarial_pattern_returns_original(self, engine, test_session, caplog):
        """ReDoS pattern -> safe_regex.sub returns text unchanged."""
        rule = _mk_regex_rule(
            test_session, pattern=_EVIL_PATTERN_ALT, action_type="regex_replace",
            action_value="",
        )
        match = RuleMatch(matched=True, match_start=0, match_end=0)
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            out = engine._apply_action(_EVIL_INPUT_ALT, rule, match)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS, (
            f"adversarial regex_replace took {elapsed:.3f}s"
        )
        assert out == _EVIL_INPUT_ALT
        assert any("[SAFE_REGEX]" in m for m in caplog.messages)

    def test_invalid_pattern_returns_original(self, engine, test_session):
        """Malformed pattern must fall through to text unchanged."""
        rule = _mk_regex_rule(
            test_session, pattern=r"(unclosed", action_type="regex_replace",
            action_value="X",
        )
        match = RuleMatch(matched=True, match_start=0, match_end=0)
        out = engine._apply_action("ESPN HD", rule, match)
        assert out == "ESPN HD"


# =========================================================================
# Site 5 — _apply_else_action regex_replace.
# =========================================================================


class TestApplyElseActionRegexReplace:
    """_apply_else_action regex_replace — sentinel: original text unchanged."""

    def test_happy_path_else_replace(self, engine, test_session):
        """Else-action runs regex_replace against the whole text."""
        rule = _mk_regex_rule(
            test_session, pattern=r"\bSports\b", action_type="replace",
            action_value="", else_action_type="regex_replace",
            else_action_value="News",
        )
        result = engine._apply_else_action("ESPN Sports HD", rule)
        assert result == "ESPN News HD"

    def test_no_condition_value_returns_text(self, engine, test_session):
        """Missing condition_value -> text unchanged (pre-migration parity)."""
        group = create_normalization_rule_group(test_session)
        rule = create_normalization_rule(
            test_session, group_id=group.id,
            condition_type="regex", condition_value="",
            action_type="remove",
            else_action_type="regex_replace", else_action_value="X",
        )
        result = engine._apply_else_action("ESPN", rule)
        assert result == "ESPN"

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        derandomize=True,
        max_examples=50,
    )
    @given(pattern=_BENIGN_PATTERN_STRATEGY, text=_BENIGN_TEXT_STRATEGY)
    def test_property_matches_stdlib_on_benign_input(
        self, engine, test_session, pattern, text,
    ):
        rule = _mk_regex_rule(
            test_session, pattern=pattern, action_type="remove",
            else_action_type="regex_replace", else_action_value="",
        )
        expected = re.sub(pattern, "", text, flags=re.IGNORECASE)
        got = engine._apply_else_action(text, rule)
        assert got == expected

    def test_adversarial_pattern_returns_original(
        self, engine, test_session, caplog,
    ):
        """ReDoS pattern as else-action condition -> text unchanged."""
        rule = _mk_regex_rule(
            test_session, pattern=_EVIL_PATTERN_ALT, action_type="remove",
            else_action_type="regex_replace", else_action_value="",
        )
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            out = engine._apply_else_action(_EVIL_INPUT_ALT, rule)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS, (
            f"adversarial else_action regex_replace took {elapsed:.3f}s"
        )
        assert out == _EVIL_INPUT_ALT
        assert any("[SAFE_REGEX]" in m for m in caplog.messages)


# =========================================================================
# Smoke test — end-to-end via normalize() to prove bd-eio04.9's
# observability hook still fires after migration.
# =========================================================================


class TestNormalizeObservabilityStillFires:
    """Assert bd-eio04.9's record_normalization_decision hook still runs
    after the bd-eio04.14 migration. A regression here means the
    migration broke the decision log, which the nightly canary
    (``normalization_canary.py``) and SLO-5 dashboards depend on."""

    def test_normalize_records_decision(self, engine, test_session, monkeypatch):
        calls: list[dict] = []

        def _capture(**kwargs):
            calls.append(kwargs)

        import observability
        monkeypatch.setattr(
            observability, "record_normalization_decision", _capture,
        )
        # Empty rule set — exercises the policy-only path (no user
        # regex calls fire) but the hook still runs.
        result = engine.normalize("ESPN HD")
        assert isinstance(result.normalized, str)
        assert len(calls) == 1
        assert "policy_version" in calls[0]
        assert calls[0]["input_text"] == "ESPN HD"
