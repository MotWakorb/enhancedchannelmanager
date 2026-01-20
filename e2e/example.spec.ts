/**
 * Example E2E test to verify Playwright setup.
 *
 * This test demonstrates basic Playwright usage patterns.
 * Replace with actual E2E tests for your application.
 */
import { test, expect } from '@playwright/test'

test.describe('Application Smoke Test', () => {
  test('should load the application', async ({ page }) => {
    // Navigate to the app
    await page.goto('/')

    // Wait for app to be ready
    await page.waitForLoadState('domcontentloaded')

    // Check that the page has loaded (look for header or main element)
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 })
  })

  test('should have the correct title', async ({ page }) => {
    await page.goto('/')

    // The app should have Enhanced Channel Manager in the heading
    await expect(page.locator('h1')).toContainText('Enhanced Channel Manager')
  })
})
