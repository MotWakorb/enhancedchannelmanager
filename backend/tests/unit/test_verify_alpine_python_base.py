"""Pinned real OCI metadata and layer bytes must bind every base-installed file."""

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_python_base", ROOT / "scripts/verify_alpine_python_base.py"
    )
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def recorded(tmp_path):
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT / "backend/tests/fixtures/alpine/python-base-oci-metadata.json.gz"
            ).read_bytes()
        )
    )
    root = tmp_path / "bundle"
    root.mkdir()
    for name, content in fixture["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(content))
    return root


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_real_pinned_official_metadata_selects_only_requested_platform(
    verifier, recorded, architecture
):
    result = verifier.metadata(recorded, architecture)
    assert result["config"]["architecture"] == architecture
    assert (
        len(result["manifest"]["layers"])
        == len(result["config"]["rootfs"]["diff_ids"])
        == 4
    )
    assert result["python_version"] == "3.12.16"
    assert result["source_commit"] == "3d7e95597e56a415d9e4abc0f15877e005d2cb2e"


def test_delivery_root_cannot_be_a_redirecting_symlink(verifier, recorded, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(recorded, target_is_directory=True)
    with pytest.raises(verifier.InputError):
        verifier.metadata(alias, "amd64")


@pytest.mark.parametrize("mutation", ["index", "manifest", "config", "missing-config"])
def test_relabeling_a_top_level_manifest_or_config_is_denied(
    verifier, recorded, mutation
):
    original = verifier.metadata(recorded, "amd64")
    path = recorded / "index.json"
    if mutation == "manifest":
        path = recorded / "blobs" / original["manifest_digest"][7:]
    elif mutation in {"config", "missing-config"}:
        path = recorded / "blobs" / original["manifest"]["config"]["digest"][7:]
    if mutation == "missing-config":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(verifier.InputError):
        verifier.metadata(recorded, "amd64")


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_original_digest_bound_provenance_contains_the_entire_original_recipe(
    verifier, recorded, architecture
):
    meta = verifier.metadata(recorded, architecture)
    recipe = (recorded / "recipe/Dockerfile").read_bytes()
    assert (
        hashlib.sha256(recipe).hexdigest()
        == "a30357f54a3531398414358c869b8bb07ae5aa95f488c20c8481e00238057415"
    )
    verifier.attestations(recorded, meta, recipe)
    assert (
        len(meta["used"]) == 7
    )  # Index, image manifest/config, attestation manifest/config, two predicates.


def test_recipe_cannot_change_under_unchanged_official_provenance(verifier, recorded):
    meta = verifier.metadata(recorded, "amd64")
    with pytest.raises(verifier.InputError, match="recipe"):
        verifier.attestations(
            recorded,
            meta,
            (recorded / "recipe/Dockerfile").read_bytes() + b"RUN unreviewed\n",
        )


def test_missing_original_sbom_is_not_silently_skipped(verifier, recorded):
    meta = verifier.metadata(recorded, "amd64")
    att = next(
        r
        for r in meta["index"]["manifests"]
        if r.get("annotations", {}).get("vnd.docker.reference.digest")
        == meta["manifest_digest"]
    )
    manifest = json.loads((recorded / "blobs" / att["digest"][7:]).read_text())
    sbom = next(
        r
        for r in manifest["layers"]
        if r.get("annotations", {}).get("in-toto.io/predicate-type")
        == "https://spdx.dev/Document"
    )
    (recorded / "blobs" / sbom["digest"][7:]).unlink()
    with pytest.raises(verifier.InputError):
        verifier.attestations(
            recorded, meta, (recorded / "recipe/Dockerfile").read_bytes()
        )


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_real_last_layer_preserves_every_original_python_link(
    verifier, recorded, architecture
):
    meta = verifier.metadata(recorded, architecture)
    layer = meta["manifest"]["layers"][-1]
    rows = verifier.layer_files(
        recorded / "blobs" / layer["digest"][7:],
        layer,
        meta["config"]["rootfs"]["diff_ids"][-1],
    )
    links = {row["path"]: row["linkname"] for row in rows if row["type"] == "symlink"}
    assert links == {
        "usr/local/bin/idle": "idle3",
        "usr/local/bin/pip": "pip3",
        "usr/local/bin/pydoc": "pydoc3",
        "usr/local/bin/python": "python3",
        "usr/local/bin/python-config": "python3-config",
    }


@pytest.mark.parametrize("mutation", ["compressed", "uncompressed"])
def test_both_original_compressed_blob_and_config_diff_id_are_required(
    verifier, recorded, mutation
):
    meta = verifier.metadata(recorded, "amd64")
    layer = meta["manifest"]["layers"][-1].copy()
    path = recorded / "blobs" / layer["digest"][7:]
    diff_id = meta["config"]["rootfs"]["diff_ids"][-1]
    if mutation == "compressed":
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        diff_id = "sha256:" + "0" * 64
    with pytest.raises(verifier.InputError):
        verifier.layer_files(path, layer, diff_id)


def test_observed_tzdata_hardlinks_are_outside_the_python_local_namespace(
    verifier, tmp_path
):
    # Actual original layer metadata: tzdata uses hardlinks outside /usr/local.
    path = tmp_path / "layer.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        item = tarfile.TarInfo("usr/share/zoneinfo/Africa/Accra")
        item.type = tarfile.LNKTYPE
        item.linkname = "usr/share/zoneinfo/Africa/Abidjan"
        archive.addfile(item)
    raw = path.read_bytes()
    descriptor = {
        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }
    assert (
        verifier.layer_files(
            path,
            descriptor,
            "sha256:" + hashlib.sha256(gzip.decompress(raw)).hexdigest(),
        )
        == []
    )


@pytest.mark.parametrize(
    "mutation", ["whiteout", "duplicate", "escape", "device", "hardlink"]
)
def test_unsupported_or_unsafe_layer_semantics_cannot_silently_disappear(
    verifier, tmp_path, mutation
):
    path = tmp_path / "layer.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        name = {"whiteout": "usr/local/.wh.python", "escape": "../operator"}.get(
            mutation, "usr/local/file"
        )
        member = tarfile.TarInfo(name)
        if mutation == "device":
            member.type = tarfile.CHRTYPE
        elif mutation == "hardlink":
            member.type = tarfile.LNKTYPE
            member.linkname = "other"
        archive.addfile(member, io.BytesIO())
        if mutation == "duplicate":
            archive.addfile(member, io.BytesIO())
    data = path.read_bytes()
    descriptor = {
        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    diff_id = "sha256:" + hashlib.sha256(gzip.decompress(data)).hexdigest()
    with pytest.raises(verifier.InputError):
        verifier.layer_files(path, descriptor, diff_id)


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    file = root / "usr/local/bin/python3.12"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"exact base fixture ELF bytes")
    file.chmod(0o755)
    link = file.with_name("python")
    link.symlink_to("python3.12")
    rows = []
    for path in [file, link]:
        stat = path.lstat()
        row = {
            "path": path.relative_to(root).as_posix(),
            "type": "symlink" if path.is_symlink() else "file",
            "mode": stat.st_mode & 0o7777,
            "uid": stat.st_uid,
            "gid": stat.st_gid,
        }
        if path.is_symlink():
            row["linkname"] = path.readlink().as_posix()
        else:
            row.update(
                size=stat.st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
            )
        rows.append(row)
    return root, rows


def test_installed_bytes_and_symlinks_are_checked_not_a_version_banner(
    verifier, installed
):
    root, rows = installed
    assert verifier.installed_files(root, rows)["verified_base_files"] == 2


@pytest.mark.parametrize(
    "mutation", ["missing", "changed", "mode", "link", "parent-symlink"]
)
def test_actual_base_file_mutations_cannot_hide_behind_correct_oci_evidence(
    verifier, installed, tmp_path, mutation
):
    root, rows = installed
    file = root / "usr/local/bin/python3.12"
    if mutation == "missing":
        file.unlink()
    elif mutation == "changed":
        changed = bytearray(file.read_bytes())
        changed[0] ^= 1
        file.write_bytes(changed)
    elif mutation == "mode":
        file.chmod(0o777)
    elif mutation == "link":
        file.with_name("python").unlink()
        file.with_name("python").symlink_to("/operator/python")
    else:
        target = tmp_path / "unrelated"
        file.parent.rename(target)
        file.parent.symlink_to(target)
    with pytest.raises(verifier.InputError):
        verifier.installed_files(root, rows)


def test_nonbase_runtime_additions_are_reported_for_the_aggregate_gate_not_accepted(
    verifier, installed
):
    root, rows = installed
    (root / "usr/local/bin/unattributed-helper").write_bytes(b"not part of the base")
    result = verifier.installed_files(root, rows)
    assert result["additional_paths"] == ["usr/local/bin/unattributed-helper"]
    assert result["full_runtime_source_acceptance"] is False


def test_extra_directory_symlinks_are_reported_even_when_their_target_is_a_directory(
    verifier, installed
):
    root, rows = installed
    (root / "usr/local/other-namespace").symlink_to("bin", target_is_directory=True)
    assert verifier.installed_files(root, rows)["additional_paths"] == [
        "usr/local/other-namespace"
    ]


def test_empty_runtime_directory_additions_are_explicit_not_silently_excluded(
    verifier, installed
):
    root, rows = installed
    (root / "usr/local/unattributed-directory").mkdir()
    result = verifier.installed_files(root, rows)
    assert "usr/local/unattributed-directory" in result["additional_directories"]


def test_an_arbitrary_source_archive_cannot_replace_the_pinned_release(
    verifier, tmp_path
):
    path = tmp_path / "Python.tar.xz"
    path.write_bytes(b"wrong source")
    with pytest.raises(verifier.InputError):
        verifier.source_members(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-signature",
        "changed-signature",
        "missing-public-key",
        "changed-public-key",
    ],
)
def test_release_signature_evidence_cannot_be_substituted(verifier, recorded, mutation):
    name = (
        "source/Python-3.12.16.tar.xz.asc"
        if mutation.endswith("signature")
        else "source/python-release-manager.asc"
    )
    path = recorded / name
    if mutation.startswith("missing"):
        path.unlink()
    else:
        path.write_bytes(b"substituted release evidence")
    with pytest.raises(verifier.InputError):
        verifier.signature_evidence(recorded, set())


def test_entire_original_public_signature_evidence_is_retained(verifier, recorded):
    used = set()
    verifier.signature_evidence(recorded, used)
    assert used == {
        "source/Python-3.12.16.tar.xz.asc",
        "source/python-release-manager.asc",
    }
