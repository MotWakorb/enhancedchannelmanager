/**
 * E2E tests for Backup & Restore feature.
 *
 * Tests:
 * - Backup & Restore nav item visible in Settings (admin only)
 * - Create Backup button triggers download
 * - Restore section shows warning and file input
 * - Restore from Backup option in initial setup modal
 */
import { test, expect, navigateToTab } from './fixtures/base'

/**
 * Navigate to Settings > Backup & Restore page
 */
async function navigateToBackupRestore(page: any): Promise<void> {
  await navigateToTab(page, 'settings')
  await page.waitForTimeout(500)

  const navItem = page.locator('.settings-nav-item:has-text("Backup & Restore")')
  await navItem.waitFor({ state: 'visible', timeout: 10000 })
  await navItem.click()
  await page.waitForTimeout(500)
}

// =============================================================================
// Settings Navigation - Backup & Restore Tab
// =============================================================================

test.describe('Backup & Restore Settings Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToBackupRestore(appPage)
  })

  test('backup & restore nav item is visible', async ({ appPage }) => {
    const navItem = appPage.locator('.settings-nav-item:has-text("Backup & Restore")')
    await expect(navItem).toBeVisible()
    await expect(navItem).toHaveClass(/active/)
  })

  test('page header is displayed', async ({ appPage }) => {
    const header = appPage.locator('h2:has-text("Backup & Restore")')
    await expect(header).toBeVisible()
  })

  test('create backup section is displayed', async ({ appPage }) => {
    const heading = appPage.locator('h3:has-text("Create Backup")')
    await expect(heading).toBeVisible()
  })

  test('download backup button is present', async ({ appPage }) => {
    const button = appPage.locator('button:has-text("Download Backup")')
    await expect(button).toBeVisible()
  })

  test('restore section is displayed', async ({ appPage }) => {
    const heading = appPage.locator('h3:has-text("Restore from Backup")')
    await expect(heading).toBeVisible()
  })

  test('restore warning is shown', async ({ appPage }) => {
    const warning = appPage.locator('.restore-warning')
    await expect(warning).toBeVisible()
    await expect(warning).toContainText('replace all current settings')
  })

  test('file input accepts zip files', async ({ appPage }) => {
    const fileInput = appPage.locator('input[type="file"][accept=".zip"]')
    await expect(fileInput).toBeVisible()
  })

  test('restore button is present', async ({ appPage }) => {
    const button = appPage.locator('.backup-restore-section button:has-text("Restore")')
    await expect(button).toBeVisible()
  })

  test('backup description mentions what is included', async ({ appPage }) => {
    const description = appPage.locator('.backup-card-description').first()
    await expect(description).toContainText('settings')
    await expect(description).toContainText('database')
  })
})

// =============================================================================
// Backup Download
// =============================================================================

test.describe('Backup Download', () => {
  test('download backup triggers file download', async ({ appPage }) => {
    await navigateToBackupRestore(appPage)

    // Listen for download event
    const downloadPromise = appPage.waitForEvent('download', { timeout: 30000 })

    // Click download button
    const button = appPage.locator('button:has-text("Download Backup")')
    await button.click()

    // Wait for download to start
    const download = await downloadPromise
    const filename = download.suggestedFilename()

    // Verify filename pattern
    expect(filename).toMatch(/^ecm-backup-\d{4}-\d{2}-\d{2}.*\.zip$/)
  })
})

// =============================================================================
// Hash Routing
// =============================================================================

test.describe('Backup Restore Hash Route', () => {
  test('navigating to #settings/backup-restore loads the page', async ({ appPage }) => {
    await appPage.goto('/#settings/backup-restore')
    await appPage.waitForTimeout(1000)

    const header = appPage.locator('h2:has-text("Backup & Restore")')
    await expect(header).toBeVisible({ timeout: 10000 })
  })
})
