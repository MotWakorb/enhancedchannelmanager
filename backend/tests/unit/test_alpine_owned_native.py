"""Original six-component receipts and every live path remain separate joins."""

import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_owned_native", ROOT / "scripts/alpine_owned_native.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", params=["amd64", "arm64"])
def original(request):
    data = json.loads(
        gzip.decompress(
            (
                ROOT
                / f"backend/tests/fixtures/alpine/owned-components-{request.param}.json.gz"
            ).read_bytes()
        )
    )
    return (
        data["package_receipt"],
        {
            name: raw.encode()
            for name, raw in data["original_component_receipts"].items()
        },
        request.param,
    )


def test_all_original_raw_receipts_join_version_and_every_signed_artifact(
    consumer, original
):
    receipt, components, arch = original
    result = consumer.bind_components(receipt, components, arch)
    assert set(result["components"]) == {
        "codecs",
        "ffmpeg",
        "gosu",
        "jxl",
        "resdet",
        "security",
    }
    assert result["source_files_verified"] == 506
    assert result["component_artifacts_verified"] == 77
    assert result["complete_package_entries_verified"] == 591
    assert result["full_runtime_source_acceptance"] is False


def test_legacy_receipts_require_exact_retained_packager_bytes(
    consumer, original, monkeypatch, tmp_path
):
    receipt, components, arch = original
    retained = tmp_path / "retained/package_alpine_native-v1.py.gz"
    retained.parent.mkdir()
    raw = gzip.decompress(
        (ROOT / "scripts/retained/package_alpine_native-v1.py.gz").read_bytes()
    )
    retained.write_bytes(gzip.compress(raw, mtime=0))
    monkeypatch.setattr(consumer, "__file__", str(tmp_path / "alpine_owned_native.py"))
    consumer.bind_components(receipt, components, arch)
    retained.write_bytes(gzip.compress(b"X" + raw[1:], mtime=0))
    with pytest.raises(consumer.InputError, match="packager bytes differ"):
        consumer.bind_components(receipt, components, arch)


def test_current_producer_binds_numeric_version_and_exact_helper(consumer, original):
    receipt, components, arch = original
    receipt = copy.deepcopy(receipt)
    helper = Path(consumer.package_alpine_native.__file__).read_bytes()
    receipt["helper_sha256"] = hashlib.sha256(helper).hexdigest()
    receipt["package"]["version"] = consumer.package_version(
        receipt["components"], receipt["helper_sha256"]
    )
    helper_row = next(
        row
        for row in receipt["installed_artifacts"]
        if row["path"].endswith("/owned/package_alpine_native.py")
    )
    helper_row.update(sha256=receipt["helper_sha256"], size=len(helper))
    assert consumer.bind_components(receipt, components, arch)["architecture"] == arch
    receipt["package"]["version"] = consumer.package_version(
        receipt["components"], receipt["helper_sha256"], legacy=True
    )
    with pytest.raises(consumer.InputError, match="package version differs"):
        consumer.bind_components(receipt, components, arch)


@pytest.mark.parametrize(
    "mutation",
    [
        "receipt-bytes",
        "missing-component",
        "extra-component",
        "cross-architecture",
        "component-recipe",
        "source-omission",
        "runtime-omission",
        "ffmpeg-composition",
        "package-version",
        "packager-bytes",
        "unattributed-package-file",
        "loader-content",
        "receipt-path-collision",
    ],
)
def test_package_label_and_self_reported_status_never_clear_incomplete_components(
    consumer, original, mutation
):
    receipt, raw, arch = original
    receipt, raw = copy.deepcopy(receipt), dict(raw)
    if mutation == "receipt-bytes":
        raw["resdet"] += b" "
    elif mutation == "missing-component":
        raw.pop("gosu")
    elif mutation == "extra-component":
        raw["unreviewed"] = raw["gosu"]
    elif mutation == "cross-architecture":
        arch = "arm64" if arch == "amd64" else "amd64"
    elif mutation in {
        "component-recipe",
        "source-omission",
        "runtime-omission",
        "ffmpeg-composition",
        "receipt-path-collision",
    }:
        name = "ffmpeg" if mutation == "ffmpeg-composition" else "resdet"
        record = json.loads(raw[name])
        if mutation == "component-recipe":
            record["recipe_sha256"] = "0" * 64
        elif mutation == "source-omission":
            record["source_files"].pop()
        elif mutation == "runtime-omission":
            record["owned_artifacts"].pop()
        elif mutation == "receipt-path-collision":
            record["owned_artifacts"][0]["path"] = (
                "usr/share/ecm/provenance/owned/resdet.json"
            )
        else:
            record["component_receipts"]["codecs"]["architecture"] = "unreviewed"
        raw[name] = (json.dumps(record, sort_keys=True) + "\n").encode()
        receipt["components"][name]["receipt_sha256"] = hashlib.sha256(
            raw[name]
        ).hexdigest()
        receipt["package"]["version"] = consumer.package_version(
            receipt["components"], receipt["helper_sha256"], legacy=True
        )
    elif mutation == "package-version":
        receipt["package"]["version"] = "1.0~" + "0" * 64 + "-r0"
    elif mutation == "packager-bytes":
        receipt["helper_sha256"] = "0" * 64
    elif mutation == "unattributed-package-file":
        receipt["installed_artifacts"].pop()
    else:
        loader = next(
            row
            for row in receipt["installed_artifacts"]
            if row["path"].startswith("etc/ld-musl-")
        )
        loader["sha256"] = "0" * 64
    with pytest.raises(consumer.InputError):
        consumer.bind_components(receipt, raw, arch)


@pytest.fixture
def live(tmp_path):
    directory = tmp_path / "usr/lib"
    directory.mkdir(parents=True)
    file = directory / "library.so.1"
    file.write_bytes(b"original signed file bytes")
    file.chmod(0o755)
    (directory / "library.so").symlink_to(file.name)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    entries = [
        {
            "path": "usr/lib/library.so.1",
            "type": "file",
            "sha256": digest,
            "size": file.stat().st_size,
            "mode": 0o755,
        },
        {
            "path": "usr/lib/library.so",
            "type": "symlink",
            "target": file.name,
            "target_sha256": digest,
        },
    ]
    directories = {"usr": "0:0:755", "usr/lib": "0:0:755"}
    # Synthetic subjects model the current test user's identity. Production's
    # caller uses the original APK's already-joined raw root-owned DB attributes.
    directories = {
        key: value.replace("0:0:", f"{os.getuid()}:{os.getgid()}:")
        for key, value in directories.items()
    }
    return tmp_path, entries, directories


def test_live_regular_link_and_parent_identity_are_all_checked(consumer, live):
    root, entries, directories = live
    result = consumer.bind_live_paths(root, entries, directories)
    assert result["regular_files_verified"] == result["symlinks_verified"] == 1
    assert result["directories_verified"] == 2
    assert result["full_runtime_source_acceptance"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "same-size-file",
        "file-mode",
        "file-to-symlink",
        "symlink-text",
        "missing-file",
        "directory-mode",
        "parent-symlink",
        "root-symlink",
        "unsafe-path",
        "directory-owner",
    ],
)
def test_live_corruption_never_clears_on_matching_package_database(
    consumer, live, tmp_path, mutation
):
    root, entries, directories = live
    file = root / "usr/lib/library.so.1"
    if mutation == "same-size-file":
        file.write_bytes(b"X" + file.read_bytes()[1:])
    elif mutation == "file-mode":
        file.chmod(0o700)
    elif mutation == "file-to-symlink":
        file.rename(file.with_name("replacement"))
        file.symlink_to("replacement")
    elif mutation == "symlink-text":
        link = file.with_name("library.so")
        link.unlink()
        link.symlink_to("./library.so.1")
    elif mutation == "missing-file":
        file.unlink()
    elif mutation == "directory-mode":
        file.parent.chmod(0o700)
    elif mutation == "parent-symlink":
        file.parent.rename(root / "redirect")
        (root / "usr/lib").symlink_to("../redirect")
    elif mutation == "root-symlink":
        alias = root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif mutation == "unsafe-path":
        entries[0]["path"] = "../operator-file"
    else:
        directories["usr/lib"] = "4294967294:4294967294:755"
    with pytest.raises(consumer.InputError):
        consumer.bind_live_paths(root, entries, directories)


@pytest.mark.parametrize("mutation", [None, "same-size", "wrong-size", "symlink"])
def test_original_archive_hash_and_size_are_not_replaced_by_receipt_inventory(
    consumer, tmp_path, mutation
):
    path = tmp_path / "original.tar.gz"
    data = b"original exact upstream archive bytes"
    path.write_bytes(data)
    item = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if mutation == "same-size":
        path.write_bytes(b"X" + data[1:])
    elif mutation == "wrong-size":
        item["size"] += 1
    elif mutation == "symlink":
        path.rename(tmp_path / "substitute")
        path.symlink_to("substitute")
    if mutation is None:
        consumer.check_input(path, item)
    else:
        with pytest.raises(consumer.InputError):
            consumer.check_input(path, item)
