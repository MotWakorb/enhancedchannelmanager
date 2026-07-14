"""
Unit tests for password security utilities.

TDD SPEC: These tests define expected password handling behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: Password Security (v6dxf.8.2)
"""


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

    def test_hashing_uses_strong_cost_factor(self):
        """bcrypt hashes embed a cost factor high enough to resist brute force.

        The security property is "the work factor is high enough," and bcrypt
        records that factor directly in the hash ($2b$<cost>$<salt+digest>).
        Parsing it is deterministic — unlike the previous wall-clock assertion
        (elapsed > 100ms), which flaked on fast hardware / under CI contention
        and only checked the work factor indirectly.
        """
        from auth.password import BCRYPT_ROUNDS, hash_password

        hashed = hash_password("testpassword")
        # bcrypt format: $2b$<two-digit-cost>$<22-char-salt><31-char-digest>
        cost = int(hashed.split("$")[2])
        assert cost == BCRYPT_ROUNDS, (
            f"bcrypt hash cost {cost} does not match configured "
            f"BCRYPT_ROUNDS={BCRYPT_ROUNDS}"
        )
        assert cost >= 12, (
            f"bcrypt cost factor {cost} is below the safe minimum of 12"
        )


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

    def test_no_composition_rules(self):
        """NIST 800-63B: no uppercase/lowercase/number requirements."""
        from auth.password import validate_password

        # All lowercase — should pass (no composition rules)
        result = validate_password("alllowercase")
        assert result.valid is True

        # All uppercase — should pass
        result = validate_password("ALLUPPERCASE")
        assert result.valid is True

        # No numbers — should pass
        result = validate_password("nonumbershere")
        assert result.valid is True

    def test_valid_password_passes_validation(self):
        """Valid password passes all validation rules."""
        from auth.password import validate_password

        result = validate_password("mysecurepassphrase")
        assert result.valid is True
        assert result.error is None

    def test_password_cannot_be_common(self):
        """Password cannot be common/breached password."""
        from auth.password import validate_password

        common_passwords = [
            "password",
            "trustno1",
            "12345678",
            "qwerty123",
            "iloveyou",
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
