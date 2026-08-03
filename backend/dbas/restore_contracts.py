"""Shared Phase-2 DBAS restore contracts.

This module pins three cross-bead data shapes that the Phase-2 restore work
references everywhere but, before this module, defined nowhere. If each importer
invented its own, they would diverge and the single restore UX component could
not render them all. (Grooming finding — code-reviewer / PM / DBA;
bead ``enhancedchannelmanager-kxuj2``.)

The three contracts:

1. :class:`RestoreReport` — ONE response schema shared by the dry-run engine
   (bead ``…-0i2vt.16``), the apply/rollback path (bead ``…-0i2vt.18``), and the
   restore-complete summary UX (bead ``…-0i2vt.20``). Carries per-entity-category
   created / updated / skipped(with reason) / failed(with reason) counts, an
   overall tri-state :class:`RestoreOutcome`, and the logo-miss aggregate that the
   logo beads (``.15`` / ``.19``) consume. The dry-run flavour reuses the SAME
   shape (would-create / would-update / would-skip) so one UI component renders
   both — see :attr:`EntityCategoryReport.would_create` etc.

2. :class:`IdRemapTable` — a source-export-id -> destination-id mapping keyed by
   :class:`EntityType`. Produced by the groups/profiles importer
   (bead ``…-0i2vt.12``) and consumed by the channels and settings importers
   (beads ``…-0i2vt.13`` / ``…-4vouz`` / ``…-l1p4p``) to rewrite FK references.

3. :class:`RollbackLedger` — a DURABLE record of created-entity destination IDs
   that must survive a mid-restore ECM crash, supporting
   reverse-dependency-order compensation and idempotent deletes
   (404-on-delete == success). Feeds the tri-state outcome of
   :class:`RestoreReport`. Consumed by the rollback path (bead ``…-0i2vt.18``).

Design rationale, durability contract, and the who-writes / who-reads / lifetime
notes live in ``docs/dbas_restore_contracts.md``.

This is a *contract definition*. There is no behaviour to test yet — the
importer beads that consume these contracts carry the functional tests.

Conventions (``docs/style_guide.md``): ``str, Enum`` with self-describing
values; Pydantic v2 ``BaseModel``; ``snake_case`` fields; Google-style
docstrings on the public surface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Schema version for the on-disk rollback ledger and the wire RestoreReport.
# Bump when a field's MEANING changes (not for additive optional fields). The
# ledger reader (bead …-0i2vt.18) refuses to compensate a ledger whose
# CONTRACT_VERSION it does not understand rather than guess at a stale shape.
CONTRACT_VERSION = 1


# ---------------------------------------------------------------------------
# Entity taxonomy
# ---------------------------------------------------------------------------


class EntityType(str, Enum):
    """A restorable Dispatcharr/ECM entity type.

    These are the FK-bearing entity types whose destination IDs are remapped
    (:class:`IdRemapTable`) and whose creations are logged for compensation
    (:class:`RollbackLedger`). One value per distinct ID namespace.

    The values are the canonical keys used as dict keys in the remap table and
    as ``entity_type`` discriminators in the ledger — keep them stable, they are
    part of the on-disk format.
    """

    M3U_ACCOUNT = "m3u_account"            # …-0i2vt.10 (Phase-2 first entity; producer of remap)
    EPG_SOURCE = "epg_source"              # …-0i2vt.11 (Phase-2; restored after M3U, before Channels)
    CHANNEL_GROUP = "channel_group"        # …-0i2vt.12 (producer of remap)
    CHANNEL_PROFILE = "channel_profile"    # …-0i2vt.12 (producer of remap)
    STREAM_PROFILE = "stream_profile"      # …-0i2vt.12 (producer of remap)
    CHANNEL = "channel"                    # …-4vouz (consumer of remap)
    STREAM = "stream"                      # …-ahygg (synthesized custom-stream orphans)
    USER_AGENT = "user_agent"              # …-0i2vt.13
    DVR_RULE = "dvr_rule"                  # …-0i2vt.13
    SETTINGS = "settings"                  # …-0i2vt.13 REPORT-ONLY category key for
                                           # core settings + comskip. NOT remappable,
                                           # NOT ledgered (a setting is config, not a
                                           # created entity) — see settings_agents.py.
    USER = "user"                          # …-l1p4p (crown-jewel, opt-in)
    LOGO = "logo"                          # …-0i2vt.15 (streaming upload; 3-tier match)


class RestoreActionKind(str, Enum):
    """What the restore did (apply) or would do (dry-run) to one entity."""

    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"
    FAILED = "failed"


class SkipReason(str, Enum):
    """Why an entity was (or would be) skipped.

    Self-describing values so a log line or UI badge is readable without the
    enum type for context (``docs/style_guide.md`` — naming discipline).
    Importers that hit a reason not enumerated here should propose an addition
    rather than overloading an existing value or inventing a free-form string.
    """

    ALREADY_EXISTS_IDENTICAL = "already_exists_identical"   # no-op; dest matches archive
    EXCLUDED_BY_OPERATOR = "excluded_by_operator"           # category opt-out / selection
    CURRENT_ADMIN_PRESERVED = "current_admin_preserved"     # …-l1p4p D11 guard
    UNSUPPORTED_IN_THIS_VERSION = "unsupported_in_this_version"  # e.g. plugins (ADR-012 D10)
    DEPENDENCY_UNRESOLVED = "dependency_unresolved"         # required remap target missing


class FailureReason(str, Enum):
    """Why an entity failed to apply.

    Distinct from :class:`SkipReason`: a *skip* is an intentional no-op that
    leaves state consistent; a *failure* is an apply attempt that errored and
    may have left partial state — it is what can drive a rollback.
    """

    VALIDATION_ERROR = "validation_error"          # pre-flight or field validation
    # FK target absent at apply time. REUSED (no dedicated value added) by
    # dbas.importers.settings_agents for a settings KEY absent on the
    # destination — same "the id/key I need to reference isn't there" shape,
    # just a string key instead of an FK id (zt3kf; see restore_orchestrator's
    # ABORT-ON-ANY-FAILED-KEY docstring section for the resulting rollback
    # policy on that reuse).
    DEPENDENCY_UNRESOLVED = "dependency_unresolved"
    UPSTREAM_API_ERROR = "upstream_api_error"      # Dispatcharr returned an error
    UPSTREAM_TIMEOUT = "upstream_timeout"          # Dispatcharr did not respond in time
    CONFLICT = "conflict"                          # uniqueness / name collision
    PASSWORD_HASH_UNSUPPORTED = "password_hash_unsupported"  # …-l1p4p hash-algo mismatch
    INTERNAL_ERROR = "internal_error"              # ECM-side bug; last-resort bucket


class RestoreOutcome(str, Enum):
    """Overall tri-state result of a restore.

    The contract is: NEVER report ``SUCCESS`` on mixed state. If any entity
    failed and the rollback ran, the outcome is one of the two rolled-back
    states — the UX (bead ``…-0i2vt.20``) labels these "restore failed — state
    rolled back", never "success".

    - ``SUCCESS`` — every selected entity was created/updated/skipped cleanly;
      nothing failed; no compensation was needed.
    - ``PARTIAL_FAILED_ROLLED_BACK`` — at least one entity failed, the
      compensating-delete rollback ran, and every created entity was
      successfully removed (or confirmed already-gone — 404 counts as success).
      The instance is back to its pre-restore state.
    - ``FAILED_ROLLBACK_INCOMPLETE`` — a failure occurred AND the compensating
      rollback itself could not fully undo the created entities (an upstream
      delete errored with something other than 404). The instance is in an
      indeterminate state; the report carries the ledger residue so an operator
      can finish cleanup manually. This is the worst state and is reported
      loudly, never as success.
    """

    SUCCESS = "success"
    PARTIAL_FAILED_ROLLED_BACK = "partial_failed_rolled_back"
    FAILED_ROLLBACK_INCOMPLETE = "failed_rollback_incomplete"


# ---------------------------------------------------------------------------
# Contract 1 — RESTORE RESPONSE SCHEMA (dry-run / apply / summary)
# ---------------------------------------------------------------------------


class SkipDetail(BaseModel):
    """One skipped entity, with the reason and a human-readable label.

    ``label`` is the operator-facing identifier (channel name, username, group
    name) — never a secret. For users (bead ``…-l1p4p``) this is the username
    only; password hashes never appear here.
    """

    reason: SkipReason
    label: str = Field(description="Operator-facing entity identifier — never a secret.")
    source_export_id: int | None = Field(
        default=None,
        description="The entity's id in the export archive, when known.",
    )


class FailureDetail(BaseModel):
    """One failed entity, with the reason and a sanitized message.

    ``message`` is a sanitized, operator-facing string — importers must not leak
    upstream stack traces, credentials, or password hashes into it.
    """

    reason: FailureReason
    label: str = Field(description="Operator-facing entity identifier — never a secret.")
    message: str = Field(description="Sanitized, operator-facing detail. No secrets, no raw traces.")
    source_export_id: int | None = Field(default=None)


class LogoMissChannel(BaseModel):
    """One channel affected by a logo miss (bead ``…-cm9bi``).

    ``channel_id`` is the DESTINATION Dispatcharr channel id, resolved through
    the ``EntityType.CHANNEL`` remap namespace on apply (the channels importer
    runs before the logos importer). It is ``None`` when the id is unknown —
    the channel's create failed/was skipped without a remap entry, or the run
    is a dry-run (whose CHANNEL remap holds PROVISIONAL ids that must never be
    rendered as real Dispatcharr links).

    ``name`` is the operator-facing channel name — never a secret (same hygiene
    as :class:`SkipDetail` / :class:`FailureDetail`).
    """

    channel_id: int | None = Field(
        default=None,
        description="Destination Dispatcharr channel id, when known. Never a provisional dry-run id.",
    )
    name: str = Field(description="Operator-facing channel name — never a secret.")


class LogoMissDetail(BaseModel):
    """One logo that could not be matched/applied on restore (bead ``…-qhui4``).

    The per-logo drill-down behind the aggregate :attr:`RestoreReport.logo_misses`
    count (ADR-012 D9). The aggregate count + red banner stay as-is; this list
    ADDS the affected-logo identities so the restore-complete UX can enumerate
    *which* logos are missing, not just *how many*.

    ``label`` is the operator-facing logo display name — never a path, byte
    payload, or secret (same hygiene as :class:`SkipDetail` / :class:`FailureDetail`).

    ``channels`` (bead ``…-cm9bi``) lists the AFFECTED CHANNELS — the archive
    channels whose ``logo_id`` referenced this missed logo. ONE miss stays ONE
    detail row (``len(logo_miss_details)`` keeps tracking the aggregate
    :attr:`RestoreReport.logo_misses`, which counts logos, not channels); a logo
    referenced by several channels lists them all here. Empty when no channel
    referenced the logo or no channel context was supplied. Additive optional —
    no ``CONTRACT_VERSION`` bump.
    """

    source_export_id: int | None = Field(
        default=None,
        description="The logo's id in the export archive, when known.",
    )
    label: str = Field(description="Operator-facing logo name — never a path or secret.")
    channels: list[LogoMissChannel] = Field(
        default_factory=list,
        description="The channels restored without this logo (destination id where known + name).",
    )


class EntityCategoryReport(BaseModel):
    """Per-entity-category counts for ONE category.

    The SAME shape carries both dry-run and apply results so a single UI
    component (bead ``…-0i2vt.20``) renders both — distinguished by
    :attr:`RestoreReport.is_dry_run`:

    - **Apply** populates :attr:`created` / :attr:`updated` / :attr:`skipped` /
      :attr:`failed`.
    - **Dry-run** populates :attr:`would_create` / :attr:`would_update` /
      :attr:`would_skip` (counts-only per ADR-012 D7; the full entity-level
      diff tree is deferred to v0.19.x). It MAY still populate
      :attr:`skip_details` (e.g. would-skip-because-already-exists) so the
      operator sees *why* before applying.
    - :attr:`failed` / :attr:`failure_details` are NOT exclusively an
      apply-flavour signal: there is no separate ``would_fail`` counter, and an
      importer MAY populate :attr:`failed` on a dry-run too when the failure is
      a FACT about the source/destination data rather than about whether the
      run applied — e.g. a channel's ambiguous null-key collision
      (``dbas/importers/channels.py``), a source-side duplicate name
      (``tasks/dbas_sync_engine.py``), or a settings key absent on the
      destination (``dbas/importers/settings_agents.py``, bead ``…-y6zg6``: the
      preview resolves each key against the SAME destination the apply would, so
      it cannot certify would-update for a key the apply then fails). A renderer
      reads :attr:`failed` the same way on both flavours.

    The detail lists are the source of truth for the reasons; the integer
    counts are conveniences derived from them. A renderer that wants the
    headline number reads the count; one that wants the reasons reads the list.
    Importers MUST keep them consistent
    (``len(skip_details) == skipped`` on apply).
    """

    entity_type: EntityType

    # Apply-flavour counts.
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    # Dry-run-flavour counts (counts-only per ADR-012 D7).
    would_create: int = 0
    would_update: int = 0
    would_skip: int = 0

    # Reasoned detail — drives the per-entity "skipped (with reason) / failed
    # (with reason)" UX. Populated on apply; optionally on dry-run for skips.
    skip_details: list[SkipDetail] = Field(default_factory=list)
    failure_details: list[FailureDetail] = Field(default_factory=list)


class RestoreReport(BaseModel):
    """The one restore response schema — dry-run, apply, and summary.

    Produced by the dry-run engine (bead ``…-0i2vt.16``) and the apply/rollback
    path (bead ``…-0i2vt.18``); rendered by the restore-complete UX
    (bead ``…-0i2vt.20``). The dry-run and the real restore return the SAME type
    so one UI component handles both — :attr:`is_dry_run` tells them apart.

    Tri-state discipline: :attr:`outcome` is :class:`RestoreOutcome` and is
    NEVER ``SUCCESS`` when any category has failures. On a dry-run,
    :attr:`outcome` is left ``None`` — a plan has no realized outcome.
    """

    contract_version: int = Field(default=CONTRACT_VERSION)
    is_dry_run: bool = Field(
        description="True for the counts-only plan (ADR-012 D7 default-ON); "
        "False for a realized apply/rollback result.",
    )
    outcome: RestoreOutcome | None = Field(
        default=None,
        description="Tri-state result of a realized restore. None on a dry-run "
        "(a plan has no realized outcome). Never SUCCESS on mixed state.",
    )

    categories: list[EntityCategoryReport] = Field(default_factory=list)

    # Logo-miss aggregate — consumed by the logo beads (.15 / .19). The count of
    # logo references in the archive that could not be resolved/applied during
    # restore (missing binary, unreachable URL, etc.). Aggregate-only here; the
    # per-logo detail lives in the logo beads' own surface.
    logo_misses: int = Field(
        default=0,
        description="Aggregate count of unresolved logo references (.15 / .19 consume this).",
    )

    # Per-logo drill-down behind the aggregate count (bead …-qhui4). ADDITIVE: the
    # aggregate count + red banner are unchanged; this list lets the UX enumerate
    # WHICH logos are missing. Populated by the logos importer (.15) on each miss;
    # ``len(logo_miss_details)`` tracks :attr:`logo_misses`. Empty on a clean
    # restore — a renderer keys off the aggregate count for the banner gate.
    logo_miss_details: list[LogoMissDetail] = Field(
        default_factory=list,
        description="Per-logo detail (id + name) for each unresolved logo (…-qhui4).",
    )

    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    # Free-form, sanitized operator notes (e.g. "users category opted out",
    # "rollback completed: 12 entities removed"). No secrets.
    notes: list[str] = Field(default_factory=list)

    def category(self, entity_type: EntityType) -> EntityCategoryReport:
        """Return the per-category report for ``entity_type``, creating it if absent.

        Importers append to one shared :class:`RestoreReport`; this keeps them
        from duplicating a category block. Not a hot path — linear scan is fine.

        Args:
            entity_type: The category to fetch or create.

        Returns:
            The existing :class:`EntityCategoryReport` for the type, or a new
            zeroed one appended to :attr:`categories`.
        """
        for cat in self.categories:
            if cat.entity_type == entity_type:
                return cat
        cat = EntityCategoryReport(entity_type=entity_type)
        self.categories.append(cat)
        return cat


# ---------------------------------------------------------------------------
# Contract 2 — ID-REMAP TABLE (source-export-id -> destination-id)
# ---------------------------------------------------------------------------


class IdRemapTable(BaseModel):
    """Maps source-export IDs to live destination IDs, keyed by entity type.

    **Why it exists:** a Dispatcharr export records each entity's id *as it was
    on the source instance*. On restore those ids are meaningless — the
    destination instance assigns its own. FK references in the archive (a
    channel's ``channel_group_id``, a profile membership, etc.) point at SOURCE
    ids and must be rewritten to DESTINATION ids before they are sent upstream.

    **Producer / consumer contract:**

    - WRITTEN by the groups/profiles importer (bead ``…-0i2vt.12``) as it
      creates each channel group / channel profile / stream profile: after a
      successful create, it records ``add(EntityType.CHANNEL_GROUP, src_id,
      dest_id)``.
    - READ by the channels importer (bead ``…-4vouz``) and the settings/users
      importers (beads ``…-0i2vt.13`` / ``…-l1p4p``) to rewrite FK references
      before sending them upstream (``resolve(EntityType.CHANNEL_GROUP,
      archived_group_id)``).
    - LIFETIME: lives for the duration of a single restore run, threaded
      through the importers in dependency order (groups/profiles BEFORE
      channels). It does NOT need to be durable across an ECM crash — a crashed
      restore is rolled back via the ledger (Contract 3) and restarted from
      scratch, which rebuilds the remap from zero.

    **Critical constraint (from the bead):** importers MUST NOT reuse
    ``backup.py``'s delete-all-then-recreate strategy. That strategy destroys
    the very relationships this table exists to preserve — it would invalidate
    every destination id mid-run. Restore is additive/idempotent per entity,
    never wholesale wipe-and-replace.
    """

    contract_version: int = Field(default=CONTRACT_VERSION)
    # entity_type value -> {source_export_id: destination_id}
    mappings: dict[EntityType, dict[int, int]] = Field(default_factory=dict)

    def add(self, entity_type: EntityType, source_export_id: int, destination_id: int) -> None:
        """Record a source-export-id -> destination-id mapping.

        Args:
            entity_type: The entity's type (its ID namespace).
            source_export_id: The id the entity had in the export archive.
            destination_id: The id the destination instance assigned on create.
        """
        self.mappings.setdefault(entity_type, {})[source_export_id] = destination_id

    def resolve(self, entity_type: EntityType, source_export_id: int) -> int | None:
        """Return the destination id for a source-export id, or None if unmapped.

        A ``None`` return is the importer's signal that an FK reference cannot be
        rewritten — the entity should be reported as
        ``FailureReason.DEPENDENCY_UNRESOLVED`` (or skipped with
        ``SkipReason.DEPENDENCY_UNRESOLVED`` in dry-run), never sent upstream
        with a dangling source id.

        Args:
            entity_type: The entity's type (its ID namespace).
            source_export_id: The id to look up.

        Returns:
            The mapped destination id, or ``None`` if no mapping was recorded.
        """
        return self.mappings.get(entity_type, {}).get(source_export_id)


# ---------------------------------------------------------------------------
# Contract 3 — ROLLBACK LEDGER (durable created-entity record)
# ---------------------------------------------------------------------------


class LedgerEntry(BaseModel):
    """One created entity recorded for possible compensation.

    :attr:`sequence` is a monotonic counter assigned at append time. Compensation
    runs in DESCENDING sequence order, which is reverse-creation order and
    therefore reverse-dependency order: because importers run in dependency
    order (groups/profiles BEFORE the channels that reference them), undoing in
    reverse never deletes a parent while a child still points at it.
    """

    sequence: int = Field(description="Monotonic append order; compensate in descending order.")
    entity_type: EntityType
    destination_id: int = Field(description="The id the destination instance assigned on create.")
    label: str = Field(description="Operator-facing identifier for audit/UX — never a secret.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Set True once a compensating DELETE has succeeded (or returned 404 ==
    # already-gone). Idempotency marker: a re-run skips already-compensated rows.
    compensated: bool = Field(default=False)


class RollbackLedger(BaseModel):
    """Durable record of created entities, for reverse-order compensating deletes.

    **Why durable, not an in-memory list:** Dispatcharr has no database
    transactions (ADR-012; bead ``…-0i2vt.18``). Best-effort consistency is a
    compensating-delete rollback — but if ECM crashes mid-restore, an in-memory
    list of "what I created so far" dies with the process, orphaning every
    created entity with no record to undo them. The ledger is therefore
    persisted to disk *as each entity is created*, BEFORE or atomically with the
    next create, so a crash leaves a recoverable record.

    **Persistence contract (the design doc carries the full detail):**

    - Stored as a JSON file under ``CONFIG_DIR`` (the same durable, mounted
      volume as ``/config/journal.db`` and ``settings.json``), e.g.
      ``/config/dbas/restore_ledger_<restore_id>.json``.
    - Append cadence: a destination id is only known AFTER the upstream create
      returns, so the entry is appended and flushed to disk IMMEDIATELY after
      the create returns and BEFORE the next create is issued. The worst-case
      crash window is then a single entity — the in-flight create whose response
      never landed and so was never ledgered. That one orphan is recoverable by
      the pre-flight orphan check (see the design doc); an intent-log that
      records the create BEFORE it is issued is deferred (not needed for
      v0.18.0).
    - Writes are atomic (temp file + ``os.replace``) so a crash never leaves a
      half-written ledger.

    **Compensation contract:**

    - ORDER: descending :attr:`LedgerEntry.sequence` (reverse creation =
      reverse dependency order). Never delete a parent before its children.
    - IDEMPOTENT: a compensating DELETE that returns **404 is treated as
      success** (the entity is already gone — desired end state reached). Only a
      non-404 upstream error counts as a failed compensation.
    - OUTCOME mapping into :class:`RestoreOutcome` (Contract 1):
        * every entry compensated (deleted or 404) -> ``PARTIAL_FAILED_ROLLED_BACK``.
        * any entry's DELETE failed with a non-404 error ->
          ``FAILED_ROLLBACK_INCOMPLETE``; the residual uncompensated entries stay
          in the ledger and are surfaced in the :class:`RestoreReport` so an
          operator can finish cleanup.
    - On a clean, fully-successful restore the ledger is deleted (no
      compensation needed); on any rollback it is retained until every entry is
      compensated.
    """

    contract_version: int = Field(default=CONTRACT_VERSION)
    restore_id: str = Field(description="Unique id for this restore run; names the on-disk ledger file.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entries: list[LedgerEntry] = Field(default_factory=list)

    def record_created(
        self,
        entity_type: EntityType,
        destination_id: int,
        label: str,
    ) -> LedgerEntry:
        """Append a created-entity entry with the next monotonic sequence.

        The caller is responsible for persisting the ledger to disk immediately
        after this call and before issuing the next upstream create — this
        method only mutates the in-memory model. Persistence is intentionally
        left to the importer/rollback layer (bead ``…-0i2vt.18``) so the durable
        write strategy lives in one place; see ``docs/dbas_restore_contracts.md``.

        Args:
            entity_type: The created entity's type.
            destination_id: The id the destination instance assigned.
            label: Operator-facing identifier — never a secret.

        Returns:
            The appended :class:`LedgerEntry`.
        """
        entry = LedgerEntry(
            sequence=len(self.entries),
            entity_type=entity_type,
            destination_id=destination_id,
            label=label,
        )
        self.entries.append(entry)
        return entry

    def compensation_order(self) -> list[LedgerEntry]:
        """Return not-yet-compensated entries in compensation order.

        Compensation order is descending :attr:`LedgerEntry.sequence` (reverse
        creation = reverse dependency order). Already-compensated entries are
        excluded so a resumed rollback is idempotent.

        Returns:
            Uncompensated entries, newest-created first.
        """
        pending = [e for e in self.entries if not e.compensated]
        return sorted(pending, key=lambda e: e.sequence, reverse=True)
