import { expect, test } from './fixtures/base';
import type { Locator } from '@playwright/test';

test.skip(
  process.env.E2E_EXACT_BUILD !== 'true' || process.env.E2E_START_SERVER !== 'true',
  'GH962 requires the isolated exact preview build, never a live API',
);

const channels = ['Radio: Family Talk', 'Radio: Freakonomics Radio Network', 'Radio: Howard 100']
  .map((name, index) => ({
    id: index + 1, name, channel_number: 3158 + index, channel_group_id: 1,
    tvg_id: null, tvc_guide_stationid: null, epg_data_id: null, streams: [],
    stream_profile_id: null, uuid: `0000000${index + 1}-0000-4000-8000-000000000000`,
    logo_id: null, auto_created: false, auto_created_by: null, auto_created_by_name: null,
  }));

async function expectFullGlyph(selector: Locator) {
  await expect(async () => {
    const geometry = await selector.evaluate((button) => {
      const icon = button.querySelector('.material-icons')!;
      const rect = icon.getBoundingClientRect();
      const style = getComputedStyle(icon);
      const clippedBy: string[] = [];
      for (let ancestor: Element | null = button; ancestor; ancestor = ancestor.parentElement) {
        const bounds = ancestor.getBoundingClientRect();
        const css = getComputedStyle(ancestor);
        if ((/(hidden|clip|auto|scroll)/.test(css.overflowX) &&
            (rect.left < bounds.left - 0.5 || rect.right > bounds.right + 0.5)) ||
            (/(hidden|clip|auto|scroll)/.test(css.overflowY) &&
            (rect.top < bounds.top - 0.5 || rect.bottom > bounds.bottom + 0.5))) {
          clippedBy.push(ancestor.className);
        }
      }
      const bounds = button.getBoundingClientRect();
      return {
        width: rect.width, height: rect.height, em: parseFloat(style.fontSize), clippedBy,
        targetWidth: bounds.width, targetHeight: bounds.height,
        contained: rect.left >= bounds.left && rect.right <= bounds.right &&
          rect.top >= bounds.top && rect.bottom <= bounds.bottom,
        hit: [rect.left + 1, rect.right - 1].every((x) =>
          button.contains(document.elementFromPoint(x, rect.top + rect.height / 2))),
      };
    });
    expect(geometry.width, JSON.stringify(geometry)).toBeGreaterThanOrEqual(geometry.em - 0.5);
    expect(geometry.height).toBeGreaterThanOrEqual(geometry.em - 0.5);
    expect(geometry.clippedBy).toEqual([]);
    expect(geometry.contained).toBe(true);
    expect(geometry.hit).toBe(true);
    expect(geometry.targetWidth).toBe(24);
    expect(geometry.targetHeight).toBe(34);
  }).toPass({ timeout: 5000 });
}

for (const viewport of [
  { width: 1920, height: 1080 }, { width: 1280, height: 720 }, { width: 390, height: 844 },
]) {
  test(`GH962 expanded edit checkboxes remain whole and selectable at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    // Same backendless route pattern as the existing Edit Mode browser guards.
    // Catch every API request so even unexpected writes cannot reach a backend.
    await page.route(/\/api\//, async (route) => {
      const path = new URL(route.request().url()).pathname;
      const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
      if (path === '/api/auth/me') return json({ detail: 'No preview session' }, 401);
      if (path === '/api/auth/status') return json({ require_auth: false, setup_complete: true, dispatcharr_enabled: false });
      if (path === '/api/auth/setup-required') return json({ required: false });
      if (path === '/api/session-start' || path === '/api/auth/refresh') return route.fulfill({ status: 204 });
      if (route.request().method() !== 'GET') return route.abort();
      if (path === '/api/settings') return json({ configured: true, url: 'http://synthetic.invalid', default_channel_profile_ids: [] });
      if (path === '/api/channels') return json({ count: channels.length, next: null, previous: null, results: channels });
      if (path === '/api/channel-groups') return json([{ id: 1, name: 'Radio', channel_count: channels.length, is_auto_sync: false }]);
      if (path === '/api/channel-profiles') return json([{ id: 1, name: 'Default', channels: [] }]);
      if (path === '/api/channel-merges') return json({ results: [], count: 0 });
      if (path === '/api/profile-conflict-reviews') return json({ reviews: [], total: 0 });
      if (path === '/api/streams/stale-ids') return json({ stale_stream_ids: [] });
      if (path === '/api/streams' || path === '/api/logos' || path === '/api/channels/logos') return json({ count: 0, next: null, previous: null, results: [] });
      if (path === '/api/notifications') return json({ notifications: [], unread_count: 0, results: [] });
      return json([]);
    });
    await page.goto('/#channel-manager');
    await expect(page.locator('.channels-pane')).toBeVisible();
    if (viewport.width < 768) {
      // Use the shipped controls, not injected layout overrides, at phone width.
      await page.locator('.sidebar-brand-toggle').click();
      await page.getByRole('separator').press('End');
    }
    await page.locator('.enter-edit-mode-btn').click();
    await expect(page.locator('.edit-mode-done-btn')).toBeVisible();
    const group = page.locator('.channels-pane .group-header').filter({ hasText: 'Radio' }).locator('.group-toggle-btn');
    await group.click();
    const selectors = page.locator('.channels-pane .channel-select-indicator');
    await expect(selectors).toHaveCount(channels.length);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: testInfo.outputPath('unchecked.png'), fullPage: true });
    for (const selector of await selectors.all()) {
      await expect(selector).not.toBeChecked();
      await expectFullGlyph(selector);
    }
    const first = selectors.first();
    await first.click();
    await expect(first).toBeChecked();
    await expect(first.locator('.material-icons')).toHaveText('check_box');
    await expectFullGlyph(first);
    await expect(selectors.nth(1)).not.toBeChecked();
    await first.focus();
    await first.press('Space');
    await expect(first).not.toBeChecked();
    await first.press('Enter');
    await expect(first).toBeChecked();
    await selectors.nth(1).click();
    await expect(selectors.nth(1)).toBeChecked();
    await expect(first).toBeChecked();
    await group.click();
    await expect(selectors).toHaveCount(0);
    await group.click();
    await expect(selectors).toHaveCount(channels.length);
    await expect(first).toBeChecked();
    await expect(selectors.nth(1)).toBeChecked();
    for (const selector of await selectors.all()) await expectFullGlyph(selector);
    await first.focus();
    await expect(first).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath('checked.png'), fullPage: true });
  });
}
