"""
Integration tests for the dummy EPG template engine through the preview
endpoints. Covers edge cases the unit tests can't exercise end-to-end:
nested conditionals, invalid regex in conditionals, and backwards
compatibility with the legacy {name_normalize} syntax.
"""
import pytest


class TestNestedConditionals:
    """Conditionals must nest correctly when routed through /api/dummy-epg/preview."""

    @pytest.mark.asyncio
    async def test_three_level_nesting_all_true(self, async_client):
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "A1-B2-C3",
            "title_pattern": r"(?P<a>\w+)-(?P<b>\w+)-(?P<c>\w+)",
            "title_template": "{if:a}A:{a}{if:b}|B:{b}{if:c}|C:{c}{/if}{/if}{/if}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200, response.json()
        assert response.json()["rendered"]["title"] == "A:A1|B:B2|C:C3"

    @pytest.mark.asyncio
    async def test_three_level_nesting_middle_false(self, async_client):
        """Inner conditional whose group is absent leaves its branch out —
        the outer conditional still fires because its group is present."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "A1",
            "title_pattern": r"(?P<a>\w+)",
            "title_template": "{if:a}A:{a}{if:b}|B:{b}{if:c}|C:{c}{/if}{/if}{/if}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "A:A1"

    @pytest.mark.asyncio
    async def test_nested_trace_reports_each_level(self, async_client):
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "nfl-chiefs",
            "title_pattern": r"(?P<league>\w+)-(?P<team>\w+)",
            "title_template": "{if:league}{league|uppercase}{if:team}/{team|titlecase}{/if}{/if}",
            "event_timezone": "UTC",
            "program_duration": 180,
            "include_trace": True,
        })
        assert response.status_code == 200
        trace = response.json()["traces"]["title_template"]
        outer = next(t for t in trace if t["kind"] == "conditional")
        assert outer["taken"] is True
        # Outer body must contain a placeholder (league) and a nested conditional.
        inner_conds = [t for t in outer["body"] if t["kind"] == "conditional"]
        assert len(inner_conds) == 1
        assert inner_conds[0]["taken"] is True


class TestLookupPipeRemovedEndToEnd:
    """bead enhancedchannelmanager-70u0r.1 / PO decision D2 — the
    ``|lookup:<table>`` pipe and the lookup_tables feature were removed.

    The regression this pins is the preview/production divergence that
    motivated removal: before removal, ``/preview`` resolved the pipe (because
    it merged a lookups dict) while ``generate_xmltv`` never passed one, so the
    generated XMLTV emitted the RAW template text instead. Preview now agrees
    with XMLTV — both degrade to the raw template.
    """

    @pytest.mark.asyncio
    async def test_lookup_pipe_now_falls_back_to_raw_template(self, async_client):
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "{name|lookup:stations}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "{name|lookup:stations}"

    @pytest.mark.asyncio
    async def test_retired_request_fields_are_ignored_not_rejected(self, async_client):
        """A stale client (cached bundle) may still post inline_lookups /
        global_lookup_ids. Pydantic ignores unknown fields, so the request must
        still succeed rather than 422/500."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "{name|uppercase}",
            "inline_lookups": {"stations": {"ESPN": "x"}},
            "global_lookup_ids": [1, 2, 3],
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200, response.json()
        assert response.json()["rendered"]["title"] == "ESPN"


class TestInvalidRegexInConditional:
    @pytest.mark.asyncio
    async def test_invalid_regex_evaluates_false(self, async_client):
        """An unclosed character class inside {if:x~regex}... shouldn't 500;
        the conditional simply doesn't fire."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN",
            "title_pattern": r"(?P<ch>\w+)",
            "title_template": "{ch}{if:ch~[unclosed} MATCH{/if}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "ESPN"

    @pytest.mark.asyncio
    async def test_oversized_regex_evaluates_false(self, async_client):
        """Regex pattern over 500 chars inside a conditional short-circuits
        to false rather than attempting catastrophic backtracking."""
        huge = "a?" * 260  # 520 chars, past safe_regex.DEFAULT_MAX_PATTERN_LEN (500)
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "AAAAA",
            "title_pattern": r"(?P<v>\w+)",
            "title_template": f"{{v}}{{if:v~{huge}}} MATCH{{/if}}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "AAAAA"

    @pytest.mark.asyncio
    async def test_invalid_regex_trace_records_regex_kind(self, async_client):
        """When tracing, the conditional step still reports kind_detail='regex'
        so the UI can show the regex wasn't evaluable."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN",
            "title_pattern": r"(?P<ch>\w+)",
            "title_template": "{if:ch~[bogus}body{/if}",
            "event_timezone": "UTC",
            "program_duration": 180,
            "include_trace": True,
        })
        trace = response.json()["traces"]["title_template"]
        cond = next(t for t in trace if t["kind"] == "conditional")
        assert cond["kind_detail"] == "regex"
        assert cond["taken"] is False


class TestBackwardsCompat:
    @pytest.mark.asyncio
    async def test_legacy_name_normalize_suffix_still_works(self, async_client):
        """Templates written against the pre-v0.14 engine must render the same
        output — critical because existing user configs depend on it."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN 2 (HD)",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "slug-{name_normalize}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "slug-espn2hd"

    @pytest.mark.asyncio
    async def test_legacy_normalize_inside_conditional(self, async_client):
        """Legacy suffix in a conditional body — both behaviors compose."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN 2 HD",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "{if:name}slug-{name_normalize}{/if}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "slug-espn2hd"

    @pytest.mark.asyncio
    async def test_pipe_on_missing_group_renders_empty(self, async_client):
        """A pipe chained after a missing group renders empty — matches the
        legacy behavior where {missing} rendered empty."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "Hello",
            "title_pattern": r"(?P<name>\w+)",
            # {absent} is not in any pattern group
            "title_template": "{name}-{absent|uppercase}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "Hello-"


class TestTraceShape:
    @pytest.mark.asyncio
    async def test_literal_only_template_still_returns_trace(self, async_client):
        """No placeholders — trace is a single literal entry."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "anything",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "static output",
            "event_timezone": "UTC",
            "program_duration": 180,
            "include_trace": True,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["rendered"]["title"] == "static output"
        assert body["traces"]["title_template"] == [
            {"kind": "literal", "text": "static output"}
        ]

    @pytest.mark.asyncio
    async def test_pipe_without_trace_flag_omits_traces(self, async_client):
        """include_trace defaults to False — response must not carry a traces key."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "hi",
            "title_pattern": r"(?P<v>.+)",
            "title_template": "{v|uppercase}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        body = response.json()
        assert body["rendered"]["title"] == "HI"
        assert "traces" not in body


class TestRedosResiliencePreview:
    """bd-eio04.16 — Evil regex in substitution pairs, title_pattern, or
    template conditional must not stall the preview endpoint. The endpoint
    covers the full dummy-epg pipeline; validating it here proves the
    safe_regex migration wraps every user-editable regex site reachable from
    the request body."""

    _GENUINE_REDOS_PATTERN = r"(a|aa)+b"
    _ADVERSARIAL_TEXT = "a" * 30 + "!"
    _WALL_CLOCK_BUDGET_MS = 2000  # generous for integration roundtrip

    @pytest.mark.asyncio
    async def test_evil_substitution_pair_does_not_stall(self, async_client):
        """Substitution pair with catastrophic backtracking pattern is
        gracefully dropped — the sample_name flows through unchanged."""
        import time as _time
        start = _time.monotonic()
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": self._ADVERSARIAL_TEXT,
            "substitution_pairs": [
                {"find": self._GENUINE_REDOS_PATTERN, "replace": "X", "is_regex": True, "enabled": True},
            ],
            "title_pattern": r"(?P<name>.+)",
            "title_template": "{name}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        elapsed_ms = (_time.monotonic() - start) * 1000
        assert response.status_code == 200
        assert response.json()["substituted_name"] == self._ADVERSARIAL_TEXT
        assert elapsed_ms < self._WALL_CLOCK_BUDGET_MS, f"elapsed {elapsed_ms:.0f}ms"

    @pytest.mark.asyncio
    async def test_evil_title_pattern_does_not_stall(self, async_client):
        """Catastrophic title_pattern collapses to no-match (matched=False)
        and the preview returns fallback rendering."""
        import time as _time
        start = _time.monotonic()
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": self._ADVERSARIAL_TEXT,
            "title_pattern": self._GENUINE_REDOS_PATTERN,
            "title_template": "{name}",
            "fallback_title_template": "fallback",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        elapsed_ms = (_time.monotonic() - start) * 1000
        assert response.status_code == 200
        body = response.json()
        assert body["matched"] is False
        assert body["rendered"]["fallback_title"] == "fallback"
        assert elapsed_ms < self._WALL_CLOCK_BUDGET_MS, f"elapsed {elapsed_ms:.0f}ms"

    @pytest.mark.asyncio
    async def test_evil_conditional_regex_does_not_stall(self, async_client):
        """{if:name~(a|aa)+b} conditional in a title_template renders as
        false (empty) without stalling the request."""
        import time as _time
        tpl = "OK-{if:name~" + self._GENUINE_REDOS_PATTERN + "}HIT{/if}"
        start = _time.monotonic()
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": self._ADVERSARIAL_TEXT,
            "title_pattern": r"(?P<name>.+)",
            "title_template": tpl,
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        elapsed_ms = (_time.monotonic() - start) * 1000
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "OK-"
        assert elapsed_ms < self._WALL_CLOCK_BUDGET_MS, f"elapsed {elapsed_ms:.0f}ms"
