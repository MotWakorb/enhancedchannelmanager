"""Tests for the bulk groups/profiles restore importer
(enhancedchannelmanager-0i2vt.12 — Phase 2).

Scope under test — THREE leaf-dependency categories restored together, each
producing the IdRemapTable mappings the Channels importer (4vouz) consumes:

1. Channel groups   -> EntityType.CHANNEL_GROUP
2. Channel profiles -> EntityType.CHANNEL_PROFILE
3. Stream profiles  -> EntityType.STREAM_PROFILE

For EACH category the same behaviours are exercised:

* Create happy path — the archived row is created via the category's Dispatcharr
  client create method; source->dest is registered in the IdRemapTable (correct
  EntityType) and the create is recorded in the RollbackLedger.
* Collision skip + remap-to-existing — an archived row whose identity (name,
  case-insensitive/trimmed) already exists on the destination is skipped
  ALREADY_EXISTS_IDENTICAL and its source id is remapped to the EXISTING dest id
  (so a later FK reference resolves) — never blind delete-all.
* Opt-in off -> no-op — nothing is created; every row is EXCLUDED_BY_OPERATOR.
* Dry-run -> zero creates + zero ledger; reports would_create.
* CONFLICT vs UPSTREAM_API_ERROR — a create that races into a uniqueness
  conflict is failed CONFLICT; a non-conflict error is UPSTREAM_API_ERROR.

FK remap + DEPENDENCY_UNRESOLVED: the three real Dispatcharr categories are
leaf dependencies (a group/profile has no outbound FK to another remapped
entity — channels point at THEM, not the reverse). The generic FK-remap path is
still exercised via a synthetic category config that declares a remappable FK,
proving an unresolvable FK is skipped DEPENDENCY_UNRESOLVED rather than created
with a stale archive id.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.groups_profiles``); the importer is exercised with an
AsyncMock client.
"""
import pytest
from unittest.mock import AsyncMock

from dbas.importers.groups_profiles import (
    _CATEGORY_CONFIGS,
    CategoryConfig,
    import_channel_groups,
    import_channel_profiles,
    import_groups_profiles,
    import_stream_profiles,
)
from dbas.restore_contracts import (
    EntityType,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _report(is_dry_run=False):
    return RestoreReport(is_dry_run=is_dry_run)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap():
    return IdRemapTable()


def _client(*, groups=None, channel_profiles=None, stream_profiles=None):
    """An AsyncMock Dispatcharr client with the three categories' get/create methods.

    Each create method returns a row echoing the payload with a fresh dest id so
    the importer's remap/ledger writes can be asserted.
    """
    client = AsyncMock()
    client.get_channel_groups = AsyncMock(return_value=groups or [])
    client.get_channel_profiles = AsyncMock(return_value=channel_profiles or [])
    client.get_stream_profiles = AsyncMock(return_value=stream_profiles or [])

    group_counter = {"n": 100}
    profile_counter = {"n": 200}
    stream_counter = {"n": 300}

    async def _create_group(name):
        group_counter["n"] += 1
        return {"id": group_counter["n"], "name": name}

    async def _create_channel_profile(data):
        profile_counter["n"] += 1
        return {"id": profile_counter["n"], **data}

    async def _create_stream_profile(data):
        stream_counter["n"] += 1
        return {"id": stream_counter["n"], **data}

    client.create_channel_group = AsyncMock(side_effect=_create_group)
    client.create_channel_profile = AsyncMock(side_effect=_create_channel_profile)
    client.create_stream_profile = AsyncMock(side_effect=_create_stream_profile)
    return client


# The three real categories, parametrized so each behavioural test runs once per
# category. ``fn`` is the per-category entry, ``etype`` its EntityType, ``getter``
# / ``creator`` the client method names, ``existing_kw`` the _client kwarg.
_CATEGORIES = [
    pytest.param(
        import_channel_groups,
        EntityType.CHANNEL_GROUP,
        "create_channel_group",
        "groups",
        id="channel_groups",
    ),
    pytest.param(
        import_channel_profiles,
        EntityType.CHANNEL_PROFILE,
        "create_channel_profile",
        "channel_profiles",
        id="channel_profiles",
    ),
    pytest.param(
        import_stream_profiles,
        EntityType.STREAM_PROFILE,
        "create_stream_profile",
        "stream_profiles",
        id="stream_profiles",
    ),
]


# ---------------------------------------------------------------------------
# Opt-in gating (per category)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_category_skipped_when_not_selected(fn, etype, creator, existing_kw):
    """OPT-IN: when the category is not selected, nothing is created — every
    archived row is recorded EXCLUDED_BY_OPERATOR."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await fn(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=False,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    getattr(client, creator).assert_not_called()
    cat = report.category(etype)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR
    assert len(ledger.entries) == 0


# ---------------------------------------------------------------------------
# Create happy path — remap + ledger (per category)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_create_happy_path_remap_and_ledger(fn, etype, creator, existing_kw):
    """An archived row is created; source->dest is registered in the IdRemapTable
    under the right EntityType, and the create is recorded in the RollbackLedger."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await fn(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    getattr(client, creator).assert_awaited_once()
    cat = report.category(etype)
    assert cat.created == 1
    dest_id = remap.resolve(etype, 5)
    assert dest_id is not None
    assert len(ledger.entries) == 1
    assert ledger.entries[0].entity_type == etype
    assert ledger.entries[0].destination_id == dest_id
    assert ledger.entries[0].label == "Sports"


@pytest.mark.asyncio
async def test_channel_group_create_passes_name_string():
    """Channel groups use ``create_channel_group(name)`` — a name STRING, not a
    dict (mirrors the existing client signature)."""
    client = _client()
    await import_channel_groups(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )
    client.create_channel_group.assert_awaited_once_with("Sports")


@pytest.mark.asyncio
async def test_profile_create_strips_source_id_and_readonly_fields():
    """A profile create payload drops the archive source id and the embedded /
    read-only fields (channels membership list, counts) — those are owned by the
    Channels importer, never sent on a profile create."""
    captured = {}

    async def _create(data):
        captured.update(data)
        return {"id": 201, **data}

    client = _client()
    client.create_channel_profile = AsyncMock(side_effect=_create)

    await import_channel_profiles(
        archive_rows=[{
            "id": 5,
            "name": "LiveTV",
            "channels": [1, 2, 3],
            "channel_count": 3,
            "created_at": "2020-01-01",
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )

    assert "id" not in captured
    assert "channels" not in captured
    assert "channel_count" not in captured
    assert "created_at" not in captured
    assert captured["name"] == "LiveTV"


# ---------------------------------------------------------------------------
# Collision skip + remap-to-existing (per category)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_existing_by_name_skipped_and_remapped(fn, etype, creator, existing_kw):
    """An archived row whose name already exists on the destination
    (case-insensitive / trimmed) is skipped ALREADY_EXISTS_IDENTICAL and its
    source id is remapped to the EXISTING dest id (so a later FK resolves).
    No create, no ledger entry — never blind delete-all."""
    client = _client(**{existing_kw: [{"id": 700, "name": "Sports"}]})
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await fn(
        archive_rows=[{"id": 5, "name": "  sports  "}],  # trimmed/case-insensitive
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    getattr(client, creator).assert_not_called()
    cat = report.category(etype)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert remap.resolve(etype, 5) == 700
    assert len(ledger.entries) == 0


# ---------------------------------------------------------------------------
# Dry-run (per category)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_dry_run_no_creates_no_ledger(fn, etype, creator, existing_kw):
    """Dry-run: no row is created, no ledger entry written; reports would_create."""
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()
    remap = _remap()

    await fn(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=True,
    )

    getattr(client, creator).assert_not_called()
    cat = report.category(etype)
    assert cat.would_create == 1
    assert cat.created == 0
    assert len(ledger.entries) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_dry_run_existing_reports_would_skip(fn, etype, creator, existing_kw):
    """Dry-run with a collision: reports would_skip with the reason, still no
    creates / ledger — and still records the remap so the dry-run plan is
    consistent with what apply would do."""
    client = _client(**{existing_kw: [{"id": 700, "name": "Sports"}]})
    report = _report(is_dry_run=True)
    remap = _remap()

    await fn(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=remap,
        is_dry_run=True,
    )

    getattr(client, creator).assert_not_called()
    cat = report.category(etype)
    assert cat.would_skip == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL


# ---------------------------------------------------------------------------
# Failure taxonomy — CONFLICT vs UPSTREAM_API_ERROR (per category)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_create_conflict_recorded_as_failure_conflict(fn, etype, creator, existing_kw):
    """A create that races into an upstream uniqueness conflict is failed CONFLICT
    (not skipped); no ledger entry, no remap."""
    client = _client()

    async def _conflict(*args, **kwargs):
        raise RuntimeError("name already exists")

    setattr(client, creator, AsyncMock(side_effect=_conflict))
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await fn(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = report.category(etype)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert len(ledger.entries) == 0
    assert remap.resolve(etype, 5) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, etype, creator, existing_kw", _CATEGORIES)
async def test_create_upstream_error_recorded_as_failure(fn, etype, creator, existing_kw):
    """A non-conflict create error is failed UPSTREAM_API_ERROR."""
    client = _client()

    async def _boom(*args, **kwargs):
        raise RuntimeError("502 bad gateway")

    setattr(client, creator, AsyncMock(side_effect=_boom))
    report = _report()

    await fn(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    cat = report.category(etype)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.UPSTREAM_API_ERROR


# ---------------------------------------------------------------------------
# FK remap + DEPENDENCY_UNRESOLVED (generic mechanism)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fk_remap_rewrites_resolvable_reference():
    """When a category config declares a remappable FK and the reference resolves
    through the IdRemapTable, the create payload carries the DEST id, not the
    stale archive id."""
    captured = {}

    async def _create(data):
        captured.update(data)
        return {"id": 201, **data}

    client = _client()
    client.create_channel_profile = AsyncMock(side_effect=_create)

    # Synthetic config: a CHANNEL_PROFILE create that remaps a stream_profile_id
    # FK through STREAM_PROFILE. Proves the generic FK-remap path end to end.
    config = CategoryConfig(
        entity_type=EntityType.CHANNEL_PROFILE,
        getter="get_channel_profiles",
        creator="create_channel_profile",
        log_prefix="DBAS-TEST",
        remappable_fk_fields={"stream_profile_id": EntityType.STREAM_PROFILE},
    )
    remap = _remap()
    remap.add(EntityType.STREAM_PROFILE, 50, 950)  # archived 50 -> dest 950

    from dbas.importers.groups_profiles import _import_category

    await _import_category(
        config=config,
        archive_rows=[{"id": 5, "name": "LiveTV", "stream_profile_id": 50}],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=remap,
    )

    assert captured["stream_profile_id"] == 950  # remapped, not 50


@pytest.mark.asyncio
async def test_fk_unresolved_skipped_dependency_unresolved():
    """When a remappable FK reference cannot be resolved through the IdRemapTable,
    the row is skipped DEPENDENCY_UNRESOLVED rather than created with a stale id."""
    client = _client()
    config = CategoryConfig(
        entity_type=EntityType.CHANNEL_PROFILE,
        getter="get_channel_profiles",
        creator="create_channel_profile",
        log_prefix="DBAS-TEST",
        remappable_fk_fields={"stream_profile_id": EntityType.STREAM_PROFILE},
    )
    report = _report()
    remap = _remap()  # no mapping for the FK

    from dbas.importers.groups_profiles import _import_category

    await _import_category(
        config=config,
        archive_rows=[{"id": 5, "name": "LiveTV", "stream_profile_id": 50}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=remap,
    )

    client.create_channel_profile.assert_not_called()
    cat = report.category(EntityType.CHANNEL_PROFILE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED
    assert remap.resolve(EntityType.CHANNEL_PROFILE, 5) is None


# ---------------------------------------------------------------------------
# Bulk entry point — all three categories in dependency order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_groups_profiles_runs_all_three_categories():
    """The bulk entry point restores all three categories and populates the
    IdRemapTable for each EntityType the Channels importer consumes."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_groups_profiles(
        archive={
            "channel_groups": [{"id": 1, "name": "Sports"}],
            "channel_profiles": [{"id": 2, "name": "LiveTV"}],
            "stream_profiles": [{"id": 3, "name": "ffmpeg"}],
        },
        client=client,
        selected={
            "channel_groups": True,
            "channel_profiles": True,
            "stream_profiles": True,
        },
        report=report,
        ledger=ledger,
        remap=remap,
    )

    assert remap.resolve(EntityType.CHANNEL_GROUP, 1) is not None
    assert remap.resolve(EntityType.CHANNEL_PROFILE, 2) is not None
    assert remap.resolve(EntityType.STREAM_PROFILE, 3) is not None
    assert len(ledger.entries) == 3
    # Each category reported one create.
    assert report.category(EntityType.CHANNEL_GROUP).created == 1
    assert report.category(EntityType.CHANNEL_PROFILE).created == 1
    assert report.category(EntityType.STREAM_PROFILE).created == 1


@pytest.mark.asyncio
async def test_import_groups_profiles_respects_per_category_selection():
    """The bulk entry point honours per-category opt-in: an unselected category is
    EXCLUDED_BY_OPERATOR while selected ones are restored."""
    client = _client()
    report = _report()
    remap = _remap()

    await import_groups_profiles(
        archive={
            "channel_groups": [{"id": 1, "name": "Sports"}],
            "channel_profiles": [{"id": 2, "name": "LiveTV"}],
            "stream_profiles": [{"id": 3, "name": "ffmpeg"}],
        },
        client=client,
        selected={
            "channel_groups": True,
            "channel_profiles": False,  # opted out
            "stream_profiles": True,
        },
        report=report,
        ledger=_ledger(),
        remap=remap,
    )

    client.create_channel_profile.assert_not_called()
    assert report.category(EntityType.CHANNEL_PROFILE).skipped == 1
    assert (
        report.category(EntityType.CHANNEL_PROFILE).skip_details[0].reason
        == SkipReason.EXCLUDED_BY_OPERATOR
    )
    assert remap.resolve(EntityType.CHANNEL_GROUP, 1) is not None
    assert remap.resolve(EntityType.STREAM_PROFILE, 3) is not None


@pytest.mark.asyncio
async def test_import_groups_profiles_defaults_selection_to_off():
    """When ``selected`` omits a category, it defaults OFF (opt-in) — nothing is
    created for the omitted category."""
    client = _client()
    report = _report()

    await import_groups_profiles(
        archive={"channel_groups": [{"id": 1, "name": "Sports"}]},
        client=client,
        selected={},  # nothing selected
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    client.create_channel_group.assert_not_called()
    assert report.category(EntityType.CHANNEL_GROUP).skipped == 1


# ---------------------------------------------------------------------------
# Config integrity
# ---------------------------------------------------------------------------


def test_category_configs_cover_the_three_entity_types():
    """The module's canonical config table covers exactly the three leaf
    categories, each with NO outbound remappable FK (they are leaf dependencies
    that channels point at — the FK direction is owned by the Channels importer)."""
    etypes = {c.entity_type for c in _CATEGORY_CONFIGS.values()}
    assert etypes == {
        EntityType.CHANNEL_GROUP,
        EntityType.CHANNEL_PROFILE,
        EntityType.STREAM_PROFILE,
    }
    for config in _CATEGORY_CONFIGS.values():
        assert config.remappable_fk_fields == {}
