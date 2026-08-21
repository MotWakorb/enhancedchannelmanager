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

RUN-4 UPDATE (bead ``…-ixdaw``, drill run 2026-08-05-run4): the matcher now
resolves the case-differing pair ITSELF. Its exact-name tiers prefer a candidate
whose RAW name is byte-identical to the archived name, so
``'TX | DALLAS | PBS KERA'`` and ``'TX | Dallas | PBS KERA'`` land on the two
DISTINCT destination ids that actually exist instead of collapsing onto the lower
one. ``claimed_ids`` is therefore no longer the primary handling for that shape —
it is the BACKSTOP, and it stays exactly as written. It remains the only defence
against a GENUINE collision (two archived streams whose raw names are truly
identical), which no pure per-stream matcher can resolve and which must still
cost one slot rather than the whole channel.

----------------------------------------------------------------------------
WHERE THIS RUNS AND WHY
----------------------------------------------------------------------------

The ARCHIVE-DRIVEN pass (:func:`rebind_placeholder_streams`) is a
RESTORE-COMPLETION step in :func:`dbas.restore_orchestrator.run_restore`,
immediately AFTER the deferred phase and only on a clean, non-dry-run apply.
That placement is forced:

* it cannot run inside the channels importer — the real streams do not exist yet;
* it cannot be left to the operator — the drill proved the documented manual
  recovery (refresh) does not rebind, so "the operator will refresh" is a
  recovery path that does not work;
* it must run after ``apply_deferred_auto_sync`` — that is the call that
  triggers the refresh and polls until the destination stream count stabilizes,
  which is precisely the moment the real streams are queryable.

----------------------------------------------------------------------------
WHY ONE SHOT IS NOT ENOUGH (bead ``…-2o0cz`` residual, drill runs 4, 5 AND 7)
----------------------------------------------------------------------------

The placement above is correct and still insufficient for the artifact most
operators hold. On a STANDARD (redacted) artifact the restored M3U account has
no credential at the moment the deferred phase runs, so its refresh materializes
NOTHING and the pass above has nothing real to match against. Three drill runs
measured the identical three-step recovery::

    1. straight after the restore   14 streams (0 with url)  all placeholder  0/n, HTTP 500
    2. + re-enter the credential
       and refresh                 110 streams (96 with url) STILL all placeholder  0/n, HTTP 500
    3. + re-run the WHOLE restore    96 streams (96 with url) all REAL  4/4, 200, 262144 B

Step 2 is the recovery an operator reaches for FIRST, and it changed nothing
they could see: the 96 real streams materialized and sat BESIDE the placeholders.
Step 3 — re-running an entire restore purely to re-trigger a rebind — is
non-obvious and nothing in the product said to do it.

:func:`rebind_placeholders_after_refresh` makes step 2 sufficient. It is the
SAME pass with a different way of naming what each placeholder slot should
become, and it needs NEITHER the archive, the ledger NOR the id remap — all of
which are gone once the restore ends. The enabling fact is that
:mod:`dbas.custom_stream_fallback` builds each placeholder FROM the orphan
record, carrying the archived stream's own ``name`` verbatim (drill run 6
observed a placeholder named ``DRILL ORPHAN STREAM (source only)`` — the
archived name byte-for-byte). So the placeholder is its own match key: the pass
re-runs :func:`dbas.stream_matcher.match_stream` with the PLACEHOLDER'S OWN
RECORD as the archived stream. Its ``url`` is empty (Tier 1 skipped) and its
``m3u_account`` is the synthetic one (Tier 2 misses), so it resolves on Tier 3 —
the exact-normalized-name rung, which carries the ``…-ixdaw`` RAW-NAME
PREFERENCE, so the case-differing pair still lands on two DISTINCT ids here.

Both entry points share ONE per-channel implementation
(:func:`_rebind_one_channel`) and ONE cleanup pair
(:func:`_sweep_orphaned_placeholders` / :func:`_drop_empty_synthetic_account`).
There is deliberately no parallel implementation to drift: the ``…-ixdaw``
``claimed_ids`` de-dup, the in-place slot rewrite that preserves archived ORDER,
and the all-or-nothing PATCH failure handling are written once and used by both.

WHERE THE REFRESH-DRIVEN PASS IS HOOKED, and what that covers:

* ``routers.m3u._poll_m3u_refresh_completion`` — the completion path of
  ``POST /api/m3u/refresh/{account_id}``. This is the UI "Refresh" button and
  the MCP ``refresh_m3u`` tool, i.e. THE step-2 route the drill measured.
* ``tasks.m3u_refresh.M3URefreshTask`` — once per task run, after every account
  has been refreshed. This is the scheduled sweep and the manual "run task"
  button, so an instance heals on its own cadence even if the operator reached
  materialized streams by a route below.

NOT covered, stated plainly rather than overclaimed: ``POST /api/m3u/refresh``
(refresh-all, and the MCP ``refresh_all_m3u`` tool) returns the instant it has
triggered the upstream refresh and exposes no completion signal to hang the pass
on; direct ``DispatcharrClient.refresh_all_m3u_accounts`` callers
(``stream_prober``); and a refresh performed in Dispatcharr's own UI. An
instance reaching materialized streams only by one of those heals on the next
scheduled ``m3u_refresh`` run, not immediately.

DOUBLE-RUN SAFETY. Two independent guarantees:

* STRUCTURAL — the restore's deferred phase calls
  ``DispatcharrClient.refresh_m3u_account`` DIRECTLY
  (:func:`dbas.importers.m3u_accounts.apply_deferred_auto_sync` step 3), never
  ECM's own ``POST /api/m3u/refresh/{account_id}`` route and never the
  ``m3u_refresh`` task. So a restore cannot trigger either hook, and the
  orchestrator's own call is the only rebind a restore performs.
* EXPLICIT — :data:`_REBIND_LOCK` serializes every entry point. The
  archive-driven pass WAITS for it (it is the authoritative pass and must never
  be skipped); the refresh-driven pass SKIPS when it is already held, because a
  rebind that is already in flight is doing exactly the work it would do. The
  check and the acquire have no ``await`` between them, so in a single event
  loop the pair is atomic. This also covers two concurrent refresh completions.

----------------------------------------------------------------------------
WHAT IT DOES
----------------------------------------------------------------------------

1. Read the placeholder ids this run created from the shared
   :class:`~dbas.restore_contracts.RollbackLedger` (``EntityType.STREAM``). Only
   streams THIS restore synthesized are ever touched — never a pre-existing one.
2. Re-fetch the destination's streams and build TWO sets from them, because
   WRITE AUTHORITY and DETECTION are different questions (bead ``…-kcfru``, and
   see its section below). The REAL candidates — everything this run did NOT
   synthesize whose url CAN SERVE — are the only things a slot may be rebound
   ONTO. The playable ids — EVERY stream whose url can serve, whoever made it —
   are what step 5's verdict is taken against. A stream with no usable address is
   in neither: it is exactly the thing we are rebinding away from. "Can serve" is
   :func:`credential_sentinel.url_can_serve`, NOT truthiness — see "A DEAD
   ADDRESS IS NOT ALWAYS AN ABSENT ONE" below.
3. For each restored channel, re-run the SAME 4-tier matcher over the real
   candidates, one slot per archived stream in archived order. A hit takes the
   real id; a miss keeps the placeholder so the channel is never left with fewer
   streams than it had. A hit landing on an id the channel ALREADY holds is
   treated as a miss (see the run-3 section above). The channel is PATCHed only
   when the ordered list changed.
4. Delete every placeholder that is no longer referenced by any channel, then
   SWEEP the residue an earlier run left behind, then drop the synthetic account
   if nothing is left under it. See "THE RESIDUE" below.
5. Report what is left, in TWO populations (bead ``…-daziw``). A channel holding
   any slot that is NOT a real destination stream with a usable address — OR
   holding no slot at all (bead ``…-15g1j``) — is counted in
   :attr:`~dbas.restore_contracts.RestoreReport.channels_needing_stream_reattach`
   and NAMED in ``stream_reattach_details``. A channel left with NOT ONE stream
   that can serve is additionally counted in
   :attr:`~dbas.restore_contracts.RestoreReport.channels_with_no_playable_stream`
   and flagged ``has_playable_stream=False`` on its detail row — that is the
   population that CANNOT PLAY, and the only one that downgrades the restore
   outcome. The distinction matters because the run-3 de-dup above deliberately
   leaves ONE slot on its placeholder while the channel keeps its real streams:
   that channel plays, and calling it a failure would false-fail the very fix
   that saved it.

----------------------------------------------------------------------------
WHY THE VERDICT IS NOT KEYED ON THIS RUN'S PLACEHOLDERS (bead ``…-oebpv``,
drill run 2026-08-05-run4)
----------------------------------------------------------------------------

Step 5 used to be nested inside "did THIS run's placeholder survive?", so the
whole verdict was skipped for a channel whose bad slots were left by an EARLIER
restore. Run 4 measured it twice: a repeat restore over an already-stranded
``KERA Dallas PBS`` reported ``channels_needing_stream_reattach: 0``,
``channels_with_no_playable_stream: 0`` and an empty ``notes[]`` for a channel
that answered HTTP 500 on playback.

The verdict is now taken for EVERY restored channel, from what it is ACTUALLY
left holding, against ``playable_ids`` — every destination stream whose address
can serve. Who created a bad slot is irrelevant to whether the channel plays. A slot that
streams nothing is named from the destination's own stream list, so a prior run's
placeholder gets its real name rather than ``<unknown>``. A channel whose every
slot streams something is healthy and is not reported at all.

----------------------------------------------------------------------------
AND NOT ON HAVING SLOTS TO REBIND EITHER (bead ``…-15g1j``)
----------------------------------------------------------------------------

The same fusion as ``…-kcfru`` below, one step earlier, and it survived that
fix untouched because it never reached the verdict to be corrected by it. The
per-channel loop opened with an emptiness guard::

    current_ids = current_by_channel.get(dest_channel_id)
    if not current_ids:
        continue

The guard was right about the REBIND — a channel holding no streams has no slot
to re-match, and running the matcher over it could only bind it to streams the
archive never put on it. It was skipping the VERDICT with the work, so a
destination channel holding NOTHING, which self-evidently cannot play, scored 0
in both counters and was never named. Three routes in
:mod:`dbas.importers.channels` reach that state: ``_plan_streams`` produces no
plan for an archive channel carrying no ``streams``, and ``_attach_streams``
either ``continue``s without a PATCH when every synthesize failed or leaves the
channel empty when its ``update_channel`` raises.

Two changes, and each is load-bearing on its own (both were confirmed by
mutation — reverting either alone re-hides the channel):

* only a channel that is not on the destination AT ALL is skipped now
  (``current_ids is None``), which is a different fact and not this pass's to
  report — the channel importer already recorded it, and naming it here would
  put an unopenable channel id in ``stream_reattach_details``. A channel holding
  an EMPTY LIST falls through, and ``_rebind_one_channel`` does nothing with it:
  no slots to walk means ``rebound`` stays 0 and it returns above its PATCH.
* the verdict's trigger adds ``undelivered``, because "some slot cannot play" is
  VACUOUSLY FALSE for a channel with no slots. Stated as the invariant it was
  always meant to be: a channel is reported when it holds a slot that streams
  nothing OR when the archive carried streams for it and it holds none. Zero
  streams is one example of the second, not its definition.

WHAT THE EMPTY CASE IS QUALIFIED ON, AND WHY IT IS NOT "EVERY EMPTY CHANNEL".
This counter means "the restore did not deliver a channel that plays", so
something has to have been UNDELIVERED — hence ``archived_by_source``. A channel
the ARCHIVE carries no streams for was never going to arrive with any: the
replica is FAITHFUL, not stranded, and the note's instruction ("attach a real
stream to each channel named") would be telling the operator to make the replica
DIVERGE from its source, on every unattended cycle, forever. That is the same
crying-wolf shape ``…-kcfru`` was filed to stop, arrived at from the other side.
It is also not hypothetical: the cross-instance sync round-trip harness seeds
exactly that channel (``{"name": "CNN", "channel_number": 5, "streams": []}``),
and counting it unconditionally turned all ten of that suite's keystone
scenarios from ``success`` into ``completed_with_failures`` for a replication
that had lost nothing. ``tests/dbas/test_restore_unplayable_channels.py`` pins
both halves — the undelivered channel IS reported, the faithful one is NOT.

----------------------------------------------------------------------------
DETECTION AND WRITE AUTHORITY ARE DIFFERENT SETS (bead ``…-kcfru``, xdmru
two-instance validation 2026-08-20)
----------------------------------------------------------------------------

The section above is the right principle; step 2 quietly broke it in the other
direction, because ONE set was doing TWO jobs. ``candidates`` is the match-TARGET
set — it excludes this run's ledgered placeholder ids so a slot can never be
rebound onto a placeholder — and the playability verdict was read straight off
it. So "this run created it" silently meant "it cannot play".

That is only invisible while every synthesized placeholder is URL-less, which is
the REDACTED artifact. :mod:`dbas.custom_stream_fallback` builds each placeholder
from the orphan record and forwards its ``url`` (it drops only ``id`` / ``pk`` /
``m3u_account``), so a FULL-fidelity archive — which is what CROSS-INSTANCE SYNC
carries — synthesizes URL-BEARING streams that stream exactly what the source
streams. Sync target ``XDMRU B`` measured the consequence on five consecutive
cycles::

    run  9 (01:17:53)  created 18  channels_with_no_playable_stream: 6
    run 11 (01:19:56)  created  0  channels_with_no_playable_stream: 0
    run 13, 21, 22     created  0  channels_with_no_playable_stream: 0

B's six channels held the SAME six stream ids under the synthetic account for all
five, each carrying A's own provider URL. Playback decided which reading was
honest, by fetching rather than inferring::

    A /proxy/ts/stream/1e946091-…  ->  HTTP 200, 188 bytes
    B /proxy/ts/stream/bb66da76-…  ->  HTTP 200, 188 bytes

B plays. The creating cycle was crying wolf, and every cycle after it was right
by accident — the same stream was judged twice and got opposite answers purely on
whether the current run's ledger happened to hold it.

The two concerns are now two sets, and the split is the fix:

* ``candidates`` — WRITE AUTHORITY. Unchanged, and still ledger-scoped: only a
  stream this run did NOT synthesize, whose address can serve, may be rebound
  onto, and only ledgered ids are ever rebound away from or deleted. Widening
  THIS is the obvious wrong fix — it would let a run mutate streams it did not
  create. (Bead ``…-1td94`` NARROWED it, which is the safe direction: a stream
  with a redacted address is no longer a rebind TARGET.)
* ``playable_ids`` — DETECTION. Read-only and UNSCOPED: every destination stream
  whose address can serve, whoever made it. Nothing is written from it.

The invariant it buys, which a single-apply test cannot check and which is why
the regression tests run the pass TWICE: the verdict is a function of the
DESTINATION'S STATE alone. Two cycles over an unchanged destination return the
same answer. A channel that streams nothing downgrades EVERY cycle, not just the
one that stranded it — an unattended schedule must not go green over a replica
that has stopped playing.

----------------------------------------------------------------------------
A DEAD ADDRESS IS NOT ALWAYS AN ABSENT ONE (bead ``…-1td94``, measured live on
Dispatcharr 0.29.0, 2026-08-20)
----------------------------------------------------------------------------

Everything above says "carries a URL" and means "can be streamed". Those were the
same sentence for as long as the only way a placeholder could fail to play was
having no ``url`` at all. Bead ``…-msqf7`` ended that. An Xtream Codes stream URL
authenticates by PATH SEGMENT, so redacting the credential without discarding the
address — which is deliberate, so the operator can still see which provider and
which stream it was — produces::

    http://provider:9191/live/***REDACTED***/***REDACTED***/53.ts

That is a non-empty string, so ``s.get("url")`` said PLAYABLE. Fetching said
HTTP 404. Read from B's Postgres and by fetching, never from the run's report::

    53 of B's channels bound to streams whose upstream returns HTTP 404
    channels_with_no_playable_stream: 0

So the report called a replica entirely healthy while 90 percent of it could not
play — the same class of silence ``…-kcfru`` and ``…-oebpv`` each closed by a
different route, reached this time through the PREDICATE rather than through
provenance or control flow.

THE FIX IS THE PREDICATE, NOT A SENTINEL TEST PER SITE. Every question of the
form "does this stream have a usable address?" in this module now goes through
:func:`credential_sentinel.url_can_serve`. Five sites ask it — the two candidate
sets, ``playable_ids``, the post-refresh placeholder gate and the residue sweep —
and the point of routing them through ONE named function is that the next reason
an address is dead is added once, there, instead of being missed at four of five
call sites. The invariant is "a channel bound to a stream that cannot serve is
reported unplayable, WHATEVER the reason the address is dead"; the redacted URL
is one example of it, not the specification.

NOT A SUBSTITUTE FOR FETCHING, and the counter must not be read as one. This is
the narrower question ECM can answer offline: is this an address at all, or is it
ECM's own record that there is not one? An address that is well-formed and simply
revoked still reads as playable here and always will.

WHAT DID NOT CHANGE: the redaction. ``…-msqf7`` closed a real credential leak
whose own live proof showed 53 provider passwords reaching B, and the trade it
made — a safe stream that cannot play, over a playable stream that ships the
password — stands. What was missing was the run SAYING so.

----------------------------------------------------------------------------
THE RESIDUE (bead ``…-dgnms``, drill runs 2026-08-05-run4 AND run5)
----------------------------------------------------------------------------

Step 4 above deleted only the placeholders in THIS run's ``RollbackLedger``, and
dropped the synthetic account only when THIS run had created it. Both conditions
are about provenance, and provenance turns out to be the wrong question for
cleanup. Two measured consequences, neither an outage — every channel played —
but both accumulating on every redacted restore cycle:

* **14 URL-less placeholder streams bound to NO channel.** They were created by
  an EARLIER restore and superseded when a later one rebound their channels onto
  real streams. Nothing referenced them again, and nothing this run owned
  referenced them either, so nothing ever deleted them.
* **The synthetic account survived while empty.** Run 5 rebound and deleted every
  placeholder (``14 placeholder(s) deleted``, ``0 channel(s) still hold a slot
  that cannot play``) and the M3U account list still showed
  ``ECM Custom Streams (DBAS restore)`` — this run had REUSED the account rather
  than created it, so the ledger held no entry and the drop no-oped.

Both are fixed by asking the state question instead of the provenance question,
and BY KEEPING every safety condition that is about state:

* :func:`_sweep_orphaned_placeholders` deletes a stream only when it is on the
  synthetic account AND carries no url AND no channel references it. An
  operator's own URL-less stream on a DIFFERENT account is untouched; so is any
  placeholder a channel still holds.
* :func:`_drop_empty_synthetic_account` deletes the account only when NO stream
  remains under it, verified against the destination's own stream list. The
  "never delete an account that still has streams" property — the one that stops
  a cascade from cutting channels this run never touched — is unchanged.

The ledger-scoped guarantee that governs REBINDING is likewise unchanged: only
streams this run synthesized are ever rebound, and only they are deleted by the
ledger-owned pass. The sweep is a strictly additional, strictly narrower pass.

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

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from credential_sentinel import url_can_serve
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

# Serializes every rebind entry point (see the module docstring's DOUBLE-RUN
# SAFETY section). Constructed at import time, which is safe on 3.10+ — an
# ``asyncio.Lock`` no longer binds an event loop until it is first awaited.
_REBIND_LOCK = asyncio.Lock()


@dataclass
class RebindResult:
    """What the rebind pass changed.

    Attributes:
        rebound: Placeholder slots swapped for a real provider stream.
        channels_updated: Channels whose ordered stream list was PATCHed.
        placeholders_deleted: Orphaned placeholder streams THIS RUN created that
            were removed.
        orphans_swept: Orphaned placeholder streams an EARLIER run left behind
            that were removed (bead ``…-dgnms``). Counted apart from
            ``placeholders_deleted`` so the drill log keeps the two populations
            legible: the first is this run cleaning up after itself, the second
            is it cleaning up after its predecessors.
        account_deleted: Whether the synthetic custom-stream account was removed.
        still_placeholder: Channels left holding at least one slot that is not a
            real destination stream whose address can serve — whoever created
            it (``…-oebpv``).
        unplayable: Channels left with NO stream that can serve — the SUBSET
            of ``still_placeholder`` that cannot play (bead ``…-daziw``).
    """

    rebound: int = 0
    channels_updated: int = 0
    placeholders_deleted: int = 0
    orphans_swept: int = 0
    account_deleted: bool = False
    still_placeholder: list[str] = field(default_factory=list)
    unplayable: list[str] = field(default_factory=list)


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


def _stream_account_id(stream: dict) -> int | None:
    """The M3U account a destination stream belongs to, or ``None``.

    Dispatcharr serializes ``m3u_account`` as the account's integer pk; a nested
    object is accepted defensively so a serializer change cannot silently turn
    the residue sweep's account test into "no match" (which would leave residue,
    not delete the wrong thing — but a silent no-op is still worth not having).
    """
    raw = stream.get("m3u_account")
    if isinstance(raw, dict):
        raw = raw.get("id")
    return _as_int(raw)


async def _synthetic_account_ids(
    client: DispatcharrClient, ledger: RollbackLedger | None = None
) -> set[int]:
    """Every destination id the synthetic custom-stream account is known under.

    The union of two sources, because either alone is incomplete:

    * the LIVE account list, matched on the well-known
      :data:`~dbas.custom_stream_fallback.CUSTOM_STREAM_ACCOUNT_NAME` — this is
      what finds an account an EARLIER restore created and this one merely
      REUSED (:func:`dbas.custom_stream_fallback._ensure_custom_stream_account`
      find-or-creates on exactly that name);
    * this run's LEDGER — the account this run created is guaranteed to be in it
      and is the historical identification path, kept so the sweep still works
      if the account list read fails or the account was renamed after creation.

    ``ledger`` is ``None`` on the refresh-driven entry point, which runs long
    after the restore that created the account ended and has no ledger to read;
    the live account list is the whole of its answer there.

    Never raises: a failed or garbled account list degrades to the ledger alone,
    which is exactly the pre-existing behaviour.
    """
    ids = {
        entry.destination_id
        for entry in (ledger.entries if ledger is not None else [])
        if entry.entity_type == EntityType.M3U_ACCOUNT
        and entry.label == CUSTOM_STREAM_ACCOUNT_NAME
    }
    try:
        accounts = await client.get_m3u_accounts()
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup pass
        logger.warning(
            "[DBAS-REBIND] Could not list M3U accounts; the residue sweep will "
            "consider only the account this run created: %s", exc,
        )
        return ids
    for account in accounts or []:
        if not isinstance(account, dict):
            continue
        if account.get("name") != CUSTOM_STREAM_ACCOUNT_NAME:
            continue
        account_id = _as_int(account.get("id"))
        if account_id is not None:
            ids.add(account_id)
    return ids


@dataclass
class _ChannelRebind:
    """What :func:`_rebind_one_channel` left ONE channel holding.

    Attributes:
        final_ids: The ordered stream ids the channel is ACTUALLY left with —
            the rewritten list on a successful PATCH, the untouched original
            when the PATCH failed or nothing changed. The playability verdict
            must be taken from this and never from the list the pass HOPED to
            write.
        rebound: Placeholder slots swapped for a real provider stream.
        updated: Whether an ``update_channel`` PATCH actually succeeded.
        retained_placeholders: Placeholder ids the channel still references, so
            the caller knows not to delete them.
    """

    final_ids: list[int] = field(default_factory=list)
    rebound: int = 0
    updated: bool = False
    retained_placeholders: set[int] = field(default_factory=set)


async def _rebind_one_channel(
    *,
    client: DispatcharrClient,
    dest_channel_id: int,
    label: str,
    current_ids: list[int],
    placeholder_ids: set[int],
    archived_for: Callable[[int], Mapping | None],
    candidates: list[dict],
    allow_fuzzy: bool,
) -> _ChannelRebind:
    """Re-match ONE channel's placeholder slots and PATCH it if anything changed.

    THE SHARED CORE of both entry points. Every guarantee runs 3–7 verified lives
    here and nowhere else, so neither entry point can drift from the other:

    * ORDER — a hit rewrites its slot IN PLACE (``ordered_ids[index] = match_id``).
      The archived order the importer established is never rebuilt, sorted or
      re-derived, so it survives the rebind untouched.
    * DE-DUP (``…-ixdaw`` backstop) — ``claimed_ids`` is seeded with the real
      streams the channel already holds and grows with every id this pass claims.
      A match landing on an id the channel already carries is demoted to a MISS,
      keeping that ONE slot on its placeholder. Dispatcharr enforces a UNIQUE
      ``(channel_id, stream_id)``, and the PATCH is all-or-nothing, so without
      this one colliding slot costs the WHOLE channel.
    * BEST-EFFORT — a failed PATCH is logged, the channel is reported as holding
      its ORIGINAL list, and every placeholder on it is marked retained
      (including ones a match had been found for — the channel did not change).
      Nothing raises.

    The two entry points differ ONLY in ``archived_for``: the restore resolves
    each placeholder back to the archive record that produced it (via the id
    remap), while the refresh-driven pass hands back the PLACEHOLDER'S OWN
    record, which carries the archived name verbatim.

    Args:
        client: The Dispatcharr API client.
        dest_channel_id: The destination channel to rebind.
        label: Operator-facing channel name, for logs.
        current_ids: The ordered stream ids the channel holds right now.
        placeholder_ids: The ids this pass is allowed to rebind away from.
            Anything outside it is a real stream and is never touched.
        archived_for: ``placeholder_id -> the stream record to re-match with``,
            or ``None`` when there is none (which yields a MISS).
        candidates: The REAL destination streams — address usable — to match
            against.
        allow_fuzzy: Whether the matcher may use its Tier-4 fuzzy rung.

    Returns:
        A :class:`_ChannelRebind` describing what the channel is left holding.
    """
    ordered_ids = list(current_ids)
    outcome = _ChannelRebind(final_ids=ordered_ids)

    # Destination ids this channel already holds. Dispatcharr enforces a
    # UNIQUE (channel_id, stream_id), so a second slot claiming an id another
    # slot already carries makes the PATCH 500 and costs the WHOLE channel.
    # Seeded with the real streams the importer bound for real — they are just
    # as unique-constrained as the ids this pass claims.
    claimed_ids = {sid for sid in current_ids if sid not in placeholder_ids}

    for index, bound_id in enumerate(current_ids):
        if bound_id not in placeholder_ids:
            # A real stream the importer already matched — never touched.
            continue
        archived_stream = archived_for(bound_id)
        tier, match_id = (
            match_stream(archived_stream, candidates, allow_fuzzy=allow_fuzzy)
            if archived_stream is not None
            else (MatchTier.MISS, None)
        )
        if tier != MatchTier.MISS and match_id is not None and match_id in claimed_ids:
            # Two archived streams normalized to the same name, so the matcher
            # handed both slots the same destination id. Demote this one to a
            # MISS: the slot keeps its placeholder and is reported, which costs
            # one slot instead of the entire channel.
            logger.info(
                "[DBAS-REBIND] Channel '%s' (id=%s): archived stream '%s' matched "
                "destination stream id=%s, which another slot already holds; "
                "keeping its placeholder so the update carries no duplicate.",
                label, dest_channel_id, _stream_name(archived_stream), match_id,
            )
            tier, match_id = MatchTier.MISS, None
        if tier != MatchTier.MISS and match_id is not None:
            # Rewrite this slot IN PLACE — the drill confirmed the matcher's
            # ordering behaviour is correct, so the archived order the importer
            # established must survive the rebind untouched.
            ordered_ids[index] = match_id
            claimed_ids.add(match_id)
            outcome.rebound += 1
        else:
            outcome.retained_placeholders.add(bound_id)
            # The placeholder stays in the list, so its id is claimed too.
            claimed_ids.add(bound_id)

    if not outcome.rebound:
        return outcome

    try:
        await client.update_channel(dest_channel_id, {"streams": ordered_ids})
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup pass
        logger.warning(
            "[DBAS-REBIND] Could not rebind channel '%s' (id=%s): %s",
            label, dest_channel_id, exc,
        )
        outcome.final_ids = list(current_ids)
        outcome.rebound = 0
        # The channel is unchanged, so EVERY placeholder on it is still live —
        # including the ones this pass had resolved a match for.
        outcome.retained_placeholders = {
            slot_id for slot_id in current_ids if slot_id in placeholder_ids
        }
        return outcome

    outcome.updated = True
    logger.info(
        "[DBAS-REBIND] Channel '%s' (id=%s): %d placeholder(s) rebound onto real "
        "provider streams.",
        label, dest_channel_id, outcome.rebound,
    )
    return outcome


async def rebind_placeholder_streams(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    archive_channels: list[dict],
    allow_fuzzy: bool,
) -> RebindResult:
    """Re-run the stream matcher against the now-materialized provider streams.

    See the module docstring for why this exists and where it runs. Never raises.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`. Updated with
            ``streams_rebound`` and, for any restored channel left holding a slot
            that is not a real stream with a usable address,
            ``channels_needing_stream_reattach`` + ``stream_reattach_details``,
            plus ``channels_with_no_playable_stream`` for the subset left with
            no stream that can serve at all.
        ledger: The shared :class:`RollbackLedger` — the ONLY source of which
            streams/accounts this run synthesized. Nothing outside it is
            rebound or deleted; the playability verdict, by contrast, covers
            every restored channel regardless of the ledger (bead ``…-oebpv``).
        remap: The shared :class:`IdRemapTable`; ``CHANNEL`` resolves each
            archived channel to its destination id, ``STREAM`` resolves each
            archived stream to the placeholder that was synthesized for it.
        archive_channels: The CHANNEL records from the export archive, carrying
            the embedded ``streams`` the matcher re-runs over.
        allow_fuzzy: Whether the matcher may use its Tier-4 fuzzy rung. Mirrors
            the channels importer's flag so the rebind never matches more loosely
            than the original attach did. **REQUIRED — deliberately no default**
            (bead ``…-efvyg``). It used to default to ``True``, and
            ``restore_orchestrator._rebind_placeholders`` simply never passed it:
            a sync target with ``fuzzy_stream_matching`` OFF had its floor
            honoured by the importer and silently discarded here, so the rebind
            re-ran the FULL ladder and bound the replica's ``XDMRU News One`` to
            the destination stream ``XDMRU News Two`` while the cycle reported
            SUCCESS. An omission that yields a WRONG binding cannot be allowed to
            look like a normal call, so there is nothing to omit: every caller
            states its policy or gets a ``TypeError``. The BEHAVIOUR is pinned by
            ``tests/tasks/test_sync_fuzzy_flag_reaches_rebind.py``; the
            required-ness itself is structural and no test enforces it
            (mutation-tested — putting the ``= True`` default back kills nothing,
            because every caller now passes the argument), so do not restore a
            default on the assumption the suite would catch it.

    Returns:
        A :class:`RebindResult` describing what changed.
    """
    # Serialized against the refresh-driven entry point (module docstring,
    # DOUBLE-RUN SAFETY). This pass WAITS rather than skipping: it is the
    # authoritative pass, it owns the restore's report, and a restore that
    # silently skipped its rebind would be the run-1 defect all over again.
    async with _REBIND_LOCK:
        return await _rebind_from_archive(
            client=client,
            report=report,
            ledger=ledger,
            remap=remap,
            archive_channels=archive_channels,
            allow_fuzzy=allow_fuzzy,
        )


async def _rebind_from_archive(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    archive_channels: list[dict],
    allow_fuzzy: bool,
) -> RebindResult:
    """The archive-driven rebind body — see :func:`rebind_placeholder_streams`.

    Split out only so the public entry point can hold :data:`_REBIND_LOCK`
    around the whole pass without indenting it.
    """
    result = RebindResult()

    # Only streams THIS run synthesized are ever rebound or deleted. The pass
    # runs even when the set is EMPTY: the playability verdict below covers every
    # restored channel, including one stranded on an EARLIER run's placeholders,
    # and returning early here is exactly what made those channels invisible
    # (bead …-oebpv).
    placeholder_ids = _ledgered_ids(ledger, EntityType.STREAM)

    logger.info(
        "[DBAS-REBIND] Post-refresh rebind: %d placeholder stream(s) created this "
        "run; re-running the stream matcher against the materialized provider "
        "streams and auditing every restored channel for playability.",
        len(placeholder_ids),
    )

    all_streams = await _fetch_all(client, client.get_streams)
    # WRITE AUTHORITY — the match-TARGET set. Everything this run did NOT
    # synthesize whose address CAN SERVE. Both conditions earn their place: a
    # stream with no usable address can never be a rebind target (playability is
    # the entire point of this pass), and this run's own placeholders are excluded
    # so no slot is ever rebound onto one. STAYS SCOPED — bead ``…-1td94``
    # narrowed the url half from truthiness to ``url_can_serve``, which subtracts
    # rebind targets and never adds any.
    candidates = [
        s
        for s in all_streams
        if _as_int(s.get("id")) not in placeholder_ids and url_can_serve(s.get("url"))
    ]
    if not candidates:
        logger.warning(
            "[DBAS-REBIND] No provider stream with a usable address this run "
            "could rebind onto; every slot keeps whatever it is already bound to."
        )
    # DETECTION — the playability test (beads …-daziw / …-kcfru / …-1td94), and a
    # SEPARATE set from the one above on purpose. A channel PLAYS if any id it is
    # left holding carries an address that CAN SERVE. WHO created that stream is
    # irrelevant to whether it streams anything, so this set is UNSCOPED: every
    # destination stream with a usable address is in it, including the
    # placeholders this run just synthesized.
    #
    # It used to be read off ``candidates``, which made the verdict depend on
    # provenance — one set doing two jobs. ``custom_stream_fallback`` builds each
    # placeholder from the orphan record and forwards its ``url``, so a
    # FULL-fidelity archive synthesizes URL-BEARING streams that play exactly
    # what the source plays. The xdmru two-instance validation measured the
    # consequence on an unattended schedule: cycle 1 reported
    # ``channels_with_no_playable_stream: 6``, every later cycle reported ``0``,
    # and NOTHING about the destination changed between them — the six streams
    # simply stopped being in the current run's ledger. Both readings cannot be
    # right; fetching decided it (A and B answered HTTP 200 / 188 bytes on the
    # same channel), so the creating cycle was the one crying wolf.
    #
    # A REDACTED archive strips the url, so those placeholders stream nothing and
    # are absent from this set on EVERY cycle — the downgrade the operator is
    # paged on is unchanged, and now holds in steady state too.
    #
    # ``url_can_serve``, not truthiness: a CREDENTIAL-redacted url (bead
    # ``…-msqf7``) is a non-empty string that fetches 404, and reading it as
    # playable put 53 of B's 59 channels in this set while none of them could
    # play. See "A DEAD ADDRESS IS NOT ALWAYS AN ABSENT ONE" above.
    playable_ids = {
        sid
        for s in all_streams
        if url_can_serve(s.get("url")) and (sid := _as_int(s.get("id"))) is not None
    }

    # EVERY destination stream id -> its operator-facing name, for the detail
    # rows. Keyed over all_streams rather than only this run's placeholders so a
    # slot left behind by an EARLIER restore is named too (bead …-oebpv) instead
    # of surfacing to the operator as "<unknown>".
    stream_names = {
        sid: _stream_name(s)
        for s in all_streams
        if (sid := _as_int(s.get("id"))) is not None
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
    # Destination channel id -> the ordered stream ids it is left holding once
    # this pass is done with it. Seeded per channel below; a channel this pass
    # does not touch is read from ``current_by_channel`` instead.
    final_by_channel: dict[int, list[int]] = {}

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
        # NO CHANNEL is not the same as NO STREAMS (bead ``…-15g1j``). ``None``
        # means the remapped id is not on the destination at all — the channel
        # importer already recorded that as its own failure, and naming it here
        # would put a channel id in ``stream_reattach_details`` that the operator
        # cannot open. An EMPTY LIST is a real channel holding nothing, and it
        # falls through: see below.
        if current_ids is None:
            continue

        label = str(archive_channel.get("name") or "<unknown>")

        # THE SHARED CORE — order preservation, the ``…-ixdaw`` de-dup backstop
        # and the all-or-nothing PATCH failure handling live in ONE place used by
        # both entry points. The restore path's only contribution is
        # ``archived_for``: the placeholder resolves back through the STREAM
        # remap to the archive record that produced it.
        #
        # A channel holding NOTHING reaches this call rather than being skipped
        # before it (bead ``…-15g1j``), and the core does nothing with it: the
        # slot loop has no slots to walk, so ``rebound`` stays 0 and it returns
        # ABOVE the ``update_channel``. The old guard's real content — no rebind
        # work on an empty holding — is therefore owned here, by the one function
        # that owns every other rebind guarantee, and is not worth a second copy
        # in this caller that could drift from it. What the guard ALSO did, and
        # had no business doing, was skip the VERDICT below: the same fusion of
        # two concerns in one control-flow decision that ``…-kcfru`` untangled
        # one step later.
        outcome = await _rebind_one_channel(
            client=client,
            dest_channel_id=dest_channel_id,
            label=label,
            current_ids=current_ids,
            placeholder_ids=placeholder_ids,
            archived_for=lambda pid, _archived=archived_by_source: _archived.get(
                source_by_placeholder.get(pid)
            ),
            candidates=candidates,
            allow_fuzzy=allow_fuzzy,
        )
        still_referenced.update(outcome.retained_placeholders)
        # What the channel is ACTUALLY left holding — the list the playability
        # verdict must be taken from. The PATCH can fail, and the handler reverts
        # every slot, so the list this pass HOPED to write is not evidence of
        # anything.
        final_ids = outcome.final_ids
        if outcome.updated:
            result.rebound += outcome.rebound
            result.channels_updated += 1

        # What this channel ENDS UP holding, so the residue sweep below can ask
        # "is this stream referenced by anything" against the post-rebind truth
        # rather than the pre-rebind list it fetched (bead …-dgnms). A channel
        # this loop never reached keeps its fetched list, which is still current.
        final_by_channel[dest_channel_id] = list(final_ids)

        # THE VERDICT (beads …-daziw / …-oebpv / …-kcfru). Taken from
        # ``final_ids`` — what the channel is ACTUALLY left holding — and keyed
        # on ``playable_ids``, EVERY destination stream whose address can
        # serve. Provenance
        # does not appear on either side of this test, in either direction: a
        # channel stranded by an EARLIER restore used to escape the verdict
        # entirely while returning HTTP 500 (…-oebpv), and a channel on a
        # URL-BEARING stream THIS run synthesized used to be condemned by it
        # while playing perfectly (…-kcfru). A channel whose every slot streams
        # something is healthy and is not reported at all.
        #
        # ``not has_playable`` is on the trigger, not only inside it, because
        # "some slot cannot play" is VACUOUSLY FALSE for a channel holding no
        # slots (bead ``…-15g1j``) — the second half of that defect, and the half
        # that survives letting an empty channel reach this code. Stated as the
        # invariant instead: a channel is reported when it holds a slot that
        # streams nothing OR when nothing it holds can play. Zero streams is one
        # example of the latter, not its definition.
        #
        # ``archived_by_source`` qualifies the empty case, and ONLY the empty
        # case: this counter means "the restore did not deliver a channel that
        # plays", so it needs something to have been UNDELIVERED. A channel the
        # archive carries no streams for was never going to arrive with any — the
        # replica is FAITHFUL, not stranded, and the report's own instruction
        # ("attach a real stream to each channel named") would be telling the
        # operator to make B diverge from A. Measured on the cross-instance sync
        # round-trip harness, whose source seeds exactly that shape
        # (``{"name": "CNN", "channel_number": 5, "streams": []}``): counting it
        # turned all ten keystone scenarios from ``success`` into
        # ``completed_with_failures`` for a replication that had lost nothing.
        # Every slot the archive DID carry and the destination does not have is
        # still counted — that is the undelivered-streams shape the bead is
        # about, and ``importers/channels._attach_streams`` reaches it two ways
        # (an all-synthesize failure ``continue``s without a PATCH, and its
        # ``update_channel`` can raise).
        non_playable_ids = [sid for sid in final_ids if sid not in playable_ids]
        has_playable = any(slot_id in playable_ids for slot_id in final_ids)
        undelivered = not final_ids and bool(archived_by_source)
        if non_playable_ids or undelivered:
            result.still_placeholder.append(label)
            if not has_playable:
                result.unplayable.append(label)
                logger.warning(
                    "[DBAS-REBIND] Channel '%s' (id=%s) has NO playable stream: "
                    "it holds %d slot(s), not one of them carrying an address "
                    "that can serve. Attach a real stream.",
                    label, dest_channel_id, len(final_ids),
                )
            report.record_stream_reattach_needed(
                name=label,
                channel_id=dest_channel_id,
                placeholder_streams=sorted(
                    {stream_names.get(sid, "<unknown>") for sid in non_playable_ids}
                ),
                has_playable_stream=has_playable,
            )

    report.streams_rebound += result.rebound

    # --- Drop the orphaned placeholders, then the synthetic account. ---------
    orphaned = sorted(placeholder_ids - still_referenced)
    deleted_ids: set[int] = set()
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
        deleted_ids.add(stream_id)

    # Everything any channel is left referencing, post-rebind. Built from the
    # per-channel final lists this pass just computed, falling back to the
    # fetched list for a channel it never touched.
    referenced_ids: set[int] = set()
    for channel_id, current in current_by_channel.items():
        referenced_ids.update(final_by_channel.get(channel_id, current))

    synthetic_account_ids = await _synthetic_account_ids(client, ledger)
    swept_ids = await _sweep_orphaned_placeholders(
        client=client,
        all_streams=all_streams,
        referenced_ids=referenced_ids,
        already_deleted=deleted_ids,
        synthetic_account_ids=synthetic_account_ids,
    )
    result.orphans_swept = len(swept_ids)

    if result.still_placeholder:
        # Say which of the two populations each number is: the drill's own report
        # claimed every still-placeholder channel "will not play", which was not
        # true of the ones that kept their real streams (bead …-daziw). The
        # sentence counts the WIDENED population — every channel left holding a
        # slot that streams nothing, not only the ones this run stranded.
        # Say WHERE the names are. The note used to end "attach a real stream to
        # each named channel", which reads as a promise the surrounding text does
        # not keep: an operator reading `details.restore_report` (drill run
        # 2026-08-08-run17) found the counters and this sentence and concluded the
        # names existed only in the container log. They are in the report — the
        # same recorder writes both — so the note points at the field.
        # "still bound to a stream that cannot play" was true of every member of
        # this population until ``…-15g1j`` added the channel bound to NO stream
        # at all, which is not bound to anything. The clause now describes what
        # the population has in common — it needs a real stream attached, which
        # is also what ``channels_needing_stream_reattach`` counts — so it stays
        # true of all three shapes: a leftover placeholder beside real streams, a
        # channel on nothing but placeholders, and a channel holding nothing.
        note = (
            "%d channel(s) need a real stream attached, %d of which have NO "
            "playable stream at all and cannot play. Attach a real stream to "
            "each channel named in stream_reattach_details."
            % (len(result.still_placeholder), len(result.unplayable))
        )
        if still_referenced:
            note += (
                " The synthetic '%s' account was kept so those bindings survive."
                % CUSTOM_STREAM_ACCOUNT_NAME
            )
        report.notes.append(note)

    # The synthetic account goes when it is EMPTY — no stream is left under it —
    # regardless of which run created it (bead …-dgnms). The emptiness test is
    # what protects the operator, and it is unchanged; only the "who created it"
    # condition is gone. The old ``if not still_referenced`` gate is subsumed:
    # a placeholder this run must keep IS a stream under the account, so the
    # account is not empty and is not dropped.
    result.account_deleted = await _drop_empty_synthetic_account(
        client=client,
        all_streams=all_streams,
        deleted_ids=deleted_ids | swept_ids,
        synthetic_account_ids=synthetic_account_ids,
    )

    logger.info(
        "[DBAS-REBIND] Rebind complete: %d slot(s) rebound across %d channel(s); "
        "%d placeholder(s) deleted; %d channel(s) still hold a slot that cannot "
        "play, %d of them with NO playable stream at all.",
        result.rebound,
        result.channels_updated,
        result.placeholders_deleted,
        len(result.still_placeholder),
        len(result.unplayable),
    )
    if result.orphans_swept:
        logger.info(
            "[DBAS-REBIND] Residue sweep: %d orphaned placeholder(s) left by an "
            "EARLIER restore removed from the synthetic '%s' account.",
            result.orphans_swept, CUSTOM_STREAM_ACCOUNT_NAME,
        )
    return result


async def rebind_placeholders_after_refresh(
    *,
    client: DispatcharrClient,
    trigger: str,
    allow_fuzzy: bool = True,
) -> RebindResult:
    """Rebind leftover restore placeholders once an M3U refresh materialized streams.

    THE FIX for the ``…-2o0cz`` residual (module docstring, "WHY ONE SHOT IS NOT
    ENOUGH"). On a redacted artifact the restore's own rebind runs against an
    account that has no credential yet and therefore no streams, so it resolves
    nothing; the operator then re-enters the credential and refreshes, and until
    now that changed nothing they could see. This is the pass that makes that
    second step sufficient, without the archive, the ledger or the id remap —
    each placeholder carries the archived stream's own name and is its own match
    key.

    CHEAP NO-OP. M3U refresh is a hot, scheduled path, so the pass gates itself
    twice before doing any real work and logs NOTHING on either exit:

    1. no M3U account named
       :data:`~dbas.custom_stream_fallback.CUSTOM_STREAM_ACCOUNT_NAME` exists —
       ONE ``get_m3u_accounts`` call, and the steady state on any instance that
       has never restored or that a previous pass already tidied (the empty
       account is dropped, so this stays false afterwards);
    2. the account exists but holds no unservable stream — one account-scoped
       stream page on top.

    Only past both gates does it fetch the full stream and channel lists.

    THE SAFETY ENVELOPE is exactly the one PR #784 established for the residue
    sweep, reused rather than re-derived: a stream is a rebind candidate ONLY
    when it sits on the synthetic account AND carries no url. An operator's own
    unservable custom stream on ANY OTHER account is never rebound, never counted
    and never deleted.

    Never raises — every failure path inside is logged and swallowed, and the
    callers (an M3U refresh completion) must not fail because a hygiene pass did.

    Args:
        client: The Dispatcharr API client.
        trigger: Operator-facing name of what invoked the pass (e.g. the account
            name, or the task id), used only in logs so the drill log says WHICH
            refresh healed the instance.
        allow_fuzzy: Whether the matcher may use its Tier-4 fuzzy rung. Defaults
            to ``True``, matching the restore path this is finishing.

    Returns:
        A :class:`RebindResult` describing what changed. All-zero when either
        gate short-circuited or a rebind was already in flight.
    """
    if _REBIND_LOCK.locked():
        # A restore's own rebind, or another refresh completion, is already
        # doing this work. Skipping is correct AND is the double-run guard: the
        # check and the acquire below have no ``await`` between them, so in one
        # event loop the pair is atomic.
        logger.info(
            "[DBAS-REBIND] A placeholder rebind is already in flight; skipping "
            "the one %s would have triggered.", trigger,
        )
        return RebindResult()
    async with _REBIND_LOCK:
        try:
            return await _rebind_from_live_placeholders(
                client=client, trigger=trigger, allow_fuzzy=allow_fuzzy
            )
        except Exception as exc:  # noqa: BLE001 - never fail a refresh over hygiene
            logger.warning(
                "[DBAS-REBIND] Post-refresh placeholder rebind (%s) hit an error "
                "and was abandoned; nothing was rolled back: %s", trigger, exc,
            )
            return RebindResult()


async def _rebind_from_live_placeholders(
    *,
    client: DispatcharrClient,
    trigger: str,
    allow_fuzzy: bool,
) -> RebindResult:
    """The refresh-driven rebind body — see :func:`rebind_placeholders_after_refresh`."""
    result = RebindResult()

    # --- Gate 1: does the synthetic account exist at all? --------------------
    synthetic_account_ids = await _synthetic_account_ids(client)
    if not synthetic_account_ids:
        return result

    # --- Gate 2: does it hold any placeholder that cannot serve? -------------
    # "Cannot serve", not "URL-less": a credential-redacted placeholder (bead
    # ``…-msqf7``) carries a non-empty url, so a truthiness gate returned EMPTY
    # here and this whole pass exited before doing anything. That made credential
    # re-entry plus a refresh — the recovery an operator reaches for FIRST —
    # change nothing they could see (bead ``…-1td94``). The ACCOUNT test below is
    # untouched and is still the entire safety envelope.
    # Account-scoped so the common case costs one small page, not the whole
    # stream table. The account filter is re-applied client-side as well: it is
    # the predicate that keeps this pass off an operator's own streams, and it
    # must not depend on an upstream query parameter being honoured.
    placeholder_by_id: dict[int, dict] = {}
    for account_id in sorted(synthetic_account_ids):
        for stream in await _fetch_all(
            client, client.get_streams, m3u_account=account_id
        ):
            stream_id = _as_int(stream.get("id"))
            if stream_id is None or url_can_serve(stream.get("url")):
                continue
            if _stream_account_id(stream) not in synthetic_account_ids:
                continue
            placeholder_by_id[stream_id] = stream
    if not placeholder_by_id:
        return result

    placeholder_ids = set(placeholder_by_id)
    logger.info(
        "[DBAS-REBIND] %s: %d leftover restore placeholder(s) on the synthetic "
        "'%s' account; re-running the stream matcher against the materialized "
        "provider streams.",
        trigger, len(placeholder_ids), CUSTOM_STREAM_ACCOUNT_NAME,
    )

    all_streams = await _fetch_all(client, client.get_streams)
    # REAL candidates — same definition as the archive-driven pass: everything
    # that is not one of the placeholders and whose address can serve.
    candidates = [
        s
        for s in all_streams
        if _as_int(s.get("id")) not in placeholder_ids and url_can_serve(s.get("url"))
    ]
    candidate_ids = {cid for s in candidates if (cid := _as_int(s.get("id"))) is not None}
    if not candidates:
        logger.info(
            "[DBAS-REBIND] %s: no provider stream with a usable address on the "
            "destination yet; the placeholders are kept so nothing loses its "
            "binding.", trigger,
        )
        return result

    all_channels = await _fetch_all(client, client.get_channels)
    current_by_channel = {
        cid: [sid for s in (ch.get("streams") or []) if (sid := _as_int(s)) is not None]
        for ch in all_channels
        if (cid := _as_int(ch.get("id"))) is not None
    }
    # The channel's OWN name, from the destination — there is no archive here to
    # take a label from.
    channel_names = {
        cid: str(ch.get("name") or "<unknown>")
        for ch in all_channels
        if (cid := _as_int(ch.get("id"))) is not None
    }

    # Post-rebind bindings per channel, so the sweep below asks "is this stream
    # referenced by anything" against the truth this pass just established
    # rather than the list it fetched. A channel this loop skipped keeps its
    # fetched list, which is still current.
    final_by_channel: dict[int, list[int]] = {}

    for dest_channel_id, current_ids in current_by_channel.items():
        if not any(sid in placeholder_ids for sid in current_ids):
            # No synthetic placeholder on this channel — nothing this pass may
            # touch. Its bindings are read for the reference set below and
            # otherwise left entirely alone.
            continue
        label = channel_names.get(dest_channel_id, "<unknown>")

        # THE SHARED CORE — identical order, de-dup and PATCH-failure behaviour
        # to the restore path. The only difference is ``archived_for``: the
        # placeholder IS the record to re-match with, because
        # ``custom_stream_fallback`` built it from the archived stream and kept
        # its name verbatim.
        outcome = await _rebind_one_channel(
            client=client,
            dest_channel_id=dest_channel_id,
            label=label,
            current_ids=current_ids,
            placeholder_ids=placeholder_ids,
            archived_for=placeholder_by_id.get,
            candidates=candidates,
            allow_fuzzy=allow_fuzzy,
        )
        final_by_channel[dest_channel_id] = list(outcome.final_ids)
        if outcome.updated:
            result.rebound += outcome.rebound
            result.channels_updated += 1

        # The playability audit, scoped to the channels this pass touched. There
        # is no RestoreReport here, so it exists to LOG the residue an operator
        # still has to fix, and it uses the same test as the restore path: a
        # channel plays if any id it is left holding is a candidate that can serve.
        if any(sid not in candidate_ids for sid in outcome.final_ids):
            result.still_placeholder.append(label)
            if not any(sid in candidate_ids for sid in outcome.final_ids):
                result.unplayable.append(label)
                logger.warning(
                    "[DBAS-REBIND] Channel '%s' (id=%s) has NO playable stream: "
                    "not one of its slots carries an address that can serve. "
                    "Attach a real stream.",
                    label, dest_channel_id,
                )

    # --- Cleanup: the SAME sweep and account drop the restore path runs. -----
    referenced_ids: set[int] = set()
    for channel_id, current in current_by_channel.items():
        referenced_ids.update(final_by_channel.get(channel_id, current))

    swept_ids = await _sweep_orphaned_placeholders(
        client=client,
        all_streams=all_streams,
        referenced_ids=referenced_ids,
        already_deleted=set(),
        synthetic_account_ids=synthetic_account_ids,
    )
    result.orphans_swept = len(swept_ids)
    result.account_deleted = await _drop_empty_synthetic_account(
        client=client,
        all_streams=all_streams,
        deleted_ids=swept_ids,
        synthetic_account_ids=synthetic_account_ids,
    )

    logger.info(
        "[DBAS-REBIND] %s: rebind complete — %d slot(s) rebound across %d "
        "channel(s); %d orphaned placeholder(s) swept; %d channel(s) still hold "
        "a slot that cannot play, %d of them with NO playable stream at all.",
        trigger,
        result.rebound,
        result.channels_updated,
        result.orphans_swept,
        len(result.still_placeholder),
        len(result.unplayable),
    )
    return result


async def _sweep_orphaned_placeholders(
    *,
    client: DispatcharrClient,
    all_streams: list[dict],
    referenced_ids: set[int],
    already_deleted: set[int],
    synthetic_account_ids: set[int],
) -> set[int]:
    """Delete placeholder streams an EARLIER restore stranded (bead ``…-dgnms``).

    THE PREDICATE — a stream is swept only when ALL FOUR hold:

    1. it sits on the synthetic ``CUSTOM_STREAM_ACCOUNT_NAME`` account (an
       account ECM itself creates for exactly this purpose and puts nothing else
       under);
    2. its url CANNOT SERVE — absent, or carrying the redaction sentinel (a
       servable stream on that account is not a
       placeholder — it is something that can actually play, and nothing here
       deletes a playable stream);
    3. NO channel references it, taken from the post-rebind reference set;
    4. this run has not already deleted it via the ledger-owned pass.

    Every one is necessary, and the conjunction is the narrowest predicate that
    still catches the measured residue. Drill runs 4 AND 5 finished the redacted
    round trip with 14 URL-less streams bound to NO channel, because the
    ledger-owned deletion above can only see placeholders THIS run created:
    a placeholder from an EARLIER restore, superseded when a later restore
    rebound its channel onto a real stream, is never referenced again and was
    never cleaned up. The instance accrued dead rows on every redacted cycle.

    WHAT IS DELIBERATELY NOT SWEPT, and why the predicate is not widened:

    * an unservable stream on ANY OTHER account — an operator may legitimately
      keep their own custom URL-less streams, and this pass has no business
      classifying those. The account test is what keeps the sweep to streams ECM
      itself synthesized, and widening the URL half to ``url_can_serve`` (bead
      ``…-1td94``) does not touch it: without that widening a credential-redacted
      placeholder survived every sweep, so each healed cycle left its dead rows
      behind for the matcher to keep tripping over;
    * a placeholder a channel still holds — that binding is load-bearing, and
      cutting it is precisely the outage the rebind pass exists to prevent;
    * anything at all when the synthetic account cannot be identified — the set
      is empty and this function is a no-op, never a guess.

    BEST-EFFORT: a failed delete is logged and skipped; nothing raises, nothing
    fails the restore, nothing triggers a rollback.

    Returns:
        The destination ids actually deleted.
    """
    swept: set[int] = set()
    if not synthetic_account_ids:
        return swept
    for stream in all_streams:
        stream_id = _as_int(stream.get("id"))
        if stream_id is None or stream_id in already_deleted:
            continue
        if url_can_serve(stream.get("url")):
            continue
        if _stream_account_id(stream) not in synthetic_account_ids:
            continue
        if stream_id in referenced_ids:
            continue
        try:
            await client.delete_stream(stream_id)
        except Exception as exc:  # noqa: BLE001 - 404 == already gone; both fine
            logger.warning(
                "[DBAS-REBIND] Could not sweep orphaned placeholder stream "
                "id=%s ('%s'): %s",
                stream_id, _stream_name(stream), exc,
            )
            continue
        swept.add(stream_id)
        logger.info(
            "[DBAS-REBIND] Swept orphaned placeholder stream id=%s ('%s'): "
            "no usable address, on the synthetic custom-stream account, and "
            "bound to no channel.",
            stream_id, _stream_name(stream),
        )
    return swept


async def _drop_empty_synthetic_account(
    *,
    client: DispatcharrClient,
    all_streams: list[dict],
    deleted_ids: set[int],
    synthetic_account_ids: set[int],
) -> bool:
    """Delete the synthetic custom-stream account once it is EMPTY (``…-dgnms``).

    THE SAFETY PROPERTY IS UNCHANGED, and it is the emptiness test: an account
    that still has streams under it is NEVER deleted, because deleting it
    cascades those streams away from channels this run did not touch. That
    reasoning is sound and this function keeps it verbatim.

    What relaxes is only WHO CREATED IT. The previous implementation walked the
    :class:`RollbackLedger` for an ``M3U_ACCOUNT`` entry and no-oped when there
    was none, so an account created by an EARLIER restore and merely REUSED by
    this one survived forever — drill run 5 emptied it completely
    (``14 placeholder(s) deleted``, ``0 channel(s) still hold a slot that cannot
    play``) and still found ``'ECM Custom Streams (DBAS restore)'`` in the M3U
    account list. Who created an empty account has no bearing on whether it
    should still exist.

    Emptiness is verified against the LIVE INSTANCE — the destination's own
    stream list this pass fetched, minus the ids this pass has since confirmed
    deleted — never against the ledger, which knows only about this run.

    BEST-EFFORT: a failed delete is logged and reported as "not deleted"; it
    never raises.

    Returns:
        ``True`` when an account was deleted.
    """
    if not synthetic_account_ids:
        return False
    # Accounts that still have at least one stream under them. A stream whose id
    # cannot be read counts as still there: "we could not tell" must keep the
    # account, never drop it.
    occupied: set[int] = set()
    for stream in all_streams:
        stream_id = _as_int(stream.get("id"))
        if stream_id is not None and stream_id in deleted_ids:
            continue
        account_id = _stream_account_id(stream)
        if account_id is not None:
            occupied.add(account_id)
    deleted_any = False
    for account_id in sorted(synthetic_account_ids):
        if account_id in occupied:
            logger.info(
                "[DBAS-REBIND] Keeping the synthetic custom-stream account id=%s: "
                "streams remain under it.",
                account_id,
            )
            continue
        try:
            await client.delete_m3u_account(account_id)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning(
                "[DBAS-REBIND] Could not delete the synthetic custom-stream "
                "account id=%s: %s",
                account_id, exc,
            )
            continue
        deleted_any = True
        logger.info(
            "[DBAS-REBIND] Deleted the now-empty synthetic custom-stream "
            "account id=%s.",
            account_id,
        )
    return deleted_any
