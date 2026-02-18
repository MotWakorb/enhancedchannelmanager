"""
Admin API endpoints for user management.

Provides CRUD operations for user accounts (admin only).
"""
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database import get_session
from models import User
from .password import hash_password
from .dependencies import get_current_active_admin
from .routes import UserResponse


logger = logging.getLogger(__name__)

# Create router with admin tag
router = APIRouter(prefix="/api/admin", tags=["Admin"])


# Request/Response Models
class AdminCreateUserRequest(BaseModel):
    """Admin create user request body."""
    username: str
    email: Optional[EmailStr] = None
    password: str
    display_name: Optional[str] = None
    is_admin: bool = False


class AdminUpdateUserRequest(BaseModel):
    """Admin update user request body."""
    email: Optional[EmailStr] = None
    display_name: Optional[str] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None  # Optional password reset


class UserListResponse(BaseModel):
    """Paginated user list response."""
    users: List[UserResponse]
    total: int
    page: int
    per_page: int


class SingleUserResponse(BaseModel):
    """Single user response."""
    user: UserResponse


class DeleteUserResponse(BaseModel):
    """Delete user response."""
    message: str = "User deactivated successfully"


# =============================================================================
# Admin User Management Endpoints
# =============================================================================

@router.get("/users", response_model=UserListResponse)
async def list_users(
    search: Optional[str] = Query(None, description="Search by username or email"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_admin: User = Depends(get_current_active_admin),
    session: Session = Depends(get_session),
):
    """
    List all users with pagination and search.

    Admin only endpoint.
    """
    query = session.query(User)

    # Apply search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                User.username.ilike(search_term),
                User.email.ilike(search_term),
            )
        )

    # Get total count
    total = query.count()

    # Apply pagination
    offset = (page - 1) * per_page
    users = query.order_by(User.id).offset(offset).limit(per_page).all()

    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/users/{user_id}", response_model=SingleUserResponse)
async def get_user(
    user_id: int,
    current_admin: User = Depends(get_current_active_admin),
    session: Session = Depends(get_session),
):
    """
    Get a single user by ID.

    Admin only endpoint.
    """
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return SingleUserResponse(user=UserResponse.model_validate(user))


@router.post("/users", response_model=SingleUserResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_user(
    create_request: AdminCreateUserRequest,
    current_admin: User = Depends(get_current_active_admin),
    session: Session = Depends(get_session),
):
    """
    Create a new user as admin.

    Admin can bypass some password rules but basic validation still applies.
    """
    # Check if username already exists
    existing_user = session.query(User).filter(User.username == create_request.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    # Check if email already exists (if provided)
    if create_request.email:
        existing_email = session.query(User).filter(User.email == create_request.email).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

    # Admin-created users have relaxed password requirements
    # Just check minimum length
    if len(create_request.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 6 characters",
        )

    # Create user
    user = User(
        username=create_request.username,
        email=create_request.email,
        password_hash=hash_password(create_request.password),
        display_name=create_request.display_name,
        auth_provider="local",
        is_admin=create_request.is_admin,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    logger.info("[AUTH-ADMIN] Admin %s created user: %s", current_admin.username, user.username)

    return SingleUserResponse(user=UserResponse.model_validate(user))


@router.patch("/users/{user_id}", response_model=SingleUserResponse)
async def admin_update_user(
    user_id: int,
    update_request: AdminUpdateUserRequest,
    current_admin: User = Depends(get_current_active_admin),
    session: Session = Depends(get_session),
):
    """
    Update a user's fields.

    Admin only endpoint.
    """
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update fields if provided
    if update_request.email is not None:
        # Check for duplicate email
        existing = session.query(User).filter(
            User.email == update_request.email,
            User.id != user_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )
        user.email = update_request.email

    if update_request.display_name is not None:
        user.display_name = update_request.display_name

    if update_request.is_admin is not None:
        # Prevent removing own admin status
        if user.id == current_admin.id and not update_request.is_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove your own admin status",
            )
        user.is_admin = update_request.is_admin

    if update_request.is_active is not None:
        # Prevent deactivating self
        if user.id == current_admin.id and not update_request.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account",
            )
        user.is_active = update_request.is_active

    if update_request.password is not None:
        if len(update_request.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Password must be at least 6 characters",
            )
        user.password_hash = hash_password(update_request.password)

    user.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(user)

    logger.info("[AUTH-ADMIN] Admin %s updated user: %s", current_admin.username, user.username)

    return SingleUserResponse(user=UserResponse.model_validate(user))


@router.delete("/users/{user_id}", response_model=DeleteUserResponse)
async def admin_delete_user(
    user_id: int,
    current_admin: User = Depends(get_current_active_admin),
    session: Session = Depends(get_session),
):
    """
    Soft delete (deactivate) a user.

    Admin only endpoint. Does not permanently delete the user.
    """
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-deletion
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    # Soft delete (deactivate)
    user.is_active = False
    user.updated_at = datetime.utcnow()
    session.commit()

    logger.info("[AUTH-ADMIN] Admin %s deactivated user: %s", current_admin.username, user.username)

    return DeleteUserResponse(message="User deactivated successfully")
