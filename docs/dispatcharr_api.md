# Dispatcharr API Reference

## Swagger / OpenAPI

Dispatcharr exposes its API schema at `/swagger.json` (returns YAML despite the name).

- **Swagger UI:** `http://<dispatcharr-host>:9191/api/swagger/`
- **Schema endpoint:** `http://<dispatcharr-host>:9191/swagger.json`
- **Format:** YAML (use `yaml.safe_load()`, not `json.loads()`)

To fetch programmatically from within ECM:
```python
resp = await client._request("GET", "/swagger.json")
swagger = yaml.safe_load(resp.text)
```

## Key Logo Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/channels/logos/` | List logos (paginated, supports `search` param) |
| POST | `/api/channels/logos/` | Create logo by URL (`{"name": "...", "url": "..."}`) |
| POST | `/api/channels/logos/upload/` | Upload logo file (multipart: `file` + `name` field) |
| GET | `/api/channels/logos/{id}/` | Get single logo |
| PATCH | `/api/channels/logos/{id}/` | Update logo |
| DELETE | `/api/channels/logos/{id}/` | Delete logo |
| DELETE | `/api/channels/logos/bulk-delete/` | Bulk delete (`{"logo_ids": [...]}`) |
| POST | `/api/channels/logos/cleanup/` | Clean unused logos |
| GET | `/api/channels/logos/{id}/cache/` | Get cached logo image |

### Logo Upload (multipart)
```python
response = await client._request(
    "POST", "/api/channels/logos/upload/",
    files={"file": (filename, content_bytes, content_type)},
    data={"name": "Logo Name"},
)
# Returns: {"id": 123, "name": "...", "url": "/data/logos/filename.png", "cache_url": "..."}
```
The file is stored and served by Dispatcharr. The `url` field is a relative path on the Dispatcharr server.

## EPG Sources & Schedules Direct (SD)

Dispatcharr models all EPG sources with a single `EPGSource` resource keyed by a
`source_type` enum: `xmltv`, `schedules_direct`, or `dummy`. Schedules Direct is
**not** a separate mechanism — just a `source_type` value with credential fields
and SD-specific account endpoints.

### EPG source CRUD

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/epg/sources/` | List EPG sources |
| POST | `/api/epg/sources/` | Create (`source_type` required) |
| GET/PATCH/DELETE | `/api/epg/sources/{id}/` | Retrieve / update / delete |
| POST | `/api/epg/import/` | Trigger import (`{"id": source_id}`) |

**Schedules Direct credential fields** (on the source body):

- `source_type`: `"schedules_direct"`
- `username`: SD account username
- `password`: SD account password — **write-only** (Dispatcharr never returns it
  and SHA1-hashes it at fetch time). ECM uses preserve-on-omit: omit `password`
  on PATCH to keep the stored one.
- `custom_properties` (JSON bag): `logo_style` (`dark`/`white`/`gray`/`light`),
  `poster_style`, `auto_apply_epg_logos`, `fetch_posters`. The SD lineups GET also
  surfaces read-only `sd_changes_remaining` / `sd_changes_reset_at`.

> Migration note: older Dispatcharr stored a single `api_key` for SD. That field
> was **removed** (Dispatcharr migration `0024`) in favor of `username`/`password`.

### Schedules Direct account/lineup endpoints

Lineups live on the SD account, not Dispatcharr's DB. Each call below
authenticates to SD live and is rate-limited **by SD** (lineup adds ~6/24h;
full refresh ~200 requests/2h). Do not wrap these in retry/polling loops.

| Method | Path | Body / returns |
|--------|------|----------------|
| GET | `/api/epg/sources/{id}/sd-lineups/` | → `{lineups, max_lineups, changes_remaining, changes_reset_at}` |
| POST | `/api/epg/sources/{id}/sd-lineups/` | `{"lineup": "USA-NJ29486-X"}` (admin) |
| DELETE | `/api/epg/sources/{id}/sd-lineups/` | `{"lineup": "..."}` (admin) |
| POST | `/api/epg/sources/{id}/sd-lineups/search/` | `{"country": "USA", "postalcode": "07030"}` → `{lineups:[{lineup,name,transport,location,headend}]}` |
| GET | `/api/epg/programs/{id}/poster/` | SD program poster image (AllowAny on Dispatcharr) |

After adding a lineup, Dispatcharr fetches station metadata so channels can be
matched before the next full schedule pull.
