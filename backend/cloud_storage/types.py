"""
Abstract base class and data types for cloud storage adapters.

Split out from the historical ``cloud_storage.base`` module so concrete
adapters can depend on the ABC + dataclasses without forming an import
cycle with the factory (which imports the concrete adapters). See bead
wlvxh for context.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

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
