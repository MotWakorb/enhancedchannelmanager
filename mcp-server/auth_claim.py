"""Request-bound authorization claims for sidecar-to-backend calls."""
from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class ClaimContext:
    tool_name: str
    classification: str
    confirmed: bool


_context: contextvars.ContextVar[ClaimContext | None] = contextvars.ContextVar(
    "mcp_claim_context", default=None
)


@contextlib.contextmanager
def claim_context(tool_name: str, classification: str, *, confirmed: bool) -> Iterator[None]:
    token = _context.set(ClaimContext(tool_name, classification, confirmed))
    try:
        yield
    finally:
        _context.reset(token)


def _canonical_body(body: object) -> bytes:
    if isinstance(body, bytes):
        if not body:
            return b"null"
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def request_claim_headers(method: str, path: str, body: object = None) -> dict[str, str]:
    """Mint a short-lived request-bound claim inside a registered tool call."""
    active = _context.get()
    if active is None:
        return {}
    from config import get_mcp_backend_credentials

    backend_key, confirmation_key = get_mcp_backend_credentials()
    if not backend_key or not confirmation_key:
        return {}
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(18)
    canonical = _canonical_body(body)
    payload = b"\0".join((
        str(timestamp).encode(), nonce.encode(), method.upper().encode(), path.encode(),
        hashlib.sha256(canonical).hexdigest().encode(),
    ))
    signature = hmac.new(confirmation_key.encode(), payload, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return {
        "Authorization": f"Bearer {backend_key}",
        "X-ECM-MCP-Claim": f"v1.{timestamp}.{nonce}.{encoded}",
    }


class SidecarBackendAuth:
    """httpx auth hook that signs the final serialized request body."""

    def auth_flow(self, request):
        raw_target = request.url.raw_path.decode("ascii")
        for name, value in request_claim_headers(
            request.method, raw_target, request.content
        ).items():
            request.headers[name] = value
        yield request
