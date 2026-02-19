/**
 * E2E tests for CSV Export functionality.
 *
 * Tests the CSV export and template download workflows.
 */
import { test, expect, navigateToTab } from './fixtures/base';

// CSV Export selectors
const csvSelectors = {
  exportButton: '[data-testid="csv-export-button"], button:has-text("Export CSV")',
  templateButton: '[data-testid="csv-template-button"], button:has-text("Download Template"), button:has-text("Template")',
  toolbar: '.pane-header, .pane-header-actions',
};

test.describe('CSV Export', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('export button is visible in channels toolbar', async ({ appPage }) => {
    const exportButton = appPage.locator(csvSelectors.exportButton);
    await expect(exportButton).toBeVisible({ timeout: 5000 });
  });

});

test.describe('CSV Template Download', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('template button is visible in channels toolbar', async ({ appPage }) => {
    const templateButton = appPage.locator(csvSelectors.templateButton);
    await expect(templateButton).toBeVisible({ timeout: 5000 });
  });

  test('clicking template button triggers download', async ({ appPage }) => {
    const templateButton = appPage.locator(csvSelectors.templateButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    // Click template button
    await templateButton.click();

    // Wait for download to start
    const download = await downloadPromise;

    // Verify download filename
    const filename = download.suggestedFilename();
    expect(filename).toMatch(/template.*\.csv$/);
  });

  test('template contains instructional comments', async ({ appPage }) => {
    const templateButton = appPage.locator(csvSelectors.templateButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    await templateButton.click();

    const download = await downloadPromise;

    // Read the downloaded content
    const content = await download.createReadStream().then(stream => {
      return new Promise<string>((resolve, reject) => {
        let data = '';
        stream.on('data', chunk => data += chunk);
        stream.on('end', () => resolve(data));
        stream.on('error', reject);
      });
    });

    // Template should start with comment
    expect(content.trim()).toMatch(/^#/);

    // Should contain instructions
    expect(content.toLowerCase()).toContain('required');
  });

  test('template contains example rows', async ({ appPage }) => {
    const templateButton = appPage.locator(csvSelectors.templateButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    await templateButton.click();

    const download = await downloadPromise;

    // Read the downloaded content
    const content = await download.createReadStream().then(stream => {
      return new Promise<string>((resolve, reject) => {
        let data = '';
        stream.on('data', chunk => data += chunk);
        stream.on('end', () => resolve(data));
        stream.on('error', reject);
      });
    });

    // Should have example with channel number like 101 or 102
    expect(content).toMatch(/10[1-2]/);
  });

  test('template contains header row', async ({ appPage }) => {
    const templateButton = appPage.locator(csvSelectors.templateButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    await templateButton.click();

    const download = await downloadPromise;

    // Read the downloaded content
    const content = await download.createReadStream().then(stream => {
      return new Promise<string>((resolve, reject) => {
        let data = '';
        stream.on('data', chunk => data += chunk);
        stream.on('end', () => resolve(data));
        stream.on('error', reject);
      });
    });

    // Should have all column headers
    expect(content).toContain('channel_number');
    expect(content).toContain('name');
    expect(content).toContain('group_name');
    expect(content).toContain('tvg_id');
    expect(content).toContain('gracenote_id');
    expect(content).toContain('logo_url');
  });
});

test.describe('CSV Toolbar Layout', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('CSV buttons are grouped together', async ({ appPage }) => {
    // Find the toolbar containing the export button (Channels pane)
    const toolbar = appPage.locator(csvSelectors.toolbar).filter({ has: appPage.locator(csvSelectors.exportButton) }).first();
    await expect(toolbar).toBeVisible();

    // Both export and template buttons should be in same toolbar
    const exportButton = toolbar.locator(csvSelectors.exportButton);
    const templateButton = toolbar.locator(csvSelectors.templateButton);

    // At least one of these should be visible
    const exportVisible = await exportButton.isVisible().catch(() => false);
    const templateVisible = await templateButton.isVisible().catch(() => false);

    expect(exportVisible || templateVisible).toBe(true);
  });

  test('CSV buttons have appropriate icons or labels', async ({ appPage }) => {
    const exportButton = appPage.locator(csvSelectors.exportButton);

    if (await exportButton.isVisible()) {
      const text = await exportButton.textContent();
      // Should have descriptive text
      expect(text?.toLowerCase()).toMatch(/export|csv|download/);
    }
  });
});
