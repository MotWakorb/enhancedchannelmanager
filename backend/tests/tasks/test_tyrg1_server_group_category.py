"""A replica's M3U accounts keep their provider connection grouping (…-tyrg1).

THE INVARIANT UNDER TEST::

    An M3U account's ``server_group`` reaches the replica as a reference to the
    REPLICA's own equivalent group — never as the source's raw pk, and never
    dropped when the group is resolvable.

WHY THIS IS NOT A "NICE TO HAVE". A Dispatcharr ``ServerGroup`` groups M3U
accounts that share provider credentials so they share a credential-scoped
connection counter (0.29.0 ``apps/m3u/connection_pool.py`` keys a Redis counter
on ``(group_id, credential fingerprint)``). Bead ``…-g8tyd`` made sync DROP the
FK, which was the right correctness floor — forwarding A's pk made a live B
answer ``400 {"server_group": ["Invalid pk \\"20\\" - object does not exist."]}``
and, M3U_ACCOUNT being a FATAL category, rolled the ENTIRE apply back. But the
cost of the drop is a replica whose accounts do not share a connection limit
until an operator recreates the grouping by hand: a replica that behaves
differently from its source until a human intervenes, which is exactly what
ADR-013's faithful-copy principle forbids. Under bead ``…-wd20y`` a provisioned
replica actually CONNECTS to the provider, so the shared counter is live rather
than theoretical.

RE-MEASURED BEFORE BUILDING, as the bead required. The readings it was filed on
were taken against Dispatcharr 0.28.2; the project standard is now
``dispatcharr:latest``. Against a live 0.29.0 container on 2026-08-23:

* ``apps/m3u/models.py:216`` — ``ServerGroup`` still has EXACTLY ONE field, a
  ``name`` with ``unique=True, max_length=100``. Nothing else.
* ``apps/m3u/serializers.py:420`` — ``ServerGroupSerializer.Meta.fields`` is
  exactly ``["id", "name"]``.
* ``apps/m3u/api_urls.py:28`` — the route is ``/api/m3u/server-groups/``.
* ``apps/m3u/models.py:39`` — the FK is
  ``on_delete=SET_NULL, null=True, blank=True``, so an account without one is
  valid.
* Consumers: ``apps/m3u/connection_pool.py`` is still the only BEHAVIOURAL one.
  The two other references are a ``select_related`` hint in
  ``apps/channels/models.py`` and the Django admin; neither reads the row's
  content.

THE FLOOR SURVIVES THE FEATURE. The drop is not reverted — it is demoted to the
fallback for an unresolvable FK. Test 4 pins that, and it is the assertion that
stops this bead from re-opening ``…-g8tyd``.

LAYERS. Tests 1-2 cross the real engine seam (``SyncHarness``). Tests 3-5 are
unit-level over the FK resolver. Tests 6-7 pin the registry ordering and the
rollback compensator, which are the two structural preconditions.
"""

import pytest

from dbas.importers.m3u_accounts import _build_create_payload
from dbas.restore_contracts import EntityType, IdRemapTable
from dbas.restore_orchestrator import (
    default_importer_steps,
    dry_run_importer_steps,
)
from tasks.dbas_sync_engine import sync_config_importer_steps
from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness


SOURCE_GROUP_NAME = "Shared Provider Pool"


# ---------------------------------------------------------------------------
# 1-2. Through the real engine, A -> B.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_replica_gets_the_group_and_the_account_points_at_ITS_id(tmp_path):
    """The whole feature in one assertion pair.

    The group has to EXIST on B, and the account's FK has to name B's id — not
    A's. The harness gives A and B disjoint id bases precisely so a leaked
    source pk is visible rather than coincidentally correct.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    source_group = next(
        g for g in source.server_groups.list() if g["name"] == SOURCE_GROUP_NAME
    )

    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    dest_group = next(
        (g for g in dest.server_groups.list() if g["name"] == SOURCE_GROUP_NAME), None
    )
    assert dest_group is not None, "the server group did not reach the replica"

    dest_account = next(
        a for a in dest.m3u_accounts.list() if a["name"] == "Provider A"
    )
    assert dest_account["server_group"] == dest_group["id"]
    # …and it is emphatically NOT the source's pk.
    assert dest_account["server_group"] != source_group["id"]


@pytest.mark.asyncio
async def test_a_second_cycle_is_a_no_op_for_the_group(tmp_path):
    """Idempotency: the group matches by name and is not recreated.

    Its name is the WHOLE row on 0.29.0, so a name match genuinely is an
    identical row — nothing is left uncompared behind that skip.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert second.category(EntityType.SERVER_GROUP).created == 0
    assert second.category(EntityType.SERVER_GROUP).skipped == 1
    assert len(dest.server_groups.list()) == 1


# ---------------------------------------------------------------------------
# 3-5. The FK resolver, including the floor that must not be traded away.
# ---------------------------------------------------------------------------


def test_a_resolvable_fk_is_rewritten_and_reports_no_degradation():
    remap = IdRemapTable()
    remap.add(EntityType.SERVER_GROUP, 20, 5020)
    payload, _redacted, _agent_ok, dropped = _build_create_payload(
        {"id": 1, "name": "Provider A", "server_group": 20}, remap
    )
    assert payload["server_group"] == 5020
    assert dropped is False


def test_an_unresolvable_fk_is_still_dropped_not_forwarded():
    """The ``…-g8tyd`` correctness floor, pinned against its own regression.

    This bead REPLACES the unconditional drop with a remap; it must not replace
    the drop with forwarding a raw source pk. A stale pk made a live B answer
    400 and — M3U_ACCOUNT being FATAL — rolled the entire apply back, costing
    the operator every entity the run had created.
    """
    payload, _redacted, _agent_ok, dropped = _build_create_payload(
        {"id": 1, "name": "Provider A", "server_group": 20}, IdRemapTable()
    )
    assert "server_group" not in payload
    assert dropped is True


def test_a_null_fk_is_left_alone_and_is_not_a_degradation():
    """``null=True`` on the column, so no group is a legal state, not a loss."""
    payload, _redacted, _agent_ok, dropped = _build_create_payload(
        {"id": 1, "name": "Provider A", "server_group": None}, IdRemapTable()
    )
    assert payload["server_group"] is None
    assert dropped is False


def test_a_non_integer_fk_is_dropped_rather_than_forwarded():
    """A shape the destination cannot accept must never reach the wire."""
    payload, _redacted, _agent_ok, dropped = _build_create_payload(
        {"id": 1, "name": "Provider A", "server_group": "not-a-pk"}, IdRemapTable()
    )
    assert "server_group" not in payload
    assert dropped is True


# ---------------------------------------------------------------------------
# 6-7. The two structural preconditions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "registry",
    [default_importer_steps, dry_run_importer_steps, sync_config_importer_steps],
    ids=["apply", "dry_run", "sync"],
)
def test_server_groups_run_before_m3u_accounts_in_all_three_registries(registry):
    """Bead ``…-9h6cv`` established FK-owner-before-dependent; ``…-efvyg``
    established that all three registries move together — a step wired into two
    of them is a cycle that behaves differently from its own preview."""
    order = [step.entity_type for step in registry()]
    assert EntityType.SERVER_GROUP in order
    assert order.index(EntityType.SERVER_GROUP) < order.index(EntityType.M3U_ACCOUNT)


@pytest.mark.asyncio
async def test_a_created_server_group_is_compensable_by_the_rollback(tmp_path):
    """A ledgered create with no compensator is an INCOMPLETE rollback.

    Driven through a real mid-run failure rather than asserted against the
    dispatch table, because a table entry proves the mapping exists and not that
    the rollback can reach it. Ordering the group FIRST is what puts it LAST in
    compensation order, so the accounts referencing it are deleted before it is.
    """
    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    def fail_the_channel_step(method: str, payload) -> None:
        if method == "create_channel":
            raise RuntimeError("destination refused the channel")

    dest.inject_fault(fail_the_channel_step)
    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # The run failed and rolled back, and the group it created is GONE — not
    # left behind as an orphan the next cycle would skip over.
    assert report.outcome is not None
    assert dest.server_groups.list() == []
    assert dest.m3u_accounts.list() == []
