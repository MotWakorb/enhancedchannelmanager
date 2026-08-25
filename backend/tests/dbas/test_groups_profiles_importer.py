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
  with the category's name-match reason (ALREADY_EXISTS_NAME_MATCH for channel
  groups, ALREADY_EXISTS_IDENTICAL for the two profile categories — bead
  …-3t74w) and its source id is remapped to the EXISTING dest id (so a later FK
  reference resolves) — never blind delete-all.
* Opt-in off -> no-op — nothing is created; every row is EXCLUDED_BY_OPERATOR.
* Dry-run -> zero creates + zero ledger; reports would_create.
* CONFLICT vs UPSTREAM_API_ERROR — a create that races into a uniqueness
  conflict is failed CONFLICT; a non-conflict error is UPSTREAM_API_ERROR.

FK remap + DEPENDENCY_UNRESOLVED: channel groups and channel profiles are leaf
dependencies (no outbound FK to another remapped entity — channels point at
THEM, not the reverse). STREAM PROFILES ARE NOT: a Dispatcharr stream profile
carries a ``user_agent`` FK (bead ``enhancedchannelmanager-lvfwd``), so its
config declares that remap and the tests below pin it. The generic FK-remap path
is additionally exercised via a synthetic category config, proving an
unresolvable FK is skipped DEPENDENCY_UNRESOLVED rather than created with a
stale archive id.

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
# What a NAME match reports, per category (bead …-3t74w). Channel groups adopt on
# a name and compare NOTHING else — their contents live on the channels, restored
# later — so they say ``already_exists_name_match``. The two profile categories
# keep ``already_exists_identical``. Read off the importer's own config table so
# this suite cannot drift from the shipped behaviour.
def _name_match_reason(entity_type):
    for config in _CATEGORY_CONFIGS.values():
        if config.entity_type == entity_type:
            return config.name_match_skip_reason
    raise AssertionError("no config for %r" % entity_type)


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
    (case-insensitive / trimmed) is SKIPPED and its source id is remapped to the
    EXISTING dest id (so a later FK resolves). No create, no ledger entry — never
    blind delete-all.

    The skip REASON is per-category (bead …-3t74w): channel groups report
    ``ALREADY_EXISTS_NAME_MATCH`` because nothing beyond the name was compared;
    channel profiles and stream profiles keep ``ALREADY_EXISTS_IDENTICAL``."""
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
    assert cat.skip_details[0].reason == _name_match_reason(etype)
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
    assert cat.skip_details[0].reason == _name_match_reason(etype)


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
async def test_channel_group_create_race_adopts_ingested_groups_and_follow_up_is_idempotent():
    """A destination ingest can create groups after the importer's first list.

    The observed Dispatcharr 0.29.0 uniqueness response must trigger a re-list,
    normalized-name adoption, and remap population. A complete follow-up import
    then adopts the same rows from its initial list without another create.
    """
    ingested_groups = [
        {"id": 701, "name": "NORTHWIND LOCAL"},
        {"id": 702, "name": " Northwind Regional "},
    ]
    destination_groups = []
    client = _client()
    client.get_channel_groups = AsyncMock(
        side_effect=lambda: [dict(row) for row in destination_groups]
    )

    async def _create_races_with_ingest(name):
        destination_groups.extend(ingested_groups)
        raise RuntimeError(
            'Channel group creation failed: 400 - '
            '{"name":["channel group with this name already exists."]}'
        )

    client.create_channel_group = AsyncMock(side_effect=_create_races_with_ingest)
    archive_rows = [
        {"id": 11, "name": "Northwind Local"},
        {"id": 12, "name": "Northwind Regional"},
    ]

    first_report = _report()
    first_remap = _remap()
    await import_channel_groups(
        archive_rows=archive_rows,
        client=client,
        selected=True,
        report=first_report,
        ledger=_ledger(),
        remap=first_remap,
    )

    first = first_report.category(EntityType.CHANNEL_GROUP)
    assert first.failed == 0
    assert first.created == 0
    assert first.skipped == 2
    assert first_remap.resolve(EntityType.CHANNEL_GROUP, 11) == 701
    assert first_remap.resolve(EntityType.CHANNEL_GROUP, 12) == 702
    client.create_channel_group.assert_awaited_once_with("Northwind Local")

    follow_up_report = _report()
    follow_up_remap = _remap()
    await import_channel_groups(
        archive_rows=archive_rows,
        client=client,
        selected=True,
        report=follow_up_report,
        ledger=_ledger(),
        remap=follow_up_remap,
    )

    follow_up = follow_up_report.category(EntityType.CHANNEL_GROUP)
    assert follow_up.failed == 0
    assert follow_up.created == 0
    assert follow_up.skipped == 2
    assert follow_up_remap.resolve(EntityType.CHANNEL_GROUP, 11) == 701
    assert follow_up_remap.resolve(EntityType.CHANNEL_GROUP, 12) == 702
    assert client.create_channel_group.await_count == 1


@pytest.mark.asyncio
async def test_channel_group_create_race_remains_fatal_when_relist_cannot_find_group():
    """The uniqueness response is not a non-fatal result without a row to adopt."""
    client = _client()
    client.create_channel_group = AsyncMock(
        side_effect=RuntimeError(
            'Channel group creation failed: 400 - '
            '{"name":["channel group with this name already exists."]}'
        )
    )
    report = _report()
    remap = _remap()

    await import_channel_groups(
        archive_rows=[{"id": 5, "name": "Sports"}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=remap,
    )

    cat = report.category(EntityType.CHANNEL_GROUP)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert remap.resolve(EntityType.CHANNEL_GROUP, 5) is None


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


def test_category_configs_cover_the_four_entity_types():
    """The module's canonical config table covers exactly the four categories.

    Channel groups, channel profiles and SERVER GROUPS (bead ``…-tyrg1``) are
    genuine leaf dependencies with NO outbound remappable FK — a Dispatcharr
    ServerGroup is a unique ``name`` and nothing else (0.29.0
    ``apps/m3u/models.py:216``). STREAM PROFILES ARE NOT (bead
    ``enhancedchannelmanager-lvfwd``): a Dispatcharr stream profile carries a
    ``user_agent`` FK, so its config MUST declare that remap.
    """
    etypes = {c.entity_type for c in _CATEGORY_CONFIGS.values()}
    assert etypes == {
        EntityType.CHANNEL_GROUP,
        EntityType.CHANNEL_PROFILE,
        EntityType.SERVER_GROUP,
        EntityType.STREAM_PROFILE,
    }
    assert _CATEGORY_CONFIGS["channel_groups"].remappable_fk_fields == {}
    assert _CATEGORY_CONFIGS["channel_profiles"].remappable_fk_fields == {}
    assert _CATEGORY_CONFIGS["server_groups"].remappable_fk_fields == {}
    assert _CATEGORY_CONFIGS["stream_profiles"].remappable_fk_fields == {
        "user_agent": EntityType.USER_AGENT
    }


# ---------------------------------------------------------------------------
# lvfwd — the stream-profile -> user-agent FK (the SILENT WRONG-BINDING defect)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_profile_user_agent_fk_is_remapped_not_passed_through():
    """A stream profile's ``user_agent`` id is rewritten through the remap.

    Bead ``enhancedchannelmanager-lvfwd`` defect 2. The archived SOURCE id must
    never reach the destination: a restore reassigns ids, so posting the source
    id either 400s (id absent) or — far worse — SUCCEEDS and silently binds a
    completely unrelated user agent that happens to occupy that id on the target.
    """
    client = _client()
    remap = _remap()
    remap.add(EntityType.USER_AGENT, 4, 77)  # archived UA 4 -> dest 77
    report = _report()

    await import_stream_profiles(
        archive_rows=[{"id": 9, "name": "Drill Profile", "user_agent": 4}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=remap,
    )

    payload = client.create_stream_profile.await_args.args[0]
    assert payload["user_agent"] == 77, "source user_agent id was passed through raw"
    assert report.category(EntityType.STREAM_PROFILE).created == 1


@pytest.mark.asyncio
async def test_stream_profile_with_unresolvable_user_agent_is_skipped():
    """An unresolvable ``user_agent`` skips the profile DEPENDENCY_UNRESOLVED.

    Never create it with a stale archive id — that is the exact silent
    wrong-binding path bead ``…-lvfwd`` found.
    """
    client = _client()
    report = _report()

    await import_stream_profiles(
        archive_rows=[{"id": 9, "name": "Drill Profile", "user_agent": 4}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),  # empty — nothing to resolve 4 against
    )

    client.create_stream_profile.assert_not_called()
    cat = report.category(EntityType.STREAM_PROFILE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED


@pytest.mark.asyncio
async def test_locked_builtin_stream_profile_is_skipped_never_updated():
    """A ``locked`` built-in profile is skipped + remapped — NEVER updated.

    Verified against the Dispatcharr 0.28.2 image source. ``StreamProfile.save()``
    (``/app/core/models.py`` lines 78-101) refuses any change to a locked profile:
    it iterates ``self._meta.fields`` comparing ``field.name`` against
    ``allowed_fields = {"user_agent_id"}``, and a ForeignKey's ``field.name`` is
    ``"user_agent"`` — so even the one field the comment says is permitted
    raises ``Cannot modify user_agent on a protected profile.`` over the REST
    API (``StreamProfileViewSet`` is a plain ModelViewSet, so a PATCH lands in
    ``save()``, not the ``update()`` classmethod).

    This importer is create-or-skip by construction — it reaches exactly two
    client methods, ``config.getter`` and ``config.creator`` — so the locked
    guard is unreachable today. This test pins that: adding an update path here
    would have to reckon with ``locked`` first. Dispatcharr ships ``ffmpeg``,
    ``Proxy``, ``Redirect`` and ``streamlink`` locked, so this is the common case
    on every restore, not a corner case.
    """
    client = _client(
        stream_profiles=[
            {"id": 1, "name": "ffmpeg", "locked": True, "user_agent": 2},
        ]
    )
    remap = _remap()
    remap.add(EntityType.USER_AGENT, 4, 77)
    report = _report()

    await import_stream_profiles(
        # The archive's copy carries a DIFFERENT user agent than the target's.
        archive_rows=[{"id": 9, "name": "ffmpeg", "locked": True, "user_agent": 4}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=remap,
    )

    cat = report.category(EntityType.STREAM_PROFILE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert cat.updated == 0
    client.create_stream_profile.assert_not_called()
    # The ONLY upstream call was the list read — no create, no update, no patch.
    assert {name for name, _, _ in client.mock_calls} == {"get_stream_profiles"}
    # The source id still resolves, to the EXISTING destination row.
    assert remap.resolve(EntityType.STREAM_PROFILE, 9) == 1


@pytest.mark.asyncio
async def test_stream_profile_without_user_agent_still_creates():
    """A profile carrying no ``user_agent`` (or an explicit null) is unaffected by
    the FK remap — the built-in Dispatcharr profiles look like this."""
    client = _client()
    report = _report()

    await import_stream_profiles(
        archive_rows=[
            {"id": 9, "name": "Direct"},
            {"id": 10, "name": "Proxy", "user_agent": None},
        ],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert report.category(EntityType.STREAM_PROFILE).created == 2
