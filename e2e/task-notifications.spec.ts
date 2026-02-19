/**
 * E2E tests for Task Notification Settings.
 *
 * Tests the "show notifications in bell icon" feature:
 * - When enabled, task results appear in the notification center
 * - When disabled, task results should NOT appear in the notification center
 */
import { test, expect, navigateToTab } from './fixtures/base';

test.describe('Task Notification Settings', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    // Navigate to Scheduled Tasks subsection via the settings sidebar nav
    const scheduledTasksNav = appPage.locator('.settings-nav-item').filter({ hasText: 'Scheduled Tasks' });
    await scheduledTasksNav.click();
    await appPage.waitForTimeout(500);
  });

  test('show_notifications unchecked prevents notifications in bell icon', async ({ appPage }) => {
    // Step 1: Clear any existing notifications first
    const bellButton = appPage.locator('.notification-bell');
    await bellButton.click();
    await appPage.waitForTimeout(500);

    // Delete all existing notifications if any
    const deleteAllButton = appPage.locator('button[title="Delete all notifications"]');
    if (await deleteAllButton.isVisible()) {
      await deleteAllButton.click();
      await appPage.waitForTimeout(500);
    }

    // Close the notification panel by clicking elsewhere
    await appPage.locator('body').click({ position: { x: 10, y: 10 } });
    await appPage.waitForTimeout(300);

    // Step 2: Find Database Cleanup task card and click its Edit button
    // Each task card has a data-testid attribute like "task-card-{task_id}"
    const taskCard = appPage.locator('[data-testid="task-card-cleanup"]');
    const editButton = taskCard.locator('button:has-text("Edit")');

    await editButton.click();
    await appPage.waitForTimeout(500);

    // Step 3: Find and uncheck "Show notifications in bell icon"
    const showNotificationsLabel = appPage.locator('label').filter({ hasText: 'Show notifications in bell icon' });
    const showNotificationsCheckbox = showNotificationsLabel.locator('input[type="checkbox"]');

    // Make sure the modal is open and checkbox is visible
    await expect(showNotificationsCheckbox).toBeVisible({ timeout: 5000 });

    // Uncheck if currently checked
    const isChecked = await showNotificationsCheckbox.isChecked();
    if (isChecked) {
      await showNotificationsCheckbox.click();
    }

    // Verify it's now unchecked
    await expect(showNotificationsCheckbox).not.toBeChecked();

    // Step 4: Save the settings
    const saveButton = appPage.locator('button').filter({ hasText: 'Save' }).first();
    await saveButton.click();
    await appPage.waitForTimeout(1000);

    // Step 5: Run the Database Cleanup task
    const taskCardForRun = appPage.locator('[data-testid="task-card-cleanup"]');
    const runButton = taskCardForRun.locator('button:has-text("Run Now")');
    await runButton.click();

    // Wait for task to complete (Database Cleanup should be fast)
    await appPage.waitForTimeout(5000);

    // Step 6: Check that NO notification appeared in the bell icon
    await bellButton.click();
    await appPage.waitForTimeout(500);

    // Look for notifications related to Cleanup task
    const cleanupNotification = appPage.locator('.notification-item').filter({ hasText: /cleanup|Cleanup|Database/i });

    // There should be NO cleanup notification
    const notificationCount = await cleanupNotification.count();
    expect(notificationCount).toBe(0);

    // Step 7: Restore the setting (re-enable notifications)
    // Close notification panel first
    await appPage.locator('body').click({ position: { x: 10, y: 10 } });
    await appPage.waitForTimeout(300);

    // Re-open task editor for Database Cleanup
    const taskCardReopen = appPage.locator('[data-testid="task-card-cleanup"]');
    await taskCardReopen.locator('button:has-text("Edit")').click();
    await appPage.waitForTimeout(500);

    // Re-check the checkbox
    const checkboxAgain = appPage.locator('label').filter({ hasText: 'Show notifications in bell icon' }).locator('input[type="checkbox"]');
    if (!(await checkboxAgain.isChecked())) {
      await checkboxAgain.click();
    }
    await expect(checkboxAgain).toBeChecked();

    // Save
    const saveBtn = appPage.locator('button').filter({ hasText: 'Save' }).first();
    await saveBtn.click();
  });

  test('show_notifications checked allows notifications in bell icon', async ({ appPage }) => {
    // Step 1: Clear any existing notifications first
    const bellButton = appPage.locator('.notification-bell');
    await bellButton.click();
    await appPage.waitForTimeout(500);

    // Delete all existing notifications if any
    const deleteAllButton = appPage.locator('button[title="Delete all notifications"]');
    if (await deleteAllButton.isVisible()) {
      await deleteAllButton.click();
      await appPage.waitForTimeout(500);
    }

    // Close the notification panel
    await appPage.locator('body').click({ position: { x: 10, y: 10 } });
    await appPage.waitForTimeout(300);

    // Step 2: Find Database Cleanup task card and click its Edit button
    const taskCard = appPage.locator('[data-testid="task-card-cleanup"]');
    const editButton = taskCard.locator('button:has-text("Edit")');
    await editButton.click();
    await appPage.waitForTimeout(500);

    // Step 3: Ensure "Show notifications in bell icon" is CHECKED
    const showNotificationsCheckbox = appPage.locator('label').filter({ hasText: 'Show notifications in bell icon' }).locator('input[type="checkbox"]');
    await expect(showNotificationsCheckbox).toBeVisible({ timeout: 5000 });

    const isChecked = await showNotificationsCheckbox.isChecked();
    if (!isChecked) {
      await showNotificationsCheckbox.click();
    }

    await expect(showNotificationsCheckbox).toBeChecked();

    // Step 4: Save the settings
    const saveButton = appPage.locator('button').filter({ hasText: 'Save' }).first();
    await saveButton.click();
    await appPage.waitForTimeout(1000);

    // Step 5: Run the Database Cleanup task
    const taskCardForRun = appPage.locator('[data-testid="task-card-cleanup"]');
    const runButton = taskCardForRun.locator('button:has-text("Run Now")');
    await runButton.click();

    // Wait for task to complete
    await appPage.waitForTimeout(5000);

    // Step 6: Check that a notification DID appear in the bell icon
    await bellButton.click();
    await appPage.waitForTimeout(500);

    // Look for notifications related to Cleanup task
    const cleanupNotification = appPage.locator('.notification-item').filter({ hasText: /cleanup|Cleanup|Database/i });

    // There SHOULD be a cleanup notification
    const notificationCount = await cleanupNotification.count();
    expect(notificationCount).toBeGreaterThan(0);
  });
});
