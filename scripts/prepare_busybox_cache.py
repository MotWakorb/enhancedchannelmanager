#!/usr/bin/env python3
"""Acquire the approved BusyBox source before offline compilation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_busybox_provider import load_manifest
from build_native_runtime import _require, fetch_input

ROOT = Path(__file__).resolve().parents[1]


def prepare(output: Path) -> None:
    _require(
        not output.exists() and not output.is_symlink(),
        "BusyBox source cache must be fresh",
    )
    recipe = load_manifest(ROOT / "docker/alpine-busybox/manifest.json")
    output.mkdir(parents=True)
    fetch_input(recipe["source"], output / recipe["source"]["sha256"], None)
    (output / "input-plan.json").write_text(
        json.dumps(recipe["source"], sort_keys=True, indent=2) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output_dir)


if __name__ == "__main__":
    main()
