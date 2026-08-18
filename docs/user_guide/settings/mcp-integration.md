# MCP Integration

> **Admin only.** This destination only appears in the Settings navigation for administrators; it does not render for non-admin operators.

MCP Integration, under **Administration** in the Settings navigation, is
where you check the MCP server's status, manage the API key, and get the
connection snippets for Claude Desktop or Claude Code. The full walkthrough,
including step-by-step setup for both connection methods, key rotation, and
troubleshooting, already lives at the link below. This page exists so the
destination has a home in the Settings navigation, not to duplicate that
guide.

## Common tasks

### Check whether the MCP server is reachable

1. Go to **Settings → MCP Integration**.
2. Read the **Server Status** card. It reports whether the MCP server is
   online and how many tools are available.

**Result:** A green status with a tool count confirms the MCP server is
reachable and your API key is recognized.

### Get connected

See the [MCP Integration reference](../integrations/mcp.md) for the full
walkthrough: generating your API key on this page, choosing between the
mcp-remote bridge (Claude Desktop) and `.mcp.json` (Claude Code), and
verifying the connection.

### Rotate your API key

1. Go to **Settings → MCP Integration**.
2. Under **API Key**, click **Regenerate Key** to issue a new one, or
   **Revoke Key** to disable access entirely.

**Result:** Existing connections using the old key stop working
immediately. Update every client's configuration with the new key. See
[Key rotation](../integrations/mcp.md#key-rotation) in the full reference.

Only a signed-in administrator can do this. A caller presenting the MCP API key
is refused, so a leaked key cannot rotate or revoke itself. Rotation replaces
the client-facing key only; the sidecar's private credentials are managed by ECM
and need no action from you.

**Enabling TLS in ECM does not encrypt MCP traffic.** ECM's TLS setting covers
ECM's own HTTPS port (default `6143`); the MCP sidecar is a separate listener on
port `6101` and is not wrapped by it. Keep MCP on loopback, or put an HTTPS
reverse proxy in front of it. See
[What the MCP key is, and what it cannot do](../integrations/mcp.md#what-the-mcp-key-is-and-what-it-cannot-do).

## Going deeper

- [MCP Integration reference](../integrations/mcp.md): full setup, both connection methods, and troubleshooting.
- [MCP Integration reference: Troubleshooting](../integrations/mcp.md#troubleshooting): connection failures, `401` errors, and the deprecated SSE transport.
