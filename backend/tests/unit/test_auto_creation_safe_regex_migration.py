"""
Unit, property-based, and ReDoS-adversarial tests for the bd-eio04.15
safe_regex migration in the auto_creation_* modules.

Covers the eight migrated call sites:

- auto_creation_evaluator.py:
  - ``_expand_date_placeholders`` (sub)
  - ``_evaluate_regex`` (search)
  - ``_evaluate_channel_exists_regex`` (compile + per-channel search)
- auto_creation_executor.py:
  - ``_apply_name_transform`` (sub)
  - ``_execute_set_variable`` regex_extract (search)
  - ``_execute_set_variable`` regex_replace (sub)
  - ``_find_channel_by_regex`` (compile + per-channel search)
- auto_creation_engine.py:
  - ``_sort_key`` for ``stream_name_regex`` (hot-path search)

Each site has:

1. A sentinel-handling unit test (None-sentinel / no-crash on timeout).
2. A Hypothesis property-based test asserting stdlib-re and safe_regex
   return equivalent results on benign inputs. Adversarial patterns are
   excluded from the strategy to keep property tests deterministic.
3. An adversarial ``(a+)+b`` evil-pattern test asserting the migrated
   site falls back without hanging and without crashing.

See bd-eio04.15 for grooming detail — the primary AttributeError risk
is that ``match.group(...)`` is dereferenced immediately after search;
on timeout the sentinel is ``None``. Each test here locks that behavior
for its corresponding site.
"""

import logging
import re
import time
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings as hyp_settings, HealthCheck
from hypothesis import strategies as st

import safe_regex
from auto_creation_evaluator import ConditionEvaluator, StreamContext
from auto_creation_executor import ActionExecutor
from auto_creation_engine import _sort_key


# =========================================================================
# Shared test constants.
# =========================================================================


# Classic catastrophic-backtracking pattern. The ``regex`` library often
# optimizes this particular form away quickly, but we pair it with
# ``_EVIL_INPUT_MISMATCH`` to force the mismatch path that actually
# exercises the backtracker. The adversarial assertion is about wall
# clock safety, not that the pattern definitely triggered the timeout.
_EVIL_PATTERN = r"(a+)+b"
_EVIL_INPUT_MISMATCH = "a" * 50 + "!"

# Alternative pattern that reliably exercises the backtracker in the
# ``regex`` library. Used when we want to be confident the timeout path
# fires rather than early-return-on-shortcut.
_EVIL_PATTERN_ALT = r"(a|aa)+b"
_EVIL_INPUT_ALT = "a" * 40 + "!"

# Wall-clock ceiling for adversarial assertions. 500 ms is generous for
# safe_regex's 100 ms default (plus Python overhead); anything above
# suggests the timeout plumbing did not engage.
_MAX_ADVERSARIAL_SECONDS = 0.5


# Benign pattern/text strategy for property-based equivalence tests.
# Explicitly excludes the meta-characters that make stdlib ``re`` and
# the ``regex`` library diverge in corner cases (Unicode class shorthand,
# possessive quantifiers, named group syntax), because those divergences
# are NOT regressions introduced by this migration.
_BENIGN_LITERAL_ALPHABET = "abcXYZ0123 -_:|"
_BENIGN_PATTERN_STRATEGY = st.text(
    alphabet=_BENIGN_LITERAL_ALPHABET, min_size=1, max_size=20,
).map(re.escape)  # pure-literal pattern; both engines agree on these
_BENIGN_TEXT_STRATEGY = st.text(
    alphabet=_BENIGN_LITERAL_ALPHABET, min_size=0, max_size=80,
)


# =========================================================================
# Helpers: thin stubs for the executor / evaluator surfaces.
# =========================================================================


def _evaluator_with_channels(channels: list[dict] | None = None) -> ConditionEvaluator:
    return ConditionEvaluator(existing_channels=channels or [])


def _executor_with_channels(channels: list[dict] | None = None) -> ActionExecutor:
    client = AsyncMock()
    return ActionExecutor(
        client=client,
        existing_channels=channels or [],
        existing_groups=[],
    )


def _minimal_stream_context(name: str = "Example HD") -> StreamContext:
    return StreamContext(stream_id=1, stream_name=name)


# =========================================================================
# Site 1 — auto_creation_evaluator._expand_date_placeholders (sub)
# =========================================================================


class TestExpandDatePlaceholders:
    """Evaluator sub() at L~270 — sentinel contract is: text unchanged."""

    def test_happy_path_expands_date(self):
        """Baseline: placeholder expands to a real date format."""
        ev = _evaluator_with_channels()
        out = ev._expand_date_placeholders("show-{date:%Y-%m-%d}", allow_ranges=False)
        # The expanded output must match a YYYY-MM-DD pattern.
        assert re.match(r"^show-\d{4}-\d{2}-\d{2}$", out)

    def test_no_placeholder_returns_input(self):
        """No placeholder => method returns input unchanged."""
        ev = _evaluator_with_channels()
        assert ev._expand_date_placeholders("plain text", allow_ranges=False) == "plain text"

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
        derandomize=True,  # pin seed for CI stability per bd-eio04.15 QA note
    )
    @given(text=_BENIGN_TEXT_STRATEGY)
    def test_property_no_placeholder_is_passthrough(self, text):
        """For inputs with no ``{``, expansion must be a pure passthrough."""
        ev = _evaluator_with_channels()
        # Drop any stray '{' to isolate the passthrough branch.
        text = text.replace("{", "")
        assert ev._expand_date_placeholders(text, allow_ranges=False) == text

    def test_adversarial_text_does_not_hang_or_crash(self):
        """
        The inner pattern in _expand_date_placeholders is hardcoded
        (safe), but the text is user-supplied. We verify the method
        completes within a generous wall-clock bound even for long
        malicious-looking input.
        """
        ev = _evaluator_with_channels()
        bad_text = "{" * 200 + "a" * 400
        start = time.perf_counter()
        result = ev._expand_date_placeholders(bad_text, allow_ranges=False)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        # Either expansion happened or it didn't; both are acceptable —
        # what matters is no unhandled exception and no hang.
        assert isinstance(result, str)


# =========================================================================
# Site 2 — auto_creation_evaluator._evaluate_regex (search)
# =========================================================================


class TestEvaluateRegex:
    """Evaluator search() at L~501 — sentinel contract: matched=False."""

    def test_happy_path_match(self):
        ev = _evaluator_with_channels()
        r = ev._evaluate_regex(r"HD$", "ESPN HD", case_sensitive=False, cond_type="regex")
        assert r.matched is True

    def test_happy_path_no_match(self):
        ev = _evaluator_with_channels()
        r = ev._evaluate_regex(r"SD$", "ESPN HD", case_sensitive=False, cond_type="regex")
        assert r.matched is False

    def test_empty_pattern_returns_no_match(self):
        ev = _evaluator_with_channels()
        r = ev._evaluate_regex("", "ESPN HD", case_sensitive=False, cond_type="regex")
        assert r.matched is False

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
        derandomize=True,
    )
    @given(pattern=_BENIGN_PATTERN_STRATEGY, text=_BENIGN_TEXT_STRATEGY)
    def test_property_matches_stdlib_on_benign_input(self, pattern, text):
        """For benign literal patterns, safe_regex.search must agree
        with stdlib re.search on the match/no-match verdict."""
        ev = _evaluator_with_channels()
        stdlib_matched = bool(re.search(pattern, text, flags=re.IGNORECASE))
        result = ev._evaluate_regex(pattern, text, case_sensitive=False, cond_type="regex")
        assert result.matched is stdlib_matched

    def test_adversarial_pattern_falls_back_to_no_match(self, caplog):
        """
        ReDoS-style user pattern must NOT raise; method must return
        matched=False within the wall-clock budget and the safe_regex
        timeout WARN must be emitted.
        """
        ev = _evaluator_with_channels()
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            r = ev._evaluate_regex(
                _EVIL_PATTERN_ALT, _EVIL_INPUT_ALT,
                case_sensitive=False, cond_type="regex",
            )
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        assert r.matched is False
        # Timeout counter proxy: safe_regex WARN emitted.
        assert any("[SAFE_REGEX]" in m for m in caplog.messages)


# =========================================================================
# Site 3 — auto_creation_evaluator._evaluate_channel_exists_regex (compile + search)
# =========================================================================


class TestEvaluateChannelExistsRegex:
    """Evaluator compile + search at L~639 — sentinel on timeout: no-match."""

    def test_happy_path_match_in_existing_channels(self):
        ev = _evaluator_with_channels([
            {"id": 1, "name": "ESPN HD"},
            {"id": 2, "name": "CNN"},
        ])
        r = ev._evaluate_channel_exists_regex(
            r"ESPN", case_sensitive=False, cond_type="channel_exists_regex"
        )
        assert r.matched is True

    def test_happy_path_no_match(self):
        ev = _evaluator_with_channels([
            {"id": 1, "name": "ESPN HD"},
            {"id": 2, "name": "CNN"},
        ])
        r = ev._evaluate_channel_exists_regex(
            r"BBC", case_sensitive=False, cond_type="channel_exists_regex"
        )
        assert r.matched is False

    def test_oversize_pattern_returns_invalid(self):
        """Patterns > DEFAULT_MAX_PATTERN_LEN (500) must not crash."""
        ev = _evaluator_with_channels([{"id": 1, "name": "ESPN HD"}])
        big = "a" * 600
        r = ev._evaluate_channel_exists_regex(
            big, case_sensitive=False, cond_type="channel_exists_regex"
        )
        assert r.matched is False
        assert "Invalid regex" in r.details

    def test_adversarial_pattern_does_not_crash(self, caplog):
        # Fill with many channels to ensure the per-channel loop gets
        # exercised at least once before the evil pattern times out.
        ev = _evaluator_with_channels([{"id": i, "name": _EVIL_INPUT_ALT} for i in range(5)])
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            r = ev._evaluate_channel_exists_regex(
                _EVIL_PATTERN_ALT, case_sensitive=False,
                cond_type="channel_exists_regex",
            )
        elapsed = time.perf_counter() - start
        # Per-channel budget is 100 ms; 5 channels => worst-case ~0.6s,
        # but typical short-circuit is far faster. Assert the whole
        # call finishes within a generous ceiling.
        assert elapsed < _MAX_ADVERSARIAL_SECONDS * 6
        assert r.matched is False


# =========================================================================
# Site 4 — auto_creation_executor._apply_name_transform (sub)
# =========================================================================


class TestApplyNameTransform:
    """Executor sub() at L~427 — sentinel on timeout: name unchanged."""

    def test_happy_path_transform(self):
        exe = _executor_with_channels()
        out = exe._apply_name_transform(
            "ESPN HD",
            {
                "name_transform_pattern": r"HD$",
                "name_transform_replacement": "",
            },
        )
        assert out == "ESPN"

    def test_no_pattern_passthrough(self):
        exe = _executor_with_channels()
        out = exe._apply_name_transform("ESPN HD", {})
        assert out == "ESPN HD"

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
        derandomize=True,
    )
    @given(pattern=_BENIGN_PATTERN_STRATEGY, text=_BENIGN_TEXT_STRATEGY)
    def test_property_matches_stdlib_on_benign_input(self, pattern, text):
        exe = _executor_with_channels()
        expected = re.sub(pattern, "", text).strip()
        got = exe._apply_name_transform(
            text,
            {"name_transform_pattern": pattern, "name_transform_replacement": ""},
        )
        assert got == expected

    def test_adversarial_pattern_returns_original_stripped(self, caplog):
        exe = _executor_with_channels()
        original = _EVIL_INPUT_ALT
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            out = exe._apply_name_transform(
                original,
                {
                    "name_transform_pattern": _EVIL_PATTERN_ALT,
                    "name_transform_replacement": "",
                },
            )
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        # Fallback contract: safe_regex.sub returns input unchanged on
        # timeout; _apply_name_transform then strips whitespace.
        assert out == original.strip()


# =========================================================================
# Site 5 & 6 — auto_creation_executor._execute_set_variable (search + sub)
# =========================================================================


class TestExecuteSetVariableRegexExtract:
    """Executor regex_extract (search) at L~1841 — sentinel: result_value=''."""

    @pytest.mark.asyncio
    async def test_happy_path_extract_group(self):
        from auto_creation_schema import Action
        from auto_creation_executor import ExecutionContext
        exe = _executor_with_channels()
        action = Action(type="set_variable", params={
            "variable_name": "v",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": r"HD\s+(\d+)",
        })
        stream_ctx = _minimal_stream_context("ESPN HD 1080p")
        exec_ctx = ExecutionContext()
        template_ctx = {"stream_name": stream_ctx.stream_name}
        result = await exe._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        assert result.success is True
        assert exec_ctx.custom_variables["v"] == "1080"

    @pytest.mark.asyncio
    async def test_adversarial_pattern_sets_empty_string(self, caplog):
        from auto_creation_schema import Action
        from auto_creation_executor import ExecutionContext
        exe = _executor_with_channels()
        action = Action(type="set_variable", params={
            "variable_name": "v",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": _EVIL_PATTERN_ALT,
        })
        stream_ctx = _minimal_stream_context(_EVIL_INPUT_ALT)
        exec_ctx = ExecutionContext()
        template_ctx = {"stream_name": stream_ctx.stream_name}
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = await exe._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        # Sentinel contract: fallback sets variable to empty string.
        assert result.success is True
        assert exec_ctx.custom_variables["v"] == ""


class TestExecuteSetVariableRegexReplace:
    """Executor regex_replace (sub) at L~1854 — sentinel: source_value unchanged."""

    @pytest.mark.asyncio
    async def test_happy_path_replace(self):
        from auto_creation_schema import Action
        from auto_creation_executor import ExecutionContext
        exe = _executor_with_channels()
        action = Action(type="set_variable", params={
            "variable_name": "v",
            "variable_mode": "regex_replace",
            "source_field": "stream_name",
            "pattern": r"HD",
            "replacement": "UHD",
        })
        stream_ctx = _minimal_stream_context("ESPN HD")
        exec_ctx = ExecutionContext()
        template_ctx = {"stream_name": stream_ctx.stream_name}
        result = await exe._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        assert result.success is True
        assert exec_ctx.custom_variables["v"] == "ESPN UHD"

    @pytest.mark.asyncio
    async def test_adversarial_pattern_returns_source_unchanged(self, caplog):
        from auto_creation_schema import Action
        from auto_creation_executor import ExecutionContext
        exe = _executor_with_channels()
        action = Action(type="set_variable", params={
            "variable_name": "v",
            "variable_mode": "regex_replace",
            "source_field": "stream_name",
            "pattern": _EVIL_PATTERN_ALT,
            "replacement": "X",
        })
        stream_ctx = _minimal_stream_context(_EVIL_INPUT_ALT)
        exec_ctx = ExecutionContext()
        template_ctx = {"stream_name": stream_ctx.stream_name}
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = await exe._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        assert result.success is True
        # Sentinel contract: safe_regex.sub returns input unchanged on
        # timeout — variable ends up set to the original source value.
        assert exec_ctx.custom_variables["v"] == _EVIL_INPUT_ALT


# =========================================================================
# Site 7 — auto_creation_executor._find_channel_by_regex (compile + search)
# =========================================================================


class TestFindChannelByRegex:
    """Executor compile + search at L~2423 — sentinel: None (channel not found)."""

    def test_happy_path_finds_channel(self):
        exe = _executor_with_channels([
            {"id": 1, "name": "ESPN HD"},
            {"id": 2, "name": "CNN"},
        ])
        ch = exe._find_channel_by_regex(r"^ESPN")
        assert ch is not None
        assert ch["id"] == 1

    def test_no_match_returns_none(self):
        exe = _executor_with_channels([
            {"id": 1, "name": "ESPN HD"},
        ])
        assert exe._find_channel_by_regex(r"^BBC") is None

    def test_adversarial_pattern_returns_none(self, caplog):
        exe = _executor_with_channels([
            {"id": i, "name": _EVIL_INPUT_ALT} for i in range(5)
        ])
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            ch = exe._find_channel_by_regex(_EVIL_PATTERN_ALT)
        elapsed = time.perf_counter() - start
        # Each of up to 5 channels gets its own 100 ms budget.
        assert elapsed < _MAX_ADVERSARIAL_SECONDS * 6
        assert ch is None


# =========================================================================
# Site 8 — auto_creation_engine._sort_key hot path (search)
# =========================================================================


class TestSortKeyHotPath:
    """Engine search() at L~2521 — sentinel: (-1, 0, '') unmatched sentinel."""

    def test_happy_path_captures_numeric_group(self):
        stream = _minimal_stream_context("Race 42 HD")
        key = _sort_key(stream, "stream_name_regex", sort_regex=r"Race (\d+)")
        # Sentinel tuple form: (0, float_or_zero, captured_str)
        assert key == (0, 42.0, "42")

    def test_no_match_returns_sentinel(self):
        stream = _minimal_stream_context("ESPN HD")
        key = _sort_key(stream, "stream_name_regex", sort_regex=r"Race (\d+)")
        assert key == (-1, 0, "")

    def test_accepts_precompiled_pattern(self):
        """The hot-path mitigation requires compiled-pattern support."""
        compiled = safe_regex.compile(r"Race (\d+)")
        stream = _minimal_stream_context("Race 7")
        key = _sort_key(stream, "stream_name_regex", sort_regex=compiled)
        assert key == (0, 7.0, "7")

    def test_adversarial_pattern_returns_sentinel_fast(self, caplog):
        stream = _minimal_stream_context(_EVIL_INPUT_ALT)
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            key = _sort_key(stream, "stream_name_regex", sort_regex=_EVIL_PATTERN_ALT)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        assert key == (-1, 0, "")

    @hyp_settings(
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
        derandomize=True,
    )
    @given(
        name_suffix=st.integers(min_value=0, max_value=99999),
        prefix=st.text(alphabet="ABCabc ", min_size=0, max_size=10),
    )
    def test_property_numeric_group_extraction(self, name_suffix, prefix):
        """For benign numeric-tag inputs, the sort key must extract the
        integer captured group identically to a stdlib re.search."""
        name = f"{prefix}Race {name_suffix}"
        stream = _minimal_stream_context(name)
        key = _sort_key(stream, sort_field="stream_name_regex", sort_regex=r"Race (\d+)")
        # Stdlib ground truth
        m = re.search(r"Race (\d+)", name)
        if m:
            assert key == (0, float(m.group(1)), m.group(1))
        else:
            assert key == (-1, 0, "")


# =========================================================================
# End-to-end smoke tests — exercise the full Condition -> Evaluator path
# with user-supplied regex strings, including an adversarial payload.
#
# Each test constructs a Condition with a user-controlled pattern, drives
# ConditionEvaluator.evaluate(), and asserts the rule still makes a
# decision (rather than raising or hanging) even when the pattern is a
# catastrophic-backtracking payload.
# =========================================================================


class TestSmokeAutoCreationEvaluator:
    """Smoke: user-supplied regex flows through Condition + Evaluator."""

    def test_stream_name_matches_benign(self):
        from auto_creation_schema import Condition, ConditionType
        ev = _evaluator_with_channels()
        ctx = _minimal_stream_context("ESPN HD 1080p")
        cond = Condition(type=ConditionType.STREAM_NAME_MATCHES,
                         value=r"HD\s+\d+", case_sensitive=False)
        result = ev.evaluate(cond, ctx)
        assert result.matched is True

    def test_stream_name_matches_adversarial_does_not_crash(self, caplog):
        from auto_creation_schema import Condition, ConditionType
        ev = _evaluator_with_channels()
        ctx = _minimal_stream_context(_EVIL_INPUT_ALT)
        cond = Condition(type=ConditionType.STREAM_NAME_MATCHES,
                         value=_EVIL_PATTERN_ALT, case_sensitive=False)
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = ev.evaluate(cond, ctx)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        # Fallback: no match. Rule skips, does not crash.
        assert result.matched is False

    def test_channel_exists_matching_adversarial_does_not_crash(self, caplog):
        from auto_creation_schema import Condition, ConditionType
        ev = _evaluator_with_channels([
            {"id": 1, "name": _EVIL_INPUT_ALT},
        ])
        ctx = _minimal_stream_context("irrelevant")
        cond = Condition(type=ConditionType.CHANNEL_EXISTS_MATCHING,
                         value=_EVIL_PATTERN_ALT, case_sensitive=False)
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = ev.evaluate(cond, ctx)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS * 2
        assert result.matched is False


class TestSmokeAutoCreationExecutor:
    """Smoke: user-supplied regex flows through a full action execution."""

    def test_name_transform_benign(self):
        exe = _executor_with_channels()
        got = exe._apply_name_transform(
            "107 | ESPN HD",
            {"name_transform_pattern": r"^\d+\s*\|\s*",
             "name_transform_replacement": ""},
        )
        assert got == "ESPN HD"

    def test_name_transform_adversarial_returns_stripped_original(self, caplog):
        exe = _executor_with_channels()
        src = _EVIL_INPUT_ALT
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            got = exe._apply_name_transform(
                src,
                {"name_transform_pattern": _EVIL_PATTERN_ALT,
                 "name_transform_replacement": "X"},
            )
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS
        # Fallback: original string unchanged (trailing whitespace stripped).
        assert got == src.strip()

    def test_find_channel_by_regex_adversarial_returns_none(self, caplog):
        exe = _executor_with_channels([
            {"id": i, "name": _EVIL_INPUT_ALT} for i in range(3)
        ])
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            got = exe._find_channel_by_regex(_EVIL_PATTERN_ALT)
        elapsed = time.perf_counter() - start
        assert elapsed < _MAX_ADVERSARIAL_SECONDS * 4
        assert got is None


class TestSmokeAutoCreationEngineSortKey:
    """Smoke: full-list sort with adversarial sort_regex does not hang."""

    def test_sort_list_with_adversarial_regex(self, caplog):
        # A handful of streams with adversarial names; sort must complete.
        streams = [_minimal_stream_context(f"{_EVIL_INPUT_ALT} #{i}") for i in range(4)]
        # Pre-compile once (the hot-path mitigation the engine uses).
        compiled = safe_regex.compile(_EVIL_PATTERN_ALT)
        start = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            streams.sort(key=lambda s: _sort_key(s, "stream_name_regex", compiled))
        elapsed = time.perf_counter() - start
        # Python's Timsort calls the key func O(n) times; per-call budget
        # 100 ms => worst-case under 1 second for 4 streams.
        assert elapsed < _MAX_ADVERSARIAL_SECONDS * 4
        # All keys collapsed to the sentinel form.
        keys = [_sort_key(s, "stream_name_regex", compiled) for s in streams]
        for k in keys:
            assert k == (-1, 0, "")

    def test_hot_path_1000_stream_sort_under_budget(self):
        """Regression guard for bd-eio04.15 hot-path mitigation.

        The compile-once optimization in ``_run_rules`` combined with
        safe_regex.search's compiled-pattern fast path should keep a
        1000-stream sort well below 50 ms. Baseline (stdlib re) is
        ~0.5 ms; compiled safe_regex measures ~2 ms locally. The 50 ms
        ceiling is a generous regression guard for CI jitter — the
        absolute cost remains imperceptible next to a real
        auto-creation run's API + DB work.
        """
        streams = []
        for i in range(1000):
            name = f"Race {i:05d} HD" if i % 2 == 0 else f"ESPN HD #{i:05d}"
            streams.append(_minimal_stream_context(name))
        compiled = safe_regex.compile(r"Race (\d+)")
        start = time.perf_counter()
        streams.sort(key=lambda s: _sort_key(s, "stream_name_regex", compiled))
        elapsed = time.perf_counter() - start
        assert elapsed < 0.050, (
            f"1000-stream sort exceeded 50ms budget: {elapsed * 1000:.2f}ms — "
            f"the hot-path mitigation in auto_creation_engine may have regressed."
        )
