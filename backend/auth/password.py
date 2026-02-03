"""
Password hashing and validation utilities.

Uses bcrypt directly for secure password hashing with automatic salt generation.
No dependency on unmaintained passlib library.
"""
import re
from dataclasses import dataclass
from typing import Optional

import bcrypt


# bcrypt cost factor (rounds = 2^BCRYPT_ROUNDS)
# 12 provides good security (~300ms hash time on modern hardware)
BCRYPT_ROUNDS = 12


# Common/weak passwords to reject
COMMON_PASSWORDS = {
    "password", "password123", "password1", "password!",
    "qwerty", "qwerty123", "qwerty1",
    "123456", "12345678", "123456789",
    "admin", "admin123", "admin1", "administrator",
    "welcome", "welcome123", "welcome1",
    "letmein", "letmein123", "letmein1",
    "monkey", "dragon", "master",
    "login", "abc123", "111111",
    "iloveyou", "sunshine", "princess",
    "football", "baseball", "soccer",
    "passw0rd", "p@ssword", "p@ssw0rd",
}


@dataclass
class PasswordValidationResult:
    """Result of password validation."""
    valid: bool
    error: Optional[str] = None


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: The plaintext password to hash.

    Returns:
        The bcrypt hash string (starts with $2b$).
    """
    # Encode to bytes, generate salt, and hash
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against its hash.

    Args:
        password: The plaintext password to verify.
        password_hash: The bcrypt hash to verify against.

    Returns:
        True if the password matches, False otherwise.
    """
    if not password or not password_hash:
        return False
    try:
        password_bytes = password.encode("utf-8")
        hash_bytes = password_hash.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hash_bytes)
    except Exception:
        return False


def validate_password(password: str, username: Optional[str] = None) -> PasswordValidationResult:
    """
    Validate password strength and security.

    Requirements:
    - At least 8 characters
    - Contains uppercase letter
    - Contains lowercase letter
    - Contains number
    - Not a common/weak password
    - Does not contain username

    Args:
        password: The password to validate.
        username: Optional username to check against.

    Returns:
        PasswordValidationResult with valid=True or error message.
    """
    # Length check
    if len(password) < 8:
        return PasswordValidationResult(
            valid=False,
            error="Password must be at least 8 characters long."
        )

    # Uppercase check
    if not re.search(r"[A-Z]", password):
        return PasswordValidationResult(
            valid=False,
            error="Password must contain at least one uppercase letter."
        )

    # Lowercase check
    if not re.search(r"[a-z]", password):
        return PasswordValidationResult(
            valid=False,
            error="Password must contain at least one lowercase letter."
        )

    # Number check
    if not re.search(r"\d", password):
        return PasswordValidationResult(
            valid=False,
            error="Password must contain at least one number."
        )

    password_lower = password.lower()

    # Username check (before common password check to provide better feedback)
    if username:
        username_lower = username.lower()
        if username_lower in password_lower:
            return PasswordValidationResult(
                valid=False,
                error="Password cannot contain your username."
            )

    # Common password check (case-insensitive)
    # Check base password without numbers/symbols
    base_password = re.sub(r"[^a-z]", "", password_lower)
    if base_password in COMMON_PASSWORDS or password_lower in COMMON_PASSWORDS:
        return PasswordValidationResult(
            valid=False,
            error="Password is too common. Please choose a stronger password."
        )

    # Also check if password starts with a common word
    for common in COMMON_PASSWORDS:
        if password_lower.startswith(common) and len(common) >= 4:
            return PasswordValidationResult(
                valid=False,
                error="Password is too common. Please choose a stronger password."
            )

    return PasswordValidationResult(valid=True)
