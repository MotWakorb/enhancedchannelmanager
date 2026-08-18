"""Dangerous-mutant tests for exact-revision image publication."""

from __future__ import annotations

import importlib.util
import os
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
    assert "push: true" in publish
