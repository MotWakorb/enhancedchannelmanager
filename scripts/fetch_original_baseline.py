#!/usr/bin/env python3
"""Fetch the same sealed original ZIP, including pinned retention mirrors."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from compare_alpine_baseline import MatrixError, load_baselines
from prepare_alpine_baseline import checked_zip, sha256

REPOSITORY = "MotWakorb/enhancedchannelmanager"
MIRRORS = {
    "amd64": {
        "id": 13639697854,
        "sha256": "62b741607f2bb7162862783c84da01e31dd82ffdd4440728946f7129afcc3266",
        "member": "original.zip",
    },
    "arm64": {
        "id": 13640590392,
        "sha256": "7aa9e86c1e7709852c3d52e40a4106ee5b3d660595b5a27ce303a12fc1bc0c85",
        "member": "original.zip",
    },
}


def inputs(arch: str) -> tuple[dict, dict]:
    return load_baselines()["architectures"][arch]["image_artifact"], MIRRORS[arch]


def download(artifact: int, target: Path) -> subprocess.CompletedProcess:
    with target.open("xb") as stream:
        result = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact}/zip"],
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=900,
        )
    with target.with_name(target.name + '.stderr').open('xb') as evidence:
        evidence.write(result.stderr)
    return result


def fetch(arch: str, target: Path) -> dict:
    if (
        target.exists()
        or target.is_symlink()
        or any(
            target.with_name(target.name + suffix).exists() or target.with_name(target.name + suffix).is_symlink()
            for suffix in [
                ".receipt.json",
                ".mirror.zip",
                ".primary-error.json",
                ".stderr",
                '.mirror.zip.stderr',
            ]
        )
    ):
        raise MatrixError("original download and evidence paths must be fresh")
    primary, mirror = inputs(arch)
    result = download(primary["id"], target)
    method = "PINNED_PRIMARY_ORIGINAL_ARCHIVE"
    if result.returncode:
        try:
            error = json.loads(target.read_bytes())
        except (ValueError, UnicodeError) as exc:
            raise MatrixError(
                "original download failed without verifiable expiry status"
            ) from exc
        if not isinstance(error, dict) or error.get("status") not in {"404", "410"}:
            raise MatrixError(
                "original download failed for a reason other than expiry/unavailability"
            )
        target.rename(target.with_name(target.name + ".primary-error.json"))
        archive = target.with_name(target.name + ".mirror.zip")
        response = download(mirror["id"], archive)
        if response.returncode:
            raise MatrixError("pinned original retention mirror unavailable")
        with checked_zip(archive, mirror["sha256"]) as bundle:
            if bundle.namelist() != [mirror["member"]]:
                raise MatrixError("unexpected original mirror archive member")
            with bundle.open(mirror["member"]) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
        method = "PINNED_RETENTION_MIRROR_SAME_ORIGINAL_BYTES"
    if target.is_symlink() or sha256(target) != primary["sha256"]:
        raise MatrixError(
            "sealed original archive identity mismatch; no substitute accepted"
        )
    receipt = {
        "status": "VERIFIED_ORIGINAL_TRANSPORT_ONLY",
        "architecture": arch,
        "method": method,
        "primary_artifact": primary,
        "retention_mirror": mirror if result.returncode else None,
        "scope": "Same original ZIP bytes only. OCI/config/rootfs verification and native execution remain separate required gates.",
    }
    target.with_name(target.name + ".receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fetch(args.arch, args.output_file), sort_keys=True))


if __name__ == "__main__":
    main()
