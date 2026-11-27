"""Source acquisition cannot replace exact signed subjects or bypass bad bytes."""

import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def producer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_alpine_external_sources",
        ROOT / "scripts/prepare_alpine_external_sources.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plan(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from alpine_source_inventory import source_plan

    fixtures = ROOT / "backend/tests/fixtures/alpine"
    return source_plan(
        {
            "installed_database": (
                fixtures / "source-inventory-installed.txt"
            ).read_text(),
            "packages": json.loads(
                (fixtures / "source-inventory-query.json").read_text()
            ),
        },
        "x86_64",
    )


def test_complete_selection_uses_recorded_actual_identity_fields(producer, plan):
    rows = [p["fields"] for p in plan["packages"] if p["name"] != ".python-rundeps"]
    selected = producer.select_packages(plan, {"main": rows})
    assert set(selected) == {"musl", "musl-utils", "skalibs-libs", "utmps-libs"}
    assert all(repo == "main" and row in rows for repo, row in selected.values())


@pytest.mark.parametrize(
    "change", ["missing", "duplicate-repository", "changed-checksum", "changed-origin"]
)
def test_selection_cannot_fetch_a_different_package_under_same_name(
    producer, plan, change
):
    rows = [
        dict(p["fields"]) for p in plan["packages"] if p["name"] != ".python-rundeps"
    ]
    indexes = {"main": rows}
    if change == "missing":
        rows.pop()
    elif change == "duplicate-repository":
        indexes["community"] = rows
    else:
        rows[0]["C" if change == "changed-checksum" else "o"] = "other"
    with pytest.raises(producer.InputError):
        producer.select_packages(plan, indexes)


def test_current_krb5_route_is_exact_official_original_source_not_a_version_substitution(producer):
    route = producer.UPSTREAM_ROUTES[("krb5", "krb5-1.22.3.tar.gz")]
    assert route == {
        "sha512": "0276d83dee90e76d50a506d22901e658212094c1191fc09b8510273b4bfc56195ab1dd1c80b5fd8288b4aa9323c62f0b60ab3f6227ea113b4712bc54cb144abb",
        "url": "https://web.mit.edu/kerberos/dist/krb5/1.22/krb5-1.22.3.tar.gz",
    }


def test_wrong_source_bytes_never_try_an_alternate_url(producer, tmp_path, monkeypatch):
    calls = []

    def download(url, path):
        calls.append(url)
        path.write_bytes(b"wrong source bytes")
        return {
            "url": url,
            "status": 200,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }

    monkeypatch.setattr(producer, "download_url", download)
    with pytest.raises(producer.SourceFetchError, match="SHA512"):
        producer.fetch_source(
            "component", "source.tar.gz", "a" * 128, tmp_path / "source"
        )
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_http_failure_without_exact_authorized_route_has_no_fallback(
    producer, tmp_path, monkeypatch, status
):
    calls = []

    def download(url, path):
        calls.append(url)
        raise HTTPError(url, status, "recorded failure", {}, None)

    monkeypatch.setattr(producer, "download_url", download)
    with pytest.raises(producer.SourceFetchError):
        producer.fetch_source(
            "unknown", "source.tar.gz", "a" * 128, tmp_path / "source"
        )
    assert len(calls) == 1


def test_only_recorded404_can_select_an_exact_checksum_bound_upstream_route(
    producer, tmp_path, monkeypatch
):
    data = b"original source fixture"
    expected = hashlib.sha512(data).hexdigest()
    calls = []
    monkeypatch.setattr(
        producer,
        "UPSTREAM_ROUTES",
        {
            ("component", "source.tar.gz"): {
                "sha512": expected,
                "url": "https://official.example/source.tar.gz",
            }
        },
    )

    def download(url, path):
        calls.append(url)
        if len(calls) == 1:
            raise HTTPError(url, 404, "Not Found", {}, None)
        path.write_bytes(data)
        return {
            "url": url,
            "status": 200,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    monkeypatch.setattr(producer, "download_url", download)
    result = producer.fetch_source(
        "component", "source.tar.gz", expected, tmp_path / "source"
    )
    assert len(calls) == 2 and calls[-1] == "https://official.example/source.tar.gz"
    assert result["attempts"][0]["status"] == 404 and result["sha512"] == expected


@pytest.mark.parametrize("status", [401, 403])
def test_even_an_authorized_upstream_route_cannot_hide_authentication_failure(
    producer, tmp_path, monkeypatch, status
):
    calls = []
    monkeypatch.setattr(
        producer,
        "UPSTREAM_ROUTES",
        {
            ("component", "source.tar.gz"): {
                "sha512": "a" * 128,
                "url": "https://official.example/source.tar.gz",
            }
        },
    )

    def download(url, path):
        calls.append(url)
        raise HTTPError(url, status, "denied", {}, None)

    monkeypatch.setattr(producer, "download_url", download)
    with pytest.raises(producer.SourceFetchError):
        producer.fetch_source(
            "component", "source.tar.gz", "a" * 128, tmp_path / "source"
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "which", ["output-exists", "work-exists", "overlap", "receipt-inside"]
)
def test_acquisition_never_overwrites_or_mixes_input_and_evidence_paths(
    producer, tmp_path, which
):
    out = tmp_path / "out"
    work = tmp_path / "work"
    receipt = tmp_path / "receipt.json"
    if which == "output-exists":
        out.mkdir()
    elif which == "work-exists":
        work.mkdir()
    elif which == "overlap":
        work = out / "work"
    else:
        receipt = out / "receipt.json"
    with pytest.raises(producer.InputError):
        producer.validate_destinations(out, work, receipt)
