"""bead 9kwzp.7 — the admin-gate inventory, pinned.

WHAT THIS PINS AND WHY
----------------------

ECM has two admin gates that look interchangeable and are not:

* ``auth.RequireAdminIfEnabled`` — admin required, and the static MCP service
  principal IS ADMITTED, because ``_build_mcp_service_principal`` sets
  ``is_admin=True``.
* ``auth.RequireHumanAdminIfEnabled`` / ``auth.RequireHumanAdminForOutboundTest``
  / ``auth.RequireHumanAdminForServiceCredential`` /
  ``auth.RequireHumanAdminForTLSMaterial`` /
  ``auth.RequireHumanAdminForOutboundPolicy`` /
  ``auth.RequireHumanAdminForOutboundDestination`` /
  ``auth.RequireHumanAdminForNotificationCredential`` /
  ``auth.RequireHumanAdminForStatisticsReset`` — admin required AND the MCP
  service principal is refused. These eight behave identically; they differ
  only in the 403 body, which names the surface being refused so incident
  triage starts in the right place.

Every gate in both families no-ops while ``require_auth`` is false or setup is
incomplete, so nothing here is reachable-only-by-an-admin on a first-run or
auth-disabled instance. Read every verdict below with that condition attached.

Reaching for the first when you meant the second closes the non-admin half of
a hole and leaves the MCP half wide open, which reads as fixed in review. That
mistake is the entire subject of beads i4qrp, 9kwzp.6 and 9kwzp.7. This module
enumerates every admin-gated route by walking the live FastAPI dependency tree
and asserts the two sets EXACTLY, so a new route or a swapped dependency has to
be classified deliberately rather than inheriting whichever gate was copied.

THE RULE THE TWO SETS ENCODE
----------------------------

The static MCP key is a legitimate admin automation surface for ordinary
configuration and channel/stream work. It is NOT an operator identity. So it
is denied exactly where a route:

1. reaches the network carrying credentials the caller need not know, or to a
   host the caller names, and reports the upstream verdict back (the
   status-code oracle / in-band scanner class — i4qrp, 9kwzp.6, 9kwzp.7); or
2. rewrites the settings blob wholesale, which would bypass the field-level
   carve-out ``routers.settings._resolve_settings_admin`` applies to
   POST /api/settings (kgz3k / 6n76m); or
3. manages the lifecycle of the static MCP key itself, which would make the
   bearer of a credential the party that rotates and revokes it (9kwzp.8); or
4. manages the TLS certificate and private-key material, the DNS-provider
   credentials that issue it, or the HTTPS listener that serves it — operator
   transport infrastructure, which the sidecar exposes no tool for and whose
   destructive half has no undo (9kwzp.11); or
5. writes the outbound POLICY the routes in (1) are measured against, which is
   the fence rather than the probe (9kwzp.10 item 1); or
6. writes an outbound DESTINATION — a backup-upload target or a sync target —
   which repoints scheduled, credential-bearing traffic to a caller-named host
   and carries the TLS-verification flag it travels under (9kwzp.10 items 3
   and 4); or
7. reads or writes a notification credential, because the alert-method
   ``config`` blob holds the webhook URL, bot token and SMTP password in clear
   (9kwzp.10 item 4); or
8. irreversibly destroys operator data with no compensating write and no
   rollback ledger (9kwzp.12).

Everything else stays admitted. The groups below record that verdict per site
so the next reader does not re-derive it.

WHERE THIS INVENTORY DELIBERATELY ADMITS SOMETHING ARGUABLE
-----------------------------------------------------------

Two verdicts below were reached against a plausible case for denying, and are
recorded here rather than left implicit:

* ``_DESTINATION_READS`` — the list/get halves of the cloud-target and
  sync-target routers stay admitted while their write halves are denied. Their
  responses mask every credential to its last four characters, so they bound
  disclosure the way the write half does not bound redirection; and admitting
  them is what keeps the sidecar's inventory tools usable. This diverges from
  ``GET /api/tls/settings``, which IS denied despite masking, because that
  route additionally emits ``dns_zone_id`` and ``acme_email`` in clear and
  belongs to a router the sidecar has no tool for at all.
* ``_ALERT_METHOD_TYPES`` — a static catalogue of the method types this build
  supports. No install data, no stored value.

KNOWN GAP, DELIBERATELY NOT CHANGED HERE
----------------------------------------

The backup read/export paths (``_BACKUP_ARCHIVE``) emit
``discord_webhook_url`` and ``telegram_chat_id`` in clear — the very values
bead 9ej7f withheld from this same principal on GET /api/settings. That is
tracked as bead 9kwzp.9 and is pinned below as CURRENT behaviour, not as
correct.
"""
import pytest
from fastapi.routing import APIRoute

from auth import (
    RequireAdminIfEnabled,
    RequireHumanAdminForNotificationCredential,
    RequireHumanAdminForOutboundDestination,
    RequireHumanAdminForOutboundPolicy,
    RequireHumanAdminForOutboundTest,
    RequireHumanAdminForServiceCredential,
    RequireHumanAdminForStatisticsReset,
    RequireHumanAdminForTLSMaterial,
    RequireHumanAdminIfEnabled,
)


# ---------------------------------------------------------------------------
# Verdicts — MCP service principal ADMITTED
# ---------------------------------------------------------------------------

# Channel / stream automation. This is the MCP sidecar's declared purpose: the
# tools exist to create, merge, reorder and clean up channels and their
# streams. Nothing here reaches an outbound host the caller names, and nothing
# writes the settings blob. Admitting the principal is the whole point.
_CHANNEL_AUTOMATION = {
    ("POST", "/api/channels"),
    ("POST", "/api/channels/assign-numbers"),
    ("POST", "/api/channels/bulk-commit"),
    ("POST", "/api/channels/bulk-merge"),
    ("POST", "/api/channels/clear-auto-created"),
    ("POST", "/api/channels/import-csv"),
    ("POST", "/api/channels/logos"),
    ("POST", "/api/channels/logos/upload"),
    ("DELETE", "/api/channels/logos/{logo_id}"),
    ("PATCH", "/api/channels/logos/{logo_id}"),
    ("POST", "/api/channels/merge"),
    ("DELETE", "/api/channels/{channel_id}"),
    ("PATCH", "/api/channels/{channel_id}"),
    ("POST", "/api/channels/{channel_id}/add-stream"),
    ("POST", "/api/channels/{channel_id}/add-streams"),
    ("POST", "/api/channels/{channel_id}/remove-stream"),
    ("POST", "/api/channels/{channel_id}/reorder-streams"),
    ("POST", "/api/normalization/apply-to-channels"),
    ("POST", "/api/m3u/accounts/{account_id}/group-auto-sync-toggle"),
}

# Channel Pipeline rules and runs, under both the legacy /api/auto-creation
# prefix and the current /api/channel-pipeline one. Same verdict as above and
# for the same reason: this is channel automation, authored and driven by the
# automation credential by design. The destructive-looking members
# (rollback / restore-snapshot) undo a pipeline run against ECM's own state,
# which is the pipeline's own reversal mechanism, not a config wipe.
_PIPELINE_AUTOMATION = {
    (m, p)
    for prefix in ("/api/auto-creation", "/api/channel-pipeline")
    for m, p in (
        ("POST", f"{prefix}/event-sync-preview"),
        ("POST", f"{prefix}/executions/{{execution_id}}/restore-snapshot"),
        ("POST", f"{prefix}/executions/{{execution_id}}/rollback"),
        ("GET", f"{prefix}/fuzzy-preview"),
        ("POST", f"{prefix}/import/yaml"),
        ("POST", f"{prefix}/reset-circuit-breaker"),
        ("POST", f"{prefix}/rules"),
        ("POST", f"{prefix}/rules/bulk-update"),
        ("POST", f"{prefix}/rules/reorder"),
        ("DELETE", f"{prefix}/rules/{{rule_id}}"),
        ("PUT", f"{prefix}/rules/{{rule_id}}"),
        ("POST", f"{prefix}/rules/{{rule_id}}/duplicate"),
        ("POST", f"{prefix}/rules/{{rule_id}}/run"),
        ("POST", f"{prefix}/rules/{{rule_id}}/toggle"),
        ("POST", f"{prefix}/run"),
    )
}

# Dedup / event-sync review queues and the EPG guide migration. Operator
# review surfaces over ECM's own rows; accepting or rejecting one mutates
# channel state, which is squarely the automation credential's domain.
_REVIEW_QUEUES = {
    ("POST", "/api/channel-merges"),
    ("GET", "/api/channel-merges/candidates"),
    ("GET", "/api/channel-merges/snapshot"),
    ("POST", "/api/channel-merges/{merge_id}/accept"),
    ("POST", "/api/channel-merges/{merge_id}/dismiss"),
    ("POST", "/api/event-sync-exclusions"),
    ("DELETE", "/api/event-sync-exclusions/{exclusion_id}"),
    ("POST", "/api/event-sync-reviews/{review_id}/accept"),
    ("POST", "/api/event-sync-reviews/{review_id}/reject"),
    ("POST", "/api/epg/migration/apply"),
    ("GET", "/api/epg/migration/apply/{batch_id}"),
    ("POST", "/api/epg/migration/preview"),
}

# Borderline, resolved as admitted: this DOES reach out with the stored Emby
# key, but the host is the saved ``emby_base_url`` and never caller-named, the
# caller supplies no credential and learns no upstream status beyond its own
# job result, and the effect (Emby re-fetches its channel logos) is
# self-healing. It is logo maintenance, not a probe.
_EMBY_LOGO_MAINTENANCE = {
    ("POST", "/api/emby/clear-logos"),
}

# bead 9kwzp.6, decided on its own merits: restart-services takes the PLAIN
# admin gate. It names no host, carries no secret, echoes no upstream status,
# and rebuilds the tracker/prober from already-saved settings — the same work
# ``update_settings`` schedules for itself. What it was missing is the ordinary
# admin tier, which is what it now has.
_OPERATIONAL_RESTART = {
    ("POST", "/api/settings/restart-services"),
}

# BACKLOG CANDIDATE, not pinned as correct — pinned as CURRENT (bead 9kwzp.9).
# The archive these emit carries ``discord_webhook_url`` and
# ``telegram_chat_id`` in clear (``routers.backup._gather_settings`` redacts
# only ``_SETTINGS_CREDENTIAL_FIELDS``, which does not include them), so the
# MCP principal can read through this path the exact values bead 9ej7f
# withholds from it on GET /api/settings.
#
# The restore-dbas pair used to be listed here too. Bead 9kwzp.10 item 2 moved
# both to ``_WHOLESALE_CONFIG_WRITE``; see that group for why.
_BACKUP_ARCHIVE = {
    ("GET", "/api/backup/create"),
    ("GET", "/api/backup/export"),
    ("GET", "/api/backup/export-sections"),
    ("GET", "/api/backup/saved"),
    ("GET", "/api/backup/saved/{filename}"),
    ("DELETE", "/api/backup/saved/{filename}"),
    ("POST", "/api/backup/save"),
    ("POST", "/api/backup/validate"),
}

# bead 9kwzp.10 items 3 and 4, decided against a plausible case for denying.
# The READ halves of the two outbound-destination routers stay admitted: every
# credential in these responses is masked to its last four characters
# (``_mask_credentials`` in both routers), so no stored secret is recoverable
# through them, and admitting them is what keeps the sidecar's
# ``list_cloud_targets`` / ``list_sync_targets`` inventory tools working. The
# WRITE halves are in ``_OUTBOUND_DESTINATION_WRITE`` below, because masking
# bounds disclosure and says nothing about redirection.
_DESTINATION_READS = {
    ("GET", "/api/cloud-targets"),
    ("GET", "/api/sync-targets"),
    ("GET", "/api/sync-targets/{target_id}"),
}

# bead 9kwzp.10 item 4. The one route in ``/api/alert-methods`` that is not
# credential-bearing: a static catalogue of the method types this build
# supports and their field descriptors. No install data, no stored value.
_ALERT_METHOD_TYPES = {
    ("GET", "/api/alert-methods/types"),
}

# bead 9kwzp.11, decided on their own merits: the two TLS status reads take the
# PLAIN admin tier. Neither returns credential material — ``/status`` returns
# the certificate's subject, issuer and validity window (which every TLS client
# is served anyway) plus domain, port and a running flag, and ``/https/status``
# returns only the running flag and the port. The rest of that router is in
# ``_TLS_MATERIAL_LIFECYCLE`` below; GET /api/tls/settings is deliberately NOT
# here because it emits masked credential fragments.
_TLS_STATUS_READS = {
    ("GET", "/api/tls/status"),
    ("GET", "/api/tls/https/status"),
}

MCP_ADMITTED: frozenset = frozenset(
    _CHANNEL_AUTOMATION
    | _PIPELINE_AUTOMATION
    | _REVIEW_QUEUES
    | _EMBY_LOGO_MAINTENANCE
    | _OPERATIONAL_RESTART
    | _BACKUP_ARCHIVE
    | _DESTINATION_READS
    | _ALERT_METHOD_TYPES
    | _TLS_STATUS_READS
)


# ---------------------------------------------------------------------------
# Verdicts — MCP service principal DENIED
# ---------------------------------------------------------------------------

# kgz3k / 6n76m: restore rewrites the settings blob wholesale, which would let
# the automation credential flip every admin-only field in one call and bypass
# the field-level gate on POST /api/settings.
#
# bead 9kwzp.10 item 2 added the restore-dbas pair, and the reason it was not
# already here is worth keeping. When 6n76m drew this line, a DBAS restore
# applied denylist-filtered settings to the DISPATCHARR upstream and never
# touched ECM's own settings.json — 6n76m's changelog entry names it as
# deliberately unchanged for exactly that reason, so the plain admin tier was
# CORRECT as written. Bead …-dfkbn item 4 then added
# ``dbas/importers/ecm_settings.py``, and the DBAS restore now writes ECM's own
# blob, excluding only the live Dispatcharr connection, install-local
# bookkeeping and redaction sentinels — not the media-server base URLs, the
# notification credentials, the GH #473 safety caps or ``ssrf_outbound_mode``.
# The gate went stale when the capability grew. That is a failure mode this
# inventory cannot catch on its own: nothing about the ROUTE changed.
_WHOLESALE_CONFIG_WRITE = {
    ("POST", "/api/backup/restore"),
    ("POST", "/api/backup/restore-saved"),
    ("POST", "/api/backup/restore-yaml"),
    ("POST", "/api/backup/restore-dbas"),
    ("POST", "/api/backup/restore-dbas-saved"),
}

# bead 9kwzp.10 item 1: the outbound POLICY write. ``ssrf_outbound_mode``
# decides which hosts every outbound path in ECM may reach, and this is its
# only field-specific writer — POST /api/settings carries the stored value
# forward untouched, though the wholesale-restore paths above can persist it
# without any source-level assignment, which is one more reason they are here
# too. Gating the eleven sinks in ``_OUTBOUND_CREDENTIAL_TEST`` while leaving
# this writable by the same principal was a partial control: it could not
# drive the probe but it could move the fence the probe was measured against.
# The always-on denylist (link-local / IMDS / ULA / CGNAT / multicast) is not
# operator-togglable and is unaffected either way.
_OUTBOUND_POLICY_WRITE = {
    ("PATCH", "/api/settings/security"),
}

# bead 9kwzp.10 items 3 and 4: the WRITE halves of the two outbound-destination
# routers. A cloud target is where ``tasks/dbas_backup.py`` PUTs the operator's
# archive; a sync target is the remote instance ``tasks/dbas_sync.py`` pushes
# config to on a timer, and creating one registers its ``dbas_sync_<id>`` task.
# Both accept a caller-named host, store the credentials the job authenticates
# with, and expose ``insecure``, which turns off TLS verification for that
# traffic. Updating either repoints a flow the operator already configured.
# That is the kgz3k shape — rewriting an outbound base URL a background job
# will then contact — deferred onto a schedule, which makes it quieter rather
# than safer: no operator sees a result and the redirect repeats every cycle.
#
# Bead jcj0f DID ship create/update/delete for both as MCP tools, and those
# three tools now receive a clean 403 on an auth-enabled instance. That is the
# deliberate outcome. A deliberately-exposed tool establishes product intent,
# not least privilege, and the encryption-at-rest plus last-4 masking these
# routers are documented around bounds DISCLOSURE of a stored credential — not
# redirection, not replacement, not the TLS downgrade. The list/get halves stay
# admitted; see ``_DESTINATION_READS``.
_OUTBOUND_DESTINATION_WRITE = {
    ("POST", "/api/cloud-targets"),
    ("PATCH", "/api/cloud-targets/{target_id}"),
    ("DELETE", "/api/cloud-targets/{target_id}"),
    ("POST", "/api/sync-targets"),
    ("PUT", "/api/sync-targets/{target_id}"),
    ("DELETE", "/api/sync-targets/{target_id}"),
}

# bead 9kwzp.10 item 4: ``/api/alert-methods``, which carried NO route
# dependency on any of its six non-test routes — reachable by any
# authenticated non-admin AND by this principal.
#
# The READ half is the sharper one, and is why these are denied rather than
# merely admin-gated. ``list_alert_methods`` and ``get_alert_method`` return
# ``AlertMethod.config`` verbatim with no masking of any kind, and that blob is
# where the Discord webhook URL, the Telegram bot token and the SMTP password
# live. Those are the exact three families bead 9ej7f withheld from this
# principal on GET /api/settings and kgz3k denies it on the settings WRITE,
# handed out in clear through a second table. A field you may not write, you
# may not read.
#
# The sidecar's ``list_alert_methods`` tool therefore now receives a 403;
# ``test_alert_method`` already did (9kwzp.6). The response bodies are
# UNCHANGED — the absence of a masking layer on ``config`` is a separate
# concern and is not addressed by this authorization fix.
_NOTIFICATION_CREDENTIAL = {
    ("GET", "/api/alert-methods"),
    ("POST", "/api/alert-methods"),
    ("GET", "/api/alert-methods/{method_id}"),
    ("PATCH", "/api/alert-methods/{method_id}"),
    ("DELETE", "/api/alert-methods/{method_id}"),
}

# bead 9kwzp.12: POST /api/settings/reset-stats carried no dependency at all
# and deletes every row of seven statistics tables.
#
# Decided on its own merits rather than by copying the sibling it was split
# from. ``restart-services`` (in ``_OPERATIONAL_RESTART`` above) kept the plain
# admin tier because it rebuilds background services from already-saved
# settings — work a settings write schedules for itself, so denying it would
# deny a restart the principal can already trigger indirectly. Nothing about
# reset-stats is recoverable that way: the seven tables are the operator's own
# watch, bandwidth, popularity, telemetry and client-connection history, there
# is no compensating write, no rollback ledger, and no other route re-derives
# them. Note the contrast with the pipeline rollback/restore-snapshot routes,
# which ARE admitted precisely because they are the pipeline's own reversal
# mechanism. An automation credential that can silently erase the observability
# record is one that can erase the evidence of its own activity.
_DESTRUCTIVE_DATA_RESET = {
    ("POST", "/api/settings/reset-stats"),
}

# i4qrp / 9kwzp.6 / 9kwzp.7: every one of these reaches the network with
# operator-supplied or STORED credentials and reports the upstream verdict
# back. That is a status-code oracle and an in-band port scanner, and for the
# stored-credential members it is a send the caller never had to know a secret
# to perform.
_OUTBOUND_CREDENTIAL_TEST = {
    ("POST", "/api/settings/test"),
    ("POST", "/api/settings/test-smtp"),
    ("POST", "/api/settings/test-discord"),
    ("POST", "/api/settings/test-telegram"),
    ("POST", "/api/settings/emby/test-connection"),
    ("POST", "/api/settings/plex/test-connection"),
    ("POST", "/api/settings/jellyfin/test-connection"),
    ("POST", "/api/alert-methods/{method_id}/test"),
    ("POST", "/api/m3u/digest/test"),
    ("POST", "/api/cloud-targets/test"),
    ("POST", "/api/cloud-targets/{target_id}/test"),
    # 9kwzp.11: the only member of the /api/tls router that is this shape. It
    # hands DNS-provider credentials to the provider API and reports the
    # verdict back, and enumerates the operator's zones on the way. It reuses
    # ``RequireHumanAdminForOutboundTest`` rather than the TLS gate so its 403
    # reads like the eleven sinks above.
    ("POST", "/api/tls/test-dns-provider"),
}

# 9kwzp.8: the static MCP key's own lifecycle. Both halves carried NO
# dependency at all, so any authenticated non-admin could mint itself a
# credential the middleware treats as admin (privilege escalation) or revoke
# every sidecar integration (denial of service). The MCP principal is refused
# on top of that for a reason unlike either group above — nothing here reaches
# the network and nothing writes the settings blob wholesale. It is refused
# because it would be the BEARER rotating and revoking its own credential: the
# minted key is disclosed only in the response body, so a holder of a leaked
# key could mint a successor that survives the operator's rotation. Hence its
# own dependency, ``RequireHumanAdminForServiceCredential``, whose 403 names
# this surface instead of a connection test that never happens here.
_SERVICE_CREDENTIAL_LIFECYCLE = {
    ("POST", "/api/settings/mcp-api-key"),
    ("DELETE", "/api/settings/mcp-api-key"),
}

# 9kwzp.11: the /api/tls router carried NO route dependency on ANY of its
# thirteen routes, so all of them were reachable by any authenticated non-admin
# and by this principal. These nine are the certificate/private-key material and
# HTTPS-termination lifecycle: ``upload-cert`` accepts caller-supplied key
# material and serves it, ``DELETE /certificate`` destroys the operator's own
# with no undo, ``configure`` writes the DNS-provider credentials that bead
# 2owpi records as plaintext on disk, the ACME trio issues and replaces the live
# key pair, the https trio is availability of the operator's transport security,
# and ``GET /settings`` emits the last four characters of three stored
# credentials plus ``dns_zone_id`` in clear — the class bead 9ej7f withheld from
# this principal on GET /api/settings.
#
# Its own dependency, ``RequireHumanAdminForTLSMaterial``, for the reason
# 9kwzp.8 needed one: reusing any of the three existing bodies would name a
# backup restore, an MCP key rotation or a connection test that these routes
# never perform, and send triage of the refusal to the wrong subsystem.
_TLS_MATERIAL_LIFECYCLE = {
    ("GET", "/api/tls/settings"),
    ("POST", "/api/tls/configure"),
    ("POST", "/api/tls/request-cert"),
    ("POST", "/api/tls/complete-challenge"),
    ("POST", "/api/tls/upload-cert"),
    ("POST", "/api/tls/renew"),
    ("POST", "/api/tls/https/start"),
    ("POST", "/api/tls/https/stop"),
    ("POST", "/api/tls/https/restart"),
    ("DELETE", "/api/tls/certificate"),
}

MCP_DENIED: frozenset = frozenset(
    _WHOLESALE_CONFIG_WRITE
    | _OUTBOUND_CREDENTIAL_TEST
    | _SERVICE_CREDENTIAL_LIFECYCLE
    | _TLS_MATERIAL_LIFECYCLE
    | _OUTBOUND_POLICY_WRITE
    | _OUTBOUND_DESTINATION_WRITE
    | _NOTIFICATION_CREDENTIAL
    | _DESTRUCTIVE_DATA_RESET
)


# ---------------------------------------------------------------------------
# Live inventory
# ---------------------------------------------------------------------------

_CHECK_ADMIN_QUALNAME = "require_admin_if_enabled.<locals>.check_admin"


def _gate_kind(call):
    """Classify one dependency callable, or return None if it is not a gate.

    ``require_admin_if_enabled`` builds every admin gate as the same closure;
    the two variants differ only in the captured ``reject_mcp_service_principal``
    flag, which is exactly the distinction this module exists to police, so we
    read it off the closure rather than comparing ``Depends`` identities (a new
    call site could build its own).
    """
    if getattr(call, "__qualname__", "") != _CHECK_ADMIN_QUALNAME:
        return None
    captured = dict(
        zip(
            call.__code__.co_freevars,
            (cell.cell_contents for cell in call.__closure__ or ()),
        )
    )
    return "denied" if captured.get("reject_mcp_service_principal") else "admitted"


def _mcp_denial_detail(call) -> str:
    """Read the 403 body one gate closure was built with."""
    captured = dict(
        zip(
            call.__code__.co_freevars,
            (cell.cell_contents for cell in call.__closure__ or ()),
        )
    )
    return captured["mcp_denial_detail"]


def _walk(dependant):
    kinds = set()
    kind = _gate_kind(dependant.call)
    if kind:
        kinds.add(kind)
    for sub in dependant.dependencies:
        kinds |= _walk(sub)
    return kinds


def _inventory():
    """Return ``{"admitted": {...}, "denied": {...}}`` of (METHOD, path)."""
    from main import app

    found = {"admitted": set(), "denied": set()}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for kind in _walk(route.dependant):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                found[kind].add((method, route.path))
    return found


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_classifier_distinguishes_the_two_gates():
    """Guard against a vacuous inventory.

    If ``_gate_kind`` stopped telling the two gates apart, the set assertions
    below would still pass whenever both sets happened to be classified the
    same way. Pin the classifier against the nine shipped dependencies first.
    """
    assert _gate_kind(RequireAdminIfEnabled.dependency) == "admitted"
    assert _gate_kind(RequireHumanAdminIfEnabled.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForOutboundTest.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForServiceCredential.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForTLSMaterial.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForOutboundPolicy.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForOutboundDestination.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForNotificationCredential.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForStatisticsReset.dependency) == "denied"


def test_every_human_admin_gate_names_its_own_surface():
    """No two human-admin gates may share a 403 body.

    The eight denial gates behave identically; the ONLY thing that
    distinguishes them is the operator-facing message, which exists so a
    refusal points triage at the right subsystem. A copy-paste that reused a
    neighbour's body would leave every behavioural test in the suite green
    while sending every incident the wrong way.
    """
    details = [
        _mcp_denial_detail(dep.dependency)
        for dep in (
            RequireHumanAdminIfEnabled,
            RequireHumanAdminForOutboundTest,
            RequireHumanAdminForServiceCredential,
            RequireHumanAdminForTLSMaterial,
            RequireHumanAdminForOutboundPolicy,
            RequireHumanAdminForOutboundDestination,
            RequireHumanAdminForNotificationCredential,
            RequireHumanAdminForStatisticsReset,
        )
    ]
    assert len(set(details)) == len(details)
    # Every one of them names the principal, so a caller can tell this refusal
    # apart from a plain non-admin one.
    assert all("MCP service principal" in detail for detail in details)


def test_no_route_is_classified_both_ways():
    inventory = _inventory()
    assert not (inventory["admitted"] & inventory["denied"])


def test_mcp_denied_routes_match_the_recorded_verdicts():
    """Every route that refuses the MCP service principal, and only those.

    A route dropping out of this set means a credential-carrying outbound sink
    or a wholesale config write just became reachable by the automation
    credential. A route appearing that is not listed means someone gated
    something without recording why — add it to the group that matches the
    rule in the module docstring.
    """
    denied = _inventory()["denied"]
    assert denied == MCP_DENIED, {
        "unexpectedly denied": sorted(denied - MCP_DENIED),
        "no longer denied": sorted(MCP_DENIED - denied),
    }


def test_mcp_admitted_routes_match_the_recorded_verdicts():
    """Every admin route that still ADMITS the MCP service principal.

    New entries are not automatically wrong — most admin routes belong here —
    but each one has to be filed under a group whose comment says why
    admitting an automation credential is intended for that route.
    """
    admitted = _inventory()["admitted"]
    assert admitted == MCP_ADMITTED, {
        "newly admitted": sorted(admitted - MCP_ADMITTED),
        "no longer admitted": sorted(MCP_ADMITTED - admitted),
    }


@pytest.mark.parametrize(
    "method,path",
    sorted(_OUTBOUND_CREDENTIAL_TEST),
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_every_known_outbound_test_sink_denies_mcp(method, path):
    """Named restatement of the sink list, so a failure reads as a route name
    rather than a set diff."""
    assert (method, path) in _inventory()["denied"]
