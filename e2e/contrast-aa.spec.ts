/**
 * Every visible text node on every route clears WCAG AA contrast, in all three
 * themes, at both supported viewports.
 *
 * WHY THIS EXISTS. Every contrast figure this project has ever quoted came from
 * an ephemeral auditor that no longer exists — a throwaway script, run once,
 * its numbers copied into a bead. Bead `enhancedchannelmanager-dlavh` moved the
 * live failure count from 140 to 34 that way and nothing has been able to
 * confirm it since. Worse, three of the figures in that bead's own description
 * turned out to be wrong and had to be retracted in a follow-up comment. An
 * unrepeatable measurement is not evidence, and a remediation nobody can re-run
 * is a claim, not a guarantee. This spec is the repeatable measurement.
 *
 * WHAT ALREADY EXISTED, AND WHY IT IS NOT ENOUGH. `e2e/operator-shell.spec.ts`
 * has two contrast tests (`Channel Manager group text ...`, `Dashboard and
 * header status ...`), both 2 viewports x 3 themes. They are SELECTOR LISTS —
 * nine to eleven named selectors on two routes. A component nobody thought to
 * add is invisible to them, which is exactly how the five failing
 * `.dashboard-card-icon` glyphs survived on the route that test walks. This
 * spec is the complement: a text-node WALK across all eleven routes, so a
 * brand-new component cannot slip past by not being on anybody's list.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * HOW IT MEASURES, AND WHY EACH STEP IS THERE
 *
 * Every arm below exists because its absence produced a confidently wrong
 * number that somebody then acted on.
 *
 * 1. THE COMPOSITOR RESOLVES THE COLOUR, NOT A REGEX.
 *    `getComputedStyle(el).backgroundColor` for
 *    `color-mix(in srgb, var(--bg-secondary) 94%, transparent)` serialises as
 *    `color(srgb 0.960784 0.960784 0.960784 / 0.94)`. An `rgba()`-only regex
 *    returns nothing for it and the layer gets treated as transparent; a
 *    "grab all the numbers" regex reads those 0-1 floats as 0-255 channels and
 *    calls a near-white surface near-BLACK. Both mistakes are live in this
 *    repo's history: the first under-read the sticky-nav pill backdrop as
 *    #f0f0ff instead of the #e7e8f7 that is actually painted (reporting 3.96
 *    where the truth is 3.68), and the second is still latent in
 *    `computedTextContrast` in `operator-shell.spec.ts`, harmless only because
 *    none of its listed selectors currently sits under a `color-mix` layer.
 *    So no colour is parsed here at all. Each one is handed to a 1x1 canvas and
 *    the pixel is read back, which means the engine that painted the page is
 *    the thing that resolves the syntax — `color()`, `oklch()`, `color-mix()`,
 *    and whatever ships next. This is the colour-space analogue of
 *    `control-typeface.spec.ts` measuring the rendered face instead of
 *    comparing `font-family` declarations: resolve it, do not read it.
 *
 * 2. THE BACKGROUND IS THE WHOLE ANCESTOR CHAIN, IN ORDER.
 *    Reading the parent's `background-color` alone is the bug that made an
 *    earlier auditor report every primary button as white-on-page. A button
 *    paints its own opaque fill and a translucent tint three ancestors up has
 *    to be composited in sequence.
 *
 * 3. `opacity` IS A GROUP MULTIPLIER, NOT A TEXT PROPERTY.
 *    An element with `opacity < 1` fades its entire subtree render — its own
 *    background and its text alike — onto whatever is behind it. That is why
 *    the Stats 64px `tv_off` illustration lands at 2.13 and not at the 4.2 its
 *    declared `color` alone would suggest.
 *
 * 4. NOT-ACTUALLY-VISIBLE IS SKIPPED, AND THAT IS NOT A LOOPHOLE.
 *    `.visually-hidden` is a 1x1 clipped box. A naive walker reports it as
 *    failing, which is correct behaviour being mis-reported — it cost 28
 *    phantom findings in one session. Same for zero-size, offscreen,
 *    `visibility: hidden`, `opacity: 0`, and `[disabled]` subtrees, which
 *    WCAG 1.4.3 exempts and which this app dims with an `opacity` that would
 *    otherwise read as a defect. `e2e/sr-only-hidden.spec.ts` is what keeps
 *    the screen-reader utilities genuinely hidden; this spec trusts it.
 *
 * 5. WHAT IT CANNOT MEASURE, IT REFUSES TO PASS.
 *    A `background-image` (gradient, texture) in the chain cannot be reduced
 *    to one colour, and a colour the engine will not resolve cannot be
 *    composited. Both are reported as UNRESOLVED and both fail the run. There
 *    are zero of either today. If one appears, the guard must be taught to
 *    handle it — silently skipping it is how a guard starts lying.
 *
 * FLOORS. WCAG 1.4.3: 4.5:1 normal text, 3:1 "large scale" (18pt/24px, or
 * 14pt/18.66px when bold). WCAG 1.4.11: 3:1 for non-text content, which is what
 * an icon-font glyph is.
 *
 * VIEWPORTS. 1280x720 and 1920x1080. 1280x720 is the PO's stated minimum
 * supported viewport, and it is not redundant with 1920: the sticky section nav
 * changes placement at 1100px and 850px, and a route that reflows can change
 * which surface a label sits on.
 *
 * WHAT IT MEASURES: the build being SERVED, not the working tree. Against a
 * stale container this reports the stale CSS. Deploy first, or point
 * `E2E_BASE_URL` at a build of the tree under test.
 *
 * WHY THE CONTEXT IS WARM, unlike `sr-only-hidden.spec.ts`. Route stylesheets
 * are appended to <head> on first visit and never removed, so by the last route
 * every chunk's CSS is live. That is the state a real operator's session is in
 * after a few clicks, and it is the state in which a cross-chunk override can
 * change a colour. A cold-per-route walk would measure a session nobody has.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WHY THERE IS AN ALLOWLIST, AND WHY IT CANNOT ROT
 *
 * Same discipline as `e2e/route-typography-scale.spec.ts`. Four arms, and the
 * last three are the load-bearing ones:
 *
 *   NEW        a failing site matching no entry                    -> FAIL
 *   STALE      an entry whose sites all now clear their floor      -> FAIL
 *                                                                    (delete it)
 *   MISSING    an entry matching nothing, not `dataDependent`      -> FAIL
 *   REGRESSED  a matched site that got WORSE than its recorded
 *              ratio by more than RATIO_TOLERANCE                  -> FAIL
 *
 * STALE means fixing a site REQUIRES deleting its entry in the same commit.
 * REGRESSED means an entry is a ceiling, not a blank cheque: an allowed 2.13
 * cannot quietly drift to 1.4. Every entry names an OPEN bead — an entry
 * pointing at a closed bead is an entry nobody owns.
 *
 * NOT VACUOUS. Every route asserts a floor on how many text elements it
 * measured. Without it a route that failed to mount contributes zero failures
 * and reads as a pass, which is the likeliest way a guard like this dies
 * quietly.
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
  type Theme,
} from './fixtures/css-guard'
import type { Page } from '@playwright/test'

/** WCAG 1.4.3 / 1.4.11 floors. */
const FLOORS = { normal: 4.5, large: 3.0, glyph: 3.0 } as const

/**
 * Slack on a ratio comparison. The canvas composites at 8 bits per layer, the
 * same as the screen, so a five-layer chain can differ from infinite-precision
 * arithmetic in the second decimal. Nowhere near enough to swallow a real
 * failure: the smallest gap this guard has to resolve is 4.20 against 4.5.
 */
const RATIO_EPSILON = 0.05

/** How far a known exception may drift before it counts as a regression. */
const RATIO_TOLERANCE = 0.15

/**
 * Subtrees that are not visible text. Screen-reader utilities are 1x1 clipped
 * boxes by design and `e2e/sr-only-hidden.spec.ts` is what proves they stay
 * that way — measuring them here would report correct behaviour as a defect.
 */
const EXCLUDED_ANCESTORS: readonly string[] = ['.sr-only', '.visually-hidden']

/**
 * The eleven routes. `PRIMARY_ROUTES` deliberately omits Dashboard because it
 * is the eager landing route rather than a lazily-loaded tab, so — as
 * `fixtures/css-guard.ts` puts it — the guards that care about it name it
 * explicitly. This one cares: five of its glyphs are the largest cluster of
 * known exceptions below.
 */
const DASHBOARD: RouteSpec = { id: 'dashboard', root: '.operator-dashboard' }
const ROUTES: readonly RouteSpec[] = [DASHBOARD, ...PRIMARY_ROUTES]

/** The PO's minimum supported viewport, then the common desktop size. */
const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
] as const

/**
 * Minimum text elements a route must yield before its result means anything.
 * Observed lows on this near-empty instance: channel-pipeline 66,
 * settings 68, stats 84. 25 is far under every one and far over "did not mount".
 */
const MIN_TEXT_ELEMENTS = 25

/**
 * Freeze every transition and animation before measuring anything.
 *
 * NOT cosmetic — this spec produced a false positive without it. Guide's
 * refresh button dims to `opacity: .6` while `:disabled` and carries
 * `transition: opacity 0.15s`. Caught in the 150ms after its `disabled`
 * attribute was removed, it no longer matched the `[disabled]` exemption but
 * its computed opacity was still animating through 0.6, and black-on-yellow at
 * 0.6 reads 3.90 against 4.5. At rest the same button measures `opacity: 1` and
 * passes — verified directly. An animating value is not a state any user is
 * asked to read, and a guard that samples one reports a defect that does not
 * exist.
 *
 * `!important` because route chunk stylesheets are appended to <head> AFTER
 * this tag on first visit, so ordinary specificity would let them win.
 */
const FREEZE_ANIMATION = `
*, *::before, *::after {
  transition: none !important;
  animation: none !important;
}`

/**
 * Elements that only mount on data this instance does not have, injected so
 * they are measured rather than silently skipped. `.notification-badge` renders
 * only when `displayUnreadCount > 0` (`NotificationCenter.tsx`), and it is the
 * canonical white-on-`--error` fill — precisely the pairing bead
 * `enhancedchannelmanager-4l51b` is about, so leaving it unmeasured would leave
 * that bead's whole subject unguarded.
 *
 * Mounted and removed INSIDE the single measuring `page.evaluate`, so React
 * cannot interleave a commit and the app never observes the mutation.
 */
const FORCE_RENDER: ReadonlyArray<{ parent: string; html: string }> = [
  { parent: '.notification-bell', html: '<span class="notification-badge">7</span>' },
]

interface Exception {
  /** Route hash id, or omitted when the site renders on every route. */
  route?: string
  /** Selector matching the offending element itself, not an ancestor. */
  selector: string
  /**
   * Worst permitted ratio per theme. A theme absent from this map is NOT
   * allowed to fail: the site failing there is a NEW violation.
   */
  worst: Partial<Record<Theme, number>>
  /** OPEN bead that owns the fix. A closed bead is an entry nobody owns. */
  bead: string
  /** Why it fails, and what is expected to happen to it. */
  reason: string
  /**
   * The site's presence depends on the instance's data — either it needs
   * records to render, or (empty states) it needs their absence. Staleness
   * cannot be judged for these, so a selector matching nothing is tolerated,
   * and only for these. A documented admission, not a hiding place.
   */
  dataDependent?: boolean
}

/**
 * SEEDED FROM A LIVE MEASUREMENT of the served build at both viewports in all
 * three themes — never from a hand-written list. Every ratio below was observed;
 * nothing is carried on trust.
 *
 * Entries deliberately NOT here, each verified absent rather than assumed:
 *   - `.sticky-section-nav button[aria-current="location"]` (Stats + Settings)
 *     and `.settings-nav-item.active .navigation-label` — the three sites bead
 *     `enhancedchannelmanager-dlavh` reported. Fixed in the same change that
 *     added this spec by retoning light `--accent-secondary` #6366f1 -> #3e41ee;
 *     measured 3.68 and 3.25 before, 5.41 and 4.79 after, with dark (10.47 /
 *     9.76) and high-contrast (15.31 / 14.04) untouched. They are the proof this
 *     guard goes red on a real defect rather than being decoration.
 *   - `.failed-streams-alert` on Channel Manager. It WAS added here, at a
 *     measured 4.40 light / 3.72 high-contrast — and the STALE arm rejected it
 *     on the next run. Both readings were taken 180ms after a theme switch,
 *     inside a `background-color` transition; at rest the same badge composites
 *     `--danger-text` #c21111 over #f3dcd9 and measures 4.75, which passes. The
 *     entry was deleted and `FREEZE_ANIMATION` added. Recorded because it is the
 *     clearest demonstration available that the ratchet catches a wrong entry —
 *     including the author's own — and not just a fixed one.
 */
const ALLOWLIST: readonly Exception[] = [
  // ── The selected route in the primary rail, on EVERY route ──────────────
  // Found by this guard, not reported to it. `.navigation-destination.active`
  // (TabNavigation.css) paints `color: var(--accent-primary)` on
  // `color-mix(in srgb, var(--accent-primary) 14%, transparent)` — the text and
  // its own backdrop are the same token, so darkening the token darkens both
  // and buys much less than it appears to. Light measures 4.20 against 4.5.
  // Dark (#ffffff) and high-contrast (#ffff00) pass comfortably.
  //
  // NOT fixed here, deliberately. Thinning the tint to 9% clears it (4.53) but
  // changes the selected-row wash in all three themes, and the primary rail is
  // frozen chrome settled by beads `-57pp3` and `-eupzi` and pinned by
  // `e2e/frozen-chrome.spec.ts`. Retoning `--accent-primary` instead is not
  // available: 359 uses, 22 of them solid fills, it feeds `--button-primary-bg`,
  // and fourteen declarations spell `var(--accent-primary)33`, which only parses
  // while the value stays a six-digit hex. That is a look change and a
  // blast-radius decision for the PO, not a drive-by.
  //
  // `:not(.settings-nav-item)` is load-bearing. Inside the Settings drill-in the
  // same anchor carries BOTH classes, and `SettingsTab.css` wins the background
  // with `--accent-10` and the colour with `--accent-secondary` — a different
  // token on a different surface, fixed by the light `--accent-secondary` retone
  // in this same change. Without the exclusion this entry claimed that site too,
  // and the guard's own REGRESSED arm reported it at 3.25 against the recorded
  // 4.20. That is the ratchet working: an over-broad selector was caught by the
  // spec rather than by a reviewer.
  {
    selector: '.navigation-destination.active:not(.settings-nav-item) .navigation-label',
    worst: { light: 4.2 },
    bead: 'enhancedchannelmanager-eo7er',
    reason:
      'Selected rail row: --accent-primary #4f46e5 on a 14% tint of itself over --bg-tertiary ' +
      '(#d2d1e7) = 4.20 vs 4.5. Renders on all ten non-Settings routes. Fix is either a 9% tint ' +
      '(4.53) or a retone of --accent-primary; both need PO sign-off because the rail is frozen ' +
      'chrome pinned by e2e/frozen-chrome.spec.ts.',
  },

  // ── Dashboard per-card identity glyphs ─────────────────────────────────
  // `.dashboard-card-icon` paints `color: var(--dashboard-accent)` on
  // `color-mix(in srgb, var(--dashboard-accent) 16%, transparent)` — again a
  // glyph on a tint of itself, which caps the achievable ratio low no matter
  // which accent is chosen. Five of the six cards fail the 3:1 glyph floor in
  // light: amber 1.75, cyan 1.92, emerald 2.01, slate 2.07, blue 2.81; violet
  // clears it. `OperatorDashboard.css` argues in its own comment that the
  // per-card accent is "decorative only — every card states its meaning in
  // text", which is the 1.4.11 exemption argument. It is recorded here rather
  // than assumed: the exemption is the PO's call, not the CSS comment's.
  {
    route: 'dashboard',
    selector: '.dashboard-card-icon .material-icons',
    worst: { light: 1.75 },
    bead: 'enhancedchannelmanager-eo7er',
    reason:
      'Per-card identity glyph on a 16% tint of its own colour; 5 of 6 fail the 3:1 glyph floor in ' +
      'light (worst amber #f59e0b at 1.75). OperatorDashboard.css argues they are decorative and ' +
      'that every card states its meaning in text — 1.4.11 exemption, pending PO confirmation.',
  },

  // ── Stats empty-state illustration ─────────────────────────────────────
  // A 64px `tv_off` at `opacity: .5`. The opacity is what sinks it: the
  // declared colour clears the floor on its own and the group fade is what
  // takes it under. Purely decorative — `.no-streams` states the condition in
  // text beside it.
  {
    route: 'stats',
    selector: '.no-streams .material-icons',
    worst: { dark: 1.78, light: 2.13 },
    bead: 'enhancedchannelmanager-eo7er',
    reason:
      '64px decorative empty-state illustration; `opacity: .5` takes it under even the 3:1 glyph ' +
      'floor (dark 1.78, light 2.13; high-contrast passes). The adjacent text carries the meaning.',
    // Data-dependent in the INVERSE direction: it renders only while the
    // instance has no streams. On a populated instance it is legitimately
    // absent, which must not read as a rotted selector.
    dataDependent: true,
  },

  // ── Channel Manager per-group probe affordance ─────────────────────────
  // Also found by this guard rather than reported to it, and the same defect
  // SHAPE as the Stats illustration: a `.material-icons` glyph dimmed by
  // `opacity: .5` while idle. Unlike that one this is an interactive control's
  // only icon, so the 1.4.11 exemption argument is much weaker — it fails in
  // ALL THREE themes, which is why it is worth its own line rather than being
  // folded into the entry above.
  {
    route: 'channel-manager',
    selector: '.probe-group-btn .material-icons',
    worst: { dark: 1.56, light: 2.1, 'high-contrast': 2.25 },
    bead: 'enhancedchannelmanager-eo7er',
    reason:
      'Idle per-group probe button glyph at `opacity: .5`: dark 1.56, light 2.10, high-contrast ' +
      '2.25, all against 3.0. Same shape as the Stats illustration but on an interactive control, ' +
      'so the decorative exemption does not obviously apply.',
    dataDependent: true,
  },
]

interface Site {
  ratio: number
  floor: number
  kind: 'normal' | 'large' | 'glyph'
  pass: boolean
  fg: string
  bg: string
  declaredColor: string
  fontSizePx: number
  fontWeight: string
  opacity: number
  signature: string
  sample: string
  /** Indices into the entry list passed in, for whichever entries claim it. */
  claimedBy: number[]
}

interface Unresolvable {
  why: string
  signature: string
  sample: string
}

interface Scan {
  measured: number
  unresolved: Unresolvable[]
  sites: Site[]
  /** entry index -> how many elements it matched at all. */
  matched: number[]
}

async function scanRoute(
  page: Page,
  entries: ReadonlyArray<{ selector: string }>
): Promise<Scan> {
  return page.evaluate(
    ({ floors, excluded, epsilon, selectors, forceRender }) => {
      /* ─── colour: let the compositor do it (see header note 1) ─────────── */
      const canvas = document.createElement('canvas')
      canvas.width = 1
      canvas.height = 1
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!

      /**
       * True when the engine accepted `value` as a colour. `fillStyle` silently
       * KEEPS ITS PREVIOUS VALUE on an unparseable string, so one assignment
       * cannot distinguish "accepted" from "ignored". Two sentinels can.
       */
      const accepts = (value: string) => {
        ctx.fillStyle = '#010203'
        ctx.fillStyle = value
        const first = ctx.fillStyle
        ctx.fillStyle = '#040506'
        ctx.fillStyle = value
        return first === ctx.fillStyle
      }
      const read = () => {
        const d = ctx.getImageData(0, 0, 1, 1).data
        return { r: d[0], g: d[1], b: d[2] }
      }
      const chan = (c: number) => {
        const v = c / 255
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
      }
      type Channels = { r: number; g: number; b: number }
      const lum = (c: Channels) => 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b)
      const contrast = (x: Channels, y: Channels) => {
        const a = lum(x)
        const b = lum(y)
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
      }
      const hex = (c: Channels) =>
        '#' + [c.r, c.g, c.b].map((v) => v.toString(16).padStart(2, '0')).join('')

      /* ─── visibility (see header note 4) ──────────────────────────────── */
      const vw = window.innerWidth
      const vh = window.innerHeight
      const isClipped = (cs: CSSStyleDeclaration) => {
        if (cs.clip && cs.clip !== 'auto' && /rect\(/.test(cs.clip)) return true
        if (cs.clipPath && cs.clipPath !== 'none' && /inset\(\s*(?:100%|50%)/.test(cs.clipPath)) return true
        return false
      }
      const readable = (el: Element) => {
        const rect = el.getBoundingClientRect()
        if (rect.width < 1 || rect.height < 1) return false
        if (rect.bottom <= 0 || rect.right <= 0 || rect.left >= vw || rect.top >= vh) return false
        for (let n: Element | null = el; n && n !== document.documentElement; n = n.parentElement) {
          const cs = getComputedStyle(n)
          if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false
          if (Number.parseFloat(cs.opacity) === 0) return false
          if (isClipped(cs)) return false
          if (n.matches('[disabled], [aria-disabled="true"], .disabled')) return false
          if (excluded.some((sel) => n.matches(sel))) return false
        }
        return true
      }

      /* ─── the composited stacking chain (see header notes 2 and 3) ─────── */
      const chainOf = (el: Element) => {
        const layers: Array<{ colour: string; groupAlpha: number }> = []
        let cumulative = 1
        let gradient: string | null = null
        for (let n: Element | null = el; n; n = n.parentElement) {
          const cs = getComputedStyle(n)
          const op = Number.parseFloat(cs.opacity)
          cumulative *= Number.isFinite(op) ? op : 1
          if (!gradient && cs.backgroundImage && cs.backgroundImage !== 'none') {
            gradient = `${n.tagName.toLowerCase()}: ${cs.backgroundImage.slice(0, 48)}`
          }
          layers.push({ colour: cs.backgroundColor, groupAlpha: cumulative })
          if (n === document.documentElement) break
        }
        return { layers, selfOpacity: cumulative, gradient }
      }

      /**
       * Paint root->element into the canvas and read the pixel back.
       * `globalAlpha` multiplies the source alpha, so one `fillRect` per layer
       * at that layer's cumulative group opacity reproduces what the
       * compositor did, without this code doing any alpha arithmetic.
       */
      const paint = (layers: Array<{ colour: string; groupAlpha: number }>) => {
        ctx.globalAlpha = 1
        // The CSS initial canvas surface. `html`/`body` paint over it on every
        // route here, so it only shows through where nothing paints at all.
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, 1, 1)
        for (let i = layers.length - 1; i >= 0; i -= 1) {
          const { colour, groupAlpha } = layers[i]
          if (!accepts(colour)) return null
          ctx.globalAlpha = groupAlpha
          ctx.fillStyle = colour
          ctx.fillRect(0, 0, 1, 1)
        }
        ctx.globalAlpha = 1
        return read()
      }

      /* ─── which floor applies ─────────────────────────────────────────── */
      const HAS_WORD = /[\p{L}\p{N}]/u
      const floorFor = (cs: CSSStyleDeclaration, text: string, el: Element) => {
        const size = Number.parseFloat(cs.fontSize)
        const weight = Number.parseInt(cs.fontWeight, 10) || 400
        const cls = el.getAttribute('class') || ''
        const iconFont =
          /material[\s-]?(icons|symbols)/i.test(cs.fontFamily) ||
          /(^|\s)material-(icons|symbols)[\w-]*(\s|$)/.test(cls)
        if (iconFont || !HAS_WORD.test(text)) return { floor: floors.glyph, kind: 'glyph' as const }
        if (size >= 24 || (size >= 18.66 && weight >= 700)) return { floor: floors.large, kind: 'large' as const }
        return { floor: floors.normal, kind: 'normal' as const }
      }

      const signature = (el: Element) => {
        const parts: string[] = []
        for (let n: Element | null = el; n && n !== document.body && parts.length < 5; n = n.parentElement) {
          const cls = (n.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3).join('.')
          parts.unshift(n.tagName.toLowerCase() + (cls ? '.' + cls : ''))
        }
        return parts.join(' > ')
      }

      /* ─── mount the data-dependent elements (see FORCE_RENDER) ─────────── */
      const injected: Element[] = []
      for (const { parent, html } of forceRender) {
        const host = document.querySelector(parent)
        if (!host) continue
        const wrapper = document.createElement('template')
        wrapper.innerHTML = html
        const child = wrapper.content.firstElementChild
        if (!child) continue
        // Already mounted for real (the instance does have the data): measure
        // the real one and inject nothing, so the two never double up.
        const classes = Array.from(child.classList)
        if (classes.length && host.querySelector('.' + classes.join('.'))) continue
        host.appendChild(child)
        injected.push(child)
      }

      try {
        /* ─── which elements each allowlist entry claims ─────────────────── */
        const claims = new Map<Element, number[]>()
        const matched = selectors.map(() => 0)
        selectors.forEach((entry, index) => {
          let hits: Element[]
          try {
            hits = Array.from(document.querySelectorAll(entry.selector))
          } catch {
            return // malformed selector surfaces as MISSING, not as an abort
          }
          for (const el of hits) {
            if (!readable(el)) continue
            matched[index] += 1
            const list = claims.get(el)
            if (list) list.push(index)
            else claims.set(el, [index])
          }
        })

        /* ─── walk ────────────────────────────────────────────────────────
           A text-node walk, not a selector list, so a brand-new component
           cannot slip past by not being on anybody's list. */
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
        const seen = new Set<Element>()
        const sites = []
        const unresolved = []
        let measured = 0
        let node: Node | null
        while ((node = walker.nextNode())) {
          const text = (node.nodeValue || '').trim()
          if (!text) continue
          const el = node.parentElement
          if (!el || seen.has(el)) continue
          seen.add(el)
          if (!readable(el)) continue

          const cs = getComputedStyle(el)
          const { layers, selfOpacity, gradient } = chainOf(el)
          if (gradient) {
            unresolved.push({
              why: 'a background-image in the ancestor chain cannot be reduced to one colour: ' + gradient,
              signature: signature(el),
              sample: text.slice(0, 40),
            })
            continue
          }
          const bg = paint(layers)
          if (!bg) {
            unresolved.push({
              why: 'a background-color in the ancestor chain was not a colour the engine would resolve',
              signature: signature(el),
              sample: text.slice(0, 40),
            })
            continue
          }
          if (!accepts(cs.color)) {
            unresolved.push({
              why: 'the computed color did not resolve: ' + cs.color,
              signature: signature(el),
              sample: text.slice(0, 40),
            })
            continue
          }
          ctx.globalAlpha = selfOpacity
          ctx.fillStyle = cs.color
          ctx.fillRect(0, 0, 1, 1)
          ctx.globalAlpha = 1
          const fg = read()

          const { floor, kind } = floorFor(cs, text, el)
          const ratio = Math.round(contrast(fg, bg) * 100) / 100
          measured += 1
          sites.push({
            ratio,
            floor,
            kind,
            pass: ratio >= floor - epsilon,
            fg: hex(fg),
            bg: hex(bg),
            declaredColor: cs.color,
            fontSizePx: Number.parseFloat(cs.fontSize),
            fontWeight: cs.fontWeight,
            opacity: Math.round(selfOpacity * 1000) / 1000,
            signature: signature(el),
            sample: text.slice(0, 46),
            claimedBy: claims.get(el) || [],
          })
        }
        return { measured, unresolved, sites, matched }
      } finally {
        for (const el of injected) el.remove()
      }
    },
    {
      floors: { ...FLOORS },
      excluded: [...EXCLUDED_ANCESTORS],
      epsilon: RATIO_EPSILON,
      selectors: entries.map((entry) => ({ selector: entry.selector })),
      forceRender: FORCE_RENDER.map((entry) => ({ ...entry })),
    }
  ) as Promise<Scan>
}

/** Per-entry tallies accumulated across every route, theme and viewport. */
interface EntryTally {
  /** Elements this entry's selector matched anywhere. 0 means MISSING. */
  matched: number
  /**
   * theme -> worst ratio observed on a FAILING element this entry claimed. A
   * theme the entry lists but that never appears here has been fixed, which is
   * the STALE arm — there is no need to record passes separately.
   */
  failing: Partial<Record<Theme, number>>
}

test.describe('every route clears WCAG AA contrast in every theme at both viewports', () => {
  // One login for the whole file — `backend/auth/routes.py` rate-limits the
  // login endpoint at 5/minute and this file opens a context per viewport.
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(4 * 60 * 1000)
    storageState = await captureStorageState(browser)
  })

  test('all eleven routes, three themes, two viewports: no new violation, no stale exception', async ({
    browser,
  }) => {
    // 2 viewports x 11 lazily-loaded routes, each with a networkidle wait, and
    // three theme passes per route. Known-long: no arbitrary timeout on it.
    test.setTimeout(25 * 60 * 1000)

    const newViolations: string[] = []
    const thinRoutes: string[] = []
    const unresolvable: string[] = []
    const regressions: string[] = []
    const tallies: EntryTally[] = ALLOWLIST.map(() => ({ matched: 0, failing: {} }))

    for (const viewport of VIEWPORTS) {
      const page = await openApp(browser, storageState, viewport)
      // Once per context: hash navigation never reloads, so this survives every
      // route change and keeps applying as later chunks append their own CSS.
      await page.addStyleTag({ content: FREEZE_ANIMATION })
      try {
        for (const route of ROUTES) {
          // Navigate ONCE per route and switch themes in place: a theme is an
          // attribute on <html>, so re-navigating per theme would triple the
          // chunk loads to measure exactly the same DOM.
          await goToRoute(page, route)
          const entries = ALLOWLIST.filter((entry) => !entry.route || entry.route === route.id)

          for (const theme of THEMES) {
            await setTheme(page, theme)
            const scan = await scanRoute(page, entries)
            const where = `${route.id} / ${theme} / ${viewport.width}x${viewport.height}`

            if (scan.measured < MIN_TEXT_ELEMENTS) {
              thinRoutes.push(
                `  ${where}: only ${scan.measured} measurable text element(s) (floor ${MIN_TEXT_ELEMENTS}). ` +
                  `The route did not render enough to be audited, so a clean result here would be meaningless.`
              )
            }

            for (const item of scan.unresolved) {
              unresolvable.push(`  ${where}\n      ${item.why}\n      ${item.signature}\n      "${item.sample}"`)
            }

            entries.forEach((entry, localIndex) => {
              tallies[ALLOWLIST.indexOf(entry)].matched += scan.matched[localIndex]
            })

            for (const site of scan.sites) {
              if (site.pass) continue
              const owners = site.claimedBy
                .map((localIndex) => ALLOWLIST.indexOf(entries[localIndex]))
                .filter((globalIndex) => globalIndex >= 0)

              // A failing site is only excused by an entry that permits this
              // THEME. An entry that lists `light` does not licence a dark
              // failure — that is a different defect wearing the same selector.
              const excusing = owners.filter((globalIndex) => ALLOWLIST[globalIndex].worst[theme] !== undefined)
              if (excusing.length === 0) {
                newViolations.push(
                  `  ${where}  ${site.ratio.toFixed(2)} vs ${site.floor} (${site.kind}` +
                    `${site.opacity < 1 ? `, opacity ${site.opacity}` : ''})\n` +
                    `      ${site.fg} on ${site.bg}   declared ${site.declaredColor}   ` +
                    `${site.fontSizePx}px/${site.fontWeight}\n` +
                    `      ${site.signature}\n` +
                    `      "${site.sample}"`
                )
                continue
              }
              for (const globalIndex of excusing) {
                const tally = tallies[globalIndex]
                const seenWorst = tally.failing[theme]
                if (seenWorst === undefined || site.ratio < seenWorst) tally.failing[theme] = site.ratio
                const permitted = ALLOWLIST[globalIndex].worst[theme] as number
                if (site.ratio < permitted - RATIO_TOLERANCE) {
                  regressions.push(
                    `  ${where}  ${ALLOWLIST[globalIndex].selector}\n` +
                      `      GOT WORSE: ${site.ratio.toFixed(2)}, but the entry records ${permitted.toFixed(2)}.\n` +
                      `      An allowlist entry is a ceiling, not a blank cheque. Either this is a new\n` +
                      `      regression to fix, or the entry needs re-measuring with a note saying why.\n` +
                      `      bead ${ALLOWLIST[globalIndex].bead}`
                  )
                }
              }
            }
          }
        }
      } finally {
        await page.context().close()
      }
    }

    const staleEntries: string[] = []
    ALLOWLIST.forEach((entry, index) => {
      const tally = tallies[index]
      if (tally.matched === 0) {
        // Nothing matched anywhere. Expected for a data-dependent site on a
        // near-empty instance; for anything else the selector has rotted.
        if (entry.dataDependent) return
        staleEntries.push(
          `  ${entry.route ?? '(all routes)'}  ${entry.selector}\n` +
            `      MATCHED NOTHING at either viewport in any theme. The entry no longer describes a\n` +
            `      rendered element — either the site was removed, or the selector rotted. Delete the\n` +
            `      entry, or fix the selector.\n` +
            `      bead ${entry.bead}: ${entry.reason}`
        )
        return
      }
      const stillFailing = (Object.keys(entry.worst) as Theme[]).filter((theme) => tally.failing[theme] !== undefined)
      if (stillFailing.length === (Object.keys(entry.worst) as Theme[]).length) return // fully earning its place
      const fixed = (Object.keys(entry.worst) as Theme[]).filter((theme) => tally.failing[theme] === undefined)
      staleEntries.push(
        `  ${entry.route ?? '(all routes)'}  ${entry.selector}\n` +
          `      STALE in ${fixed.join(', ')}: the site renders and now clears its floor there.\n` +
          `      ${
            stillFailing.length === 0
              ? 'Delete this entry.'
              : `Remove ${fixed.join(', ')} from its \`worst\` map (${stillFailing.join(', ')} still fails).`
          }\n` +
          `      An allowlist that outlives its defects is how a guard stops meaning anything. If you\n` +
          `      just fixed this, editing the entry is part of that fix.\n` +
          `      bead ${entry.bead}: ${entry.reason}`
      )
    })

    // Soft, so ONE run reports every category. A hard assertion on the first
    // would hide a new violation behind a stale entry and cost a whole
    // 2-viewport x 3-theme x 11-route walk to discover it.
    expect.soft(
      thinRoutes,
      thinRoutes.length === 0
        ? ''
        : `${thinRoutes.length} route/theme/viewport combination(s) rendered too little to audit. This ` +
            `guard must never report green because a page failed to mount.\n${thinRoutes.join('\n')}`
    ).toEqual([])

    expect.soft(
      unresolvable,
      unresolvable.length === 0
        ? ''
        : `${unresolvable.length} element(s) could not be measured. There were zero of these when this ` +
            `spec was written, and a site the guard cannot measure is a site the guard does not cover — ` +
            `so it fails rather than skipping. Teach the measurement to handle the case (a gradient ` +
            `backdrop needs its lightest and darkest stop measured, not averaged); do not add it to ` +
            `ALLOWLIST, which is for sites that fail, not for sites that are unknown.\n` +
            `${unresolvable.join('\n')}`
    ).toEqual([])

    expect.soft(
      staleEntries,
      staleEntries.length === 0
        ? ''
        : `${staleEntries.length} ALLOWLIST entr(ies) in e2e/contrast-aa.spec.ts no longer describe a ` +
            `real failure.\n${staleEntries.join('\n')}`
    ).toEqual([])

    expect.soft(
      regressions,
      regressions.length === 0
        ? ''
        : `${regressions.length} known exception(s) got WORSE than the ratio recorded for them.\n` +
            `${regressions.join('\n')}`
    ).toEqual([])

    expect.soft(
      newViolations,
      newViolations.length === 0
        ? ''
        : `${newViolations.length} NEW contrast violation(s). WCAG AA is ${FLOORS.normal}:1 for normal ` +
            `text, ${FLOORS.large}:1 for large text (>=24px, or >=18.66px bold) and ${FLOORS.glyph}:1 for ` +
            `non-text glyphs. The ratios below are TRUE COMPOSITED values — the background is the whole ` +
            `ancestor chain resolved through the compositor, with element and ancestor opacity folded ` +
            `in — so "the declared colour passes on white" is not a rebuttal. Fix the site by moving it ` +
            `to a token that clears the floor on the surface it actually sits on (see the light-theme ` +
            `remediation notes in frontend/src/index.css, bead enhancedchannelmanager-dlavh). If it is ` +
            `a deliberate, owned exception, add it to ALLOWLIST with the OPEN bead that owns it and the ` +
            `measured ratio.\n${newViolations.join('\n')}`
    ).toEqual([])
  })
})
