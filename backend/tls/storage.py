"""
Certificate storage and validation.

Manages certificate and private key storage with proper security,
and provides certificate validation and info extraction.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.x509.oid import NameOID


logger = logging.getLogger(__name__)

# Default TLS directory
TLS_DIR = Path(os.environ.get("CONFIG_DIR", "/config")) / "tls"


@dataclass
class CertificateInfo:
    """Information extracted from a certificate."""

    subject: str
    issuer: str
    serial_number: str
    not_before: datetime
    not_after: datetime
    domains: list[str]  # Subject CN + SANs
    is_valid: bool
    validation_error: Optional[str] = None

    def days_until_expiry(self) -> int:
        """Get days until certificate expires."""
        delta = self.not_after - datetime.now()
        return max(0, delta.days)

    def is_expired(self) -> bool:
        """Check if certificate is expired."""
        return datetime.now() > self.not_after

    def is_not_yet_valid(self) -> bool:
        """Check if certificate is not yet valid."""
        return datetime.now() < self.not_before


class CertificateStorage:
    """Manages certificate and key storage on disk."""

    def __init__(self, tls_dir: Optional[Path] = None):
        """Initialize storage with optional custom directory."""
        self.tls_dir = tls_dir or TLS_DIR
        self.cert_path = self.tls_dir / "cert.pem"
        self.key_path = self.tls_dir / "key.pem"
        self.chain_path = self.tls_dir / "chain.pem"
        self.fullchain_path = self.tls_dir / "fullchain.pem"
        self.acme_account_path = self.tls_dir / "acme_account.json"

    def ensure_directory(self) -> bool:
        """Ensure TLS directory exists with proper permissions."""
        try:
            self.tls_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.tls_dir, 0o700)
            return True
        except (PermissionError, OSError) as e:
            logger.error("[TLS-STORAGE] Failed to create TLS directory: %s", e)
            return False

    def save_certificate(
        self,
        cert_pem: str | bytes,
        key_pem: str | bytes,
        chain_pem: Optional[str | bytes] = None,
    ) -> bool:
        """
        Save certificate and key to disk.

        Args:
            cert_pem: PEM-encoded certificate
            key_pem: PEM-encoded private key
            chain_pem: Optional PEM-encoded certificate chain

        Returns:
            True if successful, False otherwise
        """
        if not self.ensure_directory():
            return False

        try:
            # Convert to bytes if string
            if isinstance(cert_pem, str):
                cert_pem = cert_pem.encode("utf-8")
            if isinstance(key_pem, str):
                key_pem = key_pem.encode("utf-8")

            # Validate cert/key pair before saving
            validation = self.validate_pair(cert_pem, key_pem)
            if not validation.is_valid:
                logger.error("[TLS-STORAGE] Certificate validation failed: %s", validation.validation_error)
                return False

            # Write certificate (readable by owner + group only)
            self.cert_path.write_bytes(cert_pem)
            os.chmod(self.cert_path, 0o640)

            # Write private key (restricted permissions)
            self.key_path.write_bytes(key_pem)
            os.chmod(self.key_path, 0o600)

            # Write chain if provided
            if chain_pem:
                if isinstance(chain_pem, str):
                    chain_pem = chain_pem.encode("utf-8")
                self.chain_path.write_bytes(chain_pem)
                os.chmod(self.chain_path, 0o640)

                # Create fullchain (cert + chain)
                fullchain = cert_pem + b"\n" + chain_pem
                self.fullchain_path.write_bytes(fullchain)
                os.chmod(self.fullchain_path, 0o640)

            logger.info("[TLS-STORAGE] Certificate saved to %s", self.cert_path)
            return True

        except Exception as e:
            logger.error("[TLS-STORAGE] Failed to save certificate: %s", e)
            return False

    def load_certificate(self) -> tuple[Optional[bytes], Optional[bytes]]:
        """
        Load certificate and key from disk.

        Returns:
            Tuple of (cert_pem, key_pem) or (None, None) if not found
        """
        if not self.cert_path.exists() or not self.key_path.exists():
            return None, None

        try:
            cert_pem = self.cert_path.read_bytes()
            key_pem = self.key_path.read_bytes()
            return cert_pem, key_pem
        except Exception as e:
            logger.error("[TLS-STORAGE] Failed to load certificate: %s", e)
            return None, None

    def get_certificate_info(self) -> Optional[CertificateInfo]:
        """Get info about the stored certificate."""
        cert_pem, _ = self.load_certificate()
        if not cert_pem:
            return None

        return self.parse_certificate(cert_pem)

    def validate_pair(
        self,
        cert_pem: bytes,
        key_pem: bytes,
    ) -> CertificateInfo:
        """
        Validate that certificate and key form a valid pair.

        Args:
            cert_pem: PEM-encoded certificate
            key_pem: PEM-encoded private key

        Returns:
            CertificateInfo with validation result
        """
        try:
            # Parse certificate
            cert = x509.load_pem_x509_certificate(cert_pem)

            # Parse private key
            try:
                key = serialization.load_pem_private_key(key_pem, password=None)
            except Exception as e:
                # Try with empty password
                try:
                    key = serialization.load_pem_private_key(key_pem, password=b"")
                except Exception:
                    raise ValueError(f"Cannot load private key: {e}")

            # Verify key matches certificate
            cert_public_key = cert.public_key()

            if isinstance(cert_public_key, rsa.RSAPublicKey) and isinstance(
                key, rsa.RSAPrivateKey
            ):
                if (
                    cert_public_key.public_numbers()
                    != key.public_key().public_numbers()
                ):
                    raise ValueError("RSA private key does not match certificate")
            elif isinstance(cert_public_key, ec.EllipticCurvePublicKey) and isinstance(
                key, ec.EllipticCurvePrivateKey
            ):
                if (
                    cert_public_key.public_numbers()
                    != key.public_key().public_numbers()
                ):
                    raise ValueError("EC private key does not match certificate")
            else:
                raise ValueError(
                    f"Unsupported key type: cert={type(cert_public_key)}, key={type(key)}"
                )

            # Extract certificate info
            info = self.parse_certificate(cert_pem)
            info.is_valid = True
            return info

        except Exception as e:
            logger.error("[TLS-STORAGE] Certificate validation failed: %s", e)
            return CertificateInfo(
                subject="",
                issuer="",
                serial_number="",
                not_before=datetime.min,
                not_after=datetime.min,
                domains=[],
                is_valid=False,
                validation_error=str(e),
            )

    def parse_certificate(self, cert_pem: bytes) -> CertificateInfo:
        """Parse a PEM certificate and extract info."""
        try:
            cert = x509.load_pem_x509_certificate(cert_pem)

            # Extract subject CN
            subject_cn = ""
            try:
                cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                if cn_attrs:
                    subject_cn = cn_attrs[0].value
            except Exception as e:
                logger.debug("[TLS] Suppressed subject CN extraction error: %s", e)

            # Extract issuer CN
            issuer_cn = ""
            try:
                cn_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
                if cn_attrs:
                    issuer_cn = cn_attrs[0].value
            except Exception as e:
                logger.debug("[TLS] Suppressed issuer CN extraction error: %s", e)

            # Extract domains from SANs
            domains = []
            if subject_cn:
                domains.append(subject_cn)

            try:
                san_ext = cert.extensions.get_extension_for_oid(
                    x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
                )
                for name in san_ext.value:
                    if isinstance(name, x509.DNSName):
                        if name.value not in domains:
                            domains.append(name.value)
            except x509.ExtensionNotFound as e:
                logger.debug("[TLS] Suppressed SAN extension lookup: %s", e)

            return CertificateInfo(
                subject=subject_cn,
                issuer=issuer_cn,
                serial_number=format(cert.serial_number, "x"),
                not_before=cert.not_valid_before_utc.replace(tzinfo=None),
                not_after=cert.not_valid_after_utc.replace(tzinfo=None),
                domains=domains,
                is_valid=True,
            )

        except Exception as e:
            logger.error("[TLS-STORAGE] Failed to parse certificate: %s", e)
            return CertificateInfo(
                subject="",
                issuer="",
                serial_number="",
                not_before=datetime.min,
                not_after=datetime.min,
                domains=[],
                is_valid=False,
                validation_error=str(e),
            )

    def is_expiring_soon(self, days: int = 30) -> bool:
        """Check if certificate expires within N days."""
        info = self.get_certificate_info()
        if not info:
            return False
        return info.days_until_expiry() <= days

    def delete_certificate(self) -> bool:
        """Delete stored certificate and key."""
        try:
            for path in [
                self.cert_path,
                self.key_path,
                self.chain_path,
                self.fullchain_path,
            ]:
                if path.exists():
                    path.unlink()
                    logger.info("[TLS-STORAGE] Deleted %s", path)
            return True
        except Exception as e:
            logger.error("[TLS-STORAGE] Failed to delete certificate: %s", e)
            return False

    def save_acme_account(self, account_data: dict) -> bool:
        """Save ACME account data for renewal."""
        if not self.ensure_directory():
            return False

        try:
            account_json = json.dumps(account_data, indent=2)
            self.acme_account_path.write_text(account_json)
            os.chmod(self.acme_account_path, 0o600)
            logger.info("[TLS-STORAGE] ACME account saved to %s", self.acme_account_path)
            return True
        except Exception as e:
            logger.error("[TLS-STORAGE] Failed to save ACME account: %s", e)
            return False

    def load_acme_account(self) -> Optional[dict]:
        """Load ACME account data for renewal."""
        if not self.acme_account_path.exists():
            return None

        try:
            return json.loads(self.acme_account_path.read_text())
        except Exception as e:
            logger.error("[TLS-STORAGE] Failed to load ACME account: %s", e)
            return None

    def has_certificate(self) -> bool:
        """Check if a certificate exists."""
        return self.cert_path.exists() and self.key_path.exists()
