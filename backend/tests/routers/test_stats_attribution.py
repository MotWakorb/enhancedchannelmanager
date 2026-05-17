"""Tests for multi-source attribution surfaces on the Stats API.

bd-r5f0c.4 (parent epic bd-r5f0c) — extends the bd-fm23o Emby-only
suite (``test_stats_emby.py``) to cover Plex + Jellyfin attribution.

Covers the operator-visible read APIs that expose multi-source
attribution:

* ``/api/stats/channels`` (Active Channels): per-channel
  ``emby_user_name`` + ``plex_user_name`` + ``jellyfin_user_name``
  fields populated when the respective resolver attributes a client.
* ``/api/stats/watch-time``: ``attribution_source`` precedence is
  ``"emby" > "plex" > "jellyfin" > "dispatcharr"`` (bd-r5f0c.4
  spec).
* Existing ``emby_user_name`` field path is UNCHANGED — the
  bd-fm23o tests in ``test_stats_emby.py`` are the regression target
  for that and they must stay green.

The :func:`_pick_display_name_and_source` precedence function is
unit-tested below at the pure-function level (no HTTP round-trip
needed for the seven precedence ordered sub-cases).

Synthetic identities only — ``docs/security/threat_model_stats_v2.md``
§7.7.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import SessionTelemetry
from routers.stats import _pick_display_name_and_source
from services.emby_resolver import EmbyAttribution
from services.jellyfin_resolver import JellyfinAttribution


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _add_telemetry(
    session,
    *,
    user_id,
    channel_id: str,
    observed_at_ms: int,
    poll_interval_ms: int = 10_000,
    session_id: str | None = None,
    dispatcharr_username: str | None = None,
    emby_user_id: str | None = None,
    emby_user_name: str | None = None,
    plex_user_id: str | None = None,
    plex_user_name: str | None = None,
    jellyfin_user_id: str | None = None,
    jellyfin_user_name: str | None = None,
) -> None:
    """Insert one ``session_telemetry`` row with optional per-source fields."""
    session.add(
        SessionTelemetry(
            session_id=session_id
            or f"sess-u{user_id}-{channel_id}-{observed_at_ms}",
            observed_at=observed_at_ms,
            user_id=user_id,
            dispatcharr_username=dispatcharr_username,
            provider_id=None,
            channel_id=channel_id,
            bytes_delta=1000,
            buffer_event_count=0,
            poll_interval_ms=poll_interval_ms,
            stream_id=None,
            stream_name=None,
            emby_user_id=emby_user_id,
            emby_user_name=emby_user_name,
            plex_user_id=plex_user_id,
            plex_user_name=plex_user_name,
            jellyfin_user_id=jellyfin_user_id,
            jellyfin_user_name=jellyfin_user_name,
        )
    )


# ---------------------------------------------------------------------------
# _pick_display_name_and_source — pure-function precedence tests
# ---------------------------------------------------------------------------


class TestPickDisplayNameAndSourcePrecedence:
    """The precedence is Emby > Plex > Jellyfin > Dispatcharr (bd-r5f0c.4).

    Each case below pins one rung of the precedence ladder. Together
    they enumerate every spill case the W8 regression matrix will
    exercise end-to-end.
    """

    def test_emby_wins_when_present(self):
        name, source = _pick_display_name_and_source(
            emby_user_name="alice@emby",
            plex_user_name="alice@plex",
            jellyfin_user_name="alice@jellyfin",
            dispatcharr_username="alice@dispatcharr",
        )
        assert (name, source) == ("alice@emby", "emby")

    def test_plex_wins_over_jellyfin_and_dispatcharr(self):
        name, source = _pick_display_name_and_source(
            emby_user_name=None,
            plex_user_name="alice@plex",
            jellyfin_user_name="alice@jellyfin",
            dispatcharr_username="alice@dispatcharr",
        )
        assert (name, source) == ("alice@plex", "plex")

    def test_jellyfin_wins_over_dispatcharr(self):
        name, source = _pick_display_name_and_source(
            emby_user_name=None,
            plex_user_name=None,
            jellyfin_user_name="alice@jellyfin",
            dispatcharr_username="alice@dispatcharr",
        )
        assert (name, source) == ("alice@jellyfin", "jellyfin")

    def test_falls_back_to_dispatcharr_when_no_media_source(self):
        name, source = _pick_display_name_and_source(
            emby_user_name=None,
            plex_user_name=None,
            jellyfin_user_name=None,
            dispatcharr_username="alice@dispatcharr",
        )
        assert (name, source) == ("alice@dispatcharr", "dispatcharr")

    def test_dispatcharr_when_everything_none(self):
        name, source = _pick_display_name_and_source(
            emby_user_name=None,
            plex_user_name=None,
            jellyfin_user_name=None,
            dispatcharr_username=None,
        )
        # Legacy "Unknown viewer" case — both fields null.
        assert (name, source) == (None, "dispatcharr")

    def test_back_compat_signature_emby_only(self):
        """bd-fm23o callers that pass only ``emby_user_name`` +
        ``dispatcharr_username`` (no plex/jellyfin kwargs) still
        resolve correctly — the new kwargs default to ``None``."""
        name, source = _pick_display_name_and_source(
            emby_user_name="alice@emby",
            dispatcharr_username="alice@dispatcharr",
        )
        assert (name, source) == ("alice@emby", "emby")

    def test_back_compat_signature_dispatcharr_only(self):
        name, source = _pick_display_name_and_source(
            emby_user_name=None,
            dispatcharr_username="alice@dispatcharr",
        )
        assert (name, source) == ("alice@dispatcharr", "dispatcharr")


# ---------------------------------------------------------------------------
# Active Channels — per-channel attribution from each source
# ---------------------------------------------------------------------------


def _enabled_all_sources_settings():
    """A settings stub with every media source enabled — for the
    ``_enrich_channels_with_attribution`` path."""
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = "http://192.168.1.50:8096"
    settings.emby_api_key = "emby-key"
    settings.plex_enabled = True
    settings.plex_base_url = "http://192.168.1.50:32400"
    settings.plex_token = "plex-token"
    settings.jellyfin_enabled = True
    settings.jellyfin_base_url = "http://192.168.1.50:8096"
    settings.jellyfin_api_key = "jellyfin-key"
    return settings


def _enabled_plex_only_settings():
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    settings.plex_enabled = True
    settings.plex_base_url = "http://192.168.1.50:32400"
    settings.plex_token = "plex-token"
    settings.jellyfin_enabled = False
    settings.jellyfin_base_url = ""
    settings.jellyfin_api_key = ""
    return settings


def _all_disabled_settings():
    settings = MagicMock()
    settings.emby_enabled = False
    settings.emby_base_url = ""
    settings.emby_api_key = ""
    settings.plex_enabled = False
    settings.plex_base_url = ""
    settings.plex_token = ""
    settings.jellyfin_enabled = False
    settings.jellyfin_base_url = ""
    settings.jellyfin_api_key = ""
    return settings


class TestEnrichChannelsWithAttribution:
    """``_enrich_channels_with_attribution`` mutates the channels list
    in place, adding three nullable ``*_user_name`` fields per channel
    and per client."""

    @pytest.mark.asyncio
    async def test_all_three_sources_populate_when_all_enabled(self):
        """All three resolvers match the same channel/client → all
        three ``*_user_name`` fields populated on the channel AND on
        each client entry."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "US: ESPN FHD",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]

        emby_attr = EmbyAttribution(user_id="e-uid", user_name="emby-alice")
        jellyfin_attr = JellyfinAttribution(user_id="j-uid", user_name="jellyfin-alice")
        with patch("config.get_settings", return_value=_enabled_all_sources_settings()), \
             patch("services.emby_resolver.resolve_emby_user", AsyncMock(return_value=emby_attr)), \
             patch("services.plex_resolver.resolve_plex_user", AsyncMock(return_value="plex-alice")), \
             patch("services.jellyfin_resolver.resolve_jellyfin_user", AsyncMock(return_value=jellyfin_attr)):
            await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        assert ch["emby_user_name"] == "emby-alice"
        assert ch["plex_user_name"] == "plex-alice"
        assert ch["jellyfin_user_name"] == "jellyfin-alice"
        client = ch["clients"][0]
        assert client["emby_user_name"] == "emby-alice"
        assert client["plex_user_name"] == "plex-alice"
        assert client["jellyfin_user_name"] == "jellyfin-alice"

    @pytest.mark.asyncio
    async def test_only_enabled_sources_resolved(self):
        """Plex enabled, Emby + Jellyfin disabled → only the Plex
        field populates; the other two stay None (seeded but never
        resolved)."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "US: ESPN FHD",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]

        emby_mock = AsyncMock(return_value=None)
        jellyfin_mock = AsyncMock(return_value=None)
        with patch("config.get_settings", return_value=_enabled_plex_only_settings()), \
             patch("services.emby_resolver.resolve_emby_user", emby_mock), \
             patch("services.plex_resolver.resolve_plex_user", AsyncMock(return_value="plex-only")), \
             patch("services.jellyfin_resolver.resolve_jellyfin_user", jellyfin_mock):
            await _enrich_channels_with_attribution(channels)

        # Disabled-source resolvers must NOT be called.
        emby_mock.assert_not_awaited()
        jellyfin_mock.assert_not_awaited()

        ch = channels[0]
        assert ch["plex_user_name"] == "plex-only"
        assert ch["emby_user_name"] is None
        assert ch["jellyfin_user_name"] is None

    @pytest.mark.asyncio
    async def test_keys_seeded_when_all_sources_disabled(self):
        """Every source disabled → resolvers NOT called and the three
        nullable keys are STILL seeded on each channel + client so the
        TypeScript shape contract holds (frontend can rely on key
        presence)."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "ESPN HD",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]

        emby_mock = AsyncMock()
        plex_mock = AsyncMock()
        jellyfin_mock = AsyncMock()
        with patch("config.get_settings", return_value=_all_disabled_settings()), \
             patch("services.emby_resolver.resolve_emby_user", emby_mock), \
             patch("services.plex_resolver.resolve_plex_user", plex_mock), \
             patch("services.jellyfin_resolver.resolve_jellyfin_user", jellyfin_mock):
            await _enrich_channels_with_attribution(channels)

        emby_mock.assert_not_awaited()
        plex_mock.assert_not_awaited()
        jellyfin_mock.assert_not_awaited()

        ch = channels[0]
        assert "emby_user_name" in ch and ch["emby_user_name"] is None
        assert "plex_user_name" in ch and ch["plex_user_name"] is None
        assert "jellyfin_user_name" in ch and ch["jellyfin_user_name"] is None
        client = ch["clients"][0]
        assert "emby_user_name" in client and client["emby_user_name"] is None
        assert "plex_user_name" in client and client["plex_user_name"] is None
        assert "jellyfin_user_name" in client and client["jellyfin_user_name"] is None

    @pytest.mark.asyncio
    async def test_emby_only_path_unchanged_for_regression(self):
        """Emby-only enabled (the bd-fm23o baseline) → ``emby_user_name``
        populates exactly as before, and the new ``plex_user_name`` /
        ``jellyfin_user_name`` keys exist with None values (so frontend
        consumers can detect "this source is configured but no
        attribution" vs. absent key).

        This is the existing-API regression target — operators using
        the Emby-only setup today must see exactly the same shape as
        v0.17.1-0039.
        """
        from routers.stats import _enrich_channels_with_attribution

        emby_only = MagicMock()
        emby_only.emby_enabled = True
        emby_only.emby_base_url = "http://192.168.1.50:8096"
        emby_only.emby_api_key = "emby-key"
        emby_only.plex_enabled = False
        emby_only.jellyfin_enabled = False

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "ESPN HD",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]
        emby_attr = EmbyAttribution(user_id="e-uid", user_name="emby-charlie")
        with patch("config.get_settings", return_value=emby_only), \
             patch("services.emby_resolver.resolve_emby_user", AsyncMock(return_value=emby_attr)):
            await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        assert ch["emby_user_name"] == "emby-charlie"
        assert ch["plex_user_name"] is None
        assert ch["jellyfin_user_name"] is None


# ---------------------------------------------------------------------------
# /api/stats/watch-time — attribution_source reflects new precedence
# ---------------------------------------------------------------------------


class TestWatchTimePlexPrecedence:
    """``/api/stats/watch-time`` uses the new multi-source precedence
    (Emby > Plex > Jellyfin > Dispatcharr) when picking the display
    name + ``attribution_source``."""

    @pytest.mark.asyncio
    async def test_plex_wins_over_dispatcharr_when_emby_null(
        self, async_client, test_session
    ):
        """User has only Plex attribution (no Emby) → username surfaces
        as the Plex name and ``attribution_source = "plex"``."""
        observed_at = _ms(datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc))
        _add_telemetry(
            test_session,
            user_id=100,
            channel_id="ch-a",
            observed_at_ms=observed_at,
            dispatcharr_username="alice@dispatcharr",
            plex_user_name="alice@plex",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        assert response.status_code == 200
        body = response.json()
        rows = body["data"]
        assert len(rows) == 1
        assert rows[0]["user_id"] == 100
        assert rows[0]["username"] == "alice@plex"
        assert rows[0]["attribution_source"] == "plex"

    @pytest.mark.asyncio
    async def test_jellyfin_wins_over_dispatcharr_when_emby_and_plex_null(
        self, async_client, test_session
    ):
        observed_at = _ms(datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc))
        _add_telemetry(
            test_session,
            user_id=200,
            channel_id="ch-b",
            observed_at_ms=observed_at,
            dispatcharr_username="bob@dispatcharr",
            jellyfin_user_name="bob@jellyfin",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        body = response.json()
        rows = body["data"]
        assert len(rows) == 1
        assert rows[0]["username"] == "bob@jellyfin"
        assert rows[0]["attribution_source"] == "jellyfin"

    @pytest.mark.asyncio
    async def test_emby_still_wins_over_plex(self, async_client, test_session):
        """The existing bd-fm23o emby-wins behavior is preserved: a row
        with BOTH emby AND plex names surfaces the Emby name with
        ``attribution_source = "emby"``."""
        observed_at = _ms(datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc))
        _add_telemetry(
            test_session,
            user_id=300,
            channel_id="ch-c",
            observed_at_ms=observed_at,
            dispatcharr_username="charlie@dispatcharr",
            emby_user_id="e-uid",
            emby_user_name="charlie@emby",
            plex_user_name="charlie@plex",
        )
        test_session.commit()

        response = await async_client.get("/api/stats/watch-time")
        body = response.json()
        rows = body["data"]
        assert len(rows) == 1
        assert rows[0]["username"] == "charlie@emby"
        assert rows[0]["attribution_source"] == "emby"


# ---------------------------------------------------------------------------
# bd-r5f0c.9 multi-viewer: /api/stats/channels and the in-process
# _enrich_channels_with_attribution surface the FULL viewer list per
# source in addition to the legacy singular *_user_name field.
# ---------------------------------------------------------------------------


from services.plex_resolver import PlexAttribution


class TestEnrichChannelsMultiViewer:
    """``_enrich_channels_with_attribution`` populates per-channel and
    per-client ``<source>_viewers`` lists from the plural resolvers in
    addition to the legacy ``<source>_user_name`` field (most-recent
    viewer)."""

    @pytest.mark.asyncio
    async def test_two_emby_viewers_populate_viewers_list_and_legacy_field(self):
        """2 Emby viewers → channel.emby_viewers = [...{2 dicts}] AND
        channel.emby_user_name == position-0 viewer (most-recent)."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "US: ESPN FHD",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]

        emby_viewers = [
            EmbyAttribution(user_id="uid-bob", user_name="bob"),
            EmbyAttribution(user_id="uid-alice", user_name="alice"),
        ]
        with patch("config.get_settings", return_value=_enabled_plex_only_settings()), \
             patch("services.plex_resolver.resolve_plex_user", AsyncMock(return_value=None)), \
             patch("services.plex_resolver.resolve_plex_users", AsyncMock(return_value=[])):
            # Use a custom settings stub: Emby enabled, others off.
            emby_only = MagicMock()
            emby_only.emby_enabled = True
            emby_only.emby_base_url = "http://192.168.1.50:8096"
            emby_only.emby_api_key = "emby-key"
            emby_only.plex_enabled = False
            emby_only.jellyfin_enabled = False
            with patch("config.get_settings", return_value=emby_only), \
                 patch("services.emby_resolver.resolve_emby_user",
                       AsyncMock(return_value=emby_viewers[0])), \
                 patch("services.emby_resolver.resolve_emby_users",
                       AsyncMock(return_value=emby_viewers)):
                await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        # Legacy field: most-recent viewer (bob).
        assert ch["emby_user_name"] == "bob"
        # Multi-viewer field: full list.
        assert ch["emby_viewers"] == [
            {"user_id": "uid-bob", "user_name": "bob"},
            {"user_id": "uid-alice", "user_name": "alice"},
        ]
        # Per-client propagation: same list on each client.
        client = ch["clients"][0]
        assert client["emby_user_name"] == "bob"
        assert client["emby_viewers"] == [
            {"user_id": "uid-bob", "user_name": "bob"},
            {"user_id": "uid-alice", "user_name": "alice"},
        ]

    @pytest.mark.asyncio
    async def test_keys_seeded_with_empty_viewers_lists_when_no_match(self):
        """No match → channel.emby_viewers / plex_viewers /
        jellyfin_viewers all seeded as empty lists (NOT missing). The
        frontend can rely on key presence."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "ESPN",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]

        with patch("config.get_settings", return_value=_all_disabled_settings()):
            await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        assert ch["emby_viewers"] == []
        assert ch["plex_viewers"] == []
        assert ch["jellyfin_viewers"] == []
        client = ch["clients"][0]
        assert client["emby_viewers"] == []
        assert client["plex_viewers"] == []
        assert client["jellyfin_viewers"] == []

    @pytest.mark.asyncio
    async def test_plex_multi_viewer_populates_viewers_list(self):
        """Plex resolver returns 3 viewers → channel.plex_viewers = 3
        dicts. PlexAttribution.user_id is None today (resolver doesn't
        surface stable upstream IDs)."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "ESPN",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]

        plex_users = [
            PlexAttribution(user_name="alice@plex", user_id=None),
            PlexAttribution(user_name="bob@plex", user_id=None),
            PlexAttribution(user_name="carol@plex", user_id=None),
        ]
        with patch("config.get_settings", return_value=_enabled_plex_only_settings()), \
             patch("services.plex_resolver.resolve_plex_user",
                   AsyncMock(return_value="alice@plex")), \
             patch("services.plex_resolver.resolve_plex_users",
                   AsyncMock(return_value=plex_users)):
            await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        assert ch["plex_user_name"] == "alice@plex"
        assert ch["plex_viewers"] == [
            {"user_id": None, "user_name": "alice@plex"},
            {"user_id": None, "user_name": "bob@plex"},
            {"user_id": None, "user_name": "carol@plex"},
        ]

    @pytest.mark.asyncio
    async def test_legacy_singular_only_mocks_still_work_via_fallback(self):
        """bd-r5f0c.9 dual-call back-compat: when a test mocks ONLY the
        singular function (legacy bd-fm23o test seam) and the plural
        function is unmocked (returns empty in tests), the helper
        wraps the singular result into a 1-element viewers list.

        This preserves the existing single-viewer regression contract
        (tested elsewhere) — the multi-viewer column is still
        populated with the available data.
        """
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "ESPN HD",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]
        emby_only = MagicMock()
        emby_only.emby_enabled = True
        emby_only.emby_base_url = "http://192.168.1.50:8096"
        emby_only.emby_api_key = "emby-key"
        emby_only.plex_enabled = False
        emby_only.jellyfin_enabled = False
        emby_attr = EmbyAttribution(user_id="e-uid", user_name="emby-only-alice")
        # Mock ONLY singular. Plural unmocked → calls real resolver,
        # which has no cache mocked → returns []. The fallback in
        # _enrich_one_source wraps the singular result.
        with patch("config.get_settings", return_value=emby_only), \
             patch("services.emby_resolver.resolve_emby_user",
                   AsyncMock(return_value=emby_attr)):
            await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        # Legacy field populated.
        assert ch["emby_user_name"] == "emby-only-alice"
        # Multi-viewer field reflects the singular result wrapped.
        assert ch["emby_viewers"] == [
            {"user_id": "e-uid", "user_name": "emby-only-alice"},
        ]


class TestEnrichChannelsBackCompatPreserved:
    """Regression target: the existing single-viewer behavior is
    UNCHANGED for callers that haven't migrated. ``channel.emby_user_name``
    + ``channel.plex_user_name`` + ``channel.jellyfin_user_name`` keys
    continue to work exactly as in v0.17.1-0042."""

    @pytest.mark.asyncio
    async def test_singular_user_name_field_unchanged_for_two_viewers(self):
        """Even with 2 viewers in the plural list, the legacy
        ``emby_user_name`` field continues to be the most-recent
        viewer's name — pre-W5 frontend renderers see no shape
        change."""
        from routers.stats import _enrich_channels_with_attribution

        channels = [
            {
                "channel_id": "ch-1",
                "channel_name": "ESPN",
                "channel_number": 408,
                "stream_name": "ESPN",
                "clients": [{"ip_address": "192.168.1.50"}],
            }
        ]
        emby_only = MagicMock()
        emby_only.emby_enabled = True
        emby_only.emby_base_url = "http://192.168.1.50:8096"
        emby_only.emby_api_key = "emby-key"
        emby_only.plex_enabled = False
        emby_only.jellyfin_enabled = False
        plural = [
            EmbyAttribution(user_id="e-1", user_name="newest"),
            EmbyAttribution(user_id="e-2", user_name="older"),
        ]
        with patch("config.get_settings", return_value=emby_only), \
             patch("services.emby_resolver.resolve_emby_user",
                   AsyncMock(return_value=plural[0])), \
             patch("services.emby_resolver.resolve_emby_users",
                   AsyncMock(return_value=plural)):
            await _enrich_channels_with_attribution(channels)

        ch = channels[0]
        # Legacy contract: emby_user_name is the most-recent viewer.
        assert ch["emby_user_name"] == "newest"
        assert ch["clients"][0]["emby_user_name"] == "newest"
