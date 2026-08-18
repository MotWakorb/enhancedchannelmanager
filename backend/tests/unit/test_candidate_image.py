"""Candidate archive identity must fail closed before scan or publication."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "candidate_image.py"


@pytest.fixture(scope="module")
def candidate():
    spec = importlib.util.spec_from_file_location("candidate_image", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(path: Path) -> str:
    manifest = b"candidate-manifest"
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    index = json.dumps({"manifests": [{"digest": digest}]}).encode()
    with tarfile.open(path, "w") as archive:
        for name, value in (("index.json", index), (f"blobs/sha256/{digest[7:]}", manifest)):
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    return digest


def test_exact_oci_manifest_digest_is_returned(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    expected = _archive(archive)
    assert candidate.verify_archive(archive) == expected


def test_digest_substitution_is_rejected(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    _archive(archive)
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive, "sha256:" + "0" * 64)


def test_missing_or_empty_manifest_digest_is_rejected(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    with tarfile.open(archive, "w") as output:
        value = b'{"manifests": []}'
        info = tarfile.TarInfo("index.json")
        info.size = len(value)
        output.addfile(info, io.BytesIO(value))
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive)
