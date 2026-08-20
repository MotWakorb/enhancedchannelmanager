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
import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from credential_sentinel import REDACTION_SENTINEL
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
        "user_agent": EntityType.USER_AGENT,
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
async def test_group_selection_is_deferred_even_without_auto_sync():
    """An account with NO auto_channel_sync group still defers its group settings.

    CORRECTED PREMISE (bead ``enhancedchannelmanager-2o0cz``). This test used to
    assert the opposite — that an account with no ``auto_channel_sync`` group
    contributes no deferred entry — and that assertion was the defect. The drill's
    source account had ONE of 375 groups merely ENABLED and no auto-sync anywhere,
    so nothing was deferred, the restored account came back at ``0 / 375`` groups
    in PENDING SETUP, and its refresh ingested nothing while reporting ``No
    streams returned from Xtream Codes provider``. The enabled-group SELECTION is
    the load-bearing setting; auto-sync is an optional extra on top of it.
    """
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

    groups = result.deferred_auto_sync_settings[0]["settings"]["channel_groups"]
    assert groups == [
        {"channel_group": 10, "auto_channel_sync": False, "enabled": True}
    ]


@pytest.mark.asyncio
async def test_no_deferred_settings_when_account_has_no_groups():
    """An account carrying no ``channel_groups`` at all defers nothing.

    There is no selection to restore, so there is nothing for the deferred phase
    to apply — the genuine "nothing to do" case the assertion above used to be
    mistaken for.
    """
    async def _create(payload):
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)

    result = await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_url": "http://p/a"}],
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

    # The deferred group settings carry SOURCE group pks; the deferred phase is
    # where they are rewritten to DESTINATION pks (bead …-2o0cz), so the apply
    # needs the remap that the channel-groups importer populated earlier.
    remap = _remap()
    remap.add(EntityType.CHANNEL_GROUP, 110, 210)

    final = await apply_deferred_auto_sync(
        deferred=[{"m3u_account_id": 901, "settings": {"channel_groups": [
            {"channel_group": 110, "auto_channel_sync": True, "enabled": True}
        ]}}],
        client=client,
        remap=remap,
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


# ---------------------------------------------------------------------------
# Redaction-sentinel handling on restore (bead …-6pilh)
# ---------------------------------------------------------------------------
#
# A STANDARD (non-encrypted, redact-by-default) artifact carries the literal
# ``***REDACTED***`` in every credential-class field. Restoring that value
# verbatim produced an XC account that LOOKED fully configured (populated
# password field, every truthiness probe True) and could not authenticate — zero
# streams, and a before/after credential-presence diff that reported the dead
# account as byte-identical. The importer must therefore leave the credential
# UNSET and TELL the operator which accounts need it re-entered.


@pytest.mark.asyncio
async def test_redacted_password_is_never_written_to_the_destination():
    """THE regression: the sentinel must not reach create_m3u_account."""
    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Infinity",
            "account_type": "XC",
            "username": "mot2",
            "password": REDACTION_SENTINEL,
        }],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        remap=_remap(),
    )

    payload = captured["payload"]
    assert payload.get("password") != REDACTION_SENTINEL
    # Left UNSET (absent), not set to a placeholder — a blank field is visibly
    # incomplete in the Dispatcharr UI and reads as absent to every check.
    assert "password" not in payload
    # Non-credential fields are untouched.
    assert payload["name"] == "Infinity"
    assert payload["username"] == "mot2"


@pytest.mark.asyncio
async def test_redacted_credential_is_a_counted_post_restore_action_item():
    client = _client()
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Infinity",
            "account_type": "XC",
            "username": "mot2",
            "password": REDACTION_SENTINEL,
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert report.credentials_needing_reentry == 1
    assert len(report.credential_reentry_details) == 1
    detail = report.credential_reentry_details[0]
    assert detail.entity_type == EntityType.M3U_ACCOUNT
    assert detail.label == "Infinity"
    assert detail.fields == ["password"]
    assert detail.source_export_id == 5
    assert detail.destination_id == 901


@pytest.mark.asyncio
async def test_credential_reentry_detail_carries_no_secret_material():
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Infinity",
            "server_url": "http://secret-provider/playlist.m3u",
            "username": "secret-user",
            "password": REDACTION_SENTINEL,
        }],
        client=_client(),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    blob = report.model_dump_json()
    assert "secret-provider" not in blob
    assert "secret-user" not in blob
    # The FIELD NAME is reported; the value never is.
    assert report.credential_reentry_details[0].fields == ["password"]


@pytest.mark.asyncio
async def test_credential_bearing_artifact_still_restores_the_real_password():
    """The encrypted + include_credentials path is unchanged — it works today."""
    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Infinity",
            "account_type": "XC",
            "username": "mot2",
            "password": "63832936",
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert captured["payload"]["password"] == "63832936"
    assert report.credentials_needing_reentry == 0
    assert report.credential_reentry_details == []


@pytest.mark.asyncio
async def test_dry_run_preview_names_the_accounts_that_will_need_credentials():
    """The preview was byte-identical between the encrypted and redacted
    artifacts; the operator had no way to tell which variant they were about to
    apply. The dry-run now counts the same action item, with no destination id
    (nothing was created)."""
    client = _client()
    report = _report(is_dry_run=True)

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Infinity",
            "username": "mot2",
            "password": REDACTION_SENTINEL,
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
        is_dry_run=True,
    )

    client.create_m3u_account.assert_not_awaited()
    assert report.credentials_needing_reentry == 1
    assert report.credential_reentry_details[0].destination_id is None
    assert report.credential_reentry_details[0].fields == ["password"]


@pytest.mark.asyncio
async def test_an_already_existing_account_is_not_an_action_item():
    """A skipped account keeps whatever credential the destination already has;
    nothing needs re-entering."""
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Infinity",
            "password": REDACTION_SENTINEL,
        }],
        client=_client(existing_accounts=[{"id": 77, "name": "Infinity"}]),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert report.credentials_needing_reentry == 0


# ---------------------------------------------------------------------------
# The ``user_agent`` FK (bead …-9h6cv). An M3U account's ``user_agent`` is a
# FOREIGN KEY to a Dispatcharr user-agent row, whose id the destination assigns
# itself. Forwarding A's raw pk made B answer
# ``400 {"user_agent": ["Invalid pk \"4\" - object does not exist."]}``, and
# because M3U_ACCOUNT is a FATAL failure category the whole apply rolled back
# and nothing synced at all.
#
# INVARIANT under test: no source-side FK reaches the destination unresolved.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_agent_fk_is_rewritten_to_the_destination_id():
    """The account's ``user_agent`` FK is resolved through the USER_AGENT remap
    namespace; the wire payload carries B's id, never A's source pk."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://p/a",
            "user_agent": 4,
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(user_agent={4: 77}),
    )

    assert captured["user_agent"] == 77, (
        "the raw source pk was forwarded instead of the remapped destination id"
    )
    assert report.category(EntityType.M3U_ACCOUNT).created == 1


@pytest.mark.asyncio
async def test_null_user_agent_is_preserved_and_needs_no_remap():
    """The overwhelmingly common shape — no custom agent — is untouched: the
    field stays null, the account is created, and nothing is noted."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "user_agent": None}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert captured["user_agent"] is None
    assert report.category(EntityType.M3U_ACCOUNT).created == 1
    assert report.notes == []


@pytest.mark.asyncio
async def test_unresolvable_user_agent_drops_the_field_and_still_creates():
    """When the FK cannot be resolved the account is STILL created — with the
    ``user_agent`` field DROPPED, never a stale source pk.

    Deliberately different from the stream-profile sibling, which skips
    DEPENDENCY_UNRESOLVED: a stream profile is a leaf, an M3U account is the ROOT
    of the Phase-2 chain (EPG sources, groups, channels and streams all hang off
    it), and Dispatcharr falls back to its default agent when the field is unset.
    Skipping the account would cascade a whole-tree DEPENDENCY_UNRESOLVED for a
    field the account works without."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "user_agent": 4}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),  # USER_AGENT namespace empty
    )

    assert "user_agent" not in captured, (
        "an unresolvable FK must be dropped, never sent upstream as a stale pk"
    )
    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.created == 1
    assert cat.failed == 0
    assert cat.skipped == 0
    assert not any(
        d.reason == SkipReason.DEPENDENCY_UNRESOLVED for d in cat.skip_details
    )


@pytest.mark.asyncio
async def test_dropped_user_agent_is_a_visible_operator_note():
    """Dropping the field is a DEGRADATION, so it is reported — never silent."""
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "user_agent": 4}],
        client=_client(),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert any(
        "Provider A" in note and "user agent" in note.lower()
        for note in report.notes
    ), f"no operator note named the degraded account; notes={report.notes}"


@pytest.mark.asyncio
async def test_dry_run_also_reports_the_unresolvable_user_agent():
    """The PREVIEW must not promise what the apply will not deliver: the same
    note appears on a dry-run, which creates nothing."""
    report = _report(is_dry_run=True)
    client = _client()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "user_agent": 4}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
        is_dry_run=True,
    )

    client.create_m3u_account.assert_not_awaited()
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 1
    assert any("Provider A" in note for note in report.notes)


@pytest.mark.asyncio
async def test_user_agent_note_carries_no_credential_material():
    """The degradation note names the account only — never a server_url,
    username or password."""
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://provider/playlist?token=abc123",
            "username": "operator",
            "password": "hunter2",
            "user_agent": 4,
        }],
        client=_client(),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    blob = " ".join(report.notes)
    assert "hunter2" not in blob
    assert "operator" not in blob
    assert "abc123" not in blob
    assert "provider/playlist" not in blob


# ---------------------------------------------------------------------------
# The ``server_group`` FK (bead …-g8tyd). An M3U account's ``server_group`` is a
# FOREIGN KEY to a Dispatcharr ``ServerGroup`` row, whose id the destination
# assigns itself — the same shape as the ``user_agent`` defect (…-9h6cv), with
# one difference that decides the fix: there is NO ServerGroup entity category
# and NO ServerGroup importer, so there is no remap namespace to resolve
# through. The FK is therefore DROPPED, never forwarded.
#
# Live on Dispatcharr 0.28.2, an account carrying A's ``server_group`` pk 20
# made B answer
# ``400 {"server_group": ["Invalid pk \"20\" - object does not exist."]}``, and
# because M3U_ACCOUNT is a FATAL failure category the whole apply rolled back
# (``partial_failed_rolled_back``) and nothing synced. The identical payload with
# ``server_group`` removed answered ``201``.
#
# INVARIANT under test: no source-side FK reaches the destination unresolved —
# every one is either remapped or deliberately dropped with a recorded reason.
# ``server_group`` is one example of that property, not the specification.
# ---------------------------------------------------------------------------

_V0282_SERVER_GROUP_ACCOUNT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "bd_g8tyd"
    / "dispatcharr_v0282_m3u_account_server_group.json"
)

# Every FK an M3U account carries into another Dispatcharr table on the CREATE
# payload, per 0.28.2's ``M3UAccountSerializer.Meta.fields``. ``user_agent`` is
# remapped (…-9h6cv); ``server_group`` is dropped (…-g8tyd). A new FK added to
# the payload without a disposition here fails the invariant test below.
_ACCOUNT_FK_KEYS = ("user_agent", "server_group")


@pytest.mark.asyncio
async def test_server_group_fk_is_never_forwarded_to_the_destination():
    """The account's ``server_group`` FK is DROPPED from the create payload.

    A's ServerGroup ids mean nothing on B, and there is no ServerGroup remap
    namespace to translate them through, so the only safe disposition is to omit
    the field: Dispatcharr's column is nullable (``on_delete=SET_NULL``,
    ``null=True, blank=True``) and the account works without it.
    """
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://p/a",
            # 20 is deliberately outside the destination's ServerGroup range —
            # the live B has none at all. A pk that happened to alias an
            # unrelated destination row would make this assertion pass against
            # broken code (the …-9h6cv false-green).
            "server_group": 20,
        }],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert "server_group" not in captured, (
        "source-side server_group pk %r was forwarded to the destination"
        % captured.get("server_group")
    )


@pytest.mark.asyncio
async def test_dropped_server_group_still_creates_the_account():
    """DECISION (…-g8tyd), stated as a test: the account is created WITHOUT the
    field rather than skipped ``DEPENDENCY_UNRESOLVED``.

    Same reasoning as its ``user_agent`` sibling: an M3U account is the ROOT of
    the Phase-2 chain, so skipping it cascades a whole-tree
    ``DEPENDENCY_UNRESOLVED`` for one optional grouping label. Asserted on
    ``created`` / ``failure_details`` / ``skip_details`` explicitly — this
    subsystem records conditions in three different structures and asserting on
    the wrong one passes against broken code.

    The client VALIDATES the FK the way Dispatcharr 0.28.2 does. A permissive
    mock would accept the stale pk and turn this test green against the broken
    code — the create would succeed and ``created == 1`` either way.
    """
    async def _strict_create(payload):
        if payload.get("server_group") is not None:
            raise RuntimeError(
                'Dispatcharr 400: {"server_group": ["Invalid pk \"%s\" - object '
                'does not exist."]}' % payload["server_group"]
            )
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_strict_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_group": 20}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    cat = report.category(EntityType.M3U_ACCOUNT)
    assert cat.created == 1
    assert cat.failed == 0
    assert cat.failure_details == []
    assert cat.skipped == 0
    assert not any(
        d.reason == SkipReason.DEPENDENCY_UNRESOLVED for d in cat.skip_details
    )


@pytest.mark.asyncio
async def test_dropped_server_group_is_a_visible_operator_note():
    """Dropping the field is a DEGRADATION, so it is reported — never silent.

    Asserted on ``report.notes``, the idiom …-9h6cv established for this case
    (NOT ``skip_details`` or ``failure_details``, which stay empty here).
    """
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_group": 20}],
        client=_client(),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert any(
        "Provider A" in note and "server group" in note.lower()
        for note in report.notes
    ), f"no operator note named the degraded account; notes={report.notes}"


@pytest.mark.asyncio
async def test_dry_run_also_reports_the_dropped_server_group():
    """The PREVIEW must not promise what the apply will not deliver: the same
    note appears on a dry-run, which creates nothing."""
    report = _report(is_dry_run=True)
    client = _client()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_group": 20}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
        is_dry_run=True,
    )

    client.create_m3u_account.assert_not_awaited()
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 1
    assert any(
        "Provider A" in note and "server group" in note.lower()
        for note in report.notes
    ), f"the preview stayed silent about the drop; notes={report.notes}"


@pytest.mark.asyncio
async def test_null_server_group_is_untouched_and_notes_nothing():
    """The overwhelmingly common shape — no server group — is not a degradation:
    the field stays null, the account is created, and nothing is noted."""
    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, **payload}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{"id": 5, "name": "Provider A", "server_group": None}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    assert captured["server_group"] is None
    assert report.category(EntityType.M3U_ACCOUNT).created == 1
    assert report.notes == []


@pytest.mark.asyncio
async def test_server_group_note_carries_no_credential_material():
    """The degradation note names the account only — never a server_url,
    username or password."""
    report = _report()

    await import_m3u_accounts(
        archive_accounts=[{
            "id": 5,
            "name": "Provider A",
            "server_url": "http://provider/playlist?token=abc123",
            "username": "operator",
            "password": "hunter2",
            "server_group": 20,
        }],
        client=_client(),
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )

    # Guard the guard: an EMPTY notes list would satisfy every "not in" below
    # while the degradation went entirely unreported.
    assert any("Provider A" in note for note in report.notes), (
        f"nothing was reported, so this scrub check proves nothing; "
        f"notes={report.notes}"
    )
    blob = " ".join(report.notes)
    assert "hunter2" not in blob
    assert "operator" not in blob
    assert "abc123" not in blob
    assert "provider/playlist" not in blob


@pytest.mark.asyncio
async def test_no_source_side_fk_survives_into_a_real_0282_create_payload():
    """THE INVARIANT, over a RECORDED Dispatcharr 0.28.2 response.

    The fixture is the verbatim ``GET /api/m3u/accounts/4/`` body from the
    disposable A instance — the exact shape that made B answer
    ``400 {"server_group": ["Invalid pk \\"20\\" - object does not exist."]}``.
    A hand-built dict cannot catch a field this importer has never heard of; a
    recorded one can.

    For EVERY foreign key an M3U account carries, the value that reaches the
    destination is never the source-side pk: it is either absent, null, or a
    destination id the remap produced.
    """
    archive_account = json.loads(_V0282_SERVER_GROUP_ACCOUNT.read_text())
    assert archive_account["server_group"] == 20, "fixture lost its populated FK"

    captured = {}

    async def _create(payload):
        captured.update(payload)
        return {"id": 901, "name": payload.get("name")}

    client = _client()
    client.create_m3u_account = AsyncMock(side_effect=_create)

    await import_m3u_accounts(
        archive_accounts=[archive_account],
        client=client,
        selected=True,
        report=_report(),
        ledger=_ledger(),
        # Empty: no namespace can resolve either FK, which is the worst case.
        remap=_remap(),
    )

    for fk in _ACCOUNT_FK_KEYS:
        source_pk = archive_account.get(fk)
        if source_pk is None:
            continue
        assert captured.get(fk) != source_pk, (
            "%s forwarded source pk %r to the destination — every FK must be "
            "remapped or dropped" % (fk, source_pk)
        )
