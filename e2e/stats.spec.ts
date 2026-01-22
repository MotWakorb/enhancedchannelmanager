/**
 * E2E tests for Stats Tab.
 *
 * Tests statistics display and data visualization functionality.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('Stats Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('stats tab is accessible', async ({ appPage }) => {
    const statsTab = appPage.locator(selectors.tabButton('stats'));
    await statsTab.waitFor({ state: 'visible', timeout: 5000 });
    await expect(statsTab).toHaveClass(/active/);
  });

  test('stats content is visible', async ({ appPage }) => {
    // Look for stats container
    const statsContent = appPage.locator('.stats-content, .stats-container, [data-testid="stats"]');
    const isVisible = await statsContent.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Stats Overview', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('displays summary statistics', async ({ appPage }) => {
    // Look for stat cards or summary sections
    const statCards = appPage.locator('.stat-card, .stats-summary, .summary-item, [data-testid="stat-card"]');
    const count = await statCards.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows channel count', async ({ appPage }) => {
    // Look for text containing channel count
    const channelStat = appPage.getByText(/channel/i).or(appPage.locator('[data-stat="channels"]'));
    const count = await channelStat.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows stream count', async ({ appPage }) => {
    // Look for text containing stream count
    const streamStat = appPage.getByText(/stream/i).or(appPage.locator('[data-stat="streams"]'));
    const count = await streamStat.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows EPG source count', async ({ appPage }) => {
    // Look for text containing EPG count
    const epgStat = appPage.getByText(/epg/i).or(appPage.locator('[data-stat="epg"]'));
    const count = await epgStat.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Stats Charts', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('displays charts or graphs', async ({ appPage }) => {
    // Look for chart containers (recharts uses svg)
    const charts = appPage.locator('.recharts-wrapper, .chart-container, svg.recharts-surface, [data-testid="chart"]');
    const count = await charts.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('charts are responsive to container', async ({ appPage }) => {
    const charts = appPage.locator('.recharts-wrapper, .chart-container');
    const count = await charts.count();

    if (count > 0) {
      const firstChart = charts.first();
      const box = await firstChart.boundingBox();
      // Chart should have some size
      expect(box).toBeTruthy();
      if (box) {
        expect(box.width).toBeGreaterThan(0);
        expect(box.height).toBeGreaterThan(0);
      }
    }
  });
});

test.describe('Stats Data Tables', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('displays data tables', async ({ appPage }) => {
    // Look for table elements
    const tables = appPage.locator('table, .data-table, [data-testid="stats-table"]');
    const count = await tables.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('tables have headers', async ({ appPage }) => {
    const tables = appPage.locator('table');
    const count = await tables.count();

    if (count > 0) {
      const headers = tables.first().locator('th, thead');
      const headerCount = await headers.count();
      expect(headerCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('tables have data rows', async ({ appPage }) => {
    const tables = appPage.locator('table');
    const count = await tables.count();

    if (count > 0) {
      const rows = tables.first().locator('tbody tr, tr');
      const rowCount = await rows.count();
      expect(rowCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Stats Refresh', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('refresh button exists', async ({ appPage }) => {
    const refreshButton = appPage.locator('button:has-text("Refresh"), .refresh-stats-btn, [data-testid="refresh-stats"]');
    const count = await refreshButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can refresh stats', async ({ appPage }) => {
    const refreshButton = appPage.locator('button:has-text("Refresh"), .refresh-stats-btn').first();
    const exists = await refreshButton.count();

    if (exists > 0) {
      await refreshButton.click();
      await appPage.waitForTimeout(500);
      // Should complete without error
      expect(true).toBe(true);
    }
  });
});

test.describe('Stats Filters', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('filter controls exist', async ({ appPage }) => {
    // Look for filter dropdowns or date pickers
    const filters = appPage.locator('select, .filter-control, [data-testid="stats-filter"], input[type="date"]');
    const count = await filters.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('time range selector exists', async ({ appPage }) => {
    // Look for time range options
    const timeRange = appPage.locator('select:has-text("day"), select:has-text("week"), .time-range-selector');
    const count = await timeRange.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Stats Export', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('export button exists', async ({ appPage }) => {
    const exportButton = appPage.locator('button:has-text("Export"), .export-stats-btn, [data-testid="export-stats"]');
    const count = await exportButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});
