---
type: "query"
date: "2026-04-24T12:57:24.096178+00:00"
question: "What is the ECM MCP server architecture?"
contributor: "graphify"
source_nodes: ["MCP", "server.py", "ECMClient", "FastMCP"]
---

# Q: What is the ECM MCP server architecture?

## Answer

Separate Docker container (mcp-server/) exposing ECM operations to AI agents via MCP protocol. Transport: SSE via FastMCP + Starlette on port 6101. Entry: server.py builds a Starlette app with an APIKeyAuthMiddleware that guards /sse and all paths except /health and /messages/ (session-bound after SSE handshake). 14 tool modules (channels, channel_groups, streams, m3u, epg, auto_creation, export, ffmpeg, tasks, stats, system, notifications, profiles, normalization) registered via tools/__init__.register_all_tools; they report tools_available=80 total. 3 read-only resources (stats/overview, channels/summary, tasks/status). ECMClient in ecm_client.py is an httpx.AsyncClient wrapper with get/post/patch/put/delete and per-call timeout overrides (30s default, 300s LONG_TIMEOUT for pipelines/probes/exports). Config reads /config/settings.json on every request (key rotation without restart). Dual auth: inbound MCP client authenticates to MCP server with settings.json:mcp_api_key (via ?api_key= or Authorization: Bearer); outbound MCP server authenticates to ECM backend at ECM_URL=http://ecm:6100 with the same key as Bearer token. Client is recreated when key rotates. Most tools wrap a single ECM endpoint; some orchestrate multi-step flows (set_logo_from_epg does read-read-create-patch per channel; build_channel_lineup does bulk-create then fuzzy-match streams using _score_match/_generate_variants shared helpers from tools/streams.py). Shares /config/ volume with ECM container.

## Source Nodes

- MCP
- server.py
- ECMClient
- FastMCP