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


def _copy_policy_files(gate, destination_root: Path) -> None:
    for path in gate.POLICY_FILES:
        destination = destination_root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            (REPO_ROOT / path).read_text(encoding="utf-8"), encoding="utf-8"
        )


def test_repository_satisfies_mcp_publication_policy():
    gate = _load_gate()
    assert gate.check_repository(REPO_ROOT) == []


def test_policy_rejects_push_builders_that_do_not_require_mcp_scan_success(
    tmp_path,
):
    gate = _load_gate()
    _copy_policy_files(gate, tmp_path)
    workflow = tmp_path / ".github/workflows/build.yml"
    contents = workflow.read_text(encoding="utf-8")
    trigger = "      && needs.security-scan-mcp.result == 'success'\n"
    assert contents.count(trigger) == 2
    workflow.write_text(contents.replace(trigger, "", 1), encoding="utf-8")

    failures = gate.check_repository(tmp_path)

    assert any("MCP dependency audit success" in failure for failure in failures), failures


def test_policy_rejects_mcp_scans_that_ignore_unfixed_high_findings(tmp_path):
    gate = _load_gate()
    _copy_policy_files(gate, tmp_path)
    workflow = tmp_path / ".github/workflows/build.yml"
    contents = workflow.read_text(encoding="utf-8")
    trigger = "          vuln-type: 'os,library'\n"
    assert contents.count(trigger) >= 2
    scan_start = contents.index("  trivy-scan-mcp-amd64:")
    prefix, mcp_scans = contents[:scan_start], contents[scan_start:]
    unsafe_setting = "          ignore-unfixed: true\n" + trigger
    workflow.write_text(
        prefix + mcp_scans.replace(trigger, unsafe_setting, 1),
        encoding="utf-8",
    )

    failures = gate.check_repository(tmp_path)

    assert any("unfixed Critical/High" in failure for failure in failures), failures


def test_policy_rejects_brittle_vulnerable_fixture_output_matcher(tmp_path):
    gate = _load_gate()
    _copy_policy_files(gate, tmp_path)
    workflow = tmp_path / ".github/workflows/build.yml"
    contents = workflow.read_text(encoding="utf-8")
    robust = "^starlette[[:space:]]+0\\.27\\.0[[:space:]]+[^[:space:]]+"
    brittle = "starlette[[:space:]]+0\\.27\\.0[[:space:]]+[1-9][0-9]*"
    assert contents.count(robust) == 1
    workflow.write_text(contents.replace(robust, brittle, 1), encoding="utf-8")

    failures = gate.check_repository(tmp_path)

    assert any("output-format brittle" in failure for failure in failures), failures


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
    _copy_policy_files(gate, tmp_path)

    target = tmp_path / relative_path
    original = target.read_text(encoding="utf-8")
    assert old in original, f"mutation trigger missing from {relative_path}"
    target.write_text(original.replace(old, new, 1), encoding="utf-8")

    failures = gate.check_repository(tmp_path)
    assert any(expected in failure for failure in failures), failures
