"""Regression tests for the main-bound release policy gate."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(
    os.environ.get(
        "RELEASE_GATE_POLICY_SCRIPT", ROOT / "scripts" / "release_gate_policy.py"
    )
)
WORKFLOW = ROOT / ".github" / "workflows" / "release-cut-gate.yml"


@pytest.fixture(scope="module")
def policy():
    spec = importlib.util.spec_from_file_location("release_gate_policy", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("title", "head", "kind", "version"),
    [
        ("Release v1.2.3", "release/v1.2.3", "release", "1.2.3"),
        (
            "Hotfix v1.2.4: reset regression",
            "hotfix/v1.2.4-reset-regression",
            "hotfix",
            "1.2.4",
        ),
    ],
)
def test_allowed_main_pr_shapes_are_explicit(policy, title, head, kind, version):
    assert policy.classify_main_pr(title, head) == (kind, version)


@pytest.mark.parametrize(
    ("title", "head"),
    [
        ("docs: harmless", "docs/harmless"),
        ("Release v1.2.3", "feature/v1.2.3"),
        ("Almost Release v1.2.3", "release/v1.2.3"),
        ("Release v1.2.4", "release/v1.2.3"),
        ("Hotfix v1.2.4: reset regression", "hotfix/v1.2.4"),
        ("Hotfix v1.2.5: reset regression", "hotfix/v1.2.4-reset-regression"),
        ("Release v1.2.3\nignored", "release/v1.2.3"),
    ],
)
def test_unrecognized_or_mismatched_main_prs_fail_closed(policy, title, head):
    with pytest.raises(policy.PolicyError):
        policy.classify_main_pr(title, head)


def _write_board(path: Path, issues: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(issue) + "\n" for issue in issues), encoding="utf-8"
    )


@pytest.mark.parametrize("status", ["open", "in_progress", "blocked", "deferred"])
def test_every_unresolved_p0_or_p1_status_blocks(policy, tmp_path, status):
    board = tmp_path / "issues.jsonl"
    _write_board(
        board,
        [
            {
                "id": "bd-blocker",
                "title": "known defect",
                "priority": 1,
                "status": status,
            }
        ],
    )
    assert policy.find_priority_blockers(board) == [
        {"id": "bd-blocker", "title": "known defect", "priority": 1, "status": status}
    ]


def test_closed_and_lower_priority_items_do_not_block(policy, tmp_path):
    board = tmp_path / "issues.jsonl"
    _write_board(
        board,
        [
            {"id": "bd-done", "title": "fixed", "priority": 0, "status": "closed"},
            {"id": "bd-next", "title": "later", "priority": 2, "status": "open"},
        ],
    )
    assert policy.find_priority_blockers(board) == []


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "not json\n",
        '{"id":"missing-fields"}\n',
        '{"id":"odd","title":"odd","priority":1,"status":"mystery"}\n',
    ],
)
def test_missing_malformed_or_unknown_board_input_fails_closed(
    policy, tmp_path, contents
):
    board = tmp_path / "issues.jsonl"
    board.write_text(contents, encoding="utf-8")
    with pytest.raises(policy.PolicyError):
        policy.find_priority_blockers(board)


def test_workflow_consumes_dedicated_authoritative_board_and_policy_helper():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "ref: beads" in workflow
    assert "path: authoritative-board" in workflow
    assert "scripts/release_gate_policy.py classify-pr" in workflow
    assert "scripts/release_gate_policy.py check-board" in workflow
    assert "--status open" not in workflow
    assert "gates skipped, passing" not in workflow
