"""
Integration tests for the dummy EPG template engine through the preview
endpoints. Covers edge cases the unit tests can't exercise end-to-end:
nested conditionals, missing lookups, invalid regex in conditionals, and
backwards compatibility with the legacy {name_normalize} syntax.
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


class TestLookupEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_key_passes_value_through(self, async_client):
        """Unknown key in a lookup table → input value renders unchanged."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ZZ",
            "title_pattern": r"(?P<code>.+)",
            "title_template": "{code|lookup:countries}",
            "inline_lookups": {"countries": {"US": "United States"}},
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "ZZ"

    @pytest.mark.asyncio
    async def test_unknown_table_falls_back_to_raw_template(self, async_client):
        """A reference to a table that wasn't declared is a typo, but the
        engine swallows the TemplateSyntaxError and renders the raw template
        so a single bad profile doesn't tank an XMLTV refresh. The surface
        is intentionally visible — the field shows the unrendered tokens so
        the user can spot the bug."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "ESPN",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "{name|lookup:not_a_table}",
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "{name|lookup:not_a_table}"

    @pytest.mark.asyncio
    async def test_lookup_chained_after_case_transform(self, async_client):
        """Chained pipes: case change first, lookup second, correct hit."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "espn",
            "title_pattern": r"(?P<name>.+)",
            "title_template": "{name|uppercase|lookup:stations}",
            "inline_lookups": {"stations": {"ESPN": "Entertainment Sports"}},
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "Entertainment Sports"


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
        huge = "a?" * 260  # 520 chars, past MAX_REGEX_LEN (500)
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


class TestGlobalLookupResolution:
    """Tables created via /api/lookup-tables are resolvable from the preview
    endpoint by ID — this is the happy path the preview UI relies on to show
    global lookup values."""

    @pytest.mark.asyncio
    async def test_global_lookup_resolves_end_to_end(self, async_client):
        created = (await async_client.post(
            "/api/lookup-tables",
            json={"name": "leagues", "entries": {"nfl": "National Football League"}},
        )).json()

        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "nfl",
            "title_pattern": r"(?P<code>.+)",
            "title_template": "{code|lookup:leagues}",
            "global_lookup_ids": [created["id"]],
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "National Football League"

    @pytest.mark.asyncio
    async def test_missing_global_id_is_ignored_without_error(self, async_client):
        """Stale global_lookup_ids (e.g. a table was deleted) shouldn't crash
        the preview — absent IDs are simply skipped during resolution."""
        response = await async_client.post("/api/dummy-epg/preview", json={
            "sample_name": "US",
            "title_pattern": r"(?P<code>.+)",
            "title_template": "{code}",   # no lookup pipe — only the IDs list is stale
            "global_lookup_ids": [99999],
            "event_timezone": "UTC",
            "program_duration": 180,
        })
        assert response.status_code == 200
        assert response.json()["rendered"]["title"] == "US"
