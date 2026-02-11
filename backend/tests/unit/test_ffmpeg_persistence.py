"""
Unit tests for the FFMPEG Builder persistence module.

Tests saved configuration CRUD, validation, search, and export/import (Spec 1.11).
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import json
from datetime import datetime, timedelta

import pytest

from ffmpeg_builder.persistence import (
    create_config,
    get_config,
    update_config,
    delete_config,
    list_configs,
    search_configs,
    export_config_json,
    import_config_json,
    SavedConfig,
    ConfigNotFoundError,
    ConfigValidationError,
)

from tests.fixtures.ffmpeg_factories import (
    create_builder_state,
    create_saved_config,
)


class TestSavedConfigCRUD:
    """Tests for basic CRUD operations on saved FFMPEG configurations."""

    def test_creates_saved_config(self, test_session):
        """A new configuration can be saved to the database."""
        state = create_builder_state()
        result = create_config(
            test_session,
            name="My Encode Config",
            config=state,
        )

        assert result is not None
        assert result.id is not None
        assert result.name == "My Encode Config"

    def test_reads_saved_config_by_id(self, test_session):
        """A saved configuration can be retrieved by its ID."""
        state = create_builder_state()
        created = create_config(test_session, name="Read Test", config=state)

        loaded = get_config(test_session, created.id)

        assert loaded is not None
        assert loaded.id == created.id
        assert loaded.name == "Read Test"

    def test_updates_saved_config(self, test_session):
        """A saved configuration can be updated with new values."""
        state = create_builder_state()
        created = create_config(test_session, name="Original Name", config=state)

        updated = update_config(test_session, created.id, name="Updated Name")

        assert updated.name == "Updated Name"

    def test_deletes_saved_config(self, test_session):
        """A saved configuration can be deleted."""
        state = create_builder_state()
        created = create_config(test_session, name="Delete Me", config=state)

        delete_config(test_session, created.id)

        with pytest.raises(ConfigNotFoundError):
            get_config(test_session, created.id)

    def test_lists_saved_configs(self, test_session):
        """All saved configurations can be listed."""
        state = create_builder_state()
        create_config(test_session, name="Config A", config=state)
        create_config(test_session, name="Config B", config=state)

        configs = list_configs(test_session)

        assert len(configs) >= 2
        names = [c.name for c in configs]
        assert "Config A" in names
        assert "Config B" in names

    def test_lists_configs_with_pagination(self, test_session):
        """Config listing supports offset and limit for pagination."""
        state = create_builder_state()
        for i in range(5):
            create_config(test_session, name=f"Paginated Config {i}", config=state)

        page = list_configs(test_session, offset=2, limit=2)

        assert len(page) == 2

    def test_config_has_timestamps(self, test_session):
        """Saved config includes created_at and updated_at timestamps."""
        state = create_builder_state()
        created = create_config(test_session, name="Timestamp Test", config=state)

        assert created.created_at is not None
        assert created.updated_at is not None
        assert isinstance(created.created_at, datetime)
        assert isinstance(created.updated_at, datetime)

    def test_config_stores_full_builder_state(self, test_session):
        """The full builder state dict is stored and retrievable."""
        state = create_builder_state()
        created = create_config(test_session, name="Full State", config=state)

        loaded = get_config(test_session, created.id)

        assert loaded.config is not None
        assert "input" in loaded.config
        assert "output" in loaded.config
        assert "videoCodec" in loaded.config
        assert "audioCodec" in loaded.config


class TestSavedConfigValidation:
    """Tests for saved configuration input validation."""

    def test_requires_name(self, test_session):
        """Creating a config without a name raises a validation error."""
        state = create_builder_state()

        with pytest.raises((ConfigValidationError, ValueError)):
            create_config(test_session, name="", config=state)

    def test_requires_config(self, test_session):
        """Creating a config without a config dict raises a validation error."""
        with pytest.raises((ConfigValidationError, ValueError, TypeError)):
            create_config(test_session, name="No Config", config=None)

    def test_name_max_length(self, test_session):
        """Config name exceeding maximum length is rejected."""
        state = create_builder_state()
        long_name = "A" * 300  # Exceeds reasonable max length

        with pytest.raises((ConfigValidationError, ValueError)):
            create_config(test_session, name=long_name, config=state)

    def test_description_optional(self, test_session):
        """Config can be created without a description."""
        state = create_builder_state()
        result = create_config(test_session, name="No Description", config=state)

        assert result is not None
        assert result.description is None or result.description == ""

    def test_validates_config_structure(self, test_session):
        """Config dict must have the expected builder state structure."""
        invalid_config = {"not": "a valid config"}

        with pytest.raises((ConfigValidationError, ValueError)):
            create_config(test_session, name="Invalid Structure", config=invalid_config)


class TestSavedConfigSearch:
    """Tests for searching saved configurations."""

    @pytest.fixture(autouse=True)
    def _setup_search_data(self, test_session):
        """Populate test data for search tests."""
        state = create_builder_state()
        create_config(
            test_session, name="Web Streaming Config",
            description="For web streaming workflows",
            config=state,
        )
        create_config(
            test_session, name="Archive Config",
            description="For long-term archival storage",
            config=state,
        )
        create_config(
            test_session, name="Quick Transcode",
            description="Fast transcode for preview",
            config=state,
        )

    def test_search_by_name(self, test_session):
        """Searching by name returns matching configs."""
        results = search_configs(test_session, query="Archive")

        assert len(results) >= 1
        assert any("Archive" in c.name for c in results)

    def test_search_by_description(self, test_session):
        """Searching matches against description text."""
        results = search_configs(test_session, query="archival")

        assert len(results) >= 1
        assert any("archival" in (c.description or "").lower() for c in results)

    def test_filter_by_date_range(self, test_session):
        """Configs can be filtered by creation date range."""
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        tomorrow = now + timedelta(days=1)

        results = search_configs(
            test_session,
            date_from=yesterday,
            date_to=tomorrow,
        )

        assert len(results) >= 3  # All test configs created just now


class TestConfigExport:
    """Tests for configuration export and import."""

    def test_exports_config_as_json(self, test_session):
        """A saved config can be exported as a JSON string."""
        state = create_builder_state()
        created = create_config(test_session, name="Export Test", config=state)

        exported = export_config_json(test_session, created.id)

        assert isinstance(exported, str)
        parsed = json.loads(exported)
        assert parsed["name"] == "Export Test"
        assert "config" in parsed

    def test_imports_config_from_json(self, test_session):
        """A config can be imported from a JSON string."""
        state = create_builder_state()
        json_str = json.dumps({
            "name": "Imported Config",
            "description": "Imported from file",
            "config": state,
        })

        result = import_config_json(test_session, json_str)

        assert result is not None
        assert result.name == "Imported Config"
        assert result.id is not None

    def test_import_validates_structure(self, test_session):
        """Importing invalid JSON structure raises a validation error."""
        invalid_json = json.dumps({"bad": "data"})

        with pytest.raises((ConfigValidationError, ValueError)):
            import_config_json(test_session, invalid_json)
