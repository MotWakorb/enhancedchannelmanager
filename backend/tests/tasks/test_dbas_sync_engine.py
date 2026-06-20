"""Tests for the one-way cross-instance sync ENGINE CORE (config categories).

Bead ``enhancedchannelmanager-tjaey`` (epic ``i39wu``). ADR-013 S1/S3/S4/S5/S7/S9;
threat model ``docs/security/threat_model_dbas_import.md`` §11 Addendum D
(D2 redact-by-default, D3 never-sync-users, D5 freshness gate, D8 idempotency).

The engine is "restore over HTTP": it gathers the LOCAL (source-A) config via the
SAME backup gather + redaction pipeline, assembles an :class:`ImportPlan`, and
runs the REUSED DBAS restore orchestrator pointed at a remote (dest-B) client.

These tests mock BOTH the source-A client (the local gather) and the dest-B client
(the orchestrator target) — there is NO live Dispatcharr. They assert:

* convergence  — apply against an empty B creates A's config categories on B;
* idempotency  — a second run is a clean no-op (all ALREADY_EXISTS, zero creates);
* redaction    — no plaintext secret from A appears in the assembled plan (D2);
* never-users  — the users category is never assembled into a sync plan (D3);
* freshness    — a stale/revoked/disabled target aborts with NO client + NO writes (D5);
* dry-run      — confirm_apply=False makes zero writes and returns would-create counts.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dbas.restore_contracts import EntityType, RestoreOutcome
from routers import backup as backup_mod
from tasks import dbas_sync_engine as engine
from tasks.dbas_sync_engine import (
    SYNC_CONFIG_CATEGORIES,
    SYNC_NEVER_CATEGORIES,
    SYNC_NEVER_CREDENTIAL_COLUMNS,
    build_live_source_plan,
    run_sync,
)


# ---------------------------------------------------------------------------
# Source-A config fixture (what the LOCAL instance's gather returns).
# ---------------------------------------------------------------------------

# A seeded M3U password + EPG password — the plaintext secrets that MUST NOT
# survive redaction into the sync plan (D2).
SECRET_M3U_PASSWORD = "super-secret-m3u-pw-do-not-leak"
SECRET_EPG_PASSWORD = "super-secret-epg-pw-do-not-leak"


def _source_client() -> MagicMock:
    """A mock LOCAL source-A client returning A's full config (with secrets)."""
    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 1, "name": "Provider A", "password": SECRET_M3U_PASSWORD,
             "username": "operator"},
        ]
    )
    client.get_epg_sources = AsyncMock(
        return_value=[
            {"id": 10, "name": "EPG One", "source_type": "xmltv",
             "m3u_account": None, "api_key": SECRET_EPG_PASSWORD},
        ]
    )
    client.get_channel_groups = AsyncMock(
        return_value=[{"id": 20, "name": "News"}]
    )
    client.get_channel_profiles = AsyncMock(
        return_value=[{"id": 30, "name": "Default Profile"}]
    )
    client.get_stream_profiles = AsyncMock(
        return_value=[{"id": 40, "name": "Proxy Profile", "command": "ffmpeg"}]
    )
    # A users getter is wired but MUST NEVER be assembled into the sync plan (D3).
    client.get_users = AsyncMock(
        return_value=[{"id": 99, "username": "admin", "is_superuser": True}]
    )
    return client


def _empty_dest_client() -> AsyncMock:
    """An empty dest-B client — every source entity is a fresh create."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    # Create calls echo back a dest id so the ledger/remap stay coherent.
    client.create_m3u_account = AsyncMock(return_value={"id": 101, "name": "Provider A"})
    client.create_epg_source = AsyncMock(return_value={"id": 110, "name": "EPG One"})
    client.create_channel_group = AsyncMock(return_value={"id": 120, "name": "News"})
    client.create_channel_profile = AsyncMock(return_value={"id": 130, "name": "Default Profile"})
    client.create_stream_profile = AsyncMock(return_value={"id": 140, "name": "Proxy Profile"})
    return client


def _converged_dest_client() -> AsyncMock:
    """A dest-B client that ALREADY holds A's config — every entity should skip."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 501, "name": "Provider A"}]
    )
    client.get_epg_sources = AsyncMock(
        return_value=[{"id": 510, "name": "EPG One", "source_type": "xmltv"}]
    )
    client.get_channel_groups = AsyncMock(return_value=[{"id": 520, "name": "News"}])
    client.get_channel_profiles = AsyncMock(
        return_value=[{"id": 530, "name": "Default Profile"}]
    )
    client.get_stream_profiles = AsyncMock(
        return_value=[{"id": 540, "name": "Proxy Profile", "command": "ffmpeg"}]
    )
    # Create methods present but expected UNUSED on a converged run.
    client.create_m3u_account = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_epg_source = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_channel_group = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_channel_profile = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_stream_profile = AsyncMock(return_value={"id": 9, "name": "x"})
    return client


def _sync_target(*, credential_version: int = 1) -> MagicMock:
    """A fake SyncTarget row — enabled, fresh, never-insecure."""
    target = MagicMock()
    target.id = 7
    target.name = "DR Box"
    target.base_url = "http://dr-box.lan:9191"
    target.enabled = True
    target.insecure = False
    target.token_revoked_at = None
    target.credential_version = credential_version
    target.credentials = "encrypted-blob"
    return target


# ---------------------------------------------------------------------------
# Shared never-sync constant — code-enforced (mirrors _REDACT_KEYS).
# ---------------------------------------------------------------------------


def test_never_sync_constant_contains_users():
    """The shared never-sync set permanently excludes the users category (D3)."""
    assert "users" in SYNC_NEVER_CATEGORIES
    # The credential-freshness columns are never assembled either.
    assert "credentials" in SYNC_NEVER_CREDENTIAL_COLUMNS
    assert "credential_version" in SYNC_NEVER_CREDENTIAL_COLUMNS
    assert "token_revoked_at" in SYNC_NEVER_CREDENTIAL_COLUMNS


def test_config_categories_exclude_users_channels_streams_logos():
    """This bead's config set is topology-config-only; the heavy slices are out."""
    assert SYNC_CONFIG_CATEGORIES == frozenset(
        {"m3u_accounts", "epg_sources", "channel_groups",
         "channel_profiles", "stream_profiles"}
    )
    assert "users" not in SYNC_CONFIG_CATEGORIES
    assert "channels" not in SYNC_CONFIG_CATEGORIES
    assert "streams" not in SYNC_CONFIG_CATEGORIES
    assert "logos" not in SYNC_CONFIG_CATEGORIES
    # The config set and the never-sync set never overlap.
    assert SYNC_CONFIG_CATEGORIES.isdisjoint(SYNC_NEVER_CATEGORIES)


# ---------------------------------------------------------------------------
# Live-source plan reader.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_carries_schema_version_and_config_categories():
    """The assembled plan stamps schema_version (orchestrator pre-flight gate)
    and carries exactly the config categories — never users."""
    from routers.backup import BACKUP_SCHEMA_VERSION

    with patch.object(backup_mod, "get_client", return_value=_source_client()):
        plan = await build_live_source_plan()

    # Pre-flight's .17 gate requires manifest.schema_version (spike empirical find).
    assert plan.manifest.get("schema_version") == BACKUP_SCHEMA_VERSION

    present = {c.entity_type for c in plan.categories}
    assert EntityType.M3U_ACCOUNT in present
    assert EntityType.EPG_SOURCE in present
    assert EntityType.CHANNEL_GROUP in present
    assert EntityType.CHANNEL_PROFILE in present
    assert EntityType.STREAM_PROFILE in present
    # D3: users are NEVER a category in a sync plan, even though the source has them.
    assert EntityType.USER not in present
    assert EntityType.CHANNEL not in present
    assert EntityType.LOGO not in present


@pytest.mark.asyncio
async def test_plan_redacts_plaintext_secrets():
    """D2: no plaintext secret from the source survives into the assembled plan."""
    with patch.object(backup_mod, "get_client", return_value=_source_client()):
        plan = await build_live_source_plan()

    blob = json.dumps(plan.model_dump(mode="json"))
    assert SECRET_M3U_PASSWORD not in blob
    assert SECRET_EPG_PASSWORD not in blob


@pytest.mark.asyncio
async def test_plan_never_assembles_users_even_if_source_returns_them():
    """D3: the users getter is never even called for plan assembly (defence in
    depth) and the users category is absent from the plan."""
    src = _source_client()
    with patch.object(backup_mod, "get_client", return_value=src):
        plan = await build_live_source_plan()

    assert plan.category(EntityType.USER) is None
    # The assembler never reaches for users — it is structurally excluded.
    src.get_users.assert_not_called()


# ---------------------------------------------------------------------------
# run_sync — convergence / idempotency / dry-run / freshness.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sync_apply_converges_empty_b(tmp_path):
    """Convergence: apply against an empty B creates A's config categories on B."""
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    # B received every config category as a create.
    dest.create_m3u_account.assert_awaited()
    dest.create_epg_source.assert_awaited()
    dest.create_channel_group.assert_awaited()
    dest.create_channel_profile.assert_awaited()
    dest.create_stream_profile.assert_awaited()

    assert report.is_dry_run is False
    assert report.outcome == RestoreOutcome.SUCCESS
    assert report.category(EntityType.M3U_ACCOUNT).created == 1
    assert report.category(EntityType.CHANNEL_GROUP).created == 1


@pytest.mark.asyncio
async def test_run_sync_second_run_is_noop(tmp_path):
    """Idempotency (D8): a run against an already-converged B creates nothing."""
    src = _source_client()
    dest = _converged_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    dest.create_m3u_account.assert_not_called()
    dest.create_epg_source.assert_not_called()
    dest.create_channel_group.assert_not_called()
    dest.create_channel_profile.assert_not_called()
    dest.create_stream_profile.assert_not_called()

    assert report.outcome == RestoreOutcome.SUCCESS
    # Every category resolved to a skip (already-exists), zero creates.
    for cat in report.categories:
        assert cat.created == 0


@pytest.mark.asyncio
async def test_run_sync_dry_run_default_makes_zero_writes(tmp_path):
    """Dry-run default: confirm_apply=False (default) writes nothing and returns
    would-create counts."""
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(target, session=MagicMock(), ledger_dir=tmp_path)

    # No create call fired anywhere on B.
    dest.create_m3u_account.assert_not_called()
    dest.create_epg_source.assert_not_called()
    dest.create_channel_group.assert_not_called()
    dest.create_channel_profile.assert_not_called()
    dest.create_stream_profile.assert_not_called()

    assert report.is_dry_run is True
    assert report.outcome is None
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 1
    assert report.category(EntityType.CHANNEL_GROUP).would_create == 1


@pytest.mark.asyncio
async def test_run_sync_aborts_on_stale_credentials(tmp_path):
    """D5: a freshness reason aborts the sync — no remote client is built, no
    writes happen."""
    src = _source_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client") as make_client, \
         patch.object(
             engine, "sync_freshness_reason",
             return_value="credentials for sync target 'DR Box' (id=7) were revoked",
         ):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    # Fail-closed: never built a client, never wrote.
    make_client.assert_not_called()
    assert report.outcome is None
    assert any("revoked" in note for note in report.notes)


@pytest.mark.asyncio
async def test_run_sync_journals_the_run(tmp_path):
    """D9: every run leaves a sync_outbound audit row (categories, counts,
    result, redaction_mode)."""
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None), \
         patch.object(engine.journal, "log_entry") as log_entry:
        await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    log_entry.assert_called()
    kwargs = log_entry.call_args.kwargs
    assert kwargs.get("category") == "sync_outbound"
