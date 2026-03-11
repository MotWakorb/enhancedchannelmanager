/**
 * E2E tests for the Export tab.
 *
 * Tests profile CRUD, generate/download, cloud targets, and publish configs.
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { generateTestId } from './fixtures/test-data';

test.describe('Export Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'export');
  });

  test('export tab loads with sub-navigation', async ({ appPage }) => {
    const exportTab = appPage.locator('.export-tab');
    await expect(exportTab).toBeVisible();

    // Sub-navigation should show all sections
    const nav = appPage.locator('.export-tab-nav');
    await expect(nav).toBeVisible();
    await expect(nav.locator('.export-tab-nav-item')).toHaveCount(4);
    await expect(nav.getByText('Export Profiles')).toBeVisible();
    await expect(nav.getByText('Cloud Targets')).toBeVisible();
    await expect(nav.getByText('Publishing')).toBeVisible();
    await expect(nav.locator('span:not(.material-icons)', { hasText: 'History' })).toBeVisible();
  });

  test('profiles section is active by default', async ({ appPage }) => {
    const activeNav = appPage.locator('.export-tab-nav-item.active');
    await expect(activeNav).toContainText('Export Profiles');
  });
});

test.describe('Export Profiles', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'export');
  });

  test('shows profiles list or empty state', async ({ appPage }) => {
    // Wait for content to load (loading spinner gone)
    await appPage.waitForTimeout(2000);

    const profileCards = appPage.locator('.profile-card');
    const emptyState = appPage.locator('.profile-list-empty');
    const cardCount = await profileCards.count();
    const hasEmpty = await emptyState.isVisible().catch(() => false);

    // One of the two must be true
    expect(cardCount > 0 || hasEmpty).toBe(true);
  });

  test('can open new profile editor', async ({ appPage }) => {
    // Click "New Profile" button
    const newBtn = appPage.locator('.btn-primary:has-text("New Profile"), .btn-primary:has-text("Create Your First Profile")');
    await newBtn.first().click();

    // Modal should open
    const modal = appPage.locator('.modal-container');
    await expect(modal).toBeVisible();
    await expect(modal.locator('.modal-header h3')).toContainText('New Export Profile');

    // Form fields should be visible
    await expect(modal.locator('input[placeholder="My Export Profile"]')).toBeVisible();
    await expect(modal.locator('input[placeholder="Optional description"]')).toBeVisible();

    // Checkboxes should be visible
    await expect(modal.getByText('Include Logos')).toBeVisible();
    await expect(modal.getByText('Include EPG IDs')).toBeVisible();
    await expect(modal.getByText('Include Channel Numbers')).toBeVisible();

    // Close
    await modal.locator('.modal-close-btn').click();
    await expect(modal).not.toBeVisible();
  });

  test('can create a profile', async ({ appPage }) => {
    const profileName = `Test Profile ${generateTestId()}`;

    // Open editor
    const newBtn = appPage.locator('.btn-primary:has-text("New Profile"), .btn-primary:has-text("Create Your First Profile")');
    await newBtn.first().click();

    const modal = appPage.locator('.modal-container');
    await expect(modal).toBeVisible();

    // Fill name
    await modal.locator('input[placeholder="My Export Profile"]').fill(profileName);

    // Save
    await modal.locator('.modal-btn-primary:has-text("Create Profile")').click();

    // Modal should close
    await expect(modal).not.toBeVisible({ timeout: 10000 });

    // Profile should appear in list
    await expect(appPage.locator('.profile-card-name', { hasText: profileName })).toBeVisible({ timeout: 10000 });
  });

  test('can expand profile to see details and generate controls', async ({ appPage }) => {
    const profileName = `Expand Test ${generateTestId()}`;

    // Create a profile first
    const newBtn = appPage.locator('.btn-primary:has-text("New Profile"), .btn-primary:has-text("Create Your First Profile")');
    await newBtn.first().click();
    const modal = appPage.locator('.modal-container');
    await modal.locator('input[placeholder="My Export Profile"]').fill(profileName);
    await modal.locator('.modal-btn-primary:has-text("Create Profile")').click();
    await expect(modal).not.toBeVisible({ timeout: 10000 });

    // Click on the profile card header to expand
    const card = appPage.locator('.profile-card', { has: appPage.getByText(profileName) });
    await card.locator('.profile-card-header').click();

    // Should show expanded body with generate controls
    const body = card.locator('.profile-card-body');
    await expect(body).toBeVisible();

    // Generate button should be visible
    await expect(body.locator('.btn:has-text("Generate")')).toBeVisible();
  });

  test('can edit a profile', async ({ appPage }) => {
    const profileName = `Edit Test ${generateTestId()}`;

    // Create
    const newBtn = appPage.locator('.btn-primary:has-text("New Profile"), .btn-primary:has-text("Create Your First Profile")');
    await newBtn.first().click();
    const modal = appPage.locator('.modal-container');
    await modal.locator('input[placeholder="My Export Profile"]').fill(profileName);
    await modal.locator('.modal-btn-primary:has-text("Create Profile")').click();
    await expect(modal).not.toBeVisible({ timeout: 10000 });

    // Click edit
    const card = appPage.locator('.profile-card', { has: appPage.getByText(profileName) });
    await card.locator('.btn-icon[title="Edit"]').click();

    // Edit modal should open with existing name
    const editModal = appPage.locator('.modal-container');
    await expect(editModal).toBeVisible();
    await expect(editModal.locator('.modal-header h3')).toContainText('Edit Profile');

    const nameInput = editModal.locator('input[placeholder="My Export Profile"]');
    await expect(nameInput).toHaveValue(profileName);

    // Close without saving
    await editModal.locator('.modal-close-btn').click();
  });

  test('can delete a profile', async ({ appPage }) => {
    const profileName = `Delete Test ${generateTestId()}`;

    // Create
    const newBtn = appPage.locator('.btn-primary:has-text("New Profile"), .btn-primary:has-text("Create Your First Profile")');
    await newBtn.first().click();
    const modal = appPage.locator('.modal-container');
    await modal.locator('input[placeholder="My Export Profile"]').fill(profileName);
    await modal.locator('.modal-btn-primary:has-text("Create Profile")').click();
    await expect(modal).not.toBeVisible({ timeout: 10000 });

    // Click delete
    const card = appPage.locator('.profile-card', { has: appPage.getByText(profileName) });
    await card.locator('.btn-icon[title="Delete"]').click();

    // Confirmation modal
    const confirmModal = appPage.locator('.modal-container');
    await expect(confirmModal).toBeVisible();
    await expect(confirmModal.locator('.modal-header h3')).toContainText('Delete Profile');

    // Confirm delete
    await confirmModal.locator('.modal-btn-danger:has-text("Delete")').click();

    // Profile should be gone
    await expect(appPage.locator('.profile-card-name', { hasText: profileName })).not.toBeVisible({ timeout: 10000 });
  });

  test('validates empty name on create', async ({ appPage }) => {
    const newBtn = appPage.locator('.btn-primary:has-text("New Profile"), .btn-primary:has-text("Create Your First Profile")');
    await newBtn.first().click();

    const modal = appPage.locator('.modal-container');
    await expect(modal).toBeVisible();

    // Try to save without name
    await modal.locator('.modal-btn-primary:has-text("Create Profile")').click();

    // Should show error toast (modal stays open)
    await expect(modal).toBeVisible();

    // Close modal
    await modal.locator('.modal-close-btn').click();
  });
});

test.describe('Cloud Targets', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'export');
    // Switch to Cloud Targets sub-section
    await appPage.locator('.export-tab-nav-item:has-text("Cloud Targets")').click();
  });

  test('cloud targets section loads', async ({ appPage }) => {
    const activeNav = appPage.locator('.export-tab-nav-item.active');
    await expect(activeNav).toContainText('Cloud Targets');

    // Should show either empty state or target list
    const content = appPage.locator('.cloud-target-list');
    await expect(content).toBeVisible({ timeout: 10000 });
  });

  test('shows empty state with helpful message', async ({ appPage }) => {
    const emptyState = appPage.locator('.profile-list-empty');
    const targetCards = appPage.locator('.cloud-target-list .profile-card');
    const cardCount = await targetCards.count();

    if (cardCount === 0) {
      await expect(emptyState).toBeVisible();
      await expect(appPage.getByText('Cloud targets are optional')).toBeVisible();
    }
  });

  test('can open new target editor with provider fields', async ({ appPage }) => {
    const newBtn = appPage.locator('.btn-primary:has-text("New Target"), .btn-primary:has-text("Add Cloud Target")');
    await newBtn.first().click();

    const modal = appPage.locator('.modal-container');
    await expect(modal).toBeVisible();
    await expect(modal.locator('.modal-header h3')).toContainText('New Cloud Target');

    // Should show credential fields for default S3 provider
    await expect(modal.getByText('Bucket Name')).toBeVisible();
    await expect(modal.getByText('Access Key ID')).toBeVisible();
    await expect(modal.getByText('Secret Access Key')).toBeVisible();

    // Test Connection button should be visible
    await expect(modal.locator('.modal-btn:has-text("Test Connection")')).toBeVisible();

    // Close
    await modal.locator('.modal-close-btn').click();
  });
});

test.describe('Publishing', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'export');
    await appPage.locator('.export-tab-nav-item:has-text("Publishing")').click();
  });

  test('publishing section loads', async ({ appPage }) => {
    const activeNav = appPage.locator('.export-tab-nav-item.active');
    await expect(activeNav).toContainText('Publishing');
  });

  test('shows appropriate empty state', async ({ appPage }) => {
    // Should show either "create profile first" or "no publish configs" message
    const emptyState = appPage.locator('.profile-list-empty');
    const configCards = appPage.locator('.publish-config-list .profile-card');
    const cardCount = await configCards.count();

    if (cardCount === 0) {
      await expect(emptyState).toBeVisible();
    }
  });
});

test.describe('Publish History', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'export');
    await appPage.locator('.export-tab-nav-item:has-text("History")').click();
  });

  test('history section loads with filters', async ({ appPage }) => {
    const activeNav = appPage.locator('.export-tab-nav-item.active');
    await expect(activeNav).toContainText('History');

    // Filter controls should be present
    const filters = appPage.locator('.publish-history-filters');
    await expect(filters).toBeVisible({ timeout: 10000 });
  });

  test('shows empty state when no history', async ({ appPage }) => {
    const entries = appPage.locator('.publish-history-table');
    const emptyState = appPage.locator('.profile-list-empty');

    // Either entries exist or empty state shows
    const hasEntries = await entries.isVisible().catch(() => false);
    if (!hasEntries) {
      await expect(emptyState).toBeVisible();
      await expect(appPage.getByText('No publish history yet')).toBeVisible();
    }
  });

  test('clean old button is visible', async ({ appPage }) => {
    await expect(appPage.locator('.btn:has-text("Clean Old")')).toBeVisible();
  });
});

test.describe('Export Sub-Navigation', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'export');
  });

  test('can switch between all sub-sections', async ({ appPage }) => {
    const sections = ['Export Profiles', 'Cloud Targets', 'Publishing', 'History'];

    for (const section of sections) {
      await appPage.locator(`.export-tab-nav-item:has-text("${section}")`).click();
      const activeNav = appPage.locator('.export-tab-nav-item.active');
      await expect(activeNav).toContainText(section);
    }
  });
});
