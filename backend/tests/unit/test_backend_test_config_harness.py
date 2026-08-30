"""Regression tests for deterministic backend pytest configuration."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tests._config_harness import cleanup_test_config, initialize_test_config


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_hostile_existing_config_cannot_change_test_baseline(tmp_path):
    """Dangerous mutant: the old harness reused this auth-enabled directory."""
    hostile = tmp_path / "stale-host-config"
    hostile.mkdir()
    (hostile / "auth_settings.json").write_text(
        json.dumps({"setup_complete": True, "require_auth": True}),
        encoding="utf-8",
    )
    environment = {
        "CONFIG_DIR": str(hostile),
        "ECM_TEST_CONFIG_ROOT": str(tmp_path),
    }

    first = initialize_test_config(environment)
    second = initialize_test_config(environment)
    try:
        assert first != second
        assert first != hostile
        assert second != hostile
        assert json.loads((first / "auth_settings.json").read_text()) == {
            "setup_complete": False,
            "require_auth": False,
        }
        assert json.loads((second / "auth_settings.json").read_text()) == {
            "setup_complete": False,
            "require_auth": False,
        }
        assert json.loads((hostile / "auth_settings.json").read_text()) == {
            "setup_complete": True,
            "require_auth": True,
        }
    finally:
        cleanup_test_config(first)
        cleanup_test_config(second)


def test_cleanup_refuses_unmarked_or_non_session_directories(tmp_path):
    unmarked = tmp_path / "ecm-pytest-not-owned"
    unmarked.mkdir()
    cleanup_test_config(unmarked)
    cleanup_test_config(tmp_path)
    assert unmarked.is_dir()


def test_backend_ci_uses_harness_root_and_bounded_timeout():
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    )
    backend = workflow["jobs"]["backend"]
    assert backend["timeout-minutes"] == 30
    pytest_step = next(step for step in backend["steps"] if step.get("id") == "pytest")
    assert pytest_step["env"]["ECM_TEST_CONFIG_ROOT"] == "${{ runner.temp }}"
    assert "CONFIG_DIR" not in pytest_step["env"]
