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

**The exempt set is pinned by a test.** `AUTH_EXEMPT_PATHS` is the entire
authentication gate for `/api/*` — an exact string match, no prefix logic, no
second layer behind it for routers that carry no dependency of their own. Its
contents are snapshotted in
`backend/tests/test_auth_exempt_paths_snapshot.py`, so adding a path requires
editing that file in the same commit. The failure message is the review
checklist. This exists because adding a data route to that set was otherwise a
one-line, silent, total-exposure change that shipped with green CI, and because
`/api/backup/restore-initial` — a full `journal.db` replacement, admin password
hashes included — really was in that set until bead
`enhancedchannelmanager-lf29s`.

Note that middleware exemption is not the same as anonymity: `GET` and `PUT
/api/auth/admin/settings` are exempt from the middleware but carry
`auth.routes.require_admin`, which chains `get_current_user` and validates a
token in every mode. That is pinned too.

## What `require_auth: false` permits

`require_auth: false` is a real, supported ECM operating mode, not a bug state.
Setting it means **the instance serves its API to anyone who can open a socket
to it.** Run it only on a network you trust, and read this section before you
do. (An incomplete first-run setup — `setup_complete: false` — has the same
effect and the same rules; the middleware and every gate below treat the two
conditions identically.)

PO decision, 2026-08-13, bead `enhancedchannelmanager-jy006`.

### What is open

Effectively the whole API. `GET`/`POST /api/settings`, `/api/channels`,
`/api/streams`, `/api/journal`, `POST /api/backup/restore` (a wholesale config
write), `GET /api/backup/create` and `/export` (which emit an archive
containing your stored settings), `POST /api/settings/reset-stats`, the
connection-test endpoints, the cloud- and sync-target CRUD, the alert-method
CRUD, and the two TLS status reads are all reachable **without any credential**
while the mode is on. Assume that anything the API can read, an anonymous
caller on the same network can read, and anything it can change, they can
change.

Secret redaction still applies — the `*_configured` booleans, the alert-method
`config` masking (bead `9kwzp.13`) and the `9ej7f` settings redaction do not
depend on authentication — so an anonymous read does not hand over stored
credential *values*. It does hand over everything else.

### What is still refused

Three **identity primitives** require a real, human, authenticated admin even
when `require_auth` is false:

| Surface | Route(s) | Why |
|-|-|-|
| Initial restore | `POST /api/backup/restore-initial` | Replaces `journal.db` wholesale — the `users` table and every admin password hash with it. |
| MCP service credential | `POST` / `DELETE /api/settings/mcp-api-key` | Mints or destroys a persistent, admin-equivalent bearer credential the middleware accepts across the whole `/api/` surface. |
| TLS certificate and key material | `/api/tls`: `GET /settings`, `POST /configure`, `/request-cert`, `/complete-challenge`, `/upload-cert`, `/renew`, `/https/start`, `/https/stop`, `/https/restart`, `DELETE /certificate` | Installs a caller-supplied private key as the instance's TLS identity, and holds the DNS-provider credentials that issue it. |

The line is not "how destructive is it" — `POST /api/settings` and `POST
/api/backup/restore` are both open and both do real damage. The line is
**durability of the resulting identity**: each of the three leaves the caller
holding a credential or a key that keeps working *after* you turn
authentication back on. A settings write does not.

The mechanism is `enforce_when_auth_disabled=True` on
`auth.dependencies.require_admin_if_enabled`, carried by
`RequireHumanAdminForServiceCredential` and `RequireHumanAdminForTLSMaterial`.
`restore-initial` implements the same rule in its handler
(`routers.backup._guard_initial_restore`) because it must also survive a
damaged `setup_complete`. All three share one ownership predicate,
`auth.dependencies.instance_has_operator_identity`.

### The carve-out: instances with no operator identity

All three still serve an anonymous caller on an instance that holds **no**
operator identity — no user row, and `setup_complete` false. That is a genuine
first run, or a deliberately headless deployment that runs with authentication
off and never creates a user. Without the carve-out these routes would be
permanently unreachable there with no in-band recovery, because the only way to
obtain an admin would be to run the setup wizard and thereby abandon the
posture you chose.

The predicate fails **closed**: if the `users` table cannot be read, the
instance is treated as owned.

### Consequences to expect

- **You can still sign in.** `POST /api/auth/login` and `get_current_user`
  carry no `require_auth` short-circuit, so an operator can authenticate
  normally on an auth-disabled instance and reach all three surfaces.
- **The web UI will not offer you a login.** When `require_auth` is false,
  `useAuth` never resolves a user and `ProtectedRoute` renders the app without
  a login page. The TLS and MCP settings sections are handed
  `isAdmin={user?.is_admin ?? false}` and already render as non-admin in this
  mode, so nothing that worked before stops working — but if you need to *use*
  those sections on an auth-off instance, turn `require_auth` back on. This is
  also why `PUT /api/auth/admin/settings`, the route that toggles the mode, has
  always required a token.
- **Known residual, deliberately left open.** `POST
  /api/tls/test-dns-provider` stays anonymous in this mode. It runs on
  `RequireHumanAdminForOutboundTest` alongside eleven other connection-test
  sinks, none of which this decision covers, so an anonymous caller on an
  auth-disabled instance can exercise the stored DNS-provider credentials and
  enumerate your zones — while `GET /api/tls/settings`, which merely discloses
  those credentials in masked form, is refused. Revisit with the other eleven
  sinks, not on its own.

Behaviour is pinned in
`backend/tests/routers/test_jy006_auth_disabled_identity_primitives.py` and
`backend/tests/routers/test_backup.py::TestRestoreInitialIdentityGate`.

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
(`backend/main.py:544-577`), where `decode_token_safe` runs on the exact
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
