"""GH1032 consumer integration: disposable HTTP upstream + real SQLite + API.

No production service/credentials. Account shape is recorded; status rows are
source-derived synthetic. Native ffprobe decoding is substituted (not installed
on the test host); admission, HTTP/SSRF, bitrate, persistence and serialization
run for real. This does not prove upstream atomic reservation or native decoding.
"""

import asyncio
import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from security.stream_outbound import stream_request
from stream_prober import StreamProber

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]
FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def upstream():
    account = json.loads(
        (FIXTURES / "bd_igqcy/dispatcharr_v0272_m3u_account.json").read_text()
    )
    account["max_streams"] = account["profiles"][0]["max_streams"] = 2
    state = SimpleNamespace(
        account=account, active=2, media_requests=0, status_error=False
    )
    state.accounts = [account]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            code = 200
            if path == "/api/m3u/accounts/":
                body = state.accounts
            elif path == "/proxy/ts/status":
                body = {
                    "channels": [
                        {"m3u_profile_id": 12, "client_count": 9}
                        for _ in range(state.active)
                    ]
                }
                code = 503 if state.status_error else 200
            elif path == "/api/channels/streams/7/":
                body = state.stream
            elif path == "/api/channels/streams/":
                body = {"results": [state.stream], "next": None, "count": 1}
            elif path == "/media.ts":
                state.media_requests += 1
                data = b"\x47" + b"\0" * 65535
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Type", "video/mp2t")
                self.end_headers()
                self.wfile.write(data)
                return
            else:
                body, code = {"error": "unimplemented test endpoint"}, 404
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    state.stream = {
        "id": 7,
        "name": "Disposable",
        "url": state.url + "/media.ts",
        "m3u_account": 11,
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
async def integrated_probe(upstream, async_client, tmp_path, monkeypatch):
    client = DispatcharrClient(
        DispatcharrSettings(
            url=upstream.url,
            auth_method="api_key",
            dispatcharr_api_key="disposable-test-key",
        )
    )
    prober = StreamProber(client, bitrate_sample_duration=0.01)
    monkeypatch.setattr("stream_prober.PROBE_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr("routers.stream_stats.ensure_prober", lambda: prober)
    monkeypatch.setattr("stream_prober.get_prober", lambda: prober)
    # Lock prior health in the actual disposable DB before refusing admission.
    prober._save_probe_result(
        7, "Prior healthy measurement", {"streams": []}, "success", None, 1234
    )
    try:
        yield prober
    finally:
        await client.close()


@pytest.mark.parametrize("lane", ["single", "bulk", "quality", "queued"])
async def test_saturated_entrypoints_do_not_open_media_or_change_persisted_health(
    upstream, integrated_probe, async_client, lane
):
    prober = integrated_probe
    if lane == "single":
        response = await async_client.post("/api/stream-stats/probe/7")
        assert response.status_code == 200
        assert response.json()["probe_status"] == "skipped"
    elif lane == "bulk":
        await prober.probe_streams_by_ids([7], allow_reorder_after_probe=False)
        assert prober.get_probe_progress()["skipped_count"] == 1
    else:
        from channel_pipeline_engine import ChannelPipelineEngine

        engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
        engine._stream_stats_cache = {}
        engine._load_stream_stats = AsyncMock()
        ctx = SimpleNamespace(
            stream_id=7, stream_url=upstream.stream["url"], stream_name="Disposable"
        )
        rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
        results = {"execution_log": [], "dry_run_results": []}
        if lane == "quality":
            await engine._probe_unprobed_streams(
                [(ctx, rule, [], [])], [rule], results, False
            )
        else:
            await engine._batch_probe_streams({7}, [ctx], results, False)
        assert (
            "1 skipped"
            in results["execution_log"][-1]["actions_executed"][0]["description"]
        )
    assert upstream.media_requests == 0
    saved = (await async_client.get("/api/stream-stats/7")).json()
    assert saved["probe_status"] == "success"
    assert saved["video_bitrate"] == 1234
    assert saved["stream_name"] == "Prior healthy measurement"
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


@pytest.mark.parametrize("lane", ["quality", "queued"])
@pytest.mark.parametrize("accountless", [False, True])
async def test_http_pipeline_reassignment_rejects_stale_route_before_media(
    upstream, integrated_probe, async_client, monkeypatch, lane, accountless
):
    from channel_pipeline_engine import ChannelPipelineEngine

    prober = integrated_probe
    old_url = upstream.stream["url"]
    upstream.active = 1
    upstream.account["max_streams"] = upstream.account["profiles"][0]["max_streams"] = 1
    fresh_account = deepcopy(upstream.account)
    fresh_account.update(id=20, max_streams=2)
    fresh_account["profiles"][0].update(id=21, max_streams=2)
    upstream.accounts.append(fresh_account)
    upstream.stream.update(
        url=upstream.url + "/new-media.ts", m3u_account=None if accountless else 20
    )

    async def media(url, **_kwargs):
        async with stream_request(url) as response:
            response.raise_for_status()
            await response.aread()
        return {"streams": []}

    monkeypatch.setattr(prober, "_run_ffprobe", media)
    engine = ChannelPipelineEngine.__new__(ChannelPipelineEngine)
    engine._stream_stats_cache = {}
    engine._load_stream_stats = AsyncMock()
    ctx = SimpleNamespace(stream_id=7, stream_url=old_url, stream_name="Stale route")
    rule = SimpleNamespace(id=1, sort_field="quality", probe_on_sort=True)
    results = {"execution_log": [], "dry_run_results": []}
    if lane == "quality":
        await engine._probe_unprobed_streams(
            [(ctx, rule, [], [])], [rule], results, False
        )
    else:
        await engine._batch_probe_streams({7}, [ctx], results, False)
    assert (
        "1 skipped"
        in results["execution_log"][-1]["actions_executed"][0]["description"]
    )
    assert upstream.media_requests == 0
    saved = (await async_client.get("/api/stream-stats/7")).json()
    assert saved["probe_status"] == "success"
    assert saved["video_bitrate"] == 1234
    assert saved["stream_name"] == "Prior healthy measurement"
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


async def test_real_http_one_free_slot_and_later_viewer_arrival(
    upstream, integrated_probe, async_client, monkeypatch
):
    prober = integrated_probe
    upstream.active = 1
    started, release = asyncio.Event(), asyncio.Event()
    metadata = json.loads((FIXTURES / "gh980_generated_local_ffprobe.json").read_text())

    async def decode_substitute(url, **_kwargs):
        async with stream_request(url) as response:
            response.raise_for_status()
            await response.aread()
            started.set()
            await release.wait()
        return metadata

    monkeypatch.setattr(prober, "_run_ffprobe", decode_substitute)
    first = asyncio.create_task(prober.probe_stream(7, upstream.stream["url"]))
    try:
        await asyncio.wait_for(started.wait(), 3)
        response = await async_client.post("/api/stream-stats/probe/7")
        assert response.json()["probe_status"] == "skipped"
        assert upstream.media_requests == 1
        # An upstream viewer can arrive after our read: no upstream reservation
        # exists. The in-flight direct probe is NOT claimed as viewer-safe.
        upstream.active = 2
    finally:
        release.set()
        result = await asyncio.wait_for(first, 3)
    assert result["probe_status"] == "success"
    assert upstream.media_requests == 2  # Decode substitute + real bitrate read.
    response = await async_client.post("/api/stream-stats/probe/7")
    assert response.json()["probe_status"] == "skipped"
    assert upstream.media_requests == 2
    saved = (await async_client.get("/api/stream-stats/7")).json()
    assert saved["resolution"] == "640x480"
    assert saved["probe_status"] == "success"
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections


async def test_real_http_status_failure_skips(upstream, integrated_probe, async_client):
    upstream.active = 0
    upstream.status_error = True
    response = await async_client.post("/api/stream-stats/probe/7")
    assert response.json()["probe_status"] == "skipped"
    assert upstream.media_requests == 0


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("pattern", [None, "not-a-match"])
async def test_real_http_scheduler_rewrite_fallback_never_opens_default_media(
    upstream, integrated_probe, async_client, parallel, pattern
):
    prober = integrated_probe
    upstream.account["max_streams"] = 5
    upstream.account["profiles"][0]["max_streams"] = 1
    upstream.active = 1
    other = deepcopy(upstream.account["profiles"][0])
    other.update(id=13, is_default=False, max_streams=2, replace_pattern="alternate")
    if pattern is None:
        other.pop("search_pattern", None)
    else:
        other["search_pattern"] = pattern
    upstream.account["profiles"].append(other)
    prober.parallel_probing_enabled = parallel
    prober._resolve_channel_group_ids = AsyncMock(return_value=None)
    prober._fetch_channel_stream_ids = AsyncMock(return_value=({7}, {}, {}))
    result = await prober.probe_all_streams(
        skip_m3u_refresh=True, allow_reorder_after_probe=False
    )
    assert result["status"] == "completed"
    assert prober.get_probe_progress()["skipped_count"] == 1
    assert prober.get_probe_progress()["failed_count"] == 0
    assert upstream.media_requests == 0
    saved = (await async_client.get("/api/stream-stats/7")).json()
    assert saved["probe_status"] == "success"
    assert saved["video_bitrate"] == 1234
