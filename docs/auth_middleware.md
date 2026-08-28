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
authentication gate for `/api/*`: an exact string match, no prefix logic, no
second layer behind it for routers that carry no dependency of their own. Its
contents are snapshotted in
`backend/tests/test_auth_exempt_paths_snapshot.py`, so adding a path requires
editing that file in the same commit. The failure message is the review
checklist. This exists because adding a data route to that set was otherwise a
one-line, silent, total-exposure change that shipped with green CI, and because
`/api/backup/restore-initial` (a full `journal.db` replacement, admin password
hashes included) really was in that set until bead
`enhancedchannelmanager-lf29s`.

Note that middleware exemption is not the same as anonymity: `GET` and `PUT
/api/auth/admin/settings` are exempt from the middleware but carry
`auth.routes.require_admin`, which chains `get_current_user` and validates a
token in every mode. That is pinned too.

## What `require_auth: false` permits

`require_auth: false` is a real, supported ECM operating mode, not a bug state.
Setting it means **the instance serves its API to anyone who can open a socket
to it.** Run it only on a network you trust, and read this section before you
do. (An incomplete first-run setup, meaning `setup_complete: false`, has the
same effect and the same rules; the middleware and every gate below treat the
two conditions identically.)

PO decisions, 2026-08-13 (bead `enhancedchannelmanager-jy006`) and 2026-08-15
(bead `enhancedchannelmanager-2u4e0`).

### What is open

Effectively the whole API. `GET`/`POST /api/settings`, `/api/channels`,
`/api/streams`, `/api/journal`, `POST /api/backup/restore` (a wholesale config
write), `GET /api/backup/create` and `/export` (which emit an archive
containing your stored settings), `POST /api/settings/reset-stats`, the cloud-
and sync-target CRUD, the alert-method CRUD, and the two TLS status reads are
all reachable **without any credential** while the mode is on. Assume that
anything the API can read, an anonymous caller on the same network can read,
and anything it can change, they can change.

Secret redaction still applies. The `*_configured` booleans, the alert-method
`config` masking (bead `9kwzp.13`) and the `9ej7f` settings redaction do not
depend on authentication, so an anonymous read does not hand over stored
credential *values*. It does hand over everything else.

### What is still refused

Three classes of surface require a real, human, authenticated admin even when
`require_auth` is false.

**Identity primitives** (jy006). Each one leaves the caller holding a
credential or a key that keeps working *after* you turn authentication back on:

| Surface | Route(s) | Why |
|-|-|-|
| Initial restore | `POST /api/backup/restore-initial` | Replaces `journal.db` wholesale: the `users` table and every admin password hash with it. |
| MCP service credential | `POST` / `DELETE /api/settings/mcp-api-key` | Mints or destroys a persistent, admin-equivalent bearer credential the middleware accepts across the whole `/api/` surface. |
| TLS certificate and key material | `/api/tls`: `GET /settings`, `POST /configure`, `/request-cert`, `/complete-challenge`, `/upload-cert`, `/renew`, `/https/start`, `/https/stop`, `/https/restart`, `DELETE /certificate` | Installs a caller-supplied private key as the instance's TLS identity, and holds the DNS-provider credentials that issue it. |

**Connection tests** (2u4e0). Each one reaches the network with credentials
your instance already stores, to a host the caller can often name, and reports
the upstream verdict back, so an anonymous caller can spend a secret they never
had to learn and read an in-band port scan off the reply:

| Surface | Route(s) |
|-|-|
| Dispatcharr, SMTP, Discord, Telegram | `POST /api/settings/test`, `/test-smtp`, `/test-discord`, `/test-telegram` |
| Media servers | `POST /api/settings/emby/test-connection`, `/plex/test-connection`, `/jellyfin/test-connection` |
| Alert methods and the M3U digest | `POST /api/alert-methods/{id}/test`, `POST /api/m3u/digest/test` |
| Backup upload targets | `POST /api/cloud-targets/test`, `POST /api/cloud-targets/{id}/test` |
| DNS provider | `POST /api/tls/test-dns-provider` |

**Diagnostic artifacts.** `POST /api/channel-pipeline/debug-bundle` and `GET
/api/channel-pipeline/debug-bundle/{job_id}` always require an authenticated
human admin. They do not admit the MCP service principal, and they have no
first-run/no-identity carve-out. A job is bound to the user id that started it;
another admin cannot poll or download it.

The line is not "how destructive is it." `POST /api/settings` and `POST
/api/backup/restore` are both open and both do real damage.

**What this costs you.** On an auth-disabled instance that has a user account,
a browser that is not signed in now gets `403` from **every Test Connection
button in Settings**, where it used to get a result. Nothing is permanently
lost: browse to `/login`, sign in, and every one of them works again, with
`require_auth` still false. An instance that never created a user is
unaffected, per the carve-out below. Until 2026-08-15 all twelve of those
routes were anonymous in this mode, which made a single router contradict
itself: `POST /api/tls/test-dns-provider` would exercise your stored
DNS-provider credentials and enumerate your zones for anyone on the network,
while `GET /api/tls/settings`, which merely shows those credentials *masked*,
was refused.

The identity and connection-test mechanism is `enforce_when_auth_disabled=True` on
`auth.dependencies.require_admin_if_enabled`, carried by
`RequireHumanAdminForServiceCredential`, `RequireHumanAdminForTLSMaterial` and
`RequireHumanAdminForOutboundTest`. `restore-initial` implements the same rule
in its handler (`routers.backup._guard_initial_restore`) because it must also
survive a damaged `setup_complete`. All of them share one ownership predicate,
`auth.dependencies.instance_has_operator_identity`.
Debug bundles instead use `require_authenticated_human_admin`, which always
chains `get_current_user` and therefore never enters an auth-disabled
short-circuit.

### The carve-out: instances with no operator identity

The identity primitives and connection tests still serve an anonymous caller on an instance that holds **no**
operator identity: no user row, and `setup_complete` false. That is a genuine
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
  normally on an auth-disabled instance and reach every refused surface above.
- **The web UI offers you a login at `/login`.** When `require_auth` is false,
  `ProtectedRoute` renders the app without demanding a session, so you are
  anonymous by default and the TLS and MCP settings sections render as
  non-admin. Browse to `/login` and sign in, and they become usable without
  turning `require_auth` back on. Bead `enhancedchannelmanager-p388h` added
  that route; before it, `/login` was rewritten to `/` in this mode, which left
  the three surfaces above reachable by API but not by UI. The login form is
  offered only once the instance holds an operator identity, since there is
  nothing to sign in to before that. This is also why
  `PUT /api/auth/admin/settings`, the route that toggles the mode, has always
  required a token.
- **Test Connection buttons need a session.** This is the one behaviour change
  an operator running this mode will notice, and it is described under "What
  this costs you" above. The `[AUTH]` log line naming the refused method and
  path is what to look for if a button starts returning `403`.

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
