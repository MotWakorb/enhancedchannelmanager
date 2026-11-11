"""Owned APKs must bind actual reviewed products/sources and temporary trust."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def packager(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "package_alpine_native", ROOT / "scripts/package_alpine_native.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_complete_component_set_is_required_not_a_subset_with_the_same_claim(packager):
    assert set(packager.COMPONENTS) == {
        "codecs",
        "security",
        "jxl",
        "ffmpeg",
        "gosu",
        "resdet",
    }
    assert packager.COMPONENTS["jxl"][1] == "BUILT_JXL_API_ONLY"
    with pytest.raises(packager.BuildError, match="complete component set"):
        packager.validate_inputs({}, "amd64")


def test_original_gosu_and_ffmpeg_notices_remain_in_their_real_namespaces(packager):
    packager.validate_runtime_path("usr/local/share/licenses/gosu/LICENSE")
    packager.validate_runtime_path("usr/local/share/licenses/ffmpeg/COPYING.GPLv3")


def test_package_version_changes_when_the_packaging_source_changes(packager):
    components = {"codecs": {"recipe_sha256": "a" * 64, "receipt_sha256": "b" * 64}}
    assert packager.package_version(components, "c" * 64) == packager.package_version(
        components, "c" * 64
    )
    assert packager.package_version(components, "c" * 64) != packager.package_version(
        components, "d" * 64
    )


def test_default_package_version_is_apk_scanner_compatible_and_keeps_all_digest_bits(
    packager,
):
    components = {"codecs": {"recipe_sha256": "a" * 64, "receipt_sha256": "b" * 64}}
    digest = hashlib.sha256(
        json.dumps(
            {
                "components": components,
                "packager_sha256": "c" * 64,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    version = packager.package_version(components, "c" * 64)
    assert re.fullmatch(r"1\.0(?:\.\d{1,10}){8}-r0", version)
    words = version.removesuffix("-r0").split(".")[2:]
    assert all(0 <= int(word) <= 0xFFFFFFFF for word in words)
    assert "".join(f"{int(word):08x}" for word in words) == digest
    assert (
        packager.package_version(components, "c" * 64, legacy=True)
        == "1.0~" + digest + "-r0"
    )


def test_packaging_locations_cannot_modify_component_evidence(packager, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    for work, output in [
        (source / "new-work", tmp_path / "out"),
        (tmp_path / "work", source / "new-output"),
    ]:
        with pytest.raises(packager.BuildError, match="component evidence"):
            packager.validate_locations({"codecs": source}, work, output)
        assert not work.exists() and not output.exists()


@pytest.mark.parametrize(
    "bad", ["../../escape", "/absolute", "usr/../etc/shadow", "usr/file\nname"]
)
def test_package_inventory_paths_cannot_escape_or_ambiguate_ownership(packager, bad):
    with pytest.raises(packager.BuildError, match="package path"):
        packager.checked_path(bad)


def test_dependency_metadata_is_from_real_elf_not_name_aliases(packager):
    libraries = [
        {
            "soname": "libjxl.so.0.12",
            "needed": ["libjxl_cms.so.0.12", "libhwy.so.1", "libc.musl-x86_64.so.1"],
        },
        {
            "soname": "libjxl_cms.so.0.12",
            "needed": ["libhwy.so.1", "libc.musl-x86_64.so.1"],
        },
    ]
    provides, depends = packager.dependency_metadata(libraries)
    assert "so:libjxl.so.0.12=0" in provides
    assert "so:libjxl.so.0.11=0" not in provides
    assert depends == ["so:libc.musl-x86_64.so.1", "so:libhwy.so.1"]
    with pytest.raises(packager.BuildError, match="ELF provider"):
        packager.dependency_metadata(
            [{"soname": "libjxl.so.0.12", "needed": ["libc.so.6"]}]
        )


def test_staging_does_not_overwrite_evidence_and_binds_every_copy(packager, tmp_path):
    source, dest = tmp_path / "source", tmp_path / "stage"
    source.mkdir()
    (source / "file").write_bytes(b"reviewed bytes")
    (source / "link").symlink_to("file")
    inventory = packager.owned_artifacts(source)
    packager.copy_inventory(source, inventory, dest)
    assert packager.owned_artifacts(dest) == inventory
    with pytest.raises(packager.BuildError, match="collision"):
        packager.copy_inventory(source, inventory, dest)
    (source / "file").write_bytes(b"changed bytes")
    with pytest.raises(packager.BuildError, match="inventory"):
        packager.copy_inventory(source, inventory, tmp_path / "other")


def test_source_or_runtime_inventory_cannot_smuggle_a_key_or_setuid_file(
    packager, tmp_path
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"not an executable")
    (source / "file").chmod(0o4755)
    with pytest.raises(packager.BuildError, match="privileged mode"):
        packager.copy_inventory(
            source, packager.owned_artifacts(source), tmp_path / "dest"
        )
    with pytest.raises(packager.BuildError, match="runtime namespace"):
        packager.validate_runtime_path("etc/apk/keys/owned.pub")


def test_packaging_rejects_different_component_builds_than_ffmpeg_consumed(
    packager, tmp_path
):
    directories, records = {}, {}
    for name, (recipe, status) in packager.COMPONENTS.items():
        directory = tmp_path / name
        (directory / "root/usr/local/lib").mkdir(parents=True)
        (directory / "root/usr/local/lib" / (name + ".so")).write_bytes(b"owned bytes")
        (directory / "sources").mkdir()
        (directory / "sources/source.tar").write_bytes(b"opaque source archive")
        records[name] = {
            "recipe_sha256": recipe,
            "status": status,
            "architecture": "amd64",
            "owned_artifacts": packager.owned_artifacts(directory / "root"),
            "source_files": packager.owned_artifacts(directory / "sources"),
        }
        directories[name] = directory
    records["ffmpeg"]["component_receipts"] = {
        name: json.loads(json.dumps(records[name]))
        for name in ["codecs", "security", "jxl"]
    }
    for name, record in records.items():
        (directories[name] / "build-output.json").write_text(json.dumps(record))
    assert packager.validate_inputs(directories, "amd64") == records
    records["jxl"]["extra_build_identity"] = "a different independently built output"
    (directories["jxl"] / "build-output.json").write_text(json.dumps(records["jxl"]))
    with pytest.raises(packager.BuildError, match="composed component receipt"):
        packager.validate_inputs(directories, "amd64")


def test_existing_resdet_is_part_of_the_required_signed_component_set(packager):
    assert packager.COMPONENTS["resdet"] == (
        "530f7498262e5c38541513e01ca12b4f6964b61b80273f75ec8956ff5e17a82a",
        "BUILT_RESDET_ONLY",
    )
