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
AUDIT_WORKFLOW = ROOT / ".github" / "workflows" / "security-governance-audit.yml"


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


def test_quarterly_audit_has_owner_visible_live_control_checks():
    workflow = AUDIT_WORKFLOW.read_text(encoding="utf-8")
    assert "1 1,4,7,10" in workflow
    assert "Release Cut Gate" in workflow
    assert "DAST Security Scan" in workflow
    assert "vulnerability-alerts" in workflow
    assert "secret-scanning/alerts?state=open" in workflow
    assert "branches/beads" in workflow


def test_dev_active_tests_workflow_invokes_governance_audit_for_exact_push_sha():
    import yaml

    audit = AUDIT_WORKFLOW.read_text(encoding="utf-8")
    tests = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    audit_doc = yaml.load(audit, Loader=yaml.BaseLoader)
    tests_doc = yaml.load(tests, Loader=yaml.BaseLoader)
    assert set(audit_doc["on"]) == {"workflow_call", "schedule", "workflow_dispatch"}
    call = tests_doc["jobs"]["security-governance-audit"]
    assert call["uses"] == "./.github/workflows/security-governance-audit.yml"
    assert call["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/dev'"
    # `security-events: read` was dropped with bead
    # enhancedchannelmanager-04c0u.11: it granted the built-in token a scope
    # no call in the audit uses, and it does not reach secret scanning anyway.
    assert call["permissions"] == {"contents": "read"}
    assert "with" not in call  # local reusable calls inherit the caller SHA
    for live_control in (
        "branches/main/protection",
        "branches/dev/protection",
        "branches/beads",
        "vulnerability-alerts",
        "automated-security-fixes",
        "secret-scanning/alerts?state=open",
    ):
        assert live_control in audit


# ─── Governance-audit credential policy (bead enhancedchannelmanager-04c0u.11) ──
#
# The audit reads four repository ADMINISTRATION surfaces plus secret-scanning
# alerts. The built-in GITHUB_TOKEN can reach none of them: the workflow
# `permissions:` key has no `administration` scope, and GitHub documents that
# `security-events` covers code scanning only and that secret-scanning alerts
# require a GitHub App or a personal access token. So the audit runs those
# reads on a repository secret, GOVERNANCE_AUDIT_TOKEN, and the shape of that
# wiring is what these tests pin. Every assertion below was proven red by
# mutating the workflow YAML before it was accepted.

GOVERNANCE_SECRET = "GOVERNANCE_AUDIT_TOKEN"
GOVERNANCE_WORKFLOW_REF = "./.github/workflows/security-governance-audit.yml"
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _load(path: Path) -> dict:
    """Parse a workflow with BaseLoader so the `on:` key stays the string 'on'.

    YAML 1.1's implicit typing turns a bare `on:` into the boolean True under
    safe_load, which would make every trigger assertion here look at the wrong
    key and pass vacuously.
    """
    import yaml

    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _workflow_files() -> list[Path]:
    """Both spellings. A `.yaml` caller must not be invisible to these guards."""
    return sorted(list(WORKFLOW_DIR.glob("*.yml")) + list(WORKFLOW_DIR.glob("*.yaml")))


def _governance_callers() -> list[tuple[str, str, dict]]:
    """Every job in the repository that invokes the governance audit."""
    callers = []
    for path in _workflow_files():
        for job_id, job in (_load(path).get("jobs") or {}).items():
            job = job or {}
            if job.get("uses") == GOVERNANCE_WORKFLOW_REF:
                callers.append((path.name, job_id, job))
    return callers


def _audit_steps() -> list[dict]:
    return _load(AUDIT_WORKFLOW)["jobs"]["audit"]["steps"]


def test_governance_audit_declares_the_elevated_secret_as_a_call_input():
    """Without this declaration the caller's `secrets:` mapping is rejected."""
    declared = _load(AUDIT_WORKFLOW)["on"]["workflow_call"]["secrets"]
    assert GOVERNANCE_SECRET in declared, (
        f"{AUDIT_WORKFLOW.name} must declare {GOVERNANCE_SECRET} under "
        f"on.workflow_call.secrets. A reusable workflow cannot receive a secret "
        f"it has not declared, so the elevated reads would run unauthenticated."
    )
    assert declared[GOVERNANCE_SECRET]["required"] == "true"
    assert declared[GOVERNANCE_SECRET].get("description")


def test_governance_caller_passes_the_secret_by_explicit_mapping():
    callers = _governance_callers()
    assert callers, "No job invokes the governance audit; the audit never runs."
    for filename, job_id, job in callers:
        secrets = job.get("secrets")
        assert isinstance(secrets, dict), (
            f"{filename}:{job_id} calls the governance audit without an explicit "
            f"secrets: mapping ({secrets!r}). The audit's administration and "
            f"secret-scanning reads cannot run on the built-in GITHUB_TOKEN."
        )
        assert secrets == {
            GOVERNANCE_SECRET: "${{ secrets.%s }}" % GOVERNANCE_SECRET
        }, (
            f"{filename}:{job_id} must hand the governance audit exactly one "
            f"named secret, {GOVERNANCE_SECRET}, and nothing else. Got {secrets!r}."
        )


def test_governance_caller_never_inherits_secrets_broadly():
    """`secrets: inherit` would hand over every repository secret.

    Asserted on the governance callers specifically rather than repo-wide, so a
    future legitimate `inherit` elsewhere is not collateral. Measured at the
    time of writing: no workflow in this repository uses `secrets: inherit`.
    """
    for filename, job_id, job in _governance_callers():
        assert job.get("secrets") != "inherit", (
            f"{filename}:{job_id} uses `secrets: inherit` to call the governance "
            f"audit. The audit must receive exactly {GOVERNANCE_SECRET}, so that "
            f"adding an unrelated repository secret later cannot silently widen "
            f"what this call hands over."
        )


def test_governance_audit_grants_no_write_permissions():
    audit = _load(AUDIT_WORKFLOW)
    blocks = [("workflow", audit.get("permissions") or {})]
    for job_id, job in (audit.get("jobs") or {}).items():
        blocks.append((job_id, (job or {}).get("permissions") or {}))
    for scope_name, block in blocks:
        if not isinstance(block, dict):
            # `permissions: read-all` is acceptable; `write-all` is not.
            assert "write" not in str(block), f"{scope_name}: {block!r}"
            continue
        for key, value in block.items():
            assert value == "read", (
                f"{AUDIT_WORKFLOW.name} grants '{key}: {value}' at {scope_name} "
                f"scope. This audit only ever reads; a write scope here is a "
                f"standing privilege with no call that uses it."
            )
    # The only built-in-token calls left are the beads-branch ref read and the
    # default-branch cadence probe, both of which need `contents: read` alone.
    assert audit["permissions"] == {"contents": "read"}


def test_elevated_reads_never_fall_back_to_the_built_in_token():
    """A fallback would turn a missing PAT back into the original 403.

    Worse, a fallback that reached secret-scanning would let an unreadable
    alert list read as an empty one. So the elevated steps bind GH_TOKEN to the
    secret with no `||` default, and their shell bodies never name github.token.
    """
    elevated = [
        step
        for step in _audit_steps()
        if (step.get("env") or {}).get("GH_TOKEN", "").find(GOVERNANCE_SECRET) >= 0
    ]
    assert elevated, "No step reads with the elevated credential at all."
    for step in elevated:
        token = step["env"]["GH_TOKEN"]
        assert token == "${{ secrets.%s }}" % GOVERNANCE_SECRET, (
            f"Step {step.get('name')!r} binds GH_TOKEN to {token!r}. Any "
            f"expression beyond the bare secret is a fallback path."
        )
        assert "github.token" not in step.get("run", ""), (
            f"Step {step.get('name')!r} names github.token inside an "
            f"elevated-read body."
        )
    # The converse half: the checks the built-in token CAN serve must stay on
    # it, so the elevated credential is used only where GitHub requires it.
    # Matched on the actual `gh api` invocation, not on the endpoint appearing
    # anywhere in the body: the step's own error message names the same
    # endpoint, so a substring search over the whole body stays satisfied even
    # after the real call is changed or the step is promoted to the PAT.
    import re

    invocation = re.compile(r'^\s*gh api "repos/\$REPO/branches/beads"', re.MULTILINE)
    callers = [
        step for step in _audit_steps() if invocation.search(step.get("run", ""))
    ]
    assert len(callers) == 1, (
        "Exactly one step should read the beads branch ref; found "
        f"{len(callers)}."
    )
    assert (callers[0].get("env") or {}).get("GH_TOKEN") == "${{ github.token }}", (
        "Reading a branch ref needs only `contents: read`. The beads-branch "
        "check must stay on the built-in token rather than being promoted to "
        "the elevated credential, which is used only where GitHub's permission "
        "model leaves no alternative."
    )


def test_missing_or_empty_elevated_secret_fails_closed_before_any_read():
    """An unprovisioned secret expands to the empty string, not to unset.

    `set -u` therefore never fires on it, and every gh call would run
    unauthenticated. Only an explicit emptiness test stands between that and an
    audit that reports a clean board it never actually read.
    """
    steps = _audit_steps()
    guards = [
        index
        for index, step in enumerate(steps)
        if (step.get("env") or {}).get(GOVERNANCE_SECRET)
        and "-z" in step.get("run", "")
        and "exit 1" in step.get("run", "")
    ]
    assert guards, (
        f"No step tests {GOVERNANCE_SECRET} for emptiness and exits non-zero. "
        f"Without it a masked or absent secret reads as 'nothing to report'."
    )
    guard_index = guards[0]
    guard_body = steps[guard_index]["run"]
    assert GOVERNANCE_SECRET in guard_body and "::error::" in guard_body, (
        "The fail-closed guard must name the secret in an ::error:: annotation "
        "so the message says what to provision."
    )
    first_read = min(
        index
        for index, step in enumerate(steps)
        if GOVERNANCE_SECRET in (step.get("env") or {}).get("GH_TOKEN", "")
    )
    assert guard_index < first_read, (
        "The fail-closed guard must run before the first elevated read, or the "
        "first failure the operator sees is a 401 rather than the actionable "
        "'the secret is missing' message."
    )


def test_governance_audit_swallows_no_exit_codes():
    """`|| true` / `|| echo '[]'` are how a broken read becomes a clean board."""
    import re

    swallow = re.compile(r"\|\|\s*(true|:|echo)\b")

    def strip_comment(line: str) -> str:
        """A `|| true` quoted inside a comment is prose, not command syntax."""
        stripped = line.lstrip()
        if stripped.startswith("#"):
            return ""
        return re.split(r"(?<=\s)#", line, maxsplit=1)[0]

    for step in _audit_steps():
        body = step.get("run", "")
        offenders = [
            line for line in body.splitlines() if swallow.search(strip_comment(line))
        ]
        assert not offenders, (
            f"Step {step.get('name')!r} swallows a failure: {offenders!r}. "
            f"Capture the status and re-raise it instead."
        )


def test_under_scoped_credential_is_reported_separately_from_a_violation():
    """Three outcomes, three owners; conflating them misroutes the fix."""
    audit = AUDIT_WORKFLOW.read_text(encoding="utf-8")
    assert "HTTP 403" in audit and "HTTP 404" in audit, (
        "The audit must branch on the HTTP status so a provisioning defect "
        "(403) is not reported as a control violation."
    )
    for endpoint, permission in (
        ("branches/main/protection", "Administration"),
        ("branches/dev/protection", "Administration"),
        ("vulnerability-alerts", "Administration"),
        ("automated-security-fixes", "Administration"),
        ("secret-scanning/alerts", "Secret scanning alerts"),
    ):
        assert endpoint in audit, endpoint
        assert permission in audit, permission


def test_audit_reports_whether_the_quarterly_cadence_is_actually_armed():
    """The schedule cannot fire until this file reaches the default branch.

    That is a fact about the default branch, so the audit reads it instead of
    assuming it. Reported, not failed: PENDING is the expected state until the
    next release cut.
    """
    evidence = [
        step
        for step in _audit_steps()
        if "GITHUB_STEP_SUMMARY" in step.get("run", "")
    ]
    assert evidence, "The audit records no evidence summary."
    body = "\n".join(step["run"] for step in evidence)
    # Matched on the assignments that feed the summary line, not on the words
    # appearing somewhere in the body: an annotation mentioning "PENDING" in
    # prose would otherwise satisfy this while the reported value was gone.
    for branch_value in ('cadence="ARMED', 'cadence="PENDING'):
        assert branch_value in body, (
            f"The cadence probe must assign {branch_value}...\" so both outcomes "
            f"reach the evidence summary."
        )
    assert "Scheduled cadence: $cadence" in body, (
        "The probed value must be written into the job summary, or the audit "
        "reports a cadence state nobody can read."
    )
    # Again matched on the invocation, not the body: the step's own error
    # message quotes the same URL, so a substring search would survive the
    # probe losing its ref pin and silently reading the current branch.
    import re

    assert re.search(
        r'^\s*gh api "repos/\$REPO/contents/\$AUDIT_WORKFLOW_PATH\?ref=\$branch"',
        body,
        re.MULTILINE,
    ), (
        "The cadence probe must pin the default-branch ref. Without it the "
        "probe reads the branch it is running on, where the file always "
        "exists, and reports ARMED forever."
    )
    assert "default_branch" in "\n".join(
        str(step.get("env") or {}) for step in evidence
    ), "The cadence probe must read the repository's real default branch."
    doc = (ROOT / "docs/security/release-security-governance.md").read_text(
        encoding="utf-8"
    )
    assert "default branch" in doc and "Owner:" in doc, (
        "docs/security/release-security-governance.md must name both the "
        "cadence owner and the condition under which the schedule arms."
    )
