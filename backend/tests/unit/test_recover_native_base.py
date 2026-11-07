"""Exact recorded vendor image graph, not a new base or a final-image waiver."""

import copy
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
METADATA = ROOT / "docker/native/base-recovery.json"


@pytest.fixture
def recovery():
    path = ROOT / "scripts/recover_native_base.py"
    spec = importlib.util.spec_from_file_location("recover_native_base", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_recorded_vendor_graph_is_bound_to_original_index(recovery, arch):
    descriptor, manifest, config = recovery.load_metadata(METADATA, arch)
    assert descriptor["platform"]["architecture"] == arch
    assert "sha256:" + hashlib.sha256(manifest).hexdigest() == descriptor["digest"]
    parsed = json.loads(manifest)
    assert "sha256:" + hashlib.sha256(config).hexdigest() == parsed["config"]["digest"]
    assert json.loads(config)["architecture"] == arch


@pytest.mark.parametrize("key", ["index", "amd64_manifest", "amd64_config"])
def test_metadata_tampering_is_refused_before_fetch(recovery, tmp_path, key):
    data = json.loads(METADATA.read_text())
    data[key] += " "
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity"):
        recovery.load_metadata(path, "amd64")


def test_unsupported_architecture_is_refused(recovery):
    with pytest.raises(ValueError, match="architecture"):
        recovery.load_metadata(METADATA, "ppc64le")


def test_layer_validation_checks_compressed_and_uncompressed_identity(recovery, tmp_path):
    raw = b"synthetic layer stream, not a vendor rootfs"
    data = gzip.compress(raw)
    path = tmp_path / "layer"
    path.write_bytes(data)
    descriptor = {"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}
    diff_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    recovery.verify_layer(path, descriptor, diff_id)
    for field, bad in [("digest", "sha256:" + "0" * 64), ("size", len(data) + 1)]:
        mutant = copy.deepcopy(descriptor)
        mutant[field] = bad
        with pytest.raises(ValueError):
            recovery.verify_layer(path, mutant, diff_id)
    with pytest.raises(ValueError, match="diff"):
        recovery.verify_layer(path, descriptor, "sha256:" + "0" * 64)


def test_symlink_layer_is_not_accepted(recovery, tmp_path):
    path = tmp_path / "layer"
    path.symlink_to(METADATA)
    with pytest.raises(ValueError, match="regular"):
        recovery.verify_layer(path, {}, "sha256:" + "0" * 64)


def test_failed_fetch_cannot_publish_partial_layout(recovery, tmp_path):
    output = tmp_path / "layout"

    def failed_fetch(descriptor, destination):
        destination.write_bytes(b"incomplete transfer")
        raise OSError("synthetic interrupted read")

    with pytest.raises(OSError, match="interrupted"):
        recovery.recover_layout(METADATA, "amd64", output, fetch=failed_fetch)
    assert not output.exists()


def test_wrong_layer_cannot_publish_layout(recovery, tmp_path):
    output = tmp_path / "layout"

    def wrong_fetch(descriptor, destination):
        destination.write_bytes(b"not the original layer")

    with pytest.raises(ValueError):
        recovery.recover_layout(METADATA, "amd64", output, fetch=wrong_fetch)
    assert not output.exists()


def test_existing_output_is_never_overwritten(recovery, tmp_path):
    output = tmp_path / "layout"
    output.mkdir()
    marker = output / "user-file"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        recovery.recover_layout(METADATA, "amd64", output)
    assert marker.read_text() == "keep"


def test_both_native_ci_builds_recover_base_without_obsolete_source_carriers():
    jobs = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())["jobs"]
    for arch in ("amd64", "arm64"):
        steps = jobs["build-" + arch]["steps"]
        preparation = next(s for s in steps if s.get("id") == "version")
        assert "recover_native_base.py" in preparation["run"]
        assert "--arch " + arch in preparation["run"]
        assert "--with-sources" not in preparation["run"]
        build = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action@"))
        assert build["with"]["build-contexts"].splitlines() == [
            "${{ steps.version.outputs.context }}",
        ]
        assert "docker/setup-buildx-action@" in str(steps)


def test_mcp_ci_enables_same_recovery_without_skipping_isolation():
    steps = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())["jobs"]["mcp-server"]["steps"]
    build = next(s for s in steps if s.get("id") == "isolation_build")
    assert build["env"]["ECM_RECOVER_NATIVE_BASE"] == "true"
    assert build["run"] == "bash scripts/test_mcp_container_hardening.sh --build-only"
    isolation = next(s for s in steps if s.get("id") == "isolation")
    assert isolation["env"]["COMPOSE_BUILD"] == "false"
    assert "PUID=1571 PGID=1572" in isolation["run"]
    script = (ROOT / "scripts/test_mcp_container_hardening.sh").read_text()
    assert 'scripts/recover_native_base.py" --arch "$native_arch"' in script
    assert "--with-sources" not in script


def test_compose_context_only_changes_base_transport(recovery, tmp_path):
    context = recovery.BASE + "=oci-layout:///test/base@sha256:" + "0" * 64
    path = tmp_path / "compose.json"
    recovery.write_compose_context(context, path)
    assert json.loads(path.read_text()) == {
        "services": {"ecm": {"build": {"additional_contexts": [context]}}}
    }


def source_layer(tmp_path, mutation=None):
    """Tiny collector-shaped archive; this is not signature/Source-Patch proof."""
    body = b"synthetic source RPM bytes"
    filename = "package-1-1.src.rpm"
    index = json.dumps({"architecture": "x86_64", "sources": [{
        "filename": filename, "path": "sources/" + filename,
        "size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
    }]}).encode()
    spec = {"rpm_arch": "x86_64", "source_count": 1,
            "index_sha256": hashlib.sha256(index).hexdigest()}
    path = tmp_path / "sources.tar.gz"
    prefix = "usr/share/ecm/sources/rpm/"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(prefix + "index.json")
        member.size = len(index)
        archive.addfile(member, io.BytesIO(index))
        if mutation == "missing":
            return path, spec
        member = tarfile.TarInfo(prefix + "sources/" + filename)
        if mutation == "escape":
            member.name = prefix + "sources/../../escape.src.rpm"
        if mutation == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
            archive.addfile(member)
        else:
            member.size = len(body)
            archive.addfile(member, io.BytesIO(b"x" * len(body) if mutation == "tamper" else body))
            if mutation == "duplicate":
                archive.addfile(member, io.BytesIO(body))
    return path, spec


def test_source_layer_only_materializes_hash_bound_regular_rpm_files(recovery, tmp_path):
    layer, specification = source_layer(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    recovery.unpack_source_layer(layer, output, specification)
    assert {p.name for p in output.iterdir()} == {"index.json", "sources"}
    assert (output / "sources/package-1-1.src.rpm").read_bytes() == b"synthetic source RPM bytes"


@pytest.mark.parametrize("mutation", ["missing", "escape", "link", "tamper", "duplicate"])
def test_dangerous_source_layer_mutations_are_refused(recovery, tmp_path, mutation):
    layer, specification = source_layer(tmp_path, mutation)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(ValueError):
        recovery.unpack_source_layer(layer, output, specification)


@pytest.mark.parametrize("field,bad", [("rpm_arch", "aarch64"), ("index_sha256", "0" * 64), ("source_count", 2)])
def test_source_index_cannot_rebind_architecture_hash_or_completeness(recovery, tmp_path, field, bad):
    layer, specification = source_layer(tmp_path)
    specification[field] = bad
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(ValueError):
        recovery.unpack_source_layer(layer, output, specification)
