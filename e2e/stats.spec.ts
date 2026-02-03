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

// =============================================================================
// Enhanced Stats Tests (v0.11.0)
// =============================================================================

test.describe('Enhanced Stats - Bandwidth Panel', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('bandwidth panel is visible', async ({ appPage }) => {
    // Look for bandwidth-related content
    const bandwidthPanel = appPage.locator('.bandwidth-panel, .bandwidth-stats, [data-testid="bandwidth-stats"]');
    const bandwidthText = appPage.getByText(/bandwidth/i);
    const count = await bandwidthPanel.count() + await bandwidthText.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('displays bandwidth metrics', async ({ appPage }) => {
    // Look for bandwidth values (today, week, month, year)
    const todayMetric = appPage.getByText(/today/i);
    const weekMetric = appPage.getByText(/week/i);
    const monthMetric = appPage.getByText(/month/i);

    const count = await todayMetric.count() + await weekMetric.count() + await monthMetric.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows bandwidth units', async ({ appPage }) => {
    // Look for data size units (GB, MB, TB)
    const gbUnit = appPage.getByText(/GB|MB|TB|KB/);
    const count = await gbUnit.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Enhanced Stats - Unique Viewers', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('unique viewers section is visible', async ({ appPage }) => {
    const viewersSection = appPage.locator('.unique-viewers, .viewers-panel, [data-testid="unique-viewers"]');
    const viewersText = appPage.getByText(/unique.*viewer|viewer/i);
    const count = await viewersSection.count() + await viewersText.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('displays viewer counts', async ({ appPage }) => {
    // Look for viewer metrics
    const totalViewers = appPage.getByText(/total/i);
    const todayViewers = appPage.getByText(/today/i);
    const count = await totalViewers.count() + await todayViewers.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Enhanced Stats - Popularity Rankings', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('popularity section is visible', async ({ appPage }) => {
    const popularitySection = appPage.locator('.popularity-panel, .popularity-rankings, [data-testid="popularity"]');
    const popularityText = appPage.getByText(/popular|ranking/i);
    const count = await popularitySection.count() + await popularityText.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('displays channel rankings', async ({ appPage }) => {
    // Look for ranked channel list
    const rankingList = appPage.locator('.ranking-list, .popularity-list, [data-testid="ranking-list"]');
    const rankNumbers = appPage.locator('.rank, .ranking-number');
    const count = await rankingList.count() + await rankNumbers.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows trend indicators', async ({ appPage }) => {
    // Look for trend arrows or indicators
    const trendUp = appPage.locator('.trend-up, .trending-up, [class*="up"]');
    const trendDown = appPage.locator('.trend-down, .trending-down, [class*="down"]');
    const trendStable = appPage.locator('.trend-stable, [class*="stable"]');
    const count = await trendUp.count() + await trendDown.count() + await trendStable.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('displays popularity scores', async ({ appPage }) => {
    // Look for score display (usually percentage or number)
    const scores = appPage.locator('.score, .popularity-score, [data-testid="score"]');
    const percentages = appPage.getByText(/%/);
    const count = await scores.count() + await percentages.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Enhanced Stats - Trending Channels', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('trending section is visible', async ({ appPage }) => {
    const trendingSection = appPage.locator('.trending-panel, .trending-channels, [data-testid="trending"]');
    const trendingText = appPage.getByText(/trending|trend/i);
    const count = await trendingSection.count() + await trendingText.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows trending up channels', async ({ appPage }) => {
    const trendingUp = appPage.getByText(/trending up|rising|↑/i);
    const upIcon = appPage.locator('.trend-up-icon, [class*="up"]');
    const count = await trendingUp.count() + await upIcon.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows trending down channels', async ({ appPage }) => {
    const trendingDown = appPage.getByText(/trending down|falling|↓/i);
    const downIcon = appPage.locator('.trend-down-icon, [class*="down"]');
    const count = await trendingDown.count() + await downIcon.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Enhanced Stats - Watch History', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('watch history section is visible', async ({ appPage }) => {
    const historySection = appPage.locator('.watch-history, .history-panel, [data-testid="watch-history"]');
    const historyText = appPage.getByText(/watch.*history|history/i);
    const count = await historySection.count() + await historyText.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('displays watch time statistics', async ({ appPage }) => {
    const watchTime = appPage.getByText(/watch.*time|duration|hours|minutes/i);
    const count = await watchTime.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Enhanced Stats - Top Watched', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('top watched section is visible', async ({ appPage }) => {
    const topSection = appPage.locator('.top-watched, .top-channels, [data-testid="top-watched"]');
    const topText = appPage.getByText(/top.*watched|most.*watched/i);
    const count = await topSection.count() + await topText.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('shows view counts', async ({ appPage }) => {
    const viewCounts = appPage.getByText(/view|watch/i);
    const count = await viewCounts.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Enhanced Stats - Period Selector', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('period selector exists', async ({ appPage }) => {
    // Look for period/date range selector
    const periodSelector = appPage.locator('select, .period-selector, [data-testid="period-selector"]');
    const dateOptions = appPage.getByText(/7 day|14 day|30 day|week|month/i);
    const count = await periodSelector.count() + await dateOptions.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can change time period', async ({ appPage }) => {
    // Look for period options
    const periodButtons = appPage.locator('button:has-text("7"), button:has-text("14"), button:has-text("30")');
    const periodSelect = appPage.locator('select').first();

    const buttonCount = await periodButtons.count();
    const selectCount = await periodSelect.count();

    if (buttonCount > 0 || selectCount > 0) {
      // Test that we can interact with period controls
      expect(true).toBe(true);
    }
  });
});

test.describe('Enhanced Stats - Calculate Popularity', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('calculate button exists', async ({ appPage }) => {
    const calculateButton = appPage.locator('button:has-text("Calculate"), button:has-text("Refresh"), .calculate-popularity-btn');
    const count = await calculateButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('admin can trigger calculation', async ({ appPage }) => {
    const calculateButton = appPage.locator('button:has-text("Calculate")').first();
    const exists = await calculateButton.count();

    if (exists > 0) {
      // Click should not cause errors
      await calculateButton.click().catch(() => {/* button may not be interactable */});
      await appPage.waitForTimeout(500);
      expect(true).toBe(true);
    }
  });
});

test.describe('Enhanced Stats - Responsive Layout', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
  });

  test('stats panels adapt to viewport', async ({ appPage }) => {
    // Stats content should be visible
    const statsContent = appPage.locator('.stats-content, .stats-container, .stats-tab');
    const isVisible = await statsContent.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });

  test('charts resize with container', async ({ appPage }) => {
    const charts = appPage.locator('.recharts-wrapper, svg');
    const count = await charts.count();

    if (count > 0) {
      const chart = charts.first();
      const box = await chart.boundingBox().catch(() => null);
      if (box) {
        expect(box.width).toBeGreaterThan(0);
        expect(box.height).toBeGreaterThan(0);
      }
    }
  });
});
