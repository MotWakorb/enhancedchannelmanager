import json
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

from services.epg_migration import (
    PreviewTokenError,
    build_xmltv_lcn_index,
    create_preview_token,
    parse_xmltv_lcn_index,
    preview_migration,
    verify_preview_token,
)


def test_recorded_dispatcharr_response_preserves_xmltv_lcn_as_epg_tvg_id():
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "dispatcharr_epg_lcn_recorded.json"
        ).read_text()
    )
    assert fixture["capture"]["survey"] == {
        "xmltv_channel_rows_examined": 19867,
        "id_equals_gnid_equals_lcn": 19867,
        "duplicate_lcn_rows_within_source": 0,
        "schedules_direct_epg_rows_examined": 3033,
        "schedules_direct_duplicate_tvg_ids": 0,
    }
    root = ET.fromstring(fixture["xmltv"])
    channel = root.find("channel")
    assert channel is not None
    epg_row = fixture["epgdata_response"]["results"][1]
    assert channel.findtext("lcn") == channel.findtext("gnid") == channel.get("id")
    assert epg_row["tvg_id"] == channel.get("id")
    index = parse_xmltv_lcn_index(fixture["xmltv"].encode())
    assert index.channel_to_lcn == {"10101": "10101"}
    assert {source["source_type"] for source in fixture["source_response"]} == {
        "xmltv",
        "schedules_direct",
    }
    epg_rows = fixture["epgdata_response"]["results"]
    sources = fixture["source_response"]
    xml_index = {22: index}
    to_sd = preview_migration(
        channels=[{"id": 1, "name": "Recorded", "epg_data_id": 102}],
        epg_data=epg_rows,
        sources=sources,
        target_source_id=20,
        xmltv_indexes=xml_index,
    )
    to_xml = preview_migration(
        channels=[{"id": 1, "name": "Recorded", "epg_data_id": 101}],
        epg_data=epg_rows,
        sources=sources,
        target_source_id=22,
        xmltv_indexes=xml_index,
    )
    assert (to_sd[0]["status"], to_sd[0]["target_epg_data_id"]) == ("ready", 101)
    assert (to_xml[0]["status"], to_xml[0]["target_epg_data_id"]) == ("ready", 102)


def test_preview_maps_xmltv_to_schedules_direct_by_lcn():
    rows = preview_migration(
        channels=[{"id": 7, "name": "News", "epg_data_id": 11}],
        epg_data=[
            {"id": 11, "epg_source": 1, "tvg_id": "iptv.news"},
            {"id": 22, "epg_source": 2, "tvg_id": "10101", "name": "News SD"},
        ],
        sources=[
            {"id": 1, "name": "IPTV", "source_type": "xmltv"},
            {"id": 2, "name": "Gracenote", "source_type": "schedules_direct"},
        ],
        target_source_id=2,
        xmltv_indexes={1: build_xmltv_lcn_index([("iptv.news", "10101")])},
    )
    assert rows[0]["status"] == "ready"
    assert rows[0]["target_epg_data_id"] == 22
    assert rows[0]["lcn"] == "10101"


def test_preview_maps_schedules_direct_to_xmltv_and_rejects_ambiguous_lcn():
    common = dict(
        channels=[{"id": 7, "name": "News", "epg_data_id": 11}],
        epg_data=[
            {"id": 11, "epg_source": 2, "tvg_id": "10101"},
            {"id": 22, "epg_source": 1, "tvg_id": "iptv.east"},
            {"id": 23, "epg_source": 1, "tvg_id": "iptv.west"},
        ],
        sources=[
            {"id": 1, "name": "IPTV", "source_type": "xmltv"},
            {"id": 2, "name": "Gracenote", "source_type": "schedules_direct"},
        ],
        target_source_id=1,
    )
    rows = preview_migration(
        **common,
        xmltv_indexes={
            1: build_xmltv_lcn_index(
                [("iptv.east", "10101"), ("iptv.west", "10101")]
            )
        },
    )
    assert rows[0]["status"] == "ambiguous_target"
    assert rows[0]["target_epg_data_id"] is None


def test_preview_classifies_unassigned_missing_and_already_target_without_overwrite():
    rows = preview_migration(
        channels=[
            {"id": 1, "name": "None", "epg_data_id": None},
            {"id": 2, "name": "Same", "epg_data_id": 20},
            {"id": 3, "name": "Missing", "epg_data_id": 10},
        ],
        epg_data=[
            {"id": 10, "epg_source": 1, "tvg_id": "unknown"},
            {"id": 20, "epg_source": 2, "tvg_id": "20"},
        ],
        sources=[
            {"id": 1, "name": "IPTV", "source_type": "xmltv"},
            {"id": 2, "name": "Target", "source_type": "schedules_direct"},
        ],
        target_source_id=2,
        xmltv_indexes={1: build_xmltv_lcn_index([])},
    )
    assert [row["status"] for row in rows] == [
        "unassigned",
        "already_target",
        "missing_lcn",
    ]


def _token_row():
    return {
        "channel_id": 1,
        "current_epg_data_id": 10,
        "current_source_id": 1,
        "current_tvg_id": "iptv.news",
        "lcn": "10101",
        "target_epg_data_id": 20,
        "target_tvg_id": "10101",
    }


def test_preview_rejects_dummy_and_unknown_origins_distinctly():
    rows = preview_migration(
        channels=[
            {"id": 1, "name": "Dummy", "epg_data_id": 10},
            {"id": 2, "name": "Unknown", "epg_data_id": 11},
        ],
        epg_data=[
            {"id": 10, "epg_source": 3, "tvg_id": "dummy"},
            {"id": 11, "epg_source": 99, "tvg_id": "unknown"},
            {"id": 20, "epg_source": 2, "tvg_id": "10101"},
        ],
        sources=[
            {"id": 2, "name": "Target", "source_type": "schedules_direct"},
            {"id": 3, "name": "Dummy", "source_type": "dummy"},
        ],
        target_source_id=2,
        xmltv_indexes={},
    )
    assert [row["status"] for row in rows] == [
        "unsupported_origin",
        "unsupported_origin",
    ]


def test_structurally_supports_xmltv_channel_id_different_from_gnid():
    index = parse_xmltv_lcn_index(
        b"<tv><channel id='iptv.news'><lcn>7</lcn><gnid>10101</gnid>"
        b"</channel><programme/></tv>"
    )
    assert index.channel_to_lcn == {"iptv.news": "10101"}
    assert index.lcn_to_channels == {"10101": ("iptv.news",)}


def test_preview_token_binds_order_identity_actor_instance_and_expiry():
    rows = [
        _token_row(),
        {**_token_row(), "channel_id": 2, "current_epg_data_id": 11},
    ]
    token = create_preview_token(
        secret="configured-secret",
        issuer="ecm:one",
        actor="7:admin",
        target_source_id=2,
        rows=rows,
        now=1000,
        ttl_seconds=60,
    )
    verify_args = dict(
        token=token,
        secret="configured-secret",
        issuer="ecm:one",
        actor="7:admin",
        target_source_id=2,
        rows=rows,
        now=1010,
    )
    verify_preview_token(**verify_args)
    # Tokens intentionally have no durable one-time store. A replay verifies,
    # then apply's expected-current channel check makes it idempotent.
    verify_preview_token(**verify_args)
    mutations = [
        {**rows[0], "target_tvg_id": "changed"},
        list(reversed(rows)),
    ]
    for changed in mutations:
        changed_rows = changed if isinstance(changed, list) else [changed, rows[1]]
        with pytest.raises(PreviewTokenError):
            verify_preview_token(
                token=token,
                secret="configured-secret",
                issuer="ecm:one",
                actor="7:admin",
                target_source_id=2,
                rows=changed_rows,
                now=1010,
            )
    for kwargs in (
        {"secret": "rotated-secret"},
        {"issuer": "ecm:other"},
        {"actor": "8:other"},
        {"now": 1060},
    ):
        params = dict(
            token=token,
            secret="configured-secret",
            issuer="ecm:one",
            actor="7:admin",
            target_source_id=2,
            rows=rows,
            now=1010,
        )
        params.update(kwargs)
        with pytest.raises(PreviewTokenError):
            verify_preview_token(**params)
