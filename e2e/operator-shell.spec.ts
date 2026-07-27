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

async function expectContentWithinMain(page: Page, route: 'channel-manager' | 'guide') {
  const selectors = ['.route-page-header', '#main-content h1', ...(route === 'channel-manager'
    ? ['.route-page-header .enter-edit-mode-btn', '.channel-manager-tab', '.channels-pane', '.streams-pane', '.channels-pane h2', '.streams-pane h2', 'input[placeholder="Search streams..."]']
    : ['.guide-tab', '.guide-controls', 'button[title="Refresh program data"]', 'button[title="Print channel guide"]', '.guide-container', '.guide-footer'])]

  for (const selector of selectors) {
    const item = page.locator(selector).first()
    await expect(item).toBeVisible()
    await item.scrollIntoViewIfNeeded()
    const bounds = await item.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      const main = document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect()
      return {
        withinMainX: rect.left >= main.left - 1 && rect.right <= main.right + 1,
        intersectsViewportY: rect.bottom > 0 && rect.top < window.innerHeight,
      }
    })
    expect(bounds.withinMainX, `${selector} must stay within usable main width`).toBe(true)
    expect(bounds.intersectsViewportY, `${selector} must remain vertically reachable`).toBe(true)
  }
}

const routeConsumers = [
  { name: 'Dashboard', heading: 'OVERVIEW / DASHBOARD', settled: '.dashboard-route' },
  { name: 'Channel Manager', heading: 'OPERATIONS / CHANNEL MANAGER', settled: '.enter-edit-mode-btn' },
  { name: 'Guide', heading: 'OPERATIONS / GUIDE', settled: 'button[title="Refresh program data"]' },
  { name: 'M3U Manager', heading: 'OPERATIONS / M3U MANAGER', settled: 'button:has-text("Add M3U Account")' },
  { name: 'EPG Manager', heading: 'OPERATIONS / EPG MANAGER', settled: 'button:has-text("Add Standard EPG")' },
  { name: 'Logo Manager', heading: 'OPERATIONS / LOGO MANAGER', settled: 'button:has-text("Add Logo")' },
  { name: 'Channel Pipeline', heading: 'AUTOMATION / CHANNEL PIPELINE', settled: 'button[aria-label="Retry"]' },
  { name: 'M3U Changes', heading: 'AUTOMATION / M3U CHANGES', settled: 'button:has-text("Refresh")' },
  { name: 'Stats', heading: 'INSIGHTS / STATS', settled: 'button:has-text("Refresh")' },
  { name: 'Journal', heading: 'INSIGHTS / JOURNAL', settled: 'button:has-text("Refresh")' },
  { name: 'Settings', heading: 'SYSTEM / SETTINGS', settled: 'button:has-text("Save Settings")' },
] as const

async function expectSettledRoute(page: Page, consumer: typeof routeConsumers[number]) {
  const heading = page.locator('#main-content h1')
  await expect(heading).toHaveText(consumer.heading)
  await expect(heading).toHaveCount(1)
  await expect(page.locator('#main-content .tab-loading')).toHaveCount(0)
  const settled = page.locator(consumer.settled).first()
  await expect(settled).toBeVisible()
  return settled
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
        await expectContentWithinMain(appPage, route as 'channel-manager' | 'guide')

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
        await expectContentWithinMain(appPage, route as 'channel-manager' | 'guide')
      })
    }

    test('all primary route consumers preserve hierarchy and required controls or source-backed recovery', async ({ appPage }) => {
      await dismissFirstRunPromptIfPresent(appPage)
      for (const consumer of routeConsumers) {
        await appPage.getByRole('link', { name: consumer.name }).click()
        const settled = await expectSettledRoute(appPage, consumer)
        await settled.scrollIntoViewIfNeeded()
        expect(await settled.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          const main = document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect()
          return rect.left >= main.left - 1 && rect.right <= main.right + 1
            && document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
        })).toBe(true)
      }
    })
  })
}

test.describe('operator shell at 200% equivalent', () => {
  test.use({ viewport: { width: 640, height: 360 } })

  test('all primary routes settle with their exact control and remain horizontally usable', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    for (const consumer of routeConsumers) {
      await appPage.getByRole('link', { name: consumer.name }).click()
      const settled = await expectSettledRoute(appPage, consumer)
      await settled.scrollIntoViewIfNeeded()
      const overflow = await appPage.evaluate(() => ({
        fits: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
        offenders: [...document.querySelectorAll<HTMLElement>('body *')]
          .filter((element) => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
          .slice(0, 8)
          .map((element) => `${element.tagName}.${element.className}`),
      }))
      expect(overflow.fits, `${consumer.name} must not cause document-level horizontal overflow; internal overflow: ${overflow.offenders.join(', ')}`).toBe(true)
    }
  })
})

test.describe('operator shell navigation behavior', () => {
  test.use({ serviceWorkers: 'block' })

  test('staged contextual navigation keeps editing or discards before landing on the exact settings page', async ({ page }) => {
    await page.addInitScript(() => localStorage.clear())
    await page.route(/\/api\/settings(?:\/|\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          configured: true,
          default_channel_profile_ids: [],
          stream_sort_priority: [],
          stream_sort_enabled: {},
          m3u_account_priorities: {},
          custom_network_prefixes: [],
          hide_auto_sync_groups: false,
          theme: 'dark',
          date_format: 'en-US',
        }),
      })
    })
    await page.route(/\/api\/channel-groups(?:\/|\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 91, name: 'Seeded Group', channel_count: 1 }]),
      })
    })
    await page.route(/\/api\/channels(?:\/|\?|$)/, async (route) => {
      if (route.request().method() !== 'GET') {
        await route.continue()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          count: 1,
          next: null,
          previous: null,
          results: [{
            id: 701,
            channel_number: 7,
            name: 'Seeded News',
            channel_group_id: 91,
            tvg_id: null,
            tvc_guide_stationid: null,
            epg_data_id: null,
            streams: [],
            stream_profile_id: null,
            uuid: 'operator-shell-seeded-channel',
            logo_id: null,
            auto_created: false,
            auto_created_by: null,
            auto_created_by_name: null,
          }],
        }),
      })
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.tab-navigation')).toBeVisible()
    await dismissFirstRunPromptIfPresent(page)
    await page.getByText('Seeded Group', { exact: true }).click()
    await expect(page.locator('.channel-item', { hasText: 'Seeded News' })).toBeVisible()

    await page.getByRole('button', { name: 'Edit Mode' }).click()
    const channelName = page.locator('.channel-item', { hasText: 'Seeded News' }).locator('.channel-name')
    await channelName.dblclick()
    const nameInput = page.locator('.channel-name-input')
    await nameInput.fill('Seeded News Updated')
    await nameInput.press('Enter')
    await expect(page.getByText('1 change', { exact: true })).toBeVisible()

    await page.getByRole('link', { name: 'Channel default settings' }).click()
    await expect(page.getByRole('heading', { name: 'Exit Edit Mode' })).toBeVisible()
    await page.getByRole('button', { name: 'Keep Editing' }).click()
    await expect(page).toHaveURL(/#channel-manager$/)
    await expect(page.getByText('1 change', { exact: true })).toBeVisible()
    await expect(page.locator('.channel-item', { hasText: 'Seeded News Updated' })).toBeVisible()

    await page.getByRole('link', { name: 'Channel default settings' }).click()
    await page.getByRole('button', { name: 'Discard' }).click()
    await expect(page).toHaveURL(/#settings\/channel-defaults$/)
    await expect(page.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS')
    await expect(page.getByRole('heading', { name: 'Exit Edit Mode' })).toHaveCount(0)
  })

  test('enabled route links retain the browser new-tab affordance', async ({ appPage, context }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const [newTab] = await Promise.all([
      context.waitForEvent('page'),
      appPage.getByRole('link', { name: 'Guide' }).click({ modifiers: ['Control'] }),
    ])
    await newTab.waitForLoadState('domcontentloaded')
    await expect(newTab).toHaveURL(/#guide$/)
    await newTab.close()
    await expect(appPage).toHaveURL(/#channel-manager$/)
  })

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
      await expect(appPage.locator('#main-content h1')).toBeFocused()
      await expect(appPage.locator('#main-content h1')).toHaveText(new RegExp(` / ${name.toUpperCase()}$`))
      await expect(appPage.locator('#main-content h1')).toHaveCount(1)
      await expect(appPage).toHaveTitle(`${name} | Enhanced Channel Manager`)
    }
  })

  test('contextual settings links use stable current hashes', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const link = appPage.getByRole('link', { name: 'Channel default settings' })
    await expect(link).toHaveAttribute('href', '#settings/channel-defaults')
    await link.click()
    await expect(appPage).toHaveURL(/#settings\/channel-defaults$/)
    await expect(appPage.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS')
  })

  test('contextual settings navigation cleanly exits an edit session with no staged changes', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.getByRole('button', { name: 'Edit Mode' }).click()
    await expect(appPage.getByRole('button', { name: 'Done' })).toBeVisible()
    await appPage.getByRole('link', { name: 'Channel default settings' }).click()
    await expect(appPage).toHaveURL(/#settings\/channel-defaults$/)
    await expect(appPage.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS')
    await expect(appPage.getByRole('button', { name: 'Done' })).toHaveCount(0)
    await appPage.getByRole('link', { name: 'Channel Manager' }).click()
    await expect(appPage.getByRole('button', { name: 'Edit Mode' })).toBeVisible()
  })

  test('the Channel Manager primary action belongs to its page header', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const action = appPage.locator('.route-page-header .enter-edit-mode-btn')
    await expect(action).toBeVisible()
    await expect(appPage.locator('header.header .enter-edit-mode-btn')).toHaveCount(0)
    const followsHeading = await appPage.evaluate(() => {
      const heading = document.querySelector('#main-content h1')
      const button = document.querySelector('.route-page-header .enter-edit-mode-btn')
      return Boolean(
        heading
        && button
        && (heading.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING),
      )
    })
    expect(followsHeading).toBe(true)
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
    // A 1280x720 browser at 200% zoom exposes a 640x360 CSS layout viewport.
    await appPage.setViewportSize({ width: 640, height: 360 })
    for (const link of await appPage.getByRole('navigation', { name: 'Primary' }).getByRole('link').all()) {
      await link.scrollIntoViewIfNeeded()
      await expect(link).toBeVisible()
      await link.focus()
      await expect(link).toBeFocused()
    }
    const metrics = await shellMetrics(appPage)
    expect(metrics.noSidebarXOverflow).toBe(true)
    expect(metrics.noDocumentXOverflow).toBe(true)

    await appPage.getByRole('link', { name: 'Channel Manager' }).press('Enter')
    await expectContentWithinMain(appPage, 'channel-manager')
    await appPage.getByRole('link', { name: 'Guide' }).press('Enter')
    await expectContentWithinMain(appPage, 'guide')
  })
})
