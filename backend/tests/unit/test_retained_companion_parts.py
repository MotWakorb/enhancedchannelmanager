"""Exact real GCC/SQLite sources, including byte-identical bounded GCC parts."""

import gzip
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "docker/native-cache"
ANCHOR = "93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123"
RELEASE = "openSUSE-release-20261105-4389.1.src.rpm"
GCC = "gcc16-16.2.1+git9713-1.1.src.rpm"
GCC_HASH = "e0eb6f15a65dec740af17424092d643806a00f40d9da227fea8825066efe87fa"
PARTS = [
    "1fb48b10a9fad6e9ce3da8571c3278ca1d53ea5e614e8a0f321061b6f39a0289",
    "f5fc9ab689b3e5069bcc23cf19c498d90b6c0f523b60cfd6594b595d92aa6339",
]
SQLITE = "sqlite3-3.54.1-1.1.src.rpm"
SQLITE_HASH = "f42c07f564efc29fa6b7f8edbbfaa2636d00682c50bc2d3893984d211374e629"


@pytest.mark.parametrize("mutation", [None, "gcc-bytes", "sqlite-provenance"])
def test_actual_four_source_delivery_reaches_independent_consumer(
    tmp_path, monkeypatch, mutation
):
    spec = importlib.util.spec_from_file_location(
        "four_source_consumer", ROOT / "tests/native_image/source_checks.py"
    )
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    fixtures = ROOT / "backend/tests/fixtures/native_sources"
    index = json.loads(
        gzip.decompress((fixtures / "retained-four-amd-sources.json.gz").read_bytes())
    )
    inventory = gzip.decompress(
        (fixtures / "retained-four-amd-sources.tsv.gz").read_bytes()
    )
    directory = tmp_path / "usr/share/ecm/sources/rpm"
    (directory / "sources").mkdir(parents=True)
    for record in index["sources"]:
        target = directory / record["path"]
        if record["filename"] == GCC:
            with target.open("xb") as output:
                for part in PARTS:
                    with (CACHE / part).open("rb") as source:
                        shutil.copyfileobj(source, output)
        else:
            shutil.copyfile(CACHE / record["sha256"], target)
    if mutation == "gcc-bytes":
        with (directory / "sources" / GCC).open("r+b") as f:
            f.write(b"corrupt")
    elif mutation == "sqlite-provenance":
        next(row for row in index["sources"] if row["filename"] == SQLITE)[
            "retained_sha256"
        ] = "0" * 64
    (directory / "inventory.tsv").write_bytes(inventory)
    (directory / "index.json").write_text(json.dumps(index))
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=inventory.decode()),
    )
    if mutation:
        with pytest.raises(AssertionError):
            gate._rpm_sources(tmp_path, "x86_64")
    else:
        gate._rpm_sources(tmp_path, "x86_64")


@pytest.fixture
def collector():
    spec = importlib.util.spec_from_file_location(
        "companion_parts_collector", ROOT / "scripts/collect_native_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_gcc_and_sqlite_records_select_only_the_same_original_bytes(collector):
    result = collector.retained_release_source(
        CACHE / ANCHOR,
        "x86_64",
        [(RELEASE, "openSUSE"), (GCC, "openSUSE"), (SQLITE, "openSUSE")],
    )
    assert result[GCC] == (tuple(CACHE / part for part in PARTS), GCC_HASH)
    assert result[SQLITE] == (CACHE / SQLITE_HASH, SQLITE_HASH)
    whole = hashlib.sha256()
    for part, size in zip(PARTS, [67108864, 41789839], strict=True):
        path = CACHE / part
        assert path.stat().st_size == size
        assert collector.sha256(path) == part
        with path.open("rb") as f:
            while block := f.read(1024**2):
                whole.update(block)
    assert whole.hexdigest() == GCC_HASH
    assert collector.sha256(CACHE / SQLITE_HASH) == SQLITE_HASH
    for name in [GCC, SQLITE]:
        assert (
            collector.source_url("openSUSE", "x86_64", name)
            == "https://download.opensuse.org/tumbleweed/repo/src-oss/src/" + name
        )
    assert collector.retained_release_source(None, "x86_64", [(GCC, "openSUSE")]) == {}


@pytest.mark.parametrize("mutation", ["missing", "corrupt", "swapped", "symlink"])
def test_selected_gcc_parts_are_pinned_not_an_unbounded_concatenation(
    collector, tmp_path, mutation
):
    shutil.copyfile(CACHE / ANCHOR, tmp_path / ANCHOR)
    for part in PARTS:
        (tmp_path / part).write_bytes(b"untrusted incomplete part")
    if mutation == "missing":
        (tmp_path / PARTS[0]).unlink()
    elif mutation == "symlink":
        (tmp_path / PARTS[0]).unlink()
        (tmp_path / PARTS[0]).symlink_to(CACHE / PARTS[0])
    elif mutation == "corrupt":
        shutil.copyfile(CACHE / PARTS[0], tmp_path / PARTS[0])
        with (tmp_path / PARTS[0]).open("r+b") as f:
            f.write(b"X")
    else:
        shutil.copyfile(CACHE / PARTS[1], tmp_path / PARTS[0])
    with pytest.raises(ValueError, match="retained release source"):
        collector.retained_release_source(
            tmp_path / ANCHOR, "x86_64", [(RELEASE, "openSUSE"), (GCC, "openSUSE")]
        )


@pytest.mark.parametrize("corrupt_join", [False, True])
def test_reassembled_whole_source_hash_is_checked_before_any_trust_import(
    collector, tmp_path, monkeypatch, corrupt_join
):
    inventory = tmp_path / "inventory.tsv"
    inventory.write_text(
        f"libgcc_s1\t16.2.1+git9713\t1.1\tx86_64\topenSUSE\t{GCC}\tGPL-3.0-or-later WITH GCC-exception-3.1\nopenSUSE-release\t20261105\t4389.1\tx86_64\topenSUSE\t{RELEASE}\tBSD-3-Clause\n"
    )
    out = tmp_path / "output"
    verified = []

    def key(*args):
        assert collector.sha256(out / "sources" / (GCC + ".partial")) == GCC_HASH
        return tmp_path / "db"

    def verify(path, name, vendor, db, seconds):
        verified.append(name)
        assert collector.sha256(path) == (GCC_HASH if name == GCC else ANCHOR)
        return {
            "filename": name,
            "vendor": vendor,
            "sha256": collector.sha256(path),
            "fingerprint": collector.REPOSITORIES[vendor]["fingerprint"],
        }

    monkeypatch.setattr(collector, "prepare_key", key)
    monkeypatch.setattr(collector, "verify_source", verify)
    monkeypatch.setattr(collector, "download", lambda *args: pytest.fail("No fallback"))
    if corrupt_join:
        monkeypatch.setattr(
            collector.shutil,
            "copyfileobj",
            lambda source, dest: dest.write(b"corrupt joined bytes"),
        )
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
        str(CACHE / ANCHOR),
    ]
    if corrupt_join:
        with pytest.raises(ValueError, match="copied checksum mismatch"):
            collector.main(args)
        assert verified == [] and not (out / "index.json").exists()
    else:
        collector.main(args)
        record = next(
            row
            for row in json.loads((out / "index.json").read_text())["sources"]
            if row["filename"] == GCC
        )
        assert record["retained_sha256"] == record["sha256"] == GCC_HASH
        assert set(verified) == {GCC, RELEASE}
