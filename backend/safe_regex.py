"""
safe_regex — ReDoS-guarded regex wrapper for user-supplied patterns.

This module wraps the third-party ``regex`` library (PyPI ``regex``, NOT
stdlib ``re``) with per-call ReDoS mitigation: a wall-clock timeout on
every search/match/sub and a hard cap on pattern length. It exists so
callers who accept user-supplied regex (normalization rules, auto-creation
rules, dummy-EPG templates, etc.) can execute those patterns without
giving a malicious or accidentally-pathological pattern an unbounded
runtime budget.

**The timeout is BEST-EFFORT, not preemptive.** The ``regex`` library
checks the deadline between backtracking steps in its matching loop. A
pattern that spends its time inside a single native operation (for
example a very long literal scan) can exceed the budget. In practice
the budget is effective against the catastrophic-backtracking patterns
that dominate the ReDoS threat surface, but callers must not rely on
this as a hard wall-clock ceiling. Do not call safe_regex from code
paths that must guarantee sub-second response to external traffic
without an additional ceiling one level up.

Contract
--------

=============== =================================== ===============================
function         on timeout / oversize               on compile error
=============== =================================== ===============================
search           returns ``None``                    N/A (malformed compiled inline)
match            returns ``None``                    N/A
sub              returns input ``text`` unchanged    N/A
compile          raises ``PatternTooLongError``      raises ``SafeRegexError``
                 (compile is authoring-time —
                 raising is the correct contract)
=============== =================================== ===============================

On every timeout or oversize the module emits a ``WARNING`` record via
``logging.getLogger("safe_regex")`` with the ``[SAFE_REGEX]`` prefix and
a structured payload: ``{pattern_sha256, pattern_excerpt_50chars,
text_len, timeout_ms, caller}``. The **full pattern is deliberately NOT
logged** — patterns can contain attacker-controlled text, so we log a
SHA-256 (for cross-referencing) plus a 50-character excerpt only.

See also
--------
- ``docs/style_guide.md`` Regex section (authored in bd-eio04.8) for
  project-wide guidance on when to use safe_regex vs stdlib ``re``.
- ``backend/log_utils.py`` for the ``[MODULE]`` prefix convention this
  module follows.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from typing import Optional, Union

import regex as _regex


__all__ = [
    "search",
    "match",
    "sub",
    "compile",
    "SafeRegexError",
    "RegexTimeoutError",
    "PatternTooLongError",
    "DEFAULT_TIMEOUT_MS",
    "DEFAULT_MAX_PATTERN_LEN",
]


logger = logging.getLogger("safe_regex")


# Defaults chosen to match the bead-eio04.5 grooming decisions.
DEFAULT_TIMEOUT_MS: int = 100
DEFAULT_MAX_PATTERN_LEN: int = 500

# Pattern excerpt length emitted in WARNING logs (the FULL pattern is
# never logged — see module docstring).
_EXCERPT_CHARS: int = 50


# =========================================================================
# Exception hierarchy.
# =========================================================================


class SafeRegexError(Exception):
    """Base class for safe_regex errors. Catch this for catch-all handling."""


class RegexTimeoutError(SafeRegexError):
    """Raised only when a caller opts into strict-mode timeout propagation.

    The module's default contract is sentinel-return (None / original text),
    so this class is exposed for future strict-mode callers rather than
    thrown from the default API. Reserving the name now avoids a breaking
    change when strict-mode is introduced.
    """


class PatternTooLongError(SafeRegexError):
    """Raised by :func:`compile` when the pattern exceeds the length cap."""


# =========================================================================
# Internal helpers.
# =========================================================================


def _pattern_sha256(pattern: str) -> str:
    """Return the SHA-256 hex digest of *pattern* for safe log identification."""
    return hashlib.sha256(pattern.encode("utf-8")).hexdigest()


def _pattern_excerpt(pattern: str) -> str:
    """Return a truncated, newline-escaped excerpt safe for log output."""
    excerpt = pattern[:_EXCERPT_CHARS]
    # Match log_utils' escape conventions so log injection is neutralized
    # even if safe_logging isn't installed in the caller's context.
    return excerpt.replace("\r\n", "\\r\\n").replace("\r", "\\r").replace("\n", "\\n")


def _caller_name(skip_frames: int = 2) -> str:
    """Return a ``"module:function:line"`` string for the external caller.

    Walks up *skip_frames* from the current frame to find the caller
    outside this module. Used purely for diagnostic context in WARN logs.
    """
    try:
        frame = sys._getframe(skip_frames)
    except ValueError:
        return "unknown"
    # Walk up past any remaining safe_regex frames (defensive).
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    if frame is None:
        return "unknown"
    return "%s:%s:%d" % (
        frame.f_globals.get("__name__", "?"),
        frame.f_code.co_name,
        frame.f_lineno,
    )


def _log_oversize(pattern: str, text_len: int, max_pattern_len: int) -> None:
    """Emit WARN log for oversize pattern; never logs the full pattern."""
    logger.warning(
        "[SAFE_REGEX] oversize pattern rejected "
        "pattern_sha256=%s pattern_excerpt=%r text_len=%d pattern_len=%d "
        "max_pattern_len=%d caller=%s",
        _pattern_sha256(pattern),
        _pattern_excerpt(pattern),
        text_len,
        len(pattern),
        max_pattern_len,
        _caller_name(skip_frames=3),
    )


def _log_timeout(pattern: str, text_len: int, timeout_ms: int) -> None:
    """Emit WARN log for pattern timeout; never logs the full pattern."""
    logger.warning(
        "[SAFE_REGEX] pattern timed out "
        "pattern_sha256=%s pattern_excerpt=%r text_len=%d timeout_ms=%d caller=%s",
        _pattern_sha256(pattern),
        _pattern_excerpt(pattern),
        text_len,
        timeout_ms,
        _caller_name(skip_frames=3),
    )


def _timeout_seconds(timeout_ms: int) -> float:
    """Convert an integer-millisecond budget into the float seconds the
    regex library expects via its ``timeout=`` kwarg."""
    return max(0.001, timeout_ms / 1000.0)


# =========================================================================
# Public API.
# =========================================================================


def search(
    pattern: Union[str, "_regex.Pattern"],
    text: str,
    *,
    flags: int = 0,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_pattern_len: int = DEFAULT_MAX_PATTERN_LEN,
) -> Optional["_regex.Match"]:
    """Search *text* for *pattern*; return a Match or ``None``.

    On timeout or oversize pattern, returns ``None`` and logs WARN. See
    the module docstring for the best-effort caveat on timeout.
    """
    if isinstance(pattern, str) and len(pattern) > max_pattern_len:
        _log_oversize(pattern, len(text), max_pattern_len)
        return None
    try:
        return _regex.search(
            pattern, text, flags=flags, timeout=_timeout_seconds(timeout_ms)
        )
    except TimeoutError:
        pattern_str = pattern if isinstance(pattern, str) else pattern.pattern
        _log_timeout(pattern_str, len(text), timeout_ms)
        return None
    except _regex.error as exc:
        pattern_str = pattern if isinstance(pattern, str) else pattern.pattern
        logger.warning(
            "[SAFE_REGEX] compile error at search "
            "pattern_sha256=%s pattern_excerpt=%r error=%s caller=%s",
            _pattern_sha256(pattern_str),
            _pattern_excerpt(pattern_str),
            exc,
            _caller_name(skip_frames=2),
        )
        return None


def match(
    pattern: Union[str, "_regex.Pattern"],
    text: str,
    *,
    flags: int = 0,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_pattern_len: int = DEFAULT_MAX_PATTERN_LEN,
) -> Optional["_regex.Match"]:
    """Anchored-match *pattern* at the start of *text*; return a Match or ``None``.

    On timeout or oversize pattern, returns ``None`` and logs WARN.
    """
    if isinstance(pattern, str) and len(pattern) > max_pattern_len:
        _log_oversize(pattern, len(text), max_pattern_len)
        return None
    try:
        return _regex.match(
            pattern, text, flags=flags, timeout=_timeout_seconds(timeout_ms)
        )
    except TimeoutError:
        pattern_str = pattern if isinstance(pattern, str) else pattern.pattern
        _log_timeout(pattern_str, len(text), timeout_ms)
        return None
    except _regex.error as exc:
        pattern_str = pattern if isinstance(pattern, str) else pattern.pattern
        logger.warning(
            "[SAFE_REGEX] compile error at match "
            "pattern_sha256=%s pattern_excerpt=%r error=%s caller=%s",
            _pattern_sha256(pattern_str),
            _pattern_excerpt(pattern_str),
            exc,
            _caller_name(skip_frames=2),
        )
        return None


def sub(
    pattern: Union[str, "_regex.Pattern"],
    repl: Union[str, "_regex.Pattern"],
    text: str,
    *,
    flags: int = 0,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_pattern_len: int = DEFAULT_MAX_PATTERN_LEN,
) -> str:
    """Substitute *pattern* with *repl* across *text*; return the result.

    On timeout or oversize pattern, returns *text* unchanged and logs WARN.
    """
    if isinstance(pattern, str) and len(pattern) > max_pattern_len:
        _log_oversize(pattern, len(text), max_pattern_len)
        return text
    try:
        return _regex.sub(
            pattern, repl, text, flags=flags, timeout=_timeout_seconds(timeout_ms)
        )
    except TimeoutError:
        pattern_str = pattern if isinstance(pattern, str) else pattern.pattern
        _log_timeout(pattern_str, len(text), timeout_ms)
        return text
    except _regex.error as exc:
        pattern_str = pattern if isinstance(pattern, str) else pattern.pattern
        logger.warning(
            "[SAFE_REGEX] compile error at sub "
            "pattern_sha256=%s pattern_excerpt=%r error=%s caller=%s",
            _pattern_sha256(pattern_str),
            _pattern_excerpt(pattern_str),
            exc,
            _caller_name(skip_frames=2),
        )
        return text


def compile(  # noqa: A001 — intentional parity with re/regex.compile
    pattern: str,
    *,
    flags: int = 0,
    max_pattern_len: int = DEFAULT_MAX_PATTERN_LEN,
) -> "_regex.Pattern":
    """Compile *pattern* and return the compiled object.

    Raises
    ------
    PatternTooLongError
        When ``len(pattern) > max_pattern_len``. Compile is authoring-time,
        so raising (rather than returning a sentinel) is the correct
        contract — callers need to know the pattern was not compiled.
    SafeRegexError
        When the pattern is syntactically invalid (wraps the underlying
        ``regex.error`` into this module's exception hierarchy).
    """
    if len(pattern) > max_pattern_len:
        _log_oversize(pattern, 0, max_pattern_len)
        raise PatternTooLongError(
            "pattern length %d exceeds max_pattern_len=%d (sha256=%s)"
            % (len(pattern), max_pattern_len, _pattern_sha256(pattern))
        )
    try:
        return _regex.compile(pattern, flags=flags)
    except _regex.error as exc:
        logger.warning(
            "[SAFE_REGEX] compile error "
            "pattern_sha256=%s pattern_excerpt=%r error=%s caller=%s",
            _pattern_sha256(pattern),
            _pattern_excerpt(pattern),
            exc,
            _caller_name(skip_frames=2),
        )
        raise SafeRegexError(
            "failed to compile pattern (sha256=%s): %s"
            % (_pattern_sha256(pattern), exc)
        ) from exc
