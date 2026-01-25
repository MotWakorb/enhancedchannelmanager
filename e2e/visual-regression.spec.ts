/**
 * Visual Regression Tests for CSS Changes
 *
 * These tests capture baseline screenshots of key UI components and compare
 * against them after CSS changes to detect unintended visual regressions.
 *
 * Usage:
 *   npx playwright test e2e/visual-regression.spec.ts --update-snapshots  # Generate baselines
 *   npx playwright test e2e/visual-regression.spec.ts                      # Compare against baselines
 *
 * @see https://playwright.dev/docs/test-snapshots
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

// Disable animations for consistent screenshots
test.use({
  // Disable CSS animations and transitions
  launchOptions: {
    args: ['--force-prefers-reduced-motion'],
  },
});

test.describe('Visual Regression - Tabs', () => {
  test('channels tab - default view', async ({ appPage }) => {
    // Already on channels tab by default
    await appPage.waitForSelector('.channels-pane', { timeout: 10000 });
    // Wait for any loading to complete
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('channels-tab-default.png', {
      fullPage: true,
    });
  });

  test('settings tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForSelector('.settings-tab', { timeout: 10000 });
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('settings-tab.png', {
      fullPage: true,
    });
  });

  test('m3u manager tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('m3u-manager-tab.png', {
      fullPage: true,
    });
  });

  test('epg manager tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'epg-manager');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('epg-manager-tab.png', {
      fullPage: true,
    });
  });

  test('logo manager tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('logo-manager-tab.png', {
      fullPage: true,
    });
  });

  test('stats tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'stats');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('stats-tab.png', {
      fullPage: true,
    });
  });

  test('journal tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('journal-tab.png', {
      fullPage: true,
    });
  });

  test('guide tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'guide');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('guide-tab.png', {
      fullPage: true,
    });
  });
});

test.describe('Visual Regression - Components', () => {
  test('header and navigation', async ({ appPage }) => {
    const header = appPage.locator('.app-header');
    await expect(header).toHaveScreenshot('header.png');
  });

  test('tab navigation', async ({ appPage }) => {
    const tabNav = appPage.locator('.tab-navigation');
    await expect(tabNav).toHaveScreenshot('tab-navigation.png');
  });

  test('channels pane header', async ({ appPage }) => {
    await appPage.waitForSelector('.channels-pane', { timeout: 10000 });
    const paneHeader = appPage.locator('.channels-pane .pane-header').first();
    if (await paneHeader.isVisible()) {
      await expect(paneHeader).toHaveScreenshot('channels-pane-header.png');
    }
  });

  test('streams pane header', async ({ appPage }) => {
    await appPage.waitForSelector('.streams-pane', { timeout: 10000 });
    const paneHeader = appPage.locator('.streams-pane .pane-header').first();
    if (await paneHeader.isVisible()) {
      await expect(paneHeader).toHaveScreenshot('streams-pane-header.png');
    }
  });
});

test.describe('Visual Regression - Settings Sections', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForSelector('.settings-tab', { timeout: 10000 });
  });

  test('settings general section', async ({ appPage }) => {
    // Capture a section of the settings tab
    const settingsContent = appPage.locator('.settings-content, .settings-tab').first();
    await expect(settingsContent).toHaveScreenshot('settings-content.png');
  });
});

test.describe('Visual Regression - Dark Mode', () => {
  test.use({
    colorScheme: 'dark',
  });

  test('channels tab - dark mode', async ({ appPage }) => {
    await appPage.waitForSelector('.channels-pane', { timeout: 10000 });
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('channels-tab-dark.png', {
      fullPage: true,
    });
  });

  test('settings tab - dark mode', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForSelector('.settings-tab', { timeout: 10000 });
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('settings-tab-dark.png', {
      fullPage: true,
    });
  });
});

test.describe('Visual Regression - Interactive States', () => {
  test('button hover states', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForSelector('.settings-tab', { timeout: 10000 });

    // Find a primary button and hover over it
    const primaryButton = appPage.locator('button.btn-primary, button.modal-btn-primary').first();
    if (await primaryButton.isVisible()) {
      await primaryButton.hover();
      await appPage.waitForTimeout(100);
      await expect(primaryButton).toHaveScreenshot('button-primary-hover.png');
    }
  });

  test('input focus states', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForSelector('.settings-tab', { timeout: 10000 });

    // Find an input and focus it
    const input = appPage.locator('input[type="text"], input[type="number"]').first();
    if (await input.isVisible()) {
      await input.focus();
      await appPage.waitForTimeout(100);
      await expect(input).toHaveScreenshot('input-focus.png');
    }
  });
});

test.describe('Visual Regression - Loading States', () => {
  test('loading spinner visibility', async ({ appPage }) => {
    // Navigate to a tab that might show loading
    await navigateToTab(appPage, 'stats');
    // Just verify the tab loads without screenshot (loading states are transient)
    await appPage.waitForTimeout(2000);
    // After loading completes, take screenshot
    await expect(appPage).toHaveScreenshot('stats-tab-loaded.png', {
      fullPage: true,
    });
  });
});

test.describe('Visual Regression - Empty States', () => {
  test('journal tab empty state', async ({ appPage }) => {
    await navigateToTab(appPage, 'journal');
    await appPage.waitForTimeout(1000);

    // Look for empty state or list
    const emptyState = appPage.locator('.empty-state, .no-items, .journal-empty');
    const hasEmptyState = await emptyState.isVisible().catch(() => false);

    if (hasEmptyState) {
      await expect(emptyState).toHaveScreenshot('journal-empty-state.png');
    }
  });
});
