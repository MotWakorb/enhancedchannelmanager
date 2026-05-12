"""Async HTTP client for calling the ECM backend API."""
import logging
import string

import httpx

from _endpoint_contracts import Endpoint
from config import ECM_URL, get_mcp_api_key

logger = logging.getLogger(__name__)


class ContractError(RuntimeError):
    """A tool sent a request that violates its declared endpoint contract.

    Raised by :meth:`ECMClient.call_endpoint` before any HTTP call when the
    body/query keys aren't a subset of the registered ``request_fields`` /
    ``query_params``, or when a path placeholder is unfilled / unknown. This is
    the loud-at-call-time guard — a tool that has drifted from
    ``_endpoint_contracts.ENDPOINTS`` fails here, not silently at the backend
    (the GH #221 ``group_id`` vs ``channel_group_id`` class of bug).
    """

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
                # No running loop or shutdown already in progress: drop the old
                # client; httpx will close its sockets when the object is GC'd.
                pass
        _client = httpx.AsyncClient(
            base_url=ECM_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
    return _client


def _http_error(method: str, path: str, e: httpx.HTTPStatusError) -> RuntimeError:
    """Build a descriptive RuntimeError from an httpx HTTPStatusError.

    FastAPI's auto-validation 422s carry a structured ``detail`` list
    (``[{loc: ["body", "operations", N, "<field>"], msg, type}, ...]``) that
    pinpoints the bad operation/field; a custom ``HTTPException`` carries a
    plain string. Either way, surface it instead of the bare status line so
    callers (and the MCP tools that wrap them) see *why* the request failed
    (bd-mjtxn / GH #224). Defensive: the body may not be JSON or may lack
    ``detail``.
    """
    resp = e.response
    status = resp.status_code if resp is not None else "?"
    reason = resp.reason_phrase if resp is not None else ""
    detail = None
    if resp is not None:
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = body.get("detail", body)
            else:
                detail = body
        except Exception:
            text = (resp.text or "").strip()
            detail = text[:500] if text else None
    suffix = f": {detail}" if detail not in (None, "") else ""
    return RuntimeError(f"{method} {path} -> HTTP {status} {reason}{suffix}".rstrip())


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
            raise _http_error("GET", path, e) from e

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
            raise _http_error("POST", path, e) from e

    async def post_multipart(
        self,
        path: str,
        files: dict,
        timeout: float | None = None,
    ) -> dict | list:
        """POST a multipart/form-data request to ECM API.

        ``files`` mirrors httpx's expected shape:
        ``{"file": (filename, content_bytes, content_type)}``.
        """
        client = _get_client()
        try:
            r = await client.post(path, files=files, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            logger.error("[ECM-CLIENT] POST(multipart) %s timed out after %ss", path, timeout or DEFAULT_TIMEOUT)
            raise TimeoutError(f"POST {path} timed out after {timeout or DEFAULT_TIMEOUT}s")
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("[ECM-CLIENT] POST(multipart) %s failed: %s %s — %s", path, e.response.status_code, e.response.reason_phrase, body)
            raise _http_error("POST", path, e) from e

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
            raise _http_error("PATCH", path, e) from e

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
            raise _http_error("PUT", path, e) from e

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
            raise _http_error("DELETE", path, e) from e

    async def call_endpoint(
        self,
        ep: Endpoint,
        *,
        path_args: dict | None = None,
        body: dict | None = None,
        query: dict | None = None,
        timeout: float | None = None,
    ) -> dict | list | None:
        """Call a backend endpoint declared in ``_endpoint_contracts.ENDPOINTS``.

        Enforces the contract *before* the request goes out:

        * ``ep.path`` is formatted from ``path_args`` — :class:`ContractError`
          if a ``{placeholder}`` is unfilled or an unknown key is passed.
        * ``set(body) <= ep.request_fields`` and ``set(query) <= ep.query_params``
          — :class:`ContractError` naming the offending keys, the endpoint, and
          the allowed set. Always on (cheap subset check).

        Dispatch delegates to the existing :meth:`get` / :meth:`post` /
        :meth:`patch` / :meth:`put` / :meth:`delete` methods, so it inherits
        their timeout handling and their 4xx/5xx → ``detail``-surfacing error
        behaviour (``_http_error``) — and so existing test mocks of those
        methods keep working. Response *shape* is intentionally not validated
        at runtime (the backend may omit optional fields); that's the contract
        test's job.
        """
        path_args = dict(path_args or {})
        body = dict(body) if body is not None else None
        query = dict(query) if query is not None else None

        # Format the path from path_args (clear errors for missing/unknown).
        expected_placeholders = {
            fname for _, fname, _, _ in string.Formatter().parse(ep.path) if fname
        }
        missing = expected_placeholders - set(path_args)
        if missing:
            raise ContractError(
                f"call_endpoint({ep.name!r}): missing path argument(s) "
                f"{sorted(missing)} for path {ep.path!r}"
            )
        unknown_path = set(path_args) - expected_placeholders
        if unknown_path:
            raise ContractError(
                f"call_endpoint({ep.name!r}): unknown path argument(s) "
                f"{sorted(unknown_path)} for path {ep.path!r} "
                f"(expected {sorted(expected_placeholders)})"
            )
        formatted_path = ep.path.format(**path_args)

        # Subset checks against the declared contract.
        if body:
            extra = set(body) - set(ep.request_fields)
            if extra:
                raise ContractError(
                    f"call_endpoint({ep.name!r}): body key(s) {sorted(extra)} "
                    f"not in this endpoint's request_fields "
                    f"{sorted(ep.request_fields)} ({ep.method} {ep.path}). "
                    "Update mcp-server/_endpoint_contracts.py if the backend "
                    "really accepts these, or fix the tool."
                )
        if query:
            extra = set(query) - set(ep.query_params)
            if extra:
                raise ContractError(
                    f"call_endpoint({ep.name!r}): query key(s) {sorted(extra)} "
                    f"not in this endpoint's query_params "
                    f"{sorted(ep.query_params)} ({ep.method} {ep.path})."
                )

        method = ep.method.upper()
        if method == "GET":
            return await self.get(formatted_path, timeout=timeout, **(query or {}))
        if method == "POST":
            return await self.post(formatted_path, json_data=body, timeout=timeout)
        if method == "PATCH":
            return await self.patch(formatted_path, json_data=body, timeout=timeout)
        if method == "PUT":
            return await self.put(formatted_path, json_data=body, timeout=timeout)
        if method == "DELETE":
            return await self.delete(formatted_path, json_data=body, timeout=timeout)
        raise ContractError(
            f"call_endpoint({ep.name!r}): unsupported method {ep.method!r}"
        )


def get_ecm_client() -> ECMClient:
    """Get a shared ECMClient instance."""
    return ECMClient()
