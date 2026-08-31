"""Shared priority and additive-points evaluation for Smart Sort."""

from dataclasses import dataclass
import math
import operator
from numbers import Real
from typing import Callable, Iterable


CODEC_RANK = {
    "av1": 5,
    "hevc": 4,
    "h265": 4,
    "vp9": 3,
    "h264": 2,
    "avc": 2,
    "vp8": 1,
    "mpeg2video": 0,
    "mpeg2": 0,
}

_PROBE_CRITERIA = frozenset(
    {"resolution", "bitrate", "framerate", "video_codec", "audio_channels"}
)
_BOOLEAN_CRITERIA = {
    "custom_streams": "is_custom",
    "catchup": "is_catchup",
    "failed": "failed",
    "black_screen": "black_screen",
    "low_fps": "low_fps",
}
_STREAM_METADATA_CRITERIA = frozenset(
    {"m3u_priority", "custom_streams", "catchup"}
)
_COMPARATORS: dict[str, Callable[[Real, Real], bool]] = {
    "==": operator.eq,
    "=": operator.eq,
    "eq": operator.eq,
    "!=": operator.ne,
    "ne": operator.ne,
    ">": operator.gt,
    "gt": operator.gt,
    ">=": operator.ge,
    "gte": operator.ge,
    "<": operator.lt,
    "lt": operator.lt,
    "<=": operator.le,
    "lte": operator.le,
}


@dataclass(frozen=True)
class StreamFacts:
    """Source-independent facts consumed by both Smart Sort modes."""

    stream_id: int
    probe_succeeded: bool = False
    probe_stats_available: bool = True
    resolution_height: int | float | None = None
    video_bitrate: int | float | None = None
    bitrate: int | float | None = None
    framerate: int | float | None = None
    video_codec: str | None = None
    m3u_priority: int | float | None = None
    audio_channels: int | float | None = None
    is_custom: bool | None = None
    is_catchup: bool | None = None
    failed: bool | None = None
    black_screen: bool | None = None
    low_fps: bool | None = None


@dataclass(frozen=True)
class PointRule:
    """One validated points-mode comparison supplied by a caller."""

    criterion: str
    operator: str
    value: object
    points: int


def get_codec_rank(codec_name: str | None) -> int:
    """Return the legacy codec rank, including aliases and unknown-as-zero."""
    rank = _known_codec_rank(codec_name)
    return rank if rank is not None else 0


def _known_codec_rank(codec_name: object) -> int | None:
    if not isinstance(codec_name, str):
        return None
    return CODEC_RANK.get(codec_name.lower())


def _number(value: object) -> Real | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    if isinstance(value, int):
        return value
    if not math.isfinite(value):
        return None
    return value


def _bitrate(facts: StreamFacts) -> Real | None:
    video_bitrate = _number(facts.video_bitrate)
    if video_bitrate:
        return video_bitrate
    return _number(facts.bitrate)


def _priority_value(
    facts: StreamFacts,
    criterion: str,
    *,
    include_probe_facts: bool,
) -> Real:
    if criterion in _PROBE_CRITERIA and not include_probe_facts:
        return 0
    if criterion == "resolution":
        return _number(facts.resolution_height) or 0
    if criterion == "bitrate":
        return _bitrate(facts) or 0
    if criterion == "framerate":
        return _number(facts.framerate) or 0
    if criterion == "video_codec":
        return get_codec_rank(facts.video_codec)
    if criterion == "m3u_priority":
        return _number(facts.m3u_priority) or 0
    if criterion == "audio_channels":
        return _number(facts.audio_channels) or 0
    if criterion == "custom_streams":
        return 1 if facts.is_custom is True else 0
    if criterion == "catchup":
        return 1 if facts.is_catchup is True else 0
    return 0


def _health_bucket(
    facts: StreamFacts,
    failed_rank: dict[str, int],
    *,
    deprioritize_failed: bool,
    deprioritize_black_screen: bool,
    deprioritize_low_fps: bool,
) -> int | None:
    if not deprioritize_failed:
        return None
    if facts.failed is True or not facts.probe_stats_available:
        return failed_rank.get("failed", 0)
    if deprioritize_black_screen and facts.black_screen is True:
        return failed_rank.get("black_screen", 1)
    if deprioritize_low_fps and facts.low_fps is True:
        return failed_rank.get("low_fps", 2)
    return None


def _priority_sort_key(
    facts: StreamFacts,
    criteria: tuple[str, ...],
    failed_rank: dict[str, int],
    *,
    deprioritize_failed: bool,
    deprioritize_black_screen: bool,
    deprioritize_low_fps: bool,
) -> tuple:
    """Build the legacy lexicographic key with an ascending-ID final tie."""
    bucket = _health_bucket(
        facts,
        failed_rank,
        deprioritize_failed=deprioritize_failed,
        deprioritize_black_screen=deprioritize_black_screen,
        deprioritize_low_fps=deprioritize_low_fps,
    )
    include_probe_facts = facts.probe_succeeded or bucket is not None
    criterion_key = tuple(
        -_priority_value(
            facts,
            criterion,
            include_probe_facts=include_probe_facts,
        )
        for criterion in criteria
    )
    health_key = (0, 0) if bucket is None else (1, bucket)
    return health_key + criterion_key + (facts.stream_id,)


def sort_streams_by_priority(
    streams: Iterable[StreamFacts],
    criteria: Iterable[str],
    *,
    deprioritize_failed: bool = True,
    deprioritize_black_screen: bool = True,
    deprioritize_low_fps: bool = True,
    failed_stream_sort_order: Iterable[str] | None = None,
) -> list[int]:
    """Sort normalized facts using current priority-mode semantics."""
    criteria = tuple(criteria)
    fail_order = (
        list(failed_stream_sort_order)
        if failed_stream_sort_order is not None
        else ["failed", "black_screen", "low_fps"]
    )
    failed_rank = {category: index for index, category in enumerate(fail_order)}
    return [
        facts.stream_id
        for facts in sorted(
            streams,
            key=lambda facts: _priority_sort_key(
                facts,
                criteria,
                failed_rank,
                deprioritize_failed=deprioritize_failed,
                deprioritize_black_screen=deprioritize_black_screen,
                deprioritize_low_fps=deprioritize_low_fps,
            ),
        )
    ]


def _points_value(facts: StreamFacts, criterion: str) -> Real | bool | None:
    if criterion == "resolution":
        return _number(facts.resolution_height)
    if criterion == "bitrate":
        return _bitrate(facts)
    if criterion == "framerate":
        return _number(facts.framerate)
    if criterion == "video_codec":
        return _known_codec_rank(facts.video_codec)
    if criterion == "m3u_priority":
        return _number(facts.m3u_priority)
    if criterion == "audio_channels":
        return _number(facts.audio_channels)
    attribute = _BOOLEAN_CRITERIA.get(criterion)
    if attribute is None:
        return None
    value = getattr(facts, attribute)
    return value if type(value) is bool else None


def _rule_matches(facts: StreamFacts, rule: PointRule) -> bool:
    comparator = _COMPARATORS.get(rule.operator)
    if comparator is None:
        return False

    actual = _points_value(facts, rule.criterion)
    if actual is None:
        return False

    if rule.criterion in _BOOLEAN_CRITERIA:
        if type(rule.value) is not bool or rule.operator not in {"==", "=", "eq", "!=", "ne"}:
            return False
        return comparator(actual, rule.value)

    if rule.criterion == "video_codec":
        expected = _known_codec_rank(rule.value)
    else:
        expected = _number(rule.value)
    if expected is None:
        return False
    return comparator(actual, expected)


def evaluate_points(facts: StreamFacts, rules: Iterable[PointRule]) -> int:
    """Return the sum of every matching rule's signed points."""
    return sum(rule.points for rule in rules if _rule_matches(facts, rule))


def sort_streams_by_points(
    streams: Iterable[StreamFacts], rules: Iterable[PointRule]
) -> list[int]:
    """Sort by total points descending, then by ascending stream ID."""
    rules = tuple(rules)
    return [
        facts.stream_id
        for facts in sorted(
            streams,
            key=lambda facts: (-evaluate_points(facts, rules), facts.stream_id),
        )
    ]


def stream_metadata_criteria(
    strategy: str,
    *,
    priority_criteria: Iterable[str] = (),
    point_rules: Iterable[PointRule] = (),
) -> frozenset[str]:
    """Return Dispatcharr-backed facts required by the selected strategy."""
    if strategy == "priority":
        criteria = priority_criteria
    elif strategy == "points":
        criteria = (rule.criterion for rule in point_rules)
    else:
        raise ValueError(f"Unknown Smart Sort strategy: {strategy!r}")
    return frozenset(criteria) & _STREAM_METADATA_CRITERIA


def sort_streams(
    streams: Iterable[StreamFacts],
    *,
    strategy: str,
    priority_criteria: Iterable[str] = (),
    point_rules: Iterable[PointRule] = (),
    deprioritize_failed: bool = True,
    deprioritize_black_screen: bool = True,
    deprioritize_low_fps: bool = True,
    failed_stream_sort_order: Iterable[str] | None = None,
) -> list[int]:
    """Dispatch normalized facts through the explicitly selected strategy."""
    if strategy == "points":
        return sort_streams_by_points(streams, point_rules)
    if strategy == "priority":
        return sort_streams_by_priority(
            streams,
            priority_criteria,
            deprioritize_failed=deprioritize_failed,
            deprioritize_black_screen=deprioritize_black_screen,
            deprioritize_low_fps=deprioritize_low_fps,
            failed_stream_sort_order=failed_stream_sort_order,
        )
    raise ValueError(f"Unknown Smart Sort strategy: {strategy!r}")
