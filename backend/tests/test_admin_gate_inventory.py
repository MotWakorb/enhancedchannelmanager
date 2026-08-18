"""bead 9kwzp.7 — the admin-gate inventory, pinned.

WHAT THIS PINS AND WHY
----------------------

04c0u.4 AUTHORITY LAYER: this inventory describes only the dependencies
attached to FastAPI routes. It is not the MCP service principal's effective
authority. ``auth.mcp_capabilities`` is the deny-by-default outer boundary;
the global auth middleware applies that explicit method+route matrix before a
request can reach any dependency classified here. An entry called ADMITTED
below therefore means "the route's legacy admin dependency admits it", not
"the MCP key can reach the handler". This distinction preserves the useful
dependency drift audit without turning its historical classifications into a
second, conflicting authority source.

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

MOST gates in both families no-op while ``require_auth`` is false or setup is
incomplete, so most of what follows is not reachable-only-by-an-admin on a
first-run or auth-disabled instance. Read every verdict below with that
condition attached — EXCEPT for the three named next.

THREE GATES NO LONGER NO-OP WHEN AUTH IS DISABLED (beads jy006, 2u4e0)
-----------------------------------------------------------------------

This paragraph read "every gate in both families no-ops…" until bead jy006, and
that is now wrong for ``RequireHumanAdminForServiceCredential``,
``RequireHumanAdminForTLSMaterial`` and (since bead 2u4e0, 2026-08-15)
``RequireHumanAdminForOutboundTest``. All three carry
``enforce_when_auth_disabled=True``: on an instance that HAS an operator
identity (a user row, or ``setup_complete``), they require a real human admin
even while ``require_auth`` is false. On an instance with none, they still
no-op, so no first-run or headless deployment is locked out.

Two axes, both different from the seven MCP rules below and both cutting across
them:

* DURABILITY OF THE RESULTING IDENTITY (jy006, 2026-08-13). Minting an
  ``mcp_api_key`` or installing a TLS private key leaves the caller holding a
  credential that keeps working after the operator turns authentication back
  on, where a settings write does not. The third route decided the same way,
  ``POST /api/backup/restore-initial``, does not appear in this inventory at
  all because it is guarded in its handler rather than by a dependency.
* CREDENTIAL ORACLE (2u4e0, 2026-08-15). The twelve routes of
  ``_OUTBOUND_CREDENTIAL_TEST`` reach the network with credentials the instance
  already stores and echo the upstream verdict back, so an anonymous caller
  could spend a secret they never had to learn. jy006 had left them open
  because its decision named none of them, which made ``POST
  /api/tls/test-dns-provider`` anonymous while ``GET /api/tls/settings``, which
  discloses the same credentials masked, was refused.

Both axes are pinned in
``tests/routers/test_jy006_auth_disabled_identity_primitives.py``, including a
test that exactly these three gates carry the flag and a route-level sweep of
everything the flag reaches, and NOT here: this module classifies by MCP
verdict and adding a second axis to its set assertions would make both harder
to read. What this module must not do is keep asserting the old blanket claim
in prose.

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
   password (9kwzp.10 item 4, as amended — see the exception note below,
   because the LIST read is admitted; since bead 9kwzp.13 BOTH reads mask that
   blob, so what this rule now buys is the write half); or
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
it. When that exception was made the route disclosed unmasked credentials, and
that was accepted as an open residual. Bead 9kwzp.13 closed it at the RESPONSE
rather than at the gate, so the admission is unchanged and both reads now mask
``config``. See ``_ALERT_METHOD_LIST``, which is still the comment to read
before deciding that a plain gate on this router means "harmless": the
disposition of that route has never been inferable from its gate name, and
that is as true now that the response is masked as it was when it was not.

WHERE THIS INVENTORY DELIBERATELY ADMITS SOMETHING ARGUABLE
-----------------------------------------------------------

Four verdicts below were reached against a plausible case for denying, and are
recorded here rather than left implicit. The sharpest of the four is now
``_DESTINATION_CRUD``, and it is the only one whose residual survives masking:
masking bounds what a READ discloses and says nothing whatever about a WRITE
that repoints where a scheduled job sends the operator's data.

* ``_ALERT_METHOD_LIST`` — ``GET /api/alert-methods`` is ADMITTED on a
  credential-bearing surface whose five sibling routes are not. Its
  disposition must not be inferred from the bare gate name IN EITHER
  DIRECTION: a plain gate here does not mean the route is uninteresting, and
  it no longer means the route leaks. Until bead 9kwzp.13 (build 0096) this
  response returned ``AlertMethod.config`` UNREDACTED and was the sharpest
  residual in this inventory; both reads now serialize through
  ``AlertMethod.to_dict(include_sensitive=False)``, so no credential VALUE
  leaves either one. What the principal still gets here is the method
  inventory plus every config key outside the masking set, which for a
  Telegram method includes the destination ``chat_id`` that bead 9ej7f
  withholds from this same principal on GET /api/settings. Read that group's
  comment before touching anything in ``/api/alert-methods``.
* ``_DESTINATION_CRUD`` — the cloud-target and sync-target routers are admitted
  END TO END, reads and writes. Do NOT read this as "masked, therefore
  harmless". The reads disclose the destination ``base_url`` /
  ``upload_path``, the ``insecure`` TLS-verification flag, the NAMES of the
  credential keys, each credential's last four characters (a bare ``***`` for
  a value of eight characters or fewer, so a short credential discloses no
  tail at all), ``credential_version`` and revocation state, and the outcome
  of past syncs — network topology plus credential fingerprints. Re-verified
  against the shipped serializers under bead 9kwzp.13. The writes do more:
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

CLOSED SINCE (bead 9kwzp.9)
---------------------------

This docstring used to record a KNOWN GAP: the backup read/export paths
(``_BACKUP_ARCHIVE``) emitted ``discord_webhook_url`` and ``telegram_chat_id``
in clear — the very values bead 9ej7f withheld from this same principal on GET
/api/settings — and the group below pinned that as CURRENT behaviour, not as
correct. Bead 9kwzp.9 closed it at the source rather than at the gate: the
artifact producer's denylist now DERIVES the read-redaction partition from
``config.ADMIN_ONLY_READ_REDACTED_FIELDS``, so the coarse gate on these routes
is no longer papering over a read-parity hole. The gate itself is unchanged and
is still the coarse ``RequireAdminIfEnabled``, for the reasons above.
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
        ("POST", f"{prefix}/run/prepare"),
        ("POST", f"{prefix}/run/commit"),
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
    ("POST", "/api/emby/clear-logos/prepare"),
}

# bead 9kwzp.6, decided on its own merits: restart-services takes the PLAIN
# admin gate. It names no host, carries no secret, echoes no upstream status,
# and rebuilds the tracker/prober from already-saved settings — the same work
# ``update_settings`` schedules for itself. What it was missing is the ordinary
# admin tier, which is what it now has.
_OPERATIONAL_RESTART = {
    ("POST", "/api/settings/restart-services"),
}

# Pinned as CORRECT since bead 9kwzp.9. These routes admit the MCP service
# principal (``RequireAdminIfEnabled`` accepts it), which is only acceptable
# because the artifact they emit no longer carries anything GET /api/settings
# withholds from that principal: ``routers.backup._SETTINGS_CREDENTIAL_FIELDS``
# derives ``config.ADMIN_ONLY_READ_REDACTED_FIELDS``, so
# ``discord_webhook_url`` and ``telegram_chat_id`` are redacted alongside
# ``telegram_bot_token``. If that derivation is ever unwound into a literal
# tuple, this group goes back to being a read-parity hole and the gate here is
# NOT what stops it.
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
# THE ROUTE DEPENDENCIES admit the principal on all nine, writes included. The
# effective 04c0u.4 capability matrix now refuses all six writes while keeping
# the three masked reads available. This bead first
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
# Do not infer effective authority from this dependency-only classification;
# see auth.mcp_capabilities.
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
# THIS ROUTE IS ADMITTED, AND ITS RESPONSE IS MASKED. BOTH HALVES MATTER.
# ===========================================================================
# bead 9kwzp.10 item 4, amended by the PO; disclosure closed by bead
# enhancedchannelmanager-9kwzp.13. Do not read the plain
# ``RequireAdminIfEnabled`` on ``routers/alert_methods.py::list_alert_methods``
# and conclude the route is uninteresting: the gate is plain because the
# CONTAINMENT is somewhere else, not because there is nothing to contain.
#
# WHAT IT USED TO DISCLOSE. The handler hand-rolled its response dict and
# emitted ``"config": json.loads(m.config)`` for every configured method, with
# NO masking of any kind. That blob is where the Discord webhook URL, the
# Telegram bot token and the SMTP password live, the exact three families bead
# 9ej7f withheld from this same principal on ``GET /api/settings``, so while
# that shape stood this route was a way around that redaction.
#
# WHY IT IS ADMITTED. The shipped ``list_alert_methods`` MCP tool is the
# operator's inventory of their own alert methods and calls exactly this route.
# Bead 9kwzp.10 denied it; the PO reversed that, accepting the disclosure at
# the time, because refusing it removed a capability the sidecar was built to
# provide.
#
# WHAT FIXED IT, and the reason it is recorded here rather than left to the
# gate name: the fix is the RESPONSE, not the gate. Both read handlers now
# serialize through ``models.AlertMethod.to_dict(include_sensitive=False)``,
# which substitutes ``********`` for ``password``, ``bot_token``,
# ``webhook_url`` and ``api_key``, so an admitted caller gets the inventory
# with no credential VALUE in it. Nothing about this dependency changed and the
# tool was never taken away.
#
# WHAT THIS ROUTE STILL DISCLOSES TO THE PRINCIPAL, stated so the admission is
# not read as costless: the method's id, name, type, enabled flag, severity
# filters, alert-source filters, timestamps, and every NON-credential config
# key, which for a Telegram method includes ``chat_id`` (the destination, not
# a credential; posting to it still needs the masked ``bot_token``) and for an
# SMTP method includes ``to_emails``. That also makes the human-admin gate on
# ``GET /api/alert-methods/{method_id}`` (in ``_NOTIFICATION_CREDENTIAL``)
# disclosure-vacuous against this principal, exactly as before: the list
# returns the same masked fields for every method. That gate is held because
# no tool needs the route, not because it is containing anything.
#
# If you are here because you are about to widen this router, the question to
# ask is whether the widened response still goes through ``to_dict``.
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
# reason for denying is the same one that denies ``POST /api/settings``. Note
# the writes are why this group survives the masking: a masked READ says
# nothing about a caller's ability to point the operator's alerts at a
# destination of its choosing.
#
# BE PRECISE ABOUT WHAT THE READ HALF OF THIS GATE ACHIEVES: nothing, against
# the MCP principal. ``GET /api/alert-methods`` is ADMITTED (see
# ``_ALERT_METHOD_LIST``) and returns the same fields for EVERY method, so the
# principal can read through the list exactly what this gate withholds on the
# single-method route. ``GET /{method_id}`` is kept here because no tool needs
# it and the group is coherent, not because it is containing a disclosure.
# What contains the disclosure is bead enhancedchannelmanager-9kwzp.13, which
# landed: both read handlers serialize through
# ``AlertMethod.to_dict(include_sensitive=False)``, so neither returns a
# credential value to anyone.
#
# ``test_alert_method`` was denied separately by 9kwzp.6 and lives in
# ``_OUTBOUND_CREDENTIAL_TEST``.
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
