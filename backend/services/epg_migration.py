"""Pure matching helpers for preview-first EPG guide migration."""

from __future__ import annotations

import hashlib
import hmac
import json
import io
import base64
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


PREVIEW_AUDIENCE = "ecm-guide-migration-apply"
PREVIEW_TTL_SECONDS = 300


class PreviewTokenError(ValueError):
    """A preview token is invalid, expired, or for another actor/instance."""


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
            "current_tvg_id": current.get("tvg_id") if current else None,
            "target_tvg_id": None,
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
        if current_source is None or current_source.get("source_type") not in {
            "xmltv",
            "schedules_direct",
        }:
            rows.append({**base, "status": "unsupported_origin"})
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
                    "target_tvg_id": candidate.get("tvg_id"),
                }
            )
    return rows


def _ready_identity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = [
        {
            "channel_id": row["channel_id"],
            "current_epg_data_id": row["current_epg_data_id"],
            "current_source_id": row["current_source_id"],
            "current_tvg_id": row["current_tvg_id"],
            "lcn": row["lcn"],
            "target_epg_data_id": row["target_epg_data_id"],
            "target_tvg_id": row["target_tvg_id"],
        }
        for row in rows
    ]
    return canonical


def create_preview_token(
    *,
    secret: str,
    issuer: str,
    actor: str,
    target_source_id: int,
    rows: list[dict[str, Any]],
    now: int | None = None,
    ttl_seconds: int = PREVIEW_TTL_SECONDS,
) -> str:
    issued = int(time.time() if now is None else now)
    envelope = {
        "v": 1,
        "iss": issuer,
        "aud": PREVIEW_AUDIENCE,
        "sub": actor,
        "iat": issued,
        "exp": issued + ttl_seconds,
        "jti": uuid.uuid4().hex,
        "target_source_id": target_source_id,
        "rows": _ready_identity(rows),
    }
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
    return (
        encoded.decode()
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )


def verify_preview_token(
    *,
    token: str,
    secret: str,
    issuer: str,
    actor: str,
    target_source_id: int,
    rows: list[dict[str, Any]],
    now: int | None = None,
) -> dict[str, Any]:
    try:
        encoded, encoded_signature = token.split(".", 1)
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise PreviewTokenError("Preview signature is invalid.")
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        envelope = json.loads(payload)
    except PreviewTokenError:
        raise
    except Exception as exc:
        raise PreviewTokenError("Preview token is malformed.") from exc

    current = int(time.time() if now is None else now)
    if envelope.get("v") != 1:
        raise PreviewTokenError("Preview token version is unsupported.")
    if envelope.get("iss") != issuer or envelope.get("aud") != PREVIEW_AUDIENCE:
        raise PreviewTokenError("Preview token belongs to another instance or audience.")
    if envelope.get("sub") != actor:
        raise PreviewTokenError("Preview token belongs to another actor.")
    if not isinstance(envelope.get("iat"), int) or not isinstance(envelope.get("exp"), int):
        raise PreviewTokenError("Preview token timestamps are invalid.")
    if envelope["iat"] > current + 30 or envelope["exp"] <= current:
        raise PreviewTokenError("Preview token is expired or not yet valid.")
    if envelope.get("target_source_id") != target_source_id:
        raise PreviewTokenError("Preview target source changed.")
    if envelope.get("rows") != _ready_identity(rows):
        raise PreviewTokenError("Preview assignments changed.")
    return envelope
