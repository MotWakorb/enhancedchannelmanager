"""
Unit tests for password security utilities.

TDD SPEC: These tests define expected password handling behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: Password Security (v6dxf.8.2)
"""
import pytest
import time


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_hash_password_returns_bcrypt_hash(self):
        """hash_password() returns bcrypt hash starting with $2b$."""
        from auth.password import hash_password

        hashed = hash_password("testpassword123")
        assert hashed.startswith("$2b$")

    def test_hash_password_returns_different_hashes_for_same_input(self):
        """hash_password() with same input returns different hashes (salt)."""
        from auth.password import hash_password

        hash1 = hash_password("samepassword")
        hash2 = hash_password("samepassword")
        assert hash1 != hash2

    def test_verify_password_returns_true_for_correct_password(self):
        """verify_password() returns True for correct password."""
        from auth.password import hash_password, verify_password

        password = "correctpassword123"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_password_returns_false_for_incorrect_password(self):
        """verify_password() returns False for incorrect password."""
        from auth.password import hash_password, verify_password

        hashed = hash_password("correctpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_verify_password_returns_false_for_empty_password(self):
        """verify_password() returns False for empty password."""
        from auth.password import hash_password, verify_password

        hashed = hash_password("validpassword123")
        assert verify_password("", hashed) is False

    def test_hashing_takes_minimum_time(self):
        """Hashing takes >100ms (cost factor protection against brute force)."""
        from auth.password import hash_password

        start = time.time()
        hash_password("testpassword")
        elapsed = time.time() - start
        # bcrypt should take at least 100ms with proper cost factor
        assert elapsed > 0.1, f"Hashing took only {elapsed*1000:.0f}ms, should be >100ms"


class TestPasswordValidation:
    """Tests for password validation rules."""

    def test_password_minimum_length(self):
        """Password must be >= 8 characters."""
        from auth.password import validate_password

        # Too short
        result = validate_password("Short1!")
        assert result.valid is False
        assert "8 characters" in result.error

        # Exactly 8 characters (valid length)
        result = validate_password("Valid1!!")
        assert "8 characters" not in (result.error or "")

    def test_password_requires_uppercase(self):
        """Password must contain uppercase letter."""
        from auth.password import validate_password

        result = validate_password("alllowercase123!")
        assert result.valid is False
        assert "uppercase" in result.error.lower()

    def test_password_requires_lowercase(self):
        """Password must contain lowercase letter."""
        from auth.password import validate_password

        result = validate_password("ALLUPPERCASE123!")
        assert result.valid is False
        assert "lowercase" in result.error.lower()

    def test_password_requires_number(self):
        """Password must contain a number."""
        from auth.password import validate_password

        result = validate_password("NoNumbers!!")
        assert result.valid is False
        assert "number" in result.error.lower()

    def test_valid_password_passes_validation(self):
        """Valid password passes all validation rules."""
        from auth.password import validate_password

        result = validate_password("ValidPass123!")
        assert result.valid is True
        assert result.error is None

    def test_password_cannot_be_common(self):
        """Password cannot be common/weak password."""
        from auth.password import validate_password

        common_passwords = [
            "Password123!",
            "Qwerty123!",
            "Admin123!",
            "Welcome123!",
            "Letmein123!",
        ]
        for password in common_passwords:
            result = validate_password(password)
            assert result.valid is False, f"'{password}' should be rejected as common"
            assert "common" in result.error.lower()

    def test_password_cannot_match_username(self):
        """Password cannot match or contain username."""
        from auth.password import validate_password

        # Password is the username
        result = validate_password("AdminUser123!", username="adminuser")
        assert result.valid is False
        assert "username" in result.error.lower()

        # Password contains username
        result = validate_password("MyJohnDoe123!", username="johndoe")
        assert result.valid is False

    def test_password_validation_result_structure(self):
        """validate_password() returns proper result structure."""
        from auth.password import validate_password, PasswordValidationResult

        result = validate_password("test")
        assert isinstance(result, PasswordValidationResult)
        assert hasattr(result, "valid")
        assert hasattr(result, "error")
