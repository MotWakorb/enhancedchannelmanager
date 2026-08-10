"""Fixture-based self-test for ``.claude/hooks/version-advance-guard.sh``.

Bead enhancedchannelmanager-uf2gh (the documentation-only exemption; see the
provenance chain in the hook's own header for beads w9irb and yxtuz).

This is the first pytest harness for a ``.claude/hooks/*.sh`` wrapper. Bead
enhancedchannelmanager-yxtuz closed with exactly this as a backlog candidate:
"No pytest harness exists for .claude/hooks/*.sh (only
scripts/check_version_advances.py has unit tests, which don't cover the shell
wrapper) [...] add a lightweight fixture-based self-test for the hook per
engineering-discipline's 'Enforcement Code Tests Itself' principle, mirroring
backend/tests/unit/test_check_version_advances.py's approach but invoking the
.sh directly."

## What is under test

The hook is enforcement code: it returns an allow/deny decision on a ship
action, so the interesting assertions are about the *deny* side staying intact
while the new documentation-only exemption is carved out of the *allow* side.

The exemption exists because the hook is the agent-side half of the CI
build-advance gate, and CI skips that gate on a documentation-only PR
(``.github/workflows/test.yml`` gates the step on
``needs.detect.outputs.docs_only != 'true'``). Without the exemption the hook
was strictly stricter than the rule it mirrors, and forced documentation
changes to burn build numbers that concurrent branches already held.

## How it is tested

Every case builds a throwaway git checkout shaped like ECM (a
``frontend/package.json``, a local ``refs/remotes/origin/dev`` baseline, and
the two real scripts copied in), then runs the real hook against it with a
real PreToolUse payload on stdin. The scripts are copied rather than
reimplemented so the test proves the hook drives the same classifier CI does.

No remote is configured, so the hook's best-effort ``git fetch origin dev``
fails instantly and the suite never touches the network.

Exit-code contract (Claude Code hooks): 0 allows the tool call, 2 blocks it
and feeds stderr back to the agent.

Output contract, which is why several cases assert on ``stdout`` rather than
``stderr``: on exit 2 Claude Code ignores stdout and uses stderr as the
blocking reason, but on exit 0 stderr goes to the hook debug log only and the
agent never sees it. So an exit-0 message is only actually delivered if it
appears as JSON on stdout, under ``hookSpecificOutput.additionalContext`` (read
by Claude) and ``systemMessage`` (read by the operator). Asserting that the
script wrote to fd 2 would prove the process emitted the text, not that anyone
receives it, so every exit-0 announcement is pinned on the JSON surface too.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "version-advance-guard.sh"

# Baseline build vs a legitimately advanced one. Shape per docs/versioning.md.
BASE_VERSION = "0.18.1-0053"
BUMPED_VERSION = "0.18.1-0054"

ALLOW = 0
BLOCK = 2

SHIP_COMMAND = "gh pr create --base dev --title t --body b"


# ─── Fixture plumbing ──────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed:\n{proc.stderr}"
    return proc


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package_json(version: str) -> str:
    return json.dumps({"name": "ecm-frontend", "version": version}, indent=2) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway checkout shaped like ECM, parked on a feature branch."""
    root = tmp_path / "ecm"
    root.mkdir()
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "config", "user.email", "guard-test@example.invalid")
    _git(root, "config", "user.name", "Guard Test")

    # The REAL scripts, not stand-ins: the point of the exemption is that one
    # definition of "documentation-only" is shared with CI.
    (root / "scripts").mkdir()
    for name in ("check_version_advances.py", "classify_changed_paths.py"):
        shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)

    _write(root, "frontend/package.json", _package_json(BASE_VERSION))
    _write(root, "docs/guide.md", "Baseline documentation.\n")
    _write(root, "backend/app.py", "VALUE = 1\n")
    _write(root, ".beads/issues.jsonl", '{"id": "seed"}\n')
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "baseline")

    # Stand in for the remote-tracking baseline without configuring a remote,
    # so the hook's `git fetch origin dev` fails fast and offline.
    _git(root, "update-ref", "refs/remotes/origin/dev", "HEAD")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


def _commit(repo: Path, files: dict[str, str], version: str | None = None) -> None:
    """Apply a change on the feature branch, optionally bumping the version."""
    if version is not None:
        _write(repo, "frontend/package.json", _package_json(version))
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change under test")


def _invoke(
    repo: Path,
    command: str = SHIP_COMMAND,
    project_dir: Path | None = None,
    use_env_root: bool = True,
    path_prepend: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real hook with a real PreToolUse payload on stdin."""
    payload = json.dumps({"tool_input": {"command": command}})
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project_dir if project_dir else repo)
    if path_prepend is not None:
        env["PATH"] = f"{path_prepend}{os.pathsep}{env.get('PATH', '')}"
    if use_env_root:
        env["ECM_VERSION_GUARD_ROOT"] = str(repo)
    else:
        env.pop("ECM_VERSION_GUARD_ROOT", None)
    # Keep the checker's CI annotation branch out of the assertions.
    env.pop("GITHUB_ACTIONS", None)
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=env,
        timeout=120,
        check=False,
    )


def _clobber_classifier(repo: Path, body: str) -> None:
    (repo / "scripts" / "classify_changed_paths.py").write_text(body, encoding="utf-8")


def _advance_baseline(repo: Path, files: dict[str, str]) -> None:
    """Move `refs/remotes/origin/dev` forward with commits the branch lacks.

    Reproduces the everyday case where `dev` gains commits after a branch is
    cut. It is what separates the three-dot diff the hook uses from a two-dot
    one: `origin/dev...HEAD` reports only what the BRANCH changed, while
    `origin/dev..HEAD` would fold in whatever landed on dev meanwhile.
    """
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-B", "baseline-advance", "refs/remotes/origin/dev")
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "dev moved on after the branch point")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    _git(repo, "checkout", "-q", branch)


def _notice(result: subprocess.CompletedProcess[str]) -> dict:
    """Parse the hook's exit-0 JSON, the only channel the agent actually reads.

    Asserting on stderr proves the process wrote to fd 2. It does NOT prove the
    message was delivered: exit-0 stderr goes to the hook debug log and Claude
    never sees it. This reads the delivered surface instead.
    """
    assert result.stdout.strip(), (
        "the hook exited 0 with no JSON on stdout, so nothing it said reaches "
        f"Claude or the operator. stderr was: {result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    # Both surfaces carry the same text: additionalContext reaches Claude,
    # systemMessage reaches the operator.
    assert payload["systemMessage"] == payload["hookSpecificOutput"]["additionalContext"]
    return payload


def _delivered(result: subprocess.CompletedProcess[str]) -> str:
    """The message the agent actually receives on an exit-0 path."""
    return _notice(result)["hookSpecificOutput"]["additionalContext"]


def test_hook_exists() -> None:
    """Guard against the harness silently testing nothing if the hook moves."""
    assert HOOK_PATH.is_file(), f"hook not found at {HOOK_PATH}"


# ─── The exemption: documentation-only changes need no build ───────────────


class TestDocumentationOnlyIsExempt:
    def test_markdown_only_change_without_bump_is_allowed(self, repo: Path) -> None:
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stderr
        assert "SKIPPED" in result.stderr
        assert "docs_only=true" in result.stderr
        # The whole point: it must not tell a docs PR to bump.
        assert "BLOCKED" not in result.stderr

    def test_beads_only_change_without_bump_is_allowed(self, repo: Path) -> None:
        """The live case that surfaced this defect: a .beads/ PII redaction."""
        _commit(repo, {".beads/issues.jsonl": '{"id": "seed", "email": "[redacted]"}\n'})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stderr
        assert "SKIPPED" in result.stderr

    def test_skip_is_announced_not_silent(self, repo: Path) -> None:
        """A silent exit 0 is indistinguishable from a hook that never ran.

        Pinned on the DELIVERED channel. An earlier revision announced the skip
        on stderr, which on an exit-0 path reaches the debug log and nobody
        else, so the announcement existed in the process and not in the agent.
        """
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW
        delivered = _delivered(result)
        assert "version-advance-guard:" in delivered
        assert "no build to advance" in delivered
        assert "Do NOT bump the version touchpoints" in delivered

    def test_three_dot_diff_ignores_commits_dev_gained_since_the_branch_point(
        self, repo: Path
    ) -> None:
        """`origin/dev...HEAD`, not `origin/dev..HEAD`.

        The branch changes documentation only; `dev` independently gains a code
        commit afterwards. A two-dot diff would report that code file as part of
        this change and wrongly block a documentation-only ship. Nothing else in
        this file distinguishes the two forms, because every other fixture
        leaves the baseline exactly at the branch point.
        """
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        _advance_baseline(repo, {"backend/other.py": "OTHER = 2\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in _delivered(result)

    def test_exemption_applies_to_the_cd_target_not_the_project_dir(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Classification follows the same checkout resolution as the comparison.

        This is the worktree shape from bead enhancedchannelmanager-yxtuz:
        CLAUDE_PROJECT_DIR points at some other checkout while the ship command
        runs from the tree being PR'd. Only the correctly resolved tree can
        produce the skip message, so this pins the two halves together.
        """
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(
            repo,
            command=f"cd {repo} && gh pr create --base dev",
            project_dir=decoy,
            use_env_root=False,
        )
        assert result.returncode == ALLOW, result.stderr
        assert "SKIPPED" in result.stderr


# ─── The verdict is read by key, not by whole-output equality ──────────────


class TestVerdictIsReadByKey:
    """The classifier's stdout is a key=value set that is expected to GROW.

    Bead enhancedchannelmanager-t4d5w added a `docs_site_affected` line beside
    `docs_only`. An earlier revision of the hook compared the entire stdout
    against the literals "docs_only=true" and "docs_only=false", so the moment
    a second line appeared every invocation matched neither and took the
    fail-open path: the guard would have warned on every ship while enforcing
    nothing, silently, because fail-open degradation does not announce itself
    as a block. Nothing in the rest of this file would have caught that.

    Key order is not part of the contract, so both orders are pinned.
    """

    TWO_KEYS_DOCS_FIRST = "print('docs_only=true')\nprint('docs_site_affected=false')\n"
    TWO_KEYS_DOCS_LAST = "print('docs_site_affected=false')\nprint('docs_only=true')\n"

    def test_extra_key_after_docs_only_still_skips(self, repo: Path) -> None:
        _clobber_classifier(repo, self.TWO_KEYS_DOCS_FIRST)
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in result.stderr
        assert "WARNING" not in result.stderr

    def test_extra_key_before_docs_only_still_skips(self, repo: Path) -> None:
        """Guards against depending on docs_only being the first line."""
        _clobber_classifier(repo, self.TWO_KEYS_DOCS_LAST)
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in result.stderr
        assert "WARNING" not in result.stderr

    def test_many_extra_keys_still_skip(self, repo: Path) -> None:
        _clobber_classifier(
            repo,
            "print('a=1')\nprint('docs_site_affected=true')\n"
            "print('docs_only=true')\nprint('z=99')\n",
        )
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in result.stderr

    def test_extra_keys_do_not_soften_a_false_verdict(self, repo: Path) -> None:
        """The deny side must survive the extension too, not just the skip."""
        _clobber_classifier(
            repo, "print('docs_site_affected=true')\nprint('docs_only=false')\n"
        )
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "BLOCKED by version-advance-guard" in result.stderr

    def test_a_key_merely_ENDING_in_docs_only_is_not_the_docs_only_key(
        self, repo: Path
    ) -> None:
        """The value is matched from the START of the line, not mid-line.

        Without the `^` anchor on the extraction, a future or mistaken
        `not_docs_only=true` line would be read as `docs_only=true` and skip the
        check on a CODE change. Nothing else in this file fails if the anchor is
        dropped, because every other fixture's key sits at column zero already.
        The correct behaviour is fail-open, because this classifier emitted no
        `docs_only` key at all.
        """
        _clobber_classifier(repo, "print('not_docs_only=true')\n")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        delivered = _delivered(result)
        assert "WARNING" in delivered
        assert "no docs_only key" in delivered
        assert "SKIPPED" not in delivered

    def test_crlf_line_endings_are_tolerated(self, repo: Path) -> None:
        """A Windows checkout must not degrade the guard into permanent warning."""
        _clobber_classifier(
            repo,
            "import sys\n"
            "sys.stdout.write('docs_only=true\\r\\ndocs_site_affected=false\\r\\n')\n",
        )
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in result.stderr


# ─── The deny side must survive the exemption ──────────────────────────────


class TestCodeChangesStillBlock:
    def test_code_change_without_bump_still_blocks(self, repo: Path) -> None:
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "BLOCKED by version-advance-guard" in result.stderr
        assert "SKIPPED" not in result.stderr

    def test_mixed_docs_and_code_change_without_bump_still_blocks(
        self, repo: Path
    ) -> None:
        """One code path in the set defeats the exemption for the whole set."""
        _commit(
            repo,
            {
                "docs/guide.md": "Reworded documentation.\n",
                ".beads/issues.jsonl": '{"id": "seed", "note": "touched"}\n',
                "backend/app.py": "VALUE = 2\n",
            },
        )
        result = _invoke(repo)
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "BLOCKED by version-advance-guard" in result.stderr
        assert "SKIPPED" not in result.stderr

    def test_code_change_with_bump_is_allowed(self, repo: Path) -> None:
        _commit(repo, {"backend/app.py": "VALUE = 2\n"}, version=BUMPED_VERSION)
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stderr
        assert "SKIPPED" not in result.stderr
        assert "build advances" in result.stdout + result.stderr

    def test_empty_diff_against_baseline_still_blocks(self, repo: Path) -> None:
        """No changed paths is not documentation. It falls through to the check."""
        result = _invoke(repo)  # feature branch is identical to origin/dev
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "SKIPPED" not in result.stderr


# ─── Classification trouble fails OPEN, and says so ────────────────────────


class TestClassificationFailureFailsOpen:
    """Classification trouble must not wedge shipping, and must not be silent.

    This inverts CI's idiom deliberately. CI fails open toward CODE because its
    risk is a hollow green required check. The hook cannot make anything merge,
    only stop an agent from opening a PR, and its header records the standard
    as "a false block here is a friction bug, not a safety gap" because CI
    re-enforces the rule server-side. So an unusable classifier exits 0, the
    same way a missing checker already did.

    The residual cost is real and is pinned here too: while classification is
    broken the agent-side catch is gone, so every one of these paths must emit
    a loud WARNING rather than pass quietly. That is asserted on the DELIVERED
    channel, not on stderr: exit-0 stderr reaches the debug log and nobody
    else, so a stderr-only assertion would prove the process spoke, not that
    the agent heard.
    """

    def _assert_failed_open_loudly(
        self, result: subprocess.CompletedProcess[str]
    ) -> None:
        assert result.returncode == ALLOW, result.stdout + result.stderr
        delivered = _delivered(result)
        assert "WARNING" in delivered, "a degraded guard must not be silent"
        assert "version-advance-guard:" in delivered
        assert "SKIPPED" not in delivered, "this is not a docs-only exemption"

    def test_missing_classifier_fails_open(self, repo: Path) -> None:
        (repo / "scripts" / "classify_changed_paths.py").unlink()
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)
        assert "classifier not found" in result.stderr

    def test_crashing_classifier_fails_open(self, repo: Path) -> None:
        _clobber_classifier(repo, "import sys\nsys.exit(3)\n")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)
        assert "exited non-zero" in result.stderr

    def test_classifier_syntax_error_fails_open(self, repo: Path) -> None:
        _clobber_classifier(repo, "def (\n")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)

    def test_output_without_the_docs_only_key_fails_open(self, repo: Path) -> None:
        _clobber_classifier(repo, "print('banana')\n")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)
        assert "no docs_only key" in result.stderr

    def test_other_keys_without_docs_only_fails_open(self, repo: Path) -> None:
        """A sibling key is not a substitute for the one this hook reads."""
        _clobber_classifier(repo, "print('docs_site_affected=false')\n")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)
        assert "no docs_only key" in result.stderr

    def test_contradictory_duplicate_keys_fail_open(self, repo: Path) -> None:
        """Two docs_only lines must not resolve to whichever came first."""
        _clobber_classifier(
            repo, "print('docs_only=true')\nprint('docs_only=false')\n"
        )
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)

    def test_lookalike_verdict_is_not_read_as_false_and_does_not_block(
        self, repo: Path
    ) -> None:
        """A garbled classifier must not manufacture a block either."""
        _clobber_classifier(repo, "print('docs_only=true-ish')\n")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)

    def test_unresolvable_baseline_fails_open(self, repo: Path) -> None:
        """No origin/dev means no diff to classify, and nothing to compare."""
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        _git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
        result = _invoke(repo)
        self._assert_failed_open_loudly(result)
        assert "could not diff" in result.stderr

    def test_a_healthy_classifier_saying_false_still_blocks(self, repo: Path) -> None:
        """The guard is only surrendered on trouble, never on a real verdict."""
        _clobber_classifier(repo, "print('docs_only=false')\n")
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        result = _invoke(repo)
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "BLOCKED by version-advance-guard" in result.stderr


# ─── Pre-existing behaviour the exemption must not disturb ─────────────────


class TestGuardStaysInert:
    def test_non_ship_command_is_inert(self, repo: Path) -> None:
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, command="ls -la")
        assert result.returncode == ALLOW
        assert result.stdout == ""
        assert result.stderr == ""

    def test_quoted_mention_does_not_trigger_the_guard(self, repo: Path) -> None:
        """Quoted data is not command syntax (engineering-discipline)."""
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, command="echo 'gh pr create --base dev'")
        assert result.returncode == ALLOW
        assert result.stderr == ""

    def test_release_cut_targeting_main_is_exempt(self, repo: Path) -> None:
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, command="gh pr create --base main")
        assert result.returncode == ALLOW, result.stdout + result.stderr
        # Exempt, but not silently: same standard as the documentation-only
        # skip, which this used to contradict by exiting 0 saying nothing.
        assert "SKIPPED" in _delivered(result)

    def test_release_cut_with_an_equals_sign_base_is_exempt(self, repo: Path) -> None:
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, command="gh pr create --base=main --title t")
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in _delivered(result)

    def test_a_base_branch_merely_STARTING_with_main_is_not_exempt(
        self, repo: Path
    ) -> None:
        """`--base mainline-experiment` is not `--base main`.

        The exemption is for release cuts. A prefix match hands it to any
        branch whose name happens to begin with those four letters.
        """
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, command="gh pr create --base mainline-experiment")
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "BLOCKED by version-advance-guard" in result.stderr

    def test_the_string_base_main_inside_the_body_is_not_a_base_flag(
        self, repo: Path
    ) -> None:
        """Quoted data is not command syntax (engineering-discipline).

        A real `--base dev` ship whose body merely QUOTES the characters
        `--base main` must still be checked. Matching raw command text exempted
        it, which is the silent direction: the ship proceeds unguarded and
        nothing says why.
        """
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(
            repo,
            command=(
                "gh pr create --base dev --title t "
                "--body 'Release cuts use --base main; this one does not.'"
            ),
        )
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert "BLOCKED by version-advance-guard" in result.stderr

    def test_a_later_command_mentioning_base_main_does_not_win(
        self, repo: Path
    ) -> None:
        """The FIRST --base is the one belonging to this `gh pr create`."""
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(
            repo, command="gh pr create --base dev && echo done --base main"
        )
        assert result.returncode == BLOCK, result.stdout + result.stderr


# ─── The hook output contract: exit 0 speaks JSON, exit 2 speaks stderr ────


class TestOutputReachesTheAgent:
    """Exit 0 stderr is invisible to Claude, so exit-0 messages must be JSON.

    The corollary matters just as much: on exit 2 Claude Code IGNORES stdout
    and reads stderr as the blocking reason, and the docs are explicit that a
    hook picks one signalling style per invocation. So the block path must stay
    pure stderr and emit no JSON at all.
    """

    def test_the_block_path_emits_no_json_and_speaks_on_stderr(
        self, repo: Path
    ) -> None:
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo)
        assert result.returncode == BLOCK, result.stdout + result.stderr
        assert result.stdout == "", "exit 2 ignores stdout; JSON here is noise"
        assert "BLOCKED by version-advance-guard" in result.stderr

    def test_an_inert_command_says_nothing_on_either_channel(
        self, repo: Path
    ) -> None:
        """The guard runs on EVERY Bash call, so silence stays the default."""
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, command="git status")
        assert result.returncode == ALLOW
        assert result.stdout == ""
        assert result.stderr == ""

    def test_every_exit_zero_ship_path_delivers_its_message(
        self, repo: Path
    ) -> None:
        """One assertion per exit-0 ship outcome: none may be silent.

        Docs-only skip, release-cut skip, degraded-classifier warning, and a
        clean pass. Each is a path where the guard did NOT block and therefore
        must explain itself on the channel the agent reads.
        """
        _commit(repo, {"docs/guide.md": "Reworded documentation.\n"})
        assert "SKIPPED" in _delivered(_invoke(repo))
        assert "SKIPPED" in _delivered(
            _invoke(repo, command="gh pr create --base main")
        )

        _clobber_classifier(repo, "print('banana')\n")
        assert "WARNING" in _delivered(_invoke(repo))

    def test_the_passing_path_relays_the_checker_diagnostic(
        self, repo: Path
    ) -> None:
        """The soft CHANGELOG warning lives in that relay and is worth reading."""
        _commit(repo, {"backend/app.py": "VALUE = 2\n"}, version=BUMPED_VERSION)
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        delivered = _delivered(result)
        assert "PASSED" in delivered
        assert "build advances" in delivered

    def test_the_json_survives_a_relayed_message_with_quotes_and_newlines(
        self, repo: Path
    ) -> None:
        """The payload is built by json.dumps, never by string concatenation.

        The relayed checker diagnostic is multi-line free text and can carry
        quotes and backslashes. Hand-assembled JSON would be malformed here and
        the entire announcement would be dropped rather than garbled, because
        an unparseable payload is not partially readable.
        """
        (repo / "scripts" / "check_version_advances.py").write_text(
            'print("build advances\\nquote \\" backslash \\\\ brace }")\n',
            encoding="utf-8",
        )
        _commit(repo, {"backend/app.py": "VALUE = 2\n"}, version=BUMPED_VERSION)
        result = _invoke(repo)
        assert result.returncode == ALLOW, result.stdout + result.stderr
        delivered = _delivered(result)
        assert 'quote " backslash \\ brace }' in delivered


# ─── No shell-injection surface from paths or branch names ─────────────────


class TestUntrustedInputIsData:
    def test_a_shell_metacharacter_filename_cannot_execute(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """A changed path is piped to the classifier, never expanded by bash.

        The canary file must not exist afterwards, and the code path must still
        block, because a `;`-bearing filename is not documentation.
        """
        canary = tmp_path / "canary"
        _commit(repo, {f"backend/a; touch {canary}; b.py": "VALUE = 2\n"})
        result = _invoke(repo)
        assert not canary.exists(), "a changed path was evaluated by the shell"
        assert result.returncode == BLOCK, result.stdout + result.stderr

    def test_a_markdown_path_with_metacharacters_is_still_documentation(
        self, repo: Path, tmp_path: Path
    ) -> None:
        canary = tmp_path / "canary-md"
        _commit(repo, {f"docs/a; touch {canary}; b.md": "Notes.\n"})
        result = _invoke(repo)
        assert not canary.exists(), "a changed path was evaluated by the shell"
        assert result.returncode == ALLOW, result.stdout + result.stderr
        assert "SKIPPED" in result.stderr

    def test_a_branch_name_with_metacharacters_cannot_execute(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Branch names never reach the guard's command construction.

        Git forbids spaces in a ref name, so the payload is a bare command
        substitution resolved through PATH: if any layer of the guard ever
        evaluated the branch name, ``ecm-guard-canary`` would run and leave
        its marker behind.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir()
        canary = tmp_path / "canary-branch"
        probe = bindir / "ecm-guard-canary"
        probe.write_text(f"#!/bin/sh\ntouch '{canary}'\n", encoding="utf-8")
        probe.chmod(0o755)

        _git(repo, "checkout", "-q", "-b", "feat/a$(ecm-guard-canary)b;c|d&e")
        _commit(repo, {"backend/app.py": "VALUE = 2\n"})
        result = _invoke(repo, path_prepend=bindir)
        assert not canary.exists(), "a branch name was evaluated by the shell"
        assert result.returncode == BLOCK, result.stdout + result.stderr
