"""Bounded owned-native component and live-path joins, not source clearance.

Compose with original native APKv3 signature/full installed-record verification.
Never execute a delivered recipe, target binary, compiler or source program.
Unsigned build receipts describe historical assertions; matching their bytes does
not prove reproduced builds, historical compiler linkage or advisory acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

import package_alpine_native
from alpine_owned_apk_v3 import relative
from alpine_source_inputs import InputError, require
from build_alpine_codecs import load_recipe as codecs_recipe
from build_alpine_ffmpeg import load_recipe as ffmpeg_recipe
from build_alpine_gosu import load_recipe as gosu_recipe
from build_alpine_jxl import load_recipe as jxl_recipe
from build_alpine_resdet import source_input as resdet_input
from build_alpine_security_libraries import load_recipe as security_recipe
from build_native_runtime import BuildError, owned_artifacts
from package_alpine_native import COMPONENTS, package_version, validate_runtime_path
from verify_alpine_external_sources import sha256, unique

__all__ = ["InputError", "bind_components", "bind_live_paths"]
SOURCES = "usr/share/ecm/sources/owned"
PROVENANCE = "usr/share/ecm/provenance/owned"
LOADER = b"/usr/local/lib64\n/usr/local/lib\n/lib\n/usr/lib\n"


def file_row(path: str, data: bytes, mode: int = 0o644) -> dict:
    return {
        "path": path,
        "type": "file",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mode": mode,
    }


def bind_components(
    receipt: dict, original_receipts: dict[str, bytes], architecture: str
) -> dict:
    """Join all six original raw receipts to the complete signed artifact set.

    The caller MUST first authenticate receipt.installed_artifacts with the
    original signed APK. This function alone authenticates neither input.
    """
    require(
        architecture in {"amd64", "arm64"}
        and receipt["architecture"] == architecture
        and receipt["status"] == "SIGNED_OWNED_APK_ONLY",
        "owned component package architecture/status differs",
    )
    require(
        set(original_receipts) == set(receipt["components"]) == set(COMPONENTS),
        "complete reviewed owned component set required",
    )
    helper = Path(package_alpine_native.__file__).read_bytes()
    require(
        hashlib.sha256(helper).hexdigest() == receipt["helper_sha256"],
        "reviewed original packager bytes differ",
    )
    require(
        package_version(receipt["components"], receipt["helper_sha256"])
        == receipt["package"]["version"],
        "owned component-derived package version differs",
    )
    records = {}
    artifacts = []
    source_count = 0
    runtime_count = 0
    for name in sorted(COMPONENTS):
        raw = original_receipts[name]
        require(
            isinstance(raw, bytes) and 0 < len(raw) <= 32 * 1024**2,
            "bounded original raw component receipt required",
        )
        try:
            record = json.loads(raw, object_pairs_hook=unique)
        except (ValueError, RecursionError) as exc:
            raise InputError("invalid original component receipt JSON") from exc
        digest, status = COMPONENTS[name]
        require(
            receipt["components"][name]
            == {
                "recipe_sha256": digest,
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
            }
            and record["recipe_sha256"] == digest
            and record["status"] == status
            and record["architecture"] == architecture,
            "original component receipt identity/recipe/architecture differs",
        )
        records[name] = record
        artifacts.append(file_row(f"{PROVENANCE}/{name}.json", raw))
        for key, prefix in [
            ("owned_artifacts", ""),
            ("source_files", f"{SOURCES}/{name}/"),
        ]:
            inventory = record[key]
            require(
                isinstance(inventory, list) and inventory,
                "complete original component inventory required",
            )
            for row in inventory:
                relative(row["path"])
                if not prefix:
                    try:
                        validate_runtime_path(row["path"])
                    except BuildError as exc:
                        raise InputError(str(exc)) from exc
                artifacts.append({**row, "path": prefix + row["path"]})
            source_count += len(inventory) if prefix else 0
            runtime_count += 0 if prefix else len(inventory)
    for name in ["codecs", "security", "jxl"]:
        require(
            records["ffmpeg"]["component_receipts"][name] == records[name],
            "FFmpeg original component composition differs",
        )
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    loader = f"etc/ld-musl-{machine}.path"
    require(receipt["loader_path"] == loader, "reviewed owned musl loader path differs")
    artifacts += [
        file_row(loader, LOADER),
        file_row(f"{SOURCES}/package_alpine_native.py", helper, 0o755),
    ]
    require(
        len({row["path"] for row in artifacts}) == len(artifacts),
        "colliding owned component artifact/source/receipt paths",
    )
    require(
        sorted(artifacts, key=lambda row: PurePosixPath(row["path"]))
        == receipt["installed_artifacts"],
        "complete component source/artifact/receipt package inventory differs",
    )
    return {
        "status": "OWNED_SIX_COMPONENT_RECEIPT_VERSION_AND_COMPLETE_ARTIFACT_JOIN_SIGNATURE_REQUIRED",
        "architecture": architecture,
        "components": records,
        "source_files_verified": source_count,
        "component_artifacts_verified": runtime_count,
        "complete_package_entries_verified": len(artifacts),
        "signature_verification_required": True,
        "full_runtime_source_acceptance": False,
    }


def bind_live_paths(
    root: Path, artifacts: list[dict], directories: dict[str, str]
) -> dict:
    """Check each bound file, link and directory without following parent links.

    Caller supplies original signed/DB-joined inventories, not a new allowlist.
    Directory attributes carry the signed identity for files in that directory;
    the owned-native composition supplies exclusively the reviewed 0:0 entries.
    Shared runtime directories may contain unrelated packages' paths.
    """
    require(
        root.is_dir() and not root.is_symlink() and root.absolute() == root.resolve(),
        "regular canonical owned filesystem root required",
    )
    require(
        artifacts
        and len({row["path"] for row in artifacts}) == len(artifacts)
        and directories,
        "complete unique owned live inventory required",
    )
    identities = {}
    try:
        for name, value in directories.items():
            relative(name)
            uid, gid, mode = value.split(":")
            identities[name] = (int(uid), int(gid))
            path = root / name
            metadata = path.lstat()
            require(
                stat.S_ISDIR(metadata.st_mode)
                and (metadata.st_uid, metadata.st_gid) == identities[name]
                and stat.S_IMODE(metadata.st_mode) == int(mode, 8),
                "owned live directory type/identity/mode differs",
            )
            require(
                all(
                    str(parent) == "." or str(parent) in directories
                    for parent in PurePosixPath(name).parents
                ),
                "complete owned live parent directory set required",
            )
        regular = links = 0
        for row in artifacts:
            relative(row["path"])
            parent = str(PurePosixPath(row["path"]).parent)
            require(parent in directories, "owned live artifact parent absent")
            path = root / row["path"]
            metadata = path.lstat()
            require(
                (metadata.st_uid, metadata.st_gid) == identities[parent],
                "owned live file identity differs",
            )
            if row["type"] == "file":
                require(
                    stat.S_ISREG(metadata.st_mode)
                    and metadata.st_size == row["size"]
                    and stat.S_IMODE(metadata.st_mode) == row["mode"]
                    and sha256(path) == row["sha256"],
                    "owned live regular bytes/type/size/mode differ",
                )
                regular += 1
            else:
                require(
                    row["type"] == "symlink"
                    and stat.S_ISLNK(metadata.st_mode)
                    and stat.S_IMODE(metadata.st_mode) == 0o777
                    and os.readlink(path) == row["target"]
                    and path.resolve().is_relative_to(root)
                    and sha256(path) == row["target_sha256"],
                    "owned live symlink text/referent differs",
                )
                links += 1
    except InputError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise InputError(
            "unreadable/invalid owned live filesystem subject: " + str(exc)
        ) from exc
    return {
        "status": "OWNED_LIVE_PATH_BYTES_IDENTITY_AND_MODE_JOIN_SIGNATURE_REQUIRED",
        "regular_files_verified": regular,
        "symlinks_verified": links,
        "directories_verified": len(directories),
        "signature_verification_required": True,
        "full_runtime_source_acceptance": False,
    }


def check_input(path: Path, item: dict) -> None:
    require(
        path.is_file()
        and not path.is_symlink()
        and sha256(path) == item["sha256"]
        and (
            "size" not in item
            or (type(item["size"]) is int and path.stat().st_size == item["size"])
        ),
        "owned original source input hash/size/type differs: " + str(path),
    )


def verify_sources(root: Path, records: dict) -> dict:
    """Recheck full delivered inventories and sealed upstream recipe inputs.

    Existing trusted checker code validates delivered recipes/patch hashes; no
    Python module or recipe from the delivered source tree is imported/executed.
    Archive hash correspondence is not source patch replay/compiler provenance.
    """
    base = root / SOURCES
    require(
        set(records) == set(COMPONENTS), "complete owned source component set required"
    )
    expected = [
        file_row(
            "package_alpine_native.py",
            Path(package_alpine_native.__file__).read_bytes(),
            0o755,
        )
    ]
    for name, record in records.items():
        source = base / name
        require(
            source.is_dir()
            and not source.is_symlink()
            and owned_artifacts(source) == record["source_files"],
            "complete owned original source inventory differs: " + name,
        )
        expected.extend(
            {**row, "path": name + "/" + row["path"]} for row in record["source_files"]
        )
    require(
        owned_artifacts(base)
        == sorted(expected, key=lambda row: PurePosixPath(row["path"])),
        "complete owned source namespace inventory differs",
    )
    try:
        codecs = codecs_recipe(base / "codecs/recipe/codecs.json")
        security = security_recipe(base / "security/recipe/security-libraries.json")
        jxl = jxl_recipe(base / "jxl/recipe/libjxl.json")
        ffmpeg = ffmpeg_recipe(
            base / "ffmpeg/recipe/ffmpeg.json", base / "ffmpeg/recipe"
        )
        gosu = gosu_recipe(base / "gosu/gosu.json")
        resdet = resdet_input(base / "resdet/native-dependencies.json")
    except BuildError as exc:
        raise InputError(
            "owned sealed recipe/input validation failed: " + str(exc)
        ) from exc
    inputs = []
    for name, recipe in [("codecs", codecs), ("security", security)]:
        inputs.extend(
            (base / name / item["archive"]["filename"], item["archive"])
            for item in recipe["libraries"]
        )
    inputs.extend(
        [
            (base / "jxl" / jxl["archive"]["filename"], jxl["archive"]),
            (base / "ffmpeg/ffmpeg-8.1.3.tar.xz", ffmpeg["ffmpeg"]),
            (base / "ffmpeg/ffmpeg-8.1.3.tar.xz.asc", ffmpeg["ffmpeg"]["signature"]),
            (base / "gosu/gosu.tar.gz", gosu["gosu"]),
            (base / "gosu/go1.26.10.src.tar.gz", gosu["go_source"]),
            (base / "resdet/resdet.tar.gz", resdet),
        ]
    )
    for path, item in inputs:
        check_input(path, item)
    return {
        "status": "OWNED_COMPLETE_SOURCE_INVENTORIES_AND_SEALED_RECIPE_INPUT_HASHES_ONLY",
        "components_verified": len(records),
        "source_files_verified": sum(
            len(record["source_files"]) for record in records.values()
        ),
        "source_inputs": [
            {"path": str(path.relative_to(root)), "sha256": item["sha256"]}
            for path, item in inputs
        ],
        "full_runtime_source_acceptance": False,
        "source_patch_replay_established": False,
        "historical_compiler_linkage_established": False,
        "scope": "Complete six delivered original source inventories, reviewed existing recipe/patch/configuration/equivalence/vendor/CMS asset identities and eleven original upstream/compiler archives plus FFmpeg signature bytes. Not fresh release GPG replay, source-to-patched-tree replay, language/static/compiler closure, historical build reproduction, complete license/advisory or runtime source acceptance.",
    }
