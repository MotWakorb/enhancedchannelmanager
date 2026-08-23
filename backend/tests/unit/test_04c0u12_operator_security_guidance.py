"""Executable contracts for security-critical operator guidance (04c0u.12).

Every assertion here pins a *documented claim* to the *code that makes it
true*, so the two cannot drift apart silently. A doc that says "the MCP key
cannot take a backup" is worthless the day that stops being enforced, and
nothing else in the suite notices — the docs are not otherwise executable.

Each test reads a live object — a route table, a capability set, a dependency
gate, a defaults dict, a dataclass, a port constant — and then asserts the
document agrees with it. The prose half of that pairing is a substring search,
necessarily; what makes it more than a grep is that it never stands alone.
Changing the behavior fails these tests, which is the point: the failure is the
reminder to change the prose.

Two assertions are prose-only and honest about it:
``test_every_documented_bullet_is_a_sentence_the_guide_actually_carries`` keeps
the route mapping's keys pointing at real guide text, and the
restored-account/legacy-ZIP tests check that the runbook still states in words
what their live-object assertions prove.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import routers.backup as backup_mod
from auth.dependencies import (
    RequireAdminIfEnabled,
    RequireHumanAdminForNotificationCredential,
    RequireHumanAdminForOutboundTest,
    RequireHumanAdminForServiceCredential,
    RequireHumanAdminForTLSMaterial,
    RequireHumanAdminIfEnabled,
)
from auth.mcp_capabilities import MCP_HUMAN_ONLY_ROUTES, is_mcp_route_allowed
from auth.mcp_service import MCPServiceCredentials
from dbas.importers.users import _CONSERVATIVE_PRIVILEGE_DEFAULTS


ROOT = Path(__file__).resolve().parents[3]

RUNBOOK = "docs/runbooks/disaster-recovery-restore.md"
MCP_GUIDE = "docs/user_guide/integrations/mcp.md"
MCP_SETTINGS_GUIDE = "docs/user_guide/settings/mcp-integration.md"
README = "README.md"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _prose(relative: str) -> str:
    """Document text with emphasis markers and line wrapping flattened.

    A sentence in these guides is wrapped at ~80 columns and carries ``**bold**``
    inside it, so a literal substring search finds nothing and reports the
    sentence missing. Emphasis markers are stripped but ``_`` deliberately is
    not: these documents name identifiers such as ``mcp_api_key``, and
    stripping ``_`` would make every one of them unsearchable.
    """
    return re.sub(r"\s+", " ", re.sub(r"[*`~]", "", _read(relative)))


# Fences are routinely indented here — inside `!!! danger` admonitions and
# under numbered list items — so the delimiters must NOT be anchored at column
# 0. An anchored version of this made every such block invisible, which is how
# `docker exec … curl` survived in five indented blocks behind a passing guard.
_FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)


def _commands(relative: str) -> str:
    """Only the fenced code blocks — the lines an operator actually runs.

    The prose deliberately *names* the wrong endpoints in order to warn against
    them ("do not probe a hardcoded dispatcharr:8080"). Matching raw document
    text would fire on those warnings, which is the quoted-data-is-not-command-
    syntax trap: the guard would block the very sentence that prevents the
    mistake. Scan the executable spans instead.
    """
    return "\n".join(_FENCE.findall(_read(relative)))


def test_the_fence_scanner_reads_an_indented_block() -> None:
    """Guard the guard: an anchored fence regex silently reads nothing.

    Both live examples are in this repo's runbooks — a block nested in an
    `!!! danger` admonition and one under a numbered list item.
    """
    indented = (
        "!!! danger \"x\"\n\n    ```bash\n    admonition-command\n    ```\n"
        "\n1. step\n\n   ```bash\n   list-item-command\n   ```\n"
    )
    found = "\n".join(_FENCE.findall(indented))
    assert "admonition-command" in found
    assert "list-item-command" in found


# --------------------------------------------------------------------------
# The MCP static key is a limited service credential, not an administrator.
# --------------------------------------------------------------------------

# Every route the policy reserves, attributed to the bullet in the operator
# guide that promises it. This is a two-way contract, and the second direction
# is the one that was missing: the previous version enumerated 37 of the 56
# reserved routes and only closed the "a route was added" direction, so
# *removing* ("DELETE", "/api/alert-methods/{method_id}") from the policy left
# this suite, the .4 suite and test_admin_gate_inventory.py all green while the
# guide kept promising a stolen MCP key could not delete the operator's alert
# method — the channel that would have told them about the compromise.
_DOCUMENTED_HUMAN_ONLY: dict[str, tuple[tuple[str, str], ...]] = {
    "taking, listing, downloading, deleting or restoring backups": (
        ("GET", "/api/backup/create"),
        ("POST", "/api/backup/save"),
        ("GET", "/api/backup/saved"),
        ("GET", "/api/backup/saved/{filename}"),
        ("DELETE", "/api/backup/saved/{filename}"),
        ("POST", "/api/backup/restore"),
        ("POST", "/api/backup/restore-saved"),
        ("POST", "/api/backup/restore-dbas"),
        ("POST", "/api/backup/restore-dbas-saved"),
        ("POST", "/api/backup/restore-yaml"),
    ),
    "TLS certificate and private-key lifecycle, and the security settings blob": (
        ("GET", "/api/tls/settings"),
        ("POST", "/api/tls/configure"),
        ("POST", "/api/tls/request-cert"),
        ("POST", "/api/tls/complete-challenge"),
        ("POST", "/api/tls/upload-cert"),
        ("POST", "/api/tls/renew"),
        ("DELETE", "/api/tls/certificate"),
        ("POST", "/api/tls/https/start"),
        ("POST", "/api/tls/https/stop"),
        ("POST", "/api/tls/https/restart"),
        ("PATCH", "/api/settings/security"),
    ),
    "user, identity and authorization administration, including password change": (
        ("GET", "/api/auth/admin/settings"),
        ("PUT", "/api/auth/admin/settings"),
        ("GET", "/api/auth/admin/users"),
        ("GET", "/api/auth/admin/users/{user_id}"),
        ("PUT", "/api/auth/admin/users/{user_id}"),
        ("DELETE", "/api/auth/admin/users/{user_id}"),
        ("GET", "/api/auth/identities"),
        ("POST", "/api/auth/identities/link"),
        ("DELETE", "/api/auth/identities/{identity_id}"),
        ("PUT", "/api/auth/me"),
        ("POST", "/api/auth/change-password"),
    ),
    "generating or revoking the MCP API key itself": (
        ("POST", "/api/settings/mcp-api-key"),
        ("DELETE", "/api/settings/mcp-api-key"),
    ),
    "creating, changing, deleting or testing outbound destinations, and "
    "changing M3U or EPG source credentials": (
        ("POST", "/api/cloud-targets"),
        ("PATCH", "/api/cloud-targets/{target_id}"),
        ("DELETE", "/api/cloud-targets/{target_id}"),
        ("POST", "/api/cloud-targets/test"),
        ("POST", "/api/cloud-targets/{target_id}/test"),
        ("POST", "/api/sync-targets"),
        ("PUT", "/api/sync-targets/{target_id}"),
        ("DELETE", "/api/sync-targets/{target_id}"),
        ("POST", "/api/alert-methods"),
        ("PATCH", "/api/alert-methods/{method_id}"),
        ("DELETE", "/api/alert-methods/{method_id}"),
        ("POST", "/api/alert-methods/{method_id}/test"),
        ("POST", "/api/m3u/accounts"),
        ("PATCH", "/api/m3u/accounts/{account_id}"),
        ("DELETE", "/api/m3u/accounts/{account_id}"),
        ("POST", "/api/epg/sources"),
        ("PATCH", "/api/epg/sources/{source_id}"),
        ("DELETE", "/api/epg/sources/{source_id}"),
        ("POST", "/api/epg/sources/{source_id}/sd-lineups"),
        ("DELETE", "/api/epg/sources/{source_id}/sd-lineups"),
        ("POST", "/api/epg/sources/{source_id}/sd-lineups/search"),
    ),
    "running the Channel Pipeline in one shot": (
        ("POST", "/api/channel-pipeline/run"),
    ),
}

_DOCUMENTED_ROUTES = [
    (bullet, method, route)
    for bullet, routes in _DOCUMENTED_HUMAN_ONLY.items()
    for method, route in routes
]


@pytest.mark.parametrize(
    "bullet,method,route", _DOCUMENTED_ROUTES, ids=[f"{m} {r}" for _, m, r in _DOCUMENTED_ROUTES]
)
def test_capability_the_mcp_guide_calls_human_only_really_is(bullet, method, route) -> None:
    """Each capability the guide names is refused to the MCP principal."""
    assert (method, route) in MCP_HUMAN_ONLY_ROUTES, (
        f"{MCP_GUIDE} promises this is human-only under the bullet {bullet!r}, "
        "but the policy no longer reserves it"
    )
    assert is_mcp_route_allowed(method, route) is False


def test_the_guide_and_the_policy_reserve_exactly_the_same_routes() -> None:
    """Both directions, because only one of them was closed before.

    An *added* human-only family with no bullet leaves the guide quietly
    incomplete. A *removed* one leaves the guide actively wrong — it keeps
    promising a stolen key cannot do something it now can — and a prefix-based
    check cannot see that at all.
    """
    documented = frozenset((method, route) for _, method, route in _DOCUMENTED_ROUTES)
    undocumented = sorted(f"{m} {r}" for m, r in MCP_HUMAN_ONLY_ROUTES - documented)
    unreserved = sorted(f"{m} {r}" for m, r in documented - MCP_HUMAN_ONLY_ROUTES)
    assert undocumented == [], (
        f"reserved to humans but no bullet in {MCP_GUIDE} covers them: {undocumented}"
    )
    assert unreserved == [], (
        f"{MCP_GUIDE} promises these are human-only but the policy allows the "
        f"MCP principal to reach them: {unreserved}"
    )


def test_every_documented_bullet_is_a_sentence_the_guide_actually_carries() -> None:
    """The mapping's keys are guide text, not private labels.

    Without this the mapping could drift into naming bullets the guide does not
    have, and the equality test above would still pass.
    """
    guide = _prose(MCP_GUIDE)
    missing = [
        bullet
        for bullet in _DOCUMENTED_HUMAN_ONLY
        if not all(phrase in guide for phrase in bullet.split(", and "))
    ]
    assert missing == [], f"no bullet in {MCP_GUIDE} matches: {missing}"


# The routes the guide's leak paragraph names as reachable with the key. These
# are the household's viewing history: who watched what, from which IP, when.
_VIEWING_HISTORY_ROUTES = [
    ("GET", "/api/stats/watch-history"),
    ("GET", "/api/stats/unique-viewers"),
    ("GET", "/api/stats/unique-viewers-by-channel"),
    ("GET", "/api/stats/top-watched"),
    ("GET", "/api/stats/users/dispatcharr/{user_id}"),
    ("GET", "/api/stats/users/emby/{emby_user_id}"),
    ("GET", "/api/journal"),
]


@pytest.mark.parametrize("method,route", _VIEWING_HISTORY_ROUTES)
def test_the_leak_paragraph_does_not_understate_what_the_key_reads(method, route) -> None:
    """The guide says a stolen key reads the viewing history. It does.

    Stated the strong way round on purpose: if one of these is later reserved
    to humans, this fails and the paragraph gets narrowed rather than left
    overstating the risk.
    """
    assert is_mcp_route_allowed(method, route) is True


def test_watch_history_really_carries_and_filters_on_client_ip() -> None:
    """"...from which IP" is the sharpest half of that claim; pin it."""
    from routers.stats import get_watch_history

    assert "ip_address" in inspect.signature(get_watch_history).parameters


def test_the_mcp_key_still_reaches_ordinary_channel_automation() -> None:
    """The guide says a stolen key can still read and modify channels.

    Without this control, a policy that refused everything would satisfy the
    test above while making the guide's risk statement wrong in the other
    direction.
    """
    assert is_mcp_route_allowed("GET", "/api/channels") is True
    assert is_mcp_route_allowed("DELETE", "/api/channels/{channel_id}") is True


def test_docs_state_the_three_credential_model() -> None:
    """The guide's table has one row per credential the system actually has.

    Read the objects, not the sentence: the two credentials the operator never
    handles are the fields of ``MCPServiceCredentials``, and the one they do
    handle is the ``mcp_api_key`` setting. A fourth credential appearing (or one
    of these being folded away) leaves the table wrong, and this fails.
    """
    from config import DispatcharrSettings

    private = tuple(MCPServiceCredentials.__dataclass_fields__)
    assert private == ("backend_key", "confirmation_key"), private
    assert "mcp_api_key" in DispatcharrSettings.model_fields

    guide = _prose(MCP_GUIDE)
    assert "three separate credentials" in guide
    # One table row per credential, each naming what it is for.
    assert "mcp_api_key" in guide
    assert "Sidecar backend key" in guide
    assert "Confirmation key" in guide
    # And the property that makes the split worth documenting: the key the
    # operator holds is refused by the backend outright.
    assert "the backend refuses it outright" in guide


def test_destructive_mcp_tool_confirmation_ttl_matches_the_documented_five_minutes() -> None:
    """The guide promises a 5-minute window; the sidecar defines the number."""
    policy = (ROOT / "mcp-server" / "tools" / "_safety_policy.py").read_text(encoding="utf-8")
    match = re.search(r"^CONFIRMATION_TTL_SECONDS\s*=\s*(\d+)", policy, re.MULTILINE)
    assert match, "CONFIRMATION_TTL_SECONDS is no longer defined where the docs point"
    assert int(match.group(1)) == 300
    assert "expires after 5 minutes" in _read(MCP_GUIDE)


def _enforces_when_auth_disabled(dependency) -> bool:
    """Read the flag off the live gate object, not off its source text."""
    gate = dependency.dependency
    closure = dict(zip(gate.__code__.co_freevars, (cell.cell_contents for cell in gate.__closure__)))
    return closure["enforce_when_auth_disabled"]


def test_the_guide_qualifies_the_capability_list_by_auth_mode() -> None:
    """Most of the reserved list is only reserved while auth is required.

    ``require_auth: false`` is a supported mode. The capability matrix runs
    inside the middleware's ``require_auth and setup_complete`` branch, and the
    gates behind the backup and outbound-destination bullets return ``None``
    when authentication is off — so an unqualified "a stolen key cannot
    exfiltrate a backup" is false in that mode, and contradicts
    ``docs/auth_middleware.md``, which enumerates those same routes as open.
    """
    assert _enforces_when_auth_disabled(RequireAdminIfEnabled) is False
    assert _enforces_when_auth_disabled(RequireHumanAdminIfEnabled) is False

    # The bullets that DO survive auth-disabled, and are stated unqualified.
    assert _enforces_when_auth_disabled(RequireHumanAdminForServiceCredential) is True
    assert _enforces_when_auth_disabled(RequireHumanAdminForTLSMaterial) is True

    # The backup surface is gated by the conditional pair, which is why the
    # guide may not promise it unconditionally.
    for handler in (backup_mod.create_backup, backup_mod.restore_backup):
        gate = inspect.signature(handler).parameters["_admin"].default
        assert gate in (RequireAdminIfEnabled, RequireHumanAdminIfEnabled)
        assert _enforces_when_auth_disabled(gate) is False

    guide = _prose(MCP_GUIDE)
    assert "This list holds while ECM requires authentication" in guide
    assert "docs/auth_middleware.md" in guide
    assert "Four things hold in every mode" in guide


def test_outbound_connection_tests_survive_auth_being_disabled() -> None:
    """The credential-oracle limit is the one that DOES hold with auth off.

    An earlier draft of the guide, and of the Disable Authentication dialog,
    said the opposite: it listed "creating or **testing** outbound
    destinations" together as becoming reachable without a credential. Testing
    does not. ``RequireHumanAdminForOutboundTest`` carries
    ``enforce_when_auth_disabled=True`` (bead 2u4e0), subject to the same
    "once the instance has an operator identity" carve-out as the other two
    surviving gates.

    Nothing pinned this, which is exactly why the one wrong sentence was the
    wrong one: the sibling test above reads four gates off their closures and
    this gate was not among them. Read it off the live objects here, and read
    the split off the routers, so neither half can drift.
    """
    assert _enforces_when_auth_disabled(RequireHumanAdminForOutboundTest) is True

    # ...and the WRITE verbs on the same routers deliberately do not, which is
    # what makes the guide's split a real distinction rather than hedging.
    assert _enforces_when_auth_disabled(RequireAdminIfEnabled) is False
    assert _enforces_when_auth_disabled(RequireHumanAdminForNotificationCredential) is False

    import routers.alert_methods as alert_methods_mod
    import routers.cloud_targets as cloud_targets_mod

    tested = (
        cloud_targets_mod.test_cloud_target_inline,
        cloud_targets_mod.test_cloud_target,
        alert_methods_mod.test_alert_method,
    )
    for handler in tested:
        gate = inspect.signature(handler).parameters["_admin"].default
        assert gate is RequireHumanAdminForOutboundTest, handler.__name__
        assert _enforces_when_auth_disabled(gate) is True, handler.__name__

    guide = _prose(MCP_GUIDE)
    assert "Outbound connection tests stay human-admin-only" in guide
    assert "credential-oracle limit does not follow them" in guide

    # The same caveat has to reach the operator where the decision is made, in
    # the Disable Authentication dialog. That half is proven on the rendered
    # dialog by "names the MCP-capability caveat that turning auth off removes"
    # in frontend/src/components/settings/AuthSettingsSection.test.tsx; a text
    # grep from here would be strictly weaker than the assertion that already
    # exists there.


def test_administrator_administration_is_claimed_only_for_who_it_holds_against() -> None:
    """The admin-administration guarantee, stated to exactly its real extent.

    ``/api/auth/admin/*`` chains ``get_current_user`` with no auth-disabled
    short-circuit, so a caller presenting NO token is refused in every mode, and
    the operator-facing ``mcp_api_key`` is refused before any route runs. Those
    two are what the guide may promise.

    What it may NOT promise is "human-admin-only, full stop". ``require_admin``
    checks ``user.is_admin`` and nothing else, and ``get_current_user`` answers
    the sidecar's private backend key with ``_build_mcp_service_principal()``,
    which sets ``is_admin=True``. The only thing keeping that principal out is
    the capability matrix, and ``backend/main.py`` applies it strictly inside
    ``if auth_settings.require_auth and auth_settings.setup_complete:``. So with
    authentication off, the service principal is an unrestricted administrator
    on these routes. The earlier unqualified sentence read as a guarantee
    against every credential ECM issues.

    This test therefore pins the SHAPE of the claim, not a reproduction: if
    ``require_admin`` ever grows a real principal check, the guide can be
    widened again — and this test is where that gets noticed.
    """
    from auth.routes import require_admin

    source = inspect.getsource(require_admin)
    assert "require_auth" not in source
    assert "setup_complete" not in source
    # It reaches the caller only through get_current_user, which refuses an
    # unauthenticated request in every mode; the parameter default is the live
    # proof of that chaining.
    dependency = inspect.signature(require_admin).parameters["user"].default
    assert dependency.dependency.__name__ == "get_current_user"

    # The narrowing is required as long as this stays true: no principal check.
    assert "is_mcp_service_principal" not in source
    assert "is_admin" in source

    guide = _prose(MCP_GUIDE)
    assert "Anonymous administrator administration stays blocked" in guide
    assert "not a guarantee against every credential ECM issues" in guide
    # And the reason the mcp_api_key half holds unconditionally: the refusal is
    # ahead of the require_auth branch, not inside it.
    main_source = _read("backend/main.py")
    refusal = main_source.index("cannot authenticate to the backend")
    branch = main_source.index("if auth_settings.require_auth and auth_settings.setup_complete:")
    assert refusal < branch


# --------------------------------------------------------------------------
# ECM TLS does not protect the MCP sidecar.
# --------------------------------------------------------------------------


def test_tls_versus_mcp_transport_is_explicit_everywhere_it_matters() -> None:
    """Two listeners, two ports, one of which ECM's TLS setting never touches.

    The warning is only worth reading if its numbers are right, so take them
    from the code that defines them: ECM's HTTPS port from ``tls.settings`` and
    the sidecar's from the sidecar's own config. If they ever converge, the
    "separate process on its own port" framing needs rewriting and this fails.
    """
    from tls.settings import TLSSettings

    https_port = TLSSettings().https_port
    sidecar_config = (ROOT / "mcp-server" / "config.py").read_text(encoding="utf-8")
    match = re.search(r"""MCP_PORT = int\(os\.environ\.get\("MCP_PORT", "(\d+)"\)\)""", sidecar_config)
    assert match, "the sidecar no longer defines MCP_PORT where the docs point"
    sidecar_port = int(match.group(1))
    assert https_port != sidecar_port

    guide = _prose(MCP_GUIDE)
    assert "Enabling TLS in ECM does not protect MCP" in guide
    assert f"default {https_port}" in guide, "the guide's ECM_HTTPS_PORT default is stale"
    assert f"its own port ({sidecar_port})" in guide, "the guide's MCP port is stale"
    assert "ECM's own TLS setting does not protect MCP" in _prose(README)
    assert "does not encrypt MCP traffic" in _prose(MCP_SETTINGS_GUIDE)


# --------------------------------------------------------------------------
# Disaster-recovery runbook: commands an operator runs mid-incident.
# --------------------------------------------------------------------------


def test_runbook_runs_no_ecm_api_call_against_the_retired_8080_port() -> None:
    """ECM has never served on 8080; the runbook used to say it five times."""
    commands = _commands(RUNBOOK)
    assert "localhost:8080" not in commands
    assert "dispatcharr:8080" not in commands


def test_runbook_does_not_tell_the_operator_to_exec_curl_in_the_ecm_container() -> None:
    """The ECM image ships no curl; `docker exec ecm-ecm-1 curl` cannot work."""
    commands = _commands(RUNBOOK)
    assert not re.search(r"docker exec\s+\S+\s+curl", commands)
    assert "docker exec ecm-ecm-1 python3" in commands


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/health/ready"),
        ("POST", "/api/backup/save"),
        ("POST", "/api/tasks/{task_id}/run"),
    ],
)
def test_every_ecm_endpoint_the_runbook_tells_you_to_call_exists(method, path) -> None:
    """Pin the runbook's URLs to the app's real route table, not to prose."""
    from main import app

    registered = {
        (verb, route.path)
        for route in app.routes
        for verb in getattr(route, "methods", set()) or set()
    }
    assert (method, path) in registered


def test_runbook_uses_the_default_ecm_port_for_those_endpoints() -> None:
    commands = _commands(RUNBOOK)
    assert "http://localhost:6100/api/backup/save" in commands
    assert "http://localhost:6100/api/tasks/m3u_refresh/run" in commands


def test_runbook_names_the_dispatcharr_channel_group_route_dispatcharr_actually_has() -> None:
    """A mistyped Dispatcharr API path returns the SPA with 200, not 404.

    The runbook used to say ``/api/channel-groups/``, which is not an API route:
    it falls through to Dispatcharr's web UI, so a residue cleanup would report
    success and delete nothing. ECM's own client is the authority on the path.
    """
    client = (ROOT / "backend" / "dispatcharr_client.py").read_text(encoding="utf-8")
    assert '"/api/channels/groups/"' in client

    commands = _commands(RUNBOOK)
    assert "/api/channels/groups/" in commands
    assert "/api/channel-groups/" not in commands


# --------------------------------------------------------------------------
# Restore behavior: accounts, privileges, collisions, TLS coverage.
# --------------------------------------------------------------------------


def test_restored_accounts_really_are_forced_non_privileged() -> None:
    """The runbook promises every flag off; the importer's defaults decide."""
    assert _CONSERVATIVE_PRIVILEGE_DEFAULTS == {
        "is_superuser": False,
        "is_staff": False,
        "user_level": 0,
    }
    runbook = _read(RUNBOOK)
    for claim in ("is_superuser", "is_staff", "user_level"):
        assert claim in runbook
    assert "fresh random password" in runbook
    assert "skipped, never overwritten" in runbook


def test_legacy_zip_tls_coverage_is_stated_as_the_code_has_it() -> None:
    """A legacy artifact can carry ``tls/`` and a legacy restore writes it back.

    Stated as a capability ("can contain") rather than a guarantee, because the
    producer's directory list is what an in-flight branch narrows; the restore
    side is what the runbook's advice depends on and is unchanged.

    So read the *restore* side. A branch that narrows the producer to
    ``["uploads/logos"]`` moves the legacy restore list to
    ``LEGACY_RESTORE_DIRS``; pinning ``BACKUP_DIRS`` would then fail this test
    on a change it does not own and turn ``dev`` red for whichever PR merges
    second. ``getattr`` reads whichever name the module currently publishes,
    and both shapes satisfy the sentence the runbook actually depends on.
    """
    restore_dirs = getattr(backup_mod, "LEGACY_RESTORE_DIRS", backup_mod.BACKUP_DIRS)
    assert "tls" in restore_dirs
    runbook = _read(RUNBOOK)
    assert "can contain `tls/`" in runbook
    assert "TLS certificates are not part of a DBAS restore" in runbook


def test_dbas_has_no_tls_category_to_restore_from() -> None:
    """The runbook's "DBAS never brings the certificate back" claim."""
    from dbas.restore_contracts import EntityType

    assert not [member for member in EntityType if "tls" in member.value.lower()]
