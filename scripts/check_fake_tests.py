#!/usr/bin/env python3
"""Fail CI if unambiguous fake-test markers appear in test files.

A "fake test" is one that passes regardless of whether the code under
test is correct — so it provides zero signal and masks regressions.
This guard catches the two most blatant patterns:

  Python (backend/tests/, mcp-server/tests/):
    assert True

  TypeScript (frontend/src/**/*.test.ts / *.test.tsx):
    expect(true).toBe(true)

Both patterns always pass. Any test that contains one is not a test.

Origin: bead enhancedchannelmanager-ulp7q (test-validity audit, wave 4).
Convention doc: docs/style_guide.md § "Test validity / anti-patterns".

## Suppression

If a specific line is a genuinely intentional tautology (rare — almost
always a sign of a misused assertion helper), suppress it with an inline
allow-comment on the SAME line:

    assert True  # fake-test-ok: <reason>

    expect(true).toBe(true); // fake-test-ok: <reason>

The guard skips any line that contains the literal string "fake-test-ok".
The reason is mandatory — a bare "fake-test-ok" with no explanation will
be rejected at code review.

## Usage

    python scripts/check_fake_tests.py          # from repo root
    python scripts/check_fake_tests.py --quiet  # suppress per-finding output

Exits 0 when clean, 1 when any findings are present.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# ─── Pattern definitions ───────────────────────────────────────────────────────

PYTHON_DIRS = [
    REPO_ROOT / "backend" / "tests",
    REPO_ROOT / "mcp-server" / "tests",
]
PYTHON_EXTENSIONS = {".py"}
# Matches `assert True` as a standalone statement (not inside a string literal
# or comment). We use a simple line-level check: strip the line, look for
# `assert True` at the start (after stripping indentation). This avoids the
# complexity of a full Python parser while catching every real case.
_PYTHON_PATTERN = re.compile(r"^\s*assert\s+True\b")

FRONTEND_DIRS = [
    REPO_ROOT / "frontend" / "src",
]
FRONTEND_EXTENSIONS = {".ts", ".tsx"}
FRONTEND_TEST_SUFFIXES = (".test.ts", ".test.tsx")
# Matches `expect(true).toBe(true)` anywhere on a line (handles leading
# whitespace and surrounding async/await patterns).
_FRONTEND_PATTERN = re.compile(r"expect\(true\)\.toBe\(true\)")

ALLOW_COMMENT = "fake-test-ok"


# ─── Scanner ──────────────────────────────────────────────────────────────────


def _scan_files(
    dirs: list[Path],
    extensions: set[str],
    pattern: re.Pattern[str],
    *,
    test_suffix_filter: tuple[str, ...] | None = None,
) -> list[tuple[Path, int, str]]:
    """Return a list of (path, lineno, line) tuples for every match."""
    findings: list[tuple[Path, int, str]] = []
    for base_dir in dirs:
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("*")):
            if path.suffix not in extensions:
                continue
            if test_suffix_filter is not None:
                if not any(path.name.endswith(s) for s in test_suffix_filter):
                    continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if ALLOW_COMMENT in line:
                    continue
                if pattern.search(line):
                    findings.append((path, lineno, line.rstrip()))
    return findings


# ─── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-finding output; only print the summary.",
    )
    args = parser.parse_args(argv)

    python_findings = _scan_files(
        PYTHON_DIRS,
        PYTHON_EXTENSIONS,
        _PYTHON_PATTERN,
    )
    frontend_findings = _scan_files(
        FRONTEND_DIRS,
        FRONTEND_EXTENSIONS,
        _FRONTEND_PATTERN,
        test_suffix_filter=FRONTEND_TEST_SUFFIXES,
    )

    all_findings = python_findings + frontend_findings

    if not args.quiet:
        for path, lineno, line in all_findings:
            rel = path.relative_to(REPO_ROOT)
            print(f"FAKE TEST  {rel}:{lineno}: {line.strip()}")

    if all_findings:
        count = len(all_findings)
        print(
            f"\n✗ {count} fake-test marker(s) found.\n"
            "  These assertions always pass and provide zero signal.\n"
            "  See docs/style_guide.md § 'Test validity / anti-patterns'.\n"
            "  To suppress a genuinely intentional tautology, add an inline\n"
            "  comment containing 'fake-test-ok: <reason>' on the same line.",
            file=sys.stderr,
        )
        return 1

    print(f"✓ No fake-test markers found in Python test dirs or frontend *.test.ts/tsx files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
