"""APK v2 payload inspection only; callers must separately verify signatures."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zlib
from pathlib import Path, PurePosixPath

from verify_musllinux_gnu import AttributionError, require

__all__ = ["AttributionError", "inspect_apk", "parts"]


def parts(path: Path) -> list[bytes]:
    require(path.is_file() and not path.is_symlink(), "regular APK required")
    remaining, streams = path.read_bytes(), []
    while remaining:
        decoder = zlib.decompressobj(31)
        decoder.decompress(remaining)
        require(decoder.eof, "incomplete APK gzip stream")
        size = len(remaining) - len(decoder.unused_data)
        require(size > 0, "empty APK gzip stream")
        streams.append(remaining[:size])
        remaining = decoder.unused_data
    require(len(streams) == 3, "three APK v2 gzip streams required")
    return streams


def inspect_apk(path: Path) -> dict:
    streams = parts(path)
    with tarfile.open(fileobj=io.BytesIO(streams[1]), mode="r:gz") as archive:
        members = archive.getmembers()
        info = [member for member in members if member.name == ".PKGINFO"]
        require(
            len(info) == 1 and info[0].isreg() and 0 < info[0].size < 1024 * 1024,
            "unique regular APK metadata required",
        )
        source = archive.extractfile(info[0])
        require(source is not None, "APK metadata unreadable")
        text = source.read().decode()
        scripts = []
        for member in members:
            if member.name == ".PKGINFO":
                continue
            require(
                member.isreg()
                and member.name.startswith(".")
                and len(PurePosixPath(member.name).parts) == 1,
                "unsafe APK control entry",
            )
            script = archive.extractfile(member)
            require(script is not None, "APK control script unreadable")
            content = script.read()
            scripts.append(
                {
                    "path": member.name,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mode": member.mode,
                    "uid": member.uid,
                    "gid": member.gid,
                }
            )
        require(
            len({row["path"] for row in scripts}) == len(scripts),
            "duplicate APK control script",
        )
    fields = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            require(" = " in line, "invalid APK metadata field")
            key, value = line.split(" = ", 1)
            fields.setdefault(key, []).append(value)
    require(
        fields.get("datahash") == [hashlib.sha256(streams[2]).hexdigest()],
        "APK signed-control datahash mismatch",
    )
    inventory = []
    names = set()
    with tarfile.open(fileobj=io.BytesIO(streams[2]), mode="r:gz") as archive:
        for member in archive:
            name = member.name.removeprefix("./").rstrip("/")
            if name in {"", "."} and member.isdir():
                continue
            relative = PurePosixPath(name)
            require(
                name
                and not relative.is_absolute()
                and ".." not in relative.parts
                and str(relative) == name
                and name not in names,
                "unsafe or duplicate APK payload path",
            )
            require(
                member.isreg() or member.isdir() or member.issym(),
                "unsafe APK payload entry type",
            )
            require(not member.mode & 0o7000, "privileged APK runtime mode forbidden")
            names.add(name)
            row = {
                "path": name,
                "mode": member.mode,
                "uid": member.uid,
                "gid": member.gid,
            }
            if member.isreg():
                source = archive.extractfile(member)
                require(source is not None, "APK payload unreadable")
                data = source.read()
                row.update(
                    type="file", size=len(data), sha256=hashlib.sha256(data).hexdigest()
                )
            elif member.isdir():
                row.update(type="directory")
            else:
                target = PurePosixPath(member.linkname)
                require(".." not in target.parts, "unsafe APK payload symlink")
                row.update(type="symlink", target=member.linkname)
            inventory.append(row)
    return {
        "scope": "PAYLOAD_BINDING_ONLY_SIGNATURE_VERIFICATION_REQUIRED",
        "fields": fields,
        "payload": sorted(inventory, key=lambda row: row["path"]),
        "control_scripts": sorted(scripts, key=lambda row: row["path"]),
    }
