"""The Docker runtime must execute the config/rootfs of the verified OCI subject."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/candidate_image.py"


@pytest.fixture
def candidate():
    spec = importlib.util.spec_from_file_location("runtime_candidate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _archive(
    path,
    *,
    arch="amd64",
    corrupt=None,
    config_change=None,
    media_type=None,
    manifest_change=None,
):
    layer = b"synthetic rootfs tar bytes"
    compressed = (
        layer
        if media_type == "application/vnd.oci.image.layer.v1.tar"
        else gzip.compress(layer)
    )
    config = {
        "os": "linux",
        "architecture": arch,
        "rootfs": {"type": "layers", "diff_ids": [_digest(layer)]},
    }
    if config_change:
        config_change(config)
    config_bytes = json.dumps(config).encode()
    config_digest = _digest(config_bytes)
    manifest_document = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": config_digest},
        "layers": [
            {
                "mediaType": media_type
                or "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": _digest(compressed),
            }
        ],
    }
    if manifest_change:
        manifest_change(manifest_document)
    manifest = json.dumps(manifest_document).encode()
    manifest_digest = _digest(manifest)
    blobs = {
        "index.json": json.dumps({"manifests": [{"digest": manifest_digest}]}).encode(),
        f"blobs/sha256/{manifest_digest[7:]}": manifest,
        f"blobs/sha256/{config_digest[7:]}": config_bytes,
        f"blobs/sha256/{_digest(compressed)[7:]}": compressed,
    }
    if corrupt:
        key = f"blobs/sha256/{(config_digest if corrupt == 'config' else _digest(compressed))[7:]}"
        blobs[key] = b"substitution"
    with tarfile.open(path, "w") as output:
        for name, payload in blobs.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
    return {
        "ManifestDigest": manifest_digest,
        "ConfigDigest": config_digest,
        "OS": "linux",
        "Architecture": arch,
        "RootFSDiffIDs": [_digest(layer)],
    }


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_runtime_identity_is_hash_verified_config_not_manifest(
    candidate, tmp_path, arch
):
    path = tmp_path / "image.tar"
    expected = _archive(path, arch=arch)
    assert candidate.runtime_config(path, expected["ManifestDigest"], arch) == expected
    assert expected["ManifestDigest"] != expected["ConfigDigest"]


@pytest.mark.parametrize("corrupt", ["config", "layer"])
def test_config_and_layer_substitution_fail_closed(candidate, tmp_path, corrupt):
    path = tmp_path / "image.tar"
    expected = _archive(path, corrupt=corrupt)
    with pytest.raises(candidate.CandidateError):
        candidate.runtime_config(path, expected["ManifestDigest"], "amd64")


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(os="windows"),
        lambda c: c.update(architecture="arm64"),
        lambda c: c.update(rootfs={}),
        lambda c: c["rootfs"].update(type="unknown"),
        lambda c: c["rootfs"].update(diff_ids=[]),
        lambda c: c["rootfs"].update(diff_ids=["sha256:" + "0" * 64]),
        lambda c: c["rootfs"].update(diff_ids=["bad"]),
    ],
)
def test_runtime_config_rejects_platform_or_rootfs_mismatch(
    candidate, tmp_path, change
):
    path = tmp_path / "image.tar"
    expected = _archive(path, config_change=change)
    with pytest.raises(candidate.CandidateError):
        candidate.runtime_config(path, expected["ManifestDigest"], "amd64")


def test_runtime_config_rejects_other_candidate_digest(candidate, tmp_path):
    path = tmp_path / "image.tar"
    _archive(path)
    with pytest.raises(candidate.CandidateError):
        candidate.runtime_config(path, "sha256:" + "0" * 64, "amd64")


def test_uncompressed_oci_layer_is_hash_verified(candidate, tmp_path):
    path = tmp_path / "image.tar"
    expected = _archive(path, media_type="application/vnd.oci.image.layer.v1.tar")
    assert (
        candidate.runtime_config(path, expected["ManifestDigest"], "amd64") == expected
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(schemaVersion=0),
        lambda m: m.update(config=None),
        lambda m: m["config"].update(digest="bad"),
        lambda m: m["config"].update(digest="sha256:" + "0" * 64),
        lambda m: m.update(layers=[]),
        lambda m: m.update(layers=[None]),
        lambda m: m["layers"][0].update(
            mediaType="application/vnd.oci.image.layer.v1.tar+zstd"
        ),
    ],
)
def test_missing_or_unsupported_runtime_blobs_are_denied(candidate, tmp_path, change):
    path = tmp_path / "image.tar"
    expected = _archive(path, manifest_change=change)
    with pytest.raises(candidate.CandidateError):
        candidate.runtime_config(path, expected["ManifestDigest"], "amd64")


def _loaded(identity):
    return [
        {
            "Id": identity["ConfigDigest"],
            "Os": identity["OS"],
            "Architecture": identity["Architecture"],
            "RootFS": {"Type": "layers", "Layers": identity["RootFSDiffIDs"]},
        }
    ]


def test_docker_inspect_proves_exact_config_and_rootfs(candidate, tmp_path):
    expected = _archive(tmp_path / "image.tar")
    assert candidate.verify_loaded_config(expected, _loaded(expected)) == expected


@pytest.mark.parametrize(
    "change",
    [
        lambda i: i.update(Architecture=[]),
        lambda i: i.update(RootFSDiffIDs=[]),
        lambda i: i.update(ConfigDigest=None),
    ],
)
def test_loaded_check_denies_malformed_expected_identity(candidate, tmp_path, change):
    expected = _archive(tmp_path / "image.tar")
    loaded = _loaded(expected)
    change(expected)
    with pytest.raises(candidate.CandidateError):
        candidate.verify_loaded_config(expected, loaded)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d[0].update(Id="sha256:" + "0" * 64),
        lambda d: d[0].update(Os="windows"),
        lambda d: d[0].update(Architecture="arm64"),
        lambda d: d[0].pop("RootFS"),
        lambda d: d[0]["RootFS"].update(Type="unknown"),
        lambda d: d[0]["RootFS"].update(Layers=["sha256:" + "0" * 64]),
        lambda d: d.clear(),
        lambda d: d.append(d[0]),
    ],
)
def test_loaded_image_substitution_is_denied(candidate, tmp_path, change):
    expected = _archive(tmp_path / "image.tar")
    loaded = _loaded(expected)
    change(loaded)
    with pytest.raises(candidate.CandidateError):
        candidate.verify_loaded_config(expected, loaded)


def test_runtime_identity_cli_and_loaded_verification(tmp_path):
    path = tmp_path / "image.tar"
    expected = _archive(path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "runtime-config",
            str(path),
            "--expected",
            expected["ManifestDigest"],
            "--architecture",
            "amd64",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    identity = tmp_path / "identity.json"
    identity.write_text(result.stdout)
    loaded = tmp_path / "loaded.json"
    loaded.write_text(json.dumps(_loaded(expected)))
    command = [
        sys.executable,
        str(SCRIPT),
        "verify-loaded",
        str(identity),
        "--inspect",
        str(loaded),
    ]
    assert subprocess.run(command, check=False, capture_output=True).returncode == 0
    loaded.write_text("null")
    assert subprocess.run(command, check=False, capture_output=True).returncode == 1


@pytest.mark.parametrize(
    "args", [[], ["--expected", "bad"], ["--architecture", "amd64"]]
)
def test_runtime_config_cli_requires_digest_and_platform(tmp_path, args):
    path = tmp_path / "image.tar"
    _archive(path)
    command = [sys.executable, str(SCRIPT), "runtime-config", str(path), *args]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == 1
    assert "CANDIDATE DENIED" in result.stderr
