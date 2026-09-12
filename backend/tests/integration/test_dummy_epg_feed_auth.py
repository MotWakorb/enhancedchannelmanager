"""Auth-enabled XMLTV fetch contract for Dispatcharr (GitHub #1000)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET

import pytest

from auth import settings as auth_settings
from auth.tokens import create_access_token
from cache import Cache
from main import app
from models import DummyEPGProfile, User
from routers import dummy_epg, epg


@pytest.fixture
def feed_client(async_client, monkeypatch, tmp_path):
    """Use the real app and auth loader with private, auth-enabled settings."""
    monkeypatch.setattr(auth_settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(auth_settings, "AUTH_CONFIG_FILE", tmp_path / "auth_settings.json")
    monkeypatch.setattr(auth_settings, "_cached_auth_settings", None)
    monkeypatch.setattr(auth_settings, "_cached_auth_settings_signature", None)
    settings = auth_settings.AuthSettings(require_auth=True, setup_complete=True)
    settings.jwt.secret_key = "synthetic-issue1000-signing-key-" * 2
    assert auth_settings.save_auth_settings(settings)
    assert auth_settings.get_auth_settings().require_auth is True
    assert auth_settings.get_auth_settings().setup_complete is True
    monkeypatch.setattr(dummy_epg, "cache", Cache())
    return async_client


@pytest.fixture
def feed_profile(test_session, monkeypatch):
    profile = DummyEPGProfile(
        name="Issue 1000 profile",
        enabled=True,
        name_source="channel",
        title_pattern=r"(?P<title>.+)",
        title_template="{title}",
        description_template="Showing {title}",
        event_timezone="UTC",
        tvg_id_template="ecm-{channel_number}",
    )
    profile.set_channel_group_ids([5])
    test_session.add(profile)
    test_session.commit()
    test_session.refresh(profile)
    # Only the external Dispatcharr boundary is mocked; assignment resolution,
    # template rendering, XML generation and the cache are real.
    dispatcharr = AsyncMock()
    dispatcharr.get_channels.return_value = {
        "results": [
            {
                "id": 101,
                "name": "Sports & News",
                "channel_number": 100,
                "channel_group_id": 5,
                "streams": [],
            },
            {
                "id": 102,
                "name": "Unassigned channel",
                "channel_number": 200,
                "channel_group_id": 6,
                "streams": [],
            },
        ],
        "next": None,
    }
    monkeypatch.setattr(dummy_epg, "get_client", lambda: dispatcharr)
    return profile


@pytest.fixture
def human_headers(feed_client, test_session):
    user = User(username="feed-operator", is_admin=True, is_active=True)
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    token = create_access_token(user.id, user.username, auth_epoch=user.auth_epoch)
    return {"Authorization": f"Bearer {token}"}


def _feed_path(profile, per_profile):
    # These are the routes emitted by services/api.ts's getDummyEPG*XmltvUrl
    # helpers and registered as source_type=xmltv by DummyEPGManagerSection.
    if per_profile:
        return str(app.url_path_for("get_xmltv_profile", profile_id=profile.id))
    return str(app.url_path_for("get_xmltv_all"))


def _assert_xmltv(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/xml"
    root = ET.fromstring(response.content)
    assert root.tag == "tv"
    assert [channel.attrib["id"] for channel in root.findall("channel")] == ["ecm-100"]
    assert root.findtext("channel/display-name") == "Sports & News"
    programme, = root.findall("programme")
    assert programme.attrib["channel"] == "ecm-100"
    assert programme.findtext("title") == "Sports & News"
    assert programme.findtext("desc") == "Showing Sports & News"
    assert programme.attrib["start"] < programme.attrib["stop"]


@pytest.mark.parametrize("per_profile", [False, True], ids=["combined", "profile"])
@pytest.mark.parametrize("suffix", ["", "/", "?refresh=1"])
async def test_anonymous_get_returns_generated_xmltv_with_auth_enabled(
    feed_client, feed_profile, per_profile, suffix
):
    path = _feed_path(feed_profile, per_profile)
    response = await feed_client.get(path + suffix, follow_redirects=True)
    _assert_xmltv(response)
    assert [item.status_code for item in response.history] == ([307] if suffix == "/" else [])
    assert response.url.path == path
    # Exercise the real cached-response branch as well as cold generation.
    _assert_xmltv(await feed_client.get(path))


@pytest.mark.parametrize("per_profile", [False, True], ids=["combined", "profile"])
async def test_registered_source_url_can_be_fetched_without_operator_credentials(
    feed_client, feed_profile, human_headers, monkeypatch, per_profile
):
    url = str(feed_client.base_url).rstrip("/") + _feed_path(feed_profile, per_profile)
    source = {"name": feed_profile.name, "source_type": "xmltv", "url": url, "is_active": True}
    dispatcharr = AsyncMock()
    dispatcharr.create_epg_source.side_effect = lambda data: {"id": 7, **data}
    monkeypatch.setattr(epg, "get_client", lambda: dispatcharr)

    registered = await feed_client.post("/api/epg/sources", json=source, headers=human_headers)
    assert registered.status_code == 200, registered.text
    dispatcharr.create_epg_source.assert_awaited_once_with(source)
    assert registered.json()["url"] == url
    # Dispatcharr receives only a URL, not the human's cookie or JWT.
    assert not feed_client.cookies
    _assert_xmltv(await feed_client.get(registered.json()["url"]))


@pytest.mark.parametrize("path", ["/api/dummy-epg/%78mltv", "/api/dummy-epg/xmltv/%31"])
async def test_percent_encoded_feed_path_uses_the_same_decoded_route(feed_client, feed_profile, path):
    assert feed_profile.id == 1
    _assert_xmltv(await feed_client.get(path))


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "TRACE"])
@pytest.mark.parametrize("path", ["/api/dummy-epg/xmltv", "/api/dummy-epg/xmltv/1"])
@pytest.mark.parametrize("suffix", ["", "/"])
async def test_feed_exemption_is_get_only(feed_client, method, path, suffix):
    response = await feed_client.request(method, path + suffix)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/dummy-epg/profiles"),
    ("GET", "/api/dummy-epg/profiles/1"),
    ("GET", "/api/dummy-epg/profiles/export/yaml"),
    ("POST", "/api/dummy-epg/profiles"),
    ("PATCH", "/api/dummy-epg/profiles/1"),
    ("DELETE", "/api/dummy-epg/profiles/1"),
    ("POST", "/api/dummy-epg/preview"),
    ("POST", "/api/dummy-epg/preview/batch"),
    ("POST", "/api/dummy-epg/generate"),
    ("GET", "/api/epg/sources"),
    ("POST", "/api/epg/sources"),
    ("GET", "/api/epg/grid"),
    ("GET", "/api/settings"),
    ("POST", "/api/settings/mcp-api-key"),
    ("GET", "/api/dummy-epg/xmltv.xml"),
    ("GET", "/api/dummy-epg/xmltv.gz"),
    ("GET", "/api/dummy-epg/xmltv/1.xml"),
    ("GET", "/api/dummy-epg/xmltv/1.gz"),
    ("GET", "/api/dummy-epg/xmltv/1/config"),
    ("GET", "/api/dummy-epg/xmltv/profiles"),
    ("GET", "/api/dummy-epg/xmltv/1extra"),
    ("GET", "/api/dummy-epg/xmltv//"),
    ("GET", "/api/dummy-epg/xmltv/1//"),
    ("GET", "/api/dummy-epg/xmltv//1"),
    ("GET", "/api/dummy-epg/XMLTV/1"),
    ("GET", "/api/dummy-epg/xmltv/-1"),
    ("GET", "/api/dummy-epg/xmltv/1%2fconfig"),
    ("GET", "/api/dummy-epg/xmltv/%2e%2e/profiles"),
    ("GET", "/api/dummy-epg/xmltv/%2531"),
    ("GET", "/api/dummy-epg/xmltv/1%0a"),
    ("GET", "/api/dummy-epg/xmltv/1%0d"),
    ("GET", "/api/dummy-epg/xmltv/1%09"),
    ("GET", "/api/dummy-epg/xmltv/1%3fconfig"),
    ("GET", "/api/dummy-epg/xmltv/1%23config"),
    ("GET", "/api/dummy-epg/xmltv/%EF%BC%91"),
])
async def test_neighboring_and_near_match_routes_require_auth(feed_client, method, path):
    response = await feed_client.request(method, path)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Not authenticated"}


async def test_unknown_profile_returns_handler_404(feed_client):
    response = await feed_client.get("/api/dummy-epg/xmltv/99999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Profile not found"}


async def test_authenticated_profile_management_still_works(feed_client, feed_profile, human_headers):
    response = await feed_client.get("/api/dummy-epg/profiles", headers=human_headers)
    assert response.status_code == 200, response.text
    assert [profile["id"] for profile in response.json()] == [feed_profile.id]
    response = await feed_client.patch(
        f"/api/dummy-epg/profiles/{feed_profile.id}",
        json={"name": "Renamed by operator"},
        headers=human_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed by operator"
    response = await feed_client.get(
        f"/api/dummy-epg/profiles/{feed_profile.id}", headers=human_headers
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed by operator"


async def test_feed_exemption_preserves_sidecar_client_key_refusal(feed_client, monkeypatch):
    key = "<synthetic-sidecar-client-key>"
    monkeypatch.setattr("main.get_settings", lambda: SimpleNamespace(mcp_api_key=key))
    response = await feed_client.get(
        "/api/dummy-epg/xmltv", headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 403
    assert "valid only at the sidecar" in response.json()["detail"]
