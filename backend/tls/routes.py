"""
TLS API endpoints for certificate management.

Provides REST endpoints for:
- TLS configuration status
- Let's Encrypt certificate issuance (DNS-01 challenge)
- Manual certificate upload
- Certificate renewal

AUTHORIZATION (bead 9kwzp.11)
-----------------------------

This router carried NO route-level dependency on any of its thirteen routes.
The global ``auth_middleware`` in main.py establishes only that the caller is
authenticated (no ``/api/tls`` path is or was in ``AUTH_EXEMPT_PATHS``), so
every route was reachable by any authenticated non-admin AND by the static MCP
service principal, which ``auth.dependencies._build_mcp_service_principal``
stamps ``is_admin=True``. Three tiers now apply, per route:

* ``RequireHumanAdminForTLSMaterial`` — the certificate/key material and
  HTTPS-termination lifecycle, plus the settings read that emits masked DNS
  credentials. Admin required and the MCP principal refused.
* ``RequireHumanAdminForOutboundTest`` — ``POST /test-dns-provider`` only. It
  hands DNS credentials to the provider API and reports the upstream verdict
  back, which is the credential-oracle class of beads i4qrp / 9kwzp.6 /
  9kwzp.7, and its 403 body already names that shape.
* ``RequireAdminIfEnabled`` — the two status reads, which disclose no
  credential material. Admin required, MCP principal admitted, which is the
  inventory's default for anything outside the denied classes.

AUTH-DISABLED BEHAVIOUR (bead jy006, PO decision 2026-08-13)
------------------------------------------------------------

This paragraph used to read "all three no-op when ``require_auth`` is false or
setup is incomplete". That is no longer true of the first tier, and the
difference is the point of bead jy006.

* ``RequireHumanAdminForTLSMaterial`` carries ``enforce_when_auth_disabled``.
  On an instance that HAS an operator identity (a user row, or
  ``setup_complete``), all ten of its routes require a real human admin even
  while ``require_auth`` is false. Installing a caller-supplied private key is
  one of the three identity primitives ECM refuses anonymously in every mode,
  because the installed key becomes the instance's TLS identity and survives
  the operator turning authentication back on. On an instance with NO operator
  identity — a genuine first run, or a deliberately headless auth-disabled
  deployment that never created a user — the gate still no-ops, so nothing is
  locked out.
* ``RequireHumanAdminForOutboundTest`` (``POST /test-dns-provider``) and
  ``RequireAdminIfEnabled`` (the two status reads) are UNCHANGED: they still
  no-op whenever ``require_auth`` is false or setup is incomplete. The PO's
  decision gates the three identity primitives and leaves the rest of the
  auth-disabled surface open. Note the residual this leaves inside this router:
  ``GET /settings`` is refused on such an instance while
  ``POST /test-dns-provider``, which USES the DNS-provider credentials that
  route discloses in masked form, is not.

Every verdict is pinned in ``tests/test_admin_gate_inventory.py``,
``tests/routers/test_9kwzp11_tls_router_admin_gate.py`` and
``tests/routers/test_jy006_auth_disabled_identity_primitives.py``.
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from auth import (
    RequireAdminIfEnabled,
    RequireHumanAdminForOutboundTest,
    RequireHumanAdminForTLSMaterial,
)

from .settings import (
    get_tls_settings,
    save_tls_settings,
    TLSSettings,
    TLS_DIR,
)
from .storage import CertificateStorage
from .https_server import https_server_manager

# ACME client and DNS providers require josepy - import conditionally
try:
    from .acme_client import ACMEClient
    from .dns_providers import get_dns_provider, DNSProviderError
    from .renewal import renew_certificate
    _acme_available = True
except ImportError:
    ACMEClient = None  # type: ignore
    get_dns_provider = None  # type: ignore
    DNSProviderError = Exception
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
    # HTTPS server status
    https_server_running: bool = False


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
async def get_tls_status(_admin=RequireAdminIfEnabled):
    """
    Get current TLS configuration status.

    Returns the current TLS settings, certificate status, and expiry information.

    bead 9kwzp.11: the PLAIN admin tier, decided on its own merits. The response
    carries no credential material — subject, issuer and validity window are
    served to every TLS client anyway, and domain/port/running-state are
    configuration disclosure that an authenticated non-admin has no business
    reading but that the automation credential may. Contrast ``GET /settings``
    below, which does emit credential fragments and is human-admin.
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
        https_server_running=https_server_manager.is_running,
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
async def get_tls_settings_endpoint(_admin=RequireHumanAdminForTLSMaterial):
    """
    Get TLS settings (for settings form).

    Note: Sensitive fields like dns_api_token and AWS credentials are masked in the response.

    bead 9kwzp.11: human-admin rather than the plain admin tier the two status
    reads take. Masked is not absent — the response discloses the last four
    characters of ``dns_api_token``, ``aws_access_key_id`` and
    ``aws_secret_access_key``, and ``dns_zone_id`` and ``acme_email`` in clear.
    Withholding stored credential values from the automation credential is the
    posture bead 9ej7f established on GET /api/settings, and this is the same
    class of read.
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
async def configure_tls(
    request: TLSConfigureRequest,
    _admin=RequireHumanAdminForTLSMaterial,
):
    """
    Configure TLS settings.

    This updates the TLS configuration but does not request a certificate.
    Use /api/tls/request-cert to request a Let's Encrypt certificate.

    bead 9kwzp.11: this WRITES the DNS-provider credentials that bead 2owpi
    records as living in plaintext in /config/tls_settings.json, and its
    ``enabled`` flag starts or stops HTTPS termination as a side effect below.
    Both halves are the human-admin shape, for the same reason kgz3k denies
    this principal the settings blob.
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

    # Start or stop HTTPS server based on enabled state
    https_message = ""
    if request.enabled:
        storage = CertificateStorage(TLS_DIR)
        if storage.has_certificate():
            if not https_server_manager.is_running:
                success, error = await https_server_manager.start()
                if success:
                    https_message = f" HTTPS server started on port {settings.https_port}."
                else:
                    logger.warning("[TLS] Failed to start HTTPS server after settings update: %s", error)
                    https_message = " Warning: Failed to start HTTPS server. Check logs for details."
            else:
                https_message = " HTTPS server already running."
        else:
            https_message = " Certificate required before HTTPS server can start."
    else:
        if https_server_manager.is_running:
            await https_server_manager.stop()
            https_message = " HTTPS server stopped."

    return {"success": True, "message": f"TLS settings updated.{https_message}"}


@router.post("/request-cert", response_model=CertificateRequestResponse)
async def request_certificate(_admin=RequireHumanAdminForTLSMaterial):
    """
    Request a new certificate from Let's Encrypt using DNS-01 challenge.

    This initiates the ACME certificate issuance process.
    If a DNS provider (Cloudflare/Route53) is configured, the TXT record
    is created automatically. Otherwise, you must create the TXT record
    manually and call /api/tls/complete-challenge.

    bead 9kwzp.11: ACME issuance drives the stored DNS-provider credentials
    against the provider API, mints a private key, and writes both the key and
    the certificate to /config/tls/. Certificate-material lifecycle, hence the
    human-admin tier.
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

            # Start HTTPS server
            https_msg = ""
            if settings.enabled:
                success, error = await https_server_manager.start()
                if success:
                    https_msg = f" HTTPS server started on port {settings.https_port}."
                else:
                    logger.warning("[TLS] HTTPS server failed to start after cert request: %s", error)
                    https_msg = " Warning: HTTPS server failed to start. Check logs for details."

            return CertificateRequestResponse(
                success=True,
                message=f"Certificate issued successfully.{https_msg}",
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
                logger.debug("[TLS] Creating TXT record: %s = %s", challenge.txt_record_name, challenge.txt_record_value)
                record_id, zone_id = await provider.create_and_get_zone(
                    challenge.txt_record_name,
                    challenge.txt_record_value,
                )

                # Wait for DNS propagation
                logger.debug("[TLS] Waiting 30s for DNS propagation...")
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
                    logger.warning("[TLS] Failed to delete DNS record: %s", e)

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

                    # Start HTTPS server
                    https_msg = ""
                    if settings.enabled:
                        start_success, start_error = await https_server_manager.start()
                        if start_success:
                            https_msg = f" HTTPS server started on port {settings.https_port}."
                        else:
                            logger.warning("[TLS] HTTPS server failed to start: %s", start_error)
                            https_msg = " Warning: HTTPS server failed to start. Check logs for details."

                    return CertificateRequestResponse(
                        success=True,
                        message=f"Certificate issued successfully.{https_msg}",
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
        logger.error("[TLS] Certificate request failed: %s", e)
        return CertificateRequestResponse(
            success=False,
            message=f"Certificate request failed: {e}",
        )


@router.post("/complete-challenge", response_model=CertificateRequestResponse)
async def complete_dns_challenge(_admin=RequireHumanAdminForTLSMaterial):
    """
    Complete a pending DNS-01 challenge.

    Call this after you have created the required TXT record.

    bead 9kwzp.11: the second half of the issuance above, and it is the half
    that actually saves the certificate and key. Same tier for the same reason.
    """
    if not _acme_available:
        raise HTTPException(503, "ACME functionality not available (josepy not installed)")

    settings = get_tls_settings()

    # Verify DNS record exists
    logger.debug("[TLS] Verifying DNS record...")

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

            # Start HTTPS server
            https_msg = ""
            if settings.enabled:
                start_success, start_error = await https_server_manager.start()
                if start_success:
                    https_msg = f" HTTPS server started on port {settings.https_port}."
                else:
                    logger.warning("[TLS] HTTPS server failed to start: %s", start_error)
                    https_msg = " Warning: HTTPS server failed to start. Check logs for details."

            return CertificateRequestResponse(
                success=True,
                message=f"Certificate issued successfully.{https_msg}",
                cert_expires_at=result.expires_at.isoformat(),
            )
        else:
            return CertificateRequestResponse(
                success=False,
                message=f"Challenge failed: {result.error}",
            )

    except Exception as e:
        logger.error("[TLS] Challenge completion failed: %s", e)
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
    _admin=RequireHumanAdminForTLSMaterial,
):
    """
    Upload a certificate and private key manually.

    Upload PEM-encoded certificate and key files.
    Optionally upload a chain file for intermediate certificates.

    bead 9kwzp.11: this accepts CALLER-SUPPLIED certificate and private-key
    material, persists it, flips the instance to ``mode="manual"`` and starts
    the HTTPS server serving it. It is the sharpest route in the router — an
    attacker-supplied key pair becomes the operator's transport identity — and
    it previously carried no dependency at all.
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

        # Start HTTPS server
        https_msg = ""
        start_success, start_error = await https_server_manager.start()
        if start_success:
            https_msg = f" HTTPS server started on port {settings.https_port}."
        else:
            logger.warning("[TLS] HTTPS server failed to start: %s", start_error)
            https_msg = " Warning: HTTPS server failed to start. Check logs for details."

        return {
            "success": True,
            "message": f"Certificate uploaded successfully.{https_msg}",
            "subject": validation.subject,
            "issuer": validation.issuer,
            "expires_at": validation.not_after.isoformat(),
            "days_until_expiry": validation.days_until_expiry(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[TLS] Certificate upload failed: %s", e)
        raise HTTPException(500, f"Upload failed: {e}")


# ============================================================================
# Certificate Renewal
# ============================================================================


@router.post("/renew")
async def trigger_renewal(_admin=RequireHumanAdminForTLSMaterial):
    """
    Manually trigger certificate renewal.

    This will request a new certificate from Let's Encrypt
    using the configured settings.

    bead 9kwzp.11: renewal replaces the live key pair and restarts HTTPS with
    it, driving the stored DNS credentials to do so. Same class as issuance.
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
        # Restart HTTPS server to load new certificate
        https_msg = ""
        if https_server_manager.is_running:
            restart_success, restart_error = await https_server_manager.restart()
            if restart_success:
                https_msg = " HTTPS server restarted with new certificate."
            else:
                logger.warning("[TLS] HTTPS server restart failed after renewal: %s", restart_error)
                https_msg = " Warning: HTTPS server restart failed. Check logs for details."

        return {
            "success": True,
            "message": f"Certificate renewed successfully.{https_msg}",
            "expires_at": result.expires_at.isoformat(),
        }
    else:
        return {
            "success": False,
            "message": f"Renewal failed: {result.error}",
        }


# ============================================================================
# HTTPS Server Control
# ============================================================================


@router.post("/https/start")
async def start_https_server(_admin=RequireHumanAdminForTLSMaterial):
    """
    Start the HTTPS server.

    Starts the HTTPS server if TLS is enabled and a certificate exists.

    bead 9kwzp.11: the HTTPS lifecycle trio is availability of the operator's
    own transport security. The stop half is the direct denial of service and
    the start/restart halves are its recovery, so all three take one tier; a
    caller able to restart but not stop, or the reverse, would be an arbitrary
    split. No frontend or MCP caller drives any of the three today.
    """
    settings = get_tls_settings()

    if not settings.enabled:
        raise HTTPException(400, "TLS is not enabled")

    storage = CertificateStorage(TLS_DIR)
    if not storage.has_certificate():
        raise HTTPException(400, "No certificate found")

    if https_server_manager.is_running:
        return {"success": True, "message": "HTTPS server already running"}

    success, error = await https_server_manager.start()
    if success:
        return {"success": True, "message": f"HTTPS server started on port {settings.https_port}"}
    else:
        logger.warning("[TLS] Failed to start HTTPS server: %s", error)
        return {"success": False, "message": "Failed to start HTTPS server. Check logs for details."}


@router.post("/https/stop")
async def stop_https_server(_admin=RequireHumanAdminForTLSMaterial):
    """
    Stop the HTTPS server.

    Stops the HTTPS server. The HTTP server on port 6100 continues running.

    bead 9kwzp.11: the denial-of-service half of the trio above. Falling back
    to plaintext HTTP on 6100 is precisely the downgrade an attacker wants.
    """
    if not https_server_manager.is_running:
        return {"success": True, "message": "HTTPS server not running"}

    await https_server_manager.stop()
    return {"success": True, "message": "HTTPS server stopped"}


@router.post("/https/restart")
async def restart_https_server(_admin=RequireHumanAdminForTLSMaterial):
    """
    Restart the HTTPS server.

    Useful after certificate renewal or configuration changes.

    bead 9kwzp.11: same tier as start/stop — a restart is a stop with a
    reload, so gating it any weaker would reopen the availability hole.
    """
    settings = get_tls_settings()

    if not settings.enabled:
        raise HTTPException(400, "TLS is not enabled")

    storage = CertificateStorage(TLS_DIR)
    if not storage.has_certificate():
        raise HTTPException(400, "No certificate found")

    success, error = await https_server_manager.restart()
    if success:
        return {"success": True, "message": f"HTTPS server restarted on port {settings.https_port}"}
    else:
        logger.warning("[TLS] Failed to restart HTTPS server: %s", error)
        return {"success": False, "message": "Failed to restart HTTPS server. Check logs for details."}


@router.get("/https/status")
async def get_https_server_status(_admin=RequireAdminIfEnabled):
    """
    Get HTTPS server status.

    Returns whether the HTTPS server is running and on which port.

    bead 9kwzp.11: the PLAIN admin tier, like ``GET /status``. A running flag
    and a port number are not credential material, and the port is already
    observable to anyone who can reach the host.
    """
    return https_server_manager.get_status()


# ============================================================================
# Certificate Deletion
# ============================================================================


@router.delete("/certificate")
async def delete_certificate(_admin=RequireHumanAdminForTLSMaterial):
    """
    Delete the stored certificate and disable TLS.

    This removes the certificate and key files and disables TLS.

    bead 9kwzp.11: destroys the operator's certificate and key, stops HTTPS,
    and clears ``enabled``. Unlike the pipeline's rollback/restore-snapshot
    pair (which the inventory admits this principal to, because they undo a
    pipeline run against ECM's own rows), there is no undo here: recovering
    means a fresh ACME issuance or a re-upload.
    """
    storage = CertificateStorage(TLS_DIR)

    if not storage.has_certificate():
        raise HTTPException(404, "No certificate found")

    # Stop HTTPS server first
    https_msg = ""
    if https_server_manager.is_running:
        await https_server_manager.stop()
        https_msg = " HTTPS server stopped."

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

    return {"success": True, "message": f"Certificate deleted and TLS disabled.{https_msg}"}


# ============================================================================
# Testing Endpoints
# ============================================================================


@router.post("/test-dns-provider")
async def test_dns_provider(
    request: DNSProviderTestRequest,
    _admin=RequireHumanAdminForOutboundTest,
):
    """
    Test DNS provider credentials.

    Verifies that the API token is valid and can access the zone.
    For Cloudflare, provide api_token.
    For Route53, provide aws_access_key_id and aws_secret_access_key (or use IAM role).

    bead 9kwzp.11: the one route here that is the i4qrp shape verbatim — it
    hands credentials to an upstream and reports the verdict back, which is a
    credential-validity oracle, and it enumerates the operator's DNS zones on
    the way. It reuses ``RequireHumanAdminForOutboundTest`` rather than the TLS
    gate precisely so its 403 reads like the other eleven sinks.
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
            logger.warning("[TLS] DNS provider credential verification failed: %s", error)
            return {"success": False, "message": "Invalid credentials. Verify your API token and permissions."}

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
        raise HTTPException(400, "Invalid provider configuration")
    except DNSProviderError as e:
        logger.warning("[TLS] DNS provider test failed: %s", e)
        return {"success": False, "message": "DNS provider test failed. Check API token and zone configuration."}
    except Exception as e:
        logger.error("[TLS] DNS provider test unexpected error: %s", e)
        return {"success": False, "message": "DNS provider test failed unexpectedly. Check logs for details."}


