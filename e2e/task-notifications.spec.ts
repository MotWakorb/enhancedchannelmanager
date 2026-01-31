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
    // Navigate to Scheduled Tasks subsection
    const scheduledTasksNav = appPage.locator('li').filter({ hasText: 'Scheduled Tasks' });
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

    // Step 2: Find Database Cleanup task and click Edit
    // The task name is in a generic element, find the row containing "Database Cleanup"
    const taskRow = appPage.locator('div').filter({ hasText: /^Database Cleanup/ }).first();
    const editButton = appPage.locator('button:has-text("Edit")').nth(3); // Database Cleanup is the 4th task (0-indexed)

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
    // Find Run Now button for Database Cleanup (4th task)
    const runButton = appPage.locator('button:has-text("Run Now")').nth(3); // Database Cleanup is 4th task with Run Now button (0=EPG, 1=M3U, 2=M3U Change Monitor, 3=Database Cleanup)
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

    // Re-open task editor (4th edit button)
    await editButton.click();
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

    // Step 2: Find Database Cleanup task and click Edit (4th edit button)
    const editButton = appPage.locator('button:has-text("Edit")').nth(3);
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

    // Step 5: Run the task (3rd Run Now button = Database Cleanup)
    const runButton = appPage.locator('button:has-text("Run Now")').nth(3); // Database Cleanup
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
