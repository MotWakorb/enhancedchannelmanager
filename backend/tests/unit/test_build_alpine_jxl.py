"""The seventh owned provider must retain the real baseline JXL/CMS contract."""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/alpine-native"


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_alpine_jxl", ROOT / "scripts/build_alpine_jxl.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_jxl_recipe_preserves_signed_baseline_sources_and_color_engine(builder):
    recipe = builder.load_recipe(ASSETS / "libjxl.json")
    assert recipe["version"] == "0.12.0"
    assert (
        recipe["archive"]["sha256"]
        == "03e9be69a30be4011f559da75328b6d7cea8ad921fabfbd551ce10bf45cdc992"
    )
    assert recipe["cms"]["commit"] == "96d9171c94b937a1b5f0293de7309ac16311b722"
    assert (
        recipe["cms"]["sha256"]
        == "d36e9192974fce70bf8b8b446f917469358c2f3889449e45bc84af509b174ad5"
    )
    assert "-DJPEGXL_ENABLE_SKCMS=ON" in recipe["configure"]
    assert "-DJPEGXL_FORCE_SYSTEM_LCMS2=OFF" in recipe["configure"]
    assert "-DJPEGXL_ENABLE_TRANSCODE_JPEG=ON" in recipe["configure"]
    assert "-DJPEGXL_ENABLE_JPEGLI=ON" in recipe["configure"]
    assert {p["soname"] for p in recipe["libraries"]} == {
        "libjxl.so.0.12",
        "libjxl_threads.so.0.12",
        "libjxl_cms.so.0.12",
    }
    assert (
        recipe["patches"][0]["sha256"]
        == "61fe9d986286ad1f5e7b9c49fd2de47a3500e3312490dda8460924dd523c65b2"
    )


def test_rootless_cms_archive_cannot_escape_or_replace_an_existing_source(
    builder, tmp_path
):
    archive = tmp_path / "vendor.tar.gz"
    for name in ["../escape", "/absolute", "src/file"]:
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo(name)
            member.size = 4
            tar.addfile(member, io.BytesIO(b"data"))
        target = tmp_path / "cms"
        if name == "src/file":
            builder.extract_cms(archive, target)
            assert (target / name).read_bytes() == b"data"
            with pytest.raises(builder.BuildError, match="CMS destination"):
                builder.extract_cms(archive, target)
        else:
            with pytest.raises(builder.BuildError, match="unsafe CMS"):
                builder.extract_cms(archive, target)
            assert not target.exists()
