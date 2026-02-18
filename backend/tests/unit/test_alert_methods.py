"""
Unit tests for the alert_methods module.
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from alert_methods import (
    AlertMessage,
    AlertMethod,
    AlertMethodManager,
    register_method,
    get_method_types,
    create_method,
    send_alert,
    _method_registry,
)


class TestAlertMessage:
    """Tests for AlertMessage class."""

    def test_creates_with_required_fields(self):
        """Creates AlertMessage with required fields."""
        msg = AlertMessage(title="Test", message="Test message")
        assert msg.title == "Test"
        assert msg.message == "Test message"

    def test_default_notification_type_is_info(self):
        """Default notification_type is 'info'."""
        msg = AlertMessage(title="Test", message="Message")
        assert msg.notification_type == "info"

    def test_accepts_notification_type(self):
        """Accepts custom notification_type."""
        msg = AlertMessage(title="Error", message="Failed", notification_type="error")
        assert msg.notification_type == "error"

    def test_accepts_source(self):
        """Accepts source parameter."""
        msg = AlertMessage(title="Test", message="Message", source="stream_probe")
        assert msg.source == "stream_probe"

    def test_accepts_metadata(self):
        """Accepts metadata dictionary."""
        msg = AlertMessage(title="Test", message="Message", metadata={"count": 5})
        assert msg.metadata == {"count": 5}

    def test_defaults_metadata_to_empty_dict(self):
        """Defaults metadata to empty dict."""
        msg = AlertMessage(title="Test", message="Message")
        assert msg.metadata == {}

    def test_sets_timestamp(self):
        """Sets timestamp on creation."""
        before = datetime.utcnow()
        msg = AlertMessage(title="Test", message="Message")
        after = datetime.utcnow()
        assert before <= msg.timestamp <= after

    def test_to_dict_returns_all_fields(self):
        """to_dict returns all fields."""
        msg = AlertMessage(
            title="Test",
            message="Message",
            notification_type="success",
            source="test",
            metadata={"key": "value"},
        )
        result = msg.to_dict()

        assert result["title"] == "Test"
        assert result["message"] == "Message"
        assert result["type"] == "success"
        assert result["source"] == "test"
        assert result["metadata"] == {"key": "value"}
        assert "timestamp" in result


class TestAlertMethodBase:
    """Tests for AlertMethod abstract base class."""

    def test_validate_config_returns_true_for_valid(self):
        """validate_config returns (True, '') for valid config."""

        class TestMethod(AlertMethod):
            method_type = "test"
            required_config_fields = ["url", "token"]

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        is_valid, error = TestMethod.validate_config({"url": "http://test", "token": "abc"})
        assert is_valid is True
        assert error == ""

    def test_validate_config_returns_false_for_missing_fields(self):
        """validate_config returns (False, error) for missing fields."""

        class TestMethod(AlertMethod):
            method_type = "test"
            required_config_fields = ["url", "token"]

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        is_valid, error = TestMethod.validate_config({"url": "http://test"})
        assert is_valid is False
        assert "token" in error

    def test_format_message_includes_title(self):
        """format_message includes title."""

        class TestMethod(AlertMethod):
            method_type = "test"

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        method = TestMethod(1, "Test", {})
        msg = AlertMessage(title="Test Title", message="Test message")
        result = method.format_message(msg)

        assert "Test Title" in result

    def test_format_message_includes_source(self):
        """format_message includes source."""

        class TestMethod(AlertMethod):
            method_type = "test"

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        method = TestMethod(1, "Test", {})
        msg = AlertMessage(title="Test", message="Message", source="probe")
        result = method.format_message(msg)

        assert "probe" in result

    def test_get_emoji_returns_correct_emojis(self):
        """get_emoji returns correct emoji for each type."""

        class TestMethod(AlertMethod):
            method_type = "test"

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        method = TestMethod(1, "Test", {})

        assert method.get_emoji("info") == "ℹ️"
        assert method.get_emoji("success") == "✅"
        assert method.get_emoji("warning") == "⚠️"
        assert method.get_emoji("error") == "❌"
        assert method.get_emoji("unknown") == "📢"


class TestAlertMethodDigest:
    """Tests for AlertMethod digest functionality."""

    @pytest.fixture
    def mock_method(self):
        """Create a mock alert method for testing."""

        class MockMethod(AlertMethod):
            method_type = "mock"

            def __init__(self):
                super().__init__(1, "Mock", {})
                self.sent_messages = []

            async def send(self, message):
                self.sent_messages.append(message)
                return True

            async def test_connection(self):
                return True, "OK"

        return MockMethod()

    @pytest.mark.asyncio
    async def test_send_digest_single_message(self, mock_method):
        """send_digest sends single message directly."""
        msg = AlertMessage(title="Test", message="Message")
        result = await mock_method.send_digest([msg])

        assert result is True
        assert len(mock_method.sent_messages) == 1
        assert mock_method.sent_messages[0] is msg

    @pytest.mark.asyncio
    async def test_send_digest_empty_list(self, mock_method):
        """send_digest returns True for empty list."""
        result = await mock_method.send_digest([])
        assert result is True
        assert len(mock_method.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_send_digest_combines_messages(self, mock_method):
        """send_digest combines multiple messages."""
        msg1 = AlertMessage(title="Test 1", message="Message 1", notification_type="success")
        msg2 = AlertMessage(title="Test 2", message="Message 2", notification_type="error")

        result = await mock_method.send_digest([msg1, msg2])

        assert result is True
        assert len(mock_method.sent_messages) == 1
        digest = mock_method.sent_messages[0]
        assert "Digest" in digest.title

    def test_build_digest_counts_by_type(self, mock_method):
        """_build_digest counts messages by type."""
        messages = [
            AlertMessage(title="T1", message="M1", notification_type="success"),
            AlertMessage(title="T2", message="M2", notification_type="success"),
            AlertMessage(title="T3", message="M3", notification_type="error"),
        ]

        digest = mock_method._build_digest(messages)

        assert digest.metadata["counts"]["success"] == 2
        assert digest.metadata["counts"]["error"] == 1

    def test_build_digest_sets_overall_type_error(self, mock_method):
        """_build_digest sets type to 'error' if any errors."""
        messages = [
            AlertMessage(title="T1", message="M1", notification_type="success"),
            AlertMessage(title="T2", message="M2", notification_type="error"),
        ]

        digest = mock_method._build_digest(messages)

        assert digest.notification_type == "error"

    def test_build_digest_sets_overall_type_warning(self, mock_method):
        """_build_digest sets type to 'warning' if warnings but no errors."""
        messages = [
            AlertMessage(title="T1", message="M1", notification_type="success"),
            AlertMessage(title="T2", message="M2", notification_type="warning"),
        ]

        digest = mock_method._build_digest(messages)

        assert digest.notification_type == "warning"


class TestMethodRegistry:
    """Tests for method registration."""

    def test_register_method_adds_to_registry(self):
        """register_method adds class to registry."""
        # Clear registry first to avoid conflicts
        _method_registry.clear()

        @register_method
        class NewTestMethod(AlertMethod):
            method_type = "new_test"
            display_name = "New Test"

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        assert "new_test" in _method_registry
        assert _method_registry["new_test"] is NewTestMethod

    def test_register_method_raises_without_type(self):
        """register_method raises ValueError without method_type."""
        with pytest.raises(ValueError):

            @register_method
            class BadMethod(AlertMethod):
                method_type = ""

                async def send(self, message):
                    pass

                async def test_connection(self):
                    pass


class TestGetMethodTypes:
    """Tests for get_method_types()."""

    def test_returns_list(self):
        """get_method_types returns a list."""
        result = get_method_types()
        assert isinstance(result, list)

    def test_includes_type_info(self):
        """get_method_types includes type information."""
        # Assuming discord is registered from imports
        result = get_method_types()

        # Each item should have these fields
        for item in result:
            assert "type" in item
            assert "display_name" in item
            assert "required_fields" in item
            assert "optional_fields" in item


class TestCreateMethod:
    """Tests for create_method()."""

    def test_creates_registered_method(self):
        """create_method creates instance of registered method."""
        _method_registry.clear()

        @register_method
        class CreateTestMethod(AlertMethod):
            method_type = "create_test"
            display_name = "Create Test"

            async def send(self, message):
                pass

            async def test_connection(self):
                pass

        result = create_method("create_test", 1, "Test Method", {"key": "value"})

        assert result is not None
        assert isinstance(result, CreateTestMethod)
        assert result.method_id == 1
        assert result.name == "Test Method"
        assert result.config == {"key": "value"}

    def test_returns_none_for_unknown_type(self):
        """create_method returns None for unknown type."""
        result = create_method("unknown_type_xyz", 1, "Test", {})
        assert result is None


class TestAlertMethodManager:
    """Tests for AlertMethodManager class."""

    @pytest.fixture
    def manager(self):
        """Create a fresh manager for testing."""
        return AlertMethodManager(digest_window_seconds=1)

    def test_init_with_default_digest_window(self):
        """Manager initializes with default digest window."""
        manager = AlertMethodManager()
        assert manager._digest_window == 30  # DEFAULT_DIGEST_WINDOW_SECONDS

    def test_init_with_custom_digest_window(self, manager):
        """Manager accepts custom digest window."""
        assert manager._digest_window == 1

    def test_should_alert_for_source_returns_true_without_config(self, manager):
        """_should_alert_for_source returns True when no config."""
        result = manager._should_alert_for_source(None, "epg_refresh", 1)
        assert result is True

    def test_should_alert_for_source_returns_true_without_category(self, manager):
        """_should_alert_for_source returns True when no category."""
        result = manager._should_alert_for_source('{"epg_refresh": {}}', None, 1)
        assert result is True

    def test_should_alert_for_source_filters_epg_refresh(self, manager):
        """_should_alert_for_source filters EPG refresh."""
        config = json.dumps({
            "epg_refresh": {
                "enabled": True,
                "filter_mode": "only_selected",
                "source_ids": [1, 2],
            }
        })

        # Source 1 should pass
        assert manager._should_alert_for_source(config, "epg_refresh", 1) is True
        # Source 3 should fail
        assert manager._should_alert_for_source(config, "epg_refresh", 3) is False

    def test_should_alert_for_source_filters_m3u_refresh(self, manager):
        """_should_alert_for_source filters M3U refresh."""
        config = json.dumps({
            "m3u_refresh": {
                "enabled": True,
                "filter_mode": "all_except",
                "account_ids": [5],
            }
        })

        # Account 1 should pass (not in except list)
        assert manager._should_alert_for_source(config, "m3u_refresh", 1) is True
        # Account 5 should fail (in except list)
        assert manager._should_alert_for_source(config, "m3u_refresh", 5) is False

    def test_should_alert_for_source_filters_probe_failures(self, manager):
        """_should_alert_for_source filters probe failures by count."""
        config = json.dumps({
            "probe_failures": {
                "enabled": True,
                "min_failures": 5,
            }
        })

        # 3 failures should not trigger
        assert manager._should_alert_for_source(config, "probe_failures", None, 3) is False
        # 5 failures should trigger
        assert manager._should_alert_for_source(config, "probe_failures", None, 5) is True

    def test_should_alert_for_source_handles_disabled_category(self, manager):
        """_should_alert_for_source respects enabled flag."""
        config = json.dumps({
            "epg_refresh": {
                "enabled": False,
            }
        })

        assert manager._should_alert_for_source(config, "epg_refresh", 1) is False

    def test_should_alert_for_source_handles_invalid_json(self, manager):
        """_should_alert_for_source handles invalid JSON gracefully."""
        result = manager._should_alert_for_source("not valid json", "epg_refresh", 1)
        assert result is True  # Defaults to True


class TestSendAlertFunction:
    """Tests for send_alert() convenience function."""

    @pytest.mark.asyncio
    async def test_send_alert_uses_global_manager(self):
        """send_alert uses global manager."""
        with patch("alert_methods.get_alert_manager") as mock_get:
            mock_manager = AsyncMock()
            mock_manager.send_alert = AsyncMock(return_value={})
            mock_get.return_value = mock_manager

            await send_alert(
                title="Test",
                message="Message",
                notification_type="info",
            )

            mock_manager.send_alert.assert_called_once()
