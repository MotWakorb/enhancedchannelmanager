/**
 * A checkbox or radio renders at ONE size the application chose, and the text
 * beside it is part of the pointer target.
 *
 * Bead `enhancedchannelmanager-7lwe0`.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WHY THIS IS A SIBLING OF `control-typeface.spec.ts` AND NOT A THIRD ARM
 *
 * That spec asserts a property of the TEXT inside a control — the face the
 * font engine chose and the size the cascade gave it — and both of its arms
 * share one root cause and one fix: browsers do not inherit text properties
 * into form controls, so `index.css` has to reset them. A checkbox renders no
 * text at all. Its box is geometry, its defect had 54 separate causes (54 rule
 * blocks in 26 component stylesheets, each picking its own number), and its
 * remedy is a token plus the deletion of those numbers. Folding it in would
 * put two unrelated invariants behind one allowlist.
 *
 * The decisive difference is the WALK, not the taxonomy. `control-typeface`
 * walks the ten `PRIMARY_ROUTES`. Measured on this tree, those ten routes
 * expose exactly TWO visible checkboxes between them — 73 of the 75 live in
 * the seventeen `#settings/<section>` sub-routes, which is where the PO saw
 * the defect. This spec therefore discovers the settings sections from the
 * settings nav at run time and walks them too. Bolting a 27-route walk onto
 * the existing spec would triple its runtime for two arms that provably
 * cannot move on any of the extra routes.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ARM 1 — ONE BOX SIZE
 *
 * THE DEFECT THIS GUARDS. Measured 2026-07-30, RENDERED, before the fix:
 *
 *   routes  (10 primary + 17 settings sections, 75 visible controls)
 *       43 x 18px      23 x 16px       9 x 13px
 *   dialogs (81 of the 82 catalogued, via the dev harness, 136 controls)
 *       62 x 16px      53 x 13px      17 x 18px     3 x 14px     1 x 15px
 *
 * Five distinct rendered sizes for one control. The CSS declares seven
 * (`1rem` and `0.875rem` collapse onto 16px and 14px when rendered), and 16 of
 * the 54 rule blocks declare no size at all and inherited Chromium's own
 * 13px — which is why the estate had a 13px population that no stylesheet
 * mentions. Reading the declarations would have reported the wrong number in
 * both directions.
 *
 * WHY IT READS THE TOKEN INSTEAD OF ASSERTING 16px. Same discipline as
 * `control-typeface.spec.ts` measuring the UA's control size in a CSS-free
 * iframe rather than hardcoding 13.3333px: a spec that repeats the number is a
 * second place to change it, and it goes stale silently. This one resolves
 * `--control-box-size` through a probe element — so it asserts *agreement with
 * whatever the application declares*, and a token that is missing, malformed
 * or resolves to `auto` fails loudly rather than making the arm vacuous.
 *
 * ARM 2 — THE LABEL IS THE TARGET
 *
 * WCAG 2.5.8 (AA) sets a 24x24 CSS px minimum target. NO box in this estate
 * reaches it and none plausibly could — a 24px checkbox beside 13px body text
 * would be nearly twice the height of its own label. What makes that
 * acceptable is the label association: when `<label>` wraps the input (or
 * points at it with `for`), clicking the TEXT toggles the control and the
 * target is the whole row, not the box. When it does not, the box IS the
 * target and shrinking it makes a real accessibility problem worse — which is
 * why arm 1 could not be chosen without this census.
 *
 * MEASURED, at both viewports and in all three themes (identical in all six —
 * the association is a property of the markup, so this is a check, not an
 * assumption):
 *
 *   routes    65 of 75 associated      10 not
 *   dialogs  102 of 136 associated     34 not
 *
 * `ALLOWED_UNLABELLED` was seeded with the 3 route SITES behind those 10, and
 * is EMPTY as of bead `enhancedchannelmanager-m26f8`, which fixed all five
 * source sites behind the 44. It ratchets: a new site fails, and a fixed site
 * whose entry survives fails too.
 *
 * ARM 3 — THE CONTROL SAYS WHAT IT IS
 *
 * Added by `enhancedchannelmanager-m26f8`. Arm 2 asks whether the label
 * extends the POINTER target; this asks whether the control has an accessible
 * NAME at all. They are independent, and the census that produced arm 2 did
 * not measure this one: of its 44 unlabelled controls, 30 carried `aria-label`
 * and announced perfectly well, 8 were named only by `title` — and named the
 * action rather than the control, "Click to disable this sort criterion" — and
 * 6 had no name whatsoever and announced as a bare "checkbox".
 *
 * `title` alone counts as a failure. It is the last resort of the accname
 * cascade and is never surfaced to touch or keyboard-only users.
 *
 * ARM 4 — THE TARGET IS BIG ENOUGH TO HIT
 *
 * Added by `enhancedchannelmanager-3h2u1`. Arm 2 established that a label
 * EXISTS, which is what makes the row rather than the box the target. This
 * asks the question arm 2 stops one step short of: is that row actually 24x24?
 *
 * It was deliberately not added with arm 2, and the reason is visible in the
 * numbers. MEASURED on the tree m26f8 left behind, at both viewports in all
 * three themes, routes and dialogs together — 211 controls, every one of them
 * labelled:
 *
 *   120 rows cleared 24x24        91 did not
 *   0 of the 91 failed on WIDTH   all 91 failed on height, at 18.19 / 19.5 /
 *                                 21.19 / 21.5px
 *
 * A row is as wide as the form it sits in, so width was never in question; the
 * defect was only ever that a 13px line box is 18.2px tall. Landing this arm
 * with arm 2 would have meant shipping it red on 91 sites, or shipping it with
 * an allowlist holding nine tenths of the estate — which is not a ratchet, it
 * is a record of a decision not taken.
 *
 * The remedy is `--control-target-min` and the `label:has(input)` base rule in
 * `index.css`, plus one rule at the single site whose label uses `for` instead
 * of wrapping. Like arm 1 this reads the token through a probe rather than
 * repeating 24, so the spec cannot drift from the application's own number.
 *
 * WHAT IT MEASURES IS THE UNION of the control's rect and the rects of every
 * VISIBLE label associated with it — that is the region a pointer can hit, and
 * it is the only measurement that gives the same answer for a wrapping label
 * and a `for` label sitting beside its box.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WHAT THIS SPEC CANNOT SEE: dialogs. Like every guard in this set it walks
 * routes with no modal open, so the 136 dialog-side controls above are NOT
 * covered here; they were measured before and after with the dev modal harness
 * (`scripts/measure-modal-typography.mjs` drives the same harness). One base
 * rule in `index.css` sizes both populations, and deleting it turns arm 1 red
 * on the routes immediately — but a dialog-only regression, such as a new
 * modal stylesheet re-introducing a literal `width: 18px`, would slip past.
 * Saying so is the point: `control-typeface.spec.ts` shipped without naming
 * its blind spot and stayed green through 516 mis-sized controls.
 *
 * ARM 4 INHERITS THAT BLIND SPOT AND IT IS WORSE THERE, because the arm's
 * population is more lopsided: of the 91 short rows it was written for, 40 are
 * on routes and 51 are inside dialogs. The `label:has(input)` base rule fixes
 * both and deleting it turns this arm red on the routes at once, but a dialog
 * that reintroduces a short row — a new modal stylesheet setting
 * `min-height: 0`, or a new `for`-shaped label like the one in
 * `.settings-section .checkbox-content` — is invisible to CI until bead
 * `enhancedchannelmanager-48jr2` extends these guards into the harness. The 51
 * dialog rows were proved fixed locally against the harness, at both viewports
 * in all three themes. Nothing in CI repeats that.
 *
 * WHAT IT MEASURES. The build being SERVED, not the working tree. Against a
 * stale container this reports the stale CSS. Deploy first, or point
 * `E2E_BASE_URL` at a build of the tree under test.
 *
 * EXPECTED STATE: RED until `--control-box-size` and its base rule land in
 * `frontend/src/index.css`. That is deliberate — the guard is written before
 * the fix so the fix has something to turn green.
 */
import { test, expect } from './fixtures/base'
import {
  PRIMARY_ROUTES,
  THEMES,
  captureStorageState,
  goToRoute,
  openApp,
  setTheme,
  type RouteSpec,
  type StorageState,
} from './fixtures/css-guard'
import type { Page } from '@playwright/test'

/**
 * Controls allowed to render at a size other than `--control-box-size`.
 *
 * RATCHETED, per the discipline in `route-typography-scale.spec.ts`: a NEW
 * violation fails, and a STALE entry — one whose control now matches the
 * token — fails too, with "delete this entry".
 *
 * Empty on purpose, and it is the whole point of the bead. The estate had five
 * rendered sizes because 54 rule blocks each picked one; an entry here would
 * be the 55th. A control that genuinely needs a different box needs a second
 * TOKEN with a stated reason in `index.css`, not an exception in a test.
 */
const ALLOWED_DIFFERENT_BOX: ReadonlyArray<{ route: string; site: string; bead: string }> = []

/**
 * Sites where no `<label>` is associated with the control, so the box is the
 * only pointer target.
 *
 * Every entry is a WCAG 2.5.8 gap: the remedy is markup (wrap the input in a
 * `<label>`, or give it an `id` and a `<label for>`), not CSS.
 *
 * EMPTY as of bead `enhancedchannelmanager-m26f8`, which fixed all five source
 * sites behind the 2026-07-30 census's 44 unlabelled instances. The three
 * entries that used to sit here — `div.sort-priority-list`, `th.col-select`
 * and `td.col-select` — are the route-visible ten of that 44; the other 34 are
 * the same five sites rendered inside dialogs, which this spec cannot reach.
 *
 * Ratcheted in both directions. A new unlabelled site fails; adding a label to
 * an allowed site FAILS the run until its entry is deleted, so the list can
 * only shrink.
 */
const ALLOWED_UNLABELLED: ReadonlyArray<{ route: string; site: string; note: string }> = []

/**
 * Sites whose control has no usable ACCESSIBLE NAME.
 *
 * A SECOND defect that shares the first's cause and was not measured by the
 * 2026-07-30 census (bead `enhancedchannelmanager-m26f8`). That census asked
 * "does clicking the label text toggle the control" — a hit-target question.
 * A control can fail that and still announce correctly, if it carries
 * `aria-label`; and a control can pass it and still announce nothing useful.
 * The two are independent, and an unnamed checkbox is the worse defect: the
 * operator hears "checkbox, unchecked" and has no idea what it governs.
 *
 * MEASURED across all 211 controls on 2026-07-31, of the 44 unlabelled:
 *   30  named by `aria-label`     — announce correctly, only the target is small
 *    8  named by `title` ONLY     — settings/channel-defaults sort priorities
 *    6  NO ACCESSIBLE NAME AT ALL — 3 print-guide rows, 3 task-dialog toggles
 *
 * `title` alone counts as a FAILURE here, not a pass. It is a name of last
 * resort in the accname cascade, it is not surfaced to touch or keyboard-only
 * users at all, and the eight that relied on it were all naming the ACTION
 * ("Click to disable this sort criterion") rather than the control, so a
 * screen-reader user heard an instruction where the name belonged.
 *
 * Empty on purpose. Naming a control costs one attribute; there is no site
 * that cannot have one.
 */
const ALLOWED_UNNAMED: ReadonlyArray<{ route: string; site: string; note: string }> = []

/**
 * Sites whose pointer TARGET is smaller than `--control-target-min`.
 *
 * Empty as of bead `enhancedchannelmanager-3h2u1`, which lifted all 91 short
 * rows — 40 on routes, 51 in dialogs — to the WCAG 2.5.8 floor.
 *
 * An entry here is a claim that 24x24 is the wrong trade at that site, and it
 * has to say why in terms of the row rather than the box: shrinking a target
 * below the floor is only defensible when the same action has a second, larger
 * target elsewhere (2.5.8's own "equivalent" exception) or when the row is
 * genuinely inline in a sentence. "The list would get taller" is not a reason
 * — the fix is a floor, so it costs nothing on any row that already clears it.
 *
 * Ratcheted in both directions, like the two lists above.
 */
const ALLOWED_SMALL_TARGET: ReadonlyArray<{ route: string; site: string; note: string }> = []

/**
 * Sub-pixel slack. Box sizes are integral px here, and the loosest real
 * deviation observed is the 0.02px a modal's entry transform introduces — well
 * inside this, and far below the 1px that separates any two sizes in the
 * estate's old spread.
 */
const SIZE_EPSILON = 0.25

/**
 * Sub-pixel slack for arm 4, and it is load-bearing rather than cosmetic.
 *
 * A `min-height` floor puts a compliant row at EXACTLY the token, so every row
 * this arm passes sits on a knife edge: any transform in the ancestor chain —
 * a modal's ~0.999 entry scale is the one in this codebase — reports 23.976px
 * for a 24px row and turns a correct row into a failure. That is not
 * hypothetical: the 121-of-167 figure in bead `3h2u1` is best explained by a
 * measurement taken mid-entry-transition (bead `iotbh`), which counts the 33
 * rows sitting at exactly 24px as breaches.
 *
 * A quarter of a CSS pixel is not a target-size question by any reading of
 * 2.5.8, and it is two orders of magnitude below the 6px that separated the
 * shortest failing row from the floor.
 */
const TARGET_EPSILON = 0.25

/**
 * Minimum visible checkbox/radio controls the whole walk must yield.
 *
 * 75 were measured on 2026-07-30 on a near-empty instance. The floor is set
 * well below that so ordinary data drift does not trip it, and well above zero
 * so the single most likely way a guard like this dies quietly — the routes
 * fail to mount, nothing is found, no violation is reported, green — cannot
 * happen. Same floor discipline as `control-typeface.spec.ts`.
 */
const MIN_CONTROLS_TOTAL = 45

/** Settings sections the nav must at least yield; 17 were present on 2026-07-30. */
const MIN_SETTINGS_SECTIONS = 12

const VIEWPORTS: ReadonlyArray<{ width: number; height: number }> = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
]

interface Control {
  route: string
  viewport: string
  theme: string
  /** `input[type=checkbox]` plus the control's own classes. */
  signature: string
  /** Nearest classed ancestor — the CSS rule that owns this control. */
  site: string
  text: string
  widthPx: number
  heightPx: number
  /** The pointer target: control rect ∪ every visible associated label rect. */
  targetWidthPx: number
  targetHeightPx: number
  labelled: boolean
  /** Which step of the accname cascade produced the name; `NONE` = unnamed. */
  nameFrom: 'aria-labelledby' | 'aria-label' | 'label' | 'title' | 'NONE'
  accessibleName: string
}

/**
 * Resolve a length token the way the browser does. Used for
 * `--control-box-size` (arm 1) and `--control-target-min` (arm 4).
 *
 * Not `getPropertyValue()` alone: the token may be written in any unit, and a
 * declared string is not a rendered length. A probe element is given the
 * custom property as its width and the used value is read back, so what the
 * assertion compares against is a real px length produced by the cascade.
 * Returns `null` when the token is absent or does not resolve to a length —
 * that is the pre-fix state, and it must fail loudly rather than quietly
 * comparing everything to zero.
 */
async function resolveLengthToken(
  page: Page,
  name: string
): Promise<{ declared: string; px: number | null }> {
  return page.evaluate((token) => {
    const declared = getComputedStyle(document.documentElement).getPropertyValue(token).trim()
    const probe = document.createElement('div')
    probe.style.cssText = `position:absolute;left:-9999px;top:0;height:0;width:var(${token})`
    document.body.appendChild(probe)
    const used = getComputedStyle(probe).width
    probe.remove()
    const px = Number.parseFloat(used)
    return { declared, px: declared !== '' && Number.isFinite(px) && px > 0 ? px : null }
  }, name)
}

/** The `#settings/<section>` sub-routes, read from the settings nav itself. */
async function discoverSettingsRoutes(page: Page): Promise<RouteSpec[]> {
  await goToRoute(page, { id: 'settings', root: '.settings-tab' })
  const ids = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLAnchorElement>('a[href^="#settings/"]')]
      .map((a) => a.getAttribute('href')!.slice(1))
      .filter((v, i, all) => all.indexOf(v) === i)
  )
  return ids.map((id) => ({ id, root: '.settings-content' }))
}

const COLLECT = `(() => {
  const hidden = (el) => {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return true;
    if (parseFloat(cs.opacity) === 0) return true;
    const r = el.getBoundingClientRect();
    return r.width <= 1 || r.height <= 1;
  };
  // The nearest ancestor carrying a class is the thing a CSS rule selects, so
  // it is what an allowlist entry can name stably. Element-level records would
  // churn on list length; this is the unit a stylesheet actually moves.
  const siteOf = (el) => {
    let n = el.parentElement, depth = 0;
    while (n && depth < 5) {
      const cls = (typeof n.className === 'string' ? n.className : '').trim().split(/\\s+/).filter(Boolean);
      if (cls.length) return n.tagName.toLowerCase() + '.' + cls[0];
      n = n.parentElement; depth += 1;
    }
    return el.parentElement ? el.parentElement.tagName.toLowerCase() : '(detached)';
  };
  // The accessible name of a checkbox or radio, and WHICH cascade step gave
  // it. Those two types take no name from content and have no placeholder
  // step, so accname 1.2 reduces to exactly these four in this order — which
  // is why this is computed here rather than approximated from one attribute.
  const accName = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const parts = lb.split(/\\s+/).map((id) => document.getElementById(id)).filter(Boolean)
        .map((n) => (n.textContent || '').trim()).filter(Boolean);
      if (parts.length) return { name: parts.join(' '), from: 'aria-labelledby' };
    }
    const al = el.getAttribute('aria-label');
    if (al && al.trim()) return { name: al.trim(), from: 'aria-label' };
    const fromLabel = [...(el.labels || [])]
      .map((l) => (l.textContent || '').trim()).filter(Boolean).join(' ');
    if (fromLabel) return { name: fromLabel, from: 'label' };
    const ti = el.getAttribute('title');
    if (ti && ti.trim()) return { name: ti.trim(), from: 'title' };
    return { name: '', from: 'NONE' };
  };
  const out = [];
  for (const el of document.querySelectorAll('input[type="checkbox"], input[type="radio"]')) {
    if (hidden(el)) continue;
    const cs = getComputedStyle(el);
    const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\\s+/).filter(Boolean);
    // A label only extends the target if it can actually be pointed at.
    const pointable = [...(el.labels || [])].filter((l) => {
      const lcs = getComputedStyle(l);
      if (lcs.pointerEvents === 'none' || lcs.display === 'none' || lcs.visibility === 'hidden') return false;
      const lr = l.getBoundingClientRect();
      return lr.width > 1 && lr.height > 1;
    });
    const labelled = pointable.length > 0;
    // THE POINTER TARGET (arm 4): the smallest rect enclosing the control and
    // every label that can be pointed at. Bounding rects, not used width — a
    // target is measured where it lands on the screen, transform included,
    // which is the opposite of the box measurement two lines down.
    const own = el.getBoundingClientRect();
    let l0 = own.left, t0 = own.top, r0 = own.right, b0 = own.bottom;
    for (const lab of pointable) {
      const lr = lab.getBoundingClientRect();
      l0 = Math.min(l0, lr.left); t0 = Math.min(t0, lr.top);
      r0 = Math.max(r0, lr.right); b0 = Math.max(b0, lr.bottom);
    }
    const named = accName(el);
    out.push({
      targetWidthPx: Math.round((r0 - l0) * 100) / 100,
      targetHeightPx: Math.round((b0 - t0) * 100) / 100,
      signature: 'input[type=' + el.type + ']' + cls.map((c) => '.' + c).join(''),
      site: siteOf(el),
      text: ((el.labels && el.labels[0] ? el.labels[0].textContent : '') || '').trim().slice(0, 40),
      nameFrom: named.from,
      accessibleName: named.name.slice(0, 60),
      // The USED width, not the bounding rect: a modal's entry transform
      // scales the rect by ~0.1% and would report 15.99px for a 16px box.
      widthPx: parseFloat(cs.width),
      heightPx: parseFloat(cs.height),
      labelled,
    });
  }
  return out;
})()`

test.describe('checkbox and radio boxes take one token, and their labels are part of the target', () => {
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    storageState = await captureStorageState(browser)
  })

  /**
   * ONE walk, all four arms, one login.
   *
   * Not four tests: `fullyParallel` would put them in separate workers, each
   * running `beforeAll` and so each logging in, against an endpoint
   * rate-limited at `5/minute` that every other guard in this set also draws
   * on (`fixtures/css-guard.ts`, note 1). Every arm asserts softly, so one run
   * reports every category at once and a size failure can never mask a label
   * failure.
   *
   * Themes and viewports are swept without re-navigating: a route's chunk and
   * its stylesheet are already in `<head>`, and both a theme flip and a
   * viewport resize are style/layout operations on that same document. Six
   * cells per route cost six `getComputedStyle` passes, not six page loads.
   */
  test('every visible checkbox and radio renders at --control-box-size', async ({ browser }) => {
    test.setTimeout(12 * 60 * 1000)

    const page = await openApp(browser, storageState, VIEWPORTS[0])
    const found: Control[] = []
    let token: { declared: string; px: number | null } = { declared: '', px: null }
    let targetToken: { declared: string; px: number | null } = { declared: '', px: null }
    let settingsRoutes: RouteSpec[] = []

    try {
      token = await resolveLengthToken(page, '--control-box-size')
      targetToken = await resolveLengthToken(page, '--control-target-min')
      settingsRoutes = await discoverSettingsRoutes(page)
      const routes = [...PRIMARY_ROUTES, ...settingsRoutes]

      for (const route of routes) {
        await goToRoute(page, route)
        for (const viewport of VIEWPORTS) {
          await page.setViewportSize(viewport)
          await page.waitForTimeout(150)
          for (const theme of THEMES) {
            await setTheme(page, theme)
            const rows = (await page.evaluate(COLLECT)) as Omit<
              Control,
              'route' | 'viewport' | 'theme'
            >[]
            for (const row of rows) {
              found.push({
                route: route.id,
                viewport: `${viewport.width}x${viewport.height}`,
                theme,
                ...row,
              })
            }
          }
          await setTheme(page, 'dark')
        }
        await page.setViewportSize(VIEWPORTS[0])
      }
    } finally {
      await page.context().close()
    }

    // ── The instrument itself has to be sound before its findings mean
    //    anything. A missing token, an unmounted settings nav or an empty walk
    //    each make both arms vacuously green.
    expect
      .soft(
        token.px,
        token.px !== null
          ? ''
          : `--control-box-size does not resolve to a length on :root (declared: ` +
              `${token.declared === '' ? '<absent>' : `"${token.declared}"`}).\n` +
              `Every checkbox and radio in this application is sized by that token; without it there ` +
              `is nothing to compare a rendered box against and both arms below would pass vacuously.\n` +
              `Define it in the "Icon Size Scale" group of frontend/src/index.css, beside --icon-action.`
      )
      .not.toBeNull()

    expect
      .soft(
        targetToken.px,
        targetToken.px !== null
          ? ''
          : `--control-target-min does not resolve to a length on :root (declared: ` +
              `${targetToken.declared === '' ? '<absent>' : `"${targetToken.declared}"`}).\n` +
              `Arm 4 compares every rendered pointer target against that token; without it the arm ` +
              `would compare every row against zero and pass vacuously.\n` +
              `Define it in frontend/src/index.css beside --control-box-size (bead ` +
              `enhancedchannelmanager-3h2u1).`
      )
      .not.toBeNull()

    expect
      .soft(
        settingsRoutes.length,
        settingsRoutes.length >= MIN_SETTINGS_SECTIONS
          ? ''
          : `only ${settingsRoutes.length} #settings/<section> route(s) were discovered (floor ` +
              `${MIN_SETTINGS_SECTIONS}). 73 of the 75 controls this guard exists for live in those ` +
              `sections; if the nav did not render, this run proves nothing.`
      )
      .toBeGreaterThanOrEqual(MIN_SETTINGS_SECTIONS)

    const perCell = found.length / (VIEWPORTS.length * THEMES.length)
    expect
      .soft(
        perCell,
        perCell >= MIN_CONTROLS_TOTAL
          ? ''
          : `only ${perCell} visible checkbox/radio control(s) were found per viewport/theme cell ` +
              `(floor ${MIN_CONTROLS_TOTAL}, measured 75 on 2026-07-30). A walk that finds nothing ` +
              `reports no violations and reads as a pass.`
      )
      .toBeGreaterThanOrEqual(MIN_CONTROLS_TOTAL)

    if (token.px === null) return // nothing below can say anything useful

    // ── ARM 1: one box size ───────────────────────────────────────────────
    const wrongSize = found.filter(
      (c) =>
        Math.abs(c.widthPx - token.px!) > SIZE_EPSILON ||
        Math.abs(c.heightPx - token.px!) > SIZE_EPSILON
    )
    const sizeAllowed = (c: Control) =>
      ALLOWED_DIFFERENT_BOX.some((a) => a.route === c.route && a.site === c.site)
    const unexpectedSize = wrongSize.filter((c) => !sizeAllowed(c))
    const staleSize = ALLOWED_DIFFERENT_BOX.filter(
      (a) => !wrongSize.some((c) => c.route === a.route && c.site === a.site)
    )

    // Signatures, not one row per element: the same control is measured in six
    // cells, and a CSS rule moves a signature, not an instance.
    const sizeGroups = new Map<string, { cells: number; sample: Control }>()
    for (const c of unexpectedSize) {
      const key = `${c.route}|${c.site}|${c.signature}|${c.widthPx}x${c.heightPx}`
      const hit = sizeGroups.get(key)
      if (hit) hit.cells += 1
      else sizeGroups.set(key, { cells: 1, sample: c })
    }
    const sizeLines = [...sizeGroups.values()]
      .sort((a, b) => b.cells - a.cells || a.sample.route.localeCompare(b.sample.route))
      .map(
        ({ cells, sample }) =>
          `  ${sample.route.padEnd(28)} ${sample.site.padEnd(26)} ` +
          `${sample.widthPx}x${sample.heightPx}px  (${cells} viewport/theme cell(s))` +
          (sample.text ? `  e.g. "${sample.text}"` : '')
      )

    // Asserted on the SIGNATURE keys, not on the raw rows: the same control is
    // measured in six cells, so a 16-site failure dumps ~3,400 near-identical
    // objects under the message and buries it. The keys carry every distinct
    // fact a reader needs and the ratchet is unchanged.
    expect
      .soft(
        [...sizeGroups.keys()],
        unexpectedSize.length === 0
          ? ''
          : `${sizeGroups.size} checkbox/radio site(s) do not render at --control-box-size ` +
              `(${token.declared} = ${token.px}px, resolved this run through a probe element rather ` +
              `than hardcoded).\n\n` +
              sizeLines.join('\n') +
              `\n\nThe size lives in ONE place: --control-box-size in frontend/src/index.css, applied ` +
              `by the base rule beside it. A component stylesheet must not restate it as a literal — ` +
              `that is the seven-way spread bead enhancedchannelmanager-7lwe0 deleted. If a site truly ` +
              `needs a different box, add a second TOKEN with its reason next to the first.\n`
      )
      .toEqual([])

    expect
      .soft(
        staleSize,
        staleSize.length === 0
          ? ''
          : `${staleSize.length} ALLOWED_DIFFERENT_BOX entr(ies) no longer correspond to a control off ` +
              `the token. The exception has been fixed — delete the entry:\n` +
              staleSize.map((a) => `  ${a.route} ${a.site} (${a.bead})`).join('\n')
      )
      .toEqual([])

    // ── ARM 2: the label is part of the target ────────────────────────────
    const unlabelled = found.filter((c) => !c.labelled)
    const labelAllowed = (c: Control) =>
      ALLOWED_UNLABELLED.some((a) => a.route === c.route && a.site === c.site)
    const unexpectedUnlabelled = unlabelled.filter((c) => !labelAllowed(c))
    const staleUnlabelled = ALLOWED_UNLABELLED.filter(
      (a) => !unlabelled.some((c) => c.route === a.route && c.site === a.site)
    )

    const labelGroups = new Map<string, { cells: number; sample: Control }>()
    for (const c of unexpectedUnlabelled) {
      const key = `${c.route}|${c.site}|${c.signature}`
      const hit = labelGroups.get(key)
      if (hit) hit.cells += 1
      else labelGroups.set(key, { cells: 1, sample: c })
    }

    expect
      .soft(
        [...labelGroups.keys()],
        unexpectedUnlabelled.length === 0
          ? ''
          : `${labelGroups.size} checkbox/radio site(s) have no associated <label>, so the ` +
              `${token.px}px box is the ONLY pointer target — well under the 24x24 CSS px minimum in ` +
              `WCAG 2.5.8, with no row to fall back on.\n\n` +
              [...labelGroups.values()]
                .map(
                  ({ cells, sample }) =>
                    `  ${sample.route.padEnd(28)} ${sample.site.padEnd(26)} ${sample.signature}  ` +
                    `(${cells} cell(s))`
                )
                .join('\n') +
              `\n\nThe fix is markup, not CSS: wrap the input in a <label>, or give it an id and a ` +
              `<label for>. Then clicking the text toggles the control and the target is the whole row. ` +
              `If a site genuinely cannot carry a label, add it to ALLOWED_UNLABELLED with a note ` +
              `saying why.\n`
      )
      .toEqual([])

    expect
      .soft(
        staleUnlabelled,
        staleUnlabelled.length === 0
          ? ''
          : `${staleUnlabelled.length} ALLOWED_UNLABELLED entr(ies) now have a working label ` +
              `association. That is the outcome the list exists to drive — delete the entry so the ` +
              `census keeps shrinking:\n` +
              staleUnlabelled.map((a) => `  ${a.route} ${a.site} — ${a.note}`).join('\n')
      )
      .toEqual([])

    // ── ARM 3: the control says what it is ────────────────────────────────
    //
    // Independent of arm 2 and measured separately for that reason: arm 2 is
    // about the POINTER, this is about the ANNOUNCEMENT. Of the 44 controls
    // arm 2 flagged on 2026-07-30, 30 announced perfectly well.
    const unnamed = found.filter((c) => c.nameFrom === 'NONE' || c.nameFrom === 'title')
    const nameAllowed = (c: Control) =>
      ALLOWED_UNNAMED.some((a) => a.route === c.route && a.site === c.site)
    const unexpectedUnnamed = unnamed.filter((c) => !nameAllowed(c))
    const staleUnnamed = ALLOWED_UNNAMED.filter(
      (a) => !unnamed.some((c) => c.route === a.route && c.site === a.site)
    )

    const nameGroups = new Map<string, { cells: number; sample: Control }>()
    for (const c of unexpectedUnnamed) {
      const key = `${c.route}|${c.site}|${c.signature}|${c.nameFrom}`
      const hit = nameGroups.get(key)
      if (hit) hit.cells += 1
      else nameGroups.set(key, { cells: 1, sample: c })
    }

    expect
      .soft(
        [...nameGroups.keys()],
        unexpectedUnnamed.length === 0
          ? ''
          : `${nameGroups.size} checkbox/radio site(s) have no usable accessible name. A screen ` +
              `reader announces these as an unnamed checkbox, or reads a tooltip where the name ` +
              `belongs — a defect independent of the target size above, and a worse one.\n\n` +
              [...nameGroups.values()]
                .map(
                  ({ cells, sample }) =>
                    `  ${sample.route.padEnd(28)} ${sample.site.padEnd(26)} ` +
                    `name from: ${sample.nameFrom.padEnd(6)} (${cells} cell(s))` +
                    (sample.accessibleName ? `  "${sample.accessibleName}"` : '')
                )
                .join('\n') +
              `\n\nGive the control a name: wrap it in a <label> whose text describes it, or set ` +
              `aria-label where the surrounding text is decorative (an icon ligature, a priority ` +
              `badge) and would name it wrongly. "title" alone does not count — it is invisible to ` +
              `touch and keyboard users, and every site that relied on it was naming the action ` +
              `rather than the control (bead enhancedchannelmanager-m26f8).\n`
      )
      .toEqual([])

    expect
      .soft(
        staleUnnamed,
        staleUnnamed.length === 0
          ? ''
          : `${staleUnnamed.length} ALLOWED_UNNAMED entr(ies) now have a real accessible name — ` +
              `delete the entry:\n` +
              staleUnnamed.map((a) => `  ${a.route} ${a.site} — ${a.note}`).join('\n')
      )
      .toEqual([])

    // ── ARM 4: the target is big enough to hit ────────────────────────────
    //
    // Arm 2 asks whether a label extends the target; this asks whether the
    // result clears WCAG 2.5.8's 24x24 CSS px floor. Independent again: on the
    // pre-fix tree every one of the 91 short rows HAD a working label and
    // passed arm 2, and none of them was under 24px wide.
    if (targetToken.px !== null) {
      const floor = targetToken.px
      const tooSmall = found.filter(
        (c) =>
          c.targetWidthPx < floor - TARGET_EPSILON || c.targetHeightPx < floor - TARGET_EPSILON
      )
      const targetAllowed = (c: Control) =>
        ALLOWED_SMALL_TARGET.some((a) => a.route === c.route && a.site === c.site)
      const unexpectedSmall = tooSmall.filter((c) => !targetAllowed(c))
      const staleSmall = ALLOWED_SMALL_TARGET.filter(
        (a) => !tooSmall.some((c) => c.route === a.route && c.site === a.site)
      )

      const targetGroups = new Map<string, { cells: number; sample: Control }>()
      for (const c of unexpectedSmall) {
        const key = `${c.route}|${c.site}|${c.signature}|${c.targetWidthPx}x${c.targetHeightPx}`
        const hit = targetGroups.get(key)
        if (hit) hit.cells += 1
        else targetGroups.set(key, { cells: 1, sample: c })
      }

      expect
        .soft(
          [...targetGroups.keys()],
          unexpectedSmall.length === 0
            ? ''
            : `${targetGroups.size} checkbox/radio site(s) have a pointer target smaller than ` +
                `--control-target-min (${targetToken.declared} = ${floor}px, resolved this run ` +
                `through a probe element rather than hardcoded) — under the WCAG 2.5.8 (AA) ` +
                `minimum.\n\n` +
                [...targetGroups.values()]
                  .sort((a, b) => b.cells - a.cells || a.sample.route.localeCompare(b.sample.route))
                  .map(
                    ({ cells, sample }) =>
                      `  ${sample.route.padEnd(28)} ${sample.site.padEnd(26)} ` +
                      `${sample.targetWidthPx}x${sample.targetHeightPx}px  ` +
                      `(box ${sample.widthPx}x${sample.heightPx}, ${cells} viewport/theme cell(s))` +
                      (sample.text ? `  e.g. "${sample.text}"` : '')
                  )
                  .join('\n') +
                `\n\nThe height comes from the ROW, never from the box: --control-box-size stays at ` +
                `${token.px}px because an 18px box beside 13px text is what made Settings read ` +
                `oversized (bead enhancedchannelmanager-7lwe0), and 24px would be worse. If the label ` +
                `WRAPS its input the base rule in frontend/src/index.css already gives it the floor, ` +
                `so a failure here means a component rule is overriding min-height — delete that, ` +
                `do not restate 24px. If the label points at the input with "for" the base rule ` +
                `cannot see it (CSS cannot ask what a label points at) and the site needs its own ` +
                `min-height, as .settings-section .checkbox-content does.\n`
        )
        .toEqual([])

      expect
        .soft(
          staleSmall,
          staleSmall.length === 0
            ? ''
            : `${staleSmall.length} ALLOWED_SMALL_TARGET entr(ies) now clear the floor — that is the ` +
                `outcome the list exists to drive, so delete the entry:\n` +
                staleSmall.map((a) => `  ${a.route} ${a.site} — ${a.note}`).join('\n')
        )
        .toEqual([])
    }
  })
})
