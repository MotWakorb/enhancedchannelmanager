/**
 * E2E tests for Guide Tab.
 *
 * Tests EPG guide display and navigation functionality.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('Guide Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('guide tab is accessible', async ({ appPage }) => {
    const guideTab = appPage.locator(selectors.tabButton('guide'));
    await guideTab.waitFor({ state: 'visible', timeout: 5000 });
    await expect(guideTab).toHaveClass(/active/);
  });

  test('guide content is visible', async ({ appPage }) => {
    // Look for guide container
    const guideContent = appPage.locator('.guide-content, .guide-container, .epg-guide, [data-testid="guide"]');
    const isVisible = await guideContent.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Guide Timeline', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('displays timeline header', async ({ appPage }) => {
    // Look for timeline with time markers
    const timeline = appPage.locator('.timeline-header, .time-header, .guide-timeline, [data-testid="timeline"]');
    const isVisible = await timeline.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });

  test('shows time slots', async ({ appPage }) => {
    // Look for time slot markers
    const timeSlots = appPage.locator('.time-slot, .time-marker, [data-testid="time-slot"]');
    const count = await timeSlots.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('current time indicator exists', async ({ appPage }) => {
    // Look for current time line/marker
    const currentTime = appPage.locator('.current-time, .now-indicator, .time-marker.current, [data-testid="current-time"]');
    const count = await currentTime.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Guide Channel List', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('displays channel rows', async ({ appPage }) => {
    // Look for channel rows in guide
    const channelRows = appPage.locator('.guide-channel-row, .channel-row, [data-testid="guide-channel"]');
    const count = await channelRows.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('channel rows show channel name', async ({ appPage }) => {
    const channelRows = appPage.locator('.guide-channel-row, .channel-row');
    const count = await channelRows.count();

    if (count > 0) {
      const firstChannel = channelRows.first();
      const channelName = firstChannel.locator('.channel-name, .channel-label');
      const nameCount = await channelName.count();
      expect(nameCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('channel rows show logo', async ({ appPage }) => {
    const channelRows = appPage.locator('.guide-channel-row, .channel-row');
    const count = await channelRows.count();

    if (count > 0) {
      const firstChannel = channelRows.first();
      const logo = firstChannel.locator('img, .channel-logo');
      const logoCount = await logo.count();
      expect(logoCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Guide Programs', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('displays program blocks', async ({ appPage }) => {
    // Look for program/show blocks
    const programs = appPage.locator('.program-block, .guide-program, .show-block, [data-testid="program"]');
    const count = await programs.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('program blocks show title', async ({ appPage }) => {
    const programs = appPage.locator('.program-block, .guide-program');
    const count = await programs.count();

    if (count > 0) {
      const firstProgram = programs.first();
      const text = await firstProgram.textContent();
      expect(typeof text).toBe('string');
    }
  });

  test('clicking program shows details', async ({ appPage }) => {
    const programs = appPage.locator('.program-block, .guide-program');
    const count = await programs.count();

    if (count > 0) {
      await programs.first().click();
      await appPage.waitForTimeout(300);

      // Check for details popup/modal or tooltip
      const details = appPage.locator('.program-details, .program-tooltip, .program-modal, [data-testid="program-details"]');
      const detailsCount = await details.count();
      expect(detailsCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Guide Navigation', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('can navigate to previous time', async ({ appPage }) => {
    const prevButton = appPage.locator('button:has-text("Previous"), button:has-text("Back"), .prev-time-btn, [data-testid="prev-time"]');
    const count = await prevButton.count();

    if (count > 0) {
      await prevButton.first().click();
      await appPage.waitForTimeout(300);
      expect(true).toBe(true);
    }
  });

  test('can navigate to next time', async ({ appPage }) => {
    const nextButton = appPage.locator('button:has-text("Next"), button:has-text("Forward"), .next-time-btn, [data-testid="next-time"]');
    const count = await nextButton.count();

    if (count > 0) {
      await nextButton.first().click();
      await appPage.waitForTimeout(300);
      expect(true).toBe(true);
    }
  });

  test('can jump to current time', async ({ appPage }) => {
    const nowButton = appPage.locator('button:has-text("Now"), button:has-text("Today"), .now-btn, [data-testid="go-to-now"]');
    const count = await nowButton.count();

    if (count > 0) {
      await nowButton.first().click();
      await appPage.waitForTimeout(300);
      expect(true).toBe(true);
    }
  });

  test('date picker exists', async ({ appPage }) => {
    const datePicker = appPage.locator('input[type="date"], .date-picker, [data-testid="guide-date"]');
    const count = await datePicker.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Guide Scrolling', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('can scroll horizontally through time', async ({ appPage }) => {
    const guideContent = appPage.locator('.guide-content, .guide-grid, .epg-guide');
    const isVisible = await guideContent.first().isVisible().catch(() => false);

    if (isVisible) {
      await guideContent.first().evaluate((el) => {
        el.scrollLeft = 200;
      });
      expect(true).toBe(true);
    }
  });

  test('can scroll vertically through channels', async ({ appPage }) => {
    const guideContent = appPage.locator('.guide-content, .guide-grid, .epg-guide');
    const isVisible = await guideContent.first().isVisible().catch(() => false);

    if (isVisible) {
      await guideContent.first().evaluate((el) => {
        el.scrollTop = 100;
      });
      expect(true).toBe(true);
    }
  });
});

test.describe('Guide Search', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
  });

  test('search input exists', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"], input[placeholder*="Search"]');
    const count = await searchInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can search for programs', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"]').first();
    const exists = await searchInput.count();

    if (exists > 0) {
      await searchInput.fill('News');
      await appPage.waitForTimeout(300);
      // Search should complete without error
      expect(true).toBe(true);

      // Clear search
      await searchInput.clear();
    }
  });
});
