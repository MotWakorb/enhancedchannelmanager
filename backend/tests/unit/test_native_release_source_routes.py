"""Exact release-source recovery, not generic mirror/cache permission."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
RECORDED = json.loads(
    (
        ROOT / "backend/tests/fixtures/native_sources/release-20261105-routes.json"
    ).read_text()
)
AMD = RECORDED["x86_64"]
ARM = RECORDED["aarch64"]
GOOD = b"synthetic matching archive stand-in, not an RPM"
VENDOR = "openSUSE"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def collector():
    return load("release_source_collector", "scripts/collect_native_sources.py")


def test_only_one_actual_architecture_vendor_filename_is_rerouted(collector):
    assert collector.EXACT_SOURCE_ROUTES == {
        (VENDOR, "x86_64", AMD["filename"]): {
            "url": AMD["url"],
            "sha256": AMD["sha256"],
        }
    }
    assert collector.source_url(VENDOR, "x86_64", AMD["filename"]) == AMD["url"]
    assert collector.source_url(VENDOR, "aarch64", ARM["filename"]) == ARM["url"]
    assert AMD["sha256"] != ARM["sha256"] and AMD["size"] != ARM["size"]
    for vendor, arch, filename in [
        (VENDOR, "aarch64", AMD["filename"]),
        (VENDOR, "x86_64", "openSUSE-release-20261106-4390.1.src.rpm"),
        ("http://packman.links2linux.de", "x86_64", AMD["filename"]),
    ]:
        assert (
            collector.source_url(vendor, arch, filename)
            == collector.REPOSITORIES[vendor]["urls"][arch] + "src/" + filename
        )


def test_expected_cache_index_cannot_relabel_old_canonical_route(collector, tmp_path):
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {
                "architecture": "x86_64",
                "sources": [
                    {
                        "filename": AMD["filename"],
                        "sha256": AMD["sha256"],
                        "url": collector.REPOSITORIES[VENDOR]["urls"]["x86_64"]
                        + "src/"
                        + AMD["filename"],
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="repository disagrees"):
        collector.expected_hashes(expected, "x86_64", [(AMD["filename"], VENDOR)])


def fake_hash(path):
    return (
        AMD["sha256"]
        if path.read_bytes() == GOOD
        else hashlib.sha256(path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "body,passes", [(GOOD, True), (b"wrong source archive", False)]
)
def test_exact_recovery_download_enforces_pinned_whole_archive_hash(
    collector, monkeypatch, tmp_path, body, passes
):
    requests = []

    class Response(io.BytesIO):
        def geturl(self):
            return AMD["url"]

    def open_url(url, **kwargs):
        requests.append(url)
        return Response(body)

    monkeypatch.setattr(
        collector.urllib.request,
        "build_opener",
        lambda *a: SimpleNamespace(open=open_url),
    )
    monkeypatch.setattr(collector, "sha256", fake_hash)
    target = tmp_path / "source.partial"
    if passes:
        assert collector.download(
            AMD["url"], target, collector.Budget(1, 4096, 10)
        ) == {"url": AMD["url"], "final_url": AMD["url"]}
    else:
        with pytest.raises(ValueError, match="pinned source checksum"):
            collector.download(AMD["url"], target, collector.Budget(1, 4096, 10))
    assert requests == [AMD["url"]]


@pytest.mark.parametrize(
    "url",
    [
        AMD["url"] + "?retry=1",
        AMD["url"].replace("4389.1", "4390.1"),
        "https://ftp.gwdg.de/pub/opensuse/ports/aarch64/source/tumbleweed/repo/oss/src/"
        + ARM["filename"],
    ],
)
def test_no_generic_official_mirror_prefix_or_other_arch_route_is_added(
    collector, tmp_path, url
):
    with pytest.raises(ValueError, match="only documented exact public source URLs"):
        collector.download(url, tmp_path / "refused", collector.Budget(1, 4096, 10))


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("tampered", [False, True])
def test_actual_collection_replays_authentication_and_rejects_self_consistent_wrong_archive(
    collector, monkeypatch, tmp_path, cached, tampered
):
    inventory = tmp_path / "inventory.tsv"
    inventory.write_text(
        "openSUSE-release\t20261105\t4389.1\tx86_64\topenSUSE\t"
        + AMD["filename"]
        + "\tBSD-3-Clause\n"
    )
    body = b"wrong but advertised archive" if tampered else GOOD
    monkeypatch.setattr(collector, "sha256", fake_hash)
    monkeypatch.setattr(collector, "prepare_key", lambda *a: tmp_path / "isolated-db")
    verified = []

    def verify(path, filename, vendor, database, seconds):
        verified.append(filename)
        assert path.read_bytes() == GOOD and database == tmp_path / "isolated-db"
        return {
            "filename": filename,
            "vendor": vendor,
            "sha256": AMD["sha256"],
            "fingerprint": collector.REPOSITORIES[VENDOR]["fingerprint"],
        }

    monkeypatch.setattr(collector, "verify_source", verify)

    def download(url, path, budget):
        assert not cached and url == AMD["url"]
        path.write_bytes(body)
        return {"url": url, "final_url": url}

    monkeypatch.setattr(collector, "download", download)
    output = tmp_path / "output"
    args = [
        "--inventory",
        str(inventory),
        "--arch",
        "x86_64",
        "--out",
        str(output),
        "--suse-key",
        str(tmp_path / "suse.asc"),
    ]
    if cached:
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / AMD["filename"]).write_bytes(body)
        expected = tmp_path / "expected.json"
        # Even a self-consistent caller hash cannot override the reviewed pin.
        expected.write_text(
            json.dumps(
                {
                    "architecture": "x86_64",
                    "sources": [
                        {
                            "filename": AMD["filename"],
                            "url": AMD["url"],
                            "sha256": fake_hash(cache / AMD["filename"]),
                        }
                    ],
                }
            )
        )
        args += ["--cache-dir", str(cache), "--expected-sha256", str(expected)]
    if tampered:
        with pytest.raises(ValueError, match="pinned source checksum"):
            collector.main(args)
        assert verified == [] and not (output / "index.json").exists()
    else:
        collector.main(args)
        result = json.loads((output / "index.json").read_text())
        assert verified == [AMD["filename"]]
        assert result["sources"][0]["url"] == AMD["url"]
        assert result["sources"][0]["final_url"] == (None if cached else AMD["url"])


@pytest.mark.parametrize("tampered", [False, True])
@pytest.mark.parametrize(
    "retention", ["fresh", "retained", "wrong-pin", "download-claim", "missing-label"]
)
def test_final_source_consumer_enforces_exact_route_and_archive_pin(
    monkeypatch, tmp_path, tampered, retention
):
    consumer = load("release_source_consumer", "tests/native_image/source_checks.py")
    directory = tmp_path / "usr/share/ecm/sources/rpm"
    (directory / "sources").mkdir(parents=True)
    body = b"self-consistent wrong archive" if tampered else GOOD
    archive = directory / "sources" / AMD["filename"]
    archive.write_bytes(body)
    actual_sha = hashlib.sha256(body).hexdigest() if tampered else AMD["sha256"]
    original_sha = consumer.sha256
    monkeypatch.setattr(
        consumer,
        "sha256",
        lambda path: fake_hash(path) if path == archive else original_sha(path),
    )
    row = {
        "name": "openSUSE-release",
        "version": "20261105",
        "release": "4389.1",
        "arch": "x86_64",
        "vendor": VENDOR,
        "sourcerpm": AMD["filename"],
        "license": "BSD-3-Clause",
    }
    inventory = "\t".join(row[k] for k in consumer.FIELDS) + "\n"
    (directory / "inventory.tsv").write_text(inventory)
    source = {
        "filename": AMD["filename"],
        "vendor": VENDOR,
        "url": AMD["url"],
        "sha256": actual_sha,
        "size": len(body),
        "path": "sources/" + AMD["filename"],
        "fingerprint": consumer.VENDORS[VENDOR],
        "authentication": "pinned-rpm-signature-and-exact-header",
        "license": "BSD-3-Clause",
        "payload": {
            "declared_input_payload_complete": True,
            "nosource": [],
            "nopatch": [],
            "spec_names": ["openSUSE-release.spec"],
            "source": ["weakremovers.inc"],
            "patch": [],
            "files": [
                {"name": "openSUSE-release.spec", "sha256": "1" * 64, "size": 10},
                {"name": "weakremovers.inc", "sha256": "2" * 64, "size": 10},
            ],
        },
    }
    if retention != "fresh":
        source.update(
            retrieval="reverified-retained-source",
            retained_sha256=AMD["sha256"],
            final_url=None,
        )
        if retention == "wrong-pin":
            source["retained_sha256"] = "0" * 64
        elif retention == "download-claim":
            source["final_url"] = AMD["url"]
        elif retention == "missing-label":
            source["retrieval"] = "reverified-cache"
    index = {
        "schema": 1,
        "architecture": "x86_64",
        "packages": [row],
        "excluded_public_keys": [],
        "sources": [source],
        "inventory_sha256": hashlib.sha256(inventory.encode()).hexdigest(),
        "package_set_sha256": consumer._canonical([row]),
        "source_set_sha256": consumer._canonical(
            [
                {
                    k: source[k]
                    for k in ("filename", "vendor", "url", "sha256", "fingerprint")
                }
            ]
        ),
    }
    (directory / "index.json").write_text(json.dumps(index))
    monkeypatch.setattr(
        consumer.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=inventory)
    )
    if tampered:
        with pytest.raises(AssertionError, match="pinned source"):
            consumer._rpm_sources(tmp_path, "x86_64")
    elif retention in ("wrong-pin", "download-claim", "missing-label"):
        with pytest.raises(AssertionError, match="retained source"):
            consumer._rpm_sources(tmp_path, "x86_64")
    else:
        assert consumer._rpm_sources(tmp_path, "x86_64") == (1, 1)
