"""Tests for the M3U accounts restore importer
(enhancedchannelmanager-0i2vt.10 — Phase 2 FIRST entity).

Scope under test:

1. Create M3U accounts. For each archived account, strip archive-only /
   non-writable keys (id/pk, embedded channel_groups, read-only timestamps) and
   create via ``client.create_m3u_account``. Register source->dest in the
   IdRemapTable under EntityType.M3U_ACCOUNT and record each create in the
   RollbackLedger.

2. 4-way group matching (pure helper ``resolve_group``). When reconciling an
   archived account's associated group(s) against destination groups, match by
   FOUR strategies in priority order:
     (a) by ID (through the IdRemapTable CHANNEL_GROUP namespace),
     (b) by name (case-insensitive, trimmed),
     (c) by URL,
     (d) by export-key.
   First strategy that hits wins; deterministic tie-break = lowest dest id.

3. Deferred auto-sync (CRITICAL ordering). The importer does NOT trigger the
   account's upstream auto-sync/refresh at import time. It EXTRACTS the
   auto-sync settings and RETURNS them as
   ``deferred_auto_sync_settings: list[{m3u_account_id, settings}]`` (using the
   destination/remapped account id) so the orchestrator (.14/.18) applies them
   AFTER Channels + Logos finish — protecting logo import from an auto-sync race.

4. Deferred-apply helper ``apply_deferred_auto_sync``. The status-endpoint poll +
   stream-count-stable heuristic + is_active toggle workaround that the
   orchestrator calls during the deferred phase (NOT at import). Mock-tested:
   the poll terminates when the stream count stabilizes; the is_active toggle is
   invoked. The live-polling loop itself is flagged as a deferred follow-up.

5. Opt-in: nothing happens unless the operator selected the category.

6. Dry-run: no creates, no ledger entries; reports would_create.

7. Collision taxonomy: an existing identical account (by name) is skipped
   ALREADY_EXISTS_IDENTICAL; a create that races into a conflict is failed
   CONFLICT.

8. NO credential leakage: report/ledger/log carry only safe fields (name, id,
   counts, status). server_url / username / password never surface.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.m3u_accounts``); the importer is exercised with an AsyncMock
client.
"""
import pytest
from unittest.mock import AsyncMock

from dbas.importers.m3u_accounts import (
    apply_deferred_auto_sync,
    import_m3u_accounts,
    resolve_group,
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


def _client(*, existing_accounts=None, create_side_effect=None, dest_groups=None):
    """Build an AsyncMock Dispatcharr client with the methods the importer uses."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=existing_accounts or [])
    client.get_channel_groups = AsyncMock(return_value=dest_groups or [])
    created_counter = {"n": 900}

    async def _default_create(payload):
        created_counter["n"] += 1
        return {"id": created_counter["n"], **payload}

    client.create_m3u_account = AsyncMock(side_effect=create_side_effect or _default_create)
    client.delete_m3u_account = AsyncMock(return_value=None)
    client.patch_m3u_account = AsyncMock(return_value={"success": True})
    client.refresh_m3u_account = AsyncMock(return_value={"success": True})
    client.refresh_all_m3u_accounts = AsyncMock(return_value={"success": True})
    return client


def _report(is_dry_run=False):
    return RestoreReport(is_dry_run=is_dry_run)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap(**kwargs):
    """Build an IdRemapTable pre-seeded with the given mappings.

    e.g. ``_remap(channel_group={10: 110}, m3u_account={1: 901})``.
    """
    table = IdRemapTable()
    name_to_type = {
        "channel_group": EntityType.CHANNEL_GROUP,
        "m3u_account": EntityType.M3U_ACCOUNT,
    }
    for name, mapping in kwargs.items():
        for src, dest in mapping.items():
            table.add(name_to_type[name], src, dest)
    return table


# ---------------------------------------------------------------------------
# 4-way group resolver (pure helper) — one test per strategy + priority + miss
# ---------------------------------------------------------------------------


def _group(id, name=None, url=None, export_key=None):
    g = {"id": id}
    if name is not None:
        g["name"] = name
    if url is not None:
        g["url"] = url
    if export_key is not None:
        g["export_key"] = export_key
    return g


def test_resolve_group_strategy_a_by_id():
    """Strategy (a): an archived group whose source id is in the IdRemapTable
    CHANNEL_GROUP namespace resolves to the mapped destination id — highest
    priority, taken even if name/url would point elsewhere."""
    archive_group = _group(10, name="Sports", url="http://x/sports", export_key="k-sports")
    dest_groups = [_group(110, name="Different", url="http://x/different", export_key="k-other")]
    remap = _remap(channel_group={10: 110})

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_strategy_b_by_name():
    """Strategy (b): when ID does not resolve, a case-insensitive / trimmed name
    match wins."""
    archive_group = _group(10, name="  Sports  ", url="http://new/sports")
    dest_groups = [_group(110, name="sports", url="http://old/sports")]
    remap = _remap()  # no id mapping

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_strategy_c_by_url():
    """Strategy (c): when ID and name both miss, a URL match wins."""
    archive_group = _group(10, name="Renamed Sports", url="http://x/sports")
    dest_groups = [_group(110, name="Sports Channel", url="http://x/sports")]
    remap = _remap()

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_strategy_d_by_export_key():
    """Strategy (d): the last resort — an export-key match wins when ID, name and
    URL all miss."""
    archive_group = _group(10, name="Renamed", url="http://new/url", export_key="k-sports")
    dest_groups = [_group(110, name="Sports", url="http://old/url", export_key="k-sports")]
    remap = _remap()

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_priority_id_beats_name():
    """Priority: when BOTH an id mapping and a (different) name match exist, the
    id mapping (strategy a) wins over the name match (strategy b)."""
    archive_group = _group(10, name="Sports")
    dest_groups = [
        _group(110, name="unrelated"),   # the id-mapped destination
        _group(220, name="Sports"),      # a name match to a DIFFERENT group
    ]
    remap = _remap(channel_group={10: 110})

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_priority_name_beats_url():
    """Priority: a name match (strategy b) wins over a URL match (strategy c) to a
    different destination group."""
    archive_group = _group(10, name="Sports", url="http://x/sports")
    dest_groups = [
        _group(330, name="other", url="http://x/sports"),  # url match
        _group(110, name="sports", url="http://x/other"),  # name match
    ]
    remap = _remap()

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_priority_url_beats_export_key():
    """Priority: a URL match (strategy c) wins over an export-key match (d)."""
    archive_group = _group(10, name="X", url="http://x/sports", export_key="k1")
    dest_groups = [
        _group(440, name="Y", url="http://other", export_key="k1"),       # export-key match
        _group(110, name="Z", url="http://x/sports", export_key="k2"),    # url match
    ]
    remap = _remap()

    assert resolve_group(archive_group, dest_groups, remap) == 110


def test_resolve_group_miss_returns_none():
    """Fall-through: no strategy hits — resolver returns None (caller treats as
    unresolved, never guesses a destination id)."""
    archive_group = _group(10, name="Sports", url="http://x/sports", export_key="k1")
    dest_groups = [_group(110, name="News", url="http://x/news", export_key="k2")]
    remap = _remap()

    assert resolve_group(archive_group, dest_groups, remap) is None


def test_resolve_group_tie_break_lowest_dest_id():
    """Deterministic tie-break: when several destination groups match within the
    winning strategy, the lowest destination id wins (order-independent)."""
    archive_group = _group(10, name="Sports")
    dest_groups = [
        _group(330, name="Sports"),
        _group(110, name="Sports"),
        _group(220, name="Sports"),
    ]
    remap = _remap()

    assert resolve_group(archive_group, dest_groups, remap) == 110


# ---------------------------------------------------------------------------
# Opt-in gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_skipped_when_not_selected():
    """OPT-IN: when the operator did not select the category, nothing is created —
    every archived account is recorded EXCLUDED_BY_OPERATOR and no auto-sync is
    deferred."""
    client = _client()
    report = _report()
    ledger = _ledger()
    remap = _remap()

    result = await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a"}],
        client=client,
        selected=False,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_m3u_account.assert_not_called()
    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR
    assert result.deferred_auto_sync_settings == []


# ---------------------------------------------------------------------------
# Account create — happy path (remap + ledger)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_account_happy_path_remap_and_ledger():
    """An archived account is created; source->dest is registered in the
    IdRemapTable (M3U_ACCOUNT) and the create is recorded in the RollbackLedger."""
    client = _client(
        create_side_effect=lambda payload: {"id": 901, **payload},
    )
    # AsyncMock side_effect must be async; wrap.
    async def _create(payload):
        return {"id": 901, **payload}
    client.create_m3u_account = AsyncMock(side_effect=_create)

    report = _report()
    ledger = _ledger()
    remap = _remap()

    result = await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.created == 1
    assert remap.resolve(EntityType.M3U_ACCOUNT, 5) == 901
    assert len(ledger.entries) == 1
    assert ledger.entries[0].entity_type == EntityType.M3U_ACCOUNT
    assert ledger.entries[0].destination_id == 901
    assert ledger.entries[0].label == "Provider A"
    assert result is not None


@pytest.mark.asyncio
async def test_create_payload_strips_source_id_and_embedded_groups():
    """The create payload drops the archive source id and the embedded
    channel_groups list (those are reconciled separately, never sent on create)."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()
    ledger = _ledger()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://p/a",
            "channel_groups": [{"channel_group": 10, "auto_channel_sync": True}],
            "created_at": "2020-01-01",
            "updated_at": "2020-02-02",
        }],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    assert "id" not in captured
    assert "channel_groups" not in captured
    assert "created_at" not in captured
    assert "updated_at" not in captured
    assert captured["name"] == "Provider A"


# ---------------------------------------------------------------------------
# Deferred auto-sync — extraction + NO trigger at import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deferred_auto_sync_settings_extracted_and_returned():
    """The importer extracts the account's auto-sync settings and returns them in
    the documented shape, keyed by the DESTINATION (remapped) account id."""
    async def _create(payload):
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()
    ledger = _ledger()

    result = await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://p/a",
            "is_active": True,
            "refresh_interval": 12,
            "channel_groups": [
                {"channel_group": 10, "auto_channel_sync": True, "enabled": True},
                {"channel_group": 11, "auto_channel_sync": False, "enabled": True},
            ],
        }],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    assert isinstance(result.deferred_auto_sync_settings, list)
    assert len(result.deferred_auto_sync_settings) == 1
    entry = result.deferred_auto_sync_settings[0]
    assert entry["m3u_account_id"] == 901  # destination id, not source 5
    settings = entry["settings"]
    # The extracted settings carry enough to re-apply the auto-sync later.
    assert settings["channel_groups"] == [
        {"channel_group": 10, "auto_channel_sync": True, "enabled": True},
        {"channel_group": 11, "auto_channel_sync": False, "enabled": True},
    ]
    assert settings["refresh_interval"] == 12


@pytest.mark.asyncio
async def test_no_auto_sync_or_refresh_triggered_at_import():
    """CRITICAL: the importer NEVER triggers an upstream auto-sync/refresh during
    import — the refresh/sync client calls are not made."""
    async def _create(payload):
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://p/a",
            "channel_groups": [{"channel_group": 10, "auto_channel_sync": True}],
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )

    client.refresh_m3u_account.assert_not_called()
    client.refresh_all_m3u_accounts.assert_not_called()
    client.patch_m3u_account.assert_not_called()


@pytest.mark.asyncio
async def test_no_deferred_settings_when_account_has_no_auto_sync():
    """An account with no auto_channel_sync-enabled groups contributes no deferred
    entry (nothing to apply later)."""
    async def _create(payload):
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)

    result = await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://p/a",
            "channel_groups": [{"channel_group": 10, "auto_channel_sync": False}],
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )

    assert result.deferred_auto_sync_settings == []


# ---------------------------------------------------------------------------
# Deferred-apply helper — mock-tested poll + is_active toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_deferred_auto_sync_polls_until_stream_count_stable():
    """The deferred-apply helper polls the stream count and terminates when it
    stabilizes across consecutive polls (stream-count-stable heuristic)."""
    # Stream-count sequence: grows then stabilizes at 100.
    counts = iter([10, 40, 100, 100])

    async def _stream_count(account_id):
        return next(counts)

    client = _client()
    client.refresh_m3u_account = AsyncMock(return_value={"success": True})
    sleeps = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    final = await apply_deferred_auto_sync(
        deferred=[{"m3u_account_id": 901, "settings": {"channel_groups": [
            {"channel_group": 110, "auto_channel_sync": True, "enabled": True}
        ]}}],
        client=client,
        stream_count_fn=_stream_count,
        sleep_fn=_sleep,
        max_polls=10,
        stable_polls_required=2,
    )

    # Refresh was triggered during the DEFERRED phase (this is allowed here).
    client.refresh_m3u_account.assert_awaited()
    # Group-settings (auto-sync) were applied for the destination account.
    client.update_m3u_group_settings.assert_awaited()
    # Poll loop terminated on stabilization, not on max_polls exhaustion.
    assert final[0]["stream_count"] == 100
    assert final[0]["stabilized"] is True


@pytest.mark.asyncio
async def test_apply_deferred_auto_sync_toggles_is_active_workaround():
    """The is_active toggle workaround is invoked (PATCH is_active False->True) to
    coax a newly-imported M3U into fetching streams."""
    async def _stream_count(account_id):
        return 50

    client = _client()
    patches = []

    async def _patch(account_id, data):
        patches.append((account_id, data))
        return {"id": account_id, **data}

    client.patch_m3u_account = AsyncMock(side_effect=_patch)

    async def _sleep(seconds):
        pass

    await apply_deferred_auto_sync(
        deferred=[{"m3u_account_id": 901, "settings": {"channel_groups": []}}],
        client=client,
        stream_count_fn=_stream_count,
        sleep_fn=_sleep,
        max_polls=2,
        stable_polls_required=2,
    )

    # is_active toggled off then on for the destination account.
    toggled = [d for (acc, d) in patches if acc == 901 and "is_active" in d]
    assert {"is_active": False} in toggled
    assert {"is_active": True} in toggled


@pytest.mark.asyncio
async def test_apply_deferred_auto_sync_terminates_at_max_polls():
    """If the stream count never stabilizes, the poll loop terminates at max_polls
    (bounded — never an infinite loop)."""
    forever = iter(range(0, 1000, 7))

    async def _stream_count(account_id):
        return next(forever)

    async def _sleep(seconds):
        pass

    client = _client()

    final = await apply_deferred_auto_sync(
        deferred=[{"m3u_account_id": 901, "settings": {"channel_groups": []}}],
        client=client,
        stream_count_fn=_stream_count,
        sleep_fn=_sleep,
        max_polls=3,
        stable_polls_required=2,
    )

    assert final[0]["stabilized"] is False
    assert final[0]["polls"] == 3


# ---------------------------------------------------------------------------
# Collision taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_account_by_name_skipped_already_exists():
    """An archived account whose name already exists on the destination is skipped
    ALREADY_EXISTS_IDENTICAL; its source id is still remapped to the existing
    destination id so a later FK reference resolves."""
    client = _client(existing_accounts=[{"id": 700, "name": "Provider A"}])
    report = _report()
    ledger = _ledger()
    remap = _remap()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    client.create_m3u_account.assert_not_called()
    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert remap.resolve(EntityType.M3U_ACCOUNT, 5) == 700
    assert len(ledger.entries) == 0


@pytest.mark.asyncio
async def test_create_conflict_recorded_as_failure_conflict():
    """A create that races into an upstream uniqueness conflict is failed
    CONFLICT (not skipped)."""
    async def _conflict(payload):
        raise RuntimeError("name already exists")

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_conflict)
    report = _report()
    ledger = _ledger()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert len(ledger.entries) == 0


@pytest.mark.asyncio
async def test_create_upstream_error_recorded_as_failure():
    """A non-conflict create error is failed UPSTREAM_API_ERROR."""
    async def _boom(payload):
        raise RuntimeError("502 bad gateway")

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_boom)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a"}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.UPSTREAM_API_ERROR


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_no_creates_no_ledger():
    """Dry-run: no account is created, no ledger entry written; the importer
    reports would_create."""
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()
    remap = _remap()

    result = await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a",
                           "channel_groups": [{"channel_group": 10, "auto_channel_sync": True}]}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=True,
    )

    client.create_m3u_account.assert_not_called()
    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.would_create == 1
    assert cat.created == 0
    assert len(ledger.entries) == 0
    # No deferred settings produced on a dry-run (nothing was created).
    assert result.deferred_auto_sync_settings == []


# ---------------------------------------------------------------------------
# Credential / secret hygiene (the bead .8 clear-text-logging lesson)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_and_deferred_carry_no_credential_material():
    """No server_url / username / password / api credentials surface in the
    report (labels, notes), the ledger, or the deferred return shape."""
    async def _create(payload):
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()
    ledger = _ledger()

    secret_markers = ("http://secret-provider", "s3cr3t-pass", "secret-user")

    result = await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://secret-provider/playlist.m3u",
            "username": "secret-user",
            "password": "s3cr3t-pass",
            "channel_groups": [{"channel_group": 10, "auto_channel_sync": True}],
        }],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(),
    )

    blob = report.model_dump_json() + ledger.model_dump_json() + repr(result.deferred_auto_sync_settings)
    for marker in secret_markers:
        assert marker not in blob
