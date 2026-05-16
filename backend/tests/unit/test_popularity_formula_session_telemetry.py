"""Popularity formula regression — locks in the post-step-(d) formula.

Bead: ``enhancedchannelmanager-skqln.3`` step (d).

Background: step (d) repointed ``popularity_calculator._gather_metrics``
off the legacy ``ChannelWatchStats`` aggregate and onto
``session_telemetry`` (per-poll grain). The two surfaces expose
different metrics:

* ``ChannelWatchStats.watch_count`` is a *state-transition counter* —
  incremented once each time a channel goes inactive→active across
  polls. Not derivable from a per-poll observation stream.
* ``session_telemetry`` exposes ``session_id``, ``observed_at``,
  ``poll_interval_ms``, and the per-poll grain that feeds bytes_delta.

The substitution chosen for the ``watch_count`` weight is
``COUNT(DISTINCT session_id)`` — the number of distinct viewing
sessions on the channel within the period. Justification:

* A short blip (one connection, ~30 seconds, abandoned) = 1 distinct
  session_id = legacy ``watch_count`` would also have incremented by 1
  (one inactive→active transition).
* A 4-hour binge by one client = 1 distinct session_id = legacy
  ``watch_count`` would also have been 1 (one inactive→active
  transition).
* Two clients joining the same channel back-to-back = 2 distinct
  session_ids ≈ legacy ``watch_count`` of ~2 (two transitions if there
  was an inactive gap between them).

The mapping is not bit-identical to the legacy semantic, but it is
the closest poll-derivable proxy and ranks similarly in practice. The
``total_watch_seconds / poll_interval`` alternative was rejected as it
would silently re-skin the formula to over-weight long viewing
sessions (one binge would dominate).

This test seeds a deterministic ``session_telemetry`` dataset and
asserts the calculator produces the expected channel ordering and
metric values, so any future change to the formula has to acknowledge
the regression explicitly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from models import (
    ChannelPopularityScore,
    SessionTelemetry,
    UniqueClientConnection,
)
from popularity_calculator import PopularityCalculator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_telemetry_session(
    session,
    *,
    channel_id: str,
    channel_name: str,
    session_id: str,
    poll_count: int,
    base_observed_at_ms: int,
    poll_interval_ms: int = 10_000,
    bytes_per_poll: int = 1000,
) -> None:
    """Seed N consecutive poll rows for one viewing session on one channel.

    Also seeds one ``UniqueClientConnection`` row so the calculator's
    channel-name side-load has a source to read from (the legacy
    ``ChannelWatchStats.channel_name`` field is gone post-step-(d)).
    """
    for i in range(poll_count):
        observed_at = base_observed_at_ms + i * poll_interval_ms
        session.add(
            SessionTelemetry(
                session_id=session_id,
                observed_at=observed_at,
                user_id=None,
                provider_id=None,
                channel_id=channel_id,
                bytes_delta=bytes_per_poll,
                buffer_event_count=0,
                poll_interval_ms=poll_interval_ms,
            )
        )

    # One UniqueClientConnection row so channel_name side-load works.
    # Uses a stable IP derived from session_id so multiple sessions on
    # the same channel get distinct rows (matching the writer's
    # behavior of one connection row per IP).
    last_observed_dt = datetime.utcfromtimestamp(
        (base_observed_at_ms + (poll_count - 1) * poll_interval_ms) / 1000.0
    )
    session.add(
        UniqueClientConnection(
            ip_address=f"10.0.0.{abs(hash(session_id)) % 200 + 1}",
            channel_id=channel_id,
            channel_name=channel_name,
            user_id=None,
            username=None,
            date=last_observed_dt.date(),
            connected_at=last_observed_dt,
            watch_seconds=poll_count * (poll_interval_ms // 1000),
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPopularityFormulaSessionTelemetry:
    """Regression: the post-step-(d) formula produces stable rankings
    against a deterministically-seeded ``session_telemetry`` dataset.
    """

    def test_formula_ranks_by_distinct_sessions_and_watch_time(self, test_session):
        """Seeded dataset:

        * channel-alpha: 3 distinct sessions, 30 polls total
          (10 polls × 3 sessions), 10s/poll → 300s total watch time.
        * channel-bravo: 1 long session, 50 polls, 500s total watch
          time. *More* total watch time but *fewer* distinct sessions.
        * channel-charlie: 5 distinct sessions, 4 polls each, 20 polls
          total, 200s total watch time. *Most* distinct sessions but
          shortest total watch time.

        Default weights (``DEFAULT_WEIGHTS``):
        * watch_count   0.25  → DISTINCT session_id
        * watch_time    0.30  → SUM of distinct-poll intervals
        * unique_viewers 0.30 → COUNT(DISTINCT ip) from connections
        * bandwidth     0.15  → SUM(bytes_delta)

        With these weights and the seeded data, alpha and charlie tie
        for "most distinct sessions vs. most watch_time" — the formula
        should produce a stable ranking that the regression test
        locks in.
        """
        # All times anchored to "today - 1 day" so the default 7-day
        # period filter includes them.
        anchor = datetime.utcnow() - timedelta(days=1)
        anchor_ms = int(anchor.timestamp() * 1000)
        poll_interval_ms = 10_000

        # channel-alpha: 3 sessions × 10 polls
        for s in range(3):
            _seed_telemetry_session(
                test_session,
                channel_id="ch-alpha",
                channel_name="Alpha",
                session_id=f"conn-alpha-{s}",
                poll_count=10,
                base_observed_at_ms=anchor_ms + s * 200_000,
                poll_interval_ms=poll_interval_ms,
                bytes_per_poll=1000,
            )

        # channel-bravo: 1 session × 50 polls
        _seed_telemetry_session(
            test_session,
            channel_id="ch-bravo",
            channel_name="Bravo",
            session_id="conn-bravo-0",
            poll_count=50,
            base_observed_at_ms=anchor_ms,
            poll_interval_ms=poll_interval_ms,
            bytes_per_poll=1000,
        )

        # channel-charlie: 5 sessions × 4 polls
        for s in range(5):
            _seed_telemetry_session(
                test_session,
                channel_id="ch-charlie",
                channel_name="Charlie",
                session_id=f"conn-charlie-{s}",
                poll_count=4,
                base_observed_at_ms=anchor_ms + s * 100_000,
                poll_interval_ms=poll_interval_ms,
                bytes_per_poll=1000,
            )
        test_session.commit()

        calc = PopularityCalculator()
        start_date = date.today() - timedelta(days=7)
        end_date = date.today() + timedelta(days=1)
        metrics = calc._gather_metrics(test_session, start_date, end_date)

        # All three channels are represented.
        assert set(metrics) == {"ch-alpha", "ch-bravo", "ch-charlie"}

        # watch_count = DISTINCT session_id (post-step-(d) semantic).
        assert metrics["ch-alpha"]["watch_count"] == 3
        assert metrics["ch-bravo"]["watch_count"] == 1
        assert metrics["ch-charlie"]["watch_count"] == 5

        # watch_time = sum of distinct-poll intervals per channel.
        # Each channel's seeding uses distinct observed_at values so the
        # DISTINCT-by-(channel, observed_at) collapse is a no-op here.
        assert metrics["ch-alpha"]["watch_time"] == 30 * 10  # 30 polls × 10s
        assert metrics["ch-bravo"]["watch_time"] == 50 * 10  # 50 polls × 10s
        assert metrics["ch-charlie"]["watch_time"] == 20 * 10  # 20 polls × 10s

        # channel_name side-loaded from UniqueClientConnection.
        assert metrics["ch-alpha"]["channel_name"] == "Alpha"
        assert metrics["ch-bravo"]["channel_name"] == "Bravo"
        assert metrics["ch-charlie"]["channel_name"] == "Charlie"

        # Compute scores from these metrics and assert a stable ranking.
        scores = calc._calculate_scores(metrics)
        ranked = sorted(scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
        ranked_ids = [r[0] for r in ranked]

        # With the default weights (watch_count 0.25, watch_time 0.30,
        # unique_viewers 0.30, bandwidth 0.15) and min-max normalization
        # against the max of each axis, the per-channel scores work out to:
        #
        #   ch-charlie: watch_count=5/5, watch_time=200/500, viewers=5/5,
        #               bandwidth=20000/50000 → 100*0.25 + 40*0.30 +
        #               100*0.30 + 40*0.15 = 73
        #   ch-alpha:   watch_count=3/5, watch_time=300/500, viewers=3/5,
        #               bandwidth=30000/50000 → 60 on every axis × any
        #               weight that sums to 1.0 = 60. Balanced middle.
        #   ch-bravo:   watch_count=1/5, watch_time=500/500, viewers=1/5,
        #               bandwidth=50000/50000 → 20*0.25 + 100*0.30 +
        #               20*0.30 + 100*0.15 = 56
        #
        # Expected ranking: charlie > alpha > bravo. Bravo wins two axes
        # outright (watch_time and bandwidth) but is dominated on the
        # other two; alpha is the balanced middle and edges bravo by 4
        # points. Charlie wins on the 0.55 combined weight of
        # watch_count + unique_viewers and that's enough to clear the
        # field.
        assert ranked_ids == ["ch-charlie", "ch-alpha", "ch-bravo"], (
            f"Default-weight ranking changed. Expected ['ch-charlie', "
            f"'ch-alpha', 'ch-bravo'], got {ranked_ids}. If this is a "
            f"deliberate formula change, update the regression and "
            f"document the new semantic in the test docstring."
        )

    def test_formula_ignores_data_outside_period(self, test_session):
        """Rows whose ``observed_at`` falls outside the period window
        must not contribute to any channel's metrics.

        Seed two channels: one with all rows inside the 7-day window,
        one with all rows 30 days old. The 30-day-old channel must not
        appear in the gathered metrics.
        """
        in_window = datetime.utcnow() - timedelta(days=1)
        in_window_ms = int(in_window.timestamp() * 1000)
        out_of_window = datetime.utcnow() - timedelta(days=30)
        out_of_window_ms = int(out_of_window.timestamp() * 1000)

        _seed_telemetry_session(
            test_session,
            channel_id="ch-in",
            channel_name="InWindow",
            session_id="conn-in-0",
            poll_count=5,
            base_observed_at_ms=in_window_ms,
        )
        _seed_telemetry_session(
            test_session,
            channel_id="ch-out",
            channel_name="OutOfWindow",
            session_id="conn-out-0",
            poll_count=5,
            base_observed_at_ms=out_of_window_ms,
        )
        test_session.commit()

        calc = PopularityCalculator(period_days=7)
        start_date = date.today() - timedelta(days=7)
        end_date = date.today() + timedelta(days=1)
        metrics = calc._gather_metrics(test_session, start_date, end_date)

        assert "ch-in" in metrics
        assert "ch-out" not in metrics

    def test_formula_collapses_concurrent_clients_in_watch_time(self, test_session):
        """Two concurrent clients on the same channel in the same poll
        cycle contribute only one poll interval to ``watch_time`` —
        matching the legacy ``_update_watch_time`` semantic and the
        view's DISTINCT-by-(channel, observed_at) collapse.

        Seed three rows with the same channel_id + observed_at but
        distinct session_ids (representing 3 concurrent clients). The
        gathered ``watch_time`` for that channel must be one poll
        interval (10s), not three.
        """
        anchor = datetime.utcnow() - timedelta(days=1)
        anchor_ms = int(anchor.timestamp() * 1000)
        poll_interval_ms = 10_000

        for s in range(3):
            test_session.add(
                SessionTelemetry(
                    session_id=f"conn-multi-{s}",
                    observed_at=anchor_ms,  # same observed_at — concurrent
                    user_id=None,
                    provider_id=None,
                    channel_id="ch-multi",
                    bytes_delta=1000,
                    buffer_event_count=0,
                    poll_interval_ms=poll_interval_ms,
                )
            )
        # Side-load channel_name source.
        test_session.add(
            UniqueClientConnection(
                ip_address="10.0.0.50",
                channel_id="ch-multi",
                channel_name="MultiClient",
                date=anchor.date(),
                connected_at=anchor,
                watch_seconds=10,
            )
        )
        test_session.commit()

        calc = PopularityCalculator()
        start_date = date.today() - timedelta(days=7)
        end_date = date.today() + timedelta(days=1)
        metrics = calc._gather_metrics(test_session, start_date, end_date)

        assert "ch-multi" in metrics
        # 3 concurrent clients × 10s/poll, but DISTINCT-by-observed_at
        # collapses to a single 10s contribution.
        assert metrics["ch-multi"]["watch_time"] == 10, (
            f"watch_time should collapse concurrent-client rows to one "
            f"poll interval; got {metrics['ch-multi']['watch_time']}. "
            f"This indicates the DISTINCT-by-(channel, observed_at) "
            f"collapse in _gather_metrics is broken — a channel with "
            f"N concurrent clients would silently inflate watch_time by Nx."
        )
        # watch_count counts DISTINCT session_id — 3 distinct sessions.
        assert metrics["ch-multi"]["watch_count"] == 3

    def test_calculate_all_writes_popularity_score_records(self, test_session):
        """End-to-end: ``calculate_all()`` writes ``ChannelPopularityScore``
        rows derived from session_telemetry data, including the side-loaded
        channel_name.
        """
        anchor = datetime.utcnow() - timedelta(days=1)
        anchor_ms = int(anchor.timestamp() * 1000)

        _seed_telemetry_session(
            test_session,
            channel_id="ch-one",
            channel_name="ChannelOne",
            session_id="conn-one-0",
            poll_count=10,
            base_observed_at_ms=anchor_ms,
        )
        test_session.commit()

        with patch("popularity_calculator.get_session", return_value=test_session):
            with patch(
                "popularity_calculator.get_current_date",
                return_value=date.today(),
            ):
                result = PopularityCalculator().calculate_all()

        assert result["channels_scored"] == 1
        score = (
            test_session.query(ChannelPopularityScore)
            .filter(ChannelPopularityScore.channel_id == "ch-one")
            .first()
        )
        assert score is not None
        assert score.channel_name == "ChannelOne"
        assert score.rank == 1
        # Locked-in watch_count semantic post step (d): distinct
        # session_id count, not state-transition count.
        assert score.watch_count_7d == 1
        assert score.watch_time_7d == 100  # 10 polls × 10s
