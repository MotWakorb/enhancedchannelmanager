"""Hardcoded OAuth client registry for Claude Desktop + Claude Code (bead buiqr.6).

NO Dynamic Client Registration in v1 (PO-locked 2026-05-19; ADR-009 §3, §8).
Instead this module defines a small, **in-code** registry of the only OAuth
clients ECM will ever accept, and seeds it into the ECM-managed OAuth state
store (``auth/oauth_store.py``) at ECM startup.

WHY HARDCODED (threat model SP4, CP1)
=====================================
Operators cannot add, edit, or remove clients via any API or UI in v1. This
prevents consent-phishing where an attacker registers a client named
"Claude Desktop (Official)" and tricks the admin into approving it. Because the
set of clients is fixed and in-code, the consent screen (buiqr.7) can pin a
**verified** client name ("Claude Desktop" / "Claude Code") rather than reflect
attacker-chosen input.

OPTION A SEEDING (ADR-009 Revision History; bead buiqr.6 reframing)
===================================================================
Bead buiqr.6 AC1 originally said the registry is "seeded on MCP container first
start." That is INFEASIBLE under Option A: the MCP container mounts ``/config``
**read-only** and cannot write ``mcp_oauth.db``. Under Option A the store is
**ECM-managed** (ECM is the sole reader/writer), so the registry is seeded from
**ECM at ECM startup** via :func:`seed_oauth_clients`, which is idempotent
(``OAuthStore.create_client`` is an upsert). See ``backend/main.py``'s startup
path for the wiring.

REDIRECT URIs (KNOWN UNKNOWN — flagged for PO verification)
===========================================================
The exact ``redirect_uri`` Claude Desktop's Custom Connector uses must be
verified against captured Claude Desktop traffic before release (bead buiqr.6).
We do **not** have captured traffic in this environment, so the Claude Desktop
redirect URI below is a **best-guess placeholder** marked with ``# TODO(buiqr.6)``.
The exact-match redirect-URI validation (threat model RD1) means a wrong value
here will simply reject Claude Desktop with ``400 invalid_request`` — fail-closed,
never an open redirect — so this is safe to ship as a placeholder pending
verification, but Claude Desktop will not connect until the value is confirmed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from auth.oauth_store import OAuthStore

logger = logging.getLogger(__name__)

#: Client id for Claude Desktop's Custom Connector OAuth flow.
CLAUDE_DESKTOP_CLIENT_ID = "claude-desktop"
#: Client id reserved for any future direct-Bearer use by Claude Code / scripts.
CLAUDE_CODE_CLIENT_ID = "claude-code"

# TODO(buiqr.6): VERIFY against captured Claude Desktop Custom Connector traffic
# before release. This is a best-guess placeholder. Claude Desktop's connector
# OAuth callback scheme has been observed as the custom scheme below in public
# reports, but we have not captured ECM-specific traffic to confirm it. The
# exact-match redirect-URI check (RD1) fails closed on a wrong value, so this is
# safe to ship un-verified — Claude Desktop simply will not connect until the
# real value is pinned here.
CLAUDE_DESKTOP_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
    "claudeai://oauth/callback",
]

# Claude Code / scripts use the static ?api_key= path today and have no browser
# redirect. This entry exists so a future direct-Bearer OAuth use has a pinned
# identity; out-of-band redirect (RFC 6749 "oob") signals "no redirect endpoint".
CLAUDE_CODE_REDIRECT_URIS = [
    "urn:ietf:wg:oauth:2.0:oob",
]

#: The full hardcoded registry. Each entry is exactly what gets upserted into
#: the store. ``redirect_uris`` is the exact-match allowlist (RD1) — NO prefix,
#: substring, or wildcard matching is ever performed against it.
HARDCODED_CLIENTS: list[dict] = [
    {
        "client_id": CLAUDE_DESKTOP_CLIENT_ID,
        "client_name": "Claude Desktop",
        "redirect_uris": CLAUDE_DESKTOP_REDIRECT_URIS,
    },
    {
        "client_id": CLAUDE_CODE_CLIENT_ID,
        "client_name": "Claude Code",
        "redirect_uris": CLAUDE_CODE_REDIRECT_URIS,
    },
]


def seed_oauth_clients(store: "OAuthStore") -> int:
    """Seed the hardcoded client registry into the store (idempotent).

    Calls :meth:`OAuthStore.create_client` (an idempotent upsert) for every
    entry in :data:`HARDCODED_CLIENTS`. Safe to run on every ECM startup; a
    restart re-upserts the same rows (bead buiqr.6 AC1 — idempotent on restart).

    Returns the number of client entries seeded (always ``len(HARDCODED_CLIENTS)``
    on success), for logging/telemetry.
    """
    for entry in HARDCODED_CLIENTS:
        store.create_client(
            client_id=entry["client_id"],
            client_name=entry["client_name"],
            redirect_uris=entry["redirect_uris"],
        )
    count = len(HARDCODED_CLIENTS)
    logger.info("[OAUTH] Seeded %d hardcoded OAuth client(s) into the registry", count)
    return count
