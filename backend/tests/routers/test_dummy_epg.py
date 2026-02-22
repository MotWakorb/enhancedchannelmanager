"""
Unit tests for Dummy EPG router endpoints.

Tests: Profile CRUD (with group-based channel assignment), preview, and XMLTV output.
Mocks: _fetch_all_channels, get_client, preview_pipeline, generate_xmltv, cache.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models import DummyEPGProfile


def _create_profile(session, **overrides):
    """Helper to create a DummyEPGProfile with sensible defaults."""
    defaults = {
        "name": "Test Profile",
        "enabled": True,
        "name_source": "channel",
        "stream_index": 1,
        "title_pattern": r"(?P<title>.+)",
        "time_pattern": None,
        "date_pattern": None,
        "title_template": "{title}",
        "description_template": "Showing {title}",
        "event_timezone": "US/Eastern",
        "program_duration": 180,
        "tvg_id_template": "ecm-{channel_number}",
        "include_date_tag": False,
        "include_live_tag": False,
        "include_new_tag": False,
    }
    defaults.update(overrides)
    profile = DummyEPGProfile(**defaults)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


# =============================================================================
# Profile CRUD
# =============================================================================


class TestListProfiles:
    """Tests for GET /api/dummy-epg/profiles."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, async_client):
        """Returns empty list when no profiles exist."""
        response = await async_client.get("/api/dummy-epg/profiles")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_returns_profiles_with_group_count(self, async_client, test_session):
        """Returns all profiles with group_count."""
        profile = _create_profile(test_session, name="Sports Profile")
        profile.set_channel_group_ids([5, 10])
        test_session.commit()

        response = await async_client.get("/api/dummy-epg/profiles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Sports Profile"
        assert data[0]["group_count"] == 2
        assert data[0]["channel_group_ids"] == [5, 10]

    @pytest.mark.asyncio
    async def test_returns_multiple_profiles(self, async_client, test_session):
        """Returns all profiles."""
        _create_profile(test_session, name="Profile A")
        _create_profile(test_session, name="Profile B")

        response = await async_client.get("/api/dummy-epg/profiles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {p["name"] for p in data}
        assert names == {"Profile A", "Profile B"}


class TestCreateProfile:
    """Tests for POST /api/dummy-epg/profiles."""

    @pytest.mark.asyncio
    async def test_creates_profile(self, async_client):
        """Creates a new profile with all fields."""
        with patch("routers.dummy_epg.cache"):
            response = await async_client.post("/api/dummy-epg/profiles", json={
                "name": "New Profile",
                "enabled": True,
                "name_source": "stream",
                "stream_index": 2,
                "title_pattern": r"(?P<title>.+)",
                "title_template": "{title}",
                "description_template": "Showing {title}",
                "event_timezone": "US/Eastern",
                "program_duration": 120,
                "tvg_id_template": "ecm-{channel_number}",
                "include_live_tag": True,
            })

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Profile"
        assert data["name_source"] == "stream"
        assert data["stream_index"] == 2
        assert data["program_duration"] == 120
        assert data["include_live_tag"] is True
        assert "id" in data

    @pytest.mark.asyncio
    async def test_creates_profile_with_substitution_pairs(self, async_client):
        """Creates a profile with substitution pairs."""
        with patch("routers.dummy_epg.cache"):
            response = await async_client.post("/api/dummy-epg/profiles", json={
                "name": "Subs Profile",
                "substitution_pairs": [
                    {"find": "HD", "replace": "", "is_regex": False, "enabled": True},
                    {"find": r"\s+", "replace": " ", "is_regex": True, "enabled": True},
                ],
            })

        assert response.status_code == 200
        data = response.json()
        assert len(data["substitution_pairs"]) == 2
        assert data["substitution_pairs"][0]["find"] == "HD"

    @pytest.mark.asyncio
    async def test_creates_profile_with_channel_group_ids(self, async_client):
        """Creates a profile with channel_group_ids."""
        with patch("routers.dummy_epg.cache"):
            response = await async_client.post("/api/dummy-epg/profiles", json={
                "name": "Groups Profile",
                "channel_group_ids": [5, 10, 15],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["channel_group_ids"] == [5, 10, 15]

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self, async_client, test_session):
        """Returns 409 when name already exists."""
        _create_profile(test_session, name="Existing")

        response = await async_client.post("/api/dummy-epg/profiles", json={
            "name": "Existing",
        })
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_creates_with_defaults(self, async_client):
        """Creates a profile with only required name field."""
        with patch("routers.dummy_epg.cache"):
            response = await async_client.post("/api/dummy-epg/profiles", json={
                "name": "Minimal Profile",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Minimal Profile"
        assert data["enabled"] is True
        assert data["name_source"] == "channel"
        assert data["stream_index"] == 1
        assert data["program_duration"] == 180
        assert data["event_timezone"] == "US/Eastern"
        assert data["channel_group_ids"] == []


class TestGetProfile:
    """Tests for GET /api/dummy-epg/profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_returns_profile_with_group_ids(self, async_client, test_session):
        """Returns profile including channel_group_ids."""
        profile = _create_profile(test_session, name="Detail Profile")
        profile.set_channel_group_ids([5, 10])
        test_session.commit()

        response = await async_client.get(f"/api/dummy-epg/profiles/{profile.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Detail Profile"
        assert data["channel_group_ids"] == [5, 10]

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when profile doesn't exist."""
        response = await async_client.get("/api/dummy-epg/profiles/99999")
        assert response.status_code == 404


class TestUpdateProfile:
    """Tests for PATCH /api/dummy-epg/profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_updates_name(self, async_client, test_session):
        """Updates the profile name."""
        profile = _create_profile(test_session, name="Old Name")

        with patch("routers.dummy_epg.cache"):
            response = await async_client.patch(
                f"/api/dummy-epg/profiles/{profile.id}",
                json={"name": "New Name"},
            )

        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_updates_multiple_fields(self, async_client, test_session):
        """Updates multiple fields at once."""
        profile = _create_profile(test_session)

        with patch("routers.dummy_epg.cache"):
            response = await async_client.patch(
                f"/api/dummy-epg/profiles/{profile.id}",
                json={
                    "enabled": False,
                    "program_duration": 60,
                    "include_live_tag": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["program_duration"] == 60
        assert data["include_live_tag"] is True

    @pytest.mark.asyncio
    async def test_updates_substitution_pairs(self, async_client, test_session):
        """Updates substitution pairs."""
        profile = _create_profile(test_session)

        with patch("routers.dummy_epg.cache"):
            response = await async_client.patch(
                f"/api/dummy-epg/profiles/{profile.id}",
                json={
                    "substitution_pairs": [
                        {"find": "FOO", "replace": "BAR", "is_regex": False, "enabled": True},
                    ],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["substitution_pairs"]) == 1
        assert data["substitution_pairs"][0]["find"] == "FOO"

    @pytest.mark.asyncio
    async def test_updates_channel_group_ids(self, async_client, test_session):
        """Updates channel_group_ids."""
        profile = _create_profile(test_session)
        profile.set_channel_group_ids([5])
        test_session.commit()

        with patch("routers.dummy_epg.cache"):
            response = await async_client.patch(
                f"/api/dummy-epg/profiles/{profile.id}",
                json={"channel_group_ids": [10, 20]},
            )

        assert response.status_code == 200
        assert response.json()["channel_group_ids"] == [10, 20]

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self, async_client, test_session):
        """Returns 409 when renaming to an existing name."""
        _create_profile(test_session, name="Taken Name")
        profile = _create_profile(test_session, name="My Profile")

        response = await async_client.patch(
            f"/api/dummy-epg/profiles/{profile.id}",
            json={"name": "Taken Name"},
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when profile doesn't exist."""
        response = await async_client.patch(
            "/api/dummy-epg/profiles/99999",
            json={"name": "Ghost"},
        )
        assert response.status_code == 404


class TestDeleteProfile:
    """Tests for DELETE /api/dummy-epg/profiles/{profile_id}."""

    @pytest.mark.asyncio
    async def test_deletes_profile(self, async_client, test_session):
        """Deletes a profile successfully."""
        profile = _create_profile(test_session, name="Delete Me")
        profile_id = profile.id

        with patch("routers.dummy_epg.cache"):
            response = await async_client.delete(f"/api/dummy-epg/profiles/{profile_id}")

        assert response.status_code == 204

        # Verify deleted from DB
        result = test_session.query(DummyEPGProfile).filter(
            DummyEPGProfile.id == profile_id
        ).first()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 when profile doesn't exist."""
        response = await async_client.delete("/api/dummy-epg/profiles/99999")
        assert response.status_code == 404


# =============================================================================
# Preview
# =============================================================================


class TestPreview:
    """Tests for POST /api/dummy-epg/preview."""

    @pytest.mark.asyncio
    async def test_preview_with_match(self, async_client):
        """Preview returns matched groups and rendered templates."""
        mock_result = {
            "matched": True,
            "groups": {"title": "Wolves vs Hawks"},
            "rendered_title": "Wolves vs Hawks",
            "rendered_description": "Showing Wolves vs Hawks",
        }

        with patch("dummy_epg_engine.preview_pipeline", return_value=mock_result):
            response = await async_client.post("/api/dummy-epg/preview", json={
                "sample_name": "Wolves vs Hawks HD",
                "title_pattern": r"(?P<title>.+?)\\s*HD",
                "title_template": "{title}",
                "description_template": "Showing {title}",
                "event_timezone": "US/Eastern",
                "program_duration": 180,
            })

        assert response.status_code == 200
        data = response.json()
        assert data["matched"] is True
        assert data["groups"]["title"] == "Wolves vs Hawks"

    @pytest.mark.asyncio
    async def test_preview_no_match_fallback(self, async_client):
        """Preview returns fallback when pattern doesn't match."""
        mock_result = {
            "matched": False,
            "groups": {},
            "rendered_title": "Fallback Title",
            "rendered_description": "Fallback Desc",
        }

        with patch("dummy_epg_engine.preview_pipeline", return_value=mock_result):
            response = await async_client.post("/api/dummy-epg/preview", json={
                "sample_name": "Random Channel Name",
                "title_pattern": r"(?P<title>NEVER_MATCHES)",
                "fallback_title_template": "Fallback Title",
                "fallback_description_template": "Fallback Desc",
                "event_timezone": "US/Eastern",
                "program_duration": 180,
            })

        assert response.status_code == 200
        data = response.json()
        assert data["matched"] is False
        assert data["rendered_title"] == "Fallback Title"

    @pytest.mark.asyncio
    async def test_preview_with_substitution_pairs(self, async_client):
        """Preview processes substitution pairs before pattern matching."""
        mock_result = {
            "matched": True,
            "groups": {"title": "Wolves vs Hawks"},
            "rendered_title": "Wolves vs Hawks",
        }

        with patch("dummy_epg_engine.preview_pipeline", return_value=mock_result):
            response = await async_client.post("/api/dummy-epg/preview", json={
                "sample_name": "Wolves vs Hawks HD 1080p",
                "substitution_pairs": [
                    {"find": " HD 1080p", "replace": "", "is_regex": False, "enabled": True},
                ],
                "title_pattern": r"(?P<title>.+)",
                "title_template": "{title}",
                "event_timezone": "US/Eastern",
                "program_duration": 180,
            })

        assert response.status_code == 200
        assert response.json()["matched"] is True

    @pytest.mark.asyncio
    async def test_preview_engine_error(self, async_client):
        """Returns 500 when preview engine raises."""
        with patch("dummy_epg_engine.preview_pipeline", side_effect=Exception("Engine error")):
            response = await async_client.post("/api/dummy-epg/preview", json={
                "sample_name": "Test",
                "event_timezone": "US/Eastern",
                "program_duration": 180,
            })

        assert response.status_code == 500


# =============================================================================
# XMLTV Output
# =============================================================================


class TestGetXmltvAll:
    """Tests for GET /api/dummy-epg/xmltv."""

    @pytest.mark.asyncio
    async def test_returns_xml_content_type(self, async_client, test_session):
        """Returns response with application/xml content type."""
        profile = _create_profile(test_session, name="XMLTV Test")
        xml_output = '<?xml version="1.0"?><tv></tv>'

        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()

        with patch("routers.dummy_epg._fetch_all_channels", new_callable=AsyncMock, return_value={}), \
             patch("dummy_epg_engine.generate_xmltv", return_value=xml_output), \
             patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.get("/api/dummy-epg/xmltv")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/xml"
        assert "<?xml" in response.text

    @pytest.mark.asyncio
    async def test_returns_cached_response(self, async_client, test_session):
        """Returns cached XMLTV without regenerating."""
        cached_xml = '<?xml version="1.0"?><tv><cached/></tv>'
        mock_cache = MagicMock()
        mock_cache.get.return_value = cached_xml

        with patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.get("/api/dummy-epg/xmltv")

        assert response.status_code == 200
        assert "<cached/>" in response.text

    @pytest.mark.asyncio
    async def test_only_includes_enabled_profiles(self, async_client, test_session):
        """Only enabled profiles are included in XMLTV output."""
        _create_profile(test_session, name="Enabled", enabled=True)
        _create_profile(test_session, name="Disabled", enabled=False)

        xml_output = '<?xml version="1.0"?><tv></tv>'
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()

        with patch("routers.dummy_epg._fetch_all_channels", new_callable=AsyncMock, return_value={}), \
             patch("dummy_epg_engine.generate_xmltv", return_value=xml_output) as mock_gen, \
             patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.get("/api/dummy-epg/xmltv")

        assert response.status_code == 200
        # Verify generate_xmltv was called with only 1 profile (the enabled one)
        call_args = mock_gen.call_args
        profile_data = call_args[0][0]
        assert len(profile_data) == 1
        assert profile_data[0]["name"] == "Enabled"

    @pytest.mark.asyncio
    async def test_resolves_group_ids_to_assignments(self, async_client, test_session):
        """XMLTV endpoint resolves channel_group_ids into channel_assignments."""
        profile = _create_profile(test_session, name="Group Profile")
        profile.set_channel_group_ids([5])
        test_session.commit()

        channel_map = {
            1: {"id": 1, "name": "Sports One", "channel_group_id": 5},
            2: {"id": 2, "name": "Sports Plus", "channel_group_id": 5},
            3: {"id": 3, "name": "News 24", "channel_group_id": 10},
        }

        xml_output = '<?xml version="1.0"?><tv></tv>'
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()

        with patch("routers.dummy_epg._fetch_all_channels", new_callable=AsyncMock, return_value=channel_map), \
             patch("dummy_epg_engine.generate_xmltv", return_value=xml_output) as mock_gen, \
             patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.get("/api/dummy-epg/xmltv")

        assert response.status_code == 200
        call_args = mock_gen.call_args
        profile_data = call_args[0][0]
        assert len(profile_data) == 1
        assignments = profile_data[0]["channel_assignments"]
        assert len(assignments) == 2
        assigned_ids = {a["channel_id"] for a in assignments}
        assert assigned_ids == {1, 2}


class TestGetXmltvProfile:
    """Tests for GET /api/dummy-epg/xmltv/{profile_id}."""

    @pytest.mark.asyncio
    async def test_returns_xml_for_single_profile(self, async_client, test_session):
        """Returns XMLTV for a specific profile."""
        profile = _create_profile(test_session, name="Single Profile")
        xml_output = '<?xml version="1.0"?><tv><channel/></tv>'

        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()

        with patch("routers.dummy_epg._fetch_all_channels", new_callable=AsyncMock, return_value={}), \
             patch("dummy_epg_engine.generate_xmltv", return_value=xml_output), \
             patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.get(f"/api/dummy-epg/xmltv/{profile.id}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/xml"
        assert "<channel/>" in response.text

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_profile(self, async_client):
        """Returns 404 when profile doesn't exist."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        with patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.get("/api/dummy-epg/xmltv/99999")

        assert response.status_code == 404


# =============================================================================
# Force Regenerate
# =============================================================================


class TestForceRegenerate:
    """Tests for POST /api/dummy-epg/generate."""

    @pytest.mark.asyncio
    async def test_regenerates_all(self, async_client, test_session):
        """Force-regenerates XMLTV for all enabled profiles."""
        _create_profile(test_session, name="Regen Profile", enabled=True)
        xml_output = '<?xml version="1.0"?><tv></tv>'

        mock_cache = MagicMock()

        with patch("routers.dummy_epg._fetch_all_channels", new_callable=AsyncMock, return_value={}), \
             patch("dummy_epg_engine.generate_xmltv", return_value=xml_output), \
             patch("routers.dummy_epg.cache", mock_cache):
            response = await async_client.post("/api/dummy-epg/generate")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["profiles_generated"] == 1

        # Verify cache was invalidated and set
        mock_cache.invalidate_prefix.assert_called_with("dummy_epg_xmltv")
        assert mock_cache.set.call_count >= 1
