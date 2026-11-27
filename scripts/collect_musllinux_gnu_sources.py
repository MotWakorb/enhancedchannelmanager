#!/usr/bin/env python3
"""Deliver reviewed GNU source archives/recipes/notices from an offline cache.

Copying a candidate's bytes never authenticates that candidate. Source status
is retained unchanged; the independent GNU verifier checks closure and bytes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path, PurePosixPath

from verify_musllinux_gnu import (
    AttributionError,
    cached_input,
    load_manifest,
    require,
    sha256,
)


def source_path(name: str) -> Path:
    path = PurePosixPath(name)
    require(
        name
        and not any(character.isspace() for character in name)
        and not path.is_absolute()
        and ".." not in path.parts
        and str(path) == name,
        "unsafe delivered GNU source path",
    )
    return Path(name)


def collect(manifest: dict, cache: Path, output: Path) -> dict:
    require(
        not output.exists() and not output.is_symlink(),
        "GNU source delivery must be fresh",
    )
    require(
        not output.resolve().is_relative_to(cache.resolve())
        and not cache.resolve().is_relative_to(output.resolve()),
        "GNU source cache/delivery must be disjoint",
    )
    files = []
    statuses = {}
    for family, record in manifest["source_closures"].items():
        require(
            re.fullmatch(r"[a-z0-9-]+", family) is not None, "unsafe source family name"
        )
        inventory = record.get("source_files", [])
        require(
            inventory and len({row["path"] for row in inventory}) == len(inventory),
            "complete nonduplicated GNU source inventory required",
        )
        statuses[family] = record["status"]
        for row in inventory:
            target = Path(family) / source_path(row["path"])
            source = cached_input(cache, row["sha256"])
            files.append((source, target, row["sha256"]))
    require(files, "nonempty GNU source inventory required")
    # All inputs must pass before any output is created. No source archive is
    # unpacked or executed, and no caller-supplied destination is overwritten.
    output.mkdir(parents=True)
    inventory = []
    for source, relative, expected in files:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
        require(sha256(target) == expected, "delivered GNU source bytes mismatch")
        inventory.append(
            {"path": str(relative), "sha256": expected, "bytes": target.stat().st_size}
        )
    return {
        "schema_version": 1,
        "status": "SOURCE_BYTES_COPIED_ONLY",
        "source_statuses": statuses,
        "source_files": inventory,
        "scope": "Exact source delivery only. Candidate authentication status unchanged; not binary/source/advisory/image clearance.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt, output = args.receipt_file.absolute(), args.output_dir.absolute()
        require(
            not receipt.exists()
            and not receipt.is_symlink()
            and not receipt.resolve().is_relative_to(output.resolve()),
            "GNU source receipt must be fresh and outside the source inventory",
        )
        require(
            not receipt.resolve().is_relative_to(args.cache_dir.resolve()),
            "GNU source receipt must not modify its input cache",
        )
        result = collect(
            load_manifest(args.manifest), args.cache_dir.absolute(), output
        )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    except (AttributionError, OSError, ValueError, KeyError) as exc:
        print("GNU source collection failed: " + str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
