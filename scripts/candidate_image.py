#!/usr/bin/env python3
"""Read and verify the immutable manifest digest in an OCI archive.

The digest this returns is the identity the whole exact-SHA publication design
binds to: it is captured at build time, re-checked before the Trivy scan, and
re-checked again against what the registry stores after publication. It must
therefore be the digest of the object a registry push actually writes -- the
concrete platform *image manifest*.

``docker/build-push-action`` enables provenance attestations by default, and
BuildKit then wraps that image manifest in an index holding both the image and
an ``unknown/unknown`` attestation manifest. The archive's ``index.json`` points
at that wrapper, so its digest is not the digest of anything a push stores:
``skopeo copy`` resolves an oci-archive to the single entry matching the host
platform and writes that image manifest, discarding the attestation. Reading the
wrapper digest therefore compared two different objects and could never match.
Resolve through any wrapping index to the one publishable image manifest, and
verify every blob on the way down so the descent cannot be substituted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# Media types whose payload is a list of further manifests rather than an image.
INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)

# BuildKit marks provenance/SBOM manifests both ways; either alone is enough.
ATTESTATION_TYPE_ANNOTATION = "vnd.docker.reference.type"
ATTESTATION_REFERENCE_TYPE = "attestation-manifest"
UNKNOWN_PLATFORM = "unknown"

# An archive built for one platform nests one level. Anything deeper is
# malformed or hostile; bound the descent so it always terminates.
MAX_INDEX_DEPTH = 8


class CandidateError(ValueError):
    """The archive is missing, ambiguous, corrupt, or substituted."""


def _require_digest(value: object, context: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise CandidateError(f"{context} digest is missing or malformed")
    return value


def _read_manifest_blob(archive: tarfile.TarFile, digest: str) -> bytes:
    """Return the blob at ``digest``, proving its bytes hash to that digest."""
    blob = archive.extractfile(f"blobs/sha256/{digest[7:]}")
    if blob is None:
        raise CandidateError(f"OCI manifest blob {digest} is unreadable")
    payload = blob.read()
    if hashlib.sha256(payload).hexdigest() != digest[7:]:
        raise CandidateError("OCI manifest blob does not match index digest")
    return payload


def _is_attestation(entry: dict) -> bool:
    annotations = entry.get("annotations")
    if isinstance(annotations, dict):
        if annotations.get(ATTESTATION_TYPE_ANNOTATION) == ATTESTATION_REFERENCE_TYPE:
            return True
    platform = entry.get("platform")
    if isinstance(platform, dict):
        if UNKNOWN_PLATFORM in (platform.get("os"), platform.get("architecture")):
            return True
    return False


def _select_image_entry(manifests: object) -> dict:
    """Pick the one publishable manifest in an index, ignoring attestations.

    Fails closed on zero or on more than one: a push would have to choose, and
    guessing which one it chose is exactly the mistake this module exists to
    prevent.
    """
    if not isinstance(manifests, list):
        raise CandidateError("OCI index manifests is not a list")
    images = [entry for entry in manifests if isinstance(entry, dict) and not _is_attestation(entry)]
    if len(images) != 1:
        raise CandidateError(
            f"OCI index must resolve to exactly one image manifest, found {len(images)}"
        )
    return images[0]


def _as_index(payload: bytes, media_type: object) -> dict | None:
    """Return the parsed index if this manifest is one, else ``None``."""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    declared = document.get("mediaType")
    if declared in INDEX_MEDIA_TYPES or media_type in INDEX_MEDIA_TYPES:
        return document
    return None


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
            entry = manifests[0]
            if not isinstance(entry, dict):
                raise CandidateError("OCI manifest descriptor is malformed")
            digest = _require_digest(entry.get("digest"), "OCI manifest")
            media_type = entry.get("mediaType")
            payload = _read_manifest_blob(archive, digest)

            # Descend through any wrapping index to the image manifest a push
            # would store, re-verifying each blob so no step can be substituted.
            for _ in range(MAX_INDEX_DEPTH):
                document = _as_index(payload, media_type)
                if document is None:
                    break
                child = _select_image_entry(document.get("manifests"))
                digest = _require_digest(child.get("digest"), "OCI image manifest")
                media_type = child.get("mediaType")
                payload = _read_manifest_blob(archive, digest)
            else:
                raise CandidateError("OCI index nesting exceeds the permitted depth")
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
