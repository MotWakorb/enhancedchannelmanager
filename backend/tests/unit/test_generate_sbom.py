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


@pytest.fixture(scope="module")
def sbom():
    spec = importlib.util.spec_from_file_location("generate_sbom", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
                    "node_modules/react": {"version": "19.0.0", "license": "MIT"},
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
        assert "no image digest is asserted" in comment


def test_no_document_asserts_an_image_digest(sbom, generated):
    """The whole failure mode this guards is a document naming a digest it cannot prove."""
    index = json.loads((generated / "index.json").read_text(encoding="utf-8"))
    assert index["kind"] == "source-manifest"
    assert index["subject"]["type"] == "source-tree"
    assert "image_digest" not in json.dumps(index)


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


def test_generation_is_deterministic_for_a_fixed_timestamp(sbom, tree):
    first = sbom.render(tree, "9.9.9", CREATED)
    second = sbom.render(tree, "9.9.9", CREATED)
    assert first == second


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
    with pytest.raises(sbom.SbomError):
        sbom.parse_dockerfile_images("FROM python:3.12-slim\n", "Dockerfile")


def test_a_floating_copy_from_image_is_rejected(sbom):
    with pytest.raises(sbom.SbomError):
        sbom.parse_dockerfile_images(
            f"FROM python:3.12-slim@sha256:{PYTHON_DIGEST}\nCOPY --from=busybox:latest /x /x\n",
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


def test_a_linked_workspace_entry_is_not_inventoried(sbom):
    packages = sbom.parse_lockfile_packages(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"version": "1.0.0"},
                "node_modules/left": {"version": "1.0.0", "link": True},
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


# ─── The real repository ───────────────────────────────────────────────


def _committed_directories() -> list[Path]:
    root = ROOT / "sbom"
    return sorted(path for path in root.glob("v*") if path.is_dir()) if root.is_dir() else []


def test_the_repository_carries_at_least_one_sbom(sbom):
    assert _committed_directories(), "sbom/vX.Y.Z is the artifact of record; none is committed"


@pytest.mark.parametrize("directory", _committed_directories(), ids=lambda path: path.name)
def test_every_committed_sbom_is_internally_consistent(sbom, directory):
    assert sbom.audit(directory) == []


def test_the_sbom_for_the_current_version_matches_the_current_tree(sbom):
    """Committed for this version means current, or the inventory is a fiction.

    Absence is not asserted against here: `dev` moves its build counter on
    nearly every PR, and demanding a regenerated SBOM on each one would be a
    tax this bead did not buy. Currency *for a release* is enforced by the
    Release Cut Gate, which `test_release_cut_gate_enforces_the_sbom` proves is
    still wired up. Older directories are historical records of their own cut
    and are covered by `audit` alone.
    """
    version = sbom.read_version(ROOT)
    directory = ROOT / "sbom" / f"v{version}"
    if directory.is_dir():
        assert sbom.verify(ROOT, directory, version) == []
    else:
        assert _committed_directories()


def test_release_cut_gate_enforces_the_sbom():
    """The gate step is the enforcement; a test asserting it is present is the guard.

    Without this, deleting the workflow step would leave every SBOM test above
    green while nothing checked the release at all.
    """
    workflow = (ROOT / ".github/workflows/release-cut-gate.yml").read_text(encoding="utf-8")
    assert "scripts/generate_sbom.py verify" in workflow
    assert "G8" in workflow
