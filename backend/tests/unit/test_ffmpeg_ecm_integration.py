"""
Unit tests for the FFMPEG Builder ECM integration module (Spec 1.12).

Tests profile CRUD, application to channels/groups, and validation.
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest
from unittest.mock import patch, MagicMock

from ffmpeg_builder.ecm_integration import (
    create_profile,
    get_profile,
    update_profile,
    delete_profile,
    list_profiles,
    apply_profile,
    enable_profile,
    disable_profile,
    validate_profile,
)

from tests.fixtures.ffmpeg_factories import (
    create_saved_config,
    create_builder_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_profile_data(
    name="Test Profile",
    config_id=100,
    apply_to="all",
    target_ids=None,
    enabled=True,
    **kwargs,
):
    """Create a profile data dict for test input."""
    data = {
        "name": name,
        "config_id": config_id,
        "apply_to": apply_to,
        "enabled": enabled,
    }
    if target_ids is not None:
        data["target_ids"] = target_ids
    data.update(kwargs)
    return data


# ===========================================================================
# Profile CRUD
# ===========================================================================

class TestFFMPEGProfileCRUD:
    """Tests for FFMPEG profile create/read/update/delete operations."""

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_creates_profile(self, mock_session):
        """Creating a profile returns a profile dict with an ID."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(name="My Profile", config_id=100)
        result = create_profile(data)

        assert result is not None
        assert "id" in result
        assert result["name"] == "My Profile"
        assert result["config_id"] == 100
        assert result["apply_to"] == "all"
        assert result["enabled"] is True

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_reads_profile(self, mock_session):
        """Reading a profile by ID returns the profile data."""
        session = MagicMock()
        mock_session.return_value = session

        # Create first, then read
        data = make_profile_data(name="Readable Profile")
        created = create_profile(data)
        profile_id = created["id"]

        result = get_profile(profile_id)

        assert result is not None
        assert result["id"] == profile_id
        assert result["name"] == "Readable Profile"

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_updates_profile(self, mock_session):
        """Updating a profile modifies the specified fields."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(name="Original Name")
        created = create_profile(data)
        profile_id = created["id"]

        updated = update_profile(profile_id, {"name": "Updated Name"})

        assert updated is not None
        assert updated["name"] == "Updated Name"
        assert updated["id"] == profile_id

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_deletes_profile(self, mock_session):
        """Deleting a profile removes it from the store."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(name="Deletable")
        created = create_profile(data)
        profile_id = created["id"]

        result = delete_profile(profile_id)

        assert result is True

        # After deletion, reading should return None or raise
        deleted = get_profile(profile_id)
        assert deleted is None

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_lists_profiles(self, mock_session):
        """Listing profiles returns all created profiles."""
        session = MagicMock()
        mock_session.return_value = session

        create_profile(make_profile_data(name="Profile A"))
        create_profile(make_profile_data(name="Profile B"))

        profiles = list_profiles()

        assert isinstance(profiles, list)
        assert len(profiles) >= 2
        names = [p["name"] for p in profiles]
        assert "Profile A" in names
        assert "Profile B" in names

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_profile_links_to_config(self, mock_session):
        """Profile references a valid saved FFMPEG config by ID."""
        session = MagicMock()
        mock_session.return_value = session

        saved_config = create_saved_config(name="Linked Config")
        config_id = saved_config["id"]

        data = make_profile_data(name="Linked Profile", config_id=config_id)
        result = create_profile(data)

        assert result["config_id"] == config_id


# ===========================================================================
# Profile Application
# ===========================================================================

class TestProfileApplication:
    """Tests for applying profiles to channels and groups."""

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_applies_profile_to_all_channels(self, mock_session):
        """Profile with apply_to='all' targets all channels."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(apply_to="all")
        created = create_profile(data)

        result = apply_profile(created["id"])

        assert result is not None
        assert result["apply_to"] == "all"
        assert result.get("target_ids") is None or result["target_ids"] == []

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_applies_profile_to_group(self, mock_session):
        """Profile with apply_to='group' targets specific groups."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(
            apply_to="group",
            target_ids=[1, 2, 3],
        )
        created = create_profile(data)

        result = apply_profile(created["id"])

        assert result is not None
        assert result["apply_to"] == "group"
        assert result["target_ids"] == [1, 2, 3]

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_applies_profile_to_channel(self, mock_session):
        """Profile with apply_to='channel' targets specific channels."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(
            apply_to="channel",
            target_ids=[10, 20],
        )
        created = create_profile(data)

        result = apply_profile(created["id"])

        assert result is not None
        assert result["apply_to"] == "channel"
        assert result["target_ids"] == [10, 20]

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_enable_disable_profile(self, mock_session):
        """Profile can be enabled and disabled."""
        session = MagicMock()
        mock_session.return_value = session

        data = make_profile_data(enabled=True)
        created = create_profile(data)
        profile_id = created["id"]

        # Disable
        disabled = disable_profile(profile_id)
        assert disabled["enabled"] is False

        # Re-enable
        enabled = enable_profile(profile_id)
        assert enabled["enabled"] is True


# ===========================================================================
# Profile Validation
# ===========================================================================

class TestProfileValidation:
    """Tests for profile data validation."""

    def test_requires_name(self):
        """Profile without a name fails validation."""
        data = make_profile_data(name="")
        result = validate_profile(data)

        assert result["valid"] is False
        assert any("name" in e.lower() for e in result["errors"])

    def test_requires_config_id(self):
        """Profile without a config_id fails validation."""
        data = make_profile_data()
        data.pop("config_id", None)
        result = validate_profile(data)

        assert result["valid"] is False
        assert any("config" in e.lower() for e in result["errors"])

    def test_validates_apply_to(self):
        """Profile with invalid apply_to value fails validation."""
        data = make_profile_data(apply_to="invalid_value")
        result = validate_profile(data)

        assert result["valid"] is False
        assert any("apply_to" in e.lower() or "apply" in e.lower() for e in result["errors"])

    def test_validates_target_ids(self):
        """Group/channel apply_to requires target_ids."""
        data = make_profile_data(apply_to="group", target_ids=None)
        result = validate_profile(data)

        assert result["valid"] is False
        assert any("target" in e.lower() for e in result["errors"])

    @patch("ffmpeg_builder.ecm_integration.get_db_session")
    def test_rejects_nonexistent_config_id(self, mock_session):
        """Profile referencing a non-existent config ID fails validation."""
        session = MagicMock()
        mock_session.return_value = session

        # Use a config_id that does not exist
        data = make_profile_data(config_id=999999)
        result = validate_profile(data)

        assert result["valid"] is False
        assert any("config" in e.lower() or "not found" in e.lower() for e in result["errors"])
