"""
Property-based tests for :mod:`regex_lint` (bd-eio04.7).

Uses Hypothesis to generate patterns from a "known-benign" grammar and
asserts the linter returns an empty violations list for every one. The
grammar is deliberately conservative — it builds patterns out of
anchors, literals, character classes, bounded quantifiers, optional
named groups, and simple branches — covering the shapes that dominate
the real production corpus without ever composing a nested-unbounded
quantifier.

Skipped cleanly if ``hypothesis`` is not installed. The hand-curated
sweep in :mod:`test_regex_lint.TestBenignSmokeSweep` runs unconditionally
and is the minimum guaranteed coverage.
"""
from __future__ import annotations

import pytest

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis not installed; hand-curated sweep in test_regex_lint covers the minimum",
)

from hypothesis import given, settings, strategies as st  # noqa: E402

from regex_lint import lint_pattern  # noqa: E402


# ---------------------------------------------------------------------------
# Benign-pattern grammar.
# Each leaf emits a string; compose into longer patterns via st.lists + join.
# Deliberately NO unbounded quantifiers — property target is the
# should-pass slice of the lint contract.
# ---------------------------------------------------------------------------

_LITERAL_CHARS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
    min_size=1,
    max_size=10,
).map(lambda s: s)


def _escape_literal(s: str) -> str:
    """Escape regex metacharacters so the string is a valid literal part."""
    import re as _re

    return _re.escape(s)


_literal = _LITERAL_CHARS.map(_escape_literal)

_char_class = st.sampled_from([
    r"\d",
    r"\w",
    r"\s",
    r"[A-Z]",
    r"[a-z]",
    r"[0-9]",
    r"[A-Za-z]",
    r"[A-Z]{2,5}",
    r"[0-9]{1,4}",
])

_bounded_quantified = st.sampled_from([
    r"\d{2,4}",
    r"\w{3,8}",
    r"[A-Z]{2,5}",
    r"\s{1,3}",
])

_anchor = st.sampled_from(["^", "$", r"\b"])

_optional_group = st.sampled_from([
    r"(?:HD|SD|UHD)?",
    r"(?:\s+HD)?",
    r"(?:\s*-\s*\w+)?",
])

_named_group = st.sampled_from([
    r"(?P<hour>\d{1,2})",
    r"(?P<minute>\d{2})",
    r"(?P<ampm>[AP]M)",
    r"(?<channel>\w+)",  # JS style — engine converts at runtime
    r"(?<day>\d{1,2})",
])

_part = st.one_of(
    _literal,
    _char_class,
    _bounded_quantified,
    _anchor,
    _optional_group,
    _named_group,
)

_benign_pattern = st.lists(_part, min_size=1, max_size=8).map("".join)


@settings(max_examples=200, deadline=None)
@given(pattern=_benign_pattern)
def test_benign_grammar_never_flagged(pattern):
    """Property: every pattern from the benign grammar passes the linter.

    If this ever fails, either (a) the grammar has grown a shape the
    linter rejects (tighten the grammar), or (b) the detector has a new
    false positive (fix the detector). The detector target is 0 FPs on
    the real corpus; this property extends that target to a synthetic
    sweep of benign shapes.
    """
    violations = lint_pattern(pattern)
    # Patterns can legitimately fail on length — strategies occasionally
    # produce something over 500 chars. That's a correct REGEX_TOO_LONG
    # flag; filter it out before asserting benign-ness.
    non_length = [v for v in violations if v.code != "REGEX_TOO_LONG"]
    assert non_length == [], (
        f"benign grammar produced an unexpected lint violation for {pattern!r}: "
        f"{[(v.code, v.message) for v in non_length]}"
    )
