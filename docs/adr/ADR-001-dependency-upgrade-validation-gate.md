# ADR-001: Dependency Upgrade Validation Gate

- **Status**: Accepted
- **Date**: 2026-04-20 (proposed) / 2026-04-20 (accepted) / 2026-04-23 (scope clarification amended — see Scope below)
- **Author**: IT Architect persona (on behalf of PO)
- **Bead**: `enhancedchannelmanager-xnqgo` (blocks epic `bd-6rrl5`)
- **Amendment Bead**: `enhancedchannelmanager-umaop` (2026-04-23 scope clarification)
- **Related**:
  - `enhancedchannelmanager-tp681` — flake baseline (complementary, not a substitute)
  - `enhancedchannelmanager-sm3n3` — ADR-005 (code security gating; see Scope below for the boundary)
  - `enhancedchannelmanager-4lk1q` — ADR-004 (release-cut promotion discipline; complementary backstop)

## Scope

This ADR governs **dependency upgrades only**. It is not a general code-security gating ADR.

**In scope (ADR-001 governs):**
- Cadence policy for major dependency bumps (one major bump per PR, ~1 per 7-day window — see "Dependency Upgrade Cadence" below).
- The PR-to-`dev` validation gate for dependency upgrades: fresh-image build (`build-amd64`), DAST `/api/health` smoke (`dast-scan`), and Trivy CRITICAL/HIGH CVE scan (`trivy-scan`), triggered via `paths-filter` on dependency manifest files (`backend/requirements*.txt`, `frontend/package*.json`, `mcp-server/requirements.txt`, `Dockerfile*`).
- Acceptance criteria for dep-bump beads under epic `bd-6rrl5`.
- Tagging convention: dep-bump PRs reference ADR-001 in the PR description.

**Out of scope (ADR-001 does NOT govern):**
- **General code-security gating** — CodeQL coverage, delta-zero enforcement on new HIGH/CRITICAL alerts, and required status checks for code scanning are governed by **ADR-005** (`enhancedchannelmanager-sm3n3`). ADR-001's `paths-filter` mechanism deliberately does NOT trigger CodeQL — non-dep-bump PRs (feature PRs, refactors, doc changes) are gated by ADR-005's CodeQL workflow, not by this ADR.
- **Release-cut `dev`→`main` promotion discipline** — governed by **ADR-004** (`enhancedchannelmanager-4lk1q`). ADR-001 only covers PR-to-`dev`.
- **Per-PR feature/refactor validation** — non-dep-bump PRs to `dev` are not subject to ADR-001's fresh-image-build gate (the `paths-filter` ensures this). They run the standard unit/integration test suite plus ADR-005's CodeQL gate.

**Relationship to ADR-005 (the boundary that mattered most in PR #82 confusion):**

ADR-001 and ADR-005 are **independent gates that both apply to dep-bump PRs** — they are not substitutes for one another:

| Gate | Source ADR | Catches | Why both must pass on a dep-bump PR |
|---|---|---|---|
| Fresh-image build + DAST smoke + Trivy | ADR-001 | Runtime regressions from dep changes (transitive resolution skew, missing build deps, ARM64/AMD64 differences, image-boot failures, CVE introductions in dep tree) | A dep bump can pass static analysis and still break the running container. CodeQL cannot see this. |
| CodeQL delta-zero + branch-protection required check | ADR-005 | New HIGH/CRITICAL static-analysis findings in code (path injection, log injection, etc.) — including findings in vendored or generated code that a dep bump may pull in | A dep bump can boot cleanly and still introduce a new injection sink in transitive code that only static analysis catches. |

The fresh-image-smoke gate (ADR-001) is the larger of the two by wall-clock and infra cost (~10–14 min vs ~2–4 min) because it must catch the broader class of *runtime* regressions that no static-analysis tool can see. The CodeQL gate (ADR-005) is narrower in cost but applies to **all** PRs to `dev`/`main`, not just dep bumps. A dep-bump PR is therefore subject to both.

## Context

The `bd-6rrl5` epic queues seven Phase-2 dependency upgrades (React 19, TypeScript 6, vite 8, jsdom 29, eslint 10, `@dnd-kit/sortable` 10, starlette 1.0 / fastapi 0.137+). Each is a major-version bump with transitive blast radius, and the epic has sat behind this ADR waiting for a validation contract.

**Corrected premise.** The parent bead's problem statement ("no CI registry push yet") is stale. Current CI (`.github/workflows/build.yml`) already provides, **on push to `dev` and `main`**:

1. Fresh multi-arch image build (`build-amd64`, `build-arm64`) from `Dockerfile` with `--no-cache`-equivalent clean requirements install in the `python-builder` stage.
2. Push to `ghcr.io/<repo>:{branch,sha,version}` tags.
3. Fresh-image boot + `/api/health` smoke (`dast-scan` job: `docker run`, 60s ready window, OWASP ZAP baseline).
4. CRITICAL/HIGH CVE gate (`trivy-scan`, `trivy-scan-arm64`, `ignore-unfixed: true`).
5. Backend `pip-audit` + frontend `npm audit --audit-level=high` on every push/PR to `main`.
6. Full BE (`pytest`, e2e-excluded) + FE (`npm run lint`, `typecheck`, `test`) unit+integration suites (`.github/workflows/test.yml`) on every push/PR to `dev` and `main`.

**The actual gap** is narrower and specific to dependency upgrades:

- **Pre-merge, PR-to-`dev`**: fresh-image build, DAST smoke, and Trivy scan do **not** run. Those jobs gate on `pull_request.base_ref == main` or `push` to a protected branch. A dep-bump PR targeting `dev` therefore merges on unit-test green only.
- **Dev iteration**: the CLAUDE.md loop (`docker cp + restart`, `docker exec … uv pip install …`) validates against the *mutated live venv* of `ecm-ecm-1`, not a freshly compiled `python-builder` stage. Silent skew is possible: transitive resolution differences, missing system build deps, cache-bust ordering, ARM64/AMD64 build-tool requirements (e.g. `cryptography` needs Rust on ARM64 per `Dockerfile:21-27`).

So the architectural question is not "should dep upgrades be gated?" — they already are, at merge. The question is **"should dep upgrades be gated before PR merge into `dev`, and how?"**

## Decision

**Adopt Option B: extend the existing fresh-build + DAST + Trivy jobs to run on dep-bump PRs targeting `dev`, triggered by path filters on dependency manifests.**

The mechanism already exists (`build-amd64`, `dast-scan`, `trivy-scan`) — it only needs its trigger expanded from "PR to `main`" to "PR to `main` OR (PR to `dev` with dep-manifest changes)". This is a workflow-file edit, not new infrastructure, and it is the minimum change that eliminates the silent-skew window.

## Alternatives Considered

| Option | Description | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| **A** — Local fresh-build contract | Each dep-bump bead requires engineer to run `docker compose build --no-cache && docker compose up -d && curl /api/health` locally before PR; acceptance criteria references it | No new CI; forces engineer to face fresh-build failures in their own loop; zero CI minutes | Verification is honor-system (no enforcement); slow dev loop (~3–5 min/iter on ARM64 due to Rust compile); different from CI environment (runner OS, cache state) | High — pure docker | $0 infra, +~5 min/upgrade engineer time |
| **B** — Extend CI PR gate to dev-branch dep PRs (**chosen**) | Add `pull_request.branches: [main, dev]` with `paths: [backend/requirements*.txt, frontend/package-lock.json, frontend/package.json, Dockerfile*, mcp-server/requirements.txt]` to `build-amd64`, `dast-scan`, `trivy-scan` triggers; gate merge on green | Uses existing plumbing; objective and enforceable via branch-protection; fresh image is byte-identical to what `push` produces; no engineer overhead | +~5–8 min wall-clock per dep-bump PR; +~1 CI job-hour per bump (GHCR cache-from `scope=amd64` already present); requires branch-protection rule update | High — pure GitHub Actions | Negligible infra; ~$0–1/mo in runner minutes at current volume |
| **C** — Trust CI-at-push gate, document the risk | Accept that dep bumps merge to `dev` on unit-test green, rely on post-merge `build-amd64` + `dast-scan` + Trivy to catch regressions before promotion to `main` | Zero new work; simplest | A dep bump that breaks fresh-build lands on `dev`, blocks subsequent work until reverted; recovery cost > prevention cost; no clean rollback path if multiple bumps land in sequence | High — no change | $0 infra, high incident risk |
| **D** — Staging compose smoke env | Spin up a dedicated staging `docker compose` environment on each PR via self-hosted runner; smoke-test there | Closest to prod runtime; exercises full stack | Self-hosted runner not provisioned (see `bd-2lw25`); operational burden; overkill for dep validation | Medium — self-hosted runner lock-in | ~$20–40/mo runner + ops time |

## Consequences

### Positive
- **Unblocks the full `bd-6rrl5` epic** (7 dep bumps) plus `bd-6rrl5` itself and children `5x6n7, hlcgj, in620, j9xrz, lx1gf, v28b8, zqmv1`. Each bump becomes a self-contained PR with objective merge criteria.
- **Closes the silent-skew window**: CI validates the fresh-image path before merge, matching what `push`-to-`dev` will produce.
- **Cheap to reverse**: the change is a workflow YAML diff; exit path is a single-line revert.
- **No new infrastructure**: stays within GitHub-hosted runners, no vendor lock-in introduced.

### Negative
- **PR-to-dev latency** on dep bumps rises from ~4 min (unit tests only) to ~10–14 min (fresh build + DAST boot + Trivy). Acceptable — dep bumps are infrequent relative to feature PRs.
- **Runner minutes** on dep-bump PRs roughly triple. Current open-source repo uses `ubuntu-latest` free tier; remains well under quota at the expected cadence (one bump per 1–2 weeks per ADR-001 cadence rule below).
- **Branch-protection rule for `dev`** must list the new required checks; small admin action.
- **First bump after this ADR lands** will be the canary — if any of the seven bumps silently broke fresh-build, we find out then. Recommended sequencing: run the cheapest/lowest-blast bump first (jsdom test-env-only) to validate the gate itself.

### Neutral
- Does **not** replace flake baseline (`bd-tp681`). Flake de-duplication is a prerequisite for interpreting the unit-test signal during upgrades; this ADR assumes it lands first or in parallel.
- Does **not** introduce staging or E2E-in-CI (`bd-2lw25` scope).

## Dependency Upgrade Cadence (Policy)

Policy is as important as the gate. Proposed cadence, to be enforced by grooming:

- **One major bump per PR** — no trains. A PR either bumps one package (and its direct peer deps, e.g. `react` + `react-dom` + `@types/react` + `@testing-library/react` together) or nothing. Transitive churn is acceptable.
- **Target cadence**: at most one major bump merged to `dev` per 7-day window, to keep blame bisection cheap if a latent regression appears in integration testing on `dev`.
- **Minor/patch bumps**: Dependabot-style, can bundle; not gated by this ADR.
- **Security advisories**: CRITICAL/HIGH CVE fixes bypass the 7-day spacing rule but still require the full gate (B).

## Application (Acceptance Criteria for `bd-6rrl5` Children)

Each dep-bump bead under `bd-6rrl5` must satisfy, as explicit acceptance:

1. **PR retargets `dev`** (current default).
2. **CI checks green on the PR**:
   - `Backend Tests`, `Frontend Tests` (existing)
   - `Build Docker Image (AMD64)` (newly triggered)
   - `DAST Security Scan` (newly triggered — `/api/health` smoke on fresh image)
   - `Container Security Scan (Trivy)` (newly triggered)
3. **No new `CRITICAL` or `HIGH` unfixed CVEs** introduced (Trivy report delta vs. `dev` tip — enforced by `trivy-scan` failing on new findings).
4. **No new flaky tests introduced**, per `bd-tp681` flake catalog.
5. **Changelog entry** drafted under `yopgt`/Keep-a-Changelog format (deferred until `bd-yopgt` scaffolds the file, at which point retrofit is acceptable).
6. **PR description** lists transitive diff delta (output of `pip-compile --diff` or `npm ls --depth=0` before/after) and notes any runtime-visible behavior changes from the package's release notes.

Items 1–4 are enforceable by branch protection. Items 5–6 are reviewer-verified.

## Implementation Sketch

Workflow changes (scope of a separate engineering bead — out of scope for this ADR):

```yaml
# .github/workflows/build.yml (conceptual diff — not a literal patch)

# build-amd64, dast-scan, trivy-scan:
#   change:
#     if: (event=='push' && ref in [main, dev]) || (event=='pull_request' && base_ref=='main')
#   to:
#     if: (event=='push' && ref in [main, dev])
#         || (event=='pull_request' && base_ref=='main')
#         || (event=='pull_request' && base_ref=='dev' && deps-changed())
#
# where deps-changed() evaluates a dorny/paths-filter step against:
#   - backend/requirements*.txt
#   - backend/requirements.in
#   - frontend/package.json
#   - frontend/package-lock.json
#   - mcp-server/requirements.txt
#   - Dockerfile
#   - mcp-server/Dockerfile
```

Branch-protection update on `dev`: add `Build Docker Image (AMD64)`, `DAST Security Scan`, `Container Security Scan (Trivy)` as required status checks, **conditionally** (GitHub's "required if triggered" semantics — the checks are required when they run, not always). Document in `docs/shipping.md`.

## Exit Path

If Option B proves too slow or unreliable:

1. **Soft exit** — relax the path filter (e.g. exclude `Dockerfile` changes from triggering, keep only `requirements.txt`/`package-lock.json`). One-line workflow edit.
2. **Hard exit** — revert the `pull_request.branches` expansion; fall back to Option C. Workflow revert + branch-protection rule removal. ~15 minutes.

No data migration, no infrastructure teardown, no vendor relationship to unwind.

## Open Questions (Resolved by PO — 2026-04-20)

1. **Required vs. advisory on PR?** **Resolved: required.** New CI checks (`Build Docker Image (AMD64)`, `DAST Security Scan`, `Container Security Scan (Trivy)`) are required status checks on dep-bump PRs targeting `dev`, not advisory.
2. **Apply cadence policy retroactively?** **Resolved: enforce 7-day cadence starting from this acceptance date (2026-04-20).** Parallel drafting is fine; only one major bump merges to `dev` per 7-day window.
3. **Flake baseline sequencing.** **Resolved: `bd-tp681` (flake baseline) merges before the first dep bump.** Dep-bump unit-test signals are not interpretable without a clean flake baseline.
4. **First canary bump.** **Resolved: `bd-lx1gf` (jsdom 24→29) is the canary.** Test-env-only scope, minimal runtime blast radius, exercises the new gate end-to-end before higher-stakes bumps run through it.

## References

- Bead `enhancedchannelmanager-xnqgo` — this ADR's tracker
- Bead `enhancedchannelmanager-6rrl5` — dep-bump epic
- Bead `enhancedchannelmanager-tp681` — flake baseline (companion)
- Bead `enhancedchannelmanager-umaop` — 2026-04-23 scope clarification amendment (this revision)
- `docs/adr/ADR-004-release-cut-promotion-discipline.md` — `dev`→`main` promotion gate (out of scope here)
- `docs/adr/ADR-005-code-security-gating-strategy.md` — CodeQL delta-zero gate (out of scope here; complementary)
- `.github/workflows/build.yml` — existing build/security/scan pipeline
- `.github/workflows/test.yml` — existing unit/integration test pipeline
- `Dockerfile` — multi-stage build, `python-builder` stage compiles deps
- `docs/shipping.md` — release workflow (referenced for branch-protection doc update)
