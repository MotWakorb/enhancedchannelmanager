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


def get_mcp_api_key() -> str:
    """Read the MCP API key from the shared settings.json file.

    Re-reads from disk on every call so key rotation takes effect
    without restarting the MCP container.
    """
    if not SETTINGS_FILE.exists():
        logger.warning("[MCP-CONFIG] Settings file not found at %s", SETTINGS_FILE)
        return ""

    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return data.get("mcp_api_key", "")
    except Exception as e:
        logger.error("[MCP-CONFIG] Failed to read settings: %s", e)
        return ""
