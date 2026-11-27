"""Backups ECM produces must stay restorable under the YAML structure budgets."""
from __future__ import annotations

import hashlib
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from dbas.restore_artifact import ArtifactCategoryRefused, decode_artifact_to_plan
from dbas.restore_contracts import EntityType
from routers import backup as backup_mod
from routers.backup import (
    BackupNotRestorableError,
    build_backup_artifact,
    validate_artifact_manifest,
)
from tests.dbas.test_restore_roundtrip import _mock_engine, _seed_journal_db
from tests.tasks.test_dbas_restore_task import _make_task
from yaml_safety import ALIAS_EXPANSION_BUDGET, BoundedSafeLoader, measure_bounded

STREAMS_PER_CHANNEL = 3


def _channel(i: int) -> dict:
    """A Dispatcharr channel record in the shape the list endpoint returns."""
    return {
        "id": i,
        "channel_number": float(i),
        "name": "Channel %d" % i,
        "channel_group_id": i % 40,
        "tvg_id": "channel%d.us" % i,
        "tvc_guide_stationid": None,
        "epg_data_id": None,
        "streams": [i * STREAMS_PER_CHANNEL + s for s in range(STREAMS_PER_CHANNEL)],
        "stream_profile_id": None,
        "uuid": "00000000-0000-4000-8000-%012d" % i,
        "logo_id": None,
        "user_level": 0,
        "auto_created": False,
        "auto_created_by": None,
        "auto_created_by_name": None,
    }


def _stream(sid: int) -> dict:
    return {
        "id": sid,
        "name": "Stream %d" % sid,
        "m3u_account": 1 + sid % 3,
        "url": "http://provider.invalid/user/pass/%d.ts" % sid,
    }


def _client(channel_count: int):
    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[{"id": 1, "name": "Provider"}])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    client.get_channels = AsyncMock(
        return_value={
            "count": channel_count,
            "next": None,
            "results": [_channel(i) for i in range(channel_count)],
        }
    )
    client.get_streams = AsyncMock(
        return_value={
            "count": channel_count * STREAMS_PER_CHANNEL,
            "next": None,
            "results": [
                _stream(s) for s in range(channel_count * STREAMS_PER_CHANNEL)
            ],
        }
    )
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.fetch_logo_image = AsyncMock(return_value=None)
    client.get_epg_data = AsyncMock(return_value=[])
    client.get_users = AsyncMock(return_value=[])
    client.get_user_agents = AsyncMock(return_value=[])
    client.get_dvr_rules = AsyncMock(return_value=[])
    client.get_core_settings = AsyncMock(return_value=[])
    return client


async def _build(tmp_path, channel_count: int):
    journal = tmp_path / "journal.db"
    _seed_journal_db(journal)
    (tmp_path / "settings.json").write_text("{}")
    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    settings = MagicMock()
    settings.model_dump.return_value = {"url": "http://x", "username": "a"}
    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", tmp_path / "settings.json"),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=_mock_engine()),
        patch.object(backup_mod, "get_settings", return_value=settings),
        patch.object(backup_mod, "get_session", return_value=session),
        patch.object(backup_mod, "get_client", return_value=_client(channel_count)),
    ):
        return await build_backup_artifact(dest_dir=tmp_path)


@pytest.mark.asyncio
async def test_large_channel_catalog_backup_round_trips(tmp_path):
    channel_count = 25_000
    artifact = await _build(tmp_path, channel_count)

    with zipfile.ZipFile(artifact.zip_path) as zf:
        composed = measure_bounded(
            zf.read("categories/channels.yaml"), max_nodes=10**9
        )
        # Over the original single 1M budget, which refused this catalog.
        assert composed > 1_000_000
        manifest = validate_artifact_manifest(zf)
        plan = decode_artifact_to_plan(zf, manifest=manifest)

    channels = plan.category(EntityType.CHANNEL).entities
    assert len(channels) == channel_count
    assert len(channels[-1]["streams"]) == STREAMS_PER_CHANNEL
    assert channels[-1]["streams"][0]["name"].startswith("Stream ")
    assert "url" not in channels[-1]["streams"][0]


@pytest.mark.asyncio
async def test_backup_refuses_a_category_it_could_not_restore(tmp_path):
    with (
        patch.object(backup_mod, "BACKUP_MAX_NODES", 3_000),
        pytest.raises(BackupNotRestorableError) as error,
    ):
        await _build(tmp_path, 300)

    message = str(error.value)
    assert "channels" in message
    assert "exceeds the backup size limit" in message
    assert "more than 3,000 YAML nodes" in message
    assert error.value.category == "channels"
    assert not list(tmp_path.glob("*.zip"))


@pytest.mark.asyncio
async def test_backup_task_failure_names_the_refused_category(tmp_path):
    from tasks.dbas_backup import DbasBackupTask
    from task_scheduler import ScheduleConfig, ScheduleType

    task = DbasBackupTask(ScheduleConfig(schedule_type=ScheduleType.MANUAL))

    async def build(**_kwargs):
        with patch.object(backup_mod, "BACKUP_MAX_NODES", 3_000):
            return await _build(tmp_path, 300)

    with (
        patch("tasks.dbas_backup.build_backup_artifact", side_effect=build),
        patch("tasks.dbas_backup.BACKUPS_DIR", tmp_path),
    ):
        result = await task.execute()

    assert result.success is False
    assert "channels" in result.message
    assert "exceeds the backup size limit" in result.message


def _write_artifact(tmp_path, members: dict[str, bytes]):
    file_hashes = {p: hashlib.sha256(b).hexdigest() for p, b in members.items()}
    manifest = {
        "schema_version": 1,
        "app_version": "0.18.2-test",
        "files": [{"path": p, "sha256": h} for p, h in sorted(file_hashes.items())],
    }
    path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, blob in members.items():
            zf.writestr(name, blob)
    return path


def _alias_fanout_category() -> bytes:
    """Under the composed ceiling, over the alias-amplification budget."""
    width = 1_000
    references = ALIAS_EXPANSION_BUDGET // width + 100
    return (
        "dispatcharr:\n  channels:\n"
        "  - &b {name: x, streams: [" + ",".join(["0"] * width) + "]}\n"
        + "".join("  - *b\n" for _ in range(references))
    ).encode()


def test_alias_amplification_refused_on_dbas_decode(tmp_path):
    category = _alias_fanout_category()
    composer = BoundedSafeLoader(category)
    composer.max_nodes = 10**9
    composer.get_single_node()
    assert composer.nodes < 10_000
    composer.dispose()
    path = _write_artifact(tmp_path, {"categories/channels.yaml": category})
    with zipfile.ZipFile(path) as zf, pytest.raises(ArtifactCategoryRefused) as error:
        decode_artifact_to_plan(zf, manifest=validate_artifact_manifest(zf))
    assert error.value.category == "channels"
    assert "exceeds the backup size limit" in str(error.value)
    assert "alias expansion" in str(error.value)


def test_category_node_ceiling_refused_on_dbas_decode(tmp_path):
    category = yaml.safe_dump(
        {"dispatcharr": {"channels": [{"id": i, "name": "c"} for i in range(500)]}}
    ).encode()
    path = _write_artifact(tmp_path, {"categories/channels.yaml": category})
    with (
        patch("dbas.restore_artifact.BACKUP_MAX_NODES", 1_000),
        zipfile.ZipFile(path) as zf,
        pytest.raises(ArtifactCategoryRefused) as error,
    ):
        decode_artifact_to_plan(zf, manifest=validate_artifact_manifest(zf))
    assert "more than 1,000 YAML nodes" in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm_apply", [False, True])
async def test_refused_restore_fails_before_destination_or_orchestrator(
    tmp_path, confirm_apply
):
    path = _write_artifact(
        tmp_path, {"categories/channels.yaml": _alias_fanout_category()}
    )
    task = _make_task(path, confirm_apply=confirm_apply)
    get_client = MagicMock()
    dry_run = AsyncMock()
    apply = AsyncMock()
    with (
        patch("dispatcharr_client.get_client", get_client),
        patch("dbas.restore_orchestrator.run_dry_run", dry_run),
        patch("dbas.restore_orchestrator.run_restore", apply),
    ):
        result = await task.execute()

    assert result.success is False
    assert "channels" in result.message
    assert "exceeds the backup size limit" in result.message
    get_client.assert_not_called()
    dry_run.assert_not_awaited()
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_credential_backup_not_refused_for_aliases_its_final_bytes_lack(tmp_path):
    # The credential path dumps the producer's objects unredacted, so a stream
    # shared by many channels is emitted once and aliased. Redaction copies
    # every container, so the bytes ECM writes carry no aliases at all.
    import yaml_safety

    journal = tmp_path / "journal.db"
    _seed_journal_db(journal)
    (tmp_path / "settings.json").write_text("{}")
    client = _client(0)
    shared = [1_000_000]
    client.get_channels = AsyncMock(
        return_value={
            "count": 300,
            "next": None,
            "results": [dict(_channel(i), streams=shared) for i in range(300)],
        }
    )
    client.get_streams = AsyncMock(
        return_value={"count": 1, "next": None, "results": [_stream(1_000_000)]}
    )
    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    settings = MagicMock()
    settings.model_dump.return_value = {"url": "http://x", "username": "a"}
    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", tmp_path / "settings.json"),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=_mock_engine()),
        patch.object(backup_mod, "get_settings", return_value=settings),
        patch.object(backup_mod, "get_session", return_value=session),
        patch.object(backup_mod, "get_client", return_value=client),
        # 300 aliases of a 7-node stream amplify by about 1,800 nodes.
        patch.object(yaml_safety, "ALIAS_EXPANSION_BUDGET", 100),
        patch.object(yaml_safety.BoundedSafeLoader, "alias_expansion_budget", 100),
    ):
        categories, _degraded, _unresolved = await backup_mod._gather_redacted_categories(
            include_credentials=True
        )

    text = categories["channels.yaml"]
    assert "&" not in text and "*id" not in text
    data = yaml.safe_load(text)
    assert len(data["dispatcharr"]["channels"]) == 300
