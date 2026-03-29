"""
Dropbox cloud storage adapter.
"""
import logging
import time
from pathlib import Path

from cloud_storage.base import CloudStorageAdapter, UploadResult, ConnectionTestResult

logger = logging.getLogger(__name__)


class DropboxAdapter(CloudStorageAdapter):
    """Adapter for Dropbox.

    Required credentials:
        access_token: Dropbox access token (long-lived or from refresh)

    Optional credentials:
        app_key: Dropbox app key (for refresh token flow)
        app_secret: Dropbox app secret (for refresh token flow)
    """

    def __init__(self, credentials: dict):
        self.access_token = credentials["access_token"]
        self.app_key = credentials.get("app_key") or ""
        self.app_secret = credentials.get("app_secret") or ""

    def _get_client(self):
        import dropbox as dbx_module
        if self.app_key and self.app_secret:
            return dbx_module.Dropbox(
                oauth2_access_token=self.access_token,
                app_key=self.app_key,
                app_secret=self.app_secret,
            )
        return dbx_module.Dropbox(oauth2_access_token=self.access_token)

    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        start = time.time()
        try:
            import dropbox as dbx_module

            dbx = self._get_client()
            file_size = local_path.stat().st_size

            # Ensure remote_path starts with /
            if not remote_path.startswith("/"):
                remote_path = "/" + remote_path

            with open(local_path, "rb") as f:
                result = dbx.files_upload(
                    f.read(),
                    remote_path,
                    mode=dbx_module.files.WriteMode.overwrite,
                )

            duration_ms = int((time.time() - start) * 1000)
            remote_url = f"dropbox://{remote_path}"
            logger.info("[DROPBOX] Uploaded %s (%s bytes, %sms)", remote_path, file_size, duration_ms)
            return UploadResult(success=True, remote_url=remote_url, file_size=file_size, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[DROPBOX] Upload failed: %s", e)
            return UploadResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            dbx = self._get_client()
            account = dbx.users_get_current_account()
            return ConnectionTestResult(
                success=True,
                message=f"Connected as {account.name.display_name}",
                provider_info={
                    "account_name": account.name.display_name,
                    "email": account.email,
                },
            )
        except Exception as e:
            logger.warning("[DROPBOX] Connection test failed: %s", e)
            return ConnectionTestResult(success=False, message=str(e))

    async def delete(self, remote_path: str) -> bool:
        try:
            dbx = self._get_client()
            if not remote_path.startswith("/"):
                remote_path = "/" + remote_path
            dbx.files_delete_v2(remote_path)
            logger.info("[DROPBOX] Deleted %s", remote_path)
            return True
        except Exception as e:
            logger.warning("[DROPBOX] Delete failed: %s", e)
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            dbx = self._get_client()
            if not remote_path.startswith("/"):
                remote_path = "/" + remote_path if remote_path else ""
            result = dbx.files_list_folder(remote_path)
            return [entry.name for entry in result.entries]
        except Exception as e:
            logger.warning("[DROPBOX] List files failed: %s", e)
            return []
