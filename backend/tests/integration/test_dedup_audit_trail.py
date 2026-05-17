"""Cross-trigger §D6 audit-trail integration tests for the dedup flow (bd-etdeb).

Companion to the Playwright spec ``e2e/dedup-flow.spec.ts``. The Playwright
spec proves the operator-visible flow (modal opens, Merge click, channel
state change, post-resolve UI) but cannot inspect the
``pending_merge_journal`` table directly — the journal write is entirely
backend-side. This module is the audit-field proof: for every
``trigger_context`` the v0.17.1 epic ships
(``drag_drop`` / ``add_stream`` / ``m3u_refresh``), accepting a queued
pending-merge row writes a journal entry with ALL SEVEN §D6 fields populated:

* ``actor_token_id`` — opaque token identifier (the User.id or
  the literal ``"anonymous"`` when auth is disabled).
* ``action_type`` — ``merge_confirmed`` for accept.
* ``source_channel_id`` — resolved Dispatcharr stream id when the lookup
  succeeds, falls back to raw ``stream_name`` otherwise (audit-first
  contract; verified in the existing ``test_channel_merges.py`` ambiguous-
  match test, not re-verified here).
* ``target_channel_id`` — the candidate Dispatcharr channel UUID.
* ``confidence_score`` — captured at action time.
* ``timestamp_utc`` — epoch-ms UTC, > 0.
* ``trigger_context`` — the surface tag, mirrored from the pending row.

Existing coverage in ``test_channel_merges.py`` already locks the
happy-path audit-field shape for ``m3u_refresh`` (line 314) and the
dismiss path for ``drag_drop`` (line 601). This module fills the gap:
trigger_context preservation across ALL THREE operator-visible surfaces,
side-by-side, so a regression that breaks one surface's audit-trail
mirror is caught here instead of being discovered later in a
post-mortem of "the journal says drag_drop but the operator was
clicking Add Stream".

Bead spec (bd-etdeb): the journal-audit assertion is acceptable as a
backend integration test (per the bead's explicit allowance) so the
Playwright spec stays focused on the operator-visible UI surface
without round-tripping through database introspection.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from models import PendingMerge, PendingMergeJournal


# ---------------------------------------------------------------------------
# Helpers — mirror the shapes used by test_channel_merges.py so the audit-
# trail assertions stay comparable across the test surface.
# ---------------------------------------------------------------------------
def _make_pending(
    test_session,
    *,
    stream_name: str,
    candidate_channel_id: str,
    confidence: float,
    trigger_context: str,
    group_id: int = 5,
    created_at: int = 1_700_000_000_000,
) -> PendingMerge:
    """Insert a ``PendingMerge`` row stamped with the surface that enqueued it.

    The fixture mirrors what would land in the queue from the matching
    surface:

      * ``drag_drop`` — operator drags a stream into a group, the BD-H
        hook detects a candidate, enqueues the row, and opens the modal.
      * ``add_stream`` — operator promotes a channel-less stream via the
        "Create channel(s) in group" context-menu (BD-I); the
        ``useAddStreamDedup`` hook enqueues and prompts.
      * ``m3u_refresh`` — bulk M3U import hook (BD-F) enqueues silently
        during refresh; operator drains via PendingMergesPage (BD-J).

    All three paths converge on the SAME accept endpoint and journal
    schema — that's the invariant this test locks.
    """
    row = PendingMerge(
        stream_name=stream_name,
        group_id=group_id,
        candidate_channel_id=candidate_channel_id,
        confidence=confidence,
        status="pending",
        created_at=created_at,
        resolved_at=None,
        resolution_source=None,
        trigger_context=trigger_context,
    )
    test_session.add(row)
    test_session.commit()
    test_session.refresh(row)
    return row


def _mock_dispatcharr_resolving(channel_id: str, stream_name: str, stream_id: int) -> AsyncMock:
    """Build an AsyncMock Dispatcharr client that resolves the merge unambiguously.

    Returns a client where ``get_channel`` returns a candidate channel with
    no streams yet, ``get_streams`` resolves the source stream by exact
    name to a single hit, and ``update_channel`` succeeds. This is the
    happy-path resolver the accept endpoint expects when both halves of
    the merge are reachable in Dispatcharr.
    """
    mock_client = AsyncMock()
    mock_client.get_channel.return_value = {
        "id": channel_id,
        "name": "Target Channel",
        "streams": [],
    }
    mock_client.get_streams.return_value = {
        "results": [{"id": stream_id, "name": stream_name}],
    }
    mock_client.update_channel.return_value = {"id": channel_id}
    return mock_client


# ---------------------------------------------------------------------------
# §D6 audit-trail invariants for all three trigger_context values
# ---------------------------------------------------------------------------
class TestAuditTrailCrossTrigger:
    """Every operator-visible accept writes a complete §D6 journal row.

    Parametrised so a regression that breaks one surface's audit mirror
    surfaces as a single targeted failure instead of a generic "audit
    field missing" — the parametrize id tells you which surface broke.
    """

    @pytest.mark.parametrize(
        "trigger_context,stream_name,candidate_channel_id,resolved_stream_id,confidence",
        [
            pytest.param(
                "drag_drop",
                "ESPN HD",
                "ch-uuid-drag",
                4001,
                0.87,
                id="drag_drop_surface",
            ),
            pytest.param(
                "add_stream",
                "CNN HD",
                "ch-uuid-add",
                4002,
                0.92,
                id="add_stream_surface",
            ),
            pytest.param(
                "m3u_refresh",
                "TNT HD",
                "ch-uuid-m3u",
                4003,
                1.00,
                id="m3u_refresh_surface",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_accept_writes_full_audit_set_per_trigger_context(
        self,
        async_client,
        test_session,
        trigger_context: str,
        stream_name: str,
        candidate_channel_id: str,
        resolved_stream_id: int,
        confidence: float,
    ):
        """Accept writes a journal row with all seven §D6 fields, preserving trigger_context."""
        row = _make_pending(
            test_session,
            stream_name=stream_name,
            candidate_channel_id=candidate_channel_id,
            confidence=confidence,
            trigger_context=trigger_context,
        )

        mock_client = _mock_dispatcharr_resolving(
            candidate_channel_id, stream_name, resolved_stream_id,
        )

        with patch("routers.channel_merges.get_client", return_value=mock_client):
            response = await async_client.post(
                f"/api/channel-merges/{row.id}/accept"
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "merged"
        assert body["merged_into_channel_id"] == candidate_channel_id
        # Resolved stream id is the canonical source_channel_id mirror
        # on the response envelope — the same string lands on the
        # journal row's source_channel_id column.
        assert body["source_stream_id"] == str(resolved_stream_id)
        assert body["confidence"] == pytest.approx(confidence)

        # The journal row is the audit substrate — all seven §D6 fields
        # populated, with trigger_context preserved from the pending row.
        test_session.expire_all()
        journal = (
            test_session.query(PendingMergeJournal)
            .filter(PendingMergeJournal.pending_merge_id == row.id)
            .one()
        )

        # Field 1 — actor_token_id: non-empty opaque identifier. Auth is
        # disabled in tests; the router records the literal "anonymous"
        # which is still a valid, non-empty audit identity (see
        # routers.channel_merges._actor_token_id).
        assert journal.actor_token_id
        assert journal.actor_token_id != ""

        # Field 2 — action_type: accept always records "merge_confirmed".
        assert journal.action_type == "merge_confirmed"

        # Field 3 — source_channel_id: resolved Dispatcharr stream id
        # because the mocked get_streams returned a unique match.
        assert journal.source_channel_id == str(resolved_stream_id)

        # Field 4 — target_channel_id: the candidate Dispatcharr UUID.
        assert journal.target_channel_id == candidate_channel_id

        # Field 5 — confidence_score: captured at action time, equals
        # the pending row's confidence (no drift between queue and accept
        # because the operator-configurable threshold was not re-evaluated).
        assert journal.confidence_score == pytest.approx(confidence)

        # Field 6 — timestamp_utc: epoch-ms UTC, must be > 0.
        assert journal.timestamp_utc > 0

        # Field 7 — trigger_context: mirrors the pending row's surface tag
        # verbatim. This is the load-bearing invariant for the cross-surface
        # analytics in §D6 ("are MCP-agent merges accepted at a higher
        # rate than operator-driven ones?").
        assert journal.trigger_context == trigger_context

        # Cross-check: the pending row is now in a terminal state with
        # resolution_source='operator' (anonymous user is still an
        # operator action from the audit-substrate perspective — the
        # mcp-driven path uses resolution_source='mcp_tool' instead).
        refreshed = (
            test_session.query(PendingMerge)
            .filter(PendingMerge.id == row.id)
            .one()
        )
        assert refreshed.status == "merged"
        assert refreshed.resolved_at is not None
        assert refreshed.resolution_source == "operator"
        # The pending row's own trigger_context is unchanged by accept —
        # the surface tag is set at enqueue time and stays for the row's
        # lifetime as the historical record.
        assert refreshed.trigger_context == trigger_context
