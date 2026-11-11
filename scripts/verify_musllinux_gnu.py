#!/usr/bin/env python3
"""Replay bounded GNU-wheel correspondence without loading or changing a wheel.

This is an attribution gate, not an installer or ELF normalizer. The default
refuses incomplete source closure. --binary-only explicitly produces a bounded
comparison receipt, never source/advisory/final-image clearance.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import zipfile
import zlib
from pathlib import Path

MANIFEST_SHA256 = "609dac61e58e81d074d7996bcc13dff6620cfa2592f35cd97c6e659fbebdf615"
GNU_MEMBER = re.compile(
    r"lib(?:gcc_s|stdc\+\+|gomp|gfortran|quadmath|atomic|itm)(?:-[A-Za-z0-9_+-]+)?\.so(?:\.[0-9]+)*\Z"
)


class AttributionError(RuntimeError):
    """An actual artifact, repair operation or source binding is unverified."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AttributionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def archive_member(source: Path | bytes, name: str) -> bytes:
    options = (
        {"fileobj": io.BytesIO(source)}
        if isinstance(source, bytes)
        else {"name": source}
    )
    with tarfile.open(mode="r:gz", **options) as archive:
        members = [member for member in archive if member.name == name]
        require(
            len(members) == 1
            and members[0].isreg()
            and 0 < members[0].size <= 32 * 1024 * 1024,
            "unique regular provider member required",
        )
        stream = archive.extractfile(members[0])
        require(stream is not None, "regular provider member unreadable")
        return stream.read()


def apk_payload(blob: bytes, expected: dict) -> bytes:
    # APKv2 uses three concatenated gzip streams. Its signed compressed control
    # checksum and signed payload datahash are different integrity joins.
    remaining, parts = blob, []
    while remaining:
        decompressor = zlib.decompressobj(31)
        decompressor.decompress(remaining)
        require(decompressor.eof, "incomplete APK gzip stream")
        count = len(remaining) - len(decompressor.unused_data)
        require(count > 0, "empty APK gzip stream")
        parts.append(remaining[:count])
        remaining = decompressor.unused_data
    require(len(parts) == 3, "three signed APK streams required")
    control = "Q1" + base64.b64encode(hashlib.sha1(parts[1]).digest()).decode()
    require(control == expected["C"], "APK compressed control checksum mismatch")
    info = archive_member(parts[1], ".PKGINFO").decode()
    fields = {}
    for line in info.splitlines():
        if " = " in line and not line.startswith("#"):
            key, value = line.split(" = ", 1)
            fields.setdefault(key, []).append(value)
    for key, field in [
        ("P", "pkgname"),
        ("V", "pkgver"),
        ("A", "arch"),
        ("o", "origin"),
        ("c", "commit"),
    ]:
        require(
            fields.get(field) == [expected[key]], "APK metadata/index identity mismatch"
        )
    require(
        fields.get("datahash") == [hashlib.sha256(parts[2]).hexdigest()],
        "APK signed payload hash mismatch",
    )
    return parts[2]


def cached_input(cache: Path, digest: str) -> Path:
    require(
        re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "unsafe cached input digest"
    )
    path = cache / digest
    require(
        path.is_file() and not path.is_symlink() and sha256(path) == digest,
        "cached provider/tool identity mismatch",
    )
    return path


def prepare_inputs(manifest: dict, cache: Path, output: Path) -> dict:
    require(
        not output.exists() and not output.is_symlink(),
        "prepared input location must be fresh",
    )
    require(
        not output.resolve().is_relative_to(cache.resolve())
        and not cache.resolve().is_relative_to(output.resolve()),
        "prepared inputs/cache locations must be disjoint",
    )
    originals, tools = output / "originals", output / "tools"
    originals.mkdir(parents=True)
    tools.mkdir()
    prepared = {
        "originals": {},
        "tools": {},
        "scope": "Hash-pinned provider extraction and comparison tools only; no source clearance or target ELF execution.",
    }
    for row in manifest["objects"]:
        original = row["original"]
        digest = original["sha256"]
        if digest in prepared["originals"]:
            continue
        evidence = original["evidence"]
        if original["provider_kind"] == "signed-alpine-apk":
            provider = cached_input(cache, evidence["sha256"])
            payload = apk_payload(provider.read_bytes(), evidence["index_package"])
            data = archive_member(payload, original["member"])
        elif original["provider_kind"] == "official-rust-cross-oci":
            provider = cached_input(
                cache, evidence["layer"]["digest"].removeprefix("sha256:")
            )
            diff = hashlib.sha256()
            with gzip.open(provider, "rb") as stream:
                while block := stream.read(1024 * 1024):
                    diff.update(block)
            require(
                "sha256:" + diff.hexdigest() == evidence["layer_diff_id"],
                "OCI uncompressed layer identity mismatch",
            )
            data = archive_member(provider, original["member"])
        else:
            raise AttributionError("unreviewed GNU original provider")
        require(
            hashlib.sha256(data).hexdigest() == digest
            and len(data) == original["bytes"],
            "extracted original GNU ELF mismatch",
        )
        (originals / digest).write_bytes(data)
        prepared["originals"][digest] = {
            "provider_sha256": sha256(provider),
            "member": original["member"],
        }
    for version, record in manifest["tools"].items():
        artifact = next(
            a
            for a in record["provenance"]["artifacts"]
            if a["filename"].endswith(".whl")
        )
        archive = cached_input(cache, artifact["sha256"])
        with zipfile.ZipFile(archive) as wheel:
            names = [name for name in wheel.namelist() if Path(name).name == "patchelf"]
            require(len(names) == 1, "unique comparison tool wheel member required")
            data = wheel.read(names[0])
        require(
            hashlib.sha256(data).hexdigest() == record["sha256"],
            "extracted comparison tool identity mismatch",
        )
        target = tools / record["file"]
        target.write_bytes(data)
        target.chmod(0o755)
        prepared["tools"][version] = {
            "wheel_sha256": artifact["sha256"],
            "binary_sha256": record["sha256"],
        }
    (output / "input-receipt.json").write_text(
        json.dumps(prepared, sort_keys=True, indent=2) + "\n"
    )
    return prepared


def load_manifest(path: Path) -> dict:
    require(
        path.is_file() and not path.is_symlink() and sha256(path) == MANIFEST_SHA256,
        "reviewed GNU correspondence manifest identity mismatch",
    )
    record = json.loads(path.read_text())
    require(record["schema_version"] == 1, "unsupported correspondence schema")
    expected_members(record)
    return record


def expected_members(manifest: dict) -> dict[tuple[str, str], str]:
    result = {}
    hashes = set()
    wheels = {}
    for row in manifest["objects"]:
        require(row["expected_sha256"] not in hashes, "duplicate GNU ELF object")
        hashes.add(row["expected_sha256"])
        for member in row["members"]:
            key = member["wheel"], member["path"]
            require(key not in result, "duplicate GNU wheel member")
            require(
                wheels.setdefault(key[0], member["wheel_sha256"])
                == member["wheel_sha256"],
                "conflicting whole-wheel identity",
            )
            result[key] = row["expected_sha256"]
    return result


def compare_members(actual: dict, expected: dict) -> None:
    require(actual == expected, "complete GNU member coverage/bytes mismatch")


def require_source_closure(manifest: dict, source_root: Path | None = None) -> None:
    families = {row["source_closure"] for row in manifest["objects"]}
    require(
        families and families == set(manifest["source_closures"]),
        "complete GNU source family coverage required",
    )
    unresolved = [
        name
        for name, item in manifest["source_closures"].items()
        if item["status"]
        not in {"AUTHENTICATED_PROVIDER_SOURCE", "REPRODUCED_CORRESPONDING_SOURCE"}
    ]
    require(
        not unresolved,
        "GNU corresponding source closure/delivery incomplete: "
        + ", ".join(unresolved),
    )
    require(
        source_root is not None
        and source_root.is_dir()
        and not source_root.is_symlink(),
        "GNU source closure requires actual delivered source bytes",
    )
    require(
        {path.name for path in source_root.iterdir()} == families,
        "complete GNU source family coverage mismatch",
    )
    for name, record in manifest["source_closures"].items():
        expected = record.get("source_files", [])
        require(
            expected and re.fullmatch(r"[a-z0-9-]+", name) is not None,
            "GNU source closure has no reviewed source inventory",
        )
        directory = source_root / name
        require(
            directory.is_dir() and not directory.is_symlink(),
            "GNU source closure delivery missing",
        )
        actual = set()
        for path in directory.rglob("*"):
            require(
                not path.is_symlink(),
                "GNU source closure symlink is not a retained source file",
            )
            if path.is_file():
                actual.add(str(path.relative_to(directory)))
        require(
            actual == {item["path"] for item in expected}
            and len(expected) == len(actual),
            "GNU source closure complete inventory mismatch",
        )
        for item in expected:
            path = directory / item["path"]
            require(
                path.resolve().is_relative_to(directory.resolve())
                and sha256(path) == item["sha256"],
                "GNU source closure bytes mismatch",
            )


def repair_arguments(row: dict) -> list[list[str]]:
    name = row["soname"]
    require(GNU_MEMBER.fullmatch(name) is not None, "unsafe repaired GNU SONAME")
    soname = ["--set-soname", name]
    profile = row["profile"]
    if profile == "soname-only":
        return [soname]
    if profile in [
        "auditwheel-6.6-immediate-no-rpath",
        "auditwheel-6.7-immediate-origin",
    ]:
        replacement = row["replace_needed"]
        require(
            replacement["old"] == "libgcc_s.so.1"
            and re.fullmatch(r"libgcc_s-[0-9a-f]{8}\.so\.1", replacement["new"])
            is not None,
            "unsafe GNU NEEDED replacement",
        )
        needed = ["--replace-needed", replacement["old"], replacement["new"]]
        return (
            [soname, ["--force-rpath", "--set-rpath", "$ORIGIN"], needed]
            if profile == "auditwheel-6.7-immediate-origin"
            else [soname, needed]
        )
    raise AttributionError("unreviewed GNU repair profile")


def replay(row: dict, original: Path, tool: Path, work: Path) -> dict:
    require(
        re.fullmatch(r"[0-9a-f]{64}", row["expected_sha256"]) is not None,
        "unsafe GNU ELF digest",
    )
    require(
        original.is_file()
        and not original.is_symlink()
        and sha256(original) == row["original"]["sha256"],
        "original GNU ELF identity mismatch",
    )
    target = work / row["expected_sha256"]
    require(
        not target.exists() and not target.is_symlink(),
        "GNU replay location must be fresh",
    )
    groups = repair_arguments(row)
    shutil.copyfile(original, target)
    commands = []
    for args in groups:
        command = [str(tool), *args, str(target)]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
        )
        commands.append(
            {
                "command": command,
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        require(result.returncode == 0, "GNU comparison tool failed: " + result.stderr)
    actual = sha256(target)
    require(
        actual == row["expected_sha256"], "complete ELF transformation bytes mismatch"
    )
    return {
        "original_sha256": row["original"]["sha256"],
        "result_sha256": actual,
        "profile": row["profile"],
        "commands": commands,
    }


def inspect_wheels(manifest: dict, directory: Path) -> dict:
    expected = expected_members(manifest)
    wheels = {
        m["wheel"]: m["wheel_sha256"]
        for row in manifest["objects"]
        for m in row["members"]
    }
    actual = {}
    paths = sorted(directory.glob("*.whl"))
    require(paths, "wheel directory is empty")
    selection = manifest["wheel_selection"]
    require(
        {path.name for path in paths} == set(selection),
        "complete wheel selection mismatch",
    )
    require(
        all(selection.get(name) == digest for name, digest in wheels.items()),
        "GNU members conflict with whole-wheel selection",
    )
    for path in paths:
        require(
            path.is_file() and not path.is_symlink(), "regular wheel archive required"
        )
        require(
            sha256(path) == selection[path.name],
            "whole-wheel identity mismatch: " + path.name,
        )
        with zipfile.ZipFile(path) as wheel:
            names = wheel.namelist()
            require(len(names) == len(set(names)), "duplicate wheel archive entry")
            for member in names:
                if GNU_MEMBER.fullmatch(Path(member).name):
                    # Read/archive-CRC/hash the complete member; never extract it
                    # to a runtime path or use it as the replay's input.
                    actual[path.name, member] = hashlib.sha256(
                        wheel.read(member)
                    ).hexdigest()
    compare_members(actual, expected)
    return {
        "whole_wheels": selection,
        "gnu_whole_wheels": wheels,
        "gnu_members": [
            {"wheel": wheel, "path": member, "sha256": digest}
            for (wheel, member), digest in sorted(actual.items())
        ],
    }


def verify(
    manifest: dict,
    wheels: Path,
    originals: Path,
    tools: Path,
    work: Path,
    output: Path,
    *,
    binary_only: bool = False,
    source_root: Path | None = None,
) -> None:
    require(
        not work.exists()
        and not work.is_symlink()
        and not output.exists()
        and not output.is_symlink(),
        "GNU attribution work/output locations must be fresh",
    )
    inputs = [wheels, originals, tools]
    if source_root is not None:
        inputs.append(source_root)
    for source in inputs:
        for target in [work, output]:
            require(
                not target.resolve().is_relative_to(source.resolve())
                and not source.resolve().is_relative_to(target.resolve()),
                "GNU evidence locations must be disjoint",
            )
    record = {
        "schema_version": 1,
        "manifest_sha256": MANIFEST_SHA256,
        "status": "FAILED",
        "scope": (
            "GNU binary correspondence only; no source clearance."
            if binary_only
            else "GNU binary/corresponding-source and delivered-byte binding only."
        )
        + " No target ELF execution, wheel modification, other wheel/APK source attribution, advisory or final-image clearance.",
        "source_closures": manifest["source_closures"],
        "replays": [],
        "failures": [],
    }
    work.mkdir(parents=True)
    try:
        record["wheel_inventory"] = inspect_wheels(manifest, wheels)
        for version, item in manifest["tools"].items():
            tool = tools / item["file"]
            require(
                tool.is_file()
                and not tool.is_symlink()
                and sha256(tool) == item["sha256"],
                "pinned comparison tool identity mismatch: " + version,
            )
        for row in manifest["objects"]:
            tool = tools / manifest["tools"][row["tool_version"]]["file"]
            record["replays"].append(
                replay(row, originals / row["original"]["sha256"], tool, work)
            )
        record["binary_correspondence"] = "ALL_GNU_MEMBERS_EXACT"
        if not binary_only:
            require_source_closure(manifest, source_root)
        record["status"] = (
            "BINARY_ONLY_NO_SOURCE_CLEARANCE"
            if binary_only
            else "GNU_SOURCE_ATTRIBUTED"
        )
    except (
        AttributionError,
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        subprocess.TimeoutExpired,
    ) as exc:
        record["failures"].append(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    if record["failures"]:
        raise AttributionError("; ".join(record["failures"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "manifest",
        "wheel-dir",
        "original-dir",
        "tool-dir",
        "work-dir",
        "output-file",
    ]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--binary-only",
        action="store_true",
        help="Comparison evidence only; cannot clear source or image gates",
    )
    parser.add_argument(
        "--sources-dir",
        type=Path,
        help="Delivered source tree; required for source closure",
    )
    args = parser.parse_args()
    try:
        verify(
            load_manifest(args.manifest),
            args.wheel_dir.absolute(),
            args.original_dir.absolute(),
            args.tool_dir.absolute(),
            args.work_dir.absolute(),
            args.output_file.absolute(),
            binary_only=args.binary_only,
            source_root=args.sources_dir,
        )
    except (AttributionError, OSError, ValueError, KeyError) as exc:
        print("GNU attribution failed: " + str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
