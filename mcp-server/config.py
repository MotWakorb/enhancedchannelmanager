"""Configuration for ECM MCP server.

Reads the MCP API key from the shared /config/settings.json volume
and ECM connection details from environment variables.
"""
import ipaddress
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# ECM backend URL (internal Docker network)
ECM_URL = os.environ.get("ECM_URL", "http://ecm:6100")

# MCP server port
MCP_PORT = int(os.environ.get("MCP_PORT", "6101"))
MCP_BIND_ADDRESS = os.environ.get("MCP_BIND_ADDRESS", "127.0.0.1")


def _environment_boolean(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


MCP_REQUIRE_HTTPS = _environment_boolean("MCP_REQUIRE_HTTPS")


def get_mcp_trusted_proxy_ips(raw: str | None = None) -> str:
    """Validate Uvicorn's comma-separated forwarded-header trust boundary."""
    configured = (
        os.environ.get("MCP_TRUSTED_PROXY_IPS", "127.0.0.1")
        if raw is None
        else raw
    )
    entries = configured.split(",")
    if not entries or any(not entry.strip() for entry in entries):
        raise ValueError("MCP_TRUSTED_PROXY_IPS contains an empty entry")

    normalized: list[str] = []
    for entry in entries:
        value = entry.strip()
        try:
            if "/" in value:
                network = ipaddress.ip_network(value, strict=True)
                if network.prefixlen == 0:
                    raise ValueError("trust-all networks are forbidden")
                safe_value = str(network)
            else:
                safe_value = str(ipaddress.ip_address(value))
        except ValueError as exc:
            raise ValueError(
                f"Invalid MCP_TRUSTED_PROXY_IPS entry: {entry!r}"
            ) from exc
        if safe_value not in normalized:
            normalized.append(safe_value)
    return ",".join(normalized)


MCP_TRUSTED_PROXY_IPS = get_mcp_trusted_proxy_ips()

_DEFAULT_MCP_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]", "ecm-mcp")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_mcp_allowed_host(value: str) -> str:
    """Validate one hostname/IP with no scheme, path, wildcard, or port."""
    host = value.strip().lower()
    if not host:
        raise ValueError("MCP_ALLOWED_HOSTS contains an empty host")

    # Accept bracketed IPv6 literals in the exact form carried by Host.
    if host.startswith("[") and host.endswith("]"):
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError as exc:
            raise ValueError(f"Invalid MCP_ALLOWED_HOSTS entry: {value!r}") from exc
        return host

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.rstrip(".").split(".")
        if (
            host.endswith(".")
            or len(host) > 253
            or not labels
            or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
        ):
            raise ValueError(f"Invalid MCP_ALLOWED_HOSTS entry: {value!r}")
        return host

    if address.version == 6:
        raise ValueError(
            "IPv6 MCP_ALLOWED_HOSTS entries must be bracketed, for example [::1]"
        )
    return str(address)


def get_mcp_allowed_hosts(raw: str | None = None) -> tuple[str, ...]:
    """Return safe defaults plus comma-separated operator-configured hosts.

    Entries intentionally omit ports. The server derives exact and any-port
    variants for the MCP SDK while Starlette validates the hostname itself.
    A permissive ``*`` is not supported because it disables DNS-rebinding
    protection rather than configuring it.
    """
    configured = os.environ.get("MCP_ALLOWED_HOSTS", "") if raw is None else raw
    additional = [] if not configured.strip() else configured.split(",")
    result: list[str] = list(_DEFAULT_MCP_ALLOWED_HOSTS)
    for item in additional:
        host = normalize_mcp_allowed_host(item)
        if host not in result:
            result.append(host)
    return tuple(result)


MCP_ALLOWED_HOSTS = get_mcp_allowed_hosts()


def get_mcp_allowed_origins(raw: str | None = None) -> tuple[str, ...]:
    """Return exact browser origins allowed to reach MCP.

    Non-browser MCP clients normally omit Origin. When a browser supplies one,
    it must match this list exactly; wildcards are deliberately unsupported.
    """
    configured = os.environ.get("MCP_ALLOWED_ORIGINS", "") if raw is None else raw
    defaults = (
        "http://localhost",
        "https://localhost",
        f"http://localhost:{MCP_PORT}",
        f"https://localhost:{MCP_PORT}",
        "http://127.0.0.1",
        "https://127.0.0.1",
        f"http://127.0.0.1:{MCP_PORT}",
        f"https://127.0.0.1:{MCP_PORT}",
        "http://[::1]",
        "https://[::1]",
        f"http://[::1]:{MCP_PORT}",
        f"https://[::1]:{MCP_PORT}",
    )
    result = list(defaults)
    for item in ([] if not configured.strip() else configured.split(",")):
        origin = item.strip()
        parsed = urlsplit(origin)
        try:
            _validated_port = parsed.port
        except ValueError as exc:
            raise ValueError(
                f"Invalid MCP_ALLOWED_ORIGINS entry: {item!r}"
            ) from exc
        if (
            not origin
            or origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in origin)
        ):
            raise ValueError(f"Invalid MCP_ALLOWED_ORIGINS entry: {item!r}")
        if origin not in result:
            result.append(origin)
    return tuple(result)


MCP_ALLOWED_ORIGINS = get_mcp_allowed_origins()


def get_mcp_api_key() -> str:
    """Read the MCP API key from the shared settings.json file.

    Re-reads from disk on every call so key rotation takes effect
    without restarting the MCP container.
    """
    # Single source of truth: defer to the status-aware helper and discard the
    # status. Keeps two read paths from drifting apart (bd-ix1g6).
    key, _ = get_mcp_api_key_status()
    return key


def get_mcp_api_key_status() -> tuple[str, str]:
    """Read the MCP API key and classify the read outcome (bd-ix1g6).

    Returns a ``(key, status)`` tuple, where ``status`` is one of:

      ``"ok"``             — file exists, JSON valid, ``mcp_api_key`` present, non-empty.
      ``"file_not_found"`` — ``/config/settings.json`` does not exist on the mounted volume.
                              Most common deployment misconfiguration signature:
                              MCP container's ``/config`` mount is empty, points
                              at a different volume than ECM, or ECM has never
                              written settings.json (user has never hit Save).
      ``"invalid_json"``   — file exists but is not valid JSON (corrupted /
                              partially-written / unrelated file at that path).
      ``"field_missing"``  — JSON valid but does not contain ``mcp_api_key``
                              (legacy settings.json predating the MCP feature,
                              never re-saved). Equivalent to "field empty" in
                              effect but a distinct symptom to report.
      ``"field_empty"``    — ``mcp_api_key`` present in the JSON but value is
                              an empty string (key was revoked, or never
                              generated since ECM upgraded).

    The pre-bd-ix1g6 ``get_mcp_api_key()`` collapsed all four failure modes
    into a single empty-string return, making it impossible for an operator
    to diagnose ``/health`` reporting ``api_key_configured: false`` without
    container shell access. This helper preserves that single-string return
    on the original API while letting ``/health`` surface the underlying
    cause to the operator-facing UI.
    """
    if not SETTINGS_FILE.exists():
        logger.warning("[MCP-CONFIG] Settings file not found at %s", SETTINGS_FILE)
        return "", "file_not_found"

    try:
        raw = SETTINGS_FILE.read_text()
    except Exception as e:
        # Permission denied / IO error reads as a file-read failure. We surface
        # this as invalid_json rather than introducing a fifth status code —
        # the user-facing remediation (re-mount, restart container) is the same
        # and the log line below carries the specific exception class for
        # operators who do have container access.
        logger.error("[MCP-CONFIG] Failed to read settings file: %s", e)
        return "", "invalid_json"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[MCP-CONFIG] settings.json is not valid JSON: %s", e)
        return "", "invalid_json"

    if "mcp_api_key" not in data:
        return "", "field_missing"

    key = data["mcp_api_key"] or ""
    if not key:
        return "", "field_empty"
    return key, "ok"
