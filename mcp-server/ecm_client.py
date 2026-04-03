"""Async HTTP client for calling the ECM backend API."""
import logging

import httpx

from config import ECM_URL, get_mcp_api_key

logger = logging.getLogger(__name__)

# Module-level client instance (lazy-initialized)
_client: httpx.AsyncClient | None = None

# Default timeout for most requests; long-running endpoints override per-call
DEFAULT_TIMEOUT = 30.0
LONG_TIMEOUT = 300.0  # 5 minutes for pipeline/probe/export operations


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client."""
    global _client
    api_key = get_mcp_api_key()
    # Recreate client if API key changed (handles key rotation)
    if _client is None or _client.headers.get("authorization") != f"Bearer {api_key}":
        if _client is not None:
            # Schedule close of old client (best effort)
            try:
                import asyncio
                asyncio.get_event_loop().create_task(_client.aclose())
            except Exception:
                pass
        _client = httpx.AsyncClient(
            base_url=ECM_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
    return _client


class ECMClient:
    """Wrapper around httpx.AsyncClient for ECM API calls.

    Returns parsed JSON on success, raises descriptive errors on failure.
    """

    async def get(self, path: str, timeout: float | None = None, **params) -> dict | list:
        """GET request to ECM API."""
        client = _get_client()
        params = {k: v for k, v in params.items() if v is not None}
        try:
            r = await client.get(path, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            logger.error("[ECM-CLIENT] GET %s timed out after %ss", path, timeout or DEFAULT_TIMEOUT)
            raise TimeoutError(f"GET {path} timed out after {timeout or DEFAULT_TIMEOUT}s")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("[ECM-CLIENT] GET %s failed: %s %s — %s", path, e.response.status_code, e.response.reason_phrase, body)
            raise

    async def post(self, path: str, json_data: dict | None = None, timeout: float | None = None) -> dict | list:
        """POST request to ECM API."""
        client = _get_client()
        try:
            r = await client.post(path, json=json_data, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            logger.error("[ECM-CLIENT] POST %s timed out after %ss", path, timeout or DEFAULT_TIMEOUT)
            raise TimeoutError(f"POST {path} timed out after {timeout or DEFAULT_TIMEOUT}s")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("[ECM-CLIENT] POST %s failed: %s %s — %s", path, e.response.status_code, e.response.reason_phrase, body)
            raise

    async def patch(self, path: str, json_data: dict | None = None, timeout: float | None = None) -> dict | list:
        """PATCH request to ECM API."""
        client = _get_client()
        try:
            r = await client.patch(path, json=json_data, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            logger.error("[ECM-CLIENT] PATCH %s timed out after %ss", path, timeout or DEFAULT_TIMEOUT)
            raise TimeoutError(f"PATCH {path} timed out after {timeout or DEFAULT_TIMEOUT}s")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("[ECM-CLIENT] PATCH %s failed: %s %s — %s", path, e.response.status_code, e.response.reason_phrase, body)
            raise

    async def put(self, path: str, json_data: dict | None = None, timeout: float | None = None) -> dict | list:
        """PUT request to ECM API."""
        client = _get_client()
        try:
            r = await client.put(path, json=json_data, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            logger.error("[ECM-CLIENT] PUT %s timed out after %ss", path, timeout or DEFAULT_TIMEOUT)
            raise TimeoutError(f"PUT {path} timed out after {timeout or DEFAULT_TIMEOUT}s")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("[ECM-CLIENT] PUT %s failed: %s %s — %s", path, e.response.status_code, e.response.reason_phrase, body)
            raise

    async def delete(self, path: str, json_data: dict | None = None, timeout: float | None = None) -> dict | None:
        """DELETE request to ECM API."""
        client = _get_client()
        try:
            r = await client.request("DELETE", path, json=json_data, timeout=timeout)
            r.raise_for_status()
            if r.status_code == 204:
                return None
            return r.json()
        except httpx.TimeoutException:
            logger.error("[ECM-CLIENT] DELETE %s timed out after %ss", path, timeout or DEFAULT_TIMEOUT)
            raise TimeoutError(f"DELETE {path} timed out after {timeout or DEFAULT_TIMEOUT}s")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("[ECM-CLIENT] DELETE %s failed: %s %s — %s", path, e.response.status_code, e.response.reason_phrase, body)
            raise


def get_ecm_client() -> ECMClient:
    """Get a shared ECMClient instance."""
    return ECMClient()
