"""Exact approved owned BusyBox APKv2/control/payload/installed-record joins.

These are not APKv3 rules: C binds the original compressed control SHA1, signed
control datahash binds complete payload SHA256, and these reviewed installed
file/link Q1 tokens contain SHA1. The caller must additionally authenticate the
original packages/public key, original provider/source policy, installed control
scripts and live files. No package installation, extraction or code execution.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

from alpine_apk_v2 import AttributionError, inspect_apk, parts
from alpine_source_inputs import InputError, require
from alpine_source_inventory import InventoryError, payload_record
from alpine_source_patch import replay_patch
from build_busybox_provider import MANIFEST_SHA256, derive_apkbuild, read_policy
from build_native_runtime import BuildError
from verify_alpine_external_sources import sha256, unique

__all__ = [
    "InputError",
    "bind_installed_record",
    "load_corresponding_plan",
    "replay_original_sources",
    "replay_patch",
    "source_inputs",
    "verify_original_sources",
]
NAMES = {"busybox", "busybox-binsh", "ssl_client"}
CORRESPONDING_PLAN_SHA256 = (
    "3e55d5056d82268b3150d3b0d631884a33b270544e32f4a007e8932ac3b1ebfb"
)


def source_inputs(policy: dict, original: bytes) -> list[dict]:
    """Decode all original checksum rows, not a shell-evaluated source list.

    Caller first authenticates the approved policy with read_policy. This pure
    decoder alone is not policy authentication or compiled-source acceptance.
    """
    require(
        hashlib.sha256(original).hexdigest()
        == policy["aports"]["original_recipe_sha256"],
        "original BusyBox source recipe identity differs",
    )
    rows = [
        {"file": name.decode(), "sha512": digest.decode()}
        for digest, name in re.findall(
            rb"^([0-9a-f]{128})  (.+)$", original, re.MULTILINE
        )
    ]
    require(
        len(rows) == policy["aports"]["source_input_checksums"]
        and len({row["file"] for row in rows}) == len(rows),
        "complete unique original BusyBox source input set required",
    )
    for row in rows:
        name = row["file"]
        require(
            bool(name)
            and name not in {".", ".."}
            and PurePosixPath(name).name == name
            and str(PurePosixPath(name)) == name
            and not any(character.isspace() for character in name),
            "safe original BusyBox source input basename required",
        )
    require(
        rows[0]
        == {"file": policy["source"]["filename"], "sha512": policy["source"]["sha512"]},
        "original BusyBox upstream archive checksum differs",
    )
    return rows


def verify_original_sources(source: Path) -> dict:
    """Independent original archive/aports/input/derived-recipe byte checks.

    No delivered shell/Python source is evaluated and no patched tree or target
    is executed. Full original-to-patched-source replay is a separate obligation.
    """
    require(
        source.is_dir()
        and not source.is_symlink()
        and source.absolute() == source.resolve(),
        "regular canonical original BusyBox sources required",
    )
    try:
        policy = read_policy(source / "owned-recipe/manifest.json")
        for row in [policy["aports"], *policy["patches"]]:
            path = source / "owned-recipe" / row["file"]
            require(
                path.is_file()
                and not path.is_symlink()
                and path.resolve().is_relative_to(source)
                and sha256(path) == row["sha256"],
                "original BusyBox retained apports/owned patch identity differs",
            )
            if "sha512" in row:
                require(
                    hashlib.sha512(path.read_bytes()).hexdigest() == row["sha512"],
                    "owned BusyBox patch SHA512 differs",
                )
        original_path = source / "original-aports/APKBUILD"
        require(
            original_path.is_file() and not original_path.is_symlink(),
            "regular original BusyBox recipe required",
        )
        original = original_path.read_bytes()
        rows = source_inputs(policy, original)
        for row in rows:
            path = source / (
                row["file"]
                if row["file"] == policy["source"]["filename"]
                else "original-aports/" + row["file"]
            )
            require(
                path.is_file()
                and not path.is_symlink()
                and path.resolve().is_relative_to(source)
                and hashlib.sha512(path.read_bytes()).hexdigest() == row["sha512"],
                "original BusyBox source input SHA512 differs: " + row["file"],
            )
        archive = source / policy["source"]["filename"]
        require(
            archive.stat().st_size == policy["source"]["size"]
            and sha256(archive) == policy["source"]["sha256"],
            "original BusyBox upstream archive SHA256/size differs",
        )
        apports_files = set()
        with tarfile.open(source / "owned-recipe" / policy["aports"]["file"]) as bundle:
            for member in bundle:
                if member.isdir():
                    continue
                prefix = "main/busybox/"
                require(
                    member.isreg()
                    and member.name.startswith(prefix)
                    and member.size <= 1024**2,
                    "reviewed original BusyBox apports member required",
                )
                name = member.name.removeprefix(prefix)
                require(
                    PurePosixPath(name).name == name and name not in apports_files,
                    "unique flat original BusyBox apports member required",
                )
                apports_files.add(name)
                path = source / "original-aports" / name
                raw = bundle.extractfile(member)
                require(
                    raw is not None
                    and path.is_file()
                    and not path.is_symlink()
                    and path.read_bytes() == raw.read(),
                    "delivered original BusyBox apports file differs from retained archive",
                )
        require(
            {path.name for path in (source / "original-aports").iterdir()}
            == apports_files,
            "complete original BusyBox apports tree required",
        )
        derived = source / "derived-APKBUILD"
        require(
            derived.is_file()
            and not derived.is_symlink()
            and derived.read_bytes()
            == derive_apkbuild(original.decode(), policy).encode(),
            "exact original-to-owned BusyBox derived recipe differs",
        )
    except BuildError as exc:
        raise InputError(
            "original BusyBox policy/derived recipe validation failed: " + str(exc)
        ) from exc
    return {
        "status": "OWNED_BUSYBOX_ORIGINAL_ARCHIVE_APORTS_ALL_INPUTS_AND_DERIVED_RECIPE_JOIN_ONLY",
        "original_source_inputs_verified": len(rows),
        "original_aports_files_verified": len(apports_files),
        "owned_patches_verified": len(policy["patches"]),
        "upstream_archive_sha256": policy["source"]["sha256"],
        "full_runtime_source_acceptance": False,
        "source_patch_replay_established": False,
        "historical_compiler_linkage_established": False,
        "scope": "Approved policy hash, original upstream SHA256/SHA512/size, every original SHA512 input, all original retained apports member bytes, both owned patch hashes and exact deterministic APKBUILD derivation. No shell evaluation, build/source/target execution, patch replay, historical compiler/reproduced-build assertion or aggregate source/advisory acceptance.",
    }


def load_corresponding_plan(path: Path, policy: dict) -> dict:
    """Five explicit source-only diff equivalents, never discretionary fallback.

    Both original and corresponding patch hashes and every full original file
    witness are bound. The original build inputs, tests and recipes stay intact.
    """
    require(
        path.is_file()
        and not path.is_symlink()
        and path.absolute() == path.resolve()
        and path.stat().st_size <= 64 * 1024
        and sha256(path) == CORRESPONDING_PLAN_SHA256,
        "reviewed explicit BusyBox corresponding-source plan required",
    )
    plan = json.loads(path.read_text(), object_pairs_hook=unique)
    require(
        type(plan["schema_version"]) is int
        and plan["schema_version"] == 1
        and plan["policy_sha256"] == MANIFEST_SHA256
        and plan["upstream_archive_sha256"] == policy["source"]["sha256"],
        "corresponding BusyBox source policy/archive identity differs",
    )
    originals = {
        row["file"]: row
        for row in policy["aports"]["vendor_patch_witnesses"] + policy["patches"]
    }
    entries = {"source-replay.json", "patches"}
    seen = set()
    for row in plan["patches"]:
        require(
            row["original_file"] in originals
            and row["original_file"] not in seen
            and row["witnesses"] == originals[row["original_file"]]["witnesses"],
            "complete corresponding/original BusyBox source witnesses differ",
        )
        seen.add(row["original_file"])
        name = row["file"]
        require(
            str(PurePosixPath(name)) == name
            and PurePosixPath(name).parent == PurePosixPath("patches")
            and name not in entries,
            "safe unique corresponding BusyBox patch path required",
        )
        entries.add(name)
        patch = path.parent / name
        require(
            patch.is_file()
            and not patch.is_symlink()
            and patch.resolve().is_relative_to(path.parent)
            and patch.stat().st_size <= 1024**2
            and sha256(patch) == row["sha256"],
            "corresponding BusyBox literal patch bytes/type differ",
        )
    require(
        len(seen) == 5
        and {
            entry.relative_to(path.parent).as_posix()
            for entry in path.parent.rglob("*")
        }
        == entries,
        "complete exact BusyBox corresponding-source plan namespace required",
    )
    return plan


def replay_original_sources(
    source: Path, *, corresponding_plan: Path | None = None
) -> dict:
    """Reproduce all reviewed patched source bytes in memory, never run them.

    This independently establishes source transformation correspondence. It does
    not reproduce compiler output or authenticate historical compiler linkage.
    """
    original = verify_original_sources(source)
    policy = read_policy(source / "owned-recipe/manifest.json")
    plan = (
        load_corresponding_plan(corresponding_plan, policy)
        if corresponding_plan is not None
        else None
    )
    equivalents = (
        {row["original_file"]: row for row in plan["patches"]}
        if plan is not None
        else {}
    )
    used_equivalents = set()
    files, modes = {}, {}
    total = 0
    prefix = "busybox-" + policy["upstream_version"] + "/"
    with tarfile.open(source / policy["source"]["filename"]) as archive:
        for member in archive:
            if member.name.rstrip("/") == prefix.rstrip("/"):
                require(member.isdir(), "original BusyBox source archive root differs")
                continue
            require(
                member.name.startswith(prefix) and (member.isdir() or member.isreg()),
                "original BusyBox source archive regular members/directories required",
            )
            name = member.name.removeprefix(prefix).rstrip("/")
            require(
                name
                and str(PurePosixPath(name)) == name
                and not PurePosixPath(name).is_absolute()
                and ".." not in PurePosixPath(name).parts,
                "safe original BusyBox source archive path required",
            )
            if member.isdir():
                continue
            total += member.size
            require(
                name not in files
                and len(files) < 4096
                and 0 <= member.size <= 1024**2
                and total <= 16 * 1024**2
                and member.mode in {0o644, 0o755},
                "bounded complete original BusyBox source archive required",
            )
            data = archive.extractfile(member)
            require(
                data is not None, "readable original BusyBox source member required"
            )
            files[name] = data.read()
            require(
                len(files[name]) == member.size,
                "complete original BusyBox source bytes required",
            )
            modes[name] = member.mode
    original_files = len(files)
    original_size = total
    touched = set()
    patches = []
    inputs = {
        row["file"]: row["sha512"]
        for row in source_inputs(
            policy, (source / "original-aports/APKBUILD").read_bytes()
        )
    }
    for kind, records in [
        ("vendor", policy["aports"]["vendor_patch_witnesses"]),
        ("owned", policy["patches"]),
    ]:
        for record in records:
            path = (
                source
                / ("original-aports" if kind == "vendor" else "owned-recipe")
                / record["file"]
            )
            raw = path.read_bytes()
            require(
                (hashlib.sha512(raw).hexdigest() == inputs[record["file"]])
                if kind == "vendor"
                else (hashlib.sha256(raw).hexdigest() == record["sha256"]),
                "complete original BusyBox replay patch identity differs",
            )
            selected = raw
            if record["file"] in equivalents:
                row = equivalents[record["file"]]
                require(
                    hashlib.sha256(raw).hexdigest() == row["original_sha256"],
                    "corresponding BusyBox patch original hash differs",
                )
                selected = (corresponding_plan.parent / row["file"]).read_bytes()
                require(
                    hashlib.sha256(selected).hexdigest() == row["sha256"],
                    "corresponding BusyBox patch changed after plan verification",
                )
                used_equivalents.add(record["file"])
            try:
                changed = replay_patch(files, selected, record["witnesses"])
            except InputError as exc:
                raise InputError(
                    "BusyBox original source replay failed for "
                    + record["file"]
                    + ": "
                    + str(exc)
                ) from exc
            files.update(changed)
            for name in changed:
                modes.setdefault(name, 0o644)
            touched.update(changed)
            patches.append(
                {
                    "kind": kind,
                    "file": record["file"],
                    "original_sha256": hashlib.sha256(raw).hexdigest(),
                    "replay_sha256": hashlib.sha256(selected).hexdigest(),
                    "source_equivalent": record["file"] in used_equivalents,
                    "file_witnesses_verified": len(changed),
                }
            )
    require(
        used_equivalents == set(equivalents),
        "complete corresponding BusyBox patch use required",
    )
    require(
        touched == set(policy["prepared_source_sha256"]),
        "complete original BusyBox prepared source witness coverage differs",
    )
    for name, expected in policy["prepared_source_sha256"].items():
        require(
            hashlib.sha256(files[name]).hexdigest() == expected,
            "complete replayed BusyBox prepared source differs: " + name,
        )
    inventory = [
        {
            "path": name,
            "type": "file",
            "sha256": hashlib.sha256(files[name]).hexdigest(),
            "size": len(files[name]),
            "mode": modes[name],
        }
        for name in sorted(files, key=PurePosixPath)
    ]
    digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    require(
        plan is None
        or (
            len(files) == plan["patched_regular_files"]
            and digest == plan["patched_source_inventory_sha256"]
        ),
        "complete corresponding BusyBox source inventory differs from independent native GNU control",
    )
    return {
        "status": "OWNED_BUSYBOX_COMPLETE_LITERAL_CORRESPONDING_PATCHED_SOURCE_REPLAY_ONLY",
        "original_inputs": original,
        "original_regular_files": original_files,
        "original_source_bytes": original_size,
        "patches_replayed": len(patches),
        "literal_equivalent_patches_verified": len(used_equivalents),
        "original_patch_plan_zero_offset_or_fuzz_claimed": False,
        "file_witness_applications": sum(
            row["file_witnesses_verified"] for row in patches
        ),
        "prepared_source_witnesses_verified": len(touched),
        "patched_regular_files": len(files),
        "patched_source_bytes": sum(len(data) for data in files.values()),
        "patched_source_inventory_sha256": digest,
        "patches": patches,
        "full_runtime_source_acceptance": False,
        "source_patch_replay_established": True,
        "historical_compiler_linkage_established": False,
        "scope": "Entire original archive plus all original vendor/owned patch identities; five separately sealed source-equivalent diff copies use the exact same full original pre/post file witnesses, never discretionary fuzz/search/offset. All final prepared-source hashes and complete source inventory match independent GNU-default original-patch control. Original patch files/build recipe/tests/goldens stay unchanged; strict replay of the unadapted original stack still rejects and its failure is retained, not relabeled zero-offset/zero-fuzz. Pure in-memory byte transformation with no extraction/shell/patch/compiler/upstream program/test execution. Historical compiler/static linkage, reproduced build binaries, live files/control scripts, complete attribution/advisory/MCP/aggregate acceptance remain separate.",
    }


def bind_installed_record(
    package: Path, text: str, expected: dict, architecture: str
) -> dict:
    require(
        architecture in {"amd64", "arm64"} and expected["name"] in NAMES,
        "reviewed owned BusyBox architecture/package required",
    )
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    require(
        package.is_file()
        and not package.is_symlink()
        and package.name == expected["file"]
        and package.stat().st_size <= 16 * 1024**2
        and sha256(package) == expected["sha256"],
        "original owned BusyBox APK bytes differ",
    )
    with package.open("rb") as stream:
        require(
            stream.read(2) == b"\x1f\x8b",
            "owned BusyBox APKv2 required; ADB parsing forbidden",
        )
    try:
        database = payload_record(text, machine)
        actual = inspect_apk(package)
        streams = parts(package)
    except (InventoryError, AttributionError) as exc:
        raise InputError("invalid owned BusyBox package/record: " + str(exc)) from exc
    fields = database["record"]["fields"]
    control = actual["fields"]
    name = expected["name"]
    keys = {"C", "P", "V", "A", "S", "I", "T", "U", "L", "o", "m", "t", "D", "p", "r"}
    if name == "busybox-binsh":
        keys.add("k")
    if name == "ssl_client":
        keys.add("i")
    require(
        set(fields) == keys,
        "exact reviewed owned BusyBox installed metadata/format required",
    )
    require(
        fields["P"] == name
        and fields["o"] == "busybox"
        and fields["L"] == "GPL-2.0-only"
        and fields["A"] == ("noarch" if name == "busybox-binsh" else machine)
        and fields["S"] == str(package.stat().st_size)
        and package.name == f"{name}-{fields['V']}.apk",
        "owned BusyBox installed package identity differs",
    )
    compressed_control_sha1 = hashlib.sha1(streams[1]).digest()
    require(
        fields["C"] == "Q1" + base64.b64encode(compressed_control_sha1).decode(),
        "owned BusyBox installed control checksum differs",
    )
    scalar = {
        "P": "pkgname",
        "V": "pkgver",
        "A": "arch",
        "I": "size",
        "T": "pkgdesc",
        "U": "url",
        "L": "license",
        "o": "origin",
        "m": "maintainer",
        "t": "builddate",
    }
    if name == "busybox-binsh":
        scalar["k"] = "provider_priority"
    for installed, key in scalar.items():
        require(
            control.get(key) == [fields[installed]],
            "owned BusyBox signed control/installed scalar differs: " + installed,
        )
    lists = {"D": "depend", "p": "provides", "r": "replaces"}
    if name == "ssl_client":
        lists["i"] = "install_if"
    for installed, key in lists.items():
        require(
            isinstance(control.get(key), list),
            "complete owned BusyBox signed control list required",
        )
        source = [token for value in control[key] for token in value.split()]
        observed = fields[installed].split()
        require(
            len(source) == len(set(source))
            and len(observed) == len(set(observed))
            and set(source) == set(observed),
            "owned BusyBox signed control/installed list differs: " + installed,
        )
    require(
        control.get("commit") == [""]
        and control.get("packager") == ["ECM owned BusyBox security backport"],
        "reviewed owned BusyBox control provenance required",
    )
    require(
        actual["payload"] == expected["installed_payload"]
        and actual["control_scripts"] == expected["control_scripts"],
        "complete original owned BusyBox payload/control inventory differs",
    )
    directories = {
        row["path"]: f"{row['uid']}:{row['gid']}:{row['mode']:o}"
        for row in actual["payload"]
        if row["type"] == "directory"
    }
    files = {
        row["path"]: row for row in actual["payload"] if row["type"] != "directory"
    }
    require(
        set(database["files"]) == set(files) and database["directories"] == directories,
        "complete owned BusyBox installed payload path/directory set differs",
    )
    # inspect_apk already checked the complete signed-control payload datahash.
    # Read original regular bytes/link text only; never follow/extract a member.
    checksums = {}
    with tarfile.open(fileobj=io.BytesIO(streams[2]), mode="r:gz") as archive:
        for member in archive:
            path = member.name.removeprefix("./").rstrip("/")
            if path not in files:
                continue
            if member.isreg():
                source = archive.extractfile(member)
                require(source is not None, "unreadable original owned BusyBox payload")
                digest = hashlib.file_digest(source, "sha1").digest()
            else:
                require(member.issym(), "reviewed owned BusyBox payload type required")
                digest = hashlib.sha1(member.linkname.encode()).digest()
            checksums[path] = "Q1" + base64.b64encode(digest).decode()
    require(
        set(checksums) == set(files),
        "complete owned BusyBox installed checksum subjects required",
    )
    for path, row in files.items():
        require(
            database["files"][path]
            == {
                "a": f"{row['uid']}:{row['gid']}:{row['mode']:o}",
                "Z": checksums[path],
            },
            "owned BusyBox installed file attributes/checksum differ",
        )
    control_scripts = {
        name
        + "-"
        + fields["V"]
        + ".X1"
        + compressed_control_sha1.hex()
        + script["path"]: script
        for script in actual["control_scripts"]
    }
    return {
        "status": "OWNED_BUSYBOX_APK2_CONTROL_PAYLOAD_AND_FULL_INSTALLED_RECORD_JOIN_SIGNATURE_REQUIRED",
        "name": name,
        "architecture": architecture,
        "package_sha256": expected["sha256"],
        "control_checksum": fields["C"],
        "payload_datahash_sha256": control["datahash"][0],
        "payload": actual["payload"],
        "control_scripts": actual["control_scripts"],
        "installed_control_script_subjects": control_scripts,
        "database_record_sha256": database["record"]["database_record_sha256"],
        "database_files_verified": len(files),
        "database_directories_verified": len(directories),
        "signature_verification_required": True,
        "full_runtime_source_acceptance": False,
        "scope": "Exact owned APKv2 controls/payload datahash and complete actual installed metadata/file paths/modes/checksums only. Caller must authenticate original scoped package signatures and provider/source policy, live control scripts/files, compiler/static source and full attribution/advisory/runtime gates. No APKv3 reinterpretation or missing-origin exemption.",
    }
