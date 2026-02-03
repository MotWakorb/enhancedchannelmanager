"""
TLS/SSL certificate configuration settings.

Manages TLS-related configuration including Let's Encrypt ACME settings,
manual certificate paths, and renewal status.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from pydantic import BaseModel, field_validator


logger = logging.getLogger(__name__)

# Config file location
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
TLS_CONFIG_FILE = CONFIG_DIR / "tls_settings.json"
TLS_DIR = CONFIG_DIR / "tls"


class TLSSettings(BaseModel):
    """TLS/SSL certificate configuration."""

    # Master enable/disable
    enabled: bool = False

    # Mode: "letsencrypt" for automatic ACME, "manual" for uploaded certs
    mode: Literal["letsencrypt", "manual"] = "letsencrypt"

    # Domain name for the certificate (e.g., ecm.example.com)
    domain: str = ""

    # Let's Encrypt / ACME settings
    acme_email: str = ""  # Contact email for ACME account
    challenge_type: Literal["http-01", "dns-01"] = "http-01"
    use_staging: bool = False  # Use Let's Encrypt staging for testing

    # DNS-01 challenge settings
    dns_provider: str = ""  # Provider: "cloudflare", "route53", etc.
    dns_api_token: str = ""  # API token/key for DNS provider (Cloudflare)
    dns_zone_id: str = ""  # Zone ID (optional, can be auto-detected)

    # AWS Route53 credentials (alternative to dns_api_token)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # Certificate paths (auto-populated, stored in /config/tls/)
    cert_path: str = str(TLS_DIR / "cert.pem")
    key_path: str = str(TLS_DIR / "key.pem")
    chain_path: str = str(TLS_DIR / "chain.pem")  # Full chain for some setups
    acme_account_path: str = str(TLS_DIR / "acme_account.json")

    # Certificate status
    cert_issued_at: Optional[str] = None  # ISO format datetime
    cert_expires_at: Optional[str] = None  # ISO format datetime
    cert_issuer: Optional[str] = None  # Certificate issuer CN
    cert_subject: Optional[str] = None  # Certificate subject CN

    # Renewal settings
    auto_renew: bool = True
    renew_days_before_expiry: int = 30  # Renew when this many days left
    last_renewal_attempt: Optional[str] = None  # ISO format datetime
    last_renewal_error: Optional[str] = None

    # HTTP-01 challenge port (for standalone HTTP server)
    http_challenge_port: int = 80

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        """Validate domain format."""
        if v:
            # Basic domain validation - strip whitespace
            v = v.strip().lower()
            # Remove protocol if accidentally included
            if v.startswith("http://"):
                v = v[7:]
            elif v.startswith("https://"):
                v = v[8:]
            # Remove trailing slash
            v = v.rstrip("/")
        return v

    @field_validator("acme_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Validate email format."""
        if v:
            v = v.strip().lower()
        return v

    def is_configured_for_letsencrypt(self) -> bool:
        """Check if Let's Encrypt settings are complete."""
        if not self.domain or not self.acme_email:
            return False
        if self.challenge_type == "dns-01":
            if not self.dns_provider:
                return False
            # Check provider-specific credentials
            if self.dns_provider.lower() == "cloudflare":
                return bool(self.dns_api_token)
            elif self.dns_provider.lower() == "route53":
                # Route53 can use explicit credentials or IAM role
                # If explicit credentials provided, both must be set
                if self.aws_access_key_id or self.aws_secret_access_key:
                    return bool(self.aws_access_key_id and self.aws_secret_access_key)
                # Otherwise assume IAM role authentication
                return True
            else:
                return False
        return True

    def is_configured_for_manual(self) -> bool:
        """Check if manual certificate paths exist."""
        return (
            Path(self.cert_path).exists()
            and Path(self.key_path).exists()
        )

    def get_expiry_days(self) -> Optional[int]:
        """Get days until certificate expires."""
        if not self.cert_expires_at:
            return None
        try:
            expires = datetime.fromisoformat(self.cert_expires_at)
            delta = expires - datetime.now()
            return max(0, delta.days)
        except (ValueError, TypeError):
            return None

    def needs_renewal(self) -> bool:
        """Check if certificate needs renewal."""
        if not self.auto_renew or not self.cert_expires_at:
            return False
        days_left = self.get_expiry_days()
        if days_left is None:
            return False
        return days_left <= self.renew_days_before_expiry


# In-memory cache of TLS settings
_cached_tls_settings: Optional[TLSSettings] = None


def _ensure_tls_dir() -> bool:
    """Ensure TLS directory exists. Returns True if successful."""
    try:
        TLS_DIR.mkdir(parents=True, exist_ok=True)
        # Set restrictive permissions on TLS directory
        os.chmod(TLS_DIR, 0o700)
        return True
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot create TLS directory {TLS_DIR}: {e}")
        return False


def _ensure_config_dir() -> bool:
    """Ensure config directory exists. Returns True if successful."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot create config directory {CONFIG_DIR}: {e}")
        return False


def load_tls_settings() -> TLSSettings:
    """Load TLS settings from file or return defaults."""
    global _cached_tls_settings

    if _cached_tls_settings is not None:
        return _cached_tls_settings

    logger.info(f"Loading TLS settings from {TLS_CONFIG_FILE}")

    if TLS_CONFIG_FILE.exists():
        try:
            data = json.loads(TLS_CONFIG_FILE.read_text())
            _cached_tls_settings = TLSSettings(**data)
            logger.info(
                f"Loaded TLS settings, enabled: {_cached_tls_settings.enabled}, "
                f"mode: {_cached_tls_settings.mode}"
            )
            return _cached_tls_settings
        except Exception as e:
            logger.error(f"Failed to load TLS settings: {e}")

    logger.info("Using default TLS settings (no config file found)")
    _cached_tls_settings = TLSSettings()
    return _cached_tls_settings


def save_tls_settings(settings: TLSSettings) -> bool:
    """Save TLS settings to file. Returns True if successful."""
    global _cached_tls_settings

    if not _ensure_config_dir():
        _cached_tls_settings = settings
        return False

    try:
        settings_json = json.dumps(settings.model_dump(), indent=2)
        TLS_CONFIG_FILE.write_text(settings_json)
        # Restrictive permissions on settings file (contains API tokens)
        os.chmod(TLS_CONFIG_FILE, 0o600)
        _cached_tls_settings = settings
        logger.info(f"TLS settings saved to {TLS_CONFIG_FILE}")
        return True
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot save TLS settings to {TLS_CONFIG_FILE}: {e}")
        _cached_tls_settings = settings
        return False
    except Exception as e:
        logger.error(f"Failed to save TLS settings: {e}")
        raise


def clear_tls_settings_cache() -> None:
    """Clear the cached TLS settings (forces reload)."""
    global _cached_tls_settings
    _cached_tls_settings = None
    logger.info("TLS settings cache cleared")


def get_tls_settings() -> TLSSettings:
    """Get the current TLS settings."""
    return load_tls_settings()
