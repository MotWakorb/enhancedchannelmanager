"""Tests for the non-skippable backend test dependency verifier."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_test_deps.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_test_deps", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_jq_binary_is_required():
    module = _load()
    assert "jq" in module.REQUIRED_TEST_BINARIES


def test_missing_jq_fails_nonzero_without_running_pytest(capsys):
    module = _load()
    with patch.object(module.shutil, "which", return_value=None):
        assert module.main() == 1
    error = capsys.readouterr().err
    assert "required OS test binaries" in error
    assert "apt-get install jq" in error
    assert "requirements.txt" not in error


def test_missing_python_package_has_distinct_actionable_guidance(capsys):
    module = _load()
    with (
        patch.object(module.importlib, "import_module", side_effect=ImportError("missing")),
        patch.object(module.shutil, "which", return_value="/usr/bin/jq"),
    ):
        assert module.main() == 1
    error = capsys.readouterr().err
    assert "required Python test packages" in error
    assert "backend/requirements.txt" in error
    assert "apt-get install jq" not in error


def test_present_jq_participates_in_clean_success(capsys):
    module = _load()
    with (
        patch.object(module.importlib, "import_module"),
        patch.object(module.shutil, "which", return_value="/usr/bin/jq"),
    ):
        assert module.main() == 0
    assert "jq" in capsys.readouterr().out
