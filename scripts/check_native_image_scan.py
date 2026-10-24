#!/usr/bin/env python3
"""Fail closed on incomplete, substituted or suppressed native image scan evidence.

This validates Trivy's complete JSON output, not a severity-filtered table. The
separate HIGH/CRITICAL exit-1 scan remains a publication gate. Package floors and
inventory are detection sanity checks, never substitutes for advisory review.

The supported OS report is openSUSE Tumbleweed/RPM. Custom FFmpeg 8 is not
RPM-owned and is NOT covered by this OS scan. Its exact source/build pin and
required-owned-artifact/source binding are separate parent runtime-gate
conditions; accepting this report alone never authorizes publication.
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
# Recorded amd64 integration report: 212 RPM entries, including six distinct keys.
# Native ARM CI has 211: its complete graph lacks only amd64's libnuma1 provider.
# Keep architecture-specific observed floors, not a globally reduced baseline.
MIN_OS_PACKAGES = 212
MIN_OS_PACKAGES_BY_ARCH = {"amd64": MIN_OS_PACKAGES, "arm64": 211}
# Reviewed upstream-version sanity floors from RPM Version fields, not an RPM
# EVR/advisory solver. Release fields remain separate; source-built FFmpeg has
# no RPM floor here and must satisfy the runtime gate's exact source/build pin.
PACKAGE_FLOORS = {
    "python312": (3, 12, 15),
    "python312-base": (3, 12, 15),
    "libpython3_12-1_0": (3, 12, 15),
    "libxml2-16": (2, 15, 4),
    "glibc": (2, 44),
    "libopenssl3": (3, 5, 3),
    "libexpat1": (2, 9, 0),
    "libaom3": (3, 15, 2),
    "libdav1d7": (1, 5, 4),
    "libSvtAv1Enc4": (4, 2, 0),
    "libfreetype6": (2, 15, 0),
    "libvpx12": (1, 17, 0),
    "libwebp7": (1, 6, 0),
}
REQUIRED_NATIVE_PACKAGES = set(PACKAGE_FLOORS) | {
    "libass9",
    "libfontconfig1",
    "libharfbuzz0",
    "libmp3lame0",
    "libopenjp2-7",
    "libopus0",
    "libvorbis0",
    "libvorbisenc2",
    "libx264-165",
    "libx265-215",
}


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
        or os_info.get("Family") != "opensuse-tumbleweed"
        or not os_info.get("Name")
        or os_info.get("EOSL")
    ):
        failures.append("unsupported/missing/EOL openSUSE Tumbleweed OS detection")
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
    os_results = [r for r in results if r.get("Class") == "os-pkgs"]
    if len(os_results) != 1 or os_results[0].get("Type") != "opensuse-tumbleweed":
        failures.append("missing/ambiguous openSUSE Tumbleweed RPM scan")
    else:
        packages = os_results[0].get("Packages")
        minimum = MIN_OS_PACKAGES_BY_ARCH.get(architecture, MIN_OS_PACKAGES)
        if not isinstance(packages, list) or len(packages) < minimum:
            failures.append(
                f"complete OS inventory requires at least {minimum} packages"
            )
        if not isinstance(packages, list):
            packages = []
        inventory = {}
        seen = set()
        expected_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(architecture)
        for package in packages:
            if (
                not isinstance(package, dict)
                or not isinstance(package.get("Name"), str)
                or not package.get("Name")
                or not isinstance(package.get("Version"), str)
                or not package.get("Version")
                or not isinstance(package.get("Release"), str)
                or not package.get("Release")
                or package.get("AnalyzedBy") != "rpm"
                or not isinstance(package.get("Arch"), str)
                or (
                    package["Arch"] != "None"
                    if package["Name"] == "gpg-pubkey"
                    else package["Arch"] not in {expected_arch, "noarch"}
                )
            ):
                failures.append(
                    "malformed/wrong-architecture/non-RPM package inventory"
                )
                continue
            name = package["Name"]
            # Trivy records RPM gpg-pubkey architecture as the STRING "None".
            # It is not a blanket architecture exemption for native libraries.
            if name == "gpg-pubkey" and not all(
                re.fullmatch(r"[0-9a-f]{8}", package[field])
                for field in ("Version", "Release")
            ):
                failures.append("malformed RPM public-key inventory")
            # Same-name key rows are legitimate; only repeated RPM composite
            # identities are duplicates. Version and Release are separate fields.
            key = tuple(
                package[field] for field in ("Name", "Version", "Release", "Arch")
            )
            if key in seen:
                failures.append("duplicate RPM composite package inventory")
            seen.add(key)
            inventory.setdefault(name, []).append(package)
        for name in sorted(REQUIRED_NATIVE_PACKAGES):
            installed = inventory.get(name, [])
            valid = bool(installed)
            for package in installed:
                valid &= package["Arch"] == expected_arch
                if name in PACKAGE_FLOORS:
                    match = re.fullmatch(r"\d+(?:\.\d+)+", package["Version"])
                    version = tuple(map(int, match[0].split("."))) if match else ()
                    valid &= version >= PACKAGE_FLOORS[name]
                    if name in {"python312", "python312-base", "libpython3_12-1_0"}:
                        valid &= version[:2] == (3, 12)
            if not valid:
                failures.append(f"missing/outdated expected native package {name}")
    for kind, label in (("python-pkg", "Python"), ("gobinary", "Go")):
        language_results = [
            r
            for r in results
            if r.get("Class") == "lang-pkgs" and r.get("Type") == kind
        ]
        if not language_results or any(
            not isinstance(r.get("Packages"), list) or not r["Packages"]
            for r in language_results
        ):
            failures.append(f"missing {label} package detection inventory")
        for result in language_results:
            packages = result.get("Packages")
            if isinstance(packages, list) and any(
                not isinstance(package, dict)
                or not isinstance(package.get("Name"), str)
                or not package["Name"]
                or (
                    not (isinstance(package.get("Version"), str) and package["Version"])
                    # The recorded gosu root module has no Version field.
                    and not (
                        kind == "gobinary" and package.get("Relationship") == "root"
                    )
                )
                or package.get("AnalyzedBy") != kind
                for package in packages
            ):
                failures.append(f"malformed {label} package detection inventory")
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
        "Complete unsuppressed RPM/Python/Go scan evidence verified; "
        "no HIGH/CRITICAL findings. Custom native source/build binding "
        "requires the separate runtime gate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
