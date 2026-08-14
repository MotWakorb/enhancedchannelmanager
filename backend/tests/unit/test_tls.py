"""
Unit tests for TLS certificate management module.

These tests are designed to run without the josepy dependency
by importing submodules directly instead of through __init__.py.
"""
import json
import logging
import os
import pathlib
import stat
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Test TLS settings without needing josepy - import directly from submodule
from tls.settings import TLSSettings, save_tls_settings, load_tls_settings, clear_tls_settings_cache


def _fake_owner_stat(target_path, fake_uid):
    """Patch Path.stat and os.stat so ``target_path`` reports a foreign owner.

    Same shape as the m40pn helper in ``tests/test_cloud_storage.py``: the real
    mode bits are preserved so the mode branch behaves normally, only st_uid is
    overridden, and only for the target path, so chmod/tempfile internals are
    unaffected.
    """
    real_path_stat = pathlib.Path.stat
    real_os_stat = os.stat
    target_str = str(target_path)

    class _FakeStatResult:
        def __init__(self, src):
            self._src = src

        def __getattr__(self, name):
            if name == "st_uid":
                return fake_uid
            return getattr(self._src, name)

    def _path_side_effect(self, *args, **kwargs):
        result = real_path_stat(self, *args, **kwargs)
        if str(self) == target_str:
            return _FakeStatResult(result)
        return result

    def _os_side_effect(path, *args, **kwargs):
        result = real_os_stat(path, *args, **kwargs)
        if str(path) == target_str:
            return _FakeStatResult(result)
        return result

    class _Both:
        def __enter__(self):
            self._a = patch.object(pathlib.Path, "stat", _path_side_effect)
            self._b = patch("tls.settings.os.stat", _os_side_effect)
            self._a.__enter__()
            self._b.__enter__()
            return self

        def __exit__(self, *exc):
            self._b.__exit__(*exc)
            self._a.__exit__(*exc)
            return False

    return _Both()


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


class TestTLSSettingsStartupIntegrityProbe:
    """Bead 2owpi: the m40pn startup probe extended to tls_settings.json.

    ``cloud_storage.crypto.verify_key_integrity_at_startup`` (bead m40pn)
    surfaces mode/ownership drift on the Fernet key at every container start
    rather than at the first scheduled backup. ``tls_settings.json`` holds the
    same class of secret in the same directory and had no such probe, so a
    0644 left behind by a manual edit or a restore was invisible until someone
    thought to look. The PO declined at-rest encryption for this file on
    2026-08-13 (ECM must decrypt unattended, so the key would sit beside it);
    this probe is what was chosen instead, because it catches a real
    misconfiguration rather than a threat the architecture cannot defend
    against.

    Posture mirrors m40pn exactly: repair the mode, treat ownership as
    advisory under root, log an unmissable ERROR when it is not, and NEVER
    raise. TLS settings must not be able to take down channel management.
    """

    def _probe(self, config_file):
        from tls.settings import verify_tls_settings_integrity_at_startup
        with patch('tls.settings.TLS_CONFIG_FILE', config_file):
            return verify_tls_settings_integrity_at_startup()

    def test_absent_file_is_not_a_violation(self, tmp_path, caplog):
        """TLS is optional. An unconfigured instance must probe clean."""
        with caplog.at_level(logging.ERROR, logger="tls.settings"):
            result = self._probe(tmp_path / "tls_settings.json")

        assert result is True
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_correct_mode_and_owner_passes_quietly(self, tmp_path, caplog):
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text('{"enabled": false}')
        config_file.chmod(0o600)

        with caplog.at_level(logging.WARNING, logger="tls.settings"):
            result = self._probe(config_file)

        assert result is True
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_drifted_mode_is_repaired_to_0600(self, tmp_path, caplog):
        """The case this probe exists for: a 0644 after an edit or a restore."""
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text('{"enabled": false}')
        config_file.chmod(0o644)

        with caplog.at_level(logging.WARNING, logger="tls.settings"):
            result = self._probe(config_file)

        assert result is True
        assert stat.S_IMODE(os.stat(config_file).st_mode) == 0o600
        messages = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.WARNING]
        assert any("mode_repaired" in m for m in messages)

    def test_unrepairable_mode_logs_error_and_returns_false(
        self, tmp_path, caplog
    ):
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text('{"enabled": false}')
        config_file.chmod(0o644)

        with patch('tls.settings.os.chmod', side_effect=PermissionError("nope")):
            with caplog.at_level(logging.ERROR, logger="tls.settings"):
                result = self._probe(config_file)

        assert result is False
        messages = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.ERROR]
        assert any("mode_repair_failed" in m for m in messages)

    def test_foreign_owner_under_root_is_advisory_only(self, tmp_path, caplog):
        """Root reads any file regardless of owner; mode is the real control."""
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text('{"enabled": false}')
        config_file.chmod(0o600)

        foreign_uid = os.stat(config_file).st_uid + 4242
        with _fake_owner_stat(config_file, foreign_uid):
            with patch('tls.settings.os.getuid', return_value=0):
                with caplog.at_level(logging.ERROR, logger="tls.settings"):
                    result = self._probe(config_file)

        assert result is True
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_foreign_owner_when_not_root_logs_error_and_never_raises(
        self, tmp_path, caplog
    ):
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text('{"enabled": false}')
        config_file.chmod(0o600)

        foreign_uid = os.stat(config_file).st_uid + 4242
        with _fake_owner_stat(config_file, foreign_uid):
            with patch('tls.settings.os.getuid', return_value=1000):
                with caplog.at_level(logging.ERROR, logger="tls.settings"):
                    result = self._probe(config_file)

        assert result is False
        messages = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.ERROR]
        assert any("ownership_unfixable" in m for m in messages)

    def test_probe_never_raises_even_when_stat_explodes(self, tmp_path, caplog):
        """Log-loudly-but-boot. Nothing in here may abort startup."""
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text('{"enabled": false}')

        with patch('tls.settings.os.stat', side_effect=OSError("boom")):
            with caplog.at_level(logging.ERROR, logger="tls.settings"):
                result = self._probe(config_file)

        assert result is False

    def test_probe_reads_no_credential_out_of_the_file(self, tmp_path, caplog):
        """The probe checks metadata. It must never open the contents."""
        config_file = tmp_path / "tls_settings.json"
        config_file.write_text(json.dumps({
            "enabled": True,
            "dns_api_token": "<synthetic-probe-token-2owpi>",
        }))
        config_file.chmod(0o644)

        with caplog.at_level(logging.DEBUG, logger="tls.settings"):
            self._probe(config_file)

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "<synthetic-probe-token-2owpi>" not in logged


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
