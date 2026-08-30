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

from dbas.restore_contracts import ChannelReattachMode, EntityType, RestoreOutcome
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
async def test_epg_assignment_round_trips_by_portable_row_identity(tmp_path):
    """A/B row ids differ; unique tvg_id restores new and matched channels."""
    source = StatefulDispatcharrFake.seeded_source()
    source_row = source.epg_data.create(
        {"name": "Source guide", "tvg_id": "guide.synthetic"}
    )
    source_channel = next(iter(source.channels.rows.values()))
    source_channel["epg_data_id"] = source_row["id"]
    source_channel["tvg_id"] = None

    dest = StatefulDispatcharrFake.empty_dest()
    dest_row = dest.epg_data.create(
        {"name": "Destination guide", "tvg_id": " GUIDE.SYNTHETIC "}
    )
    existing = dest.channels.create(
        {
            "name": source_channel["name"],
            "channel_number": source_channel["channel_number"],
            "streams": [],
            "epg_data_id": None,
            "tvg_id": None,
        }
    )

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert report.outcome == RestoreOutcome.SUCCESS
    assert dest.channels.rows[existing["id"]]["epg_data_id"] == dest_row["id"]
    assert dest.channels.rows[existing["id"]]["epg_data_id"] != source_row["id"]

    # A newly created channel follows the same portable-key path.
    created_source = source.channels.create(
        {
            "name": "Created synthetic",
            "channel_number": 77,
            "streams": [],
            "epg_data_id": source_row["id"],
            "tvg_id": None,
        }
    )
    await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )
    created_dest = next(
        c
        for c in dest.channels.rows.values()
        if c["name"] == created_source["name"]
    )
    assert created_dest["epg_data_id"] == dest_row["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination_rows", [[], ["collision.synthetic", "COLLISION.SYNTHETIC"]]
)
async def test_missing_or_ambiguous_epg_identity_preserves_existing_assignment(
    tmp_path, destination_rows
):
    source = StatefulDispatcharrFake.seeded_source()
    source_row = source.epg_data.create(
        {"name": "Source guide", "tvg_id": "collision.synthetic"}
    )
    source_channel = next(iter(source.channels.rows.values()))
    source_channel["epg_data_id"] = source_row["id"]
    created_source = source.channels.create(
        {
            "name": "Unresolved created synthetic",
            "channel_number": 78,
            "streams": [],
            "epg_data_id": source_row["id"],
        }
    )

    dest = StatefulDispatcharrFake.empty_dest()
    preserved = dest.epg_data.create(
        {"name": "Preserved guide", "tvg_id": "preserved.synthetic"}
    )
    for index, tvg_id in enumerate(destination_rows):
        dest.epg_data.create({"name": f"Candidate {index}", "tvg_id": tvg_id})
    existing = dest.channels.create(
        {
            "name": source_channel["name"],
            "channel_number": source_channel["channel_number"],
            "streams": [],
            "epg_data_id": preserved["id"],
        }
    )

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert dest.channels.rows[existing["id"]]["epg_data_id"] == preserved["id"]
    created_dest = next(
        row
        for row in dest.channels.rows.values()
        if row["name"] == created_source["name"]
    )
    assert created_dest.get("epg_data_id") is None
    assert report.epg_links_unrestored == 2
    assert {detail.name for detail in report.epg_link_miss_details} == {
        source_channel["name"],
        created_source["name"],
    }


@pytest.mark.asyncio
async def test_null_source_epg_assignment_does_not_replace_destination(tmp_path):
    source = StatefulDispatcharrFake.seeded_source()
    source_channel = next(iter(source.channels.rows.values()))
    source_channel["epg_data_id"] = None
    dest = StatefulDispatcharrFake.empty_dest()
    preserved = dest.epg_data.create(
        {"name": "Preserved guide", "tvg_id": "preserved.synthetic"}
    )
    existing = dest.channels.create(
        {
            "name": source_channel["name"],
            "channel_number": source_channel["channel_number"],
            "streams": [],
            "epg_data_id": preserved["id"],
        }
    )

    await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert dest.channels.rows[existing["id"]]["epg_data_id"] == preserved["id"]


@pytest.mark.asyncio
async def test_epg_identity_lookup_failures_do_not_log_diagnostic_values(
    tmp_path, caplog, monkeypatch
):
    marker = "SYNTHETIC-DIAGNOSTIC-VALUE"
    source = StatefulDispatcharrFake.seeded_source()
    source_channel = next(iter(source.channels.rows.values()))
    source_channel["epg_data_id"] = 123

    async def fail_lookup(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(source, "get_epg_data", fail_lookup)
    await SyncHarness(source=source, dest=StatefulDispatcharrFake.empty_dest()).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert marker not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["exception", "missing", "blank", "truncated"])
async def test_live_sync_requires_stamped_epg_link_provenance(
    tmp_path, caplog, monkeypatch, source_state
):
    """A channel's own tvg_id must never substitute for its linked guide row."""
    marker = "SYNTHETIC-LOOKUP-DIAGNOSTIC"
    fallback_key = "fallback.synthetic"
    source = StatefulDispatcharrFake.seeded_source()
    source_channel = next(iter(source.channels.rows.values()))
    source_channel.update({"epg_data_id": 987654, "tvg_id": fallback_key})

    if source_state == "exception":
        async def fail_lookup(*args, **kwargs):
            raise RuntimeError(marker)

        monkeypatch.setattr(source, "get_epg_data", fail_lookup)
    elif source_state == "blank":
        source.epg_data.rows[987654] = {
            "id": 987654,
            "name": "Blank source row",
            "tvg_id": "   ",
        }
    elif source_state == "truncated":
        monkeypatch.setattr("routers.backup.EPG_INDEX_MAX_ROWS", 2)
        source.epg_data.rows[987654] = {
            "id": 987654,
            "name": "Resolvable source row",
            "tvg_id": fallback_key,
        }
        source.epg_data.rows[987655] = {
            "id": 987655,
            "name": "Ceiling row",
            "tvg_id": "ceiling.synthetic",
        }

    dest = StatefulDispatcharrFake.empty_dest()
    preserved = dest.epg_data.create(
        {"name": "Preserved guide", "tvg_id": "preserved.synthetic"}
    )
    dest.epg_data.create({"name": "False fallback", "tvg_id": fallback_key})
    existing = dest.channels.create(
        {
            "name": source_channel["name"],
            "channel_number": source_channel["channel_number"],
            "streams": [],
            "epg_data_id": preserved["id"],
        }
    )

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert dest.channels.rows[existing["id"]]["epg_data_id"] == preserved["id"]
    assert report.epg_links_unrestored == 1
    assert [detail.name for detail in report.epg_link_miss_details] == [
        source_channel["name"]
    ]
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_destination_epg_inventory_ceiling_preserves_links_and_counts_misses(
    tmp_path, monkeypatch
):
    """A visible candidate is unsafe when a duplicate may be beyond the cap."""
    portable_key = "capped.synthetic"
    source = StatefulDispatcharrFake.seeded_source()
    source_row = source.epg_data.create(
        {"name": "Source guide", "tvg_id": portable_key}
    )
    source_channel = next(iter(source.channels.rows.values()))
    source_channel["epg_data_id"] = source_row["id"]

    dest = StatefulDispatcharrFake.empty_dest()
    preserved = dest.epg_data.create(
        {"name": "Preserved guide", "tvg_id": "preserved.synthetic"}
    )
    dest.epg_data.create({"name": "Visible candidate", "tvg_id": portable_key})
    dest.epg_data.create({"name": "Hidden duplicate", "tvg_id": portable_key.upper()})
    existing = dest.channels.create(
        {
            "name": source_channel["name"],
            "channel_number": source_channel["channel_number"],
            "streams": [],
            "epg_data_id": preserved["id"],
        }
    )
    monkeypatch.setattr("dbas.channel_reattach.EPG_INDEX_MAX_ROWS", 2)

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert dest.channels.rows[existing["id"]]["epg_data_id"] == preserved["id"]
    assert report.epg_links_unrestored == 1
    assert [detail.name for detail in report.epg_link_miss_details] == [
        source_channel["name"]
    ]


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
async def test_partial_failure_on_late_step_rolls_back_cleanly(tmp_path):
    """A LATE-step failure (after epg_source + stream_profile were created) now
    rolls back COMPLETELY — and a re-run still heals.

    v1uz9 closed the rollback-compensator gap: ``_delete_dispatch`` previously
    registered compensators only for M3U / group / profile / channel / stream /
    user — NOT for ``epg_source`` or ``stream_profile`` — so a channel-step
    failure (the last step) could only reach FAILED_ROLLBACK_INCOMPLETE, leaving
    those two rows as residue on B. With ``EntityType.EPG_SOURCE`` and
    ``EntityType.STREAM_PROFILE`` now wired to ``delete_epg_source`` /
    ``delete_stream_profile``, the late-step failure fully undoes EVERYTHING
    created earlier → PARTIAL_FAILED_ROLLED_BACK with B left EMPTY (no residue).
    A clean re-run still converges, proving the idempotent-recovery floor holds.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    def _fault(method: str, payload) -> None:
        if method == "create_channel":
            raise _http_status_error(500, "simulated channel-write error on B")

    dest.inject_fault(_fault)
    harness = SyncHarness(source=source, dest=dest)

    failed = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # v1uz9: the late failure now rolls back epg_source + stream_profile cleanly,
    # so the outcome is the COMPLETE-rollback state, not INCOMPLETE.
    assert failed.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    assert any("rollback completed" in note for note in failed.notes)
    # B is left CONSISTENT (empty) — no half-applied residue.
    assert dest.total_rows() == 0

    # A clean re-run heals: everything is created → B converges to A.
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
async def test_the_provider_credential_reaches_b(tmp_path):
    """AMENDED 2026-08-22 — inverted, deliberately (ADR-013 amendment (b)).

    This was ``test_redaction_no_seeded_secret_reaches_b`` and asserted that
    neither the M3U password nor the EPG password seeded on A appeared anywhere
    in B's rows. (The EPG seed was ``api_key`` until bead ``…-fmtg0``;
    Dispatcharr removed that field in its ``epg/0024`` migration.) The PO ruled that provider credentials cross on every cycle, so
    both must now arrive — and this is the end-to-end layer that proves it: the
    whole pipeline (gather → redact → plan → importer → B write) runs through the
    harness and the assertion is read off what B actually STORED, not off the
    report.

    ``test_ecm_own_secrets_never_reach_b`` below keeps the half that did not
    change, so the pair is a contrast rather than a one-sided claim.
    """
    leak_m3u = "LEAK-M3U-PASSWORD-XYZ"
    leak_epg = "LEAK-EPG-PASSWORD-XYZ"
    source = StatefulDispatcharrFake.seeded_source(
        m3u_password=leak_m3u, epg_password=leak_epg
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
    assert leak_m3u in stored, "the provider password did not reach the replica"
    assert leak_epg in stored, "the EPG password did not reach the replica"
    # Read off the account row itself, not merely "somewhere in the blob": the
    # replica authenticates with THIS field.
    b_m3u = next(a for a in dest.m3u_accounts.list() if a["name"] == "Provider A")
    assert b_m3u.get("password") == leak_m3u


@pytest.mark.asyncio
async def test_ecm_own_secrets_never_reach_b(tmp_path):
    """The half of D2 that did NOT change, at the same end-to-end layer.

    The 2026-08-22 exception is exactly two sections wide. ECM's own settings
    secrets are not provider credentials; a change that widened ``preserve_keys``
    past the provider sections would leave the test above green and this one red,
    which is the only reason the pair is worth having.
    """
    from tasks.dbas_sync_engine import _redact_sync_sections

    redacted = _redact_sync_sections(
        {
            "m3u_accounts": [{"name": "Provider A", "password": "PROVIDER-PW"}],
            "settings": [{"key": "smtp_password", "password": "ECM-OWN-SECRET"}],
        }
    )
    blob = json.dumps(redacted, default=str)
    assert "ECM-OWN-SECRET" not in blob
    assert "PROVIDER-PW" in blob


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


# ---------------------------------------------------------------------------
# (f) LOGOS opt-in slice (bead 7ipq2.1) — convergence + idempotency through the
#     stateful harness, with REAL source logo files on disk (tmp CONFIG_DIR),
#     and the never-destructive invariant (ADR-013 S9).
# ---------------------------------------------------------------------------

_PNG_BYTES = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _seed_source_logo_files(config_dir, names=("cnn.png", "espn.png")):
    logos_dir = config_dir / "uploads" / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (logos_dir / name).write_bytes(_PNG_BYTES)


@pytest.mark.asyncio
async def test_logo_slice_converges_and_is_idempotent(tmp_path):
    """Opt-in logo sync: apply uploads A's logo files onto B (correlated name
    where A's Dispatcharr record joins by basename, stem fallback otherwise);
    a SECOND run is a genuine no-op — B's own now-populated logo list matches,
    zero re-uploads. The destructive bulk-delete NEVER fires either run."""
    config_dir = tmp_path / "config"
    _seed_source_logo_files(config_dir)

    source = StatefulDispatcharrFake.seeded_source()
    # A's Dispatcharr logo record correlates cnn.png -> display name "CNN Logo".
    source.logos.create({"name": "CNN Logo", "url": "http://a/data/logos/cnn.png"})
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(
        source=source, dest=dest,
        target=make_sync_target(sync_logos=True),
        config_dir=config_dir,
    )
    ledger_dir = tmp_path / "ledger"

    first = await harness.run(confirm_apply=True, ledger_dir=ledger_dir)
    assert first.outcome == RestoreOutcome.SUCCESS
    assert first.category(EntityType.LOGO).created == 2
    assert dest.logo_names() == {"cnn logo", "espn"}

    second = await harness.run(confirm_apply=True, ledger_dir=ledger_dir)
    assert second.outcome == RestoreOutcome.SUCCESS
    assert second.category(EntityType.LOGO).created == 0
    assert second.category(EntityType.LOGO).skipped == 2
    assert dest.logo_names() == {"cnn logo", "espn"}  # stable across runs

    # ADR-013 S9 invariant: the sync path NEVER bulk-deletes B's logos.
    assert dest.bulk_logo_delete_calls == []


@pytest.mark.asyncio
async def test_logo_slice_never_deletes_b_only_logos(tmp_path):
    """Non-destructive proof: a logo that exists ONLY on B survives an opted-in
    apply untouched (source-wins adds, never clears)."""
    config_dir = tmp_path / "config"
    _seed_source_logo_files(config_dir, names=("cnn.png",))

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    dest.logos.create({"name": "B Only", "url": "/data/logos/b-only.png"})

    harness = SyncHarness(
        source=source, dest=dest,
        target=make_sync_target(sync_logos=True),
        config_dir=config_dir,
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path / "ledger")

    assert report.outcome == RestoreOutcome.SUCCESS
    assert "b only" in dest.logo_names()  # B-only logo untouched
    assert "cnn" in dest.logo_names()     # A's logo added alongside
    assert dest.bulk_logo_delete_calls == []


@pytest.mark.asyncio
async def test_logo_slice_off_leaves_b_logos_untouched(tmp_path):
    """A target with ``sync_logos`` OFF never pushes source logo files.

    The PRODUCT default moved to ON in bead …-2yq19; this fixture sets the flag
    OFF explicitly (``make_sync_target``'s own default), so the test still means
    what its name says — it guards the OFF path, not the default.
    """
    config_dir = tmp_path / "config"
    _seed_source_logo_files(config_dir)

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(source=source, dest=dest, config_dir=config_dir)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path / "ledger")

    assert report.outcome == RestoreOutcome.SUCCESS
    assert dest.logo_names() == set()
    cat = report.category(EntityType.LOGO)
    assert cat.created == 0 and cat.would_create == 0


# ---------------------------------------------------------------------------
# (i) CHANNEL-PROFILE MEMBERSHIP — the replica must enable EXACTLY what the
#     source enables, never more (bead enhancedchannelmanager-38c5a).
#
# A channel profile is a RESTRICTION. Measured 2026-08-20 on Dispatcharr 0.29.0
# against the documentation environment, after ONE apply-mode cycle that
# reported ``success, created 134, failed 0``:
#
#     source A    'Kids & Family'    6 of 59 channels ENABLED
#     replica B   'Kids & Family'   59 of 59 channels ENABLED
#
# The profile ROW crossed and every counter said so; the SELECTION did not, and
# the replica inverted the profile's purpose by failing OPEN. These tests
# therefore assert on B's RESULTING MEMBERSHIP STATE — never on the profile
# count (2 on both sides while broken) and never on the absence of a failure
# (``failed 0`` while broken).
# ---------------------------------------------------------------------------


def _restricting_profile_source():
    """A source-A whose 'Kids & Family' profile enables 2 of its 6 channels.

    The doc environment's 6-of-59 shape, scaled down. ``seeded_source`` already
    supplies 'CNN' and the unrestricted 'Default Profile', so the restriction
    sits beside a profile that legitimately enables everything — a fix that
    disabled indiscriminately would break that one and be caught here.
    """
    source = StatefulDispatcharrFake.seeded_source()
    for name, number in (
        ("Sprout Junction", 400),
        ("Cartoon Cove", 401),
        ("Nightscreen Thrillers", 303),
        ("Ringside", 209),
        ("Capitol Report", 102),
    ):
        source.channels.create(
            {"name": name, "channel_number": number, "streams": []}
        )
    profile = source.channel_profiles.create({"name": "Kids & Family"})
    kids = {"Sprout Junction", "Cartoon Cove"}
    for channel_id, row in source.channels.rows.items():
        if row.get("name") not in kids:
            source.set_membership(profile["id"], channel_id, False)
    return source


@pytest.mark.asyncio
async def test_a_restricting_profile_arrives_on_b_still_restricting(tmp_path):
    """B's 'Kids & Family' enables exactly the channels A's does — no more."""
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()
    assert source.enabled_channel_names("Kids & Family") == {
        "Sprout Junction",
        "Cartoon Cove",
    }

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # The counters an operator reads are ALL clean, exactly as the live run's
    # were — which is why they cannot be the assertion.
    assert report.outcome == RestoreOutcome.SUCCESS
    assert report.category(EntityType.CHANNEL_PROFILE).created == 2
    assert report.category(EntityType.CHANNEL_PROFILE).failed == 0

    # THE INVARIANT, read off B's membership state.
    assert dest.enabled_channel_names("Kids & Family") == source.enabled_channel_names(
        "Kids & Family"
    )
    # Stated in the direction that matters: never MORE than the source.
    assert dest.enabled_channel_names("Kids & Family") <= source.enabled_channel_names(
        "Kids & Family"
    )
    # A profile that legitimately enables everything still enables everything.
    assert dest.enabled_channel_names("Default Profile") == source.enabled_channel_names(
        "Default Profile"
    )
    # The correction away from Dispatcharr's enable-everything create default is
    # COUNTED, so the operator can see the profile was about to widen.
    assert report.profile_membership_drift == 4


@pytest.mark.asyncio
async def test_a_second_cycle_leaves_the_restriction_in_place(tmp_path):
    """Idempotency for the SELECTION, not just for the rows.

    The membership pass never reads B's current state, so a second cycle
    re-asserts the same selection. The property that matters is that it lands on
    the same answer rather than drifting open one cycle at a time.
    """
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert dest.enabled_channel_names("Kids & Family") == {
        "Sprout Junction",
        "Cartoon Cove",
    }


@pytest.mark.asyncio
async def test_an_unknown_profile_selection_fails_closed_on_b(tmp_path):
    """A profile whose archived selection cannot be read must NOT arrive open.

    ``ChannelProfileSerializer.channels`` is the enabled-channel list on 0.28.2
    and (re-confirmed) on 0.29.0, so its ABSENCE means the source never told us
    what the profile enables. The degraded direction is FEWER channels enabled,
    never all of them: the memberships this cycle CREATED are turned off, and
    the profile is named in the report.
    """
    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()

    real_get = source.get_channel_profiles

    async def _profiles_without_their_selection():
        rows = await real_get()
        for row in rows:
            row.pop("channels", None)
        return rows

    source.get_channel_profiles = _profiles_without_their_selection

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # Fail CLOSED: nothing this cycle created is left enabled in either profile.
    assert dest.enabled_channel_names("Kids & Family") == set()
    assert dest.enabled_channel_names("Default Profile") == set()
    # And loudly — a silent fail-closed is the same reporting defect wearing a
    # different sign.
    assert any("channel selection" in note for note in report.notes)


@pytest.mark.asyncio
async def test_a_membership_b_refuses_to_disable_is_reported_not_swallowed(tmp_path):
    """A DISABLE that B rejects is the fail-open outcome — it must be counted.

    B rate-limits, so a 429 on a membership PATCH is a real operational event.
    Before this it produced one WARNING log line and no counter at all: the
    channel stayed ENABLED (Dispatcharr's create default) and the cycle still
    reported a clean success. Asserted on the CHANNEL_PROFILE category's
    ``failed`` / ``failure_details`` — the ``FailureReason`` structure, the same
    place ``dbas/importers/channels.py`` records a membership PATCH failure —
    NOT on ``skip_details`` and NOT on a top-level ``RestoreReport`` int.
    """
    from dbas.restore_contracts import FailureReason

    source = _restricting_profile_source()
    dest = StatefulDispatcharrFake.empty_dest()

    def _throttle_the_disables(method, payload):
        if method == "update_profile_channel" and payload.get("enabled") is False:
            raise _http_status_error(429, "Request was throttled.")

    dest.inject_fault(_throttle_the_disables)

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    cat = report.category(EntityType.CHANNEL_PROFILE)
    assert cat.failed == 4  # the four channels 'Kids & Family' excludes
    assert {d.reason for d in cat.failure_details} == {FailureReason.UPSTREAM_API_ERROR}
    # B kept them enabled — we cannot fix what B refuses — but the run no longer
    # claims to have succeeded, and no rollback was triggered by it.
    assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert dest.enabled_channel_names("Kids & Family") == dest.enabled_channel_names(
        "Default Profile"
    )


# ---------------------------------------------------------------------------
# (j) CHANNEL → LOGO BINDING — the replica's channel must carry the replica's
#     OWN logo (bead enhancedchannelmanager-xgbjm).
#
# Bead ``…-cfxml`` (merged in PR #904) got the logo BYTES across: the final
# two-instance validation measured md5 38ee6912d90992cf51bc9ea306e413f7
# identical on A, on B's disk, and over both APIs' cache endpoints. Read off
# destination B rather than off a run report, the remaining half was:
#
#     B's Logo Manager   'XDMRU Hosted Logo'   usage UNUSED
#     every channel on B                       logo_id null
#     source A's channel 'XDMRU News One'      logo 1
#
# So the replica held the right image FILE with no channel using it — the most
# visible difference between primary and replica, and what an operator sees
# first on opening B.
#
# ``logo_id`` is a SOURCE id, so the channels importer drops it from the create
# payload — correctly; forwarding it would bind an unrelated destination row.
# The pass that puts it back, ``channel_reattach.reattach_channel_logos``, was
# wired into the archive-restore registry only, exactly as
# ``reattach_profile_memberships`` was before bead …-38c5a.
#
# THE ASSERTION SURFACE. These tests read WHICH LOGO B'S CHANNEL POINTS AT, BY
# NAME (``channel_logo_name``). Never the logo COUNT (right on both sides while
# broken), never ``failed == 0`` (clean while broken), and never an id — a
# source logo id that happens to alias an unrelated row on the destination is
# the false green this whole branch kept re-learning, so the fixtures below
# plant a DECOY on B at the exact id A's logo carries.
# ---------------------------------------------------------------------------

_DECOY_LOGO_NAME = "Decoy Logo — Not The One A Uses"


def _channel_with_a_hosted_logo():
    """A source-A whose 'CNN' channel carries a DISPATCHARR-HOSTED logo.

    Hosted (a ``url`` naming a path inside Dispatcharr's own volume) rather than
    an ECM-local file, because that is the shape the live defect took and the
    shape bead …-cfxml taught the gather to fetch: the bytes exist only on A and
    only Dispatcharr can supply them.
    """
    source = StatefulDispatcharrFake.seeded_source()
    logo = source.logos.create(
        {"name": "XDMRU Hosted Logo", "url": "/data/logos/xdmru.png"}
    )
    for row in source.channels.rows.values():
        if row.get("name") == "CNN":
            row["logo_id"] = logo["id"]
    return source, logo


def _dest_with_a_decoy_at(source_logo_id: int):
    """An empty dest-B carrying an UNRELATED logo at A's logo id.

    The false-green guard. Without it, a "fix" that forwarded A's ``logo_id``
    verbatim would either dangle harmlessly or — on a real destination whose id
    ranges overlap — silently bind the wrong image, and an id-based assertion
    would call both of those a pass. With it, the wrong answer has a NAME and
    the name-based assertion says which wrong answer it got.
    """
    dest = StatefulDispatcharrFake.empty_dest()
    dest.logos.rows[source_logo_id] = {
        "id": source_logo_id,
        "name": _DECOY_LOGO_NAME,
        "url": "/data/logos/decoy.png",
    }
    return dest


@pytest.mark.asyncio
async def test_a_channel_with_a_logo_on_a_carries_bs_own_logo_on_b(tmp_path):
    """THE INVARIANT: B's channel points at B's copy of A's logo, by name."""
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # The counters an operator reads are clean — which is why they cannot be
    # the assertion. The live run that produced this bead reported exactly this.
    assert report.outcome == RestoreOutcome.SUCCESS
    assert report.category(EntityType.CHANNEL).failed == 0
    assert report.category(EntityType.LOGO).failed == 0

    # THE INVARIANT, read off B by NAME.
    assert dest.channel_logo_name("CNN") == source.channel_logo_name("CNN")
    assert dest.channel_logo_name("CNN") == "XDMRU Hosted Logo"

    # ... bound through B's OWN id, never A's — the decoy is what A's id names
    # on B, and B's channel must not be pointing at it.
    dest_channel = next(
        row for row in dest.channels.rows.values() if row.get("name") == "CNN"
    )
    assert dest_channel["logo_id"] != source_logo["id"]
    assert dest.logos.rows[dest_channel["logo_id"]]["name"] == "XDMRU Hosted Logo"

    # The decoy is untouched: this pass binds, it never edits B's logo rows.
    assert dest.logos.rows[source_logo["id"]]["name"] == _DECOY_LOGO_NAME


@pytest.mark.asyncio
async def test_logo_sync_enabled_after_the_channels_already_replicated(tmp_path):
    """The operator flow bead …-8gnik will actually produce, and the reason the
    sync path binds SOURCE-WINS rather than preserving.

    ``sync_logos`` runs on a slower sub-interval, so the realistic sequence is:
    cycles run, B gets its lineup, THEN the flag goes on. By that point every
    channel on B already exists and is MATCHED, not created — so a pass that
    only touched channels this cycle created would bind nothing at all, forever,
    and the operator would see the new control do nothing. Same source-wins call
    the sibling EPG-link pass on this path already makes.
    """
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    # Cycle 1: logos OFF. B gets the channel; no logo crosses.
    logos_off = SyncHarness(source=source, dest=dest, target=make_sync_target())
    await logos_off.run(confirm_apply=True, ledger_dir=tmp_path)
    assert dest.channel_logo_name("CNN") is None

    # Cycle 2: the operator turns logos ON. B's channel already exists.
    logos_on = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await logos_on.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.outcome == RestoreOutcome.SUCCESS
    assert dest.channel_logo_name("CNN") == "XDMRU Hosted Logo"


@pytest.mark.asyncio
async def test_the_binding_is_stable_across_cycles(tmp_path):
    """A second cycle lands on the same answer rather than drifting.

    Idempotency for the BINDING, not just for the rows: cycle 2 matches B's
    now-present logo instead of uploading it, so it re-resolves through the
    remap's MATCH path rather than its CREATE path — a fix that only populated
    the remap on a create would bind on cycle 1 and unbind on cycle 2.
    """
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    first_binding = dest.channel_logo_name("CNN")

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert second.category(EntityType.LOGO).created == 0  # matched, not re-created
    assert dest.channel_logo_name("CNN") == first_binding == "XDMRU Hosted Logo"


@pytest.mark.asyncio
async def test_a_dry_run_predicts_the_binding_and_writes_nothing(tmp_path):
    """The preview reports the population split and PATCHes nothing.

    On a FRESH target nothing matches, so every logo is a would-CREATE with no
    destination id yet — the state bead …-dgnms measured as a preview reporting
    0 for an apply that reattached 11. The split has to count it anyway, and B
    must still be untouched afterwards.
    """
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=False, ledger_dir=tmp_path)

    # ``logo_reattach`` is a NON-OPTIONAL ReattachPopulation with a default, so
    # "the pass ran" cannot be asserted as ``is not None``. The MODE is the
    # honest discriminator: the sync path binds source-wins, and the default is
    # PRESERVE, so an OVERWRITE mode here means this pass — not the default —
    # produced the split.
    assert report.logo_reattach.mode == ChannelReattachMode.OVERWRITE
    assert report.logo_reattach.created_channels == 1
    # Nothing was written: B has no channel at all, and its logo surface is
    # still just the decoy.
    assert dest.channels.rows == {}
    assert dest.logo_names() == {_DECOY_LOGO_NAME.lower()}


@pytest.mark.asyncio
async def test_a_logo_b_never_received_is_a_named_miss_not_a_silent_null(tmp_path):
    """A binding that cannot be made is COUNTED and the channel is NAMED.

    HONEST ABOUT WHAT THIS PROVES: it was GREEN before the fix — the logos
    importer's own miss path (bead …-cm9bi) already named the channel. It is
    here as an INTERACTION guard, and the load-bearing half is the last
    assertion: the reattach pass records its own miss under a SYNTHETIC label
    (``logo #N (archived)``), and ``record_logo_miss`` is supposed to absorb
    that into the importer's existing row rather than let a second, worse-named
    row appear beside it. That absorb is only exercised once both halves run.
    """
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])
    # A's Dispatcharr cannot serve the bytes, so the logo is never created on B
    # and there is nothing for the channel to point at.
    source.hosted_logo_bytes = None

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert dest.channel_logo_name("CNN") is None
    # ``logo_misses`` is a top-level INT aggregate; the channel NAMES live in
    # ``logo_miss_details[].channels[].name``. Asserting on the int alone would
    # not prove the affected channel was named, and asserting on
    # ``skip_details``/``failure_details`` would be the wrong structure
    # entirely — a logo miss is neither.
    assert report.logo_misses >= 1
    affected = {
        channel.name
        for miss in report.logo_miss_details
        for channel in miss.channels
    }
    assert "CNN" in affected
    # ONE row, under the operator-facing NAME — not a second synthetic
    # ``logo #N (archived)`` row contributed by the reattach pass.
    assert [detail.label for detail in report.logo_miss_details] == [
        "XDMRU Hosted Logo"
    ]


@pytest.mark.asyncio
async def test_logos_off_leaves_bs_channel_logos_alone(tmp_path):
    """A target with ``sync_logos`` OFF binds nothing.

    Bead …-8gnik owns the toggle; this pass must not reach across it. Green
    before the fix as well as after — it is a REGRESSION guard on the off/on
    boundary, not a demonstration of the defect. (The product default moved to
    ON in …-2yq19; the flag is set OFF here explicitly.)
    """
    source, source_logo = _channel_with_a_hosted_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(source=source, dest=dest, target=make_sync_target())
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.outcome == RestoreOutcome.SUCCESS
    assert dest.channel_logo_name("CNN") is None
    # The pass did not run: its split is the untouched default, and the MODE is
    # still PRESERVE rather than the source-wins OVERWRITE this path binds with.
    assert report.logo_reattach.mode == ChannelReattachMode.PRESERVE
    assert report.logo_reattach.created_channels == 0
    assert report.logo_reattach.existing_channels == 0
    assert report.logo_reattach.preserved_channels == 0
    assert dest.logos.rows[source_logo["id"]]["name"] == _DECOY_LOGO_NAME


# ---------------------------------------------------------------------------
# (k) REMOTE-URL LOGOS — the third, disjoint storage shape (bead …-sgrez).
#
# The gather built the LOGO category from TWO sources: files under ECM's own
# ``/config/uploads/logos/`` and the DISPATCHARR-HOSTED bytes bead …-cfxml
# added. A logo whose ``url`` is an ABSOLUTE http(s) address is in NEITHER set,
# so it produced no plan record at all — not a miss, not a failure, nothing.
# Measured on source A of the documentation environment, 2026-08-20:
#
#     A logos total=60   remote-url=59   Dispatcharr-hosted=1
#     e.g. id 1 'Meridian News'  http://dispatcharr-p-web:9191/api/…/cache/
#
# So sync replicated ONE LOGO IN SIXTY, and it took bead …-xgbjm making the
# bindings cross for the run to start saying so ('58 logo(s) could not be
# reinstated' where it had silently said 0).
#
# THE INVARIANT under test: a channel that shows a logo on A shows the
# CORRESPONDING logo on B, whatever the logo's storage shape. The 1-of-60 count
# is one example of that property, not the specification — so the tests below
# drive a MIXED population (remote + hosted) rather than the remote shape alone.
#
# The assertion surface is bead …-xgbjm's: WHICH LOGO B'S CHANNEL POINTS AT, BY
# NAME, with a DECOY planted on B at the exact id A's logo carries.
# ---------------------------------------------------------------------------

_REMOTE_LOGO_URL = "http://cdn.northwind.example/logos/cnn.png"


def _channel_with_a_remote_url_logo():
    """A source-A whose 'CNN' channel carries a REMOTE-URL logo.

    The shape an XC-sourced instance actually holds: the provider hands over a
    ``tvg-logo`` address and Dispatcharr stores the URL, never the bytes. There
    is nothing to upload and nothing to fetch — Dispatcharr's Logo model IS
    ``{name, url}``, so the replica is restored by pointing at the same address.
    """
    source = StatefulDispatcharrFake.seeded_source()
    logo = source.logos.create({"name": "Northwind CNN", "url": _REMOTE_LOGO_URL})
    for row in source.channels.rows.values():
        if row.get("name") == "CNN":
            row["logo_id"] = logo["id"]
    return source, logo


@pytest.mark.asyncio
async def test_a_channel_with_a_remote_url_logo_carries_bs_own_logo_on_b(tmp_path):
    """THE INVARIANT for the remote shape: B's channel points at B's copy, by
    name, and B's copy carries the SAME address A points at."""
    source, source_logo = _channel_with_a_remote_url_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.outcome == RestoreOutcome.SUCCESS
    # The logo came BACK, so it is not an operator-facing loss.
    assert report.logo_misses == 0

    # THE INVARIANT, read off B by NAME.
    assert dest.channel_logo_name("CNN") == source.channel_logo_name("CNN")
    assert dest.channel_logo_name("CNN") == "Northwind CNN"

    # ... bound through B's OWN id, never A's — the decoy is what A's id names
    # on B, and B's channel must not be pointing at it.
    dest_channel = next(
        row for row in dest.channels.rows.values() if row.get("name") == "CNN"
    )
    assert dest_channel["logo_id"] != source_logo["id"]
    assert dest.logos.rows[dest_channel["logo_id"]]["name"] == "Northwind CNN"
    # B's row points at the SAME address A's does — that is what makes the
    # replica show the same picture.
    assert dest.logos.rows[dest_channel["logo_id"]]["url"] == _REMOTE_LOGO_URL

    # The decoy is untouched.
    assert dest.logos.rows[source_logo["id"]]["name"] == _DECOY_LOGO_NAME


@pytest.mark.asyncio
async def test_every_storage_shape_crosses_in_one_cycle(tmp_path):
    """A MIXED population — the specification, not the 1-of-60 reproduction.

    One remote-URL logo and one Dispatcharr-hosted logo on two different
    channels. Both channels must show their own logo on B in the SAME cycle: a
    fix that handled the remote shape by displacing the hosted one would pass a
    remote-only test and still lose a logo.
    """
    source = StatefulDispatcharrFake.seeded_source()
    remote = source.logos.create({"name": "Northwind CNN", "url": _REMOTE_LOGO_URL})
    hosted = source.logos.create(
        {"name": "XDMRU Hosted Logo", "url": "/data/logos/xdmru.png"}
    )
    for row in source.channels.rows.values():
        if row.get("name") == "CNN":
            row["logo_id"] = remote["id"]
    source.channels.create(
        {"name": "BBC", "channel_number": 6, "streams": [], "logo_id": hosted["id"]}
    )
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.outcome == RestoreOutcome.SUCCESS
    assert report.logo_misses == 0
    assert dest.channel_logo_name("CNN") == "Northwind CNN"
    assert dest.channel_logo_name("BBC") == "XDMRU Hosted Logo"
    # B holds BOTH, and never fetched bytes it had no business fetching: the
    # remote row is a pointer, the hosted row is an upload.
    assert dest.logos.rows[
        next(
            rid for rid, r in dest.logos.rows.items() if r["name"] == "Northwind CNN"
        )
    ]["url"] == _REMOTE_LOGO_URL


@pytest.mark.asyncio
async def test_the_remote_binding_is_stable_across_cycles(tmp_path):
    """Cycle 2 matches B's now-present row rather than creating a second one."""
    source, source_logo = _channel_with_a_remote_url_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    first = dest.channel_logo_name("CNN")

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert second.category(EntityType.LOGO).created == 0  # matched, not re-created
    assert dest.channel_logo_name("CNN") == first == "Northwind CNN"


@pytest.mark.asyncio
async def test_a_dry_run_predicts_the_remote_create_and_writes_nothing(tmp_path):
    """The preview must predict what the apply will DO (bead …-dgnms's lesson,
    applied to the sync path): a would-CREATE, and B untouched."""
    source, source_logo = _channel_with_a_remote_url_logo()
    dest = _dest_with_a_decoy_at(source_logo["id"])

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=False, ledger_dir=tmp_path)

    assert report.category(EntityType.LOGO).would_create == 1
    assert report.logo_misses == 0
    assert report.logo_reattach.mode == ChannelReattachMode.OVERWRITE
    assert report.logo_reattach.created_channels == 1
    assert dest.channels.rows == {}
    assert dest.logo_names() == {_DECOY_LOGO_NAME.lower()}


# ---------------------------------------------------------------------------
# (k2) CREDENTIAL INTERACTION — bead …-msqf7 closed the hole where a real XC
# provider's username and password crossed to the replica inside every stream
# URL, as PATH SEGMENTS. A logo url is a provider-supplied address on exactly
# the same instances, so COPYING one to B is a new route to the same hole.
#
# THE RULE: a logo URL that carries a credential is NOT handed to B. The address
# is not usable once its credential segments are the sentinel — it would give
# the operator a channel whose logo silently 404s, which the logos importer
# already rules is "strictly worse than an honest miss" — so the record travels
# WITHOUT a url and the operator is told, by name, which logo did not come back.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_credential_bearing_logo_url_reaches_the_replica(tmp_path):
    """AMENDED 2026-08-22 — inverted (ADR-013 amendment (b)).

    The XC path-segment shape …-msqf7 measured, on a logo url. This used to be
    dropped and reported as a named miss; under per-cycle credential
    transmission the replica holds the same credential, so an address it can
    actually fetch is worth more than an honest miss.
    """
    source = StatefulDispatcharrFake.seeded_source()
    account = next(iter(source.m3u_accounts.rows.values()))
    account["username"] = "northwind-demo"
    account["password"] = "not-a-real-password"
    logo = source.logos.create(
        {
            "name": "Leaky Logo",
            "url": "http://provider.example/live/northwind-demo/"
                   "not-a-real-password/logos/cnn.png",
        }
    )
    for row in source.channels.rows.values():
        if row.get("name") == "CNN":
            row["logo_id"] = logo["id"]
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # The address crossed WHOLE, so B loads the same picture A does.
    blob = json.dumps(
        [dict(r) for r in dest.logos.rows.values()], default=str
    )
    assert (
        "http://provider.example/live/northwind-demo/"
        "not-a-real-password/logos/cnn.png"
    ) in blob
    # ...and it is no longer reported as a miss, because it is not one. A
    # counter that keeps firing after its condition is gone is bead
    # ``…-kcfru``'s crying wolf.
    assert "Leaky Logo" not in {d.label for d in report.logo_miss_details}


@pytest.mark.asyncio
async def test_a_query_string_credential_logo_url_reaches_the_replica(tmp_path):
    """The other carrier shape: ``?username=…&password=…`` in the query."""
    source = StatefulDispatcharrFake.seeded_source()
    logo = source.logos.create(
        {
            "name": "Leaky Query Logo",
            "url": "http://provider.example/logo.php?username=u1&password=p1",
        }
    )
    for row in source.channels.rows.values():
        if row.get("name") == "CNN":
            row["logo_id"] = logo["id"]
    dest = StatefulDispatcharrFake.empty_dest()

    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target(sync_logos=True)
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    blob = json.dumps([dict(r) for r in dest.logos.rows.values()], default=str)
    assert "password=p1" in blob
    assert "username=u1" in blob
    assert "Leaky Query Logo" not in {d.label for d in report.logo_miss_details}
