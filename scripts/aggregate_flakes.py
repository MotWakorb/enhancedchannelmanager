#!/usr/bin/env python3
"""Aggregate flaky tests across the last N successful Tests workflow runs.

This script implements the "flagged-in-last-30-runs" PR-review gate
specified in ``docs/testing.md`` § "Flake baseline gate for PR reviews"
(bead enhancedchannelmanager-tp681). It is invoked by
``.github/workflows/flake-pr-comment.yml`` (bead enhancedchannelmanager-xq19y)
on every PR open / sync to surface known-flaky tests for the reviewer.

Algorithm
---------
1. Walk the GitHub Actions API for the most recent ``--window`` runs
   of the ``Tests`` workflow on the PR's base branch (typically ``dev``).
   We do not filter on conclusion — we want flake context regardless of
   whether the run as a whole passed or failed.
2. For each run, download the ``junit-backend`` and ``junit-frontend``
   artifacts (created by the Tests workflow). Skip runs that have no
   such artifacts (e.g. runs that predate the artifact upload, or where
   the test job aborted before emitting JUnit XML).
3. Parse each ``junit.xml`` and collect any test that reports a
   ``<failure>`` or ``<error>`` child. Skipped tests are not flakes.
4. Emit a JSON report listing every test that failed in **at least one**
   of the inspected runs, with a per-test count of distinct runs that
   saw a failure. Tests with a count >= ``--threshold`` are flagged
   for the PR comment.

The acceptance bar from bead xq19y:

    False-positive rate tuned so a test flagged once in 30 runs does not
    auto-block PRs (only informs the reviewer).

Accordingly, the default ``--threshold`` is 1: every test that has
flapped at least once in the window appears in the report. The PR
comment is informational only — it does not gate merge.

CLI
---

    python scripts/aggregate_flakes.py \\
        --repo MotWakorb/enhancedchannelmanager \\
        --branch dev \\
        --workflow test.yml \\
        --window 30 \\
        --threshold 1 \\
        --token "$GH_TOKEN" \\
        --output flake_report.json

The ``--token`` value must be a GitHub token with ``actions:read`` and
``contents:read`` on the target repo. In CI we pass
``${{ secrets.GITHUB_TOKEN }}``, which carries those scopes by default
for the running repository.

Exit codes:
    0 — report emitted (zero or more flakes found)
    2 — usage / input error
    3 — GitHub API error after the configured retry budget
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aggregate_flakes")

# GitHub REST API base. Hardcoded — we never talk to a different host
# from CI, and configuring it would invite "did you mean to point at
# enterprise?" footguns.
GH_API = "https://api.github.com"

# Conservative retry budget for transient GH API errors (5xx, 429).
# Three tries with exponential backoff covers the bulk of intermittent
# rate-limit / load-shedding events without making the CI step take
# meaningfully longer on the unhappy path.
MAX_RETRIES = 3
BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class TestId:
    """Stable identity for a JUnit test case across runs.

    Combines the ``classname`` (pytest module path / vitest describe
    block) and ``name`` (function / it block). Either may be absent
    in pathological JUnit output; we coerce to empty string so the
    dataclass stays hashable.
    """

    classname: str
    name: str

    def display(self) -> str:
        """Human-readable identifier for the PR comment."""
        if self.classname and self.name:
            return f"{self.classname}::{self.name}"
        return self.classname or self.name or "<unknown>"


@dataclass
class FlakeAggregate:
    """Aggregated flake data across the inspected window."""

    runs_inspected: int = 0
    runs_with_artifacts: int = 0
    # test_id.display() -> set of run_ids in which that test failed
    failures: dict[str, set[int]] = field(default_factory=dict)

    def record_failure(self, test_id: TestId, run_id: int) -> None:
        key = test_id.display()
        self.failures.setdefault(key, set()).add(run_id)

    def to_report(self, threshold: int) -> dict[str, Any]:
        """Produce the JSON-serializable report consumed by the workflow.

        Sorted by failure count desc, then test name asc — gives the
        reviewer the worst-offenders-first ordering with stable output
        for the same input.
        """
        entries = [
            {
                "test": test,
                "fail_count": len(run_ids),
                "run_ids": sorted(run_ids),
            }
            for test, run_ids in self.failures.items()
            if len(run_ids) >= threshold
        ]
        entries.sort(key=lambda e: (-e["fail_count"], e["test"]))
        return {
            "runs_inspected": self.runs_inspected,
            "runs_with_artifacts": self.runs_with_artifacts,
            "threshold": threshold,
            "flake_count": len(entries),
            "flakes": entries,
        }


# ─── HTTP plumbing ────────────────────────────────────────────────────


def _gh_request(
    url: str,
    token: str,
    *,
    accept: str = "application/vnd.github+json",
) -> tuple[bytes, dict[str, str]]:
    """GET ``url`` with retry/backoff. Returns (body, headers).

    Raises ``RuntimeError`` after ``MAX_RETRIES`` consecutive failures.
    """
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(  # noqa: S310 - hardcoded https GH API
            url,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {token}",
                "User-Agent": "ecm-aggregate-flakes/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                body = resp.read()
                # urllib's headers proxy is case-insensitive but coerce
                # to a plain dict so callers get predictable behavior.
                headers = {k: v for k, v in resp.headers.items()}
                return body, headers
        except urllib.error.HTTPError as e:
            # 5xx and 429 are retryable; everything else is terminal.
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "[FLAKES] HTTP %d on %s (attempt %d/%d), retrying in %.1fs",
                    e.code,
                    url,
                    attempt,
                    MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                last_err = e
                continue
            raise RuntimeError(f"GitHub API error {e.code} for {url}: {e.reason}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "[FLAKES] Network error on %s (attempt %d/%d), retrying in %.1fs: %s",
                    url,
                    attempt,
                    MAX_RETRIES,
                    wait,
                    e,
                )
                time.sleep(wait)
                last_err = e
                continue
            raise RuntimeError(f"Network failure contacting {url}: {e}") from e
    # Defensive: loop should always either return or raise above.
    raise RuntimeError(f"Exhausted retries for {url}: {last_err}")


# ─── GitHub API walkers ───────────────────────────────────────────────


def list_workflow_runs(
    repo: str, workflow: str, branch: str, token: str, window: int
) -> list[dict[str, Any]]:
    """Return the most recent ``window`` runs of ``workflow`` on ``branch``.

    Uses the ``actions/workflows/{file}/runs`` endpoint with
    ``per_page=100`` and pages until we have ``window`` runs or run out.
    """
    runs: list[dict[str, Any]] = []
    page = 1
    while len(runs) < window:
        url = (
            f"{GH_API}/repos/{repo}/actions/workflows/{workflow}/runs"
            f"?branch={branch}&per_page=100&page={page}"
        )
        body, _ = _gh_request(url, token)
        payload = json.loads(body)
        page_runs = payload.get("workflow_runs", [])
        if not page_runs:
            break
        runs.extend(page_runs)
        page += 1
        # Defensive cap — GitHub returns total_count but a runaway loop
        # would burn rate-limit budget.
        if page > 10:
            break
    return runs[:window]


def list_run_artifacts(repo: str, run_id: int, token: str) -> list[dict[str, Any]]:
    """List artifacts for a single workflow run."""
    url = f"{GH_API}/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"
    body, _ = _gh_request(url, token)
    payload = json.loads(body)
    return payload.get("artifacts", [])


def download_artifact_zip(
    repo: str, artifact_id: int, token: str
) -> bytes:
    """Download an artifact as zipped bytes."""
    url = f"{GH_API}/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    body, _ = _gh_request(url, token, accept="application/zip")
    return body


# ─── JUnit parsing ────────────────────────────────────────────────────


def extract_failures_from_junit(xml_bytes: bytes) -> list[TestId]:
    """Return the list of failing/erroring test cases in a JUnit XML blob.

    Handles both ``<testsuite>`` (single suite) and
    ``<testsuites><testsuite>...`` (wrapped) layouts. Skipped tests are
    not failures — they are explicit opt-outs and frequently used to
    park known flakes (per ``docs/testing.md`` § "Marking a test as a
    known flake").
    """
    failures: list[TestId] = []
    try:
        root = ET.fromstring(xml_bytes)  # noqa: S314 - trusted CI input
    except ET.ParseError as e:
        logger.warning("[FLAKES] Could not parse JUnit XML: %s", e)
        return failures

    # Normalize: both <testsuites> and <testsuite> roots are valid.
    suites = root.iter("testsuite")
    for suite in suites:
        for case in suite.iter("testcase"):
            # A failing case has either a <failure> or <error> child.
            # <skipped> is intentionally not counted.
            if case.find("failure") is None and case.find("error") is None:
                continue
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            failures.append(TestId(classname=classname, name=name))
    return failures


def collect_failures_from_zip(zip_bytes: bytes) -> list[TestId]:
    """Extract failures from every ``*.xml`` entry in a JUnit artifact zip."""
    failures: list[TestId] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                with zf.open(name) as f:
                    failures.extend(extract_failures_from_junit(f.read()))
    except zipfile.BadZipFile as e:
        logger.warning("[FLAKES] Bad zip artifact: %s", e)
    return failures


# ─── Top-level aggregation ────────────────────────────────────────────


# Artifact names emitted by .github/workflows/test.yml. Keep in sync.
JUNIT_ARTIFACT_NAMES = ("junit-backend", "junit-frontend")


def aggregate(
    repo: str,
    workflow: str,
    branch: str,
    token: str,
    window: int,
) -> FlakeAggregate:
    """Walk the last ``window`` runs and aggregate flake data."""
    agg = FlakeAggregate()
    runs = list_workflow_runs(repo, workflow, branch, token, window)
    agg.runs_inspected = len(runs)
    logger.info(
        "[FLAKES] Inspecting %d run(s) of %s on branch %s",
        agg.runs_inspected,
        workflow,
        branch,
    )

    for run in runs:
        run_id = int(run["id"])
        try:
            artifacts = list_run_artifacts(repo, run_id, token)
        except RuntimeError as e:
            # One bad run shouldn't poison the whole aggregation — log
            # and continue. The PR comment will still surface the
            # remaining runs.
            logger.warning("[FLAKES] Could not list artifacts for run %d: %s", run_id, e)
            continue

        run_had_junit = False
        for art in artifacts:
            if art.get("name") not in JUNIT_ARTIFACT_NAMES:
                continue
            # GitHub expires artifacts; the API returns expired=True for
            # those and the download returns 410.
            if art.get("expired"):
                continue
            try:
                blob = download_artifact_zip(repo, int(art["id"]), token)
            except RuntimeError as e:
                logger.warning(
                    "[FLAKES] Could not download artifact %s (run %d): %s",
                    art.get("name"),
                    run_id,
                    e,
                )
                continue

            run_had_junit = True
            for test_id in collect_failures_from_zip(blob):
                agg.record_failure(test_id, run_id)

        if run_had_junit:
            agg.runs_with_artifacts += 1

    logger.info(
        "[FLAKES] %d / %d runs had JUnit artifacts; %d distinct failing tests recorded",
        agg.runs_with_artifacts,
        agg.runs_inspected,
        len(agg.failures),
    )
    return agg


# ─── CLI ──────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Aggregate flaky tests across the last N Tests workflow runs.",
    )
    p.add_argument("--repo", required=True, help="e.g. MotWakorb/enhancedchannelmanager")
    p.add_argument(
        "--branch",
        required=True,
        help="Branch whose runs to inspect (typically the PR base, e.g. dev).",
    )
    p.add_argument(
        "--workflow",
        default="test.yml",
        help="Workflow file name (default: test.yml).",
    )
    p.add_argument(
        "--window",
        type=int,
        default=30,
        help="Number of most recent runs to inspect (default: 30).",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=1,
        help=(
            "Minimum number of distinct runs in which a test must have "
            "failed to be reported (default: 1, per bead xq19y acceptance)."
        ),
    )
    p.add_argument("--token", required=True, help="GitHub token (actions:read).")
    p.add_argument(
        "--output",
        required=True,
        help="Path to write the JSON flake report.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    args = parse_args(argv)

    if args.window < 1:
        print("--window must be >= 1", file=sys.stderr)
        return 2
    if args.threshold < 1:
        print("--threshold must be >= 1", file=sys.stderr)
        return 2

    try:
        agg = aggregate(
            repo=args.repo,
            workflow=args.workflow,
            branch=args.branch,
            token=args.token,
            window=args.window,
        )
    except RuntimeError as e:
        print(f"[FLAKES] Aggregation failed: {e}", file=sys.stderr)
        return 3

    report = agg.to_report(args.threshold)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    logger.info("[FLAKES] Wrote report to %s (flake_count=%d)", args.output, report["flake_count"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
