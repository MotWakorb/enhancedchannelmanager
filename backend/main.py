from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os

from dispatcharr_client import get_client, reset_client
from config import (
    get_settings,
    save_settings,
    clear_settings_cache,
    DispatcharrSettings,
)

app = FastAPI(
    title="Enhanced Channel Manager",
    description="Drag-and-drop channel management for Dispatcharr",
    version="0.1.0",
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request models
class AddStreamRequest(BaseModel):
    stream_id: int


class RemoveStreamRequest(BaseModel):
    stream_id: int


class ReorderStreamsRequest(BaseModel):
    stream_ids: list[int]


class AssignNumbersRequest(BaseModel):
    channel_ids: list[int]
    starting_number: Optional[float] = None


class CreateChannelGroupRequest(BaseModel):
    name: str


# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "enhanced-channel-manager"}


# Settings
class SettingsRequest(BaseModel):
    url: str
    username: str
    password: str


class SettingsResponse(BaseModel):
    url: str
    username: str
    configured: bool


class TestConnectionRequest(BaseModel):
    url: str
    username: str
    password: str


@app.get("/api/settings")
async def get_current_settings():
    """Get current settings (password masked)."""
    settings = get_settings()
    return SettingsResponse(
        url=settings.url,
        username=settings.username,
        configured=settings.is_configured(),
    )


@app.post("/api/settings")
async def update_settings(request: SettingsRequest):
    """Update Dispatcharr connection settings."""
    new_settings = DispatcharrSettings(
        url=request.url,
        username=request.username,
        password=request.password,
    )
    save_settings(new_settings)
    clear_settings_cache()
    reset_client()
    return {"status": "saved", "configured": new_settings.is_configured()}


@app.post("/api/settings/test")
async def test_connection(request: TestConnectionRequest):
    """Test connection to Dispatcharr with provided credentials."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = request.url.rstrip("/")
            response = await client.post(
                f"{url}/api/accounts/token/",
                json={
                    "username": request.username,
                    "password": request.password,
                },
            )
            if response.status_code == 200:
                return {"success": True, "message": "Connection successful"}
            else:
                return {
                    "success": False,
                    "message": f"Authentication failed: {response.status_code}",
                }
    except httpx.ConnectError:
        return {"success": False, "message": "Could not connect to server"}
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# Channels
class CreateChannelRequest(BaseModel):
    name: str
    channel_number: Optional[float] = None
    channel_group_id: Optional[int] = None


@app.get("/api/channels")
async def get_channels(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group: Optional[int] = None,
):
    client = get_client()
    try:
        return await client.get_channels(
            page=page,
            page_size=page_size,
            search=search,
            channel_group=channel_group,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels")
async def create_channel(request: CreateChannelRequest):
    client = get_client()
    try:
        data = {"name": request.name}
        if request.channel_number is not None:
            data["channel_number"] = request.channel_number
        if request.channel_group_id is not None:
            data["channel_group_id"] = request.channel_group_id
        return await client.create_channel(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}")
async def get_channel(channel_id: int):
    client = get_client()
    try:
        return await client.get_channel(channel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels/{channel_id}/streams")
async def get_channel_streams(channel_id: int):
    client = get_client()
    try:
        return await client.get_channel_streams(channel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channels/{channel_id}")
async def update_channel(channel_id: int, data: dict):
    client = get_client()
    try:
        return await client.update_channel(channel_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/add-stream")
async def add_stream_to_channel(channel_id: int, request: AddStreamRequest):
    client = get_client()
    try:
        # Get current channel
        channel = await client.get_channel(channel_id)
        current_streams = channel.get("streams", [])

        # Add stream if not already present
        if request.stream_id not in current_streams:
            current_streams.append(request.stream_id)
            return await client.update_channel(channel_id, {"streams": current_streams})
        return channel
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/remove-stream")
async def remove_stream_from_channel(channel_id: int, request: RemoveStreamRequest):
    client = get_client()
    try:
        # Get current channel
        channel = await client.get_channel(channel_id)
        current_streams = channel.get("streams", [])

        # Remove stream if present
        if request.stream_id in current_streams:
            current_streams.remove(request.stream_id)
            return await client.update_channel(channel_id, {"streams": current_streams})
        return channel
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/{channel_id}/reorder-streams")
async def reorder_channel_streams(channel_id: int, request: ReorderStreamsRequest):
    client = get_client()
    try:
        return await client.update_channel(channel_id, {"streams": request.stream_ids})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels/assign-numbers")
async def assign_channel_numbers(request: AssignNumbersRequest):
    client = get_client()
    try:
        return await client.assign_channel_numbers(
            request.channel_ids, request.starting_number
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Channel Groups
@app.get("/api/channel-groups")
async def get_channel_groups():
    client = get_client()
    try:
        return await client.get_channel_groups()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channel-groups")
async def create_channel_group(request: CreateChannelGroupRequest):
    client = get_client()
    try:
        return await client.create_channel_group(request.name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/channel-groups/{group_id}")
async def update_channel_group(group_id: int, data: dict):
    client = get_client()
    try:
        return await client.update_channel_group(group_id, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channel-groups/{group_id}")
async def delete_channel_group(group_id: int):
    client = get_client()
    try:
        await client.delete_channel_group(group_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Streams
@app.get("/api/streams")
async def get_streams(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group_name: Optional[str] = None,
    m3u_account: Optional[int] = None,
):
    client = get_client()
    try:
        result = await client.get_streams(
            page=page,
            page_size=page_size,
            search=search,
            channel_group_name=channel_group_name,
            m3u_account=m3u_account,
        )

        # Get channel groups for name lookup
        groups = await client.get_channel_groups()
        group_map = {g["id"]: g["name"] for g in groups}

        # Add channel_group_name to each stream
        for stream in result.get("results", []):
            group_id = stream.get("channel_group")
            stream["channel_group_name"] = group_map.get(group_id) if group_id else None

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream-groups")
async def get_stream_groups():
    client = get_client()
    try:
        return await client.get_stream_groups()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Providers (M3U Accounts)
@app.get("/api/providers")
async def get_providers():
    client = get_client()
    try:
        return await client.get_m3u_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Serve static files in production
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount(
        "/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets"
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve index.html for all non-API routes (SPA routing)
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not built"}
