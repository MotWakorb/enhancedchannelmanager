"""Fresh public-data transport boundary; unit fetches are not TLS proof."""

import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend/tests/fixtures/alpine"


def recorded(name):
    return json.loads(gzip.decompress((FIXTURES / name).read_bytes()))


@pytest.fixture
def snapshot(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_advisory_snapshot", ROOT / "scripts/alpine_advisory_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "part,count,zeros", [("main", 290, 31), ("community", 489, 19)]
)
def test_real_fixed_secdb_keeps_opaque_aliases_and_zero_versions_without_exemptions(
    snapshot, part, count, zeros
):
    result = snapshot.decode_secdb(recorded(f"secdb-{part}-20261109.json.gz"), part)
    assert result["package_count"] == count
    assert sum(row["fixed_version"] == "0" for row in result["fix_records"]) == zeros
    assert any(
        any(not value.startswith("CVE-") or " " in value for value in row["advisories"])
        for row in result["fix_records"]
    )
    assert result["full_runtime_advisory_acceptance"] is False


@pytest.fixture
def capsule(snapshot, tmp_path, monkeypatch):
    # Real original field vocabulary, intentionally bounded subsets for transport
    # unit tests. Complete-recorded-response parsing is tested independently.
    one = recorded("cve-11979-20261109.json.gz")
    inputs = {}
    for part in ["main", "community"]:
        inputs["tracker-" + part] = {
            "@context": one["@context"],
            "id": snapshot.HOST + "/branch/3.24-" + part,
            "items": [one],
            "type": "Collection",
        }
        secdb = recorded(f"secdb-{part}-20261109.json.gz")
        secdb["packages"] = secdb["packages"][:1]
        inputs["secdb-" + part] = secdb
    calls = []

    def fetch(name, descriptor):
        calls.append(name)
        raw = json.dumps(inputs[name]).encode()
        return raw, {
            "url": descriptor["url"],
            "response_url": descriptor["url"],
            "http_status": 200,
            "content_type": "application/json",
            "response_headers": {"Date": "unit transport stand-in"},
        }

    monkeypatch.setattr(snapshot, "fetch_document", fetch)
    out = tmp_path / "snapshot"
    snapshot.prepare(out)
    return out, calls


def test_fresh_closed_snapshot_rehashes_original_bytes_and_trusted_copier(
    snapshot, capsule
):
    out, calls = capsule
    result = snapshot.read_snapshot(out)
    assert set(calls) == set(snapshot.SOURCES) and len(calls) == 4
    assert set(result["trackers"]) == {"3.24-main", "3.24-community"}
    assert set(result["secdb"]) == {"main", "community"}
    assert result["index"]["full_runtime_advisory_acceptance"] is False
    assert len([p for p in out.rglob("*") if p.is_file()]) == 6


@pytest.mark.parametrize(
    "mutation",
    [
        "stale",
        "future",
        "small-future",
        "naive-time",
        "extra-file",
        "extra-dir",
        "corrupt-gzip",
        "copier",
        "duplicate-json",
        "redirect",
        "wrong-response-type",
        "false-clearance",
        "boolean-schema",
        "input-alias",
        "http200-empty-collection",
        "resealed-extra-gzip-stream",
        "underdeclared-raw-size",
    ],
)
def test_snapshot_rejects_incomplete_stale_or_forged_evidence(
    snapshot, capsule, tmp_path, mutation
):
    out, _calls = capsule
    path = out / "index.json"
    index = json.loads(path.read_text())
    if mutation in {"stale", "future", "small-future", "naive-time"}:
        stamp = datetime.now(UTC) + (
            timedelta(days=-1) if mutation == "stale" else timedelta(days=1)
        )
        if mutation == "small-future":
            stamp = datetime.now(UTC) + timedelta(seconds=60)
        if mutation == "naive-time":
            stamp = stamp.replace(tzinfo=None)
        index["captured_at"] = stamp.isoformat()
    elif mutation == "extra-file":
        (out / "private.pem").write_bytes(b"unselected")
    elif mutation == "extra-dir":
        (out / "unselected").mkdir()
    elif mutation == "corrupt-gzip":
        next(out.glob("*.gz")).write_bytes(b"bad gzip")
    elif mutation == "copier":
        index["helper_sha256"] = "0" * 64
    elif mutation == "duplicate-json":
        path.write_text('{"schema_version":1,' + json.dumps(index)[1:])
    elif mutation == "redirect":
        index["records"][0]["response_url"] = "https://other.invalid/data"
    elif mutation == "wrong-response-type":
        index["records"][0]["content_type"] = "text/html"
    elif mutation == "false-clearance":
        index["full_runtime_advisory_acceptance"] = True
    elif mutation == "boolean-schema":
        index["schema_version"] = True
    elif mutation in {
        "http200-empty-collection",
        "resealed-extra-gzip-stream",
        "underdeclared-raw-size",
    }:
        row = next(
            row for row in index["records"] if row["name"] == "tracker-community"
        )
        document = out / row["file"]
        if mutation == "http200-empty-collection":
            raw = json.dumps(
                {
                    "@context": "https://security.alpinelinux.org/static/context.jsonld",
                    "id": "https://security.alpinelinux.org/branch/3.24-community",
                    "items": [],
                    "type": "Collection",
                }
            ).encode()
            document.write_bytes(gzip.compress(raw, mtime=0))
            row["raw_size"] = len(raw)
            row["raw_sha256"] = hashlib.sha256(raw).hexdigest()
        elif mutation == "resealed-extra-gzip-stream":
            document.write_bytes(
                document.read_bytes() + gzip.compress(b"unselected stream", mtime=0)
            )
        else:
            row["raw_size"] = 1
        row["size"] = document.stat().st_size
        row["sha256"] = hashlib.sha256(document.read_bytes()).hexdigest()
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(out, target_is_directory=True)
        out = alias
    if mutation != "duplicate-json":
        path.write_text(json.dumps(index))
    with pytest.raises(snapshot.InputError):
        snapshot.read_snapshot(out)


def test_source_acquisition_never_overwrites_operator_output(
    snapshot, capsule, monkeypatch
):
    out, _calls = capsule

    def forbidden(*args, **kwargs):
        pytest.fail("existing output must fail before HTTP")

    monkeypatch.setattr(snapshot, "fetch_document", forbidden)
    before = (out / "index.json").read_bytes()
    with pytest.raises(snapshot.InputError):
        snapshot.prepare(out)
    assert (out / "index.json").read_bytes() == before


def test_invalid_http200_body_is_retained_outside_unpublished_snapshot(
    snapshot, tmp_path, monkeypatch
):
    name = "tracker-main"
    descriptor = snapshot.SOURCES[name]
    raw = json.dumps(
        {
            "@context": "https://security.alpinelinux.org/static/context.jsonld",
            "id": descriptor["url"],
            "items": [],
            "type": "Collection",
        }
    ).encode()
    response = {
        "url": descriptor["url"],
        "response_url": descriptor["url"],
        "http_status": 200,
        "content_type": "application/json",
        "response_headers": {"Date": "unit transport stand-in"},
    }
    monkeypatch.setattr(snapshot, "fetch_document", lambda *args: (raw, response))
    output = tmp_path / "snapshot"
    evidence = tmp_path / "acquisition"
    with pytest.raises(snapshot.InputError):
        snapshot.prepare(output, evidence=evidence)
    assert not output.exists()
    assert gzip.decompress((evidence / (name + ".json.gz")).read_bytes()) == raw
    record = json.loads((evidence / (name + "-response.json")).read_text())
    assert record["status"] == "RAW_RESPONSE_RECEIVED_NOT_VALIDATED"
    assert record["response"]["http_status"] == 200
