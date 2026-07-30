"""The channels restore importer — channel-row create + profile reattach + streams.

Bead ``enhancedchannelmanager-4vouz`` (split 2/4) created the channel-row +
profile-reattach layer; bead ``enhancedchannelmanager-0i2vt.14`` (the INTEGRATION
umbrella) extends it with the stream layer. This module restores the CHANNEL
entity category from a Dispatcharr export archive: it creates each archived
channel row, reattaches each channel to its archived channel-profile memberships,
AND re-attaches each channel's archived embedded streams to the destination.

THE STREAM LAYER (``0i2vt.14`` integration — wires the three sibling splits).
For each restored channel, the archived embedded ``streams`` are matched against
the destination instance's existing streams using the pure 4-tier matcher
(:func:`dbas.stream_matcher.match_stream`, bead ``al6e3``):

* Tier 1-4 HIT  -> the matched DESTINATION stream id is attached to the channel.
  The existing stream is NOT created and NOT ledgered (it already exists; only
  the attachment is new).
* MISS (tier 0) -> the archived stream is an ORPHAN. Orphans are collected across
  the WHOLE import and handed, ONCE, to
  :func:`dbas.custom_stream_fallback.synthesize_custom_streams` (bead ``ahygg``),
  threading one :class:`~dbas.custom_stream_fallback._FallbackState` so the
  synthesized custom-stream M3U account is found-or-created at most once. The
  synthesizer creates each orphan, ledgers it (``EntityType.STREAM``), records it
  in the remap, and WARNs. The synthesized destination ids are then attached back
  to the channels they came from.

ATTACH MECHANISM. A Dispatcharr channel's stream set IS an ordered list of stream
ids on the channel: ``client.update_channel(channel_id, {"streams": [ids…]})``
replaces the channel's stream list (verified against ``routers/channels.py`` —
reorder/merge use this same call). ORDERING is preserved by emitting the
destination ids in the archived stream order: each archived stream owns one slot,
filled with its matched id (immediately) or its synthesized id (after the batched
fallback resolves), then the whole ordered list is sent in one ``update_channel``.

create_stream (``nav0c``) is used ONLY inside the fallback synthesizer for
orphans; a matched stream is never created. The STREAM-category counts in the
report distinguish them: synthesized orphans land in ``created`` (ahygg), matched
existing streams in ``updated`` (attached-not-created).

FK remapping (the load-bearing correctness control). A Dispatcharr export records
each entity's id *as it was on the source instance*; on restore the destination
assigns its own ids. FK reference fields on a channel therefore point at SOURCE
ids and MUST be rewritten to DESTINATION ids (via the shared
:class:`~dbas.restore_contracts.IdRemapTable`, populated by the groups/profiles
importer ``0i2vt.12``) before the create is sent — or the channel is skipped
``DEPENDENCY_UNRESOLVED`` rather than sent upstream with a stale archive id.

The channel FK fields and how each is handled:

* ``channel_group_id`` -> :data:`EntityType.CHANNEL_GROUP` — REMAPPED. Resolved
  through the IdRemapTable; unresolved => channel skipped DEPENDENCY_UNRESOLVED.
* ``stream_profile_id`` -> :data:`EntityType.STREAM_PROFILE` — REMAPPED. Same
  treatment.
* ``logo_id`` / ``epg_data_id`` -> NO EntityType in the restore contract. Logos
  and EPG data are owned by separate beads (logos: ``.15`` / ``.19``, surfaced
  via :attr:`RestoreReport.logo_misses`); there is no id namespace in the remap
  to resolve them. Sending the stale archive id would dangle a reference, so
  these fields are DROPPED from the create payload. (A later logo/epg bead
  reattaches them; this importer must not invent or forward a stale id.)

Channel-profile membership reattach. Channels belong to channel profiles. After a
channel is created, each archived membership is reattached via
``client.update_profile_channel(dest_profile_id, dest_channel_id, {"enabled": ...})``.
The destination profile id is resolved through the IdRemapTable
(:data:`EntityType.CHANNEL_PROFILE`, populated by ``0i2vt.12``). If the profile
is NOT in the remap (the channel-profiles importer did not run, or did not
restore that profile), the membership is handled as ``DEPENDENCY_UNRESOLVED`` and
recorded under the :data:`EntityType.CHANNEL_PROFILE` category — never crashing,
never guessing a profile id.

Collision taxonomy. A channel whose ``(name, channel_number)`` already exists on
the destination is skipped ``ALREADY_EXISTS_IDENTICAL`` (never overwritten); its
source id is still remapped to the existing destination id so a later profile
reattach can resolve it. A create that races into an upstream uniqueness conflict
is failed ``CONFLICT``.

AMBIGUOUS NULL-NUMBER COLLISION (spike ``xp6mp`` DBA ruling 1a — UNIFORM fix).
``channel_number`` is OPTIONAL on a Dispatcharr channel. A ``(name, None)``
archived channel that "matches" a ``(name, None)`` destination channel is NOT a
proven identity — two distinct channels can share a name when neither carries a
number, so a silent ``ALREADY_EXISTS_IDENTICAL`` would let a real, different
channel be skipped. Under the CONTINUOUS cross-instance sync that reuses this
importer (epic ``i39wu``, bead ``kcxie``) that silent skip becomes *permanent*
recurring divergence — B never converges on A's channel because every cycle the
ambiguous name-only key re-collides and is silently no-op'd. So a name match
where ``channel_number`` is null/absent on BOTH sides is surfaced as
``FailureReason.CONFLICT`` (a failed-with-reason in the report), never a silent
skip. This is a CORRECTNESS fix inside this one importer; it applies UNIFORMLY
(DBAS restore too — it was a latent one-shot bug). A NON-null number equality
match stays ``ALREADY_EXISTS_IDENTICAL`` (a real channel-number IS a stable
identity).

Opt-in. The category does nothing unless the operator selected it (``selected``).

Integration with the restore contracts (bead ``kxuj2``): results land in the
shared :class:`~dbas.restore_contracts.RestoreReport`
(:data:`EntityType.CHANNEL` category, plus :data:`EntityType.CHANNEL_PROFILE` for
unresolved memberships), created channels register source->dest in the
:class:`~dbas.restore_contracts.IdRemapTable`, and every created channel is
recorded in the :class:`~dbas.restore_contracts.RollbackLedger` so a later
failure compensates by deleting it. This importer imports the contracts module
READ-ONLY.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from dbas.custom_stream_fallback import _FallbackState, synthesize_custom_streams
from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipDetail,
    SkipReason,
)
from dbas.stream_matcher import MatchTier, match_stream
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Channel FK fields whose ids reference another entity that the restore remaps.
# Maps the archive field name -> the EntityType namespace to resolve it through.
# These are the ONLY FK fields the contract can remap (the others — logo_id,
# epg_data_id — have no EntityType and are dropped, see _NON_REMAPPABLE_FK_KEYS).
_REMAPPABLE_FK_FIELDS = {
    "channel_group_id": EntityType.CHANNEL_GROUP,
    "stream_profile_id": EntityType.STREAM_PROFILE,
}

# FK fields that reference an entity NOT in the restore-contract id namespace.
# Logos / EPG data are owned by separate beads (logos: .15 / .19). There is no
# remap to rewrite their ids, so we DROP them rather than forward a stale archive
# id that would dangle. A later logo/epg bead reattaches them.
_NON_REMAPPABLE_FK_KEYS = frozenset({"logo_id", "epg_data_id"})

# Archive-source identifiers the destination assigns itself, never forwarded.
# ``uuid`` is the same class as id/pk: Dispatcharr mints one per channel row;
# forwarding A's uuid to B would alias two distinct rows under one identity.
_SOURCE_ID_KEYS = frozenset({"id", "pk", "uuid"})

# Source-side provenance + derived echoes a LIVE channel GET carries that the
# DBAS archive shape never had (live two-instance validation, bead 7ipq2.2 —
# field survey of a real Dispatcharr 0.28.2 response is in the bead):
#
# * ``auto_created_by`` is an A-LOCAL pk (the auto-creating M3U account). On a
#   real Dispatcharr-B it made EVERY channel create fail
#   ``400 {"auto_created_by": ["Invalid pk \"17\" - object does not exist."]}``.
# * ``auto_created=true`` marks a channel as owned by the destination's own
#   auto-create lifecycle — Dispatcharr garbage-collects auto-created channels
#   whose backing stream disappears, which must never happen to a synced
#   replica row B cannot re-derive.
# * ``auto_created_by_name`` / ``source_stream`` / ``override`` are read-only
#   derived echoes; ``effective_*`` (matched by prefix in
#   :func:`_build_create_payload`) are the resolved-value echoes.
_SOURCE_PROVENANCE_KEYS = frozenset(
    {
        "auto_created",
        "auto_created_by",
        "auto_created_by_name",
        "source_stream",
        "override",
    }
)

# Derived read-only echo prefix on live GET responses (effective_name,
# effective_channel_number, ...) — dropped by prefix, never sent on a create.
_DERIVED_ECHO_PREFIX = "effective_"

# Embedded/derived keys that are NOT part of a channel create payload. ``streams``
# is the stream-attachment SEAM owned by bead 0i2vt.14 — this importer strips it
# and never attaches a stream. ``profile_memberships`` is consumed separately by
# the profile-reattach step (post-create), not sent in the create body. Other
# read-only/derived fields a GET echoes back are dropped defensively.
_NON_CREATE_KEYS = frozenset(
    {
        "streams",
        "profile_memberships",
        "channelprofilemembership_set",
        "stream_count",
        "stats",
    }
)

# All keys dropped before issuing the create. The remappable FK fields are not in
# this set — they are rewritten in-place to destination ids (or the channel is
# skipped if unresolvable) rather than dropped.
_DROPPED_CREATE_KEYS = (
    _SOURCE_ID_KEYS
    | _NON_REMAPPABLE_FK_KEYS
    | _NON_CREATE_KEYS
    | _SOURCE_PROVENANCE_KEYS
)


def _channel_label(archive_channel: dict) -> str:
    """Operator-facing identifier for a channel — its name, never a secret."""
    name = archive_channel.get("name")
    return str(name) if name else "<unknown>"


def _build_create_payload(archive_channel: dict, remap: IdRemapTable) -> dict:
    """Build the create_channel payload, rewriting remappable FK ids to dest ids.

    Returns the payload on success, or ``None`` if a remappable FK reference could
    not be resolved through ``remap`` (the channel must then be skipped
    ``DEPENDENCY_UNRESOLVED`` rather than created with a stale archive id).

    Drops the archive's source id, the non-remappable FK fields (logo/epg, owned
    by other beads), and the embedded/derived non-create keys (notably the
    ``streams`` seam owned by bead 0i2vt.14).
    """
    payload = {
        k: v
        for k, v in archive_channel.items()
        if k not in _DROPPED_CREATE_KEYS and not k.startswith(_DERIVED_ECHO_PREFIX)
    }
    for field, entity_type in _REMAPPABLE_FK_FIELDS.items():
        source_id = archive_channel.get(field)
        if source_id is None:
            continue
        dest_id = remap.resolve(entity_type, int(source_id))
        if dest_id is None:
            return None
        payload[field] = dest_id
    return payload


def _existing_channel_key(channel: dict) -> tuple:
    """Identity key for an existing destination channel: (name, channel_number).

    A restored channel that matches an existing one on this key is a candidate
    no-op (ALREADY_EXISTS_IDENTICAL) ONLY when ``channel_number`` is non-null on
    both sides; ``channel_number`` may be absent on either side, in which case the
    match is ambiguous and handled separately (see :func:`_is_ambiguous_null_key`,
    spike ``xp6mp`` ruling 1a).
    """
    return (channel.get("name"), channel.get("channel_number"))


def _is_ambiguous_null_key(archive_channel: dict, existing: dict) -> bool:
    """True when a (name, channel_number) match is AMBIGUOUS — number null on both.

    Spike ``xp6mp`` ruling 1a: a name match where ``channel_number`` is null/absent
    on BOTH the archived row and the matched destination row does not PROVE the two
    are the same channel (a name is not a unique identity without a number). Such a
    match is surfaced ``FailureReason.CONFLICT`` (failed-with-reason), never a
    silent ``ALREADY_EXISTS_IDENTICAL``. A non-null number equality match is a real
    identity and is NOT ambiguous.
    """
    return (
        archive_channel.get("channel_number") is None
        and existing.get("channel_number") is None
    )


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create_channel failure into a restore-contract FailureReason.

    A name/number uniqueness conflict (the error body echoing "already exists" /
    "unique") maps to ``CONFLICT``; everything else is an upstream API error.
    """
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception) -> str:
    """Produce a sanitized, operator-facing failure message (no raw traces)."""
    return (str(exc) or "").strip() or "Upstream rejected the channel creation request."


async def import_channels(
    *,
    archive_channels: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
    allow_fuzzy_stream_match: bool = True,
) -> None:
    """Restore the CHANNEL category: create channel rows + reattach profiles.

    Args:
        archive_channels: The channel records from the export archive. Each is a
            dict; the archive's source id, non-remappable FK fields, and embedded
            stream payload are dropped/handled per the module docstring.
        client: The Dispatcharr API client.
        selected: The per-category opt-in flag. When ``False`` the entire category
            is skipped (no creates) — every channel recorded EXCLUDED_BY_OPERATOR.
        report: The shared :class:`RestoreReport`; channel results land in the
            ``EntityType.CHANNEL`` category, unresolved profile memberships in
            ``EntityType.CHANNEL_PROFILE``.
        ledger: The shared :class:`RollbackLedger`; each created channel is
            recorded for compensating deletes.
        remap: The shared :class:`IdRemapTable`. READ to resolve channel FK
            references (channel_group / stream_profile) and channel-profile ids;
            WRITTEN with each created channel's source->dest id (under
            ``EntityType.CHANNEL``) so the stream-attachment bead and the profile
            reattach can resolve channels.
        is_dry_run: When ``True``, nothing is created or reattached — the importer
            only reports ``would_create`` / ``would_skip`` so the operator sees
            the plan.
        allow_fuzzy_stream_match: Whether the embedded-stream matcher may use its
            Tier-4 fuzzy rung. ``True`` (default) keeps the full 4-tier ladder —
            the DBAS archive-restore behaviour. ``False`` FLOORS stream matching
            at Tier-3 exact-normalized (spike ``xp6mp`` ruling 1b): the
            cross-instance sync path passes the per-``SyncTarget``
            ``fuzzy_stream_matching`` flag (default off) so a continuous sync does
            not let a fuzzy name guess silently shadow a real stream every cycle.
            When fuzzy IS allowed and a Tier-4 hit wins, the attach is flagged
            LOW-CONFIDENCE in ``report.notes`` rather than counted as a silent
            ``updated``.
    """
    cat = report.category(EntityType.CHANNEL)

    # OPT-IN. Off unless the operator selected the channels category.
    if not selected:
        logger.info("[DBAS-CHANNELS] Category not selected; skipping channels.")
        for archive_channel in archive_channels:
            _skip(
                cat,
                SkipReason.EXCLUDED_BY_OPERATOR,
                _channel_label(archive_channel),
                archive_channel.get("id"),
                is_dry_run,
            )
        return

    logger.info(
        "[DBAS-CHANNELS] Restoring channels (dry_run=%s); %d archived channel(s).",
        is_dry_run,
        len(archive_channels),
    )

    # Pre-fetch existing channels to detect (name, channel_number) collisions.
    existing_by_key: dict[tuple, dict] = {}
    try:
        existing = await client.get_channels(page_size=1000)
        results = existing.get("results", []) if isinstance(existing, dict) else existing
        for ch in results or []:
            if isinstance(ch, dict):
                existing_by_key[_existing_channel_key(ch)] = ch
    except Exception as exc:
        logger.warning("[DBAS-CHANNELS] Could not list existing channels: %s", exc)

    # Channels created/resolved this run, paired with their archive record, so the
    # profile-reattach pass (post-create) can resolve each channel's dest id.
    # Each tuple: (archive_channel, destination_channel_id).
    reattach_queue: list[tuple[dict, int]] = []

    # Per-channel stream-attach plans, built during the create loop and resolved
    # after the batched custom-stream fallback runs. Each is a _ChannelStreamPlan
    # carrying the channel's dest id + one ordered slot per archived stream.
    stream_plans: list[_ChannelStreamPlan] = []

    # Destination candidate streams for the matcher — fetched ONCE for the whole
    # import (the matcher is pure; the candidate set is the destination's existing
    # streams). Empty/failed fetch => every archived stream MISSes and routes to
    # the custom-stream fallback (never a wrong attach).
    candidate_streams = (
        await _fetch_candidate_streams(client) if not is_dry_run else []
    )

    for archive_channel in archive_channels:
        label = _channel_label(archive_channel)
        source_id = archive_channel.get("id")

        # Collision: a (name, channel_number) match already on the destination.
        existing = existing_by_key.get(_existing_channel_key(archive_channel))
        if existing is not None:
            # AMBIGUOUS null-number collision (spike xp6mp ruling 1a, UNIFORM): a
            # name match with channel_number null on BOTH sides is not a proven
            # identity. Surface CONFLICT (failed-with-reason) instead of silently
            # skipping a possibly-different channel — under continuous sync that
            # silent skip is permanent recurring divergence.
            if _is_ambiguous_null_key(archive_channel, existing):
                cat.failed += 1
                cat.failure_details.append(
                    FailureDetail(
                        reason=FailureReason.CONFLICT,
                        label=label,
                        message=(
                            "ambiguous collision: a channel named %r with no "
                            "channel_number already exists on the destination; "
                            "cannot prove identity without a channel_number."
                            % label
                        ),
                        source_export_id=source_id,
                    )
                )
                logger.warning(
                    "[DBAS-CHANNELS] Channel '%s' is an AMBIGUOUS null-number "
                    "collision (name matches an existing numberless channel); "
                    "surfaced as CONFLICT (ruling 1a), not silently skipped.",
                    label,
                )
                continue

            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            # Still remap source -> existing dest id so a later profile reattach
            # (and the stream-attachment bead) can resolve this channel.
            existing_id = existing.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(EntityType.CHANNEL, int(source_id), int(existing_id))
                if not is_dry_run:
                    reattach_queue.append((archive_channel, int(existing_id)))
                    _plan_streams(
                        stream_plans,
                        archive_channel,
                        int(existing_id),
                        candidate_streams,
                        report,
                        allow_fuzzy_stream_match,
                    )
            continue

        # FK remap: rewrite remappable FK ids; unresolved => DEPENDENCY_UNRESOLVED.
        payload = _build_create_payload(archive_channel, remap)
        if payload is None:
            logger.info(
                "[DBAS-CHANNELS] Channel '%s' skipped — an FK dependency is "
                "unresolved (not yet restored).",
                label,
            )
            _skip(cat, SkipReason.DEPENDENCY_UNRESOLVED, label, source_id, is_dry_run)
            continue

        if is_dry_run:
            cat.would_create += 1
            # Provisional remap so a downstream reference to this would-be-created
            # channel resolves on the dry-run as it would on apply (anti-drift).
            # Source id used as a stable provisional destination id — never sent
            # upstream.
            if source_id is not None:
                remap.add(EntityType.CHANNEL, int(source_id), int(source_id))
            continue

        try:
            created = await client.create_channel(payload)
        except Exception as exc:
            reason = _failure_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason,
                    label=label,
                    message=_sanitize_failure(exc),
                    source_export_id=source_id,
                )
            )
            logger.warning(
                "[DBAS-CHANNELS] Failed to restore channel '%s': %s", label, reason.value
            )
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(EntityType.CHANNEL, int(source_id), dest_id)
            ledger.record_created(EntityType.CHANNEL, dest_id, label)
            reattach_queue.append((archive_channel, dest_id))
            _plan_streams(
                stream_plans,
                archive_channel,
                dest_id,
                candidate_streams,
                report,
                allow_fuzzy_stream_match,
            )
        logger.info("[DBAS-CHANNELS] Restored channel '%s' (id=%s).", label, dest_id)

    # Profile reattach pass — runs AFTER all channels are created so every channel
    # has a destination id. Dry-run reattaches nothing.
    if not is_dry_run:
        await _reattach_profiles(reattach_queue, client=client, report=report, remap=remap)
        # Stream layer: synthesize orphans (batched, one fallback state) + attach
        # the matched + synthesized stream ids to each channel in archived order.
        await _attach_streams(
            stream_plans,
            client=client,
            report=report,
            ledger=ledger,
            remap=remap,
        )


# ---------------------------------------------------------------------------
# Stream layer (0i2vt.14 integration: matcher al6e3 + fallback ahygg + attach)
# ---------------------------------------------------------------------------


@dataclass
class _StreamSlot:
    """One archived stream's attach slot, preserving archived order.

    Exactly one slot per archived embedded stream, in archived order, so the
    final channel stream list mirrors the archive. A slot resolves to ONE
    destination stream id:

    * a tier 1-4 MATCH fills :attr:`dest_id` immediately (the existing
      destination stream the matcher resolved — never created, never ledgered);
    * a MISS leaves :attr:`dest_id` ``None`` and records the archived stream as
      ``orphan`` so the batched fallback can synthesize it, after which its
      synthesized id is resolved back into the slot via the shared remap.
    """

    dest_id: int | None = None
    orphan: dict | None = None


@dataclass
class _ChannelStreamPlan:
    """A channel's ordered stream-attach plan.

    Built during the create loop (one per channel that has archived streams),
    resolved after the batched custom-stream fallback runs: each slot's final
    destination id is collected in order and sent in one ``update_channel``.
    """

    dest_channel_id: int
    label: str
    slots: list[_StreamSlot] = field(default_factory=list)


async def _fetch_candidate_streams(client: DispatcharrClient) -> list[dict]:
    """Fetch the destination instance's existing streams (matcher candidates).

    Paginates ``get_streams`` so the matcher sees the full candidate universe.
    A failed/garbled fetch yields an empty candidate list — every archived
    stream then MISSes and routes to the custom-stream fallback (a safe outcome:
    orphans are synthesized, never mis-attached). Never raises.
    """
    candidates: list[dict] = []
    page = 1
    try:
        while True:
            resp = await client.get_streams(page=page, page_size=1000)
            if isinstance(resp, dict):
                results = resp.get("results", [])
            else:
                results = resp
            page_items = [s for s in (results or []) if isinstance(s, dict)]
            candidates.extend(page_items)
            # Stop when a page returns fewer than requested (last page) or the
            # response is not the paginated dict shape (single-shot list).
            if not isinstance(resp, dict) or len(page_items) < 1000:
                break
            page += 1
    except Exception as exc:
        logger.warning(
            "[DBAS-CHANNELS] Could not list destination streams for matching; "
            "every archived stream will route to the custom-stream fallback: %s",
            exc,
        )
        return []
    logger.info(
        "[DBAS-CHANNELS] Fetched %d destination stream candidate(s) for matching.",
        len(candidates),
    )
    return candidates


def _archived_streams(archive_channel: dict) -> list[dict]:
    """The channel's archived embedded stream records, in archived order.

    Returns an empty list when the archive carries no ``streams`` (or a
    non-list) — such a channel attaches nothing and fires no fallback.
    """
    streams = archive_channel.get("streams")
    if not isinstance(streams, list):
        return []
    return [s for s in streams if isinstance(s, dict)]


def _plan_streams(
    plans: list[_ChannelStreamPlan],
    archive_channel: dict,
    dest_channel_id: int,
    candidates: list[dict],
    report: RestoreReport,
    allow_fuzzy: bool = True,
) -> None:
    """Build a channel's ordered stream-attach plan via the 4-tier matcher.

    For each archived stream (in order) run :func:`match_stream`:

    * a HIT records the matched destination id in the slot and counts the attach
      as an ``updated`` STREAM in the report (attached, NOT created/ledgered);
    * a MISS records the archived stream as an orphan slot for the batched
      fallback to synthesize.

    ``allow_fuzzy`` floors the matcher at Tier-3 exact-normalized when ``False``
    (spike ``xp6mp`` ruling 1b — the sync path's collision-safe stream floor). A
    Tier-4 fuzzy hit (only possible when ``allow_fuzzy`` is True) is still
    attached, but is flagged LOW-CONFIDENCE in ``report.notes`` so it is never a
    silent ``updated`` — the operator sees which attaches rest on a fuzzy guess.

    A channel with no archived streams produces no plan (nothing to attach).
    """
    archived = _archived_streams(archive_channel)
    if not archived:
        return

    stream_cat = report.category(EntityType.STREAM)
    label = _channel_label(archive_channel)
    plan = _ChannelStreamPlan(dest_channel_id=dest_channel_id, label=label)
    for archived_stream in archived:
        tier, match_id = match_stream(
            archived_stream, candidates, allow_fuzzy=allow_fuzzy
        )
        if tier != MatchTier.MISS and match_id is not None:
            plan.slots.append(_StreamSlot(dest_id=match_id))
            # Matched an EXISTING destination stream: attached, not created — so
            # it is an ``updated`` STREAM, never ledgered (nothing to roll back).
            stream_cat.updated += 1
            # A Tier-4 fuzzy hit is LOW-CONFIDENCE: surface it (ruling 1b) so it
            # is not a silent ``updated``. The stream name is operator-trusted
            # (no secret) — safe to put in a sanitized note.
            if tier == MatchTier.FUZZY_NORMALIZED_NAME:
                stream_label = archived_stream.get("name") or "<unknown>"
                report.notes.append(
                    "low-confidence stream match (fuzzy): attached stream '%s' on "
                    "channel '%s' via a fuzzy name match — verify it is correct."
                    % (stream_label, label)
                )
        else:
            plan.slots.append(_StreamSlot(orphan=archived_stream))
    plans.append(plan)


async def _attach_streams(
    plans: list[_ChannelStreamPlan],
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
) -> None:
    """Synthesize MISS orphans (batched, once) then attach streams per channel.

    Two passes:

    1. Collect every orphan across ALL channels and hand them to
       :func:`synthesize_custom_streams` in ONE call, threading a single
       :class:`_FallbackState` so the custom-stream account is found-or-created
       at most once. The synthesizer creates/ledgers each orphan and records its
       ``source_export_id -> dest_id`` in the shared remap.
    2. Resolve each slot's destination id (the matched id, or — for a MISS slot —
       the synthesized id looked up from the remap) in archived order, and send
       the channel's full ordered stream list in one ``update_channel`` call.

    A plan list with no orphans skips the fallback entirely (no synthesized
    account, no WARN) — the common happy case where every stream matched.
    """
    if not plans:
        return

    # Pass 1 — batched fallback over EVERY orphan, one threaded state.
    orphans = [
        slot.orphan
        for plan in plans
        for slot in plan.slots
        if slot.orphan is not None
    ]
    if orphans:
        state = _FallbackState()
        await synthesize_custom_streams(
            orphans=orphans,
            client=client,
            remap=remap,
            ledger=ledger,
            report=report,
            state=state,
        )

    # Pass 2 — resolve each slot to a dest id (matched or synthesized) and attach.
    for plan in plans:
        ordered_ids: list[int] = []
        for slot in plan.slots:
            if slot.dest_id is not None:
                ordered_ids.append(slot.dest_id)
                continue
            # MISS slot: resolve the synthesized id the fallback recorded under
            # EntityType.STREAM. A synthesize failure (no id recorded) drops the
            # orphan from the attach list rather than attaching a wrong/None id.
            source_id = slot.orphan.get("id") if slot.orphan else None
            if source_id is None:
                continue
            synthesized = remap.resolve(EntityType.STREAM, int(source_id))
            if synthesized is not None:
                ordered_ids.append(synthesized)

        if not ordered_ids:
            continue
        try:
            await client.update_channel(
                plan.dest_channel_id, {"streams": ordered_ids}
            )
        except Exception as exc:
            stream_cat = report.category(EntityType.STREAM)
            stream_cat.failed += 1
            stream_cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.UPSTREAM_API_ERROR,
                    label=plan.label,
                    message=_sanitize_failure(exc),
                    source_export_id=None,
                )
            )
            logger.warning(
                "[DBAS-CHANNELS] Failed to attach streams to channel '%s' "
                "(dest id %s): %s",
                plan.label,
                plan.dest_channel_id,
                FailureReason.UPSTREAM_API_ERROR.value,
            )


async def _reattach_profiles(
    reattach_queue: list[tuple[dict, int]],
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
) -> None:
    """Reattach each restored channel to its archived channel-profile memberships.

    For each membership, resolve the destination profile id through the
    IdRemapTable (``EntityType.CHANNEL_PROFILE``). An unresolved profile is
    recorded ``DEPENDENCY_UNRESOLVED`` under the CHANNEL_PROFILE category and the
    membership is NOT applied (no guessed id). A reattach upstream error is
    recorded ``UPSTREAM_API_ERROR``.
    """
    prof_cat = report.category(EntityType.CHANNEL_PROFILE)
    for archive_channel, dest_channel_id in reattach_queue:
        label = _channel_label(archive_channel)
        for membership in _profile_memberships(archive_channel):
            source_profile_id = membership.get("profile_id")
            if source_profile_id is None:
                continue
            dest_profile_id = remap.resolve(
                EntityType.CHANNEL_PROFILE, int(source_profile_id)
            )
            if dest_profile_id is None:
                logger.info(
                    "[DBAS-CHANNELS] Channel '%s' profile membership skipped — "
                    "profile (source id %s) not in remap (dependency unresolved).",
                    label,
                    source_profile_id,
                )
                prof_cat.skipped += 1
                prof_cat.skip_details.append(
                    SkipDetail(
                        reason=SkipReason.DEPENDENCY_UNRESOLVED,
                        label=label,
                        source_export_id=int(source_profile_id),
                    )
                )
                continue
            enabled = bool(membership.get("enabled", True))
            try:
                await client.update_profile_channel(
                    dest_profile_id, dest_channel_id, {"enabled": enabled}
                )
            except Exception as exc:
                prof_cat.failed += 1
                prof_cat.failure_details.append(
                    FailureDetail(
                        reason=FailureReason.UPSTREAM_API_ERROR,
                        label=label,
                        message=_sanitize_failure(exc),
                        source_export_id=int(source_profile_id),
                    )
                )
                logger.warning(
                    "[DBAS-CHANNELS] Failed to reattach channel '%s' to profile "
                    "(dest id %s): %s",
                    label,
                    dest_profile_id,
                    exc,
                )


def _profile_memberships(archive_channel: dict) -> list[dict]:
    """Extract the channel's archived profile memberships as a list of dicts.

    Accepts the canonical ``profile_memberships`` list (``{profile_id, enabled}``
    records). Returns an empty list when the archive carries no memberships.
    """
    memberships = archive_channel.get("profile_memberships")
    if not isinstance(memberships, list):
        return []
    return [m for m in memberships if isinstance(m, dict)]


def _skip(
    cat,
    reason: SkipReason,
    label: str,
    source_export_id,
    is_dry_run: bool,
) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(
        SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
    )
