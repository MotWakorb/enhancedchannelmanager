import { test, expect, type Page } from './fixtures/base'
import type { TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const operatorReleaseArtifactDirectory = resolve(process.cwd(), 'test-results/operator-workspace-release')

async function dismissFirstRunPromptIfPresent(page: Page) {
  const close = page.getByRole('button', { name: 'Close' })
  if (await close.isVisible().catch(() => false)) await close.click()
}

async function captureOperatorReleaseArtifact(
  page: Page,
  testInfo: TestInfo,
  viewport: { width: number; height: number },
  state: string,
) {
  await dismissReleaseToasts(page)
  await expect(page.locator('.toast')).toHaveCount(0)
  const name = `operator-workspace--${viewport.width}x${viewport.height}--${state}.png`
  await mkdir(operatorReleaseArtifactDirectory, { recursive: true })
  const path = resolve(operatorReleaseArtifactDirectory, name)
  const expectedCollapsed = state.includes('collapsed')
  const expectedSidebarWidth = expectedCollapsed ? 68 : 244
  const fontReadiness = await page.evaluate(async () => {
    await document.fonts.ready
    await document.fonts.load('24px "Material Icons"', 'check')
    return {
      fontAvailable: document.fonts.check('24px "Material Icons"', 'check'),
      fontStatus: document.fonts.status,
    }
  })
  expect(fontReadiness).toEqual({ fontAvailable: true, fontStatus: 'loaded' })
  await expect.poll(() => page.evaluate(({ collapsed, width }) => {
    const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!
    const boundingWidth = sidebar.getBoundingClientRect().width
    return {
      classMatches: sidebar.classList.contains('is-collapsed') === collapsed,
      boundingWidthSettled: Math.abs(boundingWidth - width) <= 0.5,
      scrollWidthSettled: sidebar.clientWidth === sidebar.scrollWidth,
    }
  }, { collapsed: expectedCollapsed, width: expectedSidebarWidth })).toEqual({
    classMatches: true,
    boundingWidthSettled: true,
    scrollWidthSettled: true,
  })
  const iconReadiness = await page.evaluate(() => {
    const visibleIcons = [...document.querySelectorAll<HTMLElement>('.material-icons')]
      .filter((icon) => icon.offsetParent !== null)
    const invalidIcons = visibleIcons.flatMap((icon) => {
      const style = getComputedStyle(icon)
      const rect = icon.getBoundingClientRect()
      const fontSize = Number.parseFloat(style.fontSize)
      const issues = [
        !style.fontFamily.includes('Material Icons') && 'font-family',
        (rect.width <= 0 || rect.height <= 0) && 'empty-geometry',
        rect.width > fontSize * 1.5 && 'wide-element',
        rect.height > fontSize * 1.75 && 'tall-element',
        icon.scrollWidth > fontSize * 1.5 && 'raw-ligature',
      ].filter(Boolean)
      return issues.length
        ? [{ text: icon.textContent?.trim(), issues, rect: { width: rect.width, height: rect.height }, fontSize, scrollWidth: icon.scrollWidth }]
        : []
    })
    const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')
    const sidebarCollapsed = sidebar?.classList.contains('is-collapsed') ?? false
    return {
      visibleIconCount: visibleIcons.length,
      invalidIcons,
      sidebarCollapsed,
      sidebarBoundingWidth: sidebar?.getBoundingClientRect().width ?? null,
      sidebarClientWidth: sidebar?.clientWidth ?? null,
      sidebarScrollWidth: sidebar?.scrollWidth ?? null,
      sidebarWidthSettled: sidebar?.clientWidth === sidebar?.scrollWidth,
    }
  })
  expect(iconReadiness.visibleIconCount).toBeGreaterThan(0)
  expect(iconReadiness.invalidIcons).toEqual([])
  expect(iconReadiness.sidebarWidthSettled).toBe(true)
  await writeFile(
    resolve(operatorReleaseArtifactDirectory, name.replace(/\.png$/, '.icons.json')),
    `${JSON.stringify({ viewport, state, ...fontReadiness, ...iconReadiness }, null, 2)}\n`,
  )
  await page.screenshot({ path, fullPage: true, animations: 'disabled' })
  await testInfo.attach(name, { path, contentType: 'image/png' })
}

async function expectMainKeyboardTraversal(page: Page) {
  // The collapse toggle is the logo at the top of the sidebar, so it is reached
  // before the destinations rather than after them.
  const toggle = page.getByRole('button', { name: /^(Collapse|Expand) navigation$/ })
  await toggle.focus()
  await page.keyboard.press('Tab')
  await expect(page.locator('.primary-sidebar .navigation-destination').first()).toBeFocused()

  // Tabbing past the last sidebar control must land on main's first focusable.
  const lastSidebarControl = page.locator('.primary-sidebar .navigation-destination').last()
  await expect(lastSidebarControl).toBeVisible()
  await lastSidebarControl.focus()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: /Edit Mode|Done/ })).toBeFocused()

  const traversed = await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>('#main-content')!
    const focusables = [...main.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    )].filter((element) => element.offsetParent !== null)
    const active = document.activeElement as HTMLElement
    return { activeIndex: focusables.indexOf(active), count: focusables.length }
  })
  expect(traversed.activeIndex).toBe(0)
  expect(traversed.count).toBeGreaterThan(5)

  const visited = new Set<string>()
  for (let index = 0; index < traversed.count; index += 1) {
    const focusEvidence = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement
      const style = getComputedStyle(active)
      const rect = active.getBoundingClientRect()
      return {
        identity: `${active.tagName}.${active.className}:${active.getAttribute('aria-label') ?? active.textContent}`,
        area: active.closest('.route-page-header')
          ? 'header'
          : active.closest('.channels-pane .channel-item')
            ? 'channel-row'
            : active.closest('.channels-pane')
              ? 'channels-pane'
              : active.closest('.streams-pane .stream-item')
                ? 'stream-row'
                : active.closest('.streams-pane')
                  ? 'streams-pane'
                  : 'other',
        visibleIndicator: (style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) > 0)
          || (style.boxShadow !== 'none' && style.boxShadow.length > 0),
        visibleGeometry: rect.width > 0 && rect.height > 0
          && rect.right > 0 && rect.bottom > 0
          && rect.left < window.innerWidth && rect.top < window.innerHeight,
      }
    })
    visited.add(focusEvidence.area)
    expect(focusEvidence.visibleIndicator, `${focusEvidence.identity} needs a visible focus indicator`).toBe(true)
    expect(focusEvidence.visibleGeometry, `${focusEvidence.identity} needs visible viewport geometry`).toBe(true)
    if (visited.has('header') && visited.has('channels-pane') && visited.has('channel-row')
      && visited.has('streams-pane') && visited.has('stream-row')) break
    await page.keyboard.press('Tab')
  }
  expect([...visited]).toEqual(expect.arrayContaining([
    'header', 'channels-pane', 'channel-row', 'streams-pane', 'stream-row',
  ]))

  const activeIdentity = () => page.evaluate(() => {
    const active = document.activeElement as HTMLElement
    return `${active.tagName}.${active.className}:${active.getAttribute('aria-label') ?? active.textContent}`
  })
  const beforeReverse = await activeIdentity()
  await page.keyboard.press('Shift+Tab')
  const afterReverse = await activeIdentity()
  expect(afterReverse).not.toBe(beforeReverse)
  await page.keyboard.press('Tab')
  expect(await activeIdentity()).toBe(beforeReverse)
}

async function dismissReleaseToasts(page: Page) {
  const toasts = page.locator('.toast')
  const dismissButtons = toasts.locator('.toast-dismiss')
  // Clipboard and other async actions can enqueue a toast just after the
  // initiating promise resolves. Require a quiet window, not merely one
  // zero-count sample, before a release screenshot is allowed.
  await page.waitForTimeout(500)
  for (let attempt = 0; attempt < 10; attempt += 1) {
    await dismissButtons.evaluateAll((buttons) => buttons.forEach((button) => button.click()))
    await page.waitForTimeout(350)
    if (await toasts.count() === 0) {
      await page.waitForTimeout(350)
      if (await toasts.count() === 0) return
    }
  }
  await expect(toasts).toHaveCount(0)
}

async function openDeterministicOperatorShell(page: Page) {
  let resolveAuthStatus!: () => void
  let resolveSetupStatus!: () => void
  const authStatusFulfilled = new Promise<void>((resolve) => { resolveAuthStatus = resolve })
  const setupStatusFulfilled = new Promise<void>((resolve) => { resolveSetupStatus = resolve })

  await page.route(/\/api\/auth\/status(?:\/|\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        require_auth: false,
        setup_complete: true,
        dispatcharr_enabled: false,
      }),
    })
    resolveAuthStatus()
  })
  await page.route(/\/api\/auth\/setup-required(?:\/|\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ required: false }),
    })
    resolveSetupStatus()
  })
  await page.route(/\/api\/auth\/me(?:\/|\?|$)/, (route) => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Not authenticated in deterministic no-auth mode' }),
  }))
  await page.route(/\/api\/session-start(?:\?|$)/, (route) => route.fulfill({
    status: 204,
    body: '',
  }))

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await Promise.all([authStatusFulfilled, setupStatusFulfilled])
  await expect(page.locator('.tab-navigation')).toBeVisible()
  await expect(page.locator('#main-content h1')).toBeVisible()
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
    ? ['.route-page-header .enter-edit-mode-btn', '.channel-manager-tab', '.split-pane']
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

async function expectLayoutSettled(page: Page, selectors: readonly string[]) {
  await expect.poll(async () => page.evaluate(async (observedSelectors) => {
    await document.fonts.ready
    const sample = () => observedSelectors.map((selector) => {
      const element = document.querySelector<HTMLElement>(selector)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return {
        selector,
        connected: element.isConnected,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        rect: {
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
        },
      }
    })
    const first = sample()
    await new Promise<void>((resolve) => requestAnimationFrame(() =>
      requestAnimationFrame(() => resolve())))
    const second = sample()
    const activeAnimations = observedSelectors.flatMap((selector) => {
      const element = document.querySelector<HTMLElement>(selector)
      return element?.getAnimations({ subtree: true })
        .filter((animation) => animation.playState === 'running').length ?? 0
    })
    return {
      fontsLoaded: document.fonts.status === 'loaded',
      allPresent: second.every((measurement) => measurement?.connected),
      noActiveAnimations: activeAnimations.every((count) => count === 0),
      stable: JSON.stringify(first) === JSON.stringify(second),
    }
  }, selectors)).toEqual({
    fontsLoaded: true,
    allPresent: true,
    noActiveAnimations: true,
    stable: true,
  })
}

interface RouteConsumer {
  name: string
  heading: string
  settled: string
  task: string
  focus: string
  status?: string
  alternate: { state: string; selector: string; recovery?: string } | null
}

const routeConsumers: readonly RouteConsumer[] = [
  { name: 'Dashboard', heading: 'OVERVIEW / DASHBOARD', settled: '.operator-dashboard', task: '.operator-dashboard-card', focus: '.operator-dashboard-card a', status: '.operator-dashboard-card', alternate: { state: 'independent source errors', selector: '.operator-dashboard-card' } },
  { name: 'Channel Manager', heading: 'OPERATIONS / CHANNEL MANAGER', settled: '.enter-edit-mode-btn', task: '.channel-wrapper', focus: '.enter-edit-mode-btn', alternate: { state: 'empty panes', selector: '.channel-workspace-empty' } },
  { name: 'Guide', heading: 'INSIGHTS / GUIDE', settled: 'button[title="Refresh program data"]', task: '.guide-row', focus: 'button[title="Refresh program data"]', alternate: { state: 'empty guide', selector: '.guide-empty-state, .empty-state' } },
  { name: 'M3U Manager', heading: 'OPERATIONS / M3U MANAGER', settled: 'button:has-text("Add M3U Account")', task: '.m3u-account-row', focus: 'button:has-text("Add M3U Account")', status: '.m3u-account-row .status-label', alternate: { state: 'empty accounts', selector: '.empty-state' } },
  { name: 'EPG Manager', heading: 'OPERATIONS / EPG MANAGER', settled: 'button:has-text("Add Standard EPG")', task: '.epg-source-row', focus: 'button:has-text("Add Standard EPG")', status: '.source-updated', alternate: { state: 'empty sources', selector: '.empty-state' } },
  { name: 'Logo Manager', heading: 'OPERATIONS / LOGO MANAGER', settled: 'button:has-text("Add Logo")', task: '.logo-row, .logo-card', focus: 'button:has-text("Add Logo")', status: '.logo-count, .page-header-status', alternate: { state: 'empty logos', selector: '.empty-state' } },
  { name: 'Channel Pipeline', heading: 'AUTOMATION / CHANNEL PIPELINE', settled: 'button[aria-label="Create rule"]', task: '[data-testid="rules-list"] tbody tr', focus: 'button[aria-label="Create rule"]', status: '.channel-pipeline-stats', alternate: { state: 'empty rules', selector: '.empty-state' } },
  { name: 'M3U Changes', heading: 'INSIGHTS / M3U CHANGES', settled: 'button:has-text("Refresh")', task: '.change-wrapper, .empty-state, .tab-load-unavailable', focus: 'button:has-text("Refresh")', status: '.summary-cards, .page-header-status', alternate: { state: 'request error', selector: '.tab-load-unavailable', recovery: 'button:has-text("Retry")' } },
  { name: 'Stats', heading: 'INSIGHTS / STATS', settled: 'button:has-text("Refresh")', task: '.channel-card', focus: 'button:has-text("Refresh")', status: '.stats-last-updated, .page-header-status', alternate: { state: 'independent metric errors', selector: '.stats-error, .error-state' } },
  { name: 'Journal', heading: 'INSIGHTS / JOURNAL', settled: 'button:has-text("Refresh")', task: '.entry-wrapper, .empty-state, .tab-load-unavailable', focus: 'button:has-text("Refresh")', status: '.header-stats, .page-header-status', alternate: { state: 'request error', selector: '.tab-load-unavailable', recovery: 'button:has-text("Retry")' } },
  { name: 'Settings', heading: 'SYSTEM / SETTINGS / GENERAL SETTINGS', settled: 'button:has-text("Save Settings")', task: '.settings-section', focus: 'button:has-text("Save Settings")', alternate: null },
]

const primaryRouteRequestBudgets: Readonly<Record<string, number>> = {
  Dashboard: 12,
  'Channel Manager': 20,
  Guide: 12,
  'M3U Manager': 10,
  'EPG Manager': 12,
  'Logo Manager': 8,
  'Channel Pipeline': 12,
  'M3U Changes': 8,
  Stats: 30,
  Journal: 8,
  Settings: 40,
}

type RequestSnapshot = {
  elapsedMs: number
  exact: Readonly<Record<string, number>>
  normalized: Readonly<Record<string, number>>
}

type PeriodicRequestPolicy = {
  owner: 'global' | 'Channel Manager' | 'Stats' | 'Settings'
  maximumPerWindow: number
  reason: string
}

const intendedPeriodicRequests: Readonly<Record<string, PeriodicRequestPolicy>> = {
  // Notification freshness is global; the Channel Manager pending-merge
  // badge is route-owned. Both are deliberate 30-second background polls.
  // The value is the maximum normalized requests permitted in each
  // 31-second observation window while the owner is active.
  'GET /api/notifications?page_size': {
    owner: 'global', maximumPerWindow: 2,
    reason: 'Global notification-center freshness, including accelerated active-operation cadence.',
  },
  'GET /api/channel-merges?page&page_size&status': {
    owner: 'Channel Manager', maximumPerWindow: 2,
    reason: 'Channel Manager pending-merge badge freshness.',
  },
  // The visible Stats overview intentionally refreshes its four primary
  // metrics once per configured interval. The deterministic settings fixture
  // yields one refresh per observation window.
  'GET /api/stats/activity?limit': {
    owner: 'Stats', maximumPerWindow: 1, reason: 'Visible Stats overview auto-refresh.',
  },
  'GET /api/stats/bandwidth': {
    owner: 'Stats', maximumPerWindow: 1, reason: 'Visible Stats overview auto-refresh.',
  },
  'GET /api/stats/channels': {
    owner: 'Stats', maximumPerWindow: 1, reason: 'Visible Stats overview auto-refresh.',
  },
  'GET /api/stats/top-watched?limit&sort_by': {
    owner: 'Stats', maximumPerWindow: 1, reason: 'Visible Stats overview auto-refresh.',
  },
  // Settings detects a scheduled stream probe every five seconds so an
  // externally-started operation becomes visible without user action.
  'GET /api/stream-stats/probe/progress': {
    owner: 'Settings', maximumPerWindow: 7,
    reason: 'Settings detects externally scheduled stream probes every five seconds.',
  },
}

function normalizedRequestKey(method: string, rawUrl: string) {
  const url = new URL(rawUrl)
  const canonicalKeys = [...new Set(url.searchParams.keys())].sort().join('&')
  return `${method.toUpperCase()} ${url.pathname}${canonicalKeys ? `?${canonicalKeys}` : ''}`
}

function countRequestKeys(requests: readonly string[]) {
  return Object.fromEntries(
    [...new Set(requests)].sort().map((key) => [
      key,
      requests.filter((candidate) => candidate === key).length,
    ]),
  )
}

function requestWindowGrowth(
  snapshots: readonly RequestSnapshot[],
  periodicAllowances: Readonly<Record<string, number>>,
) {
  const keys = [...new Set(snapshots.flatMap((snapshot) => Object.keys(snapshot.normalized)))].sort()
  return keys.flatMap((key) => {
    const deltas = snapshots.slice(1).map((snapshot, index) =>
      (snapshot.normalized[key] ?? 0) - (snapshots[index].normalized[key] ?? 0))
    const allowance = periodicAllowances[key] ?? 0
    return deltas.length >= 2 && deltas.some((delta) => delta > allowance)
      ? [{ key, deltas, allowance }]
      : []
  })
}

function periodicAllowancesForRoute(route: string) {
  return Object.fromEntries(
    Object.entries(intendedPeriodicRequests)
      .filter(([, policy]) => policy.owner === 'global' || policy.owner === route)
      .map(([key, policy]) => [key, policy.maximumPerWindow]),
  )
}

function requestOwner(normalizedKey: string): PeriodicRequestPolicy['owner'] | null {
  return intendedPeriodicRequests[normalizedKey]?.owner ?? null
}

async function computedTextContrast(page: Page, selector: string) {
  return page.locator(selector).first().evaluate((element, evaluatedSelector) => {
    type Rgba = { r: number; g: number; b: number; a: number }
    const parse = (value: string): Rgba => {
      // Detached/non-painting ancestors can report an empty computed
      // background during a concurrent React commit; it is equivalent to a
      // transparent paint layer, not an unsupported foreground color.
      if (value === '' || value === 'transparent') return { r: 0, g: 0, b: 0, a: 0 }
      const channels = value.match(/[\d.]+/g)?.map(Number) ?? []
      if (channels.length < 3) throw new Error(`Unsupported computed color: ${value}`)
      return { r: channels[0], g: channels[1], b: channels[2], a: channels[3] ?? 1 }
    }
    const blend = (foreground: Rgba, background: Rgba): Rgba => {
      const alpha = foreground.a + background.a * (1 - foreground.a)
      if (alpha === 0) return { r: 0, g: 0, b: 0, a: 0 }
      return {
        r: (foreground.r * foreground.a + background.r * background.a * (1 - foreground.a)) / alpha,
        g: (foreground.g * foreground.a + background.g * background.a * (1 - foreground.a)) / alpha,
        b: (foreground.b * foreground.a + background.b * background.a * (1 - foreground.a)) / alpha,
        a: alpha,
      }
    }
    const layers: Rgba[] = []
    for (let current: Element | null = element; current; current = current.parentElement) {
      layers.push(parse(getComputedStyle(current).backgroundColor))
    }
    let background: Rgba = { r: 255, g: 255, b: 255, a: 1 }
    for (const layer of layers.reverse()) background = blend(layer, background)
    const foreground = blend(parse(getComputedStyle(element).color), background)
    const luminance = (color: Rgba) => {
      const linear = [color.r, color.g, color.b].map((channel) => {
        const value = channel / 255
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    }
    const foregroundLuminance = luminance(foreground)
    const backgroundLuminance = luminance(background)
    return {
      selector: evaluatedSelector,
      foreground: getComputedStyle(element).color,
      background: `rgb(${background.r} ${background.g} ${background.b})`,
      ratio: (Math.max(foregroundLuminance, backgroundLuminance) + 0.05)
        / (Math.min(foregroundLuminance, backgroundLuminance) + 0.05),
      text: element.textContent?.trim() ?? '',
    }
  }, selector)
}

async function expectSettledRoute(page: Page, consumer: RouteConsumer) {
  const heading = page.locator('#main-content h1')
  await expect(heading).toHaveText(consumer.heading)
  await expect(heading).toHaveCount(1)
  await expect(page.locator('#main-content .tab-loading')).toHaveCount(0)
  const settled = page.locator(consumer.settled).first()
  await expect(settled).toBeVisible()
  return settled
}

async function seedPrimaryRouteBudgetData(page: Page) {
  await seedChannelWorkspace(page, true, 2)
  await page.route(/\/api\/journal(?:\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      count: 1, page: 1, page_size: 50, total_pages: 1,
      results: [{
        id: 901, timestamp: '2026-07-27T12:00:00Z', category: 'channel',
        action_type: 'create', entity_id: 41, entity_name: 'Budget journal entry',
        description: 'Deterministic populated fixture', before_value: null, after_value: null,
        user_initiated: true, mutation_source: 'ui', batch_id: null,
      }],
    }),
  }))
  await page.route(/\/api\/journal\/stats(?:\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      total_entries: 1, by_category: { channel: 1 }, by_action_type: { create: 1 },
      date_range: { oldest: '2026-07-27T12:00:00Z', newest: '2026-07-27T12:00:00Z' },
    }),
  }))
  await page.route(/\/api\/m3u\/changes(?:\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      results: [{
        id: 701, m3u_account_id: 3, change_time: '2026-07-27T12:00:00Z',
        change_type: 'group_added', group_name: 'Budget changes row',
        stream_names: [], count: 4, enabled: true, snapshot_id: 1,
      }],
      total: 1, page: 1, page_size: 50, total_pages: 1,
    }),
  }))
  await page.route(/\/api\/m3u\/changes\/summary(?:\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      total_changes: 1, groups_added: 1, groups_removed: 0, streams_added: 0,
      streams_removed: 0, accounts_affected: [3], since: '2026-07-27T00:00:00Z',
    }),
  }))
  await page.route(/\/api\/health(?:\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ status: 'healthy', service: 'ECM', version: '9.9.9', release_channel: 'stable' }),
  }))
  await page.route(/\/api\/tasks(?:\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ tasks: [{ task_id: 'refresh', enabled: true, effective_enabled: true, status: 'running', last_run: '2026-07-27T01:00:00Z' }] }),
  }))
  await page.route(/\/api\/stats\/channels(?:\/|\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      count: 1,
      channels: [{
        channel_id: 'channel-41', channel_name: 'Budget live channel', channel_number: 101,
        state: 'streaming', client_count: 1, clients: [], stream_name: 'Budget stream',
        m3u_account_id: 3, stream_id: 501,
      }],
    }),
  }))
  await page.route(/\/api\/stats\/activity(?:\/|\?|$)/, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ events: [], count: 0, total: 0, offset: 0, limit: 50 }),
  }))
}

async function openShellWithPipelineFixture(
  page: Page,
  rulesStatus = 200,
  providers: Array<Record<string, unknown>> = [],
  epgSources: Array<Record<string, unknown>> = [],
  populatedRules = false,
) {
  await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
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
    }),
  }))
  await page.route(/\/api\/providers(?:\/|\?|$)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(providers) }))
  await page.route(/\/api\/m3u\/server-groups(?:\/|\?|$)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
  await page.route(/\/api\/epg\/sources(?:\/|\?|$)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(epgSources) }))
  await page.route(/\/api\/channels\/logos(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        count: 1,
        next: null,
        previous: null,
        results: [{ id: 9, name: 'Persisted fixture artwork', url: '/persisted-channel-artwork.png', cache_url: '/persisted-channel-artwork.png' }],
      }),
    }))
  await page.route(/\/api\/channel-pipeline\/rules(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: rulesStatus,
      contentType: 'application/json',
      body: rulesStatus === 200 ? JSON.stringify({ rules: populatedRules ? [{
        id: 81, name: 'Budget pipeline rule', description: 'Deterministic populated rule',
        enabled: true, priority: 1, active_from: null, active_until: null,
        conditions: [{ type: 'stream_name_contains', value: 'sports' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
        m3u_account_id: null, target_group_id: null, run_on_refresh: false,
        stop_on_first_match: false, sort_field: null, sort_order: 'asc',
        probe_on_sort: false, sort_regex: null, stream_sort_field: null,
        stream_sort_order: 'asc', quality_tie_break_order: 'desc',
        quality_m3u_tie_break_enabled: true, normalization_group_ids: [],
        skip_struck_streams: false, orphan_action: 'delete', last_run_at: null,
        match_count: 4, created_at: '2026-07-27T00:00:00Z',
        updated_at: '2026-07-27T12:00:00Z', event_sync_config: null,
      }] : [] }) : JSON.stringify({ detail: 'Pipeline unavailable' }),
    }))
  await page.route(/\/api\/channel-pipeline\/executions(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ executions: [], total: 0, page: 1, page_size: 25 }),
    }))
  await page.route(/\/api\/channel-pipeline\/circuit-breaker(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ disabled: false, reason: null }),
    }))
  await openDeterministicOperatorShell(page)
}

async function seedChannelWorkspace(page: Page, populated: boolean, channelCount = 1, healthMatrix = false) {
  await page.route(/\/api\/health(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'healthy',
      service: 'enhanced-channel-manager',
      version: '0.18.1-0000',
      release_channel: 'test',
      git_commit: 'operator-workspace-release',
    }),
  }))
  const channel = {
    id: 41,
    name: 'A deliberately long channel identity that must remain inside the Channels pane',
    channel_number: 101,
    channel_group_id: 7,
    streams: [501],
    logo_id: 9,
    _stagedLogoUrl: '/staged-channel-artwork.png',
    tvg_id: 'espn.us',
    epg_data_id: 88,
  }
  const healthChannels = [
    { ...channel, id: 41, name: 'No streams status', channel_number: 101, streams: [], logo_id: null },
    { ...channel, id: 42, name: 'Failed probe status', channel_number: 102, streams: [501] },
    { ...channel, id: 43, name: 'Stale status', channel_number: 103, streams: [502], logo_id: null },
    { ...channel, id: 44, name: 'Black screen status', channel_number: 104, streams: [503], logo_id: null },
    { ...channel, id: 45, name: 'Low FPS status', channel_number: 105, streams: [504], logo_id: null },
    { ...channel, id: 46, name: 'Healthy status', channel_number: 106, streams: [505], logo_id: null },
  ]
  const channels = healthMatrix ? healthChannels : channelCount >= 2
    ? [
        channel,
        {
          ...channel,
          id: 42,
          uuid: 'channel-42',
          name: 'Second fixture channel',
          channel_number: 102,
          streams: [],
          tvg_id: null,
          epg_data_id: null,
          _stagedLogoUrl: undefined,
        },
      ]
    : [channel]
  const stream = {
    id: 501,
    name: 'A deliberately long source stream identity that must ellipsize before inventory actions',
    url: `https://example.invalid/${'very-long-path/'.repeat(12)}playlist.m3u8`,
    channel_group_name: 'Provider Sports',
    m3u_account: 3,
    logo_url: null,
  }
  const healthStreams = [
    stream,
    { ...stream, id: 502, name: 'Stale source', is_stale: true },
    { ...stream, id: 503, name: 'Black screen source' },
    { ...stream, id: 504, name: 'Low FPS source' },
    { ...stream, id: 505, name: 'Healthy source' },
  ]
  const baseHealthyStats = {
    resolution: '1920x1080', fps: 30, video_codec: 'h264', audio_codec: 'aac',
    audio_channels: 2, stream_type: 'hls', bitrate: 4_000_000, video_bitrate: 3_500_000,
    probe_status: 'success', error_message: null, last_probed: '2026-07-27T12:00:00Z',
    created_at: '2026-07-27T00:00:00Z', consecutive_failures: 0,
    is_black_screen: false, is_low_fps: false,
  }
  const healthStats = {
    501: {
      ...baseHealthyStats, stream_id: 501, stream_name: stream.name, probe_status: 'timeout',
      error_message: 'Probe exceeded the configured 30 second deadline while waiting for the upstream provider response',
      consecutive_failures: 3,
    },
    502: { ...baseHealthyStats, stream_id: 502, stream_name: 'Stale source' },
    503: { ...baseHealthyStats, stream_id: 503, stream_name: 'Black screen source', is_black_screen: true },
    504: { ...baseHealthyStats, stream_id: 504, stream_name: 'Low FPS source', fps: 2, is_low_fps: true },
    505: { ...baseHealthyStats, stream_id: 505, stream_name: 'Healthy source' },
  }
  await page.route(/\/api\/channel-groups(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ id: 7, name: 'Sports' }] : []),
  }))
  await page.route(/\/api\/channels(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      count: populated ? channels.length : 0,
      next: null,
      previous: null,
      results: populated ? channels : [],
    }),
  }))
  await page.route(/\/api\/stream-groups(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ name: 'Provider Sports', count: healthMatrix ? 5 : 1 }] : []),
  }))
  await page.route(/\/api\/streams(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      count: populated ? (healthMatrix ? healthStreams.length : 1) : 0,
      next: null,
      previous: null,
      results: populated ? (healthMatrix ? healthStreams : [stream]) : [],
    }),
  }))
  await page.route(/\/api\/channels\/\d+\/streams(?:\?|$)/, (route) => {
    const channelId = Number(new URL(route.request().url()).pathname.split('/').at(-2))
    const assignedId = healthMatrix ? healthChannels.find((item) => item.id === channelId)?.streams[0] : 501
    const assigned = healthMatrix
      ? healthStreams.find((item) => item.id === assignedId)
      : stream
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(assigned ? [assigned] : []),
    })
  })
  await page.route(/\/api\/stream-stats\/by-ids(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? (healthMatrix ? healthStats : {
      501: {
        stream_id: 501,
        stream_name: stream.name,
        resolution: null,
        fps: null,
        video_codec: null,
        audio_codec: null,
        audio_channels: null,
        stream_type: null,
        bitrate: null,
        video_bitrate: null,
        probe_status: 'timeout',
        error_message: 'Probe exceeded the configured 30 second deadline while waiting for the upstream provider response',
        last_probed: null,
        created_at: '2026-07-27T00:00:00Z',
        consecutive_failures: 3,
        is_black_screen: false,
        is_low_fps: false,
      },
    }) : {}),
  }))
  await page.route(/\/api\/streams\/stale-ids(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ stale_stream_ids: healthMatrix ? [502] : [] }),
  }))
  await page.route(/\/api\/stream-stats\/probe\/bulk(?:\?|$)/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ probed: 1, results: [] }),
    })
  })
  await page.route(/\/api\/epg\/sources(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ id: 5, name: 'Schedules Direct' }] : []),
  }))
  await page.route(/\/api\/epg\/data(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ id: 88, tvg_id: 'espn.us', name: 'ESPN', epg_source: 5, icon_url: null }] : []),
  }))
}

test('request storm detector catches delayed query-changing growth after an initially quiet route', () => {
  const snapshots: RequestSnapshot[] = [
    { elapsedMs: 0, exact: {}, normalized: {} },
    {
      elapsedMs: 31_000,
      exact: {},
      normalized: {},
    },
    {
      elapsedMs: 62_000,
      exact: { '/api/search?cursor=late-loop-1': 1, '/api/search?cursor=late-loop-2': 1 },
      normalized: { 'GET /api/search?cursor': 2 },
    },
  ]

  expect(requestWindowGrowth(snapshots, {})).toEqual([{
    key: 'GET /api/search?cursor',
    deltas: [0, 2],
    allowance: 0,
  }])
  expect(normalizedRequestKey('get', 'https://ecm.test/api/search?z=1&cursor=two&z=2'))
    .toBe('GET /api/search?cursor&z')
})

test('route-scoped poll policy rejects a leaked Stats refresh on Dashboard', () => {
  expect(periodicAllowancesForRoute('Stats')).toMatchObject({
    'GET /api/stats/bandwidth': 1,
  })
  expect(periodicAllowancesForRoute('Dashboard')).not.toHaveProperty('GET /api/stats/bandwidth')
  expect(requestWindowGrowth([
    { elapsedMs: 0, exact: {}, normalized: {} },
    { elapsedMs: 31_000, exact: {}, normalized: {} },
    {
      elapsedMs: 62_000,
      exact: { '/api/stats/bandwidth': 1 },
      normalized: { 'GET /api/stats/bandwidth': 1 },
    },
  ], periodicAllowancesForRoute('Dashboard'))).toEqual([{
    key: 'GET /api/stats/bandwidth',
    deltas: [0, 1],
    allowance: 0,
  }])
})

test('route-scoped poll policy rejects a leaked Channel Manager pending-merge poll on Dashboard', () => {
  const pendingMerges = 'GET /api/channel-merges?page&page_size&status'
  expect(periodicAllowancesForRoute('Channel Manager')).toMatchObject({
    [pendingMerges]: 2,
  })
  expect(periodicAllowancesForRoute('Dashboard')).not.toHaveProperty(pendingMerges)
  expect(requestWindowGrowth([
    { elapsedMs: 0, exact: {}, normalized: {} },
    { elapsedMs: 31_000, exact: {}, normalized: {} },
    {
      elapsedMs: 62_000,
      exact: { '/api/channel-merges?status=pending&page=1&page_size=1': 1 },
      normalized: { [pendingMerges]: 1 },
    },
  ], periodicAllowancesForRoute('Dashboard'))).toEqual([{
    key: pendingMerges,
    deltas: [0, 1],
    allowance: 0,
  }])
  expect(requestOwner(pendingMerges)).toBe('Channel Manager')
})

for (const viewport of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
  test.describe(`operator shell geometry at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, serviceWorkers: 'block' })

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

    test('all primary route consumers preserve hierarchy and exact healthy controls', async ({ page }) => {
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)
      for (const consumer of routeConsumers) {
        await page.getByRole('link', { name: consumer.name }).click()
        const settled = await expectSettledRoute(page, consumer)
        await settled.scrollIntoViewIfNeeded()
        expect(await settled.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          const main = document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect()
          return rect.left >= main.left - 1 && rect.right <= main.right + 1
            && document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
        })).toBe(true)
      }
    })

    test('all primary routes have no serious automated accessibility violations or clipped required controls', async ({ page }) => {
      // Advance deterministic browser time below so two complete 30-second
      // background-poll windows are observed without adding 22 real minutes
      // to the two-viewport matrix.
      await page.clock.install()
      await seedPrimaryRouteBudgetData(page)
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)
      let currentRoute = 'Shell'
      const apiRequests: Array<{
        exact: string
        normalized: string
        routeAtRequest: string
        owner: PeriodicRequestPolicy['owner'] | null
      }> = []
      page.on('request', (request) => {
        const url = new URL(request.url())
        if (request.method() === 'GET' && url.pathname.startsWith('/api/')) {
          const normalized = normalizedRequestKey(request.method(), request.url())
          apiRequests.push({
            exact: `${url.pathname}${url.search}`,
            normalized,
            routeAtRequest: currentRoute,
            owner: requestOwner(normalized),
          })
        }
      })

      for (const consumer of routeConsumers) {
        currentRoute = consumer.name
        const requestStart = apiRequests.length
        await page.getByRole('link', { name: consumer.name }).click()
        await expectSettledRoute(page, consumer)
        if (await page.locator('.toast').count()) await dismissReleaseToasts(page)

        const accessibility = await new AxeBuilder({ page })
          .include('#root')
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()
        const axeArtifact = {
          viewport,
          route: consumer.name,
          violations: accessibility.violations.map((violation) => ({
            id: violation.id,
            impact: violation.impact,
            help: violation.help,
            nodes: violation.nodes.map((node) => node.target),
          })),
        }
        await mkdir(resolve(process.cwd(), 'test-results/operator-workspace-final-validation'), { recursive: true })
        await writeFile(
          resolve(
            process.cwd(),
            'test-results/operator-workspace-final-validation',
            `axe--${viewport.width}x${viewport.height}--${consumer.name.toLowerCase().replaceAll(' ', '-')}.json`,
          ),
          `${JSON.stringify(axeArtifact, null, 2)}\n`,
        )
        const blocking = accessibility.violations.filter(
          (violation) => violation.impact === 'serious' || violation.impact === 'critical',
        )
        expect(blocking, `${consumer.name} serious/critical axe violations`).toEqual([])

        const geometry = await page.evaluate(() => {
          const viewport = { right: document.documentElement.clientWidth, bottom: window.innerHeight }
          const required = [...document.querySelectorAll<HTMLElement>(
            '#main-content a[href], #main-content button:not(:disabled), #main-content input:not(:disabled), #main-content select:not(:disabled), #main-content textarea:not(:disabled)',
          )].filter((element) => element.offsetParent !== null)
          const clipped = required.flatMap((element) => {
            const rect = element.getBoundingClientRect()
            const style = getComputedStyle(element)
            const recoverableHorizontalOwner = [...document.querySelectorAll<HTMLElement>('#main-content *')]
              .some((owner) => owner !== element && owner.contains(element)
                && /(auto|scroll)/.test(getComputedStyle(owner).overflowX)
                && owner.scrollWidth > owner.clientWidth + 1)
            const horizontallyClipped = !recoverableHorizontalOwner
              && (rect.left < -1 || rect.right > viewport.right + 1)
            const ownTextClipped = element.scrollWidth > element.clientWidth + 1
              && style.textOverflow !== 'ellipsis'
              && !/(auto|scroll)/.test(style.overflowX)
            return horizontallyClipped || ownTextClipped
              ? [`${element.tagName}.${element.className}:${element.getAttribute('aria-label') ?? element.textContent?.trim()}`]
              : []
          })
          return {
            documentFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            clipped,
          }
        })
        expect(geometry.documentFits, `${consumer.name} document horizontal overflow`).toBe(true)
        expect(geometry.clipped, `${consumer.name} clipped required controls`).toEqual([])

        const initialRouteRequests = apiRequests.slice(requestStart)
        const counts = countRequestKeys(initialRouteRequests.map((request) => request.exact))
        expect(
          initialRouteRequests.length,
          `${consumer.name} exceeded its exact GET request budget: ${JSON.stringify(counts)}`,
        ).toBeLessThanOrEqual(primaryRouteRequestBudgets[consumer.name])
        expect(
          Object.entries(counts).filter(([, count]) => count > 2),
          `${consumer.name} repeated an identical GET more than twice`,
        ).toEqual([])

        // The route has settled and its synchronous/dependent loads have
        // completed. Requests after this lifecycle grace boundary must either
        // be owned by the current route or be a documented global-shell poll.
        const quietStart = apiRequests.length
        const snapshots: RequestSnapshot[] = [{
          elapsedMs: 0,
          exact: counts,
          normalized: countRequestKeys(initialRouteRequests.map((request) => request.normalized)),
        }]
        for (const elapsedMs of [31_000, 62_000]) {
          await page.clock.fastForward(31_000)
          // A rendered assertion gives timer-triggered request promises and
          // React effects a deterministic microtask checkpoint.
          await expect(page.locator(consumer.settled).first()).toBeVisible()
          const observed = apiRequests.slice(requestStart)
          snapshots.push({
            elapsedMs,
            exact: countRequestKeys(observed.map((request) => request.exact)),
            normalized: countRequestKeys(observed.map((request) => request.normalized)),
          })
        }
        const routeAllowances = periodicAllowancesForRoute(consumer.name)
        const continuedGrowth = requestWindowGrowth(snapshots, routeAllowances)
        const leakedOwners = apiRequests.slice(quietStart).flatMap((request) =>
          request.owner && request.owner !== 'global' && request.owner !== consumer.name
            ? [{
                exact: request.exact,
                normalized: request.normalized,
                owner: request.owner,
                currentRoute: consumer.name,
                routeAtRequest: request.routeAtRequest,
              }]
            : [])
        expect(
          continuedGrowth,
          `${consumer.name} has delayed/query-changing request growth in consecutive quiet windows`,
        ).toEqual([])
        expect(
          leakedOwners,
          `${consumer.name} received requests owned by a prior route after its lifecycle grace boundary`,
        ).toEqual([])
        await writeFile(
          resolve(
            process.cwd(),
            'test-results/operator-workspace-final-validation',
            `requests--${viewport.width}x${viewport.height}--${consumer.name.toLowerCase().replaceAll(' ', '-')}.json`,
          ),
          `${JSON.stringify({
            viewport,
            route: consumer.name,
            budget: primaryRouteRequestBudgets[consumer.name],
            initialTotal: initialRouteRequests.length,
            counts,
            normalizedInitialCounts: snapshots[0].normalized,
            quietWindowMs: 31_000,
            observedWindows: 2,
            lifecycleGraceBoundary: 'route settled, transient toasts dismissed, axe and geometry complete',
            allowedPeriodicRequests: Object.fromEntries(
              Object.entries(intendedPeriodicRequests)
                .filter(([, policy]) => policy.owner === 'global' || policy.owner === consumer.name),
            ),
            timeSeries: snapshots,
            continuedGrowth,
            leakedOwners,
            observedRequests: apiRequests.slice(requestStart).map((request) => ({
              ...request,
              allowedOnCurrentRoute: request.owner === 'global' || request.owner === consumer.name,
            })),
          }, null, 2)}\n`,
        )
      }
    })

    test('Channel Manager group text meets AA contrast in populated and empty states across every theme', async ({ page }) => {
      await seedChannelWorkspace(page, true, 2)
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)
      await page.getByRole('link', { name: 'Channel Manager' }).click()
      await expectSettledRoute(page, routeConsumers[1])

      const states = [
        {
          name: 'populated',
          root: '.channel-group:not(.empty-group)',
          selectors: ['.group-toggle'],
        },
        {
          name: 'empty',
          root: '.channel-group.empty-group',
          selectors: ['.group-toggle', '.group-subtext', '.group-empty-badge'],
        },
      ] as const

      for (const theme of ['dark', 'light', 'high-contrast'] as const) {
        await page.evaluate((selectedTheme) => {
          document.documentElement.setAttribute('data-theme', selectedTheme)
        }, theme)
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        await expectLayoutSettled(page, [
          '#main-content',
          '.channel-group:not(.empty-group) .group-toggle',
          '.channel-group.empty-group .group-toggle',
        ])

        for (const state of states) {
          const stateRoot = page.locator(state.root).first()
          await expect(stateRoot, `${state.name} Channel Manager group fixture`).toBeVisible()
          await stateRoot.evaluate((element, stateName) => {
            element.setAttribute('data-contrast-state', stateName)
          }, state.name)
          const contrast = []
          for (const childSelector of state.selectors) {
            const selector = `${state.root} ${childSelector}`
            await expect(page.locator(selector).first(), `${selector} must exist`).toBeVisible()
            const evidence = await computedTextContrast(page, selector)
            expect(
              evidence.ratio,
              `${theme}/${state.name} ${childSelector}: ${JSON.stringify(evidence)}`,
            ).toBeGreaterThanOrEqual(4.5)
            contrast.push(evidence)
          }

          const accessibility = await new AxeBuilder({ page })
            .include('.channels-pane .channel-group')
            .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
            .analyze()
          const blocking = accessibility.violations.filter(
            (violation) => violation.impact === 'serious' || violation.impact === 'critical',
          )
          expect(blocking, `${theme}/${state.name} serious/critical axe violations`).toEqual([])

          const artifact = {
            viewport,
            route: 'Channel Manager',
            state: state.name,
            theme,
            minimumRatio: 4.5,
            contrast,
            axe: accessibility.violations.map((violation) => ({
              id: violation.id,
              impact: violation.impact,
              help: violation.help,
              nodes: violation.nodes.map((node) => node.target),
            })),
          }
          const artifactDirectory = resolve(
            process.cwd(),
            'test-results/operator-workspace-final-validation',
          )
          await mkdir(artifactDirectory, { recursive: true })
          await writeFile(
            resolve(
              artifactDirectory,
              `channel-manager-contrast--${viewport.width}x${viewport.height}--${theme}--${state.name}.json`,
            ),
            `${JSON.stringify(artifact, null, 2)}\n`,
          )
        }
      }
    })

    // The all-routes axe sweep runs in the default theme only, which is how the
    // light-theme Dashboard freshness failure (enhancedchannelmanager-eo7er)
    // survived the .9 validation. This walks the Dashboard and the header
    // service indicator through every theme in both populated and failed states.
    test('Dashboard and header status meet AA contrast with no serious violations across every theme', async ({ page }) => {
      await seedChannelWorkspace(page, true, 2)
      await page.route(/\/api\/health(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'healthy', service: 'ECM', version: '1.0.0', release_channel: 'stable', git_commit: 'fixture' }),
      }))
      // Hostile fixture for bead nhkd4: GitHub is made to advertise a release
      // far ahead of the running 1.0.0. The header must STILL show no update
      // pill — that signal now lives in the notification centre, written
      // server-side, and the frontend no longer calls GitHub at all (so this
      // route is expected to go unrequested; it is left in place so the
      // assertion below cannot pass merely because the fixture went missing).
      await page.route(/api\.github\.com\/repos\/.*\/releases\/latest/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tag_name: 'v99.0.0' }),
      }))
      await page.route(/\/api\/m3u\/changes\/summary(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_changes: 3, groups_added: 0, groups_removed: 0, streams_added: 2,
          streams_removed: 1, accounts_affected: [3], since: '2026-07-26T12:00:00Z',
        }),
      }))
      await page.route(/\/api\/tasks(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tasks: [
          { task_id: 'cleanup', enabled: true, effective_enabled: true, status: 'failed', last_run: '2026-07-27T02:00:00Z' },
        ] }),
      }))
      await page.route(/\/api\/journal\/stats(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_entries: 9, by_category: { channel: 5, stream: 4 }, by_action_type: {},
          date_range: { oldest: '2026-07-20T00:00:00Z', newest: '2026-07-27T02:00:00Z' },
        }),
      }))

      await openShellWithPipelineFixture(page, 200, [{ id: 3, name: 'Fixture Provider' }])
      await dismissFirstRunPromptIfPresent(page)
      await page.getByRole('link', { name: 'Dashboard' }).click()
      await expect(page.getByRole('region', { name: 'System summary' })).toBeVisible()
      await expect(page.locator('.operator-dashboard-card')).toHaveCount(6)
      await expect(page.locator('.header-update-available')).toHaveCount(0)
      // Proves this test's own journal fixture is the one being served, so the
      // failure override below is known to reach the same handler.
      await expect(page.getByText('9 entries', { exact: true })).toBeVisible()

      // Normal text at 4.5:1; .dashboard-card-value is >=1.45rem bold large text
      // but is held to the stricter floor because it always can be.
      const alwaysPresent = [
        '.operator-dashboard-intro',
        '.operator-dashboard-card h3',
        '.dashboard-card-value',
        '.dashboard-card-status',
        '.dashboard-card-freshness',
        '.dashboard-card-link',
        '.service-status-label',
        '.service-status-version',
      ] as const

      for (const state of ['populated', 'partial-failure'] as const) {
        if (state === 'partial-failure') {
          // Registered here rather than up front: Playwright matches the most
          // recently registered handler first, so this reliably overrides the
          // success fixture above. A single failing card exercises
          // .dashboard-card-error beside healthy siblings.
          await page.route(/\/api\/journal\/stats(?:\?|$)/, (route) => route.fulfill({
            status: 500, contentType: 'application/json', body: '{"detail":"fixture failure"}',
          }))
          // A full reload rather than a route round-trip: navigating to the
          // lazily-loaded Journal chunk does not reliably unmount the Dashboard,
          // so its cards keep their resolved state and never refetch.
          await page.reload()
          await dismissFirstRunPromptIfPresent(page)
          await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
          await expect(page.locator('.dashboard-card-error')).toHaveCount(1)
          await expect(page.locator('.header-update-available')).toHaveCount(0)
        }

        for (const theme of ['dark', 'light', 'high-contrast'] as const) {
          await page.evaluate((selectedTheme) => {
            document.documentElement.setAttribute('data-theme', selectedTheme)
          }, theme)
          await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
          await expectLayoutSettled(page, ['#main-content', '.operator-dashboard-card', '.service-status'])

          const selectors = [
            ...alwaysPresent,
            ...(state === 'partial-failure' ? ['.dashboard-card-error p', '.dashboard-card-error button'] : []),
          ]
          const contrast = []
          for (const selector of selectors) {
            await expect(page.locator(selector).first(), `${selector} must exist`).toBeVisible()
            const evidence = await computedTextContrast(page, selector)
            expect(
              evidence.ratio,
              `${theme}/${state} ${selector}: ${JSON.stringify(evidence)}`,
            ).toBeGreaterThanOrEqual(4.5)
            contrast.push(evidence)
          }

          const accessibility = await new AxeBuilder({ page })
            .include('#root')
            .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
            .analyze()
          const blocking = accessibility.violations.filter(
            (violation) => violation.impact === 'serious' || violation.impact === 'critical',
          )
          expect(blocking, `${theme}/${state} serious/critical axe violations`).toEqual([])

          const artifactDirectory = resolve(process.cwd(), 'test-results/operator-workspace-final-validation')
          await mkdir(artifactDirectory, { recursive: true })
          await writeFile(
            resolve(
              artifactDirectory,
              `dashboard-contrast--${viewport.width}x${viewport.height}--${theme}--${state}.json`,
            ),
            `${JSON.stringify({
              viewport, route: 'Dashboard', state, theme, minimumRatio: 4.5, contrast,
              axe: accessibility.violations.map((violation) => ({
                id: violation.id,
                impact: violation.impact,
                help: violation.help,
                nodes: violation.nodes.map((node) => node.target),
              })),
            }, null, 2)}\n`,
          )
        }
        await page.evaluate(() => document.documentElement.removeAttribute('data-theme'))
      }
    })

    test('every primary route preserves the audited vertical working-area budget', async ({ page }) => {
      await seedPrimaryRouteBudgetData(page)
      await openShellWithPipelineFixture(
        page,
        200,
        [{
          id: 3, name: 'Budget Provider', server_url: 'https://provider.test/playlist.m3u',
          file_path: null, server_group: null, max_streams: 2, is_active: true,
          created_at: '2026-07-27T00:00:00Z', updated_at: '2026-07-27T12:00:00Z',
          user_agent: null, profiles: [], locked: false, channel_groups: [],
          refresh_interval: 24, custom_properties: null, account_type: 'STD',
          username: null, password: null, stale_stream_days: 0, priority: 0,
          status: 'success', last_message: null, enable_vod: false,
          auto_enable_new_groups_live: false, auto_enable_new_groups_vod: false,
          auto_enable_new_groups_series: false,
        }],
        [{
          id: 5, name: 'Budget EPG', source_type: 'xmltv', url: 'https://epg.test/guide.xml',
          is_active: true, status: 'success', epg_data_count: 2, refresh_interval: 24,
          updated_at: '2026-07-27T12:00:00Z',
        }],
        true,
      )
      await dismissFirstRunPromptIfPresent(page)
      for (const consumer of routeConsumers) {
        await page.getByRole('link', { name: consumer.name }).click()
        const settled = await expectSettledRoute(page, consumer)
        if (consumer.name === 'Channel Manager') {
          await page.locator('.channels-pane').getByRole('button', { name: /Sports/ }).click()
        }
        const task = page.locator(consumer.task).first()
        await expect(task, `${consumer.name} must render deterministic task content`).toBeVisible()
        await expectLayoutSettled(page, ['#main-content', '.route-page-header', consumer.task])
        const budget = await page.evaluate(() => {
          const main = document.querySelector<HTMLElement>('#main-content')!
          const header = main.querySelector<HTMLElement>('.route-page-header')!
          const description = header.querySelector<HTMLElement>('.header-description')!
          const headerRect = header.getBoundingClientRect()
          return {
            headerHeight: headerRect.height,
            descriptionSingleLine: description.scrollHeight <= description.clientHeight + 1,
            descriptionRecoverable: description.title === description.textContent?.trim(),
            noDocumentXOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            noMainXOverflow: main.scrollWidth <= main.clientWidth + 1,
          }
        })
        // This is a working-height budget — chrome must not eat the work area —
        // not a spacing pin; the header's exact padding and row-gap are pinned
        // against the stylesheets in PageHeader.test.tsx. Bead
        // enhancedchannelmanager-sl7dx moved both ceilings down by exactly what
        // the worst-case route (M3U Changes) reclaimed at each viewport, 20px at
        // 1920x1080 and 4px at 1280x720, so the budget keeps the same 24.78px /
        // 18.78px of headroom it has always carried rather than silently
        // loosening by the width of the reclaim.
        expect(budget.headerHeight, `${consumer.name} route chrome must preserve working height`).toBeLessThanOrEqual(
          viewport.height === 720 ? 256 : 270,
        )
        expect(budget.descriptionRecoverable).toBe(true)
        if (viewport.height === 720) expect(budget.descriptionSingleLine).toBe(true)
        expect(budget.noDocumentXOverflow).toBe(true)
        expect(budget.noMainXOverflow).toBe(true)

        expect(await task.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0))
          return visibleHeight >= Math.min(120, rect.height)
            && rect.right <= document.documentElement.clientWidth + 1
        }), `${consumer.name} task content must be above fold and contained`).toBe(true)

        const focusTarget = page.locator(consumer.focus).first()
        await focusTarget.focus()
        await expect(focusTarget).toBeFocused()
        expect(await focusTarget.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          const header = document.querySelector<HTMLElement>('.route-page-header')!.getBoundingClientRect()
          const insideHeader = rect.top >= header.top && rect.bottom <= header.bottom
          const belowHeader = rect.top >= header.bottom
          // The footer was removed with the header status pill, so the bottom
          // bound of the work area is now the viewport itself.
          return (insideHeader || belowHeader) && rect.bottom <= window.innerHeight
            && rect.left >= document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect().left
        }), `${consumer.name} primary control focus must not be obscured`).toBe(true)

        if (consumer.status) {
          const status = page.locator(consumer.status).first()
          await expect(status, `${consumer.name} freshness/status must remain visible`).toBeVisible()
          expect(await status.evaluate((element) => element.getBoundingClientRect().top < window.innerHeight)).toBe(true)
        }

        const nestedScroll = await page.evaluate((routeName) => {
          const describe = (element: HTMLElement) => {
            const id = element.id ? `#${element.id}` : ''
            const classes = [...element.classList].slice(0, 3).map((name) => `.${name}`).join('')
            return `${element.tagName.toLowerCase()}${id}${classes}`
          }
          const scrollable = [...document.querySelectorAll<HTMLElement>('#main-content, #main-content *')]
            .filter((element) => {
              const style = getComputedStyle(element)
              return /(auto|scroll)/.test(style.overflowY) && element.scrollHeight > element.clientHeight + 1
            })
          const pairs = scrollable.flatMap((element) => {
            const ancestor = scrollable.find((candidate) => candidate !== element && candidate.contains(element))
            return ancestor ? [{ inner: element, outer: ancestor }] : []
          })
          const violations = pairs.filter(({ inner }) => {
            if (routeName === 'Channel Manager') {
              return !inner.closest('.channels-pane, .streams-pane')
            }
            if (routeName === 'Settings' || routeName === 'Stats') {
              return false
            }
            return true
          })
          return {
            scrollables: scrollable.map(describe),
            violations: violations.map(({ inner, outer }) => `${describe(inner)} inside ${describe(outer)}`),
            longPageHasSingleOwnedScroller: violations.length === 0,
          }
        }, consumer.name)
        expect(nestedScroll.violations, `${consumer.name} has unjustified nested same-axis scrolling: ${nestedScroll.scrollables.join(', ')}`)
          .toEqual([])
          expect(
            nestedScroll.longPageHasSingleOwnedScroller,
            `${consumer.name} must not nest its task scroller; the Settings navigation rail is an independent sibling`,
          ).toBe(true)
      }
    })

    test('every applicable route alternate state preserves geometry and recovery', async ({ page }) => {
      await seedChannelWorkspace(page, false)
      await openShellWithPipelineFixture(page)
      await page.route(/\/api\/channels\/logos(?:\/|\?|$)/, (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
      }))
      for (const endpoint of [
        /\/api\/health(?:\?|$)/,
        /\/api\/tasks(?:\?|$)/,
        /\/api\/journal(?:\/stats)?(?:\?|$)/,
        /\/api\/m3u\/changes(?:\/summary)?(?:\?|$)/,
        /\/api\/stats\/[^?]+(?:\?|$)/,
      ]) {
        await page.route(endpoint, (route) => route.fulfill({
          status: 503, contentType: 'application/json',
          body: JSON.stringify({ detail: 'Deterministic alternate-state fixture' }),
        }))
      }
      await dismissFirstRunPromptIfPresent(page)

      for (const consumer of routeConsumers) {
        if (!consumer.alternate) {
          expect(consumer.name, 'Settings has no meaningful empty state; save/reload errors are covered by the dedicated retained-edit journey')
            .toBe('Settings')
          continue
        }
        await page.getByRole('link', { name: consumer.name }).click()
        await expect(page.locator('#main-content h1')).toHaveText(consumer.heading)
        const state = page.locator(consumer.alternate.selector).first()
        await expect(state, `${consumer.name} must render mapped ${consumer.alternate.state}`).toBeVisible()

        const geometry = await state.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          const header = document.querySelector<HTMLElement>('.route-page-header')!.getBoundingClientRect()
          const main = document.querySelector<HTMLElement>('#main-content')!
          return {
            belowHeader: rect.top >= header.bottom - 1,
            withinViewport: rect.top < window.innerHeight && rect.bottom > header.bottom,
            contained: rect.left >= main.getBoundingClientRect().left - 1
              && rect.right <= main.getBoundingClientRect().right + 1,
            noDocumentXOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
          }
        })
        expect(geometry, `${consumer.name} ${consumer.alternate.state} geometry`).toEqual({
          belowHeader: true,
          withinViewport: true,
          contained: true,
          noDocumentXOverflow: true,
        })

        const control = page.locator(consumer.alternate.recovery ?? consumer.focus).first()
        if (await control.isVisible().catch(() => false)) {
          await control.focus()
          await expect(control).toBeFocused()
          expect(await control.evaluate((element) => {
            const rect = element.getBoundingClientRect()
            return rect.top >= 0 && rect.bottom <= window.innerHeight
          })).toBe(true)
        }
      }
    })

    if (viewport.width === 1280) {
      test('collapsed navigation recovers width for every primary route without losing controls', async ({ page }) => {
        await openShellWithPipelineFixture(page)
        await dismissFirstRunPromptIfPresent(page)
        await page.getByRole('button', { name: 'Collapse navigation' }).click()
        for (const consumer of routeConsumers) {
          await page.getByRole('link', { name: consumer.name }).click()
          const settled = await expectSettledRoute(page, consumer)
          await expect(settled).toBeVisible()
          expect(await page.evaluate(() => (
            document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
          )), `${consumer.name} must fit after operator width recovery`).toBe(true)
        }
      })

      test('Channel Pipeline recovers every compact secondary action by keyboard', async ({ page }) => {
        await openShellWithPipelineFixture(page)
        await dismissFirstRunPromptIfPresent(page)
        await page.getByRole('link', { name: 'Channel Pipeline' }).click()
        await expectSettledRoute(page, routeConsumers.find((consumer) => consumer.name === 'Channel Pipeline')!)

        await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeVisible()
        for (const name of ['Dry run', 'Import', 'Export', 'Pipeline Debug Bundle']) {
          await expect(page.getByRole('button', { name, exact: true })).toBeHidden()
        }

        const recovery = page.getByRole('button', { name: 'More Channel Pipeline actions' })
        await recovery.press('ArrowDown')
        const menu = page.getByRole('menu')
        await expect(menu).toBeVisible()
        for (const name of ['Dry Run', 'Import', 'Export', 'Pipeline Debug Bundle']) {
          await expect(menu.getByRole('menuitem', { name })).toBeAttached()
        }
        await expect(menu.getByRole('menuitem', { name: 'Import' })).toBeFocused()
        await menu.press('End')
        await expect(menu.getByRole('menuitem', { name: 'Pipeline Debug Bundle' })).toBeFocused()
        await menu.press('Escape')
        await expect(menu).toHaveCount(0)
        await expect(recovery).toBeFocused()
      })
    }

    test('Dashboard renders the approved compact inventory with actionable links above the fold', async ({ page }) => {
      await seedChannelWorkspace(page, true, 2)
      await page.route(/\/api\/health(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'healthy', service: 'ECM', version: '9.9.9', release_channel: 'stable', git_commit: 'fixture' }),
      }))
      await page.route(/\/api\/m3u\/changes\/summary(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_changes: 3, groups_added: 0, groups_removed: 0, streams_added: 2,
          streams_removed: 1, accounts_affected: [3], since: '2026-07-26T12:00:00Z',
        }),
      }))
      await page.route(/\/api\/tasks(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tasks: [
          { task_id: 'refresh', enabled: true, effective_enabled: true, status: 'running', last_run: '2026-07-27T01:00:00Z' },
          { task_id: 'cleanup', enabled: true, effective_enabled: true, status: 'failed', last_run: '2026-07-27T02:00:00Z' },
        ] }),
      }))
      await page.route(/\/api\/journal\/stats(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_entries: 9, by_category: { channel: 5, stream: 4 }, by_action_type: {},
          date_range: { oldest: '2026-07-20T00:00:00Z', newest: '2026-07-27T02:00:00Z' },
        }),
      }))
      await openShellWithPipelineFixture(page, 200, [{ id: 3, name: 'Fixture Provider' }])
      await dismissFirstRunPromptIfPresent(page)
      await page.getByRole('link', { name: 'Dashboard' }).click()

      const dashboard = page.getByRole('region', { name: 'System summary' })
      await expect(dashboard).toBeVisible()
      await expect(dashboard.locator('article')).toHaveCount(6)
      for (const value of ['healthy', '2 channels', '1 stream', '1 account', '3 changes', '2 enabled', '9 entries']) {
        await expect(dashboard.getByText(value, { exact: true })).toBeVisible()
      }
      await expect(dashboard.getByRole('link', { name: /Open Scheduled work/ }))
        .toHaveAttribute('href', '#settings/scheduled-tasks')
      await expect(dashboard.getByRole('link', { name: /Open Recent M3U changes/ }))
        .toHaveAttribute('href', '#m3u-changes?hours=24')
      expect(await dashboard.evaluate((element) => {
        const rect = element.getBoundingClientRect()
        const cards = [...element.querySelectorAll<HTMLElement>('article')]
        return {
          aboveFold: cards.every((card) => card.getBoundingClientRect().bottom <= window.innerHeight),
          contained: rect.right <= document.documentElement.clientWidth + 1,
          noDocumentOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
          paddingRight: getComputedStyle(element).paddingRight,
        }
      })).toEqual({
        aboveFold: true,
        contained: true,
        noDocumentOverflow: true,
        paddingRight: viewport.width === 1280 ? '16px' : '24px',
      })
    })

    test('Dashboard preserves unfiltered totals after channel search and provider-scoped stream metadata', async ({ page }) => {
      await seedChannelWorkspace(page, true, 2)
      await page.route(/\/api\/channels(?:\?|$)/, (route) => {
        const filtered = new URL(route.request().url()).searchParams.has('search')
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ count: filtered ? 1 : 2, next: null, previous: null, results: [] }),
        })
      })
      await page.route(/\/api\/stream-groups(?:\?|$)/, (route) => {
        const scoped = new URL(route.request().url()).searchParams.has('m3u_account_id')
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify(scoped ? [{ name: 'Provider Sports', count: 1 }] : [
            { name: 'Provider Sports', count: 4 }, { name: 'Provider News', count: 3 },
          ]),
        })
      })
      await page.route(/\/api\/streams(?:\?|$)/, (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ count: 9, next: null, previous: null, results: [] }),
      }))
      await openShellWithPipelineFixture(page, 200, [{ id: 3, name: 'Fixture Provider' }])
      await dismissFirstRunPromptIfPresent(page)
      await page.getByRole('textbox', { name: 'Search channels' }).fill('subset')
      await page.locator('.streams-pane').getByRole('button', { name: /All Providers/ }).click()
      await page.getByRole('checkbox', { name: 'Fixture Provider' }).check()
      await page.getByRole('link', { name: 'Dashboard' }).click()
      const dashboard = page.getByRole('region', { name: 'System summary' })
      await expect(dashboard.getByText('2 channels', { exact: true })).toBeVisible()
      await expect(dashboard.getByText('9 streams', { exact: true })).toBeVisible()
    })

    test('[release:operator-workspace] Channel Manager keeps the deterministic two-pane workspace usable with both navigation widths', async ({ page }, testInfo) => {
      test.setTimeout(60_000)
      await seedChannelWorkspace(page, true, 2)
      await openShellWithPipelineFixture(
        page,
        200,
        [{ id: 3, name: 'Fixture Provider' }],
        [{ id: 5, name: 'Schedules Direct' }],
      )
      await dismissFirstRunPromptIfPresent(page)

      await expect(page.locator('#main-content h1')).toHaveText('OPERATIONS / CHANNEL MANAGER')
      await expect(page.locator('#main-content h1')).toHaveCount(1)
      await expect(page.getByRole('region', { name: 'Channels' })).toBeVisible()
      await expect(page.getByRole('region', { name: 'Streams' })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Channels', level: 2 })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Streams', level: 2 })).toBeVisible()
      await expect(page.getByLabel('2 channels')).toBeVisible()
      await expect(page.getByLabel('1 total stream')).toBeVisible()
      const separator = page.getByRole('separator', { name: 'Resize Channels and Streams panes' })
      await expect(separator).toBeVisible()
      await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
      await expect(page.getByRole('main')).toHaveAttribute('id', 'main-content')
      await expect(page.getByRole('banner')).toBeVisible()
      // The footer was removed once service status and the update notice moved
      // into the header, so the shell intentionally exposes no contentinfo.
      await expect(page.getByRole('contentinfo')).toHaveCount(0)
      await expect(page.getByRole('status').first()).toBeVisible()
      await expect(page.locator('#main-content h1')).toHaveCount(1)
      const expandedShell = await shellMetrics(page)
      expect(expandedShell).toMatchObject({
        width: 244,
        noSidebarXOverflow: true,
        mainClear: true,
        noDocumentXOverflow: true,
        targetsPractical: true,
        labelsHidden: false,
      })
      for (const group of ['Overview', 'Operations', 'Automation', 'Insights', 'System']) {
        await expect(page.locator('.navigation-group h2', { hasText: group })).toBeVisible()
      }
      expect(await page.locator('.navigation-destination').evaluateAll((links) =>
        links.every((link) => Boolean(link.getAttribute('aria-label')) && link.getAttribute('title') === link.getAttribute('aria-label')),
      )).toBe(true)

      const separatorBox = await separator.boundingBox()
      if (!separatorBox) throw new Error('splitter has no geometry')
      const initialSplit = Number(await separator.getAttribute('aria-valuenow'))
      await page.mouse.move(separatorBox.x + separatorBox.width / 2, separatorBox.y + separatorBox.height / 2)
      await page.mouse.down()
      await page.mouse.move(separatorBox.x - 80, separatorBox.y + separatorBox.height / 2, { steps: 5 })
      await page.mouse.up()
      const draggedSplit = Number(await separator.getAttribute('aria-valuenow'))
      expect(draggedSplit).toBeGreaterThanOrEqual(35)
      expect(draggedSplit).toBeLessThanOrEqual(70)
      expect(draggedSplit).toBeLessThan(initialSplit)

      const channelSearch = page.getByRole('textbox', { name: 'Search channels' })
      await channelSearch.fill('deliberately long')
      await expect(channelSearch).toHaveValue('deliberately long')
      await page.locator('.channels-pane').getByRole('button', { name: 'Clear search' }).click()

      const streamSearch = page.getByRole('textbox', { name: 'Search streams' })
      await streamSearch.fill('source stream')
      await expect(streamSearch).toHaveValue('source stream')
      await expect(page.getByLabel('1 matching stream')).toBeVisible()
      await page.locator('.streams-pane').getByRole('button', { name: 'Clear search' }).click()

      const providerFilter = page.locator('.streams-pane').getByRole('button', { name: /All Providers/ })
      await providerFilter.click()
      await page.getByRole('checkbox', { name: 'Fixture Provider' }).check()
      await expect(page.locator('.streams-pane').getByRole('button', { name: /1 provider/ })).toBeVisible()
      const groupFilter = page.locator('.streams-pane').getByRole('button', { name: /All Groups/ })
      await groupFilter.press('Enter')
      await page.getByRole('checkbox', { name: 'Provider Sports' }).check()
      await expect(page.getByLabel('1 filtered stream')).toBeVisible()
      await page.getByRole('button', { name: 'Clear all filters' }).click()

      const moreActions = page.locator('.channels-pane').getByRole('button', { name: 'More actions' })
      await moreActions.focus()
      await moreActions.press('Enter')
      const paneMenu = page.getByRole('menu', { name: 'Channel pane actions' })
      await expect(paneMenu).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Channel Profiles' })).toBeFocused()
      await expect(page.getByRole('menuitem', { name: 'Channel Profiles' }).locator('.material-icons')).toHaveText('group')
      await page.keyboard.press('End')
      await expect(page.getByRole('menuitem', { name: 'Export CSV' })).toBeFocused()
      await page.keyboard.press('Home')
      await expect(page.getByRole('menuitem', { name: 'Channel Profiles' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(paneMenu).toHaveCount(0)
      await expect(moreActions).toBeFocused()

      const category = page.getByRole('button', { name: /^Other/ })
      await category.click()
      const providerGroup = page.getByRole('button', { name: /Provider Sports/ })
      await providerGroup.press('Enter')
      const inventoryIdentity = page.locator('.streams-pane .stream-name').filter({
        hasText: 'A deliberately long source stream identity that must ellipsize before inventory actions',
      })
      await expect(inventoryIdentity).toBeVisible()
      await expect(page.locator('.streams-pane .drag-handle')).toHaveCount(0)
      await expect(page.locator('.streams-pane .group-drag-handle')).toHaveCount(0)
      await expect(page.locator('.channels-pane .group-drag-handle')).toHaveCount(0)
      const previewAction = page.locator('.streams-pane').getByRole('button', { name: 'Preview stream in browser' })
      await previewAction.click()
      await page.getByRole('button', { name: 'Close', exact: true }).first().click()
      const copyAction = page.locator('.streams-pane').getByRole('button', { name: 'Copy stream URL' })
      await copyAction.focus()
      await copyAction.press('Enter')
      await expect(page.locator('.streams-pane .copy-feedback')).toBeVisible()
      const inventoryVlc = page.locator('.streams-pane').getByRole('button', { name: 'Open in VLC' })
      await inventoryVlc.focus()
      await expect(inventoryVlc).toBeFocused()
      await expect(inventoryVlc).toHaveCSS('opacity', '1')
      const inventoryPreview = page.locator('.streams-pane').getByRole('button', { name: 'Preview stream in browser' })
      await inventoryPreview.focus()
      await expect(inventoryPreview).toHaveCSS('opacity', '1')
      await inventoryPreview.press('Enter')
      await expect(page.getByRole('button', { name: 'Close', exact: true }).first()).toBeVisible()
      await page.getByRole('button', { name: 'Close', exact: true }).first().click()

      await page.locator('.channels-pane').getByRole('button', { name: /Sports/ }).click()
      await expect(page.locator('.channel-column-headers')).toHaveText(/NumberChannel \/ GuideStreams/)
      await expect(page.locator('.channel-number-col').first()).toHaveText('101')
      await expect(page.locator('.channel-number-col').first()).not.toContainText('#')
      await expect(page.getByText('Schedules Direct – ESPN')).toBeVisible()
      await expect(page.getByLabel('1 stream; failed probe')).toBeVisible()
      const channelArtwork = page.locator('.channel-logo').first()
      await expect(channelArtwork).toHaveAttribute('src', '/persisted-channel-artwork.png')
      const normalTypography = await page.evaluate(() => ({
        root: getComputedStyle(document.documentElement).fontSize,
        channel: getComputedStyle(document.querySelector<HTMLElement>('.channel-name')!).fontSize,
        inventory: getComputedStyle(document.querySelector<HTMLElement>('.streams-pane .stream-name')!).fontSize,
        actionIcon: getComputedStyle(document.querySelector<HTMLElement>('.channels-pane .channel-menu-btn .material-icons')!).fontSize,
      }))
      expect(Number.parseFloat(normalTypography.root)).toBeGreaterThanOrEqual(16)
      expect(Number.parseFloat(normalTypography.channel)).toBeGreaterThanOrEqual(12)
      expect(Number.parseFloat(normalTypography.inventory)).toBeGreaterThanOrEqual(12)
      expect(Number.parseFloat(normalTypography.actionIcon)).toBeGreaterThanOrEqual(16)
      await expectMainKeyboardTraversal(page)
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'populated-normal-expanded')
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expect.poll(() => shellMetrics(page)).toMatchObject({
        width: 68,
        noSidebarXOverflow: true,
        mainClear: true,
        noDocumentXOverflow: true,
        targetsPractical: true,
        labelsHidden: true,
        iconsCentered: true,
      })
      await expect(page.locator('.navigation-group h2')).toHaveCount(5)
      expect(await page.locator('.navigation-group h2').evaluateAll((headings) =>
        headings.every((heading) => {
          const style = getComputedStyle(heading)
          const rect = heading.getBoundingClientRect()
          return style.display === 'none' && rect.width === 0 && rect.height === 0
        }),
      )).toBe(true)
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'populated-normal-collapsed')
      await page.getByRole('button', { name: 'Expand navigation' }).click()
      const expectChannelColumnsAligned = async () => {
        expect(await page.evaluate(() => {
          const center = (selector: string) => {
            const rect = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
            return rect.left + rect.width / 2
          }
          const left = (selector: string) => document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().left
          return {
            number: Math.abs(center('.channel-column-number') - center('.channel-number-col')),
            identity: Math.abs(left('.channel-column-identity') - left('.channel-content')),
            streams: Math.abs(center('.channel-column-streams') - center('.channel-streams-count')),
          }
        })).toEqual({ number: 0, identity: 0, streams: 0 })
      }
      await expectChannelColumnsAligned()
      await expect(page.locator('.channels-pane .channel-drag-handle')).toHaveCount(0)
      await page.getByRole('button', { name: 'Edit Mode' }).click()
      await expectChannelColumnsAligned()
      await expect(channelArtwork).toHaveAttribute('src', '/staged-channel-artwork.png')
      const expectEditContainment = async () => {
        expect(await page.evaluate(() => {
          const paneContained = (selector: string) => {
            const pane = document.querySelector<HTMLElement>(selector)!
            const paneRect = pane.getBoundingClientRect()
            return [...pane.querySelectorAll<HTMLElement>('.channel-item, .stream-item, .inline-stream-item, button')]
              .filter((element) => element.offsetParent !== null)
              .every((element) => {
                const rect = element.getBoundingClientRect()
                return rect.left >= paneRect.left - 1 && rect.right <= paneRect.right + 1
              })
          }
          return {
            document: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            channels: paneContained('.channels-pane'),
            streams: paneContained('.streams-pane'),
          }
        })).toEqual({ document: true, channels: true, streams: true })
      }
      await expectEditContainment()
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expectEditContainment()
      await page.getByRole('button', { name: 'Expand navigation' }).click()
      await expectEditContainment()
      await expect(page.getByLabel('Drag channel group Sports to reorder')).toBeVisible()
      await expect(page.getByLabel(/^Drag channel A deliberately long channel identity .* to reorder$/)).toBeVisible()
      await expect(page.getByLabel('Drag stream group Provider Sports to Channels pane to create channels')).toBeVisible()
      await expect(page.getByLabel(/Drag inventory stream .* to assign it to a channel/)).toBeVisible()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'populated-edit-expanded')
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expect(page.locator('.primary-sidebar')).toHaveCSS('width', '68px')
      await expectMainKeyboardTraversal(page)
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'populated-edit-collapsed')
      await page.getByRole('button', { name: 'Expand navigation' }).click()

      const inventoryDrag = page.getByLabel(/Drag inventory stream .* to assign it to a channel/)
      await inventoryDrag.focus()
      await inventoryDrag.press('Enter')
      const channelDestinations = page.getByRole('menu', { name: 'Choose channel destination' })
      await expect(channelDestinations.getByRole('menuitem', { name: /A deliberately long channel identity/ })).toBeFocused()
      await page.keyboard.press('ArrowDown')
      await expect(channelDestinations.getByRole('menuitem', { name: 'Second fixture channel' })).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(page.locator('.keyboard-drag-status')).toContainText(/Dropped inventory stream .* on channel Second fixture channel/)

      const groupDrag = page.getByLabel('Drag stream group Provider Sports to Channels pane to create channels')
      await groupDrag.focus()
      await groupDrag.press('Enter')
      const groupDestinations = page.getByRole('menu', { name: 'Choose channel group destination' })
      await expect(groupDestinations.getByRole('menuitem', { name: 'Sports' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(groupDestinations).toHaveCount(0)
      await expect(groupDrag).toBeFocused()
      await groupDrag.press('Enter')
      await expect(page.getByRole('menu', { name: 'Choose channel group destination' })
        .getByRole('menuitem', { name: 'Sports' })).toBeFocused()
      await page.keyboard.press('Enter')
      const createFromGroupDialog = page.getByRole('dialog', {
        name: 'Create Channels from "Provider Sports"',
      })
      await expect(createFromGroupDialog).toBeVisible()
      await expect(createFromGroupDialog.getByText('"Sports"')).toBeVisible()
      await expect(createFromGroupDialog.locator('input:focus')).toHaveCount(1)
      await createFromGroupDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
      await expect(groupDrag).toBeFocused()

      await expect(page.getByRole('toolbar', { name: 'Selection actions' })).toHaveCount(0)
      await page.getByRole('checkbox', { name: /Select channel A deliberately long channel identity/ }).click()
      const selectionBar = page.getByRole('toolbar', { name: 'Selection actions' })
      await expect(selectionBar).toBeVisible()
      await expect(selectionBar.getByRole('status', { name: '1 channel selected' })).toBeVisible()
      expect(await selectionBar.getByRole('button').allTextContents()).toEqual([
        'deleteDelete',
        'speedProbe',
        'manage_searchFind Duplicates',
        'tagRenumber',
        'live_tvAssign EPG',
        'more_vertMore',
        'closeClear',
      ])
      await expect(selectionBar.getByRole('button', { name: 'Merge' })).toHaveCount(0)
      await page.getByRole('checkbox', { name: 'Select channel Second fixture channel' }).click()
      await expect(selectionBar.getByRole('status', { name: '2 channels selected' })).toBeVisible()
      await expect(selectionBar.getByRole('button', { name: 'Merge' })).toBeVisible()
      const probeSelected = selectionBar.getByRole('button', { name: 'Probe' })
      await probeSelected.click()
      const probingSelected = selectionBar.getByRole('button', { name: 'Probing…' })
      await expect(probingSelected).toBeDisabled()
      await expect(probingSelected.locator('.material-icons')).toHaveText('sync')
      await expect(selectionBar.getByRole('button', { name: 'Probe' })).toBeEnabled()

      await page.clock.install()
      const assignEpg = selectionBar.getByRole('button', { name: 'Assign EPG' })
      await assignEpg.click()
      await expect(assignEpg).toBeDisabled()
      await expect(assignEpg.locator('.material-icons')).toHaveText('sync')
      await page.clock.runFor(60)
      await expect(page.getByRole('heading', { name: 'Bulk EPG Assignment' })).toBeVisible()
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      await page.clock.resume()
      await dismissReleaseToasts(page)
      const selectionMore = selectionBar.getByRole('button', { name: 'More selection actions' })
      await selectionMore.focus()
      await selectionMore.press('Enter')
      const selectionMenu = page.getByRole('menu', { name: 'More selection actions' })
      await expect(selectionMenu).toBeVisible()
      await expect(selectionMenu.getByRole('menuitem', { name: /Move to group/ })).toBeFocused()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'populated-edit-selection-menu')
      await page.keyboard.press('Escape')
      await expect(selectionMenu).toHaveCount(0)
      await expect(selectionMore).toBeFocused()
      await selectionBar.getByRole('button', { name: 'Clear selection' }).click()
      await expect(selectionBar).toHaveCount(0)
      await moreActions.focus()
      await moreActions.press('Enter')
      const editPaneMenu = page.getByRole('menu', { name: 'Channel pane actions' })
      for (const name of [
        'Channel Profiles', 'Hidden Groups', 'Sort All Streams',
        'Renumber All Groups', 'CSV Template', 'Export CSV', 'Import CSV',
      ]) {
        await expect(editPaneMenu.getByRole('menuitem', { name })).toBeVisible()
      }
      await page.keyboard.press('ArrowDown')
      await expect(editPaneMenu.getByRole('menuitem', { name: 'Hidden Groups' })).toBeFocused()
      await page.keyboard.press('ArrowDown')
      const sortAll = editPaneMenu.getByRole('menuitem', { name: 'Sort All Streams' })
      await expect(sortAll).toBeFocused()
      await page.keyboard.press('ArrowRight')
      const sortMenu = page.getByRole('menu', { name: 'Sort all streams' })
      await expect(sortMenu.getByRole('menuitem', { name: 'Smart Sort' })).toBeFocused()
      await page.keyboard.press('End')
      await expect(sortMenu.getByRole('menuitem', { name: 'By Framerate' })).toBeFocused()
      await page.keyboard.press('ArrowLeft')
      await expect(sortAll).toBeFocused()
      await page.keyboard.press('ArrowRight')
      await expect(sortMenu.getByRole('menuitem', { name: 'Smart Sort' })).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(editPaneMenu).toHaveCount(0)
      await expect(moreActions).toBeFocused()
      await moreActions.press('Enter')
      await expect(page.getByRole('menu', { name: 'Channel pane actions' }).getByRole('menuitem', { name: 'Channel Profiles' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(editPaneMenu).toHaveCount(0)
      await expect(moreActions).toBeFocused()
      await page.getByRole('button', { name: 'Done' }).click()
      await expect(page.getByRole('heading', { name: 'Exit Edit Mode' })).toBeVisible()
      await page.getByRole('button', { name: 'Discard', exact: true }).click()
      await expect(channelArtwork).toHaveAttribute('src', '/persisted-channel-artwork.png')
      await expectEditContainment()
      await expect(page.locator('.channels-pane .channel-drag-handle')).toHaveCount(0)
      await expect(page.locator('.channels-pane .group-drag-handle')).toHaveCount(0)
      await expect(page.locator('.streams-pane .drag-handle')).toHaveCount(0)
      await expect(page.locator('.streams-pane .group-drag-handle')).toHaveCount(0)
      const channelActions = page.getByRole('button', { name: 'Channel actions' })
      await channelActions.focus()
      await channelActions.press('Enter')
      await expect(page.getByRole('menu', { name: 'Channel actions' })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Probe Channel' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(page.getByRole('menu', { name: 'Channel actions' })).toHaveCount(0)
      await expect(channelActions).toBeFocused()
      const channelIdentity = page.getByText('A deliberately long channel identity that must remain inside the Channels pane')
      await channelIdentity.click()
      await expect(page.locator('.inline-stream-name').filter({ hasText: 'A deliberately long source stream identity' })).toBeVisible()
      const timeoutWarning = page.getByLabel(
        'Probe timeout. Probe exceeded the configured 30 second deadline while waiting for the upstream provider response. 3 of 3 strikes.',
      )
      await expect(timeoutWarning).toHaveText(/Probe timeout\s*•\s*3\/3/)
      const assignedPreview = page.locator('.channels-pane').getByRole('button', { name: 'Preview stream in browser' })
      await assignedPreview.focus()
      await expect(assignedPreview).toHaveCSS('opacity', '1')
      await assignedPreview.press('Enter')
      await expect(page.getByRole('button', { name: 'Close', exact: true }).first()).toBeVisible()
      await page.getByRole('button', { name: 'Close', exact: true }).first().click()

      expect(await inventoryIdentity.evaluate((identity) => {
        const info = identity.closest<HTMLElement>('.stream-info')!
        const row = identity.closest<HTMLElement>('.stream-item')!
        const actionsGroup = row.querySelector<HTMLElement>('.stream-actions')!
        const actions = [...actionsGroup.querySelectorAll<HTMLElement>('button')]
        const infoRect = info.getBoundingClientRect()
        const direct = [...row.children]
        const identityStyle = getComputedStyle(identity)
        return {
          strictDomOrder: direct.indexOf(row.querySelector('.stream-artwork-slot')!) <
              direct.indexOf(info)
            && direct.indexOf(info) < direct.indexOf(actionsGroup)
            && direct[direct.length - 1] === actionsGroup,
          stableBlankArtwork: row.querySelector('.stream-artwork-slot')?.children.length === 0,
          usableIdentityWidth: infoRect.width >= 80,
          ellipsisContract: identityStyle.overflow === 'hidden'
            && identityStyle.textOverflow === 'ellipsis'
            && identityStyle.whiteSpace === 'nowrap',
          actionsVisible: actions.every((action) => action.getBoundingClientRect().right <= row.getBoundingClientRect().right + 1),
          noOverlap: actions.every((action) => action.getBoundingClientRect().left >= infoRect.right - 1),
        }
      })).toEqual({
        strictDomOrder: true,
        stableBlankArtwork: true,
        usableIdentityWidth: true,
        ellipsisContract: true,
        actionsVisible: true,
        noOverlap: true,
      })
      expect(await page.evaluate(() => {
        const pane = document.querySelector<HTMLElement>('.streams-pane')!.getBoundingClientRect()
        const streamUrl = document.querySelector<HTMLElement>('.streams-pane .stream-url')!.getBoundingClientRect()
        const channel = document.querySelector<HTMLElement>('.channels-pane .channel-name')!.getBoundingClientRect()
        const channelPane = document.querySelector<HTMLElement>('.channels-pane')!.getBoundingClientRect()
        const inlineName = document.querySelector<HTMLElement>('.inline-stream-name')!.getBoundingClientRect()
        const inlineRow = document.querySelector<HTMLElement>('.inline-stream-item')!
        const inlineInfo = inlineRow.querySelector<HTMLElement>('.inline-stream-info')!.getBoundingClientRect()
        const inlineActions = inlineRow.querySelector<HTMLElement>('.inline-stream-actions')!.getBoundingClientRect()
        const warning = inlineRow.querySelector<HTMLElement>('.probe-warning-summary')!.getBoundingClientRect()
        const inlineUrl = inlineRow.querySelector<HTMLElement>('.inline-stream-url')!.getBoundingClientRect()
        const provider = inlineRow.querySelector<HTMLElement>('.inline-stream-provider')!.getBoundingClientRect()
        return {
          urlContained: streamUrl.left >= pane.left && streamUrl.right <= pane.right + 1,
          channelContained: channel.left >= channelPane.left && channel.right <= channelPane.right + 1,
          inlineContained: inlineName.left >= channelPane.left && inlineName.right <= channelPane.right + 1,
          inlineIdentityUsable: inlineInfo.width >= 80,
          inlineActionsFixed: inlineActions.left >= inlineInfo.right - 1
            && inlineActions.right <= inlineRow.getBoundingClientRect().right + 1,
          timeoutDetailsUsable: warning.width >= 80 && inlineUrl.width >= 48 && provider.width >= 48,
          timeoutDetailsBeforeActions: [warning, inlineUrl, provider]
            .every((rect) => rect.right <= inlineActions.left + 1),
        }
      })).toEqual({
        urlContained: true,
        channelContained: true,
        inlineContained: true,
        inlineIdentityUsable: true,
        inlineActionsFixed: true,
        timeoutDetailsUsable: true,
        timeoutDetailsBeforeActions: true,
      })

      for (const collapsed of [false, true]) {
        if (collapsed) await page.getByRole('button', { name: 'Collapse navigation' }).click()
        const geometry = await page.evaluate(() => {
          const workspace = document.querySelector<HTMLElement>('.split-pane')!
          const channels = document.querySelector<HTMLElement>('.split-pane-left')!
          const streams = document.querySelector<HTMLElement>('.split-pane-right')!
          const main = document.querySelector<HTMLElement>('#main-content')!
          const mainRect = main.getBoundingClientRect()
          return {
            noDocumentOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            workspaceInsideMain: workspace.getBoundingClientRect().left >= mainRect.left
              && workspace.getBoundingClientRect().right <= mainRect.right + 1,
            channelsUsable: channels.clientWidth >= 300,
            streamsUsable: streams.clientWidth >= 260,
          }
        })
        expect(geometry).toEqual({
          noDocumentOverflow: true,
          workspaceInsideMain: true,
          channelsUsable: true,
          streamsUsable: true,
        })
        await testInfo.attach(
          `channel-manager-${viewport.width}x${viewport.height}-${collapsed ? 'collapsed' : 'expanded'}`,
          { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' },
        )
      }
    })

    test('[release:operator-workspace] Channel Manager empty and error states remain operable at both navigation widths', async ({ page }, testInfo) => {
      await seedChannelWorkspace(page, false)
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)

      const expectStateGeometry = async () => {
        await expect.poll(() => page.evaluate(() => {
          const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!.getBoundingClientRect()
          const main = document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect()
          return {
            oneH1: document.querySelectorAll('#main-content h1').length,
            mainClearsRail: main.left >= sidebar.right,
            noDocumentOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            sidebarNoOverflow: document.querySelector<HTMLElement>('.primary-sidebar')!.scrollWidth
              === document.querySelector<HTMLElement>('.primary-sidebar')!.clientWidth,
          }
        })).toEqual({
          oneH1: 1,
          mainClearsRail: true,
          noDocumentOverflow: true,
          sidebarNoOverflow: true,
        })
      }

      await expect(page.getByRole('status').filter({ hasText: 'No channels are configured.' })).toBeVisible()
      await expectStateGeometry()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'empty-expanded')
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expect(page.locator('.primary-sidebar')).toHaveCSS('width', '68px')
      await expectStateGeometry()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'empty-collapsed')

      await page.getByRole('button', { name: 'Expand navigation' }).click()
      await page.getByRole('button', { name: 'Edit Mode' }).click()
      await expect(page.getByRole('button', { name: 'Done' })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Create new channel', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Create new channel group' })).toBeVisible()
      await expect(page.getByRole('status').filter({ hasText: 'No channels are configured.' })).toBeVisible()
      await expect(page.locator('.channels-pane .channel-drag-handle')).toHaveCount(0)
      await expect(page.locator('.channels-pane .group-drag-handle')).toHaveCount(0)
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'empty-edit-expanded')
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expectStateGeometry()
      await expect(page.getByRole('button', { name: 'Create new channel', exact: true })).toBeVisible()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'empty-edit-collapsed')
      await page.getByRole('button', { name: 'Expand navigation' }).click()
      await page.getByRole('button', { name: 'Done' }).click()
      await expect(page.getByRole('button', { name: 'Edit Mode' })).toBeVisible()

      await page.route(/\/api\/channels(?:\?|$)/, (route) => route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Deterministic release-matrix channel failure' }),
      }))
      await page.reload({ waitUntil: 'domcontentloaded' })
      await expect(page.getByText('Channels unavailable')).toBeVisible()
      await expect(page.getByRole('button', { name: 'Retry loading channels' })).toBeVisible()
      await expect(page.locator('.channels-pane')).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Edit Mode' })).toBeDisabled()
      await expect(page.getByRole('button', { name: 'Edit Mode' }))
        .toHaveAttribute('title', 'Edit Mode is unavailable until channel data loads')
      await expectStateGeometry()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'error-expanded')
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expectStateGeometry()
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'error-collapsed')
    })

    test('[release:operator-workspace] Channel Manager renders every non-color health state and artwork fallback', async ({ page }, testInfo) => {
      await seedChannelWorkspace(page, true, 6, true)
      await openShellWithPipelineFixture(
        page,
        200,
        [{ id: 3, name: 'Fixture Provider' }],
        [{ id: 5, name: 'Schedules Direct' }],
      )
      await dismissFirstRunPromptIfPresent(page)
      await page.locator('.channels-pane').getByRole('button', { name: /Sports/ }).click()

      const statuses = [
        { name: '0 streams; no streams assigned', icon: 'warning' },
        { name: '1 stream; failed probe', icon: 'error' },
        { name: '1 stream; stale', icon: 'history' },
        { name: '1 stream; black screen', icon: 'videocam_off' },
        { name: '1 stream; low FPS', icon: 'slow_motion_video' },
        { name: '1 stream; healthy', icon: 'lan' },
      ]
      for (const status of statuses) {
        const indicator = page.getByLabel(status.name)
        await expect(indicator).toBeVisible()
        await expect(indicator.locator('.material-icons')).toHaveText(status.icon)
      }
      const noLogoRow = page.locator('.channel-item', { hasText: 'No streams status' })
      await expect(noLogoRow.locator('.channel-logo-placeholder .material-icons')).toHaveText('image')
      await expect(noLogoRow.locator('.channel-logo')).toHaveCount(0)
      await page.getByText('Healthy status', { exact: true }).click()
      await expect(page.locator('.channels-pane .meta-tag.resolution')).toHaveText('1080p')
      await expect(page.locator('.streams-pane .meta-tag.resolution, .streams-pane .probe-warning-summary')).toHaveCount(0)
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'health-and-artwork-matrix-expanded')
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expect.poll(() => shellMetrics(page)).toMatchObject({
        width: 68,
        noSidebarXOverflow: true,
        mainClear: true,
        noDocumentXOverflow: true,
        labelsHidden: true,
        iconsCentered: true,
      })
      for (const status of statuses) await expect(page.getByLabel(status.name)).toBeVisible()
      await expect(noLogoRow.locator('.channel-logo-placeholder .material-icons')).toHaveText('image')
      await captureOperatorReleaseArtifact(page, testInfo, viewport, 'health-and-artwork-matrix-collapsed')
    })
  })
}

test.describe('Channel Manager dnd-kit keyboard path', () => {
  test.use({ viewport: { width: 1280, height: 720 }, serviceWorkers: 'block' })

  test('moves a channel drag overlay through a keyboard reorder destination', async ({ page }) => {
    await seedChannelWorkspace(page, true, 2)
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('button', { name: 'Edit Mode' }).click()
    await page.locator('.channels-pane .group-toggle-btn').filter({ hasText: 'Sports' }).click()

    const channelDrag = page.getByLabel(/^Drag channel A deliberately long channel identity .* to reorder$/)
    await channelDrag.focus()
    await channelDrag.press('Space')
    await expect(page.locator('.drag-overlay-item')).toBeVisible()
    await page.keyboard.press('ArrowDown')
    await expect(page.getByRole('status').filter({
      hasText: /Draggable item 41 was moved over droppable area/,
    })).toHaveCount(1)
  })
})

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
  { width: 640, height: 360 },
]) {
  test.describe(`audited sticky controls at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, serviceWorkers: 'block' })
    test('do not cover focused Settings controls or introduce overflow traps', async ({ page }) => {
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)
      await page.getByRole('link', { name: 'Settings', exact: true }).click()
      const input = page.getByLabel('Poll interval (seconds)')
      await input.fill('45')
      await input.focus()
      await expect(page.getByRole('status', { name: 'Unsaved settings' })).toBeVisible()
      const expectFocusedControlClear = async () => {
        await expectLayoutSettled(page, [
          '#main-content',
          '.settings-content',
          '.sticky-section-nav',
          '.settings-pending-actions',
        ])
        expect(await page.evaluate(() => {
          const focused = document.activeElement!.getBoundingClientRect()
          const nav = document.querySelector('.sticky-section-nav')!.getBoundingClientRect()
          const pending = document.querySelector('.settings-pending-actions')!.getBoundingClientRect()
          const content = document.querySelector<HTMLElement>('.settings-content')!
          // The section nav must not cover the focused control. Asserting
          // "below the nav's bottom edge" only holds for the horizontal bar;
          // the rail sits beside the content and spans its full height, so the
          // real invariant is that the two rectangles do not intersect.
          const overlapX = Math.min(focused.right, nav.right) - Math.max(focused.left, nav.left)
          const overlapY = Math.min(focused.bottom, nav.bottom) - Math.max(focused.top, nav.top)
          return {
            document: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            content: content.scrollWidth <= content.clientWidth + 1,
            nav: overlapX <= 1 || overlapY <= 1,
            bottom: focused.bottom <= pending.top - 7,
          }
        })).toEqual({ document: true, content: true, nav: true, bottom: true })
      }
      await expectFocusedControlClear()

      const controls = page.locator('.settings-page input:visible, .settings-page select:visible, .settings-page textarea:visible, .settings-page button:visible')
      await controls.first().focus()
      await expectFocusedControlClear()
      await page.keyboard.press('Tab')
      await expectFocusedControlClear()
      await page.keyboard.press('Shift+Tab')
      await expectFocusedControlClear()
      await controls.last().focus()
      await expectFocusedControlClear()
      await page.keyboard.press('Shift+Tab')
      await expectFocusedControlClear()
      await page.keyboard.press('Tab')
      await expectFocusedControlClear()
    })
  })
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
  test.describe(`dense route toolbars at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, serviceWorkers: 'block' })
    test('keeps primary groups visible and secondary actions recoverable without clipping', async ({ page }) => {
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)
      for (const route of [
        { link: 'M3U Manager', toolbar: 'M3U account controls' },
        { link: 'EPG Manager', toolbar: 'EPG source controls' },
        { link: 'Logo Manager', toolbar: 'Logo inventory controls' },
        { link: 'M3U Changes', toolbar: 'M3U change filters' },
        { link: 'Journal', toolbar: 'Journal entry controls' },
      ]) {
        await page.getByRole('link', { name: route.link, exact: true }).click()
        const toolbar = page.getByRole('toolbar', { name: route.toolbar })
        await expect(toolbar).toBeVisible()
        expect(await toolbar.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          return {
            insideViewport: rect.left >= 0 && rect.right <= document.documentElement.clientWidth + 1,
            noDocumentOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
          }
        })).toEqual({ insideViewport: true, noDocumentOverflow: true })
      }
      await page.getByRole('link', { name: 'M3U Manager', exact: true }).click()
      await expect(page.getByRole('button', { name: 'Save Priorities' })).toBeDisabled()
      const more = page.getByRole('button', { name: 'M3U setup actions' })
      await more.focus()
      await more.click()
      await page.keyboard.press('Escape')
      await expect(more).toBeFocused()
    })

    test('renders the dense-route loading, empty, error, and permission state matrix', async ({ page }) => {
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)

      let releaseLogos!: () => void
      await page.route(/\/api\/channels\/logos(?:\/|\?|$)/, async (route) => {
        await new Promise<void>((resolve) => {
          releaseLogos = () => {
            void route.fulfill({
              status: 200,
              contentType: 'application/json',
              body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
            })
            resolve()
          }
        })
      })
      await page.getByRole('link', { name: 'Logo Manager', exact: true }).click()
      await expect(page.getByRole('toolbar', { name: 'Logo inventory controls' })).toBeVisible()
      await expect(page.getByText('Loading logos...')).toBeVisible()
      releaseLogos()
      await expect(page.getByText('No logos yet')).toBeVisible()

      await page.route(/\/api\/epg\/sources(?:\/|\?|$)/, (route) => route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'EPG fixture unavailable' }),
      }))
      await page.getByRole('link', { name: 'EPG Manager', exact: true }).click()
      await expect(page.getByRole('status', { name: 'EPG sources unavailable' })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Retry loading EPG sources' })).toBeVisible()

      await page.route(/\/api\/m3u\/changes(?:\?|$)/, (route) => route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Forbidden' }),
      }))
      await page.getByRole('link', { name: 'M3U Changes', exact: true }).click()
      await expect(page.getByText(/don't have permission to view M3U changes/i)).toBeVisible()
      await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)

      await page.route(/\/api\/journal(?:\?|$)/, (route) => route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Forbidden' }),
      }))
      await page.getByRole('link', { name: 'Journal', exact: true }).click()
      await expect(page.getByText(/don't have permission to view journal entries/i)).toBeVisible()
      await expect(page.getByRole('button', { name: /purge old entries/i })).toHaveCount(0)

      expect(await page.evaluate(() =>
        document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      )).toBe(true)
    })

    test('retains stale history through refresh failure and gives mixed permission failures precedence', async ({ page }) => {
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)

      let initialJournalReleased = false
      let failNextJournalRequest = false
      let resolveInitialJournal!: () => void
      const initialJournalRelease = new Promise<void>((resolve) => {
        resolveInitialJournal = resolve
      })
      const releaseInitialJournal = () => {
        initialJournalReleased = true
        resolveInitialJournal()
      }
      await page.route(/\/api\/journal(?:\?|$)/, async (route) => {
        if (!initialJournalReleased) await initialJournalRelease
        if (failNextJournalRequest) {
          failNextJournalRequest = false
          await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'Refresh failed' }) })
        } else {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              count: 1, page: 1, page_size: 50, total_pages: 1,
              results: [{
                id: 91, timestamp: '2026-07-27T12:00:00Z', category: 'channel',
                action_type: 'create', entity_id: 41, entity_name: 'Retained journal row',
                description: 'Fixture entry', before_value: null, after_value: null,
                user_initiated: true, mutation_source: 'ui', batch_id: null,
              }],
            }),
          })
        }
      })
      await page.route(/\/api\/journal\/stats(?:\?|$)/, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_entries: 1, by_category: { channel: 1 }, by_action_type: { create: 1 },
          date_range: { oldest: '2026-07-27T12:00:00Z', newest: '2026-07-27T12:00:00Z' },
        }),
      }))

      await page.getByRole('link', { name: 'Journal', exact: true }).click()
      const journalToolbar = page.getByRole('toolbar', { name: 'Journal entry controls' })
      await expect(journalToolbar).toBeVisible()
      await expect(journalToolbar.getByPlaceholder('Search entries...')).toBeDisabled()
      await expect(journalToolbar.getByRole('button', { name: /purge old entries/i })).toBeDisabled()
      releaseInitialJournal()
      await expect(page.getByText('Retained journal row')).toBeVisible()
      failNextJournalRequest = true
      await page.getByRole('button', { name: /refresh/i }).click()
      await expect(page.getByText(/showing previously loaded entries/i)).toBeVisible()
      await expect(page.getByText('Retained journal row')).toBeVisible()
      await page.getByRole('button', { name: 'Retry', exact: true }).click()
      await expect(page.getByText(/showing previously loaded entries/i)).toHaveCount(0)
      await expect(page.getByText('Retained journal row')).toBeVisible()

      let initialChangesReleased = false
      let failNextChangesRequest = false
      let mixedPermission = false
      let resolveInitialChanges!: () => void
      const initialChangesRelease = new Promise<void>((resolve) => {
        resolveInitialChanges = resolve
      })
      const releaseInitialChanges = () => {
        initialChangesReleased = true
        resolveInitialChanges()
      }
      await page.route(/\/api\/m3u\/changes(?:\?|$)/, async (route) => {
        if (!initialChangesReleased) await initialChangesRelease
        if (mixedPermission || failNextChangesRequest) {
          failNextChangesRequest = false
          return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'Changes failed' }) })
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            results: [{
              id: 71, m3u_account_id: 3, change_time: '2026-07-27T12:00:00Z',
              change_type: 'group_added', group_name: 'Retained changes row',
              stream_names: [], count: 4, enabled: true, snapshot_id: 1,
            }],
            total: 1, page: 1, page_size: 50, total_pages: 1,
          }),
        })
      })
      await page.route(/\/api\/m3u\/changes\/summary(?:\?|$)/, async (route) => {
        if (!initialChangesReleased) await initialChangesRelease
        await route.fulfill({
        status: mixedPermission ? 403 : 200,
        contentType: 'application/json',
        body: mixedPermission
          ? JSON.stringify({ detail: 'Forbidden' })
          : JSON.stringify({
              total_changes: 1, groups_added: 1, groups_removed: 0, streams_added: 0,
              streams_removed: 0, accounts_affected: [3], since: '2026-07-27T00:00:00Z',
            }),
        })
      })

      await page.getByRole('link', { name: 'M3U Changes', exact: true }).click()
      releaseInitialChanges()
      await expect(page.getByText('Retained changes row')).toBeVisible()
      failNextChangesRequest = true
      await page.getByRole('button', { name: /refresh/i }).click()
      await expect(page.getByText(/showing previously loaded changes/i)).toBeVisible()
      await expect(page.getByText('Retained changes row')).toBeVisible()
      await expect(page.getByText('1 total changes')).toBeVisible()
      await page.getByRole('button', { name: 'Retry', exact: true }).click()
      await expect(page.getByText(/showing previously loaded changes/i)).toHaveCount(0)

      mixedPermission = true
      await page.getByRole('button', { name: /refresh/i }).click()
      await expect(page.getByText(/don't have permission to view M3U changes/i)).toBeVisible()
      await expect(page.getByText('Retained changes row')).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0)
    })
  })
}

test.describe('operator shell at 200% equivalent', () => {
  test.use({ viewport: { width: 640, height: 360 }, serviceWorkers: 'block' })

  test('all primary routes settle with their exact control and remain horizontally usable', async ({ page }) => {
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    for (const consumer of routeConsumers) {
      await page.getByRole('link', { name: consumer.name }).click()
      const settled = await expectSettledRoute(page, consumer)
      await settled.scrollIntoViewIfNeeded()
      const overflow = await page.evaluate(() => ({
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

  // Section navigation is no longer gated by an allow-list: it appears wherever
  // a page has at least two sections to navigate between, and withholds itself
  // otherwise.
  test('shows section navigation on any Settings page with multiple sections', async ({ page }) => {
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'Settings', exact: true }).click()

    const sectionNav = page.getByRole('navigation', { name: 'On this page' })
    const sectionCount = async () => page.locator(
      '.settings-content-main .settings-section, .settings-content-main [data-settings-section]',
    ).count()

    // Assert the rule rather than which pages happen to satisfy it: how many
    // sections a page renders depends on its fixture data, so hardcoding a
    // negative case makes the test agree with the fixture instead of the rule.
    const pages: Array<[string, string]> = [
      ['General', 'GENERAL SETTINGS'],
      ['Normalization', 'CHANNEL NORMALIZATION'],
      ['Tags', 'TAGS'],
      ['Backup & Restore', 'BACKUP & RESTORE'],
      ['Scheduled Tasks', 'SCHEDULED TASKS'],
    ]

    await expect(sectionNav).toBeVisible()
    for (const [label, crumb] of pages) {
      await page.locator('.settings-nav-item').filter({ hasText: label }).click()
      await expect(page.locator('#main-content h1')).toHaveText(`SYSTEM / SETTINGS / ${crumb}`)
      await expectLayoutSettled(page, ['#main-content', '.settings-content-main'])
      const sections = await sectionCount()
      expect(
        await sectionNav.count() > 0,
        `${label} renders ${sections} sections, so the nav must be ${sections >= 2 ? 'present' : 'absent'}`,
      ).toBe(sections >= 2)
    }
  })

  test('audited long pages provide direct section entry and protect pending Settings edits', async ({ page }) => {
    let settingsReloadFails = false
    await seedChannelWorkspace(page, false)
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.route(/\/api\/settings(?:\?|$)/, (route) => route.fulfill({
      status: settingsReloadFails ? 503 : 200,
      contentType: 'application/json',
      body: settingsReloadFails ? JSON.stringify({ detail: 'Reload unavailable' }) : JSON.stringify({
        configured: true,
        url: 'http://dispatcharr.test',
        auth_method: 'password',
        username: 'operator',
        include_channel_number_in_name: true,
        channel_number_separator: '-',
        auto_creation_excluded_terms: ['sports'],
        default_channel_profile_ids: [],
        stream_sort_priority: [],
        stream_sort_enabled: {},
        m3u_account_priorities: {},
        custom_network_prefixes: [],
        hide_auto_sync_groups: false,
      }),
    }))
    await page.getByRole('link', { name: 'Settings', exact: true }).click()
    await page.locator('.settings-nav-item').filter({ hasText: 'Channel Pipeline' }).click()
    await page.getByRole('button', { name: 'Remove term' }).click()
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toBeVisible()
    await page.getByRole('button', { name: 'Cancel changes' }).click()
    await page.locator('.settings-nav-item').filter({ hasText: 'General' }).click()
    const sectionNav = page.getByRole('navigation', { name: 'On this page' })
    await expect(sectionNav).toBeVisible()
    await sectionNav.getByRole('button', { name: 'Stats Polling' }).click()
    await expect(page).toHaveURL(/#settings\?section=settings-general-section-stats-polling$/)
    await page.getByLabel('Poll interval (seconds)').fill('45')
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toBeVisible()
    page.once('dialog', (dialog) => dialog.dismiss())
    await page.goBack()
    await expect(page.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS / GENERAL SETTINGS')
    await expect(page).toHaveURL(/#settings\?section=settings-general-section-stats-polling$/)
    page.once('dialog', (dialog) => dialog.accept())
    await page.goBack()
    await expect(page).not.toHaveURL(/settings-general-section-stats-polling/)

    await page.locator('.settings-nav-item').filter({ hasText: 'General' }).click()
    await page.getByLabel('Poll interval (seconds)').fill('45')
    settingsReloadFails = true
    await page.getByRole('button', { name: 'Cancel changes' }).click()
    await expect(page.getByText(/Could not reload saved settings/)).toBeAttached()
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toBeVisible()
    settingsReloadFails = false
    await page.getByRole('button', { name: 'Cancel changes' }).click()
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toHaveCount(0)

    await page.locator('.timezone-select .custom-select-trigger').click()
    await page.getByRole('option', { name: 'US: Central Time (CT)' }).click()
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toBeVisible()
    await page.getByRole('button', { name: 'Cancel changes' }).click()

    // Leaving Settings needs Back first: the sidebar is drilled into the
    // Settings sections, so the primary destinations are not rendered.
    await page.getByRole('button', { name: 'Back to main navigation' }).click()
    await page.getByRole('link', { name: 'Stats', exact: true }).click()
    await page.getByRole('navigation', { name: 'On this page' })
      .getByRole('button', { name: 'Enhanced Statistics' }).click()
    await expect(page.locator('#stats-section-enhanced')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'On this page' })
      .getByRole('button', { name: 'Enhanced Statistics' })).toHaveAttribute('aria-current', 'location')
  })

  test('Settings pending actions retain edits on save failure and settle on success', async ({ page }) => {
    let saveSucceeds = false
    await seedChannelWorkspace(page, false)
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.route(/\/api\/settings(?:\?|$)/, async (route) => {
      if (route.request().method() !== 'POST') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            configured: true,
            url: 'http://dispatcharr.test',
            auth_method: 'password',
            username: 'operator',
            default_channel_profile_ids: [],
            stream_sort_priority: [],
            stream_sort_enabled: {},
            m3u_account_priorities: {},
            custom_network_prefixes: [],
            hide_auto_sync_groups: false,
          }),
        })
      }
      return route.fulfill({
        status: saveSucceeds ? 200 : 500,
        contentType: 'application/json',
        body: saveSucceeds
          ? JSON.stringify({ success: true, requires_restart: false })
          : JSON.stringify({ detail: 'Settings write unavailable' }),
      })
    })
    await page.getByRole('link', { name: 'Settings', exact: true }).click()
    await page.getByLabel('Poll interval (seconds)').fill('45')
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByText('Settings could not be saved. Your changes are still available.')).toBeAttached()
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toBeVisible()
    saveSucceeds = true
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByRole('status', { name: 'Unsaved settings' })).toHaveCount(0)
    await expect(page.getByText('Settings saved successfully')).toBeAttached()
  })

  test('Channel Manager distinguishes loading and true-empty inventory', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    await page.route(/\/api\/channels(?:\?|$)/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 400))
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
      })
    })
    await openShellWithPipelineFixture(page)
    await expect(page.getByText('Loading channels...')).toBeVisible()
    await expect(page.getByLabel('0 channels')).toBeVisible()
    await expect(page.getByLabel('0 total streams')).toBeVisible()
    await expect(page.getByText('No channels are configured.')).toBeVisible()
    await expect(page.getByText('No source streams are available.')).toBeVisible()
    await expect(page.getByRole('region', { name: 'Channels' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Streams' })).toBeVisible()
  })

  test('Channel Manager retries the exact failed stream search and recovers its matching total', async ({ page }) => {
    await seedChannelWorkspace(page, true)
    let searchAttempts = 0
    await page.route(/\/api\/streams(?:\?|$)/, (route) => {
      const url = new URL(route.request().url())
      if (url.searchParams.get('search') !== 'needle') return route.fallback()
      searchAttempts += 1
      return route.fulfill({
        status: searchAttempts === 1 ? 503 : 200,
        contentType: 'application/json',
        body: searchAttempts === 1
          ? JSON.stringify({ detail: 'Search unavailable' })
          : JSON.stringify({ count: 650, next: null, previous: null, results: [] }),
      })
    })
    await openShellWithPipelineFixture(page)
    await page.getByRole('textbox', { name: 'Search streams' }).fill('needle')
    await expect(page.getByText('Streams unavailable')).toBeVisible()
    await page.getByRole('button', { name: 'Retry loading streams' }).click()
    await expect(page.getByLabel('650 matching streams')).toBeVisible()
    expect(searchAttempts).toBe(2)
  })

  test('Channel Manager treats a lazy stream-group 403 as protected pane denial', async ({ page }) => {
    await seedChannelWorkspace(page, true)
    await page.route(/\/api\/streams(?:\?|$)/, (route) => {
      const url = new URL(route.request().url())
      if (url.searchParams.get('channel_group_name') !== 'Provider Sports') return route.fallback()
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Forbidden' }),
      })
    })
    await openShellWithPipelineFixture(page)
    await page.getByRole('button', { name: /^Other/ }).click()
    await page.getByRole('button', { name: /Provider Sports/ }).click()
    await expect(page.getByText('Streams require administrator access')).toBeVisible()
    await expect(page.locator('.channels-pane')).toHaveCount(0)
    await expect(page.locator('.streams-pane')).toHaveCount(0)
  })

  test('Channel Manager retains group A while exact-query retry recovers failed group B', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    await page.route(/\/api\/stream-groups(?:\?|$)/, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { name: 'Provider Group A', count: 1 },
        { name: 'Provider Group B', count: 1 },
      ]),
    }))
    let groupBAttempts = 0
    const seenGroupBQueries: string[] = []
    await page.route(/\/api\/streams(?:\?|$)/, (route) => {
      const url = new URL(route.request().url())
      const group = url.searchParams.get('channel_group_name')
      if (!group) return route.fallback()
      const result = {
        id: group === 'Provider Group A' ? 701 : 702,
        name: `${group} retained stream`,
        url: `https://example.invalid/${group}`,
        m3u_account: 3,
        channel_group: null,
        channel_group_name: group,
        is_custom: false,
      }
      if (group === 'Provider Group B') {
        groupBAttempts += 1
        seenGroupBQueries.push(url.search)
        if (groupBAttempts === 1) {
          return route.fulfill({
            status: 503,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Group B unavailable' }),
          })
        }
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 1, next: null, previous: null, results: [result] }),
      })
    })
    await openShellWithPipelineFixture(page, 200, [{ id: 3, name: 'Fixture Provider' }])
    await page.locator('.streams-pane').getByRole('button', { name: /All Providers/ }).click()
    await page.getByRole('checkbox', { name: 'Fixture Provider' }).check()
    await page.getByRole('button', { name: /^Other/ }).click()
    await page.getByRole('button', { name: /Provider Group A/ }).click()
    await expect(page.getByText('Provider Group A retained stream')).toBeVisible()
    await page.getByRole('button', { name: /Provider Group B/ }).click()
    await expect(page.getByText('Streams unavailable — showing previously loaded data')).toBeVisible()
    await expect(page.getByText('Provider Group A retained stream')).toBeVisible()
    await page.getByRole('button', { name: 'Retry loading streams' }).click()
    await expect(page.getByText('Provider Group B retained stream')).toBeVisible()
    expect(groupBAttempts).toBe(2)
    for (const query of seenGroupBQueries) {
      expect(query).toContain('channel_group_name=Provider+Group+B')
      expect(query).toContain('m3u_account=3')
    }
  })

  test('Channel Manager gives a named scoped error and recovers through Retry', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    let attempts = 0
    let channelsHealthy = false
    let releaseRecovery!: () => void
    const recoveryGate = new Promise<void>((resolve) => { releaseRecovery = resolve })
    await page.route(/\/api\/channels(?:\?|$)/, async (route) => {
      const isWorkspaceList = new URL(route.request().url()).searchParams.get('page_size') === '500'
      if (!isWorkspaceList) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
        })
      }
      attempts += 1
      if (channelsHealthy) await recoveryGate
      return route.fulfill({
        status: channelsHealthy ? 200 : 503,
        contentType: 'application/json',
        body: channelsHealthy
          ? JSON.stringify({ count: 0, next: null, previous: null, results: [] })
          : JSON.stringify({ detail: 'Channels source unavailable' }),
      })
    })
    await openShellWithPipelineFixture(page)
    await expect(page.getByText('Channels unavailable')).toBeVisible()
    channelsHealthy = true
    await page.getByRole('button', { name: 'Retry loading channels' }).click()
    await expect(page.getByText('Loading channels...')).toBeVisible()
    releaseRecovery()
    await expect(page.getByLabel('0 channels')).toBeVisible()
    expect(attempts).toBeGreaterThanOrEqual(2)
  })

  test('Channel Manager permission state exposes no protected panes or actions', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    await page.route(/\/api\/channels(?:\?|$)/, (route) => route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Forbidden' }),
    }))
    await openShellWithPipelineFixture(page)
    await expect(page.getByText('Channels require administrator access')).toBeVisible()
    await expect(page.getByText('Streams require administrator access')).toBeVisible()
    await expect(page.locator('.channels-pane')).toHaveCount(0)
    await expect(page.locator('.streams-pane')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Edit Mode' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Retry loading/i })).toHaveCount(0)
  })

  test('Channel Pipeline exposes deterministic named recovery when rule loading fails', async ({ page }) => {
    await openShellWithPipelineFixture(page, 503)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'Channel Pipeline' }).click()
    await expect(page.getByText('Failed to load channel pipeline rules', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create rule' })).toHaveCount(0)
  })

  test('protected M3U source data explains permission denial without exposing data or actions', async ({ page }) => {
    await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
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
      }),
    }))
    await page.route(/\/api\/m3u\/server-groups(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }))
    await page.route(/\/api\/providers(?:\/|\?|$)/, (route) => route.fulfill({
      status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Administrator access required' }),
    }))
    await openDeterministicOperatorShell(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'M3U Manager' }).click()
    await expect(page.locator('.route-page-header').getByText('Provider accounts require administrator access', { exact: true })).toBeVisible()
    await expect(page.getByText(/0 provider accounts/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Add M3U Account' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Retry loading provider accounts/i })).toHaveCount(0)
  })

  test('M3U source loading announces failure and recovers through its scoped Retry', async ({ page }) => {
    let providerMode: 'healthy' | 'error' = 'healthy'
    await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
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
      }),
    }))
    await page.route(/\/api\/m3u\/server-groups(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }))
    await page.route(/\/api\/providers(?:\/|\?|$)/, (route) => route.fulfill({
      status: providerMode === 'error' ? 503 : 200,
      contentType: 'application/json',
      body: providerMode === 'error' ? JSON.stringify({ detail: 'Temporarily unavailable' }) : '[]',
    }))

    await openDeterministicOperatorShell(page)
    await dismissFirstRunPromptIfPresent(page)
    await expect(page.locator('#main-content .tab-loading')).toHaveCount(0)

    providerMode = 'error'
    await page.getByRole('link', { name: 'M3U Manager' }).click()
    await expect(page.getByRole('status', { name: 'Provider accounts unavailable' })).toBeVisible()
    const retry = page.getByRole('button', { name: 'Retry loading provider accounts' })
    await expect(retry).toBeVisible()

    providerMode = 'healthy'
    await retry.click()
    // A recovered M3U Manager says nothing in its status slot: the route
    // header used to confirm recovery with "0 provider accounts", and that
    // count was removed in bead enhancedchannelmanager-tygwm. Recovery is now
    // the failure status and its Retry clearing, and the header returning to
    // its healthy primary action.
    await expect(page.getByRole('status', { name: 'Provider accounts unavailable' })).toHaveCount(0)
    await expect(retry).toHaveCount(0)
    await expect(page.locator('.route-page-header').getByText(/\d+ provider accounts?/)).toHaveCount(0)
    await expect(page.locator('.route-page-header').getByRole('button', { name: 'Add M3U Account' })).toBeVisible()
  })

  test('a later Logo permission denial removes cached protected content and actions', async ({ page }) => {
    let logoPermissionDenied = false
    await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
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
      }),
    }))
    await page.route(/\/api\/channels\/logos(?:\/|\?|$)/, (route) => route.fulfill({
      status: logoPermissionDenied ? 403 : 200,
      contentType: 'application/json',
      body: logoPermissionDenied
        ? JSON.stringify({ detail: 'Administrator access required' })
        : JSON.stringify({
            count: 1,
            next: null,
            previous: null,
            results: [{
              id: 41,
              name: 'Private Sports Logo',
              url: 'https://example.test/private-sports.png',
              cache_url: '',
              channel_count: 1,
              is_used: true,
            }],
          }),
    }))

    await openDeterministicOperatorShell(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'Logo Manager' }).click()
    await expect(page.getByText('Private Sports Logo', { exact: true })).toBeVisible()

    logoPermissionDenied = true
    await page.getByPlaceholder('Search logos...').fill('private')

    await expect(page.getByRole('status', { name: 'Logos access denied' })).toBeVisible()
    await expect(page.getByText('Private Sports Logo', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Add Logo' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Retry loading logos/i })).toHaveCount(0)
  })

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
    await openDeterministicOperatorShell(page)
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
    await expect(page.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS / CHANNEL DEFAULTS')
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
    // Third entry is the trailing breadcrumb crumb where it differs from the
    // destination name: Settings appends its active section.
    const expected: Array<[string, string, string?]> = [
      ['Dashboard', '#dashboard'], ['Channel Manager', '#channel-manager'], ['Guide', '#guide'],
      ['M3U Manager', '#m3u-manager'], ['EPG Manager', '#epg-manager'], ['Logo Manager', '#logo-manager'],
      ['Channel Pipeline', '#channel-pipeline'], ['M3U Changes', '#m3u-changes'], ['Stats', '#stats'],
      ['Journal', '#journal'], ['Settings', '#settings', 'GENERAL SETTINGS'],
    ]
    for (const [name, hash, crumb] of expected) {
      const link = appPage.getByRole('link', { name })
      await expect(link).toHaveAttribute('href', hash)
      await link.focus()
      await expect(link).toBeFocused()
      await appPage.keyboard.press('Enter')
      await expect(appPage).toHaveURL(new RegExp(`${hash.replace('#', '#')}$`))
      await expect(appPage.locator('#main-content h1')).toBeFocused()
      await expect(appPage.locator('#main-content h1')).toHaveText(new RegExp(` / ${crumb ?? name.toUpperCase()}$`))
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
    await expect(appPage.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS / CHANNEL DEFAULTS')
  })

  test('contextual settings navigation cleanly exits an edit session with no staged changes', async ({ page }) => {
    await seedChannelWorkspace(page, true)
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('button', { name: 'Edit Mode' }).click()
    await expect(page.getByRole('button', { name: 'Done' })).toBeVisible()
    await page.getByRole('link', { name: 'Channel default settings' }).click()
    await expect(page).toHaveURL(/#settings\/channel-defaults$/)
    await expect(page.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS / CHANNEL DEFAULTS')
    await expect(page.getByRole('button', { name: 'Done' })).toHaveCount(0)
    // Contextual links land in Settings with the sidebar drilled in; Back
    // restores the primary destinations without leaving the Settings route.
    await page.getByRole('button', { name: 'Back to main navigation' }).click()
    await page.getByRole('link', { name: 'Channel Manager' }).click()
    await expect(page.getByRole('button', { name: 'Edit Mode' })).toBeVisible()
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
