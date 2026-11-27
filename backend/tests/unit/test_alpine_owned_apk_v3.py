"""Real native ADB metadata must bind the full owned package, not APKv2 shapes."""

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_owned_apk_v3", ROOT / "scripts/alpine_owned_apk_v3.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["amd64", "arm64"])
def recorded(request):
    return json.loads(
        gzip.decompress(
            (
                ROOT
                / f"backend/tests/fixtures/alpine/owned-adb-{request.param}.json.gz"
            ).read_bytes()
        )
    )


def bind(consumer, recorded):
    return consumer.bind_metadata(
        recorded["metadata"],
        recorded["installed_fields"],
        recorded["package_receipt"],
        recorded["package_receipt"]["architecture"],
    )


def test_complete_real_adb_metadata_matches_all_591_owned_artifacts(consumer, recorded):
    result = bind(consumer, recorded)
    assert (
        result["installed_artifacts"]
        == recorded["package_receipt"]["installed_artifacts"]
    )
    assert len(result["installed_artifacts"]) == 591
    assert (
        result["regular_files"] == 559
        and result["symlinks"] == 32
        and result["directories"] == 73
    )
    assert result["full_runtime_source_acceptance"] is False
    assert result["signature_verification_required"] is True


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "version",
        "arch",
        "origin",
        "license",
        "description",
        "hashes",
        "installed-size",
        "depends",
        "provides",
    ],
)
def test_signed_metadata_must_match_actual_installed_and_package_subjects(
    consumer, recorded, field
):
    info = recorded["metadata"]["info"]
    if field in {"depends", "provides"}:
        info[field] = info[field][1:]
    elif field == "installed-size":
        info[field] += 1
    elif field == "hashes":
        info[field] = "0" * 40
    else:
        info[field] += "changed"
    with pytest.raises(consumer.InputError):
        bind(consumer, recorded)


@pytest.mark.parametrize(
    "mutation",
    [
        "root-field",
        "info-field",
        "path-field",
        "file-field",
        "acl-field",
        "user",
        "group",
        "privileged-mode",
        "size-bool",
        "unknown-type",
        "malformed-hash",
        "hash-and-target",
        "neither-hash-nor-target",
    ],
)
def test_unreviewed_adb_field_shapes_and_permissions_fail_closed(
    consumer, recorded, mutation
):
    document = recorded["metadata"]
    directory = next(p for p in document["paths"] if p.get("files"))
    file = next(f for f in directory["files"] if "hash" in f)
    if mutation == "root-field":
        document["accepted"] = True
    elif mutation == "info-field":
        document["info"]["accepted"] = True
    elif mutation == "path-field":
        directory["accepted"] = True
    elif mutation == "file-field":
        file["accepted"] = True
    elif mutation == "acl-field":
        file["acl"]["accepted"] = True
    elif mutation == "user":
        file["acl"]["user"] = "operator"
    elif mutation == "group":
        file["acl"]["group"] = "operator"
    elif mutation == "privileged-mode":
        file["acl"]["mode"] = 0o4755
    elif mutation == "size-bool":
        file["size"] = True
    elif mutation == "unknown-type":
        file.pop("hash")
        file["target"] = "0020" + b"device".hex()
    elif mutation == "malformed-hash":
        file["hash"] = "Q1not-a-sha256"
    elif mutation == "hash-and-target":
        file["target"] = "00a0" + b"elsewhere".hex()
    else:
        file.pop("hash")
    with pytest.raises(consumer.InputError):
        bind(consumer, recorded)


@pytest.mark.parametrize(
    "mutation",
    [
        "directory-escape",
        "file-escape",
        "duplicate-directory",
        "duplicate-file",
        "missing-file",
        "file-bytes",
        "file-mode",
        "link-type",
        "link-escape",
        "link-missing-target",
        "link-cycle",
    ],
)
def test_a_correct_package_label_does_not_clear_an_incomplete_or_unsafe_payload(
    consumer, recorded, mutation
):
    paths = recorded["metadata"]["paths"]
    directory = next(p for p in paths if p.get("files"))
    file = next(f for f in directory["files"] if "hash" in f)
    symlink = next(f for p in paths for f in p.get("files", []) if "target" in f)
    if mutation == "directory-escape":
        directory["name"] = "../operator"
    elif mutation == "file-escape":
        file["name"] = "../operator"
    elif mutation == "duplicate-directory":
        paths.append(copy.deepcopy(directory))
    elif mutation == "duplicate-file":
        directory["files"].append(copy.deepcopy(file))
    elif mutation == "missing-file":
        directory["files"].remove(file)
    elif mutation == "file-bytes":
        file["hash"] = "0" * 64
    elif mutation == "file-mode":
        file["acl"]["mode"] ^= 1
    elif mutation == "link-type":
        symlink["target"] = "0080" + b"elsewhere".hex()
    elif mutation == "link-escape":
        symlink["target"] = "00a0" + b"../../../operator".hex()
    elif mutation == "link-missing-target":
        symlink["target"] = "00a0" + b"missing-file".hex()
    else:
        symlink["target"] = "00a0" + symlink["name"].encode().hex()
    with pytest.raises(consumer.InputError):
        bind(consumer, recorded)


def test_every_actual_installed_record_path_mode_and_checksum_joins_signed_adb(
    consumer, recorded
):
    result = consumer.bind_installed_record(
        recorded["installed_record"],
        recorded["metadata"],
        recorded["package_receipt"],
        recorded["package_receipt"]["architecture"],
    )
    assert result["installed_payload_database_verified"] is True
    assert result["database_files_verified"] == 591
    assert result["database_directories_verified"] == 72
    assert result["signature_verification_required"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-path",
        "extra-path",
        "renamed-path",
        "file-checksum",
        "link-checksum",
        "file-mode",
        "directory-mode",
        "missing-checksum",
        "checksum-format-flag",
    ],
)
def test_a_matching_package_checksum_does_not_hide_installed_database_payload_drift(
    consumer, recorded, mutation
):
    text = recorded["installed_record"]
    if mutation == "missing-path":
        text = text.replace(
            "R:ld-musl-"
            + (
                "x86_64"
                if recorded["package_receipt"]["architecture"] == "amd64"
                else "aarch64"
            )
            + ".path\n",
            "",
            1,
        )
    elif mutation == "extra-path":
        text += "\nR:unattributed-helper\nZ:Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA="
    elif mutation == "renamed-path":
        text = text.replace("R:ld-musl-", "R:substitute-", 1)
    elif mutation in {"file-checksum", "missing-checksum"}:
        line = next(line for line in text.splitlines() if line.startswith("Z:"))
        text = text.replace(
            line,
            "Z:Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA=" if mutation == "file-checksum" else "",
            1,
        )
    elif mutation == "link-checksum":
        start = text.index("R:librsvg-2.so\n")
        end = text.index("Z:", start)
        line = text[end:].splitlines()[0]
        text = text[:end] + text[end:].replace(
            line, "Z:Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA=", 1
        )
    elif mutation == "file-mode":
        text = text.replace("a:0:0:755", "a:0:0:700", 1)
    elif mutation == "directory-mode":
        text = text.replace("F:etc\n", "F:etc\nM:0:0:700\n", 1)
    else:
        text = text.replace("f:S\n", "f:X\n", 1)
    with pytest.raises(consumer.InputError):
        consumer.bind_installed_record(
            text,
            recorded["metadata"],
            recorded["package_receipt"],
            recorded["package_receipt"]["architecture"],
        )


@pytest.fixture
def original_paths(recorded, tmp_path, monkeypatch, consumer):
    package = tmp_path / "original.apk"
    package.write_bytes(b"ADBd original package fixture")
    key = tmp_path / "ecm-build.pub"
    key.write_bytes(b"original public key fixture")
    recorded["package_receipt"]["package"]["sha256"] = hashlib.sha256(
        package.read_bytes()
    ).hexdigest()
    recorded["package_receipt"]["public_key"]["sha256"] = hashlib.sha256(
        key.read_bytes()
    ).hexdigest()
    recorded["package_receipt"]["package"]["file"] = package.name
    recorded["installed_fields"]["S"] = str(package.stat().st_size)
    return package, key


def test_native_signature_and_adbdump_are_both_required_on_the_same_original_file(
    consumer, recorded, original_paths, monkeypatch
):
    package, key = original_paths
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                json.dumps(recorded["metadata"])
                if "adbdump" in args
                else str(package) + ": OK\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(consumer.subprocess, "run", run)
    result = consumer.verify_owned_apk(
        package,
        key,
        recorded["package_receipt"],
        recorded["installed_fields"],
        recorded["package_receipt"]["architecture"],
    )
    assert result["regular_files"] == 559 and len(calls) == 2
    assert all(str(package) in args and "--network=no" in args for args in calls)
    assert "verify" in calls[0] and "adbdump" in calls[1] and "--format" in calls[1]
    assert "--allow-untrusted" not in repr(calls)


@pytest.mark.parametrize(
    "mutation",
    [
        "verify-exit",
        "dump-exit",
        "duplicate-json",
        "package-bytes",
        "public-key-bytes",
        "extra-public-key",
    ],
)
def test_native_authentication_failure_cannot_be_overridden_by_good_metadata(
    consumer, recorded, original_paths, monkeypatch, mutation
):
    package, key = original_paths
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        status = (
            1
            if (mutation == "verify-exit" and "verify" in args)
            or (mutation == "dump-exit" and "adbdump" in args)
            else 0
        )
        text = json.dumps(recorded["metadata"])
        if mutation == "duplicate-json":
            text = '{"info":{},"info":{},"paths":[]}'
        return SimpleNamespace(
            returncode=status, stdout=text, stderr="failure" if status else ""
        )

    monkeypatch.setattr(consumer.subprocess, "run", run)
    if mutation == "package-bytes":
        package.write_bytes(package.read_bytes() + b"changed")
    elif mutation == "public-key-bytes":
        key.write_bytes(b"other key")
    elif mutation == "extra-public-key":
        key.with_name("unexpected.pub").write_bytes(b"another key")
    with pytest.raises(consumer.InputError):
        consumer.verify_owned_apk(
            package,
            key,
            recorded["package_receipt"],
            recorded["installed_fields"],
            recorded["package_receipt"]["architecture"],
        )
    if mutation == "verify-exit":
        assert len(calls) == 1
