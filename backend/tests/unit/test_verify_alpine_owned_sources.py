"""Consumer boundaries: recorded real capsule/query/DB shapes, no fake auth claim."""

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend/tests/fixtures/alpine"


def recorded(name):
    return json.loads(gzip.decompress((FIXTURES / name).read_bytes()))


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_owned_sources", ROOT / "scripts/verify_alpine_owned_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["amd64", "arm64"])
def capsule(consumer, tmp_path, request):
    """Unsigned stand-ins test copying/hash/namespace only, not APK authentication."""
    arch = request.param
    index = recorded(f"owned-public-evidence-index-{arch}.json.gz")
    native = recorded(f"owned-components-{arch}.json.gz")["package_receipt"]
    busy = recorded(f"owned-busybox-{arch}.json.gz")["build_receipt"]
    proof = tmp_path / "usr/share/ecm/provenance/owned-signatures"
    for row in index["files"]:
        path = proof / row["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = ("unsigned transport unit fixture: " + row["path"]).encode()
        if path.suffix == ".pub":
            data = b"-----BEGIN PUBLIC KEY-----\nQUFB\n-----END PUBLIC KEY-----\n"
        if row["path"] == "recipe/retain_alpine_owned_evidence.py":
            data = (ROOT / "scripts/retain_alpine_owned_evidence.py").read_bytes()
        path.write_bytes(data)
        path.chmod(0o644)
    for directory, record, packages in [
        (proof / "native", native, [native["package"]]),
        (proof / "busybox", busy, busy["runtime_packages"]),
    ]:
        record["public_key"]["sha256"] = hashlib.sha256(
            (directory / record["public_key"]["file"]).read_bytes()
        ).hexdigest()
        for package in packages:
            path = (
                directory
                / ("runtime-packages" if directory.name == "busybox" else "")
                / package["file"]
            )
            package["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (proof / "native/package-output.json").write_text(json.dumps(native))
    (proof / "busybox/build-output.json").write_text(json.dumps(busy))
    (proof / "busybox/check-output.json").write_text('{"unit_only":true}\n')
    index["helper_sha256"] = hashlib.sha256(
        (ROOT / "scripts/retain_alpine_owned_evidence.py").read_bytes()
    ).hexdigest()
    index["files"] = consumer.owned_artifacts(proof)
    (proof / "index.json").write_text(json.dumps(index))
    return tmp_path, arch, proof, index


def test_real_producer_index_shape_is_a_closed_namespace_not_signature_clearance(
    consumer, capsule
):
    root, arch, _proof, index = capsule
    result = consumer.read_namespace(root, arch)
    assert result["index"] == index
    assert len(index["files"]) == 11
    assert result["native"]["architecture"] == result["busybox"]["architecture"] == arch
    assert result["index"]["signature_verification_required"] is True
    assert result["index"]["full_runtime_source_acceptance"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "schema-bool",
        "source-clearance",
        "signature-clearance",
        "wrong-arch",
        "extra-file",
        "missing-apk",
        "changed-apk",
        "helper",
        "alias-directory",
        "extra-directory",
        "duplicate-json",
        "duplicate-receipt",
        "unsafe-basename",
        "wrong-key-name",
        "selected-bool",
        "extra-index-field",
        "missing-index-row",
        "resealed-file-mode",
        "resealed-helper",
        "resealed-private-key",
    ],
)
def test_closed_namespace_rejects_forged_or_incomplete_evidence(
    consumer, capsule, mutation
):
    root, arch, proof, index = capsule
    if mutation == "schema-bool":
        index["schema_version"] = True
    elif mutation == "selected-bool":
        index["selected_packages"] = True
    elif mutation == "extra-index-field":
        index["unexpected_clearance"] = True
    elif mutation == "missing-index-row":
        index["files"].pop()
    elif mutation in {"resealed-file-mode", "resealed-helper"}:
        path = proof / "recipe/retain_alpine_owned_evidence.py"
        if mutation == "resealed-file-mode":
            path.chmod(0o755)
        else:
            path.write_bytes(b"untrusted replacement, must not execute")
            index["helper_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        index["files"] = [
            row
            for row in consumer.owned_artifacts(proof)
            if row["path"] != "index.json"
        ]
    elif mutation == "source-clearance":
        index["full_runtime_source_acceptance"] = True
    elif mutation == "signature-clearance":
        index["signature_verification_required"] = False
    elif mutation == "wrong-arch":
        index["architecture"] = "arm64" if arch == "amd64" else "amd64"
    elif mutation == "extra-file":
        (proof / "private.pem").write_bytes(b"unselected private material")
    elif mutation in {"missing-apk", "changed-apk"}:
        path = next((proof / "native").glob("*.apk"))
        if mutation == "missing-apk":
            path.unlink()
        else:
            raw = path.read_bytes()
            path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    elif mutation == "helper":
        index["helper_sha256"] = "0" * 64
    elif mutation == "resealed-private-key":
        key = proof / "native/ecm-build.pub"
        key.write_bytes(
            b"-----BEGIN PRIVATE KEY-----\nQUFB\n-----END PRIVATE KEY-----\n"
        )
        path = proof / "native/package-output.json"
        receipt = json.loads(path.read_text())
        receipt["public_key"]["sha256"] = hashlib.sha256(key.read_bytes()).hexdigest()
        path.write_text(json.dumps(receipt))
        index["files"] = [
            row
            for row in consumer.owned_artifacts(proof)
            if row["path"] != "index.json"
        ]
    elif mutation == "alias-directory":
        outside = root / "outside"
        (proof / "native").rename(outside)
        (proof / "native").symlink_to(outside, target_is_directory=True)
    elif mutation == "extra-directory":
        (proof / "unselected").mkdir()
    elif mutation == "duplicate-json":
        (proof / "index.json").write_text(
            '{"schema_version":1,' + json.dumps(index)[1:]
        )
    else:
        path = proof / "native/package-output.json"
        receipt = json.loads(path.read_text())
        if mutation == "duplicate-receipt":
            path.write_text(
                '{"architecture":' + json.dumps(arch) + "," + json.dumps(receipt)[1:]
            )
        else:
            if mutation == "unsafe-basename":
                receipt["package"]["file"] = "../outside.apk"
            else:
                receipt["public_key"]["file"] = "private.pem"
            path.write_text(json.dumps(receipt))
        # Re-seal file hashes: malformed metadata must still fail independently.
        index["files"] = [
            row
            for row in consumer.owned_artifacts(proof)
            if row["path"] != "index.json"
        ]
    if mutation != "duplicate-json":
        (proof / "index.json").write_text(json.dumps(index))
    with pytest.raises(consumer.InputError):
        consumer.read_namespace(root, arch)


def test_recorded_live_query_and_full_database_match_with_one_exact_virtual_package(
    consumer,
):
    capture = recorded("owned-runtime-base-amd64-inventory.json.gz")
    result = consumer.owned_records(capture, "x86_64")
    assert set(result["blocks"]) == {
        "ecm-owned-native",
        "busybox",
        "busybox-binsh",
        "ssl_client",
    }
    assert len(capture["packages"]) == 122
    assert [row["name"] for row in result["plan"]["virtual_packages"]] == [
        ".python-rundeps"
    ]
    assert result["plan"]["status"] == "UNAUTHENTICATED_APK_SOURCE_PLAN_ONLY"


@pytest.mark.parametrize(
    "mutation", ["missing-owned", "extra-busybox-origin", "query-mismatch"]
)
def test_live_owned_cohort_is_exact_not_a_name_prefix_or_missing_origin_exemption(
    consumer, mutation
):
    capture = recorded("owned-runtime-base-amd64-inventory.json.gz")
    blocks = capture["installed_database"].strip("\n").split("\n\n")
    block = next(b for b in blocks if "\nP:busybox-binsh\n" in "\n" + b + "\n")
    row = next(row for row in capture["packages"] if row["name"] == "busybox-binsh")
    if mutation == "missing-owned":
        blocks.remove(block)
        capture["packages"].remove(row)
    elif mutation == "extra-busybox-origin":
        blocks.append(block.replace("P:busybox-binsh\n", "P:busybox-extras\n", 1))
        extra = copy.deepcopy(row)
        extra["name"] = "busybox-extras"
        capture["packages"].append(extra)
    else:
        row["version"] = "changed"
    capture["installed_database"] = "\n\n".join(blocks) + "\n\n"
    with pytest.raises(consumer.InputError):
        consumer.owned_records(capture, "x86_64")


def test_busybox_absolute_link_referent_hash_comes_from_signed_regular_metadata(
    consumer,
):
    receipt = recorded("owned-busybox-amd64.json.gz")["build_receipt"]
    artifacts = consumer.busybox_live_artifacts(receipt)
    regular = next(row for row in artifacts["busybox"] if row["path"] == "bin/busybox")
    link = artifacts["busybox-binsh"]
    assert len(link) == 1 and link[0]["target"] == "/bin/busybox"
    assert link[0]["target_sha256"] == regular["sha256"]
    assert all(
        row["type"] != "directory" for rows in artifacts.values() for row in rows
    )


@pytest.mark.parametrize(
    "mutation", ["missing-referent", "changed-link", "relative-link"]
)
def test_busybox_link_referent_cannot_be_invented_from_live_bytes(consumer, mutation):
    receipt = recorded("owned-busybox-amd64.json.gz")["build_receipt"]
    if mutation == "missing-referent":
        receipt["runtime_packages"][0]["installed_payload"] = [
            row
            for row in receipt["runtime_packages"][0]["installed_payload"]
            if row["path"] != "bin/busybox"
        ]
    else:
        link = next(
            row
            for row in receipt["runtime_packages"][1]["installed_payload"]
            if row["type"] == "symlink"
        )
        link["target"] = "/bin/other" if mutation == "changed-link" else "busybox"
    with pytest.raises(consumer.InputError):
        consumer.busybox_live_artifacts(receipt)


@pytest.fixture
def composition(consumer, capsule, monkeypatch):
    """Orchestration only: cryptographic/source binders are deliberately stubbed.

    Real image integration separately runs all of these without substitutions.
    """
    root, arch, proof, _index = capsule
    native = json.loads((proof / "native/package-output.json").read_text())
    busy = json.loads((proof / "busybox/build-output.json").read_text())
    component_fixture = recorded(f"owned-components-{arch}.json.gz")
    for name, raw in component_fixture["original_component_receipts"].items():
        path = root / consumer.PROVENANCE / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw)
    repository = root / "etc/apk/repositories"
    repository.parent.mkdir(parents=True)
    repository.write_text("https://unit.invalid/stable/main\n")
    scripts = root / "lib/apk/db/scripts.tar.gz"
    scripts.parent.mkdir(parents=True)
    scripts.write_bytes(b"unsigned orchestration stand-in")
    blocks = {name: "P:" + name for name in consumer.OWNED}
    plan = {
        "status": "UNAUTHENTICATED_APK_SOURCE_PLAN_ONLY",
        "unresolved_packages": ["unit sentinel, not waived"],
    }
    calls = []

    def stage(name, value):
        def run(*args, **kwargs):
            calls.append((name, args, kwargs))
            return value

        return run

    monkeypatch.setattr(
        consumer,
        "owned_records",
        stage("complete-plan", {"blocks": blocks, "plan": plan}),
    )
    monkeypatch.setattr(
        consumer,
        "payload_record",
        lambda block, machine: {
            "record": {"fields": {"unit": block}},
            "directories": {"unit": "0:0:755"},
        },
    )
    monkeypatch.setattr(
        consumer,
        "verify_owned_apk",
        stage(
            "native-signature", {"installed_artifacts": native["installed_artifacts"]}
        ),
    )
    monkeypatch.setattr(
        consumer, "bind_live_paths", stage("live", {"unit": "live binder stub"})
    )
    monkeypatch.setattr(
        consumer,
        "bind_components",
        stage("components", {"components": {"unit": "component binder stub"}}),
    )
    monkeypatch.setattr(
        consumer,
        "verify_sources",
        stage("native-sources", {"unit": "source binder stub"}),
    )
    monkeypatch.setattr(
        consumer, "load_packages", stage("busybox-source-static-check", busy)
    )
    monkeypatch.setattr(
        consumer,
        "replay_original_sources",
        stage("corresponding-source-replay", {"unit": "replay stub"}),
    )
    monkeypatch.setattr(consumer, "verify_signature", stage("busybox-signature", None))
    monkeypatch.setattr(
        consumer,
        "bind_installed_record",
        stage("busybox-db", {"unit": "DB binder stub"}),
    )
    monkeypatch.setattr(
        consumer,
        "parts",
        lambda package: [b"unit signature", package.name.encode(), b"unit data"],
    )
    monkeypatch.setattr(
        consumer,
        "verify_control_scripts",
        stage("installed-scripts", [{"unit": "control binder stub"}]),
    )
    monkeypatch.setattr(
        consumer.subprocess,
        "run",
        stage(
            "default-trust-denial",
            SimpleNamespace(returncode=1, stdout="", stderr="UNTRUSTED signature"),
        ),
    )
    return root, arch, busy, blocks, plan, calls


def test_composition_requires_every_binder_and_preserves_unresolved_source_plan(
    consumer, composition
):
    root, arch, busy, blocks, plan, calls = composition
    result = consumer.verify(root, {}, arch)
    names = [name for name, _, _ in calls]
    assert names.count("native-signature") == 1
    assert names.count("busybox-signature") == names.count("busybox-db") == 3
    assert names.count("live") == 4 and names.count("default-trust-denial") == 4
    assert all(
        names.count(name) == 1
        for name in [
            "complete-plan",
            "components",
            "native-sources",
            "busybox-source-static-check",
            "corresponding-source-replay",
            "installed-scripts",
        ]
    )
    assert (
        max(i for i, name in enumerate(names) if name == "busybox-db")
        < [i for i, name in enumerate(names) if name == "live"][1]
    )
    assert (
        next(kwargs for name, _, kwargs in calls if name == "native-signature")[
            "database_record"
        ]
        == blocks["ecm-owned-native"]
    )
    assert (
        next(
            kwargs for name, _, kwargs in calls if name == "corresponding-source-replay"
        )["corresponding_plan"]
        == root / "usr/share/ecm/provenance/busybox-source-replay/source-replay.json"
    )
    expected_scripts = next(
        args[1] for name, args, _ in calls if name == "installed-scripts"
    )
    assert (
        len(expected_scripts)
        == sum(len(row["control_scripts"]) for row in busy["runtime_packages"])
        == 3
    )
    assert result["source_plan"] == plan
    assert result["full_runtime_source_acceptance"] is False
    assert result["historical_compiler_linkage_established"] is False


@pytest.mark.parametrize(
    "binder",
    [
        "verify_owned_apk",
        "bind_live_paths",
        "verify_sources",
        "bind_components",
        "load_packages",
        "replay_original_sources",
        "verify_signature",
        "bind_installed_record",
        "verify_control_scripts",
    ],
)
def test_no_partial_composition_receipt_when_any_required_binder_rejects(
    consumer, composition, monkeypatch, binder
):
    root, arch, *_ = composition

    def reject(*args, **kwargs):
        raise consumer.InputError("unit binder rejection: " + binder)

    monkeypatch.setattr(consumer, binder, reject)
    with pytest.raises(consumer.InputError, match="unit binder rejection"):
        consumer.verify(root, {}, arch)


@pytest.mark.parametrize(
    "failure", ["trusted-owned-apk", "unrelated-apk-failure", "trust-change"]
)
def test_composition_requires_specific_default_trust_denials_and_unchanged_trust(
    consumer, composition, monkeypatch, failure
):
    root, arch, *_ = composition

    def result(*args, **kwargs):
        if failure == "trust-change":
            (root / "etc/apk/repositories").write_text("changed")
        return SimpleNamespace(
            returncode=0 if failure == "trusted-owned-apk" else 1,
            stdout="",
            stderr="unrelated error"
            if failure == "unrelated-apk-failure"
            else "UNTRUSTED signature",
        )

    monkeypatch.setattr(consumer.subprocess, "run", result)
    with pytest.raises(consumer.InputError):
        consumer.verify(root, {}, arch)
