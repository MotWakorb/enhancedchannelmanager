"""Real reviewed GNU source plan; transport stubs never claim source builds."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def preparer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_musllinux_gnu_sources",
        ROOT / "scripts/prepare_musllinux_gnu_sources.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_plan_covers_all27_deliveries_and21_unique_original_inputs(preparer):
    manifest, inputs = preparer.source_inputs()
    assert len(inputs) == 21
    assert (
        sum(len(row["source_files"]) for row in manifest["source_closures"].values())
        == 27
    )
    assert sum("local" in row for row in inputs) == 12
    assert sum("url" in row for row in inputs) == 9
    assert {row["sha256"] for row in inputs} == {
        row["sha256"]
        for family in manifest["source_closures"].values()
        for row in family["source_files"]
    }
    assert all(type(row["size"]) is int and row["size"] > 0 for row in inputs)
    for row in inputs:
        if "local" in row:
            assert preparer.sha256(ROOT / row["local"]) == row["sha256"]
        else:
            assert row["url"].startswith("https://") and "latest" not in row["url"]
    assert {row["status"] for row in manifest["source_closures"].values()} == {
        "AUTHENTICATED_PROVIDER_SOURCE",
        "REPRODUCED_CORRESPONDING_SOURCE",
    }


@pytest.mark.parametrize(
    "defect",
    [
        "existing-work",
        "existing-output",
        "alias-parent",
        "overlap",
        "wrong-local-input",
    ],
)
def test_preparation_rejects_unsafe_locations_and_changed_retained_assets_before_fetch(
    preparer, monkeypatch, tmp_path, defect
):
    work, output = tmp_path / "work", tmp_path / "sources"
    if defect == "existing-work":
        work.mkdir()
    elif defect == "existing-output":
        output.mkdir()
    elif defect == "alias-parent":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        output = alias / "sources"
    elif defect == "overlap":
        output = work / "sources"
    else:
        original = preparer.source_inputs

        def changed():
            manifest, plan = original()
            local = next(row for row in plan if "local" in row)
            local["sha256"] = "0" * 64
            return manifest, plan

        monkeypatch.setattr(preparer, "source_inputs", changed)

    def forbidden(*args, **kwargs):
        pytest.fail("unsafe input must fail before network")

    monkeypatch.setattr(preparer, "fetch_input", forbidden)
    with pytest.raises(preparer.AttributionError):
        preparer.prepare(work, output)


def test_complete_exact_offline_cache_is_not_a_missing_input_network_fallback(
    preparer, tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("incomplete explicit cache cannot use network")

    monkeypatch.setattr(preparer, "fetch_input", forbidden)
    with pytest.raises(preparer.AttributionError):
        preparer.prepare(tmp_path / "work", tmp_path / "sources", cache=cache)
