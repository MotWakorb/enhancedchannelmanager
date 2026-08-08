"""Tests for ``scripts/check_publish.py`` (bead enhancedchannelmanager-t8fqg).

The script is the post-merge guard against `dev` and the container
registry diverging. Everything that talks to GitHub or a registry is
exercised through its pure parsing/selection helpers, so the suite needs
no network, no `gh` auth, and no Docker daemon.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_publish.py"


def _load_script_module():
    """Load check_publish.py as an ad-hoc module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("check_publish", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_publish"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


def _run(number, *, event="push", branch="dev", name=None, attempt=1, conclusion="success"):
    return {
        "name": name or "Build and Push Docker Image",
        "head_branch": branch,
        "event": event,
        "run_number": number,
        "run_attempt": attempt,
        "status": "completed",
        "conclusion": conclusion,
        "html_url": f"https://example.invalid/runs/{number}",
    }


# --- Run selection ----------------------------------------------------------


class TestSelectBuildRun:
    def test_picks_the_matching_push_run(self, script):
        runs = [_run(10)]
        chosen = script.select_build_run(runs, "Build and Push Docker Image", "dev")
        assert chosen is not None and chosen["run_number"] == 10

    def test_ignores_pull_request_runs_for_the_same_sha(self, script):
        """A PR run and a branch-push run share a head SHA. Only the push
        publishes, so a green PR run must never stand in for a failed push.
        """
        runs = [
            _run(11, event="pull_request", branch="feature", conclusion="success"),
            _run(12, event="push", conclusion="failure"),
        ]
        chosen = script.select_build_run(runs, "Build and Push Docker Image", "dev")
        assert chosen is not None
        assert chosen["run_number"] == 12
        assert chosen["conclusion"] == "failure"

    def test_ignores_other_workflows(self, script):
        runs = [_run(13, name="Tests")]
        assert script.select_build_run(runs, "Build and Push Docker Image", "dev") is None

    def test_ignores_runs_on_another_branch(self, script):
        runs = [_run(14, branch="main")]
        assert script.select_build_run(runs, "Build and Push Docker Image", "dev") is None

    def test_prefers_the_latest_attempt_of_a_rerun(self, script):
        """A re-run that fixes a flake is the state of record, not the
        original failure that motivated it.
        """
        runs = [
            _run(20, attempt=1, conclusion="failure"),
            _run(20, attempt=2, conclusion="success"),
        ]
        chosen = script.select_build_run(runs, "Build and Push Docker Image", "dev")
        assert chosen is not None and chosen["run_attempt"] == 2

    def test_returns_none_for_an_empty_list(self, script):
        assert script.select_build_run([], "Build and Push Docker Image", "dev") is None


# --- Image config parsing ---------------------------------------------------


class TestParseImagetoolsConfig:
    def test_parses_a_multi_platform_manifest(self, script):
        payload = """
        {
          "linux/amd64": {
            "config": {"Env": ["PATH=/bin", "ECM_VERSION=0.18.1-0043",
                               "GIT_COMMIT=abc1234"]}
          },
          "linux/arm64": {
            "config": {"Env": ["ECM_VERSION=0.18.1-0043"]}
          }
        }
        """
        env = script.parse_imagetools_config(payload)
        assert env["ECM_VERSION"] == "0.18.1-0043"
        assert env["GIT_COMMIT"] == "abc1234"

    def test_parses_a_single_platform_config(self, script):
        payload = '{"created": "now", "config": {"Env": ["ECM_VERSION=0.18.1-0044"]}}'
        assert script.parse_imagetools_config(payload)["ECM_VERSION"] == "0.18.1-0044"

    def test_env_entries_with_equals_signs_in_the_value_survive(self, script):
        payload = '{"config": {"Env": ["OPTS=a=b=c", "ECM_VERSION=0.1.0-0001"]}}'
        env = script.parse_imagetools_config(payload)
        assert env["OPTS"] == "a=b=c"
        assert env["ECM_VERSION"] == "0.1.0-0001"

    def test_raises_on_unparseable_output(self, script):
        with pytest.raises(script.CheckError):
            script.parse_imagetools_config("not json")

    def test_raises_when_no_config_env_is_present(self, script):
        with pytest.raises(script.CheckError):
            script.parse_imagetools_config('{"linux/amd64": {"rootfs": {}}}')


# --- Repo-side facts --------------------------------------------------------


class TestExpectedVersion:
    def test_reads_the_version_from_the_commit_not_the_working_tree(self, script):
        """The distinction is the whole reason this check is legible: a
        feature branch's unbumped/bumped package.json must never be
        compared against what the registry publishes for `dev`.
        """
        head_version = script.expected_version_at("HEAD")
        dev_version = script.expected_version_at("origin/dev")
        assert head_version.count("-") == 1
        assert dev_version.count("-") == 1

    def test_unknown_ref_raises_check_error(self, script):
        with pytest.raises(script.CheckError):
            script.expected_version_at("definitely-not-a-ref")


class TestCommitIsOnBranch:
    def test_dev_tip_is_on_dev(self, script):
        sha = script.resolve_commit("origin/dev")
        assert script.commit_is_on_branch(sha, "dev") is True

    def test_unknown_branch_returns_none(self, script):
        sha = script.resolve_commit("HEAD")
        assert script.commit_is_on_branch(sha, "no-such-branch-xyzzy") is None
