"""
JWT token generation and validation utilities.

Uses PyJWT for JWT handling with HS256 algorithm.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidIssuedAtError, PyJWTError

logger = logging.getLogger(__name__)

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

# One-shot flag so the first fallback to the per-process random secret is
# logged at ERROR with a full traceback; repeats log at WARNING without one.
_fallback_logged = False


def _get_secret_key() -> str:
    """Get the JWT secret key from settings, or fall back to a per-process
    random default when settings are genuinely unavailable.

    Only ImportError (settings module unavailable, e.g. stripped test
    environments or an import cycle during early startup) and OSError
    (config directory/file unreadable) trigger the fallback — any other
    exception is a bug and propagates so it surfaces as a logged 500
    instead of being silently converted into an auth lockout (bd-0gt2i:
    the old bare ``except Exception`` masked every failure as a fallback
    to a random secret, invalidating all sessions with no visible cause).
    """
    global _fallback_logged
    try:
        from .settings import get_jwt_secret_key
        return get_jwt_secret_key()
    except (ImportError, OSError) as e:
        if not _fallback_logged:
            _fallback_logged = True
            logger.error(
                "[AUTH] JWT secret key unavailable (%s: %s) — falling back to a "
                "per-process RANDOM secret. All existing sessions/tokens are "
                "invalid until auth settings become readable, and every process "
                "restart invalidates them again.",
                type(e).__name__,
                e,
                exc_info=True,
            )
        else:
            logger.warning(
                "[AUTH] JWT secret key still unavailable (%s: %s) — using "
                "per-process random fallback secret",
                type(e).__name__,
                e,
            )
        return _DEFAULT_SECRET_KEY


def _get_configured_jwt_lifetimes() -> Tuple[int, int]:
    """Configured (access_token_minutes, refresh_token_days) from settings.

    Falls back to the module constants under exactly the conditions
    :func:`_get_secret_key` already tolerates — settings module unavailable
    (ImportError) or the config directory unreadable (OSError). Any other
    exception is a bug and propagates rather than silently issuing tokens
    with a lifetime the operator never configured.

    bd-suuoh: these two values are already honored by the auth cookies'
    ``max_age``, by ``UserSession.expires_at``, and by the
    ``access_token_expires_in`` metadata the frontend schedules its proactive
    refresh from. The issuer ignoring them left every one of those in
    disagreement with the tokens actually minted.
    """
    try:
        from .settings import get_auth_settings
        jwt_settings = get_auth_settings().jwt
        return (
            jwt_settings.access_token_expire_minutes,
            jwt_settings.refresh_token_expire_days,
        )
    except (ImportError, OSError) as e:
        logger.warning(
            "[AUTH] Configured JWT lifetimes unavailable (%s: %s) — issuing "
            "tokens with the built-in defaults (%dm access / %dd refresh)",
            type(e).__name__,
            e,
            ACCESS_TOKEN_EXPIRE_MINUTES,
            REFRESH_TOKEN_EXPIRE_DAYS,
        )
        return ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS


def create_access_token(
    user_id: int,
    username: str,
    expires_delta: Optional[timedelta] = None,
    auth_epoch: int = 0,
) -> str:
    """
    Create a JWT access token.

    Args:
        user_id: The user's ID.
        username: The user's username.
        expires_delta: Optional custom expiration time. When omitted the
            configured ``jwt.access_token_expire_minutes`` is used.
        auth_epoch: The user's current account-wide credential epoch.

    Returns:
        The encoded JWT string.
    """
    if expires_delta is None:
        access_minutes, _ = _get_configured_jwt_lifetimes()
        expires_delta = timedelta(minutes=access_minutes)

    now = datetime.utcnow()
    expire = now + expires_delta

    payload = {
        "sub": str(user_id),  # JWT requires sub to be string
        "username": username,
        "type": "access",
        "exp": expire,
        "iat": now,
        "auth_epoch": auth_epoch,
    }

    return jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """
    Create a JWT refresh token with longer expiration.

    The lifetime comes from the configured ``jwt.refresh_token_expire_days``,
    which is the same value the login handlers write into
    ``UserSession.expires_at`` (bd-suuoh). Keeping them in step matters in
    both directions: a JWT that outlives its session row lets the client
    present a perfectly decodable token against a row
    ``_cleanup_expired_sessions`` has already deleted, which answers 401
    "Session not found or revoked"; a JWT that dies before its row logs the
    operator out ahead of the lifetime the settings advertise.

    Args:
        user_id: The user's ID.

    Returns:
        The encoded JWT refresh token string.
    """
    _, refresh_days = _get_configured_jwt_lifetimes()

    now = datetime.utcnow()
    expire = now + timedelta(days=refresh_days)

    payload = {
        "sub": str(user_id),  # JWT requires sub to be string
        "type": "refresh",
        "exp": expire,
        "iat": now,
        "jti": secrets.token_urlsafe(16),  # Unique token ID for revocation
    }

    return jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str, ignore_revocation: bool = False) -> dict:
    """
    Decode and validate a JWT token.

    Args:
        token: The JWT token string to decode.
        ignore_revocation: Skip the in-process jti revocation check while
            still enforcing signature and expiry. Used ONLY by the refresh
            predecessor path (bd-x67qe, bead upkp1): rotation blacklists the
            presented jti immediately, but the predecessor stays acceptable
            until its successor is used, so the DB row and not this set is
            what decides. Every predecessor acceptance depends on this flag;
            it is the normal path there, not an edge case. Never use it for
            authorization decisions.

    Returns:
        The decoded token claims.

    Raises:
        TokenExpiredError: If the token has expired.
        InvalidTokenError: If the token is invalid.
        TokenRevokedError: If the token has been revoked (unless
            ``ignore_revocation``).
    """
    if not token or not isinstance(token, str):
        raise InvalidTokenError("Invalid token format")

    # Basic structure check
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError("Malformed token")

    try:
        payload = jwt.decode(
            token,
            _get_secret_key(),
            algorithms=[ALGORITHM],
            options={"verify_iat": False},
        )

        # python-jose validated the iat type but allowed future values. Keep
        # that contract rather than adopting PyJWT's stricter clock check.
        if "iat" in payload and not isinstance(payload["iat"], int):
            raise InvalidIssuedAtError(
                "Issued At claim (iat) must be an integer."
            )

        # Check if token is revoked
        jti = payload.get("jti")
        if not ignore_revocation and jti and jti in _revoked_tokens:
            raise TokenRevokedError("Token has been revoked")

        # Convert sub back to int for API compatibility
        if "sub" in payload:
            try:
                payload["sub"] = int(payload["sub"])
            except (ValueError, TypeError) as e:
                logger.debug("[AUTH] Suppressed token sub conversion error: %s", e)

        return payload

    except ExpiredSignatureError:
        raise TokenExpiredError("Token has expired")
    except PyJWTError as e:
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


def rotate_refresh_token(
    refresh_token: str,
    username: Optional[str] = None,
    auth_epoch: int = 0,
) -> Tuple[str, str]:
    """
    Rotate refresh token - revoke old one and create new access + refresh tokens.

    Args:
        refresh_token: The current refresh token.
        username: The account's real username, for the new access token's
            ``username`` claim. Callers that have already loaded the ``User``
            row (the ``/auth/refresh`` handler does) must pass it. Omitting it
            falls back to a ``user_<id>`` placeholder, which is NOT the
            account's name — the claim is not used for authorization
            (``get_current_user`` resolves the caller from ``sub``) but it is
            logged verbatim as the acting operator by ``main.py``'s
            deprecated-admin-router warning, so a placeholder there
            misattributes the request (bd-suuoh).
        auth_epoch: The user's current account-wide credential epoch.

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
    new_access_token = create_access_token(
        user_id=user_id,
        username=username if username is not None else f"user_{user_id}",
        auth_epoch=auth_epoch,
    )
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
