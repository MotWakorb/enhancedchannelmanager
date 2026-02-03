"""
Authentication configuration settings.

Manages auth-related configuration including JWT settings, session options,
and auth provider configurations (local, OIDC, SAML, LDAP).
"""
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Optional, Literal

from pydantic import BaseModel


logger = logging.getLogger(__name__)

# Config file location
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
AUTH_CONFIG_FILE = CONFIG_DIR / "auth_settings.json"


class JWTSettings(BaseModel):
    """JWT token configuration."""
    # Secret key for signing tokens (auto-generated if not set)
    secret_key: str = ""
    # Algorithm for JWT signing
    algorithm: str = "HS256"
    # Access token expiration in minutes
    access_token_expire_minutes: int = 30
    # Refresh token expiration in days
    refresh_token_expire_days: int = 7


class SessionSettings(BaseModel):
    """Session management configuration."""
    # Maximum concurrent sessions per user (0 = unlimited)
    max_sessions_per_user: int = 5
    # Session inactivity timeout in minutes (0 = never expire from inactivity)
    inactivity_timeout_minutes: int = 0
    # Whether to extend session on activity
    extend_on_activity: bool = True


class LocalAuthSettings(BaseModel):
    """Local authentication configuration."""
    # Whether local auth is enabled
    enabled: bool = True
    # Allow user self-registration
    allow_registration: bool = False
    # Require email verification for registration
    require_email_verification: bool = False
    # Password requirements
    min_password_length: int = 8
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_number: bool = True
    require_special: bool = False


class OIDCSettings(BaseModel):
    """OpenID Connect (OIDC) configuration."""
    enabled: bool = False
    provider_name: str = ""
    client_id: str = ""
    client_secret: str = ""
    discovery_url: str = ""  # e.g., https://provider/.well-known/openid-configuration
    scopes: list[str] = ["openid", "profile", "email"]
    # Claim mappings
    username_claim: str = "preferred_username"
    email_claim: str = "email"
    name_claim: str = "name"
    # Auto-create users from OIDC
    auto_create_users: bool = True


class SAMLSettings(BaseModel):
    """SAML 2.0 configuration."""
    enabled: bool = False
    provider_name: str = ""
    # Identity Provider metadata URL or XML
    idp_metadata_url: str = ""
    idp_metadata_xml: str = ""
    # Service Provider settings
    sp_entity_id: str = ""
    sp_acs_url: str = ""  # Assertion Consumer Service URL
    # Attribute mappings
    username_attribute: str = "username"
    email_attribute: str = "email"
    name_attribute: str = "displayName"
    # Auto-create users from SAML
    auto_create_users: bool = True


class LDAPSettings(BaseModel):
    """LDAP/Active Directory configuration."""
    enabled: bool = False
    server_url: str = ""  # e.g., ldap://ldap.example.com:389
    use_ssl: bool = False
    use_tls: bool = True
    # Bind credentials (for searching)
    bind_dn: str = ""
    bind_password: str = ""
    # User search settings
    user_search_base: str = ""  # e.g., ou=users,dc=example,dc=com
    user_search_filter: str = "(uid={username})"  # {username} is replaced
    # Attribute mappings
    username_attribute: str = "uid"
    email_attribute: str = "mail"
    name_attribute: str = "cn"
    # Group settings (optional)
    group_search_base: str = ""
    group_search_filter: str = "(member={user_dn})"
    admin_group_dn: str = ""  # Users in this group get admin rights
    # Auto-create users from LDAP
    auto_create_users: bool = True


class DispatcharrAuthSettings(BaseModel):
    """Dispatcharr SSO integration settings."""
    enabled: bool = False
    # Use Dispatcharr credentials for authentication
    use_dispatcharr_auth: bool = False
    # Auto-create local user from Dispatcharr auth
    auto_create_users: bool = True


class AuthSettings(BaseModel):
    """Main authentication settings container."""
    # Setup state
    setup_complete: bool = False

    # Primary auth mode: which provider is the default
    # "local" = username/password, "oidc" = OpenID Connect, "saml" = SAML 2.0
    # "ldap" = LDAP/AD, "dispatcharr" = Dispatcharr SSO
    primary_auth_mode: Literal["local", "oidc", "saml", "ldap", "dispatcharr"] = "local"

    # Whether authentication is required at all
    # If False, the app runs in "open" mode (no login required)
    require_auth: bool = True

    # Sub-settings
    jwt: JWTSettings = JWTSettings()
    session: SessionSettings = SessionSettings()
    local: LocalAuthSettings = LocalAuthSettings()
    oidc: OIDCSettings = OIDCSettings()
    saml: SAMLSettings = SAMLSettings()
    ldap: LDAPSettings = LDAPSettings()
    dispatcharr: DispatcharrAuthSettings = DispatcharrAuthSettings()

    def is_setup_required(self) -> bool:
        """Check if initial auth setup is required."""
        return not self.setup_complete

    def get_enabled_providers(self) -> list[str]:
        """Get list of enabled authentication providers."""
        providers = []
        if self.local.enabled:
            providers.append("local")
        if self.oidc.enabled:
            providers.append("oidc")
        if self.saml.enabled:
            providers.append("saml")
        if self.ldap.enabled:
            providers.append("ldap")
        if self.dispatcharr.enabled:
            providers.append("dispatcharr")
        return providers


# In-memory cache of auth settings
_cached_auth_settings: Optional[AuthSettings] = None


def _ensure_config_dir() -> bool:
    """Ensure config directory exists. Returns True if successful."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot create config directory {CONFIG_DIR}: {e}")
        return False


def _generate_secret_key() -> str:
    """Generate a secure random secret key for JWT signing."""
    return secrets.token_urlsafe(32)


def load_auth_settings() -> AuthSettings:
    """Load auth settings from file or return defaults."""
    global _cached_auth_settings

    if _cached_auth_settings is not None:
        return _cached_auth_settings

    logger.info(f"Loading auth settings from {AUTH_CONFIG_FILE}")

    if AUTH_CONFIG_FILE.exists():
        try:
            data = json.loads(AUTH_CONFIG_FILE.read_text())
            _cached_auth_settings = AuthSettings(**data)

            # Ensure we have a secret key
            if not _cached_auth_settings.jwt.secret_key:
                _cached_auth_settings.jwt.secret_key = _generate_secret_key()
                save_auth_settings(_cached_auth_settings)

            logger.info(f"Loaded auth settings, setup_complete: {_cached_auth_settings.setup_complete}")
            return _cached_auth_settings
        except Exception as e:
            logger.error(f"Failed to load auth settings: {e}")

    logger.info("Using default auth settings (no config file found)")
    _cached_auth_settings = AuthSettings()

    # Generate and persist a secret key for new installations
    _cached_auth_settings.jwt.secret_key = _generate_secret_key()
    save_auth_settings(_cached_auth_settings)

    return _cached_auth_settings


def save_auth_settings(settings: AuthSettings) -> bool:
    """Save auth settings to file. Returns True if successful."""
    global _cached_auth_settings

    if not _ensure_config_dir():
        # Can't create directory, just cache in memory
        _cached_auth_settings = settings
        return False

    try:
        settings_json = json.dumps(settings.model_dump(), indent=2)
        AUTH_CONFIG_FILE.write_text(settings_json)
        _cached_auth_settings = settings
        logger.info(f"Auth settings saved to {AUTH_CONFIG_FILE}")
        return True
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot save auth settings to {AUTH_CONFIG_FILE}: {e}")
        _cached_auth_settings = settings  # Still cache in memory
        return False
    except Exception as e:
        logger.error(f"Failed to save auth settings: {e}")
        raise


def clear_auth_settings_cache() -> None:
    """Clear the cached auth settings (forces reload)."""
    global _cached_auth_settings
    _cached_auth_settings = None
    logger.info("Auth settings cache cleared")


def get_auth_settings() -> AuthSettings:
    """Get the current auth settings."""
    return load_auth_settings()


def get_jwt_secret_key() -> str:
    """Get the JWT secret key, generating one if needed."""
    settings = get_auth_settings()
    if not settings.jwt.secret_key:
        settings.jwt.secret_key = _generate_secret_key()
        save_auth_settings(settings)
    return settings.jwt.secret_key


def mark_setup_complete() -> None:
    """Mark the initial auth setup as complete."""
    settings = get_auth_settings()
    settings.setup_complete = True
    save_auth_settings(settings)
    logger.info("Auth setup marked as complete")
