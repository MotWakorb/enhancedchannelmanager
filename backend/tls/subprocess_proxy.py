"""HTTPS-subprocess -> main-process proxy for the lock-participating write paths.

GH #720 Part B (PO decision: MAIN IS THE SOLE WRITER OF THE AUTO-SYNC RECONCILE).
In TLS mode a second request-serving ``uvicorn main:app`` subprocess (env
``ECM_HTTPS_SUBPROCESS=1``) can serve requests, while the monitor sweep + the
scheduler run only in the main process. So a request that triggers the auto-sync
reconcile / an ``assign_channel_profile`` pipeline write, or that runs a
background task, could execute in the subprocess and race the main process — and
the IN-PROCESS per-effective-group lock (and the per-process task engine) would
not coordinate across the two processes.

This middleware — active ONLY in the HTTPS subprocess — forwards the small,
explicit allowlist of those routes to the MAIN process's local HTTP port and
relays the response verbatim, so the LOCK-PARTICIPATING writes (the group-
settings save's cascade + reconcile, the channel-pipeline / auto-creation run,
the post-refresh sweep) and ALL background task runs execute ONLY in the main
process, where the lock + scheduler are authoritative. Every other route is
served locally, unchanged.

SCOPE (honest): this covers the AUTO-SYNC RECONCILE + ``assign_channel_profile``
pipeline paths (the ones that participate in ``_group_locks``) and background
task runs — NOT every channel-profile membership write. The DIRECT membership-
edit endpoints (``PATCH /api/channel-profiles/{id}/channels[/bulk-update]``) are
a SEPARATE, PRE-EXISTING concern: they never participated in ``_group_locks``
(even in main they run unlocked), so forwarding them would be theater. They are
tracked in bead nq3ed, not addressed here.

Security: hard-coded target (127.0.0.1 + the main HTTP port), an exact
method+path allowlist (JSON routes only — no streaming, no websockets, no open
proxy), and a plain header/body pass-through (auth headers preserved). No proxy
timeout ceiling is imposed — the MAIN process's own request-timeout middleware
is authoritative (it 504s non-exempt routes and exempts long-running task runs),
so the proxy must NOT cut a legitimately long task run to a 502.
"""
from __future__ import annotations

import logging
import re

import httpx
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# (HTTP method, compiled path regex) — the ONLY routes forwarded to main. These
# are the routes that mutate channel-profile membership or trigger a reconcile /
# background task, so they must execute ONLY in the main process (where the
# per-effective-group lock is authoritative and the scheduler runs):
#   - the M3U group-settings save (cascade + instant reconcile)
#   - the single-account M3U refresh (spawns the post-refresh poll -> full sweep)
#   - the Channel Pipeline run endpoints (an assign_channel_profile action)
#   - ANY task run (the task registry IS populated in the subprocess, so a
#     subprocess run of m3u_change_monitor would sweep and channel_pipeline would
#     write membership — with a per-process _active_tasks that can't see main's
#     scheduled run of the same task; tasks are background work for main only)
_FORWARD_ALLOWLIST = [
    ("PATCH", re.compile(r"^/api/m3u/accounts/\d+/group-settings$")),
    ("POST", re.compile(r"^/api/m3u/refresh/\d+$")),
    # channel_pipeline_router is dual-mounted: the canonical /api/channel-pipeline
    # AND the deprecated alias /api/auto-creation (main.py). BOTH run the same
    # reconcile/assign-profile handler, so BOTH must be forwarded (1a).
    ("POST", re.compile(r"^/api/channel-pipeline/run$")),
    ("POST", re.compile(r"^/api/channel-pipeline/rules/\d+/run$")),
    ("POST", re.compile(r"^/api/auto-creation/run$")),
    ("POST", re.compile(r"^/api/auto-creation/rules/\d+/run$")),
    # Task run AND cancel — both must hit the SAME (main) engine, else a
    # forwarded run cannot be cancelled from HTTPS (per-process _active_tasks).
    ("POST", re.compile(r"^/api/tasks/[^/]+/run$")),
    ("POST", re.compile(r"^/api/tasks/[^/]+/cancel$")),
]

# Hop-by-hop / length headers that must NOT be copied verbatim (httpx / the
# Starlette Response recompute them).
_STRIP_REQUEST_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}
_STRIP_RESPONSE_HEADERS = {"content-length", "connection", "transfer-encoding", "content-encoding"}


def _should_forward(method: str, path: str) -> bool:
    for m, pattern in _FORWARD_ALLOWLIST:
        if method == m and pattern.match(path):
            return True
    return False


async def subprocess_proxy_middleware(request: Request, call_next):
    """Forward allowlisted profile-mutating routes to the main process when
    running in the HTTPS subprocess; otherwise serve locally."""
    # Lazily import so the main process never pays for the check beyond the flag.
    from tls.https_server import is_https_subprocess

    if not is_https_subprocess() or not _should_forward(request.method, request.url.path):
        return await call_next(request)

    from config import get_http_port
    target = f"http://127.0.0.1:{get_http_port()}{request.url.path}"
    if request.url.query:
        target += f"?{request.url.query}"

    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _STRIP_REQUEST_HEADERS
    }
    try:
        # timeout=None (finding 2): the MAIN process's request-timeout middleware
        # is authoritative — it 504s non-exempt routes at its budget and EXEMPTS
        # long-running task runs (/api/tasks/, /api/backup/). A proxy ceiling
        # would cut a legitimately long forwarded task run to a false 502 while
        # it keeps running in main. So we impose no ceiling and relay whatever
        # main returns (including main's own 504).
        async with httpx.AsyncClient(timeout=None) as client:
            upstream = await client.request(
                request.method, target, content=body, headers=fwd_headers,
            )
    except Exception as e:  # noqa: BLE001 - main unreachable -> 502
        logger.warning("[TLS-PROXY] forward to main failed for %s %s: %s",
                       request.method, request.url.path, e)
        return Response(
            content=b'{"detail":"upstream (main process) unavailable"}',
            status_code=502, media_type="application/json",
        )

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _STRIP_RESPONSE_HEADERS
    }
    logger.info("[TLS-PROXY] forwarded %s %s -> main (%s)",
                request.method, request.url.path, upstream.status_code)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
