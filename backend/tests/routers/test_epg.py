"""
Unit tests for EPG endpoints.

Tests: 12 endpoints covering EPG sources CRUD, refresh, import,
       EPG data listing, grid, and LCN lookup.
Mocks: get_client() to isolate from Dispatcharr.
"""
import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, patch
import gzip
import socket
import time

from fastapi import HTTPException
from security.ssrf import SSRFMode
from services.epg_migration import build_xmltv_lcn_index


class _FakeEPGHTTPClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient used by the
    LCN lookup endpoints. Serves canned XMLTV bytes per URL so the parser runs on
    real content. HEAD returns no content-length, so the endpoints take the
    "small file, download fully" path (no streaming/gzip branches)."""

    def __init__(self, content_by_url: dict[str, bytes]):
        self._content_by_url = content_by_url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def head(self, url, *args, **kwargs):
        # No content-length header -> file_size == 0 -> small-file path.
        return httpx.Response(200, headers={})

    async def get(self, url, *args, **kwargs):
        # request must be set so response.raise_for_status() can run.
        return httpx.Response(
            200, content=self._content_by_url[url], request=httpx.Request("GET", url)
        )

    def stream(self, method, url, *args, **kwargs):
        content = self._content_by_url[url]

        class _Stream:
            async def __aenter__(self):
                self.response = httpx.Response(
                    200,
                    content=content,
                    request=httpx.Request(method, url),
                )

                async def _aiter_raw():
                    yield content

                self.response.aiter_raw = _aiter_raw
                return self.response

            async def __aexit__(self, *exc):
                return False

        return _Stream()


def _xmltv(channels_xml: str) -> bytes:
    """Build a minimal XMLTV document. A trailing <programme> ensures the parsers
    (which break at the first programme) terminate as they do on real EPGs."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<tv>"
        f"{channels_xml}"
        '<programme channel="x" start="20260101000000 +0000" stop="20260101010000 +0000">'
        "<title>p</title></programme>"
        "</tv>"
    ).encode("utf-8")


def _channels_page(channels, count=None):
    """Build a single-page get_channels() response envelope (bead vznut.6).

    ``count`` defaults to ``len(channels)`` -- a single-page, non-truncated
    response -- matching the shape existing single-page mocks in this file
    already use (no explicit ``count`` key). Pass an explicit ``count`` larger
    than ``len(channels)`` to simulate one page of a multi-page fleet.
    """
    return {"count": count if count is not None else len(channels), "results": channels}


async def _poll_migration(async_client, accepted):
    assert accepted.status_code == 202
    batch_id = accepted.json()["batch_id"]
    for _ in range(100):
        response = await async_client.get(f"/api/epg/migration/apply/{batch_id}")
        body = response.json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(0)
    raise AssertionError("migration job did not reach a terminal state")


class TestGuideMigration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path,payload",
        [
            ("/api/epg/migration/preview", {"target_epg_source_id": 2}),
            (
                "/api/epg/migration/apply",
                {
                    "target_epg_source_id": 2,
                    "preview_token": "x.y",
                    "items": [],
                },
            ),
        ],
    )
    async def test_non_admin_is_forbidden(self, async_client, path, payload):
        from auth import RequireAdminIfEnabled as admin_dependency
        from main import app

        async def reject():
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[admin_dependency.dependency] = reject
        try:
            response = await async_client.post(path, json=payload)
        finally:
            app.dependency_overrides.pop(admin_dependency.dependency, None)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_preview_uses_recorded_xml_lcn_and_returns_signed_ready_row(
        self, async_client
    ):
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {
                "id": 1,
                "name": "IPTV",
                "source_type": "xmltv",
                "url": "https://epg.test/iptv.xml",
            },
            {
                "id": 2,
                "name": "Gracenote",
                "source_type": "schedules_direct",
                "url": None,
            },
        ]
        mock_client.get_channels.return_value = _channels_page(
            [{"id": 7, "name": "News", "epg_data_id": 11}]
        )
        mock_client.get_epg_data.return_value = [
            {"id": 11, "epg_source": 1, "tvg_id": "iptv.news"},
            {"id": 22, "epg_source": 2, "tvg_id": "10101", "name": "News SD"},
        ]
        xml = _xmltv(
            '<channel id="iptv.news"><display-name>News</display-name>'
            "<lcn>10101</lcn><gnid>10101</gnid></channel>"
        )
        fake_http = _FakeEPGHTTPClient({"https://epg.test/iptv.xml": xml})

        with patch("routers.epg.get_client", return_value=mock_client), patch(
            "routers.epg.httpx.AsyncClient", return_value=fake_http
        ):
            response = await async_client.post(
                "/api/epg/migration/preview",
                json={"target_epg_source_id": 2},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["counts"]["ready"] == 1
        assert body["rows"][0]["lcn"] == "10101"
        assert body["rows"][0]["target_epg_data_id"] == 22
        assert "." in body["preview_token"]

    @pytest.mark.asyncio
    async def test_preview_rejects_channel_fleet_beyond_bound(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {
                "id": 2,
                "name": "Gracenote",
                "source_type": "schedules_direct",
                "url": None,
            }
        ]
        mock_client.get_channels.return_value = _channels_page(
            [
                {"id": index, "name": f"Channel {index}", "epg_data_id": None}
                for index in range(1001)
            ]
        )
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/migration/preview",
                json={"target_epg_source_id": 2},
            )
        assert response.status_code == 409
        mock_client.get_epg_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_updates_current_rows_and_skips_preview_drift(
        self, async_client
    ):
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {
                "id": 1,
                "name": "IPTV",
                "source_type": "xmltv",
                "url": "https://epg.test/iptv.xml",
            },
            {
                "id": 2,
                "name": "Gracenote",
                "source_type": "schedules_direct",
                "url": None,
            },
        ]
        preview_channels = [
            {"id": 7, "name": "News", "epg_data_id": 11},
            {"id": 8, "name": "Sports", "epg_data_id": 12},
        ]
        mock_client.get_channels.return_value = _channels_page(preview_channels)
        mock_client.get_channel.side_effect = [
            {"id": 7, "name": "News", "epg_data_id": 11},
            {"id": 8, "name": "Sports", "epg_data_id": 99},
        ]
        all_epg = [
            {"id": 11, "epg_source": 1, "tvg_id": "iptv.news"},
            {"id": 12, "epg_source": 1, "tvg_id": "iptv.sports"},
            {"id": 22, "epg_source": 2, "tvg_id": "10101", "name": "News SD"},
            {"id": 23, "epg_source": 2, "tvg_id": "10102", "name": "Sports SD"},
        ]
        mock_client.get_epg_data.side_effect = [
            all_epg,
            [all_epg[2], all_epg[3]],
        ]
        epg_by_id = {row["id"]: row for row in all_epg}
        mock_client.get_epg_data_by_id.side_effect = lambda epg_id: epg_by_id[epg_id]
        xml = _xmltv(
            '<channel id="iptv.news"><gnid>10101</gnid></channel>'
            '<channel id="iptv.sports"><gnid>10102</gnid></channel>'
        )
        fake_http = _FakeEPGHTTPClient({"https://epg.test/iptv.xml": xml})
        with patch("routers.epg.get_client", return_value=mock_client), patch(
            "routers.epg.httpx.AsyncClient", return_value=fake_http
        ), patch("routers.epg.journal"):
            preview = await async_client.post(
                "/api/epg/migration/preview",
                json={"target_epg_source_id": 2},
            )
            ready = [
                {
                    "channel_id": row["channel_id"],
                    "current_epg_data_id": row["current_epg_data_id"],
                    "current_source_id": row["current_source_id"],
                    "current_tvg_id": row["current_tvg_id"],
                    "lcn": row["lcn"],
                    "target_epg_data_id": row["target_epg_data_id"],
                    "target_tvg_id": row["target_tvg_id"],
                }
                for row in preview.json()["rows"]
                if row["status"] == "ready"
            ]
            applied = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": preview.json()["preview_token"],
                    "items": ready,
                },
            )
            terminal = await _poll_migration(async_client, applied)

        assert terminal["status"] == "completed"
        assert terminal["result"]["updated"] == 1
        assert terminal["result"]["skipped"] == 1
        assert terminal["result"]["mutated"] == 1
        mock_client.update_channel.assert_awaited_once_with(7, {"epg_data_id": 22})

    @pytest.mark.asyncio
    async def test_preview_enforces_epg_cap_before_xml_fetch(self, async_client):
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 2, "name": "SD", "source_type": "schedules_direct", "url": None}
        ]
        mock_client.get_channels.return_value = _channels_page([])
        mock_client.get_epg_data.return_value = [
            {"id": index, "epg_source": 2, "tvg_id": str(index)}
            for index in range(50001)
        ]
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/migration/preview", json={"target_epg_source_id": 2}
            )
        assert response.status_code == 409
        mock_client.get_epg_data.assert_awaited_once_with(max_results=50001)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url,mode,resolved_ip",
        [
            ("file:///etc/passwd", SSRFMode.PUBLIC_ONLY, None),
            ("http://127.0.0.1/epg.xml", SSRFMode.PUBLIC_ONLY, None),
            ("http://169.254.169.254/latest", SSRFMode.LAN_FRIENDLY, None),
            ("http://epg.internal/guide.xml", SSRFMode.PUBLIC_ONLY, "10.0.0.8"),
            ("http://epg.internal/guide.xml", SSRFMode.PUBLIC_ONLY, "169.254.1.2"),
        ],
    )
    async def test_xmltv_fetch_uses_ssrf_chokepoint(
        self, url, mode, resolved_ip
    ):
        from routers.epg import _load_xmltv_migration_index

        def resolve(host, port, **kwargs):
            ip = resolved_ip or host
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        with patch("security.ssrf.socket.getaddrinfo", side_effect=resolve), patch(
            "routers.epg.get_ssrf_mode", return_value=mode
        ), patch("tasks.dbas_sync_client.get_ssrf_mode", return_value=mode):
            with pytest.raises(HTTPException) as exc:
                await _load_xmltv_migration_index({"id": 1, "url": url})
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_xmltv_rejects_doctype_before_parsing(self):
        from routers.epg import _load_xmltv_migration_index

        content = (
            b"<?xml version='1.0'?><!DOCTYPE tv [<!ENTITY x 'boom'>]>"
            b"<tv><channel id='1'><lcn>&x;</lcn></channel><programme/></tv>"
        )
        fake_http = _FakeEPGHTTPClient({"https://epg.test/unsafe.xml": content})
        with patch("routers.epg.httpx.AsyncClient", return_value=fake_http):
            with pytest.raises(HTTPException) as exc:
                await _load_xmltv_migration_index(
                    {"id": 1, "url": "https://epg.test/unsafe.xml"}
                )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_xmltv_redirect_to_metadata_is_revalidated_and_blocked(self):
        from routers.epg import _load_xmltv_migration_index

        class RedirectClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def stream(self, method, url):
                response = httpx.Response(
                    302,
                    headers={"location": "http://169.254.169.254/latest"},
                    request=httpx.Request(method, url),
                )

                class Context:
                    async def __aenter__(self):
                        return response

                    async def __aexit__(self, *exc):
                        return False

                return Context()

        with patch("routers.epg.httpx.AsyncClient", return_value=RedirectClient()):
            with pytest.raises(HTTPException) as exc:
                await _load_xmltv_migration_index(
                    {"id": 1, "url": "https://public.example/guide.xml"}
                )
        assert exc.value.status_code == 400

    def test_gzip_decompression_is_bounded_including_unconsumed_tail(self):
        from routers import epg

        payload = gzip.compress(b"A" * 4096)
        output = bytearray()
        decompressor = epg.zlib.decompressobj(epg.zlib.MAX_WBITS | 16)
        with patch("routers.epg._XMLTV_HEADER_MAX_DECOMPRESSED", 64):
            with pytest.raises(HTTPException) as exc:
                epg._append_gzip_bounded(decompressor, payload, output)
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_large_xml_index_parse_does_not_block_event_loop(self):
        from routers.epg import _load_xmltv_migration_index

        fake_http = _FakeEPGHTTPClient(
            {"https://epg.test/large.xml": _xmltv('<channel id="x"><gnid>1</gnid></channel>')}
        )

        def slow_parse(content, source_id):
            time.sleep(0.1)
            return build_xmltv_lcn_index([("x", "1")])

        with patch("routers.epg.httpx.AsyncClient", return_value=fake_http), patch(
            "routers.epg._parse_bounded_xmltv_header", side_effect=slow_parse
        ):
            parse_task = asyncio.create_task(
                _load_xmltv_migration_index(
                    {"id": 1, "url": "https://epg.test/large.xml"}
                )
            )
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            elapsed = time.perf_counter() - started
            result = await parse_task
        assert elapsed < 0.05
        assert result.channel_to_lcn == {"x": "1"}

    @pytest.mark.asyncio
    async def test_apply_continues_after_row_failure_and_reports_audit_failure(
        self, async_client
    ):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        items = [
            {
                "channel_id": 7,
                "current_epg_data_id": 11,
                "current_source_id": 1,
                "current_tvg_id": "iptv.news",
                "lcn": "10101",
                "target_epg_data_id": 22,
                "target_tvg_id": "10101",
            },
            {
                "channel_id": 8,
                "current_epg_data_id": 12,
                "current_source_id": 1,
                "current_tvg_id": "iptv.sports",
                "lcn": "10102",
                "target_epg_data_id": 23,
                "target_tvg_id": "10102",
            },
            {
                "channel_id": 9,
                "current_epg_data_id": 13,
                "current_source_id": 1,
                "current_tvg_id": "iptv.local",
                "lcn": "10103",
                "target_epg_data_id": 24,
                "target_tvg_id": "10103",
            },
        ]
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=items,
        )
        epg_rows = {
            11: {"id": 11, "epg_source": 1, "tvg_id": "iptv.news"},
            12: {"id": 12, "epg_source": 1, "tvg_id": "iptv.sports"},
            13: {"id": 13, "epg_source": 1, "tvg_id": "iptv.local"},
            22: {"id": 22, "epg_source": 2, "tvg_id": "10101"},
            23: {"id": 23, "epg_source": 2, "tvg_id": "10102"},
            24: {"id": 24, "epg_source": 2, "tvg_id": "10103"},
        }
        client = AsyncMock()
        client.get_epg_sources.return_value = [
            {"id": 1, "name": "IPTV", "source_type": "xmltv", "url": "https://x"},
            {"id": 2, "name": "SD", "source_type": "schedules_direct", "url": None},
        ]
        client.get_epg_data.return_value = [epg_rows[22], epg_rows[23], epg_rows[24]]
        client.get_epg_data_by_id.side_effect = lambda row_id: epg_rows[row_id]
        client.get_channel.side_effect = [
            {"id": 7, "name": "News", "epg_data_id": 11},
            {"id": 8, "name": "Sports", "epg_data_id": 12},
            {"id": 9, "name": "Local", "epg_data_id": 13},
        ]
        client.update_channel.side_effect = [
            RuntimeError("first row failed"),
            {"id": 8},
            {"id": 9},
        ]
        with patch("routers.epg.get_client", return_value=client), patch(
            "routers.epg.get_jwt_secret_key", return_value=secret
        ), patch(
            "routers.epg._load_xmltv_migration_index",
            new=AsyncMock(
                return_value=build_xmltv_lcn_index(
                    [
                        ("iptv.news", "10101"),
                        ("iptv.sports", "10102"),
                        ("iptv.local", "10103"),
                    ]
                )
            ),
        ), patch(
            "routers.epg.journal.log_entry",
            side_effect=[object(), RuntimeError("journal unavailable")],
        ):
            response = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": items,
                },
            )
            terminal = await _poll_migration(async_client, response)
        assert terminal["status"] == "completed"
        body = terminal["result"]
        assert body["failed"] == 1
        assert body["updated"] == 1
        assert body["audit_failed"] == 1
        assert body["mutated"] == 2
        assert len(body["batch_id"]) == 32
        int(body["batch_id"], 16)
        assert [row["status"] for row in body["results"]] == [
            "failed",
            "updated",
            "updated_audit_failed",
        ]
        assert client.update_channel.await_count == 3

    @pytest.mark.asyncio
    async def test_apply_skips_target_semantic_drift_before_patch(self, async_client):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        item = {
            "channel_id": 7,
            "current_epg_data_id": 11,
            "current_source_id": 1,
            "current_tvg_id": "iptv.news",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "10101",
        }
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=[item],
        )
        client = AsyncMock()
        client.get_epg_sources.return_value = [
            {"id": 1, "name": "IPTV", "source_type": "xmltv", "url": "https://x"},
            {"id": 2, "name": "SD", "source_type": "schedules_direct", "url": None},
        ]
        client.get_epg_data.return_value = [
            {"id": 22, "epg_source": 2, "tvg_id": "10101"}
        ]
        client.get_epg_data_by_id.side_effect = [
            {"id": 11, "epg_source": 1, "tvg_id": "iptv.news"},
            {"id": 22, "epg_source": 2, "tvg_id": "changed"},
        ]
        with patch("routers.epg.get_client", return_value=client), patch(
            "routers.epg.get_jwt_secret_key", return_value=secret
        ), patch(
            "routers.epg._load_xmltv_migration_index",
            new=AsyncMock(
                return_value=build_xmltv_lcn_index([("iptv.news", "10101")])
            ),
        ):
            response = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": [item],
                },
            )
            terminal = await _poll_migration(async_client, response)
        assert terminal["result"]["results"][0]["status"] == "semantic_drift"
        client.get_channel.assert_not_awaited()
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_rejects_sd_duplicate_introduced_after_preview(
        self, async_client
    ):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        item = {
            "channel_id": 7,
            "current_epg_data_id": 11,
            "current_source_id": 1,
            "current_tvg_id": "iptv.news",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "10101",
        }
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=[item],
        )
        client = AsyncMock()
        client.get_epg_sources.return_value = [
            {"id": 1, "source_type": "xmltv", "url": "https://iptv"},
            {"id": 2, "source_type": "schedules_direct"},
        ]
        client.get_epg_data.return_value = [
            {"id": 22, "epg_source": 2, "tvg_id": "10101"},
            {"id": 23, "epg_source": 2, "tvg_id": "10101"},
        ]
        with patch("routers.epg.get_client", return_value=client), patch(
            "routers.epg.get_jwt_secret_key", return_value=secret
        ), patch(
            "routers.epg._load_xmltv_migration_index",
            new=AsyncMock(
                return_value=build_xmltv_lcn_index([("iptv.news", "10101")])
            ),
        ):
            response = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": [item],
                },
            )
            terminal = await _poll_migration(async_client, response)
        assert terminal["result"]["results"] == [
            {"channel_id": 7, "status": "ambiguous_target"}
        ]
        client.get_epg_data_by_id.assert_not_awaited()
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_rejects_xml_duplicate_introduced_after_preview(
        self, async_client
    ):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        item = {
            "channel_id": 7,
            "current_epg_data_id": 11,
            "current_source_id": 1,
            "current_tvg_id": "10101",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "iptv.news",
        }
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=[item],
        )
        client = AsyncMock()
        client.get_epg_sources.return_value = [
            {"id": 1, "source_type": "schedules_direct"},
            {"id": 2, "source_type": "xmltv", "url": "https://iptv"},
        ]
        client.get_epg_data.return_value = [
            {"id": 22, "epg_source": 2, "tvg_id": "iptv.news"},
            {"id": 23, "epg_source": 2, "tvg_id": "iptv.news.alt"},
        ]
        with patch("routers.epg.get_client", return_value=client), patch(
            "routers.epg.get_jwt_secret_key", return_value=secret
        ), patch(
            "routers.epg._load_xmltv_migration_index",
            new=AsyncMock(
                return_value=build_xmltv_lcn_index(
                    [
                        ("iptv.news", "10101"),
                        ("iptv.news.alt", "10101"),
                    ]
                )
            ),
        ):
            response = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": [item],
                },
            )
            terminal = await _poll_migration(async_client, response)
        assert terminal["result"]["results"] == [
            {"channel_id": 7, "status": "ambiguous_target"}
        ]
        client.get_epg_data_by_id.assert_not_awaited()
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token_kind", ["expired", "tampered"])
    async def test_apply_rejects_invalid_preview_tokens_at_acceptance(
        self, async_client, token_kind
    ):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        item = {
            "channel_id": 7,
            "current_epg_data_id": 11,
            "current_source_id": 1,
            "current_tvg_id": "10101",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "iptv.news",
        }
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=[item],
            now=0 if token_kind == "expired" else None,
        )
        if token_kind == "tampered":
            token = token[:-1] + ("A" if token[-1] != "A" else "B")
        with patch("routers.epg.get_jwt_secret_key", return_value=secret):
            response = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": [item],
                },
            )
        assert response.status_code == 409
        assert "Preview again" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_apply_fails_fast_when_another_job_is_running(self, async_client):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        item = {
            "channel_id": 7,
            "current_epg_data_id": 11,
            "current_source_id": 1,
            "current_tvg_id": "10101",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "iptv.news",
        }
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=[item],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_run(request, admin, *, batch_id, job):
            started.set()
            await release.wait()
            return {**job.result, "batch_id": batch_id}

        payload = {
            "target_epg_source_id": 2,
            "preview_token": token,
            "items": [item],
        }
        with patch("routers.epg.get_jwt_secret_key", return_value=secret), patch(
            "routers.epg._run_guide_migration", side_effect=slow_run
        ):
            first = await async_client.post("/api/epg/migration/apply", json=payload)
            await started.wait()
            second = await async_client.post("/api/epg/migration/apply", json=payload)
            assert first.status_code == 202
            assert second.status_code == 409
            release.set()
            terminal = await _poll_migration(async_client, first)
        assert terminal["status"] == "completed"

    @pytest.mark.asyncio
    async def test_poll_exposes_partial_multi_item_progress(self, async_client):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        base = {
            "current_source_id": 1,
            "current_tvg_id": "10101",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "iptv.news",
        }
        items = [
            {**base, "channel_id": 7, "current_epg_data_id": 11},
            {**base, "channel_id": 8, "current_epg_data_id": 12},
        ]
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=items,
        )
        first_visible = asyncio.Event()
        release = asyncio.Event()

        async def slow_run(request, admin, *, batch_id, job):
            job.result["results"].append({"channel_id": 7, "status": "updated"})
            job.result["mutated"] = 1
            job.result["updated"] = 1
            first_visible.set()
            await release.wait()
            job.result["results"].append({"channel_id": 8, "status": "failed"})
            job.result["failed"] = 1
            return {**job.result, "batch_id": batch_id}

        with patch("routers.epg.get_jwt_secret_key", return_value=secret), patch(
            "routers.epg._run_guide_migration", side_effect=slow_run
        ):
            accepted = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": items,
                },
            )
            await first_visible.wait()
            partial = await async_client.get(
                f"/api/epg/migration/apply/{accepted.json()['batch_id']}"
            )
            assert partial.json()["processed"] == 1
            assert partial.json()["result"]["updated"] == 1
            release.set()
            terminal = await _poll_migration(async_client, accepted)
        assert terminal["processed"] == 2
        assert terminal["result"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_apply_reports_unsupported_origin_from_worker(self, async_client):
        from routers.epg import _migration_issuer
        from services.epg_migration import create_preview_token

        item = {
            "channel_id": 7,
            "current_epg_data_id": 11,
            "current_source_id": 1,
            "current_tvg_id": "10101",
            "lcn": "10101",
            "target_epg_data_id": 22,
            "target_tvg_id": "10101",
        }
        secret = "configured-secret"
        token = create_preview_token(
            secret=secret,
            issuer=_migration_issuer(secret),
            actor="auth-disabled",
            target_source_id=2,
            rows=[item],
        )
        client = AsyncMock()
        client.get_epg_sources.return_value = [
            {"id": 1, "source_type": "dummy"},
            {"id": 2, "source_type": "schedules_direct"},
        ]
        client.get_epg_data.return_value = [
            {"id": 22, "epg_source": 2, "tvg_id": "10101"}
        ]
        with patch("routers.epg.get_client", return_value=client), patch(
            "routers.epg.get_jwt_secret_key", return_value=secret
        ):
            accepted = await async_client.post(
                "/api/epg/migration/apply",
                json={
                    "target_epg_source_id": 2,
                    "preview_token": token,
                    "items": [item],
                },
            )
            terminal = await _poll_migration(async_client, accepted)
        assert terminal["result"]["results"] == [
            {"channel_id": 7, "status": "unsupported_origin"}
        ]
        client.update_channel.assert_not_awaited()


class TestGetEPGSources:
    """Tests for GET /api/epg/sources."""

    @pytest.mark.asyncio
    async def test_returns_sources(self, async_client):
        """Returns EPG sources from client."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "name": "XMLTV"},
            {"id": 2, "name": "Gracenote"},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_client_error(self, async_client):
        """Returns 500 on client error."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.side_effect = Exception("Timeout")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources")

        assert response.status_code == 500


class TestGetEPGSource:
    """Tests for GET /api/epg/sources/{source_id}."""

    @pytest.mark.asyncio
    async def test_returns_source(self, async_client):
        """Returns a single EPG source."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "XMLTV"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources/1")

        assert response.status_code == 200
        mock_client.get_epg_source.assert_called_once_with(1)


class TestCreateEPGSource:
    """Tests for POST /api/epg/sources."""

    @pytest.mark.asyncio
    async def test_creates_source(self, async_client):
        """Creates an EPG source."""
        mock_client = AsyncMock()
        mock_client.create_epg_source.return_value = {"id": 3, "name": "New EPG"}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post("/api/epg/sources", json={
                "name": "New EPG",
                "url": "http://example.com/epg.xml",
            })

        assert response.status_code == 200
        assert response.json()["name"] == "New EPG"

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_400_not_500(self, async_client):
        """A Dispatcharr 4xx on create (bad body / missing required field)
        maps to 400 with the upstream detail, not an opaque 500 (bd-1wq7z.22).

        create_epg_source raises httpx.HTTPStatusError via raise_for_status().
        """
        mock_client = AsyncMock()
        request = httpx.Request("POST", "http://disp/api/epg/sources/")
        upstream = httpx.Response(
            400, request=request,
            text='{"source_type": ["This field is required."]}',
        )
        mock_client.create_epg_source.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post("/api/epg/sources", json={
                "name": "New EPG",
                "url": "http://example.com/epg.xml",
            })

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "source_type" in detail
        assert "required" in detail

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error on create stays a 500 (bd-1wq7z.22)."""
        mock_client = AsyncMock()
        mock_client.create_epg_source.side_effect = RuntimeError("boom")

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post("/api/epg/sources", json={
                "name": "New EPG",
                "url": "http://example.com/epg.xml",
            })

        assert response.status_code == 500


class TestUpdateEPGSource:
    """Tests for PATCH /api/epg/sources/{source_id}."""

    @pytest.mark.asyncio
    async def test_updates_source(self, async_client):
        """Updates an EPG source."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "Old Name"}
        mock_client.update_epg_source.return_value = {"id": 1, "name": "New Name"}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.patch("/api/epg/sources/1", json={
                "name": "New Name",
            })

        assert response.status_code == 200
        mock_client.update_epg_source.assert_called_once_with(1, {"name": "New Name"})

    @pytest.mark.asyncio
    async def test_missing_source_returns_404_not_500(self, async_client):
        """Updating a nonexistent EPG source surfaces upstream 404 as 404, not 500
        (bd-lq38l.4). The before-state get_epg_source raises 404."""
        request = httpx.Request("GET", "http://disp/api/epg/sources/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.patch("/api/epg/sources/999", json={"name": "New Name"})

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error stays a 500 (bd-lq38l.4)."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = RuntimeError("boom")

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.patch("/api/epg/sources/1", json={"name": "New Name"})

        assert response.status_code == 500


class TestDeleteEPGSource:
    """Tests for DELETE /api/epg/sources/{source_id}."""

    @pytest.mark.asyncio
    async def test_deletes_source(self, async_client):
        """Deletes an EPG source."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "XMLTV"}
        mock_client.delete_epg_source.return_value = None

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.delete("/api/epg/sources/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_missing_source_returns_404_not_500(self, async_client):
        """Deleting a nonexistent EPG source surfaces upstream 404 as 404, not 500
        (bd-lq38l.4)."""
        request = httpx.Request("GET", "http://disp/api/epg/sources/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.delete("/api/epg/sources/999")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


class TestRefreshEPGSource:
    """Tests for POST /api/epg/sources/{source_id}/refresh."""

    @pytest.mark.asyncio
    async def test_triggers_refresh(self, async_client):
        """Triggers EPG source refresh."""
        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {
            "id": 1, "name": "XMLTV", "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_client.refresh_epg_source.return_value = {"status": "refreshing"}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.asyncio.create_task"):
            response = await async_client.post("/api/epg/sources/1/refresh")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_source_returns_404_not_500(self, async_client):
        """Refreshing a nonexistent EPG source surfaces upstream 404 as 404, not
        500 (bd-lq38l.4). The initial get_epg_source raises 404."""
        request = httpx.Request("GET", "http://disp/api/epg/sources/999/")
        upstream = httpx.Response(404, request=request, text='{"detail": "Not found."}')
        mock_client = AsyncMock()
        mock_client.get_epg_source.side_effect = httpx.HTTPStatusError(
            "404 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.send_alert", new=AsyncMock()), \
             patch("routers.epg.asyncio.create_task"):
            response = await async_client.post("/api/epg/sources/999/refresh")

        assert response.status_code == 404
        assert "Not found" in response.json()["detail"]


class TestTriggerEPGImport:
    """Tests for POST /api/epg/import."""

    @pytest.mark.asyncio
    async def test_triggers_import(self, async_client):
        """Triggers EPG data import and returns the forwarded result."""
        mock_client = AsyncMock()
        mock_client.trigger_epg_import.return_value = {"status": "importing"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/import")

        assert response.status_code == 200
        assert response.json() == {"status": "importing"}
        mock_client.trigger_epg_import.assert_called_once_with()


class TestGetEPGData:
    """Tests for GET /api/epg/data."""

    @pytest.mark.asyncio
    async def test_returns_data(self, async_client):
        """Returns EPG data with pagination."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = {
            "results": [], "count": 0,
        }

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data")

        assert response.status_code == 200
        mock_client.get_epg_data.assert_called_once_with(
            page=1, page_size=100, search=None, epg_source=None,
        )

    @pytest.mark.asyncio
    async def test_passes_filters(self, async_client):
        """Passes search and source filters."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = {"results": [], "count": 0}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data", params={
                "search": "ESPN", "epg_source": 1,
            })

        assert response.status_code == 200
        mock_client.get_epg_data.assert_called_once_with(
            page=1, page_size=100, search="ESPN", epg_source=1,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page", [0, -1])
    async def test_invalid_page_returns_422_not_500(self, async_client, page):
        """page < 1 is rejected by validation (422), never passed upstream to
        become a 500 (bead enhancedchannelmanager-g4z2h, systemic sibling of
        1a5mf)."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = {"results": [], "count": 0}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data", params={"page": page})

        assert response.status_code == 422
        mock_client.get_epg_data.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page_size", [0, -5, 1001])
    async def test_invalid_page_size_returns_422_not_500(self, async_client, page_size):
        """page_size out of [1, 1000] is rejected by validation (422)."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = {"results": [], "count": 0}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get(
                "/api/epg/data", params={"page_size": page_size}
            )

        assert response.status_code == 422
        mock_client.get_epg_data.assert_not_called()


class TestGetEPGDataById:
    """Tests for GET /api/epg/data/{data_id}."""

    @pytest.mark.asyncio
    async def test_returns_entry(self, async_client):
        """Returns a single EPG data entry."""
        mock_client = AsyncMock()
        mock_client.get_epg_data_by_id.return_value = {"id": 42, "name": "ESPN"}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/data/42")

        assert response.status_code == 200
        mock_client.get_epg_data_by_id.assert_called_once_with(42)


class TestGetEPGGrid:
    """Tests for GET /api/epg/grid."""

    @pytest.mark.asyncio
    async def test_returns_grid(self, async_client):
        """Returns EPG grid data forwarded verbatim from the client."""
        mock_client = AsyncMock()
        mock_client.get_epg_grid.return_value = {"channels": ["ch1"], "programmes": ["prog1"]}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/grid")

        assert response.status_code == 200
        assert response.json() == {"channels": ["ch1"], "programmes": ["prog1"]}
        mock_client.get_epg_grid.assert_called_once_with(start=None, end=None)

    @pytest.mark.asyncio
    async def test_handles_timeout(self, async_client):
        """Returns 504 on ReadTimeout."""
        import httpx as httpx_mod
        mock_client = AsyncMock()
        mock_client.get_epg_grid.side_effect = httpx_mod.ReadTimeout("Timed out")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/grid")

        assert response.status_code == 504


class TestGetEPGLCN:
    """Tests for GET /api/epg/lcn."""

    @pytest.mark.asyncio
    async def test_returns_404_when_no_xmltv_sources(self, async_client):
        """Returns 404 when no XMLTV sources exist."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "source_type": "gracenote", "url": None},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/lcn", params={"tvg_id": "ESPN.us"})

        assert response.status_code == 404

    async def _lookup(self, async_client, channels_xml: str, tvg_id: str = "ESPN.us"):
        """Run GET /api/epg/lcn against a single XMLTV source serving channels_xml."""
        url = "http://epg.example/xmltv.xml"
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "name": "XMLTV", "source_type": "xmltv", "url": url},
        ]
        fake_http = _FakeEPGHTTPClient({url: _xmltv(channels_xml)})
        with patch("routers.epg.get_client", return_value=mock_client), \
                patch("routers.epg.httpx.AsyncClient", return_value=fake_http):
            return await async_client.get("/api/epg/lcn", params={"tvg_id": tvg_id})

    @pytest.mark.asyncio
    async def test_reads_gnid_primary(self, async_client):
        """<gnid> present -> returns the gnid value under the legacy 'lcn' key."""
        resp = await self._lookup(
            async_client,
            '<channel id="ESPN.us"><display-name>ESPN</display-name><gnid>12345</gnid></channel>',
        )
        assert resp.status_code == 200
        assert resp.json()["lcn"] == "12345"

    @pytest.mark.asyncio
    async def test_falls_back_to_lcn_legacy(self, async_client):
        """Only <lcn> present (legacy EPG) -> returns the lcn value."""
        resp = await self._lookup(
            async_client,
            '<channel id="ESPN.us"><lcn>206</lcn></channel>',
        )
        assert resp.status_code == 200
        assert resp.json()["lcn"] == "206"

    @pytest.mark.asyncio
    async def test_gnid_wins_over_lcn_regardless_of_order(self, async_client):
        """Both present, with <lcn> first in document order -> <gnid> still wins."""
        resp = await self._lookup(
            async_client,
            '<channel id="ESPN.us"><lcn>206</lcn><gnid>99999</gnid></channel>',
        )
        assert resp.status_code == 200
        assert resp.json()["lcn"] == "99999"

    @pytest.mark.asyncio
    async def test_neither_present_returns_404(self, async_client):
        """Neither <gnid> nor <lcn> present -> 404 (unchanged behavior)."""
        resp = await self._lookup(
            async_client,
            '<channel id="ESPN.us"><display-name>ESPN</display-name></channel>',
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_whitespace_gnid_falls_back_to_lcn(self, async_client):
        """Whitespace-only <gnid> is treated as empty -> falls back to <lcn>."""
        resp = await self._lookup(
            async_client,
            '<channel id="ESPN.us"><gnid>   </gnid><lcn>206</lcn></channel>',
        )
        assert resp.status_code == 200
        assert resp.json()["lcn"] == "206"


class TestBatchLCN:
    """Tests for POST /api/epg/lcn/batch."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_items(self, async_client):
        """Returns empty results for empty items list."""
        response = await async_client.post("/api/epg/lcn/batch", json={
            "items": [],
        })

        assert response.status_code == 200
        assert response.json()["results"] == {}

    async def _batch_lookup(self, async_client, channels_xml: str, tvg_ids: list[str]):
        """Run POST /api/epg/lcn/batch against a single XMLTV source."""
        url = "http://epg.example/xmltv.xml"
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "name": "XMLTV", "source_type": "xmltv", "url": url},
        ]
        fake_http = _FakeEPGHTTPClient({url: _xmltv(channels_xml)})
        with patch("routers.epg.get_client", return_value=mock_client), \
                patch("routers.epg.httpx.AsyncClient", return_value=fake_http):
            return await async_client.post("/api/epg/lcn/batch", json={
                "items": [{"tvg_id": t} for t in tvg_ids],
            })

    @pytest.mark.asyncio
    async def test_reads_gnid_primary(self, async_client):
        """<gnid> present -> returns the gnid value under the legacy 'lcn' key."""
        resp = await self._batch_lookup(
            async_client,
            '<channel id="ESPN.us"><gnid>12345</gnid></channel>',
            ["ESPN.us"],
        )
        assert resp.status_code == 200
        assert resp.json()["results"]["ESPN.us"]["lcn"] == "12345"

    @pytest.mark.asyncio
    async def test_falls_back_to_lcn_legacy(self, async_client):
        """Only <lcn> present (legacy EPG) -> returns the lcn value."""
        resp = await self._batch_lookup(
            async_client,
            '<channel id="ESPN.us"><lcn>206</lcn></channel>',
            ["ESPN.us"],
        )
        assert resp.status_code == 200
        assert resp.json()["results"]["ESPN.us"]["lcn"] == "206"

    @pytest.mark.asyncio
    async def test_gnid_wins_over_lcn_regardless_of_order(self, async_client):
        """Both present, <lcn> first in document order -> <gnid> still wins."""
        resp = await self._batch_lookup(
            async_client,
            '<channel id="ESPN.us"><lcn>206</lcn><gnid>99999</gnid></channel>',
            ["ESPN.us"],
        )
        assert resp.status_code == 200
        assert resp.json()["results"]["ESPN.us"]["lcn"] == "99999"

    @pytest.mark.asyncio
    async def test_mixed_channels_in_one_document(self, async_client):
        """A mix across multiple <channel> ids in one document: gnid, legacy lcn,
        both (gnid wins), and neither (absent from results)."""
        channels = (
            '<channel id="GNID.us"><gnid>11111</gnid></channel>'
            '<channel id="LCN.us"><lcn>206</lcn></channel>'
            '<channel id="BOTH.us"><lcn>500</lcn><gnid>22222</gnid></channel>'
            '<channel id="NONE.us"><display-name>none</display-name></channel>'
        )
        resp = await self._batch_lookup(
            async_client, channels,
            ["GNID.us", "LCN.us", "BOTH.us", "NONE.us"],
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert results["GNID.us"]["lcn"] == "11111"
        assert results["LCN.us"]["lcn"] == "206"
        assert results["BOTH.us"]["lcn"] == "22222"
        assert "NONE.us" not in results


class TestLinkChannelToEPG:
    """Tests for POST /api/epg/channels/{channel_id}/link.

    Closes the Scenario 6 seam: picking a 'multiple candidate' tvg_id/epg_data_id
    and establishing the channel's epg_data link (so set_logo_from_epg works).
    """

    @pytest.mark.asyncio
    async def test_links_by_explicit_epg_data_id(self, async_client):
        """Sets the channel's epg_data_id via update_channel when given an id."""
        mock_client = AsyncMock()
        mock_client.update_channel.return_value = {
            "id": 7, "name": "ESPN", "epg_data_id": 42,
        }

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"epg_data_id": 42},
            )

        assert response.status_code == 200
        # The link is established with the exact-match mechanism: PATCH epg_data_id.
        mock_client.update_channel.assert_called_once_with(7, {"epg_data_id": 42})
        mock_client.get_epg_data.assert_not_called()
        assert response.json()["epg_data_id"] == 42

    @pytest.mark.asyncio
    async def test_links_by_tvg_id_resolves_to_epg_data_row(self, async_client):
        """Resolves a tvg_id to its EPG data row id, then sets epg_data_id."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = [
            {"id": 11, "tvg_id": "CNN.us", "name": "CNN"},
            {"id": 99, "tvg_id": "ESPN.us", "name": "ESPN"},
        ]
        mock_client.update_channel.return_value = {
            "id": 7, "name": "ESPN", "epg_data_id": 99,
        }

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"tvg_id": "ESPN.us"},
            )

        assert response.status_code == 200
        mock_client.get_epg_data.assert_called_once_with(search="ESPN.us")
        mock_client.update_channel.assert_called_once_with(7, {"epg_data_id": 99})

    @pytest.mark.asyncio
    async def test_epg_data_id_wins_over_tvg_id(self, async_client):
        """When both are supplied, epg_data_id is used (no tvg_id resolution)."""
        mock_client = AsyncMock()
        mock_client.update_channel.return_value = {"id": 7, "epg_data_id": 5}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/channels/7/link",
                json={"epg_data_id": 5, "tvg_id": "ESPN.us"},
            )

        assert response.status_code == 200
        mock_client.get_epg_data.assert_not_called()
        mock_client.update_channel.assert_called_once_with(7, {"epg_data_id": 5})

    @pytest.mark.asyncio
    async def test_missing_both_returns_400(self, async_client):
        """Returns 400 when neither epg_data_id nor tvg_id is provided."""
        mock_client = AsyncMock()

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/channels/7/link", json={})

        assert response.status_code == 400
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolvable_tvg_id_returns_404(self, async_client):
        """Returns 404 when no EPG data row matches the given tvg_id."""
        mock_client = AsyncMock()
        mock_client.get_epg_data.return_value = [
            {"id": 11, "tvg_id": "CNN.us", "name": "CNN"},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"tvg_id": "NOPE.us"},
            )

        assert response.status_code == 404
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_400_not_500(self, async_client):
        """A Dispatcharr 4xx on the PATCH maps to a clean 4xx, not an opaque 500."""
        request = httpx.Request("PATCH", "http://x/api/channels/channels/7/")
        bad_response = httpx.Response(400, request=request, text="bad epg_data_id")
        mock_client = AsyncMock()
        mock_client.update_channel.side_effect = httpx.HTTPStatusError(
            "400", request=request, response=bad_response,
        )

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"epg_data_id": 42},
            )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-HTTP error still surfaces as 500."""
        mock_client = AsyncMock()
        mock_client.update_channel.side_effect = Exception("boom")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/channels/7/link", json={"epg_data_id": 42},
            )

        assert response.status_code == 500


class TestSDLineups:
    """Tests for the Schedules Direct lineup proxy endpoints."""

    @pytest.mark.asyncio
    async def test_lists_lineups(self, async_client):
        """GET forwards to the client and returns Dispatcharr's payload."""
        mock_client = AsyncMock()
        mock_client.get_sd_lineups.return_value = {
            "lineups": [{"lineup": "USA-NJ29486-X", "name": "Comcast NJ"}],
            "max_lineups": 4,
            "changes_remaining": 5,
        }

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources/1/sd-lineups")

        assert response.status_code == 200
        body = response.json()
        assert body["max_lineups"] == 4
        assert body["lineups"][0]["lineup"] == "USA-NJ29486-X"
        mock_client.get_sd_lineups.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_adds_lineup_forwards_body(self, async_client):
        """POST forwards the lineup id and journals the change."""
        mock_client = AsyncMock()
        mock_client.add_sd_lineup.return_value = {"success": True}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/sources/1/sd-lineups", json={"lineup": "USA-NJ29486-X"},
            )

        assert response.status_code == 200
        mock_client.add_sd_lineup.assert_called_once_with(1, "USA-NJ29486-X")

    @pytest.mark.asyncio
    async def test_removes_lineup_forwards_body(self, async_client):
        """DELETE forwards the lineup id."""
        mock_client = AsyncMock()
        mock_client.delete_sd_lineup.return_value = {"success": True}

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.request(
                "DELETE", "/api/epg/sources/1/sd-lineups", json={"lineup": "USA-NJ29486-X"},
            )

        assert response.status_code == 200
        mock_client.delete_sd_lineup.assert_called_once_with(1, "USA-NJ29486-X")

    @pytest.mark.asyncio
    async def test_searches_lineups_forwards_location(self, async_client):
        """POST search forwards country + postalcode."""
        mock_client = AsyncMock()
        mock_client.search_sd_lineups.return_value = {"lineups": []}

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/epg/sources/1/sd-lineups/search",
                json={"country": "USA", "postalcode": "07030"},
            )

        assert response.status_code == 200
        mock_client.search_sd_lineups.assert_called_once_with(1, "USA", "07030")

    @pytest.mark.asyncio
    async def test_upstream_4xx_surfaces_as_4xx(self, async_client):
        """A Dispatcharr 4xx (e.g. SD daily add limit) maps to a 4xx, not 500."""
        mock_client = AsyncMock()
        request = httpx.Request("POST", "http://disp/api/epg/sources/1/sd-lineups/")
        upstream = httpx.Response(
            400, request=request, text='{"detail": "Daily lineup change limit reached"}',
        )
        mock_client.add_sd_lineup.side_effect = httpx.HTTPStatusError(
            "400 Client Error", request=request, response=upstream
        )

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.post(
                "/api/epg/sources/1/sd-lineups", json={"lineup": "USA-NJ29486-X"},
            )

        assert response.status_code == 400
        assert "limit" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_genuine_server_error_still_500(self, async_client):
        """A non-upstream error stays a 500."""
        mock_client = AsyncMock()
        mock_client.get_sd_lineups.side_effect = RuntimeError("boom")

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/sources/1/sd-lineups")

        assert response.status_code == 500


class TestProgramPoster:
    """Tests for GET /api/epg/programs/{program_id}/poster."""

    @pytest.mark.asyncio
    async def test_proxies_poster_bytes_and_content_type(self, async_client):
        """Streams Dispatcharr's bytes through with its Content-Type."""
        mock_client = AsyncMock()
        upstream = httpx.Response(
            200, content=b"\xff\xd8\xff-image-bytes",
            headers={"content-type": "image/png"},
        )
        mock_client.get_program_poster.return_value = upstream

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/programs/42/poster")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\xff\xd8\xff-image-bytes"
        mock_client.get_program_poster.assert_called_once_with(42)


class TestMatchChannelsToEPG:
    """Tests for POST /api/epg/match — server-side source-priority ranking."""

    @staticmethod
    def _client(sources):
        """Mock client: one ESPN channel, two tying ESPN.us EPG entries."""
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = sources
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [1]}],
        }
        mock_client.get_streams.return_value = {
            "results": [{"id": 1, "name": "US | ESPN",
                         "channel_group_name": "US Sports"}],
            "next": None,
        }
        mock_client.get_epg_data.return_value = [
            {"id": 100, "name": "ESPN", "tvg_id": "ESPN.us",
             "epg_source": {"id": 2, "name": "Backup"}},
            {"id": 101, "name": "ESPN", "tvg_id": "ESPN.us",
             "epg_source": {"id": 1, "name": "Primary"}},
        ]
        return mock_client

    @staticmethod
    def _best(data):
        bucket = data["multiple"] or data["exact"]
        return bucket[0]["matches"][0]

    @pytest.mark.asyncio
    async def test_priority_resolved_server_side(self, async_client):
        """The preferred source (higher priority) wins the tie, from the server."""
        from cache import get_cache
        get_cache().clear()
        sources = [
            {"id": 1, "name": "Primary", "priority": 10},
            {"id": 2, "name": "Backup", "priority": 1},
        ]
        mock_client = self._client(sources)
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={
                "channel_ids": [1],
            })
        assert response.status_code == 200
        mock_client.get_epg_sources.assert_awaited()
        assert self._best(response.json())["epg_source"]["id"] == 1

    @pytest.mark.asyncio
    async def test_priority_change_busts_cache(self, async_client):
        """Reordering source priority changes the cache key and the winner."""
        from cache import get_cache
        get_cache().clear()
        # First call: source 1 preferred.
        mock_a = self._client([
            {"id": 1, "name": "Primary", "priority": 10},
            {"id": 2, "name": "Backup", "priority": 1},
        ])
        with patch("routers.epg.get_client", return_value=mock_a):
            first = await async_client.post("/api/epg/match", json={"channel_ids": [1]})
        assert self._best(first.json())["epg_source"]["id"] == 1

        # Second call: priorities flipped — must NOT serve the stale cached result.
        mock_b = self._client([
            {"id": 1, "name": "Primary", "priority": 1},
            {"id": 2, "name": "Backup", "priority": 10},
        ])
        with patch("routers.epg.get_client", return_value=mock_b):
            second = await async_client.post("/api/epg/match", json={"channel_ids": [1]})
        # Fresh matching ran (cache busted) and the new preferred source wins.
        mock_b.get_epg_data.assert_awaited()
        assert self._best(second.json())["epg_source"]["id"] == 2

    @pytest.mark.asyncio
    async def test_selected_source_filter_enforced(self, async_client):
        """m4hp1 Part 1: with epg_source_ids=[1], entries from source 2 are
        excluded EVEN IF Dispatcharr ignores the epg_source query param and
        returns mixed-source data. Defense-in-depth filter in the endpoint.
        """
        from cache import get_cache
        get_cache().clear()
        sources = [
            {"id": 1, "name": "Selected", "priority": 10},
            {"id": 2, "name": "Other", "priority": 1},
        ]
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = sources
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [1]}],
        }
        mock_client.get_streams.return_value = {
            "results": [{"id": 1, "name": "US | ESPN",
                         "channel_group_name": "US Sports"}],
            "next": None,
        }
        # Simulate Dispatcharr IGNORING the epg_source filter: every per-source
        # fetch returns BOTH a source-1 and a source-2 ESPN entry.
        def _mixed(*args, **kwargs):
            return [
                {"id": 100, "name": "ESPN", "tvg_id": "ESPN.us",
                 "epg_source": {"id": 1, "name": "Selected"}},
                {"id": 200, "name": "ESPN", "tvg_id": "ESPN.us",
                 "epg_source": {"id": 2, "name": "Other"}},
            ]
        mock_client.get_epg_data.side_effect = _mixed

        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={
                "channel_ids": [1],
                "epg_source_ids": [1],
            })
        assert response.status_code == 200
        data = response.json()
        # Gather every candidate's source id across all buckets.
        all_source_ids = set()
        for bucket in (data["exact"], data["multiple"], data["none"]):
            for ch in bucket:
                for m in ch.get("matches", []):
                    all_source_ids.add(m["epg_source"]["id"])
        # Only the selected source may appear; source 2 must be filtered out.
        assert 2 not in all_source_ids
        assert all_source_ids == {1}

    @pytest.mark.asyncio
    async def test_deprecated_source_order_ignored(self, async_client):
        """A client-sent source_order is ignored; server priority still wins."""
        from cache import get_cache
        get_cache().clear()
        sources = [
            {"id": 1, "name": "Primary", "priority": 10},
            {"id": 2, "name": "Backup", "priority": 1},
        ]
        mock_client = self._client(sources)
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={
                "channel_ids": [1],
                # Deprecated: tries to make source 2 win — must be ignored.
                "source_order": [2, 1],
            })
        assert response.status_code == 200
        assert self._best(response.json())["epg_source"]["id"] == 1

    @pytest.mark.asyncio
    async def test_paginates_beyond_single_page(self, async_client):
        """bead vznut.6: /match fetches ALL channel pages, not just the first
        page_size=10000 page (code-review nit #4) -- total_channels reflects
        the full requested set even when it spans multiple Dispatcharr pages."""
        from cache import get_cache
        get_cache().clear()
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = []
        mock_client.get_channels.side_effect = [
            _channels_page([{"id": 1, "name": "ESPN", "streams": []}], count=2),
            _channels_page([{"id": 2, "name": "CNN", "streams": []}], count=2),
        ]
        mock_client.get_streams.return_value = {"results": [], "next": None}
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["total_channels"] == 2
        assert mock_client.get_channels.call_count == 2

    @pytest.mark.asyncio
    async def test_single_page_small_fleet_unchanged(self, async_client):
        """A fleet that fits in one page still resolves with exactly one
        get_channels() call."""
        from cache import get_cache
        get_cache().clear()
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = []
        mock_client.get_channels.return_value = _channels_page(
            [{"id": 1, "name": "ESPN", "streams": []}]
        )
        mock_client.get_streams.return_value = {"results": [], "next": None}
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={})
        assert response.status_code == 200
        assert response.json()["summary"]["total_channels"] == 1
        assert mock_client.get_channels.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_fleet(self, async_client):
        """An empty fleet returns empty match buckets and zero total_channels,
        without error."""
        from cache import get_cache
        get_cache().clear()
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = []
        mock_client.get_channels.return_value = _channels_page([])
        mock_client.get_streams.return_value = {"results": [], "next": None}
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["exact"] == data["multiple"] == data["none"] == []
        assert data["summary"]["total_channels"] == 0
        assert mock_client.get_channels.call_count == 1


class TestMatchCachePersistsAcrossRepeatCalls:
    """bd-41pcv: the /match response cache exists so a repeat request for the
    same channel/source/priority selection (e.g. re-opening the bulk-assign
    modal, or its "rerun analysis" button) skips the full channel/stream/EPG
    refetch + rematch. That perf behavior must survive the staleness fix
    below — this pins it so a future change doesn't regress it."""

    @pytest.mark.asyncio
    async def test_identical_repeat_request_is_cache_hit(self, async_client):
        from cache import get_cache
        get_cache().clear()
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "name": "Primary", "priority": 10},
        ]
        mock_client.get_channels.return_value = {
            "results": [{"id": 1, "name": "ESPN", "streams": [1]}],
        }
        mock_client.get_streams.return_value = {
            "results": [{"id": 1, "name": "US | ESPN",
                         "channel_group_name": "US Sports"}],
            "next": None,
        }
        mock_client.get_epg_data.return_value = [
            {"id": 100, "name": "ESPN", "tvg_id": "ESPN.us",
             "epg_source": {"id": 1, "name": "Primary"}},
        ]

        with patch("routers.epg.get_client", return_value=mock_client):
            first = await async_client.post("/api/epg/match", json={
                "channel_ids": [1], "epg_source_ids": [1],
            })
            second = await async_client.post("/api/epg/match", json={
                "channel_ids": [1], "epg_source_ids": [1],
            })

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        # The expensive fetch + rematch only happened once; the second call
        # was served from cache.
        assert mock_client.get_epg_data.await_count == 1


class TestMatchCacheInvalidatedOnSourceChange:
    """bd-41pcv: PO-reported bug — after editing an EPG source's data in
    place (same source id, e.g. swapping its URL or letting a refresh pull
    new content) and running a fresh bulk match, results still showed
    TVG-IDs from the OLD data. The /match cache key is built only from
    channel/source IDs + priority ranks, none of which change when a
    source's *content* changes, so a match within the TTL window served the
    pre-swap response. update/delete/refresh-completion must bust the
    epg_match: cache prefix so this can't happen."""

    @pytest.mark.asyncio
    async def test_update_busts_match_cache(self, async_client):
        """A same-selection match run after an in-place source update must
        reflect the post-update data, not a cached pre-update response."""
        from cache import get_cache
        get_cache().clear()
        sources = [{"id": 1, "name": "Primary", "priority": 10}]

        def _match_client(epg_data):
            mock_client = AsyncMock()
            mock_client.get_epg_sources.return_value = sources
            mock_client.get_channels.return_value = {
                "results": [{"id": 1, "name": "ESPN", "streams": [1]}],
            }
            mock_client.get_streams.return_value = {
                "results": [{"id": 1, "name": "US | ESPN",
                             "channel_group_name": "US Sports"}],
                "next": None,
            }
            mock_client.get_epg_data.return_value = epg_data
            return mock_client

        old_client = _match_client([
            {"id": 100, "name": "ESPN", "tvg_id": "OLD.us",
             "epg_source": {"id": 1, "name": "Primary"}},
        ])
        with patch("routers.epg.get_client", return_value=old_client):
            first = await async_client.post("/api/epg/match", json={
                "channel_ids": [1], "epg_source_ids": [1],
            })
        assert TestMatchChannelsToEPG._best(first.json())["tvg_id"] == "OLD.us"

        # PO edits the source in place -- same id, new content.
        update_client = AsyncMock()
        update_client.get_epg_source.return_value = {"id": 1, "name": "Primary"}
        update_client.update_epg_source.return_value = {"id": 1, "name": "Primary"}
        with patch("routers.epg.get_client", return_value=update_client), \
             patch("routers.epg.journal"):
            update_resp = await async_client.patch("/api/epg/sources/1", json={
                "url": "http://example.com/new-epg.xml",
            })
        assert update_resp.status_code == 200

        # Same channel/source/priority selection as the first call -- a
        # byte-identical cache key pre-fix -- but the source now serves
        # fresh (post-swap) data.
        new_client = _match_client([
            {"id": 101, "name": "ESPN", "tvg_id": "NEW.us",
             "epg_source": {"id": 1, "name": "Primary"}},
        ])
        with patch("routers.epg.get_client", return_value=new_client):
            second = await async_client.post("/api/epg/match", json={
                "channel_ids": [1], "epg_source_ids": [1],
            })
        new_client.get_epg_data.assert_awaited()
        assert TestMatchChannelsToEPG._best(second.json())["tvg_id"] == "NEW.us"

    @pytest.mark.asyncio
    async def test_delete_busts_epg_match_cache_prefix(self, async_client):
        """Deleting a source must not leave a match response referencing it
        servable from cache."""
        from cache import get_cache
        cache = get_cache()
        cache.clear()
        cache.set("epg_match:123:456:789", {"stale": True})

        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {"id": 1, "name": "XMLTV"}
        mock_client.delete_epg_source.return_value = None

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.journal"):
            response = await async_client.delete("/api/epg/sources/1")

        assert response.status_code == 200
        assert cache.get("epg_match:123:456:789") is None

    @pytest.mark.asyncio
    async def test_refresh_completion_busts_epg_match_cache_prefix(self):
        """The background poller -- not the trigger endpoint -- is what
        actually confirms new data landed, so it must be the one that
        invalidates the cache. Simulates the ``updated_at`` timestamp
        changing on the very first poll tick."""
        from cache import get_cache
        from routers.epg import _poll_epg_refresh_completion
        cache = get_cache()
        cache.clear()
        cache.set("epg_match:123:456:789", {"stale": True})

        mock_client = AsyncMock()
        mock_client.get_epg_source.return_value = {
            "id": 1, "name": "XMLTV", "updated_at": "2026-06-30T12:00:00Z",
        }

        with patch("routers.epg.get_client", return_value=mock_client), \
             patch("routers.epg.asyncio.sleep", new=AsyncMock()), \
             patch("routers.epg.send_alert", new=AsyncMock()), \
             patch("routers.epg.journal"):
            await _poll_epg_refresh_completion(1, "XMLTV", "2026-06-30T11:00:00Z")

        assert cache.get("epg_match:123:456:789") is None


class TestAuditEpgDuplicates:
    """Tests for GET /api/epg/audit-duplicates (bead vznut.1)."""

    @pytest.mark.asyncio
    async def test_reports_shared_link_group(self, async_client):
        """Two channels on one epg_data_id surface as a shared-link group,
        enriched with the EPG row's name/tvg_id."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 10, "name": "USA Network", "epg_data_id": 500},
                {"id": 55, "name": "USA Network West", "epg_data_id": 500},
                {"id": 60, "name": "CNN", "epg_data_id": 501},
                {"id": 61, "name": "Unlinked", "epg_data_id": None},
            ],
        }
        mock_client.get_epg_data.return_value = [
            {"id": 500, "name": "USA Network East", "tvg_id": "USANetwork.us",
             "epg_source": {"id": 3, "name": "JESmann"}},
        ]
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        data = response.json()
        assert len(data["shared_links"]) == 1
        group = data["shared_links"][0]
        assert group["epg_data_id"] == 500
        assert group["count"] == 2
        assert group["epg_name"] == "USA Network East"
        assert group["tvg_id"] == "USANetwork.us"
        assert [c["channel_id"] for c in group["channels"]] == [10, 55]
        assert data["summary"] == {
            "shared_link_groups": 1,
            "affected_channels": 2,
            "total_channels": 4,
        }

    @pytest.mark.asyncio
    async def test_no_duplicates_returns_empty(self, async_client):
        """All-singleton fleet reports no shared links."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 1, "name": "ESPN", "epg_data_id": 100},
                {"id": 2, "name": "CNN", "epg_data_id": 101},
            ],
        }
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        data = response.json()
        assert data["shared_links"] == []
        assert data["summary"]["shared_link_groups"] == 0

    @pytest.mark.asyncio
    async def test_epg_enrichment_failure_degrades_gracefully(self, async_client):
        """If EPG-data fetch fails, the audit still reports linkage (name None)."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 1, "name": "A", "epg_data_id": 42},
                {"id": 2, "name": "B", "epg_data_id": 42},
            ],
        }
        mock_client.get_epg_data.side_effect = Exception("EPG source down")
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        group = response.json()["shared_links"][0]
        assert group["epg_data_id"] == 42
        assert group["epg_name"] is None

    @pytest.mark.asyncio
    async def test_channel_fetch_failure_returns_500(self, async_client):
        """A hard failure fetching channels surfaces as 500."""
        mock_client = AsyncMock()
        mock_client.get_channels.side_effect = Exception("Dispatcharr down")
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_read_only_no_mutation(self, async_client):
        """The audit must not call any mutating client method."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 1, "name": "A", "epg_data_id": 42},
                {"id": 2, "name": "B", "epg_data_id": 42},
            ],
        }
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        mock_client.update_channel.assert_not_called()
        mock_client.create_channel.assert_not_called()
        mock_client.delete_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_paginates_beyond_single_page(self, async_client):
        """bead vznut.6: a fleet spanning multiple pages is fully counted, not
        silently truncated at the first page (previously a single
        page_size=10000 call, code-review nit #4)."""
        mock_client = AsyncMock()
        mock_client.get_channels.side_effect = [
            _channels_page(
                [{"id": 1, "name": "USA Network", "epg_data_id": 500}], count=3
            ),
            _channels_page(
                [{"id": 2, "name": "USA Network West", "epg_data_id": 500}], count=3
            ),
            _channels_page(
                [{"id": 3, "name": "CNN", "epg_data_id": 501}], count=3
            ),
        ]
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["total_channels"] == 3
        assert mock_client.get_channels.call_count == 3
        group = data["shared_links"][0]
        assert group["epg_data_id"] == 500
        assert [c["channel_id"] for c in group["channels"]] == [1, 2]

    @pytest.mark.asyncio
    async def test_single_page_small_fleet_unchanged(self, async_client):
        """A fleet that fits in one page still resolves with exactly one
        get_channels() call -- pagination does not add a spurious extra
        round-trip for the common (small-fleet) case."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = _channels_page(
            [
                {"id": 1, "name": "ESPN", "epg_data_id": 100},
                {"id": 2, "name": "CNN", "epg_data_id": 101},
            ]
        )
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        assert response.json()["summary"]["total_channels"] == 2
        assert mock_client.get_channels.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_fleet(self, async_client):
        """An empty fleet reports zero channels and zero groups, without error."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = _channels_page([])
        mock_client.get_epg_data.return_value = []
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.get("/api/epg/audit-duplicates")
        assert response.status_code == 200
        data = response.json()
        assert data["shared_links"] == []
        assert data["summary"]["total_channels"] == 0
        assert mock_client.get_channels.call_count == 1


class TestMatchEmbedsSharedLinks:
    """The /match preview embeds the shared-EPG-link audit (bead vznut.1 PO decision)."""

    @pytest.mark.asyncio
    async def test_match_response_includes_shared_epg_links(self, async_client):
        """Two matched channels sharing one epg_data_id surface in the preview."""
        from cache import get_cache
        get_cache().clear()
        mock_client = AsyncMock()
        mock_client.get_epg_sources.return_value = [
            {"id": 1, "name": "Primary", "priority": 10},
        ]
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 1, "name": "USA Network", "streams": [], "epg_data_id": 500},
                {"id": 2, "name": "USA Network West", "streams": [], "epg_data_id": 500},
            ],
        }
        mock_client.get_streams.return_value = {"results": [], "next": None}
        mock_client.get_epg_data.return_value = [
            {"id": 500, "name": "USA Network East", "tvg_id": "USANetwork.us",
             "epg_source": {"id": 1, "name": "Primary"}},
        ]
        with patch("routers.epg.get_client", return_value=mock_client):
            response = await async_client.post("/api/epg/match", json={"channel_ids": [1, 2]})
        assert response.status_code == 200
        data = response.json()
        assert "shared_epg_links" in data
        assert data["summary"]["shared_link_groups"] == 1
        group = data["shared_epg_links"]["shared_links"][0]
        assert group["epg_data_id"] == 500
        assert [c["channel_id"] for c in group["channels"]] == [1, 2]
