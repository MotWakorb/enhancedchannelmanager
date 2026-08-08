"""Tests for ``scripts/check_em_dashes.py`` (bead enhancedchannelmanager-3tflw).

The script is the ratchet guard against NEW em-dashes in documentation
prose. Its whole value rests on two properties, and both are tested here:

1. It honours the exemptions the style guide itself grants (code blocks,
   inline code spans, quoted strings, filenames, URLs, commands), and it
   does not confuse an en-dash or an arrow for an em-dash.
2. It scopes failures to newly added lines, so pre-existing debt does not
   red-line CI.

The scan surface is Markdown documentation only (`docs/**/*.md`, plus
top-level `README.md` / `CHANGELOG.md` / `CLAUDE.md`); Python and
TypeScript were removed from scope by the PO after the initial rollout
(same bead, scope narrowing) and are asserted OUT of the surface below.

These tests build their own inputs rather than asserting against real
repo files, so they stay stable as the docs churn.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_em_dashes.py"

EM = "—"  # EM DASH, the only character this guard rejects.
EN = "–"  # EN DASH, explicitly allowed.
ARROW = "→"  # RIGHTWARDS ARROW, explicitly allowed.


def _load_script_module():
    """Load check_em_dashes.py as an ad-hoc module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("check_em_dashes", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_em_dashes"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


def _lines(violations):
    return sorted(v.line for v in violations)


# --- Characters that are NOT em-dashes --------------------------------------


class TestNonEmDashCharacters:
    @pytest.mark.parametrize(
        "text",
        [
            f"A range of 2020{EN}2021 in prose.",
            f"Navigate System {ARROW} Users {ARROW} API Keys.",
            "A double hyphen -- in prose.",
            "A single hyphen - in prose.",
        ],
    )
    def test_allowed_dash_like_characters_are_not_flagged(self, script, text):
        assert script.scan_text("a.md", text, "markdown") == []

    def test_em_dash_is_flagged(self, script):
        violations = script.scan_text("a.md", f"Prose {EM} flagged.", "markdown")
        assert _lines(violations) == [1]


# --- Markdown exemptions ----------------------------------------------------


class TestMarkdownExemptions:
    def test_fenced_code_block_is_exempt(self, script):
        text = "\n".join(
            [
                "Prose line.",
                "```bash",
                f'echo "fenced {EM} exempt"',
                "```",
                f"After the fence {EM} flagged.",
            ]
        )
        assert _lines(script.scan_text("a.md", text, "markdown")) == [5]

    def test_tilde_fence_is_exempt(self, script):
        text = "\n".join(["~~~", f"log line {EM} exempt", "~~~"])
        assert script.scan_text("a.md", text, "markdown") == []

    def test_inline_code_span_is_exempt(self, script):
        text = f"Run `ecm --flag {EM} value` to continue."
        assert script.scan_text("a.md", text, "markdown") == []

    def test_prose_outside_a_code_span_on_the_same_line_is_flagged(self, script):
        text = f"Run `ecm --flag {EM} value` and then {EM} stop."
        assert _lines(script.scan_text("a.md", text, "markdown")) == [1]

    def test_link_destination_is_exempt(self, script):
        text = f"See [the page](https://example.com/a{EM}b) for detail."
        assert script.scan_text("a.md", text, "markdown") == []

    def test_bare_url_is_exempt(self, script):
        text = f"See https://example.com/a{EM}b for detail."
        assert script.scan_text("a.md", text, "markdown") == []

    def test_link_text_is_prose_and_is_flagged(self, script):
        text = f"See [the page {EM} here](https://example.com/) for detail."
        assert _lines(script.scan_text("a.md", text, "markdown")) == [1]

    def test_suppression_comment_skips_the_line(self, script):
        text = f"Quoted verbatim {EM} kept. <!-- em-dash-ok: upstream log line -->"
        assert script.scan_text("a.md", text, "markdown") == []


# --- Diff scoping (the ratchet itself) --------------------------------------


class TestParseAddedLines:
    def test_extracts_added_line_numbers_per_file(self, script):
        diff = "\n".join(
            [
                "diff --git a/docs/x.md b/docs/x.md",
                "--- a/docs/x.md",
                "+++ b/docs/x.md",
                "@@ -3,0 +4,2 @@",
                "+first added line",
                "+second added line",
                "@@ -20,1 +22,1 @@",
                "-removed line",
                "+replacement line",
            ]
        )
        assert script.parse_added_lines(diff) == {"docs/x.md": {4, 5, 22}}

    def test_ignores_deleted_files(self, script):
        diff = "\n".join(
            [
                "diff --git a/docs/gone.md b/docs/gone.md",
                "--- a/docs/gone.md",
                "+++ /dev/null",
                "@@ -1,1 +0,0 @@",
                "-only line",
            ]
        )
        assert script.parse_added_lines(diff) == {}

    def test_removed_lines_never_count_as_added(self, script):
        diff = "\n".join(
            [
                "diff --git a/docs/x.md b/docs/x.md",
                "--- a/docs/x.md",
                "+++ b/docs/x.md",
                "@@ -5,2 +5,0 @@",
                f"-old prose {EM} removed",
                "-another removed line",
            ]
        )
        assert script.parse_added_lines(diff) == {}


class TestScanSurface:
    @pytest.mark.parametrize(
        "path",
        [
            "docs/style_guide.md",
            "docs/user_guide/backup-restore/index.md",
            "README.md",
            "CHANGELOG.md",
            "CLAUDE.md",
        ],
    )
    def test_paths_inside_the_surface(self, script, path):
        assert script._in_scan_surface(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "frontend/node_modules/pkg/index.ts",
            "backend/__pycache__/main.py",
            ".beads/issues.jsonl",
            "site/index.md",
            "frontend/dist/assets/app.ts",
            ".github/workflows/test.yml",
            "frontend/package.json",
            # Python and TypeScript were removed from scope (PO call, bead
            # enhancedchannelmanager-3tflw scope narrowing): a source file
            # is out of the surface even when it sits in a directory the
            # guard used to walk.
            "backend/main.py",
            "mcp-server/server.py",
            "scripts/check_em_dashes.py",
            "frontend/src/App.tsx",
            "frontend/src/lib/util.ts",
        ],
    )
    def test_paths_outside_the_surface(self, script, path):
        assert script._in_scan_surface(path) is False


class TestKindFor:
    """`_kind_for` is the other half of the scope boundary: even a path
    that somehow lands inside the surface check must not classify as a
    scannable kind unless it is Markdown.
    """

    @pytest.mark.parametrize(
        "path",
        ["backend/main.py", "frontend/src/App.tsx", "frontend/src/lib/util.ts"],
    )
    def test_python_and_typescript_do_not_classify(self, script, path):
        assert script._kind_for(path) is None

    def test_markdown_classifies(self, script):
        assert script._kind_for("docs/x.md") == "markdown"

    def test_scan_paths_ignores_named_python_and_typescript_files(self, script):
        """Even an explicit `--paths` request against real, non-Markdown
        repo files (each of which contains at least one em-dash today) is
        a no-op: unclassified paths are skipped, never scanned as prose.
        """
        assert script.scan_paths(["backend/main.py", "frontend/src/App.tsx"]) == []


# --- Real-file regression ---------------------------------------------------


class TestRealFiles:
    def test_restore_drill_article_is_clean(self, script):
        """The drill article is dense in en-dashes and arrows and has zero
        em-dashes. If the scanner ever flags it, the allowed-character
        handling has regressed.
        """
        target = REPO_ROOT / "docs/user_guide/backup-restore/run-a-restore-drill.md"
        text = target.read_text(encoding="utf-8")
        assert EN in text and ARROW in text, "fixture assumption changed"
        assert script.scan_text(target.name, text, "markdown") == []

    def test_style_guide_rule_text_still_names_the_em_dash(self, script):
        """The guard is meaningless if the rule it enforces is edited away."""
        text = (REPO_ROOT / "docs/style_guide.md").read_text(encoding="utf-8")
        assert f"No em-dashes (`{EM}`) in prose" in text
