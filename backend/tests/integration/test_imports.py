"""
Integration tests for import paths and circular import detection.

Tests: Verify all import paths work, no circular imports,
       key modules are importable, from main import app works.
"""
import importlib
import sys
import pytest


class TestMainImport:
    """Verify main.py imports correctly."""

    def test_import_main(self):
        """main module should be importable."""
        import main
        assert hasattr(main, "app")

    def test_import_app(self):
        """from main import app should work."""
        from main import app
        assert app is not None

    def test_app_is_fastapi(self):
        """app should be a FastAPI instance."""
        from main import app
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)


class TestDatabaseImport:
    """Verify database module imports."""

    def test_import_database(self):
        """database module should be importable."""
        import database
        assert hasattr(database, "Base")
        assert hasattr(database, "get_session")

    def test_import_models(self):
        """models module should be importable."""
        import models
        assert hasattr(models, "JournalEntry")
        assert hasattr(models, "Notification")
        assert hasattr(models, "ScheduledTask")


class TestServiceImports:
    """Verify service module imports."""

    IMPORTABLE_MODULES = [
        "dispatcharr_client",
        "stream_prober",
        "bandwidth_tracker",
        "m3u_change_detector",
        "schedule_calculator",
        "cron_parser",
    ]

    @pytest.mark.parametrize("module_name", IMPORTABLE_MODULES)
    def test_service_importable(self, module_name):
        """Service modules should be importable without errors."""
        mod = importlib.import_module(module_name)
        assert mod is not None


class TestRouterImports:
    """Verify router modules import correctly."""

    def test_auth_routes_importable(self):
        """Auth routes module should be importable."""
        from auth.routes import router
        assert router is not None

    def test_admin_routes_importable(self):
        """Admin routes module should be importable."""
        from auth.admin_routes import router
        assert router is not None

    def test_tls_routes_importable(self):
        """TLS routes module should be importable."""
        from tls.routes import router
        assert router is not None


class TestTaskImports:
    """Verify task-related imports."""

    def test_task_registry_importable(self):
        """task_registry module should be importable."""
        import task_registry
        assert hasattr(task_registry, "get_registry")

    def test_task_engine_importable(self):
        """task_engine module should be importable."""
        import task_engine
        assert hasattr(task_engine, "get_engine")


class TestFFmpegImports:
    """Verify FFmpeg builder imports."""

    def test_ffmpeg_builder_importable(self):
        """ffmpeg_builder package should be importable."""
        import ffmpeg_builder
        assert ffmpeg_builder is not None

    def test_ffmpeg_persistence_importable(self):
        """ffmpeg_builder.persistence should be importable."""
        from ffmpeg_builder.persistence import SavedConfig
        assert SavedConfig is not None


class TestNoCircularImports:
    """Verify no circular import issues."""

    def test_reimport_main(self):
        """Re-importing main should not raise circular import error."""
        importlib.reload(sys.modules["main"])

    def test_database_then_main(self):
        """Importing database then main should work."""
        import database  # noqa: F811
        import main  # noqa: F811
        assert database.Base is not None
        assert main.app is not None

    def test_models_then_database(self):
        """Importing models then database should work."""
        import models  # noqa: F811
        import database  # noqa: F811
        assert models.JournalEntry is not None
        assert database.Base is not None
