"""Fail-closed scan contract against the real SUSE/RPM report projection.

Fixture digests and scanner DB times are unit-only, not publication evidence.
FFmpeg source/build identity remains the parent runtime gate's responsibility.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_native_image_scan.py"
FIXTURE = ROOT / "backend/tests/fixtures/native_scan/opensuse-tumbleweed.json"
CONFIG = "sha256:" + "a" * 64
DIFF = "sha256:" + "b" * 64
# Independently reviewed identities, not read from the validator's constants.
NATIVE_PACKAGES = (
    "python312",
    "python312-base",
    "libpython3_12-1_0",
    "libxml2-16",
    "glibc",
    "libopenssl3",
    "libexpat1",
    "libaom3",
    "libass9",
    "libdav1d7",
    "libSvtAv1Enc4",
    "libfontconfig1",
    "libfreetype6",
    "libharfbuzz0",
    "libmp3lame0",
    "libopenjp2-7",
    "libopus0",
    "libvorbis0",
    "libvorbisenc2",
    "libvpx12",
    "libwebp7",
    "libx264-165",
    "libx265-215",
)


@pytest.fixture
def scan():
    spec = importlib.util.spec_from_file_location("native_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evidence(arch="amd64"):
    report = json.loads(FIXTURE.read_text())
    identity = {
        "ManifestDigest": "sha256:" + "c" * 64,
        "ConfigDigest": CONFIG,
        "OS": "linux",
        "Architecture": arch,
        "RootFSDiffIDs": [DIFF],
    }
    if arch == "arm64":
        # Synthetic architecture mapping, NOT recorded arm64 image evidence.
        report["Metadata"]["ImageConfig"]["architecture"] = arch
        for package in report["Results"][0]["Packages"]:
            if package["Arch"] == "x86_64":
                package["Arch"] = "aarch64"
    scanner = {
        "Version": "0.76.1",
        "VulnerabilityDB": {
            "Version": 2,
            "UpdatedAt": "2026-10-24T00:00:00Z",
            "NextUpdate": "2026-10-25T00:00:00Z",
            "DownloadedAt": "2026-10-24T01:00:00Z",
        },
    }
    return report, identity, scanner


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_complete_recorded_inventory_and_unsuppressed_findings_are_accepted(scan, arch):
    report, identity, scanner = _evidence(arch)
    os_result, python_result, go_result = report["Results"]
    packages = os_result["Packages"]
    assert [len(r["Packages"]) for r in report["Results"]] == [212, 74, 4]
    assert Counter(p["AnalyzedBy"] for p in packages) == {"rpm": 212}
    assert Counter(p["Arch"] for p in packages) == {
        "x86_64" if arch == "amd64" else "aarch64": 189,
        "noarch": 17,
        "None": 6,
    }
    keys = [p for p in packages if p["Name"] == "gpg-pubkey"]
    assert len(keys) == len({(p["Version"], p["Release"]) for p in keys}) == 6
    assert python_result["Vulnerabilities"][0]["Severity"] == "MEDIUM"
    assert go_result["Vulnerabilities"][0]["Severity"] == "UNKNOWN"
    # Custom FFmpeg is not RPM-owned; this does NOT prove its source/build pin.
    assert not any(p["Name"].startswith("ffmpeg") for p in packages)
    assert scan.MIN_OS_PACKAGES == 212
    assert scan.check_report(report, identity, scanner, arch) == []


@pytest.mark.parametrize("name", NATIVE_PACKAGES)
def test_missing_native_identity_cannot_be_hidden_by_inventory_padding(scan, name):
    report, identity, scanner = _evidence()
    package = next(p for p in report["Results"][0]["Packages"] if p["Name"] == name)
    package["Name"] = "unrelated-replacement"
    assert f"missing/outdated expected native package {name}" in scan.check_report(
        report, identity, scanner, "amd64"
    )


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("python312", "3.12.14"),
        ("python312-base", "3.12.14"),
        ("libpython3_12-1_0", "3.12.14"),
        ("python312", "3.13.15"),
        ("libxml2-16", "2.15.3"),
        ("libxml2-16", "2.15.4~rc1"),
        ("libxml2-16", "2.15.4-r1"),
        ("glibc", "2.43"),
        ("libopenssl3", "3.5.2"),
        ("libexpat1", "2.8.0"),
        ("libaom3", "3.15.1"),
        ("libdav1d7", "1.5.3"),
        ("libSvtAv1Enc4", "4.1.0"),
        ("libfreetype6", "2.14.0"),
        ("libvpx12", "1.16.0"),
        ("libwebp7", "1.5.0"),
    ],
)
def test_expected_numeric_version_floors_do_not_accept_old_or_foreign_versions(
    scan, name, version
):
    report, identity, scanner = _evidence()
    next(p for p in report["Results"][0]["Packages"] if p["Name"] == name)[
        "Version"
    ] = version
    assert f"missing/outdated expected native package {name}" in scan.check_report(
        report, identity, scanner, "amd64"
    )


@pytest.mark.parametrize("name", ["python312", "libxml2-16"])
def test_later_fixed_patch_versions_are_not_exact_version_pins(scan, name):
    report, identity, scanner = _evidence()
    next(p for p in report["Results"][0]["Packages"] if p["Name"] == name)[
        "Version"
    ] = "3.12.16" if name == "python312" else "2.15.5"
    assert scan.check_report(report, identity, scanner, "amd64") == []


def test_old_native_version_is_not_hidden_by_a_second_same_name_rpm(scan):
    report, identity, scanner = _evidence()
    packages = report["Results"][0]["Packages"]
    current = next(p for p in packages if p["Name"] == "libxml2-16")
    old = copy.deepcopy(current)
    old["Version"] = "2.15.3"
    packages.insert(0, old)
    assert "missing/outdated expected native package libxml2-16" in scan.check_report(
        report, identity, scanner, "amd64"
    )


def test_rpm_release_is_part_of_composite_identity(scan):
    report, identity, scanner = _evidence()
    keys = [p for p in report["Results"][0]["Packages"] if p["Name"] == "gpg-pubkey"]
    # Synthetic same-version/different-release mutant of the recorded key rows.
    keys[1]["Version"] = keys[0]["Version"]
    assert keys[1]["Release"] != keys[0]["Release"]
    assert scan.check_report(report, identity, scanner, "amd64") == []


def test_high_finding_in_an_additional_analyzer_cannot_be_ignored(scan):
    report, identity, scanner = _evidence()
    extra = copy.deepcopy(report["Results"][2])
    extra["Type"] = "additional-analyzer"
    extra["Vulnerabilities"][0]["Severity"] = "HIGH"
    report["Results"].append(extra)
    finding = extra["Vulnerabilities"][0]
    assert (
        f"HIGH {finding['VulnerabilityID']} in {finding['PkgName']}"
        in scan.check_report(report, identity, scanner, "amd64")
    )


@pytest.mark.parametrize("result_index", [0, 1, 2], ids=["rpm", "python", "go"])
@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
@pytest.mark.parametrize(
    "status", ["fixed", "affected", "will_not_fix", "not_affected"]
)
def test_any_high_or_critical_fails_even_unfixed_or_marked_not_affected(
    scan, result_index, severity, status
):
    report, identity, scanner = _evidence()
    finding = copy.deepcopy(report["Results"][2]["Vulnerabilities"][0])
    finding.update(Severity=severity, Status=status, FixedVersion="")
    report["Results"][result_index]["Vulnerabilities"] = [finding]
    assert (
        f"{severity} {finding['VulnerabilityID']} in {finding['PkgName']}"
        in scan.check_report(report, identity, scanner, "amd64")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "null",
        "results",
        "unsupported",
        "wrongtype",
        "eosl",
        "wrongarch",
        "packagearch",
        "config",
        "diffids",
        "rootfs",
        "inventory",
        "duplicatepkgs",
        "duplicategpg",
        "nonrpm",
        "emptyanalysis",
        "emptyrelease",
        "nopython",
        "nogo",
        "suppressed",
        "vex",
        "malformedfinding",
        "malformedpkgs",
        "malformedmetadata",
        "malformedresult",
        "wrongscanner",
        "missingdb",
        "missingdbtimestamp",
        "dbversion",
        "missingtimestamp",
        "schema",
        "invalidseverity",
        "invalidpackagearch",
        "invalidfinding",
        "invalidpythonpackage",
        "invalidgopackage",
        "native-noarch",
        "native-none",
        "gpg-null",
        "gpg-missingarch",
        "gpg-arch",
        "identitymanifest",
        "identityconfig",
        "identityrootfs",
        "identityplatform",
        "ambiguousos",
    ],
)
def test_scan_fails_closed_on_incomplete_substituted_or_unsafe_evidence(scan, mutation):
    report, identity, scanner = _evidence()
    original = copy.deepcopy((report, identity, scanner))
    os_result, python_result, go_result = report["Results"]
    packages = os_result["Packages"]
    if mutation == "empty":
        report = {}
    elif mutation == "null":
        report = None
    elif mutation == "results":
        report["Results"] = []
    elif mutation == "unsupported":
        report["Metadata"]["OS"]["Family"] = "wolfi"
    elif mutation == "wrongtype":
        os_result["Type"] = "wolfi"
    elif mutation == "eosl":
        report["Metadata"]["OS"]["EOSL"] = True
    elif mutation == "wrongarch":
        report["Metadata"]["ImageConfig"]["architecture"] = "arm64"
    elif mutation == "packagearch":
        packages[0]["Arch"] = "aarch64"
    elif mutation == "config":
        report["Metadata"]["ImageID"] = "sha256:" + "0" * 64
    elif mutation == "diffids":
        report["Metadata"]["DiffIDs"] = []
    elif mutation == "rootfs":
        report["Metadata"]["ImageConfig"]["rootfs"]["diff_ids"] = []
    elif mutation == "inventory":
        packages.pop()
    elif mutation == "duplicatepkgs":
        packages[-1] = copy.deepcopy(packages[0])
    elif mutation == "duplicategpg":
        keys = [p for p in packages if p["Name"] == "gpg-pubkey"]
        keys[1].update(keys[0])
    elif mutation in {"nonrpm", "emptyanalysis"}:
        packages[0]["AnalyzedBy"] = "apk" if mutation == "nonrpm" else ""
    elif mutation == "emptyrelease":
        packages[0]["Release"] = ""
    elif mutation == "nopython":
        python_result["Packages"] = []
    elif mutation == "nogo":
        report["Results"].remove(go_result)
    elif mutation == "suppressed":
        os_result["ExperimentalModifiedFindings"] = [
            {"VulnerabilityID": "hidden", "Status": "not_affected"}
        ]
    elif mutation == "vex":
        report["Metadata"]["VEX"] = {"status": "not_affected"}
    elif mutation == "malformedfinding":
        python_result["Vulnerabilities"] = "no findings"
    elif mutation == "malformedpkgs":
        os_result["Packages"] = [None] * 212
    elif mutation == "malformedmetadata":
        report["Metadata"] = None
    elif mutation == "malformedresult":
        report["Results"] = [None]
    elif mutation == "invalidseverity":
        python_result["Vulnerabilities"][0]["Severity"] = []
    elif mutation == "invalidpackagearch":
        packages[0]["Arch"] = []
    elif mutation == "invalidfinding":
        python_result["Vulnerabilities"][0] = None
    elif mutation == "invalidpythonpackage":
        python_result["Packages"] = [None]
    elif mutation == "invalidgopackage":
        go_result["Packages"] = [None]
    elif mutation == "wrongscanner":
        scanner["Version"] = "0.1.0"
    elif mutation == "missingdb":
        del scanner["VulnerabilityDB"]
    elif mutation == "missingdbtimestamp":
        del scanner["VulnerabilityDB"]["UpdatedAt"]
    elif mutation == "dbversion":
        scanner["VulnerabilityDB"]["Version"] = 0
    elif mutation == "missingtimestamp":
        del report["CreatedAt"]
    elif mutation == "schema":
        report["SchemaVersion"] = 0
    elif mutation in {"native-noarch", "native-none"}:
        next(p for p in packages if p["Name"] == "libxml2-16")["Arch"] = (
            "noarch" if mutation == "native-noarch" else "None"
        )
    elif mutation.startswith("gpg-"):
        key = next(p for p in packages if p["Name"] == "gpg-pubkey")
        if mutation == "gpg-missingarch":
            del key["Arch"]
        else:
            key["Arch"] = None if mutation == "gpg-null" else "x86_64"
    elif mutation == "identitymanifest":
        identity["ManifestDigest"] = "not-a-digest"
    elif mutation == "identityconfig":
        identity["ConfigDigest"] = "not-a-digest"
    elif mutation == "identityrootfs":
        identity["RootFSDiffIDs"] = []
    elif mutation == "identityplatform":
        identity["OS"] = "windows"
    elif mutation == "ambiguousos":
        report["Results"].append(copy.deepcopy(os_result))
    assert (report, identity, scanner) != original
    assert scan.check_report(report, identity, scanner, "amd64")


def test_scan_cli_dogfoods_evidence_and_denies_empty_malformed_or_missing_files(
    tmp_path,
):
    report, identity, scanner = _evidence()
    paths = [
        tmp_path / name for name in ("report.json", "identity.json", "scanner.json")
    ]
    for path, document in zip(paths, (report, identity, scanner)):
        path.write_text(json.dumps(document))
    command = [
        sys.executable,
        str(SCRIPT),
        "--report",
        str(paths[0]),
        "--identity",
        str(paths[1]),
        "--scanner-identity",
        str(paths[2]),
        "--architecture",
        "amd64",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for contents in ("", "{", "null", "{}", "[]"):
        paths[0].write_text(contents)
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        assert result.returncode == 1
        assert "SCAN DENIED" in result.stderr
    paths[0].unlink()
    assert subprocess.run(command, check=False, capture_output=True).returncode == 1
