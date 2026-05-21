# MCP Integration — Claude AI Connection Reference

> **Audience:** Operator who has the MCP sidecar container running and wants to
> connect Claude Desktop or Claude Code to ECM.
>
> **Status:** Complete as of v0.17.1 (OAuth 2.1 + static key both supported).

This is the full operator reference for connecting Claude to ECM via the Model
Context Protocol. The [README MCP section](../../../README.md#mcp-server-claude-integration)
covers quick-start setup; come here when you need the step-by-step OAuth
walkthrough, key rotation details, or troubleshooting.

---

## Choose your connection method

Two paths connect Claude to ECM. They run in parallel — you can use both at the
same time.

| | Custom Connector (OAuth) | mcp-remote bridge |
|---|---|---|
| **Best for** | Claude Desktop without Node.js installed | Claude Desktop when Node.js is already installed; advanced users who want static-key control |
| **Auth model** | OAuth 2.1 + PKCE — ECM issues a short-lived JWT; Claude Desktop stores and refreshes it | Static API key embedded in the MCP URL |
| **Prerequisites** | HTTPS reverse proxy in front of MCP port 6101; `OAUTH_ISSUER` set on both containers | Node.js LTS 18+ on the Claude Desktop machine |
| **Config file edits** | None — Claude Desktop's connector UI handles everything | `claude_desktop_config.json` |
| **Claude Code** | Not applicable — Claude Code uses the static-key `.mcp.json` path | Not applicable |

**Claude Code** uses neither path above. It talks Streamable HTTP directly:
create a `.mcp.json` file with the `?api_key=` URL and Claude Code picks it up
automatically. See [Claude Code](#claude-code-mcp-json) below.

---

## Path A: Custom Connector (OAuth — no Node required)

### What this path is

Claude Desktop's built-in **Settings → Connectors → Add custom connector** UI
uses OAuth 2.1 with PKCE. ECM acts as the Authorization Server (AS): it hosts
the consent screen, issues short-lived Bearer JWTs, and manages grant records.
The MCP container acts as the Resource Server (RS): it validates Bearer JWTs
offline on every tool call without calling back to ECM.

No Node.js or `npx` is involved. The trade-off is that you need HTTPS in front
of the MCP container before starting.

> **ECM is the Authorization Server, not Anthropic.** ECM issues the OAuth
> tokens that Claude Desktop stores. Anthropic is not in the trust chain.
> See [ADR-009](../../adr/ADR-009-mcp-oauth-authorization-server-split.md) for
> the full architecture rationale.

### Prerequisites

Before adding the connector in Claude Desktop:

1. **MCP container is running.** Verify: `curl http://YOUR_ECM_HOST:6101/health`
   should return `{"status": "ok", ...}`.
2. **HTTPS reverse proxy in front of port 6101.** The MCP SDK rejects plain-HTTP
   issuer URLs for non-loopback hosts. See the
   [HTTPS reverse-proxy runbook](../../runbooks/mcp-https-reverse-proxy.md) for
   Caddy (recommended, auto TLS), nginx, and Traefik recipes.
3. **`OAUTH_ISSUER` set identically on both containers.** This is the single
   most common misconfiguration. Both the ECM container and the MCP container
   must have `OAUTH_ISSUER` set to the same external HTTPS origin.

   ```yaml
   # In docker-compose.yml — must be identical on both services
   services:
     ecm:
       environment:
         - OAUTH_ISSUER=https://mcp.yourdomain.com
     ecm-mcp:
       environment:
         - OAUTH_ISSUER=https://mcp.yourdomain.com
   ```

   If they differ by even one character, every OAuth Bearer token fails with a
   silent 401. Without this variable, both containers default to
   `https://ecm.local`, which only works loopback.

4. **ECM API key generated** (Settings → MCP Integration → Generate Key). The
   static key and OAuth operate independently, but the key must be generated for
   the MCP container's health endpoint to report `api_key_configured: true`.

> **Known open item — `redirect_uri` placeholder (buiqr.6):** ECM's registered
> `redirect_uri` for Claude Desktop is a confirmed placeholder pending
> verification. The Custom Connector authorization flow will fail with a
> `redirect_uri mismatch` error until the exact URI Claude Desktop sends is
> confirmed and registered. Watch bead `enhancedchannelmanager-buiqr.6` for
> resolution. The mcp-remote bridge and Claude Code paths are **unaffected**.

### Step-by-step walkthrough

1. **Set `OAUTH_ISSUER` on both containers** and restart them (see prerequisites
   above).

2. **Set up HTTPS** in front of MCP port 6101. Follow the
   [HTTPS reverse-proxy runbook](../../runbooks/mcp-https-reverse-proxy.md). At
   the end of that runbook, verify the discovery endpoint responds:

   ```bash
   curl -s https://mcp.yourdomain.com/.well-known/oauth-protected-resource
   # Expected: {"issuer": "https://mcp.yourdomain.com", ...}
   ```

3. **Open Claude Desktop** and navigate to **Settings → Connectors**.

4. Click **Add custom connector**.

5. Enter the MCP server URL:

   ```
   https://YOUR_MCP_HTTPS_DOMAIN/mcp
   ```

   Replace `YOUR_MCP_HTTPS_DOMAIN` with the hostname your HTTPS proxy answers on
   (e.g., `mcp.yourdomain.com`).

6. Claude Desktop queries the discovery endpoint automatically. If it cannot
   reach `/.well-known/oauth-protected-resource`, confirm your proxy is
   running and the hostname resolves.

7. **Your browser opens the ECM consent screen.** Log in to ECM if prompted.

   <!-- SCREENSHOT PLACEHOLDER: ECM consent screen
   Alt text: "ECM OAuth consent screen showing 'Claude Desktop is requesting
   access to your ECM install. It will be able to read and manage your channels,
   streams, M3U accounts, and EPG sources.' with an Authorize button."
   Caption: The ECM consent screen — log in and click Authorize.
   NOTE: Real screenshot is a manual follow-up. Capture once buiqr.6 (redirect_uri)
   is resolved and an end-to-end OAuth flow can be completed against a live install. -->

8. Click **Authorize**. ECM creates a grant record and issues a Bearer JWT to
   Claude Desktop.

9. **Claude Desktop shows the connector as connected.** No config file edits
   were needed.

You can now ask Claude to manage your ECM install — "list my channels," "probe
all streams," "run the auto-creation pipeline and report what it created," etc.

### Managing active connections

In **ECM Settings → MCP Integration → Active Connections** you can see every app
that has an active OAuth grant.

<!-- SCREENSHOT PLACEHOLDER: Active Connections section
Alt text: "Settings → MCP Integration showing the 'Active Connections' section
with a row for 'Claude Desktop', granted timestamp, last-used timestamp, and a
Revoke button."
Caption: Active Connections — each authorized app appears here with its grant
time and last-used time.
NOTE: Real screenshot is a manual follow-up (same dependency as consent screen). -->

Each row shows:
- **Client name** — the name ECM registered for that client (e.g., "Claude
  Desktop").
- **Granted** — when you authorized it.
- **Last used** — when it last made a tool call.
- **Revoke** — immediately invalidates the access. The app must re-authorize
  to connect again.

**Revoke all** (the "Revoke all active tokens" button) is a two-step panic
button: it requires a first confirmation ("This will disconnect ALL apps. Are
you sure?") and then typing `REVOKE` to execute. Use it if you suspect a grant
was issued inadvertently or a connected device is lost or compromised.

### Key rotation in the OAuth context

OAuth tokens are managed by ECM. "Rotation" in this context means two different
things:

- **`mcp_oauth_signing_secret` rotation** (the HS256 signing secret ECM uses to
  mint Bearer JWTs): ECM auto-generates this on startup if absent. If you
  manually rotate it (by deleting it from `settings.json` and restarting ECM),
  every live OAuth access token immediately fails to verify — Claude Desktop gets
  a 401 on the next tool call. Active grants remain in the database, but the app
  must re-authorize to get a new token. Do not rotate the signing secret unless
  you intend to invalidate all live sessions.
- **Revoking individual grants** (the Revoke button in Active Connections): stops
  refresh token issuance immediately. The live access token lives until its TTL
  expires (≤ 15 minutes), after which the app cannot renew and must re-authorize.

---

## Path B: mcp-remote bridge (Node required)

### What this path is

The `mcp-remote` npm package acts as a local bridge: Claude Desktop runs it via
`npx`, it connects to ECM's Streamable HTTP MCP endpoint over plain HTTP, and
presents a standard MCP interface to Claude Desktop. The static API key is
embedded in the URL.

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

> The `?api_key=` parameter is your `mcp_api_key` — the key generated in
> Settings → MCP Integration. It is not your Dispatcharr API key. Mixing them up
> breaks both: Dispatcharr returns 401, and the MCP container reports "API key
> not configured." See the
> [README field reference](../../../README.md#mcp-server-claude-integration) for
> the distinction.

### Key rotation in the static-key context

The `?api_key=` URL uses `mcp_api_key` from `settings.json`. This is a
**permanent path** — it is not deprecated and has no planned sunset.

To rotate the static key:

1. In ECM: Settings → MCP Integration → Regenerate Key.
2. Copy the new key.
3. Update the `?api_key=YOUR_API_KEY` value in `claude_desktop_config.json`.
4. Restart Claude Desktop.

Do **not** edit `dispatcharr_api_key` (or its legacy `api_key` alias). That is
the Dispatcharr REST API token and is separate from MCP auth.

---

## Claude Code (`.mcp.json`)

Claude Code talks Streamable HTTP directly — no bridge, no OAuth. Create a
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

The `?api_key=` path is permanent (PO-locked — no deprecation). Claude Code is
unaffected by the HTTPS or `OAUTH_ISSUER` requirements — those only apply to the
Custom Connector OAuth flow.

---

## Troubleshooting

### The connector authorization fails with `redirect_uri mismatch`

**Cause:** ECM's registered `redirect_uri` for Claude Desktop is a placeholder
pending verification (open item: bead `enhancedchannelmanager-buiqr.6`).

**What to do:**
1. Check the ECM backend logs for the exact URI Claude Desktop sent:
   `docker logs ecm-ecm-1 2>&1 | grep "redirect_uri" | tail -5`
2. Note the actual URI.
3. Report it to the project by commenting on bead `buiqr.6` — the maintainer
   will update the hardcoded client registry and release a fix.

This blocks the Custom Connector path only. The mcp-remote bridge and Claude
Code paths are unaffected.

### Silent 401s on every tool call (OAuth connector connected but tools fail)

**Most likely cause:** `OAUTH_ISSUER` is set differently on the ECM and MCP
containers. ECM mints tokens with `iss = OAUTH_ISSUER`; MCP verifies them
against its own `OAUTH_ISSUER`. A one-character difference produces a silent 401
on every tool call.

**Diagnose:**

```bash
# What OAUTH_ISSUER does each container have?
docker inspect ecm-ecm-1 | python3 -m json.tool | grep -A1 OAUTH_ISSUER
docker inspect ecm-mcp-1 | python3 -m json.tool | grep -A1 OAUTH_ISSUER
```

They must be identical. Correct them and restart both containers.

**Also check:** the discovery endpoint's `issuer` field matches:

```bash
curl -s https://YOUR_MCP_HTTPS_DOMAIN/.well-known/oauth-protected-resource \
  | python3 -m json.tool | grep issuer
```

### Access token expired — tool calls fail after a period of inactivity

**Normal behavior.** OAuth access tokens have a short TTL (≤ 15 minutes). The
MCP connector refreshes them automatically in the background when it detects
expiry. If the refresh token has also expired (or was revoked), Claude Desktop
prompts you to re-authorize.

**If re-authorization prompts do not appear and tool calls just fail silently:**
Remove the connector from Claude Desktop (Settings → Connectors → remove ECM)
and add it again. This is a one-time reset that forces a fresh authorization.

### PKCE failure (`invalid_request` or `invalid_grant`)

ECM enforces PKCE S256 only. The `plain` method is rejected. This error means
the client sent a non-S256 `code_challenge_method` or no code challenge at all.

Claude Desktop's built-in Custom Connector sends S256 correctly. If you see this
error, it usually means:
- A third-party MCP client is sending `plain` or no PKCE.
- A proxy or interceptor is stripping the `code_challenge` parameter.

ECM's authorization endpoint logs rejected requests at the WARN level:
`docker logs ecm-ecm-1 2>&1 | grep -i "pkce\|code_challenge\|invalid_request" | tail -20`

### OAuth discovery returns 404 on plain HTTP

**Expected behavior** (not a bug). ECM's OAuth discovery endpoints return 404 on
plain-HTTP, non-loopback requests by default (`oauth_allow_insecure` defaults to
`false`). This prevents token interception on unencrypted connections.

**Fix:** set up HTTPS as described in the
[HTTPS reverse-proxy runbook](../../runbooks/mcp-https-reverse-proxy.md).

**If you cannot set up HTTPS** (closed LAN, no DNS), you can explicitly opt in
to plain-HTTP OAuth by adding `"oauth_allow_insecure": true` to
`settings.json` (or Settings → Advanced in the UI). This carries the risk that
OAuth Bearer tokens transit the network in cleartext — anyone on the same network
segment can intercept and replay them. Only use this on a private, trusted LAN.

### `OAUTH_ISSUER` drift — silent 401s after settings change

If `OAUTH_ISSUER` changes on one container while the other retains the old value,
tokens minted after the change fail to verify. Symptoms: tool calls return 401
immediately after a container restart or compose change; the Settings → MCP
Integration → Active Connections list shows grants but they don't work.

**Fix:** ensure `OAUTH_ISSUER` is identical on both containers and restart both.

### `spawn npx ENOENT` in Claude Desktop logs (mcp-remote path)

Node.js is not on `PATH` for the Claude Desktop process. Install Node.js (see
[mcp-remote path prerequisites](#prerequisites-1)) and restart Claude Desktop.

This error only applies to the mcp-remote bridge. It does not occur with the
Custom Connector path.

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

---

## Going deeper

- **Architecture**: [ADR-009 — MCP OAuth AS/RS split](../../adr/ADR-009-mcp-oauth-authorization-server-split.md)
  — the full design rationale: why ECM is the AS, dual-path-by-shape routing,
  offline HS256 verification, and the OAUTH_ISSUER requirement.
- **HTTPS setup**: [Runbook: HTTPS Reverse Proxy for MCP OAuth](../../runbooks/mcp-https-reverse-proxy.md)
  — Caddy (recommended), nginx, and Traefik recipes. OAUTH_ISSUER alignment
  documented as the critical step.
- **Release gate**: [Runbook: MCP Release Verification Checklist](../../runbooks/mcp-release-verification.md)
  — the per-release manual checklist (Custom Connector flow, tool call, token
  refresh, static key backward-compat, settings panel smoke).
- **Security model**: `docs/security/threat_model_mcp_oauth.md` — STRIDE threat
  analysis for the OAuth surface.
- **README**: [MCP Server (Claude Integration)](../../../README.md#mcp-server-claude-integration)
  — quick-start setup and the "choose your method" overview table.
