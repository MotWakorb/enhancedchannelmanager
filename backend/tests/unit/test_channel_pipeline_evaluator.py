"""
Unit tests for the channel_pipeline_evaluator module.

Tests condition evaluation against stream contexts.
"""
from channel_pipeline_evaluator import (
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

    def test_stream_group_is_exact_match(self):
        """Matches exact provider group name."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name="USA Sports")

        result = evaluator.evaluate(
            {"type": "stream_group_is", "value": "USA Sports"},
            ctx
        )
        assert result.matched is True

    def test_stream_group_is_case_insensitive_by_default(self):
        """Case-insensitive by default, matching stream_group_matches/contains."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name="USA Sports")

        result = evaluator.evaluate(
            {"type": "stream_group_is", "value": "usa sports"},
            ctx
        )
        assert result.matched is True

    def test_stream_group_is_case_sensitive_opt_in(self):
        """case_sensitive=True requires an exact-case match."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name="USA Sports")

        result = evaluator.evaluate(
            {"type": "stream_group_is", "value": "usa sports", "case_sensitive": True},
            ctx
        )
        assert result.matched is False

    def test_stream_group_is_no_partial_match(self):
        """Unlike stream_group_contains, a substring does NOT match."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name="USA Sports HD")

        result = evaluator.evaluate(
            {"type": "stream_group_is", "value": "USA Sports"},
            ctx
        )
        assert result.matched is False

    def test_stream_group_is_empty_group(self):
        """Handles missing group name."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name=None)

        result = evaluator.evaluate(
            {"type": "stream_group_is", "value": "USA Sports"},
            ctx
        )
        assert result.matched is False

    def test_stream_group_is_negated(self):
        """negate:true inverts the match, honoring the shared negate field."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", group_name="UK Sports")

        result = evaluator.evaluate(
            {"type": "stream_group_is", "value": "USA Sports", "negate": True},
            ctx
        )
        assert result.matched is True


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


class TestConditionEvaluatorLogoExists:
    """Tests for the logo_exists condition (u8qr6.4 — was uncovered).

    Evaluated inline against ``context.logo_url``: value=True matches when a
    logo URL is present, value=False matches when it is absent, and value
    defaults to True when omitted.
    """

    def test_logo_exists_true_when_logo_present(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", logo_url="http://x/logo.png")

        result = evaluator.evaluate({"type": "logo_exists", "value": True}, ctx)
        assert result.matched is True

    def test_logo_exists_true_no_match_when_logo_absent(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", logo_url=None)

        result = evaluator.evaluate({"type": "logo_exists", "value": True}, ctx)
        assert result.matched is False

    def test_logo_exists_false_matches_when_logo_absent(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", logo_url=None)

        result = evaluator.evaluate({"type": "logo_exists", "value": False}, ctx)
        assert result.matched is True

    def test_logo_exists_defaults_to_true(self):
        """Omitting value defaults the expectation to 'has a logo'."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", logo_url="http://x/logo.png")

        result = evaluator.evaluate({"type": "logo_exists"}, ctx)
        assert result.matched is True


class TestConditionEvaluatorCodecIs:
    """Tests for the codec_is condition (u8qr6.4 — was uncovered).

    Matches ``context.video_codec`` case-insensitively against a single codec
    or a list; no codec info never matches.
    """

    def test_codec_is_single_match_case_insensitive(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", video_codec="h264")

        result = evaluator.evaluate({"type": "codec_is", "value": "H264"}, ctx)
        assert result.matched is True

    def test_codec_is_single_no_match(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", video_codec="h264")

        result = evaluator.evaluate({"type": "codec_is", "value": "hevc"}, ctx)
        assert result.matched is False

    def test_codec_is_list_match(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", video_codec="hevc")

        result = evaluator.evaluate({"type": "codec_is", "value": ["h264", "hevc"]}, ctx)
        assert result.matched is True

    def test_codec_is_no_codec_info_no_match(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", video_codec=None)

        result = evaluator.evaluate({"type": "codec_is", "value": "h264"}, ctx)
        assert result.matched is False


class TestConditionEvaluatorHasAudioTracks:
    """Tests for the has_audio_tracks condition (u8qr6.4 — was uncovered).

    Matches when ``context.audio_tracks`` is at least the requested minimum
    (value defaults to 1 when omitted/falsy).
    """

    def test_has_audio_tracks_meets_minimum(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", audio_tracks=2)

        result = evaluator.evaluate({"type": "has_audio_tracks", "value": 2}, ctx)
        assert result.matched is True

    def test_has_audio_tracks_below_minimum(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", audio_tracks=1)

        result = evaluator.evaluate({"type": "has_audio_tracks", "value": 3}, ctx)
        assert result.matched is False

    def test_has_audio_tracks_defaults_minimum_to_one(self):
        """A falsy/omitted value falls back to a minimum of 1 track."""
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", audio_tracks=1)

        result = evaluator.evaluate({"type": "has_audio_tracks"}, ctx)
        assert result.matched is True


class TestConditionEvaluatorChannelInGroup:
    """Tests for the channel_in_group condition (u8qr6.4 — was uncovered).

    Looks up the stream's existing channel by id and compares its group id to
    the requested group.
    """

    def test_channel_in_group_match(self):
        channels = [{"id": 100, "name": "ESPN", "channel_group_id": 5}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=100)

        result = evaluator.evaluate({"type": "channel_in_group", "value": 5}, ctx)
        assert result.matched is True

    def test_channel_in_group_wrong_group(self):
        channels = [{"id": 100, "name": "ESPN", "channel_group_id": 5}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=100)

        result = evaluator.evaluate({"type": "channel_in_group", "value": 6}, ctx)
        assert result.matched is False

    def test_channel_in_group_no_channel(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=None)

        result = evaluator.evaluate({"type": "channel_in_group", "value": 5}, ctx)
        assert result.matched is False

    def test_channel_in_group_channel_not_found(self):
        """channel_id set but not in the existing-channels map → no match."""
        evaluator = ConditionEvaluator(existing_channels=[])
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=999)

        result = evaluator.evaluate({"type": "channel_in_group", "value": 5}, ctx)
        assert result.matched is False


class TestConditionEvaluatorChannelHasStreams:
    """Tests for the channel_has_streams condition (u8qr6.4 — was uncovered).

    Matches when the stream's existing channel already has at least N streams.
    """

    def test_channel_has_streams_meets_minimum(self):
        channels = [{"id": 100, "name": "ESPN", "streams": [1, 2, 3]}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=100)

        result = evaluator.evaluate({"type": "channel_has_streams", "value": 2}, ctx)
        assert result.matched is True

    def test_channel_has_streams_below_minimum(self):
        channels = [{"id": 100, "name": "ESPN", "streams": [1]}]
        evaluator = ConditionEvaluator(existing_channels=channels)
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=100)

        result = evaluator.evaluate({"type": "channel_has_streams", "value": 3}, ctx)
        assert result.matched is False

    def test_channel_has_streams_no_channel(self):
        evaluator = ConditionEvaluator()
        ctx = StreamContext(stream_id=1, stream_name="ESPN", channel_id=None)

        result = evaluator.evaluate({"type": "channel_has_streams", "value": 1}, ctx)
        assert result.matched is False


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
