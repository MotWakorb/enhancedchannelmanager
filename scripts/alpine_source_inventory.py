"""Join live APK query/database bytes into an unauthenticated source work plan.

This is not a delivery consumer and never grants source or advisory acceptance.
APK signatures, recipe inputs, Python-base and owned-provider proofs are separate.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

TOKEN = re.compile(r"[A-Za-z0-9_+.~-]+\Z")
QUERY_FIELDS = {
    "P": "name",
    "V": "version",
    "A": "arch",
    "T": "description",
    "L": "license",
    "o": "origin",
    "U": "url",
}


class InventoryError(ValueError):
    """The two live inventory surfaces disagree or contain ambiguous identities."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def checksum(value: str) -> None:
    # Only the actually surveyed format is supported. Unknown newer formats fail
    # rather than being mislabeled as authenticated compressed-control SHA1.
    require(value.startswith("Q1"), "unsupported installed checksum encoding")
    try:
        decoded = base64.b64decode(value[2:], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InventoryError("invalid installed checksum") from exc
    require(
        len(decoded) == 20 and base64.b64encode(decoded).decode() == value[2:],
        "invalid installed checksum",
    )


def relative(value: str, *, filename: bool = False) -> None:
    path = PurePosixPath(value)
    require(
        bool(value)
        and str(path) == value
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value,
        "unsafe installed payload path",
    )
    require(not filename or len(path.parts) == 1, "unsafe installed payload filename")


def parse_database(text: str, architecture: str) -> list[dict]:
    require(architecture in {"x86_64", "aarch64"}, "unsupported installed architecture")
    require(
        bool(text.strip()) and len(text.encode()) <= 64 * 1024**2,
        "empty/oversized installed database",
    )
    require(
        all(char == "\n" or ord(char) >= 32 for char in text),
        "control character in installed database",
    )
    packages = []
    seen = set()
    for block in text.strip("\n").split("\n\n"):
        require(bool(block), "empty installed package record")
        fields = {}
        file_section = False
        directory = None
        filename = None
        files = set()
        directories = set()
        file_fields = set()
        for line in block.splitlines():
            require(len(line) >= 2 and line[1] == ":", "malformed installed field")
            key, value = line[0], line[2:]
            if key == "F":
                file_section = True
                relative(value)
                require(
                    value not in directories, "duplicate installed payload directory"
                )
                directories.add(value)
                directory = value
                filename = None
            elif file_section:
                require(
                    key in {"R", "a", "Z", "M"},
                    "unexpected installed file-section field",
                )
                if key == "R":
                    relative(value, filename=True)
                    filename = str(PurePosixPath(directory) / value)
                    require(filename not in files, "duplicate installed payload file")
                    files.add(filename)
                    file_fields = set()
                elif key == "M":
                    require(
                        filename is None
                        and re.fullmatch(r"\d+:\d+:[0-7]{3,4}", value) is not None,
                        "invalid installed directory metadata",
                    )
                else:
                    require(
                        filename is not None and key not in file_fields,
                        "ambiguous installed payload metadata",
                    )
                    file_fields.add(key)
                    if key == "Z":
                        checksum(value)
                    else:
                        require(
                            re.fullmatch(r"\d+:\d+:[0-7]{3,4}", value) is not None,
                            "invalid installed file attributes",
                        )
            else:
                require(key not in fields, "duplicate installed metadata field")
                fields[key] = value
        require(
            set("CPVASI").issubset(fields), "missing installed identity/size/checksum"
        )
        for key in "PV":
            require(
                bool(TOKEN.fullmatch(fields[key])) and fields[key] not in {".", ".."},
                "unsafe installed package identity",
            )
        require(fields["P"] not in seen, "duplicate installed package name")
        seen.add(fields["P"])
        require(
            fields["A"] in {architecture, "noarch"},
            "installed package architecture mismatch",
        )
        checksum(fields["C"])
        require(
            all(re.fullmatch(r"0|[1-9][0-9]*", fields[k]) for k in "SI"),
            "invalid installed package size",
        )
        if "c" in fields:
            require(
                re.fullmatch(r"[0-9a-f]{40}", fields["c"]) is not None,
                "invalid installed source commit",
            )
        if "o" in fields:
            require(
                bool(TOKEN.fullmatch(fields["o"])) and fields["o"] not in {".", ".."},
                "invalid installed source origin",
            )
        packages.append(
            {
                "name": fields["P"],
                "version": fields["V"],
                "arch": fields["A"],
                "fields": fields,
                "payload_file_count": len(files),
                "payload_directory_count": len(directories),
                "database_record_sha256": hashlib.sha256(block.encode()).hexdigest(),
            }
        )
    return sorted(packages, key=lambda row: row["name"])


def payload_record(text: str, architecture: str) -> dict:
    """Decode one validated record without interpreting its checksum algorithm.

    The selected formats' omitted attributes use the surveyed APK DB defaults.
    A consumer must bind the appropriate signed package format and every raw
    attribute/token; legacy Q1 may not mean the same file hash for APKv2 and v3.
    No existing source_plan representation/digest or acceptance scope changes.
    """
    records = parse_database(text, architecture)
    require(len(records) == 1, "one complete installed payload record required")
    directories = {}
    files = {}
    directory = None
    filename = None
    directory_attributes = set()
    for line in text.strip("\n").splitlines():
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
                    "duplicate installed directory attributes",
                )
                directory_attributes.add(directory)
                directories[directory] = value
            else:
                files[filename][key] = value
    return {
        "status": "UNAUTHENTICATED_INSTALLED_PAYLOAD_RECORD_ONLY",
        "record": records[0],
        "files": files,
        "directories": directories,
        "full_runtime_source_acceptance": False,
    }


def source_plan(capture: dict, architecture: str) -> dict:
    packages = parse_database(capture["installed_database"], architecture)
    query = capture["packages"]
    require(
        isinstance(query, list)
        and all(
            isinstance(row, dict) and isinstance(row.get("name"), str) for row in query
        ),
        "invalid live query inventory",
    )
    actual = {row["name"]: row for row in query}
    require(
        all("file-size" not in row or type(row["file-size"]) is int for row in query),
        "invalid query package size type",
    )
    require(len(actual) == len(query), "duplicate live query package")
    expected = {}
    for row in packages:
        fields = row["fields"]
        projected = {
            name: fields[key] for key, name in QUERY_FIELDS.items() if fields.get(key)
        }
        if int(fields["S"]):
            projected["file-size"] = int(fields["S"])
        expected[row["name"]] = projected
    require(actual == expected, "live query/database package-set or metadata mismatch")
    groups = {}
    virtual = []
    unresolved = []
    for row in packages:
        fields = row["fields"]
        if row["name"] == ".python-rundeps":
            require(
                row["arch"] == "noarch"
                and fields["S"] == fields["I"] == "0"
                and fields.get("T") == "virtual meta package"
                and fields.get("U") == fields.get("L") == ""
                and not {"o", "c"} & fields.keys()
                and row["payload_file_count"] == row["payload_directory_count"] == 0
                and bool(fields.get("D")),
                "invalid zero-payload Python virtual package exemption",
            )
            virtual.append(
                {
                    "name": row["name"],
                    "version": row["version"],
                    "checksum": fields["C"],
                    "dependencies": fields["D"],
                }
            )
        elif not all(fields.get(key) for key in ["o", "c", "L"]):
            # This includes unknown originless packages and any owned package
            # whose independent sealed proof has not yet been joined. Never skip.
            unresolved.append(row)
        else:
            key = (fields["o"], fields["c"], row["version"])
            groups.setdefault(key, []).append(row["name"])
    require(
        sum(map(len, groups.values())) + len(virtual) + len(unresolved)
        == len(packages),
        "incomplete source planning partition",
    )
    return {
        "schema_version": 1,
        "status": "UNAUTHENTICATED_APK_SOURCE_PLAN_ONLY",
        "architecture": architecture,
        "database_sha256": hashlib.sha256(
            capture["installed_database"].encode()
        ).hexdigest(),
        "package_set_sha256": canonical_sha256(packages),
        "packages": packages,
        "source_groups": [
            {
                "origin": key[0],
                "commit": key[1],
                "package_version": key[2],
                "packages": names,
            }
            for key, names in sorted(groups.items())
        ],
        "virtual_packages": virtual,
        "unresolved_packages": unresolved,
        "scope": "Complete query/database join and origin worklist only. No signature, recipe, source-byte, owned-package, Python-base, license or advisory acceptance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--architecture", choices=["x86_64", "aarch64"], required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    require(
        not args.output_file.exists() and not args.output_file.is_symlink(),
        "source plan output must be fresh",
    )
    result = source_plan(json.loads(args.inventory.read_text()), args.architecture)
    args.output_file.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ["status", "architecture", "package_set_sha256"]
            }
        )
    )


if __name__ == "__main__":
    main()
