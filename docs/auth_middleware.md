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
