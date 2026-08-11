#!/usr/bin/env python3
"""Classify a changed-file set for the CI gates that read it.

Origin: bead enhancedchannelmanager-5rwzy. Extended for the published
user-guide site in bead enhancedchannelmanager-t4d5w.

Two independent verdicts come out of one changed-file set:

    code_paths_changed   at least one changed path is an input to a code gate
    docs_site_affected   something the mkdocs user-guide site is built from
                         changed, so the site needs rebuilding and republishing

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
gates its expensive steps on the output of this classifier. Only an explicitly
inert machine-state change can take the cheap no-op path.

## The code_paths_changed rule

The verdict is false only when EVERY changed path is an approved root
machine-generated beads state file. Every other path is code-gate input,
regardless of suffix, case, location, or intended use. Anything absent,
unknown, or ambiguous is treated as code.

## The docs_site_affected rule

The published user-guide site is built from a much narrower slice of the tree
than `docs/`: `mkdocs.yml` excludes every internal directory. A change is
site-affecting when ANY changed path is one the site build reads:

    docs/user_guide/**                any published page
    docs/images/user_guide/**         any published screenshot
    docs/index.md                     the site landing page
    mkdocs.yml                        the site definition itself
    docs/requirements-docs.txt        the pinned build toolchain
    .github/workflows/docs-pages.yml  the build-and-deploy workflow

That list used to live in the `paths:` filter of `docs-pages.yml`. It lives
here now, and that workflow reads this output, so there is one definition.

## Fail-open, never fail-closed

The two verdicts fail open in OPPOSITE directions, because their dangerous
answers are opposite.

For `code_paths_changed`, misclassifying code as inert turns a required check
green without running the work it is named for; misclassifying documentation
as code only costs runner minutes. So every ambiguous input (empty file list,
unreadable input) resolves to `code_paths_changed=true`, and callers gate work on
`code_paths_changed != 'false'` so an absent or empty output still runs the real work.

For `docs_site_affected`, the dangerous answer is a `false` that skips a site
rebuild and leaves the published site stale behind the merged content. So
ambiguous input resolves to `docs_site_affected=true`, and `docs-pages.yml`
gates on `!= 'false'` so an absent or empty output still builds.

Either way the script exits 0 unconditionally: a classifier hiccup must never
be able to skip a dependent job by dying.

## Usage

    python scripts/classify_changed_paths.py --files-from changed_files.json
    git diff --name-only --no-renames -z origin/dev...HEAD | \
        python scripts/classify_changed_paths.py --git-z

Writes two `key=value` lines to stdout in the form GitHub Actions
`$GITHUB_OUTPUT` consumes:

    code_paths_changed=true|false
    docs_site_affected=true|false

Consumers must read the key they care about by name, not by line position.
Diagnostics go to stderr so stdout stays machine-parseable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The only code-gate exemption is root machine-generated beads state. Beads
# configuration, nested artifacts, documentation, images, and every unknown
# path run the gates.
# The inputs the published user-guide site is built from. This is the list
# `.github/workflows/docs-pages.yml` used to carry in its own `paths:` filter;
# that workflow now reads the `docs_site_affected` output instead, so the
# definition has exactly one home. `.github/workflows/docs-pages.yml` itself
# is on the list because editing the build-and-deploy workflow is a reason to
# re-run it, which is how the original filter behaved.
DOCS_SITE_PREFIXES = (
    "docs/user_guide/",
    "docs/images/user_guide/",
)
DOCS_SITE_FILES = (
    "docs/index.md",
    "mkdocs.yml",
    "docs/requirements-docs.txt",
    ".github/workflows/docs-pages.yml",
)


def normalise_path(path: str) -> str:
    """Return a canonical Git/API path unchanged, or empty when ambiguous.

    POSIX backslash and whitespace are filename bytes, not quoting. Never
    rewrite them into a different repository path. Inputs outside the API/Git
    canonical form fail toward running code gates.
    """
    if not path or path != path.strip():
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return ""
    if path.startswith(("/", "./")) or "//" in path:
        return ""
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return ""
    return path


def is_inert_path(path: str) -> bool:
    """True only for explicitly audited paths that cannot affect code gates."""
    normalised = normalise_path(path)
    if not normalised:
        return False
    if normalised == ".beads/metadata.json":
        return True
    if normalised.startswith(".beads/"):
        name = normalised.removeprefix(".beads/")
        return "/" not in name and (name.endswith(".jsonl") or name.endswith(".jsonl.bak"))
    return False


def classify(paths: list[str]) -> tuple[bool, list[str]]:
    """Return (code_paths_changed, code_paths) for a changed-file set.

    `code_paths` is every path that forced the code verdict, so the caller
    can print the evidence rather than an unexplained boolean.
    """
    if not paths:
        return True, []
    code_paths = [p for p in paths if not is_inert_path(p)]
    return bool(code_paths), code_paths


def is_docs_site_path(path: str) -> bool:
    """True when changing `path` changes what the mkdocs site publishes."""
    normalised = normalise_path(path)
    if not normalised:
        return False
    if normalised in DOCS_SITE_FILES:
        return True
    return normalised.startswith(DOCS_SITE_PREFIXES)


def classify_docs_site(paths: list[str]) -> tuple[bool, list[str]]:
    """Return (docs_site_affected, site_paths) for a changed-file set.

    Fails open to True on an empty or undetermined set: a missed rebuild
    leaves the published site stale behind merged content, whereas a
    needless rebuild costs a runner minute.
    """
    if not paths:
        return True, []
    # Any invalid path makes both decisions fail safe; it cannot be proved
    # irrelevant to the site from a lossy or ambiguous representation.
    if any(not normalise_path(path) for path in paths):
        return True, []
    site_paths = [p for p in paths if is_docs_site_path(p)]
    return bool(site_paths), site_paths


def _parse_json_paths(raw: bytes) -> list[str] | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or not payload:
        return None
    if not all(isinstance(path, str) and normalise_path(path) for path in payload):
        return None
    return payload


def _parse_git_z_paths(raw: bytes) -> list[str] | None:
    if not raw or not raw.endswith(b"\0"):
        return None
    fields = raw[:-1].split(b"\0")
    try:
        paths = [field.decode("utf-8") for field in fields]
    except UnicodeDecodeError:
        return None
    if not paths or not all(normalise_path(path) for path in paths):
        return None
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify a changed-file set for the CI gates, and emit "
            "code_paths_changed=true|false and docs_site_affected=true|false for "
            "GitHub Actions."
        )
    )
    parser.add_argument(
        "--files-from",
        type=Path,
        default=None,
        help="File holding a UTF-8 JSON array of changed paths. Defaults to stdin.",
    )
    parser.add_argument(
        "--git-z",
        action="store_true",
        help="Read NUL-delimited paths from `git diff -z` on stdin.",
    )
    args = parser.parse_args(argv)

    if args.git_z and args.files_from is not None:
        paths = None
    elif args.files_from is None:
        raw = sys.stdin.buffer.read()
        paths = _parse_git_z_paths(raw) if args.git_z else _parse_json_paths(raw)
    else:
        try:
            raw = args.files_from.read_bytes()
            paths = _parse_json_paths(raw)
        except OSError as error:
            print(
                f"::warning::could not read {args.files_from}: {error}. "
                f"Treating the change as code so every gate runs, and as "
                f"site-affecting so the docs site still rebuilds.",
                file=sys.stderr,
            )
            print("code_paths_changed=true")
            print("docs_site_affected=true")
            return 0
    if paths is None:
        print(
            "::warning::changed-path input was malformed, empty, or noncanonical. "
            "Treating the change as code and site-affecting.",
            file=sys.stderr,
        )
        print("code_paths_changed=true")
        print("docs_site_affected=true")
        return 0
    code_paths_changed, code_paths = classify(paths)
    docs_site_affected, site_paths = classify_docs_site(paths)

    if not code_paths_changed:
        print(
            f"{len(paths)} changed path(s), all on the explicit inert allowlist. "
            f"Code gates have nothing to analyse.",
            file=sys.stderr,
        )
    else:
        preview = ", ".join(code_paths[:10])
        if len(code_paths) > 10:
            preview += f", and {len(code_paths) - 10} more"
        print(
            f"{len(code_paths)} code-gate path(s) changed: {preview}",
            file=sys.stderr,
        )

    if site_paths:
        site_preview = ", ".join(site_paths[:10])
        if len(site_paths) > 10:
            site_preview += f", and {len(site_paths) - 10} more"
        print(
            f"{len(site_paths)} path(s) feed the published user-guide site: "
            f"{site_preview}",
            file=sys.stderr,
        )
    else:
        print(
            "No changed path feeds the published user-guide site.",
            file=sys.stderr,
        )

    print(f"code_paths_changed={'true' if code_paths_changed else 'false'}")
    print(f"docs_site_affected={'true' if docs_site_affected else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
