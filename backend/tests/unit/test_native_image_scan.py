"""Complete scan evidence is required; a scanner failure is never zero findings.

The compact OS inventory and report vocabulary below are recorded from the
2026-10-23 Wolfi probe scan (Trivy 0.76.1), not a guessed distro/package schema.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_native_image_scan.py"
CONFIG = "sha256:" + "a" * 64
DIFF = "sha256:" + "b" * 64
OS_INVENTORY = """
aom-libs=3.15.2-r0 apk-tools=2.14.10-r17 bash=5.3-r14 busybox=1.38.0-r3
ca-certificates-bundle=20260909-r3 cairo=1.18.6-r0 coreutils=9.12-r4
ffmpeg-9.0=9.0.2-r5 ffmpeg-9.0-libavcodec63=9.0.2-r5 ffmpeg-9.0-libavdevice63=9.0.2-r5
ffmpeg-9.0-libavfilter12=9.0.2-r5 ffmpeg-9.0-libavformat63=9.0.2-r5 ffmpeg-9.0-libavutil61=9.0.2-r5
ffmpeg-9.0-libswresample7=9.0.2-r5 ffmpeg-9.0-libswscale10=9.0.2-r5
fontconfig-config=2.18.3-r2 freetype=2.14.3-r7 gdbm=1.26-r7 glib=2.90.1-r0
glibc-2.44=2.44-r9 glibc-2.44-locale-posix=2.44-r9 gosu=1.19-r20 graphite2=1.3.15-r4
harfbuzz=15.0.0-r0 lame-libs=4.0-r2 ld-linux-2.44=2.44-r9 libacl1=2.4.0-r4
libattr1=2.6.0-r4 libaudit=4.2.1-r2 libblkid=2.42.4-r1 libbrotlicommon1=1.2.1-r0
libbrotlidec1=1.2.1-r0 libbrotlienc1=1.2.1-r0 libbsd=0.12.2-r9 libbz2-1=1.0.8-r25
libcap-ng=0.9.6-r1 libcrypt1-2.44=2.44-r9 libdav1d=1.5.4-r2 libexpat1=2.9.0-r0
libffi=3.8.0-r2 libfontconfig1=2.18.3-r2 libgcc=16.2.0-r1 libgomp=16.2.0-r1 libmd=1.3.0-r0
libmount=2.42.4-r1 libogg=1.3.6-r5 libpcre2-8-0=10.49-r1 libpng=1.6.59-r0
libsdl2=2.32.10-r5 libselinux=3.11-r2 libsemanage=3.11-r3 libsepol=3.11-r3
libsodium=1.0.22-r2 libsrt=1.5.7-r4 libssh=0.12.2-r3 libstdc++=16.2.0-r1
libsvtav1enc4=4.2.0-r2 libtheora=1.2.0-r8 libuuid=2.42.4-r1 libvorbis=1.3.7-r12
libvpx=1.17.0-r2 libwebp=1.6.0-r7 libx11=1.8.14-r1 libx265-216=4.2-r2
libxau=1.0.12-r9 libxcb=1.17.0-r17 libxcrypt=4.5.2-r6 libxdmcp=1.1.5-r12
libxext=1.3.7-r4 libxml2-16=2.15.4-r1 libxrender=0.9.13-r0 linux-pam=1.7.3-r1
mpdecimal=4.0.1-r4 ncurses=6.6.20261017-r0 ncurses-terminfo-base=6.6.20261017-r0
opencore-amr=0.1.6-r6 openjpeg=2.5.4-r3 openssl-4.0-dev=4.0.3-r8
openssl-4.0-libcrypto=4.0.3-r8 openssl-4.0-libssl=4.0.3-r8
openssl-provider-legacy-allowed=4.0.3-r1 opus=1.6.1-r5 pixman=0.46.4-r6
posix-libc-utils-2.44=2.44-r9 posix-libc-utils-bin-2.44=2.44-r9 py3-pip-wheel=26.2.2-r0
python-3.12=3.12.15-r6 python-3.12-base=3.12.15-r6 readline=8.3-r3 shadow=4.20.3-r0
snappy=1.3.1-r0 soxr=0.1.3-r8 speex=1.2.1-r8 sqlite-libs=3.54.1-r0 tzdata=2026e-r0
wolfi-base=1-r7 wolfi-baselayout=20230201-r30 wolfi-keys=1-r14 x264=2025.06.08-r4
xz=5.8.4-r0 zeromq=4.3.5-r13 zlib=1.3.2.1_rc20260921-r0
"""


@pytest.fixture
def scan():
    spec = importlib.util.spec_from_file_location("native_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evidence(arch="amd64"):
    identity = {
        "ManifestDigest": "sha256:" + "c" * 64,
        "ConfigDigest": CONFIG,
        "OS": "linux",
        "Architecture": arch,
        "RootFSDiffIDs": [DIFF],
    }
    report = {
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.76.1"},
        "CreatedAt": "2026-10-23T02:52:02.14650828-05:00",
        "ArtifactType": "container_image",
        "Metadata": {
            "OS": {"Family": "wolfi", "Name": "20230201"},
            "ImageID": CONFIG,
            "DiffIDs": [DIFF],
            "ImageConfig": {
                "os": "linux",
                "architecture": arch,
                "rootfs": {"type": "layers", "diff_ids": [DIFF]},
            },
        },
        "Results": [
            {
                "Class": "os-pkgs",
                "Type": "wolfi",
                "Packages": [
                    {
                        "Name": value.split("=")[0],
                        "Version": value.split("=")[1],
                        "Arch": "x86_64" if arch == "amd64" else "aarch64",
                        "AnalyzedBy": "apk",
                    }
                    for value in OS_INVENTORY.split()
                ],
            },
            {
                "Class": "lang-pkgs",
                "Type": "python-pkg",
                "Packages": [
                    {"Name": "Mako", "Version": "1.4.1", "AnalyzedBy": "python-pkg"}
                ],
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-fixture-medium",
                        "PkgName": "Mako",
                        "InstalledVersion": "1.4.1",
                        "Severity": "MEDIUM",
                    }
                ],
            },
        ],
    }
    scanner = {
        "Version": "0.76.1",
        "VulnerabilityDB": {
            "Version": 2,
            "UpdatedAt": "2026-10-23T00:00:00Z",
            "NextUpdate": "2026-10-24T00:00:00Z",
            "DownloadedAt": "2026-10-23T01:00:00Z",
        },
    }
    return report, identity, scanner


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_complete_recorded_inventory_and_medium_finding_are_accepted(scan, arch):
    report, identity, scanner = _evidence(arch)
    assert len(report["Results"][0]["Packages"]) == 102
    assert scan.check_report(report, identity, scanner, arch) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "null",
        "results",
        "unsupported",
        "eosl",
        "wrongarch",
        "packagearch",
        "config",
        "diffids",
        "rootfs",
        "inventory",
        "missingffmpeg",
        "missingxml",
        "oldffmpeg",
        "oldxml",
        "duplicatepkgs",
        "noapk",
        "nopython",
        "high",
        "critical",
        "unfixedhigh",
        "suppressed",
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
    ],
)
def test_scan_fails_closed_on_incomplete_substituted_or_unsafe_evidence(scan, mutation):
    report, identity, scanner = _evidence()
    original = copy.deepcopy((report, identity, scanner))
    os_result, python_result = report["Results"]
    if mutation == "empty":
        report = {}
    elif mutation == "null":
        report = None
    elif mutation == "results":
        report["Results"] = []
    elif mutation == "unsupported":
        report["Metadata"]["OS"]["Family"] = "unknown"
    elif mutation == "eosl":
        report["Metadata"]["OS"]["EOSL"] = True
    elif mutation == "wrongarch":
        report["Metadata"]["ImageConfig"]["architecture"] = "arm64"
    elif mutation == "packagearch":
        os_result["Packages"][0]["Arch"] = "aarch64"
    elif mutation == "config":
        report["Metadata"]["ImageID"] = "sha256:" + "0" * 64
    elif mutation == "diffids":
        report["Metadata"]["DiffIDs"] = []
    elif mutation == "rootfs":
        report["Metadata"]["ImageConfig"]["rootfs"]["diff_ids"] = []
    elif mutation == "inventory":
        os_result["Packages"].pop()
    elif mutation in {"missingffmpeg", "missingxml"}:
        name = "ffmpeg-9.0" if mutation == "missingffmpeg" else "libxml2-16"
        os_result["Packages"] = [p for p in os_result["Packages"] if p["Name"] != name]
        os_result["Packages"].append(
            {
                "Name": "replacement",
                "Version": "1",
                "Arch": "x86_64",
                "AnalyzedBy": "apk",
            }
        )
    elif mutation in {"oldffmpeg", "oldxml"}:
        name = "ffmpeg-9.0" if mutation == "oldffmpeg" else "libxml2-16"
        next(p for p in os_result["Packages"] if p["Name"] == name)["Version"] = (
            "1.0.0-r0"
        )
    elif mutation == "duplicatepkgs":
        os_result["Packages"][-1] = os_result["Packages"][0]
    elif mutation == "noapk":
        os_result["Type"] = "alpine"
    elif mutation == "nopython":
        python_result["Packages"] = []
    elif mutation in {"high", "critical", "unfixedhigh"}:
        finding = python_result["Vulnerabilities"][0]
        finding["Severity"] = "CRITICAL" if mutation == "critical" else "HIGH"
        if mutation == "unfixedhigh":
            finding["Status"] = "will_not_fix"
            finding["FixedVersion"] = ""
    elif mutation == "suppressed":
        os_result["ExperimentalModifiedFindings"] = [
            {"VulnerabilityID": "hidden", "Status": "not_affected"}
        ]
    elif mutation == "malformedfinding":
        python_result["Vulnerabilities"] = "no findings"
    elif mutation == "malformedpkgs":
        os_result["Packages"] = [None] * 102
    elif mutation == "malformedmetadata":
        report["Metadata"] = None
    elif mutation == "malformedresult":
        report["Results"] = [None]
    elif mutation == "invalidseverity":
        python_result["Vulnerabilities"][0]["Severity"] = []
    elif mutation == "invalidpackagearch":
        os_result["Packages"][0]["Arch"] = []
    elif mutation == "invalidfinding":
        python_result["Vulnerabilities"][0] = None
    elif mutation == "invalidpythonpackage":
        python_result["Packages"] = [None]
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
