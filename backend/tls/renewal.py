"""
Automatic certificate renewal for Let's Encrypt certificates.

Provides a background task that checks certificate expiry and
automatically renews certificates before they expire.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Awaitable

from .settings import get_tls_settings, save_tls_settings, TLS_DIR
from .storage import CertificateStorage

# ACME client and DNS providers require josepy - import conditionally
try:
    from .acme_client import ACMEClient, CertificateResult
    from .dns_providers import get_dns_provider
    _acme_available = True
except ImportError:
    _acme_available = False

    # Define a placeholder for CertificateResult when ACME is not available
    @dataclass
    class CertificateResult:
        success: bool = False
        cert_pem: Optional[str] = None
        key_pem: Optional[str] = None
        chain_pem: Optional[str] = None
        fullchain_pem: Optional[str] = None
        expires_at: Optional[datetime] = None
        error: Optional[str] = None


logger = logging.getLogger(__name__)


# Callback for when certificate is renewed (e.g., to restart server)
_renewal_callback: Optional[Callable[[], Awaitable[None]]] = None


def set_renewal_callback(callback: Callable[[], Awaitable[None]]) -> None:
    """
    Set a callback to be called after successful renewal.

    This can be used to trigger a server restart to load the new certificate.

    Args:
        callback: Async function to call after renewal
    """
    global _renewal_callback
    _renewal_callback = callback


async def check_and_renew_certificate() -> tuple[bool, Optional[str]]:
    """
    Check if certificate needs renewal and renew if necessary.

    Returns:
        Tuple of (renewed, error_message)
    """
    settings = get_tls_settings()

    # Check if TLS is enabled and configured for Let's Encrypt
    if not settings.enabled:
        return False, None

    if settings.mode != "letsencrypt":
        return False, None

    if not settings.auto_renew:
        return False, None

    # Check if certificate exists and needs renewal
    storage = CertificateStorage(TLS_DIR)

    if not storage.has_certificate():
        logger.info("[TLS-RENEWAL] No certificate found, skipping renewal check")
        return False, None

    info = storage.get_certificate_info()
    if not info or not info.is_valid:
        logger.warning("[TLS-RENEWAL] Invalid certificate, skipping renewal")
        return False, "Invalid certificate"

    days_left = info.days_until_expiry()
    logger.info("[TLS-RENEWAL] Certificate expires in %s days", days_left)

    if days_left > settings.renew_days_before_expiry:
        logger.debug(
            "[TLS-RENEWAL] Certificate renewal not needed yet (%s days left, threshold is %s)",
            days_left, settings.renew_days_before_expiry,
        )
        return False, None

    logger.info(
        "[TLS-RENEWAL] Certificate expires in %s days, initiating renewal (threshold: %s days)",
        days_left, settings.renew_days_before_expiry,
    )

    # Attempt renewal
    result = await renew_certificate()

    if result.success:
        logger.info("[TLS-RENEWAL] Certificate renewed successfully")

        # Call renewal callback if set
        if _renewal_callback:
            try:
                await _renewal_callback()
            except Exception as e:
                logger.error("[TLS-RENEWAL] Renewal callback failed: %s", e)

        return True, None
    else:
        logger.error("[TLS-RENEWAL] Certificate renewal failed: %s", result.error)
        return False, result.error


async def renew_certificate() -> CertificateResult:
    """
    Renew the certificate using the stored settings.

    Returns:
        CertificateResult with new certificate or error
    """
    if not _acme_available:
        return CertificateResult(
            success=False,
            error="ACME functionality not available (josepy not installed)",
        )

    settings = get_tls_settings()

    if not settings.is_configured_for_letsencrypt():
        return CertificateResult(
            success=False,
            error="Let's Encrypt not configured",
        )

    # Update last renewal attempt
    settings.last_renewal_attempt = datetime.now().isoformat()
    settings.last_renewal_error = None
    save_tls_settings(settings)

    try:
        # Initialize ACME client
        acme = ACMEClient(
            email=settings.acme_email,
            staging=settings.use_staging,
            account_key_path=Path(settings.acme_account_path),
        )

        if not await acme.initialize():
            error = "Failed to initialize ACME client"
            settings.last_renewal_error = error
            save_tls_settings(settings)
            return CertificateResult(success=False, error=error)

        # Request new certificate (DNS-01 challenge)
        result = await acme.request_certificate(
            domain=settings.domain,
        )

        if not result.success and "Challenge pending" in (result.error or ""):
            # Set up DNS-01 challenge
            challenges = acme.get_all_pending_challenges()
            if not challenges:
                error = "No challenges returned"
                settings.last_renewal_error = error
                save_tls_settings(settings)
                return CertificateResult(success=False, error=error)

            challenge = challenges[0]

            # Use DNS provider to create TXT record
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
                record_id, zone_id = await provider.create_and_get_zone(
                    challenge.txt_record_name,
                    challenge.txt_record_value,
                )

                # Wait for DNS propagation
                logger.debug("[TLS-RENEWAL] Waiting for DNS propagation...")
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
                    logger.warning("[TLS-RENEWAL] Failed to delete DNS record: %s", e)

            except Exception as e:
                error = f"DNS challenge failed: {e}"
                settings.last_renewal_error = error
                save_tls_settings(settings)
                return CertificateResult(success=False, error=error)

        if result.success:
            # Save the new certificate
            storage = CertificateStorage(TLS_DIR)
            saved = storage.save_certificate(
                cert_pem=result.cert_pem,
                key_pem=result.key_pem,
                chain_pem=result.chain_pem,
            )

            if not saved:
                error = "Failed to save renewed certificate"
                settings.last_renewal_error = error
                save_tls_settings(settings)
                return CertificateResult(success=False, error=error)

            # Update settings with new cert info
            settings.cert_issued_at = datetime.now().isoformat()
            settings.cert_expires_at = result.expires_at.isoformat()
            settings.last_renewal_error = None

            # Get cert info for subject/issuer
            info = storage.get_certificate_info()
            if info:
                settings.cert_subject = info.subject
                settings.cert_issuer = info.issuer

            save_tls_settings(settings)
            return result
        else:
            settings.last_renewal_error = result.error
            save_tls_settings(settings)
            return result

    except Exception as e:
        error = f"Renewal failed: {e}"
        logger.error("[TLS-RENEWAL] %s", error)
        settings.last_renewal_error = error
        save_tls_settings(settings)
        return CertificateResult(success=False, error=error)


async def certificate_renewal_task(check_interval: int = 86400) -> None:
    """
    Background task that periodically checks and renews certificates.

    This should be started when the application starts and runs
    continuously in the background.

    Args:
        check_interval: Interval between checks in seconds (default: 24 hours)
    """
    logger.info(
        "[TLS-RENEWAL] Certificate renewal task started (checking every %s seconds)",
        check_interval,
    )

    while True:
        try:
            renewed, error = await check_and_renew_certificate()

            if renewed:
                logger.info("[TLS-RENEWAL] Certificate was renewed by background task")
            elif error:
                logger.warning("[TLS-RENEWAL] Certificate renewal check failed: %s", error)

        except asyncio.CancelledError:
            logger.info("[TLS-RENEWAL] Certificate renewal task cancelled")
            break
        except Exception as e:
            logger.exception("[TLS-RENEWAL] Error in certificate renewal task: %s", e)

        # Wait before next check
        await asyncio.sleep(check_interval)


class CertificateRenewalManager:
    """
    Manager for the certificate renewal background task.

    Provides methods to start, stop, and monitor the renewal task.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._check_interval: int = 86400  # 24 hours

    @property
    def is_running(self) -> bool:
        """Check if the renewal task is running."""
        return self._task is not None and not self._task.done()

    def start(self, check_interval: int = 86400) -> None:
        """
        Start the certificate renewal background task.

        Args:
            check_interval: Interval between checks in seconds
        """
        if self.is_running:
            logger.warning("[TLS-RENEWAL] Renewal task already running")
            return

        self._check_interval = check_interval
        self._task = asyncio.create_task(
            certificate_renewal_task(check_interval)
        )
        logger.info("[TLS-RENEWAL] Certificate renewal manager started")

    def stop(self) -> None:
        """Stop the certificate renewal background task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("[TLS-RENEWAL] Certificate renewal manager stopped")

    async def trigger_renewal(self) -> CertificateResult:
        """Manually trigger a certificate renewal."""
        return await renew_certificate()


# Global renewal manager instance
renewal_manager = CertificateRenewalManager()
