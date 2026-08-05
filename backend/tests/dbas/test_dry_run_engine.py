"""Tests for the Phase-2 DBAS DRY-RUN ENGINE (enhancedchannelmanager-0i2vt.16).

Scope under test:

1. PARITY (the headline, anti-drift): the SAME archive run through the dry-run
   then through apply yields ``would_create == created`` /
   ``would_update == updated`` / ``would_skip == skipped`` per category. The
   dry-run counts come from the importers' OWN plan/match logic (the same code
   apply uses), not a parallel counter — if they drifted, this fails.
2. ZERO MUTATION: a dry-run fires NO create / update / delete / upload /
   bulk_delete on ANY importer's client — asserted across every mutator,
   ESPECIALLY the .15 logos ``bulk_delete_logos``.
3. DEFAULT-ON / UNBYPASSABLE: ``run_restore`` produces a dry-run unless an
   explicit ``confirm_apply=True`` is given AND the report is not a dry-run; a
   caller can never opt OUT of the dry-run. ``run_dry_run`` always plans.
4. NO ROLLBACK / NO DEFERRED on a dry-run.
5. AGGREGATE: counts roll up across every category into one RestoreReport, with
   ``is_dry_run=True`` and ``outcome=None`` (a plan has no realized outcome).

The Dispatcharr client is mocked at module level; the durable ledger writes to a
tmp dir, never CONFIG_DIR.
"""

import pytest
from unittest.mock import AsyncMock

from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import (
    dry_run_importer_steps,
    new_restore_id,
    run_dry_run,
    run_restore,
)

_GOOD_MANIFEST = {"schema_version": 1}

# Every client mutator a dry-run must NEVER call. ``bulk_delete_logos`` is the
# .15 destructive bulk-delete pre-step the grooming comment singles out.
_MUTATORS = (
    "create_m3u_account",
    "create_epg_source",
    "create_channel_group",
    "create_channel_profile",
    "create_stream_profile",
    "create_channel",
    "create_user",
    "create_stream",
    "update_channel",
    "update_profile_channel",
    "update_m3u_group_settings",
    "patch_m3u_account",
    "refresh_m3u_account",
    "upload_logo_file",
    "create_logo",
    "bulk_delete_logos",
    "delete_m3u_account",
    "delete_epg_source",
    "delete_channel_group",
    "delete_channel_profile",
    "delete_channel",
    "delete_stream",
    "delete_user",
)


# ---------------------------------------------------------------------------
# A shared archive — one selectable category per wired importer, destination
# EMPTY so every entity is a create (clean parity arithmetic).
# ---------------------------------------------------------------------------


def _archive_plan():
    """A multi-category plan: 2 m3u, 1 epg, 1 group, 1 profile, 1 stream-profile,
    1 user, 2 channels (one with an FK to the archived group), 1 logo miss + 1
    logo that will already exist on the destination (a skip)."""
    return ImportPlan(
        manifest=dict(_GOOD_MANIFEST),
        categories=[
            PlanCategory(
                entity_type=EntityType.M3U_ACCOUNT,
                entities=[
                    {"id": 1, "name": "Provider A"},
                    {"id": 2, "name": "Provider B"},
                ],
            ),
            PlanCategory(
                entity_type=EntityType.EPG_SOURCE,
                entities=[{"id": 5, "name": "XMLTV Main", "source_type": "xmltv"}],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL_GROUP,
                entities=[{"id": 10, "name": "Sports"}],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL_PROFILE,
                entities=[{"id": 20, "name": "Default Profile"}],
            ),
            PlanCategory(
                entity_type=EntityType.STREAM_PROFILE,
                entities=[{"id": 30, "name": "Proxy"}],
            ),
            PlanCategory(
                entity_type=EntityType.USER,
                entities=[{"id": 40, "username": "alice"}],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[
                    {"id": 50, "name": "ESPN", "channel_number": 1, "channel_group_id": 10},
                    {"id": 51, "name": "CNN", "channel_number": 2},
                ],
            ),
            PlanCategory(
                entity_type=EntityType.LOGO,
                entities=[
                    # A miss → would_create on dry-run, created on apply. Valid PNG.
                    {
                        "id": 60,
                        "name": "espn-logo",
                        "filename": "espn.png",
                        "content_type": "image/png",
                        "content_b64": _PNG_B64,
                    },
                    # Already exists on the destination by name → would_skip/skipped.
                    {"id": 61, "name": "existing-logo", "filename": "existing.png"},
                    # A remotely-hosted logo: no bytes, no filename, restored by
                    # re-creating the row from its archived URL (bead …-dgnms).
                    # The dry run must SIMULATE that branch, not fall through to
                    # the byte validator and invent a failure.
                    {"id": 62, "name": "cdn-logo", "url": "https://cdn.example/cdn.png"},
                ],
            ),
        ],
    )


# Smallest valid PNG (1x1) so the logos importer's magic-byte check passes.
import base64  # noqa: E402

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


def _selected_all():
    """Select every category in the plan (opt-in flags)."""
    return None  # plan categories default selected=True


def _client(*, created_ids=None):
    """An AsyncMock client.

    READ methods return realistic shapes for an EMPTY destination (so every
    archived entity is a create) EXCEPT one pre-existing logo named
    'existing-logo' (so logo id=61 is a skip — exercising would_skip parity).

    Every MUTATOR returns a created dict with a destination id so apply records a
    creation; on a dry-run none of these are ever called (the assertions verify).
    """
    ids = created_ids or {}
    client = AsyncMock()

    # --- READ methods (exercised on BOTH dry-run and apply). ---
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    client.get_users = AsyncMock(return_value=[])
    client.get_current_user = AsyncMock(return_value={"id": 1, "username": "operator"})
    client.get_user_schema_write_fields = AsyncMock(
        return_value={"username", "email", "is_active"}
    )
    client.get_channels = AsyncMock(return_value={"results": [], "count": 0})
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    # One pre-existing destination logo → logo 'existing-logo' is a skip.
    client.get_all_logos_paginated = AsyncMock(
        return_value=[{"id": 700, "name": "existing-logo", "url": "/logos/existing.png"}]
    )

    # --- MUTATORS (only ever called on apply). ---
    client.create_m3u_account = AsyncMock(return_value={"id": 901})
    client.create_epg_source = AsyncMock(return_value={"id": 905})
    client.create_channel_group = AsyncMock(return_value={"id": 910})
    client.create_channel_profile = AsyncMock(return_value={"id": 920})
    client.create_stream_profile = AsyncMock(return_value={"id": 930})
    client.create_channel = AsyncMock(side_effect=_channel_creator())
    client.create_user = AsyncMock(return_value={"id": 940})
    client.upload_logo_file = AsyncMock(return_value={"id": 960})
    client.create_logo = AsyncMock(return_value={"id": 965})
    client.update_channel = AsyncMock(return_value={"id": 0})
    client.update_profile_channel = AsyncMock(return_value=None)
    client.update_m3u_group_settings = AsyncMock(return_value=None)
    client.patch_m3u_account = AsyncMock(return_value=None)
    client.refresh_m3u_account = AsyncMock(return_value=None)
    client.bulk_delete_logos = AsyncMock(return_value=None)
    client.create_stream = AsyncMock(return_value={"id": 970})
    for name in (
        "delete_m3u_account",
        "delete_epg_source",
        "delete_channel_group",
        "delete_channel_profile",
        "delete_channel",
        "delete_stream",
        "delete_user",
    ):
        setattr(client, name, AsyncMock(return_value=None))
    return client


def _channel_creator():
    counter = {"n": 0}

    async def _create(payload):
        counter["n"] += 1
        return {"id": 500 + counter["n"]}

    return _create


def _assert_no_mutations(client):
    """Assert NOT ONE client mutator was awaited (the zero-mutation guarantee)."""
    for name in _MUTATORS:
        method = getattr(client, name, None)
        if method is not None:
            method.assert_not_awaited()


# ---------------------------------------------------------------------------
# 1. PARITY — dry-run counts == apply counts, per category (anti-drift).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_counts_equal_apply_counts(tmp_path):
    plan = _archive_plan()

    # --- Dry-run pass. ---
    dry = await run_dry_run(plan=_archive_plan(), client=_client(), ledger_dir=tmp_path)
    assert dry.is_dry_run is True
    assert dry.outcome is None  # a plan has no realized outcome

    # --- Apply pass (same archive, same registry). ---
    apply_report = RestoreReport(is_dry_run=False)
    apply_ledger = RollbackLedger(restore_id=new_restore_id())
    applied = await run_restore(
        plan=plan,
        client=_client(),
        steps=dry_run_importer_steps(),
        report=apply_report,
        ledger=apply_ledger,
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert applied.is_dry_run is False

    # --- PARITY: per category, would_* == realized counts. ---
    dry_by_type = {c.entity_type: c for c in dry.categories}
    apply_by_type = {c.entity_type: c for c in applied.categories}
    # Every category the apply touched must be present in the dry-run plan too.
    for entity_type, applied_cat in apply_by_type.items():
        dry_cat = dry_by_type.get(entity_type)
        assert dry_cat is not None, f"dry-run missing category {entity_type}"
        assert dry_cat.would_create == applied_cat.created, (
            f"{entity_type}: would_create {dry_cat.would_create} != created "
            f"{applied_cat.created}"
        )
        assert dry_cat.would_update == applied_cat.updated, (
            f"{entity_type}: would_update {dry_cat.would_update} != updated "
            f"{applied_cat.updated}"
        )
        assert dry_cat.would_skip == applied_cat.skipped, (
            f"{entity_type}: would_skip {dry_cat.would_skip} != skipped "
            f"{applied_cat.skipped}"
        )


@pytest.mark.asyncio
async def test_parity_specific_counts(tmp_path):
    # Spot-check the arithmetic so a silently-empty parity can't pass vacuously.
    dry = await run_dry_run(plan=_archive_plan(), client=_client(), ledger_dir=tmp_path)
    by = {c.entity_type: c for c in dry.categories}
    assert by[EntityType.M3U_ACCOUNT].would_create == 2
    assert by[EntityType.EPG_SOURCE].would_create == 1
    assert by[EntityType.CHANNEL_GROUP].would_create == 1
    assert by[EntityType.CHANNEL].would_create == 2
    # The byte-bearing miss + the remotely-hosted logo the URL path re-creates.
    assert by[EntityType.LOGO].would_create == 2
    assert by[EntityType.LOGO].would_skip == 1     # the pre-existing logo
    assert by[EntityType.LOGO].failed == 0         # dgnms: no invented failures
    # NOTHING is reported lost: one logo restores from its bytes, one by URL,
    # one is already on the destination. logo_misses is the operator-facing
    # "you have lost these" count, not a would-create count.
    assert dry.logo_misses == 0


# ---------------------------------------------------------------------------
# 2. ZERO MUTATION on a dry-run — across EVERY importer's client mutators.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_makes_zero_client_mutations(tmp_path):
    client = _client()
    await run_dry_run(plan=_archive_plan(), client=client, ledger_dir=tmp_path)
    _assert_no_mutations(client)


@pytest.mark.asyncio
async def test_dry_run_never_bulk_deletes_logos(tmp_path):
    # The .15 destructive bulk-delete pre-step must NEVER fire on a dry-run,
    # regardless of any clear-existing intent.
    client = _client()
    await run_dry_run(plan=_archive_plan(), client=client, ledger_dir=tmp_path)
    client.bulk_delete_logos.assert_not_awaited()
    client.upload_logo_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_writes_no_ledger_entries(tmp_path):
    # Nothing is created, so nothing is ledgered — nothing to ever roll back.
    dry = await run_dry_run(plan=_archive_plan(), client=_client(), ledger_dir=tmp_path)
    assert dry.is_dry_run is True
    # No ledger file is left behind for a dry-run.
    assert not any(tmp_path.glob("restore_ledger_*.json"))


# ---------------------------------------------------------------------------
# 3. DEFAULT-ON, UNBYPASSABLE — apply is opt-IN; dry-run cannot be opted out.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_restore_without_confirm_is_forced_dry_run(tmp_path):
    # A non-dry-run report + no confirm → the guardrail forces a dry-run and makes
    # ZERO mutations. The caller cannot mutate without an explicit confirm.
    client = _client()
    report = RestoreReport(is_dry_run=False)  # caller asked to apply...
    out = await run_restore(
        plan=_archive_plan(),
        client=client,
        steps=dry_run_importer_steps(),
        report=report,
        ledger=RollbackLedger(restore_id="rid-x"),
        remap=IdRemapTable(),
        confirm_apply=False,  # ...but did NOT confirm.
        ledger_dir=tmp_path,
    )
    assert out.is_dry_run is True  # forced
    _assert_no_mutations(client)
    assert any("apply not confirmed" in n for n in out.notes)


@pytest.mark.asyncio
async def test_run_restore_with_confirm_applies(tmp_path):
    # Explicit confirm + non-dry-run report → mutations DO happen.
    client = _client()
    report = RestoreReport(is_dry_run=False)
    out = await run_restore(
        plan=_archive_plan(),
        client=client,
        steps=dry_run_importer_steps(),
        report=report,
        ledger=RollbackLedger(restore_id="rid-apply"),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.is_dry_run is False
    client.create_m3u_account.assert_awaited()


@pytest.mark.asyncio
async def test_confirm_apply_does_not_override_dry_run_report(tmp_path):
    # A dry-run report stays a dry-run even if a confirm is (mistakenly) passed —
    # the report's is_dry_run is the operator's plan intent and wins.
    client = _client()
    report = RestoreReport(is_dry_run=True)
    out = await run_restore(
        plan=_archive_plan(),
        client=client,
        steps=dry_run_importer_steps(),
        report=report,
        ledger=RollbackLedger(restore_id="rid-y"),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert out.is_dry_run is True
    _assert_no_mutations(client)


@pytest.mark.asyncio
async def test_run_dry_run_always_plans(tmp_path):
    out = await run_dry_run(plan=_archive_plan(), client=_client(), ledger_dir=tmp_path)
    assert out.is_dry_run is True
    assert out.outcome is None


# ---------------------------------------------------------------------------
# 4. NO ROLLBACK / NO DEFERRED on a dry-run.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_runs_no_deferred_auto_sync(tmp_path):
    # The M3U importer would return deferred auto-sync settings on apply; on a
    # dry-run the deferred phase must NOT fire (no refresh / patch / group-update).
    client = _client()
    await run_dry_run(plan=_archive_plan(), client=client, ledger_dir=tmp_path)
    client.refresh_m3u_account.assert_not_awaited()
    client.patch_m3u_account.assert_not_awaited()
    client.update_m3u_group_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_runs_no_rollback_deletes(tmp_path):
    client = _client()
    await run_dry_run(plan=_archive_plan(), client=client, ledger_dir=tmp_path)
    for name in (
        "delete_m3u_account",
        "delete_channel",
        "delete_channel_group",
        "delete_user",
    ):
        getattr(client, name).assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. AGGREGATE — counts roll up across categories into one report.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_aggregates_all_categories(tmp_path):
    dry = await run_dry_run(plan=_archive_plan(), client=_client(), ledger_dir=tmp_path)
    present = {c.entity_type for c in dry.categories}
    # Every wired category that had entities appears in the aggregated report.
    for entity_type in (
        EntityType.M3U_ACCOUNT,
        EntityType.EPG_SOURCE,
        EntityType.CHANNEL_GROUP,
        EntityType.CHANNEL_PROFILE,
        EntityType.STREAM_PROFILE,
        EntityType.USER,
        EntityType.CHANNEL,
        EntityType.LOGO,
    ):
        assert entity_type in present, f"missing {entity_type} in aggregate"
    total_would_create = sum(c.would_create for c in dry.categories)
    # 2 m3u + 1 epg + 1 group + 1 profile + 1 stream-profile + 1 user + 2 channels
    # + 1 byte-bearing logo miss + 1 URL-restorable logo = 11.
    assert total_would_create == 11


@pytest.mark.asyncio
async def test_dry_run_preflight_refusal_still_zero_mutation(tmp_path):
    # A plan that fails pre-flight (dangling channel FK) is refused with no
    # mutation even on the dry-run path — same chokepoint as apply.
    plan = ImportPlan(
        manifest=dict(_GOOD_MANIFEST),
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[{"id": 1, "name": "ESPN", "channel_group_id": 9999}],
            )
        ],
    )
    client = _client()
    out = await run_dry_run(plan=plan, client=client, ledger_dir=tmp_path)
    assert out.outcome is None
    assert any("pre-flight refused" in n for n in out.notes)
    _assert_no_mutations(client)


# ---------------------------------------------------------------------------
# 6. The dry-run registry wires every importer (vs. the apply seams).
# ---------------------------------------------------------------------------


def test_dry_run_registry_wires_all_importers():
    steps = dry_run_importer_steps()
    wired = {s.entity_type for s in steps if s.importer is not None}
    # Every entity-bearing importer is wired for the dry-run (unlike the apply
    # default registry, which leaves EPG / groups-profiles / logos as seams).
    for entity_type in (
        EntityType.M3U_ACCOUNT,
        EntityType.EPG_SOURCE,
        EntityType.CHANNEL_GROUP,
        EntityType.CHANNEL_PROFILE,
        EntityType.STREAM_PROFILE,
        EntityType.USER,
        EntityType.CHANNEL,
        EntityType.LOGO,
    ):
        assert entity_type in wired, f"{entity_type} not wired in dry-run registry"
    # Order mirrors the hard Phase-2 sequence: M3U first, logos last.
    order = [s.entity_type for s in steps]
    assert order[0] == EntityType.M3U_ACCOUNT
    assert order[-1] == EntityType.LOGO
    assert order.index(EntityType.CHANNEL_GROUP) < order.index(EntityType.CHANNEL)
