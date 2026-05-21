# Media Server Integrations

> **Audience:** Operator who wants to see viewer usernames (e.g., "Alice via Emby") instead of raw IP addresses in the Stats tab.

ECM cross-references its bandwidth telemetry against your media server's
live-session API to surface WHO is watching WHAT in the Stats tab. You can
enable one or more of Emby, Plex, and Jellyfin. Each integration is
independent — enabling one does not require the others.

## Why enable an integration?

Without an integration, the Stats tab Connected Clients list shows your
viewers by IP address only (e.g., `192.168.1.42`). With an integration
enabled, it shows usernames (e.g., `Alice via Emby`).

## What you see when it's working

- **Connected Clients**: each viewer-row shows a username + a "via Emby" /
  "via Plex" / "via Jellyfin" badge. When multiple users share a channel
  through the same media server, all their names are listed. (Example: if
  Alice and Bob are both watching ESPN via Emby, you'll see both names on
  the ESPN row.)
- **User Stats panel**: per-user watch-time aggregated across whichever
  integration attributed each session.

## Setup — Emby

1. In ECM: Settings → Integrations → Emby
2. Enable the toggle
3. Base URL: the URL of your Emby server (e.g., `http://emby:8096`)
4. API Key: get this from your Emby Dashboard → API Keys (top-right menu) → "+" to create a new key
5. Click "Test Connection" to verify
6. Save

## Setup — Plex

1. In ECM: Settings → Integrations → Plex
2. Enable the toggle
3. Base URL: the URL of your Plex Media Server (e.g., `http://plex:32400`)
4. Plex Token: **use a server-local token, NOT your plex.tv account token**.
   The server-local token has scope limited to your specific Plex server;
   the plex.tv account token has full account scope and is a more sensitive
   credential. To find your server-local token:
   - Open Plex Web (`http://plex:32400/web`)
   - Play any item (movie, episode, channel)
   - Right-click the playing item → "Get Info" → "View XML"
   - In the URL bar of the XML page, look for `X-Plex-Token=...` — that's
     your token
   - Alternatively, see [the official Plex support article on finding tokens](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
5. Click "Test Connection" to verify
6. Save

## Setup — Jellyfin

1. In ECM: Settings → Integrations → Jellyfin
2. Enable the toggle
3. Base URL: the URL of your Jellyfin server (e.g., `http://jellyfin:8096`)
4. API Key: get this from your Jellyfin Dashboard → API Keys → "+" to
   create a new key
5. Click "Test Connection" to verify
6. Save

## Known limitations

**Multi-client disambiguation is best-effort.** When two viewers from the
same source (e.g., Alice and Bob both via Emby) are watching the SAME
channel and ECM cannot tell their connections apart, the Connected Clients
list shows each row as a `"2 viewers: Alice, Bob"` rollup rather than
guessing which row is which person. The username SET is always accurate;
only the per-row identity is rolled up when it can't be determined. (A
single viewer, or viewers ECM can distinguish, still show their individual
name.) Attribution itself is networking-agnostic — it works whether your
viewers reach ECM directly, through a media server, or NAT'd through a
Docker bridge; it does not depend on the connection's source IP matching
the media server's IP.

**Two viewers behind the same NAT IP collapse in stored history.** The
live Stats tab can distinguish two viewers even when they share one network
address (e.g., two devices behind the same router/VPN exit), because it
uses each connection's stable client id. The persisted watch-history /
watch-time tables, however, key on `(channel, IP)`, so two same-channel
viewers behind the **same** IP are recorded as a single history entry
attributed to one of them. They are correctly distinguished live; only the
stored aggregate collapses. This affects only the same-IP case — viewers on
different addresses are recorded separately.

## Privacy posture

API keys and Plex tokens are stored plaintext in ECM's settings file. The
container-resident settings file is operator-owned filesystem. ECM does
not transmit these credentials anywhere except to the configured media
server.

## Troubleshooting

- **"Connection refused" on Test Connection**: verify your media server is
  reachable from the ECM container. Try `docker exec ecm-ecm-1 wget -qO- <base_url>` to confirm network reach.
- **"Unauthorized" on Test Connection**: regenerate the API key in your
  media server. For Plex, confirm you're using a server-local token.
- **No usernames in Connected Clients**: confirm the integration is
  enabled and Test Connection succeeded. The Connected Clients list
  updates within 5 seconds of a session starting (per the session cache
  TTL).

---

## MCP Integration (Claude AI)

ECM's MCP sidecar container exposes all ECM operations to Claude Desktop and
Claude Code through the Model Context Protocol. Two connection paths are
supported:

| Path | When to use |
|---|---|
| **Custom Connector (OAuth)** | Claude Desktop without Node.js — requires HTTPS in front of MCP |
| **mcp-remote bridge** | Claude Desktop with Node.js installed |
| **`.mcp.json`** | Claude Code — always, no HTTPS or Node required |

For the full walkthrough — including the step-by-step OAuth consent flow,
key rotation, and troubleshooting — see the
**[MCP Integration reference](mcp.md)**.

---

## Going deeper

- API response fields: [`docs/api.md` — Enhanced Stats § Per-channel attribution fields](../../api.md)
- Pipeline internals: [`docs/architecture.md` — User Attribution Pipeline](../../architecture.md)
- MCP architecture: [`docs/adr/ADR-009`](../../adr/ADR-009-mcp-oauth-authorization-server-split.md) — ECM as OAuth Authorization Server
