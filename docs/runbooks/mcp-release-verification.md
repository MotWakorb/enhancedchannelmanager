# Runbook: MCP Release Verification Checklist

> Per-release manual verification that the MCP static-key connection works
> end-to-end. Walk this before tagging any release that includes MCP changes.

- **Severity**: Release gate (blocking — do not tag until all steps pass)
- **Owner**: Releaser (Project Engineer, with PO authorization to cut)
- **Last reviewed**: 2026-05-21
- **Related beads**: `enhancedchannelmanager-9axgc` (retired the MCP OAuth offering)

> **Note (bd-9axgc):** the MCP OAuth 2.1 "Custom Connector" offering was retired.
> The supported MCP authentication method is the static `?api_key=` path. The
> OAuth-flow / token-refresh / discovery verification steps were removed from
> this checklist (the code is kept dormant in-tree; see
> [ADR-009 (Superseded)](../adr/ADR-009-mcp-oauth-authorization-server-split.md)).

---

## When to Run This

Run this checklist **before cutting any release** that touches:

- `mcp-server/` (any file)
- `backend/routers/` MCP-related routers
- `settings.json` field definitions (new fields, changed defaults)
- `frontend/src/components/settings/MCPSettingsSection.tsx`
- Any `docker-compose*.yml` MCP service definition

For releases that touch none of the above, this checklist is optional but recommended.

---

## Prerequisites

Before starting, verify the environment:

```bash
# ECM is running and reachable
curl -s http://YOUR_ECM_HOST:6100/api/health | python3 -m json.tool
# Expected: {"status": "ok", ...}

# MCP container is running and reachable
curl -s http://YOUR_ECM_HOST:6101/health | python3 -m json.tool
# Expected: {"status": "ok", "api_key_configured": true, ...}
```

No HTTPS reverse proxy is required — the static `?api_key=` path runs over plain
HTTP on your private network.

---

## Step 1: Static API Key Path

**What this verifies**: the supported `?api_key=` connection works end-to-end —
the MCP Resource Server accepts the static key and dispatches a tool listing.

**Requires**: `mcp_api_key` configured in ECM Settings → MCP Integration.

1. Retrieve your MCP API key from ECM Settings → MCP Integration (or from
   `settings.json`).

2. Make a direct curl request using the static key:
   ```bash
   curl -s "http://YOUR_ECM_HOST:6101/mcp?api_key=YOUR_MCP_API_KEY" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
     | python3 -m json.tool | head -20
   ```

3. Expect a JSON response listing available tools (not a 401 or 403).

**Pass criteria**:
- [ ] Static key request returns 200 with a tools list
- [ ] `?api_key=` variant works (also try `Authorization: Bearer <static-key>`)
- [ ] A wrong key returns 401: replace the key with `wrong-key` and confirm 401

---

## Step 2: Make a Tool Call via Claude

**What this verifies**: an end-to-end tool call from a Claude client (Claude
Desktop via the mcp-remote bridge, or Claude Code via `.mcp.json`) succeeds.

1. With the connection configured (see
   [`docs/user_guide/integrations/mcp.md`](../user_guide/integrations/mcp.md)),
   ask Claude:
   ```
   Use the ECM connector to list my channel groups.
   ```

2. Observe the response. Claude should return a list of channel groups from your
   ECM install.

**Pass criteria**:
- [ ] Claude returns channel group data (not an error or empty response)
- [ ] MCP container logs show a successful tool dispatch:
      `docker logs ecm-mcp-1 2>&1 | grep "list_channel_groups\|200" | tail -5`
- [ ] MCP logs show `auth_method=static_key` (the supported path)

---

## Step 3: Settings Panel Smoke Check

**What this verifies**: the in-app MCP Settings panel correctly reflects the
current state.

1. Open ECM Settings → MCP Integration.

2. Verify:
   - [ ] Server Status badge shows "MCP server online — N tools available"
   - [ ] API Key section shows "API key is configured"
   - [ ] Connection section shows the mcp-remote bridge block and the Claude
         Code `.mcp.json` block
   - [ ] Generate / Regenerate / Revoke key buttons work

---

## Sign-Off

When all steps pass, record sign-off in the release PR description:

```
### MCP Release Verification (docs/runbooks/mcp-release-verification.md)
- [x] Step 1: Static API key path — ?api_key= returns 200 with tools list
- [x] Step 2: Tool call via Claude — list_channel_groups returned data
- [x] Step 3: Settings panel smoke check — status, key, instructions correct
Verified by: [your name], [date], on ECM vX.Y.Z-NNNN against [environment description]
```

---

## References

- `docs/user_guide/integrations/mcp.md` — operator connection reference (mcp-remote + Claude Code)
- `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md` (Superseded) — history of the retired OAuth offering
- `docs/shipping.md` — full release cut procedure (this checklist is linked from there)
