"""Pin the two stale AMD64 builders to authenticated 2026-10-25 vendor records.

The fixture records real signed-RPM queries and descriptors from signed SUSE
primary metadata. Live signature, exact SRPM header, and Source/Patch payload
verification used collect_native_sources.py; offline tests do not repeat it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = (
    ROOT / "backend/tests/fixtures/native_builder_refresh_20261025/vendor-records.json"
)
RECORDS = json.loads(FIXTURE.read_text())["packages"]
OLD_VERSIONS = {"libmp3lame-devel": "3.100-5.16", "python312-base": "3.12.15-1.1"}


@pytest.fixture
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_native_runtime_refresh", ROOT / "scripts/build_native_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _descriptor(builder, name):
    recipe = builder.load_recipe(ROOT / "docker/native/manifest.json", "amd64")
    return next(
        p for p in recipe["architectures"]["amd64"]["builder_rpms"] if p["name"] == name
    )


def _recorded_install(builder, monkeypatch, tmp_path, record, descriptor):
    """Exercise production exact-inventory verification without a package install."""
    name, version, release, arch, vendor, source, license_ = (
        record["rpm_query_tsv"].strip().split("\t")
    )
    assert vendor == "openSUSE"
    inventory = (
        "\t".join([name, record["epoch"], version, release, arch, source, license_])
        + "\n"
    )
    filesystem = MagicMock()
    filesystem.read_text.return_value = 'ID="opensuse-tumbleweed"\n'
    filesystem.glob.return_value = []
    monkeypatch.setattr(builder, "Path", lambda _: filesystem)
    run = MagicMock(
        side_effect=lambda command, **_: (
            inventory if command[0] == "/usr/bin/rpm" else ""
        )
    )
    monkeypatch.setattr(builder, "run", run)
    builder._install_builder(
        {
            "official_repository": "https://download.opensuse.org/tumbleweed/repo/oss/",
            "builder_rpms": [descriptor],
        },
        tmp_path,
        {},
    )
    install = next(
        call.args[0] for call in run.call_args_list if "install" in call.args[0]
    )
    assert install[-1] == f"{name}.{arch}={version}-{release}"


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["descriptor"]["name"])
def test_manifest_matches_recorded_official_descriptor(builder, record):
    expected = record["descriptor"]
    assert _descriptor(builder, expected["name"]) == expected


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["descriptor"]["name"])
def test_current_signed_header_satisfies_builder_inventory_check(
    builder, monkeypatch, tmp_path, record
):
    descriptor = _descriptor(builder, record["descriptor"]["name"])
    _recorded_install(builder, monkeypatch, tmp_path, record, descriptor)


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["descriptor"]["name"])
def test_obsolete_pin_is_rejected_against_current_signed_header(
    builder, monkeypatch, tmp_path, record
):
    descriptor = dict(record["descriptor"])
    descriptor["version"] = OLD_VERSIONS[descriptor["name"]]
    with pytest.raises(
        builder.BuildError, match="installed builder RPM identity mismatch"
    ):
        _recorded_install(builder, monkeypatch, tmp_path, record, descriptor)
