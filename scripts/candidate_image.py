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
import gzip
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


def _hash_stream(stream) -> str:
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        checksum.update(chunk)
    return "sha256:" + checksum.hexdigest()


def _validate_runtime_identity(identity: object) -> dict:
    if not isinstance(identity, dict):
        raise CandidateError("runtime identity is not an object")
    for field in ("ManifestDigest", "ConfigDigest"):
        _require_digest(identity.get(field), field)
    if (identity.get("OS") != "linux" or not isinstance(identity.get("Architecture"), str)
            or identity.get("Architecture") not in {"amd64", "arm64"}):
        raise CandidateError("runtime identity platform is unsupported")
    diff_ids = identity.get("RootFSDiffIDs")
    if not isinstance(diff_ids, list) or not diff_ids:
        raise CandidateError("runtime identity rootfs is missing")
    for diff_id in diff_ids:
        _require_digest(diff_id, "rootfs diff ID")
    return identity


def runtime_config(path: Path, expected: str, architecture: str) -> dict:
    """Bind a native runtime to the verified manifest, config and layer bytes.

    Docker conversion may change the manifest's media type/digest. Its image ID
    must still be the original config hash, with the same uncompressed layer
    hashes. Verify both compressed blob hashes and config diff IDs here; do not
    trust descriptor strings or a transport-created tag as runtime evidence.
    Only BuildKit's gzip/uncompressed layers are supported (fail closed otherwise).
    """
    manifest_digest = verify_archive(path, expected)
    try:
        with tarfile.open(path, "r:*") as archive:
            manifest = json.loads(_read_manifest_blob(archive, manifest_digest))
            if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
                raise CandidateError("OCI image manifest is malformed")
            descriptor = manifest.get("config")
            if not isinstance(descriptor, dict):
                raise CandidateError("OCI config descriptor is missing")
            config_digest = _require_digest(descriptor.get("digest"), "OCI config")
            config = json.loads(_read_manifest_blob(archive, config_digest))
            if not isinstance(config, dict) or not isinstance(config.get("rootfs"), dict):
                raise CandidateError("OCI config/rootfs is malformed")
            rootfs = config["rootfs"]
            identity = _validate_runtime_identity({
                "ManifestDigest": manifest_digest, "ConfigDigest": config_digest,
                "OS": config.get("os"), "Architecture": config.get("architecture"),
                "RootFSDiffIDs": rootfs.get("diff_ids"),
            })
            if identity["Architecture"] != architecture or rootfs.get("type") != "layers":
                raise CandidateError("OCI config platform/rootfs substitution")
            layers = manifest.get("layers")
            if not isinstance(layers, list) or len(layers) != len(identity["RootFSDiffIDs"]):
                raise CandidateError("OCI manifest/config layer count mismatch")
            for layer, diff_id in zip(layers, identity["RootFSDiffIDs"]):
                if not isinstance(layer, dict):
                    raise CandidateError("OCI layer descriptor is malformed")
                digest = _require_digest(layer.get("digest"), "OCI layer")
                name = f"blobs/sha256/{digest[7:]}"
                with archive.extractfile(name) as stream:
                    if _hash_stream(stream) != digest:
                        raise CandidateError("OCI layer blob hash mismatch")
                with archive.extractfile(name) as stream:
                    media_type = layer.get("mediaType")
                    if media_type in {
                        "application/vnd.oci.image.layer.v1.tar+gzip",
                        "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    }:
                        with gzip.GzipFile(fileobj=stream) as uncompressed:
                            actual_diff_id = _hash_stream(uncompressed)
                    elif media_type == "application/vnd.oci.image.layer.v1.tar":
                        actual_diff_id = _hash_stream(stream)
                    else:
                        raise CandidateError("unsupported OCI layer compression/media type")
                if actual_diff_id != diff_id:
                    raise CandidateError("OCI rootfs diff ID does not match layer bytes")
            return identity
    except (OSError, EOFError, tarfile.TarError, KeyError, TypeError,
            json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CandidateError(f"invalid OCI runtime config: {exc}") from exc


def verify_loaded_config(identity: object, inspection: object) -> dict:
    """Check Docker inspect against the already hash-verified OCI config."""
    identity = _validate_runtime_identity(identity)
    if not isinstance(inspection, list) or len(inspection) != 1:
        raise CandidateError("Docker inspection must contain exactly one loaded image")
    loaded = inspection[0]
    if not isinstance(loaded, dict):
        raise CandidateError("Docker inspection is malformed")
    expected = {
        "Id": identity["ConfigDigest"], "Os": identity["OS"],
        "Architecture": identity["Architecture"],
        "RootFS": {"Type": "layers", "Layers": identity["RootFSDiffIDs"]},
    }
    if any(loaded.get(key) != value for key, value in expected.items()):
        raise CandidateError("loaded Docker image config/platform/rootfs substitution")
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("digest", "verify-archive", "runtime-config", "verify-loaded"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected")
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    parser.add_argument("--inspect", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "runtime-config":
            if not args.expected or not args.architecture:
                raise CandidateError("runtime-config requires --expected and --architecture")
            print(json.dumps(runtime_config(args.archive, args.expected, args.architecture)))
        elif args.command == "verify-loaded":
            if args.inspect is None:
                raise CandidateError("verify-loaded requires --inspect")
            print(json.dumps(verify_loaded_config(
                json.loads(args.archive.read_text()), json.loads(args.inspect.read_text())
            )))
        else:
            print(verify_archive(args.archive, args.expected))
    except (CandidateError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"CANDIDATE DENIED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
