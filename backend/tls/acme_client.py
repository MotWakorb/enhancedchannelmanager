"""
ACME client for Let's Encrypt certificate management.

Implements the ACME protocol for automatic certificate issuance and renewal
using HTTP-01 or DNS-01 challenges.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding
from cryptography.x509.oid import NameOID
from josepy import JWKRSA, JWKECKey
import josepy as jose


logger = logging.getLogger(__name__)


# ACME directory URLs
LETSENCRYPT_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
LETSENCRYPT_PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"


@dataclass
class CertificateResult:
    """Result of a certificate request."""

    success: bool
    cert_pem: Optional[str] = None
    key_pem: Optional[str] = None
    chain_pem: Optional[str] = None
    fullchain_pem: Optional[str] = None
    expires_at: Optional[datetime] = None
    error: Optional[str] = None


@dataclass
class ChallengeInfo:
    """Information about a pending ACME challenge."""

    type: Literal["http-01", "dns-01"]
    token: str
    key_authorization: str
    domain: str
    # For HTTP-01
    url_path: Optional[str] = None
    # For DNS-01
    txt_record_name: Optional[str] = None
    txt_record_value: Optional[str] = None


class ACMEClient:
    """
    ACME client for Let's Encrypt certificate management.

    Supports HTTP-01 and DNS-01 challenges for domain validation.
    """

    def __init__(
        self,
        email: str,
        staging: bool = False,
        account_key_path: Optional[Path] = None,
    ):
        """
        Initialize ACME client.

        Args:
            email: Contact email for ACME account
            staging: Use Let's Encrypt staging environment
            account_key_path: Path to store/load account key
        """
        self.email = email
        self.directory_url = LETSENCRYPT_STAGING if staging else LETSENCRYPT_PRODUCTION
        self.account_key_path = account_key_path
        self.staging = staging

        # Will be populated during initialization
        self.directory: dict = {}
        self.account_key: Optional[JWKRSA] = None
        self.account_url: Optional[str] = None
        self.nonce: Optional[str] = None

        # Pending challenges (token -> ChallengeInfo)
        self._pending_challenges: dict[str, ChallengeInfo] = {}

    async def initialize(self) -> bool:
        """
        Initialize the ACME client.

        Fetches directory, loads/creates account key, and registers account.
        """
        try:
            # Fetch ACME directory
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.directory_url)
                resp.raise_for_status()
                self.directory = resp.json()
                logger.info(f"Fetched ACME directory from {self.directory_url}")

            # Load or create account key
            self.account_key = self._load_or_create_account_key()

            # Register or fetch existing account
            await self._register_account()

            return True

        except Exception as e:
            logger.error(f"Failed to initialize ACME client: {e}")
            return False

    def _load_or_create_account_key(self) -> JWKRSA:
        """Load existing account key or create a new one."""
        if self.account_key_path and self.account_key_path.exists():
            try:
                key_data = self.account_key_path.read_bytes()
                private_key = serialization.load_pem_private_key(key_data, password=None)
                logger.info("Loaded existing ACME account key")
                return JWKRSA(key=private_key)
            except Exception as e:
                logger.warning(f"Failed to load account key, creating new: {e}")

        # Generate new RSA key for ACME account
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )

        # Save if path provided
        if self.account_key_path:
            try:
                self.account_key_path.parent.mkdir(parents=True, exist_ok=True)
                key_pem = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                self.account_key_path.write_bytes(key_pem)
                os.chmod(self.account_key_path, 0o600)
                logger.info(f"Created and saved new ACME account key")
            except Exception as e:
                logger.warning(f"Failed to save account key: {e}")

        return JWKRSA(key=private_key)

    async def _get_nonce(self) -> str:
        """Get a fresh nonce from the ACME server."""
        async with httpx.AsyncClient() as client:
            resp = await client.head(self.directory["newNonce"])
            return resp.headers["Replay-Nonce"]

    def _sign_request(
        self,
        url: str,
        payload: Optional[dict],
        use_jwk: bool = False,
    ) -> dict:
        """
        Sign a request with the account key.

        Args:
            url: The URL being requested
            payload: The payload to sign (or None for POST-as-GET)
            use_jwk: Include full JWK instead of kid (for registration)
        """
        if payload is None:
            payload_b64 = jose.json_util.encode_b64jose(b"")
        elif payload == "":
            payload_b64 = ""
        else:
            payload_b64 = jose.json_util.encode_b64jose(
                json.dumps(payload).encode("utf-8")
            )

        protected = {
            "alg": "RS256",
            "nonce": self.nonce,
            "url": url,
        }

        if use_jwk:
            protected["jwk"] = self.account_key.public_key().fields_to_partial_json()
        else:
            protected["kid"] = self.account_url

        protected_b64 = jose.json_util.encode_b64jose(
            json.dumps(protected).encode("utf-8")
        )

        # Sign
        signature_input = f"{protected_b64}.{payload_b64}".encode("utf-8")
        signature = self.account_key.key.sign(
            signature_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        signature_b64 = jose.json_util.encode_b64jose(signature)

        return {
            "protected": protected_b64,
            "payload": payload_b64,
            "signature": signature_b64,
        }

    async def _acme_request(
        self,
        url: str,
        payload: Optional[dict] = None,
        use_jwk: bool = False,
    ) -> tuple[dict, dict]:
        """
        Make a signed ACME request.

        Returns:
            Tuple of (response_body, response_headers)
        """
        if self.nonce is None:
            self.nonce = await self._get_nonce()

        signed = self._sign_request(url, payload, use_jwk)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=signed,
                headers={"Content-Type": "application/jose+json"},
            )

            # Update nonce
            if "Replay-Nonce" in resp.headers:
                self.nonce = resp.headers["Replay-Nonce"]

            if resp.status_code >= 400:
                error = resp.json() if resp.content else {}
                raise Exception(
                    f"ACME request failed: {resp.status_code} - {error}"
                )

            body = resp.json() if resp.content else {}
            return body, dict(resp.headers)

    async def _register_account(self) -> None:
        """Register or fetch existing ACME account."""
        payload = {
            "termsOfServiceAgreed": True,
            "contact": [f"mailto:{self.email}"],
        }

        body, headers = await self._acme_request(
            self.directory["newAccount"],
            payload,
            use_jwk=True,
        )

        self.account_url = headers.get("Location")
        logger.info(f"ACME account registered/retrieved: {self.account_url}")

    async def request_certificate(
        self,
        domain: str,
        challenge_type: Literal["http-01", "dns-01"] = "http-01",
        key_type: Literal["rsa", "ec"] = "ec",
    ) -> CertificateResult:
        """
        Request a new certificate for a domain.

        Args:
            domain: The domain to get a certificate for
            challenge_type: Type of ACME challenge to use
            key_type: Type of key to generate (RSA or EC)

        Returns:
            CertificateResult with certificate or error
        """
        try:
            if not self.account_url:
                await self.initialize()

            # Step 1: Create new order
            logger.info(f"Creating certificate order for {domain}")
            order_payload = {
                "identifiers": [{"type": "dns", "value": domain}],
            }
            order, order_headers = await self._acme_request(
                self.directory["newOrder"],
                order_payload,
            )
            order_url = order_headers.get("Location")

            # Step 2: Get authorizations
            for auth_url in order["authorizations"]:
                auth, _ = await self._acme_request(auth_url, None)

                # Find the requested challenge type
                challenge = None
                for ch in auth["challenges"]:
                    if ch["type"] == challenge_type:
                        challenge = ch
                        break

                if not challenge:
                    return CertificateResult(
                        success=False,
                        error=f"Challenge type {challenge_type} not available",
                    )

                # Prepare challenge
                token = challenge["token"]
                thumbprint = self._get_thumbprint()
                key_authorization = f"{token}.{thumbprint}"

                challenge_info = ChallengeInfo(
                    type=challenge_type,
                    token=token,
                    key_authorization=key_authorization,
                    domain=domain,
                )

                if challenge_type == "http-01":
                    challenge_info.url_path = f"/.well-known/acme-challenge/{token}"
                elif challenge_type == "dns-01":
                    # DNS TXT record value is base64url(sha256(key_authorization))
                    digest = hashlib.sha256(key_authorization.encode()).digest()
                    txt_value = jose.json_util.encode_b64jose(digest)
                    challenge_info.txt_record_name = f"_acme-challenge.{domain}"
                    challenge_info.txt_record_value = txt_value

                # Store challenge for external handlers
                self._pending_challenges[token] = challenge_info

                logger.info(
                    f"Challenge prepared: {challenge_type} for {domain}, "
                    f"token={token}"
                )

                # Return challenge info for external handling
                # Caller must set up the challenge (HTTP endpoint or DNS record)
                # then call complete_challenge()

            return CertificateResult(
                success=False,
                error="Challenge pending - call complete_challenge() after setup",
            )

        except Exception as e:
            logger.error(f"Failed to request certificate: {e}")
            return CertificateResult(success=False, error=str(e))

    def get_pending_challenge(self, token: str) -> Optional[ChallengeInfo]:
        """Get a pending challenge by token."""
        return self._pending_challenges.get(token)

    def get_all_pending_challenges(self) -> list[ChallengeInfo]:
        """Get all pending challenges."""
        return list(self._pending_challenges.values())

    def get_http_challenge_response(self, token: str) -> Optional[str]:
        """Get the response to serve for an HTTP-01 challenge."""
        challenge = self._pending_challenges.get(token)
        if challenge and challenge.type == "http-01":
            return challenge.key_authorization
        return None

    async def complete_challenge(
        self,
        domain: str,
        challenge_type: Literal["http-01", "dns-01"] = "http-01",
        key_type: Literal["rsa", "ec"] = "ec",
    ) -> CertificateResult:
        """
        Complete the ACME challenge and finalize the certificate.

        This should be called after setting up the HTTP endpoint or DNS record.

        Args:
            domain: The domain being validated
            challenge_type: Type of challenge being used
            key_type: Type of key to generate for the certificate

        Returns:
            CertificateResult with certificate or error
        """
        try:
            if not self.account_url:
                await self.initialize()

            # Create new order (or resume if already created)
            order_payload = {
                "identifiers": [{"type": "dns", "value": domain}],
            }
            order, order_headers = await self._acme_request(
                self.directory["newOrder"],
                order_payload,
            )
            order_url = order_headers.get("Location")

            # Process authorizations
            for auth_url in order["authorizations"]:
                auth, _ = await self._acme_request(auth_url, None)

                if auth["status"] == "valid":
                    continue

                # Find the challenge
                challenge = None
                for ch in auth["challenges"]:
                    if ch["type"] == challenge_type:
                        challenge = ch
                        break

                if not challenge:
                    return CertificateResult(
                        success=False,
                        error=f"Challenge type {challenge_type} not available",
                    )

                # Respond to challenge (tell ACME server we're ready)
                logger.info(f"Responding to {challenge_type} challenge")
                await self._acme_request(challenge["url"], {})

                # Poll for authorization status
                for _ in range(30):  # Max 30 attempts
                    await asyncio.sleep(2)
                    auth, _ = await self._acme_request(auth_url, None)

                    if auth["status"] == "valid":
                        logger.info(f"Authorization valid for {domain}")
                        break
                    elif auth["status"] == "invalid":
                        # Get challenge error
                        for ch in auth["challenges"]:
                            if ch["type"] == challenge_type:
                                error = ch.get("error", {})
                                return CertificateResult(
                                    success=False,
                                    error=f"Challenge failed: {error.get('detail', 'Unknown error')}",
                                )
                        return CertificateResult(
                            success=False,
                            error="Authorization invalid",
                        )
                    elif auth["status"] == "pending":
                        continue
                else:
                    return CertificateResult(
                        success=False,
                        error="Authorization timeout",
                    )

            # Generate certificate key
            if key_type == "ec":
                cert_key = ec.generate_private_key(ec.SECP256R1())
            else:
                cert_key = rsa.generate_private_key(
                    public_exponent=65537,
                    key_size=2048,
                )

            # Create CSR
            csr = (
                x509.CertificateSigningRequestBuilder()
                .subject_name(
                    x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
                )
                .add_extension(
                    x509.SubjectAlternativeName([x509.DNSName(domain)]),
                    critical=False,
                )
                .sign(cert_key, hashes.SHA256())
            )

            csr_der = csr.public_bytes(serialization.Encoding.DER)
            csr_b64 = jose.json_util.encode_b64jose(csr_der)

            # Finalize order
            logger.info("Finalizing certificate order")
            finalize_payload = {"csr": csr_b64}
            order, _ = await self._acme_request(order["finalize"], finalize_payload)

            # Poll for certificate
            for _ in range(30):
                await asyncio.sleep(2)
                order, _ = await self._acme_request(order_url, None)

                if order["status"] == "valid":
                    break
                elif order["status"] == "invalid":
                    return CertificateResult(
                        success=False,
                        error="Order invalid",
                    )
            else:
                return CertificateResult(
                    success=False,
                    error="Order finalization timeout",
                )

            # Download certificate
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    order["certificate"],
                    headers={"Accept": "application/pem-certificate-chain"},
                )
                fullchain_pem = resp.text

            # Split into cert and chain
            certs = fullchain_pem.split("-----END CERTIFICATE-----")
            cert_pem = certs[0] + "-----END CERTIFICATE-----\n"
            chain_pem = "-----END CERTIFICATE-----".join(certs[1:]).strip()
            if chain_pem and not chain_pem.endswith("\n"):
                chain_pem += "\n"

            # Export private key
            key_pem = cert_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")

            # Parse certificate to get expiry
            cert = x509.load_pem_x509_certificate(cert_pem.encode())
            expires_at = cert.not_valid_after_utc.replace(tzinfo=None)

            # Clear pending challenges for this domain
            self._pending_challenges = {
                k: v
                for k, v in self._pending_challenges.items()
                if v.domain != domain
            }

            logger.info(f"Certificate issued for {domain}, expires {expires_at}")

            return CertificateResult(
                success=True,
                cert_pem=cert_pem,
                key_pem=key_pem,
                chain_pem=chain_pem if chain_pem else None,
                fullchain_pem=fullchain_pem,
                expires_at=expires_at,
            )

        except Exception as e:
            logger.error(f"Failed to complete certificate request: {e}")
            return CertificateResult(success=False, error=str(e))

    def _get_thumbprint(self) -> str:
        """Get the account key thumbprint for key authorization."""
        jwk_json = self.account_key.public_key().fields_to_partial_json()
        # JWK thumbprint per RFC 7638
        thumbprint_input = json.dumps(
            {k: jwk_json[k] for k in sorted(jwk_json.keys())},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        thumbprint = hashlib.sha256(thumbprint_input).digest()
        return jose.json_util.encode_b64jose(thumbprint)

    async def revoke_certificate(self, cert_pem: str) -> bool:
        """
        Revoke a certificate.

        Args:
            cert_pem: PEM-encoded certificate to revoke

        Returns:
            True if successful
        """
        try:
            if not self.account_url:
                await self.initialize()

            cert = x509.load_pem_x509_certificate(cert_pem.encode())
            cert_der = cert.public_bytes(serialization.Encoding.DER)
            cert_b64 = jose.json_util.encode_b64jose(cert_der)

            payload = {"certificate": cert_b64}
            await self._acme_request(self.directory["revokeCert"], payload)

            logger.info("Certificate revoked successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to revoke certificate: {e}")
            return False
