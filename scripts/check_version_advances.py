#!/usr/bin/env python3
"""Verify the frontend/package.json BUILD number ADVANCES past a baseline.

Companion to ``scripts/check_version_consistency.py`` (bd-9rtlc). That guard
checks the three version touchpoints *match*; this one checks that the BUILD
number of ``frontend/package.json`` (the canonical touchpoint) is strictly
greater than the baseline branch's BUILD — so a source PR cannot ship a
non-advancing build number.

Why this exists (bead enhancedchannelmanager-w9irb):

  ~9 source PRs shipped to dev in one session at 0.17.6-0084 with no build
  bump, undetected — the consistency guard was happy (all three touchpoints
  matched) but nobody advanced the build number. A stalled build stream
  breaks the "BUILD -> commit" mapping that docs/versioning.md relies on.

Both CI (.github/workflows/test.yml -> version-consistency job) and the
Claude Code agent-side ship hook (.claude/hooks/version-advance-guard.sh)
call THIS script, so the advance rule lives in one place.

Version shape (docs/versioning.md):  MAJOR.MINOR.PATCH-BUILD, BUILD zero-padded.
A release cut legitimately DROPS the ``-BUILD`` suffix (X.Y.Z). Such a
no-suffix current version is EXEMPT (there is no build number to advance).

Exit codes:
  0  advance verified, OR exempt (no-suffix / release cut), OR baseline
     could not be determined (soft-pass with a loud warning — a gate must
     not wedge every merge on a transient git/fetch problem).
  1  the current BUILD is <= the baseline BUILD (a non-advancing bump), OR
     the current version is malformed.

The CHANGELOG ``[Unreleased]`` check is SOFT: it only ever prints a WARNING
and never changes the exit code (PO decision on w9irb).

Usage (from repo root):

    # Compare working-tree frontend/package.json against origin/dev:
    python scripts/check_version_advances.py

    # Explicit baseline ref (CI passes the PR base branch):
    python scripts/check_version_advances.py --baseline-ref origin/dev

    # Baseline from a file instead of a git ref (used by unit tests):
    python scripts/check_version_advances.py --baseline-file /tmp/base.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Resolve repo root from this script's location: scripts/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGE_JSON_REL = "frontend/package.json"
CHANGELOG_REL = "CHANGELOG.md"

# X.Y.Z-BUILD, BUILD numeric (zero-padding preserved as-is).
_NUMBERED_RE = re.compile(r"^(\d+\.\d+\.\d+)-(\d+)$")
# X.Y.Z with no -BUILD suffix (a release cut).
_NO_SUFFIX_RE = re.compile(r"^(\d+\.\d+\.\d+)$")


# ─── Pure comparator (unit-tested; no IO) ──────────────────────────────────


@dataclass(frozen=True)
class BuildParse:
    """Result of parsing a version string's BUILD component.

    kind:
      "numbered"  -> a normal dev build; ``build`` and ``prefix`` are set.
      "no_suffix" -> a release-cut version (X.Y.Z); ``build`` is None.
      "malformed" -> not a recognizable ECM version; ``build`` is None.
    """

    kind: str
    prefix: str | None
    build: int | None


def parse_build(version: str | None) -> BuildParse:
    """Classify a version string and pull out its numeric BUILD if present."""
    if not version or not isinstance(version, str):
        return BuildParse("malformed", None, None)
    text = version.strip()
    m = _NUMBERED_RE.match(text)
    if m:
        return BuildParse("numbered", m.group(1), int(m.group(2)))
    m = _NO_SUFFIX_RE.match(text)
    if m:
        return BuildParse("no_suffix", m.group(1), None)
    return BuildParse("malformed", None, None)


@dataclass(frozen=True)
class AdvanceResult:
    status: str  # advanced | not_advanced | exempt_no_suffix | malformed_current | baseline_unavailable
    exit_code: int
    message: str


def _suggest_next(prefix: str, baseline_build: int, width: int) -> str:
    """Suggest the next build number: baseline_build + 1, zero-padded."""
    return f"{prefix}-{baseline_build + 1:0{width}d}"


def evaluate_advance(
    current_version: str | None, baseline_version: str | None
) -> AdvanceResult:
    """Decide whether ``current_version``'s BUILD advances past ``baseline``.

    Pure function — no git, no file IO — so it is exhaustively unit-testable.
    """
    current = parse_build(current_version)

    # Release cut: current has no -BUILD suffix. Nothing to advance; exempt.
    if current.kind == "no_suffix":
        return AdvanceResult(
            "exempt_no_suffix",
            0,
            f"EXEMPT: current version {current_version!r} has no -BUILD suffix "
            "(release cut). Build-advance check does not apply.",
        )

    # A current version we cannot parse is a hard error — we cannot verify it
    # advances, and it is very likely a typo in the bump.
    if current.kind == "malformed":
        return AdvanceResult(
            "malformed_current",
            1,
            f"FAIL: current version {current_version!r} is not a valid "
            "ECM version (expected MAJOR.MINOR.PATCH-BUILD, e.g. 0.17.6-0086). "
            "Fix frontend/package.json.",
        )

    # From here current.kind == "numbered".
    baseline = parse_build(baseline_version)

    # Baseline missing or unusable -> soft pass (do not wedge merges on a
    # transient fetch/parse issue), but say so loudly.
    if baseline.kind != "numbered":
        detail = (
            "baseline version is unavailable"
            if baseline_version is None
            else f"baseline version {baseline_version!r} is not a numbered dev build"
        )
        return AdvanceResult(
            "baseline_unavailable",
            0,
            f"WARNING: could not verify build advance — {detail}. "
            "Skipping the advance check (soft-pass). This should not happen on "
            "a normal PR to dev; investigate if you see this in CI.",
        )

    assert current.build is not None and baseline.build is not None
    assert current.prefix is not None
    # Zero-pad width follows the baseline's own formatting (4 digits today).
    width = len(re.sub(r"^\d+\.\d+\.\d+-", "", (baseline_version or "").strip()))
    width = max(width, 4)

    if current.build > baseline.build:
        return AdvanceResult(
            "advanced",
            0,
            f"OK: build advances — current {current_version} (build "
            f"{current.build}) > baseline {baseline_version} (build "
            f"{baseline.build}).",
        )

    suggestion = _suggest_next(current.prefix, baseline.build, width)
    relation = "equals" if current.build == baseline.build else "is behind"
    return AdvanceResult(
        "not_advanced",
        1,
        f"FAIL: build number does not advance. Current {current_version} "
        f"(build {current.build}) {relation} baseline {baseline_version} "
        f"(build {baseline.build}). Bump frontend/package.json (and the two "
        f"other touchpoints in lockstep) to {suggestion} or higher. "
        "See docs/shipping.md step 3 + docs/versioning.md.",
    )


# ─── CHANGELOG soft check (never affects exit code) ────────────────────────


def extract_unreleased_region(changelog_text: str) -> str | None:
    """Return the text between ``## [Unreleased]`` and the next ``## [`` heading.

    Returns None if no ``[Unreleased]`` heading is present.
    """
    lines = changelog_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s+\[Unreleased\]", line):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^##\s+\[", lines[j]):
            end = j
            break
    # Exclude the heading line itself; compare only the body.
    return "\n".join(lines[start + 1 : end]).strip()


def changelog_soft_warning(
    current_changelog: str | None, baseline_changelog: str | None
) -> str | None:
    """Return a WARNING string if [Unreleased] is unchanged vs baseline, else None.

    SOFT check (PO decision on w9irb): callers print this but never fail on it.
    """
    if current_changelog is None:
        return None  # no CHANGELOG in tree — not this check's job to enforce
    current_region = extract_unreleased_region(current_changelog)
    if current_region is None:
        return (
            "WARNING: CHANGELOG.md has no [Unreleased] section — a source PR "
            "usually records its user-facing change there (soft check, not blocking)."
        )
    if baseline_changelog is None:
        return None  # cannot diff without a baseline — stay silent
    baseline_region = extract_unreleased_region(baseline_changelog)
    if baseline_region is not None and current_region == baseline_region:
        return (
            "WARNING: CHANGELOG.md [Unreleased] is unchanged from the base branch — "
            "if this PR is a user-facing source change, add an entry under "
            "[Unreleased] (soft check, not blocking)."
        )
    return None


# ─── IO helpers ────────────────────────────────────────────────────────────


def _read_current_package_json(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _extract_version(package_json_text: str | None) -> str | None:
    if package_json_text is None:
        return None
    try:
        return json.loads(package_json_text).get("version")
    except (json.JSONDecodeError, AttributeError):
        return None


def _git_show(ref_path: str, repo_root: Path) -> str | None:
    """Return the contents of ``ref_path`` (e.g. origin/dev:frontend/package.json).

    Returns None on any git failure (missing ref, missing file, no git).
    """
    try:
        out = subprocess.run(
            ["git", "show", ref_path],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def _emit_warning(msg: str) -> None:
    """Print a non-failing warning to stderr (and as a GH annotation in CI)."""
    print(msg, file=sys.stderr)
    if _is_github_actions():
        # GitHub renders ::warning:: lines as annotations without failing the job.
        one_line = msg.replace("\n", " ")
        print(f"::warning::{one_line}")


# ─── main ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-ref",
        default="origin/dev",
        help="git ref to read the baseline frontend/package.json + CHANGELOG "
        "from (default: origin/dev). Ignored if --baseline-file is given.",
    )
    parser.add_argument(
        "--baseline-file",
        default=None,
        help="read the baseline frontend/package.json from this file instead "
        "of a git ref (used by unit tests / offline checks).",
    )
    parser.add_argument(
        "--current-file",
        default=str(REPO_ROOT / PACKAGE_JSON_REL),
        help="path to the current frontend/package.json "
        "(default: <repo>/frontend/package.json).",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="repo root for git operations (default: this script's repo).",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)

    # Current version (working tree).
    current_pkg_text = _read_current_package_json(Path(args.current_file))
    current_version = _extract_version(current_pkg_text)

    # Baseline version.
    if args.baseline_file is not None:
        baseline_pkg_text = _read_current_package_json(Path(args.baseline_file))
    else:
        baseline_pkg_text = _git_show(
            f"{args.baseline_ref}:{PACKAGE_JSON_REL}", repo_root
        )
    baseline_version = _extract_version(baseline_pkg_text)

    result = evaluate_advance(current_version, baseline_version)

    # Advance check output on the appropriate stream.
    if result.exit_code == 0:
        print(result.message)
    else:
        print(result.message, file=sys.stderr)
        if _is_github_actions():
            print(f"::error::{result.message.replace(chr(10), ' ')}")

    # ── Soft CHANGELOG check (never affects exit code) ──
    current_changelog = _read_current_package_json(repo_root / CHANGELOG_REL)
    if args.baseline_file is not None:
        baseline_changelog = None  # file-baseline mode: package.json only
    else:
        baseline_changelog = _git_show(
            f"{args.baseline_ref}:{CHANGELOG_REL}", repo_root
        )
    warning = changelog_soft_warning(current_changelog, baseline_changelog)
    if warning is not None:
        _emit_warning(warning)

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
