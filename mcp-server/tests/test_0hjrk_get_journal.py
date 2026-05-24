"""Regression tests for bd-0hjrk.1 — get_journal pagination + compact recovery
rendering.

Root cause (MCP layer only — the backend data is already complete): the MCP
``get_journal`` tool sent ``query={"page_size": limit}`` and the backend clamps
``page_size`` to 200. The tool fetched ONE page and never looped, so a run with
752 merges was truncated to at most 200 rows — 552 (channel_id, stream_id) pairs
were unreachable for the journal -> bulk_remove_streams recovery path. The
formatter also never surfaced ``entity_id`` (the channel_id) or the
before/after stream-id lists.

This module proves the fixed behaviour:
  (a) a >200-row batch is fully returned by client-side pagination,
  (b) merge_stream rows render channel_id AND the added stream_id,
  (c) ``offset`` skips the first N rows,
  (d) ``include_stream_ids=True`` appends the full before/after lists.

Test patterns mirror tests/test_lq38l_journal_and_nits.py (FastMCP register +
AsyncMock ecm_client patched at tools.system.get_ecm_client).
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_system() -> FastMCP:
    mcp = FastMCP("test")
    from tools.system import register

    register(mcp)
    return mcp


def _merge_row(idx: int, channel_id: int, before_ids: list[int], after_ids: list[int]):
    """Build a merge_stream JournalEntry.to_dict()-shaped row. The added stream
    id is ``set(after) - set(before)``; the description embeds it for the parse
    fallback."""
    added = sorted(set(after_ids) - set(before_ids))
    added_id = added[0] if added else "?"
    return {
        "id": idx,
        "timestamp": "2026-05-22T10:%02d:00Z" % (idx % 60),
        "category": "auto_creation",
        "action_type": "merge_stream",
        "entity_id": channel_id,
        "entity_name": "Channel %s" % channel_id,
        "description": "Merged stream %s into channel 'Channel %s'" % (added_id, channel_id),
        "before_value": {"stream_ids": list(before_ids)},
        "after_value": {"stream_ids": list(after_ids)},
        "batch_id": "19",
    }


def _paginated_backend(total_rows: list[dict], cap: int = 200):
    """Return a ``side_effect`` callable that serves ``total_rows`` as the
    backend GET /api/journal would: newest-first envelopes paginated by the
    requested ``page`` / ``page_size`` (clamped to ``cap``), reporting the true
    total ``count`` / ``total_pages``. Used to verify the client loops pages."""
    count = len(total_rows)

    def _serve(ep, *, query=None, **_kw):
        query = query or {}
        page = int(query.get("page", 1))
        page_size = min(int(query.get("page_size", 50)), cap)
        page_size = max(page_size, 1)
        start = (page - 1) * page_size
        end = start + page_size
        total_pages = (count + page_size - 1) // page_size if count > 0 else 1
        return {
            "count": count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "results": total_rows[start:end],
        }

    return _serve


def _text(result) -> str:
    return result[0][0].text


def _row_lines(text: str) -> list[str]:
    """The rendered rows (lines that begin with the two-space indent), excluding
    the header line."""
    return [ln for ln in text.splitlines() if ln.startswith("  [")]


# ===========================================================================
# (a) A >200-row batch is fully returned — the client loops pages
# ===========================================================================

class TestPaginationOverCap:
    @pytest.mark.asyncio
    async def test_returns_all_752_rows_across_pages(self):
        """752-row batch, limit=1000 → all 752 rendered (the backend cap is 200
        per page, so this REQUIRES looping multiple pages)."""
        rows = [
            _merge_row(i, channel_id=1000 + i, before_ids=[i], after_ids=[i, 5000 + i])
            for i in range(752)
        ]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows, cap=200)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_journal", {"batch_id": "19", "limit": 1000}
            )
        text = _text(result)
        # All 752 rows rendered — none truncated at the 200-row backend cap.
        assert len(_row_lines(text)) == 752
        # Header reports M from the envelope count.
        assert "showing 752 of 752 total" in text
        # It actually paginated: 752 / 200 = 4 page calls (200,200,200,152).
        assert mock_client.call_endpoint.call_count == 4

    @pytest.mark.asyncio
    async def test_limit_below_cap_stops_early(self):
        """limit smaller than the total stops once limit rows are collected and
        does not fetch every page."""
        rows = [
            _merge_row(i, channel_id=1000 + i, before_ids=[i], after_ids=[i, 5000 + i])
            for i in range(752)
        ]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows, cap=200)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_journal", {"batch_id": "19", "limit": 50}
            )
        text = _text(result)
        assert len(_row_lines(text)) == 50
        # M (envelope count) is still the true total.
        assert "showing 50 of 752 total" in text
        # Only one page needed (50 <= 200).
        assert mock_client.call_endpoint.call_count == 1

    @pytest.mark.asyncio
    async def test_page_size_query_never_exceeds_cap(self):
        """Even with a huge limit, each backend call requests page_size <= 200."""
        rows = [
            _merge_row(i, channel_id=1000 + i, before_ids=[i], after_ids=[i, 5000 + i])
            for i in range(500)
        ]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows, cap=200)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            await mcp.call_tool("get_journal", {"batch_id": "19", "limit": 5000})
        for call in mock_client.call_endpoint.call_args_list:
            assert call.kwargs["query"]["page_size"] <= 200

    @pytest.mark.asyncio
    async def test_category_and_batch_id_passed_on_every_page(self):
        """Filters must be threaded through every paginated request, not just
        the first."""
        rows = [
            _merge_row(i, channel_id=1000 + i, before_ids=[i], after_ids=[i, 5000 + i])
            for i in range(450)
        ]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows, cap=200)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "get_journal",
                {"batch_id": "19", "category": "auto_creation", "limit": 1000},
            )
        assert mock_client.call_endpoint.call_count >= 3
        for call in mock_client.call_endpoint.call_args_list:
            q = call.kwargs["query"]
            assert q["batch_id"] == "19"
            assert q["category"] == "auto_creation"


# ===========================================================================
# (b) merge_stream rows render channel_id AND the added stream_id
# ===========================================================================

class TestMergeRowRendering:
    @pytest.mark.asyncio
    async def test_renders_channel_id_and_added_stream_id(self):
        """For a merge_stream row, channel_id=entity_id and stream_id=added id
        (after - before) are surfaced, plus the before->after stream counts."""
        rows = [_merge_row(1, channel_id=42, before_ids=[100, 101], after_ids=[100, 101, 201])]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_journal", {"batch_id": "19", "limit": 20})
        text = _text(result)
        assert "channel_id=42" in text
        assert "stream_id=201" in text
        # before/after counts (2 -> 3) so an operator sees the merge delta.
        assert "streams: 2" in text and "3" in text
        # The category/action and entity_name still render.
        assert "auto_creation/merge_stream" in text
        assert "Channel 42" in text

    @pytest.mark.asyncio
    async def test_added_stream_id_parsed_from_description_when_values_absent(self):
        """Fallback path: no before/after values, but the description embeds the
        merged stream id → parse it out."""
        row = {
            "id": 7,
            "timestamp": "2026-05-22T12:00:00Z",
            "category": "auto_creation",
            "action_type": "merge_stream",
            "entity_id": 55,
            "entity_name": "ESPN",
            "description": "Merged stream 909 into channel 'ESPN'",
            "before_value": None,
            "after_value": None,
            "batch_id": "19",
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend([row])
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_journal", {"batch_id": "19"})
        text = _text(result)
        assert "channel_id=55" in text
        assert "stream_id=909" in text

    @pytest.mark.asyncio
    async def test_non_merge_row_with_entity_id_shows_channel_id_only(self):
        """A non-merge row that has entity_id renders channel_id but no
        stream_id/streams delta."""
        row = {
            "id": 3,
            "timestamp": "2026-05-22T09:00:00Z",
            "category": "channel",
            "action_type": "create",
            "entity_id": 88,
            "entity_name": "CNN",
            "description": "Created channel CNN",
        }
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend([row])
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_journal", {})
        text = _text(result)
        assert "channel_id=88" in text
        assert "stream_id=" not in text
        assert "channel/create" in text


# ===========================================================================
# (c) offset skips the first N rows
# ===========================================================================

class TestOffset:
    @pytest.mark.asyncio
    async def test_offset_skips_leading_rows(self):
        """offset=5 with a 20-row batch returns rows 5..19 (the first 5 skipped),
        newest-first ordering preserved by the backend."""
        rows = [
            _merge_row(i, channel_id=1000 + i, before_ids=[i], after_ids=[i, 5000 + i])
            for i in range(20)
        ]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows, cap=200)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_journal", {"batch_id": "19", "limit": 100, "offset": 5}
            )
        text = _text(result)
        # 15 rows remain after skipping the first 5.
        assert len(_row_lines(text)) == 15
        # Row 0..4 skipped → channel_id 1000..1004 not present; 1005 is.
        assert "channel_id=1004" not in text
        assert "channel_id=1005" in text
        assert "channel_id=1019" in text

    @pytest.mark.asyncio
    async def test_offset_spanning_page_boundary(self):
        """offset that lands inside a later page still drops exactly that many
        rows when looping across the 200-row backend cap."""
        rows = [
            _merge_row(i, channel_id=1000 + i, before_ids=[i], after_ids=[i, 5000 + i])
            for i in range(450)
        ]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows, cap=200)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_journal", {"batch_id": "19", "limit": 1000, "offset": 250}
            )
        text = _text(result)
        # 450 - 250 = 200 rows remain.
        assert len(_row_lines(text)) == 200
        # Row 249 skipped, row 250 is the first kept.
        assert "channel_id=1249" not in text
        assert "channel_id=1250" in text
        assert "channel_id=1449" in text


# ===========================================================================
# (d) include_stream_ids=True appends the full before/after lists
# ===========================================================================

class TestIncludeStreamIds:
    @pytest.mark.asyncio
    async def test_full_lists_appended_when_requested(self):
        rows = [_merge_row(1, channel_id=42, before_ids=[100, 101], after_ids=[100, 101, 201])]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_journal",
                {"batch_id": "19", "limit": 20, "include_stream_ids": True},
            )
        text = _text(result)
        # Full before/after stream-id lists present for deep audit.
        assert "[100, 101]" in text
        assert "[100, 101, 201]" in text

    @pytest.mark.asyncio
    async def test_full_lists_absent_by_default(self):
        """Default (include_stream_ids=False) must NOT dump the full lists — the
        752-row token-budget guard."""
        rows = [_merge_row(1, channel_id=42, before_ids=[100, 101], after_ids=[100, 101, 201])]
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend(rows)
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_journal", {"batch_id": "19", "limit": 20})
        text = _text(result)
        # Compact: counts/added id shown, but not the verbose full before-list.
        assert "[100, 101]" not in text
        assert "stream_id=201" in text


# ===========================================================================
# Empty / no-results path is preserved
# ===========================================================================

class TestEmpty:
    @pytest.mark.asyncio
    async def test_empty_results_reports_no_entries(self):
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _paginated_backend([])
        mcp = _register_system()
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_journal", {"batch_id": "nope"})
        assert "No journal entries found." in _text(result)
