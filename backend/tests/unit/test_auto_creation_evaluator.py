"""
Unit tests for the auto_creation_evaluator module.

Tests condition evaluation against stream contexts.
"""
from auto_creation_evaluator import (
    ConditionEvaluator,
    StreamContext,
    EvaluationResult,
    evaluate_conditions,
)


class TestStreamContext:
    """Tests for StreamContext."""

    def test_from_dispatcharr_stream_basic(self):
        """Creates context from basic stream data."""
        stream = {
            "id": 123,
            "name": "ESPN HD",
            "group_title": "Sports",
            "tvg_id": "espn.us",
            "logo_url": "http://example.com/logo.png",
        }
        ctx = StreamContext.from_dispatcharr_stream(stream, m3u_account_id=1)

        assert ctx.stream_id == 123
        assert ctx.stream_name == "ESPN HD"
        assert ctx.group_name == "Sports"
        assert ctx.tvg_id == "espn.us"
        assert ctx.logo_url == "http://example.com/logo.png"
        assert ctx.m3u_account_id == 1

    def test_from_dispatcharr_stream_with_stats(self):
        """Creates context with stream stats for quality."""
        stream = {"id": 123, "name": "ESPN HD"}
        stats = {"resolution": "1920x1080", "video_codec": "h264"}

        ctx = StreamContext.from_dispatcharr_stream(stream, stream_stats=stats)

        assert ctx.resolution == "1920x1080"
        assert ctx.resolution_height == 1080
        assert ctx.video_codec == "h264"

    def test_from_dispatcharr_stream_no_resolution(self):
        """Handles missing resolution gracefully."""
        stream = {"id": 123, "name": "ESPN"}
        ctx = StreamContext.from_dispatcharr_stream(stream)

        assert ctx.resolution is None
        assert ctx.resolution_height is None

    def test_from_dispatcharr_stream_populates_m3u_position(self):
        """Populates m3u_position from stream id."""
        stream = {"id": 456, "name": "CNN"}
        ctx = StreamContext.from_dispatcharr_stream(stream)

        assert ctx.m3u_position == 456

    def test_from_dispatcharr_stream_populates_stream_chno(self):
        """Populates stream_chno from stream data."""
        stream = {"id": 1, "name": "CNN", "stream_chno": 21262.0}
        ctx = StreamContext.from_dispatcharr_stream(stream)

        assert ctx.stream_chno == 21262.0

    def test_from_dispatcharr_stream_stream_chno_none(self):
        """stream_chno is None when not in stream data."""
        stream = {"id": 1, "name": "CNN"}
        ctx = StreamContext.from_dispatcharr_stream(stream)

        assert ctx.stream_chno is None


class TestEvaluationResult:
    """Tests for EvaluationResult."""

    def test_bool_matched(self):
        """Matched result is truthy."""
        result = EvaluationResult(matched=True, condition_type="test")
        assert bool(result) is True

    def test_bool_not_matched(self):
        """Unmatched result is falsy."""
        result = EvaluationResult(matched=False, condition_type="test")
        assert bool(result) is False


class TestConditionEvaluatorStreamName:
    """Tests for stream name conditions."""

    def test_stream_name_contains_match(self):
        """Matches substring in stream name."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD Sports")

        result = evaluator.evaluate(
            {"type": "stream_name_contains", "value": "ESPN"},
            ctx
        )
        assert result.matched is True

    def test_stream_name_contains_no_match(self):
        """Does not match missing substring."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="FOX News")

        result = evaluator.evaluate(
            {"type": "stream_name_contains", "value": "ESPN"},
            ctx
        )
        assert result.matched is False

    def test_stream_name_contains_case_insensitive(self):
        """Case insensitive by default."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="espn hd")

        result = evaluator.evaluate(
            {"type": "stream_name_contains", "value": "ESPN"},
            ctx
        )
        assert result.matched is True

    def test_stream_name_contains_case_sensitive(self):
        """Respects case_sensitive flag."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="espn hd")

        result = evaluator.evaluate(
            {"type": "stream_name_contains", "value": "ESPN", "case_sensitive": True},
            ctx
        )
        assert result.matched is False

    def test_stream_name_matches_regex(self):
        """Matches regex pattern."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD 1080p")

        result = evaluator.evaluate(
            {"type": "stream_name_matches", "value": "^ESPN.*HD"},
            ctx
        )
        assert result.matched is True

    def test_stream_name_matches_regex_no_match(self):
        """Does not match non-matching regex."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="FOX Sports")

        result = evaluator.evaluate(
            {"type": "stream_name_matches", "value": "^ESPN"},
            ctx
        )
        assert result.matched is False


class TestConditionEvaluatorStreamGroup:
    """Tests for stream group conditions."""

    def test_stream_group_matches(self):
        """Matches stream group pattern."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name="USA Sports")

        result = evaluator.evaluate(
            {"type": "stream_group_matches", "value": "^USA.*"},
            ctx
        )
        assert result.matched is True

    def test_stream_group_matches_empty_group(self):
        """Handles empty group name."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name=None)

        result = evaluator.evaluate(
            {"type": "stream_group_matches", "value": "^Sports"},
            ctx
        )
        assert result.matched is False


class TestConditionEvaluatorTvgId:
    """Tests for TVG ID conditions."""

    def test_tvg_id_exists_true(self):
        """Matches when TVG ID exists."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", tvg_id="espn.us")

        result = evaluator.evaluate(
            {"type": "tvg_id_exists", "value": True},
            ctx
        )
        assert result.matched is True

    def test_tvg_id_exists_false(self):
        """Matches when TVG ID is missing."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", tvg_id=None)

        result = evaluator.evaluate(
            {"type": "tvg_id_exists", "value": False},
            ctx
        )
        assert result.matched is True

    def test_tvg_id_matches(self):
        """Matches TVG ID pattern."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", tvg_id="espn.us")

        result = evaluator.evaluate(
            {"type": "tvg_id_matches", "value": "espn.*"},
            ctx
        )
        assert result.matched is True


class TestConditionEvaluatorQuality:
    """Tests for quality conditions."""

    def test_quality_min_match(self):
        """Matches when quality meets minimum."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", resolution_height=1080)

        result = evaluator.evaluate(
            {"type": "quality_min", "value": 720},
            ctx
        )
        assert result.matched is True

    def test_quality_min_no_match(self):
        """Does not match when quality below minimum."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", resolution_height=480)

        result = evaluator.evaluate(
            {"type": "quality_min", "value": 720},
            ctx
        )
        assert result.matched is False

    def test_quality_min_no_info(self):
        """Does not match when no quality info."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", resolution_height=None)

        result = evaluator.evaluate(
            {"type": "quality_min", "value": 720},
            ctx
        )
        assert result.matched is False

    def test_quality_max_match(self):
        """Matches when quality within maximum."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", resolution_height=720)

        result = evaluator.evaluate(
            {"type": "quality_max", "value": 1080},
            ctx
        )
        assert result.matched is True

    def test_quality_max_no_match(self):
        """Does not match when quality exceeds maximum."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", resolution_height=2160)

        result = evaluator.evaluate(
            {"type": "quality_max", "value": 1080},
            ctx
        )
        assert result.matched is False


class TestConditionEvaluatorProvider:
    """Tests for provider conditions."""

    def test_provider_is_single_match(self):
        """Matches single provider ID."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1)

        result = evaluator.evaluate(
            {"type": "provider_is", "value": 1},
            ctx
        )
        assert result.matched is True

    def test_provider_is_list_match(self):
        """Matches provider ID in list."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=2)

        result = evaluator.evaluate(
            {"type": "provider_is", "value": [1, 2, 3]},
            ctx
        )
        assert result.matched is True

    def test_provider_is_no_match(self):
        """Does not match when provider not in list."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=5)

        result = evaluator.evaluate(
            {"type": "provider_is", "value": [1, 2, 3]},
            ctx
        )
        assert result.matched is False


class TestConditionEvaluatorChannel:
    """Tests for channel-related conditions."""

    def test_has_channel_true(self):
        """Matches when stream has channel."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=100)

        result = evaluator.evaluate(
            {"type": "has_channel", "value": True},
            ctx
        )
        assert result.matched is True

    def test_has_channel_false(self):
        """Matches when stream has no channel."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=None)

        result = evaluator.evaluate(
            {"type": "has_channel", "value": False},
            ctx
        )
        assert result.matched is True

    def test_channel_exists_with_name(self):
        """Matches when channel with exact name exists."""
        channels = [{"id": 1, "name": "ESPN HD"}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="ESPN")

        result = evaluator.evaluate(
            {"type": "channel_exists_with_name", "value": "ESPN HD"},
            ctx
        )
        assert result.matched is True

    def test_channel_exists_with_name_no_match(self):
        """Does not match when channel name doesn't exist."""
        channels = [{"id": 1, "name": "FOX News"}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="ESPN")

        result = evaluator.evaluate(
            {"type": "channel_exists_with_name", "value": "ESPN HD"},
            ctx
        )
        assert result.matched is False

    def test_channel_exists_matching_regex(self):
        """Matches when channel matching regex exists."""
        channels = [{"id": 1, "name": "ESPN HD"}, {"id": 2, "name": "ESPN2"}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="Test")

        result = evaluator.evaluate(
            {"type": "channel_exists_matching", "value": "^ESPN"},
            ctx
        )
        assert result.matched is True


class TestConditionEvaluatorLogical:
    """Tests for logical operator conditions."""

    def test_and_all_match(self):
        """AND matches when all sub-conditions match."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD", resolution_height=1080)

        result = evaluator.evaluate(
            {
                "type": "and",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"},
                    {"type": "quality_min", "value": 720}
                ]
            },
            ctx
        )
        assert result.matched is True

    def test_and_one_fails(self):
        """AND does not match when one sub-condition fails."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD", resolution_height=480)

        result = evaluator.evaluate(
            {
                "type": "and",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"},
                    {"type": "quality_min", "value": 720}
                ]
            },
            ctx
        )
        assert result.matched is False

    def test_or_one_matches(self):
        """OR matches when at least one sub-condition matches."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="FOX News", resolution_height=1080)

        result = evaluator.evaluate(
            {
                "type": "or",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"},
                    {"type": "quality_min", "value": 720}
                ]
            },
            ctx
        )
        assert result.matched is True

    def test_or_none_match(self):
        """OR does not match when no sub-conditions match."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="FOX News", resolution_height=480)

        result = evaluator.evaluate(
            {
                "type": "or",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"},
                    {"type": "quality_min", "value": 720}
                ]
            },
            ctx
        )
        assert result.matched is False

    def test_not_inverts_match(self):
        """NOT inverts the sub-condition result."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="FOX News")

        result = evaluator.evaluate(
            {
                "type": "not",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"}
                ]
            },
            ctx
        )
        assert result.matched is True

    def test_not_inverts_non_match(self):
        """NOT inverts non-match to match."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD")

        result = evaluator.evaluate(
            {
                "type": "not",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"}
                ]
            },
            ctx
        )
        assert result.matched is False

    def test_nested_logical_operators(self):
        """Handles nested logical operators."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN HD",
            resolution_height=1080,
            channel_id=None
        )

        # Match: ESPN AND (HD or quality >= 720) AND no channel
        result = evaluator.evaluate(
            {
                "type": "and",
                "conditions": [
                    {"type": "stream_name_contains", "value": "ESPN"},
                    {
                        "type": "or",
                        "conditions": [
                            {"type": "stream_name_contains", "value": "HD"},
                            {"type": "quality_min", "value": 720}
                        ]
                    },
                    {
                        "type": "not",
                        "conditions": [
                            {"type": "has_channel", "value": True}
                        ]
                    }
                ]
            },
            ctx
        )
        assert result.matched is True


class TestConditionEvaluatorNegate:
    """Tests for condition negation."""

    def test_negate_flag(self):
        """Respects negate flag on condition."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD")

        result = evaluator.evaluate(
            {"type": "stream_name_contains", "value": "ESPN", "negate": True},
            ctx
        )
        assert result.matched is False


class TestConditionEvaluatorSpecial:
    """Tests for special conditions."""

    def test_always_matches(self):
        """'always' condition always matches."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="Anything")

        result = evaluator.evaluate({"type": "always"}, ctx)
        assert result.matched is True

    def test_never_matches(self):
        """'never' condition never matches."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="Anything")

        result = evaluator.evaluate({"type": "never"}, ctx)
        assert result.matched is False


class TestEvaluateConditions:
    """Tests for evaluate_conditions() convenience function."""

    def test_all_conditions_match(self):
        """Returns True when all conditions match."""
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD", resolution_height=1080)
        conditions = [
            {"type": "stream_name_contains", "value": "ESPN"},
            {"type": "quality_min", "value": 720}
        ]

        result = evaluate_conditions(conditions, ctx)
        assert result is True

    def test_one_condition_fails(self):
        """Returns False when any condition fails."""
        ctx = StreamContext(stream_id=1, stream_name="ESPN HD", resolution_height=480)
        conditions = [
            {"type": "stream_name_contains", "value": "ESPN"},
            {"type": "quality_min", "value": 720}
        ]

        result = evaluate_conditions(conditions, ctx)
        assert result is False

    def test_empty_conditions(self):
        """Returns True for empty conditions list."""
        ctx = StreamContext(stream_id=1, stream_name="ESPN")
        result = evaluate_conditions([], ctx)
        assert result is True


class TestConditionEvaluatorEPG:
    """Tests for EPG-related conditions."""

    def test_epg_title_contains_match(self):
        """Matches substring in EPG program title."""
        evaluator = ConditionEvaluator()
        prog = {"title": "MotoGP Australia", "description": "Race", "start": "2026-02-27T06:00:00Z", "stop": "2026-02-27T08:00:00Z", "source": 1}
        ctx = StreamContext(stream_id=1, stream_name="MotoGP", epg_programs=[prog])

        result = evaluator.evaluate(
            {"type": "epg_title_contains", "value": "Australia"},
            ctx
        )
        assert result.matched is True
        assert ctx.epg_match == prog
        assert ctx.matched_by_epg is True
        assert "matched 'Australia'" in result.details

    def test_epg_desc_matches_regex(self):
        """Matches regex in EPG program description."""
        evaluator = ConditionEvaluator()
        prog = {"title": "News", "description": "Live from London", "start": "2026-02-27T06:00:00Z", "stop": "2026-02-27T08:00:00Z", "source": 1}
        ctx = StreamContext(stream_id=1, stream_name="News", epg_programs=[prog])

        result = evaluator.evaluate(
            {"type": "epg_desc_matches", "value": "from [Ll]ondon"},
            ctx
        )
        assert result.matched is True
        assert ctx.epg_match == prog
        assert "segment: 'from London'" in result.details

    def test_epg_any_matches(self):
        """Matches either title or description."""
        evaluator = ConditionEvaluator()
        prog = {"title": "Sports", "description": "Football match", "start": "2026-02-27T06:00:00Z", "stop": "2026-02-27T08:00:00Z", "source": 1}
        ctx = StreamContext(stream_id=1, stream_name="Sports", epg_programs=[prog])

        # Match title
        result = evaluator.evaluate({"type": "epg_any_contains", "value": "Sports"}, ctx)
        assert result.matched is True

        # Match description
        result = evaluator.evaluate({"type": "epg_any_contains", "value": "Football"}, ctx)
        assert result.matched is True

    def test_epg_source_is_match(self):
        """Matches specific EPG source and captures 'now airing' program."""
        evaluator = ConditionEvaluator()
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        start = (now - datetime.timedelta(hours=1)).isoformat()
        stop = (now + datetime.timedelta(hours=1)).isoformat()
        
        prog = {"title": "Current Program", "start": start, "stop": stop, "source": 5}
        ctx = StreamContext(stream_id=1, stream_name="Test", epg_programs=[prog])

        result = evaluator.evaluate(
            {"type": "epg_source_is", "value": 5},
            ctx
        )
        assert result.matched is True
        assert ctx.epg_match == prog
        assert "matches (Now: 'Current Program')" in result.details

    def test_epg_source_filter_application(self):
        """EPG Source condition filters other EPG conditions in the same rule."""
        evaluator = ConditionEvaluator()
        prog1 = {"title": "MotoGP", "source": 1}
        prog2 = {"title": "MotoGP", "source": 2}
        ctx = StreamContext(stream_id=1, stream_name="Test", epg_programs=[prog1, prog2])

        # Rule has EPG source filter for source 2 (issue #6: computed once per rule)
        result = evaluator.evaluate(
            {"type": "epg_title_contains", "value": "MotoGP"},
            ctx,
            source_filter=2
        )
        assert result.matched is True
        assert ctx.epg_match == prog2  # Should match prog2 because of source filter


class TestConditionEvaluatorMultiField:
    """Tests for any_field_* conditions."""

    def test_any_field_contains_stream_name(self):
        """Matches in stream name (no EPG match)."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="MotoGP Australia")

        result = evaluator.evaluate(
            {"type": "any_field_contains", "value": "MotoGP"},
            ctx
        )
        assert result.matched is True
        assert ctx.matched_by_epg is False

    def test_any_field_contains_epg_title(self):
        """Matches in EPG title (precedence over stream name)."""
        evaluator = ConditionEvaluator()
        prog = {"title": "MotoGP Australia", "source": 1}
        ctx = StreamContext(stream_id=1, stream_name="Test Channel", epg_programs=[prog])

        result = evaluator.evaluate(
            {"type": "any_field_contains", "value": "Australia"},
            ctx
        )
        assert result.matched is True
        assert ctx.epg_match == prog
        assert ctx.matched_by_epg is True
