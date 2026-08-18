"""Private sidecar-to-backend authentication and request-bound claims.

The operator-facing ``mcp_api_key`` authenticates clients to the MCP server.
It is intentionally never accepted here.  The sidecar uses two independently
generated credentials from an owner-only projection: one authenticates its
backend principal and the other signs short-lived, single-use request claims.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

MCP_CLAIM_HEADER = "X-ECM-MCP-Claim"
MCP_CLAIM_TTL_SECONDS = 30
_CLAIM_LEDGER_LIMIT = 4096
_consumed_nonces: dict[str, int] = {}
_ledger_lock = threading.Lock()
_credential_lock = threading.Lock()


@dataclass(frozen=True)
class MCPServiceCredentials:
    backend_key: str
    confirmation_key: str


def ensure_mcp_service_credentials(path: Path) -> MCPServiceCredentials:
    """Load or atomically create the private sidecar projection."""
    with _credential_lock:
        return _ensure_mcp_service_credentials_locked(path)


def load_mcp_service_credentials(path: Path) -> MCPServiceCredentials | None:
    """Load or create the projection, returning ``None`` instead of raising.

    For callers on a liveness path — the FastAPI startup handler and the auth
    middleware, which runs on every non-exempt request. An exception out of a
    startup handler aborts the ASGI lifespan, and an exception out of the
    middleware is a 500 on every authenticated request, so a projection
    directory ECM cannot write must not be able to take the whole application
    down (bead enhancedchannelmanager-04c0u.8).

    This is not a relaxation of the credential rules. ``None`` means the
    sidecar principal has no credential, so the middleware's MCP branch is
    simply never entered and an MCP-authenticated request falls through to the
    ordinary 401 — fail-closed. ``backend/entrypoint.sh`` prepares the
    projection directory while it is still root, so reaching ``None`` in
    production means the mount changed underneath a running container.
    """
    try:
        return ensure_mcp_service_credentials(path)
    except (OSError, RuntimeError):
        logger.exception(
            "[MCP-AUTH] MCP sidecar credential projection at %s is unusable; "
            "the MCP service principal is unavailable until it is repaired",
            path.parent,
        )
        return None


def rotate_mcp_service_credentials(path: Path) -> MCPServiceCredentials:
    """Atomically rotate both private credentials without disclosing them."""
    with _credential_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        credentials = MCPServiceCredentials(
            backend_key=secrets.token_urlsafe(48),
            confirmation_key=secrets.token_urlsafe(48),
        )
        _atomic_write_credentials(path, credentials)
        return credentials


def _ensure_mcp_service_credentials_locked(path: Path) -> MCPServiceCredentials:
    """Implementation serialized across concurrent first requests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Read and re-assert the mode through a descriptor, never through the
        # path: ``os.chmod(path, ...)`` follows symlinks, so a path-based
        # re-chmod on an attacker-planted link would widen the mode of the
        # link target instead of this credential file. ``O_NOFOLLOW`` refuses
        # the link outright and ``os.fchmod`` acts on the opened inode.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "r")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            data = json.loads(handle.read())
        credentials = MCPServiceCredentials(
            backend_key=str(data["backend_key"]),
            confirmation_key=str(data["confirmation_key"]),
        )
        if not credentials.backend_key or not credentials.confirmation_key:
            raise ValueError("empty MCP service credential")
        return credentials
    except FileNotFoundError:
        pass
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("MCP service credential projection is invalid") from exc

    credentials = MCPServiceCredentials(
        backend_key=secrets.token_urlsafe(48),
        confirmation_key=secrets.token_urlsafe(48),
    )
    _atomic_write_credentials(path, credentials)
    return credentials


def _atomic_write_credentials(path: Path, credentials: MCPServiceCredentials) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(credentials.__dict__, output, separators=(",", ":"))
            output.flush()
            # Fix the mode on the descriptor before the rename, not on the
            # path afterwards: a path-based chmod follows symlinks, and the
            # descriptor is the file we actually wrote (finding 10).
            os.fchmod(output.fileno(), 0o600)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _canonical_body(raw: bytes) -> bytes:
    if not raw:
        return b"null"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _claim_payload(timestamp: int, nonce: str, method: str, path: str, body: bytes) -> bytes:
    return b"\0".join((
        str(timestamp).encode(), nonce.encode(), method.upper().encode(), path.encode(),
        hashlib.sha256(_canonical_body(body)).hexdigest().encode(),
    ))


def issue_test_claim(
    credentials: MCPServiceCredentials, method: str, path: str, body: object,
    *, timestamp: int | None = None, nonce: str | None = None,
) -> str:
    """Issue a claim for tests; production claims are minted by the sidecar."""
    issued = int(time.time()) if timestamp is None else timestamp
    unique = nonce or secrets.token_urlsafe(18)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(
        credentials.confirmation_key.encode(),
        _claim_payload(issued, unique, method, path, raw), hashlib.sha256,
    ).digest()
    return f"v1.{issued}.{unique}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


async def verify_mcp_service_claim(
    request: Request, credentials: MCPServiceCredentials,
) -> None:
    """Verify request binding, expiry and atomic single use; fail closed."""
    supplied = request.headers.get(MCP_CLAIM_HEADER, "")
    try:
        version, raw_timestamp, nonce, raw_signature = supplied.split(".", 3)
        timestamp = int(raw_timestamp)
        signature = base64.urlsafe_b64decode(raw_signature + "=" * (-len(raw_signature) % 4))
    except (ValueError, TypeError):
        raise HTTPException(status_code=403, detail="Valid MCP sidecar claim required")
    now = int(time.time())
    if version != "v1" or timestamp > now + 2 or now - timestamp > MCP_CLAIM_TTL_SECONDS:
        raise HTTPException(status_code=403, detail="MCP sidecar claim expired or invalid")
    body = await request.body()
    target = request.url.path + (
        f"?{request.url.query}" if request.url.query else ""
    )
    expected = hmac.new(
        credentials.confirmation_key.encode(),
        _claim_payload(timestamp, nonce, request.method, target, body),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="MCP sidecar claim does not match request")
    with _ledger_lock:
        cutoff = now - MCP_CLAIM_TTL_SECONDS
        for old_nonce, issued in list(_consumed_nonces.items()):
            if issued < cutoff:
                del _consumed_nonces[old_nonce]
        if nonce in _consumed_nonces:
            raise HTTPException(status_code=403, detail="MCP sidecar claim already used")
        if len(_consumed_nonces) >= _CLAIM_LEDGER_LIMIT:
            raise HTTPException(status_code=503, detail="MCP sidecar claim ledger is full")
        _consumed_nonces[nonce] = timestamp
