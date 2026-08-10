"""Tests for ``scripts/ci_junit_summary.py`` (bead enhancedchannelmanager-t4d5w).

The script writes the line that tells a reader whether a green required check
actually ran a suite. Two properties matter and are pinned here:

1. The count it reports is the count in the report, so the line is evidence
   rather than a claim.
2. It can never fail the job. It runs inside `Backend Tests`, `Frontend Tests`
   and `MCP Server Tests`, all required contexts on `dev`; a cosmetic summary
   writer that exits non-zero would turn a passing suite red.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci_junit_summary.py"

PYTEST_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="2" skipped="3" tests="2147" time="51.2">
    <testcase classname="tests.unit.test_x" name="test_one" time="0.01"/>
  </testsuite>
</testsuites>
"""

# Some writers emit a bare <testsuite> root rather than wrapping it.
BARE_SUITE_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="vitest" errors="0" failures="0" skipped="0" tests="512" time="9.1"/>
"""

MULTI_SUITE_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="a" errors="0" failures="1" skipped="0" tests="10"/>
  <testsuite name="b" errors="2" failures="0" skipped="4" tests="15"/>
</testsuites>
"""


def _load_script_module():
    spec = importlib.util.spec_from_file_location("ci_junit_summary", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_junit_summary"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


class TestReadCounts:
    def test_reads_a_pytest_report(self, script, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text(PYTEST_REPORT, encoding="utf-8")
        assert script.read_counts(report) == {
            "tests": 2147,
            "failures": 2,
            "errors": 1,
            "skipped": 3,
        }

    def test_reads_a_bare_testsuite_root(self, script, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text(BARE_SUITE_REPORT, encoding="utf-8")
        counts = script.read_counts(report)
        assert counts is not None
        assert counts["tests"] == 512

    def test_sums_across_suites(self, script, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text(MULTI_SUITE_REPORT, encoding="utf-8")
        assert script.read_counts(report) == {
            "tests": 25,
            "failures": 1,
            "errors": 2,
            "skipped": 4,
        }

    def test_missing_report_is_not_an_error(self, script, tmp_path):
        assert script.read_counts(tmp_path / "absent.xml") is None

    def test_unparseable_report_is_not_an_error(self, script, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text("<testsuites><broken", encoding="utf-8")
        assert script.read_counts(report) is None

    def test_report_with_no_testsuite_is_not_an_error(self, script, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text("<testsuites/>", encoding="utf-8")
        assert script.read_counts(report) is None

    def test_a_malformed_attribute_costs_only_that_counter(self, script, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text(
            '<testsuite tests="7" failures="oops" errors="0" skipped="0"/>',
            encoding="utf-8",
        )
        counts = script.read_counts(report)
        assert counts is not None
        assert counts["tests"] == 7
        assert counts["failures"] == 0


class TestFormatLine:
    def test_reports_the_counts(self, script):
        line = script.format_line(
            "Backend Tests",
            "ran the backend pytest suite",
            {"tests": 2147, "failures": 0, "errors": 0, "skipped": 0},
        )
        assert line == (
            "**Backend Tests**: ran the backend pytest suite. "
            "2147 tests, 0 failed, 0 errored."
        )

    def test_mentions_skips_only_when_there_are_any(self, script):
        with_skips = script.format_line(
            "Backend Tests",
            "ran it",
            {"tests": 10, "failures": 0, "errors": 0, "skipped": 2},
        )
        assert "2 skipped" in with_skips

    def test_says_so_when_the_count_is_unavailable(self, script):
        line = script.format_line("Frontend Tests", "ran the vitest suite", None)
        assert "test count unavailable" in line
        # The claim about what ran must survive even without evidence for it.
        assert "ran the vitest suite" in line

    def test_line_is_a_single_line(self, script):
        """The caller appends it to $GITHUB_STEP_SUMMARY with `>>`."""
        line = script.format_line(
            "MCP Server Tests",
            "ran the mcp-server pytest suite",
            {"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
        )
        assert "\n" not in line

    def test_no_em_dash_in_the_rendered_line(self, script):
        """docs/style_guide.md bans the em-dash in prose this project emits."""
        line = script.format_line("Backend Tests", "ran it", None)
        assert "—" not in line


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class TestCommandLine:
    def test_prints_one_line_and_exits_zero(self, tmp_path):
        report = tmp_path / "junit.xml"
        report.write_text(PYTEST_REPORT, encoding="utf-8")
        result = _run_cli(
            [
                "--label",
                "Backend Tests",
                "--ran",
                "ran the backend pytest suite",
                "--junit",
                str(report),
            ]
        )
        assert result.returncode == 0
        assert len(result.stdout.splitlines()) == 1
        assert "2147 tests" in result.stdout

    def test_missing_report_still_exits_zero(self, tmp_path):
        """The failure mode this guards: a red required check for a cosmetic
        reason, on a run whose suite actually passed."""
        result = _run_cli(
            [
                "--label",
                "Backend Tests",
                "--ran",
                "ran the backend pytest suite",
                "--junit",
                str(tmp_path / "absent.xml"),
            ]
        )
        assert result.returncode == 0
        assert "test count unavailable" in result.stdout

    def test_unreadable_report_still_exits_zero(self, tmp_path):
        """A directory where a file is expected: OSError, not FileNotFound."""
        directory = tmp_path / "junit.xml"
        directory.mkdir()
        result = _run_cli(
            ["--label", "L", "--ran", "ran it", "--junit", str(directory)]
        )
        assert result.returncode == 0
        assert "test count unavailable" in result.stdout
