"""
Stream preview router — stream and channel preview proxy endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import subprocess
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from config import get_settings
from dispatcharr_client import get_client
from security.ssrf import SSRFError
from security.stream_outbound import (
    prepare_stream_http_url,
    stream_request,
    validated_subprocess_input,
)

logger = logging.getLogger(__name__)

# Restrict ffmpeg to safe network protocols only — blocks file://, data://, concat:, etc.
# tls and crypto are required internal protocols: HTTPS chains to tls for the TLS
# handshake, HLS AES-128 encrypted segments chain to crypto. Without tls ffmpeg
# fails on every HTTPS URL with "Protocol 'tls' not on whitelist" (GH-106).
FFMPEG_PROTOCOL_WHITELIST = "http,https,tls,crypto,tcp,udp,rtp,rtmp,pipe"
RELAY_PROTOCOL_WHITELIST = "http,tcp,crypto"

router = APIRouter(tags=["Stream Preview"])


async def stream_generator(
    process: subprocess.Popen,
    chunk_size: int = 65536,
    *,
    input_context=None,
):
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
        if input_context is not None:
            await input_context.__aexit__(None, None, None)


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
    logger.debug("[PREVIEW] GET /api/stream-preview/%s", stream_id)
    settings = get_settings()
    mode = settings.stream_preview_mode

    # Get stream URL from Dispatcharr
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Not connected to Dispatcharr")

    try:
        start = time.time()
        stream = await client.get_stream(stream_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PREVIEW] get_stream %s completed in %.1fms", stream_id, elapsed_ms)
        if not stream or not stream.get("url"):
            raise HTTPException(status_code=404, detail="Stream not found or has no URL")
        stream_url = stream["url"]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[PREVIEW] Failed to get stream %s", stream_id)
        raise HTTPException(status_code=500, detail=f"Failed to get stream: {str(e)}")

    logger.info("[PREVIEW] Stream preview requested for stream %s, mode: %s", stream_id, mode)

    if mode == "passthrough":
        # Direct proxy - just fetch and forward
        try:
            initial_target = prepare_stream_http_url(stream_url)
        except SSRFError as exc:
            raise HTTPException(
                status_code=403, detail="Stream destination is not permitted"
            ) from exc

        async def passthrough_generator():
            async with stream_request(
                stream_url, timeout=None, initial_target=initial_target
            ) as response:
                response.raise_for_status()
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
        input_context = validated_subprocess_input(stream_url)
        try:
            subprocess_input = await input_context.__aenter__()
        except SSRFError as exc:
            raise HTTPException(
                status_code=403, detail="Stream destination is not permitted"
            ) from exc
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-protocol_whitelist", (
                RELAY_PROTOCOL_WHITELIST
                if subprocess_input.is_http_relay
                else FFMPEG_PROTOCOL_WHITELIST
            ),
            "-fflags", "+genpts+discardcorrupt",  # Generate pts, handle corruption
            "-analyzeduration", "2000000",        # 2 seconds to analyze stream
            "-probesize", "2000000",              # 2MB probe size
            "-i", subprocess_input.argument,
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
                stream_generator(
                    process,
                    input_context=input_context,
                ),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            await input_context.__aexit__(None, None, None)
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for transcoding support."
            )
        except Exception as e:
            await input_context.__aexit__(type(e), e, e.__traceback__)
            logger.exception("[PREVIEW] FFmpeg transcode error for stream %s", stream_id)
            raise HTTPException(status_code=500, detail=f"Transcoding failed: {str(e)}")

    elif mode == "video_only":
        # Strip audio entirely for quick preview
        input_context = validated_subprocess_input(stream_url)
        try:
            subprocess_input = await input_context.__aenter__()
        except SSRFError as exc:
            raise HTTPException(
                status_code=403, detail="Stream destination is not permitted"
            ) from exc
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-protocol_whitelist", (
                RELAY_PROTOCOL_WHITELIST
                if subprocess_input.is_http_relay
                else FFMPEG_PROTOCOL_WHITELIST
            ),
            "-fflags", "+genpts+discardcorrupt",  # Generate pts, handle corruption
            "-analyzeduration", "2000000",        # 2 seconds to analyze stream
            "-probesize", "2000000",              # 2MB probe size
            "-i", subprocess_input.argument,
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
                stream_generator(
                    process,
                    input_context=input_context,
                ),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            await input_context.__aexit__(None, None, None)
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for video-only preview."
            )
        except Exception as e:
            await input_context.__aexit__(type(e), e, e.__traceback__)
            logger.exception("[PREVIEW] FFmpeg video-only error for stream %s", stream_id)
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
    logger.debug("[PREVIEW] GET /api/channel-preview/%s", channel_id)
    settings = get_settings()
    mode = settings.stream_preview_mode

    # Get channel from Dispatcharr to get its UUID
    client = get_client()
    if not client:
        raise HTTPException(status_code=503, detail="Not connected to Dispatcharr")

    try:
        start = time.time()
        channel = await client.get_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[PREVIEW] get_channel %s completed in %.1fms", channel_id, elapsed_ms)
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

        logger.info("[PREVIEW] Channel preview: proxying Dispatcharr stream for channel %s (uuid=%s)", channel_id, channel_uuid)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[PREVIEW] Failed to get channel %s", channel_id)
        raise HTTPException(status_code=500, detail=f"Failed to get channel: {str(e)}")

    logger.info("[PREVIEW] Channel preview requested for channel %s, mode: %s", channel_id, mode)

    if mode == "passthrough":
        # Direct proxy with JWT auth - just fetch and forward
        try:
            initial_target = prepare_stream_http_url(channel_url)
        except SSRFError as exc:
            raise HTTPException(
                status_code=403, detail="Stream destination is not permitted"
            ) from exc

        async def passthrough_generator():
            async with stream_request(
                channel_url,
                timeout=None,
                headers=auth_headers,
                initial_target=initial_target,
            ) as response:
                if response.status_code != 200:
                    logger.error("[PREVIEW] Dispatcharr proxy returned %s", response.status_code)
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
        input_context = validated_subprocess_input(channel_url, headers=auth_headers)
        try:
            subprocess_input = await input_context.__aenter__()
        except SSRFError as exc:
            raise HTTPException(
                status_code=403, detail="Stream destination is not permitted"
            ) from exc
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-protocol_whitelist", (
                RELAY_PROTOCOL_WHITELIST
                if subprocess_input.is_http_relay
                else FFMPEG_PROTOCOL_WHITELIST
            ),
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-i", subprocess_input.argument,
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
                stream_generator(
                    process,
                    input_context=input_context,
                ),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            await input_context.__aexit__(None, None, None)
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for transcoding support."
            )
        except Exception as e:
            await input_context.__aexit__(type(e), e, e.__traceback__)
            logger.exception("[PREVIEW] FFmpeg transcode error for channel %s", channel_id)
            raise HTTPException(status_code=500, detail=f"Transcoding failed: {str(e)}")

    elif mode == "video_only":
        # Strip audio entirely for quick preview
        # FFmpeg -headers option passes JWT auth to Dispatcharr proxy
        input_context = validated_subprocess_input(channel_url, headers=auth_headers)
        try:
            subprocess_input = await input_context.__aenter__()
        except SSRFError as exc:
            raise HTTPException(
                status_code=403, detail="Stream destination is not permitted"
            ) from exc
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-protocol_whitelist", (
                RELAY_PROTOCOL_WHITELIST
                if subprocess_input.is_http_relay
                else FFMPEG_PROTOCOL_WHITELIST
            ),
            "-fflags", "+genpts+discardcorrupt",
            "-analyzeduration", "2000000",
            "-probesize", "2000000",
            "-i", subprocess_input.argument,
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
                stream_generator(
                    process,
                    input_context=input_context,
                ),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        except FileNotFoundError:
            await input_context.__aexit__(None, None, None)
            raise HTTPException(
                status_code=500,
                detail="FFmpeg not found. Please install FFmpeg for video-only preview."
            )
        except Exception as e:
            await input_context.__aexit__(type(e), e, e.__traceback__)
            logger.exception("[PREVIEW] FFmpeg video-only error for channel %s", channel_id)
            raise HTTPException(status_code=500, detail=f"Video extraction failed: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail=f"Invalid preview mode: {mode}")
