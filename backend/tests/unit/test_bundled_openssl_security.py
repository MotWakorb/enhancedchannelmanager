"""Guard cryptography's static OpenSSL, not Python ssl's system library.

CVE-2026-84782 and CVE-2026-84783 require OpenSSL 4.0.3; cryptography
50.0.1 wheels contain only 4.0.2. These are version/artifact and ordinary
crypto-operation regressions, not a reproduction of either vulnerability.
"""

import re
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

_SERVICE = Path(__file__).resolve().parents[2]


def _assert_fixed_openssl(text: str) -> None:
    match = re.fullmatch(r"OpenSSL 4\.0\.(\d+)(?: \d{1,2} [A-Z][a-z]{2} \d{4})?", text)
    assert match is not None, f"Missing/unreviewed bundled OpenSSL identity: {text!r}"
    assert int(match[1]) >= 3, f"Bundled OpenSSL is vulnerable: {text}"


def _assert_fixed_requirements(source: str, lock: str) -> None:
    assert re.findall(r"^cryptography>=(\d+\.\d+\.\d+)$", source, re.MULTILINE) == [
        "50.0.2"
    ], "Keep an explicit cryptography>=50.0.2 security floor"
    assert re.findall(r"^cryptography==(\d+\.\d+\.\d+)$", lock, re.MULTILINE) == [
        "50.0.2"
    ], "Keep the reviewed OpenSSL 4.0.3 wheel selection"


def test_loaded_cryptography_contains_fixed_openssl() -> None:
    # Do not substitute ssl.OPENSSL_VERSION: that reports another library.
    _assert_fixed_openssl(backend.openssl_version_text())
    assert tuple(map(int, version("cryptography").split("."))) >= (50, 0, 2)


def test_service_floor_and_lock_select_fixed_wheel() -> None:
    _assert_fixed_requirements(
        (_SERVICE / "requirements.in").read_text(),
        (_SERVICE / "requirements.txt").read_text(),
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "OpenSSL 4.0.1 9 Jun 2026",
        "OpenSSL 4.0.2 25 Aug 2026",
        "OpenSSL 3.5.3 16 Sep 2025",
        "LibreSSL 4.0.3",
        "libcrypto 4.0.3",
        "OpenSSL 4.0.3-dev",
        "OpenSSL 4.1.0",
        "OpenSSL 4.0.3\nOpenSSL 4.0.1",
    ],
)
def test_openssl_guard_rejects_vulnerable_or_missing_identity(text: str) -> None:
    with pytest.raises(AssertionError):
        _assert_fixed_openssl(text)


@pytest.mark.parametrize("text", ["OpenSSL 4.0.3 29 Sep 2026", "OpenSSL 4.0.10"])
def test_openssl_guard_accepts_fixed_patch_versions(text: str) -> None:
    _assert_fixed_openssl(text)


@pytest.mark.parametrize(
    ("source", "lock"),
    [
        ("", ""),
        ("", "cryptography==50.0.2"),
        ("cryptography>=50.0.2", ""),
        ("cryptography>=50.0.0", "cryptography==50.0.2"),
        ("cryptography>=50.0.1", "cryptography==50.0.2"),
        ("cryptography>=50.0.2", "cryptography==50.0.0"),
        ("cryptography>=50.0.2", "cryptography==50.0.1"),
        ("other-library>=50.0.2", "other-library==50.0.2"),
        ("# cryptography>=50.0.2", "# cryptography==50.0.2"),
    ],
)
def test_requirement_guard_fails_closed(source: str, lock: str) -> None:
    with pytest.raises(AssertionError):
        _assert_fixed_requirements(source, lock)


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_rsa_key_roundtrip_and_signature_verification(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    loaded = serialization.load_pem_private_key(pem, password=None)
    assert isinstance(loaded, rsa.RSAPrivateKey)
    message = b"synthetic bundled OpenSSL compatibility check"
    signature = loaded.sign(message, padding.PKCS1v15(), hashes.SHA256())
    rsa_key.public_key().verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    with pytest.raises(InvalidSignature):
        rsa_key.public_key().verify(
            signature, message + b"tampered", padding.PKCS1v15(), hashes.SHA256()
        )


def test_csr_and_x509_signatures_roundtrip(rsa_key: rsa.RSAPrivateKey) -> None:
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "native-test.invalid")]
    )
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(rsa_key, hashes.SHA256())
    )
    loaded_csr = x509.load_pem_x509_csr(csr.public_bytes(serialization.Encoding.PEM))
    assert loaded_csr.is_signature_valid
    assert loaded_csr.subject == subject
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(loaded_csr.subject)
        .issuer_name(subject)
        .public_key(loaded_csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(rsa_key, hashes.SHA256())
    )
    loaded = x509.load_pem_x509_certificate(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
    loaded.verify_directly_issued_by(loaded)
    assert loaded.subject == subject
    assert not loaded.extensions.get_extension_for_class(x509.BasicConstraints).value.ca


def test_aesgcm_roundtrip_rejects_tampering() -> None:
    cipher = AESGCM(AESGCM.generate_key(bit_length=256))
    nonce = b"test-nonce12"
    aad = b"synthetic native regression"
    plaintext = b"not a production secret"
    ciphertext = cipher.encrypt(nonce, plaintext, aad)
    assert cipher.decrypt(nonce, ciphertext, aad) == plaintext
    with pytest.raises(InvalidTag):
        cipher.decrypt(nonce, ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), aad)
