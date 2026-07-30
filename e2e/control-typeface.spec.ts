/**
 * Form controls must render in the same typeface as the text around them.
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
 * WHAT IT MEASURES. The build being SERVED, not the working tree. Against a
 * stale container this reports the stale CSS. Deploy first, or point
 * `E2E_BASE_URL` at a build of the tree under test.
 *
 * EXPECTED STATE: RED until the reset lands. That is deliberate — the guard was
 * written before the fix so the fix has something to turn green, and so the
 * defect is recorded executably rather than in prose.
 */
import { test, expect } from './fixtures/base'
import {
  PRIMARY_ROUTES,
  captureStorageState,
  goToRoute,
  openApp,
  type StorageState,
} from './fixtures/css-guard'

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

/** One string, one size, one weight — so only the family can move the width. */
const PROBE_STRING = 'Handgloves 12345 WAVE illustrate'
const PROBE_SIZE = '16px'
const PROBE_WEIGHT = '400'

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

/**
 * Collect controls whose rendered face disagrees with their nearest
 * text-bearing ancestor.
 *
 * Skips exactly what the sibling guards skip, and for the same reasons: a
 * `.visually-hidden` element is clipped to 1x1 on purpose, a `[disabled]`
 * control is exempt from WCAG 1.4.3, and a zero-size or offscreen node is not
 * something an operator can see. Counting any of them produces findings that
 * are correct behaviour rather than defects — a mistake already made once in
 * this project by an ad-hoc auditor.
 */
const COLLECT = `(() => {
  const PROBE_STRING = ${JSON.stringify(PROBE_STRING)};
  const PROBE_SIZE = ${JSON.stringify(PROBE_SIZE)};
  const PROBE_WEIGHT = ${JSON.stringify(PROBE_WEIGHT)};

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

  const out = [];
  for (const el of document.querySelectorAll('button, input, select, textarea')) {
    if (el.disabled) continue;
    if (hidden(el)) continue;

    const controlFamily = getComputedStyle(el).fontFamily;
    const surrounding = surroundingOf(el);
    const surroundingFamily = getComputedStyle(surrounding).fontFamily;

    // Identical declarations cannot resolve to different faces; skip the
    // measurement rather than burn canvas calls on them.
    if (controlFamily === surroundingFamily) continue;

    const cw = advance(controlFamily);
    const sw = advance(surroundingFamily);
    if (cw === sw) continue; // different stacks, same resolved face — fine

    out.push({
      tag: el.tagName.toLowerCase(),
      className: typeof el.className === 'string' ? el.className.trim().slice(0, 60) : '',
      label: label(el),
      controlFamily,
      surroundingFamily,
      controlWidth: cw,
      surroundingWidth: sw,
    });
  }
  return out;
})()`

test.describe('form controls inherit the application typeface', () => {
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    storageState = await captureStorageState(browser)
  })

  test('every visible control renders in the same face as its surrounding text', async ({ browser }) => {
    // One context walked in rail order: each route's stylesheet is appended on
    // first visit and never removed, so a single accumulating session is the
    // realistic cascade. Resetting between routes would hide cross-chunk
    // interference.
    const page = await openApp(browser, storageState, { width: 1600, height: 1000 })
    const mismatches: Mismatch[] = []

    try {
      for (const route of PRIMARY_ROUTES) {
        await goToRoute(page, route)
        const found = (await page.evaluate(COLLECT)) as Omit<Mismatch, 'route'>[]
        for (const f of found) mismatches.push({ route: route.id, ...f })
      }
    } finally {
      await page.context().close()
    }

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
  })
})
