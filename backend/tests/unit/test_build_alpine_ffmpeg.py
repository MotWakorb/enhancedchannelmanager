"""The Alpine build consumes the existing signed FFmpeg/patch/49-flag contract."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_alpine_ffmpeg", ROOT / "scripts/build_alpine_ffmpeg.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alpine_ffmpeg_keeps_signed_source_rtsp_patch_and_all_arguments(builder):
    recipe = builder.load_recipe(
        ROOT / "docker/alpine-native/ffmpeg.json", ROOT / "docker/native"
    )
    old = json.loads((ROOT / "docker/native/manifest.json").read_text())
    for key in ["ffmpeg", "configure", "ffmpeg_patches"]:
        assert recipe[key] == old[key]
    assert len((ROOT / "docker/native/ffmpeg.args").read_text().splitlines()) == 49


@pytest.mark.parametrize("arch,loader", [("amd64", "x86_64"), ("arm64", "aarch64")])
def test_program_interpreter_must_be_native_musl(builder, arch, loader):
    text = f"[Requesting program interpreter: /lib/ld-musl-{loader}.so.1]"
    builder.validate_interpreter(text, arch)
    for bad in [
        "",
        text.replace("musl", "linux"),
        text + "\n" + text,
        text.replace(loader, "aarch64" if loader == "x86_64" else "x86_64"),
    ]:
        with pytest.raises(builder.BuildError, match="interpreter"):
            builder.validate_interpreter(bad, arch)


def test_gnu_iconv_header_and_link_provider_are_both_explicit(builder, tmp_path):
    env = builder.build_environment(tmp_path)
    assert "-I/usr/include/gnu-libiconv" in env["CFLAGS"].split()
    assert env["extralibs"] == "-liconv"
    good = [
        {"path": "usr/local/lib64/libavformat.so.62.5.100", "needed": ["libiconv.so.2"]}
    ]
    builder.validate_gnu_iconv(good)
    for bad in [[], [{**good[0], "needed": ["libc.musl-x86_64.so.1"]}]]:
        with pytest.raises(builder.BuildError, match="GNU iconv"):
            builder.validate_gnu_iconv(bad)


@pytest.mark.parametrize(
    "mutation", ["none", "arch", "status", "recipe", "binary", "extra-source", "header"]
)
def test_component_composition_rehashes_receipts_products_and_sources(
    builder, tmp_path, mutation
):
    root, sources = tmp_path / "root", tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    (sdk / "owned-header.h").write_text("reviewed interface")
    (root / "owned.so").write_bytes(b"owned-library")
    (sources / "source.tar").write_bytes(b"exact-reviewed-source")
    record = {
        "architecture": "amd64",
        "status": "BUILT_API_SMOKE_ONLY",
        "recipe_sha256": builder.CODECS_SHA,
        "owned_artifacts": builder.owned_artifacts(root),
        "source_files": builder.owned_artifacts(sources),
        "sdk_artifacts": builder.owned_artifacts(sdk),
    }
    if mutation == "arch":
        record["architecture"] = "arm64"
    elif mutation == "status":
        record["status"] = "PASS"
    elif mutation == "recipe":
        record["recipe_sha256"] = "0" * 64
    elif mutation == "binary":
        (root / "owned.so").write_bytes(b"changed-library")
    elif mutation == "extra-source":
        (sources / "extra-source").write_text("unbound source")
    elif mutation == "header":
        (sdk / "owned-header.h").write_text("unbound inline compiler input")
    (tmp_path / "build-output.json").write_text(json.dumps(record))
    if mutation == "none":
        assert builder.validate_component(tmp_path, "amd64", "codecs") == record
    else:
        with pytest.raises(builder.BuildError, match="component"):
            builder.validate_component(tmp_path, "amd64", "codecs")


def test_actual_jxl_linkage_must_be_the_baseline_version_not_a_soname_alias(builder):
    good = [
        {
            "path": "usr/local/lib64/libavcodec.so.62.28.103",
            "needed": ["libjxl.so.0.12", "libjxl_threads.so.0.12"],
        }
    ]
    builder.validate_jxl_provider(good)
    for bad in [
        [],
        [{**good[0], "needed": ["libjxl.so.0.11", "libjxl_threads.so.0.11"]}],
    ]:
        with pytest.raises(builder.BuildError, match="owned JXL"):
            builder.validate_jxl_provider(bad)
