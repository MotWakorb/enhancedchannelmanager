# Runbook: Dep-Bump Frontend Regression (React 19 / Vite 8 / TS 6 / @dnd-kit)

> Post-merge frontend regression caused by PR5 of the v0.16.0 dep-bump epic —
> React 19 ecosystem (React + react-dom + @types/react + @testing-library/react)
> and, in adjacent PRs, Vite 8 + plugin-react 6, TypeScript 6, and
> `@dnd-kit/sortable` 10. This runbook walks an operator through detecting,
> isolating, rolling back, and — crucially — verifying that users are actually
> hitting the rolled-back bundle rather than a cached broken one.

- **Severity**: P1 — the single-page app is either blank, broken mid-session, or silently miscomputing; users cannot work around it.
- **Owner**: SRE (runbook owner); Project Engineer (post-rollback root cause).
- **Last reviewed**: 2026-04-24
- **Related beads**: `enhancedchannelmanager-pm1t4` (this runbook), `enhancedchannelmanager-6rrl5` (dep-bump epic), `enhancedchannelmanager-jpyz4` (PO decision: epic lands on `dev` before v0.16.0 cut), `enhancedchannelmanager-in620` (React 19 PR5), `enhancedchannelmanager-hlcgj` (Vite 8 PR6), `enhancedchannelmanager-v28b8` (TS 6 PR4), `enhancedchannelmanager-zqmv1` (@dnd-kit PR3).
- **Related ADR**: [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md).
- **Complementary runbook**: [Dep-Bump Fresh-Image Smoke Test](./dep-bump-smoke-test.md) — pre-merge gate; this runbook covers the post-merge case the gate missed.

## Alert / Trigger

**There is no automated alert.** ECM ships no RUM (real-user monitoring), no
frontend error telemetry, and no client-side Sentry. Regressions are detected
via one of:

- **User report on the GitHub Issues tracker** — the primary channel. Symptoms usually described as "whole app is blank", "can't drag channels anymore", "console is full of red", or "page loads then freezes".
- **Operator self-observation** — after deploying a new frontend bundle, the operator opens the UI, sees breakage, and starts here.
- **Release-notes feedback in Discord** — per `docs/discord_release_notes.md`, v0.16.0 notes are posted; users react quickly if the new build is broken.

Run this runbook when any of the above correlates with a deploy that bumped
any of: `react`, `react-dom`, `@types/react`, `vite`, `@vitejs/plugin-react`,
`typescript`, `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`.

## Symptoms

What the responder sees. Symptoms cluster by which bump is likely responsible:

| Symptom | User experience | Likely culprit |
|-|-|-|
| Blank white page on load; root `<div id="root">` remains empty | "The app is totally blank" | React 19 — hydration or concurrent-rendering contract break. Open DevTools → Console. |
| Console shows `Hydration failed because…` or `Minified React error #418/#425` | Page paints briefly then blanks, or shows a mid-render fallback | React 19 hydration strictness. |
| Console shows `Failed to load module script` / `Loading chunk N failed` | Page partially loads, then freezes on navigation | Vite 8 — chunking/manifest change; user has a stale `index.html` pointing at deleted chunk names. |
| Drag-to-reorder channel groups doesn't respond; console shows a `@dnd-kit` error | "Drag is broken" | `@dnd-kit/sortable` 10 — sensor or collision-detection API change. |
| TypeScript-surfaced runtime error (narrowed-prop ran as `undefined`, date formatter NaN) | Subtle — feature "works" but shows wrong data | TS 6 — stricter type narrowing that masked a latent bug in the app, or a stricter `lib.dom.d.ts` change that a component relied on. |
| `/src/main.tsx` 404s (or some other asset 404s) | Whole app blank, Network tab shows 404 on `/assets/index-<hash>.js` | Stale `index.html` cache — server has rolled back, client has not. **See Browser-Cache section.** |

Additional diagnostic surfaces:

- **DevTools Console** (F12 → Console). JSON log lines from the backend are **not** visible here; only frontend errors.
- **DevTools Network tab** (F12 → Network, Ctrl+R). Look for red entries on `*.js`, `*.css`, and `index.html`.
- **Server-side bundle hash check** — read `dist/index.html` after a rebuild and compare to the `index.html` the browser is loading.

## Diagnosis

### 1. Confirm the deployed bundle is the suspect

```bash
# Which bundle does the server think it is serving?
docker exec ecm-ecm-1 grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' /app/static/index.html | sort -u
# This lists the hashed asset filenames Vite emitted for the currently-deployed build.
```

Capture that list — you will compare it to what the **browser** is actually
requesting (step 4). If they differ, the user is on a cached `index.html`.

Also confirm the version:

```bash
docker exec ecm-ecm-1 env | grep -E 'ECM_VERSION|GIT_COMMIT'
```

If this does not match the frontend-bump merge commit, this runbook is the
wrong one — look elsewhere.

### 2. Isolate which bump is responsible (decision tree)

Match the symptom to the culprit, starting at the top:

1. **Blank page + "Hydration failed" / "Minified React error"** → **React 19**. React 19 stricter hydration fails hard where React 18 would warn. Confirm: temporarily undo the React bump locally (see Mode B rollback) and reproduce; if symptom clears, React 19 owns it.
2. **"Failed to load module script" / 404 on a hashed `/assets/...js`** → **Vite 8** (or it is the browser-cache case — see step 4 first; do not assume Vite without eliminating cache). Vite 8 changes chunking. A client loading stale `index.html` against a new `/assets` directory will 404. If step 4 proves the browser is on the freshest `index.html` and the 404 still reproduces, Vite is the culprit.
3. **Drag-reorder broken, `@dnd-kit` error in console** → **`@dnd-kit/sortable` 10**. The sensors / `useSortable` hook surface changed. Non-drag flows will still work.
4. **TS-narrowed prop comes through as wrong type at runtime** (feature subtly broken, not blank) → **TypeScript 6**. TS 6 changes narrowing in edge cases, and the `tsc --noEmit` check would not catch a runtime divergence from a lib.dom type. Low-signal — usually a user report, not a DevTools indicator.
5. **Multiple symptoms at once** → most likely Vite 8 + React 19 interacting (plugin-react 6's change to fast-refresh / transform). Treat the whole frontend bump as a unit and roll back the multi-package PR; do not try to bisect live.

### 3. Capture evidence before rollback

```bash
# Server-side copy of index.html + the assets manifest reference.
docker exec ecm-ecm-1 cat /app/static/index.html > /tmp/ecm-frontend-regression-index.html

# First 500 lines of container logs — backend sees the 404s on /assets/* if
# the client asks for a stale hash, which corroborates the browser-cache case.
docker logs ecm-ecm-1 --tail 500 2>&1 | grep -E '"path":"/(assets|index)' > /tmp/ecm-frontend-regression-backend.log
```

Ask the reporting user (GitHub Issue) for a screenshot of DevTools → Console
(full error text, including the React error number) and Network (at least
the `index.html` and the first two failing asset rows). These are your
symptom-side evidence.

### 4. Browser-cache verification — is the user on the rolled-back bundle?

**This is the single most common source of "rollback didn't fix it" reports.**
Stale `index.html` in a browser cache or between the user and the server
(a proxy, an ngrok tunnel, Cloudflare, a reverse proxy) can make a successful
server-side rollback look like a failure.

Ask the user to run, in DevTools → Console:

```javascript
// Which bundle is this browser actually running?
Array.from(document.scripts).map(s => s.src).filter(Boolean)
// Expected: one entry like
//   http://<host>:<port>/assets/index-<hash>.js
// The <hash> MUST match one of the filenames you captured in step 1.
```

- **Hash matches server's `/app/static/index.html`** → the user is on the current bundle; the symptom is real and not a cache artifact.
- **Hash does not match** → the user is on a stale cached `index.html`. Proceed to "Forced cache invalidation" below, **then** re-check the symptom before rolling back further.

**ECM ships no service worker** (confirmed in `frontend/vite.config.ts` — no
`vite-plugin-pwa`, no `workbox-*`, no `registerServiceWorker` in `src/`). If
a user reports `navigator.serviceWorker.controller` is non-null, that came
from a prior ECM version that did ship one, or a reverse-proxy in front of
ECM is installing one — flag it as anomalous and escalate rather than
assuming it belongs to ECM.

#### Forced cache invalidation (operator-side)

Ask the user to run **one** of the following, in order of escalation:

```text
1. Hard reload (fastest): Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac).
2. DevTools → Network tab → tick "Disable cache" → reload while DevTools is open.
3. DevTools → Application tab → Storage → "Clear site data" → reload.
4. Service-worker flush (only if anomalous SW detected above):
     DevTools → Application → Service Workers → Unregister → reload.
```

After each step, the user re-runs the `document.scripts` check from step 4.
The operation is successful when the `<hash>` matches the server's expected
`/assets/index-<hash>.js`. Only once that matches is a symptom that remains
on the client side a real regression (not a cache artifact).

> **If the browser-side invalidation doesn't resolve the symptom**, suspect
> an infrastructure-side cache in front of ECM (reverse proxy, CDN, corporate
> proxy) that is still serving a stale `index.html` or `/assets/*` chunk. See
> [Infra-Side Cache Invalidation](./infra-cache-invalidation.md) for
> nginx / Cloudflare / Varnish / generic-CDN flush procedures.

### Escalate instead of continuing if

- You cannot identify which bump is responsible and the symptom does not match
  any row in the decision tree above.
- The server-side `index.html` already refers to a pre-bump bundle (meaning
  the rollback has already been done) but multiple users still report breakage
  after completing the cache-invalidation steps — this implies an
  infrastructure-side cache (reverse proxy, CDN, corporate proxy) that needs
  its own flush. Follow [Infra-Side Cache Invalidation](./infra-cache-invalidation.md).
- Rolling the frontend back leaves users on broken chunks because the
  frontend-bump commit also changed `index.html`'s asset ref format in a way
  that the previous server version cannot serve — page the Project Engineer
  before attempting a second rollback.

## Resolution

Like the backend ASGI runbook, the rollback has two modes — GHCR-based (Mode A)
and local rebuild (Mode B). Unlike the backend, **an additional step is
required after the server-side rollback**: confirm every reporting user is
actually on the rolled-back bundle. A successful server-side rollback that
leaves users on a cached broken bundle is indistinguishable from "the
rollback didn't work" until that step is done.

### Mode A — GHCR rollback to the previous `dev-<sha>` image

Same mechanics as the backend runbook — the frontend is built into the image
in the `frontend-builder` stage (see `Dockerfile:1-12`), so pulling a prior
image pulls a prior frontend bundle.

1. **Identify the pre-bump commit.**

   ```bash
   git log --oneline --merges origin/dev -n 20
   # Locate the frontend-bump merge commit (subject mentions React/Vite/TS/dnd-kit).
   PREV_SHA=<first 7 chars of pre-bump commit>
   ```

2. **Confirm the prior image exists.**

   ```bash
   docker manifest inspect ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}
   # Expected: valid manifest. "manifest unknown" → step back one commit.
   ```

3. **Pull + swap the container.**

   ```bash
   docker pull ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}

   docker logs ecm-ecm-1 --tail 500 2>&1 > /tmp/ecm-frontend-regression.log
   docker stop ecm-ecm-1
   docker rm ecm-ecm-1

   docker run -d \
     --name ecm-ecm-1 \
     -p ${ECM_PORT:-6100}:${ECM_PORT:-6100} \
     -p ${ECM_HTTPS_PORT:-6143}:${ECM_HTTPS_PORT:-6143} \
     -v ecm-config:/config \
     --add-host=host.docker.internal:host-gateway \
     ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}
   ```

4. **Confirm the served bundle changed.**

   ```bash
   docker exec ecm-ecm-1 grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' /app/static/index.html | sort -u
   ```

   This list **must differ** from what you captured in Diagnosis step 1. If
   it is the same list, the image you pulled is not actually the pre-bump
   image — step back one more commit and retry.

### Mode B — Local rebuild from the pre-bump commit

Only if GHCR is unavailable or you are in an air-gapped deploy. This rebuilds
the frontend locally and redeploys it into the running container **without a
backend restart** — the CLAUDE.md-documented path.

1. **Check out the pre-bump commit.**

   ```bash
   git fetch origin
   git checkout ${PREV_SHA}
   ```

2. **Rebuild the frontend bundle.** Per `CLAUDE.md`:

   ```bash
   cd frontend
   npm install   # the lockfile + package.json are from the pre-bump commit
   npm run build
   # Emits dist/ with a fresh index.html + hashed /assets/*.
   ```

3. **Swap the bundle in the running container.** Per `CLAUDE.md` — **the clean step is mandatory**: `docker cp` only adds, never removes, so stale hashed chunks from the bad build would remain alongside the new ones.

   ```bash
   # MANDATORY — without this, stale chunks from the bad build persist in /app/static/assets
   # and a client requesting an old hash will still resolve it, masking the rollback.
   docker exec ecm-ecm-1 sh -c 'rm -rf /app/static/assets/*'

   # Copy the rebuilt bundle in. No backend restart required — static assets are
   # served directly from disk by FastAPI's StaticFiles mount.
   docker cp dist/. ecm-ecm-1:/app/static/
   ```

4. **Confirm the container is serving the new `index.html`.**

   ```bash
   docker exec ecm-ecm-1 grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' /app/static/index.html | sort -u
   curl -sS http://localhost:${ECM_PORT:-6100}/ | grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' | sort -u
   # Both outputs must match each other and must differ from the bad-bundle hashes.
   ```

### Verify user-side (both modes)

**Required step — do not declare the rollback complete before this passes for
every reporting user.**

For each reporting user, ask them to:

1. Hard reload (`Ctrl+Shift+R` / `Cmd+Shift+R`).
2. Run the DevTools → Console check from Diagnosis step 4:

   ```javascript
   Array.from(document.scripts).map(s => s.src).filter(Boolean)
   ```

3. Confirm the hash in the output matches the server's current expected
   hash (the `/assets/index-<hash>.js` you captured post-rollback).

If a user's browser still loads a stale hash after a hard reload, escalate
to the cache-invalidation ladder (disable cache → clear site data → SW
unregister). If they still don't match after all four steps, an
infrastructure-side cache is in play — escalate to the Project Engineer with
the proxy/CDN topology of the deploy.

Server-side verification checklist:

```bash
# Container is up, not restarting.
docker ps --filter name=ecm-ecm-1 --format 'table {{.Names}}\t{{.Status}}'

# Running image matches the rollback target (Mode A).
docker inspect ecm-ecm-1 --format '{{.Config.Image}}'

# Hashed assets reference the rolled-back bundle.
docker exec ecm-ecm-1 grep -oE '/assets/[a-zA-Z0-9_-]+\.(js|css)' /app/static/index.html | sort -u

# Backend liveness + readiness still green (rollback must not regress the backend).
curl -fsS http://localhost:${ECM_PORT:-6100}/api/health
curl -sS http://localhost:${ECM_PORT:-6100}/api/health/ready | jq '.status'
```

## Escalation

Stop and page the Project Engineer if:

- Mode A rollback completes, the reporting user confirms the served hash
  matches, and the symptom still reproduces — the failure is not in the
  bumped package, or the pre-bump image also carries the regression.
- Mode B rebuild fails locally (`npm install` errors, `npm run build` errors)
  on a clean checkout of the pre-bump commit — environment issue that the
  runbook cannot resolve.
- Cache invalidation ladder completes for one user but symptom persists
  across multiple users even post-rollback — infrastructure-side cache. Follow
  [Infra-Side Cache Invalidation](./infra-cache-invalidation.md) before paging.
- The regression appears on a tagged release (`v0.16.0`), not just `dev` —
  scope expands to [v0.16.0 Hard Rollback](./v0.16.0-rollback.md).

Provide to the engineer: the GitHub Issue URL(s), the DevTools screenshots,
the decision-tree outcome (which bump is suspected), `/tmp/ecm-frontend-regression-index.html`,
`/tmp/ecm-frontend-regression-backend.log`, and the rollback target SHA.

## Post-incident

- [ ] File a bead under `enhancedchannelmanager-6rrl5` for the root-cause fix. Label: `dep-bump`, `roadmap:v0.16.0`, `frontend`. Reference the decision-tree outcome and evidence.
- [ ] **Pin `frontend/package.json` + `frontend/package-lock.json` back to the pre-bump versions** in a follow-up PR to `dev` so a fresh `docker build` on `dev` rebuilds the safe bundle. Use the exact versions from `PREV_SHA`'s `package-lock.json`.
- [ ] Post a GitHub Issue comment on every user report that fed into this incident, letting them know the fix is live and — crucially — the hard-reload step so they stop hitting the cached broken bundle. Close the issues once reporters confirm.
- [ ] Re-cut process: once root-caused, re-propose the frontend bump. Follow ADR-001's PR-grouping rule (one major per PR), run the [dep-bump fresh-image smoke test](./dep-bump-smoke-test.md) locally, and run through the app manually (drag-reorder, channel list, EPG, settings) before merging. PR6 (Vite 8) was sequenced after PR5 (React 19 ecosystem) deliberately — if PR5 is the regression, PR6 should not move until PR5 is resolved.
- [ ] **Observability gap flag (SRE):** the absence of any frontend telemetry (no RUM, no error reporting) means a frontend regression is user-report-only — lag from break to detection is bounded by the speed of user reports. Keep this gap visible as a candidate SLO follow-up ("frontend error-free session rate"), to be considered by the PO alongside the v0.17.x backlog.
- [ ] Schedule a blameless postmortem via `/postmortem` if the incident exceeded the SLO error budget for user-perceived reliability (see [error-budget policy](../sre/slos.md#error-budget-policy)).
- [ ] Update this runbook with anything the real incident taught — especially any decision-tree symptom mapping that was missing.

## References

- [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md) — defines the pre-merge gate; frontend bumps must pass the fresh-image smoke ([dep-bump-smoke-test](./dep-bump-smoke-test.md)) before merging.
- `frontend/package.json` — current frontend dependency pins (React 18.3.1, Vite 7.3.2, TypeScript 5.9.3, `@dnd-kit/sortable` 8.0.0 as of this runbook's last-reviewed date).
- `frontend/vite.config.ts` — confirms no service worker is configured; the `build.outDir` is `dist`, `build.emptyOutDir` is true (locally).
- `Dockerfile` — multi-stage build; frontend bundle is baked into `/app/static` in the production image (lines 1–12, 63).
- `CLAUDE.md` — frontend deploy path: `cd frontend && npm run build`, clean `/app/static/assets/*`, `docker cp dist/. ecm-ecm-1:/app/static/`. The clean step is mandatory.
- `.github/workflows/build.yml` — GHCR tag scheme (`dev`, `dev-<short-sha>`, semver tags on `main`).
- [Backend ASGI Regression runbook](./dep-bump-backend-asgi-regression.md) — sibling runbook for the backend triplet.
- [Infra-Side Cache Invalidation runbook](./infra-cache-invalidation.md) — companion runbook for flushing reverse-proxy / CDN caches when the browser-side cache invalidation ladder above is not sufficient.
- Bead `enhancedchannelmanager-jpyz4` — PO decision: the full dep-bump epic lands on `dev` before v0.16.0 is cut.
- `docs/discord_release_notes.md` — release announcement channel; user reports of a broken bundle often surface here first.
