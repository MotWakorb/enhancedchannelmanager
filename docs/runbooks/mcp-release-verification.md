# Runbook: MCP Release Verification Checklist

> Per-release manual verification that the MCP static-key connection works
> end-to-end. Walk this before tagging any release that includes MCP changes.

- **Severity**: Release gate (blocking: do not tag until all steps pass)
- **Owner**: Releaser (Project Engineer, with PO authorization to cut)
- **Last reviewed**: 2026-05-21
- **Related beads**: `enhancedchannelmanager-9axgc` (retired the MCP OAuth offering)

> **Note (bd-9axgc, bd-jir0m):** the MCP OAuth 2.1 "Custom Connector" offering was
> retired and its code removed from the tree in v0.17.3. The supported MCP
> authentication method is the static Bearer-header path. The OAuth-flow /
> token-refresh / discovery verification steps were removed from this checklist
> (see [ADR-009 (Superseded)](../adr/ADR-009-mcp-oauth-authorization-server-split.md)).

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

Run the local check from the Docker host over loopback. Remote checks use the
documented HTTPS reverse proxy and remote overlay.

---

## Step 1: Static API Key Path

**What this verifies**: the supported Bearer-header connection works end-to-end:
the MCP Resource Server accepts the static key and dispatches a tool listing.

**Requires**: `mcp_api_key` configured in ECM Settings → MCP Integration.

1. Retrieve your MCP API key from ECM Settings → MCP Integration (or from
   `settings.json`).

2. Read the key without echo and stream curl's header configuration over stdin,
   keeping the credential out of shell history and process arguments:
   ```bash
   read -rsp 'MCP API key: ' MCP_KEY; echo
   printf 'header = "Authorization: Bearer %s"\n' "$MCP_KEY" | \
   curl --config - -s "http://localhost:6101/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
     | python3 -m json.tool | head -20
   ```

3. Expect a JSON response listing available tools (not a 401 or 403).

**Pass criteria**:
- [ ] Static key request returns 200 with a tools list
- [ ] A valid `Authorization: Bearer <static-key>` request works
- [ ] `?api_key=<static-key>` is rejected and the key is absent from logs
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
- [x] Step 1 (Static API key path): Bearer header returns 200; query credential rejected
- [x] Step 2 (Tool call via Claude): list_channel_groups returned data
- [x] Step 3 (Settings panel smoke check): status, key, instructions correct
Verified by: [your name], [date], on ECM vX.Y.Z-NNNN against [environment description]
```

---

## References

- `docs/user_guide/integrations/mcp.md`: operator connection reference (mcp-remote + Claude Code)
- `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md` (Superseded): history of the retired OAuth offering
- `docs/shipping.md`: full release cut procedure (this checklist is linked from there)
