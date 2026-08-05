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
    client.fetch_logo_image = AsyncMock(return_value=None)
    # No archived channel here carries an ``epg_data_id``, so the producer's EPG
    # natural-key resolution short-circuits without touching this; present so the
    # fixture stays a faithful client surface.
    client.get_epg_data = AsyncMock(return_value=[])
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
    # The archived logo restores from its bytes, so the preview reports NO loss.
    # It used to report one here, which is the defect the operator saw as
    # "1 logo is missing after this restore" on a restore that lost none (see
    # RestoreReport.record_logo_miss for the invariant). The loss path on a
    # GENUINE artifact, including the source-id -> affected-channel join, is
    # proven by test_unrestorable_logo_reports_its_affected_channels below.
    assert report.logo_misses == 0
    assert report.logo_miss_details == []


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

    # --- 8. The logo restored (uploaded as id 995 and reattached to the channel
    # above), so the operator is told about NO loss. A clean restore that reports
    # a missing logo is the reporting defect, not a safe over-count: it is
    # rendered as a red banner and a "could not be reinstated" summary line.
    assert apply_report.logo_misses == 0
    assert apply_report.logo_miss_details == []


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


# ---------------------------------------------------------------------------
# Dispatcharr-hosted logo bytes, producer -> artifact -> decoder -> importer
# (beads enhancedchannelmanager-xb58a + …-dgnms)
#
# This is the seam the drill broke. The producer emitted logo records with no
# bytes and no ``filename`` key; the decoder passed them through; the importer's
# first validation rejected every one of them. Asserting on either side alone
# could not catch it, so this test crosses the whole seam on a REAL artifact.
# ---------------------------------------------------------------------------

_HOSTED_LOGO = {"id": 13, "name": "Drill Uploaded Logo", "url": "/data/logos/drill-logo.png"}
_REMOTE_LOGO = {"id": 21, "name": "ESPN", "url": "https://cdn.example.com/espn.png"}


async def _build_artifact_with_source_logos(
    tmp_path,
    source_logos,
    logo_images,
    source_channels=None,
    local_logos=None,
    source_epg_data=None,
    epg_data_error=None,
    client_out=None,
):
    """Build a real artifact whose SOURCE Dispatcharr logos are ``source_logos``.

    ``logo_images`` maps a source logo id to the bytes ``fetch_logo_image``
    returns for it. ECM's own uploads dir is left EMPTY, which is what a real
    install looks like: Logo Manager uploads land in Dispatcharr's volume.

    ``source_epg_data`` is the SOURCE instance's guide rows, which the producer
    reads to resolve each channel's EPG link to its natural key (bead …-dfkbn).
    ``epg_data_error`` makes that fetch raise, exercising the fail-soft path.
    ``client_out`` is an out-list the SOURCE client mock is appended to, so a
    caller can assert on the upstream calls the producer actually made.
    """
    config_dir = tmp_path
    journal = config_dir / "journal.db"
    _seed_journal_db(journal)
    (config_dir / "settings.json").write_text("{}")
    if local_logos:
        local_dir = config_dir / "uploads" / "logos"
        local_dir.mkdir(parents=True, exist_ok=True)
        for rel, raw in local_logos.items():
            (local_dir / rel).write_bytes(raw)

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    channels = list(source_channels or [])
    client.get_channels = AsyncMock(
        return_value={"count": len(channels), "next": None, "results": channels}
    )
    client.get_streams = AsyncMock(return_value={"count": 0, "next": None, "results": []})
    client.get_users = AsyncMock(return_value=[])
    client.get_user_agents = AsyncMock(return_value=[])
    client.get_dvr_rules = AsyncMock(return_value=[])
    client.get_core_settings = AsyncMock(return_value=[])
    client.get_all_logos_paginated = AsyncMock(return_value=source_logos)
    client.fetch_logo_image = AsyncMock(side_effect=lambda logo_id: logo_images.get(logo_id))
    if epg_data_error is not None:
        client.get_epg_data = AsyncMock(side_effect=epg_data_error)
    else:
        client.get_epg_data = AsyncMock(return_value=list(source_epg_data or []))
    if client_out is not None:
        client_out.append(client)

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


def _decoded_logo_entities(art):
    with zipfile.ZipFile(art.zip_path, "r") as zf:
        validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf)
    return plan.category(EntityType.LOGO).entities


@pytest.mark.asyncio
async def test_uploaded_logo_round_trips_from_archived_bytes(tmp_path):
    """A Dispatcharr-hosted logo is archived WITH its bytes and restores by UPLOAD.

    Before this, the same logo reached the importer as ``{id, name, url}`` with
    no ``filename``, failed ``_validate_logo`` with "unsafe or empty logo
    filename", and (because a logo failure was fatal) took the whole restore
    down with it.
    """
    import base64

    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    art = await _build_artifact_with_source_logos(
        tmp_path, [_HOSTED_LOGO], {13: png}
    )

    entities = _decoded_logo_entities(art)
    assert len(entities) == 1
    record = entities[0]
    assert record["id"] == 13
    assert record["name"] == "Drill Uploaded Logo"
    assert record["filename"] == "drill-logo.png"
    assert base64.b64decode(record["content_b64"]) == png

    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.upload_logo_file = AsyncMock(return_value={"id": 995})
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()

    await import_logos(
        archive_logos=entities, client=client, selected=True, report=report,
        ledger=RollbackLedger(restore_id="r"), remap=remap,
    )

    client.create_logo.assert_not_awaited()
    client.upload_logo_file.assert_awaited_once()
    name, filename, content, content_type = client.upload_logo_file.await_args.args
    assert name == "Drill Uploaded Logo"
    assert filename == "drill-logo.png"
    assert content == png
    assert content_type == "image/png"
    assert report.category(EntityType.LOGO).failed == 0
    assert remap.resolve(EntityType.LOGO, 13) == 995


@pytest.mark.asyncio
async def test_remote_logo_still_round_trips_by_url_without_archived_bytes(tmp_path):
    """A CDN logo carries no bytes and is re-created from its archived URL."""
    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    art = await _build_artifact_with_source_logos(tmp_path, [_REMOTE_LOGO], {})

    with zipfile.ZipFile(art.zip_path, "r") as zf:
        assert [n for n in zf.namelist() if n.startswith("binary/logos/")] == []

    entities = _decoded_logo_entities(art)
    assert len(entities) == 1
    assert entities[0]["url"] == "https://cdn.example.com/espn.png"
    assert "content_b64" not in entities[0]

    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.create_logo = AsyncMock(return_value={"id": 996})
    report = RestoreReport(is_dry_run=False)

    await import_logos(
        archive_logos=entities, client=client, selected=True, report=report,
        ledger=RollbackLedger(restore_id="r"), remap=IdRemapTable(),
    )

    client.upload_logo_file.assert_not_awaited()
    client.create_logo.assert_awaited_once_with(
        {"name": "ESPN", "url": "https://cdn.example.com/espn.png"}
    )
    assert report.category(EntityType.LOGO).failed == 0


@pytest.mark.asyncio
async def test_preview_of_a_real_artifact_predicts_the_apply_exactly(tmp_path):
    """dgnms: preview and apply agree on a real mixed artifact.

    The drill's preview said 11 of 11 logos would fail; the apply then restored
    10 of them. An operator following the product's own "preview first, always"
    guidance would have aborted a restore that was going to work.
    """
    import base64

    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    unfetchable = {"id": 30, "name": "Gone", "url": "/data/logos/gone.png"}
    art = await _build_artifact_with_source_logos(
        tmp_path, [_REMOTE_LOGO, _HOSTED_LOGO, unfetchable], {13: png},
    )
    entities = _decoded_logo_entities(art)

    def _fresh_client():
        client = AsyncMock()
        client.get_all_logos_paginated = AsyncMock(return_value=[])
        client.upload_logo_file = AsyncMock(return_value={"id": 995})
        client.create_logo = AsyncMock(return_value={"id": 996})
        return client

    dry_report = RestoreReport(is_dry_run=True)
    await import_logos(
        archive_logos=entities, client=_fresh_client(), selected=True,
        report=dry_report, ledger=RollbackLedger(restore_id="d"),
        remap=IdRemapTable(), is_dry_run=True,
    )
    apply_report = RestoreReport(is_dry_run=False)
    await import_logos(
        archive_logos=entities, client=_fresh_client(), selected=True,
        report=apply_report, ledger=RollbackLedger(restore_id="a"),
        remap=IdRemapTable(), is_dry_run=False,
    )

    dry = dry_report.category(EntityType.LOGO)
    applied = apply_report.category(EntityType.LOGO)
    # The hosted logo restores from bytes, the CDN logo from its URL, and only
    # only the genuinely unrestorable one fails, in BOTH runs.
    assert (dry.would_create, dry.failed) == (2, 1)
    assert (applied.created, applied.failed) == (2, 1)
    assert [f.label for f in dry.failure_details] == ["Gone"]
    assert [f.label for f in applied.failure_details] == ["Gone"]


@pytest.mark.asyncio
async def test_unrestorable_logo_reports_its_affected_channels(tmp_path):
    """A GENUINE artifact's logo loss names the channels that lost their artwork.

    Proves the whole chain on real artifact bytes: the producer preserves the
    SOURCE Dispatcharr logo id, the decoder carries it onto the record, and the
    importer joins the archive channels' ``logo_id`` FK against it so the D9
    drill-down can say WHICH channels are affected. Logo 30's fetch fails, which
    is the residual case bead …-xb58a cannot remove (no bytes, no remote URL).
    """
    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    lost = {"id": 30, "name": "Lost Artwork", "url": "/data/logos/lost.png"}
    art = await _build_artifact_with_source_logos(
        tmp_path, [lost], {},
        source_channels=[{"id": 5, "name": "CNN", "channel_number": 1, "logo_id": 30}],
    )

    with zipfile.ZipFile(art.zip_path, "r") as zf:
        validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf)
    logo_entities = plan.category(EntityType.LOGO).entities
    archive_channels = plan.category(EntityType.CHANNEL).entities
    assert [e["id"] for e in logo_entities] == [30]

    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 5, 505)
    report = RestoreReport(is_dry_run=False)

    await import_logos(
        archive_logos=logo_entities, archive_channels=archive_channels,
        client=client, selected=True, report=report,
        ledger=RollbackLedger(restore_id="r"), remap=remap,
    )

    assert report.category(EntityType.LOGO).failed == 1
    assert report.logo_misses == 1
    miss = report.logo_miss_details[0]
    assert miss.source_export_id == 30
    assert miss.label == "Lost Artwork"
    assert [(c.channel_id, c.name) for c in miss.channels] == [(505, "CNN")]


@pytest.mark.asyncio
async def test_backup_reports_logos_degraded_when_bytes_cannot_be_fetched(tmp_path):
    """A backup that could not archive logo bytes is NOT a clean success.

    zt3kf's ratified rule for a partial gather: WARNING, named, never silent.
    Without this the operator's only signal that their backup is missing logo
    payload is a line in the container log.
    """
    art = await _build_artifact_with_source_logos(tmp_path, [_HOSTED_LOGO], {})

    assert "logos" in art.degraded_categories
    assert art.unarchived_logo_bytes == 1


@pytest.mark.asyncio
async def test_backup_with_all_logo_bytes_archived_is_not_degraded(tmp_path):
    """The converse: a complete logo gather stays a clean success."""
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    art = await _build_artifact_with_source_logos(
        tmp_path, [_HOSTED_LOGO], {13: png}
    )

    assert "logos" not in art.degraded_categories
    assert art.unarchived_logo_bytes == 0


@pytest.mark.asyncio
async def test_channel_ends_up_on_the_dispatcharr_bytes_not_a_stale_local_copy(tmp_path):
    """The full BLOCK-2 precondition, carried through the restore side.

    Precondition: ECM's ``/config/uploads/logos/`` holds a STALE ``abc.png``
    (a GIF) whose basename correlates to Dispatcharr logo id 44, whose
    authoritative bytes are a different image (a PNG). A channel references
    logo 44.

    Before the producer fix, both files were archived claiming source id 44:
    the local GIF uploaded first and registered ``remap[LOGO][44]``, the real
    PNG then tier-1 matched through that remap and was skipped as
    ``ALREADY_EXISTS_IDENTICAL`` (a claim of sameness about different bytes),
    and the channel was reattached to the stale image with no failure, no miss
    and nothing in the report.
    """
    import base64

    from dbas.channel_reattach import reattach_channel_logos
    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    stale_gif = b"GIF89a" + b"\x00" * 32
    assert stale_gif != png

    art = await _build_artifact_with_source_logos(
        tmp_path,
        [{"id": 44, "name": "Channel Artwork", "url": "/data/logos/abc.png"}],
        {44: png},
        source_channels=[{"id": 5, "name": "CNN", "channel_number": 1, "logo_id": 44}],
        local_logos={"abc.png": stale_gif},
    )

    with zipfile.ZipFile(art.zip_path, "r") as zf:
        validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf)
    logo_entities = plan.category(EntityType.LOGO).entities
    archive_channels = plan.category(EntityType.CHANNEL).entities

    # The artifact carries ONE record for source id 44, and it is the
    # authoritative copy. Two would be unresolvable on the restore side.
    claiming_44 = [e for e in logo_entities if e.get("id") == 44]
    assert len(claiming_44) == 1
    assert base64.b64decode(claiming_44[0]["content_b64"]) == png

    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.upload_logo_file = AsyncMock(return_value={"id": 995})
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 5, 505)
    report = RestoreReport(is_dry_run=False)

    await import_logos(
        archive_logos=logo_entities, archive_channels=archive_channels,
        client=client, selected=True, report=report,
        ledger=RollbackLedger(restore_id="r"), remap=remap,
    )
    await reattach_channel_logos(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client, report=report, remap=remap,
        archive_channels=archive_channels,
    )

    # Exactly one upload, and it carried DISPATCHARR's bytes.
    client.upload_logo_file.assert_awaited_once()
    uploaded_bytes = client.upload_logo_file.await_args.args[2]
    assert uploaded_bytes == png
    assert uploaded_bytes != stale_gif
    # Nothing was skipped as "already exists": there was no phantom twin.
    cat = report.category(EntityType.LOGO)
    assert (cat.created, cat.skipped, cat.failed) == (1, 0, 0)
    # And the channel points at the logo that carries those bytes.
    client.update_channel.assert_awaited_once_with(505, {"logo_id": 995})
    assert report.logo_misses == 0


# ---------------------------------------------------------------------------
# Channel EPG links, producer -> artifact -> decoder -> importer -> reattach
# (bead enhancedchannelmanager-dfkbn, drill run 2026-08-04-run2)
#
# The half of dfkbn that run 2 still measured failing: all 7 EPG-linked channels
# came back with ``epg_data_id=None`` on BOTH artifact variants. This is a
# producer/consumer DISAGREEMENT. The restore relinks by ``tvg_id`` (correct;
# an EPG row's pk is re-minted when the destination re-downloads its guide),
# while the producer archived channels whose own ``tvg_id`` was null. Neither
# side is wrong on its own, which is exactly why per-layer tests could not catch
# it: these cross the whole seam on a REAL artifact.
# ---------------------------------------------------------------------------


def _decoded_channel_entities(art):
    with zipfile.ZipFile(art.zip_path, "r") as zf:
        validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf)
    return plan.category(EntityType.CHANNEL).entities


def _epg_restore_client(destination_epg_rows):
    """An empty destination whose guide has ALREADY re-downloaded, with its own ids."""
    client = AsyncMock()
    client.get_channels = AsyncMock(return_value={"results": [], "count": 0})
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.create_channel = AsyncMock(return_value={"id": 505})
    client.update_channel = AsyncMock(return_value={})
    client.get_epg_data = AsyncMock(return_value=destination_epg_rows)
    return client


@pytest.mark.asyncio
async def test_epg_link_round_trips_when_the_channel_carries_no_tvg_id(tmp_path):
    """The dfkbn run-2 defect, crossed end to end on a genuinely built artifact.

    The source channel is linked ONLY through ``epg_data_id``, and its own
    ``tvg_id`` is null, which is the shape ECM's own channel PATCH produces and
    which the drill verified empirically on the restored instance. The
    destination's guide carries the SAME programme under a DIFFERENT row id
    (9001 vs the source's 2078), which is why the id itself can never round-trip.

    Before the fix the artifact's channel row was ``epg_data_id=2078,
    tvg_id=None``, ``_tvg_id_index`` had nothing to match, and the link was
    dropped with ``epg_links_unrestored=1``.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.importers.channels import import_channels
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    art = await _build_artifact_with_source_logos(
        tmp_path,
        [],
        {},
        source_channels=[
            {
                "id": 101,
                "name": "FOX News",
                "channel_number": 201,
                "tvg_id": None,
                "epg_data_id": 2078,
            }
        ],
        source_epg_data=[
            {"id": 2078, "tvg_id": "fox.news.us", "name": "FOX News"},
            {"id": 857, "tvg_id": "cnn.us", "name": "CNN"},
        ],
    )

    archive_channels = _decoded_channel_entities(art)
    assert len(archive_channels) == 1
    # The producer stamped the LINK's natural key without inventing a tvg_id for
    # the channel itself: the channel's own field is its own field.
    assert archive_channels[0][ARCHIVE_EPG_TVG_ID_KEY] == "fox.news.us"
    assert archive_channels[0].get("tvg_id") is None

    client = _epg_restore_client(
        [{"id": 9001, "tvg_id": "fox.news.us"}, {"id": 9002, "tvg_id": "cnn.us"}]
    )
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()

    await import_channels(
        archive_channels=archive_channels,
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="r"),
        remap=remap,
    )
    # The create never carries the archive-only key upstream.
    created_payload = client.create_channel.await_args.args[0]
    assert ARCHIVE_EPG_TVG_ID_KEY not in created_payload
    assert "epg_data_id" not in created_payload

    relinked = await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client, report=report, remap=remap,
        archive_channels=archive_channels,
    )

    assert relinked == 1
    assert report.epg_links_unrestored == 0
    patches = [c.args for c in client.update_channel.await_args_list]
    # Relinked to the DESTINATION's row id, not the source's 2078.
    assert (505, {"epg_data_id": 9001}) in patches


@pytest.mark.asyncio
async def test_unresolvable_epg_row_degrades_to_a_counted_named_miss(tmp_path):
    """A link whose guide row cannot be resolved never fails the BACKUP.

    The source guide has no row 2078 (it was deleted between the link and the
    backup). The artifact still builds cleanly, the channels category is not
    degraded, and the loss surfaces on RESTORE as a counted, named miss. That
    is the exact pre-fix behaviour, which is the correct floor to degrade to.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, reattach_epg_links
    from dbas.restore_contracts import IdRemapTable, RestoreReport

    built_with: list = []
    art = await _build_artifact_with_source_logos(
        tmp_path,
        [],
        {},
        source_channels=[
            {"id": 101, "name": "FOX News", "channel_number": 201,
             "tvg_id": None, "epg_data_id": 2078},
        ],
        source_epg_data=[{"id": 857, "tvg_id": "cnn.us"}],
        client_out=built_with,
    )

    # The producer DID try to resolve it and archived the channel anyway; it
    # never guesses or synthesises a tvg_id the source did not have.
    assert built_with[0].get_epg_data.await_count == 1
    assert "channels" not in art.degraded_categories
    archive_channels = _decoded_channel_entities(art)
    assert ARCHIVE_EPG_TVG_ID_KEY not in archive_channels[0]

    client = _epg_restore_client([{"id": 9001, "tvg_id": "fox.news.us"}])
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 505)

    await reattach_epg_links(
        # Predates the mode: no population information, so nothing
        # is preserved and the pass behaves as it always did.
        created_source_ids=None,
        client=client, report=report, remap=remap,
        archive_channels=archive_channels,
    )

    assert report.epg_links_unrestored == 1
    assert report.epg_link_miss_details[0].name == "FOX News"
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_epg_natural_key_resolution_is_one_fetch_not_one_per_channel(tmp_path):
    """A real guide is tens of thousands of rows, so resolve it with ONE fetch.

    The drill's guide carried 14,668 rows. Resolving per channel would issue one
    request per EPG-linked channel; this pins the single indexed fetch, matching
    ``dbas/channel_reattach._tvg_id_index``'s approach and its row cap.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY, EPG_INDEX_MAX_ROWS

    source_channels = [
        {"id": 100 + i, "name": "Channel %d" % i, "channel_number": 200 + i,
         "tvg_id": None, "epg_data_id": 2000 + i}
        for i in range(5)
    ]
    source_epg_data = [
        {"id": 2000 + i, "tvg_id": "chan%d.us" % i} for i in range(5)
    ]

    built_with: list = []
    art = await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=source_channels,
        source_epg_data=source_epg_data,
        client_out=built_with,
    )

    archive_channels = _decoded_channel_entities(art)
    assert sorted(ch[ARCHIVE_EPG_TVG_ID_KEY] for ch in archive_channels) == [
        "chan0.us", "chan1.us", "chan2.us", "chan3.us", "chan4.us"
    ]

    # One bounded fetch for the whole export, under the shared row cap.
    client = built_with[0]
    assert client.get_epg_data.await_count == 1
    assert client.get_epg_data.await_args.kwargs == {
        "max_results": EPG_INDEX_MAX_ROWS
    }


@pytest.mark.asyncio
async def test_backup_survives_an_unreachable_epg_endpoint(tmp_path):
    """The EPG fetch is best-effort: it must never fail or degrade the backup."""
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY

    built_with: list = []
    art = await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=[
            {"id": 101, "name": "FOX News", "channel_number": 201,
             "tvg_id": None, "epg_data_id": 2078},
        ],
        epg_data_error=RuntimeError("upstream down"),
        client_out=built_with,
    )

    # The fetch was attempted and its failure swallowed, not propagated.
    assert built_with[0].get_epg_data.await_count == 1
    assert "channels" not in art.degraded_categories
    archive_channels = _decoded_channel_entities(art)
    # The channel is archived intact, just without the resolved key.
    assert archive_channels[0]["name"] == "FOX News"
    assert ARCHIVE_EPG_TVG_ID_KEY not in archive_channels[0]


# ---------------------------------------------------------------------------
# ChannelReattachMode through the REAL orchestrator seam, dry run vs apply
# (bead dfkbn, PR review W1)
#
# The mode is only useful if the PREVIEW runs under it. Asserting the reattach
# pass in isolation cannot catch a step that forgets to thread the mode through,
# or a dry-run registry that skips the pass entirely — which is exactly what the
# registry used to do. These go through run_dry_run / run_restore.
# ---------------------------------------------------------------------------


def _live_destination_plan(archive_channels):
    """A CHANNELS + LOGO plan whose channels ALL already exist on the destination."""
    from dbas.preflight import ImportPlan, PlanCategory

    plan = ImportPlan(manifest={"schema_version": 1})
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.CHANNEL,
            selected=True,
            entities=list(archive_channels),
        )
    )
    plan.categories.append(
        PlanCategory(entity_type=EntityType.LOGO, selected=True, entities=[])
    )
    return plan


def _live_destination_client(existing_channels, epg_rows):
    """A destination that ALREADY HAS these channels, by (name, channel_number)."""
    client = AsyncMock()
    client.get_channels = AsyncMock(
        return_value={"results": list(existing_channels), "count": len(existing_channels)}
    )
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.get_epg_data = AsyncMock(return_value=list(epg_rows))
    client.update_channel = AsyncMock(return_value={})
    client.update_profile_channel = AsyncMock(return_value={})
    client.create_channel = AsyncMock(return_value={"id": 999})
    return client


_MERGE_ARCHIVE_CHANNELS = [
    {"id": 101, "name": "FOX News", "channel_number": 201,
     "tvg_id": None, "epg_data_id": 2078, "epg_data_tvg_id": "fox.news.us",
     "logo_id": 55},
    {"id": 102, "name": "CNN", "channel_number": 202,
     "tvg_id": None, "epg_data_id": 857, "epg_data_tvg_id": "cnn.us",
     "logo_id": 56},
]
_MERGE_EXISTING = [
    {"id": 901, "name": "FOX News", "channel_number": 201},
    {"id": 902, "name": "CNN", "channel_number": 202},
]
_MERGE_EPG_ROWS = [
    {"id": 4001, "tvg_id": "fox.news.us"},
    {"id": 4002, "tvg_id": "cnn.us"},
]


async def _dry_and_apply(mode):
    """Run the same plan through run_dry_run and run_restore under ``mode``."""
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_dry_run,
        run_restore,
    )

    dry_client = _live_destination_client(_MERGE_EXISTING, _MERGE_EPG_ROWS)
    dry = await run_dry_run(
        plan=_live_destination_plan(_MERGE_ARCHIVE_CHANNELS),
        client=dry_client,
        channel_reattach_mode=mode,
    )

    apply_client = _live_destination_client(_MERGE_EXISTING, _MERGE_EPG_ROWS)
    applied = await run_restore(
        plan=_live_destination_plan(_MERGE_ARCHIVE_CHANNELS),
        client=apply_client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        channel_reattach_mode=mode,
    )
    return dry, dry_client, applied, apply_client


@pytest.mark.asyncio
async def test_preserve_dry_run_and_apply_agree_and_touch_nothing():
    """PRESERVE, through the real registry: the preview predicts, the apply obeys."""
    from dbas.restore_contracts import ChannelReattachMode

    dry, dry_client, applied, apply_client = await _dry_and_apply(
        ChannelReattachMode.PRESERVE
    )

    # Both channels matched as already-existing, so neither was created.
    assert dry.category(EntityType.CHANNEL).would_skip == 2
    assert applied.category(EntityType.CHANNEL).skipped == 2

    for report in (dry, applied):
        assert report.epg_link_reattach.mode == ChannelReattachMode.PRESERVE
        assert report.epg_link_reattach.preserved_channels == 2
        assert report.epg_link_reattach.existing_channels == 0
        assert report.logo_reattach.preserved_channels == 2
        assert report.logo_reattach.existing_channels == 0
        # A preserved reference is NOT a loss.
        assert report.epg_links_unrestored == 0
        assert report.logo_misses == 0

    # THE parity bar: preview and apply agree on both splits.
    assert (
        applied.epg_link_reattach.preserved_channels,
        applied.epg_link_reattach.existing_channels,
        applied.logo_reattach.preserved_channels,
        applied.logo_reattach.existing_channels,
    ) == (
        dry.epg_link_reattach.preserved_channels,
        dry.epg_link_reattach.existing_channels,
        dry.logo_reattach.preserved_channels,
        dry.logo_reattach.existing_channels,
    )
    # Neither run touched the operator's live channels.
    dry_client.update_channel.assert_not_awaited()
    apply_client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_overwrite_dry_run_predicts_exactly_what_the_apply_overwrites():
    """OVERWRITE: the preview names the same channels the apply then PATCHes."""
    from dbas.restore_contracts import ChannelReattachMode

    dry, dry_client, applied, apply_client = await _dry_and_apply(
        ChannelReattachMode.OVERWRITE
    )

    for report in (dry, applied):
        assert report.epg_link_reattach.mode == ChannelReattachMode.OVERWRITE
        assert report.epg_link_reattach.existing_channels == 2
        assert sorted(report.epg_link_reattach.existing_channels_named) == [
            "CNN", "FOX News"
        ]
        assert report.epg_link_reattach.preserved_channels == 0

    # Parity on the destructive count, which is the number the operator decides on.
    assert (
        applied.epg_link_reattach.existing_channels
        == dry.epg_link_reattach.existing_channels
    )
    assert (
        sorted(applied.epg_link_reattach.existing_channels_named)
        == sorted(dry.epg_link_reattach.existing_channels_named)
    )

    # The DRY RUN still mutated nothing...
    dry_client.update_channel.assert_not_awaited()
    # ...while the apply relinked both pre-existing channels to the DESTINATION's
    # own guide rows (901/902 are the destination ids, 4001/4002 its EPG rows).
    patches = [c.args for c in apply_client.update_channel.await_args_list]
    assert (901, {"epg_data_id": 4001}) in patches
    assert (902, {"epg_data_id": 4002}) in patches


@pytest.mark.asyncio
async def test_absent_mode_defaults_to_preserve_end_to_end():
    """An OLD client that sends no mode must not silently start overwriting."""
    from dbas.restore_contracts import ChannelReattachMode, IdRemapTable
    from dbas.restore_contracts import RestoreReport, RollbackLedger
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_restore,
    )

    client = _live_destination_client(_MERGE_EXISTING, _MERGE_EPG_ROWS)
    report = await run_restore(
        plan=_live_destination_plan(_MERGE_ARCHIVE_CHANNELS),
        client=client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        # channel_reattach_mode deliberately OMITTED.
    )

    assert report.epg_link_reattach.mode == ChannelReattachMode.PRESERVE
    assert report.epg_link_reattach.preserved_channels == 2
    client.update_channel.assert_not_awaited()


# ---------------------------------------------------------------------------
# Producer edge cases + the unresolved-link count (PR review W2 / N1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_linked_channels_means_no_guide_fetch_at_all(tmp_path):
    """The short-circuit: nothing linked, so the guide is never read (N1).

    A guide read is tens of thousands of rows. An export whose channels carry no
    EPG link at all must not pay for it.
    """
    built_with: list = []
    await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=[
            {"id": 101, "name": "FOX News", "channel_number": 201, "epg_data_id": None},
            {"id": 102, "name": "CNN", "channel_number": 202},
        ],
        source_epg_data=[{"id": 4001, "tvg_id": "fox.news.us"}],
        client_out=built_with,
    )

    built_with[0].get_epg_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_guide_row_with_a_blank_tvg_id_resolves_to_nothing(tmp_path):
    """A guide row whose tvg_id is blank or whitespace is not a natural key (N1).

    Stamping ``""`` would be worse than stamping nothing: the reattach would then
    prefer an empty resolved key over the channel's own real ``tvg_id``.
    """
    from dbas.channel_reattach import ARCHIVE_EPG_TVG_ID_KEY

    art = await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=[
            {"id": 101, "name": "Blank", "channel_number": 201,
             "tvg_id": "own.key.us", "epg_data_id": 2078},
            {"id": 102, "name": "Whitespace", "channel_number": 202,
             "tvg_id": None, "epg_data_id": 2079},
        ],
        source_epg_data=[
            {"id": 2078, "tvg_id": ""},
            {"id": 2079, "tvg_id": "   "},
        ],
    )

    channels = {ch["name"]: ch for ch in _decoded_channel_entities(art)}
    assert ARCHIVE_EPG_TVG_ID_KEY not in channels["Blank"]
    assert ARCHIVE_EPG_TVG_ID_KEY not in channels["Whitespace"]
    # The channel's OWN tvg_id is untouched and still available to fall back on.
    assert channels["Blank"]["tvg_id"] == "own.key.us"


@pytest.mark.asyncio
async def test_resolved_key_matches_the_destination_case_insensitively(tmp_path):
    """XMLTV feeds are inconsistent about case; the match must not be (N1)."""
    from dbas.channel_reattach import reattach_epg_links
    from dbas.restore_contracts import ChannelReattachMode, IdRemapTable, RestoreReport

    art = await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=[
            {"id": 101, "name": "FOX News", "channel_number": 201,
             "tvg_id": None, "epg_data_id": 2078},
        ],
        source_epg_data=[{"id": 2078, "tvg_id": "  FOX.News.US  "}],
    )

    archive_channels = _decoded_channel_entities(art)
    # Trimmed at the producer, but the CASE is preserved as the source had it.
    assert archive_channels[0]["epg_data_tvg_id"] == "FOX.News.US"

    client = _epg_restore_client([{"id": 9001, "tvg_id": "fox.news.us"}])
    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 505)

    relinked = await reattach_epg_links(
        client=client, report=report, remap=remap,
        archive_channels=archive_channels,
        mode=ChannelReattachMode.OVERWRITE,
        created_source_ids={101},
    )

    assert relinked == 1
    client.update_channel.assert_awaited_once_with(505, {"epg_data_id": 9001})


@pytest.mark.asyncio
async def test_backup_counts_the_links_it_could_not_resolve(tmp_path):
    """A dangling epg_data_id is VISIBLE on the artifact, not just in a log (W2)."""
    art = await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=[
            {"id": 101, "name": "Resolvable", "channel_number": 201,
             "epg_data_id": 2078},
            {"id": 102, "name": "Dangling", "channel_number": 202,
             "epg_data_id": 9999},
            {"id": 103, "name": "Also Dangling", "channel_number": 203,
             "epg_data_id": 8888},
            {"id": 104, "name": "Never Linked", "channel_number": 204},
        ],
        source_epg_data=[{"id": 2078, "tvg_id": "fox.news.us"}],
    )

    assert art.unresolved_epg_links == 2
    # INFORMATIONAL, not a warning: a dangling FK is common and unactionable, so
    # it must not spend the badge that a failed category fetch depends on.
    assert "channels" not in art.degraded_categories
    assert art.epg_index_truncated is False


@pytest.mark.asyncio
async def test_a_clean_backup_reports_zero_unresolved_links(tmp_path):
    art = await _build_artifact_with_source_logos(
        tmp_path, [], {},
        source_channels=[
            {"id": 101, "name": "FOX News", "channel_number": 201,
             "epg_data_id": 2078},
        ],
        source_epg_data=[{"id": 2078, "tvg_id": "fox.news.us"}],
    )
    assert art.unresolved_epg_links == 0
    assert art.epg_index_truncated is False


@pytest.mark.asyncio
async def test_a_truncated_guide_read_is_reported_as_truncation(tmp_path):
    """Truncation is a DIFFERENT diagnosis from a dangling reference (W2).

    A read that came back at exactly the ceiling may simply not have SEEN the
    rows those links point at. Reporting 200,000 perfectly good links as dangling
    would be worse than saying nothing.
    """
    with patch.object(backup_mod, "EPG_INDEX_MAX_ROWS", 2):
        art = await _build_artifact_with_source_logos(
            tmp_path, [], {},
            source_channels=[
                {"id": 101, "name": "Seen", "channel_number": 201,
                 "epg_data_id": 1},
                {"id": 102, "name": "Beyond The Ceiling", "channel_number": 202,
                 "epg_data_id": 3},
            ],
            # The client honours the cap the way the real one does.
            source_epg_data=[
                {"id": 1, "tvg_id": "a.us"},
                {"id": 2, "tvg_id": "b.us"},
            ],
        )

    assert art.epg_index_truncated is True
    assert art.unresolved_epg_links == 1


@pytest.mark.asyncio
async def test_the_truncation_flag_does_not_leak_into_the_next_backup(tmp_path):
    """Each build re-arms the per-run flag; a stale True would misreport."""
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()

    with patch.object(backup_mod, "EPG_INDEX_MAX_ROWS", 1):
        first = await _build_artifact_with_source_logos(
            first_dir, [], {},
            source_channels=[{"id": 101, "name": "X", "channel_number": 201,
                              "epg_data_id": 1}],
            source_epg_data=[{"id": 1, "tvg_id": "a.us"}],
        )
    assert first.epg_index_truncated is True

    second = await _build_artifact_with_source_logos(
        second_dir, [], {},
        source_channels=[{"id": 101, "name": "X", "channel_number": 201,
                          "epg_data_id": 1}],
        source_epg_data=[{"id": 1, "tvg_id": "a.us"}],
    )
    assert second.epg_index_truncated is False


@pytest.mark.asyncio
async def test_a_mixed_plan_splits_created_from_pre_existing_through_the_registry():
    """The split is derived through the REAL registry, not handed to the pass.

    One archived channel already exists on the destination and one does not, so
    the two populations are genuinely different and the orchestrator has to tell
    them apart. Asserting the reattach pass with a hand-built created-id set
    cannot catch a channels step that forgets to fill it.
    """
    from dbas.restore_contracts import (
        ChannelReattachMode,
        IdRemapTable,
        RestoreReport,
        RollbackLedger,
    )
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_dry_run,
        run_restore,
    )

    archive = [
        {"id": 101, "name": "Already Here", "channel_number": 201,
         "tvg_id": None, "epg_data_id": 2078, "epg_data_tvg_id": "fox.news.us"},
        {"id": 102, "name": "Brand New", "channel_number": 202,
         "tvg_id": None, "epg_data_id": 857, "epg_data_tvg_id": "cnn.us"},
    ]
    existing = [{"id": 901, "name": "Already Here", "channel_number": 201}]

    dry = await run_dry_run(
        plan=_live_destination_plan(archive),
        client=_live_destination_client(existing, _MERGE_EPG_ROWS),
        channel_reattach_mode=ChannelReattachMode.PRESERVE,
    )

    apply_client = _live_destination_client(existing, _MERGE_EPG_ROWS)
    apply_client.create_channel = AsyncMock(return_value={"id": 902})
    applied = await run_restore(
        plan=_live_destination_plan(archive),
        client=apply_client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        channel_reattach_mode=ChannelReattachMode.PRESERVE,
    )

    for report in (dry, applied):
        pop = report.epg_link_reattach
        # The channel this restore made gets its link; the one the operator
        # already had is left exactly as they have it.
        assert pop.created_channels == 1
        assert pop.preserved_channels == 1
        assert pop.preserved_channels_named == ["Already Here"]
        assert pop.existing_channels == 0

    # Parity on every number the operator reads.
    assert (
        applied.epg_link_reattach.created_channels,
        applied.epg_link_reattach.preserved_channels,
        applied.epg_link_reattach.existing_channels,
    ) == (
        dry.epg_link_reattach.created_channels,
        dry.epg_link_reattach.preserved_channels,
        dry.epg_link_reattach.existing_channels,
    )
    # Only the NEW channel was relinked; 901 was never touched.
    patches = [c.args for c in apply_client.update_channel.await_args_list]
    assert (902, {"epg_data_id": 4002}) in patches
    assert not any(args[0] == 901 for args in patches)


# ---------------------------------------------------------------------------
# The DISASTER-RECOVERY preview (PR review round 2, the blocking finding)
#
# Every dry-run assertion above pre-populates the destination's guide, which is
# why the real failure survived: on a FRESH target the guide is empty at preview
# time, because the restore is what puts the rows there. The EPG-source step
# creates the sources and waits for the download BEFORE channels on an apply; on
# a dry run that wrapper is a pass-through. A preview that resolved against the
# pre-restore guide reported every link as unrestorable and named every channel,
# and then the apply restored all of them.
# ---------------------------------------------------------------------------


def _fresh_destination_client():
    """A genuinely EMPTY target: no channels, and NO guide rows yet."""
    client = AsyncMock()
    client.get_channels = AsyncMock(return_value={"results": [], "count": 0})
    client.get_streams = AsyncMock(return_value={"results": [], "count": 0})
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    # The destination has not downloaded its guide yet. This is the state a
    # preview genuinely sees on a fresh install.
    client.get_epg_data = AsyncMock(return_value=[])
    client.update_channel = AsyncMock(return_value={})
    client.update_profile_channel = AsyncMock(return_value={})
    return client


@pytest.mark.asyncio
async def test_a_dr_preview_does_not_report_every_epg_link_as_lost():
    """The blocking regression: a DR preview must not invent a mass link failure.

    Before the fix this reported ``epg_links_unrestored=2`` and named both
    channels, which at real scale is "200 channels restored without an EPG link"
    on a restore that would have relinked all 200. The CHANGELOG in this same
    tree describes that class as something an operator following the product's
    own preview-first guidance would reasonably abort on.
    """
    from dbas.restore_contracts import ChannelReattachMode
    from dbas.restore_orchestrator import run_dry_run

    client = _fresh_destination_client()
    dry = await run_dry_run(
        plan=_live_destination_plan(_MERGE_ARCHIVE_CHANNELS),
        client=client,
        channel_reattach_mode=ChannelReattachMode.PRESERVE,
    )

    # Both channels would be CREATED by this restore, so both get their link.
    assert dry.category(EntityType.CHANNEL).would_create == 2
    assert dry.epg_links_unrestored == 0
    assert dry.epg_link_miss_details == []
    assert dry.epg_link_reattach.created_channels == 2
    assert dry.epg_link_reattach.existing_channels == 0
    assert dry.epg_link_reattach.preserved_channels == 0
    # And it never even asked: the pre-restore guide is not a prediction.
    client.get_epg_data.assert_not_awaited()
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_dr_preview_matches_the_dr_apply():
    """Parity on the case that matters most, end to end through the registry.

    The apply's destination HAS downloaded its guide by the time channels run
    (the EPG step waits for it), which is precisely why the preview must not read
    the guide it can see.
    """
    from dbas.restore_contracts import (
        ChannelReattachMode,
        IdRemapTable,
        RestoreReport,
        RollbackLedger,
    )
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_dry_run,
        run_restore,
    )

    dry = await run_dry_run(
        plan=_live_destination_plan(_MERGE_ARCHIVE_CHANNELS),
        client=_fresh_destination_client(),
        channel_reattach_mode=ChannelReattachMode.PRESERVE,
    )

    # The apply sees the guide the restore itself caused to be downloaded.
    apply_client = _fresh_destination_client()
    apply_client.get_epg_data = AsyncMock(return_value=_MERGE_EPG_ROWS)
    created = iter([{"id": 701}, {"id": 702}])
    apply_client.create_channel = AsyncMock(side_effect=lambda _p: next(created))

    applied = await run_restore(
        plan=_live_destination_plan(_MERGE_ARCHIVE_CHANNELS),
        client=apply_client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        channel_reattach_mode=ChannelReattachMode.PRESERVE,
    )

    assert applied.category(EntityType.CHANNEL).created == 2
    assert applied.epg_links_unrestored == 0
    # THE parity bar for the DR case, on every number the operator reads.
    assert (
        applied.epg_link_reattach.created_channels,
        applied.epg_link_reattach.existing_channels,
        applied.epg_link_reattach.preserved_channels,
    ) == (
        dry.epg_link_reattach.created_channels,
        dry.epg_link_reattach.existing_channels,
        dry.epg_link_reattach.preserved_channels,
    )
    assert applied.epg_links_unrestored == dry.epg_links_unrestored
    # Both new channels really were relinked to the destination's own rows.
    patches = [c.args for c in apply_client.update_channel.await_args_list]
    assert (701, {"epg_data_id": 4001}) in patches
    assert (702, {"epg_data_id": 4002}) in patches


# ---------------------------------------------------------------------------
# Channels the IMPORTER SKIPPED (review round 3, the blocking finding)
#
# Every parity test above uses channels that either already exist on the
# destination or get created, so none can reach the third population: a channel
# the channels importer skipped. Such a channel has no CHANNEL remap entry and
# is not in created_source_ids, and a dry run that did not resolve the remap
# classified it as "a pre-existing channel whose live guide link would be
# REPLACED" — the destructive counter that gates the abort decision.
# ---------------------------------------------------------------------------


def _skipped_channel_plan(archive_channels):
    """A CHANNELS + LOGO plan whose channels the importer will SKIP."""
    from dbas.preflight import ImportPlan, PlanCategory

    plan = ImportPlan(manifest={"schema_version": 1})
    plan.categories.append(
        PlanCategory(
            entity_type=EntityType.CHANNEL, selected=True,
            entities=list(archive_channels),
        )
    )
    plan.categories.append(
        PlanCategory(entity_type=EntityType.LOGO, selected=True, entities=[])
    )
    return plan


def _ambiguous_collision_client():
    """A destination holding a same-named channel with NO channel_number.

    The ambiguous null-number collision (spike xp6mp ruling 1a): a name match
    with ``channel_number`` null on BOTH sides is not a proven identity, so the
    importer surfaces CONFLICT and creates nothing. The channel therefore has no
    CHANNEL remap entry and is in no created-id set, which is the population the
    dry run used to misclassify.
    """
    client = _fresh_destination_client()
    client.get_channels = AsyncMock(
        return_value={"results": [{"id": 901, "name": "Skipped By Importer"}],
                      "count": 1}
    )
    return client


_SKIPPED_ARCHIVE_CHANNELS = [
    # No channel_number on either side -> ambiguous collision -> CONFLICT.
    {"id": 101, "name": "Skipped By Importer", "tvg_id": None,
     "epg_data_id": 2078, "epg_data_tvg_id": "fox.news.us", "logo_id": 55},
]


@pytest.mark.asyncio
async def test_an_importer_skipped_channel_is_not_a_would_replace():
    """The blocking regression, under the destructive mode.

    Measured before the fix: the dry run reported ``existing=1`` naming
    'Skipped By Importer' while the apply reported ``existing=0`` and one miss.
    At scale that is a red alert naming the operator's channels for a restore
    that touches none of them.
    """
    from dbas.restore_contracts import (
        ChannelReattachMode,
        IdRemapTable,
        RestoreReport,
        RollbackLedger,
    )
    from dbas.restore_orchestrator import (
        default_importer_steps,
        new_restore_id,
        run_dry_run,
        run_restore,
    )

    dry = await run_dry_run(
        plan=_skipped_channel_plan(_SKIPPED_ARCHIVE_CHANNELS),
        client=_ambiguous_collision_client(),
        channel_reattach_mode=ChannelReattachMode.OVERWRITE,
    )

    apply_client = _ambiguous_collision_client()
    apply_client.get_epg_data = AsyncMock(return_value=_MERGE_EPG_ROWS)
    applied = await run_restore(
        plan=_skipped_channel_plan(_SKIPPED_ARCHIVE_CHANNELS),
        client=apply_client,
        steps=default_importer_steps(),
        report=RestoreReport(is_dry_run=False),
        ledger=RollbackLedger(restore_id=new_restore_id()),
        remap=IdRemapTable(),
        confirm_apply=True,
        channel_reattach_mode=ChannelReattachMode.OVERWRITE,
    )

    # The channel really was refused by the importer on both sides.
    assert dry.category(EntityType.CHANNEL).failed == 1
    assert applied.category(EntityType.CHANNEL).failed == 1

    # THE bar: the preview must not claim a destructive replacement.
    assert dry.epg_link_reattach.existing_channels == 0
    assert dry.epg_link_reattach.existing_channels_named == []
    assert dry.epg_link_reattach.created_channels == 0
    assert dry.logo_reattach.existing_channels == 0

    # ...and it agrees with the apply on every split number.
    for attr in ("created_channels", "existing_channels", "preserved_channels"):
        assert getattr(dry.epg_link_reattach, attr) == getattr(
            applied.epg_link_reattach, attr
        ), attr
        assert getattr(dry.logo_reattach, attr) == getattr(
            applied.logo_reattach, attr
        ), attr

    # The preview still records no loss; the apply is the one that counts it.
    assert dry.epg_links_unrestored == 0
    assert applied.epg_links_unrestored == 1
    apply_client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_importer_skipped_channel_is_never_named_as_a_would_replace():
    """Under PRESERVE too, nothing is reported as a destructive replacement.

    This channel IS counted in ``preserved_channels``, deliberately: the restore
    leaves it exactly as it is, the partition runs before any resolution so the
    apply reaches the same number, and the reassuring half over-counting by one
    is harmless. What must never happen is the DESTRUCTIVE half claiming it, or
    the operator seeing one of their channel names in a list of channels this
    restore would overwrite. That is what this pins.
    """
    from dbas.restore_contracts import ChannelReattachMode
    from dbas.restore_orchestrator import run_dry_run

    dry = await run_dry_run(
        plan=_skipped_channel_plan(_SKIPPED_ARCHIVE_CHANNELS),
        client=_ambiguous_collision_client(),
        channel_reattach_mode=ChannelReattachMode.PRESERVE,
    )

    pop = dry.epg_link_reattach
    # The claim that matters: NOTHING is reported as replaced, and no channel of
    # the operator's is named as one this restore would touch.
    assert pop.existing_channels == 0
    assert pop.existing_channels_named == []
    assert pop.created_channels == 0
    assert dry.epg_links_unrestored == 0
    # And the reassuring half, pinned as documented above rather than left to
    # drift: 1, not 0. Changing this to 0 is a real product decision, not a fix.
    assert pop.preserved_channels == 1
