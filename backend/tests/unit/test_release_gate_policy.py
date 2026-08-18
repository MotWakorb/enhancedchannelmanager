"""Regression tests for the main-bound release policy gate."""

from __future__ import annotations

import importlib.util
import json
import os
import re
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
# wiring is what these tests pin.
#
# Every assertion below matches LINE-ANCHORED against the audit's step `run:`
# bodies WITH COMMENT LINES STRIPPED, never against the raw file text. The
# workflow's header comment block quotes its own endpoints, permissions and
# HTTP statuses, so a whole-file substring search stays green after the code
# that produced them is deleted. That is not hypothetical: replacing the whole
# elevated step body with the naive pre-PR version left the earlier version of
# these tests passing.
#
# `GOVERNANCE_CREDENTIAL_NAME` is deliberately not spelled `GOVERNANCE_SECRET`.
# detect-secrets' KeywordDetector fires on the IDENTIFIER, so that name made
# this file a finding in the repository's secret ratchet even though its value
# is a variable name and not a credential.

GOVERNANCE_CREDENTIAL_NAME = "GOVERNANCE_AUDIT_TOKEN"
GOVERNANCE_WORKFLOW_PATH = ".github/workflows/security-governance-audit.yml"
GOVERNANCE_WORKFLOW_REF = "./" + GOVERNANCE_WORKFLOW_PATH
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _load(path: Path) -> dict:
    """Parse a workflow with BaseLoader so the `on:` key stays the string 'on'.

    YAML 1.1's implicit typing turns a bare `on:` into the boolean True under
    safe_load, which would make every trigger assertion here look at the wrong
    key and pass vacuously.
    """
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _workflow_files() -> list[Path]:
    """Both spellings. A `.yaml` caller must not be invisible to these guards."""
    return sorted(list(WORKFLOW_DIR.glob("*.yml")) + list(WORKFLOW_DIR.glob("*.yaml")))


def _invokes_governance_audit(uses: object) -> bool:
    """Match any spelling of a call to the governance audit.

    A caller may reference the reusable workflow locally (`./.github/...`) or
    by its full `owner/repo/.github/...@ref` form. Exact equality on the local
    spelling made every remote-style caller invisible to all three caller
    guards below, including the one forbidding `secrets: inherit`.
    """
    if not isinstance(uses, str):
        return False
    return uses.split("@", 1)[0].endswith(GOVERNANCE_WORKFLOW_PATH)


def _governance_callers() -> list[tuple[str, str, dict]]:
    """Every job in the repository that invokes the governance audit."""
    callers = []
    for path in _workflow_files():
        for job_id, job in (_load(path).get("jobs") or {}).items():
            job = job or {}
            if _invokes_governance_audit(job.get("uses")):
                callers.append((path.name, job_id, job))
    return callers


def _audit_steps() -> list[dict]:
    return _load(AUDIT_WORKFLOW)["jobs"]["audit"]["steps"]


def _code_lines(body: str) -> list[str]:
    """A shell body with comment-only lines and trailing comments removed."""
    lines = []
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.split(r"(?<=\s)#", line, maxsplit=1)[0])
    return lines


def _code(body: str) -> str:
    return "\n".join(_code_lines(body))


def _audit_code() -> str:
    """Executable shell of every audit step, comments stripped."""
    return "\n".join(_code(step.get("run", "")) for step in _audit_steps())


def _elevated_steps() -> list[dict]:
    return [
        step
        for step in _audit_steps()
        if GOVERNANCE_CREDENTIAL_NAME in (step.get("env") or {}).get("GH_TOKEN", "")
    ]


# ─── Swallowed-exit-status detection ───────────────────────────────────────
#
# The invariant is not "no `|| true`" — that was the demonstrated example, and
# scoping the guard to it left `|| :`, `|| status=0`, `|| /bin/true`,
# `|| printf ''` and `set +e` all alive, each of which turns a failed read into
# a clean board. The invariant is: the ONLY `||` continuation this audit may
# use is the exact status capture, and strict mode may never be relaxed.

STATUS_CAPTURE = "status=$?"
_OR_CONTINUATION = re.compile(r"\|\|\s*(?P<rhs>.*)$")
_SET_RELAX = re.compile(r"(?:^|[\s;(])set\s+\+")


def _swallow_offenders(body: str) -> list[str]:
    offenders = []
    for line in _code_lines(body):
        if _SET_RELAX.search(line):
            offenders.append(line)
            continue
        match = _OR_CONTINUATION.search(line)
        if match and match.group("rhs").strip() != STATUS_CAPTURE:
            offenders.append(line)
    return offenders


def test_governance_audit_declares_the_elevated_secret_as_a_call_input():
    """Without this declaration the caller's mapping is rejected."""
    declared = _load(AUDIT_WORKFLOW)["on"]["workflow_call"]["secrets"]
    assert GOVERNANCE_CREDENTIAL_NAME in declared, (
        f"{AUDIT_WORKFLOW.name} must declare {GOVERNANCE_CREDENTIAL_NAME} under "
        f"on.workflow_call.secrets. A reusable workflow cannot receive a "
        f"credential it has not declared, so the elevated reads would run "
        f"unauthenticated."
    )
    assert declared[GOVERNANCE_CREDENTIAL_NAME]["required"] == "true"
    assert declared[GOVERNANCE_CREDENTIAL_NAME].get("description")


def test_governance_caller_passes_the_secret_by_explicit_mapping():
    callers = _governance_callers()
    assert callers, "No job invokes the governance audit; the audit never runs."
    for filename, job_id, job in callers:
        secrets = job.get("secrets")
        assert isinstance(secrets, dict), (
            f"{filename}:{job_id} calls the governance audit without an explicit "
            f"one-entry mapping ({secrets!r}). The audit's administration and "
            f"secret-scanning reads cannot run on the built-in GITHUB_TOKEN."
        )
        assert secrets == {
            GOVERNANCE_CREDENTIAL_NAME: "${{ secrets.%s }}" % GOVERNANCE_CREDENTIAL_NAME
        }, (
            f"{filename}:{job_id} must hand the governance audit exactly one "
            f"named credential, {GOVERNANCE_CREDENTIAL_NAME}, and nothing else. "
            f"Got {secrets!r}."
        )


def test_governance_caller_never_inherits_secrets_broadly():
    """`secrets: inherit` would hand over every repository secret.

    Asserted on the governance callers specifically rather than repo-wide, so a
    future legitimate `inherit` elsewhere is not collateral. Measured at the
    time of writing: no workflow in this repository inherits broadly.
    """
    for filename, job_id, job in _governance_callers():
        assert job.get("secrets") != "inherit", (
            f"{filename}:{job_id} inherits every repository secret to call the "
            f"governance audit. The audit must receive exactly "
            f"{GOVERNANCE_CREDENTIAL_NAME}, so that adding an unrelated "
            f"repository secret later cannot silently widen what this call "
            f"hands over."
        )


@pytest.mark.parametrize(
    "uses",
    [
        "./.github/workflows/security-governance-audit.yml",
        "MotWakorb/enhancedchannelmanager/.github/workflows/"
        "security-governance-audit.yml@dev",
        "MotWakorb/enhancedchannelmanager/.github/workflows/"
        "security-governance-audit.yml@v1",
    ],
)
def test_every_spelling_of_a_governance_caller_is_visible_to_the_guards(uses):
    """A remote-style caller must not be invisible to the caller guards."""
    assert _invokes_governance_audit(uses)


@pytest.mark.parametrize(
    "uses",
    [
        None,
        "./.github/workflows/test.yml",
        "./.github/workflows/other-security-governance-audit.yml.bak",
    ],
)
def test_unrelated_uses_values_are_not_mistaken_for_governance_callers(uses):
    assert not _invokes_governance_audit(uses)


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
    elevated = _elevated_steps()
    assert elevated, "No step reads with the elevated credential at all."
    for step in elevated:
        token = step["env"]["GH_TOKEN"]
        assert token == "${{ secrets.%s }}" % GOVERNANCE_CREDENTIAL_NAME, (
            f"Step {step.get('name')!r} binds GH_TOKEN to {token!r}. Any "
            f"expression beyond the bare credential is a fallback path."
        )
        assert "github.token" not in _code(step.get("run", "")), (
            f"Step {step.get('name')!r} names github.token inside an "
            f"elevated-read body."
        )
    # The converse half: the checks the built-in token CAN serve must stay on
    # it, so the elevated credential is used only where GitHub requires it.
    # Matched on the actual `gh api` invocation, not on the endpoint appearing
    # anywhere in the body: the step's own error message names the same
    # endpoint, so a substring search over the whole body stays satisfied even
    # after the real call is changed or the step is promoted to the PAT.
    invocation = re.compile(r'^\s*gh api "repos/\$REPO/branches/beads"', re.MULTILINE)
    callers = [
        step for step in _audit_steps() if invocation.search(_code(step.get("run", "")))
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


def test_every_audit_step_runs_under_strict_bash_mode():
    """`set -euo pipefail` is load-bearing, so pin it rather than assume it.

    Four branch-protection assertions used to be bare `jq -e` calls that failed
    the job only because `set -e` was in scope, and nothing in this suite
    mentioned `set -e` at all. They are explicit `if ! ...; exit 1` blocks now,
    but strict mode still guards every unguarded command in every step.
    """
    for step in _audit_steps():
        body = step.get("run") or ""
        code = [line for line in _code_lines(body) if line.strip()]
        assert code, f"Step {step.get('name')!r} has an empty run body."
        assert code[0].strip() == "set -euo pipefail", (
            f"Step {step.get('name')!r} does not begin with "
            f"`set -euo pipefail`; its first executable line is "
            f"{code[0].strip()!r}. Without strict mode an unguarded failing "
            f"command leaves the step green."
        )


def test_every_control_assertion_re_raises_its_own_status():
    """No bare `jq -e`: a violation must name itself, not print a bare `false`.

    A bare `jq -e` fails the job only through `set -e`, and prints an
    unannotated `false` that says nothing about which control is weak on which
    branch. Every one must sit inside an `if ! ...; then echo ::error::;
    exit 1; fi`.
    """
    offenders = [
        line
        for line in _code_lines(_audit_code())
        if "jq -e" in line and not re.match(r"^\s*if ! jq -e\b", line)
    ]
    assert not offenders, (
        f"These `jq -e` assertions do not re-raise their own status and would "
        f"print an unannotated `false`: {offenders!r}"
    )


def test_every_captured_status_is_tested_in_the_pinned_form():
    """`-ne 0` / `-eq 0` are the only accepted status tests.

    `-lt 0` and `-ge 0` are both always-false / always-true against a shell
    exit status, so either mutation silently disarms an error branch. Counting
    captures against tests pins the comparison operator itself.
    """
    code = _audit_code()
    captures = len(re.findall(r"\|\| status=\$\?", code))
    tests = len(re.findall(r'\[\[ "\$status" -(?:ne|eq) 0 \]\]', code))
    assert captures and captures == tests, (
        f"{captures} `|| status=$?` captures but {tests} `-ne 0`/`-eq 0` "
        f"tests. Every captured status must be tested in the pinned form."
    )


@pytest.mark.parametrize(
    "body",
    [
        "set -euo pipefail\ngh api x || true\n",
        "set -euo pipefail\ngh api x || :\n",
        "set -euo pipefail\ngh api x || status=0\n",
        "set -euo pipefail\ngh api x || /bin/true\n",
        "set -euo pipefail\ngh api x || printf ''\n",
        "set -euo pipefail\ngh api x || echo '[]'\n",
        "set -euo pipefail\ngh api x || exit 0\n",
        "set -euo pipefail\nset +e\ngh api x\n",
        "set -euo pipefail\nset +o pipefail\ngh api x\n",
        "set -euo pipefail\ngh api x; set +e\n",
    ],
)
def test_the_swallow_guard_is_red_against_every_known_spelling(body):
    """Fixtures for the spellings that survived the previous `|| true` regex."""
    assert _swallow_offenders(body), (
        f"The swallowed-status guard does not flag {body!r}. Each of these "
        f"turns a failed read into a clean board."
    )


@pytest.mark.parametrize(
    "body",
    [
        "set -euo pipefail\nstatus=0\ngh api x || status=$?\n",
        "set -euo pipefail\n# a comment mentioning || true is prose\ngh api x\n",
        "set -euo pipefail\ngh api x  # trailing || true is prose too\n",
    ],
)
def test_the_swallow_guard_accepts_the_capture_form_and_prose(body):
    assert not _swallow_offenders(body)


def test_governance_audit_swallows_no_exit_codes():
    """The audit itself must be clean under the guard proven red above."""
    for step in _audit_steps():
        offenders = _swallow_offenders(step.get("run", ""))
        assert not offenders, (
            f"Step {step.get('name')!r} swallows a failure: {offenders!r}. "
            f"Capture the status and re-raise it instead."
        )


def test_under_scoped_credential_is_reported_separately_from_a_violation():
    """Four outcomes, four owners; conflating them misroutes the fix.

    Asserted on the CALL SITES in the executable shell, not on the strings
    appearing somewhere in the file. Every endpoint and permission named below
    also appears in the workflow's header comment block, so a whole-file
    substring search is satisfied by comment prose alone.
    """
    code = _audit_code()
    for call in (
        'read_protection main "repos/$REPO/branches/main/protection"',
        'read_protection dev "repos/$REPO/branches/dev/protection"',
        'scope_error "$endpoint" "Administration"',
        'scope_error "repos/$REPO/vulnerability-alerts" "Administration"',
        'scope_error "repos/$REPO/automated-security-fixes" "Administration"',
        'scope_error "repos/$REPO/secret-scanning/alerts" "Secret scanning alerts"',
        'credential_error "$endpoint"',
        'credential_error "repos/$REPO/vulnerability-alerts"',
        'credential_error "repos/$REPO/automated-security-fixes"',
        'credential_error "repos/$REPO/secret-scanning/alerts"',
    ):
        assert re.search(rf"^\s*{re.escape(call)}\s*$", code, re.MULTILINE), (
            f"The audit's executable shell has no call site {call!r}. A "
            f"provisioning defect on this endpoint would be reported as a "
            f"control violation, or not distinguished at all."
        )


def test_every_elevated_read_branches_on_401_403_and_404():
    """An expired PAT (401) is a fourth outcome and must not read as a finding.

    403 and 404 alone leave `401 Bad credentials` — the single most likely
    future failure of this control, since a fine-grained PAT expires — falling
    through to a generic message that names the endpoint rather than the
    credential, so it reads as a control violation.
    """
    for step in _elevated_steps():
        code = _code(step.get("run", ""))
        calls = re.findall(r"^\s*gh api\b", code, re.MULTILINE)
        assert calls, f"Elevated step {step.get('name')!r} makes no gh api call."
        for status in ("HTTP 401", "HTTP 403", "HTTP 404"):
            branches = re.findall(
                rf"^\s*(?:if|elif) grep -q '{status}' gh-error\.txt; then$",
                code,
                re.MULTILINE,
            )
            assert len(branches) == len(calls), (
                f"Elevated step {step.get('name')!r} makes {len(calls)} gh api "
                f"call(s) but branches on {status} {len(branches)} time(s). "
                f"Every elevated read must distinguish an invalid credential "
                f"(401), an under-scoped one (403), and the control being off "
                f"(404)."
            )


def test_the_expiry_hint_sits_on_the_branch_expiry_actually_reaches():
    """An expired token returns 401, never 403, so the hint belongs on 401."""
    code = _audit_code()
    credential_message = next(
        line for line in _code_lines(code) if "HTTP 401, bad credentials" in line
    )
    assert "expired" in credential_message
    scope_message = next(
        line for line in _code_lines(code) if "was refused (HTTP 403)" in line
    )
    assert "expired" not in scope_message, (
        "The 'reissue it if it has expired' hint is on the 403 branch, which "
        "an expired credential never reaches."
    )


def test_automated_security_fixes_must_be_enabled_and_not_paused():
    """`{"enabled": true, "paused": true}` opens no security-update PRs.

    GitHub pauses automated security fixes by itself on repositories whose
    update pull requests go unactioned, so asserting only on `enabled` is a
    live vacuous pass: the evidence summary reports the control as on while
    nothing is being opened.
    """
    code = _audit_code()
    assert re.search(
        r"^\s*if ! jq -e '\.enabled == true' automated-security-fixes\.json",
        code,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*if ! jq -e '\.paused == false' automated-security-fixes\.json",
        code,
        re.MULTILINE,
    ), (
        "The audit does not assert `.paused == false`. A paused service "
        "reports `enabled: true` and opens no security-update pull requests."
    )
    paused_branch = code.split("'.paused == false'", 1)[1]
    message = next(line for line in paused_branch.splitlines() if "::error::" in line)
    assert "PAUSED" in message and "Resume" in message, (
        "Resuming a paused service and re-enabling a disabled one are "
        "different operator actions and need distinct messages."
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "repos/$REPO/vulnerability-alerts",
        "repos/$REPO/automated-security-fixes",
        "repos/$REPO/secret-scanning/alerts",
    ],
)
def test_every_404_message_hedges_between_disabled_and_unreadable(endpoint):
    """GitHub answers these 404 both when the control is off and when the
    credential cannot see it, so a flat "DISABLED" misroutes the fix."""
    code = _audit_code()
    tail = code.split(f'credential_error "{endpoint}"', 1)[1]
    message = next(
        line
        for line in tail.splitlines()
        if "::error::" in line and "HTTP 404" in line
    )
    assert "GOVERNANCE_AUDIT_TOKEN has lost" in message, (
        f"The 404 message for {endpoint} flatly asserts the control is off. "
        f"It must name the credential-visibility possibility too, as the "
        f"branch-protection 404 message already does."
    )


def test_missing_or_empty_elevated_secret_fails_closed_before_any_read():
    """An unprovisioned secret expands to the empty string, not to unset.

    `set -u` therefore never fires on it, and every gh call would run
    unauthenticated. Only an explicit emptiness test stands between that and an
    audit that reports a clean board it never actually read.

    Asserted on the CONDITION, not on the error message: the guard's own
    `::error::` annotation contains the credential name, `-z` and `exit 1`, so
    the previous message-shaped assertions were satisfied by the message alone.
    Changing `${GOVERNANCE_AUDIT_TOKEN:-}` to `${GOVERNANCE_AUDIT_TOKEN:-x}` —
    one character, making the guard permanently false while the whole audit
    then runs unauthenticated — survived the entire suite.
    """
    name = GOVERNANCE_CREDENTIAL_NAME
    condition = re.compile(
        r"^\s*if\s+\[\[\s+-z\s+\"\$(?:\{" + name + r"(?::-)?\}|" + name + r")\"\s+\]\];"
        r"\s*then\s*$"
    )
    steps = _audit_steps()
    guards = [
        index
        for index, step in enumerate(steps)
        if (step.get("env") or {}).get(name)
        and any(condition.match(line) for line in _code_lines(step.get("run", "")))
    ]
    assert guards, (
        f"No step tests {name} for emptiness with an unmodified default "
        f"expansion. Without it a masked or absent credential reads as "
        f"'nothing to report'."
    )
    guard_index = guards[0]
    guard_code = _code_lines(steps[guard_index]["run"])
    condition_at = next(
        index for index, line in enumerate(guard_code) if condition.match(line)
    )
    assert any("exit 1" in line for line in guard_code[condition_at:]), (
        "The emptiness guard does not exit non-zero."
    )
    assert any(
        "::error::" in line and name in line for line in guard_code[condition_at:]
    ), (
        "The fail-closed guard must name the credential in an ::error:: "
        "annotation so the message says what to provision."
    )
    first_read = min(
        index
        for index, step in enumerate(steps)
        if name in (step.get("env") or {}).get("GH_TOKEN", "")
    )
    assert guard_index < first_read, (
        "The fail-closed guard must run before the first elevated read, or the "
        "first failure the operator sees is a 401 rather than the actionable "
        "'the credential is missing' message."
    )


def test_the_secret_scanning_projection_never_emits_the_plaintext_secret():
    """This repository is PUBLIC, so Actions logs are world-readable.

    The list-alerts response carries a plaintext `secret` field holding the
    leaked credential itself. Actions would not redact it, because it is not a
    registered Actions secret, and the failure path writes this file to stderr.
    `.secret_type` is the detector name and is safe; `.secret` is not, and
    neither is a bare `.[]` dump of whole alert objects.
    """
    code = _audit_code()
    match = re.search(
        r"gh api --paginate \"repos/\$REPO/secret-scanning/alerts[^\"]*\"\s*\\\s*\n"
        r"\s*--jq '(?P<projection>[^']*)'",
        code,
    )
    assert match, "The secret-scanning read has no pinned --jq projection."
    projection = match.group("projection")
    assert re.search(r"\.secret(?!_type)", projection) is None, (
        f"The secret-scanning projection {projection!r} references the "
        f"plaintext `.secret` field. That would print leaked credentials, "
        f"unmasked, into a world-readable Actions log."
    )
    assert projection.strip().startswith(".[] |"), (
        f"The secret-scanning projection {projection!r} is not a field "
        f"selection. A bare `.[]` dumps whole alert objects, plaintext "
        f"`secret` field included."
    )


def test_the_open_alert_gate_does_not_depend_on_trailing_newlines():
    """`wc -l` counts newlines, not lines.

    A final alert line without a trailing newline would undercount, so a
    one-alert board could report clean. `-s` is invariant by construction.
    """
    code = _audit_code()
    assert re.search(
        r"^\s*if \[\[ -s open-secret-alerts\.txt \]\]; then$", code, re.MULTILINE
    ), "The open-alert gate must test the file for emptiness, not count lines."
    assert "wc -l < open-secret-alerts.txt" not in code


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
    body = "\n".join(_code(step["run"]) for step in evidence)
    # Matched on the assignments that feed the summary line, not on the words
    # appearing somewhere in the body: an annotation mentioning "PENDING" in
    # prose would otherwise satisfy this while the reported value was gone.
    for branch_value in ('cadence="ARMED', 'cadence="PENDING'):
        assert branch_value in body, (
            f"The cadence probe must assign {branch_value}...\" so both outcomes "
            f"reach the evidence summary."
        )
    # Pin the two branch conditions themselves. `-eq 0` mutated to `-ge 0`
    # always reports ARMED; `elif grep -q 'HTTP 404'` collapsed to a bare
    # `else` makes any failure — a rate-limited run included — report PENDING,
    # which would claim the schedule is inert forever.
    assert re.search(r'^\s*if \[\[ "\$status" -eq 0 \]\]; then$', body, re.MULTILINE), (
        "The ARMED branch must be taken only on a clean exit status."
    )
    assert re.search(
        r"^\s*elif grep -q 'HTTP 404' gh-error\.txt; then$", body, re.MULTILINE
    ), (
        "PENDING must be reported only for a clean 404. A bare `else` would "
        "report a rate-limited or broken probe as a permanently inert schedule."
    )
    assert re.search(r"^\s*else$", body, re.MULTILINE) and re.search(
        r"^\s*exit 1$", body, re.MULTILINE
    ), "A probe failure that is neither a hit nor a clean 404 must fail the job."
    assert "Scheduled cadence: $cadence" in body, (
        "The probed value must be written into the job summary, or the audit "
        "reports a cadence state nobody can read."
    )
    # Again matched on the invocation, not the body: the step's own error
    # message quotes the same URL, so a substring search would survive the
    # probe losing its ref pin and silently reading the current branch.
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


def test_the_cadence_summary_claims_only_the_check_that_ran():
    """ARMED is INFERRED from file presence, not read back from GitHub.

    A file that reached the default branch with its `schedule:` block deleted
    or its cron malformed would still be present, so the summary must say what
    was checked rather than assert that the triggers are registered. Reading
    `repos/$REPO/actions/workflows/...` instead would need an `actions: read`
    grant nothing else here uses, and still would not validate the cron.
    """
    code = _audit_code()
    armed = next(line for line in code.splitlines() if 'cadence="ARMED' in line)
    assert "present on" in armed and "checked:" in armed, (
        f"The ARMED line states a conclusion rather than the check that ran: "
        f"{armed.strip()!r}"
    )
    assert "actions: read" not in _load(AUDIT_WORKFLOW).get("permissions", {})
