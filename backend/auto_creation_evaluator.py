"""
Auto-Creation Condition Evaluator Service

Evaluates conditions against streams to determine if rules should be applied.
Supports compound conditions with AND/OR/NOT operators and checks against
existing channels in the system.
"""
import re
import logging
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    channel_group_id: Optional[int] = None  # Dispatcharr channel_group numeric ID
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
            except (ValueError, IndexError) as e:
                logger.debug("[AUTO-CREATE-EVAL] Suppressed resolution parse error: %s", e)

        return cls(
            stream_id=stream.get("id"),
            stream_name=stream.get("name", ""),
            stream_url=stream.get("url"),
            group_name=stream.get("group_title") or stream.get("channel_group_name") or stream.get("m3u_group_name"),
            channel_group_id=stream.get("channel_group"),
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

    def __init__(self, existing_channels: list[dict] = None, existing_groups: list[dict] = None,
                 normalization_engine=None):
        """
        Initialize the evaluator with existing channel/group data.

        Args:
            existing_channels: List of existing channel dicts from Dispatcharr
            existing_groups: List of existing group dicts from Dispatcharr
            normalization_engine: Optional NormalizationEngine for normalized_name_in_group conditions
        """
        self.existing_channels = existing_channels or []
        self.existing_groups = existing_groups or []
        self._normalization_engine = normalization_engine

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

        # Build per-group channel name sets for fast normalized name lookups
        # Includes raw names, names with channel-number prefixes stripped (e.g. "106 | Name" -> "Name"),
        # and normalization-engine-processed names for maximum match coverage
        self._channel_names_by_group: dict[int, set[str]] = {}
        channel_number_prefix = re.compile(r'^\d+\s*\|\s*')
        for gid, channels in self._channels_by_group.items():
            names: set[str] = set()
            for c in channels:
                raw = c["name"]
                names.add(raw.lower())
                # Strip "123 | " channel number prefixes
                stripped = channel_number_prefix.sub('', raw)
                if stripped != raw:
                    names.add(stripped.lower())
                # Apply normalization engine to the stripped name
                if self._normalization_engine:
                    try:
                        result = self._normalization_engine.normalize(stripped)
                        normalized = result.normalized
                        if normalized:
                            names.add(normalized.lower())
                    except Exception as e:
                        logger.warning("[AUTO-CREATE-EVAL] Normalization failed for channel '%s': %s", stripped, e)
            self._channel_names_by_group[gid] = names

        # Build global set of all channel names across all groups for normalized_name_exists
        self._all_channel_names: set[str] = set()
        for names_set in self._channel_names_by_group.values():
            self._all_channel_names.update(names_set)

    def _expand_date_placeholders(self, text: str, allow_ranges: bool = True) -> str:
        """
        Expand {date...} or {today...} placeholders in text.

        Args:
            text: Text with potential placeholders
            allow_ranges: If True, expansions like {date+3} return a regex group (d1|d2|d3).
                         If False, only single-date placeholders are expanded.

        Supported formats:
        - {date} or {today} -> YYYY-MM-DD (today)
        - {date+N} -> today + N days (range: today to today+N)
        - {date-N} -> today - N days (range: today to today-N)
        - {date+Nd} -> today + N days (range: today to today+N)
        - {date+Nw} -> today + N weeks (range: today to today+N*7)
        - {date:FORMAT} -> today formatted (e.g., {date:%d %b})
        """
        if not text or not isinstance(text, str) or "{" not in text:
            return text

        pattern = r"\{(?:date|today)([+-]\d+[dw]?)?(:[^}]+)?\}"

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

            max_days = 90
            # Cap the range at 90 days to prevent huge regex generation
            if days_to_add > max_days:
                days_to_add = max_days
            elif days_to_add < -max_days:
                days_to_add = -max_days

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
        return re.sub(pattern, replace_match, text)

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

        logger.debug(
            "[AUTO-CREATE-EVAL] stream=%r type=%s matched=%s details=%s",
            context.stream_name, result.condition_type, result.matched, result.details
        )
        return result

    def _evaluate_condition(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Internal evaluation logic."""
        cond_type = condition.type

        try:
            cond_enum = ConditionType(cond_type)
        except ValueError:
            logger.warning("[AUTO-CREATE-EVAL] Unknown condition type: %s", cond_type)
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

        elif cond_enum == ConditionType.NORMALIZED_NAME_IN_GROUP:
            return self._evaluate_normalized_name_in_group(condition.value, context, cond_type)

        elif cond_enum == ConditionType.NORMALIZED_NAME_NOT_IN_GROUP:
            return self._evaluate_normalized_name_not_in_group(condition.value, context, cond_type)

        elif cond_enum == ConditionType.NORMALIZED_NAME_EXISTS:
            return self._evaluate_normalized_name_exists(context, cond_type)

        elif cond_enum == ConditionType.NORMALIZED_NAME_NOT_EXISTS:
            return self._evaluate_normalized_name_not_exists(context, cond_type)

        # Fallback
        logger.warning("[AUTO-CREATE-EVAL] Unhandled condition type: %s", cond_type)
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
                logger.debug(
                    "[AUTO-CREATE-EVAL] AND stream=%r short-circuit fail at "
                    "sub-condition %s: %s",
                    context.stream_name, result.condition_type, result.details
                )
                return EvaluationResult(
                    False, "and",
                    f"Failed at: {result.condition_type} - {result.details}"
                )

        logger.debug("[AUTO-CREATE-EVAL] AND stream=%r all %s sub-conditions matched", context.stream_name, len(results))
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
                logger.debug(
                    "[AUTO-CREATE-EVAL] OR stream=%r short-circuit match at "
                    "sub-condition %s: %s",
                    context.stream_name, result.condition_type, result.details
                )
                return EvaluationResult(
                    True, "or",
                    f"Matched: {result.condition_type} - {result.details}"
                )

        logger.debug(
            "[AUTO-CREATE-EVAL] OR stream=%r none of %s sub-conditions matched",
            context.stream_name, len(condition.conditions)
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

        pattern = self._expand_date_placeholders(pattern)
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
            logger.error("[AUTO-CREATE-EVAL] Invalid regex pattern '%s': %s", pattern, e)
            return EvaluationResult(False, cond_type, f"Invalid regex: {e}")

    def _evaluate_contains(self, substring: str, value: str, case_sensitive: bool,
                           cond_type: str) -> EvaluationResult:
        """Evaluate substring containment."""

        substring = self._expand_date_placeholders(substring, allow_ranges=False)

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

        channel_name = self._expand_date_placeholders(channel_name, allow_ranges=False)

        exists = channel_name.lower() in self._channel_names
        return EvaluationResult(
            exists, cond_type,
            f"Channel '{channel_name}' {'exists' if exists else 'does not exist'}"
        )

    def _evaluate_channel_exists_regex(self, pattern: str, case_sensitive: bool,
                                        cond_type: str) -> EvaluationResult:
        """Check if any channel matches the regex pattern."""

        pattern = self._expand_date_placeholders(pattern)

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


    def _evaluate_normalized_name_in_group(self, group_id: int, context: StreamContext,
                                              cond_type: str) -> EvaluationResult:
        """Check if the stream's normalized name matches any channel in the specified group."""
        if not group_id:
            return EvaluationResult(False, cond_type, "No group ID specified")

        # Get channel names in the target group
        group_names = self._channel_names_by_group.get(group_id)
        if not group_names:
            return EvaluationResult(False, cond_type, f"Group {group_id} has no channels")

        # Normalize the stream name
        stream_name = context.stream_name
        if self._normalization_engine:
            try:
                result = self._normalization_engine.normalize(stream_name)
                normalized = result.normalized
            except Exception as e:
                logger.warning("[AUTO-CREATE-EVAL] Normalization failed for '%s': %s", stream_name, e)
                normalized = stream_name
        else:
            normalized = stream_name

        # Check if normalized name matches any channel in the group (case-insensitive)
        matched = normalized.lower() in group_names

        return EvaluationResult(
            matched, cond_type,
            f"'{stream_name}' → '{normalized}' {'matches' if matched else 'no match in'} group {group_id} "
            f"({len(group_names)} channels)"
        )

    def _evaluate_normalized_name_not_in_group(self, group_id: int, context: StreamContext,
                                                  cond_type: str) -> EvaluationResult:
        """Check if the stream's normalized name does NOT match any channel in the specified group."""
        # Reuse the in-group evaluator and invert the result
        result = self._evaluate_normalized_name_in_group(group_id, context, "normalized_name_in_group")
        return EvaluationResult(
            not result.matched, cond_type,
            result.details.replace("matches", "NOT in").replace("no match in", "confirmed not in")
            if result.details else f"Inverted group check for group {group_id}"
        )

    def _normalize_stream_name(self, context: StreamContext) -> str:
        """Normalize the stream name using the normalization engine if available."""
        stream_name = context.stream_name
        if self._normalization_engine:
            try:
                result = self._normalization_engine.normalize(stream_name)
                return result.normalized
            except Exception as e:
                logger.warning("[AUTO-CREATE-EVAL] Normalization failed for '%s': %s", stream_name, e)
                return stream_name
        return stream_name

    def _evaluate_normalized_name_exists(self, context: StreamContext,
                                            cond_type: str) -> EvaluationResult:
        """Check if the stream's normalized name matches any channel in ANY group."""
        if not self._all_channel_names:
            return EvaluationResult(False, cond_type, "No channels exist in any group")

        normalized = self._normalize_stream_name(context)
        matched = normalized.lower() in self._all_channel_names

        return EvaluationResult(
            matched, cond_type,
            f"'{context.stream_name}' → '{normalized}' {'found' if matched else 'not found'} "
            f"across all groups ({len(self._all_channel_names)} names)"
        )

    def _evaluate_normalized_name_not_exists(self, context: StreamContext,
                                                cond_type: str) -> EvaluationResult:
        """Check if the stream's normalized name does NOT match any channel in any group."""
        result = self._evaluate_normalized_name_exists(context, "normalized_name_exists")
        return EvaluationResult(
            not result.matched, cond_type,
            result.details.replace("found", "confirmed absent").replace("not found", "confirmed absent")
            if result.details else "Inverted global check"
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
        logger.debug("[AUTO-CREATE-EVAL] stream=%r no conditions -> True", context.stream_name)
        return True

    # Group conditions by OR breaks (AND binds tighter than OR)
    or_groups = [[]]
    for cond in conditions:
        connector = cond.get("connector", "and") if isinstance(cond, dict) else getattr(cond, 'connector', 'and')
        if connector == "or" and or_groups[-1]:
            or_groups.append([])
        or_groups[-1].append(cond)

    logger.debug(
        "[AUTO-CREATE-EVAL] stream=%r %s conditions in %s OR-group(s)",
        context.stream_name, len(conditions), len(or_groups)
    )

    # Evaluate: any OR-group fully matching = overall match
    for group_idx, group in enumerate(or_groups):
        group_matched = True
        for condition in group:
            result = evaluator.evaluate(condition, context)
            if not result.matched:
                group_matched = False
                break
        if group_matched:
            logger.debug(
                "[AUTO-CREATE-EVAL] stream=%r OR-group %s matched -> overall True",
                context.stream_name, group_idx
            )
            return True

    logger.debug(
        "[AUTO-CREATE-EVAL] stream=%r no OR-group matched -> overall False",
        context.stream_name
    )
    return False
