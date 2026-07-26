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
   failure), the orchestrator issues compensating DELETEs in
   :meth:`RollbackLedger.compensation_order` (reverse creation = reverse
   dependency order). A delete that 404s counts as SUCCESS (already gone); a
   non-404 delete error means the rollback is INCOMPLETE.
4. **Tri-state outcome** (:class:`~dbas.restore_contracts.RestoreOutcome`) is
   computed from what actually happened and is NEVER ``SUCCESS`` on mixed state.

----------------------------------------------------------------------------
HARD ORDERING (ADR-012 D-table) + the DEFERRED phase
----------------------------------------------------------------------------

Importers run in strict dependency order::

    M3U accounts → EPG sources (+ bounded EPG-data download wait)
      → channel groups/profiles/stream profiles → user agents / settings
      → users → channels → DVR rules → logos

then the DEFERRED phase applies LAST: the M3U importer returns auto-sync
settings that MUST NOT fire during the run (they race the logo import on the
Dispatcharr side). The orchestrator collects each importer's deferred settings
and applies them only after every category is done, via
``dbas.importers.m3u_accounts.apply_deferred_auto_sync``. EPG data is the
opposite: Dispatcharr's channel↔EPG matching needs the data BEFORE channels are
created, so the apply registry's EPG step waits (bounded, non-fatal) for the
download instead of deferring it — see :func:`_epg_step_with_download_wait`.

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
    EntityType,
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


def _report_has_failures(report: RestoreReport) -> bool:
    """True when any category in the report recorded at least one failure."""
    return any(cat.failed > 0 for cat in report.categories)


def compute_outcome(
    *,
    report: RestoreReport,
    failure_occurred: bool,
    rollback: RollbackResult | None,
) -> RestoreOutcome:
    """Derive the tri-state outcome — NEVER ``SUCCESS`` on mixed state.

    The single guard that the whole bead exists to enforce:

    * ``SUCCESS`` — only when NO failure occurred AND no category reported a
      failure AND no rollback was needed. Any whiff of failure forbids SUCCESS.
    * ``PARTIAL_FAILED_ROLLED_BACK`` — a failure occurred, a rollback ran, and it
      was COMPLETE (every created entity deleted or confirmed 404-gone).
    * ``FAILED_ROLLBACK_INCOMPLETE`` — a failure occurred and the rollback could
      not fully undo (non-404 delete error, or a type with no compensator). The
      worst state; reported loudly.

    Args:
        report: The shared restore report (its per-category failure counts are an
            independent signal that something failed).
        failure_occurred: Whether the apply phase raised / decided to roll back.
        rollback: The rollback result, or ``None`` if no rollback ran.

    Returns:
        The :class:`RestoreOutcome`.
    """
    mixed = failure_occurred or _report_has_failures(report)
    if not mixed:
        return RestoreOutcome.SUCCESS

    # A failure happened — SUCCESS is now impossible. Distinguish the two
    # rolled-back states by whether the rollback fully undid the created entities.
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
       state. On clean success the durable ledger file is removed.

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

    # --- 3b. Deferred phase (clean run only) — applied LAST. ---
    if not failure_occurred and not report.is_dry_run and ctx.deferred:
        apply_fn = deferred_apply_fn or _default_deferred_apply_fn()
        try:
            await apply_fn(deferred=ctx.deferred, client=client)
            report.notes.append(f"deferred auto-sync applied for {len(ctx.deferred)} account(s).")
        except Exception:  # noqa: BLE001 - deferred apply is best-effort, post-create
            logger.warning(
                "[DBAS-RESTORE] Deferred auto-sync phase hit an error; created entities are intact."
            )
            report.notes.append("deferred auto-sync phase reported an error; entities intact.")

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

      * M3U accounts first (defers auto-sync to the final phase) — everything
        downstream remaps ``m3u_account`` FKs through it.
      * EPG sources second, WITH the bounded EPG-data download wait
        (:func:`_epg_step_with_download_wait`) so Dispatcharr has EPG rows
        before channels are created.
      * channel groups / channel profiles / stream profiles before channels —
        they populate the IdRemapTable namespaces the channels importer resolves.
      * user agents + settings (core settings / comskip) before channels
        (config in place before the big entity category).
      * users before channels (the l1p4p slot; unchanged).
      * channels, then DVR rules (a DVR rule's ``channel`` FK remaps through the
        just-populated ``EntityType.CHANNEL`` namespace), then logos LAST
        (attach to the created channels; slow streaming uploads at the tail).

    PLUGINS stay excluded per ADR-012 D10 (RCE-vs-config unresolved) — there is
    deliberately no plugins row in either registry.
    """
    s = _importer_step_builders()
    return [
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=True),
        ImporterStep(EntityType.EPG_SOURCE, _epg_step_with_download_wait(s["epg"])),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        ImporterStep(EntityType.USER_AGENT, s["user_agents"]),
        ImporterStep(EntityType.SETTINGS, s["settings"]),
        ImporterStep(EntityType.USER, s["users"]),
        ImporterStep(EntityType.CHANNEL, s["channels"]),
        ImporterStep(EntityType.DVR_RULE, s["dvr_rules"]),
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
        import_comskip,
        import_core_settings,
        import_dvr_rules,
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

    async def _settings(ctx: ApplyContext) -> list[dict] | None:
        # The SETTINGS plan slice carries the key/value blobs, not entity rows.
        # Contract: each entity is ``{"section": "core_settings"|"comskip",
        # "values": {...}}`` — self-describing so one plan category carries both
        # blobs in a fixed apply order. Results land on the shared
        # ``EntityType.SETTINGS`` report category (updated/skipped, never
        # created, never ledgered — settings rollback is out of scope, see
        # ``settings_agents.py``).
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
                )
            elif section == "comskip":
                await import_comskip(
                    archive_comskip=values,
                    client=ctx.client,
                    selected=selected,
                    report=ctx.report,
                    ledger=ctx.ledger,
                    is_dry_run=ctx.is_dry_run,
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
            is_dry_run=ctx.is_dry_run,
            persist_ledger=ctx.flush_ledger,
        )
        return None

    async def _channels(ctx: ApplyContext) -> list[dict] | None:
        await import_channels(
            archive_channels=_entities(ctx, EntityType.CHANNEL),
            client=ctx.client,
            selected=_selected(ctx, EntityType.CHANNEL),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
        )
        return None

    async def _logos(ctx: ApplyContext) -> list[dict] | None:
        # clear_existing is the DESTRUCTIVE bulk-delete pre-step; the logos
        # importer itself guards it behind ``not is_dry_run``, and a dry-run plan
        # never carries an apply confirm — so it can never fire here on a dry-run.
        await import_logos(
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
        return None

    return {
        "m3u": _m3u,
        "epg": _epg,
        "channel_groups": _channel_groups,
        "channel_profiles": _channel_profiles,
        "stream_profiles": _stream_profiles,
        "user_agents": _user_agents,
        "dvr_rules": _dvr_rules,
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
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=True),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        ImporterStep(EntityType.USER_AGENT, s["user_agents"]),
        ImporterStep(EntityType.SETTINGS, s["settings"]),
        ImporterStep(EntityType.USER, s["users"]),
        ImporterStep(EntityType.CHANNEL, s["channels"]),
        ImporterStep(EntityType.DVR_RULE, s["dvr_rules"]),
        ImporterStep(EntityType.LOGO, s["logos"]),
    ]


async def run_dry_run(
    *,
    plan: ImportPlan,
    client: DispatcharrClient,
    steps: list[ImporterStep] | None = None,
    ledger_dir: Path | None = None,
    max_entities_per_category: int = None,  # type: ignore[assignment]
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

    Returns:
        A :class:`RestoreReport` with ``is_dry_run=True`` carrying the per-category
        ``would_*`` counts and the ``logo_misses`` aggregate.
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
    )
