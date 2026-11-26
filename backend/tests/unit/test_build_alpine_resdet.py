"""Alpine retains the original resdet recipe and source, not a new reader set."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_alpine_resdet", ROOT / "scripts/build_alpine_resdet.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_input_is_the_original_manifest_pinned_commit(builder):
    source = builder.source_input(ROOT / "sbom/native-dependencies.json")
    assert (
        source["sha256"]
        == "7c818aae4131b1fb4b36c20d3d8e4009e6ace209468ed8272747e5a42657be3d"
    )
    assert source["url"].endswith("/89d078b932eeb259d8e78d07d875af2623734cf4")
    assert source["size"] == 6685870


def test_changed_resdet_manifest_is_not_an_accepted_recipe_override(builder, tmp_path):
    path = tmp_path / "recipe.json"
    path.write_text("{}")
    with pytest.raises(builder.BuildError, match="recipe identity"):
        builder.source_input(path)
