/**
 * Form controls must render in the typeface AND at a size the application
 * chose — not at the user-agent's defaults for either.
 *
 * Two properties, one root cause, two independent assertions in one route
 * walk. Browsers do not inherit `font-family` into form controls, and they do
 * not inherit `font-size` either; `frontend/src/index.css` has to reset both,
 * and each half was missed once.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ARM 1 — THE FACE (bead `enhancedchannelmanager-6z299.9`)
 *
 * THE DEFECT THIS GUARDS. `frontend/src/index.css` sets `font-family` on the
 * root, but browsers do not inherit fonts into form controls, and there is no
 * `button, input, select, textarea { font-family: inherit }` reset. So every
 * button, input, select and textarea takes the user-agent default family and
 * renders in a different face from the label beside it. Bead
 * `enhancedchannelmanager-6z299.9` owns this guard; the one-line reset has its
 * own bead and lands separately.
 *
 * WHY THIS ASSERTS THE RENDERED FACE AND NOT `font-family`. The defect was
 * first diagnosed by reading `getComputedStyle(el).fontFamily`, which returns
 * the DECLARED stack — not the face the browser actually chose. That produced a
 * confidently wrong diagnosis ("controls render in Arial, not Inter"): measured
 * by advance width on the development host, `Inter` resolves identically to a
 * deliberately nonexistent font name, and `Arial` resolves identically to
 * generic `sans-serif`. Neither is installed. The app text renders in
 * `system-ui`; the controls render in generic `sans-serif`.
 *
 * A spec that compared `fontFamily` strings would therefore compare two
 * declarations, pass while the defect persisted, and encode the original
 * mistake in executable form. This one measures what the font engine did.
 *
 * THE METHOD. For each control, take its computed `font-family` and its
 * parent's, and measure the advance width of one fixed string under each — at
 * an identical size and weight, so only the family varies. Two stacks that
 * resolve to the same face produce byte-identical widths; two that resolve
 * differently do not. `document.fonts.check()` is deliberately NOT used: it
 * returns true for fallbacks, so it cannot distinguish "Inter is available"
 * from "something will be substituted for Inter".
 *
 * WHY IT ASSERTS SAMENESS, NEVER A FONT NAME. Inter is not bundled — no
 * `@font-face`, no webfont link, no font files in the tree. Which face resolves
 * is therefore a property of the client, not of this repository, and will differ
 * between the CI runner, the developer host and an operator's machine. Pinning a
 * name would make this spec fail for reasons that are nobody's bug. Pinning
 * *agreement between a control and its surroundings* holds everywhere.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ARM 2 — THE SIZE (bead `enhancedchannelmanager-ul2tp`)
 *
 * THE DEFECT THIS GUARDS, AND WHY ARM 1 COULD NEVER SEE IT. Arm 1 measures by
 * canvas advance width at a FIXED 16px/400, deliberately, so that only the
 * family can move the number. That makes it correct for what it asserts and
 * structurally incapable of noticing that the control itself renders at the
 * user-agent's own control size. Measured on 2026-07-30 with arm 1 green: 274
 * of 418 visible controls across the ten routes, and 516 elements across 77 of
 * the 81 catalogued dialogs (`scripts/measure-modal-typography.mjs`), rendered
 * at 13.3333px — the Chromium default for form controls, a size nobody chose.
 *
 * WHY IT COMPARES AGAINST A MEASURED UA DEFAULT AND NOT THE LITERAL 13.3333px.
 * Same reasoning as "never pin a font name" below: 13.3333px is a property of
 * the client, not of this repository. It is Chromium's `-webkit-small-control`
 * size; other engines and other platforms differ, and it is NOT derived from
 * the root font-size (measured: root 16px, control 13.3333px), so it cannot be
 * computed either. So the run measures it, in an `srcdoc` iframe that carries
 * none of this application's CSS — one blank document per run, one
 * `getComputedStyle` per control tag. A hardcoded 13.3333px would make this
 * spec fail, or pass, for reasons that are nobody's bug.
 *
 * WHY "NOT THE UA DEFAULT" RATHER THAN "EQUAL TO ITS SURROUNDINGS". A control's
 * size legitimately differs from the text beside it — a 10px chip button next
 * to 13px body copy is on the P1 scale, not a defect. And inheriting is not
 * automatically right either: `body` computes to 16px here, so
 * `font-size: inherit` would have taken 263 of those 274 controls to 16px,
 * which is just as unchosen as 13.3333px and 2.67px louder. The invariant that
 * holds is the narrow one — a control renders at a size the application picked.
 * Whether that size is ON the P1 scale is `e2e/route-typography-scale.spec.ts`'s
 * question, and it walks text nodes, so it sees a `<button>Save</button>` and
 * misses an icon-only button, an `<input>` and a `<select>` entirely.
 *
 * WHAT THIS ARM STILL CANNOT SEE: dialogs. Like every guard in this set it
 * walks routes with no modal open, so the 516 dialog-side elements above are
 * NOT covered here — they were measured, before and after, with
 * `scripts/measure-modal-typography.mjs`, which force-renders all 82 dialogs.
 * One reset in `index.css` fixes both populations and deleting it turns this
 * arm red immediately, but a dialog-only regression would slip past. Saying so
 * is the point: arm 1 shipped without naming its blind spot and stayed green
 * through 516 mis-sized controls.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WHAT IT MEASURES. The build being SERVED, not the working tree. Against a
 * stale container this reports the stale CSS. Deploy first, or point
 * `E2E_BASE_URL` at a build of the tree under test.
 *
 * EXPECTED STATE: each arm was RED until its reset landed. That is deliberate —
 * the guard is written before the fix so the fix has something to turn green,
 * and so the defect is recorded executably rather than in prose.
 */
import { test, expect } from './fixtures/base'
import {
  PRIMARY_ROUTES,
  captureStorageState,
  goToRoute,
  openApp,
  type StorageState,
} from './fixtures/css-guard'
import type { Page } from '@playwright/test'

/**
 * Controls whose face is allowed to differ from their surroundings.
 *
 * RATCHETED, per the discipline in `route-typography-scale.spec.ts`: the run
 * fails on a NEW violation *and* on a STALE entry whose control now agrees with
 * its surroundings. An allowlist that only ever grows is how a guard stops
 * guarding, so a fixed exception must be deleted here in the same commit.
 *
 * Empty on purpose. No control has yet been shown to need a different face; a
 * monospace input would be the plausible first entry, and it must arrive with a
 * bead explaining why.
 */
const ALLOWED_DIFFERENT_FACE: ReadonlyArray<{ route: string; selector: string; bead: string }> = []

/**
 * Controls allowed to render at the user-agent's own control size.
 *
 * Ratcheted exactly like `ALLOWED_DIFFERENT_FACE`: a NEW violation fails, and a
 * STALE entry — one whose control now renders at a chosen size — fails too,
 * with "delete this entry".
 *
 * Empty on purpose, and hard to imagine an entry for: the UA control size is
 * whatever the client's platform happens to use, so "this control should render
 * at the platform's default" is not a design a stylesheet can express. An entry
 * here would be an admission that a control is unstyled, and would need a bead
 * saying who is going to style it.
 */
const ALLOWED_UA_DEFAULT_SIZE: ReadonlyArray<{ route: string; selector: string; bead: string }> = []

/** One string, one size, one weight — so only the family can move the width. */
const PROBE_STRING = 'Handgloves 12345 WAVE illustrate'
const PROBE_SIZE = '16px'
const PROBE_WEIGHT = '400'

/**
 * Sub-pixel slack when comparing a control's size to the UA default. Far below
 * the 0.33px that separates the UA's 13.3333px from the app's 13px body role,
 * so a fixed control can never read as still-broken.
 */
const UA_SIZE_EPSILON = 0.05

/**
 * Minimum visible, enabled controls a route must yield before its result means
 * anything. Observed lows on this near-empty instance: guide 11, journal 13,
 * settings 15. A route that fails to mount yields zero violations and would
 * otherwise read as a pass — the single most likely way a guard like this dies
 * quietly (same floor discipline as `route-typography-scale.spec.ts`).
 */
const MIN_CONTROLS_PER_ROUTE = 5

interface Mismatch {
  route: string
  tag: string
  className: string
  label: string
  controlFamily: string
  surroundingFamily: string
  controlWidth: number
  surroundingWidth: number
}

interface SizeViolation {
  route: string
  tag: string
  className: string
  label: string
  fontSizePx: number
  uaDefaultPx: number
}

/** Per-tag user-agent control sizes, measured in a CSS-free document. */
type UaSizes = Record<string, number>

/**
 * The user-agent's own control font-sizes, read from an `srcdoc` iframe.
 *
 * A blank `srcdoc` document has no author stylesheet at all — not this app's,
 * not the control reset in `index.css` — so `getComputedStyle` there returns the
 * UA value and nothing else. Doing it per RUN rather than per route keeps it to
 * one measurement; doing it per TAG rather than once keeps it honest, because
 * nothing guarantees a UA sizes `<textarea>` like `<button>` (Chromium happens
 * to give all five 13.3333px, but `<textarea>` already differs in family).
 */
async function measureUaDefaults(page: Page): Promise<UaSizes> {
  return page.evaluate(async () => {
    const frame = document.createElement('iframe')
    frame.setAttribute('aria-hidden', 'true')
    frame.style.cssText = 'position:absolute;left:-9999px;top:0;width:200px;height:200px;border:0'
    frame.srcdoc =
      '<!doctype html><html><body><button>x</button><input><select><option>x</option></select><textarea></textarea></body></html>'
    const loaded = new Promise<void>((resolve) => {
      frame.addEventListener('load', () => resolve(), { once: true })
    })
    document.body.appendChild(frame)
    await loaded
    const doc = frame.contentDocument
    const win = frame.contentWindow
    const out: Record<string, number> = {}
    if (doc && win) {
      for (const tag of ['button', 'input', 'select', 'textarea']) {
        const el = doc.querySelector(tag)
        if (el) out[tag] = Number.parseFloat(win.getComputedStyle(el).fontSize)
      }
    }
    frame.remove()
    return out
  })
}

/**
 * One pass over a route's controls, feeding both arms: the controls whose
 * rendered face disagrees with their nearest text-bearing ancestor, the
 * controls rendering at the user agent's own control size, and the count of
 * controls actually walked (so an unmounted route cannot read as clean).
 *
 * Skips exactly what the sibling guards skip, and for the same reasons: a
 * `.visually-hidden` element is clipped to 1x1 on purpose, a `[disabled]`
 * control is exempt from WCAG 1.4.3, and a zero-size or offscreen node is not
 * something an operator can see. Counting any of them produces findings that
 * are correct behaviour rather than defects — a mistake already made once in
 * this project by an ad-hoc auditor.
 */
const collectScript = (uaSizes: UaSizes, epsilon: number) => `(() => {
  const PROBE_STRING = ${JSON.stringify(PROBE_STRING)};
  const PROBE_SIZE = ${JSON.stringify(PROBE_SIZE)};
  const PROBE_WEIGHT = ${JSON.stringify(PROBE_WEIGHT)};
  const UA_SIZES = ${JSON.stringify(uaSizes)};
  const UA_EPSILON = ${JSON.stringify(epsilon)};

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const widthCache = new Map();

  // Advance width of a fixed string under one family stack. Same face in =>
  // same number out, to the sub-pixel.
  const advance = (family) => {
    if (widthCache.has(family)) return widthCache.get(family);
    ctx.font = PROBE_WEIGHT + ' ' + PROBE_SIZE + ' ' + family;
    const w = Math.round(ctx.measureText(PROBE_STRING).width * 100) / 100;
    widthCache.set(family, w);
    return w;
  };

  const hidden = (el) => {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return true;
    if (parseFloat(cs.opacity) === 0) return true;
    // Clipped-to-nothing utilities (.visually-hidden / .sr-only) are meant to
    // be unreadable; they are not typeface defects.
    if (cs.clip === 'rect(0px, 0px, 0px, 0px)') return true;
    if (cs.clipPath && cs.clipPath !== 'none' && cs.clipPath.includes('inset(50%')) return true;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return true;
    if (r.bottom < 0 || r.right < 0) return true;
    return false;
  };

  // The nearest ancestor that actually carries text, i.e. what the operator
  // reads next to this control. Falls back to <body>, which is where the app's
  // declared family lives.
  const surroundingOf = (el) => {
    let n = el.parentElement;
    while (n && n !== document.documentElement) {
      const ownText = [...n.childNodes].some(
        (c) => c.nodeType === 3 && c.textContent && c.textContent.trim().length > 0
      );
      if (ownText) return n;
      n = n.parentElement;
    }
    return document.body;
  };

  const label = (el) => {
    const t = (el.textContent || '').trim();
    if (t) return t.slice(0, 32);
    const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title');
    return aria ? aria.slice(0, 32) : '[no label]';
  };

  const faceMismatches = [];
  const sizeViolations = [];
  let controlCount = 0;

  for (const el of document.querySelectorAll('button, input, select, textarea')) {
    if (el.disabled) continue;
    if (hidden(el)) continue;

    controlCount += 1;
    const tag = el.tagName.toLowerCase();
    const className = typeof el.className === 'string' ? el.className.trim().slice(0, 60) : '';

    // ARM 2 — size. The UA default for this control's OWN tag, measured this
    // run in a document with no author CSS. Equality within a sub-pixel means
    // nothing in the cascade ever gave this control a size.
    const uaDefault = UA_SIZES[tag];
    const fontSizePx = parseFloat(getComputedStyle(el).fontSize);
    if (typeof uaDefault === 'number' && Math.abs(fontSizePx - uaDefault) < UA_EPSILON) {
      sizeViolations.push({ tag, className, label: label(el), fontSizePx, uaDefaultPx: uaDefault });
    }

    // ARM 1 — face.
    const controlFamily = getComputedStyle(el).fontFamily;
    const surrounding = surroundingOf(el);
    const surroundingFamily = getComputedStyle(surrounding).fontFamily;

    // Identical declarations cannot resolve to different faces; skip the
    // measurement rather than burn canvas calls on them.
    if (controlFamily === surroundingFamily) continue;

    const cw = advance(controlFamily);
    const sw = advance(surroundingFamily);
    if (cw === sw) continue; // different stacks, same resolved face — fine

    faceMismatches.push({
      tag,
      className,
      label: label(el),
      controlFamily,
      surroundingFamily,
      controlWidth: cw,
      surroundingWidth: sw,
    });
  }
  return { faceMismatches, sizeViolations, controlCount };
})()`

test.describe('form controls take the application typeface and a chosen size', () => {
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    storageState = await captureStorageState(browser)
  })

  /**
   * ONE walk, both arms.
   *
   * Not two tests: `fullyParallel` would put them in different workers, each
   * running `beforeAll` and so each logging in, against a login endpoint
   * rate-limited at 5/minute that every other guard in this set is also
   * drawing on (`fixtures/css-guard.ts`, note 1). Both arms assert softly, so
   * one run still reports every category at once and a face failure can never
   * mask a size failure.
   */
  test('every visible control renders in the surrounding face, at a size the app chose', async ({
    browser,
  }) => {
    test.setTimeout(6 * 60 * 1000)

    // One context walked in rail order: each route's stylesheet is appended on
    // first visit and never removed, so a single accumulating session is the
    // realistic cascade. Resetting between routes would hide cross-chunk
    // interference.
    const page = await openApp(browser, storageState, { width: 1600, height: 1000 })
    const mismatches: Mismatch[] = []
    const sizeViolations: SizeViolation[] = []
    const thinRoutes: string[] = []

    try {
      const uaSizes = await measureUaDefaults(page)
      // A UA that reported no control sizes would make arm 2 vacuously green.
      expect(Object.keys(uaSizes).sort(), 'UA control sizes could not be measured').toEqual([
        'button',
        'input',
        'select',
        'textarea',
      ])
      const COLLECT = collectScript(uaSizes, UA_SIZE_EPSILON)

      for (const route of PRIMARY_ROUTES) {
        await goToRoute(page, route)
        const found = (await page.evaluate(COLLECT)) as {
          faceMismatches: Omit<Mismatch, 'route'>[]
          sizeViolations: Omit<SizeViolation, 'route'>[]
          controlCount: number
        }
        for (const f of found.faceMismatches) mismatches.push({ route: route.id, ...f })
        for (const s of found.sizeViolations) sizeViolations.push({ route: route.id, ...s })
        if (found.controlCount < MIN_CONTROLS_PER_ROUTE) {
          thinRoutes.push(
            `  ${route.id}: only ${found.controlCount} visible control(s) (floor ${MIN_CONTROLS_PER_ROUTE}). ` +
              `The route did not render enough to be audited, so a clean result here would be meaningless.`
          )
        }
      }
    } finally {
      await page.context().close()
    }

    expect
      .soft(
        thinRoutes,
        thinRoutes.length === 0
          ? ''
          : `${thinRoutes.length} route(s) rendered too few controls to audit. This guard must never ` +
              `report green because a page failed to mount.\n${thinRoutes.join('\n')}`
      )
      .toEqual([])

    const allowed = (m: Mismatch) =>
      ALLOWED_DIFFERENT_FACE.some(
        (a) => a.route === m.route && m.className.split(/\s+/).includes(a.selector.replace(/^\./, ''))
      )

    const unexpected = mismatches.filter((m) => !allowed(m))

    // STALE arm: an allowlist entry whose control now agrees must be deleted,
    // or the list silently outlives the exception it documents.
    const stale = ALLOWED_DIFFERENT_FACE.filter(
      (a) => !mismatches.some((m) => m.route === a.route && m.className.includes(a.selector.replace(/^\./, '')))
    )

    const describe = (m: Mismatch) =>
      `  ${m.route.padEnd(17)} <${m.tag}> "${m.label}"\n` +
      `      control     ${m.controlWidth}px  ${m.controlFamily}\n` +
      `      surrounding ${m.surroundingWidth}px  ${m.surroundingFamily}`

    expect
      .soft(
        unexpected,
        unexpected.length === 0
          ? ''
          : `${unexpected.length} control(s) render in a different typeface from their surrounding text.\n` +
              `Widths are the advance of a fixed string at ${PROBE_SIZE}/${PROBE_WEIGHT}, so a difference means the\n` +
              `font engine resolved a different face — not merely a different declaration.\n\n` +
              unexpected.map(describe).join('\n') +
              `\n\nThe fix is one declaration in frontend/src/index.css:\n` +
              `  button, input, select, textarea { font-family: inherit; }\n`
      )
      .toEqual([])

    expect
      .soft(
        stale,
        stale.length === 0
          ? ''
          : `${stale.length} ALLOWED_DIFFERENT_FACE entr(ies) no longer correspond to a mismatch. ` +
              `The exception has been fixed — delete the entry:\n` +
              stale.map((s) => `  ${s.route} ${s.selector} (${s.bead})`).join('\n')
      )
      .toEqual([])

    // ── ARM 2: size ───────────────────────────────────────────────────────
    const sizeAllowed = (v: SizeViolation) =>
      ALLOWED_UA_DEFAULT_SIZE.some(
        (a) => a.route === v.route && v.className.split(/\s+/).includes(a.selector.replace(/^\./, ''))
      )

    const unexpectedSizes = sizeViolations.filter((v) => !sizeAllowed(v))

    const staleSizes = ALLOWED_UA_DEFAULT_SIZE.filter(
      (a) =>
        !sizeViolations.some(
          (v) => v.route === a.route && v.className.split(/\s+/).includes(a.selector.replace(/^\./, ''))
        )
    )

    // Signatures, not one line per element: 274 individual rows would bury the
    // shape of the finding, and a signature is what a CSS rule actually moves.
    const bySignature = new Map<string, { count: number; sample: SizeViolation }>()
    for (const v of unexpectedSizes) {
      const key = `${v.route}|${v.tag}${v.className ? `.${v.className.split(/\s+/).join('.')}` : ''}`
      const existing = bySignature.get(key)
      if (existing) existing.count += 1
      else bySignature.set(key, { count: 1, sample: v })
    }
    const signatureLines = [...bySignature.entries()]
      .sort((a, b) => b[1].count - a[1].count)
      .map(
        ([key, { count, sample }]) =>
          `  x${String(count).padStart(3)}  ${key}\n` +
          `        ${sample.fontSizePx}px (UA default for <${sample.tag}> this run) e.g. "${sample.label}"`
      )

    expect
      .soft(
        unexpectedSizes,
        unexpectedSizes.length === 0
          ? ''
          : `${unexpectedSizes.length} control(s) across ${bySignature.size} signature(s) render at the ` +
              `USER-AGENT's control size, i.e. at a size nothing in this application chose. The size was ` +
              `measured this run in a CSS-free iframe, not hardcoded, so it is this client's default and ` +
              `not a number baked into the spec.\n\n` +
              signatureLines.join('\n') +
              `\n\nThe fix is one declaration in frontend/src/index.css, beside the font-family reset:\n` +
              `  button, input, select, textarea { font-size: var(--type-body-size); }\n` +
              `Do NOT use \`inherit\`: body computes to 16px here, so inheriting swaps one unchosen size ` +
              `for another and makes every control louder. If a control needs a different size, give it a ` +
              `P1 role token from the "Typography Roles" group in frontend/src/index.css — a class rule ` +
              `outranks this element rule.\n`
      )
      .toEqual([])

    expect
      .soft(
        staleSizes,
        staleSizes.length === 0
          ? ''
          : `${staleSizes.length} ALLOWED_UA_DEFAULT_SIZE entr(ies) no longer correspond to a control at ` +
              `the UA default. The exception has been fixed — delete the entry:\n` +
              staleSizes.map((s) => `  ${s.route} ${s.selector} (${s.bead})`).join('\n')
      )
      .toEqual([])
  })
})
