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
THE ``user_agent`` FK (bead ``…-9h6cv``)
----------------------------------------------------------------------------

An M3U account's ``user_agent`` is a FOREIGN KEY to a Dispatcharr user-agent
row, not a header string, and the destination assigns that row its own id. The
create payload therefore rewrites it through the ``USER_AGENT`` remap namespace
(:func:`_resolve_user_agent_fk`); the step registries import USER_AGENT BEFORE
M3U_ACCOUNT so the namespace is populated when this importer runs. When the FK
cannot be resolved the field is DROPPED and the account is still created — see
:func:`_build_create_payload` for why this diverges from the stream-profile
sibling's skip-``DEPENDENCY_UNRESOLVED`` convention.

----------------------------------------------------------------------------
THE ``server_group`` FK (bead ``…-g8tyd``)
----------------------------------------------------------------------------

An M3U account's ``server_group`` is ALSO a foreign key — to a Dispatcharr
``ServerGroup`` row whose id the destination assigns itself — and it is
serialized on GET, so it reaches the create payload. Unlike ``user_agent`` there
is no namespace to translate it through: ECM's DBAS has no ``ServerGroup``
entity category and no ServerGroup importer, so nothing on the destination
corresponds to A's pk. The field is therefore ALWAYS DROPPED
(:func:`_drop_server_group_fk`), and the drop is reported.

The INVARIANT both cases serve: no importer forwards a source-side foreign key
to the destination without either resolving it through its remap namespace or
deliberately dropping it with a recorded reason. ``user_agent`` and
``server_group`` are the only two FKs on 0.28.2's
``M3UAccountSerializer.Meta.fields``; ``stream_profile`` is a model FK but is
NOT serialized, so it never reaches an archive.

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

from credential_sentinel import (
    credential_is_present,
    strip_redaction_sentinels,
    value_at_path,
)
from dbas.archive_keys import as_int
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

# All keys dropped before issuing the create. The ``user_agent`` FK is NOT in
# this set — it is remapped in place (or dropped when unresolvable); see
# ``_resolve_user_agent_fk``.
_DROPPED_CREATE_KEYS = _SOURCE_ID_KEYS | _NON_CREATE_KEYS

# The FK an M3U account carries into a Dispatcharr user-agent row (bead …-9h6cv).
_USER_AGENT_FK = "user_agent"

# The FK an M3U account carries into a Dispatcharr ``ServerGroup`` row (bead
# …-g8tyd). Deliberately NOT in ``_DROPPED_CREATE_KEYS``: that set is for keys
# that are never meaningful on a create, whereas this one is dropped as a
# reported DEGRADATION — see ``_drop_server_group_fk``.
_SERVER_GROUP_FK = "server_group"

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


def _resolve_user_agent_fk(
    archive_account: dict, remap: IdRemapTable
) -> tuple[bool, int | None]:
    """Resolve the account's optional ``user_agent`` FK through the remap table.

    Returns ``(resolved, dest_id)``:

    - No FK (absent / None): ``(True, None)`` — the account uses Dispatcharr's
      default agent; nothing to resolve.
    - FK present and remapped: ``(True, dest_id)``.
    - FK present but unmapped (or not an int): ``(False, None)`` — the caller
      DROPS the field; a stale source pk is never sent upstream.
    """
    source_agent = archive_account.get(_USER_AGENT_FK)
    if source_agent is None:
        return (True, None)
    try:
        source_agent_id = int(source_agent)
    except (TypeError, ValueError):
        return (False, None)
    dest_id = remap.resolve(EntityType.USER_AGENT, source_agent_id)
    if dest_id is None:
        return (False, None)
    return (True, dest_id)


def _drop_server_group_fk(payload: dict) -> bool:
    """Remove the account's ``server_group`` FK from the create payload.

    ``server_group`` is a foreign key to a Dispatcharr ``ServerGroup`` row whose
    id the DESTINATION assigns itself. There is no ``ServerGroup`` entity
    category and no ServerGroup importer in ECM's DBAS, so no remap namespace
    can translate A's pk — the field can only be DROPPED. Dispatcharr declares
    the column ``null=True, blank=True, on_delete=SET_NULL``, so an account
    without one is valid; forwarding a stale pk is not (live B answered
    ``400 {"server_group": ["Invalid pk \"20\" - object does not exist."]}``).

    Returns ``True`` only when a NON-NULL FK was actually removed — that is the
    degradation the caller reports. An absent or null ``server_group`` is the
    common shape and is left exactly as it is (``None`` is a legal value the
    destination accepts), so it is neither dropped nor reported.
    """
    source_group = payload.get(_SERVER_GROUP_FK)
    if source_group is None:
        return False
    payload.pop(_SERVER_GROUP_FK)
    return True


def _build_create_payload(
    archive_account: dict, remap: IdRemapTable
) -> tuple[dict, list[str], bool, bool]:
    """Build the create_m3u_account payload from an archive account record.

    Drops the archive source id, the embedded ``channel_groups`` settings list
    (reconciled and deferred separately), and read-only/derived fields. Keeps the
    credential fields (server_url/username/password) — they MUST be recreated for
    the account to function — but they are NEVER logged or reported.

    THE ``user_agent`` FK (bead ``…-9h6cv``). ``user_agent`` is a FOREIGN KEY to
    a Dispatcharr user-agent row whose id the DESTINATION assigns itself, not a
    header string. This importer used to forward source-A's raw pk verbatim, so a
    live B answered ``400 {"user_agent": ["Invalid pk \\"4\\" - object does not
    exist."]}`` — and because M3U_ACCOUNT is a FATAL failure category, the whole
    apply rolled back and NOTHING synced. The FK is now rewritten in place to the
    destination id through the ``USER_AGENT`` remap namespace (which the step
    registries populate BEFORE this importer runs).

    UNRESOLVABLE => DROP THE FIELD, KEEP THE ACCOUNT. The sibling convention for
    stream profiles (``importers/groups_profiles``) is to skip the whole row
    ``DEPENDENCY_UNRESOLVED``. That is right for a stream profile, which is a
    LEAF. An M3U account is the ROOT of the Phase-2 chain — EPG sources, channel
    groups, channels and streams all resolve through it — so skipping it cascades
    a whole-tree ``DEPENDENCY_UNRESOLVED`` for the sake of one optional field
    that Dispatcharr already has a default for. The account is created without
    the agent and the degradation is reported (never silent): the operator
    re-selects one agent instead of losing the entire replica.

    THE ``server_group`` FK (bead ``…-g8tyd``). ``server_group`` is a SECOND
    foreign key on the same record — to a Dispatcharr ``ServerGroup`` row, which
    groups M3U accounts that share provider credentials so they share
    credential-scoped connection counters. It is serialized on GET, so A's raw pk
    reached the create payload and B answered ``400 {"server_group": ["Invalid pk
    \"20\" - object does not exist."]}`` — the same total-blast-radius rollback
    as the ``user_agent`` case. There is no ``ServerGroup`` entity category and no
    ServerGroup importer, so unlike ``user_agent`` there is nothing to remap
    through: the field is ALWAYS DROPPED. ``ServerGroup`` carries no
    configuration of its own (a unique ``name`` and nothing else; the stream
    limits come from each account profile's ``max_streams``), so the replica
    loses a grouping label it can be given back in one action, not settings. The
    drop is reported like its sibling, never silent.

    A STANDARD (redact-by-default) artifact carries the ``***REDACTED***``
    placeholder in place of each credential. Writing that through produced an XC
    account that LOOKED configured and could not authenticate (bead ``…-6pilh``),
    so any sentinel-valued key is STRIPPED and the field is left unset — visibly
    incomplete, and absent to every presence check. Detection is by VALUE, so an
    encrypted + ``include_credentials`` artifact (which carries the real values)
    is unaffected.

    Returns:
        ``(payload, redacted_fields, user_agent_resolved, server_group_dropped)``
        — the create payload; the credential field NAMES that were stripped
        (never their values) so the caller can report them as a post-restore
        action item; whether the ``user_agent`` FK resolved (``False`` only when
        it was present and could not be remapped, in which case the field has
        been dropped); and whether a NON-NULL ``server_group`` FK was dropped.
    """
    payload = {
        k: v for k, v in archive_account.items() if k not in _DROPPED_CREATE_KEYS
    }
    user_agent_resolved, dest_agent_id = _resolve_user_agent_fk(archive_account, remap)
    if _USER_AGENT_FK in payload:
        if user_agent_resolved:
            # None is preserved as None: a free-standing account stays on the
            # destination's default agent.
            payload[_USER_AGENT_FK] = dest_agent_id
        else:
            payload.pop(_USER_AGENT_FK)
    server_group_dropped = _drop_server_group_fk(payload)
    stripped, redacted_fields = strip_redaction_sentinels(payload)
    return stripped, redacted_fields, user_agent_resolved, server_group_dropped


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


# Destination account statuses that mean "this account cannot ingest". Bead
# ``…-avrix`` measured ``status=error`` with "No streams returned from Xtream
# Codes provider" on exactly the credential-stale failure this detects.
_STALE_ACCOUNT_STATUSES: frozenset[str] = frozenset({"error"})


def destination_account_looks_stale(existing_acc: dict) -> bool:
    """True when the DESTINATION's own account row says it cannot ingest.

    ADR-013 INV-8 / S12(b) — the staleness signal for a hot standby whose
    provisioned provider credential has stopped working (rotated at the
    provider). Note what this is NOT: it never compares a credential VALUE.
    Doing so would pull B's secret back to A on a schedule to answer a question,
    which is the mirror image of bead ``…-msqf7``. Every input is state the
    cycle's destination read ALREADY returns — ``status`` and ``stream_count``
    — so this adds no fetch of any kind.

    It lives HERE, next to that read, rather than with the provisioning writer,
    because the cycle must be able to call it and the cycle must never be able
    to reach the writer (INV-2).

    The conjunction is deliberate. ``status=error`` alone fires on any transient
    upstream hiccup; zero streams alone fires on an account that simply has not
    refreshed yet. Together they are the shape ``avrix`` measured when a replica
    cannot authenticate: an errored account materializing nothing.

    A detected-stale credential must NEVER cause a push (S12(c) — scheduled or
    automatic re-push is forbidden). It causes a report; the operator decides
    and re-runs the provisioning action.
    """
    status = existing_acc.get("status")
    if not isinstance(status, str):
        return False
    if status.strip().lower() not in _STALE_ACCOUNT_STATUSES:
        return False
    count = existing_acc.get("stream_count")
    return not isinstance(count, int) or count <= 0


def stale_account_message(existing_acc: dict) -> str:
    """A sanitized operator-facing line for one stale destination account.

    ``last_message`` is an UPSTREAM error body echoed onto the row and can quote
    a request URL, so it is NEVER forwarded (the same hygiene as
    :func:`_sanitize_failure`). Only the account's own name and the two
    structural facts cross.
    """
    return (
        "Replicated provider account '%s' is in status 'error' with %s stream(s). "
        "If this target was provisioned with provider credentials, the credential "
        "has most likely stopped working. Re-run the provisioning action to push "
        "the current value — ECM never re-pushes on a schedule."
        % (_account_label(existing_acc), existing_acc.get("stream_count", 0) or 0)
    )


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
            # THE ACTION ITEM SURVIVES THE SKIP (bead …-ukjx5). The account is
            # here, and it still authenticates nowhere: skipping the CREATE is
            # not the same fact as the operator having re-entered the password.
            # Recording only on the create path made this a count of what the
            # cycle WROTE, so a scheduled sync said "2 accounts need credentials"
            # once and then nothing, forever, over a replica on which nothing had
            # changed. Asked of the DESTINATION ROW rather than of this run.
            _report_credentials_still_missing(
                report=report,
                archive_account=archive_account,
                remap=remap,
                existing_acc=existing_acc,
                label=label,
                source_id=source_id,
            )
            # THE GROUP SELECTION SURVIVES THE SKIP TOO (bead …-avrix, the same
            # shape as the credential action item above). Deferring only on the
            # CREATE path made the selection a property of the cycle that first
            # made the account, not of the source: measured live on 2026-08-21,
            # enabling a THIRD provider category on A left B at two, on a cycle
            # that reported ``SUCCESS`` with every counter at zero. A replica is
            # supposed to track its source, not a snapshot of the day it was
            # built.
            #
            # ``created: False`` is what keeps the blast radius where it was.
            # The sync path never refreshes anything, so it is inert there; on
            # the RESTORE path it tells ``apply_deferred_auto_sync`` to converge
            # this account's SELECTION and stop — an account this run did not
            # create does not get its streams refetched from the provider, which
            # is the behaviour a skip has always had.
            if existing_id is not None:
                existing_settings = _extract_group_settings(archive_account)
                if existing_settings is not None:
                    result.deferred_auto_sync_settings.append(
                        {
                            "m3u_account_id": int(existing_id),
                            "settings": existing_settings,
                            "created": False,
                        }
                    )
            logger.info(
                "[DBAS-M3U] Account '%s' already exists (dest id=%s); skipped.",
                label,
                existing_id,
            )
            continue

        (
            payload,
            redacted_fields,
            user_agent_resolved,
            server_group_dropped,
        ) = _build_create_payload(archive_account, remap)
        if not user_agent_resolved:
            # DEGRADED, not failed (…-9h6cv): the account is still created — see
            # _build_create_payload for why an M3U account is not skipped the way
            # its stream-profile sibling is. Reported on BOTH the preview and the
            # apply so the two agree. Name only; never a credential.
            logger.warning(
                "[DBAS-M3U] Account '%s' references a user agent that is not on "
                "this destination; the account is restored WITHOUT it and falls "
                "back to the default agent.",
                label,
            )
            report.notes.append(
                "M3U account '%s': its custom user agent is not on this "
                "destination, so the account was created without one and uses "
                "the default agent. Re-select an agent if the provider requires "
                "a specific one." % label
            )

        if server_group_dropped:
            # DEGRADED, not failed (…-g8tyd). Same disposition as the user_agent
            # sibling above, for a different reason: there is no ServerGroup
            # remap namespace to resolve through at all, so the FK can only be
            # dropped. Reported on BOTH the preview and the apply so the two
            # agree. Name only; never a credential.
            logger.warning(
                "[DBAS-M3U] Account '%s' belongs to a server group that does "
                "not exist on this destination; the account is restored "
                "WITHOUT one.",
                label,
            )
            report.notes.append(
                "M3U account '%s': its server group does not exist on this "
                "destination and cannot be recreated by a sync, so the account "
                "was created without one. Re-assign it to a server group if it "
                "shares provider connection limits with other accounts." % label
            )

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


def _report_credentials_still_missing(
    *,
    report: RestoreReport,
    archive_account: dict,
    remap: IdRemapTable,
    existing_acc: dict,
    label: str,
    source_id,
) -> None:
    """Report the credentials the DESTINATION account is still missing (…-ukjx5).

    Called on the ALREADY_EXISTS skip, where nothing is created and the create
    path's recorder therefore never fires. The shortfall is not "we just made an
    account with no password" — it is "the account on the destination has no
    password", which is true on every cycle until the operator fixes it and on no
    cycle afterwards.

    TWO HALVES, and both are load-bearing:

    * **What the artifact could not carry** comes from the SAME
      :func:`_build_create_payload` the create path uses, so the two can never
      disagree about which fields count as credentials. Its ``redacted_fields``
      are field NAMES, never values.
    * **What the destination still lacks** is read off ``existing_acc`` — the row
      the destination's own list endpoint returned — through
      :func:`credential_sentinel.credential_is_present`, which reads ECM's own
      placeholder as ABSENT rather than as a populated field.

    A field the destination has is dropped, and an account whose every redacted
    field has since been filled in reports nothing:
    :meth:`RestoreReport.record_credential_reentry` is a no-op on an empty list.
    Bead ``…-15g1j``'s rule holds by construction on the other side too — a
    source account with no credential produces no redacted field, so it is never
    an action item.

    RESIDUAL, stated rather than left implicit: this can only see what the
    destination's serializer returns. Measured on Dispatcharr 0.29.0,
    ``/api/m3u/accounts/`` returns both ``username`` and ``password``, so the
    check is real. A field a future serializer made write-only would read as
    absent forever, which is the noisy direction rather than the silent one —
    the deliberate choice, because an action item an operator can satisfy is
    recoverable and a lost one is not.
    """
    _, redacted_fields, _, _ = _build_create_payload(archive_account, remap)
    still_missing = [
        field
        for field in redacted_fields
        if not credential_is_present(value_at_path(existing_acc, field))
    ]

    # --- The OBSERVED half of the S11 insecure gate (ADR-013 INV-4 / threat
    # model row D16), and the staleness signal (INV-8). Both are read off the
    # SAME destination row this function already has, by the SAME
    # credential_is_present predicate it already ran. No new fetch, no new
    # comparison, and nothing about a credential's VALUE is examined or
    # recorded — only whether the destination has something at a path.
    #
    # Recording PRESENCE matters as much as recording absence: the recorded
    # provisioning marker sees only what ECM wrote, so an operator who entered
    # the provider credential on B by hand — the recovery ECM's own guide
    # documents — leaves the marker NULL while B holds a live credential that
    # the per-cycle destination read carries back to A on every cycle.
    if len(still_missing) < len(redacted_fields):
        report.record_destination_credentials_observed()
    if destination_account_looks_stale(existing_acc):
        report.record_provisioned_credential_stale(stale_account_message(existing_acc))

    if not still_missing:
        return
    logger.warning(
        "[DBAS-M3U] Account '%s' (id=%s) already exists on the destination but "
        "still has %s unset; it will not refresh until they are re-entered.",
        label, existing_acc.get("id"), ", ".join(still_missing),
    )
    report.record_credential_reentry(
        EntityType.M3U_ACCOUNT,
        label,
        still_missing,
        source_export_id=source_id,
        destination_id=as_int(existing_acc.get("id")),
    )


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


async def _apply_one_group_selection(
    *,
    account_id: int,
    settings: dict,
    client: DispatcharrClient,
    remap: IdRemapTable | None,
    report: RestoreReport | None,
) -> dict:
    """Write ONE account's archived per-group selection to the destination.

    THE ONE implementation of the group-selection apply. Both callers use it:
    :func:`apply_deferred_auto_sync` (the restore path, as step 1 of four) and
    :func:`apply_group_selection` (the sync path, as the only step). A second
    copy is exactly how the two paths would come to disagree about what a
    replica's provider account carries.

    NO PROVIDER TRAFFIC. ``PATCH /api/m3u/accounts/<id>/group-settings/`` is a
    pure destination-side upsert — ``ChannelGroupM3UAccount.objects.bulk_create(
    ..., update_conflicts=True)`` (verified live against Dispatcharr 0.29.0
    ``apps/m3u/api_views.py::update_group_settings``, 2026-08-21: it validates
    the channel ranges, upserts the rows in one transaction, and returns; it
    triggers no refresh and opens no socket to the provider). That is what lets
    the sync path call it without violating ADR-013 S9, which forbids
    re-triggering provider auto-sync on B, not writing B's own settings.

    Returns:
        ``{"m3u_account_id", "selections_total", "groups_applied",
        "groups_unresolved", "groups_enabled"}`` — safe fields only.
    """
    source_groups = settings.get("channel_groups") or []
    selections_total = len(source_groups) if isinstance(source_groups, list) else 0
    groups, unresolved = _remap_group_settings(source_groups, remap)
    groups_applied = 0
    groups_enabled = 0
    apply_failed = False
    if groups:
        try:
            await client.update_m3u_group_settings(
                account_id, {"group_settings": groups}
            )
            groups_applied = len(groups)
            groups_enabled = sum(1 for g in groups if g.get("enabled"))
            logger.info(
                "[DBAS-M3U] Applied %d group setting(s) (%d enabled) to account id=%s.",
                groups_applied,
                groups_enabled,
                account_id,
            )
        except Exception as exc:
            apply_failed = True
            logger.warning(
                "[DBAS-M3U] Group-settings apply failed for account id=%s: %s",
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

    # The operator-facing accounting (bead …-avrix). A selection the source had
    # and the destination did not receive is what decides whether the replica
    # ingests the same provider content — so it is counted and named, not left
    # to a ``notes`` entry the UI only renders on rollback residue.
    #
    # The "nothing was lost, so record nothing" decision lives in the recorder
    # and ONLY there. Guarding it here as well produced a branch that no mutation
    # could reach — the third time this module's suite has been measured blind on
    # a duplicate guard (bead …-15g1j's own note), so the duplicate is removed
    # rather than kept unexercised.
    if report is not None:
        if apply_failed:
            reason = "the destination rejected the group-settings write"
        elif unresolved:
            reason = "the source's channel group is not on this destination"
        else:
            reason = "not applied"
        report.record_provider_group_selection(
            destination_account_id=account_id,
            selections_total=selections_total,
            selections_applied=groups_applied,
            selections_unapplied=selections_total - groups_applied,
            enabled_applied=groups_enabled,
            reason=reason,
        )

    return {
        "m3u_account_id": account_id,
        "selections_total": selections_total,
        "groups_applied": groups_applied,
        "groups_unresolved": unresolved,
        "groups_enabled": groups_enabled,
    }


async def apply_group_selection(
    *,
    deferred: list[dict],
    client: DispatcharrClient,
    remap: IdRemapTable | None = None,
    report: RestoreReport | None = None,
) -> list[dict]:
    """Apply ONLY the archived per-group ENABLE selection — never a refresh.

    The group-selection half of :func:`apply_deferred_auto_sync`, without the
    is_active toggle, the refresh trigger, or the stream-count poll. Written for
    the cross-instance sync path (bead ``…-avrix``), where ADR-013 S9 forbids
    re-triggering provider auto-sync on the destination every cycle but says
    nothing about the destination's own stored settings — and where dropping
    them left the replica unable to ingest what the source ingests.

    WHY IT MATTERS, measured on Dispatcharr 0.29.0 on 2026-08-21 with a real XC
    account of 777 provider categories, 2 of them enabled on the source:

    * With the selection dropped, the destination's account held ZERO group
      rows. Given its credentials, its own refresh created all 777 rows from
      discovery and then answered ``Filtered 0 streams from 0 enabled
      categories`` — 0 streams against the source's 316.
    * The direction of that failure is not even stable. It is decided by
      ``auto_enable_new_groups_live``, which the account faithfully inherits
      from the source and which Dispatcharr defaults to ``True``: with it
      ``True`` the same empty-selection discovery enabled 777 of 777 categories,
      i.e. the provider's entire 53,661-stream catalogue.

    Carrying the selection removes both, because the groups are then no longer
    "new to this account" when the destination first refreshes.

    Args:
        deferred: The importer's ``deferred_auto_sync_settings`` list.
        client: The Dispatcharr API client (destination).
        remap: The shared :class:`IdRemapTable` for the SOURCE->DEST group-pk
            rewrite. ``None`` drops every entry rather than forwarding a stale
            source pk.
        report: Optional shared :class:`RestoreReport` — receives the
            named per-account accounting for anything that did not land.

    Returns:
        Per-account summaries; ``[]`` when nothing was deferred.
    """
    summaries: list[dict] = []
    for entry in deferred or []:
        account_id = entry.get("m3u_account_id")
        if account_id is None:
            continue
        summaries.append(
            await _apply_one_group_selection(
                account_id=account_id,
                settings=entry.get("settings") or {},
                client=client,
                remap=remap,
                report=report,
            )
        )
    return summaries


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
        #    with every SOURCE group pk rewritten to its DESTINATION pk. Shared
        #    with the sync path's ``apply_group_selection`` so the two cannot
        #    disagree about what a replica's provider account carries.
        groups_applied = (
            await _apply_one_group_selection(
                account_id=account_id,
                settings=settings,
                client=client,
                remap=remap,
                report=report,
            )
        )["groups_applied"]

        # AN ACCOUNT THIS RUN DID NOT CREATE STOPS HERE (bead …-avrix). Its
        # SELECTION is converged onto the source's — that is what makes a
        # replica track its source rather than the day it was built — but its
        # streams are not refetched from the provider, because a skip has never
        # done that and making it do so would silently add a provider refresh
        # plus a bounded poll per pre-existing account to every restore.
        # ``created`` absent means True, so every caller that predates the key
        # (and every existing test) keeps all four steps.
        if not entry.get("created", True):
            logger.info(
                "[DBAS-M3U] Converged the group selection for pre-existing "
                "account id=%s (%d setting(s)); its streams were NOT refetched.",
                account_id,
                groups_applied,
            )
            summaries.append(
                {
                    "m3u_account_id": account_id,
                    "groups_applied": groups_applied,
                    "refreshed": False,
                }
            )
            continue

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
