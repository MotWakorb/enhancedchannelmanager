"""GNU attribution must bind whole ELF bytes, not comments or producer names."""

import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "docker/python-third-party/artifact-index/musllinux-gnu.json"


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_musllinux_gnu", ROOT / "scripts/verify_musllinux_gnu.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_wheel_distribution_is_14_wheels_18_members_12_objects(verifier):
    manifest = verifier.load_manifest(MANIFEST)
    rows = manifest["objects"]
    assert len(rows) == 12
    assert sum(len(row["members"]) for row in rows) == 18
    assert len({m["wheel"] for row in rows for m in row["members"]}) == 14
    assert {row["gcc_version"] for row in rows} == {"12.4.0", "14.2.0"}
    assert all(row["expected_sha256"] != row["original"]["sha256"] for row in rows)
    assert len(manifest["wheel_selection"]) == 96


def test_raw_unknown_namespace_is_preserved_not_used_as_source_authority(verifier):
    manifest = verifier.load_manifest(MANIFEST)
    arm = next(
        row
        for row in manifest["objects"]
        if any(
            "cryptography-" in m["wheel"] and "aarch64" in m["wheel"]
            for m in row["members"]
        )
    )
    assert arm["producer_sbom"]["purl"] == "pkg:apk/NotpineForGHA/libgcc@14.2.0-r6"
    assert arm["producer_sbom"]["authority"] is False
    assert arm["original"]["provider_kind"] == "signed-alpine-apk"
    with pytest.raises(verifier.AttributionError, match="source closure"):
        verifier.require_source_closure(manifest)


@pytest.mark.parametrize("change", ["extra", "missing", "duplicate"])
def test_member_coverage_is_complete_not_first_match(verifier, change):
    manifest = verifier.load_manifest(MANIFEST)
    expected = verifier.expected_members(manifest)
    actual = dict(expected)
    key = next(iter(expected))
    if change == "extra":
        actual[("unknown.whl", "unknown.libs/libgomp-extra.so.1")] = "f" * 64
    elif change == "missing":
        del actual[key]
    else:
        manifest = copy.deepcopy(manifest)
        manifest["objects"][0]["members"].append(manifest["objects"][0]["members"][0])
        with pytest.raises(verifier.AttributionError, match="duplicate"):
            verifier.expected_members(manifest)
        return
    with pytest.raises(verifier.AttributionError, match="member coverage"):
        verifier.compare_members(actual, expected)


def test_same_prefix_comment_build_id_or_claimed_match_is_not_byte_identity(verifier):
    manifest = verifier.load_manifest(MANIFEST)
    expected = verifier.expected_members(manifest)
    actual = dict(expected)
    key = next(iter(actual))
    actual[key] = actual[key][:8] + "0" * 56
    with pytest.raises(verifier.AttributionError, match="member coverage"):
        verifier.compare_members(actual, expected)


def test_profiles_are_bounded_no_manifest_supplied_arbitrary_commands(verifier):
    row = verifier.load_manifest(MANIFEST)["objects"][0]
    assert verifier.repair_arguments(row)
    row = copy.deepcopy(row)
    row["profile"] = "run-shell-script"
    with pytest.raises(verifier.AttributionError, match="repair profile"):
        verifier.repair_arguments(row)


@pytest.mark.parametrize("field", ["expected_sha256", "profile", "gcc_version"])
def test_operator_cannot_change_the_reviewed_correspondence_map(
    verifier, tmp_path, field
):
    manifest = json.loads(MANIFEST.read_text())
    manifest["objects"][0][field] = "unreviewed"
    target = tmp_path / "changed.json"
    target.write_text(json.dumps(manifest))
    with pytest.raises(verifier.AttributionError, match="manifest identity"):
        verifier.load_manifest(target)


def test_replay_does_not_read_or_overwrite_target_elf(verifier, tmp_path):
    # The replay only receives original bytes + a hash, never the target ELF.
    source = tmp_path / "source"
    source.write_bytes(b"original bytes")
    work = tmp_path / "work"
    work.mkdir()
    row = {
        "original": {"sha256": verifier.sha256(source)},
        "expected_sha256": "0" * 64,
        "soname": "libgcc_s-deadbeef.so.1",
        "profile": "soname-only",
    }
    tool = Path("/bin/true")  # Deliberate no-op must not pass the full-byte check.
    with pytest.raises(verifier.AttributionError, match="complete ELF"):
        verifier.replay(row, source, tool, work)
    assert source.read_bytes() == b"original bytes"


def test_wheel_zip_bytes_are_checked_and_an_unlisted_gnu_copy_is_denied(
    verifier, tmp_path
):
    path = tmp_path / "recorded.whl"
    data = b"not executable: member coverage negative fixture"
    name = "recorded.libs/libgcc_s-deadbeef.so.1"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(name, data)
    manifest = {
        "objects": [
            {
                "expected_sha256": hashlib.sha256(data).hexdigest(),
                "members": [
                    {
                        "wheel": path.name,
                        "path": name,
                        "wheel_sha256": verifier.sha256(path),
                    }
                ],
            }
        ]
    }
    manifest["wheel_selection"] = {path.name: verifier.sha256(path)}
    assert len(verifier.inspect_wheels(manifest, tmp_path)["gnu_members"]) == 1
    extra = tmp_path / "unlisted.whl"
    with zipfile.ZipFile(extra, "w") as wheel:
        wheel.writestr("unlisted.libs/libgomp-extra.so.1", data)
    manifest["wheel_selection"][extra.name] = verifier.sha256(extra)
    with pytest.raises(verifier.AttributionError, match="member coverage"):
        verifier.inspect_wheels(manifest, tmp_path)


def test_whole_wheel_rebinding_is_rejected_even_when_gnu_member_stays_identical(
    verifier, tmp_path
):
    path = tmp_path / "recorded.whl"
    name = "recorded.libs/libgcc_s-deadbeef.so.1"
    data = b"GNU member unchanged"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(name, data)
    manifest = {
        "objects": [
            {
                "expected_sha256": hashlib.sha256(data).hexdigest(),
                "members": [
                    {
                        "wheel": path.name,
                        "path": name,
                        "wheel_sha256": verifier.sha256(path),
                    }
                ],
            }
        ]
    }
    manifest["wheel_selection"] = {path.name: verifier.sha256(path)}
    with zipfile.ZipFile(path, "a") as wheel:
        wheel.writestr("recorded/another.py", b"changed application payload")
    with pytest.raises(verifier.AttributionError, match="whole-wheel"):
        verifier.inspect_wheels(manifest, tmp_path)


def test_unsafe_replay_identity_cannot_escape_work_directory(verifier, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"original bytes")
    work = tmp_path / "work"
    work.mkdir()
    row = {
        "original": {"sha256": verifier.sha256(source)},
        "expected_sha256": "../outside",
        "soname": "libgcc_s-deadbeef.so.1",
        "profile": "soname-only",
    }
    with pytest.raises(verifier.AttributionError, match="ELF digest"):
        verifier.replay(row, source, Path("/bin/true"), work)
    assert not (tmp_path / "outside").exists()


def test_provider_archive_member_is_never_extracted_or_followed(verifier, tmp_path):
    archive = tmp_path / "layer.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        entry = tarfile.TarInfo("usr/lib/libgcc_s.so.1")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "/etc/shadow"
        tar.addfile(entry)
    with pytest.raises(verifier.AttributionError, match="regular provider member"):
        verifier.archive_member(archive, "usr/lib/libgcc_s.so.1")
    assert not (tmp_path / "usr").exists()


def test_duplicate_provider_members_cannot_select_a_convenient_first_match(
    verifier, tmp_path
):
    archive = tmp_path / "layer.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for content in [b"first", b"second"]:
            entry = tarfile.TarInfo("usr/lib/libgcc_s.so.1")
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    with pytest.raises(
        verifier.AttributionError, match="unique regular provider member"
    ):
        verifier.archive_member(archive, "usr/lib/libgcc_s.so.1")


def test_apk_compressed_control_and_data_hash_joins_are_independent(verifier):
    def tar_bytes(name, data):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        return buffer.getvalue()

    payload = gzip.compress(
        tar_bytes("usr/lib/libgcc_s.so.1", b"original GNU bytes"), mtime=0
    )
    metadata = (
        "pkgname = libgcc\npkgver = 14.2.0-r6\narch = x86_64\norigin = gcc\ncommit = "
        + "a" * 40
        + "\ndatahash = "
        + hashlib.sha256(payload).hexdigest()
        + "\n"
    ).encode()
    control = gzip.compress(tar_bytes(".PKGINFO", metadata), mtime=0)
    signature = gzip.compress(
        tar_bytes(".SIGN.RSA.vendor.pub", b"signature placeholder, no trust assertion"),
        mtime=0,
    )
    blob = signature + control + payload
    expected = {
        "C": "Q1" + base64.b64encode(hashlib.sha1(control).digest()).decode(),
        "P": "libgcc",
        "V": "14.2.0-r6",
        "A": "x86_64",
        "o": "gcc",
        "c": "a" * 40,
    }
    assert verifier.apk_payload(blob, expected) == payload
    with pytest.raises(verifier.AttributionError, match="compressed control"):
        verifier.apk_payload(blob, {**expected, "C": "Q1wrong"})
    with pytest.raises(verifier.AttributionError, match="payload hash"):
        verifier.apk_payload(
            signature
            + control
            + gzip.compress(tar_bytes("usr/lib/libgcc_s.so.1", b"changed")),
            expected,
        )


def test_preparation_never_accepts_a_cached_blob_from_its_filename_alone(
    verifier, tmp_path
):
    digest = "a" * 64
    (tmp_path / digest).write_bytes(b"not hash aaaa")
    with pytest.raises(
        verifier.AttributionError, match="cached provider/tool identity"
    ):
        verifier.cached_input(tmp_path, digest)


def test_source_status_labels_cannot_substitute_for_delivered_source_bytes(verifier):
    manifest = copy.deepcopy(verifier.load_manifest(MANIFEST))
    for source in manifest["source_closures"].values():
        source["status"] = "AUTHENTICATED_PROVIDER_SOURCE"
        source["delivery_status"] = "VERIFIED_DELIVERED"
    with pytest.raises(verifier.AttributionError, match="source closure"):
        verifier.require_source_closure(manifest)


def test_source_closure_rehashes_every_delivered_file_and_rejects_extras(
    verifier, tmp_path
):
    directory = tmp_path / "gcc-source"
    directory.mkdir()
    archive = directory / "gcc.tar.xz"
    archive.write_bytes(b"synthetic source-binding negative-control fixture")
    manifest = {
        "source_closures": {
            "gcc-source": {
                "status": "AUTHENTICATED_PROVIDER_SOURCE",
                "delivery_status": "VERIFIED_DELIVERED",
                "source_files": [
                    {"path": archive.name, "sha256": verifier.sha256(archive)}
                ],
            }
        }
    }
    verifier.require_source_closure(manifest, tmp_path)
    archive.write_bytes(b"changed source")
    with pytest.raises(verifier.AttributionError, match="source closure bytes"):
        verifier.require_source_closure(manifest, tmp_path)
    manifest["source_closures"]["gcc-source"]["source_files"][0]["sha256"] = (
        verifier.sha256(archive)
    )
    (directory / "extra").write_bytes(b"undeclared")
    with pytest.raises(verifier.AttributionError, match="complete inventory"):
        verifier.require_source_closure(manifest, tmp_path)


def test_unchanged_gnu_members_do_not_allow_mutated_other_wheels(verifier, tmp_path):
    path = tmp_path / "purelib.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("example/__init__.py", b"original pure Python")
    manifest = {"objects": [], "wheel_selection": {path.name: verifier.sha256(path)}}
    verifier.inspect_wheels(manifest, tmp_path)
    with zipfile.ZipFile(path, "a") as wheel:
        wheel.writestr("example/hidden.so", b"new unindexed ELF, unchanged GNU names")
    with pytest.raises(verifier.AttributionError, match="whole-wheel identity"):
        verifier.inspect_wheels(manifest, tmp_path)
