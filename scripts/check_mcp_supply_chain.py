#!/usr/bin/env python3
"""Fail closed when MCP publication supply-chain controls drift."""

from __future__ import annotations

import re
import sys
from pathlib import Path


POLICY_FILES = (
    Path(".github/workflows/build.yml"),
    Path(".github/workflows/release-cut-gate.yml"),
    Path("Dockerfile"),
    Path("mcp-server/Dockerfile"),
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_USES = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
_FROM = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)


def check_repository(root: Path) -> list[str]:
    failures: list[str] = []
    build = (root / POLICY_FILES[0]).read_text(encoding="utf-8")
    release = (root / POLICY_FILES[1]).read_text(encoding="utf-8")
    dockerfile = (root / POLICY_FILES[2]).read_text(encoding="utf-8")
    mcp_dockerfile = (root / POLICY_FILES[3]).read_text(encoding="utf-8")

    if "pip-audit -r mcp-server/requirements.txt" not in build:
        failures.append("MCP dependency audit is absent from the publication workflow")

    expected_needs = (
        "needs: [build-mcp-amd64, build-mcp-arm64, "
        "trivy-scan-mcp-amd64, trivy-scan-mcp-arm64]"
    )
    if expected_needs not in build:
        failures.append("MCP manifest is not gated by both architecture image scans")
    for job in ("trivy-scan-mcp-amd64:", "trivy-scan-mcp-arm64:"):
        if job not in build:
            failures.append(f"MCP image scan job missing: {job[:-1]}")
    for setting in ("exit-code: '1'", "severity: 'CRITICAL,HIGH'"):
        if build.count(setting) < 4:
            failures.append(f"MCP image scans do not enforce {setting}")

    for workflow_name, workflow in (("build.yml", build), ("release-cut-gate.yml", release)):
        for reference in _USES.findall(workflow):
            if reference.startswith("./"):
                continue
            _, separator, revision = reference.rpartition("@")
            if not separator or not _FULL_SHA.fullmatch(revision):
                failures.append(
                    f"immutable action requirement violated in {workflow_name}: {reference}"
                )

    for name, contents in (("Dockerfile", dockerfile), ("mcp-server/Dockerfile", mcp_dockerfile)):
        for image in _FROM.findall(contents):
            if "@sha256:" not in image:
                failures.append(f"digest-pinned FROM requirement violated in {name}: {image}")

    if "RUN npm ci" not in dockerfile or "RUN npm install" in dockerfile:
        failures.append("npm production build must install from the lockfile with npm ci")

    checksum = 'echo "${BEADS_SHA256}  ${tarball}" | sha256sum --check --strict -'
    if checksum not in release or "BEADS_SHA256:" not in release:
        failures.append("release asset checksum verification is absent")

    return failures


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures = check_repository(root)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("MCP publication supply-chain policy: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
