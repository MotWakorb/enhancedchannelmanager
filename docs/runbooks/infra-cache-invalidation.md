# Runbook: Infrastructure-Side Cache Invalidation (nginx / Cloudflare / Varnish / Generic CDN)

> Companion to [Dep-Bump Frontend Regression](./dep-bump-frontend-regression.md).
> That runbook covers browser-side invalidation (hard reload, service-worker
> flush). This one covers the layer in front of ECM — reverse proxies and
> CDNs — where a stale cached `index.html` or `/assets/*` chunk can mask a
> successful server-side deploy and leave users on a broken bundle.

- **Severity**: P2 (degraded UX, not a server outage) — promotes to P1 if it co-occurs with a frontend regression and blocks rollback verification.
- **Owner**: SRE (this runbook); Operator (performs flush against their own infra — Cloudflare/nginx/Varnish are typically outside the ECM container).
- **Last reviewed**: 2026-04-24
- **Related beads**: `enhancedchannelmanager-11t6c` (this runbook), `enhancedchannelmanager-pm1t4` (browser-side companion).

## When to use this runbook

Run this **after any frontend release** (any deploy that changes `/app/static/*`) **if** ECM is fronted by a reverse proxy or CDN that caches responses. Common shapes:

- **nginx / Apache / Caddy / Traefik** in front of ECM, with any form of response caching (`proxy_cache`, `fastcgi_cache`, edge caching modules).
- **Cloudflare** with Cache Rules, Page Rules, or the default "cache static content" behavior.
- **Varnish** in front of ECM.
- **Commercial CDNs** (Fastly, Akamai, CloudFront, Bunny.net, etc.) with edge caching enabled.
- **Corporate / ISP transparent proxies** — rarer, but seen in enterprise deploys; harder to invalidate.

Do **not** run this runbook if:

- The deploy did not change frontend assets (backend-only change, config change, env var bump).
- ECM is reached directly (browser → ECM) with nothing in between — the [browser-cache runbook](./dep-bump-frontend-regression.md#4-browser-cache-verification--is-the-user-on-the-rolled-back-bundle) is sufficient.

## Why infra-side caches matter

ECM's frontend deploy is a two-step atomic swap:

1. Clean stale chunks: `docker exec ecm-ecm-1 sh -c 'rm -rf /app/static/assets/*'`
2. Copy fresh bundle: `docker cp dist/. ecm-ecm-1:/app/static/`

After step 2, the container serves a new `index.html` pointing at new
content-hashed chunks (e.g. `/assets/index-abc12345.js`). The previous
chunks with their old hashes are gone from disk. This is correct by design
— Vite's default asset-hash strategy (see [`frontend/vite.config.ts`](../../frontend/vite.config.ts) and
[Vite build options](https://vite.dev/config/build-options)) means every
code change produces a new hashed filename, so the browser can cache
`/assets/*` forever safely. The only file that must not be cached is
`index.html`, which is the pointer to the current hashes.

What breaks this model:

- **A reverse proxy caches `/index.html` or `/`** — users continue to fetch
  the old `index.html`, which points at `/assets/index-<old-hash>.js`.
  That hash is gone from disk; they get a 404 and a blank page.
- **A CDN caches the root path** with a long TTL — same failure, at a wider
  blast radius.
- **A proxy caches `/assets/*` but serves from a stale origin snapshot** —
  users get the new `index.html` pointing at `/assets/index-<new-hash>.js`,
  but the proxy has a cached response for that path from a different origin
  deploy and serves the wrong bytes.

Browser-side invalidation (per the [Dep-Bump Frontend Regression runbook](./dep-bump-frontend-regression.md#forced-cache-invalidation-operator-side)) will not help here — the browser dutifully re-requests `index.html`, the proxy serves the stale copy, and the user sees the same broken bundle they saw before they hit `Ctrl+Shift+R`.

### What ECM emits today

- ECM serves static assets via FastAPI's [`StaticFiles`](https://fastapi.tiangolo.com/reference/staticfiles/) mount (`backend/main.py`, `/assets` and the SPA fallback).
- **ECM does not set `Cache-Control` headers on `/assets/*` or `/index.html`.** Starlette's default `StaticFiles` emits `ETag` and `Last-Modified` only. This means each upstream cache decides its own TTL according to its own heuristics — which is why "why is this still cached?" varies by operator.
- **ECM ships no service worker.** Confirmed in `frontend/vite.config.ts` (no `vite-plugin-pwa`, no `workbox-*`). Any service-worker-induced caching seen in the wild is either a reverse proxy injecting one or a stale SW from a much older ECM build — treat as anomalous.

Operators who want more predictable cache behavior should set `Cache-Control: no-cache` on `/` and `/index.html` and `Cache-Control: public, max-age=31536000, immutable` on `/assets/*` at their proxy. That configuration is the operator's call — ECM does not enforce it. See the per-proxy sections below for copy-pasteable snippets.

## Diagnosis

### 1. Confirm the server-side bundle is correct

Same step as the [browser-cache runbook](./dep-bump-frontend-regression.md#1-confirm-the-deployed-bundle-is-the-suspect):

```bash
docker exec ecm-ecm-1 grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' /app/static/index.html | sort -u
```

Save this list — call it `SERVER_HASHES`. This is what the infrastructure **should** be delivering.

### 2. Fetch `index.html` through every hop

Go outside-in: from the furthest-out cache (CDN edge) toward ECM. At each hop, compare the hashes in the response body to `SERVER_HASHES`.

```bash
# Replace <public-host> with the operator's public hostname — Cloudflare / CDN edge will answer here.
curl -sS https://<public-host>/ | grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' | sort -u

# If you can reach the origin directly (nginx/Varnish on the host OS, or the
# reverse proxy's "origin" / "upstream" URL), repeat against that host:
curl -sS http://<origin-host>:<origin-port>/ | grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' | sort -u

# Inside the container (ground truth):
docker exec ecm-ecm-1 curl -sS http://localhost:${ECM_PORT:-6100}/ | grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' | sort -u
```

Decision tree:

- **All three match `SERVER_HASHES`** → infra is not caching `index.html`. If users still hit a stale bundle, check their browser (run the [browser-cache runbook](./dep-bump-frontend-regression.md#4-browser-cache-verification--is-the-user-on-the-rolled-back-bundle)).
- **CDN edge differs, origin matches** → CDN is holding a stale `index.html`. Go to the Cloudflare / CDN section.
- **Origin host differs, container matches** → reverse proxy (nginx/Varnish/etc.) is holding it. Go to the matching proxy section.
- **Container differs from what you just deployed** → not a cache problem; the server-side deploy didn't take. Re-run the deploy steps and verify `ls -la /app/static/assets/` shows fresh mtimes.

### 3. Inspect cache headers

`curl -I` tells you which hop answered. The relevant headers differ by proxy, but the pattern is the same: look for any header that identifies the cache layer and its HIT/MISS/AGE status.

```bash
curl -sSI https://<public-host>/
```

| Header | Emitted by | What to read |
|-|-|-|
| `Age: <seconds>` | Generic HTTP caches (RFC 9111) | Non-zero `Age` on `/` means some cache is serving from storage, not asking the origin. |
| `CF-Cache-Status: HIT / MISS / DYNAMIC / REVALIDATED / EXPIRED / BYPASS` | Cloudflare | `HIT` on `/` after a deploy is the bug. `DYNAMIC` or `BYPASS` means Cloudflare is not caching — good. See [Cloudflare Cache Status docs](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/#cloudflare-cache-responses). |
| `X-Cache: HIT / MISS` | Varnish (default), CloudFront, many CDNs | `HIT` on `/` after a deploy is the bug. |
| `X-Cache-Status: HIT / MISS / BYPASS / EXPIRED / STALE / UPDATING / REVALIDATED` | nginx `proxy_cache` (`add_header X-Cache-Status $upstream_cache_status`) | Same semantics. See [nginx `$upstream_cache_status`](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#var_upstream_cache_status). |
| `X-Served-By` / `X-Varnish` | Varnish | Presence confirms Varnish is in the path. `X-Varnish: <xid1> <xid2>` (two IDs) indicates a cache hit. |
| `Via: 1.1 varnish` / `Via: 1.1 cloudfront` | Varnish / CloudFront | Identifies the cache vendor in the path. |
| `Cache-Control` | Whatever hop last set it | If absent on `/`, downstream caches apply heuristics — often "cache for 10 minutes" or worse. |
| `ETag` / `Last-Modified` | ECM (via Starlette `StaticFiles`) | Present on direct-from-container responses; proxies may strip them. |

Capture the full header dump to `/tmp/` for the incident record:

```bash
curl -sSI https://<public-host>/ > /tmp/ecm-infra-cache-headers.txt
```

## Resolution

Use the section that matches your infra. The principle is the same everywhere: **purge `/` and `/index.html` first.** Those two URLs are the pointers. Hashed `/assets/*` URLs are content-addressed and safe to leave alone; they will naturally age out. A blanket "purge everything" is fine but wasteful.

### nginx (`proxy_cache`)

Reference: [ngx_http_proxy_module — `proxy_cache`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache), [`proxy_cache_bypass`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache_bypass), [`proxy_cache_purge`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache_purge) (the last is commercial nginx Plus).

Open-source nginx has no built-in purge endpoint. Options, in order of preference:

**Option A — Scoped delete from the cache directory (open-source nginx):**

```bash
# Find your cache root (grep for `proxy_cache_path` in nginx.conf).
grep -r 'proxy_cache_path' /etc/nginx/

# Example — cache root at /var/cache/nginx, key format "$scheme$request_method$host$request_uri":
# Compute the MD5 of the cache key for "/". The key format is declared by
# `proxy_cache_key` in your config — use whatever the config says, not this example blindly.
KEY="httpsGEThttps://<public-host>/"
HASH=$(echo -n "$KEY" | md5sum | awk '{print $1}')
LEVEL1="${HASH:31:1}"
LEVEL2="${HASH:29:2}"

# Delete the cache entry for / and /index.html only:
rm -f /var/cache/nginx/${LEVEL1}/${LEVEL2}/${HASH}

# Or, the nuclear option — wipe the whole proxy cache:
find /var/cache/nginx -type f -delete
nginx -s reload   # reload only if your build uses `proxy_cache_use_stale` with `updating`
```

**Option B — `proxy_cache_bypass` on the next request:**

Add a config block that bypasses the cache when a specific header or query parameter is present, then hit the site once with that header. On hit, nginx re-validates against the origin and overwrites the cached entry.

```nginx
# In the server {} block:
proxy_cache_bypass $http_x_purge_cache;
```

```bash
curl -sSI -H "X-Purge-Cache: 1" https://<public-host>/
# The response should show X-Cache-Status: BYPASS. A subsequent request without
# the header should show MISS → HIT with the new content.
```

**Option C — Recommended config going forward:** exclude `/` and `/index.html` from caching entirely:

```nginx
location = / {
    proxy_pass http://ecm_upstream;
    proxy_cache_bypass 1;
    proxy_no_cache 1;
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;
}

location = /index.html {
    proxy_pass http://ecm_upstream;
    proxy_cache_bypass 1;
    proxy_no_cache 1;
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;
}

location /assets/ {
    proxy_pass http://ecm_upstream;
    proxy_cache ecm_cache;
    proxy_cache_valid 200 30d;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    add_header X-Cache-Status $upstream_cache_status always;
}
```

This moves the problem upstream — hashed assets cache forever (safe), pointers never cache (safe). After this config, future releases do not need a purge step.

### Cloudflare

Reference: [Cloudflare Cache Rules](https://developers.cloudflare.com/cache/how-to/cache-rules/), [Purge cache](https://developers.cloudflare.com/cache/how-to/purge-cache/).

**Dashboard — purge by URL (preferred; surgical):**

1. Log in → select the zone.
2. Caching → Configuration → **Purge Cache** → **Custom Purge**.
3. URL — enter both:
   - `https://<public-host>/`
   - `https://<public-host>/index.html`
4. Purge.

**Dashboard — purge everything (wasteful but fast):**

Caching → Configuration → Purge Cache → **Purge Everything**. Do this only if custom purge does not resolve within a minute or two, or if you cannot identify the exact URLs.

**API — purge by URL:**

```bash
# https://developers.cloudflare.com/api/operations/zone-purge
curl -sS -X POST \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/purge_cache" \
  -d '{"files":["https://<public-host>/","https://<public-host>/index.html"]}'
# Expected: {"success":true,...}
```

**API — purge by prefix (Enterprise plan; check your plan):**

```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/purge_cache" \
  -d '{"prefixes":["<public-host>/"]}'
```

**Recommended config going forward:** create a Cache Rule that bypasses cache for `/` and `/index.html` and sets a long edge TTL for `/assets/*`. Cloudflare's default "cache static content" is usually fine for the hashed assets, but the default may or may not cache `/` depending on your plan — a Cache Rule removes the ambiguity. See [Create a Cache Rule in the dashboard](https://developers.cloudflare.com/cache/how-to/cache-rules/create-dashboard/).

### Varnish

Reference: [Varnish HTTP purge](https://varnish-cache.org/docs/trunk/users-guide/purging.html), [vcl `ban`](https://varnish-cache.org/docs/trunk/reference/vcl-built-in-subs.html#vcl-recv).

Varnish distinguishes three invalidation methods — pick the right one:

| Method | What it does | When to use |
|-|-|-|
| **Purge** | Removes one exact object from cache; next request re-fetches from backend. | Known URL, single object — this is the common case for `/` and `/index.html`. |
| **Ban** | Adds a predicate (regex); future lookups matching it miss until the ban lurker evicts them. | Wildcard invalidation, e.g. all `/assets/*`. Use sparingly — bans accumulate. |
| **Refresh** (aka "force refresh") | Re-validates the cached object against the backend on the next request. | You want to keep old content as a fallback during revalidation; rarely needed for a deploy. |

For a frontend deploy you almost always want **purge**, scoped to `/` and `/index.html`.

**Purge via HTTP `PURGE` method (requires VCL handler):**

Varnish does not purge out of the box — the operator must wire a PURGE handler in VCL. Typical config ([upstream example](https://varnish-cache.org/docs/trunk/users-guide/purging.html#http-purging)):

```vcl
acl purge {
    "127.0.0.1";
    "<ops-jumphost-ip>";
}

sub vcl_recv {
    if (req.method == "PURGE") {
        if (!client.ip ~ purge) {
            return (synth(405, "PURGE not allowed for this client"));
        }
        return (purge);
    }
}
```

Then, from a permitted host:

```bash
curl -sSI -X PURGE http://<varnish-host>:<varnish-port>/
curl -sSI -X PURGE http://<varnish-host>:<varnish-port>/index.html
# Expected: HTTP/1.1 200 Purged (or 404 if the object wasn't in cache — still fine).
```

**Ban (wildcard):**

```bash
# Requires `varnishadm` access on the Varnish host.
varnishadm ban "req.url == / || req.url == /index.html"

# Wildcard example — invalidate every /assets/* path (rare; only if a proxy snapshot
# is serving the wrong bytes for a hashed chunk):
varnishadm ban "req.url ~ ^/assets/"
```

Confirm the ban landed:

```bash
varnishadm ban.list
```

### Generic CDN (Fastly, Akamai, CloudFront, Bunny.net, others)

Principles that apply to any CDN — consult the vendor's docs for exact commands:

1. **Locate the "purge" control.** Every production CDN offers a purge mechanism. The vocabulary differs — "invalidate" (CloudFront), "instant purge" (Fastly), "purge" (Bunny.net / Akamai). All mean the same thing.
2. **Prefer URL-scoped purge over full-cache purge.** The URLs you care about are `/` and `/index.html`. Hashed `/assets/*` paths do not need invalidation — they change filename on every deploy.
3. **Authenticate via the vendor's API token, not shared credentials.** Put the token in an env var; do not paste it into a runbook.
4. **Expect a propagation window.** Edge purge is not instant — common SLAs are 30 seconds to 5 minutes. If `curl` still shows the old content 60 seconds after purge, check the vendor's status page before escalating.
5. **Bypass cache in the client during verification.** Append a cachebust query parameter to rule out client-side caches: `curl -sS "https://<public-host>/?_cb=$(date +%s)"`. If the bundle is still stale with a unique query string, the origin is stale, not the CDN.

Vendor docs to start from — do **not** copy syntax without reading these; the APIs change:

- AWS CloudFront: [CreateInvalidation](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Invalidation.html).
- Fastly: [Purging — API reference](https://www.fastly.com/documentation/reference/api/purging/).
- Akamai: [Fast Purge API](https://techdocs.akamai.com/purge-cache/reference/api).
- Bunny.net: [Purge URL / Pull Zone](https://docs.bunny.net/reference/purgepublic_index).

## Verify cache is flushed

After purging, re-run the diagnosis in reverse — you are checking that every hop now returns the current server bundle.

1. **Re-fetch `index.html` through the edge** and confirm the hashes match `SERVER_HASHES`:

   ```bash
   curl -sS "https://<public-host>/?_cb=$(date +%s)" | grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' | sort -u
   ```

   The `?_cb=<timestamp>` is a cachebust against any layer that keys on query string (common). If the cache keys on path only, the bust has no effect — which is fine, the check still tells you the current answer.

2. **Check cache-status headers** on the next clean request (no cachebust):

   ```bash
   curl -sSI https://<public-host>/
   ```

   Expected, depending on proxy:

   | Proxy | Expected header after purge + one cold fetch |
   |-|-|
   | nginx | `X-Cache-Status: MISS` on first request, `HIT` on subsequent (with the new content). |
   | Cloudflare | `CF-Cache-Status: MISS` → `HIT` on subsequent. `DYNAMIC` is also fine — means not cached at all. |
   | Varnish | `X-Cache: MISS` → `HIT` on subsequent. `X-Varnish` with one ID = fresh miss; two IDs = hit. |
   | CloudFront / other | `X-Cache: Miss from cloudfront` → `Hit from cloudfront`. |
   | Any RFC 9111 cache | `Age: 0` on first request after purge. If `Age` is non-zero immediately after purge, the purge did not land. |

3. **Asset-hash cross-check** — the ground-truth test:

   ```bash
   # Container (ground truth):
   CONTAINER_HASHES=$(docker exec ecm-ecm-1 grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' /app/static/index.html | sort -u)

   # Through the proxy:
   EDGE_HASHES=$(curl -sS "https://<public-host>/?_cb=$(date +%s)" | grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' | sort -u)

   # Must match:
   diff <(echo "$CONTAINER_HASHES") <(echo "$EDGE_HASHES")
   # Expected: no output (identical).
   ```

4. **End-user verification** — have at least one reporting user run the
   [browser-side hash check](./dep-bump-frontend-regression.md#4-browser-cache-verification--is-the-user-on-the-rolled-back-bundle)
   from the companion runbook. Only when their `document.scripts` output
   matches `CONTAINER_HASHES` is the incident closed.

## Escalation

Page the Project Engineer if:

- Purge appears to succeed (HTTP 200 from the purge endpoint, vendor dashboard shows the purge event) but `EDGE_HASHES` still diverges from `CONTAINER_HASHES` after 5 minutes.
- `curl` from inside the container matches the fresh deploy, `curl` through the proxy matches, **but** users still report stale bundles after a hard reload — this usually means a corporate / ISP transparent proxy sits between the user and your edge; operators rarely have control over this layer.
- The reverse proxy or CDN is operated by a third party you cannot purge against (managed hosting, shared-edge provider) — the escalation is to the third party, not to Engineering.

Provide to the engineer: `/tmp/ecm-infra-cache-headers.txt`, the diff output from step 3 above, the list of purge attempts made (vendor, timestamp, response code), and the reporting-user report.

## Post-incident

- [ ] If the operator's infra emitted no `Cache-Control` hints on `/` or `/index.html` and relied on proxy heuristics, file a bead to recommend they adopt the `Cache-Control: no-cache` on `/` + `immutable` on `/assets/*` pattern in the next release notes. (ECM itself does not set these today — see the "What ECM emits today" section.)
- [ ] If this incident exposed a specific proxy the runbook does not cover, add a section here with the vendor's exact purge commands and the matching cache-status header.
- [ ] If multiple releases in a row required an infra-side purge, file a bead under observability to expose a "deploy succeeded but edge still stale" metric (needs instrumented edge — out of scope for ECM itself, but flagging the gap is the SRE's job).
- [ ] Update this runbook with any new cache-status header / vendor pattern seen during the incident.

## References

- [Dep-Bump Frontend Regression runbook](./dep-bump-frontend-regression.md) — companion; browser-side invalidation.
- [Dep-Bump Fresh-Image Smoke Test runbook](./dep-bump-smoke-test.md) — pre-merge dep-bump gate.
- [`frontend/vite.config.ts`](../../frontend/vite.config.ts) — Vite build config; confirms hashed-asset filenames and no service worker.
- Vite [build options](https://vite.dev/config/build-options) — asset-hash strategy upstream reference.
- FastAPI [`StaticFiles`](https://fastapi.tiangolo.com/reference/staticfiles/) — what ECM mounts `/assets` with.
- Starlette [`StaticFiles` implementation](https://www.starlette.io/staticfiles/) — headers emitted by default (`ETag`, `Last-Modified`; no `Cache-Control`).
- RFC 9111 — [HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111.html). `Age` / `Cache-Control` semantics.
- nginx — [`proxy_cache`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache), [`$upstream_cache_status`](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#var_upstream_cache_status).
- Cloudflare — [Purge cache](https://developers.cloudflare.com/cache/how-to/purge-cache/), [Cache Rules](https://developers.cloudflare.com/cache/how-to/cache-rules/), [Cache Status values](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/#cloudflare-cache-responses).
- Varnish — [Purging and invalidation](https://varnish-cache.org/docs/trunk/users-guide/purging.html).
