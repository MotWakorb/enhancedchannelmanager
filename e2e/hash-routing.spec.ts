/**
 * E2E tests for URL hash routing.
 *
 * Verifies that tab navigation is reflected in the URL hash,
 * persists across page refreshes, and supports browser back/forward.
 */
import { test, expect, navigateToTab, assertOnTab } from './fixtures/base'

test.describe('Hash Routing - Tab Navigation', () => {
  test('clicking a tab updates the URL hash', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings')
    expect(appPage.url()).toContain('#settings')

    await navigateToTab(appPage, 'm3u-manager')
    expect(appPage.url()).toContain('#m3u-manager')
  })

  test('default tab sets hash to #channel-manager', async ({ appPage }) => {
    // appPage fixture loads at / — the hook should set #channel-manager
    await appPage.waitForFunction(() => window.location.hash === '#channel-manager')
    expect(appPage.url()).toContain('#channel-manager')
  })

  test('each main tab gets correct hash', async ({ appPage }) => {
    const tabs = [
      'm3u-manager',
      'epg-manager',
      'logo-manager',
      'journal',
      'stats',
      'settings',
      'channel-manager',
    ]

    for (const tabId of tabs) {
      await navigateToTab(appPage, tabId)
      expect(appPage.url()).toContain(`#${tabId}`)
      await assertOnTab(appPage, tabId)
    }
  })
})

test.describe('Hash Routing - Refresh Persistence', () => {
  test('refreshing page restores the active tab from hash', async ({ appPage }) => {
    // Navigate to Settings
    await navigateToTab(appPage, 'settings')
    expect(appPage.url()).toContain('#settings')

    // Reload the page
    await appPage.reload({ waitUntil: 'domcontentloaded' })
    await appPage.waitForSelector('.tab-navigation', { timeout: 20000 })

    // Should still be on Settings
    await assertOnTab(appPage, 'settings')
    expect(appPage.url()).toContain('#settings')
  })

  test('refreshing preserves M3U Manager tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')

    await appPage.reload({ waitUntil: 'domcontentloaded' })
    await appPage.waitForSelector('.tab-navigation', { timeout: 20000 })

    await assertOnTab(appPage, 'm3u-manager')
    expect(appPage.url()).toContain('#m3u-manager')
  })
})

test.describe('Hash Routing - Direct URL Navigation', () => {
  test('navigating directly to a hash loads the correct tab', async ({ appPage }) => {
    // Navigate directly to a hash URL
    await appPage.goto('/#journal', { waitUntil: 'domcontentloaded' })
    await appPage.waitForSelector('.tab-navigation', { timeout: 20000 })

    await assertOnTab(appPage, 'journal')
  })

  test('invalid hash falls back to channel-manager', async ({ appPage }) => {
    await appPage.goto('/#invalid-tab-name', { waitUntil: 'domcontentloaded' })
    await appPage.waitForSelector('.tab-navigation', { timeout: 20000 })

    await assertOnTab(appPage, 'channel-manager')
  })
})

test.describe('Hash Routing - Browser Back/Forward', () => {
  test('back button navigates to previous tab', async ({ appPage }) => {
    // Navigate through several tabs
    await navigateToTab(appPage, 'm3u-manager')
    await navigateToTab(appPage, 'settings')

    // Go back
    await appPage.goBack()
    await appPage.waitForSelector('.tab-navigation', { timeout: 10000 })

    // Should be on m3u-manager
    await assertOnTab(appPage, 'm3u-manager')
    expect(appPage.url()).toContain('#m3u-manager')
  })

  test('forward button navigates to next tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'journal')
    await navigateToTab(appPage, 'stats')

    // Go back
    await appPage.goBack()
    await assertOnTab(appPage, 'journal')

    // Go forward
    await appPage.goForward()
    await appPage.waitForSelector('.tab-navigation', { timeout: 10000 })

    await assertOnTab(appPage, 'stats')
    expect(appPage.url()).toContain('#stats')
  })
})

test.describe('Hash Routing - Settings Sub-Pages', () => {
  test('settings sub-page is reflected in hash', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings')
    await appPage.waitForSelector('.settings-tab', { timeout: 15000 })

    // Click Channel Normalization nav item
    const normItem = appPage.locator('.settings-nav-item', { hasText: 'Channel Normalization' })
    await normItem.click()

    // Hash should include the sub-page
    await appPage.waitForFunction(() => window.location.hash === '#settings/normalization')
    expect(appPage.url()).toContain('#settings/normalization')
  })

  test('refreshing preserves settings sub-page', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings')
    await appPage.waitForSelector('.settings-tab', { timeout: 15000 })

    // Click Notification Settings nav item (internal page id: 'email')
    const emailItem = appPage.locator('.settings-nav-item', { hasText: 'Notification Settings' })
    await emailItem.click()

    await appPage.waitForFunction(() => window.location.hash.includes('#settings/email'))

    // Reload
    await appPage.reload({ waitUntil: 'domcontentloaded' })
    await appPage.waitForSelector('.settings-tab', { timeout: 20000 })

    // Should still be on settings/email
    expect(appPage.url()).toContain('#settings/email')
    const activeNavItem = appPage.locator('.settings-nav-item.active')
    await expect(activeNavItem).toContainText('Notification Settings')
  })

  test('direct navigation to settings sub-page works', async ({ appPage }) => {
    await appPage.goto('/#settings/normalization', { waitUntil: 'domcontentloaded' })
    await appPage.waitForSelector('.settings-tab', { timeout: 20000 })

    await assertOnTab(appPage, 'settings')
    const activeNavItem = appPage.locator('.settings-nav-item.active')
    await expect(activeNavItem).toContainText('Channel Normalization')
  })

  test('navigating between settings sub-pages updates hash', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings')
    await appPage.waitForSelector('.settings-tab', { timeout: 15000 })

    // Click through several sub-pages
    const pages = [
      { name: 'Channel Defaults', hash: '#settings/channel-defaults' },
      { name: 'Appearance', hash: '#settings/appearance' },
      { name: 'Maintenance', hash: '#settings/maintenance' },
    ]

    for (const { name, hash } of pages) {
      const navItem = appPage.locator('.settings-nav-item', { hasText: name })
      await navItem.click()
      await appPage.waitForFunction((h) => window.location.hash === h, hash)
      expect(appPage.url()).toContain(hash)
    }
  })

  test('general settings page uses #settings without sub-path', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings')
    await appPage.waitForSelector('.settings-tab', { timeout: 15000 })

    // Click General (should be the default/first item)
    const generalItem = appPage.locator('.settings-nav-item', { hasText: 'General' })
    await generalItem.click()

    // Hash should be just #settings (no /general suffix)
    await appPage.waitForFunction(() => window.location.hash === '#settings')
    expect(appPage.url()).toMatch(/#settings$/)
  })
})
