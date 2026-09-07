# Desktop Visual Repair Verification

From the repository root, with the root and frontend lockfile dependencies installed:

```bash
DESKTOP_EVIDENCE_DIR=/absolute/path/to/new/evidence \
DESKTOP_PYTHON=/absolute/path/to/backend-venv/bin/python \
node scripts/verify-desktop-visual.mjs
```

The Python environment needs the repository backend requirements. Chromium must
be installed for the root lockfile's Playwright version. If using a nondefault
browser installation, set `PLAYWRIGHT_BROWSERS_PATH` explicitly. No old `/tmp`
guard or running app is needed. The default guard is `scripts/desktop-guard`;
`DESKTOP_GUARD_DIR` is an optional override.

The runner builds the frontend, copies its output to this checkout's ignored
`backend/static`, and starts only its owned backend on `127.0.0.1:19863`. It
refuses an occupied port. Each invocation creates a unique `run-*` evidence
directory with fresh data, HOME and TMPDIR, screenshots, Playwright JSON,
backend logs, source metadata and a tracked worktree patch. The tests use fresh
browser contexts, deny external HTTP/WebSocket traffic and fulfill synthetic
API requests without falling through to the backend. The Python guard denies
outbound sockets and subprocesses, with positive/negative startup self-tests.
These are browser rendering tests, not backend integration or restore-validity
tests. Unspecified synthetic APIs return 501, which can produce unrelated
error cards/toasts in screenshots.

Every wait is foreground and synchronous. SIGINT/SIGTERM cancellation stops
owned children in reverse order, giving Playwright SIGINT to close its browsers
and other children SIGTERM. Each child has five seconds to exit gracefully,
then receives SIGKILL and a final five-second wait. Signals target only child
processes spawned by this runner, never process groups or a port's occupants.
Spawn errors and cancellation during build/backend startup also reach cleanup.
`result.json` records child PIDs, exit codes/signals, escalation and confirmed
exit state; cancellation returns 130/143 (or 1 if cleanup cannot confirm exit).
Do not run concurrent builds
or other operations against this checkout's static directory or port.

The browser origin guard uses the resolved Playwright `baseURL` fixture, not
`E2E_BASE_URL` directly. To verify the exact-build configuration independently,
with no backend and with the same synthetic API interception:

```bash
env -u E2E_BASE_URL E2E_START_SERVER=true E2E_EXACT_BUILD=true \
  node node_modules/@playwright/test/cli.js test e2e/desktop-visual-repairs.spec.ts \
  --project=chromium --workers=1 --retries=0 --grep 'G04 Gracenote text buttons dark 1440'
```

Repeat with `E2E_BASE_URL=http://127.0.0.1:9` instead of `env -u E2E_BASE_URL`
to check that the resolved exact-build origin (`127.0.0.1:4173`) takes precedence.
Exact-build startup refuses an existing server. The dedicated runner above
continues to use its own guarded backend on port 19863.

For pre-repair evidence, prefix `DESKTOP_BASELINE=true`. It archives and installs
the pinned audit revision `d5334fce3f41ed754e8105d537ddd3ea31ee441b` in the new
run directory. `DESKTOP_BASE_REV` can explicitly select another git revision.
Baseline dependency installation may access npm; application traffic remains
guarded. Baseline tests intentionally fail visual assertions. The runner uses
the current checkout's test spec against either build. `--grep 'G17 G20 Event'`
or any other Playwright test filter can be appended for a bounded rerun.

Frontend terminal gates, from `frontend`:

```bash
npm run lint
npm run typecheck
npm test -- --maxWorkers=2
```

The full suite includes `src/cssAudits`: shared class/chunk leakage (30 tests),
modal responsive breakpoints (4), and filter-select ownership (6). Screenshot
files are inspection evidence, not pixel-diff goldens. Asserted coverage is
desktop only, with dark/light/high-contrast at 1440x900 and 1920x1080, plus the
cited 1280 mappings/toolbar and the annotation resize case at 1280x720.
