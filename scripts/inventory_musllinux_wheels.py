#!/usr/bin/env python3
"""Bind exact musllinux wheel payloads and notices; metadata is not source proof."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import struct
import subprocess
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from verify_musllinux_gnu import MANIFEST_SHA256, AttributionError, require, sha256

__all__ = ["AttributionError", "build", "scan_wheel", "sha256"]

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_SHA256 = {
    "amd64": "415442890522d9c70f4137a04261f3f198d646febc5a8368602d653fda98825d",
    "arm64": "d6888db24297168b5479a6255bb58463bb07c3f24bf167cbf6d90a82b5bcc803",
}


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def elf_identity(data: bytes, arch: str) -> dict:
    require(
        len(data) >= 64 and data[:6] == b"\x7fELF\x02\x01",
        "little-endian ELF64 required",
    )
    machine = struct.unpack_from("<H", data, 18)[0]
    require(
        machine == {"amd64": 62, "arm64": 183}[arch], "native ELF architecture mismatch"
    )
    # Never load or execute a wheel member, including when inspecting ARM on AMD.
    with tempfile.TemporaryDirectory(prefix="ecm-wheel-elf-") as temporary:
        path = Path(temporary) / "payload"
        path.write_bytes(data)
        result = subprocess.run(
            ["readelf", "-d", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    dynamic = result.stdout
    return {
        "elf_machine": machine,
        "DT_NEEDED": re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic),
        "SONAME": re.findall(r"\(SONAME\).*?\[([^\]]+)\]", dynamic),
        "RPATH": re.findall(r"\((?:RUNPATH|RPATH)\).*?\[([^\]]+)\]", dynamic),
    }


def scan_wheel(path: Path, digest: str, arch: str) -> dict:
    require(
        path.is_file() and not path.is_symlink() and sha256(path) == digest,
        "whole-wheel identity mismatch",
    )
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate wheel member")
        files = {}
        for info in archive.infolist():
            name = info.filename
            require(
                name
                and not name.startswith("/")
                and "\\" not in name
                and ".." not in PurePosixPath(name).parts
                and str(PurePosixPath(name)) == name.rstrip("/"),
                "unsafe wheel member name",
            )
            require(not stat.S_ISLNK(info.external_attr >> 16), "unsafe wheel symlink")
            if not info.is_dir():
                files[name] = archive.read(info)
    metadata_names = [
        name for name in files if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)
    ]
    require(len(metadata_names) == 1, "one wheel metadata identity required")
    metadata_name = metadata_names[0]
    dist = metadata_name.rsplit("/", 1)[0]
    metadata = BytesParser().parsebytes(files[metadata_name])
    require(
        metadata.get("Name") and metadata.get("Version"),
        "wheel package identity missing",
    )
    record_name, wheel_name = dist + "/RECORD", dist + "/WHEEL"
    require(record_name in files and wheel_name in files, "wheel RECORD/WHEEL missing")
    rows = list(csv.reader(io.StringIO(files[record_name].decode())))
    require(
        all(len(row) == 3 for row in rows)
        and len({row[0] for row in rows}) == len(rows),
        "invalid/duplicate wheel RECORD",
    )
    require(
        {row[0] for row in rows} == set(files), "wheel RECORD member coverage mismatch"
    )
    for name, encoded, size in rows:
        if name == record_name:
            require(
                encoded == size == "", "wheel RECORD self-reference must be unhashed"
            )
            continue
        require(
            encoded.startswith("sha256=") and size == str(len(files[name])),
            "wheel RECORD size/hash algorithm mismatch",
        )
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest())
            .decode()
            .rstrip("=")
        )
        require(encoded == "sha256=" + expected, "wheel RECORD payload hash mismatch")
    for license_name in metadata.get_all("License-File", []):
        require(
            not license_name.startswith("/")
            and ".." not in PurePosixPath(license_name).parts,
            "unsafe declared license filename",
        )
        require(
            dist + "/licenses/" + license_name in files
            or dist + "/" + license_name in files,
            "declared license file missing",
        )
    members, native, notices, sboms = [], [], [], []
    for name, content in sorted(files.items()):
        row = {
            "path": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        if re.fullmatch(r"[^/]+\.data/scripts/[^/]+", name):
            first, separator, body = content.partition(b"\n")
            require(
                first == b"#!python" and separator == b"\n",
                "unsupported wheel script shebang",
            )
            row["script_body_sha256"] = hashlib.sha256(body).hexdigest()
        members.append(row)
        if content.startswith(b"\x7fELF"):
            native.append({**row, **elf_identity(content, arch)})
        basename = PurePosixPath(name).name.lower()
        if "/licenses/" in name or re.match(
            r"^(?:license|copying|notice|copyright)(?:[._-]|$)", basename
        ):
            notices.append(dict(row))
        if "/sboms/" in name or basename.endswith((".cdx.json", ".spdx.json")):
            payload = json.loads(content)
            require(isinstance(payload, dict), "embedded SBOM must be an object")
            sboms.append(
                {
                    **row,
                    "format": payload.get(
                        "bomFormat", payload.get("spdxVersion", "unknown")
                    ),
                    "components": payload.get(
                        "components", payload.get("packages", [])
                    ),
                }
            )
    wheel_metadata = BytesParser().parsebytes(files[wheel_name])
    pure = wheel_metadata.get("Root-Is-Purelib", "").lower() == "true"
    require(not pure or not native, "purelib wheel contains native ELF")
    return {
        "name": normalized(metadata["Name"]),
        "version": metadata["Version"],
        "filename": path.name,
        "sha256": digest,
        "purelib": pure,
        "tags": wheel_metadata.get_all("Tag", []),
        "metadata_sha256": hashlib.sha256(files[metadata_name]).hexdigest(),
        "members": members,
        "native_files": native,
        "notices": notices,
        "sboms": sboms,
        "source_status": "NOT_ESTABLISHED_BY_WHEEL_METADATA",
    }


def verify_payloads(row: dict, payloads: Path, notices: Path) -> None:
    for item in row["notices"] + row["sboms"]:
        path = payloads / item["sha256"]
        require(
            path.is_file() and not path.is_symlink() and sha256(path) == item["sha256"],
            "delivered wheel notice/SBOM identity mismatch",
        )
    for item in row["notices"]:
        for name in item.get("retained_identical_notices", []):
            path = notices / name
            require(
                path.resolve().is_relative_to(notices.resolve())
                and path.is_file()
                and not path.is_symlink()
                and sha256(path) == item["sha256"],
                "shared notice identity mismatch",
            )


def verify_installed(
    row: dict, site: Path, installed_python: str | None = None
) -> None:
    root = site.parents[2]
    for item in row["members"]:
        name = item["path"]
        # pip legitimately rewrites RECORD and script shebangs. PEP427 .data
        # headers/scripts are verified at their actual relocated destinations.
        if name.endswith(".dist-info/RECORD"):
            continue
        parts = PurePosixPath(name).parts
        if parts[0].endswith(".data"):
            if len(parts) == 3 and parts[1] == "headers":
                path = root / "include/site/python3.12" / row["name"] / parts[2]
            elif len(parts) == 3 and parts[1] == "scripts":
                path = root / "bin" / parts[2]
                require(
                    path.is_file() and not path.is_symlink(), "installed script missing"
                )
                first, separator, body = path.read_bytes().partition(b"\n")
                require(
                    installed_python is not None
                    and first == ("#!" + installed_python).encode()
                    and separator == b"\n"
                    and hashlib.sha256(body).hexdigest() == item["script_body_sha256"],
                    "installed script identity mismatch",
                )
                continue
            else:
                raise AttributionError("unsupported installed wheel relocation")
        else:
            path = site / name
        require(
            path.resolve().is_relative_to(root.resolve())
            and path.is_file()
            and not path.is_symlink()
            and sha256(path) == item["sha256"],
            "installed wheel member identity mismatch: " + name,
        )


def verify(
    index: Path,
    expected_digest: str,
    arch: str,
    wheels: Path,
    payloads: Path,
    notices: Path,
    site: Path | None = None,
    installed_python: str | None = None,
) -> dict:
    require(
        index.is_file() and not index.is_symlink() and sha256(index) == expected_digest,
        "reviewed wheel inventory identity mismatch",
    )
    inventory = json.loads(index.read_text())
    require(
        inventory["architecture"] == arch
        and inventory["requirements_sha256"]
        == sha256(ROOT / "backend/requirements.txt"),
        "wheel inventory architecture/lock mismatch",
    )
    rows = inventory["distributions"]
    require(
        len(rows) == inventory["distribution_count"] == 74
        and len({row["name"] for row in rows}) == 74,
        "complete unique wheel lock required",
    )
    for row in rows:
        actual = scan_wheel(wheels / row["filename"], row["sha256"], arch)
        expected = {key: row[key] for key in actual}
        expected["notices"] = [
            {
                key: value
                for key, value in item.items()
                if key != "retained_identical_notices"
            }
            for item in row["notices"]
        ]
        require(
            actual == expected, "actual wheel payload differs from reviewed inventory"
        )
        verify_payloads(row, payloads, notices)
        if site is not None:
            verify_installed(row, site, installed_python)
    expected_native = {item["path"] for row in rows for item in row["native_files"]}
    require(
        len(expected_native) == inventory["native_ELF_count"],
        "native wheel inventory count mismatch",
    )
    if site is not None:
        actual_native = set()
        for path in site.rglob("*"):
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        actual_native.add(path.relative_to(site).as_posix())
        require(
            actual_native == expected_native, "installed native ELF coverage mismatch"
        )
    return {
        "status": "INSTALLED_WHEEL_ARTIFACT_BINDINGS_ONLY"
        if site
        else "WHEEL_ARTIFACT_BINDINGS_ONLY",
        "architecture": arch,
        "distributions": len(rows),
        "native_ELFs": len(expected_native),
        "scope": "Artifact/notice/raw-SBOM verification only; not corresponding-source or advisory clearance.",
    }


def build(wheels: Path, pypi: Path, notices: Path, output: Path) -> dict:
    require(
        not output.exists() and not output.is_symlink(),
        "wheel inventory output must be fresh",
    )
    require(
        not output.resolve().is_relative_to(notices.resolve()),
        "inventory must not overwrite historical notices",
    )
    gnu_path = ROOT / "docker/python-third-party/artifact-index/musllinux-gnu.json"
    require(
        sha256(gnu_path) == MANIFEST_SHA256,
        "reviewed wheel selection identity mismatch",
    )
    selected = json.loads(gnu_path.read_text())["wheel_selection"]
    pins_path = ROOT / "backend/requirements.txt"
    pins = {
        normalized(name): version
        for name, version in re.findall(
            r"^([A-Za-z0-9_.-]+)==([^\s]+)$", pins_path.read_text(), re.MULTILINE
        )
    }
    require(len(pins) == 74, "unchanged 74-package lock required")
    sources = {}
    for path in sorted(pypi.glob("*.json")):
        if path.name.endswith(".receipt.json"):
            continue
        payload = json.loads(path.read_text())
        if "urls" not in payload:
            continue
        for row in payload["urls"]:
            sources[row["filename"]] = {
                "url": row["url"],
                "sha256": row["digests"]["sha256"],
                "metadata_file": path.name,
                "metadata_sha256": sha256(path),
                "yanked": row.get("yanked", False),
            }
    retained = {}
    for path in sorted(notices.rglob("*")):
        if path.is_file() and not path.is_symlink():
            retained.setdefault(sha256(path), []).append(
                path.relative_to(notices).as_posix()
            )
    output.mkdir(parents=True)
    payloads = output / "payloads"
    payloads.mkdir()
    distributions = {"amd64": [], "arm64": []}
    for filename, digest in sorted(selected.items()):
        source = sources.get(filename)
        require(
            source is not None and source["sha256"] == digest and not source["yanked"],
            "selected wheel/PyPI artifact identity mismatch",
        )
        arches = (
            ["amd64", "arm64"]
            if filename.endswith("-any.whl")
            else ["arm64" if "_aarch64.whl" in filename else "amd64"]
        )
        row = scan_wheel(wheels / filename, digest, arches[0])
        require(
            pins.get(row["name"]) == row["version"],
            "wheel metadata differs from unchanged package lock",
        )
        for item in row["notices"]:
            item["retained_identical_notices"] = retained.get(item["sha256"], [])
        row["pypi_artifact"] = source
        with zipfile.ZipFile(wheels / filename) as archive:
            for item in row["notices"] + row["sboms"]:
                target = payloads / item["sha256"]
                if not target.exists():
                    target.write_bytes(archive.read(item["path"]))
        for arch in arches:
            distributions[arch].append(row)
    for arch, rows in distributions.items():
        require(
            len(rows) == len(pins)
            and {row["name"]: row["version"] for row in rows} == pins,
            "complete per-architecture lock coverage required",
        )
        result = {
            "schema_version": 1,
            "architecture": arch,
            "requirements_sha256": sha256(pins_path),
            "selection_manifest_sha256": MANIFEST_SHA256,
            "scope": "Whole-wheel/RECORD/native-byte/notice/raw-SBOM attribution. Not corresponding-source, advisory, installed-image or native-execution approval.",
            "distribution_count": len(rows),
            "native_ELF_count": sum(len(row["native_files"]) for row in rows),
            "distributions": rows,
        }
        (output / ("linux-" + arch + ".json")).write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
    summary = {
        "status": "WHEEL_ARTIFACT_BINDINGS_ONLY",
        "whole_wheels": len(selected),
        "architectures": {
            arch: {
                "distributions": len(rows),
                "native_ELFs": sum(len(row["native_files"]) for row in rows),
            }
            for arch, rows in distributions.items()
        },
        "unique_payload_files": len(list(payloads.iterdir())),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels-dir", type=Path, required=True)
    parser.add_argument("--pypi-dir", type=Path)
    parser.add_argument(
        "--notices-dir", type=Path, default=ROOT / "docker/python-third-party/notices"
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--arch", choices=["amd64", "arm64"])
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument(
        "--installed-python",
        help="Exact expected pip script interpreter path, not an inferred or accepted shebang",
    )
    args = parser.parse_args()
    if args.verify:
        if not args.arch or args.output_dir or args.pypi_dir:
            parser.error("--verify requires --arch and does not build inventory output")
        directory = ROOT / "docker/python-third-party/artifact-index/musllinux"
        result = verify(
            directory / ("linux-" + args.arch + ".json"),
            INVENTORY_SHA256[args.arch],
            args.arch,
            args.wheels_dir,
            directory / "payloads",
            args.notices_dir,
            args.site_packages,
            args.installed_python,
        )
    else:
        if not args.output_dir or not args.pypi_dir or args.site_packages:
            parser.error("inventory construction requires --pypi-dir and --output-dir")
        result = build(
            args.wheels_dir, args.pypi_dir, args.notices_dir, args.output_dir
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
