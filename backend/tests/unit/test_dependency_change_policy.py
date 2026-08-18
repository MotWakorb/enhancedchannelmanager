"""Tests for the small-team dependency-gate path classifier."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(
    os.environ.get(
        "DEPENDENCY_CHANGE_POLICY_SCRIPT",
        ROOT / "scripts" / "dependency_change_policy.py",
    )
)
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


@pytest.fixture(scope="module")
def policy():
    spec = importlib.util.spec_from_file_location("dependency_change_policy", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    [
        "backend/requirements.txt",
        "backend/requirements.in",
        "backend/requirements-dev.txt",
        "frontend/package.json",
        "frontend/package-lock.json",
        "mcp-server/requirements.txt",
        "Dockerfile",
        "mcp-server/Dockerfile",
    ],
)
def test_dependency_and_image_manifests_trigger(policy, path):
    assert policy.has_dependency_change([path]) is True


def test_unrelated_change_does_not_trigger(policy):
    assert (
        policy.has_dependency_change(["backend/main.py", "docs/shipping.md"]) is False
    )


@pytest.mark.parametrize(
    "paths", [[], [""], ["../frontend/package.json"], [" frontend/package.json"]]
)
def test_ambiguous_input_fails_toward_running_gate(policy, paths):
    assert policy.has_dependency_change(paths) is True


def test_workflow_gates_dev_dependency_prs_before_merge():
    workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/dependency_change_policy.py" in workflow
    assert (
        "needs.detect-dependency-change.outputs.dependency_files_changed == 'true'"
        in workflow
    )
    assert 'git diff --name-only --no-renames -z "$BEFORE_SHA" "$HEAD_SHA"' in workflow
    assert "github.event.before" in workflow
    assert "refs/heads/dev" in workflow
    for check_name in (
        "Frontend Security Scan",
        "Backend Security Scan",
        "DAST Security Scan",
        "Container Security Scan (Trivy)",
    ):
        assert check_name in workflow
