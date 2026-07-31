/**
 * Cross-route shared-class typography leak guard.
 *
 * THE DEFECT (repaired four times, recurred four times: `.list-header`
 * (bead qlc4h), `.status-label` (f4yc7), `.group-count` (sccol), and
 * `.action-btn .material-icons`):
 *
 *   A page CSS file redeclares a class that `shared/common.css` already owns,
 *   bare (no ancestor scoping) and at equal specificity. Every route tab is a
 *   lazily-imported chunk whose stylesheet is appended to <head> on FIRST
 *   visit and never removed. `common.css` is emitted last inside the eager
 *   bundle, so it beats every eager file but loses permanently to any bare
 *   rule in any tab chunk the user has already visited. The winner therefore
 *   depends on the visit history — "visit Logo Manager, then M3U Manager, then
 *   go back to Logo Manager and its row icons have changed size".
 *
 * WHY `css-smoke.spec.ts` CANNOT CATCH THIS: every test there takes a fresh
 * `appPage` fixture (fixtures/base.ts -> `page.goto('/')` on a brand-new
 * Playwright context) and visits exactly ONE route, so each route is always
 * measured with only its own chunk loaded. That suite stayed green through all
 * four historical instances.
 *
 * WHAT THIS SPEC DOES: three measurement passes over all ten routes, in a
 * single uninterrupted browser context per order, and asserts that the
 * computed typography of the shared classes is identical in all three.
 *
 *   pass A1  order A (declared), first visit  -- chunks 1..n loaded so far
 *   pass A2  order A, revisit, same context   -- ALL ten chunks loaded
 *   pass B1  order B (reversed), first visit  -- the complementary chunk sets
 *
 * A1 vs A2 is the user's own "go back to the page I was on" symptom. A1 vs B1
 * covers the case where the leak only shows for a particular pair ordering.
 * Two separate contexts are required: running order B inside order A's context
 * would measure a fully-loaded <head> and could never disagree.
 *
 * Screenshots for every route in every pass land in
 * `test-results/cross-route-css/` for the next engineer to eyeball. They are
 * artifacts, NOT assertions -- a pixel diff over ten data-driven pages is
 * noisy and would not say WHICH property broke. The computed-style comparison
 * names the route, the selector and the property.
 *
 * WHEN THIS FAILS: the named selector is declared bare in more than one bundle
 * chunk. Delete the page copy and let `shared/common.css` own it (leave the
 * pointer comment behind), or scope the page copy to a page-root ancestor.
 * `frontend/src/cssAudits/sharedClassChunkLeak.audit.test.ts` is the
 * authoring-time twin of this check and will point at the exact file:line.
 * See docs/css_guidelines.md § Load order.
 */
import { test, expect, isLoginPage, performLogin } from './fixtures/base'
import type { Browser, Page } from '@playwright/test'

/** The ten route pages, in the order the primary rail lists them. */
const ROUTES: ReadonlyArray<{ id: string; root: string }> = [
  { id: 'm3u-manager', root: '.m3u-manager-tab' },
  { id: 'epg-manager', root: '.epg-manager-tab' },
  { id: 'logo-manager', root: '.logo-manager-tab' },
  { id: 'channel-manager', root: '.channels-pane' },
  { id: 'channel-pipeline', root: '.channel-pipeline-tab' },
  { id: 'guide', root: '.guide-tab' },
  { id: 'stats', root: '.stats-tab' },
  { id: 'm3u-changes', root: '.m3u-changes-tab' },
  { id: 'journal', root: '.journal-tab' },
  { id: 'settings', root: '.settings-tab' },
]

/**
 * Shared classes owned by `shared/common.css` (or otherwise rendered on more
 * than one route) whose computed typography must not depend on visit history.
 * The first four entries are the four historical instances.
 */
const SHARED_SELECTORS: readonly string[] = [
  '.action-btn .material-icons',
  '.list-header',
  '.status-label',
  '.group-count',
  '.btn-primary',
  '.btn-primary .material-icons',
  '.btn-secondary',
  '.btn-secondary .material-icons',
  '.btn-small',
  '.btn-icon .material-icons',
  '.separator-btn',
  '.micro-label',
  '.badge',
  '.type-badge',
  '.group-name',
  '.file-name',
  '.file-size',
  '.form-group label',
  '.form-input',
  '.search-input',
  '.search-box input',
  '.search-box .material-icons',
  '.filter-dropdown-button',
  '.filter-select .custom-select-trigger',
  '.empty-state h3',
  '.empty-state p',
  '.error-message',
  '.error-banner',
  '.warning-message',
  '.success-message',
  '.test-result',
  '.field-hint',
  '.field-error',
  '.form-hint',
  '.loading',
  '.pane-header h2',
  '.header-stat',
  '.stat-value',
  '.stat-label',
  '.page-info',
  '.entries-count',
  '.page-size-label',
  '.section-title',
  '.detail-section h4',
  '.modal-header h3',
]

const TYPOGRAPHY = ['fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'textTransform'] as const

/** route id -> selector -> "16px | 500 | 24px | normal | uppercase" */
type Measurement = Record<string, Record<string, string>>

async function measure(page: Page, selectors: readonly string[]): Promise<Record<string, string>> {
  return page.evaluate(
    ({ sels, props }) => {
      const out: Record<string, string> = {}
      for (const sel of sels) {
        let el: Element | undefined
        try {
          el = Array.from(document.querySelectorAll(sel)).find((candidate) => {
            const r = candidate.getBoundingClientRect()
            return r.width > 0 && r.height > 0
          })
        } catch {
          continue // malformed selector in the probe list -- skip it, don't abort the walk
        }
        if (!el) continue
        // One representative is enough: the leak is a cascade-level change, so
        // when it fires it moves every element matching that bare selector on
        // the page, not an arbitrary subset.
        const cs = getComputedStyle(el)
        out[sel] = props.map((p) => cs[p as keyof CSSStyleDeclaration] as string).join(' | ')
      }
      return out
    },
    { sels: [...selectors], props: [...TYPOGRAPHY] }
  )
}

/**
 * Navigate by mutating the URL hash rather than by clicking the rail.
 *
 * Two reasons, both load-bearing:
 *  1. Settings replaces the primary rail with its own secondary rail, so
 *     `navigateToTab()` (which clicks `[data-tab=...]`) cannot leave Settings.
 *  2. `navigateToTab()`'s recovery path calls `page.reload()`, which would
 *     throw away every lazily-appended tab stylesheet -- i.e. it would reset
 *     the exact state this spec exists to observe, and silently turn a real
 *     failure into a pass. Setting `location.hash` never reloads.
 */
async function goToRoute(page: Page, route: { id: string; root: string }): Promise<void> {
  await page.evaluate((id) => {
    window.location.hash = `#${id}`
  }, route.id)
  await page.waitForSelector('.tab-loading', { state: 'hidden', timeout: 60000 }).catch(() => undefined)
  await page.waitForSelector(route.root, { timeout: 60000 })
}

async function walk(page: Page, order: readonly { id: string; root: string }[], pass: string): Promise<Measurement> {
  const result: Measurement = {}
  for (const route of order) {
    await goToRoute(page, route)
    // Let async page data settle so we measure a populated page, not a skeleton.
    await page.waitForLoadState('networkidle').catch(() => undefined)
    await page.waitForTimeout(500)
    result[route.id] = await measure(page, SHARED_SELECTORS)
    await page
      .screenshot({ path: `test-results/cross-route-css/${pass}-${route.id}.png`, fullPage: true })
      .catch(() => undefined)
  }
  return result
}

async function openApp(browser: Browser): Promise<Page> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  if (await isLoginPage(page)) await performLogin(page)
  await page.waitForSelector('.tab-navigation', { timeout: 30000 })
  return page
}

test.describe('shared-class typography does not depend on route visit order', () => {
  test('all ten routes render identical shared typography in every visit order', async ({ browser }) => {
    // Three full walks of ten lazily-loaded routes, with screenshots.
    test.setTimeout(10 * 60 * 1000)

    const reversed = [...ROUTES].reverse()

    // Order A: one context, ten routes, then the same ten again with every
    // chunk now installed in <head>.
    const pageA = await openApp(browser)
    const a1 = await walk(pageA, ROUTES, 'A1-first-visit')
    const a2 = await walk(pageA, ROUTES, 'A2-revisit-all-chunks-loaded')
    await pageA.context().close()

    // Order B: a brand-new context so the chunk-append order genuinely differs.
    const pageB = await openApp(browser)
    const b1 = await walk(pageB, reversed, 'B1-reverse-order')
    await pageB.context().close()

    const passes: Array<[string, Measurement]> = [
      ['A1 first visit', a1],
      ['A2 revisit (all chunks loaded)', a2],
      ['B1 reverse order', b1],
    ]

    const drift: string[] = []
    for (const route of ROUTES) {
      for (const sel of SHARED_SELECTORS) {
        const seen = passes
          .map(([label, m]) => [label, m[route.id]?.[sel]] as const)
          .filter((entry): entry is readonly [string, string] => entry[1] !== undefined)
        // Only compare where the element rendered in every pass -- a selector
        // that is absent in one pass is a data/state difference, not a leak.
        if (seen.length !== passes.length) continue
        const distinct = new Set(seen.map(([, v]) => v))
        if (distinct.size > 1) {
          drift.push(
            `  ${route.id}  ${sel}\n` +
              `      (font-size | font-weight | line-height | letter-spacing | text-transform)\n` +
              seen.map(([label, v]) => `      ${label.padEnd(32)} ${v}`).join('\n')
          )
        }
      }
    }

    expect(
      drift,
      drift.length === 0
        ? ''
        : `${drift.length} shared class(es) render different typography depending on which routes ` +
            `were visited first. Each one is declared bare in more than one bundle chunk -- run ` +
            `\`npx vitest run src/cssAudits/sharedClassChunkLeak.audit.test.ts\` in frontend/ for the ` +
            `exact file:line, then delete the page copy or scope it to a page-root ancestor. ` +
            `Screenshots: test-results/cross-route-css/.\n${drift.join('\n')}`
    ).toEqual([])
  })
})
