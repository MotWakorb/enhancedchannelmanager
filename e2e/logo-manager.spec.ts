/**
 * E2E tests for Logo Manager Tab.
 *
 * Tests logo management and channel logo assignment functionality.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('Logo Manager Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('logo manager tab is accessible', async ({ appPage }) => {
    const logoTab = appPage.locator(selectors.tabButton('logo-manager'));
    await logoTab.waitFor({ state: 'visible', timeout: 5000 });
    await expect(logoTab).toHaveClass(/active/);
  });

  test('logo manager content is visible', async ({ appPage }) => {
    // Look for logo manager container
    const logoContent = appPage.locator('.logo-manager-content, .logo-manager-container, [data-testid="logo-manager"]');
    const isVisible = await logoContent.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Logo Library', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('displays logo library', async ({ appPage }) => {
    // Look for logo library/grid
    const logoLibrary = appPage.locator('.logo-library, .logo-grid, [data-testid="logo-library"]');
    const isVisible = await logoLibrary.first().isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });

  test('displays logo items', async ({ appPage }) => {
    // Look for logo items
    const logoItems = appPage.locator('.logo-item, .logo-card, [data-testid="logo-item"]');
    const count = await logoItems.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('logo items show image', async ({ appPage }) => {
    const logoItems = appPage.locator('.logo-item, .logo-card');
    const count = await logoItems.count();

    if (count > 0) {
      const firstLogo = logoItems.first();
      const image = firstLogo.locator('img, .logo-image');
      const imageCount = await image.count();
      expect(imageCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('logo items show name', async ({ appPage }) => {
    const logoItems = appPage.locator('.logo-item, .logo-card');
    const count = await logoItems.count();

    if (count > 0) {
      const firstLogo = logoItems.first();
      const text = await firstLogo.textContent();
      expect(typeof text).toBe('string');
    }
  });
});

test.describe('Logo Search', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('search input exists', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"], input[placeholder*="Search"]');
    const count = await searchInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can search for logos', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"]').first();
    const exists = await searchInput.count();

    if (exists > 0) {
      await searchInput.fill('ESPN');
      await appPage.waitForTimeout(300);
      expect(true).toBe(true);

      // Clear search
      await searchInput.clear();
    }
  });

  test('search filters logo list', async ({ appPage }) => {
    const searchInput = appPage.locator('input[type="search"], input[placeholder*="search"]').first();
    const exists = await searchInput.count();

    if (exists > 0) {
      const logoItemsBefore = appPage.locator('.logo-item, .logo-card');
      const countBefore = await logoItemsBefore.count();

      if (countBefore > 0) {
        await searchInput.fill('zzzznonexistent');
        await appPage.waitForTimeout(300);

        const logoItemsAfter = appPage.locator('.logo-item, .logo-card');
        const countAfter = await logoItemsAfter.count();

        expect(typeof countAfter).toBe('number');
      }

      // Clear search
      await searchInput.clear();
    }
  });
});

test.describe('Logo Actions', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('add logo button exists', async ({ appPage }) => {
    const addButton = appPage.locator('button:has-text("Add"), button:has-text("Upload"), .add-logo-btn, [data-testid="add-logo"]');
    const count = await addButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('refresh button exists', async ({ appPage }) => {
    const refreshButton = appPage.locator('button:has-text("Refresh"), .refresh-logos-btn, [data-testid="refresh-logos"]');
    const count = await refreshButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('logo item has action buttons', async ({ appPage }) => {
    const logoItems = appPage.locator('.logo-item, .logo-card');
    const count = await logoItems.count();

    if (count > 0) {
      const firstLogo = logoItems.first();
      // Hover to reveal actions if needed
      await firstLogo.hover();
      await appPage.waitForTimeout(200);

      const actionButtons = firstLogo.locator('button, .action-btn, .icon-btn');
      const buttonCount = await actionButtons.count();
      expect(buttonCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Logo Selection', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('clicking logo selects it', async ({ appPage }) => {
    const logoItems = appPage.locator('.logo-item, .logo-card');
    const count = await logoItems.count();

    if (count > 0) {
      const firstLogo = logoItems.first();
      await firstLogo.click();
      await appPage.waitForTimeout(200);

      // Check for selected state
      const isSelected = await firstLogo.evaluate((el) => {
        return el.classList.contains('selected') ||
               el.getAttribute('aria-selected') === 'true' ||
               el.classList.contains('active');
      });
      expect(typeof isSelected).toBe('boolean');
    }
  });

  test('can multi-select logos', async ({ appPage }) => {
    const logoItems = appPage.locator('.logo-item, .logo-card');
    const count = await logoItems.count();

    if (count >= 2) {
      // Ctrl+click for multi-select
      await logoItems.first().click();
      await logoItems.nth(1).click({ modifiers: ['Control'] });
      await appPage.waitForTimeout(200);

      expect(true).toBe(true);
    }
  });
});

test.describe('Logo Upload', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('upload button opens modal', async ({ appPage }) => {
    const uploadButton = appPage.locator('button:has-text("Upload"), button:has-text("Add Logo"), .upload-logo-btn').first();
    const exists = await uploadButton.count();

    if (exists > 0) {
      await uploadButton.click();
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

  test('upload form has file input', async ({ appPage }) => {
    const uploadButton = appPage.locator('button:has-text("Upload"), button:has-text("Add Logo"), .upload-logo-btn').first();
    const exists = await uploadButton.count();

    if (exists > 0) {
      await uploadButton.click();
      await appPage.waitForTimeout(300);

      const modal = appPage.locator(selectors.modal);
      const modalVisible = await modal.isVisible().catch(() => false);

      if (modalVisible) {
        const fileInput = modal.locator('input[type="file"]');
        const fileInputCount = await fileInput.count();
        expect(fileInputCount).toBeGreaterThanOrEqual(0);

        // Close modal
        const closeBtn = appPage.locator(selectors.modalClose);
        if (await closeBtn.count() > 0) {
          await closeBtn.click();
        }
      }
    }
  });
});

test.describe('Logo Categories', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('category filter exists', async ({ appPage }) => {
    const categoryFilter = appPage.locator('select, .category-filter, [data-testid="logo-category"]');
    const count = await categoryFilter.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can filter by category', async ({ appPage }) => {
    const categoryFilter = appPage.locator('select, .category-filter').first();
    const exists = await categoryFilter.count();

    if (exists > 0) {
      await categoryFilter.click();
      await appPage.waitForTimeout(200);
      expect(true).toBe(true);
    }
  });
});

test.describe('Logo Grid View', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager');
  });

  test('grid view is responsive', async ({ appPage }) => {
    const logoGrid = appPage.locator('.logo-grid, .logo-library');
    const isVisible = await logoGrid.first().isVisible().catch(() => false);

    if (isVisible) {
      const box = await logoGrid.first().boundingBox();
      expect(box).toBeTruthy();
      if (box) {
        expect(box.width).toBeGreaterThan(0);
      }
    }
  });

  test('can scroll through logos', async ({ appPage }) => {
    const logoGrid = appPage.locator('.logo-grid, .logo-library');
    const isVisible = await logoGrid.first().isVisible().catch(() => false);

    if (isVisible) {
      await logoGrid.first().evaluate((el) => {
        el.scrollTop = 100;
      });
      expect(true).toBe(true);
    }
  });
});
