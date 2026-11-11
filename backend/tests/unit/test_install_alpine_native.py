"""Signature trust stays build-only; installation must retain exact APK ownership."""

import importlib.util
import json
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_apkv2_signer_filename_is_explicit_and_scoped(installer, tmp_path):
    public = tmp_path / "source.pub"
    public.write_bytes(b"public key evidence")
    with installer.temporary_trust(
        [], public, tmp_path, key_name="ecm-busybox.rsa.pub"
    ) as trust:
        assert (trust / "ecm-busybox.rsa.pub").read_bytes() == public.read_bytes()
        assert not (trust / "ecm-build.pub").exists()
    assert not trust.exists()
    with (
        pytest.raises(installer.BuildError, match="filename"),
        installer.temporary_trust([], public, tmp_path, key_name="../escape.pub"),
    ):
        pytest.fail("unsafe key name accepted")


def test_negative_control_changes_authenticated_metadata_not_deflate_padding(
    installer, tmp_path
):
    original = (
        b"ADB.pckg" + b"\0" * 12 + b"\x10ecm-owned-native\0" + b"payload bytes" * 10000
    )
    compressor = zlib.compressobj(wbits=-15)
    archive = tmp_path / "owned.apk"
    archive.write_bytes(b"ADBd" + compressor.compress(original) + compressor.flush())
    damaged = tmp_path / "damaged.apk"
    installer.damage_signed_metadata(archive, damaged)
    altered = zlib.decompress(damaged.read_bytes()[4:], -15)
    assert len(altered) == len(original)
    assert sum(a != b for a, b in zip(original, altered, strict=True)) == 1
    assert b"fcm-owned-native\0" in altered
    assert altered[512:] == original[512:]


@pytest.fixture
def installer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "install_alpine_native", ROOT / "scripts/install_alpine_native.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_temporary_trust_copies_only_public_keys_and_removes_it_even_on_failure(
    installer, tmp_path
):
    system = tmp_path / "system"
    system.mkdir()
    (system / "vendor.pub").write_bytes(b"vendor public key")
    owned = tmp_path / "owned.pub"
    owned.write_bytes(b"owned public key")
    with (
        pytest.raises(RuntimeError, match="simulated install failure"),
        installer.temporary_trust([system], owned, tmp_path) as trust,
    ):
        saved = trust
        assert sorted(p.name for p in trust.iterdir()) == [
            "ecm-build.pub",
            "vendor.pub",
        ]
        assert (system / "vendor.pub").read_bytes() == b"vendor public key"
        raise RuntimeError("simulated install failure")
    assert not saved.exists() and not (system / "ecm-build.pub").exists()


def test_package_or_key_tampering_is_rejected_before_apk(
    installer, tmp_path, monkeypatch
):
    (tmp_path / "native.apk").write_bytes(
        b"not an actual APK; hash-bound location fixture"
    )
    (tmp_path / "ecm-build.pub").write_bytes(b"public key bytes")
    record = {
        "schema_version": 1,
        "status": "SIGNED_OWNED_APK_ONLY",
        "architecture": "amd64",
        "package": {
            "name": "ecm-owned-native",
            "version": "1-r0",
            "file": "native.apk",
            "sha256": installer.sha256(tmp_path / "native.apk"),
        },
        "public_key": {
            "file": "ecm-build.pub",
            "sha256": installer.sha256(tmp_path / "ecm-build.pub"),
        },
    }
    (tmp_path / "package-output.json").write_text(json.dumps(record))
    assert installer.load_package(tmp_path, "amd64") == record
    (tmp_path / "native.apk").write_bytes(b"changed")
    with pytest.raises(installer.BuildError, match="package/key identity"):
        installer.load_package(tmp_path, "amd64")


def test_installed_bytes_links_and_ownership_are_all_required(installer, tmp_path):
    (tmp_path / "file").write_bytes(b"owned product")
    (tmp_path / "link").symlink_to("file")
    inventory = installer.owned_artifacts(tmp_path)
    installer.verify_installed(tmp_path, inventory)
    installer.validate_ownership(
        "/file is owned by ecm-owned-native-1-r0\n/link is owned by ecm-owned-native-1-r0\n",
        inventory,
        "1-r0",
    )
    for bad in [
        "/file is owned by vendor-native-1-r0\n/link is owned by ecm-owned-native-1-r0\n",
        "/file is owned by ecm-owned-native-1-r0\n",
    ]:
        with pytest.raises(installer.BuildError, match="APK ownership"):
            installer.validate_ownership(bad, inventory, "1-r0")
    (tmp_path / "file").write_bytes(b"changed")
    with pytest.raises(installer.BuildError, match="installed artifact"):
        installer.verify_installed(tmp_path, inventory)


def test_actual_apk3_contents_format_cannot_hide_an_extra_or_missing_file(installer):
    recorded = json.loads(
        (ROOT / "backend/tests/fixtures/alpine/apk-v3-signing-probe.json").read_text()
    )
    inventory = [{"path": "usr/share/ecm/probe"}]
    installer.validate_contents(
        recorded["contents"], inventory, "ecm-signature-probe", "1-r0"
    )
    for bad in [
        recorded["contents"] + "etc/apk/keys/extra.pub\n",
        "ecm-signature-probe-1-r0 contains:\n",
    ]:
        with pytest.raises(installer.BuildError, match="APK contents"):
            installer.validate_contents(bad, inventory, "ecm-signature-probe", "1-r0")
