"""One-time provider-credential provisioning for a cross-instance sync target.

Bead ``enhancedchannelmanager-wd20y``. Specification: ADR-013's 2026-08-22
amendment (decisions **S10-S13**, invariants **INV-2 / INV-3 / INV-4 / INV-6 /
INV-8 / INV-9**) and ``docs/security/threat_model_dbas_import.md`` §11.5 rows
**D11-D16**.

WHAT THIS IS
------------
A replica already arrives structurally complete — the end-to-end acceptance run
(bead ``kdz6p``) measured 316 channels, 779 groups, 3 profiles, 948 profile
memberships, 316 logo bindings and 183 EPG links all crossing A->B. What B does
not have is a working provider credential, so every stream URL on B reads
``.../live/***REDACTED***/***REDACTED***/.ts`` and 404s.

This module is the ONE explicit, operator-initiated, audited, TLS-verified
action that closes that gap: it harvests the provider credential values off A's
OWN provider records and writes them onto B's replicated provider accounts,
once, and records the fact. B then fetches its own streams on its next refresh.

WHY THIS FILE LIVES IN ``backend/tasks/`` AND IS NAMED ``dbas_sync_*``
---------------------------------------------------------------------
Threat model §11.5.4 item 2, and it is a BUILD GATE. The SSRF chokepoint guard
(``tests/test_ssrf_chokepoint_guard.py``) scans ``cloud_storage/*.py`` and the
literal glob ``_SYNC_GLOB = "dbas_sync*.py"`` under ``backend/tasks/``.
``backend/routers/`` is NOT scanned, so the natural home for this code — a route
handler on ``routers/sync_targets.py`` — would place the one outbound path that
carries a credential OUTSIDE the chokepoint guard entirely. The file name is
therefore load-bearing, and ``test_ssrf_chokepoint_guard.py``
``test_provisioning_writer_is_in_scope`` pins it.

There is no raw outbound primitive here at all: every request goes through
:func:`tasks.dbas_sync_client.make_remote_client`, whose pinned transport routes
each request through ``security.ssrf.validate_outbound_url`` at execute time.

INV-2 — THE ONE-TIME PATH MUST BE UNREACHABLE FROM THE CYCLE
------------------------------------------------------------
Nothing in this module is an ``ImporterStep``; it is absent from
:func:`tasks.dbas_sync_engine.sync_config_importer_steps`; and neither
``tasks.dbas_sync`` nor ``tasks.dbas_sync_engine`` may import it, transitively,
by any path. **The dependency edge runs one way only: this module imports the
cycle's helpers, never the reverse.**

That is not tidiness, it is the single structural control the whole design rests
on. Under the ratified HARVEST input model (S10) the cycle already holds every
value a recurring push would need — ``routers.backup._collect_credential_values``
walks the raw gather on every scheduled run today, because that is what makes
``msqf7``'s literal-match path-segment rule possible. Making the cycle push them
is therefore a single call edge with no missing input, and INV-3 (nothing
persisted on A) does NOT prevent it. INV-2 does, and INV-2 alone.

Enforced by ``tests/tasks/test_sync_provisioning_reachability.py``, which walks
the transitive import closure statically (function-level imports included, since
a lazy import inside a function is a call path a ``sys.modules`` check cannot
see) and ships with its own red-proof.

INV-6 — THE WRITABLE FIELD SET IS THE REDACTOR'S OWN FIELD SET
--------------------------------------------------------------
Not a maintained literal. For each record this module computes ``redacted_fields``
exactly the way the importers' ``_build_create_payload`` does — run the shipped
deep redactor over the record, then ``strip_redaction_sentinels`` — and then
reads those SAME dotted paths off the RAW record with
``credential_sentinel.value_at_path``. The two halves cannot drift, because
there is only one half: whatever the redactor names as redacted is exactly what
is provisionable, per entity, per type.

That matters more under the harvest than it would have under operator-typed
input, because **no human reads the values before they cross**.

SCOPE — A CLOSED SET, ENFORCED IN CODE (threat model row D14)
-------------------------------------------------------------
A harvest is a loop over records, and a loop widens by accident.
:data:`PROVISIONABLE_SECTIONS` is a closed named set of exactly two categories,
separate from and narrower than the per-cycle sync allowlist. ECM's own settings
secrets, alert-method secrets, cloud-target and sync-target credentials, and
``dispatcharr_users`` are never provisioning inputs and cannot become inputs by
a gather returning more.

The credential-bearing account types this covers, enumerated rather than
generalised from the XC case:

===================  ===================================  ==================
Type                 Credential shape                     Provisioned
===================  ===================================  ==================
M3U ``XC``           ``username`` + ``password`` fields   yes
M3U ``STD``          credential INSIDE ``server_url``     yes (a URL, not a
                                                          password — a form
                                                          offering only
                                                          user/pass boxes
                                                          silently misses it)
M3U ``STD`` tuner    none (a LAN HDHomeRun URL)           nothing to do
EPG ``xmltv``        credential embedded in ``url``       yes
EPG ``schedules_     ``username`` harvestable;            username yes,
direct``             ``password`` WRITE-ONLY upstream     password only if
                                                          the operator
                                                          supplies it — see
                                                          below
EPG ``dummy``        none                                 nothing to do
===================  ===================================  ==================

SCHEDULES DIRECT (threat model row D15)
---------------------------------------
Dispatcharr marks the SD password ``write_only`` with no admin re-add and
SHA1-hashes it at fetch, so the value never enters ECM's process and CANNOT be
harvested. Absence here means UNREADABLE, not unset — which is precisely why the
statement about it is driven by ``source_type`` and never by a presence check:
an SD password was never in the gather, so it is never a ``redacted_field``, so
a presence-driven report can never name it. The operator would read "no report"
as "fully provisioned", which is the assumption they carry into an incident.

Every run therefore STATES the SD position for every ``schedules_direct`` source
it saw (:attr:`ProvisioningOutcome.schedules_direct_notes`), whether or not a
password was supplied. An operator MAY supply one for the run
(``schedules_direct_password``): it is request-scoped, applied to the SD sources
of this one action, never persisted on A, never on a cycle, and audited by FIELD
NAME only.

INV-3 — NOTHING IS PERSISTED ON A
---------------------------------
No column, no cache, no settings key, no queued payload holds a harvested value.
The two columns this feature adds to ``sync_targets`` are TIMESTAMPS
(``credentials_provisioned_at``, ``destination_credential_observed_at``).

INV-9 — DE-PROVISION IS HONEST, NOT COSMETIC
--------------------------------------------
The clear is ATTEMPTED on B over the same derived field set the provision wrote.
The marker flips ONLY if that write succeeded for EVERY targeted account. A
partial or total failure leaves the marker set, leaves ``insecure`` refused, and
names the accounts still holding a credential. A destination error can never be
swallowed into a success — see :func:`_write_credentials`, which records the
per-account verdict from the write call's own outcome.

And a SUCCESSFUL de-provision guarantees exactly one thing: B's provider account
rows no longer hold the credential, so B will not re-authenticate with it.
:data:`DEPROVISION_RESIDUAL_STATEMENT` is what the operator is told at the
moment they do it, because everything else survives — B's own stream rows
(``msqf7`` surveyed 1,409,363 provider URLs and found 100% path-credentialed),
B's backups, B's logs, anything downstream that consumed B's output, and the
provider side, since de-provision is NOT revocation.

WHAT THIS MODULE MUST NEVER DO
------------------------------
Relaxing ``_redact_credentials_deep``, ``_scrub_credential_urls``,
``_rewrite_known_credential_segments`` or ``_collect_credential_values`` is not
an implementation option, and "we provision credentials now anyway, so the
redactor is redundant" is the specific wrong conclusion. The redactor governs
the RECURRING path; this is a different path. Bead ``1td94`` additionally makes
the sentinel load-bearing for CORRECTNESS (playability counting, Tier-1 stream
matching), not only for secrecy — a relaxed redactor re-arms two defects at
once. This module only ever READS the redactor's output to learn field names.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import journal
from credential_sentinel import (
    credential_is_present,
    credential_path_is_operator_actionable,
    value_at_path,
)
from dbas.importers.epg_sources import (
    _build_create_payload as _build_epg_payload,
    _existing_by_identity,
    _identity_key,
)
from dbas.importers.m3u_accounts import (
    _account_label,
    _build_create_payload as _build_m3u_payload,
    _existing_by_name,
    _norm_name,
)
from dbas.restore_contracts import EntityType, IdRemapTable
from routers.backup import (
    _collect_credential_values,
    _gather_dispatcharr_sections,
    _redact_credentials_deep,
)
from tasks.dbas_sync_client import make_remote_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The closed provisioning surface (threat model row D14)
# ---------------------------------------------------------------------------

# The ONLY gathered categories this feature ever reads. Deliberately a separate,
# narrower constant than the per-cycle SYNC_CONFIG_CATEGORIES: two allowlists,
# two owners, neither inheriting the other's coverage. A category added to the
# sync does NOT become provisionable.
PROVISIONABLE_SECTIONS: frozenset[str] = frozenset({"m3u_accounts", "epg_sources"})

# The EPG source type whose password Dispatcharr never returns (write-only, no
# admin re-add, SHA1-hashed at fetch). Absence is UNREADABLE, not unset — see
# the module docstring.
SCHEDULES_DIRECT_SOURCE_TYPE = "schedules_direct"

# The SD field an operator may supply for the run. Named here so the statement,
# the write and the audit row all use one spelling.
SCHEDULES_DIRECT_PASSWORD_FIELD = "password"


# ---------------------------------------------------------------------------
# Audit (S13 / threat model row D9)
# ---------------------------------------------------------------------------

# Alongside the existing sync_outbound / sync_insecure_tls rows written by
# tasks.dbas_sync_client.audit_insecure_cycle.
PROVISION_JOURNAL_CATEGORY = "sync_outbound"
# Distinct action types so each is greppable and countable on its own.
PROVISION_ACTION_TYPE = "sync_provision_credentials"
DEPROVISION_ACTION_TYPE = "sync_deprovision_credentials"

# The two surfaces that reach sync-target mutation. Recorded on every row so a
# provisioning attributed to the SCHEDULER is visible as what it is: not a log
# line, but THE ALARM that the one-time path has become recurring (D12's only
# detector). Nothing in this module is reachable from a cycle (INV-2); this is
# the backstop for the day that stops being true.
SURFACE_REST = "rest"
SURFACE_MCP = "mcp"


# ---------------------------------------------------------------------------
# What a de-provision cannot guarantee (S11) — told at the moment of the action
# ---------------------------------------------------------------------------

DEPROVISION_RESIDUAL_STATEMENT = (
    "A successful de-provision guarantees exactly one thing: the replica's "
    "provider account rows no longer hold the credential, so it will not "
    "re-authenticate with it. It does NOT retract the credential from the "
    "replica's own stream rows (provider stream URLs carry it in their path "
    "segments), its backups and exports, its logs and status fields, or "
    "anything downstream that consumed its output while provisioned. "
    "De-provision is NOT revocation: the credential stays valid at the "
    "provider until you rotate it there, which is outside ECM. The replica "
    "also does not immediately go dark — it keeps serving from its existing "
    "stream rows until its next refresh fails, so \"it still works\" is not "
    "evidence the clear did not happen."
)


class ProvisioningRefused(Exception):
    """A provisioning-surface refusal that carries its own remedy.

    Raised by the S11 gate predicates. Callers (the REST router, and therefore
    the MCP tools that call the same routes) turn it into a 409 with this
    message verbatim — the reason AND the remedy, never a silent refusal.
    """


# ---------------------------------------------------------------------------
# Outcome shapes
# ---------------------------------------------------------------------------


@dataclass
class AccountWrite:
    """One destination provider account this action wrote to, or tried to.

    ``fields`` are field NAMES only. No value, no fragment of a value, and no
    masked tail of a value ever enters this object — it is what the journal row
    and the operator-facing response are built from (S13).
    """

    entity_type: EntityType
    destination_id: Optional[int]
    label: str
    fields: list[str]
    ok: bool
    error: Optional[str] = None

    def as_audit_dict(self) -> dict:
        return {
            "entity_type": self.entity_type.value,
            "destination_id": self.destination_id,
            "name": self.label,
            "fields": list(self.fields),
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class ProvisioningOutcome:
    """The result of one provisioning or de-provisioning attempt."""

    action: str
    target_id: Optional[int]
    target_name: str
    tls_verified: bool
    written: list[AccountWrite] = field(default_factory=list)
    failed: list[AccountWrite] = field(default_factory=list)
    # Credential paths the redactor named but that this action could not write
    # (nested paths with no operator-writable field on the destination). Named
    # rather than silently dropped — INV-7's rule that silence is only permitted
    # when it is true.
    unwritable_fields: list[str] = field(default_factory=list)
    # Source provider accounts with no counterpart on the destination.
    unmatched: list[str] = field(default_factory=list)
    # The source_type-driven Schedules Direct statement (threat model D15).
    schedules_direct_notes: list[str] = field(default_factory=list)
    marker_set: bool = False
    residual_statement: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """True when every account this action targeted was written."""
        return not self.failed

    @property
    def accounts_written(self) -> int:
        return len(self.written)

    @property
    def field_names(self) -> list[str]:
        """Every distinct field NAME this action wrote, in first-seen order."""
        seen: list[str] = []
        for entry in [*self.written, *self.failed]:
            for name in entry.fields:
                if name not in seen:
                    seen.append(name)
        return seen

    def as_response(self) -> dict:
        """The operator-facing shape. Field names only — never a value."""
        return {
            "action": self.action,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "tls_verified": self.tls_verified,
            "succeeded": self.succeeded,
            "accounts_written": self.accounts_written,
            "fields_written": self.field_names,
            "written": [w.as_audit_dict() for w in self.written],
            "failed": [w.as_audit_dict() for w in self.failed],
            "unwritable_fields": list(self.unwritable_fields),
            "unmatched_accounts": list(self.unmatched),
            "schedules_direct_notes": list(self.schedules_direct_notes),
            "provisioned": self.marker_set,
            "residual_statement": self.residual_statement,
        }


# ---------------------------------------------------------------------------
# INV-4 / S11 — the insecure gate, ONE predicate for every surface
# ---------------------------------------------------------------------------


def target_holds_credentials(target) -> bool:
    """True when this target is RECORDED or OBSERVED to have a credential on B.

    The widened S11 predicate (threat model row D16; PO ruling 2026-08-22 on
    §11.5.4 item 5). Two independent sources, and the second is why the gate is
    not a tautology on ECM's own bookkeeping:

    * **RECORDED** — ``credentials_provisioned_at``: ECM wrote a credential to B
      and has not seen an de-provision succeed since.
    * **OBSERVED** — ``destination_credential_observed_at``: a sync cycle SAW a
      credential on B's own provider account rows. This covers the credential
      ECM did NOT write — the operator entering it on B by hand, which is the
      recovery ECM's own guide documents today. The recorded marker cannot see
      that case at all, and it is the case rated High and reachable today.

    Presence only. Nothing here compares a value, and neither column ever holds
    one.
    """
    return (
        getattr(target, "credentials_provisioned_at", None) is not None
        or getattr(target, "destination_credential_observed_at", None) is not None
    )


def insecure_refusal_reason(target, *, requested_insecure: bool) -> Optional[str]:
    """Why setting ``insecure`` on this target is refused, or ``None``.

    Half one of the SYMMETRIC S11 refusal: a target may not be in both states
    "TLS verification disabled" and "holds a provider credential on B", and the
    two writes are refused whichever order they arrive in.

    **Clearing ``insecure`` (true -> false) is always allowed** — it can only
    tighten. That is why this takes the REQUESTED value rather than reading the
    row: a write that turns verification back on must never be refused.

    The exposure this bounds is not only the outbound push. The per-cycle
    DESTINATION READ pulls B's provider account rows back to A —
    ``dbas.importers.m3u_accounts._report_credentials_still_missing`` inspects
    them, and on Dispatcharr 0.29.0 ``/api/m3u/accounts/`` returns both
    ``username`` and ``password`` to an admin caller. So once B holds a
    credential, every subsequent cycle over an unverified-TLS connection carries
    that credential across the network INBOUND, unattended, on a schedule.

    THE REMEDY DEPENDS ON WHICH HALF FIRED, and stating the wrong one is worse
    than stating none. De-provision is the remedy for a credential ECM WROTE.
    An OBSERVED credential ECM did not write has no marker to clear and no
    ECM-side record of which fields to clear — de-provision is not its remedy;
    clearing it on B (or fixing B's certificate) is.

    Args:
        target: the ``SyncTarget`` row being written.
        requested_insecure: the value the caller is asking for.

    Returns:
        A refusal message naming the reason AND the real remedy, or ``None``.
    """
    if not requested_insecure:
        return None
    recorded = getattr(target, "credentials_provisioned_at", None) is not None
    observed = getattr(target, "destination_credential_observed_at", None) is not None
    if not (recorded or observed):
        return None

    name = getattr(target, "name", None) or "this sync target"
    if recorded:
        return (
            "Cannot disable TLS verification on sync target '%s': it has been "
            "provisioned with provider credentials, so the replica holds a live "
            "provider secret and every sync cycle would carry it over an "
            "unverified connection — outbound on the push AND inbound on the "
            "destination read. De-provision the target's credentials first "
            "(that write is attempted on the replica and only clears the marker "
            "if it succeeds), or fix the certificate." % name
        )
    return (
        "Cannot disable TLS verification on sync target '%s': a sync cycle "
        "observed a provider credential on the replica's own provider accounts "
        "that ECM did not write — most likely entered on the replica by hand. "
        "Every cycle would carry it back over an unverified connection. "
        "ECM cannot de-provision what it did not provision, so there are two "
        "remedies and neither is something ECM does for you: (1) install a "
        "valid certificate on the replica and leave TLS verification on — this "
        "keeps the standby working and ends the exposure, and is the one to "
        "prefer; or (2) remove the credential on the replica itself, after "
        "which the next cycle observes its absence and this setting becomes "
        "available again — at the cost of the replica no longer serving."
        % name
    )


def provision_refusal_reason(target) -> Optional[str]:
    """Why provisioning this target is refused, or ``None``.

    Half two of the SYMMETRIC S11 refusal. Provisioning is refused on a target
    with ``insecure=true`` — handing a live provider credential to a destination
    over a connection whose certificate is not verified is the exact combination
    S11 forbids, and it is refused rather than silently tightened: overriding a
    security-relevant setting the operator deliberately set is unauditable at
    the moment of the click, and denies them the information they need BEFORE
    handing over a secret.
    """
    if not getattr(target, "insecure", False):
        return None
    name = getattr(target, "name", None) or "this sync target"
    return (
        "Cannot provision provider credentials to sync target '%s': TLS "
        "verification is disabled for it (insecure=true), and a provider "
        "credential must never cross an unverified connection. Turn TLS "
        "verification back on for this target (clearing 'insecure' is always "
        "allowed), then provision." % name
    )


# ---------------------------------------------------------------------------
# INV-6 — the writable field set, derived from the redactor's own output
# ---------------------------------------------------------------------------


def _redact_sections(sections: dict) -> dict:
    """Run the SHIPPED per-cycle redactor over a raw gather.

    Identical call to the one :func:`tasks.dbas_sync_engine.build_live_source_plan`
    makes, including the harvested ``known_secrets`` / ``known_identities`` that
    give the path-segment rule literal values to match. Same inputs, same
    function, therefore the same field set the cycle would name as redacted —
    which is the whole of INV-6.

    Nothing here weakens the redactor: this module only reads WHICH keys it
    replaced, and the values it writes come from the RAW record.
    """
    known_secrets, known_identities = _collect_credential_values(sections)
    return _redact_credentials_deep(
        sections,
        preserve_keys=frozenset(),
        known_secrets=known_secrets,
        known_identities=known_identities,
    )


def _partition_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split redacted paths into what can be written on B and what cannot.

    Two exclusions, both derived rather than listed:

    * ``credential_path_is_operator_actionable`` drops the destination's own
      CACHED copy of the provider's reply (``…custom_properties.user_info.*``).
      There is no field to write them into, and the destination rewrites that
      blob wholesale on its next successful refresh using whatever credentials
      it DID get. Writing them would be writing into a cache.
    * A path that is not a top-level key has no operator-writable field on the
      destination serializer to PATCH. Those are returned as ``unwritable`` and
      NAMED in the outcome rather than silently dropped.

    Returns:
        ``(writable, unwritable)`` — field names only, in document order.
    """
    writable: list[str] = []
    unwritable: list[str] = []
    for path in paths:
        if not credential_path_is_operator_actionable(path):
            continue
        if "." in path or "[" in path:
            unwritable.append(path)
            continue
        writable.append(path)
    return writable, unwritable


def m3u_credential_paths(redacted_account: dict, remap: IdRemapTable) -> list[str]:
    """The credential field paths the redactor named on ONE M3U account.

    Computed by the SAME ``_build_create_payload`` the importer uses, so the
    provisionable set and the redacted set are one set rather than two that
    happen to agree today.
    """
    _, redacted_fields, _, _ = _build_m3u_payload(redacted_account, remap)
    return redacted_fields


def epg_credential_paths(redacted_source: dict) -> list[str]:
    """The credential field paths the redactor named on ONE EPG source."""
    _, redacted_fields = _build_epg_payload(redacted_source, None)
    return redacted_fields


def _harvest_values(raw_record: dict, paths: list[str]) -> dict:
    """Read the RAW values at the redactor's own dotted paths.

    The other half of the round trip ``credential_sentinel`` documents: the
    redactor reports WHICH keys it removed; this asks the raw record what it has
    at those same paths. A path whose raw value is absent, empty or is itself
    the sentinel yields nothing — there is no credential there to provision, and
    writing an empty value would be writing a fake one.
    """
    harvested: dict = {}
    for path in paths:
        value = value_at_path(raw_record, path)
        if credential_is_present(value):
            harvested[path] = value
    return harvested


# ---------------------------------------------------------------------------
# INV-8 / S12(b) — staleness lives with the CYCLE, not here
# ---------------------------------------------------------------------------
#
# ``destination_account_looks_stale`` / ``stale_account_message`` are in
# ``dbas.importers.m3u_accounts``, alongside the destination read that already
# returns the state they read. They MUST NOT live in this module: the cycle has
# to call them, and the cycle importing this module is precisely what INV-2
# forbids. The dependency runs one way — this module may import the cycle's
# helpers; the cycle may never import this one.


# ---------------------------------------------------------------------------
# The destination write
# ---------------------------------------------------------------------------


async def _write_credentials(
    *,
    client,
    entity_type: EntityType,
    destination_id: Optional[int],
    label: str,
    values: dict,
) -> AccountWrite:
    """PATCH one destination provider record, and report the write's OWN verdict.

    THE FAILURE PATH IS THE POINT (INV-9). A destination error is never swallowed
    into a success: any exception from the client call produces ``ok=False`` on
    the returned record, which is what decides whether the de-provision marker
    flips. The exception text is NOT forwarded — an upstream error body can echo
    a ``server_url`` — only its class name, which is diagnostic without being a
    disclosure.
    """
    fields = sorted(values)
    if destination_id is None:
        return AccountWrite(
            entity_type=entity_type,
            destination_id=None,
            label=label,
            fields=fields,
            ok=False,
            error="no destination id",
        )
    try:
        if entity_type == EntityType.M3U_ACCOUNT:
            await client.patch_m3u_account(destination_id, dict(values))
        else:
            await client.update_epg_source(destination_id, dict(values))
    except Exception as exc:  # noqa: BLE001 — every failure mode is a failed write
        logger.warning(
            "[SYNC-PROVISION] Write failed for %s '%s' (destination id=%s): %s",
            entity_type.value, label, destination_id, exc.__class__.__name__,
        )
        return AccountWrite(
            entity_type=entity_type,
            destination_id=destination_id,
            label=label,
            fields=fields,
            ok=False,
            error=exc.__class__.__name__,
        )
    logger.info(
        "[SYNC-PROVISION] Wrote %d credential field(s) to %s '%s' "
        "(destination id=%s): %s",
        len(fields), entity_type.value, label, destination_id, ", ".join(fields),
    )
    return AccountWrite(
        entity_type=entity_type,
        destination_id=destination_id,
        label=label,
        fields=fields,
        ok=True,
    )


# ---------------------------------------------------------------------------
# Schedules Direct statement (threat model row D15) — by source_type, never
# by a presence check, because presence is unknowable for a write-only field.
# ---------------------------------------------------------------------------


def schedules_direct_notes(
    sources: list[dict], *, password_supplied: bool
) -> list[str]:
    """State the SD position for every ``schedules_direct`` source, always.

    Driven by ``source_type``. A presence check CANNOT work here: the SD password
    is write-only upstream, so it never enters the gather, so it is never a
    redacted field, so a presence-driven reporter can never name it. The operator
    would read "no report" as "fully provisioned".

    The consequence, stated plainly rather than discovered: the standby still
    SERVES VIDEO — streams come from M3U accounts, which harvest fine. What it
    loses is guide data from that source until the password is entered.
    """
    notes: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("source_type") != SCHEDULES_DIRECT_SOURCE_TYPE:
            continue
        name = source.get("name") or "<unnamed>"
        if password_supplied:
            notes.append(
                "Schedules Direct source '%s': its password is the one field "
                "that needs your input, because Dispatcharr never returns it "
                "and there is nothing on this instance to read. The value you "
                "supplied was written to the replica for this run and was not "
                "persisted here. The replica does not return it either, so ECM "
                "can confirm it WROTE the value but never that the replica "
                "holds a working one — a mistyped password surfaces as the "
                "replica's EPG source failing to fetch, and the remedy is to "
                "re-run this action." % name
            )
        else:
            notes.append(
                "Schedules Direct source '%s': its password needs your input. "
                "Dispatcharr never returns it, so there is nothing on this "
                "instance to harvest — this is unreadable, not unset, which is "
                "why you are being told rather than left to find the gap. "
                "Supply it with a provisioning run, or enter it on the replica "
                "by hand. Until then the replica still serves video; it goes "
                "without guide data from this source." % name
            )
    return notes


# ---------------------------------------------------------------------------
# Audit (S13)
# ---------------------------------------------------------------------------


def _journal_provisioning(
    *,
    action_type: str,
    outcome: ProvisioningOutcome,
    actor: Optional[str],
    surface: str,
) -> None:
    """Write the ONE journal row this attempt produces — success or failure.

    S13. Records the actor and which surface, the target, the destination entity
    type / id / operator-facing NAME of each account, the FIELD NAMES written or
    cleared, the count, the TLS verification state, and the outcome. A
    de-provision row additionally carries the per-account success/failure
    breakdown, because that is what decides whether the marker flips.

    **No value, no fragment of a value, no masked tail of a value.** Everything
    in ``after_value`` is a name, an id, a count or a boolean.

    Best-effort like every other audit row in this subsystem — a journal failure
    must not turn a completed destination write into a reported failure — but
    loud: the row is the only trace that a secret moved at all, because under
    the harvest no human read it.
    """
    verb = "Provisioned" if action_type == PROVISION_ACTION_TYPE else "De-provisioned"
    description = (
        "%s provider credentials for sync target '%s' (id=%s) — %d account(s) "
        "written, %d failed, fields: %s; tls_verified=%s; actor=%s via %s; "
        "outcome=%s" % (
            verb,
            outcome.target_name,
            outcome.target_id,
            len(outcome.written),
            len(outcome.failed),
            ", ".join(outcome.field_names) or "none",
            outcome.tls_verified,
            actor or "unknown",
            surface,
            "success" if outcome.succeeded else "failed",
        )
    )
    try:
        journal.log_entry(
            category=PROVISION_JOURNAL_CATEGORY,
            action_type=action_type,
            entity_name=outcome.target_name,
            entity_id=outcome.target_id,
            description=description,
            after_value={
                "actor": actor,
                "surface": surface,
                "target_id": outcome.target_id,
                "target_name": outcome.target_name,
                "tls_verified": outcome.tls_verified,
                "fields": outcome.field_names,
                "accounts_written": len(outcome.written),
                "accounts_failed": len(outcome.failed),
                "succeeded": outcome.succeeded,
                "marker_set": outcome.marker_set,
                # The per-account breakdown. On a de-provision it is what
                # decides the marker, so it is the evidence the escape was real.
                "accounts": [
                    entry.as_audit_dict()
                    for entry in [*outcome.written, *outcome.failed]
                ],
                "unwritable_fields": outcome.unwritable_fields,
                "unmatched_accounts": outcome.unmatched,
                "schedules_direct_notes": outcome.schedules_direct_notes,
            },
            user_initiated=True,
        )
    except Exception as exc:  # pragma: no cover — journal best-effort
        logger.warning("[SYNC-PROVISION] Failed to journal %s: %s", action_type, exc)


# ---------------------------------------------------------------------------
# The action
# ---------------------------------------------------------------------------


async def _resolve_writes(
    *,
    client,
    clear: bool,
    schedules_direct_password: Optional[str],
) -> tuple[list[tuple[EntityType, Optional[int], str, dict]], list[str], list[str], list[str]]:
    """Pair A's provider records with B's and resolve what to write on each.

    Returns ``(plans, unwritable, unmatched, sd_notes)`` where each plan is
    ``(entity_type, destination_id, label, values)``. ``clear`` swaps the
    harvested value for ``""`` on exactly the same derived field set, which is
    what makes "the de-provision clears exactly the set the provision wrote"
    (INV-6) true by construction rather than by bookkeeping.
    """
    # Gather A's OWN records — the closed set, never whatever the gather returns.
    sections = await _gather_dispatcharr_sections(set(PROVISIONABLE_SECTIONS))
    redacted = _redact_sections(sections)

    def _rows(key: str, blob) -> list[dict]:
        rows = blob.get(key) if isinstance(blob, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    raw_accounts = _rows("m3u_accounts", sections)
    redacted_accounts = _rows("m3u_accounts", redacted)
    raw_sources = _rows("epg_sources", sections)
    redacted_sources = _rows("epg_sources", redacted)

    dest_accounts = _existing_by_name(await client.get_m3u_accounts() or [])
    dest_sources = _existing_by_identity(await client.get_epg_sources() or [])

    remap = IdRemapTable()
    plans: list[tuple[EntityType, Optional[int], str, dict]] = []
    unwritable: list[str] = []
    unmatched: list[str] = []

    for raw, red in zip(raw_accounts, redacted_accounts):
        label = _account_label(raw)
        writable, blocked = _partition_paths(m3u_credential_paths(red, remap))
        for name in blocked:
            if name not in unwritable:
                unwritable.append(name)
        if not writable:
            # Nothing the redactor named => nothing to provision. An HDHomeRun
            # tuner URL and a credential-free account land here correctly.
            continue
        existing = dest_accounts.get(_norm_name(raw.get("name")))
        if existing is None:
            unmatched.append(label)
            continue
        values = (
            {name: "" for name in writable}
            if clear
            else _harvest_values(raw, writable)
        )
        if not values:
            continue
        plans.append(
            (EntityType.M3U_ACCOUNT, existing.get("id"), label, values)
        )

    for raw, red in zip(raw_sources, redacted_sources):
        label = raw.get("name") or "<unnamed>"
        is_sd = raw.get("source_type") == SCHEDULES_DIRECT_SOURCE_TYPE
        writable, blocked = _partition_paths(epg_credential_paths(red))
        for name in blocked:
            if name not in unwritable:
                unwritable.append(name)
        existing = dest_sources.get(_identity_key(raw))
        values = (
            {name: "" for name in writable}
            if clear
            else _harvest_values(raw, writable)
        )
        if is_sd and schedules_direct_password and not clear:
            # Request-scoped, applied only to SD sources, never persisted here.
            values[SCHEDULES_DIRECT_PASSWORD_FIELD] = schedules_direct_password
        if is_sd and clear:
            # A de-provision must clear the SD password too when ECM wrote one;
            # it cannot READ the field to know, so it always clears it. Clearing
            # a field that was already unset is a no-op on the destination.
            values[SCHEDULES_DIRECT_PASSWORD_FIELD] = ""
        if not values:
            continue
        if existing is None:
            unmatched.append(label)
            continue
        plans.append((EntityType.EPG_SOURCE, existing.get("id"), label, values))

    notes = schedules_direct_notes(
        raw_sources, password_supplied=bool(schedules_direct_password) and not clear
    )
    return plans, unwritable, unmatched, notes


async def _run(
    *,
    session,
    sync_target,
    clear: bool,
    actor: Optional[str],
    surface: str,
    schedules_direct_password: Optional[str] = None,
) -> ProvisioningOutcome:
    """Shared core of provision / de-provision."""
    action_type = DEPROVISION_ACTION_TYPE if clear else PROVISION_ACTION_TYPE
    tls_verified = not bool(getattr(sync_target, "insecure", False))
    outcome = ProvisioningOutcome(
        action=action_type,
        target_id=getattr(sync_target, "id", None),
        target_name=getattr(sync_target, "name", None) or "",
        tls_verified=tls_verified,
        marker_set=getattr(sync_target, "credentials_provisioned_at", None) is not None,
    )
    if clear:
        outcome.residual_statement = DEPROVISION_RESIDUAL_STATEMENT

    client = make_remote_client(sync_target)
    try:
        plans, unwritable, unmatched, notes = await _resolve_writes(
            client=client,
            clear=clear,
            schedules_direct_password=schedules_direct_password,
        )
        outcome.unwritable_fields = unwritable
        outcome.unmatched = unmatched
        outcome.schedules_direct_notes = notes

        for entity_type, dest_id, label, values in plans:
            written = await _write_credentials(
                client=client,
                entity_type=entity_type,
                destination_id=dest_id,
                label=label,
                values=values,
            )
            (outcome.written if written.ok else outcome.failed).append(written)
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # pragma: no cover — teardown best-effort
                logger.debug("[SYNC-PROVISION] Remote client close failed", exc_info=True)

    # --- The marker (S11 / INV-9). -----------------------------------------
    #
    # PROVISION: set it when at least one account was written and none failed.
    # DE-PROVISION: clear it ONLY when the write succeeded for EVERY targeted
    # account. A partial or total failure leaves the marker SET, which leaves
    # ``insecure`` refused and names the accounts still holding a credential —
    # "the marker means B may still hold a credential", and a failed clear is
    # exactly that state. There is no "close enough".
    if outcome.succeeded and (outcome.written or clear):
        now = datetime.now(timezone.utc)
        if clear:
            sync_target.credentials_provisioned_at = None
            # A successful clear also retires the OBSERVED half: the fields ECM
            # just wrote back to unset are the ones the cycle observes. A later
            # cycle that still sees a credential on B re-stamps it, which is the
            # honest direction — the observation is re-derived, not remembered.
            sync_target.destination_credential_observed_at = None
            outcome.marker_set = False
        else:
            sync_target.credentials_provisioned_at = now
            outcome.marker_set = True
        if session is not None:
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[SYNC-PROVISION] Failed to persist provisioning marker: %s", exc
                )
                session.rollback()
                # The destination write happened; the marker did not. Report the
                # marker as it actually stands rather than as intended.
                outcome.marker_set = (
                    getattr(sync_target, "credentials_provisioned_at", None) is not None
                )

    _journal_provisioning(
        action_type=action_type, outcome=outcome, actor=actor, surface=surface
    )
    return outcome


async def provision_target_credentials(
    *,
    session,
    sync_target,
    actor: Optional[str] = None,
    surface: str = SURFACE_REST,
    schedules_direct_password: Optional[str] = None,
) -> ProvisioningOutcome:
    """Write A's provider credentials onto B's replicated provider accounts, once.

    S10. Operator-initiated, authenticated-admin, audited, TLS-verification
    mandatory. Re-running it IS the ratified rotation control (S12(a)): A
    re-reads its own current values and writes them again, and under the harvest
    it needs no input at all except an optional Schedules Direct password.

    **Scheduled or automatic re-push is FORBIDDEN under every ruling** (S12(c)),
    and is prevented structurally rather than by intent — see INV-2 in the module
    docstring.

    Raises:
        ProvisioningRefused: the target has ``insecure=true`` (S11).
    """
    reason = provision_refusal_reason(sync_target)
    if reason:
        raise ProvisioningRefused(reason)
    return await _run(
        session=session,
        sync_target=sync_target,
        clear=False,
        actor=actor,
        surface=surface,
        schedules_direct_password=schedules_direct_password,
    )


async def deprovision_target_credentials(
    *,
    session,
    sync_target,
    actor: Optional[str] = None,
    surface: str = SURFACE_REST,
) -> ProvisioningOutcome:
    """Clear the provisioned credential fields on B, and only then the marker.

    The de-provision escape the PO ratified on 2026-08-22, against the
    architect's recommendation of a permanent symmetric refusal. It is a
    first-class operation with a contract, because a cosmetic one would be worse
    than no escape at all: a local flag flip is A's BELIEF about B, falsifiable
    by an edit that changed nothing on B.

    Note this is deliberately NOT refused on an ``insecure`` target. An operator
    whose certificate broke must still be able to stop B re-authenticating, and
    refusing the de-provision would trap them in the state the gate exists to
    end.
    """
    return await _run(
        session=session,
        sync_target=sync_target,
        clear=True,
        actor=actor,
        surface=surface,
    )
