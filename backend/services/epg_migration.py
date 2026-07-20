"""Pure matching helpers for preview-first EPG guide migration."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import io
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


_TOKEN_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class XMLTVLCNIndex:
    channel_to_lcn: dict[str, str]
    lcn_to_channels: dict[str, tuple[str, ...]]


def parse_xmltv_lcn_index(content: bytes) -> XMLTVLCNIndex:
    """Parse the XMLTV channel header, preferring ``gnid`` over legacy ``lcn``."""
    pairs: list[tuple[str, str]] = []
    stream = io.BytesIO(content)
    root = None
    for event, element in ET.iterparse(stream, events=("start", "end")):
        tag = element.tag.rsplit("}", 1)[-1]
        if event == "start" and root is None:
            root = element
        if event == "end" and tag == "channel":
            channel_id = (element.get("id") or "").strip()
            gnid = None
            lcn = None
            for child in element:
                child_tag = child.tag.rsplit("}", 1)[-1]
                value = (child.text or "").strip()
                if child_tag == "gnid" and value and gnid is None:
                    gnid = value
                elif child_tag == "lcn" and value and lcn is None:
                    lcn = value
            if channel_id and (gnid or lcn):
                pairs.append((channel_id, gnid or lcn or ""))
            if root is not None:
                root.clear()
        elif event == "end" and tag == "programme":
            break
    return build_xmltv_lcn_index(pairs)


def build_xmltv_lcn_index(channels: list[tuple[str, str]]) -> XMLTVLCNIndex:
    """Build both directions without silently choosing duplicate LCN rows."""
    channel_to_lcn: dict[str, str] = {}
    reverse: dict[str, list[str]] = defaultdict(list)
    for channel_id, lcn in channels:
        channel_id = channel_id.strip()
        lcn = lcn.strip()
        if not channel_id or not lcn:
            continue
        channel_to_lcn[channel_id] = lcn
        reverse[lcn].append(channel_id)
    return XMLTVLCNIndex(
        channel_to_lcn=channel_to_lcn,
        lcn_to_channels={key: tuple(values) for key, values in reverse.items()},
    )


def preview_migration(
    *,
    channels: list[dict[str, Any]],
    epg_data: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    target_source_id: int,
    xmltv_indexes: dict[int, XMLTVLCNIndex],
) -> list[dict[str, Any]]:
    """Classify every channel; only an unambiguous LCN mapping is ``ready``."""
    source_by_id = {source["id"]: source for source in sources}
    target = source_by_id[target_source_id]
    epg_by_id = {row["id"]: row for row in epg_data}
    target_by_tvg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in epg_data:
        if row.get("epg_source") == target_source_id:
            target_by_tvg[str(row.get("tvg_id") or "")].append(row)

    rows: list[dict[str, Any]] = []
    for channel in channels:
        current = epg_by_id.get(channel.get("epg_data_id"))
        base = {
            "channel_id": channel["id"],
            "channel_name": channel.get("name") or f"Channel {channel['id']}",
            "current_epg_data_id": channel.get("epg_data_id"),
            "current_source_id": current.get("epg_source") if current else None,
            "current_source_name": None,
            "lcn": None,
            "target_epg_data_id": None,
            "target_name": None,
        }
        if current is None:
            rows.append({**base, "status": "unassigned"})
            continue
        current_source_id = current["epg_source"]
        current_source = source_by_id.get(current_source_id)
        base["current_source_name"] = (
            current_source.get("name") if current_source else f"Source {current_source_id}"
        )
        if current_source_id == target_source_id:
            rows.append({**base, "status": "already_target"})
            continue

        current_tvg = str(current.get("tvg_id") or "")
        if current_source and current_source.get("source_type") == "xmltv":
            index = xmltv_indexes.get(current_source_id)
            lcn = index.channel_to_lcn.get(current_tvg) if index else None
        else:
            # Schedules Direct imports expose the station/LCN as EPGData.tvg_id.
            lcn = current_tvg or None
        base["lcn"] = lcn
        if not lcn:
            rows.append({**base, "status": "missing_lcn"})
            continue

        if target.get("source_type") == "xmltv":
            index = xmltv_indexes.get(target_source_id)
            target_tvgs = index.lcn_to_channels.get(lcn, ()) if index else ()
        else:
            target_tvgs = (lcn,)
        candidates = [
            candidate
            for target_tvg in target_tvgs
            for candidate in target_by_tvg.get(target_tvg, ())
        ]
        if not candidates:
            rows.append({**base, "status": "missing_target"})
        elif len(candidates) > 1:
            rows.append({**base, "status": "ambiguous_target"})
        else:
            candidate = candidates[0]
            rows.append(
                {
                    **base,
                    "status": "ready",
                    "target_epg_data_id": candidate["id"],
                    "target_name": candidate.get("name"),
                }
            )
    return rows


def sign_ready_rows(target_source_id: int, rows: list[dict[str, Any]]) -> str:
    payload = _token_payload(target_source_id, rows)
    return hmac.new(_TOKEN_KEY, payload, hashlib.sha256).hexdigest()


def verify_ready_rows(
    target_source_id: int, rows: list[dict[str, Any]], token: str
) -> bool:
    expected = sign_ready_rows(target_source_id, rows)
    return hmac.compare_digest(expected, token)


def _token_payload(target_source_id: int, rows: list[dict[str, Any]]) -> bytes:
    canonical = [
        {
            "channel_id": row["channel_id"],
            "current_epg_data_id": row["current_epg_data_id"],
            "target_epg_data_id": row["target_epg_data_id"],
        }
        for row in rows
    ]
    return json.dumps(
        {"target_source_id": target_source_id, "rows": canonical},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
