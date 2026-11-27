"""Offline exact delivered Rust inputs, not aggregate runtime source clearance.

Authenticate the complete external APK/source graph first. Derive project locks
from original source/patch bytes, check all crate members, both original compiler
sources and complete distro recipe/input graphs. No source, recipe, compiler,
build script or target executable is run. Known missing ELF metadata is reported
as a gap, never used to grant full runtime source/attribution/advisory acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from alpine_rust_auditable import decode_elf, join_lock
from alpine_rust_source_inputs import (
    COMPILERS,
    LOCKS,
    compiler_recipe,
    compiler_source,
    derive_lock,
    verify_archives,
)
from alpine_source_inputs import (
    InputError,
    bind_git_recipe,
    recipe_tree,
    require,
    verify_source_inputs,
)
from verify_alpine_external_sources import (
    path_in,
    read_json,
    sha256,
)
from verify_alpine_external_sources import verify as verify_external
from verify_musllinux_gnu import apk_payload, archive_member

__all__ = [
    "InputError",
    "exact_layout",
    "live_elf",
    "project_locks",
    "release_evidence",
    "verify",
]
RELEASE_KEY = "108F66205EAEB0AAA8DD5E1C85AB96E6FA1BE5FE"
PUBLIC_KEY_SHA256 = "e54b09a439647e006b4831eec9785cbaaf3e07ab371c3a6ee6a68e1bdb9fbc6b"
SIGNATURES = {
    "1.95.0": "eb4168fdababef9ad8150d8e3ea5ed94e39b1f2537c4b7db584df451f67eed96",
    "1.96.0": "e5e87862304767e049d04785b3fdcf6879e06a60f2b61e8c80135350d29f9b9e",
}
ELFS = {
    "glycin-image-rs": "usr/libexec/glycin-loaders/2+/glycin-image-rs",
    "glycin-svg": "usr/libexec/glycin-loaders/2+/glycin-svg",
    "libglycin": "usr/lib/libglycin-2.so.0",
    "rav1e-libs": "usr/lib/librav1e.so.0.8.1",
}


def live_elf(root: Path, name: str, original: bytes) -> None:
    path = path_in(root, name)
    require(
        sha256(path) == hashlib.sha256(original).hexdigest(),
        "live Rust ELF differs from authenticated APK payload",
    )


def exact_layout(root: Path, used: set[str]) -> None:
    require(
        root.is_dir() and not root.is_symlink() and bool(used),
        "regular nonempty Rust source delivery required",
    )
    directories = {
        str(parent)
        for name in used
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    }
    files = set()
    actual_directories = set()
    for path in root.rglob("*"):
        require(
            not path.is_symlink() and (path.is_file() or path.is_dir()),
            "unsafe Rust source-delivery entry",
        )
        name = path.relative_to(root).as_posix()
        if path.is_file():
            files.add(name)
        else:
            actual_directories.add(name)
    require(
        files == used and actual_directories == directories,
        "missing or unindexed Rust source-delivery file/directory",
    )


def release_evidence(root: Path, used: set[str]) -> dict:
    # Runtime enforcement binds reviewed original public bytes/archive hashes.
    # Independent GPG verification belongs to the isolated build-only producer.
    require(
        sha256(path_in(root, "rust-key.gpg.ascii")) == PUBLIC_KEY_SHA256,
        "original Rust release public key differs",
    )
    used.add("rust-key.gpg.ascii")
    for version, digest in SIGNATURES.items():
        name = f"compilers/{version}/rustc-{version}-src.tar.xz.asc"
        require(
            sha256(path_in(root, name)) == digest,
            "original Rust release signature differs",
        )
        used.add(name)
    return {"fingerprint": RELEASE_KEY, "runtime_gpg_executed": False}


def project_locks(
    external_root: Path, index: dict
) -> tuple[dict[str, bytes], list[dict]]:
    """Caller must have passed verify_external on this same complete root/index."""
    result = {}
    records = []
    for group in index["source_groups"]:
        origin = group["origin"]
        if origin not in LOCKS:
            continue
        require(
            origin not in result, "unique complete Rust origin source group required"
        )
        commit = next(
            path_in(external_root, r["path"]).read_bytes()
            for r in group["git_objects"]
            if r["kind"] == "commit"
        )
        trees = {
            r["git_id"]: path_in(external_root, r["path"]).read_bytes()
            for r in group["git_objects"]
            if r["kind"] == "tree"
        }
        tree_id = bind_git_recipe(
            group["commit"], commit, trees, group["git_path"], origin=origin
        )
        tree = recipe_tree(
            path_in(external_root, group["recipe"]["path"]),
            tree_id,
            group["recipe"]["apkbuild_sha256"],
        )
        sources = {
            name: path_in(external_root, row["path"])
            for name, row in group["sources"].items()
        }
        binding = verify_source_inputs(tree, sources)
        require(
            len(sources) == 1, "reviewed original Rust project source archive required"
        )
        source = next(iter(sources.values()))
        prefix = "rav1e-0.8.1" if origin == "rav1e" else "glycin-2.1.5"
        original = archive_member(source, prefix + "/Cargo.lock")
        patch = (
            None if origin == "rav1e" else tree["entries"]["bump-nix.patch"]["bytes"]
        )
        result[origin] = derive_lock(origin, original, patch)
        records.append(
            {
                "origin": origin,
                "source_commit": group["commit"],
                "recipe_tree": binding["git_tree"],
                "source_archive_sha256": sha256(source),
                "source_lock_sha256": hashlib.sha256(original).hexdigest(),
                "patched_lock_sha256": LOCKS[origin],
            }
        )
    require(
        set(result) == set(LOCKS), "all three exact Rust project source groups required"
    )
    return result, records


def verify(
    root: Path, external_root: Path, capture: dict, installed_root: Path | None = None
) -> dict:
    require(
        root.is_dir() and not root.is_symlink(),
        "regular Rust source delivery root required",
    )
    external = verify_external(external_root, capture)
    index = read_json(path_in(external_root, "index.json"))
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[index["architecture"]]
    used = set()
    evidence = release_evidence(root, used)
    locks, project_records = project_locks(external_root, index)
    lock_paths = {}
    for origin, data in locks.items():
        name = "locks/" + origin + "-Cargo.lock"
        path = path_in(root, name)
        require(
            path.read_bytes() == data,
            "delivered lock differs from authenticated source/patch replay",
        )
        lock_paths[origin] = path
        used.add(name)
    registry = verify_archives(lock_paths, root / "crates")
    used.update(
        "crates/" + row["name"] + "-" + row["version"] + ".crate"
        for row in registry["crates"]
    )
    compilers = {}
    for version, expected in COMPILERS.items():
        prefix = "compilers/" + version + "/"
        commit_name = prefix + "objects/commit-" + expected["aports_commit"]
        commit = path_in(root, commit_name).read_bytes()
        used.add(commit_name)
        directory = root / prefix / "objects"
        trees = {}
        for path in directory.glob("tree-*"):
            name = path.relative_to(root).as_posix()
            trees[path.name.removeprefix("tree-")] = path_in(root, name).read_bytes()
            used.add(name)
        require(
            len(trees) == 2, "exact original Rust recipe commit path tree set required"
        )
        recipe_name = prefix + "recipe.tar"
        tree = compiler_recipe(path_in(root, recipe_name), version, commit, trees)
        used.add(recipe_name)
        archive_name = f"rustc-{version}-src.tar.xz"
        source_name = prefix + archive_name
        source = path_in(root, source_name)
        upstream = compiler_source(source, version)
        inputs = verify_source_inputs(tree, {archive_name: source})
        used.add(source_name)
        compilers[version] = {
            "source": upstream,
            "distro_recipe_inputs": inputs,
            "historical_build_attestation_established": False,
        }
    selected = []
    for record in index["packages"]:
        metadata = record["index_record"]
        name = metadata["P"]
        if name not in ELFS:
            continue
        # verify_external already authenticated this APK against this capture.
        payload = apk_payload(
            path_in(external_root, record["path"]).read_bytes(), metadata
        )
        data = archive_member(payload, ELFS[name])
        digest = hashlib.sha256(data).hexdigest()
        row = {"package": name, "path": ELFS[name], "elf_sha256": digest}
        if installed_root is not None:
            live_elf(installed_root, ELFS[name], data)
            row["live_installed_bytes_verified"] = True
        try:
            decoded = decode_elf(data, digest, architecture)
        except InputError as exc:
            require(
                name in {"glycin-image-rs", "glycin-svg", "libglycin"}
                and str(exc)
                == "unique ELF .dep-v0 required; missing metadata is not source clearance",
                "invalid or unexpectedly missing Rust ELF metadata: " + str(exc),
            )
            row.update(
                status="MISSING_AUDITABLE_METADATA_NOT_SOURCE_CLEARANCE", error=str(exc)
            )
        else:
            joined = join_lock(decoded, registry["lock_subjects"], metadata["o"])
            row.update(
                status=joined["status"],
                registry_packages=joined["registry_packages"],
                local_packages=joined["local_packages"],
                section_sha256=joined["section_sha256"],
            )
        selected.append(row)
    require(
        {row["package"] for row in selected} == set(ELFS)
        and len(selected) == len(ELFS),
        "complete selected Rust runtime ELF subject set required",
    )
    exact_layout(root, used)
    inventory_hash = hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "status": "DELIVERED_FROZEN_RUST_SOURCE_INPUTS_AND_BOUNDED_ELF_JOINS_ONLY",
        "architecture": architecture,
        "external_package_set_sha256": external["package_set_sha256"],
        "delivered_files": len(used),
        "projects": project_records,
        "registry_archives": registry["registry_archives"],
        "registry_source_files": registry["source_files"],
        "registry_source_bytes": registry["source_bytes"],
        "registry_inventory_sha256": inventory_hash,
        "compilers": compilers,
        "release_public_evidence": evidence,
        "selected_elfs": selected,
        "full_runtime_source_acceptance": False,
        "exact_compiled_features_established": False,
        "scope": "Delivered original frozen registry archives/notices, original compiler/standard-library archives and complete pinned distro recipe/source input/public evidence graphs, rederived against authenticated external runtime APK/source subjects. Optional live selected ELF byte checks do not clear missing auditable metadata, historical compiler build provenance, complete linkage/attribution/advisory or aggregate source/runtime/MCP obligations. Generated receipts and unsigned build logs are not authentication inputs.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--external-root", type=Path, required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--installed-root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(
                args.source_root,
                args.external_root,
                read_json(args.runtime_inventory),
                args.installed_root,
            ),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
