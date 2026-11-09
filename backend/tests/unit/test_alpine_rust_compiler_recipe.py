"""Whole historical recipe trees bind the distro configuration, not just labels."""

import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMITS = {
    "1.96.0": "cc20b977f6a8ecdc3713089cd0957bae0b040026",
    "1.95.0": "a988ec937b308221d5cb1d5eca2c0a7b1e7e6de4",
}


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_rust_source_inputs", ROOT / "scripts/alpine_rust_source_inputs.py"
    )
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def recorded(tmp_path):
    document = json.loads(
        gzip.decompress(
            (
                ROOT / "backend/tests/fixtures/alpine/rust-historical-recipes.json.gz"
            ).read_bytes()
        )
    )
    for name, encoded in document["files"].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
        assert hashlib.sha256(path.read_bytes()).hexdigest() == document["sha256"][name]
    return tmp_path


def objects(root, version):
    directory = root / version / "objects"
    commit = (directory / ("commit-" + COMMITS[version])).read_bytes()
    trees = {
        p.name.removeprefix("tree-"): p.read_bytes() for p in directory.glob("tree-*")
    }
    return commit, trees


@pytest.mark.parametrize("version", ["1.95.0", "1.96.0"])
def test_complete_original_recipe_tree_joins_original_commit_and_distro_patches(
    inputs, recorded, version
):
    commit, trees = objects(recorded, version)
    result = inputs.compiler_recipe(
        recorded / version / "recipe.tar", version, commit, trees
    )
    assert len(result["entries"]) == 12
    assert len(result["checksums"]) == 12
    assert sum(name.endswith(".patch") for name in result["entries"]) == 10
    recipe = result["entries"]["APKBUILD"]["bytes"]
    assert ("pkgver=" + version + "\n").encode() in recipe
    assert b"--enable-locked-deps" in recipe and b"--enable-vendor" in recipe
    assert b"crt-static=false" in recipe
    assert result["entries"]["gcc-eh-libunwind.patch"]["bytes"]


@pytest.mark.parametrize(
    "mutation",
    ["commit", "tree", "missing-tree", "archive", "other-version", "unknown-version"],
)
def test_original_compiler_configuration_cannot_be_replaced_by_same_version_text(
    inputs, recorded, mutation
):
    version = "1.96.0"
    archive = recorded / version / "recipe.tar"
    commit, trees = objects(recorded, version)
    if mutation == "commit":
        commit += b"\n"
    elif mutation == "tree":
        trees[next(iter(trees))] += b"\n"
    elif mutation == "missing-tree":
        trees.clear()
    elif mutation == "archive":
        data = archive.read_bytes()
        archive.write_bytes(data.replace(b"crt-static=false", b"crt-static=true ", 1))
    elif mutation == "other-version":
        archive = recorded / "1.95.0" / "recipe.tar"
    else:
        version = "1.96.1"
    with pytest.raises(inputs.InputError):
        inputs.compiler_recipe(archive, version, commit, trees)
