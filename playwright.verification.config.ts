import { defineConfig, devices } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// A private frontend only. All browser API/WebSocket traffic is intercepted by
// the per-test fixture; no live backend, scheduler, credentials, or database.
const output = mkdtempSync(join(tmpdir(), 'rdnia-e2e-'))
export default defineConfig({
  testDir: './e2e',
  testMatch: ['verification-helpers.spec.ts', 'task-notification-lifecycle.spec.ts', 'verification-browser.spec.ts', 'task-notifications.spec.ts'],
  timeout: 90000,
  retries: 0,
  workers: 1,
  reporter: 'list',
  outputDir: join(output, 'results'),
  use: { baseURL: 'http://127.0.0.1:42871', serviceWorkers: 'block', screenshot: 'only-on-failure', actionTimeout: 10000 },
  projects: [{ name: 'chromium', use: devices['Desktop Chrome'], metadata: { rdniaIsolated: true } }],
  webServer: {
    command: `npm run build -- --outDir "${join(output, 'dist')}" && npm run preview -- --outDir "${join(output, 'dist')}" --host 127.0.0.1 --port 42871 --strictPort`,
    cwd: './frontend',
    url: 'http://127.0.0.1:42871',
    reuseExistingServer: false,
    timeout: 120000,
  },
})
