"""Actual APK inventory bytes, not package-name heuristics, define source scope."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend/tests/fixtures/alpine"


@pytest.fixture
def inventory():
    spec = importlib.util.spec_from_file_location(
        "alpine_source_inventory", ROOT / "scripts/alpine_source_inventory.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def capture():
    return {
        "installed_database": (FIXTURES / "source-inventory-installed.txt").read_text(),
        "packages": json.loads((FIXTURES / "source-inventory-query.json").read_text()),
    }


def test_real_runtime_capture_joins_all_subpackages_without_dropping_virtual_dependencies(
    inventory, capture
):
    result = inventory.source_plan(capture, "x86_64")
    assert result["status"] == "UNAUTHENTICATED_APK_SOURCE_PLAN_ONLY"
    assert len(result["packages"]) == 5
    assert [r["origin"] for r in result["source_groups"]] == [
        "musl",
        "skalibs",
        "utmps",
    ]
    musl = result["source_groups"][0]
    assert musl["commit"] == "8f79d39391b46d6dbf1800fb68b8212a21d5ee2e"
    assert musl["packages"] == ["musl", "musl-utils"]
    assert result["virtual_packages"][0]["name"] == ".python-rundeps"
    assert "so:libssl.so.3" in result["virtual_packages"][0]["dependencies"].split()
    assert not result["unresolved_packages"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "duplicate",
        "version",
        "origin",
        "license",
        "arch",
        "size",
        "description",
        "url",
    ],
)
def test_query_and_actual_database_must_describe_the_identical_complete_set(
    inventory, capture, change
):
    if change == "missing":
        capture["packages"].pop()
    elif change == "extra":
        capture["packages"].append(
            {**capture["packages"][1], "name": "untracked-library"}
        )
    elif change == "duplicate":
        capture["packages"].append(capture["packages"][1])
    else:
        field = {"size": "file-size"}.get(change, change)
        capture["packages"][1][field] = 1 if change == "size" else "unexpected"
    with pytest.raises(inventory.InventoryError, match="query"):
        inventory.source_plan(capture, "x86_64")


@pytest.mark.parametrize(
    "before,after",
    [
        ("P:musl\n", "P:musl\nP:other\n"),
        ("A:x86_64", "A:aarch64"),
        ("c:8f79d39391b46d6dbf1800fb68b8212a21d5ee2e", "c:../unsafe"),
        ("C:Q1dw6DBNlY3O2Y12mX356Podtcoy8=", "C:Q1invalid"),
        ("F:lib", "F:../operator"),
        ("R:ld-musl-x86_64.so.1", "R:../../operator"),
        ("R:ld-musl-x86_64.so.1", "R:ld-musl-x86_64.so.1\nP:late-metadata"),
    ],
)
def test_ambiguous_or_unsafe_database_metadata_is_denied(
    inventory, capture, before, after
):
    capture["installed_database"] = capture["installed_database"].replace(
        before, after, 1
    )
    with pytest.raises(inventory.InventoryError):
        inventory.source_plan(capture, "x86_64")


def test_duplicate_installed_package_is_denied_even_if_query_is_unchanged(
    inventory, capture
):
    capture["installed_database"] += (
        "\n" + capture["installed_database"].split("\n\n")[1] + "\n\n"
    )
    with pytest.raises(inventory.InventoryError, match="duplicate installed"):
        inventory.source_plan(capture, "x86_64")


@pytest.mark.parametrize(
    "addition",
    [
        "F:usr\n",
        "F:usr\nR:payload\nZ:Q1dw6DBNlY3O2Y12mX356Podtcoy8=\n",
        "o:unknown\n",
        "c:8f79d39391b46d6dbf1800fb68b8212a21d5ee2e\n",
        "S:10\n",
    ],
)
def test_virtual_exemption_is_not_a_name_based_source_bypass(
    inventory, capture, addition
):
    # Add actual payload, fake origin or conflicting size to the real zero-payload row.
    head, tail = capture["installed_database"].split("\n\n", 1)
    capture["installed_database"] = head + "\n" + addition + "\n" + tail
    with pytest.raises(inventory.InventoryError):
        inventory.source_plan(capture, "x86_64")


def test_missing_origin_commit_is_unresolved_not_silently_excluded(inventory, capture):
    capture["installed_database"] = capture["installed_database"].replace(
        "c:8f79d39391b46d6dbf1800fb68b8212a21d5ee2e\n", "", 1
    )
    result = inventory.source_plan(capture, "x86_64")
    assert [r["name"] for r in result["unresolved_packages"]] == ["musl"]
    assert sum(len(r["packages"]) for r in result["source_groups"]) == 3


def test_changed_compressed_control_checksum_changes_source_subject(inventory, capture):
    first = inventory.source_plan(capture, "x86_64")
    capture["installed_database"] = capture["installed_database"].replace(
        "Q1dw6DBNlY3O2Y12mX356Podtcoy8=", "Q1UTctqvsjRkaWDspNehP3BjJPJNY=", 1
    )
    second = inventory.source_plan(capture, "x86_64")
    assert second["package_set_sha256"] != first["package_set_sha256"]


def test_query_size_boolean_cannot_equal_an_integer_package_size(inventory, capture):
    capture["installed_database"] = capture["installed_database"].replace(
        "S:415466", "S:1", 1
    )
    capture["packages"][1]["file-size"] = True
    with pytest.raises(inventory.InventoryError, match="query"):
        inventory.source_plan(capture, "x86_64")
