"""
TLS/SSL certificate configuration settings.

Manages TLS-related configuration including Let's Encrypt ACME settings,
manual certificate paths, and renewal status.
"""
import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

from pydantic import BaseModel, ValidationError, field_validator


logger = logging.getLogger(__name__)

# Config file location
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
TLS_CONFIG_FILE = CONFIG_DIR / "tls_settings.json"
TLS_DIR = CONFIG_DIR / "tls"

# tls_settings.json holds DNS-01 provider credentials in clear (bead 2owpi:
# the PO declined at-rest encryption on 2026-08-13 because ECM must decrypt
# unattended, which would put the key in this same directory). Owner-only is
# therefore the control that actually holds, and the startup probe below is
# what notices when it stops holding.
_REQUIRED_MODE = 0o600


def _utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime.

    ``cert_expires_at`` is populated from CertificateInfo.not_after, which is
    stored as naive UTC (see tls/storage.py::parse_certificate, which drops
    the tzinfo from cryptography's aware not_valid_*_utc). Comparisons must
    therefore use naive *UTC* now, not ``datetime.now()`` (naive *local*) —
    mixing the two skews expiry/renewal math by the host's UTC offset
    (bead n5zw2, same fix applied here for tls/settings.py under wccvo).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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

    # HTTPS port for TLS connections (HTTP always stays on 6100 as fallback)
    # Precedence:
    # 1. Saved config in tls_settings.json
    # 2. ECM_HTTPS_PORT environment variable (read at module import time)
    # 3. Default value 6143
    https_port: int = int(os.environ.get("ECM_HTTPS_PORT", 6143))

    # Emergency recovery only. When false, enabling ECM TLS makes browser
    # session cookies Secure even on the still-listening HTTP port, so a
    # browser cannot send an authenticated session over plaintext.
    allow_http_session_cookies: bool = False

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
        """Check if Let's Encrypt DNS-01 settings are complete."""
        if not self.domain or not self.acme_email:
            return False
        # DNS-01 challenge requires a DNS provider (or manual setup)
        # Allow configuration without provider for manual DNS setup
        if self.dns_provider:
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
                # Unknown provider
                return False
        # No provider means manual DNS setup - still valid
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
            delta = expires - _utcnow_naive()
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
        logger.warning("[TLS-SETTINGS] Cannot create TLS directory %s: %s", TLS_DIR, e)
        return False


def _ensure_config_dir() -> bool:
    """Ensure config directory exists. Returns True if successful."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except (PermissionError, OSError) as e:
        logger.warning("[TLS-SETTINGS] Cannot create config directory %s: %s", CONFIG_DIR, e)
        return False


def load_tls_settings() -> TLSSettings:
    """Load TLS settings from file or return defaults."""
    global _cached_tls_settings

    if _cached_tls_settings is not None:
        return _cached_tls_settings

    logger.info("[TLS-SETTINGS] Loading TLS settings from %s", TLS_CONFIG_FILE)

    if TLS_CONFIG_FILE.exists():
        try:
            data = json.loads(TLS_CONFIG_FILE.read_text())
            _cached_tls_settings = TLSSettings(**data)
            logger.info(
                "[TLS-SETTINGS] Loaded TLS settings, enabled: %s, mode: %s",
                _cached_tls_settings.enabled, _cached_tls_settings.mode,
            )
            return _cached_tls_settings
        except ValidationError as e:
            # Bead 2owpi: pydantic v2 puts ``input_value=<the value>`` in its
            # error text, so formatting this exception would print a stored
            # credential into the log whenever the file holds one of these
            # fields with the wrong JSON type. Name the fields, not the
            # values: that is what makes the failure diagnosable anyway.
            logger.error(
                "[TLS-SETTINGS] Failed to load TLS settings: %d invalid field(s): %s",
                e.error_count(),
                ", ".join(sorted({
                    ".".join(str(part) for part in err.get("loc", ())) or "<root>"
                    for err in e.errors()
                })),
            )
        except Exception as e:
            # Non-validation failures (unreadable file, malformed JSON) carry
            # no field values. json.JSONDecodeError reports a position, not
            # content.
            logger.error(
                "[TLS-SETTINGS] Failed to load TLS settings: %s: %s",
                type(e).__name__, e,
            )

    logger.info("[TLS-SETTINGS] Using default TLS settings (no config file found)")
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
        logger.info("[TLS-SETTINGS] TLS settings saved to %s", TLS_CONFIG_FILE)
        return True
    except (PermissionError, OSError) as e:
        logger.warning("[TLS-SETTINGS] Cannot save TLS settings to %s: %s", TLS_CONFIG_FILE, e)
        _cached_tls_settings = settings
        return False
    except Exception as e:
        logger.error("[TLS-SETTINGS] Failed to save TLS settings: %s", e)
        raise


def clear_tls_settings_cache() -> None:
    """Clear the cached TLS settings (forces reload)."""
    global _cached_tls_settings
    _cached_tls_settings = None
    logger.info("[TLS-SETTINGS] TLS settings cache cleared")


def get_tls_settings() -> TLSSettings:
    """Get the current TLS settings."""
    return load_tls_settings()


def verify_tls_settings_integrity_at_startup() -> bool:
    """Probe tls_settings.json mode and ownership at container startup.

    Bead 2owpi, extending the startup integrity probe bead m40pn built for the
    cloud-backup Fernet key (``cloud_storage.crypto.verify_key_integrity_at_startup``)
    to the second credential-bearing file in the same config directory. Both
    are called from one startup block in ``main.py``.

    ``save_tls_settings`` already chmods 0600 on every write, so drift comes
    from outside ECM: a manual edit, a restore, a volume copied without
    permissions. That is precisely the misconfiguration this probe exists to
    surface, and it was previously invisible until somebody thought to look.
    The PO chose this over at-rest encryption on 2026-08-13 for exactly that
    reason.

    Posture matches m40pn:

    * **Mode** is the real control. Drift is REPAIRED with chmod and reported
      at WARNING. A repair that fails is an ERROR.
    * **Ownership** is a weak control under root, because root reads any file
      regardless of owner, so a foreign owner is advisory when we are root and
      an unmissable ERROR when we are not and therefore actually exposed.
    * **Log loudly, but boot.** This never raises. ECM's core is channel
      management and a TLS-config permission problem must not take the app
      down. Nothing here reads the file's CONTENTS, only its metadata.

    Returns:
        True if the file is absent or ends the probe at mode 0600 with no
        exploitable ownership mismatch, False on an unrepaired violation.
    """
    try:
        if not TLS_CONFIG_FILE.exists():
            # TLS is optional. An instance that never configured it has
            # nothing to protect here.
            return True

        st = os.stat(TLS_CONFIG_FILE)
        current_mode = stat.S_IMODE(st.st_mode)

        healthy = True

        if current_mode != _REQUIRED_MODE:
            try:
                os.chmod(TLS_CONFIG_FILE, _REQUIRED_MODE)
                logger.warning(
                    "[TLS-SETTINGS] %s has mode %#o, expected %#o — repaired to "
                    "0600. This file holds DNS-01 provider credentials in clear; "
                    "treat them as disclosed to anyone who could read it "
                    "(tls_settings_status=mode_repaired)",
                    TLS_CONFIG_FILE, current_mode, _REQUIRED_MODE,
                )
            except OSError as chmod_err:
                healthy = False
                logger.error(
                    "[TLS-SETTINGS] %s has mode %#o, expected %#o, and the "
                    "repair failed: %s. DNS-01 provider credentials are "
                    "readable beyond their owner until this is fixed "
                    "(tls_settings_status=mode_repair_failed)",
                    TLS_CONFIG_FILE, current_mode, _REQUIRED_MODE, chmod_err,
                )

        process_uid = os.getuid()
        file_uid = st.st_uid
        if file_uid != process_uid:
            if process_uid == 0:
                logger.info(
                    "[TLS-SETTINGS] %s owned by uid=%s but process is root "
                    "(uid=0); ownership is advisory in a root context, mode is "
                    "the control (tls_settings_status=ownership_advisory)",
                    TLS_CONFIG_FILE, file_uid,
                )
            else:
                healthy = False
                logger.error(
                    "[TLS-SETTINGS] %s is owned by uid=%s but the process runs "
                    "as uid=%s and cannot take ownership. Another account owns "
                    "the file holding this instance's DNS-01 provider "
                    "credentials; rotate them and fix ownership "
                    "(tls_settings_status=ownership_unfixable)",
                    TLS_CONFIG_FILE, file_uid, process_uid,
                )

        return healthy

    except Exception as e:
        # Log-loudly-but-boot. A probe that cannot run is reported, never
        # fatal, and never quotes the file's contents.
        logger.error(
            "[TLS-SETTINGS] STARTUP INTEGRITY PROBE FAILED for %s: %s: %s "
            "(tls_settings_status=startup_probe_failed)",
            TLS_CONFIG_FILE, type(e).__name__, e,
        )
        return False
