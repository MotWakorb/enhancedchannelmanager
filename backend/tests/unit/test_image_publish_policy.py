"""Dangerous-mutant tests for exact-revision image publication."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(
    os.environ.get(
        "IMAGE_PUBLISH_POLICY_SCRIPT", ROOT / "scripts" / "image_publish_policy.py"
    )
)
BUILD = ROOT / ".github" / "workflows" / "build.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish-images.yml"
TESTS = ROOT / ".github" / "workflows" / "test.yml"


@pytest.fixture(scope="module")
def policy():
    spec = importlib.util.spec_from_file_location("image_publish_policy", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(name: str, *, run_number: int = 7, **overrides):
    value = {
        "id": run_number,
        "name": name,
        "head_sha": "abc123",
        "head_branch": "dev",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_number": run_number,
        "run_attempt": 1,
    }
    value.update(overrides)
    return value


def _inputs(**trigger_overrides):
    trigger = _run("Tests")
    trigger.update(trigger_overrides)
    return {
        "trigger": trigger,
        "branch_sha": "abc123",
        "runs": [_run("Tests"), _run("Build and Push Docker Image")],
    }


def test_current_green_push_revision_is_publishable(policy):
    assert policy.validate_publish_candidate(**_inputs()) == ("dev", "abc123")


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", None])
@pytest.mark.parametrize("workflow", ["Tests", "Build and Push Docker Image"])
def test_failed_cancelled_timed_out_or_incomplete_workflow_denies_publish(
    policy, workflow, conclusion
):
    values = _inputs()
    target = next(run for run in values["runs"] if run["name"] == workflow)
    target["conclusion"] = conclusion
    if conclusion is None:
        target["status"] = "in_progress"
    with pytest.raises(policy.PolicyError):
        policy.validate_publish_candidate(**values)


def test_missing_workflow_denies_publish(policy):
    values = _inputs()
    values["runs"] = values["runs"][:1]
    with pytest.raises(policy.PolicyError):
        policy.validate_publish_candidate(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [("head_sha", "wrong"), ("head_branch", "feature/x"), ("event", "pull_request")],
)
def test_wrong_sha_branch_or_pr_trigger_denies_publish(policy, field, value):
    values = _inputs(**{field: value})
    with pytest.raises(policy.PolicyError):
        policy.validate_publish_candidate(**values)


def test_stale_sha_denies_publish(policy):
    values = _inputs()
    values["branch_sha"] = "newer"
    with pytest.raises(policy.PolicyError):
        policy.validate_publish_candidate(**values)


def test_latest_rerun_controls_outcome(policy):
    values = _inputs()
    values["runs"].append(
        _run("Tests", run_number=8, conclusion="cancelled")
    )
    with pytest.raises(policy.PolicyError):
        policy.validate_publish_candidate(**values)
    values["runs"][-1]["conclusion"] = "success"
    assert policy.validate_publish_candidate(**values) == ("dev", "abc123")


@pytest.mark.parametrize(
    ("branch", "tests_attested"), [("main", False), ("dev", True)]
)
def test_authorization_filters_large_irrelevant_run_sets_before_cli_serialization(
    policy, branch, tests_attested
):
    publish = PUBLISH.read_text(encoding="utf-8")
    match = re.search(
        r"runs=\$\(gh api --paginate .*?\| jq -sc '([^']+)'\)",
        publish,
        re.DOTALL,
    )
    assert match, "authorize must filter paginated runs before assigning $runs"

    required_runs = [
        _run("Tests", run_number=6, head_branch=branch),
        _run("Tests", run_number=7, head_branch=branch),
        _run("Build and Push Docker Image", run_number=6, head_branch=branch),
        _run("Build and Push Docker Image", run_number=7, head_branch=branch),
    ]
    irrelevant_runs = [
        _run(
            "Dependency Graph" if index % 2 else "Publish Verified Images",
            run_number=10_000 + index,
            head_branch=branch,
            conclusion="skipped",
            irrelevant_payload="x" * 256,
        )
        for index in range(1_000)
    ]
    pages = [irrelevant_runs[:500], required_runs, irrelevant_runs[500:]]
    api_output = "\n".join(json.dumps(page) for page in pages)
    assert len(api_output) > 131_072
    result = subprocess.run(
        ["jq", "-sc", match.group(1)],
        input=api_output,
        text=True,
        capture_output=True,
        check=True,
    )
    runs = json.loads(result.stdout)

    assert runs == required_runs
    assert len(result.stdout) < 5_000

    if tests_attested:
        trigger = _run(
            "Tests", run_number=2_147_483_647, head_branch=branch, id=0
        )
        runs.append(trigger)
    else:
        trigger = required_runs[1]

    assert policy.validate_publish_candidate(
        trigger=trigger, branch_sha="abc123", runs=runs
    ) == (branch, "abc123")
    with pytest.raises(policy.PolicyError, match="stale trigger SHA"):
        policy.validate_publish_candidate(
            trigger=trigger, branch_sha="newer", runs=runs
        )
    latest_build = max(
        (run for run in runs if run["name"] == "Build and Push Docker Image"),
        key=lambda run: run["run_number"],
    )
    latest_build["head_sha"] = "wrong"
    with pytest.raises(policy.PolicyError, match="wrong revision"):
        policy.validate_publish_candidate(
            trigger=trigger, branch_sha="abc123", runs=runs
        )


def _attestation(**overrides):
    value = {
        "sha": "abc123",
        "build_run_id": 42,
        "dependency_change": True,
        "frontend_sca": "success",
        "backend_sca": "success",
        "digests": {
            name: "sha256:" + character * 64
            for name, character in (
                ("ecm_amd64", "1"),
                ("ecm_arm64", "2"),
                ("mcp_amd64", "3"),
                ("mcp_arm64", "4"),
            )
        },
    }
    value.update(overrides)
    return value


def test_attestation_binds_all_four_candidate_digests(policy):
    result = policy.validate_candidate_attestation(
        _attestation(), expected_sha="abc123", expected_build_run_id=42
    )
    assert set(result) == {"ecm_amd64", "ecm_arm64", "mcp_amd64", "mcp_arm64"}


def test_missing_architecture_digest_is_rejected(policy):
    value = _attestation()
    del value["digests"]["mcp_arm64"]
    with pytest.raises(policy.PolicyError):
        policy.validate_candidate_attestation(
            value, expected_sha="abc123", expected_build_run_id=42
        )


def test_dependency_change_requires_both_exact_sha_sca_results(policy):
    with pytest.raises(policy.PolicyError):
        policy.validate_candidate_attestation(
            _attestation(backend_sca="skipped"),
            expected_sha="abc123",
            expected_build_run_id=42,
        )


def test_workflows_separate_verification_from_publication():
    build = BUILD.read_text(encoding="utf-8")
    publish = PUBLISH.read_text(encoding="utf-8")
    tests = TESTS.read_text(encoding="utf-8")
    assert "needs.wait-for-tests.result" not in build
    assert "push: ${{ github.event_name != 'pull_request' }}" not in build
    assert "workflow_run:" in publish
    assert "workflow_call:" in publish
    assert "workflows: [Tests, Build and Push Docker Image]" in publish
    assert "uses: ./.github/workflows/publish-images.yml" in tests
    assert "tests_attested: true" in tests
    assert "github.event.workflow_run.head_sha" in publish
    # A called workflow sees the caller's github context, so gating the dev
    # path on `github.event_name == 'workflow_call'` never matches and every
    # publish job skips silently (enhancedchannelmanager-0dsa4).
    assert "github.event_name == 'workflow_call'" not in publish
    assert "(inputs.tests_attested && inputs.candidate_branch == 'dev')" in publish
    assert "image_publish_policy.py" in publish
    assert "docker/build-push-action" not in publish
    assert "skopeo copy --preserve-digests" in publish
    assert "outputs: type=oci" in build
    assert build.count("outputs: type=oci") == 4
    assert "steps.build.outputs.digest" not in build
    assert "verify-archive" in publish
    assert "packages: write" not in build


def test_every_gh_cli_step_declares_github_authentication():
    """A gh invocation must never depend on ambient or misnamed credentials."""
    import yaml

    command = re.compile(r"\bgh\s+")
    failures = []
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (workflow.get("jobs") or {}).items():
            for index, step in enumerate((job or {}).get("steps") or []):
                if command.search(str((step or {}).get("run", ""))):
                    env = (step or {}).get("env") or {}
                    if not ({"GH_TOKEN", "GITHUB_TOKEN"} & set(env)):
                        failures.append(f"{path.name}:{job_id}:steps[{index}]")
    assert failures == [], f"gh CLI steps missing GitHub token env: {failures}"


SCAN_CANDIDATES = {
    "trivy-scan": ("build-amd64", "ecm-amd64"),
    "trivy-scan-arm64": ("build-arm64", "ecm-arm64"),
    "trivy-scan-mcp-amd64": ("build-mcp-amd64", "mcp-amd64"),
    "trivy-scan-mcp-arm64": ("build-mcp-arm64", "mcp-arm64"),
}
TRIVY_ACTION = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"


@pytest.fixture
def build_workflow():
    import yaml

    return yaml.safe_load(BUILD.read_text(encoding="utf-8"))


def _assert_verified_candidate_scans(workflow):
    """Each scanner consumes its own verified candidate, independent of native jobs."""
    jobs = workflow.get("jobs", {})
    for job_id, (build_job, candidate) in SCAN_CANDIDATES.items():
        assert job_id in jobs, f"missing scanner job: {job_id}"
        job = jobs[job_id]
        assert job["needs"] == [build_job], job_id
        assert "if" not in job and not job.get("continue-on-error"), job_id
        steps = job["steps"]
        downloads = [
            step["with"]
            for step in steps
            if step.get("uses", "").startswith("actions/download-artifact@")
        ]
        assert downloads == [{"name": f"{candidate}-candidate", "path": "/tmp"}], job_id

        archive = f"/tmp/{candidate}.oci.tar"
        scan_archive = f"/tmp/trivy-{candidate}-scan.docker.tar"
        digest = "${{ needs." + build_job + ".outputs.digest }}"
        verify = (
            f"python scripts/candidate_image.py verify-archive {archive} "
            f"--expected '{digest}'"
        )
        pin = 'test "$(skopeo --version)" = "skopeo version 1.13.3"'
        convert = f"skopeo copy oci-archive:{archive} docker-archive:{scan_archive}"
        preparations = [
            (index, step, [line.strip() for line in step.get("run", "").splitlines()])
            for index, step in enumerate(steps)
            if "skopeo copy " in step.get("run", "")
        ]
        assert len(preparations) == 1, job_id
        preparation_index, preparation, commands = preparations[0]
        assert "if" not in preparation and not preparation.get("continue-on-error"), job_id
        assert all(command in commands for command in (verify, pin, convert)), job_id
        assert commands.index(verify) < commands.index(pin) < commands.index(convert), job_id

        scans = [
            (index, step)
            for index, step in enumerate(steps)
            if step.get("uses", "").startswith("aquasecurity/trivy-action@")
        ]
        assert scans, f"{job_id}: missing scan"
        for index, scan in scans:
            assert scan["uses"] == TRIVY_ACTION, job_id
            assert index > preparation_index, job_id
            assert "if" not in scan and not scan.get("continue-on-error"), job_id
            assert scan["with"]["input"] == scan_archive, job_id
        blocking = [scan for _, scan in scans if scan["with"].get("exit-code") == "1"]
        assert len(blocking) == 1, f"{job_id}: missing blocking scan"
        assert blocking[0]["with"]["severity"] == "CRITICAL,HIGH", job_id


def test_trivy_scans_converted_docker_archives_from_verified_oci_candidates(build_workflow):
    """All four scans retain their bar even when native jobs also convert archives."""
    _assert_verified_candidate_scans(build_workflow)


@pytest.mark.parametrize("job_id", SCAN_CANDIDATES)
@pytest.mark.parametrize(
    "mutation",
    [
        "remove-job",
        "remove-scans",
        "remove-blocking-scan",
        "remove-verifier",
        "remove-version-check",
        "wrong-version",
        "wrong-digest-architecture",
        "wrong-source-architecture",
        "wrong-scan-architecture",
        "scan-oci-archive",
        "convert-before-verify",
    ],
)
def test_candidate_scan_contract_rejects_dangerous_mutants(build_workflow, job_id, mutation):
    # Validate the real fixture first so an unrelated failure cannot satisfy raises().
    _assert_verified_candidate_scans(build_workflow)
    build_job, candidate = SCAN_CANDIDATES[job_id]
    job = build_workflow["jobs"][job_id]
    steps = job["steps"]
    preparation = next(step for step in steps if "skopeo copy " in step.get("run", ""))
    architecture, other_architecture = (
        ("amd64", "arm64") if "amd64" in candidate else ("arm64", "amd64")
    )
    other_candidate = candidate.replace(architecture, other_architecture)
    other_build = build_job.replace(architecture, other_architecture)
    if mutation == "remove-job":
        del build_workflow["jobs"][job_id]
    elif mutation in {"remove-scans", "remove-blocking-scan"}:
        job["steps"] = [
            step
            for step in steps
            if not (
                step.get("uses") == TRIVY_ACTION
                and (mutation == "remove-scans" or step["with"].get("exit-code") == "1")
            )
        ]
    elif mutation in {"remove-verifier", "remove-version-check"}:
        marker = "verify-archive " if mutation == "remove-verifier" else "skopeo --version"
        preparation["run"] = "\n".join(
            line for line in preparation["run"].splitlines() if marker not in line
        )
    elif mutation == "wrong-version":
        preparation["run"] = preparation["run"].replace(
            "skopeo version 1.13.3", "skopeo version 1.13.2"
        )
    elif mutation == "wrong-digest-architecture":
        preparation["run"] = preparation["run"].replace(
            f"needs.{build_job}.", f"needs.{other_build}."
        )
    elif mutation == "wrong-source-architecture":
        preparation["run"] = preparation["run"].replace(
            f"oci-archive:/tmp/{candidate}.", f"oci-archive:/tmp/{other_candidate}."
        )
    elif mutation in {"wrong-scan-architecture", "scan-oci-archive"}:
        scan = next(step for step in steps if step.get("uses") == TRIVY_ACTION)
        scan["with"]["input"] = (
            f"/tmp/trivy-{other_candidate}-scan.docker.tar"
            if mutation == "wrong-scan-architecture"
            else f"/tmp/{candidate}.oci.tar"
        )
    elif mutation == "convert-before-verify":
        lines = preparation["run"].splitlines()
        conversion = next(line for line in lines if "skopeo copy " in line)
        lines.remove(conversion)
        lines.insert(0, conversion)
        preparation["run"] = "\n".join(lines)

    with pytest.raises(AssertionError, match=job_id):
        _assert_verified_candidate_scans(build_workflow)
