"""
Event sync resolver — the ONE resolution layer shared by the Phase 1A
preview endpoint and the Phase 1B attach path
(bead enhancedchannelmanager-ti939.1.4, epic ti939 "Event Sync").

:func:`resolve_event_sync` takes a **validated** event_sync_config plus the
already-fetched master channel names and secondary streams, routes every
stream through ``services.event_sync_matcher.match_streams`` (per-group
pattern overrides applied), and classifies each stream into exactly one
disposition:

* ``would_attach`` — best candidate is in the matcher's ATTACH band; Phase
  1B attaches the stream to that master (preview only reports it).
* ``ambiguous`` — best candidate is in the AMBIGUOUS band: surfaced for
  operator review, never auto-attached.
* ``unmatched`` — parses fine but no candidate survives (master-as-ceiling
  hedge: this list is the evidence base for any Phase 3 promotion feature).
* ``parse_failed`` — no complete parsed identity (title or start time
  missing). A silently broken pattern shows up here, loudly.

**Dry-run parity by construction.** The preview endpoint and the future
attach executor both call THIS function; there is no second scoring path
to drift. Scoring itself lives one layer down in the pure matcher.

**Pure module.** No Dispatcharr client, no DB, no engine imports — the
caller fetches; this module only resolves. Master channels are identified
by NAME only (PO decision: stateless recompute); the caller re-resolves
channel IDs against Dispatcharr on every run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytz

from services.event_sync_matcher import (
    BAND_AMBIGUOUS,
    BAND_ATTACH,
    DEFAULT_EVENT_TIMEZONE,
    DEFAULT_TIME_WINDOW_MINUTES,
    EVENT_ATTACH_FLOOR,
    MasterCandidate,
    StreamMatchResult,
    match_streams,
    parse_event_name,
)

__all__ = [
    "DISPOSITION_AMBIGUOUS",
    "DISPOSITION_PARSE_FAILED",
    "DISPOSITION_UNMATCHED",
    "DISPOSITION_WOULD_ATTACH",
    "EventSyncResolution",
    "ResolvedStream",
    "SecondaryStream",
    "effective_patterns",
    "resolve_event_sync",
]

# Stream dispositions (machine-readable; preview response + journal contract).
DISPOSITION_WOULD_ATTACH = "would_attach"
DISPOSITION_AMBIGUOUS = "ambiguous"
DISPOSITION_UNMATCHED = "unmatched"
DISPOSITION_PARSE_FAILED = "parse_failed"


@dataclass(frozen=True)
class SecondaryStream:
    """One secondary-group stream as the caller fetched it.

    ``stream_id`` and ``provider`` are pass-through display/attach metadata
    — the matcher never sees them (it scores names only).
    """

    name: str
    group_id: int
    stream_id: int | None = None
    provider: str | None = None


@dataclass(frozen=True)
class ResolvedStream:
    """One secondary stream with its full match result and disposition.

    ``best`` is the winning ATTACH-band candidate when the disposition is
    ``would_attach``, else ``None`` — the exact master Phase 1B attaches to.
    """

    stream: SecondaryStream
    result: StreamMatchResult
    disposition: str
    best: MasterCandidate | None


@dataclass(frozen=True)
class EventSyncResolution:
    """Full resolution of one event_sync config against fetched data.

    ``unparsed_master_names`` lists master channels with no complete parsed
    identity — they can never be attach targets, so a master group whose
    names stop parsing must be loud, not an inexplicably empty preview.
    """

    resolved: tuple[ResolvedStream, ...]
    unparsed_master_names: tuple[str, ...]


def effective_patterns(config: dict, group_id: int) -> list[dict] | None:
    """Parse-pattern set for one group: per-group override → shared → None.

    ``None`` means the matcher's built-in ``DEFAULT_EVENT_PATTERNS``.
    ``group_patterns`` keys are looked up as both int and str — JSON object
    keys arrive as strings after a storage round-trip, dict-literal callers
    may pass ints (mirrors validate_event_sync_config's key handling).
    """
    group_patterns = config.get("group_patterns") or {}
    for key in (group_id, str(group_id)):
        if key in group_patterns:
            return group_patterns[key]
    return config.get("patterns")


def _classify(result: StreamMatchResult) -> tuple[str, MasterCandidate | None]:
    """Disposition of one match result (candidates arrive best-first)."""
    if result.unmatchable_reason is not None:
        return DISPOSITION_PARSE_FAILED, None
    if not result.candidates:
        return DISPOSITION_UNMATCHED, None
    top = result.candidates[0]
    if top.band == BAND_ATTACH:
        return DISPOSITION_WOULD_ATTACH, top
    if top.band == BAND_AMBIGUOUS:
        return DISPOSITION_AMBIGUOUS, None
    return DISPOSITION_UNMATCHED, None


def resolve_event_sync(
    config: dict,
    master_names: list[str],
    secondary_streams: list[SecondaryStream],
    *,
    now: datetime | None = None,
) -> EventSyncResolution:
    """Resolve every secondary stream against the master channel names.

    Args:
        config: A VALIDATED event_sync_config (defaults filled by
            ``channel_pipeline_schema.validate_event_sync_config``). The
            attach threshold rides through to the matcher, whose
            ``is_event_attachable`` hard-clamps it >= 0.80 — this module
            deliberately re-implements no policy.
        master_names: Names of the master group's channels (caller keeps
            the name → channel-ID mapping; this module never sees IDs).
        secondary_streams: Fetched secondary-group streams.
        now: tz-aware anchor for year inference. Defaults to the current
            time, computed ONCE here so every per-group ``match_streams``
            call shares the same anchor (a per-call "now" could infer
            different years across the calls of one resolution near the
            Dec/Jan boundary).

    Returns:
        :class:`EventSyncResolution` in a deterministic order — streams
        sorted by (group_id, name, stream_id) — so identical inputs always
        produce identical output (acceptance criterion, bead ti939.1.4).
    """
    if now is None:
        now = datetime.now(pytz.timezone(DEFAULT_EVENT_TIMEZONE))

    window_minutes = config.get("time_window_minutes", DEFAULT_TIME_WINDOW_MINUTES)
    threshold = config.get("attach_threshold", EVENT_ATTACH_FLOOR)
    master_patterns = effective_patterns(config, config["master_group_id"])

    # Master-as-ceiling diagnostic: masters with no complete parsed identity
    # can never be candidates (match_streams filters them the same way —
    # same parse function, same patterns, same "now").
    unparsed_masters = tuple(
        name for name in master_names
        if (parsed := parse_event_name(name, master_patterns, now=now)).title is None
        or parsed.start is None
    )

    by_group: dict[int, list[SecondaryStream]] = {}
    for stream in secondary_streams:
        by_group.setdefault(stream.group_id, []).append(stream)

    resolved: list[ResolvedStream] = []
    for group_id in sorted(by_group):
        patterns = effective_patterns(config, group_id)
        streams = sorted(
            by_group[group_id], key=lambda s: (s.name, s.stream_id or 0)
        )
        results = match_streams(
            [s.name for s in streams],
            master_names,
            patterns=patterns,
            master_patterns=master_patterns,
            window_minutes=window_minutes,
            threshold=threshold,
            now=now,
        )
        for stream, result in zip(streams, results):
            disposition, best = _classify(result)
            resolved.append(ResolvedStream(
                stream=stream,
                result=result,
                disposition=disposition,
                best=best,
            ))

    return EventSyncResolution(
        resolved=tuple(resolved),
        unparsed_master_names=unparsed_masters,
    )
