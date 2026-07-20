import json
from pathlib import Path
import xml.etree.ElementTree as ET

from services.epg_migration import (
    build_xmltv_lcn_index,
    parse_xmltv_lcn_index,
    preview_migration,
    sign_ready_rows,
    verify_ready_rows,
)


def test_recorded_dispatcharr_response_preserves_xmltv_lcn_as_epg_tvg_id():
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "dispatcharr_epg_lcn_recorded.json"
        ).read_text()
    )
    root = ET.fromstring(fixture["xmltv"])
    channel = root.find("channel")
    assert channel is not None
    epg_row = fixture["epgdata_response"]["results"][0]
    assert channel.findtext("lcn") == channel.findtext("gnid") == channel.get("id")
    assert epg_row["tvg_id"] == channel.get("id")
    index = parse_xmltv_lcn_index(fixture["xmltv"].encode())
    assert index.channel_to_lcn == {"10101": "10101"}


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


def test_preview_token_is_bound_to_exact_assignments():
    rows = [
        {
            "channel_id": 1,
            "current_epg_data_id": 10,
            "target_epg_data_id": 20,
        }
    ]
    token = sign_ready_rows(2, rows)
    assert verify_ready_rows(2, rows, token)
    rows[0]["target_epg_data_id"] = 21
    assert not verify_ready_rows(2, rows, token)
