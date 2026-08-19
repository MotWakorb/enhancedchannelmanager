#!/usr/bin/env python3
"""Fail closed when MCP publication supply-chain controls drift."""

from __future__ import annotations

import re
import sys
from pathlib import Path


POLICY_FILES = (
    Path(".github/workflows/build.yml"),
    Path(".github/workflows/publish-images.yml"),
    Path(".github/workflows/release-cut-gate.yml"),
    Path("Dockerfile"),
    Path("mcp-server/Dockerfile"),
)

MCP_BASE_IMAGE = "python:3.12-alpine@sha256:" + "".join(
    (
        "d09d15e6",
        "0962ca36",
        "5d1cd544",
        "a48773ba",
        "c9d33f2f",
        "b1b00f2a",
        "a0deec78",
        "ade7dc31",
    )
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
_FROM = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)


def check_repository(root: Path) -> list[str]:
    failures: list[str] = []
    build = (root / POLICY_FILES[0]).read_text(encoding="utf-8")
    publish = (root / POLICY_FILES[1]).read_text(encoding="utf-8")
    release = (root / POLICY_FILES[2]).read_text(encoding="utf-8")
    dockerfile = (root / POLICY_FILES[3]).read_text(encoding="utf-8")
    mcp_dockerfile = (root / POLICY_FILES[4]).read_text(encoding="utf-8")

    mcp_base_images = _FROM.findall(mcp_dockerfile)
    if mcp_base_images != [MCP_BASE_IMAGE]:
        failures.append(
            "MCP image must use the reviewed Alpine base digest: "
            f"expected {MCP_BASE_IMAGE}, found {mcp_base_images}"
        )

    if "pip-audit -r mcp-server/requirements.txt" not in build:
        failures.append("MCP dependency audit is absent from the publication workflow")
    if (
        "pip-audit -r backend/tests/fixtures/mcp_vulnerable_requirements.txt"
        not in build
        or "pip-audit accepted the deliberately vulnerable MCP fixture" not in build
    ):
        failures.append("MCP dependency audit vulnerable-fixture self-test is absent")
    robust_fixture_matcher = (
        "grep -Eq '^starlette[[:space:]]+0\\.27\\.0[[:space:]]+[^[:space:]]+'"
    )
    if robust_fixture_matcher not in build:
        failures.append("MCP vulnerable-fixture matcher is output-format brittle")
    if "workflows: [Tests, Build and Push Docker Image]" not in publish:
        failures.append("publication is not triggered by both verification workflows")
    if publish.count("image_publish_policy.py") != 2:
        failures.append("publication does not enforce the exact-SHA policy")

    for required in (
        "- trivy-scan-mcp-amd64",
        "- trivy-scan-mcp-arm64",
        "- build-mcp-amd64",
        "- build-mcp-arm64",
    ):
        if required not in build:
            failures.append("candidate attestation is not gated by both MCP architectures")
    for job in ("trivy-scan-mcp-amd64:", "trivy-scan-mcp-arm64:"):
        if job not in build:
            failures.append(f"MCP image scan job missing: {job[:-1]}")
    mcp_scan_section = build[
        build.index("  trivy-scan-mcp-amd64:") : build.index("  attest-image-candidates:")
    ]
    if "ignore-unfixed:" in mcp_scan_section:
        failures.append("MCP image scans ignore unfixed Critical/High findings")
    # Four image scans: ECM amd64, ECM arm64, and both MCP architectures.
    # Every published architecture is scanned on the same bar, which is why
    # the floor is four and not three.
    #
    # Count settings, not prose. These literals also appear inside build.yml's
    # own comments describing the policy, and a raw whole-file count therefore
    # reads those comments as scans: with the comment block that documents this
    # very floor in place, deleting the ECM amd64 scan job outright still left
    # five `exit-code` and four `severity` matches, so the floor passed on three
    # real scans. Comment lines are stripped first so the count is of settings
    # a runner would actually apply.
    build_settings = "\n".join(
        line for line in build.splitlines() if not line.lstrip().startswith("#")
    )
    for setting in ("exit-code: '1'", "severity: 'CRITICAL,HIGH'"):
        if build_settings.count(setting) < 4:
            failures.append(f"MCP image scans do not enforce {setting}")
    if "outputs: type=oci" not in build or "candidate_image.py digest" not in build:
        failures.append("MCP candidates do not produce verified OCI digests")
    if "skopeo copy --preserve-digests" not in publish:
        failures.append("publication does not preserve verified candidate digests")
    if "docker/build-push-action" in publish:
        failures.append("publication rebuilds instead of promoting verified candidates")
    verification = build[build.index("  build-amd64:") :]
    if "packages: write" in verification or "docker/login-action" in verification:
        failures.append("verification jobs retain registry write authority")

    for workflow_name, workflow in (
        ("build.yml", build),
        ("publish-images.yml", publish),
        ("release-cut-gate.yml", release),
    ):
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

    if "ref: beads" not in release or "authoritative-board/.beads/issues.jsonl" not in release:
        failures.append("release gate does not read the authoritative board branch")

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
