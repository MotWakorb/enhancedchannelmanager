"""Actual patched locks and registry archives define the exact nested source set."""

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_rust_source_inputs", ROOT / "scripts/alpine_rust_source_inputs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded(tmp_path):
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT
                / "backend/tests/fixtures/alpine/rust-patched-locks-and-cfg-if.json.gz"
            ).read_bytes()
        )
    )
    for name, text in fixture["files"].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(text))
        assert hashlib.sha256(path.read_bytes()).hexdigest() == fixture["sha256"][name]
    return tmp_path


def test_complete_real_patched_locks_retain_all_registry_and_workspace_subjects(
    inputs, recorded
):
    locks = {
        name: recorded / (name + "-Cargo.lock")
        for name in ["glycin", "libglycin", "rav1e"]
    }
    result = inputs.locked_crates(locks)
    assert len(result["registry"]) == 546
    assert {name: len(rows) for name, rows in result["workspace"].items()} == {
        "glycin": 17,
        "libglycin": 17,
        "rav1e": 2,
    }
    row = next(
        r for r in result["registry"] if (r["name"], r["version"]) == ("nix", "0.31.2")
    )
    assert row["origins"] == ["glycin", "libglycin"]
    assert result["full_runtime_source_acceptance"] is False


@pytest.mark.parametrize(
    "mutation", ["missing", "unknown-origin", "wrong-lock", "symlink"]
)
def test_unpatched_or_missing_locks_never_create_a_smaller_successful_source_set(
    inputs, recorded, mutation
):
    locks = {
        name: recorded / (name + "-Cargo.lock")
        for name in ["glycin", "libglycin", "rav1e"]
    }
    if mutation == "missing":
        locks.pop("rav1e")
    elif mutation == "unknown-origin":
        locks["unreviewed"] = locks["glycin"]
    elif mutation == "wrong-lock":
        locks["glycin"].write_bytes(locks["glycin"].read_bytes() + b"\n")
    else:
        path = recorded / "alias"
        path.symlink_to(locks["glycin"])
        locks["glycin"] = path
    with pytest.raises(inputs.InputError):
        inputs.locked_crates(locks)


def test_actual_registry_archive_binds_manifest_identity_and_every_original_file(
    inputs, recorded
):
    row = {
        "name": "cfg-if",
        "version": "1.0.4",
        "sha256": "9330f8b2ff13f34540b44e946ef35111825727b38d33286ef986142615121801",
    }
    result = inputs.inspect_crate(recorded / "crates/cfg-if-1.0.4.crate", row)
    assert result["license"] == "MIT OR Apache-2.0"
    assert {"Cargo.toml", "src/lib.rs", "LICENSE-MIT", "LICENSE-APACHE"} <= set(
        result["files"]
    )
    assert result["package_sha256"] == row["sha256"]
    assert result["status"] == "EXACT_LOCKED_REGISTRY_SOURCE_BYTES_ONLY"


def test_actual_legacy_license_expression_is_retained_without_invented_normalization(
    inputs, recorded
):
    row = {
        "name": "ident_case",
        "version": "1.0.1",
        "sha256": "b9e0384b61958566e926dc50660321d12159025e767c18e043daf26b70104c39",
    }
    result = inputs.inspect_crate(recorded / "crates/ident_case-1.0.1.crate", row)
    assert result["license"] == "MIT/Apache-2.0"
    assert result["full_runtime_source_acceptance"] is False


def test_a_small_recorded_subset_is_not_full_registry_source_acceptance(
    inputs, recorded
):
    locks = {
        name: recorded / (name + "-Cargo.lock")
        for name in ["glycin", "libglycin", "rav1e"]
    }
    with pytest.raises(inputs.InputError, match="complete exact"):
        inputs.verify_archives(locks, recorded / "crates")


@pytest.mark.parametrize(
    "mutation", ["bytes", "wrong-name", "wrong-version", "symlink"]
)
def test_even_a_correct_name_cannot_substitute_another_archive_or_package(
    inputs, recorded, mutation
):
    row = {
        "name": "cfg-if",
        "version": "1.0.4",
        "sha256": "9330f8b2ff13f34540b44e946ef35111825727b38d33286ef986142615121801",
    }
    path = recorded / "crates/cfg-if-1.0.4.crate"
    if mutation == "bytes":
        path.write_bytes(path.read_bytes() + b"mutated")
    elif mutation == "wrong-name":
        row["name"] = "other"
    elif mutation == "wrong-version":
        row["version"] = "1.0.5"
    else:
        alias = recorded / "alias"
        alias.symlink_to(path)
        path = alias
    with pytest.raises(inputs.InputError):
        inputs.inspect_crate(path, row)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "escape",
        "symlink",
        "hardlink",
        "mode",
        "checksum-file",
        "other-root",
    ],
)
def test_unsafe_archive_semantics_are_denied_independently_of_the_outer_hash(
    inputs, tmp_path, mutation
):
    path = tmp_path / "crate"
    name = {
        "escape": "cfg-if-1.0.4/../operator",
        "checksum-file": "cfg-if-1.0.4/.cargo-checksum.json",
        "other-root": "other-1.0.4/file",
    }.get(mutation, "cfg-if-1.0.4/file")
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if mutation == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "/operator"
        elif mutation == "hardlink":
            member.type = tarfile.LNKTYPE
            member.linkname = "other"
        elif mutation == "mode":
            member.mode = 0o4755
        archive.addfile(member, io.BytesIO())
        if mutation == "duplicate":
            archive.addfile(member, io.BytesIO())
    row = {
        "name": "cfg-if",
        "version": "1.0.4",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    with pytest.raises(inputs.InputError):
        inputs.inspect_crate(path, row)


@pytest.mark.parametrize("version", ["1.95.0", "1.96.0", "1.96.1", "unreviewed"])
def test_compiler_source_never_accepts_an_arbitrary_archive_or_inferred_current_toolchain(
    inputs, tmp_path, version
):
    path = tmp_path / "rustc-source.tar.xz"
    path.write_bytes(b"not the original signed compiler source")
    with pytest.raises(inputs.InputError):
        inputs.compiler_source(path, version)
