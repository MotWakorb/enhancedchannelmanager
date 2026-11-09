"""Bind reviewed native APKv3 JSON metadata to installed first-party subjects.

This is deliberately NOT an APKv2/gzip parser or a source acceptance gate.
verify_owned_apk runs native apk verify on the original package with separately
bound public evidence, then native apk adbdump --format json on that SAME file.
bind_metadata alone does not authenticate recorded/delivered JSON. The original
installed Q1 token is compared as an opaque encoded
metadata identity; it is not mislabeled as an APKv2 compressed-control SHA1.
"""

from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath

from alpine_source_inputs import InputError, require
from alpine_source_inventory import InventoryError, parse_database
from verify_alpine_external_sources import sha256, unique

__all__ = ["InputError", "bind_installed_record", "bind_metadata", "verify_owned_apk"]


def verify_owned_apk(
    package: Path,
    public: Path,
    receipt: dict,
    installed_fields: dict,
    architecture: str,
    *,
    database_record: str | None = None,
) -> dict:
    """Native signature/payload verification then JSON decoding; no extraction.

    The caller separately binds this first-party public evidence and receipt to
    its immutable image/build subject. It is not a new global trust anchor.
    """
    require(
        package.is_file()
        and not package.is_symlink()
        and package.name == receipt["package"]["file"]
        and 0 < package.stat().st_size <= 512 * 1024**2
        and package.stat().st_size == int(installed_fields["S"])
        and sha256(package) == receipt["package"]["sha256"],
        "original owned ADB package bytes differ",
    )
    with package.open("rb") as stream:
        require(
            stream.read(4) == b"ADBd",
            "reviewed owned ADB package encoding required; APKv2 parsing forbidden",
        )
    require(
        public.is_file()
        and not public.is_symlink()
        and public.name == receipt["public_key"]["file"]
        and sha256(public) == receipt["public_key"]["sha256"]
        and {p.name for p in public.parent.glob("*.pub")} == {public.name},
        "exact separately bound owned public signing evidence required",
    )
    commands = [
        [
            "/sbin/apk",
            "verify",
            "--network=no",
            "--keys-dir",
            str(public.parent),
            str(package),
        ],
        [
            "/sbin/apk",
            "adbdump",
            "--network=no",
            "--keys-dir",
            str(public.parent),
            "--format",
            "json",
            str(package),
        ],
    ]
    outputs = []
    for command in commands:
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InputError("native owned ADB verification unavailable") from exc
        require(
            result.returncode == 0 and len(result.stdout.encode()) <= 32 * 1024**2,
            "native owned ADB signature/payload or metadata verification failed: "
            + repr(
                {
                    "command": command[1],
                    "exit": result.returncode,
                    "stderr": result.stderr,
                }
            ),
        )
        outputs.append(result.stdout)
    try:
        document = json.loads(outputs[1], object_pairs_hook=unique)
    except (ValueError, RecursionError) as exc:
        raise InputError("invalid native owned ADB JSON metadata") from exc
    if database_record is None:
        result = {
            **bind_metadata(document, installed_fields, receipt, architecture),
            "installed_payload_database_verified": False,
        }
    else:
        result = bind_installed_record(database_record, document, receipt, architecture)
        require(
            result["installed_header_fields"] == installed_fields,
            "owned ADB selected installed header/record differs",
        )
    return {
        **result,
        "signature_verification_required": False,
        "native_signature_and_payload_verified": True,
        "package_sha256": receipt["package"]["sha256"],
        "public_key_sha256": receipt["public_key"]["sha256"],
        "signature_output": outputs[0],
        "scope": "Native APK verification of this separately bound first-party package/public evidence, metadata-to-installed-subject and full artifact receipt joins only. No package installation, source/target execution, archive extraction or runtime trust-store change. Live installed-file/source/compiler/attribution/advisory and aggregate source acceptance remain separate.",
    }


def relative(name: str, *, basename: bool = False) -> None:
    require(
        isinstance(name, str) and bool(name) and all(ord(c) >= 32 for c in name),
        "invalid owned ADB payload name",
    )
    path = PurePosixPath(name)
    require(
        str(path) == name
        and path.parts
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and (not basename or len(path.parts) == 1),
        "unsafe owned ADB payload path",
    )


def acl(value: dict) -> int:
    require(
        isinstance(value, dict)
        and set(value) == {"mode", "user", "group"}
        and value["user"] == value["group"] == "root"
        and type(value["mode"]) is int
        and 0 <= value["mode"] <= 0o777,
        "unreviewed or privileged owned ADB permissions/identity",
    )
    return value["mode"]


def bind_metadata(
    document: dict, installed_fields: dict, receipt: dict, architecture: str
) -> dict:
    require(
        architecture in {"amd64", "arm64"},
        "reviewed owned native architecture required",
    )
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    require(
        set(installed_fields)
        == {"C", "P", "V", "A", "S", "I", "T", "U", "L", "o", "D", "p", "f"}
        and installed_fields["f"] == "S"
        and installed_fields["U"] == "",
        "reviewed native owned installed record/format flags required",
    )
    require(
        isinstance(document, dict) and set(document) == {"info", "paths"},
        "exact native ADB package metadata shape required",
    )
    info = document["info"]
    require(
        isinstance(info, dict)
        and set(info)
        == {
            "name",
            "version",
            "arch",
            "description",
            "hashes",
            "installed-size",
            "license",
            "origin",
            "depends",
            "provides",
        },
        "unreviewed owned ADB info fields",
    )
    require(
        receipt["status"] == "SIGNED_OWNED_APK_ONLY"
        and receipt["architecture"] == architecture
        and receipt["package"]["name"] == "ecm-owned-native",
        "reviewed owned native package receipt subject required",
    )
    require(
        info["name"]
        == info["origin"]
        == installed_fields["P"]
        == installed_fields["o"]
        == "ecm-owned-native"
        and info["version"] == installed_fields["V"] == receipt["package"]["version"]
        and info["arch"] == installed_fields["A"] == machine
        and info["description"] == installed_fields["T"]
        and info["license"] == installed_fields["L"],
        "owned ADB installed package identity differs",
    )
    require(
        type(info["installed-size"]) is int
        and info["installed-size"] == int(installed_fields["I"])
        and info["installed-size"] > 0,
        "owned ADB installed size differs",
    )
    digest = info["hashes"]
    require(
        isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{40}", digest) is not None
        and installed_fields["C"]
        == "Q1" + base64.b64encode(bytes.fromhex(digest)).decode(),
        "owned ADB installed metadata checksum differs",
    )
    for key, field, expected in [
        ("depends", "D", receipt["external_dependencies"]),
        ("provides", "p", receipt["provides"]),
    ]:
        values = info[key]
        # apk serializes the provides set in its own sorted order; the original
        # packager appends package-name provides after SONAME provides. Preserve
        # both original orderings and compare complete unique semantic sets.
        require(
            isinstance(values, list)
            and all(isinstance(v, str) and bool(v) for v in values)
            and len(values) == len(set(values))
            and len(expected) == len(set(expected))
            and set(values) == set(expected)
            and values == installed_fields[field].split(),
            "owned ADB installed dependency/provide metadata differs",
        )
    paths = document["paths"]
    require(
        isinstance(paths, list) and 0 < len(paths) <= 4096,
        "bounded complete owned ADB path set required",
    )
    files = {}
    directories = {}
    for index, entry in enumerate(paths):
        require(
            isinstance(entry, dict)
            and "acl" in entry
            and set(entry) <= {"name", "acl", "files"},
            "unreviewed owned ADB directory fields",
        )
        name = entry.get("name", "")
        if name:
            relative(name)
        else:
            require(
                index == 0 and "name" not in entry,
                "unique original unnamed ADB root directory required",
            )
        require(name not in directories, "duplicate owned ADB directory")
        directories[name] = {
            "path": name,
            "mode": acl(entry["acl"]),
            "uid": 0,
            "gid": 0,
        }
        children = entry.get("files", [])
        require(
            isinstance(children, list) and len(children) <= 10000,
            "bounded owned ADB directory file list required",
        )
        for child in children:
            require(
                isinstance(child, dict)
                and {"name", "acl", "size", "mtime"} <= set(child)
                and set(child) <= {"name", "acl", "size", "mtime", "hash", "target"}
                and ("hash" in child) != ("target" in child),
                "unreviewed owned ADB file fields/type",
            )
            relative(child["name"], basename=True)
            path = str(PurePosixPath(name) / child["name"])
            require(path not in files, "duplicate owned ADB payload file")
            require(
                type(child["size"]) is int
                and 0 <= child["size"] <= 512 * 1024**2
                and type(child["mtime"]) is int
                and 0 <= child["mtime"] < 2**63,
                "invalid owned ADB file size/time",
            )
            mode = acl(child["acl"])
            if "hash" in child:
                require(
                    isinstance(child["hash"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", child["hash"]) is not None,
                    "original full owned ADB file SHA256 required",
                )
                row = {
                    "path": path,
                    "type": "file",
                    "sha256": child["hash"],
                    "size": child["size"],
                    "mode": mode,
                }
            else:
                target = child["target"]
                require(
                    isinstance(target, str)
                    and re.fullmatch(r"(?:[0-9a-f]{2})+", target) is not None,
                    "canonical owned ADB target bytes required",
                )
                raw = bytes.fromhex(target)
                require(
                    raw[:2] == b"\x00\xa0"
                    and len(raw) > 2
                    and child["size"] == len(raw) - 2
                    and mode == 0o777,
                    "only reviewed owned ADB symlink target encoding supported",
                )
                try:
                    link = raw[2:].decode()
                except UnicodeError as exc:
                    raise InputError("invalid owned ADB symlink text") from exc
                require(
                    link
                    and not PurePosixPath(link).is_absolute()
                    and "\\" not in link
                    and all(ord(c) >= 32 for c in link),
                    "unsafe owned ADB symlink target",
                )
                resolved = posixpath.normpath(str(PurePosixPath(name) / link))
                relative(resolved)
                row = {
                    "path": path,
                    "type": "symlink",
                    "target": link,
                    "resolved": resolved,
                }
            files[path] = row
    require(
        files and not set(files) & set(directories),
        "empty or colliding owned ADB payload inventory",
    )
    for name, row in files.items():
        if row["type"] != "symlink":
            continue
        seen = {name}
        target = row["resolved"]
        while True:
            require(
                target not in seen and target in files,
                "unresolved or cyclic owned ADB symlink",
            )
            seen.add(target)
            referent = files[target]
            if referent["type"] == "file":
                row["target_sha256"] = referent["sha256"]
                break
            target = referent["resolved"]
    # Match owned_artifacts' Path-component ordering, not string '/' ordering.
    artifacts = [
        {k: v for k, v in files[name].items() if k != "resolved"}
        for name in sorted(files, key=PurePosixPath)
    ]
    require(
        artifacts == receipt["installed_artifacts"],
        "complete owned ADB payload differs from sealed package artifact inventory",
    )
    return {
        "status": "OWNED_ADB_INSTALLED_METADATA_AND_ARTIFACT_JOIN_SIGNATURE_REQUIRED",
        "architecture": architecture,
        "metadata_checksum": installed_fields["C"],
        "installed_artifacts": artifacts,
        "directory_metadata": [directories[name] for name in sorted(directories)],
        "regular_files": sum(r["type"] == "file" for r in artifacts),
        "symlinks": sum(r["type"] == "symlink" for r in artifacts),
        "directories": len(directories),
        "signature_verification_required": True,
        "full_runtime_source_acceptance": False,
        "scope": "Native original APKv3 JSON metadata/installed subject/full artifact inventory correspondence only. Caller must independently bind original APK/public evidence/signatures and verify live installed bytes/metadata and delivered source graphs. JSON receipt parsing is not authentication or source/advisory/runtime clearance.",
    }


def bind_installed_record(
    text: str, document: dict, receipt: dict, architecture: str
) -> dict:
    """Check every actual DB payload path/attribute/checksum against native ADB.

    The reviewed native package's f:S database encoding uses SHA256-160 inside
    legacy Q1 tokens. Full file SHA256 and package integrity remain enforced by
    the signed ADB/native verifier; this compact DB correspondence is not an
    independent cryptographic authentication claim or an APKv2 checksum rule.
    """
    require(
        architecture in {"amd64", "arm64"},
        "reviewed owned database architecture required",
    )
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    try:
        parsed = parse_database(text, machine)
    except InventoryError as exc:
        raise InputError(
            "invalid owned installed database record: " + str(exc)
        ) from exc
    require(
        len(parsed) == 1 and parsed[0]["name"] == "ecm-owned-native",
        "unique complete owned native installed record required",
    )
    fields = parsed[0]["fields"]
    bound = bind_metadata(document, fields, receipt, architecture)
    signed_directories = {
        entry["name"]: entry for entry in document["paths"] if entry.get("name")
    }
    signed_files = {
        str(PurePosixPath(entry.get("name", "")) / child["name"]): child
        for entry in document["paths"]
        for child in entry.get("files", [])
    }
    directories = {}
    files = {}
    directory = None
    filename = None
    directory_attributes = set()
    for line in text.splitlines():
        key, value = line[0], line[2:]
        if key == "F":
            directory = value
            filename = None
            directories[directory] = "0:0:755"
        elif directory is not None:
            if key == "R":
                filename = str(PurePosixPath(directory) / value)
                files[filename] = {"a": "0:0:644"}
            elif key == "M":
                require(
                    directory not in directory_attributes,
                    "duplicate owned installed directory attributes",
                )
                directory_attributes.add(directory)
                directories[directory] = value
            else:
                files[filename][key] = value
    require(
        set(files) == set(signed_files) and set(directories) == set(signed_directories),
        "complete owned installed payload path/directory set differs from signed ADB",
    )
    for name, attributes in files.items():
        signed = signed_files[name]
        require(
            set(attributes) == {"a", "Z"}
            and attributes["a"] == f"0:0:{signed['acl']['mode']:o}",
            "owned installed payload permissions/checksum missing or different",
        )
        digest = (
            signed["hash"][:40]
            if "hash" in signed
            else hashlib.sha256(bytes.fromhex(signed["target"])[2:]).hexdigest()[:40]
        )
        require(
            attributes["Z"] == "Q1" + base64.b64encode(bytes.fromhex(digest)).decode(),
            "owned installed payload checksum differs from signed ADB",
        )
    for name, attributes in directories.items():
        require(
            attributes == f"0:0:{signed_directories[name]['acl']['mode']:o}",
            "owned installed directory permissions differ from signed ADB",
        )
    return {
        **bound,
        "installed_payload_database_verified": True,
        "installed_header_fields": fields,
        "database_record_sha256": hashlib.sha256(text.strip("\n").encode()).hexdigest(),
        "database_files_verified": len(files),
        "database_directories_verified": len(directories),
    }
