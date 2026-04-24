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


def main() -> int:
    missing: list[str] = []
    for mod in REQUIRED_TEST_DEPS:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            missing.append(f"{mod} ({e})")

    if missing:
        print("FATAL: required test dependencies missing:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print(
            "\nThe backend test suite REQUIRES these packages. They are "
            "pinned in backend/requirements.txt. The 'Install dependencies' "
            "step in CI (or your local `pip install -r requirements.txt`) "
            "must have failed silently. See bd-s8kq3.",
            file=sys.stderr,
        )
        return 1

    print(
        "All required test deps importable: "
        + ", ".join(REQUIRED_TEST_DEPS)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
