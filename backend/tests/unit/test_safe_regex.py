"""
Unit tests for :mod:`safe_regex` — ReDoS-guarded regex wrapper.

Tests are written BEFORE implementation per TDD. Each test covers one
contract expectation from bd-eio04.5.
"""

import hashlib
import logging
import time

import pytest
import regex as _re

import safe_regex


# =========================================================================
# Happy-path behavior
# =========================================================================


class TestHappyPath:
    def test_happy_path_search_returns_match(self):
        """search() returns a Match object with capture groups for a normal pattern."""
        m = safe_regex.search(r"(\w+)@(\w+\.\w+)", "hello user@example.com tail")
        assert m is not None
        assert m.group(1) == "user"
        assert m.group(2) == "example.com"

    def test_happy_path_match_returns_match(self):
        """match() anchors at start and returns a Match on success."""
        m = safe_regex.match(r"foo", "foo bar")
        assert m is not None
        # match() must NOT match when prefix does not align.
        assert safe_regex.match(r"bar", "foo bar") is None

    def test_happy_path_sub_returns_substituted(self):
        """sub() performs the substitution and returns the replaced string."""
        result = safe_regex.sub(r"foo", "bar", "foo baz")
        assert result == "bar baz"


# =========================================================================
# Timeout (ReDoS) behavior — best-effort, per regex library semantics.
# =========================================================================


class TestTimeoutBehavior:
    # Adversarial fixture required by bead grooming: the classic
    # "(a+)+b" against "a"*30 + "!". The 'regex' library detects the
    # redundant nesting and short-circuits this particular fixture to
    # None in sub-millisecond time, so the timeout never actually fires.
    # This test still validates the contract (no match, no exception,
    # well under the 500ms CI budget). See the genuine-ReDoS fixture
    # below for empirical verification that the timeout plumbing works.
    ADVERSARIAL_PATTERN = r"(a+)+b"
    ADVERSARIAL_TEXT = "a" * 30 + "!"
    WALL_CLOCK_BUDGET_MS = 500  # 5x the 100ms timeout_ms default.

    # A pattern the 'regex' library cannot optimize away — genuine
    # catastrophic backtracking. Used to empirically prove the timeout
    # plumbing actually returns the sentinel rather than blocking.
    REAL_REDOS_PATTERN = r"(a|aa)+b"
    REAL_REDOS_TEXT = "a" * 30 + "!"

    def test_timeout_returns_none_for_search(self):
        """Adversarial search returns None well within the wall-clock budget, 5x in a row."""
        for iteration in range(5):
            start = time.monotonic()
            result = safe_regex.search(self.ADVERSARIAL_PATTERN, self.ADVERSARIAL_TEXT)
            elapsed_ms = (time.monotonic() - start) * 1000
            assert result is None, f"iter={iteration} expected None, got {result!r}"
            assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS, (
                f"iter={iteration}: elapsed {elapsed_ms:.1f}ms exceeded "
                f"budget {self.WALL_CLOCK_BUDGET_MS}ms"
            )

    def test_timeout_sub_returns_original(self):
        """Adversarial sub returns the input text unchanged (sentinel)."""
        text = self.ADVERSARIAL_TEXT
        start = time.monotonic()
        result = safe_regex.sub(self.ADVERSARIAL_PATTERN, "REPLACED", text)
        elapsed_ms = (time.monotonic() - start) * 1000
        # For the fixture the library optimizes, result is text with no
        # change (no match found). For a genuine catastrophic pattern
        # that times out, sentinel is also the original text.
        assert result == text
        assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS

    def test_genuine_redos_search_returns_none(self):
        """
        Pattern the 'regex' library cannot optimize — genuinely triggers
        the timeout. Verifies the timeout plumbing empirically (vs the
        library's short-circuit). Returns None (sentinel) within budget.
        """
        start = time.monotonic()
        result = safe_regex.search(self.REAL_REDOS_PATTERN, self.REAL_REDOS_TEXT)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert result is None
        # Genuine timeout costs the full 100ms budget plus some overhead;
        # CI jitter cap is 500ms.
        assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS, (
            f"genuine-ReDoS elapsed {elapsed_ms:.1f}ms exceeded "
            f"budget {self.WALL_CLOCK_BUDGET_MS}ms"
        )

    def test_genuine_redos_sub_returns_original(self):
        """Genuine ReDoS sub returns input text unchanged (sentinel)."""
        text = self.REAL_REDOS_TEXT
        start = time.monotonic()
        result = safe_regex.sub(self.REAL_REDOS_PATTERN, "REPLACED", text)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert result == text
        assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS


# =========================================================================
# Pattern-length cap.
# =========================================================================


class TestOversizePattern:
    def test_oversize_pattern_search_returns_none(self, caplog):
        """Pattern > max_pattern_len: search returns None, logs [SAFE_REGEX] WARN."""
        oversize = "a" * 501
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = safe_regex.search(oversize, "aaa")
        assert result is None
        assert any(
            "[SAFE_REGEX]" in rec.getMessage() and rec.levelno == logging.WARNING
            for rec in caplog.records
        )

    def test_oversize_pattern_match_returns_none(self, caplog):
        """Pattern > max_pattern_len: match returns None, logs warning."""
        oversize = "a" * 501
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = safe_regex.match(oversize, "aaa")
        assert result is None
        assert any("[SAFE_REGEX]" in rec.getMessage() for rec in caplog.records)

    def test_oversize_pattern_sub_returns_original(self, caplog):
        """Pattern > max_pattern_len: sub returns input unchanged, logs warning."""
        oversize = "a" * 501
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = safe_regex.sub(oversize, "X", "aaa bbb")
        assert result == "aaa bbb"
        assert any("[SAFE_REGEX]" in rec.getMessage() for rec in caplog.records)

    def test_oversize_pattern_compile_raises(self):
        """Pattern > max_pattern_len: compile raises PatternTooLongError."""
        oversize = "a" * 501
        with pytest.raises(safe_regex.PatternTooLongError):
            safe_regex.compile(oversize)


# =========================================================================
# Exception hierarchy and compilation errors.
# =========================================================================


class TestExceptionHierarchy:
    def test_pattern_too_long_error_is_safe_regex_error(self):
        """PatternTooLongError inherits from SafeRegexError."""
        assert issubclass(safe_regex.PatternTooLongError, safe_regex.SafeRegexError)

    def test_regex_timeout_error_is_safe_regex_error(self):
        """RegexTimeoutError inherits from SafeRegexError."""
        assert issubclass(safe_regex.RegexTimeoutError, safe_regex.SafeRegexError)

    def test_invalid_pattern_compile_raises(self):
        """compile() with a malformed pattern raises a SafeRegexError subclass, NOT re.error."""
        with pytest.raises(safe_regex.SafeRegexError):
            # Unclosed group — guaranteed compile error in both re and regex.
            safe_regex.compile(r"(unclosed")


# =========================================================================
# Compiled patterns, flags, and sentinel consistency.
# =========================================================================


class TestCompiledPatternReuse:
    def test_compiled_pattern_reusable(self):
        """compile() once, use many times — each call returns an independent Match."""
        pat = safe_regex.compile(r"\d+")
        m1 = pat.search("abc 123")
        m2 = pat.search("zzz 987 yyy")
        assert m1 is not None and m1.group(0) == "123"
        assert m2 is not None and m2.group(0) == "987"


class TestFlagPropagation:
    def test_flags_propagate_to_search(self):
        """flags kwarg flows through to the underlying engine."""
        m = safe_regex.search(r"foo", "FOO", flags=_re.IGNORECASE)
        assert m is not None
        assert m.group(0) == "FOO"

    def test_flags_propagate_to_sub(self):
        """flags kwarg flows through sub() as well."""
        result = safe_regex.sub(r"foo", "bar", "FOO baz", flags=_re.IGNORECASE)
        assert result == "bar baz"


# =========================================================================
# Security-sensitive log formatting: sha256 + excerpt, NEVER full pattern.
# =========================================================================


class TestLogFormat:
    # Sentinels positioned BEYOND the 50-char excerpt window so leakage
    # is detectable: if the sentinel appears in the log, the module
    # logged more than the documented 50-char excerpt.
    _TAIL_SENTINEL_TIMEOUT = "PATTERN_TAIL_SENTINEL_MUST_NOT_LEAK_T7Q"
    _TAIL_SENTINEL_OVERSIZE = "PATTERN_TAIL_SENTINEL_MUST_NOT_LEAK_OVR9Z"

    def test_log_format_on_timeout_excludes_full_pattern(self, caplog):
        """
        On timeout, WARN log contains [SAFE_REGEX] + pattern_sha256 and does
        NOT contain the tail of the pattern (security requirement — patterns
        may include attacker-controlled text, so only a 50-char excerpt is
        logged).
        """
        # Build a pattern where the sentinel sits past char 50 but the
        # catastrophic construct at the tail still triggers a genuine
        # timeout. Structure: <55-char literal prefix><sentinel><(a|aa)+b>
        # The text is crafted to match the literal prefix and sentinel
        # but then force catastrophic backtracking on the tail.
        literal_prefix = "z" * 55  # pushes sentinel past char 50
        pat = literal_prefix + self._TAIL_SENTINEL_TIMEOUT + r"(a|aa)+b"
        assert pat.index(self._TAIL_SENTINEL_TIMEOUT) > 50  # precondition
        text = literal_prefix + self._TAIL_SENTINEL_TIMEOUT + "a" * 30 + "!"
        expected_sha = hashlib.sha256(pat.encode("utf-8")).hexdigest()

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = safe_regex.search(pat, text)
        assert result is None

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warn_records, "expected at least one WARNING log record"

        combined = " ".join(r.getMessage() for r in warn_records)
        assert "[SAFE_REGEX]" in combined
        assert expected_sha in combined
        # The tail sentinel must NOT appear — if it does, the module logged
        # more than the documented 50-char excerpt.
        assert self._TAIL_SENTINEL_TIMEOUT not in combined, (
            "pattern tail leaked into log output beyond the documented 50-char excerpt"
        )

    def test_log_format_on_oversize_excludes_full_pattern(self, caplog):
        """
        On oversize, WARN log contains pattern_sha256 and the logged
        excerpt does not include the tail of the oversized pattern.
        """
        # Build a 501-char pattern with the sentinel well past position 50.
        prefix_padding = "a" * 200
        suffix_padding = "a" * (501 - len(prefix_padding) - len(self._TAIL_SENTINEL_OVERSIZE))
        pat = prefix_padding + self._TAIL_SENTINEL_OVERSIZE + suffix_padding
        assert len(pat) == 501
        expected_sha = hashlib.sha256(pat.encode("utf-8")).hexdigest()

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = safe_regex.search(pat, "aaa")
        assert result is None

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warn_records
        combined = " ".join(r.getMessage() for r in warn_records)
        assert "[SAFE_REGEX]" in combined
        assert expected_sha in combined
        assert self._TAIL_SENTINEL_OVERSIZE not in combined, (
            "oversize pattern tail leaked into log output beyond the 50-char excerpt"
        )


# =========================================================================
# sub() failure reporting — on_error callback (enhancedchannelmanager-3gigl).
# =========================================================================


class TestSubOnErrorReporting:
    """``sub()`` must be able to REPORT why it returned the input unchanged.

    Default contract is untouched (swallow-and-return-input). The optional
    ``on_error`` callback receives one :class:`safe_regex.SubFailure` per
    failure so callers (the channel-pipeline executor) can surface the
    reason in user-visible execution logs instead of a hash-labeled WARN.
    """

    # Same genuine-ReDoS fixture as TestTimeoutBehavior.
    REAL_REDOS_PATTERN = r"(a|aa)+b"
    REAL_REDOS_TEXT = "a" * 30 + "!"

    def test_template_error_invokes_on_error_with_template_kind(self):
        failures = []
        result = safe_regex.sub(
            r"(a)(b)(c)", r"\2 \1 \3 \4", "abc", on_error=failures.append
        )
        assert result == "abc"  # swallow contract unchanged
        assert len(failures) == 1
        failure = failures[0]
        assert isinstance(failure, safe_regex.SubFailure)
        assert failure.kind == "template"
        assert "invalid group reference" in failure.message

    def test_template_error_is_lazy_no_error_without_match(self):
        """regex parses the template lazily — no match, no failure."""
        failures = []
        result = safe_regex.sub(
            r"(a)(b)(c)", r"\4", "zzz", on_error=failures.append
        )
        assert result == "zzz"
        assert failures == []

    def test_pattern_error_invokes_on_error_with_pattern_kind(self):
        failures = []
        result = safe_regex.sub("[invalid(", "x", "abc", on_error=failures.append)
        assert result == "abc"
        assert len(failures) == 1
        assert failures[0].kind == "pattern"

    def test_timeout_invokes_on_error_with_timeout_kind(self):
        failures = []
        result = safe_regex.sub(
            self.REAL_REDOS_PATTERN, "REPLACED", self.REAL_REDOS_TEXT,
            on_error=failures.append,
        )
        assert result == self.REAL_REDOS_TEXT
        assert len(failures) == 1
        assert failures[0].kind == "timeout"
        assert "timed out" in failures[0].message

    def test_oversize_invokes_on_error_with_oversize_kind(self):
        failures = []
        oversize = "a" * 501
        result = safe_regex.sub(oversize, "x", "aaa", on_error=failures.append)
        assert result == "aaa"
        assert len(failures) == 1
        assert failures[0].kind == "oversize"

    def test_success_does_not_invoke_on_error(self):
        failures = []
        result = safe_regex.sub(r"(a)", r"\1!", "a", on_error=failures.append)
        assert result == "a!"
        assert failures == []

    def test_default_contract_unchanged_without_on_error(self):
        """No callback: same swallow behavior as before (regression lock)."""
        assert safe_regex.sub(r"(a)(b)(c)", r"\4", "abc") == "abc"

    def test_precompiled_pattern_template_error_reports_template_kind(self):
        failures = []
        compiled = safe_regex.compile(r"(a)")
        result = safe_regex.sub(compiled, r"\2", "a", on_error=failures.append)
        assert result == "a"
        assert failures[0].kind == "template"


class TestSubErrorLogLabels:
    """The WARN label must distinguish replacement-template errors from
    pattern compile errors (3gigl: the old label said 'compile error at
    sub' even when the pattern compiled fine and the TEMPLATE was bad)."""

    def test_template_error_logged_as_template_error(self, caplog):
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            safe_regex.sub(r"(a)(b)(c)", r"\4", "abc")
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "replacement template error at sub" in combined
        assert "compile error at sub" not in combined

    def test_pattern_error_still_logged_as_compile_error(self, caplog):
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            safe_regex.sub("[invalid(", "x", "abc")
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "compile error at sub" in combined


class TestSubMetadataOnlyDiagnostics:
    """Sensitive callers can retain failure categories without value excerpts."""

    @pytest.mark.parametrize(
        ("pattern", "repl", "text", "expected_category", "fragments"),
        [
            ("sensitive-invalid-fragment(", "x", "abc", "compile error", ("sensitive-invalid",)),
            ("sensitive-oversize-fragment" * 30, "x", "abc", "oversize", ("sensitive-oversize",)),
            (r"sensitive-timeout-(a|aa)+b", "x", "sensitive-timeout-" + "a" * 30 + "!", "timed out", ("sensitive-timeout", "(a|aa)")),
            (r"(sensitive-template)", r"private-template-fragment-\2", "sensitive-template", "replacement template error", ("sensitive-template", "private-template")),
        ],
    )
    def test_metadata_only_mode_never_logs_pattern_or_replacement_fragments(
        self, caplog, pattern, repl, text, expected_category, fragments
    ):
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            assert safe_regex.sub(
                pattern,
                repl,
                text,
                diagnostic_mode="metadata_only",
            ) == text

        assert expected_category in caplog.text
        assert "pattern_sha256=" in caplog.text
        assert "pattern_excerpt=" not in caplog.text
        assert "repl_excerpt=" not in caplog.text
        for fragment in fragments:
            assert fragment not in caplog.text

    def test_default_diagnostic_mode_keeps_existing_excerpt_contract(self, caplog):
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            safe_regex.sub("visible-default-fragment(", "x", "abc")

        assert "pattern_excerpt='visible-default-fragment('" in caplog.text
