import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright config for visual-fixture component screenshots (bd-lln2x).
 *
 * Separate from the main `playwright.config.ts` because the surface
 * under test is fundamentally different:
 *
 *   - Main config: live ECM container at :6100, full app + auth + DB.
 *     E2E tests; not run in CI (deferred per bd-2lw25).
 *   - This config: standalone Vite-served fixture HTML mounting a
 *     single React component. No backend needed, runs in CI as a gate.
 *
 * The fixture pages live under `frontend/visual-fixture-*.html` and
 * mount one component each via `frontend/src/visual-fixture/`. They
 * are dev-server-only — `vite build` does not include them in `dist/`,
 * so production bundles are unaffected.
 *
 * Usage:
 *   npx playwright test --config=playwright.visual.config.ts
 *   npx playwright test --config=playwright.visual.config.ts --update-snapshots
 *
 * Snapshot tolerance:
 *   maxDiffPixels: 500 — empirical: the bd-wdpve regression (column
 *   labels rotated -45° instead of +45°) produces 3360 differing
 *   pixels against the +45° baseline; well above 500. Cross-OS
 *   anti-aliasing on rotated text labels has been measured at <100
 *   differing pixels for the same image rendered on the same browser
 *   build (font hinting noise). 500 sits comfortably between the two,
 *   catching any rotation/anchor/positioning regression without
 *   flaking on font-rendering variance. Using `maxDiffPixels`
 *   (absolute count) instead of `maxDiffPixelRatio` keeps the
 *   threshold stable when the heatmap dimensions change.
 */
export default defineConfig({
  // Self-contained directory — does not pick up the main e2e/*.spec.ts
  // files at the repo root.
  testDir: './e2e/visual',

  testMatch: '**/*.spec.ts',

  // Visual screenshots take longer than DOM assertions; bump from 30s.
  timeout: 60 * 1000,

  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      // Empirical: bd-wdpve regression = 3360 diff pixels;
      // cross-OS font noise = <100 diff pixels. 500 sits between.
      // See module docstring for derivation.
      maxDiffPixels: 500,
      animations: 'disabled',
      // Force consistent scale across OSes.
      scale: 'css',
    },
    toMatchSnapshot: {
      maxDiffPixels: 500,
    },
  },

  // Snapshots live next to the spec — keeps the baseline alongside the
  // test that produces it.
  snapshotPathTemplate:
    '{testDir}/__screenshots__/{testFileName}/{arg}{ext}',

  // Visual screenshots are deterministic when serial; running them in
  // parallel risks dev-server contention.
  fullyParallel: false,
  workers: 1,

  // Fail CI build on stray test.only.
  forbidOnly: !!process.env.CI,

  retries: process.env.CI ? 1 : 0,

  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'on-failure' }]],

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'off',
    // Pin viewport so SVG layout has stable dimensions across runs.
    viewport: { width: 1400, height: 900 },
    // Pin color scheme so theme variables resolve identically on every OS.
    colorScheme: 'dark',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    // Boot the frontend Vite dev server on :5173. The fixture HTML
    // (visual-fixture-heatmap.html) is served alongside index.html
    // because Vite's dev server resolves any HTML at the project root.
    command: 'npm run dev',
    cwd: './frontend',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60 * 1000,
  },

  outputDir: './test-results-visual',
})
