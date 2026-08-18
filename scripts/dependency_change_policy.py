#!/usr/bin/env python3
"""Conservatively classify whether a diff needs dependency security gates."""

from __future__ import annotations

import argparse
from pathlib import Path

DEPENDENCY_FILES = frozenset(
    {
        "frontend/package.json",
        "frontend/package-lock.json",
        "mcp-server/requirements.txt",
        "Dockerfile",
        "mcp-server/Dockerfile",
    }
)


def _valid_path(path: str) -> bool:
    return (
        bool(path)
        and path == path.strip()
        and not path.startswith(("/", "./"))
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def is_dependency_input(path: str) -> bool:
    if path in DEPENDENCY_FILES:
        return True
    return path.startswith("backend/requirements") and path.endswith((".in", ".txt"))


def has_dependency_change(paths: list[str]) -> bool:
    """Unknown/empty input returns True so acquisition failure cannot bypass scans."""
    if not paths or any(not _valid_path(path) for path in paths):
        return True
    return any(is_dependency_input(path) for path in paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nul_file", type=Path)
    args = parser.parse_args()
    try:
        raw = args.nul_file.read_bytes()
        if not raw or not raw.endswith(b"\0"):
            paths: list[str] = []
        else:
            paths = [item.decode("utf-8") for item in raw[:-1].split(b"\0")]
    except (OSError, UnicodeDecodeError):
        paths = []
    verdict = "true" if has_dependency_change(paths) else "false"
    print(f"dependency_files_changed={verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
