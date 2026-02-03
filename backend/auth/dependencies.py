"""
FastAPI authentication dependencies.

Provides dependency injection functions for extracting and validating
user authentication from requests.
"""
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_session
from models import User
from .tokens import decode_token, TokenExpiredError, InvalidTokenError, TokenRevokedError
from .settings import get_auth_settings


logger = logging.getLogger(__name__)


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


async def get_current_user_optional(
    request: Request,
    session: Session = Depends(get_session),
) -> Optional[User]:
    """
    FastAPI dependency to optionally get the current user.

    Returns None instead of raising an error if not authenticated.
    Useful for endpoints that have different behavior for authenticated
    vs. anonymous users.

    Args:
        request: The FastAPI request object.
        session: Database session (injected).

    Returns:
        The authenticated User object or None.
    """
    try:
        return await get_current_user(request, session)
    except HTTPException:
        return None


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
