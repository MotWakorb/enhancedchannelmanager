"""Configuration for ECM MCP server.

Reads MCP credentials from a dedicated, read-only projection directory that
contains nothing but MCP key material, and ECM connection details from
environment variables. The sidecar never mounts ECM's ``/config`` volume.
"""
import ipaddress
import json
import logging
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Dedicated credential projection (enhancedchannelmanager-04c0u.8). ECM writes
# only MCP key material here; the sidecar mounts this directory read-only and
# has no access to settings.json, auth_settings.json, the journal, TLS keys, or
# backups.
#
# The CONFIG_DIR fallback covers a version-skewed deployment, and covers it
# only partly. ``mcp-service.json`` HAS lived in CONFIG_DIR since
# …-04c0u.7, so a .8 sidecar pointed at CONFIG_DIR still finds the private
# projection a pre-.8 backend wrote. ``api-key`` is new in .8 — a pre-.8
# backend never wrote it, it kept the public key inside settings.json — so
# against a pre-.8 backend the public key resolves to ``file_not_found`` until
# that backend is upgraded. Upgrade the backend, not just the sidecar.
#
# ``or`` rather than a ``get`` default so an explicitly empty MCP_SECRETS_DIR=
# in an .env resolves to CONFIG_DIR instead of Path("") -> the process CWD;
# backend/config.py resolves the same variable the same way.
MCP_SECRETS_DIR = Path(
    os.environ.get("MCP_SECRETS_DIR") or os.environ.get("CONFIG_DIR", "/config")
)
# Public client credential — the key operators paste into MCP clients.
_PUBLIC_KEY_FILENAME = "api-key"
MCP_KEY_FILE = MCP_SECRETS_DIR / _PUBLIC_KEY_FILENAME
# Private backend principal key + destructive-confirmation signing key. These
# are separate secrets from the public key above and from each other
# (enhancedchannelmanager-04c0u.7); the sidecar refuses a projection it does
# not own or that is not owner-only.
MCP_SERVICE_FILE = MCP_SECRETS_DIR / "mcp-service.json"

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
    """Read the public MCP client key from the dedicated credential file.

    Re-reads from disk on every call so key rotation takes effect
    without restarting the MCP container.
    """
    # Single source of truth: defer to the status-aware helper and discard the
    # status. Keeps two read paths from drifting apart (bd-ix1g6).
    key, _ = get_mcp_api_key_status()
    return key


def get_mcp_backend_credentials() -> tuple[str, str]:
    """Read the owner-only sidecar projection used for backend calls.

    This is deliberately separate from ``mcp_api_key``: the latter is an
    operator-disclosed client credential and must never authenticate directly
    to the ECM backend. Re-reading supports backend-side atomic rotation.
    """
    try:
        data = json.loads(MCP_SERVICE_FILE.read_text())
        backend_key = str(data.get("backend_key") or "")
        confirmation_key = str(data.get("confirmation_key") or "")
    except (OSError, json.JSONDecodeError, TypeError):
        return "", ""
    return backend_key, confirmation_key


def get_mcp_backend_credentials_status() -> str:
    """Return non-secret readiness for the private backend projection."""
    try:
        metadata = MCP_SERVICE_FILE.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return "invalid_file"
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            return "insecure_permissions"
        if metadata.st_uid != os.geteuid():
            return "wrong_owner"
        data = json.loads(MCP_SERVICE_FILE.read_text())
    except FileNotFoundError:
        return "file_not_found"
    except PermissionError:
        return "unreadable"
    except (OSError, json.JSONDecodeError):
        return "invalid_file"
    if not isinstance(data, dict) or set(data) != {"backend_key", "confirmation_key"}:
        return "invalid_schema"
    backend_key = data.get("backend_key")
    confirmation_key = data.get("confirmation_key")
    if (
        not isinstance(backend_key, str)
        or not isinstance(confirmation_key, str)
        or len(backend_key) < 32
        or len(confirmation_key) < 32
        or backend_key == confirmation_key
    ):
        return "invalid_credentials"
    return "ok"


def get_mcp_api_key_status() -> tuple[str, str]:
    """Read the MCP API key and classify the read outcome (bd-ix1g6).

    Returns a ``(key, status)`` tuple, where ``status`` is one of:

      ``"ok"``             — file exists and contains one non-empty line.
      ``"file_not_found"`` — the credential projection does not exist.
      ``"invalid_key"``    — the projection is unreadable or contains multiple lines.
      ``"field_empty"``    — the projected credential is empty (revoked/unconfigured).

    The pre-bd-ix1g6 ``get_mcp_api_key()`` collapsed every failure mode
    into a single empty-string return, making it impossible for an operator
    to diagnose ``/health`` reporting ``api_key_configured: false`` without
    container shell access. This helper preserves that single-string return
    on the original API while letting ``/health`` surface the underlying
    cause to the operator-facing UI.
    """
    try:
        metadata = MCP_KEY_FILE.stat()
    except FileNotFoundError:
        # The projection path is deliberately NOT interpolated into this
        # message. It is a filesystem path rather than credential material, so
        # logging it is not a real disclosure — but it is derived from the
        # MCP_SECRETS_DIR environment variable, which makes it a
        # clear-text-logging finding on every scan (CodeQL alert 1894,
        # py/clear-text-logging-sensitive-data). The operator configured the
        # path, so naming the variable is as actionable and taints nothing.
        logger.warning(
            "[MCP-CONFIG] Credential projection %s not found under the "
            "configured MCP_SECRETS_DIR mount",
            _PUBLIC_KEY_FILENAME,
        )
        return "", "file_not_found"
    except OSError as exc:
        logger.error("[MCP-CONFIG] Failed to inspect credential projection: %s", exc)
        return "", "invalid_key"

    # Same validation the private projection gets at
    # get_mcp_backend_credentials_status(): regular file, owner-only, owned by
    # this process. The two files are siblings in one directory written by the
    # same producer under the same rules, so a reader is entitled to assume
    # they are validated identically. Every rejection maps onto invalid_key —
    # /health's documented "unreadable or malformed projection" — rather than
    # widening the operator-facing status vocabulary; its setup_hint already
    # names the PUID/PGID mismatch that produces a wrong owner.
    if not stat.S_ISREG(metadata.st_mode):
        logger.error("[MCP-CONFIG] Credential projection is not a regular file")
        return "", "invalid_key"
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        logger.error(
            "[MCP-CONFIG] Credential projection is not owner-only (mode %o)",
            stat.S_IMODE(metadata.st_mode),
        )
        return "", "invalid_key"
    if metadata.st_uid != os.geteuid():
        logger.error(
            "[MCP-CONFIG] Credential projection is owned by uid %d, not %d — "
            "ECM and the sidecar must share PUID/PGID",
            metadata.st_uid,
            os.geteuid(),
        )
        return "", "invalid_key"

    try:
        raw = MCP_KEY_FILE.read_text()
    except Exception as e:
        # Permission denied / IO error reads as a projection-read failure. The
        # user-facing remediation (re-mount, regenerate) is the same, while the
        # log line carries the specific exception class for operators.
        logger.error("[MCP-CONFIG] Failed to read credential projection: %s", e)
        return "", "invalid_key"

    lines = raw.splitlines()
    if len(lines) > 1:
        logger.error("[MCP-CONFIG] Credential projection contains multiple lines")
        return "", "invalid_key"
    key = lines[0] if lines else ""
    if not key:
        return "", "field_empty"
    return key, "ok"
