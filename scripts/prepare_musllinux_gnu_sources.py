#!/usr/bin/env python3
"""Acquire and deliver the already-reviewed GNU wheel sources, never build them."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from build_native_runtime import BuildError, fetch_input
from collect_musllinux_gnu_sources import collect
from verify_musllinux_gnu import (
    AttributionError,
    cached_input,
    load_manifest,
    require,
    require_source_closure,
    sha256,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = "docker/python-third-party/gnu-source-assets/"
URLS = {
    "gcc-14.2.0.tar.xz": "https://ftp.gnu.org/gnu/gcc/gcc-14.2.0/gcc-14.2.0.tar.xz",
    "gcc-12.4.0.tar.xz": "https://ftp.gnu.org/gnu/gcc/gcc-12.4.0/gcc-12.4.0.tar.xz",
    "binutils-2.44.tar.gz": "https://ftp.gnu.org/gnu/binutils/binutils-2.44.tar.gz",
    "gmp-6.3.0.tar.xz": "https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
    "mpc-1.3.1.tar.gz": "https://ftp.gnu.org/gnu/mpc/mpc-1.3.1.tar.gz",
    "mpfr-4.2.2.tar.xz": "https://ftp.gnu.org/gnu/mpfr/mpfr-4.2.2.tar.xz",
    "musl-1.2.5.tar.gz": "https://musl.libc.org/releases/musl-1.2.5.tar.gz",
    "linux-headers-4.19.88-2.tar.xz": "https://ftp.barfooze.de/pub/sabotage/tarballs/linux-headers-4.19.88-2.tar.xz",
}


def source_inputs() -> tuple[dict, list[dict]]:
    manifest = load_manifest(
        ROOT / "docker/python-third-party/artifact-index/musllinux-gnu.json"
    )
    inputs = {}
    for family, closure in manifest["source_closures"].items():
        for row in closure["source_files"]:
            name = Path(row["path"]).name
            item = {"sha256": row["sha256"], "size": row["bytes"]}
            if name in URLS:
                item["url"] = URLS[name]
            elif name == "correspondence-proof.json.gz":
                item["local"] = (
                    "docs/design/evidence/"
                    + {
                        "alpine-gcc-14": "stable-alpine-musllinux-gnu-20261108.json.gz",
                        "rust-cross-gcc-12": "stable-alpine-gnu12-source-reproduction-20261108.json.gz",
                    }[family]
                )
            elif row["path"].startswith("notices/"):
                item["local"] = ASSETS + row["path"]
            else:
                item["local"] = ASSETS + (
                    "gnu12-reproduction.json" if name == "reproduction.json" else name
                )
            if item["sha256"] in inputs:
                require(
                    inputs[item["sha256"]]["size"] == item["size"],
                    "conflicting GNU source input identities",
                )
            else:
                inputs[item["sha256"]] = item
    return manifest, sorted(inputs.values(), key=lambda row: row["sha256"])


def canonical_directory(path: Path) -> None:
    require(
        path.is_dir() and not path.is_symlink() and path.absolute() == path.resolve(),
        "canonical GNU input/output directory required",
    )


def prepare(work: Path, output: Path, *, cache: Path | None = None) -> dict:
    require(
        not work.exists()
        and not work.is_symlink()
        and not output.exists()
        and not output.is_symlink(),
        "GNU preparation work/output must be fresh",
    )
    canonical_directory(work.parent)
    canonical_directory(output.parent)
    require(
        not output.resolve().is_relative_to(work.resolve())
        and not work.resolve().is_relative_to(output.resolve()),
        "GNU work/output must be disjoint",
    )
    manifest, inputs = source_inputs()
    # Validate ALL local/explicit-cache inputs before any network or output.
    local = {}
    if cache is not None:
        canonical_directory(cache)
        require(
            not work.resolve().is_relative_to(cache)
            and not output.resolve().is_relative_to(cache),
            "GNU cache/output must be disjoint",
        )
        require(
            {path.name for path in cache.iterdir()}
            == {row["sha256"] for row in inputs} | {"input-plan.json"},
            "complete explicit GNU input cache required",
        )
        require(
            (cache / "input-plan.json").is_file()
            and (cache / "input-plan.json").absolute()
            == (cache / "input-plan.json").resolve()
            and (cache / "input-plan.json").stat().st_size <= 1024**2,
            "regular bounded GNU cached source plan required",
        )
        require(
            (cache / "input-plan.json").read_text()
            == json.dumps(inputs, sort_keys=True, indent=2) + "\n",
            "GNU cached source plan differs",
        )
    for row in inputs:
        if cache is not None:
            path = cached_input(cache, row["sha256"])
        elif "local" in row:
            path = ROOT / row["local"]
            require(
                path.absolute() == path.resolve()
                and path.is_file()
                and not path.is_symlink()
                and sha256(path) == row["sha256"],
                "retained GNU source asset differs",
            )
        else:
            continue
        require(path.stat().st_size == row["size"], "GNU source input size differs")
        local[row["sha256"]] = path
    work.mkdir(mode=0o755)
    for row in inputs:
        target = work / row["sha256"]
        if row["sha256"] in local:
            shutil.copyfile(local[row["sha256"]], target)
        else:
            fetch_input(row, target, None)
        require(
            target.is_file()
            and not target.is_symlink()
            and sha256(target) == row["sha256"]
            and target.stat().st_size == row["size"],
            "prepared GNU input identity differs",
        )
        target.chmod(0o644)
    (work / "input-plan.json").write_text(
        json.dumps(inputs, sort_keys=True, indent=2) + "\n"
    )
    copied = collect(manifest, work, output)
    require_source_closure(manifest, output)
    return {
        **copied,
        "status": "REVIEWED_GNU_SOURCE_DELIVERY_ONLY",
        "scope": "All reviewed GNU source/recipe/notice/proof bytes acquired and delivered without execution; original source statuses unchanged. No new source reproduction, binary repair, installed-image binding, other component source/advisory or aggregate clearance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    try:
        receipt = args.receipt_file
        canonical_directory(receipt.parent)
        require(
            not receipt.exists()
            and not receipt.is_symlink()
            and not any(
                receipt.resolve().is_relative_to(path.resolve())
                for path in [
                    args.work_dir,
                    args.output_dir,
                    *([args.cache_dir] if args.cache_dir else []),
                ]
            ),
            "fresh external GNU preparation receipt required",
        )
        report = prepare(args.work_dir, args.output_dir, cache=args.cache_dir)
        with receipt.open("x") as stream:
            stream.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    except (
        AttributionError,
        BuildError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        parser.exit(1, "GNU source preparation failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
