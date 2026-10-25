"""The coordinated Vitest 4 patch family excludes the two mocker advisories."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_vitest_and_coverage_resolve_one_fixed_patch_family():
    manifest = json.loads((ROOT / "frontend/package.json").read_text())
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    versions = [
        manifest["devDependencies"][name] for name in ("vitest", "@vitest/coverage-v8")
    ]
    assert versions[0] == versions[1]
    assert re.fullmatch(r"4\.\d+\.\d+", versions[0]), (
        "stay on the reviewed major with exact pins"
    )
    assert tuple(map(int, versions[0].split("."))) >= (4, 1, 12)
    packages = lock["packages"]
    assert packages["node_modules/vitest"]["version"] == versions[0]
    assert packages["node_modules/@vitest/mocker"]["version"] == versions[0]
    family = {
        name: record["version"]
        for name, record in packages.items()
        if name.startswith("node_modules/@vitest/") or "/node_modules/@vitest/" in name
    }
    assert family
    assert set(family.values()) == {versions[0]}, family
