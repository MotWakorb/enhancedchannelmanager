"""The Alpine gosu path keeps the previously reviewed compiler/source contract."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "build_alpine_gosu", ROOT / "scripts/build_alpine_gosu.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alpine_descriptor_retains_the_existing_source_modules_and_go_source(builder):
    recipe = builder.load_recipe(ROOT / "docker/alpine-native/gosu.json")
    old = json.loads((ROOT / "docker/native/manifest.json").read_text())
    assert recipe["gosu"] == old["gosu"]
    assert (
        recipe["go_source"] == old["architectures"]["arm64"]["go_toolchain"]["source"]
    )
    assert "1.26.10-alpine3.24@sha256:" in recipe["builder_image"]


@pytest.mark.parametrize("key", ["builder_image", "gosu", "go_source"])
def test_changed_owned_go_source_or_compiler_is_not_an_operator_override(
    builder, tmp_path, key
):
    recipe = json.loads((ROOT / "docker/alpine-native/gosu.json").read_text())
    recipe[key] = "unreviewed"
    p = tmp_path / "gosu.json"
    p.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="recipe identity"):
        builder.load_recipe(p)
