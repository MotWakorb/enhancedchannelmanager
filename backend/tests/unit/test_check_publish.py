"""Tests for ``scripts/check_publish.py`` (bead enhancedchannelmanager-t8fqg).

The script is the post-merge guard against `dev` and the container
registry diverging. Everything that talks to GitHub or a registry is
exercised through its pure parsing/selection helpers, so the suite needs
no network, no `gh` auth, and no Docker daemon.

The git-backed helpers are exercised against a THROWAWAY repository built
in `tmp_path`, never against the checkout the tests happen to run in. An
earlier revision of this file asserted against `origin/dev` and passed on
every developer clone while failing in CI, where `actions/checkout` makes
a single-branch, depth-1 clone that carries no remote-tracking refs at
all. A test for a git helper must supply its own git history: borrowing
the ambient repository's shape tests the checkout, not the code.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
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


# --- A repository of our own -------------------------------------------------


class FakeRepo:
    """A tiny git repository with a known, asserted-on shape.

    Two commits on `dev` (each bumping `frontend/package.json`), one
    unmerged commit on `sidebranch`, a matching `refs/remotes/origin/dev`,
    and a dirty working tree. That is every distinction the script's
    git helpers make, and none of it is inherited from the checkout the
    suite runs in.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.first = ""
        self.tip = ""
        self.sidebranch = ""

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return result.stdout

    def write_version(self, version: str) -> None:
        package = self.root / "frontend" / "package.json"
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text(
            json.dumps({"name": "ecm-frontend", "version": version}, indent=2) + "\n",
            encoding="utf-8",
        )

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path, script, monkeypatch) -> FakeRepo:
    # Isolate git from the developer's own config: no global identity, no
    # commit signing, no hooksPath pointing somewhere real.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")
    (tmp_path / "home").mkdir()

    root = tmp_path / "repo"
    root.mkdir()
    fake = FakeRepo(root)
    fake.git("init", "-q")
    # `git init -b` / `init.defaultBranch` need git >= 2.28; this does not.
    fake.git("symbolic-ref", "HEAD", "refs/heads/dev")

    fake.write_version("0.1.0-0001")
    fake.first = fake.commit("first")

    fake.write_version("0.2.0-0002")
    fake.tip = fake.commit("second")

    fake.git("checkout", "-q", "-b", "sidebranch", fake.first)
    fake.write_version("9.9.9-9999")
    fake.sidebranch = fake.commit("unmerged work")
    fake.git("checkout", "-q", "dev")

    # The remote-tracking ref the real repo would have after a fetch. Tests
    # that care about its ABSENCE delete it explicitly, so the CI-shaped
    # environment is covered by assertion rather than by luck.
    fake.git("update-ref", "refs/remotes/origin/dev", fake.tip)

    # A dirty working tree, so "reads the commit, not the tree" is a real
    # distinction rather than a tautology.
    fake.write_version("0.0.0-dirty")

    monkeypatch.setattr(script, "REPO_ROOT", root)
    return fake


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
    def test_reads_the_version_from_the_commit_not_the_working_tree(self, script, repo):
        """The distinction is the whole reason this check is legible: a
        feature branch's unbumped/bumped package.json must never be
        compared against what the registry publishes for `dev`.

        The fixture's working tree says 0.0.0-dirty and each commit says
        something else, so a working-tree read cannot pass this.
        """
        assert (repo.root / "frontend" / "package.json").read_text().count("0.0.0-dirty")
        assert script.expected_version_at(repo.tip) == "0.2.0-0002"
        assert script.expected_version_at(repo.first) == "0.1.0-0001"
        assert script.expected_version_at("dev") == "0.2.0-0002"

    def test_reads_a_sibling_branch_independently_of_the_checked_out_one(
        self, script, repo
    ):
        """`dev` is checked out; the answer for another branch must come
        from that branch's tree, not from HEAD's.
        """
        assert script.expected_version_at("sidebranch") == "9.9.9-9999"

    def test_unknown_ref_raises_check_error(self, script, repo):
        with pytest.raises(script.CheckError):
            script.expected_version_at("definitely-not-a-ref")

    def test_missing_origin_ref_says_what_to_fetch(self, script, repo):
        """The CI failure mode: a checkout with no remote-tracking refs.
        The operator gets an instruction, not `fatal: ambiguous argument`.
        """
        repo.git("update-ref", "-d", "refs/remotes/origin/dev")
        with pytest.raises(script.CheckError) as caught:
            script.expected_version_at("origin/dev")
        message = str(caught.value)
        assert "git fetch --no-tags origin dev" in message
        assert "shallow" in message

    def test_missing_package_json_is_reported_as_a_missing_file(self, script, repo):
        """A resolvable ref whose tree lacks the file is a different fault
        from an unresolvable ref, and must not be reported as one.
        """
        repo.git("rm", "-q", "-f", "frontend/package.json")
        without_package = repo.commit("drop package.json")
        with pytest.raises(script.CheckError) as caught:
            script.expected_version_at(without_package)
        message = str(caught.value)
        assert "frontend/package.json" in message
        assert "does not exist in this checkout" not in message

    def test_malformed_package_json_raises_check_error(self, script, repo):
        (repo.root / "frontend" / "package.json").write_text("{not json", encoding="utf-8")
        broken = repo.commit("break package.json")
        with pytest.raises(script.CheckError, match="not valid JSON"):
            script.expected_version_at(broken)


class TestResolveCommit:
    def test_resolves_a_branch_to_its_tip(self, script, repo):
        assert script.resolve_commit("dev") == repo.tip

    def test_unknown_ref_message_is_actionable(self, script, repo):
        with pytest.raises(script.CheckError) as caught:
            script.resolve_commit("origin/dev-that-was-never-fetched")
        message = str(caught.value)
        assert "git fetch --no-tags origin dev-that-was-never-fetched" in message

    def test_unknown_local_ref_message_names_the_checkout(self, script, repo):
        with pytest.raises(script.CheckError) as caught:
            script.resolve_commit("no-such-ref")
        assert str(repo.root) in str(caught.value)


class TestCommitIsOnBranch:
    def test_dev_tip_is_on_dev(self, script, repo):
        assert script.commit_is_on_branch(repo.tip, "dev") is True

    def test_an_ancestor_is_on_the_branch(self, script, repo):
        assert script.commit_is_on_branch(repo.first, "dev") is True

    def test_an_unmerged_commit_is_not_on_the_branch(self, script, repo):
        assert script.commit_is_on_branch(repo.sidebranch, "dev") is False

    def test_falls_back_to_the_local_branch_without_a_remote_tracking_ref(
        self, script, repo
    ):
        """The CI/shallow-clone shape. Losing `origin/dev` must not turn a
        merged commit into "not on dev", which reads as a publish defect.
        """
        repo.git("update-ref", "-d", "refs/remotes/origin/dev")
        assert script.commit_is_on_branch(repo.tip, "dev") is True
        assert script.commit_is_on_branch(repo.sidebranch, "dev") is False

    def test_prefers_the_remote_tracking_ref_over_the_local_branch(self, script, repo):
        """`origin/dev` is what the registry built from. When the local
        branch has moved ahead, the remote-tracking ref is the answer.
        """
        repo.git("update-ref", "refs/remotes/origin/dev", repo.first)
        assert script.commit_is_on_branch(repo.tip, "dev") is False
        assert script.commit_is_on_branch(repo.first, "dev") is True

    def test_unknown_branch_returns_none(self, script, repo):
        assert script.commit_is_on_branch(repo.tip, "no-such-branch-xyzzy") is None
