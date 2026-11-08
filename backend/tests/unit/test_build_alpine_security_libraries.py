"""Owned security providers must keep ABI, source/patch and crate integrity."""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/alpine-native"


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_alpine_security_libraries",
        ROOT / "scripts/build_alpine_security_libraries.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_security_recipe_keeps_stable_xml_abi_and_all_reviewed_corrections(
    builder,
):
    recipe = builder.load_recipe(ASSETS / "security-libraries.json")
    assert {item["name"] for item in recipe["libraries"]} == {
        "libxml2",
        "speex",
        "librsvg",
    }
    xml = next(item for item in recipe["libraries"] if item["name"] == "libxml2")
    assert xml["version"] == "2.13.9" and xml["soname"] == "libxml2.so.2"
    assert (
        xml["archive"]["sha256"]
        == "a2c9ae7b770da34860050c309f903221c67830c86e4a7e760692b803df95143a"
    )
    assert {p["id"] for p in xml["patches"]} == {
        "CVE-2026-6732",
        "CVE-2026-86138",
        "CVE-2026-86139",
        "CVE-2026-86140",
        "CVE-2026-86142",
        "CVE-2026-86143",
        "CVE-2026-86144",
    }
    speex = next(item for item in recipe["libraries"] if item["name"] == "speex")
    assert any(p["id"] == "CVE-2026-116741" for p in speex["patches"])
    assert any(p["id"] == "ecm-checked-append-capacity" for p in speex["patches"])
    assert recipe["capacity_smoke"]["sha256"] == builder.sha256(
        ASSETS / "speex-capacity-smoke.c"
    )
    rsvg = next(item for item in recipe["libraries"] if item["name"] == "librsvg")
    assert rsvg["version"] == "2.63.4"
    assert (
        rsvg["archive"]["sha256"]
        == "31f07d9350dad3f8964fd613c31309ac160fdef13dfac59df6fd082de78d6e8f"
    )
    assert (
        rsvg["baseline_archive_sha256"]
        == "5e6b8f8c4e488697b1711956fd0f97ca855fb059cdfeed904e9e7eb9961f3639"
    )
    assert (
        rsvg["vendor"]["sha256"]
        == "d5fd9570af2fa9c586f2f34bd4163ee3a3869a5942be70897bfb4aa8270cfc50"
    )


@pytest.mark.parametrize(
    "mutation", ["omit-provider", "omit-patch", "source", "abi", "vendor"]
)
def test_security_source_or_patch_drift_is_not_an_operator_override(
    builder, tmp_path, mutation
):
    recipe = json.loads((ASSETS / "security-libraries.json").read_text())
    if mutation == "omit-provider":
        recipe["libraries"].pop()
    elif mutation == "omit-patch":
        recipe["libraries"][0]["patches"].pop()
    elif mutation == "source":
        recipe["libraries"][0]["archive"]["sha256"] = "0" * 64
    elif mutation == "abi":
        recipe["libraries"][0]["soname"] = "libxml2.so.16"
    else:
        recipe["libraries"][-1]["vendor"]["sha256"] = "0" * 64
    path = tmp_path / "security-libraries.json"
    path.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="recipe identity"):
        builder.load_recipe(path)


def test_argv_has_no_version_key_source_or_configuration_overrides(builder):
    parser = builder.parser()
    assert {a.dest for a in parser._actions} == {
        "help",
        "manifest",
        "arch",
        "work_dir",
        "output_dir",
        "cache_dir",
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["--allow-untrusted"])


def test_shared_library_dependencies_reject_foreign_abi_and_build_paths(builder):
    text = "(SONAME) Library soname: [libxml2.so.2]\n(NEEDED) Shared library: [libc.musl-x86_64.so.1]\n(NEEDED) Shared library: [libz.so.1]"
    assert builder.dynamic_dependencies(text, "libxml2.so.2", "amd64") == [
        "libc.musl-x86_64.so.1",
        "libz.so.1",
    ]
    for bad in [
        text.replace("libz.so.1", "libc.so.6"),
        text.replace("x86_64", "aarch64"),
        text + "\n(RUNPATH) Library runpath: [/proof/sdk/usr/lib]",
        text.replace(".so.2]", ".so.16]"),
    ]:
        with pytest.raises(builder.BuildError, match="dynamic"):
            builder.dynamic_dependencies(bad, "libxml2.so.2", "amd64")


def test_security_behavior_receipt_requires_xml_nonet_and_bounded_speex_buffer(builder):
    good = {
        "xml_nonet_text": True,
        "xml_nonet_xml": True,
        "xml_tree_nonet_text": True,
        "xml_tree_nonet_xml": True,
        "speex_external_buffer_canary": True,
    }
    assert builder.validate_smoke(json.dumps(good)) == good
    for name in good:
        with pytest.raises(builder.BuildError, match="smoke"):
            builder.validate_smoke(json.dumps({**good, name: False}))
    with pytest.raises(builder.BuildError, match="smoke"):
        builder.validate_smoke("{}")


@pytest.mark.parametrize(
    "target,allowed",
    [("../COPYING.LIB", True), ("../../../escape", False), ("/etc/passwd", False)],
)
def test_source_license_links_can_stay_inside_root_but_never_escape(
    builder, tmp_path, target, allowed
):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("source/COPYING.LIB")
        member.size = 7
        tar.addfile(member, io.BytesIO(b"license"))
        member = tarfile.TarInfo("source/sub/COPYING.LIB")
        member.type, member.linkname = tarfile.SYMTYPE, target
        tar.addfile(member)
    destination = tmp_path / "source"
    if allowed:
        builder.extract_source(archive, destination)
        assert (destination / "sub/COPYING.LIB").read_bytes() == b"license"
    else:
        with pytest.raises(builder.BuildError, match="unsafe"):
            builder.extract_source(archive, destination)
        assert not destination.exists()


def test_effective_cargo_home_is_offline_and_uses_verified_absolute_vendor(
    builder, tmp_path
):
    source = tmp_path / "source with spaces"
    (source / "vendor").mkdir(parents=True)
    cargo_home = tmp_path / "cargo-home"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    builder.configure_cargo(source, cargo_home, evidence)
    config = tomllib.loads((cargo_home / "config.toml").read_text())
    assert config == {
        "net": {"offline": True},
        "source": {
            "crates-io": {"replace-with": "vendored-sources"},
            "vendored-sources": {"directory": str((source / "vendor").resolve())},
        },
    }
    assert (evidence / "cargo-config.toml").read_bytes() == (
        cargo_home / "config.toml"
    ).read_bytes()


def test_effective_cargo_home_cannot_reuse_an_ambient_configuration(builder, tmp_path):
    home = tmp_path / "cargo-home"
    home.mkdir()
    (home / "config.toml").write_text("[net]\noffline = false\n")
    with pytest.raises(builder.BuildError, match="Cargo home"):
        builder.configure_cargo(tmp_path, home, tmp_path)


@pytest.mark.parametrize(
    "text", ["null", "[]", "[{}]", "true", "42", '"not a receipt"']
)
def test_smoke_receipt_must_be_a_json_object(builder, text):
    with pytest.raises(builder.BuildError, match="smoke"):
        builder.validate_smoke(text)


def test_retained_real_cargo_graph_has_complete_lock_and_file_closure(
    builder, tmp_path
):
    recipe = builder.load_recipe(ASSETS / "security-libraries.json")
    vendor = recipe["libraries"][-1]["vendor"]
    source = tmp_path / "source"
    builder._extract(
        ASSETS / vendor["file"], source, roots={".cargo", "Cargo.lock", "vendor"}
    )
    crates = builder.verify_vendor(source, vendor["lock_sha256"])
    assert len(crates) == 352
    assert {
        "name": "itoa",
        "version": "1.0.18",
        "checksum": "8f42a60cbdf9a97f5d2305f08a87dc4e09308d1276d28c869c684d7777685682",
    } in crates
    victim = source / "vendor/itoa-1.0.18/LICENSE-MIT"
    original = victim.read_bytes()
    victim.write_bytes(original + b"\n")
    with pytest.raises(builder.BuildError, match="file checksum"):
        builder.verify_vendor(source, vendor["lock_sha256"])
    victim.write_bytes(original)
    extra = source / "vendor/itoa-1.0.18/extra-build-input"
    extra.write_text("not in verified crate")
    with pytest.raises(builder.BuildError, match="file inventory"):
        builder.verify_vendor(source, vendor["lock_sha256"])
    extra.unlink()
    (source / "vendor/itoa-1.0.18").rename(source / "removed-crate")
    with pytest.raises(builder.BuildError, match="dependency closure"):
        builder.verify_vendor(source, vendor["lock_sha256"])
