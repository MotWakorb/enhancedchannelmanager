"""
JWT token generation and validation utilities.

Uses python-jose for JWT handling with HS256 algorithm.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple, Any

from jose import JWTError, jwt, ExpiredSignatureError


# Default configuration - used when settings unavailable (e.g., during tests)
_DEFAULT_SECRET_KEY = secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class TokenExpiredError(Exception):
    """Raised when a token has expired."""
    pass


class InvalidTokenError(Exception):
    """Raised when a token is invalid (malformed, bad signature, etc.)."""
    pass


class TokenRevokedError(Exception):
    """Raised when a token has been revoked."""
    pass


# In-memory store for revoked tokens (in production, use Redis or database)
_revoked_tokens: set = set()


def _get_secret_key() -> str:
    """Get the JWT secret key from settings or use default."""
    try:
        from .settings import get_jwt_secret_key
        return get_jwt_secret_key()
    except Exception:
        # Fallback for tests or when settings not available
        return _DEFAULT_SECRET_KEY


def create_access_token(
    user_id: int,
    username: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token.

    Args:
        user_id: The user's ID.
        username: The user's username.
        expires_delta: Optional custom expiration time.

    Returns:
        The encoded JWT string.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    now = datetime.utcnow()
    expire = now + expires_delta

    payload = {
        "sub": str(user_id),  # JWT requires sub to be string
        "username": username,
        "type": "access",
        "exp": expire,
        "iat": now,
    }

    return jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """
    Create a JWT refresh token with longer expiration.

    Args:
        user_id: The user's ID.

    Returns:
        The encoded JWT refresh token string.
    """
    now = datetime.utcnow()
    expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload = {
        "sub": str(user_id),  # JWT requires sub to be string
        "type": "refresh",
        "exp": expire,
        "iat": now,
        "jti": secrets.token_urlsafe(16),  # Unique token ID for revocation
    }

    return jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.

    Args:
        token: The JWT token string to decode.

    Returns:
        The decoded token claims.

    Raises:
        TokenExpiredError: If the token has expired.
        InvalidTokenError: If the token is invalid.
    """
    if not token or not isinstance(token, str):
        raise InvalidTokenError("Invalid token format")

    # Basic structure check
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError("Malformed token")

    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[ALGORITHM])

        # Check if token is revoked
        jti = payload.get("jti")
        if jti and jti in _revoked_tokens:
            raise TokenRevokedError("Token has been revoked")

        # Convert sub back to int for API compatibility
        if "sub" in payload:
            try:
                payload["sub"] = int(payload["sub"])
            except (ValueError, TypeError):
                pass

        return payload

    except ExpiredSignatureError:
        raise TokenExpiredError("Token has expired")
    except JWTError as e:
        raise InvalidTokenError(f"Invalid token: {str(e)}")


def refresh_access_token(refresh_token: str) -> str:
    """
    Generate a new access token using a valid refresh token.

    Args:
        refresh_token: The refresh token string.

    Returns:
        A new access token string.

    Raises:
        InvalidTokenError: If the token is not a refresh token or is invalid.
        TokenExpiredError: If the refresh token has expired.
        TokenRevokedError: If the refresh token has been revoked.
    """
    claims = decode_token(refresh_token)

    # Verify it's a refresh token
    if claims.get("type") != "refresh":
        raise InvalidTokenError("Not a refresh token")

    user_id = claims["sub"]

    # Revoke the used refresh token (one-time use)
    jti = claims.get("jti")
    if jti:
        _revoked_tokens.add(jti)

    # Create new access token
    # Note: In production, we'd fetch the username from the database
    return create_access_token(user_id=user_id, username=f"user_{user_id}")


def rotate_refresh_token(refresh_token: str) -> Tuple[str, str]:
    """
    Rotate refresh token - revoke old one and create new access + refresh tokens.

    Args:
        refresh_token: The current refresh token.

    Returns:
        Tuple of (new_access_token, new_refresh_token).

    Raises:
        InvalidTokenError: If the token is not a refresh token or is invalid.
        TokenExpiredError: If the refresh token has expired.
        TokenRevokedError: If the refresh token has been revoked.
    """
    claims = decode_token(refresh_token)

    # Verify it's a refresh token
    if claims.get("type") != "refresh":
        raise InvalidTokenError("Not a refresh token")

    user_id = claims["sub"]

    # Revoke the old refresh token
    jti = claims.get("jti")
    if jti:
        _revoked_tokens.add(jti)

    # Create new tokens
    # Note: In production, we'd fetch the username from the database
    new_access_token = create_access_token(user_id=user_id, username=f"user_{user_id}")
    new_refresh_token = create_refresh_token(user_id=user_id)

    return new_access_token, new_refresh_token


def hash_token(token: str) -> str:
    """
    Create a hash of a token for storage.

    Args:
        token: The token to hash.

    Returns:
        SHA256 hash of the token.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def revoke_token(jti: str) -> None:
    """
    Revoke a token by its JTI (JWT ID).

    Args:
        jti: The token's unique identifier.
    """
    _revoked_tokens.add(jti)


def clear_revoked_tokens() -> None:
    """Clear all revoked tokens (for testing)."""
    _revoked_tokens.clear()
