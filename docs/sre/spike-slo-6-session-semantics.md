# Spike: Session Semantics for SLO-6 Denominator (`ecm_session_starts_total`)

**Bead:** [enhancedchannelmanager-1tl01](../../.beads/) (spike)
**Implementation bead (blocked on this):** [enhancedchannelmanager-arp3o](../../.beads/)
**Status:** Decision recorded. Implementation may proceed.
**Date:** 2026-04-24
**Authors:** Claude (orchestrator), with persona consultations from SRE, Security Engineer, UX Designer.
**Related:** [`docs/sre/slos.md`](./slos.md) §SLO-6, [`docs/adr/ADR-006-frontend-error-telemetry.md`](../adr/ADR-006-frontend-error-telemetry.md), [`frontend/src/services/clientErrorReporter.ts`](../../frontend/src/services/clientErrorReporter.ts).

## 1. Why this spike exists

SLO-6 (Frontend Error-free Session Rate) was scaffolded in bd-i6a1m at 99.0% error-free sessions over a rolling 28-day window. The numerator (`ecm_client_errors_total`) ships in production. The denominator does not — `ecm_session_starts_total` is the missing counter, and the `slos.md` PromQL expression for SLO-6 currently degrades to a log-aggregation approximation.

Before implementing the counter under bead arp3o, the team needs to commit to a **session semantic**. Once the counter ships and 28 days of SLI history accrue, changing the semantic invalidates the history — there is no clean migration path. This is a one-way door for the SLO; that's why it warrants a spike rather than a free choice during implementation.

## 2. Three candidates

All three were proposed during the i6a1m extension review.

### A. One per page load (SPA boot)

The frontend emits `POST /api/session-start` from the initial mount of `App.tsx`. The backend bumps `ecm_session_starts_total` with no deduplication.

- **Pros:** Simplest to implement (~10 lines frontend, ~5 lines backend). No auth dependency — captures pre-login bootstrap errors. No new client-side identifier introduced.
- **Cons:** Multi-tab inflation — three tabs of the same user count as three "sessions." Every hard-refresh resets and re-bumps. The denominator becomes systematically inflated relative to "unique user-sessions," driving the computed error-free-session rate **up** (denominator-inflated SLI looks healthier than reality).

### B. One per JWT issuance (auth-tied)

The counter bumps inside the JWT-issue handler in the auth router. Each successful login produces exactly one `ecm_session_starts_total` increment.

- **Pros:** Aligned with existing auth flow. Single, well-defined emission site. No frontend changes. Bound to a real user event.
- **Cons:** Excludes anonymous/pre-login bootstrap errors from the SLI denominator — the `kind: 'boundary'` and `kind: 'resource'` counters in clientErrorReporter.ts that fire before login (or that fire because of a chunk-load failure on the login page itself) have no matching denominator event. Long-lived JWTs mean one "session" can span days of activity, undercounting genuine session boundaries when an operator returns to a long-open tab.

### C. One per cookie/sessionStorage lifetime (true unique session)

The frontend generates a `session_id` (SubtleCrypto-backed UUID) on first mount, persists it in `sessionStorage` (or a same-origin cookie), and emits `POST /api/session-start` once with that `session_id`. The backend deduplicates via short-TTL in-memory set before incrementing.

- **Pros:** Closest to a "true unique user-session." Survives same-tab navigation and hard-refresh. Resets on tab close. No auth dependency. Counter granularity matches the natural mental model behind SLO-6.
- **Cons:** Requires a new frontend bookkeeping surface (`session_id` generation + `sessionStorage` write). Backend must hold a short-TTL deduplication set (memory cost is bounded but non-zero). Slight new privacy surface — a per-session identifier in `sessionStorage` is visible in DevTools and may interact with strict-privacy browser settings (sessionStorage disabled, ephemeral profiles, etc.).

## 3. Persona recommendations

### 3.1 SRE — recommends **C**

> *Brief: SLI accuracy, alert noise, operational impact.*

The denominator is the SLI. If it doesn't match the mental model behind the SLO, every downstream alert and every error-budget conversation starts with an asterisk. Candidate A inflates the denominator in a way that systematically hides real degradation — three tabs of one user during a chunk-load incident divides the error count by 3 instead of by 1. That's not noise; it's a structural underestimate of impact baked into the SLI forever. Candidate B excludes the most operationally-interesting failure modes from SLO-6 entirely: stale-bundle chunk-load errors and pre-mount resource failures both fire before authentication, so they go into the numerator with no matching denominator event — that's a divide-by-mismatched-baseline, worse than not measuring.

C is the only candidate where the denominator unit (a unique browser session) matches the unit referenced in the SLO statement ("Frontend Error-free Session **Rate**"). Operational impact is small: a `Set<sessionId>` with TTL=24h costs O(daily-sessions) memory, which on a LAN instance is trivially bounded by the SLO-6 §11 evaluation gate (≥50 sessions/day → ~1,400 entries over 28d). Cardinality of the metric is unchanged (counter has no labels). Recommend C, with the dedup TTL set to 24h and a metric (`ecm_session_dedup_set_size`) exposing the in-memory set size so the SRE can spot leaks if the TTL pruner ever regresses.

### 3.2 Security Engineer — recommends **C with constraints**, B as conservative alternative

> *Brief: privacy/security surface — LGPD/GDPR concerns, threat-model implications.*

A is privacy-neutral (no new identifier introduced) but has no security upside that outweighs its measurement defects. B reuses the existing JWT — privacy-equivalent to the status quo, since the bump happens server-side at token issuance and no new identifier crosses the wire. C introduces a new client-side identifier, which is the only candidate that materially changes the privacy surface.

For C to be acceptable, the `session_id` must be:

1. **A SubtleCrypto-generated v4 UUID, not a hash of UA / IP / user identifiers** — this prevents the identifier from being a derivative of PII. (Same posture as `user_agent_hash` in ADR-006: the hash is opaque-by-construction, and the same property must hold for `session_id`.)
2. **Stored in `sessionStorage`, not `localStorage` and not an httpOnly cookie persisted across browser restarts** — sessionStorage is wiped on tab close, which scopes the identifier's lifetime to the SLO's intent. localStorage would create a long-lived per-browser identifier (regression from current posture); a server-set persistent cookie would create cross-session linkage we don't want.
3. **Never logged by the backend, never returned in API responses, never indexed in the database** — the dedup set is in-memory only, with the same lifecycle as Prometheus counter state. ADR-006 §4 already enumerates the field allowlist for client-error payloads; `session_id` is **not** added to that allowlist (the SLO denominator is bumped by `POST /api/session-start`, not piggy-backed on `/api/client-errors`).

Under those constraints, **LGPD/GDPR exposure is minimal**: the identifier is short-lived, ephemeral, non-derivative, and never persisted server-side. ECM is self-hosted (the data never leaves the operator's container, per ADR-006 §1 "Privacy-neutral by default"), so the cross-jurisdiction concern collapses to "is the operator processing PII about themselves" — which is not a regulated relationship. The threat-model implication is bounded: the worst-case exfiltration of `sessionStorage` reveals an opaque UUID with no inherent meaning, and the dedup set's worst-case compromise reveals "how many sessions started in the last 24h" — both well below the disclosure bar that would warrant rejecting the design.

If the team prefers the conservative path, **B is acceptable** — it introduces no new privacy surface at all, at the cost of the measurement gaps SRE flagged. C with the three constraints above is the recommended choice.

### 3.3 UX Designer — recommends **C** (with caveat for strict-privacy users)

> *Brief: user-facing impact.*

A is invisible to users — the only concern is the silent SLI inflation, which is an operator-facing problem, not a UX one. B is invisible **for authenticated users** but excludes pre-auth users entirely from the SLO; that means any UX issue that manifests on the login page itself (slow chunk load, broken form, blank page from a stale bundle) never registers in the SLO that's supposed to measure "is the frontend healthy." That's a UX-relevance gap, not just a measurement gap.

C is invisible to ~99% of users. The `session_id` write to `sessionStorage` is visible in browser DevTools (Application → Storage → sessionStorage) but ECM's user base is operators on their own infrastructure, not external customers — DevTools visibility is not surprising for this audience. The only edge case is users running browsers with strict privacy settings that disable `sessionStorage` (Tor Browser default, Brave with aggressive shields, Firefox containers in some configurations); for those users, C should fail open — if `sessionStorage.setItem` throws, the reporter skips the bump and the user is simply not counted in the SLO denominator. That is the correct degradation: the user can still use the app; the SLO just doesn't include them.

Recommend **C**, with the explicit fail-open contract that strict-privacy users are silently excluded from SLO-6 rather than pestered with a "please enable storage" dialog.

## 4. Cross-domain conflicts

**No blocking conflicts surfaced.** All three personas converge on **C**, with three independent rationales (measurement accuracy, bounded privacy surface, invisible UX). The only divergence is the Security Engineer's offered fallback to B — which is conservative-but-acceptable, not in opposition to C. This is convergence, not consensus-by-coercion: each persona arrived at C from their own domain's first principles.

The Security Engineer's three constraints on C are not in tension with the SRE's or UX Designer's recommendations — they're additive guardrails the implementation must honor. They are encoded into the implementation steps below.

## 5. Recommended choice: **C — cookie/sessionStorage lifetime**

One-line justification per persona:

- **SRE:** C is the only candidate whose denominator unit matches the unit in the SLO statement; the others structurally distort the SLI.
- **Security:** C is acceptable under three constraints (UUIDv4 not derived from PII, `sessionStorage` not `localStorage`, never persisted server-side); under those constraints the privacy surface is bounded and LGPD/GDPR exposure is minimal.
- **UX:** C is invisible to ~99% of users; strict-privacy users fail open and are silently excluded from the SLO rather than blocked from the app.

## 6. Implementation steps for arp3o

These steps replace the "TBD by spike" placeholders in the arp3o acceptance criteria.

### 6.1 Frontend (clientErrorReporter.ts adjacent or new `sessionTracker.ts`)

1. On first call site (e.g., from `installGlobalErrorHandlers()` or a new `installSessionTracker()` invoked at app boot in `main.tsx`):
   - Read `sessionStorage.getItem('ecm_session_id')`.
   - If present, do nothing. The session has already been counted.
   - If absent (and `sessionStorage` is available), generate `crypto.randomUUID()` (SubtleCrypto-backed), `setItem('ecm_session_id', uuid)`, then `POST /api/session-start` with `{ session_id: uuid }`.
   - **Fail-open:** wrap the entire block in `try/catch`. If `sessionStorage` is unavailable (private mode, strict-privacy browser, SecurityError, QuotaExceededError), skip silently — the user is excluded from the SLO denominator, not blocked from the app.
2. The `POST /api/session-start` request reuses the same fire-and-forget transport pattern as `clientErrorReporter.ts` (`navigator.sendBeacon` with `fetch(..., { keepalive: true })` fallback, `credentials: 'include'`).
3. Honor the same `VITE_ECM_ERROR_TELEMETRY_ENABLED` build flag and runtime `telemetry_client_errors_enabled` setting that gate `clientErrorReporter.ts` — if telemetry is off, the session counter does not bump either. (One toggle for both surfaces is the right operator UX.)

### 6.2 Backend (`backend/routers/client_errors.py` adjacent or new `backend/routers/session_starts.py`)

1. New endpoint `POST /api/session-start` with Pydantic schema `{ session_id: str }`. Validate `session_id` matches a UUID v4 regex; reject anything else with 422.
2. Maintain an in-memory dedup set: `_seen_session_ids: dict[str, float]` mapping `session_id → expiry_timestamp`. TTL = 24h.
3. On request: prune expired entries (lazy, at request time), check membership; if absent, insert with `expiry = now + 24h` and increment `ecm_session_starts_total`. If present, return 200 with `deduplicated: true` (no metric bump).
4. Sit behind the same JWT-or-anonymous-bypass policy as `/api/client-errors` (per ADR-006 §1, the reporter currently runs only for authenticated sessions, but the spike's UX rationale wants pre-auth bootstrap errors to count — surface this question to the PO during arp3o implementation review; default to the existing auth posture unless explicitly changed).
5. Add `ecm_session_dedup_set_size` gauge so SRE can spot pruner leaks.
6. **Never log the `session_id`.** Never return it in any API response. Never write it to the database. The dedup set is the only place it lives, and it dies with the process.

### 6.3 Counter registration (`backend/observability.py`)

Register `ecm_session_starts_total` as a Counter with **no labels** (consistent with the cardinality-bounded posture that ADR-006 §9 codifies). Register `ecm_session_dedup_set_size` as a Gauge with no labels.

### 6.4 SLO-6 SLI expression update (`docs/sre/slos.md`)

Replace the placeholder denominator with the PromQL-native form arp3o's acceptance criteria already drafts:

```promql
1 - (sum(rate(ecm_client_errors_total{kind="boundary"}[28d]))
     / sum(rate(ecm_session_starts_total[28d])))
```

### 6.5 Alert rules update (`docs/sre/prometheus_rules.yaml`)

Convert the `ecm_client_error_rate` group from absolute-rate alerts to ratio-based alerts so they match SLO-6 semantics (e.g., `ECMClientErrorRatioElevated` fires when `ratio > 0.5%` sustained 10m, replacing the current `ECMClientErrorRateElevated` absolute-rate alert).

### 6.6 Tests

- Unit: `sessionStorage.setItem` failure path → no POST issued, no exception bubbles.
- Unit: dedup set rejects a re-submitted UUID within TTL.
- Unit: dedup set accepts a re-submitted UUID after TTL expiry.
- Integration: a full session lifecycle bumps `ecm_session_starts_total` exactly once across N navigations within the same tab.
- Smoke (in `ecm-ecm-1`): open one browser session, observe `/metrics` shows `ecm_session_starts_total` increased by exactly 1.

### 6.7 Follow-up (separate bead, not part of arp3o)

`docs/runbooks/frontend_error_rate.md` will need its alert-language updated from absolute-rate to ratio-based once the alert rules in §6.5 land. A separate bead (already filed under the runbook owner) tracks this.

## 7. What this spike intentionally does NOT decide

- **Pre-auth coverage.** ADR-006 §1 says the reporter runs only for authenticated sessions. The UX rationale for C wants pre-auth bootstrap errors to count. These two are in tension; resolving it is the PO's call during arp3o implementation review and may warrant an ADR-006 amendment (which is **out of scope** for this spike per the brief). Default until explicitly changed: existing auth posture.
- **SLO-6 numeric target revision.** 99.0% lands with i6a1m and is uncalibrated until 30d of data. The chosen semantic does not meaningfully change the measurable baseline (C is the closest to "true sessions," so it represents the SLO statement most faithfully — no recalibration justified by the spike alone).
- **The implementation itself.** That's arp3o, blocked on this spike. arp3o is now actionable.
