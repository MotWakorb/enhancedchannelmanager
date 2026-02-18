"""
Authentication module for ECM.

Provides password hashing, JWT tokens, and auth utilities.
"""
import logging

logger = logging.getLogger(__name__)

from .password import (
    hash_password,
    verify_password,
    validate_password,
    PasswordValidationResult,
)

__all__ = [
    # Password
    "hash_password",
    "verify_password",
    "validate_password",
    "PasswordValidationResult",
]

# Tokens will be imported when implemented
try:
    from .tokens import (
        create_access_token,
        create_refresh_token,
        decode_token,
        refresh_access_token,
        rotate_refresh_token,
        TokenExpiredError,
        InvalidTokenError,
        TokenRevokedError,
    )
    __all__.extend([
        "create_access_token",
        "create_refresh_token",
        "decode_token",
        "refresh_access_token",
        "rotate_refresh_token",
        "TokenExpiredError",
        "InvalidTokenError",
        "TokenRevokedError",
    ])
except ImportError as e:
    logger.debug("[AUTH] Suppressed token import error: %s", e)

# Settings imports
try:
    from .settings import (
        AuthSettings,
        JWTSettings,
        SessionSettings,
        LocalAuthSettings,
        DispatcharrAuthSettings,
        get_auth_settings,
        save_auth_settings,
        clear_auth_settings_cache,
        get_jwt_secret_key,
        mark_setup_complete,
    )
    __all__.extend([
        "AuthSettings",
        "JWTSettings",
        "SessionSettings",
        "LocalAuthSettings",
        "DispatcharrAuthSettings",
        "get_auth_settings",
        "save_auth_settings",
        "clear_auth_settings_cache",
        "get_jwt_secret_key",
        "mark_setup_complete",
    ])
except ImportError as e:
    logger.debug("[AUTH] Suppressed settings import error: %s", e)

# Dependencies imports
try:
    from .dependencies import (
        AuthenticationError,
        PermissionError,
        get_token_from_request,
        get_refresh_token_from_request,
        get_current_user,
        get_current_user_optional,
        get_current_active_admin,
        require_auth_if_enabled,
        RequireAuthIfEnabled,
    )
    __all__.extend([
        "AuthenticationError",
        "PermissionError",
        "get_token_from_request",
        "get_refresh_token_from_request",
        "get_current_user",
        "get_current_user_optional",
        "get_current_active_admin",
        "require_auth_if_enabled",
        "RequireAuthIfEnabled",
    ])
except ImportError as e:
    logger.debug("[AUTH] Suppressed dependencies import error: %s", e)

# Routes imports
try:
    from .routes import router as auth_router
    from .admin_routes import router as admin_router
    __all__.extend([
        "auth_router",
        "admin_router",
    ])
except ImportError as e:
    logger.debug("[AUTH] Suppressed routes import error: %s", e)
