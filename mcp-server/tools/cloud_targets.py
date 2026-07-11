"""Cloud-target tools.

The Export tab's playlist-profile / generate / publish tools were removed with
the tab (beads vrrxv / 1w428). ``list_cloud_targets`` remains because cloud
storage targets are still load-bearing for DBAS backup uploads. The backing
endpoint was relocated from ``/api/export/cloud-targets`` to the dedicated
``/api/cloud-targets`` router with the Export tab's removal.

enhancedchannelmanager-jcj0f added create/update/delete/test — the surface was
list-only before. Credentials are Fernet-encrypted at rest on the backend and
every response masks them (last-4 chars only, see
``routers/cloud_targets.py::_mask_credentials``); these tools never decrypt or
otherwise echo a full credential value — they only ever forward what the
caller supplies (to the encrypted-write endpoints) and surface whatever the
backend's already-masked response contains.
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_cloud_targets() -> str:
        """List configured cloud storage targets (DBAS backup upload destinations)."""
        try:
            client = get_ecm_client()
            targets = await client.call_endpoint(ENDPOINTS["cloud_list_targets"])

            if not targets:
                return "No cloud targets configured."

            lines = [f"Found {len(targets)} cloud targets:"]
            for t in targets:
                name = t.get("name", "Unknown")
                tid = t.get("id", "?")
                ttype = t.get("type", t.get("provider", "unknown"))
                lines.append(f"  {name} (id={tid}) — {ttype}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_cloud_targets failed: %s", e)
            return f"Error listing cloud targets: {e}"

    @mcp.tool()
    async def create_cloud_target(
        name: str,
        provider_type: str,
        credentials: dict,
        upload_path: str = "/",
        enabled: bool = True,
    ) -> str:
        """Create a cloud storage target (a DBAS backup upload destination).

        Credentials are encrypted at rest and NEVER echoed back in full —
        only a masked (last-4-chars) preview is shown, per the backend's
        response shape. Use test_cloud_target with inline credentials to
        validate them BEFORE creating the target.

        Args:
            name: Target name (must be unique).
            provider_type: One of "s3", "gdrive", "onedrive", "dropbox".
            credentials: Provider-specific credential dict (e.g. access_key/
                secret_key for s3).
            upload_path: Path/prefix within the target to upload backups to.
            enabled: Whether this target is available for use.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["cloud_create_target"],
                body={
                    "name": name,
                    "provider_type": provider_type,
                    "credentials": credentials,
                    "upload_path": upload_path,
                    "enabled": enabled,
                },
            )
            tid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Cloud target created: '{rname}' (id={tid}, provider={provider_type})"
        except Exception as e:
            logger.error("[MCP] create_cloud_target failed: %s", e)
            return f"Error creating cloud target: {e}"

    @mcp.tool()
    async def update_cloud_target(
        target_id: int,
        name: str | None = None,
        provider_type: str | None = None,
        credentials: dict | None = None,
        upload_path: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        """Update a cloud storage target — only provided fields change.

        Credentials, if provided, are re-encrypted wholesale (the new dict
        REPLACES the stored one; it is not merged field-by-field).

        Args:
            target_id: The cloud target ID to update.
            name: New name.
            provider_type: New provider type.
            credentials: New credential dict (replaces the stored one).
            upload_path: New upload path/prefix.
            enabled: Whether this target is available for use.
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if provider_type is not None:
                payload["provider_type"] = provider_type
            if credentials is not None:
                payload["credentials"] = credentials
            if upload_path is not None:
                payload["upload_path"] = upload_path
            if enabled is not None:
                payload["enabled"] = enabled

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["cloud_update_target"], path_args={"target_id": target_id}, body=payload,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Cloud target {target_id} updated: name='{rname}'."
        except Exception as e:
            logger.error("[MCP] update_cloud_target failed: %s", e)
            return f"Error updating cloud target {target_id}: {e}"

    @mcp.tool()
    async def delete_cloud_target(target_id: int, confirm: bool = False) -> str:
        """Delete a cloud storage target.

        CONFIRM GATING (bd-onazy convention): the first call (confirm=False,
        the default) looks up the target by name/provider and returns a
        preview — deletes NOTHING. Re-invoke with confirm=True to actually
        delete. A target in active use for DBAS backup uploads will make
        those backups fail until reconfigured.

        Args:
            target_id: The cloud target ID to delete.
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                targets = await client.call_endpoint(ENDPOINTS["cloud_list_targets"])
                match = next((t for t in (targets or []) if t.get("id") == target_id), None)
                if not match:
                    return f"Cloud target {target_id} not found."
                name = match.get("name", "?")
                provider = match.get("provider_type", match.get("type", "?"))
                return (
                    f"Cloud target {target_id} '{name}' ({provider}) will be deleted. "
                    f"Re-invoke with confirm=True to delete."
                )
            await client.call_endpoint(
                ENDPOINTS["cloud_delete_target"], path_args={"target_id": target_id}
            )
            return f"Cloud target {target_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_cloud_target failed: %s", e)
            return f"Error deleting cloud target {target_id}: {e}"

    @mcp.tool()
    async def test_cloud_target(
        target_id: int | None = None,
        provider_type: str | None = None,
        credentials: dict | None = None,
    ) -> str:
        """Test connectivity to a cloud storage target.

        Two modes:
          - Saved target: pass target_id to test an already-configured
            target (the backend uses its stored, decrypted credentials).
          - Inline: pass provider_type + credentials to test BEFORE saving
            — pairs naturally with create_cloud_target.

        Args:
            target_id: ID of a saved cloud target to test.
            provider_type: Provider type for an inline test (required if
                target_id is omitted).
            credentials: Credential dict for an inline test (required if
                target_id is omitted).
        """
        try:
            client = get_ecm_client()
            if target_id is not None:
                result = await client.call_endpoint(
                    ENDPOINTS["cloud_test_target"], path_args={"target_id": target_id}
                )
            else:
                if not provider_type or credentials is None:
                    return (
                        "Provide either target_id, or both provider_type and "
                        "credentials for an inline test."
                    )
                result = await client.call_endpoint(
                    ENDPOINTS["cloud_test_target_inline"],
                    body={"provider_type": provider_type, "credentials": credentials},
                )

            success = result.get("success") if isinstance(result, dict) else False
            message = result.get("message", "") if isinstance(result, dict) else ""
            status = "SUCCESS" if success else "FAILED"
            return f"Connection test {status}: {message}"
        except Exception as e:
            logger.error("[MCP] test_cloud_target failed: %s", e)
            return f"Error testing cloud target: {e}"
