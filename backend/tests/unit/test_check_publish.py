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


TEST_SHA = "a" * 40
TEST_VERSION = "0.18.1-0149"


def _run(
    number,
    *,
    event="push",
    branch="dev",
    name=None,
    attempt=1,
    status="completed",
    conclusion="success",
):
    return {
        "id": 33003267130,
        "name": name or "Tests",
        "head_branch": branch,
        "event": event,
        "run_number": number,
        "run_attempt": attempt,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://example.invalid/runs/{number}",
    }


def _job(
    name="Publish Verified Dev Images / Publish Verified Multi-Arch Manifests",
    *,
    status="completed",
    conclusion="success",
):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://example.invalid/jobs/98294803170",
    }


# --- Run selection ----------------------------------------------------------


class TestSelectBuildRun:
    def test_picks_the_matching_push_run(self, script):
        runs = [_run(10)]
        chosen = script.select_build_run(runs, "Tests", "dev")
        assert chosen is not None and chosen["run_number"] == 10

    def test_ignores_pull_request_runs_for_the_same_sha(self, script):
        """A PR run and a branch-push run share a head SHA. Only the push
        publishes, so a green PR run must never stand in for a failed push.
        """
        runs = [
            _run(11, event="pull_request", branch="feature", conclusion="success"),
            _run(12, event="push", conclusion="failure"),
        ]
        chosen = script.select_build_run(runs, "Tests", "dev")
        assert chosen is not None
        assert chosen["run_number"] == 12
        assert chosen["conclusion"] == "failure"

    def test_ignores_other_workflows(self, script):
        runs = [_run(13, name="Build and Push Docker Image")]
        assert script.select_build_run(runs, "Tests", "dev") is None

    def test_ignores_runs_on_another_branch(self, script):
        runs = [_run(14, branch="main")]
        assert script.select_build_run(runs, "Tests", "dev") is None

    def test_prefers_the_latest_attempt_of_a_rerun(self, script):
        """A re-run that fixes a flake is the state of record, not the
        original failure that motivated it.
        """
        runs = [
            _run(20, attempt=1, conclusion="failure"),
            _run(20, attempt=2, conclusion="success"),
        ]
        chosen = script.select_build_run(runs, "Tests", "dev")
        assert chosen is not None and chosen["run_attempt"] == 2

    def test_returns_none_for_an_empty_list(self, script):
        assert script.select_build_run([], "Tests", "dev") is None


class TestSelectPublishJob:
    def test_selects_the_reusable_workflow_manifest_job(self, script):
        chosen = script.select_publish_job([_job()])
        assert chosen is not None
        assert chosen["conclusion"] == "success"

    def test_green_tests_run_without_publish_job_is_not_proof(self, script):
        jobs = [_job(name="Backend Tests"), _job(name="Frontend Tests")]
        assert script.select_publish_job(jobs) is None

    def test_failed_publish_job_is_returned_for_the_verdict(self, script):
        chosen = script.select_publish_job([_job(conclusion="failure")])
        assert chosen is not None
        assert chosen["conclusion"] == "failure"

    def test_similarly_named_job_is_not_accepted(self, script):
        jobs = [_job(name="Publish Verified Dev Images / Publish Images (AMD64)")]
        assert script.select_publish_job(jobs) is None

    def test_duplicate_exact_jobs_are_rejected(self, script):
        with pytest.raises(script.CheckError, match="2 jobs"):
            script.select_publish_job([_job(), _job()])


class TestPaginatedGitHubData:
    def test_combines_all_workflow_run_pages(self, script, monkeypatch):
        calls = []

        def fake_run(cmd, *, timeout=300):
            calls.append(cmd)
            payload = [
                {"workflow_runs": [_run(10, name="Build and Push Docker Image")]},
                {"workflow_runs": [_run(11)]},
            ]
            return subprocess.CompletedProcess(
                cmd, 0, stdout="\n".join(json.dumps(page) for page in payload), stderr=""
            )

        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(script, "_run", fake_run)
        runs = script.fetch_workflow_runs("owner/repo", TEST_SHA)

        assert [run["run_number"] for run in runs] == [10, 11]
        assert "--paginate" in calls[0]
        assert "--slurp" not in calls[0]

    def test_combines_all_attempt_specific_job_pages(self, script, monkeypatch):
        calls = []

        def fake_run(cmd, *, timeout=300):
            calls.append(cmd)
            payload = [
                {"jobs": [_job(name="Backend Tests")]},
                {"jobs": [_job()]},
            ]
            return subprocess.CompletedProcess(
                cmd, 0, stdout="".join(json.dumps(page) for page in payload), stderr=""
            )

        monkeypatch.setattr(script, "_run", fake_run)
        jobs = script.fetch_workflow_jobs("owner/repo", 1234, 3)

        assert [job["name"] for job in jobs] == ["Backend Tests", _job()["name"]]
        assert "repos/owner/repo/actions/runs/1234/attempts/3/jobs?per_page=100" in calls[0]
        assert "--paginate" in calls[0]
        assert "--slurp" not in calls[0]


# --- Image config parsing ---------------------------------------------------


class TestParseImagetoolsConfig:
    def test_parses_a_multi_platform_manifest(self, script):
        payload = """
        {
          "linux/amd64": {
            "config": {"Env": ["PATH=/bin", "ECM_VERSION=0.18.1-0043",
                               "GIT_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}
          },
          "linux/arm64": {
            "config": {"Env": ["GIT_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                               "ECM_VERSION=0.18.1-0043"]}
          }
        }
        """
        env = script.parse_imagetools_config(payload)
        assert env["ECM_VERSION"] == "0.18.1-0043"
        assert env["GIT_COMMIT"] == "a" * 40

    def test_parses_a_single_platform_config(self, script):
        payload = json.dumps(
            {
                "created": "now",
                "config": {"Env": ["ECM_VERSION=0.18.1-0044", f"GIT_COMMIT={TEST_SHA}"]},
            }
        )
        assert script.parse_imagetools_config(payload)["ECM_VERSION"] == "0.18.1-0044"

    def test_env_entries_with_equals_signs_in_the_value_survive(self, script):
        payload = json.dumps(
            {
                "config": {
                    "Env": ["OPTS=a=b=c", "ECM_VERSION=0.1.0-0001", f"GIT_COMMIT={TEST_SHA}"]
                }
            }
        )
        env = script.parse_imagetools_config(payload)
        assert env["OPTS"] == "a=b=c"
        assert env["ECM_VERSION"] == "0.1.0-0001"

    @pytest.mark.parametrize("missing", ["ECM_VERSION", "GIT_COMMIT"])
    def test_rejects_a_platform_missing_either_marker(self, script, missing):
        complete = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        incomplete = complete | {missing: None}
        payload = json.dumps(
            {
                "linux/amd64": {
                    "config": {"Env": [f"{name}={value}" for name, value in complete.items()]}
                },
                "linux/arm64": {
                    "config": {
                        "Env": [
                            f"{name}={value}"
                            for name, value in incomplete.items()
                            if value is not None
                        ]
                    }
                },
            }
        )
        with pytest.raises(script.CheckError, match=missing):
            script.parse_imagetools_config(payload)

    @pytest.mark.parametrize("conflicting", ["ECM_VERSION", "GIT_COMMIT"])
    def test_rejects_conflicting_platform_markers(self, script, conflicting):
        first = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        second = first | {conflicting: "different"}
        payload = json.dumps(
            {
                platform: {
                    "config": {"Env": [f"{name}={value}" for name, value in markers.items()]}
                }
                for platform, markers in (("linux/amd64", first), ("linux/arm64", second))
            }
        )
        with pytest.raises(script.CheckError, match="disagree"):
            script.parse_imagetools_config(payload)

    def test_raises_on_unparseable_output(self, script):
        with pytest.raises(script.CheckError):
            script.parse_imagetools_config("not json")

    def test_raises_when_no_config_env_is_present(self, script):
        with pytest.raises(script.CheckError):
            script.parse_imagetools_config('{"linux/amd64": {"rootfs": {}}}')


class TestProcessTimeout:
    def test_timeout_is_reported_as_check_error(self, script, monkeypatch):
        def time_out(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        monkeypatch.setattr(script.subprocess, "run", time_out)
        with pytest.raises(script.CheckError, match="timed out"):
            script._run(["slow-command"], timeout=17)

    def test_launch_failure_is_reported_as_check_error(self, script, monkeypatch):
        def fail_to_launch(*args, **kwargs):
            raise FileNotFoundError("missing executable")

        monkeypatch.setattr(script.subprocess, "run", fail_to_launch)
        with pytest.raises(script.CheckError, match="could not launch"):
            script._run(["missing-command"])


class TestPublishedMarkerRead:
    def test_pull_adds_host_proof_after_mandatory_manifest_proof(self, script, monkeypatch):
        events = []
        marker = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            script,
            "read_marker_via_imagetools",
            lambda ref: events.append("manifest") or marker,
        )
        monkeypatch.setattr(
            script,
            "read_marker_via_pull",
            lambda ref: events.append("pull") or marker.copy(),
        )

        assert script.read_published_marker("example/image:dev", use_pull=True) == marker
        assert events == ["manifest", "pull"]

    def test_manifest_failure_cannot_be_rescued_by_pull(self, script, monkeypatch):
        pulled = False
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")

        def fail_manifest(ref):
            raise script.CheckError("manifest unavailable")

        def pull(ref):
            nonlocal pulled
            pulled = True
            return {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}

        monkeypatch.setattr(script, "read_marker_via_imagetools", fail_manifest)
        monkeypatch.setattr(script, "read_marker_via_pull", pull)

        with pytest.raises(script.CheckError, match="manifest unavailable"):
            script.read_published_marker("example/image:dev", use_pull=True)
        assert pulled is False

    @pytest.mark.parametrize("marker", ["ECM_VERSION", "GIT_COMMIT"])
    def test_pull_rejects_host_marker_mismatch(self, script, monkeypatch, marker):
        manifest = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        host = manifest | {marker: "different"}
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(script, "read_marker_via_imagetools", lambda ref: manifest)
        monkeypatch.setattr(script, "read_marker_via_pull", lambda ref: host)

        with pytest.raises(script.CheckError, match=marker):
            script.read_published_marker("example/image:dev", use_pull=True)

    def test_pull_operational_error_is_not_hidden(self, script, monkeypatch):
        marker = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(script, "read_marker_via_imagetools", lambda ref: marker)

        def fail_pull(ref):
            raise script.CheckError("docker pull failed")

        monkeypatch.setattr(script, "read_marker_via_pull", fail_pull)
        with pytest.raises(script.CheckError, match="docker pull failed"):
            script.read_published_marker("example/image:dev", use_pull=True)


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


# --- Whole-path verdicts ----------------------------------------------------


@pytest.fixture
def main_boundaries(script, monkeypatch):
    marker = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
    runs = [_run(1921)]
    jobs = [_job()]
    real_marker_reader = script.read_published_marker

    monkeypatch.setattr(script, "resolve_commit", lambda ref: TEST_SHA)
    monkeypatch.setattr(script, "commit_subject", lambda sha: "merge subject")
    monkeypatch.setattr(script, "expected_version_at", lambda sha: TEST_VERSION)
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(script, "repo_slug", lambda: "owner/repo")
    monkeypatch.setattr(script, "fetch_workflow_runs", lambda slug, sha: runs)
    monkeypatch.setattr(
        script,
        "fetch_workflow_jobs",
        lambda slug, run_id, run_attempt: jobs,
    )
    monkeypatch.setattr(
        script,
        "read_published_marker",
        lambda ref, use_pull: marker,
    )
    return {
        "marker": marker,
        "runs": runs,
        "jobs": jobs,
        "real_marker_reader": real_marker_reader,
    }


class TestMainVerdict:
    def test_success_requires_workflow_job_and_both_exact_markers(
        self, script, main_boundaries, capsys
    ):
        assert script.main(["--commit", TEST_SHA]) == 0
        output = capsys.readouterr()
        assert "PASS:" in output.out
        assert TEST_VERSION in output.out
        assert TEST_SHA[:12] in output.out

    @pytest.mark.parametrize("failure", ["timeout", "launch"], ids=["timeout", "launch"])
    def test_branch_orientation_operational_failure_is_incomplete_but_collects_evidence(
        self, script, main_boundaries, monkeypatch, capsys, failure
    ):
        def fail(*args, **kwargs):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
            raise FileNotFoundError("git unavailable")

        monkeypatch.setattr(script.subprocess, "run", fail)

        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "INCOMPLETE:" in output.err
        assert "COULD NOT CHECK branch orientation" in output.out
        assert "reusable publish job succeeded" in output.out
        assert "ECM_VERSION matches" in output.out
        assert "GIT_COMMIT matches" in output.out

    @pytest.mark.parametrize(
        ("host_marker", "host_value"),
        [("ECM_VERSION", "wrong-version"), ("GIT_COMMIT", "b" * 40)],
    )
    def test_pull_host_marker_mismatch_returns_incomplete_verdict(
        self,
        script,
        main_boundaries,
        monkeypatch,
        capsys,
        host_marker,
        host_value,
    ):
        manifest = main_boundaries["marker"]
        host = manifest | {host_marker: host_value}
        monkeypatch.setattr(
            script, "read_published_marker", main_boundaries["real_marker_reader"]
        )
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(script, "read_marker_via_imagetools", lambda ref: manifest)
        monkeypatch.setattr(script, "read_marker_via_pull", lambda ref: host)

        assert script.main(["--commit", TEST_SHA, "--pull"]) == 1
        output = capsys.readouterr()
        assert "INCOMPLETE:" in output.err
        assert host_marker in output.err

    def test_pull_operational_error_returns_incomplete_verdict(
        self, script, main_boundaries, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            script, "read_published_marker", main_boundaries["real_marker_reader"]
        )
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            script,
            "read_marker_via_imagetools",
            lambda ref: main_boundaries["marker"],
        )

        def fail_pull(ref):
            raise script.CheckError("docker pull failed")

        monkeypatch.setattr(script, "read_marker_via_pull", fail_pull)
        assert script.main(["--commit", TEST_SHA, "--pull"]) == 1
        output = capsys.readouterr()
        assert "INCOMPLETE:" in output.err
        assert "docker pull failed" in output.err

    @pytest.mark.parametrize(
        "built_from",
        [None, "unknown", "not-a-sha", TEST_SHA[:12], "b" * 40],
        ids=["missing", "unknown", "malformed", "abbreviated", "stale"],
    )
    def test_rejects_any_non_exact_git_commit_marker(
        self, script, main_boundaries, capsys, built_from
    ):
        if built_from is None:
            main_boundaries["marker"].pop("GIT_COMMIT")
        else:
            main_boundaries["marker"]["GIT_COMMIT"] = built_from

        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "GIT_COMMIT" in output.err
        assert "FAIL:" in output.err

    def test_rejects_wrong_version_marker(self, script, main_boundaries, capsys):
        main_boundaries["marker"]["ECM_VERSION"] = "0.18.1-0148"
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "ECM_VERSION" in capsys.readouterr().err

    def test_rejects_duplicate_final_publish_jobs(self, script, main_boundaries, capsys):
        main_boundaries["jobs"].append(_job())
        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "2 jobs" in output.err
        assert "INCOMPLETE:" in output.err

    @pytest.mark.parametrize(
        ("status", "conclusion"),
        [("completed", "skipped"), ("in_progress", None), ("completed", "failure")],
        ids=["skipped", "in-progress", "failed"],
    )
    def test_rejects_non_successful_publish_jobs(
        self, script, main_boundaries, capsys, status, conclusion
    ):
        main_boundaries["jobs"][0].update(status=status, conclusion=conclusion)
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "FAIL:" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("status", "conclusion"),
        [("in_progress", None), ("completed", "failure")],
        ids=["in-progress", "failed"],
    )
    def test_rejects_non_successful_tests_runs(
        self, script, main_boundaries, capsys, status, conclusion
    ):
        main_boundaries["runs"][0].update(status=status, conclusion=conclusion)
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "FAIL:" in capsys.readouterr().err

    @pytest.mark.parametrize("message", ["GitHub API denied", "command timed out after 300s"])
    def test_api_and_timeout_errors_return_normal_incomplete_verdicts(
        self, script, main_boundaries, monkeypatch, capsys, message
    ):
        def fail(slug, sha):
            raise script.CheckError(message)

        monkeypatch.setattr(script, "fetch_workflow_runs", fail)
        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "INCOMPLETE:" in output.err
        assert message in output.err

    def test_success_can_come_from_later_paginated_results(
        self, script, main_boundaries, capsys
    ):
        main_boundaries["runs"][:] = [
            _run(1920, name="Build and Push Docker Image"),
            _run(1921),
        ]
        main_boundaries["jobs"][:] = [_job(name="Backend Tests"), _job()]
        assert script.main(["--commit", TEST_SHA]) == 0
        assert "PASS:" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "linux/amd64": {
                    "config": {
                        "Env": [f"ECM_VERSION={TEST_VERSION}", f"GIT_COMMIT={TEST_SHA}"]
                    }
                },
                "linux/arm64": {
                    "config": {
                        "Env": [f"ECM_VERSION={TEST_VERSION}", "GIT_COMMIT=" + "b" * 40]
                    }
                },
            },
            {
                "linux/amd64": {
                    "config": {
                        "Env": [f"ECM_VERSION={TEST_VERSION}", f"GIT_COMMIT={TEST_SHA}"]
                    }
                },
                "linux/arm64": {"config": {"Env": [f"ECM_VERSION={TEST_VERSION}"]}},
            },
        ],
        ids=["conflicting-platform-marker", "missing-platform-marker"],
    )
    def test_platform_marker_errors_return_normal_incomplete_verdicts(
        self, script, main_boundaries, monkeypatch, capsys, payload
    ):
        def read_marker(ref, use_pull):
            return script.parse_imagetools_config(json.dumps(payload))

        monkeypatch.setattr(script, "read_published_marker", read_marker)
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "INCOMPLETE:" in capsys.readouterr().err
