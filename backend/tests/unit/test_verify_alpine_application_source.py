"""The candidate cannot silently build a different application from a PR merge."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/verify_alpine_application_source.py"
VERSION_TOUCHPOINTS = (
    "backend/main.py",
    "backend/routers/backup.py",
    "frontend/package.json",
)


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


def test_version_bump_source_preflight_accepts_the_complete_committed_tree(tmp_path):
    """Exercise the same pinned-manifest evidence producer as native CI."""
    output = tmp_path / "application-source.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-file", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(output.read_text())
    manifest_path = ROOT / "docker/alpine-native/application-source.json"
    manifest = json.loads(manifest_path.read_text())
    assert record["status"] == "PINNED_APPLICATION_SOURCE_ONLY"
    assert record["file_count"] == len(manifest["files"])
    assert (
        record["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("relative", VERSION_TOUCHPOINTS)
def test_version_bump_source_preflight_rejects_each_stale_touchpoint(
    verifier, tmp_path, relative
):
    """Version declarations remain exact source bytes, never exempt metadata."""
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    source_root = tmp_path / "source"
    for row in manifest["files"]:
        source = ROOT / row["path"]
        target = source_root / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
    version = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    release, separator, build = version.rpartition("-")
    stale = (
        f"{release}-{int(build) - 1:04d}"
        if separator and build.isdigit() and int(build) > 0
        else "0.0.0-0000"
    )
    assert stale != version
    path = source_root / relative
    original = path.read_bytes()
    assert original.count(version.encode()) == 1
    path.write_bytes(original.replace(version.encode(), stale.encode()))
    output = tmp_path / "application-source.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(source_root),
            "--output-file",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "application source bytes or executable bit differ: " + relative in (
        result.stderr
    )
    assert not output.exists()


def test_real_recorded_application_map_retains_date_and_container_adaptations(
    verifier,
):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    assert len(manifest["files"]) == 1081
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
    result = verifier.verify(ROOT, manifest)
    assert {r["path"] for r in result["adaptations"]} == {
        "backend/date_placeholders.py",
        "mcp-server/Dockerfile",
    }


def test_reviewed_group_delete_snapshot_extends_the_complete_previous_inventory(
    verifier,
):
    """Reconstruct the preceding inventory, including path/type/mode/hash data."""
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    update = manifest["approved_application_update"]
    assert update["application_commit"] == "4dc2601d578235c3111c81447ce2c00b05c2ebcb"
    assert update["base_commit"] == "95fc8e4e88c22744327de0ed75767ce5e8b28af9"
    assert update["bead"] == "enhancedchannelmanager-jtrlf"
    assert update["version"] == "0.18.2-0067"
    assert (
        update["previous_manifest_sha256"]
        == "a3520f9549250c896d90c5d8477fb5cdf24bc56f9829e83ce9eff4ba2c9e2156"
    )
    assert set(update["changed_files"]) == {
        "backend/main.py",
        "backend/routers/backup.py",
        "frontend/package.json",
        "backend/routers/channel_groups.py",
        "backend/routers/channels.py",
    }
    assert update["added_files"] == ["backend/channel_group_delete.py"]
    rows = {row["path"]: row for row in manifest["files"]}
    assert rows["backend/channel_group_delete.py"]["type"] == "file"
    assert rows["backend/channel_group_delete.py"]["executable"] is False
    previous = []
    for row in manifest["files"]:
        if row["path"] in update["added_files"]:
            continue
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1080
    # Pin independently of the update metadata: resealing a dropped path,
    # changed executable/type or unrelated hash cannot bless a smaller snapshot.
    assert (
        hashlib.sha256(
            json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == "09bd8191d77d71612c0fa3ab9ac3be727da3ca43164934116c3a384ba1f568fd"
    )
    result = verifier.verify(ROOT, manifest)
    assert result["approved_application_update"] == update


@pytest.mark.parametrize("mutation", ["missing", "unlisted", "executable"])
def test_application_source_cli_rejects_incomplete_or_mode_changed_snapshot(
    verifier, tmp_path, mutation
):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    source_root = tmp_path / "source"
    for row in manifest["files"]:
        source = ROOT / row["path"]
        target = source_root / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
    helper = source_root / "backend/channel_group_delete.py"
    if mutation == "missing":
        helper.unlink()
        expected = "regular application source file required"
    elif mutation == "unlisted":
        (source_root / "backend/unlisted.py").write_text("unreviewed source")
        expected = "application source coverage mismatch"
    else:
        helper.chmod(0o755)
        expected = "application source bytes or executable bit differ"
    output = tmp_path / "application-source.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(source_root),
            "--output-file",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert expected in result.stderr
    assert not output.exists()


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


@pytest.mark.parametrize(
    "cache_file",
    [".gitignore", "constants/01e8b1e3e53c700f", "examples/a-recorded-example"],
)
def test_generated_hypothesis_cache_is_not_shipped_application_source(
    verifier, tmp_path, cache_file
):
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
    cache = source.parent / ".hypothesis" / cache_file
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("recorded generated test-cache bytes")
    assert verifier.verify(tmp_path, manifest)["file_count"] == 1
    assert "**/.hypothesis" in (ROOT / ".dockerignore").read_text().splitlines()
    # The exact cache name is not a blanket exemption for hidden/new code.
    unexpected = source.parent / ".hypothesis_extra/service.py"
    unexpected.parent.mkdir()
    unexpected.write_text("unreviewed application code")
    with pytest.raises(verifier.SourceError, match="source coverage"):
        verifier.verify(tmp_path, manifest)
