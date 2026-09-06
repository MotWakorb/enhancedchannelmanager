# GH971 YAML Editor: Frozen Contract

Status: implemented in `098086ce`, with combined remediation in `802cc77a` and
build 0.18.2-0020 metadata prepared. Parent frozen-HEAD verification and independent
review remain pending. The contract below was frozen before tests or product code;
it is not a release or review approval.
Bead: enhancedchannelmanager-rtst2.10. Combined with GH980 in one PR.
Base: 066d340d. GH980 boundary: 4337ed3575d8029c1b2b9b56aa930f40bc4e83d3.

## Product Contract

- Add a secondary YAML tab for the complete collection of standard and Event
  Sync rules, enabled or disabled, independent of the normal list filter.
- Use the existing monospaced textarea and literal find/replace interaction
  patterns. No syntax highlighting, editor framework, regex replacement, or
  new dependency.
- A version 1 document contains `rules`, optional local `id`, and editable
  configuration fields only. Retaining an ID edits or renames that rule.
  Omitting an ID creates a new rule, including when copying a block. Names
  are not identity. Duplicate, unknown, or malformed supplied IDs are errors.
- Runtime statistics, managed ownership, timestamps, and history are not
  editable YAML. Edits retain identity, ownership, runtime state, and history.
  Copies receive fresh identity without inherited runtime, ownership, or history.
- Omitting an existing rule proposes deletion. Save requires explicit deletion
  confirmation showing names and reference warnings, including for delete-all.
  Cancel leaves the database and draft unchanged.
- Rule deletion affects current local configuration only. Existing local FK
  cleanup of Event Sync reviews/exclusions applies. Selected schedules may
  become stale; warn, do not repair. No Dispatcharr channel writes or deletes,
  history deletion, or upstream execution is part of Save.
- Invalid YAML, schema, scalars, regex, references, or a stale revision rejects
  the entire save. Drafts survive errors and cancellation. Dirty tab switches
  and reload use the existing preserve/cancel interaction pattern.

## API and Persistence

- Register GET and PUT `/api/channel-pipeline/rules/yaml` before `/{rule_id}`.
  GET returns `{yaml_content, revision}`. PUT supplies those fields and explicit
  deletion confirmation. Apply the existing admin write authority boundary.
- Do not reuse portable import as Save. Import/export behavior remains unchanged;
  portable export continues to omit local identity.
- Use PyYAML with a custom safe loader: reject duplicate keys and recursive
  aliases, bound depth/structure/input size, and report actionable line/column
  or rule-index/field errors. Reject unknown rule kinds and noneditable fields.
- Reuse existing standard/Event Sync configuration, regex, scalar, and reference
  validation, without live execution preflight or global validation hardening.
  Preserve snapshot references and legacy protected scopes on unchanged edits.
- Fetch external catalogs before opening a write transaction. Use a private
  SQLite connection rather than a shared StaticPool DBAPI write transaction;
  another generic reader must not be able to roll back Save. Do not hold a DB
  transaction across HTTP.
- Fingerprint editable values plus collection membership/IDs, excluding runtime
  and timestamps. Reserve the SQLite writer, reread and compare the revision,
  validate all entries before mutation, and apply creates/updates/deletes and
  local FK effects in one commit. Roll back the complete transaction on error.
- Concurrent CRUD/import/reorder must cause a stale editor's 409 or safely
  serialize with Save. Do not claim compare-and-swap without integration proof.
- Allocate new IDs before deleting omitted rows, or reserve the existing ID
  floor, to prevent same-save reuse of deleted identities.
- Narrowly replace detached-rule runtime `session.merge` writes reached by the
  editor race with runtime-column-only updates of an existing identity, adding
  a generation guard if needed. Real worker completion must neither overwrite
  saved configuration nor resurrect a deleted rule. Runtime-only writes must
  not create false editor revision conflicts.
- No migration is expected; recheck the model and GH980 migration 0056 first.
  No journal atomicity claim may depend on an out-of-transaction journal call.

## Frozen Verification Matrix

Tests are written and run red before implementing the behavior they cover.

| Layer | Required Evidence |
| --- | --- |
| API/file SQLite | Complete hidden/disabled collection, both kinds, load/edit/persist |
| API/file SQLite | ID rename retains selected references, runtime and ownership; copy creates fresh identity |
| Parser/API | Duplicate/unknown/malformed IDs, unknown fields/kinds, duplicate keys, aliases, bounded structure |
| API/file SQLite | Invalid final entry leaves every original row unchanged, including earlier staged edits |
| API/file SQLite | Schema/scalar/regex/reference rejection is atomic and actionable |
| API/file SQLite, FK enabled | Explicit deletion summary, cancel, delete-all, local FK cleanup, no upstream writes/history deletion |
| Transaction fault injection | Failure during creates/updates/deletes rolls back all rows and FK effects |
| Concurrency | Two editors; existing CRUD/import/reorder stale conflict or serialization; runtime-only nonconflict |
| Real worker completion | Cannot undo a save or resurrect a deleted identity |
| Regression | Portable import/export, selected schedules/prepared decisions, Event Sync custom config unchanged |
| Browser through real API/file SQLite | Draft/save/error/cancel/find-replace; fake upstream catalogs with no live writes |
| Rendered browser | Desktop and mobile inspection; combined GH971 and existing GH980 browser gate, serial port use |

Run focused pytest first, then the canonical `scripts/backend-gate.sh` in the
foreground with a 20-minute limit. Run frontend lint, typecheck, coverage and
build as feasible. Use project Python, Node 24, private TMPDIR/config, and the
worktree's own installed dependencies. Report exact commands, terminal results,
and any environment gaps. Parent independently reruns gates and performs the
mandatory DBA and security parser/authority reviews against this frozen scope.

## Exclusions and Delivery

No import/backup redesign, execution changes beyond the narrow runtime race,
new rule kind, new dependency, global validation hardening, general fixes,
force overwrite, backup/undo system, or scheduler repair. Valid catalog or
revision conflicts may be documented; expected atomicity is not weakened.

The focused GH971 commit preserves committed GH980. Combined build 0020 metadata
is prepared separately after implementation, with status/diff/log inspection and
explicit path staging. No amend, push, PR/merge, tracker, or GitHub state changes.

## Narrow Scalar Remediation Evidence

Starting HEAD: `b3077fc84097cfffdc185ab3dd34c9a10053900c`; base remains
`066d340d`. This addresses only the reported security Low (2/10): SafeLoader
scalar constructors raised plain `ValueError` for an unquoted invalid date
(`2026-02-30`) and a 5000-digit integer, bypassing the editor's YAML 422 handler.
The frozen contract and acceptance matrix above are unchanged.

- TDD red: `scripts/backend-gate.sh --subset
  tests/integration/test_pipeline_yaml_editor.py -k 'yaml_scalar or date_and_integer'
  -q` produced two failures (actual route HTTP 500 instead of 422) and one passing
  valid date/integer control before the production edit.
- Fix: only `_EditorLoader.construct_object` translates scalar-construction
  `ValueError` into `MarkedYAMLError` with the node's original source mark. The
  existing parser produces actionable 422 line/column errors. Non-scalar
  construction and other exception types remain outside this translation.
  No API-wide catch, shared YAML changes, new limits, dependencies, DB changes,
  frontend changes, or version bump.
- Green: `scripts/backend-gate.sh --subset
  tests/integration/test_pipeline_yaml_editor.py -o addopts='' -q`:
  **55 passed** (6.18s), including both scalar regressions, valid controls,
  duplicate keys, aliases, excessive nesting, malformed YAML and unsafe tags.
  The scalar route tests assert exact line/column, no catalog calls, no save DB
  session opened, and unchanged file-SQLite rows despite an earlier edited rule.
- Portable YAML regression: `scripts/backend-gate.sh --subset
  tests/routers/test_channel_pipeline.py -k yaml -o addopts='' -q`:
  **39 passed, 247 deselected** (2.57s).
- Full foreground gate: `timeout 1200 scripts/backend-gate.sh`:
  **13152 passed, 3 skipped, 2 deselected**, **82.82% coverage**, 881.25s.
  Skips: seeded Dispatcharr stream-matcher test and the existing Dropbox/OneDrive
  raw-outbound baselines. Canonical E2E/performance exclusions remain unchanged.
- Environment: main checkout `.venv/bin/python`, Python 3.12.3, PyYAML 6.0.3,
  SQLite 3.45.1, cryptography 46.0.7, interpreter integer conversion limit 4300.
  Tests use the existing isolated config harness and per-test temporary SQLite.
- `scripts/generate_sbom.py verify` with project Python: **PASS**, `sbom/dev`
  matches the source tree; build 0020 needs no regeneration. `git diff --check`
  passes. Ruff is unavailable in the project venv and on PATH; no dependency was
  installed. Frontend gates were not rerun because no frontend code changed.
- Remaining review: security delta confirmation of this loader-local translation
  and its route regressions; this evidence is not independent security approval.
