"""Repository-state tests for the version-advance PreToolUse hook.

The outer hook matcher is deliberately broad. Inside the hook, one monotonic
raw-literal candidate test looks for whitespace-separated ``gh pr create``
anywhere in the command text. It does not interpret quotes, heredocs, command
substitutions, or command positions. A literal in inert prose may therefore run
the repository-state check; no parser may narrow a candidate and silently turn
the guard off.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = Path(
    os.environ.get(
        "ECM_VERSION_GUARD_HOOK_UNDER_TEST",
        REPO_ROOT / ".claude" / "hooks" / "version-advance-guard.sh",
    )
)
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
BASE_VERSION = "0.18.1-0053"
BUMPED_VERSION = "0.18.1-0054"
ALLOW = 0
BLOCK = 2


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package(version: str) -> str:
    return json.dumps({"name": "ecm-frontend", "version": version}) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "ecm"
    root.mkdir()
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "config", "user.email", "guard@example.invalid")
    _git(root, "config", "user.name", "Guard Test")
    (root / "scripts").mkdir()
    for name in ("check_version_advances.py", "classify_changed_paths.py"):
        shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)
    _write(root, "frontend/package.json", _package(BASE_VERSION))
    _write(root, "backend/app.py", "VALUE = 1\n")
    _write(root, "docs/guide.md", "Baseline.\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "baseline")
    _git(root, "update-ref", "refs/remotes/origin/dev", "HEAD")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


def _commit(repo: Path, files: dict[str, str], version: str | None = None) -> None:
    if version is not None:
        _write(repo, "frontend/package.json", _package(version))
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def _invoke(
    repo: Path,
    cwd: Path | str | None = None,
    command: str = "gh pr create --base dev",
) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        {
            "cwd": str(repo if cwd is None else cwd),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT / "not-the-target-checkout")
    env.pop("GITHUB_ACTIONS", None)
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=120,
        check=False,
    )


def _delivered(result: subprocess.CompletedProcess[str]) -> str:
    payload = json.loads(result.stdout)
    assert payload["systemMessage"] == payload["hookSpecificOutput"][
        "additionalContext"
    ]
    return payload["systemMessage"]


def test_settings_route_all_bash_commands_to_the_raw_candidate_check() -> None:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    handlers = settings["hooks"]["PreToolUse"][0]["hooks"]
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert len(handlers) == 1
    assert "if" not in handlers[0]


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --base dev",
        "git push\ngh pr create --base dev",
        "git push;gh pr create --base dev",
        "(gh pr create --base dev)",
        "URL=$(gh pr create --base dev)",
        'URL="$(gh pr create --base dev)"',
        "echo `gh pr create --base dev`",
        "git commit -F - <<EOF\n$(gh pr create --base dev)\nEOF",
        "echo 'an apostrophe does not narrow; gh pr create --base dev'",
        "git commit -F - <<'EOF'\nprose: gh pr create --base dev\nEOF",
        "echo 'prose only: gh pr create --base dev'",
        "(gh pr create)",
        "URL=$(gh pr create)",
        "gh pr create; echo done",
        "gh pr create&&echo done",
        "gh pr create>pr-url.txt",
    ],
)
def test_any_raw_literal_candidate_runs_the_state_check(
    repo: Path, command: str
) -> None:
    """Executable and inert forms deliberately receive the same treatment."""
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo, command=command)
    assert result.returncode == BLOCK, command + result.stdout + result.stderr
    assert "possible PR creation command" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "echo gh-pr-create",
        "echo 'gh pr list'",
        "echo 'gh  pr'",
        "echo 'gh pr createfoo'",
    ],
)
def test_candidate_absence_allows_silently(repo: Path, command: str) -> None:
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo, command=command)
    assert result.returncode == ALLOW
    assert result.stdout == ""
    assert result.stderr == ""


def test_inline_cd_does_not_change_the_pre_execution_cwd(repo: Path) -> None:
    """The literal routes, but state is checked in the supplied session cwd."""
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo, command="cd /somewhere/else && gh pr create --base dev")
    assert result.returncode == BLOCK


def test_code_change_without_bump_blocks(repo: Path) -> None:
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo)
    assert result.returncode == BLOCK
    assert result.stdout == ""
    assert "BLOCKED by version-advance-guard" in result.stderr


def test_code_change_with_bump_passes_and_announces(repo: Path) -> None:
    _commit(repo, {"backend/app.py": "VALUE = 2\n"}, BUMPED_VERSION)
    result = _invoke(repo)
    assert result.returncode == ALLOW, result.stderr
    assert "PASSED" in _delivered(result)


@pytest.mark.parametrize("path", ["docs/guide.md", ".beads/issues.jsonl"])
def test_documentation_change_without_bump_skips(repo: Path, path: str) -> None:
    _commit(repo, {path: "Changed.\n"})
    result = _invoke(repo)
    assert result.returncode == ALLOW, result.stderr
    delivered = _delivered(result)
    assert "SKIPPED" in delivered
    assert "docs_only=true" in delivered


def test_rename_from_code_to_markdown_still_blocks(repo: Path) -> None:
    _git(repo, "mv", "backend/app.py", "docs/archived.md")
    _git(repo, "commit", "-q", "-m", "rename")
    result = _invoke(repo)
    assert result.returncode == BLOCK, result.stdout + result.stderr


def test_cwd_is_resolved_to_the_containing_worktree(repo: Path) -> None:
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    nested = repo / "backend" / "nested"
    nested.mkdir()
    result = _invoke(repo, cwd=nested)
    assert result.returncode == BLOCK


@pytest.mark.parametrize("cwd", ["", "/definitely/not/a/git/checkout"])
def test_missing_or_non_git_cwd_fails_open_loudly(repo: Path, cwd: str) -> None:
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo, cwd=cwd)
    assert result.returncode == ALLOW
    assert "WARNING" in _delivered(result)


def test_classifier_failure_fails_open_loudly(repo: Path) -> None:
    (repo / "scripts/classify_changed_paths.py").write_text("def (\n")
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo)
    assert result.returncode == ALLOW
    assert "WARNING" in _delivered(result)


def test_duplicate_docs_only_keys_fail_open(repo: Path) -> None:
    (repo / "scripts/classify_changed_paths.py").write_text(
        "print('docs_only=true')\nprint('docs_only=false')\n", encoding="utf-8"
    )
    _commit(repo, {"backend/app.py": "VALUE = 2\n"})
    result = _invoke(repo)
    assert result.returncode == ALLOW
    assert "no unique docs_only key" in _delivered(result)


def test_release_version_without_build_suffix_passes_via_checker(repo: Path) -> None:
    """No command-text `--base main` exemption is needed or permitted."""
    _commit(repo, {"backend/app.py": "VALUE = 2\n"}, "0.18.2")
    result = _invoke(repo)
    assert result.returncode == ALLOW, result.stderr
    assert "PASSED" in _delivered(result)


def test_shell_metacharacters_in_changed_path_are_data(repo: Path, tmp_path: Path) -> None:
    canary = tmp_path / "canary"
    _commit(repo, {f"backend/a; touch {canary}; b.py": "VALUE = 2\n"})
    result = _invoke(repo)
    assert not canary.exists()
    assert result.returncode == BLOCK
