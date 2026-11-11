#!/usr/bin/env python3
"""Capture the reviewed Python locations and gosu from a trusted read-only image.

No scanner input, caller package list, runtime imports, or executable invocation.
This is a bounded physical catalogue, NOT arbitrary-image language discovery,
signed provenance, source-fix proof or production acceptance. The job must resolve
and authenticate the immutable image identity outside its untrusted artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from email.parser import BytesParser
from pathlib import Path

from alpine_source_inputs import InputError, require
from alpine_source_inventory import canonical_sha256
from verify_alpine_external_sources import unique

PYTHON_ROOTS = (
    "opt/venv/lib/python3.12/site-packages",
    "usr/local/lib/python3.12/site-packages",
    "usr/share/doc/ecm/python-third-party/wheels",
)
GOSU = "usr/local/bin/gosu"
GOSU_SOURCE = "usr/share/ecm/sources/owned/gosu"
GOSU_RECEIPT = "usr/share/ecm/provenance/owned/gosu.json"
SCHEMA = "SCOPED_PHYSICAL_PYTHON_GOSU_V1"
MAX_FILES = 100_000
MAX_BYTES = 1024**3


def json_file(path: Path) -> dict:
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def physical_file(path: Path, root: Path) -> tuple[bytes, dict]:
    """Read canonical regular bytes once, detecting inode replacement/read races."""
    require(
        path.absolute() == path.resolve(),
        "canonical physical path required: " + str(path),
    )
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= 128 * 1024**2,
            "bounded physical regular file required",
        )
        data = stream.read(128 * 1024**2 + 1)
        after = os.fstat(stream.fileno())

    def stable(st):
        return (
            st.st_dev,
            st.st_ino,
            st.st_mode,
            st.st_size,
            st.st_mtime_ns,
            st.st_ctime_ns,
        )

    require(
        stable(before) == stable(after) == stable(path.stat())
        and len(data) == before.st_size,
        "physical file changed while capturing",
    )
    return data, {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "device": before.st_dev,
        "inode": before.st_ino,
        "mode": stat.S_IMODE(before.st_mode),
        "mtime_ns": before.st_mtime_ns,
        "ctime_ns": before.st_ctime_ns,
    }


def tree_witness(
    root: Path,
    directory: str,
    *,
    source_links: bool = False,
    installed_metadata: bool = False,
) -> tuple[dict, list[dict]]:
    """Seal physical code/metadata/source bytes, not only distribution labels."""
    start = root / directory
    require(
        start.is_dir() and start.resolve() == start.absolute(),
        "canonical physical directory required",
    )
    rows, metadata = [], []
    total = 0

    def unreadable(error):
        raise InputError("unreadable physical tree: " + str(error))

    for parent, directories, files in os.walk(
        start, followlinks=False, onerror=unreadable
    ):
        directories.sort()
        for name in directories:
            require(
                not (Path(parent) / name).is_symlink(),
                "canonical physical directory required",
            )
            require(not name.endswith(".egg-info"), "unreviewed Python egg inventory")
            if installed_metadata and name.endswith(".dist-info"):
                require(
                    (Path(parent) / name / "METADATA").is_file(),
                    "physical distribution metadata missing",
                )
        for name in sorted(files):
            path = Path(parent) / name
            if source_links and path.is_symlink():
                # Retained source links are witnesses of raw link text, never
                # followed into executable/runtime code or external paths.
                st = path.lstat()
                rows.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "type": "symlink",
                        "target": os.readlink(path),
                        "device": st.st_dev,
                        "inode": st.st_ino,
                        "ctime_ns": st.st_ctime_ns,
                    }
                )
                require(len(rows) < MAX_FILES, "physical tree budget exceeded")
                continue
            data, witness = physical_file(path, root)
            total += len(data)
            require(
                len(rows) < MAX_FILES and total <= MAX_BYTES,
                "physical tree budget exceeded",
            )
            rows.append(witness)
            if path.parent.name.endswith(".dist-info") and name == "METADATA":
                headers = BytesParser().parsebytes(data, headersonly=True)
                require(
                    len(headers.get_all("Name", []))
                    == len(headers.get_all("Version", []))
                    == 1,
                    "unique physical distribution Name/Version required",
                )
                package, version = headers["Name"], headers["Version"]
                require(
                    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package) is not None
                    and bool(version)
                    and version.strip() == version,
                    "physical distribution identity required",
                )
                metadata.append(
                    {"Name": package, "Version": version, "FilePath": witness["path"]}
                )
    return {
        "root": directory,
        "files": len(rows),
        "bytes": total,
        "tree_sha256": canonical_sha256(rows),
    }, metadata


def capture_python(root: Path) -> tuple[list[dict], list[dict]]:
    rows, witnesses = [], []
    for directory in PYTHON_ROOTS:
        witness, packages = tree_witness(
            root, directory, installed_metadata=directory in PYTHON_ROOTS[:2]
        )
        rows.extend(packages)
        witnesses.append(witness)
    require(bool(rows), "nonempty physical Python catalogue required")
    return sorted(rows, key=lambda r: r["FilePath"]), witnesses


def recheck_python(root: Path, before: tuple[list[dict], list[dict]]) -> None:
    require(capture_python(root) == before, "physical Python root changed")


def go_string(data: bytes, offset: int) -> tuple[bytes, int]:
    length = 0
    for shift in range(0, 64, 7):
        require(offset < len(data), "truncated Go buildinfo length")
        value = data[offset]
        offset += 1
        length |= (value & 127) << shift
        if not value & 128:
            require(
                length <= 1024**2 and offset + length <= len(data),
                "bounded Go buildinfo string required",
            )
            return data[offset : offset + length], offset + length
    raise InputError("invalid Go buildinfo length")


def go_catalogue(data: bytes, architecture: str) -> list[dict]:
    """Decode the surveyed modern ELF inline Go buildinfo; reject other formats."""
    require(
        len(data) >= 64
        and data[:6] == b"\x7fELF\x02\x01"
        and int.from_bytes(data[18:20], "little")
        == {"amd64": 62, "arm64": 183}[architecture],
        "native ELF64 little-endian gosu required",
    )
    magic = b"\xff Go buildinf:"
    offset = data.find(magic)
    require(
        offset >= 0
        and offset % 16 == 0
        and data.find(magic, offset + 1) == -1
        and data[offset + 14 : offset + 32] == b"\x08\x02" + b"\x00" * 16,
        "unique reviewed inline Go buildinfo required",
    )
    compiler, offset = go_string(data, offset + 32)
    raw, _ = go_string(data, offset)
    require(
        re.fullmatch(rb"go[0-9]+\.[0-9]+\.[0-9]+", compiler) is not None,
        "Go compiler identity required",
    )
    # Go's debug.BuildInfo framing, not an arbitrary string search for module names.
    require(
        raw[:16] == bytes.fromhex("3077af0c9274080241e1c107e6d618e6")
        and raw[-16:] == bytes.fromhex("f932433186182072008242104116d8f2"),
        "Go module framing differs",
    )
    lines = raw[16:-16].decode("utf-8").splitlines()
    modules, path, settings = [], [], []
    for line in lines:
        fields = line.split("\t")
        require(
            fields[0] in {"path", "mod", "dep", "build"},
            "unreviewed Go buildinfo record/replacement",
        )
        if fields[0] == "path":
            require(len(fields) == 2, "Go path record required")
            path.append(fields[1])
        elif fields[0] in {"mod", "dep"}:
            require(len(fields) == 4 and bool(fields[1]), "Go module record required")
            version = "" if fields[0] == "mod" and fields[2] == "(devel)" else fields[2]
            require(
                fields[0] == "mod" or bool(version), "Go dependency version required"
            )
            modules.append(
                {"Name": fields[1], "Version": version, "root": fields[0] == "mod"}
            )
        else:
            require(len(fields) == 2, "Go build setting required")
            settings.append(fields[1])
    roots = [r for r in modules if r["root"]]
    require(
        len(roots) == 1
        and path == [roots[0]["Name"]]
        and f"GOARCH={architecture}" in settings
        and "GOOS=linux" in settings
        and len({r["Name"] for r in modules}) == len(modules),
        "Go native module/path/settings identity differs",
    )
    modules.append(
        {"Name": "stdlib", "Version": "v" + compiler.decode()[2:], "root": False}
    )
    return sorted(modules, key=lambda r: r["Name"])


def capture(root: Path, identity: dict) -> dict:
    python, witnesses = capture_python(root)
    architecture = identity["Architecture"]
    data, binary = physical_file(root / GOSU, root)
    modules = go_catalogue(data, architecture)
    receipt_data, receipt_witness = physical_file(root / GOSU_RECEIPT, root)
    receipt = json.loads(receipt_data, object_pairs_hook=unique)
    source, _ = tree_witness(root, GOSU_SOURCE, source_links=True)
    recipe_path = Path(__file__).resolve().parents[1] / "docker/alpine-native/gosu.json"
    recipe = json_file(recipe_path)
    installed_recipe = root / GOSU_SOURCE / "gosu.json"
    require(
        installed_recipe.read_bytes() == recipe_path.read_bytes(),
        "reviewed gosu source recipe differs",
    )
    proof = receipt["gosu"]
    actual = {
        r["Name"]: r["Version"]
        for r in modules
        if not r["root"] and r["Name"] != "stdlib"
    }
    require(
        proof["sha256"] == binary["sha256"]
        and proof["architecture"] == architecture
        and actual == recipe["gosu"]["modules"]
        and {r["Path"]: r["Version"] for r in proof["modules"] if not r.get("Main")}
        == actual
        and [r["Name"] for r in modules if r["root"]]
        == [r["Path"] for r in proof["modules"] if r.get("Main")]
        and next(r["Version"] for r in modules if r["Name"] == "stdlib")
        == "v" + recipe["gosu"]["go_version"],
        "physical gosu buildinfo/receipt/reviewed source modules differ",
    )
    for filename, expected in [
        ("gosu.tar.gz", recipe["gosu"]),
        (f"go{recipe['gosu']['go_version']}.src.tar.gz", recipe["go_source"]),
    ]:
        _, witness = physical_file(root / GOSU_SOURCE / filename, root)
        require(
            witness["sha256"] == expected["sha256"],
            "physical gosu source archive differs",
        )
    return {
        "schema_version": 1,
        "scope": SCHEMA,
        "identity": identity,
        "python": python,
        "go": [{**r, "Target": GOSU} for r in modules],
        "physical_witnesses": {
            "python": witnesses,
            "gosu": binary,
            "receipt": receipt_witness,
            "source": source,
        },
        "complete_image_catalogue": False,
        "full_runtime_advisory_acceptance": False,
    }


def validate_capture(document: dict, expected_sha256: str, identity: dict) -> dict:
    require(
        isinstance(expected_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        and canonical_sha256(document) == expected_sha256,
        "trusted independent physical language capture digest differs",
    )
    require(
        set(document)
        == {
            "schema_version",
            "scope",
            "identity",
            "python",
            "go",
            "physical_witnesses",
            "complete_image_catalogue",
            "full_runtime_advisory_acceptance",
        }
        and type(document["schema_version"]) is int
        and document["schema_version"] == 1
        and document["scope"] == SCHEMA
        and document["identity"] == identity
        and document["complete_image_catalogue"] is False
        and document["full_runtime_advisory_acceptance"] is False
        and bool(document["python"])
        and bool(document["go"])
        and bool(document["physical_witnesses"]),
        "physical language capture schema/image identity differs",
    )
    return document


def check_catalogue(report: dict, capture: dict) -> None:
    for kind, key in [("python-pkg", "python"), ("gobinary", "go")]:
        observed = []
        for result in report["Results"]:
            if result.get("Class") != "lang-pkgs" or result.get("Type") != kind:
                continue
            for row in result["Packages"]:
                if key == "python":
                    observed.append(
                        {field: row[field] for field in ["Name", "Version", "FilePath"]}
                    )
                else:
                    observed.append(
                        {
                            "Name": row["Name"],
                            "Version": row.get("Version", ""),
                            "root": row.get("Relationship") == "root",
                            "Target": result["Target"],
                        }
                    )

        def sort_key(row):
            return json.dumps(row, sort_keys=True)

        require(
            sorted(observed, key=sort_key) == sorted(capture[key], key=sort_key),
            f"physical {key} catalogue differs from scan",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(
            bool(os.statvfs(args.root).f_flag & os.ST_RDONLY),
            "read-only immutable physical root required",
        )
        result = capture(args.root.resolve(), json_file(args.identity))
        # Exclusive output, never replace an operator file. Digest is trusted job output.
        with args.output.open("x") as stream:
            stream.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(canonical_sha256(result))
        return 0
    except (InputError, OSError, ValueError, TypeError, KeyError) as exc:
        print("LANGUAGE CAPTURE DENIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
