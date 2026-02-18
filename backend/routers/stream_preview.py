"""
Stream preview router — stream and channel preview proxy endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import subprocess

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from config import get_settings
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stream Preview"])


async def stream_generator(process: subprocess.Popen, chunk_size: int = 65536):
    """Generator that yields chunks from FFmpeg process stdout."""
    try:
        while True:
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, process.stdout.read, chunk_size
            )
            if not chunk:
                break
            yield chunk
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@router.get("/api/stream-preview/{stream_id}")
async def stream_preview(stream_id: int):
    """
    Proxy endpoint for stream preview with optional transcoding.

    Based on stream_preview_mode setting:
    - passthrough: Direct proxy (may fail on AC-3/E-AC-3/DTS audio)
    - transcode: Transcode audio to AAC for browser compatibility
    - video_only: Strip audio for quick preview

    Returns MPEG-TS stream suitable for mpegts.js playback.
    """
    settings = get_settings()
    mode = settings.stream_preview_mode

    # Get stream URL from Dispatcharr
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Not connected to Dispatcharr")

    try:
        stream = await client.get_stream(stream_id)
        if not stream or not stream.get("url"):
            raise HTTPException(status_code=404, detail="Stream not found or has no URL")
        stream_url = stream["url"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stream {stream_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stream: {str(e)}")

    logger.info(f"Stream preview requested for stream {stream_id}, mode: {mode}")

    if mode == "passthrough":
        # Direct proxy - just fetch and forward
        # Use httpx to stream the content, following redirects
        async def passthrough_generator():
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as http_client:
                async with http_client.stream("GET", stream_url) as response:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            passthrough_generator(),
            media_type="video/mp2t",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )

    elif mode == "transcode":
        # Transcode audio to AAC for browser compatibility
        # FFmpeg: copy video, transcode audio to AAC
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",  # Generate pts, handle corruption
            "-analyzeduration", "2000000",        # 2 seconds to analyze stream
            "-probesize", "2000000",              # 2MB probe size
            "-i", stream_url,
            "-c:v", "copy",           # Copy video as-is
            "-c:a", "aac",            # Transcode audio to AAC
            "-b:a", "192k",           # 192kbps audio bitrate
            "-ac", "2",               # Stereo output
            "-max_muxing_queue_size", "1024",     # Larger muxing buffer
            "-f", "mpegts",           # Output format
            "-"                       # Output to stdout
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for transcoding support."
            )
        except Exception as e:
            logger.error(f"FFmpeg transcode error: {e}")
            raise HTTPException(status_code=500, detail=f"Transcoding failed: {str(e)}")

    elif mode == "video_only":
        # Strip audio entirely for quick preview
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",  # Generate pts, handle corruption
            "-analyzeduration", "2000000",        # 2 seconds to analyze stream
            "-probesize", "2000000",              # 2MB probe size
            "-i", stream_url,
            "-c:v", "copy",           # Copy video as-is
            "-an",                    # No audio
            "-max_muxing_queue_size", "1024",     # Larger muxing buffer
            "-f", "mpegts",           # Output format
            "-"                       # Output to stdout
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for video-only preview."
            )
        except Exception as e:
            logger.error(f"FFmpeg video-only error: {e}")
            raise HTTPException(status_code=500, detail=f"Video extraction failed: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail=f"Invalid preview mode: {mode}")


@router.get("/api/channel-preview/{channel_id}")
async def channel_preview(channel_id: int):
    """
    Proxy endpoint for channel preview with optional transcoding.

    Previews the channel output from Dispatcharr's TS proxy. This tests the
    actual channel stream as it would be served to clients.

    Based on stream_preview_mode setting:
    - passthrough: Direct proxy (may fail on AC-3/E-AC-3/DTS audio)
    - transcode: Transcode audio to AAC for browser compatibility
    - video_only: Strip audio for quick preview

    Returns MPEG-TS stream suitable for mpegts.js playback.
    """
    settings = get_settings()
    mode = settings.stream_preview_mode

    # Get channel from Dispatcharr to get its UUID
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Not connected to Dispatcharr")

    try:
        channel = await client.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        channel_uuid = channel.get("uuid")
        if not channel_uuid:
            raise HTTPException(status_code=404, detail="Channel has no UUID")

        # Construct Dispatcharr TS proxy URL using UUID
        dispatcharr_url = settings.url.rstrip("/")
        channel_url = f"{dispatcharr_url}/proxy/ts/stream/{channel_uuid}"

        # Get auth token for authenticated requests to Dispatcharr proxy
        await client._ensure_authenticated()
        auth_headers = {"Authorization": f"Bearer {client.access_token}"}

        logger.info(f"Channel preview: proxying Dispatcharr stream for channel {channel_id} (uuid={channel_uuid})")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get channel: {str(e)}")

    logger.info(f"Channel preview requested for channel {channel_id}, mode: {mode}")

    if mode == "passthrough":
        # Direct proxy with JWT auth - just fetch and forward
        async def passthrough_generator():
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as http_client:
                async with http_client.stream("GET", channel_url, headers=auth_headers) as response:
                    if response.status_code != 200:
                        logger.error(f"Dispatcharr proxy returned {response.status_code}")
                        return
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            passthrough_generator(),
            media_type="video/mp2t",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )

    elif mode == "transcode":
        # Transcode audio to AAC for browser compatibility
        # FFmpeg -headers option passes JWT auth to Dispatcharr proxy
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-headers", f"Authorization: Bearer {client.access_token}\r\n",
            "-i", channel_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",
            "-max_muxing_queue_size", "1024",
            "-f", "mpegts",
            "-"
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for transcoding support."
            )
        except Exception as e:
            logger.error(f"FFmpeg transcode error: {e}")
            raise HTTPException(status_code=500, detail=f"Transcoding failed: {str(e)}")

    elif mode == "video_only":
        # Strip audio entirely for quick preview
        # FFmpeg -headers option passes JWT auth to Dispatcharr proxy
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-headers", f"Authorization: Bearer {client.access_token}\r\n",
            "-i", channel_url,
            "-c:v", "copy",
            "-an",
            "-max_muxing_queue_size", "1024",
            "-f", "mpegts",
            "-"
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=65536
            )

            return StreamingResponse(
                stream_generator(process),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for video-only preview."
            )
        except Exception as e:
            logger.error(f"FFmpeg video-only error: {e}")
            raise HTTPException(status_code=500, detail=f"Video extraction failed: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail=f"Invalid preview mode: {mode}")
