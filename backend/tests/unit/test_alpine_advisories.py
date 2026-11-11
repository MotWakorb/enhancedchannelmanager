"""Full recorded official JSON-LD, including real duplicate/foreign-branch rows."""

import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend/tests/fixtures/alpine"


def recorded(name):
    return json.loads(gzip.decompress((FIXTURES / name).read_bytes()))


@pytest.fixture
def parser(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_advisories", ROOT / "scripts/alpine_advisories.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "part,items,unique,seen,states",
    [("main", 185, 185, 292, 292), ("community", 1996, 1641, 3853, 3091)],
)
def test_complete_real_branch_response_is_not_a_guessed_flat_advisory_list(
    parser, part, items, unique, seen, states
):
    result = parser.decode_tracker(
        recorded(f"tracker-{part}-20261109.json.gz"), "3.24-" + part
    )
    assert result["items_seen"] == items
    assert result["unique_vulnerabilities"] == unique
    assert result["states_seen"] == seen
    assert len(result["states"]) == states
    assert {row["repository"] for row in result["states"]} == {"3.24-" + part}
    assert all(type(row["fixed"]) is bool for row in result["states"])
    assert all(
        row["severity"] == "UNKNOWN"
        for row in result["states"]
        if row["cvss3"]["vector"] is None
    )


def one_cve():
    return {
        "@context": "https://security.alpinelinux.org/static/context.jsonld",
        "id": "https://security.alpinelinux.org/branch/3.24-main",
        "items": [recorded("cve-11979-20261109.json.gz")],
        "type": "Collection",
    }


def test_real_unscored_libxml_catalog_advisory_is_unfixed_not_safe_or_other_branch(
    parser,
):
    result = parser.decode_tracker(one_cve(), "3.24-main")
    assert len(result["states"]) == 1
    row = result["states"][0]
    assert (row["source"], row["version"], row["cve"]) == (
        "libxml2",
        "2.13.9-r2",
        "CVE-2026-11979",
    )
    assert row["fixed"] is False and row["severity"] == "UNKNOWN"
    assert row["cvss3"] == {"score": 0.0, "vector": None}


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-root-branch",
        "string-false",
        "boolean-score",
        "unknown-state-type",
        "wrong-vuln-link",
        "unsafe-package-url",
        "conflicting-duplicate",
    ],
)
def test_malformed_or_ambiguous_tracker_input_fails_closed(parser, mutation):
    doc = one_cve()
    item = doc["items"][0]
    state = next(row for row in item["state"] if row["repo"] == "3.24-main")
    if mutation == "wrong-root-branch":
        doc["id"] = "https://security.alpinelinux.org/branch/edge-main"
    elif mutation == "string-false":
        state["fixed"] = "false"
    elif mutation == "boolean-score":
        item["cvss3"]["score"] = False
    elif mutation == "unknown-state-type":
        state["type"] = "SuppressedState"
    elif mutation == "wrong-vuln-link":
        state["vuln"] = "https://security.alpinelinux.org/vuln/CVE-2000-0000"
    elif mutation == "unsafe-package-url":
        state["packageVersion"] = "https://other.invalid/srcpkg/libxml2/2.13.9-r2"
    else:
        other = copy.deepcopy(item)
        other["state"][-1]["fixed"] = not other["state"][-1]["fixed"]
        doc["items"].append(other)
    with pytest.raises(parser.InputError):
        parser.decode_tracker(doc, "3.24-main")


def test_matching_real_installed_inventory_keeps19_potential_records_and4_unresolved_owned_packages(
    parser,
):
    trackers = [
        parser.decode_tracker(
            recorded(f"tracker-{part}-20261109.json.gz"), "3.24-" + part
        )
        for part in ["main", "community"]
    ]
    capture = recorded("owned-runtime-base-amd64-inventory.json.gz")
    result = parser.match_installed(trackers, capture, "x86_64")
    assert len(result["unfixed_matches"]) == 19
    assert {row["source"] for row in result["unfixed_matches"]} == {
        "cairo",
        "coreutils",
        "glib",
        "tar",
        "sqlite",
    }
    assert sum(row["severity"] == "UNKNOWN" for row in result["unfixed_matches"]) == 18
    assert {row["name"] for row in result["unresolved_packages"]} == {
        "busybox",
        "busybox-binsh",
        "ecm-owned-native",
        "ssl_client",
    }
    assert result["status"] == "BLOCKED_PUBLISHED_UNFIXED_OR_UNRESOLVED_SUBJECTS"
    assert result["full_runtime_advisory_acceptance"] is False


def test_query_database_mismatch_is_not_an_advisory_exemption(parser):
    capture = recorded("owned-runtime-base-amd64-inventory.json.gz")
    capture["packages"][0]["version"] = "changed"
    main = one_cve()
    community = one_cve()
    community["id"] = "https://security.alpinelinux.org/branch/3.24-community"
    trackers = [
        parser.decode_tracker(main, "3.24-main"),
        parser.decode_tracker(community, "3.24-community"),
    ]
    with pytest.raises(parser.InputError):
        parser.match_installed(trackers, capture, "x86_64")


def test_missing_tracker_repository_cannot_yield_a_clean_diagnostic(parser):
    capture = recorded("owned-runtime-base-amd64-inventory.json.gz")
    with pytest.raises(parser.InputError):
        parser.match_installed([], capture, "x86_64")
