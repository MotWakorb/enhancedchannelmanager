"""Delivered source inputs must remain exact, including public evidence/layout."""

import base64
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_rust_sources", ROOT / "scripts/verify_alpine_rust_sources.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded(tmp_path):
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT
                / "backend/tests/fixtures/alpine/rust-release-public-evidence.json.gz"
            ).read_bytes()
        )
    )
    root = tmp_path / "bundle"
    root.mkdir()
    for name, data in fixture["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
    return root


def test_original_rust_release_public_evidence_is_bound_without_importing_runtime_keys(
    consumer, recorded
):
    used = set()
    result = consumer.release_evidence(recorded, used)
    assert result["fingerprint"] == "108F66205EAEB0AAA8DD5E1C85AB96E6FA1BE5FE"
    assert len(used) == 3
    assert result["runtime_gpg_executed"] is False
    consumer.exact_layout(recorded, used)


@pytest.mark.parametrize("mutation", ["key", "signature", "missing", "symlink"])
def test_signing_evidence_is_not_authenticated_by_a_preparation_receipt(
    consumer, recorded, mutation
):
    path = recorded / "compilers/1.95.0/rustc-1.95.0-src.tar.xz.asc"
    if mutation == "key":
        (recorded / "rust-key.gpg.ascii").write_bytes(b"another key")
    elif mutation == "signature":
        path.write_bytes(b"another signature")
    elif mutation == "missing":
        path.unlink()
    else:
        path.unlink()
        path.symlink_to("../../rust-key.gpg.ascii")
    with pytest.raises(consumer.InputError):
        consumer.release_evidence(recorded, set())


@pytest.mark.parametrize(
    "mutation",
    [
        "extra-file",
        "extra-directory",
        "directory-symlink",
        "file-symlink",
        "missing",
        "root-symlink",
    ],
)
def test_an_unindexed_payload_or_redirecting_directory_cannot_hide_in_the_bundle(
    consumer, recorded, tmp_path, mutation
):
    used = set()
    consumer.release_evidence(recorded, used)
    if mutation == "extra-file":
        (recorded / "unattributed").write_bytes(b"unexpected")
    elif mutation == "extra-directory":
        (recorded / "unattributed").mkdir()
    elif mutation == "directory-symlink":
        (recorded / "redirect").symlink_to("compilers", target_is_directory=True)
    elif mutation == "file-symlink":
        (recorded / "redirect").symlink_to("rust-key.gpg.ascii")
    elif mutation == "missing":
        (recorded / "rust-key.gpg.ascii").unlink()
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(recorded, target_is_directory=True)
        recorded = alias
    with pytest.raises(consumer.InputError):
        consumer.exact_layout(recorded, used)


def test_empty_or_self_declared_clearance_inputs_do_not_grant_runtime_source_acceptance(
    consumer, tmp_path
):
    root = tmp_path / "nested"
    root.mkdir()
    (root / "index.json").write_text('{"full_runtime_source_acceptance":true}')
    external = tmp_path / "external"
    external.mkdir()
    with pytest.raises(consumer.InputError):
        consumer.verify(root, external, {})


@pytest.mark.parametrize(
    "mutation", [None, "same-size-bytes", "symlink", "parent-symlink", "missing"]
)
def test_live_selected_elf_bytes_are_compared_to_the_original_payload_not_a_banner(
    consumer, tmp_path, mutation
):
    data = gzip.decompress(
        (
            ROOT / "backend/tests/fixtures/alpine/rav1e-arm64-original-elf.gz"
        ).read_bytes()
    )
    root = tmp_path / "installed"
    root.mkdir()
    name = "usr/lib/librav1e.so.0.8.1"
    path = root / name
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    if mutation == "same-size-bytes":
        changed = bytearray(data)
        changed[128] ^= 1
        path.write_bytes(changed)
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to("elsewhere")
    elif mutation == "parent-symlink":
        target = tmp_path / "other"
        path.parent.rename(target)
        path.parent.symlink_to(target, target_is_directory=True)
    elif mutation == "missing":
        path.unlink()
    if mutation is None:
        consumer.live_elf(root, name, data)
    else:
        with pytest.raises(consumer.InputError):
            consumer.live_elf(root, name, data)
