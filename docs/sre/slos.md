# ECM Service Level Objectives

**Status:** Initial scaffold (v1). Targets are conservative and MUST be recalibrated against 30+ days of production metrics before being treated as commitments.
**Owner:** SRE persona.
**Baseline:** Built on the observability substrate shipped in [PR #80](https://github.com/MotWakorb/enhancedchannelmanager/pull/80) (bd-ak1db). The four `ecm_*` series exposed on `/metrics` are the foundation for every SLI below.
**Last updated:** 2026-04-24 (bd-5uxwh — added Alerting posture section).

## Why this exists

The observability baseline gave ECM *signal*: we can see request rate, latency distributions, error rate, and readiness state on a Prometheus-compatible endpoint. Signal without thresholds is not reliability — it's just telemetry. This document turns signal into **commitments**: what "good" looks like, how much bad we tolerate (error budget), and what we do when the budget burns.

Without SLOs, on-call has no objective criterion for paging. "The app feels slow" is not an incident; "p95 latency breached 500ms for 10 minutes, consuming 15% of the weekly error budget" is. This document exists so that answer is computable, not negotiated.

## Definitions

- **SLI — Service Level Indicator.** The raw metric we measure (e.g., ratio of successful readiness probes to total probes).
- **SLO — Service Level Objective.** The target we commit to for an SLI over a rolling window (e.g., 99% over 30 days).
- **Error budget.** `1 - SLO`. The amount of failure we can absorb without breaching the commitment. For a 99% / 30d SLO, the budget is 1% of 30 days = ~7.2 hours of downtime per 30 days.
- **Burn rate.** Current rate of budget consumption relative to the steady-state burn that would exhaust the budget exactly at window close. A burn rate of 1.0 means we're consuming budget at exactly the rate the SLO allows; 14.4 means we'd exhaust a 30-day budget in ~2 days.

## Scope

These SLOs apply to the `ecm` FastAPI backend (`ecm-ecm-1` container) serving `/api/*` and `/api/health/*` endpoints on the port configured by the container. The `/metrics` endpoint itself is **excluded** from all SLO calculations — the HTTP middleware self-skips it (see `backend/main.py:255`), and it must not influence availability or latency numbers.

Out of scope for v1:

- Frontend asset delivery (served statically; different failure mode).
- WebSocket long-lived connections (`/ws/*` — no histogram coverage yet).
- Background task success rate (task scheduler has no `ecm_task_*` metrics yet — separate bead when we instrument it).

---

## Alerting posture

**The SLO targets below are ECM's commitments. The *alerting* on those targets is operator-responsibility.**

ECM emits Prometheus-compatible SLI metrics — the `ecm_*` family defined in [`backend/observability.py`](../../backend/observability.py) (`ecm_http_requests_total`, `ecm_http_request_duration_seconds`, `ecm_health_ready_ok`, `ecm_health_ready_check_duration_seconds`, and the `ecm_normalization_*` family) — on the unauthenticated `/metrics` endpoint mounted by `backend/main.py`. That endpoint is the SLI substrate; every SLI expression in this catalog resolves against it.

What ECM does **not** ship today:

- A Prometheus instance, scrape config, or retention policy.
- An Alertmanager deployment, routing tree, or paging integration.
- A bundled dashboard stack (Grafana, etc.).
- Any reference `docker-compose` / Kubernetes manifest for the above.

What ECM **does** ship:

- The `/metrics` endpoint (always-on, no feature flag).
- [`docs/sre/prometheus_rules.yaml`](./prometheus_rules.yaml) — the burn-rate and window-based alert rules that correspond to SLO-1, SLO-2, SLO-3, and SLO-4. This is *rules-as-code*: a YAML file intended to be loaded by a Prometheus instance the operator runs, not a live alerting system.
- The runbooks in [`docs/runbooks/`](../runbooks/) — each alert in `prometheus_rules.yaml` carries a `runbook_url` annotation that resolves to one of them.

**Implication:** if an operator does not run their own Prometheus + Alertmanager (or a compatible pull-based scraper) and point it at `/metrics` with `prometheus_rules.yaml` loaded, **no alerts fire**. The SLI data is still emitted — the metrics can be scraped at any time — but nothing is watching it on ECM's behalf. This is consistent with how the rollback runbooks ([dep-bump backend ASGI](../runbooks/dep-bump-backend-asgi-regression.md), [dep-bump frontend](../runbooks/dep-bump-frontend-regression.md)) describe detection: there is always a "with Prometheus scrape" and a "without Prometheus scrape" column, because both are live deployment modes in the field.

### How to get alerting (operator checklist)

Step-by-step Prometheus/Alertmanager deployment is out of scope for this doc — it depends on the operator's environment (bare Docker, Compose alongside ECM, Kubernetes, already-existing home-lab Prometheus, etc.). The two artifacts operators need from ECM are:

1. **Scrape target:** point a Prometheus instance at `http://<ecm-host>:<ECM_PORT>/metrics`. The endpoint is intentionally unauthenticated so scrapers have no session context (see `backend/main.py` near the `/metrics` mount); operators who need to gate it should front it with their own network policy / reverse-proxy rule rather than expecting ECM to negotiate auth with a scraper.
2. **Alert rules:** load [`docs/sre/prometheus_rules.yaml`](./prometheus_rules.yaml) into Prometheus (`rule_files:` entry) and route the resulting alerts to an Alertmanager of the operator's choosing. Validate syntax locally with `promtool check rules docs/sre/prometheus_rules.yaml` before loading.

Everything downstream of that — severity routing, on-call rotations, Slack/email/Discord integrations, silences, dashboards — is the operator's infrastructure.

### Per-SLO alerting ownership

| SLO | Rules-as-code location | Alerting model | Who operates it |
|-|-|-|-|
| SLO-1: Readiness Availability | `prometheus_rules.yaml` group `ecm_readiness_availability` | Prometheus burn-rate + window-based | Operator-provisioned |
| SLO-2: HTTP Request Latency | `prometheus_rules.yaml` group `ecm_http_latency` | Prometheus window-based | Operator-provisioned |
| SLO-3: HTTP Error Rate | `prometheus_rules.yaml` group `ecm_http_error_rate` | Prometheus window-based | Operator-provisioned |
| SLO-4: Readiness Sub-check Latency | `prometheus_rules.yaml` group `ecm_readiness_subcheck_latency` | Prometheus window-based (warning only) | Operator-provisioned |
| SLO-5: Normalization Correctness | Nightly CI canary workflow (`.github/workflows/normalization-canary.yml`) + `ecm_normalization_canary_divergence_total` counter | **Not Prometheus-primary.** A divergent canary run is detected by the CI job itself and surfaces as a workflow failure; the metric exists for operators who *do* run Prometheus to alert on replayed local canary runs, but the source of truth for SLO-5 breach is the GitHub Actions job. | Maintained by ECM CI; operator Prometheus alerting is optional / supplementary |

This keeps the SLO *targets* credible regardless of what infrastructure the operator runs — the metrics are emitted, the rules are authored, the runbooks exist. Whether an alert actually wakes someone up at 3 AM depends on a scraper that ECM does not ship.

---

## SLO-1: Readiness Availability

**SLI:** Fraction of readiness probes that report `ecm_health_ready_ok == 1`, measured as the time-weighted average of the gauge over the window.

**Prometheus expression (SLI numerator over denominator):**
```promql
avg_over_time(ecm_health_ready_ok[30d])
```

**SLO target:** **99.0%** over a rolling 30-day window.

**Error budget:** 1% = ~7h 12m of `ecm_health_ready_ok == 0` per 30d.

**Why this target (initial):** 99.0% is a deliberately conservative starting point. Mature SaaS SLOs for readiness sit at 99.9% (~43 min/month) or higher, but:

1. ECM is a self-hosted LAN app that frequently runs on consumer hardware with non-trivial restart windows (container updates, host reboots, power events). A three-nines commitment would be ambitious without redundancy we haven't built.
2. The readiness probe includes a Dispatcharr dependency — ECM's availability is bounded above by Dispatcharr's availability, which is outside our control.
3. We have **zero days** of real production metrics at the time of writing. The target must be calibrated against observed behavior before tightening.

Re-tune after 30 days of production data. If we sustain 99.9% comfortably, tighten to 99.5%; revisit every quarter.

**What breaks this SLO:**

- Database file lock contention (`/config/journal.db` under heavy auto-creation load).
- Dispatcharr unreachable (network partition, Dispatcharr restart, bad credentials).
- ffprobe binary missing or permissions broken (degrades but does not fail readiness — see `routers/health.py` skip-vs-fail semantics).

**Runbook:** [`docs/runbooks/readiness_availability.md`](../runbooks/readiness_availability.md)

---

## SLO-2: HTTP Request Latency

**SLI:** 95th percentile request latency over `ecm_http_request_duration_seconds`, across all methods and route patterns (excluding `/metrics` and `/api/health/*` — those are instrumented separately and have different SLOs).

**Prometheus expression:**
```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(ecm_http_request_duration_seconds_bucket{path!~"/metrics|/api/health/.*"}[5m])
  )
)
```

**SLO target:** **p95 < 500ms** over rolling 5-minute windows, for at least **99%** of those windows over 30 days.

**Error budget:** 1% of 30d * 5m windows = ~86 minutes of windows where p95 ≥ 500ms per 30d.

**Why this target (initial):** The latency histogram buckets ak1db shipped (`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0`) were tuned for "local web app hitting SQLite + LAN Dispatcharr" where most requests land under 100ms. A 500ms p95 ceiling gives comfortable headroom: if we breach it routinely, we've got a real slowdown worth investigating. This bucket layout also means `histogram_quantile` interpolation is reasonable around the 500ms boundary — the 0.25 and 0.5 buckets straddle it cleanly.

Tighten to p95 < 250ms once we have baseline data showing we comfortably beat 500ms.

**What breaks this SLO:**

- SQLite write amplification during bulk auto-creation (lots of small transactions).
- Dispatcharr API slowness (ECM proxies many requests).
- N+1 query patterns in frontends (often the real culprit — a single user view hitting `/api/channels/*` 500 times serially).

**Runbook:** [`docs/runbooks/http_latency.md`](../runbooks/http_latency.md)

---

## SLO-3: HTTP Error Rate

**SLI:** Ratio of HTTP 5xx responses to total responses, across all routes (excluding `/metrics` which is self-skipped by the middleware).

**Prometheus expression:**
```promql
sum(rate(ecm_http_requests_total{status=~"5.."}[5m]))
/
sum(rate(ecm_http_requests_total[5m]))
```

**SLO target:** **5xx error rate < 1%** over rolling 5-minute windows, for **99%** of windows over 30 days.

**Error budget:** Same as SLO-2 structurally — ~86 minutes of > 1% 5xx windows per 30d.

**Why this target (initial):** 1% is intentionally loose for a scaffold. A mature SLO would target 0.1% or tighter, but:

1. Some ECM endpoints wrap Dispatcharr (not our failure mode, but our status code).
2. Auto-creation rules with bad upstream streams will 5xx as designed until the user fixes the rule.
3. We don't yet separate "our bug" 5xxs from "integration partner down" 5xxs.

A follow-up bead should split this SLO by `path` label once we've observed which routes dominate the error budget, and introduce per-route sub-objectives (e.g., `/api/auth/login` should be far stricter than `/api/stream/probe`).

4xx responses are **not** counted — they reflect client behavior, not service reliability. A spike in 401s during a credential-rotation event is correct behavior, not an SLO breach.

**What breaks this SLO:**

- Database session exhaustion / pool timeouts.
- Dispatcharr returning 5xx (we pass through).
- Unhandled exceptions in routers (caught by the OWASP 500-scrubber middleware — still counts as a 5xx for this SLO, which is correct).

**Runbook:** [`docs/runbooks/http_error_rate.md`](../runbooks/http_error_rate.md)

---

## SLO-5: Normalization Correctness

**SLI:** Fraction of nightly canary runs where the Test Rules preview path (`engine.test_rule` / `engine.test_rules_batch`) and the auto-creation executor path (`engine.normalize`) produce byte-identical output — **including identical `matched_rule_ids`** — across the full shared Unicode fixture bank (`backend/tests/fixtures/unicode_fixtures.py`). A single fixture diverging in a single run counts as a full-run failure for the purpose of this SLI.

**Prometheus expression (SLI numerator over denominator):**
```promql
1
-
(
  rate(ecm_normalization_canary_divergence_total[7d])
  /
  # Denominator: canary runs per 7 days. The workflow runs once per day at
  # 07:00 UTC, so the steady-state denominator is 7. Exposed as a recording
  # rule (`ecm:normalization_canary_runs_per_7d`) once alert-manager is wired.
  7
)
```

For SLO evaluation without the recording rule (interim), compute the
numerator directly — the `ecm_normalization_canary_divergence_total`
counter increments by 1 per divergent canary run, never per-fixture, so
`increase(ecm_normalization_canary_divergence_total[7d])` is the
human-readable number of SLO-5 breaches in the last 7 days.

**SLO target:** **100.0% parity** over a rolling 7-day window — any single divergent canary run is an SLO breach. (Unlike latency/availability SLOs where "99.9%" accepts a statistical tail, correctness is an invariant — the two paths share one NormalizationPolicy by construction; divergence is a structural bug, not a probabilistic event.)

**Error budget:** **Zero.** There is no tolerable rate of Test-Rules vs. Auto-Create divergence. The error budget policy below is punitive because a single divergence reproduces the exact failure mode behind GH #104.

**Why this target:** The entire point of bd-eio04 (epic: "Normalization parity") was to eliminate the divergence class that made Settings → Normalization Test and the auto-creation executor produce different outputs. A non-zero SLI means the unification regressed. 99.9% is not an acceptable answer — it means one bad deploy per thousand canary cycles silently slips through. Correctness is binary.

**Why this uses SLO-5 (not SLO-4):** SLO-4 is already claimed by Readiness Sub-check Latency above — renaming bd-eio04.9 to the next free slot was called out in the grooming comment on the bead.

**What breaks this SLO:**

- A commit to `backend/normalization_engine.py` that changes the Test Rules or Auto-Create preprocessing path without updating the other.
- An operator disabling the unified policy flag on one path (`ECM_NORMALIZATION_UNIFIED_POLICY`) without disabling it on the other — policy version is logged into the decision record for exactly this correlation.
- A new rule type or action that the two code paths interpret differently.
- Adding a fixture to `unicode_fixtures.py` whose `expected_normalized` value one path produces but the other does not.

**Runbook:** [`docs/runbooks/normalization-canary-divergence.md`](../runbooks/normalization-canary-divergence.md)

**Error-budget policy (SLO-5-specific override):**

| Budget state | Trigger | Response |
|-|-|-|
| **Healthy** | Zero divergences in window | Normal work. |
| **Breached** | One or more divergences | File a P2 ticket, open the runbook, **block the next release cut until resolved**. A blameless postmortem is recommended but not mandatory at first occurrence; the second occurrence within 30 days upgrades to mandatory postmortem. |

The "block release cut" rule overrides the normal 25%/50%/75%/100% budget bands above — correctness cannot be deferred by budget math. The standard policy resumes once the breach is closed out (root cause identified, regression test added, canary green for one consecutive run).

**Supplementary diagnostic signal (not the SLI):**

A bug-report ratio — `1 - (bug_reports_containing_normaliz_30d) / (auto_creations_30d)` — is tracked on the normalization dashboard for trend analysis. This is **not** part of the SLI because bug reports lag the incident by days and are subject to reporter self-selection; it's useful as a leading indicator of user-perceived correctness but cannot be the thing we page on.

---

## SLO-6: Frontend Error-free Session Rate

**SLI:** Fraction of authenticated user sessions that report zero client errors over a rolling 28-day window. A "session" is approximated by distinct `user_agent_hash` labels seen on `ecm_client_errors_total` within the window. The SLI is computed as:

```
1 - (sessions_with_errors / total_sessions)
```

**Prometheus expression (SLI, until a dedicated session counter exists):**
```promql
# Sessions that reported at least one client error, over 28d.
# NOTE: ecm_client_errors_total is keyed by {kind, release} — the session
# dimension is not exposed as a label (bounded cardinality, per ADR-006).
# Until a session counter ships (see "Instrumentation gap" below), the
# numerator here is approximated by 'count of distinct user_agent_hash
# values seen in the structlog stream' via the log-aggregation substrate,
# not by a PromQL-native expression. The denominator is the same over
# the same window from the log stream.
1
-
(
  # Placeholder — replace with the session counter once instrumented.
  sum(increase(ecm_client_errors_total[28d]))
  /
  # Denominator: total sessions. For now, read from the structured-log
  # aggregator. When the session counter lands, swap this for:
  #   sum(increase(ecm_session_starts_total[28d]))
  1
)
```

**SLO target:** **99.0%** error-free sessions over a rolling 28-day window, **marked uncalibrated** until 30 days of production data exists.

**Why 99.0% (initial):** Consistent with SLO-1 (readiness availability) and SLO-3 (5xx error rate) at 99.0% — we have no production baseline for frontend crash rate, and a 99.5% target would front-run data we don't have. ADR-006 explicitly calls the SLO target "placeholder until 30 days of production data exists". Once baselined, the SLRE persona tightens to 99.5% (or looser — if LAN instances see 10%+ error rates, 99% is fiction).

**Why 28-day window (not 30):** 28 days is four weekly cycles. A deploy cadence that ships one release per week produces four full cycles in the window, letting the SLO absorb a single bad week without triggering alert noise. 30 days is a slightly-off-cycle window that conflates weekly patterns with the rolling boundary.

**Error budget:** 1% = up to 1 session in 100 reporting ≥1 client error, per 28d. On a LAN instance with 3 sessions/day (~84 sessions/28d), the budget is ≤0.84 error-affected sessions — functionally "one bad session per month". The `sessions >= 50/day` evaluation gate (below) prevents the SLO from reporting nonsense on instances too small for statistical meaning.

**Evaluation gate:** Per ADR-006 §11, the SLI is computed only when the window contains **sessions ≥ 50/day** (1,400+ sessions over 28d). Below the gate, SLO-6 reports **insufficient-data** rather than a value — a LAN instance with 3 daily sessions cannot produce a statistically meaningful error-free-session rate. Operators below the gate still see the raw `ecm_client_errors_total` counter on `/metrics`; they just don't get a computed SLO.

**Instrumentation gap:** The current backend emits `ecm_client_errors_total` per report but does not emit a session counter. To produce a Prometheus-native SLI (no log-aggregation dependency), a follow-up bead needs to add `ecm_session_starts_total` (incremented on frontend login or on first protected-route navigation per `user_agent_hash`). Until then, the SLI lives in the log substrate and the target above is aspirational.

**What breaks this SLO:**

- Stale-bundle chunk-load errors after a deploy (`kind: 'chunk_load'` counter spikes with `release != current`).
- React runtime exceptions in a specific tab (`kind: 'boundary'`, correlates to a single code path).
- Unhandled promise rejections from an API client contract change (`kind: 'unhandled_rejection'`).
- Pre-mount bundle load failures (`kind: 'resource'`, emitted by the inline script in `index.html`).

**Runbook:** [`docs/runbooks/frontend_error_rate.md`](../runbooks/frontend_error_rate.md) — covers the `ECMClientErrorRateElevated` (ticket) and `ECMClientErrorRateCritical` (page) alerts in `prometheus_rules.yaml` group `ecm_client_error_rate`: triage by `kind` × `release`, kind-by-kind common causes, rollback / cache-flush / suppression mitigation patterns, and the severity-promotion ladder (bd-pls9m).

---

## SLO-4: Readiness Sub-check Latency (informational)

**SLI:** 95th percentile duration of readiness sub-checks, per `check` label (`database`, `dispatcharr`, `ffprobe`).

**Prometheus expression (per check):**
```promql
histogram_quantile(
  0.95,
  sum by (le, check) (
    rate(ecm_health_ready_check_duration_seconds_bucket[5m])
  )
)
```

**SLO target (soft / informational only):**

- `database` sub-check p95 < 50ms
- `dispatcharr` sub-check p95 < 500ms
- `ffprobe` sub-check p95 < 100ms

**Why informational only:** These are diagnostic signals — a slow readiness sub-check is a leading indicator for SLO-1 and SLO-2, but users don't directly experience readiness probe latency. We alert **warning** (not page) on breach so on-call gets situational awareness without being woken up.

**Runbook:** [`docs/runbooks/readiness_subcheck_latency.md`](../runbooks/readiness_subcheck_latency.md)

---

## Error-budget policy

What happens when the error budget burns? The rules below apply per-SLO; burns are evaluated weekly.

| Budget state | Trigger | Response |
|-|-|-|
| **Healthy** | <25% budget consumed in window | Normal feature work. No action required. |
| **Concerned** | 25–50% consumed | SRE posts weekly status in the sprint channel. No scope change. |
| **Warning** | 50–75% consumed | Reliability work is prioritized in the next sprint grooming. New features not yet started are deferred if they risk further burn. |
| **Critical** | 75–100% consumed | All feature work stops. Incident review. Reliability fixes ship before anything else resumes. |
| **Exhausted** | 100%+ consumed (SLO breached) | Blameless postmortem within 5 business days. Freeze on new deployments except reliability fixes until the budget resets or recovers by ≥10 percentage points. |

**Budget resets** on a rolling basis — the 30-day window always looks at the last 30 days, so a burn today drops out 30 days from now if no new burn occurs.

**Exceptions:** Security fixes always ship regardless of budget state. A zero-day 4pm on Friday doesn't wait for the error budget to heal — but it's tracked and the postmortem captures the reliability cost.

---

## Alerting strategy

Alert rules are defined in [`prometheus_rules.yaml`](./prometheus_rules.yaml) — rules-as-code only. See the [Alerting posture](#alerting-posture) section above for why ECM ships the rules file but not a Prometheus/Alertmanager runtime: operators wire the scrape and routing themselves. The strategy embedded in the rules file is multi-window multi-burn-rate (MWMBR) where feasible:

- **Fast burn (page):** If the current burn rate would consume 2% of a 30-day budget in 1 hour (14.4x burn), page immediately. This catches acute incidents.
- **Slow burn (ticket):** If the current burn rate would consume 10% of a 30-day budget in 6 hours (6x burn) sustained, open a ticket / warning alert. This catches chronic degradation.

For the scaffold we ship simpler single-window thresholds for SLO-2/3 (p95 latency breach, 5xx rate breach) because burn-rate alerts on histogram-derived SLIs require recording rules we haven't provisioned. Follow-up bead: add recording rules + multi-window burn-rate alerts once a Prometheus scrape target exists to consume them.

---

## Open questions / known gaps

- **No traffic-weighted availability.** SLO-1 treats every 15-second scrape of `ecm_health_ready_ok` as equal weight, regardless of how many user requests landed during that interval. A future refinement: weight by `ecm_http_requests_total` rate.
- **No per-tenant SLO.** ECM is single-tenant by deployment, but the SLOs above are global — they don't distinguish a power user from a casual one. Fine for v1; revisit if we ship multi-tenant.
- **No Dispatcharr-upstream vs ECM-self attribution.** When `ecm_http_requests_total{status="502"}` fires because Dispatcharr returned 502, it still counts against our SLO. The fix is a separate label or metric, tracked as a follow-up.
- **No SLO for long-running tasks.** Task success rate matters (restore jobs, auto-creation runs) but has no metric today. Separate bead.

## Changelog

- **2026-04-24 (bd-i6a1m):** Added **SLO-6: Frontend Error-free Session Rate** — 99.0% error-free sessions over 28d, uncalibrated until 30d of data, gated at sessions ≥ 50/day. Supported by the new `/api/client-errors` router + `ecm_client_errors_total{kind,release}` counter from ADR-006 Phase 1. Instrumentation gap: a session-start counter is a follow-up bead; until then the SLI denominator lives in the log-aggregation substrate. Companion alert rule shipped in `prometheus_rules.yaml` (group `ecm_client_error_rate`).
- **2026-04-24 (bd-5uxwh, absorbs bd-9mi6f):** Added **Alerting posture** section. Makes explicit that ECM emits SLI metrics on `/metrics` and ships `prometheus_rules.yaml` as rules-as-code, but does not ship a Prometheus/Alertmanager runtime — operators provision their own scraper if they want alerts to fire. Per-SLO ownership table added. SLO-5 noted as the edge case: its breach signal is the nightly CI canary, not Prometheus-primary.
- **2026-04-22 (bd-eio04.9):** Added **SLO-5: Normalization Correctness** — canary-based parity SLI with zero-tolerance target. Supported by new metrics in `observability.py` (`ecm_normalization_canary_divergence_total`, plus rule-match / no-change / duration / per-creation-normalized counters), a structured decision log (see `NORMALIZATION_DECISION_LOGGER`), and a nightly CI canary (`.github/workflows/normalization-canary.yml`). Runbook at `docs/runbooks/normalization-canary-divergence.md`.
- **2026-04-20 (bd-dl1bd):** Initial scaffold. Four SLOs defined, targets conservative, error-budget policy drafted, alert rules shipped in sibling YAML. **Not yet calibrated against real traffic** — targets must be revisited once 30 days of production metrics exist.
