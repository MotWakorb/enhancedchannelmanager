#!/usr/bin/env python3
"""Fail closed on incomplete, substituted or suppressed native image scan evidence.

This validates Trivy's complete JSON output, not a severity-filtered table. The
separate HIGH/CRITICAL exit-1 scan remains a publication gate. Reviewed P1
advisories also deny promotion regardless of the scanner's severity label.
Package floors and inventory are detection sanity checks, never substitutes
for advisory review.

Stable Alpine/APK reports require an exact live query/database inventory bound
to the same immutable image; historical openSUSE/RPM reports retain their own
contract. Native FFmpeg/libxml2 and official-base CPython source/live bindings
remain separate runtime-gate conditions, never inferred from an OS scan or owned
pseudo-version. Accepting this report alone never authorizes publication.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
# Primary-advisory assessments and owners: docs/security/p1-image-delivery-holds.md.
# Enforced by test_recorded_p1_advisories_deny_promotion_without_a_high_ca_finding
# and test_reviewed_p1_hold_is_independent_of_scanner_label_or_fix_availability.
REVIEWED_P1_ADVISORIES = frozenset(
    {
        "openSUSE-SU-2026:12232-1",  # Brotli
        "CVE-2026-111497",
        "openSUSE-SU-2026:12233-1",  # Mozilla CA
        "CVE-2026-114406",
        "openSUSE-SU-2026:12364-1",  # librsvg
        "CVE-2026-96889",
        "openSUSE-SU-2026:12336-1",  # Speex
        "CVE-2026-116741",
    }
)
# Actual 20261105 images: local AMD64 70781eac has 211 rows; ARM run 42426895390
# has 210. Each complete scan matches its delivered/live RPM source index. Only
# liblcms2-2 disappeared from the prior graphs; libnuma1 remains AMD-only.
# Recorded projections: cohort-20261105-{amd64,arm64}-inventory.json.
MIN_OS_PACKAGES = 211
MIN_OS_PACKAGES_BY_ARCH = {"amd64": MIN_OS_PACKAGES, "arm64": 210}
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
# Stable Alpine detection baselines from the paired 122-row real candidates,
# not RPM version-equivalence claims or advisory waivers. Source-built libxml2,
# FFmpeg and CPython are verified by their independent source/live-file gates.
ALPINE_PACKAGE_FLOORS = {
    "aom-libs": (3, 14, 1),
    "fontconfig": (2, 17, 1),
    "freetype": (2, 14, 3),
    "harfbuzz": (13, 2, 1),
    "lame-libs": (3, 100),
    "libass": (0, 17, 4),
    "libcrypto3": (3, 5, 8),
    "libssl3": (3, 5, 8),
    "libdav1d": (1, 5, 3),
    "libexpat": (2, 10, 0),
    "libuuid": (2, 42, 3),
    "libvorbis": (1, 3, 7),
    "libvpx": (1, 15, 2),
    "libwebp": (1, 6, 0),
    "musl": (1, 2, 6),
    "musl-utils": (1, 2, 6),
    "openjpeg": (2, 5, 4),
    "opus": (1, 6, 1),
    "x264-libs": (0, 164, 3108),
    "x265-libs": (4, 1),
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


def validate_alpine_inventory(
    result: dict, capture: object, architecture: str
) -> list[str]:
    """Require every actual APK row, including the validated Python virtual row."""
    from alpine_source_inventory import InventoryError, source_plan

    if not isinstance(capture, dict) or architecture not in {"amd64", "arm64"}:
        return ["complete live Alpine query/database inventory is required"]
    try:
        machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
        plan = source_plan(capture, machine)
        expected = {}
        for row in plan["packages"]:
            fields = row["fields"]
            checksum = fields["C"]
            if not checksum.startswith("Q1"):
                return ["reviewed APK v2 package checksum required"]
            expected[row["name"]] = {
                "Name": row["name"],
                "Version": row["version"],
                "Arch": row["arch"],
                "Digest": "sha1:" + base64.b64decode(checksum[2:], validate=True).hex(),
                "AnalyzedBy": "apk",
            }
            if row["name"] != ".python-rundeps":
                expected[row["name"]].update(
                    SrcName=fields["o"], SrcVersion=row["version"]
                )
        packages = result.get("Packages")
        if not isinstance(packages, list) or not packages:
            return ["complete Alpine APK scan inventory is required"]
        observed = {}
        for row in packages:
            if not isinstance(row, dict) or row.get("Name") not in expected:
                return ["substituted or malformed Alpine APK scan package"]
            name = row["Name"]
            if name in observed:
                return ["duplicate Alpine APK scan package"]
            observed[name] = {field: row.get(field) for field in expected[name]}
        if observed != expected:
            return [
                "complete Alpine scan/query/database identity or source/checksum mismatch"
            ]
    except (InventoryError, KeyError, TypeError, ValueError) as exc:
        return [f"malformed Alpine query/database inventory: {exc}"]
    return []


def check_report(
    report: object,
    identity: object,
    scanner: object,
    architecture: str,
    runtime_inventory: object = None,
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
    alpine = isinstance(os_info, dict) and os_info.get("Family") == "alpine"
    if (
        not isinstance(os_info, dict)
        or os_info.get("Family") not in ("opensuse-tumbleweed", "alpine")
        or not os_info.get("Name")
        or alpine
        and re.fullmatch(r"3\.24\.\d+", str(os_info.get("Name"))) is None
        or os_info.get("EOSL")
    ):
        failures.append(
            "unsupported/missing/EOL openSUSE Tumbleweed or stable Alpine OS detection"
        )
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
    if alpine:
        if len(os_results) != 1 or os_results[0].get("Type") != "alpine":
            failures.append("missing/ambiguous Alpine APK scan")
        elif (
            not isinstance(runtime_inventory, dict)
            or runtime_inventory.get("ConfigDigest") != identity.get("ConfigDigest")
            or runtime_inventory.get("RootFSDiffIDs") != diff_ids
            or runtime_inventory.get("Architecture") != architecture
        ):
            failures.append(
                "missing/substituted immutable Alpine live inventory binding"
            )
        else:
            capture = runtime_inventory.get("capture")
            failures.extend(
                validate_alpine_inventory(os_results[0], capture, architecture)
            )
            apk_packages = os_results[0].get("Packages")
            if not isinstance(apk_packages, list):
                apk_packages = []
            names = {
                p["Name"]
                for p in apk_packages
                if isinstance(p, dict) and isinstance(p.get("Name"), str)
            }
            if (
                not {
                    "bash",
                    "coreutils",
                    "shadow",
                    "ca-certificates",
                    "musl",
                    "libcrypto3",
                    "libssl3",
                    "ecm-owned-native",
                    "busybox",
                    "busybox-binsh",
                    "ssl_client",
                }
                <= names
            ):
                failures.append("missing required Alpine ECM runtime providers")
            installed = {
                p.get("Name"): p.get("Version")
                for p in apk_packages
                if isinstance(p, dict) and isinstance(p.get("Name"), str)
            }
            for name, floor in ALPINE_PACKAGE_FLOORS.items():
                match = re.fullmatch(
                    r"(\d+(?:\.\d+)+)-r(\d+)", str(installed.get(name))
                )
                if match is None or tuple(map(int, match[1].split("."))) < floor:
                    failures.append(f"missing/outdated expected Alpine package {name}")
                elif (
                    name in {"musl", "musl-utils"}
                    and match[1] == "1.2.6"
                    and int(match[2]) < 3
                ):
                    failures.append(
                        f"missing vendor-fixed Alpine musl revision for {name}"
                    )
                elif name == "libuuid" and match[1] == "2.42.3" and int(match[2]) < 1:
                    failures.append("missing vendor-fixed Alpine libuuid revision")
    elif len(os_results) != 1 or os_results[0].get("Type") != "opensuse-tumbleweed":
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
                or any(
                    not isinstance(finding.get(field), str) or not finding[field]
                    for field in ("VulnerabilityID", "PkgName", "InstalledVersion")
                )
                or not isinstance(finding.get("Severity"), str)
                or finding.get("Severity") not in SEVERITIES
            ):
                failures.append("malformed vulnerability finding")
            else:
                if finding["Severity"] in {"HIGH", "CRITICAL"}:
                    failures.append(
                        f"{finding['Severity']} {finding['VulnerabilityID']} in {finding['PkgName']}"
                    )
                if finding["VulnerabilityID"] in REVIEWED_P1_ADVISORIES:
                    failures.append(
                        f"reviewed P1 advisory {finding['VulnerabilityID']} in "
                        f"{finding['PkgName']} (reported {finding['Severity']})"
                    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--scanner-identity", type=Path, required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--runtime-inventory", type=Path)
    args = parser.parse_args()
    try:
        failures = check_report(
            json.loads(args.report.read_text()),
            json.loads(args.identity.read_text()),
            json.loads(args.scanner_identity.read_text()),
            args.architecture,
            json.loads(args.runtime_inventory.read_text())
            if args.runtime_inventory
            else None,
        )
    except (OSError, ValueError) as exc:
        failures = [f"unreadable/malformed scan evidence: {exc}"]
    if failures:
        for failure in failures:
            print(f"SCAN DENIED: {failure}", file=sys.stderr)
        return 1
    print(
        "Complete unsuppressed native OS/Python/Go scan evidence verified; "
        "no HIGH/CRITICAL or reviewed P1 findings. Custom native source/build binding "
        "requires the separate runtime gate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
