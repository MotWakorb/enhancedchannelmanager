"""
Cloud storage adapter framework for export distribution.
Supports S3, Google Drive, OneDrive, and Dropbox.
"""
from cloud_storage.base import (
    CloudStorageAdapter,
    UploadResult,
    ConnectionTestResult,
    get_adapter,
)

__all__ = [
    "CloudStorageAdapter",
    "UploadResult",
    "ConnectionTestResult",
    "get_adapter",
]
