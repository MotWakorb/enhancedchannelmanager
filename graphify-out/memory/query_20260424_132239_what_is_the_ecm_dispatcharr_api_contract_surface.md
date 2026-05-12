---
type: "query"
date: "2026-04-24T13:22:39.922259+00:00"
question: "What is the ECM-Dispatcharr API contract surface?"
contributor: "graphify"
source_nodes: ["DispatcharrClient", "channels_api_urls", "m3u_api_urls", "epg_api_urls"]
---

# Q: What is the ECM-Dispatcharr API contract surface?

## Answer

ECM's DispatcharrClient (backend/dispatcharr_client.py) makes 73 named calls across 14 domains, mapping to 4 of Dispatcharr's 13 Django apps. By volume: M3U (20 methods: accounts, profiles, filters, group-settings, refresh — apps/m3u/), Channels+Logos+Streams+Groups+Profiles (34 methods — apps/channels/), EPG (10 methods: sources, data, grid, import — apps/epg/), Users + runtime (3: get_users, stop_channel, stop_client, get_system_events — apps/accounts/ and apps/proxy/connect). ECM does NOT touch Dispatcharr's apps: hdhr, vod, backups, plugins, dashboard, output. 47 of 73 methods have direct camelCase counterparts in Dispatcharr's own frontend (frontend/src/api.js) — the two clients are symmetric, strong evidence of a stable API contract. The other 26 are mostly CRUD ops on DRF ModelViewSets (where list/retrieve/create/update/destroy are auto-generated and don't appear as separate frontend functions) plus specialized M3U/EPG triggers. No drift flagged but 26 unmatched methods are the surface to watch for frontend/backend divergence in Dispatcharr.

## Source Nodes

- DispatcharrClient
- channels_api_urls
- m3u_api_urls
- epg_api_urls