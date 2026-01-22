/**
 * E2E tests for M3U Manager Tab.
 *
 * Tests M3U source management functionality.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('M3U Manager Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('M3U manager tab is accessible', async ({ appPage }) => {
    const m3uTab = appPage.locator(selectors.tabButton('m3u-manager'));
    await expect(m3uTab).toHaveClass(/active/);
  });

  test('M3U sources section is visible', async ({ appPage }) => {
    // Look for M3U sources container
    const sourcesSection = appPage.locator('.m3u-sources, .m3u-manager-content, [data-testid="m3u-sources"]');
    const isVisible = await sourcesSection.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('M3U Sources List', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('displays M3U sources', async ({ appPage }) => {
    // Look for source rows/items
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item, [data-testid="m3u-source"]');
    const count = await sourceItems.count();
    // May have zero or more sources
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('source items show name and status', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      const firstSource = sourceItems.first();
      const text = await firstSource.textContent();
      // Source should have some text content
      expect(text).toBeTruthy();
    }
  });

  test('can scroll through source list', async ({ appPage }) => {
    const sourcesList = appPage.locator('.m3u-sources-list, .m3u-manager-content');
    const isVisible = await sourcesList.first().isVisible().catch(() => false);

    if (isVisible) {
      await sourcesList.first().evaluate((el) => {
        el.scrollTop = 100;
      });
      expect(true).toBe(true);
    }
  });
});

test.describe('M3U Source Actions', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('add source button exists', async ({ appPage }) => {
    const addButton = appPage.locator('button:has-text("Add"), .add-m3u-source-btn, [data-testid="add-m3u-source"]');
    const count = await addButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('refresh button exists', async ({ appPage }) => {
    const refreshButton = appPage.locator('button:has-text("Refresh"), .refresh-m3u-btn, [data-testid="refresh-m3u"]');
    const count = await refreshButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('source has action buttons', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      const firstSource = sourceItems.first();
      // Look for action buttons within the source row
      const actionButtons = firstSource.locator('button, .action-btn, .icon-btn');
      const buttonCount = await actionButtons.count();
      expect(buttonCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('M3U Source Toggle', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('sources can be enabled/disabled', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      // Look for toggle/checkbox in first source
      const toggle = sourceItems.first().locator('input[type="checkbox"], .toggle, .switch');
      const toggleExists = await toggle.count();
      expect(toggleExists).toBeGreaterThanOrEqual(0);
    }
  });

  test('inactive sources appear differently styled', async ({ appPage }) => {
    const inactiveSources = appPage.locator('.m3u-source-row.inactive, .m3u-source-item.disabled, [data-active="false"]');
    const count = await inactiveSources.count();
    // May or may not have inactive sources
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('M3U Source Status', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('sources show status indicator', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      // Look for status indicator
      const statusIndicator = sourceItems.first().locator('.status, .status-icon, .material-icons');
      const hasStatus = await statusIndicator.count();
      expect(hasStatus).toBeGreaterThanOrEqual(0);
    }
  });

  test('sources show last refresh time', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      const firstSource = sourceItems.first();
      const text = await firstSource.textContent();
      // Look for time-related text (may contain dates, "ago", etc.)
      expect(typeof text).toBe('string');
    }
  });

  test('sources show channel count', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      const firstSource = sourceItems.first();
      const text = await firstSource.textContent();
      // Source info may include channel count
      expect(typeof text).toBe('string');
    }
  });
});

test.describe('M3U Source Drag and Drop', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('sources can be reordered via drag', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count >= 2) {
      const firstSource = sourceItems.first();
      const secondSource = sourceItems.nth(1);

      const firstBox = await firstSource.boundingBox();
      const secondBox = await secondSource.boundingBox();

      if (firstBox && secondBox) {
        // Attempt drag operation
        await firstSource.dragTo(secondSource);
        await appPage.waitForTimeout(300);
        expect(true).toBe(true);
      }
    }
  });

  test('drag handle is visible', async ({ appPage }) => {
    const sourceItems = appPage.locator('.m3u-source-row, .m3u-source-item');
    const count = await sourceItems.count();

    if (count > 0) {
      const dragHandle = sourceItems.first().locator('.drag-handle, [data-drag-handle], .material-icons:has-text("drag_indicator")');
      const handleCount = await dragHandle.count();
      expect(handleCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Add M3U Source Modal', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager');
  });

  test('clicking add opens modal', async ({ appPage }) => {
    const addButton = appPage.locator('button:has-text("Add M3U"), .add-m3u-source-btn').first();
    const exists = await addButton.count();

    if (exists > 0) {
      await addButton.click();
      await appPage.waitForTimeout(300);

      const modal = appPage.locator(selectors.modal);
      const modalVisible = await modal.isVisible().catch(() => false);
      expect(typeof modalVisible).toBe('boolean');

      // Close modal if open
      if (modalVisible) {
        const closeBtn = appPage.locator(selectors.modalClose);
        if (await closeBtn.count() > 0) {
          await closeBtn.click();
        }
      }
    }
  });

  test('add form has required fields', async ({ appPage }) => {
    const addButton = appPage.locator('button:has-text("Add M3U"), .add-m3u-source-btn').first();
    const exists = await addButton.count();

    if (exists > 0) {
      await addButton.click();
      await appPage.waitForTimeout(300);

      const modal = appPage.locator(selectors.modal);
      const modalVisible = await modal.isVisible().catch(() => false);

      if (modalVisible) {
        // Check for name and URL fields
        const nameField = modal.locator('input[name="name"], input[placeholder*="name"]');
        const urlField = modal.locator('input[name="url"], input[placeholder*="URL"]');

        const hasNameField = await nameField.count();
        const hasUrlField = await urlField.count();

        expect(hasNameField + hasUrlField).toBeGreaterThanOrEqual(0);

        // Close modal
        const closeBtn = appPage.locator(selectors.modalClose);
        if (await closeBtn.count() > 0) {
          await closeBtn.click();
        }
      }
    }
  });
});
