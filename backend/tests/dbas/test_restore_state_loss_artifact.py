"""Artifact-level regression tests for the drill's state-loss findings.

Beads ``enhancedchannelmanager-2o0cz`` / ``enhancedchannelmanager-dfkbn``. The
importer-level behaviour is pinned in ``test_restore_state_loss.py``; these tests
pin the two halves that live in the ARTIFACT itself, because an importer that
works perfectly restores nothing when the producer never wrote the bytes:

* the Dispatcharr LOGO INVENTORY is produced, so a remotely-hosted logo's URL
  survives the round trip (the drill's binary metadata was
  ``{"logo_count": 0, "logos": []}`` — ECM's own ``/config/uploads/logos/`` is
  empty because ECM's Logo Manager writes to DISPATCHARR's ``/data/logos/``);
* ECM's OWN ``categories/settings.yaml`` decodes into a restorable plan category
  (it was written by the builder from day one and dropped on the floor by the
  decoder, which is why ``user_timezone`` / ``stats_poll_interval`` reverted).
"""
from __future__ import annotations

import io
import zipfile

import pytest
import yaml

from dbas.restore_contracts import EntityType


def _artifact(members: dict[str, str]) -> zipfile.ZipFile:
    """An in-memory ZIP carrying the given member -> text mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


def test_logos_is_a_produced_artifact_category():
    """The builder emits a ``logos`` category from Dispatcharr's logo inventory.

    Without it the artifact carries NO record of a logo whose bytes live on the
    Dispatcharr volume or on a CDN — which is every logo the drill lost.
    """
    from routers.backup import RESTORABLE_SECTIONS

    assert "logos" in RESTORABLE_SECTIONS
    assert RESTORABLE_SECTIONS["logos"]["dispatcharr"] is True
    # Artifact-only: the legacy per-section YAML restore has no logo restorer.
    assert RESTORABLE_SECTIONS["logos"]["artifact_only"] is True


@pytest.mark.asyncio
async def test_logo_inventory_gather_returns_the_destination_logos():
    """``_gather_dispatcharr_sections`` fetches the full logo inventory."""
    from unittest.mock import AsyncMock, patch

    from routers import backup as backup_mod

    client = AsyncMock()
    client.get_all_logos_paginated = AsyncMock(
        return_value=[{"id": 21, "name": "ESPN", "url": "https://cdn/espn.png"}]
    )
    with patch.object(backup_mod, "get_client", return_value=client):
        out = await backup_mod._gather_dispatcharr_sections({"logos"})

    assert out["logos"] == [{"id": 21, "name": "ESPN", "url": "https://cdn/espn.png"}]


def test_decoder_merges_the_logo_inventory_with_the_binary_subtree():
    """A URL-only inventory row becomes a restorable LOGO plan entity.

    And an inventory row that DOES have bytes in the binary subtree is enriched
    rather than duplicated — one logo must not restore twice.
    """
    from dbas.restore_artifact import decode_artifact_to_plan

    inventory = yaml.dump(
        {"dispatcharr": {"logos": [
            {"id": 21, "name": "ESPN", "url": "https://cdn/espn.png"},
            {"id": 22, "name": "CNN", "url": "https://cdn/cnn.png"},
        ]}}
    )
    zf = _artifact({
        "manifest.json": '{"schema_version": 1}',
        "categories/logos.yaml": inventory,
        "binary/metadata.json": '{"logos": [{"filename": "espn.png", "id": 21, "name": "ESPN"}]}',
        "binary/logos/espn.png": "notarealpng",
    })
    with zf:
        plan = decode_artifact_to_plan(zf)

    logos = plan.category(EntityType.LOGO).entities
    by_id = {entity.get("id"): entity for entity in logos}
    assert set(by_id) == {21, 22}
    # 21 kept a lazy reference to its bytes AND gained the archived URL.
    assert by_id[21]["archive_member"] == "binary/logos/espn.png"
    assert "content_b64" not in by_id[21]
    assert by_id[21]["url"] == "https://cdn/espn.png"
    # 22 has no bytes anywhere — the URL is the only way back.
    assert "content_b64" not in by_id[22]
    assert by_id[22]["url"] == "https://cdn/cnn.png"
    # Exactly ONE LOGO category in the plan — a second would shadow the first.
    assert sum(1 for c in plan.categories if c.entity_type == EntityType.LOGO) == 1


def test_decoder_produces_an_ecm_settings_category():
    """ECM's own ``categories/settings.yaml`` decodes into a restorable category.

    The builder has always written this member; the decoder had no row for the
    section, so the whole blob was silently dropped and ECM's settings were never
    restored (bead …-dfkbn item 4).
    """
    from dbas.restore_artifact import decode_artifact_to_plan

    zf = _artifact({
        "manifest.json": '{"schema_version": 1}',
        "categories/settings.yaml": yaml.dump(
            {"settings": {"user_timezone": "America/Chicago", "stats_poll_interval": 37}}
        ),
    })
    with zf:
        plan = decode_artifact_to_plan(zf)

    cat = plan.category(EntityType.ECM_SETTINGS)
    assert cat is not None
    assert cat.entities == [
        {"values": {"user_timezone": "America/Chicago", "stats_poll_interval": 37}}
    ]


def test_ecm_settings_category_is_empty_when_the_member_is_absent():
    """A settings-less artifact decodes to an EMPTY category, never a crash."""
    from dbas.restore_artifact import decode_artifact_to_plan

    zf = _artifact({"manifest.json": '{"schema_version": 1}'})
    with zf:
        plan = decode_artifact_to_plan(zf)

    cat = plan.category(EntityType.ECM_SETTINGS)
    assert cat is not None and cat.entities == []
