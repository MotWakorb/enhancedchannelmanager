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

## The summary must not repeat the lie it exists to expose

Callers run this under `if: always()` so the line appears on a failed run too,
which is exactly when knowing the count matters. But `always()` also fires
when the suite never ran at all, because an earlier step in the same job
failed first. Printing "ran the backend pytest suite" there would be the same
class of untruth this whole change exists to close, one layer down.

So `--outcome` takes the GitHub `steps.<id>.outcome` of the step that does the
work. An empty value means the step never executed, and the line says so. The
flag defaults to empty, which understates rather than overstates: a caller
that forgets it gets "did NOT run", never a false claim that it did.

## Never fails the job

A summary writer that can fail turns a passing suite into a red check for a
cosmetic reason. Every error path in `read_counts` degrades to a line saying
the count was unavailable, and `main` returns 0 on every path it reaches.
Argparse is the one exception: a malformed invocation exits 2 before `main`
runs. That is a wiring bug, not a runtime condition, and callers pass
`continue-on-error: true` so even that cannot redden a required check.

## Usage

    python scripts/ci_junit_summary.py \
        --label "Backend Tests" \
        --ran "ran the backend pytest suite" \
        --junit backend/junit.xml \
        --outcome "${{ steps.pytest.outcome }}"
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


def format_line(
    label: str,
    ran: str,
    counts: dict[str, int] | None,
    outcome: str = "",
) -> str:
    """Build the single Markdown line the job appends to the step summary.

    `outcome` is the GitHub `steps.<id>.outcome` of the step that does the
    work. Empty or `skipped` means it never executed, so the line reports
    that rather than claiming work that did not happen.
    """
    normalised = (outcome or "").strip().lower()
    if normalised in ("", "skipped"):
        return f"**{label}**: did NOT run ({ran} was skipped or never reached)."

    trailer = "" if normalised == "success" else f" The step reported {normalised}."
    if counts is None:
        return (
            f"**{label}**: {ran} (test count unavailable: no readable JUnit "
            f"report).{trailer}"
        )
    detail = f"{counts['tests']} tests, {counts['failures']} failed, {counts['errors']} errored"
    if counts["skipped"]:
        detail += f", {counts['skipped']} skipped"
    return f"**{label}**: {ran}. {detail}.{trailer}"


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
    parser.add_argument(
        "--outcome",
        default="",
        help=(
            "GitHub steps.<id>.outcome of the step that does the work. Empty "
            "means the step never executed, and the line will say so."
        ),
    )
    args = parser.parse_args(argv)

    print(format_line(args.label, args.ran, read_counts(args.junit), args.outcome))
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
