"""Tests for ``scripts/check_version_consistency.py`` (bd-9rtlc).

The script is the CI guard against version-bump skew across the three
hand-edited touchpoints (``frontend/package.json``,
``backend/routers/backup.py``, ``backend/main.py``). These tests
exercise the extractor functions on canonical and degenerate inputs
and verify the main() exit-code contract on a synthetic repo layout
in tmp_path.

The tests intentionally do NOT assert against the real repo files
(those move on every bump) — they construct a fake repo tree under
tmp_path so the test stays stable across version bumps.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_version_consistency.py"


def _load_script_module():
    """Load check_version_consistency.py as a module without affecting sys.path.

    The script is not in a Python package, so we use importlib to load it
    as an ad-hoc module. Cached after first call to avoid re-loading on
    every test (the parametrize cases trigger the loader many times).
    """
    spec = importlib.util.spec_from_file_location(
        "check_version_consistency", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_version_consistency"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


# ─── Extractor unit tests ───────────────────────────────────────────────


class TestPackageJsonExtractor:
    def test_extracts_version_from_canonical_package_json(self, script_module):
        text = '{"name": "ecm", "version": "0.17.0-0034", "type": "module"}'
        assert script_module._extract_package_json_version(text) == "0.17.0-0034"

    def test_returns_none_on_invalid_json(self, script_module):
        assert script_module._extract_package_json_version("{not json}") is None

    def test_returns_none_when_version_field_missing(self, script_module):
        text = '{"name": "ecm"}'
        assert script_module._extract_package_json_version(text) is None


class TestAppVersionLiteralExtractor:
    def test_extracts_double_quoted_literal(self, script_module):
        text = 'APP_VERSION = "0.17.0-0034"\n'
        assert script_module._extract_app_version_literal(text) == "0.17.0-0034"

    def test_extracts_single_quoted_literal(self, script_module):
        text = "APP_VERSION = '0.17.0-0034'\n"
        assert script_module._extract_app_version_literal(text) == "0.17.0-0034"

    def test_tolerates_surrounding_whitespace(self, script_module):
        text = "    APP_VERSION   =    \"0.17.0-0034\"\n"
        assert script_module._extract_app_version_literal(text) == "0.17.0-0034"

    def test_returns_none_when_assignment_missing(self, script_module):
        text = "OTHER_CONST = 'foo'\n"
        assert script_module._extract_app_version_literal(text) is None


class TestFastAPIVersionKwargExtractor:
    def test_extracts_kwarg_from_multi_line_call(self, script_module):
        text = (
            "app = FastAPI(\n"
            '    title="ECM",\n'
            '    version="0.17.0-0034",\n'
            "    docs_url=\"/api/docs\",\n"
            ")\n"
        )
        assert script_module._extract_fastapi_version_kwarg(text) == "0.17.0-0034"

    def test_does_not_match_unindented_assignment(self, script_module):
        # Top-level `version = "..."` (no leading indentation) is intentionally
        # rejected — the canonical call site is always indented inside the
        # FastAPI() constructor.
        text = 'version = "0.17.0-0034"\n'
        assert script_module._extract_fastapi_version_kwarg(text) is None


# ─── End-to-end main() exit-code tests ──────────────────────────────────


def _build_fake_repo(tmp_path: Path, *, version_pkg: str, version_app: str, version_fastapi: str) -> Path:
    """Lay out a fake repo tree under tmp_path with the three touchpoints.

    Returns the fake repo root.
    """
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        f'{{"name": "ecm", "version": "{version_pkg}"}}\n'
    )

    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "routers").mkdir()
    (tmp_path / "backend" / "routers" / "backup.py").write_text(
        f'APP_VERSION = "{version_app}"\n'
    )
    (tmp_path / "backend" / "main.py").write_text(
        "app = FastAPI(\n"
        f'    title="ECM",\n'
        f'    version="{version_fastapi}",\n'
        ")\n"
    )
    return tmp_path


def test_main_returns_zero_when_all_touchpoints_match(tmp_path, script_module, monkeypatch, capsys):
    _build_fake_repo(
        tmp_path,
        version_pkg="0.17.0-0034",
        version_app="0.17.0-0034",
        version_fastapi="0.17.0-0034",
    )
    monkeypatch.setattr(script_module, "REPO_ROOT", tmp_path)

    rc = script_module.main()

    captured = capsys.readouterr()
    assert rc == 0
    assert "0.17.0-0034" in captured.out
    assert "match canonical" in captured.out


def test_main_returns_one_on_skew(tmp_path, script_module, monkeypatch, capsys):
    _build_fake_repo(
        tmp_path,
        version_pkg="0.17.0-0034",
        version_app="0.17.0-0033",  # one build behind — the bd-9rtlc disease
        version_fastapi="0.17.0-0034",
    )
    monkeypatch.setattr(script_module, "REPO_ROOT", tmp_path)

    rc = script_module.main()

    captured = capsys.readouterr()
    assert rc == 1
    # The diagnostic must name both versions so the operator can fix without
    # having to re-run the script under a debugger.
    assert "0.17.0-0033" in captured.err
    assert "0.17.0-0034" in captured.err
    assert "backend/routers/backup.py" in captured.err


def test_main_surfaces_both_files_on_simultaneous_mismatch(
    tmp_path, script_module, monkeypatch, capsys
):
    """Two-of-three skew — mirrors the actual production divergence bd-9rtlc caught.

    The bug that motivated this guard had ``backup.py`` and ``main.py`` BOTH
    behind ``package.json`` simultaneously (backup.py at 0.16.0 from a stale
    cherry-pick, main.py at 0.16.0-0003 from a 30-build-old miss). The
    diagnostic must enumerate every divergent touchpoint with its actual
    value so an operator can fix all skews in one pass — not whack-a-mole
    them one CI run at a time.
    """
    _build_fake_repo(
        tmp_path,
        version_pkg="0.17.0-0034",  # canonical reference
        version_app="0.17.0-0033",  # one build behind (stale cherry-pick analogue)
        version_fastapi="0.16.0-0003",  # 30-build skew (long-standing miss analogue)
    )
    monkeypatch.setattr(script_module, "REPO_ROOT", tmp_path)

    rc = script_module.main()

    captured = capsys.readouterr()
    assert rc == 1
    err = captured.err
    # Canonical reference must be named so the operator knows which value to bump TO.
    assert "frontend/package.json" in err
    assert "0.17.0-0034" in err
    # Both divergent files must surface, each with its actual divergent value.
    assert "backend/routers/backup.py" in err
    assert "0.17.0-0033" in err
    assert "backend/main.py" in err
    assert "0.16.0-0003" in err


def test_main_returns_one_on_missing_file(tmp_path, script_module, monkeypatch, capsys):
    # Build only two of the three files — main.py is absent.
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"name": "ecm", "version": "0.17.0-0034"}\n'
    )
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "routers").mkdir()
    (tmp_path / "backend" / "routers" / "backup.py").write_text(
        'APP_VERSION = "0.17.0-0034"\n'
    )
    monkeypatch.setattr(script_module, "REPO_ROOT", tmp_path)

    rc = script_module.main()

    captured = capsys.readouterr()
    assert rc == 1
    assert "file not found" in captured.err
    assert "backend/main.py" in captured.err


def test_main_returns_one_on_unparseable_extractor(tmp_path, script_module, monkeypatch, capsys):
    # backup.py exists but has no APP_VERSION assignment — the extractor returns None.
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"name": "ecm", "version": "0.17.0-0034"}\n'
    )
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "routers").mkdir()
    (tmp_path / "backend" / "routers" / "backup.py").write_text(
        "# no APP_VERSION here\n"
    )
    (tmp_path / "backend" / "main.py").write_text(
        'app = FastAPI(\n    version="0.17.0-0034",\n)\n'
    )
    monkeypatch.setattr(script_module, "REPO_ROOT", tmp_path)

    rc = script_module.main()

    captured = capsys.readouterr()
    assert rc == 1
    assert "could not extract" in captured.err
