/**
 * Base Playwright fixtures for E2E tests.
 *
 * Provides extended test fixtures with common setup and utilities.
 */
import { test as base, expect, Page } from '@playwright/test'
import { selectors, testCredentials } from './test-data'

// =============================================================================
// Authentication Helpers
// =============================================================================

/**
 * Check if the current page is a login page
 */
async function isLoginPage(page: Page): Promise<boolean> {
  // Wait a moment for the page to stabilize
  await page.waitForLoadState('domcontentloaded')

  // Check for login form indicators
  const usernameField = page.locator(selectors.loginUsername)
  const passwordField = page.locator(selectors.loginPassword)

  try {
    // Wait briefly for the form to potentially appear
    await usernameField.waitFor({ state: 'visible', timeout: 2000 })
    return true
  } catch {
    // Username field not found, check if we're already logged in
    const header = page.locator(selectors.header)
    try {
      await header.waitFor({ state: 'visible', timeout: 2000 })
      return false // Already logged in
    } catch {
      // Neither login form nor header visible - wait a bit more
      await page.waitForTimeout(1000)
      const hasUsername = await usernameField.isVisible().catch(() => false)
      const hasPassword = await passwordField.isVisible().catch(() => false)
      return hasUsername && hasPassword
    }
  }
}

/**
 * Perform login with test credentials
 */
async function performLogin(page: Page, username?: string, password?: string): Promise<void> {
  const user = username || testCredentials.username
  const pass = password || testCredentials.password

  // Wait for login form to be ready
  const usernameField = page.locator(selectors.loginUsername)
  const passwordField = page.locator(selectors.loginPassword)
  const submitButton = page.locator(selectors.loginSubmit)

  await usernameField.waitFor({ state: 'visible', timeout: 5000 })
  await passwordField.waitFor({ state: 'visible', timeout: 5000 })

  // Fill login form
  await usernameField.fill(user)
  await passwordField.fill(pass)

  // Submit
  await submitButton.click()

  // Wait for either:
  // 1. Header to appear (successful login)
  // 2. Error message (login failed)
  const header = page.locator(selectors.header)
  const errorMessage = page.locator(selectors.loginError)

  const result = await Promise.race([
    header.waitFor({ state: 'visible', timeout: 15000 }).then(() => 'success'),
    errorMessage.waitFor({ state: 'visible', timeout: 15000 }).then(() => 'error'),
  ])

  if (result === 'error') {
    const errorText = await errorMessage.textContent()
    throw new Error(`Login failed: ${errorText || 'Unknown error'} - check test credentials`)
  }
}

// =============================================================================
// Custom Fixtures
// =============================================================================

interface CustomFixtures {
  /** Page with app loaded and ready */
  appPage: Page
}

/**
 * Extended test with custom fixtures
 */
export const test = base.extend<CustomFixtures>({
  appPage: async ({ page }, use) => {
    // Navigate to app
    await page.goto('/')

    // Check if we're on a login page and need to authenticate
    if (await isLoginPage(page)) {
      await performLogin(page)
    }

    // Wait for app to be ready (header visible)
    await page.waitForSelector(selectors.header, { timeout: 15000 })

    // Use the page in tests
    await use(page)
  },
})

// Re-export expect for convenience
export { expect }

// Export login helpers for tests that need custom auth
export { isLoginPage, performLogin }

// =============================================================================
// Page Object Helpers
// =============================================================================

/**
 * Content selectors to wait for after navigating to each tab
 */
const tabContentSelectors: Record<string, string> = {
  'channel-manager': '.channels-pane',
  'settings': '.settings-tab',
  'stats': '.stats-tab',
  'm3u-manager': '.m3u-manager-tab, [class*="m3u"]',
  'm3u-changes': '.m3u-changes-tab',
  'epg-manager': '.epg-manager-tab, [class*="epg"]',
  'logo-manager': '.logo-manager-tab, [class*="logo"]',
  'guide': '.guide-tab',
  'journal': '.journal-tab',
  'auto-creation': '.auto-creation-tab, [data-testid="auto-creation-tab"]',
}

/**
 * Navigate to a specific tab
 */
export async function navigateToTab(page: Page, tabId: string): Promise<void> {
  // Wait for tab navigation to be ready
  await page.waitForSelector('.tab-navigation', { timeout: 10000 })

  const tabSelector = selectors.tabButton(tabId)
  const tabButton = page.locator(tabSelector)

  // Wait for the specific tab button to be visible
  await tabButton.waitFor({ state: 'visible', timeout: 5000 })

  // Click the tab
  await tabButton.click()

  // Wait for tab-specific content to load (more reliable than fixed timeout)
  const contentSelector = tabContentSelectors[tabId]
  if (contentSelector) {
    try {
      await page.waitForSelector(contentSelector, { timeout: 10000 })
    } catch {
      // Fallback to timeout if selector not found (tab may have different structure)
      await page.waitForTimeout(1000)
    }
  } else {
    // Fallback for unknown tabs
    await page.waitForTimeout(1000)
  }
}

/**
 * Enter edit mode on Channel Manager tab
 */
export async function enterEditMode(page: Page): Promise<void> {
  const editButton = page.locator(selectors.editModeButton)
  if (await editButton.isVisible()) {
    await editButton.click()
    await page.waitForSelector(selectors.editModeDoneButton)
  }
}

/**
 * Exit edit mode (click Done)
 */
export async function exitEditMode(page: Page): Promise<void> {
  const doneButton = page.locator(selectors.editModeDoneButton)
  if (await doneButton.isVisible()) {
    await doneButton.click()
  }
}

/**
 * Cancel edit mode (click Cancel)
 */
export async function cancelEditMode(page: Page): Promise<void> {
  const cancelButton = page.locator(selectors.editModeCancelButton)
  if (await cancelButton.isVisible()) {
    await cancelButton.click()
  }
}

/**
 * Wait for a toast notification to appear
 */
export async function waitForToast(page: Page, type?: 'success' | 'error' | 'warning'): Promise<void> {
  const selector = type ? selectors[`toast${type.charAt(0).toUpperCase() + type.slice(1)}` as keyof typeof selectors] : selectors.toast
  await page.waitForSelector(selector as string, { timeout: 10000 })
}

/**
 * Close any open modal
 */
export async function closeModal(page: Page): Promise<void> {
  const closeButton = page.locator(selectors.modalClose)
  if (await closeButton.isVisible()) {
    await closeButton.click()
    await page.waitForSelector(selectors.modal, { state: 'hidden' })
  }
}

/**
 * Fill a form field by name
 */
export async function fillFormField(page: Page, name: string, value: string): Promise<void> {
  const input = page.locator(selectors.input(name))
  await input.fill(value)
}

/**
 * Check if the app shows an error state
 */
export async function hasError(page: Page): Promise<boolean> {
  const errorElement = page.locator('.error')
  return await errorElement.isVisible()
}

/**
 * Get the current tab ID from the URL or active tab
 */
export async function getCurrentTab(page: Page): Promise<string | null> {
  const activeTab = page.locator('.tab-button.active')
  return await activeTab.getAttribute('data-tab')
}

// =============================================================================
// Assertion Helpers
// =============================================================================

/**
 * Assert that the app loaded successfully
 */
export async function assertAppLoaded(page: Page): Promise<void> {
  await expect(page.locator(selectors.headerTitle)).toBeVisible()
  await expect(page.locator(selectors.headerTitle)).toContainText('Enhanced Channel Manager')
}

/**
 * Assert that we're on a specific tab
 */
export async function assertOnTab(page: Page, tabId: string): Promise<void> {
  const tabButton = page.locator(selectors.tabButton(tabId))
  await expect(tabButton).toHaveClass(/active/)
}

/**
 * Assert no JavaScript errors in console
 */
export function setupConsoleErrorCapture(page: Page): string[] {
  const errors: string[] = []
  page.on('console', msg => {
    if (msg.type() === 'error') {
      errors.push(msg.text())
    }
  })
  return errors
}
