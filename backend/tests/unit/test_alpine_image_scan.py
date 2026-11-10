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
