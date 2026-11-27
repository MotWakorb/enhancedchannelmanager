"""Downloads are selected by reviewed whole-wheel identities, not mutable resolution."""

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def preparer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_musllinux_wheels", ROOT / "scripts/prepare_musllinux_wheels.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_actual_selection_has_exactly_74_current_pins_per_arch(preparer):
    for arch in ["amd64", "arm64"]:
        rows = preparer.selection(arch)
        assert len(rows) == 74 and len({row["name"] for row in rows}) == 74
        assert all(
            row["pypi_artifact"]["url"].startswith("https://files.pythonhosted.org/")
            for row in rows
        )
        assert sum(bool(row["native_files"]) for row in rows) == 22


@pytest.mark.parametrize("arch,unchanged_rows_sha256", [
    ("amd64", "25cacfeeb60243c9e2d2c6ec59c5722317e98cc13ddcb69249fe0d89f72e389c"),
    ("arm64", "8f01243b638c3199f5c0c298bcec4f4911cbcd6fe0e795e4f8912e41b2aaa867"),
])
def test_dns_security_wheel_refresh_retains_all_other_artifact_bytes(preparer, arch, unchanged_rows_sha256):
    rows = preparer.selection(arch)
    unchanged = [row for row in rows if row["name"] != "dnspython"]
    assert len(unchanged) == 73
    assert hashlib.sha256(json.dumps(unchanged, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == unchanged_rows_sha256
    dns = next(row for row in rows if row["name"] == "dnspython")
    assert dns["version"] == "2.9.2"
    assert dns["purelib"] and dns["tags"] == ["py3-none-any"]
    assert dns["native_files"] == []
    metadata = ROOT / "docker/python-third-party/artifact-index" / dns["pypi_artifact"]["metadata_file"]
    assert preparer.sha256(metadata) == dns["pypi_artifact"]["metadata_sha256"]
    source = json.loads(metadata.read_text())
    wheel = next(row for row in source["urls"] if row["filename"] == dns["filename"])
    assert wheel["digests"]["sha256"] == dns["sha256"]
    assert wheel["url"] == dns["pypi_artifact"]["url"]


def test_offline_cache_bytes_are_rehashed_not_relabelled_as_downloads(
    preparer, tmp_path, monkeypatch
):
    from hashlib import sha256

    cache = tmp_path / "cache"
    cache.mkdir()
    data = b"bound artifact bytes"
    digest = sha256(data).hexdigest()
    filename = "sample-1.0-py3-none-any.whl"
    (cache / filename).write_bytes(data)
    row = {
        "filename": filename,
        "sha256": digest,
        "pypi_artifact": {"url": "https://files.pythonhosted.org/sample.whl"},
    }
    monkeypatch.setattr(
        preparer,
        "urlopen",
        lambda *a, **k: pytest.fail("offline cache should not fetch"),
    )
    receipt = preparer.acquire(row, tmp_path / filename, cache)
    assert receipt["method"] == "REHASHED_LOCAL_WHOLE_WHEEL_CACHE"
    assert (tmp_path / filename).read_bytes() == data


def test_corrupt_cache_cannot_fall_back_to_silent_network_replacement(
    preparer, tmp_path
):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "sample.whl").write_bytes(b"bad")
    with pytest.raises(preparer.AttributionError, match="cache"):
        preparer.acquire(
            {"filename": "sample.whl", "sha256": "0" * 64}, tmp_path / "output", cache
        )
