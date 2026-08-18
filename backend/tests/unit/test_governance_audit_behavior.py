"""Behavioural tests for the Security Governance Audit's shell steps.

`test_release_gate_policy.py` pins the audit's *shape* — which credential each
step binds, which branches exist, which strings appear where. That is necessary
and not sufficient: a step can have every branch a text guard demands and still
pass on input that should fail it. The vacuous pass that reached review was
exactly that shape — `jq -e '.enabled == true'` against
``{"enabled": true, "paused": true}``, which GitHub returns for a repository
whose Dependabot security updates it has auto-paused, and which the audit then
reported as ``enabled``.

So this module *runs* each step's `run:` body under `bash` against a stubbed
`gh`, and asserts the exit status and the operator-visible annotation for each
scenario. It is a pytest module rather than a loose script on purpose: the
previous round's scenario evidence existed only in a transcript, was not
reproducible, and was not enforced by CI, while the CHANGELOG asserted the
property as fact. These scenarios run on every push.

The companion mutation harness lives in `scripts/governance_audit_mutants.py`;
see `scripts/README-governance-audit-harness.md`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
AUDIT_WORKFLOW = ROOT / ".github" / "workflows" / "security-governance-audit.yml"

GUARD_STEP = "Verify the elevated audit credential is present"
ELEVATED_STEP = "Verify repository controls that require the elevated credential"
BEADS_STEP = "Verify the authoritative beads branch"
EVIDENCE_STEP = "Record audit evidence"

REPO = "MotWakorb/enhancedchannelmanager"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("jq")),
    reason="the governance-audit scenario harness needs bash and jq",
)


# ─── Fixture payloads ──────────────────────────────────────────────────────
#
# Real response shapes, trimmed to the fields the audit reads. Keeping them
# here rather than inline makes the difference between scenarios obvious.

MAIN_CONTEXTS = [
    "Backend Tests",
    "Frontend Tests",
    "CodeQL Analysis (python)",
    "CodeQL Analysis (javascript-typescript)",
    "Release Cut Gate",
]
DEV_CONTEXTS = [
    "Backend Tests",
    "Frontend Tests",
    "CodeQL Analysis (python)",
    "CodeQL Analysis (javascript-typescript)",
    "Build Docker Image (AMD64)",
    "DAST Security Scan",
    "Container Security Scan (Trivy)",
]


def _protection(contexts: list[str], enforce: bool = True, force: bool = False) -> str:
    return json.dumps(
        {
            "enforce_admins": {"enabled": enforce},
            "allow_force_pushes": {"enabled": force},
            "required_status_checks": {"contexts": contexts},
        }
    )


def _healthy_responses() -> list[dict]:
    """Every elevated read answering the way a compliant repository does."""
    return [
        {"match": "branches/main/protection", "stdout": _protection(MAIN_CONTEXTS)},
        {"match": "branches/dev/protection", "stdout": _protection(DEV_CONTEXTS)},
        {"match": "vulnerability-alerts", "status": 0, "stdout": ""},
        {
            "match": "automated-security-fixes",
            "stdout": json.dumps({"enabled": True, "paused": False}),
        },
        {"match": "secret-scanning/alerts", "stdout": ""},
        {"match": "branches/beads", "stdout": json.dumps({"name": "beads"})},
        {"match": "contents/", "stdout": json.dumps({"path": "x"})},
    ]


def _override(responses: list[dict], match: str, **fields) -> list[dict]:
    """Replace one endpoint's scripted answer, leaving the rest healthy."""
    out = []
    for response in responses:
        if response["match"] == match:
            replacement = {"match": match}
            replacement.update(fields)
            out.append(replacement)
        else:
            out.append(response)
    return out


def _api_error(status: int, message: str) -> dict:
    """The shape `gh api` actually writes to stderr on an HTTP failure."""
    return {"status": 1, "stderr": f"gh: {message} (HTTP {status})\n"}


# ─── Harness ───────────────────────────────────────────────────────────────

_STUB = '''#!/usr/bin/env python3
import json, os, sys

responses = json.load(open(os.environ["GH_STUB_SCENARIO"]))
argv = sys.argv[1:]
endpoint = ""
skip_next = False
for index, arg in enumerate(argv):
    if skip_next:
        skip_next = False
        continue
    if arg == "--jq":
        skip_next = True
        continue
    if arg.startswith("-") or arg in ("api",):
        continue
    endpoint = arg
    break

with open(os.environ["GH_STUB_CALLS"], "a", encoding="utf-8") as log:
    log.write(json.dumps({
        "argv": argv,
        "endpoint": endpoint,
        "GH_TOKEN": os.environ.get("GH_TOKEN", ""),
    }) + "\\n")

for response in responses:
    if response["match"] in endpoint:
        sys.stdout.write(response.get("stdout", ""))
        sys.stderr.write(response.get("stderr", ""))
        sys.exit(response.get("status", 0))

sys.stderr.write("gh stub: no scripted response for %r\\n" % endpoint)
sys.exit(70)
'''


def _step(name: str) -> dict:
    doc = yaml.load(AUDIT_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    for step in doc["jobs"]["audit"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in {AUDIT_WORKFLOW}")


class Result:
    def __init__(self, process, calls, summary):
        self.returncode = process.returncode
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.calls = calls
        self.summary = summary

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def run_step(
    tmp_path: Path,
    name: str,
    responses: list[dict],
    env: dict | None = None,
) -> Result:
    """Execute one audit step's shell body against a stubbed `gh`."""
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "gh"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)

    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps(responses), encoding="utf-8")
    calls = tmp_path / "calls.jsonl"
    calls.write_text("", encoding="utf-8")
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")

    step = _step(name)
    environment = {
        "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "GH_STUB_SCENARIO": str(scenario),
        "GH_STUB_CALLS": str(calls),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_SHA": "0" * 40,
        "REPO": REPO,
    }
    # The step's own `env:` block, with the Actions expressions resolved to the
    # values this scenario wants. An unresolved `${{ }}` would be a literal
    # string in bash and would make `-z` guards look satisfied for the wrong
    # reason.
    for key, value in (step.get("env") or {}).items():
        if "secrets.GOVERNANCE_AUDIT_TOKEN" in value:
            environment[key] = "ghp-elevated-stub"
        elif "github.token" in value:
            environment[key] = "ghs-builtin-stub"
        elif "github.repository" in value:
            environment[key] = REPO
        elif "default_branch" in value:
            environment[key] = "main"
        else:
            environment[key] = value
    environment.update(env or {})

    process = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
    )
    logged = [
        json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()
    ]
    return Result(process, logged, summary.read_text(encoding="utf-8"))


# ─── The credential guard ──────────────────────────────────────────────────


def test_a_provisioned_credential_passes_the_guard(tmp_path):
    result = run_step(tmp_path, GUARD_STEP, [])
    assert result.returncode == 0, result.output


def test_a_declared_but_unprovisioned_credential_fails_closed(tmp_path):
    """An unprovisioned secret expands to "", not to unset, so `set -u` is mute."""
    result = run_step(
        tmp_path, GUARD_STEP, [], env={"GOVERNANCE_AUDIT_TOKEN": ""}
    )
    assert result.returncode == 1, result.output
    assert "::error::GOVERNANCE_AUDIT_TOKEN is absent or empty" in result.output


# ─── Elevated reads: the healthy path ──────────────────────────────────────


def test_a_compliant_repository_passes_every_elevated_control(tmp_path):
    result = run_step(tmp_path, ELEVATED_STEP, _healthy_responses())
    assert result.returncode == 0, result.output
    endpoints = [call["endpoint"] for call in result.calls]
    for expected in (
        f"repos/{REPO}/branches/main/protection",
        f"repos/{REPO}/branches/dev/protection",
        f"repos/{REPO}/vulnerability-alerts",
        f"repos/{REPO}/automated-security-fixes",
    ):
        assert expected in endpoints, endpoints
    assert all(call["GH_TOKEN"] == "ghp-elevated-stub" for call in result.calls)


# ─── Elevated reads: Dependabot ────────────────────────────────────────────


def test_a_paused_dependabot_is_not_reported_as_enabled(tmp_path):
    """The live vacuous pass. `enabled: true, paused: true` opens no PRs."""
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "automated-security-fixes",
            stdout=json.dumps({"enabled": True, "paused": True}),
        ),
    )
    assert result.returncode == 1, result.output
    assert "PAUSED" in result.output
    assert "Resume" in result.output


def test_a_disabled_dependabot_says_re_enable_not_resume(tmp_path):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "automated-security-fixes",
            stdout=json.dumps({"enabled": False, "paused": False}),
        ),
    )
    assert result.returncode == 1, result.output
    assert "switched off" in result.output
    assert "PAUSED" not in result.output


def test_disabled_dependabot_alerts_are_reported_with_a_hedge(tmp_path):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "vulnerability-alerts",
            **_api_error(404, "Not Found"),
        ),
    )
    assert result.returncode == 1, result.output
    assert "read as DISABLED" in result.output
    assert "has lost 'Administration' (read)" in result.output


# ─── Elevated reads: credential failure modes ──────────────────────────────


@pytest.mark.parametrize(
    "endpoint",
    [
        "branches/main/protection",
        "vulnerability-alerts",
        "automated-security-fixes",
        "secret-scanning/alerts",
    ],
)
def test_an_expired_credential_is_named_as_a_credential_defect(tmp_path, endpoint):
    """401 is what an expired or revoked fine-grained PAT returns."""
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(_healthy_responses(), endpoint, **_api_error(401, "Bad credentials")),
    )
    assert result.returncode == 1, result.output
    assert "HTTP 401, bad credentials" in result.output
    assert "expired" in result.output
    assert "not a finding about the control itself" in result.output


@pytest.mark.parametrize(
    ("endpoint", "permission"),
    [
        ("branches/main/protection", "Administration"),
        ("branches/dev/protection", "Administration"),
        ("vulnerability-alerts", "Administration"),
        ("automated-security-fixes", "Administration"),
        ("secret-scanning/alerts", "Secret scanning alerts"),
    ],
)
def test_an_under_scoped_credential_names_the_permission_to_grant(
    tmp_path, endpoint, permission
):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            endpoint,
            **_api_error(403, "Resource not accessible by personal access token"),
        ),
    )
    assert result.returncode == 1, result.output
    assert f"Grant the token '{permission}' (read)" in result.output
    assert "not a finding about the control itself" in result.output


# ─── Elevated reads: branch protection ─────────────────────────────────────


@pytest.mark.parametrize("branch", ["main", "dev"])
def test_weakened_administrator_enforcement_names_the_branch(tmp_path, branch):
    contexts = MAIN_CONTEXTS if branch == "main" else DEV_CONTEXTS
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            f"branches/{branch}/protection",
            stdout=_protection(contexts, enforce=False, force=True),
        ),
    )
    assert result.returncode == 1, result.output
    assert f"Branch protection on '{branch}' is weaker" in result.output


@pytest.mark.parametrize("branch", ["main", "dev"])
def test_a_missing_required_context_names_the_branch(tmp_path, branch):
    contexts = MAIN_CONTEXTS if branch == "main" else DEV_CONTEXTS
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            f"branches/{branch}/protection",
            stdout=_protection(contexts[:-1]),
        ),
    )
    assert result.returncode == 1, result.output
    assert f"Branch '{branch}' is missing at least one required status check" in (
        result.output
    )


def test_a_branch_with_no_protection_at_all_hedges_on_visibility(tmp_path):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "branches/main/protection",
            **_api_error(404, "Branch not protected"),
        ),
    )
    assert result.returncode == 1, result.output
    assert "reports no protection settings at all" in result.output
    assert "has lost 'Administration' (read)" in result.output


# ─── Elevated reads: secret scanning ───────────────────────────────────────


def test_a_single_open_alert_without_a_trailing_newline_still_fails(tmp_path):
    """`wc -l` would count zero here and report a clean board."""
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "secret-scanning/alerts",
            stdout="#1 telegram_bot_token opened 2026-01-01T00:00:00Z",
        ),
    )
    assert result.returncode == 1, result.output
    assert "1 secret-scanning alert(s) remain undispositioned" in result.output


def test_open_alerts_fail_the_audit(tmp_path):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "secret-scanning/alerts",
            stdout="#1 a opened x\n#2 b opened y\n",
        ),
    )
    assert result.returncode == 1, result.output
    assert "2 secret-scanning alert(s) remain undispositioned" in result.output


def test_secret_scanning_switched_off_is_not_a_clean_board(tmp_path):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "secret-scanning/alerts",
            **_api_error(404, "Not Found"),
        ),
    )
    assert result.returncode == 1, result.output
    assert "reads as DISABLED" in result.output
    assert "An unreadable alert list is not an empty alert list" in result.output


def test_an_unexpected_status_still_fails_closed(tmp_path):
    result = run_step(
        tmp_path,
        ELEVATED_STEP,
        _override(
            _healthy_responses(),
            "vulnerability-alerts",
            **_api_error(500, "Internal Server Error"),
        ),
    )
    assert result.returncode == 1, result.output
    assert "failed" in result.output


# ─── The beads branch, on the built-in token ───────────────────────────────


def test_the_beads_branch_read_uses_the_built_in_token(tmp_path):
    result = run_step(tmp_path, BEADS_STEP, _healthy_responses())
    assert result.returncode == 0, result.output
    assert result.calls, "the beads step made no gh call"
    assert all(call["GH_TOKEN"] == "ghs-builtin-stub" for call in result.calls), (
        "the beads-branch read must not be promoted to the elevated credential"
    )


def test_a_missing_beads_branch_fails_the_audit(tmp_path):
    result = run_step(
        tmp_path,
        BEADS_STEP,
        _override(_healthy_responses(), "branches/beads", **_api_error(404, "Not Found")),
    )
    assert result.returncode == 1, result.output
    assert "authoritative 'beads' branch could not be read" in result.output


# ─── The cadence probe ─────────────────────────────────────────────────────


def test_the_cadence_probe_reports_armed_when_the_file_is_on_the_default_branch(
    tmp_path,
):
    result = run_step(tmp_path, EVIDENCE_STEP, _healthy_responses())
    assert result.returncode == 0, result.output
    assert "Scheduled cadence: ARMED" in result.summary
    assert "checked:" in result.summary
    assert "present on 'main'" in result.summary
    assert "?ref=main" in result.calls[0]["endpoint"]


def test_the_cadence_probe_reports_pending_on_a_clean_404(tmp_path):
    result = run_step(
        tmp_path,
        EVIDENCE_STEP,
        _override(_healthy_responses(), "contents/", **_api_error(404, "Not Found")),
    )
    assert result.returncode == 0, result.output
    assert "Scheduled cadence: PENDING" in result.summary
    assert "::warning::" in result.output


def test_a_broken_cadence_probe_fails_rather_than_reporting_pending(tmp_path):
    """A rate-limited run must not claim the schedule is inert forever."""
    result = run_step(
        tmp_path,
        EVIDENCE_STEP,
        _override(
            _healthy_responses(),
            "contents/",
            **_api_error(403, "API rate limit exceeded"),
        ),
    )
    assert result.returncode == 1, result.output
    assert "Could not determine whether the quarterly cadence is armed" in result.output
    assert "PENDING" not in result.summary


def test_the_evidence_summary_says_it_restates_the_earlier_steps(tmp_path):
    result = run_step(tmp_path, EVIDENCE_STEP, _healthy_responses())
    assert result.returncode == 0, result.output
    assert "restate that every earlier step" in result.summary
