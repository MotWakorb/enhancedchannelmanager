#!/usr/bin/env python3
"""Verify that test-required Python packages are importable.

This is a CI fast-fail guard (bd-s8kq3). The backend test suite requires
several packages from `backend/requirements.txt` that, if missing, would
otherwise cause:

  - silent skips (e.g. `pytest.importorskip("hypothesis")` returning a
    "skipped" line that CI treats as success)
  - cryptic mid-suite collection errors (bare `import hypothesis`
    failing during test collection on one specific file)

By verifying these imports BEFORE pytest runs, an install-gap surfaces
as a clear, single-line error with actionable remediation, instead of
being buried in pytest output.

Usage (from `backend/`):

    python scripts/verify_test_deps.py

Exits 0 if all required packages import cleanly; exits 1 with a
diagnostic to stderr otherwise.
"""
from __future__ import annotations

import importlib
import shutil
import sys

# Packages required by the backend test suite. Each is pinned in
# `backend/requirements.in` (and resolved into `requirements.txt`).
# When you add a hard test dependency that, if absent, would cause a
# silent skip or mid-suite collection error, add it here.
REQUIRED_TEST_DEPS = [
    "alembic",          # tests/unit/test_alembic_baseline.py + safe_regex migration tests
    "hypothesis",       # property-based tests (test_regex_lint_property, safe_regex migration tests)
    "pytest",
    "pytest_asyncio",
    "httpx",
]

REQUIRED_TEST_BINARIES = [
    "jq",  # workflow changed-path transport/rename-source contract tests
]


def main() -> int:
    missing_python: list[str] = []
    missing_binaries: list[str] = []
    for mod in REQUIRED_TEST_DEPS:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            missing_python.append(f"{mod} ({e})")

    for binary in REQUIRED_TEST_BINARIES:
        if shutil.which(binary) is None:
            missing_binaries.append(binary)

    if missing_python or missing_binaries:
        if missing_python:
            print("FATAL: required Python test packages missing:", file=sys.stderr)
            for dependency in missing_python:
                print(f"  - {dependency}", file=sys.stderr)
            print("Install backend/requirements.txt before pytest.", file=sys.stderr)
        if missing_binaries:
            print("FATAL: required OS test binaries missing:", file=sys.stderr)
            for binary in missing_binaries:
                print(f"  - {binary}", file=sys.stderr)
            print(
                "Install jq with the host package manager (for example, "
                "`apt-get install jq`) and ensure it is on PATH.",
                file=sys.stderr,
            )
        return 1

    print(
        "All required test deps available: "
        + ", ".join(REQUIRED_TEST_DEPS + REQUIRED_TEST_BINARIES)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
