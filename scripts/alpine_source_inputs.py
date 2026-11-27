"""Read-only exact APK-control, Git-tree and source-input joins.

No signature verification, shell recipe execution, extraction, installation or
acceptance gate lives here. Callers must independently authenticate the APK and
index, bind the commit to its actual Git tree, and deliver every source subject.
"""

from __future__ import annotations

import hashlib
import re
import tarfile
from pathlib import Path, PurePosixPath

from alpine_apk_v2 import parts
from verify_musllinux_gnu import AttributionError, apk_payload, archive_member

__all__ = [
    "AttributionError",
    "InputError",
    "bind_apk_control",
    "bind_git_recipe",
    "checksum_rows",
    "recipe_tree",
    "verify_source_inputs",
]

# Entirely recipe-generated release/configuration payloads, not a name-based
# missing-source exception. These exact original recipes were inspected in full.
GENERATED_RECIPES = frozenset(
    {
        "dd9dbeb82fe510aadd75305f9999dc7c0fc81c4b4476db0bbb73da3e167b3b05",
        "7962206597c478b726f46b0392a73bec21bfa70afa56c62ccb043d1ade3d6286",
    }
)


class InputError(ValueError):
    """An input does not match its exact authenticated subject or recipe graph."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InputError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bind_apk_control(path: Path, index: dict, repository_architecture: str) -> dict:
    require(
        repository_architecture in {"x86_64", "aarch64"}
        and index["A"] == repository_architecture,
        "unexpected repository architecture",
    )
    streams = parts(path)
    text = archive_member(streams[1], ".PKGINFO").decode()
    fields = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        require(" = " in line, "malformed APK control field")
        key, value = line.split(" = ", 1)
        fields.setdefault(key, []).append(value)
    control_architecture = fields.get("arch")
    require(
        control_architecture in [[repository_architecture], ["noarch"]],
        "unexpected signed-control architecture",
    )
    # abuild's repository index rewrites noarch to the repository architecture.
    # Preserve both signed representations; do not alter either input map.
    expected = {**index, "A": control_architecture[0]}
    apk_payload(path.read_bytes(), expected)
    require(fields.get("license") == [index["L"]], "APK control/index license differs")
    return {
        "status": "CONTROL_AND_PAYLOAD_BINDING_SIGNATURE_REQUIRED",
        "sha256": sha256(path),
        "repository_architecture": repository_architecture,
        "control_architecture": control_architecture[0],
        "origin": index["o"],
        "commit": index["c"],
        "index_record": dict(index),
        "control_fields": fields,
        "scope": "Compressed-control checksum, complete payload datahash and exact metadata joins only. Cryptographic signature verification is separately required.",
    }


def checksum_rows(text: str) -> dict[str, str]:
    blocks = re.findall(
        r"""(?m)^sha512sums=(["'])(.*?)\1[ \t]*(?:\n|$)""", text, re.DOTALL
    )
    if not blocks:
        require(
            hashlib.sha256(text.encode()).hexdigest() in GENERATED_RECIPES,
            "unreviewed generated/missing source-checksum recipe",
        )
        return {}
    require(len(blocks) == 1, "unique complete literal source checksum block required")
    rows = {}
    for line in blocks[0][1].strip().splitlines():
        match = re.fullmatch(r"([0-9a-f]{128})  ([A-Za-z0-9_@.+~-]+)", line)
        require(match is not None, "nonliteral, skipped or unsafe source checksum")
        digest, name = match.groups()
        require(
            name not in {".", ".."} and name not in rows,
            "duplicate/unsafe source checksum name",
        )
        rows[name] = digest
    require(bool(rows), "empty source checksum block")
    return rows


def git_object(kind: str, data: bytes) -> bytes:
    return hashlib.sha1(
        kind.encode() + b" " + str(len(data)).encode() + b"\0" + data
    ).digest()


def bind_git_recipe(
    commit_id: str,
    commit: bytes,
    trees: dict[str, bytes],
    recipe_path: str,
    *,
    origin: str,
) -> str:
    """Bind raw commit/tree objects to main/community/origin without Git execution."""
    require(
        re.fullmatch(r"[0-9a-f]{40}", commit_id) is not None
        and 0 < len(commit) <= 1024**2,
        "invalid source commit subject",
    )
    require(
        git_object("commit", commit).hex() == commit_id,
        "original Git commit object mismatch",
    )
    header = commit.split(b"\n", 1)[0]
    require(
        re.fullmatch(rb"tree [0-9a-f]{40}", header) is not None,
        "invalid original commit root tree",
    )
    path = PurePosixPath(recipe_path)
    require(
        len(path.parts) == 2
        and str(path) == recipe_path
        and path.parts[0] in {"main", "community"}
        and re.fullmatch(r"[A-Za-z0-9_+.~-]+", path.parts[1]) is not None
        and path.parts[1] not in {".", ".."},
        "unexpected stable recipe path",
    )
    require(
        path.parts[1] == origin, "recipe path differs from authenticated package origin"
    )
    tree_id = header[5:].decode()
    for part in path.parts:
        require(tree_id in trees, "missing original Git path tree")
        body = trees[tree_id]
        require(
            0 < len(body) <= 32 * 1024**2 and git_object("tree", body).hex() == tree_id,
            "original Git path tree mismatch",
        )
        entries = {}
        offset = 0
        while offset < len(body):
            end = body.find(b"\0", offset)
            require(
                end > offset and end + 21 <= len(body), "truncated original Git tree"
            )
            head = body[offset:end].split(b" ", 1)
            require(
                len(head) == 2
                and head[0] in {b"40000", b"100644", b"100755", b"120000"}
                and head[1]
                and b"/" not in head[1]
                and all(c >= 32 for c in head[1]),
                "invalid original Git tree entry",
            )
            name = head[1].decode()
            require(name not in entries, "duplicate original Git tree name")
            entries[name] = (head[0], body[end + 1 : end + 21].hex())
            offset = end + 21
        require(
            part in entries and entries[part][0] == b"40000",
            "missing original Git recipe directory",
        )
        tree_id = entries[part][1]
    return tree_id


def git_tree(entries: dict[str, dict]) -> str:
    root = {}
    for name, entry in entries.items():
        parent = root
        path = PurePosixPath(name)
        for part in path.parts[:-1]:
            require(
                part not in parent or isinstance(parent[part], dict),
                "Git file/directory collision",
            )
            parent = parent.setdefault(part, {})
        require(path.name not in parent, "duplicate Git tree path")
        parent[path.name] = (entry["git_mode"], git_object("blob", entry["bytes"]))

    def digest(directory):
        body = bytearray()
        for name, node in sorted(
            directory.items(),
            key=lambda pair: (
                pair[0].encode() + (b"/" if isinstance(pair[1], dict) else b"")
            ),
        ):
            mode, object_id = (
                ("40000", digest(node)) if isinstance(node, dict) else node
            )
            body.extend(mode.encode() + b" " + name.encode() + b"\0" + object_id)
        return git_object("tree", bytes(body))

    return digest(root).hex()


def recipe_tree(
    archive_path: Path, expected_tree: str, expected_recipe_sha256: str
) -> dict:
    require(
        re.fullmatch(r"[0-9a-f]{40}", expected_tree) is not None,
        "invalid Git tree subject",
    )
    require(
        archive_path.is_file()
        and not archive_path.is_symlink()
        and archive_path.stat().st_size <= 64 * 1024**2,
        "regular bounded recipe archive required",
    )
    entries = {}
    names = set()
    with tarfile.open(archive_path) as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            path = PurePosixPath(name)
            require(
                bool(name)
                and str(path) == name
                and not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in name
                and all(ord(c) >= 32 for c in name),
                "unsafe recipe archive path",
            )
            require(name not in names, "duplicate recipe archive path")
            names.add(name)
            require(
                member.isreg() or member.issym() or member.isdir(),
                "unsafe recipe archive member type",
            )
            if member.isdir():
                continue
            require(not member.mode & 0o7000, "privileged recipe archive mode")
            if member.issym():
                target = PurePosixPath(member.linkname)
                require(
                    bool(member.linkname)
                    and not target.is_absolute()
                    and ".." not in target.parts,
                    "unsafe recipe source symlink",
                )
                data = member.linkname.encode()
                mode = "120000"
                kind = "symlink"
            else:
                require(0 <= member.size <= 32 * 1024**2, "oversized recipe file")
                stream = archive.extractfile(member)
                require(stream is not None, "unreadable recipe archive file")
                data = stream.read()
                mode = "100755" if member.mode & 0o111 else "100644"
                kind = "file"
            entries[name] = {
                "bytes": data,
                "git_mode": mode,
                "type": kind,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
    require(
        bool(entries) and git_tree(entries) == expected_tree,
        "complete original Git tree mismatch",
    )
    recipe = entries.get("APKBUILD")
    require(
        recipe is not None
        and recipe["type"] == "file"
        and recipe["sha256"] == expected_recipe_sha256,
        "original APKBUILD bytes differ",
    )
    checksums = checksum_rows(recipe["bytes"].decode())
    return {
        "git_tree": expected_tree,
        "archive_sha256": sha256(archive_path),
        "recipe_sha256": expected_recipe_sha256,
        "entries": entries,
        "checksums": checksums,
    }


def verify_source_inputs(tree: dict, external_sources: dict[str, Path]) -> dict:
    entries = tree["entries"]
    checksums = tree["checksums"]
    missing = {name for name in checksums if name not in entries}
    require(
        set(external_sources) == missing,
        "complete exact external source input set required",
    )
    records = []
    for name, expected in checksums.items():
        if name in entries:
            entry = entries[name]
            require(
                entry["type"] == "file",
                "source checksum cannot bind an archive symlink",
            )
            data = entry["bytes"]
            actual = hashlib.sha512(data).hexdigest()
            digest = entry["sha256"]
            size = len(data)
            kind = "aports-tree-file"
        else:
            source = external_sources[name]
            require(
                source.is_file() and not source.is_symlink(),
                "regular original external source required",
            )
            with source.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha512").hexdigest()
            digest = sha256(source)
            size = source.stat().st_size
            kind = "external-source-file"
        require(actual == expected, "complete source input SHA512 mismatch: " + name)
        records.append(
            {
                "filename": name,
                "kind": kind,
                "sha512": actual,
                "sha256": digest,
                "size": size,
            }
        )
    return {
        "status": "RECIPE_TREE_AND_INPUT_CHECKSUMS_ONLY",
        "git_tree": tree["git_tree"],
        "archive_sha256": tree["archive_sha256"],
        "recipe_sha256": tree["recipe_sha256"],
        "generated_payload_recipe": tree["recipe_sha256"] in GENERATED_RECIPES,
        "recipe_files": [
            {key: value for key, value in row.items() if key != "bytes"}
            | {"path": name}
            for name, row in sorted(entries.items())
        ],
        "source_input_count": len(checksums),
        "external_input_count": len(missing),
        "source_inputs": records,
        "scope": "Exact complete original Git recipe tree and all literal checksummed source inputs only; callers must authenticate the commit binding and package subjects. Not rebuild, license, runtime-source delivery or advisory clearance.",
    }
