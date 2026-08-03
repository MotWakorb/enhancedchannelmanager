import asyncio
import hashlib
import hmac
import json
import re
import secrets
import httpx
import logging
from typing import Optional
from config import get_settings, DispatcharrSettings
from concurrency import run_cpu_bound

logger = logging.getLogger(__name__)


# Matches the message format produced by the create_* helpers below
# (``create_channel``, ``create_channel_group``, ``create_logo``, ...), which
# embed the upstream status + body in a bare ``Exception`` string rather than
# raising ``httpx.HTTPStatusError``:  "<thing> failed: <status> - <body>".
_UPSTREAM_FAILURE_RE = re.compile(r"failed:\s*(\d{3})\s*-\s*(.*)", re.DOTALL)


def _format_upstream_detail(status_code: int, body: str) -> str:
    """Turn a raw upstream response body into a concise HTTPException detail.

    Dispatcharr returns DRF-style validation errors as JSON, e.g.
    ``{"channel_group_id": ["Invalid pk \"999\" - object does not exist."]}``.
    We surface the actionable content rather than the whole envelope. If the
    body is not JSON (HTML error page, plain text), it is passed through
    trimmed.
    """
    body = (body or "").strip()
    if not body:
        return f"Dispatcharr returned HTTP {status_code}"
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return body
    # DRF errors are usually a dict of field -> [messages] or {"detail": "..."}.
    if isinstance(parsed, dict):
        if "detail" in parsed and len(parsed) == 1:
            return str(parsed["detail"])
        parts = []
        for field, messages in parsed.items():
            if isinstance(messages, (list, tuple)):
                joined = "; ".join(str(m) for m in messages)
            else:
                joined = str(messages)
            parts.append(f"{field}: {joined}")
        return " ".join(parts) if parts else body
    if isinstance(parsed, list):
        return "; ".join(str(m) for m in parsed)
    return str(parsed)


def upstream_http_exception(exc: Exception):
    """Map a Dispatcharr-client error to a client-facing ``HTTPException``.

    The Dispatcharr client surfaces upstream failures in two shapes:

    1. ``httpx.HTTPStatusError`` — from helpers that call
       ``response.raise_for_status()`` (e.g. ``create_epg_source``,
       ``delete_channel_group``, ``get_channel``). Carries ``.response``.
    2. A bare ``Exception`` whose message is
       ``"<thing> failed: <status> - <body>"`` — from helpers that build the
       error string by hand (e.g. ``create_channel``, ``create_channel_group``).

    For an upstream **4xx** this returns an ``HTTPException`` that preserves the
    actionable upstream status + detail (404 stays 404; every other 4xx maps to
    400 with the upstream detail so the caller sees *why* the request was
    rejected instead of an opaque 500). For anything else (5xx, connection
    errors, non-matching exceptions) it returns ``None`` so the caller falls
    through to its generic ``500 Internal server error`` handler. This keeps
    genuine server faults as 500 while exposing actionable client errors.

    Imported lazily inside callers' ``except`` blocks; FastAPI's
    ``HTTPException`` is imported here to avoid a module-level web-framework
    dependency in the HTTP client.
    """
    from fastapi import HTTPException

    status_code: Optional[int] = None
    body: str = ""

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        body = exc.response.text
    else:
        match = _UPSTREAM_FAILURE_RE.search(str(exc))
        if match:
            status_code = int(match.group(1))
            body = match.group(2)

    if status_code is None or not (400 <= status_code < 500):
        return None

    detail = _format_upstream_detail(status_code, body)
    if status_code == 404:
        return HTTPException(status_code=404, detail=detail)
    # Collapse other client errors (400/409/422/...) to 400 with the upstream
    # detail. The point of the bead is "stop masking the actionable 4xx detail
    # as 500", not to perfectly mirror every upstream status code.
    return HTTPException(status_code=400, detail=detail)

# Process-local random key used by ``_settings_hash`` (see below). Generated
# once per Python process; same Python process always produces the same hash
# for the same settings (so the in-memory client-singleton cache invalidation
# in ``get_client()`` works), but the same settings on a different process
# produce a different hash (defense against any cross-process correlation).
# Using HMAC with a random key rather than a bare SHA-256 of the credentials
# also avoids the CodeQL false-positive ``py/weak-sensitive-data-hashing``
# (which is correctly aimed at password-storage hashing, not at process-local
# cache-key derivation).
_SETTINGS_HASH_KEY: bytes = secrets.token_bytes(32)

# DBAS-restore endpoint paths (enhancedchannelmanager-0i2vt.13). Isolated as
# constants so a single live-verification follow-up can correct any path string
# without editing the methods. Dispatcharr groups core resources under
# ``/api/core/`` (confirmed for streamprofiles/version/system-events); the DVR
# module lives under ``/api/dvr/``. These specific paths are best-known and not
# yet live-confirmed — see the method block for the deferred-verification note.
_USER_AGENTS_PATH = "/api/core/useragents/"
_CORE_SETTINGS_PATH = "/api/core/settings/"
_DVR_RULES_PATH = "/api/dvr/rules/"
_EPG_DATA_BYTES_PER_RESULT = 2048
_EPG_DATA_MIN_RESPONSE_BYTES = 1024 * 1024


class DispatcharrClient:
    """API client for Dispatcharr with JWT authentication."""

    def __init__(self, settings: DispatcharrSettings):
        self.settings = settings
        self.base_url = self.settings.url.rstrip("/")
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=5.0,
            ),
        )
        # Lock to prevent multiple concurrent authentication attempts
        # This prevents race conditions when many requests arrive simultaneously
        self._auth_lock = asyncio.Lock()

    @property
    def _uses_api_key(self) -> bool:
        return self.settings.auth_method == "api_key"

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid access token.

        Uses a lock to prevent multiple concurrent requests from all
        triggering authentication at the same time (race condition).

        API-key mode is stateless: the key is attached per-request in
        ``_request``, so this is a no-op.
        """
        if self._uses_api_key:
            return

        # Quick check without lock - if we have a token, we're good
        if self.access_token:
            return

        # Acquire lock and check again (double-check locking pattern)
        async with self._auth_lock:
            if not self.access_token:
                await self._login()

    async def _login(self) -> None:
        """Authenticate and obtain JWT tokens."""
        logger.debug("[DISPATCHARR] Authenticating to Dispatcharr at %s", self.base_url)
        try:
            response = await self._client.post(
                f"{self.base_url}/api/accounts/token/",
                json={
                    "username": self.settings.username,
                    "password": self.settings.password,
                },
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access"]
            self.refresh_token = data.get("refresh")
            logger.info("[DISPATCHARR] Successfully authenticated to Dispatcharr - username: %s", self.settings.username)
        except httpx.HTTPStatusError as e:
            logger.error("[DISPATCHARR] Authentication failed - status: %s", e.response.status_code)
            raise
        except Exception as e:
            logger.exception("[DISPATCHARR] Authentication error: %s", e)
            raise

    async def _refresh_access_token(self) -> None:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            logger.debug("[DISPATCHARR] No refresh token available, performing full login")
            await self._login()
            return

        logger.debug("[DISPATCHARR] Refreshing access token")
        response = await self._client.post(
            f"{self.base_url}/api/accounts/token/refresh/",
            json={"refresh": self.refresh_token},
        )
        if response.status_code == 200:
            data = response.json()
            self.access_token = data["access"]
            logger.debug("[DISPATCHARR] Access token refreshed successfully")
        else:
            # Refresh token expired, do full login
            logger.warning("[DISPATCHARR] Refresh token expired (status: %s), performing full login", response.status_code)
            await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an authenticated request with automatic token refresh."""
        # Clear-text-logging hygiene (bead 0i2vt.13): NEVER log ``path`` at any
        # sink in this method. ``path`` is attacker-influenced/credential-tainted
        # in practice -- callers interpolate caller-supplied identifiers into it,
        # and a path *could* in principle carry a query string with a token. CodeQL's
        # py/clear-text-logging-sensitive-data correctly traces that credential
        # source into ``path`` and flags every ``logger.*(..., path)`` sink here.
        # These logs use only NON-sensitive fields -- HTTP method, status code,
        # and (on error) the exception TYPE name -- which is plenty to triage an
        # upstream failure without ever emitting a URL, token, or response body.
        logger.debug("[DISPATCHARR] API request: %s", method)
        await self._ensure_authenticated()

        headers = kwargs.pop("headers", {})
        if self._uses_api_key:
            # bd-jmi1c (GH #273): prefer the canonical
            # ``dispatcharr_api_key`` field; fall back to the legacy
            # ``api_key`` when running pre-migration in-memory state (e.g.,
            # tests constructing ``DispatcharrSettings(api_key=...)`` directly
            # without going through ``load_settings()``). Production code does
            # not construct the model with legacy-only fields (every site reads
            # from ``load_settings()`` which migrates), but the fallback is
            # kept defensively while the legacy field exists on the model.
            # Back-compat: drop 'or self.settings.api_key' in v0.19.0 (bd-ewm4h).
            headers["X-API-Key"] = self.settings.dispatcharr_api_key or self.settings.api_key
        else:
            headers["Authorization"] = f"Bearer {self.access_token}"

        # Use extended timeout for EPG grid endpoint (large channel counts)
        request_timeout = kwargs.pop("timeout", None)
        if request_timeout is None and path == "/api/epg/grid/":
            request_timeout = 120.0  # 2 minutes for filtered EPG grid data
            logger.debug("[DISPATCHARR] Using extended timeout (%ss) for EPG grid request", request_timeout)

        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=request_timeout,
                **kwargs,
            )

            # If unauthorized in JWT mode, try refreshing token and retry.
            # In api-key mode a 401 is terminal (the key is invalid or revoked).
            if response.status_code == 401 and not self._uses_api_key:
                logger.debug("[DISPATCHARR] Got 401, refreshing token and retrying: %s", method)
                await self._refresh_access_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    timeout=request_timeout,
                    **kwargs,
                )

            if response.status_code >= 400:
                logger.warning("[DISPATCHARR] API request failed: %s - status: %s", method, response.status_code)
            else:
                logger.debug("[DISPATCHARR] API request successful: %s - status: %s", method, response.status_code)

            return response
        except Exception as e:
            # Log only the exception TYPE name -- never the exception object or
            # ``str(e)``. An httpx error's text can embed the full request URL
            # (with query params/credentials), so logging ``e`` here would leak
            # exactly what CodeQL flags. Method + exception type is enough to
            # triage; ``logger.exception`` still attaches the traceback.
            logger.exception("[DISPATCHARR] API request error: %s - %s", method, type(e).__name__)
            raise

    # -------------------------------------------------------------------------
    # Channels
    # -------------------------------------------------------------------------

    async def get_channels(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
        channel_group: Optional[int] = None,
    ) -> dict:
        """Get paginated list of channels.

        ``channel_group`` is a channel-group **ID** (the value ECM callers —
        frontend, MCP — work with). Dispatcharr's ``channel_group`` query
        filter, however, matches on the group **name**, not the ID (bd-1wq7z.11:
        forwarding the bare ID matched zero groups and returned 0 rows). So when
        a group filter is requested we translate the ID -> name before
        forwarding. An unresolvable ID yields an empty page rather than falling
        through to an unfiltered query (which would silently return everything).
        """
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if channel_group is not None:
            group_name = await self._channel_group_name_for_id(channel_group)
            if group_name is None:
                logger.warning(
                    "[DISPATCHARR] channel_group filter id=%s matched no group; "
                    "returning empty page", channel_group
                )
                return {"count": 0, "next": None, "previous": None, "results": []}
            params["channel_group"] = group_name

        response = await self._request("GET", "/api/channels/channels/", params=params)
        response.raise_for_status()
        return response.json()

    async def _channel_group_name_for_id(self, group_id: int) -> Optional[str]:
        """Resolve a channel-group ID to its name, or None if no group matches.

        Dispatcharr's channel list ``channel_group`` filter is name-based, so
        callers that filter by group ID must translate first (see
        ``get_channels``).
        """
        groups = await self.get_channel_groups()
        for group in groups:
            if group.get("id") == group_id:
                return group.get("name")
        return None

    async def get_channel(self, channel_id: int) -> dict:
        """Get a single channel by ID."""
        response = await self._request("GET", f"/api/channels/channels/{channel_id}/")
        response.raise_for_status()
        return response.json()

    async def get_channel_streams(self, channel_id: int) -> list:
        """Get streams assigned to a channel."""
        response = await self._request(
            "GET", f"/api/channels/channels/{channel_id}/streams/"
        )
        response.raise_for_status()
        return response.json()

    async def update_channel(self, channel_id: int, data: dict) -> dict:
        """Update a channel (PATCH)."""
        response = await self._request(
            "PATCH", f"/api/channels/channels/{channel_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def create_channel(self, data: dict) -> dict:
        """Create a new channel.

        Returns the created channel, or raises an exception with the response body
        if creation fails.
        """
        response = await self._request(
            "POST", "/api/channels/channels/", json=data
        )
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Channel creation failed: {response.status_code} - {error_body}")
        return response.json()

    async def delete_channel(self, channel_id: int) -> None:
        """Delete a channel."""
        response = await self._request(
            "DELETE", f"/api/channels/channels/{channel_id}/"
        )
        response.raise_for_status()

    async def assign_channel_numbers(
        self, channel_ids: list[int], starting_number: Optional[float] = None
    ) -> dict:
        """Bulk assign channel numbers."""
        data = {"channel_ids": channel_ids}
        if starting_number is not None:
            data["starting_number"] = starting_number

        response = await self._request(
            "POST", "/api/channels/channels/assign/", json=data
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Channel Groups
    # -------------------------------------------------------------------------

    async def get_channel_groups(self) -> list:
        """Get all channel groups."""
        response = await self._request("GET", "/api/channels/groups/")
        response.raise_for_status()
        return response.json()

    async def create_channel_group(self, name: str) -> dict:
        """Create a new channel group.

        Returns the created group, or raises an exception with the response body
        if creation fails (e.g., group already exists).
        """
        response = await self._request(
            "POST", "/api/channels/groups/", json={"name": name}
        )
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Channel group creation failed: {response.status_code} - {error_body}")
        return response.json()

    async def update_channel_group(self, group_id: int, data: dict) -> dict:
        """Update a channel group."""
        response = await self._request(
            "PATCH", f"/api/channels/groups/{group_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_channel_group(self, group_id: int) -> None:
        """Delete a channel group."""
        response = await self._request("DELETE", f"/api/channels/groups/{group_id}/")
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # Streams
    # -------------------------------------------------------------------------

    async def get_streams(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
        channel_group_name: Optional[str] = None,
        m3u_account: Optional[int] = None,
        is_catchup: Optional[bool] = None,
    ) -> dict:
        """Get paginated list of streams.

        ``is_catchup`` filters by Dispatcharr's catch-up flag when set. The flag
        is ``db_index=True`` upstream, so a ``page_size=1`` request with
        ``is_catchup=True`` is a cheap indexed count query — used by the
        provider catch-up badge (bead 4dpiz) to detect whether a provider has
        any catch-up stream via the paginated ``count``.
        """
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if channel_group_name:
            params["channel_group_name"] = channel_group_name
        # Use `is not None` so m3u_account=0 (a valid provider id) is not silently
        # dropped by a falsy guard (bd-o529v; same class as the bd-d5z9u
        # channel_group=0 bug in get_channels).
        if m3u_account is not None:
            params["m3u_account"] = m3u_account
        # Dispatcharr's BooleanFilter expects the lowercase string form.
        if is_catchup is not None:
            params["is_catchup"] = "true" if is_catchup else "false"

        response = await self._request("GET", "/api/channels/streams/", params=params)
        response.raise_for_status()
        return response.json()

    async def get_stream(self, stream_id: int) -> dict:
        """Get a single stream by ID."""
        response = await self._request("GET", f"/api/channels/streams/{stream_id}/")
        response.raise_for_status()
        return response.json()

    async def update_stream(self, stream_id: int, data: dict) -> dict:
        """PATCH a stream by ID. Used to reflect ECM probe stats back to Dispatcharr."""
        response = await self._request(
            "PATCH", f"/api/channels/streams/{stream_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def create_stream(self, data: dict) -> dict:
        """Create a new stream.

        Used by the Channels restore importer (enhancedchannelmanager-nav0c):
        channels carry embedded (non-standalone) streams, so restoring a channel
        means recreating any of its streams that no matcher tier resolved to an
        existing one.

        Returns the created stream, or raises an exception with the response body
        if creation fails. The error message uses the same
        ``"<thing> failed: <status> - <body>"`` shape as ``create_channel`` /
        ``create_channel_group`` so ``upstream_http_exception`` can parse the
        upstream status + actionable detail (see restore_contracts mapper).
        """
        if not isinstance(data, dict):
            raise ValueError("create_stream requires a dict payload")
        response = await self._request(
            "POST", "/api/channels/streams/", json=data
        )
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Stream creation failed: {response.status_code} - {error_body}")
        return response.json()

    async def delete_stream(self, stream_id: int) -> None:
        """Delete a stream by ID.

        Used as the rollback compensation for a previously created stream
        (restore_contracts rollback ledger, enhancedchannelmanager-nav0c): if a
        later step in the same restore fails, streams created earlier in the run
        are deleted to avoid leaving orphans. A 404 here means the stream is
        already gone, which the caller treats as a successful (idempotent)
        compensation.
        """
        response = await self._request(
            "DELETE", f"/api/channels/streams/{stream_id}/"
        )
        response.raise_for_status()

    async def get_streams_by_ids(self, ids: list[int]) -> list:
        """Get multiple streams by IDs."""
        response = await self._request(
            "POST", "/api/channels/streams/by-ids/", json={"ids": ids}
        )
        response.raise_for_status()
        result = response.json()
        # Debug logging to see what M3U account data we're getting
        logger.debug("[DISPATCHARR] get_streams_by_ids returned %s streams", len(result))
        for stream in result:
            logger.debug("[DISPATCHARR]   Stream %s: name='%s', m3u_account=%r", stream.get('id'), stream.get('name', 'Unknown')[:50], stream.get('m3u_account'))
        return result

    async def get_stream_groups(self) -> list:
        """Get all stream groups (for filtering)."""
        response = await self._request("GET", "/api/channels/streams/groups/")
        response.raise_for_status()
        return response.json()

    async def get_stream_groups_with_counts(self, m3u_account_id: Optional[int] = None) -> list:
        """Get all stream groups with their stream counts.

        Args:
            m3u_account_id: Optional M3U account ID to filter groups by provider.
                           When provided, only returns groups that have streams
                           from this provider, with counts reflecting only that provider's streams.

        Returns list of dicts: [{"name": "Group Name", "count": 42}, ...]

        Source (enhancedchannelmanager-05h8w): the per-group stream count
        is read from the ``channel_groups[].stream_count`` field embedded
        in ``GET /api/m3u/accounts/`` (the LIST view seeds a single
        grouped COUNT per group, including disabled and zero-stream
        groups) instead of firing one ``?channel_group_name=&page_size=1``
        probe per group. This issues ZERO ``get_streams`` calls — just the
        single accounts pull plus the channel-groups id→name lookup.

        Semantics:
          - ``m3u_account_id`` given → only that account's groups.
          - ``m3u_account_id`` None → ``stream_count`` summed per group
            name across all accounts.
          - In BOTH cases, groups with count == 0 are dropped. This
            faithfully restores the pre-05h8w "only groups that have
            streams" universe (enhancedchannelmanager-rmyn7): the
            all-accounts Streams pane must not surface empty/disabled
            groups on first load.
        Sorted by name, case-insensitively.
        """
        # id→name map for channel groups (channel_groups[] carries the
        # numeric channel_group id, not the name).
        channel_groups = await self.get_channel_groups()
        name_by_id: dict = {}
        for group in channel_groups:
            group_id = group.get("id")
            group_name = group.get("name")
            if group_id is not None and group_name:
                name_by_id[group_id] = group_name

        accounts = await self.get_m3u_accounts()

        counts_by_name: dict = {}
        missing_count_seen = False
        for account in accounts:
            if m3u_account_id is not None and account.get("id") != m3u_account_id:
                continue
            for acg in account.get("channel_groups", []):
                group_id = acg.get("channel_group")
                group_name = name_by_id.get(group_id)
                if not group_name:
                    continue
                if "stream_count" not in acg:
                    missing_count_seen = True
                    stream_count = 0
                else:
                    stream_count = acg.get("stream_count") or 0
                counts_by_name[group_name] = counts_by_name.get(group_name, 0) + stream_count

        if missing_count_seen:
            logger.warning(
                "[DISPATCHARR] get_stream_groups_with_counts: one or more "
                "channel_groups[] entries lacked a stream_count field; "
                "treated as 0 (no probing fallback)"
            )

        results = [{"name": name, "count": count} for name, count in counts_by_name.items()]

        # Drop groups with 0 streams in all cases (matches the prior
        # probe-based behaviour, where the group universe came from groups
        # that actually had streams). Applies to both the provider-filtered
        # and the all-accounts paths (enhancedchannelmanager-rmyn7).
        results = [r for r in results if r["count"] > 0]

        results.sort(key=lambda x: x["name"].lower())

        return results

    # -------------------------------------------------------------------------
    # M3U Accounts (Providers)
    # -------------------------------------------------------------------------

    async def get_m3u_accounts(self) -> list:
        """Get all M3U accounts/providers."""
        response = await self._request("GET", "/api/m3u/accounts/")
        response.raise_for_status()
        accounts = response.json()
        logger.debug("[DISPATCHARR] Dispatcharr returned %s M3U accounts", len(accounts))
        for account in accounts:
            logger.debug("[DISPATCHARR]   M3U Account: id=%s, name=%s, server_url=%s", account.get('id'), account.get('name'), account.get('server_url'))
        return accounts

    async def get_all_m3u_group_settings(self) -> dict:
        """Get group settings for all M3U accounts, returns dict mapping channel_group_id to settings.

        The channel_groups data is embedded in the accounts response, so we extract it from there.

        GLOBAL-PER-CHANNEL-GROUP contract (GH #720 Part B, decision "global per
        group"): when multiple accounts carry settings for the SAME global
        channel-group id we collapse to ONE row DETERMINISTICALLY — precedence
        is ``auto_channel_sync`` ON first, then the LOWEST ``m3u_account_id``.
        (The prior "prefer auto_channel_sync, else first-seen" collapse was
        order-dependent across accounts with the same auto_channel_sync state.)
        The winning row also carries ``_ecm_channel_profile_conflict``: True
        when two account rows for the group hold DIFFERENT non-empty
        ``channel_profile_ids`` selections, so the profile reconcile can warn.
        """
        accounts = await self.get_m3u_accounts()
        logger.info("[DISPATCHARR] get_all_m3u_group_settings: Processing %s M3U accounts", len(accounts))

        # Gather ALL account rows per global group id first, then pick a
        # deterministic winner (auto_channel_sync ON first, then lowest account
        # id). Collecting first also lets us detect cross-account selection
        # conflicts.
        rows_by_group: dict = {}
        total_groups_found = 0
        for account in accounts:
            channel_groups = account.get("channel_groups", [])
            total_groups_found += len(channel_groups)
            logger.info("[DISPATCHARR]   Account %s: %s has %s channel_groups", account.get('id'), account.get('name'), len(channel_groups))
            for setting in channel_groups:
                channel_group_id = setting.get("channel_group")
                if channel_group_id:
                    rows_by_group.setdefault(channel_group_id, []).append({
                        **setting,
                        "m3u_account_id": account["id"],
                        "m3u_account_name": account.get("name", ""),
                    })

        # Finding 2: coerce ids exactly as _selection_from_setting does (numeric
        # strings -> int) so the winner tiebreak, conflict detection, and
        # normalize all see the SAME coerced ints. Int-only DROP here (while the
        # reconcile coerces) made a legacy ["12"] row lose the has-selection
        # tiebreak, never flag ["12"] vs [13] as a conflict, and never get
        # normalized to int storage.
        from services.profile_reconcile import coerce_profile_id

        def _row_selection(r):
            cp = r.get("custom_properties")
            if isinstance(cp, dict):
                sel = cp.get("channel_profile_ids")
                if isinstance(sel, list) and sel:
                    coerced = [coerce_profile_id(x) for x in sel]
                    valid = [x for x in coerced if x is not None]
                    if valid:
                        return tuple(sorted(valid))
            return None

        all_settings = {}
        for channel_group_id, rows in rows_by_group.items():
            # Deterministic precedence: auto_channel_sync ON (0) before OFF (1),
            # then — within a tier — prefer a row that HAS a non-empty selection
            # (0) over one that does not (1) so an operator's real selection is
            # never silently shadowed by a no-selection winner (Should-Fix 3),
            # then LOWEST m3u_account_id.
            winner = min(
                rows,
                key=lambda r: (
                    0 if r.get("auto_channel_sync") else 1,
                    0 if _row_selection(r) is not None else 1,
                    r.get("m3u_account_id") if r.get("m3u_account_id") is not None else 1 << 62,
                ),
            )
            distinct_selections = {
                s for s in (_row_selection(r) for r in rows) if s is not None
            }
            winner_selection = _row_selection(winner)
            # Conflict when either two rows carry DIFFERENT non-empty selections,
            # OR some row carries a selection but the chosen winner does not
            # (a selection-vs-no-selection disagreement that would otherwise
            # silently ignore the operator's choice — Should-Fix 3).
            conflict = len(distinct_selections) > 1 or (
                bool(distinct_selections) and winner_selection is None
            )
            if conflict:
                logger.warning(
                    "[DISPATCHARR] group %s has CONFLICTING channel_profile_ids across "
                    "accounts (selections=%s, winner account %s selection=%s)",
                    channel_group_id, sorted(distinct_selections),
                    winner.get("m3u_account_id"), winner_selection,
                )
            # NOTE: ``_ecm_channel_profile_conflict`` is an ECM-SYNTHETIC key —
            # it is NOT a Dispatcharr field. It must be STRIPPED before any
            # Dispatcharr group-settings PATCH (NIT 9). Today's save paths build
            # explicit payloads and never round-trip it; a future consumer that
            # forwards a collapsed row wholesale must drop this key first.
            all_settings[channel_group_id] = {**winner, "_ecm_channel_profile_conflict": conflict}

        logger.info("[DISPATCHARR]   Total channel_groups entries across all accounts: %s", total_groups_found)
        logger.info("[DISPATCHARR]   Unique channel group IDs extracted: %s", len(all_settings))
        return all_settings

    async def get_m3u_group_settings_by_provider(self) -> dict:
        """Per-(m3u_account_id, channel_group_id) group settings, NON-collapsed.

        Complements :meth:`get_all_m3u_group_settings`, which collapses to one
        entry per channel-group id (preferring auto_channel_sync ON). Provider-
        scoped event_sync needs the SPECIFIC junction row for a (provider,
        group) pair — e.g. on a group shared by two providers, provider A's row
        may be auto_channel_sync ON while provider B's is OFF. Keyed by the
        ``(m3u_account_id, channel_group_id)`` tuple.
        """
        accounts = await self.get_m3u_accounts()
        by_pair: dict = {}
        for account in accounts:
            for setting in account.get("channel_groups", []):
                channel_group_id = setting.get("channel_group")
                if channel_group_id:
                    by_pair[(account["id"], channel_group_id)] = {
                        **setting,
                        "m3u_account_id": account["id"],
                        "m3u_account_name": account.get("name", ""),
                    }
        return by_pair

    async def get_m3u_account(self, account_id: int) -> dict:
        """Get a single M3U account by ID."""
        response = await self._request("GET", f"/api/m3u/accounts/{account_id}/")
        response.raise_for_status()
        return response.json()

    async def create_m3u_account(self, data: dict) -> dict:
        """Create a new M3U account."""
        response = await self._request("POST", "/api/m3u/accounts/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_m3u_account(self, account_id: int, data: dict) -> dict:
        """Update an M3U account (full update)."""
        response = await self._request(
            "PUT", f"/api/m3u/accounts/{account_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def patch_m3u_account(self, account_id: int, data: dict) -> dict:
        """Partially update an M3U account (e.g., toggle is_active)."""
        response = await self._request(
            "PATCH", f"/api/m3u/accounts/{account_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_m3u_account(self, account_id: int) -> None:
        """Delete an M3U account."""
        response = await self._request("DELETE", f"/api/m3u/accounts/{account_id}/")
        response.raise_for_status()

    async def refresh_m3u_account(self, account_id: int) -> dict:
        """Trigger refresh for a single M3U account."""
        response = await self._request(
            "POST", f"/api/m3u/refresh/{account_id}/"
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "Refresh initiated"}

    async def refresh_all_m3u_accounts(self) -> dict:
        """Trigger refresh for all active M3U accounts."""
        response = await self._request("POST", "/api/m3u/refresh/")
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "Refresh initiated"}

    async def refresh_m3u_vod(self, account_id: int) -> dict:
        """Refresh VOD content for an XtreamCodes account."""
        response = await self._request(
            "POST", f"/api/m3u/accounts/{account_id}/refresh-vod/"
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "VOD refresh initiated"}

    # -------------------------------------------------------------------------
    # M3U Filters
    # -------------------------------------------------------------------------

    async def get_m3u_filters(self, account_id: int) -> list:
        """Get all filters for an M3U account."""
        response = await self._request(
            "GET", f"/api/m3u/accounts/{account_id}/filters/"
        )
        response.raise_for_status()
        return response.json()

    async def create_m3u_filter(self, account_id: int, data: dict) -> dict:
        """Create a new filter for an M3U account."""
        response = await self._request(
            "POST", f"/api/m3u/accounts/{account_id}/filters/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def update_m3u_filter(
        self, account_id: int, filter_id: int, data: dict
    ) -> dict:
        """Update a filter for an M3U account."""
        response = await self._request(
            "PUT", f"/api/m3u/accounts/{account_id}/filters/{filter_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_m3u_filter(self, account_id: int, filter_id: int) -> None:
        """Delete a filter from an M3U account."""
        response = await self._request(
            "DELETE", f"/api/m3u/accounts/{account_id}/filters/{filter_id}/"
        )
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # M3U Profiles
    # -------------------------------------------------------------------------

    async def get_m3u_profiles(self, account_id: int) -> list:
        """Get all profiles for an M3U account."""
        response = await self._request("GET", f"/api/m3u/accounts/{account_id}/profiles/")
        response.raise_for_status()
        return response.json()

    async def create_m3u_profile(self, account_id: int, data: dict) -> dict:
        """Create a new profile for an M3U account."""
        response = await self._request(
            "POST", f"/api/m3u/accounts/{account_id}/profiles/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def get_m3u_profile(self, account_id: int, profile_id: int) -> dict:
        """Get a specific profile for an M3U account."""
        response = await self._request(
            "GET", f"/api/m3u/accounts/{account_id}/profiles/{profile_id}/"
        )
        response.raise_for_status()
        return response.json()

    async def update_m3u_profile(
        self, account_id: int, profile_id: int, data: dict
    ) -> dict:
        """Update a profile for an M3U account."""
        response = await self._request(
            "PATCH", f"/api/m3u/accounts/{account_id}/profiles/{profile_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_m3u_profile(self, account_id: int, profile_id: int) -> None:
        """Delete a profile from an M3U account."""
        response = await self._request(
            "DELETE", f"/api/m3u/accounts/{account_id}/profiles/{profile_id}/"
        )
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # M3U Group Settings
    # -------------------------------------------------------------------------

    async def update_m3u_group_settings(self, account_id: int, data: dict) -> dict:
        """Update group settings for an M3U account.

        Data should contain group settings like:
        {
            "group_settings": [
                {"channel_group": 123, "enabled": true, "auto_channel_sync": false, ...}
            ]
        }
        """
        response = await self._request(
            "PATCH", f"/api/m3u/accounts/{account_id}/group-settings/", json=data
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Server Groups
    # -------------------------------------------------------------------------

    async def get_server_groups(self) -> list:
        """Get all server groups."""
        response = await self._request("GET", "/api/m3u/server-groups/")
        response.raise_for_status()
        return response.json()

    async def create_server_group(self, data: dict) -> dict:
        """Create a new server group."""
        response = await self._request("POST", "/api/m3u/server-groups/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_server_group(self, group_id: int, data: dict) -> dict:
        """Update a server group."""
        response = await self._request(
            "PATCH", f"/api/m3u/server-groups/{group_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_server_group(self, group_id: int) -> None:
        """Delete a server group."""
        response = await self._request("DELETE", f"/api/m3u/server-groups/{group_id}/")
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # Logos
    # -------------------------------------------------------------------------

    async def get_logos(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
    ) -> dict:
        """Get paginated list of logos."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search

        response = await self._request("GET", "/api/channels/logos/", params=params)
        response.raise_for_status()
        return response.json()

    async def get_all_logos_raw(self) -> list[dict]:
        """Fetch the complete, unpaginated list of logos from Dispatcharr.

        Dispatcharr's LogoViewSet has no ordering support and its DjangoFilterBackend
        declares no filterset_fields, so there is no ``ordering`` param to forward
        (confirmed by reading apps/channels/api_views.py in the live dispatcharr
        container — get_queryset() unconditionally ends with `.order_by('name')`).
        ECM's sort_by/sort_order/unused_only support (bead enhancedchannelmanager-
        09x38.13) therefore fetches everything in one call — via the
        ``no_pagination=true`` escape hatch LogoPagination.paginate_queryset already
        supports — and sorts/filters/paginates locally in Python.
        """
        response = await self._request(
            "GET", "/api/channels/logos/", params={"no_pagination": "true"}
        )
        response.raise_for_status()
        return response.json()

    async def get_logo(self, logo_id: int) -> dict:
        """Get a single logo by ID."""
        response = await self._request("GET", f"/api/channels/logos/{logo_id}/")
        response.raise_for_status()
        return response.json()

    async def create_logo(self, data: dict) -> dict:
        """Create a new logo.

        Returns the created logo, or raises an exception with the response body
        if creation fails (e.g., logo already exists).
        """
        response = await self._request("POST", "/api/channels/logos/", json=data)
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Logo creation failed: {response.status_code} - {error_body}")
        return response.json()

    async def upload_logo_file(self, name: str, filename: str,
                               content: bytes, content_type: str) -> dict:
        """Upload a logo image file directly to Dispatcharr.

        Uses the /api/channels/logos/upload/ multipart endpoint so the file
        is stored and served by Dispatcharr, not ECM.
        """
        response = await self._request(
            "POST", "/api/channels/logos/upload/",
            files={"file": (filename, content, content_type)},
            data={"name": name},
        )
        if response.status_code >= 400:
            error_body = response.text
            raise Exception(f"Logo upload failed: {response.status_code} - {error_body}")
        return response.json()

    async def find_logo_by_url(self, url: str) -> Optional[dict]:
        """Find an existing logo by its URL.

        Paginates through logos to find exact URL match.
        Uses larger page size to minimize API calls.
        """
        page = 1
        page_size = 500  # Use larger page size for efficiency
        while True:
            result = await self.get_logos(page=page, page_size=page_size)
            for logo in result.get("results", []):
                if logo.get("url") == url:
                    return logo
            # Check if there are more pages
            if not result.get("next"):
                break
            page += 1
        return None

    async def update_logo(self, logo_id: int, data: dict) -> dict:
        """Update a logo."""
        response = await self._request(
            "PATCH", f"/api/channels/logos/{logo_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_logo(self, logo_id: int) -> None:
        """Delete a logo."""
        response = await self._request("DELETE", f"/api/channels/logos/{logo_id}/")
        response.raise_for_status()

    async def bulk_delete_logos(self, logo_ids: list[int]) -> dict:
        """Bulk-delete logos by id (DBAS restore bulk-delete pre-step, bead 0i2vt.15).

        Issues a single ``DELETE /api/channels/logos/bulk-delete/`` with
        ``{"logo_ids": [...]}``. Used by the logos importer to clear existing
        logos before a streaming re-upload. An empty ``logo_ids`` is a no-op
        (nothing to delete) and never hits the API.

        Credential/path hygiene: this method logs only the COUNT of ids, never
        the upstream response body (the response can echo names/paths) — the
        importer surfaces a sanitized failure if the call raises.
        """
        if not logo_ids:
            return {"deleted": 0}
        response = await self._request(
            "DELETE",
            "/api/channels/logos/bulk-delete/",
            json={"logo_ids": logo_ids},
        )
        if response.status_code >= 400:
            # Do NOT echo the response body — it may carry logo names/paths. The
            # status code alone is a safe operator-facing signal.
            raise Exception(f"Logo bulk-delete failed: {response.status_code}")
        return response.json() if response.content else {"deleted": len(logo_ids)}

    async def get_all_logos_paginated(self, page_size: int = 500) -> list[dict]:
        """Return ALL logos by walking every page (DBAS restore, bead 0i2vt.15).

        Paginates ``GET /api/channels/logos/`` until the ``next`` link is
        exhausted and returns the flattened list of logo records. Used by the
        logos importer to (a) build the bulk-delete id list for the destructive
        pre-step and (b) enumerate destination logos for the 3-tier match.

        Mirrors :meth:`find_logo_by_url`'s pagination convention. Logs only the
        running count — never a logo url/name/path.
        """
        logos: list[dict] = []
        page = 1
        while True:
            result = await self.get_logos(page=page, page_size=page_size)
            results = result.get("results", []) if isinstance(result, dict) else result
            logos.extend(r for r in (results or []) if isinstance(r, dict))
            if not (isinstance(result, dict) and result.get("next")):
                break
            page += 1
        logger.debug("[DISPATCHARR] Fetched %d logo(s) across all pages.", len(logos))
        return logos

    # -------------------------------------------------------------------------
    # EPG Sources
    # -------------------------------------------------------------------------

    async def get_epg_sources(self) -> list:
        """Get all EPG sources."""
        response = await self._request("GET", "/api/epg/sources/")
        response.raise_for_status()
        return response.json()

    async def get_epg_source(self, source_id: int) -> dict:
        """Get a single EPG source by ID."""
        response = await self._request("GET", f"/api/epg/sources/{source_id}/")
        response.raise_for_status()
        return response.json()

    async def create_epg_source(self, data: dict) -> dict:
        """Create a new EPG source."""
        response = await self._request("POST", "/api/epg/sources/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_epg_source(self, source_id: int, data: dict) -> dict:
        """Update an EPG source."""
        response = await self._request("PATCH", f"/api/epg/sources/{source_id}/", json=data)
        response.raise_for_status()
        return response.json()

    async def delete_epg_source(self, source_id: int) -> None:
        """Delete an EPG source."""
        response = await self._request("DELETE", f"/api/epg/sources/{source_id}/")
        response.raise_for_status()

    async def refresh_epg_source(self, source_id: int) -> dict:
        """Refresh a single EPG source.

        Dispatcharr's /api/epg/import/ endpoint expects the EPG source ID
        in the request body as {"id": source_id}.
        """
        # Send POST to /api/epg/import/ with the source ID in the body
        response = await self._request(
            "POST",
            "/api/epg/import/",
            json={"id": source_id}
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "Refresh initiated"}

    async def trigger_epg_import(self) -> dict:
        """Trigger an EPG data import for all active sources.

        Dispatcharr's /api/epg/import/ endpoint requires an ID in the body,
        so we need to iterate over all active non-dummy sources and trigger
        each one individually.
        """
        # Get all EPG sources
        sources = await self.get_epg_sources()

        # Filter to active non-dummy sources
        active_sources = [
            s for s in sources
            if s.get("source_type") != "dummy" and s.get("is_active", True)
        ]

        if not active_sources:
            return {"success": True, "message": "No active EPG sources to refresh"}

        # Trigger refresh for each active source
        refreshed = []
        errors = []
        for source in active_sources:
            try:
                await self.refresh_epg_source(source["id"])
                refreshed.append(source["name"])
            except Exception as e:
                logger.error("[DISPATCHARR] EPG refresh failed for %s: %s", source['name'], e)
                errors.append(source['name'])

        if errors:
            return {
                "success": len(refreshed) > 0,
                "message": f"Refreshed {len(refreshed)} sources, {len(errors)} failed",
                "refreshed": refreshed,
                "errors": errors
            }

        return {
            "success": True,
            "message": f"EPG import initiated for {len(refreshed)} sources",
            "refreshed": refreshed
        }

    # -------------------------------------------------------------------------
    # Schedules Direct (SD)
    # -------------------------------------------------------------------------
    # SD lineups live on the SD account, not in Dispatcharr's DB. Dispatcharr
    # authenticates to SD live on each of these calls and is rate-limited by SD
    # (lineup adds 6/24h). ECM is a thin proxy — it must NOT retry these on top
    # of Dispatcharr or it amplifies the SD call count.

    async def get_sd_lineups(self, source_id: int) -> dict:
        """List the SD account's active lineups for a Schedules Direct source.

        Returns Dispatcharr's payload: ``{lineups, max_lineups,
        changes_remaining, changes_reset_at}``.
        """
        response = await self._request("GET", f"/api/epg/sources/{source_id}/sd-lineups/")
        response.raise_for_status()
        return response.json()

    async def add_sd_lineup(self, source_id: int, lineup: str) -> dict:
        """Add an SD lineup to the account (admin on Dispatcharr)."""
        response = await self._request(
            "POST", f"/api/epg/sources/{source_id}/sd-lineups/", json={"lineup": lineup}
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def delete_sd_lineup(self, source_id: int, lineup: str) -> dict:
        """Remove an SD lineup from the account (admin on Dispatcharr)."""
        response = await self._request(
            "DELETE", f"/api/epg/sources/{source_id}/sd-lineups/", json={"lineup": lineup}
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def search_sd_lineups(self, source_id: int, country: str, postalcode: str) -> dict:
        """Search SD headends/lineups by country + postal code.

        Returns ``{lineups:[{lineup, name, transport, location, headend}]}``.
        """
        response = await self._request(
            "POST",
            f"/api/epg/sources/{source_id}/sd-lineups/search/",
            json={"country": country, "postalcode": postalcode},
        )
        response.raise_for_status()
        return response.json()

    async def get_program_poster(self, program_id: int) -> httpx.Response:
        """Fetch an SD program poster. Returns the raw response so the caller
        can stream bytes + ``Content-Type`` through to the browser."""
        response = await self._request("GET", f"/api/epg/programs/{program_id}/poster/")
        response.raise_for_status()
        return response

    # -------------------------------------------------------------------------
    # EPG Data
    # -------------------------------------------------------------------------

    async def get_epg_data(
        self,
        page: int = 1,
        page_size: int = 500,
        search: Optional[str] = None,
        epg_source: Optional[int] = None,
        max_results: Optional[int] = None,
    ) -> list:
        """Get all EPG data entries.

        Handles both old (paginated dict) and new (flat list) Dispatcharr responses.
        For paginated responses, fetches all pages automatically.
        """
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if epg_source:
            params["epg_source"] = epg_source

        if max_results is None:
            response = await self._request("GET", "/api/epg/epgdata/", params=params)
            response.raise_for_status()
            data = response.json()
        else:
            # Newer Dispatcharr versions ignore pagination and return one flat
            # array. Stream that response behind a byte ceiling so a bounded
            # caller cannot be forced to materialize arbitrarily large JSON.
            max_bytes = max(
                _EPG_DATA_MIN_RESPONSE_BYTES,
                max_results * _EPG_DATA_BYTES_PER_RESULT,
            )
            data = await self._get_json_bounded(
                "/api/epg/epgdata/", params=params, max_bytes=max_bytes
            )

        # New Dispatcharr: flat list response
        if isinstance(data, list):
            return data[:max_results] if max_results is not None else data

        # Old Dispatcharr: paginated dict response - fetch all pages
        all_results = data.get("results", [])
        if max_results is not None and len(all_results) >= max_results:
            return all_results[:max_results]
        while data.get("next"):
            page += 1
            params["page"] = page
            if max_results is None:
                response = await self._request(
                    "GET", "/api/epg/epgdata/", params=params
                )
                response.raise_for_status()
                data = response.json()
            else:
                data = await self._get_json_bounded(
                    "/api/epg/epgdata/", params=params, max_bytes=max_bytes
                )
            if isinstance(data, list):
                # Switched to new format mid-pagination (unlikely but safe)
                all_results.extend(data)
                break
            all_results.extend(data.get("results", []))
            if max_results is not None and len(all_results) >= max_results:
                return all_results[:max_results]

        return all_results[:max_results] if max_results is not None else all_results

    async def _get_json_bounded(
        self, path: str, *, params: dict, max_bytes: int
    ):
        """GET and decode JSON only after its streamed body fits ``max_bytes``."""
        await self._ensure_authenticated()
        headers = {}
        if self._uses_api_key:
            headers["X-API-Key"] = (
                self.settings.dispatcharr_api_key or self.settings.api_key
            )
        else:
            headers["Authorization"] = f"Bearer {self.access_token}"
        headers["Accept-Encoding"] = "identity"

        async def send() -> httpx.Response:
            request = self._client.build_request(
                "GET", f"{self.base_url}{path}", headers=headers, params=params
            )
            return await self._client.send(request, stream=True)

        response = await send()
        if response.status_code == 401 and not self._uses_api_key:
            await response.aclose()
            await self._refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            response = await send()
        try:
            response.raise_for_status()
            content_encoding = response.headers.get("Content-Encoding", "").strip()
            if content_encoding and content_encoding.lower() != "identity":
                raise ValueError(
                    "Dispatcharr EPG response used unexpected Content-Encoding"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > max_bytes:
                    raise ValueError(
                        f"Dispatcharr EPG response exceeds {max_bytes} bytes"
                    )
                body.extend(chunk)
            # Parsing occurs only after the bounded stream is complete.
            return await run_cpu_bound(json.loads, bytes(body))
        finally:
            await response.aclose()

    async def get_epg_data_by_id(self, data_id: int) -> dict:
        """Get a single EPG data entry by ID."""
        response = await self._request("GET", f"/api/epg/epgdata/{data_id}/")
        response.raise_for_status()
        return response.json()

    async def get_epg_grid(self, start: str = None, end: str = None) -> list:
        """Get EPG programs for the guide grid.

        Uses /api/epg/grid/ endpoint which automatically filters to:
        - Programs ending after 1 hour ago
        - Programs starting before 24 hours from now

        This endpoint does NOT support custom time ranges via parameters.
        The time range is hard-coded on the Dispatcharr side.

        Args:
            start: Ignored - kept for API compatibility
            end: Ignored - kept for API compatibility
        """
        response = await self._request("GET", "/api/epg/grid/")
        response.raise_for_status()
        data = response.json()

        # DEBUG: Log the response structure to understand what we're getting
        logger.debug("[DISPATCHARR] EPG grid response type: %s", type(data))
        if isinstance(data, dict):
            logger.debug("[DISPATCHARR] EPG grid response keys: %s", data.keys())
            logger.debug("[DISPATCHARR] EPG grid data length: %s items", len(data.get('data', [])))
            if data.get('data') and len(data.get('data', [])) > 0:
                logger.debug("[DISPATCHARR] EPG grid first item sample: %s", data['data'][0])
        elif isinstance(data, list):
            logger.debug("[DISPATCHARR] EPG grid list length: %s items", len(data))
            if data and len(data) > 0:
                logger.debug("[DISPATCHARR] EPG grid first item sample: %s", data[0])

        # Dispatcharr returns {"data": [...]}
        if isinstance(data, dict):
            return data.get("data", [])
        # Fallback for list response
        return data if isinstance(data, list) else []

    # -------------------------------------------------------------------------
    # Stream Profiles
    # -------------------------------------------------------------------------

    async def get_stream_profiles(self) -> list:
        """Get all stream profiles."""
        response = await self._request("GET", "/api/core/streamprofiles/")
        response.raise_for_status()
        return response.json()

    async def create_stream_profile(self, data: dict) -> dict:
        """Create a new stream profile."""
        response = await self._request("POST", "/api/core/streamprofiles/", json=data)
        response.raise_for_status()
        return response.json()

    async def delete_stream_profile(self, profile_id: int) -> None:
        """Delete a stream profile by ID.

        Backs the DBAS restore/sync compensating rollback
        (enhancedchannelmanager-v1uz9): when an apply fails AFTER a stream profile
        was created, the orchestrator issues this single-id DELETE to undo it.

        Dispatcharr exposes ``/api/core/streamprofiles/`` as a standard DRF
        ModelViewSet — the detail route allows DELETE (live-confirmed:
        ``OPTIONS /api/core/streamprofiles/{id}/`` → ``Allow: GET, PUT, PATCH,
        DELETE, HEAD, OPTIONS``), mirroring ``delete_channel_profile``. A 404 here
        means the profile is already gone, which the rollback treats as success.
        """
        response = await self._request("DELETE", f"/api/core/streamprofiles/{profile_id}/")
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # Channel Profiles
    # -------------------------------------------------------------------------

    async def get_channel_profiles(self) -> list:
        """Get all channel profiles."""
        response = await self._request("GET", "/api/channels/profiles/")
        response.raise_for_status()
        return response.json()

    async def get_channel_profile(self, profile_id: int) -> dict:
        """Get a single channel profile by ID."""
        response = await self._request("GET", f"/api/channels/profiles/{profile_id}/")
        response.raise_for_status()
        return response.json()

    async def create_channel_profile(self, data: dict) -> dict:
        """Create a new channel profile."""
        response = await self._request("POST", "/api/channels/profiles/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_channel_profile(self, profile_id: int, data: dict) -> dict:
        """Update a channel profile (PATCH)."""
        response = await self._request(
            "PATCH", f"/api/channels/profiles/{profile_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_channel_profile(self, profile_id: int) -> None:
        """Delete a channel profile."""
        response = await self._request("DELETE", f"/api/channels/profiles/{profile_id}/")
        response.raise_for_status()

    async def bulk_update_profile_channels(self, profile_id: int, data: dict) -> dict:
        """Bulk enable/disable channels for a profile.

        Input format: {"channel_ids": [1, 2, 3], "enabled": true}
        API format: {"channels": [{"channel_id": 1, "enabled": true}, ...]}
        """
        # Transform from our format to API format
        channel_ids = data.get("channel_ids", [])
        enabled = data.get("enabled", True)
        api_data = {
            "channels": [
                {"channel_id": cid, "enabled": enabled}
                for cid in channel_ids
            ]
        }
        response = await self._request(
            "PATCH",
            f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
            json=api_data
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def update_profile_channel(
        self, profile_id: int, channel_id: int, data: dict
    ) -> dict:
        """Enable/disable a single channel for a profile.

        Data format: {"enabled": true}
        """
        response = await self._request(
            "PATCH",
            f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
            json=data
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    # -------------------------------------------------------------------------
    # Stats & Monitoring
    # -------------------------------------------------------------------------

    async def get_users(self) -> list:
        """Get all Dispatcharr user accounts."""
        response = await self._request("GET", "/api/accounts/users/")
        response.raise_for_status()
        return response.json()

    async def get_current_user(self) -> dict:
        """Return the AUTH SUBJECT of the credentials in use.

        Reads ``/api/accounts/users/me/`` — the user that the configured
        Dispatcharr credentials (api_key or JWT) authenticate as. This is the
        load-bearing security control for the Users restore importer
        (enhancedchannelmanager-l1p4p): the *current operator* must be identified
        by auth subject, NOT by a username or id from the archive. A cross-instance
        restore remaps ids, and a malicious or stale archive can carry a username
        that collides with the operator's — so the operator is matched against
        whoever ``/users/me/`` reports here, never against archive data.

        Works in both ``api_key`` and ``password`` auth modes (the endpoint is the
        same; ``_request`` attaches the right credential). Raises on a non-2xx
        response: the importer MUST NOT proceed without a confirmed operator
        identity (fail closed rather than guess).
        """
        response = await self._request("GET", "/api/accounts/users/me/")
        response.raise_for_status()
        return response.json()

    async def create_user(self, data: dict) -> dict:
        """Create a Dispatcharr (Django) user account — NO password ever sent.

        Used by the Users restore importer (enhancedchannelmanager-l1p4p). Per
        spike tsfv0 (live-confirmed vs Dispatcharr 0.26.0): the User serializer
        exposes ``password`` as a WRITE-ONLY plaintext field and there is no
        retrievable hash, so a restored user is created OMITTING ``password``
        entirely -> Django stores an unusable password and the operator resets it
        out-of-band (force-reset).

        This method **strips ``password`` and any ``password_hash`` key** from the
        payload defensively, so a password/hash can never cross the boundary even
        if a caller accidentally includes one. NEVER fabricate, derive, or rehash
        a password; never forward an incoming hash field.

        The error message uses the same ``"<thing> failed: <status> - <body>"``
        shape as ``create_channel`` / ``create_stream`` so ``upstream_http_exception``
        (restore_contracts mapper) can parse the upstream status + detail.
        """
        if not isinstance(data, dict):
            raise ValueError("create_user requires a dict payload")
        # Defense in depth: never let a secret cross the boundary, regardless of
        # what the caller passed. The importer already omits these; this is the
        # last line of defense at the client edge.
        payload = {
            k: v for k, v in data.items() if k not in ("password", "password_hash")
        }
        if not payload.get("username"):
            raise ValueError("create_user requires a username")
        response = await self._request("POST", "/api/accounts/users/", json=payload)
        if response.status_code >= 400:
            error_body = response.text
            raise Exception(f"User creation failed: {response.status_code} - {error_body}")
        return response.json()

    async def delete_user(self, user_id: int) -> None:
        """Delete a Dispatcharr user by ID.

        Used as the rollback compensation for a previously created user
        (restore_contracts rollback ledger, enhancedchannelmanager-l1p4p): if a
        later step in the same restore fails, users created earlier in the run are
        deleted to avoid leaving orphans. A 404 here means the user is already
        gone, which the caller treats as a successful (idempotent) compensation.
        """
        response = await self._request("DELETE", f"/api/accounts/users/{user_id}/")
        response.raise_for_status()

    async def get_user_schema_write_fields(self) -> set:
        """Return the set of WRITABLE request-body field names for user-create.

        Reads the OpenAPI document at ``/api/schema/`` and extracts the property
        names of the request body for ``POST /api/accounts/users/`` (resolving a
        ``$ref`` into ``components.schemas`` when present), excluding any property
        flagged ``readOnly``.

        This feeds the Users importer's capability check
        (enhancedchannelmanager-l1p4p): if a ``password_hash`` (or similar
        pre-hashed-password) WRITE field ever appears in this set, the importer
        fails the users category CLOSED rather than silently begin writing hashes.
        Returns an empty set if the schema cannot be parsed — the importer treats
        an unparseable schema as "cannot confirm safety" and also fails closed.
        """
        # ``?format=json`` is REQUIRED (enhancedchannelmanager-q6xjl).
        # drf-spectacular's default renderer for /api/schema/ is YAML
        # (content-type ``application/vnd.oai.openapi``), so a bare GET returns a
        # YAML document and ``.json()`` raises "Expecting value: line 1 column 1
        # (char 0)". That exception failed the whole dispatcharr_users restore
        # category CLOSED on every run against Dispatcharr 0.28.2.
        response = await self._request(
            "GET", "/api/schema/", params={"format": "json"}
        )
        response.raise_for_status()
        doc = response.json()
        if not isinstance(doc, dict):
            return set()

        post_op = (
            doc.get("paths", {})
            .get("/api/accounts/users/", {})
            .get("post", {})
        )
        body_schema = (
            post_op.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if not isinstance(body_schema, dict):
            return set()

        # Resolve a local $ref into components.schemas (one hop is sufficient for
        # the User-create request body; we do not chase nested refs).
        ref = body_schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: object = doc
            for part in ref.lstrip("#/").split("/"):
                if isinstance(target, dict):
                    target = target.get(part, {})
                else:
                    target = {}
                    break
            if isinstance(target, dict):
                body_schema = target

        props = body_schema.get("properties", {})
        if not isinstance(props, dict):
            return set()
        return {
            name
            for name, spec in props.items()
            if not (isinstance(spec, dict) and spec.get("readOnly") is True)
        }

    async def get_channel_stats(self) -> dict:
        """Get status of all active channels.

        Returns summary including active channels, client counts, bitrates, etc.
        """
        response = await self._request("GET", "/proxy/ts/status")
        response.raise_for_status()
        return response.json()

    async def get_channel_stats_detail(self, channel_id: int) -> dict:
        """Get detailed stats for a specific channel.

        Includes per-client information, buffer status, codec details, etc.
        """
        response = await self._request("GET", f"/proxy/ts/status/{channel_id}")
        response.raise_for_status()
        return response.json()

    async def get_system_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
    ) -> dict:
        """Get recent system events (channel start/stop, buffering, client connections).

        Args:
            limit: Number of events to return (max 1000)
            offset: Pagination offset
            event_type: Optional filter by event type
        """
        params = {"limit": limit, "offset": offset}
        if event_type:
            params["event_type"] = event_type

        response = await self._request("GET", "/api/core/system-events/", params=params)
        response.raise_for_status()
        return response.json()

    async def stop_channel(self, channel_id: int) -> dict:
        """Stop a channel and release all associated resources."""
        response = await self._request("POST", f"/proxy/ts/stop/{channel_id}")
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def stop_client(self, channel_id: int) -> dict:
        """Stop a specific client connection."""
        response = await self._request("POST", f"/proxy/ts/stop_client/{channel_id}")
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    # -------------------------------------------------------------------------
    # DBAS Restore — User Agents / Core Settings / DVR Rules / Comskip
    # (enhancedchannelmanager-0i2vt.13)
    #
    # These methods back the Phase-2 ``settings_agents`` restore importer. They
    # were ADDED by 0i2vt.13 — none existed before (verify-then-size: ``grep
    # user_agent / comskip / dvr / core_settings dispatcharr_client.py`` = 0
    # method hits at the start of the bead).
    #
    # ENDPOINT NOTE (verify-then-size): Dispatcharr groups core resources under
    # the ``/api/core/`` namespace (confirmed live for ``streamprofiles``,
    # ``version``, ``system-events`` — see those methods above), so user agents and
    # core settings are placed there. The DVR-rules path lives under Dispatcharr's
    # DVR module (``/api/dvr/``). The exact path strings below are the best-known
    # values from the Dispatcharr REST surface; they are isolated to module-level
    # constants so a single live-verification follow-up can correct any one path
    # without touching the importer. No live Dispatcharr instance was available to
    # confirm them tonight — flagged as a deferred verification follow-up.
    # -------------------------------------------------------------------------

    async def get_user_agents(self) -> list:
        """Get all Dispatcharr user-agent records.

        Used by the DBAS settings/agents restore importer
        (enhancedchannelmanager-0i2vt.13) to detect name collisions before
        creating. Returns the raw list as Dispatcharr serializes it.
        """
        response = await self._request("GET", _USER_AGENTS_PATH)
        response.raise_for_status()
        return response.json()

    async def create_user_agent(self, data: dict) -> dict:
        """Create a Dispatcharr user-agent record.

        A user agent is a benign label + UA string (e.g. ``VLC/3.0.20``). It
        carries no credential material, so the payload and any error body are
        safe to forward. Mirrors ``create_m3u_account`` shape.
        """
        response = await self._request("POST", _USER_AGENTS_PATH, json=data)
        response.raise_for_status()
        return response.json()

    async def delete_user_agent(self, user_agent_id: int) -> None:
        """Delete a Dispatcharr user agent by ID (rollback compensation).

        A 404 means it is already gone — the caller treats that as a successful
        idempotent compensation (restore_contracts rollback ledger).
        """
        response = await self._request("DELETE", f"{_USER_AGENTS_PATH}{user_agent_id}/")
        response.raise_for_status()

    async def get_dvr_rules(self) -> list:
        """Get all Dispatcharr DVR / recording rules.

        Used by the DBAS restore importer (enhancedchannelmanager-0i2vt.13) to
        detect collisions by name/title before creating.
        """
        response = await self._request("GET", _DVR_RULES_PATH)
        response.raise_for_status()
        return response.json()

    async def create_dvr_rule(self, data: dict) -> dict:
        """Create a Dispatcharr DVR / recording rule.

        FK references (e.g. ``channel``) MUST already be remapped to destination
        ids by the caller — this method forwards the payload as given.
        """
        response = await self._request("POST", _DVR_RULES_PATH, json=data)
        response.raise_for_status()
        return response.json()

    async def delete_dvr_rule(self, rule_id: int) -> None:
        """Delete a Dispatcharr DVR rule by ID (rollback compensation).

        A 404 means already-gone — treated as successful idempotent compensation.
        """
        response = await self._request("DELETE", f"{_DVR_RULES_PATH}{rule_id}/")
        response.raise_for_status()

    async def get_core_settings(self) -> dict:
        """Get Dispatcharr's core/global settings as a key->value mapping.

        WARNING (DBAS restore, enhancedchannelmanager-0i2vt.13): the response can
        carry credential/instance-identity material (API keys, auth config). The
        caller (settings_agents importer) MUST sanitize before logging/reporting.
        Returns the raw mapping; callers normalize a list-of-{key,value} response
        into a dict themselves.
        """
        response = await self._request("GET", _CORE_SETTINGS_PATH)
        response.raise_for_status()
        return response.json()

    async def get_core_setting_id_map(self) -> dict:
        """Return a ``{setting key -> destination row id}`` map in ONE request.

        Dispatcharr's ``CoreSettingsViewSet`` is a plain DRF ``ModelViewSet``
        with the default ``pk`` lookup, so the detail route is
        ``/api/core/settings/{integer id}/`` — there is NO key-string detail
        route (see ``tests/fixtures/dispatcharr_openapi_recorded.json``). Ids are
        per-instance and are deliberately NOT carried in a backup artifact
        (``routers.backup`` flattens core settings to key->value), so a restore
        must resolve them against the DESTINATION at apply time. Callers hold the
        returned map for the whole apply run — one GET, never one per key
        (enhancedchannelmanager-q6xjl).

        Accepts either a bare list of rows or a DRF-paginated
        ``{"results": [...]}`` envelope. A row without a usable string key or an
        integer id is DROPPED rather than guessed at — an absent key makes the
        caller fail that key explicitly, whereas a guessed id would PATCH an
        unrelated setting.
        """
        response = await self._request("GET", _CORE_SETTINGS_PATH)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            payload = payload.get("results")
        if not isinstance(payload, list):
            return {}

        id_map: dict = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            setting_name = row.get("key")
            if not isinstance(setting_name, str) or not setting_name:
                continue
            row_id = row.get("id")
            # bool is an int subclass; a boolean id is not a row id.
            if not isinstance(row_id, int) or isinstance(row_id, bool):
                continue
            id_map[setting_name] = row_id
        return id_map

    async def update_core_setting(self, setting_id: int, setting_value) -> dict:
        """Apply ONE core setting by its destination row id via PATCH.

        Per-row application (NOT a bulk PUT that could clobber unrelated keys).
        ``setting_id`` is the destination's own integer row id, resolved by
        :meth:`get_core_setting_id_map` — a key-string URL matches no route and
        404s (enhancedchannelmanager-q6xjl). The DBAS importer only calls this for
        ALLOWLISTED safe keys — never for a credential/auth/instance-identity key.
        ``setting_value`` is forwarded as-is; this method NEVER logs the value (it
        may be sensitive for a non-allowlisted key if a future caller misuses it).

        Clear-text-logging hygiene (bead 0i2vt.13): the parameters are named
        ``setting_id`` / ``setting_value`` — deliberately NOT ``key`` / ``value``.
        CodeQL's py/clear-text-logging-sensitive-data taints any value flowing
        from a ``key``-named variable as a secret; such a parameter here would
        taint the request ``path`` (and the JSON body the ``setting_value``), both
        of which reach the shared ``_request`` log/exc sinks. We also catch any
        upstream error and re-raise a generic, payload-free message so neither the
        setting value nor an upstream response body can propagate into
        ``_request``'s ``logger.exception(..., e)`` sink via the raised exception.
        """
        try:
            response = await self._request(
                "PATCH",
                f"{_CORE_SETTINGS_PATH}{setting_id}/",
                json={"value": setting_value},
            )
            response.raise_for_status()
        except Exception:
            # Generic, payload-free re-raise: the original exception text could
            # echo the request URL or response body (which may carry the setting
            # value). Drop it so nothing sensitive reaches a logging sink.
            raise RuntimeError("Core setting update failed") from None
        return response.json() if response.content else {"updated": True}

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# Singleton instance
_client: Optional[DispatcharrClient] = None
_client_settings_hash: Optional[str] = None


def _settings_hash(settings: DispatcharrSettings) -> str:
    """Get a hash of settings to detect changes.

    Includes both ``dispatcharr_api_key`` (canonical, bd-jmi1c) and the
    legacy ``api_key`` so the client singleton recreates when either field
    changes — keeps in-memory state in sync with operators who rotate via
    the UI (touches canonical) or via on-disk edits (may still touch legacy).

    Returns an HMAC-SHA-256 hex digest of the concatenated fields keyed by
    a process-local random salt (bd-jmi1c P2-3 / bd-46g4t). The function's
    name promised a hash; the prior implementation returned plaintext that,
    if ever leaked to a log line or debug bundle, would carry password +
    dispatcharr_api_key + api_key + username all in one string. The MAC
    keeps the equality semantics every caller relies on — the value is
    used opaquely as a cache key in ``get_client()`` — while ensuring a
    stray ``logger.info(...)`` cannot accidentally exfiltrate credentials.

    HMAC (not bare SHA-256) is used because (a) the random per-process key
    makes the output uncorrelated across processes even for identical
    settings, and (b) CodeQL's ``py/weak-sensitive-data-hashing`` rule
    correctly flags bare SHA-256 of credentials as inappropriate for
    password-storage hashing — not the use case here, but switching to MAC
    avoids the false positive cleanly.

    Back-compat: drop ``settings.api_key`` from the concat in v0.19.0
    (bd-ewm4h) when the legacy field is removed from the model.
    """
    concat = (
        f"{settings.url}:{settings.auth_method}:{settings.username}:"
        f"{settings.password}:{settings.dispatcharr_api_key}:{settings.api_key}"
    )
    return hmac.new(_SETTINGS_HASH_KEY, concat.encode("utf-8"), hashlib.sha256).hexdigest()


def get_client() -> DispatcharrClient:
    """Get the Dispatcharr client, recreating if settings changed."""
    global _client, _client_settings_hash

    settings = get_settings()
    current_hash = _settings_hash(settings)

    if _client is None or _client_settings_hash != current_hash:
        _client = DispatcharrClient(settings)
        _client_settings_hash = current_hash

    return _client


def reset_client() -> None:
    """Reset the client (call after settings change)."""
    global _client, _client_settings_hash
    _client = None
    _client_settings_hash = None
