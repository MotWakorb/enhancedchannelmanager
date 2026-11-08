"""Offline controls for the first three owned stable-Alpine libraries."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/alpine-native"


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_alpine_codecs", ROOT / "scripts/build_alpine_codecs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_cannot_override_sources_flags_or_jobs(builder):
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
        parser.parse_args(["--source-url", "https://attacker.invalid/input"])


def test_real_inputs_match_authenticated_baseline_payloads(builder):
    recipe = builder.load_recipe(ASSETS / "codecs.json")
    fixture = json.loads(
        (ROOT / "backend/tests/fixtures/alpine/codec-sources.json").read_text()
    )
    assert {r["name"] for r in recipe["libraries"]} == {"codec2", "twolame", "shine"}
    for item in recipe["libraries"]:
        origin = next(r for r in fixture["records"] if r["component"] == item["name"])
        assert origin["fingerprint"] == "AD485664E901B867051AB15F35A2F86E29B700A4"
        inputs = {f["name"]: f["sha256"] for f in origin["payload"]["files"]}
        assert inputs[item["archive"]["filename"]] == item["archive"]["sha256"]
        for patch in item["patches"]:
            assert inputs[Path(patch["file"]).name] == patch["sha256"]


@pytest.mark.parametrize(
    "mutation", ["url", "hash", "omit", "flags", "patches", "extra"]
)
def test_recipe_drift_fails_before_fetch_or_execution(builder, tmp_path, mutation):
    recipe = json.loads((ASSETS / "codecs.json").read_text())
    if mutation == "url":
        recipe["libraries"][0]["archive"]["url"] = "https://attacker.invalid/source"
    elif mutation == "hash":
        recipe["libraries"][0]["archive"]["sha256"] = "0" * 64
    elif mutation == "omit":
        recipe["libraries"].pop()
    elif mutation == "flags":
        recipe["libraries"][0]["configure"].append("-DLPCNET=ON")
    elif mutation == "patches":
        recipe["libraries"][0]["patches"] = []
    else:
        recipe["libraries"][0]["unknown"] = True
    path = tmp_path / "codecs.json"
    path.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="recipe identity"):
        builder.load_recipe(path)


@pytest.mark.parametrize(
    "release,machine,arch",
    [("3.24.4", "x86_64", "amd64"), ("3.24.4", "aarch64", "arm64")],
)
def test_matching_stable_alpine_platform(builder, release, machine, arch):
    builder.validate_platform(release, machine, arch)


@pytest.mark.parametrize(
    "release,machine,arch",
    [
        ("3.25.0", "x86_64", "amd64"),
        ("3.24.0_alpha1", "x86_64", "amd64"),
        ("3.24.4", "x86_64", "arm64"),
        ("3.24.4", "aarch64", "amd64"),
        ("20261105", "x86_64", "amd64"),
        ("3.24.4", "x86_64", "ppc64le"),
    ],
)
def test_wrong_release_or_target_cannot_build(builder, release, machine, arch):
    with pytest.raises(builder.BuildError, match="platform"):
        builder.validate_platform(release, machine, arch)


@pytest.mark.parametrize("arrangement", ["same", "nested", "exists", "symlink"])
def test_locations_are_new_disjoint_and_not_symlinks(builder, tmp_path, arrangement):
    work, output = tmp_path / "work", tmp_path / "output"
    if arrangement == "same":
        output = work
    elif arrangement == "nested":
        output = work / "output"
    elif arrangement == "exists":
        output.mkdir()
        (output / "user-work").write_text("preserve")
    else:
        output.symlink_to(tmp_path / "absent")
    with pytest.raises(builder.BuildError, match="locations"):
        builder.create_locations(work, output)
    if arrangement == "exists":
        assert (output / "user-work").read_text() == "preserve"
    assert not work.exists()


def test_libraries_need_matching_soname_and_musl_dependencies(builder):
    text = "(SONAME) Library soname: [libshine.so.3]\n(NEEDED) Shared library: [libc.musl-x86_64.so.1]"
    assert builder.validate_dynamic(text, "libshine.so.3", "amd64") == [
        "libc.musl-x86_64.so.1"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "(SONAME) Library soname: [libshine.so.3]\n(NEEDED) Shared library: [libc.so.6]",
        "(SONAME) Library soname: [libshine.so.3]\n(NEEDED) Shared library: [libc.musl-aarch64.so.1]",
        "(SONAME) Library soname: [libshine.so.2]\n(NEEDED) Shared library: [libc.musl-x86_64.so.1]",
        "(SONAME) Library soname: [libshine.so.3]\n(RUNPATH) Library runpath: [/tmp/build]",
        "",
        "(NEEDED) Shared library: [libc.musl-x86_64.so.1]",
    ],
)
def test_glibc_wrong_abi_or_build_path_is_not_an_alpine_library(builder, text):
    with pytest.raises(builder.BuildError, match="dynamic"):
        builder.validate_dynamic(text, "libshine.so.3", "amd64")


def test_missing_patch_preimage_cannot_invoke_patch(builder, tmp_path, monkeypatch):
    recipe = builder.load_recipe(ASSETS / "codecs.json")
    patch = recipe["libraries"][0]["patches"][0]
    monkeypatch.setattr(
        builder, "run", lambda *a, **kw: pytest.fail("patch ran without witnesses")
    )
    with pytest.raises(builder.BuildError, match="preimage"):
        builder.apply_patch(patch, ASSETS, tmp_path, {})


def test_tampered_patch_or_smoke_fails_before_build(builder, tmp_path):
    assets = tmp_path / "assets"
    shutil.copytree(ASSETS, assets)
    patch = assets / "patches/libshine-gcc15-fixes.patch"
    patch.write_bytes(patch.read_bytes() + b"\n")
    with pytest.raises(builder.BuildError, match="patch identity"):
        builder.load_recipe(assets / "codecs.json")
    shutil.copyfile(ASSETS / "patches/libshine-gcc15-fixes.patch", patch)
    (assets / "codec-smoke.c").write_text("int main(void) { return 0; }\n")
    with pytest.raises(builder.BuildError, match="smoke identity"):
        builder.load_recipe(assets / "codecs.json")


def test_patch_must_produce_the_exact_bound_postimage(builder, tmp_path, monkeypatch):
    patch = tmp_path / "change.patch"
    patch.write_text("retained patch input")
    source = tmp_path / "source"
    source.mkdir()
    member = source / "member.c"
    member.write_bytes(b"before")
    record = {
        "file": patch.name,
        "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "witnesses": [
            {
                "path": "member.c",
                "before_sha256": hashlib.sha256(b"before").hexdigest(),
                "after_sha256": hashlib.sha256(b"approved after").hexdigest(),
            }
        ],
    }

    def wrong_patch(*args, **kwargs):
        member.write_bytes(b"unapproved after")
        return "patch command exited successfully"

    monkeypatch.setattr(builder, "run", wrong_patch)
    with pytest.raises(builder.BuildError, match="postimage"):
        builder.apply_patch(record, tmp_path, source, {})


@pytest.mark.parametrize("corrupt_cache", [False, True])
def test_build_calls_repository_guard_and_rehashes_cache_before_extracting(
    builder, tmp_path, monkeypatch, corrupt_cache
):
    original_read = Path.read_text

    def platform_files(path, *args, **kwargs):
        if str(path) == "/etc/alpine-release":
            return "3.24.4\n"
        if str(path) == "/etc/apk/repositories":
            branch = "v3.24" if corrupt_cache else "edge"
            return "\n".join(
                f"https://dl-cdn.alpinelinux.org/alpine/{branch}/{feed}"
                for feed in ("main", "community")
            )
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", platform_files)
    monkeypatch.setattr(builder.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        builder, "run", lambda *args, **kwargs: "recorded builder tool output"
    )
    monkeypatch.setattr(
        builder,
        "safe_extract",
        lambda *args: pytest.fail("unverified source extracted"),
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    recipe = builder.load_recipe(ASSETS / "codecs.json")
    (cache / recipe["libraries"][0]["archive"]["sha256"]).write_bytes(b"corrupt")
    work, output = tmp_path / "work", tmp_path / "output"
    with pytest.raises(
        ValueError if not corrupt_cache else builder.BuildError,
        match="repositories|cached input checksum",
    ):
        builder.build(ASSETS / "codecs.json", "amd64", work, output, cache)
    assert not (output / "build-output.json").exists()
    if not corrupt_cache:
        assert not work.exists() and not output.exists()


def test_smoke_results_require_real_codec_operations(builder):
    valid = {
        "codec2_samples": 160,
        "codec2_bytes": 8,
        "twolame_bytes": 417,
        "shine_bytes": 418,
    }
    assert builder.validate_smoke(json.dumps(valid)) == valid
    for key in valid:
        bad = {**valid, key: 0}
        with pytest.raises(builder.BuildError, match="smoke"):
            builder.validate_smoke(json.dumps(bad))
    with pytest.raises(builder.BuildError, match="smoke"):
        builder.validate_smoke("{}")


def test_smoke_files_are_complete_real_frames_not_only_positive_counts(
    builder, tmp_path
):
    result = {
        "codec2_samples": 160,
        "codec2_bytes": 8,
        "twolame_bytes": 8,
        "shine_bytes": 8,
    }
    files = {
        "codec2.bin": b"a" * 128,
        "codec2-decoded.raw": b"b" * 5120,
        "twolame.mp2": b"\xff\xfd" + b"c" * 6,
        "shine.mp3": b"\xff\xfb" + b"d" * 6,
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    builder.validate_smoke_files(tmp_path, result)
    (tmp_path / "shine.mp3").write_bytes(b"x" * 8)
    with pytest.raises(builder.BuildError, match="MPEG frame"):
        builder.validate_smoke_files(tmp_path, result)
    (tmp_path / "shine.mp3").write_bytes(files["shine.mp3"])
    (tmp_path / "codec2-decoded.raw").write_bytes(b"x")
    with pytest.raises(builder.BuildError, match="size/type"):
        builder.validate_smoke_files(tmp_path, result)
