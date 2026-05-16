"""Unit tests for Stats v2 observability instrumentation (bd-skqln.12).

Scope:

* The Stats v2 metric families are registered with the documented label
  sets (and ONLY those labels). Cardinality discipline — ``user_id``,
  ``channel_id``, ``session_id``, and ``target_id`` must NEVER appear as
  metric labels. ``provider_id`` is the only viewer-keyed dimension that
  IS allowed (bounded <20 providers; SRE pre-clearance).
* ``ecm_session_telemetry_writes_total`` increments on each call to the
  ``_write_session_telemetry`` helper, partitioned by
  ``result=success|failure``.
* ``ecm_session_telemetry_write_duration_seconds`` observes the wall time
  of each write call.
* ``ecm_session_telemetry_row_count`` is a gauge for storage-growth
  alerts. bd-ae58c (Option B): BandwidthTracker no longer writes it —
  StatsV2RollupTask is the sole writer (post-prune table total, nightly).
  Per-poll batch size is derivable from ecm_session_telemetry_writes_total.
* ``ecm_provider_resolution_total`` is incremented by the resolver SLI
  hook (``BandwidthTracker._log_provider_resolution_sli``) using the
  ``resolved`` and ``unresolved`` counts the resolver already computes.
* The Stats v2 HTTP query histogram (``ecm_stats_query_duration_seconds``)
  is emitted by the existing observability middleware when (and only
  when) the matched route pattern lives under ``/api/stats/`` — and the
  ``endpoint`` label uses the FastAPI route PATTERN, not the resolved
  path. Cardinality stays bounded regardless of how many user-ids or
  channel-ids appear in the URL.

These tests are the RED phase of bd-skqln.12. They will fail until the
metrics are added to ``observability._build_metrics`` and the wiring is
added in ``bandwidth_tracker.py`` and ``main.py``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database
import observability


# ---------------------------------------------------------------------------
# Per-test registry reset — every test rebuilds the metrics so counters
# don't leak across cases.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_metrics_state():
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _labelnames(metric):
    """Return the metric's declared label names as a sorted tuple."""
    return tuple(sorted(getattr(metric, "_labelnames", ())))


def _counter_value(counter, **labels):
    """Read a Prometheus counter sample's current value."""
    return counter.labels(**labels)._value.get()


def _histogram_sample_count(histogram, **labels) -> int:
    """Return the total observation count for a histogram.

    Reads the ``_count`` sample emitted by ``Histogram.collect`` so the
    helper works against both labeled and unlabeled histograms without
    relying on prometheus_client's private accumulator shape (which
    differs between versions — ``_sum`` is a MutexValue, and the count
    lives in the ``+Inf`` bucket on labeled histograms).
    """
    count_suffix = "_count"
    for family in histogram.collect():
        for sample in family.samples:
            if not sample.name.endswith(count_suffix):
                continue
            # Match the requested label set exactly. Unlabeled histograms
            # render samples with an empty ``labels`` dict, so the
            # ``labels == {}`` form drops in cleanly.
            if sample.labels == labels:
                return int(sample.value)
    return 0


# ---------------------------------------------------------------------------
# Metric registry — names and label sets
# ---------------------------------------------------------------------------
class TestStatsV2MetricsRegistered:
    """Every Stats v2 metric family the bead defines must exist after
    ``install_metrics``, with the bead-mandated label set."""

    def test_session_telemetry_writes_counter_exists(self):
        metric = observability.get_metric("session_telemetry_writes_total")
        assert metric is not None
        assert _labelnames(metric) == ("result",)

    def test_session_telemetry_write_duration_histogram_exists(self):
        metric = observability.get_metric("session_telemetry_write_duration_seconds")
        assert metric is not None
        # Unlabeled — write duration is a single per-call observation.
        assert _labelnames(metric) == ()

    def test_session_telemetry_row_count_gauge_exists(self):
        metric = observability.get_metric("session_telemetry_row_count")
        assert metric is not None
        # Unlabeled — represents the most recent write batch's row count.
        assert _labelnames(metric) == ()

    def test_provider_resolution_counter_exists(self):
        metric = observability.get_metric("provider_resolution_total")
        assert metric is not None
        assert _labelnames(metric) == ("result",)

    def test_stats_query_duration_histogram_exists(self):
        metric = observability.get_metric("stats_query_duration_seconds")
        assert metric is not None
        # Bounded label set — endpoint (route pattern) + granularity. The
        # ~5 endpoints × 3 granularities ceiling sits inside the bead's
        # SRE-approved cardinality envelope.
        assert _labelnames(metric) == ("endpoint", "granularity")

    def test_metric_names_render_with_ecm_prefix(self):
        """Prometheus text exposition must show the ``ecm_`` prefix."""
        # Increment / observe once each so each metric appears in the dump.
        observability.get_metric("session_telemetry_writes_total").labels(
            result="success"
        ).inc()
        observability.get_metric("session_telemetry_write_duration_seconds").observe(0.01)
        observability.get_metric("session_telemetry_row_count").set(5)
        observability.get_metric("provider_resolution_total").labels(
            result="resolved"
        ).inc()
        observability.get_metric("stats_query_duration_seconds").labels(
            endpoint="/api/stats/watch-time", granularity="total"
        ).observe(0.05)

        body = observability.render_metrics().decode("utf-8")
        assert "ecm_session_telemetry_writes_total" in body
        assert "ecm_session_telemetry_write_duration_seconds" in body
        assert "ecm_session_telemetry_row_count" in body
        assert "ecm_provider_resolution_total" in body
        assert "ecm_stats_query_duration_seconds" in body


class TestStatsV2CardinalityDiscipline:
    """SRE veto checkpoint — the banned dimensions must NEVER be metric
    labels. user_id and channel_id belong in logs (correlated by trace_id),
    not in the metric cardinality. provider_id IS allowed, but no Stats v2
    metric uses it as a label in this bead — confirm we didn't sneak one in.
    """

    BANNED_LABELS = frozenset({
        "user_id",
        "channel_id",
        "session_id",
        "target_id",
        "client_ip",
        # trace_id belongs in logs only.
        "trace_id",
    })

    STATS_V2_METRIC_NAMES = (
        "session_telemetry_writes_total",
        "session_telemetry_write_duration_seconds",
        "session_telemetry_row_count",
        "provider_resolution_total",
        "stats_query_duration_seconds",
    )

    @pytest.mark.parametrize("metric_name", STATS_V2_METRIC_NAMES)
    def test_no_banned_label_on_metric(self, metric_name):
        metric = observability.get_metric(metric_name)
        names = set(getattr(metric, "_labelnames", ()))
        overlap = names & self.BANNED_LABELS
        assert not overlap, (
            f"{metric_name} declares banned label(s) {overlap}; "
            f"see bd-skqln.12 SRE veto on user_id/channel_id labels"
        )

    def test_session_telemetry_writes_result_label_is_bounded(self):
        """Only 'success' and 'failure' are emitted — no free-form strings."""
        # Increment with both legal values + render — neither should crash,
        # both should appear. Any future commit that adds a third value should
        # update this test deliberately.
        counter = observability.get_metric("session_telemetry_writes_total")
        counter.labels(result="success").inc()
        counter.labels(result="failure").inc()
        body = observability.render_metrics().decode("utf-8")
        assert 'result="success"' in body
        assert 'result="failure"' in body

    def test_provider_resolution_result_label_is_bounded(self):
        """Only 'resolved' and 'unresolved' are emitted."""
        counter = observability.get_metric("provider_resolution_total")
        counter.labels(result="resolved").inc()
        counter.labels(result="unresolved").inc()
        body = observability.render_metrics().decode("utf-8")
        assert 'result="resolved"' in body
        assert 'result="unresolved"' in body


# ---------------------------------------------------------------------------
# BandwidthTracker — session_telemetry write + provider resolver instrumentation
# ---------------------------------------------------------------------------
# Re-use the existing fixtures from the larger session_telemetry test
# module — duplicating them here would just drift on the next edit.
from tests.unit.test_bandwidth_tracker_session_telemetry import (  # noqa: E402
    _channel_payload,
    _drive_two_polls,
    mock_client,
    patched_session_local,
    seed_synthetic_user,
    tracker,
)


@pytest.mark.asyncio
async def test_successful_write_increments_writes_total_success(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Each successful ``_write_session_telemetry`` call increments
    ``ecm_session_telemetry_writes_total{result="success"}``."""
    counter = observability.get_metric("session_telemetry_writes_total")
    before = _counter_value(counter, result="success")

    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    await _drive_two_polls(tracker, mock_client, first, second)

    after = _counter_value(counter, result="success")
    # Two polls = two successful writes (each poll calls the helper once).
    assert after - before == 2


@pytest.mark.asyncio
async def test_successful_write_records_duration_observation(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Each call records one observation into the write-duration histogram."""
    hist = observability.get_metric("session_telemetry_write_duration_seconds")
    before = _histogram_sample_count(hist)

    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    await _drive_two_polls(tracker, mock_client, first, second)

    after = _histogram_sample_count(hist)
    assert after - before == 2


@pytest.mark.asyncio
async def test_successful_write_does_not_update_row_count_gauge(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """bd-ae58c (Option B): BandwidthTracker no longer sets
    ecm_session_telemetry_row_count. The gauge is owned exclusively by
    StatsV2RollupTask (nightly post-prune table total). Per-poll batch
    size is observable via the ecm_session_telemetry_writes_total rate.
    After two polls the gauge must remain at its initial value (0)."""
    gauge = observability.get_metric("session_telemetry_row_count")
    before = gauge._value.get()

    first = _channel_payload(
        total_bytes=1_000_000,
        client_ips=["10.0.0.1", "10.0.0.2"],
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_ips=["10.0.0.1", "10.0.0.2"],
    )
    await _drive_two_polls(tracker, mock_client, first, second)

    # Gauge must not have been touched by BandwidthTracker.
    assert gauge._value.get() == before


@pytest.mark.asyncio
async def test_failed_write_increments_writes_total_failure(
    patched_session_local,
    tracker,
    mock_client,
):
    """A write that raises is counted under ``result="failure"`` and does
    NOT increment the success counter."""
    counter = observability.get_metric("session_telemetry_writes_total")
    before_success = _counter_value(counter, result="success")
    before_failure = _counter_value(counter, result="failure")

    first = _channel_payload(total_bytes=1_000_000)
    second = _channel_payload(total_bytes=2_000_000)

    # Drive poll 1 cleanly so _active_connections is populated and the
    # helper has work to attempt on poll 2.
    mock_client.get_channel_stats.return_value = {"channels": [first]}
    await tracker._collect_stats()

    # Sabotage poll 2: make SessionTelemetry() raise so the helper's
    # try/except records a failure.
    mock_client.get_channel_stats.return_value = {"channels": [second]}
    with patch(
        "bandwidth_tracker.SessionTelemetry",
        side_effect=RuntimeError("synthetic ORM failure"),
    ):
        await tracker._collect_stats()

    after_success = _counter_value(counter, result="success")
    after_failure = _counter_value(counter, result="failure")
    # Poll 1 succeeded; poll 2 failed.
    assert after_success - before_success == 1
    assert after_failure - before_failure == 1


@pytest.mark.asyncio
async def test_provider_resolution_sli_increments_counter_both_results(
    tracker,
):
    """The resolver's per-poll SLI emission (``_log_provider_resolution_sli``)
    increments ``ecm_provider_resolution_total`` by the resolved and
    unresolved counts it computed."""
    counter = observability.get_metric("provider_resolution_total")
    before_resolved = _counter_value(counter, result="resolved")
    before_unresolved = _counter_value(counter, result="unresolved")

    # Direct unit call — bypasses the full resolver to focus on the
    # metric-emission contract.
    tracker._log_provider_resolution_sli(resolved_count=3, unresolved_count=2)

    after_resolved = _counter_value(counter, result="resolved")
    after_unresolved = _counter_value(counter, result="unresolved")
    assert after_resolved - before_resolved == 3
    assert after_unresolved - before_unresolved == 2


@pytest.mark.asyncio
async def test_provider_resolution_counter_during_full_resolver_run(
    patched_session_local,
    tracker,
):
    """End-to-end: drive the resolver against a stubbed Dispatcharr response
    and assert the provider_resolution counter reflects the SLI math (one
    resolved channel, one unresolved-because-no-stream-id channel)."""
    counter = observability.get_metric("provider_resolution_total")
    before_resolved = _counter_value(counter, result="resolved")
    before_unresolved = _counter_value(counter, result="unresolved")

    # Stub the batch lookup so stream_id=101 → provider 7, the other has
    # no stream_id at all.
    tracker.client.get_streams_by_ids = AsyncMock(
        return_value=[{"id": 101, "m3u_account": 7}]
    )

    snapshot = [
        {"channel_uuid": "ch-a", "stream_id": 101},
        {"channel_uuid": "ch-b", "stream_id": None},  # unresolvable
    ]
    result = await tracker._resolve_provider_ids(snapshot)
    # bd-kh23e: resolver now returns ``ProviderResolution`` NamedTuples
    # instead of bare provider ids. ``.provider_id`` is the carryover for
    # this counter-side assertion.
    assert result["ch-a"].provider_id == 7
    assert result["ch-b"].provider_id is None

    after_resolved = _counter_value(counter, result="resolved")
    after_unresolved = _counter_value(counter, result="unresolved")
    assert after_resolved - before_resolved == 1
    assert after_unresolved - before_unresolved == 1


@pytest.mark.asyncio
async def test_no_user_id_or_channel_id_label_after_full_poll(
    patched_session_local,
    seed_synthetic_user,
    tracker,
    mock_client,
):
    """Defense-in-depth: after a real polling cycle that DOES populate
    user_id and channel_id in the session_telemetry rows themselves, the
    metric registry must STILL not carry user_id / channel_id as labels on
    any series. Verifies the wiring didn't accidentally pipe row attributes
    into label values."""
    first = _channel_payload(
        total_bytes=1_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    second = _channel_payload(
        total_bytes=2_000_000,
        client_user_ids={"10.0.0.1": seed_synthetic_user},
    )
    await _drive_two_polls(tracker, mock_client, first, second)

    body = observability.render_metrics().decode("utf-8")
    banned_substrings = (
        f'user_id="{seed_synthetic_user}"',
        'channel_id="ch-uuid-1"',
        'session_id="conn-',
    )
    for fragment in banned_substrings:
        assert fragment not in body, (
            f"banned label substring {fragment!r} appeared in /metrics body — "
            "Stats v2 cardinality guarantee broken"
        )


# ---------------------------------------------------------------------------
# Logging — write-failure correlation fields
# ---------------------------------------------------------------------------
class TestSessionTelemetryWriteFailureLog:
    """Bead spec: every session_telemetry write failure logs at WARN with
    trace_id, session_id, provider_id — and does NOT pair user_id with
    channel_id in the same line (Privacy 11a)."""

    @pytest.mark.asyncio
    async def test_write_failure_emits_warning_log(
        self,
        patched_session_local,
        tracker,
        mock_client,
        caplog,
    ):
        import logging as _logging

        first = _channel_payload(total_bytes=1_000_000)
        second = _channel_payload(total_bytes=2_000_000)

        # Poll 1 clean → _active_connections seeded.
        mock_client.get_channel_stats.return_value = {"channels": [first]}
        await tracker._collect_stats()

        # Poll 2 sabotaged.
        mock_client.get_channel_stats.return_value = {"channels": [second]}
        with caplog.at_level(_logging.WARNING, logger="bandwidth_tracker"):
            with patch(
                "bandwidth_tracker.SessionTelemetry",
                side_effect=RuntimeError("synthetic ORM failure"),
            ):
                await tracker._collect_stats()

        # The failure log line is at WARN (or higher) and references the
        # write path.
        warn_records = [
            r for r in caplog.records
            if r.levelno >= _logging.WARNING and "session_telemetry" in r.message.lower()
        ]
        assert warn_records, (
            "expected a WARN-level log line for session_telemetry write "
            f"failure; saw: {[(r.levelname, r.message) for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_write_failure_log_does_not_pair_user_id_with_channel_id(
        self,
        patched_session_local,
        seed_synthetic_user,
        tracker,
        mock_client,
        caplog,
    ):
        """Privacy 11a: user_id and channel_id must not appear together on
        the same log record. The failure path is a top-level helper exception
        — it should NOT enumerate per-row identifiers in a way that pairs
        them."""
        import logging as _logging

        first = _channel_payload(
            total_bytes=1_000_000,
            client_user_ids={"10.0.0.1": seed_synthetic_user},
        )
        second = _channel_payload(
            total_bytes=2_000_000,
            client_user_ids={"10.0.0.1": seed_synthetic_user},
        )

        mock_client.get_channel_stats.return_value = {"channels": [first]}
        await tracker._collect_stats()

        mock_client.get_channel_stats.return_value = {"channels": [second]}
        with caplog.at_level(_logging.WARNING):
            with patch(
                "bandwidth_tracker.SessionTelemetry",
                side_effect=RuntimeError("synthetic ORM failure"),
            ):
                await tracker._collect_stats()

        # Inspect each WARN-or-higher log line: must not include both the
        # user_id AND the channel_uuid in the same message.
        for record in caplog.records:
            if record.levelno < _logging.WARNING:
                continue
            text = record.getMessage()
            paired = (
                str(seed_synthetic_user) in text and "ch-uuid-1" in text
            )
            assert not paired, (
                f"Privacy 11a violation: log line pairs user_id+channel_id: {text!r}"
            )


# ---------------------------------------------------------------------------
# HTTP middleware — stats_query_duration_seconds emission
# ---------------------------------------------------------------------------
class TestStatsQueryDurationHistogramEmission:
    """The existing observability middleware in ``main.py`` must emit one
    ``ecm_stats_query_duration_seconds`` observation per ``/api/stats/*``
    request, using the FastAPI route pattern as the ``endpoint`` label and
    the ``group_by`` query param (or ``"none"``) as the ``granularity`` label.

    Non-stats endpoints (``/api/health``, ``/api/version``) must NOT emit
    into this histogram — that would defeat the bead's "this is the Stats
    v2 SLI surface" framing.
    """

    @pytest.mark.asyncio
    async def test_stats_endpoint_emits_one_observation(self, async_client):
        hist = observability.get_metric("stats_query_duration_seconds")
        before = _histogram_sample_count(
            hist, endpoint="/api/stats/bandwidth", granularity="none"
        )

        # bandwidth is a no-param GET — fine for a smoke check.
        await async_client.get("/api/stats/bandwidth")

        after = _histogram_sample_count(
            hist, endpoint="/api/stats/bandwidth", granularity="none"
        )
        assert after - before == 1

    @pytest.mark.asyncio
    async def test_health_endpoint_does_not_emit_stats_histogram(self, async_client):
        hist = observability.get_metric("stats_query_duration_seconds")

        def _total_count() -> int:
            total = 0
            for family in hist.collect():
                for sample in family.samples:
                    if sample.name.endswith("_count"):
                        total += int(sample.value)
            return total

        before_total = _total_count()
        await async_client.get("/api/health")
        await async_client.get("/api/health")
        after_total = _total_count()
        assert after_total == before_total, (
            "/api/health requests must NOT increment the Stats v2 query "
            "histogram — that metric exists for Stats v2 endpoints only."
        )

    @pytest.mark.asyncio
    async def test_endpoint_label_uses_route_pattern_not_resolved_path(
        self, async_client
    ):
        """A parametrized endpoint must collapse to one ``endpoint`` label
        value, not one per resolved path."""
        hist = observability.get_metric("stats_query_duration_seconds")

        # Hit a parametrized stats endpoint twice with different resolved
        # paths. The route pattern in the FastAPI stats router is
        # ``/api/stats/channels/{channel_id}``.
        await async_client.get("/api/stats/channels/ch-aaa")
        await async_client.get("/api/stats/channels/ch-bbb")

        body = observability.render_metrics().decode("utf-8")
        # The route pattern label MUST appear.
        assert 'endpoint="/api/stats/channels/{channel_id}"' in body, body
        # The resolved paths MUST NOT appear (cardinality explosion).
        assert 'endpoint="/api/stats/channels/ch-aaa"' not in body
        assert 'endpoint="/api/stats/channels/ch-bbb"' not in body

    @pytest.mark.asyncio
    async def test_granularity_label_reads_group_by_query_param(self, async_client):
        """When the request carries a ``group_by`` query parameter, the
        ``granularity`` label adopts its value (bounded enum). Absent the
        query param, ``granularity`` defaults to ``"none"``."""
        hist = observability.get_metric("stats_query_duration_seconds")
        # /api/stats/bandwidth has no group_by support but accepts arbitrary
        # query params at the ASGI layer — FastAPI ignores unknown query
        # params unless the handler enumerates them. Using a request that
        # 200s with the param keeps the test focused on the label-derivation
        # contract.
        await async_client.get("/api/stats/bandwidth?group_by=day")
        body = observability.render_metrics().decode("utf-8")
        assert 'granularity="day"' in body, body
