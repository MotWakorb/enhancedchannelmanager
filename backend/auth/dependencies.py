"""
FastAPI authentication dependencies.

Provides dependency injection functions for extracting and validating
user authentication from requests.
"""
import hmac
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from config import get_settings
from database import get_session
from models import User
from .tokens import decode_token, TokenExpiredError, InvalidTokenError, TokenRevokedError
from .settings import get_auth_settings


logger = logging.getLogger(__name__)

# Synthetic, non-persisted user id for the static-MCP-key service principal.
# Negative so it can never collide with a real autoincrement users.id row
# (SQLite/Postgres autoincrement is always positive) and so any accidental
# FK write would fail loudly rather than silently corrupting a real row.
# The only place a principal's id reaches the DB is the dedup audit
# ``actor_token_id`` column (models.PendingMergeJournal), which is free-text
# (``Text``), not a foreign key — see routers/channel_merges.py:_actor_token_id.
MCP_SERVICE_PRINCIPAL_ID = -1
MCP_SERVICE_PRINCIPAL_USERNAME = "mcp-service"


def _is_mcp_service_token(token: str) -> bool:
    """Return True iff ``token`` is the configured static MCP API key.

    The static MCP key is a permanent, operator-set bearer credential
    (threat model EP2). The global ``auth_middleware`` in main.py already
    accepts it for any ``/api/*`` path; this recognizes it at the route
    dependency layer too so that JWT route-guards (``RequireAuthIfEnabled``
    / ``RequireAdminIfEnabled``) stop rejecting it as a malformed JWT.

    Uses :func:`hmac.compare_digest` (constant-time) rather than ``==`` to
    avoid a timing oracle on the static key (bd-i3axt LOW-1). An empty or
    unset ``mcp_api_key`` never matches — including against an empty token.
    """
    if not token:
        return False
    expected = get_settings().mcp_api_key
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


def _build_mcp_service_principal() -> User:
    """Construct the admin-equivalent, non-persisted MCP service principal.

    Returned to callers of ``get_current_user`` when the static MCP key is
    presented. It is a transient ``User`` instance — never added to a
    session, never committed. Callers only read ``.id``, ``.username``,
    ``.is_admin`` and ``.is_active``.
    """
    return User(
        id=MCP_SERVICE_PRINCIPAL_ID,
        username=MCP_SERVICE_PRINCIPAL_USERNAME,
        is_admin=True,
        is_active=True,
        auth_provider="mcp",
    )


class AuthenticationError(HTTPException):
    """Authentication failed."""
    def __init__(self, detail: str = "Could not validate credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class PermissionError(HTTPException):
    """User lacks required permissions."""
    def __init__(self, detail: str = "Permission denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def get_token_from_request(request: Request) -> Optional[str]:
    """
    Extract JWT token from request.

    Checks for token in the following order:
    1. access_token cookie (httpOnly cookie for web clients)
    2. Authorization header (Bearer token for API clients)

    Args:
        request: The FastAPI request object.

    Returns:
        The JWT token string or None if not found.
    """
    # Check cookies first (web clients)
    token = request.cookies.get("access_token")
    if token:
        return token

    # Check Authorization header (API clients)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]  # Remove "Bearer " prefix

    return None


def decode_token_safe(token: str) -> Optional[dict]:
    """Decode a JWT token without raising exceptions. Returns payload or None."""
    try:
        return decode_token(token)
    except (TokenExpiredError, InvalidTokenError, TokenRevokedError):
        return None


def get_refresh_token_from_request(request: Request) -> Optional[str]:
    """
    Extract refresh token from request.

    Checks for token in the following order:
    1. refresh_token cookie
    2. X-Refresh-Token header

    Args:
        request: The FastAPI request object.

    Returns:
        The refresh token string or None if not found.
    """
    # Check cookies first
    token = request.cookies.get("refresh_token")
    if token:
        return token

    # Check custom header
    token = request.headers.get("X-Refresh-Token")
    if token:
        return token

    return None


async def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """
    FastAPI dependency to get the current authenticated user.

    Extracts and validates the JWT token, then loads the user from database.

    Args:
        request: The FastAPI request object.
        session: Database session (injected).

    Returns:
        The authenticated User object.

    Raises:
        AuthenticationError: If token is missing, invalid, or user not found.
    """
    token = get_token_from_request(request)
    if not token:
        raise AuthenticationError("Not authenticated")

    # Static MCP API key: recognize it BEFORE attempting JWT decode. The key
    # is not a 3-part JWT, so decode_token() would reject it as "Malformed
    # token". The global auth_middleware already grants this key full /api/*
    # access; honoring it here aligns the route-dependency layer with that
    # grant and unblocks JWT-guarded routes (dedup, backup, add_stream).
    if _is_mcp_service_token(token):
        logger.debug("[AUTH] Authenticated request via static MCP service key")
        return _build_mcp_service_principal()

    try:
        payload = decode_token(token)
    except TokenExpiredError:
        raise AuthenticationError("Token has expired")
    except TokenRevokedError:
        raise AuthenticationError("Token has been revoked")
    except InvalidTokenError as e:
        raise AuthenticationError(f"Invalid token: {str(e)}")

    # Get user ID from token payload
    user_id = payload.get("sub")
    if user_id is None:
        raise AuthenticationError("Invalid token payload")

    # Load user from database
    user = session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise AuthenticationError("User not found")

    # Check if user is active
    if not user.is_active:
        raise AuthenticationError("User account is disabled")

    return user


async def get_current_active_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    FastAPI dependency to require an admin user.

    Args:
        current_user: The authenticated user (injected).

    Returns:
        The authenticated admin User object.

    Raises:
        PermissionError: If user is not an admin.
    """
    if not current_user.is_admin:
        raise PermissionError("Admin access required")
    return current_user


def require_auth_if_enabled():
    """
    Factory function to create a dependency that checks auth if enabled.

    This allows endpoints to optionally require authentication based
    on the auth settings. When auth is disabled (setup not complete or
    require_auth=False), the endpoint is publicly accessible.

    Returns:
        A dependency function that returns the user or None.
    """
    async def check_auth(
        request: Request,
        session: Session = Depends(get_session),
    ) -> Optional[User]:
        settings = get_auth_settings()

        # If auth not required or setup not complete, allow anonymous access
        if not settings.require_auth or not settings.setup_complete:
            return None

        # Auth is required - get the user
        return await get_current_user(request, session)

    return check_auth


# Pre-built dependency for common use
RequireAuthIfEnabled = Depends(require_auth_if_enabled())


def require_admin_if_enabled():
    """
    Factory function to create a dependency that requires admin when auth is enabled.

    When auth is disabled (setup not complete or require_auth=False),
    the endpoint is publicly accessible. When auth is enabled, the
    caller must be an authenticated admin.
    """
    async def check_admin(
        request: Request,
        session: Session = Depends(get_session),
    ) -> Optional[User]:
        settings = get_auth_settings()

        # If auth not required or setup not complete, allow anonymous access
        if not settings.require_auth or not settings.setup_complete:
            return None

        # Auth is required - get the user and check admin
        user = await get_current_user(request, session)
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        return user

    return check_admin


RequireAdminIfEnabled = Depends(require_admin_if_enabled())
