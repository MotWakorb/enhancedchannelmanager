"""Bind both real static providers to signed SDK bytes and their exact sources."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "busybox_static_sources", ROOT / "scripts/busybox_static_sources.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_source_graph_preserves_both_architectures_and_original_notices(
    sources, tmp_path
):
    path = ROOT / "docker/alpine-busybox/static-sources.json"
    recipe = sources.load_sources(path)
    assert {p["name"]: p["version"] for p in recipe["components"]} == {
        "skalibs": "2.15.0.0",
        "utmps": "0.1.3.3",
    }
    for component in recipe["components"]:
        sources.verify_source(path.parent, component, tmp_path / component["name"])
    assert (
        len(recipe["architecture_inputs"]["x86_64"])
        == len(recipe["architecture_inputs"]["aarch64"])
        == 4
    )
    for machine in ["x86_64", "aarch64"]:
        for package in recipe["architecture_inputs"][machine]:
            sources.verify_package(path.parent, package, recipe["components"], machine)


def test_installed_static_archive_cannot_be_replaced_by_equal_version_metadata(
    sources, tmp_path
):
    path = tmp_path / "usr/lib/libutmps.a"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"changed archive")
    with pytest.raises(sources.BuildError, match="installed static"):
        sources.verify_sdk(
            tmp_path,
            [
                {
                    "path": "usr/lib/libutmps.a",
                    "type": "file",
                    "mode": 0o644,
                    "uid": path.stat().st_uid,
                    "gid": path.stat().st_gid,
                    "size": 15,
                    "sha256": "0" * 64,
                }
            ],
        )


def test_delivered_notice_is_rehashed_against_the_sealed_source_graph(
    sources, tmp_path
):
    assets = ROOT / "docker/alpine-busybox"
    recipe = sources.load_sources(assets / "static-sources.json")
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    shutil.copy2(assets / "static-sources.json", delivery / "static-sources.json")
    for component in recipe["components"]:
        target = delivery / component["name"]
        target.mkdir()
        unpack = tmp_path / component["name"]
        sources.safe_extract(assets / component["aports"]["file"], unpack)
        shutil.copytree(unpack / component["name"], target / "aports")
        shutil.copy2(
            assets / component["source"]["file"],
            target / component["source"]["filename"],
        )
        shutil.copy2(assets / component["notice"]["file"], target / "COPYING")
    sources.verify_delivery(delivery)
    (delivery / "utmps/COPYING").write_bytes(b"changed delivered notice")
    with pytest.raises(sources.BuildError, match="delivered static"):
        sources.verify_delivery(delivery)
