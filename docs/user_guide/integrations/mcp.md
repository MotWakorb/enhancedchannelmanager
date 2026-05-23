# MCP Integration — Claude AI Connection Reference

> **Audience:** Operator who has the MCP sidecar container running and wants to
> connect Claude Desktop or Claude Code to ECM.
>
> **Status:** Current as of v0.17.1. MCP authenticates with a static API key via
> the `?api_key=` path. (The OAuth 2.1 "Custom Connector" offering was retired —
> see [ADR-009 (Superseded)](../../adr/ADR-009-mcp-oauth-authorization-server-split.md).)

This is the full operator reference for connecting Claude to ECM via the Model
Context Protocol. The [README MCP section](../../../README.md#mcp-server-claude-integration)
covers quick-start setup; come here when you need the step-by-step setup, key
rotation details, or troubleshooting.

---

## Choose your connection method

ECM's MCP server is authenticated with a static API key (`mcp_api_key`), passed
as the `?api_key=` query parameter. Both methods below run on *your* machine and
connect to ECM over your LAN/VPN — **nothing needs to be exposed to the public
internet**.

| | mcp-remote bridge (Claude Desktop) | Claude Code (`.mcp.json`) |
|---|---|---|
| **Best for** | Claude Desktop users; private/homelab; existing setups | Claude Code in any project |
| **Auth model** | Static API key embedded in the MCP URL | Static API key embedded in the MCP URL |
| **Prerequisites** | Node.js LTS 18+ on the Claude Desktop machine | None — Claude Code speaks Streamable HTTP natively |
| **Config file** | `claude_desktop_config.json` | `.mcp.json` in your project directory |
| **Network** | Private OK — bridge runs on your machine, connects over LAN/VPN | Private OK — Claude Code connects directly from your machine |

- **Using Claude Desktop?** See [mcp-remote bridge](#claude-desktop--mcp-remote-bridge-node-required).
- **Using Claude Code?** See [Claude Code](#claude-code-mcpjson).

---

## Claude Desktop — mcp-remote bridge (Node required)

### What this path is

The `mcp-remote` npm package acts as a local bridge: Claude Desktop runs it via
`npx`, it connects to ECM's Streamable HTTP MCP endpoint over plain HTTP, and
presents a standard MCP interface to Claude Desktop. The static API key is
embedded in the URL. Everything runs on your machine — ECM never has to be
reachable from the internet.

### Prerequisites

Node.js LTS 18+ must be installed on the same machine as Claude Desktop and on
`PATH`. Without it, Claude Desktop's logs show `spawn npx ENOENT`.

Install Node from [nodejs.org](https://nodejs.org/), or via a package manager:

| Platform | Command |
|---|---|
| Windows | `winget install OpenJS.NodeJS.LTS` |
| macOS | `brew install node` |
| Debian/Ubuntu | `apt install nodejs npm` |

### Setup

1. **Generate an API key** in ECM: Settings → MCP Integration → Generate Key.
   Copy the key — it is shown once.

2. **Add to `claude_desktop_config.json`** (replace `YOUR_ECM_HOST` and
   `YOUR_API_KEY`):

   ```json
   {
     "mcpServers": {
       "ecm": {
         "command": "npx",
         "args": [
           "mcp-remote",
           "http://YOUR_ECM_HOST:6101/mcp?api_key=YOUR_API_KEY",
           "--allow-http"
         ]
       }
     }
   }
   ```

   (`--allow-http` is needed because the endpoint is plain HTTP. The Settings →
   MCP Integration page has a copy button for the pre-filled config block.)

3. **Restart Claude Desktop.** It launches the `mcp-remote` bridge on startup.

You can now ask Claude to manage your ECM install — "list my channels," "probe
all streams," "run the auto-creation pipeline and report what it created," etc.

> The `?api_key=` parameter is your `mcp_api_key` — the key generated in
> Settings → MCP Integration. It is not your Dispatcharr API key. Mixing them up
> breaks both: Dispatcharr returns 401, and the MCP container reports "API key
> not configured." See the
> [README field reference](../../../README.md#mcp-server-claude-integration) for
> the distinction.

### Key rotation

The `?api_key=` URL uses `mcp_api_key` from `settings.json`. This is the
supported MCP authentication path.

To rotate the static key:

1. In ECM: Settings → MCP Integration → Regenerate Key.
2. Copy the new key.
3. Update the `?api_key=YOUR_API_KEY` value in `claude_desktop_config.json`.
4. Restart Claude Desktop.

Do **not** edit `dispatcharr_api_key` (or its legacy `api_key` alias). That is
the Dispatcharr REST API token and is separate from MCP auth.

---

## Claude Code (`.mcp.json`)

Claude Code talks Streamable HTTP directly — no bridge, no Node.js. Create a
`.mcp.json` file in any project directory where you want ECM tools:

```json
{
  "mcpServers": {
    "ecm": {
      "type": "http",
      "url": "http://YOUR_ECM_HOST:6101/mcp?api_key=YOUR_API_KEY"
    }
  }
}
```

1. Replace `YOUR_ECM_HOST` and `YOUR_API_KEY` (same `mcp_api_key` as the
   mcp-remote path).
2. Start Claude Code in that directory — it auto-detects `.mcp.json` on launch.
3. Run `/mcp` to reconnect if the MCP container restarts mid-session.

If running ECM locally, use `localhost` as your host. If the MCP container is on
the same Docker network as Claude Code, use the container name (`ecm-mcp`).

---

## Troubleshooting

### `spawn npx ENOENT` in Claude Desktop logs (mcp-remote path)

Node.js is not on `PATH` for the Claude Desktop process. Install Node.js (see
[mcp-remote prerequisites](#prerequisites)) and restart Claude Desktop.

This error only applies to the mcp-remote bridge.

### MCP server not reachable (Settings → MCP Integration shows "offline")

The ECM container probes the MCP container's `/health` endpoint at
`ecm-mcp:6101` by default (Docker DNS on the compose network). If both
containers share `network_mode: host`, set `MCP_HOST=localhost` on the ECM
service so the probe targets the host loopback instead.

Verify manually:
```bash
curl http://YOUR_ECM_HOST:6101/health
```

If that fails, check the MCP container is running (`docker ps | grep ecm-mcp`)
and that the `ECM_URL` environment variable on the MCP container points at the
ECM container correctly.

### MCP tools fail with "All connection attempts failed"

This is the reverse direction of the probe issue above: the MCP container
cannot reach the ECM **backend**. The MCP server calls ECM's API at the URL in
its `ECM_URL` environment variable, which defaults to `http://ecm:6100` (Docker
DNS on the canonical compose network).

If both containers run with `network_mode: host`, the `ecm` service name has no
DNS entry on a shared host network — it resolves to nothing (or to a wrong
external address) — and the ECM backend answers only on the host loopback. Every
MCP tool call then fails with `All connection attempts failed`.

**Fix:** set `ECM_URL=http://localhost:6100` on the MCP service so it reaches
the backend over the host loopback. This is the symmetric partner to the
`MCP_HOST=localhost` setting above: `MCP_HOST` fixes the ECM → MCP probe, while
`ECM_URL` fixes MCP → ECM tool calls.

```yaml
# docker-compose.yml — host-networking deployment
services:
  ecm:
    network_mode: host
    environment:
      MCP_HOST: "localhost"               # ECM → MCP health probe
  ecm-mcp:
    network_mode: host
    environment:
      ECM_URL: "http://localhost:6100"    # MCP → ECM backend API
```

Verify manually from inside the MCP container:
```bash
docker exec ecm-ecm-mcp-1 curl -s http://localhost:6100/api/health
```

### MCP server online but "API key not configured"

The MCP container's `/health` endpoint reports `api_key_configured: false`. ECM
and the MCP container share the `/config` volume, and the MCP container reads
`mcp_api_key` from `settings.json`. The most common causes:

- **No key generated yet.** Open Settings → MCP Integration and click Generate
  Key. The MCP `/health` endpoint surfaces a machine-readable `api_key_status`
  (`file_not_found` / `invalid_json` / `field_missing` / `field_empty`) plus a
  setup hint describing the exact cause.
- **The two containers don't share the same `/config` volume.** Verify both
  containers mount the same volume.

Diagnose:
```bash
curl -s http://YOUR_ECM_HOST:6101/health | python3 -m json.tool
```

### `401 Invalid API key` on tool calls

The `?api_key=` value in your Claude config does not match `mcp_api_key` in
`settings.json`. This happens after a key rotation if the config wasn't updated.
Regenerate the key in ECM (or copy the current value), update your config, and
restart Claude.

Make sure you are using the **MCP** key (`mcp_api_key`), not the Dispatcharr
REST token (`dispatcharr_api_key` / legacy `api_key`).

### Upgrading from the deprecated SSE transport

The MCP server moved from the deprecated SSE transport (`/sse` + `/messages/`)
to the modern Streamable HTTP transport on a single `/mcp` endpoint. If you have
an existing config pointing at `http://YOUR_ECM_HOST:6101/sse?api_key=...` (or
`"type": "sse"` in a `.mcp.json`), change the path to `/mcp` (and `"type":
"http"` for Claude Code). The `/sse` endpoint was removed. API-key auth is
unchanged.

---

## Going deeper

- **Architecture**: [`docs/architecture.md`](../../architecture.md) — the MCP
  Server static-key baseline and `settings.json` credential schema.
- **README**: [MCP Server (Claude Integration)](../../../README.md#mcp-server-claude-integration)
  — quick-start setup and the "choose your method" overview table.
- **Retired OAuth offering**: [ADR-009 (Superseded)](../../adr/ADR-009-mcp-oauth-authorization-server-split.md)
  and `docs/security/threat_model_mcp_oauth.md` (Superseded/dormant) — history of
  the OAuth 2.1 "Custom Connector" offering that was retired (`bd-9axgc`); the
  code is kept dormant in-tree for reversibility.
