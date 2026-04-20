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
