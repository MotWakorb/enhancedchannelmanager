"""CI source cache is a location, never a source/flag/security override."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def prepare(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_alpine_native_cache", ROOT / "scripts/prepare_alpine_native_cache.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_plan_covers_all_six_libraries_and_signed_ffmpeg(prepare):
    plan = prepare.inputs()
    assert len(plan) == 9
    assert {p["sha256"] for p in plan} >= {
        "7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3",
        "5f2880c7fea01f8694d30cb9d06362f77241ee67ce5926b157a226b18f08a538",
        "cbccae52b2c2ecc5d2757e407da567eb681241ff8dadce39d779a7219dbcf449",
        "03e9be69a30be4011f559da75328b6d7cea8ad921fabfbd551ce10bf45cdc992",
    }
    assert all(p["url"].startswith("https://") for p in plan)


def test_preparation_never_overwrites_existing_operator_or_evidence_files(
    prepare, tmp_path, monkeypatch
):
    output = tmp_path / "cache"
    output.mkdir()
    (output / "preserve").write_text("existing")
    monkeypatch.setattr(
        prepare,
        "fetch_input",
        lambda *a: pytest.fail("fetched before location validation"),
    )
    with pytest.raises(prepare.BuildError, match="fresh"):
        prepare.prepare(output)
    assert (output / "preserve").read_text() == "existing"
