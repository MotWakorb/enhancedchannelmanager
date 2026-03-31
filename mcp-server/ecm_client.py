"""Async HTTP client for calling the ECM backend API."""
import logging

import httpx

from config import ECM_URL, get_mcp_api_key

logger = logging.getLogger(__name__)

# Module-level client instance (lazy-initialized)
_client: httpx.AsyncClient | None = None


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
            timeout=30.0,
        )
    return _client


class ECMClient:
    """Wrapper around httpx.AsyncClient for ECM API calls.

    Returns parsed JSON on success, raises descriptive errors on failure.
    """

    async def get(self, path: str, **params) -> dict | list:
        """GET request to ECM API."""
        client = _get_client()
        params = {k: v for k, v in params.items() if v is not None}
        r = await client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def post(self, path: str, json_data: dict | None = None) -> dict | list:
        """POST request to ECM API."""
        client = _get_client()
        r = await client.post(path, json=json_data)
        r.raise_for_status()
        return r.json()

    async def patch(self, path: str, json_data: dict | None = None) -> dict | list:
        """PATCH request to ECM API."""
        client = _get_client()
        r = await client.patch(path, json=json_data)
        r.raise_for_status()
        return r.json()

    async def delete(self, path: str) -> dict | None:
        """DELETE request to ECM API."""
        client = _get_client()
        r = await client.delete(path)
        r.raise_for_status()
        if r.status_code == 204:
            return None
        return r.json()


def get_ecm_client() -> ECMClient:
    """Get a shared ECMClient instance."""
    return ECMClient()
