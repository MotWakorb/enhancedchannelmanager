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

from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import EntityType, FailureReason, RestoreOutcome
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
    """The CONFIG set stays topology-config-only — channels are a SEPARATE set
    (kcxie), users/logos are never in either."""
    assert SYNC_CONFIG_CATEGORIES == frozenset(
        {"m3u_accounts", "epg_sources", "channel_groups",
         "channel_profiles", "stream_profiles"}
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
