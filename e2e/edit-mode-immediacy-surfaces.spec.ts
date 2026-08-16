/**
 * Rendered-browser guard for Edit Mode's staged-vs-immediate surfaces
 * (epic enhancedchannelmanager-r93hq, beads -kz089 and -i4yk1).
 *
 * WHAT THIS PINS, AND WHY IT HAS TO BE A BROWSER.
 *
 * Three surfaces landed in that epic whose correctness is *layout*, and layout
 * is the one thing jsdom does not have. Every unit test around them asserts
 * markup and props; none of them can observe a box.
 *
 *   1. THE SELECTION ACTION BAR'S FIXED POSITIONING. Commit 165d00b3 moved
 *      `position: fixed` OFF `.selection-action-bar` and ONTO a new
 *      `.selection-action-bar-shell` wrapper, so the bar could carry a
 *      full-width immediacy note above it without the bar itself wrapping (it
 *      is a single `white-space: nowrap` row by design). If a future edit
 *      drops the rule from the shell, or re-adds it to the bar, or something
 *      introduces a transform/filter/contain ancestor that turns the fixed
 *      containing block into an element instead of the viewport, the bar stops
 *      floating over the panes and lands wherever document flow puts it —
 *      typically off the bottom of a scrolled list, unreachable. Every
 *      component test still passes: `SelectionActionBar` renders the same DOM
 *      either way. This is the highest-risk item in the epic and nothing else
 *      in the suite can see it.
 *
 *   2. THE IRREVERSIBLE NOTICE'S LINE IN `.modal-footer`. The notice claims a
 *      line of its own via `.modal-footer:has(.irreversible-action-notice)`,
 *      which is the only `:has()` selector this behaviour depends on. `:has()`
 *      support and its layout effect are both unobservable to jsdom — jsdom
 *      neither computes `flex-wrap` nor lays out the row — so a footer that
 *      quietly reverted to one nowrap line, jamming a 3-line warning between
 *      Cancel and Merge, would report green everywhere else.
 *
 *   3. THE IMMEDIATE-ACTION NOTE AT THE PROBE ENTRY POINTS. It is injected
 *      into two dropdown menus and a modal toolbar, none of which had a
 *      multi-line prose block in them before. A note that overlaps the menu
 *      item above it, or pushes the menu off-screen, is a rendering failure
 *      that a `getByTestId(...)` presence assertion cannot distinguish from
 *      success.
 *
 * NO BACKEND, NO LIVE INSTANCE — STRUCTURALLY. This spec REFUSES to run unless
 * `E2E_EXACT_BUILD=true` and `E2E_START_SERVER=true`, the same refusal
 * `e2e/filter-select-ownership.spec.ts` makes for the same reason. Those two
 * flags build the checked-out source and serve it on the isolated preview port
 * 127.0.0.1:4173, and they supply the base URL themselves, so the spec cannot
 * be aimed at the operator's live ECM on :6100 — the instance bead
 * `enhancedchannelmanager-536bl` removed the `E2E_BASE_URL` default to protect.
 * Every `/api` call is fulfilled from the constant table below, so nothing this
 * spec does can reach a real server even if one were listening: the whole point
 * of the exercise is a merge and a CSV import that DELETE and CREATE channels.
 *
 * NOT VACUOUS. Each arm asserts the thing it measures is actually on screen
 * before measuring it — the selection bar's control count, the footer's button
 * count, each menu's sibling count. A probe that renders nothing fails loudly;
 * a guard whose subject silently stops existing reports green forever, which is
 * worse than no guard.
 *
 * WHEN THIS FAILS: read which arm. Arm 1 is a positioning regression in
 * `SelectionActionBar.css`. Arm 2 is the `:has()` rule in
 * `IrreversibleActionNotice.css` (or a modal chassis change that outranks it).
 * Arm 3 is `ImmediateActionNote.css` or the menu stylesheet it sits in. None of
 * them has a tolerance to widen.
 */
import { expect, test } from './fixtures/base';
import type { Browser, Page } from '@playwright/test';

if (process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true') {
  throw new Error(
    'edit-mode-immediacy-surfaces requires E2E_EXACT_BUILD=true and E2E_START_SERVER=true. ' +
      'It drives Merge and CSV Import, which delete and create channels, so it runs only against ' +
      'the isolated preview build it serves itself — never a live ECM instance.',
  );
}

/**
 * The supported viewport range's two ends. 1280x720 is the MINIMUM supported
 * viewport (`e2e/frozen-chrome.spec.ts` pins the same floor and says why); the
 * floating bar is the widest single nowrap row in the app, so the narrow end is
 * the one that can clip it, and the wide end is where a shrink-to-fit shell can
 * drift off centre.
 */
const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
] as const;

/** `bottom: 12px` in `.selection-action-bar-shell`. Exact, not a tolerance. */
const SHELL_BOTTOM_OFFSET = 12;

/** Sub-pixel slack for centring and edge comparisons only. Never for offsets. */
const EPSILON = 0.75;

// ---------------------------------------------------------------- API stubs

const channels = Array.from({ length: 8 }, (_, index) => ({
  id: index + 1,
  channel_number: 100 + index,
  name: `Test Channel ${index + 1}`,
  channel_group_id: (index % 2) + 1,
  tvg_id: null,
  tvc_guide_stationid: null,
  epg_data_id: null,
  // Non-empty: the per-channel Probe menu item — and therefore its immediacy
  // note — renders only for a channel that has streams to probe.
  streams: [1, 2],
  stream_profile_id: null,
  uuid: `0000000${index}-0000-4000-8000-000000000000`,
  logo_id: null,
  auto_created: false,
  auto_created_by: null,
  auto_created_by_name: null,
}));

const channelGroups = [
  { id: 1, name: 'Entertainment', channel_count: 4, is_auto_sync: false },
  { id: 2, name: 'Sports', channel_count: 4, is_auto_sync: false },
];

const paginated = (results: unknown[]) => ({ count: results.length, next: null, previous: null, results });

const duplicates = {
  total_groups: 1,
  total_duplicate_channels: 2,
  groups: [
    {
      normalized_name: 'test channel',
      channels: channels.slice(0, 2).map((channel) => ({
        id: channel.id,
        name: channel.name,
        normalized_name: 'test channel',
        channel_number: channel.channel_number,
        stream_count: 2,
        channel_group_id: channel.channel_group_id,
        channel_group_name: 'Entertainment',
      })),
    },
  ],
};

/**
 * Ordered because several paths are prefixes of others (`/api/channels/logos`
 * has to be matched before `/api/channels`). Anything unmatched is answered
 * with an empty list rather than failing, so an endpoint added later renders an
 * empty state instead of crashing the route and turning this guard red for a
 * reason that has nothing to do with what it pins.
 */
const STUBS: ReadonlyArray<readonly [RegExp, unknown]> = [
  [/\/api\/auth\/status(?:\/|\?|$)/, { require_auth: false, setup_complete: true, dispatcharr_enabled: false }],
  [/\/api\/auth\/setup-required(?:\/|\?|$)/, { required: false }],
  [/\/api\/settings(?:\/|\?|$)/, { configured: true, url: 'http://synthetic.invalid', default_channel_profile_ids: [] }],
  [/\/api\/channels\/logos(?:\/|\?|$)/, paginated([])],
  [/\/api\/channels\/find-duplicates(?:\/|\?|$)/, duplicates],
  [/\/api\/channels(?:\/|\?|$)/, paginated(channels)],
  [/\/api\/channel-groups(?:\/|\?|$)/, channelGroups],
  [/\/api\/channel-profiles(?:\/|\?|$)/, [{ id: 1, name: 'Default', channels: [] }, { id: 2, name: 'Kids', channels: [] }]],
  [/\/api\/channel-merges(?:\/|\?|$)/, { results: [], count: 0 }],
  [/\/api\/streams\/stale-ids(?:\/|\?|$)/, { stale_stream_ids: [] }],
  [/\/api\/streams(?:\/|\?|$)/, paginated([])],
  [/\/api\/stream-groups(?:\/|\?|$)/, []],
  [/\/api\/stream-profiles(?:\/|\?|$)/, []],
  [/\/api\/notifications(?:\/|\?|$)/, { notifications: [], unread_count: 0, results: [] }],
  [/\/api\/logos(?:\/|\?|$)/, paginated([])],
  [/\/api\/epg\//, []],
  // `auto_channel_sync` is read unguarded off every provider; a provider list
  // without it crashes the Channel Manager tab's error boundary.
  [/\/api\/providers(?:\/|\?|$)/, [{
    id: 1, name: 'Provider A', enabled: true, channel_group_id: 1, auto_sync: false,
    custom_properties: null, stream_count: 0, auto_channel_sync: false,
  }]],
];

async function stubBackend(page: Page): Promise<void> {
  await page.route(/\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    // No session on the preview build — the same posture
    // `e2e/fixtures/css-guard.ts` and `e2e/filter-select-ownership.spec.ts`
    // state for the same backend-less mode.
    if (/\/api\/auth\/me(?:\/|\?|$)/.test(path)) return json({ detail: 'No session on the preview build' }, 401);
    if (/\/api\/(session-start|auth\/refresh)(?:\/|\?|$)/.test(path)) return route.fulfill({ status: 204, body: '' });

    for (const [pattern, body] of STUBS) if (pattern.test(path)) return json(body);
    return json([]);
  });
}

// ------------------------------------------------------------- page helpers

async function openChannelManagerInEditMode(
  browser: Browser,
  viewport: { width: number; height: number },
): Promise<Page> {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await stubBackend(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.tab-navigation', { state: 'visible', timeout: 30_000 });
  await page.evaluate(() => { window.location.hash = '#channel-manager'; });
  await page.waitForSelector('.channels-pane', { timeout: 60_000 });
  // Kill animations so a measurement can never catch a control mid-transition.
  await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
  await page.locator('.enter-edit-mode-btn').click();
  await page.waitForSelector('.edit-mode-done-btn', { timeout: 15_000 });
  // Groups render collapsed; the channel rows this spec selects are inside one.
  await page.locator('.channels-pane .group-header').filter({ hasText: 'Entertainment' }).first().click();
  await page.waitForSelector('.channels-pane .channel-item', { timeout: 15_000 });
  return page;
}

async function selectChannels(page: Page, count: number): Promise<void> {
  const selectors = page.locator('.channels-pane .channel-select-indicator');
  await expect(
    selectors,
    'Edit Mode must render a per-row select control; without one nothing below is reachable',
  ).not.toHaveCount(0);
  for (let index = 0; index < count; index += 1) await selectors.nth(index).click();
  await page.waitForSelector('.selection-action-bar', { timeout: 10_000 });
}

/** Open the pane-level three-dot menu and activate one of its items. */
async function openPaneToolbarItem(page: Page, label: string): Promise<void> {
  await page.locator('.channels-pane .pane-toolbar-menu-btn').first().click();
  await page.waitForSelector('.pane-toolbar-menu-dropdown', { timeout: 10_000 });
  await page.locator('.pane-toolbar-menu-dropdown .pane-toolbar-menu-item').filter({ hasText: label }).first().click();
}

/** Geometry of one element, as the browser laid it out. */
interface Box {
  left: number; top: number; right: number; bottom: number; width: number; height: number;
}

const boxOf = (page: Page, selector: string) => page.evaluate((sel) => {
  const element = document.querySelector(sel);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  return {
    left: +rect.left.toFixed(2), top: +rect.top.toFixed(2),
    right: +rect.right.toFixed(2), bottom: +rect.bottom.toFixed(2),
    width: +rect.width.toFixed(2), height: +rect.height.toFixed(2),
  };
}, selector) as Promise<Box | null>;

/** True when two rendered boxes share any area at all. */
const overlaps = (a: Box, b: Box) =>
  a.left < b.right - EPSILON && b.left < a.right - EPSILON &&
  a.top < b.bottom - EPSILON && b.top < a.bottom - EPSILON;

// ============================================================== ARM 1: the bar

test.describe('the selection action bar stays fixed to the viewport while Edit Mode holds a selection', () => {
  for (const viewport of VIEWPORTS) {
    test(`${viewport.width}x${viewport.height}: the shell owns the fixed positioning and the bar rides on it`, async ({ browser }) => {
      test.setTimeout(3 * 60 * 1000);
      const page = await openChannelManagerInEditMode(browser, viewport);
      try {
        await selectChannels(page, 2);

        const measured = await page.evaluate(() => {
          const shell = document.querySelector<HTMLElement>('.selection-action-bar-shell');
          const bar = document.querySelector<HTMLElement>('.selection-action-bar');
          const note = document.querySelector<HTMLElement>('.selection-action-bar-shell > .immediate-action-note');
          if (!shell || !bar) return null;
          const rect = (element: Element) => {
            const r = element.getBoundingClientRect();
            return {
              left: +r.left.toFixed(2), top: +r.top.toFixed(2), right: +r.right.toFixed(2),
              bottom: +r.bottom.toFixed(2), width: +r.width.toFixed(2), height: +r.height.toFixed(2),
            };
          };
          const shellRect = rect(shell);
          const barRect = rect(bar);
          const style = getComputedStyle(shell);
          const centre = document.elementFromPoint(
            barRect.left + barRect.width / 2,
            barRect.top + barRect.height / 2,
          );
          return {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            shellPosition: style.position,
            shellZIndex: style.zIndex,
            // The fixed containing block is the viewport only while no ancestor
            // establishes one. If a transform / filter / perspective / contain
            // appears on an ancestor, `position: fixed` still reads "fixed" and
            // the bar silently anchors to that ancestor instead. Walking the
            // chain is the only way to see the difference.
            containingBlockOffenders: (() => {
              const offenders: string[] = [];
              for (let node = shell.parentElement; node; node = node.parentElement) {
                const s = getComputedStyle(node);
                const reasons = [
                  s.transform !== 'none' && 'transform',
                  s.perspective !== 'none' && 'perspective',
                  s.filter !== 'none' && 'filter',
                  s.backdropFilter !== 'none' && 'backdrop-filter',
                  /paint|layout|strict|content/.test(s.contain) && `contain:${s.contain}`,
                  s.willChange !== 'auto' && `will-change:${s.willChange}`,
                ].filter(Boolean);
                if (reasons.length) offenders.push(`${node.tagName}.${node.className} -> ${reasons.join(', ')}`);
              }
              return offenders;
            })(),
            shell: shellRect,
            bar: barRect,
            note: note ? rect(note) : null,
            barIsInsideShell: shell.contains(bar),
            controlCount: bar.querySelectorAll('button').length,
            countLabel: bar.querySelector('[data-testid="selection-bar-count"]')?.textContent?.trim() ?? null,
            hitAtBarCentre: centre ? `${centre.tagName}.${centre.className}` : null,
            barCentreIsInBar: centre ? bar.contains(centre) : false,
            documentOverflowsX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          };
        });

        expect(measured, 'the selection action bar and its shell must both render once two channels are selected').not.toBeNull();
        const m = measured!;

        // -- anti-vacuity: the thing being measured is really the bar --------
        expect(m.countLabel, 'the bar must report the live selection count').toBe('2 selected');
        expect(m.controlCount, 'the bar must carry its full control row, not a stub').toBeGreaterThanOrEqual(8);
        expect(m.barIsInsideShell, 'the bar must be a descendant of the shell that positions it').toBe(true);

        // -- the actual invariant -------------------------------------------
        expect(m.shellPosition, 'the fixed positioning lives on .selection-action-bar-shell (165d00b3)').toBe('fixed');
        expect(m.shellZIndex, 'above pane content/sticky (500), below modal overlays (1000)').toBe('900');
        expect(
          m.containingBlockOffenders,
          'an ancestor establishing a containing block re-anchors the "fixed" bar to that element ' +
            'instead of the viewport, which reads as fixed in every computed style and floats nowhere',
        ).toEqual([]);

        expect(
          m.shell.bottom,
          `the shell must sit ${SHELL_BOTTOM_OFFSET}px above the viewport floor`,
        ).toBeCloseTo(m.innerHeight - SHELL_BOTTOM_OFFSET, 1);
        expect(
          m.bar.bottom,
          'the bar is the shell\'s last child, so it shares the shell\'s floor',
        ).toBeCloseTo(m.innerHeight - SHELL_BOTTOM_OFFSET, 1);
        expect(
          Math.abs((m.shell.left + m.shell.right) / 2 - m.innerWidth / 2),
          'the shell is centred with left:50% + translateX(-50%)',
        ).toBeLessThanOrEqual(EPSILON);

        // Reachable, not merely present: a bar underneath something else is a
        // bar the operator cannot click.
        expect(
          m.barCentreIsInBar,
          `elementFromPoint at the bar's centre returned ${m.hitAtBarCentre} — something is covering the bar`,
        ).toBe(true);

        // The note takes the full-width line ABOVE the bar. That separation is
        // the entire reason the fixed positioning moved to the shell.
        expect(m.note, 'the probe immediacy note renders as the shell\'s first line').not.toBeNull();
        expect(m.note!.bottom, 'the note sits above the bar, never beside or over it').toBeLessThanOrEqual(m.bar.top + EPSILON);
        expect(m.note!.right, 'the note must not spill out of the shell').toBeLessThanOrEqual(m.shell.right + EPSILON);
        expect(m.note!.left).toBeGreaterThanOrEqual(m.shell.left - EPSILON);

        // Neither the bar nor its note may push the page sideways.
        expect(m.documentOverflowsX, 'the floating bar must not widen the document').toBe(false);
        expect(m.shell.left, 'the shell must start inside the viewport').toBeGreaterThanOrEqual(0);
        expect(m.shell.right, 'the shell must end inside the viewport').toBeLessThanOrEqual(m.innerWidth);

        // -- the proof that "fixed" means fixed ------------------------------
        // A statically positioned shell moves with the content it follows. Scroll
        // everything scrollable and re-measure: the viewport rect must not budge.
        const before = { top: m.shell.top, bottom: m.shell.bottom, left: m.shell.left };
        await page.evaluate(() => {
          window.scrollTo(0, document.documentElement.scrollHeight);
          document.querySelectorAll('*').forEach((element) => {
            if (element.scrollHeight > element.clientHeight) element.scrollTop = element.scrollHeight;
          });
        });
        await page.waitForTimeout(300);
        const after = await boxOf(page, '.selection-action-bar-shell');
        expect(after, 'the shell must survive scrolling the panes').not.toBeNull();
        expect(after!.top, 'a fixed shell does not move when the page scrolls').toBeCloseTo(before.top, 1);
        expect(after!.bottom).toBeCloseTo(before.bottom, 1);
        expect(after!.left).toBeCloseTo(before.left, 1);
      } finally {
        await page.context().close();
      }
    });
  }
});

// ================================================== ARM 2: irreversible notice

/**
 * The two Edit Mode modals that gate an irreversible action behind the notice.
 * CSV Import carries the same notice from the same component; it is exercised
 * by the shared footer assertions through Merge and Find Duplicates rather than
 * opened a third time, because reaching its confirm button needs a file upload
 * and its footer markup is identical.
 */
const GATED_MODALS = [
  { name: 'Merge', barButton: 'Merge' },
  { name: 'Find Duplicates', barButton: 'Find Duplicates' },
] as const;

test.describe('the irreversible notice takes its own line in the modal footer and holds the confirm inert', () => {
  for (const modal of GATED_MODALS) {
    test(`${modal.name}: the notice claims a line, and the confirm cannot be clicked until it is acknowledged`, async ({ browser }) => {
      test.setTimeout(3 * 60 * 1000);
      const page = await openChannelManagerInEditMode(browser, VIEWPORTS[0]);
      try {
        await selectChannels(page, 2);
        await page.locator('.selection-action-bar button').filter({ hasText: modal.barButton }).first().click();
        await page.waitForSelector('.modal-footer .irreversible-action-notice', { timeout: 20_000 });

        const layout = await page.evaluate(() => {
          const footer = document.querySelector<HTMLElement>('.modal-footer');
          const notice = footer?.querySelector<HTMLElement>('.irreversible-action-notice');
          if (!footer || !notice) return null;
          const rect = (element: Element) => {
            const r = element.getBoundingClientRect();
            return {
              left: +r.left.toFixed(2), top: +r.top.toFixed(2), right: +r.right.toFixed(2),
              bottom: +r.bottom.toFixed(2), width: +r.width.toFixed(2), height: +r.height.toFixed(2),
            };
          };
          const buttons = [...footer.querySelectorAll('button')].filter((b) => !b.closest('.irreversible-action-notice'));
          const footerStyle = getComputedStyle(footer);
          const padding = Number.parseFloat(footerStyle.paddingLeft) + Number.parseFloat(footerStyle.paddingRight);
          return {
            hasSelectorSupport: CSS.supports('selector(:has(*))'),
            flexWrap: footerStyle.flexWrap,
            footer: rect(footer),
            footerContentWidth: +(footer.clientWidth - padding).toFixed(2),
            notice: rect(notice),
            buttons: buttons.map((b) => ({ text: b.textContent?.trim() ?? '', ...rect(b), disabled: b.disabled })),
            acknowledgementCount: notice.querySelectorAll('input[type="checkbox"]').length,
          };
        });

        expect(layout, 'the gated modal must render a footer carrying the notice').not.toBeNull();
        const l = layout!;

        // -- anti-vacuity ----------------------------------------------------
        expect(l.hasSelectorSupport, ':has() is the mechanism under test; a browser without it proves nothing').toBe(true);
        expect(l.buttons.length, 'the footer must carry Cancel plus the gated confirm').toBeGreaterThanOrEqual(2);
        expect(l.acknowledgementCount, 'the notice is gated by exactly one acknowledgement checkbox').toBe(1);

        // -- the notice gets a line of its own -------------------------------
        // Geometry first, deliberately. `flex-wrap: wrap` is the mechanism, but
        // the operator-visible claim is that the warning is not crammed in
        // beside the button it gates, so the boxes are what get asserted; the
        // computed `flex-wrap` is checked afterwards as the diagnostic that
        // names WHICH rule went missing.
        expect(
          l.notice.width,
          'the notice spans the footer\'s full content width, which is what pushes the buttons onto the next line',
        ).toBeCloseTo(l.footerContentWidth, 0);
        for (const button of l.buttons) {
          expect(
            overlaps(l.notice, button),
            `the notice shares its line with "${button.text}" — the footer collapsed back to a single row`,
          ).toBe(false);
          expect(
            l.notice.bottom,
            `"${button.text}" must sit below the notice, not beside it`,
          ).toBeLessThanOrEqual(button.top + EPSILON);
        }
        expect(l.notice.right, 'the notice must stay inside the footer').toBeLessThanOrEqual(l.footer.right + EPSILON);
        expect(l.flexWrap, '.modal-footer:has(.irreversible-action-notice) sets flex-wrap: wrap').toBe('wrap');

        // -- the confirm is inert, proven by clicking, not by reading an attribute
        const confirm = page.locator('.modal-footer .modal-btn-primary');
        const confirmLabel = (await confirm.textContent())?.trim() ?? '';
        let normalClickLanded = true;
        await confirm.click({ timeout: 2_500 }).catch(() => { normalClickLanded = false; });
        expect(
          normalClickLanded,
          `"${confirmLabel}" accepted an ordinary click before the acknowledgement was ticked`,
        ).toBe(false);

        // A `disabled` attribute is a rendering decision. Force past Playwright's
        // actionability checks and require that the action still did not run:
        // the modal is still open and no toast was raised.
        await confirm.click({ force: true, timeout: 2_500 }).catch(() => undefined);
        await page.waitForTimeout(700);
        await expect(
          page.locator('.modal-footer .irreversible-action-notice'),
          'a forced click ran the irreversible action without an acknowledgement',
        ).toHaveCount(1);
        await expect(page.locator('.toast'), 'a forced click produced an outcome toast').toHaveCount(0);

        // -- ticking the acknowledgement is what releases it -----------------
        await page.locator('.irreversible-action-notice-ack input').check();
        await expect(
          confirm,
          'the acknowledgement must actually release the confirm, or the gate is a dead end',
        ).toBeEnabled();
      } finally {
        await page.context().close();
      }
    });
  }
});

// ============================================ ARM 3: immediate-action notes

/**
 * Widest a three-dot dropdown menu may become, in px.
 *
 * `.channel-menu-dropdown` and `.group-menu-dropdown` are shrink-to-fit boxes
 * with `min-width: 180px` and no upper bound, so the immediacy note — the only
 * prose either menu has ever contained — laid itself out on one line and took
 * the menu with it: measured at roughly 3.5x the menu's natural width. Nothing
 * clipped, nothing off-screen, just a menu that no longer reads as a menu. The
 * PO's 2026-08-16 decision is to cap the menu and let the note wrap rather than
 * shorten the note, whose whole purpose is to be unmissable.
 *
 * This is a CAP, not a tolerance: the assertion below pairs it with a wrap
 * check, so a regression that removes the cap fails on the width and a
 * regression that "fixes" the width by clipping or truncating the note fails on
 * the wrap.
 */
const DROPDOWN_MAX_WIDTH = 320;

/**
 * Every place the note is injected, and how to get there. `container` is the
 * element the note must stay inside; `minSiblings` is the anti-vacuity floor on
 * the controls it must not displace. `maxWidth`, where present, is the cap that
 * container may not exceed.
 */
const IMMEDIACY_NOTES = [
  {
    name: 'bulk probe (selection action bar)',
    testId: 'probe-immediate-note-bulk',
    container: '.selection-action-bar-shell',
    minSiblings: 1,
    // The floating bar is a full-width row by design; it is not a menu.
    maxWidth: null,
    open: async (page: Page) => { await selectChannels(page, 2); },
  },
  {
    name: 'per-channel probe (channel row menu)',
    testId: 'probe-immediate-note',
    container: '.channel-menu-dropdown',
    minSiblings: 4,
    maxWidth: DROPDOWN_MAX_WIDTH,
    open: async (page: Page) => {
      await page.locator('.channels-pane .channel-item').first().hover();
      await page.locator('.channels-pane .channel-item .channel-menu-btn').first().click();
      await page.waitForSelector('.channel-menu-dropdown', { timeout: 10_000 });
    },
  },
  {
    name: 'group probe (group menu — Edit Mode only)',
    testId: 'probe-immediate-note-group',
    container: '.group-menu-dropdown',
    minSiblings: 3,
    maxWidth: DROPDOWN_MAX_WIDTH,
    open: async (page: Page) => {
      const header = page.locator('.channels-pane .group-header').filter({ hasText: 'Entertainment' }).first();
      await header.hover();
      await header.locator('.group-menu-btn').first().click();
      await page.waitForSelector('.group-menu-dropdown', { timeout: 10_000 });
    },
  },
  {
    name: 'profile administration (Channel Profiles list)',
    testId: 'profile-admin-immediate-note',
    container: '.channel-profiles-modal .modal-toolbar',
    minSiblings: 2,
    // A modal toolbar sizes with its modal.
    maxWidth: null,
    open: async (page: Page) => {
      await openPaneToolbarItem(page, 'Channel Profiles');
      await page.waitForSelector('.channel-profiles-modal', { timeout: 15_000 });
    },
  },
] as const;

test.describe('the immediate-action note renders at every entry point without displacing its neighbours', () => {
  for (const site of IMMEDIACY_NOTES) {
    test(`${site.name}: the note is inside its container, on screen, and overlaps nothing`, async ({ browser }) => {
      test.setTimeout(3 * 60 * 1000);
      const page = await openChannelManagerInEditMode(browser, VIEWPORTS[0]);
      try {
        await site.open(page);
        await page.waitForSelector(`[data-testid="${site.testId}"]`, { timeout: 15_000 });
        await page.waitForTimeout(250);

        const measured = await page.evaluate(({ testId, container }) => {
          const note = document.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
          const host = document.querySelector<HTMLElement>(container);
          if (!note || !host) return null;
          const rect = (element: Element) => {
            const r = element.getBoundingClientRect();
            return {
              left: +r.left.toFixed(2), top: +r.top.toFixed(2), right: +r.right.toFixed(2),
              bottom: +r.bottom.toFixed(2), width: +r.width.toFixed(2), height: +r.height.toFixed(2),
            };
          };
          // Every rendered sibling control in the same container. These are the
          // "controls around it" that must keep their own space.
          const siblings = [...host.querySelectorAll<HTMLElement>('button, input, label, a')]
            .filter((element) => !note.contains(element) && element.getBoundingClientRect().height > 0)
            .map((element) => ({ text: (element.textContent ?? '').trim().slice(0, 32), ...rect(element) }));
          const centre = document.elementFromPoint(
            note.getBoundingClientRect().left + 4,
            note.getBoundingClientRect().top + note.getBoundingClientRect().height / 2,
          );
          // The prose block inside the note, and how many lines it occupies.
          // `lineHeight` is resolved off the note because the body inherits it;
          // a `normal` line-height would report NaN, so it falls back to the
          // font size, which under-counts rather than inventing a pass.
          const body = note.querySelector<HTMLElement>('.immediate-action-note-body') ?? note;
          const noteStyle = getComputedStyle(note);
          const lineHeight = Number.parseFloat(noteStyle.lineHeight)
            || Number.parseFloat(noteStyle.fontSize);

          return {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            note: rect(note),
            host: rect(host),
            siblings,
            noteIsInsideHost: host.contains(note),
            noteIsHitTestable: centre ? note.contains(centre) || note === centre : false,
            textLength: (note.textContent ?? '').trim().length,
            bodyLines: Math.round(body.getBoundingClientRect().height / lineHeight),
            bodyOverflowsHorizontally: body.scrollWidth > body.clientWidth + 1,
          };
        }, { testId: site.testId, container: site.container });

        expect(measured, `${site.name}: the note and its container must both render`).not.toBeNull();
        const m = measured!;

        // -- anti-vacuity ----------------------------------------------------
        expect(m.noteIsInsideHost, 'the note must render inside the surface it annotates').toBe(true);
        expect(m.textLength, 'an empty note annotates nothing').toBeGreaterThan(40);
        expect(m.note.height, 'a zero-height note is an invisible note').toBeGreaterThan(0);
        expect(
          m.siblings.length,
          `${site.name} must render its own controls; a note beside nothing proves nothing`,
        ).toBeGreaterThanOrEqual(site.minSiblings);

        // -- the note stays inside its container ------------------------------
        expect(m.note.left, 'the note must not spill out of its container').toBeGreaterThanOrEqual(m.host.left - EPSILON);
        expect(m.note.right).toBeLessThanOrEqual(m.host.right + EPSILON);
        expect(m.note.top).toBeGreaterThanOrEqual(m.host.top - EPSILON);
        expect(m.note.bottom).toBeLessThanOrEqual(m.host.bottom + EPSILON);

        // -- a menu stays a menu ----------------------------------------------
        // The note is prose in a shrink-to-fit box. Without a cap it lays out
        // on one line and drags the whole menu out to roughly 3.5x its natural
        // width. Both halves are asserted: the cap holds, AND the note answered
        // the cap by WRAPPING rather than by being clipped or truncated.
        if (site.maxWidth !== null) {
          expect(
            m.host.width,
            `${site.name}: the menu is ${m.host.width}px wide — the immediacy note stretched it instead of wrapping`,
          ).toBeLessThanOrEqual(site.maxWidth + EPSILON);
          expect(
            m.bodyLines,
            'the note must wrap onto more than one line inside a capped menu; one line means it is being clipped or the cap is not applying',
          ).toBeGreaterThanOrEqual(2);
          expect(
            m.bodyOverflowsHorizontally,
            'the note overflows its own box horizontally — the menu was capped by hiding text rather than by wrapping it',
          ).toBe(false);
        }

        // -- and the container stays on screen --------------------------------
        expect(m.host.left, `${site.name} was pushed off the left edge`).toBeGreaterThanOrEqual(0);
        expect(m.host.right, `${site.name} was pushed off the right edge`).toBeLessThanOrEqual(m.innerWidth);
        expect(m.host.top, `${site.name} was pushed off the top edge`).toBeGreaterThanOrEqual(0);
        expect(m.host.bottom, `${site.name} was pushed off the bottom edge`).toBeLessThanOrEqual(m.innerHeight);

        // -- nothing around it lost its space ---------------------------------
        expect(m.noteIsHitTestable, 'the note is covered by something else').toBe(true);
        for (const sibling of m.siblings) {
          expect(
            overlaps(m.note, sibling),
            `the note overlaps "${sibling.text || '(unlabelled control)'}" — it displaced a control instead of taking its own space`,
          ).toBe(false);
          expect(sibling.height, `"${sibling.text}" collapsed to zero height beside the note`).toBeGreaterThan(0);
        }
      } finally {
        await page.context().close();
      }
    });
  }
});
