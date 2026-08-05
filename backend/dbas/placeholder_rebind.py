"""Rebind restored channels off URL-less placeholders onto the real streams.

Bead ``enhancedchannelmanager-2o0cz`` (P0). This is the pass that makes a
restored lineup PLAY.

----------------------------------------------------------------------------
WHAT WENT WRONG (drill run 2026-08-04-run1, ECM 0.18.1-0022 / Dispatcharr 0.28.2)
----------------------------------------------------------------------------

At channel-import time the destination has no provider streams yet — the M3U
account was created moments earlier and its refresh is DEFERRED to the end of the
run (it would otherwise race the logo import). So the 4-tier matcher
(:func:`dbas.stream_matcher.match_stream`) MISSES every archived stream, and
:mod:`dbas.custom_stream_fallback` does the only safe thing available to it:
synthesizes each orphan as a URL-less placeholder under one synthetic M3U account
(``ECM Custom Streams (DBAS restore)``) and binds the channel to that.

Nothing ever undid it. The drill measured the consequence exactly: after the
deferred refresh materialized 110 REAL provider streams in under 10 seconds,
every one of the 12 channels was STILL bound to its placeholder. The restore
reported ``success … created 32, failed 0`` for an instance where not one channel
could play, and the runbook's documented recovery (an M3U refresh) provably does
not fix it — it adds the real streams beside the placeholders and rebinds nothing.

The matcher itself is fine: the drill CONFIRMED its ordering behaviour (a
three-stream channel restored in exactly its archived order, on both artifact
variants). The defect is that nothing re-runs it once there is something to match
against.

----------------------------------------------------------------------------
WHAT WENT WRONG NEXT (drill run 2026-08-05-run3, ECM 0.18.1-0024, bead
``enhancedchannelmanager-ixdaw``)
----------------------------------------------------------------------------

The rebind above works — and then broke a channel a different way. A channel
seeded with ``TX | Dallas | PBS KERA``, ``TX | DALLAS | PBS KERA`` and
``TX | Austin | PBS KLRU`` came back holding three placeholders and returning
HTTP 500 with 0 bytes, while the restore still reported ``success, failed 0``.

The first two names differ ONLY in case. The matcher's normalizer folds case, so
Tier 2 (exact normalized name + same provider) is the correct answer for BOTH —
and it is the SAME answer, verified against the live destination:

    'TX | Dallas | PBS KERA' -> tier=2 match_id=101
    'TX | DALLAS | PBS KERA' -> tier=2 match_id=101   <-- same id
    'TX | Austin | PBS KLRU' -> tier=2 match_id=98

This pass wrote both slots and PATCHed ``streams=[101, 101, 98]``. Dispatcharr
rejected the whole update::

    psycopg.errors.UniqueViolation: duplicate key value violates unique
    constraint "unique_channel_stream"
    DETAIL:  Key (channel_id, stream_id)=(12, 101) already exists.

Because the PATCH is all-or-nothing, the handler below reverted the ENTIRE
channel to placeholders — including the KLRU slot that had a perfectly good
unique match. One colliding slot cost every slot.

The matcher is NOT wrong here: it is a PURE function returning the best match for
ONE archived stream, and it has no view of what its siblings claimed. Uniqueness
is a property of the LIST, so the de-dup belongs to the caller that assembles it.
Hence ``claimed_ids`` below: a match landing on an id the channel already holds
is demoted to a MISS, keeping that one slot on its placeholder (counted and named
like any other miss) so the PATCH can never carry a duplicate. Archived ORDER is
untouched — the demoted slot stays exactly where it sat.

----------------------------------------------------------------------------
WHERE THIS RUNS AND WHY
----------------------------------------------------------------------------

This is a RESTORE-COMPLETION step in
:func:`dbas.restore_orchestrator.run_restore`, immediately AFTER the deferred
phase and only on a clean, non-dry-run apply. That placement is forced:

* it cannot run inside the channels importer — the real streams do not exist yet;
* it cannot be left to the operator — the drill proved the documented manual
  recovery (refresh) does not rebind, so "the operator will refresh" is a
  recovery path that does not work;
* it must run after ``apply_deferred_auto_sync`` — that is the call that
  triggers the refresh and polls until the destination stream count stabilizes,
  which is precisely the moment the real streams are queryable.

----------------------------------------------------------------------------
WHAT IT DOES
----------------------------------------------------------------------------

1. Read the placeholder ids this run created from the shared
   :class:`~dbas.restore_contracts.RollbackLedger` (``EntityType.STREAM``). Only
   streams THIS restore synthesized are ever touched — never a pre-existing one.
2. Re-fetch the destination's streams and partition them: the placeholders, and
   the REAL candidates (everything else that carries a URL). A stream with no URL
   is never a rebind target — it is exactly the thing we are rebinding away from.
3. For each restored channel, re-run the SAME 4-tier matcher over the real
   candidates, one slot per archived stream in archived order. A hit takes the
   real id; a miss keeps the placeholder so the channel is never left with fewer
   streams than it had. A hit landing on an id the channel ALREADY holds is
   treated as a miss (see the run-3 section above). The channel is PATCHed only
   when the ordered list changed.
4. Delete every placeholder that is no longer referenced by any channel, then the
   synthetic account if this run created it and nothing is left under it.
5. Report what is left: a channel still holding a placeholder is counted in
   :attr:`~dbas.restore_contracts.RestoreReport.channels_needing_stream_reattach`
   and NAMED in ``stream_reattach_details``. A restore that cannot make a channel
   play says so.

BEST-EFFORT BY CONSTRUCTION: every upstream call here is post-create cleanup on
an otherwise-successful restore. A failure is logged and surfaced as a report
note; it never raises, never fails the restore, and never triggers a rollback of
state the operator would rather keep.

LEDGER: deleted placeholders are deliberately left in the ledger. Compensation
treats a 404 as success (already gone), so a later rollback of an entry this pass
removed is a no-op — cheaper and safer than mutating rollback state from a
success path.

Conventions (``docs/style_guide.md`` / ``backend/CLAUDE.md``): ``snake_case``;
Google-style docstrings; lazy ``%`` logging with a ``[DBAS-REBIND]`` prefix; no
stream URL is ever logged or reported (a provider URL embeds credentials).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from dbas.custom_stream_fallback import CUSTOM_STREAM_ACCOUNT_NAME
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)
from dbas.stream_matcher import MatchTier, match_stream
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Page size for the post-refresh stream re-fetch. Matches the channels importer's
# candidate fetch so both passes see the same shape of response.
_STREAM_PAGE_SIZE = 1000

# Hard bound on the stream/channel pagination walk. A destination with more than
# this is pathological; stopping is better than an unbounded loop in a
# post-restore cleanup pass.
_MAX_PAGES = 200


@dataclass
class RebindResult:
    """What the rebind pass changed.

    Attributes:
        rebound: Placeholder slots swapped for a real provider stream.
        channels_updated: Channels whose ordered stream list was PATCHed.
        placeholders_deleted: Orphaned placeholder streams removed.
        account_deleted: Whether the synthetic custom-stream account was removed.
        still_placeholder: Channels still holding at least one placeholder.
    """

    rebound: int = 0
    channels_updated: int = 0
    placeholders_deleted: int = 0
    account_deleted: bool = False
    still_placeholder: list[str] = field(default_factory=list)


def _as_int(value) -> int | None:
    """Coerce to ``int`` when cleanly one, else ``None`` (``bool`` rejected)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _ledgered_ids(ledger: RollbackLedger, entity_type: EntityType) -> set[int]:
    """Destination ids THIS run created for ``entity_type``."""
    return {
        entry.destination_id
        for entry in ledger.entries
        if entry.entity_type == entity_type
    }


async def _fetch_all(client: DispatcharrClient, fetch, **kwargs) -> list[dict]:
    """Walk a paginated Dispatcharr list endpoint into one flat list.

    Never raises: a failed/garbled fetch yields what was collected so far (an
    empty list on the first page), which makes the rebind a safe no-op rather
    than a crash on a post-restore cleanup path.
    """
    out: list[dict] = []
    page = 1
    while page <= _MAX_PAGES:
        try:
            resp = await fetch(page=page, page_size=_STREAM_PAGE_SIZE, **kwargs)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup pass
            logger.warning("[DBAS-REBIND] Paginated fetch failed on page %d: %s", page, exc)
            return out
        if isinstance(resp, dict):
            results = resp.get("results") or []
        else:
            results = resp or []
        items = [r for r in results if isinstance(r, dict)]
        out.extend(items)
        if not isinstance(resp, dict) or len(items) < _STREAM_PAGE_SIZE:
            break
        page += 1
    return out


def _stream_name(stream: dict) -> str:
    """Operator-facing stream name — never the URL."""
    name = stream.get("name")
    return str(name) if isinstance(name, str) and name else "<unknown>"


async def rebind_placeholder_streams(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    archive_channels: list[dict],
    allow_fuzzy: bool = True,
) -> RebindResult:
    """Re-run the stream matcher against the now-materialized provider streams.

    See the module docstring for why this exists and where it runs. Never raises.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`. Updated with
            ``streams_rebound`` and, for anything still unplayable,
            ``channels_needing_stream_reattach`` + ``stream_reattach_details``.
        ledger: The shared :class:`RollbackLedger` — the ONLY source of which
            streams/accounts this run synthesized. Nothing outside it is touched.
        remap: The shared :class:`IdRemapTable`; ``CHANNEL`` resolves each
            archived channel to its destination id, ``STREAM`` resolves each
            archived stream to the placeholder that was synthesized for it.
        archive_channels: The CHANNEL records from the export archive, carrying
            the embedded ``streams`` the matcher re-runs over.
        allow_fuzzy: Whether the matcher may use its Tier-4 fuzzy rung. Mirrors
            the channels importer's flag so the rebind never matches more loosely
            than the original attach did.

    Returns:
        A :class:`RebindResult` describing what changed.
    """
    result = RebindResult()

    placeholder_ids = _ledgered_ids(ledger, EntityType.STREAM)
    if not placeholder_ids:
        # No orphan was synthesized this run — every archived stream matched a
        # real destination stream first time. Nothing to undo.
        return result

    logger.info(
        "[DBAS-REBIND] Post-refresh rebind: %d placeholder stream(s) created this "
        "run; re-running the stream matcher against the materialized provider streams.",
        len(placeholder_ids),
    )

    all_streams = await _fetch_all(client, client.get_streams)
    # REAL candidates = everything this run did NOT synthesize that carries a
    # URL. A URL-less stream can never be a rebind target: playability is the
    # entire point of this pass.
    candidates = [
        s
        for s in all_streams
        if _as_int(s.get("id")) not in placeholder_ids and s.get("url")
    ]
    if not candidates:
        logger.warning(
            "[DBAS-REBIND] No URL-bearing provider streams on the destination; "
            "every restored channel still depends on its placeholder(s)."
        )

    # Placeholder id -> name, for the operator-facing detail rows.
    placeholder_names = {
        pid: _stream_name(s)
        for s in all_streams
        if (pid := _as_int(s.get("id"))) in placeholder_ids
    }

    # The DESTINATION's own channel -> ordered stream ids. This, not a list
    # reconstructed from the archive, is the authoritative starting point: a
    # channel can hold a mix of placeholders AND streams the importer matched
    # for real, and rebuilding the list from the STREAM remap (which only ever
    # records SYNTHESIZED orphans) would silently drop every real one.
    current_by_channel = {
        cid: [sid for s in (ch.get("streams") or []) if (sid := _as_int(s)) is not None]
        for ch in await _fetch_all(client, client.get_channels)
        if (cid := _as_int(ch.get("id"))) is not None
    }

    # Placeholder destination id -> the ARCHIVED stream record it stands in for,
    # so each placeholder slot is re-matched with the record that produced it.
    source_by_placeholder: dict[int, int] = {
        dest: source
        for source, dest in (remap.mappings.get(EntityType.STREAM) or {}).items()
    }

    still_referenced: set[int] = set()

    for archive_channel in archive_channels or []:
        source_channel_id = _as_int(archive_channel.get("id"))
        if source_channel_id is None:
            continue
        dest_channel_id = remap.resolve(EntityType.CHANNEL, source_channel_id)
        if dest_channel_id is None:
            continue

        archived_by_source = {
            sid: s
            for s in (archive_channel.get("streams") or [])
            if isinstance(s, dict) and (sid := _as_int(s.get("id"))) is not None
        }
        current_ids = current_by_channel.get(dest_channel_id)
        if not current_ids:
            continue

        label = str(archive_channel.get("name") or "<unknown>")
        ordered_ids = list(current_ids)
        rebound_here = 0
        held_placeholders: list[str] = []

        # Destination ids this channel already holds. Dispatcharr enforces a
        # UNIQUE (channel_id, stream_id), so a second slot claiming an id
        # another slot already carries makes the PATCH 500 and costs the WHOLE
        # channel. Seeded with the real streams the importer bound for real —
        # they are just as unique-constrained as the ids this pass claims.
        claimed_ids = {sid for sid in current_ids if sid not in placeholder_ids}

        for index, bound_id in enumerate(current_ids):
            if bound_id not in placeholder_ids:
                # A real stream the importer already matched — never touched.
                continue
            archived_stream = archived_by_source.get(source_by_placeholder.get(bound_id))
            tier, match_id = (
                match_stream(archived_stream, candidates, allow_fuzzy=allow_fuzzy)
                if archived_stream is not None
                else (MatchTier.MISS, None)
            )
            if tier != MatchTier.MISS and match_id is not None and match_id in claimed_ids:
                # Two archived streams normalized to the same name, so the
                # matcher handed both slots the same destination id. Demote this
                # one to a MISS: the slot keeps its placeholder and is reported,
                # which costs one slot instead of the entire channel.
                logger.info(
                    "[DBAS-REBIND] Channel '%s' (id=%s): archived stream '%s' matched "
                    "destination stream id=%s, which another slot already holds; "
                    "keeping its placeholder so the update carries no duplicate.",
                    label, dest_channel_id, _stream_name(archived_stream), match_id,
                )
                tier, match_id = MatchTier.MISS, None
            if tier != MatchTier.MISS and match_id is not None:
                # Rewrite this slot IN PLACE — the drill confirmed the matcher's
                # ordering behaviour is correct, so the archived order the
                # importer established must survive the rebind untouched.
                ordered_ids[index] = match_id
                claimed_ids.add(match_id)
                rebound_here += 1
            else:
                held_placeholders.append(placeholder_names.get(bound_id, "<unknown>"))
                still_referenced.add(bound_id)
                # The placeholder stays in the list, so its id is claimed too.
                claimed_ids.add(bound_id)

        if rebound_here:
            try:
                await client.update_channel(dest_channel_id, {"streams": ordered_ids})
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup pass
                logger.warning(
                    "[DBAS-REBIND] Could not rebind channel '%s' (id=%s): %s",
                    label, dest_channel_id, exc,
                )
                # The channel is unchanged, so EVERY placeholder on it is still
                # live — including the ones this pass had resolved a match for.
                for slot_id in current_ids:
                    if slot_id in placeholder_ids:
                        still_referenced.add(slot_id)
                        held_placeholders.append(
                            placeholder_names.get(slot_id, "<unknown>")
                        )
            else:
                result.rebound += rebound_here
                result.channels_updated += 1
                logger.info(
                    "[DBAS-REBIND] Channel '%s' (id=%s): %d placeholder(s) rebound "
                    "onto real provider streams.",
                    label, dest_channel_id, rebound_here,
                )

        if held_placeholders:
            result.still_placeholder.append(label)
            report.record_stream_reattach_needed(
                name=label,
                channel_id=dest_channel_id,
                placeholder_streams=sorted(set(held_placeholders)),
            )

    report.streams_rebound += result.rebound

    # --- Drop the orphaned placeholders, then the synthetic account. ---------
    orphaned = sorted(placeholder_ids - still_referenced)
    for stream_id in orphaned:
        try:
            await client.delete_stream(stream_id)
        except Exception as exc:  # noqa: BLE001 - 404 == already gone; both fine
            logger.warning(
                "[DBAS-REBIND] Could not delete orphaned placeholder stream id=%s: %s",
                stream_id, exc,
            )
            continue
        result.placeholders_deleted += 1

    if still_referenced:
        report.notes.append(
            "%d channel(s) are still bound to a placeholder stream and will not "
            "play; the synthetic '%s' account was kept so those bindings survive. "
            "Attach a real stream to each named channel."
            % (len(result.still_placeholder), CUSTOM_STREAM_ACCOUNT_NAME)
        )
    else:
        result.account_deleted = await _drop_synthetic_account(client, ledger)

    logger.info(
        "[DBAS-REBIND] Rebind complete: %d slot(s) rebound across %d channel(s); "
        "%d placeholder(s) deleted; %d channel(s) still need a stream.",
        result.rebound,
        result.channels_updated,
        result.placeholders_deleted,
        len(result.still_placeholder),
    )
    return result


async def _drop_synthetic_account(
    client: DispatcharrClient, ledger: RollbackLedger
) -> bool:
    """Delete the synthetic custom-stream account, if THIS run created it.

    Identified by ledger entry + the well-known
    :data:`~dbas.custom_stream_fallback.CUSTOM_STREAM_ACCOUNT_NAME`, so an
    account of the same name left by a PRIOR restore (which this run reused
    rather than created) is never removed — deleting it would cascade its
    streams away from channels this run did not touch.

    Returns ``True`` when an account was deleted.
    """
    for entry in ledger.entries:
        if (
            entry.entity_type == EntityType.M3U_ACCOUNT
            and entry.label == CUSTOM_STREAM_ACCOUNT_NAME
        ):
            try:
                await client.delete_m3u_account(entry.destination_id)
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.warning(
                    "[DBAS-REBIND] Could not delete the synthetic custom-stream "
                    "account id=%s: %s",
                    entry.destination_id, exc,
                )
                return False
            logger.info(
                "[DBAS-REBIND] Deleted the now-empty synthetic custom-stream "
                "account id=%s.",
                entry.destination_id,
            )
            return True
    return False
