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
  ``auth.RequireHumanAdminForNotificationCredential`` /
  ``auth.RequireHumanAdminForStatisticsReset`` — admin required AND the MCP
  service principal is refused. These seven behave identically; they differ
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
6. writes a notification credential, or reads a SINGLE one, because the
   alert-method ``config`` blob holds the webhook URL, bot token and SMTP
   password in clear (9kwzp.10 item 4, as amended — see the residual note
   below, because the LIST read is admitted and discloses the same blob); or
7. irreversibly destroys operator data with no compensating write and no
   rollback ledger (9kwzp.12).

Everything else stays admitted. The groups below record that verdict per site
so the next reader does not re-derive it.

RULE (6) HAS A DELIBERATE EXCEPTION, AND ONE RULE WAS WITHDRAWN
---------------------------------------------------------------

This bead originally added an eighth rule — "writes an outbound DESTINATION (a
backup-upload target or a sync target), which repoints scheduled,
credential-bearing traffic to a caller-named host and carries the
TLS-verification flag it travels under" — and a
``RequireHumanAdminForOutboundDestination`` gate implementing it over the write
halves of ``/api/cloud-targets`` and ``/api/sync-targets``. The PO WITHDREW it
before it shipped, and the gate is gone from ``auth/dependencies.py``.

The reason is capability, not a change of view on the risk: bead jcj0f ships
six MCP tools over exactly those six routes (``create`` / ``update`` /
``delete`` for each router), and the denial broke all six. Both routers now run
on plain ``RequireAdminIfEnabled`` end to end; see ``_DESTINATION_CRUD``.

Rule (6) has the same shape of exception for the same reason: ``GET
/api/alert-methods`` is admitted because the ``list_alert_methods`` tool needs
it, even though it discloses unmasked credentials. See ``_ALERT_METHOD_LIST``,
which is the single most important comment in this module for a reader
deciding whether a plain gate means "harmless".

WHERE THIS INVENTORY DELIBERATELY ADMITS SOMETHING ARGUABLE
-----------------------------------------------------------

Four verdicts below were reached against a plausible case for denying, and are
recorded here rather than left implicit:

* ``_ALERT_METHOD_LIST`` — ``GET /api/alert-methods`` is ADMITTED and returns
  ``AlertMethod.config`` UNREDACTED. This is the sharpest residual in the whole
  inventory and it must not be inferred from the bare gate name. Read that
  group's comment before touching anything in ``/api/alert-methods``.
* ``_DESTINATION_CRUD`` — the cloud-target and sync-target routers are admitted
  END TO END, reads and writes. Do NOT read this as "masked, therefore
  harmless". The reads disclose the destination ``base_url`` /
  ``upload_path``, the ``insecure`` TLS-verification flag, the NAMES of the
  credential keys, each credential's last four characters,
  ``credential_version`` and revocation state, and the outcome of past syncs —
  network topology plus credential fingerprints. The writes do more than that:
  they repoint where a scheduled job sends the operator's data and under what
  TLS posture. What the masking buys is narrow and specific: no stored secret
  VALUE can be reconstructed from a read, so a read alone cannot authenticate
  to the destination. It buys nothing at all against a write.

  The verdict is a PRODUCT JUDGEMENT that six shipped MCP tools are worth that
  residual, not a claim that the residual is nil.

  It diverges from ``GET /api/tls/settings``, which IS denied despite masking,
  on two counts: that route additionally emits ``dns_zone_id`` and
  ``acme_email`` in clear, and the sidecar has no TLS tool at all, so denying
  it costs nothing.
* ``_ALERT_METHOD_TYPES`` — a static catalogue of the method types this build
  supports. No install data, no stored value. The one unarguable member of
  this list.
* ``_DBAS_PREVIEW_ADMITTED_APPLY_DENIED_IN_HANDLER`` — see its own comment.

The through-line of the first two: on this surface the MCP principal is denied
where denying it costs no shipped tool, and admitted where it would break one.
That is the rule actually in force. It is not the same rule as "denied wherever
a credential is reachable", and a reader who assumes the latter will
misread every entry in ``MCP_ADMITTED``.

WHERE THIS INVENTORY IS DELIBERATELY COARSER THAN ITS OWN PROSE
---------------------------------------------------------------

The rule above describes the DANGEROUS member of each denied group, and the
gates are coarser than that description. Stated plainly rather than left for a
reader to discover:

* A display-name or severity-filter change on an alert method is denied
  exactly like a credential replacement.
* TIGHTENING ``ssrf_outbound_mode`` from ``lan_friendly`` to ``public_only`` is
  denied exactly like widening it, even though it can only shrink what the
  outbound sinks may reach.

That is a deliberate trade, not an oversight. A gate that branches on which
FIELDS a request changes is the ``routers.settings._resolve_settings_admin``
shape, and that machinery exists because ``POST /api/settings`` mixes admin
configuration with per-user display preferences in one blob and could not be
split. None of these routes has that problem: nothing on them is a per-user
preference, so the entire route is admin work and a coarse gate costs an
automation credential nothing it had a legitimate claim to. Coarse also fails
in the safe direction and is far easier to verify — the one place this bead
did branch, the DBAS ``confirm_apply`` split, needed six dedicated cases to
pin. Add field-level branching here only when a concrete caller needs it.

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

# bead 9kwzp.10 items 3 and 4, decided against a plausible case for denying,
# and NOT on the grounds that any of it is harmless.
#
# BEFORE THIS BEAD: ``/api/cloud-targets`` had no route dependency at all on
# its four CRUD routes, so any authenticated non-admin reached them;
# ``/api/sync-targets`` was already admin-gated on all five. What this bead
# fixes is the cloud-target half — every route in both routers now requires
# admin.
#
# THE MCP PRINCIPAL IS ADMITTED ON ALL NINE, WRITES INCLUDED. This bead first
# denied the six writes via a ``RequireHumanAdminForOutboundDestination`` gate;
# the PO withdrew that before it shipped and the gate no longer exists. The
# reason is capability: bead jcj0f ships ``create_cloud_target``,
# ``update_cloud_target``, ``delete_cloud_target``, ``create_sync_target``,
# ``update_sync_target`` and ``delete_sync_target`` as MCP tools over exactly
# these six routes, and the denial returned 403 to all six.
#
# The residual that buys, stated so nobody has to re-derive it. READS disclose
# the destination URL, the ``insecure`` flag, credential key names and last-4,
# credential version and revocation state, and past sync outcomes — network
# topology plus credential fingerprints. WRITES do materially more: a cloud
# target is where ``tasks/dbas_backup.py`` PUTs the operator's archive, a sync
# target is the remote instance ``tasks/dbas_sync.py`` pushes config to on a
# timer (creating one registers its ``dbas_sync_<id>`` task), and both carry
# ``insecure``, which turns off TLS verification for that traffic. So the
# principal can repoint scheduled, credential-bearing outbound traffic to a
# host it names. ``routers/sync_targets.py`` is explicit that the
# authoritative SSRF check runs at EXECUTE time, not at write time.
#
# What ``_mask_credentials`` buys is only that no stored secret VALUE is
# recoverable from a read; it buys nothing against a write. What still holds
# the line is that the caller must be an admin, that the outbound POLICY write
# stays denied (``_OUTBOUND_POLICY_WRITE``), and that both cloud-target
# ``/test`` verbs stay denied (``_OUTBOUND_CREDENTIAL_TEST``).
#
# Bring the gate back if these tools are withdrawn.
_DESTINATION_CRUD = {
    ("GET", "/api/cloud-targets"),
    ("POST", "/api/cloud-targets"),
    ("PATCH", "/api/cloud-targets/{target_id}"),
    ("DELETE", "/api/cloud-targets/{target_id}"),
    ("GET", "/api/sync-targets"),
    ("GET", "/api/sync-targets/{target_id}"),
    ("POST", "/api/sync-targets"),
    ("PUT", "/api/sync-targets/{target_id}"),
    ("DELETE", "/api/sync-targets/{target_id}"),
}

# bead 9kwzp.10 item 4. The one route in ``/api/alert-methods`` that is not
# credential-bearing: a static catalogue of the method types this build
# supports and their field descriptors. No install data, no stored value.
_ALERT_METHOD_TYPES = {
    ("GET", "/api/alert-methods/types"),
}

# ===========================================================================
# STOP. THIS ROUTE DISCLOSES UNREDACTED CREDENTIALS TO THE MCP PRINCIPAL.
# ===========================================================================
# bead 9kwzp.10 item 4, amended by the PO. Do not read the plain
# ``RequireAdminIfEnabled`` on ``routers/alert_methods.py::list_alert_methods``
# and conclude the route is uninteresting. It is the opposite.
#
# WHAT IT DISCLOSES. The handler hand-rolls its response dict and emits
# ``"config": json.loads(m.config)`` for every configured method, with NO
# masking of any kind. That blob is where the Discord webhook URL, the Telegram
# bot token and the SMTP password live. Those are the exact three families bead
# 9ej7f withheld from this same principal on ``GET /api/settings``, so as long
# as this route is admitted, that redaction is reachable around. Note this also
# makes the human-admin gate on ``GET /api/alert-methods/{method_id}`` (in
# ``_NOTIFICATION_CREDENTIAL``) disclosure-vacuous against the MCP principal:
# the list returns the same fields for every method, so denying the single-read
# withholds nothing the list does not already hand over. That gate is held
# because no tool needs the route, not because it is containing anything.
#
# WHY IT IS ADMITTED ANYWAY. The shipped ``list_alert_methods`` MCP tool is the
# operator's inventory of their own alert methods and calls exactly this route.
# This bead denied it; the PO reversed that, accepting the disclosure, because
# refusing it removed a capability the sidecar was built to provide.
#
# WHAT ACTUALLY FIXES IT — and this is the point of recording it here rather
# than leaving it to the gate name. The right fix is the RESPONSE, not the
# gate. ``models.AlertMethod.to_dict(include_sensitive=False)`` already builds
# a masked ``config`` and substitutes ``********`` for sensitive keys; this
# handler simply never calls it. That is bead
# enhancedchannelmanager-9kwzp.13 (P1, open). Closing it removes this residual
# without touching this dependency and without taking the tool away — which is
# why the authorization verdict here is not the place to fight it.
#
# If you are here because you are about to widen this router, the question to
# ask is whether 9kwzp.13 has landed yet.
_ALERT_METHOD_LIST = {
    ("GET", "/api/alert-methods"),
}

# READ THIS ONE CAREFULLY: the route dependency admits the principal, and the
# HANDLER refuses half of what it does. The inventory classifies by route
# dependency, so this entry would otherwise read as a plain admitted route and
# understate the contract.
#
# bead 9kwzp.10 item 2, as revised by the PR #855 review.
# ``POST /api/backup/restore-dbas-saved`` is split by ``confirm_apply``:
#
#   confirm_apply=false  -> counts-only PREVIEW, admitted.
#   confirm_apply=true   -> APPLY, refused in the handler with
#                           ``routers.backup._DBAS_APPLY_MCP_DENIAL``.
#
# The reason the blanket denial was wrong here: the justification for
# re-tiering the DBAS routes is that bead …-dfkbn item 4 taught them to write
# ECM's settings blob wholesale, and that reaches the apply, not a run that
# writes nothing. ``dbas.restore_orchestrator`` forces
# ``report.is_dry_run = True`` whenever ``confirm_apply`` is false and calls
# that the single choke point a caller can never opt out of, so the
# zero-mutation property is structural. This route also names an artifact
# ALREADY on disk, which only an admin could have saved there, and the
# sidecar's ``restore_dbas_backup_saved`` tool documents the preview as its
# primary safe mode. Denying it would have been an unrelated capability
# removal.
#
# Its upload sibling ``POST /restore-dbas`` is NOT split and stays wholly in
# ``_WHOLESALE_CONFIG_WRITE``: it takes a caller-supplied artifact that is
# streamed to disk and decoded before anything reads ``confirm_apply``, and no
# MCP tool exists for it.
#
# The apply refusal is proved in
# ``tests/routers/test_9kwzp10_12_gate_verdicts.py``, NOT here: this module
# only walks dependencies.
_DBAS_PREVIEW_ADMITTED_APPLY_DENIED_IN_HANDLER = {
    ("POST", "/api/backup/restore-dbas-saved"),
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
    | _DESTINATION_CRUD
    | _ALERT_METHOD_TYPES
    | _ALERT_METHOD_LIST
    | _DBAS_PREVIEW_ADMITTED_APPLY_DENIED_IN_HANDLER
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
#
# COARSE ON PURPOSE: TIGHTENING the mode from lan_friendly to public_only is
# denied exactly like widening it, even though it can only shrink what the
# sinks may reach. One closed enum with one writer is not worth a
# direction-aware gate.
_OUTBOUND_POLICY_WRITE = {
    ("PATCH", "/api/settings/security"),
}

# bead 9kwzp.10 item 4: the four routes of ``/api/alert-methods`` that NO MCP
# tool calls. The router carried NO route dependency on any of its six non-test
# routes, so every one of them was reachable by any authenticated non-admin AND
# by this principal.
#
# An alert method holds the Discord webhook URL, the Telegram bot token and the
# SMTP password in ``AlertMethod.config``. The three WRITES here can repoint
# where ECM's own alerts go, or end them — that is the kgz3k shape, and the
# reason for denying is the same one that denies ``POST /api/settings``. The
# single READ, ``GET /{method_id}``, returns that blob verbatim.
#
# BE PRECISE ABOUT WHAT THE READ HALF OF THIS GATE ACHIEVES TODAY: nothing,
# against the MCP principal. ``GET /api/alert-methods`` is ADMITTED (see
# ``_ALERT_METHOD_LIST``) and returns the same unmasked ``config`` for EVERY
# method, so the principal can read through the list exactly what this gate
# withholds on the single-method route. ``GET /{method_id}`` is kept here
# because no tool needs it and the group is coherent, not because it is
# containing a disclosure. Bead enhancedchannelmanager-9kwzp.13 is what
# contains it, by masking ``config`` in both read responses.
#
# ``test_alert_method`` was denied separately by 9kwzp.6 and lives in
# ``_OUTBOUND_CREDENTIAL_TEST``. The response bodies are UNCHANGED — this bead
# adds authorization, not masking.
#
# COARSE ON PURPOSE: a display-name or severity-filter change is denied
# exactly like a credential replacement.
_NOTIFICATION_CREDENTIAL = {
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
    same way. Pin the classifier against the eight shipped dependencies first.
    """
    assert _gate_kind(RequireAdminIfEnabled.dependency) == "admitted"
    assert _gate_kind(RequireHumanAdminIfEnabled.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForOutboundTest.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForServiceCredential.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForTLSMaterial.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForOutboundPolicy.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForNotificationCredential.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForStatisticsReset.dependency) == "denied"


def test_every_human_admin_gate_names_its_own_surface():
    """No two human-admin gates may share a 403 body.

    The seven denial gates behave identically; the ONLY thing that
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
