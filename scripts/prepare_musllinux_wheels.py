#!/usr/bin/env python3
"""Acquire the reviewed native wheel selection without resolving or changing pins."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from inventory_musllinux_wheels import INVENTORY_SHA256, ROOT, normalized
from verify_musllinux_gnu import AttributionError, require, sha256

__all__ = ["AttributionError", "acquire", "selection"]


def selection(arch: str) -> list[dict]:
    path = (
        ROOT
        / "docker/python-third-party/artifact-index/musllinux"
        / f"linux-{arch}.json"
    )
    require(
        arch in INVENTORY_SHA256
        and not path.is_symlink()
        and sha256(path) == INVENTORY_SHA256[arch],
        "reviewed native wheel inventory required",
    )
    payload = json.loads(path.read_text())
    pins_path = ROOT / "backend/requirements.txt"
    pins = {
        normalized(name): version
        for name, version in re.findall(
            r"^([A-Za-z0-9_.-]+)==([^\s]+)$", pins_path.read_text(), re.MULTILINE
        )
    }
    rows = payload["distributions"]
    require(
        payload["architecture"] == arch
        and payload["requirements_sha256"] == sha256(pins_path)
        and len(rows) == len(pins) == 74
        and {row["name"]: row["version"] for row in rows} == pins,
        "complete unchanged native lock selection required",
    )
    return rows


def acquire(row: dict, target: Path, cache: Path | None = None) -> dict:
    filename = row["filename"]
    require(
        Path(filename).name == filename
        and re.fullmatch("[0-9a-f]{64}", row["sha256"]) is not None,
        "safe whole-wheel identity required",
    )
    require(
        not target.exists() and not target.is_symlink(),
        "wheel destination must be fresh",
    )
    if cache is not None:
        source = cache / filename
        require(
            source.is_file()
            and not source.is_symlink()
            and sha256(source) == row["sha256"],
            "local whole-wheel cache identity mismatch",
        )
        shutil.copyfile(source, target)
        receipt = {"method": "REHASHED_LOCAL_WHOLE_WHEEL_CACHE"}
    else:
        url = row["pypi_artifact"]["url"]
        parsed = urlparse(url)
        require(
            parsed.scheme == "https"
            and parsed.hostname == "files.pythonhosted.org"
            and not parsed.username
            and not parsed.password,
            "reviewed PyPI artifact endpoint required",
        )
        with urlopen(url, timeout=90) as response, target.open("xb") as stream:
            shutil.copyfileobj(response, stream)
            receipt = {
                "method": "HASH_VERIFIED_HTTPS_ARTIFACT",
                "url": url,
                "http_status": response.status,
                "headers": dict(response.headers),
            }
    require(sha256(target) == row["sha256"], "downloaded whole-wheel identity mismatch")
    return {**receipt, "filename": filename, "sha256": row["sha256"]}


def prepare(arch: str, output: Path, cache: Path | None = None) -> dict:
    require(
        not output.exists() and not output.is_symlink(),
        "wheel preparation output must be fresh",
    )
    if cache is not None:
        require(
            not output.resolve().is_relative_to(cache.resolve())
            and not cache.resolve().is_relative_to(output.resolve()),
            "wheel cache/output must be disjoint",
        )
    rows = selection(arch)
    output.mkdir(parents=True)
    receipts = []
    for row in rows:
        receipts.append(acquire(row, output / row["filename"], cache))
        print("Verified " + row["filename"], flush=True)
    (output / "requirements.txt").write_text(
        "".join(
            f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
            for row in rows
        )
    )
    result = {
        "status": "VERIFIED_NATIVE_WHEEL_INPUTS_ONLY",
        "architecture": arch,
        "inventory_sha256": INVENTORY_SHA256[arch],
        "wheels": receipts,
        "scope": "Wheel preparation only; no installation, execution, source/advisory or whole-image approval.",
    }
    (output / "input-receipt.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    prepare(args.arch, args.output_dir, args.cache_dir)


if __name__ == "__main__":
    main()
