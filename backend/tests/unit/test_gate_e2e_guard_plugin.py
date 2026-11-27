"""The gate's pytest plugin refuses any run that collects backend/tests/e2e.

The gate's argument checks read tokens. pytest decides what to collect only
after testpaths overrides, alternate config files, symlinks and '..' segments,
none of which --ignore=tests/e2e covers for paths pytest treats as named. The
plugin (scripts/pytest_plugins/ecm_e2e_guard.py) checks the resolved path of
everything pytest is about to collect.

Every tree here is a throwaway copy with dummy tests and no e2e code; nothing
contacts a network.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = ROOT / "scripts/pytest_plugins"

DUMMY_TEST = '''\
import os

with open(os.environ["E2E_MARKS"] + "/imported", "a") as stream:
    stream.write(__name__ + "\\n")


def test_marks_that_it_ran():
    with open(os.environ["E2E_MARKS"] + "/ran", "a") as stream:
        stream.write(__name__ + "\\n")
'''

needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")


def _backend(root: Path) -> Path:
    backend = root / "backend"
    (backend / "tests/e2e/sub").mkdir(parents=True)
    (backend / "tests/unit").mkdir()
    (backend / "pytest.ini").write_text("[pytest]\ntestpaths = tests\naddopts = --ignore=tests/e2e\n")
    (backend / "tests/e2e/test_live.py").write_text(DUMMY_TEST)
    (backend / "tests/e2e/sub/test_deep.py").write_text(DUMMY_TEST)
    (backend / "tests/unit/test_ok.py").write_text("def test_ok():\n    pass\n")
    return backend


def _link(backend: Path) -> None:
    (backend / "tests/unit/linked").symlink_to(Path("..") / "e2e", target_is_directory=True)


def _seen(marks: Path) -> dict:
    return {name: (marks / name).read_text().split("\n")[:-1] if (marks / name).exists() else []
            for name in ("imported", "ran")}


@pytest.fixture
def guard_tree(tmp_path):
    backend = _backend(tmp_path)
    marks = tmp_path / "marks"
    marks.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "ECM_E2E_LIVE")}
    env.update(PYTHONPATH=str(PLUGIN_DIR), E2E_MARKS=str(marks),
               ECM_GATE_ALLOW_LIVE_E2E="0",
               ECM_GATE_E2E_DIR=str((backend / "tests/e2e").resolve()))
    return backend, marks, env


def _pytest(guard_tree, *args, **changes):
    backend, marks, env = guard_tree
    env = {**env, **changes}
    env = {key: value for key, value in env.items() if value is not None}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "ecm_e2e_guard", "-p", "no:cacheprovider",
         "--no-cov", "-q", *args],
        cwd=backend, env=env, text=True, capture_output=True, timeout=120,
    )
    return result, _seen(marks)


BLOCKED = [
    (["tests/e2e"], False),
    (["tests/e2e/sub"], False),
    (["tests/e2e/test_live.py"], False),
    (["tests/e2e/sub/test_deep.py::test_marks_that_it_ran"], False),
    (["-o", "testpaths=tests/e2e"], False),
    (["-c", "pytest.ini", "-o", "testpaths=tests/e2e"], False),
    (["-o", "addopts=", "tests"], False),
    (["-p", "no:skipping", "tests/e2e"], False),
    (["tests/unit/../e2e"], False),
    (["tests/unit/../e2e/sub/test_deep.py"], False),
    pytest.param(["tests/unit"], True, marks=needs_symlinks, id="symlink-found-by-walk"),
    pytest.param(["tests/unit/linked"], True, marks=needs_symlinks, id="symlink-named-dir"),
    pytest.param(["tests/unit/linked/test_live.py"], True, marks=needs_symlinks,
                 id="symlink-named-file"),
]


@pytest.mark.parametrize("args, link", BLOCKED)
def test_collecting_e2e_without_the_opt_in_fails_before_import(guard_tree, args, link):
    if link:
        _link(guard_tree[0])
    result, seen = _pytest(guard_tree, *args)
    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "tests/e2e" in output and "--allow-live-e2e" in output, output
    assert seen == {"imported": [], "ran": []}, output


def test_a_selection_outside_e2e_runs(guard_tree):
    result, seen = _pytest(guard_tree, "tests/unit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert seen == {"imported": [], "ran": []}


def test_the_default_selection_runs_and_never_reaches_e2e(guard_tree):
    result, seen = _pytest(guard_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert seen == {"imported": [], "ran": []}


@pytest.mark.parametrize("args, expected", [
    (["tests/e2e/test_live.py"], ["test_live"]),
    (["tests/e2e"], ["test_deep", "test_live"]),
])
def test_with_the_opt_in_a_named_e2e_path_runs_despite_the_ini_ignore(guard_tree, args, expected):
    result, seen = _pytest(guard_tree, *args, ECM_GATE_ALLOW_LIVE_E2E="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(seen["ran"]) == expected


@pytest.mark.parametrize("changes", [
    {"ECM_GATE_ALLOW_LIVE_E2E": None},
    {"ECM_GATE_ALLOW_LIVE_E2E": "yes"},
    {"ECM_GATE_ALLOW_LIVE_E2E": ""},
    {"ECM_GATE_E2E_DIR": None},
    {"ECM_GATE_E2E_DIR": ""},
])
def test_the_plugin_fails_closed_without_the_gate_settings(guard_tree, changes):
    result, seen = _pytest(guard_tree, "tests/unit", **changes)
    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "ECM_GATE_ALLOW_LIVE_E2E" in output, output
    assert seen == {"imported": [], "ran": []}


# --- the real gate, end to end, with real pytest ------------------------------

@pytest.fixture
def real_gate_tree(tmp_path):
    root = tmp_path / "checkout"
    shutil.copytree(ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    backend = _backend(root)
    (backend / "main.py").write_text("pass\n")
    (backend / "requirements.txt").write_text("demo==1.0\n")
    metadata = tmp_path / "modules/demo-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: demo\nVersion: 1.0\n")
    marks = tmp_path / "marks"
    marks.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "ECM_E2E_LIVE",
                          "ECM_GATE_ALLOW_LIVE_E2E", "ECM_GATE_E2E_DIR")}
    env.update(ECM_PYTHON=sys.executable, PYTHONPATH=str(tmp_path / "modules"),
               E2E_MARKS=str(marks), TMPDIR=str(tmp_path))
    return root, marks, env


def _gate(real_gate_tree, *args):
    root, marks, env = real_gate_tree
    result = subprocess.run([sys.executable, str(root / "scripts/gate_runner.py"), *args],
                            cwd=root.parent, env=env, text=True, capture_output=True, timeout=180)
    [line] = [line for line in result.stdout.splitlines() if line.startswith("ECM_GATE_COMPLETE ")]
    return result, json.loads(line.split(" ", 1)[1]), _seen(marks)


@needs_symlinks
def test_gate_subset_fails_when_pytest_walks_a_symlink_into_e2e(real_gate_tree):
    _link(real_gate_tree[0] / "backend")
    result, data, seen = _gate(real_gate_tree, "--subset", "tests/unit")
    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert data["status"] == "failed" and data["exit_code"] == 4
    assert "--allow-live-e2e" in output, output
    assert seen == {"imported": [], "ran": []}, output


def test_gate_subset_outside_e2e_passes_with_real_pytest(real_gate_tree):
    result, data, seen = _gate(real_gate_tree, "--subset", "tests/unit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert data["status"] == "passed"
    assert seen == {"imported": [], "ran": []}


def test_gate_documented_live_opt_in_still_reaches_a_named_e2e_file(real_gate_tree):
    result, data, seen = _gate(real_gate_tree, "--subset", "--allow-live-e2e",
                               "tests/e2e/test_live.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert data["status"] == "passed" and data["allow_live_e2e"] is True
    assert seen["ran"] == ["test_live"]
