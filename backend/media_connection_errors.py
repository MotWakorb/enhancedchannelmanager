"""Safe diagnostics and public categories for media connection tests (m8dvz)."""

import json
import logging
import re
import ssl
import traceback
import xml.etree.ElementTree as ET

import httpx

from obfuscate import REDACTED, obfuscate_text
from tls.redaction import redact_secret_values

logger = logging.getLogger(__name__)

_MEDIA_HEADER = re.compile(
    r"(\b(?:X-Emby-Token|X-Plex-Token|Authorization)[\"']?[ \t]*:[ \t]*)[^\r\n]*",
    re.IGNORECASE,
)


def redact_media_diagnostic(text: str, api_key: str) -> str:
    """Scrub media headers and known values before the shared URL controls.

    Header dumps are untrusted: discard the rest of their physical line, not
    just the first word (MediaBrowser carries quoted token parameters). Short
    keys are covered structurally, never swept out of unrelated prose.
    """
    text = _MEDIA_HEADER.sub(lambda match: match[1] + REDACTED, text)
    if len(api_key) >= 4:
        # Literal characters or UTF-8 percent bytes, with case-insensitive hex
        # only. Repeated URL quoting adds "25" at each percent, at any depth.
        # Escape every literal: a credential must never supply regex syntax.
        pattern = "".join(
            "(?:" + re.escape(char) + "|" + "".join(
                rf"%(?:25)*(?i:{re.escape(f'{byte:02x}')})" for byte in char.encode("utf-8")
            ) + ")"
            for char in api_key
        )
        text = re.sub(pattern, lambda match: REDACTED, text)
    text = obfuscate_text(text, secrets=frozenset({api_key}) - {""})
    # The shared Authorization sweep otherwise consumes subsequent traceback
    # lines as part of an unquoted header. Keep that sweep line-local here.
    return "\n".join(
        redact_secret_values(line, (api_key,)) for line in text.split("\n")
    )


def media_connection_error(exc: Exception, api_key: str) -> str:
    """Log sanitized cause detail; return only fixed operator-safe messages."""
    logger.info(
        "[SETTINGS-TEST] Media connection failed: %s",
        redact_media_diagnostic("".join(traceback.format_exception(exc)), api_key),
    )
    causes: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in {id(c) for c in causes}:
        causes.append(current)
        current = current.__cause__ or current.__context__
    # TLS is commonly nested below both httpx and httpcore ConnectError.
    if any(isinstance(c, ssl.SSLError) for c in causes):
        return "TLS connection failed. Check the media server certificate."
    if any(isinstance(c, (httpx.TimeoutException, TimeoutError)) for c in causes):
        return "Connection timed out. Check the media server and try again."
    for cause in causes:
        if isinstance(cause, httpx.HTTPStatusError):
            if cause.response.status_code in (401, 403):
                return "Authentication failed. Check the media server credentials."
            return "Media server returned an upstream error status."
    if any(isinstance(c, (
        json.JSONDecodeError, ET.ParseError,
        httpx.DecodingError, httpx.RemoteProtocolError,
    )) for c in causes):
        return "Media server returned a malformed response."
    if any(isinstance(c, (httpx.NetworkError, ConnectionError)) for c in causes):
        return "Media server is unreachable. Check the address and network."
    return "Connection test failed. Check server logs for details."
