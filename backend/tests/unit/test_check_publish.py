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
TEST_VERSION = "0.18.2-0001"


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
        "id": 33440983429,
        "name": name or "Tests",
        "head_sha": TEST_SHA,
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
        chosen = script.select_build_run(runs, "Tests", "dev", TEST_SHA)
        assert chosen is not None and chosen["run_number"] == 10

    def test_ignores_pull_request_runs_for_the_same_sha(self, script):
        """A PR run and a branch-push run share a head SHA. Only the push
        publishes, so a green PR run must never stand in for a failed push.
        """
        runs = [
            _run(11, event="pull_request", branch="feature", conclusion="success"),
            _run(12, event="push", conclusion="failure"),
        ]
        chosen = script.select_build_run(runs, "Tests", "dev", TEST_SHA)
        assert chosen is not None
        assert chosen["run_number"] == 12
        assert chosen["conclusion"] == "failure"

    def test_ignores_other_workflows(self, script):
        runs = [_run(13, name="Build and Push Docker Image")]
        assert script.select_build_run(runs, "Tests", "dev", TEST_SHA) is None

    def test_ignores_runs_on_another_branch(self, script):
        runs = [_run(14, branch="main")]
        assert script.select_build_run(runs, "Tests", "dev", TEST_SHA) is None

    def test_ignores_api_result_for_a_different_sha(self, script):
        runs = [_run(15) | {"head_sha": "b" * 40}]
        assert script.select_build_run(runs, "Tests", "dev", TEST_SHA) is None

    def test_prefers_the_latest_attempt_of_a_rerun(self, script):
        """A re-run that fixes a flake is the state of record, not the
        original failure that motivated it.
        """
        runs = [
            _run(20, attempt=1, conclusion="failure"),
            _run(20, attempt=2, conclusion="success"),
        ]
        chosen = script.select_build_run(runs, "Tests", "dev", TEST_SHA)
        assert chosen is not None and chosen["run_attempt"] == 2

    def test_returns_none_for_an_empty_list(self, script):
        assert script.select_build_run([], "Tests", "dev", TEST_SHA) is None

    @pytest.mark.parametrize("field", ["id", "run_number", "run_attempt"])
    @pytest.mark.parametrize(
        "value",
        [None, 0, -1, True, "1", 1.5],
        ids=["missing", "zero", "negative", "bool", "string", "float"],
    )
    def test_rejects_non_positive_integer_candidate_metadata(
        self, script, field, value
    ):
        run = _run(21)
        run[field] = value
        with pytest.raises(script.CheckError, match=rf"{field}.*positive integer"):
            script.select_build_run([run], "Tests", "dev", TEST_SHA)

    def test_mixed_candidate_metadata_never_raises_raw_type_error(self, script):
        runs = [_run(22), _run(23) | {"run_number": "23"}]
        with pytest.raises(script.CheckError, match="run_number.*positive integer"):
            script.select_build_run(runs, "Tests", "dev", TEST_SHA)

    def test_malformed_unrelated_run_is_ignored(self, script):
        runs = [_run(24), _run(25, name="Other") | {"run_number": "bad"}]
        assert script.select_build_run(runs, "Tests", "dev", TEST_SHA) == runs[0]


class TestSelectPublishJob:
    def test_selects_live_0001_nested_manifest_job(self, script):
        jobs = [
            _job(name="Publish Verified Dev Images / Authorize Exact-SHA Publication"),
            _job(name="Publish Verified Dev Images / Publish Images (AMD64)"),
            _job(name="Publish Verified Dev Images / Publish Images (ARM64)"),
            _job(),
        ]
        assert script.select_publish_job(jobs) == jobs[-1]

    def test_rejects_missing_or_similarly_named_job(self, script):
        jobs = [_job(name="Publish Verified Dev Images / Publish Images (AMD64)")]
        assert script.select_publish_job(jobs) is None

    def test_rejects_duplicate_exact_jobs(self, script):
        with pytest.raises(script.CheckError, match="2 jobs"):
            script.select_publish_job([_job(), _job()])


class TestPaginatedGitHubData:
    @pytest.mark.parametrize(
        ("fetch", "key", "endpoint"),
        [
            ("runs", "workflow_runs", "actions/runs?head_sha=" + TEST_SHA),
            ("jobs", "jobs", "actions/runs/123/attempts/4/jobs"),
        ],
    )
    def test_combines_every_page(self, script, monkeypatch, fetch, key, endpoint):
        calls = []
        first = _run(10, name="Other") if key == "workflow_runs" else _job(name="Other")
        second = _run(11) if key == "workflow_runs" else _job()

        def fake_run(cmd, *, timeout=300):
            calls.append(cmd)
            stdout = json.dumps({key: [first]}) + "\n" + json.dumps({key: [second]})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(script, "_run", fake_run)
        if fetch == "runs":
            result = script.fetch_workflow_runs("owner/repo", TEST_SHA)
        else:
            result = script.fetch_workflow_jobs("owner/repo", 123, 4)

        assert result == [first, second]
        assert "--paginate" in calls[0]
        assert any(endpoint in part for part in calls[0])

    @pytest.mark.parametrize(
        "payload",
        ["not-json", "[]", '{"workflow_runs": {}}', '{"workflow_runs": [null]}'],
    )
    def test_malformed_run_pagination_fails_closed(self, script, monkeypatch, payload):
        monkeypatch.setattr(script.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(
            script,
            "_run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(
                cmd, 0, stdout=payload, stderr=""
            ),
        )
        with pytest.raises(script.CheckError, match="gh api|paginated|workflow_runs"):
            script.fetch_workflow_runs("owner/repo", TEST_SHA)

    def test_malformed_attempt_job_pagination_fails_closed(self, script, monkeypatch):
        monkeypatch.setattr(
            script,
            "_run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(
                cmd, 0, stdout='{"jobs": [null]}', stderr=""
            ),
        )
        with pytest.raises(script.CheckError, match="jobs item 1 is not an object"):
            script.fetch_workflow_jobs("owner/repo", 123, 4)


# --- Image config parsing ---------------------------------------------------


class TestParseImagetoolsConfig:
    """Manifest invariant: unique object keys; exact platforms; object platform
    and config values; string-list Env; one of each marker; platform agreement.

    Version and full lowercase SHA equality are enforced by the verdict layer.
    Every malformed external shape must become an actionable CheckError.
    """

    def test_parses_a_multi_platform_manifest(self, script):
        payload = """
        {
          "linux/amd64": {
            "config": {"Env": ["PATH=/bin", "ECM_VERSION=0.18.1-0043",
                               "GIT_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}
          },
          "linux/arm64": {
            "config": {"Env": ["ECM_VERSION=0.18.1-0043",
                               "GIT_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}
          }
        }
        """
        env = script.parse_imagetools_config(payload)
        assert env["ECM_VERSION"] == "0.18.1-0043"
        assert env["GIT_COMMIT"] == TEST_SHA
        assert env[script.PLATFORMS_KEY] == "linux/amd64, linux/arm64"

    def test_rejects_a_single_platform_config(self, script):
        payload = json.dumps(
            {"config": {"Env": ["ECM_VERSION=0.18.1-0044", f"GIT_COMMIT={TEST_SHA}"]}}
        )
        with pytest.raises(script.CheckError, match="linux/amd64.*linux/arm64"):
            script.parse_imagetools_config(payload)

    def test_env_entries_with_equals_signs_in_the_value_survive(self, script):
        payload = json.dumps(
            {
                platform: {
                    "config": {
                        "Env": [
                            "OPTS=a=b=c",
                            "ECM_VERSION=0.1.0-0001",
                            f"GIT_COMMIT={TEST_SHA}",
                        ]
                    }
                }
                for platform in ("linux/amd64", "linux/arm64")
            }
        )
        env = script.parse_imagetools_config(payload)
        assert env["OPTS"] == "a=b=c"
        assert env["ECM_VERSION"] == "0.1.0-0001"

    def test_raises_on_unparseable_output(self, script):
        with pytest.raises(script.CheckError):
            script.parse_imagetools_config("not json")

    def test_raises_when_no_config_env_is_present(self, script):
        payload = {
            platform: {
                "config": (
                    {}
                    if platform == "linux/amd64"
                    else {
                        "Env": [
                            f"ECM_VERSION={TEST_VERSION}",
                            f"GIT_COMMIT={TEST_SHA}",
                        ]
                    }
                )
            }
            for platform in ("linux/amd64", "linux/arm64")
        }
        with pytest.raises(script.CheckError, match="env block for linux/amd64"):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize(
        ("platform_value", "message"),
        [
            ({}, "linux/amd64.*config field"),
            ({"config": None}, "linux/amd64.*config.*object"),
            ({"config": []}, "linux/amd64.*config.*object"),
            ({"config": "invalid"}, "linux/amd64.*config.*object"),
            ({"config": 17}, "linux/amd64.*config.*object"),
        ],
        ids=["absent", "null", "list", "string", "scalar"],
    )
    def test_rejects_malformed_config_with_exact_platform_set(
        self, script, platform_value, message
    ):
        valid = {
            "config": {
                "Env": [
                    f"ECM_VERSION={TEST_VERSION}",
                    f"GIT_COMMIT={TEST_SHA}",
                ]
            }
        }
        payload = {"linux/amd64": platform_value, "linux/arm64": valid}
        with pytest.raises(script.CheckError, match=message):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize("platform_value", [None, [], "invalid", 17])
    def test_rejects_non_object_platform_value(self, script, platform_value):
        payload = {
            "linux/amd64": platform_value,
            "linux/arm64": {
                "config": {
                    "Env": [
                        f"ECM_VERSION={TEST_VERSION}",
                        f"GIT_COMMIT={TEST_SHA}",
                    ]
                }
            },
        }
        with pytest.raises(
            script.CheckError, match="platform linux/amd64 value.*object"
        ):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize("env", [None, "invalid", ["ECM_VERSION=valid", 17]])
    def test_rejects_env_that_is_not_a_list_of_strings(self, script, env):
        payload = {
            platform: {
                "config": {
                    "Env": (
                        env
                        if platform == "linux/amd64"
                        else [
                            f"ECM_VERSION={TEST_VERSION}",
                            f"GIT_COMMIT={TEST_SHA}",
                        ]
                    )
                }
            }
            for platform in ("linux/amd64", "linux/arm64")
        }
        with pytest.raises(
            script.CheckError, match="env block for linux/amd64.*list of strings"
        ):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize("marker", ["ECM_VERSION", "GIT_COMMIT"])
    def test_rejects_marker_missing_from_any_platform(self, script, marker):
        complete = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        incomplete = {key: value for key, value in complete.items() if key != marker}
        payload = {
            "linux/amd64": {
                "config": {"Env": [f"{k}={v}" for k, v in complete.items()]}
            },
            "linux/arm64": {
                "config": {"Env": [f"{k}={v}" for k, v in incomplete.items()]}
            },
        }
        with pytest.raises(script.CheckError, match=marker):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize("marker", ["ECM_VERSION", "GIT_COMMIT"])
    def test_rejects_platform_marker_disagreement(self, script, marker):
        first = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        second = first | {marker: "different"}
        payload = {
            platform: {"config": {"Env": [f"{k}={v}" for k, v in markers.items()]}}
            for platform, markers in (("linux/amd64", first), ("linux/arm64", second))
        }
        with pytest.raises(script.CheckError, match="disagree"):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize("marker", ["ECM_VERSION", "GIT_COMMIT"])
    @pytest.mark.parametrize("duplicate_value", ["same", "different"])
    def test_rejects_duplicate_provenance_marker_per_platform(
        self, script, marker, duplicate_value
    ):
        values = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        duplicate = values[marker] if duplicate_value == "same" else "conflict"
        payload = {
            platform: {
                "config": {
                    "Env": [
                        f"ECM_VERSION={TEST_VERSION}",
                        f"GIT_COMMIT={TEST_SHA}",
                        f"{marker}={duplicate}",
                    ]
                }
            }
            for platform in ("linux/amd64", "linux/arm64")
        }
        with pytest.raises(
            script.CheckError, match=rf"linux/amd64.*duplicate {marker}"
        ):
            script.parse_imagetools_config(json.dumps(payload))

    @pytest.mark.parametrize(
        "platforms",
        [
            ["linux/amd64"],
            ["linux/amd64", "linux/s390x"],
            ["linux/amd64", "linux/arm64", "linux/s390x"],
            ["linux/amd64/v8", "linux/arm64"],
        ],
        ids=[
            "missing-arm64",
            "wrong-platform",
            "unexpected-platform",
            "malformed-platform",
        ],
    )
    def test_requires_exact_ecm_platform_set(self, script, platforms):
        payload = {
            platform: {
                "config": {
                    "Env": [f"ECM_VERSION={TEST_VERSION}", f"GIT_COMMIT={TEST_SHA}"]
                }
            }
            for platform in platforms
        }
        with pytest.raises(
            script.CheckError, match="expected.*linux/amd64.*linux/arm64"
        ):
            script.parse_imagetools_config(json.dumps(payload))

    def test_rejects_duplicate_platform_key_before_json_collapse(self, script):
        config = json.dumps(
            {
                "config": {
                    "Env": [f"ECM_VERSION={TEST_VERSION}", f"GIT_COMMIT={TEST_SHA}"]
                }
            }
        )
        payload = (
            f'{{"linux/amd64":{config},"linux/amd64":{config},"linux/arm64":{config}}}'
        )
        with pytest.raises(
            script.CheckError, match="duplicate JSON object key.*linux/amd64"
        ):
            script.parse_imagetools_config(payload)


class TestProcessFailures:
    def test_timeout_becomes_actionable_check_error(self, script, monkeypatch):
        monkeypatch.setattr(
            script.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(args[0], kwargs["timeout"])
            ),
        )
        with pytest.raises(script.CheckError, match="timed out.*17"):
            script._run(["slow"], timeout=17)

    def test_launch_failure_becomes_actionable_check_error(self, script, monkeypatch):
        monkeypatch.setattr(
            script.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")),
        )
        with pytest.raises(script.CheckError, match="could not launch"):
            script._run(["missing"])


class TestPublishedMarkerRead:
    def test_pull_is_additive_after_mandatory_manifest_proof(self, script, monkeypatch):
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
        assert (
            script.read_published_marker("example/image:dev", use_pull=True) == marker
        )
        assert events == ["manifest", "pull"]

    def test_pull_cannot_rescue_failed_manifest_proof(self, script, monkeypatch):
        pulled = []
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            script,
            "read_marker_via_imagetools",
            lambda ref: (_ for _ in ()).throw(script.CheckError("manifest failed")),
        )
        monkeypatch.setattr(
            script, "read_marker_via_pull", lambda ref: pulled.append(ref)
        )
        with pytest.raises(script.CheckError, match="manifest failed"):
            script.read_published_marker("example/image:dev", use_pull=True)
        assert pulled == []

    @pytest.mark.parametrize("marker", ["ECM_VERSION", "GIT_COMMIT"])
    def test_pull_markers_must_match_manifest(self, script, monkeypatch, marker):
        manifest = {"ECM_VERSION": TEST_VERSION, "GIT_COMMIT": TEST_SHA}
        host = manifest | {marker: "different"}
        monkeypatch.setattr(script.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(script, "read_marker_via_imagetools", lambda ref: manifest)
        monkeypatch.setattr(script, "read_marker_via_pull", lambda ref: host)
        with pytest.raises(script.CheckError, match=marker):
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
        assert (
            (repo.root / "frontend" / "package.json").read_text().count("0.0.0-dirty")
        )
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
        (repo.root / "frontend" / "package.json").write_text(
            "{not json", encoding="utf-8"
        )
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

    def test_rev_parse_operational_error_is_not_a_missing_ref(
        self, script, monkeypatch
    ):
        monkeypatch.setattr(
            script,
            "_run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(
                cmd, 128, stdout="", stderr="fatal: corrupt ref database"
            ),
        )
        with pytest.raises(script.CheckError, match="rev-parse.*corrupt ref database"):
            script.commit_is_on_branch(TEST_SHA, "dev")

    def test_merge_base_operational_error_is_reported(self, script, monkeypatch):
        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=TEST_SHA, stderr="")
            return subprocess.CompletedProcess(
                cmd, 2, stdout="", stderr="fatal: invalid commit graph"
            )

        monkeypatch.setattr(script, "_run", fake_run)
        with pytest.raises(script.CheckError, match="merge-base.*invalid commit graph"):
            script.commit_is_on_branch(TEST_SHA, "dev")


class TestRepoSlug:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/MotWakorb/enhancedchannelmanager.git",
            "ssh://git@github.com/MotWakorb/enhancedchannelmanager.git",
            "git@github.com:MotWakorb/enhancedchannelmanager.git",
        ],
    )
    def test_derives_slug_from_supported_github_origin(self, script, monkeypatch, url):
        monkeypatch.setattr(script, "_git", lambda *args: url + "\n")
        monkeypatch.setattr(
            script,
            "_run",
            lambda *args, **kwargs: pytest.fail(
                "repo_slug must not call gh or cwd-sensitive commands"
            ),
        )
        assert script.repo_slug() == "MotWakorb/enhancedchannelmanager"

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/MotWakorb/enhancedchannelmanager.git",
            "git@github.com:owner.git",
            "https://github.com/owner/repo/extra",
            "https://github.com/owner/repo.git?ref=main",
            "/local/repository",
            "https://github.com/bad owner/repo.git",
        ],
    )
    def test_rejects_non_github_or_malformed_origin(self, script, monkeypatch, url):
        monkeypatch.setattr(script, "_git", lambda *args: url + "\n")
        with pytest.raises(script.CheckError, match="GitHub origin|owner/repo"):
            script.repo_slug()


@pytest.fixture
def main_boundaries(script, monkeypatch):
    runs = [_run(1921)]
    jobs = [_job()]
    marker = {
        "ECM_VERSION": TEST_VERSION,
        "GIT_COMMIT": TEST_SHA,
        script.PLATFORMS_KEY: "linux/amd64, linux/arm64",
    }
    calls = []
    monkeypatch.setattr(
        script, "resolve_commit", lambda ref: calls.append("resolve") or TEST_SHA
    )
    monkeypatch.setattr(
        script, "commit_subject", lambda sha: calls.append("subject") or "merge subject"
    )
    monkeypatch.setattr(
        script,
        "expected_version_at",
        lambda sha: calls.append("version") or TEST_VERSION,
    )
    monkeypatch.setattr(
        script,
        "commit_is_on_branch",
        lambda sha, branch: calls.append("orientation") or True,
    )
    monkeypatch.setattr(
        script, "repo_slug", lambda: calls.append("slug") or "owner/repo"
    )
    monkeypatch.setattr(
        script,
        "fetch_workflow_runs",
        lambda slug, sha: calls.append("runs") or runs,
    )

    def fetch_jobs(slug, run_id, run_attempt):
        calls.append(("jobs", slug, run_id, run_attempt))
        return jobs

    monkeypatch.setattr(script, "fetch_workflow_jobs", fetch_jobs)
    monkeypatch.setattr(
        script,
        "read_published_marker",
        lambda ref, use_pull: calls.append(("image", use_pull)) or marker,
    )
    return {"runs": runs, "jobs": jobs, "marker": marker, "calls": calls}


class TestMainVerdict:
    def test_live_0001_topology_passes_exact_attempt_and_markers(
        self, script, main_boundaries, capsys
    ):
        assert script.main(["--commit", TEST_SHA, "--pull"]) == 0
        output = capsys.readouterr().out
        assert ("jobs", "owner/repo", 33440983429, 1) in main_boundaries["calls"]
        assert ("image", True) in main_boundaries["calls"]
        assert "attempt 1" in output
        assert "reusable publish job succeeded" in output
        assert "linux/amd64, linux/arm64" in output
        assert "GIT_COMMIT matches the exact resolved SHA" in output
        assert "PASS:" in output
        assert "current mutable tag" in output
        assert "does not bind image bytes" in output
        assert "built from a successful" not in output

    @pytest.mark.parametrize(
        ("args", "result", "workflow_runs", "image_runs", "verdict"),
        [
            ([], 0, True, True, "current mutable tag"),
            (["--skip-workflow"], 0, False, True, "workflow evidence skipped"),
            (["--skip-image"], 0, True, False, "mutable-tag marker check skipped"),
            (
                ["--pull", "--skip-image"],
                0,
                True,
                False,
                "mutable-tag marker check skipped",
            ),
            (
                ["--skip-workflow", "--skip-image"],
                2,
                False,
                False,
                "cannot be used together",
            ),
        ],
        ids=[
            "default",
            "workflow-only-skip",
            "image-only-skip",
            "pull-with-image-skip",
            "both-skipped",
        ],
    )
    def test_skip_modes_run_only_truthfully_claimed_boundaries(
        self,
        script,
        main_boundaries,
        capsys,
        args,
        result,
        workflow_runs,
        image_runs,
        verdict,
    ):
        assert script.main(["--commit", TEST_SHA, *args]) == result
        calls = main_boundaries["calls"]
        output = capsys.readouterr()
        assert ("runs" in calls) is workflow_runs
        assert (
            any(isinstance(call, tuple) and call[0] == "image" for call in calls)
            is image_runs
        )
        assert verdict in output.out + output.err
        if result == 2:
            assert calls == []

    @pytest.mark.parametrize(
        ("status", "conclusion"),
        [("in_progress", None), ("completed", "failure")],
    )
    def test_tests_run_must_be_completed_successfully(
        self, script, main_boundaries, capsys, status, conclusion
    ):
        main_boundaries["runs"][0].update(status=status, conclusion=conclusion)
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "FAIL:" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("status", "conclusion"),
        [("in_progress", None), ("completed", "failure"), ("completed", "skipped")],
    )
    def test_nested_manifest_job_must_be_completed_successfully(
        self, script, main_boundaries, capsys, status, conclusion
    ):
        main_boundaries["jobs"][0].update(status=status, conclusion=conclusion)
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "FAIL:" in capsys.readouterr().err

    def test_missing_nested_manifest_job_fails(self, script, main_boundaries, capsys):
        main_boundaries["jobs"].clear()
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "no 'Publish Verified Dev Images" in capsys.readouterr().err

    def test_duplicate_nested_manifest_job_is_incomplete(
        self, script, main_boundaries, capsys
    ):
        main_boundaries["jobs"].append(_job())
        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "2 jobs" in output.err
        assert "INCOMPLETE:" in output.err

    @pytest.mark.parametrize(
        "built_from",
        [None, "unknown", TEST_SHA[:12], "A" * 40, "b" * 40],
        ids=["missing", "malformed", "abbreviated", "uppercase", "stale"],
    )
    def test_git_commit_marker_must_be_exact_full_lowercase_sha(
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

    def test_version_marker_must_match_target_commit(
        self, script, main_boundaries, capsys
    ):
        main_boundaries["marker"]["ECM_VERSION"] = "0.18.2-0000"
        assert script.main(["--commit", TEST_SHA]) == 1
        assert "ECM_VERSION mismatch" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "boundary",
        ["fetch_workflow_runs", "fetch_workflow_jobs", "read_published_marker"],
    )
    def test_api_or_manifest_error_returns_incomplete(
        self, script, main_boundaries, monkeypatch, capsys, boundary
    ):
        monkeypatch.setattr(
            script,
            boundary,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                script.CheckError("actionable boundary failure")
            ),
        )
        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "INCOMPLETE:" in output.err
        assert "actionable boundary failure" in output.err

    def test_git_orientation_error_is_incomplete_but_other_proof_runs(
        self, script, main_boundaries, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            script,
            "commit_is_on_branch",
            lambda *args: (_ for _ in ()).throw(script.CheckError("git timed out")),
        )
        assert script.main(["--commit", TEST_SHA]) == 1
        output = capsys.readouterr()
        assert "COULD NOT CHECK branch orientation" in output.out
        assert "reusable publish job succeeded" in output.out
        assert "INCOMPLETE:" in output.err
