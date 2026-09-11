"""Stream and channel preview proxies with validated upstream startup."""

import asyncio
import logging
import subprocess

import anyio
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from config import get_settings
from dispatcharr_client import get_client
from security.ssrf import DNSResolutionError, SSRFError, SchemeDowngrade
from security.stream_outbound import (
    stream_request,
    validated_subprocess_input,
)
from stream_user_agent import StreamUserAgentError, StreamUserAgentResolver

logger = logging.getLogger(__name__)

FFMPEG_PROTOCOL_WHITELIST = "http,https,tls,crypto,tcp,udp,rtp,rtmp,pipe"
RELAY_PROTOCOL_WHITELIST = "http,tcp,crypto"
PREVIEW_SCHEME_DOWNGRADE = SchemeDowngrade.ALLOW_STREAM_PREVIEW
# Bound provider startup/stalls without limiting total viewing time.
PREVIEW_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
router = APIRouter(tags=["Stream Preview"])


def _startup_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DNSResolutionError):
        status, detail = 502, "Stream DNS resolution failed from ECM"
    elif isinstance(exc, SSRFError):
        status, detail = 403, "Stream destination is not permitted"
    elif isinstance(exc, StreamUserAgentError):
        status, detail = 502, str(exc)
    elif isinstance(exc, httpx.HTTPStatusError):
        status, detail = 502, f"Upstream stream returned HTTP {exc.response.status_code}"
    elif isinstance(exc, httpx.TimeoutException):
        status, detail = 504, "Upstream stream timed out from ECM"
    else:
        status, detail = 502, "Could not connect to upstream stream from ECM"
    # No raw exception text: HTTPX errors can include provider URLs and credentials.
    logger.warning("[PREVIEW] Startup failed: %s", detail)
    return HTTPException(status_code=status, detail=detail)


class _PreviewResources:
    def __init__(self, context):
        self.context = context
        self.process = None
        self.closed = False

    async def close(self):
        if self.closed:
            return
        self.closed = True
        # Starlette cancels streaming on disconnect. Cleanup must outlive that
        # cancellation, including a disconnect before the iterator's first read.
        with anyio.CancelScope(shield=True):
            try:
                if self.process is not None:
                    await _stop_process(self.process)
            finally:
                await self.context.__aexit__(None, None, None)


class _PreviewResponse(StreamingResponse):
    def __init__(self, iterator, resources):
        super().__init__(iterator, media_type="video/mp2t", headers=_RESPONSE_HEADERS)
        self.resources = resources

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.resources.close()


async def _stop_process(process):
    try:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
    except ProcessLookupError:
        await asyncio.to_thread(process.wait)
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


async def stream_generator(process, chunk_size=65536, *, input_context=None):
    """Yield FFmpeg stdout and release subprocess/input on generator closure."""
    try:
        while chunk := await asyncio.to_thread(process.stdout.read, chunk_size):
            yield chunk
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await _stop_process(process)
            finally:
                if input_context is not None:
                    await input_context.__aexit__(None, None, None)


async def _preview_response(url, mode, headers, *, provider_media=False):
    policy = PREVIEW_SCHEME_DOWNGRADE if provider_media else SchemeDowngrade.REFUSE
    if mode == "passthrough":
        context = stream_request(url, headers=headers, timeout=PREVIEW_TIMEOUT, scheme_downgrade=policy)
    else:
        kwargs = {"headers": headers, "timeout": PREVIEW_TIMEOUT}
        if provider_media:
            kwargs.update(scheme_downgrade=policy)
        context = validated_subprocess_input(url, **kwargs)
    resources = _PreviewResources(context)
    try:
        upstream = await context.__aenter__()
        if mode == "passthrough":
            upstream.raise_for_status()

            async def chunks():
                try:
                    async for chunk in upstream.aiter_bytes(chunk_size=65536):
                        yield chunk
                except httpx.HTTPError as exc:
                    logger.warning("[PREVIEW] Upstream streaming failed (%s)", type(exc).__name__)
                    # Headers are sent: abort the body without exposing the
                    # provider URL through the server's exception handler.
                    raise RuntimeError("Upstream preview stream failed") from None
                finally:
                    await resources.close()
        else:
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", RELAY_PROTOCOL_WHITELIST if upstream.is_http_relay else FFMPEG_PROTOCOL_WHITELIST,
                "-fflags", "+genpts+discardcorrupt", "-analyzeduration", "2000000",
                "-probesize", "2000000", "-i", upstream.argument, "-c:v", "copy",
            ]
            command += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"] if mode == "transcode" else ["-an"]
            command += ["-max_muxing_queue_size", "1024", "-f", "mpegts", "-"]
            resources.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=65536
            )

            async def chunks():
                try:
                    while chunk := await asyncio.to_thread(resources.process.stdout.read, 65536):
                        yield chunk
                    returncode = await asyncio.to_thread(resources.process.wait, timeout=5)
                    if returncode != 0:
                        logger.warning("[PREVIEW] Decoder exited abnormally (returncode=%s)", returncode)
                        raise RuntimeError("Preview decoder failed")
                except (OSError, subprocess.SubprocessError) as exc:
                    logger.warning("[PREVIEW] Decoder streaming failed (%s)", type(exc).__name__)
                    raise RuntimeError("Preview decoder failed") from None
                finally:
                    await resources.close()

        return _PreviewResponse(chunks(), resources)
    except (SSRFError, httpx.HTTPError) as exc:
        await resources.close()
        raise _startup_error(exc) from None
    except FileNotFoundError:
        await resources.close()
        logger.warning("[PREVIEW] FFmpeg is unavailable")
        raise HTTPException(500, "FFmpeg not found. Please install FFmpeg for preview.") from None
    except BaseException:
        await resources.close()
        raise


@router.get("/api/stream-preview/{stream_id}")
async def stream_preview(stream_id: int):
    """Preview provider media directly using ECM's shared User-Agent selection."""
    settings = get_settings()
    mode = settings.stream_preview_mode
    if mode not in {"passthrough", "transcode", "video_only"}:
        raise HTTPException(400, "Invalid preview mode")
    client = get_client()
    if not client:
        raise HTTPException(503, "Not connected to Dispatcharr")
    try:
        stream = await client.get_stream(stream_id)
    except Exception:
        logger.warning("[PREVIEW] Could not load stream %s from Dispatcharr", stream_id)
        raise HTTPException(502, "Could not load stream from Dispatcharr") from None
    if not stream or not stream.get("url"):
        raise HTTPException(404, "Stream not found or has no URL")
    try:
        user_agent = await StreamUserAgentResolver(client, settings.stream_user_agent).resolve(stream)
    except StreamUserAgentError as exc:
        raise _startup_error(exc) from None
    return await _preview_response(stream["url"], mode, {"User-Agent": user_agent}, provider_media=True)


@router.get("/api/channel-preview/{channel_id}")
async def channel_preview(channel_id: int):
    """Preview Dispatcharr's authenticated TS proxy; Dispatcharr owns its provider UA."""
    settings = get_settings()
    mode = settings.stream_preview_mode
    if mode not in {"passthrough", "transcode", "video_only"}:
        raise HTTPException(400, "Invalid preview mode")
    client = get_client()
    if not client:
        raise HTTPException(503, "Not connected to Dispatcharr")
    try:
        channel = await client.get_channel(channel_id)
        if not channel:
            raise HTTPException(404, "Channel not found")
        if not channel.get("uuid"):
            raise HTTPException(404, "Channel has no UUID")
        url = f"{settings.url.rstrip('/')}/proxy/ts/stream/{channel['uuid']}"
        await client._ensure_authenticated()
    except HTTPException:
        raise
    except Exception:
        logger.warning("[PREVIEW] Could not load channel %s from Dispatcharr", channel_id)
        raise HTTPException(502, "Could not load channel from Dispatcharr") from None
    return await _preview_response(url, mode, {"Authorization": f"Bearer {client.access_token}"})
