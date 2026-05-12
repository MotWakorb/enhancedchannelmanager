"""
regex_lint — write-time pattern linter for user-supplied regex.

Rejects pathological patterns at persistence boundaries (normalization rules,
auto-creation rules, dummy-EPG profiles). The detector is the hand-rolled AST
walker selected by the bd-eio04.11 spike — it walks the stdlib ``re._parser``
parse tree and flags only the nested-unbounded-quantifier-with-killer shape
Python's ``re`` engine is actually vulnerable to. Zero external dependencies.

Bead: bd-eio04.7. Depends on ``safe_regex`` (bd-eio04.5) for the length cap
and compile-error surface. Spike: bd-eio04.11 (hand-rolled won over
regexploit 0 FP vs 1 FP on the production corpus).

Three violation codes are emitted:

* ``REGEX_TOO_LONG`` — pattern length > ``MAX_PATTERN_LEN`` (shared with
  ``safe_regex.DEFAULT_MAX_PATTERN_LEN``).
* ``REGEX_COMPILE_ERROR`` — pattern failed to compile via ``safe_regex``.
* ``REGEX_NESTED_QUANTIFIER`` — AST walk detected the ReDoS shape.

The AST walk operates on the compiled parse tree, not on the pattern string
via regex-matching, so the linter is itself ReDoS-safe by construction.

Error messages are actionable (length/limit surfaced, rewrite hint included)
and link to ``docs/style_guide.md#regex`` for project-wide guidance on the
regex convention and when ``safe_regex`` applies.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as _dc_field
from typing import Any, Iterable, Literal

import safe_regex

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Stdlib AST plumbing — private modules, post-3.11 names with fallback.
# -------------------------------------------------------------------------
try:  # Python 3.11+
    import re._parser as _sre_parse  # type: ignore[import-not-found]
    from re._constants import (  # type: ignore[import-not-found]
        MAX_REPEAT,
        MIN_REPEAT,
        SUBPATTERN,
        BRANCH,
        IN,
        LITERAL,
        NOT_LITERAL,
        CATEGORY,
        AT,
        ASSERT,
        ASSERT_NOT,
        MAXREPEAT,
    )
except ImportError:  # pragma: no cover — Python < 3.11
    import sre_parse as _sre_parse  # type: ignore[no-redef]
    from sre_constants import (  # type: ignore[no-redef]
        MAX_REPEAT,
        MIN_REPEAT,
        SUBPATTERN,
        BRANCH,
        IN,
        LITERAL,
        NOT_LITERAL,
        CATEGORY,
        AT,
        ASSERT,
        ASSERT_NOT,
        MAXREPEAT,
    )

_REPEAT_OPS = {MAX_REPEAT, MIN_REPEAT}


# -------------------------------------------------------------------------
# Public constants and types.
# -------------------------------------------------------------------------

MAX_PATTERN_LEN: int = safe_regex.DEFAULT_MAX_PATTERN_LEN  # 500

# Anchored link to the Regex section of the engineering style guide. The
# style guide covers the full convention (when to use safe_regex, the
# exception list, the enforcement chain); error messages surfaced from this
# linter point the user here so "422 rejected" is immediately actionable.
DOCS_URL: str = "docs/style_guide.md#regex"

ViolationCode = Literal[
    "REGEX_TOO_LONG",
    "REGEX_NESTED_QUANTIFIER",
    "REGEX_COMPILE_ERROR",
    # Advisory codes (bd-0gntx) — surfaced via the analyze endpoint
    # only, never from the strict :func:`lint_pattern` save-time path.
    "REGEX_TRIVIALLY_MATCHES_ALL",
    "REGEX_REDUNDANT_ESCAPE_CARET",
    "OPERATOR_VALUE_LOOKS_LIKE_REGEX",
]

Severity = Literal["error", "warning", "info"]


@dataclass
class LintViolation:
    """One lint finding. See :func:`lint_pattern` for semantics.

    ``severity`` defaults to ``"error"`` for back-compat with the
    pre-bd-0gntx codes — those are wired to 422 rejections and must
    not change behavior. Advisory codes set ``severity="warning"``;
    the analyze endpoint emits them, the save-time path filters them
    out.
    """

    code: ViolationCode
    message: str
    field: str = "pattern"
    detail: dict = _dc_field(default_factory=dict)
    severity: Severity = "error"

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "detail": dict(self.detail),
        }


# -------------------------------------------------------------------------
# The nested-unbounded-quantifier detector — lifted verbatim from
# /tmp/bd-eio04.11-eval.py (spike winner). See the spike report for the
# 0-FP/7-TP empirical results and the rationale for only flagging
# nested-unbounded-with-killer.
# -------------------------------------------------------------------------


def _is_unbounded(repeat_args: Any) -> bool:
    _min, _max, _body = repeat_args
    return _max == MAXREPEAT


def _contains_unbounded_repeat(parsed: Any) -> bool:
    for op, args in parsed:
        if op in _REPEAT_OPS and _is_unbounded(args):
            return True
        if op is SUBPATTERN:
            _gid, _a, _b, subp = args
            if _contains_unbounded_repeat(subp):
                return True
        elif op is BRANCH:
            _none, branches = args
            for b in branches:
                if _contains_unbounded_repeat(b):
                    return True
        elif op in (ASSERT, ASSERT_NOT):
            _dir, subp = args
            if _contains_unbounded_repeat(subp):
                return True
    return False


def _has_post_killer(subp_tail: Any) -> bool:
    """Return True if anything in ``subp_tail`` can force a match failure.

    Literals, char classes, and anchors are "killers" — they fail against
    the wrong character and force the outer repeat to backtrack. A required
    inner repeat (min>=1) whose body itself contains a killer also counts.
    """
    for op, args in subp_tail:
        if op in (LITERAL, NOT_LITERAL, IN, CATEGORY, AT):
            return True
        if op is SUBPATTERN:
            _gid, _a, _b, inner = args
            if _has_post_killer(inner):
                return True
        if op in _REPEAT_OPS:
            _min, _max, body = args
            if _min >= 1 and _has_post_killer(body):
                return True
    return False


def _js_to_python_named_groups(pattern: str) -> str:
    """Mirror ``backend/dummy_epg_engine.py``: convert JS-style ``(?<name>``
    to Python's ``(?P<name>`` so we parse exactly the pattern the engine
    will execute. Leaves lookbehinds ``(?<=`` / ``(?<!`` untouched."""
    if not pattern:
        return pattern
    return re.sub(r"\(\?<(?!\=|\!)", "(?P<", pattern)


def _detect_nested_quantifier(pattern: str) -> tuple[bool, str]:
    """Return ``(is_vulnerable, reason)``. Empty reason means safe.

    Mirrors the spike's ``detect_redos`` (bd-eio04.11). Only flags
    nested-unbounded-quantifier-with-post-match-killer — the shape Python's
    ``re`` engine is actually vulnerable to.
    """
    try:
        converted = _js_to_python_named_groups(pattern)
        parsed = _sre_parse.parse(converted)
    except re.error as exc:
        # Compile errors are surfaced by the compile step — we don't flag
        # them here to avoid double-reporting.
        return (False, f"syntax-error:{exc}")

    def walk(subp: Any, outer_suffix_has_killer: bool) -> str | None:
        for idx, (op, args) in enumerate(subp):
            remainder = subp[idx + 1 :]
            local_killer = _has_post_killer(remainder) or outer_suffix_has_killer

            if op in _REPEAT_OPS:
                _min, _max, body = args
                if (
                    _is_unbounded((_min, _max, body))
                    and _contains_unbounded_repeat(body)
                    and local_killer
                ):
                    return "nested-unbounded-repeat-with-killer"
                r = walk(body, local_killer)
                if r:
                    return r
            elif op is SUBPATTERN:
                _gid, _a, _b, subp2 = args
                r = walk(subp2, local_killer)
                if r:
                    return r
            elif op is BRANCH:
                _none, branches = args
                for b in branches:
                    r = walk(b, local_killer)
                    if r:
                        return r
            elif op in (ASSERT, ASSERT_NOT):
                _dir, subp2 = args
                # Lookarounds are zero-width; they don't propagate outer killer.
                r = walk(subp2, False)
                if r:
                    return r
        return None

    reason = walk(parsed, outer_suffix_has_killer=False)
    return (reason is not None, reason or "")


# -------------------------------------------------------------------------
# Advisory detectors (bd-0gntx) — never block save. Walk the AST that
# was already produced by :func:`_detect_nested_quantifier`-style
# parsing so we don't string-parse the pattern.
# -------------------------------------------------------------------------


def _detect_empty_alternation(parsed: Any) -> bool:
    """Walk the parse tree; return True if any BRANCH has an empty arm.

    ``UK|`` parses as a BRANCH with branches ``[[<UK>], []]`` — the
    second arm is empty. Such a branch always matches the empty
    string, which collapses the entire pattern into a guaranteed
    match. Same logic for ``|UK``, ``(UK|)``, and ``(|UK)``.
    """
    for op, args in parsed:
        if op is BRANCH:
            _none, branches = args
            for arm in branches:
                if not list(arm):
                    return True
                if _detect_empty_alternation(arm):
                    return True
        elif op is SUBPATTERN:
            _gid, _a, _b, subp = args
            if _detect_empty_alternation(subp):
                return True
        elif op in (ASSERT, ASSERT_NOT):
            _dir, subp = args
            if _detect_empty_alternation(subp):
                return True
        elif op in _REPEAT_OPS:
            _min, _max, body = args
            if _detect_empty_alternation(body):
                return True
    return False


# Pattern starts with ``^\^`` literally — anchor followed by escaped
# caret. Almost always a double-escape typo (the user wrote ``^foo``
# in a "matches" field, then re-escaped the ``^``).
_REDUNDANT_ESCAPE_CARET_PREFIX = "^\\^"


# Substring values where these substrings appear are very likely the
# user typing regex syntax under a Contains operator. ``|`` is
# intentionally NOT in this list — M3U groups commonly contain a
# literal pipe (``UK| MOVIES``), so substring search for ``UK|`` is
# legitimate.
_REGEX_LIKE_SUBSTRINGS = (
    ".*",
    ".+",
    "\\b",
    "\\B",
    "\\d",
    "\\D",
    "\\w",
    "\\W",
    "\\s",
    "\\S",
)


def _value_looks_like_regex_for_contains(value: str) -> str | None:
    """Return a short reason if ``value`` looks like the user intended
    regex, ``None`` otherwise.

    Heuristic — never authoritative. Used only for warning-severity
    findings on Contains-operator conditions.
    """
    if not value:
        return None
    if value.startswith("^") and len(value) > 1:
        return "starts-with-^"
    if value.endswith("$") and len(value) > 1:
        return "ends-with-$"
    for token in _REGEX_LIKE_SUBSTRINGS:
        if token in value:
            return f"contains-{token}"
    return None


# -------------------------------------------------------------------------
# Public API.
# -------------------------------------------------------------------------


def lint_pattern(
    pattern: str | None,
    field: str = "pattern",
    *,
    max_pattern_len: int = MAX_PATTERN_LEN,
) -> list[LintViolation]:
    """Run all lint checks against ``pattern``; return the violations list.

    Returns an empty list when the pattern is safe to persist. Empty,
    ``None``, or whitespace-only patterns are treated as "no regex
    supplied" and pass (router-level required-field validation is a
    separate concern).

    Check order — fail fast on the cheapest check:

    1. **Length** (``REGEX_TOO_LONG``). No further checks run if this
       trips, since oversize patterns also fail compile and we don't want
       to waste CPU on the AST walk.
    2. **Compile** (``REGEX_COMPILE_ERROR``). Skipped if length already
       failed. Syntax errors short-circuit the AST walk.
    3. **Nested quantifier** (``REGEX_NESTED_QUANTIFIER``). AST-level
       check on the parsed pattern.
    """
    violations: list[LintViolation] = []

    if pattern is None:
        return violations
    if not isinstance(pattern, str):
        # Caller passed something weird; don't crash the endpoint, flag it
        # as an invalid pattern so the UI can display the message.
        violations.append(
            LintViolation(
                code="REGEX_COMPILE_ERROR",
                message=(
                    f"Pattern must be a string, got {type(pattern).__name__}. "
                    f"See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={"compile_error": f"type={type(pattern).__name__}"},
            )
        )
        return violations
    # Empty / whitespace-only: treat as no pattern supplied.
    if not pattern.strip():
        return violations

    # 1. Length check.
    if len(pattern) > max_pattern_len:
        violations.append(
            LintViolation(
                code="REGEX_TOO_LONG",
                message=(
                    f"Pattern is too long ({len(pattern)} chars, max "
                    f"{max_pattern_len}). Try breaking this into multiple "
                    f"rules. See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={
                    "pattern_len": len(pattern),
                    "max_pattern_len": max_pattern_len,
                },
            )
        )
        # Skip further checks — a too-long pattern will also fail compile
        # (safe_regex.compile raises PatternTooLongError on the same cap),
        # and we don't want to spend CPU walking an oversized AST.
        return violations

    # 2. Compile check via safe_regex. We apply the same named-group
    # conversion as the runtime engines so the compile check exercises
    # the actual pattern the engines will see.
    converted = _js_to_python_named_groups(pattern)
    try:
        safe_regex.compile(converted, max_pattern_len=max_pattern_len)
    except safe_regex.PatternTooLongError:
        # Length already handled above; reaching here means conversion
        # made the string longer than the cap, which is abnormal. Report
        # as too-long.
        violations.append(
            LintViolation(
                code="REGEX_TOO_LONG",
                message=(
                    f"Pattern is too long after named-group conversion "
                    f"({len(converted)} chars, max {max_pattern_len}). "
                    f"See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={
                    "pattern_len": len(converted),
                    "max_pattern_len": max_pattern_len,
                },
            )
        )
        return violations
    except safe_regex.SafeRegexError as exc:
        # Compile error. Message strips the internal sha256 framing that
        # safe_regex adds so the user-facing message is focused on the
        # underlying regex.error text.
        compile_error = _extract_compile_error(exc)
        violations.append(
            LintViolation(
                code="REGEX_COMPILE_ERROR",
                message=(
                    f"Pattern failed to compile: {compile_error}. "
                    f"Check for unescaped metacharacters or unbalanced "
                    f"parentheses. See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={"compile_error": compile_error},
            )
        )
        # If it can't compile, the AST walk below would also fail — stop
        # here to avoid reporting the same error twice.
        return violations

    # 3. Nested-quantifier AST walk.
    is_vuln, reason = _detect_nested_quantifier(pattern)
    if is_vuln:
        violations.append(
            LintViolation(
                code="REGEX_NESTED_QUANTIFIER",
                message=(
                    "Pattern contains a nested unbounded quantifier followed "
                    "by a terminator — this can catastrophically backtrack "
                    "on adversarial input. Rewrite to avoid nesting + and * "
                    f"inside another + or *. See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={"reason": reason},
            )
        )

    return violations


def _extract_compile_error(exc: Exception) -> str:
    """Pull the user-meaningful portion out of a SafeRegexError.

    ``safe_regex.compile`` wraps errors as
    ``"failed to compile pattern (sha256=...): <real error>"``. Strip the
    sha256 framing for UI display but preserve the ``regex.error`` text.
    """
    msg = str(exc)
    marker = "): "
    idx = msg.find(marker)
    if idx != -1:
        return msg[idx + len(marker) :]
    return msg


# -------------------------------------------------------------------------
# Bulk helpers for router wiring.
# -------------------------------------------------------------------------


def lint_pattern_advisory(
    pattern: str | None,
    field: str = "pattern",
) -> list[LintViolation]:
    """Run advisory checks against ``pattern``; return warning-level findings.

    Distinct from :func:`lint_pattern` — the strict path raises 422 on
    every violation it returns; this advisory path is for the
    /rules/analyze endpoint, which surfaces hints without blocking
    saves. All findings have ``severity="warning"``.

    Codes (bd-0gntx):

    * ``REGEX_TRIVIALLY_MATCHES_ALL`` — empty alternation makes the
      pattern equivalent to ".*" at position 0. ``UK|``, ``|UK``,
      ``(UK|)``, ``(|UK)``.
    * ``REGEX_REDUNDANT_ESCAPE_CARET`` — pattern starts with ``^\\^``,
      almost always a double-escape typo.

    Returns ``[]`` for ``None`` / empty / whitespace-only patterns and
    for patterns that fail to compile (the strict lint surfaces
    compile errors with the right severity; we don't double-report).
    """
    out: list[LintViolation] = []

    if pattern is None or not isinstance(pattern, str) or not pattern.strip():
        return out

    # Parse once via the same helper the strict path uses. If it fails
    # to parse, leave it to lint_pattern to flag — we don't double-up.
    try:
        converted = _js_to_python_named_groups(pattern)
        parsed = _sre_parse.parse(converted)
    except re.error:
        return out

    if _detect_empty_alternation(parsed):
        out.append(
            LintViolation(
                code="REGEX_TRIVIALLY_MATCHES_ALL",
                severity="warning",
                message=(
                    f"Pattern {pattern!r} contains an empty alternation "
                    f"(e.g. ``UK|``) which matches every input — likely "
                    f"a confusion between regex and substring matching. "
                    f"If you meant a literal pipe, escape it (``UK\\|``) "
                    f"or use the Begins With operator. "
                    f"See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={"reason": "empty-alternation"},
            )
        )

    if pattern.startswith(_REDUNDANT_ESCAPE_CARET_PREFIX):
        out.append(
            LintViolation(
                code="REGEX_REDUNDANT_ESCAPE_CARET",
                severity="warning",
                message=(
                    f"Pattern {pattern!r} starts with ``^\\^`` — anchor "
                    f"followed by a literal caret. This is almost always "
                    f"a double-escape typo. Did you mean ``^`` (anchor) "
                    f"or ``\\^`` (literal caret) — not both? "
                    f"See {DOCS_URL} for guidance."
                ),
                field=field,
                detail={"reason": "anchor-then-literal-caret"},
            )
        )

    return out


def lint_pattern_fields(fields: Iterable[tuple[str, str | None]]) -> list[LintViolation]:
    """Lint several ``(field_name, pattern)`` pairs; aggregate violations.

    Convenience for endpoints that have multiple pattern-bearing columns
    (e.g. dummy-EPG profile has ``title_pattern`` + ``time_pattern`` +
    ``date_pattern`` + substitution pair ``find`` values). Passing
    ``None`` or ``""`` for a pattern is a no-op.
    """
    out: list[LintViolation] = []
    for name, pattern in fields:
        out.extend(lint_pattern(pattern, field=name))
    return out


def lint_conditions_json(conditions: list | None, prefix: str = "conditions") -> list[LintViolation]:
    """Walk a list of condition objects; lint any pattern-bearing values.

    Used by the normalization and auto-creation routers which store
    compound conditions as JSON blobs. The condition shape is
    ``{type, value, ...}`` — for regex-flavored types we lint
    ``value``; all other types are skipped. Auto-creation condition
    types that take regex: ``stream_name_matches``, ``stream_group_matches``,
    ``tvg_id_matches``, ``channel_exists_matching``. Normalization
    rule condition type ``regex`` also has a regex value.
    """
    if not conditions:
        return []

    regex_condition_types = {
        # Normalization
        "regex",
        # Auto-creation
        "stream_name_matches",
        "stream_group_matches",
        "tvg_id_matches",
        "channel_exists_matching",
    }
    out: list[LintViolation] = []
    for idx, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            continue
        ctype = cond.get("type")
        # Logical operators recurse.
        if ctype in ("and", "or", "not"):
            sub = cond.get("conditions")
            if isinstance(sub, list):
                out.extend(
                    lint_conditions_json(sub, prefix=f"{prefix}[{idx}].conditions")
                )
            continue
        if ctype in regex_condition_types:
            value = cond.get("value")
            out.extend(lint_pattern(value, field=f"{prefix}[{idx}].value"))
    return out


_REGEX_CONDITION_TYPES = frozenset({
    # Normalization
    "regex",
    # Auto-creation
    "stream_name_matches",
    "stream_group_matches",
    "tvg_id_matches",
    "channel_exists_matching",
})


_CONTAINS_CONDITION_TYPES = frozenset({
    "stream_name_contains",
    "stream_group_contains",
})


def lint_conditions_json_advisory(
    conditions: list | None,
    prefix: str = "conditions",
) -> list[LintViolation]:
    """Walk a list of condition objects; emit advisory warnings.

    Counterpart to :func:`lint_conditions_json` but emits the
    bd-0gntx warning codes:

    * For ``*_matches`` regex types: runs :func:`lint_pattern_advisory`
      on the value (catches ``UK|``, ``^\\^4k``, etc.).
    * For ``*_contains`` substring types: emits
      ``OPERATOR_VALUE_LOOKS_LIKE_REGEX`` when the value contains
      substrings that suggest the user meant regex (``^foo``, ``foo$``,
      ``.*``, ``\\b``, etc.). Bare ``|`` is intentionally NOT flagged —
      M3U groups commonly contain a literal pipe.

    All findings have ``severity="warning"``. The save-time path uses
    :func:`lint_conditions_json` instead, which only surfaces errors.
    """
    if not conditions:
        return []

    out: list[LintViolation] = []
    for idx, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            continue
        ctype = cond.get("type")
        if ctype in ("and", "or", "not"):
            sub = cond.get("conditions")
            if isinstance(sub, list):
                out.extend(
                    lint_conditions_json_advisory(
                        sub, prefix=f"{prefix}[{idx}].conditions"
                    )
                )
            continue

        if ctype in _REGEX_CONDITION_TYPES:
            out.extend(
                lint_pattern_advisory(
                    cond.get("value"), field=f"{prefix}[{idx}].value"
                )
            )
        elif ctype in _CONTAINS_CONDITION_TYPES:
            value = cond.get("value")
            if isinstance(value, str):
                reason = _value_looks_like_regex_for_contains(value)
                if reason:
                    out.append(
                        LintViolation(
                            code="OPERATOR_VALUE_LOOKS_LIKE_REGEX",
                            severity="warning",
                            message=(
                                f"Value {value!r} on a Contains operator "
                                f"looks like regex syntax ({reason}). "
                                f"Contains is a literal substring match — "
                                f"the regex characters are matched as-is. "
                                f"Switch the operator to Matches (Regex), "
                                f"Begins With, or Ends With if you meant "
                                f"regex. See {DOCS_URL} for guidance."
                            ),
                            field=f"{prefix}[{idx}].value",
                            detail={"reason": reason, "value": value},
                        )
                    )
    return out


def lint_actions_json(actions: list | None, prefix: str = "actions") -> list[LintViolation]:
    """Walk a list of action objects; lint any pattern-bearing values.

    Normalization ``regex_replace`` actions store their pattern on the
    rule's ``condition_value`` (linted separately). Auto-creation
    ``set_variable`` actions in ``regex_extract`` / ``regex_replace``
    modes store the pattern in ``action.pattern``. Name-transform fields
    (``name_transform_pattern``) appear on ``create_channel`` /
    ``create_group`` actions.
    """
    if not actions:
        return []
    out: list[LintViolation] = []
    for idx, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        atype = action.get("type")
        # set_variable regex modes.
        if atype == "set_variable":
            mode = action.get("variable_mode")
            if mode in ("regex_extract", "regex_replace"):
                out.extend(
                    lint_pattern(action.get("pattern"), field=f"{prefix}[{idx}].pattern")
                )
        # Name transform on create_channel / create_group.
        if "name_transform_pattern" in action:
            out.extend(
                lint_pattern(
                    action.get("name_transform_pattern"),
                    field=f"{prefix}[{idx}].name_transform_pattern",
                )
            )
    return out


def lint_substitution_pairs(
    pairs: list | None, prefix: str = "substitution_pairs"
) -> list[LintViolation]:
    """Lint ``find`` values on substitution pairs that have ``is_regex: True``.

    Substitution pairs shape:
    ``{"find": "...", "replace": "...", "is_regex": bool, "enabled": bool}``.
    Non-regex pairs are literal strings and don't need the lint.
    """
    if not pairs:
        return []
    out: list[LintViolation] = []
    for idx, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            continue
        if pair.get("is_regex"):
            out.extend(
                lint_pattern(pair.get("find"), field=f"{prefix}[{idx}].find")
            )
    return out


def violations_to_http_detail(violations: list[LintViolation]) -> dict:
    """Build the HTTP 422 ``detail`` dict for a list of violations.

    Response envelope (matches the bead-eio04.7 grooming decision —
    single top-level code + per-violation breakdown)::

        {
          "error": {
            "code": "REGEX_VALIDATION_ERROR",
            "message": "<first violation's message>",
            "details": [<each violation as a dict>]
          }
        }

    FastAPI ``HTTPException(status_code=422, detail=...)`` wraps this under
    ``detail`` at the response level — the sanitized handler in ``main.py``
    passes ``detail`` through unchanged for non-500 codes.
    """
    # Use the first violation's message as the top-level human message —
    # it's the one the UI will show in the inline error banner.
    top_message = violations[0].message if violations else "Pattern validation failed"
    return {
        "error": {
            "code": "REGEX_VALIDATION_ERROR",
            "message": top_message,
            "details": [v.to_dict() for v in violations],
        }
    }
