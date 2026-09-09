"""Regression tests for the main-bound release policy gate."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(
    os.environ.get(
        "RELEASE_GATE_POLICY_SCRIPT", ROOT / "scripts" / "release_gate_policy.py"
    )
)
WORKFLOW = ROOT / ".github" / "workflows" / "release-cut-gate.yml"

FAKE_GH = """#!/bin/sh
printf '%s\n' "$@" > "$GH_TEST_ARGS"
printf '%s' "$GH_TEST_OUTPUT"
exit "$GH_TEST_EXIT_CODE"
"""

FAKE_STAGE_JQ = """#!/bin/sh
if [ "$1" = "$GH_TEST_FAIL_STAGE" ]; then
    exit 23
fi
exec "$GH_TEST_REAL_JQ" "$@"
"""


@pytest.fixture(scope="module")
def policy():
    spec = importlib.util.spec_from_file_location("release_gate_policy", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _g1b_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release-cut-gate"]["steps"]
    return next(step["run"] for step in steps if step.get("name", "").startswith("G1b"))


def _run_g1b(
    tmp_path: Path,
    pages: list[object],
    *,
    raw_output: str | None = None,
    gh_exit_code: int = 0,
    jq_failure: str | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    output = raw_output
    if output is None:
        output = "".join(f"{json.dumps(page)}\n" for page in pages)
    env = os.environ | {
        "GH_TEST_OUTPUT": output,
        "GH_TEST_EXIT_CODE": str(gh_exit_code),
        "GH_TEST_ARGS": str(tmp_path / "gh-args"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    if jq_failure:
        real_jq = shutil.which("jq")
        assert real_jq
        jq = bin_dir / "jq"
        jq.write_text(FAKE_STAGE_JQ, encoding="utf-8")
        jq.chmod(0o755)
        env["GH_TEST_REAL_JQ"] = real_jq
        env["GH_TEST_FAIL_STAGE"] = jq_failure
    result = subprocess.run(
        ["bash", "-c", _g1b_script()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert (tmp_path / "gh-args").read_text().splitlines() == [
        "api",
        "--paginate",
        "repos/${{ github.repository }}/code-scanning/alerts?state=open&per_page=100",
    ]
    return result


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


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_g1b_blocks_high_alert_on_later_api_page(tmp_path, severity):
    result = _run_g1b(
        tmp_path,
        [
            [],
            [
                {
                    "number": 42,
                    "rule": {
                        "security_severity_level": severity,
                        "id": "py/example-gating-alert",
                    },
                    "html_url": "https://example.invalid/alerts/42",
                }
            ],
        ],
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "G1b FAIL: 1 open HIGH/CRITICAL CodeQL alert" in result.stdout
    assert "G1b PASS" not in result.stdout


def test_g1b_passes_with_no_qualifying_alerts_across_pages(tmp_path):
    result = _run_g1b(
        tmp_path,
        [
            [
                {
                    "number": 1,
                    "rule": {
                        "security_severity_level": "medium",
                        "id": "py/example-medium",
                    },
                    "html_url": "https://example.invalid/alerts/1",
                }
            ],
            [],
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "G1b PASS: 0 open HIGH/CRITICAL CodeQL alerts" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    "raw_output",
    [
        "not-json\n",
        '{"message":"unexpected API response"}\n',
        "",
    ],
)
def test_g1b_fails_closed_on_malformed_or_non_array_api_output(tmp_path, raw_output):
    result = _run_g1b(tmp_path, [], raw_output=raw_output)

    assert result.returncode != 0
    assert "G1b PASS" not in result.stdout


def test_g1b_fails_closed_when_api_request_fails(tmp_path):
    result = _run_g1b(tmp_path, [[]], gh_exit_code=22)

    assert result.returncode != 0
    assert "G1b PASS" not in result.stdout


@pytest.mark.parametrize("stage", ["-se", "-e", "-er"])
def test_g1b_fails_closed_when_jq_stage_fails(tmp_path, stage):
    result = _run_g1b(tmp_path, [[]], jq_failure=stage)

    assert result.returncode == 23
    assert "G1b PASS" not in result.stdout


def _alert(severity=None):
    return {
        "number": 42,
        "rule": {"id": "py/example", "security_severity_level": severity},
        "html_url": "https://example.invalid/alerts/42",
    }


@pytest.mark.parametrize("severity", [None, "low", "medium"])
@pytest.mark.parametrize("multipage", [False, True])
def test_g1b_accepts_explicit_non_gating_severities(tmp_path, severity, multipage):
    pages = [[_alert(severity)]] + ([[]] if multipage else [])
    result = _run_g1b(tmp_path, pages)
    assert result.returncode == 0, result.stderr
    assert "G1b PASS" in result.stdout


@pytest.mark.parametrize(
    "entry",
    [
        None, False, 1, "alert", [], {}, {"rule": {}},
        _alert() | {"rule": None},
        _alert() | {"rule": []},
        _alert() | {"rule": {"id": "py/example"}},
        _alert() | {"rule": {"security_severity_level": None}},
        _alert() | {"rule": {"id": False, "security_severity_level": None}},
        _alert() | {"rule": {"id": "", "security_severity_level": None}},
        *[_alert(value) for value in ["", "unknown", "HIGH", 0, False, [], {}]],
        *[_alert() | {"number": value} for value in [None, True, "42", 0, -1, 1.5]],
        *[_alert() | {"html_url": value} for value in [None, 42, ""]],
        {key: value for key, value in _alert().items() if key != "number"},
        {key: value for key, value in _alert().items() if key != "html_url"},
    ],
)
def test_g1b_rejects_malformed_individual_alerts(tmp_path, entry):
    result = _run_g1b(tmp_path, [[_alert("low")], [entry]])
    assert result.returncode != 0, result.stdout
    assert "G1b PASS" not in result.stdout


@pytest.mark.parametrize("severity", ["high", "critical"])
@pytest.mark.parametrize("page", [0, 1, 2])
def test_g1b_blocks_gating_severity_on_any_page(tmp_path, severity, page):
    pages = [[_alert("low")] for _ in range(3)]
    pages[page] = [_alert(severity)]
    result = _run_g1b(tmp_path, pages)
    assert result.returncode == 1
    assert "G1b FAIL: 1 open" in result.stdout
    assert "G1b PASS" not in result.stdout


def test_g1b_metacharacters_are_inert_alert_data(tmp_path):
    marker = tmp_path / "must-not-exist"
    payload = f"$(touch {marker}); `touch {marker}` & | ' \\\""
    entry = _alert("high")
    entry["rule"]["id"] = payload
    entry["html_url"] += payload
    result = _run_g1b(tmp_path, [[entry]])
    assert result.returncode == 1
    assert payload in result.stdout
    assert not marker.exists()


@pytest.mark.parametrize("severity", [None, "low", "medium", "high", "critical"])
def test_g1b_accepts_explicit_null_rule_id_without_bypassing_severity(tmp_path, severity):
    entry = _alert(severity)
    entry["rule"]["id"] = None
    result = _run_g1b(tmp_path, [[entry]])
    gating = severity in {"high", "critical"}
    assert result.returncode == (1 if gating else 0), result.stderr
    assert ("G1b PASS" in result.stdout) is not gating
