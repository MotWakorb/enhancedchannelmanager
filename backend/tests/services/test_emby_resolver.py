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
    yield
    emby_resolver._reset_for_tests()


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
