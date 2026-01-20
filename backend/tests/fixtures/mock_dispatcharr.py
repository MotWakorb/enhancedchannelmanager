"""
Mock Dispatcharr API responses using respx.

Provides fixtures and utilities for mocking Dispatcharr HTTP endpoints
in integration tests, avoiding external dependencies.
"""
import json
from datetime import datetime
from typing import Optional, Any
import pytest
import respx
from httpx import Response

# Default base URL for mock API
MOCK_DISPATCHARR_URL = "http://dispatcharr.test"


# =============================================================================
# Sample Data Generators
# =============================================================================

def make_channel(
    id: int = 1,
    name: str = None,
    channel_number: float = None,
    channel_group: int = None,
    **kwargs
) -> dict:
    """Generate a sample channel dict."""
    return {
        "id": id,
        "uuid": f"channel-uuid-{id}",
        "name": name or f"Test Channel {id}",
        "channel_number": channel_number or float(id),
        "channel_group": channel_group or 1,
        "streams": kwargs.get("streams", [id]),
        "logo": kwargs.get("logo"),
        "epg_data": kwargs.get("epg_data"),
        "is_active": kwargs.get("is_active", True),
        **{k: v for k, v in kwargs.items() if k not in ["streams", "logo", "epg_data", "is_active"]}
    }


def make_channel_group(
    id: int = 1,
    name: str = None,
    **kwargs
) -> dict:
    """Generate a sample channel group dict."""
    return {
        "id": id,
        "name": name or f"Group {id}",
        "channel_count": kwargs.get("channel_count", 0),
        **{k: v for k, v in kwargs.items() if k != "channel_count"}
    }


def make_stream(
    id: int = 1,
    name: str = None,
    url: str = None,
    m3u_account: int = None,
    **kwargs
) -> dict:
    """Generate a sample stream dict."""
    return {
        "id": id,
        "name": name or f"Test Stream {id}",
        "url": url or f"http://stream.test/{id}.m3u8",
        "m3u_account": m3u_account or 1,
        "channel_group_name": kwargs.get("channel_group_name", "Test Group"),
        "is_active": kwargs.get("is_active", True),
        **{k: v for k, v in kwargs.items() if k not in ["channel_group_name", "is_active"]}
    }


def make_m3u_account(
    id: int = 1,
    name: str = None,
    **kwargs
) -> dict:
    """Generate a sample M3U account dict."""
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "id": id,
        "name": name or f"M3U Account {id}",
        "server_url": kwargs.get("server_url", f"http://m3u{id}.test/playlist.m3u"),
        "is_active": kwargs.get("is_active", True),
        "account_type": kwargs.get("account_type", "m3u"),
        "channel_groups": kwargs.get("channel_groups", []),
        "updated_at": kwargs.get("updated_at", now),
        "last_refresh": kwargs.get("last_refresh", now),
        **{k: v for k, v in kwargs.items() if k not in ["server_url", "is_active", "account_type", "channel_groups", "updated_at", "last_refresh"]}
    }


def make_epg_source(
    id: int = 1,
    name: str = None,
    **kwargs
) -> dict:
    """Generate a sample EPG source dict."""
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "id": id,
        "name": name or f"EPG Source {id}",
        "url": kwargs.get("url", f"http://epg{id}.test/guide.xml"),
        "source_type": kwargs.get("source_type", "xmltv"),
        "is_active": kwargs.get("is_active", True),
        "updated_at": kwargs.get("updated_at", now),
        "last_updated": kwargs.get("last_updated", now),
        **{k: v for k, v in kwargs.items() if k not in ["url", "source_type", "is_active", "updated_at", "last_updated"]}
    }


def make_epg_data(
    id: int = 1,
    channel_id: str = None,
    **kwargs
) -> dict:
    """Generate a sample EPG data entry."""
    return {
        "id": id,
        "channel_id": channel_id or f"epg-channel-{id}",
        "name": kwargs.get("name", f"EPG Data {id}"),
        "display_name": kwargs.get("display_name", f"EPG Channel {id}"),
        "epg_source": kwargs.get("epg_source", 1),
        **{k: v for k, v in kwargs.items() if k not in ["name", "display_name", "epg_source"]}
    }


def make_epg_program(
    channel_uuid: str = None,
    title: str = None,
    start: str = None,
    stop: str = None,
    **kwargs
) -> dict:
    """Generate a sample EPG program for grid."""
    now = datetime.utcnow()
    return {
        "channel_uuid": channel_uuid or "channel-uuid-1",
        "title": title or "Test Program",
        "start": start or now.isoformat() + "Z",
        "stop": stop or (now.replace(hour=now.hour + 1)).isoformat() + "Z",
        "description": kwargs.get("description", "Test program description"),
        **{k: v for k, v in kwargs.items() if k != "description"}
    }


def make_channel_stats(
    active_channels: int = 0,
    total_clients: int = 0,
    channels: list = None,
    **kwargs
) -> dict:
    """Generate sample channel stats."""
    return {
        "active_channels": active_channels,
        "total_clients": total_clients,
        "channels": channels or [],
        **kwargs
    }


def make_logo(
    id: int = 1,
    name: str = None,
    url: str = None,
    **kwargs
) -> dict:
    """Generate a sample logo dict."""
    return {
        "id": id,
        "name": name or f"Logo {id}",
        "url": url or f"http://logos.test/logo{id}.png",
        **kwargs
    }


# =============================================================================
# Paginated Response Helper
# =============================================================================

def paginated_response(
    results: list,
    page: int = 1,
    page_size: int = 100,
    base_url: str = "/api/test/"
) -> dict:
    """Wrap results in a paginated response structure."""
    total = len(results)
    start = (page - 1) * page_size
    end = start + page_size
    page_results = results[start:end]

    has_next = end < total
    has_prev = page > 1

    return {
        "count": total,
        "next": f"{base_url}?page={page + 1}&page_size={page_size}" if has_next else None,
        "previous": f"{base_url}?page={page - 1}&page_size={page_size}" if has_prev else None,
        "results": page_results,
    }


# =============================================================================
# Mock Router Class
# =============================================================================

class MockDispatcharrRouter:
    """
    Configurable mock router for Dispatcharr API endpoints.

    Usage:
        router = MockDispatcharrRouter()
        router.set_channels([make_channel(1), make_channel(2)])
        router.set_m3u_accounts([make_m3u_account(1)])

        with respx.mock:
            router.setup_routes()
            # Run tests that use DispatcharrClient
    """

    def __init__(self, base_url: str = MOCK_DISPATCHARR_URL):
        self.base_url = base_url.rstrip("/")

        # Data stores
        self._channels: list = []
        self._channel_groups: list = []
        self._streams: list = []
        self._m3u_accounts: list = []
        self._epg_sources: list = []
        self._epg_data: list = []
        self._epg_programs: list = []
        self._logos: list = []
        self._channel_stats: dict = make_channel_stats()
        self._stream_profiles: list = []
        self._channel_profiles: list = []

        # Auth tokens
        self._access_token = "mock-access-token"
        self._refresh_token = "mock-refresh-token"

    # -------------------------------------------------------------------------
    # Data Setters
    # -------------------------------------------------------------------------

    def set_channels(self, channels: list) -> "MockDispatcharrRouter":
        """Set the channels data."""
        self._channels = channels
        return self

    def set_channel_groups(self, groups: list) -> "MockDispatcharrRouter":
        """Set the channel groups data."""
        self._channel_groups = groups
        return self

    def set_streams(self, streams: list) -> "MockDispatcharrRouter":
        """Set the streams data."""
        self._streams = streams
        return self

    def set_m3u_accounts(self, accounts: list) -> "MockDispatcharrRouter":
        """Set the M3U accounts data."""
        self._m3u_accounts = accounts
        return self

    def set_epg_sources(self, sources: list) -> "MockDispatcharrRouter":
        """Set the EPG sources data."""
        self._epg_sources = sources
        return self

    def set_epg_data(self, data: list) -> "MockDispatcharrRouter":
        """Set the EPG data entries."""
        self._epg_data = data
        return self

    def set_epg_programs(self, programs: list) -> "MockDispatcharrRouter":
        """Set the EPG programs for grid."""
        self._epg_programs = programs
        return self

    def set_logos(self, logos: list) -> "MockDispatcharrRouter":
        """Set the logos data."""
        self._logos = logos
        return self

    def set_channel_stats(self, stats: dict) -> "MockDispatcharrRouter":
        """Set the channel stats."""
        self._channel_stats = stats
        return self

    def set_stream_profiles(self, profiles: list) -> "MockDispatcharrRouter":
        """Set the stream profiles."""
        self._stream_profiles = profiles
        return self

    def set_channel_profiles(self, profiles: list) -> "MockDispatcharrRouter":
        """Set the channel profiles."""
        self._channel_profiles = profiles
        return self

    # -------------------------------------------------------------------------
    # Route Setup
    # -------------------------------------------------------------------------

    def setup_routes(self, router: respx.Router = None) -> respx.Router:
        """Set up all mock routes on the given respx router.

        If no router provided, uses respx.mock (must be within respx.mock context).
        """
        if router is None:
            router = respx.mock

        # Authentication
        router.post(f"{self.base_url}/api/accounts/token/").mock(
            return_value=Response(200, json={
                "access": self._access_token,
                "refresh": self._refresh_token,
            })
        )
        router.post(f"{self.base_url}/api/accounts/token/refresh/").mock(
            return_value=Response(200, json={"access": self._access_token})
        )

        # Channels
        router.get(f"{self.base_url}/api/channels/channels/").mock(
            side_effect=self._handle_get_channels
        )
        router.get(url__regex=rf"{self.base_url}/api/channels/channels/(\d+)/$").mock(
            side_effect=self._handle_get_channel
        )
        router.get(url__regex=rf"{self.base_url}/api/channels/channels/(\d+)/streams/$").mock(
            side_effect=self._handle_get_channel_streams
        )
        router.post(f"{self.base_url}/api/channels/channels/").mock(
            side_effect=self._handle_create_channel
        )
        router.patch(url__regex=rf"{self.base_url}/api/channels/channels/(\d+)/$").mock(
            side_effect=self._handle_update_channel
        )
        router.delete(url__regex=rf"{self.base_url}/api/channels/channels/(\d+)/$").mock(
            return_value=Response(204)
        )
        router.post(f"{self.base_url}/api/channels/channels/assign/").mock(
            return_value=Response(200, json={"success": True})
        )

        # Channel Groups
        router.get(f"{self.base_url}/api/channels/groups/").mock(
            return_value=Response(200, json=self._channel_groups)
        )
        router.post(f"{self.base_url}/api/channels/groups/").mock(
            side_effect=self._handle_create_channel_group
        )
        router.patch(url__regex=rf"{self.base_url}/api/channels/groups/(\d+)/$").mock(
            side_effect=self._handle_update_channel_group
        )
        router.delete(url__regex=rf"{self.base_url}/api/channels/groups/(\d+)/$").mock(
            return_value=Response(204)
        )

        # Streams
        router.get(f"{self.base_url}/api/channels/streams/").mock(
            side_effect=self._handle_get_streams
        )
        router.get(url__regex=rf"{self.base_url}/api/channels/streams/(\d+)/$").mock(
            side_effect=self._handle_get_stream
        )
        router.post(f"{self.base_url}/api/channels/streams/by-ids/").mock(
            side_effect=self._handle_get_streams_by_ids
        )
        router.get(f"{self.base_url}/api/channels/streams/groups/").mock(
            return_value=Response(200, json=self._channel_groups)
        )

        # M3U Accounts
        router.get(f"{self.base_url}/api/m3u/accounts/").mock(
            return_value=Response(200, json=self._m3u_accounts)
        )
        router.get(url__regex=rf"{self.base_url}/api/m3u/accounts/(\d+)/$").mock(
            side_effect=self._handle_get_m3u_account
        )
        router.post(url__regex=rf"{self.base_url}/api/m3u/refresh/(\d+)/$").mock(
            return_value=Response(200, json={"success": True, "message": "Refresh initiated"})
        )
        router.post(f"{self.base_url}/api/m3u/refresh/").mock(
            return_value=Response(200, json={"success": True, "message": "Refresh initiated"})
        )

        # EPG Sources
        router.get(f"{self.base_url}/api/epg/sources/").mock(
            return_value=Response(200, json=self._epg_sources)
        )
        router.get(url__regex=rf"{self.base_url}/api/epg/sources/(\d+)/$").mock(
            side_effect=self._handle_get_epg_source
        )
        router.post(f"{self.base_url}/api/epg/import/").mock(
            return_value=Response(200, json={"success": True, "message": "Import initiated"})
        )

        # EPG Data
        router.get(f"{self.base_url}/api/epg/epgdata/").mock(
            side_effect=self._handle_get_epg_data
        )
        router.get(f"{self.base_url}/api/epg/grid/").mock(
            return_value=Response(200, json={"data": self._epg_programs})
        )

        # Logos
        router.get(f"{self.base_url}/api/channels/logos/").mock(
            side_effect=self._handle_get_logos
        )
        router.post(f"{self.base_url}/api/channels/logos/").mock(
            side_effect=self._handle_create_logo
        )

        # Stats
        router.get(f"{self.base_url}/proxy/ts/status").mock(
            return_value=Response(200, json=self._channel_stats)
        )
        router.get(url__regex=rf"{self.base_url}/proxy/ts/status/(\d+)").mock(
            return_value=Response(200, json={})
        )

        # Profiles
        router.get(f"{self.base_url}/api/core/streamprofiles/").mock(
            return_value=Response(200, json=self._stream_profiles)
        )
        router.get(f"{self.base_url}/api/channels/profiles/").mock(
            return_value=Response(200, json=self._channel_profiles)
        )

        return router

    # -------------------------------------------------------------------------
    # Request Handlers
    # -------------------------------------------------------------------------

    def _handle_get_channels(self, request):
        """Handle GET /api/channels/channels/"""
        params = dict(request.url.params)
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 100))
        search = params.get("search")
        channel_group = params.get("channel_group")

        filtered = self._channels
        if search:
            filtered = [c for c in filtered if search.lower() in c["name"].lower()]
        if channel_group:
            filtered = [c for c in filtered if c.get("channel_group") == int(channel_group)]

        return Response(200, json=paginated_response(
            filtered, page, page_size, "/api/channels/channels/"
        ))

    def _handle_get_channel(self, request):
        """Handle GET /api/channels/channels/{id}/"""
        channel_id = int(request.url.path.split("/")[-2])
        channel = next((c for c in self._channels if c["id"] == channel_id), None)
        if channel:
            return Response(200, json=channel)
        return Response(404, json={"detail": "Not found"})

    def _handle_get_channel_streams(self, request):
        """Handle GET /api/channels/channels/{id}/streams/"""
        channel_id = int(request.url.path.split("/")[-3])
        channel = next((c for c in self._channels if c["id"] == channel_id), None)
        if channel:
            stream_ids = channel.get("streams", [])
            streams = [s for s in self._streams if s["id"] in stream_ids]
            return Response(200, json=streams)
        return Response(404, json={"detail": "Not found"})

    def _handle_create_channel(self, request):
        """Handle POST /api/channels/channels/"""
        data = json.loads(request.content)
        new_id = max((c["id"] for c in self._channels), default=0) + 1
        channel = make_channel(id=new_id, **data)
        self._channels.append(channel)
        return Response(201, json=channel)

    def _handle_update_channel(self, request):
        """Handle PATCH /api/channels/channels/{id}/"""
        channel_id = int(request.url.path.split("/")[-2])
        data = json.loads(request.content)
        channel = next((c for c in self._channels if c["id"] == channel_id), None)
        if channel:
            channel.update(data)
            return Response(200, json=channel)
        return Response(404, json={"detail": "Not found"})

    def _handle_create_channel_group(self, request):
        """Handle POST /api/channels/groups/"""
        data = json.loads(request.content)
        new_id = max((g["id"] for g in self._channel_groups), default=0) + 1
        group = make_channel_group(id=new_id, **data)
        self._channel_groups.append(group)
        return Response(201, json=group)

    def _handle_update_channel_group(self, request):
        """Handle PATCH /api/channels/groups/{id}/"""
        group_id = int(request.url.path.split("/")[-2])
        data = json.loads(request.content)
        group = next((g for g in self._channel_groups if g["id"] == group_id), None)
        if group:
            group.update(data)
            return Response(200, json=group)
        return Response(404, json={"detail": "Not found"})

    def _handle_get_streams(self, request):
        """Handle GET /api/channels/streams/"""
        params = dict(request.url.params)
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 100))
        search = params.get("search")
        m3u_account = params.get("m3u_account")

        filtered = self._streams
        if search:
            filtered = [s for s in filtered if search.lower() in s["name"].lower()]
        if m3u_account:
            filtered = [s for s in filtered if s.get("m3u_account") == int(m3u_account)]

        return Response(200, json=paginated_response(
            filtered, page, page_size, "/api/channels/streams/"
        ))

    def _handle_get_stream(self, request):
        """Handle GET /api/channels/streams/{id}/"""
        stream_id = int(request.url.path.split("/")[-2])
        stream = next((s for s in self._streams if s["id"] == stream_id), None)
        if stream:
            return Response(200, json=stream)
        return Response(404, json={"detail": "Not found"})

    def _handle_get_streams_by_ids(self, request):
        """Handle POST /api/channels/streams/by-ids/"""
        data = json.loads(request.content)
        ids = data.get("ids", [])
        streams = [s for s in self._streams if s["id"] in ids]
        return Response(200, json=streams)

    def _handle_get_m3u_account(self, request):
        """Handle GET /api/m3u/accounts/{id}/"""
        account_id = int(request.url.path.split("/")[-2])
        account = next((a for a in self._m3u_accounts if a["id"] == account_id), None)
        if account:
            return Response(200, json=account)
        return Response(404, json={"detail": "Not found"})

    def _handle_get_epg_source(self, request):
        """Handle GET /api/epg/sources/{id}/"""
        source_id = int(request.url.path.split("/")[-2])
        source = next((s for s in self._epg_sources if s["id"] == source_id), None)
        if source:
            return Response(200, json=source)
        return Response(404, json={"detail": "Not found"})

    def _handle_get_epg_data(self, request):
        """Handle GET /api/epg/epgdata/"""
        # Return flat list (new Dispatcharr format)
        return Response(200, json=self._epg_data)

    def _handle_get_logos(self, request):
        """Handle GET /api/channels/logos/"""
        params = dict(request.url.params)
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 100))
        search = params.get("search")

        filtered = self._logos
        if search:
            filtered = [l for l in filtered if search.lower() in l["name"].lower()]

        return Response(200, json=paginated_response(
            filtered, page, page_size, "/api/channels/logos/"
        ))

    def _handle_create_logo(self, request):
        """Handle POST /api/channels/logos/"""
        data = json.loads(request.content)
        new_id = max((l["id"] for l in self._logos), default=0) + 1
        logo = make_logo(id=new_id, **data)
        self._logos.append(logo)
        return Response(201, json=logo)


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture
def mock_dispatcharr_router():
    """Fixture that provides a fresh MockDispatcharrRouter for each test."""
    return MockDispatcharrRouter()


@pytest.fixture
def mock_dispatcharr(mock_dispatcharr_router):
    """Fixture that sets up mock routes within respx.mock context.

    Usage:
        def test_something(mock_dispatcharr):
            mock_dispatcharr.set_channels([make_channel(1)])
            # Test code that uses DispatcharrClient
    """
    with respx.mock:
        mock_dispatcharr_router.setup_routes()
        yield mock_dispatcharr_router


@pytest.fixture
def mock_dispatcharr_with_data(mock_dispatcharr_router):
    """Fixture with pre-populated sample data.

    Provides a basic set of channels, groups, streams, M3U accounts, and EPG sources.
    """
    # Set up sample data
    groups = [
        make_channel_group(1, "Sports"),
        make_channel_group(2, "News"),
        make_channel_group(3, "Entertainment"),
    ]
    channels = [
        make_channel(1, "ESPN", channel_number=100.0, channel_group=1, streams=[1, 2]),
        make_channel(2, "CNN", channel_number=200.0, channel_group=2, streams=[3]),
        make_channel(3, "HBO", channel_number=300.0, channel_group=3, streams=[4, 5]),
    ]
    streams = [
        make_stream(1, "ESPN HD", m3u_account=1),
        make_stream(2, "ESPN SD", m3u_account=1),
        make_stream(3, "CNN Live", m3u_account=1),
        make_stream(4, "HBO East", m3u_account=2),
        make_stream(5, "HBO West", m3u_account=2),
    ]
    m3u_accounts = [
        make_m3u_account(1, "Provider A"),
        make_m3u_account(2, "Provider B"),
    ]
    epg_sources = [
        make_epg_source(1, "EPG Guide 1"),
        make_epg_source(2, "EPG Guide 2"),
    ]

    mock_dispatcharr_router.set_channel_groups(groups)
    mock_dispatcharr_router.set_channels(channels)
    mock_dispatcharr_router.set_streams(streams)
    mock_dispatcharr_router.set_m3u_accounts(m3u_accounts)
    mock_dispatcharr_router.set_epg_sources(epg_sources)

    with respx.mock:
        mock_dispatcharr_router.setup_routes()
        yield mock_dispatcharr_router
