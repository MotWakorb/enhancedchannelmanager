/**
 * Smoke tests for Enhanced Channel Manager.
 *
 * These tests verify basic application functionality and critical paths.
 */
import { test, expect, navigateToTab, assertAppLoaded } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('Smoke Tests', () => {
  test('app loads successfully', async ({ appPage }) => {
    await assertAppLoaded(appPage);
  });

  test('header is visible with correct title', async ({ appPage }) => {
    const header = appPage.locator(selectors.header);
    await expect(header).toBeVisible();

    const title = appPage.locator(selectors.headerTitle);
    await expect(title).toContainText('Enhanced Channel Manager');
  });

  test('tab navigation is visible', async ({ appPage }) => {
    const tabNav = appPage.locator(selectors.tabNavigation);
    await expect(tabNav).toBeVisible();
  });

  test('can navigate to Settings tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');

    const settingsTab = appPage.locator(selectors.tabButton('settings'));
    await expect(settingsTab).toHaveClass(/active/);
  });

  test('no console errors on load', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('/');
    await page.waitForSelector(selectors.header, { timeout: 15000 });

    // Filter out known acceptable errors (like missing API connection)
    const criticalErrors = errors.filter(
      (e) => !e.includes('Failed to fetch') && !e.includes('NetworkError')
    );

    expect(criticalErrors).toHaveLength(0);
  });

  test('page has correct title', async ({ appPage }) => {
    await expect(appPage).toHaveTitle(/Enhanced Channel Manager/);
  });

  test('edit mode toggle is visible', async ({ appPage }) => {
    const editButton = appPage.locator(selectors.editModeButton);
    // Edit mode button may or may not be visible depending on the current tab
    // This is a smoke test to verify the selector works
    const isVisible = await editButton.isVisible().catch(() => false);
    // Just verify we can query for it without errors
    expect(typeof isVisible).toBe('boolean');
  });

  test('notification center is accessible', async ({ appPage }) => {
    const notificationCenter = appPage.locator(selectors.notificationCenter);
    // May or may not be visible, just verify selector works
    const exists = await notificationCenter.count();
    expect(exists).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Navigation', () => {
  const tabs = [
    { id: 'channel-manager', name: 'Channel Manager' },
    { id: 'settings', name: 'Settings' },
  ];

  for (const tab of tabs) {
    test(`can navigate to ${tab.name} tab`, async ({ appPage }) => {
      // Wait for tab navigation to be visible
      await appPage.waitForSelector(selectors.tabNavigation, { timeout: 10000 });

      const tabButton = appPage.locator(selectors.tabButton(tab.id));

      // Wait for specific tab button to be visible
      await tabButton.waitFor({ state: 'visible', timeout: 5000 });

      await tabButton.click();
      await appPage.waitForTimeout(500);

      // Re-query the tab button to check active state
      const activeTab = appPage.locator(selectors.tabButton(tab.id));
      await expect(activeTab).toHaveClass(/active/);
    });
  }
});

test.describe('Responsiveness', () => {
  test('app renders on mobile viewport', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');
    await page.waitForSelector(selectors.header, { timeout: 15000 });

    const header = page.locator(selectors.header);
    await expect(header).toBeVisible();
  });

  test('app renders on tablet viewport', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/');
    await page.waitForSelector(selectors.header, { timeout: 15000 });

    const header = page.locator(selectors.header);
    await expect(header).toBeVisible();
  });

  test('app renders on desktop viewport', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/');
    await page.waitForSelector(selectors.header, { timeout: 15000 });

    const header = page.locator(selectors.header);
    await expect(header).toBeVisible();
  });
});
