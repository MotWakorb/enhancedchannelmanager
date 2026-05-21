# Runbook: MCP Release Verification Checklist

> Per-release manual verification that the MCP OAuth flow and static-key path both work end-to-end. Walk this before tagging any release that includes MCP or OAuth changes.

- **Severity**: Release gate (blocking — do not tag until all steps pass)
- **Owner**: Releaser (Project Engineer, with PO authorization to cut)
- **Last reviewed**: 2026-05-21
- **Related beads**: `enhancedchannelmanager-buiqr.11` (docs, PO decision #6), `enhancedchannelmanager-buiqr` (OAuth 2.1 epic)
- **Related runbooks**: `docs/runbooks/mcp-https-reverse-proxy.md` (HTTPS proxy setup required for Custom Connector check)

---

## When to Run This

Run this checklist **before cutting any release** that touches:

- `mcp-server/` (any file)
- `backend/routers/oauth*.py` or `backend/routers/mcp*.py`
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

# For the Custom Connector check (Step 2): HTTPS proxy must be up
curl -s https://YOUR_MCP_HTTPS_DOMAIN/.well-known/oauth-protected-resource | python3 -m json.tool
# Expected: {"issuer": "https://YOUR_MCP_HTTPS_DOMAIN", ...}
```

If the HTTPS check fails, see `docs/runbooks/mcp-https-reverse-proxy.md`. The static-key checks (Steps 1 and 3) do not require HTTPS; the Custom Connector check (Step 2) does.

---

## Step 1: Add a Custom Connector (OAuth Flow)

**What this verifies**: the full OAuth 2.1 + PKCE authorization flow — discovery, consent, token issuance, and Bearer-JWT verification on the first tool call.

**Requires**: Claude Desktop with HTTPS proxy running (see `mcp-https-reverse-proxy.md`).

1. Open Claude Desktop and navigate to **Settings → Connectors**.

2. Click **Add custom connector**.

3. Enter the MCP server URL:
   ```
   https://YOUR_MCP_HTTPS_DOMAIN/mcp
   ```

4. Claude Desktop discovers the OAuth endpoints via `/.well-known/oauth-protected-resource`. If discovery fails, check the HTTPS proxy is running and `OAUTH_ISSUER` is set correctly on both containers.

5. Claude Desktop opens your browser to the ECM consent screen. Log in if prompted. Click **Authorize**.

6. Claude Desktop shows the connector as connected.

**Pass criteria**:
- [ ] Connector appears in Claude Desktop's Connectors list with status "Connected"
- [ ] No OAuth error in the ECM backend logs (`docker logs ecm-ecm-1 2>&1 | grep -i "oauth\|error" | tail -20`)
- [ ] No `iss` mismatch error in the MCP container logs (`docker logs ecm-mcp-1 2>&1 | grep -i "error\|401\|iss" | tail -20`)

---

## Step 2: Make a Tool Call via the OAuth Connector

**What this verifies**: the Bearer JWT issued in Step 1 is accepted by the MCP Resource Server on a real tool call; the dual-path-by-shape router correctly routes the JWT-shaped credential to the OAuth path.

**Requires**: Connector from Step 1 connected.

1. In Claude Desktop (using the connector from Step 1), ask Claude:
   ```
   Use the ECM connector to list my channel groups.
   ```

2. Observe the response. Claude should return a list of channel groups from your ECM install.

**Pass criteria**:
- [ ] Claude returns channel group data (not an error or empty response)
- [ ] MCP container logs show a successful tool dispatch: `docker logs ecm-mcp-1 2>&1 | grep "list_channel_groups\|200" | tail -5`
- [ ] No 401 or "invalid token" errors in MCP logs

**If this fails with a 401**: Bearer JWT verification is broken. Check:
```bash
# Are OAUTH_ISSUER values identical on both containers?
docker inspect ecm-ecm-1 | python3 -m json.tool | grep -A1 OAUTH_ISSUER
docker inspect ecm-mcp-1 | python3 -m json.tool | grep -A1 OAUTH_ISSUER
```
They must match exactly. See `mcp-https-reverse-proxy.md` → "Silent 401s on every tool call".

---

## Step 3: Token Refresh (Connector Reconnect)

**What this verifies**: the OAuth token refresh path works — the connector can re-authorize after a token expiry without requiring the operator to manually re-add the connector.

**Requires**: Connector from Step 1 connected. Access tokens have a short TTL (30 minutes by default); for this check, simulate expiry by revoking the token in the ECM UI.

1. In ECM Settings → MCP Integration → **Active Connections**, find the Claude Desktop connector.

2. Click **Revoke** and confirm.

3. Return to Claude Desktop. Ask Claude to use the ECM connector again:
   ```
   Use the ECM connector to get the system overview.
   ```

4. Claude Desktop should detect the revoked token and trigger a re-authorization. Your browser opens the ECM consent screen again. Authorize.

5. Claude Desktop reconnects and completes the tool call.

**Pass criteria**:
- [ ] After revoke, Claude Desktop prompts for re-authorization (does not silently fail)
- [ ] Re-authorization succeeds without operator having to remove and re-add the connector
- [ ] Tool call after re-authorization returns data

**Note**: If Claude Desktop does not detect the revocation and silently fails with an error on tool calls, that is a UX regression — file a bead before releasing. The operator expectation is that a re-authorization prompt appears, not a cryptic failure.

---

## Step 4: Static API Key Path (Backward Compat)

**What this verifies**: the permanent `?api_key=` path continues to work alongside the OAuth path. This is a non-negotiable backward-compatibility check (PO decision #4: the static key path is permanent, no deprecation).

**Requires**: `mcp_api_key` configured in ECM Settings → MCP Integration.

1. Retrieve your MCP API key from ECM Settings → MCP Integration (or from `settings.json`).

2. Make a direct curl request using the static key:
   ```bash
   curl -s "http://YOUR_ECM_HOST:6101/mcp?api_key=YOUR_MCP_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
     | python3 -m json.tool | head -20
   ```

3. Expect a JSON response listing available tools (not a 401 or 403).

**Pass criteria**:
- [ ] Static key request returns 200 with tools list
- [ ] `?api_key=` variant works (not just `Authorization: Bearer <static-key>` variant)
- [ ] MCP logs do not show any attempt to validate this as a JWT (static-key path, not OAuth path)

---

## Step 5: Settings Panel Smoke Check

**What this verifies**: the in-app MCP Settings panel correctly reflects the current state.

1. Open ECM Settings → MCP Integration.

2. Verify:
   - [ ] Server Status badge shows "MCP server online — N tools available"
   - [ ] API Key section shows "API key is configured"
   - [ ] If there are active OAuth connections from Steps 1–3, "Active Connections" section lists them with correct client name, grant time, and last-used time
   - [ ] Revoke button on a connection triggers inline confirmation (not a modal)
   - [ ] Connection Instructions section shows both Custom Connector setup block and mcp-remote block

---

## Sign-Off

When all steps pass, record sign-off in the release PR description:

```
### MCP Release Verification (docs/runbooks/mcp-release-verification.md)
- [x] Step 1: Custom Connector OAuth flow — connected successfully
- [x] Step 2: Tool call via OAuth connector — list_channel_groups returned data
- [x] Step 3: Token refresh / re-authorization — connector re-authorized after revoke
- [x] Step 4: Static API key path — ?api_key= returns 200 with tools list
- [x] Step 5: Settings panel smoke check — status, grants, instructions correct
Verified by: [your name], [date], on ECM vX.Y.Z-NNNN against [environment description]
```

---

## Known Open Item: redirect_uri placeholder

ECM's registered `redirect_uri` for Claude Desktop is currently a placeholder pending verification (bead `enhancedchannelmanager-buiqr.6`). Step 1 of this checklist (Add custom connector) will fail if the placeholder URI does not match what Claude Desktop actually sends. If Step 1 fails with a `redirect_uri mismatch` error:

1. Check the error in ECM backend logs: `docker logs ecm-ecm-1 2>&1 | grep "redirect_uri" | tail -5`
2. Note the actual `redirect_uri` Claude Desktop sent.
3. File an update to bead `buiqr.6` with the actual URI.
4. Update the hardcoded client registry and re-test.

This item blocks the Custom Connector path until resolved. The static-key checks (Steps 3–5) are unaffected.

---

## References

- `docs/runbooks/mcp-https-reverse-proxy.md` — HTTPS proxy setup (required for Step 1)
- `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md` — dual-path routing and OAUTH_ISSUER requirements
- `docs/shipping.md` — full release cut procedure (this checklist is linked from there)
