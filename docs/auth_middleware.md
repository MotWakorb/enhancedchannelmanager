# Global Auth Middleware

All /api/* endpoints are secure-by-default via middleware; new endpoints must be added to AUTH_EXEMPT_PATHS to be public.

ECM uses a global auth middleware in `main.py` that blocks unauthenticated requests to all `/api/*` paths unless explicitly exempted.

**Why:** Before this, auth was per-endpoint via DI dependencies. Most routers had no auth at all — new endpoints were silently public. The middleware makes the default secure.

**How to apply:**
- New endpoints are automatically protected — no auth dependency needed
- To make an endpoint public, add its path to `AUTH_EXEMPT_PATHS` in `main.py`
- The middleware respects `RequireAuthIfEnabled` semantics: skips enforcement when `auth.require_auth=False` or `auth.setup_complete=False`
- Token validation uses `decode_token_safe()` from `auth/dependencies.py` (non-raising, returns payload or None)
- Per-endpoint `RequireAuthIfEnabled` / `RequireAdminIfEnabled` DI dependencies still exist for role-based checks (e.g., admin-only routes in `backup.py`)

## Known limitation: BaseException containment can't cover outer middleware bodies

`BaseExceptionContainmentMiddleware` (`backend/main.py:205`) is registered
**first**, which under Starlette's `add_middleware` (later registration =
more outer) makes it the **innermost** user middleware — wrapped directly
around the router, inside the same asyncio task that runs route handlers and
their dependencies. That position is what lets it catch a
`SystemExit`/`KeyboardInterrupt` raised by handler code before
`asyncio.Task.__step` re-raises it out of the event loop and silently kills
the process with `ExitCode 0` (see `exit_diagnostics.py` for the full
mechanism).

It structurally **cannot** cover the bodies of the `@app.middleware("http")`
functions registered outside it — including `auth_middleware` itself
(`backend/main.py:525-547`), where `decode_token_safe` runs on the exact
concurrent-cookie path from the original GH #546 repro. Each outer
`BaseHTTPMiddleware`-style middleware body executes in its own task, outside
the guard's task boundary; no registration order can bring an outer
middleware body inside a guard that only wraps what's nested beneath it.

This is a known, accepted structural ceiling of Starlette's
`BaseHTTPMiddleware` model — not a defect in the containment fix. Closing it
would require restructuring the outer middlewares (auth, CORS, etc.) as pure
ASGI middleware so the guard could wrap them too; that hasn't been done,
since no field occurrence has been observed (no `[EXIT-DIAG]` recurrence as
of 2026-07-26, and a one-time audit of the outer middleware bodies found no
`BaseException` sources).

**If this ever fires:** a `BaseException` raised inside an outer middleware
body will still kill the process with `ExitCode 0` the way the pre-fix bug
did, but the containment guard will not see it — no `[EXIT-DIAG]`/atexit
diagnostic will be logged, so the *absence* of `[EXIT-DIAG]` output does not
mean the containment fix failed. Symptoms to look for: a process exit with
no traceback and no `[EXIT-DIAG]` entry, on a request that passed through
`auth_middleware` or another outer `@app.middleware("http")` function rather
than a route handler. Tracked in bead `enhancedchannelmanager-17v07`; attach
any recurrence there so the raiser can be identified.
