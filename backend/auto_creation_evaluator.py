"""
Auto-Creation Condition Evaluator Service

Evaluates conditions against streams to determine if rules should be applied.
Supports compound conditions with AND/OR/NOT operators and checks against
existing channels in the system.
"""
import re
import logging
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

    # EPG info (current program)
    epg_title: Optional[str] = None
    epg_description: Optional[str] = None
    
    # Raw program list for the day (to find specific matches for templates)
    epg_programs: list[dict] = field(default_factory=list)
    
    # The specific program that triggered a condition match
    epg_match: Optional[dict] = None
    matched_by_epg: bool = False

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

    # Provider ordering
    m3u_position: int = 0                   # Dispatcharr stream ID (preserves M3U import order)
    stream_chno: Optional[float] = None     # tvg-chno from M3U source

    # Normalized name (after normalization rules applied)
    normalized_name: Optional[str] = None

    @classmethod
    def from_dispatcharr_stream(cls, stream: dict, m3u_account_id: int = None,
                                 m3u_account_name: str = None,
                                 stream_stats: dict = None,
                                 account_groups: dict = None) -> "StreamContext":
        """
        Create StreamContext from Dispatcharr stream API response.
        """
        # Resolve group name
        group_name = stream.get("group_title") or stream.get("channel_group_name") or stream.get("m3u_group_name")
        group_id = stream.get("channel_group")

        # account_groups is {channel_group_id: settings} from get_all_m3u_group_settings()
        if group_id and account_groups and group_id in account_groups:
            group_setting = account_groups[group_id]
            if group_setting.get("m3u_account_id") == m3u_account_id:
                if not group_name:
                    group_name = group_setting.get("name") or group_setting.get("channel_group_name")

        # Parse resolution from stream_stats
        resolution_height = None
        if stream_stats and stream_stats.get("resolution"):
            try:
                parts = stream_stats["resolution"].split("x")
                if len(parts) == 2:
                    resolution_height = int(parts[1])
            except (ValueError, IndexError):
                pass

        return cls(
            stream_id=stream.get("id"),
            stream_name=stream.get("name", ""),
            stream_url=stream.get("url"),
            group_name=group_name,
            channel_group_id=group_id,
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
            m3u_position=stream.get("id", 0),
            stream_chno=stream.get("stream_chno"),
        )


@dataclass
class EvaluationResult:
    """Result of condition evaluation."""
    matched: bool
    condition_type: str
    details: Optional[str] = None  # Human-readable explanation
    matched_data: Optional[dict] = None  # Captured data (e.g. the specific program)

    def __bool__(self):
        return self.matched


class ConditionEvaluator:
    """
    Evaluates auto-creation conditions against stream contexts.
    """

    def __init__(self, existing_channels: list[dict] = None, existing_groups: list[dict] = None,
                 normalization_engine=None):
        """
        Initialize the evaluator with existing channel/group data.
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

        self._channel_names_by_group: dict[int, set[str]] = {}
        channel_number_prefix = re.compile(r'^\d+\s*\|\s*')
        for gid, channels in self._channels_by_group.items():
            names: set[str] = set()
            for c in channels:
                raw = c["name"]
                names.add(raw.lower())
                stripped = channel_number_prefix.sub('', raw)
                if stripped != raw:
                    names.add(stripped.lower())
                if self._normalization_engine:
                    try:
                        result = self._normalization_engine.normalize(stripped)
                        if result and result.normalized:
                            names.add(result.normalized.lower())
                    except Exception:
                        pass
            self._channel_names_by_group[gid] = names

        self._all_channel_names: set[str] = set()
        for names_set in self._channel_names_by_group.values():
            self._all_channel_names.update(names_set)

    def _expand_date_placeholders(self, text: str, allow_ranges: bool = True) -> str:
        """Expand {date...} or {today...} placeholders in text."""
        if not text or not isinstance(text, str) or "{" not in text:
            return text

        pattern = r"\{(?:date|today)([+-]\d+[dw]?)?(:[^}]+)?\}"

        def replace_match(match):
            offset_str = match.group(1)
            format_str = match.group(2)
            base_date = datetime.now()
            unit = "d"
            val = 0

            if offset_str:
                if not allow_ranges:
                    return match.group(0)
                val_str = offset_str
                if offset_str[-1].lower() in ("d", "w"):
                    unit = offset_str[-1].lower()
                    val_str = offset_str[:-1]
                try:
                    val = int(val_str)
                except ValueError:
                    return match.group(0)

            fmt = "%Y-%m-%d"
            if format_str:
                fmt = format_str[1:]

            if val == 0:
                try:
                    return base_date.strftime(fmt)
                except ValueError:
                    return match.group(0)

            days_to_add = val * 7 if unit == "w" else val
            max_days = 90
            days_to_add = max(min(days_to_add, max_days), -max_days)

            dates = []
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
        """Evaluate a condition against a stream context."""
        if isinstance(condition, dict):
            condition = Condition.from_dict(condition)

        result = self._evaluate_condition(condition, context)

        # Apply negation if specified
        if condition.negate:
            result = EvaluationResult(
                matched=not result.matched,
                condition_type=f"not({result.condition_type})",
                details=f"Negated: {result.details}",
                matched_data=result.matched_data
            )

        # Capture matched data if available (after possible negation)
        if result.matched and result.matched_data:
            if "program" in result.matched_data:
                context.epg_match = result.matched_data["program"]
            
            is_epg_type = result.condition_type in (
                ConditionType.EPG_TITLE_CONTAINS, ConditionType.EPG_TITLE_MATCHES,
                ConditionType.EPG_DESC_CONTAINS, ConditionType.EPG_DESC_MATCHES,
                ConditionType.EPG_ANY_CONTAINS, ConditionType.EPG_ANY_MATCHES,
                "and", "or"
            )
            
            if is_epg_type or (result.condition_type in (
                ConditionType.ANY_FIELD_CONTAINS, ConditionType.ANY_FIELD_MATCHES
            ) and "program" in result.matched_data):
                context.matched_by_epg = True

        logger.debug(
            "[AUTO-CREATE-EVAL] stream=%r type=%s matched=%s details=%s",
            context.stream_name, result.condition_type, result.matched, result.details
        )
        return result

    def _evaluate_epg_field(self, field_name: str, pattern: str, is_regex: bool, 
                            case_sensitive: bool, context: StreamContext, 
                            cond_type: str) -> EvaluationResult:
        """Evaluate a field against daily programs and capture the matching program."""
        if not context.epg_programs:
            return EvaluationResult(False, cond_type, "No EPG data for today")

        pattern = self._expand_date_placeholders(pattern, allow_ranges=not is_regex)
        
        for prog in context.epg_programs:
            value = prog.get(field_name) or ""
            matched = False
            matched_segment = pattern
            
            if is_regex:
                try:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    match_obj = re.search(pattern, value, flags)
                    if match_obj:
                        matched = True
                        matched_segment = match_obj.group(0)
                except re.error as e:
                    return EvaluationResult(False, cond_type, f"Invalid regex: {e}")
            else:
                if case_sensitive:
                    matched = pattern in value
                else:
                    matched = pattern.lower() in value.lower()
            
            if matched:
                return EvaluationResult(
                    True, cond_type,
                    f"Program '{prog.get('title')}' matched '{matched_segment}'",
                    matched_data={"program": prog}
                )
        
        return EvaluationResult(False, cond_type, f"No program scheduled for today matched '{pattern}'")

    def _evaluate_condition(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Internal evaluation logic."""
        cond_type = condition.type.lower()

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
            actual_group = (context.group_name or "").strip()
            res = self._evaluate_contains(condition.value, actual_group,
                                           condition.case_sensitive, cond_type)
            return res
        elif cond_enum == ConditionType.STREAM_GROUP_MATCHES:
            actual_group = (context.group_name or "").strip()
            res = self._evaluate_regex(condition.value, actual_group,
                                        condition.case_sensitive, cond_type)
            return res

        # TVG conditions
        elif cond_enum == ConditionType.TVG_ID_EXISTS:
            has_tvg = bool(context.tvg_id)
            matched = has_tvg == (condition.value if condition.value is not None else True)
            return EvaluationResult(matched, cond_type, f"tvg_id {'exists' if has_tvg else 'missing'}")
        elif cond_enum == ConditionType.TVG_ID_MATCHES:
            return self._evaluate_regex(condition.value, context.tvg_id or "",
                                        condition.case_sensitive, cond_type)

        # EPG conditions
        elif cond_enum == ConditionType.EPG_TITLE_CONTAINS:
            return self._evaluate_epg_field("title", condition.value, False, condition.case_sensitive, context, cond_type)
        elif cond_enum == ConditionType.EPG_TITLE_MATCHES:
            return self._evaluate_epg_field("title", condition.value, True, condition.case_sensitive, context, cond_type)
        elif cond_enum == ConditionType.EPG_DESC_CONTAINS:
            return self._evaluate_epg_field("description", condition.value, False, condition.case_sensitive, context, cond_type)
        elif cond_enum == ConditionType.EPG_DESC_MATCHES:
            return self._evaluate_epg_field("description", condition.value, True, condition.case_sensitive, context, cond_type)

        # multi-field search
        elif cond_enum == ConditionType.EPG_ANY_CONTAINS:
            title_res = self._evaluate_epg_field("title", condition.value, False, condition.case_sensitive, context, cond_type)
            if title_res.matched: return title_res
            return self._evaluate_epg_field("description", condition.value, False, condition.case_sensitive, context, cond_type)
        elif cond_enum == ConditionType.EPG_ANY_MATCHES:
            title_res = self._evaluate_epg_field("title", condition.value, True, condition.case_sensitive, context, cond_type)
            if title_res.matched: return title_res
            return self._evaluate_epg_field("description", condition.value, True, condition.case_sensitive, context, cond_type)

        # Fallback to standard ANY_FIELD handling
        elif cond_enum == ConditionType.ANY_FIELD_CONTAINS:
            name_res = self._evaluate_contains(condition.value, context.stream_name, condition.case_sensitive, cond_type)
            if name_res.matched: return name_res
            return self.evaluate({"type": "epg_any_contains", "value": condition.value, "case_sensitive": condition.case_sensitive}, context)
        elif cond_enum == ConditionType.ANY_FIELD_MATCHES:
            name_res = self._evaluate_regex(condition.value, context.stream_name, condition.case_sensitive, cond_type)
            if name_res.matched: return name_res
            return self.evaluate({"type": "epg_any_matches", "value": condition.value, "case_sensitive": condition.case_sensitive}, context)

        # Provider, Quality, Codec, Logo
        elif cond_enum == ConditionType.LOGO_EXISTS:
            has_logo = bool(context.logo_url)
            return EvaluationResult(has_logo == (condition.value if condition.value is not None else True), cond_type, f"logo {'exists' if has_logo else 'missing'}")
        elif cond_enum == ConditionType.PROVIDER_IS:
            return self._evaluate_provider_is(condition.value, context.m3u_account_id, cond_type)
        elif cond_enum == ConditionType.QUALITY_MIN:
            return self._evaluate_quality_min(condition.value, context.resolution_height, cond_type)
        elif cond_enum == ConditionType.QUALITY_MAX:
            return self._evaluate_quality_max(condition.value, context.resolution_height, cond_type)
        elif cond_enum == ConditionType.CODEC_IS:
            return self._evaluate_codec_is(condition.value, context.video_codec, cond_type)
        elif cond_enum == ConditionType.HAS_AUDIO_TRACKS:
            min_tracks = int(condition.value) if condition.value else 1
            return EvaluationResult(context.audio_tracks >= min_tracks, cond_type, f"audio tracks: {context.audio_tracks} >= {min_tracks}")

        # Channel conditions
        elif cond_enum == ConditionType.HAS_CHANNEL:
            has_channel = context.channel_id is not None
            return EvaluationResult(has_channel == (condition.value if condition.value is not None else True), cond_type, f"has_channel: {has_channel}")
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

        return EvaluationResult(False, cond_type, "Unhandled type")

    def _evaluate_and(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Evaluate AND compound condition."""
        if not condition.conditions:
            return EvaluationResult(True, "and", "Empty AND matches")
        all_details = []
        final_matched_data = {}
        for i, sub in enumerate(condition.conditions):
            res = self.evaluate(sub, context)
            all_details.append(f"[{i}] {res.condition_type}: {res.details}")
            if not res.matched:
                return EvaluationResult(False, "and", " AND ".join(all_details))
            if res.matched_data:
                final_matched_data.update(res.matched_data)
        return EvaluationResult(True, "and", " AND ".join(all_details), matched_data=final_matched_data)

    def _evaluate_or(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Evaluate OR compound condition."""
        if not condition.conditions:
            return EvaluationResult(True, "or", "Empty OR matches")
        all_details = []
        for i, sub in enumerate(condition.conditions):
            res = self.evaluate(sub, context)
            all_details.append(f"[{i}] {res.condition_type}: {res.details}")
            if res.matched:
                return EvaluationResult(True, "or", " OR ".join(all_details), matched_data=res.matched_data)
        return EvaluationResult(False, "or", " OR ".join(all_details))

    def _evaluate_not(self, condition: Condition, context: StreamContext) -> EvaluationResult:
        """Evaluate NOT compound condition."""
        if not condition.conditions or len(condition.conditions) != 1:
            return EvaluationResult(False, "not", "NOT requires exactly 1 sub-condition")
        res = self.evaluate(condition.conditions[0], context)
        return EvaluationResult(not res.matched, "not", f"NOT ({res.details})")

    # String Matching helpers
    def _evaluate_regex(self, pattern: str, value: str, case_sensitive: bool, cond_type: str) -> EvaluationResult:
        pattern = self._expand_date_placeholders(pattern)
        if not pattern: return EvaluationResult(False, cond_type, "No pattern")
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            match_obj = re.search(pattern, value or "", flags)
            matched = bool(match_obj)
            return EvaluationResult(matched, cond_type, f"'{value}' {'matches' if matched else 'no match'} /{pattern}/")
        except re.error as e:
            return EvaluationResult(False, cond_type, f"Invalid regex: {e}")

    def _evaluate_contains(self, substring: str, value: str, case_sensitive: bool, cond_type: str) -> EvaluationResult:
        substring = self._expand_date_placeholders(substring, allow_ranges=False)
        if not substring: return EvaluationResult(False, cond_type, "No substring")
        matched = (substring in (value or "")) if case_sensitive else (substring.lower() in (value or "").lower())
        return EvaluationResult(matched, cond_type, f"'{value}' {'contains' if matched else 'no match'} '{substring}'")

    # Provider, Quality, Codec, etc. helpers
    def _evaluate_provider_is(self, expected, actual, cond_type):
        if actual is None: return EvaluationResult(False, cond_type, "No provider")
        matched = (actual in expected) if isinstance(expected, list) else (actual == expected)
        return EvaluationResult(matched, cond_type, f"Provider {actual} check")

    def _evaluate_quality_min(self, min_h, actual_h, cond_type):
        if actual_h is None: return EvaluationResult(False, cond_type, "No quality")
        return EvaluationResult(actual_h >= min_h, cond_type, f"quality {actual_h}p >= {min_h}p")

    def _evaluate_quality_max(self, max_h, actual_h, cond_type):
        if actual_h is None: return EvaluationResult(True, cond_type, "No quality")
        return EvaluationResult(actual_h <= max_h, cond_type, f"quality {actual_h}p <= {max_h}p")

    def _evaluate_codec_is(self, expected, actual, cond_type):
        if actual is None: return EvaluationResult(False, cond_type, "No codec")
        act_l = actual.lower()
        matched = (act_l in [c.lower() for c in expected]) if isinstance(expected, list) else (act_l == expected.lower())
        return EvaluationResult(matched, cond_type, f"codec {actual} check")

    # Channel conditions
    def _evaluate_channel_exists_name(self, name, cond_type):
        name = self._expand_date_placeholders(name, allow_ranges=False)
        exists = name.lower() in self._channel_names
        return EvaluationResult(exists, cond_type, f"Channel '{name}' exists: {exists}")

    def _evaluate_channel_exists_regex(self, pattern, case_sensitive, cond_type):
        pattern = self._expand_date_placeholders(pattern)
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
            for ch in self.existing_channels:
                if regex.search(ch.get("name", "")): return EvaluationResult(True, cond_type, f"Match: {ch['name']}")
            return EvaluationResult(False, cond_type, "No regex match")
        except re.error as e: return EvaluationResult(False, cond_type, f"Regex err: {e}")

    def _evaluate_channel_in_group(self, group_id, channel_id, cond_type):
        if channel_id is None: return EvaluationResult(False, cond_type, "No channel")
        ch = self._channel_by_id.get(channel_id)
        if not ch: return EvaluationResult(False, cond_type, "Not found")
        cg_id = ch.get("channel_group_id") or ch.get("channel_group", {}).get("id")
        return EvaluationResult(cg_id == group_id, cond_type, f"Group {cg_id} check")

    def _evaluate_channel_has_streams(self, min_s, channel_id, cond_type):
        if channel_id is None: return EvaluationResult(False, cond_type, "No channel")
        ch = self._channel_by_id.get(channel_id)
        if not ch: return EvaluationResult(False, cond_type, "Not found")
        count = len(ch.get("streams", []))
        return EvaluationResult(count >= min_s, cond_type, f"Streams: {count} >= {min_s}")

    def _evaluate_normalized_name_in_group(self, group_id, context, cond_type):
        if not group_id: return EvaluationResult(False, cond_type, "No group")
        group_names = self._channel_names_by_group.get(group_id)
        if not group_names: return EvaluationResult(False, cond_type, "Empty group")
        normalized = self._normalize_stream_name(context)
        matched = normalized.lower() in group_names
        return EvaluationResult(matched, cond_type, f"Normalized '{normalized}' in group {group_id}: {matched}")

    def _evaluate_normalized_name_not_in_group(self, group_id, context, cond_type):
        res = self._evaluate_normalized_name_in_group(group_id, context, "normalized_name_in_group")
        return EvaluationResult(not res.matched, cond_type, f"Inverted: {res.details}")

    def _normalize_stream_name(self, context: StreamContext) -> str:
        if self._normalization_engine:
            try: return self._normalization_engine.normalize(context.stream_name).normalized
            except Exception: pass
        return context.stream_name

    def _evaluate_normalized_name_exists(self, context, cond_type):
        if not self._all_channel_names: return EvaluationResult(False, cond_type, "No channels")
        normalized = self._normalize_stream_name(context)
        matched = normalized.lower() in self._all_channel_names
        return EvaluationResult(matched, cond_type, f"Normalized '{normalized}' exists: {matched}")

    def _evaluate_normalized_name_not_exists(self, context, cond_type):
        res = self._evaluate_normalized_name_exists(context, "normalized_name_exists")
        return EvaluationResult(not res.matched, cond_type, f"Inverted: {res.details}")


def evaluate_conditions(conditions: list, context: StreamContext,
                        existing_channels: list = None,
                        existing_groups: list = None) -> bool:
    """Convenience function to evaluate a list of conditions."""
    evaluator = ConditionEvaluator(existing_channels, existing_groups)
    if not conditions: return True
    or_groups = []
    current_group = []
    for cond in conditions:
        connector = cond.get("connector", "and") if isinstance(cond, dict) else getattr(cond, 'connector', 'and')
        if connector.lower() == "or" and current_group:
            or_groups.append(current_group)
            current_group = []
        current_group.append(cond)
    if current_group: or_groups.append(current_group)
    for group in or_groups:
        group_matched = True
        for condition in group:
            if not evaluator.evaluate(condition, context).matched:
                group_matched = False
                break
        if group_matched: return True
    return False
