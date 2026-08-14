/**
 * Shared plumbing for the three rendered-CSS regression guards:
 *
 *   - `e2e/sr-only-hidden.spec.ts`        screen-reader-only utility stays hidden
 *   - `e2e/frozen-chrome.spec.ts`         the shell's frozen constants
 *   - `e2e/route-typography-scale.spec.ts` the P1 type scale
 *
 * All three need the same three things, and all three get them wrong in the
 * same expensive ways if each rolls its own:
 *
 * 1. ONE LOGIN PER SPEC FILE. `backend/auth/routes.py` rate-limits the login
 *    endpoint at `5/minute`. These guards open many browser contexts (a cold
 *    one per route; one per theme x viewport), and a context per login blows
 *    that budget instantly — the failure surfaces as
 *    `Login failed: Too Many Requests`, which reads like a broken assertion
 *    and is really a self-inflicted flake. `captureStorageState()` logs in
 *    exactly once and every later context is created from the returned
 *    cookies + localStorage. A context built this way is still COLD in the
 *    sense that matters for these guards: no tab chunk has been fetched, so
 *    no lazily-appended stylesheet is in <head>.
 *
 * 2. HASH NAVIGATION, NEVER `page.reload()`. Every route tab is a lazily
 *    imported chunk whose stylesheet is appended to <head> on first visit and
 *    never removed. A reload throws all of them away, which resets the exact
 *    state these guards observe and would silently turn a real failure into a
 *    pass. `navigateToTab()` in `fixtures/base.ts` reloads on its recovery
 *    path, and cannot leave Settings (Settings replaces the primary rail with
 *    its own), so it is unusable here.
 *
 * 3. AN EXPLICIT `waitFor` ON THE LOGIN GATE. `isVisible({ timeout })` is a
 *    no-op in Playwright — the option is ignored and the call returns the
 *    current state immediately — so a still-rendering login form reads as
 *    "not a login page" and the run proceeds into a blank shell.
 *
 * 4. A STATED AUTH POSTURE WHEN THERE IS NO BACKEND. See
 *    `stubAuthPostureWhenBackendless()` below: on the `E2E_EXACT_BUILD`
 *    preview build there is no server to answer the posture probes, and the
 *    app now declines to guess one.
 */
import { expect, isLoginPage, performLogin } from './base'
import type { Browser, BrowserContext, Page } from '@playwright/test'

/**
 * State the instance's auth posture when the run has no backend to ask.
 *
 * `E2E_EXACT_BUILD=true` builds the checked-out source and serves it on an
 * isolated preview port with NO backend — that is what the flag means, and it
 * is the mode the `Screen-Reader-Only Rendering Guard` CI job runs in
 * (`.github/workflows/test.yml`, `docs/testing.md` § "Rendered-CSS regression
 * guards"). Every `/api` call fails there, including the two auth-posture
 * probes `ProtectedRoute` gates the whole app on.
 *
 * That used to render the app shell anyway, because `useAuthRequired()`
 * resolved an unanswered probe to "auth is not required" and the setup probe's
 * catch did the same. Bead `enhancedchannelmanager-p388h` removed that
 * fail-open: an unresolved posture renders a retryable "Cannot reach ECM"
 * screen instead of the app. That is the correct product behaviour — a
 * degraded backend must not produce a sessionless app shell — and it is also
 * the exact screen this fixture was left staring at, with `.tab-navigation`
 * never appearing and the 30s `waitForSelector` timing out. The fixture was
 * riding the defect, so the fixture is what changes.
 *
 * These four routes are the same set `openDeterministicOperatorShell()`
 * (`e2e/operator-shell.spec.ts:183`) and `openApp()`
 * (`e2e/filter-select-ownership.spec.ts:29`) already install for the same
 * reason on the same backend-less preview build. The posture the three of them
 * state is identical, `require_auth: false` with `setup_complete: true` and no
 * session; only `dispatcharr_enabled`, which nothing here reads, differs
 * between them. It describes the instance these guards have always measured.
 *
 * ONLY the posture probes are stubbed. Every data endpoint still fails exactly
 * as it did before, so each route renders the same empty and error states this
 * guard family has always measured — the rendered CSS under test is unchanged.
 *
 * Gated on `E2E_EXACT_BUILD` because that flag IS the backend-less mode. The
 * other six guards in this family run against a live ECM container on :6100
 * and must keep exercising its real auth posture and its real login.
 */
async function stubAuthPostureWhenBackendless(context: BrowserContext): Promise<void> {
  if (process.env.E2E_EXACT_BUILD !== 'true') return
  const json = (body: unknown) => ({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await context.route(/\/api\/auth\/status(?:\/|\?|$)/, (route) =>
    route.fulfill(json({ require_auth: false, setup_complete: true, dispatcharr_enabled: false })))
  await context.route(/\/api\/auth\/setup-required(?:\/|\?|$)/, (route) =>
    route.fulfill(json({ required: false })))
  await context.route(/\/api\/auth\/me(?:\/|\?|$)/, (route) =>
    route.fulfill({
      ...json({ detail: 'No session on the backend-less preview build' }),
      status: 401,
    }))
  await context.route(/\/api\/session-start(?:\?|$)/, (route) => route.fulfill({ status: 204, body: '' }))
}

/** A primary route: its hash id and the selector proving its chunk rendered. */
export interface RouteSpec {
  id: string
  root: string
}

/**
 * The ten primary routes, in the order the rail lists them. Dashboard is
 * deliberately absent: it is the eager landing route, not a lazily-loaded tab,
 * and the guards that care about it name it explicitly.
 */
export const PRIMARY_ROUTES: ReadonlyArray<RouteSpec> = [
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

/** Playwright's opaque storage-state blob. Captured once, reused everywhere. */
export type StorageState = Awaited<ReturnType<BrowserContext['storageState']>>

/**
 * Log in exactly once and hand back the resulting cookies + localStorage.
 * Call from `test.beforeAll`; pass the result to every `openApp()`.
 */
export async function captureStorageState(browser: Browser): Promise<StorageState> {
  // The 5/minute budget is shared with every other spec file in the run and
  // with whatever the operator is doing in the same container. A 429 here is
  // infrastructure noise, not a finding, and letting it surface as a test
  // failure is exactly the "probably the port thing" rot that makes a gate
  // untrustworthy. Back off past the one-minute window and retry twice.
  let lastError: unknown
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, 65_000))
    const context = await browser.newContext()
    try {
      await stubAuthPostureWhenBackendless(context)
      const page = await context.newPage()
      await page.goto('/', { waitUntil: 'domcontentloaded' })
      if (await isLoginPage(page)) await performLogin(page)
      await page.waitForSelector('.tab-navigation', { state: 'visible', timeout: 30000 })
      return await context.storageState()
    } catch (error) {
      lastError = error
      if (!/Too Many Requests/i.test(String(error))) throw error
    } finally {
      await context.close()
    }
  }
  throw new Error(
    'Could not authenticate after 3 attempts spread over the login rate-limit window ' +
      `(backend/auth/routes.py: 5/minute). Last error: ${String(lastError)}`
  )
}

/**
 * A brand-new context with no tab chunk loaded, already authenticated.
 * `viewport` defaults to Playwright's; pass one when the guard pins it.
 */
export async function openApp(
  browser: Browser,
  storageState: StorageState,
  viewport?: { width: number; height: number }
): Promise<Page> {
  const context = await browser.newContext({ storageState, ...(viewport ? { viewport } : {}) })
  await stubAuthPostureWhenBackendless(context)
  const page = await context.newPage()
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  // Belt and braces: if the reused state has expired we would land on the
  // login gate again, and every downstream assertion would be measuring a
  // login form. An explicit waitFor is required — see note 3 above.
  if (await isLoginPage(page)) await performLogin(page)
  await page.waitForSelector('.tab-navigation', { state: 'visible', timeout: 30000 })
  return page
}

/** Navigate by hash and wait for the route's own chunk to have rendered. */
export async function goToRoute(page: Page, route: RouteSpec): Promise<void> {
  await page.evaluate((id) => {
    window.location.hash = `#${id}`
  }, route.id)
  await page.waitForSelector('.tab-loading', { state: 'hidden', timeout: 60000 }).catch(() => undefined)
  await page.waitForSelector(route.root, { timeout: 60000 })
  // Let async page data land so we measure a populated route, not a skeleton.
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(400)
}

/**
 * Pin the theme. The app re-applies its stored theme when settings load, so
 * this is asserted rather than assumed: a guard that silently measured the
 * wrong theme in two of three passes would look green and prove nothing.
 */
export async function setTheme(page: Page, theme: Theme): Promise<void> {
  const attribute = THEME_ATTRIBUTE[theme]
  await page.evaluate((value) => {
    document.documentElement.setAttribute('data-theme', value)
  }, attribute)
  await page.waitForTimeout(150)
  await expect
    .poll(() => page.evaluate(() => document.documentElement.getAttribute('data-theme')), { timeout: 5000 })
    .toBe(attribute)
}

/** The three shipped themes. */
export type Theme = 'dark' | 'light' | 'high-contrast'

export const THEMES: readonly Theme[] = ['dark', 'light', 'high-contrast']

/**
 * How each theme is spelled in the DOM. Dark is the `:root` default and the
 * app writes an EMPTY `data-theme` for it (SettingsTab.tsx:1200) — there is no
 * `[data-theme="dark"]` block in index.css. Setting the literal string "dark"
 * would still render dark by fallback, so a guard that did so would look
 * correct and never actually exercise the code path the app uses.
 */
const THEME_ATTRIBUTE: Record<Theme, string> = {
  dark: '',
  light: 'light',
  'high-contrast': 'high-contrast',
}
