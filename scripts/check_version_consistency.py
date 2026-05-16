#!/usr/bin/env python3
"""Verify that all hardcoded version touchpoints are in sync.

This is a CI guard (bd-9rtlc). Today's version-bump workflow requires manually
editing the same version string in three separate files:

  1. frontend/package.json — the canonical source of truth (also baked into
     the Docker image at build time via the ECM_VERSION build-arg).
  2. backend/routers/backup.py — APP_VERSION literal used in the export
     manifest (read by `from routers.backup import APP_VERSION` in
     auto_creation.py for the rule-export ecm_version field).
  3. backend/main.py — FastAPI `app = FastAPI(version="...")` parameter,
     surfaced in the OpenAPI schema served at /api/openapi.json.

Before this guard was added, divergence happened repeatedly:

  - bd-lkyg5 cherry-pick (PR #277): backend/routers/backup.py was at
    "0.16.0" while frontend/package.json was at "0.17.0-0027" — a long-
    standing skew that only got caught because the cherry-pick happened
    to touch backup.py.
  - 9rtlc audit (this bead): backend/main.py was found at "0.16.0-0003"
    while frontend/package.json was at "0.17.0-0033" — a 30-build skew
    nobody had noticed because the FastAPI version parameter only shows
    up in the OpenAPI schema, not the UI footer or /api/version response.

This script asserts the three touchpoints match and exits non-zero with a
clear diagnostic if they don't, so the next CI run on a missed-touchpoint
PR fails before merge instead of leaking divergence into dev.

Usage (from repo root):

    python scripts/check_version_consistency.py

Exits 0 on success; exits 1 with a diagnostic to stderr on mismatch.

When you add a new version-bump touchpoint:

  1. Add it to TOUCHPOINTS below with a name + path + extractor.
  2. Add it to docs/versioning.md "Touchpoints" section so the manual
     bump workflow stays accurate.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

# Resolve repo root from this script's location: scripts/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_package_json_version(text: str) -> str | None:
    """Pull the top-level "version" field out of frontend/package.json."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    value = data.get("version")
    return value if isinstance(value, str) else None


def _extract_app_version_literal(text: str) -> str | None:
    """Pull the APP_VERSION = "..." literal out of backend/routers/backup.py.

    Tolerates surrounding whitespace and either quote style. Stops at the
    first match (the assignment is canonical and only assigned once).
    """
    match = re.search(r'^\s*APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else None


def _extract_fastapi_version_kwarg(text: str) -> str | None:
    """Pull the version="..." kwarg passed to FastAPI(...) in backend/main.py.

    The FastAPI constructor call spans multiple lines; we match the kwarg
    independently of line wrapping. Restricted to indentation that matches
    the canonical call site (kwarg on its own indented line) so we do not
    accidentally pick up `version=app.version` on the OpenAPI re-emit line
    or any other downstream `version=` assignment.
    """
    match = re.search(r'^\s+version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else None


# Each entry: (display name, repo-relative path, extractor callable).
# Extractor returns the version string or None when the file cannot be parsed.
TOUCHPOINTS: tuple[tuple[str, str, Callable[[str], str | None]], ...] = (
    (
        "frontend/package.json (canonical)",
        "frontend/package.json",
        _extract_package_json_version,
    ),
    (
        "backend/routers/backup.py APP_VERSION",
        "backend/routers/backup.py",
        _extract_app_version_literal,
    ),
    (
        "backend/main.py FastAPI(version=...)",
        "backend/main.py",
        _extract_fastapi_version_kwarg,
    ),
)


def main() -> int:
    found: list[tuple[str, str, str | None]] = []
    parse_errors: list[str] = []

    for name, rel_path, extractor in TOUCHPOINTS:
        full_path = REPO_ROOT / rel_path
        try:
            text = full_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            parse_errors.append(f"{name} ({rel_path}): file not found")
            continue

        version = extractor(text)
        if version is None:
            parse_errors.append(
                f"{name} ({rel_path}): could not extract version literal — "
                "the extractor pattern may need updating after a refactor"
            )
            continue

        found.append((name, rel_path, version))

    if parse_errors:
        print("FATAL: version-touchpoint extraction failed:", file=sys.stderr)
        for err in parse_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # All extractors succeeded; compare values against the canonical source.
    canonical_name, canonical_path, canonical_version = found[0]
    mismatches = [
        (name, path, version)
        for name, path, version in found[1:]
        if version != canonical_version
    ]

    if mismatches:
        print(
            f"FATAL: version skew detected — touchpoints disagree with "
            f"canonical {canonical_path} ({canonical_version!r}):",
            file=sys.stderr,
        )
        for name, path, version in mismatches:
            print(
                f"  - {name} ({path}) = {version!r} — expected {canonical_version!r}",
                file=sys.stderr,
            )
        print(
            "\nA version bump must update every touchpoint in lockstep. "
            "See docs/versioning.md → Touchpoints for the canonical list. "
            "Run scripts/check_version_consistency.py locally before pushing.",
            file=sys.stderr,
        )
        return 1

    print(
        f"All {len(found)} version touchpoints match canonical "
        f"{canonical_path} ({canonical_version}):"
    )
    for name, path, version in found:
        print(f"  - {name} = {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
