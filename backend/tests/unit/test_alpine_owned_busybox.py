"""Three real owned APKv2 subjects must keep distinct control/payload/DB joins."""

import base64
import gzip
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
NAMES = ["busybox", "busybox-binsh", "ssl_client"]


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_owned_busybox", ROOT / "scripts/alpine_owned_busybox.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["amd64", "arm64"])
def recorded(request, tmp_path):
    document = json.loads(
        gzip.decompress(
            (
                ROOT
                / f"backend/tests/fixtures/alpine/owned-busybox-{request.param}.json.gz"
            ).read_bytes()
        )
    )
    for name, encoded in document["files"].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    return tmp_path, document


def subject(recorded, name):
    root, document = recorded
    row = next(
        r for r in document["build_receipt"]["runtime_packages"] if r["name"] == name
    )
    return (
        root / "runtime-packages" / row["file"],
        document["installed_records"][name],
        row,
        document["architecture"],
    )


@pytest.mark.parametrize("name", NAMES)
def test_actual_owned_control_payload_and_full_installed_record_join(
    consumer, recorded, name
):
    package, text, expected, arch = subject(recorded, name)
    result = consumer.bind_installed_record(package, text, expected, arch)
    assert result["name"] == name
    assert (
        result["database_files_verified"]
        == {"busybox": 7, "busybox-binsh": 1, "ssl_client": 1}[name]
    )
    assert (
        result["database_directories_verified"]
        == {"busybox": 17, "busybox-binsh": 1, "ssl_client": 2}[name]
    )
    assert result["payload"] == expected["installed_payload"]
    assert result["control_scripts"] == expected["control_scripts"]
    assert result["signature_verification_required"] is True
    assert result["full_runtime_source_acceptance"] is False


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "arch",
        "origin",
        "size",
        "license",
        "control-checksum",
        "file-checksum",
        "missing-path",
        "extra-path",
        "mode",
        "adb-format-flag",
    ],
)
def test_same_version_label_never_hides_signed_control_or_installed_payload_drift(
    consumer, recorded, name, mutation
):
    package, text, expected, arch = subject(recorded, name)
    if mutation == "version":
        text = text.replace("V:1.37.0_p20261109-r0", "V:1.37.0_p20261109-r1", 1)
    elif mutation == "arch":
        text = text.replace(
            "A:"
            + (
                "noarch"
                if name == "busybox-binsh"
                else {"amd64": "x86_64", "arm64": "aarch64"}[arch]
            ),
            "A:unknown",
            1,
        )
    elif mutation == "origin":
        text = text.replace("o:busybox", "o:other", 1)
    elif mutation == "size":
        line = next(l for l in text.splitlines() if l.startswith("S:"))
        text = text.replace(line, "S:1", 1)
    elif mutation == "license":
        text = text.replace("L:GPL-2.0-only", "L:MIT", 1)
    elif mutation in {"control-checksum", "file-checksum"}:
        prefix = "C:" if mutation == "control-checksum" else "Z:"
        line = next(l for l in text.splitlines() if l.startswith(prefix))
        text = text.replace(line, prefix + "Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA=", 1)
    elif mutation == "missing-path":
        line = next(l for l in text.splitlines() if l.startswith("R:"))
        text = text.replace(line, line.replace("R:", "R:renamed-"), 1)
    elif mutation == "extra-path":
        text += "\nR:unattributed\nZ:Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA="
    elif mutation == "mode":
        line = next(l for l in text.splitlines() if l.startswith("a:"))
        text = text.replace(line, "a:0:0:700", 1)
    else:
        text = text.replace("o:busybox\n", "o:busybox\nf:S\n", 1)
    with pytest.raises(consumer.InputError):
        consumer.bind_installed_record(package, text, expected, arch)


@pytest.mark.parametrize(
    "mutation", ["archive-bytes", "manifest-payload", "manifest-script", "other-format"]
)
def test_untrusted_receipt_or_archive_bytes_are_not_owned_source_proof(
    consumer, recorded, mutation
):
    package, text, expected, arch = subject(recorded, "busybox")
    if mutation == "archive-bytes":
        package.write_bytes(package.read_bytes() + b"changed")
    elif mutation == "manifest-payload":
        expected["installed_payload"].pop()
    elif mutation == "manifest-script":
        expected["control_scripts"].pop()
    else:
        package.write_bytes(b"ADBd not an APKv2 package")
        expected["sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
    with pytest.raises(consumer.InputError):
        consumer.bind_installed_record(package, text, expected, arch)


@pytest.fixture
def original_source_recipe():
    policy = json.loads((ROOT / "docker/alpine-busybox/manifest.json").read_text())
    with tarfile.open(
        ROOT / "docker/alpine-busybox" / policy["aports"]["file"]
    ) as archive:
        raw = archive.extractfile("main/busybox/APKBUILD").read()
    return policy, raw


def test_complete_actual_original_busybox_source_input_checksum_plan(
    consumer, original_source_recipe
):
    policy, raw = original_source_recipe
    result = consumer.source_inputs(policy, raw)
    assert len(result) == 75
    assert len({row["file"] for row in result}) == 75
    assert result[0] == {
        "file": "busybox-1.37.0.tar.bz2",
        "sha512": policy["source"]["sha512"],
    }
    assert any(row["file"] == "0001-wget-add-header-Accept.patch" for row in result)
    # Install/trigger scripts are in the original 80-file apports tree, not
    # the 75-row upstream/source-input checksum block. The consumer checks
    # those six additional apports members independently against that tar.
    assert "busybox.post-install" not in {row["file"] for row in result}


@pytest.mark.parametrize(
    "mutation",
    [
        "original-recipe",
        "missing-input",
        "duplicate-input",
        "unsafe-input",
        "source-digest",
    ],
)
def test_original_source_plan_is_not_replaced_by_self_reported_recipe_labels(
    consumer, original_source_recipe, mutation
):
    policy, raw = original_source_recipe
    if mutation == "original-recipe":
        raw += b"\n# changed original\n"
    elif mutation == "source-digest":
        policy["source"]["sha512"] = "0" * 128
    else:
        lines = raw.splitlines(keepends=True)
        index = next(
            i for i, line in enumerate(lines) if b"  busybox-1.37.0.tar.bz2" in line
        )
        if mutation == "missing-input":
            lines.pop(index)
        elif mutation == "duplicate-input":
            lines.insert(index, lines[index])
        else:
            lines[index] = lines[index].replace(
                b"busybox-1.37.0.tar.bz2", b"../outside"
            )
        raw = b"".join(lines)
        # Even if an untrusted receipt claims the changed recipe hash, complete
        # input count/uniqueness/path/upstream digest checks remain independent.
        policy["aports"]["original_recipe_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(consumer.InputError):
        consumer.source_inputs(policy, raw)
