"""Unit tests for :mod:`services.plex_resolver` (bd-r5f0c.2, epic bd-r5f0c).

The resolver answers one question: given an ECM stream session (client
IP + stream name), is there exactly one matching Plex session, and if
so, which Plex user owns it?

Algorithm under test:

1. If the ECM session's client IP does NOT match the configured Plex
   server's IP (extracted from ``settings.plex_base_url``), return
   ``None`` immediately — the stream is going somewhere other than the
   Plex server, so no Plex attribution is possible. Do NOT call the
   cache in this branch.
2. Otherwise, fetch cached Plex sessions via
   :func:`plex_cache.get_cached_plex_sessions`.
3. Tiered matching across 3 tiers (mirrors emby_resolver bd-zldrq):
   - Tier 1: channel_name pipe-suffix match
   - Tier 2: channel_number match against pipe-prefix
   - Tier 3: fuzzy stream_name match (RapidFuzz token_set_ratio ≥ 0.85)
4. Zero matches → return ``None``.
5. One or more matches → most-recent ``last_activity_date`` wins.
   Returns user_name as a string (not a dataclass).

Test isolation note: the resolver caches DNS resolution results
module-level. :func:`plex_resolver._reset_for_tests` clears that cache
between tests.
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import observability
from plex_client import PlexSession
from services import plex_resolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SENTINEL = object()  # distinct from None to allow explicit None last_activity


def _make_session(
    *,
    session_id: str | None = None,
    user_id: str = "uid-alice",
    user_name: str = "alice",
    item_name: str | None = "408 | ESPN",
    last_activity: object = _SENTINEL,
    remote_endpoint: str = "10.0.0.99",
) -> PlexSession:
    """Build a representative :class:`PlexSession` for assertions.

    Pass ``last_activity=None`` explicitly to get ``last_activity_date=None``.
    Omit the argument to get a sensible default datetime (avoids accidental
    None tiebreak in tests that don't care about the timestamp).
    """
    if last_activity is _SENTINEL:
        last_activity = datetime(2025, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    return PlexSession(
        session_id=session_id or f"sess-{user_name}",
        user_id=user_id,
        user_name=user_name,
        remote_endpoint=remote_endpoint,
        now_playing_item_name=item_name,
        last_activity_date=last_activity,  # type: ignore[arg-type]
    )


def _enabled_settings(base_url: str = "http://192.168.1.20:32400") -> MagicMock:
    """Settings stub for a fully-configured, enabled Plex.

    Default base URL is an IP literal so tests of the resolver's
    happy-path do not need to mock ``socket.gethostbyname``.
    """
    settings = MagicMock()
    settings.plex_enabled = True
    settings.plex_base_url = base_url
    settings.plex_token = "token-123"
    return settings


@pytest.fixture(autouse=True)
def reset_resolver_state():
    """Reset the module-level DNS cache around every test."""
    plex_resolver._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    plex_resolver._reset_for_tests()
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
    """When the ECM session's IP is not the Plex server's IP, the resolver
    must return ``None`` without ever touching the cache."""

    async def test_ip_mismatch_returns_none_without_cache_call(self):
        """IP mismatch on an IP-literal base URL skips the cache entirely."""
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="10.0.0.5",  # NOT the Plex server (192.168.1.20)
                ecm_stream_name="ESPN",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_ip_match_proceeds_to_cache_call(self):
        """When the session IP matches the Plex server IP, the cache is called."""
        cache_mock = AsyncMock(return_value=[])
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock):
            await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ESPN",
            )
        cache_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Behavior: Tier 1 — channel name pipe-suffix match
# ---------------------------------------------------------------------------


class TestTier1ChannelNameMatch:
    """Tier 1: parse item_name as ``"<number> | <name>"`` and match the
    right-hand part against ``ecm_channel_name`` case-insensitively."""

    async def test_channel_name_matches_pipe_suffix(self):
        """item_name "408 | ESPN" matches ecm_channel_name "ESPN"."""
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="408 | ESPN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result == "MotWakorb"
        # Metric assertion: resolved counter incremented; unresolved is zero.
        assert _get_counter_value("user_attribution_resolved_total", "plex") == 1.0
        assert _get_counter_value("user_attribution_unresolved_total", "plex") == 0.0

    async def test_channel_name_matches_whole_name_without_prefix(self):
        """item_name is just "ESPN" (no pipe prefix) — whole-string match."""
        session = _make_session(
            user_name="MotWakorb",
            item_name="ESPN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
            )
        assert result is not None
        assert result == "MotWakorb"

    async def test_channel_name_match_is_case_insensitive(self):
        """``espn`` matches ``"408 | ESPN"`` — normalization applied."""
        session = _make_session(
            user_name="case_user",
            item_name="408 | ESPN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored — tier 1 wins first",
                ecm_channel_name="espn",
            )
        assert result is not None
        assert result == "case_user"

    async def test_no_channel_name_arg_skips_tier1(self):
        """When ecm_channel_name is None, tier 1 is skipped entirely."""
        # Session only has pipe-suffix item name; without channel_name arg
        # tier 1 cannot match but tier 3 might.
        session = _make_session(
            user_name="sports_fan",
            item_name="CNN HD",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            # No ecm_channel_name; stream_name exact-matches item_name
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result == "sports_fan"


# ---------------------------------------------------------------------------
# Behavior: Tier 2 — channel number match
# ---------------------------------------------------------------------------


class TestTier2ChannelNumberMatch:
    """Tier 2: match channel number against the left-hand prefix of
    ``"<number> | <name>"``."""

    async def test_channel_number_string_match(self):
        """ecm_channel_number=408 matches "408 | ESPN" prefix "408"."""
        session = _make_session(
            user_id="uid-num", user_name="num_user",
            item_name="408 | Some unrelated channel",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="Sports Channel A",  # won't tier-1 match
                ecm_channel_number=408,
            )
        assert result == "num_user"

    async def test_channel_number_int_cast_to_string(self):
        """ecm_channel_number can be int or str — both should match."""
        session = _make_session(
            user_name="int_user",
            item_name="200 | CNN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result_int = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignore",
                ecm_channel_name="XYZ",  # no match
                ecm_channel_number=200,
            )
        assert result_int == "int_user"

    async def test_no_pipe_in_item_name_skips_tier2(self):
        """VOD session (no pipe format) cannot match via channel number."""
        session = _make_session(
            user_name="vod_user",
            item_name="The Matrix",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",  # won't match "The Matrix"
                ecm_channel_number=408,    # prefix would be empty
            )
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: Tier 3 — fuzzy stream_name fallback
# ---------------------------------------------------------------------------


class TestTier3FuzzyFallback:
    """Tier 3: RapidFuzz token_set_ratio on ecm_stream_name against
    now_playing_item_name with the 0.85 threshold."""

    async def test_fuzzy_match_above_threshold_returns_user(self):
        """Stream name "CNN HD" vs item "CNN HD 1080p" — above 0.85."""
        session = _make_session(user_name="dan", item_name="CNN HD 1080p")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result == "dan"

    async def test_fuzzy_match_below_threshold_returns_none(self):
        """Unrelated item name scores below 0.85 — no match."""
        session = _make_session(item_name="The Office Season 3 Episode 14")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None

    async def test_fuzzy_match_on_vod_when_no_channel_args(self):
        """Movie session: no channel args, stream name fuzzy-matches title."""
        session = _make_session(
            user_name="movie_viewer",
            item_name="The Matrix 1999",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="The Matrix",
            )
        assert result == "movie_viewer"

    async def test_empty_item_name_cannot_match(self):
        """Session with None item_name cannot match any tier."""
        session = _make_session(item_name=None)
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: no-match conditions
# ---------------------------------------------------------------------------


class TestNoMatch:
    """The resolver returns ``None`` whenever the cache is empty, every
    session is below the fuzzy threshold, or no sessions exist."""

    async def test_empty_cache_returns_none(self):
        """An empty cache produces no match."""
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None

    async def test_all_sessions_idle_returns_none(self):
        """Sessions with item_name None cannot match anything."""
        idle = _make_session(item_name=None)
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[idle, idle])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        # IP matched but no session matched — unresolved counter incremented.
        assert _get_counter_value("user_attribution_unresolved_total", "plex") == 1.0
        assert _get_counter_value("user_attribution_resolved_total", "plex") == 0.0


# ---------------------------------------------------------------------------
# Behavior: tiebreak by most-recent last_activity_date
# ---------------------------------------------------------------------------


class TestMultipleMatchTiebreak:
    """Multiple matches → most-recent last_activity_date wins."""

    async def test_picks_most_recent_last_activity(self):
        """Two sessions playing the same channel; newer last_activity wins."""
        older = _make_session(
            session_id="sess-old",
            user_id="uid-old", user_name="old_viewer",
            item_name="408 | ESPN",
            last_activity=datetime(2025, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        newer = _make_session(
            session_id="sess-new",
            user_id="uid-new", user_name="new_viewer",
            item_name="408 | ESPN",
            last_activity=datetime(2025, 5, 16, 14, 0, 0, tzinfo=timezone.utc),
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[older, newer])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert result == "new_viewer"

    async def test_null_last_activity_loses_to_populated(self):
        """A session with last_activity_date=None should never beat one
        with an actual datetime."""
        no_ts = _make_session(
            session_id="sess-null",
            user_id="uid-null", user_name="null_viewer",
            item_name="408 | ESPN",
            last_activity=None,
        )
        with_ts = _make_session(
            session_id="sess-ts",
            user_id="uid-ts", user_name="ts_viewer",
            item_name="408 | ESPN",
            last_activity=datetime(2025, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[no_ts, with_ts])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
            )
        assert result == "ts_viewer"

    async def test_debug_log_fires_when_multiple_candidates(self, caplog):
        """When N > 1 candidates match, the resolver must log a DEBUG line
        surfacing the disambiguation."""
        older = _make_session(
            session_id="sess-old",
            user_name="old_user",
            item_name="408 | ESPN",
            last_activity=datetime(2025, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        newer = _make_session(
            session_id="sess-new",
            user_name="new_user",
            item_name="408 | ESPN",
            last_activity=datetime(2025, 5, 16, 14, 0, 0, tzinfo=timezone.utc),
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[older, newer])):
            with caplog.at_level(logging.DEBUG, logger="services.plex_resolver"):
                await plex_resolver.resolve_plex_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="US: ESPN FHD",
                    ecm_channel_name="ESPN",
                    ecm_channel_number=408,
                )
        assert any(
            "[PLEX]" in rec.getMessage()
            and "resolver" in rec.getMessage()
            and "candidates" in rec.getMessage()
            for rec in caplog.records
        ), f"expected disambiguation DEBUG; got {[r.getMessage() for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Behavior: hostname-based base URL DNS resolution
# ---------------------------------------------------------------------------


class TestHostnameBaseUrl:
    """When ``plex_base_url`` uses a hostname rather than an IP literal,
    the resolver falls back to ``socket.gethostbyname``."""

    async def test_hostname_resolves_to_matching_ip_attributes(self):
        """``plex.local`` resolves to ``192.168.1.20`` — matches the ECM
        session IP and the resolver proceeds to look up the session."""
        session = _make_session(user_name="eve", item_name="CNN HD")
        settings = _enabled_settings(base_url="https://plex.local:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver.socket, "gethostbyname",
                          return_value="192.168.1.20"):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result == "eve"

    async def test_hostname_resolves_to_non_matching_ip_returns_none(self):
        """``plex.local`` resolves to ``10.0.0.1`` — does NOT match."""
        cache_mock = AsyncMock(return_value=[_make_session(item_name="CNN HD")])
        settings = _enabled_settings(base_url="https://plex.local:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock), \
             patch.object(plex_resolver.socket, "gethostbyname",
                          return_value="10.0.0.1"):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_hostname_resolution_failure_logs_warn_and_returns_none(self, caplog):
        """Unresolvable hostname logs WARN with [PLEX] prefix and returns None."""
        settings = _enabled_settings(base_url="https://does-not-exist.invalid:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions", AsyncMock()) as cache_mock, \
             patch.object(plex_resolver.socket, "gethostbyname",
                          side_effect=socket.gaierror("Name resolution failed")):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                result = await plex_resolver.resolve_plex_user(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN HD",
                )
        assert result is None
        cache_mock.assert_not_awaited()
        assert any(
            "[PLEX]" in rec.getMessage() and "does-not-exist.invalid" in rec.getMessage()
            for rec in caplog.records
        )

    async def test_hostname_resolution_result_is_cached(self):
        """``socket.gethostbyname`` should be called at most once per
        process — the resolver caches the resolution."""
        session = _make_session(item_name="CNN HD")
        settings = _enabled_settings(base_url="https://plex.local:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver.socket, "gethostbyname",
                          return_value="192.168.1.20") as gethost:
            await plex_resolver.resolve_plex_user("192.168.1.20", "CNN HD")
            await plex_resolver.resolve_plex_user("192.168.1.20", "CNN HD")
            await plex_resolver.resolve_plex_user("192.168.1.20", "CNN HD")
        assert gethost.call_count == 1

    async def test_ip_literal_bypasses_dns(self):
        """IP literal in base URL bypasses ``socket.gethostbyname`` entirely."""
        session = _make_session(item_name="CNN HD")
        settings = _enabled_settings(base_url="http://192.168.1.20:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver.socket, "gethostbyname") as gethost:
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result == "alice"
        gethost.assert_not_called()

    async def test_failed_dns_poisons_cache_so_next_poll_skips_lookup(self):
        """After a DNS failure, subsequent polls return None without
        re-attempting the DNS lookup."""
        settings = _enabled_settings(base_url="https://bad.host.invalid:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions", AsyncMock(return_value=[])), \
             patch.object(plex_resolver.socket, "gethostbyname",
                          side_effect=socket.gaierror("NXDOMAIN")) as gethost:
            await plex_resolver.resolve_plex_user("192.168.1.20", "CNN HD")
            await plex_resolver.resolve_plex_user("192.168.1.20", "CNN HD")

        # DNS was only attempted once; the sentinel poisoned the cache.
        assert gethost.call_count == 1


# ---------------------------------------------------------------------------
# Behavior: empty / malformed configuration
# ---------------------------------------------------------------------------


class TestEmptyOrMalformedConfig:
    """Defensive coverage for misconfiguration."""

    async def test_empty_base_url_returns_none(self):
        """Empty ``plex_base_url`` cannot extract a hostname — returns None."""
        settings = _enabled_settings(base_url="")
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_malformed_base_url_returns_none(self):
        """A base URL that ``urlparse`` cannot extract a hostname from
        (e.g. only a scheme) returns ``None`` without raising."""
        settings = _enabled_settings(base_url="http://")
        cache_mock = AsyncMock(return_value=[_make_session()])
        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None
        cache_mock.assert_not_awaited()

    async def test_never_raises_on_exception(self):
        """The resolver must swallow exceptions and return None — the
        BandwidthTracker poll loop depends on this guarantee.

        We make ``get_settings`` raise to exercise the top-level catch-all
        without needing to reach inside the inner function.
        """
        with patch.object(plex_resolver, "get_settings", side_effect=RuntimeError("settings exploded")):
            # Must not raise; the top-level guard catches Exception
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
            )
        assert result is None


# ---------------------------------------------------------------------------
# Behavior: back-compat two-positional-arg call shape
# ---------------------------------------------------------------------------


class TestBackCompatCallShape:
    """Existing callers pass only (ip, stream_name) — must still work."""

    async def test_two_positional_args_still_works(self):
        """Pre-fix callers pass only (ip, stream_name) — fuzzy match
        must still function with no channel args."""
        session = _make_session(user_name="alice", item_name="CNN HD")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                "192.168.1.20", "CNN HD",
            )
        assert result == "alice"


# ---------------------------------------------------------------------------
# Behavior: multi-tier dedup (same session matched by multiple tiers)
# ---------------------------------------------------------------------------


class TestMultiTierDedup:
    """A session that matches both tier 1 and tier 2 should only appear
    once in the candidate list (no double-counting in tiebreak)."""

    async def test_session_matched_by_two_tiers_counted_once(self):
        """Session matches both tier 1 (channel name) and tier 2 (channel
        number). Should appear exactly once in the result, not twice."""
        session = _make_session(
            session_id="sess-dedup",
            user_name="dedup_user",
            item_name="408 | ESPN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        # Single match — no tiebreak needed, and result is the user_name.
        assert result == "dedup_user"
