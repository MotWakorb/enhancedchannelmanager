"""Downloads are selected by reviewed whole-wheel identities, not mutable resolution."""

import importlib.util
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


def test_recorded_actual_selection_has_exactly_74_unchanged_pins_per_arch(preparer):
    for arch in ["amd64", "arm64"]:
        rows = preparer.selection(arch)
        assert len(rows) == 74 and len({row["name"] for row in rows}) == 74
        assert all(
            row["pypi_artifact"]["url"].startswith("https://files.pythonhosted.org/")
            for row in rows
        )
        assert sum(bool(row["native_files"]) for row in rows) == 22


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
