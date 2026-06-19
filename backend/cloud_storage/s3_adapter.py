"""
S3-compatible cloud storage adapter.
Supports AWS S3, MinIO, and Backblaze B2.

Security (bead 0i2vt.8):
* **SSRF chokepoint (.5).** A custom ``endpoint_url`` (MinIO/B2/self-hosted) is
  pre-resolved + denylist-validated via
  :func:`cloud_storage.upload_security.preresolve_endpoint` BEFORE the boto3
  client is constructed — the §9.2 row B4 option (b) "pre-resolve + validate the
  endpoint host before the SDK call" approach. A custom endpoint pointed at
  ``169.254.169.254`` (the cloud-metadata endpoint) raises
  :class:`security.ssrf.SSRFError` before any socket opens. AWS's own regional
  endpoints (no custom ``endpoint_url``) are public AWS infrastructure and are
  not operator-influenceable, so they are not pre-resolved.
* **Event-loop safety.** boto3 is synchronous; every blocking SDK call
  (``upload_file``, ``head_bucket`` …) is wrapped in :func:`asyncio.to_thread`
  so it runs on a worker thread, never blocking the event loop (HARD AC #1).
* **Stream from disk.** ``upload_file`` streams the artifact from the on-disk
  path — it never reads the whole file into RAM (HARD AC #2).
* **Credential masking.** Logged/surfaced error strings are passed through
  :func:`cloud_storage.upload_security.mask_secrets` so access keys, secret
  keys, and X-Amz-Signature query strings never reach the logs (HARD AC #5).
"""
import asyncio
import logging
import time
from pathlib import Path

from cloud_storage.types import CloudStorageAdapter, UploadResult, ConnectionTestResult
from cloud_storage.upload_security import SSRFError, mask_secrets, preresolve_endpoint

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".m3u": "audio/x-mpegurl",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".sha256": "text/plain",
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

    def _validate_endpoint(self) -> None:
        """Pre-resolve + denylist-validate a custom endpoint (.5 chokepoint).

        No-op for the default AWS endpoint (no operator-supplied URL). Raises
        :class:`SSRFError` if a custom endpoint host is denied — BEFORE the
        boto3 client is built (so before any socket opens).
        """
        if self.endpoint_url:
            preresolve_endpoint(self.endpoint_url)

    def _get_client(self):
        import boto3  # ssrf-ok: endpoint pre-validated via preresolve_endpoint (.5)
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
            # SSRF guard FIRST — refuse a denied custom endpoint before any
            # client/socket is created.
            self._validate_endpoint()
            client = self._get_client()
            content_type = CONTENT_TYPES.get(local_path.suffix, "application/octet-stream")
            file_size = local_path.stat().st_size

            # Executor-wrap the blocking boto3 upload — boto3 streams the file
            # from the path (no whole-file read), and to_thread keeps the call
            # off the event loop.
            await asyncio.to_thread(
                client.upload_file,
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
        except SSRFError as e:
            duration_ms = int((time.time() - start) * 1000)
            # Log only safe fields (provider + remote path); the masked detail
            # is surfaced via UploadResult.error (a non-logging sink) so no
            # credential-derived string reaches the logger.
            logger.warning("[S3] Upload to bucket %s refused by SSRF policy", self.bucket)
            return UploadResult(success=False, error=mask_secrets(str(e)), duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[S3] Upload to bucket %s path %s failed", self.bucket, remote_path)
            return UploadResult(success=False, error=mask_secrets(str(e)), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            self._validate_endpoint()
            client = self._get_client()
            await asyncio.to_thread(client.head_bucket, Bucket=self.bucket)
            location = await asyncio.to_thread(client.get_bucket_location, Bucket=self.bucket)
            region = location.get("LocationConstraint") or "us-east-1"
            return ConnectionTestResult(
                success=True,
                message=f"Connected to bucket '{self.bucket}'",
                provider_info={"bucket": self.bucket, "region": region},
            )
        except SSRFError as e:
            logger.warning("[S3] Connection to bucket %s refused by SSRF policy", self.bucket)
            return ConnectionTestResult(success=False, message=mask_secrets(str(e)))
        except Exception as e:
            logger.warning("[S3] Connection test to bucket %s failed", self.bucket)
            return ConnectionTestResult(success=False, message=mask_secrets(str(e)))

    async def delete(self, remote_path: str) -> bool:
        try:
            self._validate_endpoint()
            client = self._get_client()
            await asyncio.to_thread(client.delete_object, Bucket=self.bucket, Key=remote_path)
            logger.info("[S3] Deleted %s from %s", remote_path, self.bucket)
            return True
        except Exception:
            logger.warning("[S3] Delete of %s from bucket %s failed", remote_path, self.bucket)
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            self._validate_endpoint()
            client = self._get_client()
            # list_objects_v2 caps a single response at 1000 keys. Follow the
            # ContinuationToken to the end so callers (e.g. retention pruning,
            # bead 0i2vt.9) see the FULL keyset, not just the first page.
            keys: list[str] = []
            token = None
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": remote_path}
                if token:
                    kwargs["ContinuationToken"] = token
                response = await asyncio.to_thread(client.list_objects_v2, **kwargs)
                keys.extend(obj["Key"] for obj in response.get("Contents", []))
                if not response.get("IsTruncated"):
                    break
                token = response.get("NextContinuationToken")
                if not token:
                    break
            return keys
        except Exception:
            logger.warning("[S3] List files under prefix %s in bucket %s failed", remote_path, self.bucket)
            return []
