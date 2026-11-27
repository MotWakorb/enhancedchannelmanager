"""Retained producer data participates in the Alpine source fingerprint."""

import hashlib
import importlib.util
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_retained_producer_bytes_are_fingerprinted_and_cannot_be_links(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "alpine_sbom_inputs", ROOT / "scripts/generate_sbom.py"
    )
    sbom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbom)
    for directory in ("docker/alpine-native", "docker/alpine-busybox"):
        target = tmp_path / directory
        target.mkdir(parents=True)
        for source in (ROOT / directory).glob("*.json"):
            shutil.copyfile(source, target / source.name)
    retained = tmp_path / "scripts/retained"
    retained.mkdir(parents=True)
    relative = "scripts/retained/package_alpine_native-v1.py.gz"
    producer = tmp_path / relative
    original = (ROOT / relative).read_bytes()
    producer.write_bytes(original)
    sources = {}
    sbom._alpine_runtime_native_inputs(tmp_path, sources)
    assert sources[relative] == hashlib.sha256(original).hexdigest()
    producer.write_bytes(original + b"changed")
    changed = {}
    sbom._alpine_runtime_native_inputs(tmp_path, changed)
    assert sources[relative] != changed[relative]
    producer.unlink()
    producer.symlink_to(ROOT / relative)
    with pytest.raises(sbom.SbomError, match="regular retained verification input"):
        sbom._alpine_runtime_native_inputs(tmp_path, {})
