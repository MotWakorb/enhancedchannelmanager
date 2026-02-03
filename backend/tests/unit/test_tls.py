"""
Unit tests for TLS certificate management module.

These tests are designed to run without the josepy dependency
by importing submodules directly instead of through __init__.py.
"""
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os
import sys

# Test TLS settings without needing josepy - import directly from submodule
from tls.settings import TLSSettings, save_tls_settings, load_tls_settings, clear_tls_settings_cache


class TestTLSSettings:
    """Test TLS settings schema and validation."""

    def test_default_settings(self):
        """Test default TLS settings."""
        settings = TLSSettings()
        assert settings.enabled is False
        assert settings.mode == "letsencrypt"
        assert settings.domain == ""
        assert settings.challenge_type == "http-01"
        assert settings.auto_renew is True
        assert settings.renew_days_before_expiry == 30

    def test_domain_validation_strips_protocol(self):
        """Test domain validation removes http:// prefix."""
        settings = TLSSettings(domain="http://example.com")
        assert settings.domain == "example.com"

        settings = TLSSettings(domain="https://example.com/")
        assert settings.domain == "example.com"

    def test_domain_validation_strips_whitespace(self):
        """Test domain validation strips whitespace."""
        settings = TLSSettings(domain="  EXAMPLE.COM  ")
        assert settings.domain == "example.com"

    def test_email_validation_lowercase(self):
        """Test email validation lowercases."""
        settings = TLSSettings(acme_email="ADMIN@Example.COM")
        assert settings.acme_email == "admin@example.com"

    def test_is_configured_for_letsencrypt(self):
        """Test Let's Encrypt configuration check."""
        # Not configured without domain and email
        settings = TLSSettings()
        assert settings.is_configured_for_letsencrypt() is False

        # Configured with HTTP-01
        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
            challenge_type="http-01",
        )
        assert settings.is_configured_for_letsencrypt() is True

        # DNS-01 requires dns_provider and api_token
        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
            challenge_type="dns-01",
        )
        assert settings.is_configured_for_letsencrypt() is False

        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
            challenge_type="dns-01",
            dns_provider="cloudflare",
            dns_api_token="token123",
        )
        assert settings.is_configured_for_letsencrypt() is True

    def test_get_expiry_days(self):
        """Test expiry days calculation."""
        settings = TLSSettings()
        assert settings.get_expiry_days() is None

        # Set expiry 30 days in future
        future = datetime.now() + timedelta(days=30)
        settings = TLSSettings(cert_expires_at=future.isoformat())
        days = settings.get_expiry_days()
        assert days is not None
        assert 29 <= days <= 31

        # Expired certificate
        past = datetime.now() - timedelta(days=10)
        settings = TLSSettings(cert_expires_at=past.isoformat())
        days = settings.get_expiry_days()
        assert days == 0

    def test_needs_renewal(self):
        """Test renewal needed check."""
        settings = TLSSettings()
        assert settings.needs_renewal() is False  # No cert

        # Certificate far from expiry
        future = datetime.now() + timedelta(days=60)
        settings = TLSSettings(
            auto_renew=True,
            cert_expires_at=future.isoformat(),
            renew_days_before_expiry=30,
        )
        assert settings.needs_renewal() is False

        # Certificate near expiry
        near_future = datetime.now() + timedelta(days=15)
        settings = TLSSettings(
            auto_renew=True,
            cert_expires_at=near_future.isoformat(),
            renew_days_before_expiry=30,
        )
        assert settings.needs_renewal() is True

        # Auto-renew disabled
        settings = TLSSettings(
            auto_renew=False,
            cert_expires_at=near_future.isoformat(),
            renew_days_before_expiry=30,
        )
        assert settings.needs_renewal() is False


class TestTLSSettingsPersistence:
    """Test TLS settings save/load."""

    def test_save_and_load_settings(self, tmp_path):
        """Test saving and loading settings."""
        with patch('tls.settings.CONFIG_DIR', tmp_path):
            with patch('tls.settings.TLS_CONFIG_FILE', tmp_path / "tls_settings.json"):
                clear_tls_settings_cache()

                # Save settings
                settings = TLSSettings(
                    enabled=True,
                    domain="example.com",
                    acme_email="admin@example.com",
                )
                result = save_tls_settings(settings)
                assert result is True

                # Load settings
                clear_tls_settings_cache()
                loaded = load_tls_settings()
                assert loaded.enabled is True
                assert loaded.domain == "example.com"
                assert loaded.acme_email == "admin@example.com"


# Import storage only after TLSSettings tests (doesn't need josepy)
# These tests mock the crypto operations
class TestCertificateStorageMocked:
    """Test certificate storage with mocked crypto."""

    def test_ensure_directory(self, tmp_path):
        """Test directory creation."""
        with patch('tls.storage.TLS_DIR', tmp_path / "tls"):
            from tls.storage import CertificateStorage

            storage = CertificateStorage(tmp_path / "tls")
            result = storage.ensure_directory()
            assert result is True
            assert storage.tls_dir.exists()

    def test_has_certificate(self, tmp_path):
        """Test certificate existence check."""
        from tls.storage import CertificateStorage

        storage = CertificateStorage(tmp_path)
        assert storage.has_certificate() is False

        # Create dummy cert/key files
        (tmp_path / "cert.pem").write_text("cert")
        (tmp_path / "key.pem").write_text("key")
        assert storage.has_certificate() is True


# Test challenges module (doesn't need crypto)
class TestChallenges:
    """Test ACME challenge handlers."""

    def test_register_and_get_http_challenge(self):
        """Test HTTP challenge registration."""
        from tls.challenges import (
            register_http_challenge,
            get_http_challenge_response,
            clear_http_challenge,
            clear_all_http_challenges,
        )

        # Clear any existing challenges
        clear_all_http_challenges()

        # Register challenge
        register_http_challenge("token123", "response456")
        assert get_http_challenge_response("token123") == "response456"
        assert get_http_challenge_response("unknown") is None

        # Clear challenge
        clear_http_challenge("token123")
        assert get_http_challenge_response("token123") is None

    def test_get_pending_challenge_count(self):
        """Test challenge count."""
        from tls.challenges import (
            register_http_challenge,
            get_pending_challenge_count,
            clear_all_http_challenges,
        )

        clear_all_http_challenges()
        assert get_pending_challenge_count() == 0

        register_http_challenge("token1", "resp1")
        register_http_challenge("token2", "resp2")
        assert get_pending_challenge_count() == 2

        clear_all_http_challenges()
        assert get_pending_challenge_count() == 0
