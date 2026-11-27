"""Exact recovered source companion; no new runtime provider or generic cache."""

import gzip
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
ANCHOR = "93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123"
RELEASE = "openSUSE-release-20261105-4389.1.src.rpm"
PIN = "4b90c6c666634fe3f97b2045a6556c4ae34709a9f48b53c3c930d32c665b8833"
NAME = "e2fsprogs-1.47.4-1.1.src.rpm"
URL = "https://download.opensuse.org/tumbleweed/repo/src-oss/src/" + NAME
ORIGINS = [(RELEASE, "openSUSE"), (NAME, "openSUSE")]


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "hash",
        "provenance",
        "redirect",
        "url",
        "fingerprint",
        "missing-origin",
        "bytes",
    ],
)
def test_real_native_collector_delivery_crosses_independent_consumer_boundary(
    tmp_path, monkeypatch, mutation
):
    spec = importlib.util.spec_from_file_location(
        "e2fs_source_consumer", ROOT / "tests/native_image/source_checks.py"
    )
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    index = json.loads(
        gzip.decompress(
            (
                ROOT
                / "backend/tests/fixtures/native_sources/retained-amd-source-subset.json.gz"
            ).read_bytes()
        )
    )
    directory = tmp_path / "usr/share/ecm/sources/rpm"
    (directory / "sources").mkdir(parents=True)
    for record in index["sources"]:
        shutil.copyfile(
            ROOT / "docker/native-cache" / record["sha256"], directory / record["path"]
        )
    inventory = (
        ROOT / "backend/tests/fixtures/native_sources/retained-amd-source-subset.tsv"
    ).read_text()
    (directory / "inventory.tsv").write_text(inventory)
    assert hashlib.sha256(inventory.encode()).hexdigest() == index["inventory_sha256"]
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=inventory),
    )
    record = next(row for row in index["sources"] if row["filename"] == NAME)
    if mutation == "hash":
        record["retained_sha256"] = "0" * 64
    elif mutation == "provenance":
        record["retrieval"] = "fresh-download"
    elif mutation == "redirect":
        record["final_url"] = URL
    elif mutation == "url":
        record["url"] = URL.replace("1.47.4", "1.47.5")
    elif mutation == "fingerprint":
        record["fingerprint"] = "0" * 40
    elif mutation == "missing-origin":
        index["sources"].remove(record)
    elif mutation == "bytes":
        (directory / record["path"]).write_bytes(b"corrupt")
    (directory / "index.json").write_text(json.dumps(index))
    if mutation:
        with pytest.raises(AssertionError):
            gate._rpm_sources(tmp_path, "x86_64")
    else:
        gate._rpm_sources(tmp_path, "x86_64")


@pytest.fixture
def collector():
    spec = importlib.util.spec_from_file_location(
        "e2fs_collector", ROOT / "scripts/collect_native_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def retained(tmp_path):
    directory = tmp_path / "retained"
    directory.mkdir()
    for digest in [ANCHOR, PIN]:
        shutil.copyfile(ROOT / "docker/native-cache" / digest, directory / digest)
    return directory / ANCHOR


def test_actual_recovered_source_bytes_select_exact_tuple_and_preserve_url(
    collector, retained
):
    result = collector.retained_release_source(retained, "x86_64", ORIGINS)
    assert result == {RELEASE: (retained, ANCHOR), NAME: (retained.parent / PIN, PIN)}
    assert (retained.parent / PIN).stat().st_size == 7757243
    assert hashlib.sha256((retained.parent / PIN).read_bytes()).hexdigest() == PIN
    assert collector.source_url("openSUSE", "x86_64", NAME) == URL
    tags = collector.source_header(retained.parent / PIN)
    collector.check_identity(tags, NAME, "openSUSE")
    assert tags["1022"] == ["x86_64"]
    assert tags["1014"] == ["GPL-2.0-only"]


@pytest.mark.parametrize(
    "defect",
    [
        "corrupt",
        "truncated",
        "missing",
        "renamed",
        "directory",
        "symlink",
        "parent-symlink",
    ],
)
def test_selected_companion_is_mandatory_exact_regular_canonical_bytes(
    collector, retained, tmp_path, defect
):
    path = retained.parent / PIN
    if defect == "corrupt":
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    elif defect == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    elif defect == "missing":
        path.unlink()
    elif defect == "renamed":
        path.rename(path.with_name(NAME))
    elif defect == "directory":
        path.unlink()
        path.mkdir()
    elif defect == "symlink":
        target = tmp_path / "outside"
        path.rename(target)
        path.symlink_to(target)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(retained.parent, target_is_directory=True)
        retained = alias / ANCHOR
    with pytest.raises(ValueError, match="retained release source"):
        collector.retained_release_source(retained, "x86_64", ORIGINS)


def test_explicit_anchor_architecture_and_exact_origin_are_not_optional(
    collector, retained
):
    assert collector.retained_release_source(None, "x86_64", ORIGINS) == {}
    with pytest.raises(ValueError):
        collector.retained_release_source(retained, "aarch64", ORIGINS)
    with pytest.raises(ValueError):
        collector.retained_release_source(retained, "x86_64", [(NAME, "openSUSE")])
    for origins in [
        [(RELEASE, "openSUSE")],
        [(RELEASE, "openSUSE"), (NAME, "http://packman.links2linux.de")],
        [(RELEASE, "openSUSE"), ("e2fsprogs-1.47.5-1.1.src.rpm", "openSUSE")],
    ]:
        assert collector.retained_release_source(retained, "x86_64", origins) == {
            RELEASE: (retained, ANCHOR)
        }


@pytest.mark.parametrize("failure", [None, "signature", "copy"])
def test_collector_reverifies_both_inputs_and_preserves_complete_cache_contract(
    collector, retained, tmp_path, monkeypatch, failure
):
    inventory = tmp_path / "inventory.tsv"
    inventory.write_text(
        f"openSUSE-release\t20261105\t4389.1\tx86_64\topenSUSE\t{RELEASE}\tBSD-3-Clause\nlibext2fs2\t1.47.4\t1.1\tx86_64\topenSUSE\t{NAME}\tGPL-2.0-only\n"
    )
    out = tmp_path / "out"
    args = [
        "--inventory",
        str(inventory),
        "--arch",
        "x86_64",
        "--out",
        str(out),
        "--suse-key",
        "suse.asc",
        "--retained-release-source",
        str(retained),
    ]
    verified = []

    def prepare(*args):
        for name, digest in [(RELEASE, ANCHOR), (NAME, PIN)]:
            assert collector.sha256(out / "sources" / (name + ".partial")) == digest
        return tmp_path / "db"

    def verify(path, name, vendor, database, seconds):
        verified.append(name)
        assert collector.sha256(path) == {NAME: PIN, RELEASE: ANCHOR}[name]
        if name == NAME and failure == "signature":
            raise ValueError("signature rejected")
        return {
            "filename": name,
            "vendor": vendor,
            "sha256": collector.sha256(path),
            "fingerprint": collector.REPOSITORIES[vendor]["fingerprint"],
        }

    monkeypatch.setattr(collector, "prepare_key", prepare)
    monkeypatch.setattr(collector, "verify_source", verify)
    monkeypatch.setattr(
        collector,
        "download",
        lambda *args: pytest.fail("No retained-source download or fallback"),
    )
    if failure == "copy":
        copy = collector.shutil.copyfile

        def corrupt(src, dst):
            copy(src, dst)
            if src.name == PIN:
                dst.write_bytes(b"changed during copy")

        monkeypatch.setattr(collector.shutil, "copyfile", corrupt)
    if failure:
        with pytest.raises(
            ValueError, match="signature rejected|copied checksum mismatch"
        ):
            collector.main(args)
        assert not (out / "index.json").exists()
        assert verified == ([] if failure == "copy" else [NAME])
    else:
        collector.main(args)
        records = {
            row["filename"]: row
            for row in json.loads((out / "index.json").read_text())["sources"]
        }
        assert set(records) == {NAME, RELEASE} and set(verified) == {NAME, RELEASE}
        assert records[NAME]["url"] == URL and records[NAME]["final_url"] is None
        assert records[NAME]["retrieval"] == "reverified-retained-source"
        assert records[NAME]["retained_sha256"] == PIN
        # An explicitly retained subset never becomes a partial generic cache.
        with pytest.raises(
            ValueError, match="expected SHA256 for every selected source"
        ):
            collector.main([*args, "--cache-dir", str(retained.parent)])
