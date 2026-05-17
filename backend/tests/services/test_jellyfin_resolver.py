"""Unit tests for :mod:`services.jellyfin_resolver` (bd-r5f0c.3, epic bd-r5f0c).

The resolver answers one question: given an ECM stream session (client
IP + stream name), is there exactly one matching Jellyfin session, and if
so, which Jellyfin user owns it?

Algorithm under test:

1. If the ECM session's client IP does NOT match the configured Jellyfin
   server's IP (extracted from ``settings.jellyfin_base_url``), return
   ``None`` immediately — no Jellyfin attribution possible.
2. Otherwise, fetch cached Jellyfin sessions via
   :func:`jellyfin_cache.get_cached_jellyfin_sessions`.
3. Tiered match:
   * Tier 1: channel_name match. Parse item_name as "<number> | <name>"
     OR treat whole string as channel name (Jellyfin no-pipe tolerance).
   * Tier 2: channel_number exact string match.
   * Tier 3: RapidFuzz token_set_ratio >= 0.85 on stream_name.
4. Zero matches → return ``None``.
5. Exactly one match → return ``JellyfinAttribution(user_id, user_name)``.
6. Multiple matches → pick most-recent ``last_activity_date``.

Jellyfin-specific test:
- A session whose ``NowPlayingItem.Name`` is just ``"ESPN"`` (no pipe prefix)
  must STILL be matched by Tier-1 when ``ecm_channel_name="ESPN"``.

Test isolation note: the resolver caches DNS resolution results
module-level so a single process does not thrash DNS for every poll.
:func:`jellyfin_resolver._reset_for_tests` clears that cache between tests.
"""
from __future__ import annotations

import logging
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import observability
from jellyfin_client import JellyfinSession
from services import jellyfin_resolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    *,
    user_id: str = "jf-uid-alice",
    user_name: str = "alice",
    item_name: str | None = "CNN HD",
    channel_name: str | None = None,
    channel_number: str | None = None,
    last_activity: str | None = "2026-05-17T12:00:00Z",
) -> JellyfinSession:
    """Build a representative :class:`JellyfinSession` for assertions."""
    return JellyfinSession(
        session_id=f"jf-sess-{user_name}",
        user_id=user_id,
        user_name=user_name,
        remote_endpoint="10.0.0.99",
        now_playing_item_name=item_name,
        now_playing_channel_name=channel_name,
        last_activity_date=last_activity,
        channel_number=channel_number,
    )


def _enabled_settings(base_url: str = "http://192.168.1.20:8096") -> MagicMock:
    """Settings stub representing a fully-configured, enabled Jellyfin.

    Default base URL is an IP literal so tests of the resolver's happy-path
    do not need to mock ``socket.gethostbyname``.
    """
    settings = MagicMock()
    settings.jellyfin_enabled = True
    settings.jellyfin_base_url = base_url
    settings.jellyfin_api_key = "jf-key-123"
    return settings


@pytest.fixture(autouse=True)
def reset_resolver_state():
    """Reset the module-level DNS cache and warn timestamp around every test."""
    jellyfin_resolver._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    jellyfin_resolver._reset_for_tests()
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
    """When the ECM session's IP is not the Jellyfin server's IP, the resolver
    must return ``None`` without ever touching the cache."""

    async def test_ip_mismatch_returns_none_without_cache_call(self):
        """IP mismatch on an IP-literal base URL skips the cache entirely."""
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="10.0.0.5",  # NOT the Jellyfin server (192.168.1.20)
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Behavior: Jellyfin no-pipe-suffix tolerance (THE key Jellyfin-specific test)
# ---------------------------------------------------------------------------


class TestNoPipeSuffixTolerance:
    """Jellyfin live-TV sessions often surface ``NowPlayingItem.Name`` as
    just the channel name (e.g. ``"ESPN"``) without the ``"<number> | "``
    pipe prefix that Emby uses. The resolver must handle BOTH formats.

    This is the primary behavioral divergence from emby_resolver —
    a dedicated test class ensures it is never accidentally regressed.
    """

    async def test_bare_channel_name_matches_ecm_channel_name(self):
        """Jellyfin item.Name="ESPN" (no pipe) matches ecm_channel_name="ESPN"
        via Tier-1 whole-string equality. This is the Jellyfin-specific case
        that proves the no-pipe tolerance is working."""
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="ESPN",  # bare — Jellyfin style
            channel_number="408",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",  # wouldn't fuzzy-match "ESPN" at 0.85
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-mw", user_name="MotWakorb",
        )
        # Metric assertion: resolved counter incremented; unresolved is zero.
        assert _get_counter_value("user_attribution_resolved_total", "jellyfin") == 1.0
        assert _get_counter_value("user_attribution_unresolved_total", "jellyfin") == 0.0

    async def test_pipe_format_also_works(self):
        """Jellyfin installs that DO use the pipe format (e.g. Emby-migrated
        servers) should also match correctly via Tier-1 pipe-suffix path."""
        session = _make_session(
            user_name="pipe_user",
            item_name="408 | ESPN",  # pipe format — also valid for some Jellyfin installs
            channel_number="408",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result is not None
        assert result.user_name == "pipe_user"

    async def test_bare_name_match_is_case_insensitive(self):
        """Bare channel name match is case-insensitive: "espn" matches "ESPN"."""
        session = _make_session(
            user_name="case_user",
            item_name="ESPN",
            channel_number=None,
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="irrelevant",
                ecm_channel_name="espn",
            )
        assert result is not None
        assert result.user_name == "case_user"

    async def test_bare_name_no_false_positive_with_unrelated_channel(self):
        """A Jellyfin item.Name of "ESPN" must NOT match ecm_channel_name="CNN"."""
        session = _make_session(
            user_name="wrong_user",
            item_name="ESPN",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
                ecm_channel_name="CNN",
            )
        assert result is None
        # IP matched but no session matched — unresolved counter incremented.
        assert _get_counter_value("user_attribution_unresolved_total", "jellyfin") == 1.0
        assert _get_counter_value("user_attribution_resolved_total", "jellyfin") == 0.0


# ---------------------------------------------------------------------------
# Behavior: exact / case-insensitive match
# ---------------------------------------------------------------------------


class TestExactMatch:
    """Exact (case-insensitive) match between the ECM stream name and a
    Jellyfin session's now-playing item or channel returns the corresponding
    attribution."""

    async def test_exact_match_returns_attribution(self):
        """Exact match on item name returns the session's user attribution."""
        session = _make_session(user_id="jf-uid-bob", user_name="bob", item_name="CNN HD")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result == jellyfin_resolver.JellyfinAttribution(user_id="jf-uid-bob", user_name="bob")

    async def test_case_insensitive_match(self):
        """Case differences do not prevent an exact match."""
        session = _make_session(item_name="CNN")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="cnn",
            )
        assert result is not None
        assert result.user_name == "alice"

    async def test_channel_name_match_when_item_name_none(self):
        """Live-TV sessions with channel name populated but item_name None
        can still attribute via Tier-3 fuzzy match on channel_name."""
        session = _make_session(
            user_id="jf-uid-carol", user_name="carol",
            item_name=None, channel_name="ESPN HD",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ESPN HD",
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-carol", user_name="carol",
        )


# ---------------------------------------------------------------------------
# Behavior: Tier 2 — channel number match
# ---------------------------------------------------------------------------


class TestChannelNumberMatch:
    """Tier 2: when both ecm_channel_number and the Jellyfin session's
    channel_number are present, string-compare wins."""

    async def test_channel_number_string_match(self):
        """ecm_channel_number=408 matches Jellyfin channel_number="408"."""
        session = _make_session(
            user_id="jf-uid-num", user_name="num_user",
            item_name="Some unrelated text", channel_number="408",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="Sports Channel A",  # won't tier-1 match
                ecm_channel_number=408,
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-num", user_name="num_user",
        )

    async def test_channel_number_missing_on_session_skips_tier(self):
        """When the Jellyfin session has channel_number=None, tier 2 skips."""
        session = _make_session(
            user_name="vod_user",
            item_name="The Matrix",
            channel_number=None,
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        # No tier matches — Matrix is unrelated to ESPN.
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: fuzzy match (token-set ratio >= 0.85)
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    """Tier 3: RapidFuzz token_set_ratio with 0.85 threshold."""

    async def test_fuzzy_match_above_threshold_returns_attribution(self):
        """Stream name "CNN HD" vs Jellyfin item "CNN HD 1080p" — well above 0.85."""
        session = _make_session(user_name="dan", item_name="CNN HD 1080p")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is not None
        assert result.user_name == "dan"

    async def test_fuzzy_match_below_threshold_returns_none(self):
        """Stream name vs unrelated item name scores well below the 0.85 floor."""
        session = _make_session(item_name="The Office Season 3 Episode 14")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None

    async def test_fuzzy_match_on_movie_when_no_channel_args(self):
        """Movie session: fuzzy stream_name matches movie title above 0.85."""
        session = _make_session(
            user_name="movie_viewer",
            item_name="The Matrix 1999",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="The Matrix",
            )
        assert result is not None
        assert result.user_name == "movie_viewer"


# ---------------------------------------------------------------------------
# Behavior: no-match conditions
# ---------------------------------------------------------------------------


class TestNoMatch:
    """The resolver returns ``None`` whenever the cache is empty, every
    session is below the fuzzy threshold, or every session is idle."""

    async def test_empty_cache_returns_none(self):
        """An empty cache (Jellyfin idle, or disabled) produces no match."""
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None

    async def test_all_sessions_idle_returns_none(self):
        """Sessions with both item_name and channel_name set to ``None``
        (idle clients) cannot match anything."""
        idle = _make_session(item_name=None, channel_name=None)
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[idle, idle])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: tie-break on multiple matches by most-recent last_activity_date
# ---------------------------------------------------------------------------


class TestMultipleMatchTiebreak:
    """When multiple Jellyfin sessions match the same ECM stream, pick the
    one whose ``last_activity_date`` is most recent."""

    async def test_picks_most_recent_last_activity(self):
        """Two sessions playing the same channel; newer wins."""
        older = _make_session(
            user_id="jf-uid-old", user_name="old_viewer",
            item_name="CNN HD",
            last_activity="2026-05-17T10:00:00Z",
        )
        newer = _make_session(
            user_id="jf-uid-new", user_name="new_viewer",
            item_name="CNN HD",
            last_activity="2026-05-17T14:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[older, newer])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-new", user_name="new_viewer",
        )

    async def test_null_last_activity_loses_to_populated(self):
        """A session with last_activity_date=None never beats one with a real
        timestamp."""
        no_ts = _make_session(
            user_id="jf-uid-null", user_name="null_viewer",
            item_name="CNN HD",
            last_activity=None,
        )
        with_ts = _make_session(
            user_id="jf-uid-ts", user_name="ts_viewer",
            item_name="CNN HD",
            last_activity="2026-05-17T10:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[no_ts, with_ts])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is not None
        assert result.user_name == "ts_viewer"

    async def test_two_bare_channel_matches_pick_most_recent(self):
        """Two Jellyfin sessions with bare channel name (no pipe) — same
        disambiguation logic applies."""
        older = _make_session(
            user_id="jf-uid-old", user_name="old_user",
            item_name="ESPN", channel_number="408",
            last_activity="2026-05-17T10:00:00Z",
        )
        newer = _make_session(
            user_id="jf-uid-new", user_name="new_user",
            item_name="ESPN", channel_number="408",
            last_activity="2026-05-17T14:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[older, newer])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-new", user_name="new_user",
        )


# ---------------------------------------------------------------------------
# Behavior: hostname-based base URL DNS resolution
# ---------------------------------------------------------------------------


class TestHostnameBaseUrl:
    """When ``jellyfin_base_url`` uses a hostname rather than an IP literal,
    the resolver falls back to ``socket.gethostbyname``."""

    async def test_hostname_resolves_to_matching_ip_attributes(self):
        """``jf.local`` resolves to ``192.168.1.20`` — matches the ECM
        session IP and the resolver proceeds to look up the session."""
        session = _make_session(user_name="eve", item_name="CNN HD")
        settings = _enabled_settings(base_url="https://jf.local:8920")

        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(jellyfin_resolver.socket, "gethostbyname",
                          return_value="192.168.1.20"):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is not None
        assert result.user_name == "eve"

    async def test_hostname_resolves_to_non_matching_ip_returns_none(self):
        """``jf.local`` resolves to ``10.0.0.1`` — does NOT match the ECM
        session IP and the resolver short-circuits before the cache."""
        cache_mock = AsyncMock(return_value=[_make_session(item_name="CNN HD")])
        settings = _enabled_settings(base_url="https://jf.local:8920")

        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock), \
             patch.object(jellyfin_resolver.socket, "gethostbyname",
                          return_value="10.0.0.1"):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_hostname_resolution_failure_logs_warn_and_returns_none(self, caplog):
        """``socket.gethostbyname`` raises ``socket.gaierror`` for an
        unresolvable hostname; the resolver logs at WARN and returns ``None``."""
        settings = _enabled_settings(base_url="https://does-not-exist.invalid:8920")

        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", AsyncMock()) as cache_mock, \
             patch.object(jellyfin_resolver.socket, "gethostbyname",
                          side_effect=socket.gaierror("Name resolution failed")):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                result = await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN HD",
                )
        assert result is None
        cache_mock.assert_not_awaited()
        assert any(
            "[JELLYFIN]" in rec.getMessage() and "does-not-exist.invalid" in rec.getMessage()
            for rec in caplog.records
        )

    async def test_hostname_resolution_result_is_cached(self):
        """``socket.gethostbyname`` should be called at most once per
        process — the resolver caches the resolution."""
        session = _make_session(item_name="CNN HD")
        settings = _enabled_settings(base_url="https://jf.local:8920")

        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(jellyfin_resolver.socket, "gethostbyname",
                          return_value="192.168.1.20") as gethost:
            await jellyfin_resolver.resolve_jellyfin_user("192.168.1.20", "CNN HD")
            await jellyfin_resolver.resolve_jellyfin_user("192.168.1.20", "CNN HD")
            await jellyfin_resolver.resolve_jellyfin_user("192.168.1.20", "CNN HD")
        assert gethost.call_count == 1


# ---------------------------------------------------------------------------
# Behavior: back-compat two-positional-args call shape
# ---------------------------------------------------------------------------


class TestBackCompatStreamNameOnlyCallShape:
    """Existing callers invoke ``resolve_jellyfin_user(ip, name)`` with no
    channel args — the signature must keep that call shape working."""

    async def test_two_positional_args_still_works(self):
        """Pre-fix callers pass only (ip, stream_name) — must still
        produce the same fuzzy-match result."""
        session = _make_session(user_name="alice", item_name="CNN HD")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                "192.168.1.20", "CNN HD",
            )
        assert result is not None
        assert result.user_name == "alice"


# ---------------------------------------------------------------------------
# Behavior: empty / malformed configuration
# ---------------------------------------------------------------------------


class TestEmptyOrMalformedConfig:
    """Defensive coverage for misconfiguration."""

    async def test_empty_base_url_returns_none(self):
        """Empty ``jellyfin_base_url`` cannot extract a hostname — returns ``None``."""
        settings = _enabled_settings(base_url="")
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_malformed_base_url_returns_none(self):
        """A base URL that ``urllib.parse`` cannot extract a hostname from
        (e.g. only a scheme) returns ``None`` without raising."""
        settings = _enabled_settings(base_url="http://")
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Behavior: disambiguation debug log + WARN rate-limiting
# ---------------------------------------------------------------------------


class TestDisambiguationLogging:
    """When N > 1 candidates match, the resolver must log DEBUG and a
    rate-limited WARN for disambiguation."""

    async def test_debug_log_fires_when_multiple_candidates(self, caplog):
        """When N > 1 candidates match, a DEBUG line surfaces the disambiguation."""
        older = _make_session(
            user_id="jf-uid-old", user_name="old_user",
            item_name="ESPN", channel_number="408",
            last_activity="2026-05-17T10:00:00Z",
        )
        newer = _make_session(
            user_id="jf-uid-new", user_name="new_user",
            item_name="ESPN", channel_number="408",
            last_activity="2026-05-17T14:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[older, newer])):
            with caplog.at_level(logging.DEBUG, logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="US: ESPN FHD",
                    ecm_channel_name="ESPN",
                    ecm_channel_number=408,
                )
        assert any(
            "[JELLYFIN]" in rec.getMessage()
            and "resolver" in rec.getMessage()
            and "candidates" in rec.getMessage()
            and "new_user" in rec.getMessage()
            for rec in caplog.records
        ), f"expected disambiguation DEBUG; got {[r.getMessage() for r in caplog.records]}"

    async def test_warn_fires_when_multiple_candidates(self, caplog):
        """A WARN is emitted (rate-limited) when multiple candidates exist."""
        older = _make_session(
            user_name="old_user",
            item_name="ESPN",
            last_activity="2026-05-17T10:00:00Z",
        )
        newer = _make_session(
            user_name="new_user",
            item_name="ESPN",
            last_activity="2026-05-17T14:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[older, newer])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="US: ESPN FHD",
                    ecm_channel_name="ESPN",
                )
        assert any(
            "[JELLYFIN]" in rec.getMessage()
            and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), f"expected WARN; got {[r.getMessage() for r in caplog.records]}"
