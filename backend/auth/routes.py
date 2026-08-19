"""
Authentication API endpoints.

Provides login, logout, token refresh, and password management.
"""
import ipaddress
import logging
import os
import fcntl
import re
import secrets
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_public_base_url, get_settings
from tls.settings import (
    TLS_DIR,
    break_glass_environment_override,
    get_tls_settings,
    tls_settings_load_failed,
)
from tls.storage import CertificateStorage
from database import get_session
from models import User, UserSession, PasswordResetToken
from .password import verify_password, hash_password, validate_password
from .tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
    rotate_refresh_token,
    hash_token,
    TokenExpiredError,
    InvalidTokenError,
    TokenRevokedError,
)
from .settings import get_auth_settings, save_auth_settings
from .dependencies import (
    AuthenticationError,
    get_current_user,
    get_refresh_token_from_request,
    get_token_from_request,
    reject_mcp_service_principal_mutation,
)


logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, enabled=os.environ.get("RATE_LIMIT_ENABLED", "1") != "0")

RESET_ISSUE_RATE = "5/minute"
RESET_VALIDATE_RATE = "10/minute"
PASSWORD_RESET_ACCOUNT_COOLDOWN = timedelta(minutes=5)
PASSWORD_RESET_MAX_ATTEMPTS = 10


def _consume_reset_token(
    session: Session,
    token_id: int,
    consumed_at: datetime,
) -> bool:
    """Claim one unused, unexpired reset credential with a conditional write."""
    return session.query(PasswordResetToken).filter(
        PasswordResetToken.id == token_id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > consumed_at,
    ).update(
        {PasswordResetToken.used_at: consumed_at},
        synchronize_session=False,
    ) == 1


def _serialize_initial_setup():
    """Hold a non-blocking host-wide lock for the first-admin transaction."""
    from . import settings as auth_settings_module

    lock_fd = None
    try:
        auth_settings_module.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = auth_settings_module.CONFIG_DIR / ".auth-setup.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if lock_fd is not None:
            os.close(lock_fd)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Initial setup is already in progress.",
        ) from None
    except OSError:
        if lock_fd is not None:
            os.close(lock_fd)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Initial setup is temporarily unavailable.",
        ) from None

    try:
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            logger.error("[AUTH] Failed to release initial setup lock")
        finally:
            try:
                os.close(lock_fd)
            except OSError:
                logger.error("[AUTH] Failed to close initial setup lock")


def _cleanup_expired_sessions(session: Session, user_id: int) -> int:
    """Delete expired sessions for a specific user. Returns count deleted."""
    expired = session.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.expires_at < datetime.utcnow(),
    ).all()
    count = len(expired)
    for s in expired:
        session.delete(s)
    if count:
        session.commit()
        logger.info("[AUTH] Cleaned up %d expired session(s) for user_id=%s", count, user_id)
    return count


def send_password_reset_email(to_email: str, reset_token: str, base_url: str) -> bool:
    """
    Send a password reset email using the shared SMTP settings.

    Args:
        to_email: Recipient email address.
        reset_token: The raw reset token to include in the link.
        base_url: The base URL of the application (e.g., http://localhost:6100 by default).

    Returns:
        True if email was sent successfully, False otherwise.
    """
    settings = get_settings()

    if not settings.is_smtp_configured():
        logger.warning("[AUTH] Password reset email not sent: SMTP not configured")
        return False

    smtp_host = settings.smtp_host
    smtp_port = settings.smtp_port
    smtp_user = settings.smtp_user
    smtp_password = settings.smtp_password
    from_email = settings.smtp_from_email
    from_name = settings.smtp_from_name or "Enhanced Channel Manager"
    use_tls = settings.smtp_use_tls
    use_ssl = settings.smtp_use_ssl

    # Build the reset URL
    reset_url = f"{base_url}/reset-password?token={reset_token}"

    # Build the email
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Password Reset Request"
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email

    # Plain text version
    plain_text = f"""Password Reset Request

You requested to reset your password for Enhanced Channel Manager.

Click the link below to reset your password:
{reset_url}

This link will expire in 1 hour.

If you didn't request this, you can safely ignore this email.

---
Enhanced Channel Manager
"""

    # HTML version
    html_text = f"""
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #4F46E5; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; }}
            .body {{ background-color: #f8f9fa; padding: 30px; border: 1px solid #e9ecef; border-top: none; }}
            .message {{ color: #333; line-height: 1.6; }}
            .button {{ display: inline-block; background-color: #4F46E5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0; font-weight: 600; }}
            .button:hover {{ background-color: #4338CA; }}
            .footer {{ font-size: 12px; color: #666; margin-top: 20px; padding-top: 20px; border-top: 1px solid #e9ecef; }}
            .warning {{ color: #666; font-size: 14px; margin-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Password Reset</h1>
            </div>
            <div class="body">
                <div class="message">
                    <p>You requested to reset your password for Enhanced Channel Manager.</p>
                    <p>Click the button below to set a new password:</p>
                    <p style="text-align: center;">
                        <a href="{reset_url}" style="display: inline-block; background-color: #4F46E5; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0; font-weight: 600;">Reset Password</a>
                    </p>
                    <p class="warning">This link will expire in 1 hour.</p>
                    <p class="warning">If you didn't request this password reset, you can safely ignore this email.</p>
                </div>
                <div class="footer">
                    <p>If the button doesn't work, copy and paste this link into your browser:</p>
                    <p style="word-break: break-all;">{reset_url}</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_text, "html"))

    try:
        # Connect to SMTP server
        if use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=10)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)

        try:
            if use_tls and not use_ssl:
                server.starttls(context=ssl.create_default_context())

            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)

            server.sendmail(from_email, [to_email], msg.as_string())
            # Deliberately silent on success (bead
            # enhancedchannelmanager-5u5h9). This helper only ever receives an
            # address, so anything it logged about a successful send named the
            # subscriber's email; and its caller logged the SAME event on the
            # same line, which wrote the address into the log twice and made
            # the log read as two sends. The one success line now lives in
            # ``forgot_password``, which holds the user and can say
            # ``user_id=``. Failures below stay here: they carry an SMTP
            # diagnostic the caller cannot see, and none of them names the
            # recipient.
            return True

        finally:
            server.quit()

    except smtplib.SMTPAuthenticationError as e:
        logger.error("[AUTH] SMTP authentication failed: %s", e)
        return False
    except smtplib.SMTPException as e:
        logger.error("[AUTH] SMTP error sending password reset email: %s", e)
        return False
    except Exception as e:
        logger.exception("[AUTH] Failed to send password reset email: %s", e)
        return False


# Create router with auth tag
router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# Request/Response models
class LoginRequest(BaseModel):
    """Login request body."""
    username: str
    password: str


class UserResponse(BaseModel):
    """User data for API responses."""
    id: int
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_admin: bool
    is_active: bool
    auth_provider: str
    external_id: Optional[str] = None

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    """Login response body."""
    user: UserResponse
    message: str = "Login successful"
    # Read-only metadata: seconds until the access token issued alongside this
    # response expires. Lets the frontend schedule a proactive refresh instead
    # of waiting for the first 401 after expiry (bd-3ymo4). No behavior change.
    access_token_expires_in: Optional[int] = None


class MeResponse(BaseModel):
    """Current user response body."""
    user: UserResponse
    # Read-only metadata: seconds until the CURRENT access token expires
    # (remaining lifetime, not the full configured lifetime — /me is often
    # called mid-lifetime on page load). None when unavailable (e.g. static
    # MCP key, auth disabled). See bd-3ymo4.
    access_token_expires_in: Optional[int] = None


class RefreshResponse(BaseModel):
    """Token refresh response body."""
    message: str = "Token refreshed"
    # Read-only metadata: seconds until the freshly minted access token
    # expires (bd-3ymo4).
    access_token_expires_in: Optional[int] = None


class LogoutResponse(BaseModel):
    """Logout response body."""
    message: str = "Logged out successfully"


class AuthStatusResponse(BaseModel):
    """Auth status for frontend."""
    setup_complete: bool
    require_auth: bool
    enabled_providers: list[str]
    primary_auth_mode: str
    smtp_configured: bool = False


# Password Management Models
class ChangePasswordRequest(BaseModel):
    """Change password request body."""
    current_password: str
    new_password: str


class ChangePasswordResponse(BaseModel):
    """Change password response body."""
    message: str = "Password changed successfully"


class ForgotPasswordRequest(BaseModel):
    """Forgot password request body."""
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Forgot password response body (always returns 200 for security)."""
    message: str = "If an account with that email exists, a password reset link has been sent."


class ResetPasswordRequest(BaseModel):
    """Reset password with token request body."""
    token: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    """Reset password response body."""
    message: str = "Password reset successfully"


# First-Run Setup Models
class SetupRequiredResponse(BaseModel):
    """Setup required status response."""
    required: bool


class SetupRequest(BaseModel):
    """Initial admin setup request body."""
    username: str
    email: EmailStr
    password: str


class SetupResponse(BaseModel):
    """Initial admin setup response body."""
    user: UserResponse
    message: str = "Setup complete"


def _access_token_lifetime_seconds() -> int:
    """Configured access-token lifetime in seconds (read-only metadata)."""
    return get_auth_settings().jwt.access_token_expire_minutes * 60


def _access_token_seconds_remaining(request: Request) -> Optional[int]:
    """Best-effort seconds until the request's access token expires.

    Returns None when it cannot be determined (no token, static MCP key,
    malformed/expired token). Read-only metadata for the frontend's
    proactive refresh scheduling (bd-3ymo4) — never used for auth decisions.
    """
    try:
        token = get_token_from_request(request)
        if not token:
            return None
        claims = decode_token(token)
        exp = claims.get("exp")
        if exp is None:
            return None
        # JWT exp is a UTC epoch timestamp; compare against epoch time
        # (NOT datetime.utcnow().timestamp(), which misreads naive as local).
        remaining = int(exp - time.time())
        return max(remaining, 0)
    except Exception as e:
        logger.debug("[AUTH] Could not compute access token remaining lifetime: %s", e)
        return None


class _SessionTransport(NamedTuple):
    """The transport verdict for one request, decided from local state only."""

    #: Emit session cookies with ``Secure``.
    secure: bool
    #: ECM terminates TLS itself and this request arrived in cleartext anyway,
    #: with no break-glass and no https ``public_base_url``. There is no
    #: legitimate plaintext client in that configuration, so routes that MINT a
    #: browser session refuse rather than hand out a cookie the browser will
    #: discard (RFC 6265bis 5.6) and then bounce the operator back to the login
    #: form with no error.
    refuse_plaintext: bool
    #: An operator escape hatch is open (stored flag or environment variable).
    break_glass: bool


def _ecm_terminates_tls() -> bool:
    """True when ECM's own HTTPS listener is serving, or we cannot tell.

    ``has_certificate()`` is the SAME predicate ``tls/https_server.py`` starts
    the listener from (``CertificateStorage(TLS_DIR)``, i.e. the hard-coded
    ``cert.pem`` / ``key.pem`` under the TLS directory). Reading
    ``tls_settings.cert_path`` / ``key_path`` instead would be a second,
    independent notion of "the certificate exists" that a backup restore of
    ``tls_settings.json`` can move out from under the listener.

    Fails CLOSED on an unreadable ``tls_settings.json``: ``load_tls_settings``
    degrades to ``enabled=False`` there, and reading that as "TLS is off" would
    emit cleartext-replayable cookies while the HTTPS listener kept serving.
    """
    if tls_settings_load_failed():
        return True
    tls_settings = get_tls_settings()
    return bool(tls_settings.enabled and CertificateStorage(TLS_DIR).has_certificate())


# Once per process, re-armed by ``_reset_session_transport_log_state`` in tests.
_break_glass_suppression_warned: bool = False


def _reset_session_transport_log_state() -> None:
    """Re-arm the once-per-process break-glass warning."""
    global _break_glass_suppression_warned
    _break_glass_suppression_warned = False


def _warn_break_glass_suppression() -> None:
    """WARN, once per process, that break-glass is downgrading live sessions.

    Bead 04c0u.9 remediation. The escape hatch was previously silent in every
    surface: no log at issue time, no field in ``GET /api/tls/status``, nothing
    in the UI. An operator who set it at 02:00 to recover and then forgot the
    line had every session cookie shipping without ``Secure`` indefinitely,
    with a 7-day ``refresh_token`` capturable by anyone on the LAN, and no
    signal anywhere. There is no security-audit facility in this codebase, so a
    rate-limited ``logger.warning`` is the house pattern for this.
    """
    global _break_glass_suppression_warned
    if _break_glass_suppression_warned:
        return
    _break_glass_suppression_warned = True
    logger.warning(
        "[AUTH] Break-glass is ON: session cookies are being issued WITHOUT "
        "Secure over plaintext HTTP even though this instance is otherwise "
        "protected. Anyone who can observe this network can steal a live "
        "session. Turn off 'Emergency recovery: allow authenticated sessions "
        "over HTTP' in TLS Settings and unset "
        "ECM_ALLOW_HTTP_SESSION_COOKIES as soon as HTTPS is reachable."
    )


def _session_transport(request: Request) -> _SessionTransport:
    """Decide session-cookie transport policy from trusted local state.

    Two trusted signals, both read from this process rather than from the
    request body or headers:

    * this connection was terminated as HTTPS by ECM itself, and
    * ``public_base_url``, the operator-configured canonical origin, is an
      ``https://`` origin.

    The second is deliberately the SAME notion of proxy trust bead
    ``...-qsqfv`` established for the password-reset link (``config.
    get_public_base_url``), not a second one: a reverse-proxy deployment
    declares its external scheme once, in configuration, and everything that
    needs to know reads it there.

    ECM's own policy code never consults ``X-Forwarded-Proto`` /
    ``X-Forwarded-Host`` / ``Forwarded``. The ECM backend has no trusted-proxy
    allowlist of its own (the one in ``mcp-server/config.py`` belongs to the
    sidecar's listener, a different process). Note what that does and does not
    claim: uvicorn's ``ProxyHeadersMiddleware`` is enabled by default and runs
    OUTSIDE this application, so for a client within ``FORWARDED_ALLOW_IPS``
    (default ``127.0.0.1``) it may itself rewrite ``scope['scheme']`` before
    any ECM code sees the request. The property this function holds is that ECM
    adds no header trust of its own on top of that.

    ORDER IS LOAD-BEARING. Break-glass is evaluated BEFORE the
    ``public_base_url`` signal: with an ``https://`` public base URL configured
    the old order returned early and made BOTH escape hatches unreachable, so
    the documented recovery did nothing on precisely the reverse-proxy
    deployments whose operators had followed the qsqfv advice. Genuine TLS on
    this connection still wins over everything, so break-glass can never
    downgrade a real HTTPS session.
    """
    tls_settings = get_tls_settings()
    break_glass = bool(
        tls_settings.allow_http_session_cookies or break_glass_environment_override()
    )

    if request.url.scheme.lower() == "https":
        return _SessionTransport(secure=True, refuse_plaintext=False, break_glass=break_glass)

    proxy_https = get_public_base_url().lower().startswith("https://")
    tls_active = _ecm_terminates_tls()

    if break_glass:
        # Only warn when the hatch actually changed the answer. With no TLS and
        # no https origin the cookies would be non-Secure regardless, and a
        # warning there would be noise on every plain-HTTP install.
        if proxy_https or tls_active:
            _warn_break_glass_suppression()
        return _SessionTransport(secure=False, refuse_plaintext=False, break_glass=True)

    if proxy_https:
        return _SessionTransport(secure=True, refuse_plaintext=False, break_glass=False)

    # ECM terminates TLS and this arrived in cleartext: protect the cookie AND
    # refuse to mint a session at all (see ``refuse_plaintext``).
    return _SessionTransport(
        secure=tls_active, refuse_plaintext=tls_active, break_glass=False
    )


def _auth_cookie_secure(request: Request) -> bool:
    """Return the cookie transport policy from trusted local state.

    Single source of truth for the ``secure=`` attribute of every session
    cookie ECM emits; see :func:`_session_transport` for the policy itself.
    """
    return _session_transport(request).secure


def _https_sign_in_url(request: Request) -> str:
    """Best-effort HTTPS address of this instance, for a recovery message."""
    tls_settings = get_tls_settings()
    host = tls_settings.domain or (request.url.hostname or "your-ecm-host")
    return f"https://{host}:{tls_settings.https_port}"


def _require_secure_session_transport(request: Request) -> None:
    """Refuse to mint a browser session over cleartext when ECM terminates TLS.

    Bead 04c0u.9 remediation, PO-authorised behaviour change. ``/api/auth/login``
    is in ``AUTH_EXEMPT_PATHS``, so before this the request succeeded
    server-side — password verified, ``UserSession`` row created,
    ``200 {"message": "Login successful"}`` — and only then did the browser
    silently discard the ``Secure`` cookie per RFC 6265bis 5.6. The SPA resolved
    on the 200, the next call 401'd, and the operator was bounced back to the
    login form with no error shown, so the usual response was to retry and ship
    the cleartext password again.

    The predicate is narrow on purpose: it fires ONLY when ECM itself is
    terminating TLS, break-glass is closed, this request is cleartext, and no
    ``https://`` ``public_base_url`` is configured. In that configuration there
    is no legitimate plaintext client, so this cannot break a reverse-proxy
    deployment.

    Raised as 403 rather than 401 deliberately: the SPA's ``fetchJson`` treats
    401 as "try a token refresh and retry", which would bury the message.
    """
    if not _session_transport(request).refuse_plaintext:
        return
    logger.warning(
        "[AUTH] Refused a plaintext session request from %s: ECM is terminating "
        "TLS and break-glass is closed.",
        _client_address(request),
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"This instance terminates TLS, so ECM will not start a browser "
            f"session over plaintext HTTP. Sign in at {_https_sign_in_url(request)} "
            f"instead. If HTTPS is unreachable, an admin can enable 'Emergency "
            f"recovery: allow authenticated sessions over HTTP' in TLS Settings, "
            f"or set ECM_ALLOW_HTTP_SESSION_COOKIES=true on the container and "
            f"restart ECM."
        ),
    )


def _set_access_cookie(
    response: Response,
    access_token: str,
    request: Request,
) -> None:
    """Set ONLY the short-lived access-token cookie.

    Used by the predecessor path (bd-x67qe, bead upkp1), which must NOT touch
    the refresh cookie: the browser's jar already holds the successor's
    refresh token, and re-sending the superseded one would regress it.
    """
    settings = get_auth_settings()
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=_auth_cookie_secure(request),
        samesite="lax",
        max_age=settings.jwt.access_token_expire_minutes * 60,
        path="/",
    )


def _set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    request: Request,
) -> None:
    """
    Set authentication cookies on the response.

    Args:
        response: FastAPI response object.
        access_token: JWT access token.
        refresh_token: JWT refresh token.
        request: Request whose trusted transport context selects cookie policy.
    """
    settings = get_auth_settings()

    # One policy decision for both cookies: _set_access_cookie derives its own
    # from the same request, and a split verdict between the two would be a
    # bug, not a feature.
    secure = _auth_cookie_secure(request)

    # Access token - short lived, httpOnly for security
    _set_access_cookie(response, access_token, request)

    # Refresh token - longer lived, httpOnly for security
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.jwt.refresh_token_expire_days * 24 * 60 * 60,
        path="/api/auth",  # Only sent to auth endpoints
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    """Clear authentication cookies, mirroring the attributes they were set with.

    Starlette's ``delete_cookie`` defaults to ``secure=False, httponly=False,
    samesite="lax"``. Deletion matches on (name, domain, path) so the omission
    works today, but RFC 6265bis 8.6 is explicit that a non-secure origin
    cannot overwrite a ``Secure`` cookie, and a deletion that does not mirror
    its issue-time attributes is a trap for the next change here. Same request,
    same ``_auth_cookie_secure``, same answer as ``_set_auth_cookies``.
    """
    secure = _auth_cookie_secure(request)
    response.delete_cookie(
        key="access_token",
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key="refresh_token",
        path="/api/auth",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(session: Session = Depends(get_session)):
    """
    Get authentication status and configuration.

    Returns information about whether auth is enabled, setup complete,
    and which providers are available. This endpoint is always public.
    """
    auth_settings = get_auth_settings()
    app_settings = get_settings()

    # Auto-fix setup_complete if users exist but flag is False
    # This handles upgrades where users were created before auth system
    setup_complete = auth_settings.setup_complete
    if not setup_complete:
        user_count = session.query(User).count()
        if user_count > 0:
            setup_complete = True
            # Persist the fix
            from .settings import save_auth_settings
            auth_settings.setup_complete = True
            save_auth_settings(auth_settings)

    return AuthStatusResponse(
        setup_complete=setup_complete,
        require_auth=auth_settings.require_auth,
        enabled_providers=auth_settings.get_enabled_providers(),
        primary_auth_mode=auth_settings.primary_auth_mode,
        smtp_configured=app_settings.is_smtp_configured(),
    )


# =============================================================================
# First-Run Setup
# =============================================================================

@router.get("/setup-required", response_model=SetupRequiredResponse)
async def check_setup_required(
    session: Session = Depends(get_session),
):
    """
    Check if initial setup is required.

    Returns {required: true} if no users exist in the database.
    This endpoint is always public - used to show setup wizard.
    """
    user_count = session.query(User).count()
    return SetupRequiredResponse(required=user_count == 0)


@router.post("/setup", response_model=SetupResponse, status_code=status.HTTP_201_CREATED)
async def initial_setup(
    setup_request: SetupRequest,
    session: Session = Depends(get_session),
    _setup_lock=Depends(_serialize_initial_setup),
):
    """
    Create the initial admin user during first-run setup.

    This endpoint only works when no users exist in the database.
    The first user created via this endpoint is automatically an admin.
    """
    # Check if any users already exist
    user_count = session.query(User).count()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup already completed. Users already exist.",
        )

    # Validate password strength
    password_result = validate_password(setup_request.password, setup_request.username)
    if not password_result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=password_result.error,
        )

    from models import UserIdentity

    # Create the first admin user
    user = User(
        username=setup_request.username,
        email=setup_request.email,
        password_hash=hash_password(setup_request.password),
        auth_provider="local",
        is_admin=True,  # First user is always admin
        is_active=True,
    )
    session.add(user)
    session.flush()  # Get user ID

    # Create local identity for the user
    identity = UserIdentity(
        user_id=user.id,
        provider="local",
        identifier=setup_request.username,
        external_id=None,
    )
    session.add(identity)

    # Persist the gate before returning success. The global middleware and
    # every ``*_if_enabled`` dependency key on this flag; creating the user
    # without it leaves the entire admin API anonymous until a later status
    # request happens to repair the state (bead qg14z).
    auth_settings = get_auth_settings()
    previous_setup_complete = auth_settings.setup_complete
    completed_auth_settings = auth_settings.model_copy(deep=True)
    completed_auth_settings.setup_complete = True
    try:
        settings_persisted = save_auth_settings(completed_auth_settings)
    except Exception:
        session.rollback()
        logger.error("[AUTH] Initial setup settings persistence failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Setup could not be persisted.",
        )
    if not settings_persisted:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Setup could not be persisted.",
        )

    try:
        session.commit()
    except Exception:
        session.rollback()

        # A database driver may raise after COMMIT reached durable storage.
        # Verify through an independent Session before considering a rollback
        # of the auth gate. Ambiguous outcomes remain fail-closed (True).
        durable_user_exists: Optional[bool] = None
        verification_session = None
        try:
            verification_session = Session(bind=session.get_bind())
            durable_user_exists = verification_session.query(User.id).first() is not None
        except Exception:
            logger.error("[AUTH] Could not verify initial setup commit outcome")
        finally:
            if verification_session is not None:
                verification_session.close()

        if durable_user_exists is False:
            restored_auth_settings = completed_auth_settings.model_copy(deep=True)
            restored_auth_settings.setup_complete = previous_setup_complete
            try:
                settings_restored = save_auth_settings(restored_auth_settings)
            except Exception:
                settings_restored = False
            if not settings_restored:
                logger.error("[AUTH] Failed to restore setup state after database failure")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Setup could not be completed.",
        ) from None
    session.refresh(user)

    logger.info("[AUTH] Initial setup completed. Admin user created")

    return SetupResponse(
        user=UserResponse.model_validate(user),
        message="Setup complete",
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(
    login_request: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Authenticate user and return JWT tokens.

    Sets httpOnly cookies with access and refresh tokens.
    Uses the user_identities table to find the user by local identity.
    """
    # Before any credential is read: an instance that terminates TLS itself
    # has no legitimate plaintext client (bead 04c0u.9 remediation).
    _require_secure_session_transport(request)

    from models import UserIdentity

    # First, try to find user via identity table
    identity = session.query(UserIdentity).filter(
        UserIdentity.provider == "local",
        UserIdentity.identifier == login_request.username,
    ).first()

    user = None
    if identity:
        user = identity.user
    else:
        # Fallback to direct user lookup for backwards compatibility
        user = session.query(User).filter(User.username == login_request.username).first()

    client_ip = _client_address(request)

    if user is None:
        logger.warning("[AUTH] Login attempt for nonexistent user: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Check if user has a local identity (can log in with password)
    has_local_identity = session.query(UserIdentity).filter(
        UserIdentity.user_id == user.id,
        UserIdentity.provider == "local",
    ).first() is not None

    # If no local identity, check if user was created with local auth_provider (legacy)
    if not has_local_identity and user.auth_provider != "local":
        logger.warning("[AUTH] Non-local user attempted local login: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please use your configured authentication provider to log in",
        )

    # Verify password
    if not user.password_hash or not verify_password(login_request.password, user.password_hash):
        logger.warning("[AUTH] Failed login attempt for user: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Update identity last_used_at if we found via identity
    if identity:
        identity.last_used_at = datetime.utcnow()

    # Check if user is active
    if not user.is_active:
        logger.warning("[AUTH] Login attempt for disabled user: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    # Create tokens
    access_token = create_access_token(
        user_id=user.id, username=user.username, auth_epoch=user.auth_epoch
    )
    refresh_token = create_refresh_token(user_id=user.id)

    # Create session record
    settings = get_auth_settings()
    user_session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", "")[:500],
        expires_at=datetime.utcnow() + timedelta(days=settings.jwt.refresh_token_expire_days),
    )
    session.add(user_session)

    # Update last login
    user.last_login_at = datetime.utcnow()
    session.commit()

    # Clean up expired sessions for this user
    _cleanup_expired_sessions(session, user.id)

    # Set cookies
    _set_auth_cookies(response, access_token, refresh_token, request)

    logger.info("[AUTH] User logged in: %s", user.username)

    return LoginResponse(
        user=UserResponse.model_validate(user),
        message="Login successful",
        access_token_expires_in=_access_token_lifetime_seconds(),
    )


@router.get("/me", response_model=MeResponse)
async def get_current_user_info(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user information.

    Requires valid access token.
    """
    return MeResponse(
        user=UserResponse.model_validate(current_user),
        access_token_expires_in=_access_token_seconds_remaining(request),
    )


class UpdateProfileRequest(BaseModel):
    """Update profile request body."""
    display_name: Optional[str] = None
    email: Optional[EmailStr] = None


class UpdateProfileResponse(BaseModel):
    """Update profile response body."""
    user: UserResponse
    message: str = "Profile updated"


@router.put("/me", response_model=UpdateProfileResponse)
async def update_current_user_profile(
    update_request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Update current user's profile.

    Allows users to update their display name and email.
    """
    # The transient MCP service principal has no DB row; mutating it (and the
    # subsequent session.refresh) would raise a 500. Reject with a clean 403
    # (bd-1wq7z.24 (c)) — the static MCP key is a service credential, not a
    # user account it can edit.
    reject_mcp_service_principal_mutation(current_user)

    # Update fields if provided
    if update_request.display_name is not None:
        current_user.display_name = update_request.display_name or None

    if update_request.email is not None:
        # Check if email is already used by another user
        if update_request.email:
            existing = session.query(User).filter(
                User.email == update_request.email,
                User.id != current_user.id,
            ).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already in use",
                )
        current_user.email = update_request.email or None

    current_user.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(current_user)

    logger.info("[AUTH] User %s updated their profile", current_user.username)

    return UpdateProfileResponse(
        user=UserResponse.model_validate(current_user),
        message="Profile updated",
    )


# Reason code for the one refusal that is the closest thing to a replay
# signal this server can emit: signature and expiry check out, but the
# presented credential matches no live session, current or predecessor. It
# gets its own log message (bead yhk3r, L-2) so it is greppable on its own.
_REFRESH_DENIAL_UNKNOWN_SESSION = "unknown_session"

# Bounded refusal logging for POST /api/auth/refresh (bead yhk3r, L-5).
#
# The endpoint is unauthenticated and deliberately NOT rate limited: the
# existing 5/min login limiter already makes several e2e specs unrunnable, and
# behind a proxy ``get_remote_address`` can collapse every LAN client onto one
# address (filed separately as SEC-03). One WARNING per refusal on an endpoint
# anyone can POST to is therefore a disk-fill vector, so refusals are deduped
# per (reason code, client address) for this interval. Nothing is dropped
# silently: refusals skipped during an interval are counted and reported on
# the next line that does get emitted.
_REFRESH_DENIAL_LOG_INTERVAL_SECONDS = 60.0

# Upper bound on tracked (reason, client) pairs, so a flood from many
# addresses cannot grow the dedupe map without limit.
_REFRESH_DENIAL_TRACKED_PAIRS = 256

# (reason, client) -> (monotonic time of the last emitted line, refusals
# skipped since then).
_refresh_denial_log_state = {}


def _reset_refresh_denial_log_state() -> None:
    """Forget all refusal dedupe bookkeeping.

    Test seam: the state is module-global and survives between tests, so a
    test asserting on refusal logging clears it first rather than depending
    on whatever earlier tests left behind.
    """
    _refresh_denial_log_state.clear()


def _refresh_denials_skipped_since_last(reason: str, client: str) -> Optional[int]:
    """Record one refusal against the dedupe map.

    Returns the number of refusals skipped since the last emitted line for
    this (reason, client) pair, or ``None`` when this refusal is itself
    inside the dedupe interval and must not be emitted.
    """
    now = time.monotonic()
    key = (reason, client)
    entry = _refresh_denial_log_state.get(key)

    if entry is not None and now - entry[0] < _REFRESH_DENIAL_LOG_INTERVAL_SECONDS:
        _refresh_denial_log_state[key] = (entry[0], entry[1] + 1)
        return None

    if (
        entry is None
        and len(_refresh_denial_log_state) >= _REFRESH_DENIAL_TRACKED_PAIRS
    ):
        for stale_key in [
            k
            for k, v in _refresh_denial_log_state.items()
            if now - v[0] >= _REFRESH_DENIAL_LOG_INTERVAL_SECONDS
        ]:
            del _refresh_denial_log_state[stale_key]
        if len(_refresh_denial_log_state) >= _REFRESH_DENIAL_TRACKED_PAIRS:
            # Still full of live entries: start over rather than retain a map
            # that can only grow. Worst case is one extra line per pair.
            _refresh_denial_log_state.clear()

    _refresh_denial_log_state[key] = (now, 0)
    return entry[1] if entry is not None else 0


def _forwarded_address(value: str) -> Optional[str]:
    """Return ``value`` as a canonical IP literal, or ``None`` if it is not one.

    ``X-Forwarded-For`` is caller-supplied and is only ever supposed to hold
    an IP address (proxies may append a port, and IPv6 is bracketed when a
    port is present). Anything else is discarded rather than trimmed: see
    :func:`_client_address` for why the difference matters.
    """
    candidate = value.strip()
    if not candidate:
        return None

    if candidate.startswith("["):
        # "[2001:db8::1]:41234" — the bracketed form, with or without a port.
        host, closed, _ = candidate.partition("]")
        if not closed:
            return None
        candidate = host[1:]
    elif candidate.count(":") == 1:
        # "203.0.113.7:41234". A bare IPv6 address has more than one colon,
        # so this cannot truncate one.
        candidate = candidate.split(":")[0]

    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _client_address(request: Request) -> str:
    """Best-effort client address for an auth log line.

    The returned value is ALWAYS either an IP literal this function itself
    rendered from :mod:`ipaddress`, the socket peer address, or the constant
    ``"unknown"`` — never a substring of a request header.

    That is the whole point of the function, and it is stricter than
    truncating on purpose. ``X-Forwarded-For`` is set by whoever sent the
    request; before this, its first comma-separated field was logged verbatim
    and unbounded by the refusal lines in :func:`_deny_refresh` and by the
    login-failure lines. A caller could therefore choose the text that landed
    in an auth log: a refresh token, its full sha256 hash (which L-4 of the
    upkp1 security spec says must never be logged, and which is separately a
    CodeQL finding), or padding to fill the disk. Truncating would have
    bounded the length while still emitting attacker-chosen text; validating
    the shape means there is no attacker-chosen text to emit, which also
    closes forged-log-line injection at this layer regardless of what the
    HTTP parser in front of it accepts in a header value.

    A header that does not parse as an address falls back to the socket peer
    silently. The peer is the more truthful answer in that case, and the
    alternative — logging that a bad header arrived — would hand the same
    unauthenticated caller a second way to generate lines.
    """
    forwarded = _forwarded_address(
        request.headers.get("X-Forwarded-For", "").split(",")[0]
    )
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


# A run this long of token-alphabet characters does not occur in a real
# User-Agent (its longest such run is a product token like "AppleWebKit"),
# but it is exactly the shape of a secret: a sha256 hex digest is 64
# characters and each dot-separated segment of a JWT is longer still.
_SECRET_SHAPED_RUN = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")

# Cap on the User-Agent as logged. Long enough to keep the platform and
# browser tokens that make the field worth logging at all.
_LOGGED_AGENT_CHARS = 80


def _log_safe_agent(value: Optional[str]) -> str:
    """Render a caller-supplied User-Agent as a single safe log field.

    Unlike an address, a User-Agent is free-form, so there is no shape to
    validate it against and the defence has to be applied to the value
    itself. Three things happen, in this order:

    1. secret-shaped runs are redacted, so a caller cannot get a refresh
       token or its hash into an auth log by putting it here instead of in
       ``X-Forwarded-For`` (a 64-character hash fits inside the cap below
       intact, so truncation alone would not stop it);
    2. everything outside printable ASCII is dropped, so no newline,
       carriage return or terminal escape can forge a second log line or
       rewrite the operator's console;
    3. the result is truncated.

    Redaction runs before truncation so a secret cannot survive by sitting
    across the cut.
    """
    if not value:
        return ""
    redacted = _SECRET_SHAPED_RUN.sub("[redacted]", value)
    printable = "".join(c for c in redacted if " " <= c <= "~")
    return printable[:_LOGGED_AGENT_CHARS]


def _deny_refresh(
    request: Request,
    reason: str,
    detail: str,
    *,
    claims: Optional[dict] = None,
) -> AuthenticationError:
    """Log a refresh refusal at WARNING and return the 401 to raise.

    Used as ``raise _deny_refresh(...)`` so every refusal path in
    :func:`refresh_tokens` emits exactly one line carrying its own reason
    code (bead yhk3r). Before this, the endpoint refused in complete silence:
    38 hours of container logs held zero refresh lines of any kind, which is
    why bead upkp1 had to be diagnosed from a browser's error body instead of
    from the server that produced the 401.

    What is logged, and what is not: the reason code, the subject and ``jti``
    from claims that already passed the signature check, the client address
    and a truncated User-Agent. NEVER the presented credential and never the
    stored hash of one. The ``jti`` names the token without being usable as
    one, and a full hash in a log line would additionally be a CodeQL finding.

    That guarantee has to survive the two caller-supplied fields as well, or
    it is only a guarantee about the arguments this function happens to pass:
    an unauthenticated caller who put a token or its hash in ``X-Forwarded-For``
    or ``User-Agent`` would otherwise have written it into the log itself.
    :func:`_client_address` and :func:`_log_safe_agent` are what close that,
    and property 19 asserts it over both headers.
    """
    client = _client_address(request)
    subject = None
    jti = None
    if claims:
        subject = claims.get("sub")
        jti = claims.get("jti")
    agent = _log_safe_agent(request.headers.get("User-Agent"))

    skipped = _refresh_denials_skipped_since_last(reason, client)
    if skipped is None:
        logger.debug(
            "[AUTH] Refresh refused (%s) for client=%s, deduped within the "
            "%.0fs refusal-logging interval",
            reason,
            client,
            _REFRESH_DENIAL_LOG_INTERVAL_SECONDS,
        )
    elif reason == _REFRESH_DENIAL_UNKNOWN_SESSION:
        logger.warning(
            "[AUTH] Refresh refused (%s): valid signature, but the presented "
            "credential matches no live session, current or predecessor. "
            "Either a superseded token was replayed, or the client is holding "
            "a stale cookie from a deleted session, an earlier install or a "
            "secret rotation. user_id=%s jti=%s client=%s agent=%s "
            "skipped_since_last=%d",
            reason,
            subject,
            jti,
            client,
            agent,
            skipped,
        )
    else:
        logger.warning(
            "[AUTH] Refresh refused (%s): user_id=%s jti=%s client=%s "
            "agent=%s skipped_since_last=%d",
            reason,
            subject,
            jti,
            client,
            agent,
            skipped,
        )

    return AuthenticationError(detail)


def _refresh_via_predecessor(
    session: Session,
    request: Request,
    response: Response,
    token_hash: str,
    user_id: int,
    claims: dict,
) -> RefreshResponse:
    """Answer a refresh presented with the immediately-prior refresh token.

    ROTATION CONFIRMATION (bead upkp1). The predecessor stays acceptable
    until its successor is actually used, replacing the 10-second wall-clock
    grace window this path shipped with (bd-x67qe). A client whose rotated
    response never arrived, because the tab navigated or the request was
    aborted mid-flight, used to be locked out ten seconds later with no
    non-interactive way back.

    Why no ``successor_used`` flag is needed, and why the rule is "the
    successor was ACCEPTED" rather than "the successor was presented": the
    guarded UPDATE in :func:`refresh_tokens` writes the presented hash into
    ``prior_refresh_token_hash`` in the same statement that installs the
    successor's hash as current. The instant a successor is accepted, the
    predecessor's hash is overwritten out of the row and can never match
    again, so "valid until the successor is used" IS "valid until it is
    overwritten". A presentation that fails any check never reaches that
    UPDATE and therefore cannot confirm anything. The row is the sole
    authority; a separate flag would be a second source of truth that can
    disagree with the hashes, and it would cost a migration to say nothing
    new.

    Acceptance conditions, in the order enforced below. All five must hold;
    this list is the authority on how many there are, because a pre-merge
    review of an earlier draft found a normative "four conditions and nothing
    else" phrasing that omitted the last one while a required test demanded
    it:

    1. the row's ``prior_refresh_token_hash`` matches the presented token,
    2. the row belongs to the ``sub`` in the verified claims,
    3. the row is not revoked,
    4. the row's ``expires_at`` is still in the future,
    5. the user the row belongs to still exists and is still active.

    (5) is not redundant with the others. Deactivating an account has to take
    effect on this path too, or a stranded client would keep collecting
    access tokens for a disabled user until the session expired on its own,
    and nothing else here consults the user row for anything but the username
    on the minted token.

    Security invariants:
    - Only the LATEST predecessor is honored, one generation deep. A normal
      rotation overwrites ``prior_refresh_token_hash``, so predecessors never
      chain and exactly one superseded token is live per session.
    - NO second rotation and NO refresh cookie. The jar keeps the successor's
      refresh token. Minting a fresh one here would fork the chain: the
      server does not hold the plaintext successor, so it could only install
      a NEW one and strand whoever legitimately received the real successor.
    - ``expires_at`` is untouched, so this path never extends session
      lifetime. That asymmetry is the outer bound the removed window used to
      provide: a healthy client rotates and slides its session forward, while
      a client living on predecessor answers is guaranteed back through
      interactive login at the original expiry.
    - The presented JWT's own ``exp`` is enforced before any DB lookup.
    - Revoked sessions are excluded, so logout kills the predecessor at once.

    Raises:
        AuthenticationError: when the predecessor is not acceptable.
    """
    prior_session = session.query(UserSession).filter(
        UserSession.prior_refresh_token_hash == token_hash,
        UserSession.user_id == user_id,
        UserSession.is_revoked == False,
    ).first()

    now = datetime.utcnow()
    if not prior_session:
        raise _deny_refresh(
            request,
            _REFRESH_DENIAL_UNKNOWN_SESSION,
            "Session not found or revoked",
            claims=claims,
        )

    # ``<=``, not ``<``: the rule is that the session is live while
    # ``expires_at > now``, so an instant that lands exactly on the expiry is
    # already outside it. Matches the current-token check in
    # :func:`refresh_tokens`.
    if prior_session.expires_at <= now:
        raise _deny_refresh(
            request,
            "predecessor_session_expired",
            "Session expired",
            claims=claims,
        )

    user = session.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise _deny_refresh(
            request,
            "predecessor_user_inactive",
            "User not found or disabled",
            claims=claims,
        )

    access_token = create_access_token(
        user_id=user.id, username=user.username, auth_epoch=user.auth_epoch
    )
    _set_access_cookie(response, access_token, request)

    rotated_at = prior_session.rotated_at
    # last_used_at only. Never expires_at, never either hash, never
    # rotated_at (see invariants above).
    prior_session.last_used_at = now
    session.commit()

    # rotated_at no longer gates acceptance; it survives as forensic state so
    # an operator can see a client stuck on the predecessor for days.
    age = (
        "%ds" % int((now - rotated_at).total_seconds())
        if rotated_at is not None
        else "unknown"
    )
    logger.info(
        "[AUTH] Refresh answered from the predecessor token for user %s "
        "(session_id=%s, age_since_rotation=%s, successor not used yet)",
        user.username,
        prior_session.id,
        age,
    )
    return RefreshResponse(
        message="Token refreshed",
        access_token_expires_in=_access_token_lifetime_seconds(),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Refresh access token using refresh token.

    Sets new httpOnly cookies with fresh access and refresh tokens. A refresh
    presented with the immediately-prior token is answered idempotently until
    that token's successor is actually used (rotation confirmation, bead
    upkp1) - see :func:`_refresh_via_predecessor`.

    CONCURRENCY CONSTRAINT: this body must contain NO ``await``, and adding
    one is an escalation rather than a judgment call. Every Session in this
    application shares one sqlite connection (``poolclass=StaticPool``) and
    therefore one transaction, so a second request that interleaved between
    the read below and its commit could durably commit this request's
    half-finished rotation. What makes that impossible today is that the body
    runs straight through without yielding the event loop, which is what lets
    the compare-and-swap below be decided from ``rowcount`` alone.
    """
    _require_secure_session_transport(request)

    refresh_token = get_refresh_token_from_request(request)
    if not refresh_token:
        raise _deny_refresh(request, "no_credential", "No refresh token provided")

    try:
        # Decode and validate refresh token
        try:
            claims = decode_token(refresh_token)
        except TokenRevokedError:
            # LOAD-BEARING, NOT AN EDGE CASE (bead upkp1). Rotation adds the
            # presented jti to the in-process blacklist, so a predecessor's
            # jti is blacklisted the instant its successor is minted. Under
            # rotation confirmation the predecessor stays valid for the rest
            # of the session's life, so EVERY predecessor acceptance now
            # depends on this fallback. Re-validate signature and expiry only
            # and let the DB decide: the row, not the in-process set, is the
            # revocation authority. Narrowing or removing this reintroduces
            # the stranding bug wholesale.
            claims = decode_token(refresh_token, ignore_revocation=True)

        if claims.get("type") != "refresh":
            raise _deny_refresh(
                request, "wrong_credential_type", "Invalid token type", claims=claims
            )

        user_id = claims.get("sub")
        if user_id is None:
            raise _deny_refresh(
                request, "no_subject", "Invalid token payload", claims=claims
            )

        # Verify session exists and is valid
        token_hash = hash_token(refresh_token)
        user_session = session.query(UserSession).filter(
            UserSession.refresh_token_hash == token_hash,
            UserSession.is_revoked == False,
        ).first()

        if not user_session:
            # Not the current token. It may be the immediately-prior one,
            # whose successor has not been used yet (bead upkp1).
            return _refresh_via_predecessor(
                session, request, response, token_hash, int(user_id), claims
            )

        # ``<=``, not ``<``: a session is live while ``expires_at > now``, so
        # the expiry instant itself is outside the session. Same predicate on
        # the predecessor path in :func:`_refresh_via_predecessor`.
        if user_session.expires_at <= datetime.utcnow():
            raise _deny_refresh(
                request, "session_expired", "Session expired", claims=claims
            )

        # Get user
        user = session.query(User).filter(User.id == user_id).first()
        if not user or not user.is_active:
            raise _deny_refresh(
                request, "user_inactive", "User not found or disabled", claims=claims
            )

        # Rotate tokens. Pass the real username so the refreshed access
        # token keeps identifying the account rather than a ``user_<id>``
        # placeholder (bd-suuoh) — that claim is what main.py's
        # deprecated-admin-router warning logs as the acting operator.
        new_access_token, new_refresh_token = rotate_refresh_token(
            refresh_token, username=user.username, auth_epoch=user.auth_epoch
        )

        # Guarded rotation (bd-x67qe): the UPDATE only applies while the row
        # still holds the pre-rotation hash, so exactly ONE of two racing
        # requests can rotate. Writing the presented hash into
        # ``prior_refresh_token_hash`` is also what CONFIRMS a rotation for
        # bead upkp1: it hands the loser an idempotent answer, and it
        # atomically overwrites the previous predecessor out of the row, which
        # is what ends that token's life. Rotating twice therefore refuses the
        # oldest token, with no window and no flag involved.
        settings = get_auth_settings()
        now = datetime.utcnow()
        rotated = session.query(UserSession).filter(
            UserSession.id == user_session.id,
            UserSession.refresh_token_hash == token_hash,
            UserSession.is_revoked == False,
        ).update(
            {
                "refresh_token_hash": hash_token(new_refresh_token),
                "prior_refresh_token_hash": token_hash,
                "rotated_at": now,
                "last_used_at": now,
                "expires_at": now
                + timedelta(days=settings.jwt.refresh_token_expire_days),
            },
            synchronize_session=False,
        )
        session.commit()

        if not rotated:
            # Photo-finish: another request rotated this session between our
            # read and write. Fall through to the predecessor path instead of
            # clobbering the winner's rotation. The successor minted just
            # above is discarded on purpose: acceptance is decided by the
            # stored hash, so a token that never reached the row is not a
            # credential. Storing or returning it would install a second
            # current token.
            return _refresh_via_predecessor(
                session, request, response, token_hash, int(user_id), claims
            )

        # Clean up expired sessions for this user
        _cleanup_expired_sessions(session, int(user_id))

        # Set new cookies
        _set_auth_cookies(response, new_access_token, new_refresh_token, request)

        logger.info("[AUTH] Token refreshed for user: %s", user.username)
        return RefreshResponse(
            message="Token refreshed",
            access_token_expires_in=_access_token_lifetime_seconds(),
        )

    except TokenExpiredError:
        raise _deny_refresh(request, "jwt_expired", "Refresh token expired")
    except TokenRevokedError:
        raise _deny_refresh(request, "jwt_revoked", "Refresh token revoked")
    except InvalidTokenError as e:
        raise _deny_refresh(
            request, "jwt_invalid", f"Invalid refresh token: {str(e)}"
        )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Logout current user and clear session.

    Revokes the refresh token and clears cookies.
    Always returns success even if not logged in (idempotent).
    """
    # Try to revoke the session if we have a refresh token. Also match the
    # immediately-prior token's hash (bd-x67qe): a stale tab logging out with
    # the pre-rotation cookie must still kill the session. Revocation beats
    # predecessor acceptance, which is what bounds bead upkp1's longer-lived
    # predecessor: one row is the whole chain, so revoking it refuses the
    # current token and its predecessor together.
    refresh_token = get_refresh_token_from_request(request)
    if refresh_token:
        try:
            token_hash = hash_token(refresh_token)
            user_session = session.query(UserSession).filter(
                or_(
                    UserSession.refresh_token_hash == token_hash,
                    UserSession.prior_refresh_token_hash == token_hash,
                ),
            ).first()

            if user_session:
                user_session.is_revoked = True
                session.commit()
                logger.info("[AUTH] Session revoked for user_id: %s", user_session.user_id)
        except Exception as e:
            logger.warning("[AUTH] Error revoking session: %s", e)

    # Always clear cookies
    _clear_auth_cookies(response, request)

    return LogoutResponse(message="Logged out successfully")


# =============================================================================
# Password Management
# =============================================================================

@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    change_request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Change the current user's password.

    Requires the current password for verification.
    """
    # The transient MCP service principal cannot own/change an account password
    # — reject with a clean 403 rather than letting a transient-User mutation
    # surface as a 500 (bd-1wq7z.24 (c)).
    reject_mcp_service_principal_mutation(current_user)

    # Verify current password
    if not current_user.password_hash or not verify_password(change_request.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # Validate new password strength
    password_result = validate_password(change_request.new_password, current_user.username)
    if not password_result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=password_result.error,
        )

    # Update password
    current_user.password_hash = hash_password(change_request.new_password)
    current_user.updated_at = datetime.utcnow()
    session.commit()

    logger.info("[AUTH] Password changed for user: %s", current_user.username)

    return ChangePasswordResponse(message="Password changed successfully")


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit(RESET_ISSUE_RATE)
async def forgot_password(
    forgot_request: ForgotPasswordRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    """
    Request a password reset email.

    Always returns 200 for security (don't reveal if email exists).
    """
    # Find user by email
    user = session.query(User).filter(User.email == forgot_request.email).first()

    if user and user.is_active and user.auth_provider == "local":
        now = datetime.utcnow()
        session.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            or_(
                PasswordResetToken.used_at.is_not(None),
                PasswordResetToken.expires_at <= now,
            ),
        ).delete(synchronize_session=False)
        active_token = session.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        ).order_by(PasswordResetToken.created_at.desc()).first()

        # The response is deliberately identical to a fresh issuance. This
        # account-level cooldown bounds token hashing, database writes and
        # outbound email even when requests are distributed across clients.
        if (
            active_token
            and active_token.created_at > now - PASSWORD_RESET_ACCOUNT_COOLDOWN
        ):
            session.commit()
            return ForgotPasswordResponse()

        if active_token:
            superseded = session.query(PasswordResetToken).filter(
                PasswordResetToken.id == active_token.id,
                PasswordResetToken.used_at.is_(None),
            ).update(
                {PasswordResetToken.used_at: now},
                synchronize_session=False,
            )
            session.commit()
            if superseded != 1:
                return ForgotPasswordResponse()

        # Generate reset token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)

        # Create reset token record (expires in 1 hour)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(reset_token)
        try:
            session.commit()
        except IntegrityError:
            # The partial unique index is the cross-worker account limiter.
            # A concurrent request won issuance; preserve the generic response
            # and do not send a second email.
            session.rollback()
            return ForgotPasswordResponse()

        session.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != reset_token.id,
        ).delete(synchronize_session=False)
        session.commit()

        # Build the origin the emailed reset link points at.
        #
        # Bead ...-qsqfv (P1). When the operator has configured a public base
        # URL it is used VERBATIM and the request is not consulted at all: the
        # link goes into an email ECM sends to a third party, so nothing the
        # caller supplies may influence where that link points. X-Forwarded-Host
        # is caller-supplied, X-Forwarded-Proto is caller-supplied, and the old
        # request.url.netloc fallback is just the Host header, so all three let
        # an unauthenticated caller who knows a victim's email address have a
        # genuine ECM email deliver a live reset token to a host they control.
        #
        # The unconfigured fallback below is that same unsafe construction,
        # kept deliberately (PO decision 2026-08-15) so upgrading does not
        # break the reset email on installs that never set the value.
        # get_public_base_url() warns once per process while it is unset.
        base_url = get_public_base_url()
        if not base_url:
            forwarded_proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
            forwarded_host = request.headers.get("X-Forwarded-Host", request.url.netloc)
            base_url = f"{forwarded_proto}://{forwarded_host}"

        # Send the password reset email
        email_sent = send_password_reset_email(user.email, raw_token, base_url)
        if email_sent:
            # NEVER log user.email here. This is the same log operators paste
            # into GitHub issues, and a subscriber's address is PII that the
            # user id identifies just as well for every operational purpose.
            # One line per event, in the ``user_id=`` shape every other [AUTH]
            # line in this file uses: bead cb1e1 moved the failure branch
            # below, bead 5u5h9 moved this one and removed the duplicate the
            # send helper emitted for the same event.
            logger.info("[AUTH] Password reset email sent for user_id=%s", user.id)
        else:
            # NEVER log raw_token on this branch. It is a live, working
            # password-reset credential, and an email outage is an ordinary
            # operational failure, so anyone reading the log could reset this
            # user's password. Log the user id only. An operator whose SMTP is
            # down still has two recovery paths that do not depend on this
            # line: PATCH /api/admin/users/{user_id} with a password (admin
            # only) and the reset_password.py CLI. See bead cb1e1.
            logger.warning("[AUTH] Password reset email failed for user_id=%s", user.id)

    # Always return success for security
    return ForgotPasswordResponse()


@router.post("/reset-password", response_model=ResetPasswordResponse)
@limiter.limit(RESET_VALIDATE_RATE)
async def reset_password(
    reset_request: ResetPasswordRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    """
    Reset password using a reset token.

    Token must be valid and not expired (1 hour expiry).
    """
    now = datetime.utcnow()
    token_hash = hash_token(reset_request.token)
    valid_token = session.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).first()

    if not valid_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Persist the per-account/token attempt budget independently of password
    # validation. Otherwise every rejected weak password would roll the budget
    # back with its 422 response and the limit would never become effective.
    attempt_claimed = session.query(PasswordResetToken).filter(
        PasswordResetToken.id == valid_token.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.attempt_count < PASSWORD_RESET_MAX_ATTEMPTS,
    ).update(
        {PasswordResetToken.attempt_count: PasswordResetToken.attempt_count + 1},
        synchronize_session=False,
    )
    session.commit()
    if attempt_claimed != 1:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many password reset attempts. Request a new reset link.",
        )

    # Get user
    user = session.query(User).filter(User.id == valid_token.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Validate new password strength
    password_result = validate_password(reset_request.new_password, user.username)
    if not password_result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=password_result.error,
        )

    new_password_hash = hash_password(reset_request.new_password)
    consumed_at = datetime.utcnow()
    if not _consume_reset_token(session, valid_token.id, consumed_at):
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Token consumption, password mutation and session revocation commit as a
    # single transaction. A racing consumer that loses the conditional UPDATE
    # changes none of them.
    user.password_hash = new_password_hash
    user.updated_at = consumed_at
    user.auth_epoch += 1
    session.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.is_revoked.is_(False),
    ).update({UserSession.is_revoked: True}, synchronize_session=False)

    session.commit()

    logger.info("[AUTH] Password reset for user: %s", user.username)

    return ResetPasswordResponse(message="Password reset successfully")


# =============================================================================
# Auth Providers Endpoint
# =============================================================================

class AuthProviderInfo(BaseModel):
    """Information about an available auth provider."""
    type: str
    name: str
    enabled: bool


class AuthProvidersResponse(BaseModel):
    """List of available auth providers."""
    providers: list[AuthProviderInfo]


@router.get("/providers", response_model=AuthProvidersResponse)
async def get_auth_providers():
    """
    Get list of available authentication providers.

    Returns enabled providers and their configuration.
    """
    settings = get_auth_settings()
    providers = []

    if settings.local.enabled:
        providers.append(AuthProviderInfo(
            type="local",
            name="Local",
            enabled=True,
        ))

    if settings.dispatcharr.enabled:
        providers.append(AuthProviderInfo(
            type="dispatcharr",
            name="Dispatcharr",
            enabled=True,
        ))

    if settings.saml.enabled:
        providers.append(AuthProviderInfo(
            type="saml",
            name=settings.saml.provider_name or "SAML",
            enabled=True,
        ))

    if settings.ldap.enabled:
        providers.append(AuthProviderInfo(
            type="ldap",
            name="LDAP",
            enabled=True,
        ))

    return AuthProvidersResponse(providers=providers)


# =============================================================================
# Dispatcharr Authentication
# =============================================================================

class DispatcharrLoginRequest(BaseModel):
    """Dispatcharr login request body."""
    username: str
    password: str


@router.post("/dispatcharr/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def dispatcharr_login(
    login_request: DispatcharrLoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Authenticate user via Dispatcharr.

    Validates credentials against Dispatcharr and creates/updates local user.
    Sets httpOnly cookies with access and refresh tokens.
    """
    _require_secure_session_transport(request)

    from auth.providers.dispatcharr import (
        DispatcharrClient,
        DispatcharrAuthenticationError,
        DispatcharrConnectionError,
        DispatcharrNetworkPolicyError,
        DispatcharrRateLimitError,
    )

    # Check if Dispatcharr auth is enabled
    settings = get_auth_settings()
    if not settings.dispatcharr.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispatcharr authentication is not enabled",
        )

    # Authenticate with Dispatcharr
    try:
        async with DispatcharrClient() as client:
            auth_result = await client.authenticate(
                login_request.username,
                login_request.password,
            )
    except DispatcharrRateLimitError as e:
        client_ip = _client_address(request)
        logger.warning("[AUTH] Dispatcharr rate-limited login for user: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"Retry-After": "60"},
        )
    except DispatcharrNetworkPolicyError as e:
        client_ip = _client_address(request)
        logger.error("[AUTH] Dispatcharr network policy rejected login for user: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except DispatcharrAuthenticationError as e:
        client_ip = _client_address(request)
        logger.warning("[AUTH] Dispatcharr auth failed for user: %s from %s", login_request.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except TimeoutError:
        logger.error("[AUTH] Dispatcharr connection timeout")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dispatcharr connection timeout",
        )
    except DispatcharrConnectionError as e:
        logger.error("[AUTH] Dispatcharr connection error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot connect to Dispatcharr",
        )
    except Exception as e:
        logger.exception("[AUTH] Unexpected Dispatcharr auth error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication error",
        )

    from models import UserIdentity

    # First, try to find user via identity table
    identity = session.query(UserIdentity).filter(
        UserIdentity.provider == "dispatcharr",
        UserIdentity.external_id == auth_result.user_id,
    ).first()

    user = None
    if identity:
        user = identity.user
        # Update identity last_used_at
        identity.last_used_at = datetime.utcnow()
        # Update user info from Dispatcharr
        user.email = auth_result.email or user.email
        user.display_name = auth_result.display_name or user.display_name
        logger.info("[AUTH] Dispatcharr user found via identity: %s", user.username)
    else:
        # Fallback to direct user lookup for backwards compatibility
        user = session.query(User).filter(
            User.auth_provider == "dispatcharr",
            User.external_id == auth_result.user_id,
        ).first()

        if user is not None:
            # Update existing user info from Dispatcharr
            user.email = auth_result.email or user.email
            user.display_name = auth_result.display_name or user.display_name
            logger.info("[AUTH] Updated user info from Dispatcharr: %s", user.username)
        else:
            # Create new user from Dispatcharr
            # Check if username exists with different provider
            existing = session.query(User).filter(User.username == auth_result.username).first()
            if existing:
                # Username taken by local user - create with modified username
                username = f"disp_{auth_result.username}"
                logger.info("[AUTH] Username '%s' taken, using '%s'", auth_result.username, username)
            else:
                username = auth_result.username

            user = User(
                username=username,
                email=auth_result.email,
                display_name=auth_result.display_name,
                auth_provider="dispatcharr",
                external_id=auth_result.user_id,
                is_admin=False,  # Dispatcharr users are not admins by default
                is_active=True,
            )
            session.add(user)
            session.flush()  # Flush to get the user ID

            # Create identity for the new user
            new_identity = UserIdentity(
                user_id=user.id,
                provider="dispatcharr",
                external_id=auth_result.user_id,
                identifier=auth_result.username,
            )
            session.add(new_identity)
            logger.info("[AUTH] Created new user from Dispatcharr: %s (id=%s)", user.username, user.id)

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    # Create tokens
    access_token = create_access_token(
        user_id=user.id, username=user.username, auth_epoch=user.auth_epoch
    )
    refresh_token = create_refresh_token(user_id=user.id)

    # Create session record
    user_session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", "")[:500],
        expires_at=datetime.utcnow() + timedelta(days=settings.jwt.refresh_token_expire_days),
    )
    session.add(user_session)

    # Update last login
    user.last_login_at = datetime.utcnow()
    session.commit()

    # Refresh user to get ID for new users
    session.refresh(user)

    # Set cookies
    _set_auth_cookies(response, access_token, refresh_token, request)

    logger.info("[AUTH] Dispatcharr user logged in: %s", user.username)

    return LoginResponse(
        user=UserResponse.model_validate(user),
        message="Login successful",
        access_token_expires_in=_access_token_lifetime_seconds(),
    )


# =============================================================================
# Admin: Auth Settings Management
# =============================================================================

def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency that requires admin role."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


class AuthSettingsPublicResponse(BaseModel):
    """Auth settings response (sensitive data excluded)."""
    require_auth: bool
    primary_auth_mode: str
    # Local auth settings
    local_enabled: bool
    local_min_password_length: int
    # Dispatcharr settings
    dispatcharr_enabled: bool
    dispatcharr_auto_create_users: bool


class AuthSettingsUpdateRequest(BaseModel):
    """Auth settings update request."""
    require_auth: Optional[bool] = None
    primary_auth_mode: Optional[str] = None
    # Local auth settings
    local_enabled: Optional[bool] = None
    local_min_password_length: Optional[int] = None
    # Dispatcharr settings
    dispatcharr_enabled: Optional[bool] = None
    dispatcharr_auto_create_users: Optional[bool] = None


@router.get("/admin/settings", response_model=AuthSettingsPublicResponse)
async def get_admin_auth_settings(
    admin_user: User = Depends(require_admin),
):
    """
    Get authentication settings (admin only).

    Returns settings with sensitive data (secrets) excluded.
    """
    settings = get_auth_settings()
    return AuthSettingsPublicResponse(
        require_auth=settings.require_auth,
        primary_auth_mode=settings.primary_auth_mode,
        local_enabled=settings.local.enabled,
        local_min_password_length=settings.local.min_password_length,
        dispatcharr_enabled=settings.dispatcharr.enabled,
        dispatcharr_auto_create_users=settings.dispatcharr.auto_create_users,
    )


class AuthSettingsUpdateResponse(BaseModel):
    """Auth settings update response."""
    message: str = "Settings updated"


@router.put("/admin/settings", response_model=AuthSettingsUpdateResponse)
async def update_admin_auth_settings(
    update_request: AuthSettingsUpdateRequest,
    admin_user: User = Depends(require_admin),
):
    """
    Update authentication settings (admin only).

    Only provided fields are updated. Secrets are stored securely.
    """
    settings = get_auth_settings()

    # Update top-level settings
    if update_request.require_auth is not None:
        settings.require_auth = update_request.require_auth
    if update_request.primary_auth_mode is not None:
        settings.primary_auth_mode = update_request.primary_auth_mode

    # Update local auth settings
    if update_request.local_enabled is not None:
        settings.local.enabled = update_request.local_enabled
    if update_request.local_min_password_length is not None:
        settings.local.min_password_length = update_request.local_min_password_length

    # Update Dispatcharr settings
    if update_request.dispatcharr_enabled is not None:
        settings.dispatcharr.enabled = update_request.dispatcharr_enabled
    if update_request.dispatcharr_auto_create_users is not None:
        settings.dispatcharr.auto_create_users = update_request.dispatcharr_auto_create_users

    save_auth_settings(settings)
    logger.info("[AUTH] Auth settings updated by admin: %s", admin_user.username)

    return AuthSettingsUpdateResponse(message="Settings updated")


# =============================================================================
# Admin: User Management
# =============================================================================

class UserListResponse(BaseModel):
    """List of users response."""
    users: list[UserResponse]
    total: int


class UserDetailResponse(BaseModel):
    """Single user detail response."""
    user: UserResponse
    session_count: int
    last_login_at: Optional[datetime] = None
    created_at: datetime


class UserUpdateRequest(BaseModel):
    """User update request (admin)."""
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    display_name: Optional[str] = None
    email: Optional[str] = None


class UserUpdateResponse(BaseModel):
    """User update response."""
    user: UserResponse
    message: str = "User updated"


class UserDeleteResponse(BaseModel):
    """User delete response."""
    message: str = "User deleted"


@router.get("/admin/users", response_model=UserListResponse)
async def list_users(
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """
    List all users (admin only).
    """
    users = session.query(User).order_by(User.created_at.desc()).all()
    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=len(users),
    )


@router.get("/admin/users/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """
    Get single user details (admin only).
    """
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    session_count = session.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_revoked == False,
    ).count()

    return UserDetailResponse(
        user=UserResponse.model_validate(user),
        session_count=session_count,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.put("/admin/users/{user_id}", response_model=UserUpdateResponse)
async def update_user(
    user_id: int,
    update_request: UserUpdateRequest,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """
    Update a user (admin only).

    Can update admin status, active status, display name, and email.
    """
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent admin from removing their own admin status
    if update_request.is_admin is False and user.id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin status",
        )

    # Prevent admin from deactivating themselves
    if update_request.is_active is False and user.id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    # Update fields
    if update_request.is_admin is not None:
        user.is_admin = update_request.is_admin
    if update_request.is_active is not None:
        user.is_active = update_request.is_active
    if update_request.display_name is not None:
        user.display_name = update_request.display_name
    if update_request.email is not None:
        user.email = update_request.email

    user.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(user)

    logger.info("[AUTH] User %s updated by admin %s", user.username, admin_user.username)

    return UserUpdateResponse(
        user=UserResponse.model_validate(user),
        message="User updated",
    )


@router.delete("/admin/users/{user_id}", response_model=UserDeleteResponse)
async def delete_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """
    Delete a user (admin only).

    Also revokes all user sessions.
    """
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent admin from deleting themselves
    if user.id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    username = user.username

    # Revoke all sessions
    session.query(UserSession).filter(UserSession.user_id == user_id).delete()

    # Delete password reset tokens
    session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).delete()

    # Delete user
    session.delete(user)
    session.commit()

    logger.info("[AUTH] User %s deleted by admin %s", username, admin_user.username)

    return UserDeleteResponse(message=f"User '{username}' deleted")


# =============================================================================
# Linked Identities (Account Linking)
# =============================================================================

class UserIdentityResponse(BaseModel):
    """User identity data for API responses."""
    id: int
    user_id: int
    provider: str
    external_id: Optional[str] = None
    identifier: str
    linked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LinkedIdentitiesResponse(BaseModel):
    """List of linked identities response."""
    identities: list[UserIdentityResponse]


class LinkIdentityRequest(BaseModel):
    """Request to link a new identity."""
    provider: str
    username: str
    password: str


class LinkIdentityResponse(BaseModel):
    """Link identity response."""
    identity: UserIdentityResponse
    message: str = "Identity linked successfully"


class UnlinkIdentityResponse(BaseModel):
    """Unlink identity response."""
    message: str = "Identity unlinked successfully"


# Helper functions for identity management
def get_user_identities(db: Session, user_id: int) -> list:
    """Get all identities linked to a user."""
    from models import UserIdentity
    return db.query(UserIdentity).filter(UserIdentity.user_id == user_id).all()


def find_user_by_identity(db: Session, provider: str, external_id: str) -> Optional[User]:
    """Find a user by their identity (provider + external_id)."""
    from models import UserIdentity
    identity = db.query(UserIdentity).filter(
        UserIdentity.provider == provider,
        UserIdentity.external_id == external_id,
    ).first()
    return identity.user if identity else None


def find_user_by_identifier(db: Session, provider: str, identifier: str) -> Optional[User]:
    """Find a user by their identifier (provider + username/email)."""
    from models import UserIdentity
    identity = db.query(UserIdentity).filter(
        UserIdentity.provider == provider,
        UserIdentity.identifier == identifier,
    ).first()
    return identity.user if identity else None


def add_user_identity(
    db: Session,
    user_id: int,
    provider: str,
    identifier: str,
    external_id: Optional[str] = None,
) -> "UserIdentity":
    """Add a new identity to a user account."""
    from models import UserIdentity

    identity = UserIdentity(
        user_id=user_id,
        provider=provider,
        external_id=external_id,
        identifier=identifier,
    )
    db.add(identity)
    db.flush()
    return identity


def update_identity_last_used(db: Session, identity_id: int) -> None:
    """Update the last_used_at timestamp for an identity."""
    from models import UserIdentity
    identity = db.query(UserIdentity).filter(UserIdentity.id == identity_id).first()
    if identity:
        identity.last_used_at = datetime.utcnow()


def remove_user_identity(db: Session, identity_id: int, user_id: int) -> bool:
    """
    Remove an identity from a user account.
    Returns False if this is the user's only identity (safety check).
    """
    from models import UserIdentity

    # Check how many identities the user has
    identity_count = db.query(UserIdentity).filter(
        UserIdentity.user_id == user_id
    ).count()

    if identity_count <= 1:
        return False  # Can't remove the last identity

    # Remove the identity
    result = db.query(UserIdentity).filter(
        UserIdentity.id == identity_id,
        UserIdentity.user_id == user_id,
    ).delete()

    return result > 0


@router.get("/identities", response_model=LinkedIdentitiesResponse)
async def list_linked_identities(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Get all identities linked to the current user's account.
    """
    identities = get_user_identities(session, current_user.id)
    return LinkedIdentitiesResponse(
        identities=[UserIdentityResponse.model_validate(i) for i in identities]
    )


@router.post("/identities/link", response_model=LinkIdentityResponse)
async def link_identity(
    link_request: LinkIdentityRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Link a new identity to the current user's account.

    Requires valid credentials for the target provider.
    """
    from models import UserIdentity

    provider = link_request.provider.lower()

    # Check if this provider is already linked
    existing = session.query(UserIdentity).filter(
        UserIdentity.user_id == current_user.id,
        UserIdentity.provider == provider,
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a {provider} identity linked",
        )

    # Authenticate with the provider to verify credentials
    if provider == "local":
        # For local, verify the password matches a local identity
        if not link_request.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is required for local linking",
            )

        # Check if this username is already used
        existing_identity = session.query(UserIdentity).filter(
            UserIdentity.provider == "local",
            UserIdentity.identifier == link_request.username,
        ).first()

        if existing_identity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This local username is already linked to another account",
            )

        # Create password hash for this identity
        password_hash = hash_password(link_request.password)
        current_user.password_hash = password_hash  # Store on user for now

        identity = add_user_identity(
            session,
            current_user.id,
            "local",
            link_request.username,
            external_id=None,
        )

    elif provider == "dispatcharr":
        # Authenticate with Dispatcharr
        from auth.providers.dispatcharr import (
            DispatcharrClient,
            DispatcharrAuthenticationError,
            DispatcharrConnectionError,
            DispatcharrNetworkPolicyError,
            DispatcharrRateLimitError,
        )

        settings = get_auth_settings()
        if not settings.dispatcharr.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispatcharr authentication is not enabled",
            )

        try:
            async with DispatcharrClient() as client:
                auth_result = await client.authenticate(
                    link_request.username,
                    link_request.password,
                )
        except DispatcharrRateLimitError as e:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e),
                headers={"Retry-After": "60"},
            )
        except DispatcharrNetworkPolicyError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e),
            )
        except DispatcharrAuthenticationError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Dispatcharr authentication failed: {e}",
            )
        except (DispatcharrConnectionError, TimeoutError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cannot connect to Dispatcharr",
            )

        # Check if this Dispatcharr identity is already linked to another account
        existing_identity = session.query(UserIdentity).filter(
            UserIdentity.provider == "dispatcharr",
            UserIdentity.external_id == auth_result.user_id,
        ).first()

        if existing_identity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Dispatcharr account is already linked to another user",
            )

        identity = add_user_identity(
            session,
            current_user.id,
            "dispatcharr",
            auth_result.username,
            external_id=auth_result.user_id,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Linking not supported for provider: {provider}",
        )

    session.commit()
    session.refresh(identity)

    logger.info("[AUTH] User %s linked %s identity: %s", current_user.username, provider, identity.identifier)

    return LinkIdentityResponse(
        identity=UserIdentityResponse.model_validate(identity),
        message="Identity linked successfully",
    )


@router.delete("/identities/{identity_id}", response_model=UnlinkIdentityResponse)
async def unlink_identity(
    identity_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Unlink an identity from the current user's account.

    Cannot unlink the last remaining identity (would lock out user).
    """
    from models import UserIdentity

    # Get the identity
    identity = session.query(UserIdentity).filter(
        UserIdentity.id == identity_id,
        UserIdentity.user_id == current_user.id,
    ).first()

    if not identity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Identity not found",
        )

    # Check if this is the last identity
    identity_count = session.query(UserIdentity).filter(
        UserIdentity.user_id == current_user.id
    ).count()

    if identity_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unlink your last identity - you would be locked out",
        )

    provider = identity.provider
    identifier = identity.identifier

    # Remove the identity
    session.delete(identity)
    session.commit()

    logger.info("[AUTH] User %s unlinked %s identity: %s", current_user.username, provider, identifier)

    return UnlinkIdentityResponse(message="Identity unlinked successfully")
