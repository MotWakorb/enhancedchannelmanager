#!/usr/bin/env python3
"""Join final installed wheel bytes/notices and the bounded GNU source subset.

No target ELF, delivered source, compiler or comparison tool is executed. The
reviewed map binds prior exact repair/reproduction evidence; this does not rerun
those builds or establish source closure for other bundled wheel components.
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path, PurePosixPath

from inventory_musllinux_wheels import (
    INVENTORY_SHA256,
    verify_installed,
    verify_payloads,
)
from verify_musllinux_gnu import (
    GNU_MEMBER,
    MANIFEST_SHA256,
    AttributionError,
    load_manifest,
    require,
    require_source_closure,
    sha256,
)

INDEX = "usr/share/doc/ecm/python-third-party/artifact-index"
SOURCES = "usr/share/ecm/sources/wheel-gnu"
SITE = "opt/venv/lib/python3.12/site-packages"


def gnu_members(inventory: dict, manifest: dict, architecture: str) -> list[dict]:
    """Join recorded maps only; caller must pin both manifests and live bytes."""
    require(
        architecture in INVENTORY_SHA256
        and inventory["architecture"] == architecture
        and inventory["selection_manifest_sha256"] == MANIFEST_SHA256,
        "GNU wheel architecture/selection differs",
    )
    rows = inventory["distributions"]
    require(
        len(rows) == inventory["distribution_count"] == 74
        and len({row["name"] for row in rows}) == 74
        and len({row["filename"] for row in rows}) == 74,
        "complete native wheel selection required",
    )
    selected = {row["filename"]: row["sha256"] for row in rows}
    require(
        all(
            manifest["wheel_selection"].get(name) == digest
            for name, digest in selected.items()
        ),
        "GNU whole-wheel selection binding differs",
    )
    actual = {}
    for row in rows:
        for item in row["native_files"]:
            if GNU_MEMBER.fullmatch(PurePosixPath(item["path"]).name):
                require(item["path"] not in actual, "duplicate installed GNU member")
                actual[item["path"]] = item["sha256"]
    expected, result = {}, []
    for obj in manifest["objects"]:
        for member in obj["members"]:
            if member["wheel"] not in selected:
                continue
            require(
                selected[member["wheel"]] == member["wheel_sha256"]
                and member["path"] not in expected,
                "GNU member whole-wheel binding differs",
            )
            expected[member["path"]] = obj["expected_sha256"]
            result.append(
                {
                    "path": member["path"],
                    "sha256": obj["expected_sha256"],
                    "wheel": member["wheel"],
                    "wheel_sha256": member["wheel_sha256"],
                    "source_family": obj["source_closure"],
                    "source_status": manifest["source_closures"][obj["source_closure"]][
                        "status"
                    ],
                    "gcc_version": obj["gcc_version"],
                }
            )
    require(
        actual == expected and bool(expected),
        "complete installed GNU member map differs",
    )
    return sorted(result, key=lambda row: row["path"])


def regular(path: Path, maximum: int = 512 * 1024**2) -> None:
    require(
        path.absolute() == path.resolve()
        and path.is_file()
        and not path.is_symlink()
        and 0 < path.stat().st_size <= maximum,
        "canonical bounded wheel/source file required",
    )


def verify(root: Path, architecture: str) -> dict:
    require(architecture in INVENTORY_SHA256, "reviewed wheel architecture required")
    index = root / INDEX
    manifest_path = index / "musllinux-gnu.json"
    regular(manifest_path, 32 * 1024**2)
    manifest = load_manifest(manifest_path)
    inventory_path = index / f"musllinux/linux-{architecture}.json"
    regular(inventory_path, 32 * 1024**2)
    require(
        sha256(inventory_path) == INVENTORY_SHA256[architecture],
        "reviewed installed wheel inventory differs",
    )
    inventory = json.loads(inventory_path.read_text())
    regular(root / "app/requirements.txt", 1024**2)
    require(
        sha256(root / "app/requirements.txt") == inventory["requirements_sha256"],
        "final installed application lock differs",
    )
    members = gnu_members(inventory, manifest, architecture)
    source_root = root / SOURCES
    require(
        source_root.is_dir() and source_root.absolute() == source_root.resolve(),
        "canonical delivered GNU sources required",
    )
    require_source_closure(manifest, source_root)
    source_files, source_directories = set(), set()
    for family, closure in manifest["source_closures"].items():
        for row in closure["source_files"]:
            name = family + "/" + row["path"]
            path = source_root / name
            regular(path)
            require(
                path.stat().st_size == row["bytes"]
                and path.stat().st_mode & 0o7777 == 0o644,
                "delivered GNU source size/mode differs",
            )
            source_files.add(name)
            source_directories.update(
                str(parent)
                for parent in PurePosixPath(name).parents
                if str(parent) != "."
            )
    require(
        {path.relative_to(source_root).as_posix() for path in source_root.rglob("*")}
        == source_files | source_directories,
        "closed GNU source inventory layout differs",
    )
    site = root / SITE
    require(
        site.is_dir() and site.absolute() == site.resolve(),
        "canonical installed wheel directory required",
    )
    for row in inventory["distributions"]:
        verify_installed(row, site, "/opt/venv/bin/python")
        verify_payloads(
            row,
            index / "musllinux/payloads",
            root / "usr/share/doc/ecm/python-third-party",
        )
    actual_native, actual_gnu = set(), {}
    for path in site.rglob("*"):
        if path.is_file():
            require(not path.is_symlink(), "installed wheel file aliases forbidden")
            with path.open("rb") as stream:
                header = stream.read(4)
            if header == b"\x7fELF":
                actual_native.add(path.relative_to(site).as_posix())
            if GNU_MEMBER.fullmatch(path.name):
                require(header == b"\x7fELF", "installed GNU member is not ELF")
                actual_gnu[path.relative_to(site).as_posix()] = sha256(path)
        elif path.is_symlink():
            raise AttributionError("installed wheel directory aliases forbidden")
    expected_native = {
        item["path"]
        for row in inventory["distributions"]
        for item in row["native_files"]
    }
    require(
        actual_native == expected_native
        and len(actual_native) == inventory["native_ELF_count"],
        "complete final installed native ELF coverage differs",
    )
    require(
        actual_gnu == {row["path"]: row["sha256"] for row in members},
        "complete final installed GNU ELF coverage differs",
    )
    return {
        "status": "FINAL_INSTALLED_WHEEL_BYTES_NOTICES_AND_GNU_SOURCE_SUBSET_ONLY",
        "architecture": architecture,
        "wheel_inventory_sha256": INVENTORY_SHA256[architecture],
        "gnu_manifest_sha256": MANIFEST_SHA256,
        "installed_distributions_verified": len(inventory["distributions"]),
        "installed_native_ELFs_verified": len(actual_native),
        "gnu_member_copies": members,
        "gnu_source_files_verified": len(source_files),
        "gnu_source_statuses": {
            family: row["status"] for family, row in manifest["source_closures"].items()
        },
        "full_runtime_source_acceptance": False,
        "new_source_reproduction_performed": False,
        "scope": "Final live files of all 74 reviewed wheel payloads, pip's approved RECORD/shebang/headers relocation semantics, complete native/GNU ELF sets and original notices/raw SBOM payloads. All 27 reviewed GNU source/recipe/notice/proof files rehashed in the actual image. GNU correspondence uses the unchanged sealed prior complete-ELF repair/reproduction map; no repair tool, target binary or source is executed here. No claims of new source reproduction, original removed compiler checkout identity, other bundled wheel component/source closure, independent advisories, frontend/MCP or aggregate acceptance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    args = parser.parse_args()
    try:
        require(
            platform.machine() == {"amd64": "x86_64", "arm64": "aarch64"}[args.arch],
            "native installed wheel verifier architecture differs",
        )
        result = verify(Path("/"), args.arch)
    except (AttributionError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, "final wheel source verification failed: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
