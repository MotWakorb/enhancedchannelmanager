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
 * The 10 route sites are seeded into `ALLOWED_UNLABELLED` below. This arm does
 * not fix them — it stops the number growing, and ratchets so that fixing one
 * forces its entry to be deleted.
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
 * Every entry is a WCAG 2.5.8 gap that this bead did NOT fix: the remedy is
 * markup (wrap the input in a `<label>`, or give it an `id` and a
 * `<label for>`), not CSS, and it spans nine dialogs and two routes. Seeded
 * from the 2026-07-30 census so the number cannot grow unnoticed.
 *
 * Ratcheted in both directions. Adding a label to one of these sites FAILS the
 * run until its entry is deleted here, so the list can only shrink.
 */
const ALLOWED_UNLABELLED: ReadonlyArray<{ route: string; site: string; note: string }> = [
  {
    route: 'settings/channel-defaults',
    site: 'div.sort-priority-list',
    note: 'x8 stream-sort priority toggles; the row renders its own text, no <label> element at all',
  },
  {
    route: 'channel-pipeline',
    site: 'th.col-select',
    note: 'select-all in the rule table header; shared with 9 cp-* dialogs',
  },
  {
    route: 'channel-pipeline',
    site: 'td.col-select',
    note: 'per-row select in the rule table; shared with 9 cp-* dialogs',
  },
]

/**
 * Sub-pixel slack. Box sizes are integral px here, and the loosest real
 * deviation observed is the 0.02px a modal's entry transform introduces — well
 * inside this, and far below the 1px that separates any two sizes in the
 * estate's old spread.
 */
const SIZE_EPSILON = 0.25

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
  labelled: boolean
}

/**
 * Resolve `--control-box-size` the way the browser does.
 *
 * Not `getPropertyValue()` alone: the token may be written in any unit, and a
 * declared string is not a rendered length. A probe element is given the
 * custom property as its width and the used value is read back, so what the
 * assertion compares against is a real px length produced by the cascade.
 * Returns `null` when the token is absent or does not resolve to a length —
 * that is the pre-fix state, and it must fail loudly rather than quietly
 * comparing everything to zero.
 */
async function resolveBoxToken(page: Page): Promise<{ declared: string; px: number | null }> {
  return page.evaluate(() => {
    const declared = getComputedStyle(document.documentElement)
      .getPropertyValue('--control-box-size')
      .trim()
    const probe = document.createElement('div')
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;height:0;width:var(--control-box-size)'
    document.body.appendChild(probe)
    const used = getComputedStyle(probe).width
    probe.remove()
    const px = Number.parseFloat(used)
    return { declared, px: declared !== '' && Number.isFinite(px) && px > 0 ? px : null }
  })
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
  const out = [];
  for (const el of document.querySelectorAll('input[type="checkbox"], input[type="radio"]')) {
    if (hidden(el)) continue;
    const cs = getComputedStyle(el);
    const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\\s+/).filter(Boolean);
    // A label only extends the target if it can actually be pointed at.
    const labelled = [...(el.labels || [])].some((l) => {
      const lcs = getComputedStyle(l);
      if (lcs.pointerEvents === 'none' || lcs.display === 'none' || lcs.visibility === 'hidden') return false;
      const lr = l.getBoundingClientRect();
      return lr.width > 1 && lr.height > 1;
    });
    out.push({
      signature: 'input[type=' + el.type + ']' + cls.map((c) => '.' + c).join(''),
      site: siteOf(el),
      text: ((el.labels && el.labels[0] ? el.labels[0].textContent : '') || '').trim().slice(0, 40),
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
   * ONE walk, both arms, one login.
   *
   * Not two tests: `fullyParallel` would put them in separate workers, each
   * running `beforeAll` and so each logging in, against an endpoint
   * rate-limited at `5/minute` that every other guard in this set also draws
   * on (`fixtures/css-guard.ts`, note 1). Both arms assert softly, so one run
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
    let settingsRoutes: RouteSpec[] = []

    try {
      token = await resolveBoxToken(page)
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
  })
})
