#!/usr/bin/env python3
"""Fail-closed policy primitives for PRs targeting ``main``.

The workflow deliberately keeps parsing here so its security decisions are
fixture-tested instead of embedded in untestable shell conditionals.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class PolicyError(ValueError):
    """The supplied release-policy input is invalid or unsafe."""


RELEASE_TITLE = re.compile(r"Release v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\Z")
RELEASE_BRANCH = re.compile(r"release/v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\Z")
HOTFIX_TITLE = re.compile(r"Hotfix v(?P<version>[0-9]+\.[0-9]+\.[0-9]+): [^\r\n]+\Z")
HOTFIX_BRANCH = re.compile(
    r"hotfix/v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-[a-z0-9]+(?:-[a-z0-9]+)*\Z"
)
KNOWN_STATUSES = frozenset({"open", "in_progress", "blocked", "deferred", "closed"})
REQUIRED_ISSUE_FIELDS = frozenset({"id", "title", "priority", "status"})


def classify_main_pr(title: str, head_ref: str) -> tuple[str, str]:
    """Return the allowed main-bound PR kind and version, or reject it."""
    pairs = (
        ("release", RELEASE_TITLE.fullmatch(title), RELEASE_BRANCH.fullmatch(head_ref)),
        ("hotfix", HOTFIX_TITLE.fullmatch(title), HOTFIX_BRANCH.fullmatch(head_ref)),
    )
    for kind, title_match, branch_match in pairs:
        if title_match and branch_match:
            title_version = title_match.group("version")
            branch_version = branch_match.group("version")
            if title_version != branch_version:
                raise PolicyError(
                    f"{kind} title version {title_version} does not match branch version "
                    f"{branch_version}"
                )
            return kind, title_version
    raise PolicyError(
        "PRs targeting main must use a matching release/vX.Y.Z + 'Release vX.Y.Z' "
        "shape or hotfix/vX.Y.Z-description + 'Hotfix vX.Y.Z: description' shape"
    )


def _validated_issue(value: Any, line_number: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"board line {line_number} is not a JSON object")
    missing = REQUIRED_ISSUE_FIELDS.difference(value)
    if missing:
        raise PolicyError(
            f"board line {line_number} is missing required fields: {', '.join(sorted(missing))}"
        )
    if not isinstance(value["id"], str) or not isinstance(value["title"], str):
        raise PolicyError(f"board line {line_number} has invalid id/title types")
    if not isinstance(value["priority"], int) or isinstance(value["priority"], bool):
        raise PolicyError(f"board line {line_number} has a non-integer priority")
    if value["status"] not in KNOWN_STATUSES:
        raise PolicyError(
            f"board line {line_number} has unknown status {value['status']!r}; "
            "policy must be updated before releases continue"
        )
    return value


def find_priority_blockers(board_path: Path) -> list[dict[str, Any]]:
    """Return every unresolved P0/P1 from the authoritative JSONL export."""
    try:
        contents = board_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(
            f"cannot read authoritative board {board_path}: {exc}"
        ) from exc
    if not contents.strip():
        raise PolicyError(f"authoritative board {board_path} is empty")

    blockers: list[dict[str, Any]] = []
    for line_number, line in enumerate(contents.splitlines(), start=1):
        if not line.strip():
            raise PolicyError(f"board line {line_number} is blank")
        try:
            issue = _validated_issue(json.loads(line), line_number)
        except json.JSONDecodeError as exc:
            raise PolicyError(
                f"board line {line_number} is invalid JSON: {exc.msg}"
            ) from exc
        if issue["priority"] in {0, 1} and issue["status"] != "closed":
            blockers.append(
                {
                    "id": issue["id"],
                    "title": issue["title"],
                    "priority": issue["priority"],
                    "status": issue["status"],
                }
            )
    return blockers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify = subparsers.add_parser("classify-pr")
    classify.add_argument("--title", required=True)
    classify.add_argument("--head-ref", required=True)
    board = subparsers.add_parser("check-board")
    board.add_argument("board", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "classify-pr":
            kind, version = classify_main_pr(args.title, args.head_ref)
            print(json.dumps({"kind": kind, "version": version}))
            return 0

        blockers = find_priority_blockers(args.board)
        if blockers:
            print(
                f"G1a FAIL: {len(blockers)} unresolved P0/P1 bead(s) in authoritative board",
                file=sys.stderr,
            )
            for issue in blockers:
                print(
                    f"  {issue['id']} (P{issue['priority']}, {issue['status']}): "
                    f"{issue['title']}",
                    file=sys.stderr,
                )
            return 1
        print("G1a PASS: 0 unresolved P0/P1 beads in authoritative board")
        return 0
    except PolicyError as exc:
        print(f"POLICY INPUT FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
