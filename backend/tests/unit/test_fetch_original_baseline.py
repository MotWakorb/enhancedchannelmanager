"""Retention fallback preserves the exact original ZIP and OCI subject."""

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def fetcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "fetch_original_baseline", ROOT / "scripts/fetch_original_baseline.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_real_github_missing_artifact_response_allows_pinned_fallback(
    fetcher, tmp_path, monkeypatch
):
    # Real `gh api .../actions/artifacts/0` response, including string status.
    error = b'{"message":"Not Found","documentation_url":"https://docs.github.com/rest/actions/artifacts#get-an-artifact","status":"404"}'
    original = b"original sealed archive bytes"
    mirror = tmp_path / "mirror.zip"
    with zipfile.ZipFile(mirror, "w") as z:
        z.writestr("original.zip", original)
    primary = {"id": 1, "sha256": hashlib.sha256(original).hexdigest()}
    secondary = {
        "id": 2,
        "sha256": hashlib.sha256(mirror.read_bytes()).hexdigest(),
        "member": "original.zip",
    }
    monkeypatch.setattr(fetcher, "inputs", lambda arch: (primary, secondary))

    def api(command, *, stdout, **kwargs):
        if "/1/zip" in command[-1]:
            stdout.write(error)
            return subprocess.CompletedProcess(
                command, 1, stderr=b"gh: Not Found (HTTP 404)"
            )
        stdout.write(mirror.read_bytes())
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    monkeypatch.setattr(fetcher.subprocess, "run", api)
    result = fetcher.fetch("arm64", tmp_path / "original.zip")
    assert (tmp_path / "original.zip").read_bytes() == original
    assert result["method"] == "PINNED_RETENTION_MIRROR_SAME_ORIGINAL_BYTES"
    assert (
        json.loads((tmp_path / "original.zip.primary-error.json").read_bytes())[
            "status"
        ]
        == "404"
    )


def test_wrong_primary_bytes_are_not_silently_replaced_by_a_mirror(
    fetcher, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        fetcher, "inputs", lambda arch: ({"id": 1, "sha256": "0" * 64}, {"id": 2})
    )

    def api(command, *, stdout, **kwargs):
        assert "/1/zip" in command[-1]
        stdout.write(b"wrong transport bytes")
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    monkeypatch.setattr(fetcher.subprocess, "run", api)
    with pytest.raises(fetcher.MatrixError, match="identity"):
        fetcher.fetch("amd64", tmp_path / "original.zip")


def test_permission_errors_are_not_misreported_as_artifact_expiry(
    fetcher, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        fetcher, "inputs", lambda arch: ({"id": 1, "sha256": "0" * 64}, {"id": 2})
    )

    def api(command, *, stdout, **kwargs):
        stdout.write(b'{"message":"Bad credentials","status":"401"}')
        return subprocess.CompletedProcess(
            command, 1, stderr=b"gh: Bad credentials (HTTP 401)"
        )

    monkeypatch.setattr(fetcher.subprocess, "run", api)
    with pytest.raises(fetcher.MatrixError, match="expiry"):
        fetcher.fetch("amd64", tmp_path / "original.zip")


def test_dangling_evidence_symlinks_are_not_overwritten(fetcher, tmp_path, monkeypatch):
    target = tmp_path/'original.zip'
    target.with_name(target.name+'.mirror.zip.stderr').symlink_to(tmp_path/'operator-data')
    monkeypatch.setattr(fetcher, 'download', lambda *a: pytest.fail('download before fresh evidence validation'))
    with pytest.raises(fetcher.MatrixError, match='fresh'):
        fetcher.fetch('arm64', target)
    assert not (tmp_path/'operator-data').exists()
