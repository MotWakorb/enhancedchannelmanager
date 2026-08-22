"""A replica must ingest the provider content its source ingests (…-avrix).

THE INVARIANT UNDER TEST, and the two-of-777 case is one EXAMPLE of it, not the
specification:

    A replica ingests the same provider content the source ingests, or the run
    says plainly that it will not.

WHAT WAS MEASURED, live, on Dispatcharr 0.29.0 against a real Xtream Codes
account on 2026-08-21 (bead ``…-kdz6p``'s acceptance environment):

* Source A: 777 ``dispatcharr_channels_channelgroupm3uaccount`` rows, exactly
  **2 enabled** — the choice that keeps the instance at 316 channels instead of
  the provider's 53,661 streams.
* Replica B after a sync whose every other counter was accurate: **0 rows**, and
  ``channel_group_drift`` reporting ``0`` throughout. Not wrong — silent. That
  counter measures which group a CHANNEL sits in, a different question.
* Given B's own credentials, B's refresh created all 777 rows from discovery and
  then logged ``Filtered 0 streams from 0 enabled categories`` and aborted:
  **0 streams where the source has 316.**
* The direction of the failure is not even stable. It is decided by
  ``auto_enable_new_groups_live``, which B faithfully inherits from A and which
  Dispatcharr's own serializer defaults to ``True``
  (``apps/m3u/serializers.py``: ``validated_data.pop(..., True)`` on create).
  With it ``True``, the SAME empty selection made B's discovery refresh enable
  **777 of 777** categories — the provider's entire catalogue.

Carrying the selection removes both outcomes, because the groups are then no
longer "new to this account" the first time the replica refreshes.

STRUCTURE USED (declared, per the sibling beads' convention): a top-level ``int``
on :class:`RestoreReport` (``provider_group_selection_unapplied``) plus a
dedicated ``provider_group_selection_details`` model list, mirroring the
``channel_group_drift`` / ``ChannelGroupDriftDetail`` pair. NOT
``skip_details``/``SkipReason`` — a dropped group selection is not a skipped
ENTITY, it is a partially-delivered field on an entity that was created — and
NOT ``failure_details``/``FailureReason``, because M3U_ACCOUNT is a FATAL
category and a group-settings write that did not land must not roll the whole
cycle back over an account that exists and is otherwise correct.

LAYERS. Tests 1-3 cross the real engine seam through ``SyncHarness`` (the
gather → ``run_sync`` → ``run_restore`` → the importers → B's stateful stores).
Tests 4-7 are unit-level over the apply helper and the summary renderer. Test 8
is the restore-path regression on the shared-helper refactor.
"""

import pytest

from dbas.importers.m3u_accounts import (
    apply_deferred_auto_sync,
    apply_group_selection,
)
from dbas.restore_contracts import EntityType, IdRemapTable, RestoreReport
from tests.fixtures.sync_harness import (
    StatefulDispatcharrFake,
    SyncHarness,
)


# ---------------------------------------------------------------------------
# 1-3. Through the real sync engine, A -> B.
# ---------------------------------------------------------------------------


def _source_with_a_narrow_selection() -> StatefulDispatcharrFake:
    """Source A: three provider groups on its account, exactly ONE enabled.

    The shape of the live finding at test scale. ``News`` is the enabled one;
    ``Sports`` and ``Movies`` are the 775 the operator switched off.
    """
    source = StatefulDispatcharrFake.seeded_source()
    source.channel_groups.create({"name": "Movies"})
    groups = {g["name"]: g["id"] for g in source.channel_groups.list()}
    account_id = source.m3u_accounts.list()[0]["id"]
    source.set_group_selection(
        account_id,
        [
            {
                "channel_group": groups["News"],
                "enabled": True,
                "custom_properties": {"xc_id": "10387"},
            },
            {"channel_group": groups["Sports"], "enabled": False},
            {"channel_group": groups["Movies"], "enabled": False},
        ],
    )
    return source


@pytest.mark.asyncio
async def test_replica_inherits_the_sources_enabled_group_selection(tmp_path):
    """A's 1-of-3 enabled selection reaches B's provider account (…-avrix).

    Layer: the real engine seam (gather → run_sync → importers → B's stores).
    """
    source = _source_with_a_narrow_selection()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    dest_account = next(
        a for a in dest.m3u_accounts.list() if a.get("name") == "Provider A"
    )
    dest_groups = {g["name"]: g["id"] for g in dest.channel_groups.list()}

    # Every one of the source's three selections crossed...
    selected = {
        gid for (acc, gid) in dest.group_settings if acc == dest_account["id"]
    }
    assert selected == {
        dest_groups["News"],
        dest_groups["Sports"],
        dest_groups["Movies"],
    }, "the replica's provider account did not receive the source's group rows"

    # ...and the ENABLED flag — the load-bearing half — crossed with them.
    assert dest.enabled_group_ids(dest_account["id"]) == {dest_groups["News"]}, (
        "the replica would ingest a different set of provider groups than the "
        "source does"
    )


@pytest.mark.asyncio
async def test_selection_is_keyed_by_the_replicas_own_group_ids(tmp_path):
    """The selection is REMAPPED, never forwarded at the source's pks.

    The stores are id-offset per instance, so a raw forward would either point
    at nothing on B or — the dangerous case — at an UNRELATED group that happens
    to sit at that id.
    """
    source = _source_with_a_narrow_selection()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    source_group_ids = {g["id"] for g in source.channel_groups.list()}
    dest_group_ids = {g["id"] for g in dest.channel_groups.list()}
    assert not (source_group_ids & dest_group_ids), (
        "fixture precondition: A's and B's group ids must not overlap, or this "
        "assertion cannot tell a remap from a raw forward"
    )
    written = {gid for (_acc, gid) in dest.group_settings}
    assert written <= dest_group_ids
    assert not (written & source_group_ids)


@pytest.mark.asyncio
async def test_sync_never_triggers_the_replicas_provider_refresh(tmp_path):
    """ADR-013 S9 holds: the SELECTION is written, the PROVIDER is not touched.

    S9 forbids re-triggering the destination's provider auto-sync every cycle.
    The group-settings endpoint is a pure destination-side upsert, so applying it
    is not what S9 rules out — but the three steps the RESTORE path goes on to
    perform (is_active toggle, refresh trigger, stream-count poll) are, and none
    of them may fire here.
    """
    source = _source_with_a_narrow_selection()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # The guard is non-vacuous: the fake DEFINES both methods and records every
    # call, so a path that made them would be seen rather than raising.
    assert dest.m3u_refresh_calls == [], (
        "the sync cycle triggered a provider refresh on the destination"
    )
    assert [
        (acc, data) for (acc, data) in dest.m3u_patch_calls if "is_active" in data
    ] == [], "the sync cycle ran the restore path's is_active toggle workaround"
    # And it did do the work it is allowed to do, so the two assertions above are
    # not passing because nothing ran at all.
    assert dest.group_settings, "nothing was applied — the assertions above are vacuous"


@pytest.mark.asyncio
async def test_the_selection_keeps_tracking_the_source_on_later_cycles(tmp_path):
    """STEADY STATE, not just the creating cycle (the …-ukjx5 shape).

    Measured live on 2026-08-21 before this half of the fix: with B already
    converged, enabling a THIRD provider category on A left B at two, on a cycle
    that reported ``SUCCESS`` with every counter at zero. The account is
    ``ALREADY_EXISTS_IDENTICAL`` from cycle 2 onward, so a selection deferred
    only on the CREATE path is a snapshot of the day the replica was built.
    """
    source = _source_with_a_narrow_selection()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    dest_account = next(
        a for a in dest.m3u_accounts.list() if a.get("name") == "Provider A"
    )
    dest_groups = {g["name"]: g["id"] for g in dest.channel_groups.list()}
    assert dest.enabled_group_ids(dest_account["id"]) == {dest_groups["News"]}

    # The operator enables a second group on the SOURCE, long after B was built.
    source_groups = {g["name"]: g["id"] for g in source.channel_groups.list()}
    source_account_id = source.m3u_accounts.list()[0]["id"]
    source.set_group_selection(
        source_account_id, [{"channel_group": source_groups["Sports"], "enabled": True}]
    )

    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert dest.enabled_group_ids(dest_account["id"]) == {
        dest_groups["News"],
        dest_groups["Sports"],
    }, "the replica stopped tracking the source's selection after the first cycle"
    # Still no provider refresh — converging the selection is not refetching.
    assert dest.m3u_refresh_calls == []


@pytest.mark.asyncio
async def test_a_pre_existing_account_is_converged_but_not_refetched():
    """``created: False`` converges the SELECTION and stops (restore path).

    A skip has never triggered a provider refresh, and making it do so would add
    one refresh plus a bounded poll per pre-existing account to every restore.
    """
    from unittest.mock import AsyncMock

    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 110, 210)
    report = RestoreReport(is_dry_run=False)

    client = AsyncMock()
    client.update_m3u_group_settings = AsyncMock(return_value={"message": "ok"})
    client.patch_m3u_account = AsyncMock(return_value={"success": True})
    client.refresh_m3u_account = AsyncMock(return_value={"success": True})

    deferred = _deferred([110], enabled={110})
    deferred[0]["created"] = False

    async def _boom(seconds):  # the poll must not even be entered
        raise AssertionError("the poll loop ran for a pre-existing account")

    summaries = await apply_deferred_auto_sync(
        deferred=deferred,
        client=client,
        remap=remap,
        report=report,
        sleep_fn=_boom,
        max_polls=3,
    )

    client.update_m3u_group_settings.assert_awaited_once()
    client.refresh_m3u_account.assert_not_awaited()
    client.patch_m3u_account.assert_not_awaited()
    assert summaries == [
        {"m3u_account_id": 901, "groups_applied": 1, "refreshed": False}
    ]


# ---------------------------------------------------------------------------
# 4-6. The reporting half — over the apply helper directly.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Minimal destination client: records the group-settings write."""

    def __init__(self, *, raises: Exception | None = None):
        self.calls: list[tuple[int, dict]] = []
        self._raises = raises

    async def update_m3u_group_settings(self, account_id: int, data: dict) -> dict:
        if self._raises is not None:
            raise self._raises
        self.calls.append((account_id, data))
        return {"message": "ok"}


def _deferred(source_group_ids: list[int], enabled: set[int]) -> list[dict]:
    return [
        {
            "m3u_account_id": 901,
            "settings": {
                "channel_groups": [
                    {"channel_group": gid, "enabled": gid in enabled}
                    for gid in source_group_ids
                ]
            },
        }
    ]


@pytest.mark.asyncio
async def test_a_fully_applied_selection_reports_nothing():
    """No crying wolf (…-15g1j): a selection that fully landed is not a finding.

    A counter that reads non-zero on every converged cycle forever is the
    ``…-posm1`` noise problem; this is the control that keeps it at zero.
    """
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 110, 210)
    remap.add(EntityType.CHANNEL_GROUP, 111, 211)
    report = RestoreReport(is_dry_run=False)
    client = _RecordingClient()

    await apply_group_selection(
        deferred=_deferred([110, 111], enabled={110}),
        client=client,
        remap=remap,
        report=report,
    )

    assert report.provider_group_selection_unapplied == 0
    assert report.provider_group_selection_details == []
    # Non-vacuous: the write really happened, with the enabled flag intact.
    (account_id, payload), = client.calls
    assert account_id == 901
    assert payload["group_settings"] == [
        {"channel_group": 210, "enabled": True},
        {"channel_group": 211, "enabled": False},
    ]


@pytest.mark.asyncio
async def test_a_selection_the_destination_cannot_hold_is_counted_and_named():
    """When a group is not on the destination, the run SAYS so.

    This is the "or the run says plainly that it will not" half of the invariant.
    """
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 110, 210)  # 111 deliberately unmapped
    report = RestoreReport(is_dry_run=False)
    client = _RecordingClient()

    await apply_group_selection(
        deferred=_deferred([110, 111], enabled={110}),
        client=client,
        remap=remap,
        report=report,
    )

    assert report.provider_group_selection_unapplied == 1
    detail, = report.provider_group_selection_details
    assert detail.destination_account_id == 901
    assert detail.selections_total == 2
    assert detail.selections_applied == 1
    assert detail.selections_unapplied == 1
    assert detail.enabled_applied == 1
    assert "not on this destination" in detail.reason


@pytest.mark.asyncio
async def test_a_rejected_group_settings_write_is_counted_and_named():
    """A destination that refuses the write is a loss, and is reported as one."""
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 110, 210)
    remap.add(EntityType.CHANNEL_GROUP, 111, 211)
    report = RestoreReport(is_dry_run=False)
    client = _RecordingClient(raises=RuntimeError("400 Bad Request"))

    await apply_group_selection(
        deferred=_deferred([110, 111], enabled={110}),
        client=client,
        remap=remap,
        report=report,
    )

    assert report.provider_group_selection_unapplied == 2
    detail, = report.provider_group_selection_details
    assert detail.selections_applied == 0
    assert "rejected" in detail.reason


def test_the_one_line_summary_names_the_unapplied_selection():
    """The operator surface an unattended scheduled run produces says it.

    ``report.notes`` does not count: the restore-complete UI renders it only when
    there is rollback residue, and the sync task's one-line message never carries
    it at all — which is exactly how the live run reported nine accurate counters
    and stayed silent about this.
    """
    from tasks.dbas_restore import DbasRestoreTask

    report = RestoreReport(is_dry_run=False)
    assert DbasRestoreTask._credential_reentry_suffix(report) == ""

    report.record_provider_group_selection(
        destination_account_id=901,
        selections_total=777,
        selections_applied=0,
        selections_unapplied=777,
        enabled_applied=0,
        reason="the source's channel group is not on this destination",
    )
    applied = DbasRestoreTask._credential_reentry_suffix(report)
    assert "777 provider group selection(s) did not reach" in applied
    assert "will not ingest the same content" in applied

    preview = DbasRestoreTask._credential_reentry_suffix(report, is_preview=True)
    assert "would not reach" in preview


# ---------------------------------------------------------------------------
# 8. The restore path is unchanged by the shared-helper refactor.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_path_still_refreshes_after_applying_the_selection():
    """The restore path keeps all four steps; only the sync path stops at one.

    Guards the refactor that extracted step 1 into the shared helper.
    """
    from unittest.mock import AsyncMock

    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 110, 210)
    report = RestoreReport(is_dry_run=False)

    client = AsyncMock()
    client.update_m3u_group_settings = AsyncMock(return_value={"message": "ok"})
    client.patch_m3u_account = AsyncMock(return_value={"success": True})
    client.refresh_m3u_account = AsyncMock(return_value={"success": True})

    async def _stream_count(account_id):
        return 5

    async def _sleep(seconds):
        return None

    await apply_deferred_auto_sync(
        deferred=_deferred([110], enabled={110}),
        client=client,
        remap=remap,
        report=report,
        stream_count_fn=_stream_count,
        sleep_fn=_sleep,
        max_polls=3,
        stable_polls_required=2,
    )

    client.update_m3u_group_settings.assert_awaited_once()
    client.refresh_m3u_account.assert_awaited_once()
    patched = [c.args[1] for c in client.patch_m3u_account.await_args_list]
    assert {"is_active": False} in patched
    assert {"is_active": True} in patched
    assert report.provider_group_selection_unapplied == 0
