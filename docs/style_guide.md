# ECM Engineering Style Guide

Living document. PR changes welcome: open a PR against this file and tag the
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
  - [TypeScript / React](#typescript-react)
  - [CSS](#css)
  - [Filenames](#filenames)
- [Module Organization](#module-organization)
  - [Backend (Python)](#backend-python)
  - [Frontend (React)](#frontend-react)
- [Comments and Docstrings](#comments-and-docstrings)
- [Prose Style (Docs and Comments)](#prose-style-docs-and-comments)
- [Regex](#regex)
  - [Rule](#rule)
  - [Why](#why)
  - [Contract (`safe_regex`)](#contract-safe_regex)
  - [Enforcement chain](#enforcement-chain)
  - [Exceptions](#exceptions)
  - [Operational notes](#operational-notes)
- [Error Handling and Logging](#error-handling-and-logging)
- [Shell Scripting](#shell-scripting)
  - [Rule](#rule-1)
  - [Whitelist env-var values that select behavior](#whitelist-env-var-values-that-select-behavior)
  - [Fail closed with a warning, not a crash loop](#fail-closed-with-a-warning-not-a-crash-loop)
  - [Why this matters](#why-this-matters)
  - [Reference](#reference)
- [CSS Conventions](#css-conventions)
- [Frontend Lint Policy](#frontend-lint-policy)
- [Test Conventions](#test-conventions)
  - [Test validity / anti-patterns](#test-validity-anti-patterns)

---

## Naming Conventions

### Python

- **Modules / packages**: `snake_case` (`channel_pipeline_engine.py`, `safe_regex.py`).
- **Functions / methods / variables**: `snake_case`.
- **Classes**: `PascalCase` (`StreamNormalizer`, `ChannelPipelineRule`).
- **Constants**: `UPPER_SNAKE_CASE` at module top-of-file.
- **Module-private symbols**: leading underscore (`_DISCORD_WEBHOOK_RE`,
  `_compile_pattern`). Underscore prefix is the project's signal that the
  symbol is not part of the module's public API.
- **Pre-compiled regex constants**: `_NAME_RE` suffix, module-level, compiled
  once at import. See [Regex](#regex) below. This is a hard rule, not a
  preference. Examples in `backend/routers/settings.py`,
  `backend/epg_matching.py`, `backend/stream_normalization.py`.
- **Test functions**: `test_<behavior_under_test>`. Describe the behavior,
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
- **Exports**: prefer named exports over default exports. They survive
  refactors better and surface in autocomplete consistently.
- **Tab IDs**: kebab-case string literals on the `TabId` union
  (`'channel-manager'`, `'channel-pipeline'`).

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
  component file. This is the project's "component pairing" convention,
  enforced by code review, not by tooling.
- **Modal components**: suffix with `Modal`, for example
  `DeleteOrphanedGroupsModal.tsx`, `BulkLCNFetchModal.tsx`.
- **Hook files**: `use<Behavior>.ts` (no `.tsx` unless the hook returns JSX,
  which is rare).
- **Markdown docs**: `snake_case.md` under `docs/`, with one documented
  exception: files under `docs/user_guide/` use `kebab-case.md` instead
  (`runaway-safety-cap.md`, `debugging-rules.md`, `fuzzy-locals-matching.md`,
  `sort-vs-numbering.md`, `cross-instance-sync.md`,
  `stats-v2-history-cutover.md`, etc.). Every existing file in that subtree
  already follows kebab-case, and `docs/user_guide/README.md` (the
  subtree's own authoring guide) documents it as the convention for new
  articles: "Filename matches the article title in kebab-case." Do not
  rename these files to snake_case or flag them in review. The exception
  is intentional. `index.md` and `README.md` filenames are exempt from
  both conventions (single conventional names with no word-separator to
  judge).

---

## Module Organization

### Backend (Python)

Top-level layout (see `docs/backend_architecture.md` for the full
architectural contract: the layout below is the style/structure rule):

```
backend/
├── main.py                    # FastAPI app factory + middleware wiring
├── database.py                # SQLAlchemy session / engine
├── routers/                   # FastAPI APIRouter modules, one per domain
│   ├── channels.py
│   ├── epg.py
│   └── ...
├── channel_pipeline/          # Domain package — engine, schema, types
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
  `task_registry`, `cron_parser` are not nested under a domain. They're
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
  visual consistency. See [CSS Conventions](#css-conventions).
- **No CSS modules, no styled-components.** Plain CSS files, scoped by
  class naming.
- **API layer is named exports.** `services/api.ts` exposes one named
  function per endpoint (`getChannels`, `getEPGSources`). All HTTP calls
  go through `fetchJson()` from `httpClient.ts`. Do not call `fetch`
  directly from components or services.
- **State management.** No Redux. State is centralized in `App.tsx` via
  `useState`, lifted into Context for cross-cutting concerns
  (`AuthContext`, `NotificationContext`), and decomposed into custom hooks
  for complex per-feature logic (`useEditMode`, `useChangeHistory`).
- **Dropdowns use `CustomSelect`.** Never use the native `<select>`
  element. It doesn't theme correctly under the dark/light token system.
- **Icons use Material Icons spans.**
  `<span className="material-icons">icon_name</span>`. The font is loaded
  globally; do not reach for an icon library on a per-component basis.
- **Never pass `null` as the state argument to `history.replaceState()` on a
  route entry.** Pass `window.history.state` through. `useHashRoute` stores its
  bookkeeping (`ecmRouteIndex`, `ecmRouteEpoch`) in history state, so replacing
  that state with `null` un-numbers the entry the operator is standing on, and
  an unnumbered entry is one the router can no longer rewind to by delta. The
  usual offender is code that only wants to rewrite the hash, which is exactly
  when the state looks irrelevant. It is not: rewriting the hash of an entry is
  not navigating away from it. `StickySectionNav.activate()` nulled it while
  adding `?section=` and silently broke Back/Forward for the Edit Mode exit
  guard (bead `enhancedchannelmanager-6fi7p`). There is **no repo-wide guard for
  this**: `StickySectionNav.test.tsx` asserts `ecmRouteIndex` survives a section
  click, which pins that one call site and nothing else. Passing `{}` drops the
  keys just as effectively as `null`. Check new call sites by hand.

---

## Comments and Docstrings

The standard: **comments explain why, not what.** The code says what.
Comments add the context the code can't carry.

**Write a comment when:**

- The decision is non-obvious from the code alone: "we use this fallback
  because the upstream API returns `null` for one specific tenant".
- The pattern violates a default expectation: "this `setState` runs in a
  `.then()` callback, so the lint rule's effect-body warning doesn't
  apply" (see [Frontend Lint Policy](#frontend-lint-policy)).
- The code is intentionally simple in a place where a future reader would
  reach for complexity: "no caching here because the request is hit at
  most once per session".
- A constant's value carries hidden meaning: "100 ms, matching
  `safe_regex.DEFAULT_TIMEOUT_MS` (see the Regex section)".

**Do not write a comment when:**

- It restates the line below it (`# increment counter` over `counter += 1`).
- It's a stale TODO with no bead reference (file a bead, link it, or
  delete the TODO).
- It's a `# noqa` / `// eslint-disable-next-line` without a one-line
  reason. Disables without rationale are the same as no comment plus a
  lint hole. See [Frontend Lint Policy](#frontend-lint-policy) for the
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

## Prose Style (Docs and Comments)

- **No em-dashes (`—`) in prose.** When you reach for one, rewrite instead
  of substituting an en-dash or double hyphen. Two independent clauses
  become two sentences. An appositive or definition becomes a colon, or
  gets recast as its own clause. A trailing elaboration becomes a new
  sentence. A parenthetical aside becomes commas if short, otherwise
  parentheses. This rule applies to documentation prose and code comments;
  it does not apply to literal content quoted verbatim inside a code
  block, a quoted log line, a quoted UI string, or a filename, URL, or
  command.

#### CI guard

**Automated enforcement covers documentation only.** The code-comment
clause above still stands as a convention, but it is not machine-enforced.
It relies on review, the same as any other style rule the guard doesn't
reach.

`scripts/check_em_dashes.py` runs in CI as a step of the **Operator Docs**
job in `.github/workflows/test.yml`. There is exactly one copy: since bead
`enhancedchannelmanager-5rwzy` that workflow runs on every pull request,
including a Markdown-only one, so the duplicate that used to live in
`docs-only-pass.yml` is gone. It scans Markdown under
`docs/`, plus the top-level `README.md` / `CHANGELOG.md` / `CLAUDE.md`.
Python and TypeScript were in its scan surface at initial rollout; the PO
narrowed the scope to documentation only (bead
`enhancedchannelmanager-3tflw`), so a code comment with an em-dash no
longer fails CI.

**It is a ratchet, not a cliff.** It fails only on lines a PR *adds*. The
pre-existing violations already in the tree are tolerated and reported as a
count, per the "add the guard, defer the cleanup" call on bead
`enhancedchannelmanager-3tflw`. Run `python scripts/check_em_dashes.py --all`
for the full cleanup inventory.

Only U+2014 (`—`) is flagged. En-dashes (`–`) and arrows (`→`) are not
em-dashes and are never flagged. The exemptions above are implemented for
Markdown: fenced code blocks, inline code spans, link destinations, and
URLs are all skipped.

If a line is genuinely quoted content the rule exempts and the scanner
cannot tell, suppress it with `em-dash-ok: <reason>` on the **same line**,
mirroring the `fake-test-ok` convention below. A bare `em-dash-ok` with no
reason will be rejected at code review.

Run it locally before pushing:

```bash
python scripts/check_em_dashes.py                     # vs origin/dev
python scripts/check_em_dashes.py --paths docs/x.md   # one file, in full
```

---

## Regex

### Rule

**User-supplied regex MUST use `backend.safe_regex`, not the stdlib `re`
module.**

A regex is "user-supplied" if the pattern originates from any of:

- A database column (normalization rules, Channel Pipeline rules, dummy-EPG
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

Any other regex site (a pattern built at runtime, one read from the DB, one
assembled from a request body) goes through `safe_regex`.

### Why

The Python stdlib `re` engine has no timeout. A single pathological pattern
(e.g. `(a+)+$` against `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaX`) can pin a CPU
core indefinitely. On an async FastAPI worker running the sync `re.search`
inline, this stalls the entire event loop. One malicious normalization rule
can take the whole service offline.

The third-party `regex` library (PyPI `regex`, not stdlib `re`) accepts a
`timeout=` kwarg that bounds wall-clock runtime between backtracking steps.
`safe_regex` wraps that library with:

- A **100 ms per-call timeout** (`DEFAULT_TIMEOUT_MS`), enforced by the
  `regex` library's backtracking checkpoint.
- A **500-character pattern length cap** (`DEFAULT_MAX_PATTERN_LEN`), enforced
  before compile. This catches the "paste-a-novel-into-the-rules-field" shape
  that defeats the per-call timeout.
- **Sentinel returns on timeout** (`None` / original text) rather than raising,
  so the hot path degrades gracefully: a bad rule logs a WARN and falls
  through as "did not match" instead of crashing the request.

The timeout is **best-effort, not preemptive**: the `regex` library checks
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
N log N sort comparisons in `channel_pipeline_engine`), pass the compiled
object directly:

```python
_CACHED = safe_regex.compile(user_pattern)
safe_regex.search(_CACHED, text)  # goes direct to bound method, skips re-hash
```

**`regex` library timeout raises `builtins.TimeoutError`, not
`regex.error`.** Observed in bd-eio04.5 testing. `safe_regex` callers never
need to catch this. The module's default contract converts the timeout into
a sentinel return and a WARN log. Direct callers of the third-party `regex`
library (ideally none, since callers should route through `safe_regex`) must
catch both `TimeoutError` and `regex.error` separately.

### Enforcement chain

Three layers defend against ReDoS, each at a different lifecycle stage:

1. **Write-time lint at persistence (bd-eio04.7, `backend/regex_lint.py`).**
   Normalization-rule, Channel-Pipeline-rule, and dummy-EPG router endpoints run
   `lint_pattern()` before committing a pattern. The lint catches three
   shapes:
   - `REGEX_TOO_LONG`: pattern length over the cap.
   - `REGEX_COMPILE_ERROR`: pattern fails to compile.
   - `REGEX_NESTED_QUANTIFIER`: AST walk detects
     nested-unbounded-quantifier-followed-by-killer (the Python `re`
     backtracking ReDoS shape).

   Rejects return HTTP 422 with a structured error envelope pointing back to
   this style-guide section.

   **Channel Pipeline date tokens are expanded before this lint compiles
   the pattern** (`backend/date_placeholders.py`, enhancedchannelmanager-qa43j).
   `{date}`, `{date+3d}`, `{date:FORMAT}`, etc. are a documented runtime
   feature (USER_GUIDE.md § "Date Expansion in Regex") that
   `channel_pipeline_evaluator` expands before compiling a condition's
   pattern. The write-time gate must expand the same tokens the same way
   before compiling, or a valid, runtime-supported pattern is rejected as
   invalid raw regex. This expansion applies only to the four Channel
   Pipeline regex condition types (`stream_name_matches`,
   `stream_group_matches`, `tvg_id_matches`, `channel_exists_matching`);
   normalization's `regex` condition type has no date-expansion feature at
   run time and is intentionally excluded.

2. **Runtime timeout at call (bd-eio04.5, `backend/safe_regex.py`).**
   Every regex evaluated against user data at serve time goes through
   `safe_regex`. Even if a pattern slips past the write-time lint (older rows,
   bypassed validation, a lint-rule gap), the 100 ms timeout caps the damage
   per call.

3. **CI guard at PR time (bd-eio04.8, this document, `.semgrep.yml`).**
   The `no-bare-re-on-dynamic-pattern` rule flags new `re.search/match/sub/
   compile/findall/finditer/split/subn/fullmatch` calls whose first argument
   is not a raw-string literal. Exempt idioms:
   - `re.compile(r"…")` module-level constants.
   - `rf"…{re.escape(x)}…"` f-strings: `re.escape` neutralizes the
     interpolation to literal bytes.
   - `r"…" + re.escape(x) + r"…"` concatenation: same reasoning.

   Sites that are safe but don't match those shapes (e.g. multi-line
   `re.compile(\n r"…",\n …)` constants, pre-escaped variables like
   `escaped = re.escape(x); re.compile(rf"…{escaped}…")`) are annotated with
   a same-line `# nosemgrep: no-bare-re-on-dynamic-pattern` comment and a
   justification. Every `nosemgrep` annotation either names why the call is
   safe or links to a follow-up bead to migrate.

   The CI job is a **required status check**; a PR that introduces a bare
   `re.*` on a dynamic pattern fails CI and cannot merge.

### Exceptions

- **Module-level constants from raw-string literals**: use `re.compile(r"…")`.
  No `safe_regex` wrapping needed; the pattern is authored in source and
  cannot be attacker-controlled at runtime. This is the ECM convention,
  documented here to avoid back-and-forth review cycles.
- **`re.escape(x)` inside the pattern**: `re.escape` converts its argument
  into a literal substring, which cannot contain regex metacharacters. The
  interpolation is safe even when `x` originates at runtime. Semgrep's rule
  recognises the `rf"…{re.escape(x)}…"` and `r"…" + re.escape(x) + r"…"`
  idioms and does not flag them.
- **Syntax-only validation at write time** should route through
  `safe_regex.compile` (which raises `SafeRegexError` / `PatternTooLongError`)
  for consistency with the write-time lint. Using bare `re.compile` for
  syntax validation is a lint violation. See follow-up beads
  `enhancedchannelmanager-ltjyx` (channel_pipeline_schema) and
  `enhancedchannelmanager-3u6p0` (m3u_digest routes).

If an exception is needed that isn't in this list, discuss with the code
reviewer (`/code-reviewer`) before adding a `nosemgrep` annotation.

### Operational notes

- **Log prefix.** `safe_regex` emits `[SAFE_REGEX]` at WARNING on every
  timeout, oversize, or compile error. The WARN payload contains a SHA-256
  of the pattern plus a 50-char excerpt. The full pattern is deliberately
  not logged because patterns carry attacker-controlled text.
- **Dashboards.** The SRE runbook tracks WARN-rate on the `safe_regex`
  logger as an early-warning signal for new ReDoS attempts and
  misconfigured rules (see `docs/sre/`, normalization observability,
  bd-eio04.9).
- **Performance.** `safe_regex` adds roughly 3–5 µs per call over bare `re`
  (the wrapper overhead plus `regex`-library deadline bookkeeping).
  On a hot path (sort comparisons, N-way stream matching), pre-compile
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
  `instanceof` before reading properties. Re-throw if you can't handle it.
  Swallowing errors silently is a bug, not a style choice.

**Logger usage:**

- Use the module logger: `logger = logging.getLogger(__name__)` at the top
  of each Python module. Do not use `print()` for diagnostics.
- Log levels:
  - `DEBUG`: verbose detail useful when chasing a bug; off in production.
  - `INFO`: lifecycle events (startup, shutdown, scheduled task ran).
  - `WARNING`: degraded but recovered (regex timeout, fallback
    triggered, retry succeeded). The `safe_regex` `[SAFE_REGEX]` prefix
    is the canonical example.
  - `ERROR`: operation failed; the user or upstream caller will see the
    failure.
  - `CRITICAL`: the service is unusable.
- **Tagged log prefixes** (`[SAFE_REGEX]`, `[AUTO-CREATE]`, etc.) are
  the project's convention for filterable subsystem logs. Use a consistent
  bracketed uppercase prefix when introducing a new subsystem worth
  filtering on; document the prefix in the relevant docs/ guide.

**Error envelopes (HTTP):** API errors return a structured JSON envelope.
See `docs/api.md` for the contract. Routers raise `HTTPException` with
domain-meaningful status codes (422 for validation, 404 for not-found, 409
for conflict, 500 only for genuine internal errors).

---

## Shell Scripting

### Rule

**Quote every parameter expansion in `entrypoint.sh` (or any POSIX `sh`
script) that flows into an `exec`'d argv.** This includes expansions built
entirely from the script's own defaults (`${VAR:-default}`). A default
doesn't stop an operator from overriding the value at container-run time
with something containing spaces, globs, or extra words.

```sh
# Good — quoted, cannot be word-split or glob-expanded by the shell
exec gosu appuser uvicorn main:app \
    --port "${ECM_PORT}" \
    --limit-concurrency "${ECM_LIMIT_CONCURRENCY}"

# Bad — unquoted expansion on an exec argv is subject to word-splitting
# and pathname expansion before uvicorn ever sees it
exec gosu appuser uvicorn main:app \
    --port ${ECM_PORT}
```

This unquoted-expansion class has now appeared twice in
`backend/entrypoint.sh`: the `ECM_UVICORN_LOOP` case (fixed; see below) and
the still-open `ECM_PORT` / `ECM_LIMIT_CONCURRENCY` /
`ECM_TIMEOUT_KEEP_ALIVE` expansions on the same exec line
(`enhancedchannelmanager-1xoiq`). Quote a new env-derived argv token even
when the value "looks like it will always be numeric". The type is
enforced by validation, not by the shape of today's default.

### Whitelist env-var values that select behavior

When an environment variable selects *behavior* (a mode, a flag choice,
anything that changes what gets handed to `exec` rather than being opaque
data), validate it against an explicit whitelist before use. `entrypoint.sh`
already does this for `ECM_UVICORN_LOOP`:

```sh
case "${ECM_UVICORN_LOOP:-}" in
    auto|asyncio|uvloop)
        ;;
    *)
        if [ -n "${ECM_UVICORN_LOOP:-}" ]; then
            print_warning "Invalid ECM_UVICORN_LOOP='${ECM_UVICORN_LOOP}' (allowed: auto, asyncio, uvloop) — falling back to asyncio"
        fi
        ECM_UVICORN_LOOP=asyncio
        ;;
esac
```

A `case` statement against literal alternatives is enough: no regex, no
external validator needed. The point is that arbitrary env content never
reaches the exec argv unfiltered, even a value that would otherwise quote
cleanly should still be constrained to the set of values the script
actually knows how to handle.

### Fail closed with a warning, not a crash loop

An invalid whitelisted value must fall back to a safe default and continue.
It must never `exit 1` or let the bad value propagate into a rejection from
the process being exec'd. `entrypoint.sh` runs under `set -e` with an `exec`
at the bottom of the script; a container manager restarts the container on
exit, and the same bad env var is still set on the next attempt. Treating
an invalid value as fatal doesn't fail once. It fails forever, in a
restart loop, until an operator notices and intervenes. Log a
`print_warning` naming the rejected value and the fallback chosen, then
continue on the fallback. The operator gets a diagnosable warning in the
logs instead of a container stuck restarting.

### Why this matters

- **Word-splitting / glob risk.** An unquoted `$VAR` on an argv line is
  subject to the shell's field-splitting (`IFS`) and pathname expansion
  before the target binary ever sees it. A value containing a space
  becomes two argv tokens; a value containing `*` can expand against
  files in the working directory. On an `exec` line this is a path for
  env-var content to inject extra flags into the launched process.
- **Fail-closed vs. crash-loop.** This project's containers restart on
  exit. A validation failure that exits non-zero, or a bad value that
  reaches the launched process and causes it to reject its own argv,
  doesn't degrade once. It degrades on every restart until an operator
  intervenes. Falling back to a known-good default with a logged warning
  keeps the service available and makes the misconfiguration visible
  without downtime.

### Reference

See `backend/entrypoint.sh` for the worked example (the `ECM_UVICORN_LOOP`
case block and its surrounding comment block) and
`enhancedchannelmanager-1xoiq` for the open follow-up applying this rule to
`ECM_PORT`, `ECM_LIMIT_CONCURRENCY`, and `ECM_TIMEOUT_KEEP_ALIVE`.

---

## CSS Conventions

The full CSS architecture, shared-class catalog, modal patterns, and theme
variable rules live in [`docs/css_guidelines.md`](css_guidelines.md). That
document is **authoritative** for CSS. This section summarizes the rules
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
  classes they consume. See `docs/css_guidelines.md` for the format.
- Content-pane text sizes come from the typography role tokens
  (`--type-body-*`, `--type-meta-*`, …) rather than raw numbers. The roles,
  the icon scale, and which pages have been moved onto them so far are in
  [`docs/css_guidelines.md` § Typography](css_guidelines.md#typography).

**Critical rules for theme variables:**

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

The full lint policy (including the rationale for `--max-warnings 0`, the
common-pattern fix catalog, CI behavior, and per-rule guidance) lives in
[`docs/frontend_lint.md`](frontend_lint.md). That document is
**authoritative** for ESLint policy. The summary below is the contract this
style guide enforces:

- **`npm run lint` must exit clean**: zero errors, zero warnings
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
  rule applies to Python `# noqa` comments. A bare `# noqa` is a code
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
Skipped"), read [`docs/frontend_lint.md`](frontend_lint.md). It has the
full fix catalog with worked examples.

**Backend equivalent:** Ruff is the linter and formatter for Python. The
same "fix the root cause; document any disable" principle applies to
`# noqa` comments.

---

## Test Conventions

The full pytest invocation contract (including the exact command agents
should run and why) lives in
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
  `IntersectionObserver`: do not duplicate these per-test).

### Test validity / anti-patterns

*Origin: bead `enhancedchannelmanager-ulp7q` (test-validity audit). The CI
guard `scripts/check_fake_tests.py` flags the most obvious anti-patterns
automatically; this rubric covers the broader class.*

#### The "would it bite?" bar

Before committing a test, delete or invert the code under test in your head.
If the test would still pass, it is not a test. It is a green checkbox that
gives false confidence and hides regressions.

A test must **fail if the logic it claims to verify is removed or inverted**.

#### Banned / red-flag patterns

| Pattern | Why it's fake | Fix |
|---|---|---|
| `assert True` / `expect(true).toBe(true)` | Passes regardless of code. | Assert something the code produced. If you can't yet, use `pytest.skip` / `it.skip`. |
| `assert response is not None` / `expect(response).toBeDefined()` | Any non-crash passes. | Assert `response.status_code`, `response.json()` fields, or the return value. |
| `assert response.status_code == 200` when the endpoint body **is** the behavior | Status-only passes even if the body is empty or wrong. | Assert the body fields that encode the business rule. |
| `assert status_code in (200, 400, 500)` | An always-true disjunction: every HTTP response satisfies this. | Assert the specific code the code path should return. |
| `assert result is not None` / `expect(result).toBeDefined()` when an exact value is available | Passes even when result is corrupted. | Assert the value: `assert result.count == 3`. |
| `assert len(items) >= 1` when the seeded count is known | Passes even if the list has 100 extra ghosts. | Assert `len(items) == N` where N is what you seeded. |
| Asserting the mock you just set | e.g. `mock.return_value = X; assert mock() == X`. The assertion tests `unittest.mock`, not your code. | Assert the downstream effect (what the code **did** with the return value). |
| `expect(x != 422 or "error" not in body).toBe(true)` | One branch is always-true. | Assert the actual expected outcome directly. |
| `expect(container.querySelector('.some-class')).toBeTruthy()` when the class is static boilerplate | Passes even if the feature logic is deleted. | Query for text or state that only appears when the feature works. |
| Bare-substring match against always-present copy | `assert "Error" in text` where "Error" appears in the page title too. | Assert the specific error message the code-under-test emits. |

**Deferred tests must be `skip`, not tautologies.** When a test cannot be
implemented yet, use `@pytest.mark.skip(reason="TODO(<bead-id>): …")` or
`it.skip('…')`. A placeholder that always passes is worse than no test. It
blocks the eventual real test from being added (the suite is already "green").

#### Good pattern

Mock only the **external boundary** (HTTP client, DB session, subprocess).
Assert the code's **own logic**: the transformation it applied, the
validation it enforced, the error it mapped, the exact value it computed.

**Python example**: `test_resets_all_stats` in
`backend/tests/routers/test_settings.py` seeds one row per stats table,
POSTs the reset endpoint, then asserts every table is empty **and** the
response `details` dict names the exact deleted count per table. Removing
any of the seven `db.query(...).delete()` calls fails a specific row-count
assertion. Returning the wrong count in the response fails a specific
`data["details"]["..."] == 1` assertion.

**TypeScript example**: `EditChannelModal.priority.test.tsx` builds 120
EPG entries where the high-priority entry is intentionally last in input
order (so it falls outside an unsorted `slice(0, 100)` window), then
asserts `names[0] === 'ESPN ZZZ-PREFERRED'`. Removing the sort step fails
the first-position assertion. Removing the slice logic fails the
length assertion.

Both tests are small, deterministic, and have zero always-true assertions.
They are the template for new tests in this repo.

#### CI guard

`scripts/check_fake_tests.py` runs in CI (see `.github/workflows/test.yml`)
and fails on the two most unambiguous fake-test markers:

- `assert True` in `backend/tests/` and `mcp-server/tests/`
- `expect(true).toBe(true)` in `frontend/src/**/*.test.{ts,tsx}`

If a site is a genuine intentional tautology (e.g., a third-party assertion
helper that wraps `true`), suppress it with an inline comment on the **same
line**: `# fake-test-ok: <reason>` (Python) or `// fake-test-ok: <reason>`
(TypeScript). The guard skips lines containing `fake-test-ok`.
