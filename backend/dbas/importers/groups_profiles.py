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
row is skipped ``ALREADY_EXISTS_IDENTICAL`` and its source id is remapped to the
EXISTING destination id so a later FK reference resolves — NEVER the
delete-all-then-recreate strategy, which would destroy the very relationships the
remap exists to preserve (kxuj2 contract; ADR-008 grooming note).

----------------------------------------------------------------------------
FK REMAP (generic; N/A for the three real categories)
----------------------------------------------------------------------------

The generic per-category engine supports rewriting outbound FK references through
the IdRemapTable (unresolvable -> ``DEPENDENCY_UNRESOLVED``, never sent upstream
with a stale archive id). The three REAL Dispatcharr categories are leaf
dependencies with NO outbound remappable FK — a group/profile does not reference
another remapped entity; channels reference them. So
:attr:`CategoryConfig.remappable_fk_fields` is empty for all three. The mechanism
is kept and tested (via a synthetic config) so a future FK-bearing category can
be added by config alone.

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
    rewrites it; it is EMPTY for the three real leaf categories (they carry no
    outbound FK). ``payload_style`` selects the client create calling convention:
    ``"name"`` calls ``create(name_string)`` (channel groups), ``"dict"`` calls
    ``create(payload_dict)`` (channel/stream profiles).
    """

    entity_type: EntityType
    getter: str
    creator: str
    log_prefix: str
    payload_style: str = "dict"  # "dict" | "name"
    remappable_fk_fields: dict = field(default_factory=dict)


# Canonical config table for the three real leaf categories. Keyed by the archive
# section name used in the export (and by the bulk entry's ``selected`` map).
_CATEGORY_CONFIGS: dict[str, CategoryConfig] = {
    "channel_groups": CategoryConfig(
        entity_type=EntityType.CHANNEL_GROUP,
        getter="get_channel_groups",
        creator="create_channel_group",
        log_prefix="DBAS-CHGROUP",
        payload_style="name",
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


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create failure into a restore-contract FailureReason.

    A name/uniqueness conflict maps to ``CONFLICT``; everything else is an
    upstream API error. We inspect the exception's short text only.
    """
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception, noun: str) -> str:
    """Produce a sanitized, operator-facing failure message (no raw traces)."""
    return (str(exc) or "").strip() or f"Upstream rejected the {noun} creation request."


def _build_create_payload(
    archive_row: dict, config: CategoryConfig, remap: IdRemapTable
) -> dict | None:
    """Build a create payload dict, rewriting any remappable FK ids to dest ids.

    Returns the payload on success, or ``None`` if a remappable FK reference could
    not be resolved through ``remap`` (the row must then be skipped
    ``DEPENDENCY_UNRESOLVED`` rather than created with a stale archive id). Drops
    the archive source id and the embedded/read-only non-create keys.
    """
    dropped = _SOURCE_ID_KEYS | _NON_CREATE_KEYS
    payload = {k: v for k, v in archive_row.items() if k not in dropped}
    for field_name, entity_type in config.remappable_fk_fields.items():
        source_id = archive_row.get(field_name)
        if source_id is None:
            continue
        dest_id = remap.resolve(entity_type, int(source_id))
        if dest_id is None:
            return None
        payload[field_name] = dest_id
    return payload


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
      is skipped ``ALREADY_EXISTS_IDENTICAL`` and the source id is remapped to the
      EXISTING destination id (never delete-all-then-recreate).
    * FK remap — any ``remappable_fk_fields`` are rewritten through the remap;
      unresolvable -> ``DEPENDENCY_UNRESOLVED`` (never a stale id upstream).
    * Dry-run — reports ``would_create`` / ``would_skip``; no creates, no ledger.
    * Failure taxonomy — a uniqueness race is ``CONFLICT``; other errors are
      ``UPSTREAM_API_ERROR``.

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

    # Pre-fetch existing rows to detect name collisions (safe field only).
    try:
        existing = await getattr(client, config.getter)()
    except Exception as exc:
        logger.warning(
            "[%s] Could not list existing %ss: %s", config.log_prefix, noun, exc
        )
        existing = []
    existing_by_name = _existing_by_name(existing)

    for archive_row in archive_rows:
        label = _row_label(archive_row)
        source_id = archive_row.get("id")

        # Collision: a row with the same name already on the destination.
        name_key = _norm_name(archive_row.get("name"))
        existing_row = existing_by_name.get(name_key) if name_key else None
        if existing_row is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            existing_id = existing_row.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(config.entity_type, int(source_id), int(existing_id))
            logger.info(
                "[%s] %s '%s' already exists (dest id=%s); skipped.",
                config.log_prefix,
                noun,
                label,
                existing_id,
            )
            continue

        # FK remap: rewrite remappable FK ids; unresolved => DEPENDENCY_UNRESOLVED.
        payload = _build_create_payload(archive_row, config, remap)
        if payload is None:
            logger.info(
                "[%s] %s '%s' has an unresolved FK reference; skipped "
                "DEPENDENCY_UNRESOLVED.",
                config.log_prefix,
                noun,
                label,
            )
            _skip(cat, SkipReason.DEPENDENCY_UNRESOLVED, label, source_id, is_dry_run)
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


# Order the bulk entry restores the categories within this bead's scope. NOTE:
# all three are leaf dependencies (no FK between them), so intra-bundle order is
# not load-bearing — but the BUNDLE as a whole runs AFTER M3U/EPG and BEFORE
# Channels. That cross-bead ordering is the orchestrator's job (bead …-0i2vt.18),
# not this module's.
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
