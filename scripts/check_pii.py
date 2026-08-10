#!/usr/bin/env python3
"""Fail CI when a NEW personal identifier or live credential appears in tracked text.

Two arms share one engine, one ratchet, and one CI step:

  * personal identifiers (bead `enhancedchannelmanager-il1xz`)
  * application credentials (bead `enhancedchannelmanager-h9av3`)

They are separate rule sets over the same scan surface. See "What counts as
a violation" below; the credential arm's rules and the reasoning behind each
are documented at their definitions.

## Arm 1: personal identifiers

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

## Arm 2: ECM-specific credentials

### Division of labour: three layers, no overlap

Generic and provider-shaped secret detection is NOT this arm's job. Three
layers cover this repository, and each is scoped so the others do not have
to repeat it:

  GitHub secret scanning   Partner patterns only (AWS, GitHub, Stripe, and
  + push protection        the rest of the published partner set). Blocks
                           at push time. Knows nothing application-specific.

  detect-secrets 1.5.0     The general layer, pinned, with a committed and
  (.secrets.baseline)      audited `.secrets.baseline`. Provides high
                           entropy detection, keyword detection, and its
                           own provider plugins. Baseline model is the same
                           ratchet idea used here and in check_em_dashes.py.

  THIS ARM                 Only what the two layers above provably cannot
                           know: this application's own credential shapes.

The h9av3 history sweep measured why the split has to be this sharp.
Generic keyword-anchored rules scored **274 hits for 1 true positive**, a
~99.6% false-positive rate. Of 51 distinct high-entropy 43-character values
found by shape alone, **50 were npm lockfile integrity hashes.** A guard
with those numbers gets switched off in a week, and building a second copy
of it here would only double the noise.

So this arm is three rules, and each one earns its place by covering
something measured to be uncovered:

  credential-assignment  One of THIS APPLICATION'S NAMED credential fields
                         (see ECM_CREDENTIAL_FIELDS) assigned a value with
                         the shape of a live secret, plus the MCP key's
                         `?api_key=` URL form. Verified against
                         detect-secrets 1.5.0: an `mcp_api_key` assignment
                         is reported only by the generic Base64HighEntropy
                         and Keyword detectors, which are exactly the two
                         that scored 274:1 here.

  discord-webhook-url    A webhook whose id and token are both real, not
                         the `<id>/<token>` placeholder the docs use.
                         Verified uncovered: detect-secrets ships
                         DiscordBotTokenDetector, which is a different
                         credential, and reports nothing for a webhook URL.

  telegram-bot-token     `<bot id>:<35-character token>`, Telegram's exact
                         issued format. Verified uncovered END TO END:
                         detect-secrets ships TelegramBotTokenDetector and
                         its regex does match, but the default filter
                         pipeline drops every hit. Measured across three
                         token variants in bare, assignment, and API-URL
                         contexts in both `.py` and `.md`: zero findings.

Rules for JWTs and PEM private keys were written, measured against
detect-secrets, found to be exact duplicates of its JwtTokenDetector and
PrivateKeyDetector, and deleted. See the note above the RULES tuple.

### It matches values, never names

This is the constraint the arm lives or dies on. This repository is full of
legitimate references to every one of these field names. `backend/config.py`
declares `mcp_api_key`, `smtp_password`, `telegram_bot_token`, and the Emby
/ Plex / Jellyfin key fields; `docs/` explains them; `.beads/issues.jsonl`
quotes them in bug-report prose today. A guard that fired on the string
`mcp_api_key` would be disabled the day it landed. A field name is only
ever used to decide WHERE to look; the decision to fail is made entirely by
the shape of what follows, in `_looks_secret`.

### What this arm knowingly does not cover

  * **Dispatcharr / SMTP usernames.** A username is low-entropy text. There
    is no value shape to match. The operator's real account names are
    covered instead by the `known-identifier` hashes in arm 1, which is the
    right mechanism for a low-entropy secret.
  * **Telegram chat ids.** A chat id is a bare integer (`-100...` for
    supergroups). Any rule reaching it would flag every id, count, and
    timestamp in `.beads/issues.jsonl`.
  * **Credentials embedded in an IPTV stream path** (`.../<user>/<pass>/<id>`).
    No field name, no reliable delimiter, and the path segments are
    indistinguishable from any other URL path.

### THIS ARM CANNOT SEE HISTORY

Read this before quoting a green run as evidence of anything.

This guard scans the tracked WORKING TREE against a merge base. So does
detect-secrets with a baseline. Neither can see a value that was committed
and later removed, and neither can see an unreachable object.

The h9av3 history sweep found two real `mcp_api_key` values in this
repository's history. **Both were invisible to a working-tree scanner**:
one had its tip cleaned in 2026-05, and the other never had a tip at all,
living only in an unreachable stash. Both have since been confirmed rotated
out, by offline hash comparison rather than by using the credentials, and
the PO has ruled against rewriting history.

So: **"the arm is green" means "this change adds nothing new". It does NOT
mean the repository's history is clean.** Detecting what is already in
history is a separate, on-demand, whole-history scan, and it is not what
runs in CI.

## Scan surface

  docs/**/*.md              the narrative surface the sweep found PII in
  README.md, CHANGELOG.md, CLAUDE.md
  .beads/*.jsonl(.bak)      tracked, re-committed on every bead operation
  .beads/*.md
  .env / .env.<name>        credential-file shapes; see CREDENTIAL_FILE_NAMES
  auth_settings.json        (`.env.example` and friends are exempt)
  settings.json

Source trees are OUT of scope. Their PII risk is low and their fixture
density is high (test data is full of synthetic names, emails, paths, and
deliberately fake credentials), so including them buys little and costs
false positives. The credential arm accepts the same trade: a hardcoded
secret in `backend/` is SAST's job, not this guard's.

The credential-file entries are the exception, and a narrow one. `.gitignore`
already excludes `.env` at every depth, so reaching one of these requires
`git add -f`. They are listed because the cost of the entry is one line and
the cost of the miss is a live credential in a public repository. The
realistic vector for this application is not the file itself but its
CONTENTS pasted into a doc or a bead description, and that is covered by the
Markdown and JSONL surface above.

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

A credential-arm hit means something further than redaction: the value was
published, so it is burned. ROTATE it first, then redact the file.

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

The credential arm did NOT use that seam, and the reason is worth recording
for whoever adds the third arm. `_kind_for` / `_SEGMENTERS` is the FILE-TYPE
seam: it answers "how do I turn these bytes into (line, text) segments?"
The image arm needs it because a PNG has no lines. The credential arm needs
no new segmenter for its primary surface -- Markdown lines and JSONL field
values are already exactly the right granularity -- so it extends `RULES`,
which is the orthogonal seam for "what do I look for in a segment?" The one
place it does touch `_kind_for` is the credential-file surface, and there it
reuses the existing line segmenter rather than adding one. Pick the seam by
what is actually new: new bytes-to-text, or new text-to-violation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BEAD_REF = "enhancedchannelmanager-il1xz"
CREDENTIAL_BEAD_REF = "enhancedchannelmanager-h9av3"


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

# Basenames that are credential material by construction, matched at ANY
# depth (h9av3). These carry the application's own secrets: `/config/
# settings.json` holds DispatcharrSettings, `/config/auth_settings.json`
# holds the JWT signing key.
CREDENTIAL_FILE_NAMES = frozenset({"auth_settings.json", "settings.json"})

# A dotenv file is credential material unless its name says it is a sample.
# `.env.example` is tracked in this repo today and is meant to be.
CREDENTIAL_FILE_EXEMPT_SUFFIXES = (
    ".example",
    ".sample",
    ".template",
    ".dist",
    ".defaults",
)


def _is_credential_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    if name in CREDENTIAL_FILE_NAMES:
        return True
    if name != ".env" and not name.startswith(".env."):
        return False
    return not name.endswith(CREDENTIAL_FILE_EXEMPT_SUFFIXES)


def _kind_for(rel_path: str) -> str | None:
    """Return the segmenter kind for a repo-relative path, or None to skip."""
    if rel_path.endswith(".jsonl") or rel_path.endswith(".jsonl.bak"):
        return "beads-jsonl"
    if rel_path.endswith(".md"):
        return "markdown"
    if _is_credential_file(rel_path):
        # Line-oriented, same as Markdown. See the seam note in the module
        # docstring: this arm needed a new SURFACE, not a new segmenter.
        return "plaintext"
    # fu6yd seam: return "image" for docs/images/**/*.png here.
    return None


def _is_pruned(rel_path: str) -> bool:
    return any(part in PRUNED_DIR_NAMES for part in Path(rel_path).parts)


def _in_scan_surface(rel_path: str) -> bool:
    if _is_pruned(rel_path):
        return False
    if rel_path in ROOT_MARKDOWN:
        return True
    if _is_credential_file(rel_path):
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


def _iter_credential_files(root: Path) -> Iterator[str]:
    """Yield credential-shaped filenames anywhere in the tree.

    These have no fixed home -- `.env` sits at the root, `auth_settings.json`
    in a config directory, `settings.json` under a tool directory -- so this
    is the one part of the surface that needs a walk rather than a glob. The
    walk prunes the same heavy directories the rest of the guard skips, so
    it never descends into `node_modules` or `.git`.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in PRUNED_DIR_NAMES]
        for filename in filenames:
            if not _is_credential_file(filename):
                continue
            yield (Path(dirpath) / filename).relative_to(root).as_posix()


def _iter_scan_surface() -> Iterator[str]:
    """Yield every repo-relative path inside the scan surface."""
    root = REPO_ROOT
    seen: set[str] = set()
    for name in ROOT_MARKDOWN:
        if (root / name).is_file():
            seen.add(name)
    seen.update(_iter_credential_files(root))
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


# --- Rule: credential-assignment (h9av3) ------------------------------------

# This is the rule the credential arm lives or dies on, so the reasoning is
# spelled out rather than implied.
#
# A field NAME is not evidence. `backend/config.py` declares `mcp_api_key`,
# `smtp_password`, and `telegram_bot_token`; `docs/api.md` documents them;
# `.beads/issues.jsonl` discusses them in bug-report prose today. Matching
# the name would fail on the repository as it stands. So the name is used
# only to locate a candidate, and every decision to fail is made by
# `_looks_secret` against the VALUE.
#
# The shapes being defended are this application's own:
#
#   mcp_api_key, JWT signing key    secrets.token_urlsafe(32)  -> 43 chars
#                                   (routers/settings.py, auth/settings.py)
#   Dispatcharr API key             40-character DRF token
#   Emby / Jellyfin API key         32-character hex
#   Plex token                      20-character base64url, `X-Plex-Token=`
#   SMTP / Dispatcharr password     operator-chosen, arbitrary
#
# The last one is why the rule has a floor rather than a fixed length: an
# operator password is whatever they typed. `_looks_secret` therefore asks
# "could this plausibly be a live secret?" and not "is this exactly one of
# the five formats above".

# THIS LIST IS NAMED FIELDS, NOT GENERIC SUFFIXES, AND THAT IS THE WHOLE
# POINT. An earlier draft of this rule anchored on generic suffixes
# (`apikey`, `password`, `secret`, `token`). The h9av3 history sweep
# measured that exact approach across this repository's full history:
#
#   generic keyword-anchored rules   274 hits, 1 true positive (~99.6% FP)
#   bare `token_urlsafe(32)` shape   51 distinct values, 50 of which were
#                                    npm lockfile integrity hashes
#
# A generic anchor is also precisely what `detect-secrets`' KeywordDetector
# and Base64HighEntropyString already provide, so duplicating it would buy
# double findings and an unreadable ratchet rather than coverage.
#
# What survives measurement is anchoring to the application's OWN field
# names. Every entry below is a real field in this codebase, cited to where
# it is declared or generated. Compared with `endswith` on the normalised
# key so that `settings.mcp_api_key`, `ECM_MCP_API_KEY`, and
# `"mcp_api_key"` all resolve to the same field.
#
# Bare `api_key` is deliberately ABSENT: it is the single noisiest token in
# the sweep. The MCP key's URL form is anchored separately below, on the
# query-parameter syntax rather than on the word.
ECM_CREDENTIAL_FIELDS = (
    # MCP integration. secrets.token_urlsafe(32) -> 43 characters,
    # generated at backend/routers/settings.py:2184.
    "mcp_api_key",
    # Dispatcharr. backend/config.py, DispatcharrSettings. Stored plaintext
    # at rest; the API key is a 40-character DRF token.
    "dispatcharr_api_key",
    "dispatcharr_password",
    # Notification channels. backend/config.py.
    "smtp_password",
    "telegram_bot_token",
    # Media servers. backend/config.py. Emby and Jellyfin issue 32-character
    # hex keys; a Plex token is 20 characters of base64url and is the
    # shortest credential this guard covers.
    "emby_api_key",
    "jellyfin_api_key",
    "plex_api_key",
    "plex_token",
    "x_plex_token",
    # Session and JWT signing material. backend/auth/settings.py,
    # _generate_secret_key -> secrets.token_urlsafe(32).
    "secret_key",
    "session_secret",
    "jwt_secret",
    "signing_key",
)

_NORMALIZED_CREDENTIAL_FIELDS = tuple(
    re.sub(r"[^a-z0-9]", "", field) for field in ECM_CREDENTIAL_FIELDS
)

# The value charset is deliberately narrow: the union of base64url
# (`A-Za-z0-9_-`), standard base64 (`+/=`), and hex. `.` is excluded even
# though JWTs use it, because including it turns every dotted module path,
# version string, hostname, and filename in the repository into a candidate.
# JWTs have their own rule below.
#
# The separator match is `[ \t]*` rather than `\s*` so a field name at the
# end of a sentence cannot bind to a token on the next line.
_ASSIGNMENT_RE = re.compile(
    r"""(?P<key>[A-Za-z][A-Za-z0-9_.\-]{1,63})
        ["']?[ \t]*[:=][ \t]*["']?
        (?P<value>[A-Za-z0-9+/=_-]{20,512})
    """,
    re.VERBOSE,
)

# The MCP key's other real form. The h9av3 sweep's conclusion was to anchor
# it to `api_key=` IN A URL rather than to the bare 43-character base64url
# shape, because the bare shape is indistinguishable from an npm lockfile
# integrity hash (50 of 51 sweep hits). Requiring a preceding `?` or `&`
# makes this query-parameter syntax rather than the word `api_key`, which
# is why it can carry the token the field-name list deliberately omits.
_URL_API_KEY_RE = re.compile(
    r"[?&]api[_-]?key=(?P<value>[A-Za-z0-9+/=_-]{20,512})", re.IGNORECASE
)

# Substrings that mark a value as a documentation placeholder rather than a
# live secret. Every entry earned its place against the current tree or
# against a shape the docs demonstrably use; this list is a backstop for
# `_looks_secret`, not its primary defence, and it should stay short.
PLACEHOLDER_VALUE_MARKERS = (
    "example",
    "changeme",
    "placeholder",
    "redacted",
    "yourapikey",
    "yourtoken",
    "yourpassword",
    "sample",
    "dummy",
    "fake",
    "xxxx",
)

# Floor on value length. The shortest real credential this application
# handles is a 20-character Plex token.
_MIN_SECRET_LENGTH = 20

# Floor on distinct characters. This is the "variety" test, and it replaces
# a Shannon-entropy floor that was tried first and measured out.
#
# WHY NOT ENTROPY. An entropy floor cannot be placed usefully here, and the
# numbers say so. Over 30k generated samples of each real format:
#
#   format                    min   p0.1   median   (bits per character)
#   secrets.token_urlsafe(32) 4.28  4.43   4.85
#   32-character hex          2.90  3.13   3.62
#   40-character hex          3.18  3.29   3.71
#   20-character base64url    3.30  3.48   4.02
#
# A 32-character hex key is only 16 symbols wide, so sampling variance drags
# its low tail down to 2.90. Any floor high enough to reject structured text
# (3.4) throws away 6.2% of real Emby / Jellyfin API keys; any floor low
# enough to keep them (2.9) never fires on anything the tests below have not
# already rejected. Worse, entropy does not even catch the case it looks
# like it should: `abcdef0123456789abcdef0123456789` scores a perfect 4.0.
# A distinct-character floor expresses the same intent, is calibratable, and
# can be reasoned about without a simulation.
_MIN_DISTINCT_CHARACTERS = 10


def _is_repeating(value: str) -> bool:
    """True when the value is one short unit repeated.

    Catches the fabricated-key shape entropy misses:
    `abcdef0123456789abcdef0123456789` has 16 distinct characters, digits,
    letters, and maximal Shannon entropy, and is obviously not a secret.
    The probability of a randomly generated token being a repetition is
    negligible, so this costs no coverage.
    """
    return (value + value).find(value, 1) < len(value)


def _is_wordy(value: str) -> bool:
    """True when the value reads as hyphen/underscore-joined words.

    `my-dispatcharr-api-key`, `change_this_now_please`, and
    `baseline-test-key-not-for-prod-32chars` are the shapes doc authors
    actually reach for; the last one is a real line in
    `docs/dep_upgrade_baseline.md` and was this rule's only false positive
    on first measurement against the tree.

    The single-case requirement on the alphabetic segments is what makes
    this affordable. Requiring only "all segments alphabetic" missed the
    `...-32chars` case; relaxing to "two thirds of segments alphabetic"
    caught it but then misread 3.0% of real `token_urlsafe(32)` values as
    words. Human-written placeholders are `lower-case-words` or
    `UPPER-CASE-WORDS`; a random base64url segment is mixed case with high
    probability. Adding that constraint took the false-word rate on 200k
    generated samples to 0.21% for `token_urlsafe(32)` and 0.39% for
    20-character tokens, while still rejecting every placeholder above.
    """
    segments = [part for part in re.split(r"[-_]+", value) if part]
    if len(segments) < 2:
        return False
    words = [part for part in segments if part.isalpha()]
    if not words:
        return False
    if not all(part.islower() or part.isupper() for part in words):
        return False
    if len(words) == len(segments):
        return True
    return len(segments) >= 3 and len(words) * 3 >= len(segments) * 2


def _looks_secret(value: str) -> bool:
    """True when `value` has the shape of a live credential.

    Every test rejects a false-positive class observed in this repository
    or in a shape its documentation demonstrably uses:

      length      declarations and short values (`auth_method: password`)
      variety     `xxxxxxxxxxxxxxxxxxxxxxxx`, `000000000000000000000000`
      digit       `yourapikeyhere`, `SOMEPASSWORDVALUE`, ordinary prose
      alphabet    pure counters, timestamps, and byte sizes
      repetition  `abcdef0123456789abcdef0123456789`
      wordiness   `baseline-test-key-not-for-prod-32chars`
      markers     the remaining documentation placeholders

    Known and measured misses, stated rather than papered over: the digit
    requirement drops a real 20-character base64url token 3.5% of the time
    and a 43-character one 0.09% of the time, because a random token can
    contain no digit. Removing the requirement would flag every long
    lower-case placeholder in the docs, which is the worse trade for a
    guard whose failure mode is being switched off.
    """
    if len(value) < _MIN_SECRET_LENGTH:
        return False
    if len(set(value)) < _MIN_DISTINCT_CHARACTERS:
        return False
    if not any(char.isdigit() for char in value):
        return False
    if not any(char.isalpha() for char in value):
        return False
    if _is_repeating(value):
        return False
    if _is_wordy(value):
        return False
    lowered = re.sub(r"[^a-z0-9]", "", value.casefold())
    return not any(marker in lowered for marker in PLACEHOLDER_VALUE_MARKERS)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def find_credential_assignments(segment: str) -> list[str]:
    found = []
    for match in _ASSIGNMENT_RE.finditer(segment):
        normalized = _normalize_key(match.group("key"))
        if not normalized.endswith(_NORMALIZED_CREDENTIAL_FIELDS):
            continue
        if not _looks_secret(match.group("value")):
            continue
        found.append(match.group(0))
    for match in _URL_API_KEY_RE.finditer(segment):
        if _looks_secret(match.group("value")):
            found.append(match.group(0))
    return found


# --- Rule: discord-webhook-url (h9av3) --------------------------------------

# A real webhook is `.../webhooks/<snowflake>/<68-character token>`. The
# docs and the user guide write `.../webhooks/...` and
# `.../webhooks/<id>/<token>`, and neither survives the digit and length
# requirements. The host set mirrors the Settings API validator: canonical,
# legacy, canary, and PTB. Requiring `https://` and bounding the scheme/host
# prevents suffix hosts and text prefixes from impersonating one.
_DISCORD_WEBHOOK_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])https://"
    r"(?:discord(?:app)?\.com|(?:canary|ptb)\.discord\.com)"
    r"/api/webhooks/\d{17,20}/[A-Za-z0-9_-]{55,}"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


def find_discord_webhooks(segment: str) -> list[str]:
    return [match.group(0) for match in _DISCORD_WEBHOOK_RE.finditer(segment)]


# --- Rule: telegram-bot-token (h9av3) ---------------------------------------

# Telegram issues `<bot id>:<35-character token>` and nothing else. The
# token length is EXACT, not a floor: the lookarounds reject a 34- or
# 36-character neighbour rather than matching a prefix of some longer
# base64 blob, which is what keeps this rule off `.beads/issues.jsonl`.
#
# The matching `telegram_chat_id` is knowingly uncovered -- see the module
# docstring. A chat id is a bare integer and no rule reaching it could be
# precise.
_TELEGRAM_BOT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])\d{8,12}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"
)


def find_telegram_bot_tokens(segment: str) -> list[str]:
    return [match.group(0) for match in _TELEGRAM_BOT_TOKEN_RE.finditer(segment)]


# --- Rules NOT implemented here, and why ------------------------------------
#
# Two classes were written, measured against `detect-secrets` 1.5.0, and
# then deleted rather than shipped. Recording them here so the next person
# does not re-add them:
#
#   JWT credentials     `detect-secrets`' JwtTokenDetector fires on a
#                       three-segment `eyJ...` token. Verified. Exact
#                       overlap, and precise on their side.
#   PEM private keys    `detect-secrets`' PrivateKeyDetector fires on the
#                       `-----BEGIN ... PRIVATE KEY-----` armour header.
#                       Verified. Exact overlap, literal match, no false
#                       positives to improve on.
#
# Keeping either would produce two findings for one value and make the
# ratchet harder to read, for no added coverage. If the general layer is
# ever removed, restore them; they are three lines each.


RULES: tuple[tuple[str, Callable[[str], list[str]]], ...] = (
    # Arm 1: personal identifiers (il1xz).
    ("personal-email", find_personal_emails),
    ("local-user-path", find_local_user_paths),
    ("private-host-url", find_private_host_urls),
    ("known-identifier", find_known_identifiers),
    # Arm 2: ECM-specific credentials (h9av3). Deliberately three rules.
    # Everything generic belongs to `detect-secrets`; see the module
    # docstring's division-of-labour section.
    ("credential-assignment", find_credential_assignments),
    ("discord-webhook-url", find_discord_webhooks),
    ("telegram-bot-token", find_telegram_bot_tokens),
)

# Rules whose hit means a credential was PUBLISHED, so redaction alone is
# not the remedy. Drives the rotate-first wording in the failure report.
CREDENTIAL_RULE_NAMES = frozenset(
    {
        "credential-assignment",
        "discord-webhook-url",
        "telegram-bot-token",
    }
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
#
# "plaintext" is the credential-file surface (h9av3). It is line-oriented
# exactly like Markdown, so it reuses that segmenter rather than adding a
# near-identical one; the kinds stay distinct because the SURFACE differs
# even where the segmentation does not.
_SEGMENTERS = {
    "markdown": markdown_segments,
    "plaintext": markdown_segments,
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
        description=(
            "Ratchet guard against new personal identifiers and application "
            "credentials in tracked text."
        )
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
                f"FAIL: {len(violations)} protected value(s) in the named paths:",
                file=sys.stderr,
            )
            _report_inventory(violations, sys.stderr)
            return 1
        print("PASS: no protected values in the named paths.")
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
            f"(beads {BEAD_REF}, {CREDENTIAL_BEAD_REF}); the default ratchet "
            "mode is the gate."
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
            f"FAIL: {len(results)} protected value(s) added since {base[:12]}:",
            file=sys.stderr,
        )
        _report_new(results, sys.stderr)
        print(
            "\nValues are redacted because CI logs are public. Open the file at "
            "the reported line to see what matched.",
            file=sys.stderr,
        )
        if any(item.rule in CREDENTIAL_RULE_NAMES for item in results):
            print(
                "\nAt least one hit is a CREDENTIAL. Treat it as published: "
                "ROTATE the secret first (regenerate the key, change the "
                "password, reset the webhook), then redact the file. Removing "
                "the text does not un-publish a value that reached a public "
                "branch.",
                file=sys.stderr,
            )
        print(
            "\nRedact it: replace the value with a role placeholder and keep "
            "the surrounding context. There is no suppression comment for "
            f"this guard (beads {BEAD_REF}, {CREDENTIAL_BEAD_REF}). If a rule "
            "is genuinely wrong, widen its allowlist in scripts/check_pii.py "
            "and say why.",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS: no protected value added to the {len(surface)} scanned "
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
