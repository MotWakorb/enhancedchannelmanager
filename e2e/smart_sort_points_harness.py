"""Isolated backend for the Smart Sort Points browser contract."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response

from auth.settings import AuthSettings, save_auth_settings
from config import (
    DispatcharrSettings,
    clear_settings_cache,
    get_settings,
    save_settings,
)
from database import get_session, init_db
from models import StreamStats
from routers.settings import router as settings_router
from routers.stream_stats import router as stream_stats_router


CHANNEL = {
    "id": 41,
    "uuid": "smart-sort-points-channel",
    "name": "Points Sorting Fixture",
    "channel_number": 101,
    "channel_group_id": 7,
    "streams": [101, 202],
    "logo_id": None,
    "tvg_id": None,
    "epg_data_id": None,
}

STREAMS = [
    {
        "id": 101,
        "name": "Healthy 1080p",
        "url": "https://fixture.invalid/healthy-1080p.m3u8",
        "channel_group_name": "Fixture Streams",
        "m3u_account": 7,
        "logo_url": None,
    },
    {
        "id": 202,
        "name": "Failed 720p",
        "url": "https://fixture.invalid/failed-720p.m3u8",
        "channel_group_name": "Fixture Streams",
        "m3u_account": 7,
        "logo_url": None,
    },
]


app = FastAPI()


@app.on_event("startup")
async def initialize_harness() -> None:
    init_db()
    session = get_session()
    try:
        session.add_all([
            StreamStats(
                stream_id=101,
                stream_name="Healthy 1080p",
                resolution="1920x1080",
                bitrate=7_000_000,
                probe_status="success",
            ),
            StreamStats(
                stream_id=202,
                stream_name="Failed 720p",
                resolution="1280x720",
                bitrate=3_000_000,
                probe_status="failed",
            ),
        ])
        session.commit()
    finally:
        session.close()
    Path(os.environ["MCP_SECRETS_DIR"]).mkdir(parents=True, exist_ok=True)
    save_auth_settings(AuthSettings(setup_complete=True, require_auth=False))
    save_settings(
        DispatcharrSettings(
            url="http://dispatcharr.fixture.invalid",
            auth_method="api_key",
            dispatcharr_api_key="isolated-e2e-token",
        )
    )
    clear_settings_cache()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": "smart-sort-points-e2e"}


@app.get("/api/auth/status")
async def auth_status() -> dict[str, object]:
    return {
        "setup_complete": True,
        "require_auth": False,
        "enabled_providers": ["local"],
        "primary_auth_mode": "local",
        "smtp_configured": False,
    }


@app.get("/api/auth/setup-required")
async def setup_required() -> dict[str, bool]:
    return {"required": False}


@app.get("/api/auth/me")
async def current_user() -> None:
    raise HTTPException(status_code=401, detail="No session in isolated open mode")


@app.post("/api/session-start")
async def session_start() -> Response:
    return Response(status_code=204)


@app.get("/api/channel-groups")
async def channel_groups() -> list[dict[str, object]]:
    return [{"id": 7, "name": "Fixture Channels", "channel_count": 1}]


@app.get("/api/channels")
async def channels() -> dict[str, object]:
    return {"count": 1, "next": None, "previous": None, "results": [CHANNEL]}


@app.get("/api/channels/logos")
async def channel_logos() -> dict[str, object]:
    return {"count": 0, "next": None, "previous": None, "results": []}


@app.get("/api/channels/{channel_id}/streams")
async def channel_streams(channel_id: int) -> list[dict[str, object]]:
    if channel_id != CHANNEL["id"]:
        raise HTTPException(status_code=404, detail="Fixture channel not found")
    return STREAMS


@app.get("/api/providers")
async def providers() -> list[dict[str, object]]:
    return [
        {
            "id": 7,
            "name": "Fixture Provider",
            "is_active": True,
            "max_streams": 0,
            "profiles": [],
        }
    ]


@app.get("/api/providers/group-settings")
async def provider_group_settings() -> dict[str, object]:
    return {}


@app.get("/api/stream-groups")
async def stream_groups() -> list[dict[str, object]]:
    return [{"name": "Fixture Streams", "count": 2}]


@app.get("/api/streams")
async def streams() -> dict[str, object]:
    return {"count": 2, "next": None, "previous": None, "results": STREAMS}


@app.get("/api/streams/stale-ids")
async def stale_stream_ids() -> dict[str, list[int]]:
    return {"stale_stream_ids": []}


@app.get("/api/stream-profiles")
async def stream_profiles() -> list[object]:
    return []


@app.get("/api/channel-profiles")
async def channel_profiles() -> list[object]:
    return []


@app.get("/api/epg/sources")
async def epg_sources() -> list[object]:
    return []


@app.get("/api/epg/data")
async def epg_data() -> list[object]:
    return []


@app.get("/api/stream-stats/probe/progress")
async def probe_progress() -> dict[str, object]:
    return {"in_progress": False, "status": "idle"}


@app.get("/api/stream-stats/probe/history")
async def probe_history() -> list[object]:
    return []


@app.get("/api/alert-methods")
async def alert_methods() -> list[object]:
    return []


@app.get("/api/notifications")
async def notifications() -> dict[str, object]:
    return {"notifications": [], "unread_count": 0, "results": []}


@app.get("/api/channel-merges")
async def channel_merges() -> dict[str, object]:
    return {"results": [], "count": 0}


@app.get("/api/profile-conflict-reviews")
async def profile_conflict_reviews() -> dict[str, object]:
    return {"reviews": [], "total": 0}


@app.post("/api/e2e/mutate-sort-settings")
async def mutate_sort_settings() -> dict[str, str]:
    settings = get_settings().model_copy(deep=True)
    settings.stream_sort_strategy = "priority"
    save_settings(settings)
    clear_settings_cache()
    return {"mutant": "points-strategy-ignored"}


app.include_router(settings_router)
app.include_router(stream_stats_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["SMART_SORT_POINTS_E2E_BACKEND_PORT"]),
        log_level="warning",
    )
