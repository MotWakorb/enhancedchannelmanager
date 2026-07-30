/**
 * The P1 type scale holds across every route's content pane.
 *
 * THE SCALE (bead `enhancedchannelmanager-f4yc7`, PO preset P1):
 *
 *   metric      20px / 600        status icon   18px
 *   section     15px / 600        action icon   16px
 *   body        13px / 400        small icon    14px
 *   meta        11px / 400        empty-state   64px
 *   micro/badge 10px
 *
 * so a visible text node in a route's content pane must compute to one of
 * {20, 15, 13, 11, 10} px, or one of the icon sizes {18, 16, 14, 64} px.
 * Nothing else. `em`/`rem`-relative and bare user-agent sizes are the two
 * ways sites drift off it, and both show up here as odd values — 13.3333px
 * (an unstyled UA `<button>`), 14.4px / 9.6px (`0.9em` / `0.6em` compounding),
 * 17.6px / 10.4px (`1.1rem` / `0.65rem`), 24px (a Material Icons default that
 * was never given an icon role).
 *
 * SCOPE. The primary rail and the top band are EXCLUDED. Both were settled by
 * beads `-57pp3` and `-eupzi` and are explicitly out of scope for the content
 * scale — `e2e/frozen-chrome.spec.ts` pins them instead. Screen-reader-only
 * elements are excluded too: they are 1x1 clipped boxes, not visible text
 * (`e2e/sr-only-hidden.spec.ts` is what keeps them that way).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WHY THERE IS AN ALLOWLIST, AND WHY IT CANNOT ROT
 *
 * Six of the ten routes have off-scale sites today. A guard that waited for
 * all of them to be fixed would ship on nobody's timetable and catch nothing
 * in the meantime; a guard with a plain "known exceptions, ignore these"
 * list would go permanently green and stop meaning anything the moment the
 * first one was fixed. So the allowlist is bidirectional, the same discipline
 * as TIER 2 of `frontend/src/cssAudits/sharedClassChunkLeak.audit.test.ts`:
 *
 *   NEW      an off-scale site matching no entry            -> FAIL
 *   STALE    an entry whose site renders and is now ON scale -> FAIL
 *                                                              (delete it)
 *   MISSING  an entry whose selector matches nothing, and
 *            which is not marked `dataDependent`             -> FAIL
 *
 * The STALE arm is the load-bearing one: it means fixing a site REQUIRES
 * deleting its entry in the same commit, and the list can never quietly
 * outlive the defects it describes. Every entry names an owning bead, so a
 * reader can always answer "who is going to fix this, and under what?".
 *
 * `dataDependent: true` marks a site that only renders when the instance has
 * the relevant records. This instance has almost none, so those entries
 * cannot be checked for staleness here — the flag is a documented admission,
 * not a hiding place, and everything without it is checked strictly.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * NOT VACUOUS. Every route asserts a floor on how many visible text elements
 * it walked. Without it, a route that failed to mount would contribute zero
 * off-scale sites and read as a pass — the single most likely way a guard
 * like this dies quietly.
 *
 * WHEN THIS FAILS ON A NEW SITE: give the element a P1 role from
 * `frontend/src/shared/tokens.css` rather than a literal px value, and delete
 * any `em`-relative size in its ancestor chain (an `em` size compounds and is
 * the reason 0.9em reads as 14.4px here and something else two levels down).
 * See docs/css_guidelines.md and bead enhancedchannelmanager-f4yc7.
 */
import { test, expect } from './fixtures/base'
import {
  captureStorageState,
  goToRoute,
  openApp,
  PRIMARY_ROUTES,
  type StorageState,
} from './fixtures/css-guard'
import type { Page } from '@playwright/test'

/** Text roles, then icon roles. Compared numerically — see `ON_SCALE_EPSILON`. */
const ON_SCALE_PX: readonly number[] = [20, 15, 13, 11, 10, 18, 16, 14, 64]

/** Sub-pixel slack only. Nowhere near enough to swallow a 13 -> 13.3333 drift. */
const ON_SCALE_EPSILON = 0.01

/** Subtrees that are not the content pane, and elements that are not visible text. */
const EXCLUDED_ANCESTORS: readonly string[] = ['.primary-sidebar', '.header', '.sr-only', '.visually-hidden']

/**
 * Minimum visible text elements a route must yield. Observed lows on an
 * almost-empty instance: settings 48, channel-pipeline 81, channel-manager 90.
 * 25 is well under every one of those and well over "the route did not mount".
 */
const MIN_TEXT_ELEMENTS = 25

interface Exception {
  /** Route hash id the site lives on. */
  route: string
  /** Selector matching the offending element itself, not an ancestor. */
  selector: string
  /** The off-scale computed sizes this site is permitted to render at. */
  sizesPx: readonly number[]
  /** Bead that owns the fix, or that recorded the decision to keep it. */
  bead: string
  /** Why it is off scale, and what is expected to happen to it. */
  reason: string
  /**
   * True when the element only renders if the instance holds the relevant
   * records. Staleness cannot be judged for these on a near-empty instance,
   * so absence is tolerated — and only for these.
   */
  dataDependent?: boolean
}

/**
 * SEEDED FROM A LIVE MEASUREMENT, not from a hand-written list, and re-checked
 * against a rebuild immediately before it shipped. Every entry below was
 * observed off-scale on the running build; nothing was carried over on trust.
 * Entries deliberately NOT here, each verified absent rather than assumed:
 *   - `SettingsTab .settings-page-header h2` / `p` — retired by bead `-2896r`;
 *     they no longer render at all.
 *   - `StickySectionNav > button` / `> span`, `.unused-only-toggle`,
 *     `.epg-source-row .drag-handle .material-icons`, `.col-drag .drag-handle
 *     .material-icons`, `.filter-dropdown-button > span`,
 *     `.enter-edit-mode-btn` — all nine were seeded, then reported STALE by
 *     this very guard when their fixes landed, and deleted. The list has
 *     already been proven to refuse to outlive its defects.
 */
const ALLOWLIST: readonly Exception[] = [
  // ── PERMANENT: the route header status band ───────────────────────────
  // f4yc7's PO decision scoped the P1 scale to the CONTENT PANE only. These
  // four render inside the frozen route-header band, which that decision
  // deliberately left alone. They are exceptions by design, not debt.
  {
    route: 'channel-pipeline',
    selector: '.channel-pipeline-stats .stat-value',
    sizesPx: [24],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Frozen route-header status band — outside the P1 content-pane scope by PO decision.',
    dataDependent: true,
  },
  {
    route: 'm3u-changes',
    selector: '.summary-card .summary-value',
    sizesPx: [17.6],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Frozen summary band (1.1rem) — outside the P1 content-pane scope by PO decision.',
    dataDependent: true,
  },
  {
    route: 'm3u-changes',
    selector: '.summary-card .summary-label',
    sizesPx: [10.4],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Frozen summary band (0.65rem) — outside the P1 content-pane scope by PO decision.',
    dataDependent: true,
  },
  {
    route: 'journal',
    selector: '.header-stats .header-total',
    sizesPx: [12.8],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Frozen route-header status band (0.8rem) — outside the P1 content-pane scope.',
    dataDependent: true,
  },

  // ── PENDING: em/rem-relative sizes on pages the P1 rollout has not reached ──
  // Each one is expected to disappear. When it does, this guard goes RED with
  // "STALE" and the entry must be deleted in the same commit as the fix —
  // that is the mechanism, not an inconvenience. Nine further entries seeded
  // from an earlier measurement were removed the same day, because the STALE
  // arm reported them the moment their fixes landed. That is the list working.
  {
    route: 'channel-manager',
    selector: '.filter-dropdown-button .dropdown-arrow, .group-filter-button .dropdown-arrow',
    sizesPx: [9.6],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'em-relative (0.6em) caret compounding off the scale. P1 rollout to this page pending.',
  },
  {
    route: 'journal',
    selector: '.journal-purge-control .journal-purge-label',
    sizesPx: [13.6],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'rem-relative (0.85rem) toolbar label. P1 rollout to this page pending.',
  },
  {
    route: 'channel-manager',
    selector: '.group-toggle-btn .group-toggle',
    sizesPx: [11.2],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'em-relative (0.7em) disclosure caret. P1 rollout to this page pending.',
    dataDependent: true,
  },

  // ── PENDING: Material Icons still at the 24px browser default ─────────
  // The P1 icon roles are 18 / 16 / 14px. Bead meh0a records the PO decision
  // to hold the P1 rollout back from the remaining pages while M3U and EPG
  // are still being iterated, so these are deferred rather than unowned.
  {
    route: 'stats',
    selector: '.panel-header .refresh-btn .material-icons',
    sizesPx: [24],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Material Icons default; the P1 action-icon role is 16px.',
  },
  {
    route: 'logo-manager',
    selector: '.source-load-announcement .material-icons',
    sizesPx: [24],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Material Icons default on the transient source-load status; P1 status icon is 18px.',
    dataDependent: true,
  },
  {
    route: 'logo-manager',
    selector: '.logo-thumbnail .placeholder .material-icons',
    sizesPx: [24],
    bead: 'enhancedchannelmanager-f4yc7',
    reason: 'Material Icons default on the broken-artwork placeholder; renders only when a logo fails to load.',
    dataDependent: true,
  },
]

interface OffScaleSite {
  fontSizePx: number
  signature: string
  sample: string
  count: number
}

interface RouteScan {
  textElementCount: number
  offScale: OffScaleSite[]
  /** entry index -> what its selector actually found on this route. */
  entryStatus: Array<{ matched: number; offScaleMatches: number }>
}

async function scanRoute(page: Page, entries: readonly Exception[]): Promise<RouteScan> {
  return page.evaluate(
    ({ onScale, epsilon, excluded, selectors }) => {
      const isOnScale = (size: number) => onScale.some((value) => Math.abs(value - size) < epsilon)
      const isExcluded = (element: Element) => excluded.some((ancestor) => element.closest(ancestor))
      const isRendered = (element: Element) => {
        const rect = element.getBoundingClientRect()
        if (rect.width <= 0 || rect.height <= 0) return false
        return getComputedStyle(element).visibility !== 'hidden'
      }
      /** Short structural path — enough to find the element without being a brittle full path. */
      const signature = (element: Element) => {
        const parts: string[] = []
        for (let node: Element | null = element; node && node !== document.body && parts.length < 5; node = node.parentElement) {
          const classes = (node.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3).join('.')
          parts.unshift(node.tagName.toLowerCase() + (classes ? `.${classes}` : ''))
        }
        return parts.join(' > ')
      }

      // One representative element per text node's parent — a node walk rather
      // than a selector list, so a brand-new component cannot slip past by not
      // being on anybody's list.
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      const visited = new Set<Element>()
      const bySignature = new Map<string, { fontSizePx: number; signature: string; sample: string; count: number }>()
      let textElementCount = 0
      let node: Node | null
      while ((node = walker.nextNode())) {
        const text = (node.nodeValue || '').trim()
        if (!text) continue
        const element = node.parentElement
        if (!element || visited.has(element)) continue
        visited.add(element)
        if (isExcluded(element) || !isRendered(element)) continue
        textElementCount += 1
        const fontSizePx = Number.parseFloat(getComputedStyle(element).fontSize)
        if (isOnScale(fontSizePx)) continue
        const key = `${fontSizePx}|${signature(element)}`
        const existing = bySignature.get(key)
        if (existing) existing.count += 1
        else bySignature.set(key, { fontSizePx, signature: signature(element), sample: text.slice(0, 40), count: 1 })
      }

      // Sites that an allowlist entry claims are dropped from the "new" list;
      // the same pass records whether each entry is still earning its place.
      const entryStatus = selectors.map(() => ({ matched: 0, offScaleMatches: 0 }))
      const claimed = new Set<Element>()
      selectors.forEach((entry, index) => {
        let matches: Element[]
        try {
          matches = Array.from(document.querySelectorAll(entry.selector))
        } catch {
          return // malformed selector: reported as MISSING rather than aborting the walk
        }
        for (const element of matches) {
          if (isExcluded(element) || !isRendered(element)) continue
          if (!(element.textContent || '').trim()) continue
          entryStatus[index].matched += 1
          const fontSizePx = Number.parseFloat(getComputedStyle(element).fontSize)
          if (entry.sizesPx.some((allowed) => Math.abs(allowed - fontSizePx) < epsilon)) {
            entryStatus[index].offScaleMatches += 1
            claimed.add(element)
          }
        }
      })

      // Re-walk to drop claimed elements from the off-scale tally.
      const offScale: Array<{ fontSizePx: number; signature: string; sample: string; count: number }> = []
      for (const site of bySignature.values()) offScale.push(site)
      const claimedSignatures = new Set<string>()
      for (const element of claimed) {
        claimedSignatures.add(`${Number.parseFloat(getComputedStyle(element).fontSize)}|${signature(element)}`)
      }
      return {
        textElementCount,
        offScale: offScale.filter((site) => !claimedSignatures.has(`${site.fontSizePx}|${site.signature}`)),
        entryStatus,
      }
    },
    {
      onScale: [...ON_SCALE_PX],
      epsilon: ON_SCALE_EPSILON,
      excluded: [...EXCLUDED_ANCESTORS],
      selectors: entries.map((entry) => ({ selector: entry.selector, sizesPx: [...entry.sizesPx] })),
    }
  ) as Promise<RouteScan>
}

test.describe('every route content pane stays on the P1 type scale', () => {
  // One login for the whole file — the login endpoint is rate-limited at 5/minute.
  let storageState: StorageState

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(4 * 60 * 1000)
    storageState = await captureStorageState(browser)
  })

  test('all ten routes: no new off-scale text, and no stale allowlist entry', async ({ browser }) => {
    // Ten lazily-loaded routes in one context, each with a networkidle wait.
    test.setTimeout(6 * 60 * 1000)

    const page = await openApp(browser, storageState, { width: 1600, height: 1000 })

    const newSites: string[] = []
    const thinRoutes: string[] = []
    // entry index -> per-route tallies, so staleness is judged on the route the
    // entry actually names rather than on wherever the selector happens to hit.
    const entryTotals = ALLOWLIST.map(() => ({ matched: 0, offScaleMatches: 0 }))

    try {
      for (const route of PRIMARY_ROUTES) {
        await goToRoute(page, route)
        const routeEntries = ALLOWLIST.filter((entry) => entry.route === route.id)
        const scan = await scanRoute(page, routeEntries)

        if (scan.textElementCount < MIN_TEXT_ELEMENTS) {
          thinRoutes.push(
            `  ${route.id}: only ${scan.textElementCount} visible text element(s) (floor ${MIN_TEXT_ELEMENTS}). ` +
              `The route did not render enough to be audited, so a clean result here would be meaningless.`
          )
        }

        for (const site of scan.offScale) {
          newSites.push(
            `  ${route.id}  ${site.fontSizePx}px  x${site.count}\n` +
              `      ${site.signature}\n` +
              `      e.g. "${site.sample}"`
          )
        }

        routeEntries.forEach((entry, localIndex) => {
          const globalIndex = ALLOWLIST.indexOf(entry)
          entryTotals[globalIndex].matched += scan.entryStatus[localIndex].matched
          entryTotals[globalIndex].offScaleMatches += scan.entryStatus[localIndex].offScaleMatches
        })
      }
    } finally {
      await page.context().close()
    }

    const staleEntries: string[] = []
    ALLOWLIST.forEach((entry, index) => {
      const totals = entryTotals[index]
      if (totals.offScaleMatches > 0) return // still earning its place
      if (totals.matched === 0) {
        // Nothing matched at all. For a data-dependent site on a near-empty
        // instance that is expected; for anything else it means the selector
        // has rotted and the entry is no longer describing reality.
        if (entry.dataDependent) return
        staleEntries.push(
          `  ${entry.route}  ${entry.selector}\n` +
            `      MATCHED NOTHING. The entry no longer describes any rendered element — either the\n` +
            `      site was removed, or the selector rotted. Delete the entry, or fix the selector.\n` +
            `      bead ${entry.bead}: ${entry.reason}`
        )
        return
      }
      staleEntries.push(
        `  ${entry.route}  ${entry.selector}\n` +
          `      STALE: ${totals.matched} element(s) render and every one of them is now ON scale.\n` +
          `      The exception has been fixed — delete this entry.\n` +
          `      bead ${entry.bead}: ${entry.reason}`
      )
    })

    // Soft, so ONE run reports every category at once. A hard assertion on the
    // first would hide a new off-scale site behind a stale allowlist entry and
    // cost an extra full ten-route walk to discover it.
    expect.soft(
      thinRoutes,
      thinRoutes.length === 0
        ? ''
        : `${thinRoutes.length} route(s) rendered too little to audit. This guard must never report ` +
            `green because a page failed to mount.\n${thinRoutes.join('\n')}`
    ).toEqual([])

    expect.soft(
      staleEntries,
      staleEntries.length === 0
        ? ''
        : `${staleEntries.length} allowlist entr(ies) in e2e/route-typography-scale.spec.ts no longer ` +
            `describe a real off-scale site. Delete them — an allowlist that outlives its defects is ` +
            `how a guard stops meaning anything. If you just fixed one of these, deleting its entry is ` +
            `part of that fix.\n${staleEntries.join('\n')}`
    ).toEqual([])

    expect.soft(
      newSites,
      newSites.length === 0
        ? ''
        : `${newSites.length} NEW off-scale text site(s). Every visible text node in a route's content ` +
            `pane must compute to one of ${ON_SCALE_PX.join(', ')}px (P1 text roles then icon roles, ` +
            `bead enhancedchannelmanager-f4yc7). Give the element a P1 role from ` +
            `frontend/src/shared/tokens.css instead of a literal size, and delete any em-relative size ` +
            `in its ancestor chain — em compounds, which is why 0.9em reads as 14.4px here. If the site ` +
            `is a deliberate, owned exception, add it to ALLOWLIST with the bead that owns it.\n` +
            `${newSites.join('\n')}`
    ).toEqual([])
  })
})
