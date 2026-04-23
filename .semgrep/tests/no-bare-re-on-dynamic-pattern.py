"""Self-test fixtures for the `no-bare-re-on-dynamic-pattern` Semgrep rule.

Run with: ``semgrep --config .semgrep/tests/no-bare-re-test.yml --test``

The `ruleid:` / `ok:` comments mark expected outcomes; `semgrep --test`
compares actual findings against the annotations and fails if they diverge.
"""
# ruff: noqa — this file is a Semgrep rule fixture, not production code.
# type: ignore
import re


# =============================================================================
# Positive cases — MUST be flagged.
# =============================================================================


def flagged_dynamic_variable(user_pattern, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.search(user_pattern, text)


def flagged_dynamic_match(user_pattern, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.match(user_pattern, text)


def flagged_dynamic_sub(user_pattern, repl, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.sub(user_pattern, repl, text)


def flagged_dynamic_compile(user_pattern):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.compile(user_pattern)


def flagged_dynamic_findall(user_pattern, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.findall(user_pattern, text)


def flagged_dynamic_finditer(user_pattern, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.finditer(user_pattern, text)


def flagged_dynamic_split(user_pattern, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.split(user_pattern, text)


def flagged_dynamic_fullmatch(user_pattern, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.fullmatch(user_pattern, text)


def flagged_dynamic_subn(user_pattern, repl, text):
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.subn(user_pattern, repl, text)


def flagged_fstring_no_escape(name, text):
    # f-string interpolation without re.escape — `name` could carry
    # metacharacters that change pattern semantics. Must flag.
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.search(f"^{name}$", text)


def flagged_raw_concat_no_escape(name, text):
    # Bare concat of user input into a raw-string pattern — no escape.
    # ruleid: no-bare-re-on-dynamic-pattern
    return re.search(r"\b" + name + r"\b", text)


# =============================================================================
# Negative cases — MUST NOT be flagged.
# =============================================================================

# Module-level compiled constant from raw-string literal — the ECM convention.
# ok: no-bare-re-on-dynamic-pattern
_CHANNEL_NUMBER_RE = re.compile(r"^\d+\s*\|\s*")

# With flags — still a literal.
# ok: no-bare-re-on-dynamic-pattern
_HD_RE = re.compile(r"\bhd\b", re.IGNORECASE)


def safe_literal_search(text):
    # ok: no-bare-re-on-dynamic-pattern
    return re.search(r"^\d+$", text)


def safe_literal_sub(text):
    # ok: no-bare-re-on-dynamic-pattern
    return re.sub(r"\s+", " ", text)


def safe_fstring_with_escape(name, text):
    # Interpolation goes through re.escape — neutralised to a literal.
    # ok: no-bare-re-on-dynamic-pattern
    return re.search(rf"\b{re.escape(name)}\b", text)


def safe_raw_concat_with_escape(name, text):
    # Concatenation with re.escape — same safety reasoning.
    # ok: no-bare-re-on-dynamic-pattern
    return re.search(r"\b" + re.escape(name) + r"\b", text)


def safe_compile_fstring_with_escape(name):
    # ok: no-bare-re-on-dynamic-pattern
    return re.compile(rf"^{re.escape(name)}\s*$", re.IGNORECASE)
