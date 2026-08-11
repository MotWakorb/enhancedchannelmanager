"""
Authentication configuration settings.

Manages auth-related configuration including JWT settings, session options,
and auth provider configurations (local, SAML, LDAP, Dispatcharr).
"""
import json
import fcntl
import logging
import os
import secrets
import tempfile
import threading
from pathlib import Path
from contextlib import contextmanager
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
    # Password requirements
    min_password_length: int = 8
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_number: bool = True
    require_special: bool = False


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
    # "local" = username/password, "dispatcharr" = Dispatcharr SSO
    primary_auth_mode: Literal["local", "dispatcharr"] = "local"

    # Whether authentication is required at all
    # If False, the app runs in "open" mode (no login required)
    require_auth: bool = True

    # Sub-settings
    jwt: JWTSettings = JWTSettings()
    session: SessionSettings = SessionSettings()
    local: LocalAuthSettings = LocalAuthSettings()
    dispatcharr: DispatcharrAuthSettings = DispatcharrAuthSettings()

    def is_setup_required(self) -> bool:
        """Check if initial auth setup is required."""
        return not self.setup_complete

    def get_enabled_providers(self) -> list[str]:
        """Get list of enabled authentication providers."""
        providers = []
        if self.local.enabled:
            providers.append("local")
        if self.dispatcharr.enabled:
            providers.append("dispatcharr")
        return providers


# In-memory cache of auth settings
_cached_auth_settings: Optional[AuthSettings] = None
_cached_auth_settings_signature: Optional[tuple[int, int, int]] = None

# Guards load/save/secret-generation (bd-0gt2i): without it, two concurrent
# requests that both observe an empty jwt.secret_key each generate a
# DIFFERENT secret and race the save — tokens signed with the loser's secret
# fail validation (401s) for their entire session. Re-entrant because
# load_auth_settings() calls save_auth_settings() while holding the lock.
# Sync routes run in Starlette's threadpool, so multiple threads can reach
# this module concurrently.
_settings_lock = threading.RLock()


@contextmanager
def _durable_settings_lock():
    """Serialize auth-settings compare/write operations across processes."""
    lock_fd = None
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(CONFIG_DIR / ".auth-settings.lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                logger.error("[AUTH-SETTINGS] Failed to release settings lock")
            finally:
                try:
                    os.close(lock_fd)
                except OSError:
                    logger.error("[AUTH-SETTINGS] Failed to close settings lock")


def _auth_file_signature() -> Optional[tuple[int, int, int]]:
    """Return a cheap cross-process cache key for the durable settings file."""
    try:
        stat = AUTH_CONFIG_FILE.stat()
    except OSError:
        return None
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _durable_user_exists() -> Optional[bool]:
    """Return durable ownership state, or None when it cannot be established."""
    session = None
    try:
        from database import get_session
        from models import User

        session = get_session()
        return session.query(User.id).first() is not None
    except Exception:
        logger.error("[AUTH-SETTINGS] Could not establish durable ownership state")
        return None
    finally:
        if session is not None:
            session.close()


def _safe_settings_after_load_failure() -> AuthSettings:
    """Allow a proven fresh install; otherwise fail closed without disk writes."""
    durable_user_exists = _durable_user_exists()
    if durable_user_exists is False:
        settings = AuthSettings()
    else:
        settings = AuthSettings(setup_complete=True, require_auth=True)
        logger.error("[AUTH-SETTINGS] Authentication settings unavailable; failing closed")
    settings.jwt.secret_key = _generate_secret_key()
    return settings


def _ensure_config_dir() -> bool:
    """Ensure config directory exists. Returns True if successful."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except (PermissionError, OSError) as e:
        logger.warning("[AUTH-SETTINGS] Cannot create config directory %s: %s", CONFIG_DIR, e)
        return False


def _generate_secret_key() -> str:
    """Generate a secure random secret key for JWT signing."""
    return secrets.token_urlsafe(32)


def _sanitize_auth_data(data: dict) -> dict:
    """Replace null values with field defaults to prevent Pydantic validation failures."""
    defaults = AuthSettings()
    for field_name, field_info in AuthSettings.model_fields.items():
        if field_name in data and data[field_name] is None:
            default_val = getattr(defaults, field_name)
            logger.warning("[AUTH-SETTINGS] Field '%s' is null, using default", field_name)
            data[field_name] = default_val
    return data


def load_auth_settings() -> AuthSettings:
    """Load auth settings from file or return defaults."""
    global _cached_auth_settings, _cached_auth_settings_signature

    # A stat is deliberately paid on each lookup. TLS mode has a second
    # request-serving process, so an unconditionally process-local cache can
    # leave that process in first-run/open mode after its peer completes setup.
    # The inode also changes on every atomic save, avoiding timestamp-granularity
    # races. This is metadata I/O only; JSON is re-read only after a change.
    signature = _auth_file_signature()
    if (
        _cached_auth_settings is not None
        and signature == _cached_auth_settings_signature
    ):
        return _cached_auth_settings

    with _settings_lock:
        signature = _auth_file_signature()
        if (
            _cached_auth_settings is not None
            and signature == _cached_auth_settings_signature
        ):
            return _cached_auth_settings

        previous_settings = _cached_auth_settings

        logger.info("[AUTH-SETTINGS] Loading auth settings from %s", AUTH_CONFIG_FILE)
        file_exists = AUTH_CONFIG_FILE.exists()

        if file_exists:
            try:
                data = json.loads(AUTH_CONFIG_FILE.read_text())
                data = _sanitize_auth_data(data)
                _cached_auth_settings = AuthSettings(**data)
                _cached_auth_settings_signature = signature

                # Ensure we have a secret key
                if not _cached_auth_settings.jwt.secret_key:
                    _cached_auth_settings.jwt.secret_key = _generate_secret_key()
                    save_auth_settings(_cached_auth_settings)

                logger.info("[AUTH-SETTINGS] Loaded auth settings, setup_complete: %s", _cached_auth_settings.setup_complete)
                return _cached_auth_settings
            except json.JSONDecodeError:
                logger.error("[AUTH-SETTINGS] Auth settings file is not valid JSON")
            except Exception:
                logger.error("[AUTH-SETTINGS] Failed to load auth settings")

        if file_exists:
            if previous_settings is not None:
                # A transient/partial external write must not weaken an already
                # enforcing process. ECM's own writes are atomic below.
                logger.warning("[AUTH-SETTINGS] Keeping last known settings")
                return previous_settings
            # File exists but failed to parse — use in-memory defaults only.
            # Do NOT overwrite the file; the user's real settings may be recoverable.
            logger.warning("[AUTH-SETTINGS] Existing settings could not be parsed")
            _cached_auth_settings = _safe_settings_after_load_failure()
            _cached_auth_settings_signature = signature
            return _cached_auth_settings

        # No file at all — first-run: generate and persist a secret key
        logger.info("[AUTH-SETTINGS] Auth settings file not found")
        with _durable_settings_lock():
            # Compare-and-set recheck: a peer may have completed setup while
            # this process was proving the database empty and waiting here.
            signature = _auth_file_signature()
            if signature is not None:
                try:
                    data = _sanitize_auth_data(json.loads(AUTH_CONFIG_FILE.read_text()))
                    _cached_auth_settings = AuthSettings(**data)
                    _cached_auth_settings_signature = signature
                    return _cached_auth_settings
                except Exception:
                    logger.error("[AUTH-SETTINGS] Settings changed but could not be loaded")

            _cached_auth_settings = _safe_settings_after_load_failure()
            _cached_auth_settings_signature = signature
            if not _cached_auth_settings.setup_complete:
                # Proof and first write share the durable lock. Only proven
                # absence may create open defaults.
                _save_auth_settings_locked(_cached_auth_settings)

        return _cached_auth_settings


def _save_auth_settings_locked(settings: AuthSettings) -> bool:
    """Save settings while both in-process and durable locks are held."""
    global _cached_auth_settings, _cached_auth_settings_signature

    if not _ensure_config_dir():
        return False

    temporary_path: Optional[Path] = None
    try:
            settings_json = json.dumps(settings.model_dump(), indent=2)
            fd, temporary_name = tempfile.mkstemp(
                dir=CONFIG_DIR,
                prefix=".auth-settings-",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(fd, "w") as temporary_file:
                temporary_file.write(settings_json)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, AUTH_CONFIG_FILE)
            temporary_path = None
            directory_fd = os.open(CONFIG_DIR, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            _cached_auth_settings = settings
            _cached_auth_settings_signature = _auth_file_signature()
            logger.info("[AUTH-SETTINGS] Auth settings saved to %s", AUTH_CONFIG_FILE)
            return True
    except (PermissionError, OSError):
        logger.warning("[AUTH-SETTINGS] Cannot save auth settings")
        return False
    except Exception:
        logger.error("[AUTH-SETTINGS] Failed to save auth settings")
        raise
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def save_auth_settings(settings: AuthSettings) -> bool:
    """Atomically save auth settings under host-wide serialization."""
    with _settings_lock:
        with _durable_settings_lock():
            return _save_auth_settings_locked(settings)


def get_auth_settings() -> AuthSettings:
    """Get the current auth settings."""
    return load_auth_settings()


def get_jwt_secret_key() -> str:
    """Get the JWT secret key, generating one if needed.

    The generate-and-save path is serialized (bd-0gt2i): previously two
    concurrent callers could each generate a different secret, and tokens
    signed with the losing secret would 401 on every subsequent request.
    """
    settings = get_auth_settings()
    if settings.jwt.secret_key:
        return settings.jwt.secret_key

    with _settings_lock:
        # Re-read under the lock: another thread may have generated and
        # saved a secret while we waited.
        settings = get_auth_settings()
        if not settings.jwt.secret_key:
            settings.jwt.secret_key = _generate_secret_key()
            logger.warning(
                "[AUTH-SETTINGS] JWT secret key was empty — generated a new one "
                "(all previously issued tokens are now invalid)"
            )
            save_auth_settings(settings)
        return settings.jwt.secret_key
