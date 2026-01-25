# CSS Extraction Workflow

Step-by-step workflow for safely extracting and consolidating CSS patterns.

## Overview

This workflow ensures CSS migrations are safe, testable, and reversible. Each extraction follows the same pattern to minimize risk and catch visual regressions early.

## Prerequisites

Before starting any CSS extraction work:

1. **Visual regression tests in place** (bead 5h15i)
2. **CSS smoke test script available** (bead bvcy7)
3. **shared/common.css created** (bead 5n17)

## Workflow for Each CSS Extraction

### 1. Pre-flight

```bash
# Ensure clean working state
git status

# Run full E2E suite to establish baseline
npm run test:e2e

# Generate baseline screenshots if not already done
npm run test:css-smoke:update

# Commit current state
git add -A && git commit -m "Pre-extraction baseline"
```

### 2. Extract to shared/common.css

1. **Identify the pattern** - Find all files with duplicate CSS
2. **Copy to shared/common.css** - Use generic class names:
   - Use `.btn-primary` not `.modal-btn-primary`
   - Use `.form-group` not `.settings-form-group`
3. **DO NOT modify component CSS files yet**
4. **Verify no changes** - Run smoke test (should pass, no visual changes):
   ```bash
   npm run test:css-smoke
   ```

### 3. Migrate ONE Component First

Pick the simplest component using this pattern:

1. **Add shared class alongside existing class**:
   ```tsx
   // Before
   <button className="old-btn">Click</button>

   // After (dual class during migration)
   <button className="old-btn btn-primary">Click</button>
   ```

2. **Import shared/common.css** if not already imported

3. **Run smoke test for quick feedback**:
   ```bash
   npm run test:css-smoke
   ```

4. **If smoke passes, run targeted E2E**:
   ```bash
   npx playwright test e2e/[component].spec.ts
   ```

5. **If tests pass, commit**:
   ```bash
   git add -A && git commit -m "Migrate [Component] to shared [pattern]"
   ```

### 4. Migrate Remaining Components

Repeat step 3 for each component:

```bash
# Quick iteration cycle
# 1. Make change
# 2. Run smoke test
npm run test:css-smoke

# 3. If pass, commit
git add -A && git commit -m "Migrate [Component] to shared [pattern]"

# 4. If fail, investigate or revert
git checkout -- .
```

**Important**: Commit after EACH successful migration, not in batches.

### 5. Cleanup - Remove Duplicate CSS

After ALL components are migrated:

1. **Remove old duplicate CSS** from component files
2. **Run smoke test first**:
   ```bash
   npm run test:css-smoke
   ```
3. **Run full E2E suite**:
   ```bash
   npm run test:e2e
   ```
4. **Run visual comparison**:
   ```bash
   npx playwright test e2e/visual-regression.spec.ts
   ```
5. **Commit cleanup**:
   ```bash
   git add -A && git commit -m "Remove duplicate [pattern] CSS from components"
   ```

### 6. Close Bead

```bash
# Document what was changed
bd update <id> --description "Extracted [pattern] to shared/common.css. Files modified: [list]"

# Close the bead
bd close <id>
```

## Quick Reference Commands

```bash
# CSS smoke test (fast, ~30 seconds)
npm run test:css-smoke

# Update smoke test baselines
npm run test:css-smoke:update

# Full E2E suite
npm run test:e2e

# Visual regression tests
npx playwright test e2e/visual-regression.spec.ts

# View test report with diffs
npx playwright show-report

# Shell scripts (Linux/Mac)
./scripts/css-smoke-test.sh
./scripts/css-smoke-test.sh --update

# Batch scripts (Windows)
scripts\css-smoke-test.bat
scripts\css-smoke-test.bat --update
```

## Troubleshooting

### Smoke test fails after extraction

1. Check if the shared CSS has different values than the original
2. Compare specificity - shared styles might be overridden
3. Check import order - shared/common.css should load before component CSS

### Visual diff shows unexpected changes

1. Run `npx playwright show-report` to see exact differences
2. If changes are intentional: `npm run test:css-smoke:update`
3. If changes are bugs: revert and investigate

### Component looks different after removing old CSS

1. The shared CSS might be missing some properties
2. Check for component-specific overrides that were removed
3. Add component-specific extensions to the component's CSS file

## Best Practices

1. **Small commits** - One component per commit during migration
2. **Test frequently** - Run smoke test after every change
3. **Generic names** - Use `.btn-primary` not `.modal-btn-primary`
4. **Preserve specificity** - Shared styles should be base, components extend
5. **Document gotchas** - Note any issues in the bead description
