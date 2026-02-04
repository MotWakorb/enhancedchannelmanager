"""
TLS API endpoints for certificate management.

Provides REST endpoints for:
- TLS configuration status
- Let's Encrypt certificate issuance (DNS-01 challenge)
- Manual certificate upload
- Certificate renewal
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException, UploadFile, File, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .settings import (
    get_tls_settings,
    save_tls_settings,
    TLSSettings,
    TLS_DIR,
)
from .storage import CertificateStorage
from .challenges import verify_dns_challenge

# ACME client and DNS providers require josepy - import conditionally
try:
    from .acme_client import ACMEClient
    from .dns_providers import get_dns_provider, DNSProviderError
    from .renewal import renewal_manager, renew_certificate
    _acme_available = True
except ImportError:
    ACMEClient = None  # type: ignore
    get_dns_provider = None  # type: ignore
    DNSProviderError = Exception
    renewal_manager = None  # type: ignore
    renew_certificate = None  # type: ignore
    _acme_available = False


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tls", tags=["TLS"])


# ============================================================================
# Request/Response Models
# ============================================================================


class TLSStatusResponse(BaseModel):
    """TLS configuration status."""

    enabled: bool
    mode: str  # "letsencrypt" | "manual" | "none"
    domain: Optional[str] = None
    https_port: int = 6143
    cert_issued_at: Optional[str] = None
    cert_expires_at: Optional[str] = None
    cert_subject: Optional[str] = None
    cert_issuer: Optional[str] = None
    days_until_expiry: Optional[int] = None
    auto_renew: bool = True
    last_renewal_attempt: Optional[str] = None
    last_renewal_error: Optional[str] = None
    has_certificate: bool = False
    certificate_valid: bool = False


class TLSConfigureRequest(BaseModel):
    """Request to configure TLS settings."""

    enabled: bool
    mode: Literal["letsencrypt", "manual"] = "letsencrypt"
    domain: str = ""
    https_port: int = 6143
    acme_email: str = ""
    use_staging: bool = False
    dns_provider: str = ""
    dns_api_token: str = ""  # Cloudflare API token
    dns_zone_id: str = ""
    # AWS Route53 credentials
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    auto_renew: bool = True
    renew_days_before_expiry: int = 30


class CertificateRequestResponse(BaseModel):
    """Response from certificate request."""

    success: bool
    message: str
    # For DNS-01 challenge (when manual DNS setup required)
    txt_record_name: Optional[str] = None
    txt_record_value: Optional[str] = None
    # On success
    cert_expires_at: Optional[str] = None


class DNSProviderTestRequest(BaseModel):
    """Request to test DNS provider credentials."""

    provider: str
    api_token: str = ""  # Cloudflare API token
    zone_id: str = ""
    domain: str = ""
    # AWS Route53 credentials
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"


# ============================================================================
# TLS Status Endpoints
# ============================================================================


@router.get("/status", response_model=TLSStatusResponse)
async def get_tls_status():
    """
    Get current TLS configuration status.

    Returns the current TLS settings, certificate status, and expiry information.
    """
    settings = get_tls_settings()
    storage = CertificateStorage(TLS_DIR)

    response = TLSStatusResponse(
        enabled=settings.enabled,
        mode=settings.mode if settings.enabled else "none",
        domain=settings.domain if settings.domain else None,
        https_port=settings.https_port,
        cert_issued_at=settings.cert_issued_at,
        cert_expires_at=settings.cert_expires_at,
        cert_subject=settings.cert_subject,
        cert_issuer=settings.cert_issuer,
        auto_renew=settings.auto_renew,
        last_renewal_attempt=settings.last_renewal_attempt,
        last_renewal_error=settings.last_renewal_error,
        has_certificate=storage.has_certificate(),
    )

    # Get certificate info if exists
    if storage.has_certificate():
        info = storage.get_certificate_info()
        if info and info.is_valid:
            response.certificate_valid = True
            response.days_until_expiry = info.days_until_expiry()
            if not response.cert_subject:
                response.cert_subject = info.subject
            if not response.cert_issuer:
                response.cert_issuer = info.issuer

    return response


@router.get("/settings", response_model=TLSSettings)
async def get_tls_settings_endpoint():
    """
    Get TLS settings (for settings form).

    Note: Sensitive fields like dns_api_token and AWS credentials are masked in the response.
    """
    settings = get_tls_settings()

    # Mask sensitive fields
    response = settings.model_copy()
    if response.dns_api_token:
        response.dns_api_token = "***" + response.dns_api_token[-4:]
    if response.aws_access_key_id:
        response.aws_access_key_id = "***" + response.aws_access_key_id[-4:]
    if response.aws_secret_access_key:
        response.aws_secret_access_key = "***" + response.aws_secret_access_key[-4:]

    return response


# ============================================================================
# TLS Configuration Endpoints
# ============================================================================


@router.post("/configure")
async def configure_tls(request: TLSConfigureRequest):
    """
    Configure TLS settings.

    This updates the TLS configuration but does not request a certificate.
    Use /api/tls/request-cert to request a Let's Encrypt certificate.
    """
    settings = get_tls_settings()

    # Update settings
    settings.enabled = request.enabled
    settings.mode = request.mode
    settings.domain = request.domain
    settings.https_port = request.https_port
    settings.acme_email = request.acme_email
    settings.use_staging = request.use_staging
    settings.dns_provider = request.dns_provider
    settings.auto_renew = request.auto_renew
    settings.renew_days_before_expiry = request.renew_days_before_expiry

    # Only update dns_api_token if not masked
    if request.dns_api_token and not request.dns_api_token.startswith("***"):
        settings.dns_api_token = request.dns_api_token

    if request.dns_zone_id:
        settings.dns_zone_id = request.dns_zone_id

    # AWS Route53 credentials - only update if not masked
    if request.aws_access_key_id and not request.aws_access_key_id.startswith("***"):
        settings.aws_access_key_id = request.aws_access_key_id
    if request.aws_secret_access_key and not request.aws_secret_access_key.startswith("***"):
        settings.aws_secret_access_key = request.aws_secret_access_key
    if request.aws_region:
        settings.aws_region = request.aws_region

    save_tls_settings(settings)

    return {"success": True, "message": "TLS settings updated"}


@router.post("/request-cert", response_model=CertificateRequestResponse)
async def request_certificate():
    """
    Request a new certificate from Let's Encrypt using DNS-01 challenge.

    This initiates the ACME certificate issuance process.
    If a DNS provider (Cloudflare/Route53) is configured, the TXT record
    is created automatically. Otherwise, you must create the TXT record
    manually and call /api/tls/complete-challenge.
    """
    if not _acme_available:
        raise HTTPException(503, "ACME functionality not available (josepy not installed)")

    settings = get_tls_settings()

    if not settings.enabled:
        raise HTTPException(400, "TLS is not enabled")

    if settings.mode != "letsencrypt":
        raise HTTPException(400, "TLS mode must be 'letsencrypt' for automatic certificates")

    if not settings.is_configured_for_letsencrypt():
        raise HTTPException(400, "Let's Encrypt settings are incomplete")

    # Initialize ACME client
    acme = ACMEClient(
        email=settings.acme_email,
        staging=settings.use_staging,
        account_key_path=Path(settings.acme_account_path),
    )

    try:
        if not await acme.initialize():
            return CertificateRequestResponse(
                success=False,
                message="Failed to initialize ACME client",
            )

        # Start certificate request (DNS-01 challenge)
        result = await acme.request_certificate(
            domain=settings.domain,
        )

        if result.success:
            # Certificate issued immediately (unlikely for first request)
            storage = CertificateStorage(TLS_DIR)
            storage.save_certificate(
                cert_pem=result.cert_pem,
                key_pem=result.key_pem,
                chain_pem=result.chain_pem,
            )

            # Update settings
            settings.cert_issued_at = datetime.now().isoformat()
            settings.cert_expires_at = result.expires_at.isoformat()
            save_tls_settings(settings)

            return CertificateRequestResponse(
                success=True,
                message="Certificate issued successfully",
                cert_expires_at=result.expires_at.isoformat(),
            )

        # Challenge pending - need to complete it
        challenges = acme.get_all_pending_challenges()
        if not challenges:
            return CertificateRequestResponse(
                success=False,
                message="No challenges available",
            )

        challenge = challenges[0]

        # Check if we have DNS provider configured for automatic handling
        has_cloudflare_creds = settings.dns_provider.lower() == "cloudflare" and settings.dns_api_token
        has_route53_creds = settings.dns_provider.lower() == "route53" and (
            (settings.aws_access_key_id and settings.aws_secret_access_key) or
            settings.dns_provider.lower() == "route53"  # IAM role auth
        )

        if settings.dns_provider and (has_cloudflare_creds or has_route53_creds):
            try:
                provider = get_dns_provider(
                    settings.dns_provider,
                    api_token=settings.dns_api_token,
                    zone_id=settings.dns_zone_id,
                    aws_access_key_id=settings.aws_access_key_id,
                    aws_secret_access_key=settings.aws_secret_access_key,
                    aws_region=settings.aws_region,
                )

                # Create TXT record
                logger.debug(f"Creating TXT record: {challenge.txt_record_name} = {challenge.txt_record_value}")
                record_id, zone_id = await provider.create_and_get_zone(
                    challenge.txt_record_name,
                    challenge.txt_record_value,
                )

                # Wait for DNS propagation
                logger.debug("Waiting 30s for DNS propagation...")
                await asyncio.sleep(30)

                # Complete challenge
                result = await acme.complete_challenge(
                    domain=settings.domain,
                )

                # Clean up DNS record
                try:
                    provider.zone_id = zone_id
                    await provider.delete_txt_record(record_id)
                except Exception as e:
                    logger.warning(f"Failed to delete DNS record: {e}")

                if result.success:
                    # Save certificate
                    storage = CertificateStorage(TLS_DIR)
                    storage.save_certificate(
                        cert_pem=result.cert_pem,
                        key_pem=result.key_pem,
                        chain_pem=result.chain_pem,
                    )

                    # Update settings
                    settings.cert_issued_at = datetime.now().isoformat()
                    settings.cert_expires_at = result.expires_at.isoformat()
                    info = storage.get_certificate_info()
                    if info:
                        settings.cert_subject = info.subject
                        settings.cert_issuer = info.issuer
                    save_tls_settings(settings)

                    return CertificateRequestResponse(
                        success=True,
                        message="Certificate issued successfully",
                        cert_expires_at=result.expires_at.isoformat(),
                    )
                else:
                    return CertificateRequestResponse(
                        success=False,
                        message=f"Challenge failed: {result.error}",
                    )

            except DNSProviderError as e:
                return CertificateRequestResponse(
                    success=False,
                    message=f"DNS provider error: {e}",
                )

        else:
            # Return challenge info for manual DNS setup
            return CertificateRequestResponse(
                success=False,
                message="DNS-01 challenge pending. Create the TXT record and call /api/tls/complete-challenge",
                txt_record_name=challenge.txt_record_name,
                txt_record_value=challenge.txt_record_value,
            )

    except Exception as e:
        logger.error(f"Certificate request failed: {e}")
        return CertificateRequestResponse(
            success=False,
            message=f"Certificate request failed: {e}",
        )


@router.post("/complete-challenge", response_model=CertificateRequestResponse)
async def complete_dns_challenge():
    """
    Complete a pending DNS-01 challenge.

    Call this after you have created the required TXT record.
    """
    if not _acme_available:
        raise HTTPException(503, "ACME functionality not available (josepy not installed)")

    settings = get_tls_settings()

    # Verify DNS record exists
    logger.debug("Verifying DNS record...")

    # Initialize ACME client
    acme = ACMEClient(
        email=settings.acme_email,
        staging=settings.use_staging,
        account_key_path=Path(settings.acme_account_path),
    )

    try:
        if not await acme.initialize():
            return CertificateRequestResponse(
                success=False,
                message="Failed to initialize ACME client",
            )

        result = await acme.complete_challenge(
            domain=settings.domain,
        )

        if result.success:
            # Save certificate
            storage = CertificateStorage(TLS_DIR)
            storage.save_certificate(
                cert_pem=result.cert_pem,
                key_pem=result.key_pem,
                chain_pem=result.chain_pem,
            )

            # Update settings
            settings.cert_issued_at = datetime.now().isoformat()
            settings.cert_expires_at = result.expires_at.isoformat()
            info = storage.get_certificate_info()
            if info:
                settings.cert_subject = info.subject
                settings.cert_issuer = info.issuer
            save_tls_settings(settings)

            return CertificateRequestResponse(
                success=True,
                message="Certificate issued successfully",
                cert_expires_at=result.expires_at.isoformat(),
            )
        else:
            return CertificateRequestResponse(
                success=False,
                message=f"Challenge failed: {result.error}",
            )

    except Exception as e:
        logger.error(f"Challenge completion failed: {e}")
        return CertificateRequestResponse(
            success=False,
            message=f"Challenge failed: {e}",
        )


# ============================================================================
# Manual Certificate Upload
# ============================================================================


@router.post("/upload-cert")
async def upload_certificate(
    cert_file: UploadFile = File(...),
    key_file: UploadFile = File(...),
    chain_file: UploadFile = File(None),
):
    """
    Upload a certificate and private key manually.

    Upload PEM-encoded certificate and key files.
    Optionally upload a chain file for intermediate certificates.
    """
    try:
        cert_content = await cert_file.read()
        key_content = await key_file.read()
        chain_content = await chain_file.read() if chain_file else None

        storage = CertificateStorage(TLS_DIR)

        # Validate the certificate/key pair
        validation = storage.validate_pair(cert_content, key_content)
        if not validation.is_valid:
            raise HTTPException(
                400,
                f"Invalid certificate/key pair: {validation.validation_error}",
            )

        # Save certificate
        if not storage.save_certificate(cert_content, key_content, chain_content):
            raise HTTPException(500, "Failed to save certificate")

        # Update settings
        settings = get_tls_settings()
        settings.enabled = True
        settings.mode = "manual"
        settings.cert_issued_at = validation.not_before.isoformat()
        settings.cert_expires_at = validation.not_after.isoformat()
        settings.cert_subject = validation.subject
        settings.cert_issuer = validation.issuer
        if validation.domains:
            settings.domain = validation.domains[0]
        save_tls_settings(settings)

        return {
            "success": True,
            "message": "Certificate uploaded successfully",
            "subject": validation.subject,
            "issuer": validation.issuer,
            "expires_at": validation.not_after.isoformat(),
            "days_until_expiry": validation.days_until_expiry(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Certificate upload failed: {e}")
        raise HTTPException(500, f"Upload failed: {e}")


# ============================================================================
# Certificate Renewal
# ============================================================================


@router.post("/renew")
async def trigger_renewal():
    """
    Manually trigger certificate renewal.

    This will request a new certificate from Let's Encrypt
    using the configured settings.
    """
    if not _acme_available:
        raise HTTPException(503, "ACME functionality not available (josepy not installed)")

    settings = get_tls_settings()

    if not settings.enabled:
        raise HTTPException(400, "TLS is not enabled")

    if settings.mode != "letsencrypt":
        raise HTTPException(400, "Manual certificates cannot be auto-renewed")

    result = await renew_certificate()

    if result.success:
        return {
            "success": True,
            "message": "Certificate renewed successfully",
            "expires_at": result.expires_at.isoformat(),
        }
    else:
        return {
            "success": False,
            "message": f"Renewal failed: {result.error}",
        }


# ============================================================================
# Certificate Deletion
# ============================================================================


@router.delete("/certificate")
async def delete_certificate():
    """
    Delete the stored certificate and disable TLS.

    This removes the certificate and key files and disables TLS.
    """
    storage = CertificateStorage(TLS_DIR)

    if not storage.has_certificate():
        raise HTTPException(404, "No certificate found")

    if not storage.delete_certificate():
        raise HTTPException(500, "Failed to delete certificate")

    # Update settings
    settings = get_tls_settings()
    settings.enabled = False
    settings.cert_issued_at = None
    settings.cert_expires_at = None
    settings.cert_subject = None
    settings.cert_issuer = None
    save_tls_settings(settings)

    return {"success": True, "message": "Certificate deleted and TLS disabled"}


# ============================================================================
# Testing Endpoints
# ============================================================================


@router.post("/test-dns-provider")
async def test_dns_provider(request: DNSProviderTestRequest):
    """
    Test DNS provider credentials.

    Verifies that the API token is valid and can access the zone.
    For Cloudflare, provide api_token.
    For Route53, provide aws_access_key_id and aws_secret_access_key (or use IAM role).
    """
    if not _acme_available or get_dns_provider is None:
        raise HTTPException(503, "DNS provider functionality not available (josepy not installed)")

    try:
        provider = get_dns_provider(
            request.provider,
            api_token=request.api_token,
            zone_id=request.zone_id,
            aws_access_key_id=request.aws_access_key_id,
            aws_secret_access_key=request.aws_secret_access_key,
            aws_region=request.aws_region,
        )

        # Verify credentials
        valid, error = await provider.verify_credentials()
        if not valid:
            return {"success": False, "message": f"Invalid credentials: {error}"}

        # Try to get zone if domain provided
        if request.domain:
            zone_id = await provider.get_zone_id(request.domain)
            if zone_id:
                return {
                    "success": True,
                    "message": f"Credentials valid. Found zone: {zone_id}",
                    "zone_id": zone_id,
                }
            else:
                return {
                    "success": False,
                    "message": f"Credentials valid but zone not found for {request.domain}",
                }

        return {"success": True, "message": "Credentials valid"}

    except ValueError as e:
        raise HTTPException(400, str(e))
    except DNSProviderError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Test failed: {e}"}


