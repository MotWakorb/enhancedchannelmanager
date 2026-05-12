"""
Cloud storage adapter framework for export distribution.
Supports S3, Google Drive, OneDrive, and Dropbox.
"""
from cloud_storage.factory import get_adapter
from cloud_storage.types import (
    CloudStorageAdapter,
    ConnectionTestResult,
    UploadResult,
)

__all__ = [
    "CloudStorageAdapter",
    "UploadResult",
    "ConnectionTestResult",
    "get_adapter",
]
