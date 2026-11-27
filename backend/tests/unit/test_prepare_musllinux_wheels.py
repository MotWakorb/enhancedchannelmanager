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


# The 72 rows other than the two pure-Python security replacements, hashed
# before the Mako replacement was applied.
@pytest.mark.parametrize("arch,unchanged_rows_sha256", [
    ("amd64", "49f631eb243a45d386153ce93fc7530b98ea754ad1243ad3d32ef8092df36349"),
    ("arm64", "f05ff11e776330c36f6b47a1615528c5c700a859326b4e4a5d810e5ef21e014d"),
])
def test_pure_security_wheel_refreshes_retain_all_other_artifact_bytes(preparer, arch, unchanged_rows_sha256):
    rows = preparer.selection(arch)
    unchanged = [row for row in rows if row["name"] not in {"dnspython", "mako"}]
    assert len(unchanged) == 72
    assert hashlib.sha256(json.dumps(unchanged, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == unchanged_rows_sha256


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
@pytest.mark.parametrize("name,version,wheel_sha256", [
    ("dnspython", "2.9.2", "af15f7d385a6ae2fb35a796fde2138004c8c42c7389090e2addc83e3b3340fd8"),
    ("mako", "1.4.2", "e70a2a4308952b33a21208bfd6725da79fae5a8c224578f8cfdeb60e5ace03df"),
])
def test_pure_security_wheel_is_bound_to_its_recorded_pypi_artifact(preparer, arch, name, version, wheel_sha256):
    rows = preparer.selection(arch)
    row = next(row for row in rows if row["name"] == name)
    assert row["version"] == version and row["sha256"] == wheel_sha256
    assert row["filename"] == f"{name}-{version}-py3-none-any.whl"
    assert row["purelib"] and row["tags"] == ["py3-none-any"]
    assert row["native_files"] == []
    metadata = ROOT / "docker/python-third-party/artifact-index" / row["pypi_artifact"]["metadata_file"]
    assert preparer.sha256(metadata) == row["pypi_artifact"]["metadata_sha256"]
    source = json.loads(metadata.read_text())
    wheel = next(item for item in source["urls"] if item["filename"] == row["filename"])
    assert wheel["digests"]["sha256"] == row["sha256"]
    assert wheel["url"] == row["pypi_artifact"]["url"]
    assert not wheel["yanked"]


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
