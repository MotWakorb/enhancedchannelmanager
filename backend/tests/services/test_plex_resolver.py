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
    channel_name: str | None = None,
    parent_title: str | None = None,
    last_activity: object = _SENTINEL,
    remote_endpoint: str = "10.0.0.99",
) -> PlexSession:
    """Build a representative :class:`PlexSession` for assertions.

    Pass ``last_activity=None`` explicitly to get ``last_activity_date=None``.
    Omit the argument to get a sensible default datetime (avoids accidental
    None tiebreak in tests that don't care about the timestamp).

    bd-2zcvf: ``channel_name`` (Plex ``@grandparentTitle``) and
    ``parent_title`` (Plex ``@parentTitle``) default to ``None`` to keep
    existing tests unmodified — every pre-bd-2zcvf assertion stands
    against sessions where ``item_name`` is the only populated name
    surface. New Live TV tests pass them explicitly.
    """
    if last_activity is _SENTINEL:
        last_activity = datetime(2025, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    return PlexSession(
        session_id=session_id or f"sess-{user_name}",
        user_id=user_id,
        user_name=user_name,
        remote_endpoint=remote_endpoint,
        now_playing_item_name=item_name,
        now_playing_channel_name=channel_name,
        now_playing_parent_title=parent_title,
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
    """bd-mlcla: the strict IP gate is GONE. Off-server (NAT'd) traffic now
    strict-matches Tier 1/Tier 2 (and the strict EPG callsign tier); only
    Tier 3 fuzzy stays server-IP-gated. Anti-collapse moved to the
    reconciler."""

    async def test_off_server_ip_strict_match_still_attributes(self):
        """An off-server IP that matches Tier 1 (channel-name suffix) now
        attributes — the gate removal is the 'User #0' fix."""
        session = _make_session(item_name="408 | ESPN", user_name="alice")
        cache_mock = AsyncMock(return_value=[session])
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="172.18.0.1",  # NOT the Plex server (192.168.1.20)
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
            )
        assert result == "alice"
        cache_mock.assert_awaited()

    async def test_off_server_ip_does_not_run_tier3_fuzzy(self):
        """bd-ost8o guard: off-server traffic must NOT fuzzy-match."""
        # "ESPN" fuzzy-matches "ESPN HD 1080p" but there is no Tier-1 hit
        # (no ecm_channel_name) — server IP matches via Tier 3, off-server
        # does not.
        session = _make_session(item_name="ESPN HD 1080p", user_name="dan")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            off_server = await plex_resolver.resolve_plex_users(
                ecm_session_ip="172.18.0.1",  # off-server → Tier 3 disabled
                ecm_stream_name="ESPN HD",
            )
            on_server = await plex_resolver.resolve_plex_users(
                ecm_session_ip="192.168.1.20",  # server IP → Tier 3 enabled
                ecm_stream_name="ESPN HD",
            )
        assert off_server == []
        assert [u.user_name for u in on_server] == ["dan"]

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
# bd-2zcvf: Tier 1 channel_name / parent_title matching (Plex Live TV)
# ---------------------------------------------------------------------------


class TestTier1LiveTvChannelMatch:
    """bd-2zcvf: Plex Live TV reports the PROGRAM in ``@title`` and the
    CHANNEL in ``@grandparentTitle`` (and sometimes ``@parentTitle``).
    The resolver's tier-1 must compare ``ecm_channel_name`` against all
    three candidate session fields — without this, an operator's
    "User #N" symptom in Stats is unfixable because the program title
    never matches the channel name.

    These tests parallel ``TestTier1ChannelNameMatch`` above, replacing
    the assumed ``item_name="<number> | <channel>"`` shape with the
    real Plex Live TV shape ``item_name="<program>"`` /
    ``channel_name="<channel>"``.
    """

    async def test_channel_name_matches_grandparent_title_whole(self):
        """Plex Live TV: ``item_name="Saturday Night Live"`` (the program),
        ``channel_name="NBC"`` (the channel). ECM channel_name="NBC" must
        match the grandparent_title (channel_name field on PlexSession).
        Pre-bd-2zcvf this returned None — the channel never got
        compared.
        """
        session = _make_session(
            user_id="uid-bob", user_name="bob",
            item_name="Saturday Night Live",
            channel_name="NBC",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: NBC East",
                ecm_channel_name="NBC",
            )
        assert result == "bob"

    async def test_channel_name_matches_grandparent_title_with_pipe_prefix(self):
        """ECM channel_name="2.1 | NBC" (operator's pipe-prefix shape) vs
        Plex ``channel_name="NBC"``. bd-r5f0c.11's ECM-side suffix parse
        carries over to the new channel_name candidate."""
        session = _make_session(
            user_name="alice",
            item_name="Anderson Cooper 360",
            channel_name="NBC",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: NBC East",
                ecm_channel_name="2.1 | NBC",
            )
        assert result == "alice"

    async def test_channel_name_matches_parent_title(self):
        """Some Plex DVR setups put the channel name on ``@parentTitle``
        (e.g., when the grandparent is a generic "Live TV" entity).
        bd-2zcvf includes parent_title as the third candidate field so
        these setups still resolve."""
        session = _make_session(
            user_name="dave",
            item_name="The Tonight Show",
            channel_name=None,
            parent_title="NBC",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="NBC",
            )
        assert result == "dave"

    async def test_channel_name_grandparent_pipe_suffix_match(self):
        """``channel_name="408 | ESPN"`` carries the pipe-prefix on
        Plex's side (some upstream tuners do this); ECM channel_name
        ``"ESPN"`` must still match by parsing the suffix."""
        session = _make_session(
            user_name="eve",
            item_name="SportsCenter",
            channel_name="408 | ESPN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
            )
        assert result == "eve"

    async def test_clean_session_with_empty_new_fields_unchanged(self):
        """Sessions with ``channel_name=None`` and ``parent_title=None``
        (the pre-bd-2zcvf shape — VOD or older Plex setups) still match
        via ``item_name`` exactly as before. Regression target: no
        existing match path regresses when the new fields are absent.
        """
        session = _make_session(
            user_name="frank",
            item_name="408 | CBS",
            channel_name=None,
            parent_title=None,
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="CBS",
            )
        assert result == "frank"

    async def test_channel_name_matches_none_of_three_fields_returns_none(self):
        """Defensive: when ECM channel_name doesn't match item, channel,
        OR parent, tier-1 fails and (with no number / fuzzy match)
        the resolver returns None."""
        session = _make_session(
            user_name="grace",
            item_name="The Daily Show",
            channel_name="Comedy Central",
            parent_title="Late Night",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="completely unrelated",
                ecm_channel_name="ESPN",
            )
        assert result is None


# ---------------------------------------------------------------------------
# bd-2zcvf: Tier 3 fuzzy fallback on channel_name / parent_title
# ---------------------------------------------------------------------------


class TestTier3FuzzyFallbackLiveTv:
    """bd-2zcvf: tier-3 fuzzy matching now considers ``channel_name`` and
    ``parent_title`` in addition to ``item_name``. This parallels the
    Emby resolver's tier-3 which already compares stream_name against
    both ``now_playing_item_name`` and ``now_playing_channel_name``.
    Without this, a Live TV session whose ``@title`` is the program
    would fall through tier 3 entirely when the operator's stream_name
    fuzzy-matches the channel but not the program."""

    async def test_fuzzy_stream_name_matches_grandparent_title(self):
        """Stream name "CNN HD 1080p" fuzzy-matches grandparent "CNN HD"
        well above the 0.85 threshold; ``item_name="Anderson Cooper 360"``
        does not. Pre-bd-2zcvf this returned None."""
        session = _make_session(
            user_name="harvey",
            item_name="Anderson Cooper 360",
            channel_name="CNN HD",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD 1080p",
            )
        assert result == "harvey"

    async def test_fuzzy_stream_name_matches_parent_title(self):
        """Same as above but the channel name landed on
        ``@parentTitle`` (alternate Plex DVR shape)."""
        session = _make_session(
            user_name="irene",
            item_name="Some Talk Show",
            channel_name=None,
            parent_title="FOX News HD",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="FOX News HD 720",
            )
        assert result == "irene"


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

    async def test_hostname_resolves_to_non_matching_ip_still_strict_matches(self):
        """bd-mlcla: ``plex.local`` resolves to ``10.0.0.1`` (off-server),
        but with the gate removed Tier 1 still matches and the cache is
        consulted."""
        cache_mock = AsyncMock(return_value=[_make_session(item_name="CNN HD", user_name="eve")])
        settings = _enabled_settings(base_url="https://plex.local:32400")

        with patch.object(plex_resolver, "get_settings", return_value=settings), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock), \
             patch.object(plex_resolver.socket, "gethostbyname",
                          return_value="10.0.0.1"):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
                ecm_channel_name="CNN HD",
            )
        assert result == "eve"
        cache_mock.assert_awaited()

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


# ---------------------------------------------------------------------------
# bd-r5f0c.9 multi-viewer: resolve_plex_users returns the FULL list of
# matched sessions sorted most-recent first, NOT just the tiebreak winner.
# Fixes the PO-reported bug where ECM showed only one user when multiple
# Plex viewers were watching the same channel via the Plex server proxy.
# ---------------------------------------------------------------------------


class TestMultiViewer:
    """Plural :func:`resolve_plex_users` captures every viewer that matched
    any tier. The singular wrapper :func:`resolve_plex_user` continues
    to return the most-recent viewer's user_name (str) for back-compat.
    """

    async def test_two_viewers_same_channel_returns_both(self):
        """Two Plex sessions on the same channel → resolve_plex_users
        returns BOTH attributions, most-recent first."""
        older = _make_session(
            session_id="sess-old",
            user_id="uid-alice", user_name="alice",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        newer = _make_session(
            session_id="sess-new",
            user_id="uid-bob", user_name="bob",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc),
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[older, newer])):
            users = await plex_resolver.resolve_plex_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
                ecm_channel_number=408,
            )
        assert len(users) == 2, f"expected 2 viewers; got {len(users)}"
        assert users[0].user_name == "bob"
        assert users[1].user_name == "alice"

    async def test_three_viewers_same_channel_returns_all_three(self):
        """Three Plex sessions on the same channel."""
        s1 = _make_session(
            session_id="s1", user_id="u1", user_name="alice",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            session_id="s2", user_id="u2", user_name="bob",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
        )
        s3 = _make_session(
            session_id="s3", user_id="u3", user_name="charlie",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc),
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[s1, s2, s3])):
            users = await plex_resolver.resolve_plex_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ESPN",
                ecm_channel_name="ESPN",
            )
        assert [u.user_name for u in users] == ["charlie", "bob", "alice"]

    async def test_single_viewer_returns_one_element_list(self):
        """The common case: one viewer on the channel → list of 1."""
        only = _make_session(
            user_id="uid-only", user_name="solo",
            item_name="408 | ESPN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[only])):
            users = await plex_resolver.resolve_plex_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: ESPN",
                ecm_channel_name="ESPN",
            )
        assert len(users) == 1
        assert users[0].user_name == "solo"

    async def test_no_match_returns_empty_list(self):
        """No tier matches → empty list (NOT None, NOT exception)."""
        irrelevant = _make_session(
            user_name="x", item_name="Unrelated Show",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[irrelevant])):
            users = await plex_resolver.resolve_plex_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="CNN HD",
                ecm_channel_name="ESPN",
            )
        assert users == []

    async def test_off_server_ip_plural_strict_matches(self):
        """bd-mlcla: the plural variant also drops the gate."""
        cache_mock = AsyncMock(return_value=[_make_session(item_name="408 | ESPN", user_name="alice")])
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions", cache_mock):
            users = await plex_resolver.resolve_plex_users(
                ecm_session_ip="172.18.0.1",  # NOT the Plex server
                ecm_stream_name="US: ESPN FHD",
                ecm_channel_name="ESPN",
            )
        assert [u.user_name for u in users] == ["alice"]
        cache_mock.assert_awaited()

    async def test_singular_wrapper_returns_most_recent_user_name(self):
        """Back-compat target: the legacy singular wrapper still returns
        the most-recent viewer's user_name (string)."""
        older = _make_session(
            session_id="sess-old", user_id="uid-old", user_name="old_user",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        newer = _make_session(
            session_id="sess-new", user_id="uid-new", user_name="new_user",
            item_name="408 | ESPN",
            last_activity=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc),
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[older, newer])):
            singular = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ESPN",
                ecm_channel_name="ESPN",
            )
        assert singular == "new_user"

    async def test_metric_counter_increments_per_viewer(self):
        """bd-r5f0c.9 + W6 semantic: the
        ``user_attribution_resolved_total{source="plex"}`` counter
        increments PER VIEWER. 3 matched viewers = 3 increments."""
        s1 = _make_session(session_id="s1", user_id="u1", user_name="a", item_name="408 | ESPN")
        s2 = _make_session(session_id="s2", user_id="u2", user_name="b", item_name="408 | ESPN")
        s3 = _make_session(session_id="s3", user_id="u3", user_name="c", item_name="408 | ESPN")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[s1, s2, s3])):
            users = await plex_resolver.resolve_plex_users(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ESPN",
                ecm_channel_name="ESPN",
            )
        assert len(users) == 3
        assert _get_counter_value("user_attribution_resolved_total", "plex") == 3.0


# ---------------------------------------------------------------------------
# bd-r5f0c.10: forensic no-match WARN. Same shape as the Emby resolver
# helper — see TestNoMatchDiagnostic in test_emby_resolver.py for the
# rationale. Fires once per (ip, channel) per 60 s when sessions exist
# and the IP short-circuit passed but no tier accepted any session.
# ---------------------------------------------------------------------------


class TestNoMatchDiagnostic:
    """Plex forensic no-match WARN. Mirrors the Emby resolver semantics:
    only fires when IP matched + sessions non-empty + no tier matched."""

    @staticmethod
    def _no_match_records(records):
        return [
            r for r in records
            if "[PLEX-RESOLVER] no-match diagnostic" in r.getMessage()
        ]

    async def test_emits_warn_when_sessions_exist_and_no_tier_matches(self, caplog):
        """Sessions in cache but no tier accepts any of them → one WARN."""
        session = _make_session(
            user_name="alice",
            item_name="999 | NotTheChannel",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                users = await plex_resolver.resolve_plex_users(
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
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                users = await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        assert users == []
        assert self._no_match_records(caplog.records) == []

    async def test_no_emit_when_match_succeeds(self, caplog):
        """A successful resolve must not emit the no-match WARN."""
        session = _make_session(user_name="alice", item_name="408 | ESPN")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                users = await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        assert len(users) == 1
        assert self._no_match_records(caplog.records) == []

    async def test_off_server_ip_no_strict_match_still_emits_diagnostic(self, caplog):
        """bd-mlcla: off-server IP with sessions but no strict match emits
        the forensic WARN (operators need it to debug a NAT'd 'User #0')."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                users = await plex_resolver.resolve_plex_users(
                    ecm_session_ip="172.18.0.1",  # NOT the Plex server
                    ecm_stream_name="ESPN",
                    ecm_channel_name="ESPN",
                )
        assert users == []
        assert len(self._no_match_records(caplog.records)) == 1

    async def test_rate_limit_within_window(self, caplog):
        """Two no-match calls for same (ip, channel) within 60 s → one WARN."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
                await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1

    async def test_rate_limit_per_channel_independence(self, caplog):
        """Different channels for the same IP each get their own WARN."""
        session = _make_session(item_name="UnrelatedShow")
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CBS",
                    ecm_channel_name="CBS",
                )
                await plex_resolver.resolve_plex_users(
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

        monkeypatch.setattr(plex_resolver.time, "monotonic", fake_monotonic)

        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
                fake_now[0] += 61.0
                await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="CNN",
                    ecm_channel_name="CNN",
                )
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 2

    async def test_diagnostic_payload_carries_channel_name_and_parent_title(self, caplog):
        """bd-2zcvf: the no-match diagnostic must surface the new Plex
        Live TV channel-name fields (``channel_name`` from
        ``@grandparentTitle``, ``parent_title`` from ``@parentTitle``)
        alongside the existing ``item_name`` fields. Without these in
        the forensic payload, an operator inspecting a no-match log
        line for a Live TV session would only see the PROGRAM title in
        ``item_name`` and have no way to verify which session
        corresponds to which ECM channel.
        """
        session = _make_session(
            user_name="alice",
            item_name="Saturday Night Live",
            channel_name="NBC",
            parent_title="Season 50",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                users = await plex_resolver.resolve_plex_users(
                    ecm_session_ip="192.168.1.20",
                    ecm_stream_name="ESPN HD",
                    ecm_channel_name="ESPN",
                )
        assert users == []
        warns = self._no_match_records(caplog.records)
        assert len(warns) == 1
        msg = warns[0].getMessage()
        # bd-2zcvf: the new fields must appear in the payload.
        assert "'channel_name': 'NBC'" in msg, (
            f"expected channel_name in payload, got: {msg}"
        )
        assert "'channel_name_norm': 'nbc'" in msg
        assert "'parent_title': 'Season 50'" in msg, (
            f"expected parent_title in payload, got: {msg}"
        )
        # And the existing item_name fields still surface.
        assert "'item_name': 'Saturday Night Live'" in msg

    async def test_truncation_cap_thirty(self, caplog):
        """bd-r5f0c.11: 35 sessions in cache → forensic payload includes
        exactly 30 entries (the bumped cap), not the original 10 and
        not the full 35.
        """
        sessions = [
            _make_session(
                session_id=f"sess-{idx:02d}",
                user_id=f"uid-{idx:02d}",
                user_name=f"user{idx:02d}",
                item_name=f"{900 + idx} | NotTheChannel{idx}",
            )
            for idx in range(35)
        ]
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=sessions)):
            with caplog.at_level(logging.WARNING, logger="services.plex_resolver"):
                users = await plex_resolver.resolve_plex_users(
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
# Behavior: bd-r5f0c.11 — ECM-side pipe-prefix tolerance in Tier-1
# ---------------------------------------------------------------------------


class TestECMPipePrefixTolerance:
    """bd-r5f0c.11: when an operator imports channels with the pipe-prefix
    display format leaked into the ECM channel_name itself (e.g.
    "109 | CNN"), tier-1 must match against Plex sessions even though
    ECM is carrying the upstream's display format. The fix adds two
    compares against the parsed ECM suffix (alongside the existing
    compares against ecm_channel_name as a whole), gated on the ECM
    name actually containing a pipe so clean ECM names are unaffected.
    """

    async def test_ecm_prefix_plex_prefix(self):
        """ECM channel_name "109 | CNN" vs Plex item_name "109 | CNN"."""
        session = _make_session(
            user_id="uid-mw", user_name="MotWakorb",
            item_name="109 | CNN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="109 | CNN",
            )
        assert result == "MotWakorb"

    async def test_ecm_prefix_plex_clean(self):
        """ECM channel_name "109 | CNN" vs Plex item_name "CNN".
        Only the new compare (ecm_suffix vs whole item_name) reaches
        this case.
        """
        session = _make_session(
            user_name="MotWakorb",
            item_name="CNN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="109 | CNN",
            )
        assert result == "MotWakorb"

    async def test_ecm_clean_plex_prefix(self):
        """ECM channel_name "CNN" vs Plex item_name "109 | CNN".
        Regression — existing pre-bd-r5f0c.11 path.
        """
        session = _make_session(
            user_name="MotWakorb",
            item_name="109 | CNN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="CNN",
            )
        assert result == "MotWakorb"

    async def test_ecm_clean_plex_clean(self):
        """ECM channel_name "CNN" vs Plex item_name "CNN". Trivial
        whole-string regression check.
        """
        session = _make_session(
            user_name="MotWakorb",
            item_name="CNN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: CNN HD",
                ecm_channel_name="CNN",
            )
        assert result == "MotWakorb"

    async def test_ecm_prefix_no_match(self):
        """ECM channel_name "109 | CNN" vs Plex item_name "NESN".
        Tolerance must NOT cause false positives.
        """
        session = _make_session(
            user_name="alice",
            item_name="NESN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="totally unrelated stream name",
                ecm_channel_name="109 | CNN",
            )
        assert result is None

    async def test_ecm_prefix_case_insensitive(self):
        """ECM channel_name "109 | cnn" (lowercase) vs Plex item_name
        "109 | CNN". NFC + lower + strip normalization on both sides.
        """
        session = _make_session(
            user_name="case_user",
            item_name="109 | CNN",
        )
        with patch.object(plex_resolver, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored — tier 1 wins",
                ecm_channel_name="109 | cnn",
            )
        assert result == "case_user"


# ---------------------------------------------------------------------------
# bd-ma6r3: EPG cross-reference tier
# ---------------------------------------------------------------------------


from plex_client import PlexEpgEntry  # noqa: E402 — group bd-ma6r3 additions


def _epg_entry(
    *,
    grandparent_title: str,
    channel_call_sign: str,
) -> PlexEpgEntry:
    """Build a representative EPG entry covering "now" with livetv protocol.

    The time window is intentionally wide so window-filter mechanics
    don't interfere with the matcher tests — those are exercised in
    :mod:`tests.unit.test_plex_client`.
    """
    return PlexEpgEntry(
        grandparent_title=grandparent_title,
        channel_call_sign=channel_call_sign,
        channel_identifier="x",
        channel_title="x",
        channel_vcn="x",
        begins_at=datetime(2026, 5, 18, 0, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
        protocol="livetv",
    )


def _make_live_session(
    *,
    session_id: str | None = None,
    user_name: str = "MotWakorb",
    grandparent_title: str | None = None,
    item_name: str | None = None,
) -> PlexSession:
    """Build a Plex Live TV session — ``is_live=True`` is the EPG-tier gate."""
    if last_activity := datetime(2026, 5, 18, 19, 0, 0, tzinfo=timezone.utc):
        pass
    return PlexSession(
        session_id=session_id or f"sess-{user_name}",
        user_id="uid-x",
        user_name=user_name,
        remote_endpoint="10.0.0.99",
        now_playing_item_name=item_name,
        now_playing_channel_name=grandparent_title,
        now_playing_parent_title=None,
        last_activity_date=last_activity,
        is_live=True,
    )


class TestEpgTierLiveTvChannelMatch:
    """bd-ma6r3: when Plex Live TV's ``/status/sessions`` surfaces the
    program in ``grandparentTitle`` but the channel is NOT carried on
    any session field, the EPG tier cross-references the EPG snapshot
    to recover the channel ``channelCallSign``.

    PO's actual symptom from v0.17.1-0056: ECM channel
    ``'8.1 | NBC: KGW Portland'``, Plex Live TV session with
    ``grandparentTitle='MLB Baseball'``, EPG entry mapping
    ``MLB Baseball`` → ``channelCallSign='8.1 | NBC: KGW Portland'`` →
    must resolve to ``MotWakorb``.
    """

    async def test_po_symptom_resolves_via_epg(self):
        """The exact PO symptom: program-only session + EPG carries the
        channel callsign → resolver picks up MotWakorb."""
        session = _make_live_session(
            user_name="MotWakorb",
            item_name="Brewers at Cubs",
            grandparent_title="MLB Baseball",
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="8.1 | NBC: KGW Portland",
            ),
        ]
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="US: 8.1 NBC",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        assert result == "MotWakorb"
        # Per-viewer metric incremented exactly once.
        assert _get_counter_value(
            "user_attribution_resolved_total", "plex",
        ) == 1.0

    async def test_epg_tier_matches_pipe_suffix_against_clean_ecm(self):
        """EPG channelCallSign has pipe-prefix, ECM channel_name is the
        clean right-hand part — same pipe-suffix tolerance as Tier 1."""
        session = _make_live_session(
            user_name="alice",
            item_name="Brewers at Cubs",
            grandparent_title="MLB Baseball",
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="8.1 | NBC: KGW Portland",
            ),
        ]
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="NBC: KGW Portland",
            )
        assert result == "alice"

    async def test_epg_tier_returns_none_when_no_epg_grandparent_match(self):
        """EPG snapshot contains airings but none match the session's
        ``grandparent_title`` — resolver returns None."""
        session = _make_live_session(
            user_name="bob",
            item_name="Some Episode",
            grandparent_title="Unknown Show",
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="8.1 | NBC: KGW Portland",
            ),
        ]
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="totally unrelated",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        assert result is None

    async def test_epg_tier_returns_none_when_callsign_does_not_match(self):
        """EPG entry's grandparent matches the session, but its
        ``channelCallSign`` is for a different ECM channel — returns
        None. Defends against false positives when the same program
        airs on multiple channels in the EPG."""
        session = _make_live_session(
            user_name="carol",
            item_name="Brewers at Cubs",
            grandparent_title="MLB Baseball",
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="3.2 | TVW",  # different channel
            ),
        ]
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        assert result is None

    async def test_epg_tier_does_not_run_when_no_live_sessions(self):
        """Bead acceptance criterion: resolver MUST NOT fetch EPG when
        there are zero live sessions to disambiguate. Idle Plex
        deployments must not incur an EPG fetch storm."""
        session = _make_session(  # not live — VOD
            user_name="alice",
            item_name="Dune",
        )
        epg_mock = AsyncMock(return_value=[])
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          epg_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        assert result is None
        # The EPG cache was NEVER consulted because no live session
        # needed disambiguation.
        epg_mock.assert_not_awaited()

    async def test_epg_tier_does_not_run_when_no_ecm_channel_name(self):
        """When the resolver is invoked without ``ecm_channel_name``,
        the EPG tier has no candidate to test against — must not fetch
        EPG."""
        session = _make_live_session(
            user_name="bob",
            item_name="Brewers at Cubs",
            grandparent_title="MLB Baseball",
        )
        epg_mock = AsyncMock(return_value=[])
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          epg_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="unrelated",
                # No ecm_channel_name supplied.
            )
        assert result is None
        epg_mock.assert_not_awaited()

    async def test_epg_tier_does_not_run_when_tier1_already_matched(self):
        """If Tier 1 already matched (e.g. session.now_playing_channel_name
        is itself the channel), the EPG tier has no unmatched session
        to look up and must not fetch EPG — avoids redundant upstream
        load on the steady-state happy path."""
        session = _make_live_session(
            user_name="MotWakorb",
            item_name="Some Program",
            # grandparent_title IS the channel here (typical NBC, ESPN
            # straight-up channel name) — Tier 1 will match.
            grandparent_title="NBC",
        )
        epg_mock = AsyncMock(return_value=[])
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          epg_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="NBC",
            )
        assert result == "MotWakorb"
        epg_mock.assert_not_awaited()

    async def test_epg_tier_skipped_for_non_live_sessions(self):
        """A VOD session whose grandparent happens to match an EPG entry
        must not trigger the EPG tier — only ``is_live=True`` sessions
        opt in. Defends against accidental cross-attribution where a
        Plex user is watching a recording of an episode whose show
        title coincidentally airs on a channel."""
        # VOD session (is_live=False) playing an episode named
        # "MLB Baseball" — the EPG carries a real Live TV "MLB Baseball"
        # entry on 8.1, but the VOD session must NOT be matched against
        # ECM's 8.1 channel.
        session = _make_session(
            user_name="dave",
            item_name="Brewers Documentary",
            channel_name="MLB Baseball",  # grandparentTitle present
        )
        # Force is_live=False — _make_session defaults to that.
        assert session.is_live is False
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="8.1 | NBC: KGW Portland",
            ),
        ]
        epg_mock = AsyncMock(return_value=epg)
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          epg_mock):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        # VOD session must NOT match via the EPG tier; the EPG cache
        # is never consulted because the only session in the list is
        # not live.
        assert result is None
        epg_mock.assert_not_awaited()

    async def test_epg_tier_handles_empty_snapshot(self):
        """When the EPG cache returns ``[]`` (Plex idle / cold-start
        fetch failure), the tier matches nothing — no exception, no
        attribution change."""
        session = _make_live_session(
            user_name="alice",
            item_name="Some Episode",
            grandparent_title="MLB Baseball",
        )
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=[])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        assert result is None

    async def test_epg_tier_picks_correct_callsign_when_multiple_share_grandparent(self):
        """The same program (e.g., "MLB Baseball") can air on multiple
        channels in the EPG snapshot. The tier must pick the entry whose
        ``channelCallSign`` matches the operator's ECM channel_name and
        ignore the others."""
        session = _make_live_session(
            user_name="ed",
            item_name="Brewers at Cubs",
            grandparent_title="MLB Baseball",
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="3.2 | TVW",
            ),
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="8.1 | NBC: KGW Portland",
            ),
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="679 | Milwaukee Brewers",
            ),
        ]
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="679 | Milwaukee Brewers",
            )
        assert result == "ed"

    async def test_epg_tier_diagnostic_logged_when_no_match(
        self, caplog,
    ):
        """The no-match WARN diagnostic must include the EPG attempt
        payload so a PO can see why the tier didn't help. The line
        carries ``epg=`` with snapshot_count + candidates_by_session."""
        import logging
        session = _make_live_session(
            user_name="frank",
            item_name="Brewers at Cubs",
            grandparent_title="MLB Baseball",
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="3.2 | TVW",  # wrong channel
            ),
        ]
        with caplog.at_level(logging.WARNING), \
             patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        warn_lines = [
            r.getMessage() for r in caplog.records
            if "[PLEX-RESOLVER] no-match" in r.getMessage()
        ]
        assert len(warn_lines) == 1
        # The diagnostic must surface the EPG attempt so a PO can see
        # which channelCallSigns the tier considered.
        assert "epg=" in warn_lines[0]
        assert "snapshot_count" in warn_lines[0]
        assert "3.2 | TVW" in warn_lines[0]

    async def test_epg_tier_handles_session_with_grandparent_falls_back_to_title(self):
        """When ``now_playing_channel_name`` is None but
        ``now_playing_item_name`` carries the program (some Plex Live
        TV surfaces leave grandparent blank), the EPG tier falls back
        to looking up by title."""
        session = PlexSession(
            session_id="sess-grace",
            user_id="uid-grace",
            user_name="grace",
            remote_endpoint="10.0.0.99",
            now_playing_item_name="MLB Baseball",
            now_playing_channel_name=None,  # grandparent absent
            now_playing_parent_title=None,
            last_activity_date=datetime(2026, 5, 18, 19, 0, 0, tzinfo=timezone.utc),
            is_live=True,
        )
        epg = [
            _epg_entry(
                grandparent_title="MLB Baseball",
                channel_call_sign="8.1 | NBC: KGW Portland",
            ),
        ]
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])), \
             patch.object(plex_resolver, "maybe_get_cached_plex_epg",
                          AsyncMock(return_value=epg)):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="ignored",
                ecm_channel_name="8.1 | NBC: KGW Portland",
            )
        assert result == "grace"


# ---------------------------------------------------------------------------
# Behavior: bd-f68c8 — ECM pipe-prefix parsed as Tier-2 channel-number
# ---------------------------------------------------------------------------


class TestEcmPipePrefixAsChannelNumber:
    """bd-f68c8: when ``ecm_channel_number`` is ``None`` AND
    ``ecm_channel_name`` starts with a numeric pipe-prefix, the resolver
    must parse the prefix and use it as a Tier-2 candidate against the
    session's ``item_name`` pipe-prefix (Plex's session-side numeric
    channel surface).

    Plex's Tier-2 differs from Emby/Jellyfin only in shape: Plex
    sessions carry the number as the left-hand side of the ``item_name``
    pipe-prefix (e.g. ``"2.1 | ABC: WBAY Green Bay"``), not as a
    dedicated ``channel_number`` field. The candidate-resolution helper
    is shared across all three resolvers.
    """

    async def test_ecm_prefix_numeric_matches_session_item_prefix(self):
        """ECM ``"2.1 | ABC: WBAY Green Bay"`` + ``channel_number=None``
        + Plex session ``item_name="2.1 | Saturday Night Live"``: the
        ECM-side prefix ``"2.1"`` matches the Plex item-name prefix
        ``"2.1"`` even though the suffix names diverge."""
        session = _make_session(
            user_id="plex-uid-mw", user_name="MotWakorb",
            item_name="2.1 | Saturday Night Live",
        )
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="WI | Green Bay | ABC 2 WBAY",
                ecm_channel_name="2.1 | ABC: WBAY Green Bay",
                ecm_channel_number=None,
            )
        assert result == "MotWakorb"

    async def test_ecm_prefix_non_numeric_does_not_false_match(self):
        """Word-prefixed ECM channel names ("News | CNN") must not act
        as Tier-2 candidates against arbitrary session pipe-prefixes."""
        session = _make_session(
            user_name="ghost",
            # Pick a session item whose pipe-prefix is "news" so that
            # a non-numeric ECM prefix WOULD match if the gate were
            # missing. With the gate, no match.
            item_name="news | CNN",
        )
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="News | NotCNN",
                ecm_channel_number=None,
            )
        # Tier-1 also won't match — "notcnn" vs "cnn" — and Tier-2 is
        # gated, so no false attribution.
        assert result is None

    async def test_no_pipe_prefix_in_ecm_name_skips_prefix_path(self):
        """ECM channel_name ``"ESPN"`` (no pipe) leaves Tier-2 disabled."""
        session = _make_session(
            user_name="ghost",
            item_name="408 | NotESPN",
        )
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="ESPN",
                ecm_channel_number=None,
            )
        assert result is None

    async def test_explicit_ecm_channel_number_takes_precedence_over_prefix(self):
        """Caller-supplied ``ecm_channel_number`` wins — the parsed
        prefix is not consulted when an explicit number is present."""
        session = _make_session(
            user_name="explicit_num_user",
            item_name="408 | NotESPN",
        )
        with patch.object(plex_resolver, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_resolver, "get_cached_plex_sessions",
                          AsyncMock(return_value=[session])):
            result = await plex_resolver.resolve_plex_user(
                ecm_session_ip="192.168.1.20",
                ecm_stream_name="something_unrelated",
                ecm_channel_name="99 | NotESPN",
                ecm_channel_number=408,
            )
        assert result == "explicit_num_user"
