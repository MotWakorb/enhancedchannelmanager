"""Candidate archive identity must fail closed before scan or publication."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "candidate_image.py"

IMAGE_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"


@pytest.fixture(scope="module")
def candidate():
    spec = importlib.util.spec_from_file_location("candidate_image", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(archive: tarfile.TarFile, name: str, value: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(value)
    archive.addfile(info, io.BytesIO(value))


def _blob(archive: tarfile.TarFile, value: bytes) -> str:
    digest = "sha256:" + hashlib.sha256(value).hexdigest()
    _write(archive, f"blobs/sha256/{digest[7:]}", value)
    return digest


def _archive(path: Path) -> str:
    manifest = b"candidate-manifest"
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    index = json.dumps({"manifests": [{"digest": digest}]}).encode()
    with tarfile.open(path, "w") as archive:
        for name, value in (("index.json", index), (f"blobs/sha256/{digest[7:]}", manifest)):
            _write(archive, name, value)
    return digest


def _buildx_archive(path: Path, *, attestation: bool = True) -> tuple[str, str]:
    """Reproduce the archive docker/build-push-action writes for one platform.

    Returns ``(image_manifest_digest, wrapping_index_digest)``. With provenance
    attestations enabled -- the action's default -- the root ``index.json``
    points at an *index* wrapping the image manifest alongside an
    ``unknown/unknown`` attestation manifest, so the two digests differ.
    """
    with tarfile.open(path, "w") as archive:
        config = _blob(archive, json.dumps({"architecture": "amd64", "os": "linux"}).encode())
        image = _blob(
            archive,
            json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": IMAGE_MEDIA_TYPE,
                    "config": {
                        "mediaType": "application/vnd.oci.image.config.v1+json",
                        "digest": config,
                    },
                    "layers": [],
                }
            ).encode(),
        )
        entries = [
            {
                "mediaType": IMAGE_MEDIA_TYPE,
                "digest": image,
                "platform": {"architecture": "amd64", "os": "linux"},
            }
        ]
        if attestation:
            attest = _blob(
                archive,
                json.dumps({"schemaVersion": 2, "mediaType": IMAGE_MEDIA_TYPE}).encode(),
            )
            entries.append(
                {
                    "mediaType": IMAGE_MEDIA_TYPE,
                    "digest": attest,
                    "annotations": {
                        "vnd.docker.reference.digest": image,
                        "vnd.docker.reference.type": "attestation-manifest",
                    },
                    "platform": {"architecture": "unknown", "os": "unknown"},
                }
            )
        inner = _blob(
            archive,
            json.dumps(
                {"schemaVersion": 2, "mediaType": INDEX_MEDIA_TYPE, "manifests": entries}
            ).encode(),
        )
        _write(
            archive,
            "index.json",
            json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": INDEX_MEDIA_TYPE,
                    "manifests": [{"mediaType": INDEX_MEDIA_TYPE, "digest": inner}],
                }
            ).encode(),
        )
    return image, inner


def test_exact_oci_manifest_digest_is_returned(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    expected = _archive(archive)
    assert candidate.verify_archive(archive) == expected


def test_digest_substitution_is_rejected(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    _archive(archive)
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive, "sha256:" + "0" * 64)


def test_missing_or_empty_manifest_digest_is_rejected(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    with tarfile.open(archive, "w") as output:
        _write(output, "index.json", b'{"manifests": []}')
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive)


def test_attestation_wrapped_archive_resolves_to_the_image_manifest(candidate, tmp_path):
    """The digest must be the image manifest a registry push actually stores.

    ``skopeo copy`` resolves an oci-archive to the single entry matching the
    host platform, so the wrapping index digest is never written to the
    registry. Binding to it made every publish fail its own equality check.
    """
    archive = tmp_path / "candidate.oci.tar"
    image, index = _buildx_archive(archive)
    assert image != index
    assert candidate.verify_archive(archive) == image
    assert candidate.verify_archive(archive, image) == image


def test_attestation_wrapped_archive_rejects_the_wrapping_index_digest(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    _, index = _buildx_archive(archive)
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive, index)


def test_single_platform_archive_without_attestations_still_resolves(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    image, _ = _buildx_archive(archive, attestation=False)
    assert candidate.verify_archive(archive) == image


def test_ambiguous_multi_platform_index_is_rejected(candidate, tmp_path):
    """Two publishable platforms give skopeo a choice; refuse rather than guess."""
    archive = tmp_path / "candidate.oci.tar"
    with tarfile.open(archive, "w") as output:
        first = _blob(output, json.dumps({"mediaType": IMAGE_MEDIA_TYPE}).encode())
        second = _blob(output, json.dumps({"mediaType": IMAGE_MEDIA_TYPE, "x": 1}).encode())
        inner = _blob(
            output,
            json.dumps(
                {
                    "mediaType": INDEX_MEDIA_TYPE,
                    "manifests": [
                        {
                            "mediaType": IMAGE_MEDIA_TYPE,
                            "digest": first,
                            "platform": {"architecture": "amd64", "os": "linux"},
                        },
                        {
                            "mediaType": IMAGE_MEDIA_TYPE,
                            "digest": second,
                            "platform": {"architecture": "arm64", "os": "linux"},
                        },
                    ],
                }
            ).encode(),
        )
        _write(output, "index.json", json.dumps({"manifests": [{"digest": inner}]}).encode())
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive)


def test_tampered_nested_manifest_blob_is_rejected(candidate, tmp_path):
    """Every descent step re-hashes the blob; a substituted child must fail."""
    archive = tmp_path / "candidate.oci.tar"
    with tarfile.open(archive, "w") as output:
        image = "sha256:" + hashlib.sha256(b"honest").hexdigest()
        _write(output, f"blobs/sha256/{image[7:]}", b"tampered")
        inner = _blob(
            output,
            json.dumps(
                {
                    "mediaType": INDEX_MEDIA_TYPE,
                    "manifests": [{"mediaType": IMAGE_MEDIA_TYPE, "digest": image}],
                }
            ).encode(),
        )
        _write(output, "index.json", json.dumps({"manifests": [{"digest": inner}]}).encode())
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive)


SBOM_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "buildx_sbom_oci_index.json"


def _sbom_archive(path: Path, *, attestation_manifests: int = 1) -> str:
    """Reproduce the archive buildx writes with BOTH `sbom:` and provenance on.

    The shape is taken from `buildx_sbom_oci_index.json`, recorded from a real
    `docker buildx build --sbom=true --provenance=true` run rather than guessed:
    one image manifest plus ONE `unknown/unknown` attestation manifest whose
    layers carry the SPDX document and the SLSA provenance side by side.

    `attestation_manifests` exists to cover the arrangement bead
    enhancedchannelmanager-3t0ht anticipated -- SBOM as a SECOND unknown/unknown
    child -- which the recorded run does not produce but a future BuildKit could.
    Either way the descent must reach the same single publishable manifest.
    """
    recorded = json.loads(SBOM_FIXTURE.read_text(encoding="utf-8"))
    with tarfile.open(path, "w") as archive:
        config = _blob(archive, json.dumps({"architecture": "amd64", "os": "linux"}).encode())
        image = _blob(
            archive,
            json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": IMAGE_MEDIA_TYPE,
                    "config": {
                        "mediaType": "application/vnd.oci.image.config.v1+json",
                        "digest": config,
                    },
                    "layers": [],
                }
            ).encode(),
        )
        entries = [
            {
                "mediaType": IMAGE_MEDIA_TYPE,
                "digest": image,
                "platform": {"architecture": "amd64", "os": "linux"},
            }
        ]
        for index in range(attestation_manifests):
            layers = recorded["attestation_manifest_layers"]
            if attestation_manifests > 1:
                layers = [layers[index % len(layers)]]
            attest = _blob(
                archive,
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "mediaType": IMAGE_MEDIA_TYPE,
                        "layers": [
                            {
                                "mediaType": layer["mediaType"],
                                "digest": _blob(archive, json.dumps(layer).encode()),
                                "annotations": layer["annotations"],
                            }
                            for layer in layers
                        ],
                    }
                ).encode(),
            )
            entries.append(
                {
                    "mediaType": IMAGE_MEDIA_TYPE,
                    "digest": attest,
                    "annotations": {
                        "vnd.docker.reference.digest": image,
                        "vnd.docker.reference.type": "attestation-manifest",
                    },
                    "platform": {"architecture": "unknown", "os": "unknown"},
                }
            )
        inner = _blob(
            archive,
            json.dumps(
                {"schemaVersion": 2, "mediaType": INDEX_MEDIA_TYPE, "manifests": entries}
            ).encode(),
        )
        _write(
            archive,
            "index.json",
            json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": INDEX_MEDIA_TYPE,
                    "manifests": [{"mediaType": INDEX_MEDIA_TYPE, "digest": inner}],
                }
            ).encode(),
        )
    return image


def test_recorded_buildx_shape_still_has_one_attestation_child():
    """Pin the observed structure: `sbom:` did not add a second child.

    If a future BuildKit changes this, the recorded fixture and this assertion
    are where the change becomes visible, instead of surfacing as a publication
    failure the way bead enhancedchannelmanager-5z48v did.
    """
    recorded = json.loads(SBOM_FIXTURE.read_text(encoding="utf-8"))
    children = recorded["inner_index"]["manifests"]
    assert len(children) == 2
    unknown = [
        child
        for child in children
        if child.get("platform", {}).get("architecture") == "unknown"
    ]
    assert len(unknown) == 1
    predicates = {
        layer["annotations"]["in-toto.io/predicate-type"]
        for layer in recorded["attestation_manifest_layers"]
    }
    assert predicates == {"https://spdx.dev/Document", "https://slsa.dev/provenance/v1"}


def test_sbom_bearing_archive_resolves_to_the_image_manifest(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    image = _sbom_archive(archive)
    assert candidate.verify_archive(archive) == image
    assert candidate.verify_archive(archive, image) == image


def test_sbom_as_a_second_unknown_child_still_resolves(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    image = _sbom_archive(archive, attestation_manifests=2)
    assert candidate.verify_archive(archive) == image


def test_sbom_bearing_archive_rejects_the_wrapping_index_digest(candidate, tmp_path):
    archive = tmp_path / "candidate.oci.tar"
    _sbom_archive(archive)
    with tarfile.open(archive) as opened:
        index = json.load(opened.extractfile("index.json"))
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive, index["manifests"][0]["digest"])


def test_index_recursion_is_bounded(candidate, tmp_path):
    """A deeply nested index must fail closed rather than spin."""
    archive = tmp_path / "candidate.oci.tar"
    with tarfile.open(archive, "w") as output:
        digest = _blob(
            output, json.dumps({"mediaType": INDEX_MEDIA_TYPE, "manifests": []}).encode()
        )
        for _ in range(12):
            digest = _blob(
                output,
                json.dumps(
                    {
                        "mediaType": INDEX_MEDIA_TYPE,
                        "manifests": [{"mediaType": INDEX_MEDIA_TYPE, "digest": digest}],
                    }
                ).encode(),
            )
        _write(output, "index.json", json.dumps({"manifests": [{"digest": digest}]}).encode())
    with pytest.raises(candidate.CandidateError):
        candidate.verify_archive(archive)
