# ADR-006: Frontend Error Telemetry (Phase 1 Local Sink, OTel Migration Target)

- **Status**: Accepted
- **Date**: 2026-04-24 (proposed) / 2026-04-24 (accepted)
- **Author**: IT Architect persona (on behalf of PO), synthesizing a concurrent spike with SRE and Security Engineer personas
- **Bead**: `enhancedchannelmanager-bong9`
- **Related**:
  - `enhancedchannelmanager-i6a1m` — Phase 1 implementation bead (blocked by this ADR; carries the full PO-accepted decision record)
  - `enhancedchannelmanager-5uxwh` — operator-provisioned alerting posture (PR #166, open at time of writing; the alerting rule this ADR adds inherits that posture)
  - `enhancedchannelmanager-xnqgo` — ADR-001 (dep-bump validation gate; this ADR's minor frontend runtime changes are in scope of ADR-001's cadence for any dep bump they necessitate)
  - `enhancedchannelmanager-sm3n3` — ADR-005 (CodeQL delta-zero gating; the new `/api/client-errors` router is subject to ADR-005 at PR time)
  - `enhancedchannelmanager-4lk1q` — ADR-004 (release-cut discipline; no direct interaction, noted for completeness)

## Context

ECM has no frontend runtime-error telemetry today. When a React 19 hydration failure, a Vite chunk-load error, a runtime exception, or an unhandled promise rejection happens in an operator's browser, the only signal path is:

1. The operator notices the UI broke.
2. The operator files a GitHub issue or posts in Discord.
3. The maintainer asks for a stack trace, browser version, and steps to reproduce — most of which the operator cannot easily provide from a stale console tab.

Between steps 1 and 3 there is no mechanical signal. The backend's structured-log + Prometheus substrate shipped in bd-ak1db (PR #80) makes server-side failures legibly observable via `/metrics` and the JSON log stream, but anything that breaks in the browser is invisible to the operator's own observability stack. The operator who is running their own Prometheus scrape against `/metrics` has four SLIs, none of them about the frontend.

**Observed gap:** The frontend's `ErrorBoundary` (`frontend/src/components/ErrorBoundary.tsx`) catches render-phase errors and logs them to `console.error`. That's the complete telemetry surface. `window.onerror`, `unhandledrejection`, Vite's `vite:preloadError`, dynamic-import failures, and any crash before the React tree mounts all go unreported. Chunk-load errors after a stale bundle stays in browser cache — one of the most common real-world frontend failure modes — produce zero signal.

**Constraints the architecture has to respect** (from the spike and the existing ECM architecture posture):

1. **Self-hosted, single-container, LAN app.** ECM runs inside `ecm-ecm-1`. There is no hosted backend we control, no multi-tenant cloud, and operators frequently run on consumer hardware behind residential NAT. A solution that depends on outbound internet egress to a SaaS vendor breaks in environments where we have no business demanding outbound access.
2. **Operator-provisioned observability.** Per the `5uxwh` posture documented for PR #166, ECM ships `/metrics` and `prometheus_rules.yaml` but does not ship a Prometheus/Alertmanager deployment. Any new alert rule this ADR introduces inherits that posture — the rule is defined, but the scrape target and Alertmanager routing are the operator's responsibility.
3. **Open-source, no vendor lock-in.** Portability is non-negotiable. If a third party cannot be replaced without a schema migration, it's disqualified.
4. **Privacy-neutral by default.** ECM is self-hosted; operators do not expect the app to phone home. Any external egress must be opt-in with an operator-owned destination.
5. **Zero tolerance for reporter-induced UX damage.** The failure mode the telemetry is meant to detect — the app crashing — must not be aggravated by the telemetry itself. An unreachable sink, a rate-limit 429, or a reporter exception must never become a visible problem on top of the original crash.

**The architectural question:** What is the minimum-viable, portable, self-hosted-friendly frontend error telemetry that closes the detection gap without creating new failure modes, new services to operate, or new vendor dependencies?

The spike on 2026-04-24 ran three personas concurrently against this question — SRE, security-engineer, and it-architect. All three converged on the same shape. The PO accepted the shape the same day. This ADR encodes that decision.

## Decision

**Ship a local-sink frontend error reporter at `POST /api/client-errors` for Phase 1. No external egress by default. OTel-log-record-compatible payload so the Phase 2 migration to an OpenTelemetry collector is a configuration flip, not a schema rewrite.**

Seven load-bearing components:

### 1. Endpoint: `POST /api/client-errors`, JWT-required

A new router under `backend/routers/` registers on `all_routers` and sits behind the same JWT middleware as the rest of `/api/*`. The reporter runs only for authenticated sessions; unauthenticated frontend crashes (e.g., login-page failures) are out of scope for Phase 1 and deferred until authenticated-session base data proves the shape is correct.

Using the existing auth surface avoids a new auth plane, keeps the attack surface consistent with `backend/auth/`, and means the middleware tests already cover the authz path.

### 2. Rate limit: token bucket per session, 10 events/minute + 8 KB/request

A per-session token bucket caps a single browser session at 10 reports per rolling minute, and individual requests at 8 KB after scrub. Overflow returns HTTP 429. The reporter treats 429 as "swallow and stop trying for this session-minute" — it does not retry, buffer, or backoff-with-jitter.

Rationale: a crash-loop — an error that fires an error that fires an error — is one of the few ways a naive telemetry client can DoS its own sink. The bucket bounds the blast radius from a single client. The 8 KB cap bounds the per-request blast radius against pathologically large stack traces (minified-but-not-scrubbed React dev builds can produce 50+ KB stacks). 429 as a terminal state — not a retry signal — means the reporter's own failure mode has a defined, bounded impact.

### 3. Payload: OTel-log-record compatible schema with fixed fields

```
{
  kind:           'boundary' | 'unhandled_rejection' | 'chunk_load' | 'resource',
  message:        str (<=512 chars, scrubbed),
  stack:          str (<=4096 chars, basename-stripped),
  release:        str (Vite build-env identifier),
  route:          str (pathname only — no query string, no fragment),
  user_agent_hash: str (sha256 of full UA string),
  ts:             iso8601 timestamp
}
```

OTel-log-record is the relevant upstream schema for log records in the OpenTelemetry spec. Adopting it now costs nothing extra — field names and types are not chosen for local sink convenience, they're chosen to match what an OTel collector would want to ingest. Phase 2 migration (swap the local sink for an OTLP exporter) changes configuration, not payload structure.

`kind` is a fixed four-value enum — sufficient to differentiate the Phase 1 capture sites (see component 5) without being granular enough to blow out label cardinality. `route` is pathname-only to avoid ingesting query-string PII (user search inputs, filter states, reset tokens). `user_agent_hash` is a SHA-256 of the full UA string; we keep the hash for deduplication/correlation but not the string itself, since any specific UA string is unique enough to be a soft identifier for a user on a small LAN instance.

### 4. Field allowlist: narrow, explicit, deny-by-default

**YES** (captured and transmitted, post-scrub): stack, message, pathname, viewport dimensions, UA major-version digit.

**NO** (never captured, enforced client-side in the reporter code path): query strings, referrer, cookies, localStorage contents, sessionStorage contents, user-typed input of any kind (form values, search boxes, comment drafts), DOM text content around the crash site, URL fragments.

The security-engineer persona's position — which the ADR adopts — is that an allowlist is strictly safer than a denylist for a telemetry surface. A denylist has to enumerate every PII field the frontend knows about; an allowlist has to enumerate every field the reporter needs. The second set is small and legible; the first set grows unboundedly.

### 5. Scrubber: extend `backend/obfuscate.py` patterns; apply client-side AND server-side

`backend/obfuscate.py` already handles IP, hostname, and XtreamCodes-credential redaction for debug bundles. The same patterns get ported to a client-side scrubber module that runs in the reporter before `sendBeacon`, and the backend applies the Python scrubber again before the structlog line is written.

Belt-and-suspenders. Client-side scrubbing means the network payload never contains credentials, so a misconfigured reverse-proxy log never leaks them. Server-side scrubbing means a reporter bug or an upcoming Phase 2 payload expansion can't silently drop the defense.

The scrubber patterns are not re-invented — they're the existing `_IP_RE`, `_URL_RE`, and `_XC_PATH_RE` regexes, promoted to a shared module consumable by both sides.

### 6. Capture sites: six, with defined precedence

The reporter wires into:

1. **`ErrorBoundary.componentDidCatch`** (existing at `frontend/src/components/ErrorBoundary.tsx`) — emits `kind: 'boundary'`. The existing `onError` callback is where the reporter call lands; no structural change to the boundary.
2. **`window.onerror`** — emits `kind: 'boundary'` for uncaught synchronous errors that escape React.
3. **`window.addEventListener('unhandledrejection')`** — emits `kind: 'unhandled_rejection'` for unhandled promise rejections.
4. **Vite `vite:preloadError` event** — emits `kind: 'chunk_load'` for Vite's native preload-failure signal.
5. **Dynamic-import `.catch`** on all `import(...)` sites — emits `kind: 'chunk_load'`. Most chunk-load failures come through here when Vite 6+ is running.
6. **Pre-bundle inline script in `index.html`** (~20 lines, self-contained, no framework dependencies) — emits `kind: 'resource'` via `navigator.sendBeacon` for crashes that happen before the React tree mounts. This is the only site that cannot rely on the main reporter module because the module itself may have failed to load.

Precedence: if a single error would fire two capture sites (e.g., a React render error that also triggers `window.onerror` via a browser quirk), the React-level site wins. The reporter dedupes on `(message, stack-first-line, ts-to-second)` within a 1-second window.

### 7. Transport: `navigator.sendBeacon` with `fetch` fallback; reporter swallows its own errors

`sendBeacon` is the correct primitive for telemetry: fire-and-forget, survives page unload, browser-scheduled. It returns a boolean (queued / not queued) and throws only for argument-type errors. The reporter wraps it in try/catch, falls back to `fetch(..., { keepalive: true })` on browsers where `sendBeacon` is unavailable or returned false, and wraps *that* in try/catch too. A reporter exception is caught, logged to `console.debug` only, and discarded.

**The reporter must never break UX.** No thrown exception from the reporter can propagate. No retry that blocks rendering. No synchronous network call. No user-visible indication that telemetry failed — if the sink is unreachable, the app behaves exactly as it does today, which is to say, the crash is still visible in `console.error` and the ErrorBoundary's fallback UI still renders.

### 8. Server-side landing: structlog + Prometheus counter + histogram

On successful POST:

- One `structlog` line with `logger: 'ecm.client_error'` and the full scrubbed payload as structured fields.
- `ecm_client_errors_total{kind,release}` — Prometheus counter, incremented per report.
- `ecm_client_error_reports_bytes` — Prometheus histogram on post-scrub request size.

Both metrics register on the existing `CollectorRegistry` in `backend/observability.py` alongside the existing four `ecm_*` series. No new `/metrics` endpoint; the existing one exposes them automatically.

### 9. Label cardinality: bounded, forever

- `kind` — fixed four-value enum. Unknown kinds map to `"other"` (the `_normalize_rule_category` pattern already established in `observability.py`).
- `release` — capped to the current Vite build-env identifier plus the **two prior** builds. A release identifier older than that rolls up to `"stale"`. This keeps the cardinality ceiling at 4 (current + 2 prior + stale) regardless of how long an operator runs a given instance.

Label cardinality discipline is not a one-time thing; it's a permanent architectural constraint. Every future label addition to either metric requires an ADR addendum.

### 10. Opt-out: `telemetry.client_errors_enabled` setting, default ON, local-only

A single setting in ECM's existing settings surface toggles the reporter. Default is ON because Phase 1 data never leaves the container — the privacy posture that would justify default-OFF doesn't apply here. Any future external sink (Phase 2) is **opt-IN** with an operator-owned DSN.

Documenting the toggle in `docs/user_guide/` is part of the i6a1m acceptance criteria; this ADR establishes that the toggle exists and names it.

### 11. SLO-6: error-free session rate (architectural intent)

A new SLO-6 entry lands in `docs/sre/slos.md` as part of i6a1m. This ADR does not define the SLO text — that's the implementation bead's deliverable — but it **establishes the architectural intent**:

- **SLI**: fraction of sessions with zero client errors reported, over a rolling 7-day window.
- **Target**: ≥ 99% error-free sessions, marked `to-be-calibrated after 30d of data` (consistent with other uncalibrated SLOs per the `5uxwh` posture).
- **Evaluation gate**: sessions ≥ 50/day before the SLI is computed. Below the gate, SLO-6 reports insufficient-data rather than a computed value — a LAN instance with 3 daily sessions cannot produce statistically meaningful error-free-session rates, and pretending it can is worse than not measuring.

### 12. Alerting rule: operator-provisioned per ADR-005/5uxwh posture

A new alert rule lands in `docs/sre/prometheus_rules.yaml` as part of i6a1m. This ADR establishes the rule exists; the specific threshold (5%, 10%, 25% — to be decided during implementation from first-30d data) is not pre-committed. The rule inherits the operator-provisioned posture from `5uxwh` — defined in YAML, consumed by an operator's Prometheus+Alertmanager, not shipped as a running service.

## Alternatives Considered

| # | Option | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| 1 | **Sentry self-hosted** | Feature-complete RUM; source-map support; release tracking; mature UI for triage | Heavyweight — Sentry self-hosted is a multi-container stack (web, worker, postgres, redis, clickhouse, kafka, nginx); operationally expensive for a single-container app; breaks "no new services" constraint; Sentry's licensing changed in 2023 (BSL) and the self-hosted path is under increasing commercial pressure | Medium — container images exist, schema is proprietary but exportable | ~8 GB RAM + multi-container operational burden + migration risk if BSL tightens |
| 2 | **GlitchTip self-hosted** | Sentry-protocol compatible; lighter (single container feasible); AGPL | Still adds a persistent service to operate; still introduces a schema we'd have to migrate off if GlitchTip stalls; project velocity is single-maintainer with thin sponsorship — higher bus-factor risk than Sentry | Medium | ~1 GB RAM + 1 additional container + dependency on GlitchTip's roadmap |
| 3 | **OpenTelemetry browser SDK + self-hosted collector** | Industry-standard; schema the payload ends up compatible with anyway; no vendor tie; export path to any OTLP-speaking backend | SDK footprint (~50-100 KB gzipped vs. ~2-3 KB for our scoped reporter); operator has to run an OTel collector; collector config surface is non-trivial for operators who don't already run one; Phase 1 of ECM telemetry should not ask operators to stand up a collector before they get any value | High — this is the portability gold standard | Added container / sidecar + operator learning curve |
| 4 | **Default-on SaaS sink (Sentry hosted, Datadog, Honeycomb, etc.)** | Zero infrastructure for operators; best triage UX; free tiers exist | **Rejected by security-engineer persona.** Breaks the self-hosted privacy contract; sends operator telemetry to a third party without explicit consent; creates vendor lock-in of exactly the kind ECM's architecture forbids; many operators run behind restrictive egress firewalls where outbound to a SaaS would silently fail anyway | Low | Zero infra, high trust cost |
| 5 | **Chosen: Local-sink `/api/client-errors` + OTel-schema payload, Phase 2 → OTel collector** | No new services; no outbound egress; portable by construction (payload is OTel-shaped); operators who want more can opt into Phase 2 with minimal migration; the feature delivers value in the operator's existing observability stack (Prometheus + log aggregator) from day one | Won't give Sentry-level triage UX; operator has to read structured logs; base-rate blindness on LAN-size instances (mitigated by SLO-6 evaluation gate); rate-limit tuning needs post-deploy calibration | High | Minimal — one router, one client module, one `index.html` inline script |

## Consequences

### Positive

- **Detection gap closes.** Every Phase 1 failure mode — React hydration errors, chunk-load errors, runtime exceptions, unhandled rejections, pre-mount crashes — produces a `/metrics`-visible counter increment and a structlog line. The maintainer no longer finds out about the crash via Discord.
- **Zero new services to operate.** The backend endpoint is one FastAPI router; the metrics live on the existing `/metrics`; the logs flow through the existing JSON log pipeline. An operator who already runs Prometheus against `/metrics` gets frontend error signal for free.
- **Phase 2 migration is a configuration flip, not a schema migration.** Because the payload is already OTel-log-record-shaped, swapping `/api/client-errors` for an OTLP-over-HTTP endpoint (whether that's a locally-run OTel collector or a SaaS vendor with an OTLP ingress) changes config, not data structure. The client-side reporter stays byte-identical.
- **Privacy-neutral by construction.** Data never leaves the container unless an operator explicitly opts into Phase 2 external sink with their own DSN. A default-on reporter is defensible because the default is "stays local."
- **Attribution for release-triggered crashes is automatic.** The `release` label on `ecm_client_errors_total` makes `"which Vite build introduced this?"` a PromQL query rather than a code-archaeology session. Stale-bundle chunk-load errors become visually obvious on the dashboard as `release != current` counts spiking.
- **Exit cost is low.** Local-sink rollback is: delete the router, delete the client module, delete the metrics registrations, delete the alert rule. ~30 minutes. Payload schema has no database persistence, so there's no data to migrate off.
- **Follows existing ECM conventions.** Router pattern, metric naming (`ecm_<subsystem>_<name>_<unit>`), JSON log format, bounded label cardinality — all established by `bd-ak1db` and reused here without reinvention.

### Negative

- **Rate-limit tuning will need calibration.** The 10/min + 8 KB limits are first-pass guesses. A real operator with a crash-loop bug may hit 429 legitimately before triage captures enough signal; a quiet instance may never see 429 even under reporter misbehavior. Expect one round of tuning within the first 30 days of real traffic.
- **Base-rate blindness on small LAN instances.** An operator with 3 daily sessions cannot produce statistically meaningful error-free-session rates. The SLO-6 `sessions >= 50/day` evaluation gate is the mitigation — below the gate, the SLO reports insufficient-data rather than a misleading value. Operators below the gate still get the raw `ecm_client_errors_total` counter; they just don't get a computed SLO.
- **Label cardinality discipline is a permanent constraint.** Every future label addition to `ecm_client_errors_total` or `ecm_client_error_reports_bytes` requires an ADR addendum. This is the same discipline `observability.py` already enforces for `normalization_rule_matches_total` — not a new burden, but it must not slip.
- **Pre-bundle inline script in `index.html` is a small new failure surface.** The ~20 lines of inline JS have to work in every browser ECM supports, with no dependencies, before the React bundle loads. CSP implications: any operator who tightens the site's Content Security Policy beyond Phase 1 defaults has to whitelist this inline script (or move it to an external file, losing the pre-mount capture). Documented in the i6a1m acceptance criteria.
- **CodeQL coverage applies to the new router.** Per ADR-005, the new `/api/client-errors` router is scanned on PR-to-`dev`. No blocking expected — the router is simple and the surrounding patterns (JWT dep, rate-limit dep, input validation via Pydantic) are CodeQL-friendly — but the implementation bead must not skip CodeQL review.

### Out of Scope (Phase 2 Migration Target)

This ADR deliberately defers to Phase 2 — explicitly not in scope for ADR-006 or for i6a1m:

- **OTel collector export path.** The payload is OTel-log-record-shaped; adopting a locally-run OTel collector as the sink is a future configuration change. No code migration, no schema change. Filed as a Phase 2 bead once Phase 1 data tells us whether operators actually want this.
- **Opt-in external SaaS sink (operator-owned DSN).** An operator who wants to send their telemetry to Sentry Cloud, Honeycomb, Datadog, or any OTLP-compatible SaaS can configure a DSN at that point. Opt-in, not default. Deferred until there's demand.
- **SQLite-backed admin "recent errors" view.** A UI surface inside ECM showing the last N errors from the local sink. Nice-to-have, not core. Deferred until operator demand surfaces — it's possible that structured-log + PromQL is enough and the UI is never needed.
- **Source-map resolution for minified stacks.** Vite's release build produces minified stacks that are operator-readable but not pretty. Source-map upload + server-side symbolication is a well-understood pattern (it's what Sentry does) but non-trivial to implement. Deferred until Phase 1 data shows stack readability is a real triage bottleneck.
- **Unauthenticated-session reporting.** A login-page crash produces no report today. Phase 1 keeps it that way — the alternative (unauthenticated POST endpoint with client-provided session identifiers) has a materially different threat model and deserves its own ADR.

## Risks & Mitigations

| Risk | Mitigation encoded in this ADR |
|---|---|
| **Crash-loop error storms** — an error that fires an error that fires an error, saturating the reporter | Token bucket (10 events/session/minute) + request-size cap (8 KB) + 429-as-terminal behavior. Reporter does not retry on 429. |
| **Label cardinality blow-up** — unbounded `release` values or freeform `kind` strings hydrating `/metrics` into unreadability | `kind` is a fixed 4-value enum (+ `"other"` roll-up); `release` is capped to 3 real values + `"stale"` roll-up. Every future label addition requires an ADR addendum. |
| **PII / credential leakage** — stack traces containing XtreamCodes URLs, user-typed form values in DOM context, query-string secrets | Field allowlist (stack/message/pathname/viewport/UA-major) + deny-by-default for everything else; scrubber applied client-side AND server-side using the existing `backend/obfuscate.py` regex set. |
| **Sink unreachable breaks UX** — a reporter exception aggravating the crash it's meant to report | Reporter wraps `sendBeacon` in try/catch, wraps `fetch` fallback in try/catch, never surfaces an exception. 429 is terminal for the session-minute, not a retry signal. No synchronous network call. |
| **Base-rate blindness on LAN-size instances** — SLO-6 produces noise on instances too small to be statistically meaningful | `sessions >= 50/day` evaluation gate on SLO-6; below the gate, SLO reports insufficient-data rather than a computed value. |
| **Pre-mount crash misses the main reporter** — the crash happens before the React bundle is parseable | Pre-bundle inline script in `index.html` (~20 lines, framework-free, `sendBeacon`-only) captures `kind: 'resource'` before the main reporter loads. |
| **Reporter payload grows past 8 KB on minified dev stacks** | Stack truncation at 4096 chars + basename-stripping (drops the file-path prefix, keeps the filename + line/col) before the 8 KB cap is even evaluated. |
| **Default-on posture surprises a privacy-conscious operator** | Documented in `docs/user_guide/` as part of i6a1m. Opt-out setting (`telemetry.client_errors_enabled`) is one click. Data never leaves the container by default; no external sink is even configurable in Phase 1. |
| **Phase 2 migration locks in an inconvenient payload shape** | Payload is OTel-log-record compatible from day one. Phase 2 is a config flip, not a schema migration. |

## Unresolved Questions

None of these block Phase 1 implementation (i6a1m); all are explicitly deferred:

1. **Rate-limit numeric calibration.** The 10 events/minute + 8 KB request cap are first-pass defaults. Real operator traffic may show these are too loose (reporter misbehavior gets through) or too tight (legitimate crash-loop triage gets throttled before enough signal is captured). Plan: revisit after 30 days of real-instance data.
2. **SLO-6 numeric target.** 99% error-free sessions is a placeholder until 30 days of production data exists. The SLO-5 pattern (uncalibrated on day one, tightened after calibration) applies. Plan: owned by SRE after Phase 1 ships.
3. **Alert rule threshold.** The specific burn-rate / window on the new alert rule in `prometheus_rules.yaml` is not pre-committed by this ADR. The implementation bead (i6a1m) picks the first-pass threshold and the SRE persona tunes it from real data. Plan: implementation-bead scope, not ADR scope.
4. **Deduplication semantics for errors firing in a 1-second window across multiple capture sites.** The ADR specifies a `(message, stack-first-line, ts-to-second)` key inside a 1-second window. Real-world overlap between `ErrorBoundary`, `window.onerror`, and `unhandledrejection` may produce edge cases this key doesn't handle. Plan: revisit if the first 30 days shows meaningful duplicate rates; refinement is a client-side change with no schema impact.

## Exit Path

If the local-sink approach proves unworkable:

1. **Soft exit — disable the reporter, keep the endpoint.** Flip `telemetry.client_errors_enabled` to default-OFF in a dev-side settings migration; delete the alert rule; keep the router + metrics behind the opt-in gate for operators who still want local telemetry. ~10 minutes. Preserves the portability of the payload schema for any operator who wants to re-enable.
2. **Medium exit — cut over to Phase 2 early.** Swap the `/api/client-errors` router for an OTLP-over-HTTP endpoint (or route the client directly at an operator-provisioned OTel collector). Payload stays identical; server-side code changes from "write to structlog + increment counter" to "forward to OTel". ~2 days of engineering work.
3. **Hard exit — remove frontend telemetry entirely.** Delete the router, the client module, the inline script, the metrics, the alert rule, and the SLO-6 entry. ~1 hour. Falls back to the status-quo detection gap that motivated this ADR; the exit would itself be a signal that the detection approach needs to be completely rethought.
4. **Sentry/GlitchTip adoption (Alternative 1 or 2).** If operator demand for triage UX outweighs the operational burden of a full RUM stack, a future ADR supersedes this one. The OTel-shaped payload survives — Sentry's OTLP-ingest path accepts OTel logs — so the client code does not need to change.

No data migration, no database schema, no vendor relationship to unwind.

## Implementation Pointer

This ADR does not implement anything; it encodes the architecture for `enhancedchannelmanager-i6a1m` to build against. Acceptance criteria on i6a1m:

- Backend: `POST /api/client-errors` router + rate limiter + Pydantic schema + shared scrubber + `ecm_client_errors_total` counter + `ecm_client_error_reports_bytes` histogram.
- Frontend: reporter module + `ErrorBoundary` wiring + `window.onerror` + `unhandledrejection` + `vite:preloadError` + dynamic-import `.catch` + pre-bundle inline `index.html` script + opt-out setting.
- Docs: `docs/sre/slos.md` → SLO-6 entry (uncalibrated); `docs/sre/prometheus_rules.yaml` → new alert rule (operator-provisioned); `docs/user_guide/` → telemetry opt-out documentation.

No CI workflow changes. No branch-protection changes. No infrastructure standup. The scope lives entirely in application code and documentation.

## References

- Bead `enhancedchannelmanager-bong9` — this ADR's tracker
- Bead `enhancedchannelmanager-i6a1m` — Phase 1 implementation (blocked by this ADR); carries the full PO-accepted decision record from the 2026-04-24 spike
- Bead `enhancedchannelmanager-5uxwh` — operator-provisioned alerting posture (PR #166, open at time of writing)
- ADR-001 (`docs/adr/ADR-001-dependency-upgrade-validation-gate.md`) — dep-bump cadence; applies to any dep the frontend reporter introduces
- ADR-005 (`docs/adr/ADR-005-code-security-gating-strategy.md`) — CodeQL delta-zero; applies to the new `/api/client-errors` router at PR time
- `backend/observability.py` — existing metric registry and label-cardinality pattern (referenced by components 8–9 above)
- `backend/obfuscate.py` — existing scrubber regex set; extended (not rewritten) by component 5
- `frontend/src/components/ErrorBoundary.tsx` — existing capture site; wired into the reporter by component 6.1
- `frontend/vite.config.ts` — Vite build config; the build-env release identifier (component 3, `release` field) is read from here at build time
- `docs/sre/slos.md` — SLO catalog; SLO-6 lands as part of i6a1m
- `docs/sre/prometheus_rules.yaml` — alert rules file; new rule lands as part of i6a1m
