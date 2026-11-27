"""Wheel bindings validate actual payloads, not a prior manylinux index."""

import base64
import csv
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def inventory(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "inventory_musllinux_wheels", ROOT / "scripts/inventory_musllinux_wheels.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wheel(tmp_path, *, extra=None, tamper=False):
    members = {
        "sample/__init__.py": b"pass\n",
        "sample-1.0.dist-info/METADATA": b"Metadata-Version: 2.4\nName: sample\nVersion: 1.0\nLicense-File: LICENSE\n",
        "sample-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "sample-1.0.dist-info/licenses/LICENSE": b"actual notice text\n",
    }
    members.update(extra or {})
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, content in members.items():
        writer.writerow(
            [
                name,
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .decode()
                .rstrip("="),
                len(content),
            ]
        )
    writer.writerow(["sample-1.0.dist-info/RECORD", "", ""])
    members["sample-1.0.dist-info/RECORD"] = record.getvalue().encode()
    if tamper:
        members["sample/__init__.py"] = b"changed after RECORD\n"
    path = tmp_path / "sample-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def test_complete_payload_record_and_notice_are_bound(inventory, tmp_path):
    path = wheel(tmp_path)
    result = inventory.scan_wheel(path, inventory.sha256(path), "amd64")
    assert result["name"] == "sample" and result["version"] == "1.0"
    assert result["native_files"] == []
    assert len(result["members"]) == 5
    assert result["notices"][0]["path"] == "sample-1.0.dist-info/licenses/LICENSE"
    assert (
        result["notices"][0]["sha256"]
        == hashlib.sha256(b"actual notice text\n").hexdigest()
    )


def test_whole_wheel_identity_precedes_parsing(inventory, tmp_path):
    path = tmp_path / "not-a-wheel.whl"
    path.write_bytes(b"not an archive")
    with pytest.raises(inventory.AttributionError, match="whole-wheel"):
        inventory.scan_wheel(path, "0" * 64, "arm64")


def test_valid_zip_hash_does_not_waive_record_tampering(inventory, tmp_path):
    path = wheel(tmp_path, tamper=True)
    with pytest.raises(inventory.AttributionError, match="RECORD"):
        inventory.scan_wheel(path, inventory.sha256(path), "amd64")


@pytest.mark.parametrize("member", ["../escape", "/absolute", "sample/../../escape"])
def test_archive_names_are_never_filesystem_instructions(inventory, tmp_path, member):
    path = wheel(tmp_path, extra={member: b"no extraction"})
    with pytest.raises(inventory.AttributionError, match="unsafe"):
        inventory.scan_wheel(path, inventory.sha256(path), "amd64")


def test_embedded_sbom_is_raw_artifact_evidence_not_source_authority(
    inventory, tmp_path
):
    raw = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "components": [
                {
                    "name": "libgcc",
                    "version": "14.2.0-r6",
                    "purl": "pkg:apk/NotpineForGHA/libgcc@14.2.0-r6",
                }
            ],
        }
    ).encode()
    path = wheel(tmp_path, extra={"sample-1.0.dist-info/sboms/sample.cdx.json": raw})
    result = inventory.scan_wheel(path, inventory.sha256(path), "arm64")
    assert result["sboms"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        result["sboms"][0]["components"][0]["purl"]
        == "pkg:apk/NotpineForGHA/libgcc@14.2.0-r6"
    )
    assert result["source_status"] == "NOT_ESTABLISHED_BY_WHEEL_METADATA"


def test_incomplete_license_file_metadata_is_denied(inventory, tmp_path):
    path = wheel(
        tmp_path,
        extra={
            "sample-1.0.dist-info/METADATA": b"Name: sample\nVersion: 1.0\nLicense-File: missing.txt\n"
        },
    )
    with pytest.raises(inventory.AttributionError, match="declared license"):
        inventory.scan_wheel(path, inventory.sha256(path), "amd64")


def test_delivered_notice_tampering_is_not_waived_by_valid_wheel(inventory, tmp_path):
    path = wheel(tmp_path)
    row = inventory.scan_wheel(path, inventory.sha256(path), "amd64")
    payloads = tmp_path / "payloads"
    payloads.mkdir()
    notice = row["notices"][0]
    (payloads / notice["sha256"]).write_bytes(b"wrong delivered notice")
    with pytest.raises(inventory.AttributionError, match="delivered"):
        inventory.verify_payloads(row, payloads, tmp_path / "notices")


def test_preexisting_shared_notices_are_rehashed_before_reuse(inventory, tmp_path):
    path = wheel(tmp_path)
    row = inventory.scan_wheel(path, inventory.sha256(path), "amd64")
    payloads, notices = tmp_path / "payloads", tmp_path / "notices"
    payloads.mkdir()
    notices.mkdir()
    notice = row["notices"][0]
    (payloads / notice["sha256"]).write_bytes(b"actual notice text\n")
    notice["retained_identical_notices"] = ["LICENSE"]
    (notices / "LICENSE").write_bytes(b"changed historical notice")
    with pytest.raises(inventory.AttributionError, match="shared notice"):
        inventory.verify_payloads(row, payloads, notices)


def test_installed_native_payload_is_verified_not_only_the_download(
    inventory, tmp_path
):
    path = wheel(tmp_path)
    row = inventory.scan_wheel(path, inventory.sha256(path), "amd64")
    site = tmp_path / "site"
    site.mkdir()
    with zipfile.ZipFile(path) as archive:
        archive.extractall(site)
    inventory.verify_installed(row, site)
    (site / "sample/__init__.py").write_bytes(b"tampered runtime payload")
    with pytest.raises(inventory.AttributionError, match="installed"):
        inventory.verify_installed(row, site)


def test_pep427_header_and_script_relocations_are_checked(inventory, tmp_path):
    path = wheel(
        tmp_path,
        extra={
            "sample-1.0.data/headers/sample.h": b"header bytes",
            "sample-1.0.data/scripts/sample": b'#!python\nprint("unchanged")\n',
        },
    )
    row = inventory.scan_wheel(path, inventory.sha256(path), "amd64")
    root = tmp_path / "venv"
    site = root / "lib/python3.12/site-packages"
    site.mkdir(parents=True)
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if ".data/" not in name:
                target = site / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    header = root / "include/site/python3.12/sample/sample.h"
    header.parent.mkdir(parents=True)
    header.write_bytes(b"header bytes")
    script = root / "bin/sample"
    script.parent.mkdir()
    script.write_bytes(b'#!/opt/venv/bin/python\nprint("unchanged")\n')
    inventory.verify_installed(row, site, "/opt/venv/bin/python")
    script.write_bytes(b'#!/opt/venv/bin/python\nprint("tampered")\n')
    with pytest.raises(inventory.AttributionError, match="installed script"):
        inventory.verify_installed(row, site, "/opt/venv/bin/python")


def test_recorded_actual_musllinux_inventories_cover_all_pins_and_raw_sboms(inventory):
    root = ROOT / "docker/python-third-party/artifact-index/musllinux"
    native_total = 0
    for arch, count in [("amd64", 48), ("arm64", 45)]:
        path = root / f"linux-{arch}.json"
        assert inventory.sha256(path) == inventory.INVENTORY_SHA256[arch]
        data = json.loads(path.read_text())
        assert data["distribution_count"] == len(data["distributions"]) == 74
        assert data["native_ELF_count"] == count
        native_total += sum(len(row["native_files"]) for row in data["distributions"])
        for row in data["distributions"]:
            assert row["source_status"] == "NOT_ESTABLISHED_BY_WHEEL_METADATA"
            inventory.verify_payloads(
                row, root / "payloads", ROOT / "docker/python-third-party/notices"
            )
    assert native_total == 93
    arm = json.loads((root / "linux-arm64.json").read_text())
    crypto = next(row for row in arm["distributions"] if row["name"] == "cryptography")
    assert any(
        component.get("purl") == "pkg:apk/NotpineForGHA/libgcc@14.2.0-r6"
        for sbom in crypto["sboms"]
        for component in sbom["components"]
    )
