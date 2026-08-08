#!/usr/bin/env python3
"""Classify a changed-file set as documentation-only or code.

Origin: bead enhancedchannelmanager-5rwzy.

## Why this exists

`dev` and `main` branch protection require seven status checks. Before this
script, two workflows could emit the same required check name on one commit:
`test.yml` ran on `paths-ignore: ['**.md', '.beads/**']` and
`docs-only-pass.yml` ran on `paths: ['**.md', '.beads/**']`. Those filters are
not complements. A pull request that touches BOTH code and Markdown matches
both, so every required context existed twice: once real, once a permanently
green `echo`. Observed live on PR #797, where `Backend Tests` reported
`failure` and `success` on the same commit.

The fix is one source of truth per context. Every job that emits a required
check now runs on every pull request, so the context exists exactly once, and
gates its expensive steps on the output of this classifier. On a genuinely
documentation-only change the job does a cheap no-op and passes honestly.

## The rule

A change is documentation-only when EVERY changed path matches one of the
globs the CI workflows used to ignore:

    **.md        any file whose name ends in `.md`, at any depth
    .beads/**    anything under the beads issue-tracking directory

Anything else, including a change with no files at all, is treated as code.

## Fail-open, never fail-closed

Misclassifying code as documentation is the dangerous direction: it turns a
required check green without running the work it is named for. Misclassifying
documentation as code only costs runner minutes. So every ambiguous input
(empty file list, unreadable input) resolves to `docs_only=false`, and the
script exits 0 unconditionally so a classifier hiccup can never skip a
dependent job. Callers gate work on `docs_only != 'true'` for the same reason:
an absent or empty output runs the real work.

## Usage

    python scripts/classify_changed_paths.py --files-from changed_files.txt
    git diff --name-only origin/dev...HEAD | python scripts/classify_changed_paths.py

Writes `docs_only=true` or `docs_only=false` to stdout in the
`key=value` form GitHub Actions `$GITHUB_OUTPUT` consumes. Diagnostics go to
stderr so stdout stays machine-parseable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The globs the CI workflows treat as documentation. Keep this list as the
# single definition of the rule: it used to live in three `paths` /
# `paths-ignore` blocks that drifted out of sync with each other.
DOC_SUFFIX = ".md"
DOC_PREFIXES = (".beads/",)


def is_doc_path(path: str) -> bool:
    """True when `path` is one of the documentation-only paths CI may skip.

    Paths arrive repo-relative and POSIX-separated, which is what both the
    GitHub compare/pull-request file APIs and `git diff --name-only` emit.
    Backslashes are normalised anyway so a Windows-style path cannot slip
    past the `.beads/` prefix test.
    """
    normalised = path.strip().replace("\\", "/")
    # Strip a leading `./` only. `str.lstrip("./")` would eat the leading dot
    # of `.beads/foo` and defeat the prefix test below.
    while normalised.startswith("./"):
        normalised = normalised[2:]
    if not normalised:
        return False
    if normalised.startswith(DOC_PREFIXES):
        return True
    # `**.md` matches on the name, not the directory, so a `.md` file at any
    # depth counts. A file named exactly `.md` has no stem and is not prose.
    name = normalised.rsplit("/", 1)[-1]
    return name.endswith(DOC_SUFFIX) and name != DOC_SUFFIX


def classify(paths: list[str]) -> tuple[bool, list[str]]:
    """Return (docs_only, code_paths) for a changed-file set.

    `code_paths` is every path that forced the code verdict, so the caller
    can print the evidence rather than an unexplained boolean.
    """
    cleaned = [p.strip() for p in paths if p.strip()]
    if not cleaned:
        return False, []
    code_paths = [p for p in cleaned if not is_doc_path(p)]
    return (not code_paths), code_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify a changed-file set as documentation-only or code, and "
            "emit docs_only=true|false for GitHub Actions."
        )
    )
    parser.add_argument(
        "--files-from",
        type=Path,
        default=None,
        help="File holding newline-separated changed paths. Defaults to stdin.",
    )
    args = parser.parse_args(argv)

    if args.files_from is None:
        raw = sys.stdin.read()
    else:
        try:
            raw = args.files_from.read_text(encoding="utf-8")
        except OSError as error:
            print(
                f"::warning::could not read {args.files_from}: {error}. "
                f"Treating the change as code so every gate runs.",
                file=sys.stderr,
            )
            print("docs_only=false")
            return 0

    paths = raw.splitlines()
    docs_only, code_paths = classify(paths)

    if not [p for p in paths if p.strip()]:
        print(
            "::warning::the changed-file set was empty or could not be "
            "determined. Treating the change as code so every gate runs.",
            file=sys.stderr,
        )
    elif docs_only:
        print(
            f"{len(paths)} changed path(s), all documentation. "
            f"Code gates have nothing to analyse.",
            file=sys.stderr,
        )
    else:
        preview = ", ".join(code_paths[:10])
        if len(code_paths) > 10:
            preview += f", and {len(code_paths) - 10} more"
        print(
            f"{len(code_paths)} non-documentation path(s) changed: {preview}",
            file=sys.stderr,
        )

    print(f"docs_only={'true' if docs_only else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
