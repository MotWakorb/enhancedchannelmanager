# Backend Architecture Patterns

**Modular routers**: `backend/routers/` has 20+ domain-focused modules (channels, m3u, epg, settings, etc.)

**Router registry**: `routers/__init__.py` has `all_routers` list; `main.py` includes them via `app.include_router()`

**main.py** retains: app lifecycle, middleware, auth, startup/shutdown

**Mock patches**: When testing router endpoints, patch `routers.<module>.X` not `main.X`

**Why:** The v0.13.0 refactor moved endpoints from main.py into routers/. Mock targets must match where the name is looked up at runtime.

**How to apply:** Always check which module owns the endpoint before writing test patches.
