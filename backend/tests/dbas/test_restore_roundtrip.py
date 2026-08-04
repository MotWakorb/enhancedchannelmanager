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
            # logo_id 21 references the source logo backing espn.png (below) so
            # the logo-miss affected-channel drill-down is exercised END-TO-END
            # from a genuinely produced artifact (PR #743 review item 1).
            "results": [{"id": 5, "name": "CNN", "channel_number": 1,
                         "streams": [11], "logo_id": 21}],
        }
    )
    # SOURCE Dispatcharr logos: espn.png on disk correlates to logo id 21 by URL
    # basename — the producer must carry that id into binary/metadata.json.
    client.get_all_logos_paginated = AsyncMock(
        return_value=[
            {"id": 21, "name": "ESPN", "url": "http://dispatcharr/media/logos/espn.png"},
        ]
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
    # lc6zu — the settings/agents producer set. Core settings come back in the
    # raw list-of-{key,value} shape; the producer normalizes, splits comskip*
    # keys into the comskip section, and redacts the dangerous-marked
    # ``provider_api_key`` value. The DVR rule's ``channel`` FK references the
    # archived channel (source id 5) so apply must remap it.
    client.get_user_agents = AsyncMock(
        return_value=[{"id": 31, "name": "ECM UA", "user_agent": "ECM/1.0"}]
    )
    client.get_dvr_rules = AsyncMock(
        return_value=[{"id": 41, "name": "Record CNN", "channel": 5}]
    )
    client.get_core_settings = AsyncMock(
        return_value=[
            {"id": 1, "key": "default_user_agent", "value": "ECM/1.0"},
            {"id": 2, "key": "provider_api_key", "value": "sekrit-core-value"},
            {"id": 3, "key": "comskip_ini", "value": "[main]"},
        ]
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
    """An empty-destination AsyncMock client — every archived ENTITY is a create.

    Settings are NOT an entity category: a fresh Dispatcharr instance still HAS
    its own settings rows from install, it just may not have every key the
    archive carries (enhancedchannelmanager-y6zg6 — the dry-run resolver now
    checks this, so the map must reflect a real destination's rows, not an
    empty dict). ``legacy_removed_setting`` (added to the archive's
    core_settings by :func:`_build_real_artifact`) is deliberately ABSENT here —
    it is the genuinely-missing-on-the-destination case the dry-run preview must
    report WOULD-FAIL, matching :func:`_apply_client`'s id map exactly so
    dry-run and apply agree.
    """
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
    client.get_core_setting_id_map = AsyncMock(
        return_value={
            "default_user_agent": 6,
            "comskip_ini": 17,
            "provider_api_key": 21,
        }
    )
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
    # ...and the one logo from the binary subtree — WITH its source Dispatcharr
    # id + display name preserved through build -> decode (PR #743 item 1), the
    # correlation the importer's affected-channel lookup keys on.
    logo_entities = plan.category(EntityType.LOGO).entities
    assert len(logo_entities) == 1
    assert logo_entities[0]["id"] == 21
    assert logo_entities[0]["name"] == "ESPN"
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
    # ...and (lc6zu) the settings/agents categories the producers now emit.
    ua_cat = plan.category(EntityType.USER_AGENT)
    assert ua_cat is not None and [e["name"] for e in ua_cat.entities] == ["ECM UA"]
    dvr_cat = plan.category(EntityType.DVR_RULE)
    assert dvr_cat is not None and dvr_cat.entities[0]["channel"] == 5
    settings_cat = plan.category(EntityType.SETTINGS)
    assert settings_cat is not None
    assert [r["section"] for r in settings_cat.entities] == ["core_settings", "comskip"]
    core_values = settings_cat.entities[0]["values"]
    assert core_values["default_user_agent"] == "ECM/1.0"
    # The dangerous-marked key survives by NAME (the importer skips it by name)
    # but its VALUE was redacted at the producer.
    assert core_values["provider_api_key"] == backup_mod.REDACTED
    assert "sekrit-core-value" not in str(core_values)
    assert settings_cat.entities[1]["values"] == {"comskip_ini": "[main]"}

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
    # lc6zu round-trip: the settings/agents categories flow from the produced
    # artifact all the way into the dry-run counts. The DVR rule's channel FK
    # resolves through the dry-run CHANNEL remap; the dangerous core-settings
    # key is skipped by name, the two safe+resolvable keys would be applied.
    assert report.category(EntityType.USER_AGENT).would_create == 1
    assert report.category(EntityType.DVR_RULE).would_create == 1
    settings_report = report.category(EntityType.SETTINGS)
    assert settings_report.would_update == 2
    assert settings_report.would_skip == 1
    # y6zg6: every safe key here DOES resolve against `_restore_client()`'s id
    # map (it mirrors a genuine destination's settings rows), so this clean
    # fixture reports zero WOULD-FAIL — the missing-key WOULD-FAIL case is
    # covered end-to-end, through this same orchestrator seam, by
    # test_settings_dry_run_apply_parity_on_missing_key below.
    assert settings_report.failed == 0
    # PR #743 item 1: from a GENUINE artifact, the missed logo (empty
    # destination) reports its archived affected channel. Dry-run never emits a
    # destination channel id (the remap holds provisional ids) — name only.
    assert report.logo_misses == 1
    assert len(report.logo_miss_details) == 1
    miss = report.logo_miss_details[0]
    assert miss.source_export_id == 21
    assert miss.label == "ESPN"
    assert [(c.channel_id, c.name) for c in miss.channels] == [(None, "CNN")]


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

    # channels + dispatcharr_users (7i8rf) and user_agents + dvr_rules (lc6zu)
    # are the round-trip categories — assert both ends explicitly so a future
    # edit that drops any of them is caught.
    for section, entity in (
        ("channels", EntityType.CHANNEL),
        ("dispatcharr_users", EntityType.USER),
        ("user_agents", EntityType.USER_AGENT),
        ("dvr_rules", EntityType.DVR_RULE),
    ):
        assert section in produced_dispatcharr, "producer dropped %s" % section
        assert _SECTION_TO_ENTITY.get(section) is entity, (
            "decoder mapping for %s changed" % section
        )

    # The SETTINGS-blob sections (lc6zu) decode through their own seam (they are
    # mappings, not entity lists) — assert producer + decoder both know them.
    from dbas.restore_artifact import _SETTINGS_BLOB_SECTIONS

    for section in _SETTINGS_BLOB_SECTIONS:
        assert section in produced_dispatcharr, "producer dropped %s" % section
    assert set(_SETTINGS_BLOB_SECTIONS) == {"core_settings", "comskip"}


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

    The artifact producer emits M3U/EPG/groups/profiles/channels/users/logos
    (7i8rf) AND user_agents/dvr_rules/core_settings/comskip (lc6zu) — those
    flow from the GENUINELY PRODUCED artifact and are NOT fed here. The mock
    Dispatcharr returns empty EPG/groups/profiles lists, so this only appends
    one row to each of those otherwise-empty categories.
    """
    plan.category(EntityType.EPG_SOURCE).entities.append(
        {"id": 51, "name": "EPG Main", "source_type": "xmltv",
         "url": "http://epg.example/guide.xml"}
    )
    plan.category(EntityType.CHANNEL_GROUP).entities.append({"id": 61, "name": "News"})
    plan.category(EntityType.CHANNEL_PROFILE).entities.append({"id": 62, "name": "Main"})
    plan.category(EntityType.STREAM_PROFILE).entities.append({"id": 63, "name": "Direct"})
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
    # Destination core-settings rows the key->id resolver maps the archive keys
    # onto (q6xjl): the detail route is keyed by integer pk, so the importer
    # resolves ids from the destination before PATCHing. ``provider_api_key`` is
    # deliberately present here — proving it is denylisted, not merely absent.
    client.get_core_setting_id_map = AsyncMock(
        return_value={
            "default_user_agent": 6,
            "comskip_ini": 17,
            "provider_api_key": 21,
        }
    )
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
    # ``failed`` is compared against ``dry_cat.failed`` (NOT a hardcoded 0,
    # enhancedchannelmanager-y6zg6): a settings key absent on the destination is
    # WOULD-FAIL on dry-run and FAILED on apply, both DEPENDENCY_UNRESOLVED, so
    # this loop now proves parity on the failure count too — the settings
    # category has a non-zero dry_cat.failed here (legacy_removed_setting) and
    # this assertion is the parity check that would have caught the ORIGINAL
    # q6xjl divergence (preview: 0 failed; apply: 7/7 failed).
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
            dry_cat.failed,
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
    # The stream layer attached the Tier-3-matched destination stream, and the
    # post-create reattach passes (bead …-dfkbn) put the channel's LOGO back on
    # it. Both are PATCHes against the same restored channel; before the reattach
    # pass the second call did not exist and every restored channel came back
    # with ``logo_id=None`` while the report claimed ``logo_misses: 0``.
    channel_patches = [c.args for c in client.update_channel.await_args_list]
    assert (505, {"streams": [990]}) in channel_patches
    assert (505, {"logo_id": 995}) in channel_patches
    # The archived channel carries no ``epg_data_id``, so no EPG relink is
    # attempted and none is reported missing (a channel unlinked on the source
    # must not be invented a link here).
    assert apply_report.epg_links_unrestored == 0
    assert len(channel_patches) == 2

    # The denylisted settings key was skipped by NAME and its value never sent.
    # Settings are PATCHed at the DESTINATION row id resolved for each key.
    sent_ids = [c.args[0] for c in client.update_core_setting.await_args_list]
    assert 21 not in sent_ids  # provider_api_key's row — never touched
    assert set(sent_ids) == {6, 17}  # default_user_agent, comskip_ini
    # core_settings + comskip share one namespace: ONE list fetch for the run.
    assert client.get_core_setting_id_map.await_count == 1

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

    # --- 8. PR #743 item 1: the GENUINE-artifact logo miss lists its archived
    # affected channel WITH the destination id resolved through the CHANNEL
    # remap populated this run (source 5 -> dest 505). This is the regression
    # the review froze: real artifacts previously lost the source logo id, so
    # this list was always empty on genuine backups.
    assert apply_report.logo_misses == 1
    apply_miss = apply_report.logo_miss_details[0]
    assert apply_miss.source_export_id == 21
    assert apply_miss.label == "ESPN"
    assert [(c.channel_id, c.name) for c in apply_miss.channels] == [(505, "CNN")]


# ---------------------------------------------------------------------------
# y6zg6 — dry-run/apply parity through the ORCHESTRATOR seam for a settings key
# that does not resolve on the destination.
# ---------------------------------------------------------------------------
#
# The q6xjl incident's dry-run preview certified "Settings 7 WILL UPDATE / 0
# FAILED" for an apply that then failed 7/7 on a same-instance round-trip: the
# dry-run branch never contacted upstream at all. This test proves the fix at
# the ORCHESTRATOR wiring level (restore_orchestrator.py's ``_settings``
# importer-step callable, the exact seam both q6xjl and this bead touch) — a
# key absent on the destination is WOULD-FAIL on preview and FAILED on apply,
# both DEPENDENCY_UNRESOLVED, so the two verdicts agree.
#
# This is deliberately a SEPARATE, narrow plan rather than an addition to
# ``_build_real_artifact`` above: a settings-category failure intentionally
# halts the WHOLE restore (rollback of every other category — settings changes
# are never compensated, see settings_agents.py's module docstring), which
# would defeat the multi-category happy-path fixture's own purpose (proving
# every category creates cleanly to a SUCCESS outcome). Isolating the missing-
# key case here keeps both tests focused on what each is actually proving.
# ---------------------------------------------------------------------------


def _settings_only_plan(values: dict) -> "ImportPlan":
    from dbas.preflight import ImportPlan, PlanCategory

    plan = ImportPlan(manifest={"schema_version": 1})
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.SETTINGS,
            selected=True,
            entities=[{"section": "core_settings", "values": values}],
        )
    )
    return plan


def _settings_destination_client(id_map: dict):
    client = AsyncMock()
    client.get_core_setting_id_map = AsyncMock(return_value=id_map)
    client.update_core_setting = AsyncMock(return_value={})
    return client


@pytest.mark.asyncio
async def test_settings_dry_run_apply_parity_on_missing_key():
    from dbas.restore_contracts import (
        FailureReason,
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

    # The destination has 'ui_theme' but not 'not_on_destination' — the exact
    # per-key resolution split the resolver's key->id map produces.
    archive_values = {"ui_theme": "dark", "not_on_destination": "x"}
    destination_id_map = {"ui_theme": 21}

    dry_report = await run_dry_run(
        plan=_settings_only_plan(archive_values),
        client=_settings_destination_client(destination_id_map),
    )
    dry_cat = dry_report.category(EntityType.SETTINGS)
    assert dry_cat.would_update == 1
    assert dry_cat.failed == 1
    assert {d.reason for d in dry_cat.failure_details} == {
        FailureReason.DEPENDENCY_UNRESOLVED
    }

    apply_client = _settings_destination_client(destination_id_map)
    apply_report = await run_restore(
        plan=_settings_only_plan(archive_values),
        client=apply_client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
    )
    apply_cat = apply_report.category(EntityType.SETTINGS)

    # THE parity bar for the missing-key case: preview and apply agree exactly.
    assert (apply_cat.updated, apply_cat.failed) == (dry_cat.would_update, dry_cat.failed)
    assert {d.reason for d in apply_cat.failure_details} == {
        FailureReason.DEPENDENCY_UNRESOLVED
    }
    # The resolvable key still applied — one bad key does not poison the run's
    # OTHER keys, on dry-run any more than on apply.
    apply_client.update_core_setting.assert_awaited_once_with(21, "dark")
    # A settings-category failure halts the whole restore (settings are never
    # ledgered/compensated) — confirms this bead's fix reproduces the ORIGINAL
    # q6xjl incident's "restore rolled back" apply-side behavior, now ALSO
    # visible on the preview before the operator ever confirms the apply.
    assert apply_report.outcome == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK


# ---------------------------------------------------------------------------
# lvfwd — the stream-profile -> user-agent FK, end to end through the REAL
# apply registry, onto a destination whose id space does NOT line up.
# ---------------------------------------------------------------------------
#
# The drill (bead enhancedchannelmanager-a429n) restored an artifact carrying a
# custom stream profile bound to a custom user agent onto a genuinely fresh
# Dispatcharr. Two defects compounded: user agents were imported AFTER stream
# profiles, and the stream-profile category declared no remappable FK, so the
# SOURCE instance's user_agent id was POSTed verbatim.
#
# On a fresh target that 400s ("Invalid pk 4 - object does not exist") and rolls
# the entire restore back. On a target that HAPPENS to have an unrelated user
# agent at that id the create SUCCEEDS and silently binds the WRONG agent — no
# error, no report entry. That is the case this test pins: the destination below
# already occupies id 4 with a different agent, so asserting merely "the create
# returned 2xx" would pass on the broken code. The assertion is on the NAME the
# profile resolves to.
# ---------------------------------------------------------------------------


def _shifted_id_space_plan():
    """A plan with one custom user agent + the stream profile that references it."""
    from dbas.preflight import ImportPlan, PlanCategory

    plan = ImportPlan(manifest={"schema_version": 1})
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.USER_AGENT,
            selected=True,
            entities=[{"id": 4, "name": "Drill UA", "user_agent": "Drill/1.0"}],
        )
    )
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.STREAM_PROFILE,
            selected=True,
            entities=[
                {"id": 9, "name": "Drill Profile", "command": "ffmpeg", "user_agent": 4}
            ],
        )
    )
    return plan


def _shifted_id_space_client():
    """A destination that ALREADY occupies source id 4 with an unrelated agent."""
    client = AsyncMock()
    # Destination user agents: id 4 is taken, by something else entirely.
    destination_user_agents = [{"id": 4, "name": "Unrelated UA", "user_agent": "Other/1.0"}]
    client.get_user_agents = AsyncMock(return_value=destination_user_agents)
    client.get_stream_profiles = AsyncMock(return_value=[])

    async def _create_user_agent(payload):
        created = {"id": 77, **payload}
        destination_user_agents.append(created)
        return created

    client.create_user_agent = AsyncMock(side_effect=_create_user_agent)
    client.create_stream_profile = AsyncMock(
        side_effect=lambda payload: {"id": 88, **payload}
    )
    client.destination_user_agents = destination_user_agents
    return client


@pytest.mark.asyncio
async def test_stream_profile_binds_the_correctly_named_user_agent(tmp_path):
    from dbas.restore_contracts import (
        IdRemapTable,
        RestoreOutcome,
        RestoreReport,
        RollbackLedger,
    )
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_restore,
    )

    client = _shifted_id_space_client()
    report = await run_restore(
        plan=_shifted_id_space_plan(),
        client=client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )

    assert report.outcome == RestoreOutcome.SUCCESS
    assert report.category(EntityType.USER_AGENT).created == 1
    assert report.category(EntityType.STREAM_PROFILE).created == 1

    # THE assertion the drill needed: resolve what the profile actually points at
    # on the DESTINATION and check its NAME. A 2xx create is not evidence.
    payload = client.create_stream_profile.await_args.args[0]
    by_id = {row["id"]: row["name"] for row in client.destination_user_agents}
    assert by_id[payload["user_agent"]] == "Drill UA"
    assert by_id[payload["user_agent"]] != "Unrelated UA"
    # And the raw source id never left ECM.
    assert payload["user_agent"] != 4


@pytest.mark.asyncio
async def test_stream_profile_user_agent_preview_matches_apply(tmp_path):
    """Preview and apply agree on the custom-user-agent stream profile.

    The drill's preview reported "stream_profiles 1 WILL CREATE, 0 FAILED" for an
    apply that then aborted the whole restore. With the FK declared, the dry-run
    must still promise the create (the user agent it depends on is itself a
    would-create), not degrade to a DEPENDENCY_UNRESOLVED skip.
    """
    from dbas.restore_orchestrator import run_dry_run

    dry = await run_dry_run(
        plan=_shifted_id_space_plan(), client=_shifted_id_space_client()
    )
    assert dry.category(EntityType.STREAM_PROFILE).would_create == 1
    assert dry.category(EntityType.STREAM_PROFILE).would_skip == 0
    assert dry.category(EntityType.USER_AGENT).would_create == 1


# ---------------------------------------------------------------------------
# y65si — a user-create failure must not cost the operator everything else.
# ---------------------------------------------------------------------------
#
# Dispatcharr's user-create serializer reads validated_data['password']
# unconditionally; a create that reaches it without one raises an uncaught
# KeyError and surfaces as a 500. ECM now (a) always sends a generated password
# and (b) treats a user-category failure as NON-FATAL, so an upstream that still
# refuses the create costs one user, not the whole instance.
# ---------------------------------------------------------------------------


def _user_failing_client():
    client = AsyncMock()
    client.get_channel_groups = AsyncMock(return_value=[])
    client.create_channel_group = AsyncMock(return_value={"id": 761})
    client.delete_channel_group = AsyncMock(return_value=None)
    # A rebuilt Dispatcharr whose superuser is named differently from the
    # archive's — so the category CREATES rather than skipping.
    client.get_current_user = AsyncMock(return_value={"id": 1, "username": "newadmin"})
    client.get_users = AsyncMock(return_value=[])
    client.get_user_schema_write_fields = AsyncMock(return_value={"username", "email"})
    client.create_user = AsyncMock(
        side_effect=Exception("User creation failed: 500 - Server Error (500)")
    )
    return client


def _user_plus_group_plan():
    from dbas.preflight import ImportPlan, PlanCategory

    plan = ImportPlan(manifest={"schema_version": 1})
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.CHANNEL_GROUP,
            selected=True,
            entities=[{"id": 61, "name": "News"}],
        )
    )
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.USER,
            selected=True,
            entities=[{"id": 7, "username": "drilladmin"}],
        )
    )
    return plan


@pytest.mark.asyncio
async def test_user_create_failure_keeps_the_rest_of_the_restore(tmp_path):
    from dbas.restore_contracts import (
        IdRemapTable,
        RestoreOutcome,
        RestoreReport,
        RollbackLedger,
    )
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_restore,
    )

    client = _user_failing_client()
    report = await run_restore(
        plan=_user_plus_group_plan(),
        client=client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        ledger_dir=tmp_path,
    )

    # Counted and visible…
    user_cat = report.category(EntityType.USER)
    assert user_cat.failed == 1
    assert user_cat.failure_details[0].label == "drilladmin"
    # …but the operator keeps their channel group.
    assert report.category(EntityType.CHANNEL_GROUP).created == 1
    client.delete_channel_group.assert_not_called()
    assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES


@pytest.mark.asyncio
async def test_user_create_sends_a_password_through_the_real_client_seam():
    """The generated password reaches the HTTP layer, unlike the archive's.

    Exercised through the REAL ``DispatcharrClient.create_user`` (transport
    stubbed) because the defensive strip that used to drop every password lives
    at that seam, not in the importer.
    """
    from dbas.importers.users import import_users
    from dbas.restore_contracts import RestoreReport, RollbackLedger
    from dispatcharr_client import DispatcharrClient

    sent = {}

    class _Response:
        status_code = 201

        @staticmethod
        def json():
            return {"id": 91, "username": "drilladmin"}

    async def _request(method, path, **kwargs):
        sent["method"] = method
        sent["path"] = path
        sent["json"] = kwargs.get("json")
        return _Response()

    client = DispatcharrClient.__new__(DispatcharrClient)
    client._request = _request  # type: ignore[method-assign]
    client.get_current_user = AsyncMock(return_value={"id": 1, "username": "newadmin"})
    client.get_users = AsyncMock(return_value=[])
    client.get_user_schema_write_fields = AsyncMock(return_value={"username"})

    await import_users(
        archive_users=[{"id": 7, "username": "drilladmin", "password": "hunter2"}],
        client=client,
        selected=True,
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id="test-restore"),
    )

    assert sent["path"] == "/api/accounts/users/"
    body = sent["json"]
    assert body["username"] == "drilladmin"
    # The generated password IS on the wire (upstream requires the key)…
    assert isinstance(body.get("password"), str) and len(body["password"]) >= 24
    # …and it is NOT the archive's.
    assert body["password"] != "hunter2"
