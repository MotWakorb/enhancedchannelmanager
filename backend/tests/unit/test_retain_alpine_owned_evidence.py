"""Public-evidence copying unit boundary; real signed packages checked separately.

Recorded native/BusyBox receipt shapes, tiny unsigned transport stand-ins. The
BusyBox source/check validator is stubbed here, not claimed as authentication.
"""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PUBLIC = b"-----BEGIN PUBLIC KEY-----\nQUFB\n-----END PUBLIC KEY-----\n"


@pytest.fixture
def copier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "retain_alpine_owned_evidence", ROOT / "scripts/retain_alpine_owned_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["amd64", "arm64"])
def subject(tmp_path, request, copier, monkeypatch):
    arch = request.param
    native = tmp_path / "native-input"
    busy = tmp_path / "busybox-input"
    native.mkdir()
    (busy / "runtime-packages").mkdir(parents=True)
    fixtures = ROOT / "backend/tests/fixtures/alpine"
    receipt = json.loads(
        gzip.decompress((fixtures / f"owned-components-{arch}.json.gz").read_bytes())
    )["package_receipt"]
    busy_receipt = json.loads(
        gzip.decompress((fixtures / f"owned-busybox-{arch}.json.gz").read_bytes())
    )["build_receipt"]
    for directory, record, packages in [
        (native, receipt, [receipt["package"]]),
        (busy, busy_receipt, busy_receipt["runtime_packages"]),
    ]:
        (directory / record["public_key"]["file"]).write_bytes(PUBLIC)
        record["public_key"]["sha256"] = hashlib.sha256(PUBLIC).hexdigest()
        for package in packages:
            data = (
                "unsigned transport stand-in: " + package.get("name", "native")
            ).encode()
            package["sha256"] = hashlib.sha256(data).hexdigest()
            path = (
                directory
                / ("runtime-packages" if directory == busy else "")
                / package["file"]
            )
            path.write_bytes(data)
        (directory / "private.pem").write_bytes(b"DO NOT DELIVER PRIVATE KEY")
    (native / "package-output.json").write_text(json.dumps(receipt))
    (busy / "build-output.json").write_text(json.dumps(busy_receipt))
    (busy / "check-output.json").write_text('{"unit_transport_fixture":true}\n')
    (busy / "check.log").write_bytes(b"unsigned unit check log stand-in\n")
    source = busy / "sources"
    (source / "owned-recipe").mkdir(parents=True)
    (source / "owned-recipe/manifest.json").write_bytes(
        (ROOT / "docker/alpine-busybox/manifest.json").read_bytes()
    )
    calls = []

    def load(directory, selected_arch, manifest, *, source_root=None):
        assert directory == busy and selected_arch == arch
        assert (
            manifest.read_bytes()
            == (ROOT / "docker/alpine-busybox/manifest.json").read_bytes()
        )
        calls.append((manifest, source_root))
        return json.loads((busy / "build-output.json").read_text())

    monkeypatch.setattr(copier, "load_packages", load)
    return native, busy, arch, source, tmp_path / "delivered", calls


def test_closed_public_evidence_namespace_keeps_original_bytes_and_no_private_inputs(
    copier, subject
):
    native, busy, arch, _source, out, calls = subject
    result = copier.retain(native, busy, arch, out)
    assert len(calls) == 1
    assert result["status"] == "SELECTED_ORIGINAL_OWNED_APK_EVIDENCE_COPIED_ONLY"
    assert result["architecture"] == arch
    assert result["signature_verification_required"] is True
    assert result["full_runtime_source_acceptance"] is False
    assert result["selected_packages"] == 4
    assert len(result["files"]) == 11  # Ten original inputs and the copier source.
    assert json.loads((out / "index.json").read_text()) == result
    expected = {"index.json", "recipe/retain_alpine_owned_evidence.py"}
    for directory, prefix in [(native, "native"), (busy, "busybox")]:
        for path in directory.rglob("*"):
            name = path.relative_to(directory).as_posix()
            if (
                not path.is_file()
                or name == "private.pem"
                or name.startswith("sources/")
            ):
                continue
            target = out / prefix / name
            assert target.read_bytes() == path.read_bytes()
            expected.add(prefix + "/" + name)
    assert {
        path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
    } == expected
    assert (
        not (out / "native/private.pem").exists()
        and not (out / "busybox/sources").exists()
    )
    assert {p.name for p in (out / "native").glob("*.pub")} == {"ecm-build.pub"}
    assert {p.name for p in (out / "busybox").glob("*.pub")} == {"ecm-busybox.rsa.pub"}


def test_separate_original_busybox_source_root_reuses_all_existing_validation(
    copier, subject
):
    native, busy, arch, source, out, calls = subject
    separate = busy.parent / "separate-sources"
    source.rename(separate)
    copier.retain(native, busy, arch, out, busybox_source_root=separate)
    assert calls == [(separate / "owned-recipe/manifest.json", separate)]
    assert not (out / "busybox/sources").exists()


@pytest.mark.parametrize(
    "defect",
    [
        "existing-output",
        "input-alias",
        "output-parent-alias",
        "public-symlink",
        "private-key",
        "trailing-private-key",
        "wrong-key-name",
        "copied-corruption",
        "duplicate-json",
        "oversized-receipt",
    ],
)
def test_public_evidence_delivery_fails_closed_without_overwriting_originals(
    copier, subject, tmp_path, monkeypatch, defect
):
    native, busy, arch, _source, out, _ = subject
    original = (native / "package-output.json").read_bytes()
    if defect == "existing-output":
        out.mkdir()
        (out / "operator-file").write_bytes(b"keep")
    elif defect == "input-alias":
        alias = tmp_path / "alias"
        alias.symlink_to(native, target_is_directory=True)
        native = alias
    elif defect == "output-parent-alias":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        out = alias / "delivered"
    elif defect == "public-symlink":
        key = native / "ecm-build.pub"
        key.rename(tmp_path / "original.pub")
        key.symlink_to(tmp_path / "original.pub")
    elif defect in {"private-key", "trailing-private-key", "wrong-key-name"}:
        record = json.loads(original)
        key = native / "ecm-build.pub"
        if defect == "wrong-key-name":
            record["public_key"]["file"] = "private.pem"
            key = native / "private.pem"
        else:
            key.write_bytes(
                (PUBLIC if defect == "trailing-private-key" else b"")
                + b"-----BEGIN PRIVATE KEY-----\nQUFB\n-----END PRIVATE KEY-----\n"
            )
        record["public_key"]["sha256"] = hashlib.sha256(key.read_bytes()).hexdigest()
        (native / "package-output.json").write_text(json.dumps(record))
    elif defect == "duplicate-json":
        (native / "package-output.json").write_bytes(
            b'{"architecture":' + json.dumps(arch).encode() + b"," + original[1:]
        )
    elif defect == "oversized-receipt":
        with (native / "package-output.json").open("wb") as stream:
            stream.truncate(32 * 1024**2 + 1)
    else:
        monkeypatch.setattr(
            copier.shutil, "copyfile", lambda src, dst: dst.write_bytes(b"corrupt copy")
        )
    with pytest.raises(copier.BuildError):
        copier.retain(native, busy, arch, out)
    assert not (out / "index.json").exists()
    if defect == "existing-output":
        assert (out / "operator-file").read_bytes() == b"keep"


def test_busybox_original_source_and_check_validation_is_mandatory(
    copier, subject, monkeypatch
):
    native, busy, arch, _source, out, _ = subject
    monkeypatch.setattr(
        copier,
        "load_packages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            copier.BuildError("source/check rejected")
        ),
    )
    with pytest.raises(copier.BuildError, match="source/check rejected"):
        copier.retain(native, busy, arch, out)
    assert not out.exists()
