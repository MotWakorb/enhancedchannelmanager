"""Tests for ``scripts/classify_changed_paths.py`` (bead enhancedchannelmanager-5rwzy).

The classifier is the gate that decides whether a required CI status check
runs its real work or passes as a no-op. A wrong ``docs_only=true`` verdict
turns ``Backend Tests`` green without pytest ever running, which is the exact
class of defect this bead exists to close. So the accept/reject boundary and
the fail-open behaviour are both pinned here.

Inputs are built inline rather than read from the repo so the tests stay
stable as the tree churns.
"""
from __future__ import annotations

import importlib.util
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


# --- Paths CI is allowed to treat as documentation --------------------------


class TestDocumentationPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "CHANGELOG.md",
            "docs/testing.md",
            "docs/user_guide/backup-restore/run-a-restore-drill.md",
            "./docs/shipping.md",
            ".beads/enhancedchannelmanager.jsonl",
            ".beads/backup/2026-08-08.db",
            ".beads/config.json",
        ],
    )
    def test_recognised_as_documentation(self, script, path):
        assert script.is_doc_path(path) is True


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
            "docs/.md",
        ],
    )
    def test_recognised_as_code(self, script, path):
        assert script.is_doc_path(path) is False

    def test_beads_prefix_is_not_eaten_by_lstrip(self, script):
        """Regression guard: ``lstrip('./')`` would strip the leading dot of
        ``.beads/`` and turn every beads path into ``beads/``. The path must
        still classify as documentation."""
        assert script.is_doc_path(".beads/issues.jsonl") is True


# --- Whole-set classification ----------------------------------------------


class TestClassify:
    def test_all_markdown_is_docs_only(self, script):
        docs_only, code_paths = script.classify(["CHANGELOG.md", "docs/api.md"])
        assert docs_only is True
        assert code_paths == []

    def test_markdown_and_beads_is_docs_only(self, script):
        docs_only, code_paths = script.classify(
            [".beads/enhancedchannelmanager.jsonl", "docs/api.md"]
        )
        assert docs_only is True
        assert code_paths == []

    def test_mixed_change_is_code(self, script):
        """The defect this bead closes: a PR touching code AND Markdown.

        Every shipped change carries a CHANGELOG entry, so this is the shape
        of nearly every PR in the repo. It must classify as code."""
        docs_only, code_paths = script.classify(["CHANGELOG.md", "backend/main.py"])
        assert docs_only is False
        assert code_paths == ["backend/main.py"]

    def test_code_only_is_code(self, script):
        docs_only, code_paths = script.classify(["backend/main.py"])
        assert docs_only is False
        assert code_paths == ["backend/main.py"]

    def test_empty_set_fails_open_to_code(self, script):
        """An undetermined file set must run the real work, never no-op green."""
        docs_only, code_paths = script.classify([])
        assert docs_only is False

    def test_blank_lines_are_ignored(self, script):
        docs_only, _ = script.classify(["", "  ", "docs/api.md", ""])
        assert docs_only is True

    def test_blank_only_set_fails_open_to_code(self, script):
        docs_only, _ = script.classify(["", "   ", "\t"])
        assert docs_only is False


# --- End-to-end CLI contract ------------------------------------------------


def _run_cli(args, stdin_text=""):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


class TestCommandLine:
    def test_stdin_docs_only(self):
        result = _run_cli([], "docs/api.md\nCHANGELOG.md\n")
        assert result.returncode == 0
        assert result.stdout.strip() == "docs_only=true"

    def test_stdin_mixed(self):
        result = _run_cli([], "docs/api.md\nbackend/main.py\n")
        assert result.returncode == 0
        assert result.stdout.strip() == "docs_only=false"
        assert "backend/main.py" in result.stderr

    def test_files_from(self, tmp_path):
        listing = tmp_path / "changed.txt"
        listing.write_text("docs/api.md\n.beads/x.jsonl\n", encoding="utf-8")
        result = _run_cli(["--files-from", str(listing)])
        assert result.returncode == 0
        assert result.stdout.strip() == "docs_only=true"

    def test_missing_files_from_exits_zero_and_fails_open(self, tmp_path):
        """A classifier hiccup must never skip a dependent job."""
        result = _run_cli(["--files-from", str(tmp_path / "nope.txt")])
        assert result.returncode == 0
        assert result.stdout.strip() == "docs_only=false"
        assert "::warning::" in result.stderr

    def test_empty_input_exits_zero_and_fails_open(self):
        result = _run_cli([], "")
        assert result.returncode == 0
        assert result.stdout.strip() == "docs_only=false"
        assert "::warning::" in result.stderr

    def test_output_is_a_single_github_output_line(self):
        """The workflow appends stdout straight to $GITHUB_OUTPUT, so stdout
        must carry exactly one key=value line and nothing else."""
        result = _run_cli([], "backend/main.py\n")
        assert result.stdout.splitlines() == ["docs_only=false"]


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
    def _load_workflow(path: Path) -> dict:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def _triggers(workflow: dict) -> dict:
        # YAML 1.1 parses a bare `on:` key as the boolean True, so a workflow
        # trigger block comes back under `True`, not `"on"`.
        return workflow.get(True) or workflow.get("on") or {}

    def test_no_docs_only_sentinel_workflow_remains(self):
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
        """The half of the invariant that keeps documentation-only PRs mergeable.

        GitHub counts a SKIPPED job as satisfying a required status check, so
        a required-context job must never carry a job-level `if:` that can
        evaluate false: it would be the same permanently-satisfied context
        the sentinel workflow used to provide. Every such job either has no
        `if:` at all or guards only against cancellation, and gates its real
        work at the STEP level instead."""
        for path in sorted(self.WORKFLOW_DIR.glob("*.yml")):
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
                assert "docs_only" not in condition, (
                    f"{path.name}:{job_id} gates the whole job on the "
                    f"documentation-only classification. That skips the job, "
                    f"and a skipped job satisfies the required check it is "
                    f"named for. Gate its steps instead. "
                    f"See bead enhancedchannelmanager-5rwzy."
                )

    def test_each_required_context_has_exactly_one_emitting_job(self):
        emitters: dict[str, list[str]] = {name: [] for name in self.REQUIRED_CONTEXTS}
        for path in sorted(self.WORKFLOW_DIR.glob("*.yml")):
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
