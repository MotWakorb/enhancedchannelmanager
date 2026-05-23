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

Claude Desktop speaks stdio for locally-configured MCP servers, so a remote
HTTP server needs a local bridge. The `mcp-remote` npm package is that bridge:
Claude Desktop runs it via `npx`, it connects to ECM's Streamable HTTP MCP
endpoint over your LAN, and presents a standard MCP interface back to Claude
Desktop. The static API key is embedded in the URL. Everything runs on your
machine — ECM never has to be reachable from the internet.

> **Why not use Settings → Connectors → Add custom connector?**
> Claude Desktop's built-in "Connectors" UI requires OAuth 2.1 per the MCP
> spec. ECM does not support OAuth (that offering was retired — see
> [ADR-009](../../adr/ADR-009-mcp-oauth-authorization-server-split.md)). Do
> not use the Connectors path; it will not work. The `mcp-remote` bridge below
> is the supported path.

### Prerequisites

Node.js LTS 18+ must be installed on the same machine as Claude Desktop and on
`PATH`. Without it, Claude Desktop's logs show `spawn npx ENOENT`.

Install Node from [nodejs.org](https://nodejs.org/), or via a package manager:

| Platform | Command |
|---|---|
| macOS | `brew install node` |
| Windows | `winget install OpenJS.NodeJS.LTS` |
| Debian/Ubuntu | `apt install nodejs npm` |

Verify after install: `node --version` should print `v18.x.x` or higher.

### Step 1 — Generate your MCP API key in ECM

1. Open ECM in your browser.
2. Go to **Settings → MCP Integration**.
3. Click **Generate Key**.
4. Copy the key immediately — it is displayed once. If you miss it, use
   **Regenerate Key** to issue a new one (the old key is invalidated).

> This is your `mcp_api_key`. It is **not** your Dispatcharr API key. Mixing
> them up breaks both: Dispatcharr returns 401, and the MCP container reports
> "API key not configured." See the
> [README field reference](../../../README.md#mcp-server-claude-integration)
> for the distinction.

The Settings → MCP Integration panel also has a **copy button** for the
pre-filled `claude_desktop_config.json` block with your host and key already
substituted — use it to skip manual editing in Step 3.

### Step 2 — Open the Claude Desktop config file

1. Open Claude Desktop.
2. On **macOS**: click the **Claude** menu (top-left) → **Settings**.
   On **Windows**: click the hamburger/menu icon → **Settings**.
   *(Label may vary across Claude Desktop versions; look for the gear icon or
   Settings entry.)*
3. Click the **Developer** tab.
4. Click **Edit Config**. This opens `claude_desktop_config.json` in your
   default text editor and reveals the file in Finder (macOS) or Explorer
   (Windows).

Config file locations for reference:

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

If the file doesn't exist yet, create it at that path — Claude Desktop
creates a blank one when you click Edit Config.

### Step 3 — Add the ECM server block

Paste the following into `claude_desktop_config.json`, replacing
`YOUR_ECM_HOST` with your ECM hostname or IP, and `YOUR_API_KEY` with the key
from Step 1. If you used the copy button in Settings → MCP Integration, the
values are already filled in.

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

Notes:
- `--allow-http` is required because the endpoint is plain HTTP (not HTTPS).
  Omitting it causes `mcp-remote` to refuse the connection.
- If you already have other entries in `"mcpServers"`, add the `"ecm"` block
  alongside them — do not replace the whole file.
- If running ECM on the same machine as Claude Desktop, use `localhost` for
  `YOUR_ECM_HOST`.

Save the file.

### Step 4 — Fully quit and reopen Claude Desktop

Closing the Claude Desktop window is not enough — the bridge process only
starts at launch. Fully quit the app:

- **macOS**: Claude menu → **Quit Claude** (or Cmd+Q).
- **Windows**: right-click the system tray icon → **Quit**, or use
  Task Manager to end the process.

Then reopen Claude Desktop.

### Step 5 — Confirm the tools loaded

After restart, look for the tools/MCP indicator in the chat input bar. It
typically appears as a slider icon or a **"search and tools"** control
(label varies by Claude Desktop version). Click it — the connected `ecm`
server and its tools should be listed there.

If the `ecm` server does not appear, or shows an error, check
Settings → Developer — Claude Desktop surfaces bridge errors there.

**First test prompt:** ask Claude "List my ECM channels." A valid response
(a channel list or a message that no channels exist) confirms the bridge is
working end-to-end.

### Key rotation

The `?api_key=` URL uses `mcp_api_key` from `settings.json`. This is the
supported MCP authentication path.

To rotate the static key:

1. In ECM: Settings → MCP Integration → **Regenerate Key**.
2. Copy the new key.
3. Open `claude_desktop_config.json` (Step 2 above) and update the
   `?api_key=YOUR_API_KEY` value.
4. Fully quit and reopen Claude Desktop (Step 4 above).

Do **not** edit `dispatcharr_api_key` (or its legacy `api_key` alias). That is
the Dispatcharr REST API token and is separate from MCP auth.

If you hit `401 Invalid API key` after rotation, see
[Troubleshooting — 401 Invalid API key](#401-invalid-api-key-on-tool-calls).

---

## Claude Code (`.mcp.json`)

### What this path is

Claude Code speaks Streamable HTTP directly — no bridge, no Node.js required.
You register ECM as an MCP server either via the CLI (`claude mcp add`) or by
placing a `.mcp.json` file in your project directory. Both methods result in the
same connection; pick whichever fits your workflow.

### Step 1 — Generate your MCP API key in ECM

Same as the Claude Desktop path: **Settings → MCP Integration → Generate Key**.
Copy the key immediately. The Settings → MCP Integration panel has a copy button
for the pre-filled config block with your host and key already substituted.

If you already completed this step for Claude Desktop, reuse the same
`mcp_api_key` — there is one key for both clients.

### Step 2 — Register ECM with Claude Code

Choose one method:

---

#### Method A: CLI (`claude mcp add`)

Run this command in your terminal (replace `YOUR_ECM_HOST` and `YOUR_API_KEY`):

```bash
claude mcp add --transport http ecm "http://YOUR_ECM_HOST:6101/mcp?api_key=YOUR_API_KEY"
```

By default this uses `--scope local`, which registers the server for your
current project directory only (stored in `.claude/mcp.json`, not committed).
You can pass a different scope:

| Scope | Flag | Effect |
|---|---|---|
| `local` | `--scope local` (default) | Just you, in this project directory |
| `project` | `--scope project` | Shared with everyone who checks out this repo (stored in `.mcp.json` at the project root — commit it) |
| `user` | `--scope user` | All your projects on this machine |

Example — register for all your projects:
```bash
claude mcp add --transport http --scope user ecm "http://YOUR_ECM_HOST:6101/mcp?api_key=YOUR_API_KEY"
```

> **Security note:** `project` scope commits the URL (including the API key) to
> your repo. For shared projects, consider using `local` scope per developer, or
> an environment-variable substitution pattern — see the
> [security/mcp-json-envvar branch](https://github.com/MotWakorb/enhancedchannelmanager/compare/security/mcp-json-envvar)
> for the `${ECM_MCP_API_KEY}` pattern if your Claude Code version supports it.

---

#### Method B: `.mcp.json` file

Create (or edit) `.mcp.json` in your project root:

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

Replace `YOUR_ECM_HOST` and `YOUR_API_KEY`. Save the file.

This is equivalent to `--scope project` from the CLI: Claude Code auto-detects
`.mcp.json` at startup when it is in the working directory.

Host notes:
- Same machine as Claude Code: use `localhost`.
- MCP container on the same Docker network: use the service name (`ecm-mcp`).
- Remote/homelab ECM: use the hostname or LAN IP.

---

### Step 3 — Verify the connection

Start (or restart) Claude Code in the project directory, then run the `/mcp`
slash command:

```
/mcp
```

This lists all registered MCP servers and their connection status. You should
see `ecm` with a connected status and the list of available tools.

If `ecm` is listed but shows an error, check that the ECM MCP container is
running and reachable at port 6101. See
[Troubleshooting — MCP server not reachable](#mcp-server-not-reachable-settings--mcp-integration-shows-offline).

**First test prompt:** ask Claude Code "List my ECM channels." A valid response
confirms the connection is working end-to-end.

### Reconnecting after a container restart

If the ECM MCP container restarts mid-session, re-run `/mcp` in Claude Code.
This triggers a fresh connection attempt without requiring a full Claude Code
restart.

### Key rotation

To rotate the static key: **Settings → MCP Integration → Regenerate Key** in
ECM, then update the `?api_key=` value in your config (`.mcp.json` or the
stored CLI registration) and restart Claude Code (or run `/mcp` to
reconnect).

For the full rotation procedure, see
[Key rotation](#key-rotation) in the Claude Desktop section above — the
ECM-side steps are identical.

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
