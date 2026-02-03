"""
ACME challenge handlers for HTTP-01 and DNS-01 validation.

This module manages pending challenges and provides utilities for
serving HTTP-01 challenges and verifying DNS-01 records.
"""
import asyncio
import logging
from typing import Optional, Dict

import httpx


logger = logging.getLogger(__name__)


# Global storage for pending HTTP-01 challenges
# Maps token -> key_authorization
_pending_http_challenges: Dict[str, str] = {}


def register_http_challenge(token: str, key_authorization: str) -> None:
    """
    Register an HTTP-01 challenge for serving.

    Args:
        token: The challenge token
        key_authorization: The key authorization to serve
    """
    _pending_http_challenges[token] = key_authorization
    logger.info(f"Registered HTTP-01 challenge: {token}")


def get_http_challenge_response(token: str) -> Optional[str]:
    """
    Get the response for an HTTP-01 challenge.

    Args:
        token: The challenge token from the URL

    Returns:
        The key authorization string, or None if not found
    """
    return _pending_http_challenges.get(token)


def clear_http_challenge(token: str) -> None:
    """
    Clear a completed HTTP-01 challenge.

    Args:
        token: The challenge token to clear
    """
    if token in _pending_http_challenges:
        del _pending_http_challenges[token]
        logger.info(f"Cleared HTTP-01 challenge: {token}")


def clear_all_http_challenges() -> None:
    """Clear all pending HTTP-01 challenges."""
    _pending_http_challenges.clear()
    logger.info("Cleared all HTTP-01 challenges")


def get_pending_challenge_count() -> int:
    """Get the number of pending HTTP-01 challenges."""
    return len(_pending_http_challenges)


async def verify_http_challenge_reachable(
    domain: str,
    token: str,
    expected_response: str,
    timeout: float = 10.0,
) -> tuple[bool, Optional[str]]:
    """
    Verify that an HTTP-01 challenge is reachable from the internet.

    This is a self-test to check if the challenge endpoint is working
    before telling the ACME server to validate.

    Args:
        domain: The domain being validated
        token: The challenge token
        expected_response: The expected key authorization
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success, error_message)
    """
    url = f"http://{domain}/.well-known/acme-challenge/{token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, follow_redirects=False)

            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code} (expected 200)"

            content = resp.text.strip()
            if content != expected_response:
                return False, "Response does not match expected key authorization"

            return True, None

    except httpx.TimeoutException:
        return False, f"Timeout connecting to {url}"
    except httpx.ConnectError as e:
        return False, f"Connection error: {e}"
    except Exception as e:
        return False, f"Error: {e}"


async def verify_dns_challenge(
    domain: str,
    expected_value: str,
    timeout: float = 10.0,
) -> tuple[bool, Optional[str]]:
    """
    Verify that a DNS-01 challenge TXT record exists.

    Args:
        domain: The domain being validated
        expected_value: The expected TXT record value
        timeout: Timeout for DNS lookup

    Returns:
        Tuple of (success, error_message)
    """
    import socket

    txt_name = f"_acme-challenge.{domain}"

    try:
        # Use DNS-over-HTTPS for reliable external lookup
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Use Google's DNS-over-HTTPS
            resp = await client.get(
                "https://dns.google/resolve",
                params={"name": txt_name, "type": "TXT"},
            )
            data = resp.json()

            if data.get("Status") != 0:
                return False, f"DNS lookup failed: status {data.get('Status')}"

            answers = data.get("Answer", [])
            for answer in answers:
                if answer.get("type") == 16:  # TXT record
                    # TXT records come quoted
                    value = answer.get("data", "").strip('"')
                    if value == expected_value:
                        return True, None

            found_values = [
                a.get("data", "").strip('"')
                for a in answers
                if a.get("type") == 16
            ]
            if found_values:
                return False, f"TXT record found but value doesn't match. Found: {found_values}"
            return False, f"No TXT record found for {txt_name}"

    except Exception as e:
        return False, f"DNS lookup error: {e}"


class HTTPChallengeServer:
    """
    Standalone HTTP server for ACME HTTP-01 challenges.

    Used when the main application runs on a different port (e.g., 6100)
    and we need to serve challenges on port 80.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 80):
        """
        Initialize the challenge server.

        Args:
            host: Host to bind to
            port: Port to bind to (usually 80 for HTTP-01)
        """
        self.host = host
        self.port = port
        self._server = None
        self._server_task = None

    async def start(self) -> bool:
        """Start the HTTP challenge server."""
        try:
            from aiohttp import web

            app = web.Application()
            app.router.add_get(
                "/.well-known/acme-challenge/{token}",
                self._handle_challenge,
            )

            runner = web.AppRunner(app)
            await runner.setup()

            self._server = web.TCPSite(runner, self.host, self.port)
            await self._server.start()

            logger.info(f"HTTP challenge server started on {self.host}:{self.port}")
            return True

        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning(
                    f"Port {self.port} already in use. "
                    "Assuming main app or reverse proxy will handle challenges."
                )
                return True
            logger.error(f"Failed to start challenge server: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to start challenge server: {e}")
            return False

    async def stop(self) -> None:
        """Stop the HTTP challenge server."""
        if self._server:
            await self._server.stop()
            self._server = None
            logger.info("HTTP challenge server stopped")

    async def _handle_challenge(self, request) -> "web.Response":
        """Handle an ACME challenge request."""
        from aiohttp import web

        token = request.match_info["token"]
        response = get_http_challenge_response(token)

        if response:
            logger.info(f"Serving challenge response for token: {token}")
            return web.Response(text=response, content_type="text/plain")
        else:
            logger.warning(f"Challenge not found for token: {token}")
            raise web.HTTPNotFound()
