"""The M3U accounts restore importer — Phase-2 FIRST entity.

Bead ``enhancedchannelmanager-0i2vt.10``. M3U accounts are the FIRST entity the
DBAS restore creates: they sit at the root of the hard Phase-2 ordering
(``M3U → EPG → Channels → Logos``; ADR-012 D-table) and block EPG, channel
groups, user agents, and channels. This module restores the M3U_ACCOUNT entity
category from a Dispatcharr export archive.

It mirrors the established Phase-2 importer pattern (``importers/users.py`` /
``importers/channels.py``): opt-in, consumes the shared restore contracts
(:class:`~dbas.restore_contracts.IdRemapTable`,
:class:`~dbas.restore_contracts.RollbackLedger`,
:class:`~dbas.restore_contracts.RestoreReport`, the Skip/Failure taxonomy),
per-entity results, and dry-run support.

----------------------------------------------------------------------------
CREDENTIAL HYGIENE (the bead .8 clear-text-logging lesson — read FIRST)
----------------------------------------------------------------------------

An M3U account carries CREDENTIALS: ``server_url`` (often an authenticated
playlist URL with an embedded token), ``username``, ``password``. These NEVER
surface in a log line, a :class:`RestoreReport` label/note, a
:class:`RollbackLedger` entry, or the deferred return shape. The ONLY fields we
log or report are SAFE: the account ``name``, the destination ``id``, counts,
and status codes. We never log/echo a server_url, username, password, or an
upstream SDK exception body verbatim (an error body can echo the URL). The
failure-message sanitizer below scrubs known credential markers defensively.

----------------------------------------------------------------------------
4-WAY GROUP MATCHING (pure helper :func:`resolve_group`)
----------------------------------------------------------------------------

An archived M3U account's associated channel group(s) must be reconciled against
the DESTINATION instance's channel groups (their ids differ across instances).
:func:`resolve_group` resolves an archived group to a destination group id by
FOUR strategies, in strict priority order — the first strategy that hits wins:

* **(a) by ID** — the archived group's source id resolves through the
  :class:`IdRemapTable` ``CHANNEL_GROUP`` namespace (populated by the
  groups/profiles importer ``.12``). Strongest signal: an explicit
  source→dest mapping recorded when the group was restored this run.
* **(b) by name** — case-insensitive, whitespace-trimmed name equality. Covers
  the common case where the group already existed on the destination under the
  same name but a different id.
* **(c) by URL** — exact ``url`` equality. Covers a renamed group whose
  provider URL is stable (the URL is the group's stable upstream identity).
* **(d) by export-key** — exact ``export_key`` equality. Last resort: the
  export's own stable key, used when name and URL both drifted.

Tie-break (deterministic): within the winning strategy, if several destination
groups match, the one with the **lowest integer id** wins — mirrors the ADR-008
dedup matcher's lowest-id rule, so the result is order-independent. A
fall-through (no strategy hits) returns ``None``; the caller treats that as
unresolved and never guesses a destination id.

----------------------------------------------------------------------------
DEFERRED AUTO-SYNC (the CRITICAL ordering pattern)
----------------------------------------------------------------------------

Triggering an M3U account's upstream auto-sync/refresh fetches its streams from
the provider. If that fires DURING restore, it races the Logos importer on the
Dispatcharr side (a sync can churn channel/stream rows mid-logo-attach). So this
importer DOES NOT trigger auto-sync/refresh at import time. Instead it EXTRACTS
each created account's PER-GROUP settings — the enabled-group SELECTION first and
foremost, plus any auto-sync range — and RETURNS them, in the ADR-008 contract
shape::

    deferred_auto_sync_settings: list[{"m3u_account_id": int, "settings": dict}]

keyed by the DESTINATION (remapped) account id. The orchestrator (beads ``.14``
/ ``.18``) applies them AFTER Channels + Logos finish, via
:func:`apply_deferred_auto_sync` (the deferred-apply helper) — protecting the
logo import from an auto-sync race. The importer's result
(:class:`M3uImportResult`) carries this list so the orchestrator can consume it.

----------------------------------------------------------------------------
DEFERRED-APPLY HELPER (:func:`apply_deferred_auto_sync`)
----------------------------------------------------------------------------

Called by the orchestrator during the deferred phase (NOT at import). For each
deferred account it:

1. Applies the per-group settings (``client.update_m3u_group_settings``) —
   BEFORE the refresh, with each SOURCE group pk rewritten to its DESTINATION pk
   through the shared ``IdRemapTable``. The deferred phase is the first point in
   the run where that namespace is populated (M3U accounts import before channel
   groups), which is exactly why this apply is deferred rather than done inline.
2. Triggers the account refresh (``client.refresh_m3u_account``).
3. Runs the **is_active toggle workaround**: PATCHes ``is_active`` False→True.
   Newly-imported M3U accounts on Dispatcharr do not always trigger a stream
   fetch; toggling ``is_active`` reliably kicks it off.
4. Runs the **2-stage refresh poll**: polls the destination stream count and
   terminates when it stabilizes across ``stable_polls_required`` consecutive
   polls (the stream-count-stable heuristic), bounded by ``max_polls``.

The poll/refresh/sleep are injected as seams (``stream_count_fn``, ``sleep_fn``)
so the logic is exhaustively unit-tested with mocks. The LIVE end-to-end
behaviour of the poll against a real Dispatcharr instance (does the count
actually stabilize, does the toggle actually kick a fetch) needs a live instance
and is flagged as a deferred verification follow-up — there is no live
Dispatcharr available to confirm it tonight.

Integration with the restore contracts (bead ``kxuj2``): results land in the
shared :class:`RestoreReport` (``EntityType.M3U_ACCOUNT`` category), created
accounts register source→dest in the :class:`IdRemapTable`, and every created
account is recorded in the :class:`RollbackLedger` for compensating deletes.
This importer imports the contracts module READ-ONLY.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from credential_sentinel import strip_redaction_sentinels
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

# Embedded / read-only / derived keys that are NOT part of an M3U create payload.
# ``channel_groups`` is the embedded per-group settings list (reconciled and
# deferred separately, never sent on create). The timestamps and counts are
# read-only fields a GET echoes back.
_NON_CREATE_KEYS = frozenset(
    {
        "channel_groups",
        "created_at",
        "updated_at",
        "last_refresh",
        "stream_count",
        "status",
        "locked",
    }
)

# All keys dropped before issuing the create.
_DROPPED_CREATE_KEYS = _SOURCE_ID_KEYS | _NON_CREATE_KEYS

# Credential markers scrubbed from any operator-facing failure message. An
# upstream error body can echo a server_url; we never let it through.
_CREDENTIAL_MESSAGE_KEYS = frozenset({"server_url", "username", "password", "url"})


class M3uImportResult(BaseModel):
    """The M3U importer's return value.

    Carries the deferred auto-sync settings the orchestrator (.14/.18) applies
    AFTER Channels + Logos finish. The shape per the ADR-008 deferred-auto-sync
    contract: a list of ``{"m3u_account_id": <dest id>, "settings": {...}}``.

    The settings dict carries ONLY safe, non-credential fields needed to re-apply
    the upstream sync (group auto_channel_sync flags, refresh interval) — never a
    server_url, username, or password.
    """

    deferred_auto_sync_settings: list[dict] = Field(default_factory=list)


def _account_label(archive_account: dict) -> str:
    """Operator-facing identifier for an M3U account — its name, never a secret."""
    name = archive_account.get("name")
    return str(name) if name else "<unknown>"


def _norm_name(value) -> str | None:
    """Case-insensitive, trimmed key for a group name; None when absent/blank."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed or None


def resolve_group(
    archive_group: dict,
    dest_groups: list[dict],
    remap: IdRemapTable,
) -> int | None:
    """Resolve an archived channel group to a DESTINATION group id (4-way match).

    Strategies, in strict priority order — the first that hits wins:

    (a) by ID — archived source id resolves through the IdRemapTable
        ``CHANNEL_GROUP`` namespace.
    (b) by name — case-insensitive, trimmed name equality.
    (c) by URL — exact ``url`` equality.
    (d) by export-key — exact ``export_key`` equality.

    Within the winning strategy, ties break on the LOWEST destination id
    (deterministic, order-independent). No strategy hitting returns ``None``.

    Args:
        archive_group: The archived group record (``id`` / ``name`` / ``url`` /
            ``export_key``).
        dest_groups: The destination instance's channel groups.
        remap: The shared :class:`IdRemapTable` (read-only here).

    Returns:
        The destination group id, or ``None`` if no strategy resolved it.
    """
    # (a) by ID — explicit source->dest mapping recorded this run.
    source_id = archive_group.get("id")
    if source_id is not None:
        mapped = remap.resolve(EntityType.CHANNEL_GROUP, int(source_id))
        if mapped is not None:
            return mapped

    # (b) by name — case-insensitive, trimmed.
    name_key = _norm_name(archive_group.get("name"))
    if name_key is not None:
        matches = [
            g.get("id")
            for g in dest_groups
            if _norm_name(g.get("name")) == name_key and g.get("id") is not None
        ]
        if matches:
            return min(int(m) for m in matches)

    # (c) by URL — exact equality.
    url = archive_group.get("url")
    if url:
        matches = [
            g.get("id")
            for g in dest_groups
            if g.get("url") == url and g.get("id") is not None
        ]
        if matches:
            return min(int(m) for m in matches)

    # (d) by export-key — exact equality.
    export_key = archive_group.get("export_key")
    if export_key:
        matches = [
            g.get("id")
            for g in dest_groups
            if g.get("export_key") == export_key and g.get("id") is not None
        ]
        if matches:
            return min(int(m) for m in matches)

    return None


def _build_create_payload(archive_account: dict) -> tuple[dict, list[str]]:
    """Build the create_m3u_account payload from an archive account record.

    Drops the archive source id, the embedded ``channel_groups`` settings list
    (reconciled and deferred separately), and read-only/derived fields. Keeps the
    credential fields (server_url/username/password) — they MUST be recreated for
    the account to function — but they are NEVER logged or reported.

    A STANDARD (redact-by-default) artifact carries the ``***REDACTED***``
    placeholder in place of each credential. Writing that through produced an XC
    account that LOOKED configured and could not authenticate (bead ``…-6pilh``),
    so any sentinel-valued key is STRIPPED and the field is left unset — visibly
    incomplete, and absent to every presence check. Detection is by VALUE, so an
    encrypted + ``include_credentials`` artifact (which carries the real values)
    is unaffected.

    Returns:
        ``(payload, redacted_fields)`` — the create payload, and the credential
        field NAMES that were stripped (never their values) so the caller can
        report them as a post-restore action item.
    """
    payload = {
        k: v for k, v in archive_account.items() if k not in _DROPPED_CREATE_KEYS
    }
    return strip_redaction_sentinels(payload)


def _extract_group_settings(archive_account: dict) -> dict | None:
    """Extract the safe, non-credential per-group settings for deferred apply.

    Returns a settings dict carrying every archived group membership, or ``None``
    when the account has no ``channel_groups`` at all (nothing to apply later).
    NEVER carries server_url/username/password.

    WHY EVERY GROUP, NOT JUST THE AUTO-SYNC ONES (bead ``…-2o0cz``): this used to
    return ``None`` unless at least one group had ``auto_channel_sync`` set. The
    drill's source account had ONE of 375 groups merely ENABLED and no auto-sync
    anywhere, so nothing was deferred, nothing was applied, and the restored
    account came back at ``0 / 375`` groups in PENDING SETUP. A refresh in that
    state ingests nothing and Dispatcharr reports ``No streams returned from
    Xtream Codes provider`` — blaming the provider for "you enabled no groups".
    The enabled-group SELECTION is the load-bearing setting; ``auto_channel_sync``
    is an optional extra on top of it.

    Fields carried per group (all read straight off Dispatcharr 0.28.2's
    ``ChannelGroupM3UAccountSerializer``, and all accepted by the
    ``PATCH /api/m3u/accounts/<id>/group-settings/`` upsert):

    * ``channel_group`` — the SOURCE instance's group pk. Rewritten to the
      destination pk in :func:`apply_deferred_auto_sync`, which is the first
      point in the run where the CHANNEL_GROUP remap is populated (M3U accounts
      import BEFORE channel groups).
    * ``enabled`` — the selection the drill lost.
    * ``auto_channel_sync`` / ``auto_sync_channel_start`` /
      ``auto_sync_channel_end`` — the auto-created-channel range settings.
    * ``custom_properties`` — carries ``xc_id``, the provider category id an
      Xtream-Codes refresh filters streams by (0.28.2 ``apps/m3u/tasks.py``
      ``collect_xc_streams``). Dropping it would leave an enabled group that
      still ingests nothing on an XC account.
    """
    channel_groups = archive_account.get("channel_groups")
    if not isinstance(channel_groups, list) or not channel_groups:
        return None

    # Keep only the safe per-group settings fields — not the whole group record
    # (``is_stale`` / ``last_seen`` / ``stream_count`` are destination-owned
    # read-only echoes and are never sent back).
    safe_groups = []
    for cg in channel_groups:
        if not isinstance(cg, dict):
            continue
        source_group_id = cg.get("channel_group")
        if source_group_id is None:
            continue
        entry = {
            "channel_group": source_group_id,
            "auto_channel_sync": bool(cg.get("auto_channel_sync", False)),
            "enabled": bool(cg.get("enabled", True)),
        }
        for optional_key in (
            "auto_sync_channel_start",
            "auto_sync_channel_end",
            "custom_properties",
        ):
            value = cg.get(optional_key)
            if value is not None:
                entry[optional_key] = value
        safe_groups.append(entry)

    if not safe_groups:
        return None

    settings: dict = {"channel_groups": safe_groups}
    refresh_interval = archive_account.get("refresh_interval")
    if refresh_interval is not None:
        settings["refresh_interval"] = refresh_interval
    return settings


def _existing_by_name(existing_accounts: list[dict]) -> dict[str, dict]:
    """Index existing destination accounts by their normalized name."""
    index: dict[str, dict] = {}
    for acc in existing_accounts or []:
        if isinstance(acc, dict):
            key = _norm_name(acc.get("name"))
            if key is not None and key not in index:
                index[key] = acc
    return index


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create_m3u_account failure into a restore-contract FailureReason.

    A name/uniqueness conflict maps to ``CONFLICT``; everything else is an
    upstream API error. (We inspect the exception's class/short text, never echo
    a credential.)
    """
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception) -> str:
    """Produce a sanitized, operator-facing failure message — NO credentials.

    An upstream error body can echo the account's server_url; this scrubs any
    line that mentions a known credential marker and falls back to a generic
    message rather than risk leaking a URL/username/password into the report.
    """
    text = (str(exc) or "").strip()
    lowered = text.lower()
    if any(marker in lowered for marker in _CREDENTIAL_MESSAGE_KEYS) or "http" in lowered:
        return "Upstream rejected the M3U account creation request."
    return text or "Upstream rejected the M3U account creation request."


async def import_m3u_accounts(
    *,
    archive_accounts: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> M3uImportResult:
    """Restore the M3U_ACCOUNT category: create accounts; defer auto-sync.

    Args:
        archive_accounts: The M3U account records from the export archive.
        client: The Dispatcharr API client.
        selected: The per-category opt-in flag. When ``False`` the entire
            category is skipped (no creates) — every account recorded
            EXCLUDED_BY_OPERATOR — and no auto-sync is deferred.
        report: The shared :class:`RestoreReport`; results land in the
            ``EntityType.M3U_ACCOUNT`` category.
        ledger: The shared :class:`RollbackLedger`; each created account is
            recorded for compensating deletes.
        remap: The shared :class:`IdRemapTable`. WRITTEN with each created (or
            collision-resolved) account's source->dest id under
            ``EntityType.M3U_ACCOUNT`` so later importers resolve FK references.
        is_dry_run: When ``True``, nothing is created — the importer only reports
            ``would_create`` / ``would_skip`` and returns no deferred settings.

    Returns:
        An :class:`M3uImportResult` carrying ``deferred_auto_sync_settings`` for
        the orchestrator to apply AFTER Channels + Logos finish. NEVER triggers
        auto-sync/refresh at import time.
    """
    cat = report.category(EntityType.M3U_ACCOUNT)
    result = M3uImportResult()

    # OPT-IN. Off unless the operator selected the M3U accounts category.
    if not selected:
        logger.info("[DBAS-M3U] Category not selected; skipping M3U accounts.")
        for archive_account in archive_accounts:
            _skip(
                cat,
                SkipReason.EXCLUDED_BY_OPERATOR,
                _account_label(archive_account),
                archive_account.get("id"),
                is_dry_run,
            )
        return result

    logger.info(
        "[DBAS-M3U] Restoring M3U accounts (dry_run=%s); %d archived account(s).",
        is_dry_run,
        len(archive_accounts),
    )

    # Pre-fetch existing accounts to detect name collisions (safe field only).
    try:
        existing = await client.get_m3u_accounts()
    except Exception as exc:
        logger.warning("[DBAS-M3U] Could not list existing M3U accounts: %s", exc)
        existing = []
    existing_by_name = _existing_by_name(existing)

    for archive_account in archive_accounts:
        label = _account_label(archive_account)
        source_id = archive_account.get("id")

        # Collision: an account with the same name already on the destination.
        name_key = _norm_name(archive_account.get("name"))
        existing_acc = existing_by_name.get(name_key) if name_key else None
        if existing_acc is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            existing_id = existing_acc.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(EntityType.M3U_ACCOUNT, int(source_id), int(existing_id))
            logger.info(
                "[DBAS-M3U] Account '%s' already exists (dest id=%s); skipped.",
                label,
                existing_id,
            )
            continue

        payload, redacted_fields = _build_create_payload(archive_account)

        if is_dry_run:
            cat.would_create += 1
            # The PREVIEW of a redacted artifact was byte-identical to a
            # credential-bearing one, so an operator could not tell which variant
            # they were about to apply (bead …-6pilh). Report the same action item
            # here — with no destination id, because nothing was created.
            report.record_credential_reentry(
                EntityType.M3U_ACCOUNT,
                label,
                redacted_fields,
                source_export_id=source_id,
            )
            # Provisional remap so a DOWNSTREAM importer's FK to this would-be-
            # created account resolves on the dry-run exactly as it would on apply
            # (anti-drift: dry-run and apply must agree on what is creatable). The
            # source id is used as a stable provisional destination id — never sent
            # upstream, only consulted by the in-run remap.
            if source_id is not None:
                remap.add(EntityType.M3U_ACCOUNT, int(source_id), int(source_id))
            continue

        try:
            created = await client.create_m3u_account(payload)
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
                "[DBAS-M3U] Failed to restore M3U account '%s': %s", label, reason.value
            )
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(EntityType.M3U_ACCOUNT, int(source_id), dest_id)
            ledger.record_created(EntityType.M3U_ACCOUNT, dest_id, label)
            # Post-restore action item, not a failure: the account exists but
            # will not authenticate until the operator re-enters what the
            # redacted artifact could not carry.
            report.record_credential_reentry(
                EntityType.M3U_ACCOUNT,
                label,
                redacted_fields,
                source_export_id=source_id,
                destination_id=dest_id,
            )
            # Deferred group settings — extract; DO NOT trigger sync here.
            settings = _extract_group_settings(archive_account)
            if settings is not None:
                result.deferred_auto_sync_settings.append(
                    {"m3u_account_id": dest_id, "settings": settings}
                )
        logger.info("[DBAS-M3U] Restored M3U account '%s' (id=%s).", label, dest_id)
        if redacted_fields:
            # WARN, never silent — field NAMES only (…-6pilh).
            logger.warning(
                "[DBAS-M3U] Account '%s' (id=%s) was restored from a REDACTED "
                "artifact; %s left unset and must be re-entered before it will "
                "refresh.",
                label, dest_id, ", ".join(redacted_fields),
            )

    return result


async def _default_stream_count(client: DispatcharrClient, account_id: int) -> int:
    """Read the destination stream count for an account (default poll probe).

    Uses the paginated streams endpoint's ``count`` for the account. Read-only;
    logs only the account id and the count (never a credential).
    """
    try:
        page = await client.get_streams(page=1, page_size=1, m3u_account=account_id)
    except Exception as exc:  # pragma: no cover - defensive; live-only path
        logger.warning("[DBAS-M3U] Stream-count probe failed for account id=%s: %s", account_id, exc)
        return 0
    if isinstance(page, dict):
        count = page.get("count")
        if isinstance(count, int):
            return count
        results = page.get("results")
        if isinstance(results, list):
            return len(results)
    return 0


def _remap_group_settings(
    groups: list, remap: IdRemapTable | None
) -> tuple[list[dict], int]:
    """Rewrite each group setting's SOURCE ``channel_group`` pk to a DEST pk.

    The archived membership rows carry the source instance's group pk. Sending
    one verbatim either 400s or — worse — binds an UNRELATED destination group
    that happens to sit at that id. So an entry whose source id does not resolve
    through the ``CHANNEL_GROUP`` namespace is DROPPED, never forwarded stale
    (the same rule ``dbas.custom_stream_fallback`` applies to stream FKs).

    Args:
        groups: The deferred per-group settings (source-pk keyed).
        remap: The shared :class:`IdRemapTable`, populated by the channel-groups
            importer earlier in the SAME run. ``None`` drops every entry.

    Returns:
        ``(remapped_groups, unresolved_count)``.
    """
    remapped: list[dict] = []
    unresolved = 0
    for entry in groups or []:
        if not isinstance(entry, dict):
            continue
        source_id = entry.get("channel_group")
        dest_id = None
        if remap is not None and isinstance(source_id, int) and not isinstance(source_id, bool):
            dest_id = remap.resolve(EntityType.CHANNEL_GROUP, source_id)
        if dest_id is None:
            unresolved += 1
            continue
        remapped.append({**entry, "channel_group": dest_id})
    return remapped, unresolved


async def apply_deferred_auto_sync(
    *,
    deferred: list[dict],
    client: DispatcharrClient,
    remap: IdRemapTable | None = None,
    report: RestoreReport | None = None,
    stream_count_fn=None,
    sleep_fn=None,
    poll_interval_seconds: float = 5.0,
    max_polls: int = 60,
    stable_polls_required: int = 2,
) -> list[dict]:
    """Apply deferred M3U group settings AFTER Channels + Logos finish.

    For each deferred account (``{"m3u_account_id", "settings"}``):

    1. Apply the per-group settings — the enabled-group SELECTION plus any
       auto-sync range — via ``client.update_m3u_group_settings``, after
       rewriting each SOURCE group pk to its DESTINATION pk.
    2. is_active toggle workaround — PATCH ``is_active`` False then True, to coax
       a newly-imported M3U into fetching its streams (Dispatcharr quirk).
    3. Trigger the account refresh (``client.refresh_m3u_account``).
    4. 2-stage refresh poll — poll the destination stream count; terminate when
       it stabilizes across ``stable_polls_required`` consecutive polls
       (stream-count-stable heuristic), bounded by ``max_polls``.

    ORDER IS LOAD-BEARING (bead ``…-2o0cz``): the group settings MUST land before
    the refresh. Dispatcharr's ``PATCH /api/m3u/accounts/<id>/group-settings/``
    is an UPSERT (``ChannelGroupM3UAccount.objects.bulk_create(...,
    update_conflicts=True)`` — 0.28.2 ``apps/m3u/api_views.py``), so it works on
    an account that has never refreshed: the memberships it creates carry the
    archived ``enabled`` flag and ``xc_id``, and the refresh that follows then
    ingests exactly the groups the operator had selected. Applying it after the
    refresh would leave the first refresh with zero enabled groups — which is the
    state the drill measured.

    The refresh/poll/sleep are injected so the logic is fully unit-testable:

    Args:
        deferred: The importer's ``deferred_auto_sync_settings`` list.
        client: The Dispatcharr API client.
        remap: The shared :class:`IdRemapTable` for the SOURCE->DEST group-pk
            rewrite. ``None`` (a caller that has no remap) drops every group
            setting rather than forwarding a stale source pk.
        report: Optional shared :class:`RestoreReport` — unresolved groups are
            surfaced as a sanitized note rather than silently dropped.
        stream_count_fn: ``async (account_id) -> int`` probe for the current
            destination stream count. Defaults to a streams-endpoint count probe.
        sleep_fn: ``async (seconds) -> None`` sleep seam. Defaults to
            ``asyncio.sleep``.
        poll_interval_seconds: Seconds between polls (passed to ``sleep_fn``).
        max_polls: Hard upper bound on polls per account (never an infinite loop).
        stable_polls_required: Consecutive equal counts that mark "stabilized".

    Returns:
        Per-account apply summaries: ``{"m3u_account_id", "stream_count",
        "stabilized", "polls", "groups_applied"}`` — safe fields only, no
        credentials.

    NOTE (deferred verification follow-up): the unit tests exercise the
    branching logic with mocks. The LIVE behaviour (does the count actually
    stabilize, does the is_active toggle actually kick a fetch on a real
    Dispatcharr instance) needs a live instance and is NOT verified here — flagged
    as a follow-up.
    """
    if sleep_fn is None:
        import asyncio

        sleep_fn = asyncio.sleep
    if stream_count_fn is None:
        async def stream_count_fn(account_id):  # noqa: E306 - local default seam
            return await _default_stream_count(client, account_id)

    summaries: list[dict] = []
    for entry in deferred:
        account_id = entry.get("m3u_account_id")
        settings = entry.get("settings") or {}
        if account_id is None:
            continue

        # 1. Apply per-group settings (enabled selection + auto-sync range),
        #    with every SOURCE group pk rewritten to its DESTINATION pk.
        groups, unresolved = _remap_group_settings(
            settings.get("channel_groups"), remap
        )
        groups_applied = 0
        if groups:
            try:
                await client.update_m3u_group_settings(
                    account_id, {"group_settings": groups}
                )
                groups_applied = len(groups)
                logger.info(
                    "[DBAS-M3U] Applied %d group setting(s) (%d enabled) to account "
                    "id=%s before its refresh.",
                    len(groups),
                    sum(1 for g in groups if g.get("enabled")),
                    account_id,
                )
            except Exception as exc:
                logger.warning(
                    "[DBAS-M3U] Deferred group-settings apply failed for account id=%s: %s",
                    account_id,
                    exc,
                )
                if report is not None:
                    report.notes.append(
                        "M3U account id=%s: the archived enabled-group selection could "
                        "not be applied; re-select its groups and use Save & Refresh "
                        "before expecting streams." % account_id
                    )
        if unresolved and report is not None:
            report.notes.append(
                "M3U account id=%s: %d archived group selection(s) referenced a "
                "channel group that is not on this destination and were skipped."
                % (account_id, unresolved)
            )

        # 2. is_active toggle workaround — False then True.
        try:
            await client.patch_m3u_account(account_id, {"is_active": False})
            await client.patch_m3u_account(account_id, {"is_active": True})
        except Exception as exc:
            logger.warning(
                "[DBAS-M3U] is_active toggle failed for account id=%s: %s", account_id, exc
            )

        # 3. Trigger the refresh (allowed in the DEFERRED phase, not at import).
        try:
            await client.refresh_m3u_account(account_id)
        except Exception as exc:
            logger.warning(
                "[DBAS-M3U] Deferred refresh trigger failed for account id=%s: %s",
                account_id,
                exc,
            )

        # 4. 2-stage refresh poll — stream-count-stable heuristic, bounded.
        last_count: int | None = None
        stable_streak = 0
        polls = 0
        stabilized = False
        current_count = 0
        while polls < max_polls:
            await sleep_fn(poll_interval_seconds)
            polls += 1
            current_count = await stream_count_fn(account_id)
            if last_count is not None and current_count == last_count:
                stable_streak += 1
                if stable_streak >= stable_polls_required:
                    stabilized = True
                    break
            else:
                stable_streak = 1 if last_count is not None else 0
            last_count = current_count

        logger.info(
            "[DBAS-M3U] Deferred auto-sync applied for account id=%s "
            "(stream_count=%d, stabilized=%s, polls=%d).",
            account_id,
            current_count,
            stabilized,
            polls,
        )
        summaries.append(
            {
                "m3u_account_id": account_id,
                "stream_count": current_count,
                "stabilized": stabilized,
                "polls": polls,
                "groups_applied": groups_applied,
            }
        )

    return summaries


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
