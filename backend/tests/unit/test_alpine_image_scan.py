"""APK scan contracts against real Trivy output, not invented RPM-shaped rows."""

import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def scan(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_scan_gate", ROOT / "scripts/check_native_image_scan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded():
    return json.loads(
        gzip.decompress(
            (
                ROOT / "backend/tests/fixtures/native_scan/alpine-mcp-20261110.json.gz"
            ).read_bytes()
        )
    )


def test_recorded_apk_versions_arch_checksums_and_complete_virtual_inventory(
    scan, recorded
):
    result = next(r for r in recorded["report"]["Results"] if r["Class"] == "os-pkgs")
    assert len(result["Packages"]) == 38
    assert {p["AnalyzedBy"] for p in result["Packages"]} == {"apk"}
    assert scan.validate_alpine_inventory(result, recorded["capture"], "amd64") == []


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "duplicate",
        "version",
        "arch",
        "checksum",
        "source",
        "analyzer",
        "query",
        "database",
        "virtual-omission",
        "other-architecture",
    ],
)
def test_incomplete_or_substituted_apk_scan_inventory_fails(scan, recorded, defect):
    result = next(r for r in recorded["report"]["Results"] if r["Class"] == "os-pkgs")
    capture = recorded["capture"]
    row = next(p for p in result["Packages"] if p["Name"] == "musl")
    arch = "amd64"
    if defect == "missing":
        result["Packages"].remove(row)
    elif defect == "duplicate":
        result["Packages"].append(copy.deepcopy(row))
    elif defect in {"version", "arch", "checksum", "source", "analyzer"}:
        field = {
            "version": "Version",
            "arch": "Arch",
            "checksum": "Digest",
            "source": "SrcName",
            "analyzer": "AnalyzedBy",
        }[defect]
        row[field] = "substituted"
    elif defect == "query":
        capture["packages"][0]["version"] = "changed"
    elif defect == "database":
        capture["installed_database"] = ""
    elif defect == "virtual-omission":
        result["Packages"] = [
            p for p in result["Packages"] if p["Name"] != ".python-rundeps"
        ]
    else:
        arch = "arm64"
    assert scan.validate_alpine_inventory(result, capture, arch)


def test_missing_live_inventory_is_not_accepted_as_a_smaller_alpine_floor(
    scan, recorded
):
    result = next(r for r in recorded["report"]["Results"] if r["Class"] == "os-pkgs")
    assert scan.validate_alpine_inventory(result, None, "amd64")


def test_alpine_scan_requires_immutable_live_binding_and_full_ecm_not_a_sidecar(
    scan, recorded
):
    report = recorded["report"]
    identity = {
        "ManifestDigest": "sha256:" + "f" * 64,  # unit-only manifest witness
        "ConfigDigest": recorded["image_id"],
        "RootFSDiffIDs": report["Metadata"]["DiffIDs"],
        "Architecture": "amd64",
        "OS": "linux",
    }
    timestamp = report["CreatedAt"]
    scanner = {
        "Version": report["Trivy"]["Version"],
        "VulnerabilityDB": {
            "Version": 2,
            "UpdatedAt": timestamp,
            "NextUpdate": timestamp,
            "DownloadedAt": timestamp,
        },
    }
    missing = scan.check_report(report, identity, scanner, "amd64")
    assert any("immutable Alpine live inventory binding" in r for r in missing)
    bound = {**identity, "capture": recorded["capture"]}
    failures = scan.check_report(report, identity, scanner, "amd64", bound)
    assert not any("immutable Alpine live inventory binding" in r for r in failures)
    assert "missing required Alpine ECM runtime providers" in failures
    assert "missing Go package detection inventory" in failures
    bound["ConfigDigest"] = "sha256:" + "0" * 64
    assert any(
        "immutable Alpine live inventory binding" in r
        for r in scan.check_report(report, identity, scanner, "amd64", bound)
    )


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_real_final_scans_refuse_the_original_scanner_invisible_owned_package(
    scan, architecture
):
    path = (
        ROOT
        / f"backend/tests/fixtures/native_scan/alpine-ecm-20261110-{architecture}.json.gz"
    )
    recorded = json.loads(gzip.decompress(path.read_bytes()))
    report = recorded["report"]
    assert len(report["Results"][0]["Packages"]) == 121
    assert len(recorded["runtime_inventory"]["capture"]["packages"]) == 122
    failures = scan.check_report(
        report,
        recorded["identity"],
        recorded["scanner"],
        architecture,
        recorded["runtime_inventory"],
    )
    assert failures == [
        "complete Alpine scan/query/database identity or source/checksum mismatch",
        "missing required Alpine ECM runtime providers",
        "missing Go package detection inventory",
    ]
    if architecture == "amd64":
        comprehensive = recorded["comprehensive_report"]
        go = [r for r in comprehensive["Results"] if r.get("Type") == "gobinary"]
        assert len(go) == 1 and go[0]["Target"] == "usr/local/bin/gosu"
        assert len(go[0]["Packages"]) == 4
        # Broader analysis restores Go detection; it must NOT paper over the
        # invalid original APK version or turn the 121-row report into a pass.
        assert (
            scan.check_report(
                comprehensive,
                recorded["identity"],
                recorded["scanner"],
                architecture,
                recorded["runtime_inventory"],
            )
            == failures[:2]
        )
