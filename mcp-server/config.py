"""Configuration for ECM MCP server.

Reads the MCP API key from the shared /config/settings.json volume
and ECM connection details from environment variables.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# ECM backend URL (internal Docker network)
ECM_URL = os.environ.get("ECM_URL", "http://ecm:6100")

# MCP server port
MCP_PORT = int(os.environ.get("MCP_PORT", "6101"))

# ---------------------------------------------------------------------------
# OAuth signing-key contract (bd-buiqr10, Option-A slice)
#
# The MCP RS verifies OAuth Bearer JWTs OFFLINE using a DEDICATED HS256 secret
# read from /config/settings.json (ADR-009 §1/§3). This is the LOCKED contract:
# the ECM AS (buiqr.3) generates 'mcp_oauth_signing_secret' in settings.json on
# startup and signs MCP access tokens with it; the RS reads the same key here.
# It is deliberately SEPARATE from ECM's user-session jwt.secret_key
# (auth_settings.json), which the RS never reads — so a compromised MCP can
# forge only MCP-scope tokens, never ECM admin sessions (threat model SR1,
# dedicated-secret amendment 2026-05-21). buiqr.8 consumes this same constant.
# ---------------------------------------------------------------------------
SIGNING_KEY_SETTINGS_JSON_KEY = "mcp_oauth_signing_secret"


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


def get_signing_key_status() -> tuple[str, str]:
    """Check whether the HS256 OAuth signing secret is present in settings.json.

    Under Option A (ADR-009 §1, bd-buiqr10), the MCP Resource Server verifies
    OAuth Bearer JWTs OFFLINE against a shared HS256 secret stored in
    ``/config/settings.json`` under ``SIGNING_KEY_SETTINGS_JSON_KEY``. This
    helper reports PRESENCE or ABSENCE of that secret so the operator can
    diagnose OAuth misconfiguration from the /health endpoint without container
    shell access.

    Returns a ``("", status)`` tuple, where ``status`` is one of:

      ``"ok"``                  — settings.json readable, JSON valid, signing
                                  secret field present and non-empty.
      ``"file_not_found"``      — ``/config/settings.json`` does not exist.
      ``"invalid_json"``        — file exists but is not valid JSON.
      ``"signing_key_missing"`` — JSON valid but ``SIGNING_KEY_SETTINGS_JSON_KEY``
                                  is absent or empty. Offline JWT verification
                                  cannot work until the shared secret is set.

    SECURITY (threat model ID1): the secret VALUE is NEVER returned or logged.
    The first tuple element is always an empty string. Only presence/absence is
    reported. The /health endpoint is effectively public; treat it as such.

    ONE-LINE CHANGE POINT: if buiqr.8 finalizes a different settings.json key
    name for the signing secret, update ``SIGNING_KEY_SETTINGS_JSON_KEY`` in
    this file — this function needs no other change.
    """
    # Re-use the same file-read logic as get_mcp_api_key_status() to keep
    # the two status paths consistent. We read the file fresh each call so
    # that key rotation takes effect without restarting the MCP container.
    if not SETTINGS_FILE.exists():
        logger.warning("[MCP-CONFIG] Settings file not found at %s (signing key check)", SETTINGS_FILE)
        return "", "file_not_found"

    try:
        raw = SETTINGS_FILE.read_text()
    except Exception as e:
        logger.error("[MCP-CONFIG] Failed to read settings file (signing key check): %s", e)
        return "", "invalid_json"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[MCP-CONFIG] settings.json is not valid JSON (signing key check): %s", e)
        return "", "invalid_json"

    # SECURITY: we only check for presence; the value is never captured, logged,
    # or returned. The local variable `_key_value` is intentionally discarded.
    _key_value = data.get(SIGNING_KEY_SETTINGS_JSON_KEY, "")
    if not _key_value:
        return "", "signing_key_missing"

    # Secret is present and non-empty — report "ok" but return empty string,
    # not the secret itself. The caller (handle_health in server.py) surfaces
    # this status string to the /health JSON; the secret stays in memory only.
    return "", "ok"
