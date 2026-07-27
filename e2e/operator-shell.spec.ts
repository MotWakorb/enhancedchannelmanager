import { test, expect } from './fixtures/base'

async function dismissFirstRunPromptIfPresent(page: import('@playwright/test').Page) {
  const close = page.getByRole('button', { name: 'Close' })
  if (await close.isVisible().catch(() => false)) {
    await close.click()
  }
}

const viewports = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
]

for (const viewport of viewports) {
  test.describe(`operator shell at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport })

    test('keeps primary navigation and content within their layout bounds', async ({ appPage }) => {
      const navigation = appPage.getByRole('navigation', { name: 'Primary' })
      await expect(navigation).toBeVisible()

      const metrics = await appPage.evaluate(() => {
        const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!
        const main = document.querySelector<HTMLElement>('#main-content')!
        const sidebarRect = sidebar.getBoundingClientRect()
        const mainRect = main.getBoundingClientRect()
        return {
          sidebarWidth: sidebarRect.width,
          sidebarOverflow: sidebar.scrollWidth - sidebar.clientWidth,
          mainStartsAfterSidebar: mainRect.left >= sidebarRect.right,
          documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        }
      })

      expect(metrics.sidebarWidth).toBe(244)
      expect(metrics.sidebarOverflow).toBe(0)
      expect(metrics.mainStartsAfterSidebar).toBe(true)
      expect(metrics.documentOverflow).toBe(0)
    })
  })
}

test.describe('operator shell navigation behavior', () => {
  test('uses approved labels and supports keyboard activation', async ({ appPage }) => {
    const navigation = appPage.getByRole('navigation', { name: 'Primary' })
    const labels = await navigation.getByRole('button').allTextContents()
    expect(labels.map((label) => label.trim()).filter(Boolean)).toEqual([
      'dashboardDashboard',
      'tvChannel Manager',
      'grid_onGuide',
      'playlist_playM3U Manager',
      'scheduleEPG Manager',
      'imageLogo Manager',
      'auto_fix_highChannel Pipeline',
      'compare_arrowsM3U Changes',
      'analyticsStats',
      'historyJournal',
      'settingsSettings',
    ])

    const guide = navigation.getByRole('button', { name: 'Guide' })
    await guide.focus()
    await appPage.keyboard.press('Enter')
    await expect(guide).toHaveAttribute('aria-current', 'page')
    await expect(appPage).toHaveURL(/#guide$/)
  })

  test('persists collapse and restores a truly icon-only rail', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.getByRole('button', { name: 'Collapse navigation' }).click()
    await expect(appPage.locator('.primary-sidebar')).toHaveClass(/is-collapsed/)
    await expect(appPage.locator('.navigation-label:visible')).toHaveCount(0)
    await expect(appPage.locator('.navigation-group h2:visible')).toHaveCount(0)
    await expect(appPage.locator('.primary-sidebar')).toHaveCSS('width', '68px')

    await appPage.reload({ waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    await expect(appPage.locator('.primary-sidebar')).toHaveClass(/is-collapsed/)
    await expect(appPage.getByRole('button', { name: 'Expand navigation' })).toBeVisible()
  })

  test('canonicalizes legacy hashes and preserves back/forward history', async ({ appPage }) => {
    await appPage.goto('/#auto-creation', { waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    await expect(appPage).toHaveURL(/#channel-pipeline$/)
    await expect(appPage.getByRole('button', { name: 'Channel Pipeline' })).toHaveAttribute('aria-current', 'page')

    await appPage.getByRole('button', { name: 'Stats' }).click()
    await appPage.getByRole('button', { name: 'Journal' }).click()
    await appPage.goBack()
    await expect(appPage.getByRole('button', { name: 'Stats' })).toHaveAttribute('aria-current', 'page')
    await appPage.goForward()
    await expect(appPage.getByRole('button', { name: 'Journal' })).toHaveAttribute('aria-current', 'page')
  })
})
