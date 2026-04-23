"""
Unit tests for the dummy EPG template engine.

Syntax covered:
  - Placeholders:     {name}
  - Pipe transforms:  {name|uppercase|trim}
  - Lookup pipes:     {name|lookup:tablename}
  - Conditionals:     {if:group}...{/if}
                      {if:group=value}...{/if}
                      {if:group~regex}...{/if}
  - Legacy:           {name_normalize}
"""
import pytest

import safe_regex
from template_engine import (
    TemplateEngine,
    TemplateSyntaxError,
    render,
)


# ----------------------------------------------------------------------------
# Placeholders
# ----------------------------------------------------------------------------

class TestPlaceholders:
    def test_single_placeholder(self):
        assert render("Hello {name}", {"name": "World"}) == "Hello World"

    def test_multiple_placeholders(self):
        assert render("{a} and {b}", {"a": "foo", "b": "bar"}) == "foo and bar"

    def test_missing_group_renders_as_empty(self):
        assert render("x={missing}y", {}) == "x=y"

    def test_literal_braces_outside_placeholder(self):
        # Plain text with no placeholders passes through unchanged.
        assert render("no braces here", {}) == "no braces here"

    def test_empty_template(self):
        assert render("", {"anything": "x"}) == ""

    def test_no_placeholders_in_template(self):
        assert render("static text", {"ignored": "x"}) == "static text"


# ----------------------------------------------------------------------------
# Legacy _normalize suffix (backwards compatibility with old applyTemplate)
# ----------------------------------------------------------------------------

class TestLegacyNormalize:
    def test_normalize_strips_non_alphanumeric_and_lowercases(self):
        # Matches the pre-existing frontend behavior: lowercase, keep a-z0-9 only.
        assert render("{name_normalize}", {"name": "ESPN 2 (HD)"}) == "espn2hd"

    def test_normalize_with_already_clean_value(self):
        assert render("{city_normalize}", {"city": "seattle"}) == "seattle"

    def test_normalize_on_empty_group(self):
        assert render("{name_normalize}", {"name": ""}) == ""


# ----------------------------------------------------------------------------
# Pipe transforms
# ----------------------------------------------------------------------------

class TestPipeTransforms:
    def test_uppercase(self):
        assert render("{name|uppercase}", {"name": "hello"}) == "HELLO"

    def test_lowercase(self):
        assert render("{name|lowercase}", {"name": "HELLO"}) == "hello"

    def test_titlecase(self):
        assert render("{name|titlecase}", {"name": "the espn network"}) == "The Espn Network"

    def test_trim(self):
        assert render("{name|trim}", {"name": "  hello  "}) == "hello"

    def test_strip_with_char_arg(self):
        assert render("{name|strip:-}", {"name": "--hello--"}) == "hello"

    def test_strip_multiple_chars(self):
        assert render("{name|strip:-_}", {"name": "_-hello-_"}) == "hello"

    def test_replace(self):
        assert render("{name|replace:foo:bar}", {"name": "foo-world"}) == "bar-world"

    def test_replace_removes_all_occurrences(self):
        assert render("{name|replace:x:}", {"name": "xaxbxc"}) == "abc"

    def test_normalize_as_pipe(self):
        assert render("{name|normalize}", {"name": "ESPN 2 (HD)"}) == "espn2hd"

    def test_chained_pipes_left_to_right(self):
        # strip dashes → trim whitespace → uppercase
        assert render("{name|strip:-|trim|uppercase}", {"name": "-- hello --"}) == "HELLO"

    def test_unknown_transform_raises(self):
        with pytest.raises(TemplateSyntaxError):
            render("{name|bogus}", {"name": "x"})

    def test_pipe_on_missing_group_returns_empty(self):
        # A missing group → empty string before the pipe, transforms apply to empty.
        assert render("{missing|uppercase}", {}) == ""


# ----------------------------------------------------------------------------
# Lookup tables
# ----------------------------------------------------------------------------

class TestLookups:
    def test_lookup_resolves_key_to_value(self):
        lookups = {"callsigns": {"ESPN": "espn.com"}}
        assert render("{name|lookup:callsigns}", {"name": "ESPN"}, lookups=lookups) == "espn.com"

    def test_lookup_missing_key_falls_back_to_input(self):
        lookups = {"callsigns": {"ESPN": "espn.com"}}
        assert render("{name|lookup:callsigns}", {"name": "UNKNOWN"}, lookups=lookups) == "UNKNOWN"

    def test_lookup_unknown_table_raises(self):
        with pytest.raises(TemplateSyntaxError):
            render("{name|lookup:nonexistent}", {"name": "ESPN"}, lookups={})

    def test_lookup_chained_after_transform(self):
        # uppercase first, then look up
        lookups = {"stations": {"ESPN": "Entertainment Sports Programming Network"}}
        assert (
            render("{name|uppercase|lookup:stations}", {"name": "espn"}, lookups=lookups)
            == "Entertainment Sports Programming Network"
        )

    def test_lookup_with_no_lookups_dict_raises(self):
        with pytest.raises(TemplateSyntaxError):
            render("{name|lookup:callsigns}", {"name": "ESPN"})


# ----------------------------------------------------------------------------
# Conditionals
# ----------------------------------------------------------------------------

class TestConditionals:
    def test_if_group_non_empty_renders_body(self):
        tpl = "{if:city}City: {city}{/if}"
        assert render(tpl, {"city": "Seattle"}) == "City: Seattle"

    def test_if_group_empty_omits_body(self):
        tpl = "{if:city}City: {city}{/if}"
        assert render(tpl, {"city": ""}) == ""

    def test_if_group_missing_omits_body(self):
        tpl = "{if:city}City: {city}{/if}"
        assert render(tpl, {}) == ""

    def test_if_equality_match(self):
        tpl = "{if:type=sports}SPORTS{/if}"
        assert render(tpl, {"type": "sports"}) == "SPORTS"

    def test_if_equality_no_match(self):
        tpl = "{if:type=sports}SPORTS{/if}"
        assert render(tpl, {"type": "news"}) == ""

    def test_if_regex_match(self):
        tpl = "{if:channel~^ESPN}espn channel{/if}"
        assert render(tpl, {"channel": "ESPN2"}) == "espn channel"

    def test_if_regex_no_match(self):
        tpl = "{if:channel~^ESPN}espn channel{/if}"
        assert render(tpl, {"channel": "CNN"}) == ""

    def test_if_regex_invalid_pattern_is_false(self):
        # Invalid regex should not crash — the condition simply evaluates false.
        tpl = "{if:channel~[unclosed}body{/if}"
        assert render(tpl, {"channel": "ESPN"}) == ""

    def test_conditional_content_resolves_placeholders(self):
        tpl = "{if:city}Located in {city|uppercase}.{/if}"
        assert render(tpl, {"city": "denver"}) == "Located in DENVER."

    def test_nested_conditionals(self):
        tpl = "{if:a}A:{a}{if:b}-B:{b}{/if}{/if}"
        assert render(tpl, {"a": "1", "b": "2"}) == "A:1-B:2"
        assert render(tpl, {"a": "1"}) == "A:1"
        assert render(tpl, {"b": "2"}) == ""

    def test_conditional_without_close_raises(self):
        with pytest.raises(TemplateSyntaxError):
            render("{if:city}unclosed", {"city": "x"})

    def test_unmatched_closing_raises(self):
        with pytest.raises(TemplateSyntaxError):
            render("no open{/if}", {})


# ----------------------------------------------------------------------------
# ReDoS / input-length guards
# ----------------------------------------------------------------------------

class TestGuards:
    def test_template_too_long_rejected(self):
        big = "x" * (TemplateEngine.MAX_TEMPLATE_LEN + 1)
        with pytest.raises(TemplateSyntaxError):
            render(big, {})

    def test_group_value_too_long_truncated(self):
        # Oversized user-controlled input is truncated before regex conditionals
        # evaluate, so catastrophic backtracking is bounded.
        big_value = "a" * (TemplateEngine.MAX_INPUT_LEN + 100)
        out = render("{x|uppercase}", {"x": big_value})
        assert len(out) <= TemplateEngine.MAX_INPUT_LEN

    def test_regex_length_capped_in_conditional(self):
        # A regex longer than the safe_regex cap disables the conditional
        # (evaluates false) rather than raising, so a single bad user config
        # doesn't crash pipeline. The cap now lives in safe_regex —
        # template_engine no longer has its own MAX_REGEX_LEN.
        cap = safe_regex.DEFAULT_MAX_PATTERN_LEN
        huge_regex = "(" + "a?" * (cap // 2 + 10) + ")" + "a" * 20
        tpl = "{if:x~" + huge_regex + "}match{/if}"
        assert render(tpl, {"x": "a" * 20}) == ""


# ----------------------------------------------------------------------------
# TemplateEngine class (for callers that need a reusable compiled instance)
# ----------------------------------------------------------------------------

class TestRenderWithTrace:
    """Trace mode annotates each segment so the preview UI can visualize
    pipe pipelines, conditional branches, and lookup hits/misses."""

    def test_literal_only_trace(self):
        engine = TemplateEngine()
        out, trace = engine.render_with_trace("hello world", {})
        assert out == "hello world"
        assert trace == [{"kind": "literal", "text": "hello world"}]

    def test_placeholder_with_pipes_trace(self):
        engine = TemplateEngine()
        out, trace = engine.render_with_trace("{name|uppercase|trim}", {"name": "  hi  "})
        assert out == "HI"
        placeholder = [t for t in trace if t["kind"] == "placeholder"][0]
        assert placeholder["group_name"] == "name"
        assert placeholder["initial_value"] == "  hi  "
        assert placeholder["final_value"] == "HI"
        assert [p["transform"] for p in placeholder["pipes"]] == ["uppercase", "trim"]
        assert placeholder["pipes"][0]["input"] == "  hi  "
        assert placeholder["pipes"][0]["output"] == "  HI  "
        assert placeholder["pipes"][1]["input"] == "  HI  "
        assert placeholder["pipes"][1]["output"] == "HI"

    def test_lookup_hit_annotates_matched_true(self):
        engine = TemplateEngine()
        out, trace = engine.render_with_trace(
            "{code|lookup:countries}", {"code": "US"}, lookups={"countries": {"US": "USA"}}
        )
        assert out == "USA"
        pipe = trace[0]["pipes"][0]
        assert pipe["transform"] == "lookup"
        assert pipe["source"] == "countries"
        assert pipe["matched"] is True

    def test_lookup_miss_annotates_matched_false(self):
        engine = TemplateEngine()
        out, trace = engine.render_with_trace(
            "{code|lookup:countries}", {"code": "XX"}, lookups={"countries": {"US": "USA"}}
        )
        assert out == "XX"
        pipe = trace[0]["pipes"][0]
        assert pipe["matched"] is False

    def test_conditional_taken_includes_body_trace(self):
        engine = TemplateEngine()
        out, trace = engine.render_with_trace(
            "{if:sport=nfl}Go {team|uppercase}{/if}",
            {"sport": "nfl", "team": "chiefs"},
        )
        assert out == "Go CHIEFS"
        cond = [t for t in trace if t["kind"] == "conditional"][0]
        assert cond["condition"] == "sport=nfl"
        assert cond["kind_detail"] == "equality"
        assert cond["taken"] is True
        # Body trace should contain the literal "Go " and the {team|uppercase}.
        body_kinds = [b["kind"] for b in cond["body"]]
        assert "placeholder" in body_kinds

    def test_conditional_not_taken_empty_body(self):
        engine = TemplateEngine()
        _, trace = engine.render_with_trace(
            "{if:sport=nba}never{/if}", {"sport": "nfl"}
        )
        cond = [t for t in trace if t["kind"] == "conditional"][0]
        assert cond["taken"] is False
        assert cond["body"] == []

    def test_legacy_normalize_traced(self):
        engine = TemplateEngine()
        _, trace = engine.render_with_trace("{name_normalize}", {"name": "ESPN 2"})
        placeholder = trace[0]
        assert placeholder["group_name"] == "name"
        assert placeholder["final_value"] == "espn2"
        assert placeholder["pipes"][0]["source"] == "legacy _normalize suffix"


class TestTemplateEngineClass:
    def test_reusable_across_renders(self):
        engine = TemplateEngine()
        assert engine.render("{x|uppercase}", {"x": "a"}) == "A"
        assert engine.render("{y|lowercase}", {"y": "B"}) == "b"

    def test_lookups_set_on_instance(self):
        engine = TemplateEngine(lookups={"flags": {"usa": "🇺🇸"}})
        assert engine.render("{country|lookup:flags}", {"country": "usa"}) == "🇺🇸"

    def test_render_level_lookups_override_instance_lookups(self):
        engine = TemplateEngine(lookups={"tbl": {"a": "from-instance"}})
        out = engine.render("{k|lookup:tbl}", {"k": "a"}, lookups={"tbl": {"a": "from-call"}})
        assert out == "from-call"


# ----------------------------------------------------------------------------
# bd-eio04.16 — ReDoS resilience in {if:x~regex} conditionals.
#
# The migrated call site routes user-supplied regex through
# safe_regex.compile + safe_regex.search. Adversarial patterns must not hang
# XMLTV rendering; the conditional collapses to false.
# ----------------------------------------------------------------------------

import time


class TestRegexConditionalRedosResilience:
    _ADVERSARIAL_PATTERN = r"(a+)+b"
    _GENUINE_REDOS_PATTERN = r"(a|aa)+b"
    _ADVERSARIAL_TEXT = "a" * 30 + "!"
    _WALL_CLOCK_BUDGET_MS = 500

    def test_adversarial_pattern_condition_is_false_within_budget(self):
        """{if:name~(a+)+b} against a long input collapses to false without
        stalling the render."""
        tpl = "before-{if:name~" + self._ADVERSARIAL_PATTERN + "}MATCH{/if}-after"
        start = time.monotonic()
        out = render(tpl, {"name": self._ADVERSARIAL_TEXT})
        elapsed_ms = (time.monotonic() - start) * 1000
        assert out == "before--after"
        assert elapsed_ms < self._WALL_CLOCK_BUDGET_MS, f"elapsed {elapsed_ms:.1f}ms"

    def test_genuine_redos_pattern_condition_is_false_within_budget(self):
        """Genuine catastrophic-backtracking pattern exercises the safe_regex
        timeout path; conditional still evaluates to false."""
        tpl = "ok-{if:name~" + self._GENUINE_REDOS_PATTERN + "}HIT{/if}-done"
        start = time.monotonic()
        out = render(tpl, {"name": self._ADVERSARIAL_TEXT})
        elapsed_ms = (time.monotonic() - start) * 1000
        assert out == "ok--done"
        assert elapsed_ms < self._WALL_CLOCK_BUDGET_MS, f"elapsed {elapsed_ms:.1f}ms"

    def test_trace_mode_reports_regex_kind_on_adversarial_pattern(self):
        """When tracing is on, the adversarial-pattern conditional still
        reports kind_detail='regex' and taken=False — the UI can show the
        regex was not usable."""
        engine = TemplateEngine()
        tpl = "{if:name~" + self._GENUINE_REDOS_PATTERN + "}HIT{/if}"
        _, trace = engine.render_with_trace(tpl, {"name": self._ADVERSARIAL_TEXT})
        conds = [t for t in trace if t["kind"] == "conditional"]
        assert len(conds) == 1
        assert conds[0]["kind_detail"] == "regex"
        assert conds[0]["taken"] is False
