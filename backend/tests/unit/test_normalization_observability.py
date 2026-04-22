"""
Unit tests for normalization observability (bd-eio04.9).

Exercises the metrics + decision-log helpers added to
``backend/observability.py`` and the canary harness hooks at
``backend/scripts/normalization_canary.py``. Scope:

- Metric registration — the 5 new ``ecm_normalization_*`` and
  ``ecm_auto_creation_channels_created_total`` series exist on the
  registry with the right label sets / bucket shapes.
- Sampler determinism — every-N stride produces a predictable keep/drop
  pattern; ``applied=true AND matched_rule_ids != []`` always samples.
- Decision-log payload shape — `input` / `output` truncation, the
  `input_sha256` correlation field, the rule-category enum bounding.
- Rule-category normalization — unknown `action_type` values collapse
  to ``"other"`` so the Prometheus label stays bounded.
"""
from __future__ import annotations

import hashlib
import json
import logging

import pytest

import observability


@pytest.fixture(autouse=True)
def _reset_observability():
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


class TestNormalizationMetricsRegistered:
    """The new Prometheus series exist with the documented label sets."""

    def test_rule_matches_counter_is_labeled_by_rule_category(self):
        metrics = observability.install_metrics()
        counter = metrics["normalization_rule_matches_total"]
        # Label set check — prometheus_client exposes ``_labelnames``
        # on the parent counter.
        assert counter._labelnames == ("rule_category",)

    def test_no_change_counter_has_no_labels(self):
        metrics = observability.install_metrics()
        counter = metrics["normalization_no_change_total"]
        assert counter._labelnames == ()

    def test_auto_creation_counter_labeled_by_normalized(self):
        metrics = observability.install_metrics()
        counter = metrics["auto_creation_channels_created_total"]
        assert counter._labelnames == ("normalized",)

    def test_normalization_duration_histogram_uses_sub_ms_buckets(self):
        metrics = observability.install_metrics()
        hist = metrics["normalization_duration_seconds"]
        # Buckets include the required microsecond-scale floor so the
        # histogram is useful for work in the 100µs–10ms regime.
        assert 0.0001 in hist._upper_bounds
        assert 0.5 in hist._upper_bounds
        # The HTTP histogram's 5ms floor is too coarse — verify we
        # didn't accidentally reuse the wrong bucket set.
        assert 0.005 in hist._upper_bounds

    def test_canary_divergence_counter_has_no_labels(self):
        metrics = observability.install_metrics()
        counter = metrics["normalization_canary_divergence_total"]
        assert counter._labelnames == ()

    def test_prometheus_text_exposes_new_series(self):
        observability.install_metrics()
        # Increment each new series once so the exposition carries
        # a sample line (prometheus-client omits zero-unique counters
        # by default for labeled series).
        observability.get_metric("normalization_rule_matches_total").labels(
            rule_category="replace"
        ).inc()
        observability.get_metric("normalization_no_change_total").inc()
        observability.get_metric("auto_creation_channels_created_total").labels(
            normalized="true"
        ).inc()
        observability.get_metric("normalization_duration_seconds").observe(0.001)
        observability.get_metric("normalization_canary_divergence_total").inc()

        body = observability.render_metrics().decode("utf-8")
        assert "ecm_normalization_rule_matches_total" in body
        assert "ecm_normalization_no_change_total" in body
        assert "ecm_auto_creation_channels_created_total" in body
        assert "ecm_normalization_duration_seconds" in body
        assert "ecm_normalization_canary_divergence_total" in body


class TestRuleCategoryBounding:
    def test_known_action_types_pass_through(self):
        for raw in (
            "remove",
            "replace",
            "regex_replace",
            "strip_prefix",
            "strip_suffix",
            "normalize_prefix",
            "capitalize",
            "tag_group",
            "legacy_tag",
        ):
            assert observability._normalize_rule_category(raw) == raw

    def test_unknown_action_type_collapses_to_other(self):
        # Future rule types or typos must not expand the label set.
        assert observability._normalize_rule_category("sanitize") == "other"
        assert observability._normalize_rule_category("") == "other"
        assert observability._normalize_rule_category(None) == "other"

    def test_case_insensitive_match(self):
        # `action_type` is stored lower-case in the DB but be defensive.
        assert observability._normalize_rule_category("Replace") == "replace"
        assert observability._normalize_rule_category("REMOVE") == "remove"


class TestDeterministicSampler:
    def test_first_call_is_always_sampled(self):
        sampler = observability._DeterministicSampler(stride=10)
        assert sampler.should_sample() is True

    def test_stride_of_10_keeps_every_tenth_call(self):
        sampler = observability._DeterministicSampler(stride=10)
        kept = [sampler.should_sample() for _ in range(30)]
        # Expected pattern: True at counter 1, 11, 21 → indices 0, 10, 20.
        keep_indices = [i for i, k in enumerate(kept) if k]
        assert keep_indices == [0, 10, 20]

    def test_always_keep_bypasses_stride(self):
        sampler = observability._DeterministicSampler(stride=1000)
        # With a huge stride, baseline calls would almost never sample;
        # always_keep must override.
        kept = [sampler.should_sample(always_keep=True) for _ in range(5)]
        assert kept == [True, True, True, True, True]

    def test_always_keep_advances_counter_so_baseline_stride_stays_aligned(self):
        # Contract: always_keep calls do not desynchronize the stride.
        # After 3 always-keep calls with stride=5, the next baseline call
        # is no longer guaranteed to be a "first" call.
        sampler = observability._DeterministicSampler(stride=5)
        for _ in range(3):
            sampler.should_sample(always_keep=True)
        # Counter = 3; baseline call increments to 4 → (4-1) % 5 != 0 → drop.
        assert sampler.should_sample() is False

    def test_set_stride_resets_counter(self):
        sampler = observability._DeterministicSampler(stride=10)
        for _ in range(7):
            sampler.should_sample()
        sampler.set_stride(5)
        # Counter reset → first call is always a keep.
        assert sampler.should_sample() is True


class TestTruncationAndHashing:
    def test_short_string_is_not_truncated(self):
        assert observability._truncate_for_log("hello") == "hello"

    def test_long_string_is_truncated_with_sentinel(self):
        raw = "A" * 300
        out = observability._truncate_for_log(raw)
        assert out.endswith("...[truncated]")
        assert out.startswith("A" * 256)
        assert len(out) == 256 + len("...[truncated]")

    def test_truncation_respects_custom_limit(self):
        raw = "B" * 50
        out = observability._truncate_for_log(raw, max_chars=10)
        assert out == "BBBBBBBBBB...[truncated]"

    def test_none_is_coerced_to_empty_string(self):
        assert observability._truncate_for_log(None) == ""  # type: ignore[arg-type]

    def test_sha256_matches_stdlib(self):
        raw = "US: ESPN ᴴᴰ ⁶⁰fps"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert observability.sha256_of(raw) == expected

    def test_sha256_handles_none(self):
        assert observability.sha256_of(None) == hashlib.sha256(b"").hexdigest()  # type: ignore[arg-type]


class TestRecordNormalizationDecision:
    """Full-path test of the sampled INFO log + metric updates."""

    def _capture_decision_logs(self):
        """Return (list_to_capture, cleanup_fn). Logs under the normalization
        decision logger bypass propagation by default; attach a capturing
        handler so the tests can inspect the payload."""
        captured: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Capture(level=logging.INFO)
        handler.addFilter(observability._TraceIdFilter())
        logger = logging.getLogger(observability.NORMALIZATION_DECISION_LOGGER)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        def cleanup():
            logger.removeHandler(handler)

        return captured, cleanup

    def test_always_sample_when_applied_and_rule_matched(self):
        # Even with a gigantic stride, a decision that actually changed
        # the string must still produce a log line.
        observability.reset_normalization_sampler_for_tests(stride=1000)
        captured, cleanup = self._capture_decision_logs()
        try:
            emitted = observability.record_normalization_decision(
                input_text="hello ᴴᴰ",
                output_text="hello HD",
                matched_rule_ids=[42],
                applied=True,
                policy_version="unified-v1",
                duration_seconds=0.00123,
                rule_category="replace",
                extra={"source": "normalize"},
            )
        finally:
            cleanup()
        assert emitted is True
        assert len(captured) == 1
        rec = captured[0]
        # JSON formatter flattens ``extra`` onto the record — we can read
        # the fields off directly via attribute access.
        assert rec.applied is True
        assert rec.matched_rule_ids == [42]
        assert rec.policy_version == "unified-v1"
        # Correlation hash is full (64 hex chars), not truncated.
        assert len(rec.input_sha256) == 64
        # Rule category is the bounded enum value.
        assert rec.rule_category == "replace"
        # Source tag from `extra` passes through.
        assert rec.source == "normalize"

    def test_stride_drops_baseline_no_op_traffic(self):
        observability.reset_normalization_sampler_for_tests(stride=100)
        captured, cleanup = self._capture_decision_logs()
        try:
            # 10 no-op decisions with stride=100 → only the first one keeps.
            emitted_flags = [
                observability.record_normalization_decision(
                    input_text="nothing to see",
                    output_text="nothing to see",
                    matched_rule_ids=[],
                    applied=False,
                    policy_version="unified-v1",
                )
                for _ in range(10)
            ]
        finally:
            cleanup()
        # First call always kept, next 9 dropped.
        assert emitted_flags == [True] + [False] * 9
        assert len(captured) == 1

    def test_no_change_counter_increments_for_noop_decisions(self):
        observability.reset_normalization_sampler_for_tests(stride=1)
        observability.install_metrics()
        counter = observability.get_metric("normalization_no_change_total")
        baseline = counter._value.get()
        observability.record_normalization_decision(
            input_text="same",
            output_text="same",
            matched_rule_ids=[],
            applied=False,
            policy_version="unified-v1",
        )
        assert counter._value.get() == baseline + 1

    def test_rule_matches_counter_labeled_when_category_given(self):
        observability.reset_normalization_sampler_for_tests(stride=1)
        observability.install_metrics()
        counter = observability.get_metric("normalization_rule_matches_total")
        # Record with a known category and confirm the labeled child ticks.
        observability.record_normalization_decision(
            input_text="x",
            output_text="y",
            matched_rule_ids=[1],
            applied=True,
            policy_version="unified-v1",
            rule_category="replace",
        )
        child = counter.labels(rule_category="replace")
        assert child._value.get() == 1.0

    def test_input_field_is_truncated_to_256_chars_in_payload(self):
        observability.reset_normalization_sampler_for_tests(stride=1)
        captured, cleanup = self._capture_decision_logs()
        huge = "X" * 400
        try:
            observability.record_normalization_decision(
                input_text=huge,
                output_text=huge,
                matched_rule_ids=[],
                applied=False,
                policy_version="unified-v1",
            )
        finally:
            cleanup()
        rec = captured[0]
        # Truncated input field, but full-length SHA stays.
        assert rec.input.endswith("...[truncated]")
        assert rec.input.startswith("X" * 256)
        full_hash = hashlib.sha256(huge.encode("utf-8")).hexdigest()
        assert rec.input_sha256 == full_hash


class TestSamplerEnvStride:
    def test_env_stride_override(self, monkeypatch):
        monkeypatch.setenv("ECM_NORMALIZATION_LOG_STRIDE", "50")
        stride = observability._read_stride_from_env()
        assert stride == 50

    def test_invalid_env_stride_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ECM_NORMALIZATION_LOG_STRIDE", "not-a-number")
        stride = observability._read_stride_from_env()
        assert stride == observability._DEFAULT_SAMPLE_STRIDE

    def test_zero_or_negative_stride_clamps_to_one(self, monkeypatch):
        monkeypatch.setenv("ECM_NORMALIZATION_LOG_STRIDE", "0")
        assert observability._read_stride_from_env() == 1
        monkeypatch.setenv("ECM_NORMALIZATION_LOG_STRIDE", "-5")
        assert observability._read_stride_from_env() == 1
