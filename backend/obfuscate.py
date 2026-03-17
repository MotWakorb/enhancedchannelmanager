"""
URL and text obfuscation utilities for debug bundles.

Redacts hostnames, IP addresses, and credentials from URLs and free text
so diagnostic data can be shared safely.
"""
import re
from urllib.parse import urlparse, urlunparse


# XtreamCodes path pattern: /user/pass/id.ext (3 segments, last is numeric + extension)
_XC_PATH_RE = re.compile(r"^/[^/]+/[^/]+/\d+\.\w+$")

# IP address pattern
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# URL pattern for matching in free text (generous but avoids trailing punctuation)
_URL_RE = re.compile(r"https?://[^\s\"'<>\])}]+")


def obfuscate_url(url: str) -> str:
    """Obfuscate a single URL by replacing hostname and credentials.

    - Hostname/IP replaced with ``example.com``, port with ``80``
    - XtreamCodes paths (``/user/pass/id.ts``) have credentials replaced
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if not parsed.scheme or not parsed.hostname:
        return url

    # Replace host and port
    netloc = "example.com:80" if parsed.port else "example.com"

    # Obfuscate XtreamCodes credentials in path
    path = parsed.path
    if _XC_PATH_RE.match(path):
        parts = path.strip("/").split("/")
        # parts = [user, pass, id.ext]
        parts[0] = "user"
        parts[1] = "pass"
        path = "/" + "/".join(parts)

    return urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


def obfuscate_text(text: str) -> str:
    """Obfuscate IPs and URLs in free-form text (e.g. log lines).

    - IP addresses replaced with ``[REDACTED_IP]``
    - URLs run through :func:`obfuscate_url`
    """
    # Replace URLs first (they contain IPs that we don't want double-replaced)
    def _replace_url(match):
        return obfuscate_url(match.group(0))

    text = _URL_RE.sub(_replace_url, text)

    # Replace any remaining bare IP addresses
    text = _IP_RE.sub("[REDACTED_IP]", text)

    return text
