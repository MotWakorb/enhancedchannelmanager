"""
Unit tests for tls.storage.CertificateStorage (bead u8qr6.1).

CertificateStorage handles TLS private-key material and certificate
validation; prior to this suite only ensure_directory() and has_certificate()
were covered. These tests exercise the remaining public methods with REAL
cryptographic round-trips (generate key/cert -> save -> load -> parse) plus
the error/edge paths (missing files, malformed PEM, mismatched pair,
unsupported key type, on-disk permissions).

Certificates are generated at runtime with `cryptography` (already a project
dependency) so no fixture key material is checked into the repo.
"""
import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID

from tls.storage import CertificateInfo, CertificateStorage


# ---------------------------------------------------------------------------
# cryptography >= 42 gate (bead enhancedchannelmanager-vol5d).
#
# CertificateStorage.parse_certificate() reads x509.Certificate.not_valid_
# before_utc / not_valid_after_utc, added in cryptography 42. Older
# cryptography (e.g. the 41.0.7 that ships with a bare system python3, vs.
# the 42+ pinned in the project .venv) raises AttributeError there, which
# parse_certificate's broad except Exception swallows into an is_valid=False
# CertificateInfo — so the tests below fail on assertions (wrong subject,
# wrong is_valid, wrong dates), not on an obvious ImportError/AttributeError,
# which is what made the failure mode confusing enough to cost two engineers
# real time. Gate exactly the tests whose assertions depend on a genuinely
# parsed certificate; everything else (directory perms, raw file round-trips,
# ACME JSON persistence, the already-invalid/malformed/mismatched paths, the
# timezone-skew suite that constructs CertificateInfo directly) runs
# unmodified under any cryptography version.
def _cryptography_version_tuple() -> tuple[int, ...]:
    """Best-effort (major, minor, ...) parse of cryptography.__version__.

    Deliberately avoids depending on `packaging` here — the version gate
    itself must not be able to fail before it explains why the real test
    would have.
    """
    import cryptography

    parts = []
    for chunk in cryptography.__version__.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


_CRYPTOGRAPHY_TOO_OLD = _cryptography_version_tuple() < (42,)
_CRYPTOGRAPHY_SKIP_REASON = (
    "cryptography >= 42 required (not_valid_before_utc); run under the "
    "project venv: .venv/bin/python -m pytest"
)
requires_cryptography_42 = pytest.mark.skipif(
    _CRYPTOGRAPHY_TOO_OLD, reason=_CRYPTOGRAPHY_SKIP_REASON
)


# ---------------------------------------------------------------------------
# Helpers: generate real key material and self-signed certs on the fly.
# ---------------------------------------------------------------------------

def _utc_naive(dt: datetime) -> datetime:
    """cryptography's classic builder API expects naive-UTC datetimes."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _make_cert(
    signing_key,
    public_key,
    cn: str = "example.com",
    sans: list[str] | None = None,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> x509.Certificate:
    now = datetime.now(timezone.utc)
    nb = not_before or (now - timedelta(days=1))
    na = not_after or (now + timedelta(days=365))
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(_utc_naive(nb))
        .not_valid_after(_utc_naive(na))
    )
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]),
            critical=False,
        )
    # Ed25519 signs with algorithm=None; RSA/EC use SHA-256.
    algo = None if isinstance(signing_key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
    return builder.sign(signing_key, algo)


def _key_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _rsa_pair(cn="example.com", sans=None, not_before=None, not_after=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = _make_cert(key, key.public_key(), cn=cn, sans=sans,
                      not_before=not_before, not_after=not_after)
    return _cert_pem(cert), _key_pem(key)


def _ec_pair(cn="ec.example.com"):
    key = ec.generate_private_key(ec.SECP256R1())
    cert = _make_cert(key, key.public_key(), cn=cn)
    return _cert_pem(cert), _key_pem(key)


def _ed25519_pair(cn="ed.example.com"):
    key = ed25519.Ed25519PrivateKey.generate()
    cert = _make_cert(key, key.public_key(), cn=cn)
    return _cert_pem(cert), _key_pem(key)


# Module-scoped valid RSA pair (2048-bit generation is the slow part; reuse it).
@pytest.fixture(scope="module")
def rsa_pair():
    return _rsa_pair(cn="example.com", sans=["example.com", "www.example.com"])


@pytest.fixture
def storage(tmp_path):
    return CertificateStorage(tmp_path / "tls")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------------------------------------------------------------------
# ensure_directory
# ---------------------------------------------------------------------------

class TestEnsureDirectory:
    def test_creates_dir_with_owner_only_perms(self, storage):
        assert storage.ensure_directory() is True
        assert storage.tls_dir.exists()
        assert _mode(storage.tls_dir) == 0o700

    def test_idempotent(self, storage):
        assert storage.ensure_directory() is True
        assert storage.ensure_directory() is True


# ---------------------------------------------------------------------------
# save_certificate / load_certificate round-trips
# ---------------------------------------------------------------------------

class TestSaveLoadCertificate:
    def test_round_trip_cert_and_key(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        assert storage.save_certificate(cert_pem, key_pem) is True

        loaded_cert, loaded_key = storage.load_certificate()
        assert loaded_cert == cert_pem
        assert loaded_key == key_pem

    def test_accepts_str_input(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        assert storage.save_certificate(cert_pem.decode(), key_pem.decode()) is True
        loaded_cert, loaded_key = storage.load_certificate()
        # Stored as the utf-8 encoding of the strings.
        assert loaded_cert == cert_pem
        assert loaded_key == key_pem

    def test_written_files_are_owner_only(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        assert storage.save_certificate(cert_pem, key_pem) is True
        assert _mode(storage.cert_path) == 0o600
        assert _mode(storage.key_path) == 0o600

    def test_save_with_chain_writes_fullchain(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        chain_pem = b"-----BEGIN CERTIFICATE-----\nZmFrZWNoYWlu\n-----END CERTIFICATE-----\n"
        assert storage.save_certificate(cert_pem, key_pem, chain_pem=chain_pem) is True

        assert storage.chain_path.read_bytes() == chain_pem
        assert storage.fullchain_path.read_bytes() == cert_pem + b"\n" + chain_pem
        assert _mode(storage.chain_path) == 0o600
        assert _mode(storage.fullchain_path) == 0o600

    def test_save_rejects_mismatched_pair(self, storage, rsa_pair):
        cert_pem, _ = rsa_pair
        _, other_key_pem = _rsa_pair(cn="other.example.com")
        # cert from pair A, key from pair B -> validation fails, nothing written.
        assert storage.save_certificate(cert_pem, other_key_pem) is False
        assert not storage.cert_path.exists()
        assert not storage.key_path.exists()

    def test_load_missing_returns_none_pair(self, storage):
        assert storage.load_certificate() == (None, None)

    def test_load_missing_key_returns_none_pair(self, storage, rsa_pair):
        cert_pem, _ = rsa_pair
        storage.ensure_directory()
        storage.cert_path.write_bytes(cert_pem)
        # key.pem absent -> guarded to (None, None)
        assert storage.load_certificate() == (None, None)


# ---------------------------------------------------------------------------
# validate_pair
# ---------------------------------------------------------------------------

class TestValidatePair:
    @requires_cryptography_42
    def test_valid_rsa_pair(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        info = storage.validate_pair(cert_pem, key_pem)
        assert info.is_valid is True
        assert info.validation_error is None
        assert info.subject == "example.com"

    @requires_cryptography_42
    def test_valid_ec_pair(self, storage):
        cert_pem, key_pem = _ec_pair()
        info = storage.validate_pair(cert_pem, key_pem)
        assert info.is_valid is True
        assert info.subject == "ec.example.com"

    def test_mismatched_rsa_pair(self, storage, rsa_pair):
        cert_pem, _ = rsa_pair
        _, other_key = _rsa_pair(cn="mismatch.example.com")
        info = storage.validate_pair(cert_pem, other_key)
        assert info.is_valid is False
        assert "does not match" in info.validation_error

    def test_mismatched_ec_pair(self, storage):
        cert_pem, _ = _ec_pair(cn="a.example.com")
        _, other_key = _ec_pair(cn="b.example.com")
        info = storage.validate_pair(cert_pem, other_key)
        assert info.is_valid is False
        assert "does not match" in info.validation_error

    def test_key_cert_type_crossed(self, storage, rsa_pair):
        """RSA cert with an EC key: types don't line up -> unsupported/mismatch."""
        cert_pem, _ = rsa_pair
        _, ec_key = _ec_pair()
        info = storage.validate_pair(cert_pem, ec_key)
        assert info.is_valid is False
        assert info.validation_error

    def test_unsupported_key_type(self, storage):
        """Ed25519 pairs are structurally valid but unsupported by validate_pair."""
        cert_pem, key_pem = _ed25519_pair()
        info = storage.validate_pair(cert_pem, key_pem)
        assert info.is_valid is False
        assert "Unsupported key type" in info.validation_error

    def test_malformed_cert(self, storage, rsa_pair):
        _, key_pem = rsa_pair
        info = storage.validate_pair(b"-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----\n", key_pem)
        assert info.is_valid is False
        assert info.validation_error

    def test_malformed_key(self, storage, rsa_pair):
        cert_pem, _ = rsa_pair
        info = storage.validate_pair(cert_pem, b"not a private key")
        assert info.is_valid is False
        assert "Cannot load private key" in info.validation_error


# ---------------------------------------------------------------------------
# parse_certificate
# ---------------------------------------------------------------------------

class TestParseCertificate:
    @requires_cryptography_42
    def test_extracts_subject_issuer_and_sans(self, storage):
        cert_pem, _ = _rsa_pair(cn="host.example.com", sans=["host.example.com", "alt.example.com"])
        info = storage.parse_certificate(cert_pem)
        assert info.is_valid is True
        assert info.subject == "host.example.com"
        assert info.issuer == "host.example.com"  # self-signed
        # Use .count() (exact membership) rather than `"x" in info.domains`: the
        # latter trips CodeQL's py/incomplete-url-substring-sanitization on the
        # string-in-x pattern even though info.domains is a list — and count is
        # more precise (also proves the CN-vs-SAN de-duplication below).
        assert info.domains.count("alt.example.com") == 1
        # CN is not duplicated when also present as a SAN.
        assert info.domains.count("host.example.com") == 1
        assert info.serial_number  # hex string, non-empty

    @requires_cryptography_42
    def test_cert_without_sans(self, storage):
        cert_pem, _ = _rsa_pair(cn="nosan.example.com", sans=None)
        info = storage.parse_certificate(cert_pem)
        assert info.is_valid is True
        assert info.domains == ["nosan.example.com"]

    def test_malformed_pem_returns_invalid(self, storage):
        info = storage.parse_certificate(b"definitely not a certificate")
        assert info.is_valid is False
        assert info.validation_error
        assert info.domains == []


# ---------------------------------------------------------------------------
# get_certificate_info / is_expiring_soon
# ---------------------------------------------------------------------------

class TestCertificateInfoAccessors:
    def test_get_certificate_info_none_when_absent(self, storage):
        assert storage.get_certificate_info() is None

    @requires_cryptography_42
    def test_get_certificate_info_after_save(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        storage.save_certificate(cert_pem, key_pem)
        info = storage.get_certificate_info()
        assert info is not None
        assert info.subject == "example.com"

    def test_is_expiring_soon_false_when_absent(self, storage):
        assert storage.is_expiring_soon() is False

    @requires_cryptography_42
    def test_is_expiring_soon_true_for_near_expiry(self, storage):
        now = datetime.now(timezone.utc)
        cert_pem, key_pem = _rsa_pair(
            cn="soon.example.com",
            not_before=now - timedelta(days=1),
            not_after=now + timedelta(days=10),
        )
        storage.save_certificate(cert_pem, key_pem)
        assert storage.is_expiring_soon(days=30) is True
        assert storage.is_expiring_soon(days=5) is False

    @requires_cryptography_42
    def test_is_expiring_soon_false_for_distant_expiry(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair  # 365-day cert
        storage.save_certificate(cert_pem, key_pem)
        assert storage.is_expiring_soon(days=30) is False


# ---------------------------------------------------------------------------
# CertificateInfo dataclass helpers
# ---------------------------------------------------------------------------

class TestCertificateInfoDataclass:
    @requires_cryptography_42
    def test_days_until_expiry_and_flags_for_valid_cert(self, storage):
        now = datetime.now(timezone.utc)
        cert_pem, _ = _rsa_pair(
            not_before=now - timedelta(days=1),
            not_after=now + timedelta(days=20),
        )
        info = storage.parse_certificate(cert_pem)
        assert 18 <= info.days_until_expiry() <= 21
        assert info.is_expired() is False
        assert info.is_not_yet_valid() is False

    def test_expired_cert_flags(self, storage):
        now = datetime.now(timezone.utc)
        cert_pem, _ = _rsa_pair(
            not_before=now - timedelta(days=20),
            not_after=now - timedelta(days=5),
        )
        info = storage.parse_certificate(cert_pem)
        assert info.is_expired() is True
        assert info.days_until_expiry() == 0

    @requires_cryptography_42
    def test_not_yet_valid_cert_flags(self, storage):
        now = datetime.now(timezone.utc)
        cert_pem, _ = _rsa_pair(
            not_before=now + timedelta(days=5),
            not_after=now + timedelta(days=30),
        )
        info = storage.parse_certificate(cert_pem)
        assert info.is_not_yet_valid() is True


class TestCertificateInfoTimezoneSkew:
    """Regression for bead n5zw2: expiry/validity math must compare cert times
    (naive UTC) against naive *UTC* now, not naive *local* now.

    These tests simulate a host at a nonzero UTC offset by patching
    ``tls.storage.datetime`` so that ``datetime.now()`` (naive local) runs
    +10h ahead of ``datetime.now(timezone.utc)`` (true UTC). Under the old
    ``datetime.now()``-based helpers the offset flips the result; the fixed
    helpers (naive-UTC now) are offset-independent.
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

        monkeypatch.setattr("tls.storage.datetime", _OffsetDatetime)
        # Cert times as stored by parse_certificate: naive UTC.
        return true_utc.replace(tzinfo=None)

    def _info(self, not_before, not_after):
        return CertificateInfo(
            subject="tz.example.com",
            issuer="tz.example.com",
            serial_number="1",
            not_before=not_before,
            not_after=not_after,
            domains=["tz.example.com"],
            is_valid=True,
        )

    def test_not_expired_when_within_offset_window(self, offset_clock):
        """Cert expiring 5h after true UTC now: valid. Under old naive-local
        logic (+10h) it would read expired 5h early."""
        utc_now = offset_clock
        info = self._info(
            not_before=utc_now - timedelta(days=1),
            not_after=utc_now + timedelta(hours=5),
        )
        assert info.is_expired() is False
        assert info.days_until_expiry() == 0  # <1 day, but not negative/expired

    def test_valid_cert_not_flipped_by_offset(self, offset_clock):
        """A comfortably-valid cert must not be marked expired by the offset."""
        utc_now = offset_clock
        info = self._info(
            not_before=utc_now - timedelta(days=2),
            not_after=utc_now + timedelta(days=10),
        )
        assert info.is_expired() is False
        assert info.is_not_yet_valid() is False
        # 10 days minus a fraction; old +10h logic would still say ~9-10 but the
        # boundary tests above are the load-bearing skew assertions.
        assert 9 <= info.days_until_expiry() <= 10

    def test_not_yet_valid_when_within_offset_window(self, offset_clock):
        """Cert becoming valid 5h after true UTC now: not yet valid. Under old
        naive-local logic (+10h) it would read as already valid."""
        utc_now = offset_clock
        info = self._info(
            not_before=utc_now + timedelta(hours=5),
            not_after=utc_now + timedelta(days=30),
        )
        assert info.is_not_yet_valid() is True


# ---------------------------------------------------------------------------
# delete_certificate
# ---------------------------------------------------------------------------

class TestDeleteCertificate:
    def test_removes_all_cert_files(self, storage, rsa_pair):
        cert_pem, key_pem = rsa_pair
        chain_pem = b"-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
        storage.save_certificate(cert_pem, key_pem, chain_pem=chain_pem)
        assert storage.has_certificate() is True

        assert storage.delete_certificate() is True
        assert not storage.cert_path.exists()
        assert not storage.key_path.exists()
        assert not storage.chain_path.exists()
        assert not storage.fullchain_path.exists()
        assert storage.has_certificate() is False

    def test_delete_is_idempotent_when_absent(self, storage):
        # Nothing to delete -> still reports success.
        assert storage.delete_certificate() is True


# ---------------------------------------------------------------------------
# ACME account persistence
# ---------------------------------------------------------------------------

class TestAcmeAccountPersistence:
    def test_save_and_load_round_trip(self, storage):
        account = {"kid": "https://acme/acct/1", "key": {"kty": "RSA", "n": "abc"}}
        assert storage.save_acme_account(account) is True
        assert _mode(storage.acme_account_path) == 0o600
        assert storage.load_acme_account() == account

    def test_load_missing_returns_none(self, storage):
        assert storage.load_acme_account() is None

    def test_load_malformed_json_returns_none(self, storage):
        storage.ensure_directory()
        storage.acme_account_path.write_text("{ not valid json")
        assert storage.load_acme_account() is None


# ---------------------------------------------------------------------------
# has_certificate (completeness alongside the round-trip suite)
# ---------------------------------------------------------------------------

class TestHasCertificate:
    def test_reflects_presence(self, storage, rsa_pair):
        assert storage.has_certificate() is False
        cert_pem, key_pem = rsa_pair
        storage.save_certificate(cert_pem, key_pem)
        assert storage.has_certificate() is True
