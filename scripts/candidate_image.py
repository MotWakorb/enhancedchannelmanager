#!/usr/bin/env python3
"""Read and verify the immutable manifest digest in an OCI archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class CandidateError(ValueError):
    """The archive is missing, ambiguous, corrupt, or substituted."""


def verify_archive(path: Path, expected: str | None = None) -> str:
    try:
        with tarfile.open(path, "r:*") as archive:
            index_member = archive.getmember("index.json")
            index_file = archive.extractfile(index_member)
            if index_file is None:
                raise CandidateError("OCI index.json is unreadable")
            index = json.load(index_file)
            manifests = index.get("manifests")
            if not isinstance(manifests, list) or len(manifests) != 1:
                raise CandidateError("OCI archive must contain exactly one manifest")
            digest = manifests[0].get("digest")
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise CandidateError("OCI manifest digest is missing or malformed")
            blob = archive.extractfile(f"blobs/sha256/{digest[7:]}")
            if blob is None or hashlib.sha256(blob.read()).hexdigest() != digest[7:]:
                raise CandidateError("OCI manifest blob does not match index digest")
    except (OSError, tarfile.TarError, KeyError, json.JSONDecodeError) as exc:
        raise CandidateError(f"invalid OCI candidate archive: {exc}") from exc
    if expected is not None and digest != expected:
        raise CandidateError(f"candidate digest substitution: expected {expected}, got {digest}")
    return digest


def extract_archive(path: Path, destination: Path, expected: str | None = None) -> str:
    """Verify an OCI archive, then safely materialize its exact layout for scanners."""
    digest = verify_archive(path, expected)
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            for member in members:
                target = (root / member.name).resolve()
                if (
                    (target != root and root not in target.parents)
                    or not (member.isdir() or member.isfile())
                ):
                    raise CandidateError(f"unsafe OCI archive member: {member.name}")
            for member in members:
                target = root / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise CandidateError(f"OCI archive member is unreadable: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, tarfile.TarError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise CandidateError(f"cannot extract OCI candidate archive: {exc}") from exc
    except CandidateError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("digest", "verify-archive", "extract"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "extract":
            if args.destination is None:
                parser.error("extract requires --destination")
            print(extract_archive(args.archive, args.destination, args.expected))
        else:
            print(verify_archive(args.archive, args.expected))
    except CandidateError as exc:
        print(f"CANDIDATE DENIED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
