#!/usr/bin/env python3
"""Render a one-line run summary for a required CI check.

Origin: bead enhancedchannelmanager-t4d5w.

## Why this exists

Six of the seven required status checks on `dev` gate their real work on the
documentation-only classifier. That is correct, but the check NAME is the same
either way: `Backend Tests` reads as "the backend tests ran" whether pytest
executed 2000 tests or the job printed a sentence and exited. A reader of the
checks rollup, human or agent, cannot tell the two apart without opening the
job log.

So every required job writes one line to `$GITHUB_STEP_SUMMARY` saying what it
actually did. For the three jobs that produce a JUnit report, this script turns
that report into a test count, so the line carries evidence rather than a
claim. It changes no conclusion and gates nothing.

## Never fails the job

A summary writer that can fail turns a passing suite into a red check for a
cosmetic reason. Every error path here degrades to a line that says the count
was unavailable, and the script exits 0 unconditionally. Callers should still
run it under `if: always()` so the summary appears on a failed run too, which
is exactly when knowing the count matters.

## Usage

    python scripts/ci_junit_summary.py \
        --label "Backend Tests" \
        --ran "ran the backend pytest suite" \
        --junit backend/junit.xml
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Attributes JUnit XML carries on each <testsuite>. `tests` counts every case
# including the ones that were skipped.
_COUNTERS = ("tests", "failures", "errors", "skipped")


def read_counts(junit_path: Path) -> dict[str, int] | None:
    """Sum the JUnit counters across every <testsuite> in `junit_path`.

    Returns None when the report is missing or unparseable. Both are ordinary
    outcomes: a suite that died before writing its report, or a job whose
    real work was gated off, leaves nothing to read.
    """
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError):
        return None

    # A pytest report is rooted at <testsuites>; some writers emit a bare
    # <testsuite>. `iter` covers both, and includes the root when it is
    # itself a <testsuite>.
    suites = list(root.iter("testsuite"))
    if not suites:
        return None

    counts = {key: 0 for key in _COUNTERS}
    for suite in suites:
        for key in _COUNTERS:
            try:
                counts[key] += int(suite.get(key, 0))
            except (TypeError, ValueError):
                # A malformed attribute should cost that one counter, not the
                # whole summary line.
                continue
    return counts


def format_line(label: str, ran: str, counts: dict[str, int] | None) -> str:
    """Build the single Markdown line the job appends to the step summary."""
    if counts is None:
        return f"**{label}**: {ran} (test count unavailable: no readable JUnit report)."
    detail = f"{counts['tests']} tests, {counts['failures']} failed, {counts['errors']} errored"
    if counts["skipped"]:
        detail += f", {counts['skipped']} skipped"
    return f"**{label}**: {ran}. {detail}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True, help="The required check's name.")
    parser.add_argument(
        "--ran",
        required=True,
        help="Verb phrase describing the work that ran, e.g. 'ran the backend pytest suite'.",
    )
    parser.add_argument(
        "--junit",
        type=Path,
        required=True,
        help="Path to the JUnit XML report produced by the run.",
    )
    args = parser.parse_args(argv)

    print(format_line(args.label, args.ran, read_counts(args.junit)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:  # pragma: no cover - last-resort guard
        # A cosmetic summary must never be the reason a green suite reports red.
        print(f"::warning::could not render the run summary: {error}", file=sys.stderr)
        raise SystemExit(0) from None
