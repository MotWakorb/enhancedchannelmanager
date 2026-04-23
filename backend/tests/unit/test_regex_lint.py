"""
Unit tests for :mod:`regex_lint` — write-time pattern linter (bd-eio04.7).

The detector was selected by the bd-eio04.11 spike (hand-rolled AST walker
over regexploit). These tests lock in the spike's empirical results:

  - 7/7 known-evil-with-killer patterns must flag.
  - 32/32 real-corpus patterns (including the B1G production
    ``title_pattern``) must NOT flag.
  - 3/3 ambiguous-no-killer patterns must NOT flag (Python ``re``
    optimizes them to O(n)).

Fixtures mirror the spike's test sets so future detector changes are
verified against the same corpus.
"""
from __future__ import annotations

import pytest

import regex_lint
from regex_lint import (
    LintViolation,
    lint_actions_json,
    lint_conditions_json,
    lint_pattern,
    lint_pattern_fields,
    lint_substitution_pairs,
    violations_to_http_detail,
)


# =========================================================================
# Fixture sets (lifted from /tmp/bd-eio04.11-eval.py KNOWN_EVIL / corpus).
# =========================================================================

# Known-evil: nested unbounded quantifier + killer. Empirically exploitable
# in stdlib re (verified in the spike with 5 s timeouts). The linter MUST
# flag all seven.
KNOWN_EVIL_WITH_KILLER: list[str] = [
    r"(a+)+b",
    r"^(\w+\s?)*$",
    r"(a*)*b",
    r"(.*)*X",
    r"(a+)*b",
    r"(.+)+X",
    r"(\w+)+$",
]

# Ambiguous-no-killer: Python's re engine either optimizes these to O(n)
# (bare (a*)*) or factors common prefixes ((a|a) -> a). NOT exploitable in
# our engine — the linter MUST NOT flag them.
AMBIGUOUS_NO_KILLER: list[str] = [
    r"(a*)*",
    r"(.*)*",
    r"(a|a)+$",
]

# Real-corpus: the 32 patterns harvested from production in the bd-eio04.11
# spike. Zero-false-positive target — all must pass lint. The critical
# entry is the B1G ``title_pattern`` (index 14 below) which regexploit
# falsely flagged.
REAL_CORPUS: list[str] = [
    r".*",
    r"HD",
    r"^\w+:",
    r"^(\w+):",
    r"^US:\s*",
    r"^(NOTFOUND)",
    r"(?P<name>.+)",
    r"^(\w+):\s*(.*)",
    r"(?P<hour>\d+)pm",
    r"(?<hour>\d+)(?<ampm>pm)",
    r"(?P<hour>\d+)(?P<ampm>pm)",
    r"(?<month>\d{2})/(?<day>\d{2})",
    r"(?P<month>\d{2})/(?P<day>\d{2})",
    (
        r"(?<hour>\d{1,2})(?:\s*:\s*(?<minute>\d{2}))?\s*"
        r"(?<ampm>[AaPp][Mm])(?:\s+(?<timezone>[A-Z]{1,5}))?"
    ),
    # Critical false-positive regression case — B1G Advanced EPG
    # title_pattern. regexploit flagged this; hand-rolled correctly passes.
    (
        r"(?<channel>.+?):\s+(?<event>"
        r"(?:(?<sport>.+?)\s+\|\s+(?<team1>.+?)\s+vs\s+(?<team2>.+?))"
        r"|(?:.+?))\s+@\s+"
    ),
    (
        r"(?<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\d{1,2})"
        r"[/\s]\s*(?<day>\d{1,2})(?:[/,\s]+(?<year>\d{2,4}))?"
    ),
    r"(?P<title>.+)",
    r"(?P<title>.+?)\\s*HD",
    r"(?P<title>NEVER_MATCHES)",
    r"(?P<callsign>.+)",
    r"(?P<code>.+)",
    r"(?P<k>.+)",
    r"(?P<league>.+)",
    r"(?P<callsign>\S+)\s+(?P<quality>HD|SD)?",
    r"(?P<league>\w+)-(?P<team>\w+)",
    r"(?P<a>\w+)-(?P<b>\w+)-(?P<c>\w+)",
    r"(?P<a>\w+)",
    r"(?P<ch>\w+)",
    r"(?P<v>\w+)",
    r"(?P<v>.+)",
    r"(?P<title>.+) (?P<hour>\d+):(?P<minute>\d+)(?P<ampm>[AP]M)",
    r"(?P<title>.+) (?P<hour>\d+):(?P<minute>\d+)",
]


# =========================================================================
# Core detector contract — spike parity lock.
# =========================================================================


class TestSpikeParityKnownEvil:
    """Every known-evil pattern must emit REGEX_NESTED_QUANTIFIER."""

    @pytest.mark.parametrize("pattern", KNOWN_EVIL_WITH_KILLER)
    def test_known_evil_flagged(self, pattern):
        viols = lint_pattern(pattern, field="condition_value")
        codes = [v.code for v in viols]
        assert "REGEX_NESTED_QUANTIFIER" in codes, (
            f"Expected REGEX_NESTED_QUANTIFIER for {pattern!r}; got {codes}"
        )


class TestSpikeParityAmbiguousNoKiller:
    """Python re optimizes these — MUST NOT flag."""

    @pytest.mark.parametrize("pattern", AMBIGUOUS_NO_KILLER)
    def test_ambiguous_no_killer_passes(self, pattern):
        viols = lint_pattern(pattern, field="condition_value")
        assert viols == [], (
            f"Expected no violations for {pattern!r}; got "
            f"{[(v.code, v.message) for v in viols]}"
        )


class TestSpikeParityRealCorpus:
    """All 32 real-corpus patterns must pass — zero-FP target."""

    @pytest.mark.parametrize("pattern", REAL_CORPUS)
    def test_real_corpus_passes(self, pattern):
        viols = lint_pattern(pattern, field="title_pattern")
        assert viols == [], (
            f"Regression: real-corpus pattern flagged — {pattern!r}: "
            f"{[(v.code, v.message) for v in viols]}"
        )

    def test_b1g_title_pattern_critical_regression(self):
        """The one pattern regexploit falsely flagged in the spike.

        Lifted explicitly so a grep for the bead ID finds this test.
        bd-eio04.11 spike empirically verified this pattern runs in ~2.6 ms
        on a 393-char adversarial input — NOT exploitable.
        """
        b1g_pattern = (
            r"(?<channel>.+?):\s+(?<event>"
            r"(?:(?<sport>.+?)\s+\|\s+(?<team1>.+?)\s+vs\s+(?<team2>.+?))"
            r"|(?:.+?))\s+@\s+"
        )
        viols = lint_pattern(b1g_pattern, field="title_pattern")
        assert viols == [], (
            f"bd-eio04.11 regression: B1G title_pattern flagged: "
            f"{[(v.code, v.message) for v in viols]}"
        )

    def test_fp_rate_is_zero_on_full_corpus(self):
        """Explicit FP-rate assertion — the spike's headline metric."""
        fps = [p for p in REAL_CORPUS if lint_pattern(p)]
        assert len(fps) == 0, (
            f"FP rate on real corpus: {len(fps)}/{len(REAL_CORPUS)} "
            f"(spike target: 0). Flagged: {fps!r}"
        )


# =========================================================================
# Per-code fixture tests.
# =========================================================================


class TestRegexTooLong:
    def test_pattern_at_limit_passes(self):
        """Exactly at the 500-char cap is still accepted."""
        pattern = "a" * regex_lint.MAX_PATTERN_LEN
        viols = lint_pattern(pattern)
        assert not any(v.code == "REGEX_TOO_LONG" for v in viols)

    def test_pattern_over_limit_flagged(self):
        """One char over the cap must emit REGEX_TOO_LONG."""
        pattern = "a" * (regex_lint.MAX_PATTERN_LEN + 1)
        viols = lint_pattern(pattern, field="condition_value")
        assert len(viols) == 1
        v = viols[0]
        assert v.code == "REGEX_TOO_LONG"
        assert v.field == "condition_value"
        assert v.detail["pattern_len"] == regex_lint.MAX_PATTERN_LEN + 1
        assert v.detail["max_pattern_len"] == regex_lint.MAX_PATTERN_LEN

    def test_message_is_actionable(self):
        """The message must include the concrete length, the cap, and a
        rewrite hint (per grooming decision — not 'invalid pattern')."""
        pattern = "a" * 612
        v = lint_pattern(pattern)[0]
        assert "612" in v.message
        assert "500" in v.message
        assert "multiple rules" in v.message

    def test_too_long_short_circuits_other_checks(self):
        """Oversize patterns should not also emit COMPILE / NESTED codes."""
        pattern = "a" * (regex_lint.MAX_PATTERN_LEN + 100)
        viols = lint_pattern(pattern)
        codes = {v.code for v in viols}
        assert codes == {"REGEX_TOO_LONG"}


class TestRegexCompileError:
    def test_unbalanced_parens_flagged(self):
        viols = lint_pattern(r"^(unclosed", field="condition_value")
        assert len(viols) == 1
        v = viols[0]
        assert v.code == "REGEX_COMPILE_ERROR"
        assert v.field == "condition_value"
        assert "compile_error" in v.detail

    def test_invalid_quantifier_flagged(self):
        viols = lint_pattern(r"*invalid")
        assert any(v.code == "REGEX_COMPILE_ERROR" for v in viols)

    def test_compile_error_message_user_facing(self):
        """The message must not leak the internal sha256 framing."""
        v = lint_pattern(r"^(")[0]
        assert v.code == "REGEX_COMPILE_ERROR"
        assert "sha256=" not in v.message
        # Linter error messages must link back to the style-guide Regex
        # section so the 422 response is actionable (bd-eio04.8).
        assert "docs/style_guide.md#regex" in v.message

    def test_compile_error_short_circuits_ast_walk(self):
        """A pattern that can't compile MUST NOT also be AST-walked —
        the walker would fail with the same syntax error and we'd
        double-report."""
        viols = lint_pattern(r"^(unclosed")
        codes = {v.code for v in viols}
        assert codes == {"REGEX_COMPILE_ERROR"}


class TestDocsURL:
    """DOCS_URL must point at the live style-guide Regex section.

    bd-eio04.8 replaced the bd-eio04.7 placeholder (``/docs/patterns``) with
    an anchored link to ``docs/style_guide.md#regex``. These assertions lock
    the value so any future rename surfaces as a test failure — the URL is
    also baked into end-user-facing 422 error messages.
    """

    def test_docs_url_points_to_style_guide_regex_anchor(self):
        assert regex_lint.DOCS_URL == "docs/style_guide.md#regex"

    def test_docs_url_is_not_the_bd_eio04_7_placeholder(self):
        # Guards against a stray revert — the original placeholder was
        # '/docs/patterns' and is dead as of bd-eio04.8.
        assert regex_lint.DOCS_URL != "/docs/patterns"

    def test_all_lint_messages_embed_docs_url(self):
        """Every user-facing violation message must link to the guide."""
        # One pattern per code path.
        too_long = "a" * (regex_lint.MAX_PATTERN_LEN + 1)
        bad_compile = "^("
        nested = "(a+)+b"
        for pattern in (too_long, bad_compile, nested):
            viols = lint_pattern(pattern)
            assert viols, f"expected violations for {pattern!r}"
            for v in viols:
                assert regex_lint.DOCS_URL in v.message, (
                    f"violation code={v.code} message missing DOCS_URL: "
                    f"{v.message!r}"
                )


class TestRegexNestedQuantifier:
    def test_classic_aplus_plus_flagged(self):
        viols = lint_pattern(r"(a+)+b", field="condition_value")
        assert len(viols) == 1
        assert viols[0].code == "REGEX_NESTED_QUANTIFIER"
        assert viols[0].field == "condition_value"
        assert viols[0].detail.get("reason") == "nested-unbounded-repeat-with-killer"

    def test_message_mentions_rewrite_hint(self):
        viols = lint_pattern(r"(a+)+b")
        assert "nested" in viols[0].message.lower() or "+" in viols[0].message

    def test_compiles_cleanly_so_no_compile_error(self):
        """Nested-quantifier patterns still compile — the linter must
        emit ONLY the nested code, not compile + nested."""
        viols = lint_pattern(r"(a+)+b")
        codes = {v.code for v in viols}
        assert codes == {"REGEX_NESTED_QUANTIFIER"}


# =========================================================================
# Empty / edge cases.
# =========================================================================


class TestEdgeCases:
    def test_none_returns_empty(self):
        """``None`` is not an oversight — it means the user didn't
        supply a pattern for this field. Empty list = no findings."""
        assert lint_pattern(None) == []

    def test_empty_string_returns_empty(self):
        assert lint_pattern("") == []

    def test_whitespace_only_returns_empty(self):
        assert lint_pattern("   \n\t ") == []

    def test_non_string_flagged_as_compile_error(self):
        """Defensive: caller passes a non-string by mistake. Don't
        crash the request — flag it so the UI can show something."""
        viols = lint_pattern(123)  # type: ignore[arg-type]
        assert len(viols) == 1
        assert viols[0].code == "REGEX_COMPILE_ERROR"

    def test_js_style_named_group_accepted(self):
        """``(?<name>...)`` is the JS syntax the dummy-EPG engine
        converts at runtime. Linter accepts both styles."""
        viols = lint_pattern(r"(?<hour>\d+)(?<ampm>pm)")
        assert viols == []

    def test_lookbehind_not_confused_for_named_group(self):
        """``(?<=...)`` and ``(?<!...)`` are lookbehinds, not named
        groups. The conversion helper must leave them alone."""
        viols = lint_pattern(r"(?<=foo)\d+")
        assert viols == []


# =========================================================================
# Bulk helpers.
# =========================================================================


class TestBulkHelpers:
    def test_lint_pattern_fields_multiple(self):
        """Multiple fields — each violation keeps its own ``field`` path."""
        viols = lint_pattern_fields([
            ("title_pattern", "(a+)+b"),
            ("time_pattern", None),  # no-op
            ("date_pattern", "a" * 600),
        ])
        assert len(viols) == 2
        by_field = {v.field: v.code for v in viols}
        assert by_field["title_pattern"] == "REGEX_NESTED_QUANTIFIER"
        assert by_field["date_pattern"] == "REGEX_TOO_LONG"

    def test_lint_conditions_json_flat(self):
        """Regex-flavored condition types are linted; others skipped."""
        viols = lint_conditions_json([
            {"type": "stream_name_matches", "value": "(a+)+b"},
            {"type": "stream_name_contains", "value": "(a+)+b"},  # non-regex
            {"type": "quality_min", "value": 1080},
        ])
        assert len(viols) == 1
        assert viols[0].field == "conditions[0].value"
        assert viols[0].code == "REGEX_NESTED_QUANTIFIER"

    def test_lint_conditions_json_recurses_into_logical_operators(self):
        """AND/OR/NOT compound conditions must be walked recursively."""
        viols = lint_conditions_json([
            {
                "type": "and",
                "conditions": [
                    {"type": "stream_name_matches", "value": "ok"},
                    {"type": "stream_group_matches", "value": "(a+)+b"},
                ],
            }
        ])
        assert len(viols) == 1
        assert "conditions[0].conditions[1].value" == viols[0].field

    def test_lint_actions_json_set_variable(self):
        viols = lint_actions_json([
            {
                "type": "set_variable",
                "variable_name": "foo",
                "variable_mode": "regex_extract",
                "source_field": "stream_name",
                "pattern": "(a+)+b",
            }
        ])
        assert len(viols) == 1
        assert viols[0].field == "actions[0].pattern"

    def test_lint_actions_json_name_transform(self):
        viols = lint_actions_json([
            {
                "type": "create_channel",
                "name_template": "{stream_name}",
                "name_transform_pattern": "(a+)+b",
                "name_transform_replacement": "",
            }
        ])
        assert len(viols) == 1
        assert viols[0].field == "actions[0].name_transform_pattern"

    def test_lint_actions_json_skips_literal_set_variable(self):
        """``literal`` mode has no regex — must not be linted."""
        viols = lint_actions_json([
            {
                "type": "set_variable",
                "variable_name": "foo",
                "variable_mode": "literal",
                "template": "{stream_name}",
            }
        ])
        assert viols == []

    def test_lint_substitution_pairs_only_is_regex(self):
        """Non-regex pairs are literal strings — must not be linted."""
        viols = lint_substitution_pairs([
            {"find": "(a+)+b", "replace": "", "is_regex": False, "enabled": True},
            {"find": "(a+)+b", "replace": "", "is_regex": True, "enabled": True},
        ])
        assert len(viols) == 1
        assert viols[0].field == "substitution_pairs[1].find"


# =========================================================================
# HTTP envelope.
# =========================================================================


class TestHttpEnvelope:
    def test_envelope_shape(self):
        v = LintViolation(
            code="REGEX_TOO_LONG",
            message="Pattern is too long (612 chars, max 500).",
            field="condition_value",
            detail={"pattern_len": 612, "max_pattern_len": 500},
        )
        envelope = violations_to_http_detail([v])
        assert envelope == {
            "error": {
                "code": "REGEX_VALIDATION_ERROR",
                "message": "Pattern is too long (612 chars, max 500).",
                "details": [
                    {
                        "field": "condition_value",
                        "code": "REGEX_TOO_LONG",
                        "message": "Pattern is too long (612 chars, max 500).",
                        "detail": {"pattern_len": 612, "max_pattern_len": 500},
                    }
                ],
            }
        }

    def test_envelope_with_multiple_violations_keeps_first_message(self):
        a = LintViolation(code="REGEX_TOO_LONG", message="first", field="a")
        b = LintViolation(code="REGEX_NESTED_QUANTIFIER", message="second", field="b")
        envelope = violations_to_http_detail([a, b])
        assert envelope["error"]["message"] == "first"
        assert len(envelope["error"]["details"]) == 2


# =========================================================================
# Property-based smoke test (optional — skipped if hypothesis isn't
# installed. The full pathologically-generated sweep is in
# test_regex_lint_property.py.)
# =========================================================================


class TestBenignSmokeSweep:
    """A hand-curated sweep of known-benign shapes.

    Runs without hypothesis so it's always executed. Property-based
    generation of 'should pass' patterns is in a sibling file that skips
    when hypothesis isn't available."""

    BENIGN: list[str] = [
        r"foo",
        r"foo.*bar",
        r"\d{3}-\d{4}",
        r"^ESPN\s+\d+",
        r"[a-zA-Z]{2,10}",
        r"^(?:US|CA|UK):\s*",
        r"Season\s+\d+\s+Episode\s+\d+",
        r"(HD|SD|UHD|4K)",
        r"\b[A-Z]{3,5}\b",
    ]

    @pytest.mark.parametrize("pattern", BENIGN)
    def test_benign_passes(self, pattern):
        viols = lint_pattern(pattern)
        assert viols == [], (
            f"benign sweep FP: {pattern!r} — {[v.code for v in viols]}"
        )
