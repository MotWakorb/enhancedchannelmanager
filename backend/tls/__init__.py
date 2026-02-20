"""
TLS Certificate Management module.

Provides comprehensive TLS/SSL support including:
- Let's Encrypt automatic certificate issuance via ACME
- Manual certificate upload
- HTTP-01 and DNS-01 challenge handlers
- Automatic certificate renewal
"""

from .settings import (
    TLSSettings,
    get_tls_settings,
    save_tls_settings,
    clear_tls_settings_cache,
)
from .storage import CertificateStorage, CertificateInfo

# ACME client requires josepy - import conditionally
try:
    from .acme_client import ACMEClient, CertificateResult
except ImportError:
    ACMEClient = None  # type: ignore
    CertificateResult = None  # type: ignore

__all__ = [
    "TLSSettings",
    "get_tls_settings",
    "save_tls_settings",
    "clear_tls_settings_cache",
    "CertificateStorage",
    "CertificateInfo",
    "ACMEClient",
    "CertificateResult",
]
