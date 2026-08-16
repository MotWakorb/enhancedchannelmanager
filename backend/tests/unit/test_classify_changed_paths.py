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
import shlex
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
        result = _run_cli(["--input-format", "nul"], raw)
        assert _outputs(result)["code_paths_changed"] == "true"

    def test_complete_envelope_classifies_known_paths(self):
        payload = {"complete": True, "paths": [".beads/state.jsonl"]}
        result = _run_cli(["--input-format", "envelope"], json.dumps(payload))
        assert _outputs(result) == {
            "code_paths_changed": "false",
            "docs_site_affected": "false",
        }

    @pytest.mark.parametrize(
        "payload",
        (
            {"complete": False, "paths": [".beads/state.jsonl"]},
            {"paths": [".beads/state.jsonl"]},
            {"complete": "true", "paths": [".beads/state.jsonl"]},
            {"complete": True},
        ),
    )
    def test_incomplete_or_malformed_envelope_fails_safe(self, payload):
        result = _run_cli(["--input-format", "envelope"], json.dumps(payload))
        assert _outputs(result) == {
            "code_paths_changed": "true",
            "docs_site_affected": "true",
        }
        assert "undetermined" in result.stderr.lower()
        assert ".beads/state.jsonl" not in result.stderr

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
            [sys.executable, str(SCRIPT_PATH), "--input-format", "nul"],
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
    #
    # `Operator Docs` hosts two documentation gates and four security
    # ratchets. It is safe to require because it has no job-level `if:` and no
    # path filter, so it runs and reports on every event; the assertions in
    # this class and in TestOperatorDocsRequiredContext are what keep that
    # true.
    REQUIRED_CONTEXTS = (
        "Backend Tests",
        "Frontend Tests",
        "MCP Server Tests",
        "Semgrep Lint",
        "Version Consistency",
        "CodeQL Analysis",
        "Operator Docs",
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
        """Exactly one job carries each required name, and it is this one.

        A job whose display name is one character off a required name is
        indistinguishable in the checks list. This mapping is the both-ways
        assertion: a required name that no job emits fails here, and so does a
        second job that starts emitting one."""
        required_job_names = {
            "Backend Tests": "test.yml:backend",
            "Frontend Tests": "test.yml:frontend",
            "MCP Server Tests": "test.yml:mcp-server",
            "Semgrep Lint": "test.yml:semgrep-lint",
            "Version Consistency": "test.yml:version-consistency",
            "CodeQL Analysis": "build.yml:codeql-analysis",
            "Operator Docs": "test.yml:operator-docs",
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


ACTION_USE = "./.github/actions/classify-changed-paths"
ACTION_FILE = REPO_ROOT / ".github/actions/classify-changed-paths/action.yml"
RESERVED_OUTPUTS = {"code_paths_changed", "docs_site_affected"}
RESERVED_IDENTIFIERS = ("classify_changed_paths", "classify-changed-paths")
KEY_MARKER = "<key>"


def _expected_verdict_manifest():
    expected = set()
    for filename in ("build.yml", "docs-pages.yml", "test.yml"):
        for output in RESERVED_OUTPUTS:
            expected.add((filename, ("jobs", "detect", "outputs", KEY_MARKER), output))
            expected.add(
                (
                    filename,
                    ("jobs", "detect", "outputs", output),
                    f"${{{{ steps.classify.outputs.{output} }}}}",
                )
            )

    code_false = "needs.detect.outputs.code_paths_changed == 'false'"
    code_true = "needs.detect.outputs.code_paths_changed != 'false'"
    code_always = "always() && " + code_true
    main_only = code_true + " && ((github.event_name == 'push' && github.ref == 'refs/heads/main') || (github.event_name == 'pull_request' && github.base_ref == 'main'))"
    for job in ("security-scan-frontend", "security-scan-backend", "iac-security-scan"):
        expected.add(("build.yml", ("jobs", job, "if"), main_only))
    expected.add(("build.yml", ("jobs", "wait-for-tests", "if"), code_true))
    for index, value in enumerate(
        (code_false, code_true, code_true, code_true, code_true,
         code_true + " && github.event_name == 'pull_request'", code_always)
    ):
        expected.add(("build.yml", ("jobs", "codeql-analysis", "steps", index, "if"), value))
    expected.add(
        (
            "docs-pages.yml",
            ("jobs", "build", "if"),
            "!cancelled() && needs.detect.outputs.docs_site_affected != 'false'",
        )
    )
    step_contracts = {
        "backend": (1, 6, 9),
        "mcp-server": (1, 4, 7),
        "frontend": (1, 9, 12),
        "semgrep-lint": (1, 4, 5),
    }
    for job, (first_true, last_true, last_always) in step_contracts.items():
        expected.add(("test.yml", ("jobs", job, "steps", 0, "if"), code_false))
        for index in range(first_true, last_true + 1):
            expected.add(("test.yml", ("jobs", job, "steps", index, "if"), code_true))
        for index in range(last_true + 1, last_always + 1):
            expected.add(("test.yml", ("jobs", job, "steps", index, "if"), code_always))
    expected.add(
        (
            "test.yml",
            ("jobs", "version-consistency", "steps", 3, "if"),
            code_true + " && github.event_name == 'pull_request' && github.base_ref == 'dev'",
        )
    )
    expected.add(
        (
            "test.yml",
            ("jobs", "version-consistency", "steps", 4, "env", "CODE_PATHS_CHANGED"),
            "${{ needs.detect.outputs.code_paths_changed }}",
        )
    )
    for job in (
        "fake-test-guard", "visual-regression", "operator-workspace-release",
        "sr-only-hidden", "edit-mode-immediacy-surfaces",
        "edit-mode-numbering-guards", "edit-mode-session-restore",
    ):
        expected.add(("test.yml", ("jobs", job, "if"), code_true))
    return expected


EXPECTED_VERDICT_MANIFEST = _expected_verdict_manifest()
REGISTERED_CLASSIFIER_WORKFLOWS = {"build.yml", "docs-pages.yml", "test.yml"}
CONTROL_FIELDS = ("if", "continue-on-error")
CONTROL_MANIFEST_PATH = REPO_ROOT / "backend/tests/fixtures/workflow_control_manifest.json"
EXPECTED_JOB_OUTPUTS = {
    ("test.yml", "detect"): {
        "code_paths_changed": "${{ steps.classify.outputs.code_paths_changed }}",
        "docs_site_affected": "${{ steps.classify.outputs.docs_site_affected }}",
    },
    ("docs-pages.yml", "detect"): {
        "code_paths_changed": "${{ steps.classify.outputs.code_paths_changed }}",
        "docs_site_affected": "${{ steps.classify.outputs.docs_site_affected }}",
    },
    ("build.yml", "detect"): {
        "code_paths_changed": "${{ steps.classify.outputs.code_paths_changed }}",
        "docs_site_affected": "${{ steps.classify.outputs.docs_site_affected }}",
    },
    **{
        ("build.yml", job): {"digest": "${{ steps.digest.outputs.digest }}"}
        for job in ("build-amd64", "build-arm64", "build-mcp-amd64", "build-mcp-arm64")
    },
}
def _reserved_occurrences(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and any(name in key for name in RESERVED_OUTPUTS):
                yield path + (KEY_MARKER,), key
            yield from _reserved_occurrences(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _reserved_occurrences(child, path + (index,))
    elif isinstance(value, str) and any(name in value for name in RESERVED_OUTPUTS):
        yield path, value


def _scalar_strings(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _scalar_strings(key)
            yield from _scalar_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _scalar_strings(child)
    elif isinstance(value, str):
        yield value


def _workflow_controls(directory, field):
    controls = []
    for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml"))):
        workflow = TestWorkflowContract._load_workflow(path)
        for job_id, job in (workflow.get("jobs") or {}).items():
            if field in (job or {}):
                controls.append(
                    {
                        "file": path.name,
                        "path": ["jobs", job_id, field],
                        "value": job[field],
                    }
                )
            for index, step in enumerate((job or {}).get("steps") or []):
                if field in (step or {}):
                    controls.append(
                        {
                            "file": path.name,
                            "path": ["jobs", job_id, "steps", index, field],
                            "value": step[field],
                        }
                    )
    return controls


def _discover_action_consumers(directory=None) -> tuple[tuple[Path, str, int], ...]:
    """Locate the exact local action structurally across every workflow job."""
    directory = directory or TestWorkflowContract.WORKFLOW_DIR
    found = []
    for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml"))):
        jobs = TestWorkflowContract._load_workflow(path).get("jobs") or {}
        for job_id, job in jobs.items():
            for index, step in enumerate((job or {}).get("steps") or []):
                if (step or {}).get("uses") == ACTION_USE:
                    found.append((path, job_id, index))
    assert found, "no workflow job uses the shared changed-path action"
    return tuple(found)


ACTION_CONSUMERS = _discover_action_consumers()
DETECT_WORKFLOWS = tuple(dict.fromkeys(path for path, _job, _index in ACTION_CONSUMERS))


def _workflow_action_contract(directory=None) -> None:
    directory = directory or TestWorkflowContract.WORKFLOW_DIR
    discovered = _discover_action_consumers(directory)
    by_workflow: dict[Path, list[tuple[str, int]]] = {}
    for path, job_id, index in discovered:
        by_workflow.setdefault(path, []).append((job_id, index))
    for path, locations in by_workflow.items():
        assert len(locations) == 1, f"{path.name}: shared action must appear exactly once"
    if directory == TestWorkflowContract.WORKFLOW_DIR:
        _assert_registered_consumer_names(set(path.name for path in by_workflow))
    authority_jobs = {
        (path, locations[0][0])
        for path, locations in by_workflow.items()
    }
    actual_verdicts = set()
    actual_job_outputs = {}
    for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml"))):
        workflow = TestWorkflowContract._load_workflow(path)
        actual_verdicts.update(
            (path.name, location, value)
            for location, value in _reserved_occurrences(workflow)
        )
        event_config = workflow.get("on", workflow.get(True, {})) or {}
        workflow_call = event_config.get("workflow_call") or {}
        call_outputs = set((workflow_call.get("outputs") or {}).keys())
        assert not (call_outputs & RESERVED_OUTPUTS), (
            f"{path.name}: reusable workflow may not export reserved classifier outputs"
        )
        reusable_jobs = {
            job_id
            for job_id, job in (workflow.get("jobs") or {}).items()
            if isinstance((job or {}).get("uses"), str)
        }
        for job_id in reusable_jobs:
            reference = workflow["jobs"][job_id]["uses"]
            assert not any(
                identifier in reference for identifier in RESERVED_IDENTIFIERS
            ), f"{path.name}:{job_id}: reserved classifier reusable reference"
            scalars = tuple(_scalar_strings(workflow))
            for output in RESERVED_OUTPUTS:
                assert not any(
                    f"needs.{job_id}.outputs.{output}" in scalar
                    for scalar in scalars
                ), (
                    f"{path.name}:{job_id}: reusable job reserved-output consumption"
                )
        for job_id, job in (workflow.get("jobs") or {}).items():
            if "outputs" in (job or {}):
                actual_job_outputs[(path.name, job_id)] = job["outputs"]
            output_keys = set(((job or {}).get("outputs") or {}).keys())
            if output_keys & RESERVED_OUTPUTS:
                assert (path, job_id) in authority_jobs, (
                    f"{path.name}:{job_id}: reserved classifier outputs require "
                    "the canonical authority action"
                )
            for step in (job or {}).get("steps") or []:
                run = (step or {}).get("run")
                assert not (
                    isinstance(run, str)
                    and any(identifier in run for identifier in RESERVED_IDENTIFIERS)
                ), f"{path.name}:{job_id}: reserved classifier identifier in run scalar"
                uses = (step or {}).get("uses")
                if isinstance(uses, str) and any(
                    identifier in uses for identifier in RESERVED_IDENTIFIERS
                ):
                    assert uses == ACTION_USE, (
                        f"{path.name}:{job_id}: noncanonical classifier action reference"
                    )
    if directory == TestWorkflowContract.WORKFLOW_DIR:
        expected_verdicts = EXPECTED_VERDICT_MANIFEST
        expected_job_outputs = EXPECTED_JOB_OUTPUTS
    else:
        expected_verdicts = set()
        expected_job_outputs = {}
        for path, job_id in authority_jobs:
            expected_job_outputs[(path.name, job_id)] = {
                "code_paths_changed": "${{ steps.classify.outputs.code_paths_changed }}",
                "docs_site_affected": "${{ steps.classify.outputs.docs_site_affected }}",
            }
            for output in RESERVED_OUTPUTS:
                expected_verdicts.add(
                    (path.name, ("jobs", job_id, "outputs", KEY_MARKER), output)
                )
                expected_verdicts.add(
                    (
                        path.name,
                        ("jobs", job_id, "outputs", output),
                        f"${{{{ steps.classify.outputs.{output} }}}}",
                    )
                )
    assert actual_verdicts == expected_verdicts, (
        "reserved verdict namespace differs from the explicit workflow manifest: "
        f"extra={actual_verdicts - expected_verdicts}, "
        f"missing={expected_verdicts - actual_verdicts}"
    )
    assert actual_job_outputs == expected_job_outputs, (
        "job output mappings differ from the closed classifier-workflow manifest"
    )
    controls = {
        field: _workflow_controls(directory, field)
        for field in CONTROL_FIELDS
    }
    if directory == TestWorkflowContract.WORKFLOW_DIR:
        expected_controls = json.loads(
            CONTROL_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        assert controls == expected_controls, (
            "full-repository workflow control manifest changed; review the "
            "readable location and exact typed value before updating the golden file"
        )
    else:
        allowed_if = "needs.wrapper.outputs.artifact_name != ''"
        unexpected_ifs = [
            value for value in controls["if"] if value["value"] != allowed_if
        ]
        assert not unexpected_ifs, "full-repository if control manifest changed"
        assert not controls["continue-on-error"], (
            "full-repository continue-on-error control manifest changed"
        )
    for path, locations in by_workflow.items():
        workflow = TestWorkflowContract._load_workflow(path)
        job_id, action_index = locations[0]
        job = (workflow.get("jobs") or {})[job_id]
        steps = job.get("steps") or []
        assert len(steps) == 2, f"{path.name}:{job_id}: authority job permits exactly two steps"
        assert action_index == 1
        assert steps[0].get("uses", "").startswith("actions/checkout@")
        assert "run" not in steps[0]
        assert steps[1] == {
            "name": "Classify the changed file set",
            "id": "classify",
            "uses": ACTION_USE,
        }
        assert job.get("outputs") == {
            "code_paths_changed": "${{ steps.classify.outputs.code_paths_changed }}",
            "docs_site_affected": "${{ steps.classify.outputs.docs_site_affected }}",
        }


def _assert_registered_consumer_names(names):
    assert names == REGISTERED_CLASSIFIER_WORKFLOWS, (
        "classifier workflow consumer set changed; update the closed control "
        "manifest only after reviewing every output and gate"
    )


def test_every_workflow_has_one_structured_changed_path_authority():
    _workflow_action_contract()


def _validate_action_contract(action):
    expected_outputs = {
        "code_paths_changed": {
            "description": "Whether code gates must run.",
            "value": "${{ steps.classify.outputs.code_paths_changed }}",
        },
        "docs_site_affected": {
            "description": "Whether the published documentation site must rebuild.",
            "value": "${{ steps.classify.outputs.docs_site_affected }}",
        },
    }
    assert action.get("outputs") == expected_outputs
    expected_occurrences = set()
    for output in RESERVED_OUTPUTS:
        expected_occurrences.add((("outputs", KEY_MARKER), output))
        expected_occurrences.add(
            (
                ("outputs", output, "value"),
                f"${{{{ steps.classify.outputs.{output} }}}}",
            )
        )
    assert set(_reserved_occurrences(action)) == expected_occurrences
    runs = (action.get("runs") or {})
    assert runs.get("using") == "composite"
    steps = runs.get("steps") or []
    assert len(steps) == 1
    classifier_step = steps[0]
    assert set(classifier_step) == {"id", "shell", "env", "run"}
    assert classifier_step.get("id") == "classify"
    assert classifier_step.get("shell") == "bash"
    assert classifier_step.get("env") == {
        "GH_TOKEN": "${{ github.token }}",
        "REPO": "${{ github.repository }}",
        "EVENT_NAME": "${{ github.event_name }}",
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
        "BEFORE_SHA": "${{ github.event.before }}",
        "AFTER_SHA": "${{ github.sha }}",
    }
    run = classifier_step["run"]
    assert "scripts/classify_changed_paths.py" in run
    assert run.count('echo "$verdict" >> "$GITHUB_OUTPUT"') == 1
    all_runs = [
        step.get("run") or ""
        for step in steps
        if isinstance((step or {}).get("run"), str)
    ]
    assert sum(body.count("$GITHUB_OUTPUT") for body in all_runs) == 1


def test_shared_action_has_one_stable_output_contract_and_classifier():
    _validate_action_contract(TestWorkflowContract._load_workflow(ACTION_FILE))


def test_action_swapped_output_or_later_fabricated_write_is_rejected():
    import copy

    action = TestWorkflowContract._load_workflow(ACTION_FILE)
    swapped = copy.deepcopy(action)
    swapped["outputs"]["code_paths_changed"]["value"] = (
        "${{ steps.classify.outputs.docs_site_affected }}"
    )
    with pytest.raises(AssertionError):
        _validate_action_contract(swapped)

    fabricated = copy.deepcopy(action)
    fabricated["runs"]["steps"][0]["run"] += (
        '\necho "code_paths_changed=false" >> "$GITHUB_OUTPUT"\n'
    )
    with pytest.raises(AssertionError):
        _validate_action_contract(fabricated)

    split_key = copy.deepcopy(action)
    split_key["runs"]["steps"].append(
        {
            "shell": "bash",
            "run": 'printf "code_paths_"; echo "changed=true" >> "$GITHUB_OUTPUT"',
        }
    )
    with pytest.raises(AssertionError):
        _validate_action_contract(split_key)


def test_no_second_action_can_become_changed_path_authority():
    action_files = sorted((REPO_ROOT / ".github/actions").glob("**/action.y*ml"))
    authorities = []
    for path in action_files:
        action = TestWorkflowContract._load_workflow(path)
        if any(
            "scripts/classify_changed_paths.py" in ((step or {}).get("run") or "")
            for step in ((action.get("runs") or {}).get("steps") or [])
        ):
            authorities.append(path)
    assert authorities == [ACTION_FILE]

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
assert JQ is not None, "jq is a required, non-skippable workflow-contract dependency"


def _classify_step_run(workflow: Path) -> str:
    """The single shared action's acquisition/classification shell body."""
    del workflow
    action = TestWorkflowContract._load_workflow(ACTION_FILE)
    steps = ((action.get("runs") or {}).get("steps") or [])
    runs = [step.get("run") or "" for step in steps if (step or {}).get("id") == "classify"]
    assert len(runs) == 1
    return runs[0]


def _jq_calls(workflow: Path) -> list[tuple[str, str]]:
    """Return (endpoint, jq) for API calls producing changed_files.json."""
    calls = []
    for line in _changed_file_api_lines(workflow):
        tokens = shlex.split(line)
        api = tokens.index("api")
        endpoint = next((token for token in tokens[api + 1 :] if token.startswith("repos/")), None)
        expression = tokens[tokens.index("--jq") + 1] if "--jq" in tokens else None
        assert endpoint and expression, f"{workflow.name}: unrecognised changed-file API producer"
        calls.append((endpoint, expression))
    assert calls, (
        f"{workflow.name}:detect no longer passes `--jq` to `gh api`. If the "
        f"changed-file set is now built some other way, this guard has to be "
        f"rewritten against it rather than deleted. See bead "
        f"enhancedchannelmanager-9ogyd."
    )
    return calls


def _changed_file_api_lines(workflow: Path) -> list[str]:
    run = _classify_step_run(workflow).replace("\\\n", " ")
    return [
        line
        for line in run.splitlines()
        if "gh api" in line and "changed_files.json" in line
    ]


def _payload_for(endpoint: str, files):
    if "/pulls/" in endpoint and endpoint.endswith("/files"):
        return files
    if "/compare/" in endpoint:
        return {"files": files}
    raise AssertionError(f"unrecognised changed-file endpoint: {endpoint}")


def _validate_rename_sources(workflows: tuple[Path, ...]) -> None:
    for workflow in workflows:
        for _endpoint, expression in _jq_calls(workflow):
            assert "previous_filename" in expression, (
                f"{workflow.name}: changed-file producer drops rename sources"
            )


def _run_jq(expression: str, payload):
    """Apply the workflow's real jq expression to a payload, as CI would.

    ``gh api --slurp`` wraps all response pages in one outer array. The jq
    expression returns an explicit completeness envelope so filename
    delimiters remain data and capped responses cannot look complete.
    """
    return _run_jq_slurped(expression, [payload])


def _run_jq_slurped(expression: str, pages):
    result = subprocess.run(
        [JQ, "-r", expression],
        input=json.dumps(pages),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"jq rejected the workflow's own expression {expression!r}: "
        f"{result.stderr.strip()}"
    )
    return json.loads(result.stdout)


def _envelope_paths(envelope) -> list[str]:
    assert set(envelope) == {"complete", "paths"}
    assert isinstance(envelope["complete"], bool)
    assert isinstance(envelope["paths"], list)
    return envelope["paths"]


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
        _validate_rename_sources((workflow,))

    def test_rename_into_markdown_classifies_as_code(self, script):
        """The regression guard, end to end through the workflow's own jq."""
        for workflow in DETECT_WORKFLOWS:
            for endpoint, expression in _jq_calls(workflow):
                paths = _envelope_paths(
                    _run_jq(
                        expression,
                        _payload_for(endpoint, RENAME_INTO_MARKDOWN_FILES),
                    )
                )
                code_paths_changed, code_paths = script.classify(paths)
                assert code_paths_changed is True
                assert "backend/tests/unit/test_auth_middleware.py" in code_paths

    def test_recorded_rename_commit_yields_both_sides(self):
        """Real recorded payload, not a hand-guessed shape."""
        for workflow in DETECT_WORKFLOWS:
            for endpoint, expression in _jq_calls(workflow):
                paths = _envelope_paths(
                    _run_jq(
                        expression,
                        _payload_for(endpoint, RECORDED_RENAME_COMMIT_FILES),
                    )
                )
                assert RECORDED_RENAME_SOURCE in paths
                assert "frontend/src/components/settings/OutboundPolicyCard.css" in paths

    @pytest.mark.parametrize("workflow", DETECT_WORKFLOWS)
    def test_no_blank_lines_for_ordinary_changes(self, workflow):
        """`// empty` must emit no array element when a file is unrenamed."""
        plain = [
            {"filename": "backend/main.py", "status": "modified"},
            {"filename": "CHANGELOG.md", "previous_filename": None},
        ]
        for endpoint, expression in _jq_calls(workflow):
            payload = _payload_for(endpoint, plain)
            paths = _envelope_paths(_run_jq(expression, payload))
            assert paths == ["backend/main.py", "CHANGELOG.md"], (
                f"{workflow.name} expression {expression!r} produced {paths!r}"
            )

    def test_compare_expression_tolerates_a_payload_with_no_files_key(self):
        """`gh api --paginate` can hand the compare expression a page with no
        `files` key. The `?` must keep absorbing that instead of failing the
        step, since a failed classifier leaves the set empty."""
        for workflow in DETECT_WORKFLOWS:
            for endpoint, expression in _jq_calls(workflow):
                if "/compare/" in endpoint:
                    assert _run_jq(expression, {}) == {"complete": False, "paths": []}

    def test_json_transport_preserves_filename_characters_without_rewriting(self):
        paths = ["docs/line\nbreak.md", r"docs\shipping.md", " docs/a.md", "docs/a.md "]
        payload_files = [{"filename": path, "status": "modified"} for path in paths]
        for workflow in DETECT_WORKFLOWS:
            for endpoint, expression in _jq_calls(workflow):
                assert _envelope_paths(
                    _run_jq(expression, _payload_for(endpoint, payload_files))
                ) == paths

    @pytest.mark.parametrize(
        ("endpoint_kind", "count", "complete"),
        (
            ("compare", 299, True),
            ("compare", 300, False),
            ("pull", 2999, True),
            ("pull", 3000, False),
        ),
    )
    def test_endpoint_caps_produce_explicit_completeness(
        self, endpoint_kind, count, complete
    ):
        endpoint, expression = next(
            (endpoint, expression)
            for endpoint, expression in _jq_calls(DETECT_WORKFLOWS[0])
            if ("/compare/" in endpoint) == (endpoint_kind == "compare")
        )
        files = [
            {"filename": f".beads/state-{index}.jsonl", "status": "modified"}
            for index in range(count)
        ]
        envelope = _run_jq(expression, _payload_for(endpoint, files))
        if endpoint_kind == "pull":
            pages = [files[index : index + 100] for index in range(0, count, 100)]
            envelope = _run_jq_slurped(expression, pages)
        assert envelope["complete"] is complete
        assert len(envelope["paths"]) == count

    def test_malformed_or_missing_api_response_is_explicitly_incomplete(self):
        for endpoint, expression in _jq_calls(DETECT_WORKFLOWS[0]):
            assert _run_jq(expression, {}) == {"complete": False, "paths": []}

    @pytest.mark.parametrize(
        "pages",
        (
            [{"files": [{"filename": ".beads/a.jsonl"}]}, {"files": "bad"}],
            [{"files": [{"filename": ".beads/a.jsonl"}]}, {"files": None}],
            [{"files": [{"filename": ".beads/a.jsonl"}]}, {"message": "error"}],
            [{"commits": []}, {"files": [{"filename": ".beads/a.jsonl"}]}],
            [{"files": [{"filename": ".beads/a.jsonl"}]}, "bad-page"],
            [None, {"files": [{"filename": ".beads/a.jsonl"}]}],
            [{"files": [{"filename": ".beads/a.jsonl"}]}, {"files": []}],
        ),
    )
    def test_compare_rejects_malformed_or_misordered_slurped_pages(self, pages):
        _endpoint, expression = next(
            (endpoint, expression)
            for endpoint, expression in _jq_calls(DETECT_WORKFLOWS[0])
            if "/compare/" in endpoint
        )
        envelope = _run_jq_slurped(expression, pages)
        assert envelope["complete"] is False
        result = _run_cli(["--input-format", "envelope"], json.dumps(envelope))
        assert _outputs(result) == {
            "code_paths_changed": "true",
            "docs_site_affected": "true",
        }
        assert ".beads/a.jsonl" not in result.stderr

    def test_compare_accepts_documented_later_commit_page_without_files(self):
        _endpoint, expression = next(
            (endpoint, expression)
            for endpoint, expression in _jq_calls(DETECT_WORKFLOWS[0])
            if "/compare/" in endpoint
        )
        pages = [
            {"files": [{"filename": ".beads/a.jsonl"}]},
            {"commits": []},
        ]
        assert _run_jq_slurped(expression, pages) == {
            "complete": True,
            "paths": [".beads/a.jsonl"],
        }

    @pytest.mark.parametrize("position", ("first", "later"))
    @pytest.mark.parametrize(
        ("marker", "value"),
        (
            ("message", "synthetic API error"),
            ("documentation_url", "https://example.invalid/docs"),
            ("errors", [{"code": "synthetic"}]),
        ),
    )
    def test_compare_rejects_success_shape_mixed_with_error_markers(
        self, position, marker, value
    ):
        _endpoint, expression = next(
            (endpoint, expression)
            for endpoint, expression in _jq_calls(DETECT_WORKFLOWS[0])
            if "/compare/" in endpoint
        )
        first = {"files": [{"filename": ".beads/a.jsonl"}]}
        later = {"commits": []}
        (first if position == "first" else later)[marker] = value
        envelope = _run_jq_slurped(expression, [first, later])
        assert envelope["complete"] is False
        result = _run_cli(["--input-format", "envelope"], json.dumps(envelope))
        assert _outputs(result) == {
            "code_paths_changed": "true",
            "docs_site_affected": "true",
        }
        assert ".beads/a.jsonl" not in result.stderr

    def test_compare_legitimate_status_field_is_not_an_error_marker(self):
        _endpoint, expression = next(
            (endpoint, expression)
            for endpoint, expression in _jq_calls(DETECT_WORKFLOWS[0])
            if "/compare/" in endpoint
        )
        pages = [
            {"status": "ahead", "files": [{"filename": ".beads/a.jsonl"}]},
            {"commits": []},
        ]
        assert _run_jq_slurped(expression, pages)["complete"] is True


def test_detect_workflows_use_lossless_json_transport():
    for workflow in DETECT_WORKFLOWS:
        run = _classify_step_run(workflow)
        assert "changed_files.json" in run
        assert "changed_files.txt" not in run
        for line in _changed_file_api_lines(workflow):
            tokens = shlex.split(line)
            assert "--paginate" in tokens
            assert "--slurp" in tokens
            assert tokens[tokens.index("--jq") + 1].startswith("(")


def test_classifier_workflow_discovery_includes_yaml(tmp_path):
    workflow = tmp_path / "fourth.yaml"
    _write_action_workflow(workflow, job_id="renamed_classifier_job")
    assert _discover_action_consumers(tmp_path) == (
        (workflow, "renamed_classifier_job", 1),
    )
    _workflow_action_contract(tmp_path)


def test_new_fourth_action_consumer_requires_manifest_registration():
    with pytest.raises(AssertionError, match="consumer set changed"):
        _assert_registered_consumer_names(
            REGISTERED_CLASSIFIER_WORKFLOWS | {"fourth.yaml"}
        )


def _write_action_workflow(
    path: Path,
    *,
    job_id: str = "detect",
    extra_authority_steps: str = "",
    extra_jobs: str = "",
    step_id: str = "classify",
    outputs: str | None = None,
) -> None:
    outputs = outputs or (
        "      code_paths_changed: ${{ steps.classify.outputs.code_paths_changed }}\n"
        "      docs_site_affected: ${{ steps.classify.outputs.docs_site_affected }}\n"
    )
    path.write_text(
        f"jobs:\n  {job_id}:\n    outputs:\n{outputs}    steps:\n"
        "      - uses: actions/checkout@v6\n"
        f"      - name: Classify the changed file set\n        id: {step_id}\n"
        f"        uses: {ACTION_USE}\n{extra_authority_steps}{extra_jobs}",
        encoding="utf-8",
    )


def test_second_action_invocation_is_rejected(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_authority_steps=f"      - uses: {ACTION_USE}\n",
    )
    with pytest.raises(AssertionError, match="exactly once"):
        _workflow_action_contract(tmp_path)


@pytest.mark.parametrize(
    "command",
    (
        "python -u scripts/classify_changed_paths.py --files-from other.json",
        "env MODE=test python scripts/classify_changed_paths.py",
        "command python3 scripts/classify_changed_paths.py",
        "gh api repos/x/compare/a...b \\\n+  --jq '[.files[] | .filename]'",
    ),
)
def test_any_run_step_in_authority_job_is_rejected_structurally(tmp_path, command):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_authority_steps="      - run: |\n" + "\n".join(
            f"          {line}" for line in command.splitlines()
        ) + "\n",
    )
    with pytest.raises(
        AssertionError,
        match="reserved classifier identifier|exactly two steps",
    ):
        _workflow_action_contract(tmp_path)


def test_swapped_output_or_stale_step_binding_is_rejected(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        outputs=(
            "      code_paths_changed: ${{ steps.classify.outputs.docs_site_affected }}\n"
            "      docs_site_affected: ${{ steps.old.outputs.docs_site_affected }}\n"
        ),
    )
    with pytest.raises(AssertionError):
        _workflow_action_contract(tmp_path)


def test_renamed_action_step_with_stale_output_binding_is_rejected(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(workflow, step_id="renamed")
    with pytest.raises(AssertionError):
        _workflow_action_contract(tmp_path)


def test_yaml_comment_prose_is_ignored(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_jobs=(
            "  diagnostics:\n    steps:\n"
            "      # python scripts/classify_changed_paths.py\n"
            "      - run: echo 'ordinary diagnostics are harmless here'\n"
        ),
    )
    _workflow_action_contract(tmp_path)


@pytest.mark.parametrize(
    "run",
    (
        "echo scripts/classify_changed_paths.py is centralized",
        "# scripts/classify_changed_paths.py is centralized",
    ),
)
def test_reserved_identifier_in_any_run_scalar_is_fail_safe_red(tmp_path, run):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_jobs=f"  diagnostics:\n    steps:\n      - run: '{run}'\n",
    )
    with pytest.raises(AssertionError, match="reserved classifier identifier"):
        _workflow_action_contract(tmp_path)


def test_direct_only_fourth_yaml_is_rejected_closed_world(tmp_path):
    _write_action_workflow(tmp_path / "good.yml")
    (tmp_path / "fourth.yaml").write_text(
        "jobs:\n  renamed_job:\n    steps:\n"
        "      - run: python -u scripts/classify_changed_paths.py\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="reserved classifier identifier"):
        _workflow_action_contract(tmp_path)


def test_second_direct_job_is_rejected_closed_world(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_jobs=(
            "  other:\n    steps:\n"
            "      - run: env X=1 python scripts/classify_changed_paths.py\n"
        ),
    )
    with pytest.raises(AssertionError, match="reserved classifier identifier"):
        _workflow_action_contract(tmp_path)


@pytest.mark.parametrize(
    "reference",
    (
        "../actions/classify-changed-paths",
        "owner/repo/.github/actions/classify-changed-paths@main",
        "./.github/actions/classify_changed_paths",
    ),
)
def test_noncanonical_classifier_action_reference_is_rejected(tmp_path, reference):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_jobs=f"  other:\n    steps:\n      - uses: {reference}\n",
    )
    with pytest.raises(AssertionError, match="noncanonical classifier action"):
        _workflow_action_contract(tmp_path)


def test_reserved_output_on_non_authority_job_is_rejected(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_jobs="  other:\n    outputs:\n      code_paths_changed: 'true'\n    steps: []\n",
    )
    with pytest.raises(AssertionError, match="reserved classifier outputs"):
        _workflow_action_contract(tmp_path)


def _write_reusable(path: Path, outputs: str) -> None:
    path.write_text(
        "on:\n  workflow_call:\n    outputs:\n"
        f"{outputs}jobs:\n  harmless:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo ok\n",
        encoding="utf-8",
    )


def test_called_and_caller_cannot_bridge_reserved_output(tmp_path):
    _write_action_workflow(tmp_path / "good.yml")
    _write_reusable(
        tmp_path / "called.yaml",
        "      code_paths_changed:\n        value: ${{ jobs.harmless.outputs.value }}\n",
    )
    (tmp_path / "caller.yml").write_text(
        "jobs:\n  wrapper:\n    uses: ./.github/workflows/called.yaml\n"
        "  consume:\n    needs: wrapper\n"
        "    if: needs.wrapper.outputs.code_paths_changed != 'false'\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="may not export reserved"):
        _workflow_action_contract(tmp_path)


@pytest.mark.parametrize("output", tuple(RESERVED_OUTPUTS))
def test_innocuously_named_or_swapped_wrapper_cannot_export_reserved(tmp_path, output):
    _write_action_workflow(tmp_path / "good.yml")
    _write_reusable(
        tmp_path / "ordinary-wrapper.yaml",
        f"      {output}:\n        value: harmless\n",
    )
    with pytest.raises(AssertionError, match="may not export reserved"):
        _workflow_action_contract(tmp_path)


@pytest.mark.parametrize(
    "reference",
    (
        "./.github/workflows/ordinary.yml",
        "owner/repo/.github/workflows/ordinary.yml@main",
    ),
)
def test_local_or_remote_reusable_job_cannot_feed_reserved_output(tmp_path, reference):
    _write_action_workflow(tmp_path / "good.yml")
    (tmp_path / "caller.yaml").write_text(
        "jobs:\n  wrapper:\n"
        f"    uses: {reference}\n"
        "  consume:\n    needs: wrapper\n"
        "    if: needs.wrapper.outputs.docs_site_affected != 'false'\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="reserved-output consumption"):
        _workflow_action_contract(tmp_path)


def test_classifier_like_reusable_reference_is_rejected(tmp_path):
    _write_action_workflow(tmp_path / "good.yml")
    (tmp_path / "caller.yml").write_text(
        "jobs:\n  wrapper:\n"
        "    uses: ./.github/workflows/classify-changed-paths.yml\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="reserved classifier reusable"):
        _workflow_action_contract(tmp_path)


def test_unrelated_reusable_workflow_and_output_remain_allowed(tmp_path):
    _write_action_workflow(tmp_path / "good.yml")
    _write_reusable(
        tmp_path / "called.yaml",
        "      artifact_name:\n        value: harmless\n",
    )
    (tmp_path / "caller.yml").write_text(
        "jobs:\n  wrapper:\n    uses: ./.github/workflows/called.yaml\n"
        "  consume:\n    needs: wrapper\n"
        "    if: needs.wrapper.outputs.artifact_name != ''\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        encoding="utf-8",
    )
    _workflow_action_contract(tmp_path)


def test_dynamic_remote_reusable_output_condition_is_unmanifested(tmp_path):
    _write_action_workflow(tmp_path / "good.yml")
    (tmp_path / "fourth-remote.yaml").write_text(
        "jobs:\n"
        "  wrapper:\n    uses: owner/repo/.github/workflows/ordinary.yml@main\n"
        "  consume:\n    needs: wrapper\n"
        "    if: ${{ needs.wrapper.outputs[format('{0}', 'result')] }}\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="if control manifest"):
        _workflow_action_contract(tmp_path)


def test_fully_dynamic_output_expression_is_still_a_manifested_control(tmp_path):
    _write_action_workflow(
        tmp_path / "good.yml",
        extra_jobs=(
            "  wrapper:\n    uses: owner/repo/.github/workflows/ordinary.yml@main\n"
            "  consume:\n    needs: wrapper\n"
            "    if: ${{ needs['wrapper'][format('out{0}', 'puts')]"
            "[format('{0}', 'result')] }}\n"
            "    runs-on: ubuntu-latest\n    steps: []\n"
        ),
    )
    with pytest.raises(AssertionError, match="if control manifest"):
        _workflow_action_contract(tmp_path)


def test_dynamic_continue_on_error_is_manifested_and_rejected(tmp_path):
    _write_action_workflow(
        tmp_path / "good.yml",
        extra_jobs=(
            "  other:\n    runs-on: ubuntu-latest\n"
            "    continue-on-error: ${{ fromJSON(vars.ALLOW_FAILURE) }}\n"
            "    steps: []\n"
        ),
    )
    with pytest.raises(AssertionError, match="continue-on-error control manifest"):
        _workflow_action_contract(tmp_path)


@pytest.mark.parametrize(
    "extra_job",
    (
        "  other:\n    if: ${{ needs['detect'].outputs.code_paths_changed }}\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        "  other:\n    if: ${{ needs.detect-job.outputs.docs_site_affected }}\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        "  other:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - env:\n          VALUE: ${{ steps.alternate.outputs.code_paths_changed }}\n"
        "        run: echo ok\n",
        "  other:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: echo 'code_paths_changed=true' >> $GITHUB_OUTPUT\n",
        "  other:\n    if: ${{ format('{0}', needs.detect.outputs.code_paths_changed) }}\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        "  wrong-location:\n    if: needs.detect.outputs.code_paths_changed != 'false'\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
    ),
)
def test_reserved_verdict_expression_variants_and_fabrication_are_rejected(
    tmp_path, extra_job
):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(workflow, extra_jobs=extra_job)
    with pytest.raises(AssertionError, match="reserved verdict namespace"):
        _workflow_action_contract(tmp_path)


def test_extra_unrelated_authority_step_is_deliberately_forbidden(tmp_path):
    workflow = tmp_path / "fourth.yml"
    _write_action_workflow(
        workflow,
        extra_authority_steps="      - uses: actions/setup-python@v6\n",
    )
    with pytest.raises(AssertionError, match="exactly two steps"):
        _workflow_action_contract(tmp_path)


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


# The step-level condition a gate in the Operator Docs job may carry, and the
# only one. The ratchets diff the pull request against its merge base; on a
# push to dev or main `github.base_ref` is empty, so there is no base ref to
# fetch and nothing to diff. Skipping a STEP does not skip the JOB, so the
# required context still reports a conclusion on every event, which is the
# property branch protection depends on. A step-level condition is therefore
# safe here in a way a job-level one is not.
PULL_REQUEST_ONLY = "github.event_name == 'pull_request'"

# The gates the `Operator Docs` context is trusted to enforce, keyed by the
# step `id:` the workflow gives each one. The value is the human description,
# the accepted `run:` command prefixes, and the exact step-level `if:` that
# gate is allowed to carry (`None` meaning it must carry none at all).
#
# The detect-secrets gate accepts two prefixes because the step deliberately
# runs the BASE ref's copy of the script when it exists and falls back to
# HEAD's only to bootstrap a base that predates the helper. Either branch is
# a real invocation, so pinning one of them would turn a legitimate cleanup
# of the other into a red build.
#
# This table is the pinned half. The half derived from the workflow is the set
# of step ids the job's own run summary reports on, which
# `test_gate_inventory_matches_the_run_summary` asserts is the same set: a
# seventh gate, or a gate that moves out of this job, cannot be added or
# removed without a deliberate edit here.
OPERATOR_DOCS_GATES = {
    "docs_check": (
        "the link, terminology and screenshot check",
        ("npm run docs:check",),
        None,
    ),
    "em_dash_ratchet": (
        "the em-dash ratchet",
        ("python3 scripts/check_em_dashes.py",),
        PULL_REQUEST_ONLY,
    ),
    "pii_ratchet": (
        "the personal-identifier and ECM-credential ratchet",
        ("python3 scripts/check_pii.py",),
        PULL_REQUEST_ONLY,
    ),
    "secrets_ratchet": (
        "the generic detect-secrets ratchet",
        (
            'python3 "$RUNNER_TEMP/check_secrets.py"',
            "python3 scripts/check_secrets.py",
        ),
        PULL_REQUEST_ONLY,
    ),
    "binary_artifacts": (
        "the added binary-artifact ratchet",
        ("python3 scripts/check_binary_artifacts.py",),
        PULL_REQUEST_ONLY,
    ),
    "site_build": (
        "the strict published-site build",
        ("mkdocs build --strict",),
        None,
    ),
}

# Job-level keys the `Operator Docs` job may declare. None of them can skip
# the job, change the name it reports under, or hide its failure. Every other
# key is refused by default, because the ways to break a required context are
# open-ended and an allowlist fails on the ones nobody thought of:
#
#   * `if:` and `needs:` can leave the job SKIPPED, and a required context
#     that never reports blocks the pull request with no admin bypass.
#   * `strategy:` expands the context name into `Operator Docs (<value>)`.
#     build.yml:codeql-analysis is the live proof: its matrix is why the two
#     required contexts there are `CodeQL Analysis (python)` and
#     `CodeQL Analysis (javascript-typescript)`, not `CodeQL Analysis`.
#   * `uses:` makes it a reusable-workflow call, which reports as
#     `Operator Docs / <inner job>`.
#   * `continue-on-error: true` reports success no matter what failed.
#
# The last two rename or hollow the context rather than skipping it, which is
# the same outcome for branch protection: the stored name never goes green.
SAFE_OPERATOR_DOCS_JOB_KEYS = frozenset(
    {
        "name",
        "runs-on",
        "steps",
        "permissions",
        "timeout-minutes",
        "env",
        "defaults",
        "container",
    }
)

_STEP_OUTCOME_REFERENCE = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outcome")


class TestOperatorDocsRequiredContext:
    """`Operator Docs` is a branch-protection required context on `dev`.

    It carries six gates, two of them documentation correctness and four of
    them security ratchets, and until this contract existed nothing stopped an
    edit from skipping the job, renaming it, or moving a gate out of it. Any
    of those wedges every pull request instead of failing one, because
    protection on `dev` is classic with `enforce_admins=true`: a required name
    that no job reports has no bypass.

    Bead pb2s4 is why the strict site build is one of the gates: a broken link
    passed `npm run docs:check`, failed `mkdocs build --strict`, and the
    strict build only ran after the merge."""

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
    JOB_ID = "operator-docs"
    DISPLAY_NAME = "Operator Docs"

    @staticmethod
    def _operator_docs_job() -> dict:
        import yaml

        workflow = yaml.safe_load(
            TestOperatorDocsRequiredContext.WORKFLOW.read_text(encoding="utf-8")
        )
        return (workflow.get("jobs") or {}).get(
            TestOperatorDocsRequiredContext.JOB_ID
        ) or {}

    @staticmethod
    def _step_with_id(job: dict, step_id: str) -> dict | None:
        for step in job.get("steps") or []:
            if (step or {}).get("id") == step_id:
                return step
        return None

    @staticmethod
    def _invokes(step: dict, prefixes: tuple[str, ...]) -> bool:
        """Whether the step's `run:` actually invokes one of these commands.

        Matching the raw `run:` text of the whole job would let a comment
        mentioning the command, or the `echo` that names it in the step
        summary, satisfy an assertion that the command runs. Both are present
        in this job, so the distinction is not hypothetical. Comment lines are
        dropped and the command must START the line."""
        for line in str(step.get("run", "")).splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(stripped.startswith(prefix) for prefix in prefixes):
                return True
        return False

    def test_job_key_and_display_name_are_pinned(self):
        """Both, because they fail differently.

        GitHub matches a required status check by the DISPLAYED job name, so
        `name:` is what branch protection stores and the only spelling that
        can orphan the context. Deleting `name:` orphans it too: the job key
        `operator-docs` becomes the displayed name. The key is pinned as well
        because it is how every other assertion here finds the job, and
        without it a renamed key would surface as six confusing "gate is
        gone" failures instead of one accurate one."""
        import yaml

        jobs = (
            yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8")).get("jobs") or {}
        )
        assert self.JOB_ID in jobs, (
            f"the `{self.JOB_ID}` job is gone from test.yml. It reports the "
            f"required status check {self.DISPLAY_NAME!r} on `dev`, so "
            f"removing or renaming the job leaves protection waiting forever "
            f"for a context nothing emits."
        )
        assert (jobs[self.JOB_ID] or {}).get("name") == self.DISPLAY_NAME, (
            f"test.yml:{self.JOB_ID} no longer displays as "
            f"{self.DISPLAY_NAME!r}. Branch protection stores the DISPLAYED "
            f"name, so this rename silently orphans the required context and "
            f"wedges every pull request. Change branch protection first, then "
            f"this test, then the workflow."
        )

    def test_job_declares_only_keys_that_cannot_skip_or_rename_it(self):
        """The catch-all for failure modes nobody enumerated.

        `paths:`/`paths-ignore:` are not even valid at job level, so GitHub
        would reject the workflow rather than run a filtered job, but they are
        refused here too: an editor reaching for one should get an explanation
        rather than a syntax error."""
        declared = set(self._operator_docs_job().keys())
        unsafe = sorted(declared - SAFE_OPERATOR_DOCS_JOB_KEYS)
        assert not unsafe, (
            f"test.yml:{self.JOB_ID} declares {unsafe}, which is not on the "
            f"allowlist of job-level keys that are safe for a required "
            f"status check. A required context must run on every event and "
            f"report under exactly one unchanging name: `if:`/`needs:` can "
            f"skip it, `strategy:` and `uses:` rename it, and "
            f"`continue-on-error:` hollows it out. Any of those blocks every "
            f"pull request on `dev`, where `enforce_admins` is true and there "
            f"is no bypass. If the key is genuinely safe, add it to "
            f"SAFE_OPERATOR_DOCS_JOB_KEYS with the reason it cannot skip, "
            f"rename, or hollow the job."
        )

    def test_job_stays_ungated(self):
        """Named assertions for the three keys that are actually reached for.

        The allowlist above already refuses these. They are asserted
        separately because a failure message that names the specific hole is
        worth more than a generic one, and because gating this job would also
        defeat its purpose: a Markdown-only change is exactly what it
        exists to check."""
        job = self._operator_docs_job()
        assert "if" not in job, (
            "the Operator Docs job gained a job-level `if:`. It reports a "
            "required status check, and GitHub never reports a conclusion for "
            "a job it skips, so any condition that can evaluate false wedges "
            "every pull request. Gate the STEPS instead: a skipped step still "
            "leaves the job reporting."
        )
        assert "needs" not in job, (
            "the Operator Docs job gained a `needs:` dependency. Without an "
            "explicit `!cancelled()` guard a job with `needs:` is SKIPPED "
            "when its dependency fails, and a skipped required context never "
            "reports. It runs unconditionally on purpose."
        )
        assert job.get("continue-on-error") is not True, (
            "the Operator Docs job carries `continue-on-error: true`, so it "
            "reports success whatever its gates found. A required check that "
            "cannot go red is decorative."
        )

    def test_gate_inventory_matches_the_run_summary(self):
        """The half of the gate list derived from the workflow itself.

        The job's run summary reports one line per gate, each reading that
        step's `outcome`, so the workflow states its own gate inventory. If
        the two lists disagree, either a gate was added without deciding
        whether the required context is now trusted to enforce it, or one was
        deleted or moved to another job. Moving a gate out of a required
        context is exactly as dangerous as deleting it: protection still shows
        green and nothing runs the check."""
        job = self._operator_docs_job()
        reported = {
            match
            for scalar in _scalar_strings(job)
            for match in _STEP_OUTCOME_REFERENCE.findall(scalar)
        }
        assert reported == set(OPERATOR_DOCS_GATES), (
            f"the Operator Docs run summary reports on {sorted(reported)} but "
            f"this contract pins {sorted(OPERATOR_DOCS_GATES)}. `Operator "
            f"Docs` is a required status check, so its gate list is a "
            f"branch-protection fact, not an implementation detail: adding, "
            f"removing, or relocating a gate has to be a deliberate edit to "
            f"OPERATOR_DOCS_GATES."
        )

    @pytest.mark.parametrize("step_id", sorted(OPERATOR_DOCS_GATES))
    def test_gate_is_invoked_by_this_job(self, step_id):
        description, prefixes, _condition = OPERATOR_DOCS_GATES[step_id]
        step = self._step_with_id(self._operator_docs_job(), step_id)
        assert step is not None, (
            f"{description} (step id `{step_id}`) is no longer a step of the "
            f"Operator Docs job. If it moved to another job, that job is not "
            f"the required context: the gate stops blocking merges the moment "
            f"it leaves this job, whether or not it still runs somewhere."
        )
        assert self._invokes(step, prefixes), (
            f"the `{step_id}` step no longer runs any of {list(prefixes)}, so "
            f"{description} is present in name only. A required check that "
            f"reports on a command it does not run is worse than no check."
        )

    @pytest.mark.parametrize("step_id", sorted(OPERATOR_DOCS_GATES))
    def test_gate_step_can_fail_the_job(self, step_id):
        """Finding the command in the file is not enough.

        `continue-on-error: true` swallows the failure and an unexpected `if:`
        stops the step running, and either produces an `Operator Docs` that is
        permanently green having checked nothing. Under branch protection that
        is worse than the job being absent, because absent is visible."""
        description, _prefixes, condition = OPERATOR_DOCS_GATES[step_id]
        step = self._step_with_id(self._operator_docs_job(), step_id)
        assert step is not None, (
            f"{description} (step id `{step_id}`) is no longer a step of the "
            f"Operator Docs job."
        )
        assert step.get("continue-on-error") is not True, (
            f"{description} carries `continue-on-error: true`. Its failure "
            f"would be swallowed and `Operator Docs` would report success "
            f"having verified nothing."
        )
        assert step.get("if") == condition, (
            f"{description} carries the condition {step.get('if')!r}, not "
            f"{condition!r}. The only condition a gate here may carry is "
            f"{PULL_REQUEST_ONLY!r}, and only because it has no merge base to "
            f"diff on a push. Any other condition silently narrows what the "
            f"required check covers; pin the exact new condition here rather "
            f"than loosening the assertion."
        )
