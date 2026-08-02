# Global Auth Middleware

All /api/* endpoints are secure-by-default via middleware; new endpoints must be added to AUTH_EXEMPT_PATHS to be public.

ECM uses a global auth middleware in `main.py` that blocks unauthenticated requests to all `/api/*` paths unless explicitly exempted.

**Why:** Before this, auth was per-endpoint via DI dependencies. Most routers had no auth at all. New endpoints were silently public. The middleware makes the default secure.

**How to apply:**
- New endpoints are automatically protected: no auth dependency needed
- To make an endpoint public, add its path to `AUTH_EXEMPT_PATHS` in `main.py`
- The middleware respects `RequireAuthIfEnabled` semantics: skips enforcement when `auth.require_auth=False` or `auth.setup_complete=False`
- Token validation uses `decode_token_safe()` from `auth/dependencies.py` (non-raising, returns payload or None)
- Per-endpoint `RequireAuthIfEnabled` / `RequireAdminIfEnabled` DI dependencies still exist for role-based checks (e.g., admin-only routes in `backup.py`)

## Known limitation: BaseException containment can't cover outer middleware bodies

`BaseExceptionContainmentMiddleware` (`backend/main.py:205`) is registered
**first**, which under Starlette's `add_middleware` (later registration =
more outer) makes it the **innermost** user middleware: wrapped directly
around the router, inside the same asyncio task that runs route handlers and
their dependencies. That position is what lets it catch a
`SystemExit`/`KeyboardInterrupt` raised by handler code before
`asyncio.Task.__step` re-raises it out of the event loop and silently kills
the process with `ExitCode 0` (see `exit_diagnostics.py` for the full
mechanism).

It structurally **cannot** cover the bodies of the `@app.middleware("http")`
functions registered outside it, including `auth_middleware` itself
(`backend/main.py:525-547`), where `decode_token_safe` runs on the exact
concurrent-cookie path from the original GH #546 repro. Each outer
`BaseHTTPMiddleware`-style middleware body executes in its own task, outside
the guard's task boundary; no registration order can bring an outer
middleware body inside a guard that only wraps what's nested beneath it.

This is a known, accepted structural ceiling of Starlette's
`BaseHTTPMiddleware` model, not a defect in the containment fix. Closing it
is a middleware-stack/order redesign, not a single-line move: the
task-boundary (`BaseHTTPMiddleware`) layers would need to be removed, and
containment placed or restructured so it actually wraps the bodies of the
middlewares that currently sit outside it, with handler containment
revalidated afterward. That hasn't been done, since no field occurrence has
been observed (no confirmed recurrence as of 2026-07-26, and a one-time
audit of the outer middleware bodies found no `BaseException` sources).

**If this ever fires:** a `BaseException` raised inside an outer middleware
body will still kill the process with `ExitCode 0` the way the pre-fix bug
did, and the atexit `[EXIT-DIAG]` line from `exit_diagnostics.log_atexit()`
(installed process-wide, independent of this middleware) **will still be
logged**. Atexit hooks run on this normal-shutdown path, and only
`os._exit()`/a hard signal would suppress it. What will be **absent** is a
`[EXIT-DIAG]` CRITICAL traceback immediately above that atexit line: the
containment middleware's own critical log only fires when it is the one that
catches the exception, and a `SystemExit`-class exception never reaches
`sys.excepthook` (so `log_uncaught_exception` doesn't fire for it either).
Concretely, this is `exit_diagnostics.py`'s own documented "atexit line, no
exception logged above it" `SystemExit` signature. An outer-middleware
escape produces exactly that pattern in `docker logs`. Symptoms to look for:
an `[EXIT-DIAG]` atexit line with no CRITICAL traceback directly above it, on
a request that passed through `auth_middleware` or another outer
`@app.middleware("http")` function rather than a route handler. Tracked in
bead `enhancedchannelmanager-17v07`; attach any recurrence there so the
raiser can be identified.
