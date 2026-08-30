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
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, Field

from credential_sentinel import credential_path_is_operator_actionable

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
    # …-tyrg1. The Dispatcharr ``ServerGroup`` an M3U account's ``server_group``
    # FK points at. Measured against dispatcharr:latest (0.29.0) on 2026-08-23,
    # not inherited from the 0.28.2 reading the bead was filed on: the model
    # still carries EXACTLY ONE field, a unique ``name``
    # (``apps/m3u/models.py:216``); its serializer still exposes exactly
    # ``["id", "name"]`` (``apps/m3u/serializers.py:420``); and its only
    # behavioural consumer is still ``apps/m3u/connection_pool.py``, which keys
    # a Redis counter on ``(group_id, credential fingerprint)``. (The two other
    # references are a ``select_related`` hint in ``apps/channels/models.py``
    # and the Django admin — neither reads the row's content.)
    #
    # It is a LEAF and it is ordered BEFORE M3U_ACCOUNT in all three registries,
    # because the account's FK resolves through this namespace (bead ``…-9h6cv``
    # established FK-owner-before-dependent as the pattern; ``…-efvyg``
    # established that all three registries move together).
    SERVER_GROUP = "server_group"          # …-tyrg1
    DVR_RULE = "dvr_rule"                  # …-0i2vt.13
    # …-ciabe. The DVR recording INSTANCES that have not started yet. Named for
    # what it holds rather than by contrast with its sibling: a row leaves this
    # category the moment its start time passes, and the name says so without
    # needing DVR_RULE for context. COMPLETED recordings are NOT a second
    # EntityType — they are never archived at all (they reference a media file on
    # the source instance's disk), and the exclusion is reported by the backup
    # producer rather than modelled here.
    UPCOMING_RECORDING = "upcoming_recording"
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
    # A destination row was ADOPTED because its NAME matched, and nothing beyond
    # the name was (or could be) compared — bead …-3t74w. Distinct from
    # ALREADY_EXISTS_IDENTICAL, which asserts the destination row MATCHES the
    # archive's. Used by the CHANNEL_GROUP category, whose only cross-instance
    # identity IS the name (kxuj2 contract; ADR-008): a group's contents live on
    # the CHANNELS, which are restored after it, so at match time there is
    # nothing to compare and "identical" was a claim the importer had not
    # earned. Run 12 measured a target group named ``Drill Movies`` holding a
    # DIFFERENT channel adopted under that reason with the restore reporting
    # ``success / failed 0``. The adopt itself is contractual and unchanged; the
    # divergence in CONTENTS is reported separately, after channels, as
    # :attr:`RestoreReport.channel_group_drift`.
    ALREADY_EXISTS_NAME_MATCH = "already_exists_name_match"
    EXCLUDED_BY_OPERATOR = "excluded_by_operator"           # category opt-out / selection
    CURRENT_ADMIN_PRESERVED = "current_admin_preserved"     # …-l1p4p D11 guard
    UNSUPPORTED_IN_THIS_VERSION = "unsupported_in_this_version"  # e.g. plugins (ADR-012 D10)
    # The archived entity is pinned to an ABSOLUTE moment that has since passed,
    # so applying it would schedule work in the past (bead …-ciabe). Today only
    # the UPCOMING_RECORDING category can reach it: a recording archived last
    # week whose start time is now behind us.
    #
    # NEVER A SHORTFALL, and the reason is not "it is only a skip". The
    # destination CANNOT hold this row: Dispatcharr answers a past-dated create
    # ``400 "End time must be in the future."`` (measured on 0.29.0 — see
    # ``tests/fixtures/dispatcharr_recordings_recorded.json``). A replica that
    # does not carry a programme which has already aired has lost nothing, and
    # counting it would put a permanent non-zero beside every restore of an
    # archive older than its own recordings — bead …-15g1j's crying wolf.
    SCHEDULE_ALREADY_PAST = "schedule_already_past"
    # A required remap target is missing AND the run was asked to deliver it —
    # so the replica is missing something the operator selected. Its aggregate,
    # :attr:`RestoreReport.entities_blocked_by_dependency`, is a member of
    # :data:`RestoreReport.DELIVERY_SHORTFALL_FIELDS`.
    DEPENDENCY_UNRESOLVED = "dependency_unresolved"         # required remap target missing
    # The same missing remap target, when the operator EXCLUDED the category it
    # lives in and the skipped record is a LINK INTO that category (bead
    # …-4mkoe). Split out of ``DEPENDENCY_UNRESOLVED``, which carried both and
    # so could report neither: a link into a category the operator asked to
    # leave out was never going to resolve, and naming it is bead ``…-15g1j``'s
    # crying wolf on every unattended cycle, forever. Never a shortfall.
    #
    # The test is a CONJUNCTION, and the second half is load-bearing: deselecting
    # ``channel_groups`` while selecting ``channels`` strands CHANNELS, which the
    # operator DID ask for — a loss, not a faithful absence. Only a record filed
    # UNDER the deselected category is entailed by the operator's own selection.
    # See :meth:`RestoreReport.record_dependency_unresolved`, the one place that
    # decides it.
    DEPENDENCY_DESELECTED = "dependency_deselected"


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
      * an APPLY produced a replica MISSING SOMETHING THE SOURCE HAD — any
        member of :attr:`RestoreReport.DELIVERY_SHORTFALL_FIELDS` is non-zero
        (beads ``…-daziw``, ``…-posm1``). Every row "succeeded" and the counts
        are clean, which is exactly how the drill's ``success … created 32,
        failed 0`` described an instance whose channels returned HTTP 500 with 0
        bytes, and how the cross-instance sync's ``success … created 133,
        failed 0`` described a replica that had lost 53 of 59 guide links and
        every logo binding. A replica missing what it was asked to carry is
        mixed state, so SUCCESS is forbidden. Nothing is rolled back — the
        applied state is real, kept, and recoverable.

        The membership rules — including why
        :attr:`RestoreReport.channels_needing_stream_reattach` is NOT a member
        and must never become one, and why a FAITHFUL absence is not one — are
        on ``DELIVERY_SHORTFALL_FIELDS`` itself. Read them before adding to the
        set; both exclusions have already been implemented wrongly once.
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

    @property
    def is_degraded_not_failed(self) -> bool:
        """True when the run FINISHED and left real, kept state (…-daziw/…-cwmid).

        The ONE definition of "degraded, not failed", so the two task wrappers
        that build a ``TaskResult`` from a restore report — ``tasks.dbas_restore``
        and ``tasks.dbas_sync`` — cannot disagree about which runs are a
        ``warning`` and which are the red "Task Failed". They DID disagree: the
        restore learned the rule with this bead and the cross-instance sync,
        running on the same orchestrator and sharing the outcome downgrade, kept
        announcing "Task Failed" for a run whose only shortfall was a channel
        left unable to play (PO decision 2026-08-19).

        Keyed on the OUTCOME rather than on any particular shortfall, because
        :attr:`COMPLETED_WITH_FAILURES` already MEANS "ran to completion and
        NOTHING was rolled back", for either of its two triggers. Bead
        ``…-cwmid`` measured what keying on the shortfall instead costs: a
        restore where 12 of 12 channels could not play alerted ``warning`` while
        one unwritable logo — a deliberately NON-FATAL category — alerted
        ``error``, inverting the severity ordering for triage.

        ``error`` therefore stays reserved for what it describes: the two
        rolled-back / indeterminate outcomes, where the caller either got
        nothing it asked for or cannot tell what it got.

        NOT a statement about dry runs: a preview has no realized outcome to
        degrade, and each caller guards that before asking (a predicted
        shortfall is a prediction, not a failure). Enforced by
        ``tests/tasks/test_dbas_restore_unplayable_alert.py`` and
        ``tests/tasks/test_dbas_sync_unplayable_alert.py``.
        """
        return self is RestoreOutcome.COMPLETED_WITH_FAILURES


class ChannelReattachMode(str, Enum):
    """What the post-create reattach passes do to channels this restore did NOT create.

    Bead ``…-dfkbn``, PR review W1. The reattach passes
    (:mod:`dbas.channel_reattach`) put a channel's archived EPG link and logo
    back after the create, because both are SOURCE ids the create payload has to
    drop. A channel that already existed on the destination is matched
    ``ALREADY_EXISTS_IDENTICAL`` and is deliberately never overwritten for its
    name or number, but it IS entered in the CHANNEL remap so a reattach
    pass can resolve it. That makes the pre-existing channel reachable by these
    PATCHes even though nothing else about it is touched.

    WHAT THE MODE GOVERNS (widened by bead ``…-r1ei7``). It started as "EPG link
    and logo". It now also governs the channel's GROUP: run 12 restored onto a
    populated target and measured not one channel's ``channel_group_id``
    corrected to the archive's, in EITHER mode, with nothing in the report
    saying so. Grouping belongs to the same question as the other two — "what
    happens to a channel I already had" — and answering it differently from the
    logo would be an inconsistency the operator has no way to predict. The
    DIFFERENCE from the other two is that :attr:`PRESERVE` still REPORTS the
    divergence (:attr:`RestoreReport.channel_group_drift`): leaving state alone
    is a choice, leaving it unmentioned is the defect.

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


class StreamUrlRedactionDetail(BaseModel):
    """One stream created with the provider credentials cut out of its URL (…-msqf7).

    A real Xtream Codes provider serves every live stream at
    ``/live/<user>/<pass>/<id>.ts`` — the credential IS part of the address, so
    the address cannot be carried without it. The redactor replaces those path
    segments with the sentinel and the rest of the URL crosses intact, which
    means the destination holds a stream that names where it pointed and cannot
    play until the destination's own provider account supplies a credential.

    That is a post-restore ACTION ITEM, not a failure — the stream was created
    and the outcome is unaffected — and it is exactly the class of shortfall the
    …-6pilh / …-dfkbn drills proved a clean ``success, 0 failures`` can hide.
    """

    stream_id: int | None = Field(
        default=None, description="Destination stream id, when upstream returned one."
    )
    label: str = Field(description="Operator-facing stream name — never a secret.")


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


class AccountFieldDriftDetail(BaseModel):
    """One replicated M3U account whose FIELDS differ from the source's (…-zszjd).

    THE DEFECT THIS EXISTS FOR. An M3U account that already exists on the
    destination is matched ``ALREADY_EXISTS_IDENTICAL`` and, until this bead, was
    never written to again — for EVERY field, not one. Measured during bead
    ``…-avrix``: ``auto_enable_new_groups_live`` was flipped on the source, a
    cycle ran, and the replica stayed on the old value, silently and
    permanently. The flag was one example; the ruling covered ``name``,
    ``server_url``, ``max_streams``, ``user_agent``, ``refresh_interval``,
    ``custom_properties``, the credential fields and the four preference
    booleans alike.

    THE INVARIANT THIS SURFACE ENFORCES, stated as a property rather than as
    that reproduction: **a field set on the source is the field set on the
    replica after the next cycle, for every field except those carrying a
    written exclusion** (``dbas.importers.m3u_accounts.NEVER_CONVERGE_FIELDS``,
    which names each one and why). Drift that still exists is COUNTED here on
    every cycle, so the failure mode the defect actually had — silence — cannot
    return even if a write is refused.

    FIELD NAMES ONLY, NEVER VALUES. ``fields`` is a list of names; a converging
    account carries ``username``, ``password`` and ``server_url``, and this
    report is rendered to operators and journalled. The same rule
    :meth:`RestoreReport.record_credential_reentry` follows.
    """

    destination_account_id: int | None = Field(
        default=None, description="Destination Dispatcharr M3U account id."
    )
    name: str = Field(description="Operator-facing account name — never a secret.")
    fields: list[str] = Field(
        default_factory=list,
        description="FIELD NAMES that differed from the source. Never any value.",
    )
    applied: bool = Field(
        default=False,
        description="Whether this cycle actually wrote the fields to the replica "
        "(always False on a dry run — nothing is written there).",
    )
    reason: str | None = Field(
        default=None,
        description="Why the write did not happen — a sanitized phrase, never a "
        "secret. None when it did.",
    )


class ProviderGroupSelectionDetail(BaseModel):
    """One replicated M3U account whose per-group selection did not fully land (…-avrix).

    An XC provider account's per-group ENABLED selection is the setting that
    decides what the account ingests. Measured live 2026-08-21: a source account
    with 2 of 777 provider categories enabled produced a replica holding **zero**
    ``ChannelGroupM3UAccount`` rows, and the replica's own refresh then logged
    ``Filtered 0 streams from 0 enabled categories`` and aborted — 0 streams
    where the source has 316. With the source's ``auto_enable_new_groups_live``
    at Dispatcharr's own default (``True`` — ``apps/m3u/serializers.py`` pops it
    with a ``True`` fallback on create), the SAME missing selection sends the
    replica the other way: its discovery refresh enabled all 777 of 777
    categories, which is the provider's whole 53,661-stream catalogue.

    So the counter is a LOSS, in the ``…-15g1j`` sense: it counts only group
    selections the SOURCE actually had and the destination account did not
    receive. A source account carrying no selection at all produces no rows and
    no count.

    Ids, not names: the deferred settings are keyed by DESTINATION account id and
    SOURCE channel-group pk, and neither the account name nor the group names are
    in scope at apply time. An account NAME would be safe to render (it is not a
    secret) but is not available here without a second read of the destination.
    """

    destination_account_id: int = Field(
        description="Destination Dispatcharr M3U account id the selection was for.",
    )
    selections_total: int = Field(
        description="Per-group selections the SOURCE account carried.",
    )
    selections_applied: int = Field(
        default=0,
        description="Selections written to the destination account.",
    )
    selections_unapplied: int = Field(
        default=0,
        description="Selections the destination account did NOT receive.",
    )
    enabled_applied: int = Field(
        default=0,
        description="Of the applied selections, how many were ENABLED — what the "
        "replica would ingest.",
    )
    reason: str = Field(
        description="Why they did not land — a sanitized phrase, never a secret.",
    )


class ChannelGroupDriftDetail(BaseModel):
    """One restored channel sitting in a group the archive does not put it in (…-r1ei7).

    The channel-GROUP counterpart to :class:`ProfileMembershipDriftDetail`. A
    channel that already exists on the destination is matched
    ``ALREADY_EXISTS_IDENTICAL`` and never overwritten (spike ``xp6mp``), so its
    ``channel_group_id`` is never written — on a populated target the lineup
    silently keeps whatever grouping the destination had while the counts
    reconcile exactly.

    Both group fields are operator-facing NAMES, never ids: the ids are
    instance-local and mean nothing to the person reading the report. ``moved``
    is ``True`` only in :attr:`ChannelReattachMode.OVERWRITE`, where the pass
    also puts the channel back in the archive's group (on a dry run it is the
    WOULD-BE move, like every other counter these passes predict).
    """

    channel_id: int | None = Field(
        default=None, description="Destination Dispatcharr channel id, when known."
    )
    name: str = Field(description="Operator-facing channel name — never a secret.")
    current_group: str = Field(
        description="The group the destination has this channel in — a name, never an id.",
    )
    archive_group: str = Field(
        description="The group the archive puts this channel in — a name, never an id.",
    )
    moved: bool = Field(
        default=False,
        description=(
            "True when the channel was moved into the archive's group (or, on a "
            "dry run, would be). Always False under the preserve mode."
        ),
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

    # --- How far the counts above can be trusted (bead …-tddmw). -------------
    # Both are DRY-RUN concepts; an apply reports facts, so it leaves them at
    # their defaults. ADDITIVE optional — no CONTRACT_VERSION bump.
    #
    # ``predicted=False`` says this preview did NOT predict the category at all,
    # and its zeroes must be rendered as "not predicted" rather than as a
    # confident zero. It is the per-category form of the ``None`` the
    # stream-health counters already use (bead …-dgnms): run 12 measured an
    # apply reporting ``Streams 9 CREATED`` against a preview that omitted the
    # Streams row ENTIRELY — not zero, absent — which is worse than either,
    # because an absent row cannot be argued with.
    predicted: bool = Field(
        default=True,
        description=(
            "False when a dry run could NOT predict this category — render the "
            "counts as 'not predicted', never as a confident zero."
        ),
    )
    # A short, operator-facing sentence about why the counts may not match the
    # apply. Sanitized prose, never a secret. ``None`` when there is nothing to
    # qualify.
    caveat: str | None = Field(
        default=None,
        description="Operator-facing note qualifying this category's counts. No secrets.",
    )


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

    # THE DELIVERY-SHORTFALL SET (bead ``…-posm1``) ---------------------------
    #
    # The aggregates that mean, each in its own vocabulary, THE SOURCE HAD THIS
    # AND THE REPLICA DOES NOT. Declared once, here, because two consumers must
    # not be able to disagree about the list: ``restore_orchestrator`` decides
    # the OUTCOME from it, and ``tasks.dbas_restore`` renders each member as a
    # clause in the operator's one-line summary.
    #
    # THE INVARIANT (the specification; the members are examples of it):
    #
    #     A run never presents as an unqualified SUCCESS when the replica it
    #     produced is missing something the source had and the run was asked to
    #     carry.
    #
    # WHAT THE MEMBERSHIP TEST IS, and it is deliberately narrow on both sides:
    #
    # * It is a LOSS, not an action item. Every member counts something the
    #   destination is missing right now, by comparison with the source — never
    #   a chore the operator has to do, and never work the run performed.
    # * A FAITHFUL absence is not a member (bead ``…-15g1j``). Implementing the
    #   literal "anything absent" reading turned all ten keystone round-trip
    #   scenarios red for replications that had lost nothing, so every member
    #   below is a counter whose PRODUCER already restricts it to things the
    #   source actually had: ``epg_links_unrestored`` is computed only over
    #   archive channels carrying an ``epg_data_id``, ``logo_misses`` has its own
    #   canonical loss-only invariant (see :meth:`record_logo_miss`),
    #   ``stream_urls_redacted`` counts destination rows ECM itself cut an
    #   address out of, and ``channels_with_no_playable_stream`` was narrowed by
    #   ``…-15g1j`` itself.
    # * Something the run was asked NOT to carry is not a member. That excludes
    #   ``credentials_needing_reentry`` (the redaction is deliberate — bead
    #   ``…-msqf7`` — and its CONSEQUENCE, a replica that cannot play, is already
    #   a member through the two stream counters), ``channel_group_drift`` under
    #   the default preserve mode, and both :class:`ReattachPopulation`'s
    #   ``preserved_channels``.
    # * Work the run DID is not a member: ``streams_rebound`` and, since bead
    #   ``…-ukjx5`` made it read the destination first, ``profile_membership_drift``
    #   — a membership that had drifted and was corrected leaves the replica
    #   MATCHING, which is the opposite of a shortfall.
    # * ``channels_needing_stream_reattach`` is not a member and must never
    #   become one: a channel that kept its real streams and holds one leftover
    #   placeholder PLAYS (bead ``…-daziw``).
    # * ``entities_blocked_by_dependency`` is a member, and it is the first one
    #   whose PRODUCER had to be split before it could join (bead ``…-4mkoe``).
    #   ``SkipReason.DEPENDENCY_UNRESOLVED`` covered two opposite situations —
    #   an upstream category the operator EXCLUDED, and one that was in scope and
    #   still is not there — so surfacing the reason as it stood would have
    #   reported the first as loudly as the second, forever, on every unattended
    #   cycle. Only the second increments this counter. It passes the clearability
    #   test that excludes ``credentials_needing_reentry``: restore the dependency
    #   (re-take the degraded backup, fix the collision, add the category to the
    #   selection) and the next cycle counts zero. The NAMED drill-down is the
    #   per-category ``skip_details`` the same recorder writes — there is no
    #   parallel detail list to drift out of step with the count.
    #
    # KEY ON THE OUTCOME, NEVER ON WHICH MEMBER FIRED. Bead ``…-cwmid`` had to
    # UNDO a narrower keying after drill run 2026-08-06-run9 measured the
    # severity ordering INVERTED — 12-of-12 channels unplayable alerted
    # ``warning`` while one cosmetic logo failure alerted ``error``/"Task
    # Failed". Every member here resolves to the SAME
    # ``COMPLETED_WITH_FAILURES``, which
    # :attr:`RestoreOutcome.is_degraded_not_failed` already maps to ``warning``
    # with a per-task ``alert_on_warning`` opt-out. Adding a member therefore
    # cannot reorder severities, because no member is ever consulted for one.
    DELIVERY_SHORTFALL_FIELDS: ClassVar[tuple[str, ...]] = (
        "channels_with_no_playable_stream",
        "stream_urls_redacted",
        "epg_links_unrestored",
        "logo_misses",
        "entities_blocked_by_dependency",
        # …-zszjd. Fields the SOURCE set that an APPLY tried and failed to write
        # onto the replica's existing M3U account. Both membership tests pass.
        # LOSS: it counts only fields the source actually has and the replica
        # actually lacks — a field the source never set produces no diff and no
        # count, and a dry run never contributes (nothing was attempted there,
        # so nothing can have fallen short). CLEARABLE: the next cycle re-derives
        # the diff from a fresh read of the destination, so a write that later
        # succeeds takes the counter to zero with no operator action at all;
        # there is no reachable selection under which it is permanently non-zero
        # for an absence the operator asked for (deselecting ``m3u_accounts``
        # skips the whole category before any diff is computed), which is the
        # ``…-4mkoe`` DEPENDENCY_DESELECTED trap this test exists to catch.
        # DELIBERATELY NOT the sibling ``account_field_drift``: that one counts
        # every difference INCLUDING the ones this cycle successfully converged,
        # so making it a shortfall would forbid SUCCESS on exactly the cycle that
        # fixed the problem.
        "account_convergence_unapplied",
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

    # Entities the run was asked to deliver and did not, because a dependency
    # they need is not on the destination (bead …-4mkoe). A DELIVERY_SHORTFALL
    # member; the reasoning for its membership is on the declaration above.
    #
    # Counts ONLY the genuine half. A skip whose missing dependency lives in a
    # category the operator EXCLUDED, and which is itself a link into that
    # category, is recorded ``SkipReason.DEPENDENCY_DESELECTED`` and never
    # counted here — that absence is what the operator asked for, and counting it
    # would put a permanent non-zero beside a replica that has lost nothing.
    #
    # ADDITIVE optional — no CONTRACT_VERSION bump (the module's rule at line 54:
    # bump when a field's MEANING changes, not for additive optional fields).
    # Written ONLY through :meth:`record_dependency_unresolved`, which writes the
    # count and the ``skip_details`` row in the same call so the aggregate and
    # its drill-down cannot disagree.
    entities_blocked_by_dependency: int = Field(
        default=0,
        description=(
            "Archived entities not restored because a dependency they need is "
            "absent from the destination and the run was asked to deliver it."
        ),
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

    # --- Provider-credential transmission signals (PO ruling 2026-08-22) ---
    # ADDITIVE optional, no CONTRACT_VERSION bump (the ``provider_group_selection_*``
    # precedent). Neither is a shortfall member and neither moves the outcome:
    # they are the AUDIT of what this cycle carried.
    #
    # WHAT THESE REPLACED. Two fields recorded whether a destination provider
    # account was OBSERVED to hold a credential; they existed to feed the S11
    # ``insecure`` refusal, which is gone (the PO removed it: "I know the
    # security risks. That's on the user to mitigate, not us."). With nothing
    # reading them they were deleted rather than left to read as live
    # bookkeeping — the same reason the two marker columns were dropped.
    #
    # These two are their honest replacement. Under per-cycle transmission the
    # audit trail is the only record of what moved, so every cycle states HOW
    # MANY provider records it carried a credential onto and NAMES them with the
    # field names. Names and labels only — no value, no fragment of a value, no
    # masked tail of a value.
    provider_credentials_transmitted: int = Field(
        default=0,
        description="Provider records this cycle carried a credential onto "
        "(count only, never a value).",
    )
    provider_credential_transmission_details: list[str] = Field(
        default_factory=list,
        description="One line per provider record carrying a credential — "
        "operator-facing label plus FIELD NAMES only, never values.",
    )

    # THE STALENESS SIGNAL (INV-8 / S12(b)). A standby whose provisioned
    # credential stopped working says so, on the cycle that can observe it,
    # WITHOUT any credential value crossing the wire to determine it and WITHOUT
    # triggering a push. Derived from destination ``status`` / ``stream_count``,
    # which the cycle already reads (see
    # ``dbas.importers.m3u_accounts.destination_account_looks_stale``).
    provisioned_credentials_stale: int = Field(
        default=0,
        description="Replicated provider accounts whose state indicates their "
        "credential has stopped working (ADR-013 INV-8).",
    )
    provisioned_credential_stale_details: list[str] = Field(
        default_factory=list,
        description="One sanitized operator-facing line per stale replicated "
        "provider account. Names and counts only — never an upstream message.",
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

    # Streams the DESTINATION currently holds whose URL had the provider
    # credentials cut out of it (…-msqf7). Counts STREAMS, tracking
    # ``len(stream_url_redaction_details)``.
    #
    # A FACT ABOUT THE DESTINATION, NOT ABOUT THIS RUN (bead …-ukjx5). It used to
    # be recorded where ``create_stream`` succeeded, which made it a count of
    # what THIS CYCLE wrote — and a cross-instance sync is a SCHEDULED task whose
    # second cycle creates nothing, because the rows are already there and are
    # skipped. Measured live on 0.29.0 across consecutive cycles on an UNCHANGED
    # destination: 53, then 0, while 53 of B's streams still carried a redacted
    # address and could not play. The first cycle's honesty is what made the
    # silence convincing. It is now recomputed from the destination's own stream
    # rows by the post-refresh rebind pass, which already reads them, so a
    # shortfall the replica still exhibits is reported on EVERY cycle and one it
    # no longer exhibits is reported on none.
    #
    # NULL means NOT PREDICTED, exactly as for the two counters above and for the
    # same reason: the pass that reads the destination's streams cannot run on a
    # dry run. Now that the number describes the DESTINATION, a preview ``0``
    # would be a claim ("B holds no redacted stream") derived from having looked
    # at nothing. Consumers coerce with ``or 0``.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    stream_urls_redacted: int | None = Field(
        default=0,
        description=(
            "Destination streams holding a URL the credentials were cut out of — "
            "they cannot play. NULL on a dry run: the pass that reads the "
            "destination's streams cannot run before the apply."
        ),
    )
    stream_url_redaction_details: list[StreamUrlRedactionDetail] = Field(
        default_factory=list,
        description="Which streams lost the credential half of their URL (…-msqf7).",
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

    # Channels sitting in a group the archive does not put them in (…-r1ei7).
    # The channel-GROUP sibling of the pair above, and the counter run 12 needed
    # and did not have: a populated-target restore kept the destination's whole
    # grouping in BOTH relink modes while reporting ``success / failed 0``.
    # Counts CHANNELS (the operator-meaningful unit), tracking
    # ``len(channel_group_drift_details)`` — one row per drifted channel.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    channel_group_drift: int = Field(
        default=0,
        description="Channels whose group differs from the one the archive assigns them.",
    )
    channel_group_drift_details: list[ChannelGroupDriftDetail] = Field(
        default_factory=list,
        description="Which channels drifted, the group they are in, and the archive's.",
    )
    # Per-account provider GROUP-ENABLE selections the destination account did
    # not receive (bead …-avrix). Distinct from ``channel_group_drift`` above,
    # which is about which group a CHANNEL sits in: this is about which of a
    # PROVIDER ACCOUNT's groups are switched on, i.e. what that account ingests
    # on its next refresh. ``channel_group_drift`` reported ``0`` through the
    # whole 2026-08-21 acceptance run while the replica held zero selections,
    # because it was measuring a different thing — not wrong, silent.
    #
    # DELIBERATELY NOT A ``DELIVERY_SHORTFALL_FIELDS`` MEMBER. It passes the
    # loss test (its producer counts only selections the source had) but fails
    # the CLEARABILITY test in one reachable configuration: an operator who
    # deselects the ``channel_groups`` category leaves the destination without
    # the groups these selections point at, so every cycle would count the same
    # non-zero forever with no action that clears it — bead ``…-4mkoe``'s
    # ``DEPENDENCY_DESELECTED`` trap, which cost the ``entities_blocked_by_dependency``
    # counter a producer split before it could join the set. Joining it would
    # need the same conjunction. It is rendered as a named action-item clause
    # instead (the ``profile_membership_drift`` precedent), so it is visible
    # without moving the outcome.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    provider_group_selection_unapplied: int = Field(
        default=0,
        description="Provider per-group ENABLE selections the source had that the "
        "destination's M3U account did not receive.",
    )
    provider_group_selection_details: list[ProviderGroupSelectionDetail] = Field(
        default_factory=list,
        description="Which replicated M3U accounts did not receive their group "
        "selection, how many, and why.",
    )

    # Fields on an ALREADY-EXISTING replica M3U account that differ from the
    # source's (bead …-zszjd). Counts FIELDS, not accounts — the
    # operator-meaningful unit is "3 settings on this account are not what you
    # set on the primary", exactly as ``profile_membership_drift`` counts
    # channels rather than profiles.
    #
    # DELIBERATELY NOT a ``DELIVERY_SHORTFALL_FIELDS`` member, and for the same
    # reason ``profile_membership_drift`` is not: it counts every difference the
    # cycle FOUND, including the ones it then successfully converged. A cycle
    # that detects three drifted fields and writes all three has done its job;
    # forbidding SUCCESS there would put a permanent red mark on the fix. The
    # half that IS a shortfall is ``account_convergence_unapplied`` below.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    account_field_drift: int = Field(
        default=0,
        description="Fields on an existing replica M3U account that differ from "
        "the source's.",
    )
    account_field_drift_details: list[AccountFieldDriftDetail] = Field(
        default_factory=list,
        description="Which accounts drifted, WHICH FIELD NAMES (never values), "
        "and whether this cycle wrote them.",
    )

    # The shortfall half of the pair above: fields an APPLY tried to converge and
    # could not. Membership reasoning is on ``DELIVERY_SHORTFALL_FIELDS``.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    account_convergence_unapplied: int = Field(
        default=0,
        description="Drifted fields an apply attempted to write onto the replica "
        "and could not.",
    )

    # Why the count above is not a finding, when it is not one. The pass is
    # SKIPPED when the operator deselects the channel-groups category (no
    # archived group resolves through the remap, so every matched channel would
    # report drift this restore was never asked to touch), and a skipped pass
    # leaves ``channel_group_drift`` at ``0`` — which reads as "nothing drifted"
    # when the truth is "nothing was examined". That is the SAME ambiguity the
    # ``predicted`` flag and the per-category ``caveat`` exist to kill
    # (bead …-tddmw), so it gets the same treatment: a short operator-facing
    # sentence, sanitized prose, never a secret. ``None`` when the pass ran.
    # ADDITIVE optional — no CONTRACT_VERSION bump.
    channel_group_drift_note: str | None = Field(
        default=None,
        description=(
            "Operator-facing note saying the channel-group check did NOT run "
            "(so its zero is 'not examined', not 'no drift'). No secrets."
        ),
    )

    # THE "I never read the destination" marker (bead …-jqfxm). Every count in
    # this report is a claim ABOUT the destination — "would create 24" means
    # "B does not have these 24". But each importer degrades a failed
    # destination read to ``existing = []`` ("B is empty"), so a run that could
    # not authenticate to B produced exactly the same shape as a run against an
    # empty B: a full would-create plan with zero failures. Live validation of
    # the cross-instance sync measured that against a WRONG PASSWORD — a green
    # preview, an unlocked Apply button, and seven 401/429s in B's log that no
    # surface mentioned.
    #
    # So: whenever a run could not read the destination it claims to describe —
    # credentials rejected, rate-limited, unreachable, TLS/DNS/SSRF refused, a
    # 5xx, or aborted before any read happened — this carries the sanitized
    # reason, and NOTHING may report the run as a success. Deliberately NOT a
    # category ``failed`` counter: a dry run's ``failed`` legitimately carries
    # source-side CONFLICTs (duplicate names, ambiguous null channel numbers)
    # that are facts about the SOURCE and do not mean the destination went
    # unread. Sanitized (status code + error class, never a URL, body or
    # credential). ADDITIVE optional — no CONTRACT_VERSION bump.
    destination_unreadable: str | None = Field(
        default=None,
        description=(
            "Set when the run could NOT read the destination it claims to "
            "describe (auth rejected, rate-limited, unreachable, or aborted "
            "before any read). A report carrying this is never a success, and "
            "its counts describe the source, not the destination. No secrets."
        ),
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

    def delivery_shortfalls(self) -> dict[str, int]:
        """Every :data:`DELIVERY_SHORTFALL_FIELDS` member this report carries.

        A pure read of the counts — it applies no dry-run policy, because
        "should a PREDICTED shortfall change an outcome?" is an outcome
        question and is answered where the outcome is decided
        (``dbas.restore_orchestrator.compute_outcome``, which refuses to
        downgrade a preview: a prediction of a shortfall is not a failure, and
        nothing was applied to be missing).

        Returns:
            ``{field name: count}`` for each member with a non-zero count, in
            declaration order. Empty when the replica lost nothing. ``None``
            (the "not predicted" value the dry-run counters carry) reads as
            zero, exactly as every other consumer coerces it.
        """
        found: dict[str, int] = {}
        for name in self.DELIVERY_SHORTFALL_FIELDS:
            count = getattr(self, name, 0) or 0
            if count > 0:
                found[name] = count
        return found

    def record_dependency_unresolved(
        self,
        *,
        recorded_under: EntityType,
        dependency: EntityType,
        label: str,
        remap: "IdRemapTable",
        is_dry_run: bool,
        source_export_id: int | None = None,
    ) -> SkipReason:
        """Record one entity skipped for an unresolvable dependency (bead …-4mkoe).

        THE ONE PLACE that decides which of the two opposite facts an unresolved
        dependency is, and the one place that writes both the classification and
        the aggregate. Declared once for the reason ``…-posm1`` declared
        :data:`DELIVERY_SHORTFALL_FIELDS` once: the reason on the
        ``skip_details`` row, the per-category count, and the top-level shortfall
        aggregate are three views of one decision, and three call sites deciding
        it separately is how they drift.

        THE RULE, a conjunction whose second half is load-bearing::

            faithful  <=>  the dependency's category was DESELECTED
                           AND the skip is recorded UNDER that same category

        The first half alone is wrong, and wrong in the direction that hides
        losses. Deselecting ``channel_groups`` while selecting ``channels``
        strands every grouped CHANNEL — a first-class entity the operator asked
        for, whose absence is a shortfall no matter why its group is missing. The
        only absence the operator's own selection ENTAILS is a LINK INTO the
        excluded category, and this restore already files such a link under that
        category rather than under the entity that carries it (an archived
        channel's profile membership is recorded under ``CHANNEL_PROFILE``,
        never under ``CHANNEL``). An ENTITY of a deselected category never
        reaches here at all — its own importer skips it ``EXCLUDED_BY_OPERATOR``
        before any FK is resolved — so ``recorded_under == dependency`` cannot
        mean anything else.

        FAIL LOUD when the run scope was never recorded. ``remap`` answers
        "deselected?" with ``False`` until :meth:`IdRemapTable.record_run_scope`
        has been called (``run_restore`` does it, once, for every path). The
        defect this method exists to fix is UNDER-reporting, so an unknown scope
        reports the loss rather than silencing it.

        Args:
            recorded_under: The category whose report slice carries the skip.
            dependency: The category of the id that could not be resolved.
            label: Operator-facing name of the skipped entity. Never a secret.
            remap: The run-scoped :class:`IdRemapTable` — the object whose
                ``resolve`` returned ``None``, and the one that knows the scope.
            is_dry_run: Whether this is a preview (counts ``would_skip``).
            source_export_id: The archive id of the skipped entity, if any.

        Returns:
            The :class:`SkipReason` recorded, so a caller can log which it was.
        """
        faithful = recorded_under == dependency and remap.category_deselected(
            dependency
        )
        reason = (
            SkipReason.DEPENDENCY_DESELECTED
            if faithful
            else SkipReason.DEPENDENCY_UNRESOLVED
        )
        cat = self.category(recorded_under)
        if is_dry_run:
            cat.would_skip += 1
        else:
            cat.skipped += 1
        cat.skip_details.append(
            SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
        )
        if not faithful:
            self.entities_blocked_by_dependency += 1
        return reason

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

        NOR IS ONE THE OPERATOR CANNOT PERFORM (bead ``…-posm1``). Paths inside
        the destination's own cached copy of the provider's reply
        (``…custom_properties.user_info.*``) are dropped by
        :func:`credential_sentinel.credential_path_is_operator_actionable`
        before anything is recorded: there is no field to re-enter them into,
        and the destination rewrites the blob itself on its next successful
        refresh. Measured live on 0.29.0 — with them counted, the operator's
        line still read "1 account(s) need credentials re-entered" AFTER the
        real credentials had been entered and had cleared the account's own
        ``username``/``password``. An action item that cannot be cleared by
        doing what it asks is its own defect.

        Args:
            entity_type: The restored entity's category.
            label: Operator-facing entity identifier — never a secret.
            fields: Credential FIELD NAMES left unset. Names only, never values.
            source_export_id: The entity's id in the export archive, when known.
            destination_id: The destination id, or ``None`` on a dry-run.
        """
        if not fields:
            return
        # Drop the paths the operator has no way to act on before anything is
        # recorded, so the aggregate, the detail rows, the modal and the
        # one-line summary all describe the same work (bead ``…-posm1``). A row
        # left with nothing actionable is not an action item and is not
        # recorded at all — the same disposition an empty ``fields`` gets.
        fields = [
            field for field in fields
            if credential_path_is_operator_actionable(field)
        ]
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

    def record_provider_credential_transmission(self, detail: str) -> None:
        """Record ONE provider record this cycle carried a credential onto.

        The audit half of the 2026-08-22 ruling. Aggregate and drill-down
        written through ONE recorder so they cannot drift, exactly like
        :meth:`record_credential_reentry`.

        **Names only.** ``detail`` is an operator-facing label plus the FIELD
        NAMES carried; no value, no fragment of a value, no masked tail of a
        value ever reaches this report.

        Args:
            detail: ``"<label> (<field>, <field>)"``.
        """
        if not detail or detail in self.provider_credential_transmission_details:
            return
        self.provider_credential_transmission_details.append(detail)
        self.provider_credentials_transmitted = len(
            self.provider_credential_transmission_details
        )

    def record_provisioned_credential_stale(self, message: str) -> None:
        """Record ONE replicated provider account whose credential looks stale.

        ADR-013 INV-8 / S12(b). Aggregate and drill-down written through ONE
        recorder so they cannot drift, exactly like
        :meth:`record_credential_reentry`.

        It is an ACTION ITEM, not a shortfall and not a failure: the operator
        re-runs the provisioning action. It must never itself trigger a push —
        S12(c) forbids scheduled or automatic re-push, and INV-2 is what
        enforces that structurally.

        Args:
            message: A sanitized operator-facing line. Names and counts only —
                never an upstream error body, which can quote a request URL.
        """
        if not message or message in self.provisioned_credential_stale_details:
            return
        self.provisioned_credential_stale_details.append(message)
        self.provisioned_credentials_stale = len(
            self.provisioned_credential_stale_details
        )

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

        :attr:`stream_urls_redacted` JOINED THIS SET with bead ``…-ukjx5``, which
        redefined it from "streams this run created redacted" to "streams the
        DESTINATION now holds redacted". The pass that reads the destination's
        streams is the same one, so the dry-run answer is the same one: not
        predicted, rather than a confident zero about a destination nothing
        looked at.

        A no-op per counter once its own detail rows exist, so it can never
        erase a real count.
        """
        if not self.stream_url_redaction_details:
            self.stream_urls_redacted = None
        if self.stream_reattach_details:
            return
        self.channels_needing_stream_reattach = None
        self.channels_with_no_playable_stream = None

    def record_redacted_stream_urls(
        self, observed: Sequence[tuple[int | None, str]]
    ) -> None:
        """REPLACE the redacted-URL population with what the destination holds (…-msqf7).

        REPLACES rather than appends, and that is the whole of bead ``…-ukjx5``.
        The append form could only ever describe what THIS cycle had just
        written, so a repeat cycle — which writes nothing, because the rows are
        already there — reported zero over a destination whose streams were all
        still redacted. Taking the whole population from one destination reading
        makes the number say what is TRUE NOW: it can go up, it can go down, and
        it stays put while the destination does.

        Idempotent by construction: calling it twice with the same reading gives
        the same answer, where a second append would have doubled it.

        Args:
            observed: ``(destination stream id, operator-facing label)`` for every
                destination stream currently holding a redacted address. Labels
                are stream NAMES — never a URL, which for an Xtream Codes
                provider IS the credential.
        """
        self.stream_url_redaction_details = [
            StreamUrlRedactionDetail(stream_id=stream_id, label=label)
            for stream_id, label in observed
        ]
        self.stream_urls_redacted = len(self.stream_url_redaction_details)

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

    def record_account_field_drift(
        self,
        *,
        name: str,
        fields: list[str],
        destination_account_id: int | None = None,
        applied: bool = False,
        reason: str | None = None,
    ) -> None:
        """Record ONE existing replica M3U account whose fields differ (…-zszjd).

        The ONE place :attr:`account_field_drift`, its detail list, and
        :attr:`account_convergence_unapplied` are all written, so the aggregates
        and the drill-down cannot drift out of step with each other — the same
        single-writer rule :meth:`record_credential_reentry` follows.

        The aggregate counts FIELDS (the operator-meaningful unit: "3 settings on
        this account do not match the primary"), so it tracks the total length of
        every ``fields`` list rather than the number of detail rows. A call with
        an empty ``fields`` is a no-op — an account that already matches is not
        drift, and counting it would put a permanent non-zero beside a faithful
        replica.

        ``account_convergence_unapplied`` — the ``DELIVERY_SHORTFALL_FIELDS``
        half — rises ONLY when a write was attempted and did not land
        (``applied=False`` with a ``reason``). A dry run passes ``applied=False``
        and ``reason=None``: it attempted nothing, so it has fallen short of
        nothing, and a preview must never manufacture a shortfall the apply it
        previews would not have.

        **Field NAMES only.** A converging account carries ``username``,
        ``password`` and ``server_url``; the names are safe and the values never
        enter this report.

        Args:
            name: the account's operator-facing name — never a secret.
            fields: the FIELD NAMES that differed. Never any value.
            destination_account_id: the replica's id for the account.
            applied: whether this cycle wrote the fields.
            reason: sanitized phrase saying why it did not. ``None`` on a dry run
                and on a successful write.
        """
        names = [f for f in (fields or []) if isinstance(f, str) and f]
        if not names:
            return
        self.account_field_drift_details.append(
            AccountFieldDriftDetail(
                destination_account_id=destination_account_id,
                name=name,
                fields=sorted(set(names)),
                applied=applied,
                reason=reason,
            )
        )
        self.account_field_drift += len(set(names))
        if not applied and reason is not None:
            self.account_convergence_unapplied += len(set(names))

    def record_channel_group_drift(
        self,
        *,
        name: str,
        current_group: str,
        archive_group: str,
        channel_id: int | None = None,
        moved: bool = False,
    ) -> None:
        """Record ONE channel sitting in a group the archive does not assign it (…-r1ei7).

        The ONE place the aggregate count and the detail list are both updated,
        so they cannot drift apart — the ``credentials_needing_reentry``
        precedent. Called in BOTH relink modes: ``preserve`` records the
        divergence and changes nothing, ``overwrite`` records it and sets
        ``moved``.

        Args:
            name: Operator-facing channel name — never a secret.
            current_group: The group the destination has the channel in, by NAME.
            archive_group: The group the archive puts the channel in, by NAME.
            channel_id: The destination channel id, when known.
            moved: Whether the channel was (or on a dry run would be) moved into
                the archive's group.
        """
        self.channel_group_drift_details.append(
            ChannelGroupDriftDetail(
                channel_id=channel_id,
                name=name,
                current_group=current_group,
                archive_group=archive_group,
                moved=moved,
            )
        )
        self.channel_group_drift = len(self.channel_group_drift_details)

    def record_provider_group_selection(
        self,
        *,
        destination_account_id: int,
        selections_total: int,
        selections_applied: int,
        selections_unapplied: int,
        enabled_applied: int,
        reason: str,
    ) -> None:
        """Record ONE replicated M3U account's group-selection outcome (…-avrix).

        The ONE place the aggregate count and the detail list are both updated,
        so they cannot drift apart — the same rule
        :meth:`record_channel_group_drift` follows.

        A FULLY-APPLIED account records NOTHING: a run that carried every
        selection is not carrying a finding, and a counter that reads non-zero
        on every converged cycle forever is the ``…-posm1`` noise problem this
        report exists to avoid. Only ``selections_unapplied > 0`` is recorded.

        Args:
            destination_account_id: The destination M3U account id.
            selections_total: Per-group selections the SOURCE account carried.
            selections_applied: Selections written to the destination.
            selections_unapplied: Selections the destination did NOT receive.
            enabled_applied: Of the applied selections, how many were ENABLED.
            reason: Sanitized phrase saying why they did not land — never a secret.
        """
        if selections_unapplied <= 0:
            return
        self.provider_group_selection_details.append(
            ProviderGroupSelectionDetail(
                destination_account_id=destination_account_id,
                selections_total=selections_total,
                selections_applied=selections_applied,
                selections_unapplied=selections_unapplied,
                enabled_applied=enabled_applied,
                reason=reason,
            )
        )
        self.provider_group_selection_unapplied = sum(
            d.selections_unapplied for d in self.provider_group_selection_details
        )


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

    # The categories THIS RUN was asked to carry (bead …-4mkoe). It lives here,
    # beside the mappings, because ``resolve`` returning ``None`` is the ONE fact
    # every unresolved-dependency skip is derived from, and "was this namespace
    # ever going to be populated?" is the question that makes that ``None``
    # readable. Every importer already holds this table; threading the plan's
    # selection separately into five importer signatures would put the same fact
    # in five places.
    #
    # ``None`` means NOT RECORDED, which is deliberately distinct from "nothing
    # was selected": an empty set would make every category read as deselected
    # and silence every shortfall. Written once by
    # ``dbas.restore_orchestrator.run_restore``.
    selected_categories: set[EntityType] | None = Field(
        default=None,
        description="Categories this run was asked to carry; None = not recorded.",
    )

    def record_run_scope(self, selected: Iterable[EntityType]) -> None:
        """Record which categories this run was asked to carry.

        Args:
            selected: The entity types the operator opted into for this run.
        """
        self.selected_categories = set(selected)

    def category_deselected(self, entity_type: EntityType) -> bool:
        """True when the operator EXCLUDED ``entity_type`` from this run.

        A category absent from the plan entirely reads the same as one the
        operator unticked — which is what the orchestrator's own ``_selected``
        already means, so the two cannot disagree.

        FAIL LOUD: ``False`` until :meth:`record_run_scope` has been called. A
        caller must never be able to claim a deselection this table cannot
        prove; see :meth:`RestoreReport.record_dependency_unresolved`.
        """
        if self.selected_categories is None:
            return False
        return entity_type not in self.selected_categories

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
