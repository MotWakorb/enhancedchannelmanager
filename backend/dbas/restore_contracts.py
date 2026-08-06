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
   overall :class:`RestoreOutcome`, and the logo-miss aggregate that the
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
    # ECM's OWN settings.json (…-dfkbn item 4). DISTINCT from SETTINGS, which is
    # Dispatcharr's core-settings namespace: the drill's restore reported
    # ``settings updated=7`` for that category while ECM's user_timezone and
    # stats_poll_interval silently reverted to defaults, because the artifact's
    # ``categories/settings.yaml`` had no importer at all. Same report-only,
    # never-ledgered shape as SETTINGS.
    ECM_SETTINGS = "ecm_settings"
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
    """Overall result of a restore.

    The contract is: NEVER report ``SUCCESS`` on mixed state. If any entity
    failed, the outcome is one of the non-success states — the UX (bead
    ``…-0i2vt.20``) never labels those "success".

    - ``SUCCESS`` — every selected entity was created/updated/skipped cleanly;
      nothing failed; no compensation was needed.
    - ``COMPLETED_WITH_FAILURES`` — the restore ran to completion and NOTHING was
      rolled back, but the result is not clean. TWO independent triggers:

      * at least one entity in a NON-FATAL category failed (bead ``…-y65si``:
        only ``dispatcharr_users`` is non-fatal — losing one archived user must
        not cost the operator their channels, groups, profiles and settings).
        The applied state is real and kept; the failed rows are counted in their
        category and listed in ``failure_details``.
      * an APPLY finished with :attr:`RestoreReport.channels_with_no_playable_stream`
        greater than zero (bead ``…-daziw``): a channel was restored holding NOT
        ONE URL-bearing stream, so it cannot play. Every row "succeeded" and the
        counts are clean, which is exactly how the drill's
        ``success … created 32, failed 0`` described an instance whose channels
        returned HTTP 500 with 0 bytes. A restored channel that cannot play is
        mixed state, so SUCCESS is forbidden. Nothing is rolled back — the
        applied state is real, kept, and one attached stream away from working.
        NOTE the trigger is the unplayable aggregate, NEVER
        :attr:`RestoreReport.channels_needing_stream_reattach`: a channel that
        keeps its real streams and merely holds a leftover placeholder PLAYS,
        and is not a failure.
    - ``PARTIAL_FAILED_ROLLED_BACK`` — at least one entity in a FATAL category
      failed, the compensating-delete rollback ran, and every created entity was
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
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    PARTIAL_FAILED_ROLLED_BACK = "partial_failed_rolled_back"
    FAILED_ROLLBACK_INCOMPLETE = "failed_rollback_incomplete"


class ChannelReattachMode(str, Enum):
    """What the post-create reattach passes do to channels this restore did NOT create.

    Bead ``…-dfkbn``, PR review W1. The reattach passes
    (:mod:`dbas.channel_reattach`) put a channel's archived EPG link and logo
    back after the create, because both are SOURCE ids the create payload has to
    drop. A channel that already existed on the destination is matched
    ``ALREADY_EXISTS_IDENTICAL`` and is deliberately never overwritten for its
    name, number or group, but it IS entered in the CHANNEL remap so a reattach
    pass can resolve it. That makes the pre-existing channel reachable by these
    two PATCHes even though nothing else about it is touched.

    The two cases the operator is actually in:

    * **Disaster recovery** (the primary use case, and what the round-trip drill
      exercises): the target is empty, every channel is created, and the two
      modes are IDENTICAL. The safe default costs DR nothing.
    * **Merging into a live, populated instance** (for example restoring to
      recover channel-profile membership): hundreds of channels match as
      identical, and their current EPG links and logos are state the operator
      set themselves. Resetting all of it to the archive's view is not
      recoverable by the rollback ledger, which compensates CREATES only.

    So the default is :attr:`PRESERVE`, and :attr:`OVERWRITE` is the explicit
    opt-in. The mode covers BOTH passes: "what happens to channels this restore
    did not create" is one question, and answering it differently for EPG links
    and logos would be an inconsistency the operator has no way to predict.
    """

    PRESERVE = "preserve"
    OVERWRITE = "overwrite"

    @classmethod
    def coerce(cls, value) -> "ChannelReattachMode":
        """Resolve a request value to a mode, defaulting to the SAFE one.

        ONE parsing rule for every entry point (the two restore endpoints and
        the restore task), for the same reason bead ``…-dfkbn`` exists at all: a
        second copy of a rule is a disagreement waiting to happen.

        Anything the enum does not recognise resolves to :attr:`PRESERVE`:
        absent, ``None``, empty, a typo, a value from a future client. The
        unsafe direction is never the fallback: a restore must not start
        overwriting an operator's live EPG links and logos because a field
        failed to parse, and an OLD client that does not send the field at all
        must keep the behaviour it was written against.

        Args:
            value: The raw request value.

        Returns:
            The parsed mode, or :attr:`PRESERVE`.
        """
        # A member of this enum parses as itself. ``str(ChannelReattachMode.
        # OVERWRITE)`` is ``"ChannelReattachMode.OVERWRITE"``, which the value
        # lookup below rejects — so without this the one parsing rule could not
        # parse its own type, and would answer PRESERVE with a misleading
        # warning (PR review round 2, finding 3).
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except (ValueError, AttributeError, TypeError):
            if value not in (None, ""):
                logger.warning(
                    "[DBAS-RESTORE] Unrecognised channel reattach mode; "
                    "falling back to 'preserve'."
                )
            return cls.PRESERVE


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


def _merge_logo_miss_channels(
    existing: list[LogoMissChannel],
    incoming: list[LogoMissChannel],
) -> list[LogoMissChannel]:
    """Union two producers' affected-channel lists for ONE logo (bead ``…-k2r7m``).

    Order-preserving, first-seen wins. Identity is ``(channel_id, name)``: both
    producers resolve the destination id through the same ``CHANNEL`` remap in
    the same run, so the same channel yields the same pair from either.
    """
    merged: list[LogoMissChannel] = []
    seen: set[tuple[int | None, str]] = set()
    for channel in [*existing, *incoming]:
        key = (channel.channel_id, channel.name)
        if key in seen:
            continue
        seen.add(key)
        merged.append(channel)
    return merged


class CredentialReentryDetail(BaseModel):
    """One restored entity whose credential the archive could not carry (…-6pilh).

    A STANDARD (redact-by-default) artifact replaces every credential-class value
    with the ``***REDACTED***`` sentinel. The importers REFUSE to write that
    placeholder into the destination — the field is left unset — which means the
    entity is restored but not yet usable. This detail is the operator's action
    item: which entity, and which field names to re-enter.

    Counted by :attr:`RestoreReport.credentials_needing_reentry` (one per ENTITY,
    matching ``len(credential_reentry_details)``), mirroring the
    ``logo_misses`` / ``logo_miss_details`` aggregate-plus-drill-down pair.

    ``label`` is the operator-facing entity name and ``fields`` are field NAMES
    (``["password"]``) — never a value, a URL, or a username (same hygiene as
    :class:`SkipDetail` / :class:`FailureDetail`).
    """

    entity_type: EntityType
    label: str = Field(description="Operator-facing entity identifier — never a secret.")
    fields: list[str] = Field(
        default_factory=list,
        description="Credential FIELD NAMES left unset, in document order. Names only, never values.",
    )
    source_export_id: int | None = Field(
        default=None,
        description="The entity's id in the export archive, when known.",
    )
    destination_id: int | None = Field(
        default=None,
        description="Destination id assigned on create. None on a dry-run — nothing was created.",
    )


class StreamReattachDetail(BaseModel):
    """One restored channel that still cannot play (bead ``…-2o0cz``).

    The drill's headline: a restore reporting ``success … created 32, failed 0``
    produced 12 channels bound to URL-less PLACEHOLDER streams — every one of
    them unplayable — and nothing in the report said so. The rebind pass
    (:mod:`dbas.placeholder_rebind`) re-runs the 4-tier matcher once the real
    provider streams have materialized; whatever it still cannot resolve lands
    here as a NAMED action item.

    ``channel_id`` is the DESTINATION Dispatcharr channel id. ``name`` and
    ``placeholder_streams`` are operator-facing names — never a stream URL
    (which can embed a provider token).

    :attr:`has_playable_stream` splits the rows into the two populations that
    were previously indistinguishable (bead ``…-daziw``):

    * ``True`` — the channel kept at least one REAL, URL-bearing stream and is
      merely holding a leftover placeholder in one slot. IT PLAYS. This is the
      designed output of the ``…-ixdaw`` fix ("costs one slot instead of the
      entire channel"), and it is an action item, not a failure.
    * ``False`` — NOT ONE slot carries a URL. The channel cannot play, and this
      is what downgrades the restore outcome (see :class:`RestoreOutcome`).

    Additive optional field — no ``CONTRACT_VERSION`` bump. It defaults to
    ``True`` so a row deserialized from a report written before this field
    existed is never retroactively counted as unplayable; every producer sets it
    explicitly.
    """

    channel_id: int | None = Field(
        default=None,
        description="Destination Dispatcharr channel id, when known.",
    )
    name: str = Field(description="Operator-facing channel name — never a secret.")
    placeholder_streams: list[str] = Field(
        default_factory=list,
        description="Names of the URL-less placeholder streams still attached. Names only, never URLs.",
    )
    has_playable_stream: bool = Field(
        default=True,
        description=(
            "True when the channel still holds a real URL-bearing stream and "
            "plays; False when every slot is a URL-less placeholder."
        ),
    )


class EpgLinkMissDetail(BaseModel):
    """One restored channel whose EPG link could not be reattached (…-dfkbn item 2).

    A channel's ``epg_data_id`` points at a SOURCE-instance EPG row; on the
    destination that id is meaningless, so the link is re-derived from the
    channel's archived ``tvg_id`` (:func:`dbas.channel_reattach.reattach_epg_links`).
    When no destination EPG row carries that ``tvg_id`` the channel restores with
    no guide data — counted here rather than left silent (the drill lost 10 of 12
    links while the report showed zero failures).
    """

    channel_id: int | None = Field(default=None)
    name: str = Field(description="Operator-facing channel name — never a secret.")
    tvg_id: str = Field(
        default="",
        description="The archived tvg_id that resolved to no destination EPG row.",
    )


# Upper bound on how many channel NAMES a ReattachPopulation carries per list.
# The counts are exact; the name lists are illustrative and must not turn a
# routine merge report into a five-thousand-entry payload.
REATTACH_NAMED_CHANNEL_CAP = 50


class ReattachPopulation(BaseModel):
    """How ONE post-create reattach pass split across the two channel populations.

    Bead ``…-dfkbn``, PR review W1. A single ``relinked=N`` is too coarse once
    the pass can reach channels the restore did not create: "linked a channel we
    made" and "overwrote a link on a channel the operator already had" are
    different events and only one of them is destructive.

    Reported by BOTH the dry run and the apply, from the same code path, so the
    preview cannot mispredict the split the way the ``dgnms`` preview
    mispredicted the logo category. On a dry run these are the WOULD-BE numbers;
    on an apply they are what happened.

    Names, never ids or urls: ``existing_channels_named`` /
    ``preserved_channels_named`` are operator-facing channel names, modelled on
    :class:`ProfileMembershipDriftDetail`'s ``channels_disabled``.

    Both lists are CAPPED at :data:`REATTACH_NAMED_CHANNEL_CAP`; the counts
    beside them are never capped. Under the DEFAULT mode a merge into a
    5,000-channel install preserves all 5,000, and an uncapped list would write
    ten thousand channel names into ``TaskExecution.details`` on a run where
    nothing happened. The count is the decision input; the names are there to
    make it concrete, and a few dozen does that.
    """

    mode: ChannelReattachMode = Field(
        default=ChannelReattachMode.PRESERVE,
        description="The mode this pass ran under.",
    )
    created_channels: int = Field(
        default=0,
        description="Channels THIS restore created that got their archived reference.",
    )
    existing_channels: int = Field(
        default=0,
        description="Pre-existing channels whose reference was OVERWRITTEN with the archive's.",
    )
    preserved_channels: int = Field(
        default=0,
        description="Pre-existing channels left untouched because the mode is preserve.",
    )
    existing_channels_named: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the pre-existing channels that were overwritten, capped at "
            "REATTACH_NAMED_CHANNEL_CAP. The count above is not capped."
        ),
    )
    preserved_channels_named: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the pre-existing channels that were left alone, capped at "
            "REATTACH_NAMED_CHANNEL_CAP. The count above is not capped."
        ),
    )

    def name_existing(self, label: str) -> None:
        """Count one overwritten pre-existing channel, naming it up to the cap."""
        self.existing_channels += 1
        if len(self.existing_channels_named) < REATTACH_NAMED_CHANNEL_CAP:
            self.existing_channels_named.append(label)

    def name_preserved(self, label: str) -> None:
        """Count one left-alone pre-existing channel, naming it up to the cap."""
        self.preserved_channels += 1
        if len(self.preserved_channels_named) < REATTACH_NAMED_CHANNEL_CAP:
            self.preserved_channels_named.append(label)


class ProfileMembershipDriftDetail(BaseModel):
    """One channel profile whose membership had to be corrected (…-dfkbn item 3).

    Dispatcharr's channel create adds each new channel to EVERY channel profile
    with ``enabled=True`` (0.28.2 ``apps/channels/api_views.py``), so a profile
    seeded to EXCLUDE channels silently restores containing all of them — the
    worst possible default, because a profile built to hide channels now exposes
    them. The reattach pass re-asserts the archived membership; the number of
    channels it had to flip is the DRIFT, reported so the operator can see the
    restore did not simply inherit the destination's default.
    """

    profile_id: int | None = Field(default=None, description="Destination profile id.")
    name: str = Field(description="Operator-facing profile name — never a secret.")
    channels_disabled: list[str] = Field(
        default_factory=list,
        description="Channels the archive EXCLUDED that the destination had enabled.",
    )
    channels_enabled: list[str] = Field(
        default_factory=list,
        description="Channels the archive INCLUDED that the destination had disabled.",
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

    Outcome discipline: :attr:`outcome` is :class:`RestoreOutcome` and is
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
        description="Result of a realized restore. None on a dry-run "
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

    # Credential-re-entry aggregate (bead …-6pilh). Counts ENTITIES restored from
    # a redacted artifact whose credential fields were left UNSET because the
    # archive carried only the ``***REDACTED***`` placeholder. Zero on a
    # credential-bearing (encrypted + include_credentials) restore. ADDITIVE
    # optional — no CONTRACT_VERSION bump.
    #
    # This is a POST-RESTORE ACTION ITEM, not a failure: the entity was created
    # successfully and the outcome is unaffected. But the operator MUST be told —
    # an XC M3U account restored without its password authenticates nowhere and
    # materializes zero streams, and nothing in the counts reveals that.
    credentials_needing_reentry: int = Field(
        default=0,
        description="Count of entities needing a credential re-entered before they will work.",
    )

    # Per-entity drill-down behind the aggregate count; tracks it exactly
    # (``len(credential_reentry_details) == credentials_needing_reentry``).
    credential_reentry_details: list[CredentialReentryDetail] = Field(
        default_factory=list,
        description="Which entities need which credential fields re-entered (…-6pilh).",
    )

    # --- Post-restore action items (bead …-2o0cz / …-dfkbn). -----------------
    # Each pair below follows the ``credentials_needing_reentry`` precedent: an
    # AGGREGATE count plus a NAMED drill-down, written through ONE recorder so
    # they cannot drift. None of them is a failure — the entity was created and
    # the outcome is unaffected — but every one of them is state the operator had
    # before the backup and does not have after the restore, which is exactly
    # what the drill proved a clean ``success, 0 failures`` can hide.

    # Placeholder streams the restore synthesized (…-ahygg) that a channel is
    # still bound to after the post-refresh rebind pass. Non-zero means those
    # channels hold a slot that streams nothing — it does NOT mean they cannot
    # play, and it never has: a channel that kept its real streams and holds one
    # leftover placeholder is counted here and plays perfectly (…-daziw). Use
    # :attr:`channels_with_no_playable_stream` for the unplayability signal.
    #
    # NULL means NOT PREDICTED, and only a DRY RUN ever writes it (bead
    # …-dgnms). The pass that populates this counter is the post-refresh
    # placeholder rebind, which by construction cannot run on a preview: it
    # reads the provider streams the DEFERRED M3U refresh materializes, and a
    # dry run performs no refresh. Reporting ``0`` from a preview is therefore a
    # confident claim ("no channel needs attention") derived from having looked
    # at nothing — drill run 4 measured preview ``0`` against apply ``12`` on a
    # fresh target. ``None`` says what is true: this number is unknowable before
    # the apply. Consumers coerce with ``or 0``; see ``docs/api.md``.
    channels_needing_stream_reattach: int | None = Field(
        default=0,
        description=(
            "Channels still holding at least one URL-less placeholder stream slot. "
            "NULL on a dry run — the rebind pass cannot run before the apply."
        ),
    )
    stream_reattach_details: list[StreamReattachDetail] = Field(
        default_factory=list,
        description="Which channels still need their streams reattached (…-2o0cz).",
    )
    # The SUBSET of the above that cannot play at all: not one URL-bearing stream
    # is left on the channel (bead …-daziw). This is the aggregate the restore
    # outcome is downgraded on — see :class:`RestoreOutcome`. Tracks
    # ``len([d for d in stream_reattach_details if not d.has_playable_stream])``
    # by construction; both are written by ``record_stream_reattach_needed``.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    # NULL means NOT PREDICTED on a dry run, exactly as above.
    channels_with_no_playable_stream: int | None = Field(
        default=0,
        description=(
            "Channels left with NO URL-bearing stream at all — they cannot play. "
            "NULL on a dry run — the rebind pass cannot run before the apply."
        ),
    )
    # How many placeholder slots the rebind pass DID resolve onto real provider
    # streams. Purely informational — it is the counterpart to the counter above
    # and shows the operator the pass ran and did work.
    streams_rebound: int = Field(
        default=0,
        description="Placeholder stream bindings swapped for a real provider stream.",
    )

    # Channels restored without their guide link (…-dfkbn item 2).
    epg_links_unrestored: int = Field(
        default=0,
        description="Channels whose archived EPG link could not be reattached.",
    )
    epg_link_miss_details: list[EpgLinkMissDetail] = Field(
        default_factory=list,
        description="Which channels restored with no EPG link, and the tvg_id that missed.",
    )

    # How each reattach pass split across "channels this restore created" and
    # "channels that already existed" (…-dfkbn, PR review W1). Populated on the
    # dry run AND the apply by the same code, so the preview is the prediction.
    epg_link_reattach: ReattachPopulation = Field(
        default_factory=ReattachPopulation,
        description="EPG-link reattach split by channel population, and the mode used.",
    )
    logo_reattach: ReattachPopulation = Field(
        default_factory=ReattachPopulation,
        description="Channel-logo reattach split by channel population, and the mode used.",
    )

    # Channel-profile memberships the restore had to correct away from
    # Dispatcharr's enable-everything default (…-dfkbn item 3).
    profile_membership_drift: int = Field(
        default=0,
        description="Channel/profile memberships corrected back to the archived selection.",
    )
    profile_membership_drift_details: list[ProfileMembershipDriftDetail] = Field(
        default_factory=list,
        description="Which profiles drifted, and which channels were flipped back.",
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

    def record_credential_reentry(
        self,
        entity_type: EntityType,
        label: str,
        fields: list[str],
        *,
        source_export_id: int | None = None,
        destination_id: int | None = None,
    ) -> None:
        """Record that one entity needs a credential re-entered (bead …-6pilh).

        The ONE place the aggregate count and the detail list are both updated,
        so they cannot drift. A no-op when ``fields`` is empty — an entity whose
        credentials all came through intact is not an action item.

        Args:
            entity_type: The restored entity's category.
            label: Operator-facing entity identifier — never a secret.
            fields: Credential FIELD NAMES left unset. Names only, never values.
            source_export_id: The entity's id in the export archive, when known.
            destination_id: The destination id, or ``None`` on a dry-run.
        """
        if not fields:
            return
        self.credential_reentry_details.append(
            CredentialReentryDetail(
                entity_type=entity_type,
                label=label,
                fields=list(fields),
                source_export_id=source_export_id,
                destination_id=destination_id,
            )
        )
        self.credentials_needing_reentry = len(self.credential_reentry_details)

    def record_logo_miss(
        self,
        *,
        label: str,
        source_export_id: int | None = None,
        channels: "list[LogoMissChannel] | None" = None,
        label_is_synthetic: bool = False,
    ) -> None:
        """Record ONE logo the operator has LOST (aggregate + drill-down together).

        THE LOGO-MISS INVARIANT (the canonical definition; every producer below
        is written against THIS paragraph, and any new one must be too)
        ------------------------------------------------------------------

        A logo miss is a logo the operator HAD on the source and does NOT have
        after the restore. Nothing else. It is an OPERATOR-FACING loss report,
        not an internal bookkeeping counter, because it is rendered as loss:
        ``logo_misses > 0`` gates the D9 red ``LogoMissBanner``
        (``role="alert"``, "N logos are missing after this restore") and
        :mod:`tasks.dbas_restore` appends "N logo(s) could not be reinstated" to
        the one-line summary an operator who never opens the modal will see.

        Therefore the rule is symmetric and has no exceptions:

        * **Record a miss ONLY on a path where a logo failed to come back.**
        * **Never record a miss on a path that restored the logo**, by upload,
          by URL re-create, or by matching one the destination already had.
        * A DRY RUN records a miss only where the apply would record one, so the
          preview's loss count is the apply's loss count.

        The THREE producers, each recording only its own failure:

        1. :func:`dbas.importers.logos.import_logos` upload path: the logo's
           bytes could not be read, validated, or uploaded. NOT on a successful
           upload, and NOT merely because the destination lacked the logo.
        2. :func:`dbas.importers.logos._create_logo_from_url`: the archived URL
           could not be re-created upstream. NOT on a successful create.
        3. :func:`dbas.channel_reattach.reattach_channel_logos`: an archived
           logo REFERENCE could not be put back onto the channels that had it.
           This is the one the drill needed: 12 channels lost a logo they had
           and the report said ``logo_misses: 0`` (bead ``…-dfkbn`` item 1).

        The question "did the destination already have this logo?" is a
        DIFFERENT question and is answered by
        :attr:`dbas.importers.logos.LogoImportResult.misses`, an internal
        counter that is never rendered to an operator. Do not conflate them: the
        two directions of that conflation are the whole history of this surface.
        ``…-dfkbn`` under-reported (every logo lost, ``logo_misses: 0``) and
        ``…-xb58a`` over-reported (a logo that restored fine counted as lost).

        ONE LOST LOGO IS ONE ROW, HOWEVER MANY PRODUCERS SEE IT (bead ``…-k2r7m``)
        -------------------------------------------------------------------------

        Producers 1 and 3 above fire on the SAME failure by design: an upload the
        destination rejected leaves no entry in the ``LOGO`` remap, so the
        reattach pass that runs next cannot resolve the reference either. Both
        reports are correct; they are not two losses. ``logo_misses`` counts
        LOGOS (see :class:`LogoMissDetail`), so a second report of an archived
        logo already recorded MERGES into its row rather than appending:

        * the affected channels are UNIONED — each producer sees a different
          slice (the importer sees every channel that referenced the logo; the
          reattach pass sees the ones whose PATCH it could not perform), and
          dropping either slice under-names the damage;
        * the operator-facing display name WINS over a synthesized one. The
          reattach pass holds only the archive id, so it labels its rows
          ``"logo #13 (archived)"`` and declares that with
          ``label_is_synthetic``; a producer that knows the archived NAME does
          not, and its label survives the merge regardless of which ran first.

        Identity is :attr:`LogoMissDetail.source_export_id`, the archived logo's
        id. A miss recorded WITHOUT one carries no identity to merge on and
        always gets its own row — under-counting a real loss is the failure this
        surface exists to prevent, so ambiguity resolves toward reporting.

        The run9 drill measured the pre-merge behaviour: one logo failed, and the
        report read ``failed 1`` beside ``2 logo(s) could not be reinstated``.

        The single place :attr:`logo_misses` and :attr:`logo_miss_details` are
        both updated, so ``len(logo_miss_details) == logo_misses`` holds by
        construction. Call THIS method; never increment the field directly.

        Args:
            label: Operator-facing logo name — never a path, URL, or secret.
            source_export_id: The logo's id in the export archive, when known.
            channels: The affected channels (destination id where known + name).
            label_is_synthetic: ``True`` when ``label`` was composed from the
                archive id because the producer never had the logo's name. Such
                a label yields to a real one on a merge.
        """
        incoming = list(channels or [])
        existing = self._logo_miss_for(source_export_id)
        if existing is not None:
            if not label_is_synthetic:
                existing.label = label
            existing.channels = _merge_logo_miss_channels(existing.channels, incoming)
            return
        self.logo_miss_details.append(
            LogoMissDetail(
                source_export_id=source_export_id,
                label=label,
                channels=incoming,
            )
        )
        self.logo_misses = len(self.logo_miss_details)

    def _logo_miss_for(self, source_export_id: int | None) -> "LogoMissDetail | None":
        """The already-recorded row for this archived logo, if any (…-k2r7m).

        ``None`` for an unknown ``source_export_id``: without the archive id
        there is no identity, so nothing merges.
        """
        if source_export_id is None:
            return None
        for detail in self.logo_miss_details:
            if detail.source_export_id == source_export_id:
                return detail
        return None

    def record_stream_reattach_needed(
        self,
        *,
        name: str,
        channel_id: int | None = None,
        placeholder_streams: list[str] | None = None,
        has_playable_stream: bool = True,
    ) -> None:
        """Record ONE channel still holding a placeholder (beads ``…-2o0cz`` / ``…-daziw``).

        The ONE place BOTH aggregates and the detail list are updated, so they
        cannot drift: :attr:`channels_needing_stream_reattach` counts every row,
        :attr:`channels_with_no_playable_stream` counts only the rows that
        cannot play. Call this method; never increment either field directly.

        Args:
            name: Operator-facing channel name — never a secret.
            channel_id: Destination Dispatcharr channel id, when known.
            placeholder_streams: Names of the URL-less placeholders still bound.
            has_playable_stream: Whether ANY real, URL-bearing stream is left on
                the channel. ``False`` is what makes the restore's outcome
                ``COMPLETED_WITH_FAILURES`` — the channel cannot play.
        """
        self.stream_reattach_details.append(
            StreamReattachDetail(
                channel_id=channel_id,
                name=name,
                placeholder_streams=list(placeholder_streams or []),
                has_playable_stream=has_playable_stream,
            )
        )
        self.channels_needing_stream_reattach = len(self.stream_reattach_details)
        self.channels_with_no_playable_stream = sum(
            1 for detail in self.stream_reattach_details if not detail.has_playable_stream
        )

    def mark_stream_health_unpredicted(self) -> None:
        """Say ``not predicted`` instead of ``0`` for the stream-health counters.

        Bead ``…-dgnms``. Called by :func:`dbas.restore_orchestrator.run_restore`
        on a DRY RUN, where the post-refresh placeholder rebind that writes these
        two aggregates cannot run at all: it matches against the provider streams
        the DEFERRED M3U refresh materializes, and a preview refreshes nothing.
        The default ``0`` is then not a measurement, it is the absence of one —
        drill run 4 read ``0`` from a preview whose apply immediately reported
        ``12``, which is worse for the operator than no number at all.

        Deliberately does NOT touch :attr:`streams_rebound`: ``0`` is literally
        true there — a preview rebinds nothing — and it is a work-done counter,
        not a "channels needing attention" alarm.

        A no-op once a detail row exists, so it can never erase a real count.
        """
        if self.stream_reattach_details:
            return
        self.channels_needing_stream_reattach = None
        self.channels_with_no_playable_stream = None

    def record_epg_link_unrestored(
        self,
        *,
        name: str,
        channel_id: int | None = None,
        tvg_id: str = "",
    ) -> None:
        """Record ONE channel restored without its guide link (…-dfkbn item 2)."""
        self.epg_link_miss_details.append(
            EpgLinkMissDetail(channel_id=channel_id, name=name, tvg_id=tvg_id)
        )
        self.epg_links_unrestored = len(self.epg_link_miss_details)

    def record_profile_membership_drift(
        self,
        *,
        name: str,
        profile_id: int | None = None,
        channels_disabled: list[str] | None = None,
        channels_enabled: list[str] | None = None,
    ) -> None:
        """Record ONE profile whose membership was corrected (…-dfkbn item 3).

        The AGGREGATE counts CHANNELS flipped (the operator-meaningful unit —
        "3 channels a profile was built to exclude were exposed"), not profiles,
        so it does NOT track ``len(profile_membership_drift_details)``. A call
        that flips nothing is a no-op: an already-correct profile is not drift.
        """
        disabled = list(channels_disabled or [])
        enabled = list(channels_enabled or [])
        if not disabled and not enabled:
            return
        self.profile_membership_drift_details.append(
            ProfileMembershipDriftDetail(
                profile_id=profile_id,
                name=name,
                channels_disabled=disabled,
                channels_enabled=enabled,
            )
        )
        self.profile_membership_drift += len(disabled) + len(enabled)


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
