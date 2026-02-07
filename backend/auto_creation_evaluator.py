"""
Auto-Creation Condition Evaluator Service

Evaluates conditions against streams to determine if rules should be applied.
Supports compound conditions with AND/OR/NOT operators and checks against
existing channels in the system.
"""
import re
import logging
from typing import Any, Optional
from dataclasses import dataclass

from auto_creation_schema import Condition, ConditionType


logger = logging.getLogger(__name__)


@dataclass
class StreamContext:
    """
    Context object containing all data about a stream for condition evaluation.
    Populated from Dispatcharr API response and local database.
    """
    # Stream identifiers
    stream_id: int
    stream_name: str
    stream_url: Optional[str] = None

    # Stream metadata
    group_name: Optional[str] = None
    tvg_id: Optional[str] = None
    tvg_name: Optional[str] = None
    logo_url: Optional[str] = None

    # Provider info
    m3u_account_id: Optional[int] = None
    m3u_account_name: Optional[str] = None

    # Quality info (from StreamStats if probed)
    resolution: Optional[str] = None  # e.g., "1920x1080"
    resolution_height: Optional[int] = None  # e.g., 1080
    video_codec: Optional[str] = None  # e.g., "h264", "hevc"
    audio_codec: Optional[str] = None
    audio_tracks: int = 1
    bitrate: Optional[int] = None

    # Channel association
    channel_id: Optional[int] = None  # Existing channel this stream belongs to
    channel_name: Optional[str] = None

    # Normalized name (after normalization rules applied)
    normalized_name: Optional[str] = None

    @classmethod
    def from_dispatcharr_stream(cls, stream: dict, m3u_account_id: int = None,
                                 m3u_account_name: str = None,
                                 stream_stats: dict = None) -> "StreamContext":
        """
        Create StreamContext from Dispatcharr stream API response.

        Args:
            stream: Stream dict from Dispatcharr API
            m3u_account_id: M3U account ID this stream belongs to
            m3u_account_name: M3U account name
            stream_stats: Optional StreamStats record for quality info
        """
        # Parse resolution from stream_stats
        resolution_height = None
        if stream_stats and stream_stats.get("resolution"):
            try:
                # Format is "1920x1080"
                parts = stream_stats["resolution"].split("x")
                if len(parts) == 2:
                    resolution_height = int(parts[1])
            except (ValueError, IndexError):
                pass

        return cls(
            stream_id=stream.get("id"),
            stream_name=stream.get("name", ""),
            stream_url=stream.get("url"),
            group_name=stream.get("group_title") or stream.get("channel_group_name") or stream.get("m3u_group_name"),
            tvg_id=stream.get("tvg_id"),
            tvg_name=stream.get("tvg_name"),
            logo_url=stream.get("logo_url") or stream.get("tvg_logo"),
            m3u_account_id=m3u_account_id,
            m3u_account_name=m3u_account_name,
            channel_id=stream.get("channel_id") or stream.get("channel"),
            channel_name=stream.get("channel_name"),
            resolution=stream_stats.get("resolution") if stream_stats else None,
            resolution_height=resolution_height,
            video_codec=stream_stats.get("video_codec") if stream_stats else None,
            audio_codec=stream_stats.get("audio_codec") if stream_stats else None,
            audio_tracks=stream_stats.get("audio_channels", 1) if stream_stats else 1,
            bitrate=stream_stats.get("bitrate") if stream_stats else None,
        )


@dataclass
class EvaluationResult:
    """Result of condition evaluation."""
    matched: bool
    condition_type: str
    details: Optional[str] = None  # Human-readable explanation

    def __bool__(self):
        return self.matched


class ConditionEvaluator:
    """
    Evaluates conditions against stream contexts.

    Usage:
        evaluator = ConditionEvaluator(existing_channels, existing_groups)
        result = evaluator.evaluate(condition, stream_context)
    """

    def __init__(self, existing_channels: list[dict] = None, existing_groups: list[dict] = None):
        """
        Initialize the evaluator with existing channel/group data.

        Args:
            existing_channels: List of existing channel dicts from Dispatcharr
            existing_groups: List of existing group dicts from Dispatcharr
        """
        self.existing_channels = existing_channels or []
        self.existing_groups = existing_groups or []

        # Build lookup indices for performance
        self._channel_by_id = {c["id"]: c for c in self.existing_channels}
        self._channel_names = {c["name"].lower(): c for c in self.existing_channels}
        self._channels_by_group = {}
        for channel in self.existing_channels:
            group_id = channel.get("channel_group_id") or channel.get("channel_group", {}).get("id")
            if group_id:
                if group_id not in self._channels_by_group:
                    self._channels_by_group[group_id] = []
                self._channels_by_group[group_id].append(channel)

    def evaluate(self, condition: Condition | dict, context: StreamContext) -> EvaluationResult:
        """
        Evaluate a condition against a stream context.

        Args:
            condition: Condition object or dict to evaluate
            context: StreamContext with stream data

        Returns:
            EvaluationResult indicating if condition matched
        """
        if isinstance(condition, dict):
            condition = Condition.from_dict(condition)

        result = self._evaluate_condition(condition, context)

        # Apply negation if specified
        if condition.negate:
            result = EvaluationResult(
                matched=not result.matched,
                condition_type=f"not({result.condition_type})",
                details=f"Negated: {result.details}"
            )

        return result

    def _evaluate_condition(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Internal evaluation logic."""
        cond_type = condition.type

        try:
            cond_enum = ConditionType(cond_type)
        except ValueError:
            logger.warning(f"Unknown condition type: {cond_type}")
            return EvaluationResult(False, cond_type, f"Unknown condition type: {cond_type}")

        # Logical operators
        if cond_enum == ConditionType.AND:
            return self._evaluate_and(condition, context)
        elif cond_enum == ConditionType.OR:
            return self._evaluate_or(condition, context)
        elif cond_enum == ConditionType.NOT:
            return self._evaluate_not(condition, context)

        # Special conditions
        elif cond_enum == ConditionType.ALWAYS:
            return EvaluationResult(True, cond_type, "Always matches")
        elif cond_enum == ConditionType.NEVER:
            return EvaluationResult(False, cond_type, "Never matches")

        # Stream name conditions
        elif cond_enum == ConditionType.STREAM_NAME_MATCHES:
            return self._evaluate_regex(condition.value, context.stream_name,
                                        condition.case_sensitive, cond_type)
        elif cond_enum == ConditionType.STREAM_NAME_CONTAINS:
            return self._evaluate_contains(condition.value, context.stream_name,
                                           condition.case_sensitive, cond_type)

        # Group conditions
        elif cond_enum == ConditionType.STREAM_GROUP_CONTAINS:
            return self._evaluate_contains(condition.value, context.group_name or "",
                                           condition.case_sensitive, cond_type)
        elif cond_enum == ConditionType.STREAM_GROUP_MATCHES:
            return self._evaluate_regex(condition.value, context.group_name or "",
                                        condition.case_sensitive, cond_type)

        # TVG conditions
        elif cond_enum == ConditionType.TVG_ID_EXISTS:
            has_tvg = bool(context.tvg_id)
            expected = condition.value if condition.value is not None else True
            matched = has_tvg == expected
            return EvaluationResult(matched, cond_type,
                                    f"tvg_id {'exists' if has_tvg else 'missing'}: {context.tvg_id}")
        elif cond_enum == ConditionType.TVG_ID_MATCHES:
            return self._evaluate_regex(condition.value, context.tvg_id or "",
                                        condition.case_sensitive, cond_type)

        # Logo condition
        elif cond_enum == ConditionType.LOGO_EXISTS:
            has_logo = bool(context.logo_url)
            expected = condition.value if condition.value is not None else True
            matched = has_logo == expected
            return EvaluationResult(matched, cond_type,
                                    f"logo {'exists' if has_logo else 'missing'}")

        # Provider condition
        elif cond_enum == ConditionType.PROVIDER_IS:
            return self._evaluate_provider_is(condition.value, context.m3u_account_id, cond_type)

        # Quality conditions
        elif cond_enum == ConditionType.QUALITY_MIN:
            return self._evaluate_quality_min(condition.value, context.resolution_height, cond_type)
        elif cond_enum == ConditionType.QUALITY_MAX:
            return self._evaluate_quality_max(condition.value, context.resolution_height, cond_type)

        # Codec condition
        elif cond_enum == ConditionType.CODEC_IS:
            return self._evaluate_codec_is(condition.value, context.video_codec, cond_type)

        # Audio tracks condition
        elif cond_enum == ConditionType.HAS_AUDIO_TRACKS:
            min_tracks = int(condition.value) if condition.value else 1
            matched = context.audio_tracks >= min_tracks
            return EvaluationResult(matched, cond_type,
                                    f"audio tracks: {context.audio_tracks} >= {min_tracks}")

        # Channel conditions
        elif cond_enum == ConditionType.HAS_CHANNEL:
            has_channel = context.channel_id is not None
            expected = condition.value if condition.value is not None else True
            matched = has_channel == expected
            return EvaluationResult(matched, cond_type,
                                    f"has_channel: {has_channel} (channel_id={context.channel_id})")

        elif cond_enum == ConditionType.CHANNEL_EXISTS_WITH_NAME:
            return self._evaluate_channel_exists_name(condition.value, cond_type)

        elif cond_enum == ConditionType.CHANNEL_EXISTS_MATCHING:
            return self._evaluate_channel_exists_regex(condition.value, condition.case_sensitive, cond_type)

        elif cond_enum == ConditionType.CHANNEL_IN_GROUP:
            return self._evaluate_channel_in_group(condition.value, context.channel_id, cond_type)

        elif cond_enum == ConditionType.CHANNEL_HAS_STREAMS:
            return self._evaluate_channel_has_streams(condition.value, context.channel_id, cond_type)

        # Fallback
        logger.warning(f"Unhandled condition type: {cond_type}")
        return EvaluationResult(False, cond_type, f"Unhandled condition type")

    # =========================================================================
    # Logical Operators
    # =========================================================================

    def _evaluate_and(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Evaluate AND condition - all sub-conditions must match."""
        if not condition.conditions:
            return EvaluationResult(False, "and", "No sub-conditions")

        results = []
        for sub_cond in condition.conditions:
            result = self.evaluate(sub_cond, context)
            results.append(result)
            if not result.matched:
                # Short-circuit on first failure
                return EvaluationResult(
                    False, "and",
                    f"Failed at: {result.condition_type} - {result.details}"
                )

        return EvaluationResult(
            True, "and",
            f"All {len(results)} conditions matched"
        )

    def _evaluate_or(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Evaluate OR condition - at least one sub-condition must match."""
        if not condition.conditions:
            return EvaluationResult(False, "or", "No sub-conditions")

        for sub_cond in condition.conditions:
            result = self.evaluate(sub_cond, context)
            if result.matched:
                # Short-circuit on first success
                return EvaluationResult(
                    True, "or",
                    f"Matched: {result.condition_type} - {result.details}"
                )

        return EvaluationResult(
            False, "or",
            f"None of {len(condition.conditions)} conditions matched"
        )

    def _evaluate_not(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Evaluate NOT condition - inverts the sub-condition result."""
        if not condition.conditions or len(condition.conditions) != 1:
            return EvaluationResult(False, "not", "Requires exactly 1 sub-condition")

        result = self.evaluate(condition.conditions[0], context)
        return EvaluationResult(
            not result.matched, "not",
            f"Negated ({result.condition_type}): {result.details}"
        )

    # =========================================================================
    # String Matching
    # =========================================================================

    def _evaluate_regex(self, pattern: str, value: str, case_sensitive: bool,
                        cond_type: str) -> EvaluationResult:
        """Evaluate regex pattern against value."""
        if not pattern:
            return EvaluationResult(False, cond_type, "No pattern specified")
        if value is None:
            value = ""

        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            matched = bool(re.search(pattern, value, flags))
            return EvaluationResult(
                matched, cond_type,
                f"'{value}' {'matches' if matched else 'does not match'} /{pattern}/"
            )
        except re.error as e:
            logger.error(f"Invalid regex pattern '{pattern}': {e}")
            return EvaluationResult(False, cond_type, f"Invalid regex: {e}")

    def _evaluate_contains(self, substring: str, value: str, case_sensitive: bool,
                           cond_type: str) -> EvaluationResult:
        """Evaluate substring containment."""
        if not substring:
            return EvaluationResult(False, cond_type, "No substring specified")
        if value is None:
            value = ""

        if case_sensitive:
            matched = substring in value
        else:
            matched = substring.lower() in value.lower()

        return EvaluationResult(
            matched, cond_type,
            f"'{value}' {'contains' if matched else 'does not contain'} '{substring}'"
        )

    # =========================================================================
    # Provider Conditions
    # =========================================================================

    def _evaluate_provider_is(self, expected: int | list, actual: int | None,
                               cond_type: str) -> EvaluationResult:
        """Evaluate if stream is from expected provider(s)."""
        if actual is None:
            return EvaluationResult(False, cond_type, "No provider ID on stream")

        if isinstance(expected, list):
            matched = actual in expected
            return EvaluationResult(
                matched, cond_type,
                f"provider {actual} {'in' if matched else 'not in'} {expected}"
            )
        else:
            matched = actual == expected
            return EvaluationResult(
                matched, cond_type,
                f"provider {actual} {'==' if matched else '!='} {expected}"
            )

    # =========================================================================
    # Quality Conditions
    # =========================================================================

    def _evaluate_quality_min(self, min_height: int, actual_height: int | None,
                               cond_type: str) -> EvaluationResult:
        """Evaluate minimum quality requirement."""
        if actual_height is None:
            # No quality info - assume doesn't meet minimum
            return EvaluationResult(
                False, cond_type,
                f"No quality info available (required >= {min_height}p)"
            )

        matched = actual_height >= min_height
        return EvaluationResult(
            matched, cond_type,
            f"quality {actual_height}p {'>='}  {min_height}p: {matched}"
        )

    def _evaluate_quality_max(self, max_height: int, actual_height: int | None,
                               cond_type: str) -> EvaluationResult:
        """Evaluate maximum quality limit."""
        if actual_height is None:
            # No quality info - assume within limit
            return EvaluationResult(
                True, cond_type,
                f"No quality info available (limit <= {max_height}p)"
            )

        matched = actual_height <= max_height
        return EvaluationResult(
            matched, cond_type,
            f"quality {actual_height}p {'<='} {max_height}p: {matched}"
        )

    # =========================================================================
    # Codec Conditions
    # =========================================================================

    def _evaluate_codec_is(self, expected: str | list, actual: str | None,
                           cond_type: str) -> EvaluationResult:
        """Evaluate video codec match."""
        if actual is None:
            return EvaluationResult(False, cond_type, "No codec info available")

        actual_lower = actual.lower()

        if isinstance(expected, list):
            expected_lower = [c.lower() for c in expected]
            matched = actual_lower in expected_lower
            return EvaluationResult(
                matched, cond_type,
                f"codec '{actual}' {'in' if matched else 'not in'} {expected}"
            )
        else:
            matched = actual_lower == expected.lower()
            return EvaluationResult(
                matched, cond_type,
                f"codec '{actual}' {'==' if matched else '!='} '{expected}'"
            )

    # =========================================================================
    # Channel Conditions
    # =========================================================================

    def _evaluate_channel_exists_name(self, channel_name: str, cond_type: str) -> EvaluationResult:
        """Check if a channel with exact name exists."""
        exists = channel_name.lower() in self._channel_names
        return EvaluationResult(
            exists, cond_type,
            f"Channel '{channel_name}' {'exists' if exists else 'does not exist'}"
        )

    def _evaluate_channel_exists_regex(self, pattern: str, case_sensitive: bool,
                                        cond_type: str) -> EvaluationResult:
        """Check if any channel matches the regex pattern."""
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)

            for channel in self.existing_channels:
                if regex.search(channel.get("name", "")):
                    return EvaluationResult(
                        True, cond_type,
                        f"Channel '{channel['name']}' matches /{pattern}/"
                    )

            return EvaluationResult(
                False, cond_type,
                f"No channel matches /{pattern}/"
            )
        except re.error as e:
            return EvaluationResult(False, cond_type, f"Invalid regex: {e}")

    def _evaluate_channel_in_group(self, group_id: int, channel_id: int | None,
                                    cond_type: str) -> EvaluationResult:
        """Check if the stream's channel is in a specific group."""
        if channel_id is None:
            return EvaluationResult(False, cond_type, "Stream has no channel")

        channel = self._channel_by_id.get(channel_id)
        if not channel:
            return EvaluationResult(False, cond_type, f"Channel {channel_id} not found")

        channel_group_id = channel.get("channel_group_id") or channel.get("channel_group", {}).get("id")
        matched = channel_group_id == group_id

        return EvaluationResult(
            matched, cond_type,
            f"Channel in group {channel_group_id} {'==' if matched else '!='} {group_id}"
        )

    def _evaluate_channel_has_streams(self, min_streams: int, channel_id: int | None,
                                       cond_type: str) -> EvaluationResult:
        """Check if the stream's channel has at least N streams."""
        if channel_id is None:
            return EvaluationResult(False, cond_type, "Stream has no channel")

        channel = self._channel_by_id.get(channel_id)
        if not channel:
            return EvaluationResult(False, cond_type, f"Channel {channel_id} not found")

        # Get stream count from channel
        stream_count = len(channel.get("streams", []))
        matched = stream_count >= min_streams

        return EvaluationResult(
            matched, cond_type,
            f"Channel has {stream_count} streams {'>='}  {min_streams}: {matched}"
        )


def evaluate_conditions(conditions: list, context: StreamContext,
                        existing_channels: list = None,
                        existing_groups: list = None) -> bool:
    """
    Convenience function to evaluate a list of conditions with connector support.

    Conditions are connected by AND/OR connectors (stored on each condition).
    AND binds tighter than OR (standard precedence):
      cond1 AND cond2 OR cond3 = (cond1 AND cond2) OR cond3

    Args:
        conditions: List of condition dicts or Condition objects
        context: StreamContext to evaluate against
        existing_channels: Existing channels for channel conditions
        existing_groups: Existing groups for group conditions

    Returns:
        True if conditions match according to connector logic
    """
    evaluator = ConditionEvaluator(existing_channels, existing_groups)

    if not conditions:
        return True

    # Group conditions by OR breaks (AND binds tighter than OR)
    or_groups = [[]]
    for cond in conditions:
        connector = cond.get("connector", "and") if isinstance(cond, dict) else getattr(cond, 'connector', 'and')
        if connector == "or" and or_groups[-1]:
            or_groups.append([])
        or_groups[-1].append(cond)

    # Evaluate: any OR-group fully matching = overall match
    for group in or_groups:
        group_matched = True
        for condition in group:
            result = evaluator.evaluate(condition, context)
            if not result.matched:
                group_matched = False
                break
        if group_matched:
            return True

    return False
