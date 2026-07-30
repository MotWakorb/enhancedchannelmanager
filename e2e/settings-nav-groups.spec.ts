/**
 * The Settings drill-in's grouping, rendered.
 *
 * WHAT THIS PINS. The approved Settings information architecture (bead
 * `enhancedchannelmanager-70u0r.3`, PO decision D1): six sidebar groups in a
 * fixed order, each holding a fixed set of destinations, rendered through the
 * same `.navigation-group > h2` markup the primary nav uses. `routeHierarchy`
 * and `TabNavigation` unit tests already pin the data and the DOM; what they
 * cannot see is whether six headings plus eighteen links still FIT.
 *
 * WHY IT IS A MEASUREMENT AND NOT JUST AN ASSERTION. Grouping took the drill-in
 * from two headings to six, adding roughly 120px of vertical extent to a 244px
 * rail. `1280x720` is the minimum supported viewport, and `.primary-navigation`
 * is a scroll container (`overflow-y: auto`) whose FIRST CHILD is the Back
 * control — so an overflowing rail does not clip, it scrolls Back out of reach,
 * and Back is the only way out of Settings. That failure is invisible to any
 * test that only counts headings. This guard therefore records
 * `scrollHeight` vs `clientHeight` at both viewports and asserts the overflow
 * CONTRACT rather than assuming there is no overflow.
 *
 * SCOPED LOCATORS, DELIBERATELY. `e2e/operator-shell.spec.ts:1810` asserts
 * `.navigation-group h2` has count 5 — true only with the drill-in CLOSED,
 * where just the primary nav's headings exist. Every locator here is scoped to
 * `nav[aria-label="Settings sections"]` so the two guards cannot interfere.
 *
 * THREE THEMES, because `index.css` re-declares spacing and font tokens under
 * `[data-theme="light"]` and `[data-theme="high-contrast"]`; a theme block that
 * grows the heading or the row height moves the rail in that theme only.
 *
 * WHEN THIS FAILS: either a destination moved group (update the table below in
 * the same commit, citing the bead that authorised it — this is the record of an
 * approved IA, not an incidental snapshot), or the rail's vertical budget
 * changed. Do not relax the overflow contract to make it green.
 */
import { test, expect } from './fixtures/base'
import {
  captureStorageState,
  goToRoute,
  openApp,
  setTheme,
  THEMES,
  type RouteSpec,
  type StorageState,
  type Theme,
} from './fixtures/css-guard'
import type { Page } from '@playwright/test'

const SETTINGS: RouteSpec = { id: 'settings', root: '.settings-tab' }

/**
 * `1280x720` is the minimum supported viewport and the reason this file exists;
 * `1920x1080` is the spacious case, where the rail has headroom to spare and a
 * regression would otherwise hide.
 */
const VIEWPORTS: ReadonlyArray<{ width: number; height: number }> = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
]

/**
 * The approved assignment, longhand. PO decision D1 including the amendment
 * that moved Scheduled Tasks into Upkeep — it is EPG refresh, M3U refresh and
 * database cleanup, which is upkeep rather than reporting.
 *
 * Lookup Tables is in Channel Processing provisionally; bead
 * `enhancedchannelmanager-70u0r.1` retires the destination and this group drops
 * back to four.
 *
 * The e2e user is an administrator, so Administration renders. The non-admin
 * case (five groups, no empty Administration heading) is covered by
 * `TabNavigation.test.tsx`, which can vary the flag directly.
 */
const APPROVED_GROUPS: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['Connections', ['General', 'Integrations']],
  ['Channel Processing', ['Channel Defaults', 'Channel Normalization', 'Tags', 'Lookup Tables', 'Channel Pipeline']],
  ['Notifications & Reports', ['Notification Settings', 'M3U Digest']],
  ['Upkeep', ['Scheduled Tasks', 'Maintenance', 'Backup & Restore']],
  ['Workspace', ['Appearance', 'Linked Accounts']],
  ['Administration', ['Authentication', 'User Management', 'TLS Certificates', 'MCP Integration']],
]

interface DrillInMetrics {
  sidebarWidth: number
  /** The nav's own scroll box — the rail's real vertical budget. */
  navClientHeight: number
  navScrollHeight: number
  navOverflowY: string
  /** True when the group list is taller than the space the rail gives it. */
  navOverflows: boolean
  /** How much taller, in px. Zero when it fits. */
  navOverflowBy: number
  /** Whether Back is still inside the rail's visible box at the current scroll. */
  backWithinNavBox: boolean
  backPosition: string
  /** A pinned Back must be opaque, or rows scroll through it rather than behind. */
  backOpaque: boolean
  backHeight: number
  groups: Array<[string, string[]]>
  hrefs: string[]
  currentCount: number
  backName: string | null
  noSidebarXOverflow: boolean
  noDocumentXOverflow: boolean
}

async function measureDrillIn(page: Page): Promise<DrillInMetrics> {
  return page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!
    const nav = document.querySelector<HTMLElement>('nav[aria-label="Settings sections"]')!
    const back = nav.querySelector<HTMLElement>('.navigation-back')!
    const navRect = nav.getBoundingClientRect()
    const backRect = back.getBoundingClientRect()
    const navStyle = getComputedStyle(nav)
    const links = [...nav.querySelectorAll<HTMLAnchorElement>('.navigation-destination')]
    return {
      sidebarWidth: Number(sidebar.getBoundingClientRect().width.toFixed(2)),
      navClientHeight: nav.clientHeight,
      navScrollHeight: nav.scrollHeight,
      navOverflowY: navStyle.overflowY,
      navOverflows: nav.scrollHeight > nav.clientHeight,
      navOverflowBy: Math.max(0, nav.scrollHeight - nav.clientHeight),
      backWithinNavBox: backRect.top >= navRect.top - 0.5 && backRect.bottom <= navRect.bottom + 0.5,
      backPosition: getComputedStyle(back).position,
      // `transparent` and `rgba(…, 0)` both fail; any resolved colour with a
      // non-zero alpha passes. Read from the element, so a theme that drops the
      // background in one theme only is caught.
      backOpaque: !/^(transparent$|rgba\(.*,\s*0(\.0+)?\)$)/.test(getComputedStyle(back).backgroundColor),
      backHeight: Number(backRect.height.toFixed(2)),
      groups: [...nav.querySelectorAll<HTMLElement>('.navigation-group')].map((group) => [
        group.querySelector('h2')!.textContent ?? '',
        [...group.querySelectorAll<HTMLAnchorElement>('.navigation-destination')].map(
          (link) => link.getAttribute('aria-label') ?? ''
        ),
      ] as [string, string[]]),
      hrefs: links.map((link) => link.getAttribute('href') ?? ''),
      currentCount: links.filter((link) => link.getAttribute('aria-current') === 'page').length,
      backName: back.getAttribute('aria-label'),
      noSidebarXOverflow: sidebar.scrollWidth <= sidebar.clientWidth,
      noDocumentXOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    }
  })
}

/** Group headings must occupy no layout box at all on the 68px rail. */
async function measureCollapsedHeadings(page: Page): Promise<{ count: number; allZeroBox: boolean; sidebarWidth: number }> {
  return page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!
    const headings = [...sidebar.querySelectorAll<HTMLElement>('nav[aria-label="Settings sections"] .navigation-group h2')]
    return {
      count: headings.length,
      allZeroBox: headings.every((heading) => {
        const style = getComputedStyle(heading)
        const rect = heading.getBoundingClientRect()
        return style.display === 'none' && rect.width === 0 && rect.height === 0
      }),
      sidebarWidth: Number(sidebar.getBoundingClientRect().width.toFixed(2)),
    }
  })
}

test.describe('the Settings drill-in renders the approved groups and fits the rail', () => {
  // One login for the whole file — the login endpoint is rate-limited at
  // 5/minute and this matrix opens six contexts.
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(4 * 60 * 1000)
    storageState = await captureStorageState(browser)
  })

  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${theme} @ ${viewport.width}x${viewport.height}: approved groups, no overflow trap`, async ({
        browser,
      }, testInfo) => {
        test.setTimeout(3 * 60 * 1000)

        const page = await openApp(browser, storageState, viewport)
        try {
          await goToRoute(page, SETTINGS)
          // Re-assert after navigating: the app re-applies its stored theme
          // when settings load, so a theme set before the route would be
          // silently reverted and the rail measured in the wrong one.
          await setTheme(page, theme as Theme)

          const expanded = await measureDrillIn(page)
          testInfo.annotations.push({
            type: 'drill-in rail',
            description:
              `${theme} @ ${viewport.width}x${viewport.height} expanded: ` +
              `width ${expanded.sidebarWidth}px, nav scrollHeight ${expanded.navScrollHeight}px ` +
              `vs clientHeight ${expanded.navClientHeight}px ` +
              `(${expanded.navOverflows ? `OVERFLOWS by ${expanded.navOverflowBy}px` : 'fits'})`,
          })

          // The approved IA, rendered. Compared as one value so a missing group
          // and a reordered group produce the same readable diff.
          expect(expanded.groups).toEqual(APPROVED_GROUPS.map(([label, labels]) => [label, [...labels]]))

          // IA commitments that grouping must not disturb.
          expect(expanded.backName, 'Back control accessible name').toBe('Back to main navigation')
          expect(
            expanded.hrefs.every((href) => /^#settings\/[a-z0-9-]+$/.test(href)),
            `section links must be real #settings/<page> anchors: ${expanded.hrefs.join(', ')}`
          ).toBe(true)
          expect(expanded.currentCount, 'exactly one section carries aria-current="page"').toBe(1)
          expect(expanded.sidebarWidth, 'expanded rail width').toBe(244)
          expect(expanded.noSidebarXOverflow, 'rail must not scroll horizontally').toBe(true)
          expect(expanded.noDocumentXOverflow, 'document must not scroll horizontally').toBe(true)

          // THE GATE. If the grouped list is taller than the rail's budget, the
          // rail must still be usable: a scroll container, with Back reachable.
          // Asserted as a contract rather than as "it fits", because whether it
          // fits depends on the viewport and on how many groups there are.
          expect(expanded.backWithinNavBox, 'Back must be in view at rest').toBe(true)
          if (expanded.navOverflows) {
            expect(
              expanded.navOverflowY,
              `The grouped drill-in is ${expanded.navOverflowBy}px taller than the rail at ` +
                `${viewport.width}x${viewport.height}, so the rail MUST scroll. A clipped rail ` +
                'makes the last destinations unreachable.'
            ).toMatch(/^(auto|scroll)$/)

            // Back is the first child of the scroll container and the only exit
            // from Settings, so it is pinned (`position: sticky`). Scroll to the
            // bottom and prove it did NOT go with the content — this is the one
            // assertion that would have caught the unpinned original.
            await page.evaluate(() => {
              document.querySelector<HTMLElement>('nav[aria-label="Settings sections"]')!.scrollTop = 99999
            })
            const scrolled = await measureDrillIn(page)
            expect(scrolled.backPosition, 'Back must be sticky, not static').toBe('sticky')
            expect(
              scrolled.backWithinNavBox,
              `Back scrolled out of the rail at ${viewport.width}x${viewport.height}. It is the only ` +
                'exit from Settings; it must stay pinned while the destinations scroll under it.'
            ).toBe(true)
            expect(
              scrolled.backOpaque,
              'A pinned Back needs an opaque background, or destination rows show through it while scrolling.'
            ).toBe(true)
            // Still the real control, not just a visible box.
            const back = page.getByRole('button', { name: 'Back to main navigation' })
            await expect(back).toBeVisible()
            await expect(back).toBeEnabled()
            // And the last destination is reachable by scrolling, so nothing is
            // lost to the overflow.
            await expect(page.locator('nav[aria-label="Settings sections"] .navigation-destination').last()).toBeVisible()
            await page.evaluate(() => {
              document.querySelector<HTMLElement>('nav[aria-label="Settings sections"]')!.scrollTop = 0
            })
          }

          // Collapsed: the six headings must cost nothing, exactly as the
          // primary nav's five already do.
          await page.getByRole('button', { name: 'Collapse navigation' }).click()
          await expect
            .poll(() => measureCollapsedHeadings(page), { timeout: 5000 })
            .toMatchObject({ count: APPROVED_GROUPS.length, allZeroBox: true, sidebarWidth: 68 })

          const collapsed = await measureDrillIn(page)
          testInfo.annotations.push({
            type: 'drill-in rail',
            description:
              `${theme} @ ${viewport.width}x${viewport.height} collapsed: ` +
              `width ${collapsed.sidebarWidth}px, nav scrollHeight ${collapsed.navScrollHeight}px ` +
              `vs clientHeight ${collapsed.navClientHeight}px ` +
              `(${collapsed.navOverflows ? `OVERFLOWS by ${collapsed.navOverflowBy}px` : 'fits'})`,
          })
          expect(collapsed.groups.map(([, labels]) => labels.length)).toEqual(
            APPROVED_GROUPS.map(([, labels]) => labels.length)
          )
          expect(collapsed.noSidebarXOverflow, 'collapsed rail must not scroll horizontally').toBe(true)
          expect(collapsed.noDocumentXOverflow, 'document must not scroll horizontally when collapsed').toBe(true)

          await page.getByRole('button', { name: 'Expand navigation' }).click()
          await expect.poll(() => measureDrillIn(page).then((m) => m.sidebarWidth), { timeout: 5000 }).toBe(244)
        } finally {
          await page.context().close()
        }
      })
    }
  }
})
