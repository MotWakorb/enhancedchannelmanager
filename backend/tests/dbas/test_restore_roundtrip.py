"""Round-trip integration test (bead enhancedchannelmanager-o8tbv).

Proves the full v0.18.0 round-trip seam at mock level:

    build_backup_artifact  ->  validate_artifact_manifest (.17)
        ->  decode_artifact_to_plan  ->  run_dry_run (.16)  ->  RestoreReport

The Dispatcharr client is mocked at module level (no live upstream). The LIVE
round-trip against a real Dispatcharr is the DEFERRED success-signal verification
flagged in the bead — this asserts the wiring produces a coherent RestoreReport
with sane per-category counts.
"""
from __future__ import annotations

import sqlite3
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers import backup as backup_mod
from routers.backup import build_backup_artifact, validate_artifact_manifest
from dbas.restore_artifact import decode_artifact_to_plan
from dbas.restore_contracts import EntityType
from dbas.restore_orchestrator import run_dry_run


def _mock_engine():
    engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (0,)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    engine.connect.return_value = cm
    return engine


def _seed_journal_db(path):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


async def _build_real_artifact(tmp_path):
    """Build a real artifact carrying two M3U accounts + one logo + one channel
    (with embedded streams) + one dispatcharr user — the full 7i8rf producer set."""
    config_dir = tmp_path
    journal = config_dir / "journal.db"
    _seed_journal_db(journal)
    (config_dir / "settings.json").write_text("{}")
    logos = config_dir / "uploads" / "logos"
    logos.mkdir(parents=True)
    # 1x1 PNG so the logos importer's magic-byte check passes downstream.
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    (logos / "espn.png").write_bytes(png)

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 1, "name": "Provider A"},
            {"id": 2, "name": "Provider B"},
        ]
    )
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    # 7i8rf — channels (with embedded stream IDs) + the stream records to enrich
    # them, and a dispatcharr user. The stream url (provider creds) must be
    # dropped by the producer.
    client.get_channels = AsyncMock(
        return_value={
            "count": 1, "next": None,
            "results": [{"id": 5, "name": "CNN", "channel_number": 1, "streams": [11]}],
        }
    )
    client.get_streams = AsyncMock(
        return_value={
            "count": 1, "next": None,
            "results": [{"id": 11, "name": "CNN HD", "m3u_account": 1,
                         "url": "http://prov/secret/cnn.ts"}],
        }
    )
    client.get_users = AsyncMock(
        return_value=[{"id": 7, "username": "alice", "is_superuser": False}]
    )

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    settings = MagicMock()
    settings.model_dump.return_value = {"url": "http://x", "username": "a"}

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
         patch.object(backup_mod, "get_settings", return_value=settings), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", return_value=client):
        return await build_backup_artifact(dest_dir=config_dir)


def _restore_client():
    """An empty-destination AsyncMock client — every archived entity is a create."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    client.get_users = AsyncMock(return_value=[])
    client.get_current_user = AsyncMock(return_value={"id": 1, "username": "op"})
    client.get_user_schema_write_fields = AsyncMock(return_value={"username"})
    client.get_channels = AsyncMock(return_value={"results": [], "count": 0})
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    return client


@pytest.mark.asyncio
async def test_build_then_decode_then_dry_run(tmp_path):
    art = await _build_real_artifact(tmp_path)

    with zipfile.ZipFile(art.zip_path, "r") as zf:
        # .17 validation passes on a freshly built artifact (version + integrity).
        validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf)

    # The decoded plan carries the two M3U accounts the builder gathered...
    assert len(plan.category(EntityType.M3U_ACCOUNT).entities) == 2
    # ...and the one logo from the binary subtree.
    assert len(plan.category(EntityType.LOGO).entities) == 1
    # ...and (7i8rf) the channel + dispatcharr user the producers now emit, which
    # used to decode to EMPTY because no producer wrote those categories.
    channel_cat = plan.category(EntityType.CHANNEL)
    assert channel_cat is not None and len(channel_cat.entities) == 1
    assert channel_cat.entities[0]["id"] == 5
    # The channel embeds its stream by id + safe match fields, NEVER the url.
    embedded = channel_cat.entities[0]["streams"]
    assert [s["id"] for s in embedded] == [11]
    assert "url" not in embedded[0]
    user_cat = plan.category(EntityType.USER)
    assert user_cat is not None and len(user_cat.entities) == 1
    assert user_cat.entities[0]["username"] == "alice"

    report = await run_dry_run(plan=plan, client=_restore_client())

    # Dry-run produced a coherent plan (no mutation, no realized outcome).
    assert report.is_dry_run is True
    assert report.outcome is None
    m3u = report.category(EntityType.M3U_ACCOUNT)
    # Both M3U accounts would be created against the empty destination.
    assert m3u.would_create == 2
    # 7i8rf round-trip: channels + users are now non-empty all the way through to
    # the dry-run report (they would be created against the empty destination).
    assert report.category(EntityType.CHANNEL).would_create == 1
    assert report.category(EntityType.USER).would_create == 1


def test_producer_importer_category_parity():
    """REGRESSION GUARD (7i8rf): every Dispatcharr-managed RESTORABLE_SECTIONS
    key the restore decoder maps to an EntityType MUST be emitted by the producer,
    and every importer-consumed Dispatcharr section MUST be both produced AND
    decodable. This is the parity that, when broken, made restoring channels/users
    a silent no-op against a real backup.
    """
    from routers.backup import RESTORABLE_SECTIONS
    from dbas.restore_artifact import _SECTION_TO_ENTITY

    produced_dispatcharr = {
        k for k, v in RESTORABLE_SECTIONS.items() if v.get("dispatcharr")
    }
    decodable = set(_SECTION_TO_ENTITY)

    # Every Dispatcharr section the decoder knows how to turn into a restore
    # category must be produced by the builder (no decode target without a
    # producer => silent no-op).
    missing_producer = decodable - produced_dispatcharr
    assert not missing_producer, (
        "decoder maps sections with no producer: %s" % sorted(missing_producer)
    )

    # channels + dispatcharr_users are the 7i8rf round-trip categories — assert
    # both ends explicitly so a future edit that drops either is caught.
    for section, entity in (
        ("channels", EntityType.CHANNEL),
        ("dispatcharr_users", EntityType.USER),
    ):
        assert section in produced_dispatcharr, "producer dropped %s" % section
        assert _SECTION_TO_ENTITY.get(section) is entity, (
            "decoder mapping for %s changed" % section
        )


# ---------------------------------------------------------------------------
# kxcjf — REAL-APPLY round trip: confirm_apply=True through the FULL apply
# registry. Proves (a) every category actually MUTATES the (mock) destination,
# (b) per-entity counts land in the RestoreReport, and (c) the key parity bar:
# dry-run counts == subsequent apply counts across ALL registry categories.
# ---------------------------------------------------------------------------

_ALL_REGISTRY_CATEGORIES = (
    EntityType.M3U_ACCOUNT,
    EntityType.EPG_SOURCE,
    EntityType.CHANNEL_GROUP,
    EntityType.CHANNEL_PROFILE,
    EntityType.STREAM_PROFILE,
    EntityType.USER_AGENT,
    EntityType.SETTINGS,
    EntityType.USER,
    EntityType.CHANNEL,
    EntityType.DVR_RULE,
    EntityType.LOGO,
)


def _augment_plan_all_categories(plan):
    """Extend the artifact-decoded plan so EVERY registry category has work.

    The artifact producer emits M3U/EPG/groups/profiles/channels/users/logos;
    the user-agent / DVR-rule / settings sections have no producer yet (their
    importers are wired through the plan seam — bead kxcjf), so this test feeds
    them plan slices directly, plus one row for each empty artifact category.
    """
    from dbas.preflight import PlanCategory

    plan.category(EntityType.EPG_SOURCE).entities.append(
        {"id": 51, "name": "EPG Main", "source_type": "xmltv",
         "url": "http://epg.example/guide.xml"}
    )
    plan.category(EntityType.CHANNEL_GROUP).entities.append({"id": 61, "name": "News"})
    plan.category(EntityType.CHANNEL_PROFILE).entities.append({"id": 62, "name": "Main"})
    plan.category(EntityType.STREAM_PROFILE).entities.append({"id": 63, "name": "Direct"})
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.USER_AGENT,
            entities=[{"id": 31, "name": "ECM UA", "user_agent": "ECM/1.0"}],
        )
    )
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.DVR_RULE,
            # ``channel`` FK points at the archived channel (source id 5); it must
            # remap through the CHANNEL namespace on BOTH dry-run and apply.
            entities=[{"id": 41, "name": "Record CNN", "channel": 5}],
        )
    )
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.SETTINGS,
            entities=[
                # default_user_agent is safe -> applied; the api_key-marked key is
                # denylisted -> skipped (by name only).
                {"section": "core_settings",
                 "values": {"default_user_agent": "ECM/1.0",
                            "provider_api_key": "sekrit"}},
                {"section": "comskip", "values": {"comskip_ini": "[main]"}},
            ],
        )
    )
    return plan


def _apply_client():
    """An empty-destination mock whose creates return destination ids."""
    client = _restore_client()
    client.create_m3u_account = AsyncMock(side_effect=[{"id": 901}, {"id": 902}])
    client.create_epg_source = AsyncMock(return_value={"id": 751})
    # EPG-download wait probes (terminal on first check — zero sleeping).
    client.refresh_epg_source = AsyncMock(return_value={})
    client.get_epg_source = AsyncMock(
        return_value={"id": 751, "status": "success", "epg_count": 10}
    )
    client.create_channel_group = AsyncMock(return_value={"id": 761})
    client.create_channel_profile = AsyncMock(return_value={"id": 762})
    client.create_stream_profile = AsyncMock(return_value={"id": 763})
    client.get_user_agents = AsyncMock(return_value=[])
    client.create_user_agent = AsyncMock(return_value={"id": 771})
    client.get_dvr_rules = AsyncMock(return_value=[])
    client.create_dvr_rule = AsyncMock(return_value={"id": 781})
    client.update_core_setting = AsyncMock(return_value={})
    client.create_user = AsyncMock(return_value={"id": 791})
    client.create_channel = AsyncMock(return_value={"id": 505})
    # One destination stream whose name Tier-3 matches the archived embedded
    # stream, so the attach path exercises update_channel (no fallback synth).
    client.get_streams = AsyncMock(
        return_value={"results": [{"id": 990, "name": "CNN HD"}], "count": 1}
    )
    client.update_channel = AsyncMock(return_value={})
    client.update_profile_channel = AsyncMock(return_value={})
    client.upload_logo_file = AsyncMock(return_value={"id": 995})
    return client


@pytest.mark.asyncio
async def test_real_apply_roundtrip_mutates_every_category_with_dry_run_parity(tmp_path):
    from dbas.restore_contracts import (
        IdRemapTable,
        RestoreOutcome,
        RestoreReport,
        RollbackLedger,
    )
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_dry_run,
        run_restore,
    )

    art = await _build_real_artifact(tmp_path)
    with zipfile.ZipFile(art.zip_path, "r") as zf:
        validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf)
    plan = _augment_plan_all_categories(plan)

    # --- 1. Dry-run first (the default-ON preview the operator sees). --------
    dry_report = await run_dry_run(plan=plan, client=_restore_client())
    assert dry_report.is_dry_run is True

    # Every registry category has non-zero planned work — nothing previews empty.
    for entity_type in _ALL_REGISTRY_CATEGORIES:
        dry_cat = dry_report.category(entity_type)
        planned = dry_cat.would_create + dry_cat.would_update + dry_cat.would_skip
        assert planned > 0, "dry-run planned nothing for %s" % entity_type.value

    # --- 2. Real apply (confirm_apply=True) through the FULL apply registry. --
    client = _apply_client()
    apply_report = await run_restore(
        plan=plan,
        client=client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )
    assert apply_report.is_dry_run is False
    assert apply_report.outcome == RestoreOutcome.SUCCESS

    # --- 3. THE parity bar: dry-run counts == apply counts, ALL categories. ---
    for entity_type in _ALL_REGISTRY_CATEGORIES:
        dry_cat = dry_report.category(entity_type)
        apply_cat = apply_report.category(entity_type)
        assert (
            apply_cat.created,
            apply_cat.updated,
            apply_cat.skipped,
            apply_cat.failed,
        ) == (
            dry_cat.would_create,
            dry_cat.would_update,
            dry_cat.would_skip,
            0,
        ), "dry-run/apply divergence for %s" % entity_type.value

    # --- 4. Every category actually MUTATED the destination (the silent-skip
    # defect this bead closes: pre-kxcjf only M3U/users/channels mutated). -----
    assert client.create_m3u_account.await_count == 2
    client.create_epg_source.assert_awaited_once()
    client.create_channel_group.assert_awaited_once()
    client.create_channel_profile.assert_awaited_once()
    client.create_stream_profile.assert_awaited_once()
    client.create_user_agent.assert_awaited_once()
    client.create_dvr_rule.assert_awaited_once()
    assert client.update_core_setting.await_count == 2  # safe keys only
    client.create_user.assert_awaited_once()
    client.create_channel.assert_awaited_once()
    client.upload_logo_file.assert_awaited_once()
    # The stream layer attached the Tier-3-matched destination stream.
    client.update_channel.assert_awaited_once_with(505, {"streams": [990]})

    # The denylisted settings key was skipped by NAME and its value never sent.
    sent_keys = [c.args[0] for c in client.update_core_setting.await_args_list]
    assert "provider_api_key" not in sent_keys
    assert set(sent_keys) == {"default_user_agent", "comskip_ini"}

    # --- 5. EPG-download wait ran for the CREATED source, before channels. ----
    client.refresh_epg_source.assert_awaited_once_with(751)
    client.get_epg_source.assert_awaited_once_with(751)
    # Terminal on first probe -> no incomplete-download note.
    assert not any("did not finish" in n for n in apply_report.notes)

    # --- 6. The DVR rule's channel FK was remapped source(5) -> dest(505). ----
    dvr_payload = client.create_dvr_rule.await_args.args[0]
    assert dvr_payload["channel"] == 505

    # --- 7. Per-entity counts landed in the report (spot totals). -------------
    assert apply_report.category(EntityType.M3U_ACCOUNT).created == 2
    assert apply_report.category(EntityType.EPG_SOURCE).created == 1
    assert apply_report.category(EntityType.SETTINGS).updated == 2
    assert apply_report.category(EntityType.SETTINGS).skipped == 1
    assert apply_report.category(EntityType.LOGO).created == 1
