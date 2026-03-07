"""
Abstract base class and data types for cloud storage adapters.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    success: bool
    remote_url: str = ""
    file_size: int = 0
    duration_ms: int = 0
    error: str = ""


@dataclass
class ConnectionTestResult:
    success: bool
    message: str = ""
    provider_info: dict = field(default_factory=dict)


class CloudStorageAdapter(ABC):
    """Abstract base class for cloud storage providers."""

    @abstractmethod
    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        """Upload a local file to the cloud storage.

        Args:
            local_path: Path to the local file.
            remote_path: Destination path in the cloud storage.

        Returns:
            UploadResult with success status and details.
        """

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Test the connection to the cloud storage.

        Returns:
            ConnectionTestResult with success status and provider info.
        """

    @abstractmethod
    async def delete(self, remote_path: str) -> bool:
        """Delete a file from cloud storage.

        Args:
            remote_path: Path to the file in cloud storage.

        Returns:
            True if deleted successfully, False otherwise.
        """

    @abstractmethod
    async def list_files(self, remote_path: str = "") -> list[str]:
        """List files in a cloud storage path.

        Args:
            remote_path: Directory path to list.

        Returns:
            List of file names/paths.
        """


def get_adapter(provider_type: str, credentials: dict) -> CloudStorageAdapter:
    """Factory function to create the appropriate cloud storage adapter.

    Args:
        provider_type: One of "s3", "gdrive", "onedrive", "dropbox".
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
    else:
        raise ValueError(f"Unknown cloud storage provider: {provider_type}")
