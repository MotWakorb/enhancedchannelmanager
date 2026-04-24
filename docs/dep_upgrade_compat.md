# Frontend Dependency Bump Cross-Compat Spike (bd-6rrl5.4)

- **Status**: Research complete; advisory to bd-6rrl5 epic
- **Date**: 2026-04-23
- **Bead**: `enhancedchannelmanager-6rrl5.4`
- **Scope**: Verify the recommended bump order in `bd-6rrl5` (TS6 → React 19 → Vite 8 + plugin-react 6 → ESLint 10 → jsdom 29 → @dnd-kit 10) interops at every step. Frontend-only; the backend half of `bd-6rrl5` (starlette 1.0 / fastapi 0.137 / uvicorn) is sequenced independently and is not in this spike's scope.
- **Companion**: ADR-001 (`docs/adr/ADR-001-dependency-upgrade-validation-gate.md`) defines the per-bump validation gate. This document defines the per-bump compat constraints upstream of that gate.

## TL;DR

| # | Bump | Verdict | Hard prerequisite | Notes |
|---|------|---------|-------------------|-------|
| 1 | jsdom 24 → 29 | **GO** (canary first per ADR-001) | None | Lowest blast radius; test-env only. Confirmed Node engines `^20.19.0 \|\| ^22.13.0 \|\| >=24` ✓ against `node:20-alpine` (20.20.2) in Dockerfile. |
| 2 | TypeScript 5.9.3 → 6.0.3 | **GO** | jsdom canary green | `typescript-eslint@8.58.2` (already pinned) peer-supports `>=4.8.4 <6.1.0` — TS 6.0.x fits, **TS 6.1 will require a typescript-eslint bump**. ECM's tsconfig already matches every new TS 6.0 default that flipped (strict, ESNext, bundler, react-jsx). No code-level breaking changes expected. |
| 3 | React 19.2.x ecosystem | **GO with batched bumps** | TS 6.0 (peer alignment) | Must be a **single PR** that bumps `react`, `react-dom`, `@types/react`, `@types/react-dom`, AND `@testing-library/react` 14 → **16.3.0+** together. Earlier 16.x (16.0.0–16.2.x) only declares `react ^18.0.0` peer; **16.3.0** widens to `^18.0.0 \|\| ^19.0.0`. Bumping 14→16.0 first then 16.0→16.3 buys nothing — go straight to 16.3+. |
| 4 | Vite 7.3.2 → 8.x + plugin-react 5.1.4 → 6.x | **GO with caveat** | None hard, but order this AFTER React 19 to avoid double-debugging | `vitest@4.1.4` (current pin) peer-supports `vite ^6 \|\| ^7 \|\| ^8` ✓. `@vitejs/plugin-react@6` peer-requires `vite ^8.0.0`, so plugin-react bump is **strictly coupled** to the Vite bump (not separable). Note Vite 8 ships Rolldown by default — see Caveats below. |
| 5 | ESLint 9.39.2 → 10.x | **GO with one prerequisite bump bundled** | Must include `eslint-plugin-react-hooks` 7.0.1 → **7.1.0+** in the same PR | `7.0.1` peer is `^9.0.0` only and **fails** install against ESLint 10. Fix landed in `7.1.0` (peer expanded to include `^10.0.0`); `7.1.1` is the latest patch. `eslint-plugin-react-refresh@0.5.2` already peers `^9 \|\| ^10` ✓. `typescript-eslint@8.58.2` already peers `^10.0.0` ✓. |
| 6 | @dnd-kit/sortable 8 → 10 | **GO** | None | v10 peer is `@dnd-kit/core ^6.3.0`; current `@dnd-kit/core@6.3.1` already satisfies. No `@dnd-kit/core` major bump exists yet. v9→v10 changelog is dependency-update-only; the genuine breaking change (CollisionDetection signature returning `Collision[]`) landed in earlier majors (≤v6). ECM uses default `closestCenter`/`closestCorners` strategies (verified in `ChannelsPane.tsx`, `SubstitutionPairsEditor.tsx`, `ChannelListItem.tsx`) — no custom collision detection, so the historical breaking change does not apply. |

**No required resequencing of the epic's child beads.** The epic's recommended order (`v28b8` → `in620` → `hlcgj` → `5x6n7` → `lx1gf` → `zqmv1`) is **almost** correct; one optimization detailed in §"Sequencing recommendations" below: run jsdom (`lx1gf`) FIRST as the canary per ADR-001 §"Open Questions, item 4" — which the bd-6rrl5 epic order contradicts but ADR-001 already overrides.

**No blocking gotchas where two bumps must land in the same PR**, with one exception: **ESLint 10 + react-hooks plugin 7.1+** must batch (covered in #5 above; small enough not to violate the "one major per PR" cadence rule from ADR-001).

---

## Per-bump details with sources

### 1. jsdom 24.1.3 → 29.0.2 (`bd-lx1gf`)

**Verdict: GO. Run as the canary per ADR-001.**

- Engines: `^20.19.0 || ^22.13.0 || >=24.0.0` ([npm view jsdom@29.0.2](https://www.npmjs.com/package/jsdom)). Dockerfile builder is `node:20-alpine` resolving to v20.20.2 — within range ✓.
- Notable behavior changes that could touch ECM tests:
  - The CSSOM implementation was rebuilt in v29; `getComputedStyle()` results may shift. ECM tests rarely call `getComputedStyle` (none found in `src/**/*.test.tsx` for visual regression). Low risk, but watch for snapshot drift.
  - Promise/TypeError instances now created in the jsdom global, not Node global. Only matters if a test does `instanceof TypeError` against a value crossing the realm boundary — none observed.
  - `navigator.clipboard`, `matchMedia`, `ResizeObserver`, `IntersectionObserver`, `scrollTo` are mocked in `src/test/setup.ts`; jsdom's underlying behavior here is irrelevant because ECM overrides them.
  - `WebSocket` connection-per-origin throttling regressed in v28 (upstream undici bug). Not used in tests; no impact.
- **Source**: [jsdom releases](https://github.com/jsdom/jsdom/releases). The Changelog.md path on GitHub returns 404 for both `master` and `main`; releases page is the authoritative source.

### 2. TypeScript 5.9.3 → 6.0.3 (`bd-v28b8`)

**Verdict: GO.**

- ECM's current `tsconfig.json` is already aligned with every TS 6.0 default flip:

  | TS 6.0 default flip | ECM today | Status |
  |---|---|---|
  | `strict: true` (was false) | `strict: true` | ✓ no-op |
  | `module: esnext` (was commonjs) | `"module": "ESNext"` | ✓ no-op |
  | `target: es2025` (was es5) | `"target": "ES2020"` (explicit) | ✓ no flip — keeps ES2020 |
  | `moduleResolution: bundler` works | already `"bundler"` | ✓ no-op |
  | `jsx: react-jsx` works | already `"react-jsx"` | ✓ no-op |
  | `types` defaults to `[]` (was auto) | not set | ⚠️ minor — see below |

- **`types` field implicit-to-explicit migration risk.** TS 6.0 stops auto-pulling all `node_modules/@types`. ECM does not declare `"types"` in `tsconfig.json` and does not appear to depend on ambient global types from any `@types/*` package other than `@types/react` and `@types/react-dom` (both explicitly imported). Likely a no-op, but the engineer running `bd-v28b8` should run `tsc --noEmit` immediately after the bump and add `"types": ["..."]` only if a global-augment dependency surfaces.
- **Removed options that ECM does not use**: `module: amd|umd|systemjs|none`, `moduleResolution: classic`, `outFile`, `--rulesdir`, `--no-eslintrc`. None present in `tsconfig.json` or `tsconfig.node.json`. ✓
- **typescript-eslint compat**: `typescript-eslint@8.58.2` (current pin) declares `typescript: ">=4.8.4 <6.1.0"` peer ✓. TS 6.0.x fits; **TS 6.1 will require bumping typescript-eslint**. Track but not blocking.
- **Source**: [TypeScript 6.0 release notes](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-6-0.html), [Announcing TypeScript 6.0](https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/), `npm view typescript-eslint@8.58.2 peerDependencies`.

### 3. React 19.x ecosystem (`bd-in620`)

**Verdict: GO. PR must batch react + react-dom + @types/react + @types/react-dom + @testing-library/react 14 → 16.3.0+.**

- **The non-obvious peer-dep landmine.** `@testing-library/react@16.0.0–16.2.x` peer `react: ^18.0.0` only. `16.3.0` widens to `^18.0.0 || ^19.0.0`. Skipping straight to 16.3.0+ is required; the bd-in620 description's `"@testing-library/react 16"` should be tightened to `"@testing-library/react ≥16.3.0"`.
- React 19 breaking changes that touch ECM patterns:

  | Removal | ECM exposure | Action |
  |---|---|---|
  | `ReactDOM.render` removed | `src/main.tsx` already uses `createRoot` (verified) | ✓ none |
  | `findDOMNode` removed | grep returns 0 hits in `frontend/src/` | ✓ none |
  | `defaultProps` on function components removed | grep returns 0 hits in non-test files | ✓ none |
  | `propTypes` silently ignored | not used (TypeScript) | ✓ none |
  | String refs removed | grep returns 0 hits | ✓ none |
  | Errors in render no longer re-thrown | ECM wraps in `ProtectedRoute` / `AuthProvider`; verify no test relies on uncaught throw bubbling | ⚠️ verify during in620 |
  | `act` import path moved (`react-dom/test-utils` → `react`) | grep returns 0 hits in `src/`; tests use `@testing-library/react` `act` re-export | ✓ none |
  | `forwardRef` still works (NOT removed) | dnd-kit internals use it; safe | ✓ none |
  | New JSX transform required | `tsconfig.json` already `"jsx": "react-jsx"` | ✓ none |

- React 19 codemods (`npx codemod@latest react/19/migration-recipe`) and `types-react-codemod@latest preset-19` are available; recommend running both in `bd-in620` even if grep is clean.
- **Source**: [React 19 Upgrade Guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide), [@testing-library/react releases](https://github.com/testing-library/react-testing-library/releases), `npm view @testing-library/react@16.3.0 peerDependencies`.

### 4. Vite 7.3.2 → 8.x + @vitejs/plugin-react 5.1.4 → 6.x (`bd-hlcgj`)

**Verdict: GO with three caveats.**

- **The bumps are strictly coupled.** `@vitejs/plugin-react@6.x` peer-requires `vite: ^8.0.0` ([npm view @vitejs/plugin-react@6.0.1 peerDependencies](https://www.npmjs.com/package/@vitejs/plugin-react)). Cannot bump plugin-react without bumping Vite first; cannot keep plugin-react v5 with Vite 8 (Vite blog says "v5 still works with Vite 8" but that contradicts the published peer — treat as "may work, not officially supported"; do the joint bump as the bead describes).
- **Caveat 1 — Rolldown is the new default bundler.** Vite 8 replaces esbuild+Rollup with the Rust-based Rolldown. Vite blog suggests gradual migration via the `rolldown-vite` package on Vite 7 first to isolate Rolldown-specific issues from other Vite 8 changes. **Recommended for ECM**: skip the rolldown-vite intermediate (low complexity build, no custom rollup plugins observed in `vite.config.ts`) but be ready to bisect if the build output regresses.
- **Caveat 2 — config option renames.** `build.rollupOptions` → `build.rolldownOptions`, `worker.rollupOptions` → `worker.rolldownOptions`, `optimizeDeps.esbuildOptions` → `optimizeDeps.rolldownOptions`. ECM's `vite.config.ts` uses **none** of these (verified — the file is 23 lines, only `plugins`, `server.port/proxy/fs`, `build.outDir/emptyOutDir`). ✓ no-op for ECM.
- **Caveat 3 — Babel removal in plugin-react v6.** v6 drops Babel as a direct dependency; React Refresh transform now runs through Oxc. ECM does not use custom Babel plugins (verified — no `.babelrc`, no Babel config in `vite.config.ts`). ✓ no action needed. If React Compiler were ever wanted, `reactCompilerPreset` + `@rolldown/plugin-babel` is the new path.
- **vitest peer compat**: `vitest@4.1.4` peer is `vite ^6.0.0 || ^7.0.0 || ^8.0.0` ✓ — vitest is **already** Vite-8-ready and does not need to be bumped as part of this child.
- **Engines**: Vite 8 needs `^20.19.0 || >=22.12.0`. Dockerfile `node:20-alpine` (20.20.2) ✓.
- **Default browser target shifts** (Chrome 107→111, Firefox 104→114, Safari 16.0→16.4). ECM has no documented browser-support floor; risk is low.
- **Source**: [Vite 8 announcement](https://vite.dev/blog/announcing-vite8), [Vite migration guide](https://vite.dev/guide/migration), [@vitejs/plugin-react CHANGELOG](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/CHANGELOG.md), `npm view vite@8.0.1 engines peerDependencies`, `npm view @vitejs/plugin-react@6.0.1 peerDependencies`, `npm view vitest@4.1.4 peerDependencies`.

### 5. ESLint 9.39.2 → 10.x (`bd-5x6n7`)

**Verdict: GO. PR must batch `eslint` + `eslint-plugin-react-hooks` 7.0.1 → 7.1.0+ (or 7.1.1).**

- **The blocking peer-dep gotcha.** `eslint-plugin-react-hooks@7.0.1` (currently pinned) declares peer `eslint: ^3.0.0 || ^4.0.0 || ... || ^9.0.0` — no `^10.0.0`. Installing ESLint 10 against this combination produces an unmet peer dep, which `npm install` may surface as a warning and which `npm audit` / strict installers treat as an error. The fix landed in `react-hooks@7.1.0` (peer expanded to include `^10.0.0`); latest is `7.1.1`. Both 7.1.0 and 7.1.1 are published to npm and verified via `npm view eslint-plugin-react-hooks@7.1.1 peerDependencies`.
- **Other plugin compat (already satisfied):**
  - `typescript-eslint@8.58.2` peer: `eslint: ^8.57.0 || ^9.0.0 || ^10.0.0` ✓
  - `eslint-plugin-react-refresh@0.5.2` peer: `eslint: ^9 || ^10` ✓
  - `@eslint/js@10` is the matching `eslint:recommended` source ✓
  - `globals@17.5.0` is current and unchanged across the bump ✓
- **Flat config already in use.** ECM's `eslint.config.mjs` uses `defineConfig([...])` flat-config form. ESLint 10 removes legacy `.eslintrc` entirely — ECM is unaffected.
- **Behavior change**: ESLint 10 locates `eslint.config.*` from each linted file's directory rather than CWD. ECM has only one config at `frontend/eslint.config.mjs` and lints from `frontend/`, so single-config monorepos are unaffected. ✓
- **`eslint:recommended` rule additions in v10** may flag new issues; the engineer running `bd-5x6n7` should expect to fix or `eslint-disable`-with-comment some new findings on first run.
- **Engines**: `^20.19.0 || ^22.13.0 || >=24` ✓.
- **Source**: [ESLint v10 migration guide](https://eslint.org/docs/latest/use/migrate-to-10.0.0), [ESLint v10 release blog](https://eslint.org/blog/2026/02/eslint-v10.0.0-released/), [eslint-plugin-react-hooks ESLint 10 PR #35720](https://github.com/facebook/react/pull/35720), [eslint-plugin-react-hooks ESLint 10 issue #35758](https://github.com/facebook/react/issues/35758), `npm view eslint-plugin-react-hooks@7.1.1 peerDependencies`.

### 6. @dnd-kit/sortable 8.0.0 → 10.0.0 (`bd-zqmv1`)

**Verdict: GO.**

- v10 peer: `@dnd-kit/core ^6.3.0`; current `@dnd-kit/core@6.3.1` ✓ — **no joint bump of `@dnd-kit/core` needed**, contradicting the bd-zqmv1 description's "may need joint bump" hedge.
- v9→v10 changelog is **only a dependency bump to `@dnd-kit/core@6.3.0`** — no API changes.
- v8→v9 changelog is **only a `id === 0` bug fix** — no API changes.
- The `CollisionDetection` interface refactor (return `Collision[]` instead of `UniqueIdentifier`, take `{active, collisionRect, droppableContainers}`) that the search results highlighted **landed in earlier majors (≤v6)**; ECM is already on v8 so this breaking change is already absorbed.
- ECM's dnd-kit usage (`ChannelsPane.tsx`, `SubstitutionPairsEditor.tsx`, `ChannelListItem.tsx`) uses default collision strategies — no custom `CollisionDetection` functions to update.
- **Source**: [@dnd-kit/sortable CHANGELOG](https://github.com/clauderic/dnd-kit/blob/master/packages/sortable/CHANGELOG.md), `npm view @dnd-kit/sortable@10.0.0 peerDependencies`.

---

## Sequencing recommendations

The bd-6rrl5 epic description proposes:
> **TS6 → React 19 → vite 8 + plugin-react 6 → eslint 10 → jsdom 29 → @dnd-kit 10**

ADR-001 §"Open Questions, item 4" already overrides this:
> **Resolved: bd-lx1gf (jsdom 24→29) is the canary. Test-env-only scope, minimal runtime blast radius.**

This spike confirms the ADR-001 override is correct: jsdom is the safest first move because failures are confined to the test runner, and `bd-lx1gf` exercises the new ADR-001 fresh-build CI gate end-to-end before higher-stakes bumps run through it.

**Recommended sequence for the frontend half of bd-6rrl5:**

| # | Bead | Bump | Rationale |
|---|------|------|-----------|
| 1 | `bd-lx1gf` | jsdom 24 → 29 | Canary per ADR-001. Test-env only. Validates the new CI gate. |
| 2 | `bd-v28b8` | TypeScript 5.9 → 6.0 | Foundation — every later bump's `.d.ts` will be type-checked under TS 6. Aligned tsconfig means low compile-error blast. |
| 3 | `bd-in620` | React 19 ecosystem (single PR: react + react-dom + @types/react + @types/react-dom + **@testing-library/react ≥16.3.0**) | After TS 6 so `@types/react@19` resolves cleanly under the new compiler. |
| 4 | `bd-hlcgj` | Vite 7→8 + plugin-react 5→6 (single PR; strictly coupled) | Tooling bump. After React 19 so any RSC-style import-map gotchas show up against an already-bumped React. |
| 5 | `bd-5x6n7` | ESLint 9→10 (single PR: eslint + **eslint-plugin-react-hooks 7.0.1→7.1.1**) | Independent of runtime; can technically run earlier, but its `eslint:recommended` v10 churn is best deferred until codebase has settled into React 19 + Vite 8 idioms. |
| 6 | `bd-zqmv1` | @dnd-kit/sortable 8 → 10 | Last because manual smoke for drag-reorder UX is the slowest gate. |

The `bd-6rrl5.3` baseline (`docs/dep_upgrade_baseline.md`) must land before #2; it's already an explicit dependency on `bd-v28b8`, `bd-6rrl5.1`, `bd-6rrl5.2`. ✓

The bd-zqmv1 → bd-lx1gf dependency edge in beads is currently inverted vs. this recommended order. Either accept that edge as-is (lx1gf runs first, satisfying it) or remove it during the next grooming pass — no functional issue either way.

## Cadence implication for ADR-001

ADR-001 caps merges at **one major bump per 7-day window**. With 6 frontend bumps + 3 backend bumps in `bd-6rrl5`, that's a **~9-week minimum** wall-clock for the epic at the cadence rule's tip. The PO may wish to re-confirm this cadence is acceptable for v0.16.0's release window, or carve out a documented exception (e.g. "consecutive low-blast bumps may merge in successive days if the prior CI gate is fully green").

## Required acceptance-criteria additions for child beads

Per this spike, the following should be added to the corresponding child bead descriptions before they enter "ready":

- **bd-in620** (React 19): tighten `"@testing-library/react 16"` to `"@testing-library/react ≥16.3.0"`. Also add: "PR must batch react, react-dom, @types/react, @types/react-dom, @testing-library/react in a single commit set; any one alone leaves an unsatisfied React 19 peer-dep graph."
- **bd-5x6n7** (ESLint 10): add: "PR must include `eslint-plugin-react-hooks` bump from 7.0.1 to 7.1.1 (or current 7.1.x) — 7.0.1's peer-dep range stops at ESLint 9 and will block the install."
- **bd-zqmv1** (@dnd-kit/sortable 10): correct/remove "may need joint bump with @dnd-kit/core" — v10 peer-requires `@dnd-kit/core ^6.3.0` and current `@dnd-kit/core@6.3.1` already satisfies this. No `@dnd-kit/core@7+` exists.
- **bd-v28b8** (TypeScript 6): add forward-watch note: `typescript-eslint@8.58.2` peer caps at `<6.1.0`. **TS 6.1 release will require a typescript-eslint major/minor bump** before that TS bump can land — out of scope for v28b8 but should be noted.

These are advisory-only edits (description tightening); none invalidates the existing per-bead validation gate from ADR-001.

---

## Sources

- [TypeScript 6.0 release notes](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-6-0.html)
- [Announcing TypeScript 6.0 — Microsoft DevBlog](https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/)
- [React 19 Upgrade Guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide)
- [React v19 release post](https://react.dev/blog/2024/12/05/react-19)
- [Vite 8.0 announcement](https://vite.dev/blog/announcing-vite8)
- [Vite v7→v8 migration guide](https://vite.dev/guide/migration)
- [@vitejs/plugin-react CHANGELOG](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/CHANGELOG.md)
- [ESLint v10 migration guide](https://eslint.org/docs/latest/use/migrate-to-10.0.0)
- [ESLint v10.0.0 release blog](https://eslint.org/blog/2026/02/eslint-v10.0.0-released/)
- [eslint-plugin-react-hooks ESLint 10 support PR #35720](https://github.com/facebook/react/pull/35720)
- [eslint-plugin-react-hooks ESLint 10 unmet peer issue #35758](https://github.com/facebook/react/issues/35758)
- [typescript-eslint dependency-versions doc](https://typescript-eslint.io/users/dependency-versions/)
- [jsdom releases](https://github.com/jsdom/jsdom/releases)
- [@dnd-kit/sortable CHANGELOG](https://github.com/clauderic/dnd-kit/blob/master/packages/sortable/CHANGELOG.md)
- [@testing-library/react releases](https://github.com/testing-library/react-testing-library/releases)
- npm registry queries (verified 2026-04-23): `npm view <pkg>@<ver> peerDependencies engines` for `vite@8.0.x`, `@vitejs/plugin-react@6.0.x`, `eslint@10.x`, `jsdom@29.x`, `typescript@6.0.x`, `react@19.x`, `@dnd-kit/sortable@10.0.0`, `@testing-library/react@{16.0.x,16.3.0}`, `eslint-plugin-react-hooks@{7.0.1,7.1.1}`, `eslint-plugin-react-refresh@0.5.2`, `typescript-eslint@8.58.2`, `vitest@4.1.4`
- [ADR-001: Dependency Upgrade Validation Gate](./adr/ADR-001-dependency-upgrade-validation-gate.md) — companion validation contract
- `Dockerfile` — frontend builder pinned at `node:20-alpine` (verified resolves to v20.20.2; ≥20.19 floor for all target bumps ✓)
