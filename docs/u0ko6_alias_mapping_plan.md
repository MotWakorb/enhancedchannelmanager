# enhancedchannelmanager-u0ko6: Frozen Alias Mapping Contract

Owner: project-engineer. GitHub: #775. Base: origin/dev `144334cf`.
Status: frozen contract implemented locally; final bounded code and DBA reviews complete.
This supplements, rather than replaces, the original request in the owning bead.

## Approved Behavior

- Right-side stream selection offers Add mapping, with Existing / Add new.
  Existing means a reusable preferred-name mapping, not a Dispatcharr channel ID.
- Dedicated Mapped channels tab supports review, edit, add and remove.
- Persist structured aliases. Match whole original names case-insensitively and
  literally across providers. Reject conflicting ownership; no regex, fuzzy,
  substring or provider-specific matching.
- Preferred spelling is authoritative when a name matches, including subsequent
  normalization and Create / Pipeline consumers. Unmapped behavior is preserved.
- Saving defines mappings for subsequent explicit Create / Pipeline runs and
  already-configured automation only. It performs no Dispatcharr mutation.
  Removal leaves existing channels and attachments intact.
- Examples: Polonia <- Polonia, Polonia 1, Polonia1, Polonia.1;
  Stars TV <- Stars TV, Stars.TV, Stars-TV; TVN <- TVN, TVN HD, TVN-HD.
- No duplicate cleanup, detaches, scheduler, EPG/TVG redesign, MCP tools,
  import/export, unrelated refactors or dependencies.

## Verification Boundary

Tests first: literal positives/negatives (TVN24, Polonia 2, StarsXTV), regex
metacharacters, duplicate/conflicting aliases, persisted CRUD, visible API
errors, selection prefill, preferred spelling surviving normalization, Create
grouping and repeated Pipeline execution without duplicate channels/attachments.
Existing manual-channel and group safeguards remain in force. Text normalization
tests alone are not downstream assignment evidence. Render the browser surface.
Run targeted backend/frontend tests before canonical gates; report exact results
and gaps. No live Dispatcharr writes.

## Persistence / Review

Prepare an additive Alembic migration for preferred-name mappings and uniquely
owned case-folded literal aliases. Preferred names reserve their own identity.
No existing data rewrite or automatic mapping seed. DBA review is required before
completion. Downgrade removes mapping definitions only, not Dispatcharr data.

Prepared migration: `0055`, predecessor `0054`,
`backend/alembic/versions/20260904_1200_0055_channel_name_mappings.py`.
`channel_name_mappings` holds integer ID and preferred spelling;
`channel_name_aliases` holds ID, indexed mapping FK, literal name, and globally
unique Python-casefold comparison key. CRUD reserves the preferred name in the
alias table, deduplicates same-owner keys and rolls back conflicting writes.
The migration supports the existing create-all-before-Alembic recovery path.
Standard database backup table classification follows normalization configuration;
there is no new import/export UI or MCP tool. API writes use the existing admin
tier (including its existing MCP-principal semantics), recorded in the admin gate
inventory. The initial independent DBA review found the concurrent replacement
defect recorded in the remediation evidence below; full final DBA re-review passed
as recorded in Final Review and Handoff below.

## Delivery Boundary

The user authorized commit, push and a PR against dev on feat/u0ko6-alias-mapping.
The project-engineer owns the commit only; parent owns push, PR and bead status.
HOLD merge; no merge or issue closure is authorized. The authorized PR must
record `Closes #775` and verify GitHub #775 closure on merge; do not close now.
Review blockers are contract failures, introduced regressions, security or data
loss, not additional enhancements. Report unforeseen product decisions.

## Local Verification Evidence

Verified against the uncommitted isolated worktree, not a deployed instance:

| Layer | Command | Result |
| --- | --- | --- |
| Backend API, literal matching, executor and migration | `scripts/backend-gate.sh --subset tests/routers/test_channel_name_mappings.py tests/integration/test_channel_name_mapping_migration.py` | 10 passed |
| Backend canonical gate | `scripts/backend-gate.sh` | 12,855 passed; 3 documented skips; 2 deselected; 81.45% coverage |
| Frontend lint | `npm run lint` in `frontend/` | Passed |
| Frontend types | `npm run typecheck` in `frontend/` | Passed |
| Frontend canonical suite | `npm run test:coverage` in `frontend/` | 259 files; 3,685 passed; 59.8% statement coverage |
| Frontend build | `npm run build` in `frontend/` | Passed; existing large-chunk warning remains |
| Browser/API seam | Command below | 1 Chromium test passed |
| Diff whitespace | `git diff --check` | Passed |

TDD evidence: initial API tests failed with 404 before the routes existed;
the repeated-executor test created four channels before mapping integration;
frontend preferred-name tests failed with raw names before resolver integration.
The partial-response test failed before atomic response validation was repaired.

The browser test uses the actual normalization router and file-backed SQLite on
an ephemeral loopback port. It selects streams in the real pane, saves a mapping,
stages one preferred-name channel with two attachments, discards the staged
changes without committing, reloads the management tab, and removes the mapping.
Desktop and 390px screenshots are under `test-results/`; the narrow layout uses
the existing collapsed sidebar. Other browser API calls are fixture responses.
Repeated Pipeline executor tests use a stateful fake Dispatcharr client and ECM's
managed-channel ledger between executor instances; no live Dispatcharr write was
performed. Auto-merge tests also pin manual-channel and target-group safeguards.

### Independent Reproduction

Workdir: `/tmp/opencode/ecm-u0ko6`. Node: `v24.13.0`. Dependencies were installed
from each lockfile with `npm ci` in the root and frontend (no dependency edits).
Do not use the initially bootstrapped shared frontend packages: their versions
did not match this base. Python was the existing project venv, read-only:

```sh
export ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python
export ECM_TEST_CONFIG_ROOT=/tmp/opencode/ecm-u0ko6/.test-config
export TMPDIR=/tmp/opencode/ecm-u0ko6
export PYTHONDONTWRITEBYTECODE=1
scripts/backend-gate.sh
E2E_START_SERVER=true E2E_EXACT_BUILD=true PLAYWRIGHT_HTML_OPEN=never npm run test:e2e -- e2e/channel-name-mappings.spec.ts --project=chromium --workers=1 --retries=0
```

Run the browser command with exclusive ownership of preview port 4173. Its API
fixture owns a separate ephemeral port and unique database and is stopped before
the command returns. No watcher or verification server was left running.

Logs: `backend-gate-final.log`, `frontend-coverage-final.log`. Earlier failing
gate logs remain as TDD/remediation evidence. `.npm-cache/`, `.test-config/`,
`pytest-of-lecaptainc/`, and `ecm-debug-bundle-*.tar.gz` are local test/install
scratch, not product changes and must not be staged. Generated `test-results/`,
coverage and build artifacts are also not product changes.

### Remaining Delivery Boundary

Final bounded code review, full in-scope DBA re-review and parent verification are
complete, with scope and provenance recorded in Final Review and Handoff below.
Commit, push and a PR against dev are now authorized, with push and PR reserved
for the parent. Merge remains on HOLD; publishing, live deployment/reporter
verification and issue closure are not authorized. Preserve the closure-on-merge
instructions for #775.

## Four-Finding Remediation Evidence

This records remediation of the confirmed review findings, not new acceptance
conditions. The Approved Behavior and Verification Boundary remain frozen.
Review sources: code reviewer `ses_f9028063bffejNsmS4pcADhkGG` and DBA
`ses_f90280626ffeuW8xvpzTIV47Gr`.

- Mapped Create identity now disables lookup re-normalization/core fallback and
  folded fallback, retaining the existing exact-name indices, manual gate and
  group gate. Mapped auto-merge disables the loose-name cascade. Tests first
  reproduced attaching to managed `Stars TV` instead of `Stars TV HD` in both
  Create and loose auto-merge.
- Create ownership is resolved from the original stream name, not the template
  or transform output. Subsequent rule normalization and lookup normalization
  cannot acquire mapping ownership from a transformed name. Eight executor cases
  cover mapped/unmapped originals, selected/no groups and existing/no preferred
  channel. The existing-channel negative also failed before the lookup fix.
- App Create numbering sorts resolved mapped names and legacy quality-stripped
  unmapped base names, not internal identity prefixes. The actual App staging
  callback regression failed with `ZDF=100, ABC=101` before the fix and now proves
  `ABC=100`, natural `C-SPAN`, `C-SPAN 2`, `C-SPAN 10` order, then `ZDF=104`.
  Group identity separation is unchanged.
- The first remediation added a local lock before opening mapping CRUD sessions.
  This serialized mapping CRUD but did not isolate generic StaticPool readers;
  the remaining defect and final remediation are recorded below. Update and
  delete first delete aliases by mapping ID from current database state, acquiring
  SQLite's writer lock before reading the mapping; replacement and commit remain
  one transaction. Isolated file-backed WAL/StaticPool/autoflush-false tests force
  overlapping update/update and update/delete requests. Before the fix the update
  test persisted exactly `First y, First z, Second, Final`; after the fix only the
  final request's aliases remain, and delete leaves no alias rows. The retained
  DBA probe was read, not modified or rerun (its barrier assumes the old interleave).

Fresh verification against the uncommitted remediation worktree:

| Layer | Result |
| --- | --- |
| Backend focused: mapping API/executor/concurrency, migration, executor unit tests, normalization router | 456 passed |
| Frontend focused: App staging, normalization, mapped-channel component | 29 passed |
| Backend canonical `scripts/backend-gate.sh` | 12,867 passed; 3 documented skips; 2 deselected; 81.46% coverage; 1,081.67s |
| Frontend `npm run lint` / `npm run typecheck` | Both passed |
| Frontend `npm run test:coverage` | 259 files; 3,686 passed; 59.8% statement coverage |
| Frontend `npm run build` | Passed; existing large-chunk warning |
| Existing exact-build Chromium browser/API test | 1 passed; 5.6s |

Environment: same isolated worktree, Node 24.13.0, installed root/frontend
lockfile dependencies and project Python venv documented above. Backend runs used
the documented `ECM_TEST_CONFIG_ROOT`, `TMPDIR`, and `PYTHONDONTWRITEBYTECODE`.
All verification commands returned synchronously; no live Dispatcharr writes.
Logs `backend-gate-remediation.log`, `frontend-coverage-remediation.log` and
`frontend-build-remediation.log` are scratch and must not be staged. Existing
scratch/user work is preserved. Browser output remains under `test-results/`.
No commits, pushes, PRs, merges, tracker changes or self-approval were performed.
At that stage, parent gates, code re-review and full DBA re-review were outstanding;
their final disposition is recorded below.

## Final Bounded Remediation Evidence

This round addresses the same mapping-integrity contract and the cache-sensitive
Create regression test only. No acceptance criteria were added.

- Mapping create/update/delete now use a short-lived private SQLite connection
  through a `NullPool` engine and `Session(autoflush=False)`. The existing
  `get_database_url()` helper and engine-wide PRAGMA listener are reused;
  `database.py`, its production `StaticPool`, and migration `0055` are unchanged.
  The session closes before the private engine is disposed, including failures.
  The mapping lock and delete-current-alias-set replacement logic are retained.
- Three regression cases call actual `init_db()`, pause create/update/delete
  after flush and before commit, then call the actual resolve handler and an
  ordinary `get_session()` reader while the writer is paused. Before the fix,
  create/update exposed `Final -> Second` and delete hid `Old alias` before commit.
  After the fix, both readers see the committed old set; after they close and
  the writer resumes, the persisted set matches the successful requested result.
  Existing overlapping replacement/delete and conflict rollback tests also pass.
- Mapping tests now clear shared tag-ID and tag-value caches before and after
  each case via `invalidate_tag_cache()`. The quality-stripping regression also
  asserts that `Stars TV HD` actually extracts to `Stars TV` for matching.
  In private scratch `/tmp/opencode/u0ko6-final-mutation`, changing only the Create
  lookup to `exact_only=False` produced the intended failure: `update_channel`
  was awaited once. Result: 1 failed, 18 passed, 5 deselected. The live tested
  source was never mutated. Scratch is not part of the product diff.

Final local results against the uncommitted worktree:

| Layer | Result |
| --- | --- |
| Mapping API/executor/concurrency and migration subset | 25 passed |
| Broader mapping, migration, executor unit and normalization router subset | 459 passed |
| Canonical `scripts/backend-gate.sh` | 12,870 passed; 3 documented skips; 2 deselected; 82.12% coverage; 738.39s |
| Diff whitespace | `git diff --check` passed |

Reproduction commands from `/tmp/opencode/ecm-u0ko6`, using the same environment
as the earlier evidence:

```sh
export ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python
export ECM_TEST_CONFIG_ROOT=/tmp/opencode/ecm-u0ko6/.test-config
export TMPDIR=/tmp/opencode/ecm-u0ko6
export PYTHONDONTWRITEBYTECODE=1
scripts/backend-gate.sh --subset tests/routers/test_channel_name_mappings.py tests/integration/test_channel_name_mapping_migration.py tests/unit/test_channel_pipeline_executor.py tests/routers/test_normalization.py
scripts/backend-gate.sh
git diff --check
```

The full gate was waited synchronously with 60-second heartbeat checks until
exit 0; no background watcher or test process was left running. Its captured
output is `/home/lecaptainc/.local/share/opencode/tool-output/tool_0701877b8001tgM94bCNkW0Z1s`.
Frontend code was not changed or re-gated in this round; earlier frontend/browser
results above remain historical evidence, not a fresh run. This internal plan is
not an input to the published MkDocs site. Existing logs, caches, configuration,
debug bundles and generated artifacts remain scratch and must not be staged.
No commits, pushes, PRs, merges or tracker changes were made in that remediation
round. The subsequent independent reviews and parent verification are recorded below.

## Final Review and Handoff

This documentation/bead-only handoff records the final reports supplied by the
parent; it does not claim new test runs or independent re-review by this handoff
editor. Earlier results above are chronological evidence, not pending gates.

- Final bounded code review `ses_f8fe459a5ffeNM1Tb0LY6e3SSP` approved the
  cache-test repair and private transaction boundary. The reviewer reran the
  unsafe `exact_only=False` mutant and observed the expected failure; all six
  boundary cases passed.
- Final DBA review `ses_f8fe459d8ffetvARjcsgESc5W4` reported full in-scope PASS,
  25 tests and no remaining findings. Scope included private write consumer
  isolation for create/update/delete using actual `init_db()`, overlapping writes
  and conflicts, Unicode ownership, FK/PRAGMA behavior, upgrade/downgrade/bootstrap,
  and sentinel/index checks.

| Verification owner | Final evidence |
| --- | --- |
| Parent, final backend tree | 459 focused backend tests passed |
| Parent, final browser/API seam | Actual mapping API Chromium test: 1 passed |
| Parent, documentation | Strict `mkdocs build --site-dir /tmp/opencode/ecm-u0ko6-docs-site` passed |
| Parent, whitespace | `git diff --check` passed |
| Parent, frontend | 3,686 tests, lint, typecheck, coverage and build passed; frontend unchanged by the last backend fix |
| Engineer, final full backend; reviewer log-confirmed | 12,870 passed; 3 skipped; 2 deselected; 82.12% coverage |
| Parent, earlier full backend | 12,867 passed before the last backend fix; final-tree parent verification was the 459-test focused run, not a fresh full backend run |

Local implementation and handoff: `/tmp/opencode/ecm-u0ko6`, branch
`feat/u0ko6-alias-mapping`, base/HEAD `144334cf`; code remains uncommitted.
This document is the durable frozen-contract, implementation, review and
verification handoff. Owning bead `enhancedchannelmanager-u0ko6` remains
`in_progress`; its design/notes record this handoff without replacing the original
description or acceptance criteria.

The user subsequently authorized commit, push and a PR against dev, with the
project-engineer limited to commit and the parent handling push and PR. Passing
reports do not authorize merge: HOLD merge, with no publishing or status closure.
The authorized PR must include `Closes #775` and verify GitHub #775 closure
on merge; do not close either item now.
