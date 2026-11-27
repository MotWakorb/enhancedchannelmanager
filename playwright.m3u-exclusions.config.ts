import { defineConfig } from '@playwright/test'
import verification from './playwright.verification.config'

export default defineConfig({
  ...verification,
  testMatch: ['m3u-change-exclusions.spec.ts'],
  webServer: [
    verification.webServer as NonNullable<typeof verification.webServer> & { command: string },
    {
      command: `${process.env.ECM_PYTHON ?? '.venv/bin/python'} e2e/fixtures/m3u_changes_backend.py`,
      url: 'http://127.0.0.1:42872/api/m3u/changes/exclusions',
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
})
