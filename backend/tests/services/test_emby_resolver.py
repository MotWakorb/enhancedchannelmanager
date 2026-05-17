"""Unit tests for :mod:`services.emby_resolver` (bd-6802c, epic bd-2cenq).

The resolver answers one question: given an ECM stream session (client
IP + stream name), is there exactly one matching Emby session, and if
so, which Emby user owns it?

Algorithm under test (per the bead):

1. If the ECM session's client IP does NOT match the configured Emby
   server's IP (extracted from ``settings.emby_base_url``), return
   ``None`` immediately — the stream is going somewhere other than the
   Emby server, so no Emby attribution is possible. Do NOT call the
   cache in this branch (it's pure waste).
2. Otherwise, fetch cached Emby sessions via
   :func:`emby_cache.get_cached_emby_sessions`.
3. Match each session's ``now_playing_item_name`` OR
   ``now_playing_channel_name`` against the ECM stream name:
   * exact case-insensitive first;
   * fall back to RapidFuzz ``token_set_ratio`` with the 0.85 threshold
     from the bead spec.
4. Zero matches → return ``None``.
5. Exactly one match → return ``EmbyAttribution(user_id, user_name)``.
6. Multiple matches → pick most-recent ``last_activity_date`` (ISO
   timestamps lexicographic-compare for the standard Emby format).

Test isolation note: the resolver caches DNS resolution results
module-level so a single process does not thrash DNS for every poll.
:func:`emby_resolver._reset_for_tests` clears that cache between
tests; the ``reset_resolver_state`` fixture below runs it before AND
after every test so a failing test cannot poison the next one.
"""
from __future__ import annotations

import logging
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import observability
from emby_client import EmbySession
from services import emby_resolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    *,
    user_id: str = "uid-alice",
    user_name: str = "alice",
    item_name: str | None = "CNN HD",
    channel_name: str | None = None,
    channel_number: str | None = None,
    last_activity: str | None = "2026-05-16T12:00:00Z",
) -> EmbySession:
    """Build a representative :class:`EmbySession` for assertions.

    Defaults match the most common test case (a live-TV session
    playing "CNN HD"). Callers override one or two fields per test to
    keep the table-readable.
    """
    return EmbySession(
        session_id=f"sess-{user_name}",
        user_id=user_id,
        user_name=user_name,
        remote_endpoint="10.0.0.99",
        now_playing_item_name=item_name,
        now_playing_channel_name=channel_name,
        last_activity_date=last_activity,
        channel_number=channel_number,
    )


def _enabled_settings(base_url: str = "http://192.168.1.10:8096") -> MagicMock:
    """Settings stub representing a fully-configured, enabled Emby.

    Default base URL is an IP literal so tests of the resolver's
    happy-path do not need to mock ``socket.gethostbyname``. Hostname
    cases override ``base_url`` explicitly.
    """
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = base_url
    settings.emby_api_key = "key-123"
    return settings


@pytest.fixture(autouse=True)
def reset_resolver_state():
    """Reset the module-level DNS cache around every test.

    Without this, a hostname-resolution result from one test would
    persist into the next and produce confusing false matches. Runs
    both pre- and post-test so a failing assertion cannot leak state.
    """
    emby_resolver._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    emby_resolver._reset_for_tests()
    observability.reset_for_tests()


def _get_counter_value(metric_key: str, source: str) -> float:
    """Return the current value of a labeled counter from the live registry.

    ``metric_key`` is the key in observability._METRICS (e.g.
    ``"user_attribution_resolved_total"``). The prometheus metric name is
    ``"ecm_" + metric_key``.
    """
    metric = observability.get_metric(metric_key)
    prom_name = f"ecm_{metric_key}"
    for mf in metric.collect():
        for sample in mf.samples:
            if sample.name == prom_name and sample.labels.get("source") == source:
                return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Behavior: IP mismatch short-circuit (no cache call)
# ---------------------------------------------------------------------------


class TestIpMismatch:
    """When the ECM session's IP is not the Emby server's IP, the resolver
    must return ``None`` without ever touching the cache — every poll
    cycle hits this path for every non-Emby session, so the short-circuit
    is the load-bearing optimization."""

    async def test_ip_mismatch_returns_none_without_cache_call(self):
        """IP mismatch on an IP-literal base URL skips the cache entirely."""
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions", cache_mock):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="10.0.0.5",  # NOT the Emby server (192.168.1.10)
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Behavior: exact case-insensitive match
# ---------------------------------------------------------------------------


class TestExactMatch:
    """Exact (case-insensitive) match between the ECM stream name and an
    Emby session's now-playing item or channel returns the corresponding
    attribution."""

    async def test_exact_match_returns_attribution(self):
        """Exact match on item name returns the session's user attribution."""
        session = _make_session(user_id="uid-bob", user_name="bob", item_name="CNN HD")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result == emby_resolver.EmbyAttribution(user_id="uid-bob", user_name="bob")
        # Metric assertion: resolved counter incremented; unresolved is zero.
        assert _get_counter_value("user_attribution_resolved_total", "emby") == 1.0
        assert _get_counter_value("user_attribution_unresolved_total", "emby") == 0.0

    async def test_case_insensitive_match(self):
        """Case differences do not prevent an exact match — "cnn" matches "CNN"."""
        session = _make_session(item_name="CNN")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="cnn",
            )
        assert result is not None
        assert result.user_name == "alice"

    async def test_channel_name_match_when_item_name_none(self):
        """Live-TV sessions have a channel name; the resolver must match on
        either ``now_playing_item_name`` or ``now_playing_channel_name``.
        An idle ``item_name`` with a populated ``channel_name`` still
        attributes."""
        session = _make_session(
            user_id="uid-carol", user_name="carol",
            item_name=None, channel_name="ESPN HD",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="ESPN HD",
            )
        assert result == emby_resolver.EmbyAttribution(user_id="uid-carol", user_name="carol")


# ---------------------------------------------------------------------------
# Behavior: fuzzy match (token-set ratio ≥ 0.85)
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    """When exact match fails, fall back to RapidFuzz
    ``token_set_ratio`` with the 0.85 threshold from the bead spec."""

    async def test_fuzzy_match_above_threshold_returns_attribution(self):
        """Stream name "CNN HD" vs Emby item "CNN HD 1080p" — token-set
        ratio is well above 0.85, so the resolver returns the
        attribution."""
        session = _make_session(user_name="dan", item_name="CNN HD 1080p")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is not None
        assert result.user_name == "dan"

    async def test_fuzzy_match_below_threshold_returns_none(self):
        """Stream name vs unrelated item name scores well below the
        0.85 floor — no match, ``None`` returned."""
        session = _make_session(item_name="The Office Season 3 Episode 14")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        # IP matched but no session matched — unresolved counter incremented.
        assert _get_counter_value("user_attribution_unresolved_total", "emby") == 1.0
        assert _get_counter_value("user_attribution_resolved_total", "emby") == 0.0


# ---------------------------------------------------------------------------
# Behavior: no-match conditions
# ---------------------------------------------------------------------------


class TestNoMatch:
    """The resolver returns ``None`` whenever the cache is empty, every
    session is below the fuzzy threshold, or every session is idle (no
    playing item/channel)."""

    async def test_empty_cache_returns_none(self):
        """An empty cache (Emby idle, or disabled — cache returns [])
        produces no match."""
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None

    async def test_all_sessions_idle_returns_none(self):
        """Sessions with both item_name and channel_name set to ``None``
        (idle connected clients) cannot match anything."""
        idle = _make_session(item_name=None, channel_name=None)
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[idle, idle])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: tie-break on multiple matches by most-recent last_activity_date
# ---------------------------------------------------------------------------


class TestMultipleMatchTiebreak:
    """When multiple Emby sessions match the same ECM stream (rare —
    same channel on multiple Emby clients), pick the one whose
    ``last_activity_date`` is most recent. ISO timestamps with a fixed
    format are lexicographically comparable, so plain string compare is
    sufficient."""

    async def test_picks_most_recent_last_activity(self):
        """Two sessions playing the same channel; the one with the newer
        ``last_activity_date`` wins."""
        older = _make_session(
            user_id="uid-old", user_name="old_viewer",
            item_name="CNN HD",
            last_activity="2026-05-16T10:00:00Z",
        )
        newer = _make_session(
            user_id="uid-new", user_name="new_viewer",
            item_name="CNN HD",
            last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[older, newer])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result == emby_resolver.EmbyAttribution(
            user_id="uid-new", user_name="new_viewer",
        )

    async def test_null_last_activity_loses_to_populated(self):
        """A session with ``last_activity_date is None`` should never beat
        a session with an actual timestamp — defensive against partial
        Emby payloads."""
        no_ts = _make_session(
            user_id="uid-null", user_name="null_viewer",
            item_name="CNN HD",
            last_activity=None,
        )
        with_ts = _make_session(
            user_id="uid-ts", user_name="ts_viewer",
            item_name="CNN HD",
            last_activity="2026-05-16T10:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[no_ts, with_ts])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is not None
        assert result.user_name == "ts_viewer"


# ---------------------------------------------------------------------------
# Behavior: disabled Emby (cache returns []) returns None
# ---------------------------------------------------------------------------


class TestDisabledEmby:
    """When Emby is disabled the cache returns ``[]`` (per bd-gpeot's
    settings gate). The resolver's empty-cache branch handles this
    transparently — no explicit disabled-check needed."""

    async def test_disabled_emby_cache_empty_returns_none(self):
        """``get_cached_emby_sessions`` returns [] for disabled Emby; the
        resolver's no-match branch returns ``None``."""
        # NOTE: we still need to mock get_settings so the IP-extract step
        # works — the resolver doesn't check ``emby_enabled`` itself.
        # With base_url matching the ECM session IP, the resolver gets as
        # far as the cache call and then hits the empty-list branch.
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: hostname-based base URL DNS resolution
# ---------------------------------------------------------------------------


class TestHostnameBaseUrl:
    """When ``emby_base_url`` uses a hostname rather than an IP literal,
    the resolver falls back to ``socket.gethostbyname`` to derive the
    server IP for comparison."""

    async def test_hostname_resolves_to_matching_ip_attributes(self):
        """``emby.local`` resolves to ``192.168.1.10`` — matches the ECM
        session IP and the resolver proceeds to look up the session."""
        session = _make_session(user_name="eve", item_name="CNN HD")
        settings = _enabled_settings(base_url="https://emby.local:8920")

        with patch.object(emby_resolver, "get_settings", return_value=settings), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(emby_resolver.socket, "gethostbyname",
                          return_value="192.168.1.10"):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is not None
        assert result.user_name == "eve"

    async def test_hostname_resolves_to_non_matching_ip_returns_none(self):
        """``emby.local`` resolves to ``10.0.0.1`` — does NOT match the
        ECM session IP and the resolver short-circuits before the cache."""
        cache_mock = AsyncMock(return_value=[_make_session(item_name="CNN HD")])
        settings = _enabled_settings(base_url="https://emby.local:8920")

        with patch.object(emby_resolver, "get_settings", return_value=settings), \
             patch.object(emby_resolver, "get_cached_emby_sessions", cache_mock), \
             patch.object(emby_resolver.socket, "gethostbyname",
                          return_value="10.0.0.1"):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_hostname_resolution_failure_logs_warn_and_returns_none(self, caplog):
        """``socket.gethostbyname`` raises ``socket.gaierror`` for an
        unresolvable hostname; the resolver logs at WARN with the
        ``[EMBY]`` prefix and returns ``None`` gracefully (does not
        propagate the DNS error to the BandwidthTracker poll loop)."""
        settings = _enabled_settings(base_url="https://does-not-exist.invalid:8920")

        with patch.object(emby_resolver, "get_settings", return_value=settings), \
             patch.object(emby_resolver, "get_cached_emby_sessions", AsyncMock()) as cache_mock, \
             patch.object(emby_resolver.socket, "gethostbyname",
                          side_effect=socket.gaierror("Name resolution failed")):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                result = await emby_resolver.resolve_emby_user(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN HD",
                )
        assert result is None
        cache_mock.assert_not_awaited()
        # WARN is logged once with [EMBY] prefix
        assert any(
            "[EMBY]" in rec.getMessage() and "does-not-exist.invalid" in rec.getMessage()
            for rec in caplog.records
        )

    async def test_hostname_resolution_result_is_cached(self):
        """``socket.gethostbyname`` should be called at most once per
        process — the resolver caches the resolution so repeated polls
        do not thrash DNS."""
        session = _make_session(item_name="CNN HD")
        settings = _enabled_settings(base_url="https://emby.local:8920")

        with patch.object(emby_resolver, "get_settings", return_value=settings), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(emby_resolver.socket, "gethostbyname",
                          return_value="192.168.1.10") as gethost:
            await emby_resolver.resolve_emby_user("192.168.1.10", "CNN HD")
            await emby_resolver.resolve_emby_user("192.168.1.10", "CNN HD")
            await emby_resolver.resolve_emby_user("192.168.1.10", "CNN HD")
        assert gethost.call_count == 1


# ---------------------------------------------------------------------------
# Behavior (bd-zldrq fix-forward for v0.17.1-0033): channel-name primary
# match — Emby's live-TV item.Name is "<channel_number> | <channel_name>"
# (e.g. "408 | ESPN"), and Dispatcharr stream names like "US: ESPN FHD" do
# NOT fuzzy-match it above the 0.85 floor. The resolver now accepts
# ``ecm_channel_name`` and ``ecm_channel_number`` and tries three tiers
# before the legacy fuzzy stream-name fallback.
# ---------------------------------------------------------------------------


class TestChannelNamePrimaryMatch:
    """Tier 1: parse Emby item.Name as ``"<number> | <name>"`` and match
    the right-hand part against ``ecm_channel_name`` case-insensitively.
    This is the load-bearing fix for v0.17.1-0033 — operators watching
    live TV via Emby now resolve to the right user even when the
    Dispatcharr stream name is provider-prefixed verbose.
    """

    async def test_channel_name_matches_pipe_suffix(self):
        """Emby item.Name "408 | ESPN" matches ecm_channel_name "ESPN"
        (the exact live-test scenario)."""
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="408 | ESPN", channel_name=None, channel_number="408",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result == emby_resolver.EmbyAttribution(
            user_id="uid-mw", user_name="MotWakorb",
        )

    async def test_channel_name_matches_whole_name_without_prefix(self):
        """Some Emby installs may surface live-TV item.Name as just the
        channel name with no "<number> | " prefix. Tier 1 matches the
        whole string as well."""
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="ESPN", channel_number="408",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_channel_name_match_is_case_insensitive(self):
        """``espn`` matches ``"408 | ESPN"`` — both halves are
        normalized (NFC + lowercase + strip) before comparison."""
        session = _make_session(
            user_name="case_user",
            item_name="408 | ESPN", channel_number="408",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="ignored — tier 1 wins first",
                ecm_channel_name="espn",
                ecm_channel_number=408,
            )
        assert result is not None
        assert result.user_name == "case_user"


class TestChannelNumberMatch:
    """Tier 2: when both ecm_channel_number and the Emby session's
    channel_number are present, string-compare wins regardless of the
    item.Name's textual form. Defensive against installs where the
    channel name diverges between ECM and Emby (e.g., operator renamed
    one side without renaming the other)."""

    async def test_channel_number_string_match(self):
        """ecm_channel_number=408 matches Emby channel_number="408"
        (string compare). The item.Name has nothing in common with the
        ECM stream name, so the only path that can match is tier 2."""
        session = _make_session(
            user_id="uid-num", user_name="num_user",
            item_name="Some unrelated text", channel_number="408",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="Sports Channel A",  # won't tier-1 match
                ecm_channel_number=408,
            )
        assert result == emby_resolver.EmbyAttribution(
            user_id="uid-num", user_name="num_user",
        )

    async def test_channel_number_missing_on_session_skips_tier(self):
        """When the Emby session has ``channel_number=None`` (VOD or
        idle), tier 2 cannot match — the resolver should fall through
        to tier 3 (fuzzy stream_name)."""
        session = _make_session(
            user_id="uid-vod", user_name="vod_user",
            item_name="The Matrix",  # no channel_number → tier 2 skip
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        # No tier matches — Matrix is unrelated.
        assert result is None


class TestFuzzyStreamNameFallback:
    """Tier 3: the legacy RapidFuzz path on ``ecm_stream_name`` against
    Emby item.Name OR channel_name. Still the right behavior for
    non-live-TV Emby content (movies, episodes) where channel_name /
    channel_number are both ``None``."""

    async def test_fuzzy_match_on_movie_when_no_channel_args(self):
        """Movie session: no channel_name/channel_number on either side
        and the stream name fuzzy-matches the movie title above 0.85."""
        session = _make_session(
            user_name="movie_viewer",
            item_name="The Matrix 1999",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="The Matrix",
                # ecm_channel_name and ecm_channel_number deliberately
                # omitted — back-compat call shape.
            )
        assert result is not None
        assert result.user_name == "movie_viewer"

    async def test_fuzzy_match_with_channel_args_still_falls_back(self):
        """Even when channel args are passed, if neither tier 1 nor
        tier 2 matches, the resolver should still try the fuzzy
        stream_name fallback."""
        session = _make_session(
            user_name="fb_user",
            item_name="The Matrix 1999",  # not "ESPN"
            channel_number=None,            # not 408
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="The Matrix",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result is not None
        assert result.user_name == "fb_user"


class TestMultiTierTiebreak:
    """When multiple sessions match across tiers, the same most-recent
    last_activity_date tie-break applies."""

    async def test_two_channel_name_matches_pick_most_recent(self):
        """Two Emby sessions both playing the same channel; the one
        with the newer last_activity_date wins."""
        older = _make_session(
            user_id="uid-old", user_name="old_user",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T10:00:00Z",
        )
        newer = _make_session(
            user_id="uid-new", user_name="new_user",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[older, newer])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result == emby_resolver.EmbyAttribution(
            user_id="uid-new", user_name="new_user",
        )

    async def test_debug_log_fires_when_multiple_candidates(self, caplog):
        """When N > 1 candidates match, the resolver must log a DEBUG
        line surfacing the disambiguation so operators can see the
        tie-break in trace."""
        older = _make_session(
            user_id="uid-old", user_name="old_user",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T10:00:00Z",
        )
        newer = _make_session(
            user_id="uid-new", user_name="new_user",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[older, newer])):
            with caplog.at_level(logging.DEBUG, logger="services.emby_resolver"):
                await emby_resolver.resolve_emby_user(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="US: ESPN FHD",
                    ecm_channel_name="ESPN",
                    ecm_channel_number=408,
                )
        # Look for the disambiguation DEBUG line — content per the spec
        # mentions candidate count, ip, name, and picked user.
        assert any(
            "[EMBY]" in rec.getMessage()
            and "resolver" in rec.getMessage()
            and "candidates" in rec.getMessage()
            and "new_user" in rec.getMessage()
            for rec in caplog.records
        ), f"expected disambiguation DEBUG; got {[r.getMessage() for r in caplog.records]}"


class TestBackCompatStreamNameOnlyCallShape:
    """Existing callers (and the existing test suite above) invoke
    ``resolve_emby_user(ip, name)`` with no channel args — the new
    signature must keep that call shape working for back-compat."""

    async def test_two_positional_args_still_works(self):
        """Pre-fix callers pass only (ip, stream_name) — must still
        produce the same fuzzy-match result as before bd-zldrq."""
        session = _make_session(user_name="alice", item_name="CNN HD")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                "192.168.1.10", "CNN HD",
            )
        assert result is not None
        assert result.user_name == "alice"


# ---------------------------------------------------------------------------
# Behavior: empty / malformed configuration
# ---------------------------------------------------------------------------


class TestEmptyOrMalformedConfig:
    """Defensive coverage for misconfiguration the cache layer's
    settings-gate would normally absorb. The resolver should fail safe
    (return ``None``) rather than raise."""

    async def test_empty_base_url_returns_none(self):
        """Empty ``emby_base_url`` cannot extract a hostname — defensive
        return ``None``."""
        settings = _enabled_settings(base_url="")
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(emby_resolver, "get_settings", return_value=settings), \
             patch.object(emby_resolver, "get_cached_emby_sessions", cache_mock):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_malformed_base_url_returns_none(self):
        """A base URL that ``urllib.parse`` cannot extract a hostname
        from (e.g. only a scheme) returns ``None`` without raising."""
        settings = _enabled_settings(base_url="http://")
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(emby_resolver, "get_settings", return_value=settings), \
             patch.object(emby_resolver, "get_cached_emby_sessions", cache_mock):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# bd-r5f0c.9 multi-viewer: resolve_emby_users returns the FULL list of
# matched sessions sorted most-recent first, NOT just the tiebreak winner.
# Fixes the PO-reported bug where ECM showed only one user when multiple
# Emby viewers were watching the same channel.
# ---------------------------------------------------------------------------


class TestMultiViewer:
    """Plural :func:`resolve_emby_users` captures every viewer that matched
    any tier. The singular wrapper :func:`resolve_emby_user` continues
    to return the most-recent viewer for back-compat.
    """

    async def test_two_viewers_same_channel_returns_both(self):
        """Two Emby sessions on the same channel → resolve_emby_users
        returns BOTH attributions, most-recent first. The legacy
        singular wrapper returns only position 0 (back-compat)."""
        older = _make_session(
            user_id="uid-alice", user_name="alice",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T10:00:00Z",
        )
        newer = _make_session(
            user_id="uid-bob", user_name="bob",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[older, newer])):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert len(users) == 2, f"expected 2 viewers; got {len(users)}"
        # Position 0 is the most-recent viewer.
        assert users[0].user_id == "uid-bob"
        assert users[0].user_name == "bob"
        assert users[1].user_id == "uid-alice"
        assert users[1].user_name == "alice"

    async def test_three_viewers_same_channel_returns_all_three(self):
        """Three Emby sessions on the same channel — list of 3 sorted
        by recency descending."""
        s1 = _make_session(
            user_id="u1", user_name="alice",
            item_name="408 | ESPN", last_activity="2026-05-16T10:00:00Z",
        )
        s2 = _make_session(
            user_id="u2", user_name="bob",
            item_name="408 | ESPN", last_activity="2026-05-16T12:00:00Z",
        )
        s3 = _make_session(
            user_id="u3", user_name="charlie",
            item_name="408 | ESPN", last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[s1, s2, s3])):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="ESPN",
                ecm_channel_name="ESPN",
            )
        assert [u.user_name for u in users] == ["charlie", "bob", "alice"]

    async def test_single_viewer_returns_one_element_list(self):
        """The common case: one viewer on the channel → list of 1."""
        only = _make_session(
            user_id="uid-only", user_name="solo",
            item_name="CNN HD",
            last_activity="2026-05-16T12:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[only])):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert len(users) == 1
        assert users[0].user_name == "solo"

    async def test_no_match_returns_empty_list(self):
        """No tier matches → empty list (NOT None, NOT exception)."""
        irrelevant = _make_session(
            user_id="u", user_name="u", item_name="Unrelated Show",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[irrelevant])):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert users == []

    async def test_ip_mismatch_returns_empty_list_without_cache_call(self):
        """IP short-circuit also applies to the plural variant."""
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions", cache_mock):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="10.0.0.5",  # NOT the Emby server
                ecm_stream_name="CNN HD",
            )
        assert users == []
        cache_mock.assert_not_awaited()

    async def test_singular_wrapper_returns_most_recent_viewer(self):
        """Back-compat target: the legacy singular wrapper still returns
        the most-recent viewer (position 0 of the plural list)."""
        older = _make_session(
            user_id="uid-old", user_name="old_viewer",
            item_name="CNN HD",
            last_activity="2026-05-16T10:00:00Z",
        )
        newer = _make_session(
            user_id="uid-new", user_name="new_viewer",
            item_name="CNN HD",
            last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[older, newer])):
            singular = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN HD",
            )
        assert singular is not None
        assert singular.user_name == "new_viewer"

    async def test_tier_2_match_contributes_to_list(self):
        """A viewer matched via Tier-2 (channel_number) is included in
        the list alongside Tier-1 matches — multi-tier matches are
        unioned and de-duped, not Tier-1-only."""
        # Tier-1 match: item_name suffix == ecm_channel_name
        t1 = _make_session(
            user_id="t1", user_name="t1_user",
            item_name="408 | ESPN", channel_number="408",
            last_activity="2026-05-16T14:00:00Z",
        )
        # Tier-2 match: channel_number matches but item_name does not
        t2 = _make_session(
            user_id="t2", user_name="t2_user",
            item_name="Some Other Title", channel_number="408",
            last_activity="2026-05-16T12:00:00Z",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[t1, t2])):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        names = {u.user_name for u in users}
        assert names == {"t1_user", "t2_user"}, (
            f"expected both Tier-1 and Tier-2 matches in viewer list; "
            f"got {names}"
        )

    async def test_metric_counter_increments_per_viewer(self):
        """bd-r5f0c.9 + W6 semantic: the
        ``user_attribution_resolved_total{source="emby"}`` counter
        increments PER VIEWER. 3 matched viewers = 3 increments."""
        s1 = _make_session(user_id="u1", user_name="a", item_name="CNN")
        s2 = _make_session(user_id="u2", user_name="b", item_name="CNN")
        s3 = _make_session(user_id="u3", user_name="c", item_name="CNN")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[s1, s2, s3])):
            users = await emby_resolver.resolve_emby_users(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="CNN",
            )
        assert len(users) == 3
        assert _get_counter_value("user_attribution_resolved_total", "emby") == 3.0


# ---------------------------------------------------------------------------
# bd-r5f0c.10: forensic no-match WARN. When the resolver has the
# preconditions for a match (IP short-circuit passed + sessions present)
# but no tier accepts any session, emit one WARN per (ip, channel) per
# 60 s carrying enough raw + normalized data to see why the tiers missed.
# Suppressed cases: empty cache, IP mismatch, successful match.
# ---------------------------------------------------------------------------


class TestNoMatchDiagnostic:
    """The forensic WARN fires once per (ip, channel) per 60 s when the
    resolver entered the matching path with non-empty sessions but no
    tier produced a match. All other cases stay silent."""

    @staticmethod
    def _no_match_records(records):
        """Filter caplog records to just our no-match diagnostic lines."""
        return [
            r for r in records
            if "[EMBY-RESOLVER] no-match diagnostic" in r.getMessage()
        ]

    async def test_emits_warn_when_sessions_exist_and_no_tier_matches(self, caplog):
        """Sessions in cache but no tier accepts any of them → one WARN."""
        session = _make_session(
            user_name="alice",
            item_name="999 | NotTheChannel",
            channel_name="NotTheChannel",
            channel_number="999",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                users = await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="US: CNN FHD",
                    ecm_channel_name="CNN",
                    ecm_channel_number=200,
                )
        assert users == []
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1, (
            f"expected exactly one no-match WARN; got {len(warns)}: "
            f"{[r.getMessage() for r in warns]}"
        )
        msg = warns[0].getMessage()
        # Channel + session info must be present for forensic value.
        assert "CNN" in msg
        assert "NotTheChannel" in msg

    async def test_no_emit_when_sessions_empty(self, caplog):
        """Empty session cache (idle Emby) is the normal state — no WARN."""
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                users = await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        assert users == []
        assert self._no_match_records(caplog.records) == []

    async def test_no_emit_when_match_succeeds(self, caplog):
        """A successful resolve must not emit the no-match WARN."""
        session = _make_session(user_name="alice", item_name="CNN HD")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                users = await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN HD",
                )
        assert len(users) == 1
        assert self._no_match_records(caplog.records) == []

    async def test_no_emit_when_ip_short_circuit_fails(self, caplog):
        """IP mismatch short-circuits before the session compare → no WARN."""
        session = _make_session(
            item_name="NotTheChannel", channel_number="999",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                users = await emby_resolver.resolve_emby_users(
                    ecm_session_ip="10.0.0.99",  # NOT the Emby server
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        assert users == []
        assert self._no_match_records(caplog.records) == []

    async def test_rate_limit_within_window(self, caplog):
        """Two no-match calls in quick succession for the same (ip, channel)
        → one WARN only. The second call should be suppressed by the
        60 s rate-limit."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
                await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1, (
            f"rate-limit broken: expected 1 WARN, got {len(warns)}"
        )

    async def test_rate_limit_per_channel_independence(self, caplog):
        """Two different problem channels for the same IP each get their
        own WARN — the rate-limit is keyed by (ip, channel), not by ip
        alone. This is the load-bearing assertion: one noisy channel
        must NOT silence diagnosis for a sibling channel."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CBS",
                    ecm_channel_name="CBS",
                )
                await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 2, (
            f"expected one WARN per channel; got {len(warns)}"
        )
        messages = " ".join(r.getMessage() for r in warns)
        assert "CBS" in messages and "CNN" in messages

    async def test_rate_limit_window_expiry(self, caplog, monkeypatch):
        """Advance the monotonic clock past 60 s and a second call for
        the same (ip, channel) emits a fresh WARN."""
        session = _make_session(item_name="UnrelatedShow")
        # Step the clock manually so we don't sleep in tests.
        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr(emby_resolver.time, "monotonic", fake_monotonic)

        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
                # Bump clock past the 60 s window.
                fake_now[0] += 61.0
                await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 2, (
            f"window-expiry broken: expected 2 WARNs, got {len(warns)}"
        )

    async def test_truncation_cap_thirty(self, caplog):
        """bd-r5f0c.11: 35 sessions in cache → forensic payload includes
        exactly 30 entries (the bumped cap), not the original 10 and
        not the full 35. The cap protects log size on operators with
        very large session counts while still capturing enough breadth
        for diagnosis.
        """
        sessions = [
            _make_session(
                user_id=f"uid-{idx:02d}",
                user_name=f"user{idx:02d}",
                item_name=f"{900 + idx} | NotTheChannel{idx}",
                channel_number=str(900 + idx),
            )
            for idx in range(35)
        ]
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=sessions)):
            with caplog.at_level(logging.WARNING, logger="services.emby_resolver"):
                users = await emby_resolver.resolve_emby_users(
                    ecm_session_ip="192.168.1.10",
                    ecm_stream_name="US: CNN FHD",
                    ecm_channel_name="CNN",
                    ecm_channel_number=200,
                )
        assert users == []
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1
        # Count distinct session_id markers in the payload. Each entry
        # carries an 8-char session_id prefix derived from "sess-userNN".
        msg = warns[0].getMessage()
        # The payload renders dicts with "'session_id':" — count occurrences.
        assert msg.count("'session_id':") == 30, (
            f"expected truncation cap of 30 session entries, "
            f"got {msg.count(chr(39) + 'session_id' + chr(39) + ':')}"
        )
        # The header sessions_count reports the full 35 (untruncated).
        assert "sessions_count=35" in msg


# ---------------------------------------------------------------------------
# Behavior: bd-r5f0c.11 — ECM-side pipe-prefix tolerance in Tier-1
# ---------------------------------------------------------------------------


class TestECMPipePrefixTolerance:
    """bd-r5f0c.11: when an operator imports channels with the Emby/M3U
    pipe-prefix display format leaked into the ECM channel_name itself
    (e.g. "109 | CNN"), tier-1 must match against Emby sessions even
    though ECM is carrying the upstream's display format. The fix adds
    two compares against the parsed ECM suffix (alongside the existing
    compares against ecm_channel_name as a whole), gated on the ECM
    name actually containing a pipe so clean ECM names are unaffected.
    """

    async def test_ecm_prefix_emby_prefix(self):
        """ECM channel_name "109 | CNN" vs Emby item_name "109 | CNN".
        Existing tier-1 (ecm_full vs item_name) would have matched this
        already; the new compares (ecm_suffix vs pipe_suffix) also
        succeed. Either path is acceptable — the assertion is that the
        session resolves.
        """
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="109 | CNN", channel_number="109",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="109 | CNN",
                ecm_channel_number=None,
            )
        assert result == emby_resolver.EmbyAttribution(
            user_id="uid-mw", user_name="MotWakorb",
        )

    async def test_ecm_prefix_emby_clean(self):
        """ECM channel_name "109 | CNN" vs Emby item_name "CNN" (no
        pipe prefix on the Emby side). This is the case neither existing
        compare can reach — only the new compare (ecm_suffix vs whole
        item_name) succeeds.
        """
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="CNN",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="109 | CNN",
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_clean_emby_prefix(self):
        """ECM channel_name "CNN" (clean) vs Emby item_name "109 | CNN".
        Regression check — this is the original CBS-style case that
        worked before bd-r5f0c.11 (matched via existing ecm_full vs
        pipe_suffix) and must continue to work after.
        """
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="109 | CNN", channel_number="109",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="CNN",
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_clean_emby_clean(self):
        """ECM channel_name "CNN" vs Emby item_name "CNN". The trivial
        whole-string match path (existing) — regression check that the
        new compares don't perturb it.
        """
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="CNN",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="CNN",
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_prefix_no_match(self):
        """ECM channel_name "109 | CNN" vs Emby item_name "NESN".
        Tolerance must NOT cause false positives — the ECM suffix
        "cnn" does not equal "nesn", no tier matches. The stream_name
        is unrelated so tier-3 fuzzy also cannot rescue.
        """
        session = _make_session(
            user_name="alice",
            item_name="NESN",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="totally unrelated stream name",
                ecm_channel_name="109 | CNN",
            )
        assert result is None

    async def test_ecm_prefix_case_insensitive(self):
        """ECM channel_name "109 | cnn" (lowercased prefix form) vs
        Emby item_name "109 | CNN". Normalization (NFC + lower + strip)
        applies to both sides before the new compares fire.
        """
        session = _make_session(
            user_name="case_user",
            item_name="109 | CNN",
        )
        with patch.object(emby_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_resolver, "get_cached_emby_sessions",
                          AsyncMock(return_value=[session])):
            result = await emby_resolver.resolve_emby_user(
                ecm_session_ip="192.168.1.10",
                ecm_stream_name="ignored — tier 1 wins",
                ecm_channel_name="109 | cnn",
            )
        assert result is not None
        assert result.user_name == "case_user"
