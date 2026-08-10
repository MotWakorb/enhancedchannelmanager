#!/usr/bin/env python3
"""Fail CI when a NEW personal identifier appears in tracked text.

The PO's personal email address is in this public repository's git history.
That exposure is accepted and will NOT be rewritten (PO instruction
2026-08-10, bead `enhancedchannelmanager-il1xz`). What this guard enforces
is the other half of that decision: no new instances get added.

The identifier classes below are the ones a 2026-08-01 manual PII sweep
actually found in this repo. They are not a general PII taxonomy, and the
guard deliberately does not try to be one.

## This is a ratchet, not a cliff

Same reasoning as `scripts/check_em_dashes.py`: a guard that red-lines the
existing repo on day one gets disabled within a week. Pre-existing
identifiers are tolerated on untouched lines; only added lines are scanned.
Text moved or rewritten onto an added line is checked again. This matches the
existing em-dash ratchet and keeps the decision local to content introduced by
the pull request.

## What counts as a violation

  personal-email     An address whose domain is not a documentation or
                     service-account domain. Bead records carry a committer
                     identity (`noreply@anthropic.com`), and the docs use
                     `example.com` throughout, so a bare email regex here
                     would be pure noise. See IMPERSONAL_EMAIL_DOMAINS.

  local-user-path    An absolute path into a real account's home directory
                     (`/home/<user>/`, `/Users/<user>/`, `C:\\Users\\<user>\\`).
                     Placeholder account names are exempt; see
                     PLACEHOLDER_ACCOUNT_NAMES.

  private-host-url   A host under a personal-share or ephemeral-tunnel
                     provider. The 08-01 sweep found a privately hosted
                     mockup URL keyed to the PO's personal account slug.
                     See PRIVATE_HOST_SUFFIXES.

  known-identifier   A specific canonical account token from
                     KNOWN_IDENTIFIER_HASHES: ASCII alphanumeric chunks may
                     be joined by `.`, `_`, or `-`. Hash creation and scanning
                     use this same whole-token grammar, so separator-bearing
                     names work without substring matches.
                     operator's account names and the real provider account
                     names the sweep found. Stored as SHA-256 of the
                     casefolded token, never as plaintext, because writing
                     the identifiers into a tracked file is the exact thing
                     this guard exists to prevent. Add one with
                     `--hash-term`.

Not every class is regex-reachable. The operator's Dispatcharr sub-account
named `home` cannot be listed without flagging the English word, so it is
knowingly uncovered. Naming the gap is better than a rule that cries wolf.

## Scan surface

  docs/**/*.md              the narrative surface the sweep found PII in
  README.md, CHANGELOG.md, CLAUDE.md
  .beads/*.jsonl(.bak)      tracked, re-committed on every bead operation
  .beads/*.md

Source trees are OUT of scope. Their PII risk is low and their fixture
density is high (test data is full of synthetic names, emails, and paths),
so including them buys little and costs false positives.

Unlike the em-dash guard, code blocks and inline code spans are NOT exempt.
A personal email inside a fenced block is still a personal email.

`.beads/*.jsonl` is parsed as JSON and scanned field by field, skipping the
identity fields (`owner`, `created_by`, `assignee`, `author`). Those legitimately
carry a committer identity on every single record; scanning them would fail
every PR that creates a bead. A line that does not parse as JSON is scanned
raw, so a malformed export fails closed rather than silently unscanned.

## Reporting

Matches are replaced by the constant `REDACTED` in output. This runs in a
public repository's CI logs, so even a prefix, suffix, or exact-length mask
would disclose information about the value. The path, line, and rule name are
enough to find it in a local checkout.

An in-scope file that is not strict UTF-8, contains a NUL byte, or cannot be
read fails closed as `unscannable`. Its bytes and decoding error are never
printed.

## Suppression

There is none, on purpose. An identifier that must stay gets redacted, not
annotated. If a rule is genuinely wrong, fix the rule's allowlist.

## Usage

    python scripts/check_pii.py                     # ratchet vs origin/dev
    python scripts/check_pii.py --base-ref origin/main
    python scripts/check_pii.py --all               # full inventory
    python scripts/check_pii.py --paths docs/x.md   # scan named paths in full
    python scripts/check_pii.py --hash-term <term>  # hash for the deny list

Exits 0 when clean, 1 when a file gained an identifier.

## Companion arm

Bead `enhancedchannelmanager-fu6yd` covers the same bar for screenshots
under `docs/images/`. It slots in here rather than starting over: add an
image kind to `_kind_for`, register a segmenter in `_SEGMENTERS` that
yields text extracted from the image, and everything downstream (rules,
added-line ratchet, zero-value reporting, CI wiring, `--all`, `--paths`) applies
unchanged. See the markers below.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BEAD_REF = "enhancedchannelmanager-il1xz"


# --- Scan surface -----------------------------------------------------------

PRUNED_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
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

# (repo-relative root, glob) pairs defining the scanned surface.
SCAN_GLOBS: tuple[tuple[str, str], ...] = (
    ("docs", "**/*.md"),
    (".beads", "*.md"),
    (".beads", "*.jsonl"),
    (".beads", "*.jsonl.bak"),
)

# Top-level Markdown scanned individually; a root `*.md` glob would sweep
# vendored trees.
ROOT_MARKDOWN = ("README.md", "CHANGELOG.md", "CLAUDE.md")


def _kind_for(rel_path: str) -> str | None:
    """Return the segmenter kind for a repo-relative path, or None to skip."""
    if rel_path.endswith(".jsonl") or rel_path.endswith(".jsonl.bak"):
        return "beads-jsonl"
    if rel_path.endswith(".md"):
        return "markdown"
    # fu6yd seam: return "image" for docs/images/**/*.png here.
    return None


def _is_pruned(rel_path: str) -> bool:
    return any(part in PRUNED_DIR_NAMES for part in Path(rel_path).parts)


def _in_scan_surface(rel_path: str) -> bool:
    if _is_pruned(rel_path):
        return False
    if rel_path in ROOT_MARKDOWN:
        return True
    path = Path(rel_path)
    for root, glob in SCAN_GLOBS:
        try:
            relative = path.relative_to(Path(root))
        except ValueError:
            continue
        if relative.match(glob.removeprefix("**/")):
            return True
    return False


def _iter_scan_surface() -> Iterator[str]:
    """Yield every repo-relative path inside the scan surface."""
    root = REPO_ROOT
    seen: set[str] = set()
    for name in ROOT_MARKDOWN:
        if (root / name).is_file():
            seen.add(name)
    for sub, glob in SCAN_GLOBS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in base.glob(glob):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if not _is_pruned(rel):
                seen.add(rel)
    yield from sorted(seen)


# --- Rule: personal-email ---------------------------------------------------

# Local part is >= 2 characters. A single-character local part is almost
# always a false read of something else: `.beads/issues.jsonl` carries
# `\n@app.post` from quoted FastAPI decorators, and `.post` is a real TLD.
_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9_%+-][A-Za-z0-9._%+-]*)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b"
)

# Domains that cannot belong to a private individual, or that are reserved
# for documentation. Each entry is here because it is present in the repo
# today or is reserved by RFC 6761.
IMPERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "example.com",  # used throughout docs/ for sample recipients
        "example.org",
        "example.net",
        "example.edu",
        "anthropic.com",  # `noreply@anthropic.com`, the bead committer identity
        "users.noreply.github.com",  # GitHub commit identity
        "x.co",  # short synthetic domains in the email-validation
        "x.com",  # test fixtures quoted inside bead descriptions
    }
)

# RFC 6761 reserved TLDs. Nothing under these can resolve to a real mailbox.
RESERVED_EMAIL_TLDS = frozenset({"example", "test", "invalid", "localhost"})

# Role mailboxes address an organisation, not a person.
IMPERSONAL_EMAIL_LOCAL_PARTS = frozenset(
    {
        "abuse",
        "admin",
        "alerts",
        "archive",
        "donotreply",
        "do-not-reply",
        "hostmaster",
        "info",
        "no-reply",
        "noreply",
        "oncall",
        "ops",
        "postmaster",
        "security",
        "support",
        "webmaster",
    }
)


def find_personal_emails(segment: str) -> list[str]:
    found = []
    for match in _EMAIL_RE.finditer(segment):
        local, domain = match.group(1).casefold(), match.group(2).casefold()
        if len(local) < 2:
            continue
        if local in IMPERSONAL_EMAIL_LOCAL_PARTS:
            continue
        if domain in IMPERSONAL_EMAIL_DOMAINS:
            continue
        if domain.rsplit(".", 1)[-1] in RESERVED_EMAIL_TLDS:
            continue
        found.append(match.group(0))
    return found


# --- Rule: local-user-path --------------------------------------------------

# The account name may not end in punctuation: `/home/appuser.` at the end of
# a sentence must resolve to the account `appuser`, not to `appuser.`, or the
# placeholder allowlist below silently stops matching.
_HOME_PATH_RE = re.compile(
    r"(?:/home/|/Users/|[A-Za-z]:\\Users\\)([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
)

# Account names that name a role or a placeholder rather than a person.
# `<user>`, `$USER`, `${USER}`, `%USERPROFILE%`, and `/home/...` do not start
# with an alphanumeric, so the pattern never reaches them.
PLACEHOLDER_ACCOUNT_NAMES = frozenset(
    {
        "admin",
        "administrator",
        "alpine",
        "app",
        "appuser",
        "builder",
        "ci",
        "codespace",
        "debian",
        "default",
        "docker",
        "ec2-user",
        "foo",
        "github",
        "jenkins",
        "me",
        "node",
        "nobody",
        "public",
        "root",
        "runner",
        "someuser",
        "test",
        "testuser",
        "ubuntu",
        "user",
        "username",
        "users",
        "vscode",
        "your-user",
        "your_user",
        "youruser",
    }
)


def find_local_user_paths(segment: str) -> list[str]:
    found = []
    for match in _HOME_PATH_RE.finditer(segment):
        if match.group(1).casefold() in PLACEHOLDER_ACCOUNT_NAMES:
            continue
        found.append(match.group(0))
    return found


# --- Rule: private-host-url -------------------------------------------------

# Hosting providers that mint a per-account or per-session subdomain. A URL
# under one of these is by construction someone's private endpoint.
PRIVATE_HOST_SUFFIXES = (
    "chatgpt.site",
    "loca.lt",
    "ngrok-free.app",
    "ngrok.app",
    "ngrok.io",
    "pagekite.me",
    "serveo.net",
    "trycloudflare.com",
    "ts.net",
)

_PRIVATE_HOST_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]+\.)+(?:"
    + "|".join(re.escape(suffix) for suffix in PRIVATE_HOST_SUFFIXES)
    + r")\b",
    re.IGNORECASE,
)


def find_private_host_urls(segment: str) -> list[str]:
    return [match.group(0) for match in _PRIVATE_HOST_RE.finditer(segment)]


# --- Rule: known-identifier -------------------------------------------------

# SHA-256 of the casefolded token. Plaintext would defeat the purpose: this
# file is tracked in the same public repository. Compute a new entry with
# `python scripts/check_pii.py --hash-term <term>` and describe the class it
# belongs to, never the value.
KNOWN_IDENTIFIER_HASHES: dict[str, str] = {
    "9b38e2bcd1bf3e44260621f8f6ccca69e3be9fdccbcddf226b13a179556af6bf": (
        "operator personal domain stem"
    ),
    "1c57e16744d964f064998ff458aa129c38a981581ce0f587859c09b313c522e2": (
        "operator local account name"
    ),
    "0a61b20eb32708fef1dfce0459cf03f52c2c903d52e6db8a1416c6363afc363c": (
        "operator personal cloud-app account slug"
    ),
    "c1e2b0e9248e2cf67de5ead12d0da01826e787b043cacc473c57a73ad602ca07": (
        "real Dispatcharr sub-account name on the operator instance"
    ),
    "751ee0b9444ba59fd1bceb642da4a9e9c2a935ed0fcf4a475af1d7985b401648": (
        "real Dispatcharr sub-account name on the operator instance"
    ),
    "8d95df079d16b2875a9edf972493c434e04323bed5c851dbae719313dd3c9ddf": (
        "real EPG provider account name on the operator instance"
    ),
}

_IDENTIFIER_TOKEN_PATTERN = r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*"
_IDENTIFIER_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9]){_IDENTIFIER_TOKEN_PATTERN}(?![A-Za-z0-9])"
)
_IDENTIFIER_TOKEN_FULL_RE = re.compile(rf"{_IDENTIFIER_TOKEN_PATTERN}\Z")


def hash_term(term: str) -> str:
    canonical = term.strip().casefold()
    if _IDENTIFIER_TOKEN_FULL_RE.fullmatch(canonical) is None:
        raise ValueError(
            "term must follow the identifier token grammar: ASCII alphanumeric "
            "chunks joined by '.', '_', or '-'"
        )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_known_identifiers(segment: str) -> list[str]:
    found: list[str] = []
    for token_match in _IDENTIFIER_TOKEN_RE.finditer(segment):
        token = token_match.group(0)
        # A separator may be part of an account name or may delimit that name
        # inside a host/path. Check every separator-bounded component sequence,
        # longest first. We never slice inside an alphanumeric component, so a
        # deny term cannot match an accidental character substring.
        components = list(re.finditer(r"[A-Za-z0-9]+", token))
        matched_spans: set[tuple[int, int]] = set()
        for width in range(len(components), 0, -1):
            for start in range(len(components) - width + 1):
                first = components[start]
                last = components[start + width - 1]
                candidate = token[first.start() : last.end()]
                if hash_term(candidate) not in KNOWN_IDENTIFIER_HASHES:
                    continue
                span = (first.start(), last.end())
                if span not in matched_spans:
                    found.append(candidate)
                    matched_spans.add(span)
    return found


RULES: tuple[tuple[str, Callable[[str], list[str]]], ...] = (
    ("personal-email", find_personal_emails),
    ("local-user-path", find_local_user_paths),
    ("private-host-url", find_private_host_urls),
    ("known-identifier", find_known_identifiers),
)


# --- Segmenters -------------------------------------------------------------


def markdown_segments(text: str) -> Iterator[tuple[int, str]]:
    """Every line, verbatim. Code blocks are not exempt from this guard."""
    for number, line in enumerate(text.splitlines(), start=1):
        yield number, line


# Keys whose value is a committer or assignee identity. Bead records carry
# these on every row, so scanning them would fail every bead-creating PR.
BEAD_IDENTITY_KEYS = frozenset({"owner", "created_by", "assignee", "author", "actor"})


def _walk_json_strings(node: object) -> Iterator[str]:
    """Yield every string in `node` except those under an identity key."""
    if isinstance(node, dict):
        for child_key, child in node.items():
            if child_key in BEAD_IDENTITY_KEYS:
                continue
            yield from _walk_json_strings(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_json_strings(child)
    elif isinstance(node, str):
        yield node


def beads_jsonl_segments(text: str) -> Iterator[tuple[int, str]]:
    """One segment per free-text field of each JSONL record.

    A line that will not parse is yielded raw, so a malformed export fails
    closed rather than passing unscanned.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            yield number, line
            continue
        for value in _walk_json_strings(record):
            yield number, value


# fu6yd seam: register an "image" segmenter here yielding text extracted
# from a screenshot. Everything downstream is kind-agnostic.
_SEGMENTERS = {
    "markdown": markdown_segments,
    "beads-jsonl": beads_jsonl_segments,
}


# --- Violations -------------------------------------------------------------


class Violation:
    __slots__ = ("path", "line", "rule", "match")

    def __init__(self, path: str, line: int, rule: str, match: str) -> None:
        self.path = path
        self.line = line
        self.rule = rule
        self.match = match

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Violation({self.path!r}, {self.line}, {self.rule!r})"


def redact(value: str) -> str:
    """Return a constant marker with no content or exact-length oracle."""
    del value
    return "REDACTED"


def scan_text(rel_path: str, text: str, kind: str) -> list[Violation]:
    """Return every violation in `text`, allowlists already applied."""
    segmenter = _SEGMENTERS[kind]
    violations: list[Violation] = []
    for number, segment in segmenter(text):
        if not segment:
            continue
        for rule_name, finder in RULES:
            for found in finder(segment):
                violations.append(Violation(rel_path, number, rule_name, found))
    return violations


class UnscannableFile(Exception):
    """An in-scope path cannot safely be treated as UTF-8 text."""

    def __init__(self, rel_path: str) -> None:
        super().__init__(rel_path)
        self.rel_path = rel_path


def _read(rel_path: str) -> str:
    try:
        raw = (REPO_ROOT / rel_path).read_bytes()
        if b"\x00" in raw:
            raise UnscannableFile(rel_path)
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise UnscannableFile(rel_path) from error


def scan_paths(rel_paths: Iterable[str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path in rel_paths:
        kind = _kind_for(rel_path)
        if kind is None:
            continue
        text = _read(rel_path)
        violations.extend(scan_text(rel_path, text, kind))
    return violations


# --- Git ---------------------------------------------------------------------


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
    return _git("merge-base", base_ref, "HEAD").strip()


def added_lines(base: str, rel_path: str) -> Iterator[tuple[int, str]]:
    """Yield HEAD line numbers and text added since `base`."""
    # Validate the whole in-scope file before asking Git for a textual diff.
    # Otherwise Git may emit a binary marker (NUL) or subprocess may raise a
    # decoding exception (invalid UTF-8), and both used to silently bypass the
    # added-line scanner.
    current_text = _read(rel_path)
    untracked = set(_git("ls-files", "--others", "--exclude-standard").splitlines())
    if rel_path in untracked:
        yield from enumerate(current_text.splitlines(), start=1)
        return
    diff = _git("diff", "--unified=0", "--no-color", base, "--", rel_path)
    head_line = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,\d+)?", line)
            if match:
                head_line = int(match.group(1))
                in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            yield head_line, line[1:]
            head_line += 1
        elif not line.startswith("-"):
            head_line += 1


def scan_added_lines(base: str, rel_path: str) -> list[Violation]:
    kind = _kind_for(rel_path)
    if kind is None:
        return []
    violations: list[Violation] = []
    for line_number, text in added_lines(base, rel_path):
        for violation in scan_text(rel_path, text, kind):
            violation.line = line_number
            violations.append(violation)
    return violations


# --- Ratchet -----------------------------------------------------------------


class NewOccurrence:
    __slots__ = ("path", "rule", "redacted", "lines")

    def __init__(
        self,
        path: str,
        rule: str,
        redacted: str,
        lines: list[int],
    ) -> None:
        self.path = path
        self.rule = rule
        self.redacted = redacted
        self.lines = lines


# --- Reporting ---------------------------------------------------------------


def _report_new(results: list[NewOccurrence], stream) -> None:
    for item in results:
        lines = ", ".join(str(n) for n in item.lines[:8])
        if len(item.lines) > 8:
            lines += ", ..."
        print(
            f"  {item.path}: [{item.rule}] {item.redacted} "
            f"(added line(s) {lines})",
            file=stream,
        )


def _report_inventory(violations: list[Violation], stream) -> None:
    counts = Counter((v.path, v.rule) for v in violations)
    for (path, rule), count in sorted(counts.items()):
        print(f"  {path}: [{rule}] {count} occurrence(s)", file=stream)


def _report_unscannable(error: UnscannableFile, stream) -> None:
    """Report metadata only; never expose file bytes or decoder details."""
    print(f"FAIL: {error.rel_path}: [unscannable]", file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ratchet guard against new personal identifiers in tracked text."
    )
    parser.add_argument(
        "--base-ref",
        default="origin/dev",
        help="Ref the change is measured against (default: origin/dev).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Report every occurrence in the repo, not just new ones. Clean "
        "inventories exit 0; unscannable in-scope files fail closed.",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PATH",
        help="Scan these repo-relative paths in full instead of diffing.",
    )
    parser.add_argument(
        "--hash-term",
        metavar="TERM",
        help="Print the KNOWN_IDENTIFIER_HASHES entry for TERM and exit.",
    )
    args = parser.parse_args(argv)

    if args.hash_term:
        print(hash_term(args.hash_term))
        return 0

    if args.paths:
        try:
            violations = scan_paths(args.paths)
        except UnscannableFile as error:
            _report_unscannable(error, sys.stderr)
            return 1
        if violations:
            print(
                f"FAIL: {len(violations)} personal identifier(s) in the named paths:",
                file=sys.stderr,
            )
            _report_inventory(violations, sys.stderr)
            return 1
        print("PASS: no personal identifiers in the named paths.")
        return 0

    surface = list(_iter_scan_surface())
    try:
        inventory = scan_paths(surface)
    except UnscannableFile as error:
        _report_unscannable(error, sys.stderr)
        return 1

    if args.all:
        print(
            f"Full inventory: {len(inventory)} occurrence(s) across "
            f"{len({v.path for v in inventory})} file(s) of "
            f"{len(surface)} scanned."
        )
        _report_inventory(inventory, sys.stdout)
        print(
            "\nValues are not printed. This is the tolerated-debt inventory "
            f"(bead {BEAD_REF}); the default ratchet mode is the gate."
        )
        return 0

    try:
        base = resolve_merge_base(args.base_ref)
    except RuntimeError as error:
        print(
            f"FATAL: cannot resolve base ref {args.base_ref!r}: {error}",
            file=sys.stderr,
        )
        print(
            "Pass --base-ref explicitly, or fetch the base branch first "
            "(CI needs fetch-depth: 0).",
            file=sys.stderr,
        )
        return 1

    results: list[NewOccurrence] = []
    changed = _git("diff", "--name-only", "--diff-filter=ACMR", base).splitlines()
    changed.extend(_git("ls-files", "--others", "--exclude-standard").splitlines())
    for rel_path in sorted(path for path in changed if _in_scan_surface(path)):
        try:
            added_violations = scan_added_lines(base, rel_path)
        except UnscannableFile as error:
            _report_unscannable(error, sys.stderr)
            return 1
        for violation in added_violations:
            results.append(
                NewOccurrence(
                    path=rel_path,
                    rule=violation.rule,
                    redacted=redact(violation.match),
                    lines=[violation.line],
                )
            )

    if results:
        print(
            f"FAIL: {len(results)} personal identifier(s) added since "
            f"{base[:12]}:",
            file=sys.stderr,
        )
        _report_new(results, sys.stderr)
        print(
            "\nValues are redacted because CI logs are public. Open the file at "
            "the reported line to see what matched.",
            file=sys.stderr,
        )
        print(
            "Redact it: replace the identifier with a role placeholder and "
            "keep the surrounding context. There is no suppression comment "
            f"for this guard (bead {BEAD_REF}). If a rule is genuinely wrong, "
            "widen its allowlist in scripts/check_pii.py and say why.",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS: no personal identifier added to the {len(surface)} scanned "
        f"file(s) since {base[:12]}."
    )
    print(
        f"Pre-existing occurrences tolerated by the ratchet: {len(inventory)} "
        f"in {len({v.path for v in inventory})} file(s). "
        "Run with --all to list them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
