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

    M3U accounts → EPG sources → channel groups/profiles/stream profiles
      → user agents / settings → channels → logos

then the DEFERRED phase applies LAST: the M3U importer (and EPG importer) return
auto-sync/EPG-download settings that MUST NOT fire during the run (they race the
logo import on the Dispatcharr side). The orchestrator collects each importer's
deferred settings and applies them only after every category is done, via
``dbas.importers.m3u_accounts.apply_deferred_auto_sync``.

----------------------------------------------------------------------------
WIRED vs SEAM (be precise — this is scaffolding for in-flight importers)
----------------------------------------------------------------------------

The per-category importers are separate beads, not all built yet. The
orchestrator runs whatever importer callables are registered in the
:class:`ImporterStep` list it is given and treats a category with no registered
importer as a no-op SEAM (logged, never a silent skip). The default registry
(:func:`default_importer_steps`) wires the importers that EXIST today
(M3U accounts, channels, users) and leaves explicit registration seams for the
not-yet-built ones (EPG sources, channel groups/profiles, user agents/settings,
logos). As each lands, it registers here without changing the orchestrator.

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
            registration SEAM — a category whose importer is a separate, not-yet-
            built bead. A seam step is a logged no-op, never a silent skip.
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

    ctx = ApplyContext(
        plan=plan,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=report.is_dry_run,
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
# Default importer registry — WIRED vs SEAM
# ---------------------------------------------------------------------------


def default_importer_steps() -> list[ImporterStep]:
    """The hard Phase-2 ordering with today's importers WIRED and the rest as seams.

    WIRED (importers that exist as of bead .18):
      * M3U accounts (``…-0i2vt.10``) — defers auto-sync.
      * channels (``…-4vouz``).
      * users (``…-l1p4p``) — runs in the settings/users slot.

    SEAM (separate beads, registered as ``importer=None`` no-ops until they land):
      * EPG sources (``…-0i2vt.11``) — will defer EPG download.
      * channel groups / profiles / stream profiles (``…-0i2vt.12``).
      * user agents (``…-0i2vt.13``).
      * logos (``…-0i2vt.15``).

    The ORDER is the contract even where a slot is a seam — when an importer
    lands it slots into its place without reshuffling the sequence.
    """
    s = _importer_step_builders()
    # NOTE on the EPG-sources seam (…-0i2vt.11): the EPG importer's APPLY path is
    # still a separate bead, so EPG sources occupy a documented ordering position
    # between M3U and channel groups but are not yet a discrete ImporterStep row in
    # this APPLY registry. Compensation, however, IS wired: EntityType.EPG_SOURCE
    # (and EntityType.STREAM_PROFILE) now have rollback compensators in
    # ``_delete_dispatch`` (enhancedchannelmanager-v1uz9), so any EPG-source /
    # stream-profile row recorded in the ledger by an in-flight importer is undone
    # cleanly on a late-step failure instead of being left as residue.
    return [
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=True),
        # <- EPG sources (…-0i2vt.11) order position; SEAM, see note above.
        ImporterStep(EntityType.CHANNEL_GROUP, None),                  # SEAM …-0i2vt.12
        ImporterStep(EntityType.CHANNEL_PROFILE, None),               # SEAM …-0i2vt.12
        ImporterStep(EntityType.STREAM_PROFILE, None),                # SEAM …-0i2vt.12
        ImporterStep(EntityType.USER_AGENT, None),                    # SEAM …-0i2vt.13
        ImporterStep(EntityType.USER, s["users"]),                    # WIRED …-l1p4p
        ImporterStep(EntityType.CHANNEL, s["channels"]),              # WIRED …-4vouz
        # logos SEAM …-0i2vt.15 — no EntityType row of its own; attaches to channels
    ]


# ---------------------------------------------------------------------------
# Dry-run registry — bead …-0i2vt.16. EVERY importer wired, counts-only.
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

    async def _users(ctx: ApplyContext) -> list[dict] | None:
        await import_users(
            archive_users=_entities(ctx, EntityType.USER),
            client=ctx.client,
            selected=_selected(ctx, EntityType.USER),
            report=ctx.report,
            ledger=ctx.ledger,
            is_dry_run=ctx.is_dry_run,
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
        )
        return None

    return {
        "m3u": _m3u,
        "epg": _epg,
        "channel_groups": _channel_groups,
        "channel_profiles": _channel_profiles,
        "stream_profiles": _stream_profiles,
        "users": _users,
        "channels": _channels,
        "logos": _logos,
    }


def dry_run_importer_steps() -> list[ImporterStep]:
    """The Phase-2 ordering with EVERY importer WIRED for the counts-only dry-run.

    Bead ``…-0i2vt.16``. Unlike :func:`default_importer_steps` — whose seams for
    EPG sources / groups-profiles / logos await each importer's apply-path +
    rollback wiring (bead ``.18`` / their own beads) — the dry-run registry wires
    ALL importers, because every importer is provably zero-mutation on a dry-run
    (it only reads to plan and increments ``would_*``). Wiring them here lets the
    dry-run aggregate the ``would_*`` counts for the WHOLE archive into one
    :class:`RestoreReport`, which is the point of the engine.

    The order is the same hard Phase-2 sequence
    (``M3U → EPG → groups/profiles → user-agents → users → channels → logos``);
    on a dry-run the order is not load-bearing for mutation (there is none) but is
    kept identical so the plan the operator previews mirrors what apply will do.

    This registry is for DRY-RUN ONLY. It must not be handed to an apply
    (``confirm_apply=True``) run until each wired importer's apply/rollback path is
    registered in :func:`default_importer_steps` — the guardrail in
    :func:`run_restore` forces ``is_dry_run`` when apply is not confirmed, so a
    misuse degrades safely to a dry-run rather than mutating through an unwired
    rollback path.
    """
    s = _importer_step_builders()
    return [
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=True),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        ImporterStep(EntityType.USER_AGENT, None),  # SEAM …-0i2vt.13 (no importer yet)
        ImporterStep(EntityType.USER, s["users"]),
        ImporterStep(EntityType.CHANNEL, s["channels"]),
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
