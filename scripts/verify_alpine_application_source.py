#!/usr/bin/env python3
"""Verify the pinned application source plus the recorded GNU date adaptation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SHA256 = "a3520f9549250c896d90c5d8477fb5cdf24bc56f9829e83ce9eff4ba2c9e2156"
IGNORED_DIRECTORIES = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".hypothesis",
        ".mypy_cache",
        ".ruff_cache",
    }
)
IGNORED_PREFIXES = (
    "backend/tests/",
    "frontend/dist/",
    "frontend/coverage/",
    "frontend/playwright-report/",
    "frontend/test-results/",
)


class SourceError(RuntimeError):
    """Source content or coverage differs from the reviewed application."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path) -> dict:
    require(
        path.is_file() and not path.is_symlink() and sha256(path) == MANIFEST_SHA256,
        "reviewed application source manifest identity mismatch",
    )
    return json.loads(path.read_text())


def included(path: Path) -> bool:
    name = path.as_posix()
    return (
        not any(part in IGNORED_DIRECTORIES for part in path.parts)
        and not any(
            name == prefix.rstrip("/") or name.startswith(prefix)
            for prefix in IGNORED_PREFIXES
        )
        and not name.endswith(".pyc")
    )


def verify(root: Path, manifest: dict) -> dict:
    rows = manifest["files"]
    require(
        rows and len({row["path"] for row in rows}) == len(rows),
        "complete unique application source map required",
    )
    for row in rows:
        name = PurePosixPath(row["path"])
        require(
            not name.is_absolute()
            and ".." not in name.parts
            and name.parts[0] in {"backend", "frontend", "mcp-server"},
            "unsafe application source path",
        )
        path = root / name
        require(
            path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve()),
            "regular application source file required: " + row["path"],
        )
        require(
            sha256(path) == row["sha256"]
            and bool(path.stat().st_mode & 0o111) == row["executable"],
            "application source bytes or executable bit differ: " + row["path"],
        )
    actual = set()
    for name in ["backend", "frontend", "mcp-server"]:
        directory = root / name
        if not directory.exists():
            continue
        for current, directories, files in os.walk(directory, followlinks=False):
            for name in list(directories):
                path = Path(current) / name
                relative = path.relative_to(root)
                if not included(relative):
                    directories.remove(name)
                else:
                    require(
                        not path.is_symlink(),
                        "application source directory symlink forbidden: "
                        + str(relative),
                    )
            for name in files:
                path = Path(current) / name
                relative = path.relative_to(root)
                if included(relative):
                    require(
                        not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode),
                        "regular application source file required: " + str(relative),
                    )
                    actual.add(relative.as_posix())
    expected = {row["path"] for row in rows}
    require(
        actual == expected,
        "application source coverage mismatch: "
        + repr(
            {"missing": sorted(expected - actual), "extra": sorted(actual - expected)}
        ),
    )
    return {
        "status": "PINNED_APPLICATION_SOURCE_ONLY",
        "file_count": len(rows),
        "manifest_sha256": MANIFEST_SHA256,
        "baseline_application_commit": manifest.get("baseline_application_commit"),
        "adaptations": [
            row
            for row in rows
            if "adaptation_commit" in row or "adaptation_reason" in row
        ],
        "scope": "Exact tracked application input content plus the recorded date and container-base compatibility adaptations, excluding backend tests and Docker-excluded build/cache artifacts. Not runtime or advisory approval.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    require(
        not args.output_file.exists() and not args.output_file.is_symlink(),
        "application source evidence must be fresh",
    )
    record = verify(
        args.root, load_manifest(ROOT / "docker/alpine-native/application-source.json")
    )
    args.output_file.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
