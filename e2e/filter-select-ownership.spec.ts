/**
 * Production-build guard for bead enhancedchannelmanager-8epq2.
 *
 * The three owners live in three bundle chunks, while Journal and M3U Changes
 * portal their controls into frozen chrome. This walks both chunk orders and
 * revisits every owner so an eager or lazy shared selector cannot make box
 * chrome depend on session history. It requires the live service and therefore
 * remains part of the proportional CSS guard suite, not the backend-free
 * Frontend Tests Playwright step.
 */
import { expect, test } from './fixtures/base';
import type { Browser, Page } from '@playwright/test';

const OWNERS = [
  // ChannelManagerTab supplies the multi-select callbacks, so the production
  // route renders filter-dropdown controls; the two private CustomSelect
  // alternatives are pinned by the source/component census instead.
  { id: 'channel-manager', root: '.channels-pane', owner: '.streams-filter-select', count: 0 },
  { id: 'journal', root: '.journal-tab', owner: '.journal-filter-select', count: 3 },
  { id: 'm3u-changes', root: '.m3u-changes-tab', owner: '.m3u-changes-filter-select', count: 4 },
] as const;

const VIEWPORTS = [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }] as const;

if (process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true') {
  throw new Error('filter-select-ownership requires E2E_EXACT_BUILD=true and E2E_START_SERVER=true');
}

async function openApp(browser: Browser, viewport: { width: number; height: number }): Promise<Page> {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(/\/api\/auth\/status(?:\/|\?|$)/, (route) => route.fulfill(json({ require_auth: false, setup_complete: true })));
  await page.route(/\/api\/auth\/setup-required(?:\/|\?|$)/, (route) => route.fulfill(json({ required: false })));
  await page.route(/\/api\/auth\/me(?:\/|\?|$)/, (route) => route.fulfill({ ...json({ detail: 'No auth in synthetic browser fixture' }), status: 401 }));
  await page.route(/\/api\/session-start(?:\?|$)/, (route) => route.fulfill({ status: 204, body: '' }));
  await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill(json({ configured: true, url: 'http://synthetic.invalid' })));
  await page.route(/\/api\/channel-groups(?:\?|$)/, (route) => route.fulfill(json([])));
  await page.route(/\/api\/channels(?:\?|$)/, (route) => route.fulfill(json({ count: 0, next: null, previous: null, results: [] })));
  await page.route(/\/api\/stream-groups(?:\?|$)/, (route) => route.fulfill(json([])));
  await page.route(/\/api\/streams(?:\?|$)/, (route) => route.fulfill(json({ count: 0, next: null, previous: null, results: [] })));
  await page.route(/\/api\/streams\/stale-ids(?:\?|$)/, (route) => route.fulfill(json({ stale_stream_ids: [] })));
  await page.route(/\/api\/journal\/stats(?:\?|$)/, (route) => route.fulfill(json({ total_entries: 0, by_category: {}, by_action_type: {}, date_range: { oldest: null, newest: null } })));
  await page.route(/\/api\/journal(?:\?|$)/, (route) => route.fulfill(json({ count: 0, results: [], page: 1, page_size: 50, total_pages: 0 })));
  await page.route(/\/api\/m3u\/changes\/summary(?:\?|$)/, (route) => route.fulfill(json({ total_changes: 0, groups_added: 0, groups_removed: 0, streams_added: 0, streams_removed: 0, accounts_affected: [], since: null })));
  await page.route(/\/api\/m3u\/changes(?:\?|$)/, (route) => route.fulfill(json({ results: [], total: 0, page: 1, page_size: 50, total_pages: 0 })));
  await page.route(/\/api\/health(?:\?|$)/, (route) => route.fulfill(json({ status: 'healthy', version: '0.18.1-0075' })));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const servedCss = await page.locator('link[rel="stylesheet"]').evaluateAll(async (links) =>
    Promise.all(links.map((link) => fetch((link as HTMLLinkElement).href).then((response) => response.text()))));
  // This selector exists only in the checked-out 0074 eager bundle. Unlike
  // mocked health data, fetching the linked fingerprinted asset proves what
  // the browser was actually served.
  expect(servedCss.some((css) => css.includes('.streams-filter-select'))).toBe(true);
  await page.waitForSelector('.tab-navigation', { timeout: 30_000 });
  await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
  return page;
}

async function go(page: Page, route: typeof OWNERS[number]): Promise<void> {
  await page.evaluate((id) => { window.location.hash = `#${id}`; }, route.id);
  await page.waitForSelector('.tab-loading', { state: 'hidden', timeout: 60_000 }).catch(() => undefined);
  await page.waitForSelector(route.root, { timeout: 60_000 });
  await expect(page.locator(route.owner)).toHaveCount(route.count);
}

async function capture(page: Page, route: typeof OWNERS[number]) {
  await page.mouse.move(0, 0);
  const controls = page.locator(route.owner);
  const normal = await controls.evaluateAll((owners) => owners.map((owner) => {
    const trigger = owner.matches('.custom-select') ? owner.querySelector('.custom-select-trigger')! : owner.querySelector('.custom-select-trigger')!;
    const box = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        width: rect.width, height: rect.height, padding: style.padding,
        background: style.backgroundColor, border: style.border,
        radius: style.borderRadius, color: style.color, fontSize: style.fontSize,
      };
    };
    return { owner: box(owner), trigger: box(trigger) };
  }));

  let focused = null;
  let opened = null;
  let hovered = null;
  if (route.count > 0) {
    const firstTrigger = controls.first().locator('.custom-select-trigger');
    await firstTrigger.hover();
    hovered = await firstTrigger.evaluate((el) => {
      const style = getComputedStyle(el);
      return { border: style.border, boxShadow: style.boxShadow, color: style.color };
    });
    await firstTrigger.focus();
    focused = await firstTrigger.evaluate((el) => {
      const style = getComputedStyle(el);
      return { border: style.border, boxShadow: style.boxShadow, color: style.color };
    });
    await firstTrigger.click();
    await expect(firstTrigger).toHaveAttribute('aria-expanded', 'true');
    opened = await firstTrigger.evaluate((el) => {
      const style = getComputedStyle(el);
      return { border: style.border, boxShadow: style.boxShadow, color: style.color };
    });
    await page.keyboard.press('Escape');
  }

  const chrome = await page.locator('.header').evaluate((header) => {
    const rect = header.getBoundingClientRect();
    const slots = Array.from(header.querySelectorAll('[data-route-header-slot="controls"] *'));
    return {
      height: rect.height,
      scrollWidth: header.scrollWidth,
      clientWidth: header.clientWidth,
      clipped: slots.some((element) => {
        const child = element.getBoundingClientRect();
        return child.left < rect.left || child.right > rect.right || child.top < rect.top || child.bottom > rect.bottom;
      }),
    };
  });
  expect(chrome.scrollWidth).toBeLessThanOrEqual(chrome.clientWidth);
  expect(chrome.clipped).toBe(false);
  return { normal, hovered, focused, opened, headerHeight: chrome.height };
}

const OWNER_BOX = {
  width: 218, height: 48, padding: '8px', background: 'rgb(42, 42, 53)',
  border: '1px solid rgb(74, 74, 85)', radius: '4px',
  color: 'rgba(255, 255, 255, 0.95)', fontSize: '13px',
};

function expectApprovedChrome(reading: Awaited<ReturnType<typeof capture>>) {
  expect(reading.headerHeight).toBe(45);
  for (const box of reading.normal) expect(box.owner).toEqual(OWNER_BOX);
  if (reading.normal.length === 3) {
    for (const box of reading.normal) expect(box.trigger).toEqual({
      width: 200, height: 30, padding: '3px 10px', background: 'rgb(42, 42, 53)',
      border: '1px solid rgb(74, 74, 85)', radius: '6px',
      color: 'rgba(255, 255, 255, 0.95)', fontSize: '14px',
    });
    expect(reading.focused).toEqual({ border: '1px solid rgb(255, 255, 255)', boxShadow: 'rgba(100, 108, 255, 0.2) 0px 0px 0px 2px', color: 'rgba(255, 255, 255, 0.95)' });
    expect(reading.opened).toEqual(reading.focused);
  } else if (reading.normal.length === 4) {
    for (const box of reading.normal) expect(box.trigger).toEqual({
      width: 200, height: 30, padding: '6px 8px', background: 'rgba(0, 0, 0, 0)',
      border: '0px none rgba(255, 255, 255, 0.95)', radius: '6px',
      color: 'rgba(255, 255, 255, 0.95)', fontSize: '14px',
    });
    expect(reading.hovered?.color).toBe('rgb(255, 255, 255)');
    expect(reading.focused?.boxShadow).toBe('rgba(100, 108, 255, 0.2) 0px 0px 0px 2px');
    expect(reading.opened).toEqual({ border: '0px none rgb(255, 255, 255)', boxShadow: 'none', color: 'rgb(255, 255, 255)' });
  }
}

test.describe('filter select owner chrome is session-order invariant', () => {
  for (const viewport of VIEWPORTS) {
    test(`${viewport.width}x${viewport.height}: forward, revisit, and reverse chunks agree`, async ({ browser }) => {
      test.setTimeout(8 * 60_000);
      const forwardPage = await openApp(browser, viewport);
      const first: Record<string, Awaited<ReturnType<typeof capture>>> = {};
      for (const route of OWNERS) { await go(forwardPage, route); first[route.id] = await capture(forwardPage, route); }
      const revisit: typeof first = {};
      for (const route of OWNERS) { await go(forwardPage, route); revisit[route.id] = await capture(forwardPage, route); }
      await forwardPage.context().close();

      const reversePage = await openApp(browser, viewport);
      const reverse: typeof first = {};
      for (const route of [...OWNERS].reverse()) { await go(reversePage, route); reverse[route.id] = await capture(reversePage, route); }
      await reversePage.context().close();

      expect(revisit).toEqual(first);
      expect(reverse).toEqual(first);
      for (const route of OWNERS) expectApprovedChrome(first[route.id]);
    });
  }

  for (const width of [768, 600]) {
    test(`${width}px responsive owners apply without frozen-chrome overflow`, async ({ browser }) => {
      const page = await openApp(browser, { width, height: 900 });
      for (const route of OWNERS.slice(1)) {
        await go(page, route);
        const responsive = await page.locator(route.owner).first().evaluate((owner) => {
          const style = getComputedStyle(owner);
          return { width: owner.getBoundingClientRect().width, flexGrow: style.flexGrow, minWidth: style.minWidth };
        });
        if (route.id === 'm3u-changes') {
          expect(responsive.flexGrow).toBe('1');
          expect(responsive.minWidth).toBe('150px');
        }
        if (width === 600) expect(responsive.width).toBeGreaterThan(218);
        await capture(page, route);
      }
      await page.context().close();
    });
  }
});
