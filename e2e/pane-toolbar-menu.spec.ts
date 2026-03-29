/**
 * E2E tests for PaneToolbarMenu (channels pane three-dot menu).
 *
 * Verifies that all bulk actions are accessible through the toolbar menu,
 * including selection-dependent actions that appear when channels are selected.
 */
import { test, expect, navigateToTab, enterEditMode } from './fixtures/base';
import { selectors } from './fixtures/test-data';

test.describe('PaneToolbarMenu', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('three-dot menu button is visible on channel manager tab', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn');
    // Should have at least one toolbar menu button
    const count = await menuBtn.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('clicking three-dot menu opens dropdown', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    // Dropdown should appear
    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });
  });

  test('menu contains Manage Profiles option', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });

    const profilesItem = dropdown.locator('.pane-toolbar-menu-item:has-text("Manage Profiles")');
    await expect(profilesItem).toBeVisible();
  });

  test('menu contains CSV options', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });

    const csvTemplate = dropdown.locator('.pane-toolbar-menu-item:has-text("CSV Template")');
    const exportCsv = dropdown.locator('.pane-toolbar-menu-item:has-text("Export CSV")');
    await expect(csvTemplate).toBeVisible();
    await expect(exportCsv).toBeVisible();
  });

  test('menu closes when clicking outside', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });

    // Click outside the dropdown
    await appPage.locator('body').click({ position: { x: 10, y: 10 } });

    // Dropdown should close
    await expect(dropdown).not.toBeVisible({ timeout: 5000 });
  });
});

test.describe('PaneToolbarMenu - Edit Mode', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
    await enterEditMode(appPage);
  });

  test('edit mode shows additional menu items', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });

    // Edit mode should show Hidden Groups and Import CSV
    const hiddenGroups = dropdown.locator('.pane-toolbar-menu-item:has-text("Hidden Groups")');
    const importCsv = dropdown.locator('.pane-toolbar-menu-item:has-text("Import CSV")');
    await expect(hiddenGroups).toBeVisible();
    await expect(importCsv).toBeVisible();
  });

  test('edit mode shows Renumber All Groups', async ({ appPage }) => {
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });

    const renumberAll = dropdown.locator('.pane-toolbar-menu-item:has-text("Renumber All Groups")');
    await expect(renumberAll).toBeVisible();
  });

  test('selection-dependent actions appear when channels are selected', async ({ appPage }) => {
    // Check if there are any channel items to select
    const channelItems = appPage.locator(selectors.channelItem);
    const count = await channelItems.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Click first channel to select it
    await channelItems.first().click();

    // Open menu
    const menuBtn = appPage.locator('.pane-toolbar-menu-btn').first();
    await menuBtn.click();

    const dropdown = appPage.locator('.pane-toolbar-menu-dropdown');
    await expect(dropdown).toBeVisible({ timeout: 5000 });

    // Selection section should appear with bulk actions
    const sectionLabel = dropdown.locator('.pane-toolbar-menu-section-label:has-text("Selection")');
    await expect(sectionLabel).toBeVisible();

    // Verify key bulk actions are present
    const assignEpg = dropdown.locator('.pane-toolbar-menu-item:has-text("Assign EPG")');
    const fetchGracenote = dropdown.locator('.pane-toolbar-menu-item:has-text("Fetch Gracenote IDs")');
    const normalizeNames = dropdown.locator('.pane-toolbar-menu-item:has-text("Normalize Names")');
    const renumber = dropdown.locator('.pane-toolbar-menu-item:has-text("Renumber")');
    const setLogoM3u = dropdown.locator('.pane-toolbar-menu-item:has-text("Set Logo from M3U")');

    await expect(assignEpg).toBeVisible();
    await expect(fetchGracenote).toBeVisible();
    await expect(normalizeNames).toBeVisible();
    await expect(renumber).toBeVisible();
    await expect(setLogoM3u).toBeVisible();
  });

  test('no BulkActionsDropdown elements exist in DOM', async ({ appPage }) => {
    // Verify the dead code component is truly gone - no .bulk-actions-dropdown,
    // .bulk-actions-menu, or .bulk-actions-item elements should exist
    const bulkDropdown = appPage.locator('.bulk-actions-dropdown');
    const bulkMenu = appPage.locator('.bulk-actions-menu');
    const bulkItem = appPage.locator('.bulk-actions-item');

    expect(await bulkDropdown.count()).toBe(0);
    expect(await bulkMenu.count()).toBe(0);
    expect(await bulkItem.count()).toBe(0);
  });
});
