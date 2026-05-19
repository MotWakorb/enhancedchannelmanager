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
    """bd-ost8o two-mode dispatch — when the ECM session's IP does NOT
    equal the Jellyfin server's IP (the "browser-direct-play" case), the
    resolver no longer short-circuits. Instead it enters Mode B and runs
    Tier 1/2 strict matching against the cached session list. Without a
    Tier-1/2 match the result is still ``None``, but the cache IS
    consulted now — the assert_not_awaited semantic from the pre-bd-ost8o
    strict IP-gate path is intentionally obsolete."""

    async def test_ip_mismatch_with_no_tier_match_returns_none(self):
        """Mode B (browser-direct fallback): IP mismatch, no stream-name
        candidates, no channel-name input → Tier 1/2 both skipped, no
        match, result is ``None``. Cache IS awaited (mode-B dispatch
        fetches sessions to attempt the strict match)."""
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="10.0.0.5",  # NOT the Jellyfin server (192.168.1.20)
                ecm_stream_name="totally unrelated stream",
                # No ecm_channel_name / ecm_channel_number → Tier 1/2 skip.
            )
        assert result is None
        cache_mock.assert_awaited_once()


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

    async def test_hostname_resolves_to_non_matching_ip_enters_mode_b(self):
        """bd-ost8o: ``jf.local`` resolves to ``10.0.0.1`` while the ECM
        session IP is ``192.168.1.20``. Pre-bd-ost8o this short-circuited
        without consulting the cache; post-bd-ost8o it enters Mode B and
        runs Tier 1/2 strict matching. With no ECM channel input and a
        non-matching stream name, no tier matches and the result is
        ``None`` — but the cache IS awaited."""
        cache_mock = AsyncMock(return_value=[_make_session(item_name="CNN HD")])
        settings = _enabled_settings(base_url="https://jf.local:8920")

        with patch.object(jellyfin_resolver, "get_settings", return_value=settings), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock), \
             patch.object(jellyfin_resolver.socket, "gethostbyname",
                          return_value="10.0.0.1"):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="totally unrelated stream",
                # No ecm_channel_name → Mode B Tier 1/2 cannot match.
            )
        assert result is None
        cache_mock.assert_awaited_once()

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


# ---------------------------------------------------------------------------
# bd-r5f0c.9 multi-viewer: resolve_jellyfin_users returns the FULL list of
# matched sessions sorted most-recent first, NOT just the tiebreak winner.
# Fixes the PO-reported bug where ECM showed only one user when multiple
# Jellyfin viewers were watching the same channel via the Jellyfin server proxy.
# ---------------------------------------------------------------------------


class TestMultiViewer:
    """Plural :func:`resolve_jellyfin_users` captures every viewer that
    matched any tier. The singular wrapper :func:`resolve_jellyfin_user`
    continues to return the most-recent viewer for back-compat.
    """

    async def test_two_viewers_same_channel_returns_both(self):
        """Two Jellyfin sessions on the same channel → resolve_jellyfin_users
        returns BOTH attributions, most-recent first."""
        older = _make_session(
            user_id="uid-alice", user_name="alice",
            item_name="ESPN",  # bare channel name (Jellyfin tolerance)
            last_activity="2026-05-16T10:00:00Z",
        )
        newer = _make_session(
            user_id="uid-bob", user_name="bob",
            item_name="ESPN",
            last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[older, newer])):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
            )
        assert len(users) == 2, f"expected 2 viewers; got {len(users)}"
        assert users[0].user_id == "uid-bob"
        assert users[0].user_name == "bob"
        assert users[1].user_id == "uid-alice"
        assert users[1].user_name == "alice"

    async def test_three_viewers_same_channel_returns_all_three(self):
        """Three Jellyfin sessions on the same channel."""
        s1 = _make_session(
            user_id="u1", user_name="alice",
            item_name="ESPN", last_activity="2026-05-16T10:00:00Z",
        )
        s2 = _make_session(
            user_id="u2", user_name="bob",
            item_name="ESPN", last_activity="2026-05-16T12:00:00Z",
        )
        s3 = _make_session(
            user_id="u3", user_name="charlie",
            item_name="ESPN", last_activity="2026-05-16T14:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[s1, s2, s3])):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ESPN",
                ecm_channel_name="ESPN",
            )
        assert [u.user_name for u in users] == ["charlie", "bob", "alice"]

    async def test_single_viewer_returns_one_element_list(self):
        """The common case: one viewer on the channel → list of 1."""
        only = _make_session(
            user_id="uid-only", user_name="solo",
            item_name="CNN HD",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[only])):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert len(users) == 1
        assert users[0].user_name == "solo"

    async def test_no_match_returns_empty_list(self):
        """No tier matches → empty list."""
        irrelevant = _make_session(
            user_id="u", user_name="u", item_name="Unrelated Show",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[irrelevant])):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert users == []

    async def test_ip_mismatch_enters_mode_b_for_plural_variant(self):
        """bd-ost8o: the plural variant respects two-mode dispatch
        identically to the singular wrapper. IP mismatch + no usable
        ECM channel input → empty list, but the cache IS consulted."""
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions", cache_mock):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="10.0.0.5",  # NOT the Jellyfin server
                ecm_stream_name="completely different stream",
            )
        assert users == []
        cache_mock.assert_awaited_once()

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
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[older, newer])):
            singular = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert singular is not None
        assert singular.user_name == "new_viewer"

    async def test_tier_2_match_contributes_to_list(self):
        """A viewer matched via Tier-2 (channel_number) is included in
        the list alongside Tier-1 matches."""
        # Tier-1 match: bare item_name == ecm_channel_name
        t1 = _make_session(
            user_id="t1", user_name="t1_user",
            item_name="ESPN", channel_number="408",
            last_activity="2026-05-16T14:00:00Z",
        )
        # Tier-2 match: channel_number matches but item_name does not
        t2 = _make_session(
            user_id="t2", user_name="t2_user",
            item_name="Some Other Title", channel_number="408",
            last_activity="2026-05-16T12:00:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[t1, t2])):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="192.168.1.20",
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
        ``user_attribution_resolved_total{source="jellyfin"}`` counter
        increments PER VIEWER. 3 matched viewers = 3 increments."""
        s1 = _make_session(user_id="u1", user_name="a", item_name="CNN")
        s2 = _make_session(user_id="u2", user_name="b", item_name="CNN")
        s3 = _make_session(user_id="u3", user_name="c", item_name="CNN")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[s1, s2, s3])):
            users = await jellyfin_resolver.resolve_jellyfin_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN",
                ecm_channel_name="CNN",
            )
        assert len(users) == 3
        assert _get_counter_value("user_attribution_resolved_total", "jellyfin") == 3.0


# ---------------------------------------------------------------------------
# bd-r5f0c.10: forensic no-match WARN. Same shape as the Emby resolver
# helper — see TestNoMatchDiagnostic in test_emby_resolver.py for the
# rationale. Fires once per (ip, channel) per 60 s when sessions exist
# and the IP short-circuit passed but no tier accepted any session.
# ---------------------------------------------------------------------------


class TestNoMatchDiagnostic:
    """Jellyfin forensic no-match WARN. Same suppression semantics as
    the Emby helper — only fires when IP matched + sessions non-empty +
    no tier matched."""

    @staticmethod
    def _no_match_records(records):
        return [
            r for r in records
            if "[JELLYFIN-RESOLVER] no-match diagnostic" in r.getMessage()
        ]

    async def test_emits_warn_when_sessions_exist_and_no_tier_matches(self, caplog):
        """Sessions in cache but no tier accepts any of them → one WARN."""
        session = _make_session(
            user_name="alice",
            item_name="NotTheChannel",  # bare Jellyfin style
            channel_number="999",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                users = await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
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
        assert "CNN" in msg
        assert "NotTheChannel" in msg

    async def test_no_emit_when_sessions_empty(self, caplog):
        """Empty session cache is the normal state — no WARN."""
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                users = await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        assert users == []
        assert self._no_match_records(caplog.records) == []

    async def test_no_emit_when_match_succeeds(self, caplog):
        """A successful resolve must not emit the no-match WARN."""
        session = _make_session(user_name="alice", item_name="CNN HD")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                users = await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN HD",
                )
        assert len(users) == 1
        assert self._no_match_records(caplog.records) == []

    async def test_emits_when_ip_mismatch_mode_b_finds_no_match(self, caplog):
        """bd-ost8o: post-fix, IP mismatch enters Mode B instead of
        short-circuiting. Mode B fetches sessions and runs Tier 1/2; a
        no-tier-match outcome emits the same forensic WARN as Mode A.
        This is the new behavior — the pre-bd-ost8o "short-circuit
        silences the WARN" contract no longer holds for IP mismatch."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                users = await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="10.0.0.99",  # NOT the Jellyfin server
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        assert users == []
        # Mode B no-match still emits the diagnostic WARN — that's
        # the desired surface for debugging direct-play attribution.
        assert len(self._no_match_records(caplog.records)) == 1

    async def test_rate_limit_within_window(self, caplog):
        """Two no-match calls for same (ip, channel) within 60 s → one WARN."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1

    async def test_rate_limit_per_channel_independence(self, caplog):
        """Different channels for the same IP each get their own WARN."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CBS",
                    ecm_channel_name="CBS",
                )
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 2
        messages = " ".join(r.getMessage() for r in warns)
        assert "CBS" in messages and "CNN" in messages

    async def test_rate_limit_window_expiry(self, caplog, monkeypatch):
        """Advancing the monotonic clock past 60 s permits a fresh WARN."""
        session = _make_session(item_name="UnrelatedShow")
        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr(jellyfin_resolver.time, "monotonic", fake_monotonic)

        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
                fake_now[0] += 61.0
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 2

    async def test_truncation_cap_thirty(self, caplog):
        """bd-r5f0c.11: 35 sessions in cache → forensic payload includes
        exactly 30 entries (the bumped cap), not the original 10 and
        not the full 35.
        """
        sessions = [
            _make_session(
                user_id=f"jf-uid-{idx:02d}",
                user_name=f"user{idx:02d}",
                item_name=f"{900 + idx} | NotTheChannel{idx}",
                channel_number=str(900 + idx),
            )
            for idx in range(35)
        ]
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=sessions)):
            with caplog.at_level(logging.WARNING, logger="services.jellyfin_resolver"):
                users = await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="US: CNN FHD",
                    ecm_channel_name="CNN",
                    ecm_channel_number=200,
                )
        assert users == []
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1
        msg = warns[0].getMessage()
        assert msg.count("'session_id':") == 30, (
            f"expected truncation cap of 30 session entries, "
            f"got {msg.count(chr(39) + 'session_id' + chr(39) + ':')}"
        )
        assert "sessions_count=35" in msg


# ---------------------------------------------------------------------------
# bd-dok7u: forensic INFO logging at every decision point on the resolver
# chain. Tests lock the log payload format so future "User #0" incidents
# can be diagnosed in one log read.
# ---------------------------------------------------------------------------


class TestForensicInfoLogging:
    """Every resolver call must emit:
    * One [JELLYFIN-RESOLVER] resolver_call line with sessions_count and
      sessions_with_now_playing counts so an operator can see at a glance
      whether the upstream session feed actually carried playback data.
    * One [JELLYFIN-RESOLVER] session_inspect line per session, exposing
      has_now_playing + item_name + channel_name + channel_number +
      last_activity + queue_item_id so the resolver's input is visible.
    * One [JELLYFIN-RESOLVER] tier_match line on a successful match
      (parallel to the existing no-match WARN)."""

    @staticmethod
    def _info_records(records, marker):
        return [r for r in records if marker in r.getMessage()]

    async def test_resolver_call_log_emitted_on_match_path(self, caplog):
        """On the IP-matched + sessions-available path, the resolver
        emits ONE resolver_call INFO with the input snapshot."""
        session = _make_session(user_name="alice", item_name="ESPN")
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                    ecm_channel_number=408,
                )
        calls = self._info_records(caplog.records,
                                   "[JELLYFIN-RESOLVER] resolver_call")
        assert len(calls) == 1, [r.getMessage() for r in calls]
        msg = calls[0].getMessage()
        assert "sessions_count=1" in msg
        assert "sessions_with_now_playing=1" in msg
        assert "ecm_channel='ESPN'" in msg
        assert "ecm_channel_number=408" in msg
        # bd-ost8o: Mode A (IP matches Jellyfin server) → mode=server_ip.
        assert "mode=server_ip" in msg

    async def test_session_inspect_log_emitted_per_session(self, caplog):
        """Each cached session produces one session_inspect line whose
        payload exposes the fields the matcher operates on. The PO's
        production case (NowPlayingItem absent) surfaces here as
        has_now_playing=False."""
        s1 = _make_session(user_name="alice", item_name="ESPN")
        s2 = _make_session(user_name="bob", item_name=None,
                           channel_number=None,
                           last_activity="2026-05-19T02:00:00Z")
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[s1, s2])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        inspects = self._info_records(caplog.records,
                                      "[JELLYFIN-RESOLVER] session_inspect")
        assert len(inspects) == 2
        msgs = " ".join(r.getMessage() for r in inspects)
        # Alice's row has item_name; bob has None.
        assert "user=alice" in msgs
        assert "user=bob" in msgs
        assert "has_now_playing=True" in msgs
        assert "has_now_playing=False" in msgs

    async def test_tier_match_log_emitted_on_success(self, caplog):
        """A successful match emits a single tier_match INFO line so
        operators can confirm attribution without enabling DEBUG."""
        session = _make_session(user_id="jf-mw-uid", user_name="MotWakorb",
                                item_name="ESPN")
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                result = await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        assert result is not None
        matches = self._info_records(caplog.records,
                                     "[JELLYFIN-RESOLVER] tier_match")
        assert len(matches) == 1
        msg = matches[0].getMessage()
        assert "top_user=MotWakorb" in msg
        assert "top_user_id=jf-mw-uid" in msg
        assert "match_count=1" in msg

    async def test_no_tier_match_log_on_no_match(self, caplog):
        """When the resolver returns empty (no tier matched), no
        tier_match INFO line fires — the no-match WARN handles that
        case so the success-side INFO is unambiguous."""
        session = _make_session(item_name="NotTheChannel")
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                result = await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        assert result is None
        matches = self._info_records(caplog.records,
                                     "[JELLYFIN-RESOLVER] tier_match")
        assert matches == []

    async def test_queue_item_id_surfaced_in_session_inspect(self, caplog):
        """When the session carries a NowPlayingQueue item ID (bd-dok7u),
        it surfaces in the session_inspect log so the cache's fallback
        path is observable from the resolver's perspective."""
        session = JellyfinSession(
            session_id="jf-q-sess",
            user_id="jf-q-uid",
            user_name="queued_user",
            remote_endpoint="192.168.1.61",
            now_playing_item_name=None,
            now_playing_channel_name=None,
            last_activity_date="2026-05-19T02:33:36Z",
            channel_number=None,
            now_playing_queue_item_id="2b0159285ef4370ec3c8c91534bc076d",
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        inspects = self._info_records(caplog.records,
                                      "[JELLYFIN-RESOLVER] session_inspect")
        assert len(inspects) == 1
        msg = inspects[0].getMessage()
        assert "queue_item_id=2b0159285ef4370ec3c8c91534bc076d" in msg

    async def test_ip_mismatch_emits_resolver_call_info_with_direct_fallback_mode(self, caplog):
        """bd-ost8o: IP mismatch no longer short-circuits silently. It
        emits the resolver_call INFO with ``mode=direct_fallback`` so the
        operator can confirm Mode B ran. The cache IS awaited (Mode B
        fetches sessions to attempt the strict match)."""
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[])) as cache_mock:
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="10.0.0.99",  # NOT the Jellyfin server
                    ecm_stream_name="CNN",
                )
        cache_mock.assert_awaited_once()
        calls = self._info_records(caplog.records,
                                   "[JELLYFIN-RESOLVER] resolver_call")
        assert len(calls) == 1
        assert "mode=direct_fallback" in calls[0].getMessage()


# ---------------------------------------------------------------------------
# Behavior: bd-r5f0c.11 — ECM-side pipe-prefix tolerance in Tier-1
# ---------------------------------------------------------------------------


class TestECMPipePrefixTolerance:
    """bd-r5f0c.11: when an operator imports channels with the pipe-prefix
    display format leaked into the ECM channel_name itself (e.g.
    "109 | CNN"), tier-1 must match against Jellyfin sessions even
    though ECM is carrying the upstream's display format. The fix adds
    two compares against the parsed ECM suffix (alongside the existing
    compares against ecm_channel_name as a whole), gated on the ECM
    name actually containing a pipe so clean ECM names are unaffected.
    """

    async def test_ecm_prefix_jellyfin_prefix(self):
        """ECM channel_name "109 | CNN" vs Jellyfin item_name "109 | CNN"."""
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="109 | CNN", channel_number="109",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="109 | CNN",
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-mw", user_name="MotWakorb",
        )

    async def test_ecm_prefix_jellyfin_clean(self):
        """ECM channel_name "109 | CNN" vs Jellyfin item_name "CNN"
        (the canonical Jellyfin-style bare channel name). Only the new
        compare (ecm_suffix vs whole item_name) reaches this case.
        """
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="CNN",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="109 | CNN",
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_clean_jellyfin_prefix(self):
        """ECM channel_name "CNN" vs Jellyfin item_name "109 | CNN".
        Regression — existing pre-bd-r5f0c.11 path.
        """
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="109 | CNN", channel_number="109",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="CNN",
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_clean_jellyfin_clean(self):
        """ECM channel_name "CNN" vs Jellyfin item_name "CNN".
        Trivial whole-string regression check.
        """
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="CNN",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="CNN",
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_prefix_no_match(self):
        """ECM channel_name "109 | CNN" vs Jellyfin item_name "NESN".
        Tolerance must NOT cause false positives.
        """
        session = _make_session(
            user_name="alice",
            item_name="NESN",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="totally unrelated stream name",
                ecm_channel_name="109 | CNN",
            )
        assert result is None

    async def test_ecm_prefix_case_insensitive(self):
        """ECM channel_name "109 | cnn" (lowercase) vs Jellyfin
        item_name "109 | CNN". NFC + lower + strip on both sides.
        """
        session = _make_session(
            user_name="case_user",
            item_name="109 | CNN",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored — tier 1 wins",
                ecm_channel_name="109 | cnn",
            )
        assert result is not None
        assert result.user_name == "case_user"


# ---------------------------------------------------------------------------
# Behavior: bd-f68c8 — ECM pipe-prefix parsed as channel_number candidate
# (Tier-2 reinforcement for the Jellyfin "User #0" Live TV symptom)
# ---------------------------------------------------------------------------


class TestEcmPipePrefixAsChannelNumber:
    """bd-f68c8: Jellyfin Live TV ECM channels often carry the channel
    number in the ECM channel_name as a "<number> | <rest>" pipe-prefix
    (e.g. Dispatcharr does not push a separate channel_number for sub-
    channels like "2.1"). When ``ecm_channel_number`` is ``None`` AND
    ``ecm_channel_name`` starts with a numeric pipe-prefix, the resolver
    must parse that prefix as a Tier-2 channel_number candidate and
    match it against the session's separately-reported ``channel_number``.

    This complements the existing bd-r5f0c.11 Tier-1 ECM-side pipe-suffix
    tolerance with a Tier-2-equivalent path so the resolver still
    attributes correctly when (a) ECM's name does not match the
    session's name surface (e.g. Jellyfin strips the channel-number
    prefix from ``NowPlayingItem.Name``, leaving a bare-pipe form like
    ``"| ABC: WBAY Green Bay"`` whose suffix matches the ECM suffix —
    Tier-1 covers that), AND (b) — equally — when the names don't line
    up at all but the channel numbers do.

    The PO's symptom: channel ``"2.1 | ABC: WBAY Green Bay"`` watched
    via Jellyfin, reported as "User #0" because attribution rows had
    ``jellyfin_user_name = NULL``.
    """

    async def test_ecm_prefix_numeric_matches_session_channel_number(self):
        """ECM ``"2.1 | ABC: WBAY Green Bay"`` + ``channel_number=None``
        + Jellyfin session ``channel_number="2.1"`` → match the user."""
        session = _make_session(
            user_id="72be7fac4ca046cf8198573ad285c963",
            user_name="MotWakorb",
            # Different name surface — Tier-1 pipe-suffix tolerance does
            # NOT save this case; only Tier-2 channel-number-from-prefix
            # can produce a match.
            item_name="Saturday Night Live",
            channel_number="2.1",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                # The Dispatcharr stream_name is unrelated to the
                # Jellyfin item_name — Tier-3 fuzzy must not save this.
                ecm_stream_name="WI | Green Bay | ABC 2 WBAY",
                ecm_channel_name="2.1 | ABC: WBAY Green Bay",
                ecm_channel_number=None,
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="72be7fac4ca046cf8198573ad285c963",
            user_name="MotWakorb",
        )

    async def test_po_symptom_full_data_shape(self):
        """PO's exact symptom: ECM channel ``"2.1 | ABC: WBAY Green Bay"``,
        Jellyfin session with ``item_name="| ABC: WBAY Green Bay"`` and
        ``channel_number="2.1"``. With this shape Tier-1 already matches
        via bd-r5f0c.11 ECM-side suffix tolerance (the session suffix
        ``"abc: wbay green bay"`` equals the ECM suffix), AND Tier-2
        independently matches via the bd-f68c8 prefix-as-number tolerance.
        Either path is sufficient — the assertion just confirms the
        resolver returns the user.
        """
        session = _make_session(
            user_id="72be7fac4ca046cf8198573ad285c963",
            user_name="MotWakorb",
            # Jellyfin strips the "2.1 " prefix and emits "| ABC: WBAY..."
            item_name="| ABC: WBAY Green Bay",
            channel_number="2.1",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="WI | Green Bay | ABC 2 WBAY",
                ecm_channel_name="2.1 | ABC: WBAY Green Bay",
                ecm_channel_number=None,
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_ecm_prefix_non_numeric_does_not_false_match(self):
        """ECM channel_name ``"News | CNN"`` (non-numeric pipe-prefix)
        + ``channel_number=None`` + session ``channel_number="news"``
        must NOT match via the new Tier-2 path — only numeric prefixes
        are accepted as channel-number candidates so word-prefixed
        channel names cannot collide with arbitrary session
        ``channel_number`` strings."""
        session = _make_session(
            user_name="ghost",
            # Tier-1 must not match — pick an item_name that doesn't
            # match either ECM whole or ECM suffix.
            item_name="Some Show",
            # Intentionally set channel_number to the non-numeric prefix
            # to verify we DO NOT compare non-numeric prefixes against it.
            channel_number="news",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="News | CNN",
                ecm_channel_number=None,
            )
        assert result is None

    async def test_ecm_prefix_does_not_match_when_session_number_differs(self):
        """ECM ``"5 | Foo"`` + ``channel_number=None`` + session
        ``channel_number="6"``: prefix is numeric but the values differ,
        so Tier-2 must NOT match (and no other tier should false-match)."""
        session = _make_session(
            user_name="ghost",
            item_name="Some Show",
            channel_number="6",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="5 | Foo",
                ecm_channel_number=None,
            )
        assert result is None

    async def test_no_pipe_prefix_in_ecm_name_skips_prefix_path(self):
        """ECM channel_name ``"ESPN"`` (no pipe) + ``channel_number=None``
        + session ``channel_number="408"``: no numeric prefix to parse,
        so the new Tier-2 prefix path does NOT contribute, and the
        unrelated session is not matched."""
        session = _make_session(
            user_name="ghost",
            item_name="Some Show",
            channel_number="408",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="ESPN",
                ecm_channel_number=None,
            )
        assert result is None

    async def test_explicit_ecm_channel_number_takes_precedence_over_prefix(self):
        """When ``ecm_channel_number`` is explicitly provided, the
        prefix-parsing path is NOT consulted — the caller-supplied
        number is the authoritative Tier-2 value. ECM
        ``"99 | NotESPN"`` + ``channel_number=408`` + session
        ``channel_number="408"`` matches via the explicit number, NOT
        via the (rejected) "99" prefix."""
        session = _make_session(
            user_name="explicit_num_user",
            item_name="Some Show",
            channel_number="408",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="99 | NotESPN",
                ecm_channel_number=408,
            )
        assert result is not None
        assert result.user_name == "explicit_num_user"

    async def test_subchannel_decimal_prefix_2_1(self):
        """Sub-channel numbers (e.g. ATSC ``"2.1"``) are common in
        broadcast TV — verify the prefix parser accepts a single dot
        and treats ``"2.1"`` as a valid numeric channel-number
        candidate."""
        session = _make_session(
            user_name="subchannel_user",
            item_name="Local News",
            channel_number="2.1",
        )
        with patch.object(jellyfin_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="WI | Green Bay | ABC 2 WBAY",
                ecm_channel_name="2.1 | ABC: WBAY Green Bay",
                ecm_channel_number=None,
            )
        assert result is not None
        assert result.user_name == "subchannel_user"


# ---------------------------------------------------------------------------
# bd-ost8o: two-mode dispatch — Mode B (browser-direct fallback). The PO
# opens Jellyfin Web at http://172.16.0.19:18096 in a browser; the
# browser fetches the IPTV channel URL directly from Dispatcharr. The
# ECM client IP is the browser's egress, NOT the Jellyfin server IP, so
# the pre-bd-ost8o strict IP-gate would silently skip attribution. Mode
# B fetches sessions and runs Tier 1/2 only (Tier 3 fuzzy disabled).
# ---------------------------------------------------------------------------


class TestModeBDirectFallback:
    """Browser-direct-play attribution path. The ECM session IP is some
    non-Jellyfin egress (Docker bridge, NAT, public IP) and the Jellyfin
    server is at a different address. Mode B fetches the cached sessions
    and runs strict Tier-1 / Tier-2 matching; Tier-3 fuzzy is skipped so
    a multi-user setup cannot cross-attribute on a generic stream name."""

    _BROWSER_IP = "172.18.0.1"  # Docker bridge gateway — PO's exact symptom
    _JELLYFIN_IP = "192.168.1.20"

    async def test_mode_b_tier1_channel_name_match(self):
        """One session has item_name="ESPN" matching ecm_channel_name="ESPN"
        → Mode B Tier-1 attributes the session."""
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="ESPN",  # bare — Jellyfin-style
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip=self._BROWSER_IP,
                ecm_stream_name="US: ESPN HD",
                ecm_channel_name="ESPN",
            )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-mw", user_name="MotWakorb",
        )

    async def test_mode_b_tier2_channel_number_match(self):
        """One session has channel_number="408" matching
        ecm_channel_number=408 → Mode B Tier-2 attributes the session.
        Tier-1 deliberately doesn't match (different channel name) so
        only Tier-2 can carry the match."""
        session = _make_session(
            user_id="jf-uid-mw", user_name="MotWakorb",
            item_name="Some Show",  # Tier-1 cannot match
            channel_number="408",
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip=self._BROWSER_IP,
                ecm_stream_name="ESPN",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result is not None
        assert result.user_name == "MotWakorb"

    async def test_mode_b_no_match_emits_no_match_diagnostic(self, caplog):
        """Mode B with zero Tier-1/2 matches → returns ``None`` and
        emits the structured no-match WARN (same diagnostic surface as
        Mode A's no-match path)."""
        session = _make_session(
            user_name="alice", item_name="UnrelatedShow",
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING,
                                 logger="services.jellyfin_resolver"):
                result = await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip=self._BROWSER_IP,
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        assert result is None
        no_match = [r for r in caplog.records
                    if "[JELLYFIN-RESOLVER] no-match diagnostic" in r.getMessage()]
        assert len(no_match) == 1

    async def test_mode_b_ambiguous_skip_returns_empty(self, caplog):
        """Mode B with two Tier-1 matches → ambiguous_skip; no
        attribution returned (because there is no IP-gate tiebreaker)
        and an INFO ambiguous_skip diagnostic fires."""
        s1 = _make_session(
            user_id="jf-uid-alice", user_name="alice", item_name="ESPN",
            last_activity="2026-05-19T12:00:00Z",
        )
        s2 = _make_session(
            user_id="jf-uid-bob", user_name="bob", item_name="ESPN",
            last_activity="2026-05-19T12:01:00Z",
        )
        # Distinct session_ids so both are accepted by _find_matching_sessions.
        s1 = JellyfinSession(
            session_id="jf-sess-a", user_id="jf-uid-alice", user_name="alice",
            remote_endpoint="10.0.0.99",
            now_playing_item_name="ESPN", now_playing_channel_name=None,
            channel_number=None,
            last_activity_date="2026-05-19T12:00:00Z",
        )
        s2 = JellyfinSession(
            session_id="jf-sess-b", user_id="jf-uid-bob", user_name="bob",
            remote_endpoint="10.0.0.99",
            now_playing_item_name="ESPN", now_playing_channel_name=None,
            channel_number=None,
            last_activity_date="2026-05-19T12:01:00Z",
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[s1, s2])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                result = await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip=self._BROWSER_IP,
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        assert result is None
        ambig = [r for r in caplog.records
                 if "result=ambiguous_skip" in r.getMessage()]
        assert len(ambig) == 1
        assert "matched_sessions=2" in ambig[0].getMessage()

    async def test_mode_b_skips_tier3_fuzzy(self):
        """Mode B must NOT use the Tier-3 fuzzy stream-name fallback. A
        session whose item_name only fuzzy-matches the stream_name (with
        no tier-1/2 path open) would attribute in Mode A; in Mode B it
        must NOT match."""
        # Fuzzy-only match: item_name "ESPN Sports Channel" against
        # stream_name "ESPN HD" would token-set-ratio ≥ 0.85 in Mode A.
        # Mode B should reject this — no Tier-1 (different channel
        # names) and no Tier-2 (no channel_number on either side).
        session = _make_session(
            user_name="fuzzy_user", item_name="ESPN Sports Channel",
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            # Sanity check: in Mode A this WOULD match via tier-3 fuzzy.
            mode_a_result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip=self._JELLYFIN_IP,
                ecm_stream_name="ESPN Sports Channel",
            )
            assert mode_a_result is not None
            assert mode_a_result.user_name == "fuzzy_user"
        # Now Mode B with the same fuzzy-only case must NOT match.
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            mode_b_result = await jellyfin_resolver.resolve_jellyfin_user(
                ecm_session_ip=self._BROWSER_IP,
                ecm_stream_name="ESPN Sports Channel",
                # No ecm_channel_name → tier-1 skipped; tier-2 also no-op.
            )
        assert mode_b_result is None

    async def test_mode_b_emits_resolver_call_with_mode_marker(self, caplog):
        """The resolver_call INFO log carries ``mode=direct_fallback`` so
        operators can confirm Mode B ran. Bonus check: log line carries
        the ecm_channel input."""
        session = _make_session(user_name="alice", item_name="ESPN")
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip=self._BROWSER_IP,
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        calls = [r for r in caplog.records
                 if "[JELLYFIN-RESOLVER] resolver_call" in r.getMessage()]
        assert len(calls) == 1
        msg = calls[0].getMessage()
        assert "mode=direct_fallback" in msg
        assert "ecm_channel='ESPN'" in msg

    async def test_mode_b_po_tennis_channel_attribution(self, caplog):
        """End-to-end translation of the PO's exact symptom (bd-ost8o).

        PO opens Jellyfin Web at 172.16.0.19:18096 in a browser. Browser
        plays channel ``"430 | Tennis Channel"`` from Dispatcharr. The
        ECM client IP is the browser's egress (172.18.0.1 — Docker bridge
        gateway), NOT the Jellyfin server. The cached Jellyfin session
        has been queue-resolved by bd-dok7u so its item_name carries the
        Live TV channel data (here "430 | Tennis Channel"). Mode B
        Tier-1 attributes MotWakorb and emits the tier_match INFO."""
        # bd-dok7u queue-resolved session shape: NowPlayingItem was
        # originally absent but the cache fallback populated item_name
        # and channel_number from the GET /Users/{uid}/Items/{id} lookup.
        session = JellyfinSession(
            session_id="jf-tennis-sess",
            user_id="jf-uid-mw",
            user_name="MotWakorb",
            remote_endpoint="172.16.0.61",
            now_playing_item_name="430 | Tennis Channel",
            now_playing_channel_name=None,
            channel_number="430",
            last_activity_date="2026-05-19T02:33:36Z",
            now_playing_queue_item_id="2b0159285ef4370ec3c8c91534bc076d",
        )
        with patch.object(jellyfin_resolver, "get_settings",
                          return_value=_enabled_settings(
                              base_url="http://172.16.0.19:18096")), \
             patch.object(jellyfin_resolver, "get_cached_jellyfin_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.INFO,
                                 logger="services.jellyfin_resolver"):
                result = await jellyfin_resolver.resolve_jellyfin_user(
                    ecm_session_ip="172.18.0.1",
                    ecm_stream_name="US: Tennis Channel HD",
                    ecm_channel_name="430 | Tennis Channel",
                )
        assert result == jellyfin_resolver.JellyfinAttribution(
            user_id="jf-uid-mw", user_name="MotWakorb",
        )
        # The mode marker must be present so operators can confirm Mode B
        # ran.
        calls = [r for r in caplog.records
                 if "[JELLYFIN-RESOLVER] resolver_call" in r.getMessage()]
        assert len(calls) == 1
        assert "mode=direct_fallback" in calls[0].getMessage()
        # tier_match log fires on success.
        matches = [r for r in caplog.records
                   if "[JELLYFIN-RESOLVER] tier_match" in r.getMessage()]
        assert len(matches) == 1
        assert "top_user=MotWakorb" in matches[0].getMessage()
