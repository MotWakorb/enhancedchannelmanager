"""Tags CRUD tools (enhancedchannelmanager-dswrl).

Wraps backend/routers/tags.py — tag groups + tags used by normalization
conditions (tag_group_id/tag_match_position) and channel-pipeline rule
matching. ``export``/``import`` (YAML round-trip) are intentionally NOT
exposed here — out of scope for this bead, same class of deferral as the
dummy-EPG profile YAML export/import.
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_tag_groups() -> str:
        """List all tag groups with their tag counts."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["tags_list_groups"])
            groups = result.get("groups", []) if isinstance(result, dict) else (result or [])

            if not groups:
                return "No tag groups configured."

            lines = [f"Tag Groups ({len(groups)}):"]
            for g in groups:
                name = g.get("name", "?")
                gid = g.get("id", "?")
                count = g.get("tag_count", 0)
                builtin = " [builtin]" if g.get("is_builtin") else ""
                lines.append(f"  {name} (id={gid}){builtin} — {count} tag(s)")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_tag_groups failed: %s", e)
            return f"Error listing tag groups: {e}"

    @mcp.tool()
    async def create_tag_group(name: str, description: str | None = None) -> str:
        """Create a new tag group.

        Args:
            name: Tag group name (must be unique).
            description: Optional description.
        """
        try:
            client = get_ecm_client()
            body: dict = {"name": name}
            if description is not None:
                body["description"] = description

            result = await client.call_endpoint(ENDPOINTS["tags_create_group"], body=body)
            gid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Tag group created: '{rname}' (id={gid})"
        except Exception as e:
            logger.error("[MCP] create_tag_group failed: %s", e)
            return f"Error creating tag group: {e}"

    @mcp.tool()
    async def update_tag_group(
        group_id: int, name: str | None = None, description: str | None = None,
    ) -> str:
        """Update a tag group's name and/or description. Built-in groups
        cannot be renamed (the backend rejects it).

        Args:
            group_id: The tag group ID to update.
            name: New name.
            description: New description.
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if description is not None:
                payload["description"] = description

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["tags_update_group"], path_args={"group_id": group_id}, body=payload,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Tag group {group_id} updated: name='{rname}'."
        except Exception as e:
            logger.error("[MCP] update_tag_group failed: %s", e)
            return f"Error updating tag group {group_id}: {e}"

    @mcp.tool()
    async def delete_tag_group(group_id: int, confirm: bool = False) -> str:
        """Delete a tag group. CASCADES: deletes every tag in the group too.

        Built-in tag groups cannot be deleted (backend refuses).

        CONFIRM GATING (bd-onazy convention): the first call (confirm=False,
        the default) fetches the group and returns a preview naming it and
        the number of tags that would be cascade-deleted — deletes NOTHING.
        Re-invoke with confirm=True to actually delete.

        Args:
            group_id: The tag group ID to delete.
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                group = await client.call_endpoint(
                    ENDPOINTS["tags_get_group"], path_args={"group_id": group_id}
                )
                name = group.get("name", "?") if isinstance(group, dict) else "?"
                is_builtin = bool(group.get("is_builtin")) if isinstance(group, dict) else False
                tags = group.get("tags", []) if isinstance(group, dict) else []
                if is_builtin:
                    return f"Tag group {group_id} '{name}' is built-in — cannot be deleted."
                return (
                    f"Tag group {group_id} '{name}' and its {len(tags)} tag(s) will be deleted. "
                    f"Re-invoke with confirm=True to delete."
                )
            result = await client.call_endpoint(
                ENDPOINTS["tags_delete_group"], path_args={"group_id": group_id}
            )
            status = result.get("status", "deleted") if isinstance(result, dict) else "deleted"
            return f"Tag group {group_id} {status}."
        except Exception as e:
            logger.error("[MCP] delete_tag_group failed: %s", e)
            return f"Error deleting tag group {group_id}: {e}"

    @mcp.tool()
    async def add_tags_to_group(
        group_id: int, tags: list[str], case_sensitive: bool = False,
    ) -> str:
        """Add one or more tag values to a group. Duplicates (by value,
        within the group) are skipped, not errored.

        Args:
            group_id: The tag group ID to add tags to.
            tags: Tag values to add.
            case_sensitive: Whether matching against these tags is case-sensitive.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["tags_add_to_group"],
                path_args={"group_id": group_id},
                body={"tags": tags, "case_sensitive": case_sensitive},
            )
            created = result.get("created", []) if isinstance(result, dict) else []
            skipped = result.get("skipped", []) if isinstance(result, dict) else []
            lines = [f"Added {len(created)} created, {len(skipped)} skipped (duplicates) to group {group_id}."]
            if created:
                lines.append(f"  Created: {', '.join(created)}")
            if skipped:
                lines.append(f"  Skipped (already exist): {', '.join(skipped)}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] add_tags_to_group failed: %s", e)
            return f"Error adding tags to group {group_id}: {e}"

    @mcp.tool()
    async def update_tag(
        group_id: int,
        tag_id: int,
        enabled: bool | None = None,
        case_sensitive: bool | None = None,
    ) -> str:
        """Update a tag's enabled or case_sensitive status.

        Args:
            group_id: The tag's group ID.
            tag_id: The tag ID to update.
            enabled: Whether the tag is active for matching.
            case_sensitive: Whether matching this tag's value is case-sensitive.
        """
        try:
            client = get_ecm_client()
            payload = {}
            if enabled is not None:
                payload["enabled"] = enabled
            if case_sensitive is not None:
                payload["case_sensitive"] = case_sensitive

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["tags_update_tag"],
                path_args={"group_id": group_id, "tag_id": tag_id},
                body=payload,
            )
            value = result.get("value", "?") if isinstance(result, dict) else "?"
            return f"Tag {tag_id} ('{value}') updated."
        except Exception as e:
            logger.error("[MCP] update_tag failed: %s", e)
            return f"Error updating tag {tag_id}: {e}"

    @mcp.tool()
    async def delete_tag(group_id: int, tag_id: int) -> str:
        """Delete a tag from a group. Built-in tags cannot be deleted
        (backend refuses).

        Args:
            group_id: The tag's group ID.
            tag_id: The tag ID to delete.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["tags_delete_tag"], path_args={"group_id": group_id, "tag_id": tag_id}
            )
            status = result.get("status", "deleted") if isinstance(result, dict) else "deleted"
            return f"Tag {tag_id} {status}."
        except Exception as e:
            logger.error("[MCP] delete_tag failed: %s", e)
            return f"Error deleting tag {tag_id}: {e}"

    @mcp.tool()
    async def test_tag_group(text: str, group_id: int) -> str:
        """Test text against a tag group's enabled tags to find matches.

        Args:
            text: The sample text to test (e.g. a stream or channel name).
            group_id: The tag group to test against.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["tags_test"], body={"text": text, "group_id": group_id}
            )
            matches = result.get("matches", []) if isinstance(result, dict) else []
            group_name = result.get("group_name", "?") if isinstance(result, dict) else "?"

            if not matches:
                return f"No tags matched in group '{group_name}' for: {text!r}"

            lines = [f"{len(matches)} match(es) in group '{group_name}' for: {text!r}"]
            for m in matches:
                value = m.get("value", "?")
                cs = " (case-sensitive)" if m.get("case_sensitive") else ""
                lines.append(f"  {value}{cs}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] test_tag_group failed: %s", e)
            return f"Error testing tag group: {e}"
