# ADR-014: Dispatcharr API drift strategy (recorded fixtures + a contract sweep + a version advisory)

- **Status**: Accepted
- **Date**: 2026-08-03 (PO approved the strategy the same day the design bead's architect report was delivered)
- **Author**: IT Architect persona (design report on `enhancedchannelmanager-ax0kf`), implemented by the Project Engineer persona.
- **Bead**: `enhancedchannelmanager-ax0kf` (design + implementation) · evidence beads `enhancedchannelmanager-q6xjl` (PR #765), `enhancedchannelmanager-lsa0s` (PR #768), `enhancedchannelmanager-r9oqx` (PR #772, the docs correction).
- **Scope**: how ECM defends against drift in the **Dispatcharr REST surface** it consumes through `backend/dispatcharr_client.py`. It does not govern ECM's own public API (`docs/api.md`) or any other upstream integration (Emby / Plex / Jellyfin / Schedules Direct).

## Context

**The bug class.** ECM's Dispatcharr client was written against a *guessed* surface. A comment at `backend/dispatcharr_client.py` (near the DBAS restore helpers) deferred verification to "later"; later never came. Three shipped bugs trace to that single deferral:

| Bead | What was guessed | How it failed in production |
|------|------------------|-----------------------------|
| `q6xjl` root cause A | `GET /swagger.json` "returns YAML despite the name" | The real route is `GET /api/schema/`; a bare GET renders YAML and `response.json()` raised `Expecting value: line 1 column 1`. `?format=json` is required. |
| `q6xjl` root cause B | `/api/core/settings/{key}/` (key-string detail route) | `CoreSettingsViewSet` is a plain DRF `ModelViewSet` with the default `pk` lookup — the detail route is keyed by **integer id**. A key-string URL matched no route and 404'd **7/7 settings** on a same-instance restore round-trip. |
| `lsa0s` | `/api/dvr/rules/` | Exists on **no** Dispatcharr version. A request falls through to the SPA catch-all and returns `200 text/html` (the app shell), so `get_dvr_rules()` raised a JSON-parse error and every backup silently exported a `_warning` stub for the whole `dvr_rules` category. |

Each was found by an operator or a manual round-trip, never by CI. Each was fixed as a one-off. Nothing prevented the fourth.

**Why unit tests could not catch these.** Every one of the client's tests mocks `_request` and asserts on the *path string the client passed*. That verifies the client calls what the test author believed it should call — the same belief that produced the bug. A mocked-shape test over a guessed vocabulary verifies the assumption, not the integration.

**Measured baseline** (architect, against the live Dispatcharr 0.28.2 OpenAPI document — 224 paths):

- 97 Dispatcharr call sites in `backend/dispatcharr_client.py`, over **58 distinct URL templates** (92 distinct `(method, template)` pairs); 27 of those templates are DBAS-restore-critical.
- 3 templates were pinned by recorded fixtures at design time.
- Exactly 3 templates failed to match the live document — all of them the `/api/dvr/rules/*` family, now fixed by `lsa0s`. Every other template matched method-for-method.

**Trust posture.** Home-lab, self-hosted, single-operator. Upstream is a *sibling open-source project on the operator's own machine*, not a versioned SaaS contract with a deprecation policy. It can and does move between minor versions, and the operator upgrades it on their own schedule without telling ECM.

## Decision

Adopt **(a) + (b)**, with **(c) as an advisory only**, and reject **(d)** for now.

| # | Option | Verdict | Rationale |
|---|--------|---------|-----------|
| **a** | **Recorded fixture per touched path** — capture a literal slice of a real response and assert against it. | **KEEP** — the deep pin, applied selectively to high-risk paths. | The only mechanism that catches *semantic and format* drift (`q6xjl` A and B: a renderer that returns YAML, a detail route keyed by the wrong field). Expensive per path — it needs a live instance, a judgement call about what to retain, and a redaction pass — so it stays targeted rather than universal. The 27 DBAS-critical templates are the priority queue for earning one. |
| **b** | **Contract sweep** — derive every `(method, URL template)` from the client's source and check it against a recorded manifest of the upstream OpenAPI paths. | **ADOPT** — the breadth net. | Catches the *existence/method* class (`lsa0s`) across all 58 templates at once, for one recorded artifact and one test file. Extraction is automatic, so breadth is free: sweeping all 58 costs the same as sweeping the 27 critical ones. Requires no live instance at test time. |
| **c** | **Runtime version gate** — check the Dispatcharr version ECM is talking to. | **ADOPT, ADVISORY ONLY** (warn; never block). | Closes the silent-upgrade gap: (a) and (b) both validate against a *recorded* version, so an operator who upgrades Dispatcharr underneath ECM gets a green CI and a broken instance. A **blocking** gate is wrong for the home-lab tier — it would lock an operator out of their own tool over a version tuple, and ECM has no way to know a new version is actually incompatible. Rides this bead per PO decision rather than a separate one. |
| **d** | **Schema-driven client generation** — generate the client from the OpenAPI document. | **REJECT for now.** | The client's value is precisely what a generator throws away: the credential-hygiene layer (never logging `path`, the `setting_id`/`setting_value` naming that keeps CodeQL's taint analysis honest), the response-shape translation (bare-list vs DRF envelope, old-vs-new EPG pagination), and the bounded-stream reader. A generated client re-imports all of that as hand-written wrappers, and the generator becomes a build-time dependency on a document ECM currently only *reads*. Revisit only if the consumed surface grows an order of magnitude. |

### What the sweep is, concretely

- **`scripts/record_dispatcharr_openapi_manifest.py`** — takes a raw `GET /api/schema/?format=json` response (captured by hand; **no live dependency is baked in**) and emits `backend/tests/fixtures/dispatcharr_openapi_paths_manifest.json`: `{normalized path -> {method -> path parameters}}`, with body schemas, component schemas, descriptions, tags, security and every non-path parameter **stripped**, plus the provenance-metadata block the existing recorded fixtures established (`source`, `fixture_kind`, `captured_at`, `dispatcharr_version`, `why`).
- **`backend/tests/unit/test_dispatcharr_client_contract_sweep.py`** — an `ast` walk over `DispatcharrClient` collecting `self._request("METHOD", <path>)`, the bounded-stream helper's forwarded paths, and the two raw `self._client.post(...)` login/refresh calls. F-string interpolations normalize to a `{}` wildcard; module-level path constants resolve by importing the module.
- **Assertions**: path exists, method allowed, path-parameter **count and type** line up. Query parameters and request/response **body shape are explicitly out of scope** — generalizing body assertions into the sweep would rebuild option (d) in miniature, and that job belongs to (a).
- **The manifest is deliberately a separate fixture** from `dispatcharr_openapi_recorded.json`. Different purpose, different churn profile: an upstream body-schema edit must not spuriously fail the breadth sweep. It needs no redaction — paths, methods and parameter types carry no operator data — which is a real simplicity win over the deep fixtures.

### Non-negotiable properties of the sweep

1. **Fail loudly on anything unresolvable.** A path expression the extractor cannot statically resolve is a test failure, never a silent skip. A silently-partial sweep reporting green would recreate the original failure — a claim of coverage that was never real — in miniature.
2. **A canary on call volume.** The extractor must find a non-trivial number of call sites (floors: 50 sites, 40 distinct pairs, against 97/92 actual), so a broken extractor cannot pass by finding nothing.
3. **New raw HTTP call sites are visible.** Any new direct `self._client.<verb>(...)` that is neither extracted nor a reviewed piece of plumbing fails a test until a human classifies it.
4. **The failure message names both causes without presuming one** — stale manifest (re-record) *or* real client bug (fix the client, `lsa0s` as prior art) — names each offending `(method, path, owning method)` tuple and the manifest's recorded version/date, and explicitly forbids `xfail`/`skip`/allowlisting to force CI green.
5. **Exemptions self-invalidate.** There is exactly one — `GET /api/schema/`, which drf-spectacular does not document inside the document it generates — and a test fails if it ever becomes unnecessary, so it cannot rot into an allowlist that hides real drift.

### The version advisory, concretely

`DispatcharrClient.get_version()` calls `GET /api/core/version/` (present in the schema, previously uncalled). The existing connection test — `POST /api/settings/test`, `backend/routers/settings.py` — probes it after a *successful* authentication and, if the reported version is outside the tested set, returns a non-blocking `warning` string alongside `success: true`. The operator sees a warning notification; the connection still verifies and still saves.

- **"Tested versions" is a single module-level constant**, `TESTED_DISPATCHARR_SERIES`, listing known-good `MAJOR.MINOR` series (Dispatcharr is 0.x, so the *minor* is its breaking-change axis). Updating it is a one-line edit made at the same time as re-recording the manifest.
- **Failure to determine the version is never an advisory.** An older Dispatcharr without `/api/core/version/`, a timeout, or an unparseable body produces no warning — the connection test's job is to test the connection, and a nag the operator cannot act on is worse than silence.

## Consequences

### Positive

- **The `lsa0s` class of bug cannot ship again silently.** All 58 templates are checked on every test run, hermetically, with no live instance and no new CI job.
- **Coverage tracks the client automatically.** A PR that adds a client method gets its new paths swept without anyone remembering to extend a list — the extraction is derived from source, never hand-copied.
- **Recording is cheap and repeatable.** Re-pointing at a new Dispatcharr is one read-only GET plus one script invocation; the diff of the manifest is itself a readable summary of what moved upstream.
- **The silent-upgrade gap has a signal.** Previously an operator could upgrade Dispatcharr and ECM would say nothing until something broke.

### Negative / limitations — read these before trusting a green sweep

- **The sweep verifies existence, method, and path-parameter type ONLY.** It does **not** verify response shape, field names, semantics, status codes, query parameters, or request bodies. A green sweep means *no endpoint is imaginary*; it does **not** mean the integration works. Neither of `q6xjl`'s two root causes would have been caught by the sweep — they were caught, and are now pinned, by option (a)'s deep fixtures. **The two mechanisms are complements, and treating (b) as a substitute for (a) is the main way this ADR could be misread.**
- **The manifest is a snapshot, and snapshots go stale.** It is only as current as the last deliberate re-recording. This is the price of a hermetic test, and it is why (c) exists.
- **Path-parameter typing is checked asymmetrically.** An `int`-annotated interpolation against an upstream parameter that declares a `format` (`uuid` being the shape a primary-key migration produces) fails; against a bare untyped `string` it does not. drf-spectacular emits untyped `string` for path converters with no declared type — twelve of the parameters ECM interpolates are like that on 0.28.2, and every one is an integer pk in practice. Flagging them would be twelve permanent false positives, which trains readers to ignore the sweep.
- **The version advisory is advisory.** An operator who ignores it gets exactly the behaviour they get today. That is the deliberate home-lab-tier trade.

### Recording cadence — deliberate, not scheduled

Re-record the manifest **on adopting a new Dispatcharr version**, or **when a PR adds client methods** the current manifest predates. No calendar cadence: a scheduled re-record against an instance nobody changed is churn, and a re-record that happens automatically defeats the point (the diff must be *read*).

### Operational rule — event-triggered restore exercise

**A Dispatcharr MAJOR-version bump the operator adopts triggers a restore exercise before trust:** re-record the manifest against that version, run the sweep, *and* run a real backup→restore round-trip before relying on DBAS against it. Event-triggered, not calendar-based — contrast [ADR-005](ADR-005-code-security-gating-strategy.md)'s monthly-then-quarterly audit cadence, which is periodic because the *threat* is continuous. Here the risk is discrete and observable: it arrives with the upgrade. This complements the monthly dry-run habit `nvhg7` added to `docs/runbooks/disaster-recovery-restore.md`, which exercises the artifact; this rule exercises the *upstream contract*.

## Alternatives Considered

- **Do nothing / keep fixing paths one at a time.** Rejected. Three bugs from one deferral, each found in production, is a pattern, not a coincidence; the fourth was a matter of time.
- **A live smoke test in CI against a real Dispatcharr.** Rejected. It makes the test suite depend on a running upstream instance, converting an upstream outage or a version bump into a red build for every unrelated PR. The recorded manifest gets most of the signal with none of the coupling; the live check belongs in the deliberate re-record step and the restore exercise instead.
- **Generating the sweep's expectations from the client by hand** (a checked-in list of paths). Rejected. A hand-maintained list is exactly the artifact that drifts from the code it describes — the same failure mode as the comment that claimed verification had happened.
- **A blocking version gate.** Rejected — see option (c). It would lock an operator out of their own tool over a version tuple, on a tier where the operator is the administrator.
- **Schema-driven client generation.** Rejected for now — see option (d). Exit path: if the consumed surface grows an order of magnitude, generate the transport layer and keep the hygiene/translation layer hand-written on top.

## Related

- `docs/dispatcharr_api.md` — the developer-facing contract reference, including "How to verify a path against the live schema" (corrected under `r9oqx`, PR #772) and the recorded-fixture inventory.
- `backend/tests/fixtures/dispatcharr_openapi_recorded.json`, `dispatcharr_core_settings_recorded.json`, `dispatcharr_dvr_recurring_rules_recorded.json` — option (a)'s deep pins.
- `docs/runbooks/disaster-recovery-restore.md` — the restore exercise this ADR's operational rule triggers.
- [ADR-012](ADR-012-dbas-absorption-approach.md) — DBAS absorption; the restore path is what makes 27 of these templates critical.
- Beads `enhancedchannelmanager-q6xjl`, `enhancedchannelmanager-lsa0s`, `enhancedchannelmanager-r9oqx` — the evidence.
