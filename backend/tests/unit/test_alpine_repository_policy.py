"""Stable Alpine selection must reject rolling, mixed and substituted feeds."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "backend/tests/fixtures/alpine/repository-policy.json"


@pytest.fixture
def policy():
    path = ROOT / "scripts/alpine_repository_policy.py"
    spec = importlib.util.spec_from_file_location("alpine_repository_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded():
    return json.loads(FIXTURE.read_text())


def test_actual_mcp_release_and_repository_files_are_supported(policy, recorded):
    result = policy.validate_repositories(
        recorded["alpine_release"], recorded["repositories"]
    )
    assert result["release"] == "3.24.4"
    assert result["repositories"] == recorded["repositories"].splitlines()
    assert result["policy_branch"] == "v3.24"
    assert "not immutable" in result["scope"]


@pytest.mark.parametrize(
    "release",
    [
        "edge\n",
        "3.25.0_alpha20261108\n",
        "3.24.0_rc1\n",
        "3.23.4\n",
        "3.25.0\n",
        "3.24\n",
        "3.24.4\nedge\n",
        "",
        "3.24.4\x00",
    ],
)
def test_unreviewed_or_nonstable_release_is_refused(policy, recorded, release):
    with pytest.raises(policy.AlpinePolicyError, match="release"):
        policy.validate_repositories(release, recorded["repositories"])


@pytest.mark.parametrize(
    "before,after",
    [
        ("v3.24/main", "edge/main"),
        ("v3.24/community", "edge/testing"),
        ("v3.24/community", "v3.23/community"),
        ("v3.24/main", "v3.25/main"),
        ("https:", "http:"),
        ("dl-cdn.alpinelinux.org", "unapproved.example"),
        ("/main", "/main?fallback=edge"),
        ("https://", "https://user@"),
        ("/community", "/testing"),
    ],
)
def test_rolling_mixed_or_substituted_repositories_are_refused(
    policy, recorded, before, after
):
    mutant = recorded["repositories"].replace(before, after)
    assert mutant != recorded["repositories"]
    with pytest.raises(policy.AlpinePolicyError, match="repositories"):
        policy.validate_repositories(recorded["alpine_release"], mutant)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "third", "tagged"])
def test_repository_set_is_exact_complete_and_untagged(policy, recorded, mutation):
    rows = recorded["repositories"].splitlines()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "third":
        rows.append("https://dl-cdn.alpinelinux.org/alpine/edge/testing")
    else:
        rows[1] = "@extra " + rows[1]
    with pytest.raises(policy.AlpinePolicyError, match="repositories"):
        policy.validate_repositories(recorded["alpine_release"], "\n".join(rows))


def test_empty_lines_and_full_line_comments_do_not_enable_other_feeds(policy, recorded):
    text = "# edge is forbidden, not enabled\n\n" + recorded["repositories"]
    assert (
        policy.validate_repositories(recorded["alpine_release"], text)["policy_branch"]
        == "v3.24"
    )


def test_cli_reads_actual_files_and_reports_limited_scope(
    policy, recorded, tmp_path, capsys
):
    release = tmp_path / "alpine-release"
    repositories = tmp_path / "repositories"
    release.write_text(recorded["alpine_release"])
    repositories.write_text(recorded["repositories"])
    args = ["--release-file", str(release), "--repositories", str(repositories)]
    assert policy.main(args) == 0
    assert json.loads(capsys.readouterr().out)["policy_branch"] == "v3.24"
    repositories.write_text(recorded["repositories"].replace("v3.24/main", "edge/main"))
    assert policy.main(args) == 1
    assert "repositories" in capsys.readouterr().err


def test_cli_missing_input_fails_closed(policy, tmp_path, capsys):
    assert policy.main(["--release-file", str(tmp_path / "missing")]) == 1
    assert capsys.readouterr().err
