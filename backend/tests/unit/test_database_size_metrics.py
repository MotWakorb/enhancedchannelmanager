"""
Unit tests for ``observability.update_database_size_metrics`` (bd-ygoqr).

The DB-size gauges are emitted from two paths:

  1. ``database._perform_maintenance`` at startup, post-VACUUM
  2. ``tasks.cleanup.CleanupTask.execute`` post-VACUUM

Both paths call ``observability.update_database_size_metrics`` — the helper
that does the two ``os.path.getsize`` reads and sets the gauges. These tests
exercise the helper directly with a synthetic file layout so the contract
stays locked even if the call sites move.
"""
import os

import pytest
from prometheus_client import generate_latest

import observability


@pytest.fixture(autouse=True)
def _reset_observability_state():
    """Wipe observability globals between tests."""
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


class TestUpdateDatabaseSizeMetrics:
    def test_publishes_body_and_wal_sizes_when_both_files_exist(self, tmp_path):
        """Both gauges reflect the on-disk byte size of the named files."""
        observability.install_metrics()
        db_file = tmp_path / "journal.db"
        wal_file = tmp_path / "journal.db-wal"

        body_payload = b"x" * 4096  # 4 KB
        wal_payload = b"y" * 1024   # 1 KB
        db_file.write_bytes(body_payload)
        wal_file.write_bytes(wal_payload)

        observability.update_database_size_metrics(db_path=str(db_file))

        body_gauge = observability.get_metric("database_size_bytes")
        wal_gauge = observability.get_metric("database_wal_size_bytes")
        assert body_gauge._value.get() == 4096.0
        assert wal_gauge._value.get() == 1024.0

    def test_wal_size_is_zero_when_wal_file_absent(self, tmp_path):
        """A checkpointed-and-truncated WAL = no file = gauge stays at 0."""
        observability.install_metrics()
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"x" * 2048)

        observability.update_database_size_metrics(db_path=str(db_file))

        assert observability.get_metric("database_size_bytes")._value.get() == 2048.0
        assert observability.get_metric("database_wal_size_bytes")._value.get() == 0.0

    def test_body_size_is_zero_when_db_file_absent(self, tmp_path):
        """Pre-init / pre-create_all path: file not yet on disk → gauge=0."""
        observability.install_metrics()
        ghost_path = tmp_path / "does-not-exist.db"

        observability.update_database_size_metrics(db_path=str(ghost_path))

        assert observability.get_metric("database_size_bytes")._value.get() == 0.0
        assert observability.get_metric("database_wal_size_bytes")._value.get() == 0.0

    def test_helper_does_not_raise_when_metrics_uninitialized(self, tmp_path):
        """Defensive contract: never raise into the maintenance code path.

        ``get_metric`` lazily installs the registry — but if even that
        fails (e.g., Prometheus client missing), the helper must swallow.
        """
        # Simulate a totally cold start — no install_metrics() yet.
        observability.reset_for_tests()
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"x" * 100)

        # Should not raise.
        observability.update_database_size_metrics(db_path=str(db_file))

    def test_metrics_render_with_expected_names(self, tmp_path):
        """Both gauges appear in the /metrics text-format output."""
        observability.install_metrics()
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"x" * 256)

        observability.update_database_size_metrics(db_path=str(db_file))

        rendered = generate_latest(observability.REGISTRY).decode("utf-8")
        assert "ecm_database_size_bytes" in rendered
        assert "ecm_database_wal_size_bytes" in rendered

    def test_helper_resolves_default_path_from_database_module(self, monkeypatch, tmp_path):
        """When db_path is None, helper reads database.JOURNAL_DB_FILE."""
        observability.install_metrics()
        synthetic_db = tmp_path / "journal.db"
        synthetic_db.write_bytes(b"a" * 512)

        # Patch the module-level constant the helper imports.
        import database
        monkeypatch.setattr(database, "JOURNAL_DB_FILE", synthetic_db)

        observability.update_database_size_metrics()

        assert observability.get_metric("database_size_bytes")._value.get() == 512.0
