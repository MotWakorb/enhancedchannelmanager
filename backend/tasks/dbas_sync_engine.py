"""One-way cross-instance sync ENGINE CORE — config categories (epic ``i39wu``).

Bead ``enhancedchannelmanager-tjaey``. Architecture: [ADR-013](../../docs/adr/
ADR-013-cross-instance-live-sync.md) S1/S3/S4/S5/S7/S9. Security: threat model
``docs/security/threat_model_dbas_import.md`` §11 Addendum D (D2/D3/D5/D8/D9).

What this is (the proven thesis — spike ``xp6mp``)
--------------------------------------------------
Sync is **"restore over HTTP"**. The DBAS restore orchestrator
(``dbas.restore_orchestrator.run_restore`` / ``run_dry_run``) and its importers
take the Dispatcharr ``client`` as an injected parameter — the ONLY coupling to
"local" is a single ``get_client()`` call in the archive-restore task. So sync:

1. gathers the LOCAL source-A config (the SAME ``_gather_dispatcharr_sections``
   pointed at ``get_client()`` the backup builder uses),
2. REDACTS it to topology-only via the shared ``_REDACT_KEYS`` deep redactor
   (no secrets on the wire — Addendum D D2),
3. maps each category to its :class:`EntityType` (the SAME
   ``restore_artifact._SECTION_TO_ENTITY`` table the archive decoder uses),
4. assembles an :class:`~dbas.preflight.ImportPlan` whose manifest carries
   ``schema_version = BACKUP_SCHEMA_VERSION`` (the orchestrator runs the .17
   pre-flight gate; without the stamp it refuses the plan — spike empirical find),
5. and runs the UNCHANGED orchestrator against a remote (dest-B) client built
   from a ``SyncTarget`` row (``dbas_sync_client.make_remote_client``).

There are **zero edits to ``backend/dbas/``** — the orchestrator + importers are
reused as-is. The only new code here is the live-source plan reader, the
config-only step registry, ``run_sync``, and the shared never-sync constant.

Scope of THIS bead (ADR-013 phasing / S9)
-----------------------------------------
CONFIG categories only: ``m3u_accounts``, ``epg_sources``, ``channel_groups``,
``channel_profiles``, ``stream_profiles``. Channels / streams / logos are bead
``kcxie`` (Phase-2). Users NEVER sync (D3). The deferred auto-sync / EPG-download
phase is **not** run per cycle (S9) — the config step registry passes no deferred
settings to the orchestrator.

This module is the ENGINE FUNCTION. The scheduled-task wrapper + manual trigger
(``TaskScheduler`` subclass, overlap guard) is a separate bead (``5gzg5``);
``run_sync`` is kept callable + testable so that wrapper is a thin shell.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log or report field.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import journal
from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_artifact import _SECTION_TO_ENTITY
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import (
    ImporterStep,
    _importer_step_builders,
    new_restore_id,
    run_restore,
)
from routers.backup import (
    BACKUP_SCHEMA_VERSION,
    _gather_dispatcharr_sections,
    _redact_credentials_deep,
)
from tasks.dbas_sync_client import make_remote_client, sync_freshness_reason

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared NEVER-SYNC constant — code-enforced (mirrors the always-on _REDACT_KEYS).
# ---------------------------------------------------------------------------

# The categories that MUST NEVER appear in a sync plan, unconditionally — no
# settings key, no opt-in (Addendum D D3, ADR-013 S3). Continuous one-way push of
# ``users`` would repeatedly overwrite B's privilege flags / lock out B's
# operator. This constant is imported by the plan assembler AND its test so the
# exclusion is enforced at code level, exactly like the SSRF denylist — not soft
# scope a future edit could erode.
SYNC_NEVER_CATEGORIES: frozenset[str] = frozenset({"users"})

# Credential-class columns that must never be assembled onto the wire either
# (the SyncTarget credential-freshness columns + raw credentials). These are not
# Dispatcharr config sections, but they are named here so the never-sync surface
# is one auditable constant. Defence in depth alongside the _REDACT_KEYS deep
# redactor (D2) that strips secret VALUES from whatever is gathered.
SYNC_NEVER_CREDENTIAL_COLUMNS: frozenset[str] = frozenset(
    {"credentials", "credential_version", "token_revoked_at"}
)

# The CONFIG categories this bead (tjaey) syncs — topology config only. Channels
# / streams / logos are bead kcxie; users are never (above). Each maps to an
# EntityType via _SECTION_TO_ENTITY (the same table the archive decoder uses).
SYNC_CONFIG_CATEGORIES: frozenset[str] = frozenset(
    {
        "m3u_accounts",
        "epg_sources",
        "channel_groups",
        "channel_profiles",
        "stream_profiles",
    }
)


# ---------------------------------------------------------------------------
# Live-source plan reader — gather LOCAL config -> redact -> ImportPlan.
# ---------------------------------------------------------------------------


def _assert_no_never_sync(section_key: str) -> None:
    """Fail-closed guard: a never-sync category must never reach plan assembly.

    The config-category loop already excludes ``users`` by construction (it is
    not in :data:`SYNC_CONFIG_CATEGORIES`), but this explicit guard makes the D3
    invariant code-enforced at the assembly chokepoint — a future edit that adds
    a category to the gather cannot silently smuggle ``users`` onto the wire.
    """
    if section_key in SYNC_NEVER_CATEGORIES:
        raise AssertionError(
            "never-sync category %r must not be assembled into a sync plan (D3)"
            % section_key
        )


async def build_live_source_plan() -> ImportPlan:
    """Gather the LOCAL source-A config, redact it, and assemble an ImportPlan.

    Reuses the backup gather (:func:`_gather_dispatcharr_sections`, which reads
    the LOCAL ``get_client()`` itself) and the shared
    :func:`_redact_credentials_deep` deep redactor — the SAME topology-only
    pipeline the redacted backup artifact uses (Addendum D D2: no secrets on the
    wire). Each gathered section maps to its :class:`EntityType` via
    :data:`_SECTION_TO_ENTITY`.

    The plan's manifest carries ``schema_version = BACKUP_SCHEMA_VERSION`` — the
    orchestrator's pre-flight runs the SAME .17 schema-version gate as archive
    restore and refuses a plan without it (spike ``xp6mp`` empirical find).

    Returns:
        An :class:`ImportPlan` of the redacted config categories, ready for the
        orchestrator. The ``users`` category is NEVER present (D3).
    """
    # Gather ONLY the config categories (never users; never channels/streams/
    # logos — those are other beads). _gather_dispatcharr_sections owns the LOCAL
    # client lookup and returns a ``{"_warning": ...}`` dict (no config rows) when
    # the local Dispatcharr is unavailable — never a crash.
    sections = await _gather_dispatcharr_sections(set(SYNC_CONFIG_CATEGORIES))

    # Redact to topology-only BEFORE the rows enter the plan — one shared denylist,
    # every category, no plaintext path (D2). preserve_keys is intentionally empty:
    # sync NEVER carries credentials, unlike the opt-in migration artifact (u81kh).
    redacted_sections = _redact_credentials_deep(sections, preserve_keys=frozenset())

    categories: list[PlanCategory] = []
    for section_key, entity_type in _SECTION_TO_ENTITY.items():
        if section_key not in SYNC_CONFIG_CATEGORIES:
            # Skip any decoder section outside this bead's config scope (the
            # decoder table may list more than we sync).
            continue
        _assert_no_never_sync(section_key)
        rows = redacted_sections.get(section_key) if isinstance(redacted_sections, dict) else None
        entities = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        categories.append(PlanCategory(entity_type=entity_type, entities=entities))

    plan = ImportPlan(
        # The schema_version stamp is the load-bearing manifest field: pre-flight
        # refuses a plan without it (preflight.py -> validate_restore_schema_version).
        manifest={"schema_version": BACKUP_SCHEMA_VERSION},
        categories=categories,
    )
    logger.info(
        "[SYNC] Assembled redacted source plan: %s",
        ", ".join("%s=%d" % (c.entity_type.value, len(c.entities)) for c in categories),
    )
    return plan


# ---------------------------------------------------------------------------
# Config-only importer step registry — REUSE the orchestrator's builders.
# ---------------------------------------------------------------------------


def sync_config_importer_steps() -> list[ImporterStep]:
    """The CONFIG-category step registry for a sync cycle — reuse, no-defer.

    Reuses :func:`dbas.restore_orchestrator._importer_step_builders` (the SAME
    callables that back the archive apply + dry-run registries) so there is no
    second importer path. It wires ONLY this bead's config categories in the hard
    Phase-2 dependency order (M3U → EPG → groups/profiles); channels / streams /
    logos / users are excluded (other beads / never).

    CRITICAL (ADR-013 S9): the M3U step is registered with ``defers=False`` and
    the orchestrator is given a deferred-apply no-op, so the per-cycle deferred
    auto-sync / EPG-download phase NEVER fires — re-triggering provider auto-sync
    on B every interval is exactly the behaviour S9 forbids. The M3U importer
    still returns its deferred settings; we simply never apply them.
    """
    s = _importer_step_builders()
    return [
        # M3U first (EPG sources resolve their m3u_account FK through the remap
        # M3U writes). defers=False: the deferred auto-sync phase is suppressed.
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=False),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
    ]


async def _no_deferred_apply(*, deferred: list[dict], client) -> list[dict]:
    """Deferred-apply no-op for the sync path (ADR-013 S9).

    The orchestrator only calls a deferred-apply fn when ``ctx.deferred`` is
    non-empty AND the run is a clean non-dry-run apply. The config step registry
    registers M3U with ``defers=False``, but the M3U importer still RETURNS its
    deferred settings; to guarantee S9 even if a future builder change starts
    collecting them, this fn drops them on the floor and logs.
    """
    if deferred:
        logger.info(
            "[SYNC] Suppressing %d deferred auto-sync setting(s) (ADR-013 S9 — "
            "per-cycle provider auto-sync is not re-triggered on B).",
            len(deferred),
        )
    return []


# ---------------------------------------------------------------------------
# run_sync — the engine entrypoint.
# ---------------------------------------------------------------------------


def _journal_sync_run(
    target,
    report: Optional[RestoreReport],
    *,
    confirm_apply: bool,
    aborted_reason: Optional[str] = None,
) -> None:
    """Write the per-run ``sync_outbound`` audit row (Addendum D D9).

    Records target id, the config categories + their counts, the result, and the
    redaction mode. Best-effort — a journal failure must not crash the sync. Only
    SAFE fields (names, counts, outcome) are logged; never a credential.
    """
    try:
        if aborted_reason is not None:
            description = "Cross-instance sync ABORTED: %s" % aborted_reason
            counts = {}
            result = "aborted"
        else:
            counts = {
                cat.entity_type.value: {
                    "created": cat.created,
                    "would_create": cat.would_create,
                    "skipped": cat.skipped,
                    "failed": cat.failed,
                }
                for cat in (report.categories if report else [])
            }
            outcome = report.outcome.value if (report and report.outcome) else None
            result = (
                "dry_run" if (report and report.is_dry_run) else (outcome or "unknown")
            )
            description = (
                "Cross-instance sync run (mode=%s, redaction_mode=topology_only, "
                "categories=%s)" % (result, sorted(SYNC_CONFIG_CATEGORIES))
            )
        journal.log_entry(
            category="sync_outbound",
            action_type="sync_run",
            entity_name=getattr(target, "name", None) or ("sync target %s" % getattr(target, "id", "?")),
            entity_id=getattr(target, "id", None),
            description=description,
            # after_value carries the structured run record (no secrets — only
            # category names, counts, and the result/redaction mode).
            after_value={
                "confirm_apply": confirm_apply,
                "redaction_mode": "topology_only",
                "result": result,
                "counts": counts,
            },
            user_initiated=False,
        )
    except Exception as exc:  # pragma: no cover — journal best-effort
        logger.warning("[SYNC] Failed to journal sync run: %s", exc)


async def run_sync(
    sync_target,
    *,
    confirm_apply: bool = False,
    session=None,
    captured_version: Optional[int] = None,
    ledger_dir: Optional[Path] = None,
) -> RestoreReport:
    """Run one cross-instance config sync cycle for ``sync_target`` (A → B).

    The engine entrypoint:

    1. **Freshness gate (D5)** — :func:`sync_freshness_reason` re-reads the target
       FRESH. A non-None reason ABORTS the cycle fail-closed: no remote client is
       built, no writes happen, the abort is journalled (``sync_outbound``), and a
       report carrying the reason in ``notes`` (``outcome=None``) is returned.
    2. **Remote client** — :func:`make_remote_client` builds an SSRF-guarded
       dest-B client from the target row.
    3. **Live-source plan** — :func:`build_live_source_plan` gathers + redacts A's
       config categories (D2) into an :class:`ImportPlan` (never users — D3).
    4. **Restore** — the UNCHANGED :func:`run_restore` runs the config-only step
       registry against dest-B. ``confirm_apply=False`` (the DEFAULT) produces a
       counts-only dry-run (would-create) with ZERO writes; ``confirm_apply=True``
       applies source-wins (A overwrites B; match→skip-or-create idempotent).
    5. **Audit (D9)** — the run is journalled (categories, counts, result,
       redaction_mode).

    Args:
        sync_target: a ``SyncTarget`` ORM row (or any object exposing ``id`` /
            ``name`` / ``base_url`` / ``credentials`` / ``enabled`` /
            ``token_revoked_at`` / ``credential_version`` / ``insecure``).
        confirm_apply: opt-IN to MUTATE B. ``False`` (default) is a counts-only
            dry-run (no writes); ``True`` applies source-wins.
        session: an open DB session for the freshness re-read. The caller owns its
            lifecycle. Optional only so the dry-run preview can run without one;
            when ``None`` the freshness gate is skipped (the scheduled wrapper
            bead ``5gzg5`` always passes one).
        captured_version: the ``credential_version`` captured at enqueue, threaded
            to the freshness gate (D5). ``None`` skips the version check.
        ledger_dir: override the durable rollback-ledger directory (tests).

    Returns:
        The :class:`RestoreReport` — dry-run (``is_dry_run=True``, ``outcome=None``)
        or a realized apply with the tri-state ``outcome``. On a freshness abort,
        a report with the reason in ``notes`` and ``outcome=None``.
    """
    target_label = getattr(sync_target, "name", None) or (
        "sync target %s" % getattr(sync_target, "id", "?")
    )

    # --- 1. Freshness gate (D5) — abort fail-closed on stale/revoked/disabled. ---
    if session is not None:
        reason = sync_freshness_reason(
            session, getattr(sync_target, "id", None), captured_version
        )
        if reason is not None:
            logger.warning("[SYNC] Aborting sync for %s: %s", target_label, reason)
            report = RestoreReport(is_dry_run=not confirm_apply)
            report.notes.append("sync aborted: %s" % reason)
            _journal_sync_run(
                sync_target, report, confirm_apply=confirm_apply, aborted_reason=reason
            )
            return report

    # --- 2. Remote dest-B client (SSRF-guarded). ---
    client = make_remote_client(sync_target)

    # --- 3. Redacted live-source plan (config categories, never users). ---
    plan = await build_live_source_plan()

    # --- 4. Restore (reused orchestrator) — dry-run default, source-wins apply. ---
    report = RestoreReport(is_dry_run=not confirm_apply)
    ledger = RollbackLedger(restore_id=new_restore_id())
    result = await run_restore(
        plan=plan,
        client=client,
        steps=sync_config_importer_steps(),
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=confirm_apply,
        deferred_apply_fn=_no_deferred_apply,  # ADR-013 S9 — suppress per-cycle defer.
        ledger_dir=ledger_dir,
    )

    # --- 5. Audit the run (D9). ---
    _journal_sync_run(sync_target, result, confirm_apply=confirm_apply)
    logger.info(
        "[SYNC] Sync cycle for %s complete (mode=%s, outcome=%s).",
        target_label,
        "dry_run" if result.is_dry_run else "apply",
        result.outcome.value if result.outcome else "none",
    )
    return result
