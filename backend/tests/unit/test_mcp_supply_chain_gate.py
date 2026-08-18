"""Regression coverage for the MCP publication supply-chain gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_mcp_supply_chain.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_mcp_supply_chain", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_satisfies_mcp_publication_policy():
    gate = _load_gate()
    assert gate.check_repository(REPO_ROOT) == []


@pytest.mark.parametrize(
    ("relative_path", "old", "new", "expected"),
    [
        (
            ".github/workflows/build.yml",
            "pip-audit -r mcp-server/requirements.txt",
            "echo audit-disabled",
            "MCP dependency audit",
        ),
        (
            ".github/workflows/build.yml",
            "pip-audit -r backend/tests/fixtures/mcp_vulnerable_requirements.txt",
            "echo vulnerable-fixture-disabled",
            "vulnerable-fixture self-test",
        ),
        (
            ".github/workflows/build.yml",
            "needs: [security-scan-backend, security-scan-mcp, wait-for-tests]",
            "needs: [security-scan-backend, wait-for-tests]",
            "both image builders",
        ),
        (
            ".github/workflows/build.yml",
            "needs: [build-mcp-amd64, build-mcp-arm64, trivy-scan-mcp-amd64, trivy-scan-mcp-arm64]",
            "needs: [build-mcp-amd64, build-mcp-arm64]",
            "MCP manifest",
        ),
        (
            ".github/workflows/build.yml",
            "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25",
            "aquasecurity/trivy-action@master",
            "immutable action",
        ),
        (
            "mcp-server/Dockerfile",
            "python:3.12-slim@sha256:",
            "python:3.12-slim # sha256:",
            "digest-pinned FROM",
        ),
        (
            "Dockerfile",
            "RUN npm ci",
            "RUN npm install",
            "npm production build",
        ),
        (
            ".github/workflows/release-cut-gate.yml",
            'echo "${checksum}  ${tarball}" | sha256sum --check --strict -',
            "echo checksum-disabled",
            "release asset checksum",
        ),
    ],
)
def test_policy_mutations_fail_closed(tmp_path, relative_path, old, new, expected):
    gate = _load_gate()
    for path in gate.POLICY_FILES:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((REPO_ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")

    target = tmp_path / relative_path
    original = target.read_text(encoding="utf-8")
    assert old in original, f"mutation trigger missing from {relative_path}"
    target.write_text(original.replace(old, new, 1), encoding="utf-8")

    failures = gate.check_repository(tmp_path)
    assert any(expected in failure for failure in failures), failures
