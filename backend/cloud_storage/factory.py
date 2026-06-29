"""
Cloud storage adapter factory.

One-way imports only: this module imports concrete adapters, but concrete
adapters import from ``cloud_storage.types`` — never from here. See bead
wlvxh for the topology rationale.
"""
import logging

from cloud_storage.types import CloudStorageAdapter

logger = logging.getLogger(__name__)

# Providers whose adapters are HARDENED — routed through the SSRF chokepoint
# (bead 0i2vt.8) and validated at the API boundary. OneDrive and Dropbox are
# DEFERRED to a v0.18.x follow-up (PO decision 2026-06-17): their adapters still
# make raw, un-validated outbound calls (see tests/test_ssrf_chokepoint_guard.py
# baseline), so the config-UI test/upload surfaces must NOT exercise them
# (SEC-4, bead enhancedchannelmanager-uomwu). This is the single source of truth
# for "which providers are wired this release"; tasks.dbas_backup imports it.
SUPPORTED_PROVIDERS = ("s3", "gdrive", "webdav")


def get_adapter(provider_type: str, credentials: dict) -> CloudStorageAdapter:
    """Factory function to create the appropriate cloud storage adapter.

    Args:
        provider_type: One of "s3", "gdrive", "onedrive", "dropbox", "webdav".
        credentials: Provider-specific configuration dict.

    Returns:
        An instance of the appropriate CloudStorageAdapter subclass.

    Raises:
        ValueError: If the provider type is unknown.
        ImportError: If required dependencies are not installed.
    """
    if provider_type == "s3":
        from cloud_storage.s3_adapter import S3Adapter
        return S3Adapter(credentials)
    elif provider_type == "gdrive":
        from cloud_storage.gdrive_adapter import GDriveAdapter
        return GDriveAdapter(credentials)
    elif provider_type == "onedrive":
        from cloud_storage.onedrive_adapter import OneDriveAdapter
        return OneDriveAdapter(credentials)
    elif provider_type == "dropbox":
        from cloud_storage.dropbox_adapter import DropboxAdapter
        return DropboxAdapter(credentials)
    elif provider_type == "webdav":
        from cloud_storage.webdav_adapter import WebDAVAdapter
        return WebDAVAdapter(credentials)
    else:
        raise ValueError(f"Unknown cloud storage provider: {provider_type}")
