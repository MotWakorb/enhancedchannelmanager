"""
Integration tests for application lifecycle (startup/shutdown).

Tests: Verify startup_event initializes services, DB init runs,
       settings load works, shutdown cleans up gracefully.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import main
from main import app


@pytest.fixture(autouse=True)
def isolate_persistent_logging():
    """Lifecycle tests assert wiring without leaving live file descriptors."""
    with patch("main.install_persistent_json_logging"):
        yield


class TestStartupInitialization:
    """Verify startup_event initializes critical services."""

    @pytest.mark.asyncio
    async def test_startup_calls_init_db(self):
        """startup_event should call init_db() to initialize the database."""
        startup_order = []
        with patch("main.init_db") as mock_init, \
             patch("main.get_settings") as mock_settings, \
             patch("main.set_log_level"), \
             patch("main.install_persistent_json_logging") as mock_file_logging, \
             patch("tls.https_server.is_https_subprocess", return_value=False), \
             patch("main.get_client"), \
             patch("main.BandwidthTracker"), \
             patch("main.set_tracker"), \
             patch("main.StreamProber") as mock_prober_cls, \
             patch("main.set_prober"), \
             patch("main.get_prober", return_value=None), \
             patch("task_engine.start_engine", new_callable=AsyncMock), \
             patch("task_engine.get_engine", return_value=MagicMock()), \
             patch("main.asyncio") as mock_asyncio, \
             patch("tls.settings.get_tls_settings", return_value=MagicMock(enabled=False)), \
             patch("tls.https_server.start_https_if_configured", new_callable=AsyncMock):
            settings = MagicMock()
            settings.is_configured.return_value = False
            settings.backend_log_level = None
            settings.url = "http://test"
            mock_settings.return_value = settings
            mock_prober_cls.return_value = MagicMock(start=AsyncMock())
            mock_file_logging.side_effect = lambda *args, **kwargs: startup_order.append(
                "persistent_logging"
            )
            mock_init.side_effect = lambda: startup_order.append("database")

            from main import startup_event
            await startup_event()

            mock_init.assert_called_once()
            assert startup_order.index("persistent_logging") < startup_order.index("database")
            mock_file_logging.assert_called_once_with(
                main.CONFIG_DIR,
                max_bytes=mock_settings.return_value.backend_log_file_max_bytes,
                backup_count=mock_settings.return_value.backend_log_file_backup_count,
            )

    @pytest.mark.asyncio
    async def test_startup_loads_settings(self):
        """startup_event should load application settings."""
        with patch("main.init_db"), \
             patch("main.get_settings") as mock_settings, \
             patch("main.set_log_level"), \
             patch("tls.https_server.is_https_subprocess", return_value=False), \
             patch("main.asyncio") as mock_asyncio, \
             patch("tls.settings.get_tls_settings", return_value=MagicMock(enabled=False)), \
             patch("tls.https_server.start_https_if_configured", new_callable=AsyncMock):
            settings = MagicMock()
            settings.is_configured.return_value = False
            settings.backend_log_level = None
            settings.url = ""
            mock_settings.return_value = settings

            from main import startup_event
            await startup_event()

            mock_settings.assert_called()

    @pytest.mark.asyncio
    async def test_startup_skips_services_for_https_subprocess(self):
        """startup_event should skip background services for HTTPS subprocess."""
        with patch("main.init_db") as mock_init, \
             patch("main.get_settings") as mock_settings, \
             patch("main.set_log_level"), \
             patch("tls.https_server.is_https_subprocess", return_value=True):
            settings = MagicMock()
            settings.is_configured.return_value = True
            settings.backend_log_level = None
            settings.url = "http://test"
            mock_settings.return_value = settings

            from main import startup_event
            await startup_event()

            # init_db should still be called even for subprocess
            mock_init.assert_called_once()


    @pytest.mark.asyncio
    async def test_startup_probes_key_integrity(self):
        """startup_event should probe the cloud-backup encryption key (bead m40pn)."""
        with patch("main.init_db"), \
             patch("main.get_settings") as mock_settings, \
             patch("main.set_log_level"), \
             patch("tls.https_server.is_https_subprocess", return_value=False), \
             patch("main.asyncio"), \
             patch("tls.settings.get_tls_settings", return_value=MagicMock(enabled=False)), \
             patch("tls.https_server.start_https_if_configured", new_callable=AsyncMock), \
             patch("cloud_storage.crypto.verify_key_integrity_at_startup") as mock_probe:
            settings = MagicMock()
            settings.is_configured.return_value = False
            settings.backend_log_level = None
            settings.url = ""
            mock_settings.return_value = settings

            from main import startup_event
            await startup_event()

            mock_probe.assert_called_once()


class TestShutdownCleanup:
    """Verify shutdown_event cleans up gracefully."""

    @pytest.mark.asyncio
    async def test_shutdown_runs_without_error(self):
        """shutdown_event should complete without raising."""
        with patch("tls.https_server.stop_https_server", new_callable=AsyncMock), \
             patch("tls.renewal.renewal_manager") as mock_renewal:
            mock_renewal.stop = MagicMock()

            from main import shutdown_event
            await shutdown_event()


class TestAppConfiguration:
    """Verify app-level configuration is correct."""

    def test_app_title(self):
        """App should have the expected title."""
        assert "Enhanced Channel Manager" in app.title

    def test_app_docs_url(self):
        """App should have docs at /api/docs."""
        assert app.docs_url == "/api/docs"

    def test_app_openapi_url(self):
        """App should have OpenAPI at /api/openapi.json."""
        assert app.openapi_url == "/api/openapi.json"

    def test_app_has_openapi_tags(self):
        """App should have openapi_tags configured."""
        assert app.openapi_tags is not None
        assert len(app.openapi_tags) > 20
