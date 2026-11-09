"""The candidate cannot silently build a different application from a PR merge."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_application_source",
        ROOT / "scripts/verify_alpine_application_source.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_recorded_application_map_allows_only_the_existing_date_adaptation(
    verifier,
):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    assert len(manifest["files"]) == 1080
    assert (
        manifest["baseline_application_commit"]
        == "06cc4bf5a1b9eb1f6ef29f617513d237cda2f531"
    )
    changed = [r for r in manifest["files"] if "adaptation_commit" in r]
    assert len(changed) == 1 and changed[0]["path"] == "backend/date_placeholders.py"
    assert changed[0]["adaptation_commit"] == "df80c49d96d33f6747d5610e98bea732a8618c30"
    assert all(
        r["path"] != "backend/requirements.txt" or "adaptation_commit" not in r
        for r in manifest["files"]
    )


def test_changed_missing_or_extra_application_source_fails(verifier, tmp_path):
    source = tmp_path / "backend/service.py"
    source.parent.mkdir()
    source.write_bytes(b"original application bytes")
    manifest = {
        "files": [
            {
                "path": "backend/service.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "type": "file",
                "executable": False,
            }
        ]
    }
    verifier.verify(tmp_path, manifest)
    source.write_bytes(b"different application")
    with pytest.raises(verifier.SourceError, match="source bytes"):
        verifier.verify(tmp_path, manifest)
    source.write_bytes(b"original application bytes")
    (source.parent / "new_service.py").write_bytes(b"unreviewed service")
    with pytest.raises(verifier.SourceError, match="source coverage"):
        verifier.verify(tmp_path, manifest)


def test_source_symlinks_cannot_redirect_to_operator_files(verifier, tmp_path):
    directory = tmp_path / "backend"
    directory.mkdir()
    (directory / "service.py").symlink_to(tmp_path / "operator-file")
    with pytest.raises(verifier.SourceError, match="source file"):
        verifier.verify(
            tmp_path,
            {
                "files": [
                    {
                        "path": "backend/service.py",
                        "type": "file",
                        "sha256": "0" * 64,
                        "executable": False,
                    }
                ]
            },
        )
