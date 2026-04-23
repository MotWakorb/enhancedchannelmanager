# ECM Engineering Style Guide

Living document. PR changes welcome — open a PR against this file and tag the
code reviewer (`/code-reviewer`). When a review uncovers a gap, update the
guide.

## Table of Contents

- [Regex](#regex)
  - [Rule](#rule)
  - [Why](#why)
  - [Contract (`safe_regex`)](#contract-safe_regex)
  - [Enforcement chain](#enforcement-chain)
  - [Exceptions](#exceptions)
  - [Operational notes](#operational-notes)

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
