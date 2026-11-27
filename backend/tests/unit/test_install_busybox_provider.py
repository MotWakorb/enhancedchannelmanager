"""An owned replacement must retain the real payload/ownership/trust contract."""

import hashlib
import importlib.util
import io
import json
import stat
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def installer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "install_busybox_provider", ROOT / "scripts/install_busybox_provider.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_files_directories_links_and_owners_are_checked(installer, tmp_path):
    binary = tmp_path / "bin/busybox"
    binary.parent.mkdir()
    binary.write_bytes(b"actual installed bytes")
    binary.chmod(0o755)
    (tmp_path / "bin/sh").symlink_to("/bin/busybox")
    uid, gid = binary.stat().st_uid, binary.stat().st_gid
    rows = [
        {
            "path": "bin",
            "type": "directory",
            "mode": stat.S_IMODE(binary.parent.stat().st_mode),
            "uid": uid,
            "gid": gid,
        },
        {
            "path": "bin/busybox",
            "type": "file",
            "size": len(binary.read_bytes()),
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "mode": 0o755,
            "uid": uid,
            "gid": gid,
        },
        {
            "path": "bin/sh",
            "type": "symlink",
            "target": "/bin/busybox",
            "mode": 0o777,
            "uid": uid,
            "gid": gid,
        },
    ]
    installer.verify_payload(tmp_path, rows)
    binary.chmod(0o700)
    with pytest.raises(installer.BuildError, match="metadata"):
        installer.verify_payload(tmp_path, rows)
    binary.chmod(0o755)
    binary.write_bytes(b"changed runtime bytes")
    with pytest.raises(installer.BuildError, match="bytes"):
        installer.verify_payload(tmp_path, rows)


def test_symlink_parent_escape_and_uid_changes_are_denied(installer, tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(installer.BuildError, match="parent"):
        installer.verify_payload(tmp_path, [{"path": "escape/other", "type": "file"}])
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(installer.BuildError, match="metadata"):
        installer.verify_payload(
            tmp_path,
            [
                {
                    "path": "directory",
                    "type": "directory",
                    "mode": stat.S_IMODE(directory.stat().st_mode),
                    "uid": directory.stat().st_uid + 1,
                    "gid": directory.stat().st_gid,
                }
            ],
        )


def test_contents_require_exact_file_set_but_not_shared_directory_ownership(installer):
    recipe = json.loads((ROOT / "docker/alpine-busybox/manifest.json").read_text())
    package = recipe["architecture_inputs"]["x86_64"]["signed_packages"][0]
    rows = package["original_payload"]
    paths = [r["path"] for r in rows if r["type"] != "directory"]
    name = package["index_record"]["P"]
    text = name + "-1.37.0-r31 contains:\n" + "\n".join(paths) + "\n"
    installer.validate_contents(text, name, "1.37.0-r31", rows)
    with pytest.raises(installer.BuildError, match="contents"):
        installer.validate_contents(
            text + "unreviewed-file\n", name, "1.37.0-r31", rows
        )


def test_unrelated_runtime_apk_is_denied_before_signature_or_install(
    installer, tmp_path
):
    record = {
        "status": "BUILT_SIGNED_BUSYBOX_ONLY",
        "architecture": "amd64",
        "recipe_sha256": installer.MANIFEST_SHA256,
        "package_version": "1.37.0_p20261109-r0",
        "runtime_packages": [{"name": "busybox-suid"}],
    }
    (tmp_path / "build-output.json").write_text(json.dumps(record))
    with pytest.raises(installer.BuildError, match="selection"):
        installer.load_packages(
            tmp_path, "amd64", ROOT / "docker/alpine-busybox/manifest.json"
        )


def test_actual_trusted_apkv2_tamper_denial_is_bad_signature_not_unknown_key(installer):
    fixture = json.loads(
        (
            ROOT / "backend/tests/fixtures/busybox_signature_verification.json"
        ).read_text()
    )
    denial = fixture["changed_authenticated_control"]
    installer.validate_tamper_denial(denial["exit"], denial["stdout"], denial["stderr"])
    for code, stdout in [
        (0, denial["stdout"]),
        (1, "unexpected parse error"),
        (1, "/test.apk: UNTRUSTED signature\n"),
    ]:
        with pytest.raises(installer.BuildError, match="signature"):
            installer.validate_tamper_denial(code, stdout, "")


def test_installed_database_scripts_must_match_authenticated_control_bytes(
    installer, tmp_path
):
    content = b"#!/bin/busybox sh\nexit 0\n"
    script = {
        "path": ".post-install",
        "mode": 0o755,
        "uid": 0,
        "gid": 0,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    expected = {
        "busybox-1.37.0_p20261109-r0.X18d9e155c2cb2bdbee4b96d04f9ae8d5dd660858c.post-install": script
    }
    path = tmp_path / "scripts.tar.gz"

    def archive(data):
        with tarfile.open(path, "w:gz") as bundle:
            item = tarfile.TarInfo(next(iter(expected)))
            item.mode, item.size = 0o755, len(data)
            bundle.addfile(item, io.BytesIO(data))

    archive(content)
    installer.verify_control_scripts(path, expected)
    archive(b"changed script content")
    with pytest.raises(installer.BuildError, match="control script"):
        installer.verify_control_scripts(path, expected)


def test_separate_source_location_avoids_copying_delivered_sources(installer, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    original = tmp_path / "delivered-sources"
    original.mkdir()
    assert installer.source_directory(evidence, original) == original
    assert not (evidence / "sources").exists()
    default = evidence / "sources"
    default.mkdir()
    assert installer.source_directory(evidence) == default


@pytest.mark.parametrize("mutation", ["missing", "file", "link", "parent-link"])
def test_source_location_requires_a_real_canonical_directory(installer, tmp_path, mutation):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    original = tmp_path / "delivered-sources"
    if mutation == "file": original.write_bytes(b"not a source directory")
    elif mutation in {"link", "parent-link"}:
        real = tmp_path / "real"
        real.mkdir()
        original.symlink_to(real, target_is_directory=True)
        if mutation == "parent-link":
            (real / "nested").mkdir()
            original = original / "nested"
    with pytest.raises(installer.BuildError, match="source directory"):
        installer.source_directory(evidence, original)
