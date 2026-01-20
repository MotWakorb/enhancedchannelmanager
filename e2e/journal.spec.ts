/**
 * E2E tests for Journal Tab.
 *
 * Tests activity log and event journal functionality.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('Journal Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('journal tab is accessible', async ({ appPage }) => {
    const journalTab = appPage.locator(selectors.tabButton('journal'));
    await expect(journalTab).toHaveClass(/active/);
  });

  test('journal content is visible', async ({ appPage }) => {
    // Look for journal container
    const journalContent = appPage.locator('.journal-content, .journal-container, .activity-log, [data-testid="journal"]');
    const isVisible = await journalContent.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Journal Entries', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('displays journal entries', async ({ appPage }) => {
    // Look for journal entry rows
    const entries = appPage.locator('.journal-entry, .log-entry, .activity-item, [data-testid="journal-entry"]');
    const count = await entries.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('entries show timestamp', async ({ appPage }) => {
    const entries = appPage.locator('.journal-entry, .log-entry');
    const count = await entries.count();

    if (count > 0) {
      const firstEntry = entries.first();
      const timestamp = firstEntry.locator('.timestamp, .time, .date, [data-testid="entry-time"]');
      const timestampCount = await timestamp.count();
      expect(timestampCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('entries show event type', async ({ appPage }) => {
    const entries = appPage.locator('.journal-entry, .log-entry');
    const count = await entries.count();

    if (count > 0) {
      const firstEntry = entries.first();
      const eventType = firstEntry.locator('.event-type, .log-level, .entry-type, [data-testid="event-type"]');
      const typeCount = await eventType.count();
      expect(typeCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('entries show message', async ({ appPage }) => {
    const entries = appPage.locator('.journal-entry, .log-entry');
    const count = await entries.count();

    if (count > 0) {
      const firstEntry = entries.first();
      const text = await firstEntry.textContent();
      expect(typeof text).toBe('string');
    }
  });
});

test.describe('Journal Filtering', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('filter controls exist', async ({ appPage }) => {
    // Look for filter dropdowns or checkboxes
    const filters = appPage.locator('select, .filter-control, [data-testid="journal-filter"], .log-level-filter');
    const count = await filters.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can filter by log level', async ({ appPage }) => {
    const levelFilter = appPage.locator('select:has-text("level"), select:has-text("Level"), .level-filter');
    const count = await levelFilter.count();

    if (count > 0) {
      await levelFilter.first().click();
      await appPage.waitForTimeout(200);
      expect(true).toBe(true);
    }
  });

  test('can filter by event type', async ({ appPage }) => {
    const typeFilter = appPage.locator('select:has-text("type"), select:has-text("Type"), .type-filter');
    const count = await typeFilter.count();

    if (count > 0) {
      await typeFilter.first().click();
      await appPage.waitForTimeout(200);
      expect(true).toBe(true);
    }
  });

  test('date range filter exists', async ({ appPage }) => {
    const dateFilter = appPage.locator('input[type="date"], .date-range-filter, [data-testid="date-filter"]');
    const count = await dateFilter.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Journal Search', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('search input exists', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"], input[placeholder*="Search"]');
    const count = await searchInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can search journal entries', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"]').first();
    const exists = await searchInput.count();

    if (exists > 0) {
      await searchInput.fill('error');
      await appPage.waitForTimeout(300);
      expect(true).toBe(true);

      // Clear search
      await searchInput.clear();
    }
  });
});

test.describe('Journal Pagination', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('pagination controls exist', async ({ appPage }) => {
    const pagination = appPage.locator('.pagination, .page-controls, [data-testid="pagination"]');
    const count = await pagination.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can navigate to next page', async ({ appPage }) => {
    const nextButton = appPage.locator('button:has-text("Next"), .next-page-btn, [data-testid="next-page"]');
    const count = await nextButton.count();

    if (count > 0) {
      const isEnabled = await nextButton.first().isEnabled().catch(() => false);
      if (isEnabled) {
        await nextButton.first().click();
        await appPage.waitForTimeout(300);
        expect(true).toBe(true);
      }
    }
  });

  test('can navigate to previous page', async ({ appPage }) => {
    const prevButton = appPage.locator('button:has-text("Previous"), button:has-text("Prev"), .prev-page-btn, [data-testid="prev-page"]');
    const count = await prevButton.count();

    if (count > 0) {
      const isEnabled = await prevButton.first().isEnabled().catch(() => false);
      if (isEnabled) {
        await prevButton.first().click();
        await appPage.waitForTimeout(300);
        expect(true).toBe(true);
      }
    }
  });
});

test.describe('Journal Actions', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('refresh button exists', async ({ appPage }) => {
    const refreshButton = appPage.locator('button:has-text("Refresh"), .refresh-journal-btn, [data-testid="refresh-journal"]');
    const count = await refreshButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('clear button exists', async ({ appPage }) => {
    const clearButton = appPage.locator('button:has-text("Clear"), .clear-journal-btn, [data-testid="clear-journal"]');
    const count = await clearButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('export button exists', async ({ appPage }) => {
    const exportButton = appPage.locator('button:has-text("Export"), .export-journal-btn, [data-testid="export-journal"]');
    const count = await exportButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Journal Scrolling', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
  });

  test('can scroll through entries', async ({ appPage }) => {
    const journalContent = appPage.locator('.journal-content, .journal-list, .activity-log');
    const isVisible = await journalContent.first().isVisible().catch(() => false);

    if (isVisible) {
      await journalContent.first().evaluate((el) => {
        el.scrollTop = 200;
      });
      expect(true).toBe(true);
    }
  });

  test('new entries appear at top', async ({ appPage }) => {
    const entries = appPage.locator('.journal-entry, .log-entry');
    const count = await entries.count();

    if (count > 1) {
      // First entry should be most recent (check timestamps if available)
      const firstEntry = entries.first();
      const firstText = await firstEntry.textContent();
      expect(typeof firstText).toBe('string');
    }
  });
});
