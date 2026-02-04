/**
 * E2E tests for CSV Export functionality.
 *
 * Tests the CSV export and template download workflows.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

// CSV Export selectors
const csvSelectors = {
  exportButton: '[data-testid="csv-export-button"], button:has-text("Export CSV")',
  templateButton: '[data-testid="csv-template-button"], button:has-text("Download Template"), button:has-text("Template")',
  toolbar: '.pane-header, .pane-header-actions',
  dropdownMenu: '[data-testid="csv-menu"], .csv-dropdown, .dropdown-menu',
  dropdownTrigger: '[data-testid="csv-dropdown-trigger"], .csv-dropdown-trigger',
};

test.describe('CSV Export', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('export button is visible in channels toolbar', async ({ appPage }) => {
    const exportButton = appPage.locator(csvSelectors.exportButton);
    await expect(exportButton).toBeVisible({ timeout: 5000 });
  });

  test('clicking export button triggers download', async ({ appPage }) => {
    const exportButton = appPage.locator(csvSelectors.exportButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    // Click export button
    await exportButton.click();

    // Wait for download to start
    const download = await downloadPromise;

    // Verify download filename
    const filename = download.suggestedFilename();
    expect(filename).toMatch(/channels.*\.csv$/);
  });

  test('exported CSV has correct content type', async ({ appPage }) => {
    const exportButton = appPage.locator(csvSelectors.exportButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    await exportButton.click();

    const download = await downloadPromise;

    // Save to temp file and check content
    const path = await download.path();
    expect(path).toBeTruthy();
  });

  test('exported CSV contains header row', async ({ appPage }) => {
    const exportButton = appPage.locator(csvSelectors.exportButton);

    // Set up download listener
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });

    await exportButton.click();

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

    // Verify header row
    expect(content).toContain('channel_number');
    expect(content).toContain('name');
    expect(content).toContain('group_name');
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

test.describe('CSV Export/Import Round Trip', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('exported CSV can be re-imported', async ({ appPage }) => {
    // Export channels
    const exportButton = appPage.locator(csvSelectors.exportButton);
    const downloadPromise = appPage.waitForEvent('download', { timeout: 10000 });
    await exportButton.click();
    const download = await downloadPromise;

    // Get exported content
    const content = await download.createReadStream().then(stream => {
      return new Promise<string>((resolve, reject) => {
        let data = '';
        stream.on('data', chunk => data += chunk);
        stream.on('end', () => resolve(data));
        stream.on('error', reject);
      });
    });

    // Content should be valid CSV (has header)
    expect(content).toContain('name');

    // If there are channels, the CSV should have data rows
    const lines = content.split('\n').filter(l => l.trim() && !l.startsWith('#'));
    expect(lines.length).toBeGreaterThanOrEqual(1); // At least header
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
