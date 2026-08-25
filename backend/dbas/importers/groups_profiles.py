"""The bulk groups/profiles restore importer — Phase-2 leaf dependencies.

Bead ``enhancedchannelmanager-0i2vt.12``. This module restores the THREE
leaf-dependency categories that the Channels importer (bead
``enhancedchannelmanager-4vouz``) consumes, together as one "bulk importer":

1. **Channel groups**   -> :data:`EntityType.CHANNEL_GROUP`
2. **Channel profiles** -> :data:`EntityType.CHANNEL_PROFILE`
3. **Stream profiles**  -> :data:`EntityType.STREAM_PROFILE`

Each category restores its archived rows and POPULATES the shared
:class:`~dbas.restore_contracts.IdRemapTable` under its own EntityType. The
Channels importer reads those mappings to rewrite ``channel_group_id`` /
``stream_profile_id`` FK references and channel-profile memberships before
sending channels upstream. These three are LEAF dependencies — channels point at
THEM, so they must be restored BEFORE channels (the hard Phase-2 ordering:
``M3U → EPG → groups/profiles → Channels``; ADR-012). This importer only restores
its rows + populates the remap; the dependency ORDERING across categories is the
orchestrator's job (bead ``…-0i2vt.18``).

It mirrors the established Phase-2 importer pattern (``importers/m3u_accounts.py``
/ ``importers/channels.py`` / ``importers/users.py``): opt-in per category,
consumes the shared restore contracts
(:class:`~dbas.restore_contracts.IdRemapTable`,
:class:`~dbas.restore_contracts.RollbackLedger`,
:class:`~dbas.restore_contracts.RestoreReport`, the Skip/Failure taxonomy),
per-entity results, and dry-run support. It imports the contracts module
READ-ONLY.

----------------------------------------------------------------------------
IDENTITY / MATCH KEY (per category)
----------------------------------------------------------------------------

All three categories match an existing destination row by **name**,
case-insensitive and whitespace-trimmed (:func:`_norm_name`). This mirrors ECM's
existing upsert-by-name behaviour for channel groups (``backup.py`` —
``_restore_channel_groups``) and stream profiles, and is the only stable identity
these rows carry across instances (their numeric ids differ). On a name match the
row is SKIPPED and its source id is remapped to the EXISTING destination id so a
later FK reference resolves — NEVER the delete-all-then-recreate strategy, which
would destroy the very relationships the remap exists to preserve (kxuj2
contract; ADR-008 grooming note).

WHAT THE SKIP CLAIMS (bead ``…-3t74w``). Channel profiles and stream profiles
report ``ALREADY_EXISTS_IDENTICAL``. **Channel groups do not**, because that
claim was never earned: a Dispatcharr channel group carries nothing but a name,
and its CONTENTS are the ``channel_group_id`` on the CHANNELS — restored after
this importer, so at match time there is nothing to compare. Drill run 12
(2026-08-07) built a target group named ``Drill Movies`` that was a genuinely
different object (different id, holding a different channel), watched the restore
adopt it, and read ``already exists identical`` and ``success / failed 0`` back.
Channel groups therefore report ``ALREADY_EXISTS_NAME_MATCH`` — the true
statement — and the CONTENT divergence is reported after the channels step as
:attr:`~dbas.restore_contracts.RestoreReport.channel_group_drift`
(:func:`dbas.channel_reattach.reconcile_channel_groups`). The ADOPT and the FK
remap are unchanged: name is the identity, and the alternative (a
``CONFLICT`` failure) would cascade to ``DEPENDENCY_UNRESOLVED`` for every
channel pointing at the group and lose more than it reported.

----------------------------------------------------------------------------
FK REMAP
----------------------------------------------------------------------------

The generic per-category engine rewrites outbound FK references through the
IdRemapTable (unresolvable -> ``DEPENDENCY_UNRESOLVED``, never sent upstream with
a stale archive id).

Channel groups and channel profiles are genuine leaf dependencies with NO
outbound remappable FK — they do not reference another remapped entity; channels
reference them. Their :attr:`CategoryConfig.remappable_fk_fields` is empty.

STREAM PROFILES ARE NOT A LEAF (bead ``enhancedchannelmanager-lvfwd``). A
Dispatcharr stream profile carries a ``user_agent`` FK. This module previously
asserted the opposite and shipped an EMPTY ``remappable_fk_fields`` for the
category, so the archived SOURCE instance's user-agent id was POSTed verbatim at
the destination. Against a fresh Dispatcharr that 400s ("Invalid pk ... object
does not exist") and aborts the whole restore; against a destination that happens
to have an unrelated user agent at that id the create SUCCEEDS and silently binds
the WRONG agent. ``stream_profiles`` therefore declares
``{"user_agent": EntityType.USER_AGENT}`` and the orchestrator restores user
agents BEFORE stream profiles so the namespace is populated when this runs
(``dbas.restore_orchestrator.default_importer_steps``).

----------------------------------------------------------------------------
CREATE-METHOD SHAPES (verify-then-size: all three client methods pre-existed)
----------------------------------------------------------------------------

* ``client.create_channel_group(name)`` — takes a NAME STRING (not a dict). The
  engine passes the row's name; channel groups carry no other create fields.
* ``client.create_channel_profile(data)`` — takes a payload DICT.
* ``client.create_stream_profile(data)`` — takes a payload DICT.

The ``payload_style`` on :class:`CategoryConfig` selects which calling
convention the engine uses; no new client methods were required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Archive-source identifiers the destination assigns itself, never forwarded.
_SOURCE_ID_KEYS = frozenset({"id", "pk"})

# Embedded / read-only / derived keys that are NOT part of a create payload.
# ``channels`` / membership lists are owned by the Channels importer (4vouz) and
# reattached there — never sent on a group/profile create. The counts and
# timestamps are read-only fields a GET echoes back.
_NON_CREATE_KEYS = frozenset(
    {
        "channels",
        "channel_count",
        "channels_count",
        "channelprofilemembership_set",
        "channel_count_total",
        "created_at",
        "updated_at",
    }
)


@dataclass(frozen=True)
class CategoryConfig:
    """Per-category restore configuration for the generic import engine.

    One config per restorable category. ``remappable_fk_fields`` maps an archive
    payload field name to the :class:`EntityType` whose IdRemapTable namespace
    rewrites it; it is EMPTY for the two genuine leaf categories (channel groups
    and channel profiles carry no outbound FK) and carries ``user_agent`` for
    stream profiles (bead ``…-lvfwd``). ``payload_style`` selects the client
    create calling convention: ``"name"`` calls ``create(name_string)`` (channel
    groups), ``"dict"`` calls ``create(payload_dict)`` (channel/stream profiles).
    """

    entity_type: EntityType
    getter: str
    creator: str
    log_prefix: str
    payload_style: str = "dict"  # "dict" | "name"
    remappable_fk_fields: dict = field(default_factory=dict)
    # What a NAME match against the destination is reported as (bead …-3t74w).
    # ``ALREADY_EXISTS_IDENTICAL`` asserts the destination row matches the
    # archive's; for channel groups nothing is ever compared beyond the name, so
    # that category reports ``ALREADY_EXISTS_NAME_MATCH`` instead. See the
    # module docstring's IDENTITY section.
    name_match_skip_reason: SkipReason = SkipReason.ALREADY_EXISTS_IDENTICAL
    # Operator-facing qualifier attached to this category's DRY-RUN counts only
    # (bead …-tddmw). ``None`` when the preview's counts need no qualification.
    dry_run_caveat: str | None = None


# Canonical config table for the three categories. Keyed by the archive section
# name used in the export (and by the bulk entry's ``selected`` map).
_CATEGORY_CONFIGS: dict[str, CategoryConfig] = {
    "channel_groups": CategoryConfig(
        entity_type=EntityType.CHANNEL_GROUP,
        getter="get_channel_groups",
        creator="create_channel_group",
        log_prefix="DBAS-CHGROUP",
        payload_style="name",
        # A channel group carries no attribute but its name, and its CONTENTS
        # live on the channels — restored AFTER it. "Identical" was never
        # checked and cannot be at this point (bead …-3t74w).
        name_match_skip_reason=SkipReason.ALREADY_EXISTS_NAME_MATCH,
        # Run 12: preview ``378 will create / 0 will skip`` vs apply ``3 created
        # / 375 skipped``. The counts are right for the state a preview can see —
        # the deferred M3U ingest materializes the provider groups before this
        # category runs on the apply, and a preview refreshes nothing. Say so
        # rather than model an ingest the preview cannot perform (bead …-tddmw).
        dry_run_caveat=(
            "Restoring an M3U account makes its provider groups appear before "
            "this category runs, so the apply may create far fewer groups than "
            "this preview shows and skip the rest. The end state is the same."
        ),
    ),
    # …-tyrg1. A Dispatcharr ``ServerGroup`` groups M3U accounts that share
    # provider credentials so they share a credential-scoped connection counter.
    # Measured against dispatcharr:latest (0.29.0) on 2026-08-23 rather than
    # carried over from the 0.28.2 reading the bead was filed on: the model has
    # EXACTLY ONE field, a unique ``name``, and its serializer exposes exactly
    # ``["id", "name"]``. So this is the smallest possible entity category, and
    # it exists for the FK — an M3U account's ``server_group`` had no namespace
    # to remap through and was therefore always dropped (bead ``…-g8tyd``),
    # leaving a replica whose accounts do not share a connection limit until an
    # operator recreates the grouping by hand.
    #
    # A NAME MATCH IS ALL THERE IS, and ``ALREADY_EXISTS_IDENTICAL`` is honest
    # here in a way it is not for channel groups: the name IS the whole row, so
    # a name match genuinely is an identical row. Nothing is left uncompared.
    "server_groups": CategoryConfig(
        entity_type=EntityType.SERVER_GROUP,
        getter="get_server_groups",
        creator="create_server_group",
        log_prefix="DBAS-SRVGROUP",
        payload_style="dict",
    ),
    "channel_profiles": CategoryConfig(
        entity_type=EntityType.CHANNEL_PROFILE,
        getter="get_channel_profiles",
        creator="create_channel_profile",
        log_prefix="DBAS-CHPROFILE",
        payload_style="dict",
    ),
    "stream_profiles": CategoryConfig(
        entity_type=EntityType.STREAM_PROFILE,
        getter="get_stream_profiles",
        creator="create_stream_profile",
        log_prefix="DBAS-STRPROFILE",
        payload_style="dict",
        # lvfwd — NOT a leaf. A stream profile points at a user agent, whose id
        # the restore reassigns; send the DESTINATION id or nothing at all.
        remappable_fk_fields={"user_agent": EntityType.USER_AGENT},
    ),
}


def _row_label(archive_row: dict) -> str:
    """Operator-facing identifier for a row — its name. Never a secret (these
    categories carry no credentials, but we stay consistent with the pattern)."""
    name = archive_row.get("name")
    return str(name) if name else "<unknown>"


def _norm_name(value) -> str | None:
    """Case-insensitive, trimmed key for a name; None when absent/blank."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed or None


def _existing_by_name(existing_rows: list) -> dict[str, dict]:
    """Index existing destination rows by their normalized name (lowest id wins
    on a duplicate name, deterministic / order-independent)."""
    index: dict[str, dict] = {}
    for row in existing_rows or []:
        if not isinstance(row, dict):
            continue
        key = _norm_name(row.get("name"))
        if key is None:
            continue
        current = index.get(key)
        if current is None:
            index[key] = row
            continue
        # Keep the lowest destination id for a stable, order-independent pick.
        cur_id = current.get("id")
        new_id = row.get("id")
        if new_id is not None and (cur_id is None or int(new_id) < int(cur_id)):
            index[key] = row
    return index


def _existing_by_raw_name(existing_rows: list) -> dict[str, dict]:
    """Index rows by exact raw name, omitting any ambiguous duplicate."""
    index: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for row in existing_rows or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or name in ambiguous:
            continue
        if name in index:
            index.pop(name)
            ambiguous.add(name)
            continue
        index[name] = row
    return index


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create failure into a restore-contract FailureReason.

    A name/uniqueness conflict maps to ``CONFLICT``; everything else is an
    upstream API error. We inspect the exception's short text only.
    """
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _is_channel_group_name_create_race(config: CategoryConfig, exc: Exception) -> bool:
    """Recognize the observed Dispatcharr channel-group uniqueness response."""
    text = str(exc).lower()
    return (
        config.entity_type == EntityType.CHANNEL_GROUP
        and "channel group creation failed: 400 -" in text
        and '"name"' in text
        and "channel group with this name already exists." in text
    )


def _sanitize_failure(exc: Exception, noun: str) -> str:
    """Produce a sanitized, operator-facing failure message (no raw traces)."""
    return (str(exc) or "").strip() or f"Upstream rejected the {noun} creation request."


def _build_create_payload(
    archive_row: dict, config: CategoryConfig, remap: IdRemapTable
) -> tuple[dict | None, EntityType | None]:
    """Build a create payload dict, rewriting any remappable FK ids to dest ids.

    Returns ``(payload, None)`` on success, or ``(None, entity_type)`` naming the
    FK namespace that could not be resolved through ``remap`` (the row must then
    be skipped rather than created with a stale archive id). Drops the archive
    source id and the embedded/read-only non-create keys.

    The namespace is named so the caller can classify the skip (bead ``…-4mkoe``)
    instead of guessing which category the row was waiting on.
    """
    dropped = _SOURCE_ID_KEYS | _NON_CREATE_KEYS
    payload = {k: v for k, v in archive_row.items() if k not in dropped}
    for field_name, entity_type in config.remappable_fk_fields.items():
        source_id = archive_row.get(field_name)
        if source_id is None:
            continue
        dest_id = remap.resolve(entity_type, int(source_id))
        if dest_id is None:
            return None, entity_type
        payload[field_name] = dest_id
    return payload, None


def _skip(cat, reason: SkipReason, label: str, source_export_id, is_dry_run: bool) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(
        SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
    )


async def _import_category(
    *,
    config: CategoryConfig,
    archive_rows: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore ONE category: create rows, populate the remap, record the ledger.

    The generic engine behind the three per-category entries. Behaviour mirrors
    the established Phase-2 importers:

    * OPT-IN — off unless ``selected``; an unselected category records every row
      ``EXCLUDED_BY_OPERATOR`` and creates nothing.
    * Collision — a name match (case-insensitive/trimmed) against the destination
      is skipped with the category's ``name_match_skip_reason``
      (``ALREADY_EXISTS_IDENTICAL`` for the two profile categories,
      ``ALREADY_EXISTS_NAME_MATCH`` for channel groups — bead ``…-3t74w``) and
      the source id is remapped to the EXISTING destination id (never
      delete-all-then-recreate).
    * FK remap — any ``remappable_fk_fields`` are rewritten through the remap;
      unresolvable -> ``DEPENDENCY_UNRESOLVED`` (never a stale id upstream).
    * Dry-run — reports ``would_create`` / ``would_skip``; no creates, no ledger.
    * Failure taxonomy — the observed channel-group name-uniqueness race is
      re-listed and adopted if exactly one row has the submitted raw name. Any
      race without an unambiguous owner remains a fatal ``CONFLICT``; other
      errors are ``UPSTREAM_API_ERROR``.

    Args:
        config: The per-category configuration (entity type, client methods,
            payload style, FK fields).
        archive_rows: The category's rows from the export archive.
        client: The Dispatcharr API client.
        selected: The per-category opt-in flag.
        report: The shared :class:`RestoreReport`; results land in
            ``config.entity_type``'s category.
        ledger: The shared :class:`RollbackLedger`; each created row is recorded
            for compensating deletes.
        remap: The shared :class:`IdRemapTable`; WRITTEN with each created (or
            collision-resolved) row's source->dest id under ``config.entity_type``.
        is_dry_run: When ``True``, nothing is created.
    """
    cat = report.category(config.entity_type)
    noun = config.entity_type.value.replace("_", " ")

    # OPT-IN. Off unless the operator selected this category.
    if not selected:
        logger.info("[%s] Category not selected; skipping %ss.", config.log_prefix, noun)
        for archive_row in archive_rows:
            _skip(
                cat,
                SkipReason.EXCLUDED_BY_OPERATOR,
                _row_label(archive_row),
                archive_row.get("id"),
                is_dry_run,
            )
        return

    logger.info(
        "[%s] Restoring %ss (dry_run=%s); %d archived row(s).",
        config.log_prefix,
        noun,
        is_dry_run,
        len(archive_rows),
    )

    # A PREVIEW's counts for this category may not be what the apply does, for a
    # reason the preview cannot remove (bead …-tddmw). Say so on the category
    # itself so every surface that renders the report carries it — an apply
    # reports facts and never carries a caveat.
    if is_dry_run and config.dry_run_caveat:
        cat.caveat = config.dry_run_caveat

    # Pre-fetch existing rows to detect name collisions (safe field only).
    try:
        existing = await getattr(client, config.getter)()
    except Exception as exc:
        logger.warning(
            "[%s] Could not list existing %ss: %s", config.log_prefix, noun, exc
        )
        existing = []
    existing_by_name = _existing_by_name(existing)
    existing_by_raw_name = _existing_by_raw_name(existing)

    for archive_row in archive_rows:
        label = _row_label(archive_row)
        source_id = archive_row.get("id")

        # Collision: a row with the same name already on the destination.
        raw_name = archive_row.get("name")
        name_key = _norm_name(raw_name)
        existing_row = (
            existing_by_raw_name.get(raw_name) if isinstance(raw_name, str) else None
        )
        if existing_row is None and name_key:
            existing_row = existing_by_name.get(name_key)
        if existing_row is not None:
            _skip(cat, config.name_match_skip_reason, label, source_id, is_dry_run)
            existing_id = existing_row.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(config.entity_type, int(source_id), int(existing_id))
            logger.info(
                "[%s] %s '%s' matched an existing destination row by name "
                "(dest id=%s); adopted, nothing created.",
                config.log_prefix,
                noun,
                label,
                existing_id,
            )
            continue

        # FK remap: rewrite remappable FK ids; unresolved => the row is not
        # created. A group/profile is a first-class entity the operator selected,
        # so this is a LOSS even when the FK's own category was deselected (bead
        # …-4mkoe). It is also the shape a DEGRADED BACKUP produces: bead
        # …-zt3kf's ``{"_warning": …}`` stub can leave ``user_agents`` empty
        # beside a full ``stream_profiles`` slice, and every profile pointing at
        # an agent then vanishes from the replica in silence.
        payload, unresolved_type = _build_create_payload(archive_row, config, remap)
        if payload is None:
            reason = report.record_dependency_unresolved(
                recorded_under=config.entity_type,
                dependency=unresolved_type,
                label=label,
                remap=remap,
                is_dry_run=is_dry_run,
                source_export_id=source_id,
            )
            logger.info(
                "[%s] %s '%s' skipped (%s) — its %s dependency is not on the "
                "destination.",
                config.log_prefix,
                noun,
                label,
                reason.value,
                unresolved_type.value,
            )
            continue

        if is_dry_run:
            cat.would_create += 1
            # Provisional remap so the Channels importer's FK to this would-be-
            # created group/profile resolves on the dry-run exactly as it would on
            # apply (anti-drift: a channel referencing a creatable group must
            # would_create, not would_skip DEPENDENCY_UNRESOLVED). Source id used as
            # a stable provisional destination id — never sent upstream.
            if source_id is not None:
                remap.add(config.entity_type, int(source_id), int(source_id))
            continue

        try:
            if config.payload_style == "name":
                created = await getattr(client, config.creator)(payload.get("name"))
            else:
                created = await getattr(client, config.creator)(payload)
        except Exception as exc:
            if _is_channel_group_name_create_race(config, exc):
                try:
                    refreshed = await getattr(client, config.getter)()
                except Exception as relist_exc:
                    logger.warning(
                        "[%s] Could not re-list %ss after create conflict: %s",
                        config.log_prefix,
                        noun,
                        relist_exc,
                    )
                    refreshed = []

                # Dispatcharr 0.29.0 stores ChannelGroup.name in a plain unique
                # TextField and its serializer performs no canonicalization.
                # The conflict therefore belongs only to the exact submitted
                # value; case/whitespace variants may legally coexist.
                attempted_name = payload.get("name")
                raced_candidates = [
                    row
                    for row in refreshed or []
                    if isinstance(row, dict) and row.get("name") == attempted_name
                ]
                raced_row = raced_candidates[0] if len(raced_candidates) == 1 else None
                if raced_row is not None:
                    existing_by_name = _existing_by_name(refreshed)
                    existing_by_raw_name = _existing_by_raw_name(refreshed)
                    _skip(
                        cat,
                        config.name_match_skip_reason,
                        label,
                        source_id,
                        is_dry_run,
                    )
                    destination_id = raced_row.get("id")
                    if source_id is not None and destination_id is not None:
                        remap.add(
                            config.entity_type,
                            int(source_id),
                            int(destination_id),
                        )
                    logger.info(
                        "[%s] %s '%s' appeared after a create conflict "
                        "(dest id=%s); adopted, nothing created.",
                        config.log_prefix,
                        noun,
                        label,
                        destination_id,
                    )
                    continue

            reason = _failure_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason,
                    label=label,
                    message=_sanitize_failure(exc, noun),
                    source_export_id=source_id,
                )
            )
            logger.warning(
                "[%s] Failed to restore %s '%s': %s",
                config.log_prefix,
                noun,
                label,
                reason.value,
            )
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(config.entity_type, int(source_id), dest_id)
            ledger.record_created(config.entity_type, dest_id, label)
        logger.info("[%s] Restored %s '%s' (id=%s).", config.log_prefix, noun, label, dest_id)


# ---------------------------------------------------------------------------
# Per-category entries (thin wrappers over the generic engine)
# ---------------------------------------------------------------------------


async def import_channel_groups(
    *,
    archive_rows: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the CHANNEL_GROUP category. Identity match key: name
    (case-insensitive / trimmed). Created via ``create_channel_group(name)``."""
    await _import_category(
        config=_CATEGORY_CONFIGS["channel_groups"],
        archive_rows=archive_rows,
        client=client,
        selected=selected,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )


async def import_server_groups(
    *,
    archive_rows: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the SERVER_GROUP category (bead ``…-tyrg1``).

    Identity match key: name (case-insensitive / trimmed) — which is the WHOLE
    ROW on Dispatcharr 0.29.0, so an ``ALREADY_EXISTS_IDENTICAL`` skip here
    leaves nothing uncompared.

    Runs BEFORE M3U_ACCOUNT in every registry: the account's ``server_group`` FK
    resolves through the namespace this importer fills. Before it existed the FK
    could only be DROPPED (bead ``…-g8tyd``), and a replica's M3U accounts
    therefore did not share a connection limit until an operator recreated the
    grouping by hand — a replica that behaves differently from its source until
    a human intervenes, which is what ADR-013's faithful-copy principle forbids.
    """
    await _import_category(
        config=_CATEGORY_CONFIGS["server_groups"],
        archive_rows=archive_rows,
        client=client,
        selected=selected,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )


async def import_channel_profiles(
    *,
    archive_rows: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the CHANNEL_PROFILE category. Identity match key: name
    (case-insensitive / trimmed). Channel membership is reattached by the Channels
    importer (4vouz), never sent on a profile create."""
    await _import_category(
        config=_CATEGORY_CONFIGS["channel_profiles"],
        archive_rows=archive_rows,
        client=client,
        selected=selected,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )


async def import_stream_profiles(
    *,
    archive_rows: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the STREAM_PROFILE category. Identity match key: name
    (case-insensitive / trimmed)."""
    await _import_category(
        config=_CATEGORY_CONFIGS["stream_profiles"],
        archive_rows=archive_rows,
        client=client,
        selected=selected,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )


# Order the bulk entry restores the categories in. There is no FK BETWEEN the
# three, so intra-bundle order is not load-bearing — but the BUNDLE as a whole
# runs AFTER M3U/EPG and BEFORE Channels, and stream profiles additionally
# require USER AGENTS to have been restored already (their ``user_agent`` FK
# resolves through that namespace — bead …-lvfwd). Both cross-category orderings
# are the orchestrator's job (bead …-0i2vt.18), not this module's.
_BULK_ORDER = ("channel_groups", "channel_profiles", "stream_profiles")


async def import_groups_profiles(
    *,
    archive: dict,
    client: DispatcharrClient,
    selected: dict,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore all three groups/profiles categories — the bulk entry point.

    A single clean entry the orchestrator (bead ``…-0i2vt.18``) registers so it
    can restore the whole leaf-dependency bundle in one call and then run Channels
    against the populated :class:`IdRemapTable`. Each category restores its rows
    and writes its EntityType namespace into the remap.

    Args:
        archive: The export archive sections, keyed by category name
            (``"channel_groups"`` / ``"channel_profiles"`` / ``"stream_profiles"``).
            A missing key is treated as an empty list.
        client: The Dispatcharr API client.
        selected: Per-category opt-in flags, keyed by the same category names. A
            missing key defaults OFF (opt-in) — nothing is created for it.
        report: The shared :class:`RestoreReport`.
        ledger: The shared :class:`RollbackLedger`.
        remap: The shared :class:`IdRemapTable` the Channels importer consumes.
        is_dry_run: When ``True``, nothing is created across all three categories.
    """
    for key in _BULK_ORDER:
        config = _CATEGORY_CONFIGS[key]
        await _import_category(
            config=config,
            archive_rows=archive.get(key) or [],
            client=client,
            selected=bool(selected.get(key, False)),
            report=report,
            ledger=ledger,
            remap=remap,
            is_dry_run=is_dry_run,
        )
