# GH858 Global Stop Plan

Bead: `enhancedchannelmanager-0m06f.2.3.2`. Scope frozen by the PO's
global-stop decision recorded in the bead notes. No GH969 work.

## Acceptance Criteria

- A matching enabled rule with `stop_processing` preserves the exact action
  output, including when the action makes no change. No later rule, group,
  repeat pass, legacy tag, or whitespace cleanup runs for that name.
- Preserve `Los Angeles Clippers` -> `LA Clippers` with later Title Case;
  cover the compact spelling and later lower/upper case rules too.
- Unmatched rules and disabled rules/groups remain unaffected. An executed
  else branch retains its existing group-local stop for the current pass,
  whether or not it changes the text.
- Preferred-name mappings retain precedence over rules. No normalization
  rewrite, new option, migration, or live writes.
- Saved-rule batch preview, normalize API, and direct execution agree.
  Single-rule Live Preview passes the existing stop flag through the API and
  skips post-match cleanup when set; unmatched/else cleanup is unchanged.

## Implementation And Verification

1. Add regressions using isolated SQLite fixtures and record failures on the
   unchanged implementation. Test UI checkbox-to-preview request wiring.
2. Return a stop flag with the single-pass result; break the outer loop before
   legacy tags/cleanup. Keep else handling unchanged. Pass the existing flag
   through the single-rule preview request and honor it after a match. Bulk
   apply must consume that output without trimming it again.
3. Enumerate stop-processing references and update directly affected user,
   developer, and MCP descriptions without unrelated documentation changes.
4. Run focused backend normalizer, router, mapping, and consumer tests plus
   frontend normalization tests and applicable lint/type checks. Record exact
   commands, red/green evidence, and limitations here before the focused commit.

## Evidence

Starting source: `fc8963ee`. Before implementation, the 26-case backend suite
reported **14 failed, 12 passed**. Failures include the exact `La Clippers`
reproduction, lower/upper later groups, no-change matches, repeat passes,
legacy tags/cleanup, and single-rule preview cleanup. The frontend component
test failed because the Live Preview request omitted `stop_processing`.
Two fixture mistakes (an alias field name and an else replacement whose
trailing space blocked legacy-tag matching) were corrected before this valid
red run; neither was counted as regression evidence.

After the engine/preview change, an added real-engine bulk-apply boundary
test failed on `LA  Clippers` vs ` LA  Clippers `; the pipeline create
boundary test passed. Removed the redundant bulk-apply trim, preserving the
existing empty-output fallback. External Dispatcharr writes are mocked in
these tests; only isolated test state is mutated.

## Verification Results

All commands ran synchronously in `/tmp/opencode/ecm-github-bugs` (frontend
commands from its `frontend/` directory). Backend subset runs selected the
main checkout's project `.venv` Python 3.12 via the gate script; the pytest
config harness created a unique isolated configuration directory per run.
Frontend used Node 24.13.0 and the worktree's lockfile-installed dependencies.

Backend red command:

```bash
scripts/backend-gate.sh --subset tests/routers/test_normalization_stop_processing.py
```

Backend green command: **932 passed**, including 28 GH858 cases, normalizer
logic, HTTP serialization, mappings, and consumer-boundary tests. This is a
focused subset, not the full backend gate.

```bash
scripts/backend-gate.sh --subset \
  tests/routers/test_normalization_stop_processing.py \
  tests/routers/test_normalization.py \
  tests/unit/test_normalization_parity.py \
  tests/unit/test_normalization_tags.py \
  tests/unit/test_normalization_strip_guard.py \
  tests/unit/test_normalization_compound_conditions.py \
  tests/unit/test_normalization_observability.py \
  tests/unit/test_normalization_confusables.py \
  tests/unit/test_normalization_safe_regex_migration.py \
  tests/unit/test_normalization_migration_backfill.py \
  tests/routers/test_channel_name_mappings.py \
  tests/routers/test_channels_normalize_preview.py \
  tests/integration/test_normalize_channel_create.py \
  tests/unit/test_stream_normalization.py \
  tests/unit/test_e9e5o_executor_normalize_disclosure.py \
  tests/routers/test_e9e5o_normalize_failure_disclosure.py \
  tests/unit/test_channel_pipeline_executor.py \
  tests/unit/test_channel_pipeline_evaluator.py
```

Frontend red command used only the new `stopProcessing.test.tsx` file in the
following selection. Green selection: **78 passed in 8 files**. These are
component/DOM and mocked-transport tests, not a deployed browser E2E proof.

```bash
export PATH=/home/lecaptainc/.local/share/fnm/node-versions/v24.13.0/installation/bin:$PATH
node_modules/.bin/vitest run \
  src/services/normalization.test.ts \
  src/components/settings/NormalizationEngineSection.stopProcessing.test.tsx \
  src/components/settings/NormalizationEngineSection.applyToChannels.test.tsx \
  src/components/settings/NormalizationEngineSection.ruleStats.test.tsx \
  src/components/settings/NormalizationEngineSection.groupCountPlural.test.tsx \
  src/hooks/useNormalizePreview.test.ts \
  src/components/NormalizeNamesModal.test.tsx \
  src/components/StreamsPane.normalization.test.tsx
node_modules/.bin/tsc --noEmit
node_modules/.bin/eslint src/components/settings/NormalizationEngineSection.tsx \
  src/components/settings/NormalizationEngineSection.stopProcessing.test.tsx \
  src/types/index.ts --max-warnings 0
node_modules/.bin/vite build
```

TypeScript and changed-file ESLint passed. Vite built successfully with its
over-500-kB chunk warning. Python syntax compilation and `git diff --check`
passed:

```bash
/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python -m compileall -q \
  backend/normalization_engine.py backend/routers/normalization.py \
  backend/tests/routers/test_normalization_stop_processing.py \
  mcp-server/tools/normalization.py
git diff --check
```

Ruff was unavailable (`python -m ruff --version`: No module named ruff).
No dependency changes, full-suite/coverage claim, live deployment, or browser
E2E claim. Channel Pipeline comparison-key folding and channel-name validity
fallbacks remain outside normalization transforms and were not rewritten.

Documentation sweep: searched repository Markdown for stop-processing,
`stop_processing`, `stopProcessing`, group-local and global-halt references.
Updated both group-local user-guide sections and the normalization reference;
updated the MCP normalization tool's parameter description. The divergence
runbook remains applicable. Channel Pipeline action descriptions and its
historical changelog entry describe a separate control and remain unchanged.
