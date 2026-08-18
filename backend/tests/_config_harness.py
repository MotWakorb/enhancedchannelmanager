"""Deterministic, process-isolated configuration for backend tests.

This module must stay free of application imports: ``conftest.py`` invokes it
before importing modules which bind ``CONFIG_DIR`` at import time.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import MutableMapping
from pathlib import Path


_SESSION_PREFIX = "ecm-pytest-"
_OWNERSHIP_MARKER = ".ecm-pytest-owned"
_AUTH_BASELINE = {
    "setup_complete": False,
    "require_auth": False,
}


def initialize_test_config(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    """Create and select a fresh auth-disabled config for one pytest process.

    An incoming ``CONFIG_DIR`` is intentionally ignored. It may name a stale
    developer directory or an operator-controlled directory on a self-hosted
    runner. ``ECM_TEST_CONFIG_ROOT`` controls only the parent in which a unique
    directory is created; it is never itself used as application config.
    """
    environment = os.environ if environ is None else environ
    configured_root = environment.get("ECM_TEST_CONFIG_ROOT")
    root = Path(configured_root) if configured_root else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)

    config_dir = Path(tempfile.mkdtemp(prefix=_SESSION_PREFIX, dir=root))
    config_dir.chmod(0o700)
    (config_dir / _OWNERSHIP_MARKER).write_text("pytest\n", encoding="utf-8")
    auth_path = config_dir / "auth_settings.json"
    auth_path.write_text(
        json.dumps(_AUTH_BASELINE, indent=2) + "\n",
        encoding="utf-8",
    )
    auth_path.chmod(0o600)
    environment["CONFIG_DIR"] = str(config_dir)
    return config_dir


def cleanup_test_config(config_dir: Path) -> None:
    """Remove only a directory created and marked by this harness."""
    config_dir = Path(config_dir)
    marker = config_dir / _OWNERSHIP_MARKER
    if config_dir.name.startswith(_SESSION_PREFIX) and marker.is_file():
        shutil.rmtree(config_dir)
