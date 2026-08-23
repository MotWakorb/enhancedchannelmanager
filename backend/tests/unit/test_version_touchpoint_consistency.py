"""Version-touchpoint lockstep guard (bead enhancedchannelmanager-ipcqx).

THE INVARIANT
-------------
**Every declaration of the ECM application version agrees with the canonical
source, ``frontend/package.json``.**

That is the criterion — not "these three specific files agree". The
distinction matters: the predecessor guard (``scripts/check_version_consistency.py``
plus the ``version-consistency`` CI job, both removed in the CI gate reduction
at commit 3404d2d5) carried a hardcoded ``TOUCHPOINTS`` list, so a fourth
declaration added anywhere else was outside the check by construction. This
module *discovers* declarations instead, and additionally requires the
discovered set and the documented set to be the same set — so a fourth
touchpoint cannot be added silently in either direction.

WHY THIS EXISTS
---------------
The lockstep has already broken twice, latent for months each time:

* **PR #277** (2026-05-13): ``backend/routers/backup.py`` sat at ``0.16.0``
  while ``frontend/package.json`` had advanced 27 builds to ``0.17.0-0027``.
  Caught by accident, because the cherry-picked commit happened to touch
  ``backup.py``.
* **bd-9rtlc audit** (2026-05-14): ``backend/main.py`` sat at ``0.16.0-0003``
  while ``frontend/package.json`` was at ``0.17.0-0033``. The ``FastAPI``
  kwarg only surfaces in the OpenAPI schema, which nothing external cited, so
  it drifted ~30 builds unnoticed.

This guard lives in the backend pytest suite rather than in a dedicated CI job
specifically so that a future CI gate reduction cannot delete it the way it
deleted its predecessor. It is enforcement code, so it tests itself: see
``TestGuardMechanics`` at the bottom, which drives the comparison helpers with
synthetic desynchronised inputs and asserts the failure names the offending
file.

THREE ENFORCEMENT CLAUSES
-------------------------
1. ``test_all_discovered_version_declarations_agree`` — every *declaration*
   discovered in product code equals the canonical version.
2. ``test_no_stale_build_version_literal_in_product_code`` — every string
   *literal* of ECM build shape (``X.Y.Z-NNNN``) in product code equals the
   canonical version, whatever syntax it is written in. This is the net that
   catches a hardcoded version in a shape clause 1 does not model.
3. ``test_documented_touchpoints_and_discovered_touchpoints_are_the_same_set``
   — the touchpoint table in ``docs/versioning.md`` and the discovered set
   agree, in both directions, and every documented file independently yields
   the canonical value.

WHAT IS DELIBERATELY *NOT* A TOUCHPOINT
---------------------------------------
``frontend/package-lock.json`` declares ``"version"`` at its root and at the
``packages[""]`` self-entry. It is **not** a version touchpoint and is
excluded from all three clauses. It has never tracked the ``-BUILD`` suffix,
nothing reads it for the application version (``build.yml`` reads
``jq -r .version frontend/package.json``), and npm rewrites it only on
install — so making it track would mean regenerating the lockfile on every
build bump, turning a one-line edit into a dependency-resolution event and a
guaranteed conflict between concurrent PRs. It drifts to whatever
``MAJOR.MINOR.PATCH`` was current at the last ``npm install``; that drift is
cosmetic and expected. Recorded in ``docs/versioning.md`` → Touchpoints.

WHAT THIS GUARD DOES NOT DO
---------------------------
It checks *agreement*, not *advancement*. Nothing here asserts that a code
change bumped the build number — that was ``scripts/check_version_advances.py``,
also removed, and is still a convention followed by hand
(``docs/shipping.md`` step 3a/3b).
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
from typing import Dict, Iterator, List, Tuple

import pytest

# --------------------------------------------------------------------------
# Repo layout
# --------------------------------------------------------------------------
# <repo>/backend/tests/unit/test_version_touchpoint_consistency.py
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: The canonical source of the version string. ``docs/versioning.md`` names
#: this file as canonical and ``build.yml`` reads it into the ``ECM_VERSION``
#: build-arg, so every other declaration is a mirror of this one.
CANONICAL_FILE = "frontend/package.json"

#: Product trees scanned for Python version declarations. Test trees are
#: excluded: tests legitimately use fabricated version strings as fixtures.
PRODUCT_PY_ROOTS: Tuple[str, ...] = ("backend", "mcp-server")

#: Non-Python product files scanned as text for ECM build-shaped literals.
PRODUCT_TEXT_FILES: Tuple[str, ...] = (
    "frontend/package.json",
    "Dockerfile",
    "Dockerfile.dev",
    "entrypoint.sh",
)

#: Explicitly not a touchpoint — see the module docstring for the reasoning.
NON_TOUCHPOINTS: Dict[str, str] = {
    "frontend/package-lock.json": (
        "npm-managed lockfile. Never tracked the -BUILD suffix; nothing reads "
        "it for the application version; regenerating it on every bump would "
        "be a dependency-resolution event and a cross-PR conflict generator."
    ),
}

#: ``X.Y.Z-NNNN`` — the ECM dev-build shape. Distinctive enough that any
#: literal of this shape in product code is an ECM version and nothing else.
BUILD_VERSION_SHAPE = re.compile(r"\b\d+\.\d+\.\d+-\d{4}\b")

#: ``X.Y.Z`` or ``X.Y.Z-NNNN``, anchored — a release cut drops the suffix, so
#: the declaration scan must accept both forms.
DECLARED_VERSION_SHAPE = re.compile(r"^\d+\.\d+\.\d+(?:-\d{4})?$")

#: Identifiers and kwargs that declare a version.
VERSION_IDENTIFIER = re.compile(r"^(?:[A-Za-z_]*_)?version$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Discovery helpers  (kept free of assertions so TestGuardMechanics can drive
# them directly with synthetic input)
# --------------------------------------------------------------------------
def _iter_product_py_files() -> Iterator[pathlib.Path]:
    for base in PRODUCT_PY_ROOTS:
        base_path = REPO_ROOT / base
        if not base_path.is_dir():
            continue
        for path in sorted(base_path.rglob("*.py")):
            parts = set(path.parts)
            if "tests" in parts or "__pycache__" in parts:
                continue
            yield path


def _docstring_constant_ids(tree: ast.AST) -> set:
    """ids() of the Constant nodes that are docstrings.

    Docstrings and comments routinely cite *historical* build numbers ("drill
    run 2026-08-04-run1, ECM 0.18.1-0022"). Those are prose, not declarations,
    and must not be swept up. Comments never reach the AST; docstrings do, so
    they are identified and skipped here.
    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    out.add(id(value))
    return out


def declared_versions_in_python(source: str, label: str) -> List[Tuple[str, int, str]]:
    """Version *declarations* in a Python source string.

    Returns ``(label, lineno, version)`` for every assignment, annotated
    assignment, or call keyword whose name looks like a version and whose
    value is a string constant of version shape.

    Structural (AST) matching rather than line matching is what keeps
    ``dispatcharr_client.py``'s docstring example (``{"version": "0.28.2"}``,
    Dispatcharr's version, not ECM's) out of the result.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - product tree must parse
        return []

    found: List[Tuple[str, int, str]] = []
    for node in ast.walk(tree):
        candidates: List[ast.expr] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and VERSION_IDENTIFIER.match(target.id):
                    candidates.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and VERSION_IDENTIFIER.match(node.target.id)
                and node.value is not None
            ):
                candidates.append(node.value)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and VERSION_IDENTIFIER.match(kw.arg):
                    candidates.append(kw.value)

        for value in candidates:
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and DECLARED_VERSION_SHAPE.match(value.value)
            ):
                found.append((label, value.lineno, value.value))
    return found


def build_shaped_literals_in_python(source: str, label: str) -> List[Tuple[str, int, str]]:
    """Non-docstring string literals of ECM build shape in a Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - product tree must parse
        return []

    docstrings = _docstring_constant_ids(tree)
    found: List[Tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            for match in BUILD_VERSION_SHAPE.finditer(node.value):
                found.append((label, node.lineno, match.group(0)))
    return found


def documented_touchpoints(versioning_md: str) -> List[str]:
    """Repo-relative paths listed in the Touchpoints table of versioning.md.

    ``docs/versioning.md`` instructs anyone adding a fourth touchpoint to "add
    a row to the table above". This parses that table so the instruction is
    enforced rather than merely written down.
    """
    section_match = re.search(
        r"^## Touchpoints\s*$(.*?)^## ", versioning_md, re.M | re.S
    )
    if not section_match:
        return []

    paths: List[str] = []
    for line in section_match.group(1).splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        # Skip the header row and the |---|---| separator row.
        if first.lower() == "file" or (first and set(first) <= set("- :")):
            continue
        path_match = re.search(r"`([^`]+)`", first)
        if path_match:
            paths.append(path_match.group(1))
    return paths


def declared_versions_in_file(path: pathlib.Path, label: str) -> List[Tuple[str, int, str]]:
    """Version declarations in one file, dispatched on file type.

    Generic on purpose: clause 3 reads whatever files ``docs/versioning.md``
    names, so a documented touchpoint in a file type the Python scan does not
    cover is still read and still checked.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        return declared_versions_in_python(text, label)
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:  # pragma: no cover - product tree is valid
            return []
        value = data.get("version") if isinstance(data, dict) else None
        if isinstance(value, str) and DECLARED_VERSION_SHAPE.match(value):
            return [(label, 0, value)]
        return []
    # Anything else: text scan for a version-declaration assignment.
    found: List[Tuple[str, int, str]] = []
    pattern = re.compile(
        r"""(?:^|[\s,{("'])(?:[A-Za-z_]*_)?version["']?\s*[:=]\s*["']"""
        r"""(\d+\.\d+\.\d+(?:-\d{4})?)["']""",
        re.IGNORECASE | re.M,
    )
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in pattern.finditer(line):
            found.append((label, lineno, match.group(1)))
    return found


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _canonical_version() -> str:
    data = json.loads((REPO_ROOT / CANONICAL_FILE).read_text(encoding="utf-8"))
    version = data["version"]
    assert isinstance(version, str) and version, (
        f"{CANONICAL_FILE} must declare a non-empty string 'version'"
    )
    return version


def _discover_all_declarations() -> List[Tuple[str, int, str]]:
    """Every version declaration across the scanned product surface."""
    found: List[Tuple[str, int, str]] = []
    found.extend(
        declared_versions_in_file(REPO_ROOT / CANONICAL_FILE, CANONICAL_FILE)
    )
    for path in _iter_product_py_files():
        found.extend(
            declared_versions_in_python(
                path.read_text(encoding="utf-8", errors="replace"), _rel(path)
            )
        )
    for rel in PRODUCT_TEXT_FILES:
        path = REPO_ROOT / rel
        if not path.exists() or rel == CANONICAL_FILE or path.suffix == ".py":
            continue
        found.extend(declared_versions_in_file(path, rel))
    return found


def _format_disagreements(
    canonical: str, declarations: List[Tuple[str, int, str]]
) -> str:
    """Human-readable failure body naming which file disagrees.

    A guard that says only "versions disagree" makes the reader do the search
    the guard already did. Every message from this module names the file, the
    line, the value found and the value expected.
    """
    lines = [
        f"Version touchpoints are out of lockstep.",
        f"  canonical ({CANONICAL_FILE}): {canonical}",
        "  disagreeing declarations:",
    ]
    for label, lineno, value in declarations:
        where = f"{label}:{lineno}" if lineno else label
        lines.append(f"    {where}: found {value!r}, expected {canonical!r}")
    lines.append(
        "  Fix: bump every touchpoint in lockstep "
        "(docs/versioning.md -> Touchpoints)."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Clause 1 — declarations agree
# --------------------------------------------------------------------------
def test_all_discovered_version_declarations_agree():
    """INVARIANT: every discovered version declaration equals the canonical."""
    canonical = _canonical_version()
    declarations = _discover_all_declarations()

    assert declarations, (
        "Discovered zero version declarations. The scan is broken — a guard "
        "that finds nothing passes vacuously. Check REPO_ROOT resolution "
        f"(computed {REPO_ROOT}) and PRODUCT_PY_ROOTS."
    )
    # The canonical file plus at least the two backend touchpoints.
    assert len(declarations) >= 3, (
        f"Expected at least 3 version declarations, discovered "
        f"{len(declarations)}: {declarations}. A touchpoint has gone missing "
        "or the scan no longer reaches it."
    )

    disagreeing = [d for d in declarations if d[2] != canonical]
    assert not disagreeing, _format_disagreements(canonical, disagreeing)


# --------------------------------------------------------------------------
# Clause 2 — no stale build-shaped literal anywhere in product code
# --------------------------------------------------------------------------
def test_no_stale_build_version_literal_in_product_code():
    """INVARIANT: any X.Y.Z-NNNN literal in product code is the current one.

    Broader than clause 1: it does not care what syntax declares the value,
    only that an ECM build-shaped string exists. Catches a hardcoded version
    in a dict literal, an f-string default, a header value — shapes the
    declaration scan does not model.
    """
    canonical = _canonical_version()
    stale: List[Tuple[str, int, str]] = []

    for path in _iter_product_py_files():
        for hit in build_shaped_literals_in_python(
            path.read_text(encoding="utf-8", errors="replace"), _rel(path)
        ):
            if hit[2] != canonical:
                stale.append(hit)

    for rel in PRODUCT_TEXT_FILES:
        path = REPO_ROOT / rel
        if not path.exists() or path.suffix == ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in BUILD_VERSION_SHAPE.finditer(line):
                if match.group(0) != canonical:
                    stale.append((rel, lineno, match.group(0)))

    assert not stale, _format_disagreements(canonical, stale)


# --------------------------------------------------------------------------
# Clause 3 — the documented set and the discovered set are the same set
# --------------------------------------------------------------------------
def test_documented_touchpoints_and_discovered_touchpoints_are_the_same_set():
    """INVARIANT: docs/versioning.md's Touchpoints table == what the scan finds.

    This is the clause that stops a fourth touchpoint appearing outside the
    guard. Add one in code without documenting it and the discovered set grows
    past the documented set; document one that does not exist and the
    documented set grows past the discovered set. Either way this fails, and
    names the offending path.
    """
    canonical = _canonical_version()
    versioning_md = (REPO_ROOT / "docs/versioning.md").read_text(encoding="utf-8")

    documented = documented_touchpoints(versioning_md)
    assert documented, (
        "Could not parse a Touchpoints table out of docs/versioning.md. The "
        "guard reads that table as the registry; if the table moved or its "
        "shape changed, update documented_touchpoints()."
    )

    discovered = sorted({label for label, _, _ in _discover_all_declarations()})
    documented_set = sorted(set(documented))

    undocumented = sorted(set(discovered) - set(documented_set))
    assert not undocumented, (
        "These files declare a version but are NOT listed in the Touchpoints "
        f"table of docs/versioning.md: {undocumented}\n"
        "  A version declaration outside the documented registry is exactly "
        "the drift this guard exists to stop. Add a table row (see "
        "'When you add a fourth touchpoint' in that doc), or stop declaring "
        "the version there."
    )

    missing = sorted(set(documented_set) - set(discovered))
    assert not missing, (
        "docs/versioning.md lists these touchpoints but the scan found no "
        f"version declaration in them: {missing}\n"
        "  Either the touchpoint was removed and the doc was not updated, or "
        "it is declared in a shape the scan does not recognise — in which "
        "case extend declared_versions_in_file()."
    )

    # Each documented file must independently yield the canonical value.
    for rel in documented_set:
        path = REPO_ROOT / rel
        assert path.exists(), (
            f"docs/versioning.md names touchpoint {rel!r}, which does not exist."
        )
        declarations = declared_versions_in_file(path, rel)
        assert declarations, (
            f"Documented touchpoint {rel} yielded no version declaration."
        )
        bad = [d for d in declarations if d[2] != canonical]
        assert not bad, _format_disagreements(canonical, bad)


def test_package_lock_is_documented_as_a_non_touchpoint():
    """The deliberate exclusion is recorded where a reader will look.

    ``frontend/package-lock.json`` is a fourth place the version appears and
    is knowingly allowed to lag. That decision has to be visible in the doc,
    not only here — an undocumented exclusion is indistinguishable from an
    oversight, which is how it got filed as a finding in the first place.
    """
    versioning_md = (REPO_ROOT / "docs/versioning.md").read_text(encoding="utf-8")
    for rel in NON_TOUCHPOINTS:
        assert rel in versioning_md, (
            f"{rel} is excluded from the version guard but docs/versioning.md "
            "does not say so. Document the exclusion and its reason."
        )
        assert rel not in documented_touchpoints(versioning_md), (
            f"{rel} is listed in the Touchpoints table but the guard treats "
            "it as a non-touchpoint. Pick one."
        )


def test_versioning_doc_does_not_claim_a_ci_guard_that_was_removed():
    """The doc must not tell a reader they are protected by a deleted job.

    The Touchpoints section said the checker was removed while the history
    section directly below still closed by crediting "the CI guard (this job)"
    with blocking all future divergence. A reader who stopped at the history
    came away believing a guard existed. That sentence was true when written
    and was never revisited when the job was deleted.

    Scope, stated plainly: this pins *that one retired sentence* out of the
    file and requires the doc to name the enforcement that actually exists. It
    is not a general detector of false claims — no test reads prose for
    truthfulness. Its value is that the specific sentence which misled readers
    for months cannot come back, and that the doc cannot describe enforcement
    without pointing at a real module.
    """
    versioning_md = (REPO_ROOT / "docs/versioning.md").read_text(encoding="utf-8")

    retired_claim = "the CI guard (this job) blocks any future divergence"
    assert retired_claim not in versioning_md, (
        "docs/versioning.md still claims a CI guard blocks version divergence. "
        "The version-consistency job was removed in commit 3404d2d5; the guard "
        "is now this pytest module. Describe the actual enforcement.\n"
        "  (If you are quoting the retired sentence in a historical note, "
        "paraphrase it instead — this check cannot tell a quotation from a "
        "claim.)"
    )

    # Positive half: the doc must point at enforcement that exists.
    this_module = "backend/tests/unit/test_version_touchpoint_consistency.py"
    assert this_module in versioning_md, (
        f"docs/versioning.md must name {this_module} as the enforcement for "
        "the touchpoint lockstep. A doc that asserts an invariant without "
        "citing its enforcement is what produced this bead."
    )
    assert (REPO_ROOT / this_module).is_file(), (
        f"docs/versioning.md cites {this_module}, which does not exist."
    )


# --------------------------------------------------------------------------
# The guard tests itself (engineering-discipline: "Enforcement Code Tests
# Itself"). These drive the helpers with synthetic input, so they prove the
# comparison logic can actually go red — a guard that cannot fail is not a
# guard.
# --------------------------------------------------------------------------
class TestGuardMechanics:
    def test_detects_a_desynchronised_python_assignment(self):
        found = declared_versions_in_python(
            'APP_VERSION = "0.16.0-0003"\n', "backend/routers/backup.py"
        )
        assert found == [("backend/routers/backup.py", 1, "0.16.0-0003")]
        disagreeing = [d for d in found if d[2] != "0.18.1-0141"]
        message = _format_disagreements("0.18.1-0141", disagreeing)
        assert "backend/routers/backup.py:1" in message
        assert "0.16.0-0003" in message

    def test_detects_a_desynchronised_fastapi_kwarg(self):
        found = declared_versions_in_python(
            'app = FastAPI(title="ECM", version="0.16.0-0003")\n', "backend/main.py"
        )
        assert [f[2] for f in found] == ["0.16.0-0003"]

    def test_accepts_a_suffixless_release_cut_version(self):
        found = declared_versions_in_python('APP_VERSION = "0.18.1"\n', "x.py")
        assert [f[2] for f in found] == ["0.18.1"]

    def test_ignores_a_version_cited_in_a_docstring(self):
        # dispatcharr_client.py documents Dispatcharr's version this way.
        source = '"""Returns {"version": "0.28.2"}."""\nx = 1\n'
        assert declared_versions_in_python(source, "x.py") == []
        assert build_shaped_literals_in_python(
            '"""Seen on ECM 0.18.1-0022 during the drill."""\nx = 1\n', "x.py"
        ) == []

    def test_ignores_a_version_cited_in_a_comment(self):
        source = "# build 0.17.6-0152 introduced this\nSTATUS = 'ok'\n"
        assert build_shaped_literals_in_python(source, "x.py") == []

    def test_catches_a_build_literal_the_declaration_scan_would_miss(self):
        # A hardcoded version in a dict literal: no version-named target, no
        # version kwarg — clause 1 cannot see it, clause 2 must.
        source = 'HEADERS = {"X-ECM-Build": "0.16.0-0003"}\n'
        assert declared_versions_in_python(source, "x.py") == []
        assert [h[2] for h in build_shaped_literals_in_python(source, "x.py")] == [
            "0.16.0-0003"
        ]

    def test_does_not_match_an_unrelated_third_party_version(self):
        source = 'DISPATCHARR_MIN = "0.28.2"\nrequests_version = "2.31.0"\n'
        # Neither identifier is version-shaped in the ECM sense...
        found = declared_versions_in_python(source, "x.py")
        # ``requests_version`` does match the identifier pattern by design —
        # any *_version assignment is a candidate. It is caught, which is the
        # safe direction: an unexpected declaration must be justified rather
        # than silently ignored.
        assert ("x.py", 2, "2.31.0") in found
        assert not any(f[2] == "0.28.2" for f in found)

    def test_documented_touchpoints_parses_the_real_table_shape(self):
        md = (
            "## Touchpoints\n\n"
            "| File | Line shape |\n"
            "| --- | --- |\n"
            "| [`frontend/package.json`](../frontend/package.json) | x |\n"
            "| [`backend/main.py`](../backend/main.py) | y |\n"
            "\n## Next Section\n"
        )
        assert documented_touchpoints(md) == [
            "frontend/package.json",
            "backend/main.py",
        ]

    def test_documented_touchpoints_returns_empty_without_a_section(self):
        assert documented_touchpoints("# no touchpoints here\n") == []

    def test_json_touchpoint_reader(self, tmp_path):
        p = tmp_path / "package.json"
        p.write_text(json.dumps({"name": "ecm", "version": "0.18.1-0141"}))
        assert declared_versions_in_file(p, "package.json") == [
            ("package.json", 0, "0.18.1-0141")
        ]

    def test_text_touchpoint_reader(self, tmp_path):
        p = tmp_path / "Dockerfile"
        p.write_text('ARG ECM_VERSION=unknown\nENV APP_VERSION="0.18.1-0141"\n')
        assert [d[2] for d in declared_versions_in_file(p, "Dockerfile")] == [
            "0.18.1-0141"
        ]

    def test_failure_message_names_every_disagreeing_file(self):
        message = _format_disagreements(
            "0.18.1-0141",
            [
                ("backend/main.py", 150, "0.16.0-0003"),
                ("backend/routers/backup.py", 120, "0.16.0"),
            ],
        )
        assert "backend/main.py:150" in message
        assert "backend/routers/backup.py:120" in message
        assert "0.18.1-0141" in message

    def test_repo_root_resolution_finds_the_canonical_file(self):
        # If this fails, every other clause in this module is passing
        # vacuously against the wrong tree.
        assert (REPO_ROOT / CANONICAL_FILE).is_file(), (
            f"REPO_ROOT resolved to {REPO_ROOT}, which has no {CANONICAL_FILE}"
        )
        assert (REPO_ROOT / "docs/versioning.md").is_file()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.18.1-0141", True),
        ("0.18.1", True),
        ("0.18", False),
        ("0.18.1-141", False),
        ("v0.18.1", False),
        ("", False),
    ],
)
def test_declared_version_shape(value, expected):
    assert bool(DECLARED_VERSION_SHAPE.match(value)) is expected
