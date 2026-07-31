/**
 * Screen-reader-only utility stays visually hidden on a COLD context.
 *
 * THE DEFECT (bead enhancedchannelmanager-zncyv, fixed by d4641593):
 *
 *   `.sr-only` was declared in exactly ONE file, `ConditionEditor.css`, which
 *   ships inside the lazily-loaded ChannelPipelineTab chunk. Two `role="status"
 *   aria-live="polite"` regions render that class from elsewhere:
 *   `OperatorDashboard.tsx` (EAGER bundle) and `SettingsTab.tsx` (Settings
 *   chunk). Neither chunk carried the rule, so until the operator happened to
 *   open Channel Pipeline those two regions had NO hiding styles at all —
 *   `position: static; clip: auto`, measured 1308x24 on the Dashboard — and
 *   their announcement text rendered as ordinary visible copy in the middle of
 *   the page. Once Channel Pipeline was opened its stylesheet was appended to
 *   <head> and never removed, so the same regions silently became correct for
 *   the rest of the session.
 *
 * WHY THE CONTEXT MUST BE COLD. Visiting Channel Pipeline first makes the bug
 * disappear. Any spec that reaches these regions after a pipeline visit — or
 * that reuses a context which has ever been there — is decoration: it passes
 * with the defect present. This spec therefore opens its OWN context, goes
 * straight to Dashboard and Settings, and asserts before anything else can
 * append a stylesheet. `assertPipelineNeverVisited()` re-checks that invariant
 * at the end of every test so a future edit that inserts a pipeline visit
 * turns the spec RED instead of silently defanging it.
 *
 * WHY NO EXISTING GUARD CAUGHT IT:
 *   - The three tiers in `frontend/src/cssAudits/sharedClassChunkLeak.audit
 *     .test.ts` (16 tests) all model DUPLICATE declarations colliding across
 *     chunks. `.sr-only` had exactly one declaration, so nothing ever
 *     collided; every tier was green for the entire life of the bug. Reverting
 *     d4641593 leaves all 16 green.
 *   - `e2e/cross-route-css-leak.spec.ts` compares typography only, does not
 *     list `.sr-only` among its selectors, and does not walk `dashboard`.
 *   The blind spot — declaration site and render site in different chunks — is
 *   bead enhancedchannelmanager-nraba.
 *
 * HOW IT MEASURES. Two layers, because each covers the other's gap:
 *
 *   1. COMPUTED STYLE AT REST. `position` / `clip` / `overflow` / the declared
 *      `width` and `height` are observable whether or not the region currently
 *      holds an announcement. This is strictly better than measuring geometry
 *      alone: the live regions are empty until something announces, so an
 *      unstyled empty region is a 1308x0 box that a naive "is it small?" check
 *      would wave through. The defect state resolves to
 *      `position: static; clip: auto; overflow: visible` — unmistakable.
 *   2. GEOMETRY + HIT TEST WITH AN ANNOUNCEMENT INJECTED. Empty regions have no
 *      user-visible geometry, so a realistic announcement string is written
 *      into any empty region, measured, and restored — all inside ONE
 *      `page.evaluate` call, so React cannot interleave and the app never
 *      observes the mutation. This reproduces the exact user-facing symptom:
 *      a 1308x24 box that `document.elementFromPoint` returns.
 *
 * NOT VACUOUS: every route asserts a minimum element count. Dashboard and
 * Settings each render at least one such region, so a run where the region
 * failed to mount fails loudly instead of passing with an empty list.
 *
 * OUT OF SCOPE: the three `.sr-only` <label>s inside ChannelPipelineTab's rule
 * editor (ConditionEditor.tsx:599, ActionEditor.tsx:491/683). Reaching them
 * needs a pipeline rule open in the editor, which this instance has no data
 * for AND which would require the pipeline visit this spec exists to avoid.
 * They were never at risk: they render inside the chunk that owned the rule.
 *
 * WHEN THIS FAILS: a visually-hidden utility is being declared somewhere that
 * does not load on every route that renders it. The rule belongs in
 * `frontend/src/shared/common.css` § 2 COMMON UTILITIES (eager), never in a
 * lazily-loaded tab chunk. See docs/css_guidelines.md § Other.
 */
import { test, expect } from './fixtures/base'
import { captureStorageState, goToRoute, openApp, type StorageState } from './fixtures/css-guard'
import type { Page } from '@playwright/test'

/** Both spellings of the one utility — `.sr-only` is an alias of `.visually-hidden`. */
const SR_ONLY_SELECTOR = '.sr-only, .visually-hidden'

/**
 * Routes that render the utility from a chunk that does NOT declare it. Both
 * are reachable without ever touching Channel Pipeline, which is the whole
 * point. `minElements` is the anti-vacuity floor, not a target.
 */
const COLD_ROUTES: ReadonlyArray<{ id: string; root: string; minElements: number }> = [
  // OperatorDashboard.tsx:179 — eager bundle. Measured at 1308x24 with the defect.
  { id: 'dashboard', root: '.operator-dashboard', minElements: 1 },
  // SettingsTab.tsx:5876 — Settings chunk. Measured at 800px wide with the defect.
  { id: 'settings', root: '.settings-tab', minElements: 1 },
]

/** One measured element. Every field is asserted; `text` and `cls` are for the diff. */
interface Probe {
  cls: string
  text: string
  /** True when the probe wrote an announcement in to make the box measurable. */
  announcementInjected: boolean
  position: string
  clip: string
  overflow: string
  /** Declared box, resolved. `1px` when hidden; the content width when not. */
  cssWidth: string
  cssHeight: string
  /** Rendered box with an announcement present, rounded up. */
  renderedWidth: number
  renderedHeight: number
  /** True when elementFromPoint at the box centre returns this element or a child. */
  occupiesSpace: boolean
}

/** The shape every hidden element must have. Anything else is a rendering defect. */
const HIDDEN: Omit<Probe, 'cls' | 'text' | 'announcementInjected'> = {
  position: 'absolute',
  clip: 'rect(0px, 0px, 0px, 0px)',
  overflow: 'hidden',
  cssWidth: '1px',
  cssHeight: '1px',
  renderedWidth: 1,
  renderedHeight: 1,
  occupiesSpace: false,
}

async function probeHiddenElements(page: Page, selector: string): Promise<Probe[]> {
  return page.evaluate((sel) => {
    const out: Probe[] = []
    for (const el of Array.from(document.querySelectorAll(sel))) {
      const hadText = el.textContent
      const isEmpty = !hadText || !hadText.trim()
      // A realistic announcement, written and read back inside this single
      // synchronous block. React cannot re-render mid-evaluate, so the app
      // never sees the mutation and nothing is left behind.
      if (isEmpty) el.textContent = 'Saved. 12 channels updated.'
      const rect = el.getBoundingClientRect()
      const style = getComputedStyle(el)
      let occupiesSpace = false
      if (rect.width > 0 && rect.height > 0) {
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
        occupiesSpace = hit === el || el.contains(hit)
      }
      out.push({
        cls: (el.className || '').toString(),
        text: (el.textContent || '').trim().slice(0, 48),
        announcementInjected: isEmpty,
        position: style.position,
        // Chromium reports the legacy `clip` here; `clipPath` is the modern
        // spelling and either is an acceptable hiding mechanism.
        clip: style.clip === 'auto' && style.clipPath !== 'none' ? style.clipPath : style.clip,
        overflow: style.overflow,
        cssWidth: style.width,
        cssHeight: style.height,
        // Ceil so a sub-pixel 0.5 box still reads as "1", and a 24px box can
        // never round down into the allowed range.
        renderedWidth: Math.ceil(rect.width),
        renderedHeight: Math.ceil(rect.height),
        occupiesSpace,
      })
      if (isEmpty) el.textContent = hadText
    }
    return out
  }, selector) as Promise<Probe[]>
}

/**
 * The load-bearing invariant. If Channel Pipeline has been rendered in this
 * context its stylesheet is in <head> for good and the defect is masked.
 */
async function assertPipelineNeverVisited(page: Page): Promise<void> {
  const visited = await page.evaluate(() => document.querySelector('.channel-pipeline-tab') !== null)
  expect(
    visited,
    'Channel Pipeline was rendered in this context. Its stylesheet is now appended to <head> ' +
      'permanently, which MASKS the defect this spec exists to catch. Remove the pipeline ' +
      'navigation, or move the new assertion to its own context.'
  ).toBe(false)
}

test.describe('screen-reader-only regions are hidden without a Channel Pipeline visit', () => {
  // One login for the whole file — the login endpoint is rate-limited at
  // 5/minute and each test needs its own context. Reused auth does not warm
  // the <head>: no tab chunk has been fetched in a context built from it.
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    // captureStorageState() backs off past the 5/minute login window twice.
    test.setTimeout(4 * 60 * 1000)
    storageState = await captureStorageState(browser)
  })

  for (const route of COLD_ROUTES) {
    test(`${route.id}: every .sr-only / .visually-hidden element is clipped to 1x1 on a cold context`, async ({
      browser,
    }) => {
      // Cold context + a lazily-loaded route + networkidle waits.
      test.setTimeout(3 * 60 * 1000)

      const page = await openApp(browser, storageState)
      try {
        await goToRoute(page, route)
        // Let the aria-live regions mount and any first announcement settle.
        await page.waitForTimeout(500)

        const probes = await probeHiddenElements(page, SR_ONLY_SELECTOR)

        expect(
          probes.length,
          `No element matched \`${SR_ONLY_SELECTOR}\` on #${route.id}. This spec cannot pass ` +
            `vacuously: the route is expected to render at least ${route.minElements}. Either the ` +
            `aria-live region was removed (update COLD_ROUTES) or the route failed to mount.`
        ).toBeGreaterThanOrEqual(route.minElements)

        const notHidden = probes
          .map((probe) => {
            const actual = {
              position: probe.position,
              clip: probe.clip,
              overflow: probe.overflow,
              cssWidth: probe.cssWidth,
              cssHeight: probe.cssHeight,
              renderedWidth: probe.renderedWidth,
              renderedHeight: probe.renderedHeight,
              occupiesSpace: probe.occupiesSpace,
            }
            const wrong = (Object.keys(HIDDEN) as Array<keyof typeof HIDDEN>).filter(
              (key) => actual[key] !== HIDDEN[key]
            )
            return wrong.length
              ? `  [${probe.cls}] "${probe.text}"` +
                  (probe.announcementInjected ? '  (announcement injected to make the box measurable)' : '') +
                  '\n' +
                  wrong.map((key) => `      ${key}: ${String(actual[key])}  (expected ${String(HIDDEN[key])})`).join('\n')
              : null
          })
          .filter((entry): entry is string => entry !== null)

        expect(
          notHidden,
          notHidden.length === 0
            ? ''
            : `${notHidden.length} screen-reader-only element(s) on #${route.id} are NOT visually ` +
                `hidden on a cold context — their announcement text renders as ordinary visible copy ` +
                `until the operator happens to open Channel Pipeline. The utility must be declared in ` +
                `frontend/src/shared/common.css § 2 COMMON UTILITIES (eager), not in a lazily-loaded ` +
                `tab chunk. See docs/css_guidelines.md § Other and bead ` +
                `enhancedchannelmanager-zncyv.\n${notHidden.join('\n')}`
        ).toEqual([])

        await assertPipelineNeverVisited(page)
      } finally {
        await page.context().close()
      }
    })
  }
})
