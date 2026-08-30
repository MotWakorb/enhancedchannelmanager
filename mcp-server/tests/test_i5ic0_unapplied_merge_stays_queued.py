"""The MCP accept tool relays the queue state the backend reported.

Bead ``enhancedchannelmanager-i5ic0``, PO decision 2026-08-16. A merge ECM
could not apply no longer leaves the queue: the row stays ``pending``, carries
``unapplied_reason``, and a later accept on it is an ordinary accept rather
than an idempotent replay.

Two things follow for the MCP surface, and both are about an agent's model of
what happened:

1. ``accept_channel_merge`` must not manufacture ``status: 'merged'``. It used
   to inject that whenever the backend omitted the field, which would now
   relabel an outcome that is explicitly still queued as terminal — the same
   false success claim the bead is about, one layer out.
2. ``list_pending_channel_merges`` returns flagged rows, because they are
   ``pending``. An agent must be able to tell a flagged row from a fresh one,
   which is what ``unapplied_reason`` on the row is for.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _parse(result):
    """FastMCP returns (content, structured) or a content list; take the JSON."""
    payload = result[0] if isinstance(result, tuple) else result
    if isinstance(payload, dict):
        return payload
    return json.loads(payload[0].text)


def _make_client_mock(call_endpoint):
    client = MagicMock()
    client.call_endpoint = AsyncMock(return_value=call_endpoint)
    return client


async def _accept(backend_response):
    from mcp.server.fastmcp import FastMCP

    from tools.dedup import register

    mcp = FastMCP("test")
    register(mcp)
    client = _make_client_mock(backend_response)
    with patch("tools.dedup.get_ecm_client", return_value=client):
        return _parse(await mcp.call_tool("accept_channel_merge", {"merge_id": 1}))


UNAPPLIED_ENVELOPE = {
    "merged_into_channel_id": "uuid-abc",
    "journal_entry_id": 99,
    "source_stream_id": "ESPN HD",
    "confidence": 0.92,
    "status": "pending",
    "dispatcharr_updated": False,
    "unapplied_reason": 'No Dispatcharr stream is named "ESPN HD".',
    "journal_rows_unwritten": 0,
}


class TestTheToolRelaysTheQueueState:

    @pytest.mark.asyncio
    async def test_an_unapplied_accept_is_not_relabelled_as_merged(self):
        """The row is still queued and the agent has to be told so."""
        data = await _accept(dict(UNAPPLIED_ENVELOPE))

        assert data["status"] == "pending"
        assert data["dispatcharr_updated"] is False
        assert data["unapplied_reason"] == UNAPPLIED_ENVELOPE["unapplied_reason"]

    @pytest.mark.asyncio
    async def test_an_applied_accept_still_reports_merged(self):
        """The other direction, or the field carries no information."""
        applied = dict(UNAPPLIED_ENVELOPE)
        applied.update(
            status="merged", dispatcharr_updated=True, unapplied_reason=None,
        )

        data = await _accept(applied)

        assert data["status"] == "merged"
        assert data["dispatcharr_updated"] is True

    @pytest.mark.asyncio
    async def test_a_missing_status_is_not_invented(self):
        """Absent means absent.

        The injection existed for a backend that never sent the field. It does
        send it, and a default of 'merged' is now a claim about the queue that
        can be false.
        """
        without_status = {
            k: v for k, v in UNAPPLIED_ENVELOPE.items() if k != "status"
        }

        data = await _accept(without_status)

        assert "status" not in data


class TestTheToolContractDocumentsTheFlaggedRow:
    """The docstrings ARE the agent's contract — nothing else reaches it."""

    def _doc(self, tool_name):
        from mcp.server.fastmcp import FastMCP

        from tools.dedup import register

        mcp = FastMCP("test")
        register(mcp)
        # Whitespace-normalised: the source wraps these sentences across lines,
        # so a raw substring check silently passes on a phrase that IS present
        # (measured — the pre-fix docstring "passed" this test).
        return " ".join(
            mcp._tool_manager.get_tool(tool_name).description.split()
        )

    def test_accept_does_not_promise_a_terminal_queue_row(self):
        doc = self._doc("accept_channel_merge")
        assert "always reaches a terminal state" not in doc
        assert "unapplied_reason" in doc
        # The recovery an agent should take is a RETRY of the same row, not a
        # dismiss — the row is still there to retry.
        assert "stays in the queue" in doc
        assert "retry the same merge_id" in doc

    def test_list_documents_the_flag_on_the_row(self):
        doc = self._doc("list_pending_channel_merges")
        assert "unapplied_reason" in doc
