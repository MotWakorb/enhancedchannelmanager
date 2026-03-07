"""
OneDrive/SharePoint cloud storage adapter via Microsoft Graph API.
Uses client credentials (app-only) OAuth2 flow.
"""
import logging
import time
from pathlib import Path

import httpx

from cloud_storage.base import CloudStorageAdapter, UploadResult, ConnectionTestResult

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


class OneDriveAdapter(CloudStorageAdapter):
    """Adapter for OneDrive/SharePoint via Microsoft Graph API.

    Required credentials:
        client_id: Azure AD app client ID
        client_secret: Azure AD app client secret
        tenant_id: Azure AD tenant ID

    Optional credentials:
        drive_id: Specific drive ID (defaults to app's drive)
        upload_folder_path: Folder path within the drive (default: "/")
    """

    def __init__(self, credentials: dict):
        self.client_id = credentials["client_id"]
        self.client_secret = credentials["client_secret"]
        self.tenant_id = credentials["tenant_id"]
        self.drive_id = credentials.get("drive_id") or ""
        self.folder_path = (credentials.get("upload_folder_path") or "/").strip("/")
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        url = TOKEN_URL.format(tenant_id=self.tenant_id)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
            })
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
            return self._token

    def _drive_prefix(self) -> str:
        if self.drive_id:
            return f"{GRAPH_BASE}/drives/{self.drive_id}"
        return f"{GRAPH_BASE}/me/drive"

    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        start = time.time()
        try:
            token = await self._get_token()
            file_size = local_path.stat().st_size
            filename = Path(remote_path).name

            if self.folder_path:
                item_path = f"{self.folder_path}/{filename}"
            else:
                item_path = filename

            url = f"{self._drive_prefix()}/root:/{item_path}:/content"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(local_path, "rb") as f:
                    resp = await client.put(url, headers=headers, content=f.read())
                resp.raise_for_status()

            data = resp.json()
            duration_ms = int((time.time() - start) * 1000)
            remote_url = data.get("webUrl", f"onedrive://{item_path}")
            logger.info("[ONEDRIVE] Uploaded %s (%s bytes, %sms)", filename, file_size, duration_ms)
            return UploadResult(success=True, remote_url=remote_url, file_size=file_size, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[ONEDRIVE] Upload failed: %s", e)
            return UploadResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            token = await self._get_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._drive_prefix()}/root", headers=headers)
                resp.raise_for_status()
                data = resp.json()
            return ConnectionTestResult(
                success=True,
                message=f"Connected to drive '{data.get('name', 'root')}'",
                provider_info={"drive_name": data.get("name", ""), "drive_id": self.drive_id or "default"},
            )
        except Exception as e:
            logger.warning("[ONEDRIVE] Connection test failed: %s", e)
            return ConnectionTestResult(success=False, message=str(e))

    async def delete(self, remote_path: str) -> bool:
        try:
            token = await self._get_token()
            filename = Path(remote_path).name
            if self.folder_path:
                item_path = f"{self.folder_path}/{filename}"
            else:
                item_path = filename
            url = f"{self._drive_prefix()}/root:/{item_path}"
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(url, headers=headers)
                resp.raise_for_status()
            logger.info("[ONEDRIVE] Deleted %s", item_path)
            return True
        except Exception as e:
            logger.warning("[ONEDRIVE] Delete failed: %s", e)
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            token = await self._get_token()
            path = remote_path or self.folder_path or ""
            if path:
                url = f"{self._drive_prefix()}/root:/{path}:/children"
            else:
                url = f"{self._drive_prefix()}/root/children"
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            return [item["name"] for item in data.get("value", [])]
        except Exception as e:
            logger.warning("[ONEDRIVE] List files failed: %s", e)
            return []
