"""Source delivery copies complete reviewed inventories, not asserted labels."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def collector(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "collect_musllinux_gnu_sources",
        ROOT / "scripts/collect_musllinux_gnu_sources.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(collector, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    item = cache / "temporary"
    item.write_bytes(b"synthetic source-copy fixture, not upstream attribution")
    digest = collector.sha256(item)
    item.rename(cache / digest)
    manifest = {
        "source_closures": {
            "gcc-source": {
                "status": "UNRESOLVED_BUILD_RECIPE_REVISION",
                "source_files": [{"path": "source.tar.xz", "sha256": digest}],
            }
        }
    }
    return manifest, cache, digest


def test_delivery_is_exact_but_never_upgrades_a_source_authentication_status(
    collector, tmp_path
):
    manifest, cache, digest = fixture(collector, tmp_path)
    destination = tmp_path / "delivery"
    receipt = collector.collect(manifest, cache, destination)
    assert collector.sha256(destination / "gcc-source/source.tar.xz") == digest
    assert receipt["status"] == "SOURCE_BYTES_COPIED_ONLY"
    assert receipt["source_statuses"] == {
        "gcc-source": "UNRESOLVED_BUILD_RECIPE_REVISION"
    }
    assert (
        manifest["source_closures"]["gcc-source"]["status"]
        == "UNRESOLVED_BUILD_RECIPE_REVISION"
    )


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "file\nname", "dir/../../escape"]
)
def test_manifest_source_paths_cannot_escape_or_ambiguate_inventory(
    collector, tmp_path, path
):
    manifest, cache, _ = fixture(collector, tmp_path)
    manifest["source_closures"]["gcc-source"]["source_files"][0]["path"] = path
    with pytest.raises(collector.AttributionError, match="source path"):
        collector.collect(manifest, cache, tmp_path / "delivery")
    assert not (tmp_path / "escape").exists()


def test_source_cache_tampering_or_partial_inventory_fails_before_delivery(
    collector, tmp_path
):
    manifest, cache, digest = fixture(collector, tmp_path)
    (cache / digest).write_bytes(b"changed")
    with pytest.raises(
        collector.AttributionError, match="cached provider/tool identity"
    ):
        collector.collect(manifest, cache, tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()


def test_source_delivery_cannot_write_into_cache_or_overwrite_evidence(
    collector, tmp_path
):
    manifest, cache, _ = fixture(collector, tmp_path)
    with pytest.raises(collector.AttributionError, match="disjoint"):
        collector.collect(manifest, cache, cache / "delivery")
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "operator-work").write_bytes(b"preserve")
    with pytest.raises(collector.AttributionError, match="fresh"):
        collector.collect(manifest, cache, existing)
    assert (existing / "operator-work").read_bytes() == b"preserve"


def test_every_family_needs_a_nonempty_nonduplicated_reviewed_inventory(
    collector, tmp_path
):
    manifest, cache, _ = fixture(collector, tmp_path)
    row = manifest["source_closures"]["gcc-source"]["source_files"][0]
    for rows in [[], [row, row]]:
        manifest["source_closures"]["gcc-source"]["source_files"] = rows
        with pytest.raises(collector.AttributionError, match="source inventory"):
            collector.collect(manifest, cache, tmp_path / "delivery")
