"""
Unit tests for JWT token handling.

TDD SPEC: These tests define expected JWT behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: JWT Tokens (v6dxf.8.3)
"""
import pytest
from datetime import datetime, timedelta


class TestTokenCreation:
    """Tests for JWT token creation."""

    def test_create_access_token_returns_valid_jwt_string(self):
        """create_access_token() returns valid JWT string."""
        from auth.tokens import create_access_token

        token = create_access_token(user_id=1, username="testuser")
        # JWT format: header.payload.signature
        parts = token.split(".")
        assert len(parts) == 3
        assert all(len(part) > 0 for part in parts)

    def test_access_token_contains_required_claims(self):
        """Token contains user_id, exp, iat claims."""
        from auth.tokens import create_access_token, decode_token

        token = create_access_token(user_id=42, username="testuser")
        claims = decode_token(token)

        assert claims["sub"] == 42  # user_id in subject
        assert "exp" in claims  # expiration
        assert "iat" in claims  # issued at
        assert claims["username"] == "testuser"

    def test_access_token_default_expiry(self):
        """Access token expires after configured time (default 30 min)."""
        from auth.tokens import create_access_token, decode_token

        token = create_access_token(user_id=1, username="test")
        claims = decode_token(token)

        exp = datetime.fromtimestamp(claims["exp"])
        iat = datetime.fromtimestamp(claims["iat"])
        delta = exp - iat

        # Default should be 30 minutes
        assert timedelta(minutes=25) < delta <= timedelta(minutes=35)

    def test_access_token_custom_expiry(self):
        """Access token can have custom expiry time."""
        from auth.tokens import create_access_token, decode_token

        token = create_access_token(
            user_id=1, username="test", expires_delta=timedelta(hours=1)
        )
        claims = decode_token(token)

        exp = datetime.fromtimestamp(claims["exp"])
        iat = datetime.fromtimestamp(claims["iat"])
        delta = exp - iat

        assert timedelta(minutes=55) < delta <= timedelta(minutes=65)

    def test_create_refresh_token_returns_longer_lived_token(self):
        """create_refresh_token() returns longer-lived token (default 7 days)."""
        from auth.tokens import create_refresh_token, decode_token

        token = create_refresh_token(user_id=1)
        claims = decode_token(token)

        exp = datetime.fromtimestamp(claims["exp"])
        iat = datetime.fromtimestamp(claims["iat"])
        delta = exp - iat

        # Default should be 7 days
        assert timedelta(days=6) < delta <= timedelta(days=8)

    def test_refresh_token_has_type_claim(self):
        """Refresh token has type claim to distinguish from access token."""
        from auth.tokens import create_refresh_token, decode_token

        token = create_refresh_token(user_id=1)
        claims = decode_token(token)

        assert claims.get("type") == "refresh"


class TestTokenValidation:
    """Tests for JWT token validation."""

    def test_decode_token_returns_claims_for_valid_token(self):
        """decode_token() returns claims for valid token."""
        from auth.tokens import create_access_token, decode_token

        token = create_access_token(user_id=123, username="validuser")
        claims = decode_token(token)

        assert claims is not None
        assert claims["sub"] == 123
        assert claims["username"] == "validuser"

    def test_decode_token_raises_for_expired_token(self):
        """decode_token() raises for expired token."""
        from auth.tokens import create_access_token, decode_token, TokenExpiredError

        # Create token that expires immediately
        token = create_access_token(
            user_id=1, username="test", expires_delta=timedelta(seconds=-1)
        )

        with pytest.raises(TokenExpiredError):
            decode_token(token)

    def test_decode_token_raises_for_tampered_token(self):
        """decode_token() raises for tampered token."""
        from auth.tokens import create_access_token, decode_token, InvalidTokenError

        token = create_access_token(user_id=1, username="test")
        # Tamper with the payload (middle part)
        parts = token.split(".")
        parts[1] = parts[1][:-5] + "XXXXX"  # Modify payload
        tampered_token = ".".join(parts)

        with pytest.raises(InvalidTokenError):
            decode_token(tampered_token)

    def test_decode_token_raises_for_invalid_signature(self):
        """decode_token() raises for invalid signature."""
        from auth.tokens import create_access_token, decode_token, InvalidTokenError

        token = create_access_token(user_id=1, username="test")
        # Change the signature (last part)
        parts = token.split(".")
        parts[2] = "invalidsignature123"
        bad_sig_token = ".".join(parts)

        with pytest.raises(InvalidTokenError):
            decode_token(bad_sig_token)

    def test_decode_token_raises_for_malformed_token(self):
        """decode_token() raises for malformed token."""
        from auth.tokens import decode_token, InvalidTokenError

        malformed_tokens = [
            "notavalidtoken",
            "only.two.parts.here.extra",
            "",
            "a.b",
            "...",
        ]

        for bad_token in malformed_tokens:
            with pytest.raises(InvalidTokenError):
                decode_token(bad_token)

    def test_decode_token_raises_for_none(self):
        """decode_token() raises for None token."""
        from auth.tokens import decode_token, InvalidTokenError

        with pytest.raises((InvalidTokenError, TypeError)):
            decode_token(None)


class TestTokenRefresh:
    """Tests for token refresh functionality."""

    def test_valid_refresh_token_generates_new_access_token(self):
        """Valid refresh token can generate new access token."""
        from auth.tokens import (
            create_refresh_token,
            refresh_access_token,
            decode_token,
        )

        refresh_token = create_refresh_token(user_id=1)
        new_access_token = refresh_access_token(refresh_token)

        # New access token should be valid
        claims = decode_token(new_access_token)
        assert claims["sub"] == 1

    def test_used_refresh_token_is_invalidated(self):
        """Used refresh token is invalidated (one-time use)."""
        from auth.tokens import (
            create_refresh_token,
            refresh_access_token,
            TokenRevokedError,
        )

        refresh_token = create_refresh_token(user_id=1)

        # First use should succeed
        refresh_access_token(refresh_token)

        # Second use should fail
        with pytest.raises(TokenRevokedError):
            refresh_access_token(refresh_token)

    def test_refresh_token_rotation_creates_new_refresh_token(self):
        """Refresh token rotation creates new refresh token."""
        from auth.tokens import create_refresh_token, rotate_refresh_token

        old_refresh_token = create_refresh_token(user_id=1)
        new_access_token, new_refresh_token = rotate_refresh_token(old_refresh_token)

        assert new_access_token is not None
        assert new_refresh_token is not None
        assert new_refresh_token != old_refresh_token

    def test_cannot_use_access_token_as_refresh_token(self):
        """Access token cannot be used as refresh token."""
        from auth.tokens import (
            create_access_token,
            refresh_access_token,
            InvalidTokenError,
        )

        access_token = create_access_token(user_id=1, username="test")

        with pytest.raises(InvalidTokenError):
            refresh_access_token(access_token)
