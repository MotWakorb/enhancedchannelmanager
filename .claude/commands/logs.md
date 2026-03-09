# Log Analysis

Analyze ECM logs to diagnose issues, trace requests, and surface anomalies. Supports both live container logs and log files on disk.

## Usage
`logs [focus] [path]`

- `focus` — optional filter: an error message, module tag, time range, or keyword like "slow", "errors", "startup"
- `path` — optional path to a log file or directory containing log files (e.g., `~/ecm`)

## Steps

### 1. Determine Log Source

**If a path is provided**, use log files from that location:

```bash
# If path is a directory, find log files in it
find <path> -maxdepth 1 -name "*.log" -o -name "*.txt" | head -20
ls -lhS <path>/*.log <path>/*.txt 2>/dev/null

# For large files (>10MB), NEVER read the whole file. Use tail/grep:
tail -500 <path>/output.log                          # Last 500 lines
grep -c ' - ERROR - \| - CRITICAL - ' <path>/output.log  # Count errors
grep ' - ERROR - \| - CRITICAL - ' <path>/output.log | tail -50  # Recent errors

# For time-bounded analysis on large files:
grep '^2026-03-09' <path>/output.log | tail -500     # Specific date
sed -n '/^2026-03-09 14:00/,/^2026-03-09 15:00/p' <path>/output.log | tail -500  # Time range

# For keyword/tag filtering on large files:
grep '\[STREAM-PROBE\]' <path>/output.log | tail -100
grep -B5 -A5 'Traceback' <path>/output.log | tail -200
```

**If no path is provided**, fetch from the live container:

```bash
# Default: last 500 lines
docker compose logs --no-log-prefix --tail=500 ecm 2>&1

# Time-bounded (if user specifies a range)
docker compose logs --no-log-prefix --since=1h ecm 2>&1

# Follow mode is NOT supported in this context — always use tail/since
```

### Known Log Files

| Location | Description | Size |
|-|-|-|
| `~/ecm/output.log` | Full captured backend log (~200MB, multi-day) | Large — always use tail/grep |
| `~/ecm/*.txt` | Captured log snippets (e.g., stream prober runs) | Small — can read directly |
| Container stdout | Live logs from running ECM instance | Use `docker compose logs` |

### 2. Parse Log Format

ECM backend logs follow this format:
```
TIMESTAMP - MODULE_NAME - LEVEL - [TAG] Message
```

Key fields:
| Field | Example |
|-|-|
| Timestamp | `2026-03-09 14:22:01,123` |
| Module | `main`, `routers.channels`, `config` |
| Level | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| Tag | `[MAIN]`, `[CHANNELS]`, `[M3U]`, `[REQUEST]`, `[SLOW-REQUEST]`, `[RAPID-POLLING]`, `[CONFIG]`, `[VALIDATION-ERROR]` |

Uvicorn also emits its own access/lifecycle logs (different format — no tag prefix).

### 3. Triage by Focus

**If focus is "errors" or unspecified and errors exist:**
- Filter for `ERROR` and `CRITICAL` level lines
- Group by module/tag
- Show full tracebacks (look for `Traceback` blocks and `exc_info` output)
- Check if errors are recurring or one-off

**If focus is "slow":**
- Find `[SLOW-REQUEST]` entries (requests >1000ms)
- Extract endpoint, method, and duration
- Look for patterns (same endpoint? same time window?)

**If focus is "startup":**
- Find the most recent `[MAIN] Starting ECM` or uvicorn startup message
- Trace the initialization sequence: config load, DB connection, service starts
- Flag any warnings or errors during startup

**If focus is "rapid-polling" or "polling":**
- Find `[RAPID-POLLING]` warnings (20+ hits in 10s)
- Identify which endpoints and source IPs are flooding
- Check if this correlates with slow requests or errors

**If focus is a specific module tag (e.g., "m3u", "channels", "epg"):**
- Filter logs to lines containing `[TAG]` (case-insensitive match)
- Show chronological flow for that subsystem
- Highlight any errors or warnings

**If focus is a specific error message or keyword:**
- Grep for the keyword in the captured logs
- Show surrounding context (5 lines before/after)
- Trace the request flow that produced it

### 4. Analyze Patterns

Look for:
- **Error clusters**: Multiple errors in a short time window
- **Cascading failures**: One error triggering others (e.g., DB timeout → multiple endpoint failures)
- **Recurring warnings**: Same warning repeating — may indicate a configuration issue
- **Memory/resource hints**: Messages about connection pools, file descriptors, or timeouts
- **Request timing trends**: Are slow requests concentrated on certain endpoints or time periods?

### 5. Report

Present findings as a structured summary:

**Log window**: Time range of analyzed logs
**Total lines**: Count of log lines analyzed
**Breakdown by level**: Count of DEBUG / INFO / WARNING / ERROR / CRITICAL

Then for each finding:
- **What**: The issue or pattern observed
- **Where**: Module, endpoint, or tag
- **When**: Timestamp(s) or frequency
- **Impact**: What this likely means for the running system
- **Suggestion**: Next step to investigate or fix

### 6. Check Current Log Level

If the analysis seems incomplete (e.g., no DEBUG output when needed):

```bash
docker exec ecm-ecm-1 cat /config/settings.json | grep -i log_level
```

Suggest changing the level if more detail is needed:
- More detail: set `backend_log_level` to `DEBUG` in settings
- Less noise: set to `WARNING` if logs are overwhelming

## Notes

- Container logs go to stdout/stderr; Docker manages retention (no rotation configured)
- `~/ecm/output.log` is a large persistent capture — always use tail/grep, never read it whole
- Log injection is protected (newlines sanitized via `log_utils.py`)
- Frontend logs are browser-console only — not visible in container or file logs
- The `journal.db` SQLite database stores an audit trail separate from system logs
- `httpx` lines (HTTP Request logs) appear in log files from the stream prober's outbound requests
- `--- Logging error ---` lines indicate a Python logging formatter crash — these are bugs worth flagging
