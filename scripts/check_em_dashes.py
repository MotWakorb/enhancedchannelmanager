#!/usr/bin/env python3
"""Fail CI when NEW em-dashes (U+2014) appear in documentation prose.

`docs/style_guide.md` (section "Prose Style (Docs and Comments)") bans the
em-dash in documentation prose. Until this guard existed the rule had no
enforcement, and two documentation passes shipped 144 violations through
two merged PRs without three reviewers noticing (bead
`enhancedchannelmanager-3tflw`).

The rule's text also names code comments, but that clause is a convention
enforced by review, not by this script: the scan surface is documentation
only (see bead `enhancedchannelmanager-3tflw` scope narrowing).

## This is a ratchet, not a cliff

The repository already carries a large body of pre-existing em-dashes.
Failing on all of them would red-line CI on day one, which the PO
explicitly declined. So the default mode checks only the lines a change
ADDS relative to its merge base. Existing debt is tolerated and reported
as a count; anything new fails.

Run `--all` for the full inventory when you get around to the cleanup.

## What counts as a violation

Only U+2014 (EM DASH, "-").

Explicitly NOT violations, because the style guide names neither:

  - U+2013 EN DASH ("-"), used for numeric ranges.
  - U+2192 RIGHTWARDS ARROW ("->"), used all over the docs for
    navigation paths and pipeline steps.
  - U+2500-family box drawing characters used as section rules.

## Exemptions the style guide itself grants

  "it does not apply to literal content quoted verbatim inside a code
   block, a quoted log line, a quoted UI string, or a filename, URL, or
   command."

Implemented for Markdown: fenced code blocks (``` and ~~~), inline code
spans (any backtick run), link destinations, bare URLs, and autolinks are
blanked out before scanning.

## Scan surface

Markdown under `docs/`, plus the top-level `README.md`, `CHANGELOG.md`,
and `CLAUDE.md`. Nothing else.

## Suppression

Put `em-dash-ok: <reason>` on the offending line, mirroring the
`fake-test-ok` convention in `scripts/check_fake_tests.py`. A bare
`em-dash-ok` with no reason will be rejected at code review.

## Usage

    python scripts/check_em_dashes.py                    # ratchet vs origin/dev
    python scripts/check_em_dashes.py --base-ref origin/main
    python scripts/check_em_dashes.py --all              # full inventory
    python scripts/check_em_dashes.py --paths docs/x.md  # scan named paths in full

Exits 0 when clean, 1 when new violations are present.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EM_DASH = "—"
ALLOW_COMMENT = "em-dash-ok"
STYLE_GUIDE_REF = 'docs/style_guide.md section "Prose Style (Docs and Comments)"'

# Directory names pruned wherever they appear in the walk.
PRUNED_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        ".beads",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "site",
        "coverage",
        "htmlcov",
        "test-results",
        "playwright-report",
    }
)

# (repo-relative root, glob) pairs that define the scanned surface. Kept
# deliberately narrow: documentation prose only.
SCAN_GLOBS: tuple[tuple[str, str], ...] = (("docs", "**/*.md"),)

# Top-level Markdown files scanned individually (a `*.md` glob at the repo
# root would otherwise sweep vendored trees).
ROOT_MARKDOWN = ("README.md", "CHANGELOG.md", "CLAUDE.md")


# --- File classification ----------------------------------------------------


def _kind_for(rel_path: str) -> str | None:
    """Return the scanner kind for a repo-relative path, or None to skip."""
    if rel_path.endswith(".md"):
        return "markdown"
    return None


def _is_pruned(rel_path: str) -> bool:
    return any(part in PRUNED_DIR_NAMES for part in Path(rel_path).parts)


def _in_scan_surface(rel_path: str) -> bool:
    """True when the path is one this guard is responsible for."""
    if _is_pruned(rel_path):
        return False
    if rel_path in ROOT_MARKDOWN:
        return True
    path = Path(rel_path)
    for root, glob in SCAN_GLOBS:
        root_path = Path(root)
        try:
            relative = path.relative_to(root_path)
        except ValueError:
            continue
        if relative.match(glob.removeprefix("**/")):
            return True
    return False


def _iter_scan_surface() -> Iterator[str]:
    """Yield every repo-relative path inside the scan surface."""
    seen: set[str] = set()
    for name in ROOT_MARKDOWN:
        if (REPO_ROOT / name).is_file():
            seen.add(name)
    for root, glob in SCAN_GLOBS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.glob(glob):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _is_pruned(rel):
                continue
            seen.add(rel)
    yield from sorted(seen)


# --- Markdown ---------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)*?\1", re.DOTALL)
_LINK_DEST_RE = re.compile(r"\]\([^)]*\)")
_REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S+.*$")
_AUTOLINK_RE = re.compile(r"<[a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]*>")
_BARE_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")


def _blank(match: re.Match[str]) -> str:
    """Replace a matched region with spaces, preserving newlines and offsets."""
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def markdown_prose_lines(text: str) -> dict[int, str]:
    """Map 1-based line number to the prose-only content of that line.

    Fenced code blocks are dropped entirely. Inline code spans, link
    destinations, autolinks, bare URLs, and reference definitions are
    blanked out. Every remaining line is prose the style guide governs.
    """
    lines = text.splitlines()
    prose: dict[int, str] = {}
    fence: str | None = None

    for index, line in enumerate(lines, start=1):
        fence_match = _FENCE_RE.match(line)
        if fence is None:
            if fence_match:
                fence = fence_match.group(1)[0]
                continue
        else:
            if fence_match and fence_match.group(1)[0] == fence:
                fence = None
            continue
        prose[index] = line

    if not prose:
        return prose

    # Inline code spans may wrap across lines, so re-join the prose lines
    # (holes included as blanks) and strip spans over the whole document.
    max_line = max(prose)
    joined = "\n".join(prose.get(n, "") for n in range(1, max_line + 1))
    joined = _INLINE_CODE_RE.sub(_blank, joined)
    joined = _AUTOLINK_RE.sub(_blank, joined)
    joined = _LINK_DEST_RE.sub(_blank, joined)
    joined = _BARE_URL_RE.sub(_blank, joined)

    result: dict[int, str] = {}
    for offset, content in enumerate(joined.split("\n"), start=1):
        if offset in prose:
            if _REF_DEF_RE.match(prose[offset]):
                continue
            result[offset] = content
    return result


_SCANNERS = {
    "markdown": markdown_prose_lines,
}


# --- Violation scan ---------------------------------------------------------


class Violation:
    __slots__ = ("path", "line", "text")

    def __init__(self, path: str, line: int, text: str) -> None:
        self.path = path
        self.line = line
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Violation({self.path!r}, {self.line}, {self.text!r})"


def scan_text(rel_path: str, text: str, kind: str) -> list[Violation]:
    """Return every em-dash violation in `text`, exemptions already applied."""
    scanner = _SCANNERS[kind]
    raw_lines = text.splitlines()
    violations: list[Violation] = []
    for number, content in sorted(scanner(text).items()):
        if EM_DASH not in content:
            continue
        raw = raw_lines[number - 1] if number <= len(raw_lines) else content
        if ALLOW_COMMENT in raw:
            continue
        violations.append(Violation(rel_path, number, raw.strip()))
    return violations


def scan_paths(rel_paths: Iterable[str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path in rel_paths:
        kind = _kind_for(rel_path)
        if kind is None:
            continue
        full = REPO_ROOT / rel_path
        try:
            text = full.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            continue
        violations.extend(scan_text(rel_path, text, kind))
    return violations


# --- Diff scoping -----------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Map repo-relative path to the set of line numbers the diff ADDS.

    Reads a `git diff --unified=0` payload. Deletions are ignored, and so
    are removed files: a line that no longer exists cannot be a new
    violation.
    """
    added: dict[str, set[int]] = {}
    current: str | None = None
    next_line = 0

    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = target[2:] if target.startswith("b/") else target
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        hunk = _HUNK_RE.match(raw)
        if hunk:
            next_line = int(hunk.group(1))
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            added.setdefault(current, set()).add(next_line)
            next_line += 1

    return added


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def resolve_merge_base(base_ref: str) -> str:
    """Return the merge base of `base_ref` and HEAD."""
    return _git("merge-base", base_ref, "HEAD").strip()


def untracked_files() -> list[str]:
    """Repo-relative paths git is not tracking yet, gitignore respected."""
    output = _git("ls-files", "--others", "--exclude-standard")
    return [line for line in output.splitlines() if line]


def added_lines_since(base: str) -> dict[str, set[int]]:
    """Added lines between `base` and the working tree, uncommitted included.

    A brand-new file is not in `git diff` until it is staged, so untracked
    files are folded in with every line treated as added. Without this the
    guard would wave through a whole new document locally and only fail
    once CI diffed the pushed branch.
    """
    diff = _git("diff", "--unified=0", "--no-color", "--no-renames", base)
    added = parse_added_lines(diff)

    for rel_path in untracked_files():
        if not _in_scan_surface(rel_path) or _kind_for(rel_path) is None:
            continue
        full = REPO_ROOT / rel_path
        try:
            line_count = len(full.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
        added.setdefault(rel_path, set()).update(range(1, line_count + 1))

    return added


# --- Reporting --------------------------------------------------------------


def _report(violations: list[Violation], stream) -> None:
    for violation in violations:
        print(f"  {violation.path}:{violation.line}: {violation.text}", file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ratchet guard against new em-dashes in prose and comments."
    )
    parser.add_argument(
        "--base-ref",
        default="origin/dev",
        help="Ref the change is measured against (default: origin/dev).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Report every violation in the repo, not just newly added ones. "
        "Always exits 0: this mode is the cleanup inventory, not the gate.",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PATH",
        help="Scan these repo-relative paths in full instead of diffing.",
    )
    args = parser.parse_args(argv)

    if args.paths:
        violations = scan_paths(args.paths)
        if violations:
            print(
                f"FAIL: {len(violations)} em-dash violation(s) in the named paths:",
                file=sys.stderr,
            )
            _report(violations, sys.stderr)
            print(f"\nRule: {STYLE_GUIDE_REF}.", file=sys.stderr)
            return 1
        print("PASS: no em-dashes in the named paths.")
        return 0

    inventory = scan_paths(_iter_scan_surface())

    if args.all:
        print(
            f"Full inventory: {len(inventory)} em-dash violation(s) across "
            f"{len({v.path for v in inventory})} file(s)."
        )
        _report(inventory, sys.stdout)
        print(
            "\nThis is the deferred-cleanup inventory (bead "
            "enhancedchannelmanager-3tflw). It never fails the build; the "
            "default ratchet mode does."
        )
        return 0

    try:
        base = resolve_merge_base(args.base_ref)
    except RuntimeError as error:
        print(f"FATAL: cannot resolve base ref {args.base_ref!r}: {error}", file=sys.stderr)
        print(
            "Pass --base-ref explicitly, or fetch the base branch first "
            "(CI needs fetch-depth: 0).",
            file=sys.stderr,
        )
        return 1

    added = added_lines_since(base)
    scoped = {
        path: numbers
        for path, numbers in added.items()
        if _in_scan_surface(path) and _kind_for(path) is not None
    }

    new_violations = [
        violation
        for violation in scan_paths(scoped)
        if violation.line in scoped[violation.path]
    ]

    if new_violations:
        print(
            f"FAIL: {len(new_violations)} NEW em-dash violation(s) on lines this "
            f"change adds (base {base[:12]}):",
            file=sys.stderr,
        )
        _report(new_violations, sys.stderr)
        print(
            f"\nRule: {STYLE_GUIDE_REF}. Rewrite rather than substituting an "
            "en-dash or a double hyphen: two clauses become two sentences, an "
            "appositive becomes a colon, an aside becomes commas or "
            "parentheses.",
            file=sys.stderr,
        )
        print(
            f"En-dashes and arrows are not flagged. If a line is genuinely "
            f"quoted content the style guide exempts, add "
            f"'{ALLOW_COMMENT}: <reason>' on that line.",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS: no new em-dashes on the {sum(len(v) for v in scoped.values())} "
        f"line(s) added across {len(scoped)} scanned file(s) since {base[:12]}."
    )
    print(
        f"Pre-existing debt tolerated by the ratchet: {len(inventory)} "
        f"violation(s) in {len({v.path for v in inventory})} file(s). "
        "Run with --all to list them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
