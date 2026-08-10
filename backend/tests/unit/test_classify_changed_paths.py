"""Tests for ``scripts/classify_changed_paths.py``.

Beads enhancedchannelmanager-5rwzy (``docs_only``) and
enhancedchannelmanager-t4d5w (``docs_site_affected``).

The classifier is the gate that decides whether a required CI status check
runs its real work or passes as a no-op. A wrong ``docs_only=true`` verdict
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
            "./docs/user_guide/index.md",
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
        """The opposite fail-open direction from ``docs_only``.

        A missed rebuild leaves the published site stale behind merged
        content; a needless rebuild costs a runner minute."""
        affected, site_paths = script.classify_docs_site([])
        assert affected is True
        assert site_paths == []

    def test_blank_only_set_fails_open_to_affected(self, script):
        affected, _ = script.classify_docs_site(["", "   ", "\t"])
        assert affected is True

    def test_the_two_verdicts_are_independent(self, script):
        """A change can be documentation-only AND site-affecting, or neither.

        The four combinations are all reachable, so neither output may be
        derived from the other."""
        combinations = {
            ("docs/user_guide/index.md",): (True, True),
            ("docs/shipping.md",): (True, False),
            ("mkdocs.yml",): (False, True),
            ("backend/main.py",): (False, False),
        }
        for paths, expected in combinations.items():
            docs_only, _ = script.classify(list(paths))
            affected, _ = script.classify_docs_site(list(paths))
            assert (docs_only, affected) == expected, paths


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
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        parsed[key] = value
    return parsed


class TestCommandLine:
    def test_stdin_docs_only(self):
        result = _run_cli([], "docs/api.md\nCHANGELOG.md\n")
        assert result.returncode == 0
        assert _outputs(result)["docs_only"] == "true"

    def test_stdin_mixed(self):
        result = _run_cli([], "docs/api.md\nbackend/main.py\n")
        assert result.returncode == 0
        assert _outputs(result)["docs_only"] == "false"
        assert "backend/main.py" in result.stderr

    def test_files_from(self, tmp_path):
        listing = tmp_path / "changed.txt"
        listing.write_text("docs/api.md\n.beads/x.jsonl\n", encoding="utf-8")
        result = _run_cli(["--files-from", str(listing)])
        assert result.returncode == 0
        assert _outputs(result)["docs_only"] == "true"

    def test_missing_files_from_exits_zero_and_fails_open(self, tmp_path):
        """A classifier hiccup must never skip a dependent job."""
        result = _run_cli(["--files-from", str(tmp_path / "nope.txt")])
        assert result.returncode == 0
        assert _outputs(result) == {"docs_only": "false", "docs_site_affected": "true"}
        assert "::warning::" in result.stderr

    def test_empty_input_exits_zero_and_fails_open(self):
        result = _run_cli([], "")
        assert result.returncode == 0
        assert _outputs(result) == {"docs_only": "false", "docs_site_affected": "true"}
        assert "::warning::" in result.stderr

    def test_output_is_exactly_the_two_github_output_keys(self):
        """The workflow appends stdout straight to $GITHUB_OUTPUT, so every
        stdout line must be a well-formed key=value pair and nothing else.

        Pinned as a set, not a sequence: consumers read by key, and pinning
        the order would make adding an output a breaking change for no
        reason."""
        result = _run_cli([], "backend/main.py\n")
        assert set(_outputs(result)) == {"docs_only", "docs_site_affected"}
        assert all("=" in line for line in result.stdout.splitlines())

    def test_site_verdict_on_a_published_page(self):
        result = _run_cli([], "docs/user_guide/stats/bandwidth.md\n")
        assert _outputs(result) == {"docs_only": "true", "docs_site_affected": "true"}

    def test_site_verdict_on_an_internal_doc(self):
        result = _run_cli([], "docs/shipping.md\n")
        assert _outputs(result) == {"docs_only": "true", "docs_site_affected": "false"}


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
                assert "docs_only" not in condition, (
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
        """Gating this job on the classifier would defeat its purpose: a
        documentation-only change is exactly what it exists to check."""
        job = self._operator_docs_job()
        assert "if" not in job, (
            "the Operator Docs job gained a job-level `if:`. It is the only "
            "job that examines documentation on every event; gating it means "
            "documentation-only PRs get no documentation check at all."
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
