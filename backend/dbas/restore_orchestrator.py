"""Phase-2 DBAS restore ORCHESTRATOR — pre-flight + apply + compensating rollback.

Bead ``enhancedchannelmanager-0i2vt.18``. Dispatcharr has no database
transactions (ADR-012), so a restore that hits an upstream error mid-run could
leave a half-applied archive. This module is the safety layer that prevents that:

1. **Pre-flight** (``dbas.preflight``) validates the WHOLE plan before any write.
   A failing pre-flight refuses the restore with ZERO mutation — no importer is
   ever called.
2. **Ordered apply** runs the per-category importers in the hard Phase-2
   sequence through a clean registry of :class:`ImporterStep` callables. The
   importers record every created entity into the shared
   :class:`~dbas.restore_contracts.RollbackLedger` (they already do this); the
   orchestrator persists that ledger DURABLY after each step so a mid-restore
   ECM crash leaves a recoverable record.
3. **Compensating rollback** — if any step fails (raises, or reports a category
   failure in a FATAL category — see :data:`NON_FATAL_FAILURE_CATEGORIES`), the
   orchestrator issues compensating DELETEs in
   :meth:`RollbackLedger.compensation_order` (reverse creation = reverse
   dependency order). A delete that 404s counts as SUCCESS (already gone); a
   non-404 delete error means the rollback is INCOMPLETE.
4. **Outcome** (:class:`~dbas.restore_contracts.RestoreOutcome`) is computed
   from what actually happened and is NEVER ``SUCCESS`` on mixed state.

----------------------------------------------------------------------------
HARD ORDERING (ADR-012 D-table) + the DEFERRED phase
----------------------------------------------------------------------------

Importers run in strict dependency order::

    M3U accounts → EPG sources (+ bounded EPG-data download wait)
      → channel groups / channel profiles → user agents → stream profiles
      → Dispatcharr settings → ECM settings → users → channels → DVR rules
      → logos

USER AGENTS run BEFORE STREAM PROFILES (bead ``enhancedchannelmanager-lvfwd``).
A Dispatcharr stream profile carries a ``user_agent`` FK, so the USER_AGENT
IdRemapTable namespace must be populated before the stream-profile importer
rewrites it. The previous order (stream profiles first) POSTed the archived
SOURCE user-agent id at a destination that had not created the agent yet —
``400 {"user_agent":["Invalid pk \\"4\\" - object does not exist."]}`` — which
aborted the whole restore and rolled the instance back.

then the DEFERRED phase applies LAST: the M3U importer returns auto-sync
settings that MUST NOT fire during the run (they race the logo import on the
Dispatcharr side). The orchestrator collects each importer's deferred settings
and applies them only after every category is done, via
``dbas.importers.m3u_accounts.apply_deferred_auto_sync``. EPG data is the
opposite: Dispatcharr's channel↔EPG matching needs the data BEFORE channels are
created, so the apply registry's EPG step waits (bounded, non-fatal) for the
download instead of deferring it — see :func:`_epg_step_with_download_wait`.

AFTER the deferred phase, one final restore-completion step runs on a clean
apply: the PLACEHOLDER REBIND (:mod:`dbas.placeholder_rebind`, bead
``…-2o0cz``). Deferring the M3U refresh is what makes the restore safe, but it
also means every archived stream MISSED the matcher at channel-import time and
was synthesized as a URL-less placeholder — so the deferred refresh is the first
moment there is anything real to match against. Without this step a restore
reports ``success, 0 failures`` for an instance where not one channel can play.

----------------------------------------------------------------------------
FULL WIRING + dry-run/apply parity (bead kxcjf)
----------------------------------------------------------------------------

Every per-category importer is WIRED into BOTH registries: the apply registry
(:func:`default_importer_steps`) and the dry-run registry
(:func:`dry_run_importer_steps`) cover the SAME category set, in the same
order, through the SAME shared step builders — so the counts the default-ON
dry-run preview promises are exactly what a confirmed apply delivers. The
orchestrator still supports a step with ``importer=None`` as a logged no-op
SEAM (never a silent skip) for callers that register partial step lists (e.g.
the sync engine's config-only registry), but neither default registry carries
one. PLUGINS are deliberately absent from both (ADR-012 D10).

----------------------------------------------------------------------------
ABORT-ON-ANY-FAILED-KEY (PO decision 2026-08-03, enhancedchannelmanager-zt3kf)
----------------------------------------------------------------------------

The apply loop below aborts the WHOLE restore and rolls back on the FIRST
category that reports ANY ``failed`` count greater than zero (see ``if
cat.failed > 0`` a few lines below ``run_restore``) — including a category
whose only failures are benign-sounding ``FailureReason.DEPENDENCY_UNRESOLVED``
entries (e.g. a settings key present in the archive but absent on the
destination; see ``dbas.importers.settings_agents``). The ONE exception is
:data:`NON_FATAL_FAILURE_CATEGORIES` (see below). This is a DELIBERATE,
PO-confirmed policy, not an accident of the current importer wiring:

* There is NO per-key skip-with-warning path. A restore that could partially
  apply settings (skip only the unresolved keys, apply the rest) was
  considered and REJECTED — partial application of a config category is a
  worse failure mode than a clean full rollback: an operator staring at "12
  of 13 settings applied" cannot tell which one is missing without reading a
  report, and a half-applied settings category is the ONE thing in this
  orchestrator that CANNOT be compensated (see the settings-not-rolled-back
  note below) — so letting it partially land at all is strictly worse than
  refusing.
* Rationale: Dispatcharr has no transactions (ADR-012), so "abort on first
  failure, always" is the one rule simple enough to reason about under a
  crash mid-rollback, and it matches every OTHER load-bearing category's failure
  handling (channel groups, channels, …) — settings does not get a bespoke,
  softer rule just because its failure mode reads as benign.
* Operator-facing consequence: when the aborting category is SETTINGS and its
  failure reason is ``DEPENDENCY_UNRESOLVED``, :func:`run_restore` appends an
  explicit report note that a RETRY of the same restore against the SAME
  destination will fail identically (the artifact carries a settings key the
  destination does not have — no upstream flake, nothing to retry past). The
  remediation is to edit the category selection (exclude Settings) or restore
  against a destination whose Dispatcharr version has that key — never "try
  again."

----------------------------------------------------------------------------
THE NON-FATAL CATEGORIES (beads enhancedchannelmanager-y65si + …-d0agi)
----------------------------------------------------------------------------

``dispatcharr_users`` and ``logos`` are the members of
:data:`NON_FATAL_FAILURE_CATEGORIES`. A row upstream refuses in either category
is COUNTED as a failure in that category (visible in the report, listed in
``failure_details``, and it forbids a ``SUCCESS`` outcome) but does NOT abort the
run or trigger a compensating rollback. The restore continues and the outcome is
:attr:`~dbas.restore_contracts.RestoreOutcome.COMPLETED_WITH_FAILURES`.

The admission test is the same for both, and it is narrow: NOTHING ELSE IN THE
RESTORE HOLDS A HARD FK INTO THE CATEGORY, so a row that does not come back
degrades only itself. Do not widen this set without demonstrating that.

* **users** (y65si): nothing else in the restore references a user, and the
  drill (bead ``…-a429n``) showed the alternative is catastrophic. One archived
  user Dispatcharr would not create cost the operator their M3U account, EPG
  source, channel groups and channel profile, PLUS an ECM settings mutation the
  rollback cannot compensate at all.
* **logos** (d0agi): a channel's ``logo_id`` is a SOFT reference. The logos
  importer already treats an unrestorable logo as a counted, reported miss
  (:attr:`~dbas.restore_contracts.RestoreReport.logo_misses`, the D9 red banner,
  and the affected-channel drill-down), and
  :func:`dbas.channel_reattach.reattach_channel_logos` simply leaves the channel
  without artwork. Logos also run LAST in the hard ordering, so a logo failure
  can only ever destroy work that already succeeded. The drill (run
  ``2026-08-04-run2``) saw exactly that: ONE image that could not be written
  rolled back 44 successfully-restored entities. A channel with no artwork is a
  cosmetic defect; a rolled-back restore is a data-loss event.
* **upcoming_recordings** (…-ciabe): a recording is a pure LEAF. Nothing in the
  restore holds an FK into one — a channel does not know its recordings, and
  neither does a DVR rule (the rule's own generator finds them by querying, not
  by reference). It also runs after every category it depends on, so a refusal
  here can only ever destroy work that already succeeded. Weigh the two
  directions: one recording Dispatcharr will not schedule is one programme the
  operator records by hand, while rolling the run back over it costs them their
  channels, groups, profiles and settings. The upstream refusal this most
  plausibly comes from is a stale timestamp (``400 "End time must be in the
  future."``), which is a property of the ARCHIVE's age rather than of the
  destination — so a retry cannot fix it and a rollback would punish an operator
  for restoring an old backup.

Scope: this covers a REPORTED per-row failure. An importer that RAISES stays
fatal even for these categories. ``UsersCapabilityError`` (the fail-closed
User-schema guard) means "this destination cannot be reasoned about", not "one
bad row".

----------------------------------------------------------------------------
404-AS-SUCCESS + the credential-hygiene rule (the bead .8 lesson)
----------------------------------------------------------------------------

A compensating DELETE that returns 404 is treated as a successful compensation
(the entity is already gone — desired end state). Only a non-404 upstream error
counts as a failed compensation and drives ``FAILED_ROLLBACK_INCOMPLETE``.

We log/report only SAFE fields — entity type, destination id, label (a name,
never a credential), counts, status codes. We never log a server_url, username,
password, or an upstream SDK exception body verbatim.

Conventions (``docs/style_guide.md``): Pydantic v2 models; ``snake_case``;
Google-style docstrings; lazy ``%``-formatted logging; no secrets in any log or
report field.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import CONFIG_DIR
from dbas.preflight import ImportPlan, PreflightResult, run_preflight
from dbas.restore_contracts import (
    ChannelReattachMode,
    EntityType,
    FailureReason,
    LedgerEntry,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Durable ledger lives under the same mounted volume as journal.db / settings.json
# (CONFIG_DIR), so a mid-restore ECM crash leaves a recoverable record.
_LEDGER_DIR = CONFIG_DIR / "dbas"

# Categories whose REPORTED per-row failures are counted but never abort the
# restore or trigger a compensating rollback (beads …-y65si and …-d0agi; see the
# module docstring's "THE NON-FATAL CATEGORIES" section for the admission test
# each member had to pass and what it would take to add another). A raising
# importer is still fatal regardless of category.
NON_FATAL_FAILURE_CATEGORIES: frozenset[EntityType] = frozenset(
    {EntityType.USER, EntityType.LOGO, EntityType.UPCOMING_RECORDING}
)


# ---------------------------------------------------------------------------
# Importer registry — the clean seam importers register through
# ---------------------------------------------------------------------------

# An importer callable takes the shared apply context and applies ONE category.
# It mutates the shared RestoreReport / RollbackLedger / IdRemapTable in place and
# returns an optional list of deferred settings (M3U/EPG) to apply in the final
# phase. ``None`` means "nothing deferred".
ImporterCallable = Callable[["ApplyContext"], Awaitable["list[dict] | None"]]


@dataclass
class ImporterStep:
    """One category's place in the hard restore sequence.

    Attributes:
        entity_type: The category this step restores (its place in the order).
        importer: The async callable that applies the category, or ``None`` for a
            registration SEAM — a deliberately-unwired slot in a caller-built
            partial registry. A seam step is a logged no-op, never a silent
            skip. (Both default registries are fully wired — bead kxcjf.)
        defers: Whether this step returns deferred settings (M3U / EPG) that the
            orchestrator must apply in the final deferred phase.
    """

    entity_type: EntityType
    importer: ImporterCallable | None = None
    defers: bool = False


@dataclass
class ApplyContext:
    """The shared state threaded through every importer step.

    Importers read the plan slice for their category and write into the shared
    report / ledger / remap. The deferred-apply phase reads ``deferred``.
    """

    plan: ImportPlan
    client: DispatcharrClient
    report: RestoreReport
    ledger: RollbackLedger
    remap: "object"  # IdRemapTable; typed loosely to avoid a hard import cycle
    is_dry_run: bool = False
    # Collected deferred settings (M3U auto-sync, EPG download) — applied LAST.
    deferred: list[dict] = field(default_factory=list)
    # Durable per-create ledger flush. The orchestrator wires this to
    # ``persist_ledger`` so an importer can flush the shared ledger to disk
    # IMMEDIATELY after each ``record_created`` and BEFORE the next upstream
    # create (the RollbackLedger durability contract — bead l1p4p). On a dry-run
    # this is a no-op (no entity is created, nothing to persist). Defaults to a
    # no-op so a test that builds an ApplyContext directly need not wire it.
    persist_ledger: "Callable[[], None]" = field(default=lambda: None)
    # What the post-create reattach passes do to channels this restore did NOT
    # create (bead …-dfkbn, PR review W1). PRESERVE by default: in the
    # disaster-recovery case every channel is created and the modes are
    # identical, so the safe default costs DR nothing, while a merge into a live
    # instance keeps the EPG links and logos the operator set themselves.
    channel_reattach_mode: "ChannelReattachMode" = field(
        default_factory=lambda: ChannelReattachMode.PRESERVE
    )
    # ARCHIVE (source) ids of the channels the CHANNEL step created, or on a dry
    # run would create. Filled by the channels step, read by BOTH reattach passes
    # (the logo pass runs in the LOGO step, later in the same context).
    created_channel_source_ids: set[int] = field(default_factory=set)
    # Its complement (bead …-r1ei7): ARCHIVE source id -> the DESTINATION channel
    # row the channels importer MATCHED it against, exactly as it was found. The
    # channel-group reconcile pass reads the destination's pre-restore
    # ``channel_group_id`` from it; nothing else in the run can supply that
    # without a second full channel list and a race against this run's creates.
    matched_existing_channels: dict[int, dict] = field(default_factory=dict)

    def flush_ledger(self) -> None:
        """Durably persist the shared ledger (per-create flush; no-op on dry-run).

        Importers call this right after :meth:`RollbackLedger.record_created` and
        before issuing the next create, so a mid-category ECM crash leaves a
        recoverable record of every entity created so far — not just those from
        completed steps.
        """
        self.persist_ledger()


class ImporterStepError(RuntimeError):
    """A category importer failed in a way that must trigger rollback.

    Carries the category and a SANITIZED message (no secrets). Raised by the
    orchestrator when an importer raises, or when a step reports a category
    failure and the orchestrator decides to roll back.
    """

    def __init__(self, entity_type: EntityType, message: str):
        self.entity_type = entity_type
        super().__init__(message)


# ---------------------------------------------------------------------------
# Compensating-delete dispatch — EntityType -> client delete method
# ---------------------------------------------------------------------------

# Maps a ledgered entity type to the client coroutine that deletes one by id.
# A type with no compensator here cannot be safely undone by a single-id DELETE;
# the rollback treats it as an INCOMPLETE compensation (surfaced, never silently
# counted as success) so we never claim a clean rollback we did not perform.
def _delete_dispatch(client: DispatcharrClient) -> dict[EntityType, Callable[[int], Awaitable[None]]]:
    """Build the EntityType -> single-id delete coroutine map for ``client``."""
    return {
        EntityType.M3U_ACCOUNT: client.delete_m3u_account,
        EntityType.EPG_SOURCE: client.delete_epg_source,
        EntityType.CHANNEL_GROUP: client.delete_channel_group,
        EntityType.CHANNEL_PROFILE: client.delete_channel_profile,
        EntityType.STREAM_PROFILE: client.delete_stream_profile,
        EntityType.CHANNEL: client.delete_channel,
        EntityType.STREAM: client.delete_stream,
        EntityType.USER: client.delete_user,
        # kxcjf — the full-wiring bead: every ledgerable created-entity type has
        # a compensator. SETTINGS is deliberately absent: a settings change is
        # config, not a created entity — it is never ledgered, and run_restore
        # surfaces "settings are not rolled back" in the report notes instead of
        # silently claiming a full rollback.
        EntityType.USER_AGENT: client.delete_user_agent,
        EntityType.DVR_RULE: client.delete_dvr_rule,
        EntityType.UPCOMING_RECORDING: client.delete_recording,
        EntityType.LOGO: client.delete_logo,
    }


def _status_code_of(exc: Exception) -> int | None:
    """Best-effort HTTP status code for an upstream delete error.

    Delete helpers call ``response.raise_for_status()`` → ``httpx.HTTPStatusError``
    carrying ``.response.status_code``. Some hand-built client helpers raise a
    bare ``Exception`` whose text embeds the status; we recover the code from the
    text as a fallback. ``None`` means "could not determine" (treated as non-404).
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code
    text = str(exc)
    # Hand-built helpers format "<thing> failed: <status> - <body>".
    for token in text.replace(":", " ").replace("-", " ").split():
        if token.isdigit() and len(token) == 3:
            return int(token)
    return None


def _is_already_gone(exc: Exception) -> bool:
    """True when a compensating DELETE error is a 404 — already gone == success."""
    return _status_code_of(exc) == 404


# ---------------------------------------------------------------------------
# Durable ledger persistence (atomic temp + os.replace)
# ---------------------------------------------------------------------------


def _ledger_path(restore_id: str, ledger_dir: Path | None = None) -> Path:
    """On-disk path for a restore's durable ledger file."""
    base = ledger_dir or _LEDGER_DIR
    return base / f"restore_ledger_{restore_id}.json"


def persist_ledger(ledger: RollbackLedger, *, ledger_dir: Path | None = None) -> Path:
    """Write the ledger to disk atomically (temp file + ``os.replace``).

    Called after each created-entity batch / step so a mid-restore crash leaves a
    recoverable record. The write is atomic: a crash never leaves a half-written
    ledger. Returns the path written.
    """
    base = ledger_dir or _LEDGER_DIR
    base.mkdir(parents=True, exist_ok=True)
    final = _ledger_path(ledger.restore_id, base)
    tmp = final.with_suffix(".json.tmp")
    tmp.write_text(ledger.model_dump_json())
    os.replace(tmp, final)
    return final


def delete_ledger(restore_id: str, *, ledger_dir: Path | None = None) -> None:
    """Remove a restore's ledger file (clean success — no compensation needed)."""
    path = _ledger_path(restore_id, ledger_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


@dataclass
class RollbackResult:
    """Outcome of a compensating-delete rollback run.

    ``complete`` is ``True`` only when EVERY pending ledger entry was compensated
    (deleted or confirmed already-gone via 404). ``residue`` carries the entries
    that could NOT be compensated (a non-404 delete error, or no compensator
    registered for the type) so the report can surface them for manual cleanup.
    """

    complete: bool
    compensated: list[LedgerEntry] = field(default_factory=list)
    residue: list[LedgerEntry] = field(default_factory=list)


async def run_rollback(
    *,
    ledger: RollbackLedger,
    client: DispatcharrClient,
    ledger_dir: Path | None = None,
) -> RollbackResult:
    """Issue compensating DELETEs for every created entity, in compensation order.

    Order is :meth:`RollbackLedger.compensation_order` — descending sequence =
    reverse creation = reverse dependency order, so a parent is never deleted
    while a child still points at it. Idempotent: a DELETE that 404s is a success
    (already gone); an entry already marked compensated is skipped on a re-run.

    A non-404 delete error (or a type with no registered compensator) leaves the
    entry in the ledger as RESIDUE and makes the result INCOMPLETE — surfaced
    loudly, never counted as success.

    The ledger is persisted after each successful compensation so a crash mid-
    rollback can resume without re-deleting.

    Args:
        ledger: The shared rollback ledger (mutated: entries marked compensated).
        client: The Dispatcharr API client.
        ledger_dir: Override the durable ledger directory (tests).

    Returns:
        A :class:`RollbackResult` with ``complete`` and the compensated/residue
        split.
    """
    dispatch = _delete_dispatch(client)
    compensated: list[LedgerEntry] = []
    residue: list[LedgerEntry] = []

    for entry in ledger.compensation_order():
        deleter = dispatch.get(entry.entity_type)
        if deleter is None:
            logger.error(
                "[DBAS-ROLLBACK] No compensator registered for entity_type=%s id=%s; "
                "rollback INCOMPLETE for this entry — manual cleanup required.",
                entry.entity_type.value,
                entry.destination_id,
            )
            residue.append(entry)
            continue
        try:
            await deleter(entry.destination_id)
        except Exception as exc:  # noqa: BLE001 - classify by status, re-bucket below
            if _is_already_gone(exc):
                logger.info(
                    "[DBAS-ROLLBACK] Compensating delete of %s id=%s returned 404 "
                    "(already gone) — counted as success.",
                    entry.entity_type.value,
                    entry.destination_id,
                )
                entry.compensated = True
                compensated.append(entry)
                persist_ledger(ledger, ledger_dir=ledger_dir)
                continue
            logger.error(
                "[DBAS-ROLLBACK] Compensating delete of %s id=%s FAILED (status=%s); "
                "rollback INCOMPLETE — manual cleanup required.",
                entry.entity_type.value,
                entry.destination_id,
                _status_code_of(exc),
            )
            residue.append(entry)
            continue

        entry.compensated = True
        compensated.append(entry)
        persist_ledger(ledger, ledger_dir=ledger_dir)
        logger.info(
            "[DBAS-ROLLBACK] Compensated %s id=%s.",
            entry.entity_type.value,
            entry.destination_id,
        )

    complete = not residue
    logger.warning(
        "[DBAS-ROLLBACK] Rollback %s: %d compensated, %d residue.",
        "COMPLETE" if complete else "INCOMPLETE",
        len(compensated),
        len(residue),
    )
    return RollbackResult(complete=complete, compensated=compensated, residue=residue)


# ---------------------------------------------------------------------------
# Tri-state outcome
# ---------------------------------------------------------------------------


def _record_run_scope(plan: ImportPlan, remap: object) -> None:
    """Tell the shared remap which categories this run was asked to carry.

    Bead ``…-4mkoe``. The scope is what makes ``IdRemapTable.resolve`` returning
    ``None`` readable: a namespace the operator EXCLUDED was never going to be
    populated, while one that was in scope and is still empty means the replica
    is missing something it was asked for. Recorded from the SAME
    ``category(...).selected`` reading the importer wiring uses, so a category
    absent from the plan reads as excluded in both places.

    Tolerates a ``remap`` without the method (the parameter is typed loosely to
    avoid an import cycle, and tests pass stand-ins): the classification then
    falls back to its fail-loud default and reports every unresolved dependency.

    Args:
        plan: The restore plan carrying the operator's per-category selection.
        remap: The shared ``IdRemapTable`` threaded through every importer.
    """
    recorder = getattr(remap, "record_run_scope", None)
    if recorder is None:
        return
    recorder({cat.entity_type for cat in plan.categories if cat.selected})


def _report_has_failures(report: RestoreReport) -> bool:
    """True when any category in the report recorded at least one failure."""
    return any(cat.failed > 0 for cat in report.categories)


def _report_has_delivery_shortfall(report: RestoreReport) -> bool:
    """True when an APPLY produced a replica missing something the source had.

    THE PROPERTY, not a list of cases (beads ``…-daziw`` → ``…-posm1``):

        A run never presents as an unqualified SUCCESS when the replica it
        produced is missing something the source had and the run was asked to
        carry.

    The membership test and the reasoning for every inclusion and every
    exclusion live on :data:`RestoreReport.DELIVERY_SHORTFALL_FIELDS`, which is
    the single declaration this function reads. Two of its exclusions are load-
    bearing enough to repeat here, because both have already been implemented
    wrongly once:

    * NEVER ``channels_needing_stream_reattach``. It counts channels holding at
      least one placeholder SLOT, and the ``…-ixdaw`` fix (v0.18.1-0026)
      deliberately produces exactly that on a channel that keeps its real
      streams and plays fine — downgrading on it would false-fail an instance
      where every channel works.
    * NEVER a faithful absence (bead ``…-15g1j``). A channel whose SOURCE has no
      EPG link, no logo and no stream has lost nothing; the literal reading of
      the invariant turned all ten keystone round-trip scenarios red for
      replications that had lost nothing.

    A DRY RUN can never trigger this: a preview that predicts a shortfall is a
    prediction, not a failure, and nothing was applied to be missing.
    """
    if report.is_dry_run:
        return False
    return bool(report.delivery_shortfalls())


def outcome_for_unread_destination(report: RestoreReport) -> RestoreOutcome | None:
    """The outcome a REALIZED run that never read its destination must carry.

    THE PROPERTY (bead ``…-bj442``), of which a wrong password is one example:

        A realized cycle that could not read the destination it describes
        records an outcome no consumer can read as success — and records the
        SAME one everywhere, because this is the only place that decides it.

    Bead ``…-jqfxm`` established the FACT
    (:attr:`RestoreReport.destination_unreadable`) and acted on it in
    ``tasks.dbas_sync``, which corrected what the operator is TOLD.
    ``report.outcome`` was not part of that decision, so every surface that
    RECORDS the run — the task-history ``details.outcome`` row an API or MCP
    consumer reads, the ``sync_outbound`` journal row, and the persisted
    ``sync_targets.last_outcome`` / ``last_full_sync_at`` columns — kept reading
    ``success`` for a cycle that never read the destination it claims to
    describe. Measured at ``02c2a312``: a confirmed apply whose readback gate
    passed and whose M3U category read then returned 503 recorded
    ``outcome=success``, ``last_outcome="success"`` and a fresh
    ``last_full_sync_at``, with every category ``failed`` at 0 — because every
    importer degrades a failed destination read to ``existing = []``.

    A SIBLING OF THE DELIVERY-SHORTFALL SET, NEVER A MEMBER OF IT.
    :attr:`RestoreReport.DELIVERY_SHORTFALL_FIELDS` means "the source had this
    and the replica does not": a LOSS from a cycle that RAN, whose applied state
    is real, kept and reasonable-about, and every member of it resolves to
    ``COMPLETED_WITH_FAILURES`` — which
    :attr:`RestoreOutcome.is_degraded_not_failed` maps to a ``warning`` carrying
    a per-task opt-out. An unread destination is not that. Nothing was lost; the
    cycle never read the thing it describes, so it knows neither what the
    destination carries nor what it applied. Bead ``…-jqfxm`` deliberately
    treats that as an ERROR, so making it a shortfall member would downgrade a
    hard failure into an opt-out-able warning — the inverse of the defect.

    WHY ``FAILED_ROLLBACK_INCOMPLETE`` and not one of the other three. It is the
    only value that means INDETERMINATE — "the caller cannot tell what it got" —
    which is exactly the state ``…-jqfxm`` describes. ``SUCCESS`` and
    ``COMPLETED_WITH_FAILURES`` both assert the run finished and left state the
    operator can reason about; ``PARTIAL_FAILED_ROLLED_BACK`` asserts the
    instance is back to its pre-restore state. All three are claims ABOUT the
    destination, and a run that could not read it cannot make one.
    ``tasks.dbas_sync_engine`` already resolves a no-rollback, no-residue
    source-side name conflict to this same value.

    THE ``…-cwmid`` PROPERTY IS PRESERVED: this returns an OUTCOME and nothing
    else. Severity is still read off the outcome alone
    (:attr:`RestoreOutcome.is_degraded_not_failed`), so no condition is ever
    consulted for one and no condition can reorder the severities.

    A DRY RUN gets ``None``: a preview has no realized outcome to record (the
    ``…-kxuj2`` contract) and nothing was applied to be indeterminate. The
    marker still fails the preview at the task layer, which is ``…-jqfxm``'s
    half and is unchanged.

    Args:
        report: The restore report, carrying the ``…-jqfxm`` marker or not.

    Returns:
        The forced :class:`RestoreOutcome`, or ``None`` when the run read its
        destination (or is a preview) and the ordinary decision stands.
    """
    if report.is_dry_run or report.destination_unreadable is None:
        return None
    return RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE


def compute_outcome(
    *,
    report: RestoreReport,
    failure_occurred: bool,
    rollback: RollbackResult | None,
) -> RestoreOutcome:
    """Derive the outcome — NEVER ``SUCCESS`` on mixed state.

    The single guard that the whole bead exists to enforce:

    * ``SUCCESS`` — only when NO failure occurred AND no category reported a
      failure AND no rollback was needed AND every restored channel can play.
      Any whiff of failure forbids SUCCESS.
    * ``COMPLETED_WITH_FAILURES`` — the apply never decided to abort, so nothing
      was rolled back and the applied state stands, but the result is not clean.
      Either rows DID fail in a :data:`NON_FATAL_FAILURE_CATEGORIES` category
      (bead ``…-y65si``), or the apply produced a replica MISSING SOMETHING THE
      SOURCE HAD — any member of
      :data:`RestoreReport.DELIVERY_SHORTFALL_FIELDS` (beads ``…-daziw``,
      ``…-posm1``). The second case has clean per-category counts and is still
      not a success: the drill measured a lineup where not one channel could
      play behind a reported ``success … created 32, failed 0``, and the
      cross-instance sync measured 53 of 59 replica channels landing with no
      guide link and every logo binding lost behind ``success … failed 0``.
      Nothing is rolled back — the applied state is real and worth keeping.

      The trigger is the SET, never which member fired. Bead ``…-cwmid`` had to
      undo a narrower keying after a drill measured the severity ordering
      inverted; every member resolves to this one outcome, and severity is
      decided from the outcome alone
      (:attr:`RestoreOutcome.is_degraded_not_failed`).
    * ``PARTIAL_FAILED_ROLLED_BACK`` — a fatal failure occurred, a rollback ran,
      and it was COMPLETE (every created entity deleted or confirmed 404-gone).
    * ``FAILED_ROLLBACK_INCOMPLETE`` — the INDETERMINATE state, reported loudly.
      Either a fatal failure occurred and the rollback could not fully undo it
      (non-404 delete error, or a type with no compensator), or the run could
      not read the destination it describes and therefore knows neither what
      that destination carries nor what it applied (bead ``…-bj442``, keyed on
      :attr:`RestoreReport.destination_unreadable`). The second trigger DOMINATES
      every other reading below, because the counts a run gets from a
      destination it could not read describe the source; see
      :func:`outcome_for_unread_destination`.

    Args:
        report: The shared restore report (its per-category failure counts and
            its delivery-shortfall aggregates are independent signals that the
            result is not clean).
        failure_occurred: Whether the apply phase raised / decided to roll back.
        rollback: The rollback result, or ``None`` if no rollback ran.

    Returns:
        The :class:`RestoreOutcome`.
    """
    # A run that never read the destination it describes is indeterminate, and
    # that dominates every other reading of the counts — the counts themselves
    # are the SOURCE's, because each importer degrades a failed destination read
    # to "the destination is empty" (bead ``…-bj442``). Decided here rather than
    # compensated for at the task layer so that ONE decision feeds the task
    # result, the task-history row, the journal row and the persisted per-target
    # state. Sibling of the delivery-shortfall set, never a member of it — see
    # :func:`outcome_for_unread_destination` for why, and for why the rolled-back
    # verdicts are overridden too (they are claims about a destination this run
    # could not read).
    forced = outcome_for_unread_destination(report)
    if forced is not None:
        return forced

    mixed = failure_occurred or _report_has_failures(report)
    if not mixed:
        # Nothing FAILED, but a replica missing something the source had is
        # still mixed state — the applied lineup does not do the one thing it
        # exists to do, whether what it lost was a playable stream, a guide
        # link or its branding.
        if _report_has_delivery_shortfall(report):
            return RestoreOutcome.COMPLETED_WITH_FAILURES
        return RestoreOutcome.SUCCESS

    # A failure happened — SUCCESS is now impossible.
    # No abort was decided => no rollback ran and the applied state is kept as-is.
    # Describing that as "rolled back" (or as a rollback that failed) would be a
    # lie about what is on the destination right now.
    if not failure_occurred:
        return RestoreOutcome.COMPLETED_WITH_FAILURES

    # Distinguish the two rolled-back states by whether the rollback fully undid
    # the created entities.
    if rollback is not None and rollback.complete:
        return RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    return RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE


# ---------------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------------


async def run_restore(
    *,
    plan: ImportPlan,
    client: DispatcharrClient,
    steps: list[ImporterStep],
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: object,
    confirm_apply: bool = False,
    deferred_apply_fn: Callable[..., Awaitable[list[dict]]] | None = None,
    ledger_dir: Path | None = None,
    max_entities_per_category: int = None,  # type: ignore[assignment]
    channel_reattach_mode: ChannelReattachMode = ChannelReattachMode.PRESERVE,
    allow_fuzzy_stream_match: bool = True,
) -> RestoreReport:
    """Run a full restore: pre-flight → ordered apply → rollback-on-failure.

    The single orchestration chokepoint. Behaviour:

    0. **Default-ON dry-run guardrail (bead ``…-0i2vt.16``).** A restore is a
       counts-only DRY-RUN unless the caller passes ``confirm_apply=True``. Apply
       is opt-IN, never opt-out: without an explicit confirm the run is FORCED to
       ``report.is_dry_run = True`` and makes ZERO mutations no matter what the
       caller put on the report. This is an architectural property of the entry
       point — not a UI toggle a client could bypass. The destructive apply path
       requires both ``confirm_apply=True`` AND a non-dry-run report; a caller that
       sets ``is_dry_run`` on the report keeps the dry-run even with a confirm.
    1. **Pre-flight** (``run_preflight``). On a FAIL the restore is refused with
       ZERO mutation — no importer step is called — and the report is returned
       with ``outcome=FAILED_ROLLBACK_INCOMPLETE`` only if mutation had occurred;
       a pure pre-flight refusal records the problems as notes and leaves
       ``outcome`` ``None`` (nothing happened to have an outcome). The caller
       inspects ``report.notes`` / the returned report for the refusal.
    2. **Ordered apply**: run ``steps`` in registration order (the hard Phase-2
       sequence). After each step the ledger is persisted durably. A step that
       raises, or whose category reports a failure, triggers rollback of
       EVERYTHING created so far and stops the apply.
    3. **Deferred phase** (only when no failure): apply the collected deferred
       settings LAST via ``deferred_apply_fn``.
    4. **Outcome**: computed via ``compute_outcome`` — never SUCCESS on mixed
       state, which since bead ``…-daziw`` includes an apply that finished with
       a channel left holding no URL-bearing stream (``COMPLETED_WITH_FAILURES``:
       ran to completion, nothing rolled back, but something the operator had is
       not working). On clean success the durable ledger file is removed.

    Args:
        plan: The restore plan (categories + manifest + any pre-known remap).
        client: The Dispatcharr API client.
        steps: The ordered importer registry. A step with ``importer=None`` is a
            logged no-op SEAM.
        report: The shared restore report (populated by the importers).
        ledger: The shared rollback ledger (populated by the importers).
        remap: The shared IdRemapTable (threaded through importers).
        confirm_apply: The explicit opt-in to MUTATE. ``False`` (default) forces a
            dry-run — no importer mutates, no rollback, no deferred phase. ``True``
            lets the apply proceed ONLY when ``report.is_dry_run`` is also False.
        deferred_apply_fn: The deferred-apply coroutine (defaults to the M3U
            ``apply_deferred_auto_sync``); applied LAST on a clean run.
        ledger_dir: Override the durable ledger directory (tests).
        max_entities_per_category: Pre-flight count bound override (tests).
        channel_reattach_mode: What the post-create reattach passes do to
            channels this restore did NOT create (bead …-dfkbn, PR review W1).
            ``PRESERVE`` (default) leaves a matched-existing channel's EPG link
            and logo exactly as the operator has them; ``OVERWRITE`` applies the
            archive's. On an empty destination every channel is created and the
            two are indistinguishable.
        allow_fuzzy_stream_match: The stream-matching policy for the WHOLE run —
            whether the matcher's Tier-4 fuzzy rung is admitted. It must be the
            same value the ``steps`` registry was built with, because the
            post-create rebind (step 3c) is a second matcher pass over the same
            archived streams; a run whose importers are floored at Tier-3 and
            whose rebind is not does not have a stream-matching policy at all
            (bead ``…-efvyg``). Both production callers state it rather than
            inherit it:

            * the ARCHIVE RESTORE (``tasks.dbas_restore``) passes ``True`` — the
              value :func:`dbas.importers.channels.import_channels` already uses
              on that path, and the one the ``…-2o0cz`` rebind was designed
              around: on a fresh restore the destination has no provider streams
              at channel-import time, so the rebind is where essentially ALL of
              a restore's matching actually happens. Flooring it would strand
              restored channels on placeholders — the P0 this pass exists to fix.
            * the CROSS-INSTANCE SYNC (``tasks.dbas_sync_engine``) passes the
              per-``SyncTarget`` ``fuzzy_stream_matching`` flag (default OFF),
              the enforcement point for spike ``xp6mp`` ruling 1b.

            ``True`` is the default only so the many call sites that never reach
            the rebind (dry-runs, registries with no channels step) need not
            restate a policy they do not exercise; the value is REQUIRED at
            :func:`dbas.placeholder_rebind.rebind_placeholder_streams`, the
            boundary that consumes it, where omission was what made the defect
            silent.

    Returns:
        The :class:`RestoreReport` with its tri-state ``outcome`` set.
    """
    # --- 0. Default-ON, UNBYPASSABLE dry-run guardrail. ---
    # Apply is opt-IN. Absent an explicit confirm, the run degrades to a dry-run
    # and makes ZERO mutations — the importers, rollback, and deferred phase all
    # branch on ``report.is_dry_run`` below, so forcing it here is the single,
    # architectural enforcement point. There is NO path that mutates without
    # confirm_apply=True; a caller can never opt OUT of the dry-run.
    if not confirm_apply and not report.is_dry_run:
        report.is_dry_run = True
        report.notes.append(
            "apply not confirmed (confirm_apply=False) — produced a counts-only "
            "dry-run; no mutation performed."
        )
        logger.info(
            "[DBAS-RESTORE] Apply NOT confirmed; forcing counts-only dry-run "
            "(default-ON guardrail)."
        )

    report.started_at = report.started_at or datetime.now(timezone.utc)

    # --- 1. Pre-flight — refuse with ZERO mutation on failure. ---
    preflight_kwargs = {}
    if max_entities_per_category is not None:
        preflight_kwargs["max_entities_per_category"] = max_entities_per_category
    preflight: PreflightResult = run_preflight(plan, **preflight_kwargs)
    if not preflight.passed:
        for problem in preflight.problems:
            report.notes.append(f"pre-flight refused: {problem.message}")
        report.outcome = None  # nothing was applied — a plan has no realized outcome
        report.completed_at = datetime.now(timezone.utc)
        logger.warning(
            "[DBAS-RESTORE] Restore REFUSED by pre-flight (%d problem(s)); no mutation performed.",
            len(preflight.problems),
        )
        return report

    if plan_is_dry_run := report.is_dry_run:
        logger.info("[DBAS-RESTORE] Dry-run: pre-flight passed; no apply performed.")

    # Hand the shared remap this run's SCOPE (bead …-4mkoe). This is the only
    # place that holds both the plan and the table every importer resolves
    # through, which is why it is recorded here rather than threaded into five
    # importer signatures. Without it a remap answers "was this category ever
    # going to be populated?" with "I was not told", and every unresolved
    # dependency is reported as a loss — the fail-loud default, on purpose.
    _record_run_scope(plan, remap)

    # Per-create durable flush: on a real apply, persist the shared ledger after
    # each ``record_created`` (importers call ``ctx.flush_ledger()``); on a
    # dry-run nothing is created, so the flush is a no-op that never touches the
    # ledger path. This makes the worst-case crash window a single in-flight
    # create rather than a whole category (RollbackLedger durability contract —
    # bead l1p4p).
    if report.is_dry_run:
        per_create_persist: Callable[[], None] = lambda: None
    else:
        def per_create_persist() -> None:
            persist_ledger(ledger, ledger_dir=ledger_dir)

    ctx = ApplyContext(
        plan=plan,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=report.is_dry_run,
        persist_ledger=per_create_persist,
        channel_reattach_mode=channel_reattach_mode,
    )

    # --- 2. Ordered apply (the hard Phase-2 sequence). ---
    failure_occurred = False
    failed_step: EntityType | None = None
    for step in steps:
        if step.importer is None:
            logger.info(
                "[DBAS-RESTORE] No importer registered for %s — registration seam, skipped.",
                step.entity_type.value,
            )
            continue
        try:
            deferred = await step.importer(ctx)
        except Exception as exc:  # noqa: BLE001 - any importer failure triggers rollback
            failure_occurred = True
            failed_step = step.entity_type
            logger.error(
                "[DBAS-RESTORE] Importer step %s raised; triggering rollback. (%s)",
                step.entity_type.value,
                type(exc).__name__,
            )
            # A dry-run never creates, so there is nothing durable to persist — and
            # it must make ZERO disk writes to the ledger path.
            if not report.is_dry_run:
                persist_ledger(ledger, ledger_dir=ledger_dir)
            break

        if deferred:
            ctx.deferred.extend(deferred)
        if not report.is_dry_run:
            persist_ledger(ledger, ledger_dir=ledger_dir)

        # A step that reports a category failure (without raising) also rolls back
        # — mixed state must never be reported as success.
        cat = report.category(step.entity_type)
        if cat.failed > 0:
            if step.entity_type in NON_FATAL_FAILURE_CATEGORIES:
                # y65si / d0agi: counted, surfaced, and NOT fatal. Nothing
                # downstream depends on this category, so the operator keeps
                # everything the run has applied and will apply. The failure
                # still forbids a SUCCESS outcome (compute_outcome reads
                # report.categories).
                logger.warning(
                    "[DBAS-RESTORE] Importer step %s reported %d failure(s); "
                    "category is NON-FATAL — continuing without rollback.",
                    step.entity_type.value,
                    cat.failed,
                )
                report.notes.append(
                    f"{cat.failed} {step.entity_type.value} row(s) could not be "
                    "restored; this category is non-fatal, so the rest of the "
                    "restore was applied and nothing was rolled back."
                )
                continue
            failure_occurred = True
            failed_step = step.entity_type
            logger.error(
                "[DBAS-RESTORE] Importer step %s reported %d failure(s); triggering rollback.",
                step.entity_type.value,
                cat.failed,
            )
            break

    # --- 3a. Rollback on failure. ---
    rollback: RollbackResult | None = None
    if failure_occurred and not report.is_dry_run:
        report.notes.append(
            f"restore failed at category {failed_step.value if failed_step else 'unknown'}; "
            "compensating rollback ran."
        )
        rollback = await run_rollback(ledger=ledger, client=client, ledger_dir=ledger_dir)
        if rollback.complete:
            report.notes.append(f"rollback completed: {len(rollback.compensated)} entity/entities removed.")
        else:
            report.notes.append(
                f"rollback INCOMPLETE: {len(rollback.residue)} entity/entities could not be removed — "
                "manual cleanup required."
            )
        # Settings are config, not created entities — they are never ledgered
        # and CANNOT be compensated (documented limitation, settings_agents.py).
        # If any were applied before the failure, say so LOUDLY rather than let
        # "rollback completed" read as a full undo (kxcjf).
        settings_cat = next(
            (c for c in report.categories if c.entity_type == EntityType.SETTINGS), None
        )
        if settings_cat is not None and settings_cat.updated > 0:
            report.notes.append(
                f"NOTE: {settings_cat.updated} applied setting(s) were NOT rolled back — "
                "settings changes are not compensatable and remain applied."
            )
        # zt3kf (PO decision 2026-08-03, rollback policy — see the module
        # docstring's ABORT-ON-ANY-FAILED-KEY section): when the category that
        # aborted the restore is SETTINGS and its failure is
        # DEPENDENCY_UNRESOLVED, the archive references a settings key the
        # destination does not have. Retrying the SAME restore against the
        # SAME destination will fail identically — say so explicitly instead
        # of leaving the operator to guess whether a retry might help.
        if settings_cat is not None and any(
            fd.reason == FailureReason.DEPENDENCY_UNRESOLVED
            for fd in settings_cat.failure_details
        ):
            report.notes.append(
                "NOTE: this restore failed because one or more settings keys "
                "in the archive do not exist on this destination "
                "(DEPENDENCY_UNRESOLVED) — retrying the same restore will "
                "fail the same way. Edit the category selection to exclude "
                "Settings, or restore against a destination whose "
                "Dispatcharr version has those keys."
            )

    # --- 3b. Deferred phase (clean run only) — applied LAST. ---
    if not failure_occurred and not report.is_dry_run and ctx.deferred:
        apply_fn = deferred_apply_fn or _default_deferred_apply_fn()
        try:
            # ``remap`` / ``report`` are threaded in (bead …-2o0cz): the deferred
            # group-settings apply rewrites each archived SOURCE channel-group pk
            # to its DESTINATION pk, and the deferred phase is the first point in
            # the run where the CHANNEL_GROUP namespace is populated. A custom
            # apply fn that predates these kwargs still works — see
            # ``_call_deferred_apply``.
            applied = await _call_deferred_apply(
                apply_fn, deferred=ctx.deferred, client=client, remap=remap, report=report
            )
            # Count what the apply fn ACTUALLY applied (its per-account
            # summaries), not what was queued: the sync path's injected
            # suppressor (ADR-013 S9) returns [] and the report must not claim
            # an apply that never happened (bead 7ipq2.2 live-validation
            # finding). The default fn returns one summary per account, so the
            # restore-path note is unchanged.
            if applied:
                report.notes.append(
                    f"deferred auto-sync applied for {len(applied)} account(s)."
                )
        except Exception:  # noqa: BLE001 - deferred apply is best-effort, post-create
            logger.warning(
                "[DBAS-RESTORE] Deferred auto-sync phase hit an error; created entities are intact."
            )
            report.notes.append("deferred auto-sync phase reported an error; entities intact.")

    # --- 3c. Post-refresh placeholder rebind (bead …-2o0cz, P0). ------------
    # THE step that makes a restored lineup play. At channel-import time the
    # destination has no provider streams yet, so every archived stream MISSes
    # the matcher and is synthesized as a URL-less placeholder. The deferred
    # phase above is what finally materializes the real provider streams — so
    # this is the first (and only) moment the matcher can be re-run against
    # something real. The drill proved that without it a restore reports
    # "success, 0 failures" for an instance where not one channel can play, and
    # that the documented manual recovery (an M3U refresh) does NOT rebind.
    #
    # Runs only on a clean, non-dry-run apply: a dry-run mutates nothing, and a
    # failed run is about to be rolled back.
    if report.is_dry_run:
        # A preview cannot run the pass, so it must not report the pass's
        # verdict as ``0`` (bead …-dgnms). Drill run 4 measured a fresh-target
        # preview reporting ``channels_needing_stream_reattach: 0`` /
        # ``channels_with_no_playable_stream: 0`` where the apply, minutes later,
        # reported 12 and 12. The number is not knowable without the deferred
        # refresh this preview deliberately does not perform, so the honest
        # report is NULL — "not predicted" — rather than a confident zero.
        report.mark_stream_health_unpredicted()
    elif not failure_occurred:
        await _rebind_placeholders(plan=plan, client=client, report=report,
                                   ledger=ledger, remap=remap,
                                   allow_fuzzy=allow_fuzzy_stream_match)

    # --- 4. Outcome. ---
    # A DRY-RUN is a plan, not a realized restore — it has no outcome (kxuj2
    # contract: ``outcome`` is None on a dry-run). Only an apply computes the
    # tri-state outcome.
    if report.is_dry_run:
        report.outcome = None
    else:
        report.outcome = compute_outcome(
            report=report,
            failure_occurred=failure_occurred,
            rollback=rollback,
        )
    report.completed_at = datetime.now(timezone.utc)

    if report.outcome == RestoreOutcome.SUCCESS and not report.is_dry_run:
        # Clean success — no compensation will ever be needed; drop the ledger.
        delete_ledger(ledger.restore_id, ledger_dir=ledger_dir)

    logger.info("[DBAS-RESTORE] Restore complete; outcome=%s.", report.outcome.value if report.outcome else "none")
    return report


def _would_create_logo_ids(logo_result: object) -> set[int] | None:
    """The would-create SOURCE logo ids from a logos-importer result, or ``None``.

    Bead ``…-dgnms``. ``import_logos`` is stubbed in several suites and by the
    sync engine's step overrides, so its return value is not always a
    :class:`~dbas.importers.logos.LogoImportResult`. Anything that is not a
    genuine set of ints yields ``None`` — "no would-create information" — which
    puts the logo reattach pass back on the remap alone.
    """
    ids = getattr(logo_result, "would_create_source_ids", None)
    if not isinstance(ids, (set, frozenset)):
        return None
    return {value for value in ids if isinstance(value, int) and not isinstance(value, bool)}


async def _rebind_placeholders(
    *,
    plan: ImportPlan,
    client: DispatcharrClient,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: object,
    allow_fuzzy: bool,
) -> None:
    """Run the post-refresh placeholder rebind, containing any error.

    Imported lazily (one-way dependency direction, mirroring
    :func:`_default_deferred_apply_fn`). The pass is post-create cleanup on an
    otherwise-successful restore, so an error here is logged and noted — it never
    turns a successful restore into a rollback.

    ``allow_fuzzy`` is threaded, never defaulted (bead ``…-efvyg``): the rebind
    is a matcher pass like the channels importer's, so it runs under the SAME
    stream-matching policy this run's importers ran under. See
    :func:`run_restore`'s ``allow_fuzzy_stream_match`` for where that policy
    comes from on each path.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    channel_cat = plan.category(EntityType.CHANNEL)
    archive_channels = list(channel_cat.entities) if channel_cat else []
    if not archive_channels:
        return
    try:
        await rebind_placeholder_streams(
            client=client,
            report=report,
            ledger=ledger,
            remap=remap,
            archive_channels=archive_channels,
            allow_fuzzy=allow_fuzzy,
        )
    except Exception:  # noqa: BLE001 - best-effort post-create cleanup
        logger.warning(
            "[DBAS-RESTORE] Placeholder rebind pass hit an error; restored "
            "entities are intact but some channels may still be bound to "
            "placeholder streams."
        )
        report.notes.append(
            "the post-refresh stream rebind reported an error; verify that each "
            "restored channel has a playable stream attached."
        )


async def _call_deferred_apply(
    apply_fn: Callable[..., Awaitable[list[dict]]],
    *,
    deferred: list[dict],
    client: DispatcharrClient,
    remap: object,
    report: RestoreReport,
) -> list[dict]:
    """Invoke a deferred-apply fn, passing only the kwargs it accepts.

    The default fn (``m3u_accounts.apply_deferred_auto_sync``) takes ``remap`` and
    ``report``; the sync engine's ADR-013 S9 suppressor and any test-injected
    stub predate them and take only ``deferred`` / ``client``. Inspecting the
    signature keeps this ONE call site compatible with both rather than forcing
    every injected fn to grow a ``**kwargs`` tail it has no use for.
    """
    import inspect

    kwargs: dict = {"deferred": deferred, "client": client}
    try:
        params = inspect.signature(apply_fn).parameters
    except (TypeError, ValueError):  # pragma: no cover — builtins/C callables
        params = {}
    accepts_any = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    for name, value in (("remap", remap), ("report", report)):
        if accepts_any or name in params:
            kwargs[name] = value
    return await apply_fn(**kwargs)


def _default_deferred_apply_fn() -> Callable[..., Awaitable[list[dict]]]:
    """Return the default deferred-apply coroutine (M3U auto-sync).

    Imported lazily so the orchestrator module does not pull the importer package
    at import time (one-way dependency direction).
    """
    from dbas.importers.m3u_accounts import apply_deferred_auto_sync

    return apply_deferred_auto_sync


def new_restore_id() -> str:
    """A fresh unique restore id (names the durable ledger file)."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Default importer registry — the FULL apply wiring (bead kxcjf)
# ---------------------------------------------------------------------------


def _epg_step_with_download_wait(epg_importer: ImporterCallable) -> ImporterCallable:
    """Wrap the shared EPG step with the bounded EPG-data download wait (APPLY only).

    Bead ``kxcjf`` folds in the unmet ``0i2vt.11`` acceptance item: after the
    EPG-sources importer creates sources on the destination, Dispatcharr
    downloads their EPG data asynchronously. The Channels importer must not run
    before that download finishes, or Dispatcharr's channel↔EPG matching has no
    rows to match against. This wrapper runs
    :func:`dbas.importers.epg_sources.wait_for_epg_downloads` (a bounded,
    non-fatal 2-stage trigger+poll mirroring the M3U deferred-auto-sync poll)
    over the sources CREATED this run (read from the shared ledger — an
    already-existing, skipped source has its data already).

    Apply-registry only, deliberately:

    * On a dry-run the wrapper is a pass-through (zero waiting, zero triggers —
      a plan must stay read-only and fast).
    * The sync registry (``tasks.dbas_sync_engine``) uses the UNWRAPPED shared
      ``epg`` builder — a per-cycle sync must never re-trigger EPG downloads on
      the destination (ADR-013 S9).

    A source that does not finish within the bounded wait is surfaced as a
    WARN-level :class:`RestoreReport` note — never a hang, never a failure
    (channels still restore; only upstream EPG matching may be incomplete).
    """

    async def _epg_apply(ctx: ApplyContext) -> list[dict] | None:
        from dbas.importers.epg_sources import wait_for_epg_downloads

        result = await epg_importer(ctx)
        if ctx.is_dry_run:
            return result
        created_ids = [
            entry.destination_id
            for entry in ctx.ledger.entries
            if entry.entity_type == EntityType.EPG_SOURCE
        ]
        if not created_ids:
            return result
        summaries = await wait_for_epg_downloads(
            source_ids=created_ids, client=ctx.client
        )
        for summary in summaries:
            if not summary.get("completed"):
                ctx.report.notes.append(
                    "EPG source id=%s: EPG data download did not finish within the "
                    "bounded wait; channel EPG matching may be incomplete."
                    % summary.get("epg_source_id")
                )
        return result

    return _epg_apply


def default_importer_steps() -> list[ImporterStep]:
    """The hard Phase-2 ordering with EVERY importer WIRED for the real apply.

    Bead ``kxcjf`` closed the silent-skip defect: this registry previously wired
    only M3U accounts / users / channels and left EPG sources, channel
    groups/profiles/stream profiles, user agents, DVR rules, settings, and logos
    as ``importer=None`` seams — a confirmed apply silently no-opped those
    categories while the default-ON dry-run preview promised their counts. Both
    registries now cover the SAME category set (the dry-run/apply parity bar);
    ``dry_run_importer_steps`` mirrors this order exactly.

    Ordering (dependency-driven, ADR-012 D-table):

      * user agents FIRST (bead ``…-9h6cv``) — a leaf that resolves nothing,
        and the namespace BOTH the M3U account's and the stream profile's
        ``user_agent`` FK resolve through. Anything ahead of it meets an empty
        namespace.
      * M3U accounts next (defers auto-sync to the final phase) — everything
        downstream remaps ``m3u_account`` FKs through it.
      * EPG sources next, WITH the bounded EPG-data download wait
        (:func:`_epg_step_with_download_wait`) so Dispatcharr has EPG rows
        before channels are created.
      * channel groups / channel profiles / stream profiles before channels —
        they populate the IdRemapTable namespaces the channels importer resolves.
        The stream profiles' ``user_agent`` FK is why the agents lead the list
        (bead ``…-lvfwd``): the reverse order POSTed a raw source id and aborted
        the whole restore on a fresh destination. Bead ``…-9h6cv`` found the M3U
        account carries the same FK, so the agents moved ahead of it too.
      * settings (core settings / comskip) before channels (config in place
        before the big entity category), then ECM's OWN settings.json — a
        SEPARATE category (bead …-dfkbn item 4), because the drill's report said
        ``settings updated=7`` while ECM's ``user_timezone`` and
        ``stats_poll_interval`` silently reverted: that count was Dispatcharr's
        namespace, and ECM's own blob had no importer at all.
      * users before channels (the l1p4p slot; unchanged) — and AFTER channel
        profiles, which is now load-bearing rather than incidental: a user's
        ``channel_profiles`` list is a LIST-VALUED FK remapped through the
        CHANNEL_PROFILE namespace (bead ``…-if05f``). Both registries already
        ordered profiles ahead of users; moving USER above CHANNEL_PROFILE would
        meet an empty namespace and skip every profile-scoped user.
      * channels, then DVR rules (a DVR rule's ``channel`` FK remaps through the
        just-populated ``EntityType.CHANNEL`` namespace), then logos LAST
        (attach to the created channels; slow streaming uploads at the tail).

    Two categories also run a POST-CREATE REATTACH pass inside their own step
    (bead ``…-dfkbn``), because the reference they restore is a SOURCE id that
    the create payload has to drop: channels reattach their EPG link (by the
    archived ``tvg_id``) and re-assert their archived channel-profile selection;
    logos reattach each channel's ``logo_id``. Both run only on an apply, after
    their importer, when the remap namespaces they resolve through are populated.
    See :mod:`dbas.channel_reattach`.

    PLUGINS stay excluded per ADR-012 D10 (RCE-vs-config unresolved) — there is
    deliberately no plugins row in either registry.
    """
    s = _importer_step_builders()
    return [
        # USER AGENTS FIRST (…-9h6cv). A user agent resolves nothing through the
        # remap, while BOTH the M3U account and the stream profile carry a
        # ``user_agent`` FK that resolves through the USER_AGENT namespace
        # (lvfwd for the profile, 9h6cv for the account). Ordering agents ahead
        # of every consumer is the only arrangement in which no consumer meets an
        # empty namespace. It also puts the agents FIRST in the rollback ledger,
        # so a compensating rollback deletes the accounts/profiles that reference
        # them before the agents themselves.
        ImporterStep(EntityType.USER_AGENT, s["user_agents"]),
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=True),
        ImporterStep(EntityType.EPG_SOURCE, _epg_step_with_download_wait(s["epg"])),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        ImporterStep(EntityType.SETTINGS, s["settings"]),
        # ECM's OWN settings.json (…-dfkbn item 4) — a DIFFERENT namespace from
        # the Dispatcharr core settings above. Config before entities, same as
        # its sibling.
        ImporterStep(EntityType.ECM_SETTINGS, s["ecm_settings"]),
        ImporterStep(EntityType.USER, s["users"]),
        ImporterStep(EntityType.CHANNEL, s["channels"]),
        ImporterStep(EntityType.DVR_RULE, s["dvr_rules"]),
        # …-ciabe. AFTER the DVR rules and after CHANNEL, whose namespace a
        # recording's only FK resolves through. A pure leaf — nothing holds a
        # reference into it — so it is also in NON_FATAL_FAILURE_CATEGORIES.
        ImporterStep(EntityType.UPCOMING_RECORDING, s["upcoming_recordings"]),
        ImporterStep(EntityType.LOGO, s["logos"]),
    ]


# ---------------------------------------------------------------------------
# Shared per-category step builders — ONE set of callables backs the apply
# registry, the dry-run registry, and the sync engine's config-only registry.
# ---------------------------------------------------------------------------


def _importer_step_builders() -> dict[str, ImporterCallable]:
    """Build the per-category importer-step callables, shared by both registries.

    Each callable adapts one importer's keyword signature to the
    :class:`ApplyContext`. The SAME callables back the apply registry
    (:func:`default_importer_steps`) and the dry-run registry
    (:func:`dry_run_importer_steps`); they thread ``ctx.is_dry_run`` straight into
    each importer so the dry-run count comes from the importer's OWN plan/match
    logic (the same code that decides create/update/skip on apply), never a
    parallel counter. This is the anti-drift guarantee the parity test rests on.
    """
    from dbas.importers.channels import import_channels
    from dbas.importers.epg_sources import import_epg_sources
    from dbas.importers.groups_profiles import (
        import_channel_groups,
        import_channel_profiles,
        import_stream_profiles,
    )
    from dbas.importers.logos import import_logos
    from dbas.importers.m3u_accounts import import_m3u_accounts
    from dbas.importers.settings_agents import (
        CoreSettingIdResolver,
        import_comskip,
        import_core_settings,
        import_dvr_rules,
        import_upcoming_recordings,
        import_user_agents,
    )
    from dbas.importers.users import import_users

    def _entities(ctx: ApplyContext, entity_type: EntityType) -> list[dict]:
        cat = ctx.plan.category(entity_type)
        return list(cat.entities) if cat else []

    def _selected(ctx: ApplyContext, entity_type: EntityType) -> bool:
        cat = ctx.plan.category(entity_type)
        return bool(cat.selected) if cat else False

    async def _m3u(ctx: ApplyContext) -> list[dict] | None:
        result = await import_m3u_accounts(
            archive_accounts=_entities(ctx, EntityType.M3U_ACCOUNT),
            client=ctx.client,
            selected=_selected(ctx, EntityType.M3U_ACCOUNT),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return result.deferred_auto_sync_settings or None

    async def _epg(ctx: ApplyContext) -> list[dict] | None:
        await import_epg_sources(
            archive_sources=_entities(ctx, EntityType.EPG_SOURCE),
            client=ctx.client,
            selected=_selected(ctx, EntityType.EPG_SOURCE),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _channel_groups(ctx: ApplyContext) -> list[dict] | None:
        await import_channel_groups(
            archive_rows=_entities(ctx, EntityType.CHANNEL_GROUP),
            client=ctx.client,
            selected=_selected(ctx, EntityType.CHANNEL_GROUP),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _channel_profiles(ctx: ApplyContext) -> list[dict] | None:
        await import_channel_profiles(
            archive_rows=_entities(ctx, EntityType.CHANNEL_PROFILE),
            client=ctx.client,
            selected=_selected(ctx, EntityType.CHANNEL_PROFILE),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _stream_profiles(ctx: ApplyContext) -> list[dict] | None:
        await import_stream_profiles(
            archive_rows=_entities(ctx, EntityType.STREAM_PROFILE),
            client=ctx.client,
            selected=_selected(ctx, EntityType.STREAM_PROFILE),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _user_agents(ctx: ApplyContext) -> list[dict] | None:
        await import_user_agents(
            archive_user_agents=_entities(ctx, EntityType.USER_AGENT),
            client=ctx.client,
            selected=_selected(ctx, EntityType.USER_AGENT),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _dvr_rules(ctx: ApplyContext) -> list[dict] | None:
        await import_dvr_rules(
            archive_dvr_rules=_entities(ctx, EntityType.DVR_RULE),
            client=ctx.client,
            selected=_selected(ctx, EntityType.DVR_RULE),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _upcoming_recordings(ctx: ApplyContext) -> list[dict] | None:
        await import_upcoming_recordings(
            archive_recordings=_entities(ctx, EntityType.UPCOMING_RECORDING),
            client=ctx.client,
            selected=_selected(ctx, EntityType.UPCOMING_RECORDING),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _settings(ctx: ApplyContext) -> list[dict] | None:
        # The SETTINGS plan slice carries the key/value blobs, not entity rows.
        # Contract: each entity is ``{"section": "core_settings"|"comskip",
        # "values": {...}}`` — self-describing so one plan category carries both
        # blobs in a fixed apply order. Results land on the shared
        # ``EntityType.SETTINGS`` report category (updated/skipped, never
        # created, never ledgered — settings rollback is out of scope, see
        # ``settings_agents.py``).
        # ONE key->row-id resolver for the whole step: core_settings and comskip
        # share Dispatcharr's single core-settings namespace, whose detail route
        # is keyed by integer pk, so the apply run costs one
        # GET /api/core/settings/ (bead …-q6xjl).
        id_resolver = CoreSettingIdResolver(ctx.client)
        selected = _selected(ctx, EntityType.SETTINGS)
        for record in _entities(ctx, EntityType.SETTINGS):
            section = record.get("section")
            values = record.get("values") or {}
            if section == "core_settings":
                await import_core_settings(
                    archive_core_settings=values,
                    client=ctx.client,
                    selected=selected,
                    report=ctx.report,
                    ledger=ctx.ledger,
                    is_dry_run=ctx.is_dry_run,
                    id_resolver=id_resolver,
                )
            elif section == "comskip":
                await import_comskip(
                    archive_comskip=values,
                    client=ctx.client,
                    selected=selected,
                    report=ctx.report,
                    ledger=ctx.ledger,
                    is_dry_run=ctx.is_dry_run,
                    id_resolver=id_resolver,
                )
            else:
                logger.warning(
                    "[DBAS-RESTORE] Unknown settings section %r in plan; skipped.",
                    section,
                )
        return None

    async def _users(ctx: ApplyContext) -> list[dict] | None:
        await import_users(
            archive_users=_entities(ctx, EntityType.USER),
            client=ctx.client,
            selected=_selected(ctx, EntityType.USER),
            report=ctx.report,
            ledger=ctx.ledger,
            # …-if05f: a user's channel_profiles list is remapped through the
            # CHANNEL_PROFILE namespace, which the step above this one populates.
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            persist_ledger=ctx.flush_ledger,
        )
        return None

    async def _channels(ctx: ApplyContext) -> list[dict] | None:
        archive_channels = _entities(ctx, EntityType.CHANNEL)
        await import_channels(
            archive_channels=archive_channels,
            client=ctx.client,
            selected=_selected(ctx, EntityType.CHANNEL),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            # Populated on BOTH a dry run and an apply, so the reattach passes
            # split the two channel populations identically in either mode.
            created_source_ids=ctx.created_channel_source_ids,
            # The matched pre-existing rows the channel-group reconcile pass
            # below reads the destination's current grouping from (…-r1ei7).
            matched_existing_channels=ctx.matched_existing_channels,
        )
        # Post-create reattachment (bead …-dfkbn items 2-3). Both references are
        # DROPPED from the channel create payload because they carry SOURCE ids;
        # these passes re-derive them on the destination now that the CHANNEL
        # remap is populated.
        #
        # The EPG pass ALSO runs on a dry run, because "how many links would
        # land on channels I ALREADY have" is the number that decides whether the
        # operator wants ``overwrite`` at all, and it is useless after the fact.
        # It never PATCHes and it never records a miss.
        #
        # It does NOT read the destination's guide on a dry run. That guide is
        # state the restore ITSELF creates (the EPG step above downloads it, and
        # on a dry run that wrapper is a pass-through), so a preview reading it
        # reads the pre-restore guide and reports a working restore as a total
        # failure. It DOES read the CHANNEL remap, which this same run's channels
        # importer has just populated. The distinction is "state that already
        # exists" versus "state this restore creates", and it is the same line
        # the logo pass draws. See dbas/channel_reattach.py for the full
        # reasoning and the measurements behind it.
        #
        # The profile pass stays apply-only: it has no read-only prediction to
        # offer beyond what the archive already says.
        if _selected(ctx, EntityType.CHANNEL):
            from dbas.channel_reattach import (
                CHANNEL_GROUPS_NOT_CHECKED_NOTE,
                reattach_epg_links,
                reattach_profile_memberships,
                reconcile_channel_groups,
            )

            # Channel -> GROUP membership (bead …-r1ei7). Runs here, after every
            # channel is created or matched, because a group's membership is not
            # on the group row — it is the ``channel_group_id`` on each channel,
            # and the groups importer runs BEFORE channels, when the destination
            # has no membership to compare yet.
            #
            # Gated on the CHANNEL_GROUP category as well: with groups
            # deselected, no archived group resolves through the remap, and every
            # matched channel would report drift this restore was never asked to
            # touch. Same gate shape as the profile pass below.
            #
            # SKIPPING IT IS NOT THE SAME AS FINDING NOTHING. A skipped pass
            # leaves ``channel_group_drift`` at its ``0`` default, and a zero
            # beside every other counter reads as "your grouping is fine" — the
            # exact silent-clean-report failure these beads were filed over. So
            # the skip says so, in the report, in one sentence that reaches the
            # restore panel and the task one-liner alike. It does NOT try to
            # compute drift here: there is no honest number to compute.
            #
            # Runs on a dry run TOO, and PATCHes nothing there: "how many of my
            # channels would replace move into a different group" is the number
            # that decides the mode, and it is useless after the fact.
            if _selected(ctx, EntityType.CHANNEL_GROUP):
                await reconcile_channel_groups(
                    client=ctx.client,
                    report=ctx.report,
                    remap=ctx.remap,
                    archive_channels=archive_channels,
                    archive_channel_groups=_entities(ctx, EntityType.CHANNEL_GROUP),
                    matched_existing_channels=ctx.matched_existing_channels,
                    created_source_ids=ctx.created_channel_source_ids,
                    mode=ctx.channel_reattach_mode,
                    is_dry_run=ctx.is_dry_run,
                )
            else:
                ctx.report.channel_group_drift_note = CHANNEL_GROUPS_NOT_CHECKED_NOTE

            await reattach_epg_links(
                client=ctx.client,
                report=ctx.report,
                remap=ctx.remap,
                archive_channels=archive_channels,
                mode=ctx.channel_reattach_mode,
                created_source_ids=ctx.created_channel_source_ids,
                is_dry_run=ctx.is_dry_run,
            )
            # Dispatcharr adds every new channel to EVERY profile enabled, so a
            # profile seeded to EXCLUDE channels silently widens to all of them
            # unless the archived selection is re-asserted here.
            #
            # Runs on a dry run TOO (bead …-dgnms). It was apply-only on the
            # grounds that it had "no read-only prediction to offer beyond what
            # the archive already says" — but what the archive says IS the
            # prediction: the flip set is the restored channels the archived
            # profile excludes, and the apply computes it from exactly the same
            # two inputs. Drill run 4 measured a preview reporting 0 for an apply
            # that reported 6, which silences the widening warning at the only
            # point the operator can still act on it.
            if _selected(ctx, EntityType.CHANNEL_PROFILE):
                await reattach_profile_memberships(
                    client=ctx.client,
                    report=ctx.report,
                    remap=ctx.remap,
                    archive_profiles=_entities(ctx, EntityType.CHANNEL_PROFILE),
                    archive_channels=archive_channels,
                    # Only the fail-closed path reads this (bead …-38c5a): a
                    # profile whose archived record never said what it enabled
                    # has its THIS-RUN-CREATED memberships disabled rather than
                    # left on Dispatcharr's enable-everything default, and a
                    # pre-existing channel's membership is left to the operator.
                    created_source_ids=ctx.created_channel_source_ids,
                    is_dry_run=ctx.is_dry_run,
                )
        return None

    async def _ecm_settings(ctx: ApplyContext) -> list[dict] | None:
        # ECM's OWN settings.json (bead …-dfkbn item 4) — distinct from the
        # SETTINGS category above, which is Dispatcharr's core-settings
        # namespace. The plan slice carries ONE record: {"values": {...}}.
        from dbas.importers.ecm_settings import import_ecm_settings

        selected = _selected(ctx, EntityType.ECM_SETTINGS)
        for record in _entities(ctx, EntityType.ECM_SETTINGS):
            await import_ecm_settings(
                archive_settings=record.get("values") or {},
                selected=selected,
                report=ctx.report,
                is_dry_run=ctx.is_dry_run,
            )
        return None

    async def _logos(ctx: ApplyContext) -> list[dict] | None:
        # clear_existing is the DESTRUCTIVE bulk-delete pre-step; the logos
        # importer itself guards it behind ``not is_dry_run``, and a dry-run plan
        # never carries an apply confirm — so it can never fire here on a dry-run.
        logo_result = await import_logos(
            archive_logos=_entities(ctx, EntityType.LOGO),
            client=ctx.client,
            selected=_selected(ctx, EntityType.LOGO),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            clear_existing=False,
            # Read-only channel context (bead cm9bi): each logo miss lists the
            # affected channels (archive channels whose logo_id referenced it),
            # with destination ids resolved through the CHANNEL remap the
            # channels step populated earlier in this same run.
            archive_channels=_entities(ctx, EntityType.CHANNEL),
        )
        # Put each restored channel's logo BACK on it (bead …-dfkbn item 1).
        # ``logo_id`` is dropped from the channel create payload (source id), and
        # before this pass nothing re-attached it: every restored channel came
        # back with logo_id=None while the report said logo_misses=0. Runs here,
        # after the logos importer, because that is what populates the LOGO
        # remap namespace this resolves through.
        #
        # Runs on a dry run TOO, reporting the same split and no miss. It DOES
        # resolve the LOGO remap there: the logos importer registers a
        # destination id for every archived logo it MATCHES, on a dry run as much
        # as on an apply, and for a merge into a live install that matched
        # population IS the population.
        #
        # It also counts the logos the preview knows the apply would CREATE, via
        # the source-id set the importer just returned (bead …-dgnms). On a FRESH
        # target nothing matches, so that set is the entire population and
        # without it the preview reported 0 channels for an apply that reattached
        # 11. No destination id is invented for them. See reattach_channel_logos.
        if _selected(ctx, EntityType.LOGO):
            from dbas.channel_reattach import reattach_channel_logos

            await reattach_channel_logos(
                client=ctx.client,
                report=ctx.report,
                remap=ctx.remap,
                archive_channels=_entities(ctx, EntityType.CHANNEL),
                mode=ctx.channel_reattach_mode,
                created_source_ids=ctx.created_channel_source_ids,
                is_dry_run=ctx.is_dry_run,
                # Coerced defensively: the importer is stubbed in several suites,
                # and a stub's return value is not a LogoImportResult.
                would_create_logo_source_ids=_would_create_logo_ids(logo_result),
            )
        return None

    return {
        "m3u": _m3u,
        "ecm_settings": _ecm_settings,
        "epg": _epg,
        "channel_groups": _channel_groups,
        "channel_profiles": _channel_profiles,
        "stream_profiles": _stream_profiles,
        "user_agents": _user_agents,
        "dvr_rules": _dvr_rules,
        "upcoming_recordings": _upcoming_recordings,
        "settings": _settings,
        "users": _users,
        "channels": _channels,
        "logos": _logos,
    }


def dry_run_importer_steps() -> list[ImporterStep]:
    """The Phase-2 ordering with EVERY importer WIRED for the counts-only dry-run.

    Bead ``…-0i2vt.16`` (extended by ``kxcjf``). Mirrors
    :func:`default_importer_steps` category-for-category and in the SAME order —
    the dry-run/apply parity contract: the counts the operator previews are
    produced by the same importers, over the same category set, that a confirmed
    apply runs. Every importer is provably zero-mutation on a dry-run (it only
    reads to plan and increments ``would_*``).

    The only deliberate difference from the apply registry is the EPG step: the
    dry-run uses the plain importer (no download trigger, no wait — a plan must
    stay read-only and fast), while the apply wraps it with the bounded
    EPG-data download wait (:func:`_epg_step_with_download_wait`).
    """
    s = _importer_step_builders()
    return [
        # Same FK ordering as the apply registry (lvfwd, …-9h6cv) — a preview
        # that ordered these differently would promise an M3U-account or
        # stream-profile outcome the apply cannot deliver.
        ImporterStep(EntityType.USER_AGENT, s["user_agents"]),
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=True),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        ImporterStep(EntityType.SETTINGS, s["settings"]),
        # ECM's OWN settings.json (…-dfkbn item 4) — a DIFFERENT namespace from
        # the Dispatcharr core settings above. Config before entities, same as
        # its sibling.
        ImporterStep(EntityType.ECM_SETTINGS, s["ecm_settings"]),
        ImporterStep(EntityType.USER, s["users"]),
        ImporterStep(EntityType.CHANNEL, s["channels"]),
        ImporterStep(EntityType.DVR_RULE, s["dvr_rules"]),
        # …-ciabe. AFTER the DVR rules and after CHANNEL, whose namespace a
        # recording's only FK resolves through. A pure leaf — nothing holds a
        # reference into it — so it is also in NON_FATAL_FAILURE_CATEGORIES.
        ImporterStep(EntityType.UPCOMING_RECORDING, s["upcoming_recordings"]),
        ImporterStep(EntityType.LOGO, s["logos"]),
    ]


async def run_dry_run(
    *,
    plan: ImportPlan,
    client: DispatcharrClient,
    steps: list[ImporterStep] | None = None,
    ledger_dir: Path | None = None,
    max_entities_per_category: int = None,  # type: ignore[assignment]
    channel_reattach_mode: ChannelReattachMode = ChannelReattachMode.PRESERVE,
) -> RestoreReport:
    """Produce the counts-only restore PLAN for an archive — never mutates.

    Bead ``…-0i2vt.16``. The default-ON entry: the restore UX ALWAYS calls this
    first so the operator sees "would create N / update M / skip K" before any
    apply. It runs every importer with dry-run on (``dry_run_importer_steps``),
    aggregating each category's ``would_create`` / ``would_update`` / ``would_skip``
    into one :class:`RestoreReport` whose ``is_dry_run`` is True and whose
    ``outcome`` is ``None`` (a plan has no realized outcome).

    Because it delegates to :func:`run_restore` with ``confirm_apply=False`` and a
    dry-run report, the engine's guardrail guarantees ZERO mutation: no create,
    update, delete, upload, bulk-delete, rollback, or deferred auto-sync fires.

    Args:
        plan: The restore plan (categories + manifest + any pre-known remap).
        client: The Dispatcharr API client (only its READ methods are exercised).
        steps: Override the importer registry (tests / a future endpoint that
            shares the apply registry for the parity check). Defaults to
            :func:`dry_run_importer_steps`.
        ledger_dir: Override the durable ledger directory (tests). The dry-run
            never writes ledger entries, but pre-flight refusal paths share the
            signature.
        max_entities_per_category: Pre-flight count bound override (tests).
        channel_reattach_mode: The mode the operator has selected. The preview
            MUST be produced under the same mode the apply will run under, or it
            mispredicts exactly the number the mode exists to control.

    Returns:
        A :class:`RestoreReport` with ``is_dry_run=True`` carrying the per-category
        ``would_*`` counts, the ``logo_misses`` aggregate, and the
        ``epg_link_reattach`` / ``logo_reattach`` population splits.
    """
    report = RestoreReport(is_dry_run=True)
    ledger = RollbackLedger(restore_id=new_restore_id())
    from dbas.restore_contracts import IdRemapTable

    return await run_restore(
        plan=plan,
        client=client,
        steps=steps if steps is not None else dry_run_importer_steps(),
        report=report,
        ledger=ledger,
        remap=plan.existing_remap or IdRemapTable(),
        confirm_apply=False,
        ledger_dir=ledger_dir,
        max_entities_per_category=max_entities_per_category,
        channel_reattach_mode=channel_reattach_mode,
    )
