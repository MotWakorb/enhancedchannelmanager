"""The release SBOM must fail closed on every way it can go stale or be faked.

This is enforcement code on the release path, so it carries its own fixtures
(engineering-discipline §"Enforcement Code Tests Itself"). Every guard below is
red-proven against the specific mutation it exists to catch: a dependency edited
without regenerating, a document hand-edited afterwards, a document deleted, an
extra file smuggled in, and a directory cut for the wrong version.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "generate_sbom.py"

CREATED = "2026-01-02T03:04:05Z"

DOCKERFILE = """\
FROM node:20-alpine@sha256:{node} AS frontend-builder
RUN npm ci

FROM python:3.12-slim@sha256:{python} AS python-builder
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:{uv} /uv /usr/local/bin/uv

FROM python:3.12-slim@sha256:{python}
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=frontend-builder /app/frontend/dist ./static
"""

MCP_DOCKERFILE = "FROM python:3.12-alpine@sha256:{alpine}\nRUN apk upgrade --no-cache\n"

NODE_DIGEST = "a" * 64
PYTHON_DIGEST = "b" * 64
UV_DIGEST = "c" * 64
ALPINE_DIGEST = "d" * 64


def _load():
    spec = importlib.util.spec_from_file_location("generate_sbom", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded at import time as well as via the fixture: the repository sweep below
# parametrizes over the committed directories, which is a collection-time
# question and cannot wait for a fixture.
_SBOM = _load()


@pytest.fixture(scope="module")
def sbom():
    return _SBOM


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _expected_namespace(sbom, document: dict, version: str, subject: str) -> str:
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
    return f"{sbom.NAMESPACE_ROOT}/v{version}/{subject}-{fingerprint}"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal repository with the exact manifest shapes the real one uses."""
    _write(tmp_path / "frontend" / "package.json", json.dumps({"version": "9.9.9"}))
    _write(
        tmp_path / "frontend" / "package-lock.json",
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "app", "version": "9.9.9"},
                    "node_modules/@scope/widget": {
                        "version": "1.2.3",
                        "license": "Apache-2.0",
                    },
                    "node_modules/first/node_modules/semver": {
                        "version": "7.8.5",
                        "resolved": "https://registry.npmjs.org/semver/-/semver-7.8.5.tgz",
                        "integrity": "sha512-same-package-bytes",
                        "dev": True,
                        "license": "ISC",
                    },
                    "node_modules/react": {"version": "19.0.0", "license": "MIT"},
                    "node_modules/second/node_modules/semver": {
                        "version": "7.8.5",
                        "resolved": "https://registry.npmjs.org/semver/-/semver-7.8.5.tgz",
                        "integrity": "sha512-same-package-bytes",
                        "dev": True,
                        "license": "ISC",
                    },
                    "node_modules/vite": {"version": "6.0.0", "dev": True},
                },
            }
        ),
    )
    _write(
        tmp_path / "backend" / "requirements.txt",
        "# generated\nfastapi==0.136.1\n    # via -r backend/requirements.in\ncryptography==50.0.0\n",
    )
    _write(tmp_path / "mcp-server" / "requirements.txt", "mcp==1.29.0\npyjwt==2.13.0\n")
    _write(
        tmp_path / "Dockerfile",
        DOCKERFILE.format(node=NODE_DIGEST, python=PYTHON_DIGEST, uv=UV_DIGEST),
    )
    _write(tmp_path / "mcp-server" / "Dockerfile", MCP_DOCKERFILE.format(alpine=ALPINE_DIGEST))
    return tmp_path


@pytest.fixture
def generated(sbom, tree: Path) -> Path:
    directory = tree / "sbom" / "v9.9.9"
    sbom.generate(tree, directory, "9.9.9", CREATED)
    return directory


# ─── The document itself ───────────────────────────────────────────────


def test_documents_are_spdx_2_3_with_purls_for_every_dependency(sbom, generated):
    document = json.loads((generated / "ecm.spdx.json").read_text(encoding="utf-8"))
    assert document["spdxVersion"] == "SPDX-2.3"
    assert document["dataLicense"] == "CC0-1.0"
    assert document["SPDXID"] == "SPDXRef-DOCUMENT"
    assert document["documentDescribes"] == ["SPDXRef-Package-ecm"]
    locators = {
        ref["referenceLocator"]
        for package in document["packages"]
        for ref in package.get("externalRefs", [])
    }
    assert "pkg:pypi/fastapi@0.136.1" in locators
    assert "pkg:pypi/cryptography@50.0.0" in locators
    assert "pkg:npm/react@19.0.0" in locators
    assert "pkg:npm/vite@6.0.0" in locators


def test_the_coverage_limit_is_stated_inside_the_document(sbom, generated):
    """A reader who has only the .spdx.json must still learn what it omits."""
    for name in ("ecm.spdx.json", "mcp.spdx.json"):
        document = json.loads((generated / name).read_text(encoding="utf-8"))
        comment = document["creationInfo"]["comment"]
        assert "NOT an image SBOM" in comment
        assert "no subject image digest is asserted" in comment
        assert "Digest-pinned base and build image references are recorded" in comment
        assert "no image digest is asserted" not in comment


def test_the_index_distinguishes_subject_and_dependency_image_digests(sbom, generated):
    index = json.loads((generated / "index.json").read_text(encoding="utf-8"))
    assert index["kind"] == "source-manifest"
    assert index["subject"]["type"] == "source-tree"
    assert "no subject image digest" in index["subject"]["note"]
    coverage = " ".join(index["coverage"]["includes"] + index["coverage"]["excludes"])
    assert "Base and build images referenced by digest" in coverage
    assert "Published subject image digest" in coverage
    assert "Any image digest" not in coverage


def test_base_and_build_image_digests_are_recorded_but_the_subject_has_none(
    sbom, generated
):
    for name in ("ecm.spdx.json", "mcp.spdx.json"):
        document = json.loads((generated / name).read_text(encoding="utf-8"))
        root = next(
            package
            for package in document["packages"]
            if package["SPDXID"] == document["documentDescribes"][0]
        )
        assert "checksums" not in root

        images = [
            package
            for package in document["packages"]
            if any(
                ref["referenceLocator"].startswith("pkg:oci/")
                for ref in package.get("externalRefs", [])
            )
        ]
        assert images
        for package in images:
            assert package["versionInfo"].startswith("sha256:")
            assert package["checksums"] == [
                {
                    "algorithm": "SHA256",
                    "checksumValue": package["versionInfo"].removeprefix("sha256:"),
                }
            ]


def test_a_base_image_used_twice_is_recorded_once_as_the_runtime_base(sbom, generated):
    document = json.loads((generated / "ecm.spdx.json").read_text(encoding="utf-8"))
    slim = [
        package
        for package in document["packages"]
        if package["name"] == "python:3.12-slim"
    ]
    assert len(slim) == 1
    assert slim[0]["versionInfo"] == f"sha256:{PYTHON_DIGEST}"
    assert slim[0]["comment"].startswith("Final base image")
    assert {
        "spdxElementId": "SPDXRef-Package-ecm",
        "relationshipType": "CONTAINS",
        "relatedSpdxElement": slim[0]["SPDXID"],
    } in document["relationships"]


def test_a_copy_from_a_named_build_stage_is_not_an_image(sbom, generated):
    document = json.loads((generated / "ecm.spdx.json").read_text(encoding="utf-8"))
    names = {package["name"] for package in document["packages"]}
    assert "python-builder" not in names
    assert "frontend-builder" not in names
    assert "ghcr.io/astral-sh/uv:latest" in names


def test_exact_duplicate_npm_identity_is_deduplicated_and_ids_are_globally_unique(
    sbom, generated
):
    ecm = json.loads((generated / "ecm.spdx.json").read_text(encoding="utf-8"))
    semver = [package for package in ecm["packages"] if package["name"] == "semver"]
    assert len(semver) == 1

    for name in ("ecm.spdx.json", "mcp.spdx.json"):
        document = json.loads((generated / name).read_text(encoding="utf-8"))
        package_ids = [package["SPDXID"] for package in document["packages"]]
        assert len(package_ids) == len(set(package_ids))


def test_distinct_package_identities_that_normalize_to_one_spdx_id_are_rejected(sbom):
    npm = [
        {
            "name": name,
            "version": "1.0.0",
            "dev": False,
            "license": None,
        }
        for name in ("same.name", "same_name")
    ]
    duplicate_id = "SPDXRef-Package-npm-same.name-1.0.0"

    with pytest.raises(sbom.SbomError, match=duplicate_id):
        sbom.build_document(
            subject="ecm",
            subject_name="enhanced-channel-manager",
            version="9.9.9",
            created=CREATED,
            pypi=[],
            npm=npm,
            images=[],
        )


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("dev", False),
        ("license", "MIT"),
        ("resolved", "https://registry.example/semver-7.8.5.tgz"),
        ("integrity", "sha512-different-package-bytes"),
    ],
)
def test_conflicting_duplicate_npm_identity_is_rejected(sbom, field, conflicting_value):
    metadata = {
        "version": "7.8.5",
        "resolved": "https://registry.npmjs.org/semver/-/semver-7.8.5.tgz",
        "integrity": "sha512-same-package-bytes",
        "dev": True,
        "license": "ISC",
    }
    conflicting = dict(metadata)
    conflicting[field] = conflicting_value

    with pytest.raises(sbom.SbomError, match="conflicting metadata"):
        sbom.parse_lockfile_packages(
            {
                "lockfileVersion": 3,
                "packages": {
                    "node_modules/first/node_modules/semver": metadata,
                    "node_modules/second/node_modules/semver": conflicting,
                },
            },
            "package-lock.json",
        )


def test_scoped_npm_purl_is_canonical(sbom, generated):
    document = json.loads((generated / "ecm.spdx.json").read_text(encoding="utf-8"))
    package = next(
        package for package in document["packages"] if package["name"] == "@scope/widget"
    )
    assert package["externalRefs"] == [
        {
            "referenceCategory": "PACKAGE-MANAGER",
            "referenceType": "purl",
            "referenceLocator": "pkg:npm/%40scope/widget@1.2.3",
        }
    ]


def test_oci_purls_are_canonical_and_use_only_supported_qualifiers(sbom, generated):
    expected = {
        f"pkg:oci/node@sha256:{NODE_DIGEST}"
        "?repository_url=docker.io%2Flibrary%2Fnode&tag=20-alpine",
        f"pkg:oci/python@sha256:{PYTHON_DIGEST}"
        "?repository_url=docker.io%2Flibrary%2Fpython&tag=3.12-slim",
        f"pkg:oci/uv@sha256:{UV_DIGEST}"
        "?repository_url=ghcr.io%2Fastral-sh%2Fuv&tag=latest",
        f"pkg:oci/python@sha256:{ALPINE_DIGEST}"
        "?repository_url=docker.io%2Flibrary%2Fpython&tag=3.12-alpine",
    }
    actual = set()
    for name in ("ecm.spdx.json", "mcp.spdx.json"):
        document = json.loads((generated / name).read_text(encoding="utf-8"))
        actual.update(
            ref["referenceLocator"]
            for package in document["packages"]
            for ref in package.get("externalRefs", [])
            if ref["referenceLocator"].startswith("pkg:oci/")
        )

    assert actual == expected
    for locator in actual:
        qualifiers = dict(parse_qsl(locator.partition("?")[2]))
        assert set(qualifiers) == {"repository_url", "tag"}


def test_creator_identifies_the_generator_format_version(sbom, generated):
    for name in ("ecm.spdx.json", "mcp.spdx.json"):
        document = json.loads((generated / name).read_text(encoding="utf-8"))
        assert document["creationInfo"]["creators"] == [
            "Tool: scripts/generate_sbom.py-3"
        ]


def test_generation_is_deterministic_for_a_fixed_timestamp(sbom, tree):
    first = sbom.render(tree, "9.9.9", CREATED)
    second = sbom.render(tree, "9.9.9", CREATED)
    assert first == second


def test_document_namespaces_are_content_derived_without_self_reference(sbom, tree):
    rendered = sbom.render(tree, "9.9.9", CREATED)
    namespaces = set()
    for subject in ("ecm", "mcp"):
        document = json.loads(rendered[f"{subject}.spdx.json"])
        expected = _expected_namespace(sbom, document, "9.9.9", subject)
        assert document["documentNamespace"] == expected
        assert "#" not in document["documentNamespace"]
        namespaces.add(document["documentNamespace"])
    assert len(namespaces) == 2


def test_document_namespace_changes_with_content_or_creation_time(sbom, tree):
    original = json.loads(sbom.render(tree, "9.9.9", CREATED)["ecm.spdx.json"])

    _write(
        tree / "backend" / "requirements.txt",
        "fastapi==0.136.2\ncryptography==50.0.0\n",
    )
    changed_content = json.loads(
        sbom.render(tree, "9.9.9", CREATED)["ecm.spdx.json"]
    )
    changed_time = json.loads(
        sbom.render(tree, "9.9.9", "2026-01-02T03:04:06Z")["ecm.spdx.json"]
    )

    assert changed_content["documentNamespace"] != original["documentNamespace"]
    assert changed_time["documentNamespace"] != changed_content["documentNamespace"]


# ─── verify: the mutants it must catch ─────────────────────────────────


def test_verify_passes_on_a_freshly_generated_directory(sbom, tree, generated):
    assert sbom.verify(tree, generated, "9.9.9") == []


def test_a_dependency_edited_without_regenerating_is_caught(sbom, tree, generated):
    """The mutation the gate exists for: a bump that never reached the SBOM."""
    _write(
        tree / "backend" / "requirements.txt",
        "fastapi==0.136.1\ncryptography==49.0.0\n",
    )
    assert sbom.verify(tree, generated, "9.9.9") == ["ecm.spdx.json", "index.json"]


def test_a_base_image_repinned_without_regenerating_is_caught(sbom, tree, generated):
    _write(
        tree / "mcp-server" / "Dockerfile",
        MCP_DOCKERFILE.format(alpine="e" * 64),
    )
    assert sbom.verify(tree, generated, "9.9.9") == ["index.json", "mcp.spdx.json"]


def test_a_hand_edited_document_is_caught(sbom, tree, generated):
    target = generated / "mcp.spdx.json"
    target.write_text(
        target.read_text(encoding="utf-8").replace("1.29.0", "1.30.0"), encoding="utf-8"
    )
    assert sbom.verify(tree, generated, "9.9.9") == ["mcp.spdx.json"]


def test_a_deleted_document_is_caught(sbom, tree, generated):
    (generated / "mcp.spdx.json").unlink()
    assert sbom.verify(tree, generated, "9.9.9") == ["mcp.spdx.json"]


def test_an_extra_file_is_caught(sbom, tree, generated):
    (generated / "extra.spdx.json").write_text("{}", encoding="utf-8")
    assert sbom.verify(tree, generated, "9.9.9") == ["extra.spdx.json"]


def test_a_forged_created_stamp_is_caught(sbom, tree, generated):
    """`created` is reused from the index, so moving it desynchronises the documents."""
    index_path = generated / "index.json"
    index_path.write_text(
        index_path.read_text(encoding="utf-8").replace(CREATED, "2030-01-01T00:00:00Z"),
        encoding="utf-8",
    )
    assert sbom.verify(tree, generated, "9.9.9") == [
        "ecm.spdx.json",
        "index.json",
        "mcp.spdx.json",
    ]


def test_a_missing_sbom_directory_fails_closed(sbom, tree):
    with pytest.raises(sbom.SbomError):
        sbom.verify(tree, tree / "sbom" / "v9.9.9", "9.9.9")


def test_an_sbom_cut_for_the_wrong_version_fails_closed(sbom, tree, generated):
    """G6 already pins package.json to the branch; this stops the two diverging."""
    _write(tree / "frontend" / "package.json", json.dumps({"version": "9.9.10"}))
    with pytest.raises(sbom.SbomError):
        sbom.verify(tree, generated, "9.9.9")


# ─── Input shapes that must be rejected rather than guessed at ─────────


def test_an_unpinned_requirement_is_rejected(sbom):
    with pytest.raises(sbom.SbomError):
        sbom.parse_pinned_requirements("fastapi>=0.136\n", "requirements.txt")


def test_an_empty_requirements_file_is_rejected(sbom):
    with pytest.raises(sbom.SbomError):
        sbom.parse_pinned_requirements("# only a comment\n", "requirements.txt")


def test_a_floating_base_image_is_rejected(sbom):
    with pytest.raises(sbom.SbomError, match="python:3.12-slim.*floating image reference"):
        sbom.parse_dockerfile_images("FROM python:3.12-slim\n", "Dockerfile")


def test_a_floating_copy_from_image_is_rejected(sbom):
    with pytest.raises(sbom.SbomError, match="busybox:latest.*floating image reference"):
        sbom.parse_dockerfile_images(
            f"FROM python:3.12-slim@sha256:{PYTHON_DIGEST}\nCOPY --from=busybox:latest /x /x\n",
            "Dockerfile",
        )


def test_tabbed_image_instructions_are_parsed_and_stage_aliases_are_ignored(sbom):
    images = sbom.parse_dockerfile_images(
        f"FROM\tnode:20-alpine@sha256:{NODE_DIGEST}\tAS\tbuild\n"
        f"COPY\t--from=busybox:1.36@sha256:{UV_DIGEST} /bin/x /bin/x\n"
        "FROM\tbuild\n"
        "COPY\t--from=build /app /app\n",
        "Dockerfile",
    )

    assert images == [
        {
            "image": "busybox:1.36",
            "digest": f"sha256:{UV_DIGEST}",
            "role": "build",
        },
        {
            "image": "node:20-alpine",
            "digest": f"sha256:{NODE_DIGEST}",
            "role": "runtime",
        },
    ]


def test_a_tabbed_floating_base_image_reports_the_reference(sbom):
    with pytest.raises(sbom.SbomError, match="python:3.12-slim.*floating image reference"):
        sbom.parse_dockerfile_images("FROM\tpython:3.12-slim\n", "Dockerfile")


def test_a_tabbed_floating_copy_image_reports_the_reference(sbom):
    with pytest.raises(sbom.SbomError, match="busybox:latest.*floating image reference"):
        sbom.parse_dockerfile_images(
            f"FROM\tpython:3.12-slim@sha256:{PYTHON_DIGEST}\n"
            "COPY\t--from=busybox:latest /x /x\n",
            "Dockerfile",
        )


def test_a_dockerfile_without_a_from_is_rejected(sbom):
    with pytest.raises(sbom.SbomError):
        sbom.parse_dockerfile_images("RUN true\n", "Dockerfile")


@pytest.mark.parametrize("document", [{"lockfileVersion": 1, "packages": {}}, {}, []])
def test_an_unsupported_lockfile_is_rejected(sbom, document):
    with pytest.raises(sbom.SbomError):
        sbom.parse_lockfile_packages(document, "package-lock.json")


def test_a_lockfile_entry_without_a_version_is_rejected(sbom):
    with pytest.raises(sbom.SbomError):
        sbom.parse_lockfile_packages(
            {"lockfileVersion": 3, "packages": {"node_modules/react": {}}},
            "package-lock.json",
        )


@pytest.mark.parametrize("location", ["", "node_modules/bad"])
@pytest.mark.parametrize("entry", [None, True, 1, "bad", []])
def test_every_lockfile_entry_must_be_an_object(sbom, location, entry):
    with pytest.raises(sbom.SbomError, match=repr(location)):
        sbom.parse_lockfile_packages(
            {
                "lockfileVersion": 3,
                "packages": {
                    location: entry,
                    "node_modules/real": {"version": "2.0.0"},
                },
            },
            "package-lock.json",
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("link", "true"),
        ("dev", 0),
        ("license", None),
        ("resolved", 1),
        ("integrity", []),
        ("name", False),
    ],
)
def test_present_lockfile_metadata_must_have_its_declared_type(sbom, field, invalid):
    with pytest.raises(sbom.SbomError, match=rf"entry 'node_modules/bad'.*{field}"):
        sbom.parse_lockfile_packages(
            {
                "lockfileVersion": 3,
                "packages": {
                    "node_modules/bad": {"version": "1.0.0", field: invalid},
                    "node_modules/real": {"version": "2.0.0"},
                },
            },
            "package-lock.json",
        )


@pytest.mark.parametrize(
    ("location", "entry", "field"),
    [
        ("", {"license": None}, "license"),
        ("node_modules/workspace", {"link": True, "dev": "false"}, "dev"),
    ],
)
def test_root_and_linked_entries_are_validated_before_they_are_skipped(
    sbom, location, entry, field
):
    with pytest.raises(sbom.SbomError, match=rf"entry {location!r}.*{field}"):
        sbom.parse_lockfile_packages(
            {
                "lockfileVersion": 3,
                "packages": {
                    location: entry,
                    "node_modules/real": {"version": "2.0.0"},
                },
            },
            "package-lock.json",
        )


def test_absent_optional_metadata_retains_defaults(sbom):
    packages = sbom.parse_lockfile_packages(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "app", "version": "1.0.0"},
                "node_modules/real": {"version": "2.0.0"},
            },
        },
        "package-lock.json",
    )
    assert packages == [
        {
            "name": "real",
            "version": "2.0.0",
            "dev": False,
            "license": None,
            "resolved": None,
            "integrity": None,
        }
    ]


def test_a_linked_workspace_entry_is_not_inventoried(sbom):
    packages = sbom.parse_lockfile_packages(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"version": "1.0.0"},
                "node_modules/left": {
                    "link": True,
                    "resolved": "packages/left",
                },
                "node_modules/real": {"version": "2.0.0"},
            },
        },
        "package-lock.json",
    )
    assert [package["name"] for package in packages] == ["real"]


# ─── audit: a past release's documents stay checkable without its tree ──


def test_audit_passes_on_a_generated_directory(sbom, generated):
    assert sbom.audit(generated) == []


def test_audit_catches_a_document_edited_after_the_release(sbom, generated):
    target = generated / "ecm.spdx.json"
    target.write_text(
        target.read_text(encoding="utf-8").replace("0.136.1", "0.136.2"), encoding="utf-8"
    )
    assert any("recorded sha256" in problem for problem in sbom.audit(generated))


def test_audit_catches_an_index_rehashed_to_match_a_forged_document(sbom, generated):
    """Recomputing the hash is not enough: the package count must agree too."""
    target = generated / "mcp.spdx.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    document["packages"].pop()
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    target.write_text(payload, encoding="utf-8")
    index_path = generated / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index["documents"]:
        if entry["path"] == "mcp.spdx.json":
            entry["sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert any("package count" in problem for problem in sbom.audit(generated))


def test_audit_catches_an_unlisted_file(sbom, generated):
    (generated / "smuggled.spdx.json").write_text("{}", encoding="utf-8")
    assert any("not listed in the index" in problem for problem in sbom.audit(generated))


# ─── Release records vs the transient dev snapshot ─────────────────────
#
# Two kinds of directory with opposite lifecycles: release records accumulate
# and are kept forever, the dev snapshot is superseded and there is only ever
# one. `sbom/v0.18.1-0144/` was the second kind sitting in the first kind's
# namespace, so these guards make the distinction structural rather than a
# convention a reader has to know.


@pytest.fixture
def dev_tree(tree: Path) -> Path:
    _write(tree / "frontend" / "package.json", json.dumps({"version": "9.9.9-0001"}))
    return tree


def test_a_dev_build_version_routes_to_the_transient_snapshot_directory(sbom, tree):
    assert sbom.channel_for("9.9.9-0001") == sbom.CHANNEL_DEV
    assert sbom.directory_for(tree, "9.9.9-0001") == tree / "sbom" / "dev"


def test_a_release_version_routes_to_a_permanent_release_directory(sbom, tree):
    assert sbom.channel_for("9.9.9") == sbom.CHANNEL_RELEASE
    assert sbom.directory_for(tree, "9.9.9") == tree / "sbom" / "v9.9.9"


@pytest.mark.parametrize("version", ["9.9.9-rc1", "9.9", "v9.9.9", "9.9.9-12", ""])
def test_an_unrecognised_version_shape_is_rejected_rather_than_defaulted(sbom, version):
    """Both defaults are wrong, so there is no default."""
    with pytest.raises(sbom.SbomError):
        sbom.channel_for(version)


def test_the_index_says_which_kind_of_directory_it_is(sbom, tree, dev_tree):
    release = json.loads(
        sbom.render(tree, "9.9.9", CREATED)["index.json"]
    )
    snapshot = json.loads(
        sbom.render(dev_tree, "9.9.9-0001", CREATED)["index.json"]
    )
    assert (release["channel"], release["permanent"]) == ("release", True)
    assert (snapshot["channel"], snapshot["permanent"]) == ("dev", False)
    assert "not an artifact of record" in snapshot["channelNote"].lower()


def test_audit_catches_a_dev_snapshot_in_the_release_namespace(sbom, dev_tree):
    """The v0.18.1-0144 shape: transient contents under a permanent name."""
    smuggled = dev_tree / "sbom" / "v9.9.9-0001"
    sbom.generate(dev_tree, smuggled, "9.9.9-0001", CREATED)
    assert any("release namespace" in problem for problem in sbom.audit(smuggled))


def test_audit_catches_a_release_directory_named_for_another_version(sbom, tree):
    misnamed = tree / "sbom" / "v9.9.8"
    sbom.generate(tree, misnamed, "9.9.9", CREATED)
    assert any("is named for" in problem for problem in sbom.audit(misnamed))


def test_audit_catches_a_release_index_relabelled_as_a_snapshot(sbom, generated):
    index_path = generated / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["channel"] = "dev"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert any("channel 'dev'" in problem for problem in sbom.audit(generated))


def test_verify_refuses_a_directory_whose_channel_disagrees_with_the_version(sbom, dev_tree):
    """A snapshot cannot be verified as though it were the release record."""
    snapshot = dev_tree / "sbom" / "dev"
    sbom.generate(dev_tree, snapshot, "9.9.9-0001", CREATED)
    with pytest.raises(sbom.SbomError):
        sbom.verify(dev_tree, snapshot, "9.9.9")


def test_a_snapshot_survives_a_build_bump_but_not_a_dependency_change(sbom, dev_tree):
    """The snapshot binds to the manifests, not to the build counter.

    Regenerating on every build-counter bump is a tax that buys nothing. Not
    regenerating on a dependency sweep is exactly what made the 0144 documents
    describe a dependency set that no longer existed, so that must go red.
    """
    snapshot = dev_tree / "sbom" / "dev"
    sbom.generate(dev_tree, snapshot, "9.9.9-0001", CREATED)

    _write(dev_tree / "frontend" / "package.json", json.dumps({"version": "9.9.9-0002"}))
    assert sbom.verify(dev_tree, snapshot, "9.9.9-0002") == []

    _write(
        dev_tree / "backend" / "requirements.txt",
        "fastapi==0.136.1\ncryptography==50.0.1\n",
    )
    assert sbom.verify(dev_tree, snapshot, "9.9.9-0002") == ["ecm.spdx.json", "index.json"]


def test_a_release_tree_cannot_carry_a_dev_snapshot(sbom, dev_tree):
    snapshot = dev_tree / "sbom" / "dev"
    sbom.generate(dev_tree, snapshot, "9.9.9-0001", CREATED)
    _write(dev_tree / "frontend" / "package.json", json.dumps({"version": "9.9.9"}))
    with pytest.raises(sbom.SbomError):
        sbom.verify(dev_tree, snapshot, "9.9.9-0001")


def test_committed_directories_finds_both_kinds(sbom, dev_tree):
    sbom.generate(dev_tree, dev_tree / "sbom" / "dev", "9.9.9-0001", CREATED)
    sbom.generate(dev_tree, dev_tree / "sbom" / "v9.9.8", "9.9.8", CREATED)
    assert [path.name for path in sbom.committed_directories(dev_tree)] == ["v9.9.8", "dev"]


# ─── The real repository ───────────────────────────────────────────────


def _committed_directories() -> list[Path]:
    return _SBOM.committed_directories(ROOT)


def test_the_repository_carries_at_least_one_sbom(sbom):
    assert _committed_directories(), "an SBOM is the artifact of record; none is committed"


@pytest.mark.parametrize("directory", _committed_directories(), ids=lambda path: path.name)
def test_every_committed_sbom_is_internally_consistent(sbom, directory):
    assert sbom.audit(directory) == []


def test_every_committed_document_has_a_unique_content_derived_namespace(sbom):
    namespaces = set()
    for directory in _committed_directories():
        index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        for entry in index["documents"]:
            document = json.loads(
                (directory / entry["path"]).read_text(encoding="utf-8")
            )
            expected = _expected_namespace(
                sbom,
                document,
                index["version"],
                entry["subject"],
            )
            namespace = document["documentNamespace"]
            assert namespace == expected
            assert namespace not in namespaces
            namespaces.add(namespace)


@pytest.mark.parametrize("directory", _committed_directories(), ids=lambda path: path.name)
def test_every_committed_document_has_unique_ids_and_resolved_relationships(
    sbom, directory
):
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    for entry in index["documents"]:
        document = json.loads((directory / entry["path"]).read_text(encoding="utf-8"))
        element_ids = ["SPDXRef-DOCUMENT"] + [
            package["SPDXID"] for package in document["packages"]
        ]
        assert len(element_ids) == len(set(element_ids))
        known = set(element_ids)
        for relationship in document["relationships"]:
            assert relationship["spdxElementId"] in known
            assert relationship["relatedSpdxElement"] in known


def test_the_sbom_for_the_current_version_matches_the_current_tree(sbom):
    """Committed for this version means current, or the inventory is a fiction.

    On `dev` this is the transient snapshot, and what it is held to is
    *manifest* currency, not build-counter currency: `verify` re-derives both
    documents from the tree and byte-compares, so a dependency change goes red
    here while a bare build-counter bump does not. That is deliberate — the
    build counter moves on nearly every PR and re-cutting the inventory each
    time buys nothing, while a dependency sweep landing without a regeneration
    is exactly the drift that made `sbom/v0.18.1-0144/` wrong.

    Release currency is the stricter question and is enforced by the Release Cut
    Gate, which `test_release_cut_gate_enforces_the_sbom` proves is wired up.
    Past releases are historical records of their own cut and covered by `audit`.
    """
    version = sbom.read_version(ROOT)
    directory = sbom.directory_for(ROOT, version)
    if directory.is_dir():
        assert sbom.verify(ROOT, directory, version) == []
    else:
        assert _committed_directories()


def test_no_committed_directory_is_a_dev_snapshot_in_the_release_namespace(sbom):
    """`sbom/v0.18.1-0144/` is the failure this exists to make impossible.

    A build-numbered directory under `sbom/vX.Y.Z/` reads as a release record
    and will be quoted as one during an incident, while its contents describe a
    build nobody ever received.
    """
    offenders = [
        path.name
        for path in _committed_directories()
        if path.name != sbom.DEV_DIRNAME and not sbom.RELEASE_VERSION.fullmatch(path.name[1:])
    ]
    assert offenders == [], (
        f"{offenders} name dev builds but sit in the permanent release namespace; "
        f"a transient snapshot belongs in sbom/{sbom.DEV_DIRNAME}/"
    )


def test_release_cut_gate_enforces_the_sbom():
    """The gate step is the enforcement; a test asserting it is present is the guard.

    Without this, deleting the workflow step would leave every SBOM test above
    green while nothing checked the release at all.
    """
    workflow = (ROOT / ".github/workflows/release-cut-gate.yml").read_text(encoding="utf-8")
    assert "scripts/generate_sbom.py verify" in workflow
    assert "G8" in workflow
