"""Sync-target CRUD tools (enhancedchannelmanager-jcj0f).

Wraps backend/routers/sync_targets.py — cross-instance live-sync
destinations (a SyncTarget is a remote Dispatcharr/ECM instance this
instance can push config to, epic i39wu). Mirrors the cloud-target CRUD
posture: credentials are Fernet-encrypted at rest and every response masks
them (last-4 chars only) — these tools never decrypt or otherwise echo a
full credential value. There is no sync-target connection-test endpoint on
the backend (unlike cloud-targets) — a live sync run is the only way to
validate reachability; the sync execution engine's SSRF gate runs at that
point (see routers/sync_targets.py module docstring).
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_sync_targets() -> str:
        """List all configured sync targets (cross-instance live-sync destinations)."""
        try:
            client = get_ecm_client()
            targets = await client.call_endpoint(ENDPOINTS["sync_list_targets"])

            if not targets:
                return "No sync targets configured."

            lines = [f"Sync Targets ({len(targets)}):"]
            for t in targets:
                name = t.get("name", "?")
                tid = t.get("id", "?")
                enabled = "enabled" if t.get("enabled") else "disabled"
                url = t.get("base_url", "?")
                outcome = t.get("last_outcome")
                outcome_str = f", last_outcome={outcome}" if outcome else ""
                lines.append(f"  {name} (id={tid}) — {enabled}, {url}{outcome_str}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_sync_targets failed: %s", e)
            return f"Error listing sync targets: {e}"

    @mcp.tool()
    async def create_sync_target(
        name: str,
        base_url: str,
        credentials: dict | None = None,
        enabled: bool = True,
        insecure: bool = False,
        fuzzy_stream_matching: bool = False,
        sync_logos: bool = False,
    ) -> str:
        """Create a sync target — a remote instance this instance can push
        config to.

        Credentials are encrypted at rest and NEVER echoed back in full —
        only a masked (last-4-chars) preview is shown, per the backend's
        response shape.

        Args:
            name: Target name (must be unique).
            base_url: The remote instance's base URL (http/https only;
                config-time format check only — see backend module
                docstring for why DNS isn't resolved here).
            credentials: Auth credentials for the remote instance.
            enabled: Whether this target is available for sync runs.
            insecure: Skip TLS certificate verification (not recommended).
            fuzzy_stream_matching: Use fuzzy matching when reconciling
                streams against the remote instance.
            sync_logos: Opt this target into the logo replication slice
                (default off — logos then stream to the remote one missed
                file at a time; the sync path never bulk-deletes the
                remote's logos).
        """
        try:
            client = get_ecm_client()
            body: dict = {
                "name": name,
                "base_url": base_url,
                "enabled": enabled,
                "insecure": insecure,
                "fuzzy_stream_matching": fuzzy_stream_matching,
                "sync_logos": sync_logos,
            }
            if credentials is not None:
                body["credentials"] = credentials

            result = await client.call_endpoint(ENDPOINTS["sync_create_target"], body=body)
            tid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Sync target created: '{rname}' (id={tid})"
        except Exception as e:
            logger.error("[MCP] create_sync_target failed: %s", e)
            return f"Error creating sync target: {e}"

    @mcp.tool()
    async def update_sync_target(
        target_id: int,
        name: str | None = None,
        base_url: str | None = None,
        credentials: dict | None = None,
        enabled: bool | None = None,
        insecure: bool | None = None,
        fuzzy_stream_matching: bool | None = None,
        sync_logos: bool | None = None,
    ) -> str:
        """Update a sync target — only provided fields change.

        Credentials, if provided, are re-encrypted wholesale (the new dict
        REPLACES the stored one). The backend bumps credential_version only
        when credentials are actually written — a rename or enable-toggle
        does not.

        Args: same semantics as create_sync_target; omit any field you
            don't want to change.
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if base_url is not None:
                payload["base_url"] = base_url
            if credentials is not None:
                payload["credentials"] = credentials
            if enabled is not None:
                payload["enabled"] = enabled
            if insecure is not None:
                payload["insecure"] = insecure
            if fuzzy_stream_matching is not None:
                payload["fuzzy_stream_matching"] = fuzzy_stream_matching
            if sync_logos is not None:
                payload["sync_logos"] = sync_logos

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["sync_update_target"], path_args={"target_id": target_id}, body=payload,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Sync target {target_id} updated: name='{rname}'."
        except Exception as e:
            logger.error("[MCP] update_sync_target failed: %s", e)
            return f"Error updating sync target {target_id}: {e}"

    @mcp.tool()
    async def delete_sync_target(target_id: int, confirm: bool = False) -> str:
        """Delete a sync target.

        CONFIRM GATING (bd-onazy convention): the first call (confirm=False,
        the default) fetches the target and returns a preview naming it —
        deletes NOTHING. Re-invoke with confirm=True to actually delete.

        Args:
            target_id: The sync target ID to delete.
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                target = await client.call_endpoint(
                    ENDPOINTS["sync_get_target"], path_args={"target_id": target_id}
                )
                name = target.get("name", "?") if isinstance(target, dict) else "?"
                url = target.get("base_url", "?") if isinstance(target, dict) else "?"
                return (
                    f"Sync target {target_id} '{name}' ({url}) will be deleted. "
                    f"Re-invoke with confirm=True to delete."
                )
            await client.call_endpoint(
                ENDPOINTS["sync_delete_target"], path_args={"target_id": target_id}
            )
            return f"Sync target {target_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_sync_target failed: %s", e)
            return f"Error deleting sync target {target_id}: {e}"
