#!/usr/bin/env python3
"""Read and verify the immutable manifest digest in an OCI archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("digest", "verify-archive"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected")
    args = parser.parse_args()
    try:
        print(verify_archive(args.archive, args.expected))
    except CandidateError as exc:
        print(f"CANDIDATE DENIED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
