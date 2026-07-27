import { defineConfig, devices } from '@playwright/test'
import { resolveE2EBaseURL } from './playwright-base-url.mjs'

/**
 * Playwright E2E test configuration.
 *
 * Environment variables:
 *   E2E_BASE_URL - Base URL for tests (default: http://localhost:6100)
 *   E2E_START_SERVER - Set to 'true' to auto-start dev server (default: false)
 *   E2E_EXACT_BUILD - Build and serve the checked-out source on an isolated
 *                     preview port instead of using a possibly stale dev server
 *
 * Usage:
 *   npx playwright test                              # Test against running app at :6100
 *   E2E_BASE_URL=http://localhost:5173 npx playwright test  # Test against dev server
 *   E2E_START_SERVER=true npx playwright test        # Auto-start dev server
 *
 * @see https://playwright.dev/docs/test-configuration
 */

const exactBuild = process.env.E2E_EXACT_BUILD === 'true'
const baseURL = resolveE2EBaseURL(exactBuild, process.env.E2E_BASE_URL)
const startServer = process.env.E2E_START_SERVER === 'true'

export default defineConfig({
  // Test directory
  testDir: './e2e',

  // Test file patterns
  testMatch: '**/*.spec.ts',

  // Maximum time a test can run
  timeout: 30 * 1000,

  // Maximum time to wait for expect assertions
  expect: {
    timeout: 5000,
    // Snapshot comparison settings for visual regression
    toHaveScreenshot: {
      // Allow small pixel differences for anti-aliasing
      maxDiffPixelRatio: 0.05,
      // Animation tolerance
      animations: 'disabled',
    },
    toMatchSnapshot: {
      maxDiffPixelRatio: 0.05,
    },
  },

  // Snapshot directory for baseline images
  snapshotDir: './e2e/snapshots',
  snapshotPathTemplate: '{snapshotDir}/{testFileDir}/{testFileName}-snapshots/{arg}{ext}',

  // Run tests in parallel
  fullyParallel: true,

  // Fail build on CI if you accidentally left test.only in source
  forbidOnly: !!process.env.CI,

  // Retry failed tests (helps with timing flakiness)
  retries: process.env.CI ? 2 : 1,

  // Number of parallel workers (limit locally to reduce resource contention)
  workers: process.env.CI ? 1 : 4,

  // Reporter configuration
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'on-failure' }]],

  // Shared settings for all tests
  use: {
    // Base URL for actions like page.goto('/')
    baseURL,

    // Collect trace when retrying failed tests
    trace: 'on-first-retry',

    // Screenshot on failure
    screenshot: 'only-on-failure',

    // Video recording on failure
    video: 'on-first-retry',
  },

  // Browser projects
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // Firefox and WebKit available for broader testing
    // Uncomment when needed:
    // {
    //   name: 'firefox',
    //   use: { ...devices['Desktop Firefox'] },
    // },
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },
  ],

  // Web server configuration - only starts if E2E_START_SERVER=true
  ...(startServer && {
    webServer: {
      command: exactBuild
        ? 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173'
        : 'npm run dev',
      cwd: './frontend',
      url: exactBuild ? 'http://127.0.0.1:4173' : 'http://localhost:5173',
      reuseExistingServer: exactBuild ? false : !process.env.CI,
      timeout: 120 * 1000,
    },
  }),

  // Output directory for test artifacts
  outputDir: './test-results',
})
