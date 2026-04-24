"""
OneDrive/SharePoint cloud storage adapter via Microsoft Graph API.
Uses client credentials (app-only) OAuth2 flow.
"""
import logging
import re
import time
from pathlib import Path

import httpx

from cloud_storage.types import CloudStorageAdapter, UploadResult, ConnectionTestResult

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Azure AD tenant IDs are either GUIDs (common.onmicrosoft.com case not permitted
# for app-only flows) or verified domain names such as `contoso.onmicrosoft.com`.
# Anchored start/end to prevent URL injection (e.g. `evil.com/`, `../`, null bytes).
# Module-level constant assembled from raw-string literal fragments; no runtime
# interpolation — safe by construction.
_TENANT_ID_RE = re.compile(  # nosemgrep: no-bare-re-on-dynamic-pattern
    r"\A(?:"
    # GUID form
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|"
    # DNS domain form (labels separated by dots; each label 1-63 chars, LDH)
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
    r")\Z"
)

# Microsoft Graph drive IDs are base64url-ish identifiers. Accept only
# characters that appear in real drive IDs; reject path separators, URL
# schemes, percent-encoding, whitespace, and unicode.
_DRIVE_ID_RE = re.compile(r"\A[A-Za-z0-9!_\-]{1,128}\Z")


def _validate_tenant_id(value: str) -> str:
    """Validate an Azure AD tenant identifier.

    Accepts GUID form (`00000000-0000-0000-0000-000000000000`) or a DNS
    domain (`contoso.onmicrosoft.com`). Raises ``ValueError`` otherwise.

    This guards against SSRF via URL injection into the Microsoft
    Graph OAuth token endpoint (see CodeQL alert 1361).
    """
    if not isinstance(value, str) or not value:
        raise ValueError("tenant_id must be a non-empty string")
    if len(value) > 253:
        raise ValueError("tenant_id is too long")
    if not _TENANT_ID_RE.match(value):
        raise ValueError(
            "tenant_id must be an Azure AD GUID or verified domain name"
        )
    return value


def _validate_drive_id(value: str) -> str:
    """Validate a Microsoft Graph drive identifier.

    Accepts base64url-style identifiers (alnum, ``!``, ``_``, ``-``).
    Raises ``ValueError`` otherwise.

    This guards against SSRF via URL injection into the Microsoft
    Graph drives endpoint (see CodeQL alert 1362). Empty is allowed
    because it selects the app's default drive.
    """
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("drive_id must be a string")
    if not _DRIVE_ID_RE.match(value):
        raise ValueError(
            "drive_id must be a base64url-style Microsoft Graph identifier"
        )
    return value


class OneDriveAdapter(CloudStorageAdapter):
    """Adapter for OneDrive/SharePoint via Microsoft Graph API.

    Required credentials:
        client_id: Azure AD app client ID
        client_secret: Azure AD app client secret
        tenant_id: Azure AD tenant ID (GUID or verified domain)

    Optional credentials:
        drive_id: Specific drive ID (base64url-style; defaults to app's drive)
        upload_folder_path: Folder path within the drive (default: "/")

    Raises:
        ValueError: if ``tenant_id`` or ``drive_id`` fail shape validation.
            This is defense-in-depth; the Pydantic request models at the
            API boundary reject bad values with HTTP 422 first.
    """

    def __init__(self, credentials: dict):
        self.client_id = credentials["client_id"]
        self.client_secret = credentials["client_secret"]
        # Validate tenant_id and drive_id before they ever reach URL
        # construction. See CodeQL alerts 1361 / 1362.
        self.tenant_id = _validate_tenant_id(credentials["tenant_id"])
        self.drive_id = _validate_drive_id(credentials.get("drive_id") or "")
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
