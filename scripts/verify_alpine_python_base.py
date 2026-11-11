"""Offline pinned Python OCI/source-to-installed-file correspondence, not a rebuild.

No downloads, source execution, extraction, package installation or trust changes.
Every original /usr/local base entry is checked; runtime additions are reported
for the aggregate gate rather than silently accepted as Python-base artifacts.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import posixpath
import re
import stat
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from alpine_source_inputs import InputError, require
from verify_alpine_external_sources import path_in, read_json, sha256

__all__ = [
    "InputError",
    "base_files",
    "installed_files",
    "layer_files",
    "metadata",
    "source_members",
    "verify",
]

BASE_INDEX = "e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7"
PYTHON_VERSION = "3.12.16"
SOURCE_SHA256 = "b5796915dc45ff745cfb4e990a7e7c93d6f546328a102ce31448d69ebc37575c"
SOURCE_COMMIT = "3d7e95597e56a415d9e4abc0f15877e005d2cb2e"
SOURCE_URL = (
    "https://github.com/docker-library/python.git#" + SOURCE_COMMIT + ":3.12/alpine3.24"
)
RECIPE_SHA256 = "a30357f54a3531398414358c869b8bb07ae5aa95f488c20c8481e00238057415"
RELEASE_KEY = "7169605F62C751356D054A26A821E680E5FA6305"
SIGNATURE_SHA256 = "abf484364233972765d25d5981ecffc76287317a065761fbe10fca21bcc0a197"
PUBLIC_KEY_SHA256 = "1de2bbd31e2dd10aab4098c3ab2f937c530d924ddbfa2dd091f0c7cfb2ee182a"


def signature_evidence(root: Path, used: set[str]) -> None:
    # The builder independently runs GPG. The read-only runtime consumer binds
    # those exact original public evidence bytes and the approved archive hash;
    # it does not claim to rerun GPG or add a runtime signing trust store.
    for name, digest in [
        ("source/Python-" + PYTHON_VERSION + ".tar.xz.asc", SIGNATURE_SHA256),
        ("source/python-release-manager.asc", PUBLIC_KEY_SHA256),
    ]:
        require(
            sha256(path_in(root, name)) == digest,
            "original Python release signature evidence differs",
        )
        used.add(name)


def blob(root: Path, descriptor: dict, used: set[str]) -> Path:
    digest = descriptor.get("digest", "")
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None,
        "invalid Python OCI blob digest",
    )
    name = "blobs/" + digest[7:]
    path = path_in(root, name)
    require(
        type(descriptor.get("size")) is int
        and path.stat().st_size == descriptor["size"]
        and sha256(path) == digest[7:],
        "Python OCI blob size/hash mismatch",
    )
    used.add(name)
    return path


def metadata(root: Path, architecture: str) -> dict:
    require(architecture in {"amd64", "arm64"}, "unsupported Python OCI architecture")
    index_path = path_in(root, "index.json")
    require(sha256(index_path) == BASE_INDEX, "pinned Python OCI index mismatch")
    index = read_json(index_path)
    descriptors = [
        r
        for r in index["manifests"]
        if r.get("platform", {}).get("architecture") == architecture
        and r["platform"].get("os") == "linux"
    ]
    require(len(descriptors) == 1, "unique pinned Python OCI platform required")
    descriptor = descriptors[0]
    require(
        descriptor["annotations"]["org.opencontainers.image.source"] == SOURCE_URL,
        "original Python source recipe differs",
    )
    used = {"index.json"}
    manifest = read_json(blob(root, descriptor, used))
    config = read_json(blob(root, manifest["config"], used))
    require(
        config["architecture"] == architecture
        and config["os"] == "linux"
        and config["rootfs"]["type"] == "layers",
        "Python base config platform mismatch",
    )
    require(
        len(manifest["layers"]) == len(config["rootfs"]["diff_ids"]) > 0,
        "Python base config layer-count mismatch",
    )
    env = dict(row.split("=", 1) for row in config["config"]["Env"])
    require(
        env["PYTHON_VERSION"] == PYTHON_VERSION
        and env["PYTHON_SHA256"] == SOURCE_SHA256
        and env["GPG_KEY"] == RELEASE_KEY,
        "Python base source environment differs",
    )
    return {
        "index": index,
        "manifest": manifest,
        "manifest_digest": descriptor["digest"],
        "config": config,
        "used": used,
        "python_version": PYTHON_VERSION,
        "source_commit": SOURCE_COMMIT,
    }


def layer_files(path: Path, descriptor: dict, diff_id: str) -> list[dict]:
    require(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size == descriptor["size"]
        and "sha256:" + sha256(path) == descriptor["digest"],
        "Python compressed layer hash/size mismatch",
    )
    require(
        descriptor["mediaType"] == "application/vnd.oci.image.layer.v1.tar+gzip",
        "unsupported Python base layer encoding",
    )
    with gzip.open(path, "rb") as stream:
        require(
            "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() == diff_id,
            "Python uncompressed layer diff ID mismatch",
        )
    rows = []
    names = set()
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = member.name.removeprefix("./").rstrip("/")
            pure = PurePosixPath(name)
            require(
                bool(name)
                and str(pure) == name
                and not pure.is_absolute()
                and ".." not in pure.parts
                and all(ord(c) >= 32 for c in name)
                and "\\" not in name,
                "unsafe Python layer member path",
            )
            require(name not in names, "duplicate Python layer member")
            names.add(name)
            require(
                not any(p.startswith(".wh.") for p in pure.parts),
                "unsupported Python layer whiteout semantics",
            )
            if not name.startswith("usr/local/"):
                continue
            require(
                member.isreg() or member.isdir() or member.issym(),
                "unsupported Python layer member type",
            )
            row = {
                "path": name,
                "mode": member.mode,
                "uid": member.uid,
                "gid": member.gid,
            }
            if member.isreg():
                require(0 <= member.size <= 128 * 1024**2, "oversized Python base file")
                stream = archive.extractfile(member)
                require(stream is not None, "unreadable Python base file")
                row.update(
                    type="file",
                    size=member.size,
                    sha256=hashlib.file_digest(stream, "sha256").hexdigest(),
                )
            elif member.issym():
                target = posixpath.normpath(str(pure.parent / member.linkname))
                require(
                    not PurePosixPath(member.linkname).is_absolute()
                    and target.startswith("usr/local/"),
                    "escaping Python base symlink",
                )
                row.update(type="symlink", linkname=member.linkname)
            else:
                row["type"] = "directory"
            rows.append(row)
    return rows


def base_files(root: Path, meta: dict) -> list[dict]:
    entries = {}
    for descriptor, diff_id in zip(
        meta["manifest"]["layers"], meta["config"]["rootfs"]["diff_ids"], strict=True
    ):
        path = blob(root, descriptor, meta["used"])
        for row in layer_files(path, descriptor, diff_id):
            entries[row["path"]] = row
    require(bool(entries), "empty Python base file set")
    return [entries[name] for name in sorted(entries)]


def installed_files(root: Path, rows: list[dict]) -> dict:
    require(
        root.is_dir() and not root.is_symlink(),
        "regular installed-root directory required",
    )
    expected = set()
    count = 0
    for row in rows:
        pure = PurePosixPath(row["path"])
        require(
            str(pure) == row["path"]
            and row["path"].startswith("usr/local/")
            and ".." not in pure.parts
            and row["path"] not in expected,
            "unsafe/duplicate Python base installed path",
        )
        expected.add(row["path"])
        require(
            all(not (root / parent).is_symlink() for parent in pure.parents),
            "Python installed parent-directory symlink",
        )
        path = root / pure
        require(
            path.exists() or path.is_symlink(),
            "missing original Python base file: " + row["path"],
        )
        info = path.lstat()
        require(
            (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
            == (row["mode"], row["uid"], row["gid"]),
            "Python installed ownership/mode mismatch: " + row["path"],
        )
        if row["type"] == "file":
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_size == row["size"]
                and sha256(path) == row["sha256"],
                "Python installed file hash/type mismatch: " + row["path"],
            )
            count += 1
        elif row["type"] == "symlink":
            require(
                stat.S_ISLNK(info.st_mode)
                and path.readlink().as_posix() == row["linkname"],
                "Python installed symlink mismatch: " + row["path"],
            )
            count += 1
        else:
            require(
                row["type"] == "directory" and stat.S_ISDIR(info.st_mode),
                "Python installed directory type mismatch",
            )
    additional = []
    directories = []
    for path in (root / "usr/local").rglob("*"):
        name = path.relative_to(root).as_posix()
        if name not in expected and (path.is_symlink() or not path.is_dir()):
            additional.append(name)
        elif name not in expected:
            directories.append(name)
    return {
        "verified_base_files": count,
        "additional_paths": sorted(additional),
        "additional_directories": sorted(directories),
        "full_runtime_source_acceptance": False,
    }


def source_members(path: Path) -> dict:
    require(
        path.is_file() and not path.is_symlink() and sha256(path) == SOURCE_SHA256,
        "exact pinned signed-release CPython source archive required",
    )
    members = defaultdict(list)
    pip_members = defaultdict(list)
    seen = set()
    with tarfile.open(path, "r:xz") as archive:
        for member in archive:
            name = member.name
            require(name not in seen, "duplicate original CPython source member")
            seen.add(name)
            if not member.isreg():
                continue
            stream = archive.extractfile(member)
            require(
                stream is not None and member.size <= 128 * 1024**2,
                "bounded regular CPython source member required",
            )
            data = stream.read()
            members[hashlib.sha256(data).hexdigest()].append(name)
            if (
                name
                == "Python-"
                + PYTHON_VERSION
                + "/Lib/ensurepip/_bundled/pip-25.0.1-py3-none-any.whl"
            ):
                with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                    require(
                        len(wheel.namelist()) == len(set(wheel.namelist())),
                        "duplicate original ensurepip wheel member",
                    )
                    for entry in wheel.infolist():
                        if not entry.is_dir():
                            pip_members[
                                hashlib.sha256(wheel.read(entry)).hexdigest()
                            ].append(entry.filename)
    require(
        bool(members) and bool(pip_members),
        "complete CPython and bundled ensurepip sources required",
    )
    return {"cpython": dict(members), "ensurepip": dict(pip_members)}


def attestations(root: Path, meta: dict, recipe: bytes) -> None:
    descriptors = [
        r
        for r in meta["index"]["manifests"]
        if r.get("annotations", {}).get("vnd.docker.reference.digest")
        == meta["manifest_digest"]
    ]
    require(len(descriptors) == 1, "unique digest-bound Python attestation required")
    manifest = read_json(blob(root, descriptors[0], meta["used"]))
    blob(root, manifest["config"], meta["used"])
    predicates = set()
    for descriptor in manifest["layers"]:
        payload = read_json(blob(root, descriptor, meta["used"]))
        require(
            payload["subject"]
            and all(
                s["digest"]["sha256"] == meta["manifest_digest"][7:]
                for s in payload["subject"]
            ),
            "Python provenance/SBOM subject substitution",
        )
        kind = payload["predicateType"]
        require(kind not in predicates, "duplicate Python provenance predicate")
        predicates.add(kind)
        if kind == "https://slsa.dev/provenance/v0.2":
            infos = payload["predicate"]["metadata"][
                "https://mobyproject.org/buildkit@v1#metadata"
            ]["source"]["infos"]
            require(
                len(infos) == 1
                and infos[0]["filename"] == "Dockerfile"
                and base64.b64decode(infos[0]["data"], validate=True) == recipe,
                "original Python recipe differs from pinned OCI provenance",
            )
    require(
        predicates == {"https://slsa.dev/provenance/v0.2", "https://spdx.dev/Document"},
        "complete original Python SBOM/provenance required",
    )


def verify(root: Path, architecture: str, installed_root: Path | None = None) -> dict:
    meta = metadata(root, architecture)
    recipe_path = path_in(root, "recipe/Dockerfile")
    require(
        sha256(recipe_path) == RECIPE_SHA256,
        "approved original Python Docker recipe differs",
    )
    recipe = recipe_path.read_bytes()
    meta["used"].add("recipe/Dockerfile")
    license_path = path_in(root, "recipe/LICENSE")
    require(
        sha256(license_path)
        == "0a3ffdfc4f368b76ae1140b5fbe2f2f69ee76ee00a3024e8a09ca9b2e265186f",
        "original Docker recipe license differs",
    )
    meta["used"].add("recipe/LICENSE")
    attestations(root, meta, recipe)
    signature_evidence(root, meta["used"])
    rows = base_files(root, meta)
    source_path = path_in(root, "source/Python-" + PYTHON_VERSION + ".tar.xz")
    sources = source_members(source_path)
    meta["used"].add(source_path.relative_to(root).as_posix())
    counts = Counter()
    correspondence = []
    for row in rows:
        if row["type"] != "file":
            continue
        digest = row["sha256"]
        if digest in sources["cpython"]:
            kind = "original-cpython-bytes"
            members = sources["cpython"][digest]
        elif digest in sources["ensurepip"]:
            kind = "original-bundled-ensurepip-bytes"
            members = sources["ensurepip"][digest]
        else:
            kind = "pinned-oci-recipe-output-not-reproduced"
            members = []
        counts[kind] += 1
        correspondence.append(
            {
                "path": row["path"],
                "sha256": digest,
                "source_kind": kind,
                "source_members": members,
            }
        )
    actual = set()
    for path in root.rglob("*"):
        require(
            not path.is_symlink() and (path.is_file() or path.is_dir()),
            "unsafe Python source delivery entry",
        )
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(actual == meta["used"], "missing or unindexed Python source delivery file")
    installed = (
        installed_files(installed_root, rows) if installed_root is not None else None
    )
    return {
        "status": "PINNED_PYTHON_BASE_SOURCE_AND_FILE_CORRESPONDENCE_ONLY",
        "full_runtime_source_acceptance": False,
        "architecture": architecture,
        "base_index_sha256": BASE_INDEX,
        "manifest_digest": meta["manifest_digest"],
        "python_version": PYTHON_VERSION,
        "source_sha256": SOURCE_SHA256,
        "source_commit": SOURCE_COMMIT,
        "recipe_sha256": RECIPE_SHA256,
        "base_entry_count": len(rows),
        "source_correspondence_counts": dict(counts),
        "source_correspondence": correspondence,
        "installed": installed,
        "delivered_files": len(actual),
        "scope": "Original digest-bound OCI layers/config/provenance/SBOM and complete source archive plus exact original /usr/local installed-file checks when an installed root is supplied. Generated/compiled recipe outputs are identified, not claimed as reproduced or directly source-byte identical. Runtime additions remain for the aggregate gate. No compiler/nested support, complete attribution/advisory, full runtime source or MCP acceptance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--installed-root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(args.source_root, args.architecture, args.installed_root),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
