"""
Unit tests for TLS certificate management module.

These tests are designed to run without the josepy dependency
by importing submodules directly instead of through __init__.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

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
        """Test Let's Encrypt DNS-01 configuration check."""
        # Not configured without domain and email
        settings = TLSSettings()
        assert settings.is_configured_for_letsencrypt() is False

        # Configured with just domain and email (manual DNS setup)
        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
        )
        assert settings.is_configured_for_letsencrypt() is True

        # Configured with Cloudflare provider requires api_token
        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
            dns_provider="cloudflare",
        )
        assert settings.is_configured_for_letsencrypt() is False

        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
            dns_provider="cloudflare",
            dns_api_token="token123",
        )
        assert settings.is_configured_for_letsencrypt() is True

        # Configured with Route53 provider
        settings = TLSSettings(
            domain="example.com",
            acme_email="admin@example.com",
            dns_provider="route53",
            aws_access_key_id="AKIA...",
            aws_secret_access_key="secret",
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


class TestTLSSettingsTimezoneSkew:
    """Regression for bead wccvo: get_expiry_days/needs_renewal must compare
    cert_expires_at (naive UTC, populated from CertificateInfo.not_after) against
    naive *UTC* now, not naive *local* now. Same bug class as n5zw2
    (tls/storage.py), fixed here in tls/settings.py.

    These tests simulate a host at a nonzero UTC offset by patching
    ``tls.settings.datetime`` so that ``datetime.now()`` (naive local) runs
    +10h ahead of ``datetime.now(timezone.utc)`` (true UTC). Under the old
    ``datetime.now()``-based comparison the offset flips the result; the fixed
    helper (naive-UTC now) is offset-independent.
    """

    OFFSET_HOURS = 10  # simulate a host at UTC+10
    TRUE_UTC = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    @pytest.fixture
    def offset_clock(self, monkeypatch):
        offset = self.OFFSET_HOURS
        true_utc = self.TRUE_UTC

        class _OffsetDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    # Naive local time on a UTC+offset host.
                    return (true_utc + timedelta(hours=offset)).replace(tzinfo=None)
                return true_utc.astimezone(tz)

        monkeypatch.setattr("tls.settings.datetime", _OffsetDatetime)
        # cert_expires_at as populated from CertificateInfo.not_after: naive UTC.
        return true_utc.replace(tzinfo=None)

    def test_get_expiry_days_not_shortened_by_offset(self, offset_clock):
        """Cert expiring 2 days 5h after true UTC now must read 2 days left.

        The +10h local-vs-UTC offset straddles a whole-day boundary here:
        under the old ``datetime.now()`` (naive local, +10h ahead of true
        UTC) logic, delta = 2d5h - 10h = 1d19h -> 1 day left. The fixed
        naive-UTC comparison must read 2 days left, not 1."""
        utc_now = offset_clock
        settings = TLSSettings(
            cert_expires_at=(utc_now + timedelta(days=2, hours=5)).isoformat()
        )
        assert settings.get_expiry_days() == 2

    def test_needs_renewal_not_falsely_triggered_by_offset(self, offset_clock):
        """Cert 31 days 2h from true UTC expiry, renew threshold 30 days: must
        NOT need renewal yet.

        Under the old ``datetime.now()`` (naive local, +10h ahead of true
        UTC) logic, delta = 31d2h - 10h = 30d16h -> 30 days left, which trips
        the <=30 threshold and wrongly reports needs_renewal() True a full
        day early. The fixed naive-UTC comparison reads the true 31 days
        left and correctly reports False."""
        utc_now = offset_clock
        settings = TLSSettings(
            auto_renew=True,
            cert_expires_at=(utc_now + timedelta(days=31, hours=2)).isoformat(),
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


# Note: HTTP-01 challenge tests removed - only DNS-01 is supported
# The challenges module now only contains verify_dns_challenge() which
# requires network access and is tested in integration tests
