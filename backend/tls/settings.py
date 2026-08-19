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


# In-memory cache of TLS settings, guarded by the identity of the file it was
# built from (bead 04c0u.9 remediation).
#
# ECM runs TLS as a SECOND uvicorn process (tls/https_server.py spawns it), and
# ``POST /api/tls/configure`` is not in ``tls/subprocess_proxy._FORWARD_ALLOWLIST``,
# so with TLS active the save that flips ``allow_http_session_cookies`` executes
# in the HTTPS subprocess while the plain-HTTP listener the operator is trying to
# recover on is the MAIN process. An unconditional per-process memo therefore
# left the main process holding the pre-save value for the life of the container:
# the break-glass checkbox was inert in exactly the situation it exists for.
#
# ``clear_tls_settings_cache()`` could not close that — it has no cross-process
# reach, and had no production caller at all. The file is the shared state both
# processes already agree on, so the cache is keyed to it: one ``os.stat`` per
# read, which is cheaper than the two ``Path.is_file()`` stats the cookie-policy
# caller does anyway, and it makes every process converge on the last write
# without any invalidation protocol.
_cached_tls_settings: Optional[TLSSettings] = None
# (st_mtime_ns, st_size, st_ino) of the file the cache was built from, or None
# when it was built from "no file on disk".
_cached_tls_stamp: Optional[tuple] = None
# True when the most recent load found a config file it could NOT parse. That
# is not the same as "TLS is off" and callers making a security decision must
# be able to tell the two apart — see ``tls_settings_load_failed``.
_tls_settings_load_failed: bool = False


# Returned instead of a stat tuple when the file cannot be stat-ed for a reason
# OTHER than absence. Compares equal to itself, so it caches like any other
# stamp, and is distinguishable from ``None`` ("there is genuinely no file").
_STAMP_UNREADABLE = ("<unreadable>",)


def _config_file_stamp() -> Optional[tuple]:
    """Cheap identity of ``tls_settings.json``, or None when it is absent.

    Inode is included so a replace-by-rename — which a backup restore does, and
    which ``save_tls_settings`` below now does on every save — invalidates the
    cache even if the new file lands with the same size and a coincident mtime.

    "Absent" means ``ENOENT`` and nothing else. A bare ``except OSError`` here
    reported an ``EACCES`` on the config directory as "no file", which made
    ``load_tls_settings`` clear ``_tls_settings_load_failed`` and hand back
    ``enabled=False`` — failing OPEN, so the plain-HTTP port silently resumed
    issuing non-``Secure`` session cookies while a running HTTPS subprocess kept
    serving. Every other ``OSError`` is "we cannot tell", which security callers
    must be able to fail closed on.
    """
    try:
        st = os.stat(TLS_CONFIG_FILE)
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.error(
            "[TLS-SETTINGS] Cannot stat %s (%s: %s); treating TLS state as "
            "UNKNOWN rather than absent.",
            TLS_CONFIG_FILE, type(e).__name__, e,
        )
        return _STAMP_UNREADABLE
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def break_glass_environment_override() -> bool:
    """True when ``ECM_ALLOW_HTTP_SESSION_COOKIES`` names an affirmative value.

    Deliberately an allowlist of affirmative spellings and NOT plain
    truthiness. ``docker-compose.yml`` ships
    ``ECM_ALLOW_HTTP_SESSION_COOKIES=${ECM_ALLOW_HTTP_SESSION_COOKIES:-false}``,
    so on a compose deployment the literal string ``false`` is present in the
    environment of every container. Reading "non-empty" as "on" would therefore
    disable the whole protection on every default install — a mutation the
    suite is required to kill (see
    ``tests/unit/test_04c0u9_session_transport.py``).
    """
    return os.environ.get(
        "ECM_ALLOW_HTTP_SESSION_COOKIES", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def tls_settings_load_failed() -> bool:
    """True when the last load found ``tls_settings.json`` present but unusable.

    ``load_tls_settings`` degrades to ``TLSSettings()`` (``enabled=False``) on a
    parse failure, which is the right default for renewal scheduling but the
    WRONG one for cookie transport policy: the HTTPS listener may still be
    serving from key material on disk, and answering "TLS is off" would emit
    cleartext-replayable session cookies. Security callers read this alongside
    the settings and treat "we could not tell" as "assume TLS is on".
    """
    return _tls_settings_load_failed


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
    """Load TLS settings from file, reloading when the file has changed.

    The cache is keyed to the file's stat identity rather than memoized once,
    so a write from the HTTPS subprocess is picked up by the main process (and
    vice versa) on the next read. See the module-level note on
    ``_cached_tls_settings``.
    """
    global _cached_tls_settings, _cached_tls_stamp, _tls_settings_load_failed

    stamp = _config_file_stamp()
    if _cached_tls_settings is not None and stamp == _cached_tls_stamp:
        return _cached_tls_settings

    _cached_tls_stamp = stamp
    _tls_settings_load_failed = False

    logger.info("[TLS-SETTINGS] Loading TLS settings from %s", TLS_CONFIG_FILE)

    if stamp is _STAMP_UNREADABLE:
        # ``_config_file_stamp`` already logged the errno. There is nothing to
        # parse, but this is emphatically not the no-file case.
        _tls_settings_load_failed = True
    elif stamp is not None:
        try:
            data = json.loads(TLS_CONFIG_FILE.read_text())
            _cached_tls_settings = TLSSettings(**data)
            logger.info(
                "[TLS-SETTINGS] Loaded TLS settings, enabled: %s, mode: %s",
                _cached_tls_settings.enabled, _cached_tls_settings.mode,
            )
            return _cached_tls_settings
        except ValidationError as e:
            _tls_settings_load_failed = True
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
            _tls_settings_load_failed = True
            logger.error(
                "[TLS-SETTINGS] Failed to load TLS settings: %s: %s",
                type(e).__name__, e,
            )

    if _tls_settings_load_failed:
        # Loud, and distinguishable from the no-file case below. Callers that
        # make a security decision read ``tls_settings_load_failed()`` and fail
        # closed rather than reading ``enabled=False`` as "TLS is off".
        logger.error(
            "[TLS-SETTINGS] %s could not be read; falling back to defaults. "
            "TLS-dependent security policy will assume TLS is ACTIVE until the "
            "file is repaired.",
            TLS_CONFIG_FILE,
        )
    else:
        logger.info("[TLS-SETTINGS] Using default TLS settings (no config file found)")
    _cached_tls_settings = TLSSettings()
    return _cached_tls_settings


def _write_config_atomically(settings_json: str) -> None:
    """Write ``tls_settings.json`` so a failed write cannot truncate it.

    ``TLS_CONFIG_FILE.write_text(...)`` truncates in place: a disk-full or a
    container kill part-way through left a truncated or zero-byte file, which
    the loader reports as a hard failure — and a load failure is what the
    session-cookie policy fails closed on. The temporary file carries the
    owner-only mode BEFORE the rename, so the credentials in it are never
    briefly group- or world-readable, and ``fsync`` runs before the rename so
    the rename cannot be ordered ahead of the data.

    ``os.replace`` is also what gives the ``st_ino`` leg of the cache key in
    ``_config_file_stamp`` anything to do: an in-place write kept the inode
    forever, reducing the key to ``(st_mtime_ns, st_size)``.
    """
    # PID-qualified so two processes saving at once cannot share a temp file.
    tmp_path = TLS_CONFIG_FILE.with_name(f".{TLS_CONFIG_FILE.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(settings_json)
            handle.flush()
            os.fsync(handle.fileno())
        # Explicit: os.open's mode argument is masked by umask.
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, TLS_CONFIG_FILE)
    finally:
        # A no-op on the success path; os.replace consumed the name.
        tmp_path.unlink(missing_ok=True)


def save_tls_settings(settings: TLSSettings) -> bool:
    """Save TLS settings to file. Returns True if successful.

    The write goes through a temporary file and ``os.replace`` — see
    :func:`_write_config_atomically` for why a truncating write here was a
    security problem and not only a durability one.

    The saving process's own cache is refreshed to the value it just wrote and
    re-stamped to the file it wrote, so the very next read in this process does
    not pay for a reload. Every OTHER process picks the write up through the
    stat guard in ``load_tls_settings`` — there is no invalidation message to
    send and none to miss.
    """
    global _cached_tls_settings, _cached_tls_stamp, _tls_settings_load_failed

    if not _ensure_config_dir():
        _cached_tls_settings = settings
        _cached_tls_stamp = _config_file_stamp()
        _tls_settings_load_failed = False
        return False

    try:
        settings_json = json.dumps(settings.model_dump(), indent=2)
        # Restrictive permissions on settings file (contains API tokens) are
        # applied to the temporary file before it is renamed into place.
        _write_config_atomically(settings_json)
        _cached_tls_settings = settings
        _cached_tls_stamp = _config_file_stamp()
        _tls_settings_load_failed = False
        logger.info("[TLS-SETTINGS] TLS settings saved to %s", TLS_CONFIG_FILE)
        return True
    except (PermissionError, OSError) as e:
        logger.warning("[TLS-SETTINGS] Cannot save TLS settings to %s: %s", TLS_CONFIG_FILE, e)
        _cached_tls_settings = settings
        _cached_tls_stamp = _config_file_stamp()
        _tls_settings_load_failed = False
        return False
    except Exception as e:
        logger.error("[TLS-SETTINGS] Failed to save TLS settings: %s", e)
        raise


def clear_tls_settings_cache() -> None:
    """Clear the cached TLS settings (forces reload).

    Kept for callers that relocate ``CONFIG_DIR`` under them (tests, and the
    backup-restore path), where the file identity the stat guard compares is
    no longer meaningful. It is NOT the mechanism that propagates a write
    between the main process and the HTTPS subprocess — that is the stat guard
    in ``load_tls_settings``, because nothing can call this function in another
    process.
    """
    global _cached_tls_settings, _cached_tls_stamp, _tls_settings_load_failed
    _cached_tls_settings = None
    _cached_tls_stamp = None
    _tls_settings_load_failed = False
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
