"""Channel CSV export/import/preview tools (enhancedchannelmanager-l3jf7).

Wraps backend/routers/channels.py CSV endpoints (docs/api.md "CSV" section).
Two of the three backend endpoints are NOT plain-JSON: ``export-csv`` returns
a raw ``text/csv`` file download and ``import-csv`` takes a
``multipart/form-data`` upload — ``call_endpoint``/``ENDPOINTS`` only model
JSON-body endpoints, so those two stay on ``ecm_client.get_text`` /
``post_multipart`` directly (contract-exempt, see inline comments).
``preview-csv`` is a normal JSON body and is registered in
``_endpoint_contracts.py``.
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)

# Char cap on the CSV text a single MCP tool response embeds — keeps the
# response within a sane LLM context budget. The backend export endpoint has
# no pagination, so an over-cap export is truncated at the last complete
# line rather than silently cut mid-row.
_EXPORT_CSV_CHAR_CAP = 200_000


def register(mcp: FastMCP):
    @mcp.tool()
    async def export_channels_csv() -> str:
        """Export all manually-created channels to CSV (bd-l3jf7).

        Auto-created channels are excluded (mirrors the ECM UI's CSV
        export). Returns the raw CSV text: header row plus one row per
        channel (channel_number, name, group_name, tvg_id, gracenote_id,
        logo_url, stream_urls — stream_urls is ';'-joined). There is no
        pagination on this endpoint; if the export exceeds ~200KB it is
        truncated at the last complete line and a warning is appended —
        narrow your channel set (e.g. filter by group) and re-export if you
        need the complete data in one call.
        """
        try:
            client = get_ecm_client()
            # contract-exempt: text/csv file-download response, not JSON —
            # call_endpoint only models JSON-body endpoints.
            csv_text = await client.get_text("/api/channels/export-csv", timeout=120.0)

            if len(csv_text) <= _EXPORT_CSV_CHAR_CAP:
                return csv_text

            truncated = csv_text[:_EXPORT_CSV_CHAR_CAP]
            last_newline = truncated.rfind("\n")
            if last_newline > 0:
                truncated = truncated[:last_newline + 1]
            total_lines = csv_text.count("\n") + (0 if csv_text.endswith("\n") else 1)
            kept_lines = truncated.count("\n")
            return (
                f"{truncated}"
                f"# TRUNCATED: showing {kept_lines} of {total_lines} row(s) "
                f"({_EXPORT_CSV_CHAR_CAP:,} char cap reached). Full export is "
                f"{len(csv_text):,} chars — narrow your channel set (filter by "
                f"group) to get a complete export in a single call."
            )
        except Exception as e:
            logger.error("[MCP] export_channels_csv failed: %s", e)
            return f"Error exporting channels CSV: {e}"

    @mcp.tool()
    async def preview_channels_csv(content: str) -> str:
        """Preview and validate CSV content before importing it (bd-l3jf7).

        ALWAYS run this before import_channels_csv (which also runs this
        same preview internally before you confirm) — it parses the content
        exactly the way import will, and surfaces row-level errors without
        touching any data.

        Args:
            content: Raw CSV text (e.g. from export_channels_csv, or
                hand-built following the channel-import CSV format:
                channel_number, name, group_name, tvg_id, gracenote_id,
                logo_url, stream_urls).
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["channels_preview_csv"], body={"content": content}
            )
            rows = result.get("rows", []) if isinstance(result, dict) else []
            errors = result.get("errors", []) if isinstance(result, dict) else []

            lines = [f"Preview: {len(rows)} row(s) parsed, {len(errors)} error(s)."]
            if errors:
                lines.append("Errors:")
                for err in errors[:20]:
                    row_no = err.get("row", "?") if isinstance(err, dict) else "?"
                    msg = err.get("error", err) if isinstance(err, dict) else err
                    lines.append(f"  Row {row_no}: {msg}")
                if len(errors) > 20:
                    lines.append(f"  ... and {len(errors) - 20} more error(s)")
            if rows:
                lines.append("Sample rows:")
                for row in rows[:5]:
                    name = row.get("name", "?") if isinstance(row, dict) else row
                    group = row.get("group_name", "") if isinstance(row, dict) else ""
                    lines.append(f"  {name} ({group})" if group else f"  {name}")
                if len(rows) > 5:
                    lines.append(f"  ... and {len(rows) - 5} more row(s)")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] preview_channels_csv failed: %s", e)
            return f"Error previewing channels CSV: {e}"

    @mcp.tool()
    async def import_channels_csv(
        content: str, confirm: bool = False, filename: str = "import.csv"
    ) -> str:
        """Import channels from CSV content (bd-l3jf7).

        BULK-CREATE: creates channels — and channel groups as needed — from
        every valid row. ALWAYS run preview_channels_csv on the same
        content FIRST to review row-level errors before importing; this
        tool's own confirm=False preview only reports counts, not detail.

        CONFIRM GATING (bd-onazy convention): the first call (confirm=False,
        the default) parses the content the same way preview_channels_csv
        does and returns row/error counts — it imports NOTHING. Re-invoke
        with confirm=True to actually import.

        Args:
            content: Raw CSV text to import (same format as
                export_channels_csv).
            confirm: Set True on the second call to perform the import.
            filename: Filename reported to the backend (cosmetic only, no
                effect on parsing).
        """
        try:
            client = get_ecm_client()
            if not confirm:
                preview = await client.call_endpoint(
                    ENDPOINTS["channels_preview_csv"], body={"content": content}
                )
                rows = preview.get("rows", []) if isinstance(preview, dict) else []
                errors = preview.get("errors", []) if isinstance(preview, dict) else []
                err_note = f", {len(errors)} error(s) found" if errors else ""
                return (
                    f"This will import {len(rows)} channel(s){err_note}. "
                    f"Run preview_channels_csv first if you haven't already, "
                    f"to review row-level detail. Re-invoke with confirm=True "
                    f"to import."
                )

            # contract-exempt: multipart/form-data upload, not a JSON body —
            # call_endpoint only models JSON-body endpoints.
            result = await client.post_multipart(
                "/api/channels/import-csv",
                files={"file": (filename, content.encode("utf-8"), "text/csv")},
                timeout=300.0,
            )
            created = result.get("channels_created", 0) if isinstance(result, dict) else 0
            groups = result.get("groups_created", 0) if isinstance(result, dict) else 0
            linked = result.get("streams_linked", 0) if isinstance(result, dict) else 0
            errors = result.get("errors", []) if isinstance(result, dict) else []
            lines = [
                f"Import complete: {created} channel(s) created, "
                f"{groups} group(s) created, {linked} stream(s) linked."
            ]
            if errors:
                lines.append(f"{len(errors)} error(s):")
                for err in errors[:20]:
                    lines.append(f"  {err}")
                if len(errors) > 20:
                    lines.append(f"  ... and {len(errors) - 20} more error(s)")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] import_channels_csv failed: %s", e)
            return f"Error importing channels CSV: {e}"
