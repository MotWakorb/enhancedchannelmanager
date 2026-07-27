"""Tests for ``scripts/check_version_advances.py`` (bead enhancedchannelmanager-w9irb).

The script is the shared build-number-advance guard called by BOTH the CI
version-consistency job and the Claude Code agent ship hook. These tests
exercise the pure comparator (``parse_build`` / ``evaluate_advance``), the
soft CHANGELOG check, and the main() exit-code contract on a synthetic
frontend/package.json under tmp_path.

The comparator tests take version *strings* directly — no git, no IO — so
they stay stable across real version bumps.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_version_advances.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_version_advances", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_version_advances"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_script_module()


# ─── parse_build ────────────────────────────────────────────────────────────


class TestParseBuild:
    def test_numbered_version(self, mod):
        p = mod.parse_build("0.17.6-0085")
        assert p.kind == "numbered"
        assert p.prefix == "0.17.6"
        assert p.build == 85

    def test_no_suffix_is_release_cut(self, mod):
        p = mod.parse_build("0.17.6")
        assert p.kind == "no_suffix"
        assert p.build is None

    @pytest.mark.parametrize(
        "bad",
        ["", "not-a-version", "0.17.6-abc", "0.17-0085", "v0.17.6-0085", None],
    )
    def test_malformed(self, mod, bad):
        assert mod.parse_build(bad).kind == "malformed"

    def test_leading_zeros_parsed_as_int(self, mod):
        assert mod.parse_build("0.17.6-0007").build == 7


# ─── evaluate_advance ───────────────────────────────────────────────────────


class TestEvaluateAdvance:
    def test_advances(self, mod):
        r = mod.evaluate_advance("0.17.6-0086", "0.17.6-0085")
        assert r.status == "advanced"
        assert r.exit_code == 0

    def test_equal_build_fails(self, mod):
        r = mod.evaluate_advance("0.17.6-0085", "0.17.6-0085")
        assert r.status == "not_advanced"
        assert r.exit_code == 1
        # Suggests the next build number.
        assert "0.17.6-0086" in r.message

    def test_regresses_fails(self, mod):
        r = mod.evaluate_advance("0.17.6-0084", "0.17.6-0085")
        assert r.status == "not_advanced"
        assert r.exit_code == 1
        assert "0.17.6-0086" in r.message

    def test_minor_bump_advances_even_if_build_resets_higher(self, mod):
        # A minor bump that also advances the build number still passes.
        r = mod.evaluate_advance("0.18.0-0100", "0.17.6-0085")
        assert r.exit_code == 0

    def test_prefix_advance_with_counter_reset_passes(self, mod):
        # The documented post-release reset (shipping.md §Cut Mechanics step 8,
        # docs/versioning.md): after a release cut, dev reopens at the next
        # target's -0000. The build number legitimately resets because the
        # MAJOR.MINOR.PATCH prefix advanced (beads 92fhj + w9irb — this exact
        # case blocked the v0.18.0 back-sync PR).
        r = mod.evaluate_advance("0.18.1-0000", "0.17.6-0174")
        assert r.status == "advanced_prefix"
        assert r.exit_code == 0

    def test_prefix_advance_with_lower_build_passes(self, mod):
        r = mod.evaluate_advance("0.18.0-0001", "0.17.6-0085")
        assert r.exit_code == 0

    def test_prefix_regression_fails_even_with_higher_build(self, mod):
        # Closing the converse hole: a LOWER prefix must fail even when its
        # build number happens to exceed the baseline's.
        r = mod.evaluate_advance("0.16.0-0200", "0.17.6-0085")
        assert r.status == "prefix_regression"
        assert r.exit_code == 1

    def test_same_prefix_build_regression_still_fails(self, mod):
        # The prefix-aware fix must not weaken the original same-prefix rule.
        r = mod.evaluate_advance("0.17.6-0084", "0.17.6-0085")
        assert r.status == "not_advanced"
        assert r.exit_code == 1

    def test_no_suffix_current_is_exempt(self, mod):
        # Release-cut PR: current version drops the -BUILD suffix.
        r = mod.evaluate_advance("0.17.6", "0.17.6-0085")
        assert r.status == "exempt_no_suffix"
        assert r.exit_code == 0

    def test_malformed_current_fails(self, mod):
        r = mod.evaluate_advance("0.17.6-xx", "0.17.6-0085")
        assert r.status == "malformed_current"
        assert r.exit_code == 1

    def test_missing_baseline_soft_passes(self, mod):
        r = mod.evaluate_advance("0.17.6-0086", None)
        assert r.status == "baseline_unavailable"
        assert r.exit_code == 0

    def test_no_suffix_baseline_soft_passes(self, mod):
        # Baseline is a release (no suffix) — cannot compare builds; soft pass.
        r = mod.evaluate_advance("0.17.6-0086", "0.17.6")
        assert r.status == "baseline_unavailable"
        assert r.exit_code == 0

    def test_suggestion_preserves_zero_padding(self, mod):
        r = mod.evaluate_advance("0.17.6-0009", "0.17.6-0009")
        assert "0.17.6-0010" in r.message


# ─── CHANGELOG soft check ───────────────────────────────────────────────────


class TestChangelogSoftCheck:
    CHANGELOG_A = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- thing\n\n## [0.1.0] - 2026\n- old\n"
    CHANGELOG_B = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- thing\n- NEW thing\n\n## [0.1.0] - 2026\n- old\n"

    def test_extract_unreleased_region(self, mod):
        region = mod.extract_unreleased_region(self.CHANGELOG_A)
        assert "thing" in region
        assert "old" not in region  # stops at next heading

    def test_unchanged_unreleased_warns(self, mod):
        w = mod.changelog_soft_warning(self.CHANGELOG_A, self.CHANGELOG_A)
        assert w is not None
        assert "unchanged" in w.lower()

    def test_changed_unreleased_no_warning(self, mod):
        assert mod.changelog_soft_warning(self.CHANGELOG_B, self.CHANGELOG_A) is None

    def test_missing_unreleased_heading_warns(self, mod):
        w = mod.changelog_soft_warning("# Changelog\n\n## [0.1.0]\n- x\n", self.CHANGELOG_A)
        assert w is not None

    def test_no_current_changelog_silent(self, mod):
        assert mod.changelog_soft_warning(None, self.CHANGELOG_A) is None


# ─── main() exit-code contract on a synthetic tree ──────────────────────────


class TestMainExitCode:
    def _write_pkg(self, path: Path, version: str) -> Path:
        path.write_text(f'{{"name": "ecm", "version": "{version}"}}', encoding="utf-8")
        return path

    def test_main_blocks_non_advancing(self, mod, tmp_path, capsys):
        current = self._write_pkg(tmp_path / "current.json", "0.17.6-0085")
        baseline = self._write_pkg(tmp_path / "baseline.json", "0.17.6-0085")
        rc = mod.main(
            [
                "--current-file",
                str(current),
                "--baseline-file",
                str(baseline),
                "--repo-root",
                str(tmp_path),
            ]
        )
        assert rc == 1
        assert "does not advance" in capsys.readouterr().err

    def test_main_passes_advancing(self, mod, tmp_path, capsys):
        current = self._write_pkg(tmp_path / "current.json", "0.17.6-0086")
        baseline = self._write_pkg(tmp_path / "baseline.json", "0.17.6-0085")
        rc = mod.main(
            [
                "--current-file",
                str(current),
                "--baseline-file",
                str(baseline),
                "--repo-root",
                str(tmp_path),
            ]
        )
        assert rc == 0
        assert "advances" in capsys.readouterr().out

    def test_main_exempts_release_cut(self, mod, tmp_path, capsys):
        current = self._write_pkg(tmp_path / "current.json", "0.17.6")
        baseline = self._write_pkg(tmp_path / "baseline.json", "0.17.6-0085")
        rc = mod.main(
            [
                "--current-file",
                str(current),
                "--baseline-file",
                str(baseline),
                "--repo-root",
                str(tmp_path),
            ]
        )
        assert rc == 0
        assert "EXEMPT" in capsys.readouterr().out
