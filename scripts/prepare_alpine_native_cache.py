#!/usr/bin/env python3
"""Fetch the complete reviewed native inputs before networkless compilation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_alpine_codecs import load_recipe as codecs_recipe
from build_alpine_ffmpeg import load_recipe as ffmpeg_recipe
from build_alpine_jxl import load_recipe as jxl_recipe
from build_alpine_security_libraries import load_recipe as security_recipe
from build_native_runtime import BuildError, _require, fetch_input

ROOT = Path(__file__).resolve().parents[1]


def inputs() -> list[dict]:
    assets = ROOT / "docker/alpine-native"
    codecs = codecs_recipe(assets / "codecs.json")
    security = security_recipe(assets / "security-libraries.json")
    ffmpeg = ffmpeg_recipe(assets / "ffmpeg.json", ROOT / "docker/native")
    jxl = jxl_recipe(assets / "libjxl.json")
    return [
        *[p["archive"] for p in codecs["libraries"] + security["libraries"]],
        jxl["archive"],
        ffmpeg["ffmpeg"],
        ffmpeg["ffmpeg"]["signature"],
    ]


def prepare(output: Path) -> None:
    _require(
        not output.exists() and not output.is_symlink(), "source cache must be fresh"
    )
    plan = inputs()
    output.mkdir(parents=True)
    for item in plan:
        fetch_input(item, output / item["sha256"], None)
    (output / "input-plan.json").write_text(json.dumps(plan, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.output_dir)
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit(f"Alpine input preparation failed: {exc}") from exc


if __name__ == "__main__":
    main()
