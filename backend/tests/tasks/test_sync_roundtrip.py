"""Cross-instance sync ROUND-TRIP keystone suite (bead enhancedchannelmanager-46pkq).

Epic ``i39wu``. The sync-test analogue of the DBAS restore round-trip anchor
(``tests/dbas/test_restore_roundtrip.py``): a small, shareable, harness-driven
keystone suite that future sync beads (``tjaey``, ``kcxie``) extend.

What makes these tests REAL (vs the call-count engine tests)
------------------------------------------------------------
``test_dbas_sync_engine.py`` mocks dest-B as independent ``AsyncMock`` methods and
asserts on CALL COUNTS — it proves the engine *calls* B but not that B *converged*.
These tests instead use the STATEFUL two-instance harness
(``tests/fixtures/sync_harness.py``): dest-B APPLIES every write (create → stores +
returns a new server id; duplicate create → 409; update → mutates), so:

* **convergence** is asserted as ``B.state_by_key() == A.state_by_key()`` — every
  source entity present on B under its NATURAL key (ids differ; B assigns its own);
* **idempotency** is a GENUINE second ``run_sync`` against the now-populated B that
  the importers resolve to ``ALREADY_EXISTS_IDENTICAL`` → zero creates, no second
  hand-built "already-converged" mock;
* **partial failure** injects a real mid-sync write error on B and asserts the
  orchestrator's compensating rollback left B consistent, then that a clean re-run
  HEALS (converges) — the idempotent-recovery property.

Plus the two security invariants end-to-end through the harness: never-sync-users
(D3) and redact-by-default (D2 — no seeded secret reaches B).

NO live Dispatcharr. The live two-stack nightly tier is DEFERRED (see
``docs/testing/dbas-test-env.md`` → "Cross-instance sync test strategy" and
``tests/dbas-test-env/docker-compose.dbas-sync-test.yml``).
"""
from __future__ import annotations

import json

import pytest

from dbas.restore_contracts import EntityType, RestoreOutcome
from tests.fixtures.sync_harness import (
    StatefulDispatcharrFake,
    SyncHarness,
    _http_status_error,
    make_sync_target,
)


# ---------------------------------------------------------------------------
# (a) CONVERGENCE — empty B → apply → B's config == A's (by natural key).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convergence_empty_b_matches_a_by_natural_key(tmp_path):
    """Apply against an empty B makes B's syncable state equal A's (natural key).

    The stateful dest-B applies every create, so after the cycle a key-by-key
    comparison is meaningful: B holds every config category + the channel A holds.
    ids differ (B assigns its own) — equality is asserted on natural keys only.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    assert dest.total_rows() == 0  # B starts empty.

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.outcome == RestoreOutcome.SUCCESS
    # The whole point: B converged to A's state by natural key.
    assert dest.state_by_key() == source.state_by_key()
    # Sanity: every config category + the channel actually got created on B.
    assert report.category(EntityType.M3U_ACCOUNT).created == 1
    assert report.category(EntityType.EPG_SOURCE).created == 1
    assert report.category(EntityType.CHANNEL_GROUP).created == 2
    assert report.category(EntityType.CHANNEL).created == 1


@pytest.mark.asyncio
async def test_dry_run_default_makes_zero_writes_to_b(tmp_path):
    """confirm_apply=False (default) is a counts-only preview — B stays empty."""
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(ledger_dir=tmp_path)  # confirm_apply defaults False

    assert report.is_dry_run is True
    assert report.outcome is None
    assert dest.total_rows() == 0  # ZERO writes — B is untouched.
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 1


# ---------------------------------------------------------------------------
# (b) IDEMPOTENCY — a SECOND run against the now-populated B is a no-op.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_second_run_is_a_real_noop(tmp_path):
    """A second cycle against the SAME (populated) B creates nothing.

    Because B is stateful, the second run reads B's OWN now-populated config, so
    every source entity matches → ALREADY_EXISTS_IDENTICAL. This is idempotency
    proven against the real apply, not against a separate hand-built mock.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    first = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert first.outcome == RestoreOutcome.SUCCESS
    rows_after_first = dest.total_rows()
    assert rows_after_first > 0

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert second.outcome == RestoreOutcome.SUCCESS
    # No new rows on B — the second run is a genuine no-op.
    assert dest.total_rows() == rows_after_first
    # Zero creates across EVERY category; every category resolved to a skip.
    for cat in second.categories:
        assert cat.created == 0, "category %s re-created on idempotent run" % cat.entity_type
    assert sum(cat.skipped for cat in second.categories) >= 5
    # And B still equals A — convergence is stable across runs.
    assert dest.state_by_key() == source.state_by_key()


# ---------------------------------------------------------------------------
# (c) PARTIAL FAILURE — mid-sync B write error → rollback leaves B consistent;
#     a clean re-run heals (converges).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_failure_rolls_back_then_reconverges(tmp_path):
    """A mid-sync write error on B → non-SUCCESS + compensating rollback leaves B
    consistent; a clean re-run converges.

    The fault fires on the SECOND importer step (``create_epg_source``), so only
    the M3U account was created before the failure. M3U HAS a registered rollback
    compensator, so the orchestrator's compensating delete fully undoes it →
    PARTIAL_FAILED_ROLLED_BACK and B is left EMPTY (consistent — no half-applied
    residue). Clearing the fault and re-running then converges B to A.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    def _fault(method: str, payload) -> None:
        if method == "create_epg_source":
            raise _http_status_error(500, "simulated mid-sync upstream error on B")

    dest.inject_fault(_fault)
    harness = SyncHarness(source=source, dest=dest)

    failed = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # Tri-state discipline: NEVER SUCCESS on mixed state.
    assert failed.outcome != RestoreOutcome.SUCCESS
    assert failed.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    # Rollback compensated the one created entity (M3U) — B is left CONSISTENT
    # (empty), not half-applied.
    assert dest.total_rows() == 0
    assert any("rollback completed" in note for note in failed.notes)

    # Now clear the fault and re-run: the system HEALS — a clean cycle converges.
    dest.inject_fault(None)
    healed = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert healed.outcome == RestoreOutcome.SUCCESS
    assert dest.state_by_key() == source.state_by_key()


@pytest.mark.asyncio
async def test_partial_failure_on_late_step_surfaces_residue(tmp_path):
    """A LATE-step failure (after categories without a rollback compensator were
    created) surfaces INCOMPLETE rollback honestly — and a re-run still heals.

    This documents a REAL orchestrator property the harness makes visible: the
    rollback dispatch (``restore_orchestrator._delete_dispatch``) registers
    compensators only for M3U / group / profile / channel / stream / user — NOT
    for ``epg_source`` or ``stream_profile``. So a channel-step failure (the last
    step) cannot fully undo the EPG source + stream profile created earlier; the
    outcome is FAILED_ROLLBACK_INCOMPLETE with that residue left on B, reported
    loudly (never a silent clean-success). A clean re-run still converges (the
    residue matches → skip), proving the idempotent-recovery floor holds even from
    an incompletely-rolled-back state.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    def _fault(method: str, payload) -> None:
        if method == "create_channel":
            raise _http_status_error(500, "simulated channel-write error on B")

    dest.inject_fault(_fault)
    harness = SyncHarness(source=source, dest=dest)

    failed = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert failed.outcome == RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE
    assert any("INCOMPLETE" in note for note in failed.notes)
    # The residue (epg_source + stream_profile — no compensator) is left on B.
    assert dest.total_rows() > 0

    # A clean re-run heals despite the residue: the leftover rows match → skip,
    # everything else is created → B converges to A.
    dest.inject_fault(None)
    healed = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert healed.outcome == RestoreOutcome.SUCCESS
    assert dest.state_by_key() == source.state_by_key()


# ---------------------------------------------------------------------------
# (d) NEVER-SYNC-USERS guard — end-to-end through the harness (D3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_sync_users_end_to_end(tmp_path):
    """D3: no user is ever created on B, even though source-A could serve them.

    The source fake deliberately exposes a ``get_users`` that, if it were ever
    reached, would return a superuser. The harness asserts it is NEVER called for
    plan assembly AND that B's user-creating method is never invoked — the users
    category is structurally excluded from a sync plan.
    """
    from unittest.mock import AsyncMock

    source = StatefulDispatcharrFake.seeded_source()
    # Wire a users getter + a B-side create that MUST NEVER fire.
    source.get_users = AsyncMock(
        return_value=[{"id": 99, "username": "admin", "is_superuser": True}]
    )
    dest = StatefulDispatcharrFake.empty_dest()
    dest.create_user = AsyncMock(return_value={"id": 1, "username": "admin"})

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.outcome == RestoreOutcome.SUCCESS
    # The users getter was never reached (structural exclusion, defence in depth).
    source.get_users.assert_not_called()
    # No user was ever created on B — the load-bearing D3 invariant.
    dest.create_user.assert_not_called()
    # The USER category was never populated (zero creates/would-creates) — the
    # report auto-materializes a zero-count shell on access, so we assert counts,
    # not None (the *plan* never carries a USER category — verified directly below).
    user_cat = report.category(EntityType.USER)
    assert user_cat.created == 0 and user_cat.would_create == 0

    # And, directly: the assembled sync PLAN structurally excludes the USER
    # category (it is never even gathered — D3).
    from tasks.dbas_sync_engine import build_live_source_plan
    from routers import backup as backup_mod
    from unittest.mock import patch

    with patch.object(backup_mod, "get_client", return_value=source):
        plan = await build_live_source_plan()
    assert plan.category(EntityType.USER) is None


# ---------------------------------------------------------------------------
# (e) REDACTION — no seeded secret reaches B end-to-end (D2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redaction_no_seeded_secret_reaches_b(tmp_path):
    """D2: the M3U password + EPG api_key seeded on A never appear in B's rows.

    The whole config pipeline (gather → deep-redact → plan → importer → B write)
    runs through the harness; we then dump everything B actually STORED and assert
    neither plaintext secret is present anywhere in it.
    """
    leak_m3u = "LEAK-M3U-PASSWORD-XYZ"
    leak_epg = "LEAK-EPG-APIKEY-XYZ"
    source = StatefulDispatcharrFake.seeded_source(
        m3u_password=leak_m3u, epg_api_key=leak_epg
    )
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert report.outcome == RestoreOutcome.SUCCESS

    # Everything B stored, serialized — neither plaintext secret may appear.
    stored = json.dumps(
        {
            "m3u_accounts": dest.m3u_accounts.list(),
            "epg_sources": dest.epg_sources.list(),
            "channel_groups": dest.channel_groups.list(),
            "channels": dest.channels.list(),
            "streams": dest.streams.list(),
        }
    )
    assert leak_m3u not in stored
    assert leak_epg not in stored
    # The M3U account DID sync (topology) — but its secret field is redacted.
    b_m3u = next(a for a in dest.m3u_accounts.list() if a["name"] == "Provider A")
    assert b_m3u.get("password") != leak_m3u


# ---------------------------------------------------------------------------
# Write-API contract fidelity — the dest-B fake models the Dispatcharr WRITE
# contract the sync path depends on (the bead's least-validated surface).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dest_create_returns_new_server_id():
    """create_* echoes the payload PLUS a NEW server-assigned id (B owns ids)."""
    dest = StatefulDispatcharrFake.empty_dest()
    created = await dest.create_m3u_account({"name": "Provider A"})
    assert created["name"] == "Provider A"
    assert isinstance(created["id"], int)
    # A second, DIFFERENT account gets a DIFFERENT id.
    created2 = await dest.create_m3u_account({"name": "Provider B"})
    assert created2["id"] != created["id"]


@pytest.mark.asyncio
async def test_dest_duplicate_create_raises_409():
    """A duplicate create (same natural key) surfaces a real 409 conflict."""
    import httpx

    dest = StatefulDispatcharrFake.empty_dest()
    await dest.create_channel_group("News")
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        # case-insensitive / trimmed natural key — "  news " collides with "News".
        await dest.create_channel_group("  news ")
    assert exc_info.value.response.status_code == 409


@pytest.mark.asyncio
async def test_dest_update_mutates_stored_row():
    """update_* mutates the stored row in place and returns it."""
    dest = StatefulDispatcharrFake.empty_dest()
    created = await dest.create_channel({"name": "CNN", "channel_number": 5})
    updated = await dest.update_channel(created["id"], {"channel_number": 7})
    assert updated["channel_number"] == 7
    # The store reflects the mutation (read-after-write).
    page = await dest.get_channels()
    assert page["results"][0]["channel_number"] == 7


@pytest.mark.asyncio
async def test_dest_delete_absent_id_raises_404():
    """delete_* of an absent id raises 404 — the rollback 404-as-success shape."""
    import httpx

    dest = StatefulDispatcharrFake.empty_dest()
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await dest.delete_m3u_account(99999)
    assert exc_info.value.response.status_code == 404


def test_distinct_id_bases_prevent_a_b_id_collision():
    """A's ids and B's ids never coincide by accident (a confused test would fail
    loudly rather than pass on a shared id)."""
    a = StatefulDispatcharrFake.seeded_source()
    b = StatefulDispatcharrFake.empty_dest()
    a_ids = {r["id"] for r in a.m3u_accounts.list()}
    # B's next-assigned id base is far from A's, so a leaked A-id is detectable.
    assert b.m3u_accounts._next_id not in a_ids
