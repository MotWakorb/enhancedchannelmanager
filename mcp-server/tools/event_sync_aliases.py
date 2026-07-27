"""Event Sync team-alias dictionary tools (bead enhancedchannelmanager-ti939.4.2).

Wraps backend/routers/event_sync_aliases.py — the operator team-alias
dictionary consulted by the event matcher's team-token layer: groups of
KNOWN-equivalent team spellings ("Man Utd" == "Manchester United" ==
"MUFC") that raise recall on abbreviation-heavy providers without
loosening the fuzzy threshold.

The update tool is a FULL-REPLACE write of the whole dictionary (mirroring
the backend PUT): callers should read the current groups first, modify,
and send the complete list back. The backend validates every term against
the matcher's own normalization (blank/identity-free terms, <2-term
groups, and duplicate terms across groups are rejected with actionable
400s) and journals before/after state.

Aliases are corpus-gated by policy: only add a group when observed
provider pairs prove the equivalence — a wrong alias is a new
false-positive vector (see docs/event_sync.md).
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def _format_groups(groups: list) -> str:
    lines = []
    for i, group in enumerate(groups, start=1):
        terms = group.get("terms") or []
        note = group.get("note")
        note_str = f"  — {note}" if note else ""
        lines.append(f"  {i}. {' == '.join(terms)}{note_str}")
    return "\n".join(lines)


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_event_sync_team_aliases() -> str:
        """Get the Event Sync team-alias dictionary (operator equivalences).

        Each group declares known-equivalent team spellings consulted by
        the event matcher's team-token check ("Man Utd" == "Manchester
        United" == "MUFC"). An empty dictionary is the shipped default.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["es_get_team_aliases"])
            groups = result.get("groups", []) if isinstance(result, dict) else []
            if not groups:
                return (
                    "Team-alias dictionary is empty (no operator aliases "
                    "configured — the shipped default)."
                )
            return (
                f"Team-alias dictionary ({len(groups)} group(s)):\n"
                + _format_groups(groups)
            )
        except Exception as e:
            logger.error("[MCP] get_event_sync_team_aliases failed: %s", e)
            return f"Error getting team aliases: {e}"

    @mcp.tool()
    async def update_event_sync_team_aliases(groups: list[dict]) -> str:
        """Replace the Event Sync team-alias dictionary (FULL-REPLACE write).

        Sends the COMPLETE new dictionary — any group not included is
        removed. Read the current dictionary with
        get_event_sync_team_aliases first, modify, and send everything
        back. Pass an empty list to clear the dictionary.

        Policy: aliases are corpus-gated — only add a group when observed
        provider pairs prove the equivalence. A wrong alias is a new
        false-positive vector for the matcher.

        Args:
            groups: Full list of alias groups. Each group is a dict:
                {"terms": ["Man Utd", "Manchester United", "MUFC"],
                 "note": "optional evidence note"}.
                Every group needs at least 2 terms; a term may appear in
                only one group (terms are compared case/punctuation-
                insensitively, the matcher's own normalization).
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["es_update_team_aliases"], body={"groups": groups}
            )
            saved = result.get("groups", []) if isinstance(result, dict) else []
            term_count = sum(len(g.get("terms") or []) for g in saved)
            if not saved:
                return "Team-alias dictionary cleared (0 groups)."
            return (
                f"Team-alias dictionary saved: {len(saved)} group(s), "
                f"{term_count} term(s).\n" + _format_groups(saved)
            )
        except Exception as e:
            logger.error("[MCP] update_event_sync_team_aliases failed: %s", e)
            return f"Error updating team aliases: {e}"
