"""Final source-gate composition; real paired installed records, stubbed consumers.

The individual consumers have independent signature/source/live-file tests.
These tests enforce their mandatory composition, not fresh image execution.
"""

import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_runtime_sources",
        ROOT / "scripts/verify_alpine_runtime_sources.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def subject(gate, monkeypatch, tmp_path):
    bundle = json.loads(
        gzip.decompress(
            (
                ROOT
                / "docs/design/evidence/stable-alpine-advisory-feasibility-20261110.receipts.json.gz"
            ).read_bytes()
        )
    )
    capture = json.loads(
        next(
            row["original_utf8"]
            for row in bundle["records"]
            if row["path"] == "native-amd64/runtime-inventory.json"
        )
    )
    calls = []
    plan = gate.source_plan(capture, "x86_64")
    external = {
        "status": "EXTERNAL_APK_SOURCE_DELIVERY_ONLY",
        "package_set_sha256": plan["package_set_sha256"],
        "external_packages": 117,
        "unresolved_owned_packages": plan["unresolved_packages"],
        "full_runtime_source_acceptance": False,
    }
    owned = {
        "status": "OWNED_NATIVE_SIGNATURE_DATABASE_LIVE_CONTROL_AND_ORIGINAL_SOURCE_JOINS_ONLY",
        "source_plan": plan,
        "full_runtime_source_acceptance": False,
    }
    for name, result in [
        ("verify_external", external),
        ("verify_owned", owned),
        ("verify_python_base", {"status": "BOUNDED_TEST_STAND_IN"}),
        ("verify_wheels", {"status": "BOUNDED_TEST_STAND_IN"}),
        ("verify_rust", {"status": "BOUNDED_TEST_STAND_IN"}),
    ]:

        def consume(*args, name=name, result=result):
            calls.append(name)
            return result

        monkeypatch.setattr(gate, name, consume)
    return tmp_path, capture, calls, external, owned


def test_all_existing_source_consumers_are_required_not_rpm_or_advisory_clearance(
    gate, subject
):
    root, capture, calls, _external, _owned = subject
    result = gate.verify(root, capture, "amd64")
    assert calls == [
        "verify_external",
        "verify_owned",
        "verify_python_base",
        "verify_wheels",
        "verify_rust",
    ]
    assert result["installed_packages"] == 122
    assert result["owned_packages_verified"] == [
        "busybox",
        "busybox-binsh",
        "ecm-owned-native",
        "ssl_client",
    ]
    assert result["full_runtime_source_acceptance"] is False
    assert result["full_runtime_advisory_acceptance"] is False
    assert set(result["consumers"]) == {
        "external",
        "owned",
        "python_base",
        "wheels",
        "rust",
    }


@pytest.mark.parametrize(
    "name",
    [
        "verify_external",
        "verify_owned",
        "verify_python_base",
        "verify_wheels",
        "verify_rust",
    ],
)
def test_any_consumer_failure_still_fails_source_integrity(
    gate, subject, monkeypatch, name
):
    root, capture, _calls, _external, _owned = subject

    def reject(*args):
        raise gate.InputError("original source consumer rejected input")

    monkeypatch.setattr(gate, name, reject)
    with pytest.raises(gate.InputError, match="consumer rejected"):
        gate.verify(root, capture, "amd64")


@pytest.mark.parametrize(
    "defect",
    [
        "query",
        "architecture",
        "missing-owned",
        "extra-owned",
        "partition",
        "other-subject",
    ],
)
def test_incomplete_or_substituted_subject_never_becomes_a_deferred_security_pass(
    gate, subject, defect
):
    root, capture, _calls, external, owned = subject
    arch = "amd64"
    if defect == "query":
        capture["packages"][0]["version"] = "changed"
    elif defect == "architecture":
        arch = "arm64"
    elif defect == "missing-owned":
        external["unresolved_owned_packages"] = external["unresolved_owned_packages"][
            :-1
        ]
    elif defect == "extra-owned":
        external["unresolved_owned_packages"] = [
            *external["unresolved_owned_packages"],
            {"name": "unknown-package"},
        ]
    elif defect == "partition":
        external["external_packages"] -= 1
    else:
        owned["source_plan"] = copy.deepcopy(owned["source_plan"])
        owned["source_plan"]["package_set_sha256"] = "0" * 64
    with pytest.raises((gate.InputError, gate.InventoryError)):
        gate.verify(root, capture, arch)


def test_native_entrypoint_dispatches_alpine_without_requesting_an_rpm_index(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "native_sources_dispatch", ROOT / "tests/native_image/source_checks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/alpine-release").write_text("3.24.4\n")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    expected = {"native_source_and_artifact_integrity": {"distribution": "alpine"}}
    monkeypatch.setattr(module, "_alpine_delivery", lambda root, arch: expected)

    def forbidden(*args):
        pytest.fail("Alpine must not request an RPM source index")

    monkeypatch.setattr(module, "_rpm_sources", forbidden)
    assert module.native_source_and_artifact_integrity(tmp_path) == expected
