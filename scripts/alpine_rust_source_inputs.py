"""Exact nested registry inputs for three frozen external Alpine Rust origins.

The original archives/patches and signed source-commit joins live in the separate
external APK consumer. These helpers verify the reviewed resulting Cargo locks
and every original crate member without extraction or execution. They grant no
official binary-linkage, compiler-source, complete license or runtime acceptance.
"""

from __future__ import annotations

import hashlib
import re
import tarfile
import tomllib
from collections import Counter
from pathlib import Path, PurePosixPath

from alpine_source_inputs import InputError, require
from verify_alpine_external_sources import sha256

__all__ = [
    "InputError",
    "compiler_source",
    "inspect_crate",
    "locked_crates",
    "verify_archives",
]
LOCKS = {
    "glycin": "1400935c4a3517d464aa99778eb49c39dae71e604743abec0212c3a3b6b30793",
    "libglycin": "1400935c4a3517d464aa99778eb49c39dae71e604743abec0212c3a3b6b30793",
    "rav1e": "861482385bdef579e207bb371937dd49cb9ff47d9406c40660b760fa672d0589",
}
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
COMPILERS = {
    "1.96.0": {
        "commit": "ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96",
        "sha256": "b99ce16cdf0ecfc761b585ac84d131b46733465a02f8ecd0ff2de9713c62ee09",
    },
    "1.95.0": {
        "commit": "59807616e1fa2540724bfbac14d7976d7e4a3860",
        "sha256": "62b67230754da642a264ca0cb9fc08820c54e2ed7b3baba0289876d4cdb48c08",
    },
}


def compiler_source(path: Path, version: str) -> dict:
    """Rehash the exact independently release-signed archive; never extract it."""
    require(version in COMPILERS, "unreviewed Rust compiler source version")
    expected = COMPILERS[version]
    require(
        path.is_file() and not path.is_symlink() and sha256(path) == expected["sha256"],
        "exact signed upstream Rust compiler source hash/type mismatch",
    )
    prefix = "rustc-" + version + "-src"
    seen = set()
    counts = Counter()
    witnesses = {}
    links = []
    required = {
        "git-commit-hash",
        "src/version",
        "LICENSE-APACHE",
        "LICENSE-MIT",
        "library/std/src/lib.rs",
        "library/core/src/lib.rs",
        "library/alloc/src/lib.rs",
    }
    with tarfile.open(path, "r|xz") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            pure = PurePosixPath(name)
            require(
                bool(pure.parts)
                and pure.parts[0] == prefix
                and not pure.is_absolute()
                and str(pure) == name
                and ".." not in pure.parts
                and "\\" not in name
                and all(ord(c) >= 32 for c in name),
                "unsafe original Rust source member path",
            )
            require(
                name not in seen
                and (member.isreg() or member.isdir() or member.issym()),
                "duplicate or unsupported Rust source member",
            )
            seen.add(name)
            counts[member.type.decode()] += 1
            relative = PurePosixPath(*pure.parts[1:]).as_posix()
            if member.issym():
                # Original compiler test/vendor trees contain symlink metadata.
                # Preserve it as archive evidence; do not follow or extract it.
                links.append({"path": relative, "target": member.linkname})
            if relative not in required:
                continue
            require(
                member.isreg() and member.size <= 16 * 1024**2,
                "regular bounded Rust source witness required",
            )
            stream = archive.extractfile(member)
            require(stream is not None, "unreadable original Rust source witness")
            data = stream.read()
            witnesses[relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
            if relative == "git-commit-hash":
                require(
                    data.decode().strip() == expected["commit"],
                    "Rust source commit differs from observed compiler subject",
                )
            if relative == "src/version":
                require(
                    data.decode().strip() == version, "Rust source version mismatch"
                )
    require(
        set(witnesses) == required,
        "incomplete original compiler/standard-library source archive",
    )
    return {
        "status": "PINNED_UPSTREAM_RUST_COMPILER_SOURCE_ONLY",
        "version": version,
        "commit": expected["commit"],
        "archive_sha256": expected["sha256"],
        "source_member_count": len(seen),
        "source_member_types": dict(counts),
        "source_witnesses": witnesses,
        "symlinks": links,
        "full_runtime_source_acceptance": False,
        "scope": "Whole independently release-signed upstream compiler/standard-library source archive, original notices and embedded commit witnesses only. Runtime GPG is not repeated; approved archive hash is enforced. Original symlinks remain unextracted evidence. Distro patch/build provenance, complete binary linkage, language support and aggregate license/advisory/runtime acceptance remain separate.",
    }


def identity(name: str, version: str) -> None:
    require(
        isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name) is not None,
        "unsafe Rust crate name",
    )
    require(
        isinstance(version, str)
        and re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version) is not None,
        "unsafe Rust crate version",
    )


def locked_crates(paths: dict[str, Path]) -> dict:
    require(
        set(paths) == set(LOCKS),
        "complete exact external Rust origin lock set required",
    )
    registry = {}
    workspace = {}
    for origin, path in sorted(paths.items()):
        require(
            path.is_file() and not path.is_symlink() and sha256(path) == LOCKS[origin],
            "reviewed patched Cargo lock identity mismatch",
        )
        packages = tomllib.loads(path.read_text())["package"]
        local = []
        seen = set()
        for package in packages:
            name = package["name"]
            version = package["version"]
            identity(name, version)
            key = (name, version)
            require(key not in seen, "duplicate Cargo package identity")
            seen.add(key)
            if "source" not in package:
                require(
                    "checksum" not in package, "unexpected workspace package checksum"
                )
                local.append({"name": name, "version": version})
                continue
            require(
                package["source"] == REGISTRY
                and re.fullmatch(r"[0-9a-f]{64}", package.get("checksum", ""))
                is not None,
                "unreviewed or unhashed Cargo registry source",
            )
            if key in registry:
                require(
                    registry[key]["sha256"] == package["checksum"],
                    "inconsistent registry crate checksum across origins",
                )
                registry[key]["origins"].append(origin)
            else:
                registry[key] = {
                    "name": name,
                    "version": version,
                    "sha256": package["checksum"],
                    "origins": [origin],
                }
        workspace[origin] = local
    return {
        "status": "REVIEWED_PATCHED_LOCK_SUBJECTS_ONLY",
        "full_runtime_source_acceptance": False,
        "registry": [registry[key] for key in sorted(registry)],
        "workspace": workspace,
        "lock_sha256": dict(LOCKS),
        "scope": "Exact previously reviewed patch-result lock bytes only. Callers must join original signed recipe/source/patch subjects; local workspace sources remain in those original project archives.",
    }


def inspect_crate(path: Path, expected: dict) -> dict:
    name = expected["name"]
    version = expected["version"]
    identity(name, version)
    require(
        re.fullmatch(r"[0-9a-f]{64}", expected.get("sha256", "")) is not None,
        "complete original Cargo checksum required",
    )
    require(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size <= 128 * 1024**2
        and sha256(path) == expected["sha256"],
        "original locked crate archive hash/type mismatch",
    )
    prefix = name + "-" + version
    files = {}
    manifest = None
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            pure = PurePosixPath(member.name)
            require(
                bool(pure.parts)
                and str(pure) == member.name
                and pure.parts[0] == prefix
                and not pure.is_absolute()
                and ".." not in pure.parts
                and len(pure.parts) > 1
                and "\\" not in member.name
                and all(ord(c) >= 32 for c in member.name),
                "unsafe original crate member path/root",
            )
            relative = PurePosixPath(*pure.parts[1:]).as_posix()
            require(
                relative not in files and relative != ".cargo-checksum.json",
                "duplicate or substituted original crate member",
            )
            require(
                member.isreg()
                and not member.mode & 0o7000
                and 0 <= member.size <= 64 * 1024**2,
                "unsupported or privileged original crate member",
            )
            total += member.size
            require(
                total <= 512 * 1024**2 and len(files) < 100000,
                "oversized original crate payload",
            )
            stream = archive.extractfile(member)
            require(stream is not None, "unreadable original crate member")
            data = stream.read()
            require(len(data) == member.size, "truncated original crate member")
            files[relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "mode": member.mode,
            }
            if relative == "Cargo.toml":
                manifest = tomllib.loads(data.decode())["package"]
    require(
        manifest is not None
        and (manifest["name"], manifest["version"]) == (name, version),
        "original Cargo manifest identity differs from locked subject",
    )
    license_value = manifest.get("license")
    require(
        isinstance(license_value, str)
        and bool(license_value)
        and "license-file" not in manifest,
        "unresolved original crate license metadata",
    )
    # Keep original legacy slash forms and SPDX-like strings intact. This is not
    # license expression normalization, legal approval or notice completeness.
    return {
        "status": "EXACT_LOCKED_REGISTRY_SOURCE_BYTES_ONLY",
        "name": name,
        "version": version,
        "package_sha256": expected["sha256"],
        "license": license_value,
        "files": files,
        "file_count": len(files),
        "source_bytes": total,
        "full_runtime_source_acceptance": False,
        "scope": "Entire original checksum-bound registry archive and every regular member, including all original notices/source files. No source executed or extracted. License strings are retained verbatim, not interpreted as legal clearance.",
    }


def verify_archives(lock_paths: dict[str, Path], directory: Path) -> dict:
    plan = locked_crates(lock_paths)
    require(
        directory.is_dir() and not directory.is_symlink(),
        "regular original registry archive directory required",
    )
    expected = {
        row["name"] + "-" + row["version"] + ".crate": row for row in plan["registry"]
    }
    actual = {path.name for path in directory.iterdir()}
    require(
        actual == set(expected), "complete exact original registry archive set required"
    )
    rows = [
        inspect_crate(directory / name, row) for name, row in sorted(expected.items())
    ]
    return {
        "status": "COMPLETE_REVIEWED_REGISTRY_ARCHIVE_INPUTS_ONLY",
        "full_runtime_source_acceptance": False,
        "registry_archives": len(rows),
        "source_files": sum(row["file_count"] for row in rows),
        "source_bytes": sum(row["source_bytes"] for row in rows),
        "lock_subjects": plan,
        "crates": rows,
        "scope": "All exact frozen patched-lock registry archive bytes and member inventories only. Signed original recipe/patch joins, compiler/language support, target binary linkage, final delivery/consumer, complete attribution/advisory and aggregate source acceptance remain separate.",
    }
