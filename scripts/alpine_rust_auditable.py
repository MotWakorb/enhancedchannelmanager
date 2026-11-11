"""Read one bounded .dep-v0 graph and join it to reviewed origin-specific locks.

No target code, object loader or build script runs. The caller must derive the
expected ELF hash from its independently authenticated APK/installed-file join,
and obtain the lock plan from alpine_rust_source_inputs.locked_crates. A matching
digest supplied by an untrusted manifest is not a signature check. Format 1 is
conservative metadata, not a proof of exact compiled features or absence of code.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib

from alpine_rust_source_inputs import LOCKS, identity
from alpine_source_inputs import InputError, require
from verify_alpine_external_sources import unique

__all__ = ["InputError", "decode_elf", "join_lock"]
MAX_METADATA = 4 * 1024**2


def graph(document: dict) -> list[dict]:
    require(
        isinstance(document, dict)
        and set(document) == {"format", "packages"}
        and type(document["format"]) is int
        and document["format"] == 1,
        "reviewed auditable metadata format 1 required",
    )
    packages = document["packages"]
    require(
        isinstance(packages, list) and 0 < len(packages) <= 10000,
        "bounded nonempty auditable package graph required",
    )
    result = []
    identities = set()
    for package in packages:
        require(
            isinstance(package, dict)
            and {"name", "version", "source"} <= set(package)
            and set(package)
            <= {"name", "version", "source", "dependencies", "kind", "root"},
            "unreviewed auditable package fields",
        )
        identity(package["name"], package["version"])
        require(
            package["source"] in {"crates.io", "local"}, "unreviewed auditable source"
        )
        key = (package["name"], package["version"], package["source"])
        require(key not in identities, "duplicate auditable package identity")
        identities.add(key)
        kind = package.get("kind", "runtime")
        root = package.get("root", False)
        dependencies = package.get("dependencies", [])
        require(
            kind in {"runtime", "build"} and type(root) is bool,
            "invalid auditable kind/root",
        )
        require(
            isinstance(dependencies, list)
            and all(
                type(index) is int and 0 <= index < len(packages)
                for index in dependencies
            )
            and len(set(dependencies)) == len(dependencies),
            "invalid auditable dependency indexes",
        )
        result.append(
            {
                "name": package["name"],
                "version": package["version"],
                "source": package["source"],
                "kind": kind,
                "root": root,
                "dependencies": dependencies,
            }
        )
    require(
        sum(package["root"] for package in result) == 1,
        "unique auditable graph root required",
    )
    # Validate the complete graph, including disconnected build inputs, without
    # recursion or treating a package's absence from a root walk as code absence.
    remaining = [len(package["dependencies"]) for package in result]
    dependents = [[] for _ in result]
    for index, package in enumerate(result):
        for dependency in package["dependencies"]:
            dependents[dependency].append(index)
    pending = [i for i, count in enumerate(remaining) if count == 0]
    processed = 0
    while pending:
        processed += 1
        for index in dependents[pending.pop()]:
            remaining[index] -= 1
            if remaining[index] == 0:
                pending.append(index)
    require(processed == len(result), "cyclic auditable dependency graph")
    return result


def decode_elf(data: bytes, expected_sha256: str, architecture: str) -> dict:
    require(
        isinstance(data, bytes)
        and 64 <= len(data) <= 128 * 1024**2
        and isinstance(expected_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None
        and hashlib.sha256(data).hexdigest() == expected_sha256,
        "authenticated subject ELF bytes/hash required",
    )
    require(
        architecture in {"amd64", "arm64"} and data[:7] == b"\x7fELF\x02\x01\x01",
        "native little-endian ELF64 required",
    )
    require(
        struct.unpack_from("<H", data, 18)[0]
        == {"amd64": 62, "arm64": 183}[architecture],
        "auditable ELF architecture mismatch",
    )
    require(struct.unpack_from("<H", data, 52)[0] == 64, "invalid ELF header size")
    offset = struct.unpack_from("<Q", data, 40)[0]
    width, count, strings_index = struct.unpack_from("<HHH", data, 58)
    require(
        width == 64
        and 0 < count <= 4096
        and 0 < strings_index < count
        and offset >= 64
        and offset + count * width <= len(data),
        "bounded ELF section/name table required",
    )
    sections = [
        struct.unpack_from("<IIQQQQIIQQ", data, offset + i * width)
        for i in range(count)
    ]
    strings = sections[strings_index]
    require(
        strings[1] == 3
        and 0 < strings[5] <= 1024**2
        and strings[4] + strings[5] <= len(data),
        "bounded ELF section-name string table required",
    )
    names = data[strings[4] : strings[4] + strings[5]]
    selected = []
    for section in sections:
        name_offset = section[0]
        require(name_offset < len(names), "ELF section name outside table")
        end = names.find(b"\0", name_offset)
        require(end >= name_offset, "unterminated ELF section name")
        if names[name_offset:end] == b".dep-v0":
            selected.append(section)
    require(
        len(selected) == 1,
        "unique ELF .dep-v0 required; missing metadata is not source clearance",
    )
    section = selected[0]
    require(
        section[1] == 1
        and 0 < section[5] <= MAX_METADATA
        and section[4] + section[5] <= len(data),
        "bounded regular .dep-v0 section required",
    )
    payload = data[section[4] : section[4] + section[5]]
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(payload, MAX_METADATA + 1)
        require(
            len(raw) <= MAX_METADATA
            and decoder.eof
            and not decoder.unused_data
            and not decoder.unconsumed_tail,
            "single bounded complete auditable zlib stream required",
        )
        document = json.loads(raw, object_pairs_hook=unique)
    except (zlib.error, ValueError, UnicodeError, RecursionError) as exc:
        raise InputError("invalid compressed auditable metadata") from exc
    packages = graph(document)
    return {
        "elf_sha256": expected_sha256,
        "architecture": architecture,
        "section_sha256": hashlib.sha256(payload).hexdigest(),
        "metadata_sha256": hashlib.sha256(raw).hexdigest(),
        "format": 1,
        "packages": packages,
    }


def join_lock(metadata: dict, plan: dict, origin: str) -> dict:
    require(
        origin in LOCKS and plan.get("lock_sha256") == LOCKS,
        "complete reviewed origin-specific Cargo lock plan required",
    )
    packages = graph({"format": metadata["format"], "packages": metadata["packages"]})
    registry = {
        (r["name"], r["version"]): r for r in plan["registry"] if origin in r["origins"]
    }
    workspace = {(r["name"], r["version"]) for r in plan["workspace"][origin]}
    joined = []
    local = []
    root = next(p for p in packages if p["root"])
    require(
        root["source"] == "local",
        "auditable root must join original local workspace source",
    )
    for package in packages:
        key = (package["name"], package["version"])
        if package["source"] == "crates.io":
            require(
                key in registry,
                "auditable registry package missing from its origin lock: " + repr(key),
            )
            digest = registry[key]["sha256"]
            require(
                isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                "auditable registry archive checksum required",
            )
            joined.append({**package, "archive_sha256": digest})
        else:
            require(
                key in workspace,
                "auditable local package missing from original workspace source: "
                + repr(key),
            )
            local.append(package)
    return {
        "status": "ELF_AUDITABLE_METADATA_ORIGIN_LOCK_JOIN_ONLY",
        "origin": origin,
        "elf_sha256": metadata["elf_sha256"],
        "section_sha256": metadata["section_sha256"],
        "registry_packages": len(joined),
        "local_packages": len(local),
        "registry": joined,
        "local": local,
        "root": {"name": root["name"], "version": root["version"]},
        "full_runtime_source_acceptance": False,
        "exact_compiled_features_established": False,
        "scope": "All packages declared by this authenticated ELF's conservative auditable format-1 graph join their original origin-specific frozen lock. Whole crate/archive and local workspace source delivery, compiler/distro build provenance and complete binary/source/attribution/advisory acceptance must be independently enforced. No claim of exact compiled features or complete code absence from missing metadata.",
    }
