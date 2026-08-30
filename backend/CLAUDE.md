# Backend Agent Instructions

> Full system architecture diagram: `docs/architecture.md`

## Framework & Stack

- **FastAPI** (async), **SQLAlchemy** ORM with **SQLite**, **Pydantic** validation
- Entry: `main.py` (app lifecycle, middleware, WebSocket, auth, startup/shutdown)
- DB file: `/config/journal.db`; models in `models.py`

## Directory Structure

```
backend/
├── main.py                 # App entry, middleware, router registration
├── routers/                # 20 domain-focused API routers
│   └── __init__.py         # all_routers list (registration order matters)
├── services/               # Service layer (notification_service.py)
├── tasks/                  # Scheduled task implementations
├── auth/                   # Auth subsystem (routes, tokens, dependencies, providers/)
├── tls/                    # TLS/ACME certificate management
├── tests/                  # Test suite (conftest.py, routers/, services/, unit/, integration/)
├── models.py               # SQLAlchemy ORM models
├── database.py             # Session factory, init_db()
├── config.py               # Settings management
├── dispatcharr_client.py   # Async HTTP client for Dispatcharr API
├── journal.py              # Audit logging
├── cache.py                # In-memory cache with TTL
├── auto_creation_*.py      # Auto-creation engine/evaluator/executor/schema
├── stream_prober.py        # Stream health checking
├── task_scheduler.py       # Abstract task base class
├── task_registry.py        # Task registry
└── task_engine.py          # Task execution engine
```

## Router Conventions

```python
router = APIRouter(prefix="/api/channels", tags=["Channels"])

@router.get("")            # Root route uses "" not "/"
async def get_channels(...):
```

- Prefix format: `/api/<domain>` (e.g., `/api/channels`, `/api/m3u`, `/api/settings`)
- Root routes use `""` (empty string), NOT `"/"` — avoids trailing slash 307 redirects
- Tags match domain names: `tags=["Channels"]`, `tags=["M3U"]`, `tags=["Settings"]`
- Routers registered in `routers/__init__.py` → `all_routers` list → included by `main.py`

## Logging

```python
import logging
logger = logging.getLogger(__name__)

# Always use lazy % formatting, never f-strings in log calls
logger.info("[CHANNELS] Created channel id=%s name=%s", channel_id, name)
logger.warning("[CHANNELS] Failed to update: %s", e)
logger.debug("[CHANNELS] Fetched %d channels in %.1fms", count, elapsed_ms)
```

- **Prefix format**: `[UPPERCASE-MODULE]` in brackets (e.g., `[CHANNELS]`, `[M3U]`, `[EPG]`, `[AUTH]`, `[TASKS]`, `[DATABASE]`)
- **Lazy formatting**: Always `logger.x("msg %s", val)` — never `logger.x(f"msg {val}")`

## Error Handling

```python
# Standard pattern in routers
try:
    result = await client.operation()
except Exception as e:
    logger.warning("[MODULE] Operation failed: %s", e)
    raise HTTPException(status_code=500, detail=str(e))
```

- Never silently swallow exceptions (`except: pass`)
- Always log before raising HTTPException
- Status codes: 200 (success), 204 (delete), 400 (validation), 404 (not found), 409 (conflict), 500 (server error)

## Database Patterns

```python
from database import get_session

# In routers - FastAPI dependency injection
@router.get("/items")
async def get_items(db: Session = Depends(get_session)):
    ...

# In tasks/services - direct usage
db = get_session()
try:
    ...
finally:
    db.close()
```

## Testing

**The gate is `scripts/backend-gate.sh`. Run it from anywhere; it takes no arguments.**

```bash
scripts/backend-gate.sh          # THE backend gate
echo $?                          # 0 = green. Read the code, not a piped tail.
```

Do not hand-type a pytest invocation and call it the gate. Two invocations used to circulate — one from this file's prose, one from CI — differing by 72 collected tests, so "backend gate green" could be reported by a run that never executed them (bead `enhancedchannelmanager-c9lb9`). The script is the single invocation, and `backend/tests/unit/test_backend_gate_contract.py` asserts it still matches `.github/workflows/test.yml` flag for flag.

The script selects the interpreter itself (`$ECM_PYTHON`, else the repo `.venv`, else — in a worktree, which has none — the main checkout's, derived from `git-common-dir`) and refuses to run rather than fall back to ambient `python`. That fallback is a real trap: ambient `python` resolves an older `cryptography` and *silently self-skips 9 TLS tests* instead of failing, so the run still reports success. The gap is invisible unless you compare skip counts.

**What the gate excludes, and why** — every exclusion is named, none is a bare number:

| Excluded | Count | Why |
| --- | --- | --- |
| `tests/e2e/` | 10 files | Needs a live ECM container on `localhost:6100`; self-skips without one, and exercises whatever that container runs with one. Deferred to bead `enhancedchannelmanager-2lw25`. |
| `tests/performance/` | 2 files | Seeds 250k rows; runs in the `perf-benchmarks` workflow (`bd-skqln.10`). |
| `-m "not slow"` | 2 tests | A 5ms-per-call microbenchmark (host contention false-fails it) and a 5M-row migration volume gate. Named individually in `DOCUMENTED_SLOW_TESTS` in `test_backend_gate_contract.py`. |

Expected shape on a green `dev`: **`3 skipped, 2 deselected`**. The full-tree run (`pytest tests/`, no ignores) instead reports **9 skipped** — the extra 6 live in the excluded trees. `18 skipped` from either means the wrong interpreter.

**Subset runs: coverage will fail you, and it is not real.** `pytest.ini` sets `--cov=. --cov-fail-under=56`, and coverage is measured over the whole tree regardless of what you selected — so *any* subset run exits non-zero even when every test in it passes (a bare `--collect-only` reports `Total coverage: 18.39%`). Use the subset mode, which disables coverage and prints the warning:

```bash
scripts/backend-gate.sh --subset tests/unit/test_foo.py -k some_case   # adds --no-cov
```

A `--subset` run is **not** the gate and must never be reported as one.

- In-memory SQLite with `StaticPool` for isolation
- **Mock at router module level**: `patch("routers.channels.get_client", ...)` — NOT `patch("main.get_client", ...)`
- Fixtures in `tests/conftest.py`: `test_engine`, `test_session`, `async_client`
- Test naming: `test_returns_channels()`, `test_client_error()`, `test_creates_item()`

## Task System

```python
from task_scheduler import TaskScheduler, TaskResult
from task_registry import register_task

class M3URefreshTask(TaskScheduler):
    task_id = "m3u_refresh"
    name = "M3U Refresh"

    async def execute(self) -> TaskResult:
        ...

register_task(M3URefreshTask)
```

## Key Singletons

- `get_client()` / `reset_client()` — Dispatcharr HTTP client
- `get_settings()` / `save_settings()` — Configuration
- `get_cache()` — In-memory cache with TTL
- `journal.log_entry(category, action_type, ...)` — Audit logging

## Deploy to Container

```bash
docker cp backend/main.py ecm-ecm-1:/app/main.py
docker cp backend/routers/. ecm-ecm-1:/app/routers/
docker restart ecm-ecm-1   # No --reload; restart required
```

Backend deploys to `/app/` (NOT `/app/backend/`). The entrypoint runs `cd /app && uvicorn main:app`.
