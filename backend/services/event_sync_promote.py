"""
Event Sync unmatched-stream promotion planner (bead ti939.4.1, epic ti939
"Event Sync" Phase 3).

Opt-in promotion of unmatched secondary-only events to ECM-managed channels
— the ONE sanctioned exception to Event Sync's "ECM never creates channels"
principle. This module owns the PURE half of the feature: which resolved
streams are promotable, how they cluster into promotion units, what the
deterministic channel name of a unit is, and how the per-run cap applies.
Execution (channel create/adopt + stream attach) lives in
``channel_pipeline_executor.ActionExecutor``; the preview endpoint calls
THIS SAME planner over the same resolver output, so the promotion PLAN the
preview shows and the plan a live run executes cannot diverge on identical
inputs (dry-run parity by construction — the same argument as
``services.event_sync_resolver``).

**PO-locked design decisions (implement-as-law):**

* **Lifecycle: reconciliation-driven deletion.** A promoted channel is
  current iff its justifying unmatched stream is observed present in the
  provider playlist THIS run; Pass 4 orphan reconciliation deletes (per the
  rule's ``orphan_action``, default delete) when it drops out. NO wall-clock
  arithmetic, NO parsed or synthesized timestamps in any delete decision,
  NO K-run counters. Nothing in this module reads a clock.
* **Clustering: exact-event-key only.** Same-run unmatched streams (any
  provider) sharing a :func:`services.event_sync_review.master_event_key`
  form ONE promotion unit. NO fuzzy clustering; promoted channels do NOT
  enter the matcher candidate set (they live in the promotion target group,
  which the resolver never reads).

**Which dispositions are promotable (pinned semantics, AC-11):**

* ``unmatched`` — the core case: an event only secondary providers carry.
* ``excluded_by_operator`` — ALSO promotable. An operator exclusion is
  "never attach THIS stream to THAT master" (a pairing-level standing
  order); it says nothing about the stream deserving its own channel.
  Exclusions block ATTACH to a specific master, not promotion.
* ``ambiguous`` is NOT promotable — it is an open operator question that
  may still resolve into an attach; promoting it would race the review
  queue. ``parse_failed`` rows are untouchable (no identity to key on),
  and ``would_attach`` rows have a master.

Only rows with a COMPLETE parsed identity (``master_event_key`` returns a
key) are promotable — an identity-less stream can neither name a channel
deterministically nor be recognized as "the same event" next run.

**Naming: derived from the event key, nothing else.** The channel name is
a pure function of the clustering key (cleaned title + LOCAL clock time;
the date only when genuinely parsed). The name is the adoption identity —
next run's plan finds the existing channel by name in the target group —
so it must be byte-stable across runs, providers and (for dateless
parses, t6bin semantics inherited verbatim) across midnight. A SYNTHESIZED
date NEVER appears in the name or the identity.

**Pure module.** No DB, no Dispatcharr client, no engine imports — the
same isolation contract as ``services.event_sync_matcher``.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.event_sync_matcher import (
    SYNTHESIZED_DATE_PATTERN_NAMES,
    ParsedEvent,
)
from services.event_sync_resolver import (
    DISPOSITION_EXCLUDED,
    DISPOSITION_UNMATCHED,
)
from services.event_sync_review import master_event_key

__all__ = [
    "DEFAULT_MAX_PROMOTE_PER_RUN",
    "MAX_PROMOTE_CEILING",
    "PROMOTABLE_DISPOSITIONS",
    "PROMOTE_ACTION_ATTACH_EXISTING",
    "PROMOTE_ACTION_CREATE",
    "PromotionPlan",
    "PromotionUnit",
    "build_promotion_plan",
    "promoted_channel_name",
]

# Default per-run cap on NEW promoted channels (units whose action is
# "create"). Deliberately small — promotion CREATES channels, a strictly
# bigger blast radius than the attach path's merges, so the default cap is
# a fraction of DEFAULT_MAX_ATTACH_PER_RUN (100). Adoption of an existing
# promoted channel and stream attaches to it never consume cap budget
# (idempotent re-runs must not misreport their own prior work as deferred —
# the same rule the attach cap follows).
DEFAULT_MAX_PROMOTE_PER_RUN: int = 25

# Ceiling for event_sync_config.max_promote_per_run (validated in
# channel_pipeline_schema.validate_event_sync_config). The cap is a
# blast-radius control; a config cannot raise it past this.
MAX_PROMOTE_CEILING: int = 200

# How a promotion unit will be realized against the target group.
PROMOTE_ACTION_CREATE = "create"
PROMOTE_ACTION_ATTACH_EXISTING = "attach_existing"

# Pinned promotable dispositions — see the module docstring for the AC-11
# excluded_by_operator rationale.
PROMOTABLE_DISPOSITIONS = frozenset({
    DISPOSITION_UNMATCHED,
    DISPOSITION_EXCLUDED,
})


def promoted_channel_name(parsed: ParsedEvent) -> str | None:
    """Deterministic promoted-channel name for one complete parsed identity.

    A pure function of the same components :func:`master_event_key` keys on
    — the LOCALS-cleaned title and the LOCAL clock time — so two streams
    that cluster together derive the SAME name regardless of provider
    spelling, and the name a re-run derives always finds the channel the
    previous run created (name-based adoption in the target group).

    * Dated parse: ``<Title-Cased Cleaned Title> @ <Mon DD HH:MM AM/PM>``
      (local clock of the parse timezone — the time the provider name
      literally carried).
    * Dateless parse (``matched_pattern`` in
      :data:`SYNTHESIZED_DATE_PATTERN_NAMES`): the date component of
      ``start`` was fabricated from "now" (t6bin), so the name carries
      ``<Title-Cased Cleaned Title> @ <HH:MM AM/PM>`` — NO date, ever. A
      recurring dateless slot derives the same name every day, so the
      re-run after midnight adopts the same channel instead of minting a
      dated duplicate.

    Returns ``None`` when the parse is incomplete (no title or no start) —
    mirroring ``master_event_key``'s contract: such a stream is not
    promotable.
    """
    if parsed.title is None or parsed.start is None:
        return None
    # Reuse the key's own cleaning by deriving the display title from the
    # SAME cleaned form the key carries (single identity source). The key
    # format is "<cleaned>|<...>"; recompute the cleaned title through
    # master_event_key's exact code path by splitting the key.
    key = master_event_key(parsed)
    if key is None:  # pragma: no cover — guarded by the title/start check
        return None
    cleaned_title = key.split("|", 1)[0]
    display_title = cleaned_title.title()
    if parsed.matched_pattern in SYNTHESIZED_DATE_PATTERN_NAMES:
        clock = parsed.start.strftime("%I:%M %p")
        return f"{display_title} @ {clock}"
    stamp = parsed.start.strftime("%b %d %I:%M %p")
    return f"{display_title} @ {stamp}"


@dataclass(frozen=True)
class PromotionUnit:
    """One promotion unit: every same-run promotable stream sharing one
    exact event key (any provider), realized as ONE channel.

    ``rows`` are the resolver's ResolvedStream objects (kept whole so the
    executor can thread provenance without re-deriving anything), in
    deterministic (provider_id, stream_id) order. ``action`` is the planned
    realization against the CALLER-SUPPLIED existing-name map:
    ``create`` (no channel with the derived name exists in the target
    group) or ``attach_existing`` (adopt + attach only). ``existing_channel_id``
    carries the adopted channel's id when known (display/preview use — the
    executor re-resolves through its own live lookup).
    """

    event_key: str
    channel_name: str
    dateless: bool
    rows: tuple
    action: str
    existing_channel_id: int | None = None


@dataclass(frozen=True)
class PromotionPlan:
    """Full promotion plan for one rule's resolution.

    ``units`` are the units this run will realize; ``capped_units`` are
    create-units beyond the per-run cap — NOT realized this run (runs are
    idempotent: the remainder re-surfaces next run, mirroring the attach
    cap's posture). ``cap`` echoes the effective cap.
    """

    units: tuple[PromotionUnit, ...]
    capped_units: tuple[PromotionUnit, ...]
    cap: int
    target_group_id: int | None

    @property
    def capped(self) -> bool:
        return bool(self.capped_units)

    @property
    def cap_overage(self) -> int:
        return len(self.capped_units)

    @property
    def would_create(self) -> int:
        return sum(1 for u in self.units if u.action == PROMOTE_ACTION_CREATE)

    @property
    def would_attach_existing(self) -> int:
        return sum(
            1 for u in self.units
            if u.action == PROMOTE_ACTION_ATTACH_EXISTING
        )

    @property
    def stream_count(self) -> int:
        return sum(len(u.rows) for u in self.units)


def _row_sort_key(row) -> tuple:
    """Deterministic within-unit stream order: (provider_id, stream_id)."""
    return (
        row.stream.provider_id if row.stream.provider_id is not None else -1,
        row.stream.stream_id if row.stream.stream_id is not None else -1,
        row.stream.name,
    )


def build_promotion_plan(
    config: dict,
    resolved,
    existing_name_to_id: dict[str, int],
) -> PromotionPlan:
    """Build the promotion plan for one rule's resolved streams.

    Args:
        config: A VALIDATED event_sync_config with ``promote_unmatched``
            true (the caller gates on the flag; this function trusts it).
        resolved: Iterable of ``services.event_sync_resolver.ResolvedStream``
            — the resolver output shared by preview and run.
        existing_name_to_id: LOWERCASED channel name -> channel id for the
            channels CURRENTLY in the promotion target group. Drives the
            create-vs-adopt decision. The caller supplies it from the same
            channel data its execution path resolves against, so plan and
            execution agree.

    Determinism: units are ordered by event key (byte order), so cap
    trimming selects the same units on preview and run for identical
    inputs. Two distinct keys that derive the SAME channel name (rare —
    e.g. equal cleaned titles + clock across different tz offsets)
    deliberately collapse onto one channel: the first (by key order)
    creates it, later ones adopt — name identity IS the adoption identity,
    so the plan mirrors what execution would do anyway.
    """
    cap = config.get("max_promote_per_run", DEFAULT_MAX_PROMOTE_PER_RUN)
    target_group_id = config.get("promote_target_group_id")

    by_key: dict[str, list] = {}
    for row in resolved:
        if row.disposition not in PROMOTABLE_DISPOSITIONS:
            continue
        key = master_event_key(row.result.parsed)
        if key is None:
            # Incomplete parsed identity — not promotable (and parse_failed
            # rows never reach here anyway: their disposition is excluded
            # above).
            continue
        by_key.setdefault(key, []).append(row)

    units: list[PromotionUnit] = []
    capped_units: list[PromotionUnit] = []
    creates = 0
    planned_names: dict[str, str] = {}  # lowercased name -> first event key
    for key in sorted(by_key):
        rows = sorted(by_key[key], key=_row_sort_key)
        parsed = rows[0].result.parsed
        name = promoted_channel_name(parsed)
        if name is None:  # pragma: no cover — key is None first
            continue
        name_lower = name.lower()
        existing_id = existing_name_to_id.get(name_lower)
        if existing_id is not None or name_lower in planned_names:
            action = PROMOTE_ACTION_ATTACH_EXISTING
        else:
            action = PROMOTE_ACTION_CREATE
        unit = PromotionUnit(
            event_key=key,
            channel_name=name,
            dateless=parsed.matched_pattern in SYNTHESIZED_DATE_PATTERN_NAMES,
            rows=tuple(rows),
            action=action,
            existing_channel_id=existing_id,
        )
        if action == PROMOTE_ACTION_CREATE:
            if cap and creates >= cap:
                capped_units.append(unit)
                continue
            creates += 1
            planned_names[name_lower] = key
        units.append(unit)

    return PromotionPlan(
        units=tuple(units),
        capped_units=tuple(capped_units),
        cap=cap,
        target_group_id=target_group_id,
    )
