"""
Password hashing and validation utilities.

Uses bcrypt directly for secure password hashing with automatic salt generation.
No dependency on unmaintained passlib library.

Password policy follows NIST 800-63B guidelines:
- Minimum 8 characters
- Check against common/breached password list
- No composition rules (uppercase/lowercase/number)
- No periodic rotation requirements
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bcrypt


logger = logging.getLogger(__name__)

# bcrypt cost factor (rounds = 2^BCRYPT_ROUNDS)
# 12 provides good security (~300ms hash time on modern hardware)
BCRYPT_ROUNDS = 12

# Common/breached passwords loaded from file (NIST 800-63B)
_common_passwords: Optional[set] = None
_COMMON_PASSWORDS_FILE = Path(__file__).parent / "common_passwords.txt"

# Inline fallback if file is missing
_FALLBACK_PASSWORDS = {
    "password", "password123", "password1", "password!",
    "qwerty", "qwerty123", "qwerty1",
    "123456", "12345678", "123456789", "1234567890",
    "admin", "admin123", "admin1", "administrator",
    "welcome", "welcome123", "welcome1",
    "letmein", "letmein123", "letmein1",
    "monkey", "dragon", "master",
    "login", "abc123", "111111",
    "iloveyou", "sunshine", "princess",
    "football", "baseball", "soccer",
    "passw0rd", "p@ssword", "p@ssw0rd",
}


def _load_common_passwords() -> set:
    """Load common passwords from file, falling back to inline set."""
    global _common_passwords
    if _common_passwords is not None:
        return _common_passwords

    if _COMMON_PASSWORDS_FILE.exists():
        try:
            _common_passwords = {
                line.strip().lower()
                for line in _COMMON_PASSWORDS_FILE.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            }
            logger.info("[AUTH] Loaded %d common passwords from %s", len(_common_passwords), _COMMON_PASSWORDS_FILE)
            return _common_passwords
        except Exception as e:
            logger.warning("[AUTH] Failed to load common passwords file: %s", e)

    _common_passwords = _FALLBACK_PASSWORDS
    return _common_passwords


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
    Validate password strength per NIST 800-63B guidelines.

    Requirements:
    - At least 8 characters
    - Not a common/breached password
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

    password_lower = password.lower()

    # Username check
    if username:
        username_lower = username.lower()
        if username_lower in password_lower:
            return PasswordValidationResult(
                valid=False,
                error="Password cannot contain your username."
            )

    # Common/breached password check (case-insensitive)
    common = _load_common_passwords()
    if password_lower in common:
        return PasswordValidationResult(
            valid=False,
            error="This password is too common. Please choose a stronger password."
        )

    return PasswordValidationResult(valid=True)
