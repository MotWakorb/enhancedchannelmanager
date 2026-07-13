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

**Review-queue decisions (bead ti939.3.2)** are an optional INPUT to this
resolver — never a second scorer. ``decisions`` carries prior operator
accepts/rejects keyed on content fingerprints
(``services.event_sync_review``):

* a REJECTED pairing's master is removed from the stream's candidate set
  BEFORE classification — it can neither attach (threshold or decision)
  nor re-surface as an ambiguous question;
* an ACCEPTED pairing upgrades an ``ambiguous`` disposition to
  ``would_attach`` (``attach_source="review_queue"``) when that master is
  still an attach- or ambiguous-band candidate. Accepts never override the
  matcher's hard rejects (team conflict, time window, below the ambiguous
  floor).

Because preview and attach both call THIS function with the same decisions
object, decision application inherits the dry-run-parity guarantee.
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

import logging
from dataclasses import dataclass, replace
from datetime import datetime

import pytz

from services.event_sync_matcher import (
    BAND_AMBIGUOUS,
    BAND_ATTACH,
    DEFAULT_EVENT_TIMEZONE,
    DEFAULT_TIME_WINDOW_MINUTES,
    EVENT_ATTACH_FLOOR,
    EVENT_NO_TEAMS_FLOOR,
    TEAM_VERDICT_AGREE,
    MasterCandidate,
    StreamMatchResult,
    match_streams,
    parse_event_name,
)
from services.event_sync_review import (
    ATTACH_SOURCE_REVIEW_QUEUE,
    ATTACH_SOURCE_THRESHOLD,
    MAX_REVIEW_CANDIDATES_PER_STREAM,
    PROVIDER_ID_UNKNOWN,
    ReviewDecisions,
    master_event_key,
    stream_name_hash,
)

logger = logging.getLogger(__name__)

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

# S5 (bead sf8dj) — provenance: which optional relaxation admitted a
# would-attach row. Each entry is a (machine_key, human_label) pair. The list
# is a DIAGNOSTIC annotation only — computed from data already on the resolved
# result (and, for assume_current_date, one cheap single-name re-parse). It
# NEVER changes any score, band, or attach decision; the frozen matcher corpus
# is untouched. A flag "contributed" when reverting just it to its default
# would drop this row out of the attach band.
MATCHED_VIA_ASSUME_CURRENT_DATE = ("assume_current_date", "assumed date")
MATCHED_VIA_TIME_WINDOW_IGNORED = ("time_window_ignored", "time ignored")
MATCHED_VIA_LOWERED_THRESHOLD = ("lowered_threshold", "low threshold")
MATCHED_VIA_MASTER_FROM_STREAM = ("master_from_stream", "master-from-stream")

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
    — the matcher never sees them (it scores names only). ``provider_id``
    (the M3U account id) is the fingerprint component review-queue
    decisions key on (bead ti939.3.2) — ``None`` maps to the documented
    unknown-provider sentinel in ``services.event_sync_review``.
    """

    name: str
    group_id: int
    stream_id: int | None = None
    provider: str | None = None
    provider_id: int | None = None


@dataclass(frozen=True)
class ResolvedStream:
    """One secondary stream with its full match result and disposition.

    ``best`` is the winning ATTACH-band candidate when the disposition is
    ``would_attach``, else ``None`` — the exact master Phase 1B attaches to.

    ``ambiguous_reason`` is the machine-readable reason when the disposition
    is ``ambiguous`` (``AMBIGUOUS_REASON_CONTESTED`` /
    ``AMBIGUOUS_REASON_BAND``), else ``None``.

    Review-queue fields (bead ti939.3.2):

    * ``attach_source`` — how a ``would_attach`` disposition was reached:
      ``"threshold"`` (the matcher's own admission) or ``"review_queue"``
      (a prior operator accept). ``None`` for every other disposition.
    * ``review_candidates`` — when the disposition is ``ambiguous``, the
      enqueue-eligible pairings: attach/ambiguous-band candidates that
      survived rejection filtering, best-first, capped at
      ``MAX_REVIEW_CANDIDATES_PER_STREAM``. Empty otherwise.
    * ``rejected_suppressed`` — how many of this stream's candidates were
      removed because the operator previously REJECTED that pairing.
    * ``matched_via`` — S5 (bead sf8dj) diagnostic provenance: for a
      ``would_attach`` row, the (machine_key, human_label) pairs of the
      optional relaxations that admitted it (assume_current_date,
      time_window_ignored, lowered_threshold, master_from_stream). Empty for a
      plain in-window default-threshold match and for every non-attach
      disposition. Annotation only — never influences the match.
    """

    stream: SecondaryStream
    result: StreamMatchResult
    disposition: str
    best: MasterCandidate | None
    ambiguous_reason: str | None = None
    attach_source: str | None = None
    review_candidates: tuple[MasterCandidate, ...] = ()
    rejected_suppressed: int = 0
    matched_via: tuple[tuple[str, str], ...] = ()


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


async def build_master_name_to_id(
    channels: list[dict], client, parse_master_from_stream: bool
) -> dict[str, int]:
    """Map a master IDENTITY name -> master channel id (bead parse-from-stream).

    Default identity is the channel's OWN name. When
    ``parse_master_from_stream`` is on, the identity is the name of the
    channel's FIRST attached stream (fetched by id via ``get_streams_by_ids``)
    — so operators can name master channels freely while the event title+time
    are read from the underlying auto-synced stream. Duplicate identity names
    resolve to the LOWEST channel id (deterministic, mirrors the channel-name
    path). The matcher is unchanged: it scores whatever identity strings this
    map's keys carry, and the attach path maps the winning key back to its
    channel id here.
    """
    name_to_id: dict[str, int] = {}
    if not parse_master_from_stream:
        for ch in channels:
            name, cid = ch.get("name"), ch.get("id")
            if not name or cid is None:
                continue
            if name not in name_to_id or cid < name_to_id[name]:
                name_to_id[name] = cid
        return name_to_id

    # Stream-identity: the first attached stream per channel.
    first_stream_id: dict[int, int] = {}
    for ch in channels:
        cid = ch.get("id")
        streams = ch.get("streams") or []
        if cid is None or not streams:
            continue
        first = streams[0]
        sid = first["id"] if isinstance(first, dict) else first
        if sid is not None:
            first_stream_id[cid] = sid
    if not first_stream_id:
        return name_to_id
    stream_objs = await client.get_streams_by_ids(
        sorted(set(first_stream_id.values()))
    ) or []
    sid_to_name = {
        s.get("id"): s.get("name") for s in stream_objs if s.get("name")
    }
    for cid, sid in sorted(first_stream_id.items()):
        name = sid_to_name.get(sid)
        if not name:
            continue
        if name not in name_to_id or cid < name_to_id[name]:
            name_to_id[name] = cid
    return name_to_id


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
    return _classify_candidates(result.unmatchable_reason, result.candidates)


def _classify_candidates(
    unmatchable_reason: str | None,
    candidates: tuple[MasterCandidate, ...],
) -> tuple[str, MasterCandidate | None, str | None]:
    """Body of :func:`_classify`, parameterized on the candidate tuple.

    Split out (bead ti939.3.2) so decision application can classify a
    REJECTION-FILTERED candidate tuple through the exact same band/contested
    logic — one classification path, whether or not decisions are in play.
    """
    if unmatchable_reason is not None:
        return DISPOSITION_PARSE_FAILED, None, None
    if not candidates:
        return DISPOSITION_UNMATCHED, None, None
    top = candidates[0]
    if top.band == BAND_ATTACH:
        # CONTESTED RAIL (PR #613 review, bead ti939.2.1): a stream whose
        # top candidates cannot be confidently separated is AMBIGUOUS —
        # skip + count, never attach to an alphabetical tie-break winner.
        if _is_contested(candidates):
            return DISPOSITION_AMBIGUOUS, None, AMBIGUOUS_REASON_CONTESTED
        return DISPOSITION_WOULD_ATTACH, top, None
    if top.band == BAND_AMBIGUOUS:
        return DISPOSITION_AMBIGUOUS, None, AMBIGUOUS_REASON_BAND
    return DISPOSITION_UNMATCHED, None, None


def _resolve_stream(
    stream: SecondaryStream,
    result: StreamMatchResult,
    decisions: ReviewDecisions | None,
) -> ResolvedStream:
    """Classify one stream, applying review-queue decisions (ti939.3.2).

    Decision application order (see the module docstring's contract):

    1. REJECTED pairings are removed from the candidate set — an explicit
       operator "no" suppresses both threshold attaches and re-enqueueing
       for that exact pairing. Removal happens BEFORE classification, so
       e.g. a contest between A and a rejected B collapses to a clean
       threshold attach of A (the same answer the operator gave).
    2. The filtered candidates classify through the ONE band/contested
       path (:func:`_classify_candidates`).
    3. An ``ambiguous`` outcome is upgraded to ``would_attach``
       (``attach_source="review_queue"``) when an ACCEPTED pairing's master
       is still an attach- or ambiguous-band candidate — never a reject-band
       one (accepts do not override the matcher's hard rejects). Best-first
       order breaks the (pathological) multiple-accepts tie
       deterministically.
    4. A still-ambiguous stream exposes its enqueue-eligible pairings via
       ``review_candidates`` (attach/ambiguous band, best-first, capped).
    """
    candidates = result.candidates
    rejected_suppressed = 0
    keyed: dict[str, tuple[int, str, str]] = {}

    has_decisions = decisions is not None and (
        decisions.accepted or decisions.rejected
    )
    if has_decisions and candidates:
        provider = (
            stream.provider_id if stream.provider_id is not None
            else PROVIDER_ID_UNKNOWN
        )
        shash = stream_name_hash(stream.name)
        kept: list[MasterCandidate] = []
        for c in candidates:
            event_key = master_event_key(c.parsed)
            if event_key is None:
                kept.append(c)
                continue
            key = (provider, shash, event_key)
            keyed[c.master_name] = key
            if decisions.is_rejected(key):
                rejected_suppressed += 1
            else:
                kept.append(c)
        candidates = tuple(kept)

    disposition, best, ambiguous_reason = _classify_candidates(
        result.unmatchable_reason, candidates
    )

    attach_source: str | None = None
    review_candidates: tuple[MasterCandidate, ...] = ()
    if disposition == DISPOSITION_WOULD_ATTACH:
        attach_source = ATTACH_SOURCE_THRESHOLD
    elif disposition == DISPOSITION_AMBIGUOUS:
        eligible = tuple(
            c for c in candidates if c.band in (BAND_ATTACH, BAND_AMBIGUOUS)
        )
        if has_decisions:
            for c in eligible:
                if decisions.is_accepted(keyed.get(c.master_name)):
                    disposition = DISPOSITION_WOULD_ATTACH
                    best = c
                    ambiguous_reason = None
                    attach_source = ATTACH_SOURCE_REVIEW_QUEUE
                    break
        if disposition == DISPOSITION_AMBIGUOUS:
            review_candidates = eligible[:MAX_REVIEW_CANDIDATES_PER_STREAM]

    return ResolvedStream(
        stream=stream,
        result=result,
        disposition=disposition,
        best=best,
        ambiguous_reason=ambiguous_reason,
        attach_source=attach_source,
        review_candidates=review_candidates,
        rejected_suppressed=rejected_suppressed,
    )


def _matched_via(
    resolved: ResolvedStream,
    *,
    patterns: list[dict] | None,
    now: datetime,
    assume_current_date: bool,
    enforce_time_window: bool,
    time_window_minutes: int,
    parse_master_from_stream: bool,
) -> tuple[tuple[str, str], ...]:
    """S5 provenance for one resolved stream (bead sf8dj).

    Diagnostic only — reads what the matcher already decided; it never
    re-scores, re-bands, or otherwise alters the match. Each entry marks an
    optional relaxation without which this ``would_attach`` row would drop out
    of the attach band. Non-attach dispositions carry no provenance.
    """
    if resolved.disposition != DISPOSITION_WOULD_ATTACH or resolved.best is None:
        return ()
    best = resolved.best
    via: list[tuple[str, str]] = []

    # assume_current_date: the stream had a time but no date, so its start was
    # placed on "today". Counterfactual — re-parse the single stream name with
    # the flag OFF (one cheap parse, not a re-resolution): a parse-fail (no
    # start) proves the flag is what made this stream a candidate at all.
    if assume_current_date and resolved.result.parsed.start is not None:
        without = parse_event_name(
            resolved.stream.name, patterns, now=now, assume_current_date=False
        )
        if without.start is None:
            via.append(MATCHED_VIA_ASSUME_CURRENT_DATE)

    # time_window_ignored: the gate is off AND the winner's time delta exceeds
    # the configured window — with the gate on, that candidate would have been
    # eliminated before scoring.
    if not enforce_time_window and best.time_delta_minutes > time_window_minutes:
        via.append(MATCHED_VIA_TIME_WINDOW_IGNORED)

    # lowered_threshold: the winner's score is below the DEFAULT effective floor
    # it would face (0.80, or 0.90 without team agreement — is_event_attachable
    # policy). It only reached the attach band because the operator lowered
    # attach_threshold beneath that default; the score is >= the configured
    # threshold by attach-band membership.
    default_floor = (
        EVENT_ATTACH_FLOOR
        if best.team_verdict == TEAM_VERDICT_AGREE
        else EVENT_NO_TEAMS_FLOOR
    )
    if best.score < default_floor:
        via.append(MATCHED_VIA_LOWERED_THRESHOLD)

    # master_from_stream: the master identity was read from the master's own
    # stream name rather than the channel name. A clean counterfactual would
    # need the original channel names, which this pure resolver no longer holds
    # (the caller maps identity->id upstream) — so, per the accepted-heuristic
    # allowance, the flag being on for a would-attach row marks it.
    if parse_master_from_stream:
        via.append(MATCHED_VIA_MASTER_FROM_STREAM)

    return tuple(via)


def _log_resolved_debug(
    stream: SecondaryStream,
    result: StreamMatchResult,
    resolved: ResolvedStream,
    via: tuple[tuple[str, str], ...],
) -> None:
    """Emit per-stream + per-candidate DEBUG evidence for live tailing.

    OBSERVABILITY ONLY (bead 03nji) — reads what the matcher already decided;
    never scores, bands, or attaches. Callers MUST guard this with
    ``logger.isEnabledFor(logging.DEBUG)`` so the string/round work below costs
    nothing when DEBUG is off (Python evaluates a log call's args eagerly
    regardless of level, so lazy %-args alone would not spare the computation).
    """
    parsed = result.parsed
    best = resolved.best
    logger.debug(
        "[EVENT-SYNC] resolve stream=%r group=%s -> %s parsed_title=%r "
        "parsed_start=%s best_master=%r score=%s band=%s teams=%s dt=%s "
        "reason=%s matched_via=%s",
        stream.name, stream.group_id, resolved.disposition,
        parsed.title,
        parsed.start.isoformat() if parsed.start else None,
        best.master_name if best is not None else None,
        round(best.score, 4) if best is not None else None,
        best.band if best is not None else None,
        best.team_verdict if best is not None else None,
        round(best.time_delta_minutes, 1) if best is not None else None,
        result.unmatchable_reason or resolved.ambiguous_reason,
        [key for key, _ in via],
    )
    for c in result.candidates:
        logger.debug(
            "[EVENT-SYNC]   candidate stream=%r master=%r score=%.4f band=%s "
            "teams=%s dt=%s reject=%s",
            stream.name, c.master_name, c.score, c.band, c.team_verdict,
            round(c.time_delta_minutes, 1),
            c.reject_reasons[0] if c.reject_reasons else None,
        )


def resolve_event_sync(
    config: dict,
    master_names: list[str],
    secondary_streams: list[SecondaryStream],
    *,
    now: datetime | None = None,
    decisions: ReviewDecisions | None = None,
    attached_stream_ids: set[int] | frozenset[int] | None = None,
) -> EventSyncResolution:
    """Resolve every secondary stream against the master channel names.

    Args:
        config: A VALIDATED event_sync_config (defaults filled by
            ``channel_pipeline_schema.validate_event_sync_config``). The
            attach threshold rides through to the matcher, whose
            ``is_event_attachable`` treats it as authoritative (0.80 is the
            default, not a hard minimum) — this module
            deliberately re-implements no policy.
        master_names: Names of the master group's channels (caller keeps
            the name → channel-ID mapping; this module never sees IDs).
        secondary_streams: Fetched secondary-group streams.
        now: tz-aware anchor for year inference. Defaults to the current
            time, computed ONCE here so every per-group ``match_streams``
            call shares the same anchor (a per-call "now" could infer
            different years across the calls of one resolution near the
            Dec/Jan boundary).
        decisions: Prior review-queue accepts/rejects for THIS rule
            (bead ti939.3.2), fingerprint-keyed — see the module docstring
            and :func:`_resolve_stream` for the application contract.
            ``None`` (or empty) preserves pre-queue behavior exactly.
        attached_stream_ids: Stream ids already attached to a master
            channel (bead 6xxmp). Master-group streams (group_id ==
            master_group_id, present only when
            ``include_master_group_streams`` is on) whose id is in this set
            are dropped BEFORE scoring — the auto-synced provider's own
            streams are already on their channels, so re-offering them would
            desync preview ("would attach") from the run (an idempotent
            no-op). Scoped to the master group so every other secondary
            group's behavior is byte-identical. ``None`` (or empty) filters
            nothing.

    Returns:
        :class:`EventSyncResolution` in a deterministic order — streams
        sorted by (group_id, name, stream_id) — so identical inputs always
        produce identical output (acceptance criterion, bead ti939.1.4).
    """
    if now is None:
        now = datetime.now(pytz.timezone(DEFAULT_EVENT_TIMEZONE))

    # bead krkm4: per-rule opt-out of the time-window candidacy gate.
    # enforce_time_window defaults True (gate on at time_window_minutes);
    # when explicitly False the matcher's window is None — no time gate,
    # candidates ranked by title/team alone. Safe for a single-provider
    # same-day master list; the 0.90 no-teams floor + team/numeric rails
    # still route borderline collisions to the review queue.
    if config.get("enforce_time_window", True):
        window_minutes = config.get(
            "time_window_minutes", DEFAULT_TIME_WINDOW_MINUTES
        )
    else:
        window_minutes = None
    threshold = config.get("attach_threshold", EVENT_ATTACH_FLOOR)
    master_patterns = effective_patterns(config, config["master_group_id"])
    # bead assume-current-date: opt-in dateless matching (both master and
    # secondary sides share the same "today" so their times compare on one day).
    assume_current_date = bool(config.get("assume_current_date", False))
    # S5 provenance inputs (bead sf8dj) — read straight from the config; used
    # only to annotate would-attach rows, never to change the match.
    enforce_time_window = config.get("enforce_time_window", True)
    configured_window = config.get("time_window_minutes", DEFAULT_TIME_WINDOW_MINUTES)
    parse_master_from_stream = bool(config.get("parse_master_from_stream", False))

    # Master-as-ceiling diagnostic: masters with no complete parsed identity
    # can never be candidates (match_streams filters them the same way —
    # same parse function, same patterns, same "now").
    unparsed_masters = tuple(
        name for name in master_names
        if (parsed := parse_event_name(
                name, master_patterns, now=now,
                assume_current_date=assume_current_date,
            )).title is None
        or parsed.start is None
    )

    # bead 6xxmp: drop the master group's already-attached streams (the
    # auto-synced provider's own streams). Scoped to the master group so
    # normal secondary groups resolve exactly as before.
    if attached_stream_ids:
        master_group_id = config["master_group_id"]
        secondary_streams = [
            s for s in secondary_streams
            if not (s.group_id == master_group_id
                    and s.stream_id in attached_stream_ids)
        ]

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
            assume_current_date=assume_current_date,
        )
        for stream, result in zip(streams, results):
            rs = _resolve_stream(stream, result, decisions)
            via = _matched_via(
                rs,
                patterns=patterns,
                now=now,
                assume_current_date=assume_current_date,
                enforce_time_window=enforce_time_window,
                time_window_minutes=configured_window,
                parse_master_from_stream=parse_master_from_stream,
            )
            if via:
                rs = replace(rs, matched_via=via)
            # Gated per-candidate DEBUG evidence (bead 03nji) — no-op unless
            # LOG_LEVEL=DEBUG. Guarded so the formatting/round work is skipped
            # entirely below DEBUG. Observability only; the match is unchanged.
            if logger.isEnabledFor(logging.DEBUG):
                _log_resolved_debug(stream, result, rs, via)
            resolved.append(rs)

    return EventSyncResolution(
        resolved=tuple(resolved),
        unparsed_master_names=unparsed_masters,
    )
