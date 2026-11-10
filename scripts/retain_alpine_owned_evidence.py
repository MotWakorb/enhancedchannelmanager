#!/usr/bin/env python3
"""Retain selected original public APK evidence, not source/signature clearance.

Used after the existing candidate installers authenticate and install the owned
packages. Copies original archives/receipts/public keys/check evidence only;
never copies private keys or changes runtime repositories/trusted key stores.
Independent native signature/installed DB/live/source checks remain mandatory.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from alpine_owned_native import reviewed_package_basename
from build_native_runtime import BuildError, _require, owned_artifacts, sha256
from install_alpine_native import load_package
from install_busybox_provider import load_packages
from verify_alpine_external_sources import unique


def canonical_directory(path: Path) -> None:
    _require(
        path.is_dir() and not path.is_symlink() and path.absolute() == path.resolve(),
        "regular canonical owned evidence directory required",
    )


def checked_file(
    path: Path, expected: str | None = None, *, maximum: int = 512 * 1024**2
) -> str:
    _require(
        path.is_file()
        and not path.is_symlink()
        and path.absolute() == path.resolve()
        and 0 < path.stat().st_size <= maximum,
        "regular canonical bounded owned evidence file required",
    )
    digest = sha256(path)
    _require(
        expected is None or digest == expected, "owned evidence input hash differs"
    )
    return digest


def retain(
    native: Path,
    busybox: Path,
    architecture: str,
    output: Path,
    *,
    busybox_source_root: Path | None = None,
) -> dict:
    _require(
        architecture in {"amd64", "arm64"},
        "reviewed owned evidence architecture required",
    )
    for directory in [native, busybox, output.parent]:
        canonical_directory(directory)
    _require(
        not output.exists() and not output.is_symlink(),
        "owned evidence output must be fresh",
    )
    source = busybox / "sources" if busybox_source_root is None else busybox_source_root
    canonical_directory(source)
    metadata = {
        "native/package-output.json": native / "package-output.json",
        "busybox/build-output.json": busybox / "build-output.json",
        "busybox/check-output.json": busybox / "check-output.json",
        "busybox/check.log": busybox / "check.log",
    }
    metadata_hashes = {
        name: checked_file(path, maximum=32 * 1024**2)
        for name, path in metadata.items()
    }
    try:
        decoded = {
            name: json.loads(path.read_text(), object_pairs_hook=unique)
            for name, path in metadata.items()
            if name.endswith(".json")
        }
    except (ValueError, RecursionError) as exc:
        raise BuildError("invalid or ambiguous owned public evidence receipt") from exc
    _require(
        all(isinstance(record, dict) for record in decoded.values()),
        "owned evidence JSON objects required",
    )
    # Reuse ALL existing BusyBox source/static/check validators, without a second
    # source tree. Neither loader alone authenticates copied package signatures.
    native_record = load_package(native, architecture)
    busybox_record = load_packages(
        busybox, architecture, source / "owned-recipe/manifest.json", source_root=source
    )
    _require(
        decoded["native/package-output.json"] == native_record
        and decoded["busybox/build-output.json"] == busybox_record,
        "original raw owned evidence receipt changed during validation",
    )
    _require(
        native_record["architecture"] == busybox_record["architecture"] == architecture,
        "owned evidence architecture differs",
    )
    _require(
        native_record["public_key"]["file"] == "ecm-build.pub"
        and busybox_record["public_key"]["file"] == "ecm-busybox.rsa.pub",
        "reviewed owned public-key basenames required",
    )
    reviewed_package_basename(native_record)
    _require(
        [row["name"] for row in busybox_record["runtime_packages"]]
        == ["busybox", "busybox-binsh", "ssl_client"],
        "all three reviewed BusyBox packages required",
    )
    selected = []
    for directory, prefix, record in [
        (native, "native", native_record),
        (busybox, "busybox", busybox_record),
    ]:
        key = directory / record["public_key"]["file"]
        checked_file(key, record["public_key"]["sha256"], maximum=16 * 1024)
        # Public-only PEM envelope, not a cryptographic validity/signature claim.
        # Exact original keys are separately hash-bound and natively verified.
        _require(
            re.fullmatch(
                rb"-----BEGIN PUBLIC KEY-----\n(?:[A-Za-z0-9+/=]+\n)+-----END PUBLIC KEY-----\n",
                key.read_bytes(),
            )
            is not None,
            "only original public-key PEM evidence may be delivered",
        )
        selected.append((key, prefix + "/" + key.name, record["public_key"]["sha256"]))
    selected += [
        (
            native / "package-output.json",
            "native/package-output.json",
            metadata_hashes["native/package-output.json"],
        ),
        (
            native / native_record["package"]["file"],
            "native/" + native_record["package"]["file"],
            native_record["package"]["sha256"],
        ),
        (
            busybox / "build-output.json",
            "busybox/build-output.json",
            metadata_hashes["busybox/build-output.json"],
        ),
        (
            busybox / "check-output.json",
            "busybox/check-output.json",
            metadata_hashes["busybox/check-output.json"],
        ),
        (
            busybox / "check.log",
            "busybox/check.log",
            metadata_hashes["busybox/check.log"],
        ),
        (Path(__file__).resolve(), "recipe/retain_alpine_owned_evidence.py", None),
    ]
    for row in busybox_record["runtime_packages"]:
        _require(
            row["file"]
            == row["name"] + "-" + busybox_record["package_version"] + ".apk"
            and Path(row["file"]).name == row["file"],
            "reviewed BusyBox evidence basename required",
        )
        selected.append(
            (
                busybox / "runtime-packages" / row["file"],
                "busybox/runtime-packages/" + row["file"],
                row["sha256"],
            )
        )
    bound = [
        (path, name, checked_file(path, expected)) for path, name, expected in selected
    ]
    _require(
        len(bound) == 11 and len({name for _, name, _ in bound}) == 11,
        "complete unique owned evidence selection required",
    )
    output.mkdir(mode=0o755)
    for path, name, digest in bound:
        target = output / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)
        _require(sha256(target) == digest, "copied owned evidence hash differs")
    result = {
        "schema_version": 1,
        "status": "SELECTED_ORIGINAL_OWNED_APK_EVIDENCE_COPIED_ONLY",
        "architecture": architecture,
        "selected_packages": 4,
        "files": owned_artifacts(output),
        "helper_sha256": sha256(Path(__file__)),
        "signature_verification_required": True,
        "full_runtime_source_acceptance": False,
        "scope": "Original four signed APK archives, raw native/BusyBox receipts, separate original-basename public keys, BusyBox original check receipt/log and trusted copier source. No separate source-tree duplicate, private key or runtime trust/repository change. Public-only evidence transport, not cryptographic authentication, reproduced builds/tests, historical compiler linkage or complete source/advisory/MCP acceptance; independent native signature/DB/live/source composition remains required.",
    }
    with (output / "index.json").open("x") as stream:
        stream.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-packages", type=Path, required=True)
    parser.add_argument("--busybox-packages", type=Path, required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    options = parser.parse_args()
    try:
        result = retain(
            options.native_packages,
            options.busybox_packages,
            options.arch,
            options.output_dir,
        )
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, "owned public evidence delivery failed: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
