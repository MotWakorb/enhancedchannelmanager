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
* ``ambiguous`` — best candidate is in the AMBIGUOUS band, OR the attach
  decision is CONTESTED (more than one attach-band candidate / runner-up
  within ``CONTESTED_SCORE_EPSILON`` of the winner — the PR #613 rail):
  surfaced for operator review, never auto-attached.
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
    "AMBIGUOUS_REASON_BAND",
    "AMBIGUOUS_REASON_CONTESTED",
    "CONTESTED_SCORE_EPSILON",
    "DEFAULT_MAX_ATTACH_PER_RUN",
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

# Machine-readable reasons for an AMBIGUOUS disposition (preview response +
# journal contract, bead ti939.2.1).
#
# ``contested_top_candidates`` is the CONTESTED RAIL mandated by the PR #613
# review: the matcher's team-agree boost can tie same-fixture-different-
# session masters at identical scores ("Fury vs. Usyk" main card and
# "Fury vs. Usyk Prelims" both score 1.0 against either stream), and a
# classification that only looks at candidates[0] would let the alphabetical
# tie-break pick the attach target — Phase 1B would attach to the WRONG
# master. When more than one candidate lands in the attach band, or the
# runner-up is within ``CONTESTED_SCORE_EPSILON`` of the winner, the stream
# is AMBIGUOUS (skip + count) — never attached. Precision over recall
# (1,341-incident trust benchmark).
AMBIGUOUS_REASON_CONTESTED = "contested_top_candidates"
# The pre-existing ambiguous case: the single best candidate itself scored
# into the matcher's AMBIGUOUS band.
AMBIGUOUS_REASON_BAND = "top_candidate_ambiguous_band"

# Runner-up-within-epsilon-of-winner ⇒ contested. Deliberately generous
# (more contested = fewer attaches = the conservative direction): two
# distinct real-world events that both survive the time-window block and
# score within 0.05 of each other are not a confident single match.
CONTESTED_SCORE_EPSILON: float = 0.05

# Default per-run attach cap for the Phase 1B attach executor
# (event_sync_config.max_attach_per_run; validated by
# channel_pipeline_schema.validate_event_sync_config). Blast-radius control:
# the existing created-channel cap does not cover merge/attach operations.
DEFAULT_MAX_ATTACH_PER_RUN: int = 100


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

    ``ambiguous_reason`` is the machine-readable reason when the disposition
    is ``ambiguous`` (``AMBIGUOUS_REASON_CONTESTED`` /
    ``AMBIGUOUS_REASON_BAND``), else ``None``.
    """

    stream: SecondaryStream
    result: StreamMatchResult
    disposition: str
    best: MasterCandidate | None
    ambiguous_reason: str | None = None


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


def _is_contested(candidates: tuple[MasterCandidate, ...]) -> bool:
    """True when the winner's attach decision is CONTESTED (PR #613 rail).

    Contested means EITHER of (candidates arrive best-first):

    * a second candidate also landed in the ATTACH band, or
    * the runner-up's score is within ``CONTESTED_SCORE_EPSILON`` of the
      winner's (whatever band the runner-up fell into — a near-tie means the
      matcher could not confidently separate two masters).

    Only meaningful when ``candidates[0]`` is in the attach band; the caller
    guarantees that.
    """
    top = candidates[0]
    # The epsilon check only needs candidates[1] (best-first ordering), but
    # the attach-band check must scan ALL runners: band is NOT monotonic in
    # score, because the no-teams floor raises the effective attach threshold
    # PER CANDIDATE (matcher.is_event_attachable). The natural hidden
    # contender is an ambiguous-band runner sitting between two attach-band
    # candidates: a verdict-absent candidate at 0.85 lands AMBIGUOUS (needs
    # >= EVENT_NO_TEAMS_FLOOR without team agreement) yet outscores a
    # team-agree attach-band contender at 0.82. (Rejects can never hide a
    # contender this way — every reject rail forces score 0.0 or sits below
    # the ambiguous floor.)
    if len(candidates) > 1 \
            and top.score - candidates[1].score <= CONTESTED_SCORE_EPSILON:
        return True
    return any(runner.band == BAND_ATTACH for runner in candidates[1:])


def _classify(
    result: StreamMatchResult,
) -> tuple[str, MasterCandidate | None, str | None]:
    """Disposition of one match result (candidates arrive best-first).

    Returns ``(disposition, best, ambiguous_reason)``. ``best`` is only set
    for ``would_attach``; ``ambiguous_reason`` only for ``ambiguous``.
    """
    if result.unmatchable_reason is not None:
        return DISPOSITION_PARSE_FAILED, None, None
    if not result.candidates:
        return DISPOSITION_UNMATCHED, None, None
    top = result.candidates[0]
    if top.band == BAND_ATTACH:
        # CONTESTED RAIL (PR #613 review, bead ti939.2.1): a stream whose
        # top candidates cannot be confidently separated is AMBIGUOUS —
        # skip + count, never attach to an alphabetical tie-break winner.
        if _is_contested(result.candidates):
            return DISPOSITION_AMBIGUOUS, None, AMBIGUOUS_REASON_CONTESTED
        return DISPOSITION_WOULD_ATTACH, top, None
    if top.band == BAND_AMBIGUOUS:
        return DISPOSITION_AMBIGUOUS, None, AMBIGUOUS_REASON_BAND
    return DISPOSITION_UNMATCHED, None, None


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
            disposition, best, ambiguous_reason = _classify(result)
            resolved.append(ResolvedStream(
                stream=stream,
                result=result,
                disposition=disposition,
                best=best,
                ambiguous_reason=ambiguous_reason,
            ))

    return EventSyncResolution(
        resolved=tuple(resolved),
        unparsed_master_names=unparsed_masters,
    )
