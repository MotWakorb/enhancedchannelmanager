# Error Telemetry & Opt-out

> **Status:** Complete — ADR-006 Phase 1 behavior (bead `i6a1m`).

Enhanced Channel Manager ships with **local-sink** frontend error telemetry. When the UI crashes in your browser, ECM captures a structured record of *what broke, where, and roughly what browser* and writes it to the container's own `/metrics` endpoint and structured log stream. **Nothing is sent outside your container.** No third party, no Sentry, no Datadog, no phone-home.

This page explains exactly what is and isn't captured, where it goes, and how to turn it off.

## What gets collected

Each time the ECM UI catches a runtime error, the reporter sends one small record to `POST /api/client-errors` inside your own container:

| Field | Example | Why |
|-|-|-|
| `kind` | `boundary`, `unhandled_rejection`, `chunk_load`, `resource`, `other` | Categorizes the failure. Fixed 5-value enum (bounded metric cardinality). |
| `message` | `TypeError: Cannot read property 'id' of undefined` | Truncated to 512 characters. |
| `stack` | Minified stack with basenames only, e.g. `at onClick (bundle.js:42:17)` | Absolute filesystem paths are stripped client-side AND server-side. Truncated to 4096 characters. |
| `release` | `0.16.0-0058` | Which ECM build the browser was running. Helps identify stale-bundle issues after deploys. |
| `route` | `/channels` | The URL pathname at the time of the crash. Query strings (`?search=…`) and URL fragments (`#…`) are stripped. |
| `user_agent_hash` | SHA-256 of the full User-Agent string | Lets us correlate repeat errors from one browser without storing the UA string itself. |
| `ts` | `2026-04-24T14:22:03Z` | ISO-8601 timestamp. |

## What is NEVER collected

The reporter uses an **allowlist**, not a blocklist. Everything below is **never** read, never sent, never logged:

- **Query strings** (`?search=my-search-term`)
- **URL fragments** (`#section`)
- **Referrer URLs**
- **Cookies** (session cookies, auth cookies, any cookie)
- **`localStorage` / `sessionStorage`** contents
- **User-typed input** — form values, search boxes, comment drafts, anything you typed
- **DOM text around the crash site** (e.g., the visible content of the page)
- **Full User-Agent string** — only a SHA-256 hash of it
- **Your IP address** — the reporter doesn't forward it; the backend sees it only because every HTTP request has one, and it's never written to the telemetry log line
- **Dispatcharr URLs / credentials** — the scrubber strips IPs, hostnames, and Xtream Codes credential paths from stack traces and messages before they're logged

## Where the data goes

Everywhere it lands is **inside your own ECM container**:

1. **Prometheus `/metrics`** — three metric series:
   - `ecm_client_errors_total{kind, release}` — a counter, one increment per reported error
   - `ecm_client_errors_dropped_total{reason}` — a counter for reports the backend rejected (rate-limit, oversize, bad schema)
   - `ecm_client_error_reports_bytes` — a histogram of request sizes
2. **Structured log stream** — one `[CLIENT-ERROR]` line per reported error on the `ecm.client_error` logger. Lives in the container's stdout, readable with `docker logs ecm-ecm-1`.
3. **Nowhere else.** No external HTTP request is made. No file outside `/metrics` and the container log is written.

If you scrape `/metrics` with your own Prometheus and ship your container logs to your own log aggregator, those are the only two places the telemetry appears. If you don't, the data stays in-memory and in the container's stdout buffer and is lost on restart.

## How to turn it off

ECM ships with telemetry **enabled by default** because the data never leaves your container — there is no privacy surface that would justify default-off. If you still want to disable it (e.g., your security posture forbids any runtime introspection, even local), it is a one-click flip.

### Via the Settings UI

1. Open **Settings** → **Advanced** (or the section labeled **Telemetry**, depending on your UI version).
2. Toggle **"Send frontend error telemetry to local /metrics"** to **Off**.
3. Click **Save**.

The change takes effect on the next frontend error — no reload, no restart. Both the frontend reporter AND the backend endpoint honor the flag (belt-and-suspenders, so a stale browser tab cannot override your choice).

### Via the API

If you manage settings via the API directly, set `telemetry_client_errors_enabled` to `false` on `POST /api/settings`:

```bash
curl -X POST http://your-ecm-host:6100/api/settings \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $YOUR_JWT" \
  -d '{ ..., "telemetry_client_errors_enabled": false }'
```

`GET /api/settings` returns the current state on the `telemetry_client_errors_enabled` field.

### Via the config file (cold)

If you prefer editing `settings.json` directly:

```json
{
  "...": "other settings",
  "telemetry_client_errors_enabled": false
}
```

Changes take effect on the next settings reload (usually immediate; restart the container if you're unsure).

## What happens when it's off

- The frontend reporter **short-circuits** before building the payload. No network call is issued.
- The backend `/api/client-errors` endpoint **returns 204** immediately without reading the body, validating it, incrementing any counter, or writing any log line.
- The three `ecm_client_errors_*` metrics stay flat.
- Your app still works the same way it did before — crashes are still handled by the React `ErrorBoundary` and the user-visible fallback UI, you just don't get a counter increment.

## What can I learn from the data (if I leave it on)?

The point of the telemetry is that the maintainer, or you as the operator, can answer questions like:

- **"Did the last deploy introduce a crash?"** — `rate(ecm_client_errors_total{release="current-build"}[5m])` spiking after a deploy is a strong signal.
- **"Are my users on a stale bundle?"** — `sum by (release) (ecm_client_errors_total)` with one label spiking on `release != current` means browsers are running an old cached bundle.
- **"How many of my users hit errors this month?"** — SLO-6 (see `docs/sre/slos.md`) gives an error-free session rate.

Without this signal, the only way to find out ECM crashed was to notice the UI was broken and file a bug. With it, you can notice from `/metrics` before the report reaches you.

## Related

- **[ADR-006](../adr/ADR-006-frontend-error-telemetry.md)** — full architecture record for this feature, including why we chose local-sink over Sentry/GlitchTip/OTel SDK.
- **[SLO-6 in `docs/sre/slos.md`](../sre/slos.md)** — the error-free-session-rate SLO defined on top of this data.
- **[`docs/sre/prometheus_rules.yaml`](../sre/prometheus_rules.yaml)** — alert rule `ECMClientErrorRateElevated` / `ECMClientErrorRateCritical` for operators running Prometheus + Alertmanager.
