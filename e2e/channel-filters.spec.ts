/**
 * E2E tests for Missing Data filter options in the Channels Pane.
 *
 * Tests the filter dropdown's "Missing Data" section: Missing Logo,
 * Missing TVG-ID, Missing EPG Data, and Missing Gracenote filters.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

const filterButtonSelector = '.filter-settings-button';
const filterMenuSelector = '.filter-settings-menu';
const filterSeparatorSelector = '.filter-settings-separator';
const filterSubheaderSelector = '.filter-settings-subheader';

test.describe('Missing Data Filters - UI', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('filter dropdown contains Missing Data section with separator and subheader', async ({ appPage }) => {
    // Open the filter dropdown
    const filterButton = appPage.locator(filterButtonSelector);
    await expect(filterButton).toBeVisible();
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    await expect(filterMenu).toBeVisible();

    // Check separator exists
    const separator = filterMenu.locator(filterSeparatorSelector);
    await expect(separator).toBeVisible();

    // Check "Missing Data" subheader exists
    const subheader = filterMenu.locator(filterSubheaderSelector);
    await expect(subheader).toBeVisible();
    await expect(subheader).toHaveText('Missing Data');
  });

  test('filter dropdown has all 4 missing data checkboxes', async ({ appPage }) => {
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    await expect(filterMenu).toBeVisible();

    // Verify all 4 missing data filter labels exist
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Missing TVG-ID' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Missing EPG Data' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Gracenote' })).toBeVisible();
  });

  test('missing data checkboxes are unchecked by default', async ({ appPage }) => {
    // Clear any saved filter state from localStorage
    await appPage.evaluate(() => localStorage.removeItem('channelListFilters'));
    await appPage.reload();
    await appPage.waitForSelector(selectors.channelsPane, { timeout: 10000 });

    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    await expect(filterMenu).toBeVisible();

    // All 4 checkboxes should be unchecked by default
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');
    const missingTvgIdCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing TVG-ID' }).locator('input[type="checkbox"]');
    const missingEpgCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing EPG Data' }).locator('input[type="checkbox"]');
    const missingGracenoteCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Gracenote' }).locator('input[type="checkbox"]');

    await expect(missingLogoCheckbox).not.toBeChecked();
    await expect(missingTvgIdCheckbox).not.toBeChecked();
    await expect(missingEpgCheckbox).not.toBeChecked();
    await expect(missingGracenoteCheckbox).not.toBeChecked();
  });

  test('checking a filter checkbox toggles it on', async ({ appPage }) => {
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');

    // Check it
    await missingLogoCheckbox.check();
    await expect(missingLogoCheckbox).toBeChecked();

    // Uncheck it
    await missingLogoCheckbox.uncheck();
    await expect(missingLogoCheckbox).not.toBeChecked();
  });

  test('existing group filter checkboxes still present above separator', async ({ appPage }) => {
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    await expect(filterMenu).toBeVisible();

    // Existing group filters should still be present
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Show Empty Groups' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Show Newly Created Groups' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Show Provider Groups' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Show Manual Groups' })).toBeVisible();
    await expect(filterMenu.locator('label.filter-settings-option', { hasText: 'Show Auto Channel Groups' })).toBeVisible();
  });
});

test.describe('Missing Data Filters - Active Indicator', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('filter button has no filter-active class when no missing data filters are checked', async ({ appPage }) => {
    // Clear filters
    await appPage.evaluate(() => localStorage.removeItem('channelListFilters'));
    await appPage.reload();
    await appPage.waitForSelector(selectors.channelsPane, { timeout: 10000 });

    const filterButton = appPage.locator(filterButtonSelector);
    await expect(filterButton).not.toHaveClass(/filter-active/);
  });

  test('filter button gets filter-active class when a missing data filter is checked', async ({ appPage }) => {
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');

    await missingLogoCheckbox.check();

    // Close the menu by clicking elsewhere
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });

    // Button should now have the filter-active class
    await expect(filterButton).toHaveClass(/filter-active/);
  });

  test('filter-active class removed when all missing data filters unchecked', async ({ appPage }) => {
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');

    // Check then uncheck
    await missingLogoCheckbox.check();
    await expect(filterButton).toHaveClass(/filter-active/);

    await missingLogoCheckbox.uncheck();
    await expect(filterButton).not.toHaveClass(/filter-active/);
  });
});

test.describe('Missing Data Filters - Filtering Behavior', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('enabling a missing data filter reduces or changes the channel list', async ({ appPage }) => {
    // Get the initial channel count
    const channelItems = appPage.locator(selectors.channelItem);
    const initialCount = await channelItems.count();

    if (initialCount === 0) {
      test.skip();
      return;
    }

    // Open filter dropdown and enable "Missing Logo" filter
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');
    await missingLogoCheckbox.check();

    // Close the menu
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });
    await appPage.waitForTimeout(500);

    // Channel count should have changed (either fewer or same if all are missing logos)
    const filteredCount = await channelItems.count();
    // We can't predict exact count, but filteredCount should be <= initialCount
    expect(filteredCount).toBeLessThanOrEqual(initialCount);

    // Clean up: uncheck the filter
    await filterButton.click();
    await filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]').uncheck();
  });

  test('disabling all missing data filters restores the full channel list', async ({ appPage }) => {
    const channelItems = appPage.locator(selectors.channelItem);
    const initialCount = await channelItems.count();

    if (initialCount === 0) {
      test.skip();
      return;
    }

    const filterButton = appPage.locator(filterButtonSelector);

    // Enable a filter
    await filterButton.click();
    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');
    await missingLogoCheckbox.check();

    // Close menu and wait for filter to apply
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });
    await appPage.waitForTimeout(500);

    // Now disable the filter
    await filterButton.click();
    await filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]').uncheck();

    // Close menu and wait
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });
    await appPage.waitForTimeout(500);

    // Count should be back to original
    const restoredCount = await channelItems.count();
    expect(restoredCount).toBe(initialCount);
  });

  test('multiple filters use OR logic (union of missing data)', async ({ appPage }) => {
    const channelItems = appPage.locator(selectors.channelItem);
    const initialCount = await channelItems.count();

    if (initialCount === 0) {
      test.skip();
      return;
    }

    const filterButton = appPage.locator(filterButtonSelector);

    // Enable first filter only
    await filterButton.click();
    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');
    const missingTvgIdCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing TVG-ID' }).locator('input[type="checkbox"]');

    await missingLogoCheckbox.check();
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });
    await appPage.waitForTimeout(500);
    const countWithLogo = await channelItems.count();

    // Enable second filter (OR logic means count should be >= single filter)
    await filterButton.click();
    await missingTvgIdCheckbox.check();
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });
    await appPage.waitForTimeout(500);
    const countWithBoth = await channelItems.count();

    // With OR logic, adding another filter should show >= channels as single filter
    expect(countWithBoth).toBeGreaterThanOrEqual(countWithLogo);

    // Clean up
    await filterButton.click();
    await missingLogoCheckbox.uncheck();
    await missingTvgIdCheckbox.uncheck();
  });

  test('each missing data filter can be toggled independently', async ({ appPage }) => {
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);

    const filters = [
      'Missing Logo',
      'Missing TVG-ID',
      'Missing EPG Data',
      'Missing Gracenote',
    ];

    for (const filterLabel of filters) {
      const checkbox = filterMenu.locator('label.filter-settings-option', { hasText: filterLabel }).locator('input[type="checkbox"]');

      // Toggle on
      await checkbox.check();
      await expect(checkbox).toBeChecked();

      // Toggle off
      await checkbox.uncheck();
      await expect(checkbox).not.toBeChecked();
    }
  });
});

test.describe('Missing Data Filters - Persistence', () => {
  test('filter state persists in localStorage', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');

    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    const missingLogoCheckbox = filterMenu.locator('label.filter-settings-option', { hasText: 'Missing Logo' }).locator('input[type="checkbox"]');

    // Enable the filter
    await missingLogoCheckbox.check();

    // Check localStorage
    const savedFilters = await appPage.evaluate(() => {
      const raw = localStorage.getItem('channelListFilters');
      return raw ? JSON.parse(raw) : null;
    });

    expect(savedFilters).not.toBeNull();
    expect(savedFilters.filterMissingLogo).toBe(true);

    // Clean up
    await missingLogoCheckbox.uncheck();
  });

  test('filter state survives page reload', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');

    // Enable a filter
    const filterButton = appPage.locator(filterButtonSelector);
    await filterButton.click();

    const filterMenu = appPage.locator(filterMenuSelector);
    await filterMenu.locator('label.filter-settings-option', { hasText: 'Missing EPG Data' }).locator('input[type="checkbox"]').check();

    // Close menu
    await appPage.locator(selectors.channelsPane).click({ position: { x: 5, y: 5 } });

    // Reload the page
    await appPage.reload();
    await appPage.waitForSelector(selectors.channelsPane, { timeout: 10000 });

    // The filter button should still show active state
    await expect(appPage.locator(filterButtonSelector)).toHaveClass(/filter-active/);

    // Open menu and verify checkbox is still checked
    await appPage.locator(filterButtonSelector).click();
    const reloadedMenu = appPage.locator(filterMenuSelector);
    const epgCheckbox = reloadedMenu.locator('label.filter-settings-option', { hasText: 'Missing EPG Data' }).locator('input[type="checkbox"]');
    await expect(epgCheckbox).toBeChecked();

    // Clean up
    await epgCheckbox.uncheck();
  });
});
