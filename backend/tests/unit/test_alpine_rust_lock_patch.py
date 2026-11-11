"""Replay the real source-to-lock transition without executing patch or Cargo."""

import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_rust_source_inputs", ROOT / "scripts/alpine_rust_source_inputs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded():
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT / "backend/tests/fixtures/alpine/rust-original-lock-patch.json.gz"
            ).read_bytes()
        )
    )
    files = {name: base64.b64decode(data) for name, data in fixture["files"].items()}
    assert all(
        hashlib.sha256(data).hexdigest() == fixture["sha256"][name]
        for name, data in files.items()
    )
    return files


@pytest.mark.parametrize("origin", ["glycin", "libglycin", "rav1e"])
def test_real_original_lock_and_exact_distro_patch_reproduce_reviewed_lock(
    inputs, recorded, origin
):
    original = recorded[
        ("rav1e" if origin == "rav1e" else "glycin") + "-original-Cargo.lock"
    ]
    result = inputs.derive_lock(
        origin, original, None if origin == "rav1e" else recorded["bump-nix.patch"]
    )
    assert hashlib.sha256(result).hexdigest() == inputs.LOCKS[origin]
    if origin == "rav1e":
        assert result == original
    else:
        assert result != original
        assert b'name = "nix"\nversion = "0.31.2"' in result


@pytest.mark.parametrize(
    "mutation", ["source", "patch", "missing-patch", "unneeded-patch", "other-origin"]
)
def test_lock_replay_never_ignores_changed_source_or_an_unreviewed_patch(
    inputs, recorded, mutation
):
    original = recorded["glycin-original-Cargo.lock"]
    patch = recorded["bump-nix.patch"]
    origin = "glycin"
    if mutation == "source":
        original += b"\n"
    elif mutation == "patch":
        patch += b"\n"
    elif mutation == "missing-patch":
        patch = None
    elif mutation == "unneeded-patch":
        origin = "rav1e"
        original = recorded["rav1e-original-Cargo.lock"]
    else:
        origin = "other"
    with pytest.raises(inputs.InputError):
        inputs.derive_lock(origin, original, patch)
