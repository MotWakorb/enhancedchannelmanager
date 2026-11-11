"""Tests of the disposable witness instrument, not Cairo/tar CVE clearance."""

import copy
import ctypes
import gzip
import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _capsule():
    return json.loads(
        gzip.decompress(
            (
                ROOT
                / "docs/design/evidence/cairo_tar_remediation_20261111.receipts.json.gz"
            ).read_bytes()
        )
    )


@pytest.fixture
def probe(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cairo_tar_contract_probe_20261111",
        ROOT / "scripts/proofs/cairo_tar_contract_probe_20261111.py",
    )
    module = importlib.util.module_from_spec(spec)

    def forbidden(*args, **kwargs):
        raise AssertionError("import must not execute native inputs")

    with monkeypatch.context() as context:
        context.setattr(subprocess, "run", forbidden)
        context.setattr(ctypes, "CDLL", forbidden)
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("write_member", [False, True])
def test_controlled_archive_has_only_relative_scratch_names(probe, write_member):
    with tarfile.open(
        fileobj=io.BytesIO(probe.tar_fixture_bytes(write_member))
    ) as archive:
        members = archive.getmembers()
        assert len(members) == (2 if write_member else 1)
        assert members[0].name == "alias" and members[0].islnk()
        assert members[0].linkname == "sentinel"
        if write_member:
            assert members[1].isfile() and members[1].name == "alias"
            assert archive.extractfile(members[1]).read() == b"changed\n"


def test_boundary_requires_all_observed_properties_not_just_exit_zero(probe):
    confined = {
        "compatibility_ok": True,
        "boundaries": [
            {"sentinel_unchanged": True, "outside_hardlink": False},
            {"sentinel_unchanged": True, "outside_hardlink": False},
        ],
    }
    assert probe.tar_confinement_satisfied(confined)
    for field in ("sentinel_unchanged", "outside_hardlink"):
        escaped = copy.deepcopy(confined)
        escaped["boundaries"][0][field] = field == "outside_hardlink"
        assert not probe.tar_confinement_satisfied(escaped)
    confined["compatibility_ok"] = False
    assert not probe.tar_confinement_satisfied(confined)


@pytest.mark.parametrize("boundaries", [[], [{}], [{"sentinel_unchanged": True}]])
def test_missing_or_empty_observations_do_not_pass(probe, boundaries):
    assert not probe.tar_confinement_satisfied(
        {"compatibility_ok": True, "boundaries": boundaries}
    )


def test_import_does_not_load_native_libraries_or_execute_tar(probe):
    # The fixture traps native calls before exec_module, not after import.
    assert probe.tar_fixture_bytes(False)
    assert not probe.tar_confinement_satisfied({})


def test_recorded_native_boundary_failure_is_not_a_false_pass(probe):
    report = json.loads(
        (
            ROOT / "docs/design/evidence/cairo_tar_native_contract_20261111.json"
        ).read_text()
    )
    assert report["native"]["tar"]["compatibility_ok"] is True
    assert report["native"]["tar"]["boundaries"][0]["outside_hardlink"] is True
    assert report["native"]["tar"]["boundaries"][1]["sentinel_unchanged"] is False
    assert not probe.tar_confinement_satisfied(report["native"]["tar"])
    assert report["native"]["poppler_input_trigger"] == "NOT_TESTED"


def test_repeated_extraction_fixture_is_explicitly_scratch_relative(probe):
    first, second = probe.tar_repeated_fixture_bytes()
    with tarfile.open(fileobj=io.BytesIO(first)) as archive:
        member = archive.getmembers()[0]
        assert member.issym() and member.name == "escape"
        # This points only at a sibling created inside the disposable /tmp root.
        assert member.linkname == "../victim"
    with tarfile.open(fileobj=io.BytesIO(second)) as archive:
        member = archive.getmembers()[0]
        assert member.isfile() and member.name == "escape/sentinel"
        assert archive.extractfile(member).read() == b"changed\n"


def test_recorded_primary_mrs_are_not_a_matching_font_correction():
    records = {row["receipt"]["name"]: row for row in _capsule()["http"]}
    for number in (621, 632):
        response = records[f"cairo-mr{number}.json"]
        assert response["receipt"]["status"] == 200
        mr = json.loads(response["original_utf8"])
        assert mr["state"] == "opened" and mr["merged_at"] is None
        patch = records[f"cairo-mr{number}.patch"]["original_utf8"]
        assert "cairo-bentley-ottmann-rectangular.c" in patch
        assert "cairo-ft-font.c" not in patch


def test_source_prototype_retains_original_complete_patch_bytes_and_tests():
    capsule = _capsule()
    records = {row["receipt"]["name"]: row for row in capsule["http"]}
    local = {row["name"]: row for row in capsule["local"]}
    prototype = json.loads(local["bounded-source-prototype.json"]["original_utf8"])
    assert prototype["runtime_remediated"] is False
    assert len(prototype["ordered_patches"]) == 12
    for patch in prototype["ordered_patches"]:
        record = records[patch["file"]]
        body = record["original_utf8"].encode()
        assert hashlib.sha256(body).hexdigest() == patch["sha256"]
        assert record["receipt"]["status"] == 200
        assert len(body) == patch["bytes"]
    replay = json.loads(local["source-probe-bounded.json"]["original_utf8"])
    assert [row["patch"] for row in replay] == [
        row["file"] for row in prototype["ordered_patches"]
    ]
    assert all(row["returncode"] == 0 for row in replay)
    changed = {row["path"] for row in prototype["changed_regular_files"]}
    assert {"tests/Makefile.am", "tests/testsuite.at"} <= changed
    assert {"tests/rename08.at", "tests/rename09.at", "gnu/openat2.c"} <= changed
