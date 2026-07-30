/**
 * The operator shell's frozen constants.
 *
 * WHAT THIS PINS. Five measurements of the persistent chrome — the primary
 * rail, the top band, and the route title — settled by beads
 * `enhancedchannelmanager-57pp3`, `-eupzi`, `-sccol` and `-sl7dx`. They are
 * the thing every CSS change in this project must not move, and until now
 * they lived only in throwaway scratchpad measurement scripts: the claims
 * were reproducible, but nobody could re-check them after the session ended.
 *
 * CONSTANTS, NOT A BASELINE DIFF. There is deliberately no committed snapshot
 * to regenerate. A baseline file rots the first time someone runs the update
 * command to make a red build green — which is the normal way a frozen value
 * quietly stops being frozen. The numbers below are written out longhand, so
 * changing one is a reviewable diff with a bead attached, not a regenerated
 * artefact.
 *
 * WHY THE MATRIX IS SHAPED THIS WAY:
 *   - TEN ROUTES, because every route tab is a lazily loaded chunk whose
 *     stylesheet is appended to <head> on first visit and never removed. A
 *     bare rule in any one of them can move the shell on every route visited
 *     afterwards. Measuring one route would miss nine chunks.
 *   - ONE CONTEXT PER (theme, viewport), walked in rail order, so chunk
 *     accumulation is realistic rather than reset between routes.
 *   - THREE THEMES, because `index.css` re-declares tokens under
 *     `[data-theme="light"]` and `[data-theme="high-contrast"]`; a theme block
 *     that redefines a spacing or font token moves the chrome in that theme
 *     only, and dark-only measurement would never see it.
 *   - THREE VIEWPORTS, because the shell has responsive breakpoints and a
 *     constant that holds wide can still collapse narrow. 1280x720 is the
 *     MINIMUM SUPPORTED viewport and therefore the one that has to be pinned;
 *     it was added by bead `enhancedchannelmanager-70u0r.4`, which found the
 *     matrix stopping at 1280x800 — 80px of height that the supported floor
 *     does not have, and the height is what the Settings drill-in strains.
 *     1280x800 is kept alongside it rather than replaced: it is the width
 *     breakpoint's other height, and dropping a row from a frozen matrix is not
 *     a free change.
 *
 * NOT VACUOUS. Each probe declares the routes it must be present on. A probe
 * that fails to render where it is required fails the run — a guard whose
 * elements silently stop existing is worse than no guard, because it reports
 * green forever. The one legitimate absence is modelled explicitly: Settings
 * replaces the primary rail with its own secondary rail, so
 * `.tab-navigation .navigation-destination` genuinely does not exist there.
 *
 * WHEN THIS FAILS: something moved a frozen constant. That is either a real
 * regression (fix the CSS) or a deliberate re-freeze (change the number here,
 * in the same commit, citing the bead that authorised it). Never widen a
 * tolerance — these are exact values, and they are exact on purpose.
 */
import { test, expect } from './fixtures/base'
import {
  captureStorageState,
  goToRoute,
  openApp,
  PRIMARY_ROUTES,
  setTheme,
  THEMES,
  type StorageState,
  type Theme,
} from './fixtures/css-guard'
import type { Page } from '@playwright/test'

/** Viewports the constants must hold at. 1280x720 is the supported minimum. */
const VIEWPORTS: ReadonlyArray<{ width: number; height: number }> = [
  { width: 1600, height: 1000 },
  { width: 1280, height: 800 },
  { width: 1280, height: 720 },
]

/**
 * Routes on which a probe is allowed to be absent, with the reason. Anything
 * not listed here MUST render on all ten routes.
 */
const ABSENT_BY_DESIGN: Record<string, { routes: readonly string[]; reason: string }> = {
  'rail.label': {
    routes: ['settings'],
    reason: 'Settings replaces the primary rail with its own secondary section rail',
  },
  'rail.icon': {
    routes: ['settings'],
    reason: 'Settings replaces the primary rail with its own secondary section rail',
  },
}

/**
 * The frozen constants. `props` are read from `getComputedStyle` except
 * `width` / `height`, which are the rendered `getBoundingClientRect` box.
 */
const PROBES: ReadonlyArray<{ name: string; selector: string; expected: Record<string, string | number> }> = [
  {
    // The expanded primary rail. 68px is the COLLAPSED width and is a
    // different state, not a tolerance — `assertRailExpanded` rules it out
    // before anything is measured, so a stray collapse cannot be misread as a
    // width regression.
    name: 'sidebar.width',
    selector: '.primary-sidebar',
    expected: { width: 244 },
  },
  {
    name: 'rail.label',
    selector: '.tab-navigation .navigation-destination .navigation-label',
    expected: { fontSize: '14px', fontWeight: '400' },
  },
  {
    // The destination's own icon. Asserted twice over: the declared font-size
    // (which is what sizes a Material Icons ligature) and the rendered box,
    // because a ligature that fails to load renders as wide literal text at
    // the correct font-size.
    name: 'rail.icon',
    selector: '.tab-navigation .navigation-destination .material-icons',
    expected: { fontSize: '20px', width: 20, height: 20 },
  },
  {
    name: 'header.band',
    selector: '.header',
    expected: { height: 45 },
  },
  {
    // line-height 1.3 on a 20px font resolves to 26px. Chromium reports the
    // used pixel value, never the ratio, so 26px IS `line-height: 1.3` here.
    name: 'route.title',
    selector: '.page-header .header-title h1',
    expected: { fontSize: '20px', fontWeight: '700', lineHeight: '26px' },
  },
]

type Measured = Record<string, string | number> | null

async function measureChrome(page: Page): Promise<Record<string, Measured>> {
  return page.evaluate((probes) => {
    const out: Record<string, Measured> = {}
    for (const probe of probes) {
      const element = Array.from(document.querySelectorAll(probe.selector)).find((candidate) => {
        const rect = candidate.getBoundingClientRect()
        return rect.width > 0 && rect.height > 0
      })
      if (!element) {
        out[probe.name] = null
        continue
      }
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      const values: Record<string, string | number> = {}
      for (const property of Object.keys(probe.expected)) {
        values[property] =
          property === 'width' || property === 'height'
            ? Number(rect[property].toFixed(2))
            : (style[property as keyof CSSStyleDeclaration] as string)
      }
      out[probe.name] = values
    }
    return out
  }, PROBES.map((probe) => ({ name: probe.name, selector: probe.selector, expected: probe.expected })))
}

/**
 * The rail has a collapsed state at 68px. Measuring it collapsed would report
 * a 244 -> 68 "regression" that is really a state difference, so rule it out
 * up front with a message that says so.
 */
async function assertRailExpanded(page: Page): Promise<void> {
  const collapsed = await page.evaluate(
    () => document.querySelector('.primary-sidebar')?.classList.contains('is-collapsed') ?? null
  )
  expect(
    collapsed,
    'The primary rail is collapsed (68px) or missing. The frozen 244px is the EXPANDED width; ' +
      'a collapsed rail is a different state, not a regression. Reset the persisted collapse ' +
      'state before running this guard.'
  ).toBe(false)
}

test.describe('frozen shell constants hold on every route, theme and viewport', () => {
  // One login for the whole file — the login endpoint is rate-limited at
  // 5/minute and this matrix opens six contexts.
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(4 * 60 * 1000)
    storageState = await captureStorageState(browser)
  })

  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${theme} @ ${viewport.width}x${viewport.height}: all ten routes hold the frozen chrome`, async ({
        browser,
      }) => {
        // Ten lazily-loaded routes in one context, each with a networkidle wait.
        test.setTimeout(6 * 60 * 1000)

        const page = await openApp(browser, storageState, viewport)
        const drift: string[] = []
        try {
          for (const route of PRIMARY_ROUTES) {
            await goToRoute(page, route)
            // Re-assert after every navigation: the app re-applies its stored
            // theme when settings load (App.tsx:684), so a theme set once at
            // the top of the walk can be silently reverted mid-route — and the
            // remaining routes would then be measured in the wrong theme and
            // report green for a theme they never visited.
            await setTheme(page, theme as Theme)
            await assertRailExpanded(page)

            const measured = await measureChrome(page)
            for (const probe of PROBES) {
              const actual = measured[probe.name]
              if (actual === null) {
                const allowed = ABSENT_BY_DESIGN[probe.name]
                if (allowed && allowed.routes.includes(route.id)) continue
                drift.push(
                  `  ${route.id}  ${probe.name}  (${probe.selector})\n` +
                    `      NOT RENDERED. This probe is required on every route; either the shell ` +
                    `element was removed or renamed (update PROBES), or the route failed to mount. ` +
                    `A missing probe is never an automatic pass.`
                )
                continue
              }
              for (const [property, want] of Object.entries(probe.expected)) {
                if (actual[property] !== want) {
                  drift.push(
                    `  ${route.id}  ${probe.name}  (${probe.selector})\n` +
                      `      ${property}: ${String(actual[property])}   expected ${String(want)}`
                  )
                }
              }
            }
          }
        } finally {
          await page.context().close()
        }

        expect(
          drift,
          drift.length === 0
            ? ''
            : `${drift.length} frozen shell constant(s) moved in the ${theme} theme at ` +
                `${viewport.width}x${viewport.height}. These values were settled by beads ` +
                `enhancedchannelmanager-57pp3, -eupzi, -sccol and -sl7dx and are the invariant every ` +
                `CSS change in this project has to preserve. Fix the CSS, or — if the move is ` +
                `deliberate — change the constant in e2e/frozen-chrome.spec.ts in the same commit and ` +
                `cite the bead that authorised it. Do NOT widen a tolerance; there are none.\n${drift.join('\n')}`
        ).toEqual([])
      })
    }
  }
})
