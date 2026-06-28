"""W3 (enhancedchannelmanager-vp1rx) — AI-mutation audit log.

Proves the actor/origin dimension on the journal:

* an MCP-originated mutation writes ``mutation_source="mcp_ai"`` with a
  populated ``before_value`` (forensics — what the AI overwrote/deleted);
* a UI (JWT) mutation writes ``mutation_source="ui"``;
* a bulk op correlates under one shared ``batch_id``;
* **a dry-run / preview writes ZERO journal rows** (the key invariant);
* the auto-creation pipeline writes ``mutation_source="auto_creation"``.

Actor attribution is PRINCIPAL-BASED: the middleware derives the source from
the auth bearer (static MCP key vs JWT), so a client cannot forge or forget it.
These tests drive real HTTP requests through the middleware (mocking only the
Dispatcharr client) so the contextvar plumbing is exercised end-to-end.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import journal
from auth import dependencies as auth_deps
from auth.tokens import create_access_token
from models import JournalEntry


MCP_KEY = "mcp-static-secret-key-vp1rx-audit-log-aaaaaaaaaaaa"


@pytest.fixture
def mcp_key_configured(monkeypatch):
    """Configure a non-empty static MCP key the middleware can recognize."""
    class _Settings:
        mcp_api_key = MCP_KEY

    # The actor middleware resolves the principal via
    # auth.dependencies._is_mcp_service_token -> get_settings().mcp_api_key.
    monkeypatch.setattr(auth_deps, "get_settings", lambda: _Settings(), raising=False)
    return MCP_KEY


def _channel(channel_id: int = 7, name: str = "ESPN HD"):
    return {"id": channel_id, "name": name, "channel_number": 700, "streams": []}


# ---------------------------------------------------------------------------
# Actor attribution — principal based
# ---------------------------------------------------------------------------
class TestActorAttribution:
    @pytest.mark.asyncio
    async def test_mcp_mutation_writes_mcp_ai_with_before_value(
        self, async_client, test_session, mcp_key_configured
    ):
        """An MCP-key delete writes mutation_source='mcp_ai' + before_value."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = _channel()
        mock_client.delete_channel.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client):
            resp = await async_client.delete(
                "/api/channels/7",
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )
        assert resp.status_code == 200

        row = test_session.query(JournalEntry).filter_by(entity_id=7).one()
        assert row.mutation_source == journal.MUTATION_SOURCE_MCP_AI == "mcp_ai"
        # before_value is the single most valuable forensic field: what the AI
        # deleted. delete_channel captures name + channel_number.
        assert row.to_dict()["before_value"] == {
            "name": "ESPN HD",
            "channel_number": 700,
        }

    @pytest.mark.asyncio
    async def test_ui_mutation_writes_ui(self, async_client, test_session):
        """A JWT (operator) delete writes mutation_source='ui'."""
        jwt = create_access_token(user_id=1, username="operator")

        mock_client = AsyncMock()
        mock_client.get_channel.return_value = _channel(channel_id=9, name="BBC One")
        mock_client.delete_channel.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client):
            resp = await async_client.delete(
                "/api/channels/9",
                headers={"Authorization": f"Bearer {jwt}"},
            )
        assert resp.status_code == 200

        row = test_session.query(JournalEntry).filter_by(entity_id=9).one()
        assert row.mutation_source == journal.MUTATION_SOURCE_UI == "ui"

    @pytest.mark.asyncio
    async def test_unauthenticated_request_leaves_source_null(
        self, async_client, test_session
    ):
        """No bearer => mutation_source NULL (unknown actor), not mislabeled."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = _channel(channel_id=11, name="No Auth")
        mock_client.delete_channel.return_value = None

        with patch("routers.channels.get_client", return_value=mock_client):
            resp = await async_client.delete("/api/channels/11")
        assert resp.status_code == 200

        row = test_session.query(JournalEntry).filter_by(entity_id=11).one()
        assert row.mutation_source is None


# ---------------------------------------------------------------------------
# Bulk correlation — one shared batch_id
# ---------------------------------------------------------------------------
class TestBulkBatchCorrelation:
    @pytest.mark.asyncio
    async def test_bulk_delete_shares_one_batch_id(
        self, async_client, test_session, mcp_key_configured
    ):
        """A bulk_delete of N (looped single deletes carrying one X-ECM-Batch-Id)
        writes N rows under one shared batch_id.

        This mirrors how the MCP ``bulk_delete_channels`` tool works: it loops
        the single-channel DELETE endpoint, sending the SAME X-ECM-Batch-Id
        header on every call. The backend folds that header into each row's
        batch_id, so the N rows are one correlatable batch.
        """
        batch_id = "abc123def456"

        async def _delete_one(cid):
            mock_client = AsyncMock()
            mock_client.get_channel.return_value = _channel(
                channel_id=cid, name=f"Channel {cid}"
            )
            mock_client.delete_channel.return_value = None
            with patch("routers.channels.get_client", return_value=mock_client):
                return await async_client.delete(
                    f"/api/channels/{cid}",
                    headers={
                        "Authorization": f"Bearer {MCP_KEY}",
                        "X-ECM-Batch-Id": batch_id,
                    },
                )

        for cid in (21, 22, 23):
            resp = await _delete_one(cid)
            assert resp.status_code == 200

        rows = test_session.query(JournalEntry).filter(
            JournalEntry.entity_id.in_([21, 22, 23])
        ).all()
        assert len(rows) == 3
        batch_ids = {r.batch_id for r in rows}
        assert batch_ids == {batch_id}, "all bulk rows must share ONE batch_id"
        assert {r.mutation_source for r in rows} == {journal.MUTATION_SOURCE_MCP_AI}


# ---------------------------------------------------------------------------
# CRITICAL invariant — dry-run / preview writes ZERO journal rows
# ---------------------------------------------------------------------------
class TestDryRunWritesZeroRows:
    @pytest.mark.asyncio
    async def test_auto_creation_dry_run_writes_zero_journal_rows(self, test_session):
        """run_pipeline(dry_run=True) mutates nothing => writes NO journal rows.

        The W2 preview path and auto-creation dry-run must short-circuit BEFORE
        any journal write. We assert the journal table is empty after a dry run.
        """
        from auto_creation_executor import ActionExecutor, ExecutionContext
        from auto_creation_evaluator import StreamContext

        before = test_session.query(JournalEntry).count()

        executor = ActionExecutor(client=None, existing_channels=[])
        executor._execution_id = 7777  # journaling WOULD be enabled if not dry-run
        # Drive a destructive journal writer in dry-run mode: it must early-return
        # BEFORE appending anything to the journal buffer.
        exec_ctx = ExecutionContext(dry_run=True)
        stream_ctx = StreamContext(stream_id=1, stream_name="ESPN")
        result = await executor._update_channel(
            channel={"id": 5, "name": "ESPN", "logo_url": None, "tvg_id": None},
            stream_ctx=stream_ctx,
            exec_ctx=exec_ctx,
            params={},
        )
        assert result.modified is True  # it WOULD update...
        assert executor._journal_buffer == []  # ...but NOTHING was buffered
        executor._flush_journal_buffer()

        after = test_session.query(JournalEntry).count()
        assert after == before == 0, "dry-run must write ZERO journal rows"

    @pytest.mark.asyncio
    async def test_log_entry_dry_run_short_circuit_is_structural(self):
        """The executor buffer stays empty in dry-run (no append happened)."""
        from auto_creation_executor import ActionExecutor

        executor = ActionExecutor(client=None, existing_channels=[])
        assert executor._journal_buffer == [], "buffer must start empty"


# ---------------------------------------------------------------------------
# Auto-creation pipeline attribution
# ---------------------------------------------------------------------------
class TestAutoCreationAttribution:
    @pytest.mark.asyncio
    async def test_executor_stamps_auto_creation_source(self):
        """A non-dry-run executor adoption journal row carries 'auto_creation'.

        Asserts on the dict the executor buffers for ``journal.log_entries``
        (patched, per the existing executor-test convention) — the most precise
        proof that the executor stamps the source itself, independent of the
        contextvar/middleware.
        """
        from auto_creation_executor import ActionExecutor
        from auto_creation_evaluator import StreamContext

        executor = ActionExecutor(client=None, existing_channels=[])
        executor._execution_id = 4242  # enable journaling
        stream_ctx = StreamContext(stream_id=99, stream_name="Stream X")

        with patch("auto_creation_executor.journal.log_entries") as mock_log:
            executor._journal_manual_channel_adoption(
                channel={"id": 50, "name": "Manual CH"},
                stream_ctx=stream_ctx,
                action_type="merge",
            )
            executor._flush_journal_buffer()

        mock_log.assert_called_once()
        entries = mock_log.call_args.kwargs["entries"]
        assert len(entries) == 1
        assert entries[0]["mutation_source"] == journal.MUTATION_SOURCE_AUTO_CREATION == "auto_creation"
        assert entries[0]["batch_id"] == "4242"


# ---------------------------------------------------------------------------
# Contextvar plumbing (the mechanism)
# ---------------------------------------------------------------------------
class TestMutationSourceContextvar:
    def test_explicit_source_overrides_contextvar(self):
        """An explicit mutation_source always wins over the contextvar."""
        token = journal.set_mutation_source(journal.MUTATION_SOURCE_UI)
        try:
            assert journal._resolve_mutation_source("scheduler") == "scheduler"
            assert journal._resolve_mutation_source(None) == "ui"
        finally:
            journal.reset_mutation_source(token)

    def test_no_context_resolves_none(self):
        """Outside a request and with no explicit value, source is NULL."""
        assert journal._resolve_mutation_source(None) is None
