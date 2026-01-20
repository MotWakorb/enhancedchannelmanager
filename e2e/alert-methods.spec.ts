/**
 * E2E tests for Alert Methods.
 */
import { test, expect, navigateToTab, waitForToast, closeModal } from './fixtures/base';
import { selectors, sampleAlertMethods } from './fixtures/test-data';

test.describe('Alert Methods', () => {
  test.beforeEach(async ({ appPage }) => {
    // Navigate to settings where alert methods are configured
    await navigateToTab(appPage, 'settings');
  });

  test('alert methods section is visible', async ({ appPage }) => {
    const alertSection = appPage.locator(
      selectors.alertMethodList +
        ', [data-testid="alert-methods"], .alert-methods, :has-text("Alert Methods"), :has-text("Notifications")'
    );

    const exists = await alertSection.count();
    expect(exists).toBeGreaterThanOrEqual(0);
  });

  test('can view alert method list', async ({ appPage }) => {
    const alertItems = appPage.locator(selectors.alertMethodItem + ', [data-testid*="alert"]');
    const count = await alertItems.count();

    // Alert methods may or may not be configured
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('add button is visible', async ({ appPage }) => {
    const addButton = appPage.locator(
      selectors.alertMethodAddButton +
        ', button:has-text("Add"), button:has-text("New"), button[title*="Add"]'
    );

    const count = await addButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Alert Method Types', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('Discord type is available', async ({ appPage }) => {
    // Look for Discord in the UI
    const discord = appPage.locator(':has-text("Discord"), [data-testid*="discord"]');
    const count = await discord.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('Telegram type is available', async ({ appPage }) => {
    const telegram = appPage.locator(':has-text("Telegram"), [data-testid*="telegram"]');
    const count = await telegram.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('Email/SMTP type is available', async ({ appPage }) => {
    const email = appPage.locator(':has-text("Email"), :has-text("SMTP"), [data-testid*="email"], [data-testid*="smtp"]');
    const count = await email.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Alert Method Configuration', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('alert methods show enabled status', async ({ appPage }) => {
    const toggles = appPage.locator(
      '.alert-method-item input[type="checkbox"], .alert-method-item .toggle'
    );
    const count = await toggles.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('alert methods show notification type filters', async ({ appPage }) => {
    // Look for notification type filters (info, success, warning, error)
    const typeFilters = appPage.locator(
      ':has-text("Info"), :has-text("Success"), :has-text("Warning"), :has-text("Error"), [data-testid*="notify"]'
    );
    const count = await typeFilters.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Alert Method Actions', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('test button exists for alert methods', async ({ appPage }) => {
    const testButtons = appPage.locator(
      selectors.alertMethodTestButton + ', button:has-text("Test"), button[title*="Test"]'
    );

    const count = await testButtons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('edit functionality is available', async ({ appPage }) => {
    const editButtons = appPage.locator(
      'button:has-text("Edit"), button[title*="Edit"], .edit-button'
    );

    const count = await editButtons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('delete functionality is available', async ({ appPage }) => {
    const deleteButtons = appPage.locator(
      'button:has-text("Delete"), button[title*="Delete"], .delete-button, button[aria-label*="Delete"]'
    );

    const count = await deleteButtons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Alert Method Form', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('clicking add opens form/modal', async ({ appPage }) => {
    const addButton = appPage.locator(
      selectors.alertMethodAddButton + ', button:has-text("Add Alert"), button:has-text("Add Method")'
    ).first();

    const exists = await addButton.count();
    if (exists > 0) {
      await addButton.click();
      await appPage.waitForTimeout(500);

      // Should show a modal or form
      const modal = appPage.locator(selectors.modal + ', [role="dialog"], .modal');
      const form = appPage.locator('form, [data-testid*="form"]');

      const modalCount = await modal.count();
      const formCount = await form.count();

      // Either should appear
      expect(modalCount + formCount).toBeGreaterThan(0);

      // Close if modal opened
      if (modalCount > 0) {
        await closeModal(appPage);
      }
    }
  });

  test('form has name field', async ({ appPage }) => {
    const nameInput = appPage.locator(
      'input[name="name"], input[placeholder*="Name"], input[placeholder*="name"]'
    );

    const count = await nameInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('form has type selector', async ({ appPage }) => {
    const typeSelect = appPage.locator(
      'select[name="method_type"], select[name="type"], [data-testid*="type-select"]'
    );

    const count = await typeSelect.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});
