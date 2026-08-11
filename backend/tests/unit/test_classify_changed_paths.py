"""Tests for ``scripts/classify_changed_paths.py``.

Beads enhancedchannelmanager-5rwzy (``code_paths_changed``) and
enhancedchannelmanager-t4d5w (``docs_site_affected``).

The classifier is the gate that decides whether a required CI status check
runs its real work or passes as a no-op. A wrong ``code_paths_changed=false`` verdict
turns ``Backend Tests`` green without pytest ever running, which is the exact
class of defect this bead exists to close. So the accept/reject boundary and
the fail-open behaviour are both pinned here.

``docs_site_affected`` decides whether the published user-guide site rebuilds.
Its dangerous verdict is the opposite one: a wrong ``false`` leaves the live
site stale behind merged content, so it fails open to ``true``.

Inputs are built inline rather than read from the repo so the tests stay
stable as the tree churns. The exceptions are the workflow-contract tests at
the bottom, which read the real workflow files on purpose.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "classify_changed_paths.py"


def _load_script_module():
    """Load classify_changed_paths.py as an ad-hoc module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("classify_changed_paths", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_changed_paths"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


# --- The only paths CI may treat as inert machine state ---------------------


class TestInertPaths:
    @pytest.mark.parametrize(
        "path",
        [
            ".beads/enhancedchannelmanager.jsonl",
            ".beads/issues.jsonl.bak",
            ".beads/metadata.json",
        ],
    )
    def test_recognised_as_inert(self, script, path):
        assert script.is_inert_path(path) is True


class TestCodePaths:
    @pytest.mark.parametrize(
        "path",
        [
            "backend/main.py",
            "frontend/src/App.tsx",
            ".github/workflows/test.yml",
            "scripts/classify_changed_paths.py",
            "frontend/package.json",
            # A directory named like a doc file is still not a doc file: the
            # rule matches on the trailing path segment.
            "docs.md/handler.py",
            # `beads` without the leading dot is an ordinary directory.
            "beads/tool.py",
            # `**.md` matches the file NAME, so a bare `.md` file is not prose.
            ".md",
            ".beads/config.yaml",
            ".beads/.gitignore",
            ".beads/backup/2026-08-08.db",
            ".beads/config.json",
            ".beads/metadata.json.bak",
            ".beads/nested/issues.jsonl",
            ".beads/issues.jsonl.bak.extra",
            # Live Markdown fixture consumed by a backend test.
            "docs/style_guide.md",
            "docs/security/threat_model_dbas_import.md",
            "docs/shipping.md",
            "README.md",
            "CHANGELOG.md",
            "nested/notes.md",
            "docs/user_guide/backup-restore/run-a-restore-drill.md",
            "symlink-name.md",
            "docs/UPPER.MD",
            "docs/Mixed.Md",
            "docs/images/architecture.png",
            "graphify-out/memory/report.txt",
            "docs/prometheus_rules.yaml",
            "mkdocs.yml",
            "docs/requirements-docs.txt",
        ],
    )
    def test_recognised_as_code(self, script, path):
        assert script.is_inert_path(path) is False

    def test_beads_prefix_is_not_eaten_by_lstrip(self, script):
        """Regression guard: ``lstrip('./')`` would strip the leading dot of
        ``.beads/`` and turn every beads path into ``beads/``. The path must
        still classify as documentation."""
        assert script.is_inert_path(".beads/issues.jsonl") is True


def test_classifier_consumers_use_only_positive_output_name():
    positive_consumers = [
        REPO_ROOT / ".github/workflows/test.yml",
        REPO_ROOT / ".github/workflows/build.yml",
        REPO_ROOT / ".claude/hooks/version-advance-guard.sh",
    ]
    all_consumers = positive_consumers + [
        REPO_ROOT / ".github/workflows/docs-pages.yml",
        REPO_ROOT / "docs/shipping.md",
    ]
    for consumer in all_consumers:
        text = consumer.read_text(encoding="utf-8")
        assert "docs_only" not in text, consumer
    for consumer in positive_consumers:
        text = consumer.read_text(encoding="utf-8")
        assert "code_paths_changed" in text, consumer
    assert "docs_site_affected" in all_consumers[3].read_text(encoding="utf-8")


# --- Whole-set classification ----------------------------------------------


class TestClassify:
    def test_root_machine_beads_state_only_is_inert(self, script):
        code_paths_changed, code_paths = script.classify(
            [".beads/issues.jsonl", ".beads/archive.jsonl.bak", ".beads/metadata.json"]
        )
        assert code_paths_changed is False
        assert code_paths == []

    def test_all_markdown_runs_code_gates(self, script):
        code_paths_changed, code_paths = script.classify(["CHANGELOG.md", "docs/api.md"])
        assert code_paths_changed is True
        assert code_paths == ["CHANGELOG.md", "docs/api.md"]

    def test_markdown_makes_machine_beads_change_code(self, script):
        code_paths_changed, code_paths = script.classify(
            [".beads/enhancedchannelmanager.jsonl", "docs/api.md"]
        )
        assert code_paths_changed is True
        assert code_paths == ["docs/api.md"]

    def test_mixed_change_is_code(self, script):
        """The defect this bead closes: a PR touching code AND Markdown.

        Every shipped change carries a CHANGELOG entry, so this is the shape
        of nearly every PR in the repo. It must classify as code."""
        code_paths_changed, code_paths = script.classify(["CHANGELOG.md", "backend/main.py"])
        assert code_paths_changed is True
        assert code_paths == ["CHANGELOG.md", "backend/main.py"]

    def test_code_only_is_code(self, script):
        code_paths_changed, code_paths = script.classify(["backend/main.py"])
        assert code_paths_changed is True
        assert code_paths == ["backend/main.py"]

    def test_empty_set_fails_open_to_code(self, script):
        """An undetermined file set must run the real work, never no-op green."""
        code_paths_changed, code_paths = script.classify([])
        assert code_paths_changed is True

    def test_blank_or_whitespace_names_fail_open_to_code(self, script):
        code_paths_changed, _ = script.classify(["", "  ", "docs/api.md"])
        assert code_paths_changed is True

    def test_blank_only_set_fails_open_to_code(self, script):
        code_paths_changed, _ = script.classify(["", "   ", "\t"])
        assert code_paths_changed is True


# --- Paths the published user-guide site is built from ----------------------


class TestDocsSitePaths:
    @pytest.mark.parametrize(
        "path",
        [
            "docs/user_guide/index.md",
            "docs/user_guide/stats/bandwidth.md",
            "docs/images/user_guide/dashboard.png",
            "docs/index.md",
            "mkdocs.yml",
            "docs/requirements-docs.txt",
            ".github/workflows/docs-pages.yml",
        ],
    )
    def test_recognised_as_site_input(self, script, path):
        assert script.is_docs_site_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            # Internal documentation. `mkdocs.yml` excludes every one of
            # these directories, so changing them cannot change the site.
            "docs/shipping.md",
            "docs/testing.md",
            "docs/adr/ADR-005-code-security-gating-strategy.md",
            "docs/runbooks/restore.md",
            "docs/security/threat_model_dbas_import.md",
            "docs/sre/slos.md",
            # Images outside the published tree.
            "docs/images/architecture.png",
            # Source, and a workflow that is not the site's workflow.
            "backend/main.py",
            ".github/workflows/test.yml",
            # Prefix lookalikes: the match is on a path boundary.
            "docs/user_guides/index.md",
            "docs/index.markdown",
            "other/mkdocs.yml",
        ],
    )
    def test_recognised_as_not_site_input(self, script, path):
        assert script.is_docs_site_path(path) is False


class TestClassifyDocsSite:
    def test_a_user_guide_page_affects_the_site(self, script):
        affected, site_paths = script.classify_docs_site(
            ["docs/user_guide/stats/index.md", "backend/main.py"]
        )
        assert affected is True
        assert site_paths == ["docs/user_guide/stats/index.md"]

    def test_internal_docs_do_not_affect_the_site(self, script):
        affected, site_paths = script.classify_docs_site(
            ["docs/shipping.md", "docs/adr/ADR-001.md"]
        )
        assert affected is False
        assert site_paths == []

    def test_empty_set_fails_open_to_affected(self, script):
        """The opposite fail-open direction from ``code_paths_changed``.

        A missed rebuild leaves the published site stale behind merged
        content; a needless rebuild costs a runner minute."""
        affected, site_paths = script.classify_docs_site([])
        assert affected is True
        assert site_paths == []

    def test_blank_only_set_fails_open_to_affected(self, script):
        affected, _ = script.classify_docs_site(["", "   ", "\t"])
        assert affected is True

    def test_the_two_verdicts_are_independent(self, script):
        """Code-gate and documentation-site decisions remain independent.

        The four combinations are all reachable, so neither output may be
        derived from the other."""
        combinations = {
            ("docs/user_guide/index.md",): (True, True),
            ("docs/testing.md",): (True, False),
            ("mkdocs.yml",): (True, True),
            ("backend/main.py",): (True, False),
            (".beads/issues.jsonl",): (False, False),
        }
        for paths, expected in combinations.items():
            code_paths_changed, _ = script.classify(list(paths))
            affected, _ = script.classify_docs_site(list(paths))
            assert (code_paths_changed, affected) == expected, paths


# --- End-to-end CLI contract ------------------------------------------------


def _run_cli(args, stdin_text=""):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


def _outputs(result) -> dict[str, str]:
    """Parse the ``key=value`` lines the workflow appends to $GITHUB_OUTPUT.

    Read by key, never by line position: the workflows do the same, so a
    future third output must not be able to break an existing consumer."""
    parsed = {}
    stdout = result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout
    for line in stdout.splitlines():
        key, _, value = line.partition("=")
        parsed[key] = value
    return parsed


class TestCommandLine:
    def test_stdin_code_paths_changed(self):
        result = _run_cli([], json.dumps(["docs/api.md", "CHANGELOG.md"]))
        assert result.returncode == 0
        assert _outputs(result)["code_paths_changed"] == "true"

    def test_stdin_mixed(self):
        result = _run_cli([], json.dumps(["docs/api.md", "backend/main.py"]))
        assert result.returncode == 0
        assert _outputs(result)["code_paths_changed"] == "true"
        assert "backend/main.py" in result.stderr

    def test_files_from(self, tmp_path):
        listing = tmp_path / "changed.txt"
        listing.write_text(json.dumps(["docs/api.md", ".beads/x.jsonl"]), encoding="utf-8")
        result = _run_cli(["--files-from", str(listing)])
        assert result.returncode == 0
        assert _outputs(result)["code_paths_changed"] == "true"

    def test_missing_files_from_exits_zero_and_fails_open(self, tmp_path):
        """A classifier hiccup must never skip a dependent job."""
        result = _run_cli(["--files-from", str(tmp_path / "nope.txt")])
        assert result.returncode == 0
        assert _outputs(result) == {"code_paths_changed": "true", "docs_site_affected": "true"}
        assert "::warning::" in result.stderr

    def test_empty_input_exits_zero_and_fails_open(self):
        result = _run_cli([], "")
        assert result.returncode == 0
        assert _outputs(result) == {"code_paths_changed": "true", "docs_site_affected": "true"}
        assert "::warning::" in result.stderr

    def test_output_is_exactly_the_two_github_output_keys(self):
        """The workflow appends stdout straight to $GITHUB_OUTPUT, so every
        stdout line must be a well-formed key=value pair and nothing else.

        Pinned as a set, not a sequence: consumers read by key, and pinning
        the order would make adding an output a breaking change for no
        reason."""
        result = _run_cli([], json.dumps(["backend/main.py"]))
        assert set(_outputs(result)) == {"code_paths_changed", "docs_site_affected"}
        assert all("=" in line for line in result.stdout.splitlines())

    def test_site_verdict_on_a_published_page(self):
        result = _run_cli([], json.dumps(["docs/user_guide/stats/bandwidth.md"]))
        assert _outputs(result) == {"code_paths_changed": "true", "docs_site_affected": "true"}

    def test_site_verdict_on_an_internal_doc(self):
        result = _run_cli([], json.dumps(["docs/testing.md"]))
        assert _outputs(result) == {"code_paths_changed": "true", "docs_site_affected": "false"}

    @pytest.mark.parametrize(
        "path",
        ["docs/line\nbreak.md", r"docs\shipping.md", " docs/shipping.md", "docs/shipping.md "],
    )
    def test_lossless_odd_names_fail_open_to_code(self, path):
        result = _run_cli([], json.dumps([path]))
        assert _outputs(result)["code_paths_changed"] == "true"

    @pytest.mark.parametrize("payload", ["not json", "{}", '["docs/a.md", 1]', '["a\\u0000b"]'])
    def test_malformed_or_ambiguous_payload_fails_open(self, payload):
        result = _run_cli([], payload)
        assert _outputs(result) == {"code_paths_changed": "true", "docs_site_affected": "true"}

    def test_git_z_transport_preserves_rename_source_destination_and_delete(self):
        raw = "backend/source.py\0docs/destination.md\0backend/deleted.py\0"
        result = _run_cli(["--git-z"], raw)
        assert _outputs(result)["code_paths_changed"] == "true"

    @pytest.mark.parametrize(
        "raw",
        [
            b"docs/line\nbreak.md\0",
            b"docs\\shipping.md\0",
            b" docs/shipping.md\0",
            b"docs/shipping.md \0",
            b"docs/shipping.md",  # missing terminal NUL is ambiguous
            b"docs/shipping.md\0\0",
        ],
    )
    def test_git_z_odd_or_malformed_names_fail_open(self, raw):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--git-z"],
            input=raw,
            capture_output=True,
            check=False,
        )
        assert _outputs(result)["code_paths_changed"] == "true"


# --- The rule matches what the workflows gate on ----------------------------


class TestWorkflowContract:
    """The whole design rests on required checks being emitted exactly once.

    These assertions read the real workflow files, so a future edit that
    reintroduces a path filter on the workflows carrying required contexts,
    or resurrects a second emitter of those names, fails here rather than
    silently on a pull request."""

    # `CodeQL Analysis` is the job name; its matrix expands it into the two
    # required contexts `CodeQL Analysis (python)` and
    # `CodeQL Analysis (javascript-typescript)`.
    REQUIRED_CONTEXTS = (
        "Backend Tests",
        "Frontend Tests",
        "MCP Server Tests",
        "Semgrep Lint",
        "Version Consistency",
        "CodeQL Analysis",
    )
    WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

    @staticmethod
    def _workflow_files() -> list[Path]:
        """Every workflow file, both spellings.

        GitHub accepts `.yaml` as readily as `.yml`. Globbing only `*.yml`
        would make a `.yaml` workflow invisible to every assertion in this
        class, including the one that forbids a second emitter of a required
        context, so the guard would pass while the defect it exists to catch
        was present."""
        directory = TestWorkflowContract.WORKFLOW_DIR
        return sorted(
            list(directory.glob("*.yml")) + list(directory.glob("*.yaml"))
        )

    @staticmethod
    def _load_workflow(path: Path) -> dict:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def _triggers(workflow: dict) -> dict:
        # YAML 1.1 parses a bare `on:` key as the boolean True, so a workflow
        # trigger block comes back under `True`, not `"on"`.
        return workflow.get(True) or workflow.get("on") or {}

    def test_no_code_paths_changed_sentinel_workflow_remains(self):
        assert not (self.WORKFLOW_DIR / "docs-only-pass.yml").exists()

    @pytest.mark.parametrize("workflow", ["test.yml", "build.yml"])
    def test_required_check_workflows_have_no_path_filters(self, workflow):
        triggers = self._triggers(self._load_workflow(self.WORKFLOW_DIR / workflow))
        for event, config in triggers.items():
            if not isinstance(config, dict):
                continue
            for key in ("paths", "paths-ignore"):
                assert key not in config, (
                    f"{workflow} reintroduced `{key}` on the `{event}` trigger. "
                    f"A workflow that emits a required status check must run on "
                    f"every pull request, or the context goes missing on the "
                    f"filtered half and something else has to fill in for it. "
                    f"Gate the expensive STEPS on the `detect` job instead. "
                    f"See bead enhancedchannelmanager-5rwzy."
                )

    def test_required_context_jobs_can_never_be_skipped(self):
        """The half of the invariant that keeps every PR check set complete.

        GitHub counts a SKIPPED job as satisfying a required status check, so
        a required-context job must never carry a job-level `if:` that can
        evaluate false: it would be the same permanently-satisfied context
        the sentinel workflow used to provide. Every such job either has no
        `if:` at all or guards only against cancellation, and gates its real
        work at the STEP level instead."""
        for path in self._workflow_files():
            jobs = self._load_workflow(path).get("jobs") or {}
            for job_id, job in jobs.items():
                job = job or {}
                if job.get("name", job_id) not in self.REQUIRED_CONTEXTS:
                    continue
                condition = str(job.get("if", "")).strip()
                if not condition:
                    continue
                assert "!cancelled()" in condition, (
                    f"{path.name}:{job_id} emits a required status check but "
                    f"has a job-level `if:` that can evaluate false "
                    f"({condition!r}). A skipped job satisfies a required "
                    f"check without running anything. Gate the steps instead, "
                    f"and keep the job-level guard at `!cancelled()`. "
                    f"See bead enhancedchannelmanager-5rwzy."
                )
                assert "code_paths_changed" not in condition, (
                    f"{path.name}:{job_id} gates the whole job on the "
                    f"documentation-only classification. That skips the job, "
                    f"and a skipped job satisfies the required check it is "
                    f"named for. Gate its steps instead. "
                    f"See bead enhancedchannelmanager-5rwzy."
                )

    def test_each_required_context_has_exactly_one_emitting_job(self):
        emitters: dict[str, list[str]] = {name: [] for name in self.REQUIRED_CONTEXTS}
        for path in self._workflow_files():
            jobs = self._load_workflow(path).get("jobs") or {}
            for job_id, job in jobs.items():
                display_name = (job or {}).get("name", job_id)
                if display_name in emitters:
                    emitters[display_name].append(f"{path.name}:{job_id}")
        for context, sources in emitters.items():
            assert len(sources) == 1, (
                f"required context {context!r} is emitted by {len(sources)} "
                f"job(s) ({sources or 'none'}). Exactly one job may carry the "
                f"name: a second emitter reports its own conclusion under the "
                f"same context, and a permanently green one masks a real "
                f"failure. See bead enhancedchannelmanager-5rwzy."
            )

    def test_no_new_job_name_collides_with_a_required_context(self):
        """Phase 1 is additive: it adds steps, never a new context.

        A job whose display name is one character off a required name is
        indistinguishable in the checks list, and adding a genuinely new
        required-looking name is the Phase 2 change this work deliberately
        stayed out of."""
        required_job_names = {
            "Backend Tests": "test.yml:backend",
            "Frontend Tests": "test.yml:frontend",
            "MCP Server Tests": "test.yml:mcp-server",
            "Semgrep Lint": "test.yml:semgrep-lint",
            "Version Consistency": "test.yml:version-consistency",
            "CodeQL Analysis": "build.yml:codeql-analysis",
        }
        found: dict[str, str] = {}
        for path in self._workflow_files():
            jobs = self._load_workflow(path).get("jobs") or {}
            for job_id, job in jobs.items():
                display_name = (job or {}).get("name", job_id)
                if display_name in required_job_names:
                    found[display_name] = f"{path.name}:{job_id}"
        assert found == required_job_names, (
            "the job carrying each required status check moved, disappeared, "
            "or gained a twin. Branch protection on `dev` is classic with "
            "enforce_admins=true, so a required name that no job emits wedges "
            "every PR with no admin bypass. See bead "
            "enhancedchannelmanager-t4d5w."
        )


# --- Rename sources reach the classifier ------------------------------------


# Every workflow carrying a `detect` job that feeds this classifier. All three
# build their changed-file set from the same GitHub APIs, so all three have to
# ask for both sides of a rename.
DETECT_WORKFLOWS = ("test.yml", "build.yml", "docs-pages.yml")

# `--jq '<expression>'` as the detect step spells it. The expressions never
# contain a single quote, so a non-greedy character class is enough and no
# shell parser is needed.
_JQ_ARGUMENT = re.compile(r"--jq\s+'([^']*)'")

# Recorded from the real GitHub API for commit b47ced96 of this repository,
# via `gh api repos/MotWakorb/enhancedchannelmanager/commits/b47ced96`. Entries
# are verbatim, reduced to the three fields the detect step can read and to
# three of the commit's 18 entries: the renamed one plus one either side of it,
# enough to prove the expression handles a mixed set.
#
# That commit is the evidence in bead enhancedchannelmanager-9ogyd:
# `git diff --name-only b47ced96^...b47ced96` lists 18 paths and the same
# command with `--no-renames` lists 19. The path only the second spelling shows
# is the `previous_filename` below.
RECORDED_RENAME_COMMIT_FILES = [
    {"filename": "CHANGELOG.md", "status": "modified"},
    {"filename": "backend/routers/backup.py", "status": "modified"},
    {
        "filename": "frontend/src/components/settings/OutboundPolicyCard.css",
        "previous_filename": (
            "frontend/src/components/settings/SecuritySettingsSection.css"
        ),
        "status": "renamed",
    },
]
RECORDED_RENAME_SOURCE = (
    "frontend/src/components/settings/SecuritySettingsSection.css"
)

# The attack from bead enhancedchannelmanager-9ogyd, in the shape the
# pull-request files API returns it. One entry, one `.md` destination, and a
# deleted authentication test hiding in `previous_filename`.
RENAME_INTO_MARKDOWN_FILES = [
    {
        "filename": "docs/legacy_auth_test_notes.md",
        "previous_filename": "backend/tests/unit/test_auth_middleware.py",
        "status": "renamed",
    },
]

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(
    JQ is None,
    reason=(
        "the jq binary is not installed. The dependency-free half of this "
        "guard, test_every_detect_workflow_asks_for_rename_sources, still "
        "runs and still fails if the expression drops previous_filename."
    ),
)


def _classify_step_run(workflow_name: str) -> str:
    """The shell body of the `detect` job's `classify` step, verbatim."""
    path = TestWorkflowContract.WORKFLOW_DIR / workflow_name
    job = (TestWorkflowContract._load_workflow(path).get("jobs") or {}).get("detect")
    assert job, f"{workflow_name} no longer has a `detect` job"
    for step in job.get("steps") or []:
        if (step or {}).get("id") == "classify":
            return step.get("run") or ""
    raise AssertionError(f"{workflow_name}:detect has no step with id `classify`")


def _jq_expressions(workflow_name: str) -> list[str]:
    expressions = _JQ_ARGUMENT.findall(_classify_step_run(workflow_name))
    assert expressions, (
        f"{workflow_name}:detect no longer passes `--jq` to `gh api`. If the "
        f"changed-file set is now built some other way, this guard has to be "
        f"rewritten against it rather than deleted. See bead "
        f"enhancedchannelmanager-9ogyd."
    )
    return expressions


def _array_shaped(expressions: list[str]) -> str:
    """The expression for `pulls/N/files`, which returns a bare array."""
    matches = [e for e in expressions if ".[][]" in e]
    assert len(matches) == 1, f"expected one array-shaped expression, got {matches}"
    return matches[0]


def _compare_shaped(expressions: list[str]) -> str:
    """The expression for the compare API, which nests the list under `files`."""
    matches = [e for e in expressions if ".files" in e]
    assert len(matches) == 1, f"expected one compare-shaped expression, got {matches}"
    return matches[0]


def _run_jq(expression: str, payload) -> list[str]:
    """Apply the workflow's real jq expression to a payload, as CI would.

    ``gh api --slurp`` wraps all response pages in one outer array. The jq
    expression must return one JSON array so filename delimiters remain data.
    """
    result = subprocess.run(
        [JQ, "-r", expression],
        input=json.dumps([payload]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"jq rejected the workflow's own expression {expression!r}: "
        f"{result.stderr.strip()}"
    )
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    return parsed


class TestRenameSourcesReachTheClassifier:
    """A rename shows CI only its destination unless the workflow asks for more.

    `git diff --name-only` with default rename detection prints the
    destination and drops the source, and the pull-request and compare APIs
    have the same shape: the source lives in `previous_filename`, which
    `.[].filename` discards. So `git mv
    backend/tests/unit/test_auth_middleware.py docs/legacy_auth_test_notes.md`
    reached the classifier as one `.md` path, classified documentation-only,
    and every required status check passed having executed nothing. Branch
    protection on `dev` requires no reviews, so those green checks were the
    entire gate. See bead enhancedchannelmanager-9ogyd.
    """

    @pytest.mark.parametrize("workflow", DETECT_WORKFLOWS)
    def test_every_detect_workflow_asks_for_rename_sources(self, workflow):
        """Dependency-free half of the guard: it cannot skip, ever."""
        for expression in _jq_expressions(workflow):
            assert "previous_filename" in expression, (
                f"{workflow}:detect builds its changed-file set with "
                f"{expression!r}, which sees only the DESTINATION of a "
                f"renamed file. The source path is dropped, so a rename into "
                f"a `.md` path classifies documentation-only and every gate "
                f"reading that verdict no-ops green over deleted code. "
                f"See bead enhancedchannelmanager-9ogyd."
            )

    @requires_jq
    def test_rename_into_markdown_classifies_as_code(self, script):
        """The regression guard, end to end through the workflow's own jq."""
        paths = _run_jq(
            _array_shaped(_jq_expressions("test.yml")), RENAME_INTO_MARKDOWN_FILES
        )
        code_paths_changed, code_paths = script.classify(paths)
        assert code_paths_changed is True, (
            f"a rename of a test file into a `.md` path classified "
            f"documentation-only from {paths}. Six of the seven required "
            f"checks gate every step on that verdict."
        )
        assert "backend/tests/unit/test_auth_middleware.py" in code_paths

    @requires_jq
    def test_recorded_rename_commit_yields_both_sides(self):
        """Real recorded payload, not a hand-guessed shape."""
        for expression, payload in (
            (
                _array_shaped(_jq_expressions("test.yml")),
                RECORDED_RENAME_COMMIT_FILES,
            ),
            (
                _compare_shaped(_jq_expressions("test.yml")),
                {"files": RECORDED_RENAME_COMMIT_FILES},
            ),
        ):
            paths = _run_jq(expression, payload)
            assert RECORDED_RENAME_SOURCE in paths, (
                f"{expression!r} dropped the rename source recorded on commit "
                f"b47ced96: {paths}"
            )
            assert (
                "frontend/src/components/settings/OutboundPolicyCard.css" in paths
            )

    @requires_jq
    @pytest.mark.parametrize("workflow", DETECT_WORKFLOWS)
    def test_no_blank_lines_for_ordinary_changes(self, workflow):
        """`// empty` must emit no array element when a file is unrenamed."""
        plain = [
            {"filename": "backend/main.py", "status": "modified"},
            {"filename": "CHANGELOG.md", "previous_filename": None},
        ]
        for expression in _jq_expressions(workflow):
            payload = {"files": plain} if ".files" in expression else plain
            paths = _run_jq(expression, payload)
            assert paths == ["backend/main.py", "CHANGELOG.md"], (
                f"{workflow} expression {expression!r} produced {paths!r}"
            )

    @requires_jq
    def test_compare_expression_tolerates_a_payload_with_no_files_key(self):
        """`gh api --paginate` can hand the compare expression a page with no
        `files` key. The `?` must keep absorbing that instead of failing the
        step, since a failed classifier leaves the set empty."""
        for workflow in ("test.yml", "build.yml", "docs-pages.yml"):
            expression = _compare_shaped(_jq_expressions(workflow))
            assert _run_jq(expression, {}) == []

    @requires_jq
    def test_json_transport_preserves_filename_characters_without_rewriting(self):
        paths = ["docs/line\nbreak.md", r"docs\shipping.md", " docs/a.md", "docs/a.md "]
        expression = _array_shaped(_jq_expressions("test.yml"))
        payload = [{"filename": path, "status": "modified"} for path in paths]
        assert _run_jq(expression, payload) == paths


def test_detect_workflows_use_lossless_json_transport():
    for workflow in DETECT_WORKFLOWS:
        run = _classify_step_run(workflow)
        assert run.count("gh api --paginate") == run.count("gh api --paginate --slurp")
        assert "changed_files.json" in run
        assert "changed_files.txt" not in run
        assert "--jq '[" in run


def test_version_consistency_summary_uses_positive_verdict_polarity():
    workflow = (TestWorkflowContract.WORKFLOW_DIR / "test.yml").read_text(encoding="utf-8")
    summary = workflow.split("- name: Summarize what this check did", 1)[1]
    summary = summary.split("# ─── Fake-test guard", 1)[0]
    assert 'elif [ "$CODE_PATHS_CHANGED" = "false" ]; then' in summary
    assert "NOT run (inert machine-state change)" in summary


# --- The published user-guide site has one path definition ------------------


def _load_yaml_ignoring_unknown_tags(path: Path) -> dict:
    """Parse YAML that carries tags SafeLoader refuses, such as mkdocs.yml.

    `mkdocs.yml` uses `!!python/name:` to wire the emoji extension. Resolving
    that tag would import the module; the tests only need the document
    structure, so unknown tags collapse to None."""
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("", lambda loader, suffix, node: None)
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


class TestDocsSiteWorkflowContract:
    """`docs-pages.yml` reads the classifier instead of carrying its own list.

    The list of paths the published site is built from used to live in that
    workflow's `paths:` filter, where nothing could see it drift. It now lives
    in the classifier. These assertions fail if a second copy comes back."""

    WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
    DOCS_PAGES = WORKFLOW_DIR / "docs-pages.yml"
    MKDOCS_CONFIG = REPO_ROOT / "mkdocs.yml"

    def test_docs_pages_has_no_path_filter(self):
        workflow = _load_yaml_ignoring_unknown_tags(self.DOCS_PAGES)
        triggers = workflow.get(True) or workflow.get("on") or {}
        for event, config in triggers.items():
            if not isinstance(config, dict):
                continue
            for key in ("paths", "paths-ignore"):
                assert key not in config, (
                    f"docs-pages.yml reintroduced `{key}` on the `{event}` "
                    f"trigger. That is a second copy of the site's path list, "
                    f"which is what bead enhancedchannelmanager-t4d5w "
                    f"collapsed into scripts/classify_changed_paths.py. Gate "
                    f"the `build` job on `docs_site_affected` instead."
                )

    def test_docs_pages_build_gates_on_the_classifier_output(self):
        workflow = _load_yaml_ignoring_unknown_tags(self.DOCS_PAGES)
        build = (workflow.get("jobs") or {}).get("build") or {}
        condition = str(build.get("if", ""))
        assert "docs_site_affected" in condition, (
            "docs-pages.yml:build no longer reads `docs_site_affected`. "
            "Without it the site rebuilds on every push to dev, or not at all."
        )
        assert "!= 'false'" in condition, (
            "docs-pages.yml:build must gate on `!= 'false'` so an absent or "
            "empty classifier output still rebuilds the site. Gating on "
            "`== 'true'` fails closed, and the failure mode is a published "
            "site silently stale behind merged content."
        )
        assert "!cancelled()" in condition, (
            "docs-pages.yml:build must carry `!cancelled()`. A job-level "
            "`if:` with no status function implies `success()` over its "
            "`needs`, so without it a FAILED `detect` skips the build, skips "
            "the deploy, and leaves the published site silently behind "
            "merged content until some later push heals it. `!= 'false'` "
            "only covers an absent or empty output from a job that "
            "SUCCEEDED. build.yml:codeql-analysis carries `!cancelled()` for "
            "the mirror-image reason."
        )

    def test_every_published_nav_target_is_a_recognised_site_path(self, script):
        """The invariant that keeps the site from going stale after a merge.

        If a page is added to the mkdocs nav outside the prefixes the
        classifier knows about, editing that page would classify as
        `docs_site_affected=false` and the deploy would never fire. Rather
        than trust the two lists to stay aligned by inspection, derive one
        from the other."""
        config = _load_yaml_ignoring_unknown_tags(self.MKDOCS_CONFIG)
        docs_dir = config.get("docs_dir", "docs").strip("/")

        targets: list[str] = []

        def collect(node) -> None:
            if isinstance(node, str):
                if node.endswith(".md"):
                    targets.append(node)
            elif isinstance(node, list):
                for item in node:
                    collect(item)
            elif isinstance(node, dict):
                for value in node.values():
                    collect(value)

        collect(config.get("nav") or [])
        assert targets, "mkdocs.yml has no nav entries; the parse went wrong."

        missed = [
            target
            for target in targets
            if not script.is_docs_site_path(f"{docs_dir}/{target}")
        ]
        assert not missed, (
            f"{len(missed)} page(s) in the mkdocs nav are outside the path "
            f"list in scripts/classify_changed_paths.py: {missed[:5]}. "
            f"Editing one of them would classify as docs_site_affected=false, "
            f"so docs-pages.yml would not rebuild and the published site would "
            f"go stale behind dev. Add the prefix to DOCS_SITE_PREFIXES or "
            f"the file to DOCS_SITE_FILES."
        )

    def test_declared_site_files_exist_in_the_repo(self, script):
        """A renamed file would silently drop out of the site's path list."""
        for relative in script.DOCS_SITE_FILES:
            assert (REPO_ROOT / relative).is_file(), (
                f"{relative} is in DOCS_SITE_FILES but is not in the tree. "
                f"A stale entry means a real site input is no longer tracked."
            )


class TestOperatorDocsRunsTheSiteBuild:
    """`mkdocs build --strict` is the only PR-time check of the published site.

    Bead pb2s4: a broken link passed `npm run docs:check` and failed the
    strict build, and the strict build only ran after the merge. It runs on
    the PR now, in the one documentation job that is ungated."""

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"

    @staticmethod
    def _operator_docs_job() -> dict:
        import yaml

        workflow = yaml.safe_load(
            TestOperatorDocsRunsTheSiteBuild.WORKFLOW.read_text(encoding="utf-8")
        )
        return (workflow.get("jobs") or {}).get("operator-docs") or {}

    @staticmethod
    def _step_running(job: dict, prefix: str) -> dict | None:
        """The step whose `run:` actually invokes a command starting `prefix`.

        Matching the raw `run:` text of the whole job would let a comment
        mentioning the command, or an `echo` naming it in the step summary,
        satisfy an assertion that the command runs. Both are present in this
        job, so the distinction is not hypothetical. Comment lines are
        dropped and the command must START the line."""
        for step in job.get("steps") or []:
            for line in str(step.get("run", "")).splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if stripped.startswith(prefix):
                    return step
        return None

    def _assert_step_can_fail_the_job(self, step: dict, description: str) -> None:
        """A step that cannot fail the job is a check that verifies nothing.

        Finding the command in the file is not enough. `continue-on-error:
        true` swallows its failure, and an `if:` that never evaluates true
        stops it running at all. Either produces a permanently green
        `Operator Docs` that never checks anything, which is the same defect
        this whole change exists to close, one layer down."""
        assert step.get("continue-on-error") is not True, (
            f"the {description} step carries `continue-on-error: true`. Its "
            f"failure would be swallowed and `Operator Docs` would report "
            f"success having verified nothing. That is exactly the "
            f"hollow-pass defect bead enhancedchannelmanager-t4d5w exists to "
            f"close, reintroduced at the step level."
        )
        assert "if" not in step, (
            f"the {description} step gained an `if:` condition. A condition "
            f"that evaluates false silently turns `Operator Docs` into a job "
            f"that runs nothing and passes. If the step genuinely needs a "
            f"gate, pin the exact condition in this test rather than "
            f"loosening the assertion."
        )

    def test_job_runs_mkdocs_strict_and_that_step_can_fail_the_job(self):
        step = self._step_running(self._operator_docs_job(), "mkdocs build")
        assert step is not None and "--strict" in str(step.get("run")), (
            "the Operator Docs job no longer builds the published site with "
            "--strict. Without it a broken user-guide link merges green and "
            "surfaces as a failed Pages deploy on dev. See beads "
            "enhancedchannelmanager-pb2s4 and enhancedchannelmanager-t4d5w."
        )
        self._assert_step_can_fail_the_job(step, "mkdocs build --strict")

    def test_job_runs_docs_check_and_that_step_can_fail_the_job(self):
        step = self._step_running(self._operator_docs_job(), "npm run docs:check")
        assert step is not None, (
            "the Operator Docs job no longer runs `npm run docs:check`, "
            "which validates links, terminology and screenshot dimensions."
        )
        self._assert_step_can_fail_the_job(step, "npm run docs:check")

    def test_em_dash_ratchet_carries_its_exact_condition(self):
        """This step legitimately has a gate, so pin the gate rather than
        assert its absence.

        The ratchet diffs against the pull request's merge base. On a push to
        dev there is no base ref to diff, so it runs on pull requests only.
        Any OTHER condition is a silent narrowing of the guard."""
        step = self._step_running(
            self._operator_docs_job(), "python3 scripts/check_em_dashes.py"
        )
        assert step is not None, "the em-dash ratchet step is gone."
        assert step.get("if") == "github.event_name == 'pull_request'", (
            f"the em-dash ratchet's condition changed to {step.get('if')!r}. "
            f"It must stay exactly `github.event_name == 'pull_request'`: any "
            f"narrower condition silently reduces what the ratchet covers."
        )
        assert step.get("continue-on-error") is not True, (
            "the em-dash ratchet carries `continue-on-error: true`, so new "
            "em-dashes would no longer fail the pull request."
        )

    def test_job_stays_ungated(self):
        """Gating this job would defeat its purpose: Markdown changes are
        exactly what it exists to check."""
        job = self._operator_docs_job()
        assert "if" not in job, (
            "the Operator Docs job gained a job-level `if:`. It is the only "
            "job that examines documentation on every event; gating it means "
            "Markdown PRs get no documentation check at all."
        )
        assert "needs" not in job, (
            "the Operator Docs job gained a `needs:` dependency. It runs "
            "unconditionally on purpose so it cannot be skipped by an "
            "upstream failure."
        )
        assert job.get("continue-on-error") is not True, (
            "the Operator Docs job carries `continue-on-error: true`, so "
            "nothing it checks can fail a pull request."
        )
