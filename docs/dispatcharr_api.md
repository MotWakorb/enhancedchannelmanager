# Dispatcharr API Reference

## Schema / OpenAPI

> **Corrected 2026-08-03** (bead `enhancedchannelmanager-r9oqx`, PR #765 team review). This section previously said the schema endpoint was `/swagger.json`, "returns YAML despite the name." That was wrong for Dispatcharr 0.28.2 and the staleness sat behind three shipped bugs (`enhancedchannelmanager-q6xjl`, `-lsa0s`, and the dry-run parity fix in `-y6zg6`) before anyone re-checked it against a live instance.

Dispatcharr 0.28.2 serves its API schema via **drf-spectacular** at:

- **Schema endpoint:** `GET /api/schema/`
- **Format:** **YAML by default** (`content-type: application/vnd.oai.openapi`). A bare `GET` returns a YAML document, not JSON — `response.json()` raises `Expecting value: line 1 column 1 (char 0)` on it.
- **To get JSON:** pass `?format=json`. ECM must always request `GET /api/schema/?format=json` — this is fix B of bead `q6xjl`.
- **Swagger UI:** `http://<dispatcharr-host>:9191/api/swagger/` (unaffected — this is the rendered UI, not the schema document itself).

To fetch programmatically from within ECM (see `backend/dispatcharr_client.py::get_user_schema_write_fields` for the live implementation):
```python
response = await client._request("GET", "/api/schema/", params={"format": "json"})
response.raise_for_status()
doc = response.json()
paths = doc.get("paths", {})
```

There is no `/swagger.json` route on 0.28.2 and no YAML-despite-the-name behavior on `/api/schema/` — the renderer is exactly what its content-type says, and the fix is the query parameter, not a different parser.

### How to verify a path against the live schema

Three of ECM's Dispatcharr integration bugs (`q6xjl`, `lsa0s`, and the settings-agents importer's original guesses) trace back to a path being asserted from memory or a stale doc instead of checked against the running instance. Before adding or trusting a claim about a Dispatcharr endpoint:

1. Fetch the live schema: `GET /api/schema/?format=json` (see above — do not fetch the bare route).
2. Check the `paths` object in the response for the exact path you intend to call, and inspect its `get`/`post`/`patch`/`delete` keys for the request/response shape.
3. If you cannot reach a live instance, use the recorded fixtures below — they are literal slices of a real 0.28.2 response, not hand-written examples:
   - `backend/tests/fixtures/dispatcharr_openapi_recorded.json` — a slice of the `/api/schema/?format=json` document (the `/api/core/settings/` and `/api/core/settings/{id}/` path items, plus the `User` component schema).
   - `backend/tests/fixtures/dispatcharr_core_settings_recorded.json` — the shape of `GET /api/core/settings/` (a bare list, non-contiguous integer ids).
   - `backend/tests/fixtures/dispatcharr_dvr_recurring_rules_recorded.json` — the shape of `GET /api/channels/recurring-rules/` and the dead-endpoint capture for the old `/api/dvr/rules/` guess.
   - `backend/tests/fixtures/dispatcharr_openapi_paths_manifest.json` — **every** path and method the live 0.28.2 document exposes (all 224), with bodies stripped. Use it to answer "does this path exist, and does it allow this method?" without a live instance; use the three fixtures above when you need the *shape* of a response.
4. A path that "sounds right" by analogy to another resource is a guess, not a fact — Dispatcharr's namespacing is not fully consistent (see the DVR rules case below), and a wrong guess here has shipped a JSON-parse failure, a 404 on every apply, and a silently-empty backup category.

### The automated sweep (ADR-014)

You do not have to remember step 4. `backend/tests/unit/test_dispatcharr_client_contract_sweep.py` runs in the normal pytest gate and checks **every** `(method, URL template)` in `backend/dispatcharr_client.py` — derived from the source with an `ast` walk, never a hand-maintained list — against the recorded paths manifest above. It asserts path existence, allowed method, and path-parameter count/type; request and response **body shape are out of scope** (that is what the deep fixtures in step 3 are for). See [ADR-014](adr/ADR-014-dispatcharr-api-drift-strategy.md) for why the two mechanisms are complements, not substitutes.

**Re-recording the manifest.** Deliberate, not scheduled — when adopting a new Dispatcharr version, or when a PR adds client methods the manifest predates:

```bash
# Capture the raw document (read-only GET; ?format=json is required)
curl -s 'http://<dispatcharr-host>:9191/api/schema/?format=json' \
    -H 'X-API-Key: <key>' > /tmp/disp_schema.json
# Read the version the same way ECM's advisory does
curl -s 'http://<dispatcharr-host>:9191/api/core/version/' -H 'X-API-Key: <key>'

python scripts/record_dispatcharr_openapi_manifest.py \
    --schema /tmp/disp_schema.json --dispatcharr-version 0.28.2
```

The script performs no network request of its own, so CI stays hermetic. Read the resulting fixture diff — it is a readable summary of what moved upstream. If you also intend to *support* the new version, update `TESTED_DISPATCHARR_SERIES` in `backend/dispatcharr_client.py` in the same change, or the connection test will keep warning operators that it is untested.

## Core Settings (`/api/core/settings/`)

> Added 2026-08-03 alongside the schema correction above — this contract was previously undocumented and its absence was the root cause of bead `q6xjl` (a 404 on every settings-restore key).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/core/settings/` | List all core/global settings. |
| GET/PUT/PATCH/DELETE | `/api/core/settings/{id}/` | Retrieve / replace / update / delete **one row, by its integer primary key.** |

**The detail route is keyed by the row's integer `id`, not by the setting's `key` string.** `CoreSettingsViewSet` is a plain DRF `ModelViewSet` with the default `pk` lookup — there is no `/api/core/settings/{key}/` route. A key-string URL matches nothing and 404s; this is exactly how bead `q6xjl` failed 7/7 settings on a same-instance restore round-trip.

### List→key→id resolution pattern

Because a backup artifact stores settings as `key -> value` (ids are per-instance and are never carried in an artifact — see `routers/backup.py::_normalize_core_settings`), a restore must resolve each key to the *destination's* row id before it can PATCH anything. ECM does this with `DispatcharrClient.get_core_setting_id_map()` (`backend/dispatcharr_client.py`), used by the run-scoped `CoreSettingIdResolver` (`backend/dbas/importers/settings_agents.py`):

1. **One `GET /api/core/settings/` per restore run** (apply *or* dry-run), never one request per key — the map is fetched lazily and memoized, including the failure case, so a dead destination cannot turn into a per-key fetch storm.
2. Each returned row is `{"id": int, "key": str, "name": str, "value": ...}`; the map is built as `{row["key"]: row["id"]}`.
3. The resolved id is then used for `PATCH /api/core/settings/{id}/` with body `{"value": ...}` — a per-row PATCH, never a bulk PUT that could clobber unrelated keys.

### Bare-list vs. paginated-envelope handling

The live 0.28.2 response is a **bare JSON list** of rows (confirmed by the recorded fixture — 7 rows, non-contiguous ids). `get_core_setting_id_map()` also accepts the alternative shape a paginating destination could return, a DRF-style envelope `{"results": [...], "next": ...}`, and fetches every page until `next` is falsy. A non-null `next` is treated as a more-pages *signal*, not a URL to dereference — each subsequent page is re-requested against the same `/api/core/settings/` path with an incrementing `?page=` query parameter (the client's `_request` convention takes a path, not an arbitrary URL). This assumes Dispatcharr's page-numbered pagination scheme; it would not work if a future version switched to cursor/offset-based pagination.

**20-page cap:** pagination is bounded by `_CORE_SETTINGS_MAX_PAGES = 20`. Exceeding it raises `RuntimeError` loudly rather than silently returning a partial map — a backstop against a misbehaving or infinitely-linking destination, not a fix for a mismatched pagination scheme.

**Drop-don't-guess rule:** a row without a usable string `key` or without an integer `id` is **dropped from the map**, never guessed at. An absent key then makes the caller fail that key explicitly (`DEPENDENCY_UNRESOLVED`); a guessed id would instead risk PATCHing an unrelated setting. (Known documented limitation: if the destination has a duplicate `key` across rows, the later row's id silently wins — last-write-wins, never validated against a live instance.)

## DVR Rules

> Corrected 2026-08-03 (bead `enhancedchannelmanager-lsa0s`). ECM's `dvr_rules` backup category previously targeted `/api/dvr/rules/`, which **does not exist on any Dispatcharr version.** A request to it falls through to the SPA's catch-all route and returns `200 text/html` (the app shell), not a 404 — which is why `get_dvr_rules()` raised a JSON-parse error and every backup silently exported a `_warning` stub for the category instead of failing loudly.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/channels/recurring-rules/` | List recurring recording rules (`RecurringRecordingRule`: `name` + a single `channel` FK + weekly schedule). |
| POST | `/api/channels/recurring-rules/` | Create a recurring rule. |
| DELETE | `/api/channels/recurring-rules/{id}/` | Delete a recurring rule. |

The real recurring-rules resource is `/api/channels/recurring-rules/` — this is what ECM's `dvr_rules` backup/restore category now targets. Two related Dispatcharr DVR surfaces are deliberately **not** part of this category:

- **Series rules** live inside the `dvr_settings` key of the `/api/core/settings/` blob (the `core_settings` category already carries them) — they are not a separate REST resource.
- **Recordings** (`/api/channels/recordings/`) are per-instance runtime state, not a rule definition, and are out of scope for a config backup.
- **`/api/channels/dvr/comskip-config/` exists** (an earlier note here claimed Dispatcharr had no separate comskip endpoint — wrong; it does). But it is **not a usable backup source**: `GET` returns only `{"path": "...", "exists": bool}` — never the `comskip.ini` file *content* — and `POST` takes a multipart `.ini` file upload, not a JSON body. There is nothing to export from it and nothing to restore into it via JSON. Comskip *config values* that ECM does back up live as ordinary keys inside the same `/api/core/settings/` blob (split out client-side by a `comskip`-prefixed key match, not fetched from this endpoint).

The live response for `/api/channels/recurring-rules/` is a **bare JSON list** on 0.28.2 (same shape caveat as core settings above: a destination that paginates this ViewSet would return a DRF `{"results": [...]}` envelope instead, and `get_dvr_rules()` normalizes both shapes to a list).

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
**not** a separate mechanism. It is just a `source_type` value with credential fields
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
- `password`: SD account password. It is **write-only**: Dispatcharr never returns it
  and SHA1-hashes it at fetch time. ECM uses preserve-on-omit: omit `password`
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
