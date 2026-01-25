/**
 * CSS Visual Smoke Tests
 *
 * Quick visual sanity checks for CSS changes (~30 seconds vs full suite).
 * Run after CSS modifications for fast feedback before running full E2E.
 *
 * Usage:
 *   npm run test:css-smoke                    # Quick smoke test
 *   npm run test:css-smoke -- --update-snapshots  # Update baselines
 *
 * @see https://playwright.dev/docs/test-snapshots
 */
import { test, expect, navigateToTab } from './fixtures/base';

// Disable animations for consistent screenshots
test.use({
  launchOptions: {
    args: ['--force-prefers-reduced-motion'],
  },
});

test.describe('CSS Smoke Tests', () => {
  test('channels tab - main view', async ({ appPage }) => {
    await appPage.waitForSelector('.channels-pane', { timeout: 10000 });
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('smoke-channels.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });

  test('streams pane', async ({ appPage }) => {
    await appPage.waitForSelector('.streams-pane', { timeout: 10000 });
    const streamsPane = appPage.locator('.streams-pane');
    await expect(streamsPane).toHaveScreenshot('smoke-streams-pane.png', {
      maxDiffPixelRatio: 0.01,
    });
  });

  test('settings tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForSelector('.settings-tab', { timeout: 10000 });
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('smoke-settings.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });

  test('epg manager tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'epg-manager');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('smoke-epg-manager.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });

  test('m3u manager tab', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
    await appPage.waitForTimeout(1000);
    await expect(appPage).toHaveScreenshot('smoke-m3u-manager.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });

  test('channels tab - dark mode', async ({ appPage }) => {
    // Toggle to dark mode via media emulation
    await appPage.emulateMedia({ colorScheme: 'dark' });
    await appPage.waitForSelector('.channels-pane', { timeout: 10000 });
    await appPage.waitForTimeout(500);
    await expect(appPage).toHaveScreenshot('smoke-channels-dark.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });
});
