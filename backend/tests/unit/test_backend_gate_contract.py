"""The backend gate has exactly one invocation (bead enhancedchannelmanager-c9lb9).

THE INVARIANT
-------------
**The gate an engineer runs locally and the gate CI runs to decide the PR are
the same invocation, and everything it excludes is named.**

WHY THIS EXISTS
---------------
Two backend gate invocations were circulating, differing by 72 collected tests:

======================================  ==========  ==================
invocation                              collected   where it came from
======================================  ==========  ==================
``pytest tests/``                       11304       backend/CLAUDE.md prose
``pytest --ignore=... -m "not slow"``   11232       .github/workflows/test.yml
======================================  ==========  ==================

Nothing was failing, so this was an instrument gap and not a live defect. It is
still the false-green class: an agent could report "backend gate green" having
never executed 72 tests, and the figure looked authoritative because every
handover repeated it. (The 72 is stable — measured at 35a49d84 when the bead
was filed, and again at f9f7522: ``tests/e2e/`` is 10 files and
``tests/performance/`` is 2.)

Prose gets copied and mutated. ``scripts/backend-gate.sh`` is the single
invocation; this module is what stops it drifting away from CI, by parsing
both and comparing them flag for flag.

WHAT THIS MODULE PINS
---------------------
1. ``scripts/backend-gate.sh`` exists and is executable.
2. Its pytest flags equal the flags in ``.github/workflows/test.yml``.
3. The tests deselected by ``-m "not slow"`` are exactly the two that are
   documented, so "2 deselected" is never an unexplained number again.
4. ``backend/CLAUDE.md`` cites the script instead of spelling out flags.
"""
from __future__ import annotations

import ast
import pathlib
import re
import shlex
from typing import Dict, List, Set

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "scripts/backend-gate.sh"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"
BACKEND_CLAUDE_MD = REPO_ROOT / "backend/CLAUDE.md"

#: Flags that are CI plumbing rather than gate semantics, normalised away
#: before comparison. ``--junitxml`` writes the report the flake-PR-comment
#: workflow consumes; it changes no test outcome.
PLUMBING_PREFIXES = ("--junitxml",)

#: The complete set of tests excluded by ``-m "not slow"``, each with the
#: reason it is excluded. "2 deselected" with no explanation is unreadable;
#: this is the explanation, and test_slow_marked_tests_are_exactly_the_named_set
#: keeps it true.
DOCUMENTED_SLOW_TESTS: Dict[str, str] = {
    "tests/services/test_dedup_matcher.py::TestPerformanceMicrobench"
    "::test_find_candidate_under_5ms_per_call_for_500_candidates": (
        "Wall-clock microbenchmark asserting a 5ms soft cap per call. Host "
        "contention on a CI runner false-fails it, and it gates nothing — the "
        "latency figure is informational. Still runs under an explicit "
        "'-m slow' or a full-suite invocation."
    ),
    "tests/integration/test_session_telemetry_migration.py"
    "::test_migration_up_down_against_5m_rows": (
        "Seeds ~5M session_telemetry rows to time an Alembic up/down against "
        "volume. Local pre-merge gate only (bd-skqln.2); additionally guarded "
        "by ECM_RUN_VOLUME_TESTS, so it self-skips even when selected."
    ),
}


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def _join_continued_lines(lines: List[str], start: int) -> str:
    """Join a backslash-continued shell command starting at ``lines[start]``."""
    out = []
    idx = start
    while idx < len(lines):
        raw = lines[idx].rstrip()
        stripped = raw.rstrip("\\").strip()
        out.append(stripped)
        if not raw.endswith("\\"):
            break
        idx += 1
    return " ".join(out)


def _normalise(command: str) -> Set[str]:
    """Reduce a ``... -m pytest ...`` command line to its comparable flags."""
    tokens = shlex.split(command)
    # Drop everything up to and including the literal 'pytest'.
    if "pytest" in tokens:
        tokens = tokens[tokens.index("pytest") + 1 :]
    keep: List[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            keep.append(f"-m={tok}")
            skip_next = False
            continue
        if tok == "-m":
            skip_next = True
            continue
        if tok in ("$@", "exec"):
            continue
        if tok.startswith(PLUMBING_PREFIXES):
            continue
        keep.append(tok)
    return set(keep)


def ci_pytest_flags() -> Set[str]:
    """The flags of the backend 'Run pytest' step in test.yml."""
    lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if "python -m pytest" in line and not line.strip().startswith("#"):
            command = _join_continued_lines(lines, i)
            # The backend gate is the one that ignores tests/e2e; the
            # mcp-server job in the same file runs `pytest tests/`.
            if "--ignore=tests/e2e" in command:
                return _normalise(command)
    raise AssertionError(
        "Could not find the backend 'Run pytest' invocation in "
        f"{CI_WORKFLOW}. If the workflow was restructured, update this parser "
        "— do not delete the check."
    )


def gate_script_pytest_flags() -> Set[str]:
    """The flags of the gate invocation in scripts/backend-gate.sh.

    The script contains two pytest invocations: the gate, and the ``--subset``
    escape hatch. They are told apart by ``--no-cov``, which only the subset
    mode passes — deliberately not by ``--ignore=tests/e2e``, so that a script
    which has *lost* an ignore is still recognised as the gate and reported as
    drift rather than as a missing invocation.
    """
    lines = GATE_SCRIPT.read_text(encoding="utf-8").splitlines()
    candidates: List[str] = []
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        if "-m pytest" in line:
            candidates.append(_join_continued_lines(lines, i))

    gate = [c for c in candidates if "--no-cov" not in c]
    assert gate, (
        f"Could not find a gate pytest invocation in {GATE_SCRIPT}. "
        f"Found {len(candidates)} pytest invocation(s), all of which pass "
        "--no-cov (i.e. all subset-mode). The gate must run with coverage, "
        "as CI does."
    )
    assert len(gate) == 1, (
        f"{GATE_SCRIPT} has {len(gate)} non-subset pytest invocations. There "
        "must be exactly one — 'which invocation is the gate?' is the whole "
        f"question this module answers. Found: {gate}"
    )
    return _normalise(gate[0])


def _slow_marked_tests() -> Set[str]:
    """``file::Class::func`` for every test carrying @pytest.mark.slow.

    Static (AST) discovery rather than a pytest sub-collection: this test must
    not shell out to another pytest run inside the gate.
    """
    tests_root = REPO_ROOT / "backend/tests"
    found: Set[str] = set()

    def is_slow(node: ast.AST) -> bool:
        for dec in getattr(node, "decorator_list", []):
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "slow":
                value = target.value
                if isinstance(value, ast.Attribute) and value.attr == "mark":
                    return True
        return False

    for path in sorted(tests_root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT / "backend").as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_slow(node):
                    found.add(f"{rel}::{node.name}")
            elif isinstance(node, ast.ClassDef):
                class_slow = is_slow(node)
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if class_slow or is_slow(sub):
                            found.add(f"{rel}::{node.name}::{sub.name}")
    return found


# --------------------------------------------------------------------------
# Clause 1 — the script exists and is runnable
# --------------------------------------------------------------------------
def test_gate_script_exists_and_is_executable():
    assert GATE_SCRIPT.is_file(), (
        f"{GATE_SCRIPT} is missing. backend/CLAUDE.md and docs/testing.md "
        "point at it as THE backend gate."
    )
    import os

    assert os.access(GATE_SCRIPT, os.X_OK), (
        f"{GATE_SCRIPT} is not executable (chmod +x). A gate nobody can run "
        "gets replaced by a hand-typed invocation, which is the drift this "
        "script exists to end."
    )


def test_gate_script_pins_the_interpreter_rather_than_trusting_path():
    """The environment is part of the gate.

    Ambient ``python`` resolves an older cryptography and *self-skips* 9 TLS
    tests instead of failing, so the run still reports success. The script must
    select the interpreter itself, and must refuse rather than fall back.
    """
    body = GATE_SCRIPT.read_text(encoding="utf-8")
    assert ".venv/bin/python" in body
    assert "ECM_PYTHON" in body
    assert "rev-parse --git-common-dir" in body, (
        "A worktree has no .venv of its own. The script must derive the main "
        "checkout's interpreter instead of leaving the caller to hardcode a "
        "path — that hardcoding is how the wrong interpreter gets used."
    )


# --------------------------------------------------------------------------
# Clause 2 — script and CI cannot drift apart
# --------------------------------------------------------------------------
def test_gate_script_invocation_matches_ci_exactly():
    """INVARIANT: the local gate and the required check run the same thing."""
    ci = ci_pytest_flags()
    gate = gate_script_pytest_flags()

    only_ci = sorted(ci - gate)
    only_gate = sorted(gate - ci)

    assert not (only_ci or only_gate), (
        "scripts/backend-gate.sh has drifted from the CI backend gate.\n"
        f"  in CI but not the script : {only_ci}\n"
        f"  in the script but not CI : {only_gate}\n"
        "  A local gate that runs a different set than the required check is "
        "not a gate. Change both, or neither.\n"
        "  (Pure-plumbing flags are normalised away: "
        f"{list(PLUMBING_PREFIXES)}.)"
    )
    # Guard against a vacuous pass if both parsers silently return nothing.
    assert "--ignore=tests/e2e" in gate
    assert '-m=not slow' in gate


# --------------------------------------------------------------------------
# Clause 3 — everything excluded is named
# --------------------------------------------------------------------------
def test_slow_marked_tests_are_exactly_the_named_set():
    """INVARIANT: '2 deselected' is always attributable to named tests."""
    actual = _slow_marked_tests()
    documented = set(DOCUMENTED_SLOW_TESTS)

    undocumented = sorted(actual - documented)
    assert not undocumented, (
        "These tests are marked 'slow' — and therefore silently deselected "
        "from every gate run — without being named anywhere:\n  "
        + "\n  ".join(undocumented)
        + "\n  Add each to DOCUMENTED_SLOW_TESTS with the reason it is "
        "excluded, and to docs/testing.md. An unexplained deselect count is "
        "exactly the unreadable signal bead c9lb9 was filed about."
    )

    stale = sorted(documented - actual)
    assert not stale, (
        "DOCUMENTED_SLOW_TESTS names tests that are no longer marked 'slow' "
        f"(renamed, moved, or unmarked): {stale}\n"
        "  Update the registry so the documented exclusions stay real."
    )

    assert len(actual) == 2, (
        f"Expected exactly 2 slow-marked tests, found {len(actual)}. Update "
        "the count in docs/testing.md and backend/CLAUDE.md alongside the "
        "registry."
    )


def test_every_documented_slow_test_states_a_reason():
    for name, reason in DOCUMENTED_SLOW_TESTS.items():
        assert reason and len(reason) > 40, (
            f"{name} is excluded without a substantive reason. 'Excluded "
            "because it is slow' is not a reason; say what breaks if it runs "
            "in the gate."
        )


def test_ignored_directories_are_named_in_the_script():
    """The two --ignore'd trees must carry their justification at the site."""
    body = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "tests/e2e" in body and "localhost:6100" in body, (
        "scripts/backend-gate.sh must say why tests/e2e is excluded "
        "(it needs a live container)."
    )
    assert "tests/performance" in body and "perf-benchmarks" in body, (
        "scripts/backend-gate.sh must say why tests/performance is excluded "
        "(it runs in the perf-benchmarks workflow)."
    )


# --------------------------------------------------------------------------
# Clause 4 — the docs cite the target, not the flags
# --------------------------------------------------------------------------
def test_backend_claude_md_cites_the_script_and_not_a_raw_invocation():
    """Flags spelled out in prose get copied and mutated. That is the bug."""
    body = BACKEND_CLAUDE_MD.read_text(encoding="utf-8")
    assert "scripts/backend-gate.sh" in body, (
        "backend/CLAUDE.md must cite scripts/backend-gate.sh as the gate."
    )

    # The specific prose invocation that diverged from CI must not return.
    forbidden = re.compile(
        r"pytest\s+tests/\s+--tb=short\s+--no-header\s+-p\s+no:warnings"
    )
    assert not forbidden.search(body), (
        "backend/CLAUDE.md spells out the old `pytest tests/ --tb=short "
        "--no-header -p no:warnings` invocation again. That command collects "
        "72 tests CI does not run, and it is the reason two gate figures "
        "circulated. Cite scripts/backend-gate.sh instead."
    )


def test_coverage_subset_trap_is_documented_where_an_agent_will_hit_it():
    """pytest.ini's --cov-fail-under makes every subset run fail.

    ``--cov=.`` measures the whole tree regardless of what was selected, so a
    subset run reports e.g. "Total coverage: 18.39%" and exits non-zero even
    when every test in it passed. Read as a real failure, that sends someone
    off to "fix" a healthy tree. The trap must be documented at the two places
    someone meets it.
    """
    gate_body = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "--no-cov" in gate_body and "--cov-fail-under" in gate_body, (
        "scripts/backend-gate.sh must offer a subset mode that disables "
        "coverage and explain why."
    )
    claude_body = BACKEND_CLAUDE_MD.read_text(encoding="utf-8")
    assert "--no-cov" in claude_body, (
        "backend/CLAUDE.md must warn that any subset run fails on coverage "
        "unless --no-cov is passed."
    )


# --------------------------------------------------------------------------
# The parsers test themselves.
# --------------------------------------------------------------------------
class TestParserMechanics:
    def test_normalise_strips_plumbing_and_wrapper_tokens(self):
        assert _normalise(
            'exec "$PY" -m pytest --ignore=tests/e2e -m "not slow" '
            '--junitxml=junit.xml "$@"'
        ) == {"--ignore=tests/e2e", "-m=not slow"}

    def test_normalise_keeps_marker_expression_attached_to_its_flag(self):
        # A bare set would make `-m "not slow"` and `-m "slow"` compare equal
        # on the flag alone. The value must ride with it.
        assert _normalise("pytest -m 'not slow'") != _normalise("pytest -m 'slow'")

    def test_join_continued_lines(self):
        lines = ["python -m pytest \\", "    --ignore=tests/e2e \\", "    -q", "next"]
        assert _join_continued_lines(lines, 0) == (
            "python -m pytest --ignore=tests/e2e -q"
        )

    def test_detects_a_drifted_script(self):
        """The comparison must be able to fail — prove it on synthetic input."""
        ci = _normalise('pytest --ignore=tests/e2e --ignore=tests/performance -m "not slow"')
        drifted = _normalise('pytest --ignore=tests/e2e -m "not slow"')
        assert ci != drifted
        assert sorted(ci - drifted) == ["--ignore=tests/performance"]

    def test_slow_detection_finds_a_decorated_function(self, tmp_path):
        src = "import pytest\n\n@pytest.mark.slow\ndef test_x():\n    pass\n"
        tree = ast.parse(src)
        node = tree.body[-1]
        dec = node.decorator_list[0]
        assert isinstance(dec, ast.Attribute) and dec.attr == "slow"

    def test_ci_parser_finds_the_backend_job_not_the_mcp_job(self):
        flags = ci_pytest_flags()
        assert "--ignore=tests/e2e" in flags, (
            "The parser matched the wrong 'Run pytest' step — the mcp-server "
            "job in the same workflow runs a plain `pytest tests/`."
        )


@pytest.mark.parametrize("name", sorted(DOCUMENTED_SLOW_TESTS))
def test_documented_slow_test_file_exists(name):
    rel = name.split("::")[0]
    assert (REPO_ROOT / "backend" / rel).is_file(), (
        f"DOCUMENTED_SLOW_TESTS names {rel}, which does not exist."
    )
