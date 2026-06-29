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
