"""bead 9kwzp.7 — the admin-gate inventory, pinned.

WHAT THIS PINS AND WHY
----------------------

ECM has two admin gates that look interchangeable and are not:

* ``auth.RequireAdminIfEnabled`` — admin required, and the static MCP service
  principal IS ADMITTED, because ``_build_mcp_service_principal`` sets
  ``is_admin=True``.
* ``auth.RequireHumanAdminIfEnabled`` / ``auth.RequireHumanAdminForOutboundTest``
  / ``auth.RequireHumanAdminForServiceCredential`` — admin required AND the MCP
  service principal is refused. These three behave identically; they differ
  only in the 403 body, which names the surface being refused so incident
  triage starts in the right place.

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
   bearer of a credential the party that rotates and revokes it (9kwzp.8).

Everything else stays admitted. The groups below record that verdict per site
so the next reader does not re-derive it.

KNOWN GAPS, DELIBERATELY NOT CHANGED HERE
-----------------------------------------

The audit turned up sites that are arguably on the wrong side of the rule but
are outside 9kwzp.6 / .7 scope and are recorded as backlog candidates rather
than silently re-gated: the backup read/export paths (which emit
``discord_webhook_url`` and ``telegram_chat_id`` in clear, the very values
bead 9ej7f just withheld from this same principal on GET /api/settings),
PATCH /api/settings/security (which lets the principal widen the SSRF outbound
policy that guards the routes above), and the sync-target CRUD (which writes an
outbound base URL plus credentials, a thing kgz3k denies the principal on
POST /api/settings). They are listed in ``MCP_ADMITTED`` under their own
group names so the inventory stays honest about what is pinned versus what is
merely current.
"""
import pytest
from fastapi.routing import APIRoute

from auth import (
    RequireAdminIfEnabled,
    RequireHumanAdminForOutboundTest,
    RequireHumanAdminForServiceCredential,
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

# BACKLOG CANDIDATE, not pinned as correct — pinned as CURRENT. The archive
# these emit carries ``discord_webhook_url`` and ``telegram_chat_id`` in clear
# (``routers.backup._gather_settings`` redacts only
# ``_SETTINGS_CREDENTIAL_FIELDS``, which does not include them), so the MCP
# principal can read through this path the exact values bead 9ej7f withholds
# from it on GET /api/settings. The restore-dbas pair additionally writes
# config wholesale, which is the kgz3k class the three /api/backup/restore*
# endpoints are already human-admin for. Out of scope for 9kwzp.6 / .7.
_BACKUP_ARCHIVE = {
    ("GET", "/api/backup/create"),
    ("GET", "/api/backup/export"),
    ("GET", "/api/backup/export-sections"),
    ("GET", "/api/backup/saved"),
    ("GET", "/api/backup/saved/{filename}"),
    ("DELETE", "/api/backup/saved/{filename}"),
    ("POST", "/api/backup/save"),
    ("POST", "/api/backup/validate"),
    ("POST", "/api/backup/restore-dbas"),
    ("POST", "/api/backup/restore-dbas-saved"),
}

# BACKLOG CANDIDATE, pinned as current. Sync-target CRUD writes an outbound
# base URL plus credentials — the same shape kgz3k denies this principal on
# POST /api/settings — but bead jcj0f exposed create/update/delete as MCP
# tools deliberately. Responses mask credentials (last 4 chars). Flagged for
# the PO rather than re-gated here.
_SYNC_TARGET_CRUD = {
    ("GET", "/api/sync-targets"),
    ("POST", "/api/sync-targets"),
    ("GET", "/api/sync-targets/{target_id}"),
    ("PUT", "/api/sync-targets/{target_id}"),
    ("DELETE", "/api/sync-targets/{target_id}"),
}

# BACKLOG CANDIDATE, pinned as current. This is the only writer of
# ``ssrf_outbound_mode`` (POST /api/settings carries the stored value forward
# untouched), so the MCP principal can widen the outbound policy from
# public-only to LAN-friendly — weakening the control that constrains WHICH
# hosts the routes in MCP_DENIED may reach. Flagged for the PO.
_SSRF_POLICY_WRITE = {
    ("PATCH", "/api/settings/security"),
}

MCP_ADMITTED: frozenset = frozenset(
    _CHANNEL_AUTOMATION
    | _PIPELINE_AUTOMATION
    | _REVIEW_QUEUES
    | _EMBY_LOGO_MAINTENANCE
    | _OPERATIONAL_RESTART
    | _BACKUP_ARCHIVE
    | _SYNC_TARGET_CRUD
    | _SSRF_POLICY_WRITE
)


# ---------------------------------------------------------------------------
# Verdicts — MCP service principal DENIED
# ---------------------------------------------------------------------------

# kgz3k / 6n76m: restore rewrites the settings blob wholesale, which would let
# the automation credential flip every admin-only field in one call and bypass
# the field-level gate on POST /api/settings.
_WHOLESALE_CONFIG_WRITE = {
    ("POST", "/api/backup/restore"),
    ("POST", "/api/backup/restore-saved"),
    ("POST", "/api/backup/restore-yaml"),
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

MCP_DENIED: frozenset = frozenset(
    _WHOLESALE_CONFIG_WRITE
    | _OUTBOUND_CREDENTIAL_TEST
    | _SERVICE_CREDENTIAL_LIFECYCLE
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
    same way. Pin the classifier against the four shipped dependencies first.
    """
    assert _gate_kind(RequireAdminIfEnabled.dependency) == "admitted"
    assert _gate_kind(RequireHumanAdminIfEnabled.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForOutboundTest.dependency) == "denied"
    assert _gate_kind(RequireHumanAdminForServiceCredential.dependency) == "denied"


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
