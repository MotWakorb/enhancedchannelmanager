#!/usr/bin/env python3
"""Fail CI when a change ADDS a credential-bearing or bulk binary artifact.

Bead `enhancedchannelmanager-6mqn5`. Playwright's file-upload sandbox refuses
any path outside the repository root, so every backup/restore drill round that
uploads an artifact through ECM's Restore-from-artifact UI is forced to copy
that artifact into the repo working tree first. One such round left
`.playwright-mcp/` holding 2,321 files totalling 3.1 GB, including
`enc-artifact.zip`, a passphrase-encrypted ECM backup envelope carrying a live
XC provider credential. None of it was tracked, and none of it was ignored: a
single `git add -A` would have committed the lot to a public repository.

`.gitignore` now names that one directory. This guard is the backstop for the
next directory nobody predicted, and it is deliberately not written in terms
of `.playwright-mcp/` at all.

## Why the existing guards do not cover this

`scripts/check_secrets.py` and `scripts/check_pii.py` read added LINES. A
ChaCha20-Poly1305 ciphertext has no lines, a ZIP central directory has no
lines, and detect-secrets finds nothing to report in either. The risk here is
the blob itself, so the unit of inspection is the added FILE.

## Ratchet, not cliff

Only files a change ADDS relative to its merge base are inspected, exactly as
in `scripts/check_em_dashes.py`. Whatever is already tracked stays tracked.
Run `--all` for the whole-tree inventory; today it is clean, and the self-test
in `backend/tests/unit/test_check_binary_artifacts.py` pins it that way so a
false positive on the repository's own screenshots cannot ship.

## The three rules

1. **Content signature.** The leading bytes are matched against a small set of
   high-signal container formats: the ECM `ECMBKENC` envelope, ZIP (called out
   separately when the archive contains `manifest.json`, which is the DBAS
   artifact shape), gzip, bzip2, xz, zstd, 7-Zip, RAR, tar, and SQLite. This
   runs regardless of file extension and regardless of size, because the
   motivating artifact was named `.zip` but the actual risk is a credential
   blob under any name. The repository tracks zero files matching any of these
   signatures.

2. **Unrecognized binary.** A file holding a NUL byte in its first 8 KiB is
   binary. Binary is fine for the image and font types a web application
   genuinely needs; anything else is blocked by type. The suffix list is a
   list of TYPES, not of PATHS, on purpose: a path allowlist would red-line
   every new user-guide section that ships a screenshot, and a guard people
   disable is worse than no guard.

3. **Size.** Per added file, and in aggregate across the change. The aggregate
   rule is what a 2,321-file directory of small trace files trips; no single
   member of it is remarkable.

## Escape hatch

`.binary-artifacts.allowlist` at the repository root. One exact repository
relative path per line, each with a mandatory `#` reason:

    e2e/fixtures/sample-upload.zip  # 40-byte fixture, no credentials, bd-xxxxx

The allowlist is a checked-in, diff-visible file, which is the same shape as
`.secrets.baseline`, the nearest existing convention in this repository. It
was chosen over the inline-comment convention used by `check_em_dashes.py`
(`em-dash-ok: <reason>`) and `check_fake_tests.py` (`fake-test-ok`) for the
obvious reason that a binary file has no line to put a comment on, and over a
commit-message token like the commit-msg hook's `[no-bead]` because a token
scrolls out of view the moment the PR merges while the reason for keeping a
binary in a repository has to stay readable years later.

Entries are exact paths. Globs are not accepted: `*.zip` would re-open the
hole in one line, and the whole value of the hatch is that a human had to name
one file and say why.

## Usage

    python3 scripts/check_binary_artifacts.py                     # vs origin/dev
    python3 scripts/check_binary_artifacts.py --base-ref origin/main
    python3 scripts/check_binary_artifacts.py --all               # whole tree
    python3 scripts/check_binary_artifacts.py --paths a.zip b.png # named paths

Exits 0 when clean, 1 on a finding or on an unusable allowlist.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ALLOWLIST_PATH = ".binary-artifacts.allowlist"

# Per added file. The largest thing this repository legitimately tracks
# outside the size-exempt prefixes below is a 949 KB dev-harness baseline and
# a 198 KB documentation screenshot, so 2 MiB leaves generous headroom while
# still catching a database dump or a media file.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Across every added file in one change. A directory of 2,321 Playwright trace
# files has no remarkable member; the total is the only thing that gives it
# away. Not applied by `--all`, which inventories a whole tree rather than a
# change, and whose total is meaningless as a threshold.
MAX_TOTAL_BYTES = 25 * 1024 * 1024

# Binary file types a web application genuinely needs. Everything tracked
# today is `.png`; the rest are the same media and font classes and are listed
# so the first legitimate favicon or webfont does not have to argue with the
# guard. Note that `.svg` is absent on purpose: SVG is text, so it never
# reaches the binary rule at all.
ALLOWED_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
    }
)

# Exempt from the SIZE rules only. The signature and binary rules still apply.
# `.beads/issues.jsonl` is the machine-managed issue board: 3.8 MB of text that
# the `bd` tool rewrites wholesale, and a re-add after a local reset would
# otherwise trip the per-file ceiling for no reason anyone can act on.
SIZE_EXEMPT_PREFIXES = (".beads/",)

# Leading-byte signatures, longest prefix first so `PK\x03\x04` cannot shadow
# a longer match. Each value is the human-readable format name used in the
# failure message.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"ECMBKENC", "ECM encrypted backup envelope (ECMBKENC)"),
    (b"SQLite format 3\x00", "SQLite database"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"\xfd7zXZ\x00", "xz archive"),
    (b"Rar!\x1a\x07", "RAR archive"),
    (b"\x28\xb5\x2f\xfd", "zstd archive"),
    (b"\x1f\x8b", "gzip archive"),
    (b"PK\x03\x04", "ZIP archive"),
    (b"PK\x05\x06", "ZIP archive"),
    (b"PK\x07\x08", "ZIP archive"),
)

# bzip2 is `BZh` followed by the block-size digit. The digit is part of the
# match on purpose: "BZh" alone is three printable ASCII characters that a
# text file could open with by coincidence.
_BZIP2_PREFIX = b"BZh"
_BZIP2_DIGITS = b"123456789"

# POSIX tar puts its format identifier at offset 257 rather than at the head.
_TAR_OFFSET = 257
_TAR_MAGICS = (b"ustar\x0000", b"ustar  \x00")

# The DBAS artifact is a ZIP whose top level carries this manifest. Naming it
# in the failure message is the difference between "some archive" and "the
# backup artifact that carries provider credentials".
_DBAS_MANIFEST_NAME = "manifest.json"

_SNIFF_BYTES = 8192

REMEDY = (
    "Artifacts staged for a browser upload belong OUTSIDE the repository once "
    "the run is over: delete the staged copy, or move it somewhere the "
    "working tree does not reach. If this file genuinely belongs in the "
    f"repository, add its exact path to {ALLOWLIST_PATH} with a `#` reason "
    "saying what it is and why it carries no credentials."
)


class GuardError(Exception):
    """The guard cannot run: unusable allowlist, or a failed git call."""


# --- Findings ---------------------------------------------------------------


class Finding:
    __slots__ = ("path", "rule", "detail")

    def __init__(self, path: str, rule: str, detail: str) -> None:
        self.path = path
        self.rule = rule
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Finding({self.path!r}, {self.rule!r}, {self.detail!r})"


# --- Allowlist --------------------------------------------------------------


def parse_allowlist(text: str) -> dict[str, str]:
    """Map allowlisted path to its reason.

    Every entry needs a reason. A bare path is a configuration error rather
    than a silently accepted entry: the reason IS the review artifact, and an
    entry nobody had to justify is indistinguishable from turning the guard
    off for that file.
    """
    entries: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path, separator, reason = line.partition("#")
        path = path.strip()
        reason = reason.strip()
        if not path:
            raise GuardError(f"{ALLOWLIST_PATH}:{number}: entry has no path")
        if any(character in path for character in "*?["):
            raise GuardError(
                f"{ALLOWLIST_PATH}:{number}: {path!r} looks like a glob. "
                "Entries are exact repository-relative paths, one artifact "
                "per line, so each one is reviewed on its own merits."
            )
        if not separator or not reason:
            raise GuardError(
                f"{ALLOWLIST_PATH}:{number}: {path!r} has no `# reason`. "
                "Say what the file is and why it carries no credentials."
            )
        entries[path] = reason
    return entries


def load_allowlist(repo_root: Path) -> dict[str, str]:
    allowlist = repo_root / ALLOWLIST_PATH
    if not allowlist.is_file():
        return {}
    try:
        text = allowlist.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GuardError(f"{ALLOWLIST_PATH} is unreadable: {error}") from error
    return parse_allowlist(text)


# --- Content inspection -----------------------------------------------------


def signature_of(head: bytes) -> str | None:
    """Return the container format the leading bytes identify, or None."""
    for magic, name in _SIGNATURES:
        if head.startswith(magic):
            return name
    if (
        head.startswith(_BZIP2_PREFIX)
        and len(head) > len(_BZIP2_PREFIX)
        and head[len(_BZIP2_PREFIX)] in _BZIP2_DIGITS
    ):
        return "bzip2 archive"
    tail = head[_TAR_OFFSET : _TAR_OFFSET + 8]
    if any(tail.startswith(magic) for magic in _TAR_MAGICS):
        return "tar archive"
    return None


def is_dbas_artifact(full_path: Path) -> bool:
    """True when a ZIP carries the DBAS backup manifest at its top level."""
    try:
        with zipfile.ZipFile(full_path) as archive:
            return any(
                name == _DBAS_MANIFEST_NAME
                for name in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile):
        return False


def inspect_file(rel_path: str, full_path: Path, *, check_size: bool) -> list[Finding]:
    """Return every finding for one added file."""
    try:
        size = full_path.stat().st_size
        with full_path.open("rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError:
        # A path in the diff that is not a readable regular file (a submodule
        # gitlink, a symlink to nowhere) is not this guard's business.
        return []

    findings: list[Finding] = []

    signature = signature_of(head)
    if signature is not None:
        detail = signature
        if signature == "ZIP archive" and is_dbas_artifact(full_path):
            detail = (
                "DBAS backup artifact (ZIP containing "
                f"{_DBAS_MANIFEST_NAME})"
            )
        findings.append(Finding(rel_path, "signature", detail))
    elif b"\x00" in head and full_path.suffix.lower() not in ALLOWED_BINARY_SUFFIXES:
        findings.append(
            Finding(
                rel_path,
                "binary",
                f"binary content with an unrecognized suffix "
                f"{full_path.suffix or '(none)'}",
            )
        )

    if check_size and size > MAX_FILE_BYTES:
        findings.append(
            Finding(
                rel_path,
                "size",
                f"{_human(size)} exceeds the {_human(MAX_FILE_BYTES)} "
                "per-file ceiling",
            )
        )

    return findings


def _human(count: int) -> str:
    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MiB"
    if count >= 1024:
        return f"{count / 1024:.1f} KiB"
    return f"{count} B"


def is_size_exempt(rel_path: str) -> bool:
    return rel_path.startswith(SIZE_EXEMPT_PREFIXES)


def scan_paths(
    rel_paths: Iterable[str],
    allowlist: dict[str, str],
    *,
    repo_root: Path = REPO_ROOT,
    aggregate: bool = False,
) -> list[Finding]:
    """Inspect each path, returning every finding across all of them."""
    findings: list[Finding] = []
    total = 0
    for rel_path in sorted(set(rel_paths)):
        if rel_path in allowlist:
            continue
        full_path = repo_root / rel_path
        if not full_path.is_file():
            continue
        check_size = not is_size_exempt(rel_path)
        findings.extend(inspect_file(rel_path, full_path, check_size=check_size))
        if check_size and aggregate:
            try:
                total += full_path.stat().st_size
            except OSError:
                pass

    if aggregate and total > MAX_TOTAL_BYTES:
        findings.append(
            Finding(
                "(whole change)",
                "aggregate",
                f"{_human(total)} added across the change exceeds the "
                f"{_human(MAX_TOTAL_BYTES)} ceiling",
            )
        )
    return findings


# --- Git plumbing -----------------------------------------------------------


def _git(*args: str, repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GuardError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def resolve_merge_base(base_ref: str, repo_root: Path = REPO_ROOT) -> str:
    return _git("merge-base", base_ref, "HEAD", repo_root=repo_root).strip()


def added_paths_since(base: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Repository-relative paths this change ADDS, uncommitted ones included.

    Two sources, because neither alone sees the whole change. `git diff`
    covers committed and staged additions, which is what CI sees on a pushed
    branch. `git ls-files --others` covers a file that is merely sitting in
    the working tree, which is what the motivating incident actually looked
    like: 3.1 GB untracked, unignored, and one `git add -A` from history.
    Gitignore is respected, so an artifact under an ignored directory is
    already contained and is not reported twice.
    """
    diff = _git(
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=A",
        base,
        repo_root=repo_root,
    )
    paths = [item for item in diff.split("\0") if item]
    others = _git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        repo_root=repo_root,
    )
    paths.extend(item for item in others.split("\0") if item)
    return paths


def tracked_paths(repo_root: Path = REPO_ROOT) -> list[str]:
    output = _git("ls-files", "-z", repo_root=repo_root)
    return [item for item in output.split("\0") if item]


# --- Reporting --------------------------------------------------------------


_RULE_HEADLINE = {
    "signature": "matches a blocked container format",
    "binary": "is binary content of a type this repository does not track",
    "size": "is too large to add",
    "aggregate": "adds too many bytes in total",
}


def report(findings: list[Finding], stream) -> None:
    for finding in findings:
        headline = _RULE_HEADLINE.get(finding.rule, finding.rule)
        print(f"  {finding.path}: {headline} ({finding.detail})", file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when a change adds a credential-bearing or bulk binary "
            "artifact to the repository."
        )
    )
    parser.add_argument(
        "--base-ref",
        default="origin/dev",
        help="Ref the change is measured against (default: origin/dev).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository to inspect (default: the one holding this script).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Inspect every tracked file instead of only added ones. The "
        "aggregate ceiling does not apply: it describes a change, not a tree.",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PATH",
        help="Inspect these repository-relative paths instead of diffing.",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    try:
        allowlist = load_allowlist(repo_root)

        if args.paths:
            scope = "the named paths"
            findings = scan_paths(
                args.paths, allowlist, repo_root=repo_root, aggregate=False
            )
            counted = len(args.paths)
        elif args.all:
            scope = "the tracked tree"
            paths = tracked_paths(repo_root)
            findings = scan_paths(
                paths, allowlist, repo_root=repo_root, aggregate=False
            )
            counted = len(paths)
        else:
            base = resolve_merge_base(args.base_ref, repo_root)
            scope = f"the file(s) added since {base[:12]}"
            paths = added_paths_since(base, repo_root)
            findings = scan_paths(
                paths, allowlist, repo_root=repo_root, aggregate=True
            )
            counted = len(paths)
    except GuardError as error:
        print(f"FAIL: binary-artifact guard cannot run: {error}", file=sys.stderr)
        if not args.paths and not args.all:
            print(
                "Pass --base-ref explicitly, or fetch the base branch first "
                "(CI needs fetch-depth: 0).",
                file=sys.stderr,
            )
        return 1

    if findings:
        print(
            f"FAIL: {len(findings)} blocked artifact finding(s) in {scope}:",
            file=sys.stderr,
        )
        report(findings, sys.stderr)
        print(f"\n{REMEDY}", file=sys.stderr)
        print(
            "Context: bead enhancedchannelmanager-6mqn5. A restore drill left "
            "an ECMBKENC-encrypted backup envelope holding a live provider "
            "credential in the working tree of this public repository.",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: no blocked artifacts across {counted} path(s) in {scope}.")
    if allowlist:
        print(
            f"{len(allowlist)} path(s) exempted by {ALLOWLIST_PATH}: "
            + ", ".join(sorted(allowlist))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
