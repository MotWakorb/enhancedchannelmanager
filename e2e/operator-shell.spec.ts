import { test, expect, type Page } from './fixtures/base'

async function dismissFirstRunPromptIfPresent(page: Page) {
  const close = page.getByRole('button', { name: 'Close' })
  if (await close.isVisible().catch(() => false)) await close.click()
}

async function shellMetrics(page: Page) {
  return page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!
    const main = document.querySelector<HTMLElement>('#main-content')!
    const links = [...sidebar.querySelectorAll<HTMLElement>('.navigation-destination')]
    const sidebarRect = sidebar.getBoundingClientRect()
    const mainRect = main.getBoundingClientRect()
    return {
      width: sidebarRect.width,
      noSidebarXOverflow: sidebar.clientWidth === sidebar.scrollWidth,
      mainClear: mainRect.left >= sidebarRect.right,
      noDocumentXOverflow: document.documentElement.clientWidth === document.documentElement.scrollWidth,
      targetsPractical: links.every((link) => link.getBoundingClientRect().height >= 40),
      labelsHidden: [...sidebar.querySelectorAll<HTMLElement>('.navigation-label, .navigation-group h2')]
        .every((label) => getComputedStyle(label).display === 'none'),
      iconsCentered: links.every((link) => {
        const icon = link.querySelector<HTMLElement>('.material-icons')!
        const linkRect = link.getBoundingClientRect()
        const iconRect = icon.getBoundingClientRect()
        return Math.abs((linkRect.left + linkRect.width / 2) - (iconRect.left + iconRect.width / 2)) <= 1
      }),
    }
  })
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
  test.describe(`operator shell geometry at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport })

    for (const route of ['channel-manager', 'guide']) {
      test(`${route} clears expanded and collapsed navigation`, async ({ appPage }) => {
        await dismissFirstRunPromptIfPresent(appPage)
        await appPage.getByRole('link', { name: route === 'guide' ? 'Guide' : 'Channel Manager' }).click()

        const expanded = await shellMetrics(appPage)
        expect(expanded).toMatchObject({
          width: 244,
          noSidebarXOverflow: true,
          mainClear: true,
          noDocumentXOverflow: true,
          targetsPractical: true,
          labelsHidden: false,
        })

        await appPage.getByRole('button', { name: 'Collapse navigation' }).click()
        await expect(appPage.locator('.primary-sidebar')).toHaveCSS('width', '68px')
        const collapsed = await shellMetrics(appPage)
        expect(collapsed).toMatchObject({
          width: 68,
          noSidebarXOverflow: true,
          mainClear: true,
          noDocumentXOverflow: true,
          targetsPractical: true,
          labelsHidden: true,
          iconsCentered: true,
        })
      })
    }
  })
}

test.describe('operator shell navigation behavior', () => {
  test('all primary routes are real links and keyboard reachable', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const expected = [
      ['Dashboard', '#dashboard'], ['Channel Manager', '#channel-manager'], ['Guide', '#guide'],
      ['M3U Manager', '#m3u-manager'], ['EPG Manager', '#epg-manager'], ['Logo Manager', '#logo-manager'],
      ['Channel Pipeline', '#channel-pipeline'], ['M3U Changes', '#m3u-changes'], ['Stats', '#stats'],
      ['Journal', '#journal'], ['Settings', '#settings'],
    ]
    for (const [name, hash] of expected) {
      const link = appPage.getByRole('link', { name })
      await expect(link).toHaveAttribute('href', hash)
      await link.focus()
      await expect(link).toBeFocused()
      await appPage.keyboard.press('Enter')
      await expect(appPage).toHaveURL(new RegExp(`${hash.replace('#', '#')}$`))
      await expect(appPage.locator('.route-page-heading')).toBeFocused()
      await expect(appPage).toHaveTitle(`${name} | Enhanced Channel Manager`)
    }
  })

  test('skip link focuses main without changing a non-default route or history', async ({ appPage }) => {
    await appPage.goto('/#guide', { waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    const before = await appPage.evaluate(() => ({ href: location.href, length: history.length }))
    await appPage.keyboard.press('Tab')
    await expect(appPage.getByRole('link', { name: 'Skip to main content' })).toBeFocused()
    await appPage.keyboard.press('Enter')
    await expect(appPage.locator('#main-content')).toBeFocused()
    expect(await appPage.evaluate(() => ({ href: location.href, length: history.length }))).toEqual(before)
  })

  test('persists collapse and safely restores icon-only mode', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.getByRole('button', { name: 'Collapse navigation' }).click()
    await appPage.reload({ waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    await expect(appPage.locator('.primary-sidebar')).toHaveClass(/is-collapsed/)
    await expect(appPage.getByRole('button', { name: 'Expand navigation' })).toBeVisible()
  })

  test('canonicalizes aliases and does not hijack focus on browser history', async ({ appPage }) => {
    await appPage.goto('/#auto-creation', { waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    await expect(appPage).toHaveURL(/#channel-pipeline$/)
    await appPage.getByRole('link', { name: 'Stats' }).click()
    await appPage.getByRole('link', { name: 'Journal' }).click()
    await appPage.getByRole('button', { name: 'Notifications' }).focus()
    await appPage.goBack()
    await expect(appPage.getByRole('link', { name: 'Stats' })).toHaveAttribute('aria-current', 'page')
    await expect(appPage.getByRole('button', { name: 'Notifications' })).toBeFocused()
    await appPage.goForward()
    await expect(appPage.getByRole('link', { name: 'Journal' })).toHaveAttribute('aria-current', 'page')
  })

  test('remains reachable with reduced motion and at 200% zoom', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.emulateMedia({ reducedMotion: 'reduce' })
    await expect(appPage.locator('.primary-sidebar')).toHaveCSS('transition-duration', '0s')
    await appPage.evaluate(() => { document.documentElement.style.zoom = '2' })
    for (const link of await appPage.getByRole('navigation', { name: 'Primary' }).getByRole('link').all()) {
      await link.scrollIntoViewIfNeeded()
      await expect(link).toBeVisible()
      await link.focus()
      await expect(link).toBeFocused()
    }
    expect((await shellMetrics(appPage)).noSidebarXOverflow).toBe(true)
  })
})
