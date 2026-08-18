#!/usr/bin/env python3
"""Validate that a workflow-run candidate may publish mutable image tags."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


class PolicyError(ValueError):
    """Publishing input is missing, stale, or not authoritative."""


REQUIRED_WORKFLOWS = frozenset({"Tests", "Build and Push Docker Image"})
ALLOWED_BRANCHES = frozenset({"dev", "main"})


def _latest_run(runs: list[dict[str, Any]], workflow: str) -> dict[str, Any]:
    matches = [run for run in runs if run.get("name") == workflow]
    if not matches:
        raise PolicyError(f"missing {workflow!r} workflow run")
    return max(
        matches,
        key=lambda run: (
            int(run.get("run_number", -1)),
            int(run.get("run_attempt", -1)),
            int(run.get("id", -1)),
        ),
    )


def validate_publish_candidate(
    *, trigger: dict[str, Any], branch_sha: str, runs: list[dict[str, Any]]
) -> tuple[str, str]:
    """Return ``(branch, sha)`` only for the current fully-green push commit."""
    sha = trigger.get("head_sha")
    branch = trigger.get("head_branch")
    if not isinstance(sha, str) or not sha:
        raise PolicyError("trigger is missing head_sha")
    if branch not in ALLOWED_BRANCHES:
        raise PolicyError(f"branch {branch!r} is not publishable")
    if trigger.get("event") != "push":
        raise PolicyError("only push-origin workflow runs may publish")
    if trigger.get("status") != "completed":
        raise PolicyError("trigger workflow is not completed")
    if branch_sha != sha:
        raise PolicyError(
            f"stale trigger SHA {sha}; refs/heads/{branch} currently points to {branch_sha}"
        )

    for workflow in REQUIRED_WORKFLOWS:
        run = _latest_run(runs, workflow)
        if run.get("head_sha") != sha or run.get("head_branch") != branch:
            raise PolicyError(f"latest {workflow!r} run is for the wrong revision")
        if run.get("event") != "push":
            raise PolicyError(f"latest {workflow!r} run is not push-origin")
        if run.get("status") != "completed":
            raise PolicyError(f"latest {workflow!r} run is still {run.get('status')!r}")
        if run.get("conclusion") != "success":
            raise PolicyError(
                f"latest {workflow!r} run concluded {run.get('conclusion')!r}"
            )
    return branch, sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--branch-sha", required=True)
    parser.add_argument("--runs", required=True)
    args = parser.parse_args(argv)
    try:
        branch, sha = validate_publish_candidate(
            trigger=json.loads(args.trigger),
            branch_sha=args.branch_sha,
            runs=json.loads(args.runs),
        )
    except (PolicyError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"IMAGE PUBLISH DENIED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"branch": branch, "sha": sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
