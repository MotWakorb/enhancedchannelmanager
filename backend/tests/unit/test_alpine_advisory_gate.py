"""Independent denial crosses recorded publisher -> image inventory -> CLI seam.

Transport replay is explicitly historical, not fresh TLS or corrected-image proof.
"""

import copy
import gzip
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend/tests/fixtures/alpine"


def recorded(name):
    return json.loads(gzip.decompress((FIXTURES / name).read_bytes()))


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("check_alpine_advisories")


@pytest.fixture(scope="module")
def official_snapshot(tmp_path_factory):
    sys.path.insert(0, str(ROOT / "scripts"))
    snapshot = importlib.import_module("alpine_advisory_snapshot")
    out = tmp_path_factory.mktemp("official-replay") / "snapshot"
    original = snapshot.fetch_document

    def replay(name, descriptor):
        raw = gzip.decompress((FIXTURES / f"{name}-20261109.json.gz").read_bytes())
        return raw, {
            "url": descriptor["url"],
            "response_url": descriptor["url"],
            "http_status": 200,
            "content_type": "application/json",
            "response_headers": {"X-Test-Scope": "recorded transport replay, not TLS"},
        }

    try:
        snapshot.fetch_document = replay
        snapshot.prepare(out)
    finally:
        snapshot.fetch_document = original
        sys.path.remove(str(ROOT / "scripts"))
    return out


@pytest.fixture
def evidence(gate, official_snapshot, tmp_path):
    doc = recorded("published-0066-amd64-scan.json.gz")
    files = {}
    for key in ["report", "identity", "scanner", "runtime_inventory"]:
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(doc[key]))
        files[key] = path
    snapshot = tmp_path / "snapshot"
    shutil.copytree(official_snapshot, snapshot)
    return files, snapshot


def bind(gate, evidence):
    files, snapshot = evidence
    binding = gate.bind_evidence(files, snapshot, "amd64")
    return binding, gate.canonical_sha256(binding)


def test_real_published_paired_inventories_are_complete_not_advisory_clearance(gate):
    for arch in ["amd64", "arm64"]:
        doc = recorded(f"published-0066-{arch}-scan.json.gz")
        assert (
            gate.check_report(
                doc["report"],
                doc["identity"],
                doc["scanner"],
                arch,
                doc["runtime_inventory"],
            )
            == []
        )
        assert len(doc["runtime_inventory"]["capture"]["packages"]) == 122
        severities = [
            v["Severity"]
            for r in doc["report"]["Results"]
            for v in r.get("Vulnerabilities", [])
        ]
        assert severities.count("MEDIUM") == 11 and severities.count("LOW") == 3


def test_known_affected_source_denies_even_with_empty_vendor_findings(gate, evidence):
    files, snapshot = evidence
    report = json.loads(files["report"].read_text())
    for row in report["Results"]:
        row["Vulnerabilities"] = []
    files["report"].write_text(json.dumps(report))
    binding, anchor = bind(gate, evidence)
    result = gate.evaluate(files, snapshot, binding, anchor, "amd64")
    assert result["status"] == "BLOCKED"
    assert result["scan_failures"] == []
    assert len(result["unfixed_matches"]) == 19
    assert len(result["known_advisory_holds"]) == 19
    assert {
        (r["repository"], r["source"], r["recorded_version"], r["cve"])
        for r in result["known_advisory_holds"]
    } == {
        (r["repository"], r["source"], r["version"], r["cve"])
        for r in result["unfixed_matches"]
    }
    assert sum(r["severity"] == "UNKNOWN" for r in result["unfixed_matches"]) == 18
    assert {r["name"] for r in result["owned_subject_holds"]} == {
        "busybox",
        "busybox-binsh",
        "ssl_client",
        "ecm-owned-native",
    }
    assert result["full_runtime_advisory_acceptance"] is False


def test_low_official_label_and_known_hold_cannot_be_promoted_by_vendor_omission(
    gate, evidence
):
    files, snapshot = evidence
    report = json.loads(files["report"].read_text())
    for row in report["Results"]:
        row["Vulnerabilities"] = []
    files["report"].write_text(json.dumps(report))
    path = snapshot / "tracker-main.json.gz"
    raw = json.loads(gzip.decompress(path.read_bytes()))
    item = next(x for x in raw["items"] if x["id"].endswith("CVE-2025-50422"))
    item["cvss3"] = {
        "score": 1.0,
        "vector": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N",
    }
    reseal_snapshot(snapshot, "tracker-main", raw)
    binding, anchor = bind(gate, evidence)
    result = gate.evaluate(files, snapshot, binding, anchor, "amd64")
    assert result["status"] == "BLOCKED"
    assert result["scan_failures"] == []
    assert (
        next(r for r in result["unfixed_matches"] if r["source"] == "cairo")["severity"]
        == "LOW"
    )
    assert len(result["known_advisory_holds"]) == 19


def reseal_snapshot(snapshot, name, raw):
    data = json.dumps(raw).encode()
    path = snapshot / (name + ".json.gz")
    path.write_bytes(gzip.compress(data, mtime=0))
    index_path = snapshot / "index.json"
    index = json.loads(index_path.read_text())
    row = next(r for r in index["records"] if r["name"] == name)
    row.update(
        raw_size=len(data),
        raw_sha256=hashlib.sha256(data).hexdigest(),
        size=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    index_path.write_text(json.dumps(index))


def test_nonempty_resealed_publisher_truncation_cannot_drop_known19_or_owned_holds(
    gate, evidence
):
    files, snapshot = evidence
    for part in ["main", "community"]:
        raw = json.loads(
            gzip.decompress((snapshot / f"tracker-{part}.json.gz").read_bytes())
        )
        raw["items"] = raw["items"][:1]
        reseal_snapshot(snapshot, "tracker-" + part, raw)
    binding, anchor = bind(gate, evidence)
    result = gate.evaluate(files, snapshot, binding, anchor, "amd64")
    assert len(result["known_advisory_holds"]) == 19
    assert len(result["owned_subject_holds"]) == 4
    assert result["status"] == "BLOCKED"


def test_nominal_tar_version_change_is_not_source_fix_proof(gate, evidence):
    files, snapshot = evidence
    inventory = json.loads(files["runtime_inventory"].read_text())
    capture = inventory["capture"]
    package = next(p for p in capture["packages"] if p["name"] == "tar")
    old = package["version"]
    package["version"] = "1.99-r0"
    blocks = capture["installed_database"].split("\n\n")
    capture["installed_database"] = "\n\n".join(
        block.replace("V:" + old, "V:1.99-r0")
        if "\nP:tar\n" in "\n" + block + "\n"
        else block
        for block in blocks
    )
    report = json.loads(files["report"].read_text())
    package = next(
        p
        for r in report["Results"]
        if r["Class"] == "os-pkgs"
        for p in r["Packages"]
        if p["Name"] == "tar"
    )
    package.update(Version="1.99-r0", SrcVersion="1.99-r0")
    files["runtime_inventory"].write_text(json.dumps(inventory))
    files["report"].write_text(json.dumps(report))
    binding, anchor = bind(gate, evidence)
    result = gate.evaluate(files, snapshot, binding, anchor, "amd64")
    assert not any(r["source"] == "tar" for r in result["unfixed_matches"])
    holds = [r for r in result["known_advisory_holds"] if r["source"] == "tar"]
    assert len(holds) == 3
    assert all(
        r["installed_packages"] == [{"name": "tar", "version": "1.99-r0"}]
        for r in holds
    )
    assert result["status"] == "BLOCKED"


def test_binding_does_not_accept_resealed_old_timestamp_or_fabricated_discharge(
    gate, evidence
):
    files, snapshot = evidence
    binding, _anchor = bind(gate, evidence)
    binding["captured_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with pytest.raises(gate.InputError, match="stale"):
        gate.evaluate(files, snapshot, binding, gate.canonical_sha256(binding), "amd64")
    binding, _anchor = bind(gate, evidence)
    binding["dispositions"] = [
        {"source": "libxml2", "status": "fixed", "version": "new"}
    ]
    with pytest.raises(gate.InputError, match="schema"):
        gate.evaluate(files, snapshot, binding, gate.canonical_sha256(binding), "amd64")


def test_original_known_severity_and_full_record_identity_survive_publisher_omission(
    gate, evidence
):
    files, snapshot = evidence
    binding, anchor = bind(gate, evidence)
    result = gate.evaluate(files, snapshot, binding, anchor, "amd64")
    holds = result["known_advisory_holds"]
    assert sum(r["recorded_severity"] == "UNKNOWN" for r in holds) == 18
    assert all(
        r["recorded_cvss3"] == {"score": 0.0, "vector": None}
        for r in holds
        if r["recorded_severity"] == "UNKNOWN"
    )
    assert {r["repository"] for r in holds} == {"3.24-main"}


@pytest.mark.parametrize(
    "defect",
    [
        "query",
        "database",
        "scan-inventory",
        "scanner",
        "rootfs",
        "manifest",
        "arch",
        "stale-snapshot",
        "resealed-binding",
        "resealed-valid-time",
        "pretend-clearance",
    ],
)
def test_tampered_or_resealed_evidence_never_yields_clearance(gate, evidence, defect):
    files, snapshot = evidence
    binding, anchor = bind(gate, evidence)
    if defect == "stale-snapshot":
        path = snapshot / "index.json"
        doc = json.loads(path.read_text())
        doc["captured_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        path.write_text(json.dumps(doc))
        # Even a freshly rebound old snapshot must fail semantic freshness.
        with pytest.raises(gate.InputError):
            bind(gate, evidence)
        return
    if defect in {"resealed-binding", "resealed-valid-time", "pretend-clearance"}:
        binding = copy.deepcopy(binding)
        if defect == "pretend-clearance":
            binding["full_runtime_advisory_acceptance"] = True
        elif defect == "resealed-valid-time":
            binding["captured_at"] = (
                datetime.now(UTC) - timedelta(minutes=1)
            ).isoformat()
        else:
            binding["files"]["scanner"] = "0" * 64
    else:
        key = (
            "runtime_inventory"
            if defect in {"query", "database", "rootfs", "manifest", "arch"}
            else "report"
            if defect == "scan-inventory"
            else "scanner"
        )
        doc = json.loads(files[key].read_text())
        if defect == "query":
            doc["capture"]["packages"].pop()
        elif defect == "database":
            doc["capture"]["installed_database"] = ""
        elif defect == "scan-inventory":
            doc["Results"][0]["Packages"].pop()
        elif defect == "scanner":
            doc["VulnerabilityDB"]["UpdatedAt"] = "2020-01-01T00:00:00Z"
        elif defect == "rootfs":
            doc["RootFSDiffIDs"] = ["sha256:" + "0" * 64]
        elif defect == "manifest":
            doc["ManifestDigest"] = "sha256:" + "0" * 64
        else:
            doc["Architecture"] = "arm64"
        files[key].write_text(json.dumps(doc))
    with pytest.raises(gate.InputError):
        gate.evaluate(files, snapshot, binding, anchor, "amd64")


def test_rebound_wrong_manifest_and_incomplete_inventory_are_denied_not_merely_hash_checked(
    gate, evidence
):
    files, _snapshot = evidence
    doc = json.loads(files["runtime_inventory"].read_text())
    doc["ManifestDigest"] = "sha256:" + "0" * 64
    files["runtime_inventory"].write_text(json.dumps(doc))
    with pytest.raises(gate.InputError, match="identity"):
        bind(gate, evidence)


def test_cli_emits_blocked_report_and_nonzero_against_complete_recorded0066(
    gate, evidence, tmp_path
):
    files, snapshot = evidence
    binding, anchor = bind(gate, evidence)
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(binding))
    output = tmp_path / "result.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/check_alpine_advisories.py"),
        "verify",
        "--snapshot",
        str(snapshot),
        "--binding",
        str(binding_path),
        "--binding-sha256",
        anchor,
        "--architecture",
        "amd64",
        "--output",
        str(output),
    ]
    for key, path in files.items():
        command += ["--" + key.replace("_", "-"), str(path)]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=90)
    assert proc.returncode == 1, proc.stderr
    result = json.loads(output.read_text())
    assert result["status"] == "BLOCKED" and len(result["unfixed_matches"]) == 19
    assert "BLOCKED" in proc.stderr


@pytest.mark.parametrize(
    "helper",
    [
        "alpine_source_inputs.py",
        "verify_alpine_external_sources.py",
        "alpine_apk_v2.py",
        "verify_musllinux_gnu.py",
    ],
)
def test_actual_enforcement_dependency_drift_rejected_after_binding(
    gate, evidence, tmp_path, monkeypatch, helper
):
    # Copy trusted checkout, not artifacts. Do not execute artifact-directory code.
    checkout = tmp_path / "consumer"
    shutil.copytree(ROOT / "scripts", checkout)
    monkeypatch.setattr(gate, "__file__", str(checkout / "check_alpine_advisories.py"))
    binding, anchor = bind(gate, evidence)
    path = checkout / helper
    original = path.read_text()
    if helper == "verify_alpine_external_sources.py":
        # Semantic drift: last duplicate wins instead of unique-key rejection.
        path.write_text(original + "\ndef unique(pairs):\n    return dict(pairs)\n")
    elif helper == "alpine_source_inputs.py":
        path.write_text(
            original
            + "\ndef require(value, message):\n    if not value: raise InputError('drift: ' + message)\n"
        )
    else:
        path.write_text(
            original + "\ndef require(value, message):\n    raise ValueError('drift')\n"
        )
    with pytest.raises(gate.InputError, match="helpers"):
        gate.evaluate(*evidence, binding, anchor, "amd64")


def test_no_physical_language_capture_is_explicitly_minimum_coverage_only(
    gate, evidence
):
    binding, anchor = bind(gate, evidence)
    result = gate.evaluate(*evidence, binding, anchor, "amd64")
    assert result["language_inventory"]["status"] == "MINIMUM_COVERAGE_ONLY"
    assert result["language_inventory"]["complete_image_catalogue"] is False


def physical_capture_path(tmp_path):
    path = tmp_path / "physical-language.json"
    path.write_bytes(
        gzip.decompress(
            (FIXTURES / "published-0066-amd64-physical-language.json.gz").read_bytes()
        )
    )
    return path


@pytest.mark.parametrize(
    "kind,retained",
    [("python-pkg", 1), ("python-pkg", 76), ("gobinary", 1), ("gobinary", 3)],
)
def test_actual_physical_catalogue_rejects_rebound_language_truncation(
    gate, evidence, tmp_path, kind, retained
):
    files, snapshot = evidence
    path = physical_capture_path(tmp_path)
    anchor = gate.canonical_sha256(gate.read_json(path))
    gate.bind_evidence(files, snapshot, "amd64", path, anchor)
    report = gate.read_json(files["report"])
    row = next(row for row in report["Results"] if row["Type"] == kind)
    row["Packages"] = row["Packages"][:retained]
    files["report"].write_text(json.dumps(report))
    with pytest.raises(gate.InputError, match="catalogue"):
        gate.bind_evidence(files, snapshot, "amd64", path, anchor)


@pytest.mark.parametrize("defect", ["source-inode", "rootfs", "extra", "version"])
def test_actual_physical_capture_anchor_and_exact_image_join(
    gate, evidence, tmp_path, defect
):
    files, snapshot = evidence
    path = physical_capture_path(tmp_path)
    document = gate.read_json(path)
    anchor = gate.canonical_sha256(document)
    if defect == "source-inode":
        document["physical_witnesses"]["gosu"]["inode"] += 1
    elif defect == "rootfs":
        document["identity"]["RootFSDiffIDs"] = ["sha256:" + "0" * 64]
    else:
        report = gate.read_json(files["report"])
        packages = next(
            row for row in report["Results"] if row["Type"] == "python-pkg"
        )["Packages"]
        if defect == "extra":
            packages.append(
                {"Name": "false-extra", "Version": "1.0", "FilePath": "false/METADATA"}
            )
        else:
            packages[0]["Version"] = "false"
        files["report"].write_text(json.dumps(report))
    path.write_text(json.dumps(document))
    with pytest.raises(gate.InputError, match="independent|catalogue"):
        gate.bind_evidence(files, snapshot, "amd64", path, anchor)
    if defect == "rootfs":
        with pytest.raises(gate.InputError, match="identity"):
            gate.bind_evidence(
                files, snapshot, "amd64", path, gate.canonical_sha256(document)
            )


def test_real_physical_capture_through_producer_bind_and_cli_consumer(
    gate, evidence, tmp_path
):
    files, snapshot = evidence
    path = physical_capture_path(tmp_path)
    anchor = gate.canonical_sha256(gate.read_json(path))
    base = [sys.executable, str(ROOT / "scripts/check_alpine_advisories.py")]
    common = [
        "--snapshot",
        str(snapshot),
        "--architecture",
        "amd64",
        "--language-capture",
        str(path),
        "--language-capture-sha256",
        anchor,
    ]
    for key, value in files.items():
        common += ["--" + key.replace("_", "-"), str(value)]
    binding = tmp_path / "physical-binding.json"
    producer = subprocess.run(
        base + ["bind", *common, "--output", str(binding)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert producer.returncode == 0, producer.stderr
    output = tmp_path / "physical-result.json"
    consumer = subprocess.run(
        base
        + [
            "verify",
            *common,
            "--binding",
            str(binding),
            "--binding-sha256",
            producer.stdout.strip(),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert consumer.returncode == 1, consumer.stderr
    result = gate.read_json(output)
    assert result["language_inventory"]["status"] == "SCOPED_PHYSICAL_CATALOGUE_JOINED"
    assert result["language_inventory"]["complete_image_catalogue"] is False
    assert result["status"] == "BLOCKED" and result["scan_failures"] == []
    assert len(result["known_advisory_holds"]) == 19
    assert len(result["owned_subject_holds"]) == 4
