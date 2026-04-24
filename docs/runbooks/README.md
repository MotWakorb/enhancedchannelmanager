# Runbooks

Operational playbooks for on-call and incident response. Read during incidents, written for the person who is stressed, tired, and possibly not the one who built the system.

## Conventions

- Every runbook follows `template.md`: **Alert/Trigger → Symptoms → Diagnosis → Resolution → Post-incident**.
- Commands are exact and copy-pasteable. No "run the usual deploy" — spell it out.
- Decision points are `if / then`, not paragraphs of context.
- Every alert that pages a human must have a runbook listed here. An alert without a runbook is a fire alarm without an exit map.
- Update the runbook during the post-incident review — a runbook that survived an incident without edits is a runbook that wasn't followed, or wasn't needed.

## Index

| Runbook | Scope | Last Exercised |
|-|-|-|
| [v0.16.0 Hard Rollback](./v0.16.0-rollback.md) | Post-release rollback of a tagged version across git, GitHub Release, and GHCR | 2026-04-20 (real incident) |
| [NormalizationPolicy Unified Mode](./normalization-unified-policy.md) | `ECM_NORMALIZATION_UNIFIED_POLICY` — rollback switch for the bd-eio04.1 unified Unicode preprocessor (GH #104) | Not exercised |
| [Normalization Canary Divergence](./normalization-canary-divergence.md) | Nightly canary detected Test Rules vs Auto Create output drift — SLO-5 breach, zero error budget | Not exercised |
| [Duplicate Channels — Unicode Suffix](./duplicate-channels-unicode-suffix.md) | Manual triage for user-reported duplicate channels caused by Unicode-suffix divergence (ᴴᴰ / ² / ZWSP / NFD) | Not exercised |
| [Request Timeouts, Concurrency, CPU Offload](./request-timeout.md) | 504 / 503 response patterns; `ECM_REQUEST_TIMEOUT_SECONDS` + `ECM_LIMIT_CONCURRENCY` + CPU pool tuning (bd-w3z4h) | Not exercised |
| [HTTP Error Rate](./http_error_rate.md) | SLO-1 breach — 5xx rate elevated above budget | Not exercised |
| [HTTP Latency](./http_latency.md) | SLO-2 breach — P95 latency elevated above budget | Not exercised |
| [Readiness Availability](./readiness_availability.md) | Readiness check failing across sub-checks | Not exercised |
| [Readiness Sub-check Latency](./readiness_subcheck_latency.md) | Individual readiness sub-check exceeding its latency budget | Not exercised |
| [Dep-Bump Fresh-Image Smoke Test](./dep-bump-smoke-test.md) | Pre-merge ADR-001 fresh-image smoke for dependency-upgrade PRs (`scripts/smoke_test_dev_container.sh`) — workflow runbook, not paging | 2026-04-23 (script self-test) |
| [Infra-Side Cache Invalidation](./infra-cache-invalidation.md) | Operator-facing: flush reverse-proxy / CDN (nginx, Cloudflare, Varnish, generic) caches after a frontend release — companion to the browser-side dep-bump frontend runbook | Not exercised |

## Adding a runbook

1. Copy `template.md` to `<alert-or-scenario-slug>.md`.
2. Fill every section. If a section does not apply, write `N/A — <reason>`; do not delete the heading.
3. Add a row to the index above.
4. Open a PR. Runbooks are reviewed by the Technical Writer (clarity) and the SRE (operational accuracy). Both approvals required.
