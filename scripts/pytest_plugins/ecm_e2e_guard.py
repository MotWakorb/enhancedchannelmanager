"""Stop a gate-started pytest run that would collect backend/tests/e2e.

scripts/gate_runner.py loads this module with ``-p ecm_e2e_guard`` and sets
both variables below. Its argument checks read tokens; this plugin reads what
pytest is actually about to collect. testpaths overrides, alternate config
files, symlinks and '..' segments all bypass --ignore=tests/e2e, so every
walked path, named module and collected item is checked by its resolved path.

A refused directory or module is replaced by an empty collector, so no module
under tests/e2e is imported. The run then ends with a usage error (exit code 4)
once collection finishes. Exiting earlier does not work: an exit raised while
pytest walks a directory is reported as a collection error, exit code 2, which
the gate reads as an interrupted run.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ALLOW_ENV = "ECM_GATE_ALLOW_LIVE_E2E"
DIR_ENV = "ECM_GATE_E2E_DIR"
_E2E = pytest.StashKey[Path]()
_ALLOW = pytest.StashKey[bool]()
_REFUSED = pytest.StashKey[list]()


def pytest_configure(config: pytest.Config) -> None:
    allow = os.environ.get(ALLOW_ENV)
    directory = os.environ.get(DIR_ENV)
    if allow not in ("0", "1") or not directory:
        raise pytest.UsageError(
            f"ecm_e2e_guard needs {ALLOW_ENV}=0 or 1 and {DIR_ENV}; "
            "it is loaded by scripts/backend-gate.sh, which sets both")
    config.stash[_ALLOW] = allow == "1"
    config.stash[_E2E] = Path(directory).resolve()
    config.stash[_REFUSED] = []


def _refuse(config: pytest.Config, path: os.PathLike | str) -> bool:
    """Record and report whether ``path`` resolves into tests/e2e without the opt-in."""
    if config.stash[_ALLOW]:
        return False
    e2e = config.stash[_E2E]
    resolved = Path(path).resolve()
    if resolved == e2e or e2e in resolved.parents:
        config.stash[_REFUSED].append(str(path))
        return True
    return False


class _Refused(pytest.File):
    """Stands in for a refused module so that it is never imported."""

    def collect(self):
        return []


class _RefusedDirectory(pytest.Directory):
    """Stands in for a refused directory so that nothing below it is walked."""

    def collect(self):
        return []


# pytest runs its own --ignore check before this hook, so the gate's
# --ignore=tests/e2e still silently prunes the real directory during a walk.
@pytest.hookimpl(tryfirst=True)
def pytest_collect_directory(path: Path, parent: pytest.Collector):
    if _refuse(parent.config, path):
        return _RefusedDirectory.from_parent(parent, path=path)
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(module_path: Path, parent: pytest.Collector):
    if _refuse(parent.config, module_path):
        return _Refused.from_parent(parent, path=module_path)
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        _refuse(config, item.path)


def pytest_collection_finish(session: pytest.Session) -> None:
    refused = session.config.stash[_REFUSED]
    if refused:
        e2e = session.config.stash[_E2E]
        pytest.exit(
            f"refusing to collect {', '.join(sorted(set(refused)))}: it resolves into {e2e} "
            "(tests/e2e), which mutates the live ECM on localhost:6100. Only "
            "scripts/backend-gate.sh --subset --allow-live-e2e with a named tests/e2e path "
            "may run it.",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
