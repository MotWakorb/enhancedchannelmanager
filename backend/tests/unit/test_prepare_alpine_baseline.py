"""Old baseline images come from pinned immutable artifacts, never mutable tags."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def preparer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_alpine_baseline", ROOT / "scripts/prepare_alpine_baseline.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zip_byte_identity_precedes_any_archive_extraction(preparer, tmp_path):
    archive = tmp_path / "baseline.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../../operator-data", b"never extract")
    with pytest.raises(preparer.MatrixError, match="artifact identity"):
        preparer.checked_zip(archive, "0" * 64)
    assert not (tmp_path / "operator-data").exists()


def test_zip_does_not_accept_duplicate_or_unexpected_image_payloads(preparer, tmp_path):
    archive = tmp_path / "baseline.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("old.oci.tar", b"one")
        z.writestr("extra.oci.tar", b"two")
    with pytest.raises(preparer.MatrixError, match="archive member"):
        preparer.extract_image(
            archive, preparer.sha256(archive), "old.oci.tar", tmp_path / "output"
        )


def test_only_fresh_disjoint_evidence_locations_are_allowed(preparer, tmp_path):
    baseline, native = tmp_path / "baseline.zip", tmp_path / "native.zip"
    baseline.write_bytes(b"preserve baseline")
    native.write_bytes(b"preserve native")
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(preparer.MatrixError, match="fresh"):
        preparer.prepare("arm64", baseline, native, output)
    assert baseline.read_bytes() == b"preserve baseline"


def test_current_native_build_does_not_silently_use_a_historical_package_snapshot(
    preparer, tmp_path, monkeypatch
):
    original = tmp_path / "original.zip"
    original.write_bytes(b"identity reader wired separately in this unit test")
    descriptor = preparer.load_baselines()
    identity = descriptor["architectures"]["arm64"]["identity"]
    monkeypatch.setattr(preparer, "extract_image", lambda *args: None)
    monkeypatch.setattr(preparer, "runtime_config", lambda *args: identity)
    result = preparer.prepare("arm64", original, None, tmp_path / "original-only")
    assert result["status"] == "VERIFIED_ORIGINAL_IMAGE_ONLY"
    assert "native_artifact_sha256" not in result and "package" not in result
    assert not (tmp_path / "original-only/packages").exists()
