# Runbook: ECM Frontend Client-Error Rate

> Operators who page on `ECMClientErrorRateElevated` or
> `ECMClientErrorRateCritical` land here. Both alerts watch
> `ecm_client_errors_total` — the counter incremented by
> `POST /api/client-errors` for every frontend runtime error report
> shipped in PR #173 (bd-i6a1m, ADR-006 Phase 1).

**Alerts that route here:**
- `ECMClientErrorRateElevated` (ticket) — `sum(ecm:client_errors:rate5m) > 0.2` for 15m
- `ECMClientErrorRateCritical` (page) — `sum(ecm:client_errors:rate5m) > 2.0` for 5m

**SLO:** [SLO-6 Frontend Error-free Session Rate](../sre/slos.md#slo-6-frontend-error-free-session-rate)

**ADR:** [ADR-006 Frontend Error Telemetry (Phase 1)](../adr/ADR-006-frontend-error-telemetry.md)

---

## What it fires on

The `/api/client-errors` router emits two counters on `/metrics`. The recording rule `ecm:client_errors:rate5m` (defined in [`prometheus_rules.yaml`](../sre/prometheus_rules.yaml) group `ecm_client_error_rate`) sums the per-`{kind, release}` 5-minute rate of `ecm_client_errors_total`. Both alerts watch the unlabeled total of that rule.

**`ecm_client_errors_total{kind, release}`** — counter, incremented per accepted report.

- `kind` — fixed enum, bounded cardinality:
  - `boundary` — caught by the React `ErrorBoundary` during render.
  - `unhandled_rejection` — `window.onunhandledrejection` from the post-mount reporter.
  - `chunk_load` — `vite:preloadError` or a wrapped `import()` that 404'd a chunk.
  - `resource` — pre-React inline bootstrap script in `index.html` caught a `window.error` or `unhandledrejection` before the SPA loaded. Always paired with `release="bootstrap"` for this source.
  - `other` — anything that didn't match the enum (clamped server-side; should be rare).
- `release` — bounded LRU at 3 real values + a `stale` roll-up (ADR-006 §9). The pre-bundle inline script always reports `release="bootstrap"` regardless of the actual build hash — that bucket is the proxy for "couldn't even load the app."

**`ecm_client_errors_dropped_total{reason}`** — counter, incremented when a report was rejected before ingest. Not in either alert predicate, but check it during triage:

- `rate_limited` — token bucket fired (10 events / 60s per bucket key).
- `oversized` — request body exceeded the 8 KB cap.
- `invalid_schema` — Pydantic rejected the payload (extra fields, wrong types, bad `kind`).

A spike in `dropped_total{reason="rate_limited"}` while `client_errors_total` is flat means the actual error rate is higher than the alert sees — the limiter is masking it. Treat as if the alert had fired one severity higher.

---

## Initial triage

**1. Pull the live `kind` × `release` breakdown.**

```bash
docker exec ecm-ecm-1 sh -c 'curl -s http://localhost:8000/metrics' \
  | grep '^ecm_client_errors_total'
```

Read the dominant `kind`. The branch you take next depends on it:

| Dominant `kind` | Next step |
|-|-|
| `boundary` | [Common causes — boundary](#boundary--react-render-bug) |
| `chunk_load` | [Common causes — chunk_load](#chunk_load--bundlechunk-404-from-cache) |
| `resource` (with `release="bootstrap"`) | [Common causes — bootstrap resource](#resource-with-releasebootstrap--app-never-loaded) |
| `resource` (with current `release`) | [Common causes — post-bootstrap resource](#resource-with-current-release--static-asset-404) |
| `unhandled_rejection` | [Common causes — unhandled_rejection](#unhandled_rejection--promise-rejection-regression) |
| `other` | Pull the structured logs (below) — the kind-collapse means the client sent a value the server didn't recognize. |

**2. Confirm the `release` distribution.**

A spike on a `release` label that is **not** the currently-deployed build = stale-bundle incident (users on a cached `index.html` pointing at deleted chunks). A spike on the **current** `release` = regression in the just-deployed bundle.

**3. Pull recent client-error logs for stack/route detail.**

```bash
docker logs ecm-ecm-1 --since 15m \
  | grep '\[CLIENT-ERROR\]' \
  | head -40
```

Each line carries `kind`, `release`, `route`, and a scrubbed `msg`. Group by `route` to see if the failure is concentrated on one tab.

**4. Check the dropped counter.**

```bash
docker exec ecm-ecm-1 sh -c 'curl -s http://localhost:8000/metrics' \
  | grep '^ecm_client_errors_dropped_total'
```

If `rate_limited` is non-trivial, the actual incident is larger than the alert rate suggests — see severity promotion below.

---

## Common causes by kind

### `boundary` — React render bug

The React `ErrorBoundary` caught an exception during render. Almost always introduced by a recent commit to a component on the affected route.

- **Signals:** spike concentrated on the **current** `release` label; `route` label clusters on one tab; stack trace points at a component file.
- **Investigate:**
  ```bash
  git log --oneline -10 origin/dev
  ```
  Look for changes touching the component named in the stack.
- **Mitigation:** rollback the bad image (see [Mitigation patterns](#mitigation-patterns)). File a bug bead with the `[CLIENT-ERROR]` log lines attached.

### `chunk_load` — bundle/chunk 404 from cache

`vite:preloadError` or a `withImportTelemetry`-wrapped lazy import failed to fetch its chunk. Almost always a stale-bundle problem: the user's `index.html` points at a hashed chunk that no longer exists on disk because a fresher deploy replaced it.

- **Signals:** `release` label = **previous** build (not the currently-deployed one); message mentions a path under `/assets/`.
- **Cross-link:** [Infrastructure-Side Cache Invalidation](./infra-cache-invalidation.md) — flush the reverse-proxy / CDN cache so users stop being served the stale `index.html`.
- **If chunk_load is on the *current* release**, the deploy is broken (chunk referenced by `index.html` was never copied). Cross-link [Dep-Bump Frontend Regression](./dep-bump-frontend-regression.md) for the rollback procedure.

### `resource` (with `release="bootstrap"`) — app never loaded

The pre-React inline script in `index.html` caught an error **before** the main Vite module loaded. The browser couldn't even parse the entry chunk. This is the worst case — users see a blank page or the framework-free fallback only.

- **Signals:** `release` = `bootstrap`; spike correlates exactly with a deploy timestamp; `route` is whatever the user happened to be on.
- **Common causes:**
  - The new `index.html` references a chunk that wasn't copied to `/app/static/assets/`.
  - The reverse proxy is serving a stale `index.html` that points at deleted chunks (cross-link [Infrastructure-Side Cache Invalidation](./infra-cache-invalidation.md)).
  - A syntax error in the main chunk (build artifact corrupt — re-run `npm run build` and re-deploy).
- **Mitigation:** rollback to the previous image tag, then verify `/app/static/assets/` contents match `index.html` references.

### `resource` (with current `release`) — static asset 404

Post-bootstrap, after React mounted, a non-chunk resource (image, font, sourcemap, etc.) 404'd. Less severe than the bootstrap variant but still indicates a deploy mismatch.

- **Signals:** `release` = current; `kind` = `resource`; message names a `/assets/*` or `/static/*` path.
- **Mitigation:** confirm the missing file exists in `/app/static/`; if not, re-deploy the frontend per the procedure in [Frontend Agent Instructions](../../frontend/CLAUDE.md) (`rm -rf /app/static/assets/*` before `docker cp` — the docker copy is additive, not replacing).

### `unhandled_rejection` — promise rejection regression

`window.onunhandledrejection` fired. Usually an API client contract change: a fetch returns a shape the caller doesn't expect, the promise rejects, and nothing catches it.

- **Signals:** spike correlates with a backend deploy that changed an `/api/*` response shape; stack trace points at code in `services/api.ts` or a hook.
- **Investigate:** correlate the alert window with backend deploys, not just frontend ones. A backend ASGI bump can produce frontend `unhandled_rejection` spikes if a response body shape changed.
- **Mitigation:** rollback the offending service (frontend if the regression is in client code; backend if the API contract drifted — see [Dep-Bump Backend ASGI Regression](./dep-bump-backend-asgi-regression.md)).

---

## Mitigation patterns

Order of preference: rollback > cache flush > suppress. Suppress is **not a fix** — it silences the alert while users still hit the bug.

### Rollback to previous image tag

For the post-merge regression case, the relevant runbook depends on which side regressed:

- **Frontend regression** (boundary, chunk_load on current release, bootstrap resource): see [Dep-Bump Frontend Regression](./dep-bump-frontend-regression.md) — rollback procedure plus user-cache verification.
- **Backend regression** (unhandled_rejection from API contract drift): see [Dep-Bump Backend ASGI Regression](./dep-bump-backend-asgi-regression.md).
- **Tagged-release retraction** (rare): see [v0.16.0 Rollback](./v0.16.0-rollback.md).

### Force-clear infrastructure cache

If the rollback shipped a fresh `index.html` but users still report the broken bundle, a reverse proxy / CDN is serving the stale copy. Walk through [Infrastructure-Side Cache Invalidation](./infra-cache-invalidation.md) — it covers nginx, Cloudflare, Varnish, and generic CDNs. Browser-side cache flushes do not fix this.

### Temporary suppression — NOT a fix

If the alert is storming and you need quiet to investigate, flip the
`telemetry_client_errors_enabled` setting off. The backend short-circuits
to 204 without incrementing counters, the alert recovers, and the actual
bug is still there for users.

```bash
# Read current settings.
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/settings \
  | jq '.telemetry_client_errors_enabled'

# Flip off.
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8000/api/settings \
  -d '{"telemetry_client_errors_enabled": false}'
```

The reporter honors the runtime override on the next `/api/settings` resolve (no page reload required for new tabs; existing tabs flip on next route change). **Re-enable as soon as the underlying issue is resolved** — running with telemetry off blinds SLO-6.

This is a stop-gap. It does not fix what users are experiencing; it stops the page from waking on-call while you work the real fix. File a bead naming the suppression window and the re-enable verification step.

---

## Severity promotion

The two alerts climb a deliberate ladder:

| State | Predicate | Severity | Expected on-call action |
|-|-|-|-|
| Quiet | `< 0.2/s` | none | normal |
| `ECMClientErrorRateElevated` | `> 0.2/s` for 15m | **ticket** | open bead, run triage above during business hours |
| `ECMClientErrorRateCritical` | `> 2/s` for 5m | **page** | wake on-call, treat as incident, identify offending `release`, prepare rollback |

Promote ticket → page yourself if any of:

- `kind="boundary"` or `kind="chunk_load"` is climbing across multiple sequential `/metrics` scrapes (regression spreading, not bouncing).
- `release="bootstrap"` `resource` count is non-zero (some users cannot load the app at all — even one is severe on a small instance).
- `ecm_client_errors_dropped_total{reason="rate_limited"}` is climbing alongside the elevated rate (limiter is masking the real magnitude — actual rate is higher than the alert sees).
- The error correlates with a deploy in the last 30 minutes and no rollback has been initiated.

Demote page → ticket only after `ecm:client_errors:rate5m` has been below the elevated threshold for at least 15 minutes **and** the offending release is no longer the active deploy.

---

## Postmortem hook

ECM does not yet ship a runbook-style postmortem template — the postmortem workflow is the `/postmortem` skill at `~/.claude/skills/postmortem/SKILL.md` (blameless, timeline-driven, pulls relevant personas for contributing-factor analysis).

Trigger a postmortem when:

- `ECMClientErrorRateCritical` paged (any duration).
- `ECMClientErrorRateElevated` ran for > 1 hour before mitigation.
- Suppression via `telemetry_client_errors_enabled=false` was used at any point during the incident — capture why, the suppression window, and the re-enable timestamp.

The postmortem must capture: deploy SHA before/after, dominant `kind` × `release` breakdown at peak, mitigation chosen (rollback / cache flush / suppress), time-to-detect, time-to-mitigate, and a regression test or alert tuning that would have caught it earlier.

---

## See also

- [SLO-6 — Frontend Error-free Session Rate](../sre/slos.md#slo-6-frontend-error-free-session-rate)
- [ADR-006 — Frontend Error Telemetry](../adr/ADR-006-frontend-error-telemetry.md)
- [User-facing opt-out guide](../user_guide/error-telemetry-opt-out.md) — what users see and how operators turn telemetry off persistently.
- [Prometheus alert rules](../sre/prometheus_rules.yaml) — group `ecm_client_error_rate`.
- Backend metric definitions: `backend/observability.py` (search `ecm_client_errors_total`).
- Router source: `backend/routers/client_errors.py`.
- Frontend reporter: `frontend/src/services/clientErrorReporter.ts`.
- Pre-bundle inline script: `frontend/index.html`.
