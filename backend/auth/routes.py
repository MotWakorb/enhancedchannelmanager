"""
Authentication API endpoints.

Provides login, logout, token refresh, and password management.
"""
import logging
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from config import get_settings
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
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from .settings import get_auth_settings, save_auth_settings, AuthSettings
from .dependencies import (
    AuthenticationError,
    get_current_user,
    get_token_from_request,
    get_refresh_token_from_request,
)


logger = logging.getLogger(__name__)


def send_password_reset_email(to_email: str, reset_token: str, base_url: str) -> bool:
    """
    Send a password reset email using the shared SMTP settings.

    Args:
        to_email: Recipient email address.
        reset_token: The raw reset token to include in the link.
        base_url: The base URL of the application (e.g., http://localhost:6100).

    Returns:
        True if email was sent successfully, False otherwise.
    """
    settings = get_settings()

    if not settings.is_smtp_configured():
        logger.warning("Password reset email not sent: SMTP not configured")
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
            logger.info(f"Password reset email sent to: {to_email}")
            return True

        finally:
            server.quit()

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending password reset email: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}")
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


class MeResponse(BaseModel):
    """Current user response body."""
    user: UserResponse


class RefreshResponse(BaseModel):
    """Token refresh response body."""
    message: str = "Token refreshed"


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


def _set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    secure: bool = False,  # Set to True in production with HTTPS
) -> None:
    """
    Set authentication cookies on the response.

    Args:
        response: FastAPI response object.
        access_token: JWT access token.
        refresh_token: JWT refresh token.
        secure: Whether to set Secure flag (requires HTTPS).
    """
    settings = get_auth_settings()

    # Access token - short lived, httpOnly for security
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.jwt.access_token_expire_minutes * 60,
        path="/",
    )

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


def _clear_auth_cookies(response: Response) -> None:
    """Clear authentication cookies from the response."""
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/api/auth")


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

    session.commit()
    session.refresh(user)

    logger.info(f"Initial setup completed. Admin user created: {user.username}")

    return SetupResponse(
        user=UserResponse.model_validate(user),
        message="Setup complete",
    )


@router.post("/login", response_model=LoginResponse)
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

    if user is None:
        logger.warning(f"Login attempt for nonexistent user: {login_request.username}")
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
        logger.warning(f"Non-local user attempted local login: {login_request.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please use your configured authentication provider to log in",
        )

    # Verify password
    if not user.password_hash or not verify_password(login_request.password, user.password_hash):
        logger.warning(f"Failed login attempt for user: {login_request.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Update identity last_used_at if we found via identity
    if identity:
        identity.last_used_at = datetime.utcnow()

    # Check if user is active
    if not user.is_active:
        logger.warning(f"Login attempt for disabled user: {login_request.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    # Create tokens
    access_token = create_access_token(user_id=user.id, username=user.username)
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

    # Set cookies
    _set_auth_cookies(response, access_token, refresh_token)

    logger.info(f"User logged in: {user.username}")

    return LoginResponse(
        user=UserResponse.model_validate(user),
        message="Login successful",
    )


@router.get("/me", response_model=MeResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user information.

    Requires valid access token.
    """
    return MeResponse(user=UserResponse.model_validate(current_user))


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

    logger.info(f"User {current_user.username} updated their profile")

    return UpdateProfileResponse(
        user=UserResponse.model_validate(current_user),
        message="Profile updated",
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Refresh access token using refresh token.

    Sets new httpOnly cookies with fresh access and refresh tokens.
    """
    refresh_token = get_refresh_token_from_request(request)
    if not refresh_token:
        raise AuthenticationError("No refresh token provided")

    try:
        # Decode and validate refresh token
        claims = decode_token(refresh_token)

        if claims.get("type") != "refresh":
            raise AuthenticationError("Invalid token type")

        user_id = claims.get("sub")
        if user_id is None:
            raise AuthenticationError("Invalid token payload")

        # Verify session exists and is valid
        token_hash = hash_token(refresh_token)
        user_session = session.query(UserSession).filter(
            UserSession.refresh_token_hash == token_hash,
            UserSession.is_revoked == False,
        ).first()

        if not user_session:
            raise AuthenticationError("Session not found or revoked")

        if user_session.expires_at < datetime.utcnow():
            raise AuthenticationError("Session expired")

        # Get user
        user = session.query(User).filter(User.id == user_id).first()
        if not user or not user.is_active:
            raise AuthenticationError("User not found or disabled")

        # Rotate tokens
        new_access_token, new_refresh_token = rotate_refresh_token(refresh_token)

        # Update session with new refresh token hash
        user_session.refresh_token_hash = hash_token(new_refresh_token)
        user_session.last_used_at = datetime.utcnow()
        settings = get_auth_settings()
        user_session.expires_at = datetime.utcnow() + timedelta(days=settings.jwt.refresh_token_expire_days)
        session.commit()

        # Set new cookies
        _set_auth_cookies(response, new_access_token, new_refresh_token)

        logger.info(f"Token refreshed for user: {user.username}")
        return RefreshResponse(message="Token refreshed")

    except TokenExpiredError:
        raise AuthenticationError("Refresh token expired")
    except TokenRevokedError:
        raise AuthenticationError("Refresh token revoked")
    except InvalidTokenError as e:
        raise AuthenticationError(f"Invalid refresh token: {str(e)}")


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
    # Try to revoke the session if we have a refresh token
    refresh_token = get_refresh_token_from_request(request)
    if refresh_token:
        try:
            token_hash = hash_token(refresh_token)
            user_session = session.query(UserSession).filter(
                UserSession.refresh_token_hash == token_hash,
            ).first()

            if user_session:
                user_session.is_revoked = True
                session.commit()
                logger.info(f"Session revoked for user_id: {user_session.user_id}")
        except Exception as e:
            logger.warning(f"Error revoking session: {e}")

    # Always clear cookies
    _clear_auth_cookies(response)

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

    logger.info(f"Password changed for user: {current_user.username}")

    return ChangePasswordResponse(message="Password changed successfully")


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
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
        # Generate reset token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_password(raw_token)  # Use bcrypt for token hash

        # Create reset token record (expires in 1 hour)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(reset_token)
        session.commit()

        # Build base URL from request
        # Use X-Forwarded headers if behind a proxy, otherwise use request URL
        forwarded_proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
        forwarded_host = request.headers.get("X-Forwarded-Host", request.url.netloc)
        base_url = f"{forwarded_proto}://{forwarded_host}"

        # Send the password reset email
        email_sent = send_password_reset_email(user.email, raw_token, base_url)
        if email_sent:
            logger.info(f"Password reset email sent to: {user.email}")
        else:
            # Still log the token for debugging if email fails
            logger.warning(f"Password reset email failed for {user.email}, token: {raw_token}")

    # Always return success for security
    return ForgotPasswordResponse()


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    reset_request: ResetPasswordRequest,
    session: Session = Depends(get_session),
):
    """
    Reset password using a reset token.

    Token must be valid and not expired (1 hour expiry).
    """
    # Find valid reset token
    # We need to check all tokens since we hash them
    reset_tokens = session.query(PasswordResetToken).filter(
        PasswordResetToken.used_at.is_(None),
    ).all()

    valid_token = None
    for token_record in reset_tokens:
        if verify_password(reset_request.token, token_record.token_hash):
            valid_token = token_record
            break

    if not valid_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check if token is expired
    if valid_token.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired",
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

    # Update password
    user.password_hash = hash_password(reset_request.new_password)
    user.updated_at = datetime.utcnow()

    # Mark token as used
    valid_token.used_at = datetime.utcnow()

    session.commit()

    logger.info(f"Password reset for user: {user.username}")

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
    from auth.providers.dispatcharr import (
        DispatcharrClient,
        DispatcharrAuthenticationError,
        DispatcharrConnectionError,
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
    except DispatcharrAuthenticationError as e:
        logger.warning(f"Dispatcharr auth failed for user: {login_request.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except TimeoutError:
        logger.error("Dispatcharr connection timeout")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dispatcharr connection timeout",
        )
    except DispatcharrConnectionError as e:
        logger.error(f"Dispatcharr connection error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot connect to Dispatcharr",
        )
    except Exception as e:
        logger.exception(f"Unexpected Dispatcharr auth error: {e}")
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
        logger.info(f"Dispatcharr user found via identity: {user.username}")
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
            logger.info(f"Updated user info from Dispatcharr: {user.username}")
        else:
            # Create new user from Dispatcharr
            # Check if username exists with different provider
            existing = session.query(User).filter(User.username == auth_result.username).first()
            if existing:
                # Username taken by local user - create with modified username
                username = f"disp_{auth_result.username}"
                logger.info(f"Username '{auth_result.username}' taken, using '{username}'")
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
            logger.info(f"Created new user from Dispatcharr: {user.username} (id={user.id})")

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    # Create tokens
    access_token = create_access_token(user_id=user.id, username=user.username)
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
    _set_auth_cookies(response, access_token, refresh_token)

    logger.info(f"Dispatcharr user logged in: {user.username}")

    return LoginResponse(
        user=UserResponse.model_validate(user),
        message="Login successful",
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
    logger.info(f"Auth settings updated by admin: {admin_user.username}")

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

    logger.info(f"User {user.username} updated by admin {admin_user.username}")

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

    logger.info(f"User {username} deleted by admin {admin_user.username}")

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

    logger.info(f"User {current_user.username} linked {provider} identity: {identity.identifier}")

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

    logger.info(f"User {current_user.username} unlinked {provider} identity: {identifier}")

    return UnlinkIdentityResponse(message="Identity unlinked successfully")

