"""
Google Drive cloud storage adapter via service account.

Security (bead 0i2vt.8):
* **SSRF chokepoint (.5).** The Google Drive API endpoint host
  (``www.googleapis.com``) is pre-resolved + denylist-validated via
  :func:`cloud_storage.upload_security.preresolve_endpoint` before any SDK call
  — the §9.2 row B4 option (b) "pre-resolve + validate the endpoint host before
  the SDK call" approach. The endpoint is a fixed Google host (not
  operator-supplied), so in practice this defends against a poisoned resolver
  pointing googleapis.com at an internal/metadata address: such a record is
  denied BEFORE any socket opens.
* **Event-loop safety.** The googleapiclient is synchronous; every blocking
  ``.execute()`` call is wrapped in :func:`asyncio.to_thread` so it runs on a
  worker thread and never blocks the event loop (HARD AC #1).
* **Stream from disk.** ``MediaFileUpload(..., resumable=True)`` streams the
  artifact from the on-disk path in chunks — it never reads the whole file
  into RAM (HARD AC #2).
* **Credential masking.** Logged/surfaced error strings pass through
  :func:`cloud_storage.upload_security.mask_secrets` so bearer tokens never
  reach the logs (HARD AC #5).
"""
import asyncio
import json
import logging
import time
from pathlib import Path

from cloud_storage.types import CloudStorageAdapter, UploadResult, ConnectionTestResult
from cloud_storage.upload_security import SSRFError, mask_secrets, preresolve_endpoint

logger = logging.getLogger(__name__)

# Fixed Google Drive API endpoint host — pre-validated through the .5 chokepoint
# so a poisoned resolver pointing it at an internal address is refused.
_GOOGLE_API_ENDPOINT = "https://www.googleapis.com/"


class GDriveAdapter(CloudStorageAdapter):
    """Adapter for Google Drive via service account.

    Required credentials:
        service_account_json: Full service account key JSON (as string or dict)
        folder_id: Target Google Drive folder ID
    """

    def __init__(self, credentials: dict):
        sa_json = credentials["service_account_json"]
        self.sa_info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        self.folder_id = credentials["folder_id"]

    def _validate_endpoint(self) -> None:
        """Pre-resolve + denylist-validate the Google API host (.5 chokepoint).

        Raises :class:`SSRFError` if the host resolves to a denied address —
        BEFORE the Drive service / any socket is built.
        """
        preresolve_endpoint(_GOOGLE_API_ENDPOINT)

    def _get_service(self):
        from google.oauth2 import service_account as sa_module  # ssrf-ok: endpoint pre-validated (.5)
        from googleapiclient.discovery import build  # ssrf-ok: endpoint pre-validated (.5)

        creds = sa_module.Credentials.from_service_account_info(
            self.sa_info, scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        return build("drive", "v3", credentials=creds)

    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        start = time.time()
        try:
            self._validate_endpoint()
            from googleapiclient.http import MediaFileUpload  # ssrf-ok: endpoint pre-validated (.5)

            service = self._get_service()
            filename = Path(remote_path).name
            file_size = local_path.stat().st_size

            # Check if file already exists in folder (blocking → executor).
            existing = await asyncio.to_thread(
                lambda: service.files().list(
                    q=f"name='{filename}' and '{self.folder_id}' in parents and trashed=false",
                    fields="files(id)",
                ).execute()
            )

            # resumable=True streams the artifact from the on-disk path in
            # chunks — never a whole-file read into RAM.
            media = MediaFileUpload(str(local_path), resumable=True)

            if existing.get("files"):
                file_id = existing["files"][0]["id"]
                result = await asyncio.to_thread(
                    lambda: service.files().update(
                        fileId=file_id, media_body=media
                    ).execute()
                )
            else:
                metadata = {"name": filename, "parents": [self.folder_id]}
                result = await asyncio.to_thread(
                    lambda: service.files().create(
                        body=metadata, media_body=media, fields="id,webViewLink"
                    ).execute()
                )

            duration_ms = int((time.time() - start) * 1000)
            remote_url = result.get("webViewLink", f"gdrive://{result.get('id', '')}")
            logger.info("[GDRIVE] Uploaded %s (%s bytes, %sms)", filename, file_size, duration_ms)
            return UploadResult(success=True, remote_url=remote_url, file_size=file_size, duration_ms=duration_ms)
        except SSRFError as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[GDRIVE] Upload refused by SSRF policy: %s", mask_secrets(str(e)))
            return UploadResult(success=False, error=mask_secrets(str(e)), duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[GDRIVE] Upload failed: %s", mask_secrets(str(e)))
            return UploadResult(success=False, error=mask_secrets(str(e)), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            self._validate_endpoint()
            service = self._get_service()
            folder = await asyncio.to_thread(
                lambda: service.files().get(fileId=self.folder_id, fields="id,name").execute()
            )
            return ConnectionTestResult(
                success=True,
                message=f"Connected to folder '{folder.get('name', self.folder_id)}'",
                provider_info={"folder_id": self.folder_id, "folder_name": folder.get("name", "")},
            )
        except SSRFError as e:
            logger.warning("[GDRIVE] Connection refused by SSRF policy: %s", mask_secrets(str(e)))
            return ConnectionTestResult(success=False, message=mask_secrets(str(e)))
        except Exception as e:
            logger.warning("[GDRIVE] Connection test failed: %s", mask_secrets(str(e)))
            return ConnectionTestResult(success=False, message=mask_secrets(str(e)))

    async def delete(self, remote_path: str) -> bool:
        try:
            self._validate_endpoint()
            service = self._get_service()
            filename = Path(remote_path).name
            results = await asyncio.to_thread(
                lambda: service.files().list(
                    q=f"name='{filename}' and '{self.folder_id}' in parents and trashed=false",
                    fields="files(id)",
                ).execute()
            )
            for f in results.get("files", []):
                await asyncio.to_thread(
                    lambda fid=f["id"]: service.files().delete(fileId=fid).execute()
                )
            logger.info("[GDRIVE] Deleted %s", filename)
            return True
        except Exception as e:
            logger.warning("[GDRIVE] Delete failed: %s", mask_secrets(str(e)))
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            self._validate_endpoint()
            service = self._get_service()
            results = await asyncio.to_thread(
                lambda: service.files().list(
                    q=f"'{self.folder_id}' in parents and trashed=false",
                    fields="files(name)",
                ).execute()
            )
            return [f["name"] for f in results.get("files", [])]
        except Exception as e:
            logger.warning("[GDRIVE] List files failed: %s", mask_secrets(str(e)))
            return []
