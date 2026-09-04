#!/usr/bin/env python3
"""Generate and verify the per-release SPDX 2.3 software bill of materials.

WHAT THIS DOCUMENT IS, AND WHAT IT IS NOT
-----------------------------------------
This is a **source-manifest** SBOM. It is derived from the dependency manifests
in the repository -- ``backend/requirements.txt``, ``frontend/package-lock.json``,
``mcp-server/requirements.txt``, and ``sbom/native-dependencies.json`` -- and from
the digest-pinned base images named in the two Dockerfiles. It is **not** an image
SBOM: it does not enumerate the Debian or Alpine packages inside those base
images, and it does not name the published subject image digest, because at
release-cut time no release image exists yet. Digest-pinned base and build image
references are recorded.

That limitation is deliberate and load-bearing, so it is stated in the document
itself (``creationInfo.comment``) as well as here. An SBOM whose coverage is
misunderstood is worse than no SBOM, because it will be believed.

WHY NOT AN IMAGE SBOM (bead enhancedchannelmanager-3t0ht)
---------------------------------------------------------
An image SBOM has to be generated from an image, and the images a release
publishes are built *after* the release branch is frozen: ``build.yml`` runs on
the push to ``main`` that the release PR's merge commit creates. That merge
commit has a different SHA from the release branch tip, and ``Dockerfile`` bakes
``GIT_COMMIT`` into the ECM image as an ``ENV``, so the image built from the
release branch and the image published from ``main`` are guaranteed to have
different digests even though their contents are otherwise identical. Building
on ``release/**`` and inventorying *that* image would therefore commit a
document naming a digest that is not, and never will be, in the registry.

TWO KINDS OF DIRECTORY, SEPARATED BY PATH RATHER THAN BY CONVENTION
-------------------------------------------------------------------
``sbom/vX.Y.Z/`` is a **release record**. Release records accumulate and are kept
forever: when an advisory lands the question is *which shipped versions contain
the affected package*, and that is only answerable if the history is there.

``sbom/dev/`` is a **transient snapshot** of whatever ``dev`` currently carries.
There is at most one, and it is superseded rather than accumulated. It is not an
artifact of record and no released version is described by it.

The two are told apart by the **shape of the version string**, which decides the
path, so they cannot be confused and cannot occupy each other's namespace.
``docs/versioning.md`` §Format: a release drops the ``-BUILD`` suffix, so
``X.Y.Z`` is a release and ``X.Y.Z-NNNN`` is a dev build. ``channel_for`` is the
single place that judgement is made and ``directory_for`` is the single place a
path is derived from it, so ``generate --version 0.18.1-0147`` cannot create a
release directory no matter who types it.

This mattered: ``sbom/v0.18.1-0144/`` was committed for a build number that was
never released, and its contents became wrong when a 60-package dependency sweep
landed one build later. A directory holding an inventory matching nothing that
ever shipped is worse than no directory, because it is the first thing somebody
finds when they go looking during an incident.

BINDING
-------
Because there is no published subject image digest to bind to, this document
binds to what it can actually be checked against: the exact bytes of every
manifest it was derived from. ``sbom/vX.Y.Z/index.json`` records the SHA-256 of
each source manifest, and ``verify`` regenerates the documents from the working
tree and byte-compares them. Editing a requirement without regenerating,
hand-editing a document, or committing a document for the wrong version all fail
the check. The only field carried over from the committed document rather than
recomputed is ``creationInfo.created``, which is a timestamp and cannot be
re-derived; every other byte is re-derived from the tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

SPDX_VERSION = "SPDX-2.3"
DATA_LICENSE = "CC0-1.0"
NOASSERTION = "NOASSERTION"
INDEX_SCHEMA = 1
GENERATOR_FORMAT_VERSION = "4"
NATIVE_DEPENDENCIES_PATH = "sbom/native-dependencies.json"

# SPDX 2.3 section 6.5 requires a new namespace when a document is updated.
# Content-addressing the namespace-free document provides that uniqueness while
# remaining reproducible for `verify`.
NAMESPACE_ROOT = "https://github.com/motwakorb/enhancedchannelmanager/sbom"

PINNED_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;#]+)\s*$")
DOCKER_IMAGE_REF = re.compile(
    r"^(?P<image>[^\s@:]+(?::[^\s@]+)?)@(?P<digest>sha256:[0-9a-f]{64})$"
)
DOCKER_INSTRUCTION_WHITESPACE = " \t\v\f\r"
DOCKER_INSTRUCTION_WHITESPACE_PATTERN = r"[\t\v\f\r ]+"
DOCKER_IMAGE_INSTRUCTION = re.compile(
    rf"^(?P<instruction>FROM|COPY)(?:{DOCKER_INSTRUCTION_WHITESPACE_PATTERN}"
    r"(?P<arguments>.*))?$",
    re.IGNORECASE,
)
SPDX_ID_UNSAFE = re.compile(r"[^A-Za-z0-9.-]")

# docs/versioning.md §Format: `BUILD` is a zero-padded CI build counter used on
# dev builds only, and a release cut drops the `-BUILD` suffix entirely. So the
# version string alone says which kind of directory it belongs in.
CHANNEL_RELEASE = "release"
CHANNEL_DEV = "dev"
DEV_DIRNAME = "dev"
RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
DEV_VERSION = re.compile(r"^\d+\.\d+\.\d+-\d{4,}$")

CHANNEL_NOTE = {
    CHANNEL_RELEASE: (
        "Release record. Kept forever alongside every other release, because "
        "answering 'which shipped versions contain this package' requires the "
        "history to be present."
    ),
    CHANNEL_DEV: (
        "Transient snapshot of the dev branch, NOT an artifact of record. There "
        "is at most one and it is superseded rather than accumulated. The "
        "version below names the build this snapshot was taken at, not what dev "
        "carries now; the binding that matters is the source-manifest hashes."
    ),
}

COVERAGE_INCLUDES = (
    "Python distributions pinned in the image's requirements.txt, with versions",
    "npm packages resolved in frontend/package-lock.json, with versions (ECM only)",
    "Direct source-built native dependencies declared in sbom/native-dependencies.json",
    "Base and build images referenced by digest in the Dockerfile",
)
COVERAGE_EXCLUDES = (
    "Operating-system packages inside the base images (apt/apk); the Dockerfiles "
    "also run `apt-get upgrade` / `apk upgrade` at build time, so the installed "
    "OS package set is not derivable from the source tree at all",
    "Transitive native libraries vendored inside Python wheels",
    "Published subject image digest: no release image exists when this document is cut",
)

CREATION_COMMENT = (
    "Source-manifest SBOM generated by scripts/generate_sbom.py from the "
    "repository's dependency manifests, including direct source-built native "
    "dependencies. It is NOT an image SBOM: the OS package "
    "set inside the digest-pinned base images is not enumerated, and no subject "
    "image digest is asserted. Digest-pinned base and build image references are "
    "recorded. See sbom/README.md for the full coverage statement."
)


class SbomError(ValueError):
    """The tree, the arguments, or a committed document is unusable."""


def _spdx_id(*parts: str) -> str:
    return "SPDXRef-" + "-".join(SPDX_ID_UNSAFE.sub(".", part) for part in parts)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SbomError(f"cannot read {path}: {exc}") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise SbomError(f"{path} is not valid JSON: {exc.msg}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SbomError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def parse_pinned_requirements(text: str, source: str) -> list[dict[str, str]]:
    """Return every ``name==version`` pin, rejecting anything unpinned.

    ``uv pip compile`` output is fully pinned by construction. A line that is
    not a pin means the manifest shape changed, and guessing at it would put a
    wrong version in a document people make patching decisions from -- so fail.
    """
    packages: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = PINNED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise SbomError(f"{source} line {number} is not a simple pin: {line!r}")
        packages.append({"name": match["name"], "version": match["version"]})
    if not packages:
        raise SbomError(f"{source} declares no pinned requirements")
    return packages


def parse_lockfile_packages(document: Any, source: str) -> list[dict[str, Any]]:
    """Return every resolved npm package in an npm lockfile v2/v3 ``packages`` map."""
    if not isinstance(document, dict):
        raise SbomError(f"{source} is not a JSON object")
    if document.get("lockfileVersion") not in (2, 3):
        raise SbomError(
            f"{source} has unsupported lockfileVersion "
            f"{document.get('lockfileVersion')!r}; only v2/v3 are understood"
        )
    entries = document.get("packages")
    if not isinstance(entries, dict) or not entries:
        raise SbomError(f"{source} has no 'packages' map")
    packages: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for location, entry in entries.items():
        if not isinstance(entry, dict):
            raise SbomError(f"{source} entry {location!r} is not an object")
        for field in ("link", "dev"):
            if field in entry and type(entry[field]) is not bool:
                raise SbomError(
                    f"{source} entry {location!r} field {field!r} must be a boolean"
                )
        for field in ("license", "resolved", "integrity", "name"):
            if field in entry and type(entry[field]) is not str:
                raise SbomError(
                    f"{source} entry {location!r} field {field!r} must be a string"
                )
        if location == "":
            continue
        if entry.get("link") is True:
            continue
        marker = "node_modules/"
        if marker not in location:
            raise SbomError(f"{source} entry {location!r} is not a node_modules path")
        name = entry.get("name") or location[location.rindex(marker) + len(marker) :]
        version = entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not version:
            raise SbomError(f"{source} entry {location!r} has no resolved name/version")
        package = {
            "name": name,
            "version": version,
            "dev": entry.get("dev", False),
            "license": entry.get("license"),
            "resolved": entry.get("resolved"),
            "integrity": entry.get("integrity"),
        }
        identity = (name, version)
        previous = packages.get(identity)
        if previous is not None:
            previous_package, previous_location = previous
            if previous_package != package:
                raise SbomError(
                    f"{source} entries {previous_location!r} and {location!r} resolve "
                    f"duplicate identity {name!r}@{version!r} with conflicting metadata"
                )
            continue
        packages[identity] = (package, location)
    if not packages:
        raise SbomError(f"{source} resolves no packages")
    return [package for package, _location in packages.values()]


def parse_dockerfile_images(text: str, source: str) -> list[dict[str, str]]:
    """Return every digest-pinned image the Dockerfile pulls, runtime last.

    Both ``FROM ref@sha256:...`` and ``COPY --from=ref@sha256:...`` are pulled
    into the build, so both belong in the inventory. An unpinned reference is
    rejected: an SBOM naming a floating tag records nothing verifiable.
    """
    images: dict[tuple[str, str], dict[str, str]] = {}
    stage_base: dict[str, tuple[str, str]] = {}
    runtime_key: tuple[str, str] | None = None

    def _record(entry: dict[str, str]) -> tuple[str, str]:
        key = (entry["image"], entry["digest"])
        images.setdefault(key, entry)
        return key

    # Docker physical lines end at LF; CR is also valid instruction whitespace
    # and must not split an instruction when it appears anywhere else.
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip(DOCKER_INSTRUCTION_WHITESPACE)
        match = DOCKER_IMAGE_INSTRUCTION.fullmatch(line)
        if match is None:
            continue
        instruction = match["instruction"].upper()
        arguments = (match["arguments"] or "").strip(DOCKER_INSTRUCTION_WHITESPACE)
        if instruction == "FROM":
            tokens = (
                re.split(DOCKER_INSTRUCTION_WHITESPACE_PATTERN, arguments)
                if arguments
                else []
            )
            if not tokens:
                raise SbomError(f"{source} line {number} has a bare FROM")
            reference = tokens[0]
            if reference in stage_base:
                key = stage_base[reference]
            else:
                key = _record(_pinned_image(reference, source, number, "base"))
            if len(tokens) >= 3 and tokens[1].upper() == "AS":
                stage_base[tokens[2]] = key
            runtime_key = key
        elif "--from=" in arguments:
            reference = re.split(
                DOCKER_INSTRUCTION_WHITESPACE_PATTERN,
                arguments.split("--from=", 1)[1],
                maxsplit=1,
            )[0]
            if reference in stage_base:
                continue
            _record(_pinned_image(reference, source, number, "build"))
    if runtime_key is None:
        raise SbomError(f"{source} has no FROM instruction")
    # An image that is both an intermediate base and the final base is one
    # image; record it once, under the role that describes what ships.
    images[runtime_key]["role"] = "runtime"
    return [images[key] for key in sorted(images)]


def parse_native_dependencies(
    document: Any, subject: str, source: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return explicitly declared source-built packages and SPDX relationships."""
    if not isinstance(document, dict) or not isinstance(document.get("subjects"), dict):
        raise SbomError(f"{source} must contain a 'subjects' object")
    subject_entry = document["subjects"].get(subject, {"packages": [], "relationships": []})
    if not isinstance(subject_entry, dict):
        raise SbomError(f"{source} subject {subject!r} must be an object")
    packages = subject_entry.get("packages")
    relationships = subject_entry.get("relationships")
    if not isinstance(packages, list) or not isinstance(relationships, list):
        raise SbomError(
            f"{source} subject {subject!r} must contain package and relationship lists"
        )

    required_package_fields = (
        "id",
        "name",
        "version",
        "license",
        "downloadLocation",
        "purl",
    )
    parsed_packages: list[dict[str, str]] = []
    aliases = {subject}
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            raise SbomError(f"{source} {subject} package {index} is not an object")
        for field in required_package_fields:
            if not isinstance(package.get(field), str) or not package[field]:
                raise SbomError(
                    f"{source} {subject} package {index} has no {field!r} string"
                )
        if package["id"] in aliases:
            raise SbomError(
                f"{source} {subject} repeats native package id {package['id']!r}"
            )
        aliases.add(package["id"])
        parsed_packages.append({field: package[field] for field in required_package_fields})

    parsed_relationships: list[dict[str, str]] = []
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            raise SbomError(f"{source} {subject} relationship {index} is not an object")
        for field in ("source", "type", "target"):
            if not isinstance(relationship.get(field), str) or not relationship[field]:
                raise SbomError(
                    f"{source} {subject} relationship {index} has no {field!r} string"
                )
        unknown = {
            relationship[endpoint]
            for endpoint in ("source", "target")
            if relationship[endpoint] not in aliases
        }
        if unknown:
            raise SbomError(
                f"{source} {subject} relationship {index} references unknown native "
                f"package(s): {', '.join(sorted(unknown))}"
            )
        parsed_relationships.append(
            {field: relationship[field] for field in ("source", "type", "target")}
        )
    return parsed_packages, parsed_relationships


def _pinned_image(reference: str, source: str, line: int, role: str) -> dict[str, str]:
    match = DOCKER_IMAGE_REF.fullmatch(reference)
    if match is None:
        raise SbomError(
            f"{source} line {line} references {reference!r} without a sha256 digest; "
            "an SBOM cannot record a floating image reference"
        )
    return {"image": match["image"], "digest": match["digest"], "role": role}


def _pypi_package(entry: dict[str, str]) -> dict[str, Any]:
    return {
        "SPDXID": _spdx_id("Package", "pypi", entry["name"], entry["version"]),
        "name": entry["name"],
        "versionInfo": entry["version"],
        "downloadLocation": NOASSERTION,
        "filesAnalyzed": False,
        "supplier": NOASSERTION,
        "licenseConcluded": NOASSERTION,
        "licenseDeclared": NOASSERTION,
        "copyrightText": NOASSERTION,
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:pypi/{entry['name']}@{entry['version']}",
            }
        ],
    }


def _npm_package(entry: dict[str, Any]) -> dict[str, Any]:
    declared = entry["license"] or NOASSERTION
    package = {
        "SPDXID": _spdx_id("Package", "npm", entry["name"], entry["version"]),
        "name": entry["name"],
        "versionInfo": entry["version"],
        "downloadLocation": NOASSERTION,
        "filesAnalyzed": False,
        "supplier": NOASSERTION,
        "licenseConcluded": NOASSERTION,
        "licenseDeclared": declared,
        "copyrightText": NOASSERTION,
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    f"pkg:npm/{quote(entry['name'], safe='/')}"
                    f"@{quote(entry['version'], safe='')}"
                ),
            }
        ],
    }
    if entry["dev"]:
        package["comment"] = "Build-time only; not present in the published image."
    else:
        package["comment"] = (
            "node_modules is not copied into the published image; this package's "
            "code reaches the image only insofar as the bundler emitted it into "
            "/app/static."
        )
    return package


def _image_package(entry: dict[str, str]) -> dict[str, Any]:
    role = {
        "runtime": "Final base image of the published container.",
        "base": "Intermediate build-stage base image; not published.",
        "build": "Pulled during the build to copy a tool out of; not published.",
    }[entry["role"]]
    repository, tag = entry["image"], None
    if ":" in repository.rsplit("/", 1)[-1]:
        repository, tag = repository.rsplit(":", 1)
    first_fragment = repository.split("/", 1)[0]
    if "." not in first_fragment and ":" not in first_fragment and first_fragment != "localhost":
        repository = (
            f"docker.io/{repository}"
            if "/" in repository
            else f"docker.io/library/{repository}"
        )
    repository = repository.lower()
    name = repository.rsplit("/", 1)[-1]
    qualifiers = {"repository_url": repository}
    if tag is not None:
        qualifiers["tag"] = tag
    qualifier_string = "&".join(
        f"{key}={quote(value, safe='')}" for key, value in sorted(qualifiers.items())
    )

    return {
        "SPDXID": _spdx_id("Package", "oci", entry["image"], entry["digest"][7:19]),
        "name": entry["image"],
        "versionInfo": entry["digest"],
        "downloadLocation": NOASSERTION,
        "filesAnalyzed": False,
        "supplier": NOASSERTION,
        "licenseConcluded": NOASSERTION,
        "licenseDeclared": NOASSERTION,
        "copyrightText": NOASSERTION,
        "checksums": [{"algorithm": "SHA256", "checksumValue": entry["digest"][7:]}],
        "comment": (
            f"{role} Its own OS package set is NOT enumerated by this document, "
            "and the Dockerfile applies OS security upgrades on top of it at "
            "build time."
        ),
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    f"pkg:oci/{quote(name, safe='')}@{entry['digest']}?{qualifier_string}"
                ),
            }
        ],
    }


def _native_package(entry: dict[str, str]) -> dict[str, Any]:
    return {
        "SPDXID": _spdx_id("Package", "native", entry["id"], entry["version"]),
        "name": entry["name"],
        "versionInfo": entry["version"],
        "downloadLocation": entry["downloadLocation"],
        "filesAnalyzed": False,
        "supplier": NOASSERTION,
        "licenseConcluded": NOASSERTION,
        "licenseDeclared": entry["license"],
        "copyrightText": NOASSERTION,
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": entry["purl"],
            }
        ],
    }


def _relationship(source: str, kind: str, target: str) -> dict[str, str]:
    return {
        "spdxElementId": source,
        "relationshipType": kind,
        "relatedSpdxElement": target,
    }


def build_document(
    *,
    subject: str,
    subject_name: str,
    version: str,
    created: str,
    pypi: list[dict[str, str]],
    npm: list[dict[str, Any]],
    images: list[dict[str, str]],
    native_packages: list[dict[str, str]] | None = None,
    native_relationships: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    root_id = _spdx_id("Package", subject)
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": root_id,
            "name": subject_name,
            "versionInfo": version,
            "downloadLocation": NOASSERTION,
            "filesAnalyzed": False,
            "supplier": NOASSERTION,
            "licenseConcluded": NOASSERTION,
            "licenseDeclared": NOASSERTION,
            "copyrightText": NOASSERTION,
            "primaryPackagePurpose": "CONTAINER",
        }
    ]
    relationships: list[dict[str, str]] = [
        _relationship("SPDXRef-DOCUMENT", "DESCRIBES", root_id)
    ]

    for entry in images:
        package = _image_package(entry)
        packages.append(package)
        kind = "CONTAINS" if entry["role"] == "runtime" else "BUILD_DEPENDENCY_OF"
        if kind == "CONTAINS":
            relationships.append(_relationship(root_id, "CONTAINS", package["SPDXID"]))
        else:
            relationships.append(_relationship(package["SPDXID"], kind, root_id))

    for entry in pypi:
        package = _pypi_package(entry)
        packages.append(package)
        relationships.append(
            _relationship(package["SPDXID"], "RUNTIME_DEPENDENCY_OF", root_id)
        )

    for entry in npm:
        package = _npm_package(entry)
        packages.append(package)
        kind = "DEV_DEPENDENCY_OF" if entry["dev"] else "BUILD_DEPENDENCY_OF"
        relationships.append(_relationship(package["SPDXID"], kind, root_id))

    native_ids = {subject: root_id}
    for entry in native_packages or []:
        package = _native_package(entry)
        packages.append(package)
        native_ids[entry["id"]] = package["SPDXID"]
    for entry in native_relationships or []:
        relationships.append(
            _relationship(
                native_ids[entry["source"]],
                entry["type"],
                native_ids[entry["target"]],
            )
        )

    package_ids = [package["SPDXID"] for package in packages]
    duplicate_ids = sorted(
        package_id for package_id in set(package_ids) if package_ids.count(package_id) > 1
    )
    if duplicate_ids:
        raise SbomError(f"document has duplicate package SPDXIDs: {', '.join(duplicate_ids)}")

    packages[1:] = sorted(packages[1:], key=lambda item: item["SPDXID"])
    relationships[1:] = sorted(
        relationships[1:],
        key=lambda item: (item["relationshipType"], item["spdxElementId"]),
    )
    document = {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": DATA_LICENSE,
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{subject}-{version}",
        "documentNamespace": "",
        "creationInfo": {
            "created": created,
            "creators": [
                f"Tool: scripts/generate_sbom.py-{GENERATOR_FORMAT_VERSION}"
            ],
            "comment": CREATION_COMMENT,
        },
        "documentDescribes": [root_id],
        "packages": packages,
        "relationships": relationships,
    }
    namespace_free = {
        key: value for key, value in document.items() if key != "documentNamespace"
    }
    canonical = json.dumps(
        namespace_free,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    document["documentNamespace"] = (
        f"{NAMESPACE_ROOT}/v{version}/{subject}-{fingerprint}"
    )
    return document


SUBJECTS = {
    "ecm": {
        "subject_name": "enhanced-channel-manager",
        "dockerfile": "Dockerfile",
        "requirements": "backend/requirements.txt",
        "lockfile": "frontend/package-lock.json",
    },
    "mcp": {
        "subject_name": "enhanced-channel-manager-mcp",
        "dockerfile": "mcp-server/Dockerfile",
        "requirements": "mcp-server/requirements.txt",
        "lockfile": None,
    },
}


def read_version(root: Path) -> str:
    document = _read_json(root / "frontend" / "package.json")
    version = document.get("version") if isinstance(document, dict) else None
    if not isinstance(version, str) or not version:
        raise SbomError("frontend/package.json has no version string")
    return version


def channel_for(version: str) -> str:
    """Return the channel a version string belongs to, or reject the string.

    This is the *only* place the release/dev judgement is made. An unrecognised
    shape is rejected rather than defaulted, because both defaults are wrong:
    calling a dev build a release puts a transient snapshot in the permanent
    namespace (which is how ``sbom/v0.18.1-0144/`` happened), and calling a
    release a dev build silently declines to keep the record that matters.
    """
    if RELEASE_VERSION.fullmatch(version):
        return CHANNEL_RELEASE
    if DEV_VERSION.fullmatch(version):
        return CHANNEL_DEV
    raise SbomError(
        f"{version!r} is neither a release version (X.Y.Z) nor a dev build "
        "(X.Y.Z-NNNN); see docs/versioning.md"
    )


def directory_for(root: Path, version: str) -> Path:
    """Return the directory a version's SBOM belongs in, derived from its channel."""
    if channel_for(version) == CHANNEL_DEV:
        return root / "sbom" / DEV_DIRNAME
    return root / "sbom" / f"v{version}"


def committed_directories(root: Path) -> list[Path]:
    """Return every SBOM directory in the tree: the release records and the snapshot.

    One definition, used by both ``audit --all`` and the repository tests, so a
    directory cannot be audited by one and skipped by the other.
    """
    sbom_root = root / "sbom"
    if not sbom_root.is_dir():
        return []
    found = sorted(path for path in sbom_root.glob("v*") if path.is_dir())
    snapshot = sbom_root / DEV_DIRNAME
    if snapshot.is_dir():
        found.append(snapshot)
    return found


def render(root: Path, version: str, created: str) -> dict[str, Any]:
    """Return ``{relative path: serialized bytes}`` for the whole SBOM directory."""
    sources: dict[str, str] = {}
    documents: dict[str, dict[str, Any]] = {}
    native_document = _read_json(root / NATIVE_DEPENDENCIES_PATH)
    sources[NATIVE_DEPENDENCIES_PATH] = sha256_file(root / NATIVE_DEPENDENCIES_PATH)

    for subject, spec in SUBJECTS.items():
        requirements_path = spec["requirements"]
        dockerfile_path = spec["dockerfile"]
        pypi = parse_pinned_requirements(
            _read_text(root / requirements_path), requirements_path
        )
        images = parse_dockerfile_images(_read_text(root / dockerfile_path), dockerfile_path)
        sources[requirements_path] = sha256_file(root / requirements_path)
        sources[dockerfile_path] = sha256_file(root / dockerfile_path)
        npm: list[dict[str, Any]] = []
        if spec["lockfile"]:
            npm = parse_lockfile_packages(
                _read_json(root / spec["lockfile"]), spec["lockfile"]
            )
            sources[spec["lockfile"]] = sha256_file(root / spec["lockfile"])
        native_packages, native_relationships = parse_native_dependencies(
            native_document, subject, NATIVE_DEPENDENCIES_PATH
        )
        documents[subject] = build_document(
            subject=subject,
            subject_name=spec["subject_name"],
            version=version,
            created=created,
            pypi=pypi,
            npm=npm,
            images=images,
            native_packages=native_packages,
            native_relationships=native_relationships,
        )

    rendered: dict[str, str] = {
        f"{subject}.spdx.json": _serialize(document)
        for subject, document in documents.items()
    }
    channel = channel_for(version)
    index = {
        "schema": INDEX_SCHEMA,
        "version": version,
        "created": created,
        "kind": "source-manifest",
        "channel": channel,
        "permanent": channel == CHANNEL_RELEASE,
        "channelNote": CHANNEL_NOTE[channel],
        "subject": {
            "type": "source-tree",
            "note": (
                "These documents describe the dependency manifests of the release "
                "source tree. They do NOT describe a container image and assert no "
                "subject image digest. Digest-pinned base and build image references "
                "are recorded; see sbom/README.md."
            ),
        },
        "coverage": {"includes": list(COVERAGE_INCLUDES), "excludes": list(COVERAGE_EXCLUDES)},
        "sources": {path: f"sha256:{digest}" for path, digest in sorted(sources.items())},
        "documents": [
            {
                "path": name,
                "subject": name.split(".", 1)[0],
                "packages": len(documents[name.split(".", 1)[0]]["packages"]),
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
            for name, payload in sorted(rendered.items())
        ],
    }
    rendered["index.json"] = _serialize(index)
    return rendered


def _serialize(document: Any) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def committed_created(directory: Path) -> str:
    """Return the ``created`` stamp of an already-committed SBOM directory.

    A timestamp cannot be re-derived from the tree, so ``verify`` reuses the
    committed one and re-derives every other byte. The stamp is therefore the
    one field this check does not police, which is stated rather than hidden.
    """
    index = _read_json(directory / "index.json")
    if not isinstance(index, dict):
        raise SbomError(f"{directory}/index.json is not a JSON object")
    created = index.get("created")
    if not isinstance(created, str) or not created:
        raise SbomError(f"{directory}/index.json has no 'created' stamp")
    return created


def audit_placement(directory: Path, index: dict[str, Any]) -> list[str]:
    """Return every disagreement between a directory's name and its own index.

    This is the check that stops a transient dev snapshot from being read as a
    release record, which is the specific failure ``sbom/v0.18.1-0144/``
    produced. ``audit`` walks every committed directory, so without it a dev
    snapshot sitting in the release namespace would audit clean and then be
    quoted to somebody holding a CVE as though a release contained it.
    """
    problems: list[str] = []
    name = directory.name
    version = index.get("version")
    channel = index.get("channel")
    if channel not in (CHANNEL_RELEASE, CHANNEL_DEV):
        return [f"{name}/index.json declares no recognised channel ({channel!r})"]
    if not isinstance(version, str) or not version:
        return [f"{name}/index.json has no version string"]
    try:
        expected = channel_for(version)
    except SbomError as exc:
        return [f"{name}/index.json: {exc}"]
    if channel != expected:
        problems.append(
            f"{name}/index.json declares channel {channel!r} but version "
            f"{version!r} is a {expected} version"
        )
    if name == DEV_DIRNAME:
        if channel != CHANNEL_DEV:
            problems.append(
                f"{name}/ is the transient dev snapshot but its index claims "
                f"channel {channel!r}"
            )
    elif name.startswith("v"):
        if channel != CHANNEL_RELEASE:
            problems.append(
                f"{name}/ sits in the permanent release namespace but its index "
                f"claims channel {channel!r}; a dev snapshot belongs in sbom/{DEV_DIRNAME}/"
            )
        elif name[1:] != version:
            problems.append(
                f"{name}/ is named for {name[1:]!r} but its index records {version!r}"
            )
    else:
        problems.append(
            f"{name}/ is neither sbom/{DEV_DIRNAME}/ nor sbom/vX.Y.Z/"
        )
    return problems


def audit(directory: Path) -> list[str]:
    """Return every internal inconsistency in a committed SBOM directory.

    This checks a document against its own index only, so it also applies to a
    *past* release whose source tree is no longer checked out. It catches the
    thing an archive is most likely to suffer: somebody hand-editing a document
    long after the release, leaving the index's recorded hash behind.
    """
    problems: list[str] = []
    index = _read_json(directory / "index.json")
    if not isinstance(index, dict):
        return [f"{directory.name}/index.json is not a JSON object"]
    if index.get("schema") != INDEX_SCHEMA:
        problems.append(f"{directory.name}/index.json schema is {index.get('schema')!r}")
    problems.extend(audit_placement(directory, index))
    entries = index.get("documents")
    if not isinstance(entries, list) or not entries:
        return problems + [f"{directory.name}/index.json lists no documents"]
    listed = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            problems.append(f"{directory.name}/index.json has a malformed document entry")
            continue
        name = entry["path"]
        listed.add(name)
        target = directory / name
        if not target.is_file():
            problems.append(f"{directory.name}/{name} is listed but missing")
            continue
        payload = _read_text(target)
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != entry.get("sha256"):
            problems.append(f"{directory.name}/{name} does not match its recorded sha256")
            continue
        document = json.loads(payload)
        if document.get("spdxVersion") != SPDX_VERSION:
            problems.append(f"{directory.name}/{name} is not {SPDX_VERSION}")
        if len(document.get("packages") or []) != entry.get("packages"):
            problems.append(f"{directory.name}/{name} package count differs from the index")
    present = {path.name for path in directory.iterdir() if path.is_file()}
    for name in sorted(present - listed - {"index.json"}):
        problems.append(f"{directory.name}/{name} is present but not listed in the index")
    return problems


def generate(root: Path, directory: Path, version: str, created: str) -> list[str]:
    rendered = render(root, version, created)
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in sorted(rendered.items()):
        (directory / name).write_text(payload, encoding="utf-8")
    return sorted(rendered)


def verify(root: Path, directory: Path, version: str) -> list[str]:
    """Return the list of drifted files; empty means the committed SBOM is current."""
    if not directory.is_dir():
        raise SbomError(
            f"no SBOM committed at {directory}; run "
            f"'python scripts/generate_sbom.py generate --version {version}'"
        )
    channel = channel_for(version)
    index = _read_json(directory / "index.json")
    if not isinstance(index, dict):
        raise SbomError(f"{directory}/index.json is not a JSON object")
    if index.get("channel") != channel:
        raise SbomError(
            f"{directory}/index.json declares channel {index.get('channel')!r} "
            f"but {version} is a {channel} version"
        )
    declared = read_version(root)
    if channel == CHANNEL_RELEASE:
        if declared != version:
            raise SbomError(
                f"frontend/package.json is {declared} but the SBOM directory demands {version}"
            )
        effective = version
    else:
        # A dev snapshot records the build it was taken at, not what dev carries
        # now. The build counter moves on nearly every PR and re-cutting the
        # inventory each time is a tax that buys nothing, because the binding
        # that matters is the source-manifest hashes compared below: change a
        # dependency and this goes red, move only the build counter and it does
        # not. Release currency is a different and stricter question, and it is
        # the branch above.
        if channel_for(declared) != CHANNEL_DEV:
            raise SbomError(
                f"frontend/package.json is {declared}, a release version; "
                f"sbom/{DEV_DIRNAME}/ describes dev builds only"
            )
        effective = index.get("version")
        if not isinstance(effective, str) or channel_for(effective) != CHANNEL_DEV:
            raise SbomError(f"{directory}/index.json records no dev version")
    rendered = render(root, effective, committed_created(directory))
    present = {path.name for path in directory.iterdir() if path.is_file()}
    drifted = sorted(set(rendered) ^ present)
    for name, payload in sorted(rendered.items()):
        target = directory / name
        if not target.is_file():
            continue
        if _read_text(target) != payload and name not in drifted:
            drifted.append(name)
    return sorted(drifted)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or verify the release SBOM.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to this script's parent).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "verify", "audit"):
        sub = subparsers.add_parser(name)
        sub.add_argument(
            "--version",
            help="Release version; defaults to frontend/package.json.",
        )
        sub.add_argument(
            "--out",
            type=Path,
            help=(
                "SBOM directory; defaults to <repo-root>/sbom/v<version> for a "
                "release version and <repo-root>/sbom/dev for a dev build."
            ),
        )
        if name == "audit":
            sub.add_argument(
                "--all",
                action="store_true",
                help="Audit every committed directory under sbom/ instead of one.",
            )
        if name == "generate":
            sub.add_argument(
                "--created",
                help="SPDX creation timestamp; defaults to the current UTC time.",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root: Path = args.repo_root
    try:
        version = args.version or read_version(root)
        directory = args.out or directory_for(root, version)
        if args.command == "audit" and getattr(args, "all", False):
            directories = committed_directories(root)
            if not directories:
                print("SBOM FAIL: nothing committed under sbom/", file=sys.stderr)
                return 1
            failed = False
            for candidate in directories:
                problems = audit(candidate)
                if problems:
                    failed = True
                    print("SBOM FAIL: " + "; ".join(problems), file=sys.stderr)
                else:
                    print(f"SBOM PASS: {candidate} is internally consistent")
            return 1 if failed else 0
        if args.command == "generate":
            created = args.created or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            for name in generate(root, directory, version, created):
                print(f"wrote {directory}/{name}")
            return 0
        if args.command == "audit":
            problems = audit(directory)
            if problems:
                print("SBOM FAIL: " + "; ".join(problems), file=sys.stderr)
                return 1
            print(f"SBOM PASS: {directory} is internally consistent")
            return 0
        drifted = verify(root, directory, version)
        if drifted:
            print(
                f"SBOM FAIL: {directory} does not match the source tree: "
                f"{', '.join(drifted)}",
                file=sys.stderr,
            )
            print(
                "Regenerate with "
                f"'python scripts/generate_sbom.py generate --version {version}' "
                "and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"SBOM PASS: {directory} matches the source tree")
        return 0
    except SbomError as exc:
        print(f"SBOM INPUT FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
