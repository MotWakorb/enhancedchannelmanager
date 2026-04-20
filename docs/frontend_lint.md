# Frontend Lint Policy

**Policy:** `npm run lint` must exit clean — zero errors, zero warnings
(`--max-warnings 0`). Enforced in CI via `.github/workflows/test.yml` on every
push and pull request.

## Why `--max-warnings 0`

Warnings have a half-life measured in months. Once the count is non-zero, it
trends up — reviewers stop reading the list because it's too long, and real
signals drown in noise. The only way to keep ESLint useful is to treat every
warning as a failure. If a rule is truly noisy, turn the rule off at the
config level rather than accumulating warnings against it.

Baseline was cleared in bead `enhancedchannelmanager-zjge5` (sessions 1–3),
which fixed ~90 errors and ~90 warnings that had accumulated during ~2 months
of the lint being broken by a stale `ajv` override.

## Rules of the Road

1. **Fix the root cause.** Prefer a real refactor over silencing. A `setState`
   in an effect usually means either the effect shouldn't exist or the state
   should be derived. Read the React docs for
   ["You Might Not Need an Effect"](https://react.dev/learn/you-might-not-need-an-effect)
   before reaching for a disable.

2. **When a disable is genuinely right, explain why inline.** Use the form:

   ```ts
   // eslint-disable-next-line <rule-name> -- <one-line reason specific to this site>
   ```

   The reason must be specific — "intentional" is not a reason. Good:

   ```ts
   // eslint-disable-next-line react-hooks/exhaustive-deps -- polling lifecycle is owned by `probingAll`; parent callback identity doesn't need to restart polling
   ```

   Bad:

   ```ts
   // eslint-disable-next-line react-hooks/exhaustive-deps
   ```

3. **Never disable at file scope** unless the entire file is an exception
   (e.g., generated code). Targeted line-level disables are reviewable.

4. **Don't disable rules you could instead configure off.** If a rule is a
   net negative for the codebase (e.g., `react-refresh/only-export-components`
   for hook/provider co-location patterns), disable it in `eslint.config.js`
   with a comment explaining the tradeoff. Don't sprinkle
   `eslint-disable-next-line` across 50 call sites.

## Common Patterns and Their Fixes

### `react-hooks/refs` — "Cannot access ref value during render"

Reading `ref.current` during render is unsafe because refs aren't tracked by
React's reactivity system — the UI won't update when the ref changes.

- **Fix:** Switch to `useState` when the value drives rendering.
- **Exception:** DOM-measurement patterns (useLayoutEffect measures → writes
  ref → forceUpdate) — this is a known-idiomatic pattern for aligning with
  browser layout. Disable at the specific read site with a reason.

### `react-hooks/set-state-in-effect` — "Avoid calling setState() directly within an effect"

- **Prefer:** Derive state from props with update-during-render guards:

  ```ts
  if (itemIds.length !== pairs.length) {
    setItemIds(resize(itemIds, pairs.length));
  }
  ```

- **For modals with prop-based `isOpen`:** Split into an outer wrapper that
  gates on `isOpen` and an inner that owns the "open-session" state. Each
  open is a fresh mount, so state resets via `useState` initializers rather
  than effects. Example: `DeleteOrphanedGroupsModal.tsx`,
  `BulkLCNFetchModal.tsx`.

- **For data fetching on mount:** The classic `useEffect(() => { void
  loadX(); }, [loadX])` pattern transitively sets state, so the rule will
  flag it. In most cases `setState` fires in a `.then()` callback (async),
  not the effect body — disable the rule at that site with the reason
  `async setState (inside .then), not synchronous`.

### `react-hooks/exhaustive-deps` — missing/unnecessary dep

- Add the missing dep. If adding it would cause a render loop, the callback
  probably shouldn't be in the effect — restructure.
- If the missing value is a stable reference (useCallback/useMemo or a state
  setter), adding it is cheap.
- Context values returned from `useContext(...)` should themselves be
  memoized via `useMemo` in the provider (see `NotificationContext.tsx`) so
  consumers can list them as deps without triggering churn.

### `react-refresh/only-export-components`

Hook + provider co-location (e.g., `NotificationContext.tsx` exporting both
`NotificationProvider` and `useNotifications`) is an established React
pattern; the rule's suggestion to split would cascade imports across the
codebase for a marginal dev-HMR benefit. Disabled at specific sites with a
comment. Consider a file-level ignore if this becomes common.

### React Compiler "Compilation Skipped: Existing memoization could not be preserved"

Compiler-inferred deps didn't match the manual `useCallback` deps. Typically
caused by optional-chain property deps (`displayInfo?.externalUrl`). Fix by
using the whole object (`displayInfo`) — the compiler re-memoizes correctly.

## CI Behavior

`.github/workflows/test.yml` runs `npm run lint` as a blocking step on every
push and every pull request. There is no "informational" mode — a new
warning fails the build, same as a new test failure.
