"""
Google Drive cloud storage adapter via service account.
"""
import json
import logging
import time
from pathlib import Path

from cloud_storage.base import CloudStorageAdapter, UploadResult, ConnectionTestResult

logger = logging.getLogger(__name__)


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

    def _get_service(self):
        from google.oauth2 import service_account as sa_module
        from googleapiclient.discovery import build

        creds = sa_module.Credentials.from_service_account_info(
            self.sa_info, scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        return build("drive", "v3", credentials=creds)

    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        start = time.time()
        try:
            from googleapiclient.http import MediaFileUpload

            service = self._get_service()
            filename = Path(remote_path).name
            file_size = local_path.stat().st_size

            # Check if file already exists in folder
            existing = service.files().list(
                q=f"name='{filename}' and '{self.folder_id}' in parents and trashed=false",
                fields="files(id)",
            ).execute()

            media = MediaFileUpload(str(local_path), resumable=True)

            if existing.get("files"):
                # Update existing file
                file_id = existing["files"][0]["id"]
                result = service.files().update(
                    fileId=file_id, media_body=media
                ).execute()
            else:
                # Create new file
                metadata = {"name": filename, "parents": [self.folder_id]}
                result = service.files().create(
                    body=metadata, media_body=media, fields="id,webViewLink"
                ).execute()

            duration_ms = int((time.time() - start) * 1000)
            remote_url = result.get("webViewLink", f"gdrive://{result.get('id', '')}")
            logger.info("[GDRIVE] Uploaded %s (%s bytes, %sms)", filename, file_size, duration_ms)
            return UploadResult(success=True, remote_url=remote_url, file_size=file_size, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[GDRIVE] Upload failed: %s", e)
            return UploadResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            service = self._get_service()
            folder = service.files().get(fileId=self.folder_id, fields="id,name").execute()
            return ConnectionTestResult(
                success=True,
                message=f"Connected to folder '{folder.get('name', self.folder_id)}'",
                provider_info={"folder_id": self.folder_id, "folder_name": folder.get("name", "")},
            )
        except Exception as e:
            logger.warning("[GDRIVE] Connection test failed: %s", e)
            return ConnectionTestResult(success=False, message=str(e))

    async def delete(self, remote_path: str) -> bool:
        try:
            service = self._get_service()
            filename = Path(remote_path).name
            results = service.files().list(
                q=f"name='{filename}' and '{self.folder_id}' in parents and trashed=false",
                fields="files(id)",
            ).execute()
            for f in results.get("files", []):
                service.files().delete(fileId=f["id"]).execute()
            logger.info("[GDRIVE] Deleted %s", filename)
            return True
        except Exception as e:
            logger.warning("[GDRIVE] Delete failed: %s", e)
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            service = self._get_service()
            results = service.files().list(
                q=f"'{self.folder_id}' in parents and trashed=false",
                fields="files(name)",
            ).execute()
            return [f["name"] for f in results.get("files", [])]
        except Exception as e:
            logger.warning("[GDRIVE] List files failed: %s", e)
            return []
