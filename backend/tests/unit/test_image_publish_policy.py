"""Dangerous-mutant tests for exact-revision image publication."""

from __future__ import annotations

import importlib.util
import os
import re
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
    assert "image_publish_policy.py" in publish
    assert "docker/build-push-action" not in publish
    assert "skopeo copy --preserve-digests" in publish
    assert "outputs: type=oci" in build
    assert build.count("outputs: type=oci") == 4
    assert "steps.build.outputs.digest" not in build
    assert "verify-archive" in publish
    assert "fail_action: true" in build
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


def test_trivy_scans_extracted_oci_layouts_not_raw_archives():
    """Trivy requires an OCI layout directory; raw BuildKit OCI tar files fail."""
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert workflow.count("candidate_image.py extract ") == 4
    assert workflow.count("input: /tmp/trivy-") == 4
    assert not re.search(r"input:\s+\S+\.oci\.tar", workflow)
