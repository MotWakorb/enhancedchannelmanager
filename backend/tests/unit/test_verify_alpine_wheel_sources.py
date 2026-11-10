"""Recorded wheel/GNU maps; final live-byte proof is separate actual integration."""

import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
INDEX = ROOT / "docker/python-third-party/artifact-index"


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_wheel_sources", ROOT / "scripts/verify_alpine_wheel_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["amd64", "arm64"])
def maps(request):
    arch = request.param
    return (
        arch,
        json.loads((INDEX / f"musllinux/linux-{arch}.json").read_text()),
        json.loads((INDEX / "musllinux-gnu.json").read_text()),
    )


def test_recorded_native_maps_join_all9_gnu_copies_to6_objects_and2_source_families(
    consumer, maps
):
    arch, inventory, manifest = maps
    result = consumer.gnu_members(inventory, manifest, arch)
    assert len(result) == 9 and len({row["sha256"] for row in result}) == 6
    assert {row["source_family"] for row in result} == set(manifest["source_closures"])
    assert {row["gcc_version"] for row in result} == {"12.4.0", "14.2.0"}
    assert all(
        row["source_status"]
        in {"AUTHENTICATED_PROVIDER_SOURCE", "REPRODUCED_CORRESPONDING_SOURCE"}
        for row in result
    )
    assert len(inventory["distributions"]) == 74


@pytest.mark.parametrize(
    "mutation",
    ["extra-gnu", "missing-gnu", "changed-gnu", "wheel-rebinding", "wrong-arch"],
)
def test_gnu_join_is_complete_not_filename_or_comment_matching(
    consumer, maps, mutation
):
    arch, inventory, manifest = maps
    dist = next(
        row
        for row in inventory["distributions"]
        if any("libgcc_s-" in item["path"] for item in row["native_files"])
    )
    member = next(row for row in dist["native_files"] if "libgcc_s-" in row["path"])
    if mutation == "extra-gnu":
        extra = copy.deepcopy(member)
        extra["path"] = "unknown.libs/libatomic-unknown.so.1"
        dist["native_files"].append(extra)
    elif mutation == "missing-gnu":
        dist["native_files"].remove(member)
    elif mutation == "changed-gnu":
        member["sha256"] = "0" * 64
    elif mutation == "wheel-rebinding":
        dist["sha256"] = "0" * 64
    else:
        inventory["architecture"] = "arm64" if arch == "amd64" else "amd64"
    with pytest.raises(consumer.AttributionError):
        consumer.gnu_members(inventory, manifest, arch)


@pytest.mark.parametrize(
    "mutation",
    ["missing-sources", "changed-inventory", "changed-lock", "inventory-alias"],
)
def test_final_image_gate_cannot_rely_on_builder_receipts_or_unbound_source_trees(
    consumer, maps, tmp_path, mutation
):
    arch, _inventory, _manifest = maps
    target = tmp_path / consumer.INDEX
    (target / "musllinux").mkdir(parents=True)
    for name in ["musllinux-gnu.json", f"musllinux/linux-{arch}.json"]:
        shutil.copyfile(INDEX / name, target / name)
    (tmp_path / "app").mkdir()
    shutil.copyfile(
        ROOT / "backend/requirements.txt", tmp_path / "app/requirements.txt"
    )
    inventory = target / f"musllinux/linux-{arch}.json"
    if mutation == "changed-inventory":
        inventory.write_bytes(inventory.read_bytes() + b"\n")
    elif mutation == "changed-lock":
        (tmp_path / "app/requirements.txt").write_bytes(b"changed")
    elif mutation == "inventory-alias":
        inventory.rename(tmp_path / "outside")
        inventory.symlink_to(tmp_path / "outside")
    with pytest.raises(consumer.AttributionError):
        consumer.verify(tmp_path, arch)
