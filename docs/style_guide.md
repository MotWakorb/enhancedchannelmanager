# ECM Engineering Style Guide

Living document. PR changes welcome — open a PR against this file and tag the
code reviewer (`/code-reviewer`). When a review uncovers a gap, update the
guide.

This guide is the **canonical reference** for ECM coding conventions. It
consolidates rules that previously lived in `CLAUDE.md` (root and
`frontend/`), `docs/css_guidelines.md`, and `docs/frontend_lint.md`. Those
files now defer here for style; they retain only what is genuinely
agent-workflow (read X before doing Y) or operational (deploy steps,
container names) in nature.

## Table of Contents

- [Naming Conventions](#naming-conventions)
  - [Python](#python)
  - [TypeScript / React](#typescript--react)
  - [CSS](#css)
  - [Filenames](#filenames)
- [Module Organization](#module-organization)
  - [Backend (Python)](#backend-python)
  - [Frontend (React)](#frontend-react)
- [Comments and Docstrings](#comments-and-docstrings)
- [Regex](#regex)
  - [Rule](#rule)
  - [Why](#why)
  - [Contract (`safe_regex`)](#contract-safe_regex)
  - [Enforcement chain](#enforcement-chain)
  - [Exceptions](#exceptions)
  - [Operational notes](#operational-notes)
- [Error Handling and Logging](#error-handling-and-logging)
- [CSS Conventions](#css-conventions)
- [Frontend Lint Policy](#frontend-lint-policy)
- [Test Conventions](#test-conventions)

---

## Naming Conventions

### Python

- **Modules / packages**: `snake_case` (`auto_creation_engine.py`, `safe_regex.py`).
- **Functions / methods / variables**: `snake_case`.
- **Classes**: `PascalCase` (`StreamNormalizer`, `AutoCreationRule`).
- **Constants**: `UPPER_SNAKE_CASE` at module top-of-file.
- **Module-private symbols**: leading underscore (`_DISCORD_WEBHOOK_RE`,
  `_compile_pattern`). Underscore prefix is the project's signal that the
  symbol is not part of the module's public API.
- **Pre-compiled regex constants**: `_NAME_RE` suffix, module-level, compiled
  once at import. See [Regex](#regex) below — this is a hard rule, not a
  preference. Examples in `backend/routers/settings.py`,
  `backend/epg_matching.py`, `backend/stream_normalization.py`.
- **Test functions**: `test_<behavior_under_test>` — describe the behavior,
  not the method (`test_expired_token_returns_401`, not
  `test_validate_token`). See [Test Conventions](#test-conventions).

### TypeScript / React

- **Components**: `PascalCase` for both the symbol and the file
  (`ChannelsPane`, `ChannelsPane.tsx`).
- **Hooks**: `camelCase` with `use` prefix (`useEditMode`, `useChangeHistory`,
  `useAsyncOperation`).
- **Utilities, services, helpers**: `camelCase` for functions, `PascalCase`
  for types/interfaces/classes.
- **Type aliases / interfaces**: `PascalCase`. Props interfaces follow the
  `[Component]Props` pattern (e.g. `ChannelsPaneProps`).
- **Request/response types**: `<Resource>CreateRequest`,
  `<Resource>UpdateRequest`, `<Resource>Response` (e.g. `ChannelCreateRequest`).
- **Exports**: prefer named exports over default exports — they survive
  refactors better and surface in autocomplete consistently.
- **Tab IDs**: kebab-case string literals on the `TabId` union
  (`'channel-manager'`, `'auto-creation'`).

### CSS

- **BEM-inspired**, dash-separated: `.component-name`,
  `.component-name-child`, `.component-name-item`.
- **State classes**: `is-` prefix where adopting from scratch
  (`.is-active`, `.is-disabled`, `.is-loading`). Existing legacy state
  classes without the prefix (`.active`, `.filter-active`) are acknowledged
  but not preferred for new code.
- **CSS custom properties**: `--<group>-<role>` in `kebab-case`
  (`--bg-primary`, `--text-secondary`, `--accent-50`, `--button-primary-bg`).
  Group prefix groups by purpose: `bg-`, `text-`, `accent-`, `border-`,
  `button-`, `input-`.
- See [CSS Conventions](#css-conventions) for the full token contract and
  the BEM/state-class rationale.

### Filenames

- **Python**: `snake_case.py`. Test files mirror the module under test:
  `backend/foo.py` → `tests/test_foo.py`.
- **React component triple**: `ComponentName.tsx` + `ComponentName.css` +
  `ComponentName.test.tsx`. The CSS and test files live next to the
  component file. This is the project's "component pairing" convention —
  enforced by code review, not by tooling.
- **Modal components**: suffix with `Modal` — `DeleteOrphanedGroupsModal.tsx`,
  `BulkLCNFetchModal.tsx`.
- **Hook files**: `use<Behavior>.ts` (no `.tsx` unless the hook returns JSX,
  which is rare).
- **Markdown docs**: `snake_case.md` under `docs/`.

---

## Module Organization

### Backend (Python)

Top-level layout (see `docs/backend_architecture.md` for the full
architectural contract — the layout below is the style/structure rule):

```
backend/
├── main.py                    # FastAPI app factory + middleware wiring
├── database.py                # SQLAlchemy session / engine
├── routers/                   # FastAPI APIRouter modules, one per domain
│   ├── channels.py
│   ├── epg.py
│   └── ...
├── auto_creation/             # Domain package — engine, schema, types
├── safe_regex.py              # Cross-cutting utility
├── regex_lint.py              # Cross-cutting utility
└── tests/                     # mirrored tree under backend/tests/
```

Conventions:

- **One router per domain.** Routers live in `backend/routers/<domain>.py`
  and expose a single `router = APIRouter(...)` symbol that `main.py`
  mounts. Do not scatter routes across helper modules.
- **Domain logic separates from transport.** Business rules live in
  `<domain>/` packages or top-level modules; routers are thin wrappers that
  do request validation, call into the domain layer, and shape the
  response. Routers should not contain regex matching, normalization
  logic, or DB queries beyond simple CRUD.
- **Cross-cutting utilities at top level.** `safe_regex`, `regex_lint`,
  `task_registry`, `cron_parser` are not nested under a domain — they're
  used everywhere.
- **Imports**: stdlib → third-party → local, blank line between groups.
  Enforced by Ruff (see [Frontend Lint Policy](#frontend-lint-policy) for
  the equivalent on the frontend side).

### Frontend (React)

```
frontend/src/
├── App.tsx                    # Centralized state via useState hooks
├── TabNavigation.tsx
├── main.tsx                   # Entry point (AuthProvider → ProtectedRoute → App)
├── index.css                  # CSS variables / theme
├── components/                # ~60+ components
│   ├── tabs/                  # Tab-content components
│   ├── autoCreation/          # Domain subfolder
│   ├── ffmpegBuilder/
│   ├── settings/
│   └── *.tsx + *.css + *.test.tsx
├── contexts/                  # React Context providers
├── hooks/                     # Custom hooks
├── services/                  # API client layer (api.ts, httpClient.ts)
├── types/                     # TypeScript definitions
└── utils/                     # Helpers
```

Conventions:

- **Component pairing.** Every component is a triple:
  `ComponentName.tsx` + `ComponentName.css` + `ComponentName.test.tsx`.
  See [Filenames](#filenames). If the component has no tests yet, that's a
  gap to file, not a license to skip the pairing rule for new components.
- **Domain folders under `components/`.** When a feature grows past two or
  three files, group them into a folder
  (`components/autoCreation/RuleBuilder.tsx`, etc.). Don't deepen the tree
  past two levels without discussion.
- **Tab content is lazy-loaded.** Tab components use `React.lazy()` +
  `Suspense`. Top-level tab loading uses `.tab-loading` from `App.css` for
  visual consistency — see [CSS Conventions](#css-conventions).
- **No CSS modules, no styled-components.** Plain CSS files, scoped by
  class naming.
- **API layer is named exports.** `services/api.ts` exposes one named
  function per endpoint (`getChannels`, `getEPGSources`). All HTTP calls
  go through `fetchJson()` from `httpClient.ts` — do not call `fetch`
  directly from components or services.
- **State management.** No Redux. State is centralized in `App.tsx` via
  `useState`, lifted into Context for cross-cutting concerns
  (`AuthContext`, `NotificationContext`), and decomposed into custom hooks
  for complex per-feature logic (`useEditMode`, `useChangeHistory`).
- **Dropdowns use `CustomSelect`.** Never use the native `<select>`
  element — it doesn't theme correctly under the dark/light token system.
- **Icons use Material Icons spans.**
  `<span className="material-icons">icon_name</span>`. The font is loaded
  globally; do not reach for an icon library on a per-component basis.

---

## Comments and Docstrings

The standard: **comments explain why, not what.** The code says what.
Comments add the context the code can't carry.

**Write a comment when:**

- The decision is non-obvious from the code alone — "we use this fallback
  because the upstream API returns `null` for one specific tenant".
- The pattern violates a default expectation — "this `setState` runs in a
  `.then()` callback, so the lint rule's effect-body warning doesn't
  apply" (see [Frontend Lint Policy](#frontend-lint-policy)).
- The code is intentionally simple in a place where a future reader would
  reach for complexity — "no caching here because the request is hit at
  most once per session".
- A constant's value carries hidden meaning — "100 ms — see Regex
  section, matches `safe_regex.DEFAULT_TIMEOUT_MS`".

**Do not write a comment when:**

- It restates the line below it (`# increment counter` over `counter += 1`).
- It's a stale TODO with no bead reference (file a bead, link it, or
  delete the TODO).
- It's a `# noqa` / `// eslint-disable-next-line` without a one-line
  reason. Disables without rationale are the same as no comment plus a
  lint hole — see [Frontend Lint Policy](#frontend-lint-policy) for the
  required form.

**Docstrings:**

- **Python public functions and classes**: Google-style docstring with
  `Args:`, `Returns:`, `Raises:` sections. Required on anything imported
  outside its own module.
- **Python private helpers (`_name`)**: docstring optional; one-line
  explanation if the name doesn't carry the meaning.
- **TypeScript**: TSDoc / JSDoc comments on exported functions and types
  when the signature alone doesn't convey intent. The type system covers
  most of what a docstring would say in Python.

---

## Regex

### Rule

**User-supplied regex MUST use `backend.safe_regex`, not the stdlib `re`
module.**

A regex is "user-supplied" if the pattern originates from any of:

- A database column (normalization rules, auto-creation rules, dummy-EPG
  profiles, user settings)
- A request body or query parameter
- A configuration file editable by an operator
- A template substitution resolved at runtime
- A user-uploaded file (M3U, DBAS export, etc.)

The stdlib `re` module is **reserved for module-level constants compiled from
hard-coded raw-string literals**. The project convention is:

```python
# Module top-of-file, UPPER_SNAKE_CASE, underscore-prefixed for private.
_CHANNEL_NUMBER_PREFIX_RE = re.compile(r"^\d+\s*\|\s*")
_QUALITY_SUFFIX_RE = re.compile(r"\b(HD|FHD|UHD|SD|4K)\b", re.IGNORECASE)
```

Any other regex site — a pattern built at runtime, one read from the DB, one
assembled from a request body — goes through `safe_regex`.

### Why

The Python stdlib `re` engine has no timeout. A single pathological pattern
(e.g. `(a+)+$` against `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaX`) can pin a CPU
core indefinitely. On an async FastAPI worker running the sync `re.search`
inline, this stalls the entire event loop — one malicious normalization rule
can take the whole service offline.

The third-party `regex` library (PyPI `regex`, not stdlib `re`) accepts a
`timeout=` kwarg that bounds wall-clock runtime between backtracking steps.
`safe_regex` wraps that library with:

- A **100 ms per-call timeout** (`DEFAULT_TIMEOUT_MS`), enforced by the
  `regex` library's backtracking checkpoint.
- A **500-character pattern length cap** (`DEFAULT_MAX_PATTERN_LEN`), enforced
  before compile — catches the "paste-a-novel-into-the-rules-field" shape
  that defeats the per-call timeout.
- **Sentinel returns on timeout** (`None` / original text) rather than raising,
  so the hot path degrades gracefully: a bad rule logs a WARN and falls
  through as "did not match" instead of crashing the request.

The timeout is **best-effort, not preemptive** — the `regex` library checks
the deadline between backtracking steps; a pattern that spends its time in a
single native operation (e.g. a very long literal scan) can exceed the budget.
In practice the budget is effective against the catastrophic-backtracking
shape that dominates the ReDoS threat surface, but callers in code paths with
sub-second external-response requirements must layer an additional ceiling
(request-scoped timeout, circuit breaker) on top.

### Contract (`safe_regex`)

The module lives at `backend/safe_regex.py`. Public API:

| Function | On success | On timeout / oversize | On compile error |
|---|---|---|---|
| `search(pattern, text, *, flags=0, timeout_ms=100, max_pattern_len=500)` | returns `Match` | returns `None`, WARN-logs `[SAFE_REGEX]` | returns `None`, WARN-logs |
| `match(pattern, text, *, flags=0, ...)` | returns `Match` | returns `None` | returns `None` |
| `sub(pattern, repl, text, *, flags=0, ...)` | returns replaced string | returns `text` unchanged | returns `text` unchanged |
| `compile(pattern, *, flags=0, max_pattern_len=500)` | returns compiled `Pattern` | raises `PatternTooLongError` | raises `SafeRegexError` |

Exception hierarchy:

```
SafeRegexError              # Base — catch this for catch-all handling.
├── RegexTimeoutError       # Reserved for a future strict-mode API
│                             (default contract is sentinel-return).
└── PatternTooLongError     # Raised by compile() when len(pattern) > cap.
```

**Pre-compiled patterns are supported.** When the pattern is a compiled
`regex.Pattern` (e.g. cached on a hot path such as the
N log N sort comparisons in `auto_creation_engine`), pass the compiled
object directly:

```python
_CACHED = safe_regex.compile(user_pattern)
safe_regex.search(_CACHED, text)  # goes direct to bound method, skips re-hash
```

**`regex` library timeout raises `builtins.TimeoutError`, not
`regex.error`.** Observed in bd-eio04.5 testing. `safe_regex` callers never
need to catch this — the module's default contract converts the timeout into
a sentinel return and a WARN log. Direct callers of the third-party `regex`
library (should be none — route through `safe_regex`) must catch both
`TimeoutError` and `regex.error` separately.

### Enforcement chain

Three layers defend against ReDoS, each at a different lifecycle stage:

1. **Write-time lint at persistence (bd-eio04.7 — `backend/regex_lint.py`).**
   Normalization-rule, auto-creation-rule, and dummy-EPG router endpoints run
   `lint_pattern()` before committing a pattern. The lint catches three
   shapes:
   - `REGEX_TOO_LONG` — pattern length over the cap.
   - `REGEX_COMPILE_ERROR` — pattern fails to compile.
   - `REGEX_NESTED_QUANTIFIER` — AST walk detects
     nested-unbounded-quantifier-followed-by-killer (the Python `re`
     backtracking ReDoS shape).

   Rejects return HTTP 422 with a structured error envelope pointing back to
   this style-guide section.

2. **Runtime timeout at call (bd-eio04.5 — `backend/safe_regex.py`).**
   Every regex evaluated against user data at serve time goes through
   `safe_regex`. Even if a pattern slips past the write-time lint (older rows,
   bypassed validation, a lint-rule gap), the 100 ms timeout caps the damage
   per call.

3. **CI guard at PR time (bd-eio04.8 — this document, `.semgrep.yml`).**
   The `no-bare-re-on-dynamic-pattern` rule flags new `re.search/match/sub/
   compile/findall/finditer/split/subn/fullmatch` calls whose first argument
   is not a raw-string literal. Exempt idioms:
   - `re.compile(r"…")` module-level constants.
   - `rf"…{re.escape(x)}…"` f-strings — `re.escape` neutralizes the
     interpolation to literal bytes.
   - `r"…" + re.escape(x) + r"…"` concatenation — same reasoning.

   Sites that are safe but don't match those shapes (e.g. multi-line
   `re.compile(\n r"…",\n …)` constants, pre-escaped variables like
   `escaped = re.escape(x); re.compile(rf"…{escaped}…")`) are annotated with
   a same-line `# nosemgrep: no-bare-re-on-dynamic-pattern` comment and a
   justification. Every `nosemgrep` annotation either names why the call is
   safe or links to a follow-up bead to migrate.

   The CI job is a **required status check**; a PR that introduces a bare
   `re.*` on a dynamic pattern fails CI and cannot merge.

### Exceptions

- **Module-level constants from raw-string literals** — use `re.compile(r"…")`.
  No `safe_regex` wrapping needed; the pattern is authored in source and
  cannot be attacker-controlled at runtime. This is the ECM convention,
  documented here to avoid back-and-forth review cycles.
- **`re.escape(x)` inside the pattern** — `re.escape` converts its argument
  into a literal substring, which cannot contain regex metacharacters. The
  interpolation is safe even when `x` originates at runtime. Semgrep's rule
  recognises the `rf"…{re.escape(x)}…"` and `r"…" + re.escape(x) + r"…"`
  idioms and does not flag them.
- **Syntax-only validation at write time** should route through
  `safe_regex.compile` (which raises `SafeRegexError` / `PatternTooLongError`)
  for consistency with the write-time lint. Using bare `re.compile` for
  syntax validation is a lint violation — see follow-up beads
  `enhancedchannelmanager-ltjyx` (auto_creation_schema) and
  `enhancedchannelmanager-3u6p0` (m3u_digest routes).

If an exception is needed that isn't in this list, discuss with the code
reviewer (`/code-reviewer`) before adding a `nosemgrep` annotation.

### Operational notes

- **Log prefix.** `safe_regex` emits `[SAFE_REGEX]` at WARNING on every
  timeout, oversize, or compile error. The WARN payload contains a SHA-256
  of the pattern plus a 50-char excerpt — the full pattern is deliberately
  not logged because patterns carry attacker-controlled text.
- **Dashboards.** The SRE runbook tracks WARN-rate on the `safe_regex`
  logger as an early-warning signal for new ReDoS attempts and
  misconfigured rules (see `docs/sre/` — normalization observability,
  bd-eio04.9).
- **Performance.** `safe_regex` adds roughly 3–5 µs per call over bare `re`
  (the wrapper overhead plus `regex`-library deadline bookkeeping).
  On a hot path — sort comparisons, N-way stream matching — pre-compile
  with `safe_regex.compile` and reuse the compiled object; the module-level
  pattern path avoids the `regex` library's per-call pattern-hash lookup.
- **Frontend.** The frontend enforces the write-time lint before the POST
  hits the backend so the user sees inline errors instead of a 422. The
  backend lint is the source of truth; the frontend check is UX polish
  and may lag in strictness.

---

## Error Handling and Logging

**Catch what you can act on.** Bare `except:` and bare `catch (e)` are
prohibited.

- **Python**: catch the specific exception class. `except Exception:` is
  acceptable only at the outermost handler of a request lifecycle (router
  boundary, background task entry point) where the goal is "log and don't
  crash the worker." In that case, log with `logger.exception(...)` so the
  traceback is captured.
- **TypeScript**: prefer typed errors. When catching, narrow with
  `instanceof` before reading properties. Re-throw if you can't handle —
  swallowing errors silently is a bug, not a style choice.

**Logger usage:**

- Use the module logger: `logger = logging.getLogger(__name__)` at the top
  of each Python module. Do not use `print()` for diagnostics.
- Log levels:
  - `DEBUG` — verbose detail useful when chasing a bug; off in production.
  - `INFO` — lifecycle events (startup, shutdown, scheduled task ran).
  - `WARNING` — degraded but recovered (regex timeout, fallback
    triggered, retry succeeded). The `safe_regex` `[SAFE_REGEX]` prefix
    is the canonical example.
  - `ERROR` — operation failed; the user or upstream caller will see the
    failure.
  - `CRITICAL` — the service is unusable.
- **Tagged log prefixes** (`[SAFE_REGEX]`, `[AUTO_CREATION]`, etc.) are
  the project's convention for filterable subsystem logs. Use a consistent
  bracketed uppercase prefix when introducing a new subsystem worth
  filtering on; document the prefix in the relevant docs/ guide.

**Error envelopes (HTTP):** API errors return a structured JSON envelope —
see `docs/api.md` for the contract. Routers raise `HTTPException` with
domain-meaningful status codes (422 for validation, 404 for not-found, 409
for conflict, 500 only for genuine internal errors).

---

## CSS Conventions

The full CSS architecture, shared-class catalog, modal patterns, and theme
variable rules live in [`docs/css_guidelines.md`](css_guidelines.md). That
document is **authoritative** for CSS — this section summarizes the rules
that intersect with general code style.

**Naming:**

- BEM-inspired, dash-separated: `.component-name`, `.component-name-child`.
- State classes prefer `is-` prefix for new code (`.is-active`,
  `.is-disabled`, `.is-loading`). Legacy unprefixed state classes
  (`.active`, `.filter-active`) are tolerated but not preferred.
- CSS custom properties: `--<group>-<role>` in `kebab-case`.

**Architecture:**

- Five layers, used in order of preference before writing new CSS:
  design tokens (`index.css`) → common (`shared/common.css`) → tab loading
  (`App.css`) → settings (`SettingsTab.css`) → modals (`ModalBase.css`) →
  component (`ComponentName.css`).
- **Golden rule**: never duplicate a style that already exists in
  `common.css`. Reuse the shared class.
- Component CSS files include a header comment listing which shared
  classes they consume — see `docs/css_guidelines.md` for the format.

**Theme variables — critical rules:**

- `--accent-primary` / `--accent-secondary` flip between dark and light
  mode and **must not be used for backgrounds or badge colors**. They
  cause contrast failures.
- Safe-for-background: `--bg-primary`, `--bg-secondary`, `--bg-tertiary`,
  `--input-bg`, `--button-primary-bg`.
- Safe-for-text: `--text-primary`, `--text-secondary`, `--text-muted`,
  `--button-primary-text`.

For the full shared-class inventory (buttons, forms, badges, status
indicators, modal patterns, settings page patterns), the modal size
classes, and the per-component checklist, read
[`docs/css_guidelines.md`](css_guidelines.md). When the two documents
appear to disagree, `docs/css_guidelines.md` wins; please file a PR
against this style guide so they are reconciled.

---

## Frontend Lint Policy

The full lint policy — including the rationale for `--max-warnings 0`, the
common-pattern fix catalog, CI behavior, and per-rule guidance — lives in
[`docs/frontend_lint.md`](frontend_lint.md). That document is
**authoritative** for ESLint policy. The summary below is the contract this
style guide enforces:

- **`npm run lint` must exit clean** — zero errors, zero warnings
  (`--max-warnings 0`). Enforced in CI on every push and PR.
- **Fix the root cause first.** Reach for a disable only after attempting
  a real refactor. Read
  ["You Might Not Need an Effect"](https://react.dev/learn/you-might-not-need-an-effect)
  before disabling a hooks rule.
- **When a disable is genuinely right, explain why inline:**

  ```ts
  // eslint-disable-next-line <rule-name> -- <one-line reason specific to this site>
  ```

  The reason must be specific. "intentional" is not a reason. The same
  rule applies to Python `# noqa` comments — a bare `# noqa` is a code
  review block.

- **Never disable at file scope** unless the entire file is an exception
  (e.g., generated code).
- **Don't disable rules you could configure off.** If a rule is a net
  negative for the codebase, disable it in `eslint.config.js` with a
  comment explaining the tradeoff. Don't sprinkle line-level disables
  across 50 sites.

For specific recurring patterns (`react-hooks/refs`,
`react-hooks/set-state-in-effect`, `react-hooks/exhaustive-deps`,
`react-refresh/only-export-components`, React Compiler "Compilation
Skipped"), read [`docs/frontend_lint.md`](frontend_lint.md) — it has the
full fix catalog with worked examples.

**Backend equivalent:** Ruff is the linter and formatter for Python. The
same "fix the root cause; document any disable" principle applies to
`# noqa` comments.

---

## Test Conventions

The full pytest invocation contract — including the exact command agents
should run and why — lives in
[`docs/pytest_conventions.md`](pytest_conventions.md). That document is
**authoritative** for backend test invocation. Broader testing strategy
(MSW, Vitest, Playwright, fixtures, mocking) lives in
[`docs/testing.md`](testing.md).

**Style rules that apply across both stacks:**

- **Test names describe the behavior being tested**, not the method.
  Good: `test_expired_token_returns_401`. Bad: `test_validate_token`.
- **Arrange-Act-Assert** structure inside the test body. Helper fixtures
  may abstract the arrange step but must not hide the assertion logic.
- **One concept per test.** A test that asserts ten unrelated things
  produces ten unrelated failure modes.
- **Tests assert specific outcomes.** `assert result is not None` is
  not a test. `assert result.status == "active"` is.
- **Tests are independent.** No shared mutable state, no execution-order
  dependencies. Each test sets up and cleans up its own preconditions.
- **No flaky tests.** Fix the root cause (timing, state leakage, external
  dependency) or delete. Skipping indefinitely is the worst option.

**Backend (pytest):**

- Test files mirror the module under test: `backend/foo.py` →
  `tests/test_foo.py`.
- Use the canonical command from `docs/pytest_conventions.md`. Do not
  invent variants.

**Frontend (Vitest + @testing-library/react):**

- Tests colocated with components: `Component.test.tsx` next to
  `Component.tsx`.
- MSW mocks API responses in `src/test/mocks/`.
- Test setup in `src/test/setup.ts` (mocks `matchMedia`, `ResizeObserver`,
  `IntersectionObserver` — do not duplicate these per-test).
