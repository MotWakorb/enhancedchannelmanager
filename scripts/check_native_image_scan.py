#!/usr/bin/env python3
"""Fail closed on incomplete, substituted or suppressed native image scan evidence.

This validates Trivy's complete JSON output, not a severity-filtered table. The
separate HIGH/CRITICAL exit-1 scan remains a publication gate. Package floors and
inventory are detection sanity checks, never substitutes for advisory review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
MIN_OS_PACKAGES = 102
PACKAGE_FLOORS = {"ffmpeg-9.0": (9, 0, 2), "libxml2-16": (2, 15, 4)}


def _timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _has_suppression(document: object) -> bool:
    if isinstance(document, dict):
        for key, value in document.items():
            if (
                any(
                    word in key.lower()
                    for word in ("suppress", "modifiedfinding", "vex")
                )
                and value
            ):
                return True
            if _has_suppression(value):
                return True
    elif isinstance(document, list):
        return any(_has_suppression(value) for value in document)
    return False


def check_report(
    report: object, identity: object, scanner: object, architecture: str
) -> list[str]:
    """Return failures for the native candidate's complete Trivy evidence."""
    failures = []
    if (
        not isinstance(identity, dict)
        or not isinstance(report, dict)
        or not isinstance(scanner, dict)
    ):
        return ["report, candidate identity and scanner identity must be objects"]
    if architecture not in {"amd64", "arm64"}:
        failures.append("unsupported expected architecture")
    if any(
        not isinstance(identity.get(key), str) or not DIGEST.fullmatch(identity[key])
        for key in ("ManifestDigest", "ConfigDigest")
    ):
        failures.append("missing candidate manifest/config digests")
    diff_ids = identity.get("RootFSDiffIDs")
    if (
        not isinstance(diff_ids, list)
        or not diff_ids
        or any(
            not isinstance(value, str) or not DIGEST.fullmatch(value)
            for value in diff_ids
        )
    ):
        failures.append("missing candidate rootfs identity")
    if identity.get("OS") != "linux" or identity.get("Architecture") != architecture:
        failures.append("candidate platform substitution")
    if (
        report.get("SchemaVersion") != 2
        or report.get("ArtifactType") != "container_image"
    ):
        failures.append("unsupported/missing Trivy container report schema")
    if not _timestamp(report.get("CreatedAt")):
        failures.append("missing scan timestamp")
    trivy = report.get("Trivy")
    if (
        not isinstance(trivy, dict)
        or not trivy.get("Version")
        or trivy.get("Version") != scanner.get("Version")
    ):
        failures.append("missing/mismatching scanner version")
    db = scanner.get("VulnerabilityDB")
    if (
        not isinstance(db, dict)
        or db.get("Version") != 2
        or not all(
            _timestamp(db.get(key))
            for key in ("UpdatedAt", "NextUpdate", "DownloadedAt")
        )
    ):
        failures.append("missing vulnerability database identity/timestamps")
    metadata = report.get("Metadata")
    if not isinstance(metadata, dict):
        return failures + ["missing image scan metadata"]
    os_info = metadata.get("OS")
    if (
        not isinstance(os_info, dict)
        or os_info.get("Family") != "wolfi"
        or not os_info.get("Name")
        or os_info.get("EOSL")
    ):
        failures.append("unsupported/missing/EOL Wolfi OS detection")
    config = metadata.get("ImageConfig")
    if (
        not isinstance(config, dict)
        or config.get("os") != "linux"
        or config.get("architecture") != architecture
        or config.get("rootfs") != {"type": "layers", "diff_ids": diff_ids}
        or metadata.get("ImageID") != identity.get("ConfigDigest")
        or metadata.get("DiffIDs") != diff_ids
    ):
        failures.append("scan image config/platform/rootfs substitution")
    if _has_suppression(report):
        failures.append("suppressed/modified/VEX findings are forbidden")
    results = report.get("Results")
    if (
        not isinstance(results, list)
        or not results
        or any(not isinstance(result, dict) for result in results)
    ):
        return failures + ["missing/malformed scan results"]
    os_results = [
        r for r in results if r.get("Class") == "os-pkgs" and r.get("Type") == "wolfi"
    ]
    if len(os_results) != 1:
        failures.append("missing/ambiguous Wolfi APK scan")
    else:
        packages = os_results[0].get("Packages")
        if not isinstance(packages, list) or len(packages) < MIN_OS_PACKAGES:
            failures.append(
                f"complete OS inventory requires at least {MIN_OS_PACKAGES} packages"
            )
        if not isinstance(packages, list):
            packages = []
        inventory = {}
        expected_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(architecture)
        for package in packages:
            if (
                not isinstance(package, dict)
                or not isinstance(package.get("Name"), str)
                or not package.get("Name")
                or not isinstance(package.get("Version"), str)
                or not package.get("Version")
                or package.get("AnalyzedBy") != "apk"
                or not isinstance(package.get("Arch"), str)
                or package.get("Arch") not in {expected_arch, "noarch"}
            ):
                failures.append(
                    "malformed/wrong-architecture/non-APK package inventory"
                )
                continue
            name = package["Name"]
            if name in inventory:
                failures.append("duplicate OS package inventory")
            inventory[name] = package["Version"]
        for name, floor in PACKAGE_FLOORS.items():
            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)-r\d+", inventory.get(name, ""))
            if match is None or tuple(map(int, match.groups())) < floor:
                failures.append(f"missing/outdated expected native package {name}")
    python_results = [
        r
        for r in results
        if r.get("Class") == "lang-pkgs" and r.get("Type") == "python-pkg"
    ]
    if not python_results or any(
        not isinstance(r.get("Packages"), list) or not r["Packages"]
        for r in python_results
    ):
        failures.append("missing Python wheel/package detection inventory")
    for result in python_results:
        packages = result.get("Packages")
        if isinstance(packages, list) and any(
            not isinstance(package, dict)
            or not package.get("Name")
            or not package.get("Version")
            or package.get("AnalyzedBy") != "python-pkg"
            for package in packages
        ):
            failures.append("malformed Python package detection inventory")
    for result in results:
        findings = result.get("Vulnerabilities", [])
        if not isinstance(findings, list):
            failures.append("malformed vulnerability findings")
            continue
        for finding in findings:
            if (
                not isinstance(finding, dict)
                or not finding.get("VulnerabilityID")
                or not finding.get("PkgName")
                or not finding.get("InstalledVersion")
                or not isinstance(finding.get("Severity"), str)
                or finding.get("Severity") not in SEVERITIES
            ):
                failures.append("malformed vulnerability finding")
            elif finding["Severity"] in {"HIGH", "CRITICAL"}:
                failures.append(
                    f"{finding['Severity']} {finding['VulnerabilityID']} in {finding['PkgName']}"
                )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--scanner-identity", type=Path, required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    args = parser.parse_args()
    try:
        failures = check_report(
            json.loads(args.report.read_text()),
            json.loads(args.identity.read_text()),
            json.loads(args.scanner_identity.read_text()),
            args.architecture,
        )
    except (OSError, ValueError) as exc:
        failures = [f"unreadable/malformed scan evidence: {exc}"]
    if failures:
        for failure in failures:
            print(f"SCAN DENIED: {failure}", file=sys.stderr)
        return 1
    print(
        "Complete unsuppressed native scan evidence verified; no HIGH/CRITICAL findings"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
