"""
Template engine for dummy EPG title/description rendering.

Supports:
  - Placeholders          {name}
  - Chained pipes         {name|uppercase|trim|strip:-}
  - Lookup pipes          {name|lookup:tablename}
  - Conditionals          {if:group}body{/if}
                          {if:group=value}body{/if}
                          {if:group~regex}body{/if}
  - Legacy suffix         {name_normalize}  (lowercase, strip non-alphanumeric)

Safety:
  Template length, individual group values, and user-supplied regex patterns
  are all length-capped so a malicious config can't trigger catastrophic
  backtracking against channel-name inputs. Invalid regex patterns inside a
  conditional evaluate to false rather than raising.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import safe_regex


class TemplateSyntaxError(ValueError):
    """Raised when a template is malformed or references an unknown transform/table."""


# ---------------------------------------------------------------------------
# Legacy helper (preserved for {name_normalize} back-compat with the previous
# applyTemplate() in DummyEPGSourceModal.tsx).
# ---------------------------------------------------------------------------
_NORMALIZE_STRIP_RE = re.compile(r"[^a-z0-9]")


def _legacy_normalize(value: str) -> str:
    return _NORMALIZE_STRIP_RE.sub("", value.lower())


# ---------------------------------------------------------------------------
# Transforms. Each takes (value, arg) — arg is None for arg-less transforms.
# ---------------------------------------------------------------------------
def _t_uppercase(value: str, _arg: Optional[str]) -> str:
    return value.upper()


def _t_lowercase(value: str, _arg: Optional[str]) -> str:
    return value.lower()


def _t_titlecase(value: str, _arg: Optional[str]) -> str:
    return value.title()


def _t_trim(value: str, _arg: Optional[str]) -> str:
    return value.strip()


def _t_strip(value: str, arg: Optional[str]) -> str:
    return value.strip(arg) if arg else value.strip()


def _t_replace(value: str, arg: Optional[str]) -> str:
    # `replace:<from>:<to>` — arg is "from:to" (first colon splits).
    if arg is None:
        return value
    if ":" in arg:
        src, dst = arg.split(":", 1)
    else:
        src, dst = arg, ""
    return value.replace(src, dst)


def _t_normalize(value: str, _arg: Optional[str]) -> str:
    return _legacy_normalize(value)


_TRANSFORMS = {
    "uppercase": _t_uppercase,
    "lowercase": _t_lowercase,
    "titlecase": _t_titlecase,
    "trim": _t_trim,
    "strip": _t_strip,
    "replace": _t_replace,
    "normalize": _t_normalize,
    # "lookup" is handled specially because it needs the lookups dict.
}


class TemplateEngine:
    """Render dummy-EPG templates against extracted regex groups.

    Instances are cheap to construct and reusable; per-call arguments override
    per-instance defaults.
    """

    # Public caps — exposed so tests and callers can reason about them.
    # The regex-length cap is inherited from ``safe_regex.DEFAULT_MAX_PATTERN_LEN``
    # (500 chars). Conditional regex patterns are routed through ``safe_regex``
    # which handles both the length cap and the ReDoS timeout.
    MAX_TEMPLATE_LEN = 4096
    MAX_INPUT_LEN = 1024

    # Pattern for a single {placeholder} — body has no unescaped '{' or '}'.
    _PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

    def __init__(self, lookups: Optional[dict[str, dict[str, str]]] = None) -> None:
        self._lookups = lookups or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def render(
        self,
        template: str,
        groups: dict[str, Any],
        lookups: Optional[dict[str, dict[str, str]]] = None,
    ) -> str:
        if template is None:
            return ""
        if len(template) > self.MAX_TEMPLATE_LEN:
            raise TemplateSyntaxError(
                f"Template exceeds maximum length of {self.MAX_TEMPLATE_LEN} chars"
            )

        effective_lookups = lookups if lookups is not None else self._lookups
        # Truncate group values up-front so every downstream transform and
        # regex conditional operates on bounded input.
        bounded_groups = {k: self._truncate(str(v)) for k, v in groups.items()}
        return self._render_segment(template, bounded_groups, effective_lookups)

    def render_with_trace(
        self,
        template: str,
        groups: dict[str, Any],
        lookups: Optional[dict[str, dict[str, str]]] = None,
    ) -> tuple[str, list[dict]]:
        """Render template and return (output, trace).

        Trace entries:
          - {"kind": "literal", "text": <str>}
          - {"kind": "placeholder", "raw": "{name|pipe}", "group_name": <str>,
              "initial_value": <str>, "pipes": [<pipe_step>...], "final_value": <str>}
          - {"kind": "conditional", "condition": <str>, "kind_detail": "truthy"|
              "equality"|"regex", "taken": <bool>, "value": <str>, "body": [...]}
        Pipe step: {"transform": <str>, "arg": <str|None>, "input": <str>,
                     "output": <str>, "source"?: <str> (e.g. lookup table name),
                     "matched"?: <bool>}
        """
        if template is None:
            return "", []
        if len(template) > self.MAX_TEMPLATE_LEN:
            raise TemplateSyntaxError(
                f"Template exceeds maximum length of {self.MAX_TEMPLATE_LEN} chars"
            )
        effective_lookups = lookups if lookups is not None else self._lookups
        bounded_groups = {k: self._truncate(str(v)) for k, v in groups.items()}
        trace: list[dict] = []
        output = self._render_segment(template, bounded_groups, effective_lookups, trace_out=trace)
        return output, trace

    # ------------------------------------------------------------------
    # Core rendering
    # ------------------------------------------------------------------
    def _render_segment(
        self,
        template: str,
        groups: dict[str, str],
        lookups: dict[str, dict[str, str]],
        trace_out: Optional[list[dict]] = None,
    ) -> str:
        out: list[str] = []
        i = 0
        n = len(template)

        while i < n:
            brace = template.find("{", i)
            if brace == -1:
                tail = template[i:]
                out.append(tail)
                if trace_out is not None and tail:
                    trace_out.append({"kind": "literal", "text": tail})
                break

            # Emit any leading literal text.
            if brace > i:
                literal = template[i:brace]
                out.append(literal)
                if trace_out is not None:
                    trace_out.append({"kind": "literal", "text": literal})

            close = template.find("}", brace)
            if close == -1:
                raise TemplateSyntaxError(f"Unclosed '{{' at position {brace}")

            directive = template[brace + 1 : close]

            if directive.startswith("if:"):
                body_start = close + 1
                body_end, after = self._find_matching_endif(template, body_start)
                condition = directive[3:]
                body_trace: list[dict] = []
                taken, detail = self._evaluate_condition(condition, groups)
                if taken:
                    out.append(self._render_segment(
                        template[body_start:body_end], groups, lookups,
                        trace_out=body_trace if trace_out is not None else None,
                    ))
                if trace_out is not None:
                    trace_out.append({
                        "kind": "conditional",
                        "condition": condition,
                        "kind_detail": detail["kind"],
                        "taken": taken,
                        "value": detail.get("value", ""),
                        "body": body_trace,
                    })
                i = after
                continue

            if directive == "/if":
                raise TemplateSyntaxError("Unmatched '{/if}'")

            out.append(self._render_placeholder(directive, groups, lookups, trace_out=trace_out))
            i = close + 1

        return "".join(out)

    # ------------------------------------------------------------------
    # {if:...} helpers
    # ------------------------------------------------------------------
    def _find_matching_endif(self, template: str, body_start: int) -> tuple[int, int]:
        """Return (body_end, after_endif) for the nearest matching {/if}.

        Handles nested conditionals by tracking {if:...} depth.
        """
        depth = 1
        i = body_start
        n = len(template)
        while i < n:
            b = template.find("{", i)
            if b == -1:
                break
            c = template.find("}", b)
            if c == -1:
                break
            directive = template[b + 1 : c]
            if directive.startswith("if:"):
                depth += 1
            elif directive == "/if":
                depth -= 1
                if depth == 0:
                    return b, c + 1
            i = c + 1
        raise TemplateSyntaxError("'{if:...}' without matching '{/if}'")

    def _evaluate_condition(
        self, condition: str, groups: dict[str, str]
    ) -> tuple[bool, dict]:
        """Evaluate a conditional. Returns (taken, detail) where detail describes
        the evaluation kind and the value examined, for trace display."""
        # Equality first so a '=' inside the regex doesn't get mis-parsed.
        if "=" in condition and "~" not in condition.split("=", 1)[0]:
            name, expected = condition.split("=", 1)
            value = groups.get(name, "")
            return value == expected, {"kind": "equality", "value": value}

        if "~" in condition:
            name, pattern = condition.split("~", 1)
            value = groups.get(name, "")
            # Fallback contract (bd-eio04.16): route user-supplied regex through
            # safe_regex. ``compile`` raises ``PatternTooLongError`` for oversize
            # patterns and ``SafeRegexError`` for malformed patterns — both are
            # caught below and collapse the conditional to false rather than
            # crashing the render. ``search`` on the compiled pattern returns
            # None on ReDoS timeout (logged by safe_regex), again collapsing to
            # false. A single bad user config therefore skips a {if:...~...}
            # block rather than bringing down XMLTV generation.
            try:
                compiled = safe_regex.compile(pattern)
            except safe_regex.PatternTooLongError:
                return False, {"kind": "regex", "value": value, "error": "pattern too long"}
            except safe_regex.SafeRegexError as err:
                return False, {"kind": "regex", "value": value, "error": str(err)}
            matched = safe_regex.search(compiled, value) is not None
            return matched, {"kind": "regex", "value": value}

        # {if:group} — truthy if non-empty.
        value = groups.get(condition, "")
        return bool(value), {"kind": "truthy", "value": value}

    # ------------------------------------------------------------------
    # {placeholder|pipe|...} rendering
    # ------------------------------------------------------------------
    def _render_placeholder(
        self,
        body: str,
        groups: dict[str, str],
        lookups: dict[str, dict[str, str]],
        trace_out: Optional[list[dict]] = None,
    ) -> str:
        parts = body.split("|")
        name = parts[0].strip()
        pipes = parts[1:]
        raw = "{" + body + "}"

        # Legacy suffix: {name_normalize} → render with the legacy-normalize pipe.
        if name.endswith("_normalize") and name not in groups:
            base = name[: -len("_normalize")]
            initial = groups.get(base, "")
            final = _legacy_normalize(initial)
            if trace_out is not None:
                trace_out.append({
                    "kind": "placeholder",
                    "raw": raw,
                    "group_name": base,
                    "initial_value": initial,
                    "pipes": [{
                        "transform": "normalize",
                        "arg": None,
                        "input": initial,
                        "output": final,
                        "source": "legacy _normalize suffix",
                    }],
                    "final_value": final,
                })
            return final

        initial = groups.get(name, "")
        value = initial
        pipe_steps: list[dict] = []

        for pipe_spec in pipes:
            pipe_spec = pipe_spec.strip()
            if not pipe_spec:
                continue

            if ":" in pipe_spec:
                transform, arg = pipe_spec.split(":", 1)
            else:
                transform, arg = pipe_spec, None

            step_input = value
            if transform == "lookup":
                if arg is None:
                    raise TemplateSyntaxError("lookup transform requires a table name")
                if arg not in lookups:
                    raise TemplateSyntaxError(f"Unknown lookup table: {arg!r}")
                table = lookups[arg]
                matched = value in table
                value = table[value] if matched else value
                if trace_out is not None:
                    pipe_steps.append({
                        "transform": "lookup",
                        "arg": arg,
                        "input": step_input,
                        "output": value,
                        "source": arg,
                        "matched": matched,
                    })
                continue

            fn = _TRANSFORMS.get(transform)
            if fn is None:
                raise TemplateSyntaxError(f"Unknown transform: {transform!r}")
            value = fn(value, arg)
            if trace_out is not None:
                pipe_steps.append({
                    "transform": transform,
                    "arg": arg,
                    "input": step_input,
                    "output": value,
                })

        final = self._truncate(value)
        if trace_out is not None:
            trace_out.append({
                "kind": "placeholder",
                "raw": raw,
                "group_name": name,
                "initial_value": initial,
                "pipes": pipe_steps,
                "final_value": final,
            })
        return final

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @classmethod
    def _truncate(cls, value: str) -> str:
        if len(value) > cls.MAX_INPUT_LEN:
            return value[: cls.MAX_INPUT_LEN]
        return value


# ---------------------------------------------------------------------------
# Convenience wrapper matching the API the tests expect.
# ---------------------------------------------------------------------------
def render(
    template: str,
    groups: dict[str, Any],
    lookups: Optional[dict[str, dict[str, str]]] = None,
) -> str:
    return TemplateEngine().render(template, groups, lookups=lookups)
