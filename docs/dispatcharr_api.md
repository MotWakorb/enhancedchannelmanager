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
   - `backend/tests/fixtures/dispatcharr_recordings_recorded.json` — `GET`/`POST`/`DELETE` on `/api/channels/recordings/`, captured on **0.29.0** (the others are 0.28.2). The only fixture here with populated rows *and* a refused request: it records the `400 "End time must be in the future."` a past-dated create earns, and the destination writing its own key into `custom_properties` between the 201 and the next GET.
   - `backend/tests/fixtures/dispatcharr_openapi_paths_manifest.json` — **every** path and method the live 0.28.2 document exposes (all 224), with bodies stripped. Use it to answer "does this path exist, and does it allow this method?" without a live instance; use the fixtures above when you need the *shape* of a response.
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

## Concurrency: 0.28.x offers no conditional update

**Measured, not assumed.** Bead `enhancedchannelmanager-auocn`, 2026-08-15, against a live Dispatcharr 0.28.2 instance. `GET /api/schema/?format=json` returned `200` and roughly 716 KB of JSON. Searched in full, that document contains:

| Looked for | Occurrences |
|-|-|
| `If-Match` | 0 |
| `If-None-Match` | 0 |
| `If-Unmodified-Since` | 0 |
| `ETag` | 0 |
| `412` (as a declared response) | 0 |

Further, `PATCH /api/channels/channels/{id}/` declares the path `id` parameter and nothing else, and the `Channel` and `PatchedChannel` component schemas carry **no** version, revision or modified-at field. So there is not even a token to compare client-side and reject on: the absence is at every layer, not just the HTTP one.

**What this constrains.** Any ECM write that reads a Dispatcharr row, decides something from it, and then writes it back is a read-modify-write with no compare-and-set available. It cannot be made atomic against a concurrent writer, and no amount of client-side care changes that. The mitigations actually available are:

1. **Re-read immediately before the write** and skip when the row has already moved. This narrows the window to one round trip; it does not close it. `backend/channel_group_reparent.py::_still_belongs_to_group` is the worked example, and its docstring says "check, not atomicity" for exactly this reason.
2. **Withhold the write only on positive evidence.** A re-read that fails, or returns a shape without the field being checked, must proceed. Refusing on no evidence converts a rare lost update into a common outright failure.
3. **Report what actually happened**, so a skipped write is a logged, counted outcome rather than a silent one.

**Before proposing optimistic concurrency for a Dispatcharr write, re-run the measurement above against the version you are targeting.** This finding is pinned to 0.28.x. If a later release adds `ETag` or a `modified_at` field, this section is what should change, and the re-record procedure in [The automated sweep (ADR-014)](#the-automated-sweep-adr-014) already captures the schema you would check it in.

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

The real recurring-rules resource is `/api/channels/recurring-rules/` — this is what ECM's `dvr_rules` backup/restore category targets. Two related Dispatcharr DVR surfaces are deliberately **not** part of *this* category:

- **Series rules** live inside the `dvr_settings` key of the `/api/core/settings/` blob (the `core_settings` category already carries them) — they are not a separate REST resource.
- **Recordings** (`/api/channels/recordings/`) are recording INSTANCES rather than rule definitions, so they are not `dvr_rules`. They get their **own** category — see [Recordings](#recordings) below. (An earlier note here said they were "out of scope for a config backup" outright; bead `enhancedchannelmanager-ciabe` corrected that. The consequence of the blanket exclusion was that NO category covered recordings at all, so a restore silently produced a replica with none of the operator's scheduled recordings.)
- **`/api/channels/dvr/comskip-config/` exists** (an earlier note here claimed Dispatcharr had no separate comskip endpoint — wrong; it does). But it is **not a usable backup source**: `GET` returns only `{"path": "...", "exists": bool}` — never the `comskip.ini` file *content* — and `POST` takes a multipart `.ini` file upload, not a JSON body. There is nothing to export from it and nothing to restore into it via JSON. Comskip *config values* that ECM does back up live as ordinary keys inside the same `/api/core/settings/` blob (split out client-side by a `comskip`-prefixed key match, not fetched from this endpoint).

The live response for `/api/channels/recurring-rules/` is a **bare JSON list** on 0.28.2 (same shape caveat as core settings above: a destination that paginates this ViewSet would return a DRF `{"results": [...]}` envelope instead, and `get_dvr_rules()` normalizes both shapes to a list).

## Recordings

> Added 2026-08-23 (bead `enhancedchannelmanager-ciabe`). Measured live against **Dispatcharr 0.29.0** (`GET /api/core/version/`); the full capture, including the two behaviours below that the model source does not reveal, is in `backend/tests/fixtures/dispatcharr_recordings_recorded.json`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/channels/recordings/` | List recording instances. **Bare JSON list**, same pagination caveat as recurring-rules; `get_recordings()` normalizes both shapes. |
| POST | `/api/channels/recordings/` | Schedule a recording. |
| DELETE | `/api/channels/recordings/{id}/` | Delete a recording (204). |

The `Recording` serializer is the whole model: `{id, start_time, end_time, task_id, custom_properties, channel}`. `id` and `task_id` are `readOnly` — `task_id` is Dispatcharr's own celery handle (`dvr-recording-<id>`) and must never be sent on a create.

Three facts that are **not** derivable from the model source and each of which would ship as a defect if assumed:

1. **There is no status column.** The upcoming/completed distinction is not a field. `custom_properties["status"]` exists (`scheduled` / `recording` / `completed` / `interrupted` / `stopped`) but it is a free-form JSONField key the DVR pipeline writes, absent on a manually created row. The only always-present discriminator is the absolute `start_time` — which is also what Dispatcharr itself uses to mean "upcoming" in `BulkDeleteUpcomingRecordingsAPIView` (`Recording.objects.filter(start_time__gt=now)`).
2. **A past-dated create is refused**: `400 {"non_field_errors": ["End time must be in the future."]}`. ECM's own filter gates on `start_time`, which is strictly stricter, so it never hands upstream a create this validator would reject.
3. **The destination rewrites `custom_properties` after the create.** A recording POSTed with `custom_properties: {}` came back from the very next GET carrying `{"poster_logo_id": 316}` that Dispatcharr's artwork pass had written. It is therefore unusable as an identity field; ECM matches recordings on `(channel, start_time, end_time)`, comparing timestamps as parsed instants because the server also re-serializes them (a second-precision `start_time` came back at microsecond precision).

ECM's `upcoming_recordings` category carries only recordings that have not started. Already-started/finished ones name a media file on the source instance's disk and cannot be carried by any API; rule-generated ones are regenerated on the destination by its own hourly `maintain_recurring_recordings` beat from the restored `dvr_rules`. Both exclusions are reported to the operator in the backup run message (ADR-013).

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
