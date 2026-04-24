"""
S3-compatible cloud storage adapter.
Supports AWS S3, MinIO, and Backblaze B2.
"""
import logging
import time
from pathlib import Path

from cloud_storage.types import CloudStorageAdapter, UploadResult, ConnectionTestResult

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".m3u": "audio/x-mpegurl",
    ".xml": "application/xml",
}


class S3Adapter(CloudStorageAdapter):
    """Adapter for S3-compatible storage (AWS S3, MinIO, Backblaze B2).

    Required credentials:
        bucket_name: S3 bucket name
        access_key_id: AWS access key ID
        secret_access_key: AWS secret access key

    Optional credentials:
        endpoint_url: Custom endpoint for MinIO/B2 (omit for AWS)
        region: AWS region (default: us-east-1)
    """

    def __init__(self, credentials: dict):
        self.bucket = credentials["bucket_name"]
        self.access_key = credentials["access_key_id"]
        self.secret_key = credentials["secret_access_key"]
        self.endpoint_url = credentials.get("endpoint_url") or None
        self.region = credentials.get("region") or "us-east-1"

    def _get_client(self):
        import boto3
        kwargs = {
            "service_name": "s3",
            "aws_access_key_id": self.access_key,
            "aws_secret_access_key": self.secret_key,
            "region_name": self.region,
        }
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        return boto3.client(**kwargs)

    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        start = time.time()
        try:
            client = self._get_client()
            content_type = CONTENT_TYPES.get(local_path.suffix, "application/octet-stream")
            file_size = local_path.stat().st_size

            client.upload_file(
                str(local_path),
                self.bucket,
                remote_path,
                ExtraArgs={"ContentType": content_type},
            )

            duration_ms = int((time.time() - start) * 1000)
            remote_url = f"s3://{self.bucket}/{remote_path}"
            if self.endpoint_url:
                remote_url = f"{self.endpoint_url}/{self.bucket}/{remote_path}"

            logger.info("[S3] Uploaded %s to %s (%s bytes, %sms)", local_path.name, remote_path, file_size, duration_ms)
            return UploadResult(success=True, remote_url=remote_url, file_size=file_size, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[S3] Upload failed: %s", e)
            return UploadResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            client = self._get_client()
            client.head_bucket(Bucket=self.bucket)
            location = client.get_bucket_location(Bucket=self.bucket)
            region = location.get("LocationConstraint") or "us-east-1"
            return ConnectionTestResult(
                success=True,
                message=f"Connected to bucket '{self.bucket}'",
                provider_info={"bucket": self.bucket, "region": region},
            )
        except Exception as e:
            logger.warning("[S3] Connection test failed: %s", e)
            return ConnectionTestResult(success=False, message=str(e))

    async def delete(self, remote_path: str) -> bool:
        try:
            client = self._get_client()
            client.delete_object(Bucket=self.bucket, Key=remote_path)
            logger.info("[S3] Deleted %s from %s", remote_path, self.bucket)
            return True
        except Exception as e:
            logger.warning("[S3] Delete failed: %s", e)
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            client = self._get_client()
            response = client.list_objects_v2(Bucket=self.bucket, Prefix=remote_path)
            return [obj["Key"] for obj in response.get("Contents", [])]
        except Exception as e:
            logger.warning("[S3] List files failed: %s", e)
            return []
