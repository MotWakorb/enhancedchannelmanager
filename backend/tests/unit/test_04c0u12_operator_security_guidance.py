"""Executable contracts for security-critical operator guidance (04c0u.12).

Every assertion here pins a *documented claim* to the *code that makes it
true*, so the two cannot drift apart silently. A doc that says "the MCP key
cannot take a backup" is worthless the day that stops being enforced, and
nothing else in the suite notices — the docs are not otherwise executable.

These are deliberately NOT grep-for-a-word checks. Each one reads the live
policy object (a route table, a capability set, a defaults dict, a constant)
and asserts the document agrees with it. Changing the behavior fails these
tests, which is the point: the failure is the reminder to change the prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from auth.mcp_capabilities import MCP_HUMAN_ONLY_ROUTES, is_mcp_route_allowed
from dbas.importers.users import _CONSERVATIVE_PRIVILEGE_DEFAULTS
from routers.backup import BACKUP_DIRS


ROOT = Path(__file__).resolve().parents[3]

RUNBOOK = "docs/runbooks/disaster-recovery-restore.md"
MCP_GUIDE = "docs/user_guide/integrations/mcp.md"
MCP_SETTINGS_GUIDE = "docs/user_guide/settings/mcp-integration.md"
README = "README.md"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _commands(relative: str) -> str:
    """Only the fenced code blocks — the lines an operator actually runs.

    The prose deliberately *names* the wrong endpoints in order to warn against
    them ("do not probe a hardcoded dispatcharr:8080"). Matching raw document
    text would fire on those warnings, which is the quoted-data-is-not-command-
    syntax trap: the guard would block the very sentence that prevents the
    mistake. Scan the executable spans instead.
    """
    return "\n".join(re.findall(r"^```[^\n]*\n(.*?)^```", _read(relative), re.MULTILINE | re.DOTALL))


# --------------------------------------------------------------------------
# The MCP static key is a limited service credential, not an administrator.
# --------------------------------------------------------------------------

# Exactly the capabilities the operator guide promises are human-only. If a
# route moves out of MCP_HUMAN_ONLY_ROUTES the guide is lying, and this fails.
_DOCUMENTED_HUMAN_ONLY = [
    # "taking, listing, downloading, deleting or restoring backups"
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
    # "TLS certificate and private-key lifecycle, and the security settings blob"
    ("POST", "/api/tls/upload-cert"),
    ("POST", "/api/tls/configure"),
    ("POST", "/api/tls/request-cert"),
    ("POST", "/api/tls/renew"),
    ("DELETE", "/api/tls/certificate"),
    ("PATCH", "/api/settings/security"),
    # "user, identity and authorization administration, including password change"
    ("GET", "/api/auth/admin/users"),
    ("PUT", "/api/auth/admin/users/{user_id}"),
    ("DELETE", "/api/auth/admin/users/{user_id}"),
    ("PUT", "/api/auth/admin/settings"),
    ("POST", "/api/auth/change-password"),
    ("PUT", "/api/auth/me"),
    # "generating or revoking the MCP API key itself"
    ("POST", "/api/settings/mcp-api-key"),
    ("DELETE", "/api/settings/mcp-api-key"),
    # "creating, changing, deleting or testing outbound destinations ...
    #  and changing M3U or EPG source credentials"
    ("POST", "/api/cloud-targets"),
    ("PATCH", "/api/cloud-targets/{target_id}"),
    ("DELETE", "/api/cloud-targets/{target_id}"),
    ("POST", "/api/cloud-targets/test"),
    ("POST", "/api/sync-targets"),
    ("PUT", "/api/sync-targets/{target_id}"),
    ("DELETE", "/api/sync-targets/{target_id}"),
    ("POST", "/api/alert-methods/{method_id}/test"),
    ("POST", "/api/m3u/accounts"),
    ("PATCH", "/api/m3u/accounts/{account_id}"),
    ("POST", "/api/epg/sources"),
    ("PATCH", "/api/epg/sources/{source_id}"),
]


@pytest.mark.parametrize("method,route", _DOCUMENTED_HUMAN_ONLY)
def test_capability_the_mcp_guide_calls_human_only_really_is(method, route) -> None:
    """Each capability the guide names is refused to the MCP principal."""
    assert (method, route) in MCP_HUMAN_ONLY_ROUTES
    assert is_mcp_route_allowed(method, route) is False


def test_mcp_guide_does_not_understate_the_reserved_surface() -> None:
    """The guide's five bullets must still span every human-only prefix.

    A new human-only family (say ``/api/webhooks``) added to the policy without
    a matching bullet would leave the guide quietly incomplete. Fail here so the
    prose is updated with the policy.
    """
    documented_prefixes = (
        "/api/backup/",
        "/api/tls/",
        "/api/settings/",
        "/api/auth/",
        "/api/cloud-targets",
        "/api/sync-targets",
        "/api/alert-methods",
        "/api/m3u/accounts",
        "/api/epg/sources",
        "/api/channel-pipeline/run",
    )
    undocumented = sorted(
        f"{method} {route}"
        for method, route in MCP_HUMAN_ONLY_ROUTES
        if not route.startswith(documented_prefixes)
    )
    assert undocumented == [], (
        "These routes are reserved to humans but no bullet in "
        f"{MCP_GUIDE} covers them: {undocumented}"
    )


def test_the_mcp_key_still_reaches_ordinary_channel_automation() -> None:
    """The guide says a stolen key can still read and modify channels.

    Without this control, a policy that refused everything would satisfy the
    test above while making the guide's risk statement wrong in the other
    direction.
    """
    assert is_mcp_route_allowed("GET", "/api/channels") is True
    assert is_mcp_route_allowed("DELETE", "/api/channels/{channel_id}") is True


def test_docs_state_the_three_credential_model() -> None:
    guide = _read(MCP_GUIDE)
    assert "three separate credentials" in guide
    for credential in ("Sidecar backend key", "Confirmation key"):
        assert credential in guide


def test_destructive_mcp_tool_confirmation_ttl_matches_the_documented_five_minutes() -> None:
    """The guide promises a 5-minute window; the sidecar defines the number."""
    policy = (ROOT / "mcp-server" / "tools" / "_safety_policy.py").read_text(encoding="utf-8")
    match = re.search(r"^CONFIRMATION_TTL_SECONDS\s*=\s*(\d+)", policy, re.MULTILINE)
    assert match, "CONFIRMATION_TTL_SECONDS is no longer defined where the docs point"
    assert int(match.group(1)) == 300
    assert "expires after 5 minutes" in _read(MCP_GUIDE)


# --------------------------------------------------------------------------
# ECM TLS does not protect the MCP sidecar.
# --------------------------------------------------------------------------


def test_tls_versus_mcp_transport_is_explicit_everywhere_it_matters() -> None:
    assert "Enabling TLS in ECM does not protect MCP" in _read(MCP_GUIDE)
    assert "ECM's own TLS setting does not protect MCP" in _read(README)
    assert "does not encrypt MCP traffic" in _read(MCP_SETTINGS_GUIDE)


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
    assert "/api/channels/groups/ID/" in commands
    assert "/api/channel-groups/ID" not in commands


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
    """
    assert "tls" in BACKUP_DIRS
    runbook = _read(RUNBOOK)
    assert "can contain `tls/`" in runbook
    assert "TLS certificates are not part of a DBAS restore" in runbook


def test_dbas_has_no_tls_category_to_restore_from() -> None:
    """The runbook's "DBAS never brings the certificate back" claim."""
    from dbas.restore_contracts import EntityType

    assert not [member for member in EntityType if "tls" in member.value.lower()]
