"""A replica's M3U account tracks its source, field by field (bead ``…-zszjd``).

THE INVARIANT UNDER TEST, stated as a property. ``auto_enable_new_groups_live``
is ONE EXAMPLE of it and is deliberately not the specification::

    A field set on source A is the field set on replica B after the next sync
    cycle, for EVERY field on the M3U account, except those carrying a written
    exclusion in ``dbas.importers.m3u_accounts.NEVER_CONVERGE_FIELDS``.

WHAT WAS MEASURED, and why the flag is the wrong unit to test. During bead
``…-avrix``, ``auto_enable_new_groups_live`` was flipped on A (database only, no
refresh), a cycle was run, and B stayed on the old value — the account matched
``ALREADY_EXISTS_IDENTICAL`` and was never written to. Spike ``xp6mp`` ruled
that skip, and the ruling covered the WHOLE ROW: ``server_url``,
``max_streams``, ``user_agent``, ``refresh_interval``, ``custom_properties``,
``username``, ``password``, ``account_type``, ``priority``,
``stale_stream_days``, ``is_active`` and the four preference booleans all froze
at their first-sync values. A fix scoped to the measured flag would have left
nineteen fields diverging exactly as silently. So the convergence test is
PARAMETERISED over the field surface, and the exclusion test is parameterised
over the exclusion register.

THE SECOND PROPERTY, which the defect's own worst feature demands. The bead's
finding is that the divergence was *silent*: "B stayed false … silently and
permanently, with nothing reporting the drift." So drift is REPORTED while it
exists — on the preview and on the apply — and the report CLEARS when the drift
does, which is what keeps it a signal rather than a permanent red mark.

LAYERS, declared. Tests 1-6 cross the real engine seam (``SyncHarness``: the
gather → ``run_sync`` → ``run_restore`` → the reused importers → B's stateful
stores), so they exercise the production path end to end rather than one helper.
Tests 7-10 are unit-level over ``build_convergence_patch`` and the report
recorder, where a per-field property is cheapest to state exhaustively.

FIXTURE FIDELITY. ``StatefulDispatcharrFake.patch_m3u_account`` models
Dispatcharr 0.29.0's ``M3UAccountSerializer.update`` — the four preference
booleans are popped into ``custom_properties`` and the blob is MERGED rather
than replaced — because both behaviours decide whether these assertions can fail
at all. A fake that stored the booleans at the top level would contradict its own
``get_m3u_accounts``, which projects them back out of the blob.
"""

import pytest

from dbas.importers.m3u_accounts import (
    NEVER_CONVERGE_FIELDS,
    build_convergence_patch,
)
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
)
from tests.fixtures.sync_harness import (
    StatefulDispatcharrFake,
    SyncHarness,
    make_sync_target,
)


# ---------------------------------------------------------------------------
# Shared setup: A and B already hold the SAME account by name, with B's copy
# frozen at the values it was created with — the exact state the defect leaves.
# ---------------------------------------------------------------------------

#: The account name both instances hold. Convergence is triggered by the
#: ALREADY_EXISTS_IDENTICAL match, which is a name match, so the two rows must
#: agree here and may differ everywhere else.
ACCOUNT_NAME = "Provider A"

#: The FIELD SURFACE this bead makes converge, with a source value and the
#: stale destination value it must overwrite. Every entry is a real
#: ``M3UAccountSerializer`` field on Dispatcharr 0.29.0 (``apps/m3u/
#: serializers.py``, ``Meta.fields``), read 2026-08-23 rather than guessed.
#:
#: The four preference booleans are the ones stored inside
#: ``custom_properties``; the rest are plain columns. Both storage shapes are
#: represented on purpose — they take different code paths through Dispatcharr's
#: serializer, and a test covering only one would pass while the other diverged.
CONVERGING_FIELDS: dict[str, tuple[object, object]] = {
    # (source value, stale destination value)
    "server_url": ("http://provider-a.test/rotated.m3u", "http://old.test/x.m3u"),
    "username": ("operator2", "operator"),
    "password": ("ROTATED-SECRET", "OLD-SECRET"),
    "max_streams": (12, 3),
    "refresh_interval": (24, 6),
    "account_type": ("XC", "STD"),
    "priority": (5, 1),
    "stale_stream_days": (14, 7),
    "is_active": (False, True),
    "file_path": ("/data/a.m3u", "/data/old.m3u"),
    # Stored inside ``custom_properties`` by the serializer (…-avrix).
    "auto_enable_new_groups_live": (False, True),
    "auto_enable_new_groups_vod": (False, True),
    "auto_enable_new_groups_series": (False, True),
    "enable_vod": (True, False),
}


def _stored_row(fields: dict, *, index: int) -> dict:
    """Turn a ``{field: (source, dest)}`` map into ONE row as Dispatcharr STORES it.

    The four preference booleans are stored inside ``custom_properties``, not at
    the top level — the serializer projects them out on read and pops them back
    in on write (0.29.0 ``apps/m3u/serializers.py``). Seeding them at the top
    level of a stored row is a fixture that cannot drift: the very next
    ``get_m3u_accounts`` overwrites the top-level value from the blob, so both
    instances read back Dispatcharr's default and the test asserts a convergence
    that was never needed. This helper is the one place that split lives.

    Args:
        index: ``0`` for the SOURCE value, ``1`` for the stale DESTINATION value.
    """
    row: dict = {"name": ACCOUNT_NAME}
    custom: dict = {}
    for name, values in fields.items():
        if name in StatefulDispatcharrFake._PREFERENCE_DEFAULTS:
            custom[name] = values[index]
        else:
            row[name] = values[index]
    if custom:
        row["custom_properties"] = custom
    return row


def _two_instances_with_a_drifted_account(
    *, drifted: dict | None = None
) -> tuple[StatefulDispatcharrFake, StatefulDispatcharrFake]:
    """Build (A, B) where B already holds ``Provider A`` with stale values.

    ``drifted`` names the fields to skew; ``None`` skews the whole
    :data:`CONVERGING_FIELDS` surface at once.
    """
    fields = CONVERGING_FIELDS if drifted is None else drifted

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    source_account = source.m3u_accounts.list()[0]
    source.m3u_accounts.update(source_account["id"], _stored_row(fields, index=0))

    # B's copy: same NAME (so it matches), stale everywhere else.
    dest.m3u_accounts.create(_stored_row(fields, index=1))
    return source, dest


async def _dest_account(dest: StatefulDispatcharrFake) -> dict:
    """B's ``Provider A`` row, as B's own list endpoint serializes it."""
    rows = await dest.get_m3u_accounts()
    matching = [r for r in rows if r.get("name") == ACCOUNT_NAME]
    assert matching, "the destination lost the account under test"
    return matching[0]


# ---------------------------------------------------------------------------
# 1. THE INVARIANT, through the real engine, parameterised over the field
#    surface rather than over the one flag that exposed it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", sorted(CONVERGING_FIELDS))
async def test_every_field_set_on_the_source_is_the_field_set_on_the_replica(
    field_name, tmp_path
):
    """One cycle makes B's value equal A's — for each field independently.

    Parameterised per field, not asserted over a bulk update, so a fix that
    happens to carry the blob-stored booleans while dropping the plain columns
    (or the reverse) fails on the field it dropped instead of passing on the
    field it kept.
    """
    source_value, stale_value = CONVERGING_FIELDS[field_name]
    source, dest = _two_instances_with_a_drifted_account(
        drifted={field_name: (source_value, stale_value)}
    )
    before = await _dest_account(dest)
    assert before[field_name] == stale_value, "fixture did not actually drift"

    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    after = await _dest_account(dest)
    assert after[field_name] == source_value


@pytest.mark.asyncio
async def test_the_whole_field_surface_converges_in_one_cycle(tmp_path):
    """All fields at once — a replica does not need N cycles to catch up."""
    source, dest = _two_instances_with_a_drifted_account()
    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    after = await _dest_account(dest)
    diverged = {
        name: (after.get(name), values[0])
        for name, values in CONVERGING_FIELDS.items()
        if after.get(name) != values[0]
    }
    assert diverged == {}


# ---------------------------------------------------------------------------
# 2. THE DRIFT IS REPORTED WHILE IT EXISTS — the defect's worst property.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_apply_reports_which_fields_drifted_by_name(tmp_path):
    """Field NAMES on the report, never values — the account carries secrets."""
    source, dest = _two_instances_with_a_drifted_account()
    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert report.account_field_drift > 0
    detail = next(
        d for d in report.account_field_drift_details if d.name == ACCOUNT_NAME
    )
    assert detail.applied is True
    assert set(detail.fields) >= set(CONVERGING_FIELDS)

    # No VALUE may appear anywhere in the detail row — this account's password
    # and its credential-bearing URL are both in the converged set.
    rendered = detail.model_dump_json()
    assert "ROTATED-SECRET" not in rendered
    assert "provider-a.test" not in rendered


@pytest.mark.asyncio
async def test_a_preview_reports_the_drift_and_writes_nothing(tmp_path):
    """The preview is the prediction: same drift, zero writes, zero shortfall."""
    source, dest = _two_instances_with_a_drifted_account()
    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=False, ledger_dir=tmp_path)

    assert report.account_field_drift > 0
    # Nothing was attempted, so nothing can have fallen short. A preview that
    # manufactured a shortfall would report a loss the apply it previews would
    # not have produced.
    assert report.account_convergence_unapplied == 0
    assert dest.m3u_patch_calls == []
    after = await _dest_account(dest)
    assert after["max_streams"] == CONVERGING_FIELDS["max_streams"][1]


@pytest.mark.asyncio
async def test_the_counter_clears_once_the_replica_matches(tmp_path):
    """A converged replica reports ZERO — the signal is drift, not activity.

    The second cycle is the one that matters. A counter that keeps counting
    after the problem is fixed is the ``…-4mkoe`` permanent-non-zero trap, and it
    trains an operator to ignore the surface that is supposed to warn them.
    """
    source, dest = _two_instances_with_a_drifted_account()
    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    second = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert second.account_field_drift == 0
    assert second.account_convergence_unapplied == 0
    assert second.account_field_drift_details == []


# ---------------------------------------------------------------------------
# 3. A REFUSED WRITE IS REPORTED, NOT CATASTROPHIC.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_convergence_write_degrades_without_rolling_back(tmp_path):
    """M3U_ACCOUNT is a FATAL category; a field write must not use that door.

    Rolling a whole replica back over one setting is the trade the ``…-d0agi``
    logos drill already paid once (one unwritable image rolled back 44 restored
    entities). The drift is reported and forbids SUCCESS; nothing is deleted.
    """
    source, dest = _two_instances_with_a_drifted_account()

    def refuse_patch(method: str, payload) -> None:
        if method == "patch_m3u_account":
            raise RuntimeError("destination refused the update")

    dest.inject_fault(refuse_patch)
    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    detail = next(
        d for d in report.account_field_drift_details if d.name == ACCOUNT_NAME
    )
    assert detail.applied is False
    assert detail.reason
    # The shortfall half fires, so the cycle can never read as a clean success…
    assert report.account_convergence_unapplied > 0
    assert report.delivery_shortfalls().get("account_convergence_unapplied")
    assert report.outcome != RestoreOutcome.SUCCESS
    # …and the account itself is still there. Nothing was rolled back.
    assert [r["name"] for r in dest.m3u_accounts.list()].count(ACCOUNT_NAME) == 1
    assert report.category(EntityType.M3U_ACCOUNT).failed == 0


# ---------------------------------------------------------------------------
# 4. THE EXCLUSIONS HOLD — each is a named harm or a named impossibility.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_destinations_own_health_state_is_never_overwritten(tmp_path):
    """``status`` / ``last_message`` are B's own account health.

    They are the two fields ``destination_account_looks_stale`` reads to tell an
    operator their replica cannot authenticate. Writing A's healthy ``status``
    over B's ``error`` would blind that detector on every cycle — turning a
    reported failure into a silent one, which is the shape this epic exists to
    remove.
    """
    source, dest = _two_instances_with_a_drifted_account(
        drifted={"max_streams": (12, 3)}
    )
    source_account = source.m3u_accounts.list()[0]
    source.m3u_accounts.update(
        source_account["id"], {"status": "success", "last_message": "all good"}
    )
    dest_account = await _dest_account(dest)
    dest.m3u_accounts.update(
        dest_account["id"],
        {"status": "error", "last_message": "No streams returned from provider"},
    )

    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    after = await _dest_account(dest)
    assert after["status"] == "error"
    assert after["last_message"] == "No streams returned from provider"
    # …while the convergeable field beside them did move, so the assertion above
    # is proving an exclusion rather than proving convergence never ran.
    assert after["max_streams"] == 12


@pytest.mark.asyncio
async def test_the_convergence_patch_never_carries_source_channel_group_pks(
    tmp_path,
):
    """``channel_groups`` carries A's group pks; on B those address other groups.

    The per-group ENABLE selection is the setting bead ``…-avrix`` proved decides
    whether the replica ingests nothing or the provider's entire 53,661-stream
    catalogue. It converges through the deferred group-selection path, which
    remaps every pk. Two writers on one setting, one of them unremapped, is
    strictly worse than one.
    """
    source, dest = _two_instances_with_a_drifted_account()
    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    patched_bodies = [body for _account_id, body in dest.m3u_patch_calls]
    assert patched_bodies, "no convergence PATCH was issued at all"
    for body in patched_bodies:
        assert "channel_groups" not in body
        assert "channel_group" not in body


# ---------------------------------------------------------------------------
# 5. Unit level: the patch builder as a property over the exclusion register.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("excluded", sorted(NEVER_CONVERGE_FIELDS))
def test_no_excluded_field_can_reach_the_patch(excluded):
    """Parameterised over the register itself, so a member added without a
    guard cannot pass silently — the ``…-posm1`` declaration-driven pattern."""
    archive = {
        "id": 1,
        "name": ACCOUNT_NAME,
        excluded: "SOURCE-VALUE",
        "max_streams": 12,
    }
    existing = {"id": 900, "name": ACCOUNT_NAME, excluded: "DEST-VALUE", "max_streams": 3}
    patch, drifted = build_convergence_patch(archive, existing, IdRemapTable())
    assert excluded not in patch
    assert excluded not in drifted


def test_a_replica_that_already_matches_produces_no_patch_at_all():
    """Convergence costs zero writes on a converged replica."""
    row = {"name": ACCOUNT_NAME, "max_streams": 12, "refresh_interval": 24}
    patch, drifted = build_convergence_patch(
        {"id": 1, **row}, {"id": 900, **row}, IdRemapTable()
    )
    assert patch == {}
    assert drifted == []


def test_a_destination_only_custom_property_is_not_reported_as_drift():
    """Dispatcharr MERGES ``custom_properties`` on update, so a key only B holds
    is one a PATCH physically cannot remove.

    Equality comparison would report it as drift on every cycle forever and
    issue a write every cycle that could not clear it. Subset comparison reports
    exactly what a PATCH can fix, so the counter clears when the drift does.
    """
    archive = {"id": 1, "name": ACCOUNT_NAME, "custom_properties": {"xc_id": "10387"}}
    existing = {
        "id": 900,
        "name": ACCOUNT_NAME,
        "custom_properties": {"xc_id": "10387", "b_only": "kept"},
    }
    patch, drifted = build_convergence_patch(archive, existing, IdRemapTable())
    assert patch == {}
    assert drifted == []

    # …but a key whose VALUE differs is drift, and is written.
    existing["custom_properties"]["xc_id"] = "99999"
    patch, drifted = build_convergence_patch(archive, existing, IdRemapTable())
    assert patch == {"custom_properties": {"xc_id": "10387"}}
    assert drifted == ["custom_properties"]


def test_an_unreadable_password_is_written_but_not_called_drift():
    """A destination that does not RETURN ``password`` is unreadable, not equal.

    Dispatcharr marks it ``write_only`` and re-adds it only for a caller at
    ``user_level >= 10``. A diff cannot decide it in either direction, so it
    crosses on every cycle — the PO's 2026-08-22 per-cycle credential ruling
    applied to the account that already exists — and is NOT counted as drift,
    because a counter that can never reach zero is noise.
    """
    archive = {"id": 1, "name": ACCOUNT_NAME, "password": "SECRET", "max_streams": 3}
    existing = {"id": 900, "name": ACCOUNT_NAME, "max_streams": 3}
    patch, drifted = build_convergence_patch(archive, existing, IdRemapTable())
    assert patch == {"password": "SECRET"}
    assert drifted == []


def test_the_recorder_is_the_only_writer_of_both_aggregates():
    """Aggregate and drill-down move together, and an empty call is a no-op."""
    report = RestoreReport(is_dry_run=False)
    report.record_account_field_drift(name="X", fields=[], applied=True)
    assert report.account_field_drift == 0
    assert report.account_field_drift_details == []

    report.record_account_field_drift(
        name="X", fields=["a", "b"], destination_account_id=9, applied=True
    )
    assert report.account_field_drift == 2
    assert report.account_convergence_unapplied == 0

    report.record_account_field_drift(
        name="Y", fields=["c"], destination_account_id=10, applied=False,
        reason="upstream refused",
    )
    assert report.account_field_drift == 3
    assert report.account_convergence_unapplied == 1
    assert len(report.account_field_drift_details) == 2


def test_the_archive_restore_path_still_leaves_an_existing_account_alone():
    """``converge_existing`` defaults OFF, so one-shot restore is unchanged.

    Continuous sync is what turns a frozen field into permanent silent
    divergence. A one-shot restore onto a populated instance is an operator
    action with a different blast radius, and widening it is not this bead's to
    decide — so the default has to stay where it was, and be pinned there.
    """
    import inspect

    from dbas.importers.m3u_accounts import import_m3u_accounts

    signature = inspect.signature(import_m3u_accounts)
    assert signature.parameters["converge_existing"].default is False


@pytest.mark.asyncio
async def test_the_sync_registry_is_the_thing_that_turns_convergence_on(tmp_path):
    """The engine's own step, not the shared builder — asserted by behaviour.

    A structural assertion on the registry would pass on a step that was wired
    but inert. This runs a cycle and looks at what reached B.
    """
    source, dest = _two_instances_with_a_drifted_account(
        drifted={"max_streams": (12, 3)}
    )
    harness = SyncHarness(
        source=source, dest=dest, target=make_sync_target()
    )
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)
    assert (await _dest_account(dest))["max_streams"] == 12
