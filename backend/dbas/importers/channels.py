"""The channels restore importer — channel-row create + profile reattach.

Bead ``enhancedchannelmanager-4vouz`` (split 2/4 of the ``0i2vt.14`` channels
importer epic). This module restores the CHANNEL entity category from a
Dispatcharr export archive: it creates each archived channel row and reattaches
each channel to its archived channel-profile memberships.

SCOPE — TIGHT. This importer does channel-row create + profile membership
reattach ONLY. It deliberately leaves a clean SEAM where bead ``0i2vt.14``
integrates the stream matcher (``al6e3``) + ``create_stream`` (``nav0c``) +
custom-stream fallback (``ahygg``) to match and attach an archived channel's
streams. This importer NEVER matches a stream, NEVER attaches a stream, and
strips any embedded ``streams`` payload from the create. Stream attachment is
explicitly out of scope.

FK remapping (the load-bearing correctness control). A Dispatcharr export records
each entity's id *as it was on the source instance*; on restore the destination
assigns its own ids. FK reference fields on a channel therefore point at SOURCE
ids and MUST be rewritten to DESTINATION ids (via the shared
:class:`~dbas.restore_contracts.IdRemapTable`, populated by the groups/profiles
importer ``0i2vt.12``) before the create is sent — or the channel is skipped
``DEPENDENCY_UNRESOLVED`` rather than sent upstream with a stale archive id.

The channel FK fields and how each is handled:

* ``channel_group_id`` -> :data:`EntityType.CHANNEL_GROUP` — REMAPPED. Resolved
  through the IdRemapTable; unresolved => channel skipped DEPENDENCY_UNRESOLVED.
* ``stream_profile_id`` -> :data:`EntityType.STREAM_PROFILE` — REMAPPED. Same
  treatment.
* ``logo_id`` / ``epg_data_id`` -> NO EntityType in the restore contract. Logos
  and EPG data are owned by separate beads (logos: ``.15`` / ``.19``, surfaced
  via :attr:`RestoreReport.logo_misses`); there is no id namespace in the remap
  to resolve them. Sending the stale archive id would dangle a reference, so
  these fields are DROPPED from the create payload. (A later logo/epg bead
  reattaches them; this importer must not invent or forward a stale id.)

Channel-profile membership reattach. Channels belong to channel profiles. After a
channel is created, each archived membership is reattached via
``client.update_profile_channel(dest_profile_id, dest_channel_id, {"enabled": ...})``.
The destination profile id is resolved through the IdRemapTable
(:data:`EntityType.CHANNEL_PROFILE`, populated by ``0i2vt.12``). If the profile
is NOT in the remap (the channel-profiles importer did not run, or did not
restore that profile), the membership is handled as ``DEPENDENCY_UNRESOLVED`` and
recorded under the :data:`EntityType.CHANNEL_PROFILE` category — never crashing,
never guessing a profile id.

Collision taxonomy. A channel whose ``(name, channel_number)`` already exists on
the destination is skipped ``ALREADY_EXISTS_IDENTICAL`` (never overwritten); its
source id is still remapped to the existing destination id so a later profile
reattach can resolve it. A create that races into an upstream uniqueness conflict
is failed ``CONFLICT``.

Opt-in. The category does nothing unless the operator selected it (``selected``).

Integration with the restore contracts (bead ``kxuj2``): results land in the
shared :class:`~dbas.restore_contracts.RestoreReport`
(:data:`EntityType.CHANNEL` category, plus :data:`EntityType.CHANNEL_PROFILE` for
unresolved memberships), created channels register source->dest in the
:class:`~dbas.restore_contracts.IdRemapTable`, and every created channel is
recorded in the :class:`~dbas.restore_contracts.RollbackLedger` so a later
failure compensates by deleting it. This importer imports the contracts module
READ-ONLY.
"""

from __future__ import annotations

import logging

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

# Channel FK fields whose ids reference another entity that the restore remaps.
# Maps the archive field name -> the EntityType namespace to resolve it through.
# These are the ONLY FK fields the contract can remap (the others — logo_id,
# epg_data_id — have no EntityType and are dropped, see _NON_REMAPPABLE_FK_KEYS).
_REMAPPABLE_FK_FIELDS = {
    "channel_group_id": EntityType.CHANNEL_GROUP,
    "stream_profile_id": EntityType.STREAM_PROFILE,
}

# FK fields that reference an entity NOT in the restore-contract id namespace.
# Logos / EPG data are owned by separate beads (logos: .15 / .19). There is no
# remap to rewrite their ids, so we DROP them rather than forward a stale archive
# id that would dangle. A later logo/epg bead reattaches them.
_NON_REMAPPABLE_FK_KEYS = frozenset({"logo_id", "epg_data_id"})

# Archive-source identifiers the destination assigns itself, never forwarded.
_SOURCE_ID_KEYS = frozenset({"id", "pk"})

# Embedded/derived keys that are NOT part of a channel create payload. ``streams``
# is the stream-attachment SEAM owned by bead 0i2vt.14 — this importer strips it
# and never attaches a stream. ``profile_memberships`` is consumed separately by
# the profile-reattach step (post-create), not sent in the create body. Other
# read-only/derived fields a GET echoes back are dropped defensively.
_NON_CREATE_KEYS = frozenset(
    {
        "streams",
        "profile_memberships",
        "channelprofilemembership_set",
        "stream_count",
        "stats",
    }
)

# All keys dropped before issuing the create. The remappable FK fields are not in
# this set — they are rewritten in-place to destination ids (or the channel is
# skipped if unresolvable) rather than dropped.
_DROPPED_CREATE_KEYS = (
    _SOURCE_ID_KEYS | _NON_REMAPPABLE_FK_KEYS | _NON_CREATE_KEYS
)


def _channel_label(archive_channel: dict) -> str:
    """Operator-facing identifier for a channel — its name, never a secret."""
    name = archive_channel.get("name")
    return str(name) if name else "<unknown>"


def _build_create_payload(archive_channel: dict, remap: IdRemapTable) -> dict:
    """Build the create_channel payload, rewriting remappable FK ids to dest ids.

    Returns the payload on success, or ``None`` if a remappable FK reference could
    not be resolved through ``remap`` (the channel must then be skipped
    ``DEPENDENCY_UNRESOLVED`` rather than created with a stale archive id).

    Drops the archive's source id, the non-remappable FK fields (logo/epg, owned
    by other beads), and the embedded/derived non-create keys (notably the
    ``streams`` seam owned by bead 0i2vt.14).
    """
    payload = {
        k: v for k, v in archive_channel.items() if k not in _DROPPED_CREATE_KEYS
    }
    for field, entity_type in _REMAPPABLE_FK_FIELDS.items():
        source_id = archive_channel.get(field)
        if source_id is None:
            continue
        dest_id = remap.resolve(entity_type, int(source_id))
        if dest_id is None:
            return None
        payload[field] = dest_id
    return payload


def _existing_channel_key(channel: dict) -> tuple:
    """Identity key for an existing destination channel: (name, channel_number).

    A restored channel that matches an existing one on this key is a no-op
    (ALREADY_EXISTS_IDENTICAL); ``channel_number`` may be absent on either side.
    """
    return (channel.get("name"), channel.get("channel_number"))


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create_channel failure into a restore-contract FailureReason.

    A name/number uniqueness conflict (the error body echoing "already exists" /
    "unique") maps to ``CONFLICT``; everything else is an upstream API error.
    """
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception) -> str:
    """Produce a sanitized, operator-facing failure message (no raw traces)."""
    return (str(exc) or "").strip() or "Upstream rejected the channel creation request."


async def import_channels(
    *,
    archive_channels: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the CHANNEL category: create channel rows + reattach profiles.

    Args:
        archive_channels: The channel records from the export archive. Each is a
            dict; the archive's source id, non-remappable FK fields, and embedded
            stream payload are dropped/handled per the module docstring.
        client: The Dispatcharr API client.
        selected: The per-category opt-in flag. When ``False`` the entire category
            is skipped (no creates) — every channel recorded EXCLUDED_BY_OPERATOR.
        report: The shared :class:`RestoreReport`; channel results land in the
            ``EntityType.CHANNEL`` category, unresolved profile memberships in
            ``EntityType.CHANNEL_PROFILE``.
        ledger: The shared :class:`RollbackLedger`; each created channel is
            recorded for compensating deletes.
        remap: The shared :class:`IdRemapTable`. READ to resolve channel FK
            references (channel_group / stream_profile) and channel-profile ids;
            WRITTEN with each created channel's source->dest id (under
            ``EntityType.CHANNEL``) so the stream-attachment bead and the profile
            reattach can resolve channels.
        is_dry_run: When ``True``, nothing is created or reattached — the importer
            only reports ``would_create`` / ``would_skip`` so the operator sees
            the plan.
    """
    cat = report.category(EntityType.CHANNEL)

    # OPT-IN. Off unless the operator selected the channels category.
    if not selected:
        logger.info("[DBAS-CHANNELS] Category not selected; skipping channels.")
        for archive_channel in archive_channels:
            _skip(
                cat,
                SkipReason.EXCLUDED_BY_OPERATOR,
                _channel_label(archive_channel),
                archive_channel.get("id"),
                is_dry_run,
            )
        return

    logger.info(
        "[DBAS-CHANNELS] Restoring channels (dry_run=%s); %d archived channel(s).",
        is_dry_run,
        len(archive_channels),
    )

    # Pre-fetch existing channels to detect (name, channel_number) collisions.
    existing_by_key: dict[tuple, dict] = {}
    try:
        existing = await client.get_channels(page_size=1000)
        results = existing.get("results", []) if isinstance(existing, dict) else existing
        for ch in results or []:
            if isinstance(ch, dict):
                existing_by_key[_existing_channel_key(ch)] = ch
    except Exception as exc:
        logger.warning("[DBAS-CHANNELS] Could not list existing channels: %s", exc)

    # Channels created/resolved this run, paired with their archive record, so the
    # profile-reattach pass (post-create) can resolve each channel's dest id.
    # Each tuple: (archive_channel, destination_channel_id).
    reattach_queue: list[tuple[dict, int]] = []

    for archive_channel in archive_channels:
        label = _channel_label(archive_channel)
        source_id = archive_channel.get("id")

        # Collision: an identical (name, number) already on the destination.
        existing = existing_by_key.get(_existing_channel_key(archive_channel))
        if existing is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            # Still remap source -> existing dest id so a later profile reattach
            # (and the stream-attachment bead) can resolve this channel.
            existing_id = existing.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(EntityType.CHANNEL, int(source_id), int(existing_id))
                if not is_dry_run:
                    reattach_queue.append((archive_channel, int(existing_id)))
            continue

        # FK remap: rewrite remappable FK ids; unresolved => DEPENDENCY_UNRESOLVED.
        payload = _build_create_payload(archive_channel, remap)
        if payload is None:
            logger.info(
                "[DBAS-CHANNELS] Channel '%s' skipped — an FK dependency is "
                "unresolved (not yet restored).",
                label,
            )
            _skip(cat, SkipReason.DEPENDENCY_UNRESOLVED, label, source_id, is_dry_run)
            continue

        if is_dry_run:
            cat.would_create += 1
            continue

        try:
            created = await client.create_channel(payload)
        except Exception as exc:
            reason = _failure_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason,
                    label=label,
                    message=_sanitize_failure(exc),
                    source_export_id=source_id,
                )
            )
            logger.warning(
                "[DBAS-CHANNELS] Failed to restore channel '%s': %s", label, reason.value
            )
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(EntityType.CHANNEL, int(source_id), dest_id)
            ledger.record_created(EntityType.CHANNEL, dest_id, label)
            reattach_queue.append((archive_channel, dest_id))
        logger.info("[DBAS-CHANNELS] Restored channel '%s' (id=%s).", label, dest_id)

    # Profile reattach pass — runs AFTER all channels are created so every channel
    # has a destination id. Dry-run reattaches nothing.
    if not is_dry_run:
        await _reattach_profiles(reattach_queue, client=client, report=report, remap=remap)


async def _reattach_profiles(
    reattach_queue: list[tuple[dict, int]],
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
) -> None:
    """Reattach each restored channel to its archived channel-profile memberships.

    For each membership, resolve the destination profile id through the
    IdRemapTable (``EntityType.CHANNEL_PROFILE``). An unresolved profile is
    recorded ``DEPENDENCY_UNRESOLVED`` under the CHANNEL_PROFILE category and the
    membership is NOT applied (no guessed id). A reattach upstream error is
    recorded ``UPSTREAM_API_ERROR``.
    """
    prof_cat = report.category(EntityType.CHANNEL_PROFILE)
    for archive_channel, dest_channel_id in reattach_queue:
        label = _channel_label(archive_channel)
        for membership in _profile_memberships(archive_channel):
            source_profile_id = membership.get("profile_id")
            if source_profile_id is None:
                continue
            dest_profile_id = remap.resolve(
                EntityType.CHANNEL_PROFILE, int(source_profile_id)
            )
            if dest_profile_id is None:
                logger.info(
                    "[DBAS-CHANNELS] Channel '%s' profile membership skipped — "
                    "profile (source id %s) not in remap (dependency unresolved).",
                    label,
                    source_profile_id,
                )
                prof_cat.skipped += 1
                prof_cat.skip_details.append(
                    SkipDetail(
                        reason=SkipReason.DEPENDENCY_UNRESOLVED,
                        label=label,
                        source_export_id=int(source_profile_id),
                    )
                )
                continue
            enabled = bool(membership.get("enabled", True))
            try:
                await client.update_profile_channel(
                    dest_profile_id, dest_channel_id, {"enabled": enabled}
                )
            except Exception as exc:
                prof_cat.failed += 1
                prof_cat.failure_details.append(
                    FailureDetail(
                        reason=FailureReason.UPSTREAM_API_ERROR,
                        label=label,
                        message=_sanitize_failure(exc),
                        source_export_id=int(source_profile_id),
                    )
                )
                logger.warning(
                    "[DBAS-CHANNELS] Failed to reattach channel '%s' to profile "
                    "(dest id %s): %s",
                    label,
                    dest_profile_id,
                    exc,
                )


def _profile_memberships(archive_channel: dict) -> list[dict]:
    """Extract the channel's archived profile memberships as a list of dicts.

    Accepts the canonical ``profile_memberships`` list (``{profile_id, enabled}``
    records). Returns an empty list when the archive carries no memberships.
    """
    memberships = archive_channel.get("profile_memberships")
    if not isinstance(memberships, list):
        return []
    return [m for m in memberships if isinstance(m, dict)]


def _skip(
    cat,
    reason: SkipReason,
    label: str,
    source_export_id,
    is_dry_run: bool,
) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(
        SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
    )
