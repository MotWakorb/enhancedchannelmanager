"""WebDAV cloud storage adapter (bead 0i2vt.8 — NET-NEW).

Conforms to the :class:`cloud_storage.types.CloudStorageAdapter` ABC
(``upload`` / ``test_connection`` / ``delete`` / ``list_files``). Targets any
RFC 4918 WebDAV endpoint (Nextcloud, ownCloud, Apache mod_dav, rclone serve
webdav, a NAS, …) reached over http/https with optional Basic auth.

Security posture (HARD ACs, grooming SRE + Security):

* **SSRF chokepoint (.5).** Every outbound request is pre-validated through
  :func:`cloud_storage.upload_security.preresolve_endpoint`, which raises
  :class:`security.ssrf.SSRFError` BEFORE any socket opens if the base URL host
  (or any resolved record) is denied — including the ``169.254.169.254``
  metadata endpoint. The httpx client then CONNECTS by the validated IP
  (resolve-then-connect-by-IP), preserving the hostname for TLS SNI + ``Host:``.

* **Stream from disk.** The upload sends the artifact via httpx's file-object
  streaming (``content=<open file handle>``) — httpx reads the body in chunks,
  so the whole artifact is NEVER buffered into RAM (HARD AC #2).

* **Executor safety.** Reads/writes are async httpx calls on the event loop;
  the only blocking primitive is opening the local file handle, which is
  negligible. No blocking SDK call runs on the loop (HARD AC #1).

* **Credential masking.** Any logged/surfaced string is passed through
  :func:`cloud_storage.upload_security.redact_secrets` so Authorization headers
  and tokens never reach the logs (HARD AC #5).

* **TLS.** ``verify=True`` by default; the per-target ``insecure`` flag wires
  ``verify=False`` (the caller — :mod:`tasks.dbas_backup` — writes the
  per-request insecure audit row, HARD AC #4).
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from cloud_storage.types import (
    CloudStorageAdapter,
    ConnectionTestResult,
    UploadResult,
)
from cloud_storage.upload_security import (
    SSRFError,
    UPLOAD_TIMEOUT_SECONDS,
    redact_secrets,
    pinned_async_client,
    pinned_request_url,
    preresolve_endpoint,
)

logger = logging.getLogger(__name__)


class WebDAVAdapter(CloudStorageAdapter):
    """Adapter for a generic WebDAV endpoint.

    Required credentials:
        base_url: WebDAV collection base URL (http/https), e.g.
            ``https://nas.example.com/remote.php/dav/files/admin``

    Optional credentials:
        username: Basic-auth username (omit for anonymous endpoints).
        password: Basic-auth password.
        insecure: When True, skip TLS verification (default False). The caller
            is responsible for the per-request insecure audit row.
    """

    def __init__(self, credentials: dict):
        base_url = credentials.get("base_url") or credentials.get("url")
        if not base_url:
            raise ValueError("WebDAV adapter requires a 'base_url'")
        self.base_url = base_url.rstrip("/")
        self.username = credentials.get("username") or ""
        self.password = credentials.get("password") or ""
        self.insecure = bool(credentials.get("insecure", False))

        parts = urlsplit(self.base_url)
        # The path prefix the collection lives under (preserved when building
        # per-file URLs). e.g. "/remote.php/dav/files/admin".
        self._base_path = parts.path.rstrip("/")

    # -- internal helpers --------------------------------------------------
    def _auth_header(self) -> dict:
        if not self.username:
            return {}
        raw = f"{self.username}:{self.password}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    def _remote_url_for(self, remote_path: str) -> str:
        """Compose the absolute WebDAV URL for ``remote_path`` (display only)."""
        rp = remote_path.lstrip("/")
        return f"{self.base_url}/{quote(rp)}"

    def _validate(self, remote_path: str):
        """Pre-resolve + denylist-validate the destination URL (.5 chokepoint).

        Raises SSRFError BEFORE any socket opens. Returns (target, full_url,
        connect_url) where connect_url is pinned to the validated IP.
        """
        full_url = self._remote_url_for(remote_path)
        target = preresolve_endpoint(full_url)
        parts = urlsplit(full_url)
        connect_url = pinned_request_url(target, parts.path, parts.query)
        return target, full_url, connect_url

    def _request_kwargs(self, target) -> dict:
        """Per-request headers/extensions that preserve hostname for TLS+vhost."""
        headers = {"Host": target.host_header}
        headers.update(self._auth_header())
        # Preserve TLS SNI for the original hostname even though we connect by IP.
        return {
            "headers": headers,
            "extensions": {"sni_hostname": target.hostname},
        }

    # -- ABC surface -------------------------------------------------------
    async def upload(self, local_path: Path, remote_path: str) -> UploadResult:
        start = time.time()
        try:
            target, display_url, connect_url = self._validate(remote_path)
            file_size = local_path.stat().st_size
            req = self._request_kwargs(target)

            async with pinned_async_client(
                target, verify=not self.insecure, timeout=UPLOAD_TIMEOUT_SECONDS
            ) as client:
                # Stream the artifact from the on-disk file — httpx reads the
                # body in chunks, never buffering the whole file into RAM.
                with open(local_path, "rb") as fh:
                    resp = await client.put(connect_url, content=fh, **req)
                resp.raise_for_status()

            duration_ms = int((time.time() - start) * 1000)
            # Log only safe fields (filename, byte count, duration, validated
            # host). The display URL — which can carry credential-bearing query
            # material — is returned via UploadResult.remote_url, never logged.
            logger.info(
                "[WEBDAV] Uploaded %s to host %s (%s bytes, %sms)",
                local_path.name, target.hostname, file_size, duration_ms,
            )
            return UploadResult(
                success=True, remote_url=display_url, file_size=file_size,
                duration_ms=duration_ms,
            )
        except SSRFError as e:
            duration_ms = int((time.time() - start) * 1000)
            # Masked detail goes only to UploadResult.error (non-logging sink);
            # the logger gets generic safe text so no sensitive source reaches it.
            logger.warning("[WEBDAV] Upload of %s refused by SSRF policy", local_path.name)
            return UploadResult(success=False, error=redact_secrets(str(e)), duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("[WEBDAV] Upload of %s failed", local_path.name)
            return UploadResult(success=False, error=redact_secrets(str(e)), duration_ms=duration_ms)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            target, display_url, connect_url = self._validate("")
            req = self._request_kwargs(target)
            async with pinned_async_client(
                target, verify=not self.insecure, timeout=30.0
            ) as client:
                # PROPFIND depth 0 on the collection root is the canonical
                # WebDAV "are you there?" probe.
                req["headers"]["Depth"] = "0"
                resp = await client.request("PROPFIND", connect_url, **req)
                resp.raise_for_status()
            return ConnectionTestResult(
                success=True,
                message=f"Connected to WebDAV collection '{target.hostname}'",
                provider_info={"host": target.hostname},
            )
        except SSRFError as e:
            logger.warning("[WEBDAV] Connection refused by SSRF policy")
            return ConnectionTestResult(success=False, message=redact_secrets(str(e)))
        except Exception as e:
            logger.warning("[WEBDAV] Connection test failed")
            return ConnectionTestResult(success=False, message=redact_secrets(str(e)))

    async def delete(self, remote_path: str) -> bool:
        try:
            target, _display, connect_url = self._validate(remote_path)
            req = self._request_kwargs(target)
            async with pinned_async_client(
                target, verify=not self.insecure, timeout=30.0
            ) as client:
                resp = await client.delete(connect_url, **req)
                resp.raise_for_status()
            logger.info("[WEBDAV] Deleted %s", remote_path)
            return True
        except Exception:
            logger.warning("[WEBDAV] Delete of %s failed", remote_path)
            return False

    async def list_files(self, remote_path: str = "") -> list[str]:
        try:
            target, _display, connect_url = self._validate(remote_path)
            req = self._request_kwargs(target)
            req["headers"]["Depth"] = "1"
            async with pinned_async_client(
                target, verify=not self.insecure, timeout=30.0
            ) as client:
                resp = await client.request("PROPFIND", connect_url, **req)
                resp.raise_for_status()
                body = resp.text
            return _parse_propfind_names(body)
        except Exception:
            logger.warning("[WEBDAV] List files under %s failed", remote_path)
            return []


def _parse_propfind_names(xml_body: str) -> list[str]:
    """Extract <d:href> leaf names from a PROPFIND multistatus response.

    Best-effort: WebDAV servers vary in namespace prefix. We scan for href
    elements regardless of prefix and return the final path segment of each.
    """
    import re as _re

    names: list[str] = []
    for match in _re.finditer(r"<[^>]*href[^>]*>([^<]+)</[^>]*href[^>]*>", xml_body, _re.IGNORECASE):
        href = match.group(1).strip().rstrip("/")
        if not href:
            continue
        leaf = href.rsplit("/", 1)[-1]
        if leaf:
            names.append(leaf)
    return names
