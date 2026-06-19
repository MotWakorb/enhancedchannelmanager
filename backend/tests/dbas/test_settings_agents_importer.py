"""Tests for the settings/agents DBAS restore importer
(enhancedchannelmanager-0i2vt.13 — Phase 2 bulk importer).

FOUR categories, TWO shapes:

ENTITY categories (create rows, remappable, ledger-tracked):
  1. user_agents -> EntityType.USER_AGENT — create + remap + ledger; identity by
     name; collision -> ALREADY_EXISTS_IDENTICAL + remap to existing.
  2. dvr_rules -> EntityType.DVR_RULE — create + remap + ledger; identity by a
     stable key (name/title); FK ``channel`` reference remapped through the
     IdRemapTable; unresolvable FK -> FailureReason.DEPENDENCY_UNRESOLVED (skip
     DEPENDENCY_UNRESOLVED on dry-run).

SETTINGS categories (APPLY key/value config; NOT entity-create; NOT id-remapped;
NOT ledgered as creates):
  3. core_settings — apply archived key/value settings via per-key PATCH.
     CONSERVATIVE: dangerous (credential/auth/instance-identity) keys are NEVER
     applied — they are reported as skipped, not created, not ledgered. Settings
     rollback is OUT OF SCOPE (a settings change is not a created entity).
  4. comskip — same shape as core_settings (a config blob applied conservatively).

CREDENTIAL HYGIENE: core_settings + comskip may carry credentials/API keys.
NEVER logged, NEVER leaked into the RestoreReport — sanitized. Tests assert no
secret material appears in the report or in any log record.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.settings_agents``) with an AsyncMock.

PLUGINS are NOT restored (ADR-012 D10). USERS are NOT restored here (l1p4p owns
them). Neither is referenced in this module.
"""
import logging

import pytest
from unittest.mock import AsyncMock

from dbas.importers.settings_agents import (
    DANGEROUS_SETTING_KEY_MARKERS,
    import_comskip,
    import_core_settings,
    import_dvr_rules,
    import_settings_agents,
    import_user_agents,
    is_safe_setting_key,
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


def _client(
    *,
    existing_user_agents=None,
    existing_dvr_rules=None,
    ua_create_side_effect=None,
    dvr_create_side_effect=None,
    settings_patch_side_effect=None,
):
    """Build an AsyncMock Dispatcharr client with the methods the importer uses."""
    client = AsyncMock()
    client.get_user_agents = AsyncMock(return_value=existing_user_agents or [])
    client.get_dvr_rules = AsyncMock(return_value=existing_dvr_rules or [])

    ua_counter = {"n": 700}

    async def _default_ua_create(payload):
        ua_counter["n"] += 1
        return {"id": ua_counter["n"], **payload}

    dvr_counter = {"n": 800}

    async def _default_dvr_create(payload):
        dvr_counter["n"] += 1
        return {"id": dvr_counter["n"], **payload}

    client.create_user_agent = AsyncMock(
        side_effect=ua_create_side_effect or _default_ua_create
    )
    client.delete_user_agent = AsyncMock(return_value=None)
    client.create_dvr_rule = AsyncMock(
        side_effect=dvr_create_side_effect or _default_dvr_create
    )
    client.delete_dvr_rule = AsyncMock(return_value=None)
    client.update_core_setting = AsyncMock(
        side_effect=settings_patch_side_effect
        or (lambda key, value: {"key": key, "value": value})
    )
    return client


def _report(is_dry_run=False):
    return RestoreReport(is_dry_run=is_dry_run)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


def _remap(**kwargs):
    """Build an IdRemapTable pre-seeded with given mappings.

    e.g. ``_remap(channel={5: 105})``.
    """
    table = IdRemapTable()
    name_to_type = {
        "channel": EntityType.CHANNEL,
        "user_agent": EntityType.USER_AGENT,
        "dvr_rule": EntityType.DVR_RULE,
    }
    for name, mapping in kwargs.items():
        for src, dest in mapping.items():
            table.add(name_to_type[name], src, dest)
    return table


def _cat(report, entity_type):
    return report.category(entity_type)


# ===========================================================================
# USER AGENTS (entity category)
# ===========================================================================


@pytest.mark.asyncio
async def test_user_agents_create_happy_path_remaps_and_ledgers():
    """A new user agent is created, registered in the remap table under
    USER_AGENT, and recorded in the rollback ledger."""
    archive = [{"id": 1, "name": "VLC", "user_agent": "VLC/3.0.20"}]
    client = _client()
    report, ledger, remap = _report(), _ledger(), _remap()

    await import_user_agents(
        archive_user_agents=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = _cat(report, EntityType.USER_AGENT)
    assert cat.created == 1
    assert remap.resolve(EntityType.USER_AGENT, 1) == 701
    assert len(ledger.entries) == 1
    assert ledger.entries[0].entity_type == EntityType.USER_AGENT
    assert ledger.entries[0].destination_id == 701
    # The create payload dropped the archive id.
    sent = client.create_user_agent.call_args.args[0]
    assert "id" not in sent


@pytest.mark.asyncio
async def test_user_agents_collision_skips_and_remaps_to_existing():
    """A user agent whose name already exists on the destination is skipped
    ALREADY_EXISTS_IDENTICAL and remapped to the existing destination id (so a
    DVR rule referencing it still resolves)."""
    archive = [{"id": 1, "name": "VLC", "user_agent": "VLC/3.0.20"}]
    client = _client(existing_user_agents=[{"id": 555, "name": "vlc"}])
    report, ledger, remap = _report(), _ledger(), _remap()

    await import_user_agents(
        archive_user_agents=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = _cat(report, EntityType.USER_AGENT)
    assert cat.skipped == 1
    assert cat.created == 0
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert remap.resolve(EntityType.USER_AGENT, 1) == 555
    assert ledger.entries == []
    client.create_user_agent.assert_not_called()


@pytest.mark.asyncio
async def test_user_agents_opt_in_off_skips_all():
    """Category opt-out: every record is EXCLUDED_BY_OPERATOR; nothing created."""
    archive = [{"id": 1, "name": "VLC"}, {"id": 2, "name": "Kodi"}]
    client = _client()
    report, ledger, remap = _report(), _ledger(), _remap()

    await import_user_agents(
        archive_user_agents=archive,
        client=client,
        selected=False,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = _cat(report, EntityType.USER_AGENT)
    assert cat.skipped == 2
    assert all(d.reason == SkipReason.EXCLUDED_BY_OPERATOR for d in cat.skip_details)
    client.create_user_agent.assert_not_called()


@pytest.mark.asyncio
async def test_user_agents_dry_run_creates_nothing():
    """Dry-run: would_create incremented; no creates, no ledger entries."""
    archive = [{"id": 1, "name": "VLC"}]
    client = _client()
    report, ledger, remap = _report(is_dry_run=True), _ledger(), _remap()

    await import_user_agents(
        archive_user_agents=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=True,
    )

    cat = _cat(report, EntityType.USER_AGENT)
    assert cat.would_create == 1
    assert cat.created == 0
    assert ledger.entries == []
    client.create_user_agent.assert_not_called()


@pytest.mark.asyncio
async def test_user_agents_conflict_vs_upstream_error():
    """A create raising 'already exists' -> CONFLICT; any other error ->
    UPSTREAM_API_ERROR."""
    async def _conflict(payload):
        raise Exception("name already exists (unique constraint)")

    client = _client(ua_create_side_effect=_conflict)
    report, ledger, remap = _report(), _ledger(), _remap()
    await import_user_agents(
        archive_user_agents=[{"id": 1, "name": "VLC"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )
    cat = _cat(report, EntityType.USER_AGENT)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT

    async def _boom(payload):
        raise Exception("503 service unavailable")

    client2 = _client(ua_create_side_effect=_boom)
    report2 = _report()
    await import_user_agents(
        archive_user_agents=[{"id": 1, "name": "VLC"}],
        client=client2,
        selected=True,
        report=report2,
        ledger=_ledger(),
        remap=_remap(),
    )
    cat2 = _cat(report2, EntityType.USER_AGENT)
    assert cat2.failed == 1
    assert cat2.failure_details[0].reason == FailureReason.UPSTREAM_API_ERROR


# ===========================================================================
# DVR RULES (entity category, FK remap)
# ===========================================================================


@pytest.mark.asyncio
async def test_dvr_rules_create_with_fk_remap_and_ledger():
    """A DVR rule's archived ``channel`` FK is remapped to the destination id;
    the rule is created, remapped, and ledgered."""
    archive = [{"id": 1, "name": "Record News", "channel": 5}]
    client = _client()
    report, ledger = _report(), _ledger()
    remap = _remap(channel={5: 105})

    await import_dvr_rules(
        archive_dvr_rules=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.created == 1
    sent = client.create_dvr_rule.call_args.args[0]
    assert sent["channel"] == 105  # remapped from 5
    assert "id" not in sent
    assert remap.resolve(EntityType.DVR_RULE, 1) == 801
    assert ledger.entries[0].entity_type == EntityType.DVR_RULE


@pytest.mark.asyncio
async def test_dvr_rules_unresolvable_fk_fails_dependency_unresolved():
    """A DVR rule referencing a channel with no remap entry is failed
    DEPENDENCY_UNRESOLVED and never sent upstream."""
    archive = [{"id": 1, "name": "Record News", "channel": 999}]
    client = _client()
    report, ledger = _report(), _ledger()
    remap = _remap(channel={5: 105})  # 999 not mapped

    await import_dvr_rules(
        archive_dvr_rules=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.DEPENDENCY_UNRESOLVED
    client.create_dvr_rule.assert_not_called()
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_dvr_rules_unresolvable_fk_dry_run_skips_dependency_unresolved():
    """On dry-run, an unresolvable FK is a would_skip DEPENDENCY_UNRESOLVED, not a
    failure."""
    archive = [{"id": 1, "name": "Record News", "channel": 999}]
    client = _client()
    report = _report(is_dry_run=True)
    remap = _remap(channel={5: 105})

    await import_dvr_rules(
        archive_dvr_rules=archive,
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=remap,
        is_dry_run=True,
    )

    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.would_skip == 1
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED
    client.create_dvr_rule.assert_not_called()


@pytest.mark.asyncio
async def test_dvr_rules_collision_skips_and_remaps():
    """A DVR rule whose name already exists is skipped ALREADY_EXISTS_IDENTICAL
    and remapped to the existing id."""
    archive = [{"id": 1, "name": "Record News", "channel": 5}]
    client = _client(existing_dvr_rules=[{"id": 444, "name": "record news"}])
    report, ledger = _report(), _ledger()
    remap = _remap(channel={5: 105})

    await import_dvr_rules(
        archive_dvr_rules=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=remap,
    )

    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL
    assert remap.resolve(EntityType.DVR_RULE, 1) == 444
    client.create_dvr_rule.assert_not_called()


@pytest.mark.asyncio
async def test_dvr_rules_opt_in_off_skips_all():
    archive = [{"id": 1, "name": "R1"}]
    client = _client()
    report = _report()
    await import_dvr_rules(
        archive_dvr_rules=archive,
        client=client,
        selected=False,
        report=report,
        ledger=_ledger(),
        remap=_remap(),
    )
    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR
    client.create_dvr_rule.assert_not_called()


@pytest.mark.asyncio
async def test_dvr_rules_dry_run_creates_nothing():
    archive = [{"id": 1, "name": "R1", "channel": 5}]
    client = _client()
    report = _report(is_dry_run=True)
    ledger = _ledger()
    await import_dvr_rules(
        archive_dvr_rules=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        remap=_remap(channel={5: 105}),
        is_dry_run=True,
    )
    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.would_create == 1
    assert cat.created == 0
    assert ledger.entries == []
    client.create_dvr_rule.assert_not_called()


@pytest.mark.asyncio
async def test_dvr_rules_conflict_vs_upstream_error():
    async def _conflict(payload):
        raise Exception("already exists")

    client = _client(dvr_create_side_effect=_conflict)
    report = _report()
    await import_dvr_rules(
        archive_dvr_rules=[{"id": 1, "name": "R1", "channel": 5}],
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        remap=_remap(channel={5: 105}),
    )
    cat = _cat(report, EntityType.DVR_RULE)
    assert cat.failure_details[0].reason == FailureReason.CONFLICT

    async def _boom(payload):
        raise Exception("500 internal")

    client2 = _client(dvr_create_side_effect=_boom)
    report2 = _report()
    await import_dvr_rules(
        archive_dvr_rules=[{"id": 1, "name": "R1", "channel": 5}],
        client=client2,
        selected=True,
        report=report2,
        ledger=_ledger(),
        remap=_remap(channel={5: 105}),
    )
    cat2 = _cat(report2, EntityType.DVR_RULE)
    assert cat2.failure_details[0].reason == FailureReason.UPSTREAM_API_ERROR


# ===========================================================================
# CORE SETTINGS (settings category — apply key/value, conservative)
# ===========================================================================


def test_is_safe_setting_key_blocks_credential_markers():
    """The key-safety predicate rejects credential/auth/instance-identity keys."""
    for bad in [
        "dispatcharr_api_key",
        "API_KEY",
        "secret_token",
        "smtp_password",
        "auth_token",
        "private_key",
        "session_secret",
        "credentials",
        "jwt_signing_key",
    ]:
        assert is_safe_setting_key(bad) is False, bad
    for good in [
        "default_user_agent",
        "preferred_region",
        "auto_import_mapped_files",
        "comskip_enabled",
        "ui_theme",
    ]:
        assert is_safe_setting_key(good) is True, good


@pytest.mark.asyncio
async def test_core_settings_applies_safe_keys_only():
    """Safe keys are PATCHed; the category is reported updated (not created), no
    ledger entry. A dangerous key in the archive is NOT applied."""
    archive = {
        "default_user_agent": "VLC/3.0.20",
        "ui_theme": "dark",
        "dispatcharr_api_key": "SECRET-KEY-VALUE",  # must NOT be applied
    }
    client = _client()
    report, ledger = _report(), _ledger()

    await import_core_settings(
        archive_core_settings=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    cat = report.category(EntityType.M3U_ACCOUNT)  # placeholder check below
    # Settings land in their own pseudo-category; assert via the report notes /
    # updated counts on the settings category.
    settings_cat = _settings_category(report)
    assert settings_cat.updated == 2
    assert settings_cat.skipped == 1  # the dangerous key skipped

    applied_keys = {c.args[0] for c in client.update_core_setting.call_args_list}
    assert applied_keys == {"default_user_agent", "ui_theme"}
    assert "dispatcharr_api_key" not in applied_keys
    # No ledger create-entry for settings.
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_core_settings_dry_run_applies_nothing():
    archive = {"default_user_agent": "VLC", "ui_theme": "dark"}
    client = _client()
    report = _report(is_dry_run=True)
    await import_core_settings(
        archive_core_settings=archive,
        client=client,
        selected=True,
        report=report,
        ledger=_ledger(),
        is_dry_run=True,
    )
    settings_cat = _settings_category(report)
    assert settings_cat.would_update == 2
    assert settings_cat.updated == 0
    client.update_core_setting.assert_not_called()


@pytest.mark.asyncio
async def test_core_settings_opt_in_off_applies_nothing():
    archive = {"default_user_agent": "VLC"}
    client = _client()
    report = _report()
    await import_core_settings(
        archive_core_settings=archive,
        client=client,
        selected=False,
        report=report,
        ledger=_ledger(),
    )
    client.update_core_setting.assert_not_called()


@pytest.mark.asyncio
async def test_core_settings_no_secret_in_report_or_logs(caplog):
    """A credential key/value in the archive never appears in the report (notes,
    skip/failure details) nor in any log record."""
    secret_value = "TOP-SECRET-abcdef123456"
    archive = {
        "dispatcharr_api_key": secret_value,
        "smtp_password": "hunter2-password",
        "default_user_agent": "VLC/3.0.20",
    }
    client = _client()
    report = _report()
    with caplog.at_level(logging.DEBUG):
        await import_core_settings(
            archive_core_settings=archive,
            client=client,
            selected=True,
            report=report,
            ledger=_ledger(),
        )

    blob = report.model_dump_json()
    assert secret_value not in blob
    assert "hunter2-password" not in blob
    # The dangerous key NAME may appear (as a skipped-key audit) but the VALUE
    # never does — and no log record carries either secret value.
    for rec in caplog.records:
        msg = rec.getMessage()
        assert secret_value not in msg
        assert "hunter2-password" not in msg


# ===========================================================================
# COMSKIP (settings category — same shape)
# ===========================================================================


@pytest.mark.asyncio
async def test_comskip_applies_safe_keys_only():
    """Comskip config applies safe keys, skips dangerous ones, reports
    updated/skipped not created, and never ledgers."""
    archive = {
        "comskip_enabled": True,
        "comskip_ini": "detect_method=255",
        "comskip_upload_token": "SECRET-COMSKIP-TOKEN",  # dangerous -> skip
    }
    client = _client()
    report, ledger = _report(), _ledger()

    await import_comskip(
        archive_comskip=archive,
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    settings_cat = _settings_category(report)
    assert settings_cat.updated == 2
    assert settings_cat.skipped == 1
    applied = {c.args[0] for c in client.update_core_setting.call_args_list}
    assert "comskip_upload_token" not in applied
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_comskip_no_secret_leak(caplog):
    secret = "SECRET-COMSKIP-TOKEN-zzz"
    archive = {"comskip_enabled": True, "comskip_api_secret": secret}
    client = _client()
    report = _report()
    with caplog.at_level(logging.DEBUG):
        await import_comskip(
            archive_comskip=archive,
            client=client,
            selected=True,
            report=report,
            ledger=_ledger(),
        )
    assert secret not in report.model_dump_json()
    for rec in caplog.records:
        assert secret not in rec.getMessage()


# ===========================================================================
# BULK ENTRY (per-category opt-in)
# ===========================================================================


@pytest.mark.asyncio
async def test_bulk_entry_per_category_opt_in():
    """import_settings_agents drives all four categories with independent opt-in
    flags; an off category creates/applies nothing."""
    archive = {
        "user_agents": [{"id": 1, "name": "VLC"}],
        "dvr_rules": [{"id": 1, "name": "R1", "channel": 5}],
        "core_settings": {"ui_theme": "dark"},
        "comskip": {"comskip_enabled": True},
    }
    client = _client()
    report, ledger = _report(), _ledger()
    remap = _remap(channel={5: 105})

    await import_settings_agents(
        archive=archive,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        select_user_agents=True,
        select_dvr_rules=False,  # OFF
        select_core_settings=True,
        select_comskip=False,  # OFF
    )

    assert _cat(report, EntityType.USER_AGENT).created == 1
    assert _cat(report, EntityType.DVR_RULE).skipped == 1  # opt-out
    client.create_dvr_rule.assert_not_called()
    # core_settings applied, comskip not.
    applied = {c.args[0] for c in client.update_core_setting.call_args_list}
    assert applied == {"ui_theme"}


# ---------------------------------------------------------------------------
# Local helper to read the settings pseudo-category
# ---------------------------------------------------------------------------


def _settings_category(report):
    """Return the settings category report (core_settings + comskip share the
    M3U/settings shape via a dedicated EntityType not used for create)."""
    from dbas.importers.settings_agents import SETTINGS_CATEGORY_TYPE

    return report.category(SETTINGS_CATEGORY_TYPE)


def test_dangerous_markers_constant_is_frozenset():
    """The dangerous-key marker set is an exported, inspectable frozenset."""
    assert isinstance(DANGEROUS_SETTING_KEY_MARKERS, frozenset)
    assert "api_key" in DANGEROUS_SETTING_KEY_MARKERS
    assert "password" in DANGEROUS_SETTING_KEY_MARKERS
