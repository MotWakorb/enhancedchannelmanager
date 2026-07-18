"""
date_placeholders — shared ``{date...}`` / ``{today...}`` token expansion.

Single source of truth for the Channel Pipeline "Date Expansion in Regex"
feature (USER_GUIDE.md § "Date Expansion in Regex", contributed by
@lpukatch). Two call sites MUST apply the exact same expansion, in the
exact same routing, or a rule can pass one gate and fail the other
(bead enhancedchannelmanager-qa43j):

* **Runtime** — :class:`channel_pipeline_evaluator.ConditionEvaluator`
  expands the token before compiling/evaluating a condition's pattern
  against a stream (``_evaluate_regex``, ``_evaluate_contains``,
  ``_evaluate_channel_exists_name``, ``_evaluate_channel_exists_regex``).
* **Write-time validation** — ``regex_lint.lint_conditions_json`` (the
  save-time 422 gate) and ``channel_pipeline_schema.Condition.validate``
  (the save-time 400 gate) both compile a condition's regex value via
  ``safe_regex`` *before* persisting a rule. Prior to this module, both
  compiled the RAW value — a documented, runtime-supported token like
  ``{date+3d}`` is not valid raw regex (``{...}`` is quantifier syntax),
  so every save-time gate rejected it even though the evaluator handles
  it correctly at run time. The fix is expanding here too, not loosening
  ``safe_regex`` — see ``docs/style_guide.md#regex``.

Only the four Channel Pipeline regex-flavored condition types
(``stream_name_matches``, ``stream_group_matches``, ``tvg_id_matches``,
``channel_exists_matching``) and the two non-regex-but-still-expanded
value types (``stream_name_contains``-style substring compares and
``channel_exists_with_name``) go through this expansion. The
Normalization-rule ``regex`` condition type does NOT — normalization has
no date-expansion feature at runtime, so a raw ``{date+3d}`` there is
correctly rejected as invalid regex on both sides of the gate.

Malformed tokens (``{date+}``, ``{dat}``) are intentionally NOT
special-cased: the expansion regex simply fails to match them, so they
pass through unexpanded — exactly as at runtime — and any downstream
compile step (lint or ``safe_regex.compile``) fails on them exactly as
the runtime ``safe_regex.search`` call would. That is correct: a pattern
that would never successfully match at runtime should not be
save-able either.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import safe_regex

# Recognizes ``{date}``, ``{today}``, ``{date+N}``, ``{date+Nd}``,
# ``{date+Nw}``, ``{date-N}``, and any of those with a trailing
# ``:FORMAT`` (e.g. ``{date+3d:%d %b}``). This is a module-literal raw
# string (not user-supplied) — the ``safe_regex.sub`` wrapping below is
# defense-in-depth because the *text* being substituted into is
# user-supplied (bd-eio04.15).
_DATE_TOKEN_PATTERN = r"\{(?:date|today)([+-]\d+[dw]?)?(:[^}]+)?\}"

# Cap on the day-offset range a ``{date+N}``/``{date-N}`` token expands
# into, to prevent unbounded regex-alternation generation. Also
# documented in USER_GUIDE.md § "Date Expansion in Regex".
MAX_DATE_RANGE_DAYS = 90


def expand_date_placeholders(text: str, allow_ranges: bool = True) -> str:
    """Expand ``{date...}``/``{today...}`` placeholders in ``text``.

    Args:
        text: Text with potential placeholders.
        allow_ranges: If True, offset placeholders like ``{date+3}``
            expand to a regex alternation group ``(d0|d1|d2|d3)``. If
            False, only bare ``{date}``/``{today}`` (optionally with a
            ``:FORMAT``) expand; offset tokens are left unexpanded.

    Supported formats:
        - ``{date}`` or ``{today}`` -> ``YYYY-MM-DD`` (today)
        - ``{date+N}`` -> today through today+N (inclusive range)
        - ``{date-N}`` -> today through today-N (inclusive range)
        - ``{date+Nd}`` -> same as ``{date+N}``, explicit "days" unit
        - ``{date+Nw}`` -> today through today+N weeks
        - ``{date:FORMAT}`` -> today formatted (e.g. ``{date:%d %b}``)

    Malformed tokens (unparseable offsets, missing digits) and,
    when ``allow_ranges=False``, offset tokens, are returned unchanged
    — the caller (evaluator at run time, or a lint/validate gate at
    save time) then attempts to compile/match the literal text, which
    fails consistently in both places.
    """
    if not text or not isinstance(text, str) or "{" not in text:
        return text

    def replace_match(match):
        offset_str = match.group(1)
        format_str = match.group(2)

        base_date = datetime.now()

        # Default unit is days
        unit = "d"
        val_str = None
        val = 0

        if offset_str:
            if not allow_ranges:
                return match.group(0)  # Don't expand ranges if not allowed

            val_str = offset_str
            # Check if specific unit provided (d=days, w=weeks)
            if offset_str[-1].lower() in ("d", "w"):
                unit = offset_str[-1].lower()
                val_str = offset_str[:-1]

            try:
                # Parse the numerical offset value
                val = int(val_str)
            except ValueError:
                return match.group(0)  # Return original if parsing fails

        # Remove colon from format string if present
        fmt = "%Y-%m-%d"
        if format_str:
            fmt = format_str[1:]  # Skip the ':'

        if val == 0:
            try:
                return base_date.strftime(fmt)
            except ValueError:
                return match.group(0)

        # Range calculation
        days_to_add = val
        if unit == "w":
            days_to_add = val * 7

        # Cap the range to prevent huge regex generation
        if days_to_add > MAX_DATE_RANGE_DAYS:
            days_to_add = MAX_DATE_RANGE_DAYS
        elif days_to_add < -MAX_DATE_RANGE_DAYS:
            days_to_add = -MAX_DATE_RANGE_DAYS

        dates = []
        # range is exclusive at end, so we need +1 or -1 to include the target date
        step = 1 if days_to_add > 0 else -1
        end = days_to_add + step

        try:
            for i in range(0, end, step):
                d = base_date + timedelta(days=i)
                dates.append(d.strftime(fmt))
            return f"({'|'.join(dates)})"
        except ValueError:
            return match.group(0)

    # bd-eio04.15 / enhancedchannelmanager-qa43j: route through safe_regex
    # for ReDoS protection. On timeout, safe_regex.sub returns ``text``
    # unchanged, matching the runtime degradation the evaluator already
    # relies on (placeholders simply aren't expanded).
    return safe_regex.sub(_DATE_TOKEN_PATTERN, replace_match, text)
