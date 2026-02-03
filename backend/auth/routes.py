"""
Authentication API endpoints.

Provides login, logout, token refresh, user registration, and password management.
"""
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

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


# User Registration Models
class RegisterRequest(BaseModel):
    """User registration request body."""
    username: str
    email: EmailStr
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 100:
            raise ValueError("Username must be at most 100 characters")
        if not v.isalnum() and not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("Username can only contain letters, numbers, underscores, and hyphens")
        return v


class RegisterResponse(BaseModel):
    """User registration response body."""
    user: UserResponse
    message: str = "Registration successful"


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
async def get_auth_status():
    """
    Get authentication status and configuration.

    Returns information about whether auth is enabled, setup complete,
    and which providers are available. This endpoint is always public.
    """
    settings = get_auth_settings()
    return AuthStatusResponse(
        setup_complete=settings.setup_complete,
        require_auth=settings.require_auth,
        enabled_providers=settings.get_enabled_providers(),
        primary_auth_mode=settings.primary_auth_mode,
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
# User Registration
# =============================================================================

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    register_request: RegisterRequest,
    session: Session = Depends(get_session),
):
    """
    Register a new user account.

    Creates a new local user with the provided username, email, and password.
    Password must meet strength requirements.
    """
    # Check if username already exists
    existing_user = session.query(User).filter(User.username == register_request.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    # Check if email already exists
    existing_email = session.query(User).filter(User.email == register_request.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Validate password strength
    password_result = validate_password(register_request.password, register_request.username)
    if not password_result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=password_result.error,
        )

    from models import UserIdentity

    # Create user
    user = User(
        username=register_request.username,
        email=register_request.email,
        password_hash=hash_password(register_request.password),
        auth_provider="local",
        is_admin=False,  # New users are not admin by default
        is_active=True,
    )
    session.add(user)
    session.flush()  # Get user ID

    # Create local identity for the user
    identity = UserIdentity(
        user_id=user.id,
        provider="local",
        identifier=register_request.username,
        external_id=None,
    )
    session.add(identity)

    session.commit()
    session.refresh(user)

    logger.info(f"New user registered: {user.username}")

    return RegisterResponse(
        user=UserResponse.model_validate(user),
        message="Registration successful",
    )


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

        # TODO: Send email with reset link containing raw_token
        # For now, log it (remove in production)
        logger.info(f"Password reset token generated for user: {user.email} (token: {raw_token})")

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

    if settings.oidc.enabled:
        providers.append(AuthProviderInfo(
            type="oidc",
            name=settings.oidc.provider_name or "OpenID Connect",
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
    local_allow_registration: bool
    local_min_password_length: int
    # Dispatcharr settings
    dispatcharr_enabled: bool
    dispatcharr_auto_create_users: bool
    # OIDC settings (no secrets)
    oidc_enabled: bool
    oidc_provider_name: str
    oidc_discovery_url: str
    oidc_auto_create_users: bool
    # SAML settings (no secrets)
    saml_enabled: bool
    saml_provider_name: str
    saml_idp_metadata_url: str
    saml_auto_create_users: bool
    # LDAP settings (no secrets)
    ldap_enabled: bool
    ldap_server_url: str
    ldap_use_ssl: bool
    ldap_use_tls: bool
    ldap_user_search_base: str
    ldap_auto_create_users: bool


class AuthSettingsUpdateRequest(BaseModel):
    """Auth settings update request."""
    require_auth: Optional[bool] = None
    primary_auth_mode: Optional[str] = None
    # Local auth settings
    local_enabled: Optional[bool] = None
    local_allow_registration: Optional[bool] = None
    local_min_password_length: Optional[int] = None
    # Dispatcharr settings
    dispatcharr_enabled: Optional[bool] = None
    dispatcharr_auto_create_users: Optional[bool] = None
    # OIDC settings
    oidc_enabled: Optional[bool] = None
    oidc_provider_name: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_discovery_url: Optional[str] = None
    oidc_auto_create_users: Optional[bool] = None
    # SAML settings
    saml_enabled: Optional[bool] = None
    saml_provider_name: Optional[str] = None
    saml_idp_metadata_url: Optional[str] = None
    saml_sp_entity_id: Optional[str] = None
    saml_auto_create_users: Optional[bool] = None
    # LDAP settings
    ldap_enabled: Optional[bool] = None
    ldap_server_url: Optional[str] = None
    ldap_use_ssl: Optional[bool] = None
    ldap_use_tls: Optional[bool] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_password: Optional[str] = None
    ldap_user_search_base: Optional[str] = None
    ldap_user_search_filter: Optional[str] = None
    ldap_auto_create_users: Optional[bool] = None


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
        local_allow_registration=settings.local.allow_registration,
        local_min_password_length=settings.local.min_password_length,
        dispatcharr_enabled=settings.dispatcharr.enabled,
        dispatcharr_auto_create_users=settings.dispatcharr.auto_create_users,
        oidc_enabled=settings.oidc.enabled,
        oidc_provider_name=settings.oidc.provider_name,
        oidc_discovery_url=settings.oidc.discovery_url,
        oidc_auto_create_users=settings.oidc.auto_create_users,
        saml_enabled=settings.saml.enabled,
        saml_provider_name=settings.saml.provider_name,
        saml_idp_metadata_url=settings.saml.idp_metadata_url,
        saml_auto_create_users=settings.saml.auto_create_users,
        ldap_enabled=settings.ldap.enabled,
        ldap_server_url=settings.ldap.server_url,
        ldap_use_ssl=settings.ldap.use_ssl,
        ldap_use_tls=settings.ldap.use_tls,
        ldap_user_search_base=settings.ldap.user_search_base,
        ldap_auto_create_users=settings.ldap.auto_create_users,
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
    if update_request.local_allow_registration is not None:
        settings.local.allow_registration = update_request.local_allow_registration
    if update_request.local_min_password_length is not None:
        settings.local.min_password_length = update_request.local_min_password_length

    # Update Dispatcharr settings
    if update_request.dispatcharr_enabled is not None:
        settings.dispatcharr.enabled = update_request.dispatcharr_enabled
    if update_request.dispatcharr_auto_create_users is not None:
        settings.dispatcharr.auto_create_users = update_request.dispatcharr_auto_create_users

    # Update OIDC settings
    if update_request.oidc_enabled is not None:
        settings.oidc.enabled = update_request.oidc_enabled
    if update_request.oidc_provider_name is not None:
        settings.oidc.provider_name = update_request.oidc_provider_name
    if update_request.oidc_client_id is not None:
        settings.oidc.client_id = update_request.oidc_client_id
    if update_request.oidc_client_secret is not None:
        settings.oidc.client_secret = update_request.oidc_client_secret
    if update_request.oidc_discovery_url is not None:
        settings.oidc.discovery_url = update_request.oidc_discovery_url
    if update_request.oidc_auto_create_users is not None:
        settings.oidc.auto_create_users = update_request.oidc_auto_create_users

    # Update SAML settings
    if update_request.saml_enabled is not None:
        settings.saml.enabled = update_request.saml_enabled
    if update_request.saml_provider_name is not None:
        settings.saml.provider_name = update_request.saml_provider_name
    if update_request.saml_idp_metadata_url is not None:
        settings.saml.idp_metadata_url = update_request.saml_idp_metadata_url
    if update_request.saml_sp_entity_id is not None:
        settings.saml.sp_entity_id = update_request.saml_sp_entity_id
    if update_request.saml_auto_create_users is not None:
        settings.saml.auto_create_users = update_request.saml_auto_create_users

    # Update LDAP settings
    if update_request.ldap_enabled is not None:
        settings.ldap.enabled = update_request.ldap_enabled
    if update_request.ldap_server_url is not None:
        settings.ldap.server_url = update_request.ldap_server_url
    if update_request.ldap_use_ssl is not None:
        settings.ldap.use_ssl = update_request.ldap_use_ssl
    if update_request.ldap_use_tls is not None:
        settings.ldap.use_tls = update_request.ldap_use_tls
    if update_request.ldap_bind_dn is not None:
        settings.ldap.bind_dn = update_request.ldap_bind_dn
    if update_request.ldap_bind_password is not None:
        settings.ldap.bind_password = update_request.ldap_bind_password
    if update_request.ldap_user_search_base is not None:
        settings.ldap.user_search_base = update_request.ldap_user_search_base
    if update_request.ldap_user_search_filter is not None:
        settings.ldap.user_search_filter = update_request.ldap_user_search_filter
    if update_request.ldap_auto_create_users is not None:
        settings.ldap.auto_create_users = update_request.ldap_auto_create_users

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


# =============================================================================
# OIDC Authentication
# =============================================================================

@router.get("/oidc/authorize")
async def oidc_authorize(
    request: Request,
):
    """
    Start OIDC authorization flow.

    Generates PKCE challenge, stores state, and redirects to OIDC provider.
    """
    from fastapi.responses import RedirectResponse
    from auth.providers.oidc import OIDCClient, get_state_store, OIDCDiscoveryError

    settings = get_auth_settings()
    if not settings.oidc.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC authentication is not enabled",
        )

    if not settings.oidc.discovery_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC discovery URL not configured",
        )

    # Build the callback URL
    # Use the request's base URL to construct the callback
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/oidc/callback"

    # Create auth state with PKCE
    state_store = get_state_store()
    auth_state = state_store.create_state(redirect_uri)

    # Get authorization URL
    try:
        async with OIDCClient(settings.oidc) as client:
            auth_url = await client.get_authorization_url(
                redirect_uri=redirect_uri,
                state=auth_state.state,
                nonce=auth_state.nonce,
                code_verifier=auth_state.code_verifier,
            )
    except OIDCDiscoveryError as e:
        logger.error(f"OIDC discovery failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"OIDC provider unavailable: {e}",
        )
    except Exception as e:
        logger.exception(f"OIDC authorization error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start OIDC authentication",
        )

    logger.info(f"Redirecting to OIDC provider for authorization")
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    response: Response,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """
    Handle OIDC callback from provider.

    Exchanges authorization code for tokens, validates ID token,
    creates/updates user, and sets session cookies.
    """
    from fastapi.responses import RedirectResponse
    from auth.providers.oidc import (
        OIDCClient,
        get_state_store,
        OIDCError,
        OIDCTokenError,
        OIDCValidationError,
    )
    from models import UserIdentity

    settings = get_auth_settings()
    if not settings.oidc.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC authentication is not enabled",
        )

    # Handle error responses from provider
    if error:
        logger.warning(f"OIDC provider returned error: {error} - {error_description}")
        # Redirect to login with error
        return RedirectResponse(
            url=f"/login?error=oidc_error&message={error_description or error}",
            status_code=302,
        )

    # Validate required parameters
    if not code or not state:
        logger.warning("OIDC callback missing code or state")
        return RedirectResponse(
            url="/login?error=oidc_error&message=Invalid callback parameters",
            status_code=302,
        )

    # Retrieve and validate state
    state_store = get_state_store()
    auth_state = state_store.get_state(state)

    if not auth_state:
        logger.warning("OIDC state not found or expired")
        return RedirectResponse(
            url="/login?error=oidc_error&message=Session expired, please try again",
            status_code=302,
        )

    # Exchange code for tokens and authenticate
    try:
        async with OIDCClient(settings.oidc) as client:
            auth_result = await client.authenticate(
                code=code,
                redirect_uri=auth_state.redirect_uri,
                code_verifier=auth_state.code_verifier,
                nonce=auth_state.nonce,
            )
    except OIDCTokenError as e:
        logger.error(f"OIDC token exchange failed: {e}")
        return RedirectResponse(
            url="/login?error=oidc_error&message=Authentication failed",
            status_code=302,
        )
    except OIDCValidationError as e:
        logger.error(f"OIDC token validation failed: {e}")
        return RedirectResponse(
            url="/login?error=oidc_error&message=Token validation failed",
            status_code=302,
        )
    except OIDCError as e:
        logger.error(f"OIDC authentication error: {e}")
        return RedirectResponse(
            url="/login?error=oidc_error&message=Authentication error",
            status_code=302,
        )
    except Exception as e:
        logger.exception(f"Unexpected OIDC error: {e}")
        return RedirectResponse(
            url="/login?error=oidc_error&message=Unexpected error",
            status_code=302,
        )

    # Find or create user
    # First, try to find user via identity table using 'sub' (subject identifier)
    identity = session.query(UserIdentity).filter(
        UserIdentity.provider == "oidc",
        UserIdentity.external_id == auth_result.sub,
    ).first()

    user = None
    if identity:
        user = identity.user
        # Update identity last_used_at
        identity.last_used_at = datetime.utcnow()
        # Update user info from OIDC
        if auth_result.email:
            user.email = auth_result.email
        if auth_result.name:
            user.display_name = auth_result.name
        logger.info(f"OIDC user found via identity: {user.username}")
    else:
        # Fallback to direct user lookup for backwards compatibility
        user = session.query(User).filter(
            User.auth_provider == "oidc",
            User.external_id == auth_result.sub,
        ).first()

        if user is not None:
            # Update existing user info from OIDC
            if auth_result.email:
                user.email = auth_result.email
            if auth_result.name:
                user.display_name = auth_result.name
            logger.info(f"Updated user info from OIDC: {user.username}")
        elif settings.oidc.auto_create_users:
            # Create new user from OIDC
            # Check if username exists with different provider
            existing = session.query(User).filter(User.username == auth_result.username).first()
            if existing:
                # Username taken - create with modified username
                username = f"oidc_{auth_result.username}"
                logger.info(f"Username '{auth_result.username}' taken, using '{username}'")
            else:
                username = auth_result.username

            user = User(
                username=username,
                email=auth_result.email,
                display_name=auth_result.name,
                auth_provider="oidc",
                external_id=auth_result.sub,
                is_admin=False,  # OIDC users are not admins by default
                is_active=True,
            )
            session.add(user)
            session.flush()  # Flush to get the user ID

            # Create identity for the new user
            new_identity = UserIdentity(
                user_id=user.id,
                provider="oidc",
                external_id=auth_result.sub,
                identifier=auth_result.username,
            )
            session.add(new_identity)
            logger.info(f"Created new user from OIDC: {user.username} (id={user.id})")
        else:
            # Auto-create is disabled
            logger.warning(f"OIDC user not found and auto-create disabled: {auth_result.sub}")
            return RedirectResponse(
                url="/login?error=oidc_error&message=User not found. Contact administrator.",
                status_code=302,
            )

    # Check if user is active
    if not user.is_active:
        logger.warning(f"OIDC login for disabled user: {user.username}")
        return RedirectResponse(
            url="/login?error=oidc_error&message=User account is disabled",
            status_code=302,
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

    # Create redirect response with cookies
    redirect_response = RedirectResponse(url="/", status_code=302)
    _set_auth_cookies(redirect_response, access_token, refresh_token)

    logger.info(f"OIDC user logged in: {user.username}")

    return redirect_response


# =============================================================================
# OIDC Account Linking
# =============================================================================

@router.get("/identities/link/oidc/authorize")
async def oidc_link_authorize(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Start OIDC linking flow for an authenticated user.

    Similar to authorize, but stores the user ID to link after callback.
    """
    from fastapi.responses import RedirectResponse
    from auth.providers.oidc import OIDCClient, get_state_store, OIDCDiscoveryError

    settings = get_auth_settings()
    if not settings.oidc.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC authentication is not enabled",
        )

    # Build the callback URL for linking
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/identities/link/oidc/callback"

    # Create auth state with PKCE and linking user ID
    state_store = get_state_store()
    auth_state = state_store.create_state(redirect_uri, linking_user_id=current_user.id)

    # Get authorization URL
    try:
        async with OIDCClient(settings.oidc) as client:
            auth_url = await client.get_authorization_url(
                redirect_uri=redirect_uri,
                state=auth_state.state,
                nonce=auth_state.nonce,
                code_verifier=auth_state.code_verifier,
            )
    except OIDCDiscoveryError as e:
        logger.error(f"OIDC discovery failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"OIDC provider unavailable: {e}",
        )

    logger.info(f"Redirecting user {current_user.username} to OIDC provider for linking")
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/identities/link/oidc/callback")
async def oidc_link_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """
    Handle OIDC linking callback.

    Links the OIDC identity to the user who initiated the linking flow.
    """
    from fastapi.responses import RedirectResponse
    from auth.providers.oidc import (
        OIDCClient,
        get_state_store,
        OIDCError,
    )
    from models import UserIdentity

    settings = get_auth_settings()

    # Handle error responses from provider
    if error:
        logger.warning(f"OIDC link error: {error} - {error_description}")
        return RedirectResponse(
            url=f"/settings?tab=linked-accounts&error=oidc_error&message={error_description or error}",
            status_code=302,
        )

    # Validate required parameters
    if not code or not state:
        return RedirectResponse(
            url="/settings?tab=linked-accounts&error=oidc_error&message=Invalid callback",
            status_code=302,
        )

    # Retrieve and validate state
    state_store = get_state_store()
    auth_state = state_store.get_state(state)

    if not auth_state or not auth_state.linking_user_id:
        return RedirectResponse(
            url="/settings?tab=linked-accounts&error=oidc_error&message=Session expired",
            status_code=302,
        )

    # Get the user who initiated linking
    user = session.query(User).filter(User.id == auth_state.linking_user_id).first()
    if not user:
        return RedirectResponse(
            url="/login?error=oidc_error&message=User not found",
            status_code=302,
        )

    # Exchange code for tokens and authenticate
    try:
        async with OIDCClient(settings.oidc) as client:
            auth_result = await client.authenticate(
                code=code,
                redirect_uri=auth_state.redirect_uri,
                code_verifier=auth_state.code_verifier,
                nonce=auth_state.nonce,
            )
    except OIDCError as e:
        logger.error(f"OIDC link authentication error: {e}")
        return RedirectResponse(
            url="/settings?tab=linked-accounts&error=oidc_error&message=Authentication failed",
            status_code=302,
        )

    # Check if this OIDC identity is already linked to another account
    existing_identity = session.query(UserIdentity).filter(
        UserIdentity.provider == "oidc",
        UserIdentity.external_id == auth_result.sub,
    ).first()

    if existing_identity:
        if existing_identity.user_id == user.id:
            return RedirectResponse(
                url="/settings?tab=linked-accounts&message=OIDC account already linked",
                status_code=302,
            )
        else:
            return RedirectResponse(
                url="/settings?tab=linked-accounts&error=oidc_error&message=This OIDC account is linked to another user",
                status_code=302,
            )

    # Check if user already has an OIDC identity
    user_oidc_identity = session.query(UserIdentity).filter(
        UserIdentity.user_id == user.id,
        UserIdentity.provider == "oidc",
    ).first()

    if user_oidc_identity:
        return RedirectResponse(
            url="/settings?tab=linked-accounts&error=oidc_error&message=You already have an OIDC identity linked",
            status_code=302,
        )

    # Create the identity link
    new_identity = UserIdentity(
        user_id=user.id,
        provider="oidc",
        external_id=auth_result.sub,
        identifier=auth_result.username,
    )
    session.add(new_identity)
    session.commit()

    logger.info(f"User {user.username} linked OIDC identity: {auth_result.sub}")

    return RedirectResponse(
        url="/settings?tab=linked-accounts&message=OIDC account linked successfully",
        status_code=302,
    )
