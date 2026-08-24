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

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import EntityType, FailureReason, RestoreOutcome, SkipReason
from routers import backup as backup_mod
from tasks import dbas_sync_engine as engine
from tasks.dbas_sync_engine import (
    SYNC_CONFIG_CATEGORIES,
    SYNC_NEVER_CATEGORIES,
    SYNC_NEVER_CREDENTIAL_COLUMNS,
    _split_name_conflicts,
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
            # ``password``, not ``api_key``: Dispatcharr REMOVED ``api_key``
            # from ``EPGSource`` in its ``epg/0024`` migration and replaced it
            # with ``username``/``password`` (bead ``…-fmtg0``).
            {"id": 10, "name": "EPG One", "source_type": "xmltv",
             "m3u_account": None, "password": SECRET_EPG_PASSWORD},
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
    # User agents ARE a per-cycle config category (ADR-013 S9, bead …-hiacv).
    # Wired empty here so the shared fixture exercises the real gather without
    # changing what the pre-existing convergence/idempotency tests assert; the
    # populated case has its own fixtures below.
    client.get_user_agents = AsyncMock(return_value=[])
    # A users getter is wired but MUST NEVER be assembled into the sync plan (D3).
    client.get_users = AsyncMock(
        return_value=[{"id": 99, "username": "admin", "is_superuser": True}]
    )
    # Channels default to empty so tests that don't care about the CHANNEL
    # category still exercise the real `_gather_live_channels` path (an
    # unwired MagicMock here silently swallows a TypeError on `await` and
    # returns [] anyway, masking whether the channels slice actually ran).
    client.get_channels = AsyncMock(return_value={"results": [], "count": 0})
    client.get_channel_streams = AsyncMock(return_value=[])
    return client


def _empty_dest_client() -> AsyncMock:
    """An empty dest-B client — every source entity is a fresh create."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    client.get_user_agents = AsyncMock(return_value=[])
    # Create calls echo back a dest id so the ledger/remap stay coherent.
    client.create_m3u_account = AsyncMock(return_value={"id": 101, "name": "Provider A"})
    client.create_epg_source = AsyncMock(return_value={"id": 110, "name": "EPG One"})
    client.create_channel_group = AsyncMock(return_value={"id": 120, "name": "News"})
    client.create_channel_profile = AsyncMock(return_value={"id": 130, "name": "Default Profile"})
    client.create_stream_profile = AsyncMock(return_value={"id": 140, "name": "Proxy Profile"})
    client.create_user_agent = AsyncMock(return_value={"id": 150, "name": "Custom UA"})
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
    client.get_user_agents = AsyncMock(return_value=[])
    # Create methods present but expected UNUSED on a converged run.
    client.create_m3u_account = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_epg_source = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_channel_group = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_channel_profile = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_stream_profile = AsyncMock(return_value={"id": 9, "name": "x"})
    client.create_user_agent = AsyncMock(return_value={"id": 9, "name": "x"})
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
    # Explicit booleans — a bare MagicMock attribute is TRUTHY, which would
    # silently opt every test into fuzzy matching / logo sync.
    target.fuzzy_stream_matching = False
    target.sync_logos = False
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
    """The CONFIG set stays topology-config-only — M3U/EPG/groups/profiles plus
    the two FK-owner categories an M3U account and a stream profile resolve
    through: USER AGENTS (bead …-hiacv) and SERVER GROUPS (bead …-tyrg1).
    Channels are a SEPARATE set (kcxie); users/logos are never in either."""
    assert SYNC_CONFIG_CATEGORIES == frozenset(
        {"m3u_accounts", "epg_sources", "channel_groups",
         "channel_profiles", "stream_profiles", "user_agents",
         "server_groups"}
    )
    assert "users" not in SYNC_CONFIG_CATEGORIES
    # Channels are NOT a config category — they are gathered separately (kcxie).
    assert "channels" not in SYNC_CONFIG_CATEGORIES
    assert "streams" not in SYNC_CONFIG_CATEGORIES
    assert "logos" not in SYNC_CONFIG_CATEGORIES
    # The config set and the never-sync set never overlap.
    assert SYNC_CONFIG_CATEGORIES.isdisjoint(SYNC_NEVER_CATEGORIES)


def test_all_categories_add_channels_but_never_logos_or_users():
    """The full per-cycle surface (kcxie) = config + channels; logos/users excluded
    (ADR-013 S9 / D3)."""
    from tasks.dbas_sync_engine import SYNC_ALL_CATEGORIES, SYNC_CHANNEL_CATEGORIES

    assert SYNC_CHANNEL_CATEGORIES == frozenset({"channels"})
    assert SYNC_ALL_CATEGORIES == SYNC_CONFIG_CATEGORIES | {"channels"}
    # Logos carry a destructive clear_existing + streaming cost — never per cycle.
    assert "logos" not in SYNC_ALL_CATEGORIES
    # Users are never synced (D3).
    assert "users" not in SYNC_ALL_CATEGORIES
    assert SYNC_ALL_CATEGORIES.isdisjoint(SYNC_NEVER_CATEGORIES)


# ---------------------------------------------------------------------------
# USER AGENTS in the per-cycle set (bead …-hiacv). ADR-013 S9 lists user agents
# in the config categories synced every cycle; before this bead the gather never
# fetched them AND the sync step registry carried no USER_AGENT step, so every
# stream profile with a ``user_agent`` FK was skipped DEPENDENCY_UNRESOLVED.
# ---------------------------------------------------------------------------


def test_config_categories_include_user_agents():
    """S9 lists user agents in the per-cycle config set — and they are NOT users."""
    assert "user_agents" in SYNC_CONFIG_CATEGORIES
    # A user AGENT is a different entity from a Django USER. Wiring the former
    # must not widen the permanently-never-synced set (ADR-013 S3 / D3).
    assert "users" not in SYNC_CONFIG_CATEGORIES
    assert "users" in SYNC_NEVER_CATEGORIES
    assert SYNC_CONFIG_CATEGORIES.isdisjoint(SYNC_NEVER_CATEGORIES)


def _step_index(steps, entity_type) -> int:
    """Index of ``entity_type`` in an ordered ImporterStep list (-1 when absent)."""
    for i, step in enumerate(steps):
        if step.entity_type == entity_type:
            return i
    return -1


def test_sync_registry_imports_user_agents_before_stream_profiles():
    """The sync registry carries a USER_AGENT step, ordered BEFORE STREAM_PROFILE
    so a stream profile's ``user_agent`` FK resolves through a populated remap."""
    from tasks.dbas_sync_engine import sync_config_importer_steps

    steps = sync_config_importer_steps()
    agents = _step_index(steps, EntityType.USER_AGENT)
    profiles = _step_index(steps, EntityType.STREAM_PROFILE)

    assert agents >= 0, "sync registry carries no USER_AGENT step"
    assert profiles >= 0, "sync registry carries no STREAM_PROFILE step"
    assert agents < profiles, (
        "USER_AGENT must be imported before STREAM_PROFILE — the FK resolves "
        "through the USER_AGENT remap namespace"
    )
    # D3 stays intact: the sync registry never imports the users category.
    assert _step_index(steps, EntityType.USER) == -1


def test_sync_and_restore_registries_agree_on_agent_before_profile():
    """Ordering parity: both registries are fed by the SAME
    ``_importer_step_builders`` callables, so an ordering divergence between them
    is exactly how this defect arose. Assert the relationship, in both."""
    from dbas.restore_orchestrator import default_importer_steps
    from tasks.dbas_sync_engine import sync_config_importer_steps

    for label, steps in (
        ("restore", default_importer_steps()),
        ("sync", sync_config_importer_steps()),
    ):
        agents = _step_index(steps, EntityType.USER_AGENT)
        profiles = _step_index(steps, EntityType.STREAM_PROFILE)
        assert agents >= 0, f"{label} registry carries no USER_AGENT step"
        assert profiles >= 0, f"{label} registry carries no STREAM_PROFILE step"
        assert agents < profiles, (
            f"{label} registry imports USER_AGENT after STREAM_PROFILE"
        )


def _source_client_with_custom_user_agent() -> MagicMock:
    """Source A: one custom user agent, and a stream profile whose ``user_agent``
    FK points at it — the exact shape that used to skip DEPENDENCY_UNRESOLVED."""
    client = _source_client()
    client.get_user_agents = AsyncMock(
        return_value=[{"id": 50, "name": "Custom UA", "user_agent": "ECM/1.0"}]
    )
    client.get_stream_profiles = AsyncMock(
        return_value=[
            {"id": 40, "name": "Proxy Profile", "command": "ffmpeg",
             "user_agent": 50},
        ]
    )
    return client


@pytest.mark.asyncio
async def test_run_sync_creates_user_agent_and_its_stream_profile_on_empty_b(tmp_path):
    """INVARIANT (…-hiacv): nothing the ratified per-cycle set says is synced may
    be skipped DEPENDENCY_UNRESOLVED. The custom-user-agent stream profile is one
    example — it must be CREATED on B, with the FK rewritten to B's agent id."""
    src = _source_client_with_custom_user_agent()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    # The agent itself was created on B.
    dest.create_user_agent.assert_awaited()
    assert report.category(EntityType.USER_AGENT).created == 1

    # And the profile that depends on it was CREATED — not skipped.
    dest.create_stream_profile.assert_awaited()
    profile_cat = report.category(EntityType.STREAM_PROFILE)
    assert profile_cat.created == 1
    assert profile_cat.failed == 0
    # The unresolved FK is recorded as a SKIP (SkipReason), not a failure — the
    # detail list to assert on is skip_details, and asserting on failure_details
    # instead is a false-green (proven: that form passed against the broken code).
    assert not any(
        d.reason == SkipReason.DEPENDENCY_UNRESOLVED
        for d in profile_cat.skip_details
    )

    # The FK on the wire is B's agent id (150), never A's source id (50).
    payload = dest.create_stream_profile.await_args.args[0]
    assert payload["user_agent"] == 150

    assert report.outcome == RestoreOutcome.SUCCESS


@pytest.mark.asyncio
async def test_stream_profile_fk_repoints_at_b_own_user_agent(tmp_path):
    """When B ALREADY has the agent under the same name at a DIFFERENT id, the
    profile's FK must be re-pointed at B's id — never A's, and never skipped."""
    src = _source_client_with_custom_user_agent()
    dest = _empty_dest_client()
    # B holds the same agent by name, but under its own id.
    dest.get_user_agents = AsyncMock(
        return_value=[{"id": 950, "name": "Custom UA", "user_agent": "ECM/1.0"}]
    )
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    # The agent is adopted, not duplicated.
    dest.create_user_agent.assert_not_called()
    assert report.category(EntityType.USER_AGENT).created == 0

    # The profile still creates, bound to B's agent id.
    dest.create_stream_profile.assert_awaited()
    payload = dest.create_stream_profile.await_args.args[0]
    assert payload["user_agent"] == 950
    assert report.category(EntityType.STREAM_PROFILE).created == 1


@pytest.mark.asyncio
async def test_no_category_skipped_dependency_unresolved_on_empty_b(tmp_path):
    """The invariant stated directly: after a cycle over the ratified per-cycle
    category set, NO category reports a DEPENDENCY_UNRESOLVED skip/failure."""
    src = _source_client_with_custom_user_agent()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(),
            ledger_dir=tmp_path,
        )

    offenders = [
        (cat.entity_type, d.label)
        for cat in report.categories
        for d in list(cat.skip_details) + list(cat.failure_details)
        if d.reason
        in (SkipReason.DEPENDENCY_UNRESOLVED, FailureReason.DEPENDENCY_UNRESOLVED)
    ]
    assert offenders == [], f"DEPENDENCY_UNRESOLVED after sync: {offenders}"


@pytest.mark.asyncio
async def test_plan_carries_user_agents_but_never_users(tmp_path):
    """The gather now fetches user agents into the plan — and still never users."""
    src = _source_client_with_custom_user_agent()

    with patch.object(backup_mod, "get_client", return_value=src):
        plan = await build_live_source_plan()

    agent_cat = next(
        (c for c in plan.categories if c.entity_type == EntityType.USER_AGENT), None
    )
    assert agent_cat is not None, "sync plan carries no USER_AGENT category"
    assert [e.get("name") for e in agent_cat.entities] == ["Custom UA"]
    # D3 is untouched: users are still never assembled.
    assert EntityType.USER not in {c.entity_type for c in plan.categories}


# ---------------------------------------------------------------------------
# Live-source plan reader.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_carries_schema_version_and_config_categories():
    """The assembled plan stamps schema_version (orchestrator pre-flight gate)
    and carries the config categories PLUS channels (kcxie) — never users/logos."""
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
    # Channels are now IN the plan (bead kcxie) — appended last after config deps.
    assert EntityType.CHANNEL in present
    # D3 / S9: users and logos are NEVER a category in a sync plan, even though the
    # source has them.
    assert EntityType.USER not in present
    assert EntityType.LOGO not in present


@pytest.mark.asyncio
async def test_plan_carries_the_provider_credential():
    """AMENDED 2026-08-22 — this test asserted the exact opposite.

    It was ``test_plan_redacts_plaintext_secrets`` and pinned D2's "no plaintext
    secret from the source survives into the assembled plan". The PO ruled that
    provider credentials cross on every cycle (ADR-013 amendment (b)), so the
    provider half of that claim is now wrong. It is INVERTED here rather than
    deleted: the assembled plan is where a regression would show up first.

    The half that did NOT change is pinned by
    :func:`test_plan_still_redacts_ecms_own_secrets` below, so the pair is a
    CONTRAST — a redactor accidentally switched off everywhere turns that one
    red while leaving this one green.
    """
    with patch.object(backup_mod, "get_client", return_value=_source_client()):
        plan = await build_live_source_plan()

    blob = json.dumps(plan.model_dump(mode="json"))
    assert SECRET_M3U_PASSWORD in blob
    assert SECRET_EPG_PASSWORD in blob


@pytest.mark.asyncio
async def test_plan_still_redacts_ecms_own_secrets():
    """The exception is TWO SECTIONS WIDE, and this is what proves it.

    ECM's own settings secrets and alert-method secrets are not provider
    credentials and have no purpose on a replica. Asserting the provider half
    alone would pass just as happily if ``preserve_keys`` had been applied to
    the whole gather, which is the easy and invisible way to widen this.
    """
    from tasks.dbas_sync_engine import _redact_sync_sections

    redacted = _redact_sync_sections(
        {
            "m3u_accounts": [{"name": "P", "password": SECRET_M3U_PASSWORD}],
            "settings": [{"key": "smtp_password", "password": "ECM-OWN-SECRET"}],
            "alert_methods": [{"name": "Ops", "bot_token": "ECM-OWN-TOKEN"}],
        }
    )
    blob = json.dumps(redacted, default=str)
    assert "ECM-OWN-SECRET" not in blob
    assert "ECM-OWN-TOKEN" not in blob
    assert SECRET_M3U_PASSWORD in blob


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


# ---------------------------------------------------------------------------
# Source-side name-conflict tolerance (bug fix): a duplicate name in a
# NAME_UNIQUE_CATEGORIES category degrades to a per-item CONFLICT instead of
# preflight's all-or-nothing refusal blanking out every other category's diff.
# ---------------------------------------------------------------------------


def _source_client_with_duplicate_channel_groups() -> MagicMock:
    """A source-A client whose channel_groups carry a source-side duplicate
    name (e.g. two groups both named "World Cup 2026") — every OTHER category
    is a normal, conflict-free single entity."""
    client = _source_client()
    client.get_channel_groups = AsyncMock(
        return_value=[
            {"id": 20, "name": "World Cup 2026"},
            {"id": 21, "name": "World Cup 2026"},
        ]
    )
    return client


def test_split_name_conflicts_keeps_first_dedupes_case_insensitive_dupes():
    """Unit-level: normalization matches preflight's exactly (trim + lowercase),
    non-string/empty names are left alone, and a category OUTSIDE
    NAME_UNIQUE_CATEGORIES is never touched even if it carries "duplicate"
    names (e.g. two CHANNEL entities may legitimately share a display name)."""
    plan = ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL_GROUP,
                entities=[
                    {"id": 1, "name": "World Cup 2026"},
                    {"id": 2, "name": " world cup 2026 "},  # dup: trim+lowercase match
                    {"id": 3, "name": "News"},
                    {"id": 4, "name": None},  # non-string name: left alone
                    {"id": 5},  # missing name: left alone
                ],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[
                    {"id": 10, "name": "CNN"},
                    {"id": 11, "name": "CNN"},
                ],
            ),
        ],
    )

    deduped, excluded = _split_name_conflicts(plan)

    group_cat = deduped.category(EntityType.CHANNEL_GROUP)
    assert {e.get("id") for e in group_cat.entities} == {1, 3, 4, 5}
    assert [e["id"] for e in excluded[EntityType.CHANNEL_GROUP]] == [2]

    # CHANNEL is not in NAME_UNIQUE_CATEGORIES — both "CNN" entities survive.
    channel_cat = deduped.category(EntityType.CHANNEL)
    assert len(channel_cat.entities) == 2
    assert EntityType.CHANNEL not in excluded


def test_split_name_conflicts_remaps_channel_fk_off_excluded_duplicate():
    """FK-remap regression: a CHANNEL entity referencing the EXCLUDED
    duplicate's source id must have that FK rewritten onto the KEPT (surviving,
    first-occurrence) entity's source id — so
    ``dbas.preflight._validate_fk_references`` does not refuse the deduped plan
    (``UNRESOLVED_FK_REFERENCE``) all over again. Without this, a channel
    referencing the losing duplicate's id would dangle and reproduce the exact
    "0 items processed" bug this whole tolerance model exists to close, just
    via a different validator than DUPLICATE_UNIQUE_NAME.

    Exercises ``channel_group_id`` — the field this actually fires for today,
    since :data:`~dbas.preflight.NAME_UNIQUE_CATEGORIES` (what
    ``_split_name_conflicts`` dedupes) currently covers
    ``CHANNEL_GROUP``/``CHANNEL_PROFILE``/``M3U_ACCOUNT`` but NOT
    ``STREAM_PROFILE`` — so a duplicate stream-profile name is never deduped
    (and its FK never remapped) yet. The remap logic itself is generic over
    every field in ``CHANNEL_FK_FIELDS``, so ``stream_profile_id`` will pick up
    the same fix automatically if/when ``STREAM_PROFILE`` joins
    ``NAME_UNIQUE_CATEGORIES``."""
    from dbas.preflight import run_preflight

    plan = ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL_GROUP,
                entities=[
                    {"id": 20, "name": "World Cup 2026"},
                    {"id": 21, "name": "World Cup 2026"},  # excluded duplicate
                ],
            ),
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[
                    {
                        "id": 5,
                        "name": "CNN",
                        "channel_group_id": 21,  # references the LOSING duplicate
                    },
                ],
            ),
        ],
    )

    deduped, excluded = _split_name_conflicts(plan)

    assert [e["id"] for e in excluded[EntityType.CHANNEL_GROUP]] == [21]

    # The channel's FK now points at the KEPT (surviving) source id.
    channel = deduped.category(EntityType.CHANNEL).entities[0]
    assert channel["channel_group_id"] == 20

    # The deduped + remapped plan PASSES preflight outright — the regression
    # test for the bug: previously this would still fail preflight with
    # UNRESOLVED_FK_REFERENCE even after the name-conflict dedup.
    result = run_preflight(deduped)
    assert result.passed, result.problems


@pytest.mark.asyncio
async def test_run_sync_duplicate_channel_group_name_is_conflict_not_plan_refusal(
    tmp_path,
):
    """A source-side duplicate channel-group name must NOT trigger preflight's
    all-or-nothing refusal (the root-cause bug: "0 items processed" instead of
    the many channels/groups the operator expected). It degrades to exactly
    ONE per-item CONFLICT — mirroring channels.py's existing ambiguous-
    collision precedent — while every OTHER category still gets diffed."""
    src = _source_client_with_duplicate_channel_groups()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    # Preflight did NOT refuse the whole plan — the OTHER categories resolved
    # normally (this is the exact bug: report.categories used to stay []).
    assert report.category(EntityType.M3U_ACCOUNT).created == 1
    assert report.category(EntityType.EPG_SOURCE).created == 1
    assert report.category(EntityType.STREAM_PROFILE).created == 1

    # channel_group: first occurrence kept + created; the duplicate is ONE conflict.
    cat = report.category(EntityType.CHANNEL_GROUP)
    assert cat.created == 1
    assert cat.failed == 1
    assert len(cat.failure_details) == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert cat.failure_details[0].source_export_id == 21
    dest.create_channel_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_sync_dry_run_duplicate_channel_group_name_is_conflict(tmp_path):
    """The same tolerance applies to a dry-run preview — a conflict is visible
    BEFORE an operator ever confirms apply (no ``is_dry_run`` guard, matching
    the channels.py precedent)."""
    src = _source_client_with_duplicate_channel_groups()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(target, session=MagicMock(), ledger_dir=tmp_path)

    assert report.is_dry_run is True
    dest.create_channel_group.assert_not_called()
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 1

    cat = report.category(EntityType.CHANNEL_GROUP)
    assert cat.would_create == 1
    assert cat.failed == 1
    assert len(cat.failure_details) == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert any("name-conflict" in n for n in report.notes)


def _source_client_with_duplicate_group_and_referencing_channel() -> MagicMock:
    """Duplicate channel-groups (20 kept, 21 excluded) PLUS a CHANNEL entity
    whose ``channel_group_id`` references the EXCLUDED duplicate's source id
    (21) — the real-world scenario the FK-remap fix closes: a channel that was
    attached to the losing duplicate group must end up on the surviving one,
    not dangle. ``get_channels``/``get_channel_streams`` are wired as real
    AsyncMocks so ``_gather_live_channels`` actually returns this channel
    instead of silently swallowing an unwired-mock TypeError and returning []."""
    client = _source_client()
    client.get_channel_groups = AsyncMock(
        return_value=[
            {"id": 20, "name": "World Cup 2026"},
            {"id": 21, "name": "World Cup 2026"},
        ]
    )
    client.get_channels = AsyncMock(
        return_value={
            "results": [
                {
                    "id": 5,
                    "name": "CNN",
                    "channel_number": 5,
                    "channel_group_id": 21,
                    "streams": [],
                },
            ],
            "count": 1,
        }
    )
    client.get_channel_streams = AsyncMock(return_value=[])
    return client


@pytest.mark.asyncio
async def test_run_sync_channel_fk_remapped_off_excluded_duplicate_group(tmp_path):
    """End-to-end FK-remap regression (Finding 1): a channel referencing the
    EXCLUDED duplicate channel-group's source id must NOT trip preflight's
    UNRESOLVED_FK_REFERENCE (which would refuse the WHOLE plan again — the
    exact "0 items processed" bug via a different validator). It must instead
    be remapped onto the KEPT group and sync normally."""
    src = _source_client_with_duplicate_group_and_referencing_channel()
    dest = _dest_client_for_channels()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    # (a) preflight did NOT refuse the whole plan — every OTHER category still
    # resolved normally (the regression: report.categories used to stay []).
    assert report.category(EntityType.M3U_ACCOUNT).created == 1
    assert report.category(EntityType.EPG_SOURCE).created == 1
    assert report.category(EntityType.STREAM_PROFILE).created == 1

    # channel_group: first occurrence (20) kept + created; duplicate (21) is
    # ONE conflict, same as the existing non-FK-referencing test above.
    group_cat = report.category(EntityType.CHANNEL_GROUP)
    assert group_cat.created == 1
    assert group_cat.failed == 1
    dest.create_channel_group.assert_awaited_once()

    # (b) the CHANNEL category was actually processed — not empty (this is the
    # test-harness gap the reviewer found: an unwired get_channels mock would
    # silently make this category empty and mask the whole FK-remap bug).
    channel_cat = report.category(EntityType.CHANNEL)
    assert channel_cat.created == 1
    assert channel_cat.failed == 0

    # (c) the channel that referenced the excluded group's source id (21) was
    # created against the KEPT group's DESTINATION id — verified via the
    # destination client's create_channel call args, mirroring how the other
    # CHANNEL tests in this file verify channel-group attachment.
    dest.create_channel.assert_awaited_once()
    payload = dest.create_channel.await_args.args[0]
    assert payload["channel_group_id"] == 120  # dest id _empty_dest_client's
    # create_channel_group mock always returns for the (one) group it creates.


# ---------------------------------------------------------------------------
# CHANNELS + STREAMS sync (bead kcxie) — convergence, idempotency, the
# collision-safe floor (ruling 1a) and the per-target fuzzy flag (ruling 1b).
# ---------------------------------------------------------------------------


def _source_client_with_channels(*, channels, channel_streams=None) -> MagicMock:
    """A source-A client that also serves channels + their embedded streams.

    ``channels`` is the paginated channel list; ``channel_streams`` maps a channel
    id -> its stream records (what get_channel_streams returns).
    """
    client = _source_client()
    client.get_channels = AsyncMock(
        return_value={"results": channels, "count": len(channels)}
    )
    streams_by_channel = channel_streams or {}

    async def _channel_streams(channel_id):
        return streams_by_channel.get(channel_id, [])

    client.get_channel_streams = AsyncMock(side_effect=_channel_streams)
    return client


def _dest_client_for_channels(
    *, existing_channels=None, dest_streams=None
) -> AsyncMock:
    """An empty-config dest-B that also answers the channel/stream getters."""
    client = _empty_dest_client()
    client.get_channels = AsyncMock(
        return_value={
            "results": existing_channels or [],
            "count": len(existing_channels or []),
        }
    )
    client.get_streams = AsyncMock(
        return_value={"results": dest_streams or [], "count": len(dest_streams or [])}
    )
    created = {"n": 700}

    async def _create_channel(payload):
        created["n"] += 1
        return {"id": created["n"], **payload}

    client.create_channel = AsyncMock(side_effect=_create_channel)
    client.update_channel = AsyncMock(return_value={"success": True})
    client.update_profile_channel = AsyncMock(return_value={"success": True})
    return client


@pytest.mark.asyncio
async def test_sync_channels_converge_on_empty_b(tmp_path):
    """Convergence: apply pushes A's channels onto an empty B (created)."""
    src = _source_client_with_channels(
        channels=[{"id": 5, "name": "CNN", "channel_number": 5, "streams": []}],
        channel_streams={5: []},
    )
    dest = _dest_client_for_channels()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.create_channel.assert_awaited()
    assert report.category(EntityType.CHANNEL).created == 1


@pytest.mark.asyncio
async def test_sync_channels_idempotent_non_colliding(tmp_path):
    """Idempotency: a re-run against a B that already holds A's channel (same
    non-null number) is a no-op — ALREADY_EXISTS_IDENTICAL, zero creates."""
    src = _source_client_with_channels(
        channels=[{"id": 5, "name": "CNN", "channel_number": 5, "streams": []}],
        channel_streams={5: []},
    )
    dest = _dest_client_for_channels(
        existing_channels=[{"id": 88, "name": "CNN", "channel_number": 5}]
    )
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.create_channel.assert_not_called()
    cat = report.category(EntityType.CHANNEL)
    assert cat.created == 0
    assert cat.skipped == 1


@pytest.mark.asyncio
async def test_sync_channels_null_number_collision_is_conflict(tmp_path):
    """The load-bearing floor (ruling 1a) end-to-end: a source channel (name,
    null) matching a dest channel (name, null) surfaces as a CONFLICT in the
    sync report — never a silent skip."""
    src = _source_client_with_channels(
        channels=[{"id": 5, "name": "CNN", "streams": []}],  # no channel_number
        channel_streams={5: []},
    )
    dest = _dest_client_for_channels(
        existing_channels=[{"id": 88, "name": "CNN"}]  # no channel_number
    )
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.create_channel.assert_not_called()
    cat = report.category(EntityType.CHANNEL)
    assert cat.failed == 1
    from dbas.restore_contracts import FailureReason
    assert cat.failure_details[0].reason == FailureReason.CONFLICT


@pytest.mark.asyncio
async def test_sync_threads_fuzzy_flag_off_by_default(tmp_path):
    """Ruling 1b seam: a target with fuzzy_stream_matching off (default) floors
    the stream matcher — a fuzzy-only source stream is NOT attached (routes to the
    custom-stream fallback as an orphan)."""
    # Source channel carries an embedded stream that only fuzzy-matches B's stream.
    src = _source_client_with_channels(
        channels=[{"id": 5, "name": "ESPN", "channel_number": 7, "streams": []}],
        channel_streams={
            5: [{"id": 1, "name": "ESPN HD East", "url": "http://a/old"}]
        },
    )
    dest = _dest_client_for_channels(
        dest_streams=[
            {"id": 9001, "name": "ESPN East HD", "url": "http://b/x", "m3u_account": 99}
        ]
    )
    target = _sync_target()
    target.fuzzy_stream_matching = False

    synth = AsyncMock()
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None), \
         patch("dbas.importers.channels.synthesize_custom_streams", synth):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    # Floored: no fuzzy attach (zero updated streams); the orphan went to fallback.
    assert report.category(EntityType.STREAM).updated == 0
    synth.assert_awaited()


@pytest.mark.asyncio
async def test_sync_fuzzy_opt_in_attaches_low_confidence(tmp_path):
    """Ruling 1b seam: a target with fuzzy_stream_matching ON attaches a fuzzy
    stream hit but flags it LOW-CONFIDENCE in the report notes (not silent)."""
    src = _source_client_with_channels(
        channels=[{"id": 5, "name": "ESPN", "channel_number": 7, "streams": []}],
        channel_streams={
            5: [{"id": 1, "name": "ESPN HD East", "url": "http://a/old"}]
        },
    )
    dest = _dest_client_for_channels(
        dest_streams=[
            {"id": 9001, "name": "ESPN East HD", "url": "http://b/x", "m3u_account": 99}
        ]
    )
    target = _sync_target()
    target.fuzzy_stream_matching = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    assert report.category(EntityType.STREAM).updated == 1
    assert any("low-confidence stream match (fuzzy)" in n for n in report.notes)


# ---------------------------------------------------------------------------
# LOGOS sync (bead 7ipq2.1) — per-target OPT-IN (ADR-013 S9: logos are NOT in
# the unconditional per-cycle set), never destructive, metadata-only plan (D8
# streaming: bytes hydrate lazily per-logo at import time, misses only).
# ---------------------------------------------------------------------------


def _seed_logo_files(config_dir, files=("cnn.png", "espn.png")):
    """Create real logo files under <config_dir>/uploads/logos (a valid PNG)."""
    import base64 as _b64mod

    png = _b64mod.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    logos_dir = config_dir / "uploads" / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    for name in files:
        (logos_dir / name).write_bytes(png)
    return png


def _source_client_with_logos() -> MagicMock:
    """A source client whose Dispatcharr logo records correlate cnn.png."""
    client = _source_client()
    client.get_all_logos_paginated = AsyncMock(
        return_value=[
            {"id": 77, "name": "CNN Logo", "url": "http://a/data/logos/cnn.png"},
        ]
    )
    return client


def _dest_client_with_logos(*, dest_logos=None) -> AsyncMock:
    """An empty-config dest-B that answers the logo surface."""
    client = _dest_client_for_channels()
    client.get_all_logos_paginated = AsyncMock(return_value=dest_logos or [])
    counter = {"n": 9000}

    async def _upload(name, filename, content, content_type):
        counter["n"] += 1
        return {"id": counter["n"], "name": name}

    client.upload_logo_file = AsyncMock(side_effect=_upload)
    client.bulk_delete_logos = AsyncMock(return_value={"deleted": 0})
    return client


def test_logo_category_constant_is_opt_in_and_disjoint():
    """Logos are an OPT-IN category set — never in the unconditional per-cycle
    set (ADR-013 S9) and never overlapping the never-sync set (D3)."""
    from tasks.dbas_sync_engine import (
        SYNC_ALL_CATEGORIES,
        SYNC_LOGO_CATEGORIES,
    )

    assert SYNC_LOGO_CATEGORIES == frozenset({"logos"})
    assert SYNC_LOGO_CATEGORIES.isdisjoint(SYNC_ALL_CATEGORIES)
    assert SYNC_LOGO_CATEGORIES.isdisjoint(SYNC_NEVER_CATEGORIES)


def test_sync_registry_carries_channel_and_logo_steps_once():
    """kxcjf parity pin: the sync path has ONE registry serving BOTH dry-run
    and apply (run_sync threads the same steps list either way), and that
    registry ends CHANNEL -> LOGO (after every config dependency). A future
    edit cannot add the logo step to one mode and not the other — there is
    only one list to edit."""
    from tasks.dbas_sync_engine import sync_config_importer_steps

    steps = sync_config_importer_steps()
    order = [s.entity_type for s in steps]
    assert order[-2:] == [EntityType.CHANNEL, EntityType.LOGO]
    assert order.count(EntityType.LOGO) == 1
    assert EntityType.USER not in order


@pytest.mark.asyncio
async def test_plan_excludes_logos_unless_opted_in(tmp_path):
    """Default (include_logos=False): no LOGO category even when source logo
    files exist — the existing default-plan pin stays true."""
    _seed_logo_files(tmp_path)
    with patch.object(backup_mod, "get_client", return_value=_source_client_with_logos()), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan()
    assert plan.category(EntityType.LOGO) is None


@pytest.mark.asyncio
async def test_plan_opt_in_appends_metadata_only_logo_category_last(tmp_path):
    """include_logos=True appends the LOGO category LAST, records are
    METADATA-ONLY (no content_b64 in the plan — the D8 pin) and carry the
    source-correlation id when the basename joins a Dispatcharr logo record."""
    _seed_logo_files(tmp_path)
    with patch.object(backup_mod, "get_client", return_value=_source_client_with_logos()), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    assert plan.categories[-1].entity_type == EntityType.LOGO
    logo_cat = plan.category(EntityType.LOGO)
    assert {e["filename"] for e in logo_cat.entities} == {"cnn.png", "espn.png"}
    for entity in logo_cat.entities:
        assert "content_b64" not in entity  # D8: never bytes in the plan.
        assert isinstance(entity.get("size"), int)
    correlated = next(e for e in logo_cat.entities if e["filename"] == "cnn.png")
    assert correlated["id"] == 77
    assert correlated["name"] == "CNN Logo"
    uncorrelated = next(e for e in logo_cat.entities if e["filename"] == "espn.png")
    assert "id" not in uncorrelated or uncorrelated.get("id") is None
    assert uncorrelated["name"] == "espn"  # basename-stem fallback (decoder parity)


@pytest.mark.asyncio
async def test_run_sync_logos_off_by_default_touches_no_logo_surface(tmp_path):
    """sync_logos=False (default): B's logo surface is never even listed."""
    _seed_logo_files(tmp_path)
    src = _source_client_with_logos()
    dest = _dest_client_with_logos()
    target = _sync_target()  # sync_logos=False

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.get_all_logos_paginated.assert_not_called()
    dest.upload_logo_file.assert_not_called()
    cat = report.category(EntityType.LOGO)
    assert cat.created == 0 and cat.would_create == 0


@pytest.mark.asyncio
async def test_run_sync_logos_dry_run_counts_misses_zero_uploads(tmp_path):
    """Opt-in + dry-run: missed logos are counted (would_create) with ZERO
    uploads and ZERO deletes."""
    _seed_logo_files(tmp_path)
    src = _source_client_with_logos()
    dest = _dest_client_with_logos()
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(target, session=MagicMock(), ledger_dir=tmp_path)

    assert report.is_dry_run is True
    dest.upload_logo_file.assert_not_called()
    dest.bulk_delete_logos.assert_not_called()
    assert report.category(EntityType.LOGO).would_create == 2
    # would_create, not a loss: both logos WILL be created on the destination,
    # so neither belongs in the operator-facing logo-miss report (the D9 red
    # banner). See RestoreReport.record_logo_miss for the invariant.
    assert report.logo_misses == 0


@pytest.mark.asyncio
async def test_run_sync_logos_apply_uploads_misses_skips_matches(tmp_path):
    """Opt-in + apply: a B-side match skips (never re-uploaded); a miss uploads
    via the streaming path; the destructive bulk-delete NEVER fires on the sync
    path (code-enforced clear_existing=False)."""
    png = _seed_logo_files(tmp_path)
    src = _source_client_with_logos()
    # B already has the CNN logo (tier-2 name match); espn is a miss.
    dest = _dest_client_with_logos(
        dest_logos=[{"id": 8801, "name": "CNN Logo", "url": "http://b/data/logos/cnn.png"}]
    )
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    assert report.outcome == RestoreOutcome.SUCCESS
    dest.upload_logo_file.assert_awaited_once()
    upload_args = dest.upload_logo_file.await_args.args
    assert upload_args[1] == "espn.png"
    assert upload_args[2] == png  # real file bytes travelled the lazy path
    dest.bulk_delete_logos.assert_not_called()  # NEVER destructive on sync.
    cat = report.category(EntityType.LOGO)
    assert cat.created == 1
    assert cat.skipped == 1


# ---------------------------------------------------------------------------
# DISPATCHARR-HOSTED logo bytes on the sync path (bead …-cfxml).
#
# Dispatcharr is ECM's source of truth for logos: a logo uploaded through ECM's
# own Logo Manager is written to DISPATCHARR's ``/data/logos/``, and ECM's own
# ``/config/uploads/logos/`` holds at most a stale mirror. Bead …-xb58a taught
# the BACKUP builder to fetch those bytes; until this bead the sync gather read
# only ECM's upload directory, so a replica received whatever happened to sit
# there and nothing else.
#
# INVARIANT under test: after a cycle with ``sync_logos`` on, B holds every logo
# A can serve, regardless of whether the bytes live in ECM's upload dir or are
# hosted by Dispatcharr — and the gather never holds more than one logo's bytes
# at a time (D8). The two-stale-files case on the live instance is one example
# of that property, not the specification.
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    """A minimal, magic-byte-valid 1x1 PNG (what the importer will accept)."""
    import base64 as _b64mod

    return _b64mod.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


# (url, id, name) for logos whose bytes ONLY Dispatcharr can supply: the url is
# a path inside Dispatcharr's own volume, not an absolute http(s) CDN address,
# so there is nothing for the restore/sync path to re-point at.
_HOSTED_LOGOS = (
    ("/data/logos/nbc.png", 91, "NBC"),
    ("/data/logos/abc.png", 92, "ABC"),
)


def _source_client_with_hosted_logos(*, hosted=_HOSTED_LOGOS, fetch=None) -> MagicMock:
    """A source-A client whose Dispatcharr logos are HOSTED, with a byte fetch."""
    client = _source_client()
    client.get_all_logos_paginated = AsyncMock(
        return_value=[
            {"id": logo_id, "name": name, "url": url} for url, logo_id, name in hosted
        ]
    )

    async def _default_fetch(logo_id):
        return _png_bytes()

    client.fetch_logo_image = AsyncMock(side_effect=fetch or _default_fetch)
    return client


@pytest.mark.asyncio
async def test_plan_carries_hosted_logos_from_an_empty_upload_dir(tmp_path):
    """The gather reaches Dispatcharr-hosted logos even when ECM's own upload
    directory has nothing in it — the whole point of this bead. Records stay
    METADATA-ONLY: no ``content_b64`` in the plan and NO image fetched at gather
    time (D8 — bytes hydrate lazily, one missed logo at a time)."""
    src = _source_client_with_hosted_logos()
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    cat = plan.category(EntityType.LOGO)
    assert {e["filename"] for e in cat.entities} == {"nbc.png", "abc.png"}
    assert {e["id"] for e in cat.entities} == {91, 92}
    for entity in cat.entities:
        assert "content_b64" not in entity
    src.fetch_logo_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sync_uploads_hosted_logo_bytes_to_the_replica(tmp_path):
    """Opt-in apply: every Dispatcharr-hosted logo reaches B with its real image
    bytes, fetched from A one at a time, and the destructive bulk-delete still
    never fires."""
    png = _png_bytes()
    src = _source_client_with_hosted_logos()
    dest = _dest_client_with_logos()
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    assert report.outcome == RestoreOutcome.SUCCESS
    uploaded = {
        call.args[1]: call.args[2] for call in dest.upload_logo_file.await_args_list
    }
    assert uploaded == {"nbc.png": png, "abc.png": png}
    assert report.category(EntityType.LOGO).created == 2
    dest.bulk_delete_logos.assert_not_called()


@pytest.mark.asyncio
async def test_hosted_bytes_supersede_the_stale_ecm_local_copy(tmp_path):
    """A file in ECM's upload dir that correlates BY BASENAME to a hosted
    Dispatcharr logo is a stale mirror of the authoritative bytes. Exactly one
    record may claim that source id (two would collide through the LOGO remap
    and one would be skipped as ALREADY_EXISTS_IDENTICAL — a claim of sameness
    about bytes that are not the same), and the winner is Dispatcharr's."""
    _seed_logo_files(tmp_path, files=("cnn.png",))
    fresh = _png_bytes() + b"FRESH-DISPATCHARR-BYTES"

    async def _fetch(logo_id):
        return fresh

    src = _source_client_with_hosted_logos(
        hosted=(("/data/logos/cnn.png", 77, "CNN Logo"),), fetch=_fetch
    )
    dest = _dest_client_with_logos()
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.upload_logo_file.assert_awaited_once()
    args = dest.upload_logo_file.await_args.args
    assert args[1] == "cnn.png"  # no id-suffixed collision name
    assert args[2] == fresh  # the AUTHORITATIVE bytes, not the stale local file
    assert report.category(EntityType.LOGO).created == 1


@pytest.mark.asyncio
async def test_hosted_logo_bytes_are_fetched_one_at_a_time(tmp_path):
    """D8: fetch -> upload -> release, repeat. Never fetch-all-then-upload, which
    would hold the whole logo set in memory (the failure mode bead …-drc55 is
    open about on the restore side)."""
    events: list[tuple[str, object]] = []

    async def _fetch(logo_id):
        events.append(("fetch", logo_id))
        return _png_bytes()

    src = _source_client_with_hosted_logos(fetch=_fetch)
    dest = _dest_client_with_logos()

    async def _upload(name, filename, content, content_type):
        events.append(("upload", filename))
        return {"id": 9000 + len(events), "name": name}

    dest.upload_logo_file = AsyncMock(side_effect=_upload)
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    assert events == [
        ("fetch", 91),
        ("upload", "nbc.png"),
        ("fetch", 92),
        ("upload", "abc.png"),
    ]


@pytest.mark.asyncio
async def test_hosted_logo_fetch_budget_bounds_one_unattended_cycle(
    tmp_path, monkeypatch
):
    """Sync runs on a SCHEDULE, unattended, so the byte fetch carries a per-cycle
    wall-clock budget the backup builder does not have (open bead …-sj32h). Once
    it is spent the remaining logos are honest misses this cycle; the next cycle
    re-attempts them (the ones already uploaded now MATCH), so the target still
    converges instead of the tail never syncing at all."""
    monkeypatch.setattr(engine, "_LOGO_FETCH_BUDGET_SECONDS", 0.0)
    src = _source_client_with_hosted_logos()
    dest = _dest_client_with_logos()
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    # The first fetch always runs (the budget starts when it does); the second is
    # refused rather than left to run unbounded.
    assert src.fetch_logo_image.await_count == 1
    assert dest.upload_logo_file.await_count == 1
    cat = report.category(EntityType.LOGO)
    assert cat.created == 1
    assert cat.failed == 1


@pytest.mark.asyncio
async def test_hosted_logo_fetch_cannot_hang_the_cycle(tmp_path, monkeypatch):
    """A per-fetch wall-clock bound: the Dispatcharr client passes ``timeout=None``
    through to httpx, which means NO timeout, so one unanswered logo request
    would otherwise stall a scheduled cycle forever."""
    monkeypatch.setattr(engine, "_LOGO_FETCH_TIMEOUT_SECONDS", 0.01)

    async def _hang(logo_id):
        await asyncio.sleep(30)

    src = _source_client_with_hosted_logos(
        hosted=(("/data/logos/nbc.png", 91, "NBC"),), fetch=_hang
    )
    dest = _dest_client_with_logos()
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await asyncio.wait_for(
            run_sync(
                target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
            ),
            timeout=10,
        )

    dest.upload_logo_file.assert_not_called()
    assert report.category(EntityType.LOGO).failed == 1


# ---------------------------------------------------------------------------
# REMOTE-URL logos on the sync path (bead …-sgrez).
#
# The third storage shape, and the one an XC-sourced instance is made of: the
# provider hands over a tvg-logo ADDRESS and Dispatcharr stores the url, never
# the bytes. Such a logo is neither an ECM-local file nor Dispatcharr-hosted, so
# the gather produced no record for it AT ALL — the LOGO category on the
# documentation environment's source A carried 1 entity out of 60.
#
# COPIED AS A URL, NOT REHOSTED: Dispatcharr's Logo model IS {name, url}, the
# restore importer has re-created this shape from this field since bead …-dfkbn,
# and 59 fetches per unattended cycle would land in bead …-cfxml's 300s budget
# for no fidelity gain — A itself holds only the pointer.
# ---------------------------------------------------------------------------


def _source_client_with_remote_logos(*, logos=None) -> MagicMock:
    """A source-A whose Dispatcharr logos carry ABSOLUTE http(s) addresses."""
    client = _source_client()
    client.get_all_logos_paginated = AsyncMock(
        return_value=list(
            logos
            or [
                {"id": 1, "name": "Meridian News",
                 "url": "http://cdn.northwind.example/logos/meridian.png"},
                {"id": 2, "name": "Capitol Report",
                 "url": "https://cdn.northwind.example/logos/capitol.png"},
            ]
        )
    )
    client.fetch_logo_image = AsyncMock(return_value=None)
    return client


@pytest.mark.asyncio
async def test_plan_carries_remote_url_logos_as_addresses(tmp_path):
    """The gather emits a record per remote logo, carrying the ADDRESS and no
    bytes — and fetches nothing, because there is nothing of ours to fetch."""
    src = _source_client_with_remote_logos()
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    cat = plan.category(EntityType.LOGO)
    assert {e["id"] for e in cat.entities} == {1, 2}
    assert {e["url"] for e in cat.entities} == {
        "http://cdn.northwind.example/logos/meridian.png",
        "https://cdn.northwind.example/logos/capitol.png",
    }
    for entity in cat.entities:
        assert "content_b64" not in entity  # D8: never bytes in the plan.
        assert "filename" not in entity  # nothing to upload -> nothing to name.
    src.fetch_logo_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sync_recreates_remote_logos_by_url_and_uploads_nothing(tmp_path):
    """Apply: B gets a row per remote logo pointing at the SAME address, via the
    create-by-url path. No bytes are fetched from A and none are uploaded to B —
    the cost this shape must not have on an unattended schedule."""
    src = _source_client_with_remote_logos()
    dest = _dest_client_with_logos()
    created: list[dict] = []

    async def _create(data):
        created.append(dict(data))
        return {"id": 8900 + len(created), "name": data.get("name")}

    dest.create_logo = AsyncMock(side_effect=_create)
    target = _sync_target()
    target.sync_logos = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    assert report.outcome == RestoreOutcome.SUCCESS
    assert created == [
        {"name": "Meridian News",
         "url": "http://cdn.northwind.example/logos/meridian.png"},
        {"name": "Capitol Report",
         "url": "https://cdn.northwind.example/logos/capitol.png"},
    ]
    dest.upload_logo_file.assert_not_called()
    src.fetch_logo_image.assert_not_awaited()
    assert report.category(EntityType.LOGO).created == 2
    assert report.logo_misses == 0
    dest.bulk_delete_logos.assert_not_called()


@pytest.mark.asyncio
async def test_a_nameless_remote_logo_gets_a_stable_operator_facing_label(tmp_path):
    """The label reaches B as the created row's NAME, so it can be neither the
    importer's ``<unknown>`` placeholder nor anything derived from the url (a
    url is the thing that carries the credential). The id is stable, so the next
    cycle's tier-2 name match still finds the row this cycle created."""
    src = _source_client_with_remote_logos(
        logos=[{"id": 51, "name": "  ", "url": "http://cdn.test/x.png"}]
    )
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    assert plan.category(EntityType.LOGO).entities[0]["name"] == "logo 51"


@pytest.mark.asyncio
async def test_an_ecm_local_mirror_still_wins_over_the_remote_pointer(tmp_path):
    """A file in ECM's upload dir that correlates BY BASENAME to a remote logo
    holds real bytes for that source id, so it keeps the record — the previously
    shipped slice (bead 7ipq2.1) is untouched. Exactly ONE record may claim a
    source id: two would collide through the LOGO remap and the loser would be
    skipped ALREADY_EXISTS_IDENTICAL, a claim of sameness about images that are
    not the same."""
    _seed_logo_files(tmp_path, files=("cnn.png",))
    src = _source_client_with_logos()  # logo id 77, url http://a/data/logos/cnn.png
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    entities = plan.category(EntityType.LOGO).entities
    assert [e["id"] for e in entities if e.get("id") == 77] == [77]
    correlated = next(e for e in entities if e.get("id") == 77)
    assert correlated["filename"] == "cnn.png"  # the local FILE, not a pointer
    assert "url" not in correlated


# ---------------------------------------------------------------------------
# CREDENTIAL INTERACTION with bead …-msqf7. A logo url is a provider-supplied
# address on the same instances whose STREAM urls were found carrying the
# account's username and password in PATH SEGMENTS. Until 2026-08-22 such a url
# was DROPPED rather than copied, and the logo reported as a named miss.
#
# AMENDED: the PO ruled that provider credentials cross on every cycle (ADR-013
# amendment (b)), so the drop bought nothing — the replica holds the same
# credential now — and cost it its branding. The two tests below are INVERTED
# rather than deleted, because "the address crosses whole" is the property that
# would silently regress if someone re-armed the scrub.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leaky_url",
    [
        # The XC path-segment shape …-msqf7 measured on 1,409,363 real stream
        # urls. SECRET_M3U_PASSWORD is what _source_client's account holds, so
        # the match is literal, not a guess.
        "http://provider.test/live/operator/%s/logos/cnn.png",
        # Percent-encoded: an escape must not be a way through.
        "http://provider.test/live/operator/%s/cnn.png",
    ],
)
async def test_a_path_segment_credential_logo_url_is_copied(tmp_path, leaky_url):
    src = _source_client_with_remote_logos(
        logos=[{"id": 5, "name": "Leaky Logo", "url": leaky_url % SECRET_M3U_PASSWORD}]
    )
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    entity = plan.category(EntityType.LOGO).entities[0]
    assert entity["id"] == 5
    assert entity["name"] == "Leaky Logo"
    # The address crosses WHOLE, so the replica loads the same picture A does.
    assert entity["url"] == leaky_url % SECRET_M3U_PASSWORD


@pytest.mark.asyncio
async def test_a_query_string_credential_logo_url_is_copied(tmp_path):
    """The other carrier shape, same disposition."""
    src = _source_client_with_remote_logos(
        logos=[
            {"id": 6, "name": "Leaky Query Logo",
             "url": "http://provider.test/logo.php?username=u1&password=p1"},
        ]
    )
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    entity = plan.category(EntityType.LOGO).entities[0]
    assert entity["id"] == 6
    assert entity["url"] == "http://provider.test/logo.php?username=u1&password=p1"


@pytest.mark.asyncio
async def test_a_credential_free_address_is_carried_byte_for_byte(tmp_path):
    """The converse, and the reason the rule is a literal match rather than a
    pattern: an ordinary address with structural path words survives intact."""
    src = _source_client_with_remote_logos(
        logos=[
            {"id": 7, "name": "Ordinary Logo",
             "url": "http://cdn.test/live/movie/news/operator-choice.png"},
        ]
    )
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(backup_mod, "CONFIG_DIR", tmp_path):
        plan = await build_live_source_plan(include_logos=True)

    entity = plan.category(EntityType.LOGO).entities[0]
    assert entity["url"] == "http://cdn.test/live/movie/news/operator-choice.png"


# ---------------------------------------------------------------------------
# Persisted sync state (bead 7ipq2.2 — live-validation finding): the DBA-ruled
# sync_targets columns (last_full_sync_at / last_outcome, migration 0024) were
# never stamped by any code path — the operator status surface stayed NULL
# forever. run_sync stamps them post-run when it has a session.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sync_apply_success_stamps_persisted_state(tmp_path):
    """A realized apply stamps last_outcome; a FULL success also stamps
    last_full_sync_at. Committed through the caller's session."""
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()
    target.last_outcome = None
    target.last_full_sync_at = None
    session = MagicMock()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=session, ledger_dir=tmp_path,
        )

    assert report.outcome == RestoreOutcome.SUCCESS
    assert target.last_outcome == "success"
    assert target.last_full_sync_at is not None
    session.commit.assert_called()


@pytest.mark.asyncio
async def test_run_sync_dry_run_does_not_stamp_persisted_state(tmp_path):
    """A dry-run is a plan, not a sync — it must NOT stamp last_outcome or
    last_full_sync_at (the staleness surface would otherwise read a preview
    as B being kept current)."""
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()
    target.last_outcome = None
    target.last_full_sync_at = None
    session = MagicMock()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=False, session=session, ledger_dir=tmp_path,
        )

    assert report.is_dry_run is True
    assert target.last_outcome is None
    assert target.last_full_sync_at is None


@pytest.mark.asyncio
async def test_run_sync_partial_apply_stamps_outcome_but_not_full_sync_time(tmp_path):
    """A mixed apply stamps last_outcome with the tri-state value but NEVER
    advances last_full_sync_at — only a FULL success counts as 'B was current
    as of this time' (mirrors the last-success gauge contract)."""
    src = _source_client_with_duplicate_channel_groups()
    dest = _empty_dest_client()
    target = _sync_target()
    target.last_outcome = None
    target.last_full_sync_at = None
    session = MagicMock()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=session, ledger_dir=tmp_path,
        )

    # The duplicate-name CONFLICT makes the realized outcome non-SUCCESS.
    assert report.outcome != RestoreOutcome.SUCCESS
    assert target.last_outcome == report.outcome.value
    assert target.last_full_sync_at is None


# ---------------------------------------------------------------------------
# The M3U account's own ``user_agent`` FK (bead …-9h6cv).
#
# ``importers/m3u_accounts`` forwarded A's raw ``user_agent`` pk to B, and the
# registry ran M3U_ACCOUNT five steps BEFORE USER_AGENT so the remap namespace
# was empty anyway. Live, B answered
# ``400 {"user_agent": ["Invalid pk \"4\" - object does not exist."]}``; because
# M3U_ACCOUNT is a FATAL failure category the apply rolled back and NOTHING
# synced. Both halves are needed — ordering alone still forwards the raw pk,
# and the remap alone resolves against an empty namespace.
#
# INVARIANT: no importer forwards a source-side FK to the destination without
# resolving it through its remap namespace, on any category, at any ordering.
# ---------------------------------------------------------------------------


def test_sync_registry_imports_user_agents_before_m3u_accounts():
    """An M3U account's ``user_agent`` FK resolves through the USER_AGENT remap
    namespace, so the agents step must precede the accounts step."""
    from tasks.dbas_sync_engine import sync_config_importer_steps

    steps = sync_config_importer_steps()
    agents = _step_index(steps, EntityType.USER_AGENT)
    accounts = _step_index(steps, EntityType.M3U_ACCOUNT)

    assert agents >= 0, "sync registry carries no USER_AGENT step"
    assert accounts >= 0, "sync registry carries no M3U_ACCOUNT step"
    assert agents < accounts, (
        "USER_AGENT must be imported before M3U_ACCOUNT — an account's "
        "user_agent FK resolves through the USER_AGENT remap namespace"
    )


def test_every_registry_imports_user_agents_before_m3u_accounts():
    """Ordering parity across ALL THREE registries fed by the same
    ``_importer_step_builders`` callables — the sync registry, the restore apply
    registry, and the restore dry-run registry. A divergence between two of them
    is exactly how the sibling defect (…-hiacv) arose."""
    from dbas.restore_orchestrator import default_importer_steps, dry_run_importer_steps
    from tasks.dbas_sync_engine import sync_config_importer_steps

    for label, steps in (
        ("sync", sync_config_importer_steps()),
        ("restore-apply", default_importer_steps()),
        ("restore-dry-run", dry_run_importer_steps()),
    ):
        agents = _step_index(steps, EntityType.USER_AGENT)
        accounts = _step_index(steps, EntityType.M3U_ACCOUNT)
        assert agents >= 0, f"{label} registry carries no USER_AGENT step"
        assert accounts >= 0, f"{label} registry carries no M3U_ACCOUNT step"
        assert agents < accounts, (
            f"{label} registry imports USER_AGENT after M3U_ACCOUNT"
        )
        # The …-hiacv relationship still holds in the same list.
        profiles = _step_index(steps, EntityType.STREAM_PROFILE)
        assert profiles >= 0, f"{label} registry carries no STREAM_PROFILE step"
        assert agents < profiles, (
            f"{label} registry imports USER_AGENT after STREAM_PROFILE"
        )


def _source_client_with_custom_agent_on_the_m3u_account() -> MagicMock:
    """Source A: an M3U account whose ``user_agent`` FK points at a custom agent
    — the exact shape that aborted the whole apply with a 400."""
    client = _source_client()
    client.get_user_agents = AsyncMock(
        return_value=[
            {"id": 4, "name": "XDMRU Custom Agent",
             "user_agent": "XDMRU-Probe/1.0"},
        ]
    )
    client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 1, "name": "Provider A", "password": SECRET_M3U_PASSWORD,
             "username": "operator", "user_agent": 4},
        ]
    )
    return client


def _strict_dest_client() -> AsyncMock:
    """Dest B, empty, that VALIDATES the ``user_agent`` FK the way Dispatcharr
    does — an unknown pk is a 400, not a silent accept.

    A permissive mock would let a stale pk through and turn this test green
    against the broken code.
    """
    client = _empty_dest_client()
    client.create_user_agent = AsyncMock(
        return_value={"id": 150, "name": "XDMRU Custom Agent"}
    )
    known_agent_ids = {150}

    async def _create(payload):
        agent = payload.get("user_agent")
        if agent is not None and agent not in known_agent_ids:
            raise RuntimeError(
                'Dispatcharr 400: {"user_agent": ["Invalid pk \\"%s\\" - object '
                'does not exist."]}' % agent
            )
        return {"id": 101, "name": payload.get("name")}

    client.create_m3u_account = AsyncMock(side_effect=_create)
    return client


@pytest.mark.asyncio
async def test_m3u_account_with_a_custom_user_agent_is_created_on_b(tmp_path):
    """The reproduction, as a cycle: an account carrying a custom user agent
    CREATES on B with the FK rewritten to B's agent id — no 400, no rollback."""
    src = _source_client_with_custom_agent_on_the_m3u_account()
    dest = _strict_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    account_cat = report.category(EntityType.M3U_ACCOUNT)
    assert account_cat.created == 1
    # An unresolved FK on this path is a FAILURE (FailureReason), recorded in
    # ``failure_details`` — the M3U create raises and the importer classifies it.
    # Asserting on ``skip_details`` here would be a false green: the broken code
    # records nothing there either.
    assert account_cat.failed == 0, (
        "M3U create failed: %s"
        % [(d.reason, d.message) for d in account_cat.failure_details]
    )
    # The FK on the wire is B's agent id (150), never A's source pk (4).
    payload = dest.create_m3u_account.await_args.args[0]
    assert payload["user_agent"] == 150

    assert report.outcome == RestoreOutcome.SUCCESS


@pytest.mark.asyncio
async def test_a_custom_user_agent_no_longer_rolls_the_whole_cycle_back(tmp_path):
    """Blast-radius pin. The defect was not 'the account is missing its agent',
    it was 'the ENTIRE apply rolls back and nothing syncs'. Every other category
    must reach B on a cycle whose account carries a custom agent."""
    src = _source_client_with_custom_agent_on_the_m3u_account()
    dest = _strict_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.create_epg_source.assert_awaited()
    dest.create_channel_group.assert_awaited()
    dest.create_channel_profile.assert_awaited()
    dest.create_stream_profile.assert_awaited()
    assert not any(
        "rollback" in note.lower() for note in report.notes
    ), f"the cycle rolled back; notes={report.notes}"
    assert report.outcome == RestoreOutcome.SUCCESS


@pytest.mark.asyncio
async def test_account_still_created_when_its_agent_cannot_be_resolved(tmp_path):
    """DECISION (…-9h6cv), stated as a test: an M3U account whose ``user_agent``
    cannot be resolved is created WITHOUT the field rather than skipped
    DEPENDENCY_UNRESOLVED like its stream-profile sibling.

    An M3U account is the ROOT of the Phase-2 chain — EPG sources, groups,
    channels and streams all hang off it — and Dispatcharr falls back to its
    default agent when the field is unset. Skipping the account would cascade a
    whole-tree DEPENDENCY_UNRESOLVED for a field the account works without. The
    degradation is reported in ``report.notes``, never silent."""
    # A's account points at agent 4, but A's own agent list does not carry it
    # (deleted between the two gathers), so the USER_AGENT namespace has no 4.
    src = _source_client()
    src.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 1, "name": "Provider A", "password": SECRET_M3U_PASSWORD,
             "username": "operator", "user_agent": 4},
        ]
    )
    dest = _strict_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    account_cat = report.category(EntityType.M3U_ACCOUNT)
    assert account_cat.created == 1
    assert account_cat.failed == 0
    assert not any(
        d.reason == SkipReason.DEPENDENCY_UNRESOLVED
        for d in account_cat.skip_details
    )
    payload = dest.create_m3u_account.await_args.args[0]
    assert "user_agent" not in payload
    assert any("Provider A" in note for note in report.notes)


# ---------------------------------------------------------------------------
# The M3U account's ``server_group`` FK (bead …-g8tyd) — the whole cycle.
#
# Same shape as the ``user_agent`` defect (…-9h6cv) with no remap namespace to
# resolve through: Dispatcharr 0.28.2 has a ``ServerGroup`` table, but ECM's
# DBAS has no ServerGroup entity category and no ServerGroup importer, so A's
# pk cannot be translated. It is DROPPED instead.
#
# Live on the disposable stack, an account carrying A's ``server_group`` pk 20
# made B answer ``400 {"server_group": ["Invalid pk \"20\" - object does not
# exist."]}``; M3U_ACCOUNT is a FATAL failure category, so the apply rolled back
# (``partial_failed_rolled_back``) and NOTHING synced. The identical payload
# with ``server_group`` removed answered ``201``.
# ---------------------------------------------------------------------------


def _source_client_with_a_server_group_on_the_m3u_account() -> MagicMock:
    """Source A: an M3U account assigned to a ServerGroup — the exact shape that
    aborted the whole apply with a 400.

    pk 20 is deliberately outside the destination's ServerGroup range (the live
    B has no server groups at all, and ECM never creates any). A pk that happened
    to alias an unrelated destination row would produce a FALSE GREEN.
    """
    client = _source_client()
    client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 1, "name": "Provider A", "password": SECRET_M3U_PASSWORD,
             "username": "operator", "server_group": 20},
        ]
    )
    return client


def _server_group_strict_dest_client() -> AsyncMock:
    """Dest B, empty, that VALIDATES the ``server_group`` FK the way Dispatcharr
    does — ANY non-null pk is a 400, because B has no ServerGroup rows and ECM
    has no importer that could create one.

    A permissive mock would let the stale pk through and turn these tests green
    against the broken code.
    """
    client = _empty_dest_client()

    async def _create(payload):
        group = payload.get("server_group")
        if group is not None:
            raise RuntimeError(
                'Dispatcharr 400: {"server_group": ["Invalid pk \\"%s\\" - object '
                'does not exist."]}' % group
            )
        return {"id": 101, "name": payload.get("name")}

    client.create_m3u_account = AsyncMock(side_effect=_create)
    return client


@pytest.mark.asyncio
async def test_m3u_account_in_a_server_group_is_created_on_b(tmp_path):
    """The reproduction, as a cycle: an account assigned to a ServerGroup on A
    CREATES on B with the FK dropped — no 400, no rollback."""
    src = _source_client_with_a_server_group_on_the_m3u_account()
    dest = _server_group_strict_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    account_cat = report.category(EntityType.M3U_ACCOUNT)
    assert account_cat.created == 1
    # An unresolved FK on this path is a FAILURE (FailureReason) recorded in
    # ``failure_details`` — the M3U create raises and the importer classifies it.
    # Asserting on ``skip_details`` here would be a false green: the broken code
    # records nothing there either.
    assert account_cat.failed == 0, (
        "M3U create failed: %s"
        % [(d.reason, d.message) for d in account_cat.failure_details]
    )
    payload = dest.create_m3u_account.await_args.args[0]
    assert "server_group" not in payload, (
        "source pk %r reached the destination" % payload.get("server_group")
    )
    assert report.outcome == RestoreOutcome.SUCCESS


@pytest.mark.asyncio
async def test_a_server_group_no_longer_rolls_the_whole_cycle_back(tmp_path):
    """Blast-radius pin. The defect was not 'the account lost its server group',
    it was 'the ENTIRE apply rolls back and nothing syncs'. Every other category
    must reach B on a cycle whose account is assigned to a server group."""
    src = _source_client_with_a_server_group_on_the_m3u_account()
    dest = _server_group_strict_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    dest.create_epg_source.assert_awaited()
    dest.create_channel_group.assert_awaited()
    dest.create_channel_profile.assert_awaited()
    dest.create_stream_profile.assert_awaited()
    assert not any(
        "rollback" in note.lower() for note in report.notes
    ), f"the cycle rolled back; notes={report.notes}"
    assert report.outcome == RestoreOutcome.SUCCESS
    # The degradation is reported, never silent.
    assert any(
        "Provider A" in note and "server group" in note.lower()
        for note in report.notes
    ), f"no operator note named the degraded account; notes={report.notes}"


# ---------------------------------------------------------------------------
# The channel->logo binding pass is gated on CHANNEL as well as LOGO
# (bead enhancedchannelmanager-xgbjm, guarding against …-lngo5's shape).
# ---------------------------------------------------------------------------


def _logo_step_context(*, channel_selected: bool, logo_selected: bool = True):
    """An ApplyContext carrying one archived logo and one channel that uses it."""
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger
    from dbas.restore_orchestrator import ApplyContext

    plan = ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(
                entity_type=EntityType.CHANNEL,
                entities=[{"id": 90001, "name": "Archived Channel", "logo_id": 90002}],
                selected=channel_selected,
            ),
            PlanCategory(
                entity_type=EntityType.LOGO,
                entities=[{"id": 90002, "name": "Archived Logo"}],
                selected=logo_selected,
            ),
        ],
    )
    return ApplyContext(
        plan=plan,
        client=AsyncMock(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id="test"),
        remap=IdRemapTable(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel_selected, logo_selected, should_run",
    [(True, True, True), (False, True, False), (True, False, False)],
)
async def test_logo_binding_pass_needs_both_categories(
    channel_selected, logo_selected, should_run
):
    """The binding pass runs only when BOTH CHANNEL and LOGO are selected.

    Bead ``…-lngo5`` is an OPEN defect of exactly this shape on the RESTORE
    side: its logo reattach guards on LOGO alone while ``_entities()`` returns
    the archived channels regardless of selection, so a CHANNEL-deselected run
    classifies every archived channel against an EMPTY remap. Wiring a binding
    pass into the sync path without the CHANNEL half of the gate would give that
    defect a second home. The state is not producible through ``run_sync``
    today (``build_live_source_plan`` always appends CHANNEL selected), which is
    precisely why the gate has to be pinned HERE, at the step, rather than
    inferred from a caller that could change.
    """
    from dbas.restore_contracts import ChannelReattachMode
    from tasks.dbas_sync_engine import sync_config_importer_steps

    step = next(
        s for s in sync_config_importer_steps() if s.entity_type == EntityType.LOGO
    )
    ctx = _logo_step_context(
        channel_selected=channel_selected, logo_selected=logo_selected
    )

    with patch.object(engine, "import_logos", new=AsyncMock(return_value=None)), \
         patch.object(engine, "reattach_channel_logos", new=AsyncMock()) as reattach:
        await step.importer(ctx)

    assert reattach.await_count == (1 if should_run else 0)
    if should_run:
        kwargs = reattach.await_args.kwargs
        # Source-wins: a replica's branding is the source's, and the channels
        # are MATCHED rather than created on every cycle after the first.
        assert kwargs["mode"] == ChannelReattachMode.OVERWRITE
        assert kwargs["archive_channels"] == ctx.plan.category(
            EntityType.CHANNEL
        ).entities


# ---------------------------------------------------------------------------
# Per-cycle provider-credential transmission (PO ruling 2026-08-22, ADR-013
# amendment (b) — S13' / INV-5').
#
# WHAT THIS BLOCK REPLACED. Five tests pinned the one-time provisioning design:
# that a cycle wrote no ``sync_provision_credentials`` journal row (INV-5), and
# four that stamped and cleared ``destination_credential_observed_at`` from what
# the cycle observed on B (INV-4). All five asserted properties of controls this
# ruling deleted — the provisioning action, its journal action types, the
# observed marker column and the ``insecure`` refusal it fed. Keeping them
# pointed at absent machinery would have been a guard that reads as coverage and
# enforces nothing, so they are replaced by the properties that ARE now
# load-bearing: the cycle carries the credential, and the cycle SAYS it did.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cycle_writes_exactly_one_audit_row_naming_what_it_carried(tmp_path):
    """S13' / bead ``…-gad2p``'s surviving invariant.

    Under per-cycle transmission this row is the only record of how often a
    secret moved, so it has to exist on every terminal route and it has to name
    the records — labels and FIELD NAMES, never a value.
    """
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None), \
         patch.object(engine.journal, "log_entry") as log_entry:
        await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    rows = [
        call.kwargs for call in log_entry.call_args_list
        if call.kwargs.get("action_type") == "sync_run"
    ]
    assert len(rows) == 1, "a cycle must write exactly one sync_run row"
    after = rows[0]["after_value"]
    assert after["redaction_mode"] == "topology_plus_provider_credentials"
    assert after["provider_credentials_transmitted"] >= 1
    assert after["tls_verified"] is True
    named = " ".join(after["provider_credential_records"])
    assert "password" in named
    # Names only. The audit row records THAT a secret moved; it is never a place
    # the secret itself can be read back.
    assert SECRET_M3U_PASSWORD not in json.dumps(rows[0], default=str)


@pytest.mark.asyncio
async def test_an_aborted_cycle_still_writes_a_row_saying_it_carried_nothing(tmp_path):
    """The routes that send nothing say so, rather than saying nothing.

    Bead ``…-gad2p`` measured two failure routes that terminated with NO audit
    row at all. Those two routes are gone with the action they belonged to; the
    invariant that replaced them — no attempt terminates without a row — is only
    meaningful if the abort paths honour it too.
    """
    target = _sync_target()

    with patch.object(engine, "sync_freshness_reason", return_value="token revoked"), \
         patch.object(engine.journal, "log_entry") as log_entry:
        await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    rows = [
        call.kwargs for call in log_entry.call_args_list
        if call.kwargs.get("action_type") == "sync_run"
    ]
    assert len(rows) == 1
    assert rows[0]["after_value"]["provider_credentials_transmitted"] == 0
    assert rows[0]["after_value"]["provider_credential_records"] == []


@pytest.mark.asyncio
async def test_an_insecure_target_is_warned_not_refused(tmp_path):
    """S7' — the refusal came out; the warning went in, on EVERY cycle.

    The PO removed the 409: "I know the security risks. That's on the user to
    mitigate, not us." What replaces it has to fire on every credential-carrying
    cycle rather than once at setup, because under per-cycle transmission the
    exposure recurs on the schedule.
    """
    src = _source_client()
    dest = _empty_dest_client()
    target = _sync_target()
    target.insecure = True

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None), \
         patch.object(engine.journal, "log_entry") as log_entry:
        report = await run_sync(
            target, confirm_apply=True, session=MagicMock(), ledger_dir=tmp_path,
        )

    # Not refused: the cycle ran and carried the credential.
    assert report.provider_credentials_transmitted >= 1
    warnings = [n for n in report.notes if "TLS verification is DISABLED" in n]
    assert warnings, "an insecure credential-carrying cycle said nothing: %r" % (
        report.notes,
    )
    assert "repeats on the schedule" in warnings[0]
    rows = [
        call.kwargs for call in log_entry.call_args_list
        if call.kwargs.get("action_type") == "sync_run"
    ]
    assert rows[0]["after_value"]["tls_verified"] is False


def test_a_secure_target_produces_no_insecure_warning():
    """The warning must mean something when it fires."""
    from tasks.dbas_sync_engine import insecure_transmission_warning

    secure = _sync_target()
    secure.insecure = False
    assert insecure_transmission_warning(secure, carrying_credentials=True) is None

    insecure = _sync_target()
    insecure.insecure = True
    # ...and a cycle that carried nothing has nothing to warn about either.
    assert insecure_transmission_warning(insecure, carrying_credentials=False) is None
    assert insecure_transmission_warning(insecure, carrying_credentials=True)


@pytest.mark.asyncio
async def test_the_schedules_direct_password_is_written_onto_sd_sources_only(tmp_path):
    """The one credential an operator types, cascading like everything else.

    Driven by ``source_type``, never by a presence check: the SD password is
    write-only upstream, so it is never in the gather, so a presence-driven
    writer would skip every SD source forever.
    """
    from tasks.dbas_sync_engine import _inject_schedules_direct_password

    sections = {
        "epg_sources": [
            {"name": "SD", "source_type": "schedules_direct", "username": "u"},
            {"name": "XMLTV", "source_type": "xmltv", "url": "http://x/g.xml"},
        ]
    }
    _inject_schedules_direct_password(sections, "sd-secret")
    rows = {row["name"]: row for row in sections["epg_sources"]}
    assert rows["SD"]["password"] == "sd-secret"
    assert "password" not in rows["XMLTV"]


def test_no_schedules_direct_password_leaves_the_replicas_alone():
    """An empty value must not CLEAR a working password on the replica.

    Writing ``""`` would take an SD source down on the first cycle after an
    operator upgraded without filling the new field in — a regression caused by
    the absence of input, which is the worst kind to diagnose.
    """
    from tasks.dbas_sync_engine import _inject_schedules_direct_password

    sections = {
        "epg_sources": [{"name": "SD", "source_type": "schedules_direct"}]
    }
    for empty in (None, ""):
        _inject_schedules_direct_password(sections, empty)
        assert "password" not in sections["epg_sources"][0]
