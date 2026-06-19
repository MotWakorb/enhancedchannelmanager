"""The EPG sources restore importer — Phase-2, after M3U, before Channels.

Bead ``enhancedchannelmanager-0i2vt.11``. EPG sources sit second in the hard
Phase-2 ordering (``M3U → EPG → Channels → Logos``; ADR-012 D-table): they are
restored AFTER M3U accounts (so an EPG source that references an M3U account can
remap that FK) and BEFORE Channels (so a channel's ``epg_data`` linkage — handled
by the channels/logos importers, NOT here — has its source rows to match
against). This module restores the EPG_SOURCE entity category from a Dispatcharr
export archive: the SOURCE rows only. EPG-to-channel linkage (``set_logo`` /
``epg_data`` on channels) is explicitly out of scope here.

It mirrors the established Phase-2 importer pattern (``importers/m3u_accounts.py``
/ ``importers/channels.py`` / ``importers/users.py``): opt-in, consumes the
shared restore contracts (:class:`~dbas.restore_contracts.IdRemapTable`,
:class:`~dbas.restore_contracts.RollbackLedger`,
:class:`~dbas.restore_contracts.RestoreReport`, the Skip/Failure taxonomy),
per-entity results, and dry-run support.

----------------------------------------------------------------------------
CREDENTIAL HYGIENE (the bead .8 clear-text-logging lesson — read FIRST)
----------------------------------------------------------------------------

An EPG source can carry CREDENTIALS. An XMLTV source's ``url`` often embeds an
authenticated token; a Schedules-Direct source carries ``username`` /
``password`` (the password write-only, per ``docs/dispatcharr_api.md``). These
NEVER surface in a log line, a :class:`RestoreReport` label/note, a
:class:`RollbackLedger` entry, or a sanitized failure message. The ONLY fields we
log or report are SAFE: the source ``name``, the source ``source_type``, the
destination ``id``, counts, and status codes. We never log/echo a url, username,
password, or an upstream SDK exception body verbatim (an error body can echo the
url). The failure-message sanitizer below scrubs known credential markers
defensively and falls back to a generic message.

----------------------------------------------------------------------------
IDENTITY / MATCH KEY
----------------------------------------------------------------------------

An archived EPG source is matched against the destination instance's existing
sources by a STABLE identity: ``(source_type, normalized name)`` — the
source_type discriminator plus the case-insensitive, whitespace-trimmed name.
Rationale: the name is the operator-facing identity Dispatcharr displays, and
including ``source_type`` prevents an ``xmltv`` "Sports" colliding with a
``schedules_direct`` "Sports". We deliberately do NOT key on ``url``: an XMLTV
url can embed a credential token, so matching on it would mean comparing (and
risking logging) credential-bearing strings. A match → skip
``ALREADY_EXISTS_IDENTICAL`` (and the source id is still remapped to the existing
destination id so a later FK reference resolves). No match → create.

----------------------------------------------------------------------------
FK REMAP (m3u_account)
----------------------------------------------------------------------------

An EPG source may reference an M3U account (``m3u_account``) — e.g. a provider
that serves both the playlist and its EPG. That FK points at a SOURCE id and is
rewritten to the DESTINATION id through the :class:`IdRemapTable`
``M3U_ACCOUNT`` namespace (populated by the M3U importer ``.10``, which runs
first). An m3u_account reference that cannot be resolved is treated per the
contract as ``DEPENDENCY_UNRESOLVED`` — the source is skipped and never sent
upstream with a dangling source id. A null/absent m3u_account is NOT a dependency
(a free-standing XMLTV/SD source) and is created normally.

Integration with the restore contracts (bead ``kxuj2``): results land in the
shared :class:`RestoreReport` (``EntityType.EPG_SOURCE`` category), created
sources register source→dest in the :class:`IdRemapTable`, and every created
source is recorded in the :class:`RollbackLedger` for compensating deletes. This
importer imports the contracts module READ-ONLY.
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

# Archive-source identifiers the destination assigns itself, never forwarded.
_SOURCE_ID_KEYS = frozenset({"id", "pk"})

# Read-only / derived keys a GET echoes back that are NOT part of a create
# payload. Timestamps, status, and counts are server-maintained.
_NON_CREATE_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "last_refresh",
        "status",
        "epg_count",
        "channel_count",
        "sd_changes_remaining",
        "sd_changes_reset_at",
        "locked",
    }
)

# All keys dropped before issuing the create. (The FK ``m3u_account`` is NOT
# dropped here — it is remapped in place; see ``_build_create_payload``.)
_DROPPED_CREATE_KEYS = _SOURCE_ID_KEYS | _NON_CREATE_KEYS

# The FK reference an EPG source may carry into an M3U account.
_M3U_ACCOUNT_FK = "m3u_account"

# Credential markers scrubbed from any operator-facing failure message. An
# upstream error body can echo a url / username / password; we never let it
# through. ``http`` is included because any url leak starts with the scheme.
_CREDENTIAL_MESSAGE_KEYS = frozenset({"url", "username", "password", "http"})


def _source_label(archive_source: dict) -> str:
    """Operator-facing identifier for an EPG source — its name, never a secret."""
    name = archive_source.get("name")
    return str(name) if name else "<unknown>"


def _norm_name(value) -> str | None:
    """Case-insensitive, trimmed key for a name; None when absent/blank."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed or None


def _identity_key(source: dict) -> tuple[str | None, str | None]:
    """The stable identity for an EPG source: (source_type, normalized name).

    ``source_type`` is compared verbatim (a small enum: xmltv /
    schedules_direct / dummy); the name is normalized case-insensitively and
    trimmed. Neither field is a credential, so this key is safe to build, compare,
    and (for the name) log.
    """
    return (source.get("source_type"), _norm_name(source.get("name")))


def _existing_by_identity(existing_sources: list[dict]) -> dict[tuple, dict]:
    """Index existing destination sources by their (source_type, name) identity."""
    index: dict[tuple, dict] = {}
    for src in existing_sources or []:
        if isinstance(src, dict):
            key = _identity_key(src)
            if key[1] is not None and key not in index:
                index[key] = src
    return index


def _build_create_payload(archive_source: dict, m3u_dest_id: int | None) -> dict:
    """Build the create_epg_source payload from an archive source record.

    Drops the archive source id and read-only / derived fields. Keeps the
    credential fields (url / username / password) — they MUST be recreated for the
    source to function — but they are NEVER logged or reported. The
    ``m3u_account`` FK, if present, is rewritten in place to the remapped
    destination id (``m3u_dest_id``).
    """
    payload = {
        k: v for k, v in archive_source.items() if k not in _DROPPED_CREATE_KEYS
    }
    if _M3U_ACCOUNT_FK in payload:
        # Rewrite the FK to the destination id (None when the source had no
        # association — preserved as-is so a free-standing source stays
        # free-standing).
        payload[_M3U_ACCOUNT_FK] = m3u_dest_id
    return payload


def _resolve_m3u_fk(
    archive_source: dict, remap: IdRemapTable
) -> tuple[bool, int | None]:
    """Resolve the source's optional ``m3u_account`` FK through the remap table.

    Returns ``(resolved, dest_id)``:

    - No FK (None / absent): ``(True, None)`` — a free-standing source, not a
      dependency.
    - FK present and remapped: ``(True, dest_id)``.
    - FK present but unmapped: ``(False, None)`` — caller skips the source
      ``DEPENDENCY_UNRESOLVED``; we never send a dangling source id upstream.
    """
    source_m3u = archive_source.get(_M3U_ACCOUNT_FK)
    if source_m3u is None:
        return (True, None)
    dest_id = remap.resolve(EntityType.M3U_ACCOUNT, int(source_m3u))
    if dest_id is None:
        return (False, None)
    return (True, dest_id)


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create_epg_source failure into a restore-contract FailureReason.

    A name/uniqueness conflict maps to ``CONFLICT``; everything else is an
    upstream API error. (We inspect the exception's short text, never echo a
    credential.)
    """
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception) -> str:
    """Produce a sanitized, operator-facing failure message — NO credentials.

    An upstream error body can echo the source url / username / password; this
    scrubs any message that mentions a known credential marker and falls back to a
    generic message rather than risk leaking a credential into the report.
    """
    text = (str(exc) or "").strip()
    lowered = text.lower()
    if any(marker in lowered for marker in _CREDENTIAL_MESSAGE_KEYS):
        return "Upstream rejected the EPG source creation request."
    return text or "Upstream rejected the EPG source creation request."


async def import_epg_sources(
    *,
    archive_sources: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the EPG_SOURCE category: create sources; remap the m3u_account FK.

    Args:
        archive_sources: The EPG source records from the export archive.
        client: The Dispatcharr API client.
        selected: The per-category opt-in flag. When ``False`` the entire
            category is skipped (no creates) — every source recorded
            EXCLUDED_BY_OPERATOR.
        report: The shared :class:`RestoreReport`; results land in the
            ``EntityType.EPG_SOURCE`` category.
        ledger: The shared :class:`RollbackLedger`; each created source is
            recorded for compensating deletes.
        remap: The shared :class:`IdRemapTable`. READ for the ``M3U_ACCOUNT`` FK;
            WRITTEN with each created (or collision-resolved) source's
            source->dest id under ``EntityType.EPG_SOURCE`` so later importers
            resolve FK references.
        is_dry_run: When ``True``, nothing is created — the importer only reports
            ``would_create`` / ``would_skip``.
    """
    cat = report.category(EntityType.EPG_SOURCE)

    # OPT-IN. Off unless the operator selected the EPG sources category.
    if not selected:
        logger.info("[DBAS-EPG] Category not selected; skipping EPG sources.")
        for archive_source in archive_sources:
            _skip(
                cat,
                SkipReason.EXCLUDED_BY_OPERATOR,
                _source_label(archive_source),
                archive_source.get("id"),
                is_dry_run,
            )
        return

    logger.info(
        "[DBAS-EPG] Restoring EPG sources (dry_run=%s); %d archived source(s).",
        is_dry_run,
        len(archive_sources),
    )

    # Pre-fetch existing sources to detect identity collisions (safe fields only).
    try:
        existing = await client.get_epg_sources()
    except Exception as exc:
        logger.warning("[DBAS-EPG] Could not list existing EPG sources: %s", exc)
        existing = []
    existing_by_identity = _existing_by_identity(existing)

    for archive_source in archive_sources:
        label = _source_label(archive_source)
        source_id = archive_source.get("id")

        # FK resolution first — an unresolved m3u_account is a hard skip, no create.
        resolved, m3u_dest_id = _resolve_m3u_fk(archive_source, remap)
        if not resolved:
            _skip(cat, SkipReason.DEPENDENCY_UNRESOLVED, label, source_id, is_dry_run)
            logger.info(
                "[DBAS-EPG] Source '%s' (type=%s) skipped: m3u_account dependency "
                "unresolved.",
                label,
                archive_source.get("source_type"),
            )
            continue

        # Collision: a source with the same (source_type, name) already on dest.
        identity = _identity_key(archive_source)
        existing_src = (
            existing_by_identity.get(identity) if identity[1] is not None else None
        )
        if existing_src is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            existing_id = existing_src.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(EntityType.EPG_SOURCE, int(source_id), int(existing_id))
            logger.info(
                "[DBAS-EPG] Source '%s' (type=%s) already exists (dest id=%s); skipped.",
                label,
                archive_source.get("source_type"),
                existing_id,
            )
            continue

        if is_dry_run:
            cat.would_create += 1
            continue

        payload = _build_create_payload(archive_source, m3u_dest_id)
        try:
            created = await client.create_epg_source(payload)
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
                "[DBAS-EPG] Failed to restore EPG source '%s' (type=%s): %s",
                label,
                archive_source.get("source_type"),
                reason.value,
            )
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(EntityType.EPG_SOURCE, int(source_id), dest_id)
            ledger.record_created(EntityType.EPG_SOURCE, dest_id, label)
        logger.info(
            "[DBAS-EPG] Restored EPG source '%s' (type=%s, id=%s).",
            label,
            archive_source.get("source_type"),
            dest_id,
        )


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
