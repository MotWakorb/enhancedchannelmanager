"""Custom-stream fallback synthesis for orphaned archived streams.

Bead ``enhancedchannelmanager-ahygg`` (split 4/4 of the ``0i2vt.14`` channels
importer epic).

WHAT THIS IS. The 4-tier stream matcher (bead ``enhancedchannelmanager-al6e3``,
:func:`dbas.stream_matcher.match_stream`) tries to re-attach an archived
channel's embedded streams to streams that already exist on the *destination*
Dispatcharr instance. When every tier misses (``MatchTier.MISS``), the archived
stream is **orphaned** — the destination has no equivalent stream to point at.
Rather than silently drop the orphan (losing the operator's stream) or attach it
to a wrong stream, this module synthesizes a home for it:

1. **Ensure ONE synthesized "custom-stream" M3U account exists** on the
   destination. Find-or-create by a well-known name
   (:data:`CUSTOM_STREAM_ACCOUNT_NAME`): if an account with that name already
   exists (a prior restore run, or an earlier batch in this same run), reuse its
   id; otherwise create it once. Idempotent — a second batch within the same run
   reuses the just-created account (a one-call in-memory cache on the
   :class:`_FallbackState` the caller threads through), and a re-run reuses the
   account it finds on the destination.

2. **Create each orphan under that synthesized account** via
   ``client.create_stream`` (bead ``enhancedchannelmanager-nav0c``) and
   **attribute** it — the created stream's ``m3u_account`` field is set to the
   synthesized account's id so the orphan visibly belongs to the custom-stream
   provider. Each created stream id is recorded in the
   :class:`~dbas.restore_contracts.IdRemapTable` under
   :data:`~dbas.restore_contracts.EntityType.STREAM` (source-export id ->
   destination id) and in the
   :class:`~dbas.restore_contracts.RollbackLedger` so a later restore failure
   compensates by deleting them in reverse order (the ``kxuj2`` contract).

3. **WARN on the fallback path EVERY time it fires** (``[DBAS_RESTORE]`` prefix,
   lazy ``%`` formatting) including the orphan count. This is a
   correctness/observability requirement, not optional: an operator MUST see
   that some streams could not be matched and were synthesized, so they can
   decide whether the destination's provider line-up is what they expected.

SCOPE — TIGHT. This module does NOT run the matcher (that is ``al6e3``) and does
NOT build the channel rows (that is ``4vouz``). It consumes the MISS results —
the list of orphaned archived stream records — and is invoked by the ``0i2vt.14``
integration umbrella, which threads the shared ``IdRemapTable`` / ``RollbackLedger``
/ ``RestoreReport`` through. The integration umbrella owns persisting the ledger
to disk after each create (the durable-write strategy lives in one place per the
``kxuj2`` contract); this module only mutates the in-memory models.

Conventions (``docs/style_guide.md`` / ``backend/CLAUDE.md``): ``snake_case``;
Google-style docstrings; lazy ``%`` logging with a ``[DBAS_RESTORE]`` prefix;
no bare ``re.*`` (this module builds no regex); imports the restore contracts
READ-ONLY.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from credential_sentinel import REDACTION_SENTINEL
from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# The well-known name of the single synthesized M3U account that owns every
# orphaned stream. Find-or-create keys on this exact name (case-sensitive), so
# it must stay stable across releases — changing it would orphan a prior run's
# synthesized account and create a duplicate. Operator-facing (it shows up as a
# provider in Dispatcharr), so it is descriptive, not an internal token.
CUSTOM_STREAM_ACCOUNT_NAME = "ECM Custom Streams (DBAS restore)"

# The create payload for the synthesized account. ``name`` is the find-or-create
# key. No ``server_url`` / credentials — this account never refreshes from an
# upstream M3U; it is a static container for operator streams that had no
# destination match. (Dispatcharr accepts a credential-less account; the streams
# under it are created explicitly, not ingested.)
_ACCOUNT_CREATE_PAYLOAD = {"name": CUSTOM_STREAM_ACCOUNT_NAME}

# Keys never forwarded onto the synthesized stream create: archive-source
# identifiers the destination assigns itself, and ``m3u_account`` which we
# OVERRIDE to the synthesized account id (the whole point of the fallback is to
# re-home the orphan, not preserve its dead source provider).
_DROPPED_STREAM_KEYS = frozenset({"id", "pk", "m3u_account"})

# Derived / read-only echoes a LIVE stream GET carries that the archive shape
# never had (live two-instance validation, bead 7ipq2.2 — field survey of a
# real Dispatcharr 0.28.2 channel-streams response is in the bead). Viewer
# counts, staleness, stats and the server-computed hash are destination-owned;
# ``is_custom`` is set by the destination for streams created via this path.
_DERIVED_STREAM_KEYS = frozenset(
    {
        "current_viewers",
        "updated_at",
        "last_seen",
        "is_stale",
        "stream_hash",
        "stream_stats",
        "stream_stats_updated_at",
        "is_custom",
    }
)

# FK fields on a stream record that carry SOURCE-instance pks. Resolved through
# the shared IdRemapTable (the groups/profiles importers register every source
# id — created AND existing-identical); an unresolvable reference is DROPPED
# (soft degradation — the stream still lands under the synthesized account),
# never forwarded stale. Forwarding a stale ``channel_group`` pk made EVERY
# orphan create fail 400 on a real Dispatcharr-B (bead 7ipq2.2).
_REMAPPED_STREAM_FK_FIELDS: dict[str, EntityType] = {
    "channel_group": EntityType.CHANNEL_GROUP,
    "stream_profile_id": EntityType.STREAM_PROFILE,
}


@dataclass
class _FallbackState:
    """Per-restore-run cache of the synthesized account id.

    Threaded through successive ``synthesize_custom_streams`` calls in ONE
    restore run so the account is found-or-created at most once even across
    multiple batches, without re-hitting ``get_m3u_accounts`` every batch. The
    caller (the ``0i2vt.14`` umbrella) constructs one and reuses it; when omitted
    each call resolves the account afresh (still idempotent against the
    destination, just one extra ``get_m3u_accounts`` per batch).
    """

    account_id: int | None = None


@dataclass
class CustomStreamFallbackResult:
    """What the fallback synthesized in one batch.

    Returned so the ``0i2vt.14`` umbrella can fold the outcome into the overall
    restore — e.g. surface the synthesized-account id and the created stream ids.

    Attributes:
        account_id: The synthesized account's destination id, or ``None`` when
            there were no orphans (nothing synthesized).
        created_stream_ids: Destination ids of every orphan stream created in
            this batch, in creation order. Empty when there were no orphans (or
            every create failed).
        orphan_count: How many orphans were fed in (drives the WARN count).
    """

    account_id: int | None = None
    created_stream_ids: list[int] = field(default_factory=list)
    orphan_count: int = 0


async def synthesize_custom_streams(
    *,
    orphans: Sequence[Mapping],
    client: DispatcharrClient,
    remap: IdRemapTable,
    ledger: RollbackLedger,
    report: RestoreReport,
    state: _FallbackState | None = None,
) -> CustomStreamFallbackResult:
    """Synthesize a home for orphaned archived streams (the matcher-MISS path).

    For each orphan (an archived stream the 4-tier matcher returned
    ``MatchTier.MISS`` for): ensure the single synthesized custom-stream M3U
    account exists (find-or-create, idempotent), create the orphan under it
    attributed to that account, and record the created id in ``remap`` (under
    :data:`~dbas.restore_contracts.EntityType.STREAM`) and ``ledger``.

    WARN-logs on entry whenever there is at least one orphan, including the
    orphan count, so an operator sees the heuristic fired. A zero-orphan call is
    a complete no-op: no account is synthesized, nothing is created, and no WARN
    is logged (the common happy case where every stream matched).

    Args:
        orphans: The archived stream records the matcher missed. Each is a
            Mapping carrying at least ``id`` (its source-export id) and the
            fields ``create_stream`` needs (``name``, ``url``). Never mutated.
        client: The Dispatcharr API client. Uses ``get_m3u_accounts`` /
            ``create_m3u_account`` (find-or-create the account) and
            ``create_stream`` (create each orphan).
        remap: The shared :class:`IdRemapTable`; each created orphan's
            ``source_export_id -> destination_id`` is recorded under
            ``EntityType.STREAM``.
        ledger: The shared :class:`RollbackLedger`; each created orphan is
            recorded for compensating deletes. This function only mutates the
            in-memory model — the caller persists it per the kxuj2 contract.
        report: The shared :class:`RestoreReport`; created/failed orphans land in
            the ``EntityType.STREAM`` category.
        state: Optional per-run cache of the synthesized account id, so the
            account is resolved at most once across batches. Omit for a
            single-batch call.

    Returns:
        A :class:`CustomStreamFallbackResult` carrying the synthesized account id
        (``None`` if no orphans) and the created stream ids.
    """
    result = CustomStreamFallbackResult(orphan_count=len(orphans))

    # Zero orphans => complete no-op. No account, no creates, NO WARN. This is
    # the common happy case (every archived stream matched a destination stream)
    # and must stay silent so the WARN means something when it does fire.
    if not orphans:
        return result

    # Observability requirement: WARN EVERY time the fallback fires, with the
    # count, so the operator sees streams could not be matched and were
    # synthesized. Lazy %% formatting; [DBAS_RESTORE] prefix.
    logger.warning(
        "[DBAS_RESTORE] Stream matcher MISS — synthesizing custom-stream "
        "account for %d orphaned stream(s) that matched no destination stream.",
        len(orphans),
    )

    account_id = await _ensure_custom_stream_account(client, state)
    result.account_id = account_id

    cat = report.category(EntityType.STREAM)

    for orphan in orphans:
        source_export_id = orphan.get("id")
        label = _orphan_label(orphan)
        payload = _build_stream_payload(orphan, account_id, remap)

        try:
            created = await client.create_stream(payload)
        except Exception as exc:
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.UPSTREAM_API_ERROR,
                    label=label,
                    message=_sanitize_failure(exc),
                    source_export_id=_as_int_or_none(source_export_id),
                )
            )
            logger.warning(
                "[DBAS_RESTORE] Failed to synthesize orphan stream '%s' under "
                "custom-stream account %s: %s",
                label,
                account_id,
                FailureReason.UPSTREAM_API_ERROR.value,
            )
            continue

        dest_id = created.get("id") if isinstance(created, Mapping) else None
        dest_id = _as_int_or_none(dest_id)
        if dest_id is None:
            # Upstream accepted the create but returned no usable id — we cannot
            # ledger or remap it, so treat it as a failure rather than silently
            # losing track of a created entity.
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.UPSTREAM_API_ERROR,
                    label=label,
                    message="create_stream returned no usable stream id.",
                    source_export_id=_as_int_or_none(source_export_id),
                )
            )
            logger.warning(
                "[DBAS_RESTORE] Synthesized orphan stream '%s' but upstream "
                "returned no id; not ledgered.",
                label,
            )
            continue

        cat.created += 1
        # Bead …-msqf7: an Xtream Codes stream URL authenticates by path segment,
        # so the redactor cuts the credential OUT of the address rather than
        # dropping the address. What lands is a stream that names where it
        # pointed and cannot play until the destination has its own provider
        # account. Reported here rather than at redaction time because this is
        # the point where it is known to have actually reached the destination.
        if REDACTION_SENTINEL in str(payload.get("url") or ""):
            report.record_stream_url_redacted(label=label, stream_id=dest_id)
        result.created_stream_ids.append(dest_id)
        ledger.record_created(EntityType.STREAM, dest_id, label)
        src_int = _as_int_or_none(source_export_id)
        if src_int is not None:
            remap.add(EntityType.STREAM, src_int, dest_id)
        logger.info(
            "[DBAS_RESTORE] Synthesized orphan stream '%s' as id=%s under "
            "custom-stream account %s.",
            label,
            dest_id,
            account_id,
        )

    return result


async def _ensure_custom_stream_account(
    client: DispatcharrClient,
    state: _FallbackState | None,
) -> int:
    """Find-or-create the single synthesized custom-stream M3U account.

    Idempotent. Resolution order:

    1. If a ``state`` cache already knows the account id (an earlier batch in
       this run created/found it), reuse it without any API call.
    2. Otherwise list the destination's M3U accounts and reuse the first one
       whose name equals :data:`CUSTOM_STREAM_ACCOUNT_NAME` (a prior restore
       run, or this run before the cache was populated).
    3. Otherwise create it once and cache the new id.

    Args:
        client: The Dispatcharr API client.
        state: Optional per-run cache; populated with the resolved id.

    Returns:
        The synthesized account's destination id.
    """
    if state is not None and state.account_id is not None:
        return state.account_id

    accounts = await client.get_m3u_accounts()
    existing_id = _find_account_id_by_name(accounts, CUSTOM_STREAM_ACCOUNT_NAME)
    if existing_id is not None:
        logger.info(
            "[DBAS_RESTORE] Reusing existing custom-stream account id=%s ('%s').",
            existing_id,
            CUSTOM_STREAM_ACCOUNT_NAME,
        )
        if state is not None:
            state.account_id = existing_id
        return existing_id

    created = await client.create_m3u_account(dict(_ACCOUNT_CREATE_PAYLOAD))
    account_id = created.get("id") if isinstance(created, Mapping) else None
    account_id = _as_int_or_none(account_id)
    if account_id is None:
        # The account create is the load-bearing precondition for the whole
        # fallback; if it returns no id there is nothing to attribute orphans to.
        raise ValueError(
            "create_m3u_account returned no usable id for the synthesized "
            "custom-stream account."
        )
    logger.info(
        "[DBAS_RESTORE] Created synthesized custom-stream account id=%s ('%s').",
        account_id,
        CUSTOM_STREAM_ACCOUNT_NAME,
    )
    if state is not None:
        state.account_id = account_id
    return account_id


def _find_account_id_by_name(accounts, name: str) -> int | None:
    """Return the id of the first account whose name equals ``name``, or None.

    Case-sensitive exact match on the well-known synthesized-account name. A
    non-list/garbled accounts response yields ``None`` (defensive — never
    raises) so a transient upstream shape never blocks orphan synthesis from
    falling through to a create.
    """
    if not isinstance(accounts, Sequence):
        return None
    for account in accounts:
        if isinstance(account, Mapping) and account.get("name") == name:
            return _as_int_or_none(account.get("id"))
    return None


def _build_stream_payload(
    orphan: Mapping, account_id: int, remap: IdRemapTable | None = None
) -> dict:
    """Build the ``create_stream`` payload for an orphan, attributed to the account.

    Copies the orphan's fields, drops archive-source identifiers
    (:data:`_DROPPED_STREAM_KEYS`) and live-GET derived echoes
    (:data:`_DERIVED_STREAM_KEYS`), rewrites the source-instance FK pks
    (:data:`_REMAPPED_STREAM_FK_FIELDS`) through ``remap`` — dropping any that
    do not resolve, never forwarding a stale source pk (bead 7ipq2.2) — and
    forces ``m3u_account`` to the synthesized account id — the attribution that
    re-homes the orphan onto the custom-stream provider. Never mutates the
    input orphan.

    Args:
        orphan: The archived stream record (a Mapping).
        account_id: The synthesized account's destination id to attribute to.
        remap: The shared IdRemapTable for FK resolution. ``None`` (legacy
            callers) drops every remappable FK field rather than forwarding it.

    Returns:
        A fresh dict payload for ``client.create_stream``.
    """
    payload = {
        key: value
        for key, value in orphan.items()
        if key not in _DROPPED_STREAM_KEYS
        and key not in _DERIVED_STREAM_KEYS
        and key not in _REMAPPED_STREAM_FK_FIELDS
    }
    for field, entity_type in _REMAPPED_STREAM_FK_FIELDS.items():
        source_id = orphan.get(field)
        if source_id is None:
            continue
        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            continue
        dest_id = remap.resolve(entity_type, source_id) if remap else None
        if dest_id is not None:
            payload[field] = dest_id
    payload["m3u_account"] = account_id
    return payload


def _orphan_label(orphan: Mapping) -> str:
    """Operator-facing label for an orphan — its name, or a sentinel.

    Never a secret (a stream name is operator-facing); used in the report and
    the ledger entry. Falls back to ``<unknown>`` for a missing/blank name.
    """
    name = orphan.get("name")
    return str(name) if isinstance(name, str) and name else "<unknown>"


def _sanitize_failure(exc: Exception) -> str:
    """A sanitized, operator-facing failure message — no raw traces, no secrets.

    ``create_stream`` raises ``"Stream creation failed: <status> - <body>"``; the
    body is Dispatcharr's own error JSON (no ECM credentials), so ``str(exc)`` is
    safe to surface. Truncated to keep the report compact.
    """
    text = str(exc)
    return text[:300]


def _as_int_or_none(value) -> int | None:
    """Coerce ``value`` to ``int`` when it cleanly is one, else ``None``.

    ``bool`` is rejected explicitly (it is an ``int`` subclass but never a valid
    stream/account id). Used so a malformed id from an archive or upstream never
    pollutes the remap/ledger with a non-int.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
