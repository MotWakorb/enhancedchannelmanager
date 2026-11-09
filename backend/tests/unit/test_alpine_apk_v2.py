"""Inspect signed APK payload bindings without extracting or executing files."""

import gzip
import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def inspector(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_apk_v2", ROOT / "scripts/alpine_apk_v2.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apk(tmp_path, name="bin/busybox", bad_datahash=False):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as tar:
        member = tarfile.TarInfo(name)
        member.mode = 0o755
        member.size = 7
        tar.addfile(member, io.BytesIO(b"payload"))
    data = gzip.compress(payload.getvalue(), mtime=0)
    control = io.BytesIO()
    checksum = "0" * 64 if bad_datahash else hashlib.sha256(data).hexdigest()
    text = (
        "pkgname = busybox\npkgver = 1.37.0-r31\narch = x86_64\ndatahash = "
        + checksum
        + "\n"
    ).encode()
    with tarfile.open(fileobj=control, mode="w") as tar:
        member = tarfile.TarInfo(".PKGINFO")
        member.size = len(text)
        tar.addfile(member, io.BytesIO(text))
    path = tmp_path / "package.apk"
    path.write_bytes(
        gzip.compress(b"not signature clearance", mtime=0)
        + gzip.compress(control.getvalue(), mtime=0)
        + data
    )
    return path


def test_inventory_binds_payload_bytes_but_never_claims_signature_clearance(
    inspector, tmp_path
):
    result = inspector.inspect_apk(apk(tmp_path))
    assert result["fields"]["pkgname"] == ["busybox"]
    assert result["payload"][0]["path"] == "bin/busybox"
    assert result["payload"][0]["sha256"] == hashlib.sha256(b"payload").hexdigest()
    assert result["scope"] == "PAYLOAD_BINDING_ONLY_SIGNATURE_VERIFICATION_REQUIRED"
    assert not (tmp_path / "bin").exists()


def test_signed_control_datahash_is_not_ignored(inspector, tmp_path):
    with pytest.raises(inspector.AttributionError, match="datahash"):
        inspector.inspect_apk(apk(tmp_path, bad_datahash=True))


def test_archive_path_is_data_not_an_extraction_instruction(inspector, tmp_path):
    with pytest.raises(inspector.AttributionError, match="unsafe"):
        inspector.inspect_apk(apk(tmp_path, name="../operator-data"))
