"""Concurrency contract for ``publish-images.yml``.

The workflow runs three ways: as the reusable **Publish Verified Dev Images**
call from a dev ``push`` Tests run, and as a standalone ``workflow_run`` after
either Tests or Build completes. Only the reusable dev call and the ``main``
``workflow_run`` can publish; every other run skips at the ``authorize`` job.

A standalone ``workflow_run`` executes the default branch's copy of this file,
not the copy on the branch that triggered it, so the reusable dev lane must be
a name no branch-derived group (``publish-images-<branch>``) can produce.

GitHub Actions concurrency cannot be executed locally. These tests evaluate the
real group and ``if`` expressions with a small resolver over the GitHub
expression subset the file uses (case-insensitive ``==``, value-returning
``&&``/``||``, ``format``), across every trigger context, and assert:

* a run that cannot publish never shares a group with any other run;
* both publishing paths keep one stable lane each, and the cancel-older
  behaviour between same-path runs;
* the reusable dev lane can never equal a group the default branch's copy
  derives from a branch name;
* the jobs, their gates, and the triggers are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PUBLISH = ROOT / ".github" / "workflows" / "publish-images.yml"


# ─── Minimal GitHub Actions expression resolver ──────────────────────────────

_TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+)|(?P<op>&&|\|\||==|!=|!|\(|\)|,)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_][A-Za-z0-9_\-]*)*))"
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise ValueError(f"unsupported expression syntax at {text[position:]!r}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


def _truthy(value) -> bool:
    return value not in (None, False, 0, "")


def _to_string(value) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _equal(left, right) -> bool:
    # GitHub compares strings case-insensitively and coerces mixed types to
    # numbers; the file only compares strings, so mirror that case exactly.
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    return left == right


class _Parser:
    def __init__(self, tokens, contexts):
        self.tokens, self.index, self.contexts = tokens, 0, contexts

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else (None, None)

    def take(self, value=None):
        token = self.peek()
        if value is not None and token[1] != value:
            raise ValueError(f"expected {value!r}, got {token[1]!r}")
        self.index += 1
        return token

    def parse(self):
        value = self.or_()
        if self.index != len(self.tokens):
            raise ValueError(f"trailing tokens: {self.tokens[self.index:]}")
        return value

    def or_(self):
        value = self.and_()
        while self.peek()[1] == "||":
            self.take()
            right = self.and_()
            value = value if _truthy(value) else right
        return value

    def and_(self):
        value = self.equality()
        while self.peek()[1] == "&&":
            self.take()
            right = self.equality()
            value = right if _truthy(value) else value
        return value

    def equality(self):
        value = self.unary()
        while self.peek()[1] in ("==", "!="):
            operator = self.take()[1]
            right = self.unary()
            value = _equal(value, right) if operator == "==" else not _equal(value, right)
        return value

    def unary(self):
        if self.peek()[1] == "!":
            self.take()
            return not _truthy(self.unary())
        return self.primary()

    def primary(self):
        kind, text = self.take()
        if text == "(":
            value = self.or_()
            self.take(")")
            return value
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return int(text)
        if kind == "name":
            if text in ("true", "false", "null"):
                return {"true": True, "false": False, "null": None}[text]
            if self.peek()[1] == "(":
                return self.call(text)
            value = self.contexts
            for part in text.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            return value
        raise ValueError(f"unexpected token {text!r}")

    def call(self, function):
        self.take("(")
        arguments = []
        while self.peek()[1] != ")":
            arguments.append(self.or_())
            if self.peek()[1] == ",":
                self.take()
        self.take(")")
        if function.lower() != "format":
            raise ValueError(f"unsupported function {function}")
        template, *values = arguments
        return re.sub(
            r"\{(\d+)\}", lambda match: _to_string(values[int(match.group(1))]), template
        )


def evaluate(expression: str, contexts: dict):
    """Evaluate one bare expression (no ``${{ }}`` wrapper)."""
    return _Parser(_tokenize(expression), contexts).parse()


def interpolate(template: str, contexts: dict) -> str:
    """Render a string field the way the runner does: substitute each ``${{ }}``."""
    return re.sub(
        r"\$\{\{(.*?)\}\}",
        lambda match: _to_string(evaluate(match.group(1), contexts)),
        template,
        flags=re.DOTALL,
    )


def test_resolver_matches_github_expression_semantics():
    """Smoke-test the instrument on known-good and known-bad inputs first."""
    contexts = {"inputs": {"b": "dev", "t": True}, "github": {"run_id": 9}}
    assert evaluate("inputs.b || 'x'", contexts) == "dev"
    assert evaluate("inputs.missing || 'x'", contexts) == "x"
    assert evaluate("inputs.t && 'yes'", contexts) == "yes"
    assert evaluate("inputs.missing && 'yes'", contexts) is None
    assert evaluate("inputs.b == 'DEV'", contexts) is True
    assert evaluate("inputs.b == 'main'", contexts) is False
    assert evaluate("!inputs.missing", contexts) is True
    assert evaluate("format('a-{0}', github.run_id)", contexts) == "a-9"
    assert interpolate("p-${{ inputs.b }}", contexts) == "p-dev"
    assert interpolate("p-${{ inputs.missing }}", contexts) == "p-"
    with pytest.raises(ValueError):
        evaluate("inputs.b ? 1 : 2", contexts)


# ─── Trigger contexts ────────────────────────────────────────────────────────


def _reusable_dev(run_id: int, sha: str) -> dict:
    """Tests push on dev calling publish-images.yml: github is the CALLER's."""
    return {
        "inputs": {"candidate_sha": sha, "candidate_branch": "dev", "tests_attested": True},
        "github": {"event_name": "push", "run_id": run_id, "event": {"after": sha}},
    }


def _workflow_run(run_id: int, workflow: str, branch: str, event: str = "push") -> dict:
    """A standalone run: no inputs, github.event is the completed run."""
    return {
        "inputs": {},
        "github": {
            "event_name": "workflow_run",
            "run_id": run_id,
            "event": {
                "workflow_run": {
                    "name": workflow,
                    "head_branch": branch,
                    "head_sha": "a" * 40,
                    "event": event,
                }
            },
        },
    }


# The two publisher lanes. REUSABLE_DEV_LANE is deliberately not a
# `publish-images-<branch>` shape; see test_reusable_dev_lane_is_not_a_branch_group.
REUSABLE_DEV_LANE = "publish-images-dev:reusable"
MAIN_LANE = "publish-images-main"

TESTS = "Tests"
BUILD = "Build and Push Docker Image"

# Every way this file runs. Run ids are unique per context, as on GitHub.
CONTEXTS = {
    "reusable-dev-sha1": _reusable_dev(1001, "1" * 40),
    "reusable-dev-sha2": _reusable_dev(1002, "2" * 40),
    # Build finishes after Tests' reusable publisher started (observed order).
    "dev-workflow_run-after-build": _workflow_run(2001, BUILD, "dev"),
    # Tests finishes (after its reusable publisher returns or is cancelled).
    "dev-workflow_run-after-tests": _workflow_run(2002, TESTS, "dev"),
    "main-workflow_run-after-tests": _workflow_run(3001, TESTS, "main"),
    "main-workflow_run-after-build": _workflow_run(3002, BUILD, "main"),
    # Tests and Build both run on pull_request; head_branch is the PR head,
    # which a fork may name `main` or `dev`.
    "pr-workflow_run-head-main": _workflow_run(4001, TESTS, "main", "pull_request"),
    "pr-workflow_run-head-dev": _workflow_run(4002, BUILD, "dev", "pull_request"),
    "pr-workflow_run-feature": _workflow_run(4003, TESTS, "fix/x", "pull_request"),
    "dispatch-workflow_run-dev": _workflow_run(4004, TESTS, "dev", "workflow_dispatch"),
    # No caller does either today. Each pins one clause of the group's mirror
    # of the gate: the first `inputs.tests_attested`, the second `== 'dev'`.
    "reusable-unattested-dev": {
        "inputs": {"candidate_sha": "4" * 40, "candidate_branch": "dev", "tests_attested": False},
        "github": {"event_name": "push", "run_id": 5002, "event": {}},
    },
    "reusable-unattested-main": {
        "inputs": {"candidate_sha": "3" * 40, "candidate_branch": "main", "tests_attested": False},
        "github": {"event_name": "push", "run_id": 5001, "event": {}},
    },
}


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))


def _group(workflow: dict, context: dict) -> str:
    return interpolate(workflow["concurrency"]["group"], context)


def _publishes(workflow: dict, context: dict) -> bool:
    return _truthy(evaluate(workflow["jobs"]["authorize"]["if"], context))


def test_only_the_reusable_dev_call_and_main_workflow_run_can_publish(workflow):
    """Sanity: the contexts above exercise the authorize gate as intended."""
    publishing = {name for name, ctx in CONTEXTS.items() if _publishes(workflow, ctx)}
    assert publishing == {
        "reusable-dev-sha1",
        "reusable-dev-sha2",
        "main-workflow_run-after-tests",
        "main-workflow_run-after-build",
    }


@pytest.mark.parametrize(
    "skipped", ["dev-workflow_run-after-build", "dev-workflow_run-after-tests"]
)
def test_skipped_dev_workflow_run_cannot_enter_the_reusable_publisher_group(
    workflow, skipped
):
    """Holds for both Build-after-Tests and Tests-after-Build order."""
    publisher = _group(workflow, CONTEXTS["reusable-dev-sha1"])
    assert not _publishes(workflow, CONTEXTS[skipped])
    assert _group(workflow, CONTEXTS[skipped]) != publisher


def test_a_run_that_cannot_publish_never_shares_a_concurrency_group(workflow):
    """With cancel-in-progress, a shared group can cancel.

    Any run whose authorize gate is false must resolve to a group no other run
    resolves to, so it can neither cancel nor be cancelled by a publisher.
    """
    groups = {name: _group(workflow, ctx) for name, ctx in CONTEXTS.items()}
    for name, ctx in CONTEXTS.items():
        if _publishes(workflow, ctx):
            continue
        sharing = [other for other, group in groups.items() if other != name and group == groups[name]]
        assert sharing == [], f"{name} ({groups[name]}) shares its group with {sharing}"


def test_publishing_paths_keep_their_groups_and_cancel_older_runs(workflow):
    """Successive dev SHAs and both main orderings still serialise, newest wins."""
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert _group(workflow, CONTEXTS["reusable-dev-sha1"]) == REUSABLE_DEV_LANE
    assert _group(workflow, CONTEXTS["reusable-dev-sha2"]) == REUSABLE_DEV_LANE
    assert _group(workflow, CONTEXTS["main-workflow_run-after-tests"]) == MAIN_LANE
    assert _group(workflow, CONTEXTS["main-workflow_run-after-build"]) == MAIN_LANE


def test_group_eligibility_mirrors_the_authorize_gate(workflow):
    """The group may only name a publisher lane when the gate would publish.

    Both expressions are derived from the same predicate; if one changes
    without the other, a skipped run could re-enter a publisher lane.
    """
    lanes = {REUSABLE_DEV_LANE, MAIN_LANE}
    for name, ctx in CONTEXTS.items():
        assert (_group(workflow, ctx) in lanes) == _publishes(workflow, ctx), name


def test_unattested_dev_call_is_ineligible_and_skips(workflow):
    """Pins the group's `inputs.tests_attested` clause on the dev branch."""
    context = CONTEXTS["reusable-unattested-dev"]
    assert not _publishes(workflow, context)
    assert _group(workflow, context) == "publish-images-ineligible-5002"


# ─── Default-branch copy: workflow_run runs never read this branch's file ────

# The group expression of the default branch's copy of this file, read with
# `git show origin/main:.github/workflows/publish-images.yml` (origin/main
# d5e91a37). A standalone workflow_run evaluates the default branch's
# expression, never the one in this branch's copy.
DEFAULT_BRANCH_GROUP = (
    "publish-images-${{ inputs.candidate_branch || github.event.workflow_run.head_branch }}"
)

# Characters git-check-ref-format(1) forbids anywhere in a ref name: rule 4
# (control characters, DEL, space, `~`, `^`, `:`), rule 5 (`?`, `*`, `[`) and
# rule 10 (`\`).
_FORBIDDEN_IN_REFS = set(" ~^:?*[\\") | {chr(c) for c in range(0x20)} | {"\x7f"}


def test_reusable_dev_lane_is_not_a_branch_group():
    """No valid branch x gives `publish-images-<x>` equal to the reusable lane.

    The lane equals `publish-images-<x>` (case-insensitively, as GitHub
    compares groups) only if x is the lane's suffix, ignoring case. That
    suffix holds a character git forbids in every ref name, in any case, so
    no branch, tag or PR head can be that x.
    """
    prefix = "publish-images-"
    assert REUSABLE_DEV_LANE.startswith(prefix)
    suffix = REUSABLE_DEV_LANE[len(prefix):]
    assert set(suffix) & _FORBIDDEN_IN_REFS, suffix
    assert set(suffix.lower()) & _FORBIDDEN_IN_REFS
    assert set(suffix.upper()) & _FORBIDDEN_IN_REFS


def test_default_branch_copy_cannot_cancel_the_reusable_dev_publisher(workflow):
    """A dev push's standalone workflow_run runs the default branch's group."""
    publisher = _group(workflow, CONTEXTS["reusable-dev-sha1"])
    for name, context in CONTEXTS.items():
        if context["github"]["event_name"] != "workflow_run":
            continue
        group = interpolate(DEFAULT_BRANCH_GROUP, context)
        assert group.lower() != publisher.lower(), name
    # Under that expression a dev push's workflow_runs take `publish-images-dev`,
    # so the reusable lane must not be that name.
    for name in ("dev-workflow_run-after-build", "dev-workflow_run-after-tests"):
        assert interpolate(DEFAULT_BRANCH_GROUP, CONTEXTS[name]) == "publish-images-dev"


def test_main_lane_matches_the_default_branch_copy(workflow):
    """Main pushes serialise in one lane whichever copy of the file runs."""
    for name in ("main-workflow_run-after-tests", "main-workflow_run-after-build"):
        assert interpolate(DEFAULT_BRANCH_GROUP, CONTEXTS[name]) == MAIN_LANE
        assert _group(workflow, CONTEXTS[name]) == MAIN_LANE


# ─── Preservation: everything but the concurrency group is unchanged ─────────

# The authorize gate. Changing it changes who can publish: re-review the
# exact-SHA, attestation, scan and digest contracts before editing this copy.
AUTHORIZE_IF = (
    "(inputs.tests_attested && inputs.candidate_branch == 'dev')"
    " || (github.event_name == 'workflow_run'"
    " && github.event.workflow_run.event == 'push'"
    " && github.event.workflow_run.head_branch == 'main')"
)

# sha256 of the canonical JSON of `on` and `jobs`. Any edit to the triggers,
# job graph, gates, steps or permissions must be reviewed against the
# exact-SHA, attestation, scan and digest contracts before re-pinning these.
TRIGGERS_SHA256 = "ef2ba0aca9d2b8589ea4d01812edbc8dc131b8f0f1b739a1f0f14ffdc1aeefd6"
JOBS_SHA256 = "9a2be78b6654cc88f64f31dedf1a909492f2bb8c28f4567c9071265afa47aba2"


def _canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_authorize_gate_is_unchanged(workflow):
    assert workflow["jobs"]["authorize"]["if"] == AUTHORIZE_IF


def test_triggers_and_jobs_match_their_reviewed_hashes(workflow):
    # PyYAML reads the bare key `on` as boolean True.
    assert _canonical_sha256(workflow[True]) == TRIGGERS_SHA256
    assert _canonical_sha256(workflow["jobs"]) == JOBS_SHA256


def test_concurrency_is_workflow_level_and_only_there(workflow):
    """Job-level concurrency would let an authorized run's publish jobs race."""
    assert set(workflow["concurrency"]) == {"group", "cancel-in-progress"}
    for job_id, job in workflow["jobs"].items():
        assert "concurrency" not in job, job_id


def test_exact_sha_attestation_and_digest_gates_remain_required(workflow):
    jobs = workflow["jobs"]
    authorize = "\n".join(step.get("run", "") for step in jobs["authorize"]["steps"])
    # Exact-current-SHA authorization, before and while waiting for Build.
    assert 'if test "$branch_sha" != "$SHA"; then' in authorize
    assert "Candidate became stale while awaiting verification" in authorize
    # Test + Build evidence and the Build attestation (which carries the scan
    # and SCA results) through the policy script, both phases.
    assert authorize.count("python scripts/image_publish_policy.py") == 2
    assert '--build-run-id "$build_run_id" --attestation "$attestation"' in authorize
    assert "--name verified-image-candidates" in authorize
    assert 'test "$(jq -r \'.sha\' <<<"$decision")" = "$SHA"' in authorize
    for arch in ("amd64", "arm64"):
        job = jobs[f"publish-{arch}"]
        assert job["needs"] == ["authorize"]
        assert "if" not in job and not job.get("continue-on-error")
        promote = "\n".join(step.get("run", "") for step in job["steps"])
        assert promote.count("python scripts/candidate_image.py verify-archive") == 2
        assert promote.count("assert_published ") == 2
        assert promote.count("skopeo copy --preserve-digests") == 2
    manifests = jobs["publish-manifests"]
    assert manifests["needs"] == ["authorize", "publish-amd64", "publish-arm64"]
    assert "if" not in manifests and not manifests.get("continue-on-error")
